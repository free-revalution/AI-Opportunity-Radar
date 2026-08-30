"""arXiv connector (cs.AI + cs.CL categories).

arXiv exposes a free Atom XML API:
  http://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=30

No auth, no proxy required, very generous rate limit (~1 req / 3s).
We use ``feedparser`` (already a dep for the RSS connector) wrapped
in ``asyncio.to_thread`` so the rest of the pipeline stays async.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import feedparser  # type: ignore[import-not-found]
import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_ARXIV_ENDPOINT = (
    "http://export.arxiv.org/api/query?"
    "search_query=cat:cs.AI+OR+cat:cs.CL"
    "&sortBy=submittedDate&sortOrder=descending&max_results=30"
)


class ArxivConnector(SourceConnector):
    source = "arxiv"

    def __init__(
        self,
        *,
        endpoint: str = _ARXIV_ENDPOINT,
        max_results: int = 30,
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.endpoint = endpoint
        self.max_results = max_results
        self.timeout = timeout
        self._client = client

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_arxiv()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": "ai-opportunity-radar/0.1"},
        )
        owns_client = self._client is None
        try:
            resp = await client.get(self.endpoint)
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source, errors=[f"arxiv http error: {exc}"]
            )
        finally:
            if owns_client:
                await client.aclose()

        if resp.status_code != 200:
            return SourceConnectorResult(
                source=self.source,
                errors=[f"arxiv http {resp.status_code}"],
                http_status=resp.status_code,
            )
        parsed = await asyncio.to_thread(feedparser.parse, resp.content)
        items: list[RawItem] = []
        for entry in parsed.get("entries", [])[: self.max_results]:
            published_at = None
            for key in ("published_parsed", "updated_parsed"):
                raw = entry.get(key)
                if raw:
                    published_at = datetime(*raw[:6], tzinfo=timezone.utc)
                    break
            link = entry.get("link") or entry.get("id") or ""
            items.append(
                RawItem(
                    source=self.source,
                    source_id=f"arxiv:{entry.get('id') or link}",
                    url=link,
                    title=entry.get("title") or "(untitled)",
                    author=", ".join(a.get("name", "") for a in entry.get("authors", []) if a.get("name")),
                    content=entry.get("summary"),
                    published_at=published_at,
                    metadata={
                        "category": "tech/ai",
                        "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                    },
                )
            )
        return SourceConnectorResult(source=self.source, items=items)


def _mock_arxiv() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="arxiv",
        items=[
            RawItem(
                source="arxiv",
                source_id="arxiv:2401.01234",
                url="https://arxiv.org/abs/2401.01234",
                title="Efficient Long-Context Inference for LLMs",
                author="Doe, J., Smith, A.",
                content="We propose a new KV-cache compression ...",
                published_at=now,
                metadata={"category": "tech/ai", "tags": ["cs.CL"]},
            ),
            RawItem(
                source="arxiv",
                source_id="arxiv:2401.05678",
                url="https://arxiv.org/abs/2401.05678",
                title="Reward Hacking in RLHF: A Survey",
                author="Lee, K.",
                content="We catalogue 47 reward-hacking patterns ...",
                published_at=now,
                metadata={"category": "tech/ai", "tags": ["cs.LG", "cs.AI"]},
            ),
        ],
    )


__all__ = ["ArxivConnector"]
