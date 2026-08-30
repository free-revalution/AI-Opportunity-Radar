"""Compliance pre-send gate — unified chokepoint for outbound delivery.

Phase 24 wires every outbound channel (Feishu IM, Telegram digest, multi-
channel content writes) through one helper that:

1. Runs ``ComplianceService.check_content`` over the text we'd send.
2. Persists a single ``AuditLog`` row tagged with the verdict's risk_level,
   risk_types, and reason (skipping LOW pass-throughs).
3. Raises ``ComplianceBlockedError`` when the verdict is HIGH/BLOCKED so
   callers can short-circuit before any HTTP request is made.

The helper is intentionally small. Phase 12E already implements the heavy
lifting (PII / prompt-injection / content-safety / copyright detectors +
risk-level scoring); this module is just glue.

Usage::

    from app.services.compliance.gate import gate_outbound, ComplianceBlockedError

    verdict = await gate_outbound(
        text=card_text,
        channel="feishu",
        resource_type="feishu_message",
        resource_id=str(message_id),
        session=session,
        context="activation_code_issue",
    )
    if not verdict.allowed:
        raise ComplianceBlockedError(verdict=verdict)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .service import default_service


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------
class ComplianceBlockedError(Exception):
    """Raised by ``gate_outbound`` (and re-raised by callers) when the
    verdict's risk_level is HIGH or BLOCKED.

    Carries the full ``ComplianceResult`` so callers can decide whether to
    surface the reason to the user / write their own audit row / send a
    fallback reply.
    """

    def __init__(self, verdict: Any, *, channel: str | None = None) -> None:
        self.verdict = verdict
        self.channel = channel
        level = getattr(verdict, "risk_level", None)
        level_value = getattr(level, "value", str(level))
        reason = getattr(verdict, "reason", "")
        type_values: list[str] = [
            rt.value for rt in getattr(verdict, "risk_types", [])
        ]
        type_part = f" types=[{','.join(type_values)}]" if type_values else ""
        super().__init__(
            f"compliance_blocked[{level_value}]: {reason}{type_part}"
        )


# ---------------------------------------------------------------------------
# Result envelope (so callers can distinguish "verdict" vs "audit row" cleanly)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class GateOutcome:
    """Returned by ``gate_outbound`` so the caller can inspect both the
    compliance verdict and whether (and which) audit row was written."""

    verdict: Any
    audit_entry: Any  # AuditEntry | None — None means clean LOW pass-through


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------
async def gate_outbound(
    text: str,
    *,
    channel: str,
    resource_type: str,
    resource_id: str,
    session: Any | None = None,
    context: str = "outbound",
    actor_id: str | None = None,
) -> GateOutcome:
    """Run ``ComplianceService.check_content`` + persist an AuditLog row.

    When ``session`` is ``None`` the audit write is skipped (useful in
    unit tests that exercise the gate without a DB). Returns a
    ``GateOutcome`` whose ``verdict`` is always the live ``ComplianceResult``;
    callers can inspect ``verdict.allowed`` / ``verdict.risk_level`` to
    decide what to do next.

    Does NOT raise on blocked verdicts — callers decide their own policy.
    The companion ``enforce_gate_outbound`` raises ``ComplianceBlockedError``
    for the HIGH/BLOCKED levels, which is what the Feishu/Notification
    chokepoints use.
    """
    verdict = default_service().check_content(text, context=context)

    actor = actor_id or f"compliance_gate:{channel}"
    audit_entry: Any = None
    if session is not None:
        # Local import avoids pulling the audit service into the gate module
        # at import time (keeps the cycle graph smaller).
        from app.services.audit import default_service as audit_default_service

        audit_entry = await audit_default_service().record_compliance_decision(
            session,
            verdict,
            actor_id=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            context=context,
        )

    return GateOutcome(verdict=verdict, audit_entry=audit_entry)


async def enforce_gate_outbound(
    text: str,
    *,
    channel: str,
    resource_type: str,
    resource_id: str,
    session: Any | None = None,
    context: str = "outbound",
    actor_id: str | None = None,
) -> GateOutcome:
    """Variant of ``gate_outbound`` that raises ``ComplianceBlockedError``
    on HIGH/BLOCKED verdicts. LOW/MEDIUM pass-through (audit row still
    written when ``session`` is provided).
    """
    outcome = await gate_outbound(
        text,
        channel=channel,
        resource_type=resource_type,
        resource_id=resource_id,
        session=session,
        context=context,
        actor_id=actor_id,
    )
    risk_level = getattr(outcome.verdict, "risk_level", None)
    level_value = getattr(risk_level, "value", None)
    if level_value in {"high", "blocked"}:
        raise ComplianceBlockedError(verdict=outcome.verdict, channel=channel)
    return outcome


__all__ = [
    "ComplianceBlockedError",
    "GateOutcome",
    "enforce_gate_outbound",
    "gate_outbound",
]