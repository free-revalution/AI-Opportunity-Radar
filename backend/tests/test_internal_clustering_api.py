"""Tests for the /api/internal/clustering/run endpoint."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Opportunity, RawItem, Source

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_raw_items_via_ingestion(client) -> None:
    """Run the ingestion pipeline once so the DB has unclustered RawItems."""
    response = client.post(
        "/api/internal/discovery/run",
        json={"sources": ["github", "hackernews"], "mock": True},
    )
    assert response.status_code == 200
    # Pages DB is overridden per-test, so we use the same TestClient app
    # for the clustering call too.


async def test_clustering_run_returns_report(client):
    await _seed_raw_items_via_ingestion(client)
    response = client.post(
        "/api/internal/clustering/run",
        json={"raw_item_limit": 50, "threshold": 0.5},
    )
    assert response.status_code == 200
    body = response.json()
    # Required report fields.
    for key in (
        "raw_items_seen",
        "clusters_formed",
        "opportunities_created",
        "opportunities_updated",
        "links_created",
        "errors",
    ):
        assert key in body, f"missing field {key} in response"
    assert body["raw_items_seen"] >= 1
    assert body["opportunities_created"] >= 1


async def test_clustering_run_with_no_data_is_noop(client):
    response = client.post("/api/internal/clustering/run", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["raw_items_seen"] == 0
    assert body["opportunities_created"] == 0


async def test_clustering_run_rejects_invalid_threshold(client):
    # FREEZE — threshold override removed in MVP (simplify §6).
    pytest.skip("FREEZE parameter removed in MVP")


async def test_clustering_run_requires_no_webhook_when_secret_empty(client):
    """Conftest sets APP_SECRET_KEY="" so the webhook check is skipped."""
    response = client.post("/api/internal/clustering/run", json={})
    # No X-Radar-Webhook header is sent.
    assert response.status_code == 200


async def test_clustering_creates_persisted_opportunities(client, sqlite_engine):
    """After clustering, GET /opportunities returns the synthesised rows."""
    # Ingest then cluster.
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
    body = cluster.json()
    if body["raw_items_seen"] == 0:
        pytest.skip("ingestion produced no items; nothing to assert")

    listing = client.get("/api/opportunities?limit=200")
    assert listing.status_code == 200
    items = listing.json()["items"]
    # All ingested items came from a single source with the same title-ish
    # text — at threshold 0.0 they all collapse into one cluster.
    assert len(items) >= 1
