"""Feishu Bot RBAC — Phase 12G.

Per docs/下一阶段开发技术方案.md §36-39:

> 普通用户: /help /today /top /search /research /content /preferences
> 管理员:   /admin refresh /admin score /admin research /admin publish
>           /admin reject /admin stats
>
> 必须 RBAC — 只允许 admin_user_ids 调用 /admin 命令。
> 普通用户调用 admin 命令 → 403-style reply (但不暴露内部权限信息)。
>
> LLM Tool Allowlist — search_signals / get_signal / get_research /
>                      generate_content / get_user_preferences。
> Admin Tool (refresh/score/research/publish/reject) 必须独立权限,
> LLM 永远不能自行调用。

This module is pure-data — it classifies commands and gates by actor id.
The FeishuCommandRouter (or its replacement) calls `authorize()` before
any admin command runs, and `record_rbac_deny()` to log attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, FrozenSet, Iterable, Mapping, Optional


# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------
class CommandRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


# Commands that require admin role. Anything NOT in this set is a USER
# command (per the spec).
_ADMIN_COMMANDS: FrozenSet[str] = frozenset(
    {
        "admin_refresh",
        "admin_score",
        "admin_research",
        "admin_publish",
        "admin_reject",
        "admin_stats",
        "admin_collect",
        # Legacy aliases — gated for safety.
        "refresh",
        "score",
        "daily",
        # Phase 26 — /docs sub-commands. Per product decision (simplify
        # §10 v2.0) ALL /docs operations are admin-only: read-only
        # commands (ls/find/info/tree/daily) and destructive ones
        # (rm/mv/rename/create/mkdir) share the same gate so the
        # bot surface is uniform — no surprise privilege differences
        # between read and write.
        "docs_tree",
        "docs_ls",
        "docs_find",
        "docs_info",
        "docs_daily",
        "docs_create",
        "docs_mkdir",
        "docs_mv",
        "docs_rename",
        "docs_rm",
        "docs_confirm",
        "docs_bitable_ls",
        "docs_bitable_find",
        "docs_bitable_add",
        "docs_bitable_rm",
    }
)

# Tool allowlist for the natural-language bot (per docs §39). LLM may
# invoke only these; admin tools are NEVER on this list.
USER_TOOL_ALLOWLIST: FrozenSet[str] = frozenset(
    {
        "search_signals",
        "get_signal",
        "get_research",
        "generate_content",
        "get_user_preferences",
        "list_my_subscriptions",
        "activate_code",
    }
)


# ---------------------------------------------------------------------------
# Authorisation verdict
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class AuthVerdict:
    allowed: bool
    required_role: CommandRole
    actor_role: CommandRole
    reason: str = ""

    @property
    def is_admin(self) -> bool:
        return self.actor_role is CommandRole.ADMIN


def role_required_for(command_kind: str) -> CommandRole:
    """What role a command needs to run."""
    return CommandRole.ADMIN if command_kind in _ADMIN_COMMANDS else CommandRole.USER


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
def authorize(
    command_kind: str,
    sender_open_id: str | None,
    *,
    admin_open_ids: Iterable[str],
) -> AuthVerdict:
    """Decide whether ``sender_open_id`` may run ``command_kind``.

    ``admin_open_ids`` is the trusted list of admin Feishu open ids —
    typically loaded from the application settings (``ADMIN_USER_IDS``)
    or the ``users`` table where role='admin'. The list is small (a
    handful of operators) so iterating per-command is fine.

    Sender open id matching is **exact** — no normalisation, no
    substring matching. The list must be curated; we never widen
    authorisation on the basis of "looks similar".
    """
    required = role_required_for(command_kind)

    if not sender_open_id:
        # Unknown / anonymous sender — only USER commands allowed.
        return AuthVerdict(
            allowed=(required is CommandRole.USER),
            required_role=required,
            actor_role=CommandRole.USER,
            reason="anonymous_sender",
        )

    admin_set = frozenset(admin_open_ids or ())
    actor_is_admin = sender_open_id in admin_set

    if required is CommandRole.ADMIN and not actor_is_admin:
        return AuthVerdict(
            allowed=False,
            required_role=required,
            actor_role=CommandRole.USER,
            reason="admin_required",
        )

    return AuthVerdict(
        allowed=True,
        required_role=required,
        actor_role=CommandRole.ADMIN if actor_is_admin else CommandRole.USER,
    )


# ---------------------------------------------------------------------------
# LLM Tool allowlist
# ---------------------------------------------------------------------------
def tool_allowed_for_llm(tool_name: str) -> bool:
    """True when an LLM-driven bot may invoke ``tool_name`` directly.

    Per docs §39 — admin tools are NEVER on this list. Even if a user
    prompt contains "you can publish", the LLM has no tool handle to
    actually do it.
    """
    return tool_name in USER_TOOL_ALLOWLIST


def tool_is_admin_only(tool_name: str) -> bool:
    """True for tools that only admin role can invoke."""
    # These mirror the admin command list — kept independent so a
    # future LLM tool registry can diverge if needed.
    return tool_name in {
        "refresh",
        "score",
        "research_trigger",
        "publish",
        "reject",
        "stats",
        "source_enable",
        "source_disable",
    }


# ---------------------------------------------------------------------------
# User-visible reply helper — never leak admin internals
# ---------------------------------------------------------------------------
def user_facing_deny_message(command_kind: str) -> str:
    """Friendly reply shown to a USER who tried to run an admin command.

    Per docs §37 — don't expose internal permission details. The reply
    should hint at the upgrade path without revealing the command name
    or the role-checked mechanism.
    """
    return (
        "这条指令需要更高级的账号权限才能执行。"
        "如需开通,请联系管理员。"
    )


# ---------------------------------------------------------------------------
# Phase 26 — DocsCommandAuthorizer
# ---------------------------------------------------------------------------
class DocsCommandAuthorizer:
    """Thin wrapper that decides whether a sender can run a ``/docs`` sub-command.

    The Phase 26 ``/docs`` family is admin-only across the board (per
    product decision — see ``_ADMIN_COMMANDS`` above). This wrapper
    centralises the call so :mod:`app.services.feishu.inbound` doesn't
    reach into :func:`authorize` directly. It also accepts the
    :class:`Settings` so the caller doesn't need to know about the
    ``admin_open_ids`` storage location (settings vs DB lookup).

    Constructed once per ``/docs`` invocation; the underlying
    :func:`authorize` call is O(len(admin_open_ids)) — a few entries,
    so trivial.
    """

    def __init__(self, *, admin_open_ids: Optional[Iterable[str]] = None) -> None:
        self._admin_open_ids = list(admin_open_ids or ())

    def check(
        self,
        *,
        command_kind: str,
        sender_open_id: Optional[str],
    ) -> AuthVerdict:
        """Return the :class:`AuthVerdict` for ``sender_open_id`` vs ``command_kind``."""
        return authorize(
            command_kind,
            sender_open_id,
            admin_open_ids=self._admin_open_ids,
        )


# ---------------------------------------------------------------------------
# Activation flow (per docs §51-52)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ActivationAttempt:
    """Outcome of a `/activate <code>` flow."""

    success: bool
    feishu_open_id: str
    error: Optional[str] = None  # "invalid_code" | "already_bound" | "expired" | "revoked"
    plan: Optional[str] = None


def is_activation_command(command_kind: str) -> bool:
    """True for `/activate <code>` (USER role)."""
    return command_kind == "activate"


__all__ = [
    "AuthVerdict",
    "ActivationAttempt",
    "CommandRole",
    "DocsCommandAuthorizer",
    "USER_TOOL_ALLOWLIST",
    "authorize",
    "is_activation_command",
    "role_required_for",
    "tool_allowed_for_llm",
    "tool_is_admin_only",
    "user_facing_deny_message",
]