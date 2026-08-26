"""Async signal repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal


@dataclass(slots=True)
class SignalAggregate:
    """Aggregated metrics for a set of signals.

    Returned by `aggregate_for_opportunity`. Lets the scoring service
    derive a velocity / engagement / relevance boost from raw signal
    rows — independent of the LLM's first-pass sub-scores.
    """

    count: int
    avg_velocity: float
    avg_engagement: float
    avg_relevance: float
    total_engagement: float

    @property
    def is_empty(self) -> bool:
        return self.count == 0


class SignalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **fields: Any) -> Signal:
        signal = Signal(**fields)
        self.session.add(signal)
        await self.session.flush()
        return signal

    async def get_by_raw_item(self, raw_item_id: int) -> list[Signal]:
        result = await self.session.execute(
            select(Signal).where(Signal.raw_item_id == raw_item_id)
        )
        return list(result.scalars().all())

    async def latest_for_keyword(
        self, keyword: str, limit: int = 50
    ) -> list[Signal]:
        result = await self.session.execute(
            select(Signal)
            .where(Signal.keyword == keyword)
            .order_by(Signal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_opportunity(
        self, opportunity_id: int
    ) -> list[Signal]:
        """Signals linked (transitively) to an Opportunity.

        Joins `signals.raw_item_id` → `opportunity_sources.raw_item_id`.
        """
        from app.models import OpportunitySource

        result = await self.session.execute(
            select(Signal)
            .join(
                OpportunitySource,
                OpportunitySource.raw_item_id == Signal.raw_item_id,
            )
            .where(OpportunitySource.opportunity_id == opportunity_id)
            .order_by(Signal.created_at.desc())
        )
        return list(result.scalars().all())

    async def aggregate_for_opportunity(
        self, opportunity_id: int
    ) -> SignalAggregate:
        """Aggregate all signals for one opportunity."""
        from app.models import OpportunitySource

        velocity = func.coalesce(func.avg(Signal.velocity_score), 0.0)
        engagement = func.coalesce(func.avg(Signal.engagement_score), 0.0)
        relevance = func.coalesce(func.avg(Signal.relevance_score), 0.0)
        total_engagement = func.coalesce(func.sum(Signal.engagement_score), 0.0)
        count = func.count(Signal.id)

        stmt = (
            select(
                count.label("count"),
                velocity.label("avg_velocity"),
                engagement.label("avg_engagement"),
                relevance.label("avg_relevance"),
                total_engagement.label("total_engagement"),
            )
            .select_from(Signal)
            .join(
                OpportunitySource,
                OpportunitySource.raw_item_id == Signal.raw_item_id,
            )
            .where(OpportunitySource.opportunity_id == opportunity_id)
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return SignalAggregate(
            count=int(row.count or 0),
            avg_velocity=float(row.avg_velocity or 0.0),
            avg_engagement=float(row.avg_engagement or 0.0),
            avg_relevance=float(row.avg_relevance or 0.0),
            total_engagement=float(row.total_engagement or 0.0),
        )


__all__ = ["SignalAggregate", "SignalRepository"]
