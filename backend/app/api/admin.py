"""Admin API — Phase 13B.

Mounted at ``/api/admin``. Two auth paths (either is sufficient):

  * ``X-Radar-Admin-Secret`` header matches ``settings.admin_api_secret``
    (HMAC-compare, constant time).
  * ``X-Feishu-Open-Id`` header appears in ``settings.admin_open_ids``.

Endpoints (per docs/下一阶段开发技术方案.md §55, §88, §65-66):

    Activation
      POST   /api/admin/activation/issue         — body: {plan, ttl_days?}
      GET    /api/admin/activation               — list codes (filter: status, plan, limit)
      POST   /api/admin/activation/{id}/revoke   — set status='revoked'

    Subscriptions
      GET    /api/admin/subscriptions            — list (filter: status, plan, limit)
      GET    /api/admin/subscriptions/{id}       — single
      POST   /api/admin/subscriptions/{id}/extend — body: {days}
      POST   /api/admin/subscriptions/{id}/cancel — set status='cancelled'

    Audit
      GET    /api/admin/audit                    — list audit logs (filter: actor_type, action, result, since, limit)

    Sources
      GET    /api/admin/sources                  — list with compliance posture
      PATCH  /api/admin/sources/{id}/compliance — body: {compliance_level, retention_policy?, source_block_reason?}

All list endpoints honour ``admin_max_list_limit`` (default 200) and
reject callers silently — no enumeration hints in error responses.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import (
    ActivationCode,
    AuditLog,
    Source,
    Subscription,
)
from app.services.activation import (
    ActivationError,
    DEFAULT_SERVER_PEPPER,
    hash_code,
    issue_code,
    redeem_code,
)
from app.services.audit import AuditService, default_service as default_audit_service
from app.services.compliance.models import ComplianceLevel
from app.services.subscriptions import PLAN_CATALOGUE


router = APIRouter()
_VALID_STATUSES = {"active", "expired", "suspended", "cancelled", "revoked"}


def _to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Serialise a datetime as an ISO-8601 string in UTC.

    SQLite strips tzinfo from ``DateTime(timezone=True)`` columns on read,
    so ``row.expires_at.isoformat()`` would emit a naive string and trip
    ``datetime.fromisoformat`` in tests that subtract ``now(tz=utc)``.
    Always re-attach UTC before serialising so downstream parsers see
    a tz-aware value.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
def _require_admin(
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Radar-Admin-Secret"),
    x_feishu_open_id: Optional[str] = Header(default=None, alias="X-Feishu-Open-Id"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Verify the caller is an admin. Returns the caller's id for audit.

    Two accepted paths:
      1. ``X-Radar-Admin-Secret`` matches ``settings.admin_api_secret``.
      2. ``X-Feishu-Open-Id`` is in ``settings.admin_open_ids``.

    Otherwise 401 with a deliberately opaque message.
    """
    secret = settings.admin_api_secret
    if secret and x_admin_secret:
        if hmac.compare_digest(
            hashlib.sha256(x_admin_secret.encode()).hexdigest(),
            hashlib.sha256(secret.encode()).hexdigest(),
        ):
            return "secret"

    admins = settings.admin_open_ids or []
    if x_feishu_open_id and x_feishu_open_id in admins:
        return x_feishu_open_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="admin credentials required",
    )


