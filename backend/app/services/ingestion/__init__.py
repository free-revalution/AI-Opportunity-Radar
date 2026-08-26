"""Ingestion service package."""

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.github import GitHubTrendingConnector
from app.services.ingestion.hackernews import HackerNewsConnector
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
from app.services.ingestion.youtube import YouTubeConnector

__all__ = [
    "GitHubTrendingConnector",
    "HackerNewsConnector",
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
    "YouTubeConnector",
    "build_connector",
    "list_all",
    "list_enabled",
    "registry_as_dict",
]