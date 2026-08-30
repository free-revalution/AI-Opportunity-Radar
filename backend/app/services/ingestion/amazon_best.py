"""Amazon Best Sellers RSS connector.

Public RSS feeds (no auth) — one feed per category. Phase 25 v2.1
hits the global bestseller list by default; operators can extend
the ``CATEGORIES`` tuple to track specific verticals.

Endpoint pattern:
  https://www.amazon.com/feed/bestsellers/<department>/<category>
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import feedparser  # type: ignore[import-not-found]
import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


# Default list — keep modest. Operators can extend by passing
# their own ``categories`` tuple to the connector.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Amazon Bestsellers (general)", "https://www.amazon.com/feed/bestsellers"),
    (
        "Amazon Bestsellers (electronics)",
        "https://www.amazon.com/feed/bestsellers/electronics",
    ),
    (
        "Amazon Bestsellers (home)",
        "https://www.amazon.com/feed/bestsellers/home-garden",
    ),
)


class AmazonBestSellersConnector(SourceConnector):
    source = "amazon_best"

    def __init__(
        self,
        *,
        categories: tuple[tuple[str, str], ...] = CATEGORIES,
        max_per_feed: int = 25,
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.categories = categories
        self.max_per_feed = max_per_feed
        self.timeout = timeout
        self._client = client

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_amazon_best()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": "ai-opportunity-radar/0.1"},
        )
        owns_client = self._client is None
        try:
            responses = await client.get_multi(  # type: ignore[attr-defined]
                *(url for _, url in self.categories)
            ) if hasattr(client, "get_multi") else None
            if responses is None:
                import asyncio

                responses = await asyncio.gather(
                    *(client.get(url) for _, url in self.categories),
                    return_exceptions=True,
                )
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source, errors=[f"amazon_best http error: {exc}"]
            )
        finally:
            if owns_client:
                await client.aclose()

        import asyncio

        items: list[RawItem] = []
        errors: list[str] = []
        for (name, url), resp in zip(self.categories, responses, strict=False):
            if isinstance(resp, Exception):
                errors.append(f"amazon_best/{name}: {resp}")
                continue
            if resp.status_code != 200:
                errors.append(f"amazon_best/{name} {resp.status_code}")
                continue
            parsed = await asyncio.to_thread(feedparser.parse, resp.content)
            for entry in parsed.get("entries", [])[: self.max_per_feed]:
                items.append(
                    RawItem(
                        source=self.source,
                        source_id=f"amazon_best:{entry.get('id') or entry.get('link') or entry.get('title')}",
                        url=entry.get("link") or url,
                        title=entry.get("title") or "(untitled)",
                        author="",
                        content=entry.get("summary"),
                        published_at=None,
                        metadata={
                            "category": "ecommerce/amazon",
                            "feed": name,
                        },
                    )
                )
        return SourceConnectorResult(source=self.source, items=items, errors=errors)


def _mock_amazon_best() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="amazon_best",
        items=[
            RawItem(
                source="amazon_best",
                source_id="amazon_best:1",
                url="https://www.amazon.com/dp/B0EXAMPLE1",
                title="Example Product 1 — best seller in electronics",
                author="",
                content="$29.99 — rank #1 in Electronics",
                published_at=now,
                metadata={"category": "ecommerce/amazon", "feed": "Amazon Bestsellers (electronics)"},
            ),
        ],
    )


__all__ = ["AmazonBestSellersConnector", "CATEGORIES"]
