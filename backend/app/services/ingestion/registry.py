"""Source connector registry.

Maps a source slug to a configured connector instance. Used by the
ingestion service and exposed (read-only) at `/api/sources` so the
frontend can show what is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.services.ingestion.amazon_best import AmazonBestSellersConnector
from app.services.ingestion.arxiv import ArxivConnector
from app.services.ingestion.base import SourceConnector
from app.services.ingestion.douyin import DouyinConnector
from app.services.ingestion.github import GitHubTrendingConnector
from app.services.ingestion.hackernews import HackerNewsConnector
from app.services.ingestion.huggingface import HuggingFaceConnector
from app.services.ingestion.producthunt import ProductHuntConnector
from app.services.ingestion.reddit import RedditConnector
from app.services.ingestion.rss import RSSConnector
from app.services.ingestion.wallstreetcn_hot import WallStreetCNHotConnector
from app.services.ingestion.weibo import WeiboConnector
from app.services.ingestion.youtube import YouTubeConnector
from app.services.ingestion.zhihu import ZhihuConnector


@dataclass(slots=True)
class SourceSpec:
    slug: str
    name: str
    type: str
    description: str
    default_interval_minutes: int


REGISTRY: dict[str, SourceSpec] = {
    "github": SourceSpec(
        slug="github",
        name="GitHub",
        type="api",
        description="Trending repos, AI topic search.",
        default_interval_minutes=60,
    ),
    "reddit": SourceSpec(
        slug="reddit",
        name="Reddit",
        type="api",
        description="Subreddit hot posts in r/SaaS, r/LocalLLaMA, ...",
        default_interval_minutes=30,
    ),
    "hackernews": SourceSpec(
        slug="hackernews",
        name="Hacker News",
        type="api",
        description="Front-page and best posts via Firebase API.",
        default_interval_minutes=30,
    ),
    "producthunt": SourceSpec(
        slug="producthunt",
        name="Product Hunt",
        type="api",
        description="Daily launches and comments.",
        default_interval_minutes=60,
    ),
    "rss": SourceSpec(
        slug="rss",
        name="Generic RSS",
        type="rss",
        description="AI official blogs and curated feeds.",
        default_interval_minutes=120,
    ),
    "youtube": SourceSpec(
        slug="youtube",
        name="YouTube",
        type="api",
        description="AI tool / indie hacker channels.",
        default_interval_minutes=240,
    ),
    # --- Phase 25 v2.1 expansion ---
    "arxiv": SourceSpec(
        slug="arxiv",
        name="arXiv",
        type="api",
        description="New papers in cs.AI / cs.CL categories.",
        default_interval_minutes=120,
    ),
    "huggingface": SourceSpec(
        slug="huggingface",
        name="Hugging Face",
        type="api",
        description="Top-downloaded model leaderboard — community signal.",
        default_interval_minutes=120,
    ),
    "douyin": SourceSpec(
        slug="douyin",
        name="抖音热点",
        type="api",
        description="抖音热点榜（短视频热度信号）.",
        default_interval_minutes=30,
    ),
    "weibo": SourceSpec(
        slug="weibo",
        name="微博热搜",
        type="api",
        description="微博热搜榜（需代理访问）.",
        default_interval_minutes=30,
    ),
    "zhihu": SourceSpec(
        slug="zhihu",
        name="知乎热榜",
        type="api",
        description="知乎热榜（需代理访问）.",
        default_interval_minutes=30,
    ),
    "amazon_best": SourceSpec(
        slug="amazon_best",
        name="Amazon Best Sellers",
        type="rss",
        description="Amazon 各分类 Best Sellers 榜单.",
        default_interval_minutes=240,
    ),
    "wallstreetcn_hot": SourceSpec(
        slug="wallstreetcn_hot",
        name="华尔街见闻热门",
        type="api",
        description="华尔街见闻热门文章列表.",
        default_interval_minutes=60,
    ),
}


def build_connector(slug: str, settings: Settings, *, mock: bool | None = None) -> SourceConnector:
    """Construct a connector for the given slug, honouring `mock` override."""
    if slug not in REGISTRY:
        raise KeyError(f"unknown source slug: {slug!r}")

    force_mock = mock if mock is not None else settings.mock_external_services

    if slug == "github":
        return GitHubTrendingConnector(
            token=settings.MiniMax_api_key or None,  # any non-empty token lowers rate limit
            mock=force_mock,
        )
    if slug == "reddit":
        return RedditConnector(mock=force_mock)
    if slug == "hackernews":
        return HackerNewsConnector(mock=force_mock)
    if slug == "producthunt":
        return ProductHuntConnector(
            token=settings.telegram_bot_token or None,  # placeholder; real PH token lands later
            mock=force_mock,
        )
    if slug == "rss":
        return RSSConnector(mock=force_mock)
    if slug == "youtube":
        return YouTubeConnector(
            api_key=None,  # real key in Phase 3.1
            mock=force_mock,
        )
    # --- Phase 25 v2.1 expansion ---
    if slug == "arxiv":
        return ArxivConnector(mock=force_mock)
    if slug == "huggingface":
        return HuggingFaceConnector(mock=force_mock)
    if slug == "douyin":
        return DouyinConnector(mock=force_mock)
    if slug == "weibo":
        return WeiboConnector(mock=force_mock)
    if slug == "zhihu":
        return ZhihuConnector(mock=force_mock)
    if slug == "amazon_best":
        return AmazonBestSellersConnector(mock=force_mock)
    if slug == "wallstreetcn_hot":
        return WallStreetCNHotConnector(mock=force_mock)
    raise KeyError(slug)


def list_enabled(settings: Settings) -> list[SourceSpec]:
    enabled = set(settings.enabled_sources or [])
    return [REGISTRY[s] for s in enabled if s in REGISTRY]


def list_all() -> list[SourceSpec]:
    return list(REGISTRY.values())


def registry_as_dict() -> dict[str, dict[str, Any]]:
    return {
        slug: {
            "slug": spec.slug,
            "name": spec.name,
            "type": spec.type,
            "description": spec.description,
            "default_interval_minutes": spec.default_interval_minutes,
        }
        for slug, spec in REGISTRY.items()
    }


__all__ = [
    "REGISTRY",
    "SourceSpec",
    "build_connector",
    "list_all",
    "list_enabled",
    "registry_as_dict",
]