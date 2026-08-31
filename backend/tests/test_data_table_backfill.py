"""Phase 31 P31-A — Data 表全量回填 + ORM path 修复测试。

覆盖:
  * Phase 30 PR-1 缺口修复: ``bulk_insert_orm_raw_items`` 能从 ORM
    RawItem 拼主键(不依赖 dataclass 的 ``.source`` 属性)
  * ``bulk_insert_raw_items_unbounded`` — 普通路径 / 幂等 / since 过滤 /
    on_progress 回调 / 孤儿行跳过
  * ``_primary_key_for`` 拒绝缺 source_name 的 ORM RawItem
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
import pytest_asyncio

from app.models import RawItem as ORMRawItem
from app.models import Source as ORMSource
from app.services.feishu.data_table import (
    DataTableClient,
    _primary_key_for,
    _source_slug_for_item,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeBitableClient:
    """Minimal in-memory stand-in for FeishuBitableClient."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._counter = 0
        self._table_created = True
        self._app_token = "fake_app_token"
        self._table_id = "tb_data"
        # 与 FeishuBitableClient 同形(让 DataTableClient.ensure_table 的内部缓存读写能跑通)
        self._cached_app_token: Optional[str] = "fake_app_token"
        self._cached_table_id: Optional[str] = "tb_data"
        self._table_name: str = "Data"

    async def ensure_app(self) -> str:
        self._cached_app_token = self._app_token
        return self._app_token

    async def list_tables(
        self, *, app_token: Optional[str] = None
    ) -> list[dict[str, Any]]:
        return [{"table_id": self._table_id, "name": "Data"}]

    async def list_records(
        self,
        *,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
        page_size: int = 20,
        page_token: Optional[str] = None,
        filter_: Optional[dict[str, Any]] = None,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        # 简化的 Source is filter
        out: list[dict[str, Any]] = []
        for r in self._records:
            fields = r.get("fields") or {}
            if filter_ and "conditions" in filter_:
                conds = filter_["conditions"]
                match = True
                for c in conds:
                    fn = c.get("field_name")
                    val = fields.get(fn)
                    want = c.get("value")
                    if isinstance(want, list):
                        want = want[0] if want else None
                    if val != want:
                        match = False
                        break
                if match:
                    out.append(r)
            else:
                out.append(r)
        return out[:page_size], None

    async def batch_create_records(
        self,
        *,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
        records: list[dict[str, Any]],
        chunk_size: int = 500,
    ) -> int:
        for rec in records:
            self._counter += 1
            rid = f"rec_{self._counter:03d}"
            self._records.append(
                {"record_id": rid, "table_id": table_id, "fields": dict(rec["fields"])}
            )
        return len(records)

    async def update_record(
        self,
        *,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        for r in self._records:
            if r["record_id"] == record_id:
                r["fields"] = {**r["fields"], **fields}
                return r
        return {"record_id": record_id, "fields": fields}


class _FakeAppClient:
    """Stand-in for FeishuAppClient — token provider."""

    def __init__(self, *, settings: Any) -> None:
        self.settings = settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def fake_bitable() -> _FakeBitableClient:
    return _FakeBitableClient()


@pytest_asyncio.fixture
async def sources(sqlite_session: Any) -> list[ORMSource]:
    src_a = ORMSource(
        name="github", type="api", url="https://gh.example",
        compliance_level="A", commercial_use_status="allowed",
        access_method="api",
    )
    src_b = ORMSource(
        name="reddit", type="api", url="https://reddit.example",
        compliance_level="A", commercial_use_status="allowed",
        access_method="api",
    )
    sqlite_session.add_all([src_a, src_b])
    await sqlite_session.flush()
    return [src_a, src_b]


@pytest_asyncio.fixture
async def seeded_raw_items(sqlite_session: Any, sources: list[ORMSource]) -> list[ORMRawItem]:
    """3 行 RawItem — 2 github + 1 reddit,时间分散。"""
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    items = [
        ORMRawItem(
            source_id=sources[0].id,
            external_id="gh-1",
            url="https://gh.example/1",
            title="GH 1",
            content_hash="h1",
            fetched_at=base,
        ),
        ORMRawItem(
            source_id=sources[0].id,
            external_id="gh-2",
            url="https://gh.example/2",
            title="GH 2",
            content_hash="h2",
            fetched_at=base + timedelta(days=5),
        ),
        ORMRawItem(
            source_id=sources[1].id,
            external_id="rd-1",
            url="https://reddit.example/1",
            title="RD 1",
            content_hash="h3",
            fetched_at=base + timedelta(days=10),
        ),
    ]
    sqlite_session.add_all(items)
    await sqlite_session.commit()
    for it in items:
        await sqlite_session.refresh(it)
    return items


def _make_client(fake_bitable: _FakeBitableClient, sqlite_session: Any) -> DataTableClient:
    """Build a DataTableClient with fake bitable injected."""
    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    # 直接把内部的 _bitable 换掉 — 单元测试只关心 _bitable 调用
    client._bitable = fake_bitable  # type: ignore[assignment]
    # 给 SQLite session 提供 raw_item.source_id → Source.name 解析路径
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# Phase 30 PR-1 fix — ORM path
# ---------------------------------------------------------------------------
def test_primary_key_for_orm_requires_source_name() -> None:
    """ORM RawItem 没有 ``.source`` 属性,必须传 source_name。"""
    orm_item = ORMRawItem(
        source_id=1,
        external_id="x",
        url="https://x",
        title="X",
        content_hash="hh",
    )
    with pytest.raises(ValueError, match="source_name"):
        _primary_key_for(orm_item)


def test_primary_key_for_orm_with_source_name() -> None:
    orm_item = ORMRawItem(
        source_id=1,
        external_id="x",
        url="https://x",
        title="X",
        content_hash="hh",
    )
    pk = _primary_key_for(orm_item, source_name="github")
    assert pk == "github:x"


def test_source_slug_for_dataclass_no_source_name() -> None:
    """Dataclass RawItem 自己有 ``.source``。"""
    from app.services.ingestion.raw_item import RawItem

    item = RawItem(
        source="hackernews",
        source_id="abc",
        url="https://hn.example/x",
        title="X",
    )
    assert _source_slug_for_item(item=item) == "hackernews"


@pytest.mark.asyncio
async def test_bulk_insert_orm_raw_items_basic(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
) -> None:
    """ORM path: 3 行 → 3 行落 Data 表,主键包含 Source.name。"""
    client = _make_client(fake_bitable, sqlite_session)
    result = await client.bulk_insert_orm_raw_items(
        items=seeded_raw_items, run_id=42, session=sqlite_session
    )
    assert result["inserted"] == 3
    assert result["skipped_duplicate"] == 0
    assert result["skipped_orphan"] == 0
    assert len(fake_bitable._records) == 3
    keys = {r["fields"]["Source"] for r in fake_bitable._records}
    assert keys == {"github:gh-1", "github:gh-2", "reddit:rd-1"}


# ---------------------------------------------------------------------------
# Phase 31 P31-A — 全量回填
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unbounded_backfills_all(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
) -> None:
    client = _make_client(fake_bitable, sqlite_session)
    result = await client.bulk_insert_raw_items_unbounded(
        session=sqlite_session, chunk_size=500
    )
    assert result["inserted"] == 3
    assert result["skipped_duplicate"] == 0
    assert result["skipped_orphan"] == 0
    assert result["scanned"] == 3
    assert len(fake_bitable._records) == 3


@pytest.mark.asyncio
async def test_unbounded_is_idempotent(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
) -> None:
    """第二次跑应全部去重,不会写第二遍。"""
    client = _make_client(fake_bitable, sqlite_session)
    first = await client.bulk_insert_raw_items_unbounded(session=sqlite_session)
    second = await client.bulk_insert_raw_items_unbounded(session=sqlite_session)
    assert first["inserted"] == 3
    assert second["inserted"] == 0
    assert second["skipped_duplicate"] == 3
    # records 数应保持 3
    assert len(fake_bitable._records) == 3


@pytest.mark.asyncio
async def test_unbounded_since_filter(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
) -> None:
    """``--since 2026-08-07`` 应只回填 rd-1(fetched_at >= D+6)。"""
    client = _make_client(fake_bitable, sqlite_session)
    since = datetime(2026, 8, 7, tzinfo=timezone.utc)
    result = await client.bulk_insert_raw_items_unbounded(
        session=sqlite_session, since=since
    )
    assert result["scanned"] == 1
    assert result["inserted"] == 1
    keys = {r["fields"]["Source"] for r in fake_bitable._records}
    assert keys == {"reddit:rd-1"}


@pytest.mark.asyncio
async def test_unbounded_calls_on_progress(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
) -> None:
    """``on_progress`` 每批后被调用一次。"""
    client = _make_client(fake_bitable, sqlite_session)
    calls: list[tuple[int, int]] = []

    async def _progress(inserted: int, total: int) -> None:
        calls.append((inserted, total))

    await client.bulk_insert_raw_items_unbounded(
        session=sqlite_session, chunk_size=2, on_progress=_progress
    )
    # chunk=2 → 2 批
    assert len(calls) == 2
    # 最后一通 total=3
    assert calls[-1][1] == 3


@pytest.mark.asyncio
async def test_unbounded_skips_orphan_rows(
    sqlite_session: Any,
    sources: list[ORMSource],
    fake_bitable: _FakeBitableClient,
) -> None:
    """孤儿 RawItem(没有 Source.name 关联)被跳过并 warn。"""
    # 删 reddit source — 让 rd-1 变孤儿
    sqlite_session.expire_all()
    orphan = ORMRawItem(
        source_id=999_999,  # 没对应的 Source
        external_id="orphan-1",
        url="https://orphan",
        title="Orphan",
        content_hash="h_orphan",
    )
    sqlite_session.add(orphan)
    await sqlite_session.commit()
    await sqlite_session.refresh(orphan)

    client = _make_client(fake_bitable, sqlite_session)
    result = await client.bulk_insert_raw_items_unbounded(session=sqlite_session)
    # bulk_insert_orm_raw_items 路径下,orphan 在 INNER JOIN 时就过滤掉了;
    # 在 unbounded 路径下,Source.name 缺失的 row 也被跳过。
    assert result["skipped_orphan"] >= 1
    assert result["inserted"] == 0  # 唯一行被孤儿过滤


@pytest.mark.asyncio
async def test_unbounded_empty_db_noop(
    sqlite_session: Any, fake_bitable: _FakeBitableClient
) -> None:
    """空 DB 跑回填应该 noop,不打飞书。"""
    client = _make_client(fake_bitable, sqlite_session)
    result = await client.bulk_insert_raw_items_unbounded(session=sqlite_session)
    assert result == {
        "inserted": 0,
        "skipped_duplicate": 0,
        "skipped_orphan": 0,
        "scanned": 0,
    }
    assert fake_bitable._records == []


# ---------------------------------------------------------------------------
# Phase 32 PR-32-A — update_screening_results 串行化回归测试
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_screening_results_runs_serially(
    sqlite_session: Any, fake_bitable: _FakeBitableClient
) -> None:
    """PR-32-A 回归测试: 确认 update_screening_results 不并发执行 update_record。

    飞书 Bitable 底层串行处理同文档写 — 并发触发 1254291 Write conflict。
    通过 asyncio.Lock 观测最大并发数 = 1 来证明串行化。
    """
    import asyncio

    concurrent_peaks: list[int] = []
    in_flight = 0
    lock = asyncio.Lock()

    async def _fake_update_record(
        self: Any,
        *,
        table_id: Any,
        record_id: Any,
        fields: Any,
    ) -> dict[str, Any]:
        nonlocal in_flight
        in_flight += 1
        concurrent_peaks.append(in_flight)
        # 模拟飞书调用耗时,放大串行/并发的差异
        await asyncio.sleep(0.01)
        in_flight -= 1
        # 同步落 fake records
        for r in self._records:  # type: ignore[attr-defined]
            if r["record_id"] == record_id:
                r["fields"] = {**r["fields"], **fields}
                return r
        return {"record_id": record_id, "fields": fields}

    # Pre-seed: 3 行 Data 表记录,供 update_screening_results 命中 record_id
    fake_bitable._records = [
        {"record_id": "rec_1", "table_id": "tb_data",
         "fields": {"Source": "github:g1", "Title": "t1"}},
        {"record_id": "rec_2", "table_id": "tb_data",
         "fields": {"Source": "github:g2", "Title": "t2"}},
        {"record_id": "rec_3", "table_id": "tb_data",
         "fields": {"Source": "reddit:r1", "Title": "r1"}},
    ]
    # 让 list_records 在 phase 32 测试中查得到这 3 行(覆盖默认 filter)
    fake_bitable._records_for_list = fake_bitable._records

    from app.services.feishu.data_table import DataTableClient
    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    # Monkeypatch update_record 计数并发
    import unittest.mock

    with unittest.mock.patch.object(
        type(client._bitable), "update_record", _fake_update_record
    ):
        mapping = {
            "github:g1": {"Category": "x", "Score": 80, "Opportunity ID": 1},
            "github:g2": {"Category": "y", "Score": 70, "Opportunity ID": 2},
            "reddit:r1": {"Category": "z", "Score": 60, "Opportunity ID": 3},
        }
        updated = await client.update_screening_results(mapping=mapping)
        assert updated == 3
        # — 并发峰值 = 1(串行)。如果是 asyncio.gather,峰值会是 3。
        assert max(concurrent_peaks) == 1, (
            f"update_screening_results not serial! peak={max(concurrent_peaks)}"
        )


@pytest.mark.asyncio
async def test_update_screening_results_records_metric_on_failure(
    sqlite_session: Any, fake_bitable: _FakeBitableClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败的 update_record → 调 ``record_external_error`` 一次。"""
    fake_bitable._records = [
        {"record_id": "rec_x", "table_id": "tb_data",
         "fields": {"Source": "github:gx", "Title": "x"}},
    ]
    fake_bitable._records_for_list = fake_bitable._records

    from app.services.feishu.content_client import FeishuContentError
    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    import unittest.mock

    async def _boom(self: Any, **kwargs: Any) -> dict[str, Any]:
        raise FeishuContentError("feishu 1254291 write conflict")

    metric_calls: list[tuple[str, str]] = []

    def _spy_record(provider: str, kind: str) -> None:
        metric_calls.append((provider, kind))

    monkeypatch.setattr(
        "app.metrics.record_external_error", _spy_record
    )

    with unittest.mock.patch.object(
        type(client._bitable), "update_record", _boom
    ):
        mapping = {"github:gx": {"Category": "x", "Score": 80, "Opportunity ID": 1}}
        updated = await client.update_screening_results(mapping=mapping)
        # — update 失败 → 返回 0,但 metric 被记
        assert updated == 0
        assert ("feishu_data_table", "update_record_failed") in metric_calls


@pytest.mark.asyncio
async def test_update_screening_results_no_metric_on_success(
    sqlite_session: Any, fake_bitable: _FakeBitableClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全部成功 → 不调 record_external_error(避免 metric 噪音)。"""
    fake_bitable._records = [
        {"record_id": "rec_y", "table_id": "tb_data",
         "fields": {"Source": "github:gy", "Title": "y"}},
    ]
    fake_bitable._records_for_list = fake_bitable._records

    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    metric_calls: list[tuple[str, str]] = []

    def _spy_record(provider: str, kind: str) -> None:
        metric_calls.append((provider, kind))

    monkeypatch.setattr("app.metrics.record_external_error", _spy_record)

    mapping = {"github:gy": {"Category": "y", "Score": 80, "Opportunity ID": 1}}
    updated = await client.update_screening_results(mapping=mapping)
    assert updated == 1
    assert metric_calls == []