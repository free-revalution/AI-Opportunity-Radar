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
        return {
            "doc_id": f"doc_{self._doc_counter}_{title[:10]}",
            "url": f"https://feishu.cn/docx/doc_{self._doc_counter}",
            "folder_token": folder_token or self.folder_token,
        }


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


@pytest.mark.asyncio
async def test_day_folder_uses_redis_cache_on_second_call() -> None:
    """PR-33-C 回归: 第二次 get_or_create_day_folder 应该命中 Redis,
    不再调 ensure_folder_path。
    """
    from app.services import redis_client

    fake = _FakeRedis()
    redis_client.set_redis_for_tests(fake)

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
async def test_day_folder_falls_back_when_redis_down() -> None:
    """PR-33-C 回归: Redis 不可用时 (None) 走原路径,功能不变。"""
    from app.services import redis_client

    redis_client.set_redis_for_tests(None)  # Redis 挂

    drive = FakeDriveClient()
    service = DriveOrgService(drive=drive)
    day = date(2026, 8, 31)
    folder_token = await service.get_or_create_day_folder(day=day)
    # 仍能正确返回 token(不抛异常)
    assert folder_token.startswith("fld_")


@pytest.mark.asyncio
async def test_day_folder_per_day_keys() -> None:
    """PR-33-C 回归: 不同 day 用不同 cache key — 不会跨天拿到旧 token。"""
    from app.services import redis_client

    fake = _FakeRedis()
    redis_client.set_redis_for_tests(fake)

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
# Fixtures
# ---------------------------------------------------------------------------
