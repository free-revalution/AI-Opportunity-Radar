"""Phase 25 v2.1 — Feishu outbound retry + webhook fallback tests.

Covers:
  * 5xx on /im/v1/messages → retried (tenacity backs off)
  * network error → retried
  * 4xx / business `code != 0` → NOT retried (raised immediately)
  * 99991663 / 99991664 token-expired path → still works (refresh + retry)
  * webhook fallback helper accepts a 200 + StatusCode=0 payload
  * webhook fallback rejects a 200 + StatusCode != 0 payload
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
    _RetryableIMHTTPError,
)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------
def _settings(app_id: str = "cli_retry", app_secret: str = "sec_retry") -> Settings:
    from app.config import get_settings

    s = get_settings()
    s.feishu_app_id = app_id
    s.feishu_app_secret = app_secret
    # Phase 25 v2.1 — outbound retry behaviour is independent of the
    # pre-send compliance gate; turn the gate off in tests so the
    # focus stays on retry mechanics.
    s.compliance_pre_send_gate_enabled = False
    return s


# ---------------------------------------------------------------------------
# Recording transport with programmable status codes
# ---------------------------------------------------------------------------
class _ProgrammableTransport(httpx.AsyncBaseTransport):
    """Each request pops the next response from the queue."""

    def __init__(self, *, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        # — token request always succeeds.
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tok-1", "expire": 7200},
                request=request,
            )
        if not self._responses:
            return httpx.Response(
                500,
                json={"code": 999, "msg": "exhausted"},
                request=request,
            )
        resp = self._responses.pop(0)
        # — propagate request object so httpx.Response is well-formed.
        return httpx.Response(
            resp.status_code,
            content=resp.content,
            headers=resp.headers,
            request=request,
        )


def _ok(message_id: str = "om_1") -> httpx.Response:
    import json

    return httpx.Response(
        200,
        json={"code": 0, "data": {"message_id": message_id}},
    )


def _http_5xx(status_code: int = 503) -> httpx.Response:
    import json

    return httpx.Response(status_code, json={"code": 999, "msg": "boom"})


def _business_error(code: int = 230002) -> httpx.Response:
    import json

    return httpx.Response(200, json={"code": code, "msg": "perm denied"})


# ---------------------------------------------------------------------------
# Retry behaviour — 5xx
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_5xx_is_retried_until_success() -> None:
    transport = _ProgrammableTransport(
        responses=[_http_5xx(503), _http_5xx(503), _ok("om_final")]
    )
    client = FeishuAppClient(
        settings=_settings(), http_client=httpx.AsyncClient(transport=transport)
    )
    try:
        data = await client.send_message(
            receive_id="oc_1",
            msg_type="text",
            content={"text": "hello"},
        )
    finally:
        await client.aclose()
    assert data["code"] == 0
    assert data["data"]["message_id"] == "om_final"
    # — three IM attempts (2 × 5xx + 1 × 200).
    im_calls = [c for c in transport.calls if c.url.path.endswith("/im/v1/messages")]
    assert len(im_calls) == 3


@pytest.mark.asyncio
async def test_5xx_exhausts_retries_raises() -> None:
    transport = _ProgrammableTransport(
        responses=[_http_5xx(500), _http_5xx(502), _http_5xx(503)]
    )
    client = FeishuAppClient(
        settings=_settings(), http_client=httpx.AsyncClient(transport=transport)
    )
    with pytest.raises(FeishuAppError, match="retries exhausted"):
        await client.send_message(
            receive_id="oc_1",
            msg_type="text",
            content={"text": "hello"},
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_4xx_is_not_retried() -> None:
    """Client-side 4xx (not 5xx) must NOT be retried — they are non-transient."""
    transport = _ProgrammableTransport(
        responses=[httpx.Response(404, json={"code": 999, "msg": "no path"})]
    )
    client = FeishuAppClient(
        settings=_settings(), http_client=httpx.AsyncClient(transport=transport)
    )
    with pytest.raises(FeishuAppError, match="HTTP 404"):
        await client.send_message(
            receive_id="oc_1",
            msg_type="text",
            content={"text": "hello"},
        )
    # — Only one IM attempt (no retry on 4xx).
    im_calls = [c for c in transport.calls if c.url.path.endswith("/im/v1/messages")]
    assert len(im_calls) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_business_code_not_retried() -> None:
    """`code != 0` business errors must NOT be retried (e.g. permission)."""
    transport = _ProgrammableTransport(
        responses=[_business_error(230002), _ok("om_x")]
    )
    client = FeishuAppClient(
        settings=_settings(), http_client=httpx.AsyncClient(transport=transport)
    )
    with pytest.raises(FeishuAppError, match="rejected"):
        await client.send_message(
            receive_id="oc_1",
            msg_type="text",
            content={"text": "hello"},
        )
    im_calls = [c for c in transport.calls if c.url.path.endswith("/im/v1/messages")]
    assert len(im_calls) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_token_expired_single_retry_still_works() -> None:
    """99991663/99991664 path keeps the original single-shot refresh."""
    responses = [
        httpx.Response(200, json={"code": 99991663, "msg": "token expired"}),
        httpx.Response(200, json={"code": 0, "data": {"message_id": "om_refresh"}}),
    ]
    transport = _ProgrammableTransport(responses=responses)
    client = FeishuAppClient(
        settings=_settings(), http_client=httpx.AsyncClient(transport=transport)
    )
    data = await client.send_message(
        receive_id="oc_1",
        msg_type="text",
        content={"text": "hi"},
    )
    assert data["code"] == 0
    await client.aclose()


# ---------------------------------------------------------------------------
# Network errors
# ---------------------------------------------------------------------------
class _RaisingTransport(httpx.AsyncBaseTransport):
    """Always raises httpx.ConnectError — exercises tenacity's HTTPError retry."""

    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tok-1", "expire": 7200},
                request=request,
            )
        self.calls += 1
        raise httpx.ConnectError("network down")


