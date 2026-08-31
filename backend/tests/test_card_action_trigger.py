"""Phase 30 — card.action.trigger_v1 dispatch tests.

Covers the helper extractors + the click handler's success / failure
branches. Uses an in-memory SQLite + FakeDrive (same shape as the
detail_docx tests) so we don't hit Feishu.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest
import pytest_asyncio

from app.models import Opportunity, Source
from app.services.feishu.card_actions import (
    SUPPORTED_ACTIONS,
    extract_action,
    extract_chat_id,
    extract_sender_open_id,
    handle_card_action_trigger,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeDrive:
    def __init__(self, *, settings: Any) -> None:
        self.settings = settings
        self._folders: dict[tuple[str, str], str] = {}
        self._counter = 0

    @property
    def is_configured(self) -> bool:
        return bool(self.folder_token)

    @property
    def folder_token(self) -> str:
        return self.settings.feishu_drive_root_folder_token

    async def ensure_folder_path(
        self, *, parent_token: str, path: list[str]
    ) -> str:
        cur = parent_token
        for name in path:
            key = (cur, name)
            if key not in self._folders:
                self._counter += 1
                self._folders[key] = f"fld_{self._counter}_{name[:6]}"
            cur = self._folders[key]
        return cur

    async def create_docx_from_markdown(
        self,
        *,
        title: str,
        markdown: str,
        folder_token: Optional[str] = None,
    ) -> dict[str, Any]:
        self._counter += 1
        doc_id = f"doc_{self._counter:03d}"
        return {
            "doc_id": doc_id,
            "url": f"https://feishu.cn/docx/{doc_id}",
            "folder_token": folder_token or self.folder_token,
        }


class FakeRedis:
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


def _settings_with_root() -> Any:
    from app.config import get_settings

    s = get_settings()
    s.feishu_drive_root_folder_token = "root_tok"
    return s


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------
def test_extract_action_returns_name_and_value() -> None:
    payload = {
        "event": {
            "action": {
                "value": {"action": "write_detail_docx", "opportunity_id": 7}
            }
        }
    }
    name, value = extract_action(payload)
    assert name == "write_detail_docx"
    assert value["opportunity_id"] == 7


def test_extract_action_handles_missing_fields() -> None:
    assert extract_action({}) == ("", {})
    assert extract_action({"event": {}}) == ("", {})
    assert extract_action({"event": {"action": {"value": "not-a-dict"}}}) == ("", {})


def test_extract_sender_open_id_and_chat_id() -> None:
    payload = {
        "event": {
            "operator": {"operator_id": {"open_id": "ou_abc"}},
            "context": {"chat_id": "oc_chat_1"},
        }
    }
    assert extract_sender_open_id(payload) == "ou_abc"
    assert extract_chat_id(payload) == "oc_chat_1"


def test_supported_actions_includes_detail_docx() -> None:
    assert "write_detail_docx" in SUPPORTED_ACTIONS


# ---------------------------------------------------------------------------
# Handler — fresh write
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def seeded_opp(sqlite_session: Any) -> int:
    src = Source(
        id=10, name="TechCrunch", type="rss", url="https://tc.example"
    )
    sqlite_session.add(src)
    await sqlite_session.flush()
    opp = Opportunity(
        id=100,
        title="Phase 30 Click Test",
        slug="phase-30-click-test",
        total_score=80.0,
        source_count=1,
    )
    sqlite_session.add(opp)
    await sqlite_session.commit()
    return opp.id


@pytest_asyncio.fixture
async def drive_factory(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch FeishuDriveClient.create_default to return FakeDrive."""
    drives: dict[str, FakeDrive] = {}

    def _factory(*, settings: Any):
        d = FakeDrive(settings=settings)
        drives["current"] = d
        return d

    # — Sync replacement: card_actions.py calls
    # ``FeishuDriveClient.create_default(settings=settings)`` (no await).
    monkeypatch.setattr(
        "app.services.feishu.content_client.FeishuDriveClient.create_default",
        staticmethod(_factory),
    )
    return drives


@pytest_asyncio.fixture
async def redis_monkeypatch(monkeypatch: pytest.MonkeyPatch):
    fake = FakeRedis()

    async def _get_redis() -> Any:
        return fake

    monkeypatch.setattr(
        "app.services.feishu.detail_docx.get_redis", _get_redis
    )
    return fake


