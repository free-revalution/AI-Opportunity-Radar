"""Phase 31 P31-B — card.action.trigger_v1 transport-level event_id SETNX.

覆盖:
  * 同 event_id 二次点击 → 第二次跳过(返回 duplicate=True, 不调业务)
  * 不同 event_id → 各自独立
  * 无 event_id → fail-open(不阻挡,照常处理)
  * Redis 挂 → fail-open(不阻挡,照常处理)

测试策略: monkeypatch ``handle_card_action_trigger`` 计数,
SETNX 命中 → 业务层**不应该被调**。避免 outbound HTTP(tenant token)
复杂性,只关心 _handle_card_action 的入口幂等行为。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeRedis:
    """Minimal async Redis — only ``set(nx=True, ex=...)`` semantics."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self._kv.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: Optional[int] = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True


def _build_click_payload(
    *,
    opportunity_id: int,
    event_id: str = "ev_xyz_001",
    chat_id: str = "oc_chat_1",
) -> dict[str, Any]:
    return {
        "header": {
            "event_type": "card.action.trigger_v1",
            "event_id": event_id,
            "tenant_key": "tenant_xyz",
        },
        "event": {
            "operator": {"operator_id": {"open_id": "ou_user_1"}},
            "context": {"chat_id": chat_id},
            "action": {
                "tag": "button",
                "type": "primary",
                "text": {"tag": "plain_text", "content": "生成报告"},
                "value": {
                    "action": "write_detail_docx",
                    "opportunity_id": opportunity_id,
                },
            },
        },
    }


@pytest_asyncio.fixture
async def redis_factory(monkeypatch: pytest.MonkeyPatch):
    """Wire FakeRedis into ``get_redis`` (used by both detail_docx + inbound)."""
    fake = FakeRedis()

    async def _get_redis() -> Any:
        return fake

    monkeypatch.setattr(
        "app.services.feishu.detail_docx.get_redis", _get_redis
    )
    monkeypatch.setattr(
        "app.services.redis_client.get_redis", _get_redis
    )
    return fake


@pytest_asyncio.fixture
async def handler_stub(monkeypatch: pytest.MonkeyPatch):
    """Count calls to ``handle_card_action_trigger`` + return a fake card."""
    calls: list[dict[str, Any]] = []

    async def _stub(*, event_payload: Any, settings: Any, session: Any) -> dict[str, Any]:
        calls.append({"event_id": (event_payload.get("header") or {}).get("event_id")})
        # 真实 handler 返回的卡片最小骨架,_handle_card_action 只看它是否为 None
        return {
            "header": {"template": "green", "title": {"content": "✅ 详情已生成"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "x"}},
                {
                    "tag": "action",
                    "actions": [
                        {"tag": "button", "text": {"content": "查看详情"},
                         "url": "https://feishu.cn/docx/doc_001"}
                    ],
                },
            ],
        }

    # _handle_card_action 局部导入,patch 模块属性
    import app.services.feishu.card_actions as card_module

    monkeypatch.setattr(card_module, "handle_card_action_trigger", _stub)
    return calls


@pytest_asyncio.fixture
async def app_client_stub(monkeypatch: pytest.MonkeyPatch):
    """Capture outbound send_message — but make tenant token call noop."""
    sent: list[dict[str, Any]] = []

    class _StubAppClient:
        is_configured = True

        async def send_message(
            self,
            *,
            receive_id: str,
            msg_type: str,
            content: Any,
            receive_id_type: str = "chat_id",
            session: Any = None,
            compliance_context: str = "feishu_outbound",
        ) -> dict[str, Any]:
            sent.append(
                {
                    "receive_id": receive_id,
                    "msg_type": msg_type,
                    "content": content,
                    "compliance_context": compliance_context,
                }
            )
            return {"code": 0, "msg": "ok", "data": {"message_id": "om_stub"}}

        async def aclose(self) -> None:
            return None

    # 替成会调 send_message 的 stub。_handle_card_action 内部
    # ``from app.services.feishu.app_client import FeishuAppClient``(line 465),
    # 所以 patch 源模块而不是 feishu_inbound 命名空间。
    import app.services.feishu.app_client as app_client_module

    monkeypatch.setattr(
        app_client_module, "FeishuAppClient", lambda **kw: _StubAppClient()
    )
    return sent


