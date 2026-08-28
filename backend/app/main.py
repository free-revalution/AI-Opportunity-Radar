"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.feishu_inbound import router as feishu_inbound_router
from app.api.internal import router as internal_router
from app.api.metrics import router as metrics_router
from app.api.notifications import router as notifications_router
from app.api.opportunities import router as opportunities_router
from app.api.readiness import router as readiness_router
from app.api.research import router as research_router
from app.api.sources import router as sources_router
from app.api.trends import router as trends_router
from app.config import get_settings
from app.db import close_db, init_db
from app.middleware import HTTPMetricsMiddleware
from app.utils import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks."""
    settings = get_settings()
    configure_logging(settings.app_log_level)
    logger.info(
        "startup",
        env=settings.app_env,
        mock_mode=settings.mock_external_services,
        version=__version__,
    )

    # In `local` / `dev` we keep DB init optional — tests + tooling may not need
    # a running Postgres. Real environments call `alembic upgrade head` before
    # booting the service.
    if settings.app_env in {"dev", "staging", "prod"}:
        try:
            await init_db()
        except Exception as exc:  # noqa: BLE001 — log and continue so /health still answers.
            logger.warning("db_init_failed", error=str(exc))

    yield

    logger.info("shutdown")
    await close_db()


def create_app() -> FastAPI:
    """Application factory — used by tests via `create_app()`."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Global AI Business Opportunity Radar",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Phase 12: HTTP request count + latency. Pure ASGI so it does not
    # interfere with streaming responses (Starlette's BaseHTTPMiddleware
    # has known issues there). Always-on — `prometheus_metrics_enabled`
    # gates only the `/api/metrics` exposition endpoint.
    app.add_middleware(HTTPMetricsMiddleware)

    # Routers
    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(readiness_router, prefix="/api", tags=["health"])
    app.include_router(opportunities_router, prefix="/api", tags=["opportunities"])
    app.include_router(research_router, prefix="/api", tags=["research"])
    app.include_router(notifications_router, prefix="/api", tags=["notifications"])
    app.include_router(sources_router, prefix="/api", tags=["sources"])
    app.include_router(trends_router, prefix="/api", tags=["trends"])
    app.include_router(metrics_router, prefix="/api", tags=["metrics"])
    app.include_router(internal_router, prefix="/api/internal", tags=["internal"])
    # Phase 13B: Admin Console API — protected by `X-Radar-Admin-Secret`
    # or `X-Feishu-Open-Id` matching admin_open_ids.
    app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
    # Phase 6 v2.0: Feishu event-subscription callback (inbound bot commands).
    # Mounted at `/api/feishu/event` — uses Feishu's own Verification Token,
    # NOT the shared `X-Radar-Webhook` (that's for outbound internal calls).
    app.include_router(feishu_inbound_router, prefix="/api/feishu", tags=["feishu"])

    return app


app = create_app()