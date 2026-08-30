"""Pydantic schemas — ContentOpportunity (Phase 17).

Public/admin view of a vertical interpretation of a Signal. Mirrors the
``OpportunityResponse`` shape — ``from_attributes=True`` so we can pass
the ORM row directly when needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ContentOpportunityResponse(BaseModel):
    """Admin-facing view of one ContentOpportunity row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    signal_id: int
    platform: str = "general"
    audience: Optional[str] = None
    niche: Optional[str] = None
    tone: Optional[str] = None
    content_angle: Optional[str] = None
    hook: Optional[str] = None
    title_candidates: Optional[dict[str, Any]] = None
    material_ideas: Optional[dict[str, Any]] = None
    script_outline: Optional[str] = None
    recommended_length: Optional[int] = None
    cta: Optional[str] = None
    risk_warning: Optional[str] = None
    content_score: float = 0.0
    status: str = "draft"
    # Phase 17 — compliance gate verdict (mirrors ``metadata_json``).
    compliance_blocked: bool = False
    compliance_risk_score: float = 0.0
    compliance_risk_types: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ContentOpportunityListResponse(BaseModel):
    """List envelope — mirrors OpportunityListResponse."""

    items: list[ContentOpportunityResponse]
    total: int
    limit: int
    offset: int


class ContentOpportunityRejectRequest(BaseModel):
    """Body for ``POST /api/admin/content_opportunities/{id}/reject``."""

    reason: Optional[str] = Field(default=None, max_length=255)
