"""Feishu bot orchestrator — Phase 2 v2.0.

`FeishuBot` ties the three moving parts together:

  1. `Opportunity` rows (top-N from the DB, ordered by score)
  2. `format_daily_digest(...)` (turns them into a Feishu card)
  3. `FeishuProvider.send_card(...)` (delivers to the webhook)

Optional behaviour:
  * `auto_generate_content=True` — runs the v2.0 `content_generator`
    orchestrator on each opportunity BEFORE formatting, so the Feishu
    card surfaces content that exists (a `daily_report` was already
    published). This keeps Feishu in sync with the content pipeline.

Callers:
  * `POST /api/internal/feishu/digest/send` (ad-hoc operator run)
  * n8n cron `feishu-daily-digest` at 02:30 UTC (after discovery at 02:00)

The bot NEVER raises from `send_card` — failures surface in the result
summary so the caller can persist them in the `notifications` table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.feishu.base import (
    FeishuCard,
    FeishuProvider,
    FeishuSendResult,
)
from app.services.feishu.formatter import format_daily_digest
from app.utils import ExternalServiceError, get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result DTO
# ---------------------------------------------------------------------------
@dataclass
class FeishuDigestSummary:
    """Outcome of a `send_digest(...)` call."""

    sent: bool = False
    opportunity_count: int = 0
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    error: Optional[str] = None
    provider: str = ""
    body_chars: int = 0
    content_generated: int = 0  # how many content_generator runs ran

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "opportunity_count": self.opportunity_count,
            "window_start": (
                self.window_start.isoformat() if self.window_start else None
            ),
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "error": self.error,
            "provider": self.provider,
            "body_chars": self.body_chars,
            "content_generated": self.content_generated,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class FeishuBot:
    """Pull opportunities → format card → send to Feishu."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        provider: FeishuProvider,
        cta_base_url: str = "http://localhost:3000/opportunities",
    ) -> None:
        self.session = session
        self.provider = provider
        self.cta_base_url = cta_base_url

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    async def _select_top_opportunities(
        self,
        *,
        limit: int,
        only_qualified: bool,
        window_hours: int | None,
    ) -> list[Any]:
        """Pull the top-N opportunities by score."""
        from app.models import Opportunity  # local import

        stmt = select(Opportunity).order_by(Opportunity.total_score.desc())
        if only_qualified:
            stmt = stmt.where(
                Opportunity.commercial_status.in_(["qualified", "promising"])
            )
        if window_hours is not None and window_hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            stmt = stmt.where(Opportunity.created_at >= cutoff)
        stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Optional content generation
    # ------------------------------------------------------------------
    async def _maybe_generate_content(
        self,
        opportunities: list[Any],
        *,
        enabled: bool,
    ) -> int:
        """Run the v2.0 content generator on each opportunity.

        Returns the number of GeneratedContent records produced (0 if
        disabled or the orchestrator raises).
        """
        if not enabled or not opportunities:
            return 0
        try:
            from app.services.content_generator import ContentGeneratorService
            from app.services.llm import build_llm_provider
            from app.config import get_settings
        except ImportError as exc:  # pragma: no cover - import sanity
            logger.warning("feishu_content_import_failed", error=str(exc))
            return 0

        try:
            llm = build_llm_provider(settings=get_settings())
            service = ContentGeneratorService(session=self.session, llm=llm)
        except Exception as exc:  # noqa: BLE001
            logger.warning("feishu_content_init_failed", error=str(exc))
            return 0

        total = 0
        for opp in opportunities:
            try:
                produced = await service.run_for_opportunity(opp, enrich=False)
                total += len(produced)
            except Exception as exc:  # noqa: BLE001 — one opp must not kill the rest
                logger.warning(
                    "feishu_content_opp_failed",
                    opportunity_id=getattr(opp, "id", None),
                    error=str(exc),
                )
                continue
        return total

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------
    async def send_digest(
        self,
        *,
        limit: int = 5,
        only_qualified: bool = True,
        window_hours: int | None = 24,
        auto_generate_content: bool = False,
        title_prefix: str | None = None,
    ) -> FeishuDigestSummary:
        """Pull the top opportunities and ship them to Feishu.

        `window_hours=None` means "all-time top N" (used by ad-hoc
        operator runs); the n8n daily cron passes a finite window so
        the digest is bounded to "yesterday's" opportunities.
        """
        summary = FeishuDigestSummary(
            window_end=datetime.now(timezone.utc),
        )
        summary.provider = self.provider.name

        try:
            opps = await self._select_top_opportunities(
                limit=limit,
                only_qualified=only_qualified,
                window_hours=window_hours,
            )
        except Exception as exc:  # noqa: BLE001
            summary.error = f"selection_failed: {exc}"
            logger.error("feishu_selection_failed", error=str(exc))
            return summary

        summary.opportunity_count = len(opps)
        summary.content_generated = await self._maybe_generate_content(
            opps, enabled=auto_generate_content
        )

        if window_hours:
            summary.window_start = (
                summary.window_end - timedelta(hours=window_hours)
                if summary.window_end
                else None
            )

        card: FeishuCard = format_daily_digest(
            opps,
            cta_base_url=self.cta_base_url,
            **( {"title_prefix": title_prefix} if title_prefix else {} ),
        )

        try:
            result = await self.provider.send_card(card)
        except ExternalServiceError as exc:
            summary.error = str(exc)
            logger.error("feishu_send_failed", error=str(exc))
            return summary
        except Exception as exc:  # noqa: BLE001 — translate anything else
            summary.error = f"send_failed: {exc}"
            logger.error("feishu_send_unexpected", error=str(exc))
            return summary

        summary.sent = True
        summary.body_chars = result.body_chars
        logger.info(
            "feishu_digest_sent",
            **summary.as_dict(),
        )
        return summary


__all__ = ["FeishuBot", "FeishuDigestSummary"]