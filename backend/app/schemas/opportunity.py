"""Pydantic schemas — request/response contracts for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Recommendation = Literal[
    "strongly_recommend",
    "recommend",
    "watch",
    "not_recommended",
    "insufficient_data",
]


class OpportunityResponse(BaseModel):
    """Public view of an Opportunity."""

    model_config = ConfigDict(from_attributes=True)

    id: int | str
    slug: str
    title: str
    summary: Optional[str] = None
    category: Optional[str] = None
    market: Optional[str] = None
    target_user: Optional[str] = None
    source_count: int = 0
    score: float = Field(0.0, description="alias for total_score")
    total_score: float = 0.0
    trend_score: float = 0.0
    demand_score: float = 0.0
    monetization_score: float = 0.0
    competition_gap_score: float = 0.0
    china_gap_score: float = 0.0
    execution_score: float = 0.0
    status: str = "detected"
    recommendation: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OpportunityListResponse(BaseModel):
    items: list[OpportunityResponse]
    total: int
    limit: int
    offset: int
    generated_at: datetime


class TriggerResearchResponse(BaseModel):
    opportunity_id: int | str
    status: Literal["queued", "running", "completed", "failed"]
    job_id: Optional[str] = None