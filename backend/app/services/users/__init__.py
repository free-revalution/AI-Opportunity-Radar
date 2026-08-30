"""User preferences service — Phase 15B v2.0.

Per docs/下一阶段开发技术方案.md §31 / §86-87 / §130:

  > User 增加字段: vertical, niche, platform, audience, tone, language,
  >   preferences_json(其余偏好)

Phase 15 只做 **持久化 + 校验**。把字段写入 User 表后 ContentRadarAgent
读取是 Phase 16(ContentRadarAgent V2)。见 plan §5。

设计要点:

  * **校验白名单** — platform/tone/language 各有允许值,乱填就拒绝,
    避免 User 表被灌垃圾(下一阶段 doc §130 「偏好要稳定可靠」)。
  * **`get_or_create_user_by_feishu` 幂等** — `/preferences` 第一次读时
    自动建一行(不需要单独的"注册"流程),`/activate` 成功后也调一次。
  * **`apply_preference` 内存改 ORM 行** — 调用方负责 commit,
    让批量写一次 commit(避免多次 round-trip)。
  * **`update_subscription_mirror`** — `_ensure_subscription` 在成功绑
    定后调一次,把 status/expires_at 同步到 User 行,让 /preferences
    一行读出来就能告诉用户"你的专业版到 2026-10-12 到期"。
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 白名单
# ---------------------------------------------------------------------------
_ALLOWED_PLATFORMS: frozenset[str] = frozenset(
    {
        "general",       # 通用(默认)
        "douyin",        # 抖音
        "xiaohongshu",   # 小红书
        "bilibili",      # B 站
        "wechat",        # 公众号
        "youtube",       # YouTube
        "tiktok",        # TikTok
    }
)

# 中文 tone 取 doc §130 推荐的 4 类
_ALLOWED_TONES: frozenset[str] = frozenset({"通俗", "专业", "幽默", "严肃"})

# ISO 639-1 两位代码(本 MVP 实际只接 zh / en)
_ALLOWED_LANGUAGES: frozenset[str] = frozenset({"zh", "en"})


# ---------------------------------------------------------------------------
# 可设置偏好字段映射(key → ORM 列名)
# ---------------------------------------------------------------------------
# 注意: `plan` 不在白名单 — 订阅 plan 由 Subscription 表独占,改 User.plan
# 不影响 PaywallVerdict(后者直接查 Subscription 表)。这里只暴露 doc §31
# 列出的 6 个个人偏好字段。
PREFERENCE_KEY_TO_COLUMN: dict[str, str] = {
    "vertical": "vertical",
    "niche": "niche",
    "platform": "platform",
    "audience": "audience",
    "tone": "tone",
    "language": "language",
}

ALLOWED_KEYS: frozenset[str] = frozenset(PREFERENCE_KEY_TO_COLUMN.keys())


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_preference(key: str, value: str) -> tuple[bool, str | None]:
    """检查 (key, value) 是否合法。

    Returns:
      (True,  None)                — 合法
      (False, "中文错误信息")        — 非法,文案可直接给飞书 reply
    """
    if not value or not value.strip():
        return False, f"❌ {key} 不能为空。"

    value = value.strip()

    if key not in ALLOWED_KEYS:
        allowed_list = " / ".join(sorted(ALLOWED_KEYS))
        return False, f"❌ 未知的偏好字段 {key!r}。\n允许的字段:{allowed_list}"

    if key == "platform":
        if value not in _ALLOWED_PLATFORMS:
            allowed_list = " / ".join(sorted(_ALLOWED_PLATFORMS))
            return False, (
                f"❌ platform 不在允许列表。\n"
                f"允许值:{allowed_list}\n"
                f"收到的值:{value!r}"
            )
        return True, None

    if key == "tone":
        if value not in _ALLOWED_TONES:
            allowed_list = " / ".join(_ALLOWED_TONES)
            return False, (
                f"❌ tone 不在允许列表。\n"
                f"允许值:{allowed_list}\n"
                f"收到的值:{value!r}"
            )
        return True, None

    if key == "language":
        if value not in _ALLOWED_LANGUAGES:
            allowed_list = " / ".join(sorted(_ALLOWED_LANGUAGES))
            return False, (
                f"❌ language 不在允许列表。\n"
                f"允许值:{allowed_list}"
            )
        return True, None

    # vertical / niche / audience 都是自由文本,做长度上限
    if key == "vertical":
        if len(value) > 64:
            return False, "❌ vertical 太长(最多 64 字符)。"
        return True, None
    if key == "niche":
        if len(value) > 128:
            return False, "❌ niche 太长(最多 128 字符)。"
        return True, None
    if key == "audience":
        if len(value) > 255:
            return False, "❌ audience 太长(最多 255 字符)。"
        return True, None

    return True, None


# ---------------------------------------------------------------------------
# 应用偏好(内存修改)
# ---------------------------------------------------------------------------
def apply_preference(user: User, key: str, value: str) -> tuple[User, str | None]:
    """把 (key, value) 写到 user 行上,**不 commit**。

    Returns:
      (user, None)               — 成功
      (user, "错误信息")           — 失败,user 状态不变
    """
    if key not in PREFERENCE_KEY_TO_COLUMN:
        return user, f"❌ 未知的偏好字段 {key!r}。"

    column = PREFERENCE_KEY_TO_COLUMN[key]
    if not hasattr(user, column):
        return user, f"❌ User 模型缺字段 {column!r}。"

    ok, err = validate_preference(key, value)
    if not ok:
        return user, err

    setattr(user, column, value.strip())
    return user, None


def reset_preferences(user: User) -> User:
    """清空 6 个偏好列(保留 preferences_json 不动,Phase 16 用)。"""
    for column in PREFERENCE_KEY_TO_COLUMN.values():
        setattr(user, column, None)
    return user


# ---------------------------------------------------------------------------
# Subscription mirror
# ---------------------------------------------------------------------------
def update_subscription_mirror(
    user: User,
    *,
    status: str,
    expires_at: Any,
    plan: Optional[str] = None,
) -> None:
    """把订阅状态镜像到 User 行(给 /preferences 一行读出来用)。

    ``plan`` 是可选的——activation 流程知道 plan 时会传过来,但
    _mirror_subscription_to_user 也支持 plan=None(只更新 status /
    expires_at)。如果没传,保留 User 行的现有 plan 列不变。
    """
    user.subscription_status = status
    user.subscription_expires_at = expires_at
    if plan is not None:
        user.plan = plan


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render_preferences_zh(user: User) -> str:
    """给 /preferences 命令构造中文回复。"""
    lines: list[str] = ["📋 **你的偏好设置**", ""]
    rows: list[tuple[str, str | None]] = [
        ("vertical", user.vertical),
        ("niche", user.niche),
        ("platform", user.platform),
        ("audience", user.audience),
        ("tone", user.tone),
        ("language", user.language),
    ]
    for key, val in rows:
        if val:
            lines.append(f"• **{key}**: {val}")
        else:
            lines.append(f"• **{key}**: _(未设置)_")

    lines.append("")
    lines.append("💎 **订阅**")
    plan_label = user.plan or user.subscription_status or "free"
    lines.append(f"• 套餐:`{plan_label}`")
    lines.append(
        f"• 状态:`{user.subscription_status or 'free'}`"
    )
    if user.subscription_expires_at:
        lines.append(
            f"• 到期:`{user.subscription_expires_at.isoformat()}`"
        )
    else:
        lines.append("• 到期:_(无)_")

    lines.append("")
    lines.append("用法:`/preferences set <key>=<value>` / `/preferences reset`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_or_create
# ---------------------------------------------------------------------------
async def get_or_create_user_by_feishu(
    session: AsyncSession,
    feishu_open_id: str,
    *,
    commit: bool = True,
) -> User:
    """Find or insert a User by Feishu open id.

    * Idempotent — concurrent calls converge to a single row.
    * Auto-fills stub email / password_hash so the existing NOT NULL
      constraints pass without a real auth flow (Feishu binding is the
      primary identity for v2.0 — see plan §3).

    ``commit=True`` (default) commits the transaction so the row is
    visible to subsequent sessions — convenient for the Feishu command
    handlers that want "find-or-insert then read-back" semantics.
    Pass ``commit=False`` to defer the commit to the caller (useful
    when the upsert is part of a larger write transaction).
    """
    if not feishu_open_id:
        raise ValueError("feishu_open_id required")

    existing: Optional[User] = await session.scalar(
        select(User).where(User.feishu_open_id == feishu_open_id)
    )
    if existing is not None:
        return existing

    # Stub — never used at the bot layer; the legacy email/password
    # path is dormant. ``feishu-{open_id}@radar.local`` is unique per
    # Feishu ID so concurrent inserts collide on the email index first.
    user = User(
        email=f"feishu-{feishu_open_id}@radar.local",
        password_hash="!feishu-stub-no-password!",
        plan="free",
        feishu_open_id=feishu_open_id,
    )
    session.add(user)
    try:
        await session.flush()
    except Exception:
        # Concurrent INSERT race — roll back our attempt and re-SELECT.
        await session.rollback()
        existing = await session.scalar(
            select(User).where(User.feishu_open_id == feishu_open_id)
        )
        if existing is not None:
            return existing
        raise
    if commit:
        await session.commit()
        await session.refresh(user)
    return user


__all__ = [
    "ALLOWED_KEYS",
    "PREFERENCE_KEY_TO_COLUMN",
    "apply_preference",
    "build_vertical_context_for_open_id",
    "get_or_create_user_by_feishu",
    "render_preferences_zh",
    "reset_preferences",
    "update_subscription_mirror",
    "validate_preference",
]


# ---------------------------------------------------------------------------
# Phase 16C — vertical context bridge
# ---------------------------------------------------------------------------
async def build_vertical_context_for_open_id(
    session: AsyncSession,
    feishu_open_id: str,
) -> Any:
    """Look up (or auto-create) the User row for ``feishu_open_id`` and
    build a :class:`VerticalContext` from its preference columns.

    Phase 16 wires the ContentRadarAgent into the Feishu handlers
    (``/today``, ``/search``, ``/content``), which means each handler
    needs a `VerticalContext` for the sender. Lazy-upserting a User
    row is the right default — a brand-new Feishu user who runs
    ``/content 42`` before ever running ``/preferences`` still gets a
    well-formed context (with the defaults from `agents/base.py`).

    The upsert is committed at the end so the row is durable — the
    caller doesn't have to worry about it. Pass an explicit
    ``commit=False`` only if you're composing this with a larger
    transaction.

    Note: the return type is intentionally `Any` because we import
    `VerticalContext` lazily to avoid a circular import
    (`agents.context` → `User` → `services.users`).
    """
    user = await get_or_create_user_by_feishu(
        session, feishu_open_id, commit=True
    )
    # Lazy import — `agents.context` imports `User`, which would
    # cycle through `services.users` if pulled at module load.
    from app.services.agents.context import build_vertical_context

    return build_vertical_context(user, sender_open_id=feishu_open_id)
