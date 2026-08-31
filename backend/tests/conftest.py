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
# NOTE: webhook auth in app/api/internal.py reads `settings.app_secret_key`
# first, then `RADAR_WEBHOOK_SECRET` from os.environ. Both must be empty for
# `_check_webhook_secret` to short-circuit and accept the call. Direct
# assignment (not setdefault) because compose injects these from .env.
os.environ["APP_SECRET_KEY"] = ""
os.environ["RADAR_WEBHOOK_SECRET"] = ""
os.environ["MOCK_EXTERNAL_SERVICES"] = "true"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
# Phase 29 fix — set dummy Feishu credentials so FeishuAppClient
# construction in the bot async-runner path doesn't raise
# ``feishu app not configured`` during tests. The transport is mocked,
# so the credentials are never actually used to hit the real API.
os.environ.setdefault("FEISHU_APP_ID", "cli_test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test-app-secret-not-real")

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


@pytest.fixture(autouse=True)
def _reset_redis_singleton() -> None:
    """Phase 33 PR-33-C: 每次 test 自动清理真 Redis 上的 day-folder 残留。

    之前 PR-33-C 在 test_feishu_drive_org.py 几个 test 调
    ``set_redis_for_tests(fake)`` 写 singleton,但 teardown 不还原。
    下游 test_detail_docx::test_write_to_drive_fresh 走到 ``get_redis()``
    拿到上一个 test 注入的 FakeRedis,其中 ``store[day_folder_key]`` 还
    存着旧 token → 触发 "cache hit" → ``ensure_folder_path`` 不被调 →
    测试 fail。

    进一步问题: 真 Redis 上如果存在同名 key(如 batch1 PR-33-C 真实
    跑过写入的),测试会拿到残留。

    fix: 每次 test 前后清理真 Redis 上 day_folder 相关 key。
    test 自己的 monkeypatch 不动。
    """
    import asyncio as _aio
    try:
        import redis.asyncio as _r
        _c = _r.from_url("redis://localhost:6379/0", decode_responses=True)
        # Clear all day-folder keys (today + recent past) before test.
        for k in (
            "radar:drive:day_folder:2026-08-30",
            "radar:drive:day_folder:2026-08-31",
        ):
            _aio.run(_c.delete(k))
    except Exception:
        pass

    yield

    # After-test cleanup too: a test that wrote to real Redis leaves
    # residue for the next test.
    try:
        for k in (
            "radar:drive:day_folder:2026-08-30",
            "radar:drive:day_folder:2026-08-31",
        ):
            _aio.run(_c.delete(k))
    except Exception:
        pass
    try:
        _aio.run(_c.aclose())
    except Exception:
        pass


@pytest.fixture
def fake_redis() -> "_FakeRedisClient":
    """Fresh in-memory Redis fake for one test.

    Tests that exercise paywall / activation-rate-limit get this fixture
    and inject it into either ``app.services.redis_client.get_redis()``
    (via ``set_redis_for_tests``) or directly into the function-under-
    test's ``redis_client`` parameter. Reset between tests by virtue of
    pytest's per-function fixture scope.
    """
    from tests.test_redis_seam import _FakeRedisClient

    return _FakeRedisClient()