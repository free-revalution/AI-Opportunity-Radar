"""Browser Use-backed WebDataProvider.

Browser Use is a cloud-rendered browser API — useful as a fallback for
JS-heavy or authenticated pages that Firecrawl cannot render. We use it
behind the same `WebDataProvider` boundary as Firecrawl so the research
engine can swap providers transparently and so the
`FallbackWebDataProvider` composite can chain them.

Endpoints (hosted cloud API at `BROWSER_USE_API_URL`,
default `https://api.browser-use.com`):

  * `POST /api/v1/search` — body `{"query": str, "limit": int}`
    response: `{"results": [{"url", "title", "description"}], ...}`
  * `POST /api/v1/scrape` — body `{"url": str}`
    response: `{"markdown": str, "title": str, ...}`

The exact paths are best-effort against the public cloud API; the
provider is unit-tested with a stubbed httpx transport so we can patch
the constants below without breaking callers. Every error is surfaced
as `ExternalServiceError(provider="browser_use", ...)` — that is what
makes the fallback chain work uniformly.

# TODO(verify-endpoints): confirm `/api/v1/search` and `/api/v1/scrape`
# against the live docs at https://docs.browser-use.com when convenient.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.research.web_data import SourceDoc, WebDataProvider
from app.utils import ExternalServiceError, assert_safe_url, get_logger

logger = get_logger(__name__)

# Cloud REST endpoints — see module docstring.
_SEARCH_PATH = "/api/v1/search"
_SCRAPE_PATH = "/api/v1/scrape"


class BrowserUseWebDataProvider(WebDataProvider):
    """Hosted Browser Use — JSON over HTTPS, no SDK."""

    name = "browser_use"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.browser-use.com",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("BrowserUseWebDataProvider requires api_key")
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
                f"{self.base_url}{_SEARCH_PATH}",
                json={"query": query, "limit": limit},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"browser_use search failed: {exc}",
                provider=self.name,
                operation="search",
            ) from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code != 200:
            raise ExternalServiceError(
                f"browser_use search returned {response.status_code}",
                provider=self.name,
                operation="search",
                body=response.text[:200],
            )

        payload: dict[str, Any] = response.json()
        docs: list[SourceDoc] = []
        # The cloud API returns `{"results": [...]}`; tolerate a bare list
        # body too — useful for mock mirrors and contract drift.
        raw = payload.get("results") if isinstance(payload, dict) else None
        if raw is None and isinstance(payload, list):
            raw = payload
        for entry in raw or []:
            if not isinstance(entry, dict):
                continue
            docs.append(
                SourceDoc(
                    url=entry.get("url") or "",
                    title=entry.get("title") or "",
                    content=(
                        entry.get("description")
                        or entry.get("markdown")
                        or entry.get("snippet")
                        or ""
                    ),
                    via_provider=self.name,
                    metadata={
                        "score": entry.get("score"),
                    },
                )
            )
        return docs

    async def scrape(self, url: str) -> SourceDoc:
        # SSRF guard — block private / loopback hosts before any HTTP.
        try:
            assert_safe_url(url)
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                f"refusing to scrape unsafe url: {url}",
                provider=self.name,
                operation="scrape",
            ) from exc

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns = self._client is None
        try:
            response = await client.post(
                f"{self.base_url}{_SCRAPE_PATH}",
                json={"url": url},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"browser_use scrape failed: {exc}",
                provider=self.name,
                operation="scrape",
                url=url,
            ) from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code != 200:
            raise ExternalServiceError(
                f"browser_use scrape returned {response.status_code}",
                provider=self.name,
                operation="scrape",
                url=url,
                body=response.text[:200],
            )

        payload = response.json() if isinstance(response.json(), dict) else {}
        return SourceDoc(
            url=url,
            title=payload.get("title") or "",
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


__all__ = ["BrowserUseWebDataProvider"]
