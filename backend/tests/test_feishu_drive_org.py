"""Phase 25 v2.1 — DriveOrgService (飞书云盘 4 段结构) tests.

These tests pin the contract the new internal API endpoints
``/api/internal/docs/tree`` and ``/api/internal/docs/daily`` rely on:

  * ``ensure_root_tree`` is idempotent — re-running does not duplicate
    folders.
  * ``write_daily_digest`` writes the Docx into ``每日报告/<date>/``
    and persists a ``DailyDigestDoc`` row keyed by ``date``.
  * Same-day re-runs return the prior doc without creating a new one.
  * ``get_daily_doc`` returns the persisted row.
  * When the drive root is not configured, both ops raise a clear error.

A fake ``FeishuDriveClient`` (no real HTTP) is used so the suite
runs in offline mode.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Iterable
from unittest.mock import patch

import pytest

from app.config import get_settings
from app.services.feishu.drive_org import (
    DriveOrgService,
    SECTION_DAILY,
)
from app.services.feishu.content_client import FeishuContentError


# ---------------------------------------------------------------------------
# Fakes — keep tests offline and deterministic
# ---------------------------------------------------------------------------
class FakeDriveClient:
    """Drop-in replacement for FeishuDriveClient for tests."""

    def __init__(self, *, settings: Any | None = None) -> None:
        # — folder_token mirrors the real Settings contract (line 195 of
        # content_client.py) so DriveOrgService can read it directly.
        self.settings = settings or _fake_settings()
        self._folders: dict[tuple[str, str], str] = {}
        self._counter = 0
        self._doc_counter = 0

    # — shape mirrors FeishuDriveClient.
    @property
    def is_configured(self) -> bool:
        return bool(self.folder_token)

    @property
    def folder_token(self) -> str:
        return self.settings.feishu_drive_root_folder_token

    async def create_folder(self, *, name: str, parent_token: str | None = None) -> str:
        parent = parent_token or self.folder_token
        self._counter += 1
        tok = f"fld_{self._counter}_{name[:6]}"
        self._folders[(parent, name)] = tok
        return tok

    async def list_children(
        self, *, folder_token: str, name: str | None = None
    ) -> list[dict[str, Any]]:
        out = []
        for (parent, n), tok in self._folders.items():
            if parent != folder_token:
                continue
            if name is not None and n != name:
                continue
            out.append({"token": tok, "name": n})
        return out

    async def find_child_by_name(self, *, folder_token: str, name: str) -> dict[str, Any] | None:
        matches = await self.list_children(folder_token=folder_token, name=name)
        return matches[0] if matches else None

    async def ensure_folder_path(
        self, *, parent_token: str, path: list[str]
    ) -> str:
        cur = parent_token
        for name in path:
            found = await self.find_child_by_name(folder_token=cur, name=name)
            if found is not None:
                cur = found["token"]
                continue
            cur = await self.create_folder(name=name, parent_token=cur)
        return cur

    async def create_docx_from_markdown(
        self,
        *,
        title: str,
        markdown: str,
        folder_token: str | None = None,
    ) -> dict[str, Any]:
        self._doc_counter += 1
        # — 注册 docx 到 folder_token 的 children 里,这样 list_children
        # 看见非空,``cleanup_empty_day_folders`` 才不会误删。
        if folder_token and folder_token != self.folder_token:
            self._folders.setdefault(
                (folder_token, f"{title}.docx"), f"doc_{self._doc_counter}"
            )
        return {
            "doc_id": f"doc_{self._doc_counter}_{title[:10]}",
            "url": f"https://feishu.cn/docx/doc_{self._doc_counter}",
            "folder_token": folder_token or self.folder_token,
        }

    async def delete_file(
        self, *, file_token: str, file_type: str = "folder"
    ) -> dict[str, Any]:
        # — 删 folder:把 _folders 里 (parent, name) → token 的对应项 drop。
        # 任意 parent 下 name 是 file_token 的(即 token 当 name 用)找不到 —
        # 我们的 fake 是 (parent, name) 主键,token 是 value,所以反查:
        to_drop: list[tuple[str, str]] = []
        for (parent, name), tok in self._folders.items():
            if tok == file_token:
                to_drop.append((parent, name))
        for key in to_drop:
            self._folders.pop(key, None)
        self._counter += 1
        return {"task_id": f"task_{self._counter}", "file_token": file_token}

    async def poll_delete_task(
        self, *, task_id: str, timeout: float = 5.0
    ) -> dict[str, Any]:
        # Fake — 永远 immediate success
        return {"task_id": task_id, "status": "success"}


def _fake_settings(root: str = "root_folder_token") -> Any:
    """Build a Settings-like object sufficient for DriveOrgService."""
    s = get_settings()
    s.feishu_drive_root_folder_token = root
    return s


# ---------------------------------------------------------------------------
# ensure_root_tree
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_root_tree_resolves_existing_sections() -> None:
    """Phase 27 — ensure_root_tree is now read-only.

    Phase 30 — single-section tree: only ``📁 每日报告`` is required.
    Earlier behaviour expected 4 sections; the Phase 30 redesign
    (plan D4) drops 📌 首页 / 📅 今日 / 📚 信息源 entirely.
    """
    drive = FakeDriveClient()
    # — Pre-populate the root folder with the single section.
    await drive.create_folder(name=SECTION_DAILY, parent_token="root_folder_token")

    service = DriveOrgService(drive=drive)
    tokens = await service.ensure_root_tree()
    assert tokens.root == "root_folder_token"
    as_dict = tokens.as_dict()
    assert as_dict["daily_reports"].startswith("fld_")
    # — Phase 30: home/today/sources always empty (legacy back-compat)
    assert as_dict["home"] == ""
    assert as_dict["today"] == ""
    assert as_dict["sources"] == ""


@pytest.mark.asyncio
async def test_ensure_root_tree_is_idempotent() -> None:
    """Re-running resolve against an unchanged root returns the
    same tokens (the read-only path is naturally idempotent —
    the create-then-list-create cycle is gone)."""
    drive = FakeDriveClient()
    await drive.create_folder(name=SECTION_DAILY, parent_token="root_folder_token")

    service = DriveOrgService(drive=drive)
    first = await service.ensure_root_tree()
    second = await service.ensure_root_tree()
    assert first.daily_reports == second.daily_reports
    assert first.daily_reports.startswith("fld_")


@pytest.mark.asyncio
async def test_ensure_root_tree_without_root_token_raises() -> None:
    settings = _fake_settings(root="")
    drive = FakeDriveClient(settings=settings)
    service = DriveOrgService(drive=drive, settings=settings)
    with pytest.raises(FeishuContentError, match="not configured"):
        await service.ensure_root_tree()


@pytest.mark.asyncio
async def test_ensure_root_tree_missing_sections_raises_friendly() -> None:
    """Phase 27 — when sections don't exist, raise a friendly
    message naming each missing section instead of trying to create
    them (which 404s on tenants that disable folder creation).

    Phase 30 — single section: only ``📁 每日报告`` is required."""
    drive = FakeDriveClient()
    # — Empty root folder (no sections pre-populated).
    service = DriveOrgService(drive=drive)
    with pytest.raises(FeishuContentError, match="missing required section"):
        await service.ensure_root_tree()


# ---------------------------------------------------------------------------
# write_daily_digest — happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_write_daily_digest_creates_docx_and_persists_row(
    sqlite_session: Any,
) -> None:
    """End-to-end: fake drive + real DB session."""
    drive = FakeDriveClient()
    service = DriveOrgService(
        drive=drive, session=sqlite_session
    )
    ref = await service.write_daily_digest(
        day=date(2026, 8, 30),
        markdown="# Hello",
        run_id=42,
        raw_count=120,
        signal_count=8,
    )
    assert ref.date == date(2026, 8, 30)
    assert ref.doc_id.startswith("doc_")
    assert ref.doc_url.startswith("https://feishu.cn/docx/")
    assert ref.run_id == 42
    assert ref.raw_count == 120
    assert ref.signal_count == 8

    # — row was persisted
    from app.models import DailyDigestDoc

    stored = await sqlite_session.get(DailyDigestDoc, date(2026, 8, 30))
    assert stored is not None
    assert stored.doc_id == ref.doc_id
    assert stored.run_id == 42


@pytest.mark.asyncio
async def test_write_daily_digest_idempotent_same_day(
    sqlite_session: Any,
) -> None:
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive, session=sqlite_session)
    first = await service.write_daily_digest(
        day=date(2026, 8, 30),
        markdown="# first",
        run_id=1,
    )
    second = await service.write_daily_digest(
        day=date(2026, 8, 30),
        markdown="# second — must NOT be written",
        run_id=2,
    )
    assert first.doc_id == second.doc_id
    assert first.doc_url == second.doc_url
    assert second.run_id == 1  # — the original row's run_id wins


@pytest.mark.asyncio
async def test_write_daily_digest_uses_per_day_folder_path() -> None:
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive)
    await service.write_daily_digest(
        day=date(2026, 8, 30),
        markdown="# ok",
    )
    # — daily folder created
    assert await drive.find_child_by_name(
        folder_token="root_folder_token", name=SECTION_DAILY
    ) is not None
    # — day folder nested inside daily
    daily_root = (await drive.find_child_by_name(
        folder_token="root_folder_token", name=SECTION_DAILY
    ))["token"]
    assert await drive.find_child_by_name(
        folder_token=daily_root, name="2026-08-30"
    ) is not None


@pytest.mark.asyncio
async def test_write_daily_digest_without_root_token_raises() -> None:
    settings = _fake_settings(root="")
    drive = FakeDriveClient(settings=settings)
    service = DriveOrgService(drive=drive, settings=settings)
    with pytest.raises(FeishuContentError, match="not configured"):
        await service.write_daily_digest(
            day=date(2026, 8, 30), markdown="x"
        )


@pytest.mark.asyncio
async def test_write_daily_digest_custom_title() -> None:
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive)
    # — custom title used to construct the docx (drive fake prepends
    # `doc_<n>_<title[:10]>` to the doc_id). We verify the slice by
    # checking the docx was created with the expected prefix.
    captured: dict[str, Any] = {}

    real_create = drive.create_docx_from_markdown

    async def spy(*, title: str, markdown: str, folder_token: str | None = None):
        captured["title"] = title
        return await real_create(
            title=title, markdown=markdown, folder_token=folder_token
        )

    drive.create_docx_from_markdown = spy  # type: ignore[assignment]
    await service.write_daily_digest(
        day=date(2026, 8, 30),
        markdown="x",
        title="My Daily",
    )
    assert captured["title"] == "My Daily"


# ---------------------------------------------------------------------------
# get_daily_doc
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_daily_doc_returns_persisted_row(
    sqlite_session: Any,
) -> None:
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive, session=sqlite_session)
    await service.write_daily_digest(
        day=date(2026, 8, 30), markdown="# t"
    )
    fetched = await service.get_daily_doc(date(2026, 8, 30))
    assert fetched is not None
    assert str(fetched.date) == "2026-08-30"


@pytest.mark.asyncio
async def test_get_daily_doc_missing_returns_none(sqlite_session: Any) -> None:
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive, session=sqlite_session)
    fetched = await service.get_daily_doc(date(2020, 1, 1))
    assert fetched is None


# ---------------------------------------------------------------------------
# Phase 33 PR-33-C — Drive day folder Redis 缓存
# ---------------------------------------------------------------------------
class _FakeRedis:
    """Minimal in-memory Redis 替身 — get/set/ping."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls += 1
        self.store[key] = value

    async def ping(self) -> bool:
        return True


