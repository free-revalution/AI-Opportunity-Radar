"""Phase 26 — DriveManager tests.

Covers walk / resolve / list_section / find_files / create_child /
mkdir_path / move / rename / request_delete / execute_delete / within_root.
Uses an in-memory fake FeishuDriveClient — same shape as the real one
but no HTTP. Drives DriveOrgService through real code paths.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.services.feishu.confirm_store import (
    ConfirmStore,
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


# ---------------------------------------------------------------------------
# Fake drive — same shape as FeishuDriveClient for tests
# ---------------------------------------------------------------------------
class FakeDrive:
    def __init__(self, *, settings: Any | None = None) -> None:
        from app.config import get_settings

        self.settings = settings or get_settings()
        self._folders: dict[tuple[str, str], str] = {}
        self._counter = 0
        self.deleted: list[tuple[str, str]] = []

    @property
    def is_configured(self) -> bool:
        return bool(self.folder_token)

    @property
    def folder_token(self) -> str:
        return self.settings.feishu_drive_root_folder_token

    async def ensure_folder_path(
        self, *, parent_token: Optional[str], path: list[str]
    ) -> str:
        cur = parent_token or self.folder_token
        for name in path:
            existing = await self.find_child_by_name(
                folder_token=cur, name=name
            )
            if existing:
                cur = existing
                continue
            cur = await self.create_folder(parent_token=cur, name=name)
        return cur

    async def create_folder(self, *, name: str, parent_token: Optional[str] = None) -> str:
        parent = parent_token or self.folder_token
        self._counter += 1
        tok = f"fld_{self._counter}_{name[:6]}"
        self._folders[(parent, name)] = tok
        return tok

    async def list_children(self, *, folder_token: str) -> list[dict[str, Any]]:
        return [
            {"token": tok, "name": name, "type": "folder"}
            for (parent, name), tok in self._folders.items()
            if parent == folder_token
        ]

    async def find_child_by_name(
        self, *, folder_token: str, name: str
    ) -> Optional[str]:
        for (parent, n), tok in self._folders.items():
            if parent == folder_token and n == name:
                return tok
        return None

    async def get_file_meta(
        self, *, file_tokens: list[str], file_type: str = "folder"
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tok in file_tokens:
            out.append({"token": tok, "type": file_type, "url": f"https://x/{tok}"})
        return out

    async def move_file(
        self, *, file_token: str, target_folder_token: str, file_type: str = "folder"
    ) -> dict[str, Any]:
        return {"file_token": file_token, "target_folder_token": target_folder_token}

    async def rename_file(
        self, *, file_token: str, new_name: str, file_type: str = "folder"
    ) -> dict[str, Any]:
        return {"file_token": file_token, "name": new_name}

    async def delete_file(
        self, *, file_token: str, file_type: str = "folder"
    ) -> dict[str, Any]:
        self.deleted.append((file_token, file_type))
        return {"task_id": f"task_{file_token}", "file_token": file_token}

    async def poll_delete_task(
        self, *, task_id: str, timeout_sec: float = 60.0, interval_sec: float = 1.5
    ) -> dict[str, Any]:
        return {"status": "success", "raw": {}}

    async def search_files(
        self, *, folder_token: Optional[str] = None, keyword: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        target = folder_token or self.folder_token
        for (parent, name), tok in self._folders.items():
            if parent != target:
                continue
            if keyword.lower() in name.lower():
                out.append({"token": tok, "name": name, "type": "folder"})
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def getdel(self, key: str) -> str | None:
        v = self.store.pop(key, None)
        return v


@pytest.fixture
def fake_drive() -> FakeDrive:
    from app.config import get_settings

    s = get_settings()
    s.feishu_drive_root_folder_token = "root_tok"
    return FakeDrive(settings=s)


@pytest.fixture
def fake_store() -> ConfirmStore:
    return ConfirmStore(redis_client=_FakeRedis(), ttl_sec=60)


@pytest.fixture
def manager(fake_drive: FakeDrive, fake_store: ConfirmStore) -> DriveManager:
    return DriveManager(drive=fake_drive, confirm_store=fake_store)


# ---------------------------------------------------------------------------
# ensure_tree + walk + resolve
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_tree_creates_four_sections(manager: DriveManager) -> None:
    tokens = await manager.ensure_tree()
    # — keys are English token names; values are the Drive tokens.
    for key in ("home", "today", "daily_reports", "sources"):
        assert tokens[key], f"missing token for {key}"
    assert tokens["root"] == "root_tok"


@pytest.mark.asyncio
async def test_walk_returns_root_with_children(manager: DriveManager) -> None:
    tree = await manager.walk(max_depth=2)
    assert tree["token"] == "root_tok"
    assert any(c["name"] == SECTION_HOME for c in tree["children"])
    assert any(c["name"] == SECTION_TODAY for c in tree["children"])


@pytest.mark.asyncio
async def test_resolve_path_to_section(manager: DriveManager) -> None:
    node = await manager.resolve(path=SECTION_TODAY)
    assert node is not None
    assert node.type == "folder"
    assert node.path == SECTION_TODAY


@pytest.mark.asyncio
async def test_resolve_path_anti_traversal(manager: DriveManager) -> None:
    # — First segment must be a known section name.
    node = await manager.resolve(path="/etc/passwd")
    assert node is None
    node = await manager.resolve(path="../somewhere")
    assert node is None


# ---------------------------------------------------------------------------
# list_section + find_files
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_section_default_today(manager: DriveManager) -> None:
    await manager.ensure_tree()
    items = await manager.list_section(section=SECTION_TODAY)
    assert isinstance(items, list)


@pytest.mark.asyncio
async def test_list_section_unknown_returns_empty(manager: DriveManager) -> None:
    items = await manager.list_section(section="not-a-section")
    assert items == []


@pytest.mark.asyncio
async def test_find_files_substring_match(manager: DriveManager) -> None:
    await manager.create_child_folder(section=SECTION_TODAY, name="AI 报告")
    await manager.create_child_folder(section=SECTION_HOME, name="日报样板")
    items = await manager.find_files(keyword="报告", limit=10)
    names = [it["name"] for it in items]
    assert "AI 报告" in names


# ---------------------------------------------------------------------------
# create_child_folder + mkdir_path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_child_folder_returns_token(manager: DriveManager) -> None:
    result = await manager.create_child_folder(
        section=SECTION_SOURCES, name="News"
    )
    assert result["token"].startswith("fld_")
    assert result["section"] == SECTION_SOURCES
    assert result["name"] == "News"


@pytest.mark.asyncio
async def test_create_child_folder_empty_name_raises(manager: DriveManager) -> None:
    with pytest.raises(FeishuContentError):
        await manager.create_child_folder(section=SECTION_TODAY, name="")


@pytest.mark.asyncio
async def test_mkdir_path_nested(manager: DriveManager) -> None:
    result = await manager.mkdir_path(
        path=f"{SECTION_DAILY}/2026-08-30"
    )
    assert result["token"].startswith("fld_")
    assert result["path"] == f"{SECTION_DAILY}/2026-08-30"


@pytest.mark.asyncio
async def test_mkdir_path_rejects_non_top_level(manager: DriveManager) -> None:
    with pytest.raises(FeishuContentError):
        await manager.mkdir_path(path="随机/a/b")


# ---------------------------------------------------------------------------
# move + rename
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_move_to_section(manager: DriveManager) -> None:
    await manager.ensure_tree()
    created = await manager.create_child_folder(
        section=SECTION_TODAY, name="tmp"
    )
    result = await manager.move_to_section(
        file_token=created["token"],
        file_type="folder",
        target_section=SECTION_HOME,
    )
    assert result["target_folder_token"]


@pytest.mark.asyncio
async def test_rename(manager: DriveManager) -> None:
    await manager.ensure_tree()
    created = await manager.create_child_folder(
        section=SECTION_TODAY, name="old"
    )
    result = await manager.rename(
        file_token=created["token"],
        file_type="folder",
        new_name="new",
    )
    assert result["name"] == "new"


# ---------------------------------------------------------------------------
# request_delete + execute_delete (two-step confirmation)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_delete_returns_action(manager: DriveManager) -> None:
    await manager.create_child_folder(section=SECTION_TODAY, name="victim")
    action = await manager.request_delete(
        path=f"{SECTION_TODAY}/victim"
    )
    assert action.kind == "drive_delete"
    assert action.action_id
    assert action.payload["name"] == "victim"


@pytest.mark.asyncio
async def test_execute_delete_consumes_token_and_deletes(
    manager: DriveManager, fake_drive: FakeDrive
) -> None:
    await manager.create_child_folder(section=SECTION_TODAY, name="victim")
    action = await manager.request_delete(path=f"{SECTION_TODAY}/victim")
    # — Resolve to a DriveNode and execute.
    node = await manager.resolve(path=f"{SECTION_TODAY}/victim")
    assert node is not None
    outcome = await manager.execute_delete(action=action)
    assert outcome["poll"]["status"] == "success"
    assert (node.token, "folder") in fake_drive.deleted


@pytest.mark.asyncio
async def test_request_delete_without_confirm_store_raises(
    fake_drive: FakeDrive,
) -> None:
    mgr = DriveManager(drive=fake_drive, confirm_store=None)
    await mgr.create_child_folder(section=SECTION_TODAY, name="x")
    with pytest.raises(ConfirmStoreUnavailable):
        await mgr.request_delete(path=f"{SECTION_TODAY}/x")


# ---------------------------------------------------------------------------
# within_root
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_within_root_accepts_root_and_empty(manager: DriveManager) -> None:
    assert await manager.within_root(token="root_tok") is True
    assert await manager.within_root(token="") is True
    assert await manager.within_root(token="other_tok") is False


@pytest.mark.asyncio
async def test_within_root_accepts_descendant(manager: DriveManager) -> None:
    """A child folder created inside a section should pass within_root."""
    await manager.create_child_folder(section=SECTION_TODAY, name="descendant")
    # — Capture the token from inside the manager's own list_section.
    items = await manager.list_section(section=SECTION_TODAY)
    assert items, "expected the section to list the new child"
    child_token = items[0]["token"]
    assert await manager.within_root(token=child_token) is True
