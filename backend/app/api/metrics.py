"""Prometheus exposition endpoint (Phase 12).

Returns the current snapshot of the metrics registry in the standard
text format. The endpoint is gated by the `prometheus_metrics_enabled`
setting — operators can disable it without removing the middleware
(the middleware still records, but the exposition is hidden).

We do NOT add a webhook secret guard here; Prometheus scrapers don't
speak our HMAC scheme, and the network boundary (cluster-internal
service, ingress allowlist, or a sidecar) is the right place to
restrict access. RUNBOOK.md calls this out.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.config import get_settings
from app.metrics import content_type, render

router = APIRouter()


@router.get(
    "/metrics",
    summary="Prometheus text-format exposition",
    response_class=Response,
)
def metrics_endpoint() -> Response:
    settings = get_settings()
    if not settings.prometheus_metrics_enabled:
        return Response(
            content=b"# prometheus_metrics_enabled=false\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
            status_code=404,
        )
    return Response(content=render(), media_type=content_type())


__all__ = ["router"]
