"""Phase 26 — /docs sub-command dispatcher tests.

Covers parse_docs_subcommand, parse_bitable_fields, and the
handler happy-paths via the in-memory fake managers. Does NOT
exercise the inbound router directly (that path needs httpx +
FeishuAppClient — covered by tests/test_inbound.py if added).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.feishu.docs_commands import (
    DocsSubcommand,
    parse_bitable_fields,
    parse_docs_subcommand,
    run_docs_subcommand,
    DocsContext,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_parse_empty_returns_menu() -> None:
    sub, rest = parse_docs_subcommand("")
    assert sub == DocsSubcommand.TREE
    assert rest == ""


def test_parse_ls() -> None:
    sub, rest = parse_docs_subcommand("ls 📅 今日")
    assert sub == DocsSubcommand.LS
    assert rest == "📅 今日"


def test_parse_ls_with_english_alias() -> None:
    sub, rest = parse_docs_subcommand("list today")
    assert sub == DocsSubcommand.LS
    assert rest == "today"


def test_parse_find() -> None:
    sub, rest = parse_docs_subcommand("find AI 报告")
    assert sub == DocsSubcommand.FIND
    assert rest == "AI 报告"


def test_parse_rm() -> None:
    sub, rest = parse_docs_subcommand("rm 📁 每日报告/old.docx")
    assert sub == DocsSubcommand.RM
    assert rest == "📁 每日报告/old.docx"


def test_parse_bitable_ls_wins_over_plain_ls() -> None:
    sub, rest = parse_docs_subcommand("bitable:ls")
    assert sub == DocsSubcommand.BITABLE_LS
    assert rest == ""


def test_parse_bitable_find_with_table() -> None:
    sub, rest = parse_docs_subcommand("bitable:find AI Opportunities")
    assert sub == DocsSubcommand.BITABLE_FIND
    assert rest == "AI Opportunities"


def test_parse_bitable_add() -> None:
    sub, rest = parse_docs_subcommand("bitable:add Opportunities Title=AI雷达")
    assert sub == DocsSubcommand.BITABLE_ADD
    assert rest == "Opportunities Title=AI雷达"


def test_parse_unknown_returns_menu() -> None:
    sub, rest = parse_docs_subcommand("garbage 123")
    assert sub == DocsSubcommand.TREE
    assert rest  # echo back so caller can error


# ---------------------------------------------------------------------------
# parse_bitable_fields
# ---------------------------------------------------------------------------
def test_parse_bitable_fields_simple() -> None:
    out = parse_bitable_fields("Title=AI雷达;Score=88")
    assert out == {"Title": "AI雷达", "Score": "88"}


def test_parse_bitable_fields_with_equals_in_value() -> None:
    out = parse_bitable_fields("URL=https://x?a=1;b=2")
    assert out == {"URL": "https://x?a=1", "b": "2"}


def test_parse_bitable_fields_empty() -> None:
    assert parse_bitable_fields("") == {}


def test_parse_bitable_fields_ignores_malformed() -> None:
    out = parse_bitable_fields("good=x;no-equals;bad=;also=ok")
    # — "no-equals" dropped (no `=`). "bad=" kept as key "bad" → ""
    # (only empty *keys* are rejected). "good" / "also" round-trip.
    assert out == {"good": "x", "bad": "", "also": "ok"}


# ---------------------------------------------------------------------------
# Handler happy paths via fake managers
# ---------------------------------------------------------------------------
class _FakeDriveManager:
    def __init__(self) -> None:
        self.ensure_tree_calls = 0
        self.list_section_calls: list[str] = []
        self.find_files_calls: list[str] = []
        self.created: list[tuple[str, str]] = []
        self.mkdir_calls: list[str] = []
        self.mv_calls: list[dict[str, Any]] = []
        self.rename_calls: list[dict[str, Any]] = []
        self._confirm_store: Any = None

    async def ensure_tree(self) -> dict[str, str]:
        self.ensure_tree_calls += 1
        return {"root": "r", "home": "h", "today": "t", "daily_reports": "d", "sources": "s"}

    async def walk(self, *, max_depth: int = 3) -> dict[str, Any]:
        return {
            "name": "root",
            "token": "r",
            "type": "folder",
            "children": [
                {"name": "📅 今日", "token": "t", "type": "folder", "children": []},
                {"name": "📚 信息源", "token": "s", "type": "folder", "children": []},
            ],
        }

    async def resolve(self, *, path: str) -> Any:
        if not path:
            return None
        return type(
            "Node", (), {"name": path.split("/")[-1], "token": f"tok_{path}", "type": "folder", "path": path}
        )()

    async def list_section(self, *, section: str, limit: int = 30) -> list[dict[str, Any]]:
        self.list_section_calls.append(section)
        return [{"name": "demo.txt", "token": "abc", "type": "file"}]

    async def find_files(self, *, keyword: str, scope: str = "all", limit: int = 20) -> list[dict[str, Any]]:
        self.find_files_calls.append(keyword)
        return [{"name": f"{keyword}.docx", "token": "x", "type": "docx", "section": "📅 今日"}]

    async def create_child_folder(self, *, section: str, name: str) -> dict[str, Any]:
        self.created.append((section, name))
        return {"token": "new", "section": section, "name": name}

    async def mkdir_path(self, *, path: str) -> dict[str, Any]:
        self.mkdir_calls.append(path)
        return {"token": "new_path", "path": path}

    async def move_to_section(self, *, file_token: str, file_type: str, target_section: str) -> dict[str, Any]:
        self.mv_calls.append({"file_token": file_token, "target_section": target_section})
        return {"file_token": file_token, "target_folder_token": "target"}

    async def rename(self, *, file_token: str, file_type: str, new_name: str) -> dict[str, Any]:
        self.rename_calls.append({"file_token": file_token, "new_name": new_name})
        return {"file_token": file_token, "name": new_name}

    async def request_delete(self, *, path: str) -> Any:
        from app.services.feishu.confirm_store import PendingAction

        return PendingAction(
            action_id="abc1234567",
            kind="drive_delete",
            payload={"path": path, "token": "tok", "type": "folder", "name": "x"},
            created_at=0.0,
            expires_at=60.0,
        )

    async def execute_delete(self, *, action: Any) -> dict[str, Any]:
        return {"status": "success", "action_id": action.action_id}

    @property
    def confirm_store(self) -> Any:
        return self._confirm_store

    @property
    def drive(self) -> Any:
        class _MiniDrive:
            async def get_file_meta(self, *, file_tokens, file_type="folder"):
                return [{"token": t, "type": file_type, "url": f"https://x/{t}"}]
        return _MiniDrive()


class _FakeBitableManager:
    def __init__(self) -> None:
        self.list_calls = 0
        self.find_calls: list[tuple[str, str | None]] = []
        self.add_calls: list[dict[str, Any]] = []
        self.delete_calls: list[str] = []
        self._confirm_store: Any = None

    async def list_tables(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [{"table_id": "tb1", "name": "Opportunities"}]

    async def find_records(self, *, table_name=None, keyword="", limit=10, table_id=None):
        self.find_calls.append((keyword, table_name))
        return [{"record_id": "rec_001", "fields": {"Title": keyword}, "matched_field": "Title"}]

    async def add_record(self, *, table_name=None, fields, table_id=None) -> dict[str, Any]:
        self.add_calls.append({"table": table_name, "fields": fields})
        return {"record_id": "rec_new", "fields": fields}

    async def request_delete(self, *, record_id, table_name=None, table_id=None) -> Any:
        from app.services.feishu.confirm_store import PendingAction

        return PendingAction(
            action_id="bitable_abc",
            kind="bitable_rm",
            payload={"record_id": record_id, "table_id": "tb1"},
            created_at=0.0,
            expires_at=60.0,
        )

    async def execute_delete(self, *, action) -> dict[str, Any]:
        return {"record_id": action.payload.get("record_id"), "deleted": True}

    @property
    def confirm_store(self) -> Any:
        return self._confirm_store


@pytest.fixture
def ctx() -> DocsContext:
    from app.config import get_settings
    from app.services.feishu.confirm_store import ConfirmStore
    from tests.test_feishu_confirm_store import _FakeRedis

    s = get_settings()
    s.feishu_drive_root_folder_token = "root"
    drive = _FakeDriveManager()
    bitable = _FakeBitableManager()
    # — Both managers share the same ConfirmStore — the docs_commands
    # handler rejects destructive ops when either manager sees None.
    store = ConfirmStore(redis_client=_FakeRedis(), ttl_sec=60)
    drive._confirm_store = store  # type: ignore[attr-defined]
    bitable._confirm_store = store  # type: ignore[attr-defined]
    return DocsContext(
        drive_manager=drive,  # type: ignore[arg-type]
        bitable_manager=bitable,  # type: ignore[arg-type]
        settings=s,
        sender_open_id="ou_admin",
        chat_id="oc_chat",
    )


# ---------------------------------------------------------------------------
# ls / find / create / mkdir / mv / rename / rm
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_ls(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(args="ls 📅 今日", ctx=ctx)
    assert "📂" in reply.text or "demo.txt" in reply.text
    assert reply.metadata["subcommand"] == "ls"


@pytest.mark.asyncio
async def test_run_find(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(args="find AI", ctx=ctx)
    assert "AI.docx" in reply.text or "匹配" in reply.text


@pytest.mark.asyncio
async def test_run_create(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(args="create foo 📅 今日", ctx=ctx)
    assert "已创建" in reply.text
    assert ("📅 今日", "foo") in ctx.drive_manager.created  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_mkdir(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(
        args="mkdir 📁 每日报告/2026-08-30", ctx=ctx
    )
    assert "目录已创建" in reply.text or "✅" in reply.text


@pytest.mark.asyncio
async def test_run_mv(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(
        args="mv 📅 今日/foo 📚 信息源", ctx=ctx
    )
    assert ctx.drive_manager.mv_calls  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_rename(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(
        args="rename 📅 今日/foo new_name", ctx=ctx
    )
    assert ctx.drive_manager.rename_calls  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_rm_returns_confirm_token(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(args="rm 📅 今日/foo", ctx=ctx)
    assert "确认删除" in reply.text
    assert "abc1234567" in reply.text
    assert reply.metadata["action_id"] == "abc1234567"


@pytest.mark.asyncio
async def test_run_tree_submits_async_task(
    ctx: DocsContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tree delegates to submit_docs_tree_task — we just verify the
    handler builds a CommandReply with task_id metadata."""
    from app.services.feishu import task_runner as tr
    from app.services.feishu.task_runner import TaskRecord

    async def fake_submit(
        *,
        chat_id,
        sender_open_id,
        settings=None,
        redis_client=None,
        receive_id_type="chat_id",
    ):
        return TaskRecord(
            task_id="tree_task_001",
            submitted_at=0.0,
            chat_id=chat_id,
            sender_open_id=sender_open_id,
            command_kind="docs_tree",
            receive_id_type=receive_id_type,
        )

    monkeypatch.setattr(tr, "submit_docs_tree_task", fake_submit)
    reply = await run_docs_subcommand(args="tree", ctx=ctx)
    assert "tree_task_001" in reply.text
    assert reply.metadata["async"] is True


