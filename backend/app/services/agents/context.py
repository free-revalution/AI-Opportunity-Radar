"""Phase 16C — build a `VerticalContext` from a User row.

The user-preferences work in Phase 15A added 6 scalar columns
(`vertical / niche / platform / audience / tone / language`) +
`preferences_json` to the User model. `VerticalContext` (Phase 12F)
already exposes all 6 as named fields with sane defaults
(`platform="general"`, `tone="通俗"`, `language="zh"`).

This module is the one-line bridge — no business logic, no I/O, no
database. It exists so callers don't import the User model into the
agents package (which should stay vendor-agnostic for Phase 17 when
we add e-commerce / sales agents).

Out of scope:
  * `preferences_json` decoding — left for Phase 17 (LLM semantic
    re-rank uses it).
  * Cache / lazy load — User rows are small; one query per request.
"""

from __future__ import annotations

from app.models import User
from app.services.agents.base import VerticalContext


# Default values mirror `VerticalContext` field defaults. Kept as
# module-level constants so tests can assert the wire-up matches
# `agents/base.py` if those defaults ever change.
_DEFAULT_PLATFORM = "general"
_DEFAULT_AUDIENCE = ""
_DEFAULT_NICHE = ""
_DEFAULT_TONE = "通俗"
_DEFAULT_LANGUAGE = "zh"


def build_vertical_context(
    user: User,
    *,
    sender_open_id: str | None = None,
) -> VerticalContext:
    """Construct a `VerticalContext` from a `User` row.

    Args:
      user:           User row (must have phase-15A preference columns).
      sender_open_id: Optional override for the Feishu open_id. When
                      None we fall back to `user.feishu_open_id` (which
                      may itself be None for users created via the web
                      signup path — `VerticalContext.feishu_open_id`
                      is Optional, so passing None is safe).

    Returns a fully-populated `VerticalContext`. None / empty values
    on `User` are filled in with the module-level defaults so callers
    never have to special-case "preference not set".
    """
    return VerticalContext(
        user_id=user.id,
        feishu_open_id=sender_open_id or user.feishu_open_id,
        platform=user.platform or _DEFAULT_PLATFORM,
        audience=user.audience or _DEFAULT_AUDIENCE,
        niche=user.niche or _DEFAULT_NICHE,
        tone=user.tone or _DEFAULT_TONE,
        language=user.language or _DEFAULT_LANGUAGE,
    )


__all__ = [
    "build_vertical_context",
]
