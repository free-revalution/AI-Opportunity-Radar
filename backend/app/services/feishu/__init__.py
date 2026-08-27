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

__all__ = [
    "FeishuBot",
    "FeishuCard",
    "FeishuDigestSummary",
    "FeishuProvider",
    "FeishuSendResult",
    "HttpxFeishuProvider",
    "MockFeishuProvider",
    "build_feishu_provider",
    "format_daily_digest",
    "sign_feishu_payload",
]