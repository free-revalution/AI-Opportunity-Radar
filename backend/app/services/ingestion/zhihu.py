"""知乎热榜 connector.

Public endpoint:
  https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total

Phase 25 v2.1 — like Weibo, requires reaching Zhihu from a
mainland-China IP or operator proxy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_ZHIHU_ENDPOINT = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total"


class ZhihuConnector(SourceConnector):
    source = "zhihu"

    def __init__(
        self,
        *,
        endpoint: str = _ZHIHU_ENDPOINT,
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
            return _mock_zhihu()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Referer": "https://www.zhihu.com/",
                "Accept": "application/json, text/plain, */*",
            },
        )
        owns_client = self._client is None
        try:
            resp = await client.get(self.endpoint)
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source, errors=[f"zhihu http error: {exc}"]
            )
        finally:
            if owns_client:
                await client.aclose()

        if resp.status_code != 200:
            return SourceConnectorResult(
                source=self.source,
                errors=[f"zhihu http {resp.status_code}"],
                http_status=resp.status_code,
            )
        payload = resp.json() or {}
        entries = payload.get("data") or []
        items: list[RawItem] = []
        for idx, entry in enumerate(entries[: self.max_items], start=1):
            target = entry.get("target") or entry
            title = target.get("title") or target.get("question_title") or f"#{idx}"
            url = target.get("link", {}).get("url") if isinstance(target.get("link"), dict) else target.get("url")
            url = url or f"https://www.zhihu.com/question/{target.get('id', '')}"
            items.append(
                RawItem(
                    source=self.source,
                    source_id=f"zhihu:{target.get('id') or idx}",
                    url=url,
                    title=str(title).strip(),
                    author="",
                    content=target.get("excerpt") or target.get("answer_count"),
                    published_at=None,
                    metadata={
                        "category": "social/hot",
                        "rank": idx,
                        "hot_value": target.get("detail_text") or target.get("hot_value"),
                    },
                )
            )
        return SourceConnectorResult(source=self.source, items=items)


def _mock_zhihu() -> SourceConnectorResult:
    return SourceConnectorResult(
        source="zhihu",
        items=[
            RawItem(
                source="zhihu",
                source_id="zhihu:1",
                url="https://www.zhihu.com/question/1",
                title="2025 年最值得关注的 AI 应用方向是什么？",
                content="百万热度",
                published_at=None,
                metadata={"category": "social/hot", "rank": 1, "hot_value": "5_000_000"},
            ),
        ],
    )


__all__ = ["ZhihuConnector"]
