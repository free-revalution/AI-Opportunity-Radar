"""Tests for the v2.0 Feishu (Lark) custom-robot integration.

Layout:

  * Signature helper
  * Formatter (opportunities → interactive card)
  * Mock provider (records sends)
  * Httpx provider (real transport, with `_MockTransport`)
  * Bot orchestrator (integration with content_generator)
  * `/api/internal/feishu/digest/send` endpoint

We don't actually call Feishu — the provider is exercised against a
mock httpx transport that emulates the Feishu open-api contract.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.models import Opportunity
from app.services.feishu import (
    FeishuBot,
    FeishuCard,
    HttpxFeishuProvider,
    MockFeishuProvider,
    build_feishu_provider,
    format_daily_digest,
    sign_feishu_payload,
)
from app.services.feishu.base import FeishuSendResult
from app.utils import ExternalServiceError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_opp(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        id=1,
        title="AI 法律合同审核",
        summary="海外律师事务所在用 LLM 自动审核合同条款。",
        target_user="中型律所",
        total_score=92.0,
        market_size="100M-500M USD",
        mvp_days=14,
        difficulty="medium",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _MockTransport(httpx.AsyncBaseTransport):
    """Stub httpx transport that emulates Feishu's response envelope."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: dict[str, Any] | None = None,
        capture: list[httpx.Request] | None = None,
        raise_on_path: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body if body is not None else {"StatusCode": 0, "msg": "success"}
        self.calls = capture if capture is not None else []
        self.raise_on_path = raise_on_path

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.raise_on_path and self.raise_on_path in request.url.path:
            raise httpx.ConnectError("simulated outage")
        return httpx.Response(
            status_code=self.status_code,
            json=self.body,
            request=request,
        )


# ===========================================================================
# Signature helper
# ===========================================================================
def test_sign_feishu_payload_rejects_empty_secret() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        sign_feishu_payload(secret="")


def test_sign_feishu_payload_matches_documented_algorithm() -> None:
    """Reproduce Feishu's reference example verbatim.

    Feishu official spec (verified against
    https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot):
        string_to_sign = f"{timestamp}\\n{secret}"
        sign = base64(hmac.new(string_to_sign.encode(), b"",
                              hashlib.sha256).digest())

    The HMAC `key` is `string_to_sign` itself; the `message` is the
    empty byte string. Older internal docs at our company described the
    opposite ordering (key=secret, msg=string_to_sign) — that was
    wrong, fixed in Phase 6 bugfix.
    """
    secret = "secret_key_for_test"
    ts = 1700000000

    # Reference computation per the live Feishu docs:
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    expected = base64.b64encode(
        hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    ).decode("utf-8")

    out = sign_feishu_payload(secret=secret, timestamp=ts)
    assert out["timestamp"] == str(ts)
    assert out["sign"] == expected


def test_sign_feishu_payload_uses_fresh_timestamp_by_default() -> None:
    """Two calls within the same second should produce the same timestamp."""
    a = sign_feishu_payload(secret="k")
    b = sign_feishu_payload(secret="k")
    # Very loose assertion — both should be strings of 10 digits.
    assert a["timestamp"].isdigit() and len(a["timestamp"]) >= 10
    assert b["timestamp"].isdigit()
    assert a["sign"] != b["sign"] or a["timestamp"] == b["timestamp"]


# ===========================================================================
# Formatter
# ===========================================================================
def test_formatter_renders_interactive_envelope() -> None:
    card = format_daily_digest([_make_opp()])
    assert isinstance(card, FeishuCard)
    assert card.body["msg_type"] == "interactive"
    assert "card" in card.body
    assert card.body["card"]["header"]["template"] == "blue"
    assert "AI 机会雷达日报" in card.body["card"]["header"]["title"]["content"]


