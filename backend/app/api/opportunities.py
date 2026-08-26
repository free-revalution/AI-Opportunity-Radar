"""Opportunities API.

Stub endpoints — full implementation lands in later phases (scoring +
clustering). The contract here is stable so the frontend can wire up
against it immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


_DEMO_OPPORTUNITIES: list[dict[str, Any]] = [
    {
        "id": "demo-001",
        "title": "AI Ecommerce Avatar",
        "score": 91,
        "category": "AI SaaS",
        "summary": "Generate AI product avatars for cross-border ecommerce listings.",
        "china_gap": 87,
        "execution_score": 82,
        "monetization": "$19-99/month",
        "recommendation": "strongly_recommend",
    },
    {
        "id": "demo-002",
        "title": "AI Browser Agent",
        "score": 88,
        "category": "Agent",
        "summary": "Browser-based autonomous agents for enterprise data extraction.",
        "china_gap": 70,
        "execution_score": 65,
        "monetization": "Usage-based",
        "recommendation": "recommend",
    },
    {
        "id": "demo-003",
        "title": "AI Video Localization",
        "score": 84,
        "category": "AI Media",
        "summary": "Translate and dub long-form video into 30+ languages with lip-sync.",
        "china_gap": 60,
        "execution_score": 70,
        "monetization": "$0.10/minute",
        "recommendation": "recommend",
    },
]


@router.get("/opportunities", summary="List opportunities")
async def list_opportunities(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    category: str | None = Query(None, description="Filter by category"),
) -> dict[str, Any]:
    items = _DEMO_OPPORTUNITIES
    if category:
        items = [o for o in items if o["category"].lower() == category.lower()]
    page = items[offset : offset + limit]
    return {
        "items": page,
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/opportunities/{opportunity_id}", summary="Opportunity detail")
async def get_opportunity(opportunity_id: str) -> dict[str, Any]:
    for opp in _DEMO_OPPORTUNITIES:
        if opp["id"] == opportunity_id:
            return opp
    # Real lookup will hit the database once Phase 2 models are wired in.
    if opportunity_id.startswith("demo-"):
        raise HTTPException(status_code=404, detail="opportunity not found")
    raise HTTPException(status_code=404, detail="opportunity not found")


@router.post(
    "/opportunities/{opportunity_id}/research",
    summary="Trigger deep research for an opportunity",
    status_code=202,
)
async def trigger_research(opportunity_id: str) -> dict[str, Any]:
    return {
        "opportunity_id": opportunity_id,
        "status": "queued",
        "job_id": f"job-{opportunity_id}",
    }