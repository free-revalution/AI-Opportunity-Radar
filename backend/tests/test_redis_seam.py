"""Tests for the new `_check_redis_with_client` DI seam.

`_check_redis` previously had no test seam — it would attempt a real
Redis connection and fall into the broad `except` → `{"status": "down"}`.
Phase 12 splits the function so the readiness probe (and any future
test) can inject a fake client.

Phase 15 extends `_FakeRedisClient` with the full surface used by the
paywall / activation-rate-limit code paths (`get`, `set`, `incr`,
`decr`, `delete`, `expire`, `ttl`). It's a tiny in-memory dict — not
a full Redis spec — but enough for the 5-10 ops our code actually
calls. The TTL is wall-clock seconds, with an `advance_time` helper
for tests that want to simulate key expiry.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

from app.api import health as health_module


# ---------------------------------------------------------------------------
# Fake Redis client
# ---------------------------------------------------------------------------
class _FakeRedisClient:
    """Stand-in for `redis.asyncio.Redis`.

    Phase 15E — extends the original `_check_redis_with_client` test
    fake (which only supported `ping`) with `get / set / incr / decr /
    delete / expire / ttl`. Storage is a `{key: str}` dict; expiry is
    wall-clock seconds with an explicit `advance_time(seconds)` helper.
    """

    def __init__(
        self,
        *,
        pong: bool = True,
        raise_exc: Exception | None = None,
        now: float | None = None,
    ) -> None:
        self.pong = pong
        self.raise_exc = raise_exc
        self.ping_called = 0
        self._store: dict[str, str] = {}
        self._ttls: dict[str, float] = {}
        # Wall-clock anchor for TTL bookkeeping. Tests that need to
        # simulate time passing call `advance_time(s)`.
        self._now = float(now) if now is not None else time.monotonic()

    # ---- time helpers ------------------------------------------------
    def advance_time(self, seconds: float) -> None:
        """Shift the wall-clock anchor forward — keys whose TTL elapses
        are dropped on the next read."""
        self._now += seconds
        self._evict_expired()

    def _evict_expired(self) -> None:
        for key in list(self._store.keys()):
            if self._expires_at(key) is not None and self._now >= self._expires_at(key):  # type: ignore[operator]
                self._store.pop(key, None)
                self._ttls.pop(key, None)

    def _expires_at(self, key: str) -> float | None:
        return self._ttls.get(key)

    # ---- ping (Phase 12 surface) -------------------------------------
    async def ping(self) -> bool:
        self.ping_called += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.pong

    # ---- string ops (Phase 15E) --------------------------------------
    async def get(self, key: str) -> str | None:
        self._evict_expired()
        return self._store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
    ) -> bool:
        self._evict_expired()
        self._store[key] = str(value)
        if ex is not None:
            self._ttls[key] = self._now + float(ex)
        else:
            self._ttls.pop(key, None)
        return True

    # ---- numeric ops (Phase 15E) ------------------------------------
    async def incr(self, key: str) -> int:
        self._evict_expired()
        current_raw = self._store.get(key)
        current = int(current_raw) if current_raw is not None else 0
        current += 1
        self._store[key] = str(current)
        return current

    async def decr(self, key: str) -> int:
        self._evict_expired()
        current_raw = self._store.get(key)
        current = int(current_raw) if current_raw is not None else 0
        current -= 1
        self._store[key] = str(current)
        return current

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self._store.pop(key, None) is not None:
                removed += 1
            self._ttls.pop(key, None)
        return removed

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self._store:
            return False
        self._ttls[key] = self._now + float(seconds)
        return True

    async def ttl(self, key: str) -> int:
        self._evict_expired()
        exp = self._expires_at(key)
        if exp is None:
            return -1
        remaining = exp - self._now
        return int(remaining) if remaining > 0 else -2

    # ---- iteration helpers for tests --------------------------------
    def snapshot(self) -> dict[str, str]:
        self._evict_expired()
        return dict(self._store)

    def __contains__(self, key: str) -> bool:
        self._evict_expired()
        return key in self._store

    def __iter__(self) -> Iterator[str]:
        self._evict_expired()
        return iter(self._store)


# ---------------------------------------------------------------------------
# Phase 12 tests — `_check_redis_with_client` DI seam
# ---------------------------------------------------------------------------
async def test_check_redis_with_client_healthy_when_ping_returns_true() -> None:
    fake = _FakeRedisClient(pong=True)
    result = await health_module._check_redis_with_client(fake)
    assert result == {"status": "healthy"}
    assert fake.ping_called == 1


async def test_check_redis_with_client_down_when_ping_returns_false() -> None:
    fake = _FakeRedisClient(pong=False)
    result = await health_module._check_redis_with_client(fake)
    assert result["status"] == "down"
    assert fake.ping_called == 1


async def test_check_redis_with_client_down_when_ping_raises() -> None:
    fake = _FakeRedisClient(raise_exc=RuntimeError("connection refused"))
    result = await health_module._check_redis_with_client(fake)
    assert result["status"] == "down"
    assert "connection refused" in result["error"]


async def test_check_redis_delegates_to_seam_when_no_client(monkeypatch) -> None:
    """The legacy `_check_redis()` should call `_check_redis_with_client(None)`."""
    fake = _FakeRedisClient(pong=True)
    calls: dict[str, object] = {"args": None}

    original = health_module._check_redis_with_client

    async def spy(client=None):
        calls["args"] = client
        return await original(client or fake)

    monkeypatch.setattr(health_module, "_check_redis_with_client", spy)
    result = await health_module._check_redis()
    assert result == {"status": "healthy"}
    # The legacy `_check_redis()` must call the seam with no argument.
    assert calls["args"] is None
    assert fake.ping_called == 1


# ---------------------------------------------------------------------------
# Phase 15E — fake Redis surface for paywall + activation rate-limit tests
# ---------------------------------------------------------------------------
async def test_fake_redis_incr_starts_at_one() -> None:
    fake = _FakeRedisClient()
    n = await fake.incr("k")
    assert n == 1
    n = await fake.incr("k")
    assert n == 2


async def test_fake_redis_set_with_ex_sets_ttl() -> None:
    fake = _FakeRedisClient()
    await fake.set("k", "v", ex=600)
    assert await fake.ttl("k") == 600


async def test_fake_redis_set_without_ex_clears_ttl() -> None:
    fake = _FakeRedisClient()
    await fake.set("k", "v", ex=600)
    await fake.set("k", "v2")
    assert await fake.ttl("k") == -1


async def test_fake_redis_expire_returns_false_for_missing_key() -> None:
    fake = _FakeRedisClient()
    assert await fake.expire("missing", 100) is False


async def test_fake_redis_delete_returns_count() -> None:
    fake = _FakeRedisClient()
    await fake.set("a", "1")
    await fake.set("b", "2")
    removed = await fake.delete("a", "b", "missing")
    assert removed == 2


async def test_fake_redis_advance_time_evicts_expired_keys() -> None:
    fake = _FakeRedisClient()
    await fake.set("k", "v", ex=10)
    assert "k" in fake
    fake.advance_time(11)
    assert "k" not in fake
    assert await fake.get("k") is None


async def test_fake_redis_get_evicts_expired_before_return() -> None:
    fake = _FakeRedisClient()
    await fake.set("k", "v", ex=5)
    fake.advance_time(6)
    assert await fake.get("k") is None


__all__ = ["_FakeRedisClient"]
