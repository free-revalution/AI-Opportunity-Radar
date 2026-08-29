"""Tests for the activation anti-brute-force guard — Phase 15D.

Covers:

  * 5 consecutive failures for the same open_id → 6th returns RATE_LIMITED.
  * user_message for RATE_LIMITED contains "稍后再试".
  * Success clears the failure counter.
  * Redis unavailable (redis_client=None) → no blocking.
  * Empty open_id short-circuits at INVALID_FORMAT (no counter increment).
  * TTL expiry → counter resets to 1 on next failure.
  * Distinct open_ids have independent counters (parallel brute-force).
  * `_activate` Feishu handler returns the friendly Chinese reply.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_code(sessionmaker, *, plan: str = "basic"):
    from app.services.activation import generate_code, hash_code
    from app.models import ActivationCode

    plaintext = generate_code()
    async with sessionmaker() as session:
        row = ActivationCode(
            code_hash=hash_code(plaintext),
            plan=plan,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
            status="unused",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return plaintext, row.id


async def _seed_revoked_code(sessionmaker):
    from app.services.activation import generate_code, hash_code
    from app.models import ActivationCode

    plaintext = generate_code()
    async with sessionmaker() as session:
        row = ActivationCode(
            code_hash=hash_code(plaintext),
            plan="basic",
            expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
            status="revoked",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return plaintext


# ---------------------------------------------------------------------------
# Rate limit guard — straight `redeem_for_user` calls
# ---------------------------------------------------------------------------
class TestRateLimitGuard:
    async def test_five_failures_then_locked(self, client, fake_redis):
        """6th failed attempt returns RATE_LIMITED, 5 prior ones record normally."""
        from app.services.activation import redeem_for_user
        from app.services.activation import generate_code

        # Properly-formatted but unknown code — passes validate_format,
        # fails hash lookup → NOT_FOUND → bumps counter.
        BAD_CODE = generate_code()  # 12-char code in 3 groups of 4
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            for _ in range(5):
                r = await redeem_for_user(
                    session,
                    code=BAD_CODE,
                    feishu_open_id="ou_attacker",
                    redis_client=fake_redis,
                )
                assert r.success is False
                assert r.status.value == "not_found", r.status.value
        # 6th attempt — locked
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(
                session,
                code=BAD_CODE,
                feishu_open_id="ou_attacker",
                redis_client=fake_redis,
            )
        assert r.success is False
        assert r.status.value == "rate_limited"
        assert r.error.value == "rate_limited"
        assert "稍后再试" in r.user_message

    async def test_success_clears_counter(self, client, fake_redis):
        from app.services.activation import redeem_for_user, generate_code
        from app.models import ActivationCode

        plaintext, _ = await _seed_code(client.sessionmaker)  # type: ignore[attr-defined]
        # 4 prior failures (not enough to lock)
        BAD_CODE = generate_code()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            for _ in range(4):
                await redeem_for_user(
                    session,
                    code=BAD_CODE,
                    feishu_open_id="ou_x",
                    redis_client=fake_redis,
                )
        # Counter should be 4
        snap = fake_redis.snapshot()
        assert any(int(v) == 4 for v in snap.values()), snap
        # Successful redeem clears it
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(
                session,
                code=plaintext,
                feishu_open_id="ou_x",
                redis_client=fake_redis,
            )
        assert r.success
        snap = fake_redis.snapshot()
        assert not any(int(v) >= 1 for v in snap.values()), (
            f"counter should be empty after success, got {snap}"
        )

    async def test_redis_unavailable_no_blocking(self, client):
        from app.services.activation import redeem_for_user, generate_code

        BAD_CODE = generate_code()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            for _ in range(10):
                r = await redeem_for_user(
                    session,
                    code=BAD_CODE,
                    feishu_open_id="ou_y",
                    redis_client=None,
                )
                assert r.success is False
                assert r.status.value == "not_found"
        # No RATE_LIMITED — fail-open kept blocking disabled.

    async def test_empty_open_id_short_circuits(self, client, fake_redis):
        """Empty open_id → INVALID_FORMAT, no counter bump."""
        from app.services.activation import redeem_for_user, generate_code

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(
                session,
                code=generate_code(),
                feishu_open_id="",
                redis_client=fake_redis,
            )
        assert r.status.value == "invalid_format"
        # Counter not incremented for empty open_id
        snap = fake_redis.snapshot()
        assert snap == {}

    async def test_invalid_format_does_not_bump_counter(self, client, fake_redis):
        """Bad code format → INVALID_FORMAT, no counter bump.

        (Counter is only bumped once we get past the format check and
        actually tried to look the code up — cheap-rejection shouldn't
        affect the brute-force window.)
        """
        from app.services.activation import redeem_for_user

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            for _ in range(10):
                r = await redeem_for_user(
                    session,
                    code="hi",  # too short
                    feishu_open_id="ou_z",
                    redis_client=fake_redis,
                )
                assert r.status.value == "invalid_format"
        snap = fake_redis.snapshot()
        assert snap == {}

    async def test_distinct_open_ids_have_independent_counters(
        self, client, fake_redis
    ):
        from app.services.activation import redeem_for_user, generate_code

        BAD_CODE = generate_code()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            for _ in range(5):
                await redeem_for_user(
                    session,
                    code=BAD_CODE,
                    feishu_open_id="ou_user1",
                    redis_client=fake_redis,
                )
            # Different user — should still be at 0
            r = await redeem_for_user(
                session,
                code=BAD_CODE,
                feishu_open_id="ou_user2",
                redis_client=fake_redis,
            )
        assert r.status.value == "not_found"

    async def test_ttl_expiry_resets_counter(self, client, fake_redis):
        from app.services.activation import redeem_for_user, generate_code

        BAD_CODE = generate_code()
        # 5 failures — at the threshold
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            for _ in range(5):
                await redeem_for_user(
                    session,
                    code=BAD_CODE,
                    feishu_open_id="ou_ttl",
                    redis_client=fake_redis,
                )
        # Advance past the 10-minute TTL
        fake_redis.advance_time(11 * 60)
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r = await redeem_for_user(
                session,
                code=BAD_CODE,
                feishu_open_id="ou_ttl",
                redis_client=fake_redis,
            )
        # Counter expired — back to not_found (not rate_limited).
        assert r.status.value == "not_found"


# ---------------------------------------------------------------------------
# /activate Feishu handler integration
# ---------------------------------------------------------------------------
class TestActivateHandlerIntegration:
    @pytest.fixture
    def patched_sessionmaker(self, client, monkeypatch):
        from app import db as db_module

        monkeypatch.setattr(
            db_module,
            "get_sessionmaker",
            lambda: client.sessionmaker,  # type: ignore[attr-defined]
        )
        return client.sessionmaker

    async def test_handler_returns_rate_limited_message(
        self, client, patched_sessionmaker, fake_redis
    ):
        """`_activate` Feishu handler — 6th invalid attempt returns RATE_LIMITED."""
        from app.services.feishu.inbound import FeishuCommandRouter, parse_command

        # Pre-burn the counter via direct redeem_for_user calls
        from app.services.activation import redeem_for_user, generate_code

        BAD_CODE = generate_code()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            for _ in range(5):
                await redeem_for_user(
                    session,
                    code=BAD_CODE,
                    feishu_open_id="ou_handler",
                    redis_client=fake_redis,
                )

        router = FeishuCommandRouter(redis_client=fake_redis)
        router._sender_open_id = "ou_handler"
        cmd = parse_command(f"/activate {BAD_CODE}")
        reply = await router.route(cmd)

        assert reply.metadata["status"] == "rate_limited"
        assert "稍后再试" in reply.text

    async def test_handler_succeeds_when_redis_unavailable(
        self, client, patched_sessionmaker
    ):
        """When Redis is None, /activate never blocks — fail-open."""
        from app.services.activation import generate_code, hash_code
        from app.models import ActivationCode
        from app.services.feishu.inbound import FeishuCommandRouter, parse_command

        plaintext = generate_code()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            session.add(
                ActivationCode(
                    code_hash=hash_code(plaintext),
                    plan="basic",
                    expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
                    status="unused",
                )
            )
            await session.commit()

        router = FeishuCommandRouter(redis_client=None)
        router._sender_open_id = "ou_noredis"
        cmd = parse_command(f"/activate {plaintext}")
        reply = await router.route(cmd)

        assert reply.metadata["success"] is True
        assert "激活成功" in reply.text


# ---------------------------------------------------------------------------
# user_message helper
# ---------------------------------------------------------------------------
class TestUserMessage:
    def test_rate_limited_message_in_chinese(self):
        from app.services.activation import RedemptionResult, RedemptionStatus
        from app.services.activation.flow import user_message

        r = RedemptionResult(
            status=RedemptionStatus.RATE_LIMITED,
            success=False,
        )
        msg = user_message(r)
        assert "稍后再试" in msg
        assert "10 分钟" in msg
