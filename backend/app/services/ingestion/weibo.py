"""微博热搜 connector.

Public endpoint:
  https://weibo.com/ajax/side/hotSearch

Phase 25 v2.1 — note this endpoint requires reaching Weibo from
a server in mainland China OR via operator proxy. When the host
fails to reach Weibo, the connector returns a ``SourceConnectorResult``
with the error recorded (and operators see it in ``/sources``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_WEIBO_ENDPOINT = "https://weibo.com/ajax/side/hotSearch"


class WeiboConnector(SourceConnector):
    source = "weibo"

    def __init__(
        self,
        *,
        endpoint: str = _WEIBO_ENDPOINT,
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
            return _mock_weibo()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Referer": "https://weibo.com/",
                "Accept": "application/json, text/plain, */*",
            },
        )
        owns_client = self._client is None
        try:
            resp = await client.get(self.endpoint)
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source, errors=[f"weibo http error: {exc}"]
            )
        finally:
            if owns_client:
                await client.aclose()

        if resp.status_code != 200:
            return SourceConnectorResult(
                source=self.source,
                errors=[f"weibo http {resp.status_code}"],
                http_status=resp.status_code,
            )
        payload = resp.json() or {}
        entries = payload.get("data", {}).get("realtime") or []
        items: list[RawItem] = []
        for idx, entry in enumerate(entries[: self.max_items], start=1):
            word = entry.get("word") or entry.get("note") or f"#{idx}"
            items.append(
                RawItem(
                    source=self.source,
                    source_id=f"weibo:{entry.get('word_scheme') or idx}",
                    url=entry.get("url") or f"https://s.weibo.com/weibo?q={word}",
                    title=str(word),
                    author="",
                    content=entry.get("label_name") or entry.get("category"),
                    published_at=None,
                    metadata={
                        "category": "social/hot",
                        "rank": idx,
                        "hot_value": entry.get("num"),
                    },
                )
            )
        return SourceConnectorResult(source=self.source, items=items)


def _mock_weibo() -> SourceConnectorResult:
    return SourceConnectorResult(
        source="weibo",
        items=[
            RawItem(
                source="weibo",
                source_id="weibo:1",
                url="https://s.weibo.com/weibo?q=AI%20工具",
                title="AI 工具",
                content="热",
                published_at=None,
                metadata={"category": "social/hot", "rank": 1, "hot_value": 5_200_000},
            ),
            RawItem(
                source="weibo",
                source_id="weibo:2",
                url="https://s.weibo.com/weibo?q=跨境电商",
                title="跨境电商",
                content="",
                published_at=None,
                metadata={"category": "social/hot", "rank": 2, "hot_value": 3_800_000},
            ),
        ],
    )


__all__ = ["WeiboConnector"]
