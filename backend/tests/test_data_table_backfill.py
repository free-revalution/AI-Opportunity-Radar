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
    """Minimal in-memory stand-in for FeishuBitableClient.

    Phase 35 PR-35-D: 多 token — 记录按 ``(app_token, table_id)`` 隔离,
    不同 token 的表互不可见,这样 per-target dedup 才能正确测试。
    """

    def __init__(self) -> None:
        self._records_by_token: dict[
            tuple[str, str], list[dict[str, Any]]
        ] = {}  # PR-35-D: per-target isolation
        self._counter = 0
        self._table_created = True
        self._app_token = "fake_app_token"
        self._table_id = "tb_data"
        # 与 FeishuBitableClient 同形(让 DataTableClient.ensure_table 的内部缓存读写能跑通)
        self._cached_app_token: Optional[str] = "fake_app_token"
        self._cached_table_id: Optional[str] = "tb_data"
        self._table_name: str = "Data"
        # Phase 35 PR-35-A: field backfill 测试需要 list_fields/create_field
        # 默认字段表 = 12 老字段。测试里改成 14 或 8 都行,_FakeBitableClient
        # 只关心字段名集合。
        # PR-35-D: 每个 token 一份独立字段表,模拟现实多 app 不同 schema
        self._fields_by_token: dict[tuple[str, str], list[dict[str, Any]]] = {}
        default_fields = [
            {"field_name": spec["field_name"], "type": spec["type"]}
            for spec in [
                {"field_name": "Run ID", "type": 2},
                {"field_name": "Source", "type": 1},
                {"field_name": "External ID", "type": 1},
                {"field_name": "Title", "type": 1},
                {"field_name": "URL", "type": 1},
                {"field_name": "Author", "type": 1},
                {"field_name": "Published At", "type": 5},
                {"field_name": "Category", "type": 1},
                {"field_name": "Score", "type": 2},
                {"field_name": "Opportunity ID", "type": 2},
                {"field_name": "Content Hash", "type": 1},
                {"field_name": "Fetched At", "type": 5},
            ]
        ]
        self._fields: list[dict[str, Any]] = list(default_fields)
        self._fields_by_token[(self._app_token, self._table_id)] = list(default_fields)
        self._create_field_calls: list[dict[str, Any]] = []
        # 默认 token 的 record bucket 预初始化(单 token 旧测试可写
        # ``fake_bitable._records = [...]``,会被 property setter 落到这里)
        self._records_by_token[(self._app_token, self._table_id)] = []

    @property
    def _records(self) -> list[dict[str, Any]]:
        """PR-35-D backward-compat: 老测试 fixture 直接 ``fake_bitable._records = [...]``
        设值,这个 property 让它落到默认 token 的 bucket。新测试优先用
        ``fake_bitable._records_by_token[(token, tid)]`` 做 per-target 操作。
        """
        return self._records_by_token.setdefault(
            (self._app_token, self._table_id), []
        )

    @_records.setter
    def _records(self, value: list[dict[str, Any]]) -> None:
        self._records_by_token[(self._app_token, self._table_id)] = value

    def _bucket(self, app_token: Optional[str], table_id: Optional[str]) -> tuple[str, str]:
        """Resolve (app_token, table_id) bucket key. Defaults to primary."""
        return (
            app_token or self._app_token,
            table_id or self._table_id,
        )

    async def ensure_app(self) -> str:
        self._cached_app_token = self._app_token
        return self._app_token

    async def list_tables(
        self, *, app_token: Optional[str] = None
    ) -> list[dict[str, Any]]:
        # PR-35-D: 每个 token 独立返回自己的表列表。这里 mock 只暴露主表,
        # 不同 token 用同一 table_id "tb_data" — 通过 _records_by_token
        # 隔离数据。
        return [{"table_id": self._table_id, "name": "Data"}]

    async def list_fields(
        self,
        *,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        bucket = self._bucket(app_token, table_id)
        if bucket not in self._fields_by_token:
            # 兜底 — 给新 token 用主 token 的 schema
            return list(self._fields)
        return list(self._fields_by_token[bucket])

    async def create_field(
        self,
        *,
        field_name: str,
        field_type: int,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
        is_primary: bool = False,
        **extra: Any,
    ) -> str:
        self._create_field_calls.append(
            {"field_name": field_name, "type": field_type,
             "is_primary": is_primary, "app_token": app_token or self._app_token,
             "table_id": table_id or self._table_id, **extra}
        )
        bucket = self._bucket(app_token, table_id)
        fields = self._fields_by_token.setdefault(bucket, list(self._fields))
        # 幂等:已存在同名就不重复加
        if not any(f["field_name"] == field_name for f in fields):
            fields.append({"field_name": field_name, "type": field_type})
        return f"fld_{len(fields):03d}"

    async def list_records(
        self,
        *,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
        page_size: int = 20,
        page_token: Optional[str] = None,
        filter_: Optional[dict[str, Any]] = None,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        # PR-35-D: 用 _records_by_token 做 per-token 隔离
        bucket = self._bucket(app_token, table_id)
        records = self._records_by_token.get(bucket, [])
        # Phase 33 PR-33-B: 支持 page_token 翻页 — FakeBitableClient
        # 之前总是返回 None(整张表),不能验证多页场景。
        # 简单实现: 把 records 按 page_size 切片,page_token 是下一页 offset
        # 的字符串形式 "page_N"。

        # 1) 应用 filter
        if filter_ and "conditions" in filter_:
            conds = filter_["conditions"]
            filtered = []
            for r in records:
                fields = r.get("fields") or {}
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
                    filtered.append(r)
        else:
            filtered = list(records)

        # 2) 解析 page_token → offset
        offset = 0
        if page_token:
            try:
                offset = int(page_token.replace("page_", ""))
            except (ValueError, AttributeError):
                offset = 0

        # 3) 切片 + 算 next_token
        page = filtered[offset : offset + page_size]
        next_offset = offset + page_size
        next_token = (
            f"page_{next_offset}" if next_offset < len(filtered) else None
        )
        return page, next_token

    async def batch_create_records(
        self,
        *,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
        records: list[dict[str, Any]],
        chunk_size: int = 500,
    ) -> int:
        bucket = self._bucket(app_token, table_id)
        bucket_records = self._records_by_token.setdefault(bucket, [])
        for rec in records:
            self._counter += 1
            rid = f"rec_{self._counter:03d}"
            entry = {
                "record_id": rid,
                "table_id": table_id or self._table_id,
                "fields": dict(rec["fields"]),
            }
            bucket_records.append(entry)
        return len(records)

    async def update_record(
        self,
        *,
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        bucket = self._bucket(app_token, table_id)
        bucket_records = self._records_by_token.get(bucket, [])
        for r in bucket_records:
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
@pytest_asyncio.fixture(autouse=True)
async def _reset_data_app_token():
    """PR-35-D: 每个测试前后重置 ``feishu_bitable_data_app_token`` 到空。

    否则 ``.env`` 里的逗号分隔值会污染后续测试 — 所有 multi-token 测试
    设置后,后续单 token 测试可能继承污染,导致 ``ensure_table`` 返回
    多目标,查找 bucket 时拿到错的桶。
    """
    from app.config import get_settings

    settings = get_settings()
    original = getattr(settings, "feishu_bitable_data_app_token", "")
    object.__setattr__(settings, "feishu_bitable_data_app_token", "")
    yield
    object.__setattr__(settings, "feishu_bitable_data_app_token", original)


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
    # PR-35-D: result 现在还含 inserted_rows / targets 字段
    assert result["inserted"] == 0
    assert result["skipped_duplicate"] == 0
    assert result["skipped_orphan"] == 0
    assert result["scanned"] == 0
    assert result["inserted_rows"] == 0
    assert result["targets"] == []
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
        app_token: Any = None,  # PR-35-D: 多目标传入 app_token
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
        # PR-35-D: 按 (app_token, table_id) 定位 bucket,默认 fallback 老 flat list
        bucket = (app_token or "fake_app_token", table_id)
        records = self._records_by_token.get(bucket, self._records)  # type: ignore[attr-defined]
        for r in records:
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


# ---------------------------------------------------------------------------
# Phase 33 PR-33-B — batched list_records 替代 N+1 串行查
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_records_by_sources_single_page(
    sqlite_session: Any, fake_bitable: _FakeBitableClient
) -> None:
    """PR-33-B: 单页场景(< 500 行)— helper 一次 fetch 返回 {source: record_id}。"""
    fake_bitable._records = [
        {"record_id": "r1", "table_id": "tb_data",
         "fields": {"Source": "github:g1", "Title": "t1"}},
        {"record_id": "r2", "table_id": "tb_data",
         "fields": {"Source": "reddit:r1", "Title": "r1"}},
    ]

    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]

    out = await client._fetch_records_by_sources(
        app_token="fake_app_token",
        table_id="tb_data",
        wanted_sources={"github:g1", "reddit:r1"},
    )
    assert out == {"github:g1": "r1", "reddit:r1": "r2"}


@pytest.mark.asyncio
async def test_fetch_records_by_sources_paginates(
    sqlite_session: Any, fake_bitable: _FakeBitableClient
) -> None:
    """PR-33-B: 多页场景(> page_size)— helper 翻页拿全表。"""
    # 1200 行,page_size=500 → 3 页(500 + 500 + 200)
    fake_bitable._records = [
        {"record_id": f"r{i:04d}", "table_id": "tb_data",
         "fields": {"Source": f"github:g{i:04d}", "Title": f"t{i}"}}
        for i in range(1200)
    ]

    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]

    # wanted_sources 是 subset,只返回这 3 行
    wanted = {"github:g0001", "github:g0500", "github:g1199"}
    out = await client._fetch_records_by_sources(
        app_token="fake_app_token",
        table_id="tb_data",
        wanted_sources=wanted,
        page_size=500,
    )
    assert out == {
        "github:g0001": "r0001",
        "github:g0500": "r0500",
        "github:g1199": "r1199",
    }


@pytest.mark.asyncio
async def test_existing_sources_uses_single_fetch_not_n_plus_1(
    sqlite_session: Any, fake_bitable: _FakeBitableClient
) -> None:
    """PR-33-B 回归: existing_sources 应该 1 次 list_records,不是 N 次。

    通过计数 fake.list_records 调用次数来证明。
    """
    # Pre-seed 5 行,代表"已存在"的主键
    fake_bitable._records = [
        {"record_id": f"r{i}", "table_id": "tb_data",
         "fields": {"Source": f"github:g{i}", "Title": f"t{i}"}}
        for i in range(5)
    ]

    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]

    call_count = {"n": 0}
    real_list = type(client._bitable).list_records

    async def _counting_list(
        self: Any, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        call_count["n"] += 1
        return await real_list(self, **kwargs)

    import unittest.mock
    with unittest.mock.patch.object(
        type(client._bitable), "list_records", _counting_list
    ):
        # 5 个 source_key 进来,新 helper 应该 1 次 fetch
        existing = await client.existing_sources(
            sources=[f"github:g{i}" for i in range(5)]
        )
    assert existing == {f"github:g{i}" for i in range(5)}
    # PR-33-B: 单次 fetch。如果回退到 N+1 这里会是 5(per-source filter)
    assert call_count["n"] == 1, (
        f"existing_sources made {call_count['n']} list_records calls — "
        "PR-33-B 回归:有人改回 N+1 模式了?"
    )


@pytest.mark.asyncio
async def test_update_screening_results_uses_single_lookup(
    sqlite_session: Any, fake_bitable: _FakeBitableClient
) -> None:
    """PR-33-B 回归: update_screening_results 应该 1 次 list_records 查 record_id。"""
    # Pre-seed 3 行 record
    fake_bitable._records = [
        {"record_id": "r1", "table_id": "tb_data",
         "fields": {"Source": "github:g1", "Title": "t1"}},
        {"record_id": "r2", "table_id": "tb_data",
         "fields": {"Source": "github:g2", "Title": "t2"}},
        {"record_id": "r3", "table_id": "tb_data",
         "fields": {"Source": "reddit:r1", "Title": "r1"}},
    ]
    fake_bitable._records_for_list = fake_bitable._records

    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    call_count = {"n": 0}
    real_list = type(client._bitable).list_records

    async def _counting_list(
        self: Any, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        call_count["n"] += 1
        return await real_list(self, **kwargs)

    import unittest.mock
    with unittest.mock.patch.object(
        type(client._bitable), "list_records", _counting_list
    ):
        mapping = {
            "github:g1": {"Category": "x", "Score": 80, "Opportunity ID": 1},
            "github:g2": {"Category": "y", "Score": 70, "Opportunity ID": 2},
            "reddit:r1": {"Category": "z", "Score": 60, "Opportunity ID": 3},
        }
        updated = await client.update_screening_results(mapping=mapping)
    assert updated == 3
    # PR-33-B: 单次 fetch 查 record_id(不是 3 次 per-source)
    assert call_count["n"] == 1, (
        f"update_screening_results made {call_count['n']} list_records — "
        "PR-33-B 回归:有人改回 per-source 查?"
    )


# ---------------------------------------------------------------------------
# Phase 35 PR-35-A: Data 表字段 backfill + Source Name/Type 透传
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_table_backfills_missing_fields(
    sqlite_session: Any, fake_bitable: _FakeBitableClient
) -> None:
    """PR-35-A: 已有表少 Source Name / Source Type → ensure_table 自动补。

    模拟操作员手上 Data 表是 12 列老 schema(没有 Phase 35 的两列)。
    第一次 ensure_table: 2 次 create_field,补齐 2 个字段。
    第二次 ensure_table: 0 次 create_field(幂等)。
    """
    # 12 老字段 — 故意去掉 Source Name / Source Type
    fake_bitable._fields = [
        {"field_name": name, "type": t}
        for name, t in [
            ("Run ID", 2),
            ("Source", 1),
            ("External ID", 1),
            ("Title", 1),
            ("URL", 1),
            ("Author", 1),
            ("Published At", 5),
            ("Category", 1),
            ("Score", 2),
            ("Opportunity ID", 2),
            ("Content Hash", 1),
            ("Fetched At", 5),
        ]
    ]
    fake_bitable._create_field_calls = []

    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]

    # 1st ensure_table — 应补齐 Source Name + Source Type
    # PR-35-D: ensure_table 现在返回 list[(app_token, table_id)]
    targets = await client.ensure_table()
    assert ("fake_app_token", "tb_data") in targets
    backfilled_names = {c["field_name"] for c in fake_bitable._create_field_calls}
    assert backfilled_names == {"Source Name", "Source Type"}, (
        f"first backfill 漏字段: {backfilled_names}"
    )

    # 2nd ensure_table — 幂等,不再 POST
    fake_bitable._create_field_calls.clear()
    await client.ensure_table()
    assert fake_bitable._create_field_calls == [], (
        f"second ensure_table 仍 create_field,非幂等: "
        f"{fake_bitable._create_field_calls}"
    )


@pytest.mark.asyncio
async def test_ensure_table_backfill_swallows_list_fields_failure(
    sqlite_session: Any, fake_bitable: _FakeBitableClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-35-A: list_fields 失败 → log warning,不阻塞 ensure_table 返回。"""
    from app.services.feishu.content_client import FeishuContentError
    from app.services.feishu.data_table import DataTableClient

    async def _boom_list_fields(self: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise FeishuContentError("feishu down")

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]

    import unittest.mock
    with unittest.mock.patch.object(
        type(client._bitable), "list_fields", _boom_list_fields
    ):
        # 不应抛;list_fields 失败被吞,返回 list[(app_token, table_id)]
        targets = await client.ensure_table()
    assert ("fake_app_token", "tb_data") in targets


@pytest.mark.asyncio
async def test_data_table_flow_end_to_end_with_source_metadata(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
) -> None:
    """用户硬要求 PR-35-D: 证明数据能从 DB 流到 Data 表,带 source 元数据。

    跑完整链路: ensure_table backfill → bulk_insert_raw_items_unbounded
    → batch_create_records,断言 6 行(或这里 3 行)全部带 Source Name +
    Source Type 字段。
    """
    # 把 fake_bitable 的字段表重置成老 12 列,触发 1 次 backfill
    fake_bitable._fields = [
        {"field_name": name, "type": t}
        for name, t in [
            ("Run ID", 2), ("Source", 1), ("External ID", 1),
            ("Title", 1), ("URL", 1), ("Author", 1),
            ("Published At", 5), ("Category", 1), ("Score", 2),
            ("Opportunity ID", 2), ("Content Hash", 1), ("Fetched At", 5),
        ]
    ]
    fake_bitable._create_field_calls = []
    fake_bitable._records = []

    from app.services.feishu.data_table import DataTableClient

    client = DataTableClient(app_client=_FakeAppClient(settings=__import__(
        "app.config", fromlist=["get_settings"]
    ).get_settings()))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    result = await client.bulk_insert_raw_items_unbounded(
        session=sqlite_session, chunk_size=500
    )

    # 1. backfill 触发了
    backfilled = {c["field_name"] for c in fake_bitable._create_field_calls}
    assert backfilled == {"Source Name", "Source Type"}, (
        f"端到端 backfill 漏字段: {backfilled}"
    )

    # 2. 3 行 raw_item 全部落 Data 表
    assert result["inserted"] == 3
    assert len(fake_bitable._records) == 3

    # 3. 每行都有 Source Name / Source Type,值与 seed 一致
    by_source = {r["fields"]["Source"]: r["fields"] for r in fake_bitable._records}
    assert "github:gh-1" in by_source
    assert "github:gh-2" in by_source
    assert "reddit:rd-1" in by_source
    for pk, fields in by_source.items():
        assert "Source Name" in fields, f"{pk} 缺 Source Name"
        assert "Source Type" in fields, f"{pk} 缺 Source Type"
        assert fields["Source Name"]  # 非空
        assert fields["Source Type"]  # 非空

    # 4. github 两行 Source Type 都是 "api"(seed 时 source fixture 用 type='api')
    assert by_source["github:gh-1"]["Source Name"] == "github"
    assert by_source["github:gh-2"]["Source Name"] == "github"
    assert by_source["github:gh-1"]["Source Type"] == "api"
    assert by_source["reddit:rd-1"]["Source Name"] == "reddit"
    assert by_source["reddit:rd-1"]["Source Type"] == "api"

    # 5. 幂等 — 再跑一次 → insert=0, source metadata 行数不增
    records_before = len(fake_bitable._records)
    second = await client.bulk_insert_raw_items_unbounded(
        session=sqlite_session, chunk_size=500
    )
    assert second["inserted"] == 0
    assert second["skipped_duplicate"] == 3
    assert len(fake_bitable._records) == records_before


# ---------------------------------------------------------------------------
# Phase 35 PR-35-D: Data 表多目标广播写入
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_parse_data_app_tokens_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR-35-D: ``FEISHU_BITABLE_DATA_APP_TOKEN`` 支持逗号分隔多 token。"""
    from app.config import get_settings
    from app.services.feishu.data_table import _parse_data_app_tokens

    settings_obj = get_settings()
    # pydantic BaseSettings 不支持 monkeypatch.setattr — 用 object.__setattr__
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "V3X0b56wIasW3MsJnY4cFFSQnze,KJynbSctXazjQns64Itc1P7ynAf",
    )
    tokens = _parse_data_app_tokens(settings_obj)
    assert tokens == [
        "V3X0b56wIasW3MsJnY4cFFSQnze",
        "KJynbSctXazjQns64Itc1P7ynAf",
    ]

    # 单 token(向后兼容)
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "V3X0b56wIasW3MsJnY4cFFSQnze",
    )
    assert _parse_data_app_tokens(settings_obj) == ["V3X0b56wIasW3MsJnY4cFFSQnze"]

    # 空 → 走 auto-create 路径
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token", ""
    )
    assert _parse_data_app_tokens(settings_obj) == []

    # 含空白 / 多个逗号 / 末尾逗号 — 都被 strip
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "  tok1  ,,tok2, tok3  ,",
    )
    assert _parse_data_app_tokens(settings_obj) == ["tok1", "tok2", "tok3"]


