"""Source connector registry API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


_SOURCES: list[dict[str, Any]] = [
    {
        "slug": "github",
        "name": "GitHub",
        "type": "api",
        "description": "Trending repos, AI topic search.",
        "default_interval_minutes": 60,
    },
    {
        "slug": "reddit",
        "name": "Reddit",
        "type": "api",
        "description": "Subreddit hot posts in r/SaaS, r/LocalLLaMA, ...",
        "default_interval_minutes": 30,
    },
    {
        "slug": "hackernews",
        "name": "Hacker News",
        "type": "rss",
        "description": "Front-page and best posts via Firebase API.",
        "default_interval_minutes": 30,
    },
    {
        "slug": "producthunt",
        "name": "Product Hunt",
        "type": "api",
        "description": "Daily launches and comments.",
        "default_interval_minutes": 60,
    },
    {
        "slug": "rss",
        "name": "Generic RSS",
        "type": "rss",
        "description": "AI official blogs and curated feeds.",
        "default_interval_minutes": 120,
    },
    {
        "slug": "youtube",
        "name": "YouTube",
        "type": "api",
        "description": "AI tool / indie hacker channels.",
        "default_interval_minutes": 240,
    },
]


@router.get("/sources", summary="List configured source connectors")
async def list_sources() -> dict[str, Any]:
    return {"items": _SOURCES, "total": len(_SOURCES)}