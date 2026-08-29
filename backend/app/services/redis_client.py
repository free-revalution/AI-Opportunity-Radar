"""Redis client singleton — Phase 15A v2.0.

Lazy-initialised module-level connection pool. Returns ``None`` on
unreachable Redis so callers can pick a fail-open policy (we always do
for quota / rate-limit checks — see ``services/subscriptions/paywall.py``
and ``services/activation/flow.py``).

Why a custom singleton rather than ``aioredis.from_url`` inline at every
call site:

* Connection pooling — every consumer reuses the same socket pool.
* A single place to log `redis_unavailable_paywall_fail_open` when the
  daemon is down, so the operator notices in Grafana / log search.
* Test seam — ``set_redis_for_tests(None)`` / ``set_redis_for_tests(fake)``
  lets tests substitute an in-memory fake without monkey-patching
  ``redis.asyncio`` everywhere.

The client is created on first ``get_redis()`` call (lazy) so the app
can boot in environments where Redis is not yet up — a request that
hits a quota check will simply fail-open for that request. ``aclose()``
is wired into ``app.main.lifespan`` for graceful shutdown.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.config import get_settings
from app.utils import get_logger

logger = get_logger(__name__)


# ``redis.asyncio`` may not be installed in every dev environment (the
# health probe imports it lazily). We resolve the import on first use so
# tests that never call ``get_redis()`` don't blow up at import time.
_redis_module: Optional[Any] = None
_client: Optional[Any] = None
_lock = asyncio.Lock()


def _resolve_redis_module() -> Any:
    global _redis_module
    if _redis_module is None:
        import redis.asyncio as redis_async  # type: ignore[import-not-found]

        _redis_module = redis_async
    return _redis_module


async def get_redis() -> Any:
    """Return the lazily-initialised Redis client, or ``None``.

    Never raises — returns ``None`` when:

    * the ``redis`` package isn't installed (green-field dev / CI minimal)
    * the URL is unreachable (Redis down, DNS failure, etc.)

    Callers must check for ``None`` and choose fail-open semantics.
    """
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is not None:
            return _client
        try:
            redis_async = _resolve_redis_module()
            _client = redis_async.from_url(
                get_settings().redis_url,
                decode_responses=True,
            )
            # Probe once so a misconfigured URL fails fast here rather
            # than on the first ``incr()`` call mid-request.
            await asyncio.wait_for(_client.ping(), timeout=2.0)
            return _client
        except Exception as exc:  # noqa: BLE001 — surface as warn, never raise.
            logger.warning(
                "redis_unavailable_paywall_fail_open",
                error=str(exc)[:200],
            )
            _client = None
            return None


async def close_redis() -> None:
    """Cleanup hook — called from ``app.main.lifespan`` on shutdown."""
    global _client
    if _client is None:
        return
    try:
        await _client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_close_failed", error=str(exc)[:200])
    finally:
        _client = None


def set_redis_for_tests(client: Any) -> None:
    """Test seam — replace the singleton with a fake (or ``None``)."""
    global _client
    _client = client


__all__ = ["close_redis", "get_redis", "set_redis_for_tests"]
