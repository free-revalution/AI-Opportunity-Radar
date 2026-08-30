"""Tests for the subscription paywall — Phase 15B/15C v2.0.

Covers:

  * `check_access` — free user 1st /today → allowed
  * free user 2nd /today → denied with upgrade hint
  * basic user 6th /today → denied
  * pro user 21st /today → denied
  * creator user 1000th /today → still allowed (10**9 cap)
  * expired subscription → falls back to free quota = 1
  * no subscription at all → falls back to free quota = 1
  * Redis unavailable → fail-open (allowed, plan='unknown', warn logged)
  * command not in COMMAND_TO_FEATURE → bypass quota
  * quota counter is per-(quota_type, open_id) — distinct buckets
  * quota key schema (radar:q:{type}:{open_id}:{yyyymmdd_utc})
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_subscription(
    sessionmaker,
    *,
    feishu_open_id: str,
    plan: str = "free",
    status: str = "active",
    expires_at: datetime | None = None,
) -> int:
    from app.models import Subscription

    if expires_at is None:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=30)
    async with sessionmaker() as session:
        sub = Subscription(
            feishu_open_id=feishu_open_id,
            plan=plan,
            status=status,
            expires_at=expires_at,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return sub.id


# ---------------------------------------------------------------------------
# Free tier
# ---------------------------------------------------------------------------
class TestFreeTier:
    async def test_first_today_allowed(self, client, fake_redis):
        from app.services.subscriptions.paywall import check_access

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session,
                "ou_free",
                "today",
                redis_client=fake_redis,
            )
        assert v.allowed
        assert v.plan == "free"
        assert v.quota_type == "view_top_signals"
        assert v.quota_limit == 1
        assert v.quota_used == 0

    async def test_second_today_denied(self, client, fake_redis):
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        # Burn the free quota with one distinct signal ID
        await record_view_top_signals(fake_redis, "ou_free", [101])
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_free", "today", redis_client=fake_redis
            )
        assert not v.allowed
        assert v.plan == "free"
        assert v.quota_used == 1
        assert v.quota_limit == 1
        assert v.deny_reason == "quota_exceeded"
        assert "免费" in v.deny_message_zh or "1" in v.deny_message_zh


# ---------------------------------------------------------------------------
# Paid tiers
# ---------------------------------------------------------------------------
class TestPaidTiers:
    async def test_basic_within_quota_allowed(self, client, fake_redis):
        from app.services.subscriptions.paywall import check_access

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_b",
            plan="basic",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_b", "today", redis_client=fake_redis
            )
        assert v.allowed
        assert v.plan == "basic"
        assert v.quota_limit == 5

    async def test_basic_over_quota_denied(self, client, fake_redis):
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_b",
            plan="basic",
        )
        # 5 distinct signal IDs (basic plan daily_signals = 5)
        await record_view_top_signals(
            fake_redis, "ou_b", [101, 102, 103, 104, 105]
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_b", "today", redis_client=fake_redis
            )
        assert not v.allowed
        assert v.quota_used == 5
        assert v.quota_limit == 5
        assert "基础" in v.deny_message_zh or "5" in v.deny_message_zh

    async def test_pro_within_quota(self, client, fake_redis):
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_p",
            plan="pro",
        )
        await record_view_top_signals(fake_redis, "ou_p", list(range(1, 20)))
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_p", "today", redis_client=fake_redis
            )
        assert v.allowed
        assert v.quota_limit == 20

    async def test_pro_over_quota_denied(self, client, fake_redis):
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_p",
            plan="pro",
        )
        await record_view_top_signals(fake_redis, "ou_p", list(range(1, 21)))
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_p", "today", redis_client=fake_redis
            )
        assert not v.allowed
        assert v.quota_used == 20
        assert v.quota_limit == 20

    async def test_creator_high_cap(self, client, fake_redis):
        """Creator tier is essentially unlimited — 10**9."""
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_c",
            plan="creator",
        )
        await record_view_top_signals(fake_redis, "ou_c", list(range(1, 1001)))
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_c", "today", redis_client=fake_redis
            )
        assert v.allowed
        assert v.quota_limit == 10**9
        assert v.quota_used == 1000


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    async def test_expired_subscription_falls_back_to_free(self, client, fake_redis):
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        # Expired pro subscription
        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_expired",
            plan="pro",
            status="active",  # status says active, but expires_at < now
            expires_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
        )
        await record_view_top_signals(fake_redis, "ou_expired", [999])
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session,
                "ou_expired",
                "today",
                redis_client=fake_redis,
            )
        # Treated as free — quota = 1, already used.
        assert not v.allowed
        assert v.plan == "free"  # expired row → no active sub → free fallback
        assert v.quota_limit == 1

    async def test_no_subscription_falls_back_to_free(self, client, fake_redis):
        from app.services.subscriptions.paywall import check_access

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session,
                "ou_no_sub",
                "today",
                redis_client=fake_redis,
            )
        assert v.allowed
        assert v.plan == "free"
        assert v.quota_limit == 1

    async def test_redis_unavailable_fail_open(self, client):
        """No Redis → quota still allowed (fail-open semantics)."""
        from app.services.subscriptions.paywall import check_access

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session,
                "ou_anyone",
                "today",
                redis_client=None,
            )
        assert v.allowed
        assert v.quota_used == 0  # can't read, so we report 0

    async def test_command_not_mapped_bypasses_paywall(self, client, fake_redis):
        from app.services.subscriptions.paywall import check_access

        for kind in ("help", "activate", "preferences", "refresh", "score"):
            async with client.sessionmaker() as session:  # type: ignore[attr-defined]
                v = await check_access(
                    session, "ou_user", kind, redis_client=fake_redis
                )
            assert v.allowed, f"{kind} should bypass paywall"
            assert v.quota_type == "bypass"


# ---------------------------------------------------------------------------
# Quota key schema
# ---------------------------------------------------------------------------
class TestQuotaKeySchema:
    def test_quota_key_format(self):
        from app.services.subscriptions.paywall import quota_key

        now = datetime(2026, 8, 29, 12, 34, 56, tzinfo=timezone.utc)
        assert quota_key("view_top_signals", "ou_x", now) == (
            "radar:q:view_top_signals:ou_x:20260829"
        )

    def test_quota_key_research(self):
        from app.services.subscriptions.paywall import quota_key

        now = datetime(2026, 1, 5, 0, 0, 0, tzinfo=timezone.utc)
        assert quota_key("research", "ou_y", now) == (
            "radar:q:research:ou_y:20260105"
        )

    def test_command_to_feature_mapping(self):
        from app.services.subscriptions.paywall import command_to_feature

        assert command_to_feature("today") == "view_top_signals"
        assert command_to_feature("top") == "view_top_signals"
        assert command_to_feature("search") == "view_top_signals"
        assert command_to_feature("research") == "research"
        assert command_to_feature("daily") == "content_full"
        assert command_to_feature("report") == "content_full"
        assert command_to_feature("table") == "content_full"
        assert command_to_feature("content") == "content_full"
        # bypass
        assert command_to_feature("help") is None
        assert command_to_feature("activate") is None
        assert command_to_feature("preferences") is None
        assert command_to_feature("refresh") is None


# ---------------------------------------------------------------------------
# Distinct quota buckets
# ---------------------------------------------------------------------------
class TestQuotaBuckets:
    async def test_research_and_today_are_separate_counters(
        self, client, fake_redis
    ):
        """A user burning their /today quota must NOT lock out /research."""
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_mix",
            plan="basic",
        )
        # Burn /today with 5 distinct IDs
        await record_view_top_signals(
            fake_redis, "ou_mix", [11, 22, 33, 44, 55]
        )
        # /research still allowed (basic has 1/day, not yet used)
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await check_access(
                session, "ou_mix", "research", redis_client=fake_redis
            )
        assert r.allowed
        assert r.quota_type == "research"
        assert r.quota_limit == 1


# ---------------------------------------------------------------------------
# record_consumption idempotency / safety
# ---------------------------------------------------------------------------
class TestRecordConsumption:
    async def test_record_consumption_with_redis_none_returns_zero(self):
        from app.services.subscriptions.paywall import record_consumption

        n = await record_consumption(None, "ou_x", "view_top_signals")
        assert n == 0

    async def test_record_consumption_ignores_unknown_quota_type(self, fake_redis):
        from app.services.subscriptions.paywall import record_consumption

        n = await record_consumption(fake_redis, "ou_x", "bogus_type")
        assert n == 0
        assert fake_redis.snapshot() == {}

    async def test_record_consumption_increments_correct_key(self, fake_redis):
        """`research` (and `content_full`) still go through the legacy
        INCR path — Phase 16 only moved ``view_top_signals`` to SADD."""
        from app.services.subscriptions.paywall import record_consumption

        await record_consumption(fake_redis, "ou_k", "research")
        await record_consumption(fake_redis, "ou_k", "research")
        snap = fake_redis.snapshot()
        assert len(snap) == 1
        assert int(next(iter(snap.values()))) == 2


# ---------------------------------------------------------------------------
# Phase 16 — distinct-signal quota (SADD-based)
# ---------------------------------------------------------------------------
class TestDistinctQuota:
    """doc §47 — Free 每天 1 个公开 Signal / Basic 5 / Pro 10~20.

    Phase 16 implements this with SADD distinct-IDs instead of INCR
    command-counting. Free user `/today` 第二次必须返 deny,而不是依然
    返 Top 10 但 INCR=2.
    """

    async def test_view_top_signals_distinct_ids(self, fake_redis):
        """SADD 5 distinct IDs → SCARD == 5."""
        from app.services.subscriptions.paywall import (
            peek_view_top_signals_count,
            record_view_top_signals,
        )

        await record_view_top_signals(fake_redis, "ou_d", [1, 2, 3, 4, 5])
        used = await peek_view_top_signals_count(fake_redis, "ou_d")
        assert used == 5

    async def test_view_top_signals_idempotent_sadd(self, fake_redis):
        """SADD same ID twice → SCARD still 1 (no double-charge)."""
        from app.services.subscriptions.paywall import (
            peek_view_top_signals_count,
            record_view_top_signals,
        )

        await record_view_top_signals(fake_redis, "ou_d", [42])
        await record_view_top_signals(fake_redis, "ou_d", [42])
        await record_view_top_signals(fake_redis, "ou_d", [42, 42])
        used = await peek_view_top_signals_count(fake_redis, "ou_d")
        assert used == 1

    async def test_view_top_signals_ignores_unknown_ids(self, fake_redis):
        """Empty ID list is a no-op (returns 0, no Redis write)."""
        from app.services.subscriptions.paywall import record_view_top_signals

        added = await record_view_top_signals(fake_redis, "ou_d", [])
        assert added == 0
        assert fake_redis.snapshot_sets() == {}

    async def test_view_top_signals_returns_zero_when_missing(self, fake_redis):
        """First-time peek on a brand-new open_id → 0 (not 1, not raise)."""
        from app.services.subscriptions.paywall import peek_view_top_signals_count

        used = await peek_view_top_signals_count(fake_redis, "ou_brand_new")
        assert used == 0

    async def test_view_top_signals_returns_zero_when_redis_none(self):
        """Redis client missing → fail-open (0)."""
        from app.services.subscriptions.paywall import (
            peek_view_top_signals_count,
            record_view_top_signals,
        )

        assert await peek_view_top_signals_count(None, "ou_x") == 0
        assert await record_view_top_signals(None, "ou_x", [1, 2]) == 0

    async def test_view_top_signals_record_returns_post_add_count(self, fake_redis):
        """`record_view_top_signals` returns the post-add SCARD so
        callers can show "used N / limit M" without a second round-trip."""
        from app.services.subscriptions.paywall import record_view_top_signals

        n1 = await record_view_top_signals(fake_redis, "ou_d", [1])
        assert n1 == 1
        n2 = await record_view_top_signals(fake_redis, "ou_d", [1, 2, 3])
        assert n2 == 3

    async def test_view_top_signals_truncate_to_remaining_in_check_access(
        self, client, fake_redis
    ):
        """Free user + 1 already-seen distinct ID → 2nd /today is denied."""
        from app.services.subscriptions.paywall import (
            check_access,
            record_view_top_signals,
        )

        await record_view_top_signals(fake_redis, "ou_d", [101])
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_d", "today", redis_client=fake_redis
            )
        assert v.allowed is False
        assert v.plan == "free"
        assert v.quota_used == 1
        assert v.quota_limit == 1
        assert v.deny_reason == "quota_exceeded"

    async def test_view_top_signals_research_still_uses_incr(
        self, client, fake_redis
    ):
        """`research` quota is a command-call count, not distinct IDs —
        the INCR path must remain unchanged after the Phase 16 split."""
        from app.services.subscriptions.paywall import (
            check_access,
            record_consumption,
        )

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_dual",
            plan="basic",
        )
        await record_consumption(fake_redis, "ou_dual", "research")
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            v = await check_access(
                session, "ou_dual", "research", redis_client=fake_redis
            )
        # Basic plan has research_requests=1/day; 1 used → quota full.
        assert v.quota_type == "research"
        assert v.quota_used == 1
        assert v.quota_limit == 1

    async def test_view_top_signals_writes_to_set_bucket_not_string(
        self, fake_redis
    ):
        """The SCARD/INCR families must not collide — Phase 16 keeps
        `view_top_signals` keys as Redis SETs while `research` and
        `content_full` stay as STRING/INCR."""
        from app.services.subscriptions.paywall import record_view_top_signals

        await record_view_top_signals(fake_redis, "ou_kinds", [1, 2, 3])
        sets = fake_redis.snapshot_sets()
        strings = fake_redis.snapshot()
        # SET bucket holds the distinct-IDs record
        assert sets and any(
            k.endswith(":ou_kinds:20260829") or "ou_kinds" in k for k in sets
        )
        # STRING bucket is untouched by record_view_top_signals
        assert strings == {}