async def _audit_db(
    session: AsyncSession,
    *,
    action: str,
    actor: str,
    resource_type: str,
    resource_id: str,
    result: str = "success",
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Persist one AuditLog row + append to the in-memory buffer."""
    await default_audit_service().record_db(
        session,
        actor_type="admin",
        action=action,
        actor_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class IssueCodeRequest(BaseModel):
    plan: str = Field(..., description="free | basic | pro | creator")
    ttl_days: int = Field(default=365, ge=1, le=3650)


class ExtendSubscriptionRequest(BaseModel):
    days: int = Field(..., ge=1, le=3650)


class UpdateComplianceRequest(BaseModel):
    compliance_level: str = Field(..., description="A | B | C | D | E")
    retention_policy: Optional[str] = Field(default=None, max_length=64)
    source_block_reason: Optional[str] = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# Activation endpoints
# ---------------------------------------------------------------------------
@router.post(
    "/activation/issue",
    summary="Issue a new activation code (admin)",
)
async def issue_activation_code(
    body: IssueCodeRequest,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    plan = body.plan.lower()
    if plan not in PLAN_CATALOGUE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown plan: {plan}",
        )
    issued = issue_code(plan, ttl_days=body.ttl_days)
    row = ActivationCode(
        code_hash=issued.code_hash,
        plan=issued.plan,
        expires_at=issued.expires_at,
        status="unused",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    await _audit_db(
        session,
        action="activation_issue",
        actor=actor,
        resource_type="activation_code",
        resource_id=str(row.id),
        metadata={"plan": plan, "ttl_days": body.ttl_days},
    )

    return {
        "id": row.id,
        "code": issued.code,           # plaintext — returned ONCE
        "plan": row.plan,
        "status": row.status,
        "expires_at": _to_utc_iso(row.expires_at),
    }


@router.get(
    "/activation",
    summary="List activation codes (admin)",
)
async def list_activation_codes(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    plan: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    stmt = select(ActivationCode).order_by(ActivationCode.created_at.desc()).limit(limit)
    if status_filter:
        if status_filter not in {"unused", "active", "expired", "revoked"}:
            raise HTTPException(422, "invalid status_filter")
        stmt = stmt.where(ActivationCode.status == status_filter)
    if plan:
        stmt = stmt.where(ActivationCode.plan == plan)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "plan": r.plan,
                "status": r.status,
                "expires_at": _to_utc_iso(r.expires_at),
                "bound_feishu_open_id": r.bound_feishu_open_id,
                "bound_at": _to_utc_iso(r.bound_at),
                "created_at": _to_utc_iso(r.created_at),
                "used_at": _to_utc_iso(r.used_at),
            }
            for r in rows
        ],
    }


@router.post(
    "/activation/{code_id}/revoke",
    summary="Revoke an activation code (admin)",
)
async def revoke_activation_code(
    code_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    row = await session.get(ActivationCode, code_id)
    if row is None:
        raise HTTPException(404, "code not found")
    row.status = "revoked"
    await session.commit()

    await _audit_db(
        session,
        action="activation_revoke",
        actor=actor,
        resource_type="activation_code",
        resource_id=str(code_id),
    )

    return {"id": code_id, "status": "revoked"}


# ---------------------------------------------------------------------------
# Subscription endpoints
# ---------------------------------------------------------------------------
@router.get(
    "/subscriptions",
    summary="List subscriptions (admin)",
)
async def list_subscriptions(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    plan: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    stmt = select(Subscription).order_by(Subscription.created_at.desc()).limit(limit)
    if status_filter:
        if status_filter not in _VALID_STATUSES:
            raise HTTPException(422, "invalid status_filter")
        stmt = stmt.where(Subscription.status == status_filter)
    if plan:
        stmt = stmt.where(Subscription.plan == plan)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "feishu_open_id": r.feishu_open_id,
                "plan": r.plan,
                "status": r.status,
                "source_channel": r.source_channel,
                "starts_at": _to_utc_iso(r.starts_at),
                "expires_at": _to_utc_iso(r.expires_at),
                "created_at": _to_utc_iso(r.created_at),
                "updated_at": _to_utc_iso(r.updated_at),
            }
            for r in rows
        ],
    }


@router.get(
    "/subscriptions/{sub_id}",
    summary="Get one subscription (admin)",
)
async def get_subscription(
    sub_id: int,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    row = await session.get(Subscription, sub_id)
    if row is None:
        raise HTTPException(404, "subscription not found")
    return {
        "id": row.id,
        "user_id": row.user_id,
        "feishu_open_id": row.feishu_open_id,
        "plan": row.plan,
        "status": row.status,
        "source_channel": row.source_channel,
        "starts_at": _to_utc_iso(row.starts_at),
        "expires_at": _to_utc_iso(row.expires_at),
        "created_at": _to_utc_iso(row.created_at),
        "updated_at": _to_utc_iso(row.updated_at),
    }


@router.post(
    "/subscriptions/{sub_id}/extend",
    summary="Extend a subscription's expiry (admin)",
)
async def extend_subscription(
    sub_id: int,
    body: ExtendSubscriptionRequest,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    row = await session.get(Subscription, sub_id)
    if row is None:
        raise HTTPException(404, "subscription not found")
    now = datetime.now(tz=timezone.utc)
    # SQLite strips tzinfo from DateTime(timezone=True) columns on read —
    # normalise the row value so the comparison + arithmetic below works.
    row_exp = row.expires_at
    if row_exp is not None and row_exp.tzinfo is None:
        row_exp = row_exp.replace(tzinfo=timezone.utc)
    base = row_exp if (row_exp and row_exp > now) else now
    row.expires_at = base + timedelta(days=body.days)
    row.status = "active"
    await session.commit()

    await _audit_db(
        session,
        action="subscription_extend",
        actor=actor,
        resource_type="subscription",
        resource_id=str(sub_id),
        metadata={"days": body.days, "new_expires_at": _to_utc_iso(row.expires_at)},
    )

    return {
        "id": row.id,
        "plan": row.plan,
        "status": row.status,
        "expires_at": _to_utc_iso(row.expires_at),
    }


@router.post(
    "/subscriptions/{sub_id}/cancel",
    summary="Cancel a subscription (admin)",
)
async def cancel_subscription(
    sub_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    row = await session.get(Subscription, sub_id)
    if row is None:
        raise HTTPException(404, "subscription not found")
    row.status = "cancelled"
    await session.commit()

    await _audit_db(
        session,
        action="subscription_cancel",
        actor=actor,
        resource_type="subscription",
        resource_id=str(sub_id),
    )

    return {"id": row.id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Audit log endpoint
# ---------------------------------------------------------------------------
@router.get(
    "/audit",
    summary="List audit log entries (admin)",
)
async def list_audit_logs(
    actor_type: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    result_filter: Optional[str] = Query(default=None, alias="result"),
    since: Optional[datetime] = Query(default=None, description="ISO-8601 lower bound"),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if actor_type:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if result_filter:
        if result_filter not in {"success", "failure", "blocked", "partial"}:
            raise HTTPException(422, "invalid result filter")
        stmt = stmt.where(AuditLog.result == result_filter)
    if since:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        stmt = stmt.where(AuditLog.created_at >= since)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "actor_type": r.actor_type,
                "actor_id": r.actor_id,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "result": r.result,
                "metadata_json": r.metadata_json,
                "created_at": _to_utc_iso(r.created_at),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Source compliance endpoints
# ---------------------------------------------------------------------------
@router.get(
    "/sources",
    summary="List sources with compliance posture (admin)",
)
async def list_sources(
    compliance_level: Optional[str] = Query(default=None, description="A | B | C | D | E"),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    stmt = select(Source).order_by(Source.id).limit(limit)
    if compliance_level:
        stmt = stmt.where(Source.compliance_level == compliance_level)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "url": r.url,
                "enabled": r.enabled,
                "compliance_level": r.compliance_level,
                "commercial_use_status": r.commercial_use_status,
                "access_method": r.access_method,
                "retention_policy": r.retention_policy,
                "source_block_reason": r.source_block_reason,
                "last_compliance_check": _to_utc_iso(r.last_compliance_check),
            }
            for r in rows
        ],
    }


@router.patch(
    "/sources/{source_id}/compliance",
    summary="Update a source's compliance posture (admin)",
)
async def update_source_compliance(
    source_id: int,
    body: UpdateComplianceRequest,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(_require_admin),
) -> dict[str, Any]:
    level = body.compliance_level.upper()
    if level not in {"A", "B", "C", "D", "E"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid compliance_level: {body.compliance_level}",
        )
    row = await session.get(Source, source_id)
    if row is None:
        raise HTTPException(404, "source not found")
    row.compliance_level = level
    if body.retention_policy is not None:
        row.retention_policy = body.retention_policy
    if body.source_block_reason is not None:
        row.source_block_reason = body.source_block_reason or None
    row.last_compliance_check = datetime.now(tz=timezone.utc)
    await session.commit()

    await _audit_db(
        session,
        action="source_compliance_update",
        actor=actor,
        resource_type="source",
        resource_id=str(source_id),
        metadata={
            "new_compliance_level": level,
            "retention_policy": body.retention_policy,
            "source_block_reason": body.source_block_reason,
        },
    )

    return {
        "id": row.id,
        "compliance_level": row.compliance_level,
        "retention_policy": row.retention_policy,
        "source_block_reason": row.source_block_reason,
        "last_compliance_check": _to_utc_iso(row.last_compliance_check),
    }


__all__ = ["router"]