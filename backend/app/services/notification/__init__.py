"""Notification service package — Phase 8.

Public surface:

    NotificationService       orchestrator (preview / send / history)
    TelegramProvider          abstraction over the Telegram Bot HTTP API
    HttpxTelegramProvider     real Telegram (JSON over HTTPS)
    MockTelegramProvider      offline test fixture
    build_telegram_provider   factory

    format_digest / format_opportunity_alert   MarkdownV2 builders
    escape_markdown_v2 / truncate_to_telegram_limit / assert_markdown_v2_safe
    DigestEntry                                 per-item data carrier
"""

from app.services.notification.formatting import (
    DigestEntry,
    TELEGRAM_MAX_TEXT,
    TELEGRAM_RECOMMEND_EMOJI,
    assert_markdown_v2_safe,
    escape_markdown_v2,
    format_digest,
    format_opportunity_alert,
    truncate_text,
    truncate_to_telegram_limit,
)
from app.services.notification.httpx_telegram import HttpxTelegramProvider
from app.services.notification.mock_telegram import MockTelegramProvider
from app.services.notification.service import (
    DigestSendSummary,
    NotificationOutcome,
    NotificationService,
)
from app.services.notification.telegram import (
    TelegramMessage,
    TelegramProvider,
    TelegramSendResult,
    build_telegram_provider,
)

__all__ = [
    "DigestEntry",
    "DigestSendSummary",
    "HttpxTelegramProvider",
    "MockTelegramProvider",
    "NotificationOutcome",
    "NotificationService",
    "TELEGRAM_MAX_TEXT",
    "TELEGRAM_RECOMMEND_EMOJI",
    "TelegramMessage",
    "TelegramProvider",
    "TelegramSendResult",
    "assert_markdown_v2_safe",
    "build_telegram_provider",
    "escape_markdown_v2",
    "format_digest",
    "format_opportunity_alert",
    "truncate_text",
    "truncate_to_telegram_limit",
]
