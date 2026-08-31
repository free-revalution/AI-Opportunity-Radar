"""飞书多维表格 — Data 表读写(Phase 30 - store-first 架构)

按 plan §2.2 实现。Data 表的设计目的:

  * **绝不丢数据** — 每次 /run 的**新增** RawItem(经 ingestion 去重后)
    全部写入 Data 表,按 Source(信息源) + 后续 screening 的 Category
    分类存储。
  * **可独立查询** — 运营 / 研究人员可以不看 screening 结果就拿
    到原始数据做透视 / 二次分析。
  * **回填 screening 字段** — screening 完成后把对应行的
    Category / Score / Opportunity ID 回填,方便溯源。

去重策略(plan D2):
  * 主键列 ``Source = "source:external_id"``
  * 写入前一次 ``list_records(filter_=Source in [...])`` 拉已存在
    主键集合 → Python 端过滤 → ``batch_create_records`` 落盘。

复用:
  * ``FeishuBitableClient._TokenMixin._request``(Bearer + 一次性 token 重试)
  * ``FeishuBitableClient.batch_create_records``(chunk_size=500)
  * ``FeishuBitableClient.list_records``(带 filter_)

不在这里实现:
  * upsert — 飞书 Bitable 没原生 upsert,用"先查后插"实现等价语义
  * delete — Data 表永远只增不删(plan D1)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Optional

from sqlalchemy import select as _sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import RawItem as ORMRawItem
from app.models import Source as ORMSource
from app.services.feishu.app_client import FeishuAppClient
from app.services.feishu.content_client import (
    FeishuBitableClient,
    FeishuContentError,
)
from app.services.ingestion.raw_item import RawItem
from app.utils import get_logger

logger = get_logger(__name__)

OnProgress = Callable[[int, int], Awaitable[None]]


# Data 表 schema(plan §3.1)。``ensure_table`` 首次创建时一次性写入。
#   type 1 = Text, type 2 = Number, type 5 = DateTime
# Source 列标记为 is_primary=True,作为 list_records 预查的主键。
_DATA_FIELDS: list[dict[str, Any]] = [
    {"field_name": "Run ID",          "type": 2},                 # Number
    {"field_name": "Source",          "type": 1, "is_primary": True},  # Text primary
    {"field_name": "External ID",     "type": 1},
    {"field_name": "Title",           "type": 1},
    {"field_name": "URL",             "type": 1},
    {"field_name": "Author",          "type": 1},
    {"field_name": "Published At",    "type": 5},                 # DateTime
    {"field_name": "Category",        "type": 1},                 # screening 回填
    {"field_name": "Score",           "type": 2},                 # screening 回填
    {"field_name": "Opportunity ID",  "type": 2},                 # screening 回填
    {"field_name": "Content Hash",    "type": 1},
    {"field_name": "Fetched At",      "type": 5},                 # DateTime
]
_DATA_TABLE_NAME = "Data"


def _source_slug_for_item(
    *,
    item: RawItem,
    source_name: Optional[str] = None,
) -> str:
    """提取一个 item 的信息源 slug,Data 表主键需要它。

    支持两种输入:
      * dataclass ``RawItem``(in-memory,connector 直接产出):
        ``item.source`` 就是 slug。
      * ORM ``RawItem``(从 ``raw_items`` 表读出):``item.source`` 不存在,
        必须传入预先 JOIN 出的 ``Source.name`` 作为 slug。

    没传 source_name 时回退到 dataclass 形态(抛错保护 — 强制 caller 显式选择)。
    """
    if source_name:
        return source_name.strip()
    # dataclass path
    if hasattr(item, "source") and isinstance(item.source, str) and item.source:
        return item.source.strip()
    raise ValueError(
        "Cannot resolve source slug for RawItem — pass source_name "
        "(ORM path) or use the dataclass path (ingest)."
    )


def _primary_key_for(
    item: RawItem,
    *,
    source_name: Optional[str] = None,
) -> str:
    """构造 Data 表主键。``Source`` 列存 ``"source_slug:external_id"``。

    Args:
      item: dataclass ``RawItem`` 或 ORM ``RawItem``。ORM 路径必须传 source_name。
      source_name: ORM JOIN 出的 ``Source.name``(可选)。

    Notes:
      * dataclass ``RawItem.source_id`` = upstream 字符串 ID(无空白)
      * ORM ``RawItem.external_id`` = 同上的数据库列;``RawItem.source_id`` 是 FK int
      * 两者取出后 ``.strip()`` 即可,语义一致
    """
    source = _source_slug_for_item(item=item, source_name=source_name)
    external = _external_id_for(item)
    return f"{source}:{external}" if external else f"{source}:"


def _external_id_for(item: Any) -> str:
    """取出 external_id 字符串(dataclass 或 ORM 通用)。

    - dataclass ``RawItem``: ``item.source_id`` 是 str(upstream ID)
    - ORM ``RawItem``:      ``item.external_id`` 是 str 列;``item.source_id`` 是 int
    """
    # ORM 实例
    if hasattr(item, "_sa_instance_state"):
        val = getattr(item, "external_id", "") or ""
        return str(val).strip()
    # dataclass 路径 — item.source_id 是 str
    raw = getattr(item, "source_id", "")
    if isinstance(raw, int):  # 防御:不该发生
        return ""
    return (raw or "").strip()


def _row_for(
    *,
    item: RawItem,
    run_id: int,
    source_name: Optional[str] = None,
) -> dict[str, Any]:
    """把 RawItem 序列化为 Data 表一行。

    ``item``: dataclass 或 ORM 实例。ORM 路径必须传 source_name。
    """
    fields: dict[str, Any] = {
        "Run ID": run_id,
        "Source": _primary_key_for(item, source_name=source_name),
        "External ID": _external_id_for(item)[:255],
        "Title": (item.title or "")[:500],
        "URL": (item.url or "")[:1024],
        # dataclass 没有 content_hash,ORM 有。存哪个都不影响去重
        # (Data 表去重靠 Source 主键),保留一个字段方便运营溯源。
        "Content Hash": getattr(item, "content_hash", "") or "",
        "Fetched At": int((item.fetched_at or datetime.utcnow()).timestamp() * 1000),
    }
    if item.author:
        fields["Author"] = item.author[:255]
    if item.published_at:
        # Feishu DateTime 类型用 ms epoch
        fields["Published At"] = int(item.published_at.timestamp() * 1000)
    # Category / Score / Opportunity ID 留空,screening 后回填
    return fields


class DataTableClient:
    """Data 多维表格客户端(Phase 30)。"""

    TOKEN_SETTING = "feishu_bitable_data_app_token"

    def __init__(
        self,
        *,
        app_client: FeishuAppClient,
        settings: Optional[Settings] = None,
        table_name: str = _DATA_TABLE_NAME,
    ) -> None:
        self._bitable = FeishuBitableClient(
            app_client=app_client,
            settings=settings,
            table_name=table_name,
            token_setting=self.TOKEN_SETTING,
        )

    # ------------------------------------------------------------------
    # Lifecycle (委托给 FeishuBitableClient,保持一处实现)
    # ------------------------------------------------------------------
    async def ensure_table(self) -> tuple[str, str]:
        """确保 Data 表存在;返回 (app_token, table_id)。"""
        # ensure_table 走的是 Phase 7 的 _OPP_FIELDS,不是我们的 _DATA_FIELDS。
        # 我们在此绕开:先 ensure_app(自动建 app),再自己 list+create table,
        # 然后单独建字段,避免被 Phase 7 默认字段污染。
        await self._bitable.ensure_app()
        app_token = self._bitable._cached_app_token  # noqa: SLF001 — 内部约定
        if not app_token:
            raise FeishuContentError("ensure_app did not populate app_token")
        tables = await self._bitable.list_tables(app_token=app_token)
        for table in tables:
            if (table.get("name") or "").strip() == self._bitable._table_name:  # noqa: SLF001
                tid = (table.get("table_id") or "").strip()
                if tid:
                    return app_token, tid

        # 创建表(空表,字段稍后单独建)
        create_resp = await self._bitable._request(  # noqa: SLF001
            method="POST",
            path=f"/bitable/v1/apps/{app_token}/tables",
            json_body={"table": {"name": self._bitable._table_name}},  # noqa: SLF001
        )
        if create_resp.get("code") != 0:
            raise FeishuContentError(
                f"data table create rejected: code={create_resp.get('code')} "
                f"msg={create_resp.get('msg')}"
            )
        tid = (create_resp.get("data") or {}).get("table_id", "").strip()
        if not tid:
            raise FeishuContentError("data table create returned no table_id")

        # 建字段
        for field in _DATA_FIELDS:
            field_resp = await self._bitable._request(  # noqa: SLF001
                method="POST",
                path=f"/bitable/v1/apps/{app_token}/tables/{tid}/fields",
                json_body=field,
            )
            if field_resp.get("code") != 0:
                logger.warning(
                    "feishu_data_table_field_create_failed",
                    field_name=field["field_name"],
                    code=field_resp.get("code"),
                    msg=field_resp.get("msg"),
                )
        logger.info(
            "feishu_data_table_ready",
            app_token=app_token,
            table_id=tid,
        )
        return app_token, tid

    # ------------------------------------------------------------------
    # Dedup + bulk insert
    # ------------------------------------------------------------------
    async def existing_sources(self, sources: Iterable[str]) -> set[str]:
        """预查 Data 表中已存在的主键集合。"""
        sources_list = [s for s in sources if s]
        if not sources_list:
            return set()
        try:
            _, table_id = await self.ensure_table()
        except FeishuContentError as exc:
            logger.warning("feishu_data_table_ensure_failed_skip_dedup", error=str(exc))
            return set()

        existing: set[str] = set()
        # 飞书 list_records filter_ 支持 `is` operator 对 Text 列精确匹配;
        # 用 `or` 把每个 source 单独查(避免 array IN 的兼容性风险)
        for source_key in sources_list:
            try:
                items, _ = await self._bitable.list_records(
                    table_id=table_id,
                    page_size=10,
                    filter_={
                        "conditions": [
                            {
                                "field_name": "Source",
                                "operator": "is",
                                "value": [source_key],
                            }
                        ]
                    },
                )
            except FeishuContentError as exc:
                logger.warning(
                    "feishu_data_table_existing_lookup_failed",
                    source_key=source_key,
                    error=str(exc),
                )
                continue
            for item in items:
                fields = (item.get("fields") or {})
                val = fields.get("Source")
                if isinstance(val, list):
                    if val:
                        existing.add(str(val[0]))
                elif val is not None:
                    existing.add(str(val))
        return existing

    async def bulk_insert_raw_items(
        self,
        *,
        items: list[tuple[RawItem, Optional[str]]],
        run_id: int,
    ) -> dict[str, int]:
        """把一批 RawItem 写入 Data 表。

        Args:
          items: list of ``(item, source_name)`` tuples。
                 - dataclass path: ``(item, None)`` — connector 产出,``item.source`` 已有 slug
                 - ORM path: ``(item, source_name)`` — DB 读出,caller 必须预 JOIN 拿到 ``Source.name``

        Returns:
          ``{"inserted": N, "skipped_duplicate": M}``。
        """
        if not items:
            return {"inserted": 0, "skipped_duplicate": 0}

        # 预查去重(主键 = source_slug:external_id)
        primary_keys = [
            _primary_key_for(it, source_name=sn) for it, sn in items
        ]
        existing = await self.existing_sources(primary_keys)

        new_pairs = [
            (it, sn) for (it, sn), pk in zip(items, primary_keys)
            if pk and pk not in existing
        ]
        skipped = len(items) - len(new_pairs)

        if not new_pairs:
            return {"inserted": 0, "skipped_duplicate": skipped}

        try:
            _, table_id = await self.ensure_table()
        except FeishuContentError as exc:
            logger.warning(
                "feishu_data_table_ensure_failed_skip_insert",
                error=str(exc),
                n=len(new_pairs),
            )
            return {"inserted": 0, "skipped_duplicate": skipped}

        records = [
            {"fields": _row_for(item=it, run_id=run_id, source_name=sn)}
            for it, sn in new_pairs
        ]
        try:
            inserted = await self._bitable.batch_create_records(
                table_id=table_id, records=records
            )
        except FeishuContentError as exc:
            logger.error(
                "feishu_data_table_bulk_insert_failed",
                error=str(exc),
                n=len(records),
            )
            return {"inserted": 0, "skipped_duplicate": skipped}

        logger.info(
            "feishu_data_table_inserted",
            inserted=inserted,
            skipped_duplicate=skipped,
            run_id=run_id,
        )
        return {"inserted": inserted, "skipped_duplicate": skipped}

    # ------------------------------------------------------------------
    # Screening backfill
    # ------------------------------------------------------------------
    async def update_screening_results(
        self,
        *,
        mapping: dict[str, dict[str, Any]],
    ) -> int:
        """Screening 后回填。

        Args:
          mapping: ``{primary_key: {"Category": ..., "Score": ..., "Opportunity ID": ...}}``
                   主键 = ``"source:external_id"``(同 ``_primary_key_for``)。

        Returns:
          实际更新的行数。

        Notes:
          飞书 Bitable update_record 必须有 record_id,所以先 list_records
          按主键查 → 再 update_record。一行一个调用,并发用 asyncio.gather。
        """
        if not mapping:
            return 0
        try:
            _, table_id = await self.ensure_table()
        except FeishuContentError as exc:
            logger.warning(
                "feishu_data_table_ensure_failed_skip_backfill",
                error=str(exc),
                n=len(mapping),
            )
            return 0

        # 按 Source 批量查(每个主键一个 list 调用,简单可靠)
        record_id_by_source: dict[str, str] = {}
        for source_key in mapping.keys():
            try:
                items, _ = await self._bitable.list_records(
                    table_id=table_id,
                    page_size=10,
                    filter_={
                        "conditions": [
                            {
                                "field_name": "Source",
                                "operator": "is",
                                "value": [source_key],
                            }
                        ]
                    },
                )
            except FeishuContentError as exc:
                logger.warning(
                    "feishu_data_table_lookup_for_update_failed",
                    source_key=source_key,
                    error=str(exc),
                )
                continue
            for item in items:
                rid = (item.get("record_id") or "").strip()
                fields = item.get("fields") or {}
                val = fields.get("Source")
                if isinstance(val, list):
                    val = val[0] if val else None
                if rid and val is not None:
                    record_id_by_source[str(val)] = rid

        # 并发 update
        import asyncio

        async def _one(src_key: str, payload: dict[str, Any]) -> bool:
            rid = record_id_by_source.get(src_key)
            if not rid:
                return False
            try:
                await self._bitable.update_record(
                    table_id=table_id,
                    record_id=rid,
                    fields=payload,
                )
                return True
            except FeishuContentError as exc:
                logger.warning(
                    "feishu_data_table_update_record_failed",
                    source_key=src_key,
                    error=str(exc),
                )
                return False

        # Phase 32 PR-32-A: serial (not concurrent) updates.
        #
        # 飞书 Bitable 多维表格底层串行处理同一文档的写接口 — ``asyncio.gather``
        # 并发 N 个 ``update_record`` 会触发 ``code=1254291 "Write conflict"``。
        # 改成顺序 for 循环,每行单独 ``try/except``,update 失败计数记 metric。
        # 实际耗时:`Screening` 一次 /run 写 ~10-30 行,串行开销 ~300-900ms,
        # 但避免了并发冲突的 hard fail — 净收益。
        updated = 0
        failed = 0
        for src_key, payload in mapping.items():
            ok = await _one(src_key, payload)
            if ok:
                updated += 1
            else:
                failed += 1
        logger.info(
            "feishu_data_table_backfilled",
            updated=updated,
            failed=failed,
            requested=len(mapping),
        )
        # Prometheus counter — 用 ``record_external_error(provider='feishu', ...)``
        # 走现成标签轴,运营可在 ``/metrics`` 看 ``radar_external_service_errors_total``
        # 是否突然飙升(踩 1254291 的早期信号)。
        if failed:
            from app.metrics import record_external_error

            record_external_error(
                provider="feishu_data_table",
                kind="update_record_failed",
            )
        return updated

    # ------------------------------------------------------------------
    # Phase 31 — ORM path + 全量回填
    # ------------------------------------------------------------------
    async def bulk_insert_orm_raw_items(
        self,
        *,
        items: list[ORMRawItem],
        run_id: int,
        session: Optional[AsyncSession] = None,
    ) -> dict[str, int]:
        """把一批 ORM ``RawItem``(从 ``raw_items`` 表读出)写入 Data 表。

        走法:
          * 内部按 ``Source.name`` JOIN 出 slug
          * 对调用方仍是单调用(``_write_data_table`` 现有路径)

        Args:
          items: ORM RawItem 列表。
          run_id: 本次 Run ID。
          session: 可选 — 如果 items 没在某个 SQLAlchemy session 内
                   (例如刚 ``session.execute()`` 出来还没 ``.scalars().all()``
                   后 ``refresh``),caller 必须显式传入 session 用于 JOIN 查询。

        Notes:
          * 没有 ``Source.name`` 的孤儿行被静默跳过(并 warn)
          * 主键去重同 dataclass 路径,重跑幂等
        """
        if not items:
            return {"inserted": 0, "skipped_duplicate": 0, "skipped_orphan": 0}

        # ORM 对象需要预 JOIN 拿到 slug;这里做单次批量查询。
        source_ids = list({it.source_id for it in items if it.source_id is not None})
        source_name_by_id: dict[int, str] = {}
        if source_ids:
            if session is None:
                # 退路:从 ORM 实例拿 session。访问 _sa_instance_state
                # 不触发 lazy-load(只是读属性),不会进入 MissingGreenlet。
                if hasattr(items[0], "_sa_instance_state"):
                    session = items[0]._sa_instance_state.session  # type: ignore[assignment]
            if session is not None:
                stmt = _sa_select(ORMSource.id, ORMSource.name).where(
                    ORMSource.id.in_(source_ids)
                )
                rows = (await session.execute(stmt)).all()
                source_name_by_id = {sid: name for sid, name in rows}
            else:
                logger.warning(
                    "feishu_data_table_orm_path_no_session",
                    n=len(items),
                )

        pairs: list[tuple[ORMRawItem, Optional[str]]] = []
        orphan = 0
        for it in items:
            sn = source_name_by_id.get(it.source_id)
            if sn is None:
                orphan += 1
                logger.warning(
                    "feishu_data_table_orphan_skipped",
                    raw_item_id=it.id,
                    source_id=it.source_id,
                )
                continue
            pairs.append((it, sn))

        if not pairs:
            return {
                "inserted": 0,
                "skipped_duplicate": 0,
                "skipped_orphan": orphan,
            }

        result = await self.bulk_insert_raw_items(items=pairs, run_id=run_id)
        return {
            "inserted": result["inserted"],
            "skipped_duplicate": result["skipped_duplicate"],
            "skipped_orphan": orphan,
        }

    async def bulk_insert_raw_items_unbounded(
        self,
        *,
        session: AsyncSession,
        since: Optional[datetime] = None,
        chunk_size: int = 500,
        run_id_label: int = 0,
        on_progress: Optional[OnProgress] = None,
    ) -> dict[str, int]:
        """全量(回填)把存量 RawItem 推 Data 表 — Phase 31 P31-A。

        与 ``_write_data_table``(只取 ``fetched_at >= run.started_at``)
        不同,这里支持:
          * ``since`` 可选 — 不传 = 全部历史
          * 分批游标(按 id 升序)避免大表 OOM
          * ``on_progress(inserted_so_far, total)`` 异步回调,CLI 用来打进度

        主键去重仍走 ``existing_sources``,**幂等** — 重跑不重复。

        Returns:
          ``{"inserted", "skipped_duplicate", "skipped_orphan", "scanned"}``
        """
        # 1. 估算总数(给进度用;SQLite 不支持 subquery count,简化:跑一次 SELECT id)
        count_stmt = _sa_select(ORMRawItem.id)
        if since is not None:
            count_stmt = count_stmt.where(ORMRawItem.fetched_at >= since)
        total = len((await session.execute(count_stmt)).all())

        if total == 0:
            logger.info(
                "feishu_data_table_unbounded_noop",
                since=since.isoformat() if since else None,
            )
            return {
                "inserted": 0,
                "skipped_duplicate": 0,
                "skipped_orphan": 0,
                "scanned": 0,
            }

        # 2. 分批游标(按 id 升序;chunk_size 默认 500,与 Feishu batch_create 上限一致)
        last_id = 0
        inserted_total = 0
        skipped_dup_total = 0
        skipped_orphan_total = 0
        scanned_total = 0

        while True:
            # LEFT JOIN Source — 没有 Source 的孤儿显式 ``None`` 而不是被过滤,
            # 这样 ``skipped_orphan`` 才有意义(否则会被 INNER JOIN 静默吃掉)。
            stmt = (
                _sa_select(ORMRawItem, ORMSource.name)
                .outerjoin(ORMSource, ORMSource.id == ORMRawItem.source_id)
                .where(ORMRawItem.id > last_id)
                .order_by(ORMRawItem.id.asc())
                .limit(chunk_size)
            )
            if since is not None:
                stmt = stmt.where(ORMRawItem.fetched_at >= since)
            rows = list((await session.execute(stmt)).all())
            if not rows:
                break

            pairs: list[tuple[ORMRawItem, Optional[str]]] = [
                (raw, name) for raw, name in rows if name
            ]
            orphan = len(rows) - len(pairs)
            skipped_orphan_total += orphan

            if pairs:
                result = await self.bulk_insert_raw_items(
                    items=pairs, run_id=run_id_label
                )
                inserted_total += result["inserted"]
                skipped_dup_total += result["skipped_duplicate"]
            scanned_total += len(rows)

            last_id = rows[-1][0].id

            if on_progress is not None:
                try:
                    await on_progress(inserted_total, total)
                except Exception as exc:  # 进度回调出错不影响主流程
                    logger.warning(
                        "feishu_data_table_unbounded_progress_error",
                        error=str(exc),
                    )

            # 释放 ORM session,避免长跑 OOM
            await asyncio.sleep(0)

        logger.info(
            "feishu_data_table_unbounded_done",
            inserted=inserted_total,
            skipped_duplicate=skipped_dup_total,
            skipped_orphan=skipped_orphan_total,
            scanned=scanned_total,
            since=since.isoformat() if since else None,
        )
        return {
            "inserted": inserted_total,
            "skipped_duplicate": skipped_dup_total,
            "skipped_orphan": skipped_orphan_total,
            "scanned": scanned_total,
        }


__all__ = ["DataTableClient"]
