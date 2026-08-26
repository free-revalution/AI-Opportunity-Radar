"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.opportunities import router as opportunities_router
from app.api.research import router as research_router
from app.api.sources import router as sources_router
from app.api.trends import router as trends_router
from app.config import get_settings
from app.db import close_db, init_db
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

    # Routers
    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(opportunities_router, prefix="/api", tags=["opportunities"])
    app.include_router(research_router, prefix="/api", tags=["research"])
    app.include_router(sources_router, prefix="/api", tags=["sources"])
    app.include_router(trends_router, prefix="/api", tags=["trends"])
    app.include_router(internal_router, prefix="/api/internal", tags=["internal"])

    return app


app = create_app()