"""Tests for the Phase 11 BrowserUseWebDataProvider (auth + SSRF + endpoints)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.research.browser_use_provider import BrowserUseWebDataProvider
from app.utils import ExternalServiceError


class _MockTransport(httpx.AsyncBaseTransport):
    """Stub httpx transport — canned responses for /api/v1/{search,scrape}."""

    def __init__(
        self,
        *,
        search_payload: dict[str, Any] | list[Any] | None = None,
        scrape_payload: dict[str, Any] | None = None,
        status_code: int = 200,
        raise_on: set[str] | None = None,
    ) -> None:
        self.search_payload = search_payload if search_payload is not None else {
            "results": []
        }
        self.scrape_payload = scrape_payload or {"title": "", "markdown": ""}
        self.status_code = status_code
        self.raise_on = raise_on or set()
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path in self.raise_on:
            raise httpx.ConnectError("simulated outage")
        if path.endswith("/api/v1/search"):
            body = json.dumps(self.search_payload)
        elif path.endswith("/api/v1/scrape"):
            body = json.dumps(self.scrape_payload)
        else:
            body = "{}"
        return httpx.Response(self.status_code, content=body.encode())


def _make_provider(
    transport: httpx.AsyncBaseTransport | None = None,
    **kwargs: Any,
) -> BrowserUseWebDataProvider:
    return BrowserUseWebDataProvider(
        api_key="bu_test_key",
        base_url="https://api.browser-use.com",
        client=(
            httpx.AsyncClient(transport=transport, timeout=5.0)
            if transport is not None
            else None
        ),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        BrowserUseWebDataProvider(api_key="")  # type: ignore[arg-type]


def test_name_is_browser_use() -> None:
    assert _make_provider().name == "browser_use"


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_hits_correct_endpoint_with_bearer_auth() -> None:
    transport = _MockTransport(
        search_payload={
            "results": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "description": "snippet a",
                }
            ]
        }
    )
    provider = _make_provider(transport)
    docs = await provider.search("ai tooling", limit=3)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.url == "https://example.com/a"
    assert doc.title == "A"
    assert doc.content == "snippet a"
    assert doc.via_provider == "browser_use"

    request = transport.calls[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/api/v1/search")
    body = json.loads(request.content)
    assert body == {"query": "ai tooling", "limit": 3}
    assert request.headers["Authorization"] == "Bearer bu_test_key"
    assert request.headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_search_tolerates_bare_list_payload() -> None:
    """Some mirrors return `[]` at the top level — accept either shape."""
    transport = _MockTransport(
        search_payload=[
            {"url": "https://x", "title": "t", "description": "d"}
        ]
    )
    docs = await _make_provider(transport).search("q")
    assert len(docs) == 1 and docs[0].url == "https://x"


@pytest.mark.asyncio
async def test_search_maps_non_200_to_external_service_error() -> None:
    transport = _MockTransport(
        status_code=401,
        search_payload={"error": "unauthorized"},
    )
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).search("q")
    assert exc.value.context.get("provider") == "browser_use"
    assert exc.value.context.get("operation") == "search"
    assert "401" in str(exc.value)


@pytest.mark.asyncio
async def test_search_maps_transport_error() -> None:
    transport = _MockTransport(raise_on={"/api/v1/search"})
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).search("q")
    assert exc.value.context.get("provider") == "browser_use"
    assert "simulated outage" in str(exc.value)


# ---------------------------------------------------------------------------
# scrape()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_returns_markdown_and_title() -> None:
    transport = _MockTransport(
        scrape_payload={
            "title": "Example Domain",
            "markdown": "# Hello\nworld",
        }
    )
    doc = await _make_provider(transport).scrape("https://example.com/")
    assert doc.url == "https://example.com/"
    assert doc.title == "Example Domain"
    assert doc.content.startswith("# Hello")
    assert doc.via_provider == "browser_use"

    request = transport.calls[0]
    assert request.url.path.endswith("/api/v1/scrape")
    body = json.loads(request.content)
    assert body == {"url": "https://example.com/"}
    assert request.headers["Authorization"] == "Bearer bu_test_key"


@pytest.mark.asyncio
async def test_scrape_enforces_ssrf_preflight() -> None:
    """Localhost / private IPs are refused before any HTTP is issued.

    The provider re-wraps the underlying `SSRFError` into a uniform
    `ExternalServiceError` — same pattern as Firecrawl.
    """
    transport = _MockTransport()
    provider = _make_provider(transport)
    with pytest.raises(ExternalServiceError) as exc1:
        await provider.scrape("http://localhost/admin")
    assert exc1.value.context.get("provider") == "browser_use"
    with pytest.raises(ExternalServiceError) as exc2:
        await provider.scrape("http://169.254.169.254/latest/meta-data/")
    assert exc2.value.context.get("provider") == "browser_use"
    # Nothing should have reached the transport.
    assert transport.calls == []


@pytest.mark.asyncio
async def test_scrape_maps_non_200() -> None:
    transport = _MockTransport(status_code=502)
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).scrape("https://example.com/")
    assert exc.value.context.get("provider") == "browser_use"
    assert exc.value.context.get("operation") == "scrape"
    assert exc.value.context.get("url") == "https://example.com/"


@pytest.mark.asyncio
async def test_scrape_maps_transport_error() -> None:
    transport = _MockTransport(raise_on={"/api/v1/scrape"})
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).scrape("https://example.com/")
    assert exc.value.context.get("provider") == "browser_use"
    assert "simulated outage" in str(exc.value)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_creates_and_closes_its_own_client() -> None:
    """When no client is injected, the provider must close the one it made."""
    import app.services.research.browser_use_provider as mod

    transport = _MockTransport(
        search_payload={"results": [{"url": "https://x", "title": "t", "description": "d"}]}
    )
    created: list[httpx.AsyncClient] = []
    closed: list[httpx.AsyncClient] = []

    class _Spy(httpx.AsyncClient):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            created.append(self)

        async def aclose(self) -> None:
            closed.append(self)
            await super().aclose()

    original = mod.httpx.AsyncClient
    mod.httpx.AsyncClient = _Spy  # type: ignore[assignment]
    try:
        # Pass the same transport to every Spy instance so the test doesn't
        # hit the real network.
        def _factory(*a: Any, **kw: Any) -> httpx.AsyncClient:
            kw.setdefault("transport", transport)
            kw.setdefault("timeout", 5.0)
            return _Spy(*a, **kw)

        mod.httpx.AsyncClient = _factory  # type: ignore[assignment]
        await _make_provider().search("q")
    finally:
        mod.httpx.AsyncClient = original  # type: ignore[assignment]

    assert len(created) == 1, f"expected 1 created client, got {len(created)}"
    assert len(closed) == 1, f"expected 1 closed client, got {len(closed)}"
    assert closed[0] is created[0]
