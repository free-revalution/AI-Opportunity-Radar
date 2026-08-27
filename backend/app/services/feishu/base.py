"""Feishu (Lark) custom-robot provider — Phase 2 of v2.0.

The notification service / content pipeline never speaks HTTP to Feishu
directly. Instead it calls `FeishuProvider.send_card(...)`, behind a
small async interface. This keeps the boundary swappable and lets the
test suite run against a deterministic in-memory mock.

The "custom robot" is the simplest Feishu integration:
  * User creates a custom robot in a Feishu group
  * Feishu returns a Webhook URL containing a token
  * We `POST <webhook_url>` with a JSON body — `interactive` cards
    are the common format and what we ship here.

Optional "加签" (signed) mode:
  * Group admin enables signing on the robot
  * Feishu gives us a `secret` string
  * Each request must include `timestamp` + `sign` in the body
  * `sign = base64(HMAC-SHA256(key=secret, msg=f"{timestamp}\\n{secret}"))`

This module owns the signature algorithm and the factory. The HTTP
implementation lives in `client.py`; the in-memory mock lives in
`mock_client.py`.

Selection (see `build_feishu_provider`):
  * mock   — offline, deterministic, default when no URL is configured
  * httpx  — real `POST <webhook_url>` against Feishu's open API
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class FeishuCard:
    """An interactive card payload that will be sent (or accepted in mock).

    `body` MUST be a `msg_type == "interactive"` payload — Feishu's API
    expects `{"msg_type": "interactive", "card": {...}}`. We pre-shape
    it on construction so the HTTP layer can just JSON-encode and ship.
    """

    body: dict[str, Any]
    title: str = ""

    @classmethod
    def from_card(cls, *, card: dict[str, Any], title: str = "") -> "FeishuCard":
        """Wrap a `card` dict in the canonical Feishu envelope."""
        header = card.get("header") or {}
        if title:
            header.setdefault("title", {"tag": "plain_text", "content": title})
            card = {**card, "header": header}
        return cls(body={"msg_type": "interactive", "card": card}, title=title)


@dataclass(slots=True)
class FeishuSendResult:
    """Outcome of a single `send_card()` call.

    Mirrors the Telegram provider's result shape so we can reuse the
    existing notification service plumbing if we ever want to.
    """

    ok: bool
    title: str
    body_chars: int
    provider: str
    error: Optional[str] = None
    response: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------
class FeishuProvider(ABC):
    """Async boundary for sending Feishu custom-robot cards."""

    name: str = "abstract"

    @abstractmethod
    async def send_card(self, card: FeishuCard) -> FeishuSendResult:
        """Deliver one card. Must not raise — translate to result."""


# ---------------------------------------------------------------------------
# Signature helper (custom-robot "加签" mode)
# ---------------------------------------------------------------------------
def sign_feishu_payload(
    *,
    secret: str,
    timestamp: Optional[int] = None,
) -> dict[str, str]:
    """Compute the `timestamp` + `sign` fields for a signed custom robot.

    Returns a dict with two string keys (`timestamp`, `sign`). The caller
    merges this into the outgoing JSON body. We do NOT add the keys to
    the body here because the same dict can be used to construct both
    the body (interactive card) and a separate `text`-mode message.

    Per Feishu docs:
        string_to_sign = f"{timestamp}\\n{secret}"
        sign = base64(hmac.new(secret.encode(), string_to_sign.encode(),
                              hashlib.sha256).digest())
    """
    if not secret:
        raise ValueError("sign_feishu_payload requires a non-empty secret")
    ts = int(time.time()) if timestamp is None else int(timestamp)
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    return {"timestamp": str(ts), "sign": base64.b64encode(digest).decode("utf-8")}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_feishu_provider(settings, *, prefer: Optional[str] = None):
    """Return the configured provider, falling back to the mock.

    Selection rules:
      * `MOCK_EXTERNAL_SERVICES=true`   → mock
      * `prefer="mock"`                 → mock
      * no `feishu_webhook_url` set     → mock
      * otherwise                       → httpx provider
    """
    if getattr(settings, "mock_external_services", False):
        from app.services.feishu.mock_client import MockFeishuProvider

        return MockFeishuProvider()
    if (prefer or "").lower() == "mock":
        from app.services.feishu.mock_client import MockFeishuProvider

        return MockFeishuProvider()
    url = getattr(settings, "feishu_webhook_url", "") or ""
    if not url:
        from app.services.feishu.mock_client import MockFeishuProvider

        return MockFeishuProvider()

    from app.services.feishu.client import HttpxFeishuProvider

    return HttpxFeishuProvider(
        webhook_url=url,
        signing_secret=getattr(settings, "feishu_webhook_secret", "") or "",
        timeout=float(getattr(settings, "feishu_timeout", 15.0)),
    )


__all__ = [
    "FeishuCard",
    "FeishuProvider",
    "FeishuSendResult",
    "build_feishu_provider",
    "sign_feishu_payload",
]