"""Tests for the activation flow — Phase 14A.

Covers:

  * Happy path: valid code → binds + creates Subscription + writes audit row.
  * Idempotent re-bind: same user / same code again → ALREADY_ACTIVE.
  * ALREADY_BOUND: different user trying same code → blocked.
  * EXPIRED: expires_at in past → blocked.
  * REVOKED: status='revoked' → blocked.
  * NOT_FOUND: unknown code hash → blocked.
  * INVALID_FORMAT: malformed code → blocked.
  * Plan upgrade: existing higher-tier sub is preserved.
  * Plan downgrade: existing lower-tier sub is upgraded.
  * Subscription.expires_at extension: existing expired sub is extended.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------
async def _seed_code(
    sessionmaker,
    *,
    plan: str = "pro",
    expires_at: datetime | None = None,
    status: str = "unused",
    bound_to: str | None = None,
    bound_at: datetime | None = None,
) -> tuple[str, int]:
    """Insert an ActivationCode row. Returns (plaintext_code, code_id)."""
    from app.services.activation import generate_code, hash_code
    from app.models import ActivationCode

    if expires_at is None:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=365)
    plaintext = generate_code()
    async with sessionmaker() as session:
        row = ActivationCode(
            code_hash=hash_code(plaintext),
            plan=plan,
            expires_at=expires_at,
            status=status,
            bound_feishu_open_id=bound_to,
            bound_at=bound_at,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return plaintext, row.id


async def _seed_subscription(
    sessionmaker,
    *,
    feishu_open_id: str,
    plan: str = "free",
    status: str = "expired",
    expires_at: datetime | None = None,
) -> int:
    from app.models import Subscription

    if expires_at is None:
        expires_at = datetime.now(tz=timezone.utc) - timedelta(days=1)
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
# Happy path
# ---------------------------------------------------------------------------
class TestHappyPath:
    async def test_new_user_bind_creates_subscription(self, client):
        from app.models import ActivationCode, AuditLog, Subscription
        from app.services.activation import redeem_for_user

        plaintext, code_id = await _seed_code(client.sessionmaker, plan="pro")  # type: ignore[attr-defined]
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await redeem_for_user(
                session,
                code=plaintext,
                feishu_open_id="ou_new_user",
            )
        assert result.success
        assert result.status.value == "success"
        assert result.plan == "pro"
        assert result.code_id == code_id
        assert "激活成功" in result.user_message

        # ActivationCode row updated
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            row = (
                await session.execute(
                    select(ActivationCode).where(ActivationCode.id == code_id)
                )
            ).scalar_one()
            assert row.bound_feishu_open_id == "ou_new_user"
            assert row.status == "active"

        # Subscription row created
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            subs = list(
                (
                    await session.execute(
                        select(Subscription).where(
                            Subscription.feishu_open_id == "ou_new_user"
                        )
                    )
                ).scalars().all()
            )
            assert len(subs) == 1
            assert subs[0].plan == "pro"
            assert subs[0].status == "active"

        # Audit row written
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            audits = list(
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "activate")
                    )
                ).scalars().all()
            )
            assert len(audits) == 1
            assert audits[0].actor_id == "ou_new_user"
            assert audits[0].resource_id == str(code_id)

    async def test_user_message_includes_plan_and_expiry(self, client):
        from app.services.activation import redeem_for_user, user_message
        from app.models import ActivationCode

        plaintext, _ = await _seed_code(client.sessionmaker, plan="basic")  # type: ignore[attr-defined]
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await redeem_for_user(
                session,
                code=plaintext,
                feishu_open_id="ou_user_a",
            )
        msg = user_message(result)
        assert "基础版" in msg
        assert "¥29" in msg
        assert result.expires_at is not None
        assert "UTC" in msg


# ---------------------------------------------------------------------------
# Idempotent re-bind
# ---------------------------------------------------------------------------
class TestIdempotent:
    async def test_same_user_same_code_twice(self, client):
        from app.services.activation import redeem_for_user

        plaintext, _ = await _seed_code(client.sessionmaker)  # type: ignore[attr-defined]
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r1 = await redeem_for_user(session, code=plaintext, feishu_open_id="ou_a")
            r2 = await redeem_for_user(session, code=plaintext, feishu_open_id="ou_a")
        assert r1.success
        assert r2.success
        assert r2.status.value == "already_active"
        assert "无需重复激活" in r2.user_message


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------
class TestFailureModes:
    async def test_different_user_same_code_blocked(self, client):
        from app.services.activation import redeem_for_user

        plaintext, _ = await _seed_code(client.sessionmaker)  # type: ignore[attr-defined]
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r1 = await redeem_for_user(session, code=plaintext, feishu_open_id="ou_a")
        assert r1.success

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r2 = await redeem_for_user(session, code=plaintext, feishu_open_id="ou_b")
        assert not r2.success
        assert r2.status.value == "already_bound"
        assert "已被其他账号使用" in r2.user_message

    async def test_expired_code_blocked(self, client):
        from app.services.activation import redeem_for_user

        past = datetime.now(tz=timezone.utc) - timedelta(days=1)
        plaintext, _ = await _seed_code(
            client.sessionmaker,  # type: ignore[attr-defined]
            expires_at=past,
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(session, code=plaintext, feishu_open_id="ou_x")
        assert not r.success
        assert r.status.value == "expired"
        assert "已过期" in r.user_message

    async def test_revoked_code_blocked(self, client):
        from app.services.activation import redeem_for_user

        plaintext, _ = await _seed_code(
            client.sessionmaker,  # type: ignore[attr-defined]
            status="revoked",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(session, code=plaintext, feishu_open_id="ou_x")
        assert not r.success
        assert r.status.value == "revoked"
        assert "已被吊销" in r.user_message

    async def test_unknown_code_blocked(self, client):
        from app.services.activation import redeem_for_user

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(
                session, code="ABCD-EFGH-JKLM", feishu_open_id="ou_x"
            )
        assert not r.success
        assert r.status.value == "not_found"
        assert "无效" in r.user_message

    async def test_invalid_format_blocked(self, client):
        from app.services.activation import redeem_for_user

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(session, code="hi", feishu_open_id="ou_x")
        assert not r.success
        assert r.status.value == "invalid_format"

    async def test_empty_open_id_blocked(self, client):
        from app.services.activation import redeem_for_user

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(
                session, code="ABCD-EFGH-JKLM", feishu_open_id=""
            )
        assert not r.success
        assert r.status.value == "invalid_format"


# ---------------------------------------------------------------------------
# Subscription preservation
# ---------------------------------------------------------------------------
class TestSubscriptionRules:
    async def test_existing_expired_sub_extended(self, client):
        from app.services.activation import redeem_for_user
        from app.models import Subscription

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_up",
            plan="free",
            status="expired",
            expires_at=datetime.now(tz=timezone.utc) - timedelta(days=10),
        )
        plaintext, _ = await _seed_code(client.sessionmaker, plan="basic")  # type: ignore[attr-defined]
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(session, code=plaintext, feishu_open_id="ou_up")
        assert r.success
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            subs = list(
                (
                    await session.execute(
                        select(Subscription).where(
                            Subscription.feishu_open_id == "ou_up"
                        )
                    )
                ).scalars().all()
            )
        assert len(subs) == 1
        assert subs[0].plan == "basic"
        assert subs[0].status == "active"
        # expires_at extended to ~30 days from now
        exp = subs[0].expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        delta = exp - datetime.now(tz=timezone.utc)
        assert 28 <= delta.days <= 30

    async def test_existing_higher_tier_preserved(self, client):
        """User already has 'creator' plan — redeeming 'basic' must not downgrade."""
        from app.services.activation import redeem_for_user
        from app.models import Subscription

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_pro",
            plan="creator",
            status="active",
            expires_at=datetime.now(tz=timezone.utc) + timedelta(days=100),
        )
        plaintext, _ = await _seed_code(client.sessionmaker, plan="basic")  # type: ignore[attr-defined]
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            await redeem_for_user(session, code=plaintext, feishu_open_id="ou_pro")
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            subs = list(
                (
                    await session.execute(
                        select(Subscription).where(
                            Subscription.feishu_open_id == "ou_pro"
                        )
                    )
                ).scalars().all()
            )
        assert len(subs) == 1
        # Plan preserved at 'creator'
        assert subs[0].plan == "creator"

    async def test_existing_lower_tier_upgraded(self, client):
        """User has 'free' — redeeming 'pro' upgrades."""
        from app.services.activation import redeem_for_user
        from app.models import Subscription

        await _seed_subscription(
            client.sessionmaker,  # type: ignore[attr-defined]
            feishu_open_id="ou_f",
            plan="free",
            status="active",
            expires_at=datetime.now(tz=timezone.utc) + timedelta(days=100),
        )
        plaintext, _ = await _seed_code(client.sessionmaker, plan="pro")  # type: ignore[attr-defined]
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            await redeem_for_user(session, code=plaintext, feishu_open_id="ou_f")
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            subs = list(
                (
                    await session.execute(
                        select(Subscription).where(
                            Subscription.feishu_open_id == "ou_f"
                        )
                    )
                ).scalars().all()
            )
        assert len(subs) == 1
        assert subs[0].plan == "pro"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
class TestPlanDisplayZh:
    def test_known_plans(self):
        from app.services.activation import plan_display_zh

        assert "¥29" in plan_display_zh("basic")
        assert "¥59" in plan_display_zh("pro")
        assert "¥129" in plan_display_zh("creator")
        assert plan_display_zh("free") == "免费"

    def test_unknown_plan_falls_back(self):
        from app.services.activation import plan_display_zh

        assert plan_display_zh("weird") == "weird"