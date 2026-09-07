"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.health import router as health_router
from app.api.feishu_inbound import router as feishu_inbound_router
from app.api.internal import router as internal_router
from app.api.metrics import router as metrics_router
from app.api.notifications import router as notifications_router
from app.api.opportunities import router as opportunities_router
from app.api.readiness import router as readiness_router
from app.api.research import router as research_router
from app.api.signals import router as signals_router
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

    # Phase 25 v2.1 — surface the Feishu App credentials state at boot
    # so an operator notices a missing pair before the first event
    # arrives (which would otherwise 401 silently). Dev / mock
    # environments skip the warning so local boots stay quiet.
    if settings.app_env in {"staging", "prod"}:
        has_id = bool((settings.feishu_app_id or "").strip())
        has_secret = bool((settings.feishu_app_secret or "").strip())
        if not (has_id and has_secret):
            logger.error(
                "feishu_credentials_missing",
                has_app_id=has_id,
                has_app_secret=has_secret,
                hint=(
                    "set FEISHU_APP_ID and FEISHU_APP_SECRET so the bot "
                    "can reply to inbound events; without them the bot "
                    "will fall back to feishu_webhook_url only."
                ),
            )
        elif not (settings.feishu_webhook_url or "").strip():
            logger.warning(
                "feishu_webhook_not_configured",
                hint=(
                    "no feishu_webhook_url — App API transient errors "
                    "won't have a fallback channel."
                ),
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
    # Phase 33 PR-33-G: 释放 task_runner 共享 httpx client
    from app.services.feishu.task_runner import aclose_shared_client

    await aclose_shared_client()


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
    # Phase 17 — admin Content Center browses signals via webhook auth.
    app.include_router(signals_router, prefix="/api", tags=["signals"])
    app.include_router(sources_router, prefix="/api", tags=["sources"])
    app.include_router(trends_router, prefix="/api", tags=["trends"])
    app.include_router(metrics_router, prefix="/api", tags=["metrics"])
    app.include_router(internal_router, prefix="/api/internal", tags=["internal"])
    # Phase 6 v2.0: Feishu event-subscription callback (inbound bot commands).
    # Mounted at `/api/feishu/event` — uses Feishu's own Verification Token,
    # NOT the shared `X-Radar-Webhook` (that's for outbound internal calls).
    app.include_router(feishu_inbound_router, prefix="/api/feishu", tags=["feishu"])

    # Phase 36+ — surface unhandled exceptions back to the caller.
    # FastAPI's default 500 handler returns ``{"detail": "Internal
    # Server Error"}`` with zero context, which makes ``POST
    # /api/internal/pipeline/run`` failures show up in the Feishu bot
    # as a useless "Internal Server Error" message. We override it so
    # dev/staging operators see the exception type + first line; prod
    # still gets the safe generic message.
    import traceback as _tb

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        # — HTTPException 是 FastAPI 自己 raise 的(404/422/...),
        # 走它自己的 handler,我们不插手。
        from fastapi import HTTPException as _HTTPException

        if isinstance(exc, _HTTPException):
            # — 借用默认 handler: 返回 exc.detail + status_code
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )

        tb_str = _tb.format_exc()
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        logger.error(
            "unhandled_exception",
            path=str(request.url.path),
            method=request.method,
            exc_type=exc.__class__.__name__,
            error=first_line[:300],
            exc_info=True,
        )
        if settings.app_env == "prod":
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error"},
            )
        # — dev / staging: 把异常类型 + 第一行 error 贴回去,operator
        # 不用去翻 docker logs 就能定位。
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "error": f"{exc.__class__.__name__}: {first_line[:280]}",
                "trace_tail": tb_str.splitlines()[-12:],
            },
        )

    return app


app = create_app()