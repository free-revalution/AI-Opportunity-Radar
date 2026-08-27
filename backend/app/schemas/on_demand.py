"""Pydantic schemas for the Phase 5 (v2.0) on-demand research flow.

The on-demand path lets the operator accept a single customer-supplied
URL or topic, generate a research report inline, and (optionally)
attach an Order — all in one request. Used by the
`/api/internal/research/on_demand` endpoints.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.order import ChannelName


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class OnDemandResearchRequest(BaseModel):
    """Body for `POST /api/internal/research/on_demand`.

    Exactly one of `url` or `topic` must be provided. Optional
    customer/order fields create an Order in the same transaction —
    which lets the operator mark "report delivered = revenue event"
    without a second round-trip.
    """

    url: Optional[str] = Field(
        None,
        max_length=1024,
        description="Public URL to research (e.g. a product page).",
    )
    topic: Optional[str] = Field(
        None,
        max_length=255,
        description="Free-text topic / project name (e.g. 'AI 法律合同审核').",
    )

    # Optional customer + payment — attaches an Order to this job.
    customer_name: Optional[str] = Field(None, max_length=128)
    customer_contact: Optional[str] = Field(None, max_length=255)
    amount_cny: Optional[Decimal] = Field(None, ge=0)
    channel: Optional[ChannelName] = None
    payment_method: Optional[str] = Field(None, max_length=64)
    payment_reference: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _exactly_one_of_url_topic(self) -> "OnDemandResearchRequest":
        has_url = bool(self.url and self.url.strip())
        has_topic = bool(self.topic and self.topic.strip())
        if has_url == has_topic:
            raise ValueError(
                "exactly one of `url` or `topic` must be provided "
                "(not both, not neither)"
            )
        return self

    @model_validator(mode="after")
    def _order_fields_are_consistent(self) -> "OnDemandResearchRequest":
        """If any customer / payment field is set, `customer_name` + `amount_cny` are required."""
        any_order_field = any(
            getattr(self, f) is not None
            for f in (
                "customer_name",
                "customer_contact",
                "amount_cny",
                "channel",
                "payment_method",
                "payment_reference",
                "notes",
            )
        )
        if any_order_field and not (self.customer_name and self.amount_cny):
            raise ValueError(
                "to attach an Order, both `customer_name` and `amount_cny` are required"
            )
        return self


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class OnDemandReportSummary(BaseModel):
    """A compact preview of the generated report — enough to render on
    the page without re-fetching the full ResearchReport row."""

    model_config = ConfigDict(from_attributes=True)

    job_id: int
    opportunity_id: int
    status: Literal["pending", "running", "completed", "failed"]
    recommendation: Optional[str] = None
    confidence: float = 0.0
    sources_count: int = 0
    error: Optional[str] = None
    seed_url: Optional[str] = None
    seed_topic: Optional[str] = None
    executive_summary: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class OnDemandCreateResponse(BaseModel):
    """Returned by `POST /research/on_demand`."""

    opportunity_id: int
    opportunity_title: str
    opportunity_slug: str
    job_id: int
    status: Literal["pending", "running", "completed", "failed"]
    recommendation: Optional[str] = None
    confidence: float = 0.0
    sources_count: int = 0
    executive_summary: Optional[str] = None
    order_id: Optional[int] = None


class OnDemandListResponse(BaseModel):
    generated_at: datetime
    items: list[OnDemandReportSummary]
    total: int


class OnDemandDetailResponse(BaseModel):
    """Returned by `GET /research/on_demand/{job_id}` — full job + report."""

    job_id: int
    opportunity_id: int
    opportunity_title: str
    status: str
    recommendation: Optional[str] = None
    confidence: float = 0.0
    sources_count: int = 0
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    seed_url: Optional[str] = None
    seed_topic: Optional[str] = None
    report: Optional[dict[str, Any]] = None  # full ResearchReport payload