def _build_click_payload(
    *, opportunity_id: int, chat_id: str = "oc_first"
) -> dict[str, Any]:
    return {
        "header": {
            "event_type": "card.action.trigger_v1",
            "event_id": "ev_click_1",
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


@pytest.mark.asyncio
async def test_handler_writes_docx_and_returns_card(
    sqlite_session: Any,
    seeded_opp: int,
    drive_factory: Any,
    redis_monkeypatch: Any,
) -> None:
    settings = _settings_with_root()
    payload = _build_click_payload(opportunity_id=seeded_opp)
    card = await handle_card_action_trigger(
        event_payload=payload,
        settings=settings,
        session=sqlite_session,
    )
    assert card is not None
    # — Card shape
    assert card["header"]["template"] == "green"
    assert card["header"]["title"]["content"] == "✅ 详情已生成"
    body_md = card["elements"][0]["text"]["content"]
    assert "Phase 30 Click Test" in body_md
    # — Action button
    button = card["elements"][1]["actions"][0]
    assert button["url"].startswith("https://feishu.cn/docx/")


@pytest.mark.asyncio
async def test_handler_unknown_action_returns_none(
    sqlite_session: Any, drive_factory: Any, redis_monkeypatch: Any
) -> None:
    settings = _settings_with_root()
    payload = {
        "header": {"event_type": "card.action.trigger_v1"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_1"}},
            "context": {"chat_id": "oc_1"},
            "action": {
                "value": {"action": "not_supported", "opportunity_id": 1}
            },
        },
    }
    result = await handle_card_action_trigger(
        event_payload=payload,
        settings=settings,
        session=sqlite_session,
    )
    assert result is None


@pytest.mark.asyncio
async def test_handler_missing_opportunity_id_returns_error_card(
    sqlite_session: Any, drive_factory: Any, redis_monkeypatch: Any
) -> None:
    settings = _settings_with_root()
    payload = {
        "header": {"event_type": "card.action.trigger_v1"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_1"}},
            "context": {"chat_id": "oc_1"},
            "action": {
                "value": {"action": "write_detail_docx"}  # no opportunity_id
            },
        },
    }
    card = await handle_card_action_trigger(
        event_payload=payload,
        settings=settings,
        session=sqlite_session,
    )
    assert card is not None
    assert card["header"]["template"] == "yellow"
    assert "格式错误" in card["elements"][0]["text"]["content"]


@pytest.mark.asyncio
async def test_handler_invalid_opportunity_id_returns_error_card(
    sqlite_session: Any, drive_factory: Any, redis_monkeypatch: Any
) -> None:
    settings = _settings_with_root()
    payload = _build_click_payload(opportunity_id=99999)  # not in DB
    card = await handle_card_action_trigger(
        event_payload=payload,
        settings=settings,
        session=sqlite_session,
    )
    assert card is not None
    assert card["header"]["template"] == "yellow"
    body = card["elements"][0]["text"]["content"]
    assert "未找到" in body or "失败" in body


@pytest.mark.asyncio
async def test_handler_no_drive_configured_returns_error_card(
    sqlite_session: Any, drive_factory: Any, redis_monkeypatch: Any
) -> None:
    settings = _settings_with_root()
    settings.feishu_drive_root_folder_token = ""
    # — Drive factory still returns a FakeDrive but is_configured=False
    payload = _build_click_payload(opportunity_id=1)
    card = await handle_card_action_trigger(
        event_payload=payload,
        settings=settings,
        session=sqlite_session,
    )
    assert card is not None
    assert card["header"]["template"] == "yellow"
    assert "云盘未配置" in card["elements"][0]["text"]["content"]


@pytest.mark.asyncio
async def test_handler_idempotent_same_click(
    sqlite_session: Any,
    seeded_opp: int,
    drive_factory: Any,
    redis_monkeypatch: Any,
) -> None:
    """Clicking the same button twice → same doc_id, second card from_cache."""
    settings = _settings_with_root()
    payload = _build_click_payload(opportunity_id=seeded_opp)
    first = await handle_card_action_trigger(
        event_payload=payload,
        settings=settings,
        session=sqlite_session,
    )
    second = await handle_card_action_trigger(
        event_payload=payload,
        settings=settings,
        session=sqlite_session,
    )
    assert first is not None and second is not None
    first_url = first["elements"][1]["actions"][0]["url"]
    second_url = second["elements"][1]["actions"][0]["url"]
    assert first_url == second_url
    # — from_cache card template is blue, fresh is green
    assert first["header"]["template"] == "green"
    assert second["header"]["template"] == "blue"
