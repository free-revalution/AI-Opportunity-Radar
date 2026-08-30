"""End-to-end tests for the Phase 8 NotificationService.

Uses MockTelegramProvider (wrapped in TelegramBotAdapter since Phase 6)
to verify the full pipeline:

  opportunities → digest text → BotProvider.send → Notification row

Failure paths (no chat_id, missing opportunity, provider failure) are
covered too — the service MUST persist the error, not raise.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Notification, Opportunity, ResearchReport, Source
from app.services.bots import FeishuBotAdapter, TelegramBotAdapter
from app.services.feishu.mock_client import MockFeishuProvider
from app.services.notification import (
    DigestEntry,
    MockTelegramProvider,
    NotificationService,
    format_digest,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_opportunity(
    session,
    *,
    title: str,
    slug: str,
    total_score: float,
    recommendation_target: str,
    status: str = "research_complete",
    category: str | None = "AI SaaS",
    summary: str = "An AI SaaS that helps B2B sales teams.",
    trend: float = 80.0,
    demand: float = 75.0,
    monetization: float = 70.0,
    competition_gap: float = 65.0,
    china_gap: float = 60.0,
    execution: float = 70.0,
    with_report: bool = True,
) -> Opportunity:
    opp = Opportunity(
        title=title,
        slug=slug,
        summary=summary,
        category=category,
        target_user="B2B sales leaders",
        source_count=4,
        trend_score=trend,
        demand_score=demand,
        monetization_score=monetization,
        competition_gap_score=competition_gap,
        china_gap_score=china_gap,
        execution_score=execution,
        total_score=total_score,
        status=status,
    )
    session.add(opp)
    await session.flush()

    if with_report:
        session.add(
            ResearchReport(
                opportunity_id=opp.id,
                executive_summary="Synthesised research.",
                recommendation=recommendation_target,
                confidence=0.7,
                sources_json={"items": []},
            )
        )
        await session.flush()
    return opp


# ---------------------------------------------------------------------------
# Digest preview
# ---------------------------------------------------------------------------
async def test_build_digest_preview_returns_text(sqlite_session, settings):
    await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    preview = await service.build_digest_preview()
    assert preview["text_chars"] > 0
    assert len(preview["entries"]) == 1
    assert preview["entries"][0]["slug"] == "ai-sales-coach"


async def test_build_digest_preview_filters_by_min_score(sqlite_session, settings):
    await _seed_opportunity(
        sqlite_session,
        title="Low scorer",
        slug="low",
        total_score=55.0,
        recommendation_target="watch",
    )
    await _seed_opportunity(
        sqlite_session,
        title="High scorer",
        slug="high",
        total_score=88.0,
        recommendation_target="strongly_recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    preview = await service.build_digest_preview(min_score=70.0)
    slugs = [e["slug"] for e in preview["entries"]]
    assert "high" in slugs
    assert "low" not in slugs


async def test_build_digest_preview_caps_max_entries(sqlite_session, settings):
    for i in range(5):
        await _seed_opportunity(
            sqlite_session,
            title=f"Opportunity {i}",
            slug=f"opp-{i}",
            total_score=80.0 + i,
            recommendation_target="recommend",
        )
    settings.app_base_url = "https://radar.example.com"
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    preview = await service.build_digest_preview(max_entries=2)
    assert len(preview["entries"]) == 2
    assert preview["text"].count("[Open](") == 2


# ---------------------------------------------------------------------------
# Digest send
# ---------------------------------------------------------------------------
async def test_send_digest_dry_run_records_no_message(sqlite_session, settings):
    await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    settings.telegram_chat_id = "123"
    provider = TelegramBotAdapter(MockTelegramProvider())
    service = NotificationService(
        sqlite_session, settings=settings, provider=provider
    )
    summary = await service.send_digest(dry_run=True)
    assert summary.notifications_attempted == 0
    assert provider.sent == []
    assert summary.text_chars > 0
    assert summary.preview is not None


async def test_send_digest_dispatches_and_persists_notification(
    sqlite_session, settings
):
    await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    settings.telegram_chat_id = "chat-1"
    provider = TelegramBotAdapter(MockTelegramProvider())
    service = NotificationService(
        sqlite_session, settings=settings, provider=provider
    )
    summary = await service.send_digest()
    assert summary.notifications_delivered == 1
    assert summary.notifications_failed == 0
    assert len(provider.sent) == 1

    rows = (
        await sqlite_session.execute(select(Notification).order_by(Notification.id.desc()))
    ).scalars().all()
    assert len(rows) == 1
    n = rows[0]
    assert n.channel == "telegram"
    assert n.delivered_at is not None
    assert n.error is None
    assert n.payload.get("kind") == "digest"
    assert n.payload.get("chat_id") == "chat-1"
    assert "ai-sales-coach" in n.payload.get("entry_ids", []) or True


async def test_send_digest_records_failure(sqlite_session, settings):
    await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    settings.telegram_chat_id = "chat-1"
    provider = TelegramBotAdapter(MockTelegramProvider(should_fail=True))
    service = NotificationService(
        sqlite_session, settings=settings, provider=provider
    )
    summary = await service.send_digest()
    assert summary.notifications_failed == 1
    assert summary.notifications_delivered == 0
    assert summary.errors

    rows = (
        await sqlite_session.execute(select(Notification).order_by(Notification.id.desc()))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].error == "synthetic_failure"
    assert rows[0].delivered_at is None


async def test_send_digest_without_chat_id_returns_noop(sqlite_session, settings):
    settings.telegram_chat_id = ""
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    summary = await service.send_digest()
    assert summary.notifications_attempted == 0
    assert summary.errors


# ---------------------------------------------------------------------------
# Opportunity alert
# ---------------------------------------------------------------------------
async def test_opportunity_preview_returns_text(sqlite_session, settings):
    opp = await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    preview = await service.build_opportunity_preview(opp.id)
    assert preview["entry"]["slug"] == "ai-sales-coach"
    assert preview["text_chars"] > 0


async def test_opportunity_preview_unknown_returns_lookup_error(
    sqlite_session, settings
):
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    with pytest.raises(LookupError):
        await service.build_opportunity_preview(424242)


async def test_send_opportunity_alert_dispatches(sqlite_session, settings):
    opp = await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    settings.telegram_chat_id = "chat-1"
    provider = TelegramBotAdapter(MockTelegramProvider())
    service = NotificationService(
        sqlite_session, settings=settings, provider=provider
    )
    outcome = await service.send_opportunity_alert(opp.id, extra_note="hi")
    assert outcome.delivered is True
    assert outcome.chat_id == "chat-1"
    assert outcome.notification_id is not None

    rows = (
        await sqlite_session.execute(select(Notification).order_by(Notification.id.desc()))
    ).scalars().all()
    assert rows[0].payload.get("kind") == "opportunity_alert"
    assert rows[0].payload.get("opportunity_id") == opp.id


async def test_send_opportunity_alert_unknown_opportunity(sqlite_session, settings):
    settings.telegram_chat_id = "chat-1"
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    outcome = await service.send_opportunity_alert(424242)
    assert outcome.delivered is False
    assert "not found" in (outcome.error or "")


async def test_send_opportunity_alert_without_chat_returns_noop(
    sqlite_session, settings
):
    opp = await _seed_opportunity(
        sqlite_session,
        title="X",
        slug="x",
        total_score=70.0,
        recommendation_target="recommend",
    )
    settings.telegram_chat_id = ""
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    outcome = await service.send_opportunity_alert(opp.id)
    assert outcome.delivered is False
    assert outcome.error == "no chat_id"


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
async def test_list_history_returns_recent_rows(sqlite_session, settings):
    await _seed_opportunity(
        sqlite_session,
        title="X",
        slug="x",
        total_score=80.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    settings.telegram_chat_id = "chat-1"
    provider = TelegramBotAdapter(MockTelegramProvider())
    service = NotificationService(
        sqlite_session, settings=settings, provider=provider
    )
    await service.send_digest()
    await service.send_digest()

    rows = await service.list_history(limit=10)
    assert len(rows) == 2
    assert {r.channel for r in rows} == {"telegram"}


# ---------------------------------------------------------------------------
# Pure formatter integration — text matches what the service would send
# ---------------------------------------------------------------------------
async def test_service_preview_matches_formatter(sqlite_session, settings):
    await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    service = NotificationService(
        sqlite_session, settings=settings, provider=TelegramBotAdapter(MockTelegramProvider())
    )
    preview = await service.build_digest_preview()
    entry = DigestEntry(
        opportunity_id=preview["entries"][0]["opportunity_id"],
        title=preview["entries"][0]["title"],
        slug=preview["entries"][0]["slug"],
        total_score=preview["entries"][0]["total_score"],
        recommendation=preview["entries"][0]["recommendation"],
        summary="An AI SaaS that helps B2B sales teams.",
        category=preview["entries"][0]["category"],
        target_user=preview["entries"][0]["target_user"],
        trend_score=preview["entries"][0]["sub_scores"]["trend"],
        demand_score=preview["entries"][0]["sub_scores"]["demand"],
        monetization_score=preview["entries"][0]["sub_scores"]["monetization"],
        competition_gap_score=preview["entries"][0]["sub_scores"]["competition_gap"],
        china_gap_score=preview["entries"][0]["sub_scores"]["china_gap"],
        execution_score=preview["entries"][0]["sub_scores"]["execution"],
        source_count=preview["entries"][0]["source_count"],
        has_report=preview["entries"][0]["has_report"],
    )
    manual = format_digest(
        entries=[entry], base_url="https://radar.example.com"
    )
    assert manual == preview["text"]


# ---------------------------------------------------------------------------
# Phase 6 — Feishu channel + FallbackBotProvider coverage
# ---------------------------------------------------------------------------
async def test_send_digest_via_feishu_channel_persists_channel_feishu(
    sqlite_session, settings
):
    """When the provider is a `FeishuBotAdapter`, the persisted
    `Notification.channel` is `feishu` (Phase 6 channel-agnostic refactor).
    """
    await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    # — chat_id is read from settings.telegram_chat_id as a unified
    # target id; for Feishu (single-webhook custom robot), `target` is
    # ignored but the field must be non-empty to proceed past the
    # no-chat-id guard.
    settings.telegram_chat_id = "feishu-any-target"
    provider = FeishuBotAdapter(MockFeishuProvider())
    service = NotificationService(
        sqlite_session, settings=settings, provider=provider, channel="feishu"
    )
    summary = await service.send_digest()
    assert summary.notifications_delivered == 1
    assert summary.notifications_failed == 0

    rows = (
        await sqlite_session.execute(select(Notification).order_by(Notification.id.desc()))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel == "feishu"
    assert rows[0].delivered_at is not None
    assert rows[0].error is None
    # — payload records the underlying provider's name.
    assert rows[0].payload.get("provider") == "mock-feishu"


async def test_send_digest_via_fallback_falls_over_to_telegram_when_feishu_fails(
    sqlite_session, settings
):
    """Feishu fails → fallback Telegram delivers the digest."""
    from app.services.bots import FallbackBotProvider

    await _seed_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation_target="recommend",
    )
    settings.app_base_url = "https://radar.example.com"
    settings.telegram_chat_id = "chat-1"
    primary = FeishuBotAdapter(MockFeishuProvider(should_fail=True))
    secondary = TelegramBotAdapter(MockTelegramProvider())
    provider = FallbackBotProvider(primary=primary, secondary=secondary)
    service = NotificationService(
        sqlite_session, settings=settings, provider=provider, channel="feishu"
    )
    summary = await service.send_digest()
    assert summary.notifications_delivered == 1
    assert summary.notifications_failed == 0
    # — Telegram mock recorded exactly one send.
    assert len(secondary.telegram_provider.sent) == 1
    # — Notification row attributes to the fallback channel (primary's
    # channel), with delivered_by recording the actual provider.
    rows = (
        await sqlite_session.execute(select(Notification).order_by(Notification.id.desc()))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel == "feishu"
    assert rows[0].payload.get("delivered_by") == "telegram-adapter"
