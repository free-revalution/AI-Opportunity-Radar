"""Activation flow — Phase 14A.

Orchestration layer between the Feishu ``/activate <code>`` command and
the pure-data reducers in ``app.services.activation``.

Per docs/下一阶段开发技术方案.md §50-53 + §88:

> 用户在闲鱼下单 → 管理员通过 /api/admin/activation/issue 生成 Code
> → 管理员在闲鱼把 Code 发给买家 → 买家在飞书 /activate <Code>
> → 飞书机器人把 Code 绑定到买家的 Feishu Open ID 并创建 Subscription
> → 买家立即可使用 /today /research /content 等付费功能

The flow:

  1. Hash the supplied code and look it up in the activation_codes table.
  2. Validate format + status (unused/active/revoked/expired).
  3. Bind to ``feishu_open_id`` if not already bound.
  4. Create or update the user's ``Subscription`` row for the Code's plan.
  5. Write an ``AuditLog`` row tagged ``activate`` (success or blocked).
  6. Return a ``RedemptionResult`` carrying the user-facing Chinese reply.

The flow never raises on user input — bad codes, expired codes, and
already-bound codes all return a structured ``RedemptionResult`` with
``success=False`` and a friendly Chinese message. The DB write path
(subscription + audit) is only invoked on success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivationCode, AuditLog, Subscription
from app.services.activation import (
    ActivationError,
    DEFAULT_SERVER_PEPPER,
    hash_code,
    redeem_code,
    validate_format,
)
from app.services.subscriptions import PLAN_CATALOGUE, get_plan_profile


# ---------------------------------------------------------------------------
# Plan catalogue display strings (Chinese).
# ---------------------------------------------------------------------------
_PLAN_DISPLAY_ZH: dict[str, str] = {
    "free": "免费",
    "basic": "基础版(¥29/月)",
    "pro": "专业版(¥59/月)",
    "creator": "创作者版(¥129/月)",
}


def plan_display_zh(plan: str) -> str:
    """Return the Chinese display name for ``plan`` (or ``plan`` itself)."""
    return _PLAN_DISPLAY_ZH.get(plan, plan)


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------
class RedemptionStatus(str, Enum):
    """End-state of a single redemption attempt."""

    SUCCESS = "success"                 # new binding
    ALREADY_ACTIVE = "already_active"   # idempotent re-bind by same user
    ALREADY_BOUND = "already_bound"
    EXPIRED = "expired"
    REVOKED = "revoked"
    NOT_FOUND = "not_found"
    INVALID_FORMAT = "invalid_format"


@dataclass(slots=True)
class RedemptionResult:
    """Structured outcome of ``redeem_for_user``."""

    status: RedemptionStatus
    success: bool
    plan: Optional[str] = None
    expires_at: Optional[datetime] = None
    code_id: Optional[int] = None
    error: Optional[ActivationError] = None
    user_message: str = ""
    audit_action: str = "activate"
    audit_result: str = "success"


# ---------------------------------------------------------------------------
# User-facing messages
# ---------------------------------------------------------------------------
def _format_expires(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def user_message(result: RedemptionResult) -> str:
    """Return the Chinese reply that should be shown to the user."""
    plan_zh = plan_display_zh(result.plan or "")
    if result.status == RedemptionStatus.SUCCESS:
        return (
            f"✅ 激活成功!已开通 **{plan_zh}**\n"
            f"📅 到期时间:{_format_expires(result.expires_at)}\n"
            f"现在你可以使用 /today /research /content 等所有功能 🎉"
        )
    if result.status == RedemptionStatus.ALREADY_ACTIVE:
        return (
            f"✅ 你已开通 **{plan_zh}**\n"
            f"📅 到期时间:{_format_expires(result.expires_at)}\n"
            f"无需重复激活。"
        )
    if result.status == RedemptionStatus.ALREADY_BOUND:
        return "❌ 该激活码已被其他账号使用。如需协助请联系客服。"
    if result.status == RedemptionStatus.EXPIRED:
        return "❌ 激活码已过期。请联系客服续费。"
    if result.status == RedemptionStatus.REVOKED:
        return "❌ 激活码已被吊销。请联系客服。"
    if result.status == RedemptionStatus.NOT_FOUND:
        return "❌ 激活码无效。请检查输入,或联系客服。"
    if result.status == RedemptionStatus.INVALID_FORMAT:
        return "❌ 激活码格式不正确。格式示例:`ABCD-EFGH-JKLM`"
    return "❌ 激活失败,请稍后重试或联系客服。"


# ---------------------------------------------------------------------------
# Default subscription duration per plan (days).
# ---------------------------------------------------------------------------
# In production this should come from the Code's bound ``order_id`` /
# a duration table keyed off the plan. Phase 14A uses a simple default:
# pro/creator = 30 days, basic = 30 days, free = 30 days.
_DEFAULT_SUBSCRIPTION_DAYS: dict[str, int] = {
    "free": 30,
    "basic": 30,
    "pro": 30,
    "creator": 30,
}


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------
async def redeem_for_user(
    session: AsyncSession,
    *,
    code: str,
    feishu_open_id: str,
    pepper: str = DEFAULT_SERVER_PEPPER,
    now: Optional[datetime] = None,
    commit: bool = True,
) -> RedemptionResult:
    """Bind ``code`` to ``feishu_open_id`` and create/update Subscription.

    ``commit=True`` (default) commits the DB transaction after writing
    the Subscription + AuditLog rows. Set ``commit=False`` when the
    caller wants to fold this into a larger transaction (e.g. a test
    that seeds + redeems + checks in a single session).

    The reducer ``redeem_code()`` is pure-data; we wrap it with the DB
    side-effects that turn a successful validation into a usable
    subscription.
    """
    now = now or datetime.now(tz=timezone.utc)

    # Cheap pre-check — reject bad formats before hitting the DB.
    if not validate_format(code):
        return _failed(
            RedemptionStatus.INVALID_FORMAT,
            ActivationError.INVALID_FORMAT,
            user_message_text=user_message(
                RedemptionResult(
                    status=RedemptionStatus.INVALID_FORMAT,
                    success=False,
                    error=ActivationError.INVALID_FORMAT,
                )
            ),
        )

    if not feishu_open_id:
        return _failed(
            RedemptionStatus.INVALID_FORMAT,
            ActivationError.INVALID_FORMAT,
            user_message_text=user_message(
                RedemptionResult(
                    status=RedemptionStatus.INVALID_FORMAT,
                    success=False,
                    error=ActivationError.INVALID_FORMAT,
                )
            ),
        )

    # Hash + lookup.
    code_hash = hash_code(code, pepper)
    row = await session.get(ActivationCode, code_hash) if hasattr(
        session, "get"
    ) else None
    # ``session.get`` doesn't take a custom where clause — fall back to a
    # select for the hash column (code_hash is the natural key).
    if row is None:
        from sqlalchemy import select

        stmt = select(ActivationCode).where(ActivationCode.code_hash == code_hash)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    def _lookup_by_hash(h: str) -> Any:
        # The reducer is sync; we precomputed row. If the reducer queries
        # again (it shouldn't given we already have the row), return
        # whatever we have.
        return row

    outcome = redeem_code(
        code,
        feishu_open_id,
        pepper=pepper,
        lookup_by_hash=_lookup_by_hash,
        now=now,
    )

    if not outcome.success:
        status, err = _map_redeem_outcome(outcome.error)
        return _failed(
            status,
            err,
            user_message_text=user_message(
                RedemptionResult(status=status, success=False, error=err)
            ),
        )

    # Successful validation → bind + create subscription.
    if row is None:
        # Defensive — the reducer said success but the row vanished
        # between the lookup and the bind. Treat as not-found.
        return _failed(
            RedemptionStatus.NOT_FOUND,
            ActivationError.NOT_FOUND,
            user_message_text=user_message(
                RedemptionResult(
                    status=RedemptionStatus.NOT_FOUND,
                    success=False,
                    error=ActivationError.NOT_FOUND,
                )
            ),
        )

    plan = outcome.plan or row.plan or "basic"
    is_first_bind = row.bound_feishu_open_id is None
    already_active = row.bound_feishu_open_id == feishu_open_id

    if is_first_bind:
        row.bound_feishu_open_id = feishu_open_id
        row.bound_at = now
        row.used_at = now
        row.status = "active"
    # If already bound to this user → idempotent, no DB writes for the code.

    # Create / update Subscription row.
    sub_expires_at = now + timedelta(days=_DEFAULT_SUBSCRIPTION_DAYS.get(plan, 30))
    sub = await _ensure_subscription(
        session,
        feishu_open_id=feishu_open_id,
        plan=plan,
        expires_at=sub_expires_at,
        commit=False,
    )

    # AuditLog row.
    audit_status = "success" if is_first_bind else "success"  # both idempotent
    audit = AuditLog(
        actor_type="user",
        actor_id=feishu_open_id,
        action="activate",
        resource_type="activation_code",
        resource_id=str(row.id),
        result=audit_status,
        metadata_json={
            "plan": plan,
            "feishu_open_id": feishu_open_id,
            "subscription_id": sub.id if sub else None,
            "idempotent": not is_first_bind,
        },
    )
    session.add(audit)

    if commit:
        await session.commit()
        if sub is not None:
            await session.refresh(sub)
        await session.refresh(audit)

    final_status = (
        RedemptionStatus.ALREADY_ACTIVE if already_active else RedemptionStatus.SUCCESS
    )
    return RedemptionResult(
        status=final_status,
        success=True,
        plan=plan,
        expires_at=sub.expires_at if sub else row.expires_at,
        code_id=row.id,
        user_message=user_message(
            RedemptionResult(
                status=final_status,
                success=True,
                plan=plan,
                expires_at=sub.expires_at if sub else row.expires_at,
            )
        ),
        audit_action="activate",
        audit_result="success",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _map_redeem_outcome(
    err: Optional[ActivationError],
) -> tuple[RedemptionStatus, Optional[ActivationError]]:
    if err == ActivationError.NOT_FOUND:
        return RedemptionStatus.NOT_FOUND, err
    if err == ActivationError.ALREADY_BOUND:
        return RedemptionStatus.ALREADY_BOUND, err
    if err == ActivationError.EXPIRED:
        return RedemptionStatus.EXPIRED, err
    if err == ActivationError.REVOKED:
        return RedemptionStatus.REVOKED, err
    if err == ActivationError.INVALID_FORMAT:
        return RedemptionStatus.INVALID_FORMAT, err
    return RedemptionStatus.NOT_FOUND, err


def _failed(
    status: RedemptionStatus,
    err: Optional[ActivationError],
    *,
    user_message_text: str,
) -> RedemptionResult:
    return RedemptionResult(
        status=status,
        success=False,
        error=err,
        user_message=user_message_text,
        audit_action="activate",
        audit_result="blocked",
    )


async def _ensure_subscription(
    session: AsyncSession,
    *,
    feishu_open_id: str,
    plan: str,
    expires_at: datetime,
    commit: bool,
) -> Optional[Subscription]:
    """Find or create the active Subscription for ``feishu_open_id``.

    Strategy: prefer to extend the *latest* existing active row. If
    none, create a new one. Always returns the row that was created or
    updated (``None`` only when something went wrong).

    ``commit`` is ignored — the caller controls the transaction boundary.
    """
    from sqlalchemy import select

    stmt = (
        select(Subscription)
        .where(Subscription.feishu_open_id == feishu_open_id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        # Don't downgrade — keep the higher plan.
        existing_plan = get_plan_profile(existing.plan)
        new_plan = get_plan_profile(plan)
        # Creator > Pro > Basic > Free.
        rank = {"free": 0, "basic": 1, "pro": 2, "creator": 3}
        if rank.get(plan, 0) > rank.get(existing.plan, 0):
            existing.plan = plan
        # If the existing subscription is expired or about to expire,
        # extend from now. Otherwise leave expires_at alone — the user
        # already paid for that window.
        now = datetime.now(tz=timezone.utc)
        exp = existing.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp is None or exp <= now:
            existing.expires_at = expires_at
        existing.status = "active"
        return existing

    sub = Subscription(
        feishu_open_id=feishu_open_id,
        plan=plan if plan in PLAN_CATALOGUE else "basic",
        status="active",
        expires_at=expires_at,
        source_channel="feishu_activate",
    )
    session.add(sub)
    return sub


__all__ = [
    "RedemptionResult",
    "RedemptionStatus",
    "plan_display_zh",
    "redeem_for_user",
    "user_message",
]