"""Tests for Phase 6 Feishu inbound event handling.

Covers:

  * `verify_event` — accepts unsigned bodies in dev (token empty),
    rejects mismatched tokens, accepts matching tokens.
  * `parse_event` — URL verification handshake, unencrypted message
    events, encrypted event stub.
  * `FeishuCommandRouter` — 8 commands (`/help`, `/today`, `/top`,
    `/research`, `/refresh`, `/score`, `/daily`, unknown text).
    Each routes to its target endpoint via mocked httpx transport.
  * `/api/feishu/event` FastAPI endpoint — challenge handshake,
    signature rejection, command routing happy path, non-command
    ack.

The router uses `httpx.AsyncClient` to call internal APIs. We mock
that transport in-process via `httpx.MockTransport` so the tests
run fully offline.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.feishu.inbound import (
    BotCommand,
    CommandReply,
    FeishuCommandRouter,
    FeishuEvent,
    parse_command,
    parse_event,
    verify_event,
)


# ---------------------------------------------------------------------------
# verify_event
# ---------------------------------------------------------------------------
def test_verify_event_accepts_when_token_empty():
    """Dev / mock mode: no token configured → accept everything."""
    settings = get_settings()
    settings.feishu_verification_token = ""
    assert verify_event(headers={}, body={"token": "anything"}, settings=settings) is True
    assert verify_event(headers={}, body={}, settings=settings) is True


def test_verify_event_rejects_handshake_when_token_mismatch():
    """URL verification handshake carries the token; if it mismatches,
    reject (this is the only place Feishu sends a `token` field)."""
    settings = get_settings()
    settings.feishu_verification_token = "secret-abc"
    assert (
        verify_event(
            headers={},
            body={"challenge": "x", "token": "wrong"},
            settings=settings,
        )
        is False
    )


def test_verify_event_accepts_handshake_when_token_matches():
    settings = get_settings()
    settings.feishu_verification_token = "secret-abc"
    assert (
        verify_event(
            headers={},
            body={"challenge": "x", "token": "secret-abc"},
            settings=settings,
        )
        is True
    )


def test_verify_event_rejects_handshake_when_token_missing():
    """Handshake body without a `token` is rejected when a token is
    configured — protects against a misconfigured Feishu open platform."""
    settings = get_settings()
    settings.feishu_verification_token = "secret-abc"
    assert (
        verify_event(headers={}, body={"challenge": "x"}, settings=settings) is False
    )


def test_verify_event_trusts_real_event_when_handshake_already_done():
    """Phase 6 fix: real `im.message.receive_v1` events do NOT carry
    a `token` field — Feishu only sends the token during the initial
    URL verification handshake. After that, we trust that the handshake
    succeeded (and ngrok + Feishu's HTTPS provide transport security).

    Regression test for the bug where production events were rejected
    with 401 because the code expected `body.token` on every event.
    """
    settings = get_settings()
    settings.feishu_verification_token = "secret-abc"
    real_event = {
        "header": {"event_type": "im.message.receive_v1", "tenant_key": "t"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "/help"}),
            },
        },
    }
    assert verify_event(headers={}, body=real_event, settings=settings) is True


# ---------------------------------------------------------------------------
# parse_event
# ---------------------------------------------------------------------------
def test_parse_event_handles_url_verification():
    parsed = parse_event({"challenge": "abc-123"})
    assert parsed == {"challenge": "abc-123"}


def test_parse_event_returns_none_for_encrypted_payload():
    """Encrypted events are a Phase 6.x stub — we ack + return None."""
    parsed = parse_event({"encrypt": "base64-blob-here"})
    assert parsed is None


def test_parse_event_returns_none_for_unknown_event_type():
    parsed = parse_event({"header": {"event_type": "something.else"}})
    assert parsed is None


def test_parse_event_decodes_unencrypted_message():
    body = {
        "header": {"event_type": "im.message.receive_v1", "tenant_key": "t1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "/today"}),
            },
        },
    }
    parsed = parse_event(body)
    assert isinstance(parsed, FeishuEvent)
    assert parsed.event_type == "im.message.receive_v1"
    assert parsed.tenant_key == "t1"
    assert parsed.sender_open_id == "ou_user"
    assert parsed.chat_id == "oc_chat"
    assert parsed.chat_type == "group"
    assert parsed.text == "/today"
    assert parsed.is_command is True
    assert parsed.command == "/today"
    assert parsed.command_args == ""


def test_parse_event_command_args_after_token():
    body = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "/research AI 法律合同"}),
            },
        },
    }
    parsed = parse_event(body)
    assert isinstance(parsed, FeishuEvent)
    assert parsed.command == "/research"
    assert parsed.command_args == "AI 法律合同"


def test_parse_event_strips_mention_prefix_and_recognises_command():
    """Regression test: Feishu prefixes @-mentioned group messages with
    `@_user_1`. parse_event must strip that prefix so downstream
    `is_command` returns True and `parse_command` routes correctly.

    Without this fix, group messages were logged as
    `feishu_event_non_command` and silently ignored.
    """
    body = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 /daily"}),
            },
        },
    }
    parsed = parse_event(body)
    assert isinstance(parsed, FeishuEvent)
    assert parsed.is_command is True
    assert parsed.text == "/daily"
    assert parsed.raw_text == "@_user_1 /daily"
    assert parsed.command == "/daily"
    assert parsed.command_args == ""


def test_parse_event_strips_multi_word_display_name_mention():
    """@Display Name With Spaces + command should also resolve."""
    body = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps(
                    {"text": "@AI Opportunity Radar /research AI 法律合同"}
                ),
            },
        },
    }
    parsed = parse_event(body)
    assert isinstance(parsed, FeishuEvent)
    assert parsed.is_command is True
    assert parsed.command == "/research"
    assert parsed.command_args == "AI 法律合同"


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------
def test_parse_command_recognises_english_aliases():
    assert parse_command("/help").kind == "help"
    assert parse_command("/today").kind == "today"
    assert parse_command("/top").kind == "top"
    assert parse_command("/research AI").kind == "research"
    assert parse_command("/refresh").kind == "refresh"
    assert parse_command("/score").kind == "score"
    assert parse_command("/daily").kind == "daily"


def test_parse_command_recognises_chinese_aliases():
    assert parse_command("/今日").kind == "today"
    assert parse_command("/分析 X").kind == "research"
    assert parse_command("/刷新").kind == "refresh"
    assert parse_command("/重评").kind == "score"
    assert parse_command("/日报").kind == "daily"


def test_parse_command_unknown_kind():
    assert parse_command("/foo bar").kind == "unknown"
    assert parse_command("/foo bar").args == "/foo bar"


def test_parse_command_non_command_text():
    assert parse_command("hello world").kind == "unknown"


def test_parse_command_assumes_already_cleaned_text():
    """parse_command now operates on mention-stripped text — mention
    stripping moved to parse_event / _strip_mentions in the
    parse_command refactor. When a raw mention-prefixed text reaches
    parse_command (e.g. unit-test direct invocation), it is
    classified as 'unknown' — that's by design; the production
    path always strips first.

    See `test_parse_event_strips_mention_prefix_and_recognises_command`
    for the end-to-end regression test.
    """
    # — Direct invocation without prior stripping → unknown.
    assert parse_command("@_user_1 /help").kind == "unknown"
    # — But the cleaned form is what parse_command expects:
    assert parse_command("/help").kind == "help"


# ---------------------------------------------------------------------------
# FeishuCommandRouter — mocked httpx transport
# ---------------------------------------------------------------------------
def _make_router(handler: callable) -> FeishuCommandRouter:
    """Build a router whose `httpx.AsyncClient` uses an in-memory
    `MockTransport`. All `client.get/post` calls land in `handler`.
    """
    settings = get_settings()
    settings.app_base_url = "http://radar.test"
    transport = httpx.MockTransport(handler)
    # — httpx requires either a base URL or a fully-qualified URL per
    # request when a transport is provided; reuse the router's base.
    client = httpx.AsyncClient(
        transport=transport, base_url="http://radar.test"
    )
    return FeishuCommandRouter(settings=settings, http_client=client)


def _opportunities_handler(request: httpx.Request) -> httpx.Response:
    """Default handler: returns 2 fake opportunities for `/api/opportunities`."""
    if "/api/opportunities" in request.url.path:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": 1, "title": "AI Coach", "total_score": 88.0, "slug": "ai-coach"},
                    {"id": 2, "title": "Legal AI", "total_score": 75.0, "slug": "legal-ai"},
                ],
                "total": 2,
            },
        )
    return httpx.Response(200, json={"job_id": "job-99"})


async def test_router_help_returns_menu():
    router = _make_router(_opportunities_handler)
    reply = await router.route(BotCommand(kind="help"))
    assert "/help" in reply.text
    assert "/research" in reply.text
    assert reply.card is None


async def test_router_today_lists_opportunities():
    router = _make_router(_opportunities_handler)
    reply = await router.route(BotCommand(kind="today", args=""))
    assert "AI Coach" in reply.text
    assert "Legal AI" in reply.text
    assert reply.metadata["items_count"] == 2


async def test_router_today_handles_empty_database():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="today", args=""))
    assert "暂无" in reply.text or "没有" in reply.text


async def test_router_top_uses_opportunities_endpoint():
    router = _make_router(_opportunities_handler)
    reply = await router.route(BotCommand(kind="top", args=""))
    assert "AI Coach" in reply.text


async def test_router_research_requires_topic():
    router = _make_router(_opportunities_handler)
    reply = await router.route(BotCommand(kind="research", args=""))
    assert "用法" in reply.text or "例如" in reply.text


async def test_router_research_calls_on_demand_endpoint():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if "/research/on_demand" in request.url.path:
            body = json.loads(request.content)
            return httpx.Response(200, json={"job_id": "job-42", "topic": body["topic"]})
        return httpx.Response(200, json={})

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="research", args="AI 法律合同"))
    assert "job-42" in reply.text
    assert any("/research/on_demand" in r.url.path for r in captured)
    # — the topic payload reaches the endpoint verbatim.
    on_demand = next(r for r in captured if "/research/on_demand" in r.url.path)
    payload = json.loads(on_demand.content)
    assert payload["topic"] == "AI 法律合同"


async def test_router_refresh_triggers_discovery():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        return httpx.Response(200, json={"status": "queued"})

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="refresh"))
    assert "抓取" in reply.text or "触发" in reply.text
    assert any("/discovery/run" in p for p in captured)


async def test_router_score_triggers_scoring():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        return httpx.Response(200, json={"status": "queued"})

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="score"))
    assert "评分" in reply.text or "触发" in reply.text
    assert any("/scoring/run" in p for p in captured)


async def test_router_daily_triggers_digest():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        return httpx.Response(200, json={"status": "queued"})

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="daily"))
    assert "日报" in reply.text or "推送" in reply.text
    assert any("/notifications/digest/send" in p for p in captured)


async def test_router_unknown_kind_returns_help_hint():
    router = _make_router(_opportunities_handler)
    reply = await router.route(BotCommand(kind="unknown", args="/foo bar"))
    assert "不理解" in reply.text or "/help" in reply.text


# ---------------------------------------------------------------------------
# FastAPI endpoint integration
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Reset `get_settings()` lru_cache between tests so mutations to
    `feishu_verification_token` don't leak across tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def feishu_client(monkeypatch):
    """A FastAPI TestClient with internal-api + Feishu App API calls stubbed."""
    app = create_app()

    sent_app_messages: list[dict[str, object]] = []

    class _StubAppClient:
        """Captures outbound Feishu App API calls without hitting the network."""

        is_configured = True

        async def send_message(self, *, chat_id, msg_type, content):
            sent_app_messages.append(
                {"chat_id": chat_id, "msg_type": msg_type, "content": content}
            )
            return {"code": 0, "msg": "ok", "data": {"message_id": "om_stub"}}

        async def aclose(self):
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/opportunities" in request.url.path:
            return httpx.Response(
                200,
                json={"items": [{"id": 1, "title": "Stub", "total_score": 80.0, "slug": "stub"}]},
            )
        return httpx.Response(200, json={"job_id": "stub-job"})

    transport = httpx.MockTransport(handler)
    base_client = httpx.AsyncClient(transport=transport, base_url="http://radar.test")

    # — Inject the mocked http_client into FeishuCommandRouter.
    real_init = FeishuCommandRouter.__init__

    def patched_init(self, **kwargs):
        kwargs.setdefault("http_client", base_client)
        real_init(self, **kwargs)

    monkeypatch.setattr(
        "app.services.feishu.inbound.FeishuCommandRouter.__init__",
        patched_init,
    )
    # — Inject the stub FeishuAppClient so the endpoint actually sends.
    monkeypatch.setattr(
        "app.api.feishu_inbound.FeishuAppClient",
        lambda **kwargs: _StubAppClient(),
    )

    test_client = TestClient(app)
    # — Expose the capture list on the *module* (TestClient objects
    # don't allow dynamic attributes) so individual tests can inspect
    # what was sent via `request.node.module._sent_app_messages`.
    test_client._sent_app_messages = sent_app_messages  # type: ignore[attr-defined]
    return test_client


def test_feishu_endpoint_handles_url_verification_challenge(feishu_client):
    response = feishu_client.post(
        "/api/feishu/event",
        json={"challenge": "test-challenge-xyz"},
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "test-challenge-xyz"}


def test_feishu_endpoint_rejects_bad_signature(feishu_client):
    """When `feishu_verification_token` is set, wrong token → 401."""
    get_settings.cache_clear()
    settings = get_settings()
    original = settings.feishu_verification_token
    settings.feishu_verification_token = "expected-token"
    try:
        response = feishu_client.post(
            "/api/feishu/event",
            json={"token": "wrong-token", "challenge": "abc"},
        )
        # — Wrong token + URL verification → still signature-failed first
        # (URL verification doesn't include `token`; in practice Feishu
        # sends both, so without `token` we fall into the signature path).
        assert response.status_code == 401
    finally:
        settings.feishu_verification_token = original
        get_settings.cache_clear()


def test_feishu_endpoint_routes_today_command(feishu_client):
    response = feishu_client.post(
        "/api/feishu/event",
        json={
            "header": {"event_type": "im.message.receive_v1", "tenant_key": "t1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_user"}},
                "message": {
                    "chat_id": "oc_chat",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": "/today"}),
                },
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"code": 0, "msg": "ok"}
    # — Endpoint must have actively sent the reply via Feishu App API
    # (not echoed in the response body — Feishu callbacks don't work that way).
    sent = feishu_client._sent_app_messages  # type: ignore[attr-defined]
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "oc_chat"
    assert sent[0]["msg_type"] == "interactive"
    assert "elements" in sent[0]["content"]


def test_feishu_endpoint_acks_non_command_text(feishu_client):
    response = feishu_client.post(
        "/api/feishu/event",
        json={
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_user"}},
                "message": {
                    "chat_id": "oc_chat",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": "今天天气不错"}),
                },
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    # — No reply payload for non-commands (Feishu doesn't retry).
    assert payload == {"code": 0, "msg": "ok"}


def test_feishu_endpoint_ignores_encrypted_payload(feishu_client):
    response = feishu_client.post(
        "/api/feishu/event",
        json={"encrypt": "base64-payload"},
    )
    assert response.status_code == 200
    assert response.json() == {}  # acked, no data


def test_feishu_endpoint_rejects_invalid_json(feishu_client):
    response = feishu_client.post(
        "/api/feishu/event",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400