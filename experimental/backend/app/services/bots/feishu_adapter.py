"""Feishu adapter — wraps the existing `FeishuProvider`.

`FeishuProvider.send_card(card: FeishuCard)` only takes a card. When the
upper layer passes a `BotMessage` with no card (e.g. the daily digest
preview's `format_digest(...)` text body that Telegram sends as
MarkdownV2), this adapter wraps the text in a minimal interactive
card so Feishu still renders it as rich content. When a card IS
present, we ship the card verbatim (the formatter already built it).

The text → card fallback shape is intentionally minimal — Feishu's
custom-robot `msg_type="text"` payload is also accepted, but cards
render more nicely in the chat client.
"""

from __future__ import annotations

from typing import Any

from app.services.bots.base import (
    BotChannel,
    BotMessage,
    BotProvider,
    BotSendResult,
)
from app.services.feishu.base import FeishuCard, FeishuProvider
from app.utils.errors import ExternalServiceError


def _wrap_text_as_card(message: BotMessage) -> FeishuCard:
    """Build a minimal interactive card containing only the text.

    Falls back to this when the upper layer didn't pre-build a card.
    Feishu renders `lark_md` (a Markdown subset) inside `div` elements.
    """
    card_payload: dict[str, Any] = {
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": message.text,
                },
            }
        ]
    }
    return FeishuCard.from_card(card=card_payload, title="")


class FeishuBotAdapter(BotProvider):
    """Adapts `BotProvider.send(...)` to a `FeishuProvider`.

    Two message paths:
      * `message.card is not None` → ship the card verbatim
      * `message.card is None`     → wrap the text in a minimal card
                                     (so Feishu still renders rich text)

    Errors raised by the underlying provider (`ExternalServiceError`
    from the mock, httpx errors from the real client) propagate up.
    The `FallbackBotProvider` layer catches them.
    """

    name = "feishu-adapter"
    channel = BotChannel.FEISHU.value

    def __init__(self, provider: FeishuProvider) -> None:
        self._provider = provider

    @property
    def feishu_provider(self) -> FeishuProvider:
        return self._provider

    async def send(self, *, target: str, message: BotMessage) -> BotSendResult:
        # Card takes precedence — the formatter already did the work.
        if message.card is not None:
            card = FeishuCard.from_card(card=message.card, title="")
        else:
            card = _wrap_text_as_card(message)

        # `target` is unused today — Feishu's custom robot is bound to a
        # single webhook URL (no per-chat routing). Kept in the signature
        # so a future per-reply flow can plumb it through.
        del target
        try:
            result = await self._provider.send_card(card)
        except ExternalServiceError as exc:
            return BotSendResult(
                ok=False,
                channel=self.channel,
                provider=self._provider.name,
                target="",
                body_chars=len(message.text or ""),
                error=str(exc),
            )
        return BotSendResult(
            ok=result.ok,
            channel=self.channel,
            provider=result.provider,
            target="",
            body_chars=result.body_chars,
            error=result.error,
        )


__all__ = ["FeishuBotAdapter"]