"""Tests for the FirecrawlWebDataProvider — auth headers + SSRF guard."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.research.firecrawl_provider import FirecrawlWebDataProvider
from app.utils import ExternalServiceError


class _MockTransport(httpx.AsyncBaseTransport):
    """Stub httpx transport — returns canned responses for /v1/{search,scrape}."""

    def __init__(self, *, search_payload=None, scrape_payload=None, status_code=200):
        self.search_payload = search_payload or {"data": []}
        self.scrape_payload = scrape_payload or {"data": {"markdown": "", "metadata": {}}}
        self.status_code = status_code
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path.endswith("/v1/search"):
            body = json.dumps(self.search_payload)
        elif path.endswith("/v1/scrape"):
            body = json.dumps(self.scrape_payload)
        else:
            body = "{}"
        return httpx.Response(self.status_code, content=body.encode())


def _make_provider(transport: httpx.AsyncBaseTransport) -> FirecrawlWebDataProvider:
    return FirecrawlWebDataProvider(
        api_key="fc_test_key",
        base_url="https://api.firecrawl.dev",
        client=httpx.AsyncClient(transport=transport, timeout=5.0),
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def test_construct_requires_api_key():
    with pytest.raises(ValueError):
        FirecrawlWebDataProvider(api_key="", base_url="https://x.com")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_sends_bearer_and_query():
    transport = _MockTransport(
        search_payload={
            "data": [
                {"url": "https://a.com/1", "title": "A", "description": "about A"},
                {"url": "https://b.com/2", "title": "B", "markdown": "about B"},
            ]
        }
    )
    provider = _make_provider(transport)
    docs = await provider.search("AI sales coach", limit=2)
    assert len(docs) == 2
    assert {d.url for d in docs} == {"https://a.com/1", "https://b.com/2"}
    assert all(d.via_provider == "firecrawl" for d in docs)

    # Verify the auth + body.
    request = transport.calls[0]
    assert request.headers.get("Authorization") == "Bearer fc_test_key"
    body = json.loads(request.content)
    assert body["query"] == "AI sales coach"
    assert body["limit"] == 2


@pytest.mark.asyncio
async def test_search_translates_http_error():
    transport = _MockTransport(status_code=500)
    provider = _make_provider(transport)
    with pytest.raises(ExternalServiceError) as exc:
        await provider.search("anything", limit=1)
    assert exc.value.context.get("provider") == "firecrawl"
    assert exc.value.context.get("operation") == "search"


# ---------------------------------------------------------------------------
# Scrape + SSRF guard
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scrape_uses_markdown_format():
    transport = _MockTransport(
        scrape_payload={
            "data": {
                "markdown": "# Title\n\nBody text",
                "metadata": {"title": "Found Title"},
            }
        }
    )
    provider = _make_provider(transport)
    doc = await provider.scrape("https://www.example.com/page")
    assert doc.title == "Found Title"
    assert "Body text" in doc.content

    request = transport.calls[0]
    assert request.url.path.endswith("/v1/scrape")
    body = json.loads(request.content)
    assert body["url"] == "https://www.example.com/page"
    assert body["formats"] == ["markdown"]


@pytest.mark.asyncio
async def test_scrape_refuses_localhost_url():
    transport = _MockTransport()
    provider = _make_provider(transport)
    with pytest.raises(ExternalServiceError) as exc:
        await provider.scrape("http://localhost:8080/admin")
    assert "unsafe" in str(exc.value).lower()
    assert transport.calls == []  # never issued


@pytest.mark.asyncio
async def test_scrape_refuses_rfc1918_url():
    transport = _MockTransport()
    provider = _make_provider(transport)
    with pytest.raises(ExternalServiceError):
        await provider.scrape("http://10.0.0.5/secret")


@pytest.mark.asyncio
async def test_scrape_refuses_metadata_ip():
    transport = _MockTransport()
    provider = _make_provider(transport)
    with pytest.raises(ExternalServiceError):
        await provider.scrape("http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_scrape_translates_http_error():
    transport = _MockTransport(status_code=502)
    provider = _make_provider(transport)
    with pytest.raises(ExternalServiceError) as exc:
        await provider.scrape("https://example.com/page")
    assert exc.value.context.get("operation") == "scrape"
    assert exc.value.context.get("url") == "https://example.com/page"


@pytest.mark.asyncio
async def test_provider_creates_and_closes_its_own_client(monkeypatch):
    """When no client is passed, the provider must spin one up + close it."""
    captured: dict[str, Any] = {}

    class _RecordingClient:
        def __init__(self, *args, **kwargs):
            captured["ctor"] = (args, kwargs)

        async def post(self, *args, **kwargs):
            request = httpx.Request(
                "POST", "https://api.firecrawl.dev/v1/scrape", json={}
            )
            return httpx.Response(
                200,
                json={"data": {"markdown": "hi", "metadata": {"title": "T"}}},
                request=request,
            )

        async def aclose(self):
            captured["closed"] = True

    import app.services.research.firecrawl_provider as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _RecordingClient)
    provider = FirecrawlWebDataProvider(
        api_key="fc_test_key", base_url="https://api.firecrawl.dev"
    )
    doc = await provider.scrape("https://example.com/")
    assert doc.content == "hi"
    assert captured.get("closed") is True
