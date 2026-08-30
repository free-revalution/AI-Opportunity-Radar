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
def parse_event(body: dict[str, Any]) -> FeishuEvent | dict[str, Any] | None:
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

    # Encrypted events — Phase 6 stub.
    if "encrypt" in body:
        logger.warning("feishu_event_encrypted_not_implemented")
        return None

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
    "/help": "help",
    "/today": "today",
    "/今日": "today",
    "/top": "top",
    "/research": "research",
    "/分析": "research",
    "/refresh": "refresh",
    "/刷新": "refresh",
    "/score": "score",
    "/重评": "score",
    "/daily": "daily",
    "/日报": "daily",
    # — Phase 7 v2.0 — content ecosystem
    "/report": "report",
    "/doc": "report",          # alias
    "/文档": "report",          # 中文 alias
    "/table": "table",
    "/表格": "table",
    # — Phase 14A — activation code redemption
    "/activate": "activate",
    "/激活": "activate",
    # — Phase 15C — search stub + user preferences
    "/search": "search",
    "/搜索": "search",
    "/preferences": "preferences",
    "/偏好": "preferences",
    # — Phase 16 — content brief generator (ContentRadarAgent V2)
    "/content": "content",
    "/内容": "content",
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
    from app.services.subscriptions.paywall import command_to_feature

    return command_to_feature(kind)