def _set_redis_with_cleanup(monkeypatch, value) -> None:
    """替换 redis singleton,test 结束自动还原 — 防 PR-33-C 测试污染
    其他模块(尤其 detail_docx)的 get_redis() 调用。

    Phase 33 PR-33-C 修复: 之前 3 个测试用 ``set_redis_for_tests(fake)``
    直接写 singleton,test 结束不还原。下游 test_detail_docx::test_write_to_drive_fresh
    走到 ``get_redis()`` 拿到的还是上一个 test 的 FakeRedis,且其中
    ``store[day_folder_key]`` 还存着之前的 token,导致 "cache hit",
    ``ensure_folder_path`` 不被调用 → ``drive._folders`` 空 → 测试失败。

    fix: monkeypatch.setattr 把 _client 还原成 test 前的值。
    """
    from app.services import redis_client

    redis_client.set_redis_for_tests(value)
    # monkeypatch 在 teardown 时把 _client 还原回原值(None 或上一个 fake)
    monkeypatch.setattr(redis_client, "_client", value)


@pytest.mark.asyncio
async def test_day_folder_uses_redis_cache_on_second_call(monkeypatch) -> None:
    """PR-33-C 回归: 第二次 get_or_create_day_folder 应该命中 Redis,
    不再调 ensure_folder_path。
    """
    fake = _FakeRedis()
    _set_redis_with_cleanup(monkeypatch, fake)

    drive = FakeDriveClient()
    # Pre-create folder 让 ensure_folder_path 能找到
    service = DriveOrgService(drive=drive)

    day = date(2026, 8, 31)
    # 1st call — miss → ensure_folder_path → 写 cache
    folder_walk_calls_1 = {"n": 0}
    real_ensure = drive.ensure_folder_path

    async def _counting_ensure(**kwargs: Any) -> str:
        folder_walk_calls_1["n"] += 1
        return await real_ensure(**kwargs)

    with patch.object(drive, "ensure_folder_path", side_effect=_counting_ensure):
        first = await service.get_or_create_day_folder(day=day)
    assert folder_walk_calls_1["n"] >= 1
    assert fake.set_calls == 1  # cache 写入

    # 2nd call — 应该命中 cache,不调 ensure_folder_path
    folder_walk_calls_2 = {"n": 0}

    async def _counting_ensure_2(**kwargs: Any) -> str:
        folder_walk_calls_2["n"] += 1
        return await real_ensure(**kwargs)

    with patch.object(drive, "ensure_folder_path", side_effect=_counting_ensure_2):
        second = await service.get_or_create_day_folder(day=day)
    assert first == second
    assert folder_walk_calls_2["n"] == 0, (
        f"2nd call made {folder_walk_calls_2['n']} ensure_folder_path — "
        "PR-33-C 回归:Redis cache 没生效?"
    )


