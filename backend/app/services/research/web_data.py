"""Web data provider — abstraction over Firecrawl / Browser Use / mocks.

The research engine never speaks HTTP directly to a vendor. Instead it
calls `WebDataProvider.search()` and `WebDataProvider.scrape()`, behind
a small async interface. This keeps the boundary swappable and lets
the test suite run with deterministic mock content.

Provider selection (see `build_web_data_provider`):
  * mock        — offline, deterministic, used by tests + local dev
  * firecrawl   — hosted REST API, the README §12 mandated default
  * browser_use — fallback for JS-heavy / authenticated pages
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(slots=True)
class SourceDoc:
    """One fetched document — search result or scraped page."""

    url: str
    title: str
    content: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    via_provider: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def excerpt(self) -> str:
        """Truncated content for log lines + small prompts."""
        text = self.content.strip()
        if len(text) <= 280:
            return text
        return text[:277] + "..."

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "excerpt": self.excerpt,
            "via_provider": self.via_provider,
            "fetched_at": self.fetched_at.isoformat(),
        }


class WebDataProvider(ABC):
    """Async boundary for search + scrape.

    Implementations MUST translate every failure into
    `app.utils.ExternalServiceError` so the engine can retry uniformly.
    """

    name: str = "abstract"

    @abstractmethod
    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        """Return up to `limit` results for a free-text query."""

    @abstractmethod
    async def scrape(self, url: str) -> SourceDoc:
        """Fetch and extract the textual content of a single URL."""

    # ------------------------------------------------------------------
    # convenience
    # ------------------------------------------------------------------
    async def scrape_many(
        self, urls: Sequence[str], *, max_concurrency: int = 4
    ) -> list[SourceDoc]:
        """Default sequential scrape. Providers can override for parallelism."""
        return [await self.scrape(u) for u in urls]


__all__ = ["SourceDoc", "WebDataProvider"]


# ----------------------------------------------------------------------
# Factory — used by the research service. Imports stay inside the
# function to avoid loading vendor SDKs at import time.
# ----------------------------------------------------------------------
def build_web_data_provider(settings, *, prefer: Optional[str] = None):
    """Return the configured provider, falling back to the mock."""
    from app.services.research.mock_web_data import MockWebDataProvider

    if getattr(settings, "mock_external_services", False):
        return MockWebDataProvider()

    chosen = (prefer or "firecrawl").lower()
    if chosen == "firecrawl" and getattr(settings, "firecrawl_api_key", ""):
        from app.services.research.firecrawl_provider import (
            FirecrawlWebDataProvider,
        )

        return FirecrawlWebDataProvider(
            api_key=settings.firecrawl_api_key,
            base_url=settings.firecrawl_api_url,
        )
    return MockWebDataProvider()
