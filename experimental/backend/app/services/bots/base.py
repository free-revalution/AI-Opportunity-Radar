"""Bot provider abstraction — Phase 6 of v2.0.

Phase 5 split notifications into two parallel code paths (Telegram in
`services/notification/`, Feishu in `services/feishu/`). Both reached
the same destination with very different shapes — two `send_*` methods,
two result dataclasses, two factories. Phase 6 unifies them behind a
single `BotProvider` interface so the upper-layer orchestrator
(`NotificationService`) becomes channel-agnostic and we can add
fallback (Feishu → Telegram) without duplicating business logic.

This package owns:

  * `BotChannel`           enum of supported platforms
  * `BotMessage`           unified message payload (text + optional card)
  * `BotSendResult`        unified result shape
  * `BotProvider`          ABC — single `async def send(...)` entry point
  * `TelegramBotAdapter`   wraps `TelegramProvider`
  * `FeishuBotAdapter`     wraps `FeishuProvider`
  * `build_bot_provider`   factory (mock | real) — channel-selectable
  * `FallbackBotProvider`  primary → secondary automatic failover

The two adapter classes do not duplicate HTTP plumbing — they wrap the
already-tested providers in `services/notification/` and `services/feishu/`
and translate the unified `BotMessage` into the per-platform payload.
Adding a new platform later (e.g. DingTalk) means writing one adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class BotChannel(str, Enum):
    """The supported bot channels. String-valued so it survives JSON."""

    TELEGRAM = "telegram"
    FEISHU = "feishu"


@dataclass(slots=True)
class BotMessage:
    """A message that can be sent via any `BotProvider`.

    `text`    — Markdown text body. Always required.
    `card`    — Optional structured payload (Feishu interactive card;
                Telegram ignores it). When both are present, Feishu
                sends the card; Telegram sends the text.
    `parse_mode` — For Telegram: "MarkdownV2" (default) | "HTML" |
                "Markdown". Ignored for Feishu (Feishu text-mode is
                lark_md and is shaped by the formatter, not here).
    `link_preview` — For Telegram: whether to render unfurl for URLs
                in the message. Ignored for Feishu.
    """

    text: str
    card: Optional[dict[str, Any]] = None
    parse_mode: str = "MarkdownV2"
    link_preview: bool = False


@dataclass(slots=True)
class BotSendResult:
    """Unified outcome of a single `BotProvider.send(...)` call."""

    ok: bool
    channel: str  # BotChannel value
    provider: str  # provider implementation name (e.g. "telegram", "mock-feishu")
    target: str  # chat_id / open_chat_id / oc_xxx — opaque to upper layers
    body_chars: int
    message_id: Optional[str] = None
    error: Optional[str] = None
    # For `FallbackBotProvider`: which provider actually delivered.
    # Empty when only one provider was attempted.
    delivered_by: str = ""


class BotProvider(ABC):
    """Channel-agnostic async boundary for sending one message.

    Implementations: `TelegramBotAdapter`, `FeishuBotAdapter`,
    `FallbackBotProvider`. The factory returns one of these.

    `name`    — implementation label (e.g. "telegram", "feishu",
                "mock-telegram", "mock-feishu", "fallback"). Used for
                metrics and `Notification.provider`.
    `channel` — which platform this provider talks to. Used by the
        upper layer (`NotificationService`) to populate
        `Notification.channel`.
    """

    name: str = "abstract"
    channel: str = BotChannel.TELEGRAM.value

    @abstractmethod
    async def send(self, *, target: str, message: BotMessage) -> BotSendResult:
        """Deliver one message. Must not raise — translate errors to
        a `BotSendResult(ok=False, error=...)` so the upper layer can
        iterate over fallbacks without try/except.

        `target` is the platform-specific chat/recipient id
        (Telegram: `chat_id`; Feishu: webhook is fixed and `target`
        is unused — pass the chat id from `event.message.chat_id` for
        future per-reply flows).
        """


__all__ = [
    "BotChannel",
    "BotMessage",
    "BotProvider",
    "BotSendResult",
]