@pytest.mark.asyncio
async def test_day_folder_falls_back_when_redis_down(monkeypatch) -> None:
    """PR-33-C 回归: Redis 不可用时 (None) 走原路径,功能不变。"""
    _set_redis_with_cleanup(monkeypatch, None)  # Redis 挂

    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive)
    day = date(2026, 8, 31)
    folder_token = await service.get_or_create_day_folder(day=day)
    # 仍能正确返回 token(不抛异常)
    assert folder_token.startswith("fld_")


@pytest.mark.asyncio
async def test_day_folder_per_day_keys(monkeypatch) -> None:
    """PR-33-C 回归: 不同 day 用不同 cache key — 不会跨天拿到旧 token。"""
    fake = _FakeRedis()
    _set_redis_with_cleanup(monkeypatch, fake)

    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive)
    d1 = date(2026, 8, 30)
    d2 = date(2026, 8, 31)

    t1 = await service.get_or_create_day_folder(day=d1)
    t2 = await service.get_or_create_day_folder(day=d2)
    # 不同 day 应有不同 cache key
    cache_keys = list(fake.store.keys())
    assert any("2026-08-30" in k for k in cache_keys)
    assert any("2026-08-31" in k for k in cache_keys)
    # 不同 day → 不同 folder token
    assert t1 != t2


