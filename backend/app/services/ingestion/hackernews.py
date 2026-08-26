"""Hacker News connector.

Public Firebase API — no auth, very generous rate limit.
We hit `/topstories.json` and resolve each top item in parallel.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_HN_API = "https://hacker-news.firebaseio.com/v0"


class HackerNewsConnector(SourceConnector):
    source = "hackernews"

    def __init__(
        self,
        *,
        top_n: int = 30,
        kind_filter: tuple[str, ...] = ("story",),
        mock: bool = False,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.top_n = top_n
        self.kind_filter = kind_filter
        self.timeout = timeout
        self._client = client

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_hn()

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns_client = self._client is None
        try:
            ids_resp = await client.get(f"{_HN_API}/topstories.json")
            if ids_resp.status_code != 200:
                return SourceConnectorResult(
                    source=self.source, errors=[f"hn topstories {ids_resp.status_code}"]
                )
            ids = (ids_resp.json() or [])[: self.top_n]
            items_meta = await asyncio.gather(
                *(client.get(f"{_HN_API}/item/{i}.json") for i in ids),
                return_exceptions=True,
            )
        except httpx.HTTPError as exc:
            return SourceConnectorResult(source=self.source, errors=[f"hn http error: {exc}"])
        finally:
            if owns_client:
                await client.aclose()

        items: list[RawItem] = []
        errors: list[str] = []
        for raw in items_meta:
            if isinstance(raw, Exception):
                errors.append(str(raw))
                continue
            if raw.status_code != 200:
                continue
            data: dict[str, Any] = raw.json()
            if not data or data.get("deleted") or data.get("dead"):
                continue
            if self.kind_filter and data.get("type") not in self.kind_filter:
                continue
            published_at = (
                datetime.fromtimestamp(data["time"], tz=timezone.utc) if data.get("time") else None
            )
            items.append(
                RawItem(
                    source=self.source,
                    source_id=str(data.get("id")),
                    url=data.get("url") or f"https://news.ycombinator.com/item?id={data.get('id')}",
                    title=data.get("title") or "(untitled)",
                    author=data.get("by"),
                    content=data.get("text"),
                    published_at=published_at,
                    metadata={
                        "score": data.get("score", 0),
                        "comments": data.get("descendants", 0),
                        "type": data.get("type"),
                    },
                )
            )

        return SourceConnectorResult(source=self.source, items=items, errors=errors)


def _mock_hn() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="hackernews",
        items=[
            RawItem(
                source="hackernews",
                source_id="400001",
                url="https://news.ycombinator.com/item?id=400001",
                title="Show HN: AI Avatar Generator for Shopify",
                author="indie_dev",
                content="Built in 3 weekends. MRR $4k.",
                published_at=now,
                metadata={"score": 612, "comments": 188},
            ),
            RawItem(
                source="hackernews",
                source_id="400002",
                url="https://news.ycombinator.com/item?id=400002",
                title="Ask HN: What AI SaaS are people quietly paying for?",
                author="pm_curious",
                published_at=now,
                metadata={"score": 412, "comments": 320},
            ),
        ],
    )