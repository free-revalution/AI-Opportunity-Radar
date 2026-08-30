"""Phase 24 split — Content safety detector tests."""

from __future__ import annotations

from app.services.compliance.content_safety import scan_content_safety


class TestContentSafety:
    def test_clean_text_zero_score(self):
        result = scan_content_safety("This is a normal product review article.")
        assert result.raw_score == 0.0
        assert result.findings == []

    def test_financial_buy_signal(self):
        result = scan_content_safety("Now is the time to 抄底 AAPL!")
        assert result.is_high_risk
        assert any(f.category == "financial" for f in result.findings)

    def test_financial_target_price(self):
        result = scan_content_safety("目标价:120 元,买入评级。")
        assert any(f.category == "financial" for f in result.findings)

    def test_guaranteed_returns(self):
        result = scan_content_safety("guaranteed 10x returns in 30 days")
        assert any(f.category == "financial" for f in result.findings)

    def test_medical_prescription(self):
        result = scan_content_safety("确诊糖尿病后,处方开具二甲双胍,每日服用 500mg.")
        assert any(f.category == "medical" for f in result.findings)

    def test_political_overthrow(self):
        result = scan_content_safety("呼吁推翻现政府")
        assert any(f.category == "political" for f in result.findings)

    def test_illegal_synthesis(self):
        result = scan_content_safety("教大家如何制作冰毒 step by step")
        assert any(f.category == "illegal" for f in result.findings)

    def test_defamation_accusation(self):
        result = scan_content_safety("某某是骗子,大家小心。")
        assert any(f.category == "defamation" for f in result.findings)

    def test_multiple_categories_aggregate(self):
        # Financial + illegal triggers both.
        text = "guaranteed 10x returns. Also step-by-step how to make meth."
        result = scan_content_safety(text)
        cats = {f.category for f in result.findings}
        assert "financial" in cats
        assert "illegal" in cats

    def test_caps_at_one(self):
        # Force multiple hits — still ≤ 1.0.
        text = (
            "guaranteed 10x returns "
            "确诊糖尿病 prescription "
            "推翻政府 "
            "如何制作冰毒 step by step"
        )
        assert scan_content_safety(text).raw_score <= 1.0
