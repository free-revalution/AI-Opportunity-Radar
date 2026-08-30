"""Phase 24 split — PII detector tests.

Extracted from ``tests/test_compliance.py`` so failures in one detector
don't drag the rest down. The original file re-exports everything as a
backwards-compat shim.
"""

from __future__ import annotations

from app.services.compliance.pii_detector import (
    _id_check_digit_valid,
    has_pii,
    redact_pii,
    scan_pii,
)


class TestPiiDetector:
    def test_empty_returns_empty(self):
        assert scan_pii("").count == 0
        assert scan_pii(None or "").count == 0  # type: ignore[arg-type]
        assert has_pii("") is False

    def test_mainland_mobile(self):
        result = scan_pii("call 13812345678 anytime")
        assert result.count == 1
        assert result.findings[0].category == "mobile_cn"

    def test_hk_mobile_with_alpha_context(self):
        # 8-digit run inside a sentence with alpha — flagged.
        result = scan_pii("phone: 98765432 for HK rep")
        assert any(f.category == "mobile_hk" for f in result.findings)

    def test_email_detected(self):
        result = scan_pii("reach me at jane.doe+filter@example.co.uk please")
        assert any(f.category == "email" for f in result.findings)

    def test_email_at_path(self):
        # Avoid matching e.g. "this@home" without TLD.
        result = scan_pii("user said this@home was a typo")
        assert not any(f.category == "email" for f in result.findings)

    def test_id_18_with_valid_check_digit(self):
        # A known-valid 身份证号 (test fixture) — check digit calculated.
        # 11010519491231002X: weights sum -> check X.
        valid = "11010519491231002X"
        assert _id_check_digit_valid(valid) is True
        result = scan_pii(f"id is {valid}")
        assert any(f.category == "id_cn" for f in result.findings)
        assert result.has_high_risk

    def test_id_18_with_invalid_check_digit_rejected(self):
        invalid = "110105194912310026"  # last digit should be X
        assert _id_check_digit_valid(invalid) is False
        result = scan_pii(f"id is {invalid}")
        assert not any(f.category == "id_cn" for f in result.findings)

    def test_id_legacy_15_flagged(self):
        result = scan_pii("legacy id 110105491231002")
        assert any(f.category == "id_cn_legacy" for f in result.findings)

    def test_card_16_19_digits_flagged(self):
        result = scan_pii("card 6222021234567890123 thanks")
        assert any(f.category == "card" for f in result.findings)
        assert result.has_high_risk

    def test_address_with_keyword_flagged(self):
        result = scan_pii("ship to 北京市朝阳区建国路88号 please")
        assert any(f.category == "address" for f in result.findings)

    def test_wechat_id(self):
        result = scan_pii("wechat: Radar_Ops-2026")
        assert any(f.category == "wechat" for f in result.findings)

    def test_qq_with_prefix(self):
        result = scan_pii("QQ：1234567 hi")
        assert any(f.category == "qq" for f in result.findings)

    def test_redact_pii_replaces(self):
        text = "contact 13812345678 or jane@example.com"
        out = redact_pii(text)
        assert "13812345678" not in out
        assert "jane@example.com" not in out
        assert "[mobile_cn]" in out
        assert "[email]" in out

    def test_redact_pii_keeps_clean_text(self):
        assert redact_pii("no pii here") == "no pii here"

    def test_nested_overlap_suppression(self):
        # Email shouldn't double-match as wechat.
        result = scan_pii("hi name@example.com please")
        cats = [f.category for f in result.findings]
        assert "email" in cats
        assert cats.count("wechat") == 0
