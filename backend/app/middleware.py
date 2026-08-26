"""Pure-ASGI middleware that records HTTP request metrics (Phase 12).

We deliberately avoid `starlette.middleware.base.BaseHTTPMiddleware`
because it has known issues with streaming responses (it wraps the
response in a `StreamingResponse` of its own, which inflates latency
and breaks SSE).

The middleware is mounted as a plain ASGI app ahead of the FastAPI
router stack. It does:

1. Capture the start time.
2. Read the raw `method` + `path` from the ASGI scope.
3. Call the downstream app.
4. Extract the response status code from the `send` events.
5. Increment `radar_http_requests_total{method, path, status}` and
   observe `radar_http_request_duration_seconds{method, path}`.

`path` is mapped through `_label_path` so we never blow up cardinality
on `/api/opportunities/{id}` — that label collapses to `opportunities`.
Unknown paths collapse to `path="other"`.
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.metrics import HTTP_DURATION, HTTP_REQUESTS

# ---------------------------------------------------------------------------
# Path allowlist — keep cardinality bounded
# ---------------------------------------------------------------------------

# (regex, label) pairs. First match wins; no match -> "other".
# These labels are chosen to match the operator's mental model of the
# service tree, not the literal URLs.
_PATH_RULES: tuple[tuple[re, str], ...] = (
    (re.compile(r"^/api/health/live$"), "health_live"),
    (re.compile(r"^/api/health/ready$"), "health_ready"),
    (re.compile(r"^/api/health$"), "health"),
    (re.compile(r"^/api/metrics$"), "metrics"),
    (re.compile(r"^/api/opportunities/[^/]+/research$"), "opportunity_research"),
    (re.compile(r"^/api/opportunities/[^/]+$"), "opportunity_detail"),
    (re.compile(r"^/api/opportunities$"), "opportunities"),
    (re.compile(r"^/api/research/[^/]+$"), "research_detail"),
    (re.compile(r"^/api/notifications/history$"), "notifications_history"),
    (re.compile(r"^/api/notifications/digest/preview$"), "digest_preview"),
    (re.compile(r"^/api/notifications/digest/send$"), "digest_send"),
    (re.compile(r"^/api/notifications/opportunity/[^/]+/preview$"), "opp_alert_preview"),
    (re.compile(r"^/api/notifications/opportunity/[^/]+/send$"), "opp_alert_send"),
    (re.compile(r"^/api/sources$"), "sources"),
    (re.compile(r"^/api/trends$"), "trends"),
    # Internal endpoints
    (re.compile(r"^/api/internal/discovery/run$"), "internal_discovery"),
    (re.compile(r"^/api/internal/clustering/run$"), "internal_clustering"),
    (re.compile(r"^/api/internal/screening/run$"), "internal_screening"),
    (re.compile(r"^/api/internal/scoring/run$"), "internal_scoring"),
    (re.compile(r"^/api/internal/scoring/score/[^/]+$"), "internal_scoring_one"),
    (re.compile(r"^/api/internal/research/run$"), "internal_research"),
    (re.compile(r"^/api/internal/research/run/[^/]+$"), "internal_research_one"),
    (re.compile(r"^/api/internal/research/cancel/[^/]+$"), "internal_research_cancel"),
    (re.compile(r"^/api/internal/notifications/.*$"), "internal_notifications"),
    (re.compile(r"^/api/internal/digest/build$"), "internal_digest"),
    # Docs / OpenAPI
    (re.compile(r"^/docs$"), "docs"),
    (re.compile(r"^/redoc$"), "redoc"),
    (re.compile(r"^/openapi.json$"), "openapi"),
    # Frontend static / catch-all
    (re.compile(r"^/_next/.*$"), "next_static"),
    (re.compile(r"^/$"), "root"),
)


def label_path(raw_path: str) -> str:
    """Return the bounded metric label for *raw_path*."""
    for pattern, label in _PATH_RULES:
        if pattern.match(raw_path):
            return label
    return "other"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class HTTPMetricsMiddleware:
    """ASGI middleware that records request count + latency.

    Reads `scope["method"]` + `scope["path"]`, then waits for the first
    `http.response.start` event from downstream to extract `status`. Any
    downstream exception bubbles up unchanged — the middleware only
    observes, never swallows.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            # Lifespan / websocket — skip.
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = label_path(scope.get("path", ""))
        started = time.perf_counter()
        status_holder = {"code": 500}

        async def _send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            duration = time.perf_counter() - started
            HTTP_REQUESTS.labels(
                method=method, path=path, status=str(status_holder["code"])
            ).inc()
            HTTP_DURATION.labels(method=method, path=path).observe(duration)


__all__ = ["HTTPMetricsMiddleware", "label_path"]
