"""Smoke tests for FastAPI endpoints.

These do NOT require a live Postgres / Redis — they exercise the
schema-level contracts and the liveness probe only.
"""

from __future__ import annotations


def test_liveness_endpoint(client) -> None:
    response = client.get("/api/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "alive"}


def test_health_endpoint_returns_expected_shape(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert body["service"] == "ai-opportunity-radar-backend"
    assert "components" in body
    assert set(body["components"]) == {
        "postgres", "redis", "llm", "firecrawl",
        "browser_use", "telegram", "n8n",
    }


def test_health_endpoint_is_resilient_to_missing_services(client) -> None:
    """Even with no Postgres / Redis / API keys the endpoint must answer 200."""
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"healthy", "degraded", "down"}


def test_opportunities_list_returns_demo_data(client) -> None:
    response = client.get("/api/opportunities")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert body["total"] >= 3
    assert all("score" in o for o in body["items"])


def test_opportunity_detail_404_for_unknown_id(client) -> None:
    response = client.get("/api/opportunities/demo-does-not-exist")
    assert response.status_code == 404


def test_sources_endpoint_returns_registered_connectors(client) -> None:
    response = client.get("/api/sources")
    assert response.status_code == 200
    body = response.json()
    slugs = {item["slug"] for item in body["items"]}
    assert {"github", "reddit", "hackernews", "producthunt", "rss"} <= slugs


def test_trends_endpoint_returns_demo_keywords(client) -> None:
    response = client.get("/api/trends")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) >= 1


def test_trigger_research_returns_202(client) -> None:
    response = client.post("/api/opportunities/demo-001/research")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"