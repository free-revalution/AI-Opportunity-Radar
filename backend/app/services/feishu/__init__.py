"""Feishu (Lark) custom-robot integration — Phase 2 v2.0.

Public surface:

    FeishuProvider         async boundary (ABC)
    FeishuCard             pre-shaped interactive-card payload
    FeishuSendResult       per-call outcome
    build_feishu_provider  factory (mock | httpx)
    sign_feishu_payload    HMAC-SHA256 signing helper for "加签" robots
    HttpxFeishuProvider    real Feishu (POST JSON over HTTPS)
    MockFeishuProvider     offline test fixture (records every send)
    format_daily_digest    Opportunity rows → interactive card payload
    FeishuBot              orchestrator: pull opps → format → send

Phase 12G additions:
    authorize / role_required_for / user_facing_deny_message
        — RBAC enforcement for /admin commands.
    tool_allowed_for_llm / tool_is_admin_only
        — LLM tool allowlist (per docs §39).
"""

from app.services.feishu.base import (
    FeishuCard,
    FeishuProvider,
    FeishuSendResult,
    build_feishu_provider,
    sign_feishu_payload,
)
from app.services.feishu.bot import FeishuBot, FeishuDigestSummary
from app.services.feishu.client import HttpxFeishuProvider
from app.services.feishu.formatter import format_daily_digest
from app.services.feishu.mock_client import MockFeishuProvider
from app.services.feishu.rbac import (
    AuthVerdict,
    CommandRole,
    USER_TOOL_ALLOWLIST,
    authorize,
    is_activation_command,
    role_required_for,
    tool_allowed_for_llm,
    tool_is_admin_only,
    user_facing_deny_message,
)

__all__ = [
    "AuthVerdict",
    "CommandRole",
    "FeishuBot",
    "FeishuCard",
    "FeishuDigestSummary",
    "FeishuProvider",
    "FeishuSendResult",
    "HttpxFeishuProvider",
    "MockFeishuProvider",
    "USER_TOOL_ALLOWLIST",
    "authorize",
    "build_feishu_provider",
    "format_daily_digest",
    "is_activation_command",
    "role_required_for",
    "sign_feishu_payload",
    "tool_allowed_for_llm",
    "tool_is_admin_only",
    "user_facing_deny_message",
]