# ---------------------------------------------------------------------------
# Bitable sub-commands
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_bitable_ls(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(args="bitable:ls", ctx=ctx)
    assert "Opportunities" in reply.text


@pytest.mark.asyncio
async def test_run_bitable_find(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(
        args="bitable:find AI Opportunities", ctx=ctx
    )
    assert ctx.bitable_manager.find_calls  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_bitable_add_parses_fields(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(
        args="bitable:add Opportunities Title=AI雷达;Score=88", ctx=ctx
    )
    fields = ctx.bitable_manager.add_calls[0]["fields"]  # type: ignore[attr-defined]
    assert fields["Title"] == "AI雷达"
    assert fields["Score"] == "88"


@pytest.mark.asyncio
async def test_run_bitable_rm_returns_confirm(ctx: DocsContext) -> None:
    reply = await run_docs_subcommand(
        args="bitable:rm rec_123 Opportunities", ctx=ctx
    )
    assert "bitable_abc" in reply.text


# ---------------------------------------------------------------------------
# Confirm flow
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_confirm_executes_pending(ctx: DocsContext) -> None:
    """Wire a ConfirmStore with one pre-staged action."""
    import json
    import time as _time

    from app.services.feishu.confirm_store import ConfirmStore, PendingAction

    fake_redis = AsyncMock()
    pending = PendingAction(
        action_id="aaaa1111",
        kind="drive_delete",
        payload={"path": "📅 今日/foo", "token": "tok", "type": "folder", "name": "foo"},
        created_at=_time.time(),
        expires_at=_time.time() + 60,
    )
    fake_redis.getdel = AsyncMock(return_value=json.dumps({
        "action_id": pending.action_id,
        "kind": pending.kind,
        "payload": pending.payload,
        "created_at": pending.created_at,
        "expires_at": pending.expires_at,
    }))
    store = ConfirmStore(redis_client=fake_redis, ttl_sec=60)
    # — Manually wire both managers.
    ctx.drive_manager._confirm_store = store  # type: ignore[attr-defined]
    ctx.bitable_manager._confirm_store = store  # type: ignore[attr-defined]
    reply = await run_docs_subcommand(args="confirm aaaa1111", ctx=ctx)
    assert "删除完成" in reply.text
    assert reply.metadata["kind"] == "drive_delete"


@pytest.mark.asyncio
async def test_run_confirm_unknown_token(ctx: DocsContext) -> None:
    import json

    from app.services.feishu.confirm_store import ConfirmStore

    fake_redis = AsyncMock()
    fake_redis.getdel = AsyncMock(return_value=None)
    store = ConfirmStore(redis_client=fake_redis)
    ctx.drive_manager._confirm_store = store  # type: ignore[attr-defined]
    ctx.bitable_manager._confirm_store = store  # type: ignore[attr-defined]
    reply = await run_docs_subcommand(args="confirm missing00", ctx=ctx)
    assert "已过期或不存在" in reply.text
