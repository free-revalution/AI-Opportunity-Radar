"""Async source repository."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_slug(self, slug: str) -> Optional[Source]:
        result = await self.session.execute(select(Source).where(Source.url.contains(slug)))
        return result.scalar_one_or_none()

    async def get_by_id(self, source_id: int) -> Optional[Source]:
        return await self.session.get(Source, source_id)

    async def list_enabled(self) -> list[Source]:
        result = await self.session.execute(
            select(Source).where(Source.enabled.is_(True))
        )
        return list(result.scalars().all())

    async def upsert(self, *, name: str, type: str, url: str, enabled: bool = True) -> Source:
        existing = await self.get_by_slug(name.lower())
        if existing is None:
            source = Source(name=name, type=type, url=url, enabled=enabled)
            self.session.add(source)
            await self.session.flush()
            return source
        existing.name = name
        existing.type = type
        existing.url = url
        existing.enabled = enabled
        await self.session.flush()
        return existing