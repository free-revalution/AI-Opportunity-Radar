"""Firecrawl-backed WebDataProvider.

Uses the hosted Firecrawl REST API only — we do not vendor or self-host
the AGPL-3.0 server (see docs/THIRD_PARTY_AUDIT.md). Two endpoints:

  * POST /v1/search   — free-text search → list of search results
  * POST /v1/scrape   — single URL → extracted markdown

Every URL passed to `scrape()` is first validated with
`app.utils.url_validation.assert_safe_url` (SSRF guard).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.services.research.web_data import SourceDoc, WebDataProvider
from app.utils import ExternalServiceError, assert_safe_url, get_logger

logger = get_logger(__name__)


class FirecrawlWebDataProvider(WebDataProvider):
    """Host Firecrawl — JSON HTTP API, no SDK."""

    name = "firecrawl"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.firecrawl.dev",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("FirecrawlWebDataProvider requires api_key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns = self._client is None
        try:
            response = await client.post(
                f"{self.base_url}/v1/search",
                json={"query": query, "limit": limit},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"firecrawl search failed: {exc}",
                provider="firecrawl",
                operation="search",
            ) from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code != 200:
            raise ExternalServiceError(
                f"firecrawl search returned {response.status_code}",
                provider="firecrawl",
                operation="search",
                body=response.text[:200],
            )

        payload: dict[str, Any] = response.json()
        docs: list[SourceDoc] = []
        for entry in payload.get("data", []) or []:
            docs.append(
                SourceDoc(
                    url=entry.get("url") or "",
                    title=entry.get("title") or "",
                    content=entry.get("description") or entry.get("markdown") or "",
                    via_provider=self.name,
                    metadata={"score": entry.get("score")},
                )
            )
        return docs

    async def scrape(self, url: str) -> SourceDoc:
        # SSRF guard — refuse internal / loopback hosts before issuing HTTP.
        try:
            assert_safe_url(url)
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                f"refusing to scrape unsafe url: {url}",
                provider="firecrawl",
                operation="scrape",
            ) from exc

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns = self._client is None
        try:
            response = await client.post(
                f"{self.base_url}/v1/scrape",
                json={"url": url, "formats": ["markdown"]},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"firecrawl scrape failed: {exc}",
                provider="firecrawl",
                operation="scrape",
                url=url,
            ) from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code != 200:
            raise ExternalServiceError(
                f"firecrawl scrape returned {response.status_code}",
                provider="firecrawl",
                operation="scrape",
                url=url,
                body=response.text[:200],
            )

        payload = response.json().get("data") or {}
        return SourceDoc(
            url=url,
            title=payload.get("metadata", {}).get("title") or "",
            content=payload.get("markdown") or payload.get("html") or "",
            via_provider=self.name,
            metadata={"status_code": response.status_code},
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "ai-opportunity-radar/0.1",
        }


__all__ = ["FirecrawlWebDataProvider"]
