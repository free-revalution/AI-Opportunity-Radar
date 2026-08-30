"""抖音热点榜 connector.

Public endpoint (no auth required for read-only billboard):
  https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/aweme/

Phase 25 v2.1 — the user's brief was "any kind of hotspot,
filter on the Feishu layer". 抖音 is one of the four user-named
hot-spot sources (社媒热点/资讯平台).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_DOUYIN_ENDPOINT = (
    "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/aweme/"
)


class DouyinConnector(SourceConnector):
    source = "douyin"

    def __init__(
        self,
        *,
        endpoint: str = _DOUYIN_ENDPOINT,
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
            return _mock_douyin()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                )
            },
        )
        owns_client = self._client is None
        try:
            resp = await client.get(self.endpoint)
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source, errors=[f"douyin http error: {exc}"]
            )
        finally:
            if owns_client:
                await client.aclose()

        if resp.status_code != 200:
            return SourceConnectorResult(
                source=self.source,
                errors=[f"douyin http {resp.status_code}"],
                http_status=resp.status_code,
            )
        payload = resp.json() or {}
        entries = payload.get("aweme_list") or payload.get("word_list") or []
        items: list[RawItem] = []
        for idx, entry in enumerate(entries[: self.max_items], start=1):
            title = (
                entry.get("word")
                or entry.get("title")
                or entry.get("aweme_name")
                or f"#{idx}"
            )
            items.append(
                RawItem(
                    source=self.source,
                    source_id=f"douyin:{entry.get('aweme_id') or entry.get('sentence_id') or idx}",
                    url=entry.get("share_url") or entry.get("video_url") or self.endpoint,
                    title=str(title),
                    author="",
                    content=entry.get("desc") or entry.get("hot_value"),
                    published_at=None,
                    metadata={
                        "category": "social/hot",
                        "rank": idx,
                        "hot_value": entry.get("hot_value"),
                    },
                )
            )
        return SourceConnectorResult(source=self.source, items=items)


def _mock_douyin() -> SourceConnectorResult:
    return SourceConnectorResult(
        source="douyin",
        items=[
            RawItem(
                source="douyin",
                source_id="douyin:1",
                url="https://www.douyin.com/hot/1",
                title="某 AI 工具 3 天涨粉百万",
                content="热度值 12_500_000",
                published_at=None,
                metadata={"category": "social/hot", "rank": 1, "hot_value": "12_500_000"},
            ),
            RawItem(
                source="douyin",
                source_id="douyin:2",
                url="https://www.douyin.com/hot/2",
                title="跨境电商 Q2 选品趋势",
                content="热度值 9_200_000",
                published_at=None,
                metadata={"category": "social/hot", "rank": 2, "hot_value": "9_200_000"},
            ),
        ],
    )


__all__ = ["DouyinConnector"]
