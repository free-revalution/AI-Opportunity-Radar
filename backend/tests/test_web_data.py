"""Tests for the deterministic MockWebDataProvider + factory selection."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.research import (
    FallbackWebDataProvider,
    MockWebDataProvider,
    SourceDoc,
    WebDataProvider,
    build_web_data_provider,
)


# ---------------------------------------------------------------------------
# SourceDoc helpers
# ---------------------------------------------------------------------------
def test_source_doc_excerpt_short_text():
    doc = SourceDoc(url="https://a.com", title="A", content="short")
    assert doc.excerpt == "short"


def test_source_doc_excerpt_truncates_long_text():
    long = "x" * 1000
    doc = SourceDoc(url="https://a.com", title="A", content=long)
    assert doc.excerpt.endswith("...")
    assert len(doc.excerpt) == 280


def test_source_doc_to_dict_includes_excerpt_and_provider():
    doc = SourceDoc(
        url="https://a.com",
        title="T",
        content="hello world",
        via_provider="mock",
    )
    d = doc.to_dict()
    assert d["url"] == "https://a.com"
    assert d["excerpt"] == "hello world"
    assert d["via_provider"] == "mock"
    assert "fetched_at" in d


# ---------------------------------------------------------------------------
# Mock provider behaviour
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mock_search_returns_n_results():
    provider = MockWebDataProvider()
    docs = await provider.search("AI SaaS for sales", limit=5)
    assert len(docs) == 5
    assert all(isinstance(d, SourceDoc) for d in docs)
    assert all(d.via_provider == "mock" for d in docs)


@pytest.mark.asyncio
async def test_mock_search_is_deterministic_for_same_query():
    p = MockWebDataProvider()
    a = await p.search("AI sales coach", limit=4)
    b = await p.search("AI sales coach", limit=4)
    assert [d.url for d in a] == [d.url for d in b]


@pytest.mark.asyncio
async def test_mock_search_query_keywords_appear_in_content():
    p = MockWebDataProvider()
    docs = await p.search("renewable energy storage", limit=3)
    for d in docs:
        assert "renewable energy storage" in d.content.lower()


@pytest.mark.asyncio
async def test_mock_scrape_extracts_hostname():
    p = MockWebDataProvider()
    doc = await p.scrape("https://www.example.com/some/path")
    assert "example.com" in doc.title.lower()
    assert doc.via_provider == "mock"
    assert doc.url == "https://www.example.com/some/path"


@pytest.mark.asyncio
async def test_mock_scrape_many_runs_sequentially_by_default():
    p = MockWebDataProvider()
    docs = await p.scrape_many(
        ["https://a.com/x", "https://b.com/y"], max_concurrency=2
    )
    assert len(docs) == 2
    assert docs[0].url == "https://a.com/x"
    assert docs[1].url == "https://b.com/y"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_factory_returns_mock_when_mocks_enabled(settings):
    settings.mock_external_services = True
    provider = build_web_data_provider(settings)
    assert isinstance(provider, MockWebDataProvider)
    assert isinstance(provider, WebDataProvider)


def test_factory_returns_mock_when_no_firecrawl_key(settings):
    settings.mock_external_services = False
    settings.firecrawl_api_key = ""
    settings.browser_use_api_key = ""
    provider = build_web_data_provider(settings)
    assert isinstance(provider, MockWebDataProvider)


def test_factory_returns_firecrawl_when_key_present(settings):
    from app.services.research.firecrawl_provider import FirecrawlWebDataProvider

    settings.mock_external_services = False
    settings.firecrawl_api_key = "fc_test_key"
    settings.firecrawl_api_url = "https://api.firecrawl.dev"
    settings.browser_use_api_key = ""
    provider = build_web_data_provider(settings, prefer="firecrawl")
    # The factory wraps a single-backend chain in FallbackWebDataProvider
    # so the chain stays uniform — but with only one real provider plus
    # the always-on Mock, it collapses to two steps.
    assert isinstance(provider, FallbackWebDataProvider)
    chain = provider.chain
    assert "firecrawl" in chain
    assert "mock" in chain
    fc_providers = [
        p for p in chain if p == "firecrawl"
    ]
    assert fc_providers  # at least one firecrawl step
    # Inspect the underlying composition directly.
    assert any(
        isinstance(p, FirecrawlWebDataProvider)
        for p in getattr(provider, "_providers", [])
    )


def test_factory_prefer_unknown_falls_back_to_mock(settings):
    settings.mock_external_services = False
    settings.firecrawl_api_key = ""
    settings.browser_use_api_key = ""
    provider = build_web_data_provider(settings, prefer="browser_use")
    assert isinstance(provider, MockWebDataProvider)


def test_factory_returns_browser_use_chain_when_key_present(settings):
    from app.services.research.browser_use_provider import BrowserUseWebDataProvider

    settings.mock_external_services = False
    settings.browser_use_api_key = "bu_test_key"
    settings.browser_use_api_url = "https://api.browser-use.com"
    settings.firecrawl_api_key = ""
    provider = build_web_data_provider(settings, prefer="browser_use")
    assert isinstance(provider, FallbackWebDataProvider)
    assert provider.chain[0] == "browser_use"
    assert "mock" in provider.chain
    assert any(
        isinstance(p, BrowserUseWebDataProvider)
        for p in getattr(provider, "_providers", [])
    )


def test_factory_browser_use_prefer_includes_firecrawl_fallback(settings):
    """When both keys are set and prefer='browser_use', the chain is BU → FC → mock."""
    settings.mock_external_services = False
    settings.browser_use_api_key = "bu_test"
    settings.browser_use_api_url = "https://api.browser-use.com"
    settings.firecrawl_api_key = "fc_test"
    settings.firecrawl_api_url = "https://api.firecrawl.dev"
    provider = build_web_data_provider(settings, prefer="browser_use")
    assert isinstance(provider, FallbackWebDataProvider)
    assert provider.chain == ["browser_use", "firecrawl", "mock"]


def test_factory_auto_with_both_keys_orders_browser_use_first(settings):
    settings.mock_external_services = False
    settings.browser_use_api_key = "bu_test"
    settings.browser_use_api_url = "https://api.browser-use.com"
    settings.firecrawl_api_key = "fc_test"
    settings.firecrawl_api_url = "https://api.firecrawl.dev"
    provider = build_web_data_provider(settings, prefer="auto")
    assert isinstance(provider, FallbackWebDataProvider)
    assert provider.chain == ["browser_use", "firecrawl", "mock"]


def test_factory_auto_with_only_firecrawl_key(settings):
    settings.mock_external_services = False
    settings.browser_use_api_key = ""
    settings.firecrawl_api_key = "fc_test"
    settings.firecrawl_api_url = "https://api.firecrawl.dev"
    provider = build_web_data_provider(settings, prefer="auto")
    assert isinstance(provider, FallbackWebDataProvider)
    assert provider.chain == ["firecrawl", "mock"]


def test_factory_auto_with_no_keys(settings):
    settings.mock_external_services = False
    settings.browser_use_api_key = ""
    settings.firecrawl_api_key = ""
    provider = build_web_data_provider(settings, prefer="auto")
    assert isinstance(provider, MockWebDataProvider)


# ---------------------------------------------------------------------------
# SourceDoc defaults
# ---------------------------------------------------------------------------
def test_source_doc_defaults_fetched_at():
    doc = SourceDoc(url="https://x.com", title="X", content="")
    assert isinstance(doc.fetched_at, datetime)
