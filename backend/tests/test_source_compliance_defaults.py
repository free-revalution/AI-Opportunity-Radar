"""Phase 29 — source compliance default + RSS feed audit.

These tests pin the curated compliance defaults that ship with every
ingestion connector. Without them the pre-fetch gate would block the
connector on first run (default compliance_level='E'), the bot would
report "尚未采集" forever, and the operator would have to manually
flip compliance levels in the admin console before any data flows.

Two halves:
  * source_compliance — every registered slug has a known-good
    default, with ``github`` / ``hackernews`` / ``producthunt`` /
    ``arxiv`` / ``huggingface`` / ``rss`` set to ``A`` (public /
    official API), reddit/youtube to ``B`` (public with ToS caveats),
    and douyin/weibo/zhihu left at ``E`` (ToS forbids scraping).
  * RSS feeds — the live URLs probed during the audit; if any of
    them silently go stale the operator needs an alarm.
"""

from __future__ import annotations

import pytest

from app.services.ingestion.source_compliance import (
    SOURCE_COMPLIANCE_DEFAULTS,
    get_default,
)
from app.services.ingestion.registry import REGISTRY


# ---------------------------------------------------------------------------
# source_compliance.defaults
# ---------------------------------------------------------------------------
def test_every_registry_slug_has_a_compliance_default() -> None:
    """If a slug is registered but has no default, the connector will
    be blocked on first run (default E). Operators can still override
    in admin, but the curated baseline should cover every slug."""
    missing = set(REGISTRY) - set(SOURCE_COMPLIANCE_DEFAULTS)
    assert not missing, (
        f"connectors without a compliance default: {sorted(missing)}. "
        f"Add an entry to SOURCE_COMPLIANCE_DEFAULTS."
    )


@pytest.mark.parametrize(
    "slug,expected_level",
    [
        ("github", "A"),       # GitHub Search API — public unauth
        ("hackernews", "A"),   # Firebase API — open
        ("producthunt", "A"),  # Public GraphQL/REST
        ("arxiv", "A"),        # Public preprint server
        ("huggingface", "A"),  # Public Hub API
        ("rss", "A"),          # RSS feeds are openly published
        ("reddit", "B"),       # Public JSON, but ToS wants OAuth for commercial
        ("youtube", "B"),      # Data API v3 needs a key for >quotas
        ("douyin", "E"),       # ToS forbids scraping
        ("weibo", "E"),        # ToS forbids scraping
        ("zhihu", "E"),        # ToS forbids scraping
        ("amazon_best", "C"),  # ToS unclear — flagged for human review
        ("wallstreetcn_hot", "C"),  # ToS unclear — flagged for human review
    ],
)
def test_source_compliance_levels(slug: str, expected_level: str) -> None:
    """Pin the curated compliance levels so a careless refactor can't
    silently block / unblock a connector."""
    default = get_default(slug)
    assert default is not None, f"no compliance default for {slug!r}"
    assert default.compliance_level == expected_level


def test_github_default_carries_terms_and_robots() -> None:
    """The GitHub default must carry a terms_url so the audit log can
    show *why* we allowed the connector."""
    default = get_default("github")
    assert default is not None
    assert default.terms_url is not None
    assert default.terms_url.startswith("https://")
    assert default.commercial_use_status == "allowed"


def test_reddit_default_is_conditional_commercial_use() -> None:
    """Reddit ToS explicitly requires OAuth2 for commercial use, so
    the default must say ``conditional`` and ``B`` — not A."""
    default = get_default("reddit")
    assert default is not None
    assert default.compliance_level == "B"
    assert default.commercial_use_status == "conditional"


def test_unknown_slug_returns_none_default() -> None:
    """An unknown slug must NOT have a default — caller falls back to
    the conservative ``E``."""
    assert get_default("nonexistent-source-slug") is None


# ---------------------------------------------------------------------------
# RSS feed manifest — probed-live URL sanity
# ---------------------------------------------------------------------------
def test_rss_default_feeds_all_use_https() -> None:
    """All feeds must be HTTPS — plain HTTP feeds are vulnerable to
    in-flight tampering and many operators block them at egress."""
    from app.services.ingestion.rss import DEFAULT_FEEDS

    for name, url, _category in DEFAULT_FEEDS:
        assert url.startswith("https://"), (
            f"feed {name!r} uses non-HTTPS URL: {url}"
        )


def test_rss_default_feeds_have_no_duplicates() -> None:
    """A duplicated URL means two concurrent fetches for the same
    content — wastes quota and risks rate-limits."""
    from app.services.ingestion.rss import DEFAULT_FEEDS

    urls = [url for _name, url, _category in DEFAULT_FEEDS]
    duplicates = {u for u in urls if urls.count(u) > 1}
    assert not duplicates, f"duplicate feed URLs: {duplicates}"