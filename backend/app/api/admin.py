"""Admin API — Phase 13B.

Mounted at ``/api/admin``. Phase 21 unified the auth paths into a single
``require_admin`` dependency (see ``app/api/deps.py``). Any one of these
headers is accepted:

  * ``X-Radar-Webhook`` — internal operator UI + n8n workflows.
  * ``X-Radar-Admin-Secret`` — manual/CLI admin operations.
  * ``X-Feishu-Open-Id`` ∈ ``settings.admin_open_ids`` — Feishu bot
    (forward-compat hook; no live callers today).

Endpoints (per docs/下一阶段开发技术方案.md §55, §88, §65-66):

    Activation
      POST   /api/admin/activation/issue         — body: {plan, ttl_days?}
      GET    /api/admin/activation               — list codes (filter: status, plan, limit)
      POST   /api/admin/activation/{id}/revoke   — set status='revoked'
      POST   /api/admin/activation/{id}/resend  — Phase 23 IM resend hint

    Subscriptions
      GET    /api/admin/subscriptions            — list (filter: status, plan, limit)
      GET    /api/admin/subscriptions/{id}       — single
      POST   /api/admin/subscriptions/{id}/extend — body: {days}
      POST   /api/admin/subscriptions/{id}/cancel — set status='cancelled'

    Audit
      GET    /api/admin/audit_logs               — paginated viewer (Phase 20)

    Sources
      GET    /api/admin/sources                  — list with compliance posture
      PATCH  /api/admin/sources/{id}/compliance — body: {compliance_level, retention_policy?, source_block_reason?}

    Messages (Phase 23)
      GET    /api/admin/notifications            — paginated Notification history
                                                 — filter: kind, channel, since

All list endpoints honour ``admin_max_list_limit`` (default 200) and
reject callers silently — no enumeration hints in error responses.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import get_settings
from app.db import get_session
from app.models import (
    ActivationCode,
    AuditLog,
    ContentOpportunity,
    Notification,
    Signal,
    Source,
    Subscription,
)
from app.repositories import ContentOpportunityRepository
from app.repositories.content_opportunities import IllegalStatusTransition
from app.schemas import ContentOpportunityRejectRequest
from app.services.activation import (
    ActivationError,
    DEFAULT_SERVER_PEPPER,
    hash_code,
    issue_code,
    redeem_code,
)
from app.services.audit import AuditService, default_service as default_audit_service
from app.services.compliance.models import ComplianceLevel
from app.services.feishu.app_client import FeishuAppClient, FeishuAppError
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
# Auth dependency — Phase 21 unified into app/api/deps.py::require_admin
# ---------------------------------------------------------------------------


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
# Phase 23 — Feishu IM helpers (activation-code delivery + renewal
# reminders). Both write a Notification row + AuditLog row regardless of
# success/failure so the operator has a single source of truth for "who
# got what message when".
# ---------------------------------------------------------------------------
def _activation_code_card(
    *,
    plan: str,
    code: str,
    expires_at: Optional[datetime],
) -> dict[str, Any]:
    """Build the interactive card body for a freshly issued activation
    code. Mirrors the Phase 6 `_feishu_card(reply)` shape so the
    downstream `send_message` call is symmetric with the chat-reply path.
    """
    expires_iso = (
        expires_at.astimezone(timezone.utc).isoformat() if expires_at else "—"
    )
    text = (
        f"**您的 AI 机会雷达 {plan} 订阅激活码**\n\n"
        f"激活码：`{code}`\n\n"
        f"有效期至：{expires_iso}\n\n"
        "请复制本激活码，然后向 AI 机会雷达 飞书机器人发送 `/activate "
        f"{code}` 完成绑定。"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": text},
                }
            ],
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"激活码 · {plan}",
                },
                "template": "blue",
            },
        },
    }


def _renewal_reminder_card(
    *,
    plan: str,
    expires_at: datetime,
    days_until: int,
    sub_id: int,
) -> dict[str, Any]:
    """Build the interactive card body for a renewal reminder."""
    expires_iso = expires_at.astimezone(timezone.utc).isoformat()
    text = (
        f"**您的 AI 机会雷达 {plan} 订阅即将到期**\n\n"
        f"剩余 {days_until} 天后到期（{expires_iso}）。\n\n"
        f"续订请回复 `/renew sub={sub_id}` 或联系管理员。"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": text},
                }
            ],
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"续期提醒 · {plan}",
                },
                "template": "orange",
            },
        },
    }


async def _write_notification(
    session: AsyncSession,
    *,
    channel: str,
    payload: dict[str, Any],
    delivered_at: Optional[datetime] = None,
    error: Optional[str] = None,
) -> Notification:
    """Persist a Notification audit row. Pure INSERT — `payload` is free
    form JSON; ``delivered_at`` is set on success, ``error`` on failure.
    Caller is responsible for committing the session."""
    row = Notification(
        channel=channel,
        payload=payload,
        delivered_at=delivered_at,
        error=error,
    )
    session.add(row)
    await session.flush()
    return row


async def _send_activation_code_im(
    session: AsyncSession,
    *,
    code_id: int,
    code: str,
    plan: str,
    expires_at: Optional[datetime],
    open_id: str,
    actor: str,
) -> dict[str, Any]:
    """Phase 23 — auto-IM a freshly issued activation code.

    Always writes a Notification row + AuditLog row (success OR failure)
    so the operator has a complete record under `/admin/messages`.

    Returns:
      ``{sent: bool, message_id?: str, error?: str}`` — surfaced in the
      admin issue endpoint's response under the ``im_send`` key so the
      UI banner can show the result.
    """
    card = _activation_code_card(plan=plan, code=code, expires_at=expires_at)
    base_payload: dict[str, Any] = {
        "kind": "activation_code_issued",
        "activation_code_id": code_id,
        "plan": plan,
        "open_id": open_id,
    }

    now = datetime.now(tz=timezone.utc)
    sent = False
    message_id: Optional[str] = None
    error: Optional[str] = None
    try:
        settings = get_settings()
        client = FeishuAppClient(settings=settings)
        try:
            response = await client.send_message(
                receive_id=open_id,
                receive_id_type="open_id",
                msg_type=card["msg_type"],
                content=card["card"],
            )
            data = response.get("data") or {}
            message_id = data.get("message_id")
            sent = True
        finally:
            await client.aclose()
    except FeishuAppError as exc:
        error = str(exc)

    await _write_notification(
        session,
        channel="feishu",
        payload={
            **base_payload,
            "code_preview": f"{code[:4]}…{code[-4:]}",  # never store plaintext
            "message_id": message_id,
        },
        delivered_at=now if sent else None,
        error=error,
    )
    await _audit_db(
        session,
        action="activation_im_send",
        actor=actor,
        resource_type="activation_code",
        resource_id=str(code_id),
        result="success" if sent else "failure",
        metadata={
            "open_id": open_id,
            "message_id": message_id,
            "error": error,
        },
    )
    return {"sent": sent, "message_id": message_id, "error": error}


async def _send_renewal_reminder_im(
    session: AsyncSession,
    *,
    sub_id: int,
    plan: str,
    expires_at: datetime,
    open_id: str,
    actor: str,
    days_until: int,
) -> dict[str, Any]:
    """Phase 23 — IM a renewal reminder to a subscription's bound user.

    Same Notification + AuditLog pattern as ``_send_activation_code_im``,
    but the payload is keyed by ``kind=subscription_renewal_reminder``
    and ``subscription_id`` so the admin /messages page can deep-link
    back to the subscription list.
    """
    card = _renewal_reminder_card(
        plan=plan,
        expires_at=expires_at,
        days_until=days_until,
        sub_id=sub_id,
    )
    base_payload: dict[str, Any] = {
        "kind": "subscription_renewal_reminder",
        "subscription_id": sub_id,
        "plan": plan,
        "open_id": open_id,
        "days_until": days_until,
        "expires_at": _to_utc_iso(expires_at),
    }
    sent = False
    message_id: Optional[str] = None
    error: Optional[str] = None
    try:
        settings = get_settings()
        client = FeishuAppClient(settings=settings)
        try:
            response = await client.send_message(
                receive_id=open_id,
                receive_id_type="open_id",
                msg_type=card["msg_type"],
                content=card["card"],
            )
            data = response.get("data") or {}
            message_id = data.get("message_id")
            sent = True
        finally:
            await client.aclose()
    except FeishuAppError as exc:
        error = str(exc)

    now = datetime.now(tz=timezone.utc)
    await _write_notification(
        session,
        channel="feishu",
        payload={**base_payload, "message_id": message_id},
        delivered_at=now if sent else None,
        error=error,
    )
    await _audit_db(
        session,
        action="subscription_renewal_reminder",
        actor=actor,
        resource_type="subscription",
        resource_id=str(sub_id),
        result="success" if sent else "failure",
        metadata={
            "open_id": open_id,
            "days_until": days_until,
            "message_id": message_id,
            "error": error,
        },
    )
    return {"sent": sent, "message_id": message_id, "error": error}


def _notification_deep_link(payload: dict[str, Any]) -> Optional[str]:
    """Compute the in-app deep link for a Notification row.

    Mirrors the frontend's ``notificationDeepLink`` helper in
    ``frontend/src/lib/adminCrud.ts`` — keeping them in sync is the only
    way the `/admin/messages` view's "打开资源" button lands somewhere
    sensible.
    """
    kind = payload.get("kind")
    if kind == "activation_code_issued" or kind == "activation_code_resend":
        code_id = payload.get("activation_code_id")
        if code_id is not None:
            return f"/admin/activation?id={code_id}"
        return "/admin/activation"
    if kind == "subscription_renewal_reminder":
        sub_id = payload.get("subscription_id")
        if sub_id is not None:
            return f"/admin/subscriptions?id={sub_id}"
        return "/admin/subscriptions"
    return None


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class IssueCodeRequest(BaseModel):
    plan: str = Field(..., description="free | basic | pro | creator")
    ttl_days: int = Field(default=365, ge=1, le=3650)
    # Phase 23 — when supplied, the freshly issued plaintext is IM'd to
    # the customer's Feishu open_id via `POST /im/v1/messages` right
    # after the activation_codes row commits. The plaintext is still
    # returned in the response so the operator banner can show it.
    feishu_open_id: Optional[str] = Field(
        default=None,
        description="Phase 23 — destination Feishu open_id for auto-IM",
    )
    # Phase 23 — operator toggle. Default true. Set false to issue the
    # code without an outbound IM (e.g. when the operator hands the
    # code over by hand).
    send_im: bool = Field(default=True, description="Phase 23 — auto-IM toggle")


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
    actor: str = Depends(require_admin),
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

    response: dict[str, Any] = {
        "id": row.id,
        "code": issued.code,           # plaintext — returned ONCE
        "plan": row.plan,
        "status": row.status,
        "expires_at": _to_utc_iso(row.expires_at),
    }

    # Phase 23 — auto-IM the activation code to the operator-supplied
    # open_id. Disabled when:
    #   * `send_im=false` (operator wants to hand-deliver),
    #   * no `feishu_open_id` supplied,
    #   * the setting `send_activation_code_via_im=false` (kill switch
    #     for dev / dry-runs).
    # We still return `im_send=null` in those cases so the UI's response
    # shape is uniform.
    settings = get_settings()
    if body.send_im and body.feishu_open_id and settings.send_activation_code_via_im:
        im_status = await _send_activation_code_im(
            session,
            code_id=row.id,
            code=issued.code,
            plan=plan,
            expires_at=row.expires_at,
            open_id=body.feishu_open_id,
            actor=actor,
        )
        await session.commit()
        response["im_send"] = im_status
    else:
        response["im_send"] = None

    return response


@router.get(
    "/activation",
    summary="List activation codes (admin)",
)
async def list_activation_codes(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    plan: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
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
    actor: str = Depends(require_admin),
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
# Phase 23 — re-send a previously issued (unused) code's plaintext
# notification. The plaintext is gone from the DB so we can't reconstitute
# the original — the resend composes a fresh card that says "your
# activation code has been re-issued; please contact support for the
# plaintext". For real reuse, the operator should issue a NEW code.
# ---------------------------------------------------------------------------
class ResendActivationRequest(BaseModel):
    open_id: Optional[str] = Field(
        default=None,
        description="Phase 23 — Feishu open_id to send the reminder to",
    )


@router.post(
    "/activation/{code_id}/resend",
    summary="Resend a 'please contact us' reminder for an unused code",
)
async def resend_activation_code(
    code_id: int,
    body: ResendActivationRequest,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    row = await session.get(ActivationCode, code_id)
    if row is None:
        raise HTTPException(404, "code not found")
    if row.status not in {"unused", "active"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot resend — code status is {row.status}",
        )
    target = body.open_id or row.bound_feishu_open_id
    if not target:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`open_id` required (code has no bound_feishu_open_id)",
        )
    settings = get_settings()
    if not settings.send_activation_code_via_im:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="send_activation_code_via_im disabled by setting",
        )

    # The plaintext is gone — we can only hint the user to contact us.
    # In production this is paired with the operator pulling the code
    # out of the original banner before it 30s-expires; for a resend
    # the operator has to walk the customer through a new code.
    sent = False
    message_id: Optional[str] = None
    error: Optional[str] = None
    try:
        client = FeishuAppClient(settings=settings)
        try:
            card = {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": (
                                    f"您的 AI 机会雷达 {row.plan} 激活码 #"
                                    f"{row.id} 仍在等待绑定。请联系管理员索取"
                                    " 明文，或等待新激活码。\n\n"
                                    f"有效期至：{_to_utc_iso(row.expires_at) or '—'}"
                                ),
                            },
                        }
                    ],
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "激活码 · 待绑定",
                        },
                        "template": "orange",
                    },
                },
            }
            response = await client.send_message(
                receive_id=target,
                receive_id_type="open_id",
                msg_type=card["msg_type"],
                content=card["card"],
            )
            data = response.get("data") or {}
            message_id = data.get("message_id")
            sent = True
        finally:
            await client.aclose()
    except FeishuAppError as exc:
        error = str(exc)

    now = datetime.now(tz=timezone.utc)
    await _write_notification(
        session,
        channel="feishu",
        payload={
            "kind": "activation_code_resend",
            "activation_code_id": code_id,
            "plan": row.plan,
            "open_id": target,
            "message_id": message_id,
        },
        delivered_at=now if sent else None,
        error=error,
    )
    await _audit_db(
        session,
        action="activation_im_resend",
        actor=actor,
        resource_type="activation_code",
        resource_id=str(code_id),
        result="success" if sent else "failure",
        metadata={
            "open_id": target,
            "message_id": message_id,
            "error": error,
        },
    )
    await session.commit()

    return {
        "id": code_id,
        "sent": sent,
        "message_id": message_id,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Notification history (Phase 23) — powers /admin/messages
# ---------------------------------------------------------------------------
@router.get(
    "/notifications",
    summary="List notification delivery history (Phase 23)",
)
async def list_notifications(
    kind: Optional[str] = Query(
        default=None,
        description=(
            "Filter by payload.kind (cross-dialect JSON path) — "
            "e.g. 'activation_code_issued', 'subscription_renewal_reminder'."
        ),
    ),
    channel: Optional[str] = Query(
        default=None,
        description="Filter by Notification.channel (e.g. 'feishu').",
    ),
    since: Optional[datetime] = Query(
        default=None,
        description="Lower bound on created_at (ISO-8601).",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Paginated Notification history for the ``/admin/messages`` viewer.

    Each row is augmented with:
      * ``kind`` — extracted from ``payload.kind`` (cross-dialect JSON
        path; mirrors the cooldown check in
        ``send_subscription_renewal_reminders``).
      * ``deep_link`` — admin path that takes the operator back to the
        related resource (activation code, subscription, …).
      * ``failed`` — boolean convenience flag for the UI's red-chip row.

    Cross-dialect JSON extraction: PostgreSQL uses ``payload['kind'].astext``;
    SQLite uses ``func.json_extract(payload, '$.kind')``. Mirrors the
    pattern already used by ``_fetch_grouped_content`` for
    ``payload['opportunity_id']``.
    """
    base = select(Notification).order_by(
        Notification.created_at.desc(),
        Notification.id.desc(),
    )
    count_base = select(func.count()).select_from(Notification)

    bind = session.bind
    dialect_name = bind.dialect.name if bind is not None else "sqlite"

    if kind:
        if dialect_name == "postgresql":
            kind_filter = Notification.payload["kind"].astext == kind
        else:
            kind_filter = (
                func.json_extract(Notification.payload, "$.kind") == kind
            )
        base = base.where(kind_filter)
        count_base = count_base.where(kind_filter)

    if channel:
        ch_filter = Notification.channel == channel
        base = base.where(ch_filter)
        count_base = count_base.where(ch_filter)

    if since is not None:
        # SQLite strips tzinfo — coerce to UTC for a stable compare.
        since_dt = since
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
        since_filter = Notification.created_at >= since_dt
        base = base.where(since_filter)
        count_base = count_base.where(since_filter)

    stmt = base.limit(limit).offset(offset)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int((await session.execute(count_base)).scalar_one())

    items: list[dict[str, Any]] = []
    for n in rows:
        payload = dict(n.payload or {})
        items.append(
            {
                "id": n.id,
                "channel": n.channel,
                "kind": payload.get("kind"),
                "payload": payload,
                "delivered_at": _to_utc_iso(n.delivered_at),
                "error": n.error,
                "failed": n.error is not None,
                "deep_link": _notification_deep_link(payload),
                "created_at": _to_utc_iso(n.created_at),
            }
        )

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


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
    _actor: str = Depends(require_admin),
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
    _actor: str = Depends(require_admin),
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
    actor: str = Depends(require_admin),
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
    actor: str = Depends(require_admin),
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
# Audit log viewer (Phase 20)
# ---------------------------------------------------------------------------
# Replaces the Phase 12 ``GET /api/admin/audit`` endpoint (admin-secret
# auth, no offset / no resource filters — deprecated in Phase 21 in
# favour of this richer viewer). Filters: actor_type, actor_id, action,
# result, resource_type, resource_id, since, until. Response carries
# ``total`` (full count, not the slice) so the UI can paginate properly.

