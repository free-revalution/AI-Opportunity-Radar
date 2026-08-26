"""Async raw_item repository.

Deduplication contract (README §10 / §20):

    UNIQUE(source_id, external_id)   — DB-level uniqueness
    content_hash                     — sha256 over normalised title+url,
                                       used to detect the same story on
                                       different sources.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError  # noqa: F401 — kept for callers
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawItem


def compute_content_hash(url: str, title: str) -> str:
    """Stable hash over (url + title) used for cross-source dedup."""
    norm = " ".join((url.strip().lower(), title.strip().lower()))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class RawItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_source_external(
        self, source_id: int, external_id: str
    ) -> Optional[RawItem]:
        result = await self.session.execute(
            select(RawItem).where(
                RawItem.source_id == source_id, RawItem.external_id == external_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_content_hash(self, content_hash: str) -> Optional[RawItem]:
        result = await self.session.execute(
            select(RawItem).where(RawItem.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def create(self, *, content_hash: str, **fields: Any) -> RawItem:
        item = RawItem(content_hash=content_hash, **fields)
        self.session.add(item)
        await self.session.flush()
        return item

    async def upsert(
        self,
        *,
        source_id: int,
        external_id: str,
        url: str,
        title: str,
        **fields: Any,
    ) -> tuple[RawItem, bool]:
        """Insert or skip-on-conflict.

        Returns `(item, created)` where `created` is True iff a new row was
        inserted. The `content_hash` is always derived from (url, title).
        """
        content_hash = compute_content_hash(url, title)

        # Fast path — already inserted.
        existing = await self.get_by_source_external(source_id, external_id)
        if existing is not None:
            return existing, False
        existing = await self.get_by_content_hash(content_hash)
        if existing is not None:
            return existing, False

        # Try insert. If a parallel worker beat us to it the unique
        # constraint will reject — fall back to a re-read.
        try:
            item = await self.create(
                source_id=source_id,
                external_id=external_id,
                url=url,
                title=title,
                content_hash=content_hash,
                **fields,
            )
            await self.session.commit()
            return item, True
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_content_hash(content_hash)
            if existing is not None:
                return existing, False
            raise