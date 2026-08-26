"""Tests for /api/internal/screening/run."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _ingest_and_cluster(client) -> dict:
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
    return cluster.json()


async def test_screening_run_returns_report(client):
    cluster_body = await _ingest_and_cluster(client)
    if cluster_body.get("opportunities_created", 0) == 0:
        pytest.skip("clustering produced no opportunities; nothing to screen")

    response = client.post(
        "/api/internal/screening/run",
        json={"limit": 10, "use_mock": True},
    )
    assert response.status_code == 200
    body = response.json()
    for key in (
        "opportunities_attempted",
        "opportunities_screened",
        "opportunities_failed",
        "signals_created",
        "errors",
    ):
        assert key in body, f"missing field {key}"
    assert body["opportunities_screened"] >= 1
    assert body["signals_created"] >= 1


async def test_screening_run_with_no_data_is_noop(client):
    response = client.post("/api/internal/screening/run", json={"use_mock": True})
    assert response.status_code == 200
    body = response.json()
    assert body["opportunities_attempted"] == 0
    assert body["opportunities_screened"] == 0


async def test_screening_run_requires_no_webhook_when_secret_empty(client):
    """Conftest sets APP_SECRET_KEY='' — webhook is bypassed."""
    response = client.post("/api/internal/screening/run", json={"use_mock": True})
    assert response.status_code == 200


async def test_screening_run_populates_opportunity_total_score(client):
    """After screening, GET /opportunities surfaces screened rows."""
    cluster_body = await _ingest_and_cluster(client)
    if cluster_body.get("opportunities_created", 0) == 0:
        pytest.skip("no opportunities persisted after clustering")

    screen = client.post(
        "/api/internal/screening/run",
        json={"limit": 10, "use_mock": True},
    )
    assert screen.status_code == 200
    assert screen.json()["opportunities_screened"] >= 1

    listing = client.get("/api/opportunities?limit=200")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert items, "expected at least one opportunity listed after screening"
    # Screened opportunities have non-zero total_score.
    screened = [o for o in items if o.get("status") == "screened"]
    assert screened, "expected at least one screened opportunity"
    assert any(o["total_score"] > 0 for o in screened)


async def test_screening_run_with_use_mock_false_falls_back_when_no_keys(client):
    """Without API keys and use_mock=False, factory returns MockLLMProvider."""
    await _ingest_and_cluster(client)
    response = client.post(
        "/api/internal/screening/run",
        json={"limit": 5, "use_mock": False},
    )
    assert response.status_code == 200
    # No API key → mock fallback → screened at least one opportunity.
    body = response.json()
    assert body["opportunities_screened"] >= 1
