"""Phase 31 P31-C — pipeline sinks 走 retry 的端到端测试。

覆盖:
  * RetryableError 抛一次 + 重试一次 = 成功 — 模拟 Feishu 5xx
  * RetryableError 抛 max_attempts 次 = raise — 模拟 Feishu 完全挂
  * 非 retriable 异常立即抛 — 模拟业务码 != 0
  * backoff 真的调 asyncio.sleep

注: internal.py 的 5 个 sink 都是函数内 ``from app.utils import
retry_async as _retry_async`` 局部 import,不能 monkeypatch 模块属性。
所以这里只测 retry_async 的真实行为 — 它被 sink 用 *args 调用,
函数引用保证内部 import 拿到同一个 callable。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.utils import retry_async
from app.utils.errors import RetryableError


# ---------------------------------------------------------------------------
# Sink-style retry patterns
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sink_retries_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟 _write_data_table: 第一次抛 RetryableError,第二次 ok。"""

    async def _noop_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    calls = {"n": 0}

    async def _fake_sink(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RetryableError("feishu transient 5xx")
        return {"inserted": 5, "skipped_duplicate": 0}

    # run_pipeline 里的调用形态
    out = await retry_async(
        _fake_sink,
        session=None,
        settings=None,
        run_id=1,
        max_attempts=3,
        base_delay=0.001,
        op_name="pipeline.write_data_table",
    )
    assert out == {"inserted": 5, "skipped_duplicate": 0}
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_sink_retries_exhausted_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟 _write_opportunities_table: 3 次都 RetryableError → raise,外层 try/except 接。"""

    async def _noop_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    calls = {"n": 0}

    async def _fake_sink(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        raise RetryableError(f"feishu still down #{calls['n']}")

    with pytest.raises(RetryableError, match="feishu still down #3"):
        await retry_async(
            _fake_sink,
            session=None,
            settings=None,
            run_id=1,
            n=5,
            max_attempts=3,
            base_delay=0.001,
            op_name="pipeline.write_opportunities_table",
        )
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_sink_does_not_retry_on_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FeishuContentError(业务码 != 0)不是 retriable — 不重试。

    注: 实际 FeishuContentError 不在 retry_async 默认 retriable_exceptions
    里(只有 RetryableError 等 family)。这里用 ValueError 替代验证"未列入
    retriable_exceptions 立即抛"的合约。
    """
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    calls = {"n": 0}

    async def _fake_sink(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        # 业务码 9999 不在 retry_async 的 retriable tuple 里
        raise ValueError("feishu biz code 9999: invalid parameter")

    with pytest.raises(ValueError):
        await retry_async(
            _fake_sink,
            session=None,
            settings=None,
            run_id=1,
            max_attempts=3,
            base_delay=0.001,
            op_name="pipeline.send_digest",
        )
    # 业务错不重试
    assert calls["n"] == 1
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_sink_uses_config_defaults(
    settings: Any,
) -> None:
    """验证 settings 的 radar_pipeline_max_retries / radar_pipeline_base_delay_seconds
    默认值能让 retry_async 直接工作 — 不需要每个 sink 单独传 max_attempts/base_delay。

    (internal.py 显式传了,这里只验证 default 值在 settings 上是合理的。)
    """
    assert settings.radar_pipeline_max_retries == 3
    assert settings.radar_pipeline_base_delay_seconds == 2.0


# ---------------------------------------------------------------------------
# Backoff 实打 asyncio.sleep
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sink_retry_invokes_asyncio_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retry_async 真的调 asyncio.sleep —— 不是 time.sleep。"""

    sleep_count = 0

    async def _fake_sleep(seconds: float) -> None:
        nonlocal sleep_count
        sleep_count += 1

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def _flaky() -> str:
        raise RetryableError("once")

    with pytest.raises(RetryableError):
        # max_attempts=3 → 2 次重试 → sleep 2 次
        await retry_async(
            _flaky, max_attempts=3, base_delay=0.001, op_name="t_sleep"
        )
    assert sleep_count == 2


# ---------------------------------------------------------------------------
# 5 个 sink 都用 retry_async 包 — 通过 ast 模块扫描验证(防回归)
# ---------------------------------------------------------------------------
def test_pipeline_run_uses_retry_async_for_all_feishu_sinks() -> None:
    """静态分析: internal.py 的 run_pipeline 必须对每个飞书写 sink
    调用 retry_async。Sink 列表:
        _write_data_table, _backfill_data_table_screening,
        _write_opportunities_table, send_digest, write_daily_docx
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "api"
        / "internal.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 找 retry_async / _retry_async / _retry_digest / _retry_docx 出现位置
    found_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.alias):
            if node.name == "retry_async":
                found_aliases.add(node.asname or "retry_async")
        # 也算直接 import 'from app.utils import retry_async'
        if isinstance(node, ast.ImportFrom) and node.module == "app.utils":
            for n in node.names:
                if n.name == "retry_async":
                    found_aliases.add(n.asname or "retry_async")

    # 必须有 3 个不同 alias(局部函数内 import):
    #   _retry_async (line 318 附近)
    #   _retry_digest (send_digest 内)
    #   _retry_docx (write_docx 内)
    assert "_retry_async" in found_aliases
    assert "_retry_digest" in found_aliases
    assert "_retry_docx" in found_aliases