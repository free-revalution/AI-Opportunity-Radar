"""Tests for the Telegram provider — Mock + Httpx implementations."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.notification import (
    HttpxTelegramProvider,
    MockTelegramProvider,
    TelegramMessage,
    TelegramSendResult,
    build_telegram_provider,
)


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mock_provider_records_successful_send():
    p = MockTelegramProvider()
    result = await p.send_message(chat_id="123", text="hello")
    assert isinstance(result, TelegramSendResult)
    assert result.ok is True
    assert result.provider == "mock-telegram"
    assert result.chat_id == "123"
    assert result.text_chars == 5
    assert result.message_id is not None

    sent = p.sent
    assert len(sent) == 1
    assert sent[0].chat_id == "123"
    assert sent[0].text == "hello"
    assert sent[0].parse_mode == "MarkdownV2"
    assert sent[0].link_preview_options == {"disable_web_page_preview": True}


@pytest.mark.asyncio
async def test_mock_provider_message_ids_are_unique():
    p = MockTelegramProvider()
    a = await p.send_message(chat_id="1", text="a")
    b = await p.send_message(chat_id="1", text="b")
    assert a.message_id != b.message_id


@pytest.mark.asyncio
async def test_mock_provider_synthetic_failure_flag():
    p = MockTelegramProvider(should_fail=True)
    result = await p.send_message(chat_id="1", text="hi")
    assert result.ok is False
    assert result.error == "synthetic_failure"
    assert p.sent == []


def test_mock_provider_clear_resets_history():
    p = MockTelegramProvider()
    p._counter = 5  # noqa: SLF001 — internal but convenient for tests
    p._sent.append(TelegramMessage(chat_id="x", text="y"))  # noqa: SLF001
    p.clear()
    assert p.sent == []
    assert p._counter == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# Httpx provider
# ---------------------------------------------------------------------------
class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, status_code=200, payload=None, raise_exc=None):
        self.status_code = status_code
        self.payload = payload or {"ok": True, "result": {"message_id": 99}}
        self.raise_exc = raise_exc
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(
            self.status_code, content=json.dumps(self.payload).encode()
        )


def _make_provider(transport: httpx.AsyncBaseTransport) -> HttpxTelegramProvider:
    return HttpxTelegramProvider(
        bot_token="bot-secret",
        base_url="https://api.telegram.org",
        client=httpx.AsyncClient(transport=transport, timeout=5.0),
    )


def test_httpx_provider_requires_token():
    with pytest.raises(ValueError):
        HttpxTelegramProvider(bot_token="")


@pytest.mark.asyncio
async def test_httpx_provider_posts_to_send_message_with_token():
    transport = _MockTransport()
    provider = _make_provider(transport)
    result = await provider.send_message(chat_id="42", text="hello", link_preview=True)
    assert result.ok is True
    assert result.message_id == "99"
    assert result.provider == "telegram"

    request = transport.calls[0]
    # The token is part of the path, NOT the Authorization header.
    assert request.url.host == "api.telegram.org"
    assert "/botbot-secret/sendMessage" in str(request.url.path)
    assert "Authorization" not in request.headers

    body = json.loads(request.content)
    assert body["chat_id"] == "42"
    assert body["text"] == "hello"
    assert body["parse_mode"] == "MarkdownV2"
    # link_preview=True → disable_web_page_preview=False.
    assert body["disable_web_page_preview"] is False


@pytest.mark.asyncio
async def test_httpx_provider_disable_link_preview_by_default():
    transport = _MockTransport()
    provider = _make_provider(transport)
    await provider.send_message(chat_id="42", text="x")
    body = json.loads(transport.calls[0].content)
    assert body["disable_web_page_preview"] is True


@pytest.mark.asyncio
async def test_httpx_provider_translates_404():
    transport = _MockTransport(
        status_code=404,
        payload={"ok": False, "description": "chat not found"},
    )
    provider = _make_provider(transport)
    result = await provider.send_message(chat_id="x", text="x")
    assert result.ok is False
    assert "404" in (result.error or "")
    assert "chat not found" in (result.error or "")


@pytest.mark.asyncio
async def test_httpx_provider_translates_telegram_ok_false():
    transport = _MockTransport(
        status_code=200, payload={"ok": False, "description": "bad request"}
    )
    provider = _make_provider(transport)
    result = await provider.send_message(chat_id="x", text="x")
    assert result.ok is False
    assert result.error == "bad request"


@pytest.mark.asyncio
async def test_httpx_provider_translates_network_error():
    transport = _MockTransport(raise_exc=httpx.ConnectError("nope"))
    provider = _make_provider(transport)
    result = await provider.send_message(chat_id="x", text="x")
    assert result.ok is False
    assert "nope" in (result.error or "")


@pytest.mark.asyncio
async def test_httpx_provider_creates_and_closes_own_client(monkeypatch):
    captured: dict = {}

    class _RecordingClient:
        def __init__(self, *a, **kw):
            captured["ctor"] = True

        async def post(self, url, json):
            request = httpx.Request("POST", url, json=json)
            return httpx.Response(
                200,
                content=b'{"ok": true, "result": {"message_id": 7}}',
                request=request,
            )

        async def aclose(self):
            captured["closed"] = True

    import app.services.notification.httpx_telegram as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _RecordingClient)
    provider = HttpxTelegramProvider(
        bot_token="bot-secret", base_url="https://api.telegram.org"
    )
    result = await provider.send_message(chat_id="x", text="hi")
    assert result.ok is True
    assert result.message_id == "7"
    assert captured.get("ctor") is True
    assert captured.get("closed") is True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_factory_returns_mock_when_mocking_enabled(settings):
    settings.mock_external_services = True
    p = build_telegram_provider(settings)
    assert isinstance(p, MockTelegramProvider)


def test_factory_returns_mock_when_no_token(settings):
    settings.mock_external_services = False
    settings.telegram_bot_token = ""
    p = build_telegram_provider(settings)
    assert isinstance(p, MockTelegramProvider)


def test_factory_returns_mock_when_prefer_mock(settings):
    settings.mock_external_services = False
    settings.telegram_bot_token = "real-token"
    p = build_telegram_provider(settings, prefer="mock")
    assert isinstance(p, MockTelegramProvider)


def test_factory_returns_httpx_when_token_present(settings):
    settings.mock_external_services = False
    settings.telegram_bot_token = "real-token"
    p = build_telegram_provider(settings)
    assert isinstance(p, HttpxTelegramProvider)