@pytest.mark.asyncio
async def test_parse_data_app_tokens_dedups_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-35-D follow-up: 同一 token 写两遍 → 去重 + warning。

    .env 历史里出现过 ``V3X0b56wIasW3MsJnY4cFFSQnze,KJynbSctXazjQns64Itc1P7ynAf,
    KJynbSctXazjQns64Itc1P7ynAf`` — 同一张表被广播两遍毫无意义,而且会
    让 ensure_table 对同一张表跑两遍(浪费 2×list_fields + 2×list_records)。
    """
    from app.config import get_settings
    from app.services.feishu.data_table import (
        _DUPLICATE_WARNED,
        _parse_data_app_tokens,
        logger as data_table_logger,
    )

    settings_obj = get_settings()
    # 同一 token 写三次,夹杂一个不同 token
    raw = "tokA,tokB,tokA,tokA,tokB"
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token", raw
    )

    # 清掉模块级缓存,确保这次调用真的会 warn(fingerprint 是新 raw)
    _DUPLICATE_WARNED.clear()

    # caplog 抓不到 structlog 的 warning — 直接 spy 在 logger.warning 上
    warning_calls: list[tuple[str, dict[str, Any]]] = []
    real_warning = data_table_logger.warning

    def _spy_warning(event: str, *args: Any, **kwargs: Any) -> Any:
        warning_calls.append((event, kwargs))
        return real_warning(event, *args, **kwargs)

    monkeypatch.setattr(data_table_logger, "warning", _spy_warning)

    tokens = _parse_data_app_tokens(settings_obj)

    # 去重后只剩 tokA + tokB,保持首次出现顺序
    assert tokens == ["tokA", "tokB"]

    # 警告被触发
    dup_events = [ev for ev, _ in warning_calls if ev == "feishu_data_table_duplicate_tokens"]
    assert len(dup_events) == 1, (
        f"expected one duplicate-tokens warning, got: {warning_calls}"
    )

    # warning kwargs 包含 duplicates / unique_targets / hint,运营能直接定位
    _, kwargs = next(
        (ev, kw) for ev, kw in warning_calls
        if ev == "feishu_data_table_duplicate_tokens"
    )
    assert "tokA" in kwargs["duplicates"]
    assert "tokB" in kwargs["duplicates"]
    assert kwargs["unique_targets"] == ["tokA", "tokB"]
    assert kwargs["n_unique"] == 2
    assert kwargs["n_duplicates"] == 3  # tokA 写了 2 次 + tokB 写了 1 次 = 3 个重复
    assert ".env" in kwargs["hint"]


@pytest.mark.asyncio
async def test_parse_data_app_tokens_duplicate_warning_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-35-D follow-up: 同一 raw fingerprint 只 warn 一次。

    每次 sync / 每次 DataTableClient 实例化都会调 _parse_data_app_tokens,
    反复 warn 会刷屏。按 fingerprint 缓存避免。
    """
    from app.config import get_settings
    from app.services.feishu.data_table import (
        _DUPLICATE_WARNED,
        _parse_data_app_tokens,
        logger as data_table_logger,
    )

    settings_obj = get_settings()
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "tokA,tokA",
    )

    _DUPLICATE_WARNED.clear()

    warning_calls: list[str] = []
    real_warning = data_table_logger.warning

    def _spy_warning(event: str, *args: Any, **kwargs: Any) -> Any:
        warning_calls.append(event)
        return real_warning(event, *args, **kwargs)

    monkeypatch.setattr(data_table_logger, "warning", _spy_warning)

    # 调 3 次只 warn 1 次(fingerprint 相同)
    _parse_data_app_tokens(settings_obj)
    _parse_data_app_tokens(settings_obj)
    _parse_data_app_tokens(settings_obj)

    dup_count = sum(
        1 for ev in warning_calls if ev == "feishu_data_table_duplicate_tokens"
    )
    assert dup_count == 1, (
        f"重复 warn 应只触发 1 次,实际 {dup_count} 次: {warning_calls}"
    )


