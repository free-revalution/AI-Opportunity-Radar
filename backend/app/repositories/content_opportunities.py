"""ContentOpportunity repository — Phase 17.

Persists rows produced by the ``/content`` Feishu command and supports
the admin state-machine transitions
``draft → approved → published → archived`` plus ``* → rejected``
(see docs/下一阶段开发技术方案.md §87).

The repository owns the SQL; admin endpoints in ``app/api/admin.py``
call its ``transition_status`` helper rather than mutating fields
directly. That keeps illegal transitions in one place — callers see a
``ValueError`` (HTTP layer maps it to 422) and never write a forbidden
status pair by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentOpportunity


# ---------------------------------------------------------------------------
# Status state machine
# ---------------------------------------------------------------------------
# Allowed transitions keyed by current status. ``archived`` and
# ``rejected`` are terminal in Phase 17 — the admin API does not yet
# expose a route that writes them, but ``transition_status`` accepts the
# pair so Phase 18 can add the endpoints without touching the repo.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"approved", "rejected", "archived"},
    "approved": {"published", "rejected", "archived"},
    "published": {"archived"},
    "rejected": set(),  # terminal
    "archived": set(),  # terminal
}


class IllegalStatusTransition(ValueError):
    """Raised when ``transition_status`` is asked for a forbidden pair."""


class ContentOpportunityRepository:
    """Thin async wrapper over the ``content_opportunities`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------- queries -------------------------
    async def get_by_id(self, id: int) -> Optional[ContentOpportunity]:
        return await self.session.get(ContentOpportunity, id)

    async def list_paginated(
        self,
        *,
        status: str | None = None,
        signal_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[ContentOpportunity], int]:
        """Return (rows, total) filtered by status + signal_id.

        Phase 17 — the admin UI filters on ``metadata_json
        .compliance_blocked`` in Python post-filter (see admin endpoint).
        Pushing the JSON_EXTRACT into SQL would tie us to one dialect's
        operator (``->>`` on Postgres, ``json_extract`` on SQLite); the
        per-page result set is small enough that a Python walk is fine.
        """
        base = select(ContentOpportunity)
        count_q = select(
            ContentOpportunity.id  # cheap count via id column.
        ) if False else None  # placeholder; replaced below

        if status:
            base = base.where(ContentOpportunity.status == status)
            count_stmt = (
                select(ContentOpportunity)
                .where(ContentOpportunity.status == status)
            )
        else:
            count_stmt = select(ContentOpportunity)

        if signal_id is not None:
            base = base.where(ContentOpportunity.signal_id == signal_id)
            count_stmt = count_stmt.where(
                ContentOpportunity.signal_id == signal_id
            )

        from sqlalchemy import func

        count_q = select(func.count()).select_from(count_stmt.subquery())

        base = (
            base.order_by(
                ContentOpportunity.created_at.desc(),
                ContentOpportunity.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(base)
        total = await self.session.scalar(count_q) or 0
        return result.scalars().all(), int(total)

    # ------------------------- commands -------------------------
    async def create(self, **fields: Any) -> ContentOpportunity:
        """INSERT a new row. Caller commits.

        ``metadata_json`` defaults to ``{}`` if not supplied so the
        column never lands as NULL on disk — keeps JSON_EXTRACT queries
        in admin endpoints simpler.
        """
        fields.setdefault("metadata_json", {})
        row = ContentOpportunity(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def transition_status(
        self, id: int, new_status: str
    ) -> ContentOpportunity:
        """Update ``status`` enforcing the state machine.

        Returns the updated row. Raises:

          * ``ContentOpportunityRepository.NotFound`` — id missing
          * ``IllegalStatusTransition`` — current → new is forbidden

        Caller is responsible for committing; this only flushes.
        """
        row = await self.get_by_id(id)
        if row is None:
            raise _NotFound(id)
        cur = row.status
        allowed = _ALLOWED_TRANSITIONS.get(cur, set())
        if new_status not in allowed:
            raise IllegalStatusTransition(
                f"illegal status transition: {cur} -> {new_status}"
            )
        row.status = new_status
        await self.session.flush()
        await self.session.refresh(row)
        return row


class _NotFound(LookupError):
    """Internal — admin endpoint maps to HTTP 404."""

    def __init__(self, id: int) -> None:
        super().__init__(id)
        self.id = id


# Re-export under a public name so the admin endpoint can catch it
# without leaking the private underscore class.
ContentOpportunityRepository.NotFound = _NotFound  # type: ignore[attr-defined]


__all__ = [
    "ContentOpportunityRepository",
    "IllegalStatusTransition",
]
