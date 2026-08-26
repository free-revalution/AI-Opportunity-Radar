"""Telegram provider — abstraction over the Telegram Bot HTTP API.

The notification service never speaks HTTP to Telegram directly. Instead
it calls `TelegramProvider.send_message(...)`, behind a small async
interface. This keeps the boundary swappable and lets the test suite run
against a deterministic in-memory mock.

Selection (see `build_telegram_provider`):

  * mock        — offline, deterministic, default when no token is set
  * httpx       — real `POST https://api.telegram.org/bot{token}/sendMessage`
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class TelegramMessage:
    """A message that the provider successfully sent (or accepted in mock)."""

    chat_id: str
    text: str
    parse_mode: str = "MarkdownV2"
    link_preview_options: dict[str, Any] = field(default_factory=dict)
    message_id: Optional[str] = None


@dataclass(slots=True)
class TelegramSendResult:
    """Outcome of a single `send_message()` call."""

    ok: bool
    chat_id: str
    text_chars: int
    provider: str
    message_id: Optional[str] = None
    error: Optional[str] = None


class TelegramProvider(ABC):
    """Async boundary for sending Telegram messages."""

    name: str = "abstract"

    @abstractmethod
    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str = "MarkdownV2",
        link_preview: bool = False,
    ) -> TelegramSendResult:
        """Deliver a single message. Must not raise — translate to result."""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_telegram_provider(settings, *, prefer: Optional[str] = None):
    """Return the configured provider, falling back to the mock.

    Selection rules:
      * `MOCK_EXTERNAL_SERVICES=true`            → mock
      * `prefer="mock"`                          → mock
      * no `telegram_bot_token` configured       → mock
      * otherwise                                → httpx provider
    """
    if getattr(settings, "mock_external_services", False):
        from app.services.notification.mock_telegram import MockTelegramProvider

        return MockTelegramProvider()
    if (prefer or "").lower() == "mock":
        from app.services.notification.mock_telegram import MockTelegramProvider

        return MockTelegramProvider()
    token = getattr(settings, "telegram_bot_token", "") or ""
    if not token:
        from app.services.notification.mock_telegram import MockTelegramProvider

        return MockTelegramProvider()

    from app.services.notification.httpx_telegram import HttpxTelegramProvider

    return HttpxTelegramProvider(
        bot_token=token,
        timeout=float(getattr(settings, "telegram_timeout", 15.0)),
    )


__all__ = [
    "TelegramMessage",
    "TelegramProvider",
    "TelegramSendResult",
    "build_telegram_provider",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def encode_telegram_payload(
    *,
    chat_id: str,
    text: str,
    parse_mode: str = "MarkdownV2",
    link_preview: bool = False,
) -> dict[str, Any]:
    """Build the canonical Telegram `sendMessage` body."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": not link_preview,
    }
    return payload


def parse_telegram_response(body: str | bytes | dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON body — tolerate bytes/str for the httpx path."""
    if isinstance(body, dict):
        return body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return {"ok": False, "description": "invalid json from telegram"}


def message_ids_from(payload: dict[str, Any]) -> Sequence[str]:
    """Pull `message_id` strings out of a parsed Telegram response."""
    result = payload.get("result")
    if isinstance(result, dict):
        mid = result.get("message_id")
        if mid is not None:
            return [str(mid)]
    if isinstance(result, list):
        return [str(item.get("message_id")) for item in result if isinstance(item, dict)]
    return []