@pytest.mark.asyncio
async def test_seconds_until_midnight_utc_basic() -> None:
    """PR-33-C 辅助函数: TTL 算到下个 UTC 00:00。"""
    from app.services.feishu.drive_org import _seconds_until_midnight_utc

    # 当天 12:00 → 12h 到午夜
    from datetime import datetime, timezone
    noon = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    assert _seconds_until_midnight_utc(now=noon) == 12 * 3600

    # 当天 23:59:30 → 30s 到午夜,但最小 60s(防极端边缘)
    late = datetime(2026, 8, 31, 23, 59, 30, tzinfo=timezone.utc)
    secs = _seconds_until_midnight_utc(now=late)
    assert secs == 60  # 30s 不到 → 兜底 60s


# ---------------------------------------------------------------------------
# Phase 35 PR-follow-up: 空 day-folder 清理(用户原话:空日期目录)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_day_folder_if_empty_deletes_when_no_children() -> None:
    """空 day_folder(0 children) → 删除成功,return True。"""
    drive = FakeDriveClient()
    # 预建一个 2026-09-05 的空子目录(没文件)
    daily_root = await drive.create_folder(
        name=SECTION_DAILY, parent_token="root_folder_token"
    )
    await drive.create_folder(
        name="2026-09-05", parent_token=daily_root
    )

    service = DriveOrgService(drive=drive)
    deleted = await service.delete_day_folder_if_empty(day=date(2026, 9, 5))
    assert deleted is True

    # 文件夹已不在了
    children = await drive.list_children(folder_token=daily_root)
    names = {c["name"] for c in children}
    assert "2026-09-05" not in names


