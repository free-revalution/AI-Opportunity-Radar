"""Feishu inbound event endpoint — Phase 6 of v2.0.

Mounted at `/api/feishu/event`. Receives event-subscription callbacks
from Feishu and dispatches them to `FeishuCommandRouter`.

Three Feishu variants we accept:

  1. URL verification (handshake):
        {"challenge": "..."} → echo `{"challenge": "..."}` back
  2. Unencrypted message events:
        {"header": {...}, "event": {...}} → parse + route + reply
  3. Encrypted events (`{"encrypt": "..."}`) → Phase 6 stub. We
     log + return 200 OK so Feishu doesn't retry.

Authentication uses the Verification Token in the body
(`settings.feishu_verification_token`). When unset, the endpoint is
open (matches the existing internal webhook dev-mode behavior).

Replying to the user requires actively calling Feishu's App open API
(`/im/v1/messages`) — the event callback's response body itself does
not cause a message to be sent. See `FeishuAppClient`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.config import get_settings
from app.services.feishu.app_client import FeishuAppClient, FeishuAppError
from app.services.feishu.content_client import (
    FeishuBitableClient,
    FeishuDriveClient,
)
from app.services.feishu.inbound import (
    CommandReply,
    FeishuCommandRouter,
    FeishuEvent,
    parse_command,
    parse_event,
    verify_event,
)
from app.utils import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _feishu_card(reply: CommandReply) -> dict[str, Any]:
    """Wrap a `CommandReply` in a Feishu interactive card payload.

    Feishu supports a rich-card schema (`msg_type=interactive`). When
    `reply.card` is set we use it directly; otherwise we build a
    minimal text-only card so the user sees the reply in the right
    visual treatment.

    Per the Feishu Open API spec, this matches what their send API
    expects and is what the bot would echo in the response body.
    """
    if reply.card:
        return {"msg_type": "interactive", "card": reply.card}

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": reply.text,
                    },
                }
            ],
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "AI 机会雷达",
                },
                "template": "blue",
            },
        },
    }


def _build_content_clients(
    settings: Any,
    app_client: FeishuAppClient,
) -> tuple[FeishuDriveClient, FeishuBitableClient, FeishuBitableClient]:
    """Construct the three Phase 7 content clients sharing `app_client`'s
    token cache.
    """
    drive = FeishuDriveClient(app_client=app_client, settings=settings)
    bitable_digest = FeishuBitableClient(
        app_client=app_client,
        settings=settings,
        token_setting="feishu_bitable_app_token",
        table_name="Daily Digest",
    )
    bitable_ops = FeishuBitableClient(
        app_client=app_client,
        settings=settings,
        token_setting="feishu_bitable_opportunities_app_token",
        table_name="Opportunities",
    )
    return drive, bitable_digest, bitable_ops


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post(
    "/event",
    summary="Feishu event-subscription callback",
)
async def handle_feishu_event(request: Request) -> dict[str, Any]:
    """Handle one inbound Feishu event.

    Returns:
      * `{"challenge": "..."}` for URL verification handshake.
      * `{"code": 0, "msg": "ok", "data": {...}}` for message events
        (Feishu expects this shape on success).
      * `{}` for events we don't handle yet (encrypted path).

    Feishu retries on non-200 responses, so we accept-and-log rather
    than 4xx-ing on unknown event types.
    """
    settings = get_settings()

    # — Parse body. We need the raw dict for token verification, so
    # parse manually instead of relying on Pydantic model parsing.
    try:
        body: dict[str, Any] = await request.json()
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON body",
        )

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="body must be a JSON object",
        )

    headers = dict(request.headers)

    # 1. — Signature check (Verification Token). Always on; open in dev
    # only when `feishu_verification_token` is empty (see
    # `verify_event`).
    if not verify_event(headers=headers, body=body, settings=settings):
        logger.warning("feishu_event_signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid verification token",
        )

    # 2. — Parse event.
    parsed = parse_event(body)

    # 2a. URL verification handshake — echo back the challenge.
    if isinstance(parsed, dict) and "challenge" in parsed:
        logger.info(
            "feishu_event_challenge",
            challenge_len=len(parsed["challenge"] or ""),
        )
        return {"challenge": parsed["challenge"]}

    # 2b. Encrypted / unknown event types — ack so Feishu stops
    # retrying. Phase 6.x will decode the encrypted envelope.
    if parsed is None:
        logger.info(
            "feishu_event_ignored",
            event_type=str(body.get("header", {}).get("event_type") or "unknown"),
        )
        return {}

    # 3. — Message event: route the command.
    event: FeishuEvent = parsed
    if not event.is_command:
        logger.info(
            "feishu_event_non_command",
            chat_id=event.chat_id,
            text_preview=event.text[:80],
        )
        # — Ack without a reply. Feishu will only retry on non-200.
        return {"code": 0, "msg": "ok"}

    # — Single `FeishuAppClient` instance per request so all Phase 7
    # sibling clients (Drive + Bitable) share its token cache.
    app_client = FeishuAppClient(settings=settings)
    drive_client, bitable_digest_client, bitable_ops_client = _build_content_clients(
        settings=settings, app_client=app_client
    )
    # — Phase 15C v2.0: Redis client for paywall quota counters + the
    # activation rate-limit guard. Resolved lazily — if Redis is down
    # the singleton returns None and the router falls open.
    from app.services.redis_client import get_redis

    redis_client = await get_redis()
    try:
        command = parse_command(event.text)
        router_instance = FeishuCommandRouter(
            settings=settings,
            drive_client=drive_client,
            bitable_digest_client=bitable_digest_client,
            bitable_ops_client=bitable_ops_client,
            redis_client=redis_client,
        )
        # Phase 14A — commands like `/activate` need the sender's Open
        # ID. The router accepts it via a transient attribute so the
        # command AST itself stays pure-data.
        router_instance._sender_open_id = event.sender_open_id
        reply = await router_instance.route(command)
    except Exception as exc:  # noqa: BLE001 — log + ack so Feishu doesn't retry.
        logger.error(
            "feishu_command_failed",
            command=command.kind,
            error=str(exc),
            exc_info=True,
        )
        # — Still ack so Feishu doesn't retry the same message; the
        # operator sees the failure in logs + Prometheus metrics.
        await app_client.aclose()
        return {"code": 0, "msg": "ok"}

    logger.info(
        "feishu_command_routed",
        command=command.kind,
        chat_id=event.chat_id,
        sender=event.sender_open_id,
        metadata=reply.metadata,
    )

    # — 4. Send the reply via Feishu's App API. The event callback
    # response body does NOT itself cause a message to be sent —
    # we have to call /im/v1/messages explicitly. Failures here
    # are logged but we still return 200 so Feishu doesn't retry.
    card_payload = _feishu_card(reply)
    try:
        await app_client.send_message(
            chat_id=event.chat_id,
            msg_type=card_payload["msg_type"],
            content=card_payload["card"],
        )
    except FeishuAppError as exc:
        logger.error(
            "feishu_reply_send_failed",
            command=command.kind,
            chat_id=event.chat_id,
            error=str(exc),
        )
    finally:
        # — Close the shared httpx client AFTER the send_message
        # attempt (success or failure). Doing this in the previous
        # `finally` block caused Phase 7 reply sends to fail with
        # `Cannot send a request, as the client has been closed`
        # because the router's Drive/Bitable siblings already use
        # the same httpx client during `route()` and the client
        # was being torn down before the explicit send_message here.
        await app_client.aclose()

    return {"code": 0, "msg": "ok"}


__all__ = ["router"]
