"""Source-policy detector — gate signals / content by source compliance.

Per 下一阶段 #22 / #23 / #24:

> Data Source Registry 必须新增：
>   compliance_level (A/B/C/D/E)
>   terms_url, robots_url, commercial_use_status, access_method,
>   rate_limit, last_compliance_check, retention_policy
>
> A → allow
> B → allow_with_limits
> C → manual_review
> D → block
> E → block
>
> 任何 Connector 遇到 403 / 429 / CAPTCHA / LOGIN_REQUIRED / PAYWALL /
> ACCESS_DENIED 不得自动绕过,必须 STOP 并记录 ``source_block_reason``。

This module consumes a *source policy record* (the dataclass here) and
returns a risk verdict. It's pure-data — no DB access, no network — so
the same source record can be re-evaluated in tests, audits, and the
admin console.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Source policy record — mirrors what we'll add to the ``sources`` table
# in Phase 12D. Kept here as a plain dataclass so the detector doesn't
# need a DB / ORM dependency.
# ---------------------------------------------------------------------------
class AccessMethod(str, Enum):
    OFFICIAL_API = "official_api"
    PUBLIC_PAGE = "public_page"
    RSS = "rss"
    SEARCH_API = "search_api"
    CRAWLER = "crawler"
    UNKNOWN = "unknown"


class BlockReason(str, Enum):
    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    CAPTCHA = "captcha"
    LOGIN_REQUIRED = "login_required"
    PAYWALL = "paywall"
    ACCESS_DENIED = "access_denied"
    TERMS_VIOLATION = "terms_violation"


@dataclass(slots=True)
class SourcePolicyRecord:
    """Lightweight DTO for compliance evaluation.

    Field names mirror the eventual ``sources`` table columns. Defaults
    represent the *most conservative* posture — a source we know
    nothing about is treated as ``E`` (block) until a human reviews.
    """

    source_id: int
    name: str
    compliance_level: str = "E"  # default to block
    commercial_use_status: str = "unknown"  # allowed | conditional | forbidden | unknown
    access_method: str = "unknown"
    rate_limit: int | None = None  # requests / minute; None = unknown
    last_compliance_check: datetime | None = None
    retention_policy: str = "session"
    robots_url: str | None = None
    terms_url: str | None = None
    enabled: bool = True
    last_block_reason: str | None = None  # any of ``BlockReason`` value


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class SourcePolicyFinding:
    """Result of evaluating a single source record."""

    source_id: int
    allowed: bool
    risk_score: float
    reason: str
    requires_human_review: bool
    policy_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Decision matrix
# ---------------------------------------------------------------------------
# A/B → allowed (B carries a "with limits" caveat that turns into a
# MEDIUM risk score so it surfaces for periodic re-review).
# C       → manual review required.
# D / E   → blocked outright.
#
# We also flip to blocked when:
#   * last_compliance_check is older than 90 days (stale → block),
#   * last_block_reason indicates the source recently hit a forbidden
#     access pattern (per 下一阶段 #24 — never auto-bypass).

_STALE_CHECK_DAYS = 90


def evaluate_source_policy(
    record: SourcePolicyRecord,
    *,
    now: datetime | None = None,
) -> SourcePolicyFinding:
    """Evaluate a single source against the compliance matrix.

    Returns a ``SourcePolicyFinding`` carrying both the *decision*
    (allowed / blocked) and the *risk score* (so the ComplianceService
    can fold it into the aggregated ``ComplianceResult``).
    """
    now = now or datetime.now(tz=timezone.utc)

    metadata: dict[str, Any] = {
        "compliance_level": record.compliance_level,
        "commercial_use_status": record.commercial_use_status,
        "access_method": record.access_method,
        "last_block_reason": record.last_block_reason,
    }

    if not record.enabled:
        return SourcePolicyFinding(
            source_id=record.source_id,
            allowed=False,
            risk_score=0.0,
            reason="source_disabled",
            requires_human_review=False,
            policy_metadata=metadata,
        )

    # Forced blocks --------------------------------------------------------
    if record.last_block_reason:
        metadata["forced_block_reason"] = record.last_block_reason
        return SourcePolicyFinding(
            source_id=record.source_id,
            allowed=False,
            risk_score=0.9,
            reason=f"source_recently_blocked:{record.last_block_reason}",
            requires_human_review=False,
            policy_metadata=metadata,
        )

    level = (record.compliance_level or "E").upper()
    if level == "A":
        return SourcePolicyFinding(
            source_id=record.source_id,
            allowed=True,
            risk_score=0.05,
            reason="level_a_official",
            requires_human_review=False,
            policy_metadata=metadata,
        )
    if level == "B":
        # Stale check flag — MEDIUM risk if not reviewed in 90d.
        stale = _is_stale(record.last_compliance_check, now)
        return SourcePolicyFinding(
            source_id=record.source_id,
            allowed=True,
            risk_score=0.25 if stale else 0.15,
            reason="level_b_public" + ("_stale_check" if stale else ""),
            requires_human_review=stale,
            policy_metadata=metadata,
        )
    if level == "C":
        return SourcePolicyFinding(
            source_id=record.source_id,
            allowed=False,
            risk_score=0.45,
            reason="level_c_manual_review_required",
            requires_human_review=True,
            policy_metadata=metadata,
        )
    # D / E / unknown / anything else → block.
    return SourcePolicyFinding(
        source_id=record.source_id,
        allowed=False,
        risk_score=0.95,
        reason=f"level_{level}_blocked",
        requires_human_review=False,
        policy_metadata=metadata,
    )


def _is_stale(last_check: datetime | None, now: datetime) -> bool:
    if not last_check:
        return True
    # ``last_check`` may be naive or aware — normalise.
    if last_check.tzinfo is None:
        last_check = last_check.replace(tzinfo=timezone.utc)
    age = now - last_check
    return age.days >= _STALE_CHECK_DAYS


__all__ = [
    "AccessMethod",
    "BlockReason",
    "SourcePolicyFinding",
    "SourcePolicyRecord",
    "evaluate_source_policy",
]