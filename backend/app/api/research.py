"""Research reports API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import OpportunityRepository

router = APIRouter()


# Synthetic demo report — used when the DB has no research report yet
# (Phase 7 will replace it with the deep-research engine output).
_DEMO_REPORT = {
    "id": "demo-001",
    "opportunity_id": "demo-001",
    "executive_summary": (
        "AI Ecommerce Avatar is a high-conviction opportunity: growing "
        "Reddit/HN chatter, multiple paying Shopify sellers, and a clear "
        "China gap."
    ),
    "problem": "Cross-border sellers need localized product imagery at scale.",
    "target_customers": ["Shopify sellers", "Amazon FBA", "DTC brands"],
    "demand_evidence": [
        {"source": "Reddit r/SaaS", "url": "https://reddit.com/r/SaaS", "note": "Repeated posts"},
        {"source": "Product Hunt", "url": "https://producthunt.com", "note": "3 launches in 90d"},
    ],
    "competitors": [
        {"name": "Arcads", "price": "$99/mo", "weakness": "no avatar, only video"},
        {"name": "Synthesia", "price": "$89/mo", "weakness": "enterprise focus"},
    ],
    "china_market": "Baidu / Xiaohongshu show minimal dedicated tooling.",
    "china_gap": 87,
    "monetization": ["SaaS $19-99/mo", "Usage credits"],
    "mvp": {"features": ["Avatar gen", "Background swap", "Bulk upload"], "estimated_days": 14},
    "risks": ["Stable Diffusion API cost", "Shopify TOS"],
    "recommendation": "strongly_recommend",
    "confidence": 82,
    "sources": [],
}


@router.get("/research/{research_id}", summary="Fetch a research report")
async def get_research(
    research_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    # Phase 2: the lookup keys off the opportunity slug/id; the actual
    # research_reports table is populated by the Phase 7 deep-research
    # worker. For now we serve the synthetic demo for demo-001.
    repo = OpportunityRepository(session)
    opp = await repo.get_by_slug(research_id)
    if opp is not None:
        return {
            **_DEMO_REPORT,
            "id": str(opp.id),
            "opportunity_id": opp.slug,
            "executive_summary": (
                f"Opportunity '{opp.title}' — score {opp.total_score}/100, status '{opp.status}'. "
                "Detailed research lands in Phase 7."
            ),
        }
    if research_id == "demo-001":
        return _DEMO_REPORT

    raise HTTPException(status_code=404, detail="research report not found")