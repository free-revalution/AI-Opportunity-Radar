"""Hugging Face Trending Models connector.

Free public API:
  https://huggingface.co/api/models?sort=downloads&direction=-1&limit=30

No auth, no proxy. Returns the top-N most-downloaded models
across all tasks — a real-time indicator of which open-source
models are gaining traction (Phase 25 v2.1).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.ingestion.base import SourceConnector, SourceConnectorResult
from app.services.ingestion.raw_item import RawItem


_HF_ENDPOINT = "https://huggingface.co/api/models?sort=downloads&direction=-1&limit=30"


class HuggingFaceConnector(SourceConnector):
    source = "huggingface"

    def __init__(
        self,
        *,
        endpoint: str = _HF_ENDPOINT,
        limit: int = 30,
        mock: bool = False,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(mock=mock)
        self.endpoint = endpoint
        self.limit = limit
        self.timeout = timeout
        self._client = client

    async def fetch(self) -> SourceConnectorResult:
        if self.mock:
            return _mock_huggingface()

        client = self._client or httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": "ai-opportunity-radar/0.1"},
        )
        owns_client = self._client is None
        try:
            resp = await client.get(self.endpoint)
        except httpx.HTTPError as exc:
            return SourceConnectorResult(
                source=self.source, errors=[f"huggingface http error: {exc}"]
            )
        finally:
            if owns_client:
                await client.aclose()

        if resp.status_code != 200:
            return SourceConnectorResult(
                source=self.source,
                errors=[f"huggingface http {resp.status_code}"],
                http_status=resp.status_code,
            )
        data = resp.json() or []
        items: list[RawItem] = []
        for entry in data[: self.limit]:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("modelId") or entry.get("id") or ""
            if not model_id:
                continue
            tags = entry.get("tags") or []
            pipeline_tag = next(
                (t for t in tags if not t.startswith(("arxiv:", "license:", "doi:"))),
                None,
            )
            items.append(
                RawItem(
                    source=self.source,
                    source_id=f"hf:{model_id}",
                    url=f"https://huggingface.co/{model_id}",
                    title=model_id,
                    author=entry.get("author") or "",
                    content=pipeline_tag or "",
                    published_at=_parse_iso(entry.get("createdAt")),
                    metadata={
                        "category": "tech/ai",
                        "downloads": entry.get("downloads", 0),
                        "likes": entry.get("likes", 0),
                        "tags": tags[:5],
                    },
                )
            )
        return SourceConnectorResult(source=self.source, items=items)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        # Hugging Face returns ISO 8601 strings like
        # "2024-01-15T12:34:56.000Z" — accept trailing Z.
        text = value.replace("Z", "+00:00") if value.endswith("Z") else value
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def _mock_huggingface() -> SourceConnectorResult:
    now = datetime.now(timezone.utc)
    return SourceConnectorResult(
        source="huggingface",
        items=[
            RawItem(
                source="huggingface",
                source_id="hf:meta-llama/Llama-3-70B-Instruct",
                url="https://huggingface.co/meta-llama/Llama-3-70B-Instruct",
                title="meta-llama/Llama-3-70B-Instruct",
                author="meta-llama",
                content="text-generation",
                published_at=now,
                metadata={
                    "category": "tech/ai",
                    "downloads": 1_500_000,
                    "likes": 4200,
                    "tags": ["text-generation"],
                },
            ),
            RawItem(
                source="huggingface",
                source_id="hf:black-forest-labs/FLUX.1-schnell",
                url="https://huggingface.co/black-forest-labs/FLUX.1-schnell",
                title="black-forest-labs/FLUX.1-schnell",
                author="black-forest-labs",
                content="text-to-image",
                published_at=now,
                metadata={
                    "category": "tech/ai",
                    "downloads": 350_000,
                    "likes": 1200,
                    "tags": ["text-to-image"],
                },
            ),
        ],
    )


__all__ = ["HuggingFaceConnector"]
