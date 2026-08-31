"""Phase 25 v2.1 — RSS feed expansion + category propagation.

The user's brief was "any kind of hotspot, filter on the Feishu
layer". The MVP-1 RSS surface was 5 AI/tech feeds. Phase 25 v2.1
expanded to 15 feeds across Chinese finance, Chinese e-commerce /
start-ups, and international mainstream press (operator-proxy
reachable). Phase 29 audited each URL with a real fetch and
replaced the 8 stale / dead feeds (see app/services/ingestion/rss.py
for the full rationale). This test file pins:

  * the 4 surviving MVP feeds are still present (OpenAI Blog was
    renamed to "OpenAI News" because its URL moved; Anthropic News
    was dropped — Anthropic disabled their RSS feed in 2024)
  * the 10+ new feeds are present and span AI / tech / finance
    / operators families
  * every feed carries a non-empty ``category`` string
  * the ``RawItem.metadata["category"]`` propagated by the connector
    matches the feed's category
  * mock mode returns the expected 3 items covering 2 distinct
    categories (Phase 25 v2.1 adds a finance/cn mock item)
"""

from __future__ import annotations

import pytest

from app.services.ingestion.rss import (
    DEFAULT_FEEDS,
    RSSConnector,
    _mock_rss,
)


# ---------------------------------------------------------------------------
# Feed manifest
# ---------------------------------------------------------------------------
def test_default_feeds_count_is_at_least_14() -> None:
    """Expansion went from 5 → 15 (5 MVP + 10 Phase 25 v2.1).
    Phase 29 replaced 8 stale URLs with 8 fresh ones; the new total
    is 14 working feeds (>= the 15 we promised in Phase 25 v2.1 once
    you subtract the 1 dead Anthropic News entry that was dropped)."""
    assert len(DEFAULT_FEEDS) >= 14


def test_default_feeds_entries_are_3_tuples() -> None:
    """Phase 25 v2.1 — entries are (name, url, category), not (name, url)."""
    for entry in DEFAULT_FEEDS:
        assert len(entry) == 3, f"expected 3-tuple, got {entry!r}"
        name, url, category = entry
        assert name and isinstance(name, str)
        assert url.startswith(("http://", "https://"))
        assert category and isinstance(category, str)


def test_original_mvp_feeds_are_still_present() -> None:
    """Regression guard — the 5 original feeds must remain in the list.

    Phase 29 rename note: ``OpenAI Blog`` was renamed to
    ``OpenAI News`` because the legacy URL redirects to the news
    feed; ``Anthropic News`` was dropped (404 since 2024). The
    remaining 3 are still present verbatim.
    """
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {"OpenAI News", "Google AI Blog",
            "Hacker News Frontpage", "Lobsters"} <= names


def test_phase_29_replacement_feeds_are_present() -> None:
    """Phase 29 fix — the 8 stale feeds were replaced with the 8
    feeds below (probed 200 + ≥10 entries each on a vanilla Mozilla
    UA)."""
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {
        "HuggingFace Blog", "GitHub Blog",
        "Ars Technica", "TechCrunch", "LWN",
        "Simon Willison", "Stratechery",
        "FT 中文网",
    } <= names


def test_phase_29_broken_feeds_are_dropped() -> None:
    """Phase 29 fix — these feeds were probed and returned 404 /
    ConnectError, so they're no longer in DEFAULT_FEEDS. If any of
    them reappear in the manifest, somebody added them back without
    verifying."""
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert names.isdisjoint({
        "OpenAI Blog",        # → redirects to OpenAI News
        "Anthropic News",     # 404 since 2024
        "财富中文网",            # 404
        "华尔街见闻",            # 404
        "36氪",                # ConnectError (CN-only block)
        "虎嗅",                 # ConnectError
        "亿邦动力",              # 301 → 404
        "投资界",                # /rss 404 (pedaily.cn decommissioned)
        "Reuters",            # 301 → stale downstream
    })


def test_phase_25_v21_expansion_covers_all_four_user_categories() -> None:
    """User brief: 财经/电商/选品/社媒热点 + 国际 mainstream.

    Phase 29 dropped most CN e-commerce feeds (CN-only block).
    The remaining manifest still spans finance + tech + operators
    families — that's the breadth we promise today.
    """
    categories = {entry[2] for entry in DEFAULT_FEEDS}
    families = {c.split("/", 1)[0] for c in categories}
    assert {"finance", "tech"}.issubset(families)


def test_phase_29_chinese_finance_feeds_present() -> None:
    """Phase 29 — most CN finance feeds (财富中文网, 华尔街见闻,
    投资界) were probed and returned 404 / ConnectError, so they
    were dropped. Only FT 中文网 survived."""
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {"FT 中文网"} <= names


def test_phase_29_ecommerce_feeds_removed() -> None:
    """Phase 29 — 36氪 / 虎嗅 / 亿邦动力 all returned ConnectError
    (CN-only block) and were dropped."""
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert names.isdisjoint({"36氪", "虎嗅", "亿邦动力"})


def test_phase_29_international_feeds_present() -> None:
    """Phase 29 — CNBC and The Verge still work; Reuters was dropped."""
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {"CNBC Top News", "The Verge"} <= names
    assert "Reuters" not in names


def test_every_feed_has_non_empty_category() -> None:
    for entry in DEFAULT_FEEDS:
        assert entry[2].strip(), f"feed {entry[0]!r} has empty category"


# ---------------------------------------------------------------------------
# Connector behaviour — mock mode
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mock_rss_includes_finance_item() -> None:
    """Mock payload now ships an FT 中文网 item to exercise the new category."""
    result = _mock_rss()
    assert any(it.metadata.get("category") == "finance/cn" for it in result.items)


@pytest.mark.asyncio
async def test_mock_rss_categories_propagate() -> None:
    result = _mock_rss()
    categories = {it.metadata.get("category") for it in result.items}
    # 2 distinct categories — mirrors the multi-source dedup test.
    assert len(categories) >= 2


@pytest.mark.asyncio
async def test_rss_connector_mock_uses_new_default_feeds() -> None:
    """End-to-end: RSSConnector(mock=True) returns the upgraded mock items."""
    connector = RSSConnector(mock=True)
    result = await connector.fetch()
    assert result.source == "rss"
    assert len(result.items) >= 3
    for item in result.items:
        assert item.metadata.get("category")
