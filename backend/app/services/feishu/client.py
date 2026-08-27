"""Real Feishu custom-robot provider — `POST <webhook_url>` with JSON body.

We don't use an SDK; we POST JSON with httpx. The webhook URL is a
literal endpoint already containing the robot's token — we never
inspect the URL or split the token out.

Optional "加签" (signed) mode is handled here:
  * If `signing_secret` is set, we attach `timestamp` + `sign` to the
    JSON body (Feishu requires them in the body, NOT in headers).
  * `sign_feishu_payload(...)` does the HMAC-SHA256 dance.

A non-2xx response (or a body with `{"StatusCode": ..., "msg": "..."}`
other than success) is translated to `ExternalServiceError` so the
notification / bot service can record a failure rather than crash.

Security:
  * The signing secret is NEVER logged.
  * We never let the caller redirect the URL — it's bound at construction.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from app.services.feishu.base import (
    FeishuCard,
    FeishuProvider,
    FeishuSendResult,
    sign_feishu_payload,
)
from app.utils import ExternalServiceError, get_logger

logger = get_logger(__name__)

# Feishu's success code is `StatusCode == 0` (note the capital S, single value).
_SUCCESS_STATUS_CODE = 0


class HttpxFeishuProvider(FeishuProvider):
    """Real Feishu custom-robot client."""

    name = "feishu"

    def __init__(
        self,
        *,
        webhook_url: str,
        signing_secret: str = "",
        timeout: float = 15.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not webhook_url:
            raise ValueError("HttpxFeishuProvider requires webhook_url")
        # Defensive: the URL must look like a Feishu open-apis endpoint.
        # We don't *parse* it — but if it doesn't contain `open.feishu.cn`
        # or `open.larksuite.com`, that's almost certainly a mistake.
        if "open.feishu.cn" not in webhook_url and "open.larksuite.com" not in webhook_url:
            raise ValueError(
                "feishu webhook_url does not look like a Feishu endpoint: "
                f"{webhook_url[:60]}…"
            )
        self.webhook_url = webhook_url
        self.signing_secret = signing_secret
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_body(self, card: FeishuCard) -> dict[str, Any]:
        body = dict(card.body)
        if self.signing_secret:
            sig = sign_feishu_payload(secret=self.signing_secret)
            body.update(sig)
        return body

    def _parse_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Decode a Feishu response into the canonical success/failure shape.

        Feishu's success envelope is `{"StatusCode": 0, "msg": "success",
        "data": {...}}`. Anything else is a failure we surface.
        """
        if not isinstance(payload, dict):
            return {"ok": False, "error": "non_dict_response"}
        if payload.get("StatusCode") == _SUCCESS_STATUS_CODE:
            return {"ok": True, "data": payload.get("data") or {}}
        msg = payload.get("msg") or payload.get("message") or "feishu_rejected"
        code = payload.get("StatusCode")
        return {
            "ok": False,
            "error": f"{code}:{msg}" if code is not None else str(msg),
            "raw": payload,
        }

    async def send_card(self, card: FeishuCard) -> FeishuSendResult:
        body = self._build_body(card)
        title = card.title or ""

        try:
            client = await self._get_client()
            response = await client.post(self.webhook_url, json=body)
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"feishu request failed: {exc}", provider=self.name
            ) from exc

        # 2xx — inspect body for Feishu's StatusCode contract.
        if 200 <= response.status_code < 300:
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = {
                    "ok": False,
                    "error": f"invalid_json: status={response.status_code}",
                }
            verdict = self._parse_response(payload)
            if verdict.get("ok"):
                return FeishuSendResult(
                    ok=True,
                    title=title,
                    body_chars=len(json.dumps(body, ensure_ascii=False)),
                    provider=self.name,
                    response={"data": verdict.get("data") or {}},
                )
            raise ExternalServiceError(
                f"feishu rejected card: {verdict.get('error')}",
                provider=self.name,
            )

        # Non-2xx — surface as ExternalServiceError so callers can record.
        raise ExternalServiceError(
            f"feishu HTTP {response.status_code}: {response.text[:200]}",
            provider=self.name,
        )


__all__ = ["HttpxFeishuProvider"]