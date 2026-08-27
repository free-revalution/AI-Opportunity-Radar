"""Phase 10 — n8n workflow sync helper.

We do **not** embed n8n business logic. Each workflow is a thin HTTP
orchestrator that calls our FastAPI backend. This module is the
companion that pushes the local JSON files in `n8n/workflows/` into a
running n8n container via the n8n public REST API (`/api/v1/workflows`).

Public surface:
    N8nClient            thin httpx wrapper
    N8nWorkflowSummary   parsed summary of one remote workflow
    sync_workflows_dir   load every *.json file, create-or-update on n8n,
                         optionally activate, and return a sync report
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

from app.config import get_settings
from app.utils import get_logger

logger = get_logger(__name__)


class N8nError(RuntimeError):
    """Raised when the n8n API rejects our request or is unreachable."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class N8nWorkflowSummary:
    """One row in the sync report."""

    name: str
    remote_id: str
    action: str  # "created" | "updated" | "skipped" | "failed"
    detail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# Fields that n8n's POST / PUT /api/v1/workflows treat as read-only.
# Source: https://docs.n8n.io/api/v1/workflows — these are owned by n8n
# (e.g. set by the runner, the scheduler, or the user via the UI toggle)
# and must NOT appear in the request body. Activation goes through the
# dedicated POST /api/v1/workflows/{id}/activate endpoint.
_READONLY_TOP_LEVEL_FIELDS = frozenset({"active", "createdAt", "updatedAt"})


def _strip_readonly(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *payload* without read-only top-level fields.

    n8n's v1 API returns 400 `request/body/active is read-only` if the
    create/update body contains fields it manages internally. We strip
    them here so callers can keep their hand-written workflow JSONs in
    the n8n-export format (which always includes `"active": true`).
    """
    return {k: v for k, v in payload.items() if k not in _READONLY_TOP_LEVEL_FIELDS}


class N8nClient:
    """Minimal n8n REST client.

    The n8n API is documented at https://docs.n8n.io/api/. All endpoints
    we call are read-or-write only on `/api/v1/workflows` and require
    an `X-N8N-API-KEY` header in addition to basic auth when basic-auth
    is enabled (we send both defensively).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        resolved = (base_url or settings.n8n_base_url or "").rstrip("/")
        if not resolved:
            raise N8nError("n8n base URL not configured (set N8N_BASE_URL)")
        self.base_url = resolved
        self.api_key = api_key or settings.n8n_api_key or ""
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._default_headers(),
        )
        if client is not None:
            self._client.headers.update(self._default_headers())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def health(self) -> bool:
        """Best-effort liveness probe — returns True on any 2xx/3xx."""
        try:
            resp = self._client.get("/healthz")
            return resp.status_code < 400
        except httpx.HTTPError:
            return False

    def list_workflows(self) -> list[dict[str, Any]]:
        resp = self._client.get("/api/v1/workflows")
        self._raise_for_status(resp)
        body = resp.json()
        if isinstance(body, dict) and "data" in body:
            data = body["data"]
        else:
            data = body
        if not isinstance(data, list):
            raise N8nError(
                "unexpected /api/v1/workflows payload shape",
                status_code=resp.status_code,
            )
        return [w for w in data if isinstance(w, dict)]

    def create_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post("/api/v1/workflows", json=payload)
        self._raise_for_status(resp)
        body = resp.json()
        return body if isinstance(body, dict) else {"data": body}

    def update_workflow(
        self, workflow_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        resp = self._client.put(
            f"/api/v1/workflows/{workflow_id}", json=payload
        )
        self._raise_for_status(resp)
        body = resp.json()
        return body if isinstance(body, dict) else {"data": body}

    def activate(self, workflow_id: str) -> dict[str, Any]:
        resp = self._client.post(f"/api/v1/workflows/{workflow_id}/activate")
        self._raise_for_status(resp)
        body = resp.json()
        return body if isinstance(body, dict) else {"data": body}

    def deactivate(self, workflow_id: str) -> dict[str, Any]:
        resp = self._client.post(f"/api/v1/workflows/{workflow_id}/deactivate")
        self._raise_for_status(resp)
        body = resp.json()
        return body if isinstance(body, dict) else {"data": body}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _default_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        return headers

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if 200 <= resp.status_code < 300:
            return
        detail = resp.text[:500] if resp.text else ""
        raise N8nError(
            f"n8n {resp.request.method} {resp.request.url.path} "
            f"-> {resp.status_code}: {detail}",
            status_code=resp.status_code,
        )


# ---------------------------------------------------------------------------
# Directory sync
# ---------------------------------------------------------------------------
def load_workflow_file(path: Path) -> dict[str, Any]:
    """Read a single workflow JSON file. Raises ValueError on bad shape."""
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON ({exc.msg} @ line {exc.lineno})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}: top-level must be an object")
    if "name" not in payload or not isinstance(payload["name"], str):
        raise ValueError(f"{path.name}: missing 'name' string")
    if "nodes" not in payload or not isinstance(payload["nodes"], list):
        raise ValueError(f"{path.name}: missing 'nodes' array")
    if "connections" not in payload or not isinstance(payload["connections"], dict):
        raise ValueError(f"{path.name}: missing 'connections' object")
    return payload


