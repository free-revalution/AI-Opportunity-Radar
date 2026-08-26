"""Async OpportunitySource repository — link table between opportunities and raw_items."""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OpportunitySource, RawItem


class OpportunitySourceRepository:
    """DB access for the `opportunity_sources` link table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------- queries -------------------------
    async def get_for_opportunity(
        self, opportunity_id: int
    ) -> Sequence[OpportunitySource]:
        result = await self.session.execute(
            select(OpportunitySource).where(
                OpportunitySource.opportunity_id == opportunity_id
            )
        )
        return result.scalars().all()

    async def list_raw_items_for_opportunity(
        self, opportunity_id: int
    ) -> list[RawItem]:
        """Return RawItems linked to the given opportunity, ordered by relevance desc."""
        result = await self.session.execute(
            select(RawItem)
            .join(
                OpportunitySource,
                OpportunitySource.raw_item_id == RawItem.id,
            )
            .where(OpportunitySource.opportunity_id == opportunity_id)
            .order_by(OpportunitySource.relevance.desc())
        )
        return list(result.scalars().all())

    async def raw_item_ids_in_any_opportunity(self) -> set[int]:
        """Return the set of RawItem ids already linked to an Opportunity.

        Used by the clustering service to skip items that have already
        been synthesised — keeps clustering idempotent.
        """
        result = await self.session.execute(
            select(OpportunitySource.raw_item_id)
        )
        return {row[0] for row in result.all()}

    # ------------------------- commands -------------------------
    async def link(
        self,
        *,
        opportunity_id: int,
        raw_item_id: int,
        relevance: float = 1.0,
    ) -> OpportunitySource:
        """Insert (or no-op) a single link row.

        `relevance` is overwritten on conflict so re-clustering with
        better data always wins.
        """
        link = OpportunitySource(
            opportunity_id=opportunity_id,
            raw_item_id=raw_item_id,
            relevance=relevance,
        )
        self.session.add(link)
        try:
            await self.session.flush()
            return link
        except Exception:
            await self.session.rollback()
            existing = await self.session.execute(
                select(OpportunitySource).where(
                    OpportunitySource.opportunity_id == opportunity_id,
                    OpportunitySource.raw_item_id == raw_item_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row is None:
                raise
            row.relevance = relevance
            await self.session.flush()
            return row


__all__ = ["OpportunitySourceRepository"]