@pytest.mark.asyncio
async def test_delete_day_folder_if_empty_keeps_when_has_children() -> None:
    """非空 day_folder(里面有 docx) → 不删,return False。"""
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive, session=None)
    # 写入 daily digest → 自动建 folder + 1 个 docx
    await service.write_daily_digest(
        day=date(2026, 9, 5),
        markdown="# hello",
    )
    # folder 里现在有 1 个 docx → 不该被删
    deleted = await service.delete_day_folder_if_empty(day=date(2026, 9, 5))
    assert deleted is False


@pytest.mark.asyncio
async def test_write_daily_digest_docx_failure_cleans_up_empty_folder() -> None:
    """用户原话场景: ``create_docx_from_markdown`` 失败 → 自动清空 day_folder。"""
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive, session=None)

    # 1) 让 docx 创建抛异常
    async def _boom(**_kwargs):
        raise FeishuContentError("feishu 502 from docx api")

    drive.create_docx_from_markdown = _boom  # type: ignore[assignment]

    # 2) write_daily_digest 应该抛 — 关键是别让空 folder 留下
    with pytest.raises(FeishuContentError, match="feishu 502"):
        await service.write_daily_digest(
            day=date(2026, 9, 5),
            markdown="# hello",
        )

    # 3) 验证空 day_folder 已自动清理
    daily_root = await drive.ensure_folder_path(
        parent_token="root_folder_token", path=[SECTION_DAILY]
    )
    children = await drive.list_children(folder_token=daily_root)
    names = {c["name"] for c in children}
    # 2026-09-05 不应在(已删)
    assert "2026-09-05" not in names


@pytest.mark.asyncio
async def test_cleanup_empty_day_folders_bulk_mixed() -> None:
    """bulk 模式:扫到 N 个日期目录,只删空的,保留非空的。"""
    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive, session=None)

    # — 准备场景:
    #   2026-09-01: 空 (手动建)
    #   2026-09-02: 有 docx (来自 write_daily_digest)
    #   2026-09-03: 空 (手动建)
    #   README:   非日期名,忽略
    daily_root = await drive.create_folder(
        name=SECTION_DAILY, parent_token="root_folder_token"
    )
    await drive.create_folder(name="2026-09-01", parent_token=daily_root)
    await drive.create_folder(name="2026-09-03", parent_token=daily_root)
    await drive.create_folder(name="README.md", parent_token=daily_root)
    await service.write_daily_digest(
        day=date(2026, 9, 2), markdown="# 9-2"
    )

    summary = await service.cleanup_empty_day_folders()
    assert summary["scanned"] == 3  # 3 个日期目录
    assert summary["deleted"] == 2  # 09-01 + 09-03
    assert summary["kept_with_children"] == 1  # 09-02 (有 docx)

    # 验证 09-02 还在,09-01/03 不在了
    remaining = await drive.list_children(folder_token=daily_root)
    remaining_names = {c["name"] for c in remaining}
    assert "2026-09-02" in remaining_names
    assert "2026-09-01" not in remaining_names
    assert "2026-09-03" not in remaining_names
    # README.md (非日期格式) 不动
    assert "README.md" in remaining_names


@pytest.mark.asyncio
async def test_cleanup_empty_day_folders_noop_when_daily_missing() -> None:
    """📁 每日报告 段都还没建 → 安全 noop(返回 0/0/0)。"""
    drive = FakeDriveClient()  # root 空
    service = DriveOrgService(drive=drive)
    summary = await service.cleanup_empty_day_folders()
    assert summary == {"scanned": 0, "deleted": 0, "kept_with_children": 0}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
