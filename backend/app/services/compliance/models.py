"""Compliance Engine — shared dataclasses + enums.

``ComplianceResult`` is the **only** return shape from the engine — every
detector emits a partial result, and ``ComplianceService`` merges them
into a final ``ComplianceResult`` per ``docs/下一阶段开发技术方案.md`` #27.

``RiskType`` and ``RiskLevel`` are string-based enums (str, Enum) so they
serialise cleanly in JSON / audit logs without bespoke encoders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class RiskType(str, Enum):
    """Discrete risk categories the engine tracks.

    All detectors must tag findings with one of these. ``UNKNOWN`` is
    reserved for future expansion; we never want to drop a finding just
    because it doesn't fit the current taxonomy.
    """

    PRIVACY = "privacy"
    PII = "pii"
    COPYRIGHT = "copyright"
    MISINFORMATION = "misinformation"
    DEFAMATION = "defamation"
    ILLEGAL_CONTENT = "illegal_content"
    FINANCIAL_ADVICE = "financial_advice"
    MEDICAL_ADVICE = "medical_advice"
    POLITICAL_RISK = "political_risk"
    PROMPT_INJECTION = "prompt_injection"
    SOURCE_POLICY = "source_policy"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Discrete risk bands — see 下一阶段 #29 for semantics."""

    LOW = "low"            # auto-pass
    MEDIUM = "medium"      # enqueue for review
    HIGH = "high"          # no auto-publish
    BLOCKED = "blocked"    # never generate / publish


# Per 下一阶段 #23 — 5-tier compliance posture for *data sources*.
# We reuse the same letters so admin UIs and audit logs can carry one
# canonical vocabulary across signals + sources.
class ComplianceLevel(str, Enum):
    A = "A"  # official API / explicit license — auto-allow
    B = "B"  # public page / reasonable access — allow with limits
    C = "C"  # commercial / automation gated — manual review
    D = "D"  # login / paywall / technical restriction — block
    E = "E"  # explicitly forbids automation / commercial use — block


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ComplianceResult:
    """Aggregated output of the Compliance Engine.

    Fields are populated by ``ComplianceService`` from the per-detector
    results. ``allowed`` is the *final* decision — derived from
    ``risk_level`` and ``requires_human_review``.

    The engine is fail-closed: if any detector raised an exception or
    if the risk score crosses the BLOCKED threshold, ``allowed`` is
    False. Callers MUST honour ``allowed`` — bypassing it bypasses the
    commercial-grade safety contract documented in ``docs/COMPLIANCE.md``.
    """

    allowed: bool
    risk_score: float
    risk_level: RiskLevel
    risk_types: list[RiskType] = field(default_factory=list)
    reason: str = ""
    requires_human_review: bool = False

    # Free-form audit metadata — detector names, thresholds, exceptions,
    # etc. Always JSON-safe. Keep small (a few KB) so we can write to the
    # audit_logs table without bloating it.
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Stable JSON shape for API responses + audit logs."""
        return {
            "allowed": self.allowed,
            "risk_score": round(self.risk_score, 4),
            "risk_level": self.risk_level.value,
            "risk_types": [rt.value for rt in self.risk_types],
            "reason": self.reason,
            "requires_human_review": self.requires_human_review,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Score → Level mapping
# ---------------------------------------------------------------------------
# Per 下一阶段 #29:
#   LOW      → auto-pass
#   MEDIUM   → review queue
#   HIGH     → no auto-publish
#   BLOCKED  → never generate / publish
#
# Thresholds are inclusive lower bounds. A score of exactly 0.30 maps to
# MEDIUM; 0.69 maps to HIGH; 0.85 maps to BLOCKED.
_LOW_THRESHOLD: float = 0.30
_MEDIUM_THRESHOLD: float = 0.55
_HIGH_THRESHOLD: float = 0.70


def risk_level_for_score(score: float) -> RiskLevel:
    """Map a normalised risk score to a discrete ``RiskLevel``.

    Clamps the input to ``[0.0, 1.0]`` so callers don't have to think
    about edge cases (e.g. a detector rounding to 1.0001).
    """
    s = max(0.0, min(1.0, float(score)))
    if s < _LOW_THRESHOLD:
        return RiskLevel.LOW
    if s < _MEDIUM_THRESHOLD:
        return RiskLevel.MEDIUM
    if s < _HIGH_THRESHOLD:
        return RiskLevel.HIGH
    return RiskLevel.BLOCKED


def content_safe_to_publish(
    result: ComplianceResult,
    *,
    allow_medium: bool = False,
) -> bool:
    """Single source of truth for "is this allowed to be published?".

    Used by:
      * ``ContentGenerator`` — gate LLM output before persistence
      * Publisher adapters — gate auto-publish vs. manual-review
      * Feishu bot — gate user-visible replies

    Default policy (``allow_medium=False``):
      only LOW can auto-publish. Anything else requires a human.

    Pass ``allow_medium=True`` to permit MEDIUM in the public-facing
    Feishu reply (e.g. paid-tier customers who opted into more
    aggressive surfacing) — HIGH and BLOCKED still never auto-publish.
    """
    if result.allowed is False:
        return False
    if result.risk_level == RiskLevel.LOW:
        return True
    if result.risk_level == RiskLevel.MEDIUM:
        return bool(allow_medium) and not result.requires_human_review
    return False


def merge_risk_types(*lists: Iterable[RiskType]) -> list[RiskType]:
    """Deduplicate + order-preserving union of risk types."""
    seen: set[RiskType] = set()
    out: list[RiskType] = []
    for lst in lists:
        for rt in lst:
            if rt not in seen:
                seen.add(rt)
                out.append(rt)
    return out


__all__ = [
    "ComplianceLevel",
    "ComplianceResult",
    "RiskLevel",
    "RiskType",
    "content_safe_to_publish",
    "merge_risk_types",
    "risk_level_for_score",
]