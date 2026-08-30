"""Phase 26 — ConfirmStore tests.

Covers the Redis-backed one-shot pending-action store used by
``/docs rm`` / ``/docs bitable rm``. Uses an in-memory fake
Redis (a plain dict + TTL via asyncio.sleep + monotonic time).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import pytest

from app.services.feishu.confirm_store import (
    ConfirmStore,
    ConfirmStoreUnavailable,
    PendingAction,
    get_confirm_store,
    set_confirm_store_for_tests,
)


# ---------------------------------------------------------------------------
# Fake Redis — minimal subset used by ConfirmStore
# ---------------------------------------------------------------------------
class _FakeRedis:
    """In-memory fake with SET EX + GETDEL semantics.

    Stores strings under keys with TTL in seconds. Expired keys
    are evicted on read.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        if not ex:
            return False
        self._store[key] = (value, time.time() + ex)
        return True

    async def get(self, key: str) -> Optional[str]:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if time.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    async def getdel(self, key: str) -> Optional[str]:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        self._store.pop(key, None)
        if time.time() >= expires_at:
            return None
        return value

    def pipeline(self, transaction: bool = True) -> "_Pipeline":
        return _Pipeline(self)

    async def __aenter__(self) -> "_FakeRedis":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _Pipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str]] = []

    def get(self, key: str) -> "_Pipeline":
        self._ops.append(("get", key))
        return self

    def delete(self, key: str) -> "_Pipeline":
        self._ops.append(("delete", key))
        return self

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for op, key in self._ops:
            if op == "get":
                out.append(await self._redis.get(key))
            elif op == "delete":
                self._redis._store.pop(key, None)
                out.append(1)
        return out

    async def __aenter__(self) -> "_Pipeline":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def store(fake_redis: _FakeRedis) -> ConfirmStore:
    return ConfirmStore(redis_client=fake_redis, ttl_sec=60)


@pytest.mark.asyncio
async def test_create_returns_pending_action_with_token(store: ConfirmStore) -> None:
    action = await store.create(
        kind="drive_delete",
        payload={"path": "📁 每日报告/2026-08-30/report.docx", "token": "abc"},
    )
    assert action.action_id and len(action.action_id) == 12
    assert action.kind == "drive_delete"
    assert action.payload["path"].endswith("report.docx")
    assert action.expires_at > action.created_at


@pytest.mark.asyncio
async def test_create_unknown_kind_raises(store: ConfirmStore) -> None:
    with pytest.raises(ValueError):
        await store.create(kind="invalid_kind", payload={})


@pytest.mark.asyncio
async def test_consume_returns_and_removes(store: ConfirmStore) -> None:
    action = await store.create(kind="drive_delete", payload={"k": "v"})
    consumed = await store.consume(action.action_id)
    assert consumed is not None
    assert consumed.action_id == action.action_id
    # — Second consume returns None (atomicity).
    again = await store.consume(action.action_id)
    assert again is None


@pytest.mark.asyncio
async def test_peek_does_not_remove(store: ConfirmStore) -> None:
    action = await store.create(kind="drive_delete", payload={"k": "v"})
    peeked = await store.peek(action.action_id)
    assert peeked is not None
    # — Peek again still works.
    again = await store.peek(action.action_id)
    assert again is not None


@pytest.mark.asyncio
async def test_consume_unknown_token_returns_none(store: ConfirmStore) -> None:
    out = await store.consume("nonexistent_token_id")
    assert out is None


@pytest.mark.asyncio
async def test_consume_with_no_redis_returns_none() -> None:
    s = ConfirmStore(redis_client=None)
    out = await s.consume("any_id")
    assert out is None


@pytest.mark.asyncio
async def test_create_with_no_redis_raises_unavailable() -> None:
    s = ConfirmStore(redis_client=None)
    with pytest.raises(ConfirmStoreUnavailable):
        await s.create(kind="drive_delete", payload={})


@pytest.mark.asyncio
async def test_redis_error_fails_open(store: ConfirmStore) -> None:
    """When Redis raises, peek/consume return None instead of crashing."""
    broken = _FakeRedis()

    async def boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("redis down")

    broken.get = boom  # type: ignore[assignment]
    broken.getdel = boom  # type: ignore[assignment]
    s = ConfirmStore(redis_client=broken)
    assert await s.peek("any") is None
    assert await s.consume("any") is None


@pytest.mark.asyncio
async def test_get_confirm_store_singleton_reuses_for_same_redis(
    fake_redis: _FakeRedis,
) -> None:
    set_confirm_store_for_tests(None)
    s1 = get_confirm_store(fake_redis)
    s2 = get_confirm_store(fake_redis)
    assert s1 is s2
    set_confirm_store_for_tests(None)  # cleanup


@pytest.mark.asyncio
async def test_pending_action_is_expired_after_ttl(fake_redis: _FakeRedis) -> None:
    s = ConfirmStore(redis_client=fake_redis, ttl_sec=1)
    action = await s.create(kind="drive_delete", payload={"k": "v"})
    # — Fake TTL by manipulating the stored expires_at.
    await asyncio.sleep(1.1)
    # — Redis evicts on read because real time has passed.
    out = await s.consume(action.action_id)
    assert out is None
