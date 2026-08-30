"""Feishu App (event-subscription) outbound client — Phase 6 v2.0.

When the inbound event endpoint receives a user command, it must
**actively** call `POST /im/v1/messages` to send the reply to the
chat — Feishu's event callback response body does NOT itself cause
a message to be sent.

Per the Feishu Open API spec:
  * `POST /auth/v3/tenant_access_token/internal` returns a 2-hour
    `tenant_access_token`; we cache it and refresh lazily on expiry.
  * `POST /im/v1/messages?receive_id_type=...` sends one message to a
    chat or a user. Phase 23 v2.0 — supports `receive_id_type` ∈
    {"chat_id", "open_id", "union_id", "email"}. body shape:
        {
          "receive_id": "<oc_xxx | ou_xxx | ...>",
          "msg_type": "text" | "interactive" | "post",
          "content": "{\"text\":\"...\"}"  # string of JSON
        }
    Authorization header: `Bearer <tenant_access_token>`.

Reference:
  https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
  https://open.feishu.cn/document/server-docs/im-v1/message/create
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.utils import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://open.feishu.cn/open-apis"
_TOKEN_PATH = "/auth/v3/tenant_access_token/internal"
_MESSAGE_PATH = "/im/v1/messages"
_TOKEN_REFRESH_BUFFER_SEC = 300  # refresh 5 min before expiry


class FeishuAppError(RuntimeError):
    """Raised when the Feishu App API rejects or is unreachable."""


class FeishuAppClient:
    """Async client for the Feishu App (event-subscription) open API.

    The client holds a cached `tenant_access_token` and refreshes it
    lazily on expiry. Designed to be created once per FastAPI process
    and reused across requests — but safe to instantiate per-call too
    (the cache is per-instance).
    """

    def __init__(
        self,
        *,
        settings: Optional[Settings] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        base_url: str = _BASE_URL,
    ) -> None:
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=15.0)
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    @property
    def app_id(self) -> str:
        return (self.settings.feishu_app_id or "").strip()

    @property
    def app_secret(self) -> str:
        return (self.settings.feishu_app_secret or "").strip()

    @property
    def is_configured(self) -> bool:
        """Whether we have credentials for outbound App API calls."""
        return bool(self.app_id) and bool(self.app_secret)

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------
    async def _fetch_token(self) -> str:
        """POST /auth/v3/tenant_access_token/internal to get a new token."""
        if not self.is_configured:
            raise FeishuAppError(
                "feishu app not configured (set FEISHU_APP_ID + FEISHU_APP_SECRET)"
            )
        url = f"{self.base_url}{_TOKEN_PATH}"
        try:
            response = await self._http.post(
                url,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
        except httpx.HTTPError as exc:
            raise FeishuAppError(f"tenant_access_token request failed: {exc}") from exc
        if response.status_code != 200:
            raise FeishuAppError(
                f"tenant_access_token HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        if data.get("code") != 0:
            raise FeishuAppError(
                f"tenant_access_token rejected: code={data.get('code')} "
                f"msg={data.get('msg')}"
            )
        token = data.get("tenant_access_token")
        expire = int(data.get("expire") or 7200)
        if not token:
            raise FeishuAppError("tenant_access_token missing in response")
        self._token = token
        self._token_expires_at = time.time() + expire - _TOKEN_REFRESH_BUFFER_SEC
        logger.info(
            "feishu_app_token_refreshed",
            expires_in_sec=expire,
        )
        return token

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        return await self._fetch_token()

    async def get_token(self) -> str:
        """Public alias for `_get_token`.

        Sibling clients (Drive/Bitable in `content_client.py`) call this
        to share the same token cache instead of each maintaining their
        own. Exposed publicly so router / helper layers don't reach into
        a `_private` method.
        """
        return await self._get_token()

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------
    async def send_message(
        self,
        *,
        receive_id: str,
        msg_type: str,
        content: dict[str, Any],
        receive_id_type: str = "chat_id",
    ) -> dict[str, Any]:
        """POST /im/v1/messages to send one message.

        Phase 23 v2.0 — the parameter was renamed from ``chat_id`` to
        the semantically more accurate ``receive_id`` so the same
        method can target a chat (``oc_xxx``), an open user id
        (``ou_xxx``), a union id, or an email. ``receive_id_type``
        defaults to ``"chat_id"`` so existing callers keep working
        without changes.

        Args:
          receive_id: the target identifier — ``oc_xxx`` for chat,
            ``ou_xxx`` for a user's open_id, etc. Match the value
            passed to ``receive_id_type``.
          msg_type: one of ``"text"``, ``"interactive"``, ``"post"``.
          content: typed message body — Feishu expects this dict to
            be JSON-serialised into the request's ``content`` field
            as a STRING (not an object).
          receive_id_type: one of ``chat_id``, ``open_id``,
            ``union_id``, ``email``. Phase 23 — ``open_id`` is the
            typical choice for activation-code delivery and
            subscription renewal reminders because the
            ``Subscription.feishu_open_id`` row stores the user
            identifier (not a chat).

        Returns:
          The Feishu response body (a dict with `code`, `msg`, `data`).

        Raises:
          FeishuAppError: on HTTP failure, non-zero ``code``, or
            missing credentials / unsupported ``receive_id_type``.
        """
        if not receive_id:
            raise FeishuAppError("send_message: receive_id is empty")
        if receive_id_type not in {"chat_id", "open_id", "union_id", "email"}:
            raise FeishuAppError(
                f"send_message: unsupported receive_id_type {receive_id_type!r}"
            )
        if not self.is_configured:
            raise FeishuAppError(
                "feishu app not configured (set FEISHU_APP_ID + FEISHU_APP_SECRET)"
            )

        import json

        token = await self._get_token()
        url = f"{self.base_url}{_MESSAGE_PATH}?receive_id_type={receive_id_type}"
        body = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            response = await self._http.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise FeishuAppError(f"im/v1/messages request failed: {exc}") from exc
        if response.status_code != 200:
            raise FeishuAppError(
                f"im/v1/messages HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        if data.get("code") != 0:
            # — token might have just expired; force-refresh once and retry.
            if data.get("code") in (99991663, 99991664):  # token expired/invalid
                self._token = None
                token = await self._get_token()
                headers["Authorization"] = f"Bearer {token}"
                response = await self._http.post(url, json=body, headers=headers)
                data = response.json()
            if data.get("code") != 0:
                raise FeishuAppError(
                    f"im/v1/messages rejected: code={data.get('code')} "
                    f"msg={data.get('msg')}"
                )
        logger.info(
            "feishu_app_message_sent",
            receive_id_type=receive_id_type,
            receive_id=receive_id,
            msg_type=msg_type,
            message_id=(data.get("data") or {}).get("message_id"),
        )
        return data


__all__ = ["FeishuAppClient", "FeishuAppError"]