def test_formatter_includes_top_block_with_metadata() -> None:
    opp = _make_opp(total_score=92.0, market_size="100M-500M USD", mvp_days=14, difficulty="medium")
    card = format_daily_digest([opp])
    elements = card.body["card"]["elements"]
    # Find the lark_md div that contains "TOP 1" (the second div in
    # the card — the first is the summary line).
    top_blocks = [e for e in elements if e.get("tag") == "div"]
    top_div = next(
        (d for d in top_blocks if "TOP " in d["text"]["content"]),
        None,
    )
    assert top_div is not None, "expected a <div> with 'TOP ...'"
    top_text = top_div["text"]["content"]
    assert "TOP 1" in top_text
    assert "AI 法律合同审核" in top_text
    assert "100M-500M USD" in top_text
    assert "14 天" in top_text
    assert "medium" in top_text
    assert "[查看详情]" in top_text
    assert "/opportunities/1" in top_text


def test_formatter_handles_empty_opportunity_list() -> None:
    card = format_daily_digest([])
    elements = card.body["card"]["elements"]
    assert any("暂无" in e["text"]["content"] for e in elements if e.get("tag") == "div")


def test_formatter_caps_at_max_opportunities() -> None:
    opps = [_make_opp(id=i, title=f"opp-{i}") for i in range(20)]
    card = format_daily_digest(opps)
    # Should contain exactly MAX_OPPORTUNITIES top blocks (one div per opp).
    top_divs = [
        e for e in card.body["card"]["elements"]
        if e.get("tag") == "div" and "TOP " in e["text"]["content"]
    ]
    from app.services.feishu.formatter import MAX_OPPORTUNITIES
    assert len(top_divs) == MAX_OPPORTUNITIES


def test_formatter_truncates_long_summaries() -> None:
    long = "x" * 5000
    card = format_daily_digest([_make_opp(summary=long)])
    elements = card.body["card"]["elements"]
    top_text = next(
        e["text"]["content"] for e in elements
        if e.get("tag") == "div" and "TOP " in e["text"]["content"]
    )
    from app.services.feishu.formatter import SUMMARY_CHAR_LIMIT
    # The full summary line should be capped, with an ellipsis.
    assert "…" in top_text
    assert top_text.count("x") <= SUMMARY_CHAR_LIMIT


def test_formatter_score_emoji_thresholds() -> None:
    from app.services.feishu.formatter import _score_emoji

    assert _score_emoji(95) == "⭐"
    assert _score_emoji(85) == "🟢"
    assert _score_emoji(75) == "🟡"
    assert _score_emoji(65) == "🟠"
    assert _score_emoji(45) == "🔴"
    assert _score_emoji(None) == "⚪"


# ===========================================================================
# Mock provider
# ===========================================================================
@pytest.mark.asyncio
async def test_mock_provider_records_sends() -> None:
    p = MockFeishuProvider()
    card = format_daily_digest([_make_opp()])
    result = await p.send_card(card)

    assert isinstance(result, FeishuSendResult)
    assert result.ok is True
    assert result.provider == "mock-feishu"
    assert result.body_chars > 0

    sent = p.sent
    assert len(sent) == 1
    assert sent[0].body["msg_type"] == "interactive"


@pytest.mark.asyncio
async def test_mock_provider_failure_flag_raises_external_service_error() -> None:
    p = MockFeishuProvider(should_fail=True, failure_message="robot_disabled")
    card = format_daily_digest([_make_opp()])
    with pytest.raises(ExternalServiceError, match="robot_disabled"):
        await p.send_card(card)


@pytest.mark.asyncio
async def test_mock_provider_reset_clears_history() -> None:
    p = MockFeishuProvider()
    await p.send_card(format_daily_digest([_make_opp()]))
    assert len(p.sent) == 1
    p.reset()
    assert p.sent == []


# ===========================================================================
# Httpx provider (real transport, mock underlying HTTP)
# ===========================================================================
def test_httpx_provider_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="webhook_url"):
        HttpxFeishuProvider(webhook_url="")


def test_httpx_provider_rejects_non_feishu_url() -> None:
    with pytest.raises(ValueError, match="Feishu endpoint"):
        HttpxFeishuProvider(webhook_url="https://example.com/hook")