@pytest.mark.asyncio
async def test_ensure_table_returns_all_targets(
    sqlite_session: Any, fake_bitable: _FakeBitableClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-35-D: ensure_table 返回 list[(app_token, table_id)] — 每目标一项。"""
    from app.config import get_settings
    from app.services.feishu.data_table import DataTableClient

    # 设两个 token
    settings_obj = get_settings()
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "tok_alpha,tok_beta",
    )

    client = DataTableClient(app_client=_FakeAppClient(settings=settings_obj))
    client._bitable = fake_bitable  # type: ignore[assignment]

    targets = await client.ensure_table()
    assert len(targets) == 2
    # 每个 token 都返回 (token, table_id) — table_id 用 fake 默认 "tb_data"
    assert ("tok_alpha", "tb_data") in targets
    assert ("tok_beta", "tb_data") in targets

    # 每个 token 的字段表独立被创建
    assert ("tok_alpha", "tb_data") in fake_bitable._fields_by_token
    assert ("tok_beta", "tb_data") in fake_bitable._fields_by_token


@pytest.mark.asyncio
async def test_bulk_insert_unbounded_writes_to_all_targets(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-35-D: 一次 sync 把所有 RawItem 落到所有 target(广播)。

    3 行 → 2 target → 每个 target 各 3 行,result["inserted_rows"] = 6,
    result["inserted"] = 3(unique items landed)。
    """
    from app.config import get_settings
    from app.services.feishu.data_table import DataTableClient

    settings_obj = get_settings()
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "tok_bot_space,tok_user_space",
    )

    client = DataTableClient(app_client=_FakeAppClient(settings=settings_obj))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    result = await client.bulk_insert_raw_items_unbounded(
        session=sqlite_session, chunk_size=500
    )

    # unique items landed
    assert result["inserted"] == 3
    # broadcast sum
    assert result["inserted_rows"] == 6  # 3 rows × 2 targets
    assert result["skipped_duplicate"] == 0
    assert result["scanned"] == 3
    # 2 个 target 都在聚合里
    assert len(result["targets"]) == 2
    target_keys = {t["app_token"] for t in result["targets"]}
    assert target_keys == {"tok_bot_space", "tok_user_space"}
    # 每个 target 独立 inserted = 3
    for t in result["targets"]:
        assert t["inserted"] == 3
        assert t["skipped_duplicate"] == 0
        assert t["error"] is None

    # 数据层面: 每个 token 的桶里都有 3 行
    bot_records = fake_bitable._records_by_token[("tok_bot_space", "tb_data")]
    user_records = fake_bitable._records_by_token[("tok_user_space", "tb_data")]
    assert len(bot_records) == 3
    assert len(user_records) == 3
    bot_keys = {r["fields"]["Source"] for r in bot_records}
    user_keys = {r["fields"]["Source"] for r in user_records}
    assert bot_keys == {"github:gh-1", "github:gh-2", "reddit:rd-1"}
    assert user_keys == {"github:gh-1", "github:gh-2", "reddit:rd-1"}


