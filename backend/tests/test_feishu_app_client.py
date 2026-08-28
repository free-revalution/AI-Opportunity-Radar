"""Tests for FeishuAppClient (Phase 6 v2.0 outbound App API client).

Verifies:
  * Token caching — first call hits /auth/v3/tenant_access_token,
    subsequent calls reuse the cached token until expiry.
  * /im/v1/messages request shape — proper headers, body field
    name `content` (as a stringified JSON), and receive_id_type.
  * Error translation — non-zero `code` raises FeishuAppError.
  * is_configured reflects whether App credentials are present.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.services.feishu.app_client import (
    FeishuAppClient,
    FeishuAppError,
)


def _make_settings(app_id: str = "cli_test", app_secret: str = "secret_test") -> Settings:
    """Build a Settings object with Feishu App fields populated.

    We use the live get_settings() and patch the attributes so other
    code reading from settings (incl. settings.feishu_app_id) sees
    the test values.
    """
    from app.config import get_settings

    s = get_settings()
    s.feishu_app_id = app_id
    s.feishu_app_secret = app_secret
    return s


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Captures every outbound request and replies with a canned Feishu API."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        # — handler signature matches handle_async_request
        self._token_calls = 0
        self._message_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            self._token_calls += 1
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tok-1", "expire": 7200},
                request=request,
            )
        if request.url.path.endswith("/im/v1/messages"):
            self._message_calls += 1
            return httpx.Response(
                200,
                json={"code": 0, "data": {"message_id": f"om_{self._message_calls}"}},
                request=request,
            )
        return httpx.Response(404, json={"code": 999, "msg": "not found"}, request=request)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def test_is_configured_reflects_credentials() -> None:
    s = _make_settings(app_id="cli_x", app_secret="sec_x")
    c = FeishuAppClient(settings=s, http_client=httpx.AsyncClient())
    assert c.is_configured is True


def test_is_configured_false_when_app_id_missing() -> None:
    s = _make_settings(app_id="", app_secret="sec_x")
    c = FeishuAppClient(settings=s, http_client=httpx.AsyncClient())
    assert c.is_configured is False


def test_is_configured_false_when_app_secret_missing() -> None:
    s = _make_settings(app_id="cli_x", app_secret="")
    c = FeishuAppClient(settings=s, http_client=httpx.AsyncClient())
    assert c.is_configured is False


# ---------------------------------------------------------------------------
# Token caching
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_message_fetches_token_on_first_call() -> None:
    s = _make_settings()
    transport = _RecordingTransport()
    http = httpx.AsyncClient(transport=transport)
    client = FeishuAppClient(settings=s, http_client=http)
    try:
        await client.send_message(
            chat_id="oc_chat",
            msg_type="interactive",
            content={"elements": []},
        )
        assert transport._token_calls == 1
        assert transport._message_calls == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_send_message_reuses_cached_token() -> None:
    s = _make_settings()
    transport = _RecordingTransport()
    http = httpx.AsyncClient(transport=transport)
    client = FeishuAppClient(settings=s, http_client=http)
    try:
        await client.send_message(
            chat_id="oc_1", msg_type="text", content={"text": "a"}
        )
        await client.send_message(
            chat_id="oc_2", msg_type="text", content={"text": "b"}
        )
        await client.send_message(
            chat_id="oc_3", msg_type="text", content={"text": "c"}
        )
        assert transport._token_calls == 1
        assert transport._message_calls == 3
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# /im/v1/messages request shape
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_message_request_has_bearer_token_and_stringified_content() -> None:
    s = _make_settings()
    transport = _RecordingTransport()
    http = httpx.AsyncClient(transport=transport)
    client = FeishuAppClient(settings=s, http_client=http)
    try:
        card: dict[str, Any] = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "hi"}}],
        }
        await client.send_message(
            chat_id="oc_target", msg_type="interactive", content=card
        )
        msg_request = transport.calls[-1]
        assert msg_request.url.path.endswith("/im/v1/messages")
        assert msg_request.url.params["receive_id_type"] == "chat_id"
        assert msg_request.headers["Authorization"] == "Bearer tok-1"
        body = json.loads(msg_request.content.decode("utf-8"))
        assert body["receive_id"] == "oc_target"
        assert body["msg_type"] == "interactive"
        # — content must be a stringified JSON, not a nested object
        assert isinstance(body["content"], str)
        parsed_content = json.loads(body["content"])
        assert parsed_content == card
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_message_raises_on_feishu_rejection() -> None:
    s = _make_settings()

    class _RejectingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
                return httpx.Response(
                    200,
                    json={"code": 0, "tenant_access_token": "tok-x", "expire": 7200},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"code": 230001, "msg": "robot disabled"},
                request=request,
            )

    http = httpx.AsyncClient(transport=_RejectingTransport())
    client = FeishuAppClient(settings=s, http_client=http)
    try:
        with pytest.raises(FeishuAppError, match="robot disabled"):
            await client.send_message(
                chat_id="oc_chat",
                msg_type="text",
                content={"text": "hi"},
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_send_message_raises_when_not_configured() -> None:
    s = _make_settings(app_id="", app_secret="")
    client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient())
    with pytest.raises(FeishuAppError, match="not configured"):
        await client.send_message(
            chat_id="oc_chat",
            msg_type="text",
            content={"text": "hi"},
        )


@pytest.mark.asyncio
async def test_send_message_raises_on_empty_chat_id() -> None:
    s = _make_settings()
    client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient())
    with pytest.raises(FeishuAppError, match="chat_id is empty"):
        await client.send_message(
            chat_id="",
            msg_type="text",
            content={"text": "hi"},
        )