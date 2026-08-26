"""Tests for the Prometheus exposition endpoint (Phase 12)."""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_metrics_endpoint_returns_200_with_prometheus_content_type(client) -> None:
    response = client.get("/api/metrics")
    assert response.status_code == 200
    # The text format media type is the documented Prometheus value.
    assert "text/plain" in response.headers["content-type"]


def test_metrics_endpoint_emits_radar_namespaces(client) -> None:
    body = client.get("/api/metrics").text
    # At least one counter and one histogram from our namespace must be
    # present, even on a fresh app with no traffic.
    assert "radar_pipeline_runs_total" in body
    assert "radar_http_requests_total" in body
    assert "radar_pipeline_duration_seconds" in body


def test_metrics_endpoint_records_http_request(client) -> None:
    """A real GET must increment the HTTP middleware counter for that path label."""
    from tests._phase12_helpers import counter_value

    # We trigger `/api/health/live` and assert the `health_live` counter ticks.
    labels = {"method": "GET", "path": "health_live", "status": "200"}
    before = counter_value("radar_http_requests_total", labels)
    response = client.get("/api/health/live")
    assert response.status_code == 200
    after = counter_value("radar_http_requests_total", labels)
    assert after == before + 1


def test_metrics_endpoint_disabled_returns_404(client, monkeypatch) -> None:
    monkeypatch.setenv("PROMETHEUS_METRICS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        response = client.get("/api/metrics")
        assert response.status_code == 404
        body = response.text
        assert "prometheus_metrics_enabled=false" in body
    finally:
        get_settings.cache_clear()


def test_metrics_endpoint_is_unauthenticated(client) -> None:
    """Prometheus scrapers don't speak HMAC; the network is the boundary."""
    response = client.get("/api/metrics")
    # No `X-Radar-Webhook` header required — 200 with no auth challenge.
    assert response.status_code == 200
