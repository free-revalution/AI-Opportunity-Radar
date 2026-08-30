"""Phase 26 — ``/docs`` sub-command dispatcher.

The bot's ``/docs`` family delegates here. Each sub-command
(``tree``, ``ls``, ``rm``, ``bitable:ls`` etc.) is one
``async def _handle_xxx`` function returning a
:class:`app.services.feishu.inbound.CommandReply`.

Layout
------

  * :class:`DocsSubcommand` — enum of all supported sub-commands.
  * :class:`DocsContext` — request-scoped bundle (managers + config +
    sender identity) threaded through the handlers.
  * :func:`run_docs_subcommand` — dispatcher; parses
    ``/docs <sub> <args>`` and routes to the right handler.
  * Helper renderers (``_chunk_reply``, ``_render_*``) keep the
    IM-reply size within Feishu's 4 000-character limit.

The dispatcher is **stateless** — every entry into
:func:`run_docs_subcommand` rebuilds a :class:`DocsContext` from
the request scope. ``DocsContext`` itself is cheap; managers hold
no per-request state.

Async vs synchronous
--------------------

``/docs tree`` is the only async sub-command: walking a deep tree
can exceed Feishu's 30 s per-event reply window. The handler
submits a background task via :func:`app.services.feishu.task_runner.submit_docs_tree_task`
and returns a ``task_id`` immediately. The other 15 sub-commands
are synchronous because they touch at most one Drive/Bitable
endpoint each.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, datetime
from enum import Enum
from typing import Any, Optional

from app.config import Settings
from app.services.feishu.bitable_manager import BitableManager
from app.services.feishu.confirm_store import (
    ConfirmStoreUnavailable,
    PendingAction,
)
from app.services.feishu.content_client import FeishuContentError
from app.services.feishu.drive_manager import DriveManager
from app.services.feishu.drive_org import (
    SECTION_DAILY,
    SECTION_HOME,
    SECTION_SOURCES,
    SECTION_TODAY,
)
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enum + context
# ---------------------------------------------------------------------------
class DocsSubcommand(str, Enum):
    TREE = "tree"
    LS = "ls"
    FIND = "find"
    DAILY = "daily"
    INFO = "info"
    CREATE = "create"
    MKDIR = "mkdir"
    MV = "mv"
    RENAME = "rename"
    RM = "rm"
    CONFIRM = "confirm"
    BITABLE_LS = "bitable:ls"
    BITABLE_FIND = "bitable:find"
    BITABLE_ADD = "bitable:add"
    BITABLE_RM = "bitable:rm"


# — Lookup table mapping user-facing strings to the enum. Order matters:
# the longer ``bitable:*`` aliases are checked first so ``/docs bitable
# ls`` doesn't resolve to ``LS``.
_SUBCOMMAND_ALK: dict[str, DocsSubcommand] = {
    "tree": DocsSubcommand.TREE,
    "ls": DocsSubcommand.LS,
    "list": DocsSubcommand.LS,
    "find": DocsSubcommand.FIND,
    "search": DocsSubcommand.FIND,
    "daily": DocsSubcommand.DAILY,
    "info": DocsSubcommand.INFO,
    "create": DocsSubcommand.CREATE,
    "mkdir": DocsSubcommand.MKDIR,
    "mv": DocsSubcommand.MV,
    "move": DocsSubcommand.MV,
    "rename": DocsSubcommand.RENAME,
    "mv-name": DocsSubcommand.RENAME,
    "rm": DocsSubcommand.RM,
    "delete": DocsSubcommand.RM,
    "confirm": DocsSubcommand.CONFIRM,
    "bitable:ls": DocsSubcommand.BITABLE_LS,
    "bitable:list": DocsSubcommand.BITABLE_LS,
    "bitable:find": DocsSubcommand.BITABLE_FIND,
    "bitable:search": DocsSubcommand.BITABLE_FIND,
    "bitable:add": DocsSubcommand.BITABLE_ADD,
    "bitable:rm": DocsSubcommand.BITABLE_RM,
    "bitable:delete": DocsSubcommand.BITABLE_RM,
}


def parse_docs_subcommand(text: str) -> tuple[DocsSubcommand, str]:
    """Parse the argument string after ``/docs`` into ``(sub, rest)``.

    ``text`` is the trimmed body (no leading ``/docs``). Empty /
    whitespace-only text returns ``(None, "")``-like behaviour:
    we return ``(TREE, "")`` so callers get the menu; they can
    tell "empty" by inspecting ``rest == ""`` and ``sub == TREE``.

    For any other unrecognised first token we return
    ``(None, text)`` so the dispatcher can surface a help reply.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return DocsSubcommand.TREE, ""
    head, _, tail = cleaned.partition(" ")
    head_lower = head.strip().lower()
    # — Try the long forms first so "bitable:ls" wins over plain "ls".
    for prefix in ("bitable:ls", "bitable:list", "bitable:find", "bitable:search",
                   "bitable:add", "bitable:rm", "bitable:delete"):
        if head_lower == prefix:
            return _SUBCOMMAND_ALK[prefix], tail.strip()
    sub = _SUBCOMMAND_ALK.get(head_lower)
    if sub is None:
        return DocsSubcommand.TREE, cleaned  # menu fallback
    return sub, tail.strip()


