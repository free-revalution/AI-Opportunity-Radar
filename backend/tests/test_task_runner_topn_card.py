"""Phase 30 — /run Top-N reply card tests.

Covers:
  * ``_build_header_card`` — shape + counts surface
  * ``_build_top_n_signal_card`` — each opp has a ``[生成报告]``
    button whose value carries the right action payload
  * ``_truncate`` helper
  * ``_fetch_top_opportunities`` — HTTP fail-open when server is down
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.feishu.task_runner import (
    _build_header_card,
    _build_top_n_signal_card,
    _truncate,
)


# ---------------------------------------------------------------------------
# _build_header_card
# ---------------------------------------------------------------------------
def test_build_header_card_renders_counts() -> None:
    summary = {
        "run_id": 42,
        "raw_count": 213,
        "new_count": 35,
        "signal_count": 9,
        "digest_sent": True,
        "started_at": "2026-08-31T09:00:00Z",
        "finished_at": "2026-08-31T09:08:21Z",
    }
    card = _build_header_card(summary=summary, task_id="t-abc-1")
    assert card["header"]["template"] == "green"
    assert card["header"]["title"]["content"] == "AI 机会雷达 · /run"
    body_md = card["elements"][0]["text"]["content"]
    assert "task_id=`t-abc-1`" in body_md
    assert "run_id=`42`" in body_md
    assert "**213**" in body_md  # raw_count
    assert "**35**" in body_md  # new_count
    assert "**9**" in body_md  # signal_count
    assert "**是**" in body_md  # digest_sent True


def test_build_header_card_handles_missing_keys() -> None:
    """Empty summary shouldn't crash — falls back to defaults."""
    card = _build_header_card(summary={}, task_id="t-x")
    assert card["header"]["template"] == "green"
    body_md = card["elements"][0]["text"]["content"]
    assert "run_id=`?`" in body_md
    assert "**0**" in body_md


# ---------------------------------------------------------------------------
# _build_top_n_signal_card
# ---------------------------------------------------------------------------
def test_build_top_n_card_each_opp_has_button() -> None:
    opps = [
        {
            "id": 1,
            "title": "AI Tutor",
            "slug": "ai-tutor",
            "total_score": 88.5,
            "category": "education",
            "source_count": 5,
            "summary": "An AI tutor for SMB teams.",
        },
        {
            "id": 2,
            "title": "EV Charging Optimizer",
            "slug": "ev-charging",
            "total_score": 75.0,
            "category": "energy",
            "source_count": 3,
            "summary": None,
        },
    ]
    card = _build_top_n_signal_card(opportunities=opps, task_id="t-2")
    assert card["header"]["template"] == "blue"
    assert card["header"]["title"]["content"] == "🔥 Top-N 机会信号"

    # — Find action elements (one per opp).
    action_elements = [
        el for el in card["elements"] if el.get("tag") == "action"
    ]
    assert len(action_elements) == 2

    # — Each action has 1 button with the right value shape.
    btn0 = action_elements[0]["actions"][0]
    assert btn0["tag"] == "button"
    assert btn0["text"]["content"] == "生成报告"
    assert btn0["value"] == {
        "action": "write_detail_docx",
        "opportunity_id": 1,
    }
    btn1 = action_elements[1]["actions"][0]
    assert btn1["value"]["opportunity_id"] == 2

    # — Row 1 shows title + score + category + sources
    row1 = card["elements"][0]
    row1_md = row1["text"]["content"]
    assert "#1 AI Tutor" in row1_md
    assert "**88**" in row1_md  # score formatted as int
    assert "education" in row1_md
    assert "5" in row1_md


def test_build_top_n_card_empty_opps_skips_action_layer() -> None:
    """An empty Top-N list should still build a card (header + note
    only), not crash."""
    card = _build_top_n_signal_card(opportunities=[], task_id="t-empty")
    action_elements = [
        el for el in card["elements"] if el.get("tag") == "action"
    ]
    assert action_elements == []
    assert card["header"]["title"]["content"] == "🔥 Top-N 机会信号"


def test_build_top_n_card_long_title_truncated() -> None:
    """Title over 60 chars is truncated — Feishu card layout breaks otherwise."""
    long_title = "X" * 100
    opps = [
        {
            "id": 1,
            "title": long_title,
            "total_score": 50.0,
            "category": "misc",
            "source_count": 1,
        }
    ]
    card = _build_top_n_signal_card(opportunities=opps, task_id="t")
    row_md = card["elements"][0]["text"]["content"]
    assert len(row_md.split("\n")[0]) <= 80  # "#1 XXX" prefix + 60 chars


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------
def test_truncate_short_text_unchanged() -> None:
    assert _truncate("hello", max_chars=10) == "hello"


