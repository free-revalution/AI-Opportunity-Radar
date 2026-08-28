"""Vertical Agents package — Content Radar MVP.

Public surface:

    VerticalAgent          (Protocol)        — base.py
    VerticalContext        (dataclass)       — base.py
    VerticalResult         (dataclass)       — base.py
    HeuristicContentRadarAgent               — content.py (deterministic, no LLM)
    register / get_agent / names / reset     — registry.py

Phase 12F wires the protocol + the registry + a heuristic implementation.
Phase 13 will add the LLM-backed ContentRadarAgent on top.
"""

from __future__ import annotations

from .base import VerticalAgent, VerticalContext, VerticalResult
from .content import HeuristicContentRadarAgent
from .registry import (
    agents,
    get_agent,
    names,
    register,
    reset,
    try_get_agent,
)

__all__ = [
    "HeuristicContentRadarAgent",
    "VerticalAgent",
    "VerticalContext",
    "VerticalResult",
    "agents",
    "get_agent",
    "names",
    "register",
    "reset",
    "try_get_agent",
]