"""Phase 21 — single admin/auth dependency for all admin + internal endpoints.

Replaces three legacy duplicates that had drifted apart:

  - ``app/api/admin.py::_require_admin`` (admin secret OR Feishu open id)
  - ``app/api/admin.py::_require_webhook`` (X-Radar-Webhook only)
  - ``app/api/internal.py::_check_webhook_secret`` (X-Radar-Webhook only,
    copy-pasted to avoid the import cycle)

Accepted credentials (any one is sufficient; Feishu > Admin > Webhook
precedence so audit rows carry the strongest actor label):

  1. ``X-Feishu-Open-Id`` ∈ ``settings.admin_open_ids`` → returns the
     matched open_id.
  2. ``X-Radar-Admin-Secret`` matches ``settings.admin_api_secret``
     (SHA-256 + constant-time compare) → returns ``"secret"``.
  3. ``X-Radar-Webhook`` matches **either** ``settings.app_secret_key``
     **or** ``os.environ['RADAR_WEBHOOK_SECRET']`` (same hash) → returns
     ``"webhook"``.

Two env vars are accepted for the webhook on purpose:

- ``APP_SECRET_KEY`` — long-standing shared secret (read by Settings).
- ``RADAR_WEBHOOK_SECRET`` — injected into the n8n container via
  ``docker-compose.yml`` and referenced by workflow JSON as
  ``={{$env.RADAR_WEBHOOK_SECRET}}`` so n8n's HTTP Request nodes can
  authenticate. Either one works for the client.

Dev / local short-circuit: if every settings/env source is empty, the
caller is accepted and labelled ``"webhook"``. This matches the legacy
``_require_webhook`` behavior — ``tests/conftest.py`` clears
``APP_SECRET_KEY`` + ``RADAR_WEBHOOK_SECRET`` so the whole test suite
relies on this short-circuit.

Otherwise 401 with ``detail="admin credentials required"`` — deliberately
opaque so the endpoint doesn't leak which header was wrong.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


def require_admin(
    x_webhook: Optional[str] = Header(default=None, alias="X-Radar-Webhook"),
    x_admin_secret: Optional[str] = Header(
        default=None, alias="X-Radar-Admin-Secret"
    ),
    x_feishu_open_id: Optional[str] = Header(
        default=None, alias="X-Feishu-Open-Id"
    ),
    settings: Settings = Depends(get_settings),
) -> str:
    """Return the actor label for AuditLog rows, or raise 401.

    Three independent credentials are checked in precedence order —
    Feishu (strongest), admin secret, webhook (weakest). Dev short-
    circuit at the end if every source is unconfigured.
    """
    admins = settings.admin_open_ids or []
    if x_feishu_open_id and x_feishu_open_id in admins:
        return x_feishu_open_id

    admin_secret = settings.admin_api_secret
    if admin_secret and x_admin_secret and hmac.compare_digest(
        hashlib.sha256(x_admin_secret.encode()).hexdigest(),
        hashlib.sha256(admin_secret.encode()).hexdigest(),
    ):
        return "secret"

    # Webhook — accept *any* of the configured webhook sources. See the
    # module docstring for why two env vars are accepted.
    webhook_candidates: list[str] = [
        settings.app_secret_key,
        os.environ.get("RADAR_WEBHOOK_SECRET", ""),
    ]
    if x_webhook:
        for candidate in webhook_candidates:
            if candidate and hmac.compare_digest(
                hashlib.sha256(x_webhook.encode()).hexdigest(),
                hashlib.sha256(candidate.encode()).hexdigest(),
            ):
                return "webhook"

    # Dev / local — only when EVERY source is unconfigured.
    if not admins and not admin_secret and not any(webhook_candidates):
        return "webhook"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="admin credentials required",
    )
