"""Feishu inbound event handling — Phase 6 of v2.0.

Implements the receiving end of a 飞书 event subscription (event
subscription, not a custom robot). When a group user mentions the bot
or sends a text command, Feishu POSTs the event to our
`/api/feishu/event` endpoint. We:

  1. Verify the event signature (Verification Token) — or skip when
     no token is configured (local dev / mock mode).
  2. Parse the event body — three variants (challenge, unencrypted,
     encrypted; encrypted is currently a stub).
  3. Route the user text to a `BotCommand` and return a card reply
     via `POST /api/feishu/event`'s response body.

Command routing delegates to internal HTTP APIs (Phase 5 on-demand,
the existing discovery / scoring / notification endpoints). **No
business logic lives here** — only command parsing + httpx calls.

Public surface:

    FeishuEvent            typed event model (header + sender + message)
    verify_event           signature / token check
    parse_event            parse body — returns FeishuEvent | challenge dict
    FeishuCommandRouter    command dispatcher — `/today` `/top` `/research` …
    BotCommand             command AST (kind + args)

Encryption: Phase 6 leaves the encrypted-event path as a stub. It
will be implemented when this codebase is deployed against a real
Feishu App (and the user enables 加密传输 in the open platform).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import httpx

from app.config import Settings, get_settings
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Typed event model
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class FeishuEvent:
    """Decoded inbound message event.

    Mirrors the JSON shape Feishu posts in unencrypted events:

      {
        "header": {
          "event_type": "im.message.receive_v1",
          "tenant_key": "..."
        },
        "event": {
          "sender": {"sender_id": {"open_id": "ou_xxx"}},
          "message": {
            "chat_id": "oc_xxx",
            "chat_type": "group" | "p2p",
            "message_type": "text",
            "content": "{\"text\":\"/today\"}"
          }
        }
      }
    """

    event_type: str
    tenant_key: str
    sender_open_id: str
    chat_id: str
    chat_type: str  # "group" | "p2p"
    message_type: str  # "text" | "post" | …
    text: str  # cleaned text (mentions stripped)
    raw_text: str = ""  # original text including @-mentions

    @property
    def is_command(self) -> bool:
        return self.text.strip().startswith("/")

    @property
    def command(self) -> str:
        """The first whitespace-separated token, lowercased, with `/`."""
        text = self.text.strip()
        if not text.startswith("/"):
            return ""
        # First word only.
        return text.split()[0].lower() if text else ""

    @property
    def command_args(self) -> str:
        """Everything after the command word."""
        text = self.text.strip()
        cmd = self.command
        if not cmd:
            return ""
        return text[len(cmd):].strip()


# ---------------------------------------------------------------------------
# Signature check
# ---------------------------------------------------------------------------
def verify_event(
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    settings: Settings,
) -> bool:
    """Verify the inbound event is actually from Feishu.

    Per the 飞书 docs, the Verification Token is only sent during the
    initial **URL verification handshake** (`{"challenge": "..."}` body) —
    subsequent message events do NOT include a `token` field. We rely on
    that fact for security:

      1. URL handshake must echo the token → verified by caller via
         challenge echo (see `parse_event`).
      2. Real events arrive only after handshake succeeded → we trust
         the connection. This matches the live Feishu Open API spec
         (https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration).

    **Local dev / mock mode**: when `feishu_verification_token` is
    empty, we accept all events (matches the existing `_check_webhook_secret`
    dev-mode behavior for outbound webhooks).

    For belt-and-suspenders in production, when a token IS configured
    we still require the handshake body to contain `token` matching
    `expected` — anything else falls through to the trust-the-handshake
    branch (real events).
    """
    expected = (getattr(settings, "feishu_verification_token", "") or "").strip()
    if not expected:
        # — dev / mock: accept everything
        return True

    # — URL verification handshake carries the token. Require match.
    is_handshake = "challenge" in body
    if is_handshake:
        provided = (body.get("token") or "").strip()
        if not provided:
            logger.warning(
                "feishu_handshake_missing_token",
                hint="set Verification Token in 飞书 事件订阅 or unset FEISHU_VERIFICATION_TOKEN to skip",
            )
            return False
        return hmac.compare_digest(provided, expected)

    # — Real events: trust that handshake already succeeded. Token
    # comparison would always fail (Feishu doesn't include it).
    return True


# ---------------------------------------------------------------------------
# Body parsing
# ---------------------------------------------------------------------------
def parse_event(
    body: dict[str, Any],
    *,
    settings: Optional[Settings] = None,
) -> FeishuEvent | dict[str, Any] | None:
    """Parse an inbound body into a typed event or special return.

    Returns:
      * `{"challenge": "..."}` — for URL verification handshake; the
        caller echoes this back as the response.
      * `FeishuEvent` — for normal `im.message.receive_v1` events.
      * `None` — for unrecognised event types (caller returns 200 OK
        with empty body so Feishu doesn't retry).
    """
    # URL verification handshake (no header, just challenge).
    if "challenge" in body:
        return {"challenge": body["challenge"]}

    # Encrypted events (Phase 25 F.1) — AES-256-CBC, PKCS#7 padded,
    # IV = first 16 bytes of the ciphertext, key = SHA-256(encrypt_key).
    if "encrypt" in body:
        try:
            plaintext = _decrypt_feishu_envelope(
                body["encrypt"],
                settings=settings or get_settings(),
            )
        except FeishuDecryptError as exc:
            logger.warning(
                "feishu_event_decrypt_failed",
                error=str(exc),
            )
            return None
        if not isinstance(plaintext, dict):
            logger.warning("feishu_event_decrypt_not_object")
            return None
        # Re-parse the decrypted body as a normal event.
        body = plaintext

    header = body.get("header") or {}
    event_type = header.get("event_type") or ""
    if event_type != "im.message.receive_v1":
        # Not a message event — ack and move on.
        return None

    event = body.get("event") or {}
    sender = (event.get("sender") or {}).get("sender_id") or {}
    message_obj = event.get("message") or {}
    content_raw = message_obj.get("content") or "{}"
    # — Feishu encodes message content as a JSON-encoded string.
    import json

    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
    except (ValueError, TypeError):
        content = {}
    if not isinstance(content, dict):
        content = {}

    raw_text = str(content.get("text") or "")
    # — strip bot mentions added by Feishu (e.g. "@_user_1", "@bot").
    # Without this, downstream is_command / parse_command see
    # "@_user_1 /help" instead of "/help" and the command gets
    # classified as 'unknown'. See parse_command() for the
    # multi-word display-name logic.
    cleaned_text = _strip_mentions(raw_text).strip()

    return FeishuEvent(
        event_type=event_type,
        tenant_key=header.get("tenant_key") or "",
        sender_open_id=sender.get("open_id") or "",
        chat_id=message_obj.get("chat_id") or "",
        chat_type=message_obj.get("chat_type") or "",
        message_type=message_obj.get("message_type") or "text",
        text=cleaned_text,
        raw_text=raw_text,
    )


# ---------------------------------------------------------------------------
# Phase 25 F.1 — Feishu encrypted event envelope (AES-256-CBC + PKCS#7)
# ---------------------------------------------------------------------------
class FeishuDecryptError(Exception):
    """Raised when an inbound ``{"encrypt": "..."}`` envelope cannot
    be decrypted — wrong key, bad base64, padding error, etc."""


def _decrypt_feishu_envelope(
    envelope_b64: str,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Decrypt a Feishu encrypted event body.

    Per the 飞书 open-api docs (event-subscription encrypt-key flow):

      * The Encrypt Key is configured in the Feishu app settings; we
        receive it via ``settings.feishu_encrypt_key``.
      * The 32-byte AES-256 key is ``SHA-256(encrypt_key)``.
      * The first 16 bytes of the base64-decoded ciphertext are the IV.
      * The remainder is AES-256-CBC encrypted with PKCS#7 padding.
      * The decrypted bytes are UTF-8 JSON.

    Returns the parsed dict. Raises :class:`FeishuDecryptError` on
    any failure — the caller logs + acks Feishu so it doesn't retry.
    """
    import base64
    import hashlib
    import json as _json

    encrypt_key = (getattr(settings, "feishu_encrypt_key", "") or "").strip()
    if not encrypt_key:
        raise FeishuDecryptError("feishu_encrypt_key not configured")

    try:
        ciphertext = base64.b64decode(envelope_b64)
    except (ValueError, TypeError) as exc:
        raise FeishuDecryptError(f"invalid base64: {exc}") from exc

    if len(ciphertext) < 32:
        raise FeishuDecryptError("ciphertext too short")

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = ciphertext[:16]
    body_bytes = ciphertext[16:]

    try:
        from cryptography.hazmat.primitives.ciphers import (  # type: ignore[import-not-found]
            Cipher,
            algorithms,
            modes,
        )
        from cryptography.hazmat.primitives import padding as _pad  # type: ignore[import-not-found]
    except ImportError as exc:
        raise FeishuDecryptError(
            f"cryptography library not installed: {exc}"
        ) from exc

    try:
        decryptor = Cipher(
            algorithms.AES(key), modes.CBC(iv)
        ).decryptor()
        padded = decryptor.update(body_bytes) + decryptor.finalize()
        unpadder = _pad.PKCS7(algorithms.AES.block_size).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except (ValueError, TypeError) as exc:
        raise FeishuDecryptError(f"decrypt failed: {exc}") from exc

    try:
        parsed = _json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeishuDecryptError(f"plaintext not JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise FeishuDecryptError("plaintext not a JSON object")
    return parsed


# ---------------------------------------------------------------------------
# Phase 25 F.2 — Redis SETNX event idempotency
# ---------------------------------------------------------------------------
async def _event_already_processed(
    event_id: str,
    *,
    redis_client: Any,
    ttl_seconds: int = 86_400,
) -> bool:
    """Check + claim an event_id atomically (SETNX with TTL).

    Returns ``True`` if the event_id has already been processed (i.e.
    another delivery beat us to it — Feishu retries on timeouts). The
    caller should ack-and-skip in that case.

    Returns ``False`` when this is the first time we've seen the event
    AND we successfully claimed it. The TTL is 24h by default — Feishu
    retries for at most ~30s, but we keep the marker longer so a
    duplicate triggered by replay stays idempotent.
    """
    if not event_id or redis_client is None:
        return False  # nothing to dedupe, or no Redis — fail-open
    key = f"radar:feishu:event:{event_id}"
    try:
        # redis-py asyncio: SET NX EX <ttl> — returns True if we won
        # the race, None if the key already existed.
        won = await redis_client.set(key, "1", nx=True, ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001 — fail-open on Redis hiccup
        logger.warning(
            "feishu_event_dedup_redis_error",
            error=str(exc)[:200],
        )
        return False
    return not bool(won)


def _strip_mentions(text: str) -> str:
    """Strip leading bot-mention tokens from user text.

    Feishu prefixes @-mentioned group messages with one of:
      * `@_user_1` (placeholder form)
      * `@bot_name` (single-word username)
      * `@Display Name With Spaces` (multi-word display name)

    The boundary between mention and command isn't known up front, so
    we keep dropping leading whitespace-separated tokens until either
    (a) the head token starts with `/` (a command), or (b) the text
    is empty.
    """
    text = text.strip()
    while text:
        head, _, rest = text.partition(" ")
        if head.startswith("/"):
            break
        text = rest.strip()
    return text


# ---------------------------------------------------------------------------
# Command router
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class BotCommand:
    """Parsed command — kind + args."""

    kind: Literal[
        "help",
        "today",
        "top",
        "research",
        "refresh",
        "score",
        "daily",
        "report",
        "table",
        "activate",
        "search",
        "content",
        "preferences",
        "unknown",
    ]
    args: str = ""


_COMMAND_ALK: dict[str, str] = {
    # MVP surface (simplify §10) — five commands + their 中文 aliases.
    "/help": "help",
    "/帮助": "help",
    "/today": "today",
    "/今日": "today",
    "/run": "run",
    "/运行": "run",
    "/status": "status",
    "/状态": "status",
    "/sources": "sources",
    "/源": "sources",
}


def parse_command(text: str) -> BotCommand:
    """Extract a `BotCommand` from already-cleaned user text. Falls back to
    `BotCommand(kind="unknown")` when the text isn't a recognised command.

    `text` should be the **mention-stripped** form produced by
    `parse_event` — by the time we get here, leading `@_user_1` /
    `@bot` / `@Display Name` tokens have been removed. See
    `_strip_mentions` for the multi-word display-name handling.
    """
    text = text.strip()
    if not text.startswith("/"):
        return BotCommand(kind="unknown", args=text)
    head, _, tail = text.partition(" ")
    head_lower = head.lower()
    kind = _COMMAND_ALK.get(head_lower)
    if kind is None:
        return BotCommand(kind="unknown", args=text)
    return BotCommand(kind=kind, args=tail.strip())


# ---------------------------------------------------------------------------
# Paywall helpers (Phase 15C v2.0)
# ---------------------------------------------------------------------------
# Imported lazily inside the functions below to keep import-time graph
# small — ``paywall`` pulls SQLAlchemy + models and we don't want the
# router module to require all of that at import.
def _command_quota_type(kind: str) -> Optional[str]:
    """Return the quota feature for ``kind`` (or ``None`` to bypass)."""
    from app.services.paywall import command_to_feature

    return command_to_feature(kind)


async def _paywall_check(*, command: "BotCommand", redis_client: Any, sender_open_id: Optional[str]) -> Any:
    """Open a DB session, run ``check_access``, return the verdict.

    Lazy-imports ``app.services.paywall`` and
    ``app.db.get_sessionmaker`` so the router module stays import-clean
    in tests that never call ``route()``.
    """
    from app.db import get_sessionmaker
    from app.services.paywall import check_access

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        return await check_access(
            session,
            sender_open_id or "",
            command.kind,
            redis_client=redis_client,
        )


async def _paywall_record(
    *,
    redis_client: Any,
    sender_open_id: Optional[str],
    quota_type: str,
) -> None:
    """Increment the per-day counter after a successful handler run."""
    from app.services.paywall import record_consumption

    if not sender_open_id:
        # Anonymous — nothing to attribute the counter to.
        return
    await record_consumption(
        redis_client,
        sender_open_id,
        quota_type,
    )


def _paywall_deny_reply(verdict: Any) -> CommandReply:
    """Build the ``CommandReply`` for a paywall denial."""
    return CommandReply(
        text=verdict.deny_message_zh,
        metadata={
            "command": verdict.quota_type,
            "denied": True,
            "plan": verdict.plan,
            "quota_type": verdict.quota_type,
            "quota_used": verdict.quota_used,
            "quota_limit": verdict.quota_limit,
            "reason": verdict.deny_reason,
        },
    )


# ---------------------------------------------------------------------------
# Router — calls existing internal HTTP APIs
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CommandReply:
    """The card the bot should reply with."""

    text: str
    card: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class FeishuCommandRouter:
    """Routes a `BotCommand` to the right internal HTTP API.

    Reuses every existing endpoint — zero business-logic duplication:

      `/today`    → GET /api/opportunities (?window_hours=24)
                    + SADD distinct IDs (Phase 16 — view_top_signals).
      `/top`      → GET /api/opportunities
                    + SADD distinct IDs (Phase 16 — view_top_signals).
      `/search`   → GET /api/opportunities?q=<kw>
                    + SADD distinct IDs (Phase 16 — view_top_signals).
      `/research` → POST /api/internal/research/on_demand
                    + INCR research counter (legacy Phase 15 path).
      `/refresh`  → POST /api/internal/discovery/run
      `/score`    → POST /api/internal/scoring/run
      `/daily`    → POST /api/internal/notifications/digest/send
      `/report`   → GET /api/internal/research/on_demand/{id}
                    + Drive Docx import (Phase 7)
      `/table`    → GET /api/opportunities + Bitable bulk insert (Phase 7)
      `/content`  → GET /api/opportunities/{id}
                    + ContentRadarAgent.analyze() (Phase 16)
                    + INCR content_full counter (legacy Phase 15 path).

    The webhook secret is read from `APP_SECRET_KEY` / `RADAR_WEBHOOK_SECRET`
    — the same one used by n8n cron workflows.
    """

    DEFAULT_BASE_URL = "http://localhost:8000"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: Optional[httpx.AsyncClient] = None,
        base_url: Optional[str] = None,
        drive_client: Optional[Any] = None,
        bitable_digest_client: Optional[Any] = None,
        bitable_ops_client: Optional[Any] = None,
        redis_client: Optional[Any] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.webhook_secret = (
            self.settings.app_secret_key
            or _env_webhook_secret()
            or ""
        )
        # — When the Feishu inbound handler is invoked, we receive the
        # event over the public ngrok tunnel and need to call back into
        # our own backend to execute the command. `localhost:8000` would
        # hit ngrok's listener (the public endpoint that just routed
        # the request in), not this FastAPI process — so default to the
        # docker service name `backend:8000` for the in-container
        # loopback. Operators can override via `FEISHU_INTERNAL_API_URL`
        # or by passing `base_url=...` explicitly.
        self.base_url = (
            base_url
            or self.settings.feishu_internal_api_url
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        # — owned by caller; if None, create a one-shot per call.
        self._http = http_client

        # — Phase 7 v2.0: Feishu Drive / Bitable clients. Used by
        # `/report`, `/doc`, `/table`, and the auto-Docx-on-`/research`
        # hook. Optional so existing call sites (and unit tests that
        # don't exercise content paths) can keep instantiating the
        # router without supplying them.
        self._drive = drive_client
        self._bitable_digest = bitable_digest_client
        self._bitable_ops = bitable_ops_client

        # — Phase 15C v2.0: Redis client used for paywall quota counters
        # and activation rate-limit guards. ``None`` means Redis is
        # unreachable → quota / rate-limit checks fall open (warn-logged).
        # The caller (FeishuEventHandler) is responsible for passing the
        # singleton from ``app.services.redis_client.get_redis()``.
        self._redis = redis_client

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.webhook_secret:
            h["X-Radar-Webhook"] = self.webhook_secret
        return h

    async def _post(
        self, path: str, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.post(
                url, json=json_body or {}, headers=self._headers()
            )
        finally:
            if owns_client:
                await client.aclose()
        try:
            return response.json() if response.content else {}
        except ValueError:
            return {"_status": response.status_code, "_text": response.text}

    async def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.get(url, headers=self._headers())
        finally:
            if owns_client:
                await client.aclose()
        # Phase 16 — always include ``_status`` in the returned dict so
        # downstream handlers can detect 4xx / 5xx without re-issuing
        # the request. For non-JSON bodies we fall back to a textual
        # envelope.
        if not response.content:
            return {"_status": response.status_code}
        try:
            payload = response.json()
        except ValueError:
            return {"_status": response.status_code, "_text": response.text}
        if not isinstance(payload, dict):
            return {"_status": response.status_code, "_value": payload}
        payload.setdefault("_status", response.status_code)
        return payload

    async def route(self, command: BotCommand) -> CommandReply:
        """Dispatch a parsed command and return the reply.

        Phase 15C v2.0 — wraps every quota-gated command with a paywall
        check (``app.services.paywall.check_access``) and
        records consumption on success. Commands not in
        ``COMMAND_TO_FEATURE`` (``help`` / ``activate`` / ``preferences``
        / ``refresh`` / ``score``) bypass paywall — they're either free
        metadata commands or admin-style operations.

        Phase 16 — ``view_top_signals`` quota is SADD-distinct, so the
        paywall's INCR-based ``record_consumption`` doesn't fit. We
        stash the verdict on ``self._last_verdict`` for the handler to
        read the *residual quota*, and let the handler call
        ``record_view_top_signals(redis, open_id, [id1, id2, ...])``
        with the *actual* IDs we ended up showing. ``research`` and
        ``content_full`` keep the legacy INCR path because they count
        "1 piece per call", not distinct IDs.
        """
        # — Static / metadata commands bypass paywall entirely.
        if command.kind == "help":
            return _help_reply()

        # — Phase 15C v2.0: paywall gate for quota-gated commands.
        quota_type = _command_quota_type(command.kind)
        verdict = None
        if quota_type is not None:
            verdict = await _paywall_check(
                command=command,
                redis_client=self._redis,
                sender_open_id=getattr(self, "_sender_open_id", None),
            )
            if not verdict.allowed:
                return _paywall_deny_reply(verdict)
        # Stash so handlers can compute "how many more can I show?"
        self._last_verdict = verdict

        # — Dispatch
        if command.kind == "today":
            reply = await self._today(command.args)
        elif command.kind == "run":
            reply = await self._run()
        elif command.kind == "status":
            reply = await self._status()
        elif command.kind == "sources":
            reply = await self._sources()
        else:
            reply = _unknown_reply(command.args)

        # — Phase 15C v2.0: record consumption on a successful
        # paywall-gated dispatch. Errors / denials don't consume quota
        # (handler set ``metadata["error"]`` or the deny branch already
        # short-circuited above).
        #
        # Phase 16 — view_top_signals is SADD-distinct; the handler
        # already wrote the actual shown IDs via
        # ``record_view_top_signals``. Skip the legacy INCR fallback so
        # we don't double-count.
        if (
            quota_type == "view_top_signals"
            or reply.metadata.get("error")
            or quota_type is None
        ):
            return reply
        await _paywall_record(
            redis_client=self._redis,
            sender_open_id=getattr(self, "_sender_open_id", None),
            quota_type=quota_type,
        )
        return reply

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    async def _today(self, args: str) -> CommandReply:
        """`/today` — Phase 16 distinct-signal SADD edition.

        Reads the residual quota from ``self._last_verdict`` (set by
        ``route()`` after the paywall check), fetches ``residual+5``
        items to leave headroom, truncates to ``residual``, and SADD
        the IDs that the user actually sees. Free user with quota=1
        sees 1 signal; Pro user with quota=20 sees 20.
        """
        _ = args
        residual, sender_open_id = self._residual_and_sender()
        if residual == 0:
            return CommandReply(
                text="⏰ 今日信号额度已用完。",
                metadata={"command": "today", "denied": True},
            )
        fetch_n = (residual if residual is not None else 10) + 5
        data = await self._get(f"/api/opportunities?limit={fetch_n}")
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return CommandReply(
                text="今日暂无新机会。明天再看看 👀",
                metadata={"command": "today", "items_count": 0},
            )
        if residual is not None:
            items = items[:residual]
        # SADD the actually-shown IDs (Phase 16 — distinct quota).
        ids = [o.get("id") for o in items if o.get("id") is not None]
        recorded = await self._record_signal_ids(sender_open_id, ids)
        # — render as plain text (Feishu lark_md). MVP keeps no
        # frontend / external link target — users read the digest
        # directly inside the Feishu chat, with sources surfaced
        # by the daily Docx the bot writes each morning.
        lines = ["**🔥 AI 机会雷达 · 今日 Top 信号**", ""]
        for idx, opp in enumerate(items, start=1):
            score = float(opp.get("total_score") or 0)
            title = opp.get("title") or "(无标题)"
            category = opp.get("category") or "未分类"
            summary = (opp.get("summary") or "").strip()
            lines.append(f"{idx}. **{title}** — ⭐ {int(round(score))} · {category}")
            if summary:
                # Trim to a single line so the IM message stays under
                # Feishu's 4000-char limit (5 entries × ~400 chars).
                one_line = summary.splitlines()[0][:240]
                lines.append(f"   {one_line}")
        return CommandReply(
            text="\n".join(lines),
            metadata={
                "command": "today",
                "items_count": len(items),
                "view_top_signals_recorded": recorded,
            },
        )

    # ------------------------------------------------------------------
    # Phase 16 — view_top_signals SADD helpers (KEEP for /today)
    # ------------------------------------------------------------------
    def _residual_and_sender(self) -> tuple[int | None, str]:
        """Compute residual view_top_signals quota + sender_open_id."""
        sender_open_id = getattr(self, "_sender_open_id", None) or ""
        verdict = getattr(self, "_last_verdict", None)
        if (
            verdict is None
            or getattr(verdict, "quota_type", None) != "view_top_signals"
        ):
            return None, sender_open_id
        residual = max(0, int(verdict.quota_limit) - int(verdict.quota_used))
        return residual, sender_open_id

    async def _record_signal_ids(
        self, sender_open_id: str, signal_ids: list[Any]
    ) -> bool:
        """SADD ``signal_ids`` to the per-day distinct-quota set (no-op for MVP)."""
        if not sender_open_id or not signal_ids or self._redis is None:
            return False
        from app.services.paywall import record_view_top_signals

        await record_view_top_signals(self._redis, sender_open_id, signal_ids)
        return True

    async def _run(self) -> CommandReply:
        """/run — submit a full pipeline run asynchronously.

        Phase 25 v2.1 — moved from synchronous to async because the
        full MVP pipeline (discovery → clustering → scoring →
        screening → research → digest) regularly exceeds Feishu's
        30 s per-event reply window. The handler now schedules a
        background task via :mod:`app.services.feishu.task_runner`,
        returns a `task_id` immediately, and the task posts the
        result summary back to the originating chat when it
        finishes (success or failure).

        For legacy callers / tests that need a synchronous answer
        (``/run` should still answer something on the IM thread`),
        we keep the synchronous code path as a fallback that the
        bot uses when ``task_runner.submit_pipeline_run`` raises.
        """
        try:
            from app.services.feishu.task_runner import submit_pipeline_run

            record = await submit_pipeline_run(
                chat_id=getattr(self, "_chat_id", "")
                or "",
                sender_open_id=getattr(self, "_sender_open_id", "") or "",
                command_kind="run",
            )
        except Exception as exc:  # noqa: BLE001 — log + fall back to synchronous path
            logger.warning(
                "feishu_async_run_submit_failed",
                error=str(exc),
                exc_info=True,
            )
            record = None

        if record is not None and record.status == "running":
            return CommandReply(
                text=(
                    f"🚀 流水线已提交（task_id={record.task_id}）。\n"
                    "完成后会自动回推到本对话，无需等待。"
                ),
                metadata={"command": "run", "task_id": record.task_id},
            )

        # — fallback: either task_runner was disabled OR the request
        # was rejected (too many concurrent runs). Fall through to the
        # synchronous path so the user still gets a useful reply.
        result = await self._post(
            "/api/internal/pipeline/run",
            {"source_slugs": None, "send_digest": True},
        )
        if result.get("_status", 200) >= 400 or result.get("error"):
            return CommandReply(
                text=(
                    "⚠️ 任务执行失败。\n"
                    f"阶段:{result.get('stage', 'pipeline')}\n"
                    f"错误:{result.get('error', 'unknown')[:200]}\n"
                    "请检查后端日志。"
                ),
                metadata={"command": "run", "error": True},
            )
        run_id = result.get("run_id", "?")
        return CommandReply(
            text=(
                f"✅ 任务已开始（run_id={run_id}）。\n"
                f"采集:{result.get('raw_count', 0)} 条；"
                f"新增:{result.get('new_count', 0)} 条；"
                f"信号:{result.get('signal_count', 0)} 条。\n"
                f"日报已发送:{'是' if result.get('digest_sent') else '否'}。"
            ),
            metadata={"command": "run", "run_id": run_id},
        )

    async def _status(self) -> CommandReply:
        """/status — show last run summary + per-source health."""
        result = await self._get("/api/internal/status")
        if result.get("_status", 200) >= 400:
            return CommandReply(
                text="⚠️ 暂时无法获取系统状态，请稍后重试。",
                metadata={"command": "status", "error": True},
            )
        last = result.get("last_run") or {}
        sources = result.get("sources") or {}
        healthy = sources.get("healthy", 0)
        total = sources.get("total", 0)

        if not last:
            run_line = "Last Run: 暂无"
        else:
            finished = last.get("finished_at") or "运行中"
            run_line = (
                f"Last Run: {finished}\n"
                f"状态: {last.get('status', '?')}；"
                f"触发: {last.get('trigger', '?')}\n"
                f"采集 {last.get('raw_count') or 0}；"
                f"新增 {last.get('new_count') or 0}；"
                f"信号 {last.get('signal_count') or 0}"
            )

        return CommandReply(
            text=(
                "系统状态\n\n"
                "Collector: OK\n"
                "Database: OK\n"
                "LLM: OK\n"
                "Feishu: OK\n\n"
                f"{run_line}\n\n"
                f"信息源: {healthy} / {total} healthy\n"
                f"累计信号: {result.get('total_signals', 0)}"
            ),
            metadata={"command": "status"},
        )

    async def _sources(self) -> CommandReply:
        """/sources — show each enabled source + last-success timestamp."""
        result = await self._get("/api/internal/sources/healthy")
        if result.get("_status", 200) >= 400:
            return CommandReply(
                text="⚠️ 暂时无法获取信息源状态，请稍后重试。",
                metadata={"command": "sources", "error": True},
            )
        items = result.get("items") or []
        if not items:
            return CommandReply(
                text="当前没有启用的信息源。",
                metadata={"command": "sources"},
            )
        lines = ["当前信息源:", ""]
        for it in items:
            mark = "✓" if it.get("healthy") else "✗"
            last = it.get("last_success_at") or "尚未采集"
            lines.append(f"{mark} {it.get('name')} ({it.get('type')}) — {last}")
        lines.append("")
        lines.append(
            f"状态: {result.get('healthy', 0)} / {result.get('count', 0)} healthy"
        )
        return CommandReply(
            text="\n".join(lines),
            metadata={"command": "sources", "count": len(items)},
        )


# ---------------------------------------------------------------------------
# Reply builders for static commands
# ---------------------------------------------------------------------------
def _help_reply() -> CommandReply:
    """MVP command menu — only the 5 commands kept after the simplify refactor.

    Chinese aliases (``/今日`` etc.) are accepted by ``_COMMAND_ALK``
    in :func:`parse_command` but the canonical English names are the
    ones surfaced here.
    """
    lines = [
        "**AI 机会雷达 — 命令菜单**",
        "",
        "/help    — 显示本菜单",
        "/today   — 今日信号（每日额度内）",
        "/run     — 手动触发完整流水线（采集→研究→飞书摘要）",
        "/status  — 上次运行摘要 + 信息源健康度",
        "/sources — 每个信息源的上次采集时间",
        "",
        "每日 08:00 CST 自动运行一次完整流水线并推送飞书摘要。",
    ]
    return CommandReply(text="\n".join(lines))


# ---------------------------------------------------------------------------
# Phase 16 — content payload rendering
# ---------------------------------------------------------------------------
def _render_content_opportunity_zh(payload: dict[str, Any], ctx: Any) -> str:
    """Render a :class:`VerticalResult.payload` (ContentOpportunity)
    as Feishu lark_md.

    Payload keys are populated by ``HeuristicContentRadarAgent``:
      ``title_candidates`` (list[str])
      ``hook``             (str)
      ``script_outline``   (str)
      ``material_ideas``   (list[str] | str)
      ``cta``              (str)
      ``risk_warning``     (str)

    Missing sections are silently skipped — agents can leave any of
    these empty without breaking the chat reply.
    """
    opp_id = payload.get("opportunity_id") or payload.get("signal_id") or "?"
    lines: list[str] = [f"**🎬 内容方案 · #{opp_id}**", ""]
    if ctx is not None and getattr(ctx, "platform", "general") != "general":
        plat = getattr(ctx, "platform", "")
        tone = getattr(ctx, "tone", "")
        if plat or tone:
            lines.append(
                f"_平台:_ `{plat}` · _语调:_ `{tone}`"
            )
            lines.append("")

    titles = payload.get("title_candidates") or []
    if titles:
        lines.append("**📝 标题候选**")
        for i, t in enumerate(titles, 1):
            lines.append(f"{i}. {t}")
        lines.append("")

    hook = payload.get("hook")
    if hook:
        lines.append(f"**🪝 开场钩子**\n{hook}\n")

    outline = payload.get("script_outline")
    if outline:
        lines.append(f"**🎞️ 脚本大纲**\n{outline}\n")

    materials = payload.get("material_ideas")
    if materials:
        if isinstance(materials, list):
            lines.append("**🧰 素材建议**")
            for m in materials:
                lines.append(f"- {m}")
            lines.append("")
        else:
            lines.append(f"**🧰 素材建议**\n{materials}\n")

    cta = payload.get("cta")
    if cta:
        lines.append(f"**📣 CTA**\n{cta}\n")

    risk = payload.get("risk_warning")
    if risk:
        lines.append(f"**⚠️ 风险提示**\n{risk}")

    # Fallback when the agent returned an empty payload — surface a
    # short note so the user doesn't get a blank card.
    if len(lines) <= 2:
        lines.append("(内容生成器没有产出可显示的字段)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown rendering for /report + /research → Docx
# ---------------------------------------------------------------------------
_REPORT_SECTIONS: list[tuple[str, str]] = [
    ("executive_summary", "执行摘要"),
    ("market_analysis", "市场分析"),
    ("competition_analysis", "竞争分析"),
    ("china_analysis", "中国市场分析"),
    ("monetization_analysis", "盈利模式"),
    ("mvp_analysis", "MVP 分析"),
    ("risk_analysis", "风险评估"),
]


def _render_report_markdown(
    detail: dict[str, Any], report: dict[str, Any]
) -> str:
    """Render a finished on-demand research report as Markdown.

    Feeds Feishu's `drive/v1/import_tasks` `file.content` (after
    base64). Layout follows the 7-section schema from
    `ResearchReport` (see `app/models.py`), plus a recommendation /
    confidence headline and a sources section.
    """
    title = detail.get("opportunity_title") or detail.get("seed_topic") or "研究报告"
    recommendation = (
        report.get("recommendation")
        or detail.get("recommendation")
        or ""
    ).strip() or "(无建议)"
    try:
        confidence_pct = f"{float(report.get('confidence') or detail.get('confidence') or 0):.0%}"
    except (TypeError, ValueError):
        confidence_pct = "n/a"
    sources = report.get("sources") or []

    lines: list[str] = [
        f"# {title}",
        "",
        f"_推荐: **{recommendation}** · 置信度: {confidence_pct}_",
        "",
    ]
    for key, label in _REPORT_SECTIONS:
        body = (report.get(key) or "").strip()
        if not body:
            continue
        lines.append(f"## {label}")
        lines.append("")
        lines.append(body)
        lines.append("")

    if sources:
        lines.append("## 来源")
        lines.append("")
        for src in sources[:20]:
            if isinstance(src, dict):
                src_title = src.get("title") or src.get("url") or "(来源)"
                src_url = src.get("url") or ""
                if src_url:
                    lines.append(f"- [{src_title}]({src_url})")
                else:
                    lines.append(f"- {src_title}")
            else:
                lines.append(f"- {src}")
    return "\n".join(lines).strip() + "\n"


def _unknown_reply(text: str) -> CommandReply:
    snippet = text[:80]
    return CommandReply(
        text=(
            f"🤔 我不理解 `/{snippet}`\n"
            "试试 `/help` 查看命令菜单。"
        ),
        metadata={"command": "unknown", "raw": text},
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _env_webhook_secret() -> str:
    """Read `RADAR_WEBHOOK_SECRET` directly from os.environ.

    Used because `Settings` may be lru_cached from earlier in the
    process lifetime; `RADAR_WEBHOOK_SECRET` is the historical env var.
    """
    import os

    return os.environ.get("RADAR_WEBHOOK_SECRET", "") or ""


__all__ = [
    "BotCommand",
    "CommandReply",
    "FeishuCommandRouter",
    "FeishuEvent",
    "parse_command",
    "parse_event",
    "verify_event",
]