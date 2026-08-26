"""Async signal repository."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal


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