@dataclass(slots=True)
class DocsContext:
    """Per-request bundle for the /docs sub-commands."""

    drive_manager: DriveManager
    bitable_manager: BitableManager
    settings: Settings
    sender_open_id: Optional[str] = None
    chat_id: Optional[str] = None
    # — Phase 27 fix: redis_client is exposed so the `/docs tree`
    # async path can build an independent FeishuAppClient in the
    # background task (the inbound-handler-scoped client gets
    # aclose()'d before the background task finishes its walk).
    redis_client: Optional[Any] = None


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------
async def run_docs_subcommand(
    *,
    args: str,
    ctx: DocsContext,
) -> Any:
    """Dispatch ``/docs <sub> <args>`` to the right handler.

    ``args`` is the substring after ``/docs`` (already trimmed).
    Returns a :class:`CommandReply`-shaped object — duck-typed so
    this module doesn't need to import ``inbound.py`` (avoids a
    circular import: ``inbound`` imports ``docs_commands``).
    """
    sub, rest = parse_docs_subcommand(args)
    try:
        if sub == DocsSubcommand.TREE:
            return await _handle_tree(args=rest, ctx=ctx)
        if sub == DocsSubcommand.LS:
            return await _handle_ls(args=rest, ctx=ctx)
        if sub == DocsSubcommand.FIND:
            return await _handle_find(args=rest, ctx=ctx)
        if sub == DocsSubcommand.DAILY:
            return await _handle_daily(args=rest, ctx=ctx)
        if sub == DocsSubcommand.INFO:
            return await _handle_info(args=rest, ctx=ctx)
        if sub == DocsSubcommand.CREATE:
            return await _handle_create(args=rest, ctx=ctx)
        if sub == DocsSubcommand.MKDIR:
            return await _handle_mkdir(args=rest, ctx=ctx)
        if sub == DocsSubcommand.MV:
            return await _handle_mv(args=rest, ctx=ctx)
        if sub == DocsSubcommand.RENAME:
            return await _handle_rename(args=rest, ctx=ctx)
        if sub == DocsSubcommand.RM:
            return await _handle_rm(args=rest, ctx=ctx)
        if sub == DocsSubcommand.CONFIRM:
            return await _handle_confirm(args=rest, ctx=ctx)
        if sub == DocsSubcommand.BITABLE_LS:
            return await _handle_bitable_ls(args=rest, ctx=ctx)
        if sub == DocsSubcommand.BITABLE_FIND:
            return await _handle_bitable_find(args=rest, ctx=ctx)
        if sub == DocsSubcommand.BITABLE_ADD:
            return await _handle_bitable_add(args=rest, ctx=ctx)
        if sub == DocsSubcommand.BITABLE_RM:
            return await _handle_bitable_rm(args=rest, ctx=ctx)
        return _help_reply()
    except ConfirmStoreUnavailable as exc:
        return _err_reply(sub=sub, error=f"Redis 未就绪,破坏性操作已禁用: {exc}")
    except FeishuContentError as exc:
        return _err_reply(sub=sub, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "feishu_docs_subcommand_failed",
            sub=sub.value if isinstance(sub, DocsSubcommand) else None,
            error=str(exc)[:200],
            exc_info=True,
        )
        return _err_reply(sub=sub, error=f"内部错误: {exc}")


