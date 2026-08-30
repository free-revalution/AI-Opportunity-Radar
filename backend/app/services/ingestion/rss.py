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


# Default feeds — Phase 25 v2.1 expansion.
#
# Three tiers, in priority order:
#   1. AI official blogs (OpenAI / Anthropic / Google) — the original
#      MVP surface; kept verbatim for source-health continuity.
#   2. Tech press (Hacker News / Lobsters) — dev community signal.
#   3. Phase 25 v2.1 expansion — business / finance / e-commerce / hot
#      topics. The user's brief was "any kind of hotspot, filter on
#      the Feishu layer later", so we deliberately mix mainstream
#      tech, Chinese financial press, e-commerce trade sites, and a
#      few generalist international feeds.
#
# Each entry: (display_name, url, category). The category is a
# free-form label propagated to ``RawItem.metadata["category"]`` so
# downstream scoring / digest rendering can highlight (or filter)
# by topic — Phase 25 v2.1 leaves the filtering on the Feishu side.
DEFAULT_FEEDS: tuple[tuple[str, str, str], ...] = (
    # --- original MVP surface ---
    ("OpenAI Blog", "https://openai.com/blog/rss.xml", "tech/ai"),
    ("Anthropic News", "https://www.anthropic.com/news/rss.xml", "tech/ai"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/", "tech/ai"),
    ("Hacker News Frontpage", "https://news.ycombinator.com/rss", "tech/community"),
    ("Lobsters", "https://lobste.rs/rss", "tech/community"),
    # --- Phase 25 v2.1 — Chinese financial / business press ---
    ("财富中文网", "https://www.fortunechina.com/rss.xml", "finance/cn"),
    ("华尔街见闻", "https://wallstreetcn.com/rss", "finance/cn"),
    ("FT 中文网", "https://www.ftchinese.com/rss/feed", "finance/cn"),
    # --- Phase 25 v2.1 — start-ups / e-commerce trade ---
    ("36氪", "https://36kr.com/feed", "ecommerce/cn"),
    ("虎嗅", "https://www.huxiu.com/rss/0.xml", "ecommerce/cn"),
    ("亿邦动力", "https://www.ebrun.com/rss", "ecommerce/cn"),
    ("投资界", "https://www.pedaily.cn/rss", "finance/cn"),
    # --- Phase 25 v2.1 — international mainstream (operator proxy) ---
    ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "finance/global"),
    ("Reuters", "https://www.reutersagency.com/feed/?best-topics=top-news", "finance/global"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "tech/global"),
)


class RSSConnector(SourceConnector):
    source = "rss"

    def __init__(
        self,
        *,
        feeds: tuple[tuple[str, str, str], ...] = DEFAULT_FEEDS,
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
                *(client.get(url) for _, url, _ in self.feeds),
                return_exceptions=True,
            )
        finally:
            if owns_client:
                await client.aclose()

        for (name, url, category), resp in zip(self.feeds, responses, strict=False):
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
                            "category": category,
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
                metadata={
                    "feed": "OpenAI Blog",
                    "category": "tech/ai",
                    "tags": ["api", "pricing"],
                },
            ),
            RawItem(
                source="rss",
                source_id="Anthropic News:2",
                url="https://www.anthropic.com/news/claude-update",
                title="Anthropic ships Claude 5",
                author="anthropic",
                content="Long-context improvements, lower hallucination rate.",
                published_at=now,
                metadata={
                    "feed": "Anthropic News",
                    "category": "tech/ai",
                    "tags": ["llm"],
                },
            ),
            RawItem(
                source="rss",
                source_id="财富中文网:3",
                url="https://www.fortunechina.com/example",
                title="某科技公司 Q2 营收超预期",
                author="fortunechina",
                content="净利润同比增长 25%。",
                published_at=now,
                metadata={
                    "feed": "财富中文网",
                    "category": "finance/cn",
                    "tags": ["财报"],
                },
            ),
        ],
    )


__all__ = ["RSSConnector", "DEFAULT_FEEDS"]