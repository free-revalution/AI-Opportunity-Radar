"""Research reports API.

Returns the Phase 7 `ResearchReport` rows. When the opportunity exists
but no report has been written yet, we synthesise a fallback response
from the opportunity's stored metadata so the dashboard never crashes
on a missing report.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Opportunity, ResearchReport
from app.repositories import OpportunityRepository
from app.services.scoring import recommendation_for

router = APIRouter()


# Synthetic demo report — only used when the DB has no rows at all
# (the seed script writes these so the dashboard isn't empty on a fresh
# install). Mirrors the Phase 7 `ResearchReport` JSON shape.
_DEMO_REPORT: dict[str, Any] = {
    "id": "demo-001",
    "opportunity_id": "demo-001",
    "executive_summary": (
        "AI Ecommerce Avatar is a high-conviction opportunity: growing "
        "Reddit/HN chatter, multiple paying Shopify sellers, and a clear "
        "China gap."
    ),
    "market_analysis": (
        "Cross-border sellers spend $3–6B/year on product imagery. AI avatar "
        "generation collapses both cost and turnaround time."
    ),
    "competition_analysis": (
        "Arcads focuses on video at $99/mo with no avatar support. "
        "Synthesia targets enterprise at $89/mo and lacks Shopify integration."
    ),
    "china_analysis": (
        "Baidu / Xiaohongshu show minimal dedicated tooling for cross-border "
        "avatar generation."
    ),
    "monetization_analysis": (
        "SaaS at $19-99/mo plus usage credits. PLG funnel from a free tier "
        "with one free avatar."
    ),
    "mvp_analysis": (
        "Avatar gen + background swap + bulk upload. ~14 days to ship on "
        "top of an LLM vision API."
    ),
    "risk_analysis": (
        "Stable Diffusion API cost may compress margins; Shopify TOS may "
        "restrict on-platform automation."
    ),
    "recommendation": "strongly_recommend",
    "confidence": 0.82,
    "sources": [],
}


def _serialise_report(report: ResearchReport) -> dict[str, Any]:
    """Map a `ResearchReport` ORM row to the dashboard JSON shape."""
    sources = []
    raw_sources = (report.sources_json or {}).get("items", []) or []
    for entry in raw_sources:
        if isinstance(entry, dict) and entry.get("url"):
            sources.append(
                {"url": entry.get("url"), "title": entry.get("title", "")}
            )
    return {
        "id": str(report.id),
        "opportunity_id": str(report.opportunity_id),
        "executive_summary": report.executive_summary or "",
        "market_analysis": report.market_analysis or "",
        "competition_analysis": report.competition_analysis or "",
        "china_analysis": report.china_analysis or "",
        "monetization_analysis": report.monetization_analysis or "",
        "mvp_analysis": report.mvp_analysis or "",
        "risk_analysis": report.risk_analysis or "",
        "recommendation": report.recommendation or "insufficient_data",
        "confidence": float(report.confidence or 0.0),
        "sources": sources,
    }


def _fallback_for(opp: Opportunity) -> dict[str, Any]:
    """Build a graceful fallback when no `ResearchReport` row exists yet."""
    score = float(opp.total_score or 0.0)
    return {
        "id": str(opp.id),
        "opportunity_id": str(opp.id),
        "executive_summary": (
            f"Opportunity '{opp.title}' scored {score:.1f}/100 "
            f"(status '{opp.status}'). Deep research has not produced a "
            "report yet — run the research worker to populate this view."
        ),
        "market_analysis": "",
        "competition_analysis": "",
        "china_analysis": "",
        "monetization_analysis": "",
        "mvp_analysis": "",
        "risk_analysis": "",
        "recommendation": recommendation_for(score),
        "confidence": 0.0,
        "sources": [],
        "pending": True,
    }


async def _fetch_report_for_opportunity(
    session: AsyncSession, opportunity_id: int
) -> Optional[ResearchReport]:
    """Latest `ResearchReport` row for an opportunity."""
    result = await session.execute(
        select(ResearchReport)
        .where(ResearchReport.opportunity_id == opportunity_id)
        .order_by(ResearchReport.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _resolve_opportunity(
    session: AsyncSession, key: str
) -> Optional[Opportunity]:
    repo = OpportunityRepository(session)
    try:
        numeric_id = int(key)
        opp = await repo.get_by_id(numeric_id)
        if opp is not None:
            return opp
    except (ValueError, TypeError):
        pass
    return await repo.get_by_slug(key)


@router.get("/research/{research_id}", summary="Fetch a research report")
async def get_research(
    research_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Look up by opportunity id (numeric or slug) and return the report."""
    opp = await _resolve_opportunity(session, research_id)
    if opp is not None:
        report = await _fetch_report_for_opportunity(session, opp.id)
        if report is not None:
            return _serialise_report(report)
        return _fallback_for(opp)

    if research_id == "demo-001":
        return _DEMO_REPORT

    raise HTTPException(status_code=404, detail="research report not found")
