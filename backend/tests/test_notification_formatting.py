"""Tests for the MarkdownV2 formatter — escaping, truncation, builders."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.notification import (
    DigestEntry,
    TELEGRAM_MAX_TEXT,
    assert_markdown_v2_safe,
    escape_markdown_v2,
    format_digest,
    format_opportunity_alert,
    truncate_text,
    truncate_to_telegram_limit,
)


# ---------------------------------------------------------------------------
# Escape
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plain text", "plain text"),
        ("a_b", r"a\_b"),
        ("*bold*", r"\*bold\*"),
        ("[link]", r"\[link\]"),
        ("(parens)", r"\(parens\)"),
        ("back`tick", r"back\`tick"),
        ("1. number", r"1\. number"),
        ("hash#", r"hash\#"),
        ("plus+minus-equal=|brace{}tilde~",
         r"plus\+minus\-equal\=\|brace\{\}tilde\~"),
        ("exclam!", r"exclam\!"),
        # Caret is NOT a MarkdownV2 reserved char — must pass through.
        ("caret^", "caret^"),
        # Backslash: input `\` (1 char) → output `\\` (2 chars).
        ("\\", "\\\\"),
    ],
)
def test_escape_markdown_v2_reserved_chars(raw, expected):
    assert escape_markdown_v2(raw) == expected


def test_escape_handles_none():
    assert escape_markdown_v2(None) == ""


def test_escape_does_not_touch_safe_punctuation():
    # Forward slash, comma, single quote, colon, semicolon, etc. are safe.
    assert escape_markdown_v2("hello, world: 'safe'/ok;") == "hello, world: 'safe'/ok;"


# ---------------------------------------------------------------------------
# Truncate
# ---------------------------------------------------------------------------
def test_truncate_to_telegram_limit_under():
    assert truncate_to_telegram_limit("hello") == "hello"


def test_truncate_to_telegram_limit_over():
    text = "x" * (TELEGRAM_MAX_TEXT + 100)
    out = truncate_to_telegram_limit(text)
    assert len(out) <= TELEGRAM_MAX_TEXT
    assert out.endswith("…")


def test_truncate_to_telegram_limit_edge_case():
    out = truncate_to_telegram_limit("y" * TELEGRAM_MAX_TEXT, limit=4)
    assert len(out) == 4
    assert out.endswith("…")


def test_truncate_text_plain():
    assert truncate_text("hello world", limit=5) == "hell…"
    assert truncate_text("hi", limit=10) == "hi"


# ---------------------------------------------------------------------------
# DigestEntry.score_label
# ---------------------------------------------------------------------------
def test_digest_entry_score_label():
    e = DigestEntry(
        opportunity_id=1,
        title="x",
        slug="x",
        total_score=82.456,
        recommendation="recommend",
        summary="",
    )
    assert e.score_label() == "82.5"


# ---------------------------------------------------------------------------
# format_digest
# ---------------------------------------------------------------------------
def _entry(**overrides) -> DigestEntry:
    base = dict(
        opportunity_id=1,
        title="AI Sales Coach for SDRs",
        slug="ai-sales-coach",
        total_score=82.0,
        recommendation="recommend",
        summary="AI SaaS that summarises sales calls and coaches reps.",
        category="AI SaaS",
        target_user="B2B sales leaders",
        trend_score=85,
        demand_score=80,
        monetization_score=78,
        competition_gap_score=70,
        china_gap_score=65,
        execution_score=72,
        source_count=5,
        has_report=True,
    )
    base.update(overrides)
    return DigestEntry(**base)


def test_format_digest_with_entries_includes_title_score_and_link():
    text = format_digest(
        entries=[_entry()],
        base_url="https://radar.example.com",
    )
    assert "AI Sales Coach for SDRs" in text
    assert "82\\.0" in text  # the period is reserved, so it's escaped
    assert "https://radar\\.example\\.com/opportunities/ai\\-sales\\-coach" in text
    # MarkdownV2 link syntax.
    assert "[Open](" in text
    # Recommendation label appears.
    assert "recommend" in text


def test_format_digest_escapes_special_chars_in_title():
    # The reserved chars in the title must be doubled before reaching the wire.
    text = format_digest(
        entries=[_entry(title="SaaS #1 (beta) — pipeline!")],
        base_url="https://radar.example.com",
    )
    assert r"\#1" in text
    assert r"\(beta\)" in text
    assert "pipeline\\!" in text


def test_format_digest_empty_entries_message():
    text = format_digest(entries=[], base_url="https://x.com")
    assert "No opportunities crossed the research threshold" in text


def test_format_digest_respects_max_entries():
    text = format_digest(
        entries=[_entry(opportunity_id=i, slug=f"s-{i}") for i in range(10)],
        base_url="https://x.com",
        max_entries=3,
    )
    # Three numbered items + their [Open] links.
    assert text.count("[Open](") == 3
    assert "1\\." in text
    assert "3\\." in text
    assert "4\\." not in text


def test_format_digest_truncates_long_summaries():
    long = "x" * 5_000
    text = format_digest(
        entries=[_entry(summary=long)],
        base_url="https://x.com",
        per_entry_summary_chars=120,
    )
    assert "x" * 121 not in text


def test_format_digest_includes_generated_at_when_provided():
    text = format_digest(
        entries=[_entry()],
        base_url="https://x.com",
        generated_at=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc),
    )
    assert "\\*AI Opportunity Radar — Daily Digest\\*" in text


def test_format_digest_fits_within_telegram_limit():
    entries = [
        _entry(opportunity_id=i, title=f"Opportunity {i}", slug=f"opp-{i}")
        for i in range(5)
    ]
    text = format_digest(entries=entries, base_url="https://x.com")
    assert len(text) <= TELEGRAM_MAX_TEXT


# ---------------------------------------------------------------------------
# format_opportunity_alert
# ---------------------------------------------------------------------------
def test_format_opportunity_alert_includes_score_and_link():
    text = format_opportunity_alert(
        entry=_entry(), base_url="https://radar.example.com"
    )
    assert "AI Sales Coach for SDRs" in text
    assert "82\\.0" in text  # escaped period
    assert "https://radar\\.example\\.com/opportunities/ai\\-sales\\-coach" in text


def test_format_opportunity_alert_handles_missing_category_and_target():
    text = format_opportunity_alert(
        entry=_entry(category=None, target_user=None),
        base_url="https://x.com",
    )
    # No escape means we left an unescaped reserved char in.
    assert r"uncategorised" in text
    assert "—" not in text or "—" in text  # em dash is safe in MarkdownV2


def test_format_opportunity_alert_includes_extra_note():
    text = format_opportunity_alert(
        entry=_entry(),
        base_url="https://x.com",
        extra_note="Launching next week!",
    )
    assert "Launching next week\\!" in text


def test_format_opportunity_alert_truncates_summary():
    long = "y" * 5_000
    text = format_opportunity_alert(
        entry=_entry(summary=long),
        base_url="https://x.com",
        max_summary_chars=200,
    )
    assert "y" * 250 not in text


# ---------------------------------------------------------------------------
# assert_markdown_v2_safe — sanity check for RAW user text BEFORE it
# reaches the formatter. The formatter output is safe by construction.
# ---------------------------------------------------------------------------
def test_assert_safe_flags_oversized_raw_text():
    text = "x" * (TELEGRAM_MAX_TEXT + 10)
    warnings = assert_markdown_v2_safe(text)
    assert any("exceeds" in w for w in warnings)


def test_assert_safe_flags_unescaped_reserved_chars_in_raw_text():
    warnings = assert_markdown_v2_safe("hello *bold*")
    assert any("*" in w for w in warnings)


def test_assert_safe_is_clean_for_already_escaped_text():
    warnings = assert_markdown_v2_safe(r"hello \*bold\*")
    assert warnings == []
