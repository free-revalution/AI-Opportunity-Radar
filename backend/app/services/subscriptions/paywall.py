"""Subscription paywall — Phase 16 v2.0.

`gate()`(在 ``subscriptions/__init__.py``)只回答"订阅是否 active"
这一个问题。Phase 15/16 还要在"每天能用几次"上做强制——这才是 doc §47
要的"Free 每天 1 个 Signal / Basic 5 个 / Pro 20 个"语义。

设计取舍见 plan 文件 §1-§6:

  * **view_top_signals quota = SADD distinct IDs**(Phase 16)——doc §47
    字面"每天 N 个 *公开 Signal*" = distinct 计数。Free 用户第 2 次
    /today 必须返 quota deny,不是返 Top 10 但 INCR=2。Redis SET 而不是
    INCR 字符串。research / content_full 仍走 INCR(每次调用 = 1 piece)。
  * **Redis 不可用 → fail-open**,记 warn log,不阻塞付费用户。
  * **TTL 到 UTC 当日 24:00**——key 自带日期,跨日自动重置。
  * **过期订阅 → 视为 free**(quota=1),给老用户 1 次/天尝鲜。
  * **找不到订阅记录 → 视为 free**(同上)。

调用时机(``feishu/inbound.py::route()``):

  在 dispatch handler 之前先 ``await check_access(...)``。
  不允许 → 直接 return ``CommandReply`` 拒绝文案,不进 handler。
  handler 成功跑完 → ``await record_consumption(...)``(research /
  content_full)或 ``await record_view_top_signals(...)``(view_top_signals)。
  ``record_view_top_signals`` 需要 handler 把真实"展示给用户"的 signal
  IDs 传进去——所以 handler 才有素材做"截断到剩余 quota"那一步。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription
from app.services.subscriptions import (
    PLAN_CATALOGUE,
    Plan,
    PlanProfile,
    SubscriptionStatus,
    get_plan_profile,
)
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 命令 → quota 字段映射
# ---------------------------------------------------------------------------
# Phase 15 把 doc §47 的「free/basic/pro/creator 每天信号 / 研究 / 内容」
# quota 拉进 PaywallVerdict。映射关系:每个 BotCommand.kind 对应 PlanProfile
# 中的一个 quota 字段。
COMMAND_TO_FEATURE: dict[str, str] = {
    "today": "view_top_signals",
    "top": "view_top_signals",
    "search": "view_top_signals",
    "research": "research",
    "content": "content_full",
    "daily": "content_full",
    "report": "content_full",
    "table": "content_full",
    # "help" / "activate" / "preferences" / "score" / "refresh" 不进 paywall
}


def command_to_feature(kind: str) -> Optional[str]:
    """Map a ``BotCommand.kind`` to a quota feature. ``None`` → bypass."""
    return COMMAND_TO_FEATURE.get(kind)


# ---------------------------------------------------------------------------
# Redis key schema
# ---------------------------------------------------------------------------
def _utc_day_key(now: datetime) -> str:
    """``YYYYMMDD`` UTC — appended to quota keys so they expire at UTC 24:00."""
    return now.strftime("%Y%m%d")


def quota_key(quota_type: str, feishu_open_id: str, now: datetime) -> str:
    """Redis key for a per-user, per-day quota counter.

    Schema: ``radar:q:{quota_type}:{open_id}:{yyyymmdd_utc}``

    TTL is set on first INCR to ``_seconds_until_utc_midnight(now)``.
    """
    return f"radar:q:{quota_type}:{feishu_open_id}:{_utc_day_key(now)}"


def _seconds_until_utc_midnight(now: datetime) -> int:
    """Seconds from ``now`` (UTC) to the next 00:00 UTC.

    Min 60 — never set a 0-second TTL (would race with INCR).
    """
    tomorrow = (now + __import__("datetime").timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    delta = (tomorrow - now).total_seconds()
    return max(60, int(delta))


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class PaywallVerdict:
    """Outcome of a paywall check.

    Attributes:
      allowed:           True → handler may run + record_consumption.
      plan:              "free" / "basic" / "pro" / "creator" / "unknown"
                         (unknown when no subscription + Redis fail-open).
      expires_at:        Subscription row's expires_at (or None for free).
      quota_type:        "view_top_signals" / "research" / "content_full".
      quota_limit:       Per-plan cap. 0 means feature is blocked outright.
      quota_used:        Current count from Redis (or 0 if Redis down).
      deny_reason:       "" when allowed; a short token otherwise.
      deny_message_zh:   Pre-baked Chinese reply line (use as
                         ``CommandReply.text``).
    """

    allowed: bool
    plan: str
    quota_type: str
    quota_limit: int
    quota_used: int
    expires_at: Optional[datetime] = None
    deny_reason: str = ""
    deny_message_zh: str = ""


# ---------------------------------------------------------------------------
# Plan helpers (private — the public catalogue lives in subscriptions/)
# ---------------------------------------------------------------------------
def _plan_for_row(row: Optional[Subscription]) -> str:
    """Pick the plan for a (possibly missing) Subscription row."""
    if row is None:
        return Plan.FREE.value
    return row.plan or Plan.FREE.value


def _quota_limit_for(plan: str, feature: str) -> int:
    """Look up the per-plan limit for ``feature``. 0 means blocked."""
    profile = get_plan_profile(plan)
    if feature == "view_top_signals":
        return profile.daily_signals
    if feature == "research":
        return profile.research_requests
    if feature == "content_full":
        return profile.content_pieces
    return 0


def _is_row_active(row: Optional[Subscription], *, now: datetime) -> bool:
    if row is None:
        return False
    if row.status != SubscriptionStatus.ACTIVE.value:
        return False
    if row.expires_at is None:
        return True
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    return exp > now


# ---------------------------------------------------------------------------
# Active subscription lookup
# ---------------------------------------------------------------------------
async def _find_active_subscription(
    session: AsyncSession,
    *,
    feishu_open_id: str,
    now: datetime,
) -> Optional[Subscription]:
    """Most recent active subscription for ``feishu_open_id``.

    "Active" = ``status='active' AND expires_at > now`` (NULL expires_at
    is treated as perpetual). Returns ``None`` for free-tier users.
    """
    stmt = (
        select(Subscription)
        .where(Subscription.feishu_open_id == feishu_open_id)
        .where(Subscription.status == SubscriptionStatus.ACTIVE.value)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    if not _is_row_active(row, now=now):
        return None
    return row


# ---------------------------------------------------------------------------
# Redis quota counter
# ---------------------------------------------------------------------------
async def _read_quota(
    redis_client: Any,
    *,
    quota_type: str,
    feishu_open_id: str,
    now: datetime,
) -> int:
    """Read the current count for ``(quota_type, feishu_open_id)``.

    Returns 0 when the key doesn't exist or Redis is unavailable.
    Never raises — quota checks must always be best-effort.
    """
    if redis_client is None:
        return 0
    key = quota_key(quota_type, feishu_open_id, now)
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "paywall_redis_get_failed",
            quota_type=quota_type,
            error=str(exc)[:200],
        )
        return 0
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def _bump_quota(
    redis_client: Any,
    *,
    quota_type: str,
    feishu_open_id: str,
    now: datetime,
) -> int:
    """INCR + first-time EXPIRE. Returns the new count, or 0 if Redis down."""
    if redis_client is None:
        return 0
    key = quota_key(quota_type, feishu_open_id, now)
    try:
        new_count = await redis_client.incr(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "paywall_redis_incr_failed",
            quota_type=quota_type,
            error=str(exc)[:200],
        )
        return 0
    # First-time EXPIRE — only when INCR returns 1 (new key).
    if int(new_count) == 1:
        try:
            await redis_client.expire(key, _seconds_until_utc_midnight(now))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "paywall_redis_expire_failed",
                quota_type=quota_type,
                error=str(exc)[:200],
            )
    return int(new_count)


# ---------------------------------------------------------------------------
# Phase 16 — Distinct-signal quota (SADD-based)
# ---------------------------------------------------------------------------
# doc §47 "Free 每天 1 个公开 Signal / Basic 5 / Pro 10~20" 字面意思
# 是"每天能看到 *N 个不同的* signal",而不是"能调 N 次命令"。Phase 15
# 用 INCR 走的是调用次数语义,Free 用户 /today 第 2 次依然返 Top 10,
# 违反产品语义。Phase 16 改 SADD:每个 signal ID 一天只能被记录一次,
# 第 2 次 /today 时 SCARD == limit → deny。
#
# Key schema 跟 INCR 一致(同 `quota_key(...)`),只是为了语义清晰把
# "view_top_signals" 单独提到这个文件里——Redis 里它实际是 SET 类型,
# 而 research / content_full 仍是 STRING(整数)。
async def peek_view_top_signals_count(
    redis_client: Any,
    sender_open_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """SCARD the per-day distinct-signal set.

    Returns the number of *distinct* signal IDs already shown to
    ``sender_open_id`` today. Returns 0 when the key is missing OR
    Redis is unavailable — never raises (paywall must be best-effort).
    """
    if redis_client is None:
        return 0
    now = now or datetime.now(tz=timezone.utc)
    key = quota_key("view_top_signals", sender_open_id, now)
    try:
        count = await redis_client.scard(key)
    except AttributeError:
        # Phase 16E — `redis_client.scard` missing (older fake / stub).
        logger.warning("paywall_redis_scard_unsupported")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "paywall_redis_scard_failed",
            error=str(exc)[:200],
        )
        return 0
    try:
        return int(count)
    except (TypeError, ValueError):
        return 0


async def record_view_top_signals(
    redis_client: Any,
    sender_open_id: str,
    signal_ids: list[int | str],
    *,
    now: datetime | None = None,
) -> int:
    """SADD ``signal_ids`` to the per-day distinct-signal set.

    Idempotent for duplicate IDs — Redis SADD returns the count of
    *new* members added; we use that to refresh the TTL (first SADD
    of the day → EXPIRE to UTC midnight). Empty ``signal_ids`` is a
    no-op and returns 0 without touching Redis.

    Returns the new SCARD, or 0 when Redis is unavailable.
    """
    if redis_client is None:
        return 0
    if not signal_ids:
        return 0
    now = now or datetime.now(tz=timezone.utc)
    key = quota_key("view_top_signals", sender_open_id, now)
    members = [str(sid) for sid in signal_ids]
    try:
        added = await redis_client.sadd(key, *members)
    except AttributeError:
        logger.warning("paywall_redis_sadd_unsupported")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "paywall_redis_sadd_failed",
            error=str(exc)[:200],
        )
        return 0
    # First SADD of the day → set TTL. (SADD returns members-actually-
    # added; comparing against requested length is more correct than
    # testing "added == 1", because a duplicate is a no-op.)
    if int(added) > 0:
        try:
            expire_fn = getattr(redis_client, "expire_set", None) or getattr(
                redis_client, "expire", None
            )
            if expire_fn is not None:
                await expire_fn(key, _seconds_until_utc_midnight(now))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "paywall_redis_set_expire_failed",
                error=str(exc)[:200],
            )
    # Return the post-add SCARD so callers can show "used N / limit M".
    try:
        count = await redis_client.scard(key)
        return int(count)
    except Exception:  # noqa: BLE001
        return int(added)


# ---------------------------------------------------------------------------
# Public API — access check + consumption recording
# ---------------------------------------------------------------------------
async def check_access(
    session: AsyncSession,
    sender_open_id: str,
    command_kind: str,
    *,
    redis_client: Any,
    now: datetime | None = None,
) -> PaywallVerdict:
    """Decide whether ``sender_open_id`` may run ``command_kind``.

    Quota map is ``COMMAND_TO_FEATURE``. Commands not in the map bypass
    paywall (``help`` / ``activate`` / ``preferences`` etc.).

    Returns a :class:`PaywallVerdict` with both the boolean and a
    pre-rendered Chinese denial line. Callers are expected to short-
    circuit on ``verdict.allowed is False`` *before* dispatching to
    the handler.

    Quota counts are *read* here but **not incremented**. The handler
    should call :func:`record_consumption` after a successful run.
    """
    now = now or datetime.now(tz=timezone.utc)
    feature = command_to_feature(command_kind)
    if feature is None:
        # No quota → free pass. Caller still gets a verdict so the
        # handler can attach metadata.
        return PaywallVerdict(
            allowed=True,
            plan="unknown",
            quota_type="bypass",
            quota_limit=0,
            quota_used=0,
        )

    row = await _find_active_subscription(
        session, feishu_open_id=sender_open_id, now=now
    )
    plan = _plan_for_row(row)
    limit = _quota_limit_for(plan, feature)
    # Phase 16 — distinct-signal quota is SADD-based; everything else
    # stays on INCR (research / content_full are "1 piece per call").
    if feature == "view_top_signals":
        used = await peek_view_top_signals_count(
            redis_client, sender_open_id, now=now
        )
    else:
        used = await _read_quota(
            redis_client,
            quota_type=feature,
            feishu_open_id=sender_open_id,
            now=now,
        )

    # Hard plan block — feature requires a paid tier but plan gives 0/day.
    if limit <= 0:
        verdict = PaywallVerdict(
            allowed=False,
            plan=plan,
            quota_type=feature,
            quota_limit=limit,
            quota_used=used,
            expires_at=row.expires_at if row else None,
            deny_reason="plan_no_feature",
            deny_message_zh=_plan_block_message_zh(plan, feature),
        )
        return verdict

    # Quota exhausted for the day.
    if used >= limit:
        verdict = PaywallVerdict(
            allowed=False,
            plan=plan,
            quota_type=feature,
            quota_limit=limit,
            quota_used=used,
            expires_at=row.expires_at if row else None,
            deny_reason="quota_exceeded",
            deny_message_zh=_quota_message_zh(plan, feature, used, limit),
        )
        return verdict

    # OK.
    return PaywallVerdict(
        allowed=True,
        plan=plan,
        quota_type=feature,
        quota_limit=limit,
        quota_used=used,
        expires_at=row.expires_at if row else None,
    )


async def record_consumption(
    redis_client: Any,
    sender_open_id: str,
    quota_type: str,
    *,
    now: datetime | None = None,
) -> int:
    """Increment the per-day quota counter.

    Call **after** the handler succeeded — denied handlers should not
    bump the counter. Quota type must be one of ``COMMAND_TO_FEATURE``
    values; ``"bypass"`` and unknown values are silently ignored
    (defensive: callers may pass the type even when the verdict was a
    bypass).

    Returns the new count, or 0 when Redis is unavailable.
    """
    if quota_type in ("bypass", "", None):
        return 0
    if quota_type not in COMMAND_TO_FEATURE.values():
        return 0
    now = now or datetime.now(tz=timezone.utc)
    return await _bump_quota(
        redis_client,
        quota_type=quota_type,
        feishu_open_id=sender_open_id,
        now=now,
    )


# ---------------------------------------------------------------------------
# Chinese denial copy
# ---------------------------------------------------------------------------
def _plan_block_message_zh(plan: str, feature: str) -> str:
    """Free 用户的「此功能需付费」文案。"""
    if feature == "research":
        return (
            "🔒 深度研究是付费功能。\n"
            "升级到基础版(¥29/月)即可每天发起 1 次研究。\n"
            "回复 /activate <激活码> 绑定订阅。"
        )
    if feature == "content_full":
        return (
            "🔒 完整内容方案是付费功能。\n"
            "升级到基础版(¥29/月)即可解锁。\n"
            "回复 /activate <激活码> 绑定订阅。"
        )
    # view_top_signals → free quota = 1, shouldn't hit this path normally.
    return "🔒 当前套餐暂不支持此功能,升级订阅后即可使用。"


def _quota_message_zh(plan: str, feature: str, used: int, limit: int) -> str:
    """Quota 用完的中文文案 + 升级 CTA。"""
    feature_zh = {
        "view_top_signals": "信号",
        "research": "深度研究",
        "content_full": "内容方案",
    }.get(feature, "操作")

    profile: PlanProfile = get_plan_profile(plan)
    plan_zh = profile.name_zh

    upgrade_hint = ""
    if plan == Plan.FREE.value:
        upgrade_hint = (
            "\n升级到基础版(¥29/月)每天 5 个、专业版(¥59/月)每天 20 个。"
        )
    elif plan == Plan.BASIC.value:
        upgrade_hint = "\n升级到专业版(¥59/月)每天 20 个。"

    return (
        f"⏰ 今日{feature_zh}额度已用完({used}/{limit} · {plan_zh})。"
        f"{upgrade_hint}\n"
        f"明日 UTC 00:00 自动重置。"
    )


__all__ = [
    "COMMAND_TO_FEATURE",
    "PaywallVerdict",
    "check_access",
    "command_to_feature",
    "peek_view_top_signals_count",
    "quota_key",
    "record_consumption",
    "record_view_top_signals",
]
