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
from app.db import get_session as _get_session_dep
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
    # MVP surface only (simplify §10): /help /today /run /status /sources.
    assert parse_command("/help").kind == "help"
    assert parse_command("/today").kind == "today"
    assert parse_command("/run").kind == "run"
    assert parse_command("/status").kind == "status"
    assert parse_command("/sources").kind == "sources"


def test_parse_command_recognises_chinese_aliases():
    # MVP surface only.
    assert parse_command("/今日").kind == "today"
    assert parse_command("/运行").kind == "run"
    assert parse_command("/状态").kind == "status"
    assert parse_command("/源").kind == "sources"


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
@pytest.fixture(autouse=True)
def _stub_paywall(monkeypatch):
    """Phase 15C — `route()` now opens a DB session for the paywall check.

    These tests use httpx.MockTransport only (no real DB). Stub the
    paywall check at the module boundary so it returns an "allowed"
    verdict without touching the DB. Each test that *does* want to
    exercise the paywall (e.g. test_paywall.py) wires its own
    sessionmaker via the `client` fixture.
    """
    from app.services.feishu import inbound as inbound_module

    async def _noop_paywall(**_kwargs):
        from app.services.paywall import PaywallVerdict

        return PaywallVerdict(
            allowed=True,
            plan="unknown",
            quota_type="bypass",
            quota_limit=0,
            quota_used=0,
        )

    monkeypatch.setattr(
        inbound_module, "_paywall_check", _noop_paywall
    )


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
    """MVP: /help must show the 5 kept commands, not the legacy FREEZE menu."""
    router = _make_router(_opportunities_handler)
    reply = await router.route(BotCommand(kind="help"))
    # MVP commands surfaced in the menu
    assert "/help" in reply.text
    assert "/today" in reply.text
    assert "/run" in reply.text
    assert "/status" in reply.text
    assert "/sources" in reply.text
    # FREEZE-era commands must not be advertised
    assert "/research" not in reply.text
    assert "/content" not in reply.text
    assert "/activate" not in reply.text
    assert "/preferences" not in reply.text
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
    # FREEZE — /top dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


async def test_router_research_requires_topic():
    # FREEZE — /research dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


async def test_router_research_calls_on_demand_endpoint():
    # FREEZE — /research dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


async def test_router_refresh_triggers_discovery():
    # FREEZE — /refresh dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


async def test_router_score_triggers_scoring():
    # FREEZE — /score dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


async def test_router_daily_triggers_digest():
    # FREEZE — /daily dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


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
def feishu_client(monkeypatch, sqlite_engine):
    """A FastAPI TestClient with internal-api + Feishu App API calls stubbed.

    Phase 24 — also overrides the ``get_session`` dep with the SQLite
    engine so the pre-send compliance gate (called inside the handler)
    can persist AuditLog rows without reaching the production Postgres.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    app = create_app()

    sessionmaker = async_sessionmaker(sqlite_engine, expire_on_commit=False)

    async def _override_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[_get_session_dep] = _override_session

    sent_app_messages: list[dict[str, object]] = []

    class _StubAppClient:
        """Captures outbound Feishu App API calls without hitting the network."""

        is_configured = True

        # Phase 23A — `send_message` renamed `chat_id` → `receive_id`
        # and gained `receive_id_type`. Default `chat_id` keeps the
        # stub in sync with the production caller.
        async def send_message(
            self,
            *,
            receive_id,
            msg_type,
            content,
            receive_id_type="chat_id",
            session=None,  # Phase 24 — pre-send gate
            compliance_context="feishu_outbound",  # Phase 24
        ):
            sent_app_messages.append(
                {
                    "receive_id": receive_id,
                    "receive_id_type": receive_id_type,
                    "msg_type": msg_type,
                    "content": content,
                }
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
    # Phase 23A — `receive_id` (not `chat_id`) is the new keyword +
    # stored key. The endpoint explicitly passes receive_id_type=
    # "chat_id" for the inbound chat-reply path so the assertion
    # is symmetric with the production call.
    assert sent[0]["receive_id"] == "oc_chat"
    assert sent[0]["receive_id_type"] == "chat_id"
    assert sent[0]["msg_type"] == "interactive"
    assert "elements" in sent[0]["content"]


def test_feishu_endpoint_send_message_survives_router_aclose(feishu_client):
    """Regression: Phase 7 added Drive/Bitable sibling clients that
    share the endpoint's `FeishuAppClient._http`. The `aclose()` must
    happen AFTER `send_message()`, not in a `finally` that fires
    before the reply is delivered — otherwise the chat reply silently
    fails with `Cannot send a request, as the client has been closed`.

    Triggered by the bug found while testing `/report` / `/table`
    end-to-end: the router's Drive/Bitable siblings hit `app_client._http`
    during `route()`, then the old `finally: aclose()` ran before the
    explicit `send_message`, closing the client mid-request.
    """
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
                    "content": json.dumps({"text": "/today"}),
                },
            },
        },
    )
    # — Must be 200, not 500. The bug surfaced as `Internal Server Error`.
    assert response.status_code == 200
    sent = feishu_client._sent_app_messages  # type: ignore[attr-defined]
    assert len(sent) == 1, "send_message should have run after route()"


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


# ---------------------------------------------------------------------------
# Phase 16E — distinct-signal SADD behaviour for /today / /top
# ---------------------------------------------------------------------------
async def test_router_today_sadds_distinct_ids(fake_redis):
    """`/today` writes the shown signal IDs into a per-day Redis SET
    so a second `/today` (or `/top`) does not re-bill the same signal.
    FREEZE — paywall distinct-id tracking removed in MVP (simplify §6).
    """
    pytest.skip("FREEZE quota tracking removed in MVP")


async def test_router_top_sadds_distinct_ids(fake_redis):
    # FREEZE — see test_router_today_sadds_distinct_ids.
    pytest.skip("FREEZE quota tracking removed in MVP")


async def test_router_today_today_dedupes_across_calls(fake_redis):
    """Same `/today` called twice → SCARD stays at 2 (not 4) because
    SADD is idempotent for already-present IDs.
    FREEZE — see test_router_today_sadds_distinct_ids.
    """
    pytest.skip("FREEZE quota tracking removed in MVP")


async def test_router_today_quota_exhausted_returns_deny(monkeypatch):
    """FREEZE — paywall quota-exhaustion denial removed in MVP
    (simplify §6). All MVP commands return success.
    """
    pytest.skip("FREEZE quota tracking removed in MVP")


async def test_router_search_alias_recognised():
    # FREEZE — /search dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


async def test_router_content_alias_recognised():
    # FREEZE — /content dispatcher removed in MVP (simplify §10).
    pytest.skip("FREEZE dispatcher removed in MVP")


async def test_router_help_includes_content():
    """MVP: /help must show only the 5 MVP commands (no FREEZE content/search)."""
    router = _make_router(_opportunities_handler)
    reply = await router.route(BotCommand(kind="help"))
    # FREEZE-era commands must not be advertised
    assert "/content" not in reply.text
    assert "/search" not in reply.text
    # The "Phase 16" placeholder must be gone.
    assert "Phase 16" not in reply.text