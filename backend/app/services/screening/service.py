"""Screening service — Phase 5.

Pipeline:

  1. SELECT opportunities WHERE status='detected'
  2. For each opportunity:
       a. load its linked RawItems (in relevance order)
       b. build the screening prompt from title + summary + snippets
       c. call the LLM provider (cheap model, JSON-mode)
       d. parse the response into a `ScreeningResult`
       e. UPDATE opportunity: sub-scores, category, summary, status,
          total_score (via scoring.calculate_total_score)
       f. INSERT one Signal per linked RawItem (signal_type='screening')
  3. Return a structured `ScreeningReport`.

Failure policy: a single LLM error MUST NOT block the other
opportunities — we record it in `errors` and continue. Status moves to
`screen_failed` for the broken one.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Opportunity, RawItem
from app.repositories import (
    OpportunityRepository,
    OpportunitySourceRepository,
    SignalRepository,
)
from app.services.llm import LLMProvider, build_llm_provider
from app.services.screening.parsers import ScreeningResult, parse_screening_response
from app.services.screening.prompts import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.services.scoring import ScoreInput, calculate_total_score
from app.utils import ValidationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ScreeningReport:
    opportunities_attempted: int = 0
    opportunities_screened: int = 0
    opportunities_skipped: int = 0
    opportunities_failed: int = 0
    signals_created: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


class ScreeningService:
    """Phase 5 orchestrator."""

    SCREEN_PASSED_STATUS = "screened"
    SCREEN_FAILED_STATUS = "screen_failed"

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        limit: int = 50,
        max_snippets: int = 6,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.provider = provider or build_llm_provider(self.settings)
        self.limit = limit
        self.max_snippets = max_snippets

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def run_once(self) -> ScreeningReport:
        """Screen every Opportunity in `detected` state."""
        report = ScreeningReport()
        opp_repo = OpportunityRepository(self.session)
        pending = await opp_repo.list_pending_screening(limit=self.limit)
        report.opportunities_attempted = len(pending)
        if not pending:
            logger.info("screening_nothing_pending")
            return report

        for opp in pending:
            try:
                outcome = await self._screen_one(opp)
            except ValidationError as exc:
                report.opportunities_failed += 1
                report.errors.append(f"opp {opp.id}: parse error: {exc}")
                logger.warning("screening_parse_failed", opportunity_id=opp.id, error=str(exc))
                await self._mark_failed(opp)
                continue
            except Exception as exc:  # noqa: BLE001
                report.opportunities_failed += 1
                report.errors.append(f"opp {opp.id}: {exc}")
                logger.exception("screening_failed", opportunity_id=opp.id)
                await self._mark_failed(opp)
                continue

            if outcome == "skipped":
                report.opportunities_skipped += 1
                continue
            report.opportunities_screened += 1
            report.signals_created += int(outcome) if isinstance(outcome, int) else 0

        logger.info("screening_run_complete", **report.as_dict())
        return report

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _screen_one(self, opp: Opportunity) -> str | int:
        """Return 'skipped', or the number of signals created on success."""
        link_repo = OpportunitySourceRepository(self.session)
        raw_items = await link_repo.list_raw_items_for_opportunity(opp.id)
        if not raw_items:
            logger.warning("screening_skip_no_sources", opportunity_id=opp.id)
            return "skipped"

        user_prompt = build_user_prompt(
            title=opp.title,
            summary=opp.summary or "",
            source_snippets=self._build_snippets(raw_items),
        )
        payload = await self.provider.complete_json(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            response_schema=RESPONSE_SCHEMA,
            model=self.settings.MiniMax_model_cheap,
        )
        result = parse_screening_response(payload)
        await self._apply(opp, result, raw_items)
        await self.session.commit()
        return len(raw_items)

    def _build_snippets(self, items: Iterable[RawItem]) -> list[str]:
        out: list[str] = []
        for item in items:
            parts = [(item.title or "").strip()]
            if item.content:
                parts.append(item.content.strip()[:280])
            text = " — ".join(p for p in parts if p)
            if text:
                out.append(text[:300])
            if len(out) >= self.max_snippets:
                break
        return out

    async def _apply(
        self,
        opp: Opportunity,
        result: ScreeningResult,
        raw_items: list[RawItem],
    ) -> None:
        opp.category = result.category or opp.category
        if result.problem and result.potential_business:
            combined = (
                f"{result.problem.strip()}\n\n"
                f"Potential business: {result.potential_business.strip()}"
            )
            # Append to existing summary if present, capped.
            existing = opp.summary or ""
            merged = f"{existing}\n\n{combined}".strip() if existing else combined
            opp.summary = merged[:2000]
        elif result.problem:
            opp.summary = result.problem[:2000]

        opp.trend_score = float(result.trend_strength)
        opp.demand_score = float(result.demand_strength)
        opp.monetization_score = float(result.monetization_potential)
        opp.competition_gap_score = float(result.competition_gap)
        opp.china_gap_score = float(result.china_gap)
        opp.execution_score = float(result.execution_feasibility)
        opp.total_score = calculate_total_score(
            ScoreInput(
                trend=result.trend_strength,
                demand=result.demand_strength,
                monetization=result.monetization_potential,
                competition_gap=result.competition_gap,
                china_gap=result.china_gap,
                execution=result.execution_feasibility,
            )
        )
        opp.status = self.SCREEN_PASSED_STATUS

        await self.session.flush()

        signal_repo = SignalRepository(self.session)
        for item in raw_items:
            await signal_repo.create(
                raw_item_id=item.id,
                signal_type="screening",
                keyword=(result.keywords[0] if result.keywords else None),
                category=result.category or None,
                velocity_score=float(result.trend_strength),
                engagement_score=self._engagement_for(item),
                relevance_score=1.0 if result.is_business_relevant else 0.0,
            )
        await self.session.flush()

    @staticmethod
    def _engagement_for(item: RawItem) -> float:
        md = item.metadata_json or {}
        score = 0.0
        for key in ("stars", "points", "score", "upvotes", "votes", "comments", "forks"):
            value = md.get(key)
            if isinstance(value, (int, float)):
                score += float(value)
        return score

    async def _mark_failed(self, opp: Opportunity) -> None:
        opp.status = self.SCREEN_FAILED_STATUS
        await self.session.commit()


__all__ = ["ScreeningReport", "ScreeningService"]
