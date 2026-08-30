"""Audit service — append-only trail for publish / research / compliance actions.

Per docs/下一阶段开发技术方案.md §65-67:

> AuditLog { actor_type, actor_id, action, resource_type, resource_id,
>            result, metadata, created_at }
>
> publish / research / content_generate / source_enable 必须记录

Public surface:

    AuditService.record(actor_type, actor_id, action, ...)
        — append a single audit row. Pure-data shape; persistence
          is the caller's job (or wired in Phase 12G via a session
          adapter).

The service carries an in-memory ring buffer (last N entries) so admin
UIs and tests can read recent activity without hitting the DB. The
buffer is *not* the source of truth — it's a cache for the admin view.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class ActorType(str, Enum):
    ADMIN = "admin"
    SYSTEM = "system"
    USER = "user"
    BOT = "bot"


class AuditResult(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    PARTIAL = "partial"


class AuditAction(str, Enum):
    PUBLISH = "publish"
    REJECT = "reject"
    RESEARCH = "research"
    CONTENT_GENERATE = "content_generate"
    SOURCE_ENABLE = "source_enable"
    SOURCE_DISABLE = "source_disable"
    REFRESH = "refresh"
    SCORE = "score"
    ACTIVATE = "activate"
    RBAC_DENY = "rbac_deny"
    COMPLIANCE_BLOCK = "compliance_block"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class AuditEntry:
    actor_type: str
    action: str
    actor_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    result: str = AuditResult.SUCCESS.value
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "result": self.result,
            "metadata": self.metadata or {},
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Audit service
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class AuditService:
    """Append-only audit logger with a small ring buffer.

    The buffer is consulted by admin views to render "recent activity"
    without a DB roundtrip. ``max_buffer`` defaults to 1000 entries.
    """

    max_buffer: int = 1000
    _buffer: deque[AuditEntry] = field(default_factory=deque)

    def record(
        self,
        actor_type: str,
        action: str,
        *,
        actor_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        result: str = AuditResult.SUCCESS.value,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            actor_type=actor_type,
            action=action,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata=metadata or {},
        )
        self._buffer.append(entry)
        if len(self._buffer) > self.max_buffer:
            self._buffer.popleft()
        return entry

    def record_publish(
        self,
        actor_id: str,
        *,
        notification_id: int,
        channel: str,
        success: bool,
        external_id: str | None = None,
        error: str | None = None,
    ) -> AuditEntry:
        return self.record(
            actor_type=ActorType.SYSTEM.value,
            action=AuditAction.PUBLISH.value,
            actor_id=actor_id,
            resource_type="notification",
            resource_id=str(notification_id),
            result=AuditResult.SUCCESS.value if success else AuditResult.FAILURE.value,
            metadata={
                "channel": channel,
                "external_id": external_id,
                "error": error,
            },
        )

    def record_rbac_deny(
        self,
        actor_id: str,
        *,
        command: str,
        required_role: str,
        actor_role: str | None = None,
    ) -> AuditEntry:
        return self.record(
            actor_type=ActorType.BOT.value,
            action=AuditAction.RBAC_DENY.value,
            actor_id=actor_id,
            resource_type="command",
            resource_id=command,
            result=AuditResult.BLOCKED.value,
            metadata={"required_role": required_role, "actor_role": actor_role},
        )

    def record_compliance_block(
        self,
        actor_id: str,
        *,
        resource_type: str,
        resource_id: str,
        reason: str,
        risk_score: float,
    ) -> AuditEntry:
        return self.record(
            actor_type=ActorType.SYSTEM.value,
            action=AuditAction.COMPLIANCE_BLOCK.value,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            result=AuditResult.BLOCKED.value,
            metadata={"reason": reason, "risk_score": risk_score},
        )

    # ----- Phase 24: Compliance Service on_decision hook ------------------
    async def record_compliance_decision(
        self,
        session: Any,
        verdict: Any,  # duck-typed ComplianceResult
        *,
        actor_id: str = "compliance_gate",
        resource_type: str,
        resource_id: str,
        context: str | None = None,
    ) -> "AuditEntry | None":
        """Persist one compliance decision as an AuditLog row.

        Maps the verdict's risk_level to ``AuditResult``:
          LOW      → ``success``
          MEDIUM   → ``partial``
          HIGH     → ``failure``
          BLOCKED  → ``blocked``

        Skips LOW verdicts that flagged no risk_types — a clean pass-through
        is not interesting to the audit trail. Everything else (any
        non-empty risk_types OR MEDIUM+) writes one row.

        Returns the persisted AuditEntry, or ``None`` if the verdict was a
        clean pass-through (nothing to audit).
        """
        # Duck-typed — avoid importing app.services.compliance.models so
        # this module stays free of cross-service imports.
        level_value: str = verdict.risk_level.value
        type_values: list[str] = [rt.value for rt in verdict.risk_types]
        requires_review: bool = bool(verdict.requires_human_review)

        # Clean pass-throughs skip the row to avoid noise.
        if level_value == "low" and not type_values and not requires_review:
            return None

        level_to_result = {
            "low": AuditResult.SUCCESS,
            "medium": AuditResult.PARTIAL,
            "high": AuditResult.FAILURE,
            "blocked": AuditResult.BLOCKED,
        }
        result_value = level_to_result[level_value].value

        metadata: dict[str, Any] = {
            "risk_level": level_value,
            "risk_types": type_values,
            "risk_score": float(verdict.risk_score),
            "reason": verdict.reason,
            "requires_human_review": requires_review,
        }
        if context is not None:
            metadata["context"] = context
        # Detectors can stash JSON-safe scalars in metadata — surface them.
        verdict_metadata = getattr(verdict, "metadata", None)
        if isinstance(verdict_metadata, dict) and verdict_metadata:
            metadata["detectors"] = verdict_metadata

        return await self.record_db(
            session,
            actor_type=ActorType.SYSTEM.value,
            action=AuditAction.COMPLIANCE_BLOCK.value,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result_value,
            metadata=metadata,
            commit=True,
        )

    def recent(self, n: int = 50, *, action: str | None = None) -> list[AuditEntry]:
        """Return up to ``n`` most-recent entries (optionally filtered)."""
        out: list[AuditEntry] = []
        for entry in reversed(self._buffer):
            if action and entry.action != action:
                continue
            out.append(entry)
            if len(out) >= n:
                break
        return out

    def count(self, *, action: str | None = None) -> int:
        if not action:
            return len(self._buffer)
        return sum(1 for e in self._buffer if e.action == action)

    def clear(self) -> None:
        self._buffer.clear()

    # ----- DB persistence --------------------------------------------------
    async def record_db(
        self,
        session: Any,
        actor_type: str,
        action: str,
        *,
        actor_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        result: str = AuditResult.SUCCESS.value,
        metadata: Optional[dict[str, Any]] = None,
        commit: bool = True,
    ) -> "AuditEntry":
        """Persist one row + append to the in-memory buffer.

        Returns the entry that was written. ``commit=False`` lets the
        caller fold the audit row into a larger transaction (e.g.
        revoking an activation code and writing the audit row together).
        """
        from app.models import AuditLog  # local import to avoid cycle

        row = AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata_json=metadata or {},
        )
        session.add(row)
        if commit:
            await session.commit()
            await session.refresh(row)
        entry = AuditEntry(
            actor_type=actor_type,
            action=action,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata=metadata,
            created_at=getattr(row, "created_at", None) or datetime.now(tz=timezone.utc),
        )
        self._buffer.append(entry)
        if len(self._buffer) > self.max_buffer:
            self._buffer.popleft()
        return entry


# ---------------------------------------------------------------------------
# Module-level singleton — mirrors the ComplianceService pattern.
# ---------------------------------------------------------------------------
_default_service: AuditService | None = None


def default_service() -> AuditService:
    global _default_service
    if _default_service is None:
        _default_service = AuditService()
    return _default_service


def reset_default_service() -> AuditService:
    """Reset the singleton (used by tests that need a fresh buffer)."""
    global _default_service
    _default_service = AuditService()
    return _default_service


# ---------------------------------------------------------------------------
# Convenience — module-level helper used by API layers.
# ---------------------------------------------------------------------------
def record_audit(
    actor_type: str,
    action: str,
    *,
    actor_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    result: str = AuditResult.SUCCESS.value,
    metadata_json: Optional[dict[str, Any]] = None,
) -> AuditEntry:
    """Synchronous in-memory record. For DB persistence use
    ``AuditService.record_db(session, ...)`` from inside an endpoint."""
    return default_service().record(
        actor_type=actor_type,
        action=action,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        metadata=metadata_json,
    )


__all__ = [
    "ActorType",
    "AuditAction",
    "AuditEntry",
    "AuditResult",
    "AuditService",
    "default_service",
    "record_audit",
    "reset_default_service",
]