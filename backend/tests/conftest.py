"""Shared pytest fixtures — DB overrides for API + repository tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Disable webhook auth + force mock mode for tests BEFORE app modules are
# imported — they cache settings via @lru_cache.
os.environ.setdefault("APP_SECRET_KEY", "")
os.environ.setdefault("MOCK_EXTERNAL_SERVICES", "true")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")

from app.config import get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture
def settings():
    """Per-test settings instance (clears the lru_cache)."""
    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture
async def sqlite_engine() -> AsyncIterator[AsyncEngine]:
    """In-memory SQLite engine — fully reset between tests.

    `connect_args={"check_same_thread": False}` is required because
    SQLAlchemy's async session spans threads under the hood.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def sqlite_session(sqlite_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    sessionmaker = async_sessionmaker(sqlite_engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
async def client(sqlite_engine: AsyncEngine) -> AsyncIterator[TestClient]:
    """FastAPI test client with the DB session dependency overridden.

    Every test starts with an empty SQLite database; tables are created
    once on engine startup and dropped on teardown. The sessionmaker is
    exposed via `client.sessionmaker` so tests can seed data without
    having to round-trip through the HTTP layer.
    """
    sessionmaker = async_sessionmaker(sqlite_engine, expire_on_commit=False)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as c:
        c.sessionmaker = sessionmaker  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"