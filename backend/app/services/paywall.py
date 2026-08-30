"""Minimal paywall — MVP bypass.

simplify §10: the 5 MVP bot commands (/today /run /status /sources /help)
do not enforce per-user quota. All verdicts return ``allowed=True`` with
plan="free".

This file replaces the 900-line ``app.services.subscriptions.paywall``
plus the data classes that used to live in
``app.services.subscriptions.__init__``. It keeps only:

  * :class:`PaywallVerdict` — the dataclass the dispatcher / tests
    construct when stubbing paywall in offline tests.
  * :func:`command_to_feature` — returns ``None`` for every command so
    :func:`app.services.feishu.inbound._command_quota_type` short-
    circuits the gate.
  * No-op stubs for ``check_access``, ``record_consumption``,
    ``record_view_top_signals``.

If a future phase wants per-user quotas back, restore the real
implementation from git history (HEAD:backend/app/services/subscriptions/).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class PaywallVerdict:
    """Outcome of a paywall check — always allowed for MVP."""

    allowed: bool = True
    plan: str = "free"
    expires_at: Optional[Any] = None
    quota_type: str = "bypass"
    quota_limit: int = 0
    quota_used: int = 0
    deny_reason: Optional[str] = None
    deny_message_zh: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# All MVP commands bypass paywall. Returning None here makes
# ``_command_quota_type`` skip the gate entirely.
COMMAND_TO_FEATURE: dict[str, str] = {}


def command_to_feature(kind: str) -> Optional[str]:
    """Map a ``BotCommand.kind`` to a quota feature — always ``None``."""
    return COMMAND_TO_FEATURE.get(kind)


async def check_access(*_args: Any, **_kwargs: Any) -> PaywallVerdict:
    """No-op — always allowed."""
    return PaywallVerdict()


async def record_consumption(*_args: Any, **_kwargs: Any) -> None:
    """No-op — MVP does not count usage."""


async def record_view_top_signals(*_args: Any, **_kwargs: Any) -> None:
    """No-op — MVP does not record distinct viewed signals."""