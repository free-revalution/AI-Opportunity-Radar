"""Vertical Agent registry — Phase 12F + Phase 13A.

Per docs/下一阶段开发技术方案.md §15:

> agents/
>   base.py        — VerticalAgent protocol
>   content.py     — HeuristicContentRadarAgent (baseline, no LLM)
>   llm_content.py — LLMContentRadarAgent (Phase 13A, LLM-backed)
>   registry.py    — name → VerticalAgent + build_llm_content_agent factory

The registry is intentionally tiny — process-local, no DB, no async
quirks. ``build_llm_content_agent()`` is the public factory for
constructing an LLM-backed agent with explicit dependencies; the
returned instance is **not** registered globally (callers can swap the
default via ``reset()`` then ``register(...)`` in tests).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .base import VerticalAgent
from .content import HeuristicContentRadarAgent
from .llm_content import LLMContentRadarAgent


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class _Registry:
    """In-process map of vertical name → VerticalAgent instance."""

    def __init__(self) -> None:
        self._agents: dict[str, VerticalAgent] = {}

    def register(self, agent: VerticalAgent) -> None:
        if not agent.name:
            raise ValueError("agent.name must be a non-empty string")
        if agent.name in self._agents:
            raise ValueError(f"agent '{agent.name}' already registered")
        self._agents[agent.name] = agent

    def get(self, name: str) -> VerticalAgent | None:
        return self._agents.get(name)

    def all(self) -> Iterable[VerticalAgent]:
        return self._agents.values()

    def names(self) -> list[str]:
        return list(self._agents.keys())

    def reset(self) -> None:
        self._agents.clear()


_registry = _Registry()

# Default registrations — import-time so callers can `get()` immediately.
_registry.register(HeuristicContentRadarAgent())
_registry.register(LLMContentRadarAgent())  # provider=None → always falls back


def register(agent: VerticalAgent) -> None:
    _registry.register(agent)


def get_agent(name: str) -> VerticalAgent:
    """Strict lookup — raises if the vertical is unknown."""
    a = _registry.get(name)
    if a is None:
        raise KeyError(f"unknown vertical agent: {name!r}")
    return a


def try_get_agent(name: str) -> VerticalAgent | None:
    """Lenient lookup — returns None for unknown verticals."""
    return _registry.get(name)


def agents() -> Iterable[VerticalAgent]:
    return _registry.all()


def names() -> list[str]:
    return _registry.names()


def reset() -> None:
    """Used by tests — clears all registrations, then re-installs defaults."""
    _registry.reset()
    _registry.register(HeuristicContentRadarAgent())
    _registry.register(LLMContentRadarAgent())


def build_llm_content_agent(
    *,
    provider: Optional[Any] = None,
    compliance_service: Optional[Any] = None,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> LLMContentRadarAgent:
    """Construct an LLM-backed Content Radar with explicit dependencies.

    Parameters
    ----------
    provider:
        An ``LLMProvider`` instance. When ``None``, ``analyze()`` falls
        back to the heuristic agent.
    compliance_service:
        Optional ``ComplianceService`` instance — when provided the LLM
        output is gated; if BLOCKED, the agent falls back.
    model, max_tokens, temperature:
        Forwarded to ``provider.complete_json()``.

    Returns a fresh ``LLMContentRadarAgent`` instance — does **not**
    register it in the global registry. Callers that want to swap the
    default ``"llm_content"`` registration can do so via
    ``reset()`` then ``register(...)`` in tests.
    """
    return LLMContentRadarAgent(
        provider=provider,
        compliance_service=compliance_service,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )


__all__ = [
    "agents",
    "build_llm_content_agent",
    "get_agent",
    "names",
    "register",
    "reset",
    "try_get_agent",
]