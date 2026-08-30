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

Phase 16E extends `_FakeRedisClient` with the SET family (`sadd`,
`scard`, `smembers`, `sismember`, `srem`) used by the new
distinct-signal quota counter in `paywall.py`. Storage for SETs is
kept in a separate `self._sets` dict so string and set types don't
collide on the same key — first write decides the kind, subsequent
operations on the wrong kind raise a TypeError to mirror real Redis
WRONGTYPE behaviour. (Currently the production code never mixes them,
but defensive checks catch test bugs early.)
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import pytest

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
        # Phase 16E — SET family storage. Kept in a parallel dict so
        # string and set types don't collide on the same key. The
        # first write (sadd / set) decides the key's "kind"; mixing
        # kinds on an existing key raises TypeError to mirror real
        # Redis WRONGTYPE behaviour.
        self._sets: dict[str, set[str]] = {}
        self._set_ttls: dict[str, float] = {}
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
        for key in list(self._sets.keys()):
            if self._set_expires_at(key) is not None and self._now >= self._set_expires_at(key):  # type: ignore[operator]
                self._sets.pop(key, None)
                self._set_ttls.pop(key, None)

    def _expires_at(self, key: str) -> float | None:
        return self._ttls.get(key)

    def _set_expires_at(self, key: str) -> float | None:
        return self._set_ttls.get(key)

    def _kind_of(self, key: str) -> str | None:
        """Return 'string' / 'set' / None based on where the key lives."""
        if key in self._store:
            return "string"
        if key in self._sets:
            return "set"
        return None

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

    # ---- set ops (Phase 16E) ----------------------------------------
    async def sadd(self, key: str, *members: str) -> int:
        """SADD. Returns count of NEW members added (idempotent for
        already-present members — matches Redis semantics).
        """
        self._evict_expired()
        kind = self._kind_of(key)
        if kind == "string":
            raise TypeError(
                f"WRONGTYPE Operation against a key holding the wrong kind of value: {key}"
            )
        bucket = self._sets.setdefault(key, set())
        added = 0
        for m in members:
            if m not in bucket:
                bucket.add(m)
                added += 1
        return added

    async def scard(self, key: str) -> int:
        self._evict_expired()
        kind = self._kind_of(key)
        if kind == "string":
            raise TypeError(
                f"WRONGTYPE Operation against a key holding the wrong kind of value: {key}"
            )
        return len(self._sets.get(key, set()))

    async def smembers(self, key: str) -> set[str]:
        self._evict_expired()
        kind = self._kind_of(key)
        if kind == "string":
            raise TypeError(
                f"WRONGTYPE Operation against a key holding the wrong kind of value: {key}"
            )
        return set(self._sets.get(key, set()))

    async def sismember(self, key: str, member: str) -> bool:
        self._evict_expired()
        kind = self._kind_of(key)
        if kind == "string":
            raise TypeError(
                f"WRONGTYPE Operation against a key holding the wrong kind of value: {key}"
            )
        return member in self._sets.get(key, set())

    async def srem(self, key: str, *members: str) -> int:
        self._evict_expired()
        kind = self._kind_of(key)
        if kind == "string":
            raise TypeError(
                f"WRONGTYPE Operation against a key holding the wrong kind of value: {key}"
            )
        bucket = self._sets.get(key)
        if bucket is None:
            return 0
        removed = 0
        for m in members:
            if m in bucket:
                bucket.discard(m)
                removed += 1
        if not bucket:
            self._sets.pop(key, None)
            self._set_ttls.pop(key, None)
        return removed

    async def expire_set(self, key: str, seconds: int) -> bool:
        """Phase 16E — set a TTL on a SET-typed key. Returns False if
        the key isn't a set. (Mirrors `expire()` but for the set
        bucket.)"""
        if key not in self._sets:
            return False
        self._set_ttls[key] = self._now + float(seconds)
        return True

    # ---- iteration helpers for tests --------------------------------
    def snapshot(self) -> dict[str, str]:
        self._evict_expired()
        return dict(self._store)

    def snapshot_sets(self) -> dict[str, set[str]]:
        """Phase 16E — read-only view of the SET bucket. Test code
        uses this to assert distinct-quota state."""
        self._evict_expired()
        return {k: set(v) for k, v in self._sets.items()}

    def __contains__(self, key: str) -> bool:
        self._evict_expired()
        return key in self._store or key in self._sets

    def __iter__(self) -> Iterator[str]:
        self._evict_expired()
        seen: set[str] = set()
        for k in self._store:
            seen.add(k)
            yield k
        for k in self._sets:
            if k not in seen:
                yield k


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


# ---------------------------------------------------------------------------
# Phase 16E — fake Redis SET family for distinct-signal quota tests
# ---------------------------------------------------------------------------
async def test_fake_redis_sadd_returns_new_member_count() -> None:
    fake = _FakeRedisClient()
    added = await fake.sadd("sigset", "1", "2", "3")
    assert added == 3
    # Same members again → idempotent, returns 0.
    added = await fake.sadd("sigset", "1", "2", "3")
    assert added == 0