@pytest.mark.asyncio
async def test_httpx_provider_sends_interactive_card_and_parses_success() -> None:
    captured: list[httpx.Request] = []
    transport = _MockTransport(
        status_code=200,
        body={"StatusCode": 0, "msg": "success", "data": {"message_id": "om_1"}},
        capture=captured,
    )
    client = httpx.AsyncClient(timeout=10, transport=transport)
    provider = HttpxFeishuProvider(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        client=client,
    )
    try:
        result = await provider.send_card(format_daily_digest([_make_opp()]))
    finally:
        await provider.aclose()

    assert result.ok is True
    assert result.body_chars > 0
    assert len(captured) == 1
    sent_payload = json.loads(captured[0].content.decode("utf-8"))
    assert sent_payload["msg_type"] == "interactive"


@pytest.mark.asyncio
async def test_httpx_provider_attaches_signature_when_secret_set() -> None:
    captured: list[httpx.Request] = []
    transport = _MockTransport(capture=captured)
    client = httpx.AsyncClient(timeout=10, transport=transport)
    provider = HttpxFeishuProvider(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        signing_secret="robot_secret",
        client=client,
    )
    try:
        await provider.send_card(format_daily_digest([_make_opp()]))
    finally:
        await provider.aclose()

    sent_payload = json.loads(captured[0].content.decode("utf-8"))
    assert "timestamp" in sent_payload
    assert "sign" in sent_payload
    # Verify the signature against the live Feishu algorithm
    # (key = string_to_sign, message = empty bytes).
    ts = sent_payload["timestamp"]
    expected = base64.b64encode(
        hmac.new(
            f"{ts}\nrobot_secret".encode("utf-8"),
            b"",
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    assert sent_payload["sign"] == expected


@pytest.mark.asyncio
async def test_httpx_provider_translates_4xx_to_external_error() -> None:
    transport = _MockTransport(status_code=400, body={"StatusCode": 999, "msg": "bad"})
    client = httpx.AsyncClient(timeout=10, transport=transport)
    provider = HttpxFeishuProvider(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        client=client,
    )
    try:
        with pytest.raises(ExternalServiceError, match="HTTP 400"):
            await provider.send_card(format_daily_digest([_make_opp()]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_httpx_provider_translates_feishu_rejection_to_external_error() -> None:
    transport = _MockTransport(
        status_code=200,
        body={"StatusCode": 230001, "msg": "robot disabled"},
    )
    client = httpx.AsyncClient(timeout=10, transport=transport)
    provider = HttpxFeishuProvider(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        client=client,
    )
    try:
        with pytest.raises(ExternalServiceError, match="robot disabled"):
            await provider.send_card(format_daily_digest([_make_opp()]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_httpx_provider_translates_connect_error_to_external_error() -> None:
    transport = _MockTransport(raise_on_path="open-apis")
    client = httpx.AsyncClient(timeout=10, transport=transport)
    provider = HttpxFeishuProvider(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        client=client,
    )
    try:
        with pytest.raises(ExternalServiceError, match="feishu request failed"):
            await provider.send_card(format_daily_digest([_make_opp()]))
    finally:
        await provider.aclose()


# ===========================================================================
# Factory
# ===========================================================================
def test_factory_returns_mock_when_no_url_configured() -> None:
    settings = SimpleNamespace(
        mock_external_services=False,
        feishu_webhook_url="",
        feishu_webhook_secret="",
        feishu_timeout=15.0,
    )
    provider = build_feishu_provider(settings)
    assert isinstance(provider, MockFeishuProvider)


def test_factory_returns_httpx_when_url_configured_even_under_mock_flag() -> None:
    """Phase 6 product decision: a configured `feishu_webhook_url` is
    an explicit opt-in to live delivery — `MOCK_EXTERNAL_SERVICES=true`
    no longer wins over a real URL. Operators who want fully-offline
    behaviour can omit the URL (default) or pass `prefer="mock"`.
    """
    settings = SimpleNamespace(
        mock_external_services=True,
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        feishu_webhook_secret="",
        feishu_timeout=15.0,
    )
    provider = build_feishu_provider(settings)
    assert isinstance(provider, HttpxFeishuProvider)


def test_factory_returns_mock_when_prefer_mock_with_url() -> None:
    """`prefer="mock"` still wins — tests opt-out explicitly."""
    settings = SimpleNamespace(
        mock_external_services=True,
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        feishu_webhook_secret="",
        feishu_timeout=15.0,
    )
    provider = build_feishu_provider(settings, prefer="mock")
    assert isinstance(provider, MockFeishuProvider)


def test_factory_returns_httpx_when_url_configured() -> None:
    settings = SimpleNamespace(
        mock_external_services=False,
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        feishu_webhook_secret="",
        feishu_timeout=15.0,
    )
    provider = build_feishu_provider(settings)
    assert isinstance(provider, HttpxFeishuProvider)


# ===========================================================================
# Bot orchestrator
# ===========================================================================
@pytest.mark.asyncio
async def test_bot_sends_digest_and_persists_count(sqlite_session) -> None:
    from app.models import Opportunity

    for i in range(3):
        sqlite_session.add(
            Opportunity(
                title=f"opp-{i}",
                slug=f"opp-{i}",
                total_score=80.0 + i,
                commercial_status="qualified",
                content_status="new",
            )
        )
    await sqlite_session.flush()

    mock_provider = MockFeishuProvider()
    bot = FeishuBot(session=sqlite_session, provider=mock_provider)
    summary = await bot.send_digest(limit=2, only_qualified=True, window_hours=None)

    assert summary.sent is True
    assert summary.opportunity_count == 2
    assert summary.error is None
    assert summary.provider == "mock-feishu"
    assert len(mock_provider.sent) == 1


@pytest.mark.asyncio
async def test_bot_reports_error_when_provider_fails(sqlite_session) -> None:
    from app.models import Opportunity

    sqlite_session.add(
        Opportunity(
            title="opp-1",
            slug="opp-1",
            total_score=85.0,
            commercial_status="qualified",
            content_status="new",
        )
    )
    await sqlite_session.flush()

    mock_provider = MockFeishuProvider(should_fail=True)
    bot = FeishuBot(session=sqlite_session, provider=mock_provider)
    summary = await bot.send_digest(limit=5, only_qualified=True, window_hours=None)

    assert summary.sent is False
    assert summary.error and "synthetic_failure" in summary.error


@pytest.mark.asyncio
async def test_bot_handles_empty_selection_gracefully(sqlite_session) -> None:
    """No qualifying opps → still returns a successful (empty) digest."""
    mock_provider = MockFeishuProvider()
    bot = FeishuBot(session=sqlite_session, provider=mock_provider)
    summary = await bot.send_digest(limit=5, only_qualified=True, window_hours=None)

    assert summary.sent is True
    assert summary.opportunity_count == 0
    # The mock still recorded the card; the body just says "no opps".
    assert len(mock_provider.sent) == 1


# ===========================================================================
# Endpoint integration
# ===========================================================================
@pytest.mark.asyncio
async def test_feishu_digest_endpoint_returns_summary(
    client,
    sqlite_session,
) -> None:
    from app.models import Opportunity

    sqlite_session.add(
        Opportunity(
            title="endpoint opp",
            slug="endpoint-opp",
            total_score=87.0,
            commercial_status="qualified",
            content_status="new",
        )
    )
    await sqlite_session.flush()

    # When FEISHU_WEBHOOK_URL is unset (default in tests) the endpoint
    # routes through the mock provider, so the call always succeeds.
    response = client.post(
        "/api/internal/feishu/digest/send",
        json={"limit": 1, "only_qualified": True, "window_hours": None},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["sent"] is True
    assert payload["opportunity_count"] == 1
    assert payload["provider"] == "mock-feishu"
    assert payload["error"] is None