def _settings_with_root() -> Any:
    from app.config import get_settings

    s = get_settings()
    s.feishu_drive_root_folder_token = "root_tok"
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_card_action_event_id_first_call_processes(
    sqlite_session: Any,
    handler_stub: list[dict[str, Any]],
    redis_factory: FakeRedis,
    app_client_stub: list[dict[str, Any]],
) -> None:
    """首次同 event_id → 正常调业务 handler → 发卡片。"""
    from app.api.feishu_inbound import _handle_card_action

    settings = _settings_with_root()
    payload = _build_click_payload(opportunity_id=1, event_id="ev_001")
    response = await _handle_card_action(
        body=payload, settings=settings, session=sqlite_session
    )
    assert response == {"code": 0, "msg": "ok"}
    # — 业务 handler 被调一次
    assert len(handler_stub) == 1
    # — 发卡片成功
    assert len(app_client_stub) == 1
    assert app_client_stub[0]["receive_id"] == "oc_chat_1"


@pytest.mark.asyncio
async def test_card_action_event_id_second_call_returns_duplicate(
    sqlite_session: Any,
    handler_stub: list[dict[str, Any]],
    redis_factory: FakeRedis,
    app_client_stub: list[dict[str, Any]],
) -> None:
    """同 event_id 第二次 → transport-level SETNX 命中,跳过。"""
    from app.api.feishu_inbound import _handle_card_action

    settings = _settings_with_root()
    payload = _build_click_payload(opportunity_id=1, event_id="ev_dup")

    first = await _handle_card_action(
        body=payload, settings=settings, session=sqlite_session
    )
    second = await _handle_card_action(
        body=payload, settings=settings, session=sqlite_session
    )

    assert first == {"code": 0, "msg": "ok"}
    assert second == {"code": 0, "msg": "ok", "duplicate": True}

    # — 业务 handler 只调一次
    assert len(handler_stub) == 1
    # — 只发一次卡
    assert len(app_client_stub) == 1


@pytest.mark.asyncio
async def test_card_action_no_event_id_still_processes(
    sqlite_session: Any,
    handler_stub: list[dict[str, Any]],
    redis_factory: FakeRedis,
    app_client_stub: list[dict[str, Any]],
) -> None:
    """无 event_id → fail-open:每次都按业务处理。"""
    from app.api.feishu_inbound import _handle_card_action

    settings = _settings_with_root()
    payload = _build_click_payload(opportunity_id=1, event_id="")

    first = await _handle_card_action(
        body=payload, settings=settings, session=sqlite_session
    )
    second = await _handle_card_action(
        body=payload, settings=settings, session=sqlite_session
    )

    # 两通都成功,且都调了业务 handler
    assert first["code"] == 0
    assert second["code"] == 0
    assert "duplicate" not in first
    assert "duplicate" not in second
    assert len(handler_stub) == 2


@pytest.mark.asyncio
async def test_card_action_redis_down_fail_open(
    sqlite_session: Any,
    handler_stub: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    app_client_stub: list[dict[str, Any]],
) -> None:
    """Redis 挂 → fail-open,不阻挡业务。"""
    from app.api.feishu_inbound import _handle_card_action

    class _BoomRedis:
        async def set(self, *args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("redis down")

        async def get(self, *args: Any, **kwargs: Any) -> Optional[str]:
            return None

    async def _get_redis() -> Any:
        return _BoomRedis()

    monkeypatch.setattr(
        "app.services.feishu.detail_docx.get_redis", _get_redis
    )
    monkeypatch.setattr(
        "app.services.redis_client.get_redis", _get_redis
    )

    settings = _settings_with_root()
    payload = _build_click_payload(opportunity_id=1, event_id="ev_redis_down")

    response = await _handle_card_action(
        body=payload, settings=settings, session=sqlite_session
    )
    assert response["code"] == 0
    assert "duplicate" not in response
    # 业务 handler 被调一次(没被 SETNX 阻挡)
    assert len(handler_stub) == 1


@pytest.mark.asyncio
async def test_card_action_different_event_ids_are_independent(
    sqlite_session: Any,
    handler_stub: list[dict[str, Any]],
    redis_factory: FakeRedis,
    app_client_stub: list[dict[str, Any]],
) -> None:
    """不同 event_id → 互相独立,各发一次卡。"""
    from app.api.feishu_inbound import _handle_card_action

    settings = _settings_with_root()
    payload_a = _build_click_payload(opportunity_id=1, event_id="ev_A")
    payload_b = _build_click_payload(opportunity_id=1, event_id="ev_B")

    a = await _handle_card_action(
        body=payload_a, settings=settings, session=sqlite_session
    )
    b = await _handle_card_action(
        body=payload_b, settings=settings, session=sqlite_session
    )

    assert a["code"] == 0 and "duplicate" not in a
    assert b["code"] == 0 and "duplicate" not in b
    assert len(handler_stub) == 2
    assert len(app_client_stub) == 2