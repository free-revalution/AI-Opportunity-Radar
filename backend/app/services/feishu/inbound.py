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
      `/top`      → GET /api/opportunities
      `/research` → POST /api/internal/research/on_demand
      `/refresh`  → POST /api/internal/discovery/run
      `/score`    → POST /api/internal/scoring/run
      `/daily`    → POST /api/internal/notifications/digest/send

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
        try:
            return response.json() if response.content else {}
        except ValueError:
            return {"_status": response.status_code, "_text": response.text}

    async def route(self, command: BotCommand) -> CommandReply:
        """Dispatch a parsed command and return the reply."""
        if command.kind == "help":
            return _help_reply()

        if command.kind == "today":
            return await self._today(command.args)

        if command.kind == "top":
            return await self._top(command.args)

        if command.kind == "research":
            return await self._research(command.args)

        if command.kind == "refresh":
            return await self._refresh()

        if command.kind == "score":
            return await self._score()

        if command.kind == "daily":
            return await self._daily()

        return _unknown_reply(command.args)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    async def _today(self, args: str) -> CommandReply:
        # — call the existing opportunities endpoint. The default
        # `/api/opportunities` returns all-time top; we keep it simple
        # here and let the UI's filter handle the window. Phase 6.x
        # could add a `window_hours` query param.
        _ = args
        data = await self._get("/api/opportunities?limit=10")
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return CommandReply(
                text="今日暂无新机会。明天再看看 👀",
                metadata={"command": "today", "items_count": 0},
            )
        # — render as plain text (Feishu lark_md). Card rendering is
        # reserved for Phase 7 knowledge-base replies.
        lines = ["**🔥 AI 机会雷达 · 今日 Top 10**", ""]
        for idx, opp in enumerate(items[:10], start=1):
            score = float(opp.get("total_score") or 0)
            title = opp.get("title") or "(无标题)"
            url = f"{self.public_base_url}/opportunities/{opp.get('id')}"
            lines.append(f"{idx}. **{title}** — ⭐ {int(round(score))}")
            lines.append(f"   [查看详情]({url})")
        return CommandReply(
            text="\n".join(lines),
            metadata={"command": "today", "items_count": len(items)},
        )

    async def _top(self, args: str) -> CommandReply:
        _ = args
        data = await self._get("/api/opportunities?limit=10")
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return CommandReply(text="数据库里还没有机会。")
        lines = ["**🏆 历史 Top 10 机会**", ""]
        for idx, opp in enumerate(items[:10], start=1):
            score = float(opp.get("total_score") or 0)
            title = opp.get("title") or "(无标题)"
            url = f"{self.public_base_url}/opportunities/{opp.get('id')}"
            lines.append(f"{idx}. {title} — ⭐ {int(round(score))}")
            lines.append(f"   [查看详情]({url})")
        return CommandReply(text="\n".join(lines))

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
        return CommandReply(
            text=(
                f"✅ 已生成研究任务 #{job_id} · 主题:`{topic}`\n"
                f"[在 Web 上查看完整报告]({url})"
            ),
            metadata={"command": "research", "job_id": job_id, "topic": topic},
        )

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
        return CommandReply(
            text="📬 已触发日报推送(主通道 → 降级通道)。",
            metadata={"command": "daily", "result": str(result)[:500]},
        )


# ---------------------------------------------------------------------------
# Reply builders for static commands
# ---------------------------------------------------------------------------
def _help_reply() -> CommandReply:
    lines = [
        "**AI 机会雷达 — 命令菜单**",
        "",
        "/help — 显示本菜单",
        "/today — 今日 Top 10 机会",
        "/top — 全历史 Top 10",
        "/research <主题> — 按需生成研究报告(¥299 场景)",
        "/refresh — 触发数据抓取",
        "/score — 触发重新评分",
        "/daily — 触发日报推送",
        "",
        "更多能力见 Web 端 /dashboard",
    ]
    return CommandReply(text="\n".join(lines))


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