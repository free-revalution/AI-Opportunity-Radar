"""Notification service — Phase 8 / Phase 6.

Builds the daily digest + per-opportunity alerts from the database,
hands the formatted MarkdownV2 text to a `BotProvider` (Phase 6
abstraction over Telegram + Feishu), and records the outcome in the
`notifications` table.

Phase 6 changes:

  * `provider` is now typed `BotProvider` (was `TelegramProvider`).
  * `CHANNEL` is no longer hard-coded — it's read from
    `provider.channel` so the same code path works for any platform.
  * `_dispatch` calls `BotProvider.send(target, BotMessage(...))`
    instead of `TelegramProvider.send_message(...)`.
  * `send_via_fallback(...)` walks a list of channels, building each
    on demand, so a Feishu-down scenario still reaches operators
    via Telegram.

Failure policy mirrors the rest of the codebase: a single failed send
MUST NOT poison the batch. Each call persists its own `Notification`
row so the dashboard can show what was attempted, what was delivered,
and what failed.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.metrics import record_notification
from app.models import Notification, Opportunity, ResearchReport
from app.repositories import OpportunityRepository
from app.services.bots import (
    BotMessage,
    BotProvider,
    BotSendResult,
    build_bot_provider,
)
from app.services.notification.formatting import (
    DigestEntry,
    assert_markdown_v2_safe,
    format_digest,
    format_opportunity_alert,
)
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class NotificationOutcome:
    """Outcome of one `send_*` call."""

    notification_id: int | None
    channel: str
    chat_id: str
    delivered: bool
    text_chars: int
    provider: str
    error: str | None = None
    message_id: str | None = None
    # When sent via `FallbackBotProvider`, the actual provider that
    # delivered (may differ from the configured one). Empty otherwise.
    delivered_by: str = ""


@dataclass(slots=True)
class DigestSendSummary:
    """Outcome of a full `send_digest` sweep."""

    notifications_attempted: int = 0
    notifications_delivered: int = 0
    notifications_failed: int = 0
    chat_id: str | None = None
    text_chars: int = 0
    channel: str = ""  # — Phase 6: which channel was used
    provider: str = ""  # — Phase 6: which provider actually shipped
    errors: list[str] = field(default_factory=list)
    preview: str | None = None

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class NotificationService:
    """Phase 8 orchestrator (channel-agnostic since Phase 6)."""

    # — backwards-compat constant for callers that import it; mirrors
    # `provider.channel` at construction time. Kept as a class-level
    # default so legacy tests that don't inject a provider see "telegram".
    DEFAULT_CHANNEL = "telegram"

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        provider: BotProvider | None = None,
        base_url: str | None = None,
        channel: str | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        # — Phase 6: factory picks the channel-specific BotProvider.
        # When `channel` is supplied, that channel wins regardless of the
        # settings default — used by `send_via_fallback` and by the
        # `/api/internal/notifications/digest/send?channel=...` endpoint.
        self.provider: BotProvider = provider or build_bot_provider(
            self.settings, channel=channel
        )
        # — the active channel — taken from the provider unless explicitly
        # overridden (rare; mostly for testing).
        self.channel: str = (
            channel or self.provider.channel or self.DEFAULT_CHANNEL
        )
        # Default base URL — UI deep-links live here.
        self.base_url = (base_url or self.settings.app_base_url or "").rstrip("/")

    # ------------------------------------------------------------------
    # public API — previews
    # ------------------------------------------------------------------
    async def build_digest_preview(
        self,
        *,
        max_entries: int = 5,
        per_entry_summary_chars: int = 240,
        min_score: float | None = None,
    ) -> dict[str, Any]:
        """Compose the digest text + return it without sending."""
        entries = await self._collect_digest_entries(
            max_entries=max_entries, min_score=min_score
        )
        text = format_digest(
            entries=entries,
            base_url=self.base_url,
            max_entries=max_entries,
            per_entry_summary_chars=per_entry_summary_chars,
        )
        return {
            "entries": [self._entry_to_dict(e) for e in entries],
            "text": text,
            "text_chars": len(text),
            "warnings": assert_markdown_v2_safe(text),
        }

    async def build_opportunity_preview(
        self, opportunity_id: int, *, max_summary_chars: int = 600
    ) -> dict[str, Any]:
        entry = await self._entry_for_opportunity(opportunity_id)
        if entry is None:
            raise LookupError(f"opportunity not found: {opportunity_id}")
        text = format_opportunity_alert(
            entry=entry,
            base_url=self.base_url,
            max_summary_chars=max_summary_chars,
        )
        return {
            "entry": self._entry_to_dict(entry),
            "text": text,
            "text_chars": len(text),
            "warnings": assert_markdown_v2_safe(text),
        }

    # ------------------------------------------------------------------
    # public API — sends
    # ------------------------------------------------------------------
    async def send_digest(
        self,
        *,
        chat_id: str | None = None,
        dry_run: bool = False,
        max_entries: int = 5,
        per_entry_summary_chars: int = 240,
        min_score: float | None = None,
    ) -> DigestSendSummary:
        """Build + send the daily digest."""
        target_chat = (chat_id or self.settings.telegram_chat_id or "").strip()
        summary = DigestSendSummary(chat_id=target_chat or None)

        if not target_chat:
            summary.errors.append("no chat_id provided and settings.telegram_chat_id empty")
            logger.warning("notification_digest_no_chat")
            return summary

        entries = await self._collect_digest_entries(
            max_entries=max_entries, min_score=min_score
        )
        text = format_digest(
            entries=entries,
            base_url=self.base_url,
            max_entries=max_entries,
            per_entry_summary_chars=per_entry_summary_chars,
        )
        summary.text_chars = len(text)
        summary.preview = text  # always available for callers

        if dry_run:
            return summary

        outcome = await self._dispatch(
            chat_id=target_chat,
            text=text,
            payload={
                "kind": "digest",
                "entry_count": len(entries),
                "entry_ids": [e.opportunity_id for e in entries],
                "min_score": min_score,
                "max_entries": max_entries,
            },
        )
        summary.notifications_attempted = 1
        if outcome.delivered:
            summary.notifications_delivered = 1
        else:
            summary.notifications_failed = 1
            if outcome.error:
                summary.errors.append(outcome.error)
        return summary

    async def send_opportunity_alert(
        self,
        opportunity_id: int,
        *,
        chat_id: str | None = None,
        dry_run: bool = False,
        extra_note: str | None = None,
        max_summary_chars: int = 600,
    ) -> NotificationOutcome:
        """Send a single-opportunity alert to `chat_id`."""
        target_chat = (chat_id or self.settings.telegram_chat_id or "").strip()
        if not target_chat:
            logger.warning(
                "notification_opportunity_no_chat", opportunity_id=opportunity_id
            )
            return NotificationOutcome(
                notification_id=None,
                channel=self.channel,
                chat_id="",
                delivered=False,
                text_chars=0,
                provider=self.provider.name,
                error="no chat_id",
            )

        entry = await self._entry_for_opportunity(opportunity_id)
        if entry is None:
            return NotificationOutcome(
                notification_id=None,
                channel=self.channel,
                chat_id=target_chat,
                delivered=False,
                text_chars=0,
                provider=self.provider.name,
                error=f"opportunity not found: {opportunity_id}",
            )

        text = format_opportunity_alert(
            entry=entry,
            base_url=self.base_url,
            extra_note=extra_note,
            max_summary_chars=max_summary_chars,
        )

        if dry_run:
            return NotificationOutcome(
                notification_id=None,
                channel=self.channel,
                chat_id=target_chat,
                delivered=False,
                text_chars=len(text),
                provider=self.provider.name,
            )

        return await self._dispatch(
            chat_id=target_chat,
            text=text,
            payload={
                "kind": "opportunity_alert",
                "opportunity_id": opportunity_id,
                "slug": entry.slug,
                "score": entry.total_score,
                "recommendation": entry.recommendation,
            },
        )

    async def list_history(
        self, *, limit: int = 50, channel: str | None = None
    ) -> list[Notification]:
        result = await self.session.execute(
            select(Notification)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        if channel:
            rows = [r for r in rows if r.channel == channel]
        return rows

    # ------------------------------------------------------------------
    # internals — DB access
    # ------------------------------------------------------------------
    async def _collect_digest_entries(
        self,
        *,
        max_entries: int,
        min_score: float | None,
    ) -> list[DigestEntry]:
        repo = OpportunityRepository(self.session)
        rows, _total = await repo.list_paginated(
            limit=max(1, max_entries * 4),  # over-fetch + filter
            offset=0,
            status="research_complete",
            min_total_score=min_score,
        )
        # If there aren't any research_complete opportunities, fall back to
        # the top-scoring ones — the digest is more useful empty than missing.
        if not rows:
            rows, _ = await repo.list_paginated(
                limit=max(1, max_entries * 4),
                offset=0,
                min_total_score=min_score,
            )

        # Hydrate with report-presence flags.
        entry_ids = [r.id for r in rows[: max(1, max_entries * 2)]]
        has_report = await self._opportunity_ids_with_reports(entry_ids)

        entries: list[DigestEntry] = []
        for r in rows:
            entries.append(
                DigestEntry(
                    opportunity_id=r.id,
                    title=r.title or "(untitled)",
                    slug=r.slug,
                    total_score=float(r.total_score or 0.0),
                    recommendation=_recommendation_for(r),
                    summary=r.summary or "",
                    category=r.category,
                    target_user=r.target_user,
                    trend_score=float(r.trend_score or 0.0),
                    demand_score=float(r.demand_score or 0.0),
                    monetization_score=float(r.monetization_score or 0.0),
                    competition_gap_score=float(r.competition_gap_score or 0.0),
                    china_gap_score=float(r.china_gap_score or 0.0),
                    execution_score=float(r.execution_score or 0.0),
                    source_count=int(r.source_count or 0),
                    has_report=r.id in has_report,
                )
            )
            if len(entries) >= max_entries:
                break
        return entries

    async def _entry_for_opportunity(
        self, opportunity_id: int
    ) -> DigestEntry | None:
        repo = OpportunityRepository(self.session)
        r = await repo.get_by_id(opportunity_id)
        if r is None:
            return None
        has_report = await self._opportunity_ids_with_reports([r.id])
        return DigestEntry(
            opportunity_id=r.id,
            title=r.title or "(untitled)",
            slug=r.slug,
            total_score=float(r.total_score or 0.0),
            recommendation=_recommendation_for(r),
            summary=r.summary or "",
            category=r.category,
            target_user=r.target_user,
            trend_score=float(r.trend_score or 0.0),
            demand_score=float(r.demand_score or 0.0),
            monetization_score=float(r.monetization_score or 0.0),
            competition_gap_score=float(r.competition_gap_score or 0.0),
            china_gap_score=float(r.china_gap_score or 0.0),
            execution_score=float(r.execution_score or 0.0),
            source_count=int(r.source_count or 0),
            has_report=r.id in has_report,
        )

    async def _opportunity_ids_with_reports(
        self, opportunity_ids: Iterable[int]
    ) -> set[int]:
        ids = [i for i in opportunity_ids if i is not None]
        if not ids:
            return set()
        result = await self.session.execute(
            select(ResearchReport.opportunity_id).where(
                ResearchReport.opportunity_id.in_(ids)
            )
        )
        return {row[0] for row in result.all()}

    # ------------------------------------------------------------------
    # internals — dispatch + persistence
    # ------------------------------------------------------------------
    async def _dispatch(
        self,
        *,
        chat_id: str,
        text: str,
        payload: dict[str, Any],
    ) -> NotificationOutcome:
        result: BotSendResult = await self.provider.send(
            target=chat_id,
            message=BotMessage(text=text, parse_mode="MarkdownV2"),
        )

        notification = Notification(
            channel=self.channel,
            payload={
                **payload,
                "chat_id": chat_id,
                "text_chars": len(text or ""),
                "provider": result.provider,
                "message_id": result.message_id,
                "delivered_by": result.delivered_by or "",
            },
            delivered_at=datetime.now(UTC) if result.ok else None,
            error=None if result.ok else (result.error or "unknown error")[:2000],
        )
        self.session.add(notification)
        await self.session.flush()
        await self.session.commit()

        logger.info(
            "notification_sent" if result.ok else "notification_failed",
            chat_id=chat_id,
            ok=result.ok,
            provider=result.provider,
            delivered_by=result.delivered_by or "",
            channel=self.channel,
            text_chars=len(text or ""),
            notification_id=notification.id,
            kind=payload.get("kind"),
        )
        record_notification(
            kind=str(payload.get("kind") or "unknown"),
            provider=result.provider,
            outcome="success" if result.ok else "error",
        )

        return NotificationOutcome(
            notification_id=notification.id,
            channel=self.channel,
            chat_id=chat_id,
            delivered=result.ok,
            text_chars=len(text or ""),
            provider=result.provider,
            message_id=result.message_id,
            error=result.error,
            delivered_by=result.delivered_by or "",
        )

    # ------------------------------------------------------------------
    # public API — channel-aware send (Phase 6)
    # ------------------------------------------------------------------
    async def send_digest_to_channel(
        self,
        channel: str,
        *,
        chat_id: str | None = None,
        max_entries: int = 5,
        per_entry_summary_chars: int = 240,
        min_score: float | None = None,
    ) -> DigestSendSummary:
        """Send the digest via a specific channel (telegram / feishu).

        Builds a fresh `NotificationService` bound to that channel so the
        `_dispatch` row records `channel=<channel>`. Returns the same
        `DigestSendSummary` shape as `send_digest`.
        """
        target_service = NotificationService(
            self.session,
            settings=self.settings,
            channel=channel,
            base_url=self.base_url,
        )
        summary = await target_service.send_digest(
            chat_id=chat_id,
            max_entries=max_entries,
            per_entry_summary_chars=per_entry_summary_chars,
            min_score=min_score,
        )
        # — annotate the summary with the channel we used so the API can
        # echo it back to the caller without inspecting Notification rows.
        summary.channel = target_service.channel
        if summary.errors and not summary.provider:
            # — single-channel failure path: surface the provider name
            # from the most recent error context if available
            pass
        return summary

    async def send_via_fallback(
        self,
        channels: list[str],
        *,
        chat_id: str | None = None,
        max_entries: int = 5,
        per_entry_summary_chars: int = 240,
        min_score: float | None = None,
    ) -> DigestSendSummary:
        """Try each channel in order; stop at the first successful send.

        Each attempt persists its own `Notification` row (with the
        attempted channel) so the audit trail shows the failure chain.
        Returns a `DigestSendSummary` aggregating the outcomes.
        """
        all_errors: list[str] = []
        for idx, channel in enumerate(channels):
            target_service = NotificationService(
                self.session,
                settings=self.settings,
                channel=channel,
                base_url=self.base_url,
            )
            try:
                summary = await target_service.send_digest(
                    chat_id=chat_id,
                    max_entries=max_entries,
                    per_entry_summary_chars=per_entry_summary_chars,
                    min_score=min_score,
                )
            except Exception as exc:  # noqa: BLE001 — translate to summary error
                all_errors.append(f"{channel}: unexpected {type(exc).__name__}: {exc}")
                continue
            if summary.notifications_delivered > 0:
                # — success: augment summary with the channel that worked
                summary.channel = channel
                summary.errors = all_errors + list(summary.errors)
                return summary
            # — this channel delivered zero notifications; try the next
            all_errors.append(
                f"{channel}: no deliveries"
                + (f" ({'; '.join(summary.errors)})" if summary.errors else "")
            )
        # — all channels failed
        summary = DigestSendSummary(
            notifications_attempted=len(channels),
            notifications_delivered=0,
            notifications_failed=len(channels),
            chat_id=chat_id,
            errors=all_errors,
        )
        return summary

    # ------------------------------------------------------------------
    # internals — helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _entry_to_dict(entry: DigestEntry) -> dict[str, Any]:
        return {
            "opportunity_id": entry.opportunity_id,
            "title": entry.title,
            "slug": entry.slug,
            "total_score": entry.total_score,
            "recommendation": entry.recommendation,
            "category": entry.category,
            "target_user": entry.target_user,
            "sub_scores": {
                "trend": entry.trend_score,
                "demand": entry.demand_score,
                "monetization": entry.monetization_score,
                "competition_gap": entry.competition_gap_score,
                "china_gap": entry.china_gap_score,
                "execution": entry.execution_score,
            },
            "source_count": entry.source_count,
            "has_report": entry.has_report,
        }


# ---------------------------------------------------------------------------
# Pure helper — recommendation label for a persisted Opportunity.
# ---------------------------------------------------------------------------
def _recommendation_for(opp: Opportunity) -> str:
    """Best-effort label without importing the full scoring module.

    Mirrors `recommendation_for()` from `app.services.scoring` for the
    canonical score bands, but never raises — falls back to
    'insufficient_data' for malformed rows.
    """
    score = float(opp.total_score or 0.0)
    if score >= 85:
        return "strongly_recommend"
    if score >= 70:
        return "recommend"
    if score >= 55:
        return "watch"
    if score > 0:
        return "not_recommended"
    return "insufficient_data"


__all__ = [
    "DigestSendSummary",
    "NotificationOutcome",
    "NotificationService",
]