@pytest.mark.asyncio
async def test_network_error_is_retried_then_raises() -> None:
    transport = _RaisingTransport()
    client = FeishuAppClient(
        settings=_settings(), http_client=httpx.AsyncClient(transport=transport)
    )
    with pytest.raises(FeishuAppError, match="retries exhausted"):
        await client.send_message(
            receive_id="oc_1",
            msg_type="text",
            content={"text": "hello"},
        )
    # — tenacity should have called multiple times (3 attempts).
    assert transport.calls >= 2
    await client.aclose()


# ---------------------------------------------------------------------------
# Webhook fallback helper
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_webhook_fallback_success(monkeypatch) -> None:
    """Recording transport accepts the webhook POST and returns StatusCode=0."""
    from app.api import feishu_inbound

    captured: dict[str, Any] = {}

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"StatusCode": 0, "msg": "ok"})

    transport = _Transport()
    settings = _settings()
    settings.feishu_webhook_url = "https://example.com/webhook"

    # — Monkeypatch the global httpx.AsyncClient so the helper's
    # `async with httpx.AsyncClient(...)` block uses our transport.
    # We DO NOT route through `feishu_inbound.httpx` because that
    # attribute IS the same `httpx` module — patching it from inside
    # the lambda would recurse forever.
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    from app.services.feishu.inbound import FeishuEvent

    event = FeishuEvent(
        event_type="im.message.receive_v1",
        tenant_key="t",
        sender_open_id="ou_x",
        chat_id="oc_x",
        chat_type="p2p",
        message_type="text",
        text="/help",
    )
    ok = await feishu_inbound._webhook_fallback(
        settings=settings, text="hello", event=event
    )
    assert ok is True
    assert captured["url"] == "https://example.com/webhook"
    assert captured["body"]["msg_type"] == "text"
    assert captured["body"]["content"]["text"] == "hello"


@pytest.mark.asyncio
async def test_webhook_fallback_rejects_when_status_error(monkeypatch) -> None:
    """A 200 with StatusCode != 0 means Feishu rejected the broadcast."""
    from app.api import feishu_inbound

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"StatusCode": 999, "msg": "bad"})

    transport = _Transport()
    settings = _settings()
    settings.feishu_webhook_url = "https://example.com/webhook"

    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    from app.services.feishu.inbound import FeishuEvent

    event = FeishuEvent(
        event_type="im.message.receive_v1",
        tenant_key="t",
        sender_open_id="ou_x",
        chat_id="oc_x",
        chat_type="p2p",
        message_type="text",
        text="/help",
    )
    ok = await feishu_inbound._webhook_fallback(
        settings=settings, text="hello", event=event
    )
    assert ok is False


@pytest.mark.asyncio
async def test_webhook_fallback_no_url_returns_false() -> None:
    from app.api import feishu_inbound
    from app.services.feishu.inbound import FeishuEvent

    settings = _settings()
    settings.feishu_webhook_url = ""
    event = FeishuEvent(
        event_type="im.message.receive_v1",
        tenant_key="t",
        sender_open_id="ou_x",
        chat_id="oc_x",
        chat_type="p2p",
        message_type="text",
        text="/help",
    )
    assert await feishu_inbound._webhook_fallback(settings=settings, text="x", event=event) is False


# ---------------------------------------------------------------------------
# _RetryableIMHTTPError marker sanity
# ---------------------------------------------------------------------------
def test_retryable_marker_is_httpx_error() -> None:
    exc = _RetryableIMHTTPError("boom", status_code=503)
    assert exc.status_code == 503
    assert isinstance(exc, httpx.HTTPError)
