"""Async retry helper with exponential backoff — Phase 31 P31-C.

Used by pipeline sinks that can be interrupted by transient Feishu / Redis
hiccups. Goal: one Feishu 5xx shouldn't fail the whole /run.

Design choices (PR-3 plan §3):
  * Exponential backoff: 2s / 4s / 8s for max_attempts=3, base_delay=2
  * Retriable exceptions default to ``RetryableError`` family + ``asyncio.TimeoutError``
    + ``ExternalServiceError``;``FeishuContentError`` is NOT retried by default
    (业务码 != 0 表明这是逻辑错,不是抖动)
  * Each attempt logs at INFO with attempt index + delay
  * ``on_retry`` optional hook (per-call) — pipeline uses it to record metric
  * ``asyncio.sleep`` is monkeypatch-able in tests (no ``time.sleep``)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional, TypeVar

from app.utils import get_logger
from app.utils.errors import (
    ExternalServiceError,
    RateLimitError,
    RetryableError,
    TimeoutError_,
)

logger = get_logger(__name__)

T = TypeVar("T")

DEFAULT_RETRIABLE: tuple[type[BaseException], ...] = (
    RetryableError,
    RateLimitError,
    TimeoutError_,
    ExternalServiceError,
    asyncio.TimeoutError,
    ConnectionError,
)

OnRetryHook = Callable[[int, BaseException, float], Awaitable[None]]


async def retry_async(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    backoff_multiplier: float = 2.0,
    retriable_exceptions: tuple[type[BaseException], ...] = DEFAULT_RETRIABLE,
    on_retry: Optional[OnRetryHook] = None,
    op_name: str = "retry_async",
    **kwargs: Any,
) -> T:
    """Run ``await func(*args, **kwargs)`` with exponential-backoff retry.

    Args:
      func: async callable to run
      max_attempts: total tries (1 = no retry, 3 = try once + retry twice)
      base_delay: seconds to sleep before retry 1 (then ×multiplier)
      backoff_multiplier: factor applied to delay each retry
      retriable_exceptions: tuple of exception types to retry on
      on_retry: async hook called BEFORE each sleep with
                ``(attempt_index, exception, delay_seconds)``
      op_name: label used in logs

    Returns:
      Whatever ``func`` returns.

    Raises:
      The last caught exception if all attempts fail. Non-retriable
      exceptions propagate immediately.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    last_exc: Optional[BaseException] = None
    delay = base_delay

    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except retriable_exceptions as exc:  # noqa: PERF203 — retry loop
            last_exc = exc
            if attempt >= max_attempts:
                logger.warning(
                    "retry_exhausted",
                    op=op_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=str(exc)[:200],
                )
                raise
            logger.info(
                "retry_attempt",
                op=op_name,
                attempt=attempt,
                max_attempts=max_attempts,
                delay_seconds=delay,
                error=str(exc)[:200],
            )
            if on_retry is not None:
                try:
                    await on_retry(attempt, exc, delay)
                except Exception as hook_exc:  # noqa: BLE001
                    logger.warning(
                        "retry_on_retry_hook_failed",
                        op=op_name,
                        error=str(hook_exc)[:200],
                    )
            await asyncio.sleep(delay)
            delay *= backoff_multiplier

    # 不可达 — Python 静态分析需要
    assert last_exc is not None
    raise last_exc


__all__ = ["retry_async", "OnRetryHook", "DEFAULT_RETRIABLE"]