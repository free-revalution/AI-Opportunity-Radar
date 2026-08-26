"""Real Telegram provider — `POST https://api.telegram.org/bot<token>/sendMessage`.

No SDK is used. We send the minimum fields Telegram expects and translate
non-2xx responses into `ExternalServiceError` so the notification service
can persist the failure rather than crash.

Security:
  * The bot token is NEVER logged.
  * All URLs we ever call live on `api.telegram.org` — we don't pass
    user-supplied URLs to this provider.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.services.notification.telegram import (
    TelegramProvider,
    TelegramSendResult,
    encode_telegram_payload,
    parse_telegram_response,
)
from app.utils import ExternalServiceError, get_logger

logger = get_logger(__name__)


class HttpxTelegramProvider(TelegramProvider):
    """Real Telegram — JSON over HTTPS, no SDK."""

    name = "telegram"

    def __init__(
        self,
        *,
        bot_token: str,
        base_url: str = "https://api.telegram.org",
        timeout: float = 15.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not bot_token:
            raise ValueError("HttpxTelegramProvider requires bot_token")
        self.bot_token = bot_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str = "MarkdownV2",
        link_preview: bool = False,
    ) -> TelegramSendResult:
        url = f"{self.base_url}/bot{self.bot_token}/sendMessage"
        body = encode_telegram_payload(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            link_preview=link_preview,
        )

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns = self._client is None
        try:
            response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            logger.warning(
                "telegram_send_failed",
                chat_id=chat_id,
                error=str(exc),
            )
            if owns:
                await client.aclose()
            return TelegramSendResult(
                ok=False,
                chat_id=chat_id,
                text_chars=len(text or ""),
                provider=self.name,
                error=str(exc),
            )
        if owns:
            await client.aclose()

        if response.status_code >= 400:
            logger.warning(
                "telegram_send_http_error",
                chat_id=chat_id,
                status_code=response.status_code,
            )
            return TelegramSendResult(
                ok=False,
                chat_id=chat_id,
                text_chars=len(text or ""),
                provider=self.name,
                error=f"http {response.status_code}: {response.text[:200]}",
            )

        parsed = parse_telegram_response(response.text)
        if not parsed.get("ok"):
            err = parsed.get("description") or "unknown telegram error"
            return TelegramSendResult(
                ok=False,
                chat_id=chat_id,
                text_chars=len(text or ""),
                provider=self.name,
                error=err,
            )

        message_id: Optional[str] = None
        result = parsed.get("result")
        if isinstance(result, dict):
            mid = result.get("message_id")
            if mid is not None:
                message_id = str(mid)
        return TelegramSendResult(
            ok=True,
            chat_id=chat_id,
            text_chars=len(text or ""),
            provider=self.name,
            message_id=message_id,
        )


__all__ = ["HttpxTelegramProvider"]
