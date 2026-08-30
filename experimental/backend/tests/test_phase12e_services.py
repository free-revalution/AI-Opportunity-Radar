"""Tests for Phase 12E — ActivationCode + Subscription + Audit services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.activation import (
    ActivationError,
    generate_code,
    hash_code,
    issue_code,
    redeem_code,
    validate_format,
)
from app.services.audit import (
    ActorType,
    AuditAction,
    AuditResult,
    AuditService,
    default_service as default_audit,
    reset_default_service,
)
from app.services.subscriptions import (
    PLAN_CATALOGUE,
    Plan,
    PlanProfile,
    SubscriptionRow,
    SubscriptionStatus,
    gate,
    get_plan_profile,
    is_active,
)


# ---------------------------------------------------------------------------
# Activation service
# ---------------------------------------------------------------------------
class TestActivationCode:
    def test_format_is_groups_of_4(self):
        code = generate_code(12)
        assert code.count("-") == 2  # 12 chars → 3 groups → 2 dashes
        assert validate_format(code)

    def test_format_short(self):
        code = generate_code(4)
        assert code == code.upper()
        assert validate_format(code)

    def test_invalid_format_rejected(self):
        assert not validate_format("")
        assert not validate_format("lower-case")
        assert not validate_format("123")          # too short
        assert not validate_format("AB")           # too short
        # 16 raw chars in 4 groups — valid.
        assert validate_format("ABCD-EFGH-JKLM-NPQR")
        # 17 chars → invalid (last group too short).
        assert not validate_format("ABCD-EFGH-JKLM-NPQR-S")

    def test_hash_is_64_hex(self):
        h = hash_code("ABCD-EFGH")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_changes_with_pepper(self):
        h1 = hash_code("ABCD-EFGH", pepper="pepper-1")
        h2 = hash_code("ABCD-EFGH", pepper="pepper-2")
        assert h1 != h2

    def test_issue_returns_plaintext_and_hash(self):
        issued = issue_code("pro")
        assert issued.code
        assert issued.code_hash
        assert len(issued.code_hash) == 64
        assert issued.plan == "pro"
        assert issued.expires_at > datetime.now(tz=timezone.utc)

    def test_redeem_happy_path(self):
        issued = issue_code("pro")
        # Simulated DB row.
        class Row:
            id = 1
            code_hash = issued.code_hash
            plan = "pro"
            status = "unused"
            expires_at = issued.expires_at
            bound_feishu_open_id = None

        outcome = redeem_code(
            issued.code,
            "ou_user_123",
            lookup_by_hash=lambda h: Row() if h == issued.code_hash else None,
        )
        assert outcome.success
        assert outcome.plan == "pro"
        assert outcome.feishu_open_id == "ou_user_123"

    def test_redeem_wrong_code(self):
        issued = issue_code("pro")

        class Row:
            id = 1
            code_hash = issued.code_hash
            plan = "pro"
            status = "unused"
            expires_at = issued.expires_at
            bound_feishu_open_id = None

        outcome = redeem_code(
            "ZZZZ-ZZZZ-ZZZZ",
            "ou_user_123",
            lookup_by_hash=lambda h: Row() if h == issued.code_hash else None,
        )
        assert outcome.success is False
        assert outcome.error == ActivationError.NOT_FOUND

    def test_redeem_already_bound_to_other_user(self):
        issued = issue_code("pro")

        class Row:
            id = 1
            code_hash = issued.code_hash
            plan = "pro"
            status = "active"
            expires_at = issued.expires_at
            bound_feishu_open_id = "ou_other"

        outcome = redeem_code(
            issued.code,
            "ou_user_123",
            lookup_by_hash=lambda h: Row() if h == issued.code_hash else None,
        )
        assert outcome.success is False
        assert outcome.error == ActivationError.ALREADY_BOUND

    def test_redeem_same_user_idempotent(self):
        issued = issue_code("pro")

        class Row:
            id = 1
            code_hash = issued.code_hash
            plan = "pro"
            status = "active"
            expires_at = issued.expires_at
            bound_feishu_open_id = "ou_user_123"

        outcome = redeem_code(
            issued.code,
            "ou_user_123",
            lookup_by_hash=lambda h: Row() if h == issued.code_hash else None,
        )
        # Same user binding same code again is allowed (idempotent).
        assert outcome.success

    def test_redeem_revoked(self):
        issued = issue_code("pro")

        class Row:
            id = 1
            code_hash = issued.code_hash
            plan = "pro"
            status = "revoked"
            expires_at = issued.expires_at
            bound_feishu_open_id = None

        outcome = redeem_code(
            issued.code,
            "ou_user_123",
            lookup_by_hash=lambda h: Row() if h == issued.code_hash else None,
        )
        assert outcome.success is False
        assert outcome.error == ActivationError.REVOKED

    def test_redeem_expired(self):
        issued = issue_code("pro")

        class Row:
            id = 1
            code_hash = issued.code_hash
            plan = "pro"
            status = "active"
            expires_at = datetime.now(tz=timezone.utc) - timedelta(days=1)
            bound_feishu_open_id = None

        outcome = redeem_code(
            issued.code,
            "ou_user_123",
            lookup_by_hash=lambda h: Row() if h == issued.code_hash else None,
        )
        assert outcome.success is False
        assert outcome.error == ActivationError.EXPIRED

    def test_redeem_invalid_format(self):
        outcome = redeem_code("not-a-valid-format-lower", "ou_x")
        assert outcome.success is False
        assert outcome.error == ActivationError.INVALID_FORMAT

    def test_redeem_empty_open_id_rejected(self):
        outcome = redeem_code("ABCD-EFGH-JKLM", "")
        assert outcome.success is False
        assert outcome.error == ActivationError.INVALID_FORMAT

    def test_redeem_no_lookup(self):
        outcome = redeem_code("ABCD-EFGH-JKLM", "ou_x", lookup_by_hash=None)
        assert outcome.error == ActivationError.NOT_FOUND


# ---------------------------------------------------------------------------
# Subscription service
# ---------------------------------------------------------------------------
class TestSubscriptionGating:
    def _sub(self, plan="free", status="active", expires_at=None):
        return SubscriptionRow(plan=plan, status=status, expires_at=expires_at)

    def test_active_subscription_passes(self):
        v = gate(self._sub("pro", "active"), "view_top_signals")
        assert v.allowed

    def test_inactive_subscription_denied(self):
        v = gate(self._sub("pro", "expired"), "view_top_signals")
        assert not v.allowed
        assert "expired" in v.reason

    def test_expired_by_date_denied(self):
        past = datetime.now(tz=timezone.utc) - timedelta(days=1)
        v = gate(self._sub("pro", "active", expires_at=past), "view_top_signals")
        assert not v.allowed
        assert "expired" in v.reason

    def test_future_expiry_passes(self):
        future = datetime.now(tz=timezone.utc) + timedelta(days=30)
        v = gate(self._sub("pro", "active", expires_at=future), "view_top_signals")
        assert v.allowed

    def test_free_cannot_research(self):
        v = gate(self._sub("free"), "research")
        assert not v.allowed
        assert v.upgrade_to == "basic"

    def test_basic_can_research(self):
        v = gate(self._sub("basic"), "research")
        assert v.allowed

    def test_free_cannot_content_full(self):
        v = gate(self._sub("free"), "content_full")
        assert not v.allowed

    def test_basic_can_content_full(self):
        v = gate(self._sub("basic"), "content_full")
        assert v.allowed

    def test_free_cannot_auto_publish(self):
        v = gate(self._sub("free"), "auto_publish")
        assert not v.allowed
        assert v.upgrade_to == "pro"

    def test_basic_cannot_auto_publish(self):
        v = gate(self._sub("basic"), "auto_publish")
        assert not v.allowed

    def test_pro_can_auto_publish(self):
        v = gate(self._sub("pro"), "auto_publish")
        assert v.allowed

    def test_creator_can_anything(self):
        for feat in ("view_top_signals", "research", "content_full", "auto_publish"):
            assert gate(self._sub("creator"), feat).allowed

    def test_unknown_feature_fails_closed(self):
        v = gate(self._sub("creator"), "no_such_feature")
        assert not v.allowed
        assert "unknown" in v.reason

    def test_accepts_dict_input(self):
        d = {"plan": "pro", "status": "active", "expires_at": None}
        v = gate(d, "auto_publish")
        assert v.allowed

    def test_accepts_orm_row(self):
        class Row:
            plan = "pro"
            status = "active"
            expires_at = None

        v = gate(Row(), "auto_publish")
        assert v.allowed

    def test_is_active_helper(self):
        assert is_active(self._sub("pro")) is True
        assert is_active(self._sub("pro", "expired")) is False
        assert is_active(self._sub("free")) is True  # free is always active


class TestPlanCatalogue:
    def test_all_plans_present(self):
        for plan in (Plan.FREE, Plan.BASIC, Plan.PRO, Plan.CREATOR):
            assert plan.value in PLAN_CATALOGUE

    def test_pricing_per_doc(self):
        # docs §48: free 0 / basic 29 / pro 59 / creator 129
        assert get_plan_profile("free").price_cny == 0.0
        assert get_plan_profile("basic").price_cny == 29.0
        assert get_plan_profile("pro").price_cny == 59.0
        assert get_plan_profile("creator").price_cny == 129.0

    def test_unknown_plan_falls_back_to_free(self):
        p = get_plan_profile("nonexistent")
        assert p.code == "free"
        assert p.price_cny == 0.0

    def test_pro_has_auto_publish(self):
        assert get_plan_profile("pro").auto_publish

    def test_creator_unlimited(self):
        p = get_plan_profile("creator")
        assert p.daily_signals >= 1_000_000
        assert p.research_requests >= 1_000_000
        assert p.content_pieces >= 1_000_000


# ---------------------------------------------------------------------------
# Audit service
# ---------------------------------------------------------------------------
class TestAuditService:
    def test_record_basic(self):
        svc = AuditService()
        entry = svc.record(
            actor_type=ActorType.ADMIN.value,
            action=AuditAction.PUBLISH.value,
            actor_id="ou_admin_1",
            resource_type="notification",
            resource_id="123",
        )
        assert entry.actor_type == "admin"
        assert entry.action == "publish"
        assert svc.count() == 1

    def test_record_publish_helper(self):
        svc = AuditService()
        svc.record_publish(
            actor_id="ou_admin_1",
            notification_id=42,
            channel="wechat_article",
            success=True,
            external_id="wechat:abc",
        )
        recent = svc.recent()
        assert len(recent) == 1
        assert recent[0].metadata["channel"] == "wechat_article"

    def test_record_rbac_deny(self):
        svc = AuditService()
        svc.record_rbac_deny(
            actor_id="ou_user_9",
            command="/admin refresh",
            required_role="admin",
        )
        recent = svc.recent(action="rbac_deny")
        assert len(recent) == 1
        assert recent[0].result == AuditResult.BLOCKED.value

    def test_record_compliance_block(self):
        svc = AuditService()
        svc.record_compliance_block(
            actor_id="system",
            resource_type="notification",
            resource_id="99",
            reason="pii_leak",
            risk_score=0.85,
        )
        recent = svc.recent(action="compliance_block")
        assert len(recent) == 1
        assert recent[0].metadata["risk_score"] == 0.85

    def test_ring_buffer_caps(self):
        svc = AuditService(max_buffer=5)
        for i in range(20):
            svc.record("system", "publish", actor_id=f"a{i}")
        assert svc.count() == 5

    def test_recent_filter(self):
        svc = AuditService()
        svc.record("system", "publish", actor_id="a")
        svc.record("system", "reject", actor_id="b")
        svc.record("system", "publish", actor_id="c")
        publish = svc.recent(action="publish")
        assert len(publish) == 2
        assert all(e.action == "publish" for e in publish)

    def test_to_dict_serializable(self):
        import json

        svc = AuditService()
        svc.record("admin", "publish", actor_id="x", resource_id="1")
        d = svc.recent()[0].to_dict()
        json.dumps(d)  # must not raise

    def test_default_service_singleton(self):
        a = default_audit()
        b = default_audit()
        assert a is b

    def test_reset_default_service(self):
        a = default_audit()
        reset_default_service()
        b = default_audit()
        assert a is not b