"""Async SQLAlchemy engine + session factory.

The engine is lazily created on first call so importing this module never
forces a DB connection (helpful for tests + tooling).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Return (or build) the global async engine."""
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            future=True,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return (or build) the global session factory."""
    get_engine()
    assert _sessionmaker is not None  # for type-checker
    return _sessionmaker


async def init_db() -> None:
    """Initialise the engine and verify connectivity."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: None)


async def close_db() -> None:
    """Dispose of the engine on shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields an async session bound to one request."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session