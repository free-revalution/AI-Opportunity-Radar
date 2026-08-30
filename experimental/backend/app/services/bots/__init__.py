"""Bot provider abstraction — Phase 6.

Public surface:

    BotChannel                   enum (telegram | feishu)
    BotMessage                   unified message (text + optional card)
    BotSendResult                unified result
    BotProvider                  ABC — `async def send(target, message)`

    TelegramBotAdapter           wraps `TelegramProvider`
    FeishuBotAdapter             wraps `FeishuProvider`
    FallbackBotProvider          primary → secondary failover

    build_bot_provider           channel-selecting factory
    build_bot_provider_with_fallback
                                 wraps the factory's primary output in
                                 a `FallbackBotProvider` per settings
"""

from app.services.bots.base import (
    BotChannel,
    BotMessage,
    BotProvider,
    BotSendResult,
)
from app.services.bots.fallback import (
    FallbackBotProvider,
    build_bot_provider_with_fallback,
)
from app.services.bots.factory import build_bot_provider
from app.services.bots.feishu_adapter import FeishuBotAdapter
from app.services.bots.telegram_adapter import TelegramBotAdapter

__all__ = [
    "BotChannel",
    "BotMessage",
    "BotProvider",
    "BotSendResult",
    "FallbackBotProvider",
    "FeishuBotAdapter",
    "TelegramBotAdapter",
    "build_bot_provider",
    "build_bot_provider_with_fallback",
]