@router.get(
    "/audit_logs",
    summary="Paginated audit log viewer (admin)",
)
async def list_audit_logs_v2(
    actor_type: Optional[str] = Query(default=None),
    actor_id: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    result_filter: Optional[str] = Query(default=None, alias="result"),
    resource_type: Optional[str] = Query(default=None),
    resource_id: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None, description="ISO-8601 lower bound"),
    until: Optional[datetime] = Query(default=None, description="ISO-8601 upper bound"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Sole-operator audit viewer backed by AuditLog indexes.

    All filter fields are exact match. ``actor_type`` / ``action`` are
    NOT enum-validated at the API boundary — the model is a free-form
    ``String(32/64)`` so new action strings (e.g. ``content_opportunity
    _transition``) pass through. ``result`` IS validated (4-value set)
    to keep the chip filter UI honest.
    """
    filters = []
    if actor_type:
        filters.append(AuditLog.actor_type == actor_type)
    if actor_id:
        filters.append(AuditLog.actor_id == actor_id)
    if action:
        filters.append(AuditLog.action == action)
    if result_filter:
        if result_filter not in {"success", "failure", "blocked", "partial"}:
            raise HTTPException(422, "invalid result filter")
        filters.append(AuditLog.result == result_filter)
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)
    if resource_id:
        filters.append(AuditLog.resource_id == resource_id)
    if since:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        filters.append(AuditLog.created_at >= since)
    if until:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        filters.append(AuditLog.created_at <= until)

    count_stmt = select(func.count()).select_from(AuditLog)
    items_stmt = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    for f in filters:
        count_stmt = count_stmt.where(f)
        items_stmt = items_stmt.where(f)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = list((await session.execute(items_stmt)).scalars().all())

    return {
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
        "total": total,
        "limit": limit,
        "offset": offset,
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
    _actor: str = Depends(require_admin),
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
    actor: str = Depends(require_admin),
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


# ---------------------------------------------------------------------------
# Content Center (Phase 17) — webhook-auth admin endpoints
# ---------------------------------------------------------------------------
def _serialize_content_opportunity(row: ContentOpportunity) -> dict[str, Any]:
    """Project a ContentOpportunity row + ``metadata_json`` bag into a
    JSON-safe dict for admin list/detail responses."""
    md = dict(row.metadata_json or {})
    return {
        "id": row.id,
        "signal_id": row.signal_id,
        "platform": row.platform,
        "audience": row.audience,
        "niche": row.niche,
        "tone": row.tone,
        "content_angle": row.content_angle,
        "hook": row.hook,
        "title_candidates": row.title_candidates,
        "material_ideas": row.material_ideas,
        "script_outline": row.script_outline,
        "recommended_length": row.recommended_length,
        "cta": row.cta,
        "risk_warning": row.risk_warning,
        "content_score": float(row.content_score),
        "status": row.status,
        "compliance_blocked": bool(md.get("compliance_blocked", False)),
        "compliance_risk_score": float(md.get("compliance_risk_score", 0.0)),
        "compliance_risk_types": list(md.get("compliance_risk_types", [])),
        "metadata": md,
        "created_at": _to_utc_iso(row.created_at),
        "updated_at": _to_utc_iso(row.updated_at),
    }


@router.get(
    "/content_opportunities",
    summary="List content opportunities (admin)",
)
async def list_content_opportunities(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by status (draft | approved | published | archived | rejected)",
    ),
    signal_id: Optional[int] = Query(default=None),
    compliance_blocked: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """List ContentOpportunity rows for the admin Content Center.

    ``compliance_blocked`` is filtered in Python post-walk because
    Postgres ``->>`` and SQLite ``json_extract`` differ; per-page
    result is small enough that an O(limit) Python pass is fine.
    """
    repo = ContentOpportunityRepository(session)
    rows, total = await repo.list_paginated(
        status=status_filter, signal_id=signal_id,
        limit=limit, offset=offset,
    )
    items = [_serialize_content_opportunity(r) for r in rows]
    if compliance_blocked is not None:
        items = [
            it for it in items
            if it["compliance_blocked"] == compliance_blocked
        ]
        # ``total`` reflects the post-filter count (Phase 17 — admin UI
        # uses this as a "show only blocked" toggle; the per-page
        # result set is small enough that an O(limit) Python pass is
        # fine and the count is exact).
        total = len(items)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/content_opportunities/{co_id}",
    summary="Single content opportunity (admin)",
)
async def get_content_opportunity(
    co_id: int,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    repo = ContentOpportunityRepository(session)
    row = await repo.get_by_id(co_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content_opportunity not found")
    return _serialize_content_opportunity(row)


async def _transition_content_opportunity(
    session: AsyncSession,
    *,
    co_id: int,
    new_status: str,
    actor: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """Shared helper for approve/reject/publish endpoints.

    Phase 20 — capture the *previous* status before mutating so the
    AuditLog row records `from` along with `to`. Without it the
    dashboard activity feed renders ``"? → approved"``.
    """
    repo = ContentOpportunityRepository(session)
    current = await repo.get_by_id(co_id)
    if current is None:
        raise HTTPException(
            status_code=404, detail="content_opportunity not found"
        )
    from_status = current.status
    try:
        row = await repo.transition_status(co_id, new_status)
    except ContentOpportunityRepository.NotFound:
        raise HTTPException(status_code=404, detail="content_opportunity not found")
    except IllegalStatusTransition as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await session.commit()
    await session.refresh(row)

    await _audit_db(
        session,
        action="content_opportunity_transition",
        actor=actor,
        resource_type="content_opportunity",
        resource_id=str(co_id),
        metadata={"from": from_status, "to": new_status, "reason": reason},
    )
    return _serialize_content_opportunity(row)


@router.post(
    "/content_opportunities/{co_id}/approve",
    summary="draft → approved (admin)",
)
async def approve_content_opportunity(
    co_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    return await _transition_content_opportunity(
        session, co_id=co_id, new_status="approved", actor=actor,
    )


@router.post(
    "/content_opportunities/{co_id}/reject",
    summary="* → rejected (admin)",
)
async def reject_content_opportunity(
    co_id: int,
    body: ContentOpportunityRejectRequest,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    return await _transition_content_opportunity(
        session,
        co_id=co_id,
        new_status="rejected",
        actor=actor,
        reason=body.reason,
    )


@router.post(
    "/content_opportunities/{co_id}/publish",
    summary="approved → published (admin)",
)
async def publish_content_opportunity(
    co_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    return await _transition_content_opportunity(
        session, co_id=co_id, new_status="published", actor=actor,
    )


# ---------------------------------------------------------------------------
# Dashboard summary (Phase 19)
# ---------------------------------------------------------------------------
# Sole-operator console: one endpoint that returns everything the
# `/admin` landing page needs — ContentOpportunity + Signal status
# breakdowns + recent activity feed from AuditLog. Uses the Phase 21
# unified ``require_admin`` (webhook / admin secret / Feishu open_id)
# so the Phase 18 sessionStorage prompt works.
#
# Three serial SELECTs against indexed columns; expected < 200ms even
# at 10k rows. No caching — Phase 20+ may add ETags.

_CONTENT_STATUS_BUCKETS: tuple[str, ...] = (
    "draft", "approved", "published", "rejected", "archived",
)
_SIGNAL_STATUS_BUCKETS: tuple[str, ...] = (
    "discovered", "validating", "verified", "analyzing",
    "published", "expired", "rejected",
)


async def _build_dashboard(session: AsyncSession) -> dict[str, Any]:
    """Aggregate stats + last 20 content_opportunity transitions."""
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. ContentOpportunity aggregates ---------------------------------
    co_total = (await session.execute(
        select(func.count()).select_from(ContentOpportunity)
    )).scalar_one()

    co_by_status_rows = (await session.execute(
        select(ContentOpportunity.status, func.count())
        .group_by(ContentOpportunity.status)
    )).all()
    co_by_status = {s: 0 for s in _CONTENT_STATUS_BUCKETS}
    for status_value, count in co_by_status_rows:
        if status_value in co_by_status:
            co_by_status[status_value] = count

    co_recent_7d = (await session.execute(
        select(func.count()).select_from(ContentOpportunity)
        .where(ContentOpportunity.created_at >= seven_days_ago)
    )).scalar_one()

    co_new_today = (await session.execute(
        select(func.count()).select_from(ContentOpportunity)
        .where(ContentOpportunity.created_at >= today_start)
    )).scalar_one()

    # Blocked review queue = drafts with compliance_blocked=true in
    # metadata_json. Pull the latest 200 drafts (Phase 17 plan §B
    # notes SQL JSON index is deferred to Phase 20) and filter in
    # Python. The dashboard's "today" workload is well under that.
    draft_rows = (await session.execute(
        select(ContentOpportunity.metadata_json)
        .where(ContentOpportunity.status == "draft")
        .limit(200)
    )).all()
    blocked_review_queue = sum(
        1 for (md,) in draft_rows
        if isinstance(md, dict) and md.get("compliance_blocked") is True
    )

    # 2. Signal aggregates ---------------------------------------------
    sig_total = (await session.execute(
        select(func.count()).select_from(Signal)
    )).scalar_one()

    sig_by_status_rows = (await session.execute(
        select(Signal.status, func.count())
        .group_by(Signal.status)
    )).all()
    sig_by_status = {s: 0 for s in _SIGNAL_STATUS_BUCKETS}
    for status_value, count in sig_by_status_rows:
        if status_value in sig_by_status:
            sig_by_status[status_value] = count

    sig_recent_7d = (await session.execute(
        select(func.count()).select_from(Signal)
        .where(Signal.created_at >= seven_days_ago)
    )).scalar_one()
    sig_new_today = (await session.execute(
        select(func.count()).select_from(Signal)
        .where(Signal.created_at >= today_start)
    )).scalar_one()

    # 3. Recent activity feed (last 20 content_opportunity_transition) -
    activity_rows = (await session.execute(
        select(AuditLog)
        .where(AuditLog.action == "content_opportunity_transition")
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    )).scalars().all()

    return {
        "generated_at": _to_utc_iso(now),
        "content_opportunities": {
            "total": int(co_total),
            "by_status": co_by_status,
            "blocked_review_queue": blocked_review_queue,
            "recent_7d_count": int(co_recent_7d),
            "new_today": int(co_new_today),
        },
        "signals": {
            "total": int(sig_total),
            "by_status": sig_by_status,
            "recent_7d_count": int(sig_recent_7d),
            "new_today": int(sig_new_today),
            "verified_count": sig_by_status.get("verified", 0),
        },
        "recent_activity": [
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
            for r in activity_rows
        ],
    }


@router.get(
    "/dashboard",
    summary="Admin dashboard summary (admin)",
)
async def get_dashboard(
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Aggregated stats + recent activity feed for the operator console.

    Returns ContentOpportunity status breakdown, blocked-review queue,
    Signal health, and the latest 20 ``content_opportunity_transition``
    AuditLog rows. Sole-operator dashboard — one round-trip per page
    load is enough; refresh by browser reload.
    """
    return await _build_dashboard(session)


__all__ = ["router"]