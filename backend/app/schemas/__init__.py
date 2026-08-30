"""Pydantic schemas (request / response models)."""

from app.schemas.common import ErrorResponse, HealthResponse
from app.schemas.content_opportunity import (
    ContentOpportunityListResponse,
    ContentOpportunityRejectRequest,
    ContentOpportunityResponse,
)
from app.schemas.opportunity import (
    OpportunityListResponse,
    OpportunityResponse,
    TriggerResearchResponse,
)

__all__ = [
    "ContentOpportunityListResponse",
    "ContentOpportunityRejectRequest",
    "ContentOpportunityResponse",
    "ErrorResponse",
    "HealthResponse",
    "OpportunityListResponse",
    "OpportunityResponse",
    "TriggerResearchResponse",
]