def sync_workflows_dir(
    directory: Path,
    *,
    activate: bool = False,
    client: N8nClient | None = None,
) -> list[N8nWorkflowSummary]:
    """Sync every *.json file in *directory* into n8n.

    Behaviour:
    * For each file, look up the workflow by name on the remote instance.
    * If absent → create. If present → update in place.
    * Read-only fields (e.g. `active`) are stripped before the create/update
      call — n8n's API rejects them with 400 `is read-only`.
    * If `activate=True`, call `/activate` after a successful create/update.
    * Exceptions are caught and reported via `N8nWorkflowSummary.action='failed'`.
    """
    if not directory.exists():
        raise FileNotFoundError(f"workflows directory not found: {directory}")

    own_client = client is None
    client = client or N8nClient()
    try:
        existing = {w.get("name"): w for w in client.list_workflows()}
        summaries: list[N8nWorkflowSummary] = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = load_workflow_file(path)
            except ValueError as exc:
                summaries.append(
                    N8nWorkflowSummary(
                        name=path.stem,
                        remote_id="",
                        action="failed",
                        detail=str(exc),
                    )
                )
                continue

            name = str(payload["name"])
            body = _strip_readonly(payload)
            try:
                if name in existing and existing[name].get("id"):
                    remote_id = str(existing[name]["id"])
                    client.update_workflow(remote_id, body)
                    action = "updated"
                else:
                    created = client.create_workflow(body)
                    remote_id = str(
                        created.get("id")
                        or (created.get("data") or {}).get("id")
                        or ""
                    )
                    action = "created"
                if activate and remote_id:
                    client.activate(remote_id)
                summaries.append(
                    N8nWorkflowSummary(
                        name=name,
                        remote_id=remote_id,
                        action=action,
                        detail="ok",
                    )
                )
            except N8nError as exc:
                logger.warning(
                    "n8n_sync_workflow_failed",
                    workflow=name,
                    error=str(exc),
                    status_code=exc.status_code,
                )
                summaries.append(
                    N8nWorkflowSummary(
                        name=name,
                        remote_id="",
                        action="failed",
                        detail=str(exc),
                    )
                )
        return summaries
    finally:
        if own_client:
            client.close()


def summarise(summaries: Iterable[N8nWorkflowSummary]) -> str:
    """Pretty-print a sync report — used by the CLI."""
    rows = list(summaries)
    if not rows:
        return "no workflows to sync"
    parts = [f"{r.action:8s} {r.remote_id or '-':8s} {r.name}  {r.detail}" for r in rows]
    return "\n".join(parts)