# ---------------------------------------------------------------------------
# Helpers — IM reply rendering
# ---------------------------------------------------------------------------
def _reply(
    text: str,
    *,
    sub: Optional[DocsSubcommand] = None,
    metadata: Optional[dict[str, Any]] = None,
):
    """Build a CommandReply-shaped dict (no inbound.py dependency).

    The inbound layer accepts anything that has ``.text`` and
    ``.metadata``; we build a tiny duck-typed object so this
    module has no inbound import.
    """
    md = {"command": "docs"}
    if sub is not None:
        md["subcommand"] = sub.value
    if metadata:
        md.update(metadata)
    return _Reply(text=text.strip() + "\n", metadata=md)


class _Reply:
    __slots__ = ("text", "card", "metadata")

    def __init__(self, text: str, metadata: dict[str, Any]) -> None:
        self.text = text
        self.card = None
        self.metadata = metadata


def _err_reply(*, sub: Optional[DocsSubcommand], error: str):
    return _reply(
        f"⚠️ {error}",
        sub=sub,
        metadata={"error": True, "error_message": error[:200]},
    )


def _chunk_text(text: str, *, max_chars: int = 3500) -> list[str]:
    """Split a long reply into ≤ ``max_chars`` chunks at line boundaries.

    Feishu IM rejects messages > 4 000 chars; we keep a safety margin.
    Each chunk keeps the trailing newline so concatenation is clean.
    """
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > max_chars:
        # — Try to split at the last newline before max_chars so we
        # don't cut mid-line.
        cut = rest.rfind("\n", 0, max_chars)
        if cut <= 0:
            cut = max_chars  # — no newline → hard cut.
        chunks.append(rest[:cut] + "\n")
        rest = rest[cut:]
    if rest:
        chunks.append(rest)
    return chunks


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def _handle_tree(*, args: str, ctx: DocsContext):
    """``/docs tree`` — async via :func:`submit_docs_tree_task`.

    The handler returns a task_id immediately so the bot replies
    within Feishu's 30s window. The background task posts a
    multi-card reply with the walked tree.
    """
    if args:
        # — Tree takes no args; ignore extras.
        logger.debug("feishu_docs_tree_extra_args", extra=args[:60])
    try:
        from app.services.feishu.task_runner import submit_docs_tree_task
    except ImportError as exc:  # pragma: no cover
        return _err_reply(
            sub=DocsSubcommand.TREE, error=f"task runner unavailable: {exc}"
        )
    record = await submit_docs_tree_task(
        chat_id=ctx.chat_id or "",
        sender_open_id=ctx.sender_open_id or "",
        settings=ctx.settings,
        redis_client=ctx.redis_client,
    )
    return _reply(
        (
            f"🌳 树状结构后台生成中（task_id={record.task_id}）。\n"
            "完成后会自动回推到本对话。"
        ),
        sub=DocsSubcommand.TREE,
        metadata={"task_id": record.task_id, "async": True},
    )


async def _handle_ls(*, args: str, ctx: DocsContext):
    """``/docs ls [section]`` — list a section's direct children."""
    section_arg = args.strip() or SECTION_TODAY
    items = await ctx.drive_manager.list_section(section=section_arg)
    if not items:
        return _reply(
            f"📂 `{section_arg}` 下没有内容。",
            sub=DocsSubcommand.LS,
            metadata={"section": section_arg, "count": 0},
        )
    lines = [f"📂 **{section_arg}** ({len(items)} 个)", ""]
    for it in items[:30]:
        kind = it.get("type") or "?"
        icon = "📁" if kind == "folder" else "📄"
        name = it.get("name") or "(未命名)"
        token = it.get("token") or ""
        lines.append(f"{icon} {name}  `token={token[:12]}…`")
    if len(items) > 30:
        lines.append("")
        lines.append(f"…还有 {len(items) - 30} 个未显示。")
    return _reply(
        "\n".join(lines),
        sub=DocsSubcommand.LS,
        metadata={"section": section_arg, "count": len(items)},
    )


