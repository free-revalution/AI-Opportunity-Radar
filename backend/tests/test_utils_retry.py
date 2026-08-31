"""Phase 31 P31-C — async retry helper tests.

覆盖 retry_async 的核心行为:
  * 第一次成功 → 直接返回
  * 第 N 次成功 → 重试并返回
  * 全部失败 → 抛最后一个异常
  * 非 retriable 异常 → 立即抛出(不重试)
  * max_attempts < 1 → ValueError
  * on_retry 钩子被调
  * backoff 退避(2s / 4s)用 asyncio.sleep 计数
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.utils import retry_async
from app.utils.errors import (
    ExternalServiceError,
    RateLimitError,
    RetryableError,
    TimeoutError_,
    ValidationError,
)


class _Boom(RetryableError):
    """Test exception extending RetryableError (default retriable)."""


@pytest.mark.asyncio
async def test_retry_succeeds_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def _ok() -> str:
        return "ok"

    out = await retry_async(
        _ok, max_attempts=3, base_delay=1.0, op_name="t1"
    )
    assert out == "ok"
    # 一次就成功 → 不应 sleep
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    calls = {"n": 0}

    async def _flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _Boom("transient")
        return "ok-after-1-retry"

    out = await retry_async(
        _flaky, max_attempts=3, base_delay=2.0, op_name="t2"
    )
    assert out == "ok-after-1-retry"
    # 第一次失败 → sleep 2s → 第二次成功
    assert sleep_calls == [2.0]


@pytest.mark.asyncio
async def test_retry_exhausts_raises_last_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    calls = {"n": 0}

    async def _always_fail() -> None:
        calls["n"] += 1
        raise _Boom(f"fail-{calls['n']}")

    with pytest.raises(_Boom, match="fail-3"):
        await retry_async(
            _always_fail, max_attempts=3, base_delay=1.0, op_name="t3"
        )
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retry_skips_non_retriable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    calls = {"n": 0}

    async def _fail_validation() -> None:
        calls["n"] += 1
        raise ValidationError("not retriable")

    with pytest.raises(ValidationError):
        await retry_async(
            _fail_validation, max_attempts=3, base_delay=1.0, op_name="t4"
        )
    # ValidationError 不在 retriable_exceptions 里 → 立即抛
    assert calls["n"] == 1
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_retry_max_attempts_must_be_positive() -> None:
    async def _ok() -> None:
        return None

    with pytest.raises(ValueError, match="max_attempts"):
        await retry_async(_ok, max_attempts=0)


@pytest.mark.asyncio
async def test_retry_backoff_exponential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def _always_fail() -> None:
        raise _Boom("nope")

    with pytest.raises(_Boom):
        await retry_async(
            _always_fail,
            max_attempts=4,
            base_delay=2.0,
            backoff_multiplier=2.0,
            op_name="t6",
        )
    # max_attempts=4 → 3 次重试 sleep
    # 每次: base × multiplier^(attempt-1)
    #   attempt=1 fail → sleep 2 × 1 = 2
    #   attempt=2 fail → sleep 2 × 2 = 4
    #   attempt=3 fail → sleep 2 × 4 = 8
    #   attempt=4 fail → 不 sleep(最后失败)
    assert sleep_calls == [2.0, 4.0, 8.0]


@pytest.mark.asyncio
async def test_retry_on_retry_hook_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    hook_calls: list[tuple[int, BaseException, float]] = []

    async def _hook(attempt: int, exc: BaseException, delay: float) -> None:
        hook_calls.append((attempt, exc, delay))

    async def _always_fail() -> None:
        raise _Boom("boom")

    with pytest.raises(_Boom):
        await retry_async(
            _always_fail,
            max_attempts=3,
            base_delay=2.0,
            op_name="t7",
            on_retry=_hook,
        )
    # 3 attempts → 2 retries → hook called twice
    assert len(hook_calls) == 2
    assert hook_calls[0][0] == 1  # attempt index
    assert hook_calls[1][0] == 2
    assert isinstance(hook_calls[0][1], _Boom)


@pytest.mark.asyncio
async def test_retry_hook_failure_does_not_break_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    async def _bad_hook(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("hook kaboom")

    calls = {"n": 0}

    async def _flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom("transient")
        return "ok"

    out = await retry_async(
        _flaky,
        max_attempts=3,
        base_delay=1.0,
        op_name="t8",
        on_retry=_bad_hook,
    )
    # hook 抛异常被吞掉,retry 继续
    assert out == "ok"


@pytest.mark.asyncio
async def test_retry_custom_retriable_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用方可以扩展 retriable_exceptions 列表,例如把 ValidationError 纳进来。"""
    async def _noop_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    async def _ok_after_2() -> str:
        # 用一个不默认 retriable 的异常,通过自定义 tuple 启用
        raise ValueError("custom-retriable")

    with pytest.raises(ValueError):
        await retry_async(
            _ok_after_2,
            max_attempts=2,
            base_delay=1.0,
            retriable_exceptions=(ValueError,),
            op_name="t9",
        )


@pytest.mark.asyncio
async def test_retry_does_not_swallow_unrelated_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未列入 retriable_exceptions 的异常立即抛出,不计入重试。"""
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def _fail_keyboard_interrupt() -> None:
        raise KeyboardInterrupt("user pressed ctrl+c")

    with pytest.raises(KeyboardInterrupt):
        await retry_async(
            _fail_keyboard_interrupt,
            max_attempts=3,
            base_delay=1.0,
            op_name="t10",
        )
    assert sleep_calls == []


# ---------------------------------------------------------------------------
# 默认 retriable_exceptions 包含正确异常族(防回归)
# ---------------------------------------------------------------------------
def test_default_retriable_includes_app_error_family() -> None:
    from app.utils import DEFAULT_RETRIABLE

    assert RetryableError in DEFAULT_RETRIABLE
    assert RateLimitError in DEFAULT_RETRIABLE
    assert TimeoutError_ in DEFAULT_RETRIABLE
    assert ExternalServiceError in DEFAULT_RETRIABLE
    assert ValidationError not in DEFAULT_RETRIABLE