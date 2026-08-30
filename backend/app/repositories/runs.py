"""Async repository for the ``runs`` table (MVP — simplify §37).

The Run table records every collection / AI / Feishu pipeline execution
so the `/status` Feishu command can show "what is the system doing right
now" and "what did it do last". One row per pipeline invocation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run


class RunRepository:
    """Async CRUD wrapper for ``Run`` rows.

    All methods accept an explicit ``session`` so the same instance can
    share the request-scoped ``AsyncSession`` used by the API layer —
    no engine / sessionmaker is held by this class.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    async def start(self, *, trigger: str = "manual") -> Run:
        """Insert a new row in ``running`` state.

        ``trigger`` is one of "scheduler" | "manual" | "bot_run" (free
        string today; nothing in MVP validates it).
        """
        run = Run(status="running", trigger=trigger)
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_success(
        self,
        run: Run,
        *,
        raw_count: Optional[int] = None,
        new_count: Optional[int] = None,
        signal_count: Optional[int] = None,
    ) -> Run:
        """Mark a run as ``success`` and capture the final counts."""
        run.status = "success"
        run.finished_at = datetime.now(tz=timezone.utc)
        if raw_count is not None:
            run.raw_count = raw_count
        if new_count is not None:
            run.new_count = new_count
        if signal_count is not None:
            run.signal_count = signal_count
        await self.session.flush()
        return run

    async def finish_failed(self, run: Run, *, error: str) -> Run:
        """Mark a run as ``failed`` and persist the error message.

        Commits explicitly because sibling services (clustering /
        scoring / screening) call ``session.commit()`` after their own
        work — without an explicit commit here the failed-status
        update would be rolled back when the request-scoped session
        closes via ``__aexit__`` (which rolls back on exception),
        leaving the row stuck in ``running`` even though the
        endpoint raised.
        """
        run.status = "failed"
        run.finished_at = datetime.now(tz=timezone.utc)
        run.error = error[:8000]  # cap so a chatty trace doesn't blow the column
        await self.session.flush()
        await self.session.commit()
        return run

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    async def get_by_id(self, run_id: int) -> Optional[Run]:
        return await self.session.get(Run, run_id)

    async def latest(self) -> Optional[Run]:
        """Most recent Run row (by ``started_at`` desc)."""
        stmt = select(Run).order_by(desc(Run.started_at)).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def recent(self, *, limit: int = 10) -> list[Run]:
        """Most recent N Run rows (by ``started_at`` desc)."""
        stmt = select(Run).order_by(desc(Run.started_at)).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


__all__ = ["RunRepository"]
