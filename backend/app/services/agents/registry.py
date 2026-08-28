"""Vertical Agent registry — Phase 12F.

Per docs/下一阶段开发技术方案.md §15:

> agents/
>   base.py        — VerticalAgent protocol
>   content.py     — ContentRadarAgent (first vertical)
>   registry.py    — name → VerticalAgent

The registry is intentionally tiny — process-local, no DB, no async
quirks. Phase 13 will add the LLM-backed ContentRadarAgent on top of
the same registry.
"""

from __future__ import annotations

from typing import Iterable

from .base import VerticalAgent
from .content import HeuristicContentRadarAgent


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


__all__ = [
    "agents",
    "get_agent",
    "names",
    "register",
    "reset",
    "try_get_agent",
]