async def _handle_find(*, args: str, ctx: DocsContext):
    """``/docs find <keyword>`` — substring search across 4 sections."""
    keyword = args.strip()
    if not keyword:
        return _err_reply(sub=DocsSubcommand.FIND, error="用法:/docs find <关键词>")
    items = await ctx.drive_manager.find_files(keyword=keyword, limit=20)
    if not items:
        return _reply(
            f"🔍 没有找到包含 `{keyword}` 的文件。",
            sub=DocsSubcommand.FIND,
            metadata={"keyword": keyword, "count": 0},
        )
    lines = [f"🔍 找到 {len(items)} 个匹配 `{keyword}`:", ""]
    for it in items:
        icon = "📁" if it.get("type") == "folder" else "📄"
        name = it.get("name") or "(未命名)"
        section = it.get("section") or "?"
        token = it.get("token") or ""
        lines.append(f"{icon} [{section}] {name}  `token={token[:12]}…`")
    return _reply(
        "\n".join(lines),
        sub=DocsSubcommand.FIND,
        metadata={"keyword": keyword, "count": len(items)},
    )


async def _handle_daily(*, args: str, ctx: DocsContext):
    """``/docs daily [YYYY-MM-DD]`` — fetch a day's Docx ref."""
    arg = args.strip()
    if arg:
        try:
            target = datetime.strptime(arg, "%Y-%m-%d").date()
        except ValueError as exc:
            return _err_reply(
                sub=DocsSubcommand.DAILY,
                error=f"日期格式应为 YYYY-MM-DD: {exc}",
            )
    else:
        target = DateType.today()
    # — Reuse DriveOrgService.get_daily_doc for the DB lookup.
    from app.services.feishu.drive_org import DriveOrgService

    org = DriveOrgService(
        drive=ctx.drive_manager.drive, settings=ctx.settings
    )
    if org.session is None:
        # — Without a session, fall back to a no-DB message.
        return _reply(
            f"📅 {target}:无法查询（DB session 不可用）。",
            sub=DocsSubcommand.DAILY,
            metadata={"date": str(target), "found": False},
        )
    row = await org.get_daily_doc(target)
    if row is None:
        return _reply(
            f"📅 {target}: 尚未生成日报。",
            sub=DocsSubcommand.DAILY,
            metadata={"date": str(target), "found": False},
        )
    return _reply(
        (
            f"📅 **{target}**\n"
            f"🔗 {row.doc_url}\n"
            f"doc_id: `{row.doc_id}`\n"
            f"采集 {row.raw_count} / 信号 {row.signal_count}\n"
            f"run_id: {row.run_id or '-'}"
        ),
        sub=DocsSubcommand.DAILY,
        metadata={"date": str(target), "found": True, "doc_id": row.doc_id},
    )


async def _handle_info(*, args: str, ctx: DocsContext):
    """``/docs info <path>`` — metadata + URL for one node."""
    path = args.strip()
    if not path:
        return _err_reply(sub=DocsSubcommand.INFO, error="用法:/docs info <路径>")
    node = await ctx.drive_manager.resolve(path=path)
    if node is None:
        return _err_reply(sub=DocsSubcommand.INFO, error=f"路径未找到: {path!r}")
    metas = await ctx.drive_manager.drive.get_file_meta(
        file_tokens=[node.token], file_type=node.type or "folder"
    )
    if not metas:
        return _reply(
            (
                f"ℹ️ {node.path}\n"
                f"类型: {node.type}\n"
                f"token: `{node.token}`"
            ),
            sub=DocsSubcommand.INFO,
            metadata={"path": node.path},
        )
    m = metas[0]
    created = m.get("created_time") or "?"
    url = m.get("url") or "(无 URL)"
    return _reply(
        (
            f"ℹ️ **{node.path}**\n"
            f"类型: {m.get('type') or node.type}\n"
            f"token: `{node.token}`\n"
            f"创建时间: {created}\n"
            f"🔗 {url}"
        ),
        sub=DocsSubcommand.INFO,
        metadata={"path": node.path, "token": node.token, "type": m.get("type")},
    )


async def _handle_create(*, args: str, ctx: DocsContext):
    """``/docs create <name> [section]`` — sub-folder in a section."""
    parts = args.split(maxsplit=1)
    name = parts[0].strip() if parts else ""
    section = parts[1].strip() if len(parts) > 1 else SECTION_TODAY
    if not name:
        return _err_reply(sub=DocsSubcommand.CREATE, error="用法:/docs create <名称> [段]")
    try:
        result = await ctx.drive_manager.create_child_folder(
            section=section, name=name
        )
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.CREATE, error=str(exc))
    return _reply(
        (
            f"✅ 子文件夹已创建: **{name}**\n"
            f"位于: {section}\n"
            f"token: `{result['token']}`"
        ),
        sub=DocsSubcommand.CREATE,
        metadata={"section": section, "name": name, "token": result["token"]},
    )


