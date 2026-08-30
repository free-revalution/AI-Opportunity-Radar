"""Ingestion service package."""

from app.services.ingestion.amazon_best import AmazonBestSellersConnector
from app.services.ingestion.arxiv import ArxivConnector
from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.douyin import DouyinConnector
from app.services.ingestion.github import GitHubTrendingConnector
from app.services.ingestion.hackernews import HackerNewsConnector
from app.services.ingestion.huggingface import HuggingFaceConnector
from app.services.ingestion.producthunt import ProductHuntConnector
from app.services.ingestion.raw_item import RawItem
from app.services.ingestion.reddit import RedditConnector
from app.services.ingestion.registry import (
    REGISTRY,
    SourceSpec,
    build_connector,
    list_all,
    list_enabled,
    registry_as_dict,
)
from app.services.ingestion.rss import RSSConnector
from app.services.ingestion.service import IngestionReport, IngestionService
from app.services.ingestion.wallstreetcn_hot import WallStreetCNHotConnector
from app.services.ingestion.weibo import WeiboConnector
from app.services.ingestion.youtube import YouTubeConnector
from app.services.ingestion.zhihu import ZhihuConnector

__all__ = [
    "AmazonBestSellersConnector",
    "ArxivConnector",
    "DouyinConnector",
    "GitHubTrendingConnector",
    "HackerNewsConnector",
    "HuggingFaceConnector",
    "IngestionReport",
    "IngestionService",
    "ProductHuntConnector",
    "REGISTRY",
    "RSSConnector",
    "RawItem",
    "RedditConnector",
    "SourceConnector",
    "SourceConnectorResult",
    "SourceSpec",
    "WallStreetCNHotConnector",
    "WeiboConnector",
    "YouTubeConnector",
    "ZhihuConnector",
    "build_connector",
    "list_all",
    "list_enabled",
    "registry_as_dict",
]