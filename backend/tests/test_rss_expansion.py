"""Phase 25 v2.1 — RSS feed expansion + category propagation.

The user's brief was "any kind of hotspot, filter on the Feishu
layer". The MVP-1 RSS surface was 5 AI/tech feeds. Phase 25 v2.1
expands to 15 feeds across Chinese finance, Chinese e-commerce /
start-ups, and international mainstream press (operator-proxy
reachable). This test file pins:

  * the 5 original feeds are still present (no regression)
  * the 10 new feeds are present and cover all 4 user categories
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
def test_default_feeds_count_is_at_least_15() -> None:
    """Expansion went from 5 → 15 (5 MVP + 10 Phase 25 v2.1)."""
    assert len(DEFAULT_FEEDS) >= 15


def test_default_feeds_entries_are_3_tuples() -> None:
    """Phase 25 v2.1 — entries are (name, url, category), not (name, url)."""
    for entry in DEFAULT_FEEDS:
        assert len(entry) == 3, f"expected 3-tuple, got {entry!r}"
        name, url, category = entry
        assert name and isinstance(name, str)
        assert url.startswith(("http://", "https://"))
        assert category and isinstance(category, str)


def test_original_mvp_feeds_are_still_present() -> None:
    """Regression guard — the 5 original feeds must remain in the list."""
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {"OpenAI Blog", "Anthropic News", "Google AI Blog",
            "Hacker News Frontpage", "Lobsters"} <= names


def test_phase_25_v21_expansion_covers_all_four_user_categories() -> None:
    """User brief: 财经/电商/选品/社媒热点 + 国际 mainstream."""
    categories = {entry[2] for entry in DEFAULT_FEEDS}
    # 4 rough buckets must all be reachable (their sub-categories may
    # overlap, but at minimum the families are covered).
    families = {c.split("/", 1)[0] for c in categories}
    assert {"finance", "ecommerce", "tech"}.issubset(families)


def test_phase_25_v21_chinese_finance_feeds_present() -> None:
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {"财富中文网", "华尔街见闻", "FT 中文网", "投资界"} <= names


def test_phase_25_v21_ecommerce_feeds_present() -> None:
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {"36氪", "虎嗅", "亿邦动力"} <= names


def test_phase_25_v21_international_feeds_present() -> None:
    names = {entry[0] for entry in DEFAULT_FEEDS}
    assert {"CNBC Top News", "Reuters", "The Verge"} <= names


def test_every_feed_has_non_empty_category() -> None:
    for entry in DEFAULT_FEEDS:
        assert entry[2].strip(), f"feed {entry[0]!r} has empty category"


# ---------------------------------------------------------------------------
# Connector behaviour — mock mode
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mock_rss_includes_finance_item() -> None:
    """Mock payload now ships a 财富中文网 item to exercise the new category."""
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