async def _handle_mkdir(*, args: str, ctx: DocsContext):
    """``/docs mkdir <a/b/c>`` — recursive folder path."""
    path = args.strip()
    if not path:
        return _err_reply(sub=DocsSubcommand.MKDIR, error="用法:/docs mkdir <路径>")
    try:
        result = await ctx.drive_manager.mkdir_path(path=path)
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.MKDIR, error=str(exc))
    return _reply(
        f"✅ 目录已创建: **{path}**\ntoken: `{result['token']}`",
        sub=DocsSubcommand.MKDIR,
        metadata={"path": path, "token": result["token"]},
    )


async def _handle_mv(*, args: str, ctx: DocsContext):
    """``/docs mv <path> <section>`` — move a node into a section.

    Path must resolve to an existing node; ``section`` is the
    target display name or English key.
    """
    parts = args.split()
    if len(parts) < 2:
        return _err_reply(
            sub=DocsSubcommand.MV,
            error="用法:/docs mv <路径> <目标段>",
        )
    path = " ".join(parts[:-1])
    target_section = parts[-1].strip()
    node = await ctx.drive_manager.resolve(path=path)
    if node is None:
        return _err_reply(sub=DocsSubcommand.MV, error=f"路径未找到: {path!r}")
    try:
        result = await ctx.drive_manager.move_to_section(
            file_token=node.token,
            file_type=node.type,
            target_section=target_section,
        )
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.MV, error=str(exc))
    return _reply(
        (
            f"✅ 已移动 **{path}** → {target_section}\n"
            f"目标 token: `{result['target_folder_token']}`"
        ),
        sub=DocsSubcommand.MV,
        metadata={
            "path": path,
            "target_section": target_section,
            "token": node.token,
        },
    )


async def _handle_rename(*, args: str, ctx: DocsContext):
    """``/docs rename <path> <new_name>``."""
    parts = args.split()
    if len(parts) < 2:
        return _err_reply(
            sub=DocsSubcommand.RENAME,
            error="用法:/docs rename <路径> <新名>",
        )
    new_name = parts[-1].strip()
    path = " ".join(parts[:-1])
    node = await ctx.drive_manager.resolve(path=path)
    if node is None:
        return _err_reply(
            sub=DocsSubcommand.RENAME, error=f"路径未找到: {path!r}"
        )
    try:
        result = await ctx.drive_manager.rename(
            file_token=node.token,
            file_type=node.type,
            new_name=new_name,
        )
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.RENAME, error=str(exc))
    return _reply(
        f"✅ 已重命名 → **{new_name}**",
        sub=DocsSubcommand.RENAME,
        metadata={"old_path": path, "new_name": new_name},
    )


async def _handle_rm(*, args: str, ctx: DocsContext):
    """``/docs rm <path>`` — STAGE a delete; reply with a token.

    Does NOT touch Feishu. The operator must reply
    ``/docs confirm <token>`` to actually run the delete.
    """
    path = args.strip()
    if not path:
        return _err_reply(sub=DocsSubcommand.RM, error="用法:/docs rm <路径>")
    if ctx.drive_manager.confirm_store is None:
        return _err_reply(
            sub=DocsSubcommand.RM,
            error="ConfirmStore 未配置,无法启用删除（需要 Redis）",
        )
    try:
        action = await ctx.drive_manager.request_delete(path=path)
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.RM, error=str(exc))
    payload = action.payload
    return _reply(
        (
            f"⚠️ 准备删除 **{payload.get('path')}**\n"
            f"类型: {payload.get('type')}\n"
            f"token: `{payload.get('token')}`\n\n"
            f"如确认删除,请在 60 秒内回复:\n"
            f"`/docs confirm {action.action_id}`"
        ),
        sub=DocsSubcommand.RM,
        metadata={
            "action_id": action.action_id,
            "kind": action.kind,
            "expires_at": action.expires_at,
            "path": payload.get("path"),
        },
    )


