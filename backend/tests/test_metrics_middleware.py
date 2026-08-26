"""Tests for the HTTP metrics middleware + path-labeller."""

from __future__ import annotations

from app.middleware import label_path


def test_label_path_maps_known_routes_to_bounded_labels() -> None:
    assert label_path("/api/health/live") == "health_live"
    assert label_path("/api/health/ready") == "health_ready"
    assert label_path("/api/health") == "health"
    assert label_path("/api/metrics") == "metrics"
    assert label_path("/api/opportunities") == "opportunities"
    assert label_path("/api/opportunities/abc-123") == "opportunity_detail"
    assert label_path("/api/opportunities/abc-123/research") == "opportunity_research"
    assert label_path("/api/research/42") == "research_detail"
    assert label_path("/api/internal/discovery/run") == "internal_discovery"
    assert label_path("/api/internal/research/run/7") == "internal_research_one"


def test_label_path_collapses_unknown_paths_to_other() -> None:
    assert label_path("/random/route") == "other"
    assert label_path("/api/internal/something-new") == "other"


def test_http_middleware_records_request_via_client(client) -> None:
    """A real GET must increment `radar_http_requests_total`."""
    response = client.get("/api/health/live")
    assert response.status_code == 200

    metrics = client.get("/api/metrics").text
    # The `/api/health/live` request must have produced at least one
    # sample line with path="health_live".
    assert 'radar_http_requests_total{method="GET",path="health_live"' in metrics


def test_http_middleware_records_duration(client) -> None:
    """A real GET must produce a `_count` increment for the latency histogram."""
    response = client.get("/api/health/live")
    assert response.status_code == 200

    metrics = client.get("/api/metrics").text
    # `_count` series of the histogram carries the same labels.
    assert 'radar_http_request_duration_seconds_count{method="GET",path="health_live"}' in metrics
