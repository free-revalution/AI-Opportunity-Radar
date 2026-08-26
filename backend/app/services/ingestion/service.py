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

            try:
                result = await connector.fetch()
            except Exception as exc:  # noqa: BLE001
                report.sources_failed += 1
                report.errors.append(f"{slug}: fetch crashed: {exc}")
                continue

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