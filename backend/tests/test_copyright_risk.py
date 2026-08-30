"""Phase 24 split — Copyright risk detector tests."""

from __future__ import annotations

from app.services.compliance.copyright_risk import scan_copyright


class TestCopyrightRisk:
    def test_empty_output(self):
        result = scan_copyright("", "source text")
        assert result.copy_blocks == []
        assert result.raw_score == 0.0

    def test_clean_short_output(self):
        result = scan_copyright(
            "Short summary with no source match.",
            "a completely different source",
        )
        assert result.copy_blocks == []

    def test_verbatim_4_sentences_flagged(self):
        source = "A. B. C. D. E. F."
        # Re-use the first 4 sentences verbatim.
        output = "A. B. C. D. Then my own conclusion."
        result = scan_copyright(output, source)
        assert any(b.length_sentences >= 4 for b in result.copy_blocks)
        assert result.is_high_risk

    def test_long_block_flagged(self):
        sentence = "This is a fairly long sentence that should match on its own length. "
        source = sentence * 3
        output = "Intro line. " + sentence * 3 + " Outro."
        result = scan_copyright(output, source)
        # 3 consecutive matching sentences pass the MIN_COPY_RUN_SENTENCES
        # threshold; the matching block is ~213 chars total.
        assert any(b.length_sentences >= 3 for b in result.copy_blocks)
        assert any(b.length_chars >= 180 for b in result.copy_blocks)

    def test_no_source_long_no_citation_medium(self):
        # Long marketing copy without a citation link = soft risk.
        output = ("Buy now! " * 100)  # ~900 chars
        result = scan_copyright(output, source=None)
        assert result.raw_score > 0.0
        assert not result.is_high_risk

    def test_no_source_with_citation_safe(self):
        output = ("We explain it well. " * 30) + " See https://example.com/source"
        result = scan_copyright(output, source=None)
        assert result.raw_score == 0.0

    def test_chinese_source_verbatim(self):
        source = "今天北京天气晴朗。气温二十五度。适合户外运动。空气质量良好。"
        output = "新闻摘要:今天北京天气晴朗。气温二十五度。适合户外运动。空气质量良好。我的评价是X。"
        result = scan_copyright(output, source)
        # 4 matching sentences pass the >= 3 threshold.
        assert any(b.length_sentences >= 3 for b in result.copy_blocks)

    def test_summary_alone_safe(self):
        source = "Long article. " * 50
        output = "In short: the company announced a new product."
        result = scan_copyright(output, source)
        assert result.copy_blocks == []
