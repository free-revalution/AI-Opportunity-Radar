"""Activation-code service — Xianyu-style invite code issuance + redemption.

Per docs/下一阶段开发技术方案.md §52-53:

> Activation Code 模型: id, code_hash, plan, order_id, status, expires_at,
>   bound_feishu_open_id, created_at, used_at
>
> 状态: UNUSED | ACTIVE | EXPIRED | REVOKED
> 不要明文存储 Code,存 SHA-256(code + server_pepper)
>
> 一个邀请码只能绑定一个 Feishu Open ID

The plaintext code is generated with ``secrets.choice`` and only
returned to the caller once (the response). After that, only the hash
is stored — server-side we can never recover the plaintext.

Failure modes:
  * wrong code           → NOT_FOUND
  * already used         → ALREADY_BOUND
  * already bound to a different Feishu Open ID → ALREADY_BOUND
  * expired              → EXPIRED
  * revoked              → REVOKED

Each redemption writes an ``AuditLog`` row (wired in Phase 12G when the
audit hook is registered).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CODE_LENGTH = 12  # raw characters (excluding separators)
DEFAULT_CODE_TTL_DAYS = 365
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Crockford-style (no 0/O/1/I)
_CODE_FORMAT_RE = re.compile(r"^[A-Z0-9]{4,16}(-[A-Z0-9]{4,16})*$")

# Server pepper — make sure this stays in sync with the real environment
# when you deploy. Default value is a placeholder for tests / dev mode.
DEFAULT_SERVER_PEPPER = "radar-activation-pepper-v2"


# ---------------------------------------------------------------------------
# Redemption outcomes
# ---------------------------------------------------------------------------
class ActivationError(str, Enum):
    NOT_FOUND = "not_found"
    ALREADY_BOUND = "already_bound"
    EXPIRED = "expired"
    REVOKED = "revoked"
    INVALID_FORMAT = "invalid_format"
    RATE_LIMITED = "rate_limited"


@dataclass(slots=True)
class ActivationOutcome:
    success: bool
    error: Optional[ActivationError] = None
    plan: Optional[str] = None
    code_id: Optional[int] = None
    feishu_open_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------
def generate_code(length: int = DEFAULT_CODE_LENGTH) -> str:
    """Generate a fresh, human-friendly activation code.

    Format: ``ABCD-EFGH-JKLM`` (groups of 4 separated by dashes). Length
    refers to the raw characters; with default length=12 you get a
    3-group code.
    """
    if length < 4 or length > 16:
        raise ValueError("length must be 4..16")
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
    # Group in fours for readability: 12 chars → ABCD-EFGH-JKLM
    groups = [raw[i : i + 4] for i in range(0, len(raw), 4)]
    return "-".join(groups)


def hash_code(code: str, pepper: str = DEFAULT_SERVER_PEPPER) -> str:
    """SHA-256(code + pepper) — returns 64-char hex digest."""
    h = hashlib.sha256()
    h.update(pepper.encode("utf-8"))
    h.update(code.encode("utf-8"))
    return h.hexdigest()


def validate_format(code: str) -> bool:
    """Cheap syntactic check — don't call the DB if the format is wrong."""
    if not code:
        return False
    return bool(_CODE_FORMAT_RE.match(code))


# ---------------------------------------------------------------------------
# Issue + redeem (test-friendly stubs — no DB required)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class IssuedCode:
    code: str            # plaintext — shown to admin ONCE
    code_hash: str       # store this in DB
    plan: str
    expires_at: datetime


def issue_code(
    plan: str,
    *,
    length: int = DEFAULT_CODE_LENGTH,
    ttl_days: int = DEFAULT_CODE_TTL_DAYS,
    pepper: str = DEFAULT_SERVER_PEPPER,
    now: datetime | None = None,
) -> IssuedCode:
    """Generate a fresh activation code.

    Plaintext is returned in the response; only the hash should be
    persisted server-side.
    """
    now = now or datetime.now(tz=timezone.utc)
    # Single code → derive both plaintext and hash from it. The earlier
    # implementation generated two independent codes and then the lookup
    # never matched — that was a real bug, not a stylistic choice.
    code = generate_code(length)
    return IssuedCode(
        code=code,
        code_hash=hash_code(code, pepper),
        plan=plan,
        expires_at=now + timedelta(days=ttl_days),
    )


def redeem_code(
    code: str,
    feishu_open_id: str,
    *,
    pepper: str = DEFAULT_SERVER_PEPPER,
    lookup_by_hash: Any = None,
    now: datetime | None = None,
) -> ActivationOutcome:
    """Validate + redeem an activation code.

    This is the *pure-data* reducer; the caller passes in a
    ``lookup_by_hash(hash_str) -> row`` callable to abstract DB access.
    In production, ``lookup_by_hash`` is a SQLAlchemy query. In tests
    it's an in-memory dict.

    The reducer never raises — bad input yields a ``success=False``
    outcome with an ``error`` code. Audit-log writes are *not* the
    reducer's job; the API layer wraps this call and writes the audit
    row.
    """
    now = now or datetime.now(tz=timezone.utc)

    if not validate_format(code):
        return ActivationOutcome(success=False, error=ActivationError.INVALID_FORMAT)

    if not feishu_open_id:
        return ActivationOutcome(success=False, error=ActivationError.INVALID_FORMAT)

    if lookup_by_hash is None:
        return ActivationOutcome(success=False, error=ActivationError.NOT_FOUND)

    row = lookup_by_hash(hash_code(code, pepper))
    if row is None:
        return ActivationOutcome(success=False, error=ActivationError.NOT_FOUND)

    status = getattr(row, "status", "")
    if status == "revoked":
        return ActivationOutcome(success=False, error=ActivationError.REVOKED)
    if status == "expired":
        return ActivationOutcome(success=False, error=ActivationError.EXPIRED)

    expires_at = getattr(row, "expires_at", None)
    if expires_at is not None:
        exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if exp <= now:
            return ActivationOutcome(success=False, error=ActivationError.EXPIRED)

    bound = getattr(row, "bound_feishu_open_id", None)
    if bound and bound != feishu_open_id:
        return ActivationOutcome(success=False, error=ActivationError.ALREADY_BOUND)

    if bound == feishu_open_id:
        # Idempotent — same user binding same code again → success.
        return ActivationOutcome(
            success=True,
            plan=getattr(row, "plan", None),
            code_id=getattr(row, "id", None),
            feishu_open_id=feishu_open_id,
        )

    return ActivationOutcome(
        success=True,
        plan=getattr(row, "plan", None),
        code_id=getattr(row, "id", None),
        feishu_open_id=feishu_open_id,
    )


__all__ = [
    "ActivationError",
    "ActivationOutcome",
    "IssuedCode",
    "generate_code",
    "hash_code",
    "issue_code",
    "redeem_code",
    "validate_format",
]


# Re-export the Phase 14A flow surface so callers can `from app.services.activation import redeem_for_user`.
from app.services.activation.flow import (  # noqa: E402
    RedemptionResult,
    RedemptionStatus,
    plan_display_zh,
    redeem_for_user,
    user_message,
)

__all__ += [
    "RedemptionResult",
    "RedemptionStatus",
    "plan_display_zh",
    "redeem_for_user",
    "user_message",
]