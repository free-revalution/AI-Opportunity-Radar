"""Tests for the Phase 6 bot abstraction layer.

Covers:

  * `BotProvider` contract (Telegram + Feishu adapters satisfy it)
  * `build_bot_provider` selection rules (mock vs real, default vs override)
  * `FallbackBotProvider` (primary succeeds → no fallback; primary fails →
    secondary carries the load; both fail → augmented error)

These run offline (no real Telegram/Feishu credentials). The mock
providers already used by the rest of the test suite provide
deterministic fixtures.
"""

from __future__ import annotations

import pytest

from app.services.bots import (
    BotChannel,
    BotMessage,
    BotProvider,
    BotSendResult,
    FallbackBotProvider,
    FeishuBotAdapter,
    TelegramBotAdapter,
    build_bot_provider,
    build_bot_provider_with_fallback,
)
from app.services.feishu.mock_client import MockFeishuProvider
from app.services.notification.mock_telegram import MockTelegramProvider

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Adapter contracts
# ---------------------------------------------------------------------------
async def test_telegram_adapter_satisfies_bot_provider_contract():
    """`TelegramBotAdapter` returns a `BotSendResult` with the right
    shape when wrapping the mock provider."""
    adapter = TelegramBotAdapter(MockTelegramProvider())
    assert isinstance(adapter, BotProvider)
    assert adapter.channel == BotChannel.TELEGRAM.value
    result = await adapter.send(
        target="chat-1",
        message=BotMessage(text="hello world", parse_mode="MarkdownV2"),
    )
    assert isinstance(result, BotSendResult)
    assert result.ok is True
    assert result.channel == "telegram"
    assert result.target == "chat-1"
    assert result.body_chars == len("hello world")
    assert result.message_id is not None
    assert result.error is None


async def test_feishu_adapter_satisfies_bot_provider_contract():
    """`FeishuBotAdapter` adapts text into a card when no card is set."""
    adapter = FeishuBotAdapter(MockFeishuProvider())
    assert isinstance(adapter, BotProvider)
    assert adapter.channel == BotChannel.FEISHU.value
    result = await adapter.send(
        target="ignored",
        message=BotMessage(text="hello feishu"),
    )
    assert result.ok is True
    assert result.channel == "feishu"
    # — text was wrapped into a card; body_chars is the JSON-encoded size
    # of the card (always larger than the raw text length).
    assert result.body_chars > len("hello feishu")


async def test_feishu_adapter_passes_card_through():
    """When the caller supplies a card, ship it verbatim (no re-wrap)."""
    raw_card = {
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "card"}}],
        "header": {"title": {"tag": "plain_text", "content": "Test"}},
    }
    mock = MockFeishuProvider()
    adapter = FeishuBotAdapter(mock)
    await adapter.send(
        target="ignored",
        message=BotMessage(text="ignored", card=raw_card),
    )
    sent = mock.sent
    assert len(sent) == 1
    # — title was preserved (the adapter did not overwrite the header).
    body = sent[0].body
    assert body["msg_type"] == "interactive"
    assert body["card"]["header"]["title"]["content"] == "Test"


async def test_telegram_adapter_propagates_failure():
    """Mock failure propagates as `ok=False` + error message."""
    adapter = TelegramBotAdapter(MockTelegramProvider(should_fail=True))
    result = await adapter.send(
        target="chat-1", message=BotMessage(text="hi")
    )
    assert result.ok is False
    assert "synthetic" in (result.error or "").lower()


async def test_feishu_adapter_translates_external_service_error():
    """Feishu mock raises `ExternalServiceError`; adapter catches and
    returns `ok=False` (NOT re-raises) — `FallbackBotProvider` reads
    the result, not the exception, on the Feishu path."""
    adapter = FeishuBotAdapter(MockFeishuProvider(should_fail=True))
    result = await adapter.send(
        target="ignored", message=BotMessage(text="hi")
    )
    assert result.ok is False
    assert result.channel == "feishu"
    assert "synthetic" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Factory selection rules
# ---------------------------------------------------------------------------
async def test_factory_picks_feishu_by_default(settings):
    """Default channel (Phase 6 product decision) is Feishu."""
    settings.notification_default_channel = "feishu"
    provider = build_bot_provider(settings, prefer="mock")
    assert isinstance(provider, FeishuBotAdapter)


async def test_factory_picks_telegram_when_explicit(settings):
    settings.notification_default_channel = "feishu"
    provider = build_bot_provider(settings, channel="telegram", prefer="mock")
    assert isinstance(provider, TelegramBotAdapter)


async def test_factory_falls_back_to_feishu_on_unknown_channel(settings):
    settings.notification_default_channel = "feishu"
    provider = build_bot_provider(settings, channel="slack", prefer="mock")
    # — unknown channel → Feishu (default), not raise.
    assert isinstance(provider, FeishuBotAdapter)


