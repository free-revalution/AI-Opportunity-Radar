"""Generic RSS / Atom connector.

Uses `feedparser` (sync) wrapped in `asyncio.to_thread` so the rest of
the pipeline stays async.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import feedparser  # type: ignore[import-not-found]
import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


# Default feeds — AI official blogs + tech press.
DEFAULT_FEEDS: tuple[tuple[str, str], ...] = (
    ("OpenAI Blog", "https://openai.com/blog/rss.xml"),
    ("Anthropic News", "https://www.anthropic.com/news/rss.xml"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/"),
    ("Hacker News Frontpage", "https://news.ycombinator.com/rss"),
    ("Lobsters", "https://lobste.rs/rss"),
)


class RSSConnector(SourceConnector):
    source = "rss"

    def __init__(
        self,
        *,
        feeds: tuple[tuple[str, str], ...] = DEFAULT_FEEDS,
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.feeds = feeds
        self.timeout = timeout
        self._client = client

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_rss()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": "ai-opportunity-radar/0.1"},
        )
        owns_client = self._client is None
        items: list[RawItem] = []
        errors: list[str] = []

        try:
            responses = await asyncio.gather(
                *(client.get(url) for _, url in self.feeds),
                return_exceptions=True,
            )
        finally:
            if owns_client:
                await client.aclose()

        for (name, url), resp in zip(self.feeds, responses, strict=False):
            if isinstance(resp, Exception):
                errors.append(f"rss/{name}: {resp}")
                continue
            if resp.status_code != 200:
                errors.append(f"rss/{name} {resp.status_code}")
                continue

            parsed = await asyncio.to_thread(feedparser.parse, resp.content)
            for entry in parsed.get("entries", [])[:25]:
                published_at = None
                for key in ("published_parsed", "updated_parsed", "created_parsed"):
                    raw_time = entry.get(key)
                    if raw_time:
                        published_at = datetime(*raw_time[:6], tzinfo=timezone.utc)
                        break
                items.append(
                    RawItem(
                        source=self.source,
                        source_id=f"{name}:{entry.get('id') or entry.get('link')}",
                        url=entry.get("link") or url,
                        title=entry.get("title") or "(untitled)",
                        author=entry.get("author"),
                        content=entry.get("summary"),
                        published_at=published_at,
                        metadata={
                            "feed": name,
                            "tags": [t.get("term") for t in entry.get("tags", []) if t],
                        },
                    )
                )

        return SourceConnectorResult(source=self.source, items=items, errors=errors)


def _mock_rss() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="rss",
        items=[
            RawItem(
                source="rss",
                source_id="OpenAI Blog:1",
                url="https://openai.com/blog/announcement",
                title="OpenAI announces a new developer-tier API",
                author="openai",
                content="Better caching, lower latency, $X/mo pricing.",
                published_at=now,
                metadata={"feed": "OpenAI Blog", "tags": ["api", "pricing"]},
            ),
            RawItem(
                source="rss",
                source_id="Anthropic News:2",
                url="https://www.anthropic.com/news/claude-update",
                title="Anthropic ships Claude 5",
                author="anthropic",
                content="Long-context improvements, lower hallucination rate.",
                published_at=now,
                metadata={"feed": "Anthropic News", "tags": ["llm"]},
            ),
        ],
    )


__all__ = ["RSSConnector", "DEFAULT_FEEDS"]