async def test_fake_redis_sadd_returns_partial_when_some_new() -> None:
    fake = _FakeRedisClient()
    await fake.sadd("sigset", "1", "2")
    added = await fake.sadd("sigset", "2", "3", "4")
    assert added == 2  # only 3 and 4 were new


async def test_fake_redis_scard_zero_when_missing() -> None:
    fake = _FakeRedisClient()
    assert await fake.scard("missing") == 0


async def test_fake_redis_scard_counts_only_set_members() -> None:
    fake = _FakeRedisClient()
    await fake.sadd("sigset", "a", "b", "c")
    assert await fake.scard("sigset") == 3


async def test_fake_redis_smembers_empty_when_missing() -> None:
    fake = _FakeRedisClient()
    assert await fake.smembers("missing") == set()


async def test_fake_redis_smembers_returns_snapshot_copy() -> None:
    fake = _FakeRedisClient()
    await fake.sadd("sigset", "a", "b")
    members = await fake.smembers("sigset")
    assert members == {"a", "b"}
    # Mutating the returned set must NOT affect the stored bucket.
    members.add("c")
    again = await fake.smembers("sigset")
    assert again == {"a", "b"}


async def test_fake_redis_sismember_false_when_missing() -> None:
    fake = _FakeRedisClient()
    assert await fake.sismember("missing", "x") is False


async def test_fake_redis_sismember_truthy_for_member() -> None:
    fake = _FakeRedisClient()
    await fake.sadd("sigset", "x", "y")
    assert await fake.sismember("sigset", "x") is True
    assert await fake.sismember("sigset", "z") is False


async def test_fake_redis_srem_returns_zero_when_missing() -> None:
    fake = _FakeRedisClient()
    assert await fake.srem("missing", "x") == 0


async def test_fake_redis_srem_counts_actual_removals() -> None:
    fake = _FakeRedisClient()
    await fake.sadd("sigset", "a", "b", "c")
    removed = await fake.srem("sigset", "a", "missing", "c")
    assert removed == 2
    assert await fake.scard("sigset") == 1
    assert await fake.smembers("sigset") == {"b"}


async def test_fake_redis_srem_empties_bucket_when_last_member_removed() -> None:
    """Emptying a set removes it from `_sets` — keeps the snapshot clean."""
    fake = _FakeRedisClient()
    await fake.sadd("sigset", "a")
    await fake.srem("sigset", "a")
    assert "sigset" not in fake
    assert fake.snapshot_sets() == {}


async def test_fake_redis_set_and_set_keys_coexist_independently() -> None:
    """String and SET buckets don't collide on the same key — both
    operations succeed and read from their own dicts."""
    fake = _FakeRedisClient()
    await fake.set("strkey", "hello", ex=60)
    await fake.sadd("setkey", "x", "y")

    assert "strkey" in fake
    assert "setkey" in fake
    assert await fake.get("strkey") == "hello"
    assert await fake.smembers("setkey") == {"x", "y"}
    # Mixed-kind operations on the wrong key raise TypeError.
    with pytest.raises(TypeError):
        await fake.smembers("strkey")
    with pytest.raises(TypeError):
        await fake.sadd("strkey", "x")


async def test_fake_redis_snapshot_sets_reads_only_set_bucket() -> None:
    """The string-bucket `snapshot()` does not leak set contents into
    its dict view — keeps Phase 15 string tests stable."""
    fake = _FakeRedisClient()
    await fake.set("k", "v")
    await fake.sadd("s", "a")
    assert fake.snapshot() == {"k": "v"}
    assert fake.snapshot_sets() == {"s": {"a"}}


async def test_fake_redis_expire_set_then_advance_evicts_set() -> None:
    """TTL on a set bucket evicts it on the next read after expiry."""
    fake = _FakeRedisClient()
    await fake.sadd("sigset", "a", "b")
    assert await fake.expire_set("sigset", 30) is True
    assert await fake.scard("sigset") == 2
    fake.advance_time(31)
    assert await fake.scard("sigset") == 0
    assert "sigset" not in fake


async def test_fake_redis_expire_set_returns_false_for_missing() -> None:
    fake = _FakeRedisClient()
    assert await fake.expire_set("missing", 60) is False


async def test_fake_redis_iter_covers_both_buckets() -> None:
    """`__iter__` yields keys from both string and set buckets without
    duplicates — used by tests that walk the whole keyspace."""
    fake = _FakeRedisClient()
    await fake.set("k1", "v")
    await fake.sadd("s1", "a")
    await fake.sadd("s2", "b")
    assert set(fake) == {"k1", "s1", "s2"}


__all__ = ["_FakeRedisClient"]
