"""华尔街见闻 热门榜 connector.

Phase 25 v2.1 — uses the public article-list JSON endpoint. The
exact endpoint shape is rendered in the URL constant; if it
changes upstream, the connector degrades gracefully via the
``errors`` field rather than crashing the ingestion pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_WSCN_ENDPOINT = "https://api-one.wallstcn.com/apiv1/content/articles?limit=30&channel=global"


class WallStreetCNHotConnector(SourceConnector):
    source = "wallstreetcn_hot"

    def __init__(
        self,
        *,
        endpoint: str = _WSCN_ENDPOINT,
        max_items: int = 30,
        mock: bool = False,
        timeout: float = 12.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.endpoint = endpoint
        self.max_items = max_items
        self.timeout = timeout
        self._client = client

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_wallstreetcn()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
            },
        )
        owns_client = self._client is None
        try:
            resp = await client.get(self.endpoint)
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source,
                errors=[f"wallstreetcn http error: {exc}"],
            )
        finally:
            if owns_client:
                await client.aclose()

        if resp.status_code != 200:
            return SourceConnectorResult(
                source=self.source,
                errors=[f"wallstreetcn http {resp.status_code}"],
                http_status=resp.status_code,
            )
        payload = resp.json() or {}
        entries = payload.get("data", {}).get("items") or payload.get("data") or []
        items: list[RawItem] = []
        for idx, entry in enumerate(entries[: self.max_items], start=1):
            title = entry.get("title") or entry.get("content_text") or f"#{idx}"
            url = entry.get("uri") or entry.get("url") or self.endpoint
            content = entry.get("content_text") or entry.get("summary")
            items.append(
                RawItem(
                    source=self.source,
                    source_id=f"wscn:{entry.get('id') or entry.get('content_id') or idx}",
                    url=url,
                    title=str(title).strip(),
                    author=entry.get("author", {}).get("display_name", "") if isinstance(entry.get("author"), dict) else str(entry.get("author") or ""),
                    content=content,
                    published_at=_parse_ts(entry.get("display_time")),
                    metadata={
                        "category": "finance/cn",
                        "rank": idx,
                    },
                )
            )
        return SourceConnectorResult(source=self.source, items=items)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def _mock_wallstreetcn() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="wallstreetcn_hot",
        items=[
            RawItem(
                source="wallstreetcn_hot",
                source_id="wscn:1",
                url="https://wallstreetcn.com/articles/1",
                title="美联储释放降息信号",
                author="华尔街见闻",
                content="美元指数走弱 ...",
                published_at=now,
                metadata={"category": "finance/cn", "rank": 1},
            ),
        ],
    )


__all__ = ["WallStreetCNHotConnector"]
