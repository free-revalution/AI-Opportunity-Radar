"""ComplianceService — orchestrator that merges all detectors.

Per 下一阶段 #27 / #108:

> ComplianceResult = {allowed, risk_score, risk_types, reason, requires_human_review}
>
> 任何用户可见内容: Generated Content → Compliance Engine → Allowed?

Public surface:

    ComplianceService().check_content(output, source=None)
        — check generated content (LLM output) for safety

    ComplianceService().check_raw_text(text)
        — check user-supplied text (Feishu chat, search query)

    ComplianceService().check_signal_text(title, summary)
        — light-weight check on signal metadata

    ComplianceService().check_source(record)
        — gate by source compliance posture

The service is *stateless* — a single ``default_service()`` instance is
shared via the ``app.services.compliance`` re-export. Future call sites
(DB persistence, async Redis cache, audit hook) attach via the
``on_decision`` callback list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .content_safety import scan_content_safety
from .copyright_risk import scan_copyright
from .models import (
    ComplianceResult,
    RiskLevel,
    RiskType,
    merge_risk_types,
    risk_level_for_score,
)
from .pii_detector import scan_pii
from .prompt_injection import scan_prompt_injection
from .source_policy import (
    SourcePolicyFinding,
    SourcePolicyRecord,
    evaluate_source_policy,
)


# ---------------------------------------------------------------------------
# Decision callback
# ---------------------------------------------------------------------------
DecisionCallback = Callable[[ComplianceResult, str], None]
"""Called with ``(result, context_label)`` after every check.

