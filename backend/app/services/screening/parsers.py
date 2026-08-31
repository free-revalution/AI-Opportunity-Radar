"""Parse + validate LLM screening responses.

The screening service relies on this module to coerce loosely-typed
LLM output into a strictly-typed `ScreeningResult` dataclass. Anything
that does not match the schema raises `ValidationError` so the caller
can either retry or mark the opportunity as `failed`.

Phase 29 fix — LLM responses occasionally omit a numeric sub-score
(``trend_strength`` etc.) and return ``null`` instead. The previous
implementation raised :class:`ValidationError` for every such row,
which on a real-mode run with 50+ opportunities turned into 8-10
``opp <id>: parse error: trend_strength: expected int, got NoneType``
errors and left ``opportunities_failed`` inflated. We now fall back
to a neutral default (50 — mid-range) and warn-log the event so the
operator can investigate if the pattern becomes systemic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.utils import ValidationError, get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ScreeningResult:
    """Strictly-typed screening outcome."""

    is_business_relevant: bool
    category: str
    problem: str
    potential_business: str
    trend_strength: int
    demand_strength: int
    monetization_potential: int
    competition_gap: int
    china_gap: int
    execution_feasibility: int
    keywords: list[str] = field(default_factory=list)
    confidence: float = 0.5


# Neutral mid-range default applied when the LLM returns ``None`` for
# a numeric sub-score. 50 keeps the opportunity in the screening pool
# (so a real run doesn't lose 10-20% of its signals) while flagging it
# as uncertain — the warn-log gives the operator visibility.
_NEUTRAL_INT_DEFAULT = 50


def _as_int(
    value: Any,
    *,
    field: str,
    default: int = _NEUTRAL_INT_DEFAULT,
) -> int:
    """Coerce ``value`` to a clamped int in [0, 100].

    Falls back to ``default`` (50) on ``None`` so LLM responses that
    omit the field don't kill the entire screening for the row. All
    other type-mismatch errors still raise :class:`ValidationError` —
    silent coercion would mask real schema drift.
    """
    if value is None:
        # Phase 29 fix — the previous behaviour raised
        # ``ValidationError("trend_strength: expected int, got NoneType")``
        # for every LLM response that omitted the field, which on real
        # runs inflated ``opportunities_failed`` to 10-20% of attempts.
        # 50 keeps the opportunity in the screening pool; the warn-log
        # surfaces systemic drift so the operator can retune the prompt.
        logger.warning(
            "screening_field_missing",
            field=field,
            default=default,
        )
        return default
    if isinstance(value, bool):
        # bool is subclass of int — disallow.
        raise ValidationError(f"{field}: expected int, got bool")
    if isinstance(value, int):
        return max(0, min(100, value))
    if isinstance(value, float):
        return max(0, min(100, int(round(value))))
    if isinstance(value, str):
        try:
            return max(0, min(100, int(float(value))))
        except ValueError as exc:
            raise ValidationError(f"{field}: cannot parse int from {value!r}") from exc
    raise ValidationError(f"{field}: expected int, got {type(value).__name__}")


def _as_str(value: Any, *, field: str, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()[:2000]
    if isinstance(value, (int, float, bool)):
        return str(value)
    return default


def _as_keywords(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                token = item.strip().lower()
                if token and token not in out:
                    out.append(token)
            elif isinstance(item, (int, float)):
                out.append(str(item))
        return out[:20]
    if isinstance(value, str):
        # Comma-separated fallback for models that didn't follow the schema.
        return [t.strip().lower() for t in value.split(",") if t.strip()][:20]
    return []


def _as_confidence(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(max(0.0, min(1.0, value)))
    if isinstance(value, str):
        try:
            return float(max(0.0, min(1.0, float(value))))
        except ValueError:
            return 0.5
    return 0.5


def parse_screening_response(payload: Any) -> ScreeningResult:
    """Validate + coerce an LLM response into a `ScreeningResult`.

    Raises `ValidationError` if any required field is missing or
    unparseable.
    """
    if not isinstance(payload, dict):
        raise ValidationError("screening response must be a JSON object")

    if "is_business_relevant" not in payload:
        raise ValidationError("missing field: is_business_relevant")
    is_relevant = bool(payload["is_business_relevant"])

    try:
        result = ScreeningResult(
            is_business_relevant=is_relevant,
            category=_as_str(payload.get("category"), field="category", default=""),
            problem=_as_str(payload.get("problem"), field="problem", default=""),
            potential_business=_as_str(
                payload.get("potential_business"),
                field="potential_business",
                default="",
            ),
            trend_strength=_as_int(
                payload.get("trend_strength"), field="trend_strength"
            ),
            demand_strength=_as_int(
                payload.get("demand_strength"), field="demand_strength"
            ),
            monetization_potential=_as_int(
                payload.get("monetization_potential"),
                field="monetization_potential",
            ),
            competition_gap=_as_int(
                payload.get("competition_gap"), field="competition_gap"
            ),
            china_gap=_as_int(payload.get("china_gap"), field="china_gap"),
            execution_feasibility=_as_int(
                payload.get("execution_feasibility"), field="execution_feasibility"
            ),
            keywords=_as_keywords(payload.get("keywords")),
            confidence=_as_confidence(payload.get("confidence")),
        )
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"screening parse failed: {exc}") from exc

    if not result.is_business_relevant:
        # Enforce the rule from the system prompt: cap sub-scores at 35.
        result.trend_strength = min(result.trend_strength, 35)
        result.demand_strength = min(result.demand_strength, 35)
        result.monetization_potential = min(result.monetization_potential, 30)

    return result


__all__ = ["ScreeningResult", "parse_screening_response"]
