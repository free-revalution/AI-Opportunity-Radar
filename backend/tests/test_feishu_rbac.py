"""Tests for ``app.services.feishu.rbac`` — Phase 12G RBAC + tool allowlist."""

from __future__ import annotations

import pytest

from app.services.feishu import (
    AuthVerdict,
    CommandRole,
    USER_TOOL_ALLOWLIST,
    authorize,
    is_activation_command,
    role_required_for,
    tool_allowed_for_llm,
    tool_is_admin_only,
    user_facing_deny_message,
)


ADMINS = ["ou_admin_1", "ou_admin_2"]
USERS = ["ou_user_42", "ou_user_99"]


# ---------------------------------------------------------------------------
# role_required_for
# ---------------------------------------------------------------------------
class TestRoleRequiredFor:
    @pytest.mark.parametrize(
        "kind",
        [
            "admin_refresh",
            "admin_score",
            "admin_research",
            "admin_publish",
            "admin_reject",
            "admin_stats",
            "admin_collect",
            "refresh",
            "score",
            "daily",
        ],
    )
    def test_admin_commands(self, kind):
        assert role_required_for(kind) is CommandRole.ADMIN

    @pytest.mark.parametrize(
        "kind",
        [
            "help",
            "today",
            "top",
            "research",
            "search",
            "content",
            "preferences",
            "activate",
            "unknown",
        ],
    )
    def test_user_commands(self, kind):
        assert role_required_for(kind) is CommandRole.USER


# ---------------------------------------------------------------------------
# authorize
# ---------------------------------------------------------------------------
class TestAuthorize:
    def test_admin_can_run_admin_command(self):
        v = authorize("admin_refresh", "ou_admin_1", admin_open_ids=ADMINS)
        assert v.allowed
        assert v.actor_role is CommandRole.ADMIN
        assert v.required_role is CommandRole.ADMIN

    def test_user_blocked_from_admin_command(self):
        v = authorize("admin_refresh", "ou_user_42", admin_open_ids=ADMINS)
        assert not v.allowed
        assert v.actor_role is CommandRole.USER
        assert v.required_role is CommandRole.ADMIN
        assert v.reason == "admin_required"

    def test_user_can_run_user_command(self):
        v = authorize("today", "ou_user_42", admin_open_ids=ADMINS)
        assert v.allowed
        assert v.actor_role is CommandRole.USER

    def test_admin_can_also_run_user_command(self):
        v = authorize("today", "ou_admin_1", admin_open_ids=ADMINS)
        assert v.allowed
        assert v.actor_role is CommandRole.ADMIN

    def test_anonymous_sender_allowed_for_user_commands(self):
        v = authorize("today", None, admin_open_ids=ADMINS)
        assert v.allowed
        assert v.actor_role is CommandRole.USER
        assert v.reason == "anonymous_sender"

    def test_anonymous_sender_blocked_from_admin_commands(self):
        v = authorize("admin_refresh", None, admin_open_ids=ADMINS)
        assert not v.allowed

    def test_unknown_sender_not_promoted(self):
        # An open id that's not in admin list is treated as USER.
        v = authorize("admin_score", "ou_random_xyz", admin_open_ids=ADMINS)
        assert not v.allowed
        assert v.actor_role is CommandRole.USER

    def test_empty_admin_list_blocks_all_admin(self):
        v = authorize("admin_refresh", "ou_admin_1", admin_open_ids=[])
        assert not v.allowed

    def test_deny_message_does_not_leak_internals(self):
        msg = user_facing_deny_message("admin_refresh")
        assert "admin" not in msg.lower()
        assert "refresh" not in msg.lower()
        assert "internal" not in msg.lower()
        assert "权限" in msg or "账号" in msg

    def test_is_admin_property(self):
        v = authorize("today", "ou_admin_1", admin_open_ids=ADMINS)
        assert v.is_admin is True
        v = authorize("today", "ou_user_42", admin_open_ids=ADMINS)
        assert v.is_admin is False


# ---------------------------------------------------------------------------
# LLM Tool allowlist
# ---------------------------------------------------------------------------
class TestToolAllowlist:
    def test_user_tools_allowed(self):
        for tool in (
            "search_signals",
            "get_signal",
            "get_research",
            "generate_content",
            "get_user_preferences",
        ):
            assert tool_allowed_for_llm(tool), tool

    def test_admin_tools_not_allowed_for_llm(self):
        for tool in ("refresh", "score", "publish", "reject", "stats"):
            assert not tool_allowed_for_llm(tool), tool

    def test_unknown_tool_not_allowed(self):
        assert not tool_allowed_for_llm("hack_the_mainframe")
        assert not tool_allowed_for_llm("delete_everything")

    def test_admin_only_helper(self):
        assert tool_is_admin_only("publish")
        assert tool_is_admin_only("score")
        assert not tool_is_admin_only("search_signals")

    def test_allowlist_size_reasonable(self):
        # Sanity — guard against accidental mass-additions.
        assert 5 <= len(USER_TOOL_ALLOWLIST) <= 20


# ---------------------------------------------------------------------------
# Activation command
# ---------------------------------------------------------------------------
class TestActivationCommand:
    def test_is_activation(self):
        assert is_activation_command("activate")

    def test_other_commands_not_activation(self):
        assert not is_activation_command("today")
        assert not is_activation_command("admin_refresh")
        assert not is_activation_command("unknown")