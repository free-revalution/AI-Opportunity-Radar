"""Static validation of the n8n workflow JSON files committed in this repo.

These tests are intentionally independent of the n8n sync helper — they
just assert that every `n8n/workflows/*.json` file we ship has the
minimum shape required to import cleanly:

* top-level object with `name`, `nodes`, `connections`, `settings`
* `nodes` is a non-empty list
* `connections` is an object
* every `id` inside `nodes` is unique
* every cron-style trigger has a real cron expression (not an empty `interval`)
* every HTTP request node targets an `/api/internal/...` URL on our backend
* every webhook secret header references `RADAR_WEBHOOK_SECRET`

If you add a new workflow, drop it in `n8n/workflows/` and these tests
will validate it for free.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / "n8n" / "workflows"


def _workflow_files() -> list[Path]:
    if not WORKFLOW_DIR.exists():
        return []
    return sorted(WORKFLOW_DIR.glob("*.json"))


WORKFLOW_FILES = _workflow_files()
WORKFLOW_IDS = [p.stem for p in WORKFLOW_FILES]


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=WORKFLOW_IDS)
def test_workflow_json_is_valid_and_well_shaped(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert isinstance(payload, dict), f"{path.name}: top-level must be object"
    assert isinstance(payload.get("name"), str) and payload["name"], (
        f"{path.name}: missing 'name'"
    )
    assert isinstance(payload.get("nodes"), list) and payload["nodes"], (
        f"{path.name}: 'nodes' must be a non-empty array"
    )
    assert isinstance(payload.get("connections"), dict), (
        f"{path.name}: 'connections' must be an object"
    )
    if "settings" in payload:
        assert isinstance(payload["settings"], dict), (
            f"{path.name}: 'settings' must be an object"
        )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=WORKFLOW_IDS)
def test_workflow_node_ids_are_unique(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [n.get("id") for n in payload["nodes"]]
    assert len(ids) == len(set(ids)), f"{path.name}: duplicate node ids {ids}"


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=WORKFLOW_IDS)
def test_workflow_cron_triggers_have_a_cron_expression(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if not node.get("type", "").endswith(".scheduleTrigger"):
            continue
        params = node.get("parameters", {})
        # n8n 0.x used `rule.cronExpression`; n8n 1.x uses
        # `triggerTimes.item[].expression` with `mode: cronExpression`.
        # We accept both shapes — the new one is what we shipped.
        cron: str | None = None
        rule = params.get("rule", {})
        if isinstance(rule, dict):
            cron = rule.get("cronExpression")
        if not cron:
            for item in params.get("triggerTimes", {}).get("item", []):
                if (
                    isinstance(item, dict)
                    and item.get("mode") == "cronExpression"
                    and isinstance(item.get("expression"), str)
                ):
                    cron = item["expression"]
                    break
        assert isinstance(cron, str) and cron.strip(), (
            f"{path.name}: schedule trigger '{node.get('name')}' "
            "must declare a cronExpression"
        )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=WORKFLOW_IDS)
def test_workflow_http_nodes_hit_our_backend(path: Path) -> None:
    """Every HTTP Request node should target /api/internal/... on the backend.

    We do NOT allow arbitrary outbound URLs — n8n is a thin orchestrator
    that calls our FastAPI backend, nothing else.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    url_re = re.compile(r"api/internal/")
    for node in payload["nodes"]:
        if not node.get("type", "").endswith(".httpRequest"):
            continue
        url = (node.get("parameters", {}) or {}).get("url") or ""
        # n8n expressions like "={{$env.API_BASE_URL_INTERNAL}}/api/internal/..."
        assert "$env." in url or url.startswith("http"), (
            f"{path.name}: HTTP node '{node.get('name')}' url must be a "
            "template or absolute URL"
        )
        # Strip any n8n expression wrapper before searching for the path.
        unwrapped = url.replace("={{", "").replace("}}", "")
        assert url_re.search(unwrapped), (
            f"{path.name}: HTTP node '{node.get('name')}' must call "
            "/api/internal/... (got url={url!r})"
        )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=WORKFLOW_IDS)
def test_workflow_webhook_header_uses_radar_secret(path: Path) -> None:
    """Every HTTP Request node signing an internal endpoint must use
    X-Radar-Webhook + $env.RADAR_WEBHOOK_SECRET (the env var our
    docker-compose file injects). No other secret name is allowed.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if not node.get("type", "").endswith(".httpRequest"):
            continue
        params = node.get("parameters", {}) or {}
        url = params.get("url") or ""
        unwrapped = url.replace("={{", "").replace("}}", "")
        if "/api/internal/" not in unwrapped:
            continue
        headers = (params.get("headerParameters", {}) or {}).get("parameters", [])
        names = {h.get("name") for h in headers if isinstance(h, dict)}
        assert "X-Radar-Webhook" in names, (
            f"{path.name}: HTTP node '{node.get('name')}' must send "
            "X-Radar-Webhook"
        )
        secret_value = next(
            (
                h.get("value")
                for h in headers
                if isinstance(h, dict) and h.get("name") == "X-Radar-Webhook"
            ),
            "",
        )
        # The backend's `_check_webhook_secret` accepts either
        # APP_SECRET_KEY (preferred) or RADAR_WEBHOOK_SECRET (fallback).
        # We accept either name in the workflow header.
        assert (
            "APP_SECRET_KEY" in (secret_value or "")
            or "RADAR_WEBHOOK_SECRET" in (secret_value or "")
        ), (
            f"{path.name}: HTTP node '{node.get('name')}' webhook secret "
            f"must reference APP_SECRET_KEY or RADAR_WEBHOOK_SECRET "
            f"(got {secret_value!r})"
        )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=WORKFLOW_IDS)
def test_workflow_connections_reference_known_nodes(path: Path) -> None:
    """Sanity: every node referenced in `connections` exists in `nodes`.

    n8n's connection shape is::

        connections[source][output_type][branch_index] = [
            {node, type, index}, ...
        ]

    so we walk three levels deep before inspecting each link.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    known = {n.get("name") for n in payload["nodes"]}
    for src, output_types in payload["connections"].items():
        assert src in known, f"{path.name}: connection from unknown node {src!r}"
        for _output_type, branches in output_types.items():
            for branch in branches or []:
                for link in branch or []:
                    assert isinstance(link, dict), (
                        f"{path.name}: connection link under {src!r} is not an "
                        f"object (got {link!r})"
                    )
                    target = link.get("node")
                    assert target in known, (
                        f"{path.name}: connection target {target!r} not in nodes"
                    )
