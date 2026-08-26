"""Tests for the new `_check_redis_with_client` DI seam.

`_check_redis` previously had no test seam — it would attempt a real
Redis connection and fall into the broad `except` → `{"status": "down"}`.
Phase 12 splits the function so the readiness probe (and any future
test) can inject a fake client.
"""

from __future__ import annotations

from app.api import health as health_module


class _FakeRedisClient:
    """Stand-in for `redis.asyncio.Redis` — returns the configured `pong`."""

    def __init__(self, *, pong: bool = True, raise_exc: Exception | None = None) -> None:
        self.pong = pong
        self.raise_exc = raise_exc
        self.ping_called = 0

    async def ping(self) -> bool:
        self.ping_called += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.pong


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
