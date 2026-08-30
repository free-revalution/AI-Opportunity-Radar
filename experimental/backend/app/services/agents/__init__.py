"""Vertical Agents package — Content Radar MVP.

Public surface:

    VerticalAgent          (Protocol)        — base.py
    VerticalContext        (dataclass)       — base.py
    VerticalResult         (dataclass)       — base.py
    HeuristicContentRadarAgent               — content.py (deterministic, no LLM)
    LLMContentRadarAgent                    — llm_content.py (Phase 13A, LLM-backed)
    register / get_agent / names / reset
    build_llm_content_agent                  — registry.py factory

Phase 12F wires the protocol + the registry + a heuristic implementation.
Phase 13A adds the LLM-backed Content Radar with the same VerticalResult
shape. LLMContentRadarAgent transparently falls back to the heuristic
when no provider is configured, when the LLM call fails, when the
response fails schema validation, or when ComplianceService blocks the
output.
"""

from __future__ import annotations

from .base import VerticalAgent, VerticalContext, VerticalResult
from .content import HeuristicContentRadarAgent
from .llm_content import CONTENT_RADAR_SCHEMA, LLMContentRadarAgent
from .registry import (
    agents,
    build_llm_content_agent,
    get_agent,
    names,
    register,
    reset,
    try_get_agent,
)

__all__ = [
    "CONTENT_RADAR_SCHEMA",
    "HeuristicContentRadarAgent",
    "LLMContentRadarAgent",
    "VerticalAgent",
    "VerticalContext",
    "VerticalResult",
    "agents",
    "build_llm_content_agent",
    "get_agent",
    "names",
    "register",
    "reset",
    "try_get_agent",
]