"""Phase 25 v2.1 — new source connectors smoke tests.

Each connector is exercised in **mock mode** (the default in tests)
to verify the constructor + ``source`` slug + ``fetch()`` shape.
The ``REGISTRY`` test asserts every new slug is wired into
``build_connector`` and that ``registry_as_dict`` exposes the
operator-facing metadata.

We deliberately do NOT hit live upstream APIs in unit tests —
the connector real-mode fetches rely on `httpx.MockTransport` in
the integration suite (see ``test_connectors_live_transport.py``
if/when added).
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.ingestion.amazon_best import AmazonBestSellersConnector
from app.services.ingestion.arxiv import ArxivConnector
from app.services.ingestion.douyin import DouyinConnector
from app.services.ingestion.huggingface import HuggingFaceConnector
from app.services.ingestion.registry import (
    REGISTRY,
    build_connector,
    registry_as_dict,
)
from app.services.ingestion.wallstreetcn_hot import WallStreetCNHotConnector
from app.services.ingestion.weibo import WeiboConnector
from app.services.ingestion.zhihu import ZhihuConnector


# ---------------------------------------------------------------------------
# Connector-level mock tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_arxiv_connector_mock_returns_items() -> None:
    c = ArxivConnector(mock=True)
    result = await c.fetch()
    assert result.source == "arxiv"
    assert len(result.items) >= 1
    assert all(it.metadata.get("category") == "tech/ai" for it in result.items)


@pytest.mark.asyncio
async def test_huggingface_connector_mock_returns_items() -> None:
    c = HuggingFaceConnector(mock=True)
    result = await c.fetch()
    assert result.source == "huggingface"
    assert len(result.items) >= 1
    for item in result.items:
        assert "downloads" in item.metadata
        assert item.metadata["category"] == "tech/ai"


@pytest.mark.asyncio
async def test_douyin_connector_mock_returns_items() -> None:
    c = DouyinConnector(mock=True)
    result = await c.fetch()
    assert result.source == "douyin"
    assert len(result.items) >= 1
    assert all(it.metadata.get("category") == "social/hot" for it in result.items)


@pytest.mark.asyncio
async def test_weibo_connector_mock_returns_items() -> None:
    c = WeiboConnector(mock=True)
    result = await c.fetch()
    assert result.source == "weibo"
    assert len(result.items) >= 1
    assert all(it.metadata.get("category") == "social/hot" for it in result.items)


@pytest.mark.asyncio
async def test_zhihu_connector_mock_returns_items() -> None:
    c = ZhihuConnector(mock=True)
    result = await c.fetch()
    assert result.source == "zhihu"
    assert len(result.items) >= 1


@pytest.mark.asyncio
async def test_amazon_best_connector_mock_returns_items() -> None:
    c = AmazonBestSellersConnector(mock=True)
    result = await c.fetch()
    assert result.source == "amazon_best"
    assert len(result.items) >= 1
    assert all(it.metadata.get("category") == "ecommerce/amazon" for it in result.items)


@pytest.mark.asyncio
async def test_wallstreetcn_hot_connector_mock_returns_items() -> None:
    c = WallStreetCNHotConnector(mock=True)
    result = await c.fetch()
    assert result.source == "wallstreetcn_hot"
    assert len(result.items) >= 1
    assert all(it.metadata.get("category") == "finance/cn" for it in result.items)


# ---------------------------------------------------------------------------
# Registry wiring — every new slug must be buildable
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        "arxiv",
        "huggingface",
        "douyin",
        "weibo",
        "zhihu",
        "amazon_best",
        "wallstreetcn_hot",
    ],
)
def test_registry_includes_new_slug(slug: str) -> None:
    assert slug in REGISTRY, f"{slug} missing from REGISTRY"


@pytest.mark.parametrize(
    "slug",
    [
        "arxiv",
        "huggingface",
        "douyin",
        "weibo",
        "zhihu",
        "amazon_best",
        "wallstreetcn_hot",
    ],
)
def test_build_connector_returns_instance(slug: str) -> None:
    """``build_connector`` returns a live instance in mock mode."""
    settings = get_settings()
    settings.mock_external_services = True
    connector = build_connector(slug, settings)
    assert connector.source == slug


def test_registry_as_dict_includes_new_sources() -> None:
    as_dict = registry_as_dict()
    for slug in (
        "arxiv",
        "huggingface",
        "douyin",
        "weibo",
        "zhihu",
        "amazon_best",
        "wallstreetcn_hot",
    ):
        assert slug in as_dict
        assert as_dict[slug]["slug"] == slug
        assert as_dict[slug]["default_interval_minutes"] > 0
