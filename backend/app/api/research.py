"""Research reports API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/research/{research_id}", summary="Fetch a research report")
async def get_research(research_id: str) -> dict[str, Any]:
    if research_id != "demo-001":
        raise HTTPException(status_code=404, detail="research report not found")

    return {
        "id": research_id,
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