@pytest.mark.asyncio
async def test_bulk_insert_per_target_dedup_independent(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-35-D: per-target dedup — 目标 A 已有某主键,目标 B 没,广播仍写入 B。

    预填目标 A 一行(github:gh-1),目标 B 空。Sync 跑完后:
      * A: 2 新行(github:gh-2, reddit:rd-1),skipped_dup=1
      * B: 3 新行,skipped_dup=0
      * unique inserted = 3(全部)
    """
    from app.config import get_settings
    from app.services.feishu.data_table import DataTableClient

    settings_obj = get_settings()
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "tok_a,tok_b",
    )

    # 预填目标 A 的桶里放一行(模拟"用户已经在 A 表手建了这条")
    fake_bitable._records_by_token[("tok_a", "tb_data")] = [
        {"record_id": "pre_a_1", "table_id": "tb_data",
         "fields": {"Source": "github:gh-1", "Title": "pre-existing"}},
    ]

    client = DataTableClient(app_client=_FakeAppClient(settings=settings_obj))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    result = await client.bulk_insert_raw_items_unbounded(session=sqlite_session)

    # unique items = 3 (gh-1, gh-2, rd-1)
    assert result["inserted"] == 3
    # broadcast = 5 (gh-2+rd-1 写到 A,3 行写到 B)
    assert result["inserted_rows"] == 5

    # target A: gh-1 skipped,gh-2/rd-1 inserted
    target_a = next(t for t in result["targets"] if t["app_token"] == "tok_a")
    assert target_a["inserted"] == 2
    assert target_a["skipped_duplicate"] == 1
    assert target_a["error"] is None

    # target B: 全部 inserted,skipped=0
    target_b = next(t for t in result["targets"] if t["app_token"] == "tok_b")
    assert target_b["inserted"] == 3
    assert target_b["skipped_duplicate"] == 0
    assert target_b["error"] is None

    # 数据验证: target A 桶里只有 2 行(原 1 + 新 2)
    a_records = fake_bitable._records_by_token[("tok_a", "tb_data")]
    assert len(a_records) == 3
    a_keys = {r["fields"]["Source"] for r in a_records}
    assert a_keys == {"github:gh-1", "github:gh-2", "reddit:rd-1"}
    # target B 桶里有 3 行
    b_records = fake_bitable._records_by_token[("tok_b", "tb_data")]
    assert len(b_records) == 3


@pytest.mark.asyncio
async def test_bulk_insert_target_failure_does_not_break_others(
    sqlite_session: Any,
    seeded_raw_items: list[ORMRawItem],
    fake_bitable: _FakeBitableClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-35-D: 一个 target batch_create 失败 → 其它 target 仍写入。

    模拟 tok_a 桶的 batch_create 抛 FeishuContentError,tok_b 正常。返回
    里 tok_a 标 error, tok_b 标 inserted=3。
    """
    from app.config import get_settings
    from app.services.feishu.content_client import FeishuContentError
    from app.services.feishu.data_table import DataTableClient

    settings_obj = get_settings()
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "tok_a,tok_b",
    )

    client = DataTableClient(app_client=_FakeAppClient(settings=settings_obj))
    client._bitable = fake_bitable  # type: ignore[assignment]
    client._bitable._session = sqlite_session  # type: ignore[attr-defined]

    real_batch = type(client._bitable).batch_create_records

    async def _selective_batch(
        self: Any, **kwargs: Any
    ) -> int:
        # tok_a 触发失败
        tok = kwargs.get("app_token") or fake_bitable._app_token
        if tok == "tok_a":
            raise FeishuContentError("feishu 502 from tok_a")
        return await real_batch(self, **kwargs)

    import unittest.mock
    with unittest.mock.patch.object(
        type(client._bitable), "batch_create_records", _selective_batch
    ):
        result = await client.bulk_insert_raw_items_unbounded(session=sqlite_session)

    # tok_a 失败但 tok_b 成功 — unique items 仍按"落入至少一个 target"算
    assert result["inserted"] == 3  # 全部 3 行都进了 tok_b
    assert result["inserted_rows"] == 3  # tok_a 0, tok_b 3

    target_a = next(t for t in result["targets"] if t["app_token"] == "tok_a")
    target_b = next(t for t in result["targets"] if t["app_token"] == "tok_b")
    assert target_a["inserted"] == 0
    assert target_a["error"] is not None
    assert "feishu 502" in target_a["error"]
    assert target_b["inserted"] == 3
    assert target_b["error"] is None

    # tok_b 桶里真有 3 行
    b_records = fake_bitable._records_by_token[("tok_b", "tb_data")]
    assert len(b_records) == 3
    # tok_a 桶里还是空
    a_records = fake_bitable._records_by_token.get(("tok_a", "tb_data"), [])
    assert a_records == []


