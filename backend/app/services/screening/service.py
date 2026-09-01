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
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Opportunity, RawItem, Signal
from app.repositories import (
    OpportunityRepository,
    OpportunitySourceRepository,
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
        emit_screening_callback: Optional[
            Callable[[Any, dict[str, Any]], Awaitable[None]]
        ] = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.provider = provider or build_llm_provider(self.settings)
        self.limit = limit
        self.max_snippets = max_snippets
        # Phase 33 PR-33-F: pipeline 传一个 async callback,
        # _apply 在写入 sub-scores 后 emit (raw_item, payload) 给 caller。
        # 取代 _backfill_data_table_screening 的二次 SELECT Signal JOIN JOIN。
        # 传 raw_item 而非主键 — caller 自己解析 source_slug
        # (ORM RawItem 没 .source 属性,要 JOIN Source.name)。
        self._emit_screening_callback = emit_screening_callback

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

        # Phase 33 PR-33-A — 单次 commit 取代 per-opp commit。
        # 50 opp × ~50-150ms/commit ≈ 2.5-7.5s WAL flush 开销降到 ~50-150ms。
        # _screen_one / _mark_failed 内部只 flush(让行级错误尽早 surface)。
        await self.session.commit()

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

        # Phase 30 — store-first + chat-pick: persist screening 原文 so the
        # detail docx (built on button click) can show problem / business /
        # keywords without re-running the LLM. Migration a30b1c2d3e4f.
        opp.problem = (result.problem or "")[:2000] or None
        opp.potential_business = (result.potential_business or "")[:2000] or None
        opp.keywords_json = list(result.keywords or []) if result.keywords else None

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

        # Phase 34 PR-34-E: batched insert — 一次 add_all + 一次 flush,
        # 取代 per-signal create+flush (50 opps × ~5 signals = 250 flushes)。
        # row-level error 仍由 run_once 的 except Exception 接住,不会
        # 影响其它 opp。
        keyword = (result.keywords[0] if result.keywords else None)
        category = result.category or None
        velocity = float(result.trend_strength)
        relevance = 1.0 if result.is_business_relevant else 0.0
        signals_to_add = [
            Signal(
                raw_item_id=item.id,
                signal_type="screening",
                keyword=keyword,
                category=category,
                velocity_score=velocity,
                engagement_score=self._engagement_for(item),
                relevance_score=relevance,
            )
            for item in raw_items
        ]
        if signals_to_add:
            self.session.add_all(signals_to_add)
        await self.session.flush()

        # Phase 33 PR-33-F: emit (raw_item, payload) 给 callback。
        # caller (run_pipeline) 自己解析 source_slug — ORM RawItem 没
        # .source 属性,这一层不查 DB。
        if self._emit_screening_callback is not None:
            payload = {
                "Category": result.category or opp.category or "",
                "Score": int(round(float(opp.total_score or 0))),
                "Opportunity ID": int(opp.id),
            }
            for item in raw_items:
                try:
                    await self._emit_screening_callback(item, payload)
                except Exception as exc:  # noqa: BLE001 — emit 失败不阻塞 screening
                    logger.warning(
                        "screening_emit_callback_failed",
                        opportunity_id=opp.id,
                        error=str(exc)[:200],
                    )

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
        # Phase 33 PR-33-A — commit 移到 run_once 末尾,这里只 flush 让 row-level
        # 错误尽早 surface。50 opp × commit ≈ 2.5-7.5s → 1 commit ≈ 50-150ms。
        await self.session.flush()


__all__ = ["ScreeningReport", "ScreeningService"]
