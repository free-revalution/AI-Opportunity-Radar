"""Tests for /api/internal/scoring/run + /api/internal/scoring/score/{id}."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _ingest_cluster_screen(client) -> dict:
    ingest = client.post(
        "/api/internal/discovery/run",
        json={"sources": ["hackernews"], "mock": True},
    )
    assert ingest.status_code == 200
    cluster = client.post(
        "/api/internal/clustering/run",
        json={"raw_item_limit": 50, "threshold": 0.0},
    )
    assert cluster.status_code == 200
    screen = client.post(
        "/api/internal/screening/run",
        json={"limit": 10, "use_mock": True},
    )
    assert screen.status_code == 200
    return cluster.json()


async def test_scoring_run_returns_report(client):
    cluster_body = await _ingest_cluster_screen(client)
    if cluster_body.get("opportunities_created", 0) == 0:
        pytest.skip("clustering produced no opportunities; nothing to score")

    response = client.post("/api/internal/scoring/run", json={})
    assert response.status_code == 200
    body = response.json()
    for key in (
        "opportunities_attempted",
        "opportunities_scored",
        "opportunities_marked_eligible",
        "research_jobs_created",
        "unchanged",
        "errors",
    ):
        assert key in body, f"missing field {key}"


async def test_scoring_run_with_no_data_is_noop(client):
    response = client.post("/api/internal/scoring/run", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["opportunities_attempted"] == 0


async def test_scoring_run_marks_eligible_above_threshold(client):
    cluster_body = await _ingest_cluster_screen(client)
    if cluster_body.get("opportunities_created", 0) == 0:
        pytest.skip("clustering produced no opportunities")

    screen = client.post(
        "/api/internal/screening/run",
        json={"limit": 10, "use_mock": True},
    )
    scored = client.post("/api/internal/scoring/run", json={})
    body = scored.json()
    # At least one screened opportunity should now have a status update.
    listing = client.get("/api/opportunities?limit=200")
    items = listing.json()["items"]
    screened_or_eligible = [
        o for o in items if o.get("status") in {"scored", "research_eligible"}
    ]
    assert screened_or_eligible, "expected at least one scored opportunity"
    # Body should report ≥ 1 mark-eligible (mock scores are high).
    assert body["opportunities_attempted"] >= 1


async def test_score_one_endpoint_returns_payload(client):
    # FREEZE — /api/internal/scoring/score/{id} removed in MVP (simplify §6).
    pytest.skip("FREEZE endpoint removed in MVP")


async def test_score_one_endpoint_404_for_missing(client):
    # FREEZE — /api/internal/scoring/score/{id} removed in MVP (simplify §6).
    pytest.skip("FREEZE endpoint removed in MVP")


async def test_scoring_run_respects_custom_threshold(client):
    cluster_body = await _ingest_cluster_screen(client)
    if cluster_body.get("opportunities_created", 0) == 0:
        pytest.skip("clustering produced no opportunities")

    # A threshold above the maximum possible score → nothing eligible.
    response = client.post(
        "/api/internal/scoring/run",
        json={"trigger_threshold": 1000.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["opportunities_marked_eligible"] == 0
    assert body["research_jobs_created"] == 0
