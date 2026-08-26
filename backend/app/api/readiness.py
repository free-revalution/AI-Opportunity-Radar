"""Readiness probe — separate from liveness (Phase 12).

Kubernetes (and any orchestrator with the same semantics) distinguishes
between:

* **Liveness** — the process is up and the event loop is healthy. Always
  200. Failures here mean "restart the container".
* **Readiness** — the process can serve requests. 503 means "stop
  sending it traffic for now", but do NOT restart. Used by load
  balancers and rolling-deploy gates.

This probe only checks the two boot-critical dependencies:

* Postgres — without it we can't write anything.
* Redis — without it the queue / cache is gone.

It deliberately ignores Firecrawl / Browser Use / Telegram / n8n — those
are scrape-time concerns; the app must keep serving dashboard reads
even when an external service is down. If the dashboard itself is
degraded because the DB is gone, we return 503 so the orchestrator
pulls the pod out of the LB pool.

`_check_redis_with_client(client=None)` (in `app/api/health.py`) lets
tests inject a fake client so the success branch is exerciseable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.health import _check_postgres, _check_redis_with_client
from app.db import get_session

router = APIRouter()


@router.get(
    "/health/ready",
    summary="Readiness probe (Postgres + Redis must be healthy)",
)
async def readiness_endpoint(
    session: AsyncSession = Depends(get_session),  # noqa: B008 - FastAPI dependency
) -> Response:
    """Return 200 when the two boot-critical deps are healthy.

    Any other state — `degraded` or `down` for either component — yields
    a 503 so the orchestrator pulls the pod from the load-balancer pool
    without restarting it.
    """
    pg = await _check_postgres(session)
    rd = await _check_redis_with_client()

    pg_ok = pg.get("status") == "healthy"
    rd_ok = rd.get("status") == "healthy"
    overall_ok = pg_ok and rd_ok
    body = {
        "status": "ready" if overall_ok else "not_ready",
        "components": {"postgres": pg, "redis": rd},
    }
    status_code = 200 if overall_ok else 503
    return Response(
        content=_json_dumps(body),
        media_type="application/json",
        status_code=status_code,
    )


def _json_dumps(obj: object) -> bytes:
    import json

    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


__all__ = ["router"]
