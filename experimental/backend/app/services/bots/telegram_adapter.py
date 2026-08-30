"""Telegram adapter — wraps the existing `TelegramProvider`.

The existing `TelegramProvider.send_message(...)` takes a flat set of
kwargs (`chat_id`, `text`, `parse_mode`, `link_preview`). This adapter
maps the unified `BotMessage` shape onto that signature and translates
the per-call `TelegramSendResult` back to `BotSendResult`.

The wrapping is intentionally thin — no business logic lives here.
If Telegram's payload shape changes, only `TelegramProvider` and its
`encode_telegram_payload` helper need editing.
"""

from __future__ import annotations

from typing import Any

from app.services.bots.base import (
    BotChannel,
    BotMessage,
    BotProvider,
    BotSendResult,
)
from app.services.notification.telegram import TelegramProvider


class TelegramBotAdapter(BotProvider):
    """Adapts `BotProvider.send(...)` to a `TelegramProvider`."""

    name = "telegram-adapter"
    channel = BotChannel.TELEGRAM.value

    def __init__(self, provider: TelegramProvider) -> None:
        self._provider = provider

    @property
    def telegram_provider(self) -> TelegramProvider:
        """Escape hatch when callers (tests, ops scripts) need the
        underlying provider directly."""
        return self._provider

    @property
    def sent(self) -> Any:
        """Forward `.sent` to the underlying `MockTelegramProvider` so
        tests don't have to reach into `.telegram_provider.sent`. Returns
        `None` on real providers (which don't record sends in-memory).
        """
        return getattr(self._provider, "sent", None)

    async def send(self, *, target: str, message: BotMessage) -> BotSendResult:
        # Telegram ignores `message.card` — interactive cards don't
        # exist in the Bot HTTP API. We just send the text body.
        result = await self._provider.send_message(
            chat_id=target,
            text=message.text,
            parse_mode=message.parse_mode,
            link_preview=message.link_preview,
        )
        return BotSendResult(
            ok=result.ok,
            channel=self.channel,
            provider=result.provider,
            target=result.chat_id,
            body_chars=result.text_chars,
            message_id=result.message_id,
            error=result.error,
        )


__all__ = ["TelegramBotAdapter"]