@pytest.mark.asyncio
async def test_update_screening_results_broadcasts_to_all_targets(
    sqlite_session: Any, fake_bitable: _FakeBitableClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-35-D: update_screening_results 多目标 — 每个 target 都更新一次。"""
    from app.config import get_settings
    from app.services.feishu.data_table import DataTableClient

    settings_obj = get_settings()
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token",
        "tok_a,tok_b",
    )

    # 每个 target 桶里都 pre-seed 3 行
    seed_rows = [
        {"record_id": "rec_a_1", "table_id": "tb_data",
         "fields": {"Source": "github:g1", "Title": "t1"}},
        {"record_id": "rec_a_2", "table_id": "tb_data",
         "fields": {"Source": "github:g2", "Title": "t2"}},
        {"record_id": "rec_a_3", "table_id": "tb_data",
         "fields": {"Source": "reddit:r1", "Title": "r1"}},
    ]
    fake_bitable._records_by_token[("tok_a", "tb_data")] = [
        dict(r, record_id=r["record_id"].replace("rec_a_", "rec_a_"))
        for r in seed_rows
    ]
    seed_rows_b = [
        {"record_id": rid.replace("rec_a_", "rec_b_"),
         "table_id": "tb_data",
         "fields": r["fields"]}
        for rid, r in zip(["rec_a_1", "rec_a_2", "rec_a_3"], seed_rows)
    ]
    fake_bitable._records_by_token[("tok_b", "tb_data")] = seed_rows_b
    # 兼容老 flat list(某些测试 helper 仍读它)
    fake_bitable._records = seed_rows

    client = DataTableClient(app_client=_FakeAppClient(settings=settings_obj))
    client._bitable = fake_bitable  # type: ignore[assignment]

    mapping = {
        "github:g1": {"Category": "x", "Score": 80, "Opportunity ID": 1},
        "github:g2": {"Category": "y", "Score": 70, "Opportunity ID": 2},
        "reddit:r1": {"Category": "z", "Score": 60, "Opportunity ID": 3},
    }
    updated = await client.update_screening_results(mapping=mapping)
    # 3 行 × 2 target = 6 updates(aggregate unique count = 6)
    assert updated == 6

    # 每个 target 桶里的 record 都被更新
    for tok in ("tok_a", "tok_b"):
        for r in fake_bitable._records_by_token[(tok, "tb_data")]:
            assert "Category" in r["fields"], (
                f"{tok} 没被 update_record 写入 Category: {r}"
            )