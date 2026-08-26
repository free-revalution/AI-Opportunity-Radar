"""Trends API — currently a stub that returns demo data.

Real implementation lives in `app.services.scoring` once the LLM +
clustering pipeline is wired in (Phase 5/6).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/trends", summary="Recent trends surfaced by the radar")
async def list_trends() -> dict[str, Any]:
    return {
        "items": [
            {"keyword": "ai avatar", "velocity": 0.91, "mentions_24h": 142},
            {"keyword": "browser agent", "velocity": 0.84, "mentions_24h": 98},
            {"keyword": "video localization", "velocity": 0.78, "mentions_24h": 64},
            {"keyword": "sales copilot", "velocity": 0.71, "mentions_24h": 52},
            {"keyword": "legal research ai", "velocity": 0.66, "mentions_24h": 41},
        ],
    }