async def test_factory_uses_settings_default_when_set_to_telegram(settings):
    settings.notification_default_channel = "telegram"
    provider = build_bot_provider(settings, prefer="mock")
    assert isinstance(provider, TelegramBotAdapter)


async def test_factory_with_fallback_wraps_in_fallback_provider(settings):
    settings.notification_default_channel = "feishu"
    settings.notification_fallback_channels = ["telegram"]
    provider = build_bot_provider_with_fallback(settings)
    assert isinstance(provider, FallbackBotProvider)
    assert isinstance(provider.primary, FeishuBotAdapter)
    assert isinstance(provider.secondary, TelegramBotAdapter)


async def test_factory_with_empty_fallback_list_returns_primary(settings):
    settings.notification_default_channel = "feishu"
    settings.notification_fallback_channels = []
    provider = build_bot_provider_with_fallback(settings)
    # — no fallback wrapping.
    assert isinstance(provider, FeishuBotAdapter)
    assert not isinstance(provider, FallbackBotProvider)


# ---------------------------------------------------------------------------
# FallbackBotProvider
# ---------------------------------------------------------------------------
async def test_fallback_uses_primary_when_it_succeeds():
    primary = TelegramBotAdapter(MockTelegramProvider())
    secondary = TelegramBotAdapter(MockTelegramProvider())
    fallback = FallbackBotProvider(primary=primary, secondary=secondary)
    result = await fallback.send(
        target="chat-1", message=BotMessage(text="ok")
    )
    assert result.ok is True
    assert result.delivered_by == primary.name


async def test_fallback_falls_over_when_primary_fails():
    """Primary fails → secondary carries the message."""
    primary = TelegramBotAdapter(MockTelegramProvider(should_fail=True))
    secondary = TelegramBotAdapter(MockTelegramProvider())
    fallback = FallbackBotProvider(primary=primary, secondary=secondary)
    result = await fallback.send(
        target="chat-1", message=BotMessage(text="survive primary")
    )
    assert result.ok is True
    # — secondary delivered the message; delivered_by records it.
    assert result.delivered_by == secondary.name
    assert len(secondary.telegram_provider.sent) == 1


async def test_fallback_augments_error_when_both_fail():
    """Both fail → result.ok is False, error mentions both."""
    primary = TelegramBotAdapter(MockTelegramProvider(should_fail=True))
    secondary = FeishuBotAdapter(MockFeishuProvider(should_fail=True))
    fallback = FallbackBotProvider(primary=primary, secondary=secondary)
    result = await fallback.send(
        target="ignored", message=BotMessage(text="both fail")
    )
    assert result.ok is False
    assert result.delivered_by == ""
    # — both providers' errors are surfaced in the chain.
    assert "primary=" in (result.error or "")
    assert "secondary=" in (result.error or "")
    assert "synthetic" in (result.error or "").lower()


async def test_fallback_requires_secondary():
    with pytest.raises(ValueError):
        FallbackBotProvider(primary=TelegramBotAdapter(MockTelegramProvider()))


async def test_fallback_records_metric_on_primary_raise(monkeypatch):
    """`record_external_error` is called with the primary's name when
    the primary raises (e.g. transport-layer `ExternalServiceError`).

    When the primary returns `ok=False` *without* raising, no metric
    tick is recorded — the upper-layer Notification row already
    persists the error text.
    """
    from app.services.bots import BotMessage, BotSendResult, FeishuBotAdapter
    from app.services.bots.fallback import FallbackBotProvider
    from app.services.feishu.mock_client import MockFeishuProvider
    from app.utils.errors import ExternalServiceError

    calls: list[tuple[str, str]] = []

    def fake_record(provider: str, kind: str) -> None:
        calls.append((provider, kind))

    # — `fallback` imports `record_external_error` directly into its
    # module namespace; patch the module-local binding.
    monkeypatch.setattr(
        "app.services.bots.fallback.record_external_error",
        fake_record,
    )

    # — Build a stub primary that *raises* ExternalServiceError (bypassing
    # the Feishu adapter's translation).
    class RaisingPrimary(FeishuBotAdapter):
        async def send(self, *, target, message: BotMessage) -> BotSendResult:
            raise ExternalServiceError("simulated transport error", provider=self.name)

    primary = RaisingPrimary(MockFeishuProvider())
    secondary = FeishuBotAdapter(MockFeishuProvider())
    fallback = FallbackBotProvider(primary=primary, secondary=secondary)
    await fallback.send(target="ignored", message=BotMessage(text="hi"))
    # — primary raised → metric recorded with primary's name.
    recorded_names = {call[0] for call in calls}
    assert "feishu-adapter" in recorded_names