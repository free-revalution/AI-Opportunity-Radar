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

import json
from datetime import datetime
from typing import Any, Iterable, Optional

from app.config import Settings, get_settings
from app.services.feishu.app_client import FeishuAppClient
from app.services.feishu.content_client import (
    FeishuBitableClient,
    FeishuContentError,
)
from app.services.ingestion.raw_item import RawItem
from app.utils import get_logger

logger = get_logger(__name__)


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


def _primary_key_for(item: RawItem) -> str:
    """构造 Data 表主键。``Source`` 列存 ``"source:external_id"``。

    注: RawItem dataclass 字段叫 ``source_id``(上行 ID),
    数据库 ``raw_items.external_id`` 列就是从这里映射过去(见
    ``RawItemRepository.upsert``)。这里用 ``source_id`` 直接拼主键,
    保证与入库值一致。
    """
    external = (item.source_id or "").strip()
    return f"{item.source}:{external}" if external else f"{item.source}:"


def _row_for(*, item: RawItem, run_id: int) -> dict[str, Any]:
    """把 RawItem 序列化为 Data 表一行。"""
    fields: dict[str, Any] = {
        "Run ID": run_id,
        "Source": _primary_key_for(item),
        "External ID": (item.source_id or "")[:255],
        "Title": (item.title or "")[:500],
        "URL": (item.url or "")[:1024],
        "Content Hash": item.source_id or "",  # RawItem 没有 content_hash 字段,用 source_id 占位
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
        items: list[RawItem],
        run_id: int,
    ) -> dict[str, int]:
        """把一批 RawItem 写入 Data 表。

        Returns:
          ``{"inserted": N, "skipped_duplicate": M}``。
        """
        if not items:
            return {"inserted": 0, "skipped_duplicate": 0}

        # 预查去重
        primary_keys = [_primary_key_for(it) for it in items]
        existing = await self.existing_sources(primary_keys)

        new_items = [
            it for it, pk in zip(items, primary_keys) if pk and pk not in existing
        ]
        skipped = len(items) - len(new_items)

        if not new_items:
            return {"inserted": 0, "skipped_duplicate": skipped}

        try:
            _, table_id = await self.ensure_table()
        except FeishuContentError as exc:
            logger.warning(
                "feishu_data_table_ensure_failed_skip_insert",
                error=str(exc),
                n=len(new_items),
            )
            return {"inserted": 0, "skipped_duplicate": skipped}

        records = [
            {"fields": _row_for(item=it, run_id=run_id)} for it in new_items
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

        results = await asyncio.gather(
            *[_one(src, payload) for src, payload in mapping.items()]
        )
        updated = sum(1 for ok in results if ok)
        logger.info(
            "feishu_data_table_backfilled",
            updated=updated,
            requested=len(mapping),
        )
        return updated


__all__ = ["DataTableClient"]
