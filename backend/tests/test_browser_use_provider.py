"""Tests for the BrowserUseWebDataProvider (v2 async task API)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.research.browser_use_provider import BrowserUseWebDataProvider
from app.utils import ExternalServiceError


class _MockTransport(httpx.AsyncBaseTransport):
    """Stub httpx transport — simulates the Browser Use v2 async task API.

    POST /api/v2/tasks               — creates a task, returns {"id": "..."}
    GET  /api/v2/tasks/{id}          — polls, returns {"status": ..., "output": ...}
    POST /api/v2/tasks/{id}/stop     — cancels (unused here)
    """

    def __init__(
        self,
        *,
        task_output: str = "",
        task_status: str = "finished",
        status_code: int = 200,
        raise_on: set[str] | None = None,
        poll_responses: list[dict[str, Any]] | None = None,
    ) -> None:
        self.task_output = task_output
        self.task_status = task_status
        self.status_code = status_code
        self.raise_on = raise_on or set()
        # Optional scripted responses for the poll sequence. If provided,
        # they're returned one by one then we fall back to task_status.
        self.poll_responses = list(poll_responses or [])
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        method = request.method.upper()

        if path in self.raise_on:
            raise httpx.ConnectError("simulated outage")

        # POST /api/v2/tasks — create
        if method == "POST" and path == "/api/v2/tasks":
            body = json.dumps({"id": "task-abc123", "sessionId": "sess-xyz"})
            return httpx.Response(self.status_code, content=body.encode())

        # GET /api/v2/tasks/{id} — poll
        if method == "GET" and path.startswith("/api/v2/tasks/"):
            if self.poll_responses:
                payload = self.poll_responses.pop(0)
            else:
                payload = {
                    "status": self.task_status,
                    "output": self.task_output,
                }
            return httpx.Response(self.status_code, content=json.dumps(payload).encode())

        body = "{}"
        return httpx.Response(404, content=body.encode())


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
        poll_timeout=5.0,  # bound test runtime
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
# search() — wraps Browser Use as a task, polls until finished.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_creates_task_then_polls_with_correct_auth() -> None:
    transport = _MockTransport(
        task_output="[{\"url\": \"https://example.com/a\", \"title\": \"A\", \"snippet\": \"snippet a\"}]"
    )
    provider = _make_provider(transport)
    docs = await provider.search("ai tooling", limit=3)
    assert len(docs) == 1
    doc = docs[0]
    # search wraps the response in a single SourceDoc with the raw output.
    assert doc.title == "browser_use search: ai tooling"
    assert "snippet a" in doc.content
    assert doc.via_provider == "browser_use"

    # First call: POST /api/v2/tasks with the natural-language task body
    create_call = transport.calls[0]
    assert create_call.method == "POST"
    assert create_call.url.path == "/api/v2/tasks"
    body = json.loads(create_call.content)
    assert body["task"].startswith("Search the web for:")
    assert "ai tooling" in body["task"]
    # v2 uses X-Browser-Use-API-Key, NOT Authorization: Bearer.
    assert create_call.headers["X-Browser-Use-API-Key"] == "bu_test_key"
    assert create_call.headers["Content-Type"] == "application/json"
    assert "Authorization" not in create_call.headers

    # Second call: GET /api/v2/tasks/{id} poll
    poll_call = transport.calls[1]
    assert poll_call.method == "GET"
    assert poll_call.url.path == "/api/v2/tasks/task-abc123"
    assert poll_call.headers["X-Browser-Use-API-Key"] == "bu_test_key"


@pytest.mark.asyncio
async def test_search_maps_create_task_non_200_to_external_service_error() -> None:
    transport = _MockTransport(status_code=401)
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).search("q")
    assert exc.value.context.get("provider") == "browser_use"
    assert exc.value.context.get("operation") == "create_task"
    assert "401" in str(exc.value)


@pytest.mark.asyncio
async def test_search_maps_transport_error() -> None:
    transport = _MockTransport(raise_on={"/api/v2/tasks"})
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).search("q")
    assert exc.value.context.get("provider") == "browser_use"
    assert "simulated outage" in str(exc.value)


@pytest.mark.asyncio
async def test_search_polls_until_finished() -> None:
    """Two pending polls then `finished` on the third — provider must keep polling."""
    transport = _MockTransport(
        poll_responses=[
            {"status": "started", "output": ""},
            {"status": "pending", "output": ""},
            {"status": "finished", "output": "final answer"},
        ]
    )
    docs = await _make_provider(transport).search("q")
    assert "final answer" in docs[0].content
    # 1 create + 3 polls = 4 calls.
    assert len(transport.calls) == 4


@pytest.mark.asyncio
async def test_search_maps_task_failure_to_external_service_error() -> None:
    transport = _MockTransport(task_status="failed", task_output="model refused")
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).search("q")
    assert exc.value.context.get("provider") == "browser_use"
    assert exc.value.context.get("operation") == "poll_task"
    assert "failed" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# scrape()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_returns_output_from_task() -> None:
    transport = _MockTransport(task_output="# Hello\nworld markdown body")
    doc = await _make_provider(transport).scrape("https://example.com/")
    assert doc.url == "https://example.com/"
    assert doc.content.startswith("# Hello")
    assert doc.via_provider == "browser_use"

    create_call = transport.calls[0]
    assert create_call.url.path == "/api/v2/tasks"
    body = json.loads(create_call.content)
    assert "Open https://example.com/" in body["task"]


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
async def test_scrape_maps_create_non_200() -> None:
    transport = _MockTransport(status_code=502)
    with pytest.raises(ExternalServiceError) as exc:
        await _make_provider(transport).scrape("https://example.com/")
    assert exc.value.context.get("provider") == "browser_use"
    assert exc.value.context.get("operation") == "create_task"


@pytest.mark.asyncio
async def test_scrape_maps_transport_error() -> None:
    transport = _MockTransport(raise_on={"/api/v2/tasks"})
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
        poll_responses=[{"status": "finished", "output": "done"}]
    )
    created: list[httpx.AsyncClient] = []
    closed: list[httpx.AsyncClient] = []

    def _factory(*a: Any, **kw: Any) -> httpx.AsyncClient:
        kw.setdefault("transport", transport)
        kw.setdefault("timeout", 5.0)
        spy = _SpyClient(*a, **kw)
        created.append(spy)
        return spy

    class _SpyClient(httpx.AsyncClient):
        async def aclose(self) -> None:
            closed.append(self)
            await super().aclose()

    original = mod.httpx.AsyncClient
    mod.httpx.AsyncClient = _factory  # type: ignore[assignment]
    try:
        await _make_provider().scrape("https://example.com/")
    finally:
        mod.httpx.AsyncClient = original  # type: ignore[assignment]

    assert len(created) == 1, f"expected 1 created client, got {len(created)}"
    assert len(closed) == 1, f"expected 1 closed client, got {len(closed)}"
    assert closed[0] is created[0]