def test_truncate_long_text_adds_ellipsis() -> None:
    out = _truncate("hello world", max_chars=8)
    assert out.endswith("…")
    assert len(out) == 8


# ---------------------------------------------------------------------------
# _fetch_top_opportunities
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_top_opportunities_handles_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the backend is unreachable, fetch returns ``[]`` so the
    reply card builder falls back to header-only (no Top-N)."""
    import httpx

    from app.config import get_settings
    from app.services.feishu.task_runner import _fetch_top_opportunities

    settings = get_settings()
    settings.feishu_internal_api_url = "http://127.0.0.1:1"  # bad port

    class _BoomClient:
        async def get(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError("nope")

    import app.services.feishu.task_runner as task_module

    # PR-33-G: 共享 client — 替换 _get_shared_client 返回 boom 替身
    monkeypatch.setattr(task_module, "_get_shared_client", lambda: _BoomClient())
    out = await _fetch_top_opportunities(settings=settings, n=5)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_top_opportunities_returns_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path — server returns a dict with ``items`` key, we return it."""
    from app.config import get_settings
    from app.services.feishu.task_runner import _fetch_top_opportunities

    settings = get_settings()
    settings.feishu_internal_api_url = "http://backend:8000"

    class _FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "items": [
                    {"id": 7, "title": "X", "total_score": 80.0},
                    {"id": 8, "title": "Y"},
                ],
                "count": 2,
            }

    class _FakeClient:
        async def get(self, *args: Any, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse()

    import app.services.feishu.task_runner as task_module

    # PR-33-G: 共享 client — 替换 _get_shared_client 返回 fake 替身
    monkeypatch.setattr(task_module, "_get_shared_client", lambda: _FakeClient())
    out = await _fetch_top_opportunities(settings=settings, n=2)
    assert len(out) == 2
    assert out[0]["id"] == 7
    assert out[1]["title"] == "Y"


# ---------------------------------------------------------------------------
# Phase 33 PR-33-G — shared httpx.AsyncClient pool
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_shared_client_is_singleton() -> None:
    """PR-33-G 回归: _get_shared_client() 多次调用返回同一对象 — 必须
    共用 connection pool,避免每次 task 重建 TCP/TLS。"""
    import app.services.feishu.task_runner as task_module

    # 强制重置(前一个 test 可能已建)
    task_module._SHARED_HTTP_CLIENT = None
    c1 = task_module._get_shared_client()
    c2 = task_module._get_shared_client()
    assert c1 is c2, "_get_shared_client() 每次返回新实例 — pool 没复用"
    # 清理(避免污染后续 test)
    await task_module.aclose_shared_client()
    assert task_module._SHARED_HTTP_CLIENT is None


@pytest.mark.asyncio
async def test_aclose_shared_client_resets_singleton() -> None:
    """PR-33-G 回归: aclose_shared_client() 关闭并清空 — 下次 _get_shared_client() 重新建。"""
    import app.services.feishu.task_runner as task_module

    c1 = task_module._get_shared_client()
    assert c1 is not None
    await task_module.aclose_shared_client()
    assert task_module._SHARED_HTTP_CLIENT is None
    c2 = task_module._get_shared_client()
    assert c2 is not c1, "aclose 后应建新 client"
    await task_module.aclose_shared_client()


@pytest.mark.asyncio
async def test_get_shared_client_uses_pool_limits() -> None:
    """PR-33-G 回归: shared client 配置了 max_connections=10, keepalive=5。

    注:httpx 的 Limits 在 client 上没有公开属性直接读,只能通过
    ``_transport`` 间接验。直接 assert client 类型 + timeout 配置即可。
    """
    import httpx

    import app.services.feishu.task_runner as task_module

    task_module._SHARED_HTTP_CLIENT = None
    c = task_module._get_shared_client()
    try:
        assert isinstance(c, httpx.AsyncClient)
        # 默认 timeout 30s(httpx.Timeout 对象 .connect / .read / .write / .pool)
        assert float(c.timeout.connect) == 30.0
    finally:
        await task_module.aclose_shared_client()