async def _handle_confirm(*, args: str, ctx: DocsContext):
    """``/docs confirm <token>`` — actually run the staged action."""
    action_id = args.strip().split()[0] if args.strip() else ""
    if not action_id:
        return _err_reply(
            sub=DocsSubcommand.CONFIRM,
            error="用法:/docs confirm <token>",
        )
    if ctx.drive_manager.confirm_store is None and ctx.bitable_manager.confirm_store is None:
        return _err_reply(
            sub=DocsSubcommand.CONFIRM,
            error="ConfirmStore 未配置",
        )
    # — Try both stores (drive + bitable share the same instance in
    # production, but tests might wire them separately).
    store = (
        ctx.drive_manager.confirm_store or ctx.bitable_manager.confirm_store
    )
    action = await store.consume(action_id)
    if action is None:
        return _err_reply(
            sub=DocsSubcommand.CONFIRM,
            error=f"token 已过期或不存在: `{action_id[:12]}`",
        )
    try:
        if action.kind == "drive_delete":
            outcome = await ctx.drive_manager.execute_delete(action=action)
        elif action.kind == "bitable_rm":
            outcome = await ctx.bitable_manager.execute_delete(action=action)
        else:
            return _err_reply(
                sub=DocsSubcommand.CONFIRM,
                error=f"未知操作类型: {action.kind}",
            )
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.CONFIRM, error=str(exc))
    return _reply(
        (
            f"✅ 删除完成（token `{action_id[:12]}`）。\n"
            f"类型: {action.kind}\n"
            f"结果: {outcome}"
        ),
        sub=DocsSubcommand.CONFIRM,
        metadata={
            "action_id": action_id,
            "kind": action.kind,
            "outcome": _safe_truncate(outcome, max_chars=200),
        },
    )


# ---------------------------------------------------------------------------
# Bitable handlers
# ---------------------------------------------------------------------------
async def _handle_bitable_ls(*, args: str, ctx: DocsContext):
    """``/docs bitable ls`` — list tables in the configured Bitable."""
    try:
        tables = await ctx.bitable_manager.list_tables()
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.BITABLE_LS, error=str(exc))
    if not tables:
        return _reply(
            "📋 Bitable 暂无表格（请先在 `.env` 配置 `FEISHU_BITABLE_APP_TOKEN`）。",
            sub=DocsSubcommand.BITABLE_LS,
            metadata={"count": 0},
        )
    lines = [f"📋 **{len(tables)} 个表格**", ""]
    for t in tables:
        name = t.get("name") or "(未命名)"
        tid = t.get("table_id") or ""
        lines.append(f"📄 {name}  `table_id={tid[:12]}…`")
    return _reply(
        "\n".join(lines),
        sub=DocsSubcommand.BITABLE_LS,
        metadata={"count": len(tables)},
    )


async def _handle_bitable_find(*, args: str, ctx: DocsContext):
    """``/docs bitable find <keyword> [table]``."""
    parts = args.split(maxsplit=1)
    keyword = parts[0].strip() if parts else ""
    table_name = parts[1].strip() if len(parts) > 1 else None
    if not keyword:
        return _err_reply(
            sub=DocsSubcommand.BITABLE_FIND,
            error="用法:/docs bitable find <关键词> [table]",
        )
    try:
        items = await ctx.bitable_manager.find_records(
            table_name=table_name, keyword=keyword, limit=10
        )
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.BITABLE_FIND, error=str(exc))
    if not items:
        return _reply(
            f"🔍 Bitable 中没有匹配 `{keyword}` 的记录。",
            sub=DocsSubcommand.BITABLE_FIND,
            metadata={"keyword": keyword, "count": 0},
        )
    lines = [f"🔍 Bitable 找到 {len(items)} 条:", ""]
    for rec in items:
        rid = rec.get("record_id") or ""
        matched = rec.get("matched_field") or "?"
        snippet = _safe_truncate(rec.get("fields") or {}, max_chars=120)
        lines.append(f"📌 `{rid[:12]}…` matched `{matched}` — {snippet}")
    return _reply(
        "\n".join(lines),
        sub=DocsSubcommand.BITABLE_FIND,
        metadata={"keyword": keyword, "count": len(items)},
    )


