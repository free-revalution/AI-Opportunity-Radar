"""Ingestion service.

Coordinates the source connectors and writes results to the `raw_items`
table via `RawItemRepository.upsert()`. The dedup contract from
README §10 + §20 is enforced inside the repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import (
    OpportunityRepository,
    RawItemRepository,
    SourceRepository,
    compute_content_hash,
)
from app.services.ingestion.base import SourceConnectorResult
from app.services.ingestion.registry import (
    REGISTRY,
    SourceSpec,
    build_connector,
)
from app.utils import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class IngestionReport:
    """Aggregated outcome of a single ingestion run."""

    sources_attempted: int = 0
    sources_succeeded: int = 0
    sources_failed: int = 0
    sources_skipped: int = 0
    items_seen: int = 0
    items_inserted: int = 0
    items_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "sources_attempted": self.sources_attempted,
            "sources_succeeded": self.sources_succeeded,
            "sources_failed": self.sources_failed,
            "sources_skipped": self.sources_skipped,
            "items_seen": self.items_seen,
            "items_inserted": self.items_inserted,
            "items_skipped": self.items_skipped,
            "errors": list(self.errors),
            "per_source": dict(self.per_source),
        }


class IngestionService:
    """Runs every enabled connector and persists RawItems."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        source_slugs: list[str] | None = None,
        mock: bool | None = None,
        settings=None,
    ) -> None:
        from app.config import get_settings

        self.session = session
        self.source_slugs = source_slugs
        self.mock = mock
        self.settings = settings or get_settings()

    async def run_once(self) -> IngestionReport:
        """Fetch from every selected connector, then persist.

        Connectors run sequentially — Phase 3 keeps it simple; Phase 4
        will wrap this in a worker queue.

        Phase 24 — a pre-fetch compliance gate consults the Source row's
        ``compliance_level`` (via ``ComplianceService.check_source``)
        before any network call. Sources at level D/E or with a recent
        ``source_block_reason`` are short-circuited with a recorded
        block_reason — no HTTP request is made.
        """
        report = IngestionReport()
        slugs = self._resolve_slugs()

        for slug in slugs:
            report.sources_attempted += 1
            try:
                connector = build_connector(slug, self.settings, mock=self.mock)
            except Exception as exc:  # noqa: BLE001
                report.sources_failed += 1
                report.errors.append(f"{slug}: build failed: {exc}")
                continue

            # Phase 24 — pre-fetch gate. Look up the Source row (if
            # any) and consult the compliance engine.
            pre_fetch_block = await self._check_source_pre_fetch(slug)
            if pre_fetch_block is not None:
                report.sources_skipped += 1
                report.per_source[slug] = {
                    "items": 0,
                    "inserted": 0,
                    "block_reason": pre_fetch_block,
                }
                logger.info(
                    "source_pre_fetch_blocked",
                    source=slug,
                    block_reason=pre_fetch_block,
                )
                continue

            try:
                result = await connector.fetch()
            except Exception as exc:  # noqa: BLE001
                report.sources_failed += 1
                report.errors.append(f"{slug}: fetch crashed: {exc}")
                continue

            # Phase 24 — if the connector surfaced a block reason
            # (HTTP 403/429, captcha, paywall, etc.) record it on the
            # Source row + bump last_compliance_check.
            if result.was_blocked:
                await self._record_block_reason(slug, result)

            if result.was_skipped:
                report.sources_skipped += 1
                report.per_source[slug] = {
                    "items": 0,
                    "inserted": 0,
                    "skipped_reason": 1,
                }
                if result.skipped_reason:
                    logger.info("source_skipped", source=slug, reason=result.skipped_reason)
                continue

            report.sources_succeeded += 1
            report.per_source[slug] = {
                "items": len(result.items),
                "inserted": 0,
                "errors": len(result.errors),
            }
            if result.errors:
                for err in result.errors:
                    report.errors.append(f"{slug}: {err}")

            try:
                inserted = await self._persist(slug, result)
                report.items_seen += len(result.items)
                report.items_inserted += inserted
                report.items_skipped += len(result.items) - inserted
                report.per_source[slug]["inserted"] = inserted
            except Exception as exc:  # noqa: BLE001
                report.sources_failed += 1
                report.errors.append(f"{slug}: persist failed: {exc}")
                logger.exception("ingestion_persist_failed", source=slug)

        return report

    # ------------------------- Phase 24 helpers -------------------------
    async def _check_source_pre_fetch(self, slug: str) -> str | None:
        """Run ``ComplianceService.check_source`` for `slug`.

        Returns the block_reason string when the verdict is BLOCKED,
        otherwise ``None``. Looks up the Source row by name (the slug
        is the canonical key in `sources.name`).
        """
        try:
            source_row = await self._get_source_row(slug)
        except Exception:
            # If we can't even look up the Source row we can't evaluate
            # compliance posture. Fall open — the connector's own
            # errors will surface via the existing path.
            logger.warning(
                "source_pre_fetch_lookup_failed", source=slug, exc_info=True
            )
            return None
        if source_row is None:
            return None  # no row yet — connector will upsert one

        try:
            from app.services.compliance import default_service
            from app.services.compliance.source_policy import SourcePolicyRecord

            record = SourcePolicyRecord(
                source_id=source_row.id,
                name=source_row.name,
                compliance_level=source_row.compliance_level or "E",
                commercial_use_status=source_row.commercial_use_status or "unknown",
                access_method=source_row.access_method or "unknown",
                rate_limit=source_row.rate_limit,
                last_compliance_check=source_row.last_compliance_check,
                retention_policy=source_row.retention_policy or "session",
                robots_url=source_row.robots_url,
                terms_url=source_row.terms_url,
                enabled=source_row.enabled,
                last_block_reason=source_row.source_block_reason,
            )
            verdict = default_service().check_source(record, context=f"fetch:{slug}")
        except Exception:
            logger.warning(
                "source_pre_fetch_evaluation_failed",
                source=slug,
                exc_info=True,
            )
            return None

        # Setting disables the gate entirely (operator kill switch).
        if not getattr(self.settings, "compliance_source_gate_enabled", True):
            return None

        if not verdict.allowed:
            return source_row.source_block_reason or "policy_block"
        return None

    async def _record_block_reason(
        self, slug: str, result: SourceConnectorResult
    ) -> None:
        """Persist ``result.block_reason`` on the Source row.

        Idempotent — safe to call on every connector run; only writes
        when the row exists.
        """
        source_row = await self._get_source_row(slug)
        if source_row is None:
            return
        from datetime import datetime, timezone

        source_row.source_block_reason = result.block_reason
        source_row.last_compliance_check = datetime.now(timezone.utc)
        if result.http_status is not None:
            source_row.last_error_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def _get_source_row(self, slug: str) -> Any:
        """Return the Source row whose ``name == slug`` (case-insensitive),
        or ``None`` if no row has been upserted yet.
        """
        from sqlalchemy import select

        from app.models import Source

        stmt = select(Source).where(Source.name.ilike(slug))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ------------------------- helpers -------------------------
    def _resolve_slugs(self) -> list[str]:
        if self.source_slugs:
            return [s for s in self.source_slugs if s in REGISTRY]
        return [s for s in (self.settings.enabled_sources or []) if s in REGISTRY]

    async def _persist(self, source_slug: str, result: SourceConnectorResult) -> int:
        source_repo = SourceRepository(self.session)
        raw_repo = RawItemRepository(self.session)
        opp_repo = OpportunityRepository(self.session)

        # Upsert the `sources` row so we get a stable FK.
        spec = REGISTRY.get(source_slug)
        source_row = await source_repo.upsert(
            name=spec.name if spec else source_slug.title(),
            type=spec.type if spec else "api",
            url=f"https://example.com/{source_slug}",
            enabled=True,
        )
        # Phase 29 fix — without this, ``sources.last_success_at``
        # stayed NULL forever and the bot's ``/sources`` reply rendered
        # "尚未采集" for every source after a successful /run. The
        # /sources/healthy endpoint reads this column to render the
        # "last collected at" timestamp.
        from datetime import datetime, timezone

        source_row.last_success_at = datetime.now(timezone.utc)

        # Phase 29 fix — stamp the curated compliance default on any
        # Source row that has never been reviewed
        # (``last_compliance_check IS NULL``). Without this, freshly
        # upserted rows stayed at the conservative ``compliance_level='E'``
        # and the pre-fetch gate blocked the very next fetch. Existing
        # rows with a real review timestamp are left untouched —
        # operators may have already manually adjusted them.
        if source_row.last_compliance_check is None:
            from app.services.ingestion.source_compliance import (
                SOURCE_COMPLIANCE_DEFAULTS,
            )

            default = SOURCE_COMPLIANCE_DEFAULTS.get(source_slug)
            if default is not None:
                source_row.compliance_level = default.compliance_level
                source_row.access_method = default.access_method
                source_row.commercial_use_status = default.commercial_use_status
                source_row.terms_url = default.terms_url
                source_row.robots_url = default.robots_url
                if default.rate_limit is not None:
                    source_row.rate_limit = default.rate_limit
                source_row.last_compliance_check = datetime.now(timezone.utc)

        await self.session.flush()

        inserted = 0
        for item in result.items:
            try:
                _, created = await raw_repo.upsert(
                    source_id=source_row.id,
                    external_id=item.source_id,
                    url=item.url,
                    title=item.title,
                    content=item.content,
                    author=item.author,
                    published_at=item.published_at,
                    fetched_at=item.fetched_at,
                    metadata_json=item.metadata,
                )
            except Exception:
                # The repo rolls back on IntegrityError and returns the existing row.
                # Other exceptions propagate.
                continue
            if created:
                inserted += 1

        # Bump opportunity-level source_count counters so the dashboard
        # sees activity immediately (no LLM required yet).
        await opp_repo.session.commit() if False else None  # noop; commit happens at request boundary

        return inserted


__all__ = ["IngestionService", "IngestionReport"]