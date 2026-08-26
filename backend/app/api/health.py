"""Health-check endpoint.

Reports per-dependency status (postgres, redis, llm, firecrawl, browser_use,
telegram, n8n). Each check is wrapped so a single failure never breaks the
overall endpoint.

Phase 2: postgres check now uses a real injected session so the result
reflects actual DB connectivity rather than a fresh-engine probe.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.utils import get_logger

router = APIRouter()
logger = get_logger(__name__)


async def _check_postgres(session: AsyncSession) -> dict[str, Any]:
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=3.0)
        return {"status": "healthy"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "down", "error": str(exc)[:200]}


async def _check_redis() -> dict[str, Any]:
    """Cheap Redis ping using an inline client (avoids hard dependency at import time)."""
    return await _check_redis_with_client()


async def _check_redis_with_client(
    client: object | None = None,
) -> dict[str, Any]:
    """Ping Redis; tests inject a fake client to exercise the healthy branch.

    Without a client we build an inline `redis.asyncio` connection from
    `settings.redis_url` and dispose it on exit. Any error becomes
    `{"status": "down"}` with the message attached.
    """
    try:
        if client is None:
            import redis.asyncio as redis_async  # type: ignore[import-not-found]

            owned = redis_async.from_url(
                get_settings().redis_url, decode_responses=True
            )
            try:
                pong = await asyncio.wait_for(owned.ping(), timeout=3.0)
                return {"status": "healthy" if pong else "down"}
            finally:
                await owned.aclose()
        pong = await asyncio.wait_for(client.ping(), timeout=3.0)
        return {"status": "healthy" if pong else "down"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "down", "error": str(exc)[:200]}


def _check_llm() -> dict[str, Any]:
    settings = get_settings()
    providers = {
        "MiniMax": bool(settings.MiniMax_api_key),
        "openai": bool(settings.openai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "gemini": bool(settings.gemini_api_key),
    }
    configured = [name for name, ok in providers.items() if ok]
    if not configured:
        return {"status": "degraded", "note": "no LLM provider configured"}
    return {"status": "healthy", "providers": configured, "default": settings.llm_default_provider}


def _probe_url(url: str, *, timeout: float = 3.0) -> tuple[bool, str]:
    """Best-effort synchronous GET against *url*.

    Returns `(ok, detail)`. Never raises — any exception becomes
    `ok=False` with the message attached. The endpoint is wired so a
    probe failure can never break the overall health response.
    """
    try:
        import httpx  # local import — the app must boot without it

        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
        if 200 <= response.status_code < 300:
            return True, ""
        return False, f"HTTP {response.status_code}: {response.text[:120]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def _check_firecrawl() -> dict[str, Any]:
    s = get_settings()
    if not s.firecrawl_api_key:
        return {"status": "degraded", "note": "FIRECRAWL_API_KEY not set"}
    ok, detail = _probe_url(f"{s.firecrawl_api_url.rstrip('/')}/api/v1/health")
    if ok:
        return {"status": "healthy"}
    return {"status": "degraded", "note": "firecrawl probe failed", "detail": detail}


def _check_browser_use() -> dict[str, Any]:
    s = get_settings()
    if not s.browser_use_api_key:
        return {"status": "degraded", "note": "BROWSER_USE_API_KEY not set"}
    ok, detail = _probe_url(f"{s.browser_use_api_url.rstrip('/')}/healthz")
    if ok:
        return {"status": "healthy"}
    return {
        "status": "degraded",
        "note": "browser_use probe failed",
        "detail": detail,
    }


def _check_telegram() -> dict[str, Any]:
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        return {"status": "degraded", "note": "Telegram bot not configured"}
    return {"status": "healthy"}


def _check_n8n() -> dict[str, Any]:
    s = get_settings()
    if not s.n8n_base_url:
        return {"status": "degraded", "note": "N8N_BASE_URL not set"}
    return {"status": "healthy", "url": s.n8n_base_url}


@router.get("/health", summary="Aggregated health check")
async def health_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    postgres, redis_ = await asyncio.gather(
        _check_postgres(session),
        _check_redis(),
        return_exceptions=True,
    )

    def _safe(value: Any) -> dict[str, Any]:
        if isinstance(value, Exception):
            return {"status": "down", "error": str(value)[:200]}
        return value

    components = {
        "postgres": _safe(postgres),
        "redis": _safe(redis_),
        "llm": _check_llm(),
        "firecrawl": _check_firecrawl(),
        "browser_use": _check_browser_use(),
        "telegram": _check_telegram(),
        "n8n": _check_n8n(),
    }

    statuses = [c["status"] for c in components.values()]
    if all(s == "healthy" for s in statuses):
        overall = "healthy"
    elif any(s == "down" for s in statuses):
        overall = "down"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "service": "ai-opportunity-radar-backend",
        "version": "0.1.0",
        "components": components,
    }


@router.get("/health/live", summary="Liveness probe (no dependency checks)")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}
