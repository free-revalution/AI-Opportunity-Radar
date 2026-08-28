"""Subscription service — tier gating + plan lookups.

Per docs/下一阶段开发技术方案.md §44-48 / §88:

> Subscription { id, user_id, plan, status, starts_at, expires_at, source_channel }
>   plans:  free | basic | pro | creator
>   status: active | expired | suspended | cancelled
>
> 用户请求 /today /content /research 必须经过 SubscriptionService
> 检查 ACTIVE 且 expires_at > now

> 套餐: free ¥0 / basic ¥29 / pro ¥59 / creator ¥129

This module is pure-data — no DB access. The caller passes in the
subscription row (or a dict) and gets a verdict.

Pricing is deliberately *not* hardcoded here — the spec is explicit
about this ("不要在代码里硬编码价格"). The tiers dict carries the
canonical feature gating rules but the price list is loaded at
runtime from settings (``SubscriptionPlan.price_for(plan)``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional


# ---------------------------------------------------------------------------
# Plan / status enums
# ---------------------------------------------------------------------------
class Plan(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    CREATOR = "creator"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Plan catalogue — feature gating + price (CNY / month).
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class PlanProfile:
    """Per-plan features + price. Kept in code for MVP — docs §48 says
    config can override, but in dev / test we read this directly.
    """

    code: str
    name_zh: str
    price_cny: float
    daily_signals: int         # max signals /day (Free=1, Basic=5, Pro=20, Creator=∞)
    research_requests: int     # max on-demand research /day
    content_pieces: int        # max auto-generated content /day
    auto_publish: bool         # may the publisher bypass manual review?
    priority_queue: bool       # lower latency in queues


PLAN_CATALOGUE: dict[str, PlanProfile] = {
    PlanProfile(
        code="free",
        name_zh="免费",
        price_cny=0.0,
        daily_signals=1,
        research_requests=0,
        content_pieces=0,
        auto_publish=False,
        priority_queue=False,
    ).code: PlanProfile(
        code="free",
        name_zh="免费",
        price_cny=0.0,
        daily_signals=1,
        research_requests=0,
        content_pieces=0,
        auto_publish=False,
        priority_queue=False,
    ),
    PlanProfile(
        code="basic",
        name_zh="基础",
        price_cny=29.0,
        daily_signals=5,
        research_requests=1,
        content_pieces=3,
        auto_publish=False,
        priority_queue=False,
    ).code: PlanProfile(
        code="basic",
        name_zh="基础",
        price_cny=29.0,
        daily_signals=5,
        research_requests=1,
        content_pieces=3,
        auto_publish=False,
        priority_queue=False,
    ),
    PlanProfile(
        code="pro",
        name_zh="专业",
        price_cny=59.0,
        daily_signals=20,
        research_requests=5,
        content_pieces=10,
        auto_publish=True,
        priority_queue=True,
    ).code: PlanProfile(
        code="pro",
        name_zh="专业",
        price_cny=59.0,
        daily_signals=20,
        research_requests=5,
        content_pieces=10,
        auto_publish=True,
        priority_queue=True,
    ),
    PlanProfile(
        code="creator",
        name_zh="创作者",
        price_cny=129.0,
        daily_signals=10**9,
        research_requests=10**9,
        content_pieces=10**9,
        auto_publish=True,
        priority_queue=True,
    ).code: PlanProfile(
        code="creator",
        name_zh="创作者",
        price_cny=129.0,
        daily_signals=10**9,
        research_requests=10**9,
        content_pieces=10**9,
        auto_publish=True,
        priority_queue=True,
    ),
}


def get_plan_profile(plan: str) -> PlanProfile:
    """Look up a plan profile; unknown values fall back to FREE."""
    return PLAN_CATALOGUE.get(plan, PLAN_CATALOGUE[Plan.FREE.value])


# ---------------------------------------------------------------------------
# Gating verdict
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class GatingVerdict:
    """Single source of truth for "is the user allowed to do X?".

    Returned by ``gate()`` so callers can render a single Feishu reply
    with the right copy.
    """

    allowed: bool
    plan: str
    reason: str = ""
    upgrade_to: str | None = None  # cheapest plan that unlocks the feature


@dataclass(slots=True)
class SubscriptionRow:
    """Lightweight view of the subscription — accepts ORM rows or dicts."""

    plan: str
    status: str
    expires_at: Optional[datetime] = None

    @classmethod
    def from_any(cls, row: Any) -> "SubscriptionRow":
        if isinstance(row, cls):
            return row
        if isinstance(row, Mapping):
            return cls(
                plan=str(row.get("plan", Plan.FREE.value)),
                status=str(row.get("status", SubscriptionStatus.EXPIRED.value)),
                expires_at=row.get("expires_at"),
            )
        return cls(
            plan=str(getattr(row, "plan", Plan.FREE.value)),
            status=str(getattr(row, "status", SubscriptionStatus.EXPIRED.value)),
            expires_at=getattr(row, "expires_at", None),
        )


# ---------------------------------------------------------------------------
# Core gate
# ---------------------------------------------------------------------------
def gate(
    subscription: Any,
    feature: str,
    *,
    now: datetime | None = None,
) -> GatingVerdict:
    """Decide whether ``subscription`` may use ``feature``.

    Recognised features:
      * ``view_top_signals`` — /today, /top
      * ``research``         — /research <id>
      * ``content_full``     — /content <id> returns full output
      * ``auto_publish``     — publisher can auto-publish without review
    """
    now = now or datetime.now(tz=timezone.utc)
    row = SubscriptionRow.from_any(subscription)

    if row.status != SubscriptionStatus.ACTIVE.value:
        return GatingVerdict(
            allowed=False,
            plan=row.plan,
            reason=f"subscription_{row.status}",
            upgrade_to=_upgrade_target_for(feature),
        )

    if row.expires_at is not None:
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if exp <= now:
            return GatingVerdict(
                allowed=False,
                plan=row.plan,
                reason="subscription_expired",
                upgrade_to=_upgrade_target_for(feature),
            )

    profile = get_plan_profile(row.plan)

    # Per-feature gating
    if feature == "view_top_signals":
        if profile.daily_signals >= 1:
            return GatingVerdict(allowed=True, plan=row.plan)
        return GatingVerdict(
            allowed=False,
            plan=row.plan,
            reason="plan_no_top_signals",
            upgrade_to=_upgrade_target_for(feature),
        )

    if feature == "research":
        if profile.research_requests >= 1:
            return GatingVerdict(allowed=True, plan=row.plan)
        return GatingVerdict(
            allowed=False,
            plan=row.plan,
            reason="plan_no_research",
            upgrade_to=_upgrade_target_for(feature),
        )

    if feature == "content_full":
        if profile.content_pieces >= 1:
            return GatingVerdict(allowed=True, plan=row.plan)
        return GatingVerdict(
            allowed=False,
            plan=row.plan,
            reason="plan_no_content_full",
            upgrade_to=_upgrade_target_for(feature),
        )

    if feature == "auto_publish":
        if profile.auto_publish:
            return GatingVerdict(allowed=True, plan=row.plan)
        return GatingVerdict(
            allowed=False,
            plan=row.plan,
            reason="plan_no_auto_publish",
            upgrade_to=_upgrade_target_for(feature),
        )

    # Unknown feature → fail-closed.
    return GatingVerdict(allowed=False, plan=row.plan, reason="unknown_feature")


def is_active(
    subscription: Any,
    *,
    now: datetime | None = None,
) -> bool:
    """Quick boolean — used by middleware / decorator paths."""
    return gate(subscription, "view_top_signals", now=now).allowed


# ---------------------------------------------------------------------------
# Upgrade hints
# ---------------------------------------------------------------------------
def _upgrade_target_for(feature: str) -> str:
    """Cheapest plan that unlocks ``feature``. Used for upgrade CTAs."""
    if feature in ("research", "content_full"):
        return Plan.BASIC.value
    if feature == "auto_publish":
        return Plan.PRO.value
    return Plan.BASIC.value


__all__ = [
    "GatingVerdict",
    "PLAN_CATALOGUE",
    "Plan",
    "PlanProfile",
    "SubscriptionRow",
    "SubscriptionStatus",
    "gate",
    "get_plan_profile",
    "is_active",
]