async def _handle_bitable_add(*, args: str, ctx: DocsContext):
    """``/docs bitable add <table> key=value;k2=v2``."""
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return _err_reply(
            sub=DocsSubcommand.BITABLE_ADD,
            error="用法:/docs bitable add <table> key=value;k2=v2",
        )
    table_name = parts[0].strip()
    payload_str = parts[1]
    fields = parse_bitable_fields(payload_str)
    if not fields:
        return _err_reply(
            sub=DocsSubcommand.BITABLE_ADD,
            error="字段格式错误,示例:Title=AI雷达;Score=88",
        )
    try:
        rec = await ctx.bitable_manager.add_record(
            table_name=table_name, fields=fields
        )
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.BITABLE_ADD, error=str(exc))
    return _reply(
        (
            f"✅ 已新增记录到 **{table_name}**\n"
            f"record_id: `{rec.get('record_id')}`"
        ),
        sub=DocsSubcommand.BITABLE_ADD,
        metadata={
            "table": table_name,
            "record_id": rec.get("record_id"),
            "fields": _safe_truncate(fields, max_chars=200),
        },
    )


async def _handle_bitable_rm(*, args: str, ctx: DocsContext):
    """``/docs bitable rm <record_id> [table]`` — STAGE a delete."""
    parts = args.split(maxsplit=1)
    record_id = parts[0].strip() if parts else ""
    table_name = parts[1].strip() if len(parts) > 1 else None
    if not record_id:
        return _err_reply(
            sub=DocsSubcommand.BITABLE_RM,
            error="用法:/docs bitable rm <record_id> [table]",
        )
    if ctx.bitable_manager.confirm_store is None:
        return _err_reply(
            sub=DocsSubcommand.BITABLE_RM,
            error="ConfirmStore 未配置,无法启用删除（需要 Redis）",
        )
    try:
        action = await ctx.bitable_manager.request_delete(
            record_id=record_id, table_name=table_name
        )
    except FeishuContentError as exc:
        return _err_reply(sub=DocsSubcommand.BITABLE_RM, error=str(exc))
    return _reply(
        (
            f"⚠️ 准备删除 Bitable 记录\n"
            f"record_id: `{record_id}`\n"
            f"table: {table_name or '(默认)'}\n\n"
            f"如确认删除,请在 60 秒内回复:\n"
            f"`/docs confirm {action.action_id}`"
        ),
        sub=DocsSubcommand.BITABLE_RM,
        metadata={
            "action_id": action.action_id,
            "record_id": record_id,
            "table": table_name,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_bitable_fields(payload: str) -> dict[str, str]:
    """Parse ``"key=value;key2=value2"`` → ``{"key": "value", ...}``.

    Tolerates ``"="`` inside values, splits only on ``";"``.
    Returns ``{}`` on empty / malformed input.
    """
    if not payload or not payload.strip():
        return {}
    out: dict[str, str] = {}
    for chunk in payload.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = value.strip()
    return out


def _safe_truncate(obj: Any, *, max_chars: int) -> str:
    """Best-effort ``str()`` then truncate — used to keep error
    payloads from blowing the IM character limit."""
    try:
        s = str(obj)
    except Exception:  # noqa: BLE001
        s = repr(obj)
    return s[:max_chars]


def _help_reply():
    """Top-level ``/docs`` help — rendered when args don't match a
    recognised sub-command, or when the user just types ``/docs``."""
    return _reply(
        "\n".join(
            [
                "**📁 /docs 命令菜单（管理员）**",
                "",
                "/docs tree — 4 段结构（异步生成）",
                "/docs ls [段] — 列出某段下的内容（默认 今日）",
                "/docs find <关键词> — 跨段搜索文件名",
                "/docs daily [YYYY-MM-DD] — 某天的日报",
                "/docs info <路径> — 文件元信息 + URL",
                "/docs create <名> [段] — 在某段下建子文件夹",
                "/docs mkdir <段/a/b> — 递归建多级目录",
                "/docs mv <路径> <段> — 移动到另一段",
                "/docs rename <路径> <新名> — 重命名",
                "/docs rm <路径> — 准备删除（返 token）",
                "/docs confirm <token> — 执行删除",
                "",
                "**多维表格（Bitable）**",
                "/docs bitable ls — 列表",
                "/docs bitable find <kw> [table] — 搜索",
                "/docs bitable add <table> k=v;k2=v2 — 新增",
                "/docs bitable rm <record_id> [table] — 准备删除",
                "",
                "破坏性操作需要 60 秒内二次确认。仅 `ADMIN_OPEN_IDS` 可用。",
            ]
        ),
        sub=None,
        metadata={"help": True},
    )


__all__ = [
    "DocsContext",
    "DocsSubcommand",
    "parse_bitable_fields",
    "parse_docs_subcommand",
    "run_docs_subcommand",
]
