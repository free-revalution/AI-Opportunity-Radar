"""Tests for the internal webhook endpoints used by n8n / workers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_discovery_run_returns_report(client):
    response = client.post("/api/internal/discovery/run", json={"sources": ["github"], "mock": True})
    assert response.status_code == 200
    body = response.json()
    assert body["sources_attempted"] == 1
    assert body["sources_succeeded"] == 1
    assert body["items_inserted"] >= 1


async def test_discovery_run_with_no_body_uses_defaults(client):
    response = client.post("/api/internal/discovery/run")
    assert response.status_code == 200


async def test_digest_build_returns_top_opportunities(client):
    response = client.post("/api/internal/digest/build", json={})
    assert response.status_code == 200
    body = response.json()
    assert "top_opportunities" in body
    assert isinstance(body["top_opportunities"], list)