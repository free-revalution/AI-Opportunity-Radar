"""Browser Use-backed WebDataProvider.

Browser Use is a cloud-rendered browser API — useful as a fallback for
JS-heavy or authenticated pages that Firecrawl cannot render. We use it
behind the same `WebDataProvider` boundary as Firecrawl so the research
engine can swap providers transparently and so the
`FallbackWebDataProvider` composite can chain them.

Endpoints (hosted cloud API at `BROWSER_USE_API_URL`,
default `https://api.browser-use.com`):

  * `POST /api/v2/tasks` — body `{"task": str, "llm": str (optional)}`
    returns `{"id": str, "sessionId": str}` (async — caller polls)
  * `GET  /api/v2/tasks/{task_id}` — returns the current task state
    including `status` (`pending|started|finished|failed`), `output`,
    `steps`, `cost`, `isSuccess`.
  * `POST /api/v2/tasks/{task_id}/stop` — cancel a running task.

Authentication uses `X-Browser-Use-API-Key: <key>` (NOT `Authorization: Bearer`).
Tasks take seconds-to-minutes; we poll `/api/v2/tasks/{id}` until the
status flips to `finished` or `failed`, bounded by `poll_timeout` seconds.

Every error is surfaced as `ExternalServiceError(provider="browser_use", ...)`
so the fallback chain in `app.services.research.fallback_provider` can
swap providers uniformly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.services.research.web_data import SourceDoc, WebDataProvider
from app.utils import ExternalServiceError, assert_safe_url, get_logger

logger = get_logger(__name__)

# Cloud REST endpoints — v2 (see module docstring).
_TASK_PATH = "/api/v2/tasks"
_TASK_POLL_INTERVAL = 2.0  # seconds between status polls


class BrowserUseWebDataProvider(WebDataProvider):
    """Hosted Browser Use — JSON over HTTPS, no SDK."""

    name = "browser_use"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.browser-use.com",
        timeout: float = 30.0,
        poll_timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("BrowserUseWebDataProvider requires api_key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_timeout = poll_timeout
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # public API — WebDataProvider contract
    # ------------------------------------------------------------------
    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        """Browser Use has no native search — model a search as a task that
        asks the browser to enumerate public sources. Returned as a single
        result because v2 tasks don't produce structured lists.
        """
        task = (
            f"Search the web for: {query!r}. "
            f"Return the top {limit} URLs with their titles and a one-sentence "
            f"snippet for each. Format the answer as a JSON array of "
            f'{{"url", "title", "snippet"}}.'
        )
        output = await self._run_task(task)
        # Best-effort: the model might not return strict JSON; tolerate text.
        return [SourceDoc(
            url="",
            title=f"browser_use search: {query}",
            content=output,
            via_provider=self.name,
            metadata={"query": query, "limit": limit},
        )]

    async def scrape(self, url: str) -> SourceDoc:
        """Scrape a URL by handing it to Browser Use as a task.

        The task asks for clean markdown — the orchestrator gets a single
        SourceDoc regardless of whether the page is static or JS-heavy.
        """
        # SSRF guard — block private / loopback hosts before any HTTP.
        try:
            assert_safe_url(url)
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                f"refusing to scrape unsafe url: {url}",
                provider=self.name,
                operation="scrape",
            ) from exc

        task = (
            f"Open {url} and return the full page content as clean markdown, "
            f"including the page title and main body text. Do not summarise."
        )
        output = await self._run_task(task)
        return SourceDoc(
            url=url,
            title="",
            content=output,
            via_provider=self.name,
            metadata={"status_code": 200},
        )

    # ------------------------------------------------------------------
    # internals — v2 async task lifecycle
    # ------------------------------------------------------------------
    async def _run_task(self, task_description: str) -> str:
        """Submit a task and block until it finishes (or times out)."""
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns = self._owns_client
        try:
            task_id = await self._create_task(client, task_description)
            return await self._poll_task(client, task_id)
        finally:
            if owns:
                await client.aclose()

    async def _create_task(self, client: httpx.AsyncClient, task: str) -> str:
        try:
            response = await client.post(
                f"{self.base_url}{_TASK_PATH}",
                json={"task": task},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"browser_use create_task failed: {exc}",
                provider=self.name,
                operation="create_task",
            ) from exc

        if response.status_code not in (200, 201, 202):
            raise ExternalServiceError(
                f"browser_use create_task returned {response.status_code}",
                provider=self.name,
                operation="create_task",
                body=response.text[:200],
            )

        payload = response.json()
        task_id = payload.get("id")
        if not task_id:
            raise ExternalServiceError(
                "browser_use create_task response missing 'id'",
                provider=self.name,
                operation="create_task",
                body=str(payload)[:200],
            )
        logger.info("browser_use_task_created", task_id=task_id)
        return str(task_id)

    async def _poll_task(self, client: httpx.AsyncClient, task_id: str) -> str:
        deadline = asyncio.get_event_loop().time() + self.poll_timeout
        while True:
            if asyncio.get_event_loop().time() >= deadline:
                raise ExternalServiceError(
                    f"browser_use task timed out after {self.poll_timeout}s",
                    provider=self.name,
                    operation="poll_task",
                    task_id=task_id,
                )

            try:
                response = await client.get(
                    f"{self.base_url}{_TASK_PATH}/{task_id}",
                    headers=self._headers(),
                )
            except httpx.HTTPError as exc:
                raise ExternalServiceError(
                    f"browser_use poll_task failed: {exc}",
                    provider=self.name,
                    operation="poll_task",
                    task_id=task_id,
                ) from exc

            if response.status_code != 200:
                raise ExternalServiceError(
                    f"browser_use poll_task returned {response.status_code}",
                    provider=self.name,
                    operation="poll_task",
                    task_id=task_id,
                    body=response.text[:200],
                )

            payload = response.json()
            status = (payload.get("status") or "").lower()

            if status in {"finished", "complete", "completed", "succeeded"}:
                output = payload.get("output") or payload.get("result") or ""
                logger.info(
                    "browser_use_task_finished",
                    task_id=task_id,
                    output_chars=len(str(output)),
                )
                return str(output)

            if status in {"failed", "error", "cancelled", "canceled"}:
                raise ExternalServiceError(
                    f"browser_use task {status}",
                    provider=self.name,
                    operation="poll_task",
                    task_id=task_id,
                    body=str(payload)[:200],
                )

            await asyncio.sleep(_TASK_POLL_INTERVAL)

    def _headers(self) -> dict[str, str]:
        return {
            # Browser Use v2 expects this header — NOT `Authorization: Bearer`.
            "X-Browser-Use-API-Key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "ai-opportunity-radar/0.1",
        }


__all__ = ["BrowserUseWebDataProvider"]