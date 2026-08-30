"""Async opportunity repository — all DB access for opportunities."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Opportunity


class OpportunityRepository:
    """Thin async wrapper over the `opportunities` table.

    Repositories own the SQL; services own the business logic. Keeping
    them split means tests can fake either side independently.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------- queries -------------------------
    async def list_paginated(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
        status: str | None = None,
        min_total_score: float | None = None,
        q: str | None = None,
        sort: str = "total_score",
    ) -> tuple[Sequence[Opportunity], int]:
        """Return (rows, total_count).

        Phase 16D — ``q`` is a case-insensitive LIKE filter across
        ``title / summary / category / target_user``. SQLAlchemy
        ``.contains()`` maps to ILIKE on Postgres and LIKE on SQLite
        so the same code works against both backends used in tests.

        Phase 17D — ``sort`` accepts ``total_score`` (default, classic
        LLM-weighted blend) or ``signal_score`` (averaged across the
        opportunity's underlying Signal rows via
        ``signals.raw_item_id → opportunity_sources.raw_item_id``).
        Any other value is silently ignored — caller-side ``Query``
        validation should reject bad values before this fires.
        """
        base = select(Opportunity)
        count_q = select(func.count()).select_from(Opportunity)

        if category:
            base = base.where(Opportunity.category == category)
            count_q = count_q.where(Opportunity.category == category)
        if status:
            base = base.where(Opportunity.status == status)
            count_q = count_q.where(Opportunity.status == status)
        if min_total_score is not None:
            base = base.where(Opportunity.total_score >= min_total_score)
            count_q = count_q.where(Opportunity.total_score >= min_total_score)
        if q:
            # SQLAlchemy `.contains()` is dialect-aware: Postgres emits
            # ILIKE, SQLite emits LIKE. We OR across 4 columns and let
            # the planner pick the cheapest index. `autoescape=True`
            # escapes `%` / `_` in user input so the query is safe.
            pattern = q.strip()
            if pattern:
                kw = f"%{pattern}%"
                cond = (
                    Opportunity.title.contains(kw, autoescape=True)
                    | Opportunity.summary.contains(kw, autoescape=True)
                    | Opportunity.category.contains(kw, autoescape=True)
                    | Opportunity.target_user.contains(kw, autoescape=True)
                )
                base = base.where(cond)
                count_q = count_q.where(cond)

        if sort == "signal_score":
            # — Aggregate AVG(Signal.signal_score) per opportunity via
            # ``signals.raw_item_id → opportunity_sources.raw_item_id``.
            # ``nullslast()`` keeps opportunities with zero signals at
            # the bottom rather than scattering them.
            from app.models import OpportunitySource, Signal

            sig_avg = (
                select(func.avg(Signal.signal_score).label("avg_sig"))
                .select_from(Signal)
                .join(
                    OpportunitySource,
                    OpportunitySource.raw_item_id == Signal.raw_item_id,
                )
                .where(OpportunitySource.opportunity_id == Opportunity.id)
                .scalar_subquery()
            )
            base = base.order_by(sig_avg.desc().nullslast(), Opportunity.id.desc())
        else:
            # — Default: classic total_score (Phase 1–16 behaviour).
            base = base.order_by(Opportunity.total_score.desc(), Opportunity.id.desc())
        base = base.limit(limit).offset(offset)

        result = await self.session.execute(base)
        total = await self.session.scalar(count_q) or 0
        return result.scalars().all(), int(total)

    async def get_by_id(self, opportunity_id: int) -> Optional[Opportunity]:
        return await self.session.get(Opportunity, opportunity_id)

    async def get_by_slug(self, slug: str) -> Optional[Opportunity]:
        result = await self.session.execute(
            select(Opportunity).where(Opportunity.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str) -> Optional[Opportunity]:
        """Demo/test helper — matches by stringified id when seeded."""
        try:
            return await self.get_by_id(int(external_id))
        except (ValueError, TypeError):
            return None

    async def list_pending_screening(
        self, *, limit: int = 50
    ) -> Sequence[Opportunity]:
        """Opportunities that have not yet been screened.

        The screening service queries this; the worker processes them
        in batches so the LLM rate limit stays friendly.
        """
        result = await self.session.execute(
            select(Opportunity)
            .where(Opportunity.status == "detected")
            .order_by(Opportunity.id.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_scored_candidates(
        self, *, limit: int = 200
    ) -> Sequence[Opportunity]:
        """Opportunities whose screening has settled and may need re-scoring.

        Excludes terminal statuses (`research_complete`, `failed`).
        Returned in id order so the scoring sweeper is deterministic.
        """
        result = await self.session.execute(
            select(Opportunity)
            .where(Opportunity.status.in_(["screened", "scored", "research_eligible"]))
            .order_by(Opportunity.id.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def has_pending_research(self, opportunity_id: int) -> bool:
        """True if a non-terminal ResearchJob already exists for this Opportunity.

        Prevents duplicate research-job creation when the scoring
        sweeper runs more than once for the same opportunity.
        """
        from app.models import ResearchJob

        result = await self.session.execute(
            select(ResearchJob).where(
                ResearchJob.opportunity_id == opportunity_id,
                ResearchJob.status.in_(["pending", "running"]),
            )
        )
        return result.scalar_one_or_none() is not None

    # ------------------------- commands -------------------------
    async def create(self, **fields: Any) -> Opportunity:
        opp = Opportunity(**fields)
        self.session.add(opp)
        await self.session.flush()
        return opp

    async def upsert_by_slug(self, **fields: Any) -> Opportunity:
        slug = fields.get("slug")
        if not slug:
            raise ValueError("slug is required for upsert")
        existing = await self.get_by_slug(slug)
        if existing is None:
            return await self.create(**fields)
        for key, value in fields.items():
            setattr(existing, key, value)
        await self.session.flush()
        return existing