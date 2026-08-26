"""Screening service package — Phase 5.

Public surface:

    ScreeningService     orchestrator (find pending → LLM → persist)
    ScreeningResult      parsed outcome
    parse_screening_response   strict LLM-output validator
    RESPONSE_SCHEMA      JSON schema enforced by the system prompt
    build_user_prompt    composes the per-opportunity user prompt
"""

from app.services.screening.parsers import ScreeningResult, parse_screening_response
from app.services.screening.prompts import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.services.screening.service import ScreeningReport, ScreeningService

__all__ = [
    "RESPONSE_SCHEMA",
    "SYSTEM_PROMPT",
    "ScreeningReport",
    "ScreeningResult",
    "ScreeningService",
    "build_user_prompt",
    "parse_screening_response",
]
