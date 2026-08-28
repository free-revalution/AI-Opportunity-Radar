"""Bot provider factory — Phase 6.

Selects a `BotProvider` implementation based on settings. Mirrors the
selection rules already in `build_telegram_provider` and
`build_feishu_provider`:

  * `MOCK_EXTERNAL_SERVICES=true` → mock (any channel)
  * `prefer="mock"`               → mock
  * `channel="telegram"` + no token → mock-telegram
  * `channel="feishu"`   + no URL  → mock-feishu
  * otherwise                       → real provider (httpx)

The `channel` argument is the channel the caller wants. When omitted,
falls back to `settings.notification_default_channel` (default "feishu"
per Phase 6 product decision — Telegram becomes the fallback, not
the primary).

The returned `BotProvider` is **already wrapped** in an adapter — the
upper layer does not need to wrap it again. For a `FallbackBotProvider`
use `build_bot_provider_with_fallback(...)` instead.
"""

from __future__ import annotations

from typing import Optional

from app.services.bots.base import BotChannel, BotProvider
from app.services.bots.feishu_adapter import FeishuBotAdapter
from app.services.bots.telegram_adapter import TelegramBotAdapter
from app.services.feishu.base import build_feishu_provider
from app.services.notification.telegram import build_telegram_provider


def build_bot_provider(
    settings,
    *,
    channel: Optional[str] = None,
    prefer: Optional[str] = None,
) -> BotProvider:
    """Pick a channel-specific `BotProvider` based on `settings`.

    `channel` — explicit override (e.g. "telegram", "feishu"). When
        omitted, falls back to `settings.notification_default_channel`
        which defaults to "feishu".
    `prefer`  — "mock" forces the in-memory mock for whichever channel
        was selected. Used by tests and offline dev.
    """
    selected_channel = (channel or "").strip().lower() or (
        getattr(settings, "notification_default_channel", "feishu") or "feishu"
    ).strip().lower()

    if selected_channel not in (BotChannel.TELEGRAM.value, BotChannel.FEISHU.value):
        # Unknown channel name — fall back to the default rather than
        # raising. The Phase 6 product decision is "Feishu first".
        selected_channel = BotChannel.FEISHU.value

    if selected_channel == BotChannel.TELEGRAM.value:
        provider = build_telegram_provider(settings, prefer=prefer)
        return TelegramBotAdapter(provider)

    # Feishu path.
    provider = build_feishu_provider(settings, prefer=prefer)
    return FeishuBotAdapter(provider)


__all__ = ["build_bot_provider"]