Phase 12E wires the audit service here. Today the list is empty and the
service stays free of side-effects, which keeps it trivially testable.
"""


# ---------------------------------------------------------------------------
# ComplianceService
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ComplianceService:
    """Stateless orchestrator — one shared instance per process."""

    on_decision: list[DecisionCallback] = field(default_factory=list)

    # ----- public API -----
    def check_content(
        self,
        output: str,
        source: str | None = None,
        *,
        context: str = "content",
    ) -> ComplianceResult:
        """Heavy check — runs all 4 content detectors."""
        result = self._aggregate_content(output, source)
        self._notify(result, context)
        return result

    def check_raw_text(
        self,
        text: str,
        *,
        context: str = "raw_text",
    ) -> ComplianceResult:
        """Light check on user input — PII + prompt-injection only.

        Skips content_safety (the user didn't write the model output)
        and copyright (no source to compare against).
        """
        result = self._aggregate_input(text)
        self._notify(result, context)
        return result

    def check_signal_text(
        self,
        title: str,
        summary: str,
        *,
        context: str = "signal",
    ) -> ComplianceResult:
        """Check signal-level metadata (lightweight, no copyright)."""
        joined = f"{title}\n{summary or ''}"
        result = self._aggregate_input(joined)
        self._notify(result, context)
        return result

    def check_source(
        self,
        record: SourcePolicyRecord,
        *,
        context: str = "source",
    ) -> ComplianceResult:
        """Gate by source compliance posture."""
        finding = evaluate_source_policy(record)
        result = self._from_source_finding(finding)
        self._notify(result, context)
        return result

    # ----- aggregation helpers -----
    def _aggregate_content(
        self,
        output: str,
        source: str | None,
    ) -> ComplianceResult:
        risk_types: list[RiskType] = []
        score = 0.0
        metadata: dict[str, Any] = {}

        # 1. PII ------------------------------------------------------------
        pii = scan_pii(output)
        metadata["pii_count"] = pii.count
        metadata["pii_high_risk"] = pii.has_high_risk
        if pii.has_high_risk:
            risk_types.append(RiskType.PII)
            risk_types.append(RiskType.PRIVACY)
            score = max(score, 0.7)
        elif pii.count > 0:
            risk_types.append(RiskType.PII)
            score = max(score, 0.4)

        # 2. Prompt injection ----------------------------------------------
        inj = scan_prompt_injection(output)
        metadata["prompt_injection_score"] = inj.raw_score
        if inj.is_suspicious:
            risk_types.append(RiskType.PROMPT_INJECTION)
            score = max(score, 0.75)

        # 3. Content safety ------------------------------------------------
        safety = scan_content_safety(output)
        metadata["content_safety_score"] = safety.raw_score
        if safety.raw_score > 0:
            for f in safety.findings:
                if f.category == "financial":
                    risk_types.append(RiskType.FINANCIAL_ADVICE)
                elif f.category == "medical":
                    risk_types.append(RiskType.MEDICAL_ADVICE)
                elif f.category == "political":
                    risk_types.append(RiskType.POLITICAL_RISK)
                elif f.category == "illegal":
                    risk_types.append(RiskType.ILLEGAL_CONTENT)
                elif f.category == "defamation":
                    risk_types.append(RiskType.DEFAMATION)
            # Per 下一阶段 #30 — financial is BLOCK territory.
            score = max(score, safety.raw_score)

        # 4. Copyright -----------------------------------------------------
        cop = scan_copyright(output, source)
        metadata["copyright_score"] = cop.raw_score
        metadata["copyright_blocks"] = len(cop.copy_blocks)
        if cop.copy_blocks:
            risk_types.append(RiskType.COPYRIGHT)
            score = max(score, cop.raw_score)

        level = risk_level_for_score(score)
        requires_review = level in {RiskLevel.MEDIUM, RiskLevel.HIGH}
        allowed = level in {RiskLevel.LOW, RiskLevel.MEDIUM}
        reason = self._build_reason(level, risk_types, score, metadata)

        return ComplianceResult(
            allowed=allowed,
            risk_score=score,
            risk_level=level,
            risk_types=merge_risk_types(risk_types),
            reason=reason,
            requires_human_review=requires_review,
            metadata=metadata,
        )

    def _aggregate_input(self, text: str) -> ComplianceResult:
        """Light aggregation for *input* text — PII + injection only."""
        risk_types: list[RiskType] = []
        score = 0.0
        metadata: dict[str, Any] = {}

        pii = scan_pii(text)
        metadata["pii_count"] = pii.count
        if pii.has_high_risk:
            risk_types.extend([RiskType.PII, RiskType.PRIVACY])
            score = max(score, 0.7)
        elif pii.count > 0:
            risk_types.append(RiskType.PII)
            score = max(score, 0.4)

        inj = scan_prompt_injection(text)
        metadata["prompt_injection_score"] = inj.raw_score
        if inj.is_suspicious:
            risk_types.append(RiskType.PROMPT_INJECTION)
            score = max(score, 0.75)

        level = risk_level_for_score(score)
        allowed = level in {RiskLevel.LOW, RiskLevel.MEDIUM}
        requires_review = level in {RiskLevel.MEDIUM, RiskLevel.HIGH}
        reason = self._build_reason(level, risk_types, score, metadata)

        return ComplianceResult(
            allowed=allowed,
            risk_score=score,
            risk_level=level,
            risk_types=merge_risk_types(risk_types),
            reason=reason,
            requires_human_review=requires_review,
            metadata=metadata,
        )

    def _from_source_finding(self, finding: SourcePolicyFinding) -> ComplianceResult:
        """Map a SourcePolicyFinding into the unified result shape."""
        risk_types: list[RiskType] = []
        if not finding.allowed:
            risk_types.append(RiskType.SOURCE_POLICY)
        level = risk_level_for_score(finding.risk_score)
        return ComplianceResult(
            allowed=finding.allowed,
            risk_score=finding.risk_score,
            risk_level=level,
            risk_types=merge_risk_types(risk_types),
            reason=finding.reason,
            requires_human_review=finding.requires_human_review,
            metadata={"source_finding": finding.policy_metadata},
        )

    @staticmethod
    def _build_reason(
        level: RiskLevel,
        risk_types: list[RiskType],
        score: float,
        metadata: dict[str, Any],
    ) -> str:
        if level == RiskLevel.LOW:
            return "low_risk_pass"
        if not risk_types:
            return f"{level.value}_score={score:.2f}"
        # First risk type names the headline reason; others are listed
        # in metadata for the admin console.
        head = risk_types[0].value
        more = ",".join(rt.value for rt in risk_types[1:])
        suffix = f"+{more}" if more else ""
        return f"{level.value}:{head}{suffix}"

    def _notify(self, result: ComplianceResult, context: str) -> None:
        for cb in self.on_decision:
            try:
                cb(result, context)
            except Exception:  # pragma: no cover — callback isolation
                # Callback failures must NEVER influence the verdict.
                # The audit / metrics callbacks live in Phase 12E; this
                # catch-all prevents them from taking down the request.
                pass


# ---------------------------------------------------------------------------
# Module-level singleton — equivalent to ``get_settings()`` patterns
# elsewhere in the codebase.
# ---------------------------------------------------------------------------
_default_service: ComplianceService | None = None


def default_service() -> ComplianceService:
    """Return the process-wide ComplianceService singleton."""
    global _default_service
    if _default_service is None:
        _default_service = ComplianceService()
    return _default_service


def reset_default_service() -> ComplianceService:
    """Reset the singleton (used by tests that need a fresh callback list)."""
    global _default_service
    _default_service = ComplianceService()
    return _default_service


__all__ = [
    "ComplianceService",
    "DecisionCallback",
    "default_service",
    "reset_default_service",
]