async def _paywall_check(*, command: "BotCommand", redis_client: Any, sender_open_id: Optional[str]) -> Any:
    """Open a DB session, run ``check_access``, return the verdict.

    Lazy-imports ``app.services.subscriptions.paywall`` and
    ``app.db.get_sessionmaker`` so the router module stays import-clean
    in tests that never call ``route()``.
    """
    from app.db import get_sessionmaker
    from app.services.subscriptions.paywall import check_access

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
    from app.services.subscriptions.paywall import record_consumption

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
        # — Base URL for *user-visible* links — users click these in
        # the Feishu reply, so it must point at a publicly reachable
        # host (frontend or the ngrok tunnel), not at the docker
        # internal DNS name we use for backend self-callbacks.
        self.public_base_url = (
            (self.settings.app_base_url or "").rstrip("/")
            or self.base_url
        )
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
        check (``app.services.subscriptions.paywall.check_access``) and
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
        elif command.kind == "top":
            reply = await self._top(command.args)
        elif command.kind == "research":
            reply = await self._research(command.args)
        elif command.kind == "refresh":
            reply = await self._refresh()
        elif command.kind == "score":
            reply = await self._score()
        elif command.kind == "daily":
            reply = await self._daily()
        elif command.kind == "report":
            reply = await self._report(command.args)
        elif command.kind == "table":
            reply = await self._table(command.args)
        elif command.kind == "activate":
            reply = await self._activate(command.args)
        elif command.kind == "search":
            reply = await self._search(command.args)
        elif command.kind == "content":
            reply = await self._content(command.args)
        elif command.kind == "preferences":
            reply = await self._preferences(command.args)
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
        # — render as plain text (Feishu lark_md). Card rendering is
        # reserved for Phase 7 knowledge-base replies.
        lines = ["**🔥 AI 机会雷达 · 今日 Top 信号**", ""]
        for idx, opp in enumerate(items, start=1):
            score = float(opp.get("total_score") or 0)
            title = opp.get("title") or "(无标题)"
            url = f"{self.public_base_url}/opportunities/{opp.get('id')}"
            lines.append(f"{idx}. **{title}** — ⭐ {int(round(score))}")
            lines.append(f"   [查看详情]({url})")
        return CommandReply(
            text="\n".join(lines),
            metadata={
                "command": "today",
                "items_count": len(items),
                "view_top_signals_recorded": recorded,
            },
        )

    async def _top(self, args: str) -> CommandReply:
        """`/top` — Phase 16 distinct-signal SADD edition.

        Same pattern as ``/today`` — the residual quota is shared, so a
        user who used their daily quota on ``/today`` gets nothing
        from ``/top`` and vice versa. SADD deduplicates across both.
        """
        _ = args
        residual, sender_open_id = self._residual_and_sender()
        if residual == 0:
            return CommandReply(
                text="⏰ 今日信号额度已用完。",
                metadata={"command": "top", "denied": True},
            )
        fetch_n = (residual if residual is not None else 10) + 5
        data = await self._get(f"/api/opportunities?limit={fetch_n}")
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return CommandReply(text="数据库里还没有机会。")
        if residual is not None:
            items = items[:residual]
        ids = [o.get("id") for o in items if o.get("id") is not None]
        recorded = await self._record_signal_ids(sender_open_id, ids)
        lines = ["**🏆 历史 Top 机会**", ""]
        for idx, opp in enumerate(items, start=1):
            score = float(opp.get("total_score") or 0)
            title = opp.get("title") or "(无标题)"
            url = f"{self.public_base_url}/opportunities/{opp.get('id')}"
            lines.append(f"{idx}. **{title}** — ⭐ {int(round(score))}")
            lines.append(f"   [查看详情]({url})")
        return CommandReply(
            text="\n".join(lines),
            metadata={
                "command": "top",
                "items_count": len(items),
                "view_top_signals_recorded": recorded,
            },
        )

    # ------------------------------------------------------------------
    # Phase 16 — view_top_signals SADD helpers
    # ------------------------------------------------------------------
    def _residual_and_sender(self) -> tuple[int | None, str]:
        """Compute residual view_top_signals quota + sender_open_id.

        Returns (residual, sender_open_id). ``residual`` is None when
        the command bypassed paywall (``/today`` and ``/top`` are
        always gated, so this only fires for tests that bypass).
        """
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
        """SADD ``signal_ids`` to the per-day distinct-quota set.

        Returns True when at least one ID was recorded (or the caller
        is anonymous / no Redis — both are no-ops). False means we
        didn't touch Redis.
        """
        if not sender_open_id or not signal_ids or self._redis is None:
            return False
        # Lazy import keeps the module import-graph tiny for tests
        # that never call ``route()``.
        from app.services.subscriptions.paywall import record_view_top_signals

        await record_view_top_signals(self._redis, sender_open_id, signal_ids)
        return True

    async def _research(self, args: str) -> CommandReply:
        topic = args.strip()
        if not topic:
            return CommandReply(text="用法:`/research <主题>`\n例如:`/research AI 法律合同审核`")
        # — delegate to Phase 5 on-demand endpoint
        result = await self._post(
            "/api/internal/research/on_demand",
            {"topic": topic},
        )
        if result.get("_status", 200) >= 400:
            return CommandReply(
                text=f"❌ 研究任务创建失败:{result.get('_text') or result}",
            )
        job_id = result.get("job_id")
        url = f"{self.public_base_url}/on-demand"

        # — Phase 7 v2.0: auto-create Docx once the report finishes.
        # The /api/internal/research/on_demand endpoint is synchronous
        # (returns after the report is rendered), so we can poll for
        # completion inline. Failures here are logged but never
        # supersede the Web link — the chat reply always has at least
        # the URL fallback.
        doc_line = ""
        if job_id is not None and self._drive is not None and self._drive.is_configured:
            doc_line = await self._maybe_create_docx_for_job(int(job_id), topic)

        text = (
            f"✅ 已生成研究任务 #{job_id} · 主题:`{topic}`\n"
            f"[在 Web 上查看完整报告]({url})"
        )
        if doc_line:
            text = f"{text}\n{doc_line}"
        return CommandReply(
            text=text,
            metadata={"command": "research", "job_id": job_id, "topic": topic},
        )

    async def _maybe_create_docx_for_job(
        self, job_id: int, topic: str
    ) -> str:
        """Best-effort: fetch a finished on-demand report and push to Drive.

        Returns the markdown link line to append to the chat reply, or
        `""` on any failure (we never let Docx issues break the main
        reply).
        """
        from app.services.feishu.content_client import FeishuContentError

        detail = await self._get(f"/api/internal/research/on_demand/{job_id}")
        if detail.get("_status", 200) >= 400 or detail.get("status") != "completed":
            return ""
        report_payload = detail.get("report") or {}
        if not report_payload:
            return ""
        markdown = _render_report_markdown(detail, report_payload)
        try:
            result = await self._drive.create_docx_from_markdown(  # type: ignore[union-attr]
                title=f"研究报告 #{job_id} · {topic}",
                markdown=markdown,
            )
            doc_url = result.get("url") or ""
            if doc_url:
                return f"📄 [飞书云文档已生成]({doc_url})"
        except FeishuContentError as exc:
            logger.warning(
                "feishu_research_docx_failed",
                job_id=job_id,
                error=str(exc),
            )
        return ""

    async def _refresh(self) -> CommandReply:
        # — fires off discovery. Long-running; the endpoint just queues.
        result = await self._post("/api/internal/discovery/run", {})
        return CommandReply(
            text=(
                "🔄 已触发数据抓取(GitHub / Reddit / Hacker News / Product Hunt / RSS / YouTube)。\n"
                "完成后会推送日报。"
            ),
            metadata={"command": "refresh", "result": str(result)[:500]},
        )

    async def _score(self) -> CommandReply:
        result = await self._post("/api/internal/scoring/run", {})
        return CommandReply(
            text="📊 已触发重新评分。完成后查看 /dashboard。",
            metadata={"command": "score", "result": str(result)[:500]},
        )

    async def _daily(self) -> CommandReply:
        result = await self._post(
            "/api/internal/notifications/digest/send",
            {},
        )
        # — Phase 7 v2.0: also sync Top-10 to the daily-digest Bitable.
        # Failure here is non-fatal — chat push is the primary channel.
        table_line = ""
        if (
            self._bitable_digest is not None
            and self._bitable_digest.is_configured is False  # may auto-create
        ):
            pass  # is_configured may be False initially but ensure_app handles it
        if self._bitable_digest is not None:
            try:
                top10 = await self._get("/api/opportunities?limit=10")
                items = top10.get("items") if isinstance(top10, dict) else None
                if items:
                    inserted = await self._bitable_digest.bulk_insert_opportunities(  # type: ignore[union-attr]
                        items=items,
                        base_url_for_links=self.public_base_url,
                    )
                    app_token, _ = await self._bitable_digest.ensure_table()  # type: ignore[union-attr]
                    table_url = self._bitable_digest.public_url(app_token=app_token)
                    if table_url:
                        table_line = (
                            f"📊 已同步今日 Top {inserted} 条至多维表格\n"
                            f"[在 Bitable 中查看]({table_url})"
                        )
            except Exception as exc:  # noqa: BLE001 — non-fatal
                logger.warning(
                    "feishu_daily_bitable_sync_failed",
                    error=str(exc),
                )

        text = "📬 已触发日报推送(主通道 → 降级通道)。"
        if table_line:
            text = f"{text}\n{table_line}"
        return CommandReply(
            text=text,
            metadata={"command": "daily", "result": str(result)[:500]},
        )

    async def _report(self, args: str) -> CommandReply:
        """`/report <job_id>` — push a finished research report to Docx.

        Distinct from the auto-Docx path in `_research`: this is the
        explicit "retroactively push job #N" command, useful when the
        user wants to share a report they generated earlier.
        """
        from app.services.feishu.content_client import FeishuContentError

        job_id_str = args.strip()
        if not job_id_str.isdigit():
            return CommandReply(
                text="用法:`/report <job_id>`\n例如:`/report 5`"
            )
        if self._drive is None or not self._drive.is_configured:
            return CommandReply(
                text="❌ 飞书云文档未配置(FEISHU_DRIVE_ROOT_FOLDER_TOKEN 为空)。"
            )
        job_id = int(job_id_str)
        detail = await self._get(f"/api/internal/research/on_demand/{job_id}")
        status_code = detail.get("_status", 200)
        if status_code >= 400:
            return CommandReply(
                text=f"❌ 任务 #{job_id} 状态异常:HTTP {status_code}。"
            )
        if detail.get("status") != "completed":
            current_status = detail.get("status") or "unknown"
            # — Heuristic: a Phase 5 on-demand job that has `started_at`
            # null AND status=pending is almost certainly a *historical*
            # job from before the on-demand pipeline was migrated to
            # synchronous mode (i.e. the worker was removed but old rows
            # remained). Surface a clearer message so the user knows
            # they should re-run via `/research` instead of waiting.
            if current_status == "pending" and not detail.get("started_at"):
                return CommandReply(
                    text=(
                        f"⚠️ 任务 #{job_id} 是 Phase 5 历史遗留的 pending job "
                        "(`started_at` 为空,worker 已下线,不会再启动)。\n"
                        f"请用 `/research <新主题>` 重新生成一份研究报告,"
                        f"Phase 7 会立刻同步推送。"
                    ),
                )
            return CommandReply(
                text=f"⏳ 任务 #{job_id} 当前状态 `{current_status}`,尚未完成。"
            )
        report_payload = detail.get("report") or {}
        if not report_payload:
            return CommandReply(
                text=f"❌ 任务 #{job_id} 没有可推送的报告内容。"
            )
        markdown = _render_report_markdown(detail, report_payload)
        topic = detail.get("seed_topic") or detail.get("opportunity_title") or ""
        try:
            created = await self._drive.create_docx_from_markdown(  # type: ignore[union-attr]
                title=f"研究报告 #{job_id} · {topic}" if topic else f"研究报告 #{job_id}",
                markdown=markdown,
            )
        except FeishuContentError as exc:
            return CommandReply(
                text=f"❌ 文档创建失败:{exc}",
                metadata={"command": "report", "job_id": job_id, "error": str(exc)},
            )
        return CommandReply(
            text=(
                f"📄 报告 #{job_id} 已推送至飞书云文档\n"
                f"[打开]({created.get('url', '')})"
            ),
            metadata={
                "command": "report",
                "job_id": job_id,
                "doc_id": created.get("doc_id"),
                "doc_url": created.get("url"),
            },
        )

    async def _table(self, args: str) -> CommandReply:
        """`/table` — sync the full opportunity list to a Bitable app."""
        from app.services.feishu.content_client import FeishuContentError

        _ = args
        if self._bitable_ops is None:
            return CommandReply(
                text="❌ Opportunities 多维表格客户端未初始化。"
            )
        if not self._bitable_ops.is_configured:
            # is_configured only flips True once ensure_app writes the
            # token back into settings. Try ensure_app once to surface
            # a clean error message on auto-create failure.
            pass
        data = await self._get("/api/opportunities?limit=200")
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return CommandReply(
                text="📭 数据库里没有可同步的机会。先跑 `/refresh` + `/score` 抓一些数据。"
            )
        try:
            inserted = await self._bitable_ops.bulk_insert_opportunities(  # type: ignore[union-attr]
                items=items,
                base_url_for_links=self.public_base_url,
            )
            app_token, _ = await self._bitable_ops.ensure_table()  # type: ignore[union-attr]
            table_url = self._bitable_ops.public_url(app_token=app_token)
        except FeishuContentError as exc:
            return CommandReply(
                text=f"❌ 多维表格同步失败:{exc}",
                metadata={"command": "table", "error": str(exc)},
            )
        text = f"📊 已同步 {inserted} 条机会到多维表格"
        if table_url:
            text = f"{text}\n[在 Bitable 中查看]({table_url})"
        return CommandReply(
            text=text,
            metadata={
                "command": "table",
                "inserted": inserted,
                "table_url": table_url,
            },
        )

    async def _activate(self, args: str) -> CommandReply:
        """`/activate <code>` — bind an Activation Code to this Feishu user.

        Phase 14A — Xianyu-to-Feishu last-mile. Wraps
        ``app.services.activation.redeem_for_user()`` so the bot never
        needs to know about hash schemes, status flips, or Subscription
        row creation. Always returns a Chinese reply — even on bad input
        — so the user never sees a traceback.

        Phase 15D — also passes ``redis_client=self._redis`` so the
        activation flow's anti-brute-force guard (5 fails / 10 min) is
        active. With ``redis_client=None`` the guard is bypassed — fine
        for local dev, never in prod.
        """
        from app.services.activation import redeem_for_user

        # We need the sender's Feishu Open ID. The router doesn't carry it
        # directly — callers must stash it on the router before invoking
        # route(). Falls back to None which causes INVALID_FORMAT.
        sender_open_id = getattr(self, "_sender_open_id", None)

        code = (args or "").strip()
        if not code:
            return CommandReply(
                text="用法:`/activate <激活码>`\n例如:`/activate ABCD-EFGH-JKLM`",
                metadata={"command": "activate", "error": "missing_code"},
            )

        # Self-callback to /api/admin-style endpoint would be too heavy
        # here — talk to the DB directly via the same engine the app uses.
        from app.db import get_sessionmaker

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            result = await redeem_for_user(
                session,
                code=code,
                feishu_open_id=sender_open_id or "",
                redis_client=self._redis,
            )

        return CommandReply(
            text=result.user_message,
            metadata={
                "command": "activate",
                "status": result.status.value,
                "success": result.success,
                "plan": result.plan,
                "code_id": result.code_id,
            },
        )

    async def _search(self, args: str) -> CommandReply:
        """`/search <query>` — Phase 16 real implementation.

        Calls ``/api/opportunities?q=<kw>`` (the SQL-LIKE filter added
        in 16D) and applies the same distinct-signal SADD quota as
        ``/today`` / ``/top``. The quota bucket is shared — a user who
        already burned their quota on ``/today`` is denied here too.
        """
        from urllib.parse import quote_plus

        query = (args or "").strip()
        if not query:
            return CommandReply(
                text="用法:`/search <关键词>`\n例如:`/search AI 法律合同审核`",
                metadata={"command": "search", "error": "missing_query"},
            )

        residual, sender_open_id = self._residual_and_sender()
        if residual == 0:
            return CommandReply(
                text="⏰ 今日信号额度已用完。",
                metadata={"command": "search", "denied": True, "query": query},
            )

        fetch_n = (residual if residual is not None else 20) + 5
        # `quote_plus` URL-encodes spaces + CJK + special chars so the
        # query lands in FastAPI's `q` Query parameter intact.
        encoded = quote_plus(query)
        data = await self._get(
            f"/api/opportunities?q={encoded}&limit={fetch_n}"
        )
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return CommandReply(
                text=(
                    f"🔍 没找到匹配「{query}」的机会。\n"
                    f"试试 `/today` 或 `/top` 看看其他热门。"
                ),
                metadata={
                    "command": "search",
                    "query": query,
                    "items_count": 0,
                },
            )
        if residual is not None:
            items = items[:residual]
        ids = [o.get("id") for o in items if o.get("id") is not None]
        recorded = await self._record_signal_ids(sender_open_id, ids)

        lines = [f"**🔍 搜索结果 · `{query}`**", ""]
        for idx, opp in enumerate(items, start=1):
            score = float(opp.get("total_score") or 0)
            title = opp.get("title") or "(无标题)"
            url = f"{self.public_base_url}/opportunities/{opp.get('id')}"
            lines.append(f"{idx}. **{title}** — ⭐ {int(round(score))}")
            lines.append(f"   [查看详情]({url})")
        return CommandReply(
            text="\n".join(lines),
            metadata={
                "command": "search",
                "query": query,
                "items_count": len(items),
                "view_top_signals_recorded": recorded,
            },
        )

    async def _content(self, args: str) -> CommandReply:
        """`/content <opportunity_id>` — Phase 16 ContentRadarAgent V2 +
        Phase 17 compliance gate + persistence.

        Builds a :class:`VerticalContext` from the sender's User
        preferences (Phase 15A columns), hands the opportunity detail
        to ``ContentRadarAgent.analyze()``, runs the rendered lark_md
        through ``ComplianceService.check_content``, appends a warning
        when the verdict is denied, then persists the row to
        ``content_opportunities`` for the admin Content Center.

        ``content_full`` quota is INCR-based (one piece per call) so
        ``route()`` records consumption via the legacy
        ``record_consumption`` path — this handler does NOT call
        ``record_view_top_signals``.
        """
        sig_id_str = (args or "").strip()
        if not sig_id_str.isdigit():
            return CommandReply(
                text=(
                    "用法:`/content <opportunity_id>`\n"
                    "例如:`/content 42`"
                ),
                metadata={"command": "content", "error": "bad_args"},
            )
        sig_id = int(sig_id_str)
        detail = await self._get(f"/api/opportunities/{sig_id}")
        if detail.get("_status", 200) >= 400:
            return CommandReply(
                text=f"❌ 信号 #{sig_id} 找不到。",
                metadata={
                    "command": "content",
                    "error": "not_found",
                    "signal_id": sig_id,
                },
            )

        # — Build VerticalContext from User preferences (Phase 15A).
        ctx = await self._vertical_context_for_sender()
        # — Run the registered content agent (heuristic by default;
        # LLMContentRadarAgent with provider=None auto-falls-back).
        from app.services.agents.registry import try_get_agent

        agent = try_get_agent("content")
        if agent is None:
            return CommandReply(
                text="❌ ContentRadarAgent 未注册。",
                metadata={
                    "command": "content",
                    "error": "no_agent",
                    "signal_id": sig_id,
                },
            )
        result = await agent.analyze(signal=detail, context=ctx, report=None)
        text = _render_content_opportunity_zh(result.payload, ctx)

        # — Phase 17: compliance gate on user-visible output. We do not
        # block the reply (the user already submitted the request; a
        # silent drop would be worse than a marked draft). The admin
        # can review blocked rows at /api/admin/content_opportunities.
        from app.services.compliance.service import default_service

        compliance = default_service().check_content(
            text, source=ctx.platform, context="content"
        )
        compliance_blocked = not compliance.allowed
        compliance_risk_types = [rt.value for rt in compliance.risk_types]

        if compliance_blocked:
            text = (
                text
                + "\n\n⚠️ 内容已标记为合规风险,管理员审核后才可见。"
            )

        metadata = {
            "command": "content",
            "signal_id": sig_id,
            "platform": ctx.platform,
            "tone": ctx.tone,
            "language": ctx.language,
            "agent": agent.name,
            "confidence": result.confidence,
            "compliance_blocked": compliance_blocked,
            "compliance_risk_score": compliance.risk_score,
            "compliance_risk_types": compliance_risk_types,
        }

        # — Phase 17: persist the row so the admin Content Center can
        # show it. Lazy import keeps the test path working when the
        # DB engine is monkey-patched (see test_content_command.py).
        persisted = await self._persist_content_opportunity(
            signal_id=sig_id,
            ctx=ctx,
            payload=result.payload,
            confidence=result.confidence,
            agent_name=agent.name,
            compliance_blocked=compliance_blocked,
            compliance_risk_score=compliance.risk_score,
            compliance_risk_types=compliance_risk_types,
        )
        metadata["persisted"] = persisted

        return CommandReply(text=text, metadata=metadata)

    async def _persist_content_opportunity(
        self,
        *,
        signal_id: int,
        ctx: Any,
        payload: dict[str, Any],
        confidence: float,
        agent_name: str,
        compliance_blocked: bool,
        compliance_risk_score: float,
        compliance_risk_types: list[str],
    ) -> bool:
        """Persist one row to ``content_opportunities``.

        Returns True on commit, False on any DB failure (fail-open —
        the user has already seen the reply, dropping it now would be
        worse than losing the admin-visible row). Admin endpoints can
        therefore see all *successful* rows; the missing ones are
        surfaced only via logs.

        We import lazily so tests without a DB sessionmaker simply
        skip persistence instead of failing the request.
        """
        try:
            from app.db import get_sessionmaker
            from app.repositories.content_opportunities import (
                ContentOpportunityRepository,
            )

            sessionmaker = get_sessionmaker()
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "content_persist_no_sessionmaker",
                signal_id=signal_id,
                error=str(exc),
            )
            return False

        try:
            async with sessionmaker() as session:
                repo = ContentOpportunityRepository(session)
                await repo.create(
                    signal_id=signal_id,
                    platform=ctx.platform,
                    audience=ctx.audience or None,
                    niche=ctx.niche or None,
                    tone=ctx.tone or None,
                    content_angle=payload.get("content_angle"),
                    hook=payload.get("hook"),
                    title_candidates=payload.get("title_candidates"),
                    material_ideas=payload.get("material_ideas"),
                    script_outline=payload.get("script_outline"),
                    recommended_length=payload.get("recommended_length"),
                    cta=payload.get("cta"),
                    risk_warning=payload.get("risk_warning"),
                    content_score=float(confidence) * 100.0,
                    status="draft",
                    metadata_json={
                        "compliance_blocked": compliance_blocked,
                        "compliance_risk_score": compliance_risk_score,
                        "compliance_risk_types": compliance_risk_types,
                        "feishu_open_id": ctx.feishu_open_id,
                        "agent_name": agent_name,
                    },
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "content_persist_failed",
                signal_id=signal_id,
                error=str(exc),
            )
            return False
        return True

    async def _vertical_context_for_sender(self) -> Any:
        """Look up (or auto-create) the sender's User row and return
        a :class:`VerticalContext` for it.

        Falls back to a default ``VerticalContext()`` when the sender
        is anonymous (no Feishu open_id) or the DB is unreachable —
        the agent still produces a sensible heuristic output.
        """
        from app.services.agents.base import VerticalContext
        from app.services.users import build_vertical_context_for_open_id

        sender_open_id = getattr(self, "_sender_open_id", None) or ""
        if not sender_open_id:
            return VerticalContext()
        try:
            from app.db import get_sessionmaker

            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                ctx = await build_vertical_context_for_open_id(
                    session, sender_open_id
                )
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feishu_vertical_context_failed",
                sender_open_id=sender_open_id,
                error=str(exc)[:200],
            )
            return VerticalContext()

    async def _preferences(self, args: str) -> CommandReply:
        """`/preferences [set k=v | reset]` — Phase 15C v2.0.

        三个子模式:
          * 无参         — 读当前偏好(自动 upsert User 行)
          * ``set k=v``  — 设置单个偏好字段(白名单校验)
          * ``reset``    — 清空 6 个偏好列

        Phase 16 会把 User.preferences_* 注入 ContentRadarAgent prompt
        context —— 这一步只做持久化。
        """
        from app.db import get_sessionmaker
        from app.services.users import (
            apply_preference,
            get_or_create_user_by_feishu,
            render_preferences_zh,
            reset_preferences,
        )

        sender_open_id = getattr(self, "_sender_open_id", None)
        if not sender_open_id:
            return CommandReply(
                text=(
                    "请先 `/activate <激活码>` 绑定飞书账号后查看偏好。\n"
                    "未绑定的用户无法保存偏好设置。"
                ),
                metadata={"command": "preferences", "error": "no_sender"},
            )

        sub = (args or "").strip()
        sessionmaker = get_sessionmaker()

        # — read mode ----------------------------------------------------
        if not sub or sub == "show":
            async with sessionmaker() as session:
                user = await get_or_create_user_by_feishu(
                    session, sender_open_id, commit=False
                )
                await session.commit()
                text = render_preferences_zh(user)
            return CommandReply(
                text=text,
                metadata={"command": "preferences", "mode": "read"},
            )

        # — reset --------------------------------------------------------
        if sub == "reset":
            async with sessionmaker() as session:
                user = await get_or_create_user_by_feishu(
                    session, sender_open_id, commit=False
                )
                reset_preferences(user)
                await session.commit()
                text = render_preferences_zh(user)
            return CommandReply(
                text="✅ 已清空偏好。\n\n" + text,
                metadata={"command": "preferences", "mode": "reset"},
            )

        # — set k=v ------------------------------------------------------
        if sub.startswith("set "):
            kv = sub[4:].strip()
            if "=" not in kv:
                return CommandReply(
                    text=(
                        "用法:`/preferences set <key>=<value>`\n"
                        "例如:`/preferences set platform=xiaohongshu`\n"
                        "允许的字段:vertical / niche / platform / "
                        "audience / tone / language"
                    ),
                    metadata={
                        "command": "preferences",
                        "mode": "set",
                        "error": "missing_equals",
                    },
                )
            key, _, value = kv.partition("=")
            key = key.strip()
            value = value.strip()
            async with sessionmaker() as session:
                user = await get_or_create_user_by_feishu(
                    session, sender_open_id, commit=False
                )
                user, err = apply_preference(user, key, value)
                if err is not None:
                    return CommandReply(
                        text=err,
                        metadata={
                            "command": "preferences",
                            "mode": "set",
                            "error": "invalid",
                            "key": key,
                        },
                    )
                await session.commit()
                rendered = render_preferences_zh(user)
            return CommandReply(
                text=f"✅ 已设置 `{key}={value}`\n\n{rendered}",
                metadata={
                    "command": "preferences",
                    "mode": "set",
                    "key": key,
                    "value": value,
                },
            )

        # — anything else — usage hint ----------------------------------
        return CommandReply(
            text=(
                "用法:\n"
                "• `/preferences` — 查看当前偏好\n"
                "• `/preferences set <key>=<value>` — 设置(例如 "
                "`platform=xiaohongshu`)\n"
                "• `/preferences reset` — 清空偏好\n"
                "\n"
                "允许的字段:vertical / niche / platform / audience / "
                "tone / language"
            ),
            metadata={"command": "preferences", "error": "bad_subcommand"},
        )


# ---------------------------------------------------------------------------
# Reply builders for static commands
# ---------------------------------------------------------------------------
def _help_reply() -> CommandReply:
    lines = [
        "**AI 机会雷达 — 命令菜单**",
        "",
        "/help — 显示本菜单",
        "/today — 今日信号(每日额度内)",
        "/top — 全历史 Top 信号(共享每日额度)",
        "/search <关键词> — 按关键词搜索(共享每日额度)",
        "/research <主题> — 按需生成研究报告(深度研究配额)",
        "/refresh — 触发数据抓取",
        "/score — 触发重新评分",
        "/daily — 触发日报推送(同时同步 Top 10 到多维表格)",
        "/report <job_id> — 将已完成的研究报告推送至飞书云文档",
        "/doc <job_id> — /report 的别名",
        "/table — 手动同步机会表到多维表格",
        "/content <opportunity_id> — 基于偏好生成内容方案(内容配额)",
        "/preferences — 查看 / 设置偏好(vertical / niche / platform …)",
        "/activate <激活码> — 绑定闲鱼购买的激活码",
        "",
        "💎 套餐:免费 1 信号/天 · 基础 ¥29(5/天) · 专业 ¥59(20/天)",
        "更多能力见 Web 端 /dashboard",
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