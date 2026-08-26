"""Tests for the Phase 10 n8n workflow-sync helper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.n8n import (
    N8nClient,
    N8nError,
    N8nWorkflowSummary,
    load_workflow_file,
    summarise,
    sync_workflows_dir,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeN8nTransport(httpx.BaseTransport):
    """In-memory n8n stand-in. Routes are matched on method + path."""

    def __init__(self) -> None:
        self.workflows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.next_id = 1
        self.fail_on: set[tuple[str, str]] = set()
        self.fail_status = 500
        self.fail_body = "boom"

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.calls.append((request.method, request.url.path, self._maybe_json(request)))
        if key in self.fail_on:
            return httpx.Response(self.fail_status, text=self.fail_body)

        if key == ("GET", "/healthz"):
            return httpx.Response(200, text='{"status":"ok"}')

        if key == ("GET", "/api/v1/workflows"):
            return httpx.Response(
                200,
                json={"data": list(self.workflows.values())},
            )

        if key == ("POST", "/api/v1/workflows"):
            body = self._maybe_json(request) or {}
            wid = str(self.next_id)
            self.next_id += 1
            record = {**body, "id": wid}
            self.workflows[record["name"]] = record
            return httpx.Response(200, json=record)

        if request.method == "PUT" and request.url.path.startswith("/api/v1/workflows/"):
            wid = request.url.path.rsplit("/", 1)[-1]
            record = next(
                (w for w in self.workflows.values() if w["id"] == wid), None
            )
            if record is None:
                return httpx.Response(404, text="not found")
            body = self._maybe_json(request) or {}
            record.update(body)
            record["id"] = wid
            return httpx.Response(200, json=record)

        if request.method == "POST" and request.url.path.endswith("/activate"):
            wid = request.url.path.split("/")[-2]
            record = next(
                (w for w in self.workflows.values() if w["id"] == wid), None
            )
            if record is None:
                return httpx.Response(404, text="not found")
            record["active"] = True
            return httpx.Response(200, json=record)

        if request.method == "POST" and request.url.path.endswith("/deactivate"):
            wid = request.url.path.split("/")[-2]
            record = next(
                (w for w in self.workflows.values() if w["id"] == wid), None
            )
            if record is None:
                return httpx.Response(404, text="not found")
            record["active"] = False
            return httpx.Response(200, json=record)

        return httpx.Response(404, text="unhandled")

    @staticmethod
    def _maybe_json(request: httpx.Request) -> dict[str, Any] | None:
        if not request.content:
            return None
        try:
            payload = json.loads(request.content)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def close(self) -> None:  # pragma: no cover — required by BaseTransport
        return None


@pytest.fixture
def fake_transport() -> FakeN8nTransport:
    return FakeN8nTransport()


@pytest.fixture
def client(fake_transport: FakeN8nTransport) -> N8nClient:
    httpx_client = httpx.Client(
        base_url="http://n8n.test",
        transport=fake_transport,
        headers={"X-N8N-API-KEY": "secret"},
    )
    return N8nClient(
        base_url="http://n8n.test",
        api_key="secret",
        client=httpx_client,
    )


SAMPLE_WORKFLOW: dict[str, Any] = {
    "name": "test-workflow",
    "nodes": [{"id": "n1", "type": "n8n-nodes-base.webhook"}],
    "connections": {},
    "active": False,
}


# ---------------------------------------------------------------------------
# N8nClient tests
# ---------------------------------------------------------------------------


def test_health_returns_true_on_2xx(client: N8nClient) -> None:
    assert client.health() is True


def test_list_workflows_returns_array(client: N8nClient) -> None:
    assert client.list_workflows() == []


def test_create_and_update_workflow(
    client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    created = client.create_workflow(SAMPLE_WORKFLOW)
    assert created["id"] == "1"
    assert fake_transport.workflows["test-workflow"]["id"] == "1"

    updated = client.update_workflow(
        "1", {**SAMPLE_WORKFLOW, "active": True}
    )
    assert updated["active"] is True
    assert fake_transport.workflows["test-workflow"]["active"] is True


def test_activate_and_deactivate(
    client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    client.create_workflow(SAMPLE_WORKFLOW)
    client.activate("1")
    assert fake_transport.workflows["test-workflow"]["active"] is True
    client.deactivate("1")
    assert fake_transport.workflows["test-workflow"]["active"] is False


def test_raises_on_4xx(
    client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    fake_transport.fail_on.add(("GET", "/api/v1/workflows"))
    fake_transport.fail_status = 401
    fake_transport.fail_body = "unauthorized"
    with pytest.raises(N8nError) as exc:
        client.list_workflows()
    assert exc.value.status_code == 401
    assert "unauthorized" in str(exc.value)


def test_requires_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """When neither the explicit arg nor the env provides a URL, raise."""
    # Force get_settings() to behave as if no n8n is configured.
    from app.services.n8n import sync as sync_module

    class _Empty:
        n8n_base_url = ""
        n8n_api_key = ""

    monkeypatch.setattr(sync_module, "get_settings", lambda: _Empty())
    with pytest.raises(N8nError):
        N8nClient(base_url="")


# ---------------------------------------------------------------------------
# load_workflow_file tests
# ---------------------------------------------------------------------------


def test_load_workflow_file_happy_path(tmp_path: Path) -> None:
    p = tmp_path / "wf.json"
    p.write_text(json.dumps(SAMPLE_WORKFLOW))
    assert load_workflow_file(p) == SAMPLE_WORKFLOW


@pytest.mark.parametrize(
    "missing_field",
    ["name", "nodes", "connections"],
)
def test_load_workflow_file_rejects_missing_field(
    tmp_path: Path, missing_field: str
) -> None:
    payload = {**SAMPLE_WORKFLOW}
    payload.pop(missing_field)
    p = tmp_path / "wf.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=missing_field):
        load_workflow_file(p)


def test_load_workflow_file_rejects_non_object(tmp_path: Path) -> None:
    p = tmp_path / "wf.json"
    p.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError, match="top-level"):
        load_workflow_file(p)


def test_load_workflow_file_rejects_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "wf.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_workflow_file(p)


# ---------------------------------------------------------------------------
# sync_workflows_dir tests
# ---------------------------------------------------------------------------


def test_sync_creates_each_file(
    tmp_path: Path, client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    (tmp_path / "a.json").write_text(json.dumps({**SAMPLE_WORKFLOW, "name": "a"}))
    (tmp_path / "b.json").write_text(json.dumps({**SAMPLE_WORKFLOW, "name": "b"}))
    summaries = sync_workflows_dir(tmp_path, client=client)
    actions = {s.name: s.action for s in summaries}
    assert actions == {"a": "created", "b": "created"}
    assert set(fake_transport.workflows) == {"a", "b"}


def test_sync_updates_existing(
    tmp_path: Path, client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    # Pre-seed remote with name "test-workflow"
    fake_transport.workflows["test-workflow"] = {
        **SAMPLE_WORKFLOW,
        "id": "42",
        "active": False,
    }
    fake_transport.next_id = 100

    (tmp_path / "a.json").write_text(json.dumps(SAMPLE_WORKFLOW))
    summaries = sync_workflows_dir(tmp_path, client=client)
    assert len(summaries) == 1
    assert summaries[0].action == "updated"
    assert summaries[0].remote_id == "42"


def test_sync_activates_when_requested(
    tmp_path: Path, client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    (tmp_path / "a.json").write_text(json.dumps(SAMPLE_WORKFLOW))
    sync_workflows_dir(tmp_path, activate=True, client=client)
    assert fake_transport.workflows["test-workflow"]["active"] is True


def test_sync_reports_failure_per_file(
    tmp_path: Path, client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "good.json").write_text(json.dumps(SAMPLE_WORKFLOW))
    summaries = sync_workflows_dir(tmp_path, client=client)
    by_name = {s.name: s for s in summaries}
    assert by_name["bad"].action == "failed"
    assert by_name["test-workflow"].action == "created"


def test_sync_handles_remote_create_failure(
    tmp_path: Path, client: N8nClient, fake_transport: FakeN8nTransport
) -> None:
    fake_transport.fail_on.add(("POST", "/api/v1/workflows"))
    (tmp_path / "a.json").write_text(json.dumps({**SAMPLE_WORKFLOW, "name": "a"}))
    summaries = sync_workflows_dir(tmp_path, client=client)
    assert summaries[0].action == "failed"
    assert "500" in summaries[0].detail or "boom" in summaries[0].detail


def test_sync_raises_when_directory_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sync_workflows_dir(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# summarise helper
# ---------------------------------------------------------------------------


def test_summarise_empty() -> None:
    assert summarise([]) == "no workflows to sync"


def test_summarise_renders_rows() -> None:
    rows = [
        N8nWorkflowSummary(name="a", remote_id="1", action="created"),
        N8nWorkflowSummary(name="b", remote_id="2", action="failed", detail="x"),
    ]
    out = summarise(rows)
    assert "created" in out and "failed" in out
    assert "a" in out and "b" in out
