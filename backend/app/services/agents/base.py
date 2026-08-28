"""Vertical Agent protocol + registry — Phase 12F.

Per docs/下一阶段开发技术方案.md §15-17:

> 新增 ContentRadarAgent — 第一个垂直 Agent。
> 位置: backend/app/services/agents/
> 建议: agents/base.py + content.py + registry.py
> 接口: VerticalAgent(Protocol) — async def analyze(signal, context)
>        -> VerticalResult
>
> 输入: Signal + 相关 ResearchReport + source evidence + 用户画像 + platform
> 输出: ContentOpportunity
> 必须: 基于 Signal 事实
> 不得: 自行制造事实

MVP scope (this phase):
  * Protocol definition only — the runtime registry pattern lets us
    plug in new verticals (cross-border e-commerce / sales / recruitment)
    later without breaking anything.
  * A registry keyed by vertical name (e.g. "content", "sales").
  * A `ContentRadarAgent` skeleton that turns a `Signal` + context into
    a `ContentOpportunity` shape — but does NOT yet call the LLM (the
    real LLM call plugs in Phase 13 once we have a verified flow).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Vertical result
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class VerticalResult:
    """Output of a Vertical Agent's analyze() call.

    `payload` carries the vertical-specific shape — for ContentRadarAgent
    this is a ``ContentOpportunity`` projection (title candidates,
    script outline, hook, etc.). The agent must NOT fabricate facts;
    anything in `payload` should trace back to a Signal / ResearchReport
    field.
    """

    vertical: str
    opportunity_id: Optional[int]
    payload: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    sources_used: list[str] = field(default_factory=list)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Vertical context — input to analyze()
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class VerticalContext:
    """Bundles everything an agent might need to produce a result.

    Loose-typed on purpose — the agent picks the bits it cares about.
    Future agents (sales, e-commerce) extend the schema with their own
    fields without breaking the protocol.
    """

    user_id: Optional[int] = None
    feishu_open_id: Optional[str] = None
    platform: str = "general"          # douyin | xiaohongshu | bilibili | wechat | general
    audience: str = ""                 # persona description
    niche: str = ""                    # vertical niche (e.g. "AI tools")
    tone: str = "通俗"                 # 通俗 / 深度 / 营销 / 学术
    language: str = "zh"
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# VerticalAgent protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class VerticalAgent(Protocol):
    """Common shape every vertical agent must satisfy."""

    name: str
    """Stable identifier — used by the registry key + audit logs."""

    async def analyze(
        self,
        signal: Any,
        context: VerticalContext,
        *,
        report: Any | None = None,
    ) -> VerticalResult:
        """Produce a vertical result for the given signal + context."""
        ...


__all__ = [
    "VerticalAgent",
    "VerticalContext",
    "VerticalResult",
]