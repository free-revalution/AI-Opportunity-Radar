"""Web data provider — abstraction over Firecrawl / Browser Use / mocks.

The research engine never speaks HTTP directly to a vendor. Instead it
calls `WebDataProvider.search()` and `WebDataProvider.scrape()`, behind
a small async interface. This keeps the boundary swappable and lets
the test suite run with deterministic mock content.

Provider selection (see `build_web_data_provider`):
  * mock        — offline, deterministic, used by tests + local dev
  * firecrawl   — hosted REST API, the README §12 mandated default
  * browser_use — JS-heavy / authenticated pages fallback

When more than one real backend is configured the factory wraps them in
a `FallbackWebDataProvider` so a single failure (e.g. Browser Use 401)
automatically falls through to the next provider. This is the
implementation of the project rule:

    如果 Browser Use 服务不可用：必须让系统继续使用 Firecrawl
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
def _build_chain(
    settings, *, prefer: str
) -> list[WebDataProvider]:
    """Materialise the ordered list of providers for *prefer*.

    `prefer` values:
      * "browser_use" — Browser Use (if key) → Firecrawl (if key) → Mock
      * "firecrawl"   — Firecrawl (if key) → Mock
      * "auto"        — both keys → [Browser Use, Firecrawl, Mock];
                        Firecrawl-only → [Firecrawl, Mock];
                        neither → [Mock]
      * anything else — treated as "auto" for forward-compatibility
    """
    from app.services.research.fallback_provider import (  # local import
        FallbackWebDataProvider,
    )
    from app.services.research.firecrawl_provider import (
        FirecrawlWebDataProvider,
    )
    from app.services.research.mock_web_data import MockWebDataProvider

    has_firecrawl = bool(getattr(settings, "firecrawl_api_key", ""))
    has_browser_use = bool(getattr(settings, "browser_use_api_key", ""))

    chain: list[WebDataProvider] = []

    def _firecrawl() -> WebDataProvider | None:
        if not has_firecrawl:
            return None
        return FirecrawlWebDataProvider(
            api_key=settings.firecrawl_api_key,
            base_url=settings.firecrawl_api_url,
        )

    def _browser_use() -> WebDataProvider | None:
        if not has_browser_use:
            return None
        # Import lazily so missing the Browser Use SDK doesn't break
        # the import chain of the rest of the app.
        from app.services.research.browser_use_provider import (
            BrowserUseWebDataProvider,
        )

        return BrowserUseWebDataProvider(
            api_key=settings.browser_use_api_key,
            base_url=settings.browser_use_api_url,
        )

    chosen = (prefer or "auto").lower()
    if chosen == "browser_use":
        bu = _browser_use()
        if bu is not None:
            chain.append(bu)
        fc = _firecrawl()
        if fc is not None:
            chain.append(fc)
    elif chosen == "firecrawl":
        fc = _firecrawl()
        if fc is not None:
            chain.append(fc)
    else:
        # auto — preserve the README §12 ordering: Browser Use first when
        # the key is set (it's strictly more capable for JS pages), then
        # Firecrawl as the always-on fallback.
        bu = _browser_use()
        if bu is not None:
            chain.append(bu)
        fc = _firecrawl()
        if fc is not None:
            chain.append(fc)

    # Every chain terminates in the offline mock so the research job
    # never aborts on a vendor outage during local dev.
    chain.append(MockWebDataProvider())
    return chain


def build_web_data_provider(settings, *, prefer: Optional[str] = None):
    """Return the configured provider.

    When `mock_external_services=True` the offline mock is returned
    immediately (preserves the test + local-dev contract).

    Otherwise the factory returns:
      * a single provider, if the resolved chain has length 1;
      * a `FallbackWebDataProvider` wrapping the chain, otherwise.

    Existing callers that compare `provider.name` keep working because
    `FallbackWebDataProvider.name == "fallback"`.
    """
    from app.services.research.fallback_provider import (  # local import
        FallbackWebDataProvider,
    )

    if getattr(settings, "mock_external_services", False):
        from app.services.research.mock_web_data import MockWebDataProvider

        return MockWebDataProvider()

    chain = _build_chain(settings, prefer=prefer or "auto")
    if len(chain) == 1:
        return chain[0]
    return FallbackWebDataProvider(chain)
