"""Phase 30 — Card action click handler.

When the user clicks a button in an interactive card (e.g. the
``[生成报告]`` button on the /run Top-N card), Feishu POSTs a
``card.action.trigger_v1`` event to ``/api/feishu/event``. The shape:

    {
      "header": {"event_type": "card.action.trigger_v1", "event_id": "..."},
      "event": {
        "operator": {"operator_id": {"open_id": "ou_xxx"}},
        "action": {
          "tag": "button",
          "value": {"action": "write_detail_docx", "opportunity_id": 42},
          "type": "primary",
          "text": {"tag": "plain_text", "content": "生成报告"}
        },
        "context": {"chat_id": "oc_xxx"}
      }
    }

This module turns that JSON into a real :class:`DetailDocxService` call
and returns the card "reply" payload that the inbound handler will push
back to the user via :class:`FeishuAppClient`.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config import Settings
from app.services.feishu.content_client import FeishuContentError
from app.services.feishu.detail_docx import DetailDocxService
from app.services.feishu.drive_org import DriveOrgService
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Action value parsing
# ---------------------------------------------------------------------------
SUPPORTED_ACTIONS = frozenset({"write_detail_docx"})


def extract_action(
    event_payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Pull ``(action_name, value_dict)`` out of a card.action payload.

    Returns ``("", {})`` for missing / malformed events so the caller
    can ack-and-skip rather than 500. Supported actions live in
    :data:`SUPPORTED_ACTIONS`.
    """
    event_obj = event_payload.get("event") or {}
    action = event_obj.get("action") or {}
    value = action.get("value") or {}
    if not isinstance(value, dict):
        return ("", {})
    name = str(value.get("action") or "").strip()
    return (name, value)


def extract_sender_open_id(event_payload: dict[str, Any]) -> str:
    event_obj = event_payload.get("event") or {}
    operator = event_obj.get("operator") or {}
    op_id = operator.get("operator_id") or {}
    return str(op_id.get("open_id") or "")


def extract_chat_id(event_payload: dict[str, Any]) -> str:
    event_obj = event_payload.get("event") or {}
    ctx = event_obj.get("context") or {}
    return str(ctx.get("chat_id") or "")


# ---------------------------------------------------------------------------
# Handler — runs the action and returns a "card reply" payload
# ---------------------------------------------------------------------------
async def handle_card_action_trigger(
    *,
    event_payload: dict[str, Any],
    settings: Settings,
    session: Any,
) -> Optional[dict[str, Any]]:
    """Dispatch a ``card.action.trigger_v1`` event to the right backend.

    Returns the JSON payload for an interactive card reply (so the
    inbound handler can POST it via ``app_client.send_message``), or
    ``None`` when the action isn't recognised — the inbound handler
    should ack-and-skip in that case.

    The function never raises for handler-level failures (missing
    opportunity, drive not configured, etc.) — those are caught and
    converted into a friendly error card so the user sees something
    useful and Feishu doesn't retry.
    """
    action_name, value = extract_action(event_payload)
    if not action_name:
        logger.info("card_action_no_action_name")
        return None
    if action_name not in SUPPORTED_ACTIONS:
        logger.info(
            "card_action_unsupported",
            action=action_name,
        )
        return None

    # — Phase 30 PR-4b: detail docx click.
    if action_name == "write_detail_docx":
        return await _handle_write_detail_docx(
            value=value, settings=settings, session=session
        )

    # — Defensive fallback — should be unreachable.
    logger.warning("card_action_unknown", action=action_name)
    return None


async def _handle_write_detail_docx(
    *,
    value: dict[str, Any],
    settings: Settings,
    session: Any,
) -> dict[str, Any]:
    """Click handler for the /run Top-N card's ``[生成报告]`` button.

    Steps:
      1) Read ``opportunity_id`` from the action value (must be int).
      2) Build a :class:`DetailDocxService` and call ``write_to_drive``.
      3) Build the chat-card reply via ``render_chat_card_reply``.

    Failure modes are returned as a yellow "warning" card so the
    user sees a friendly message instead of 500-from-bot.
    """
    try:
        opportunity_id = int(value.get("opportunity_id") or 0)
    except (ValueError, TypeError):
        return _error_card("按钮数据格式错误,缺少 opportunity_id。")
    if opportunity_id <= 0:
        return _error_card("按钮数据格式错误,opportunity_id 必须为正整数。")

    # — Lazy-drive: FeishuDriveClient.create_default reads from
    # settings; the singleton picks up the same settings object the
    # inbound handler is wired with.
    from app.services.feishu.content_client import FeishuDriveClient

    try:
        drive = FeishuDriveClient.create_default(settings=settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("card_action_drive_init_failed", error=str(exc)[:200])
        return _error_card("Drive 客户端初始化失败,请稍后重试。")

    if not drive.is_configured:
        return _error_card(
            "飞书云盘未配置,无法生成详情报告\n"
            "(FEISHU_DRIVE_ROOT_FOLDER_TOKEN 为空)。"
        )

    service = DetailDocxService(
        session=session, drive=drive, settings=settings
    )

    try:
        result = await service.write_to_drive(opportunity_id=opportunity_id)
    except FeishuContentError as exc:
        logger.warning(
            "card_action_detail_docx_failed",
            opportunity_id=opportunity_id,
            error=str(exc)[:200],
        )
        return _error_card(f"生成详情失败: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "card_action_detail_docx_unexpected",
            opportunity_id=opportunity_id,
            error=str(exc)[:200],
            exc_info=True,
        )
        return _error_card("详情生成遇到未预期错误,请稍后重试。")

    logger.info(
        "card_action_detail_docx_ok",
        opportunity_id=opportunity_id,
        doc_id=result.doc_id[:24],
        from_cache=result.from_cache,
    )
    return service.render_chat_card_reply(result)


def _error_card(message: str) -> dict[str, Any]:
    """Fallback yellow card for handler failures."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⚠️ 详情生成失败"},
            "template": "yellow",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": message},
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "Via /run · card.action.trigger_v1",
                    }
                ],
            },
        ],
    }


__all__ = [
    "SUPPORTED_ACTIONS",
    "extract_action",
    "extract_chat_id",
    "extract_sender_open_id",
    "handle_card_action_trigger",
]


# — Touch DriveOrgService so static analysers don't drop the import
# when the future detail-docx per-folder walk is added.
_ = DriveOrgService
