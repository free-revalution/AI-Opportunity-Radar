"""Signal Score service — V2.0 商业信号雷达的核心评分.

Per docs/下一阶段开发技术方案.md §7:

> Signal Score =
>     Freshness              × 0.20
>   + Velocity               × 0.20
>   + Evidence Confidence    × 0.20
>   + Novelty                × 0.15
>   + Commercial Value       × 0.10
>   + Actionability          × 0.10
>   + Information Scarcity   × 0.05

All sub-scores are 0..100. The aggregate maps to 4 bands:

  0..49    LOW      — background noise, no action
  50..69   WATCH    — interesting but not actionable yet
  70..84   HOT      — likely to need attention
  85..100  BREAKING — must surface in /today immediately

This module is pure-data — no I/O, no DB access — so the same inputs
give the same outputs in tests, audit logs, and the admin console.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Sub-score weights — keep these in lockstep with docs §7.
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "freshness": 0.20,
    "velocity": 0.20,
    "evidence": 0.20,
    "novelty": 0.15,
    "commercial_value": 0.10,
    "actionability": 0.10,
    "scarcity": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------
class SignalBand(str, Enum):
    LOW = "low"
    WATCH = "watch"
    HOT = "hot"
    BREAKING = "breaking"


# ---------------------------------------------------------------------------
# Status state machine — mirror of the ``Signal.status`` column.
# ---------------------------------------------------------------------------
class SignalStatus(str, Enum):
    DISCOVERED = "discovered"
    VALIDATING = "validating"
    VERIFIED = "verified"
    ANALYZING = "analyzing"
    PUBLISHED = "published"
    EXPIRED = "expired"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class SignalScoreInputs:
    """Bundles the 7 sub-scores needed for the Signal Score formula.

    All fields are 0..100 (clamped on compute).
    """

    freshness: float = 0.0
    velocity: float = 0.0
    evidence: float = 0.0
    novelty: float = 0.0
    commercial_value: float = 0.0
    actionability: float = 0.0
    scarcity: float = 0.0

    @classmethod
    def from_signal_row(cls, row: Mapping[str, Any]) -> "SignalScoreInputs":
        """Convenience adapter for ``Signal`` ORM rows.

        Accepts either an ORM instance or a dict-like object. Pulls the
        Velocity field from the legacy ``velocity_score`` column.
        """
        return cls(
            freshness=_get(row, "freshness_score", 0.0),
            velocity=_get(row, "velocity_score", 0.0),
            evidence=_get(row, "evidence_score", 0.0),
            novelty=_get(row, "novelty_score", 0.0),
            commercial_value=_get(row, "commercial_value_score", 0.0),
            actionability=_get(row, "actionability_score", 0.0),
            scarcity=_get(row, "scarcity_score", 0.0),
        )


@dataclass(slots=True)
class SignalScoreResult:
    total: float
    band: SignalBand
    components: dict[str, float] = field(default_factory=dict)
    weighted_breakdown: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get(row: Any, key: str, default: Any = None) -> Any:
    """Dict-or-attribute read — works on ORM rows and plain dicts."""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_signal_score(inputs: SignalScoreInputs | Mapping[str, Any]) -> SignalScoreResult:
    """Compute the weighted Signal Score from a bundle of sub-scores.

    Accepts either a ``SignalScoreInputs`` instance or a mapping
    (dict-like or ORM row) with the seven sub-score fields.
    """
    if isinstance(inputs, SignalScoreInputs):
        components = {
            "freshness": inputs.freshness,
            "velocity": inputs.velocity,
            "evidence": inputs.evidence,
            "novelty": inputs.novelty,
            "commercial_value": inputs.commercial_value,
            "actionability": inputs.actionability,
            "scarcity": inputs.scarcity,
        }
    else:
        components = {
            "freshness": float(_get(inputs, "freshness_score", 0.0)),
            "velocity": float(_get(inputs, "velocity_score", 0.0)),
            "evidence": float(_get(inputs, "evidence_score", 0.0)),
            "novelty": float(_get(inputs, "novelty_score", 0.0)),
            "commercial_value": float(_get(inputs, "commercial_value_score", 0.0)),
            "actionability": float(_get(inputs, "actionability_score", 0.0)),
            "scarcity": float(_get(inputs, "scarcity_score", 0.0)),
        }

    total = 0.0
    breakdown: dict[str, float] = {}
    for name, weight in WEIGHTS.items():
        clamped = _clamp(components[name])
        weighted = clamped * weight
        breakdown[name] = round(weighted, 4)
        total += weighted

    total = round(total, 4)
    return SignalScoreResult(
        total=total,
        band=band_for_score(total),
        components={k: round(_clamp(v), 4) for k, v in components.items()},
        weighted_breakdown=breakdown,
    )


def band_for_score(score: float) -> SignalBand:
    """Map a Signal Score (0..100) to a discrete band."""
    if score < 50:
        return SignalBand.LOW
    if score < 70:
        return SignalBand.WATCH
    if score < 85:
        return SignalBand.HOT
    return SignalBand.BREAKING


def freshness_from_age(
    detected_at: datetime | None,
    *,
    now: datetime | None = None,
    half_life_hours: float = 6.0,
) -> float:
    """Compute a 0..100 Freshness score from signal age.

    A signal detected *now* scores 100; one older than ``2 × half_life_hours``
    decays to ~25. Anything older than 24h saturates at 0.
    """
    if not detected_at:
        return 0.0
    if detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(tz=timezone.utc)
    age_hours = max(0.0, (now - detected_at).total_seconds() / 3600.0)
    if age_hours >= 24.0:
        return 0.0
    # Exponential decay: 100 at age=0, 50 at age=half_life, ~25 at 2*half_life.
    import math

    return round(100.0 * math.pow(0.5, age_hours / half_life_hours), 2)


def evidence_from_source_count(source_count: int) -> float:
    """Map source-count to 0..100 Evidence Confidence.

    Per 下一阶段 §9:
      1 source    → low confidence
      2 sources   → medium
      3+ sources  → high
    """
    if source_count <= 0:
        return 0.0
    if source_count == 1:
        return 30.0
    if source_count == 2:
        return 60.0
    if source_count == 3:
        return 85.0
    # 4+ sources: asymptotic toward 100.
    return min(100.0, 85.0 + 5.0 * (source_count - 3))


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------
_VALID_TRANSITIONS: dict[SignalStatus, frozenset[SignalStatus]] = {
    SignalStatus.DISCOVERED: frozenset({SignalStatus.VALIDATING, SignalStatus.REJECTED}),
    SignalStatus.VALIDATING: frozenset({SignalStatus.VERIFIED, SignalStatus.REJECTED}),
    SignalStatus.VERIFIED: frozenset({SignalStatus.ANALYZING, SignalStatus.REJECTED}),
    SignalStatus.ANALYZING: frozenset({SignalStatus.PUBLISHED, SignalStatus.REJECTED}),
    SignalStatus.PUBLISHED: frozenset({SignalStatus.EXPIRED}),
    SignalStatus.EXPIRED: frozenset(),
    SignalStatus.REJECTED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """True if ``current → target`` is a valid state-machine transition.

    Accepts both enum and string values for convenience. Unknown values
    always return False (fail-closed).
    """
    try:
        cur = SignalStatus(current)
        tgt = SignalStatus(target)
    except ValueError:
        return False
    return tgt in _VALID_TRANSITIONS.get(cur, frozenset())


__all__ = [
    "ConsolidationResult",
    "DEFAULT_TITLE_JACCARD_THRESHOLD",
    "DEFAULT_TITLE_LOOKBACK_DAYS",
    "SignalBand",
    "SignalScoreInputs",
    "SignalScoreResult",
    "SignalStatus",
    "WEIGHTS",
    "band_for_score",
    "can_transition",
    "compute_signal_score",
    "consolidate_raw_item",
    "evidence_from_source_count",
    "freshness_from_age",
    "jaccard",
    "normalize_title",
]


# ---------------------------------------------------------------------------
# Phase 14B — signal consolidation (multi-source attach).
# Eager-imported: the consolidator pulls SQLAlchemy + ORM models, but those
# are already loaded by the time any caller imports this module.
# ---------------------------------------------------------------------------
from app.services.signals import consolidator as _consolidator  # noqa: E402

# Re-bind the public names so `from app.services.signals import X` works
# without callers having to know about the submodule.
ConsolidationResult = _consolidator.ConsolidationResult
DEFAULT_TITLE_JACCARD_THRESHOLD = _consolidator.DEFAULT_TITLE_JACCARD_THRESHOLD
DEFAULT_TITLE_LOOKBACK_DAYS = _consolidator.DEFAULT_TITLE_LOOKBACK_DAYS
consolidate_raw_item = _consolidator.consolidate_raw_item
jaccard = _consolidator.jaccard
normalize_title = _consolidator.normalize_title