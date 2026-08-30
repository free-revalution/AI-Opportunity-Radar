"""Phase 24 split — Source policy evaluator tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.compliance.source_policy import (
    BlockReason,
    SourcePolicyRecord,
    evaluate_source_policy,
)


class TestSourcePolicy:
    def _record(self, **kwargs):
        return SourcePolicyRecord(source_id=1, name="x", **kwargs)

    def test_level_a_allow(self):
        r = self._record(compliance_level="A")
        f = evaluate_source_policy(r)
        assert f.allowed is True
        assert f.risk_score < 0.1
        assert "official" in f.reason

    def test_level_b_allow_low_risk(self):
        r = self._record(compliance_level="B")
        f = evaluate_source_policy(r)
        assert f.allowed is True
        assert f.risk_score < 0.3

    def test_level_c_manual_review(self):
        r = self._record(compliance_level="C")
        f = evaluate_source_policy(r)
        assert f.allowed is False
        assert f.requires_human_review is True

    def test_level_d_block(self):
        r = self._record(compliance_level="D")
        f = evaluate_source_policy(r)
        assert f.allowed is False
        assert f.risk_score > 0.5

    def test_level_e_block(self):
        r = self._record(compliance_level="E")
        f = evaluate_source_policy(r)
        assert f.allowed is False

    def test_disabled_source_blocks(self):
        r = self._record(compliance_level="A", enabled=False)
        f = evaluate_source_policy(r)
        assert f.allowed is False
        assert f.reason == "source_disabled"

    def test_recent_block_forces_block(self):
        r = self._record(
            compliance_level="A",
            last_block_reason=BlockReason.CAPTCHA.value,
        )
        f = evaluate_source_policy(r)
        assert f.allowed is False
        assert "captcha" in f.reason

    def test_stale_check_triggers_review(self):
        old = datetime.now(tz=timezone.utc) - timedelta(days=120)
        r = self._record(compliance_level="B", last_compliance_check=old)
        f = evaluate_source_policy(r)
        assert f.allowed is True
        assert f.requires_human_review is True
        assert "stale" in f.reason

    def test_unknown_level_treated_as_block(self):
        r = self._record(compliance_level="Z")
        f = evaluate_source_policy(r)
        assert f.allowed is False
        assert "blocked" in f.reason

    def test_metadata_carries_record(self):
        r = self._record(compliance_level="A", access_method="official_api")
        f = evaluate_source_policy(r)
        assert f.policy_metadata["compliance_level"] == "A"
        assert f.policy_metadata["access_method"] == "official_api"
