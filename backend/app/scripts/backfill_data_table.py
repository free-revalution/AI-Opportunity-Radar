"""Phase 31 P31-A — 全量回填存量 RawItem 到 Data 多维表格。

Usage:
    # Dry-run(只 SELECT count,不打飞书)
    python -m app.scripts.backfill_data_table --dry-run

    # 全量回填
    python -m app.scripts.backfill_data_table --apply

    # 只回填某个时间点之后
    python -m app.scripts.backfill_data_table --apply --since 2026-08-01

Notes:
    * 默认 ``--dry-run`` — 防误操作。运营确认数字后再 ``--apply``。
    * CLI 而非 HTTP endpoint:回填可能跑 30+ 分钟,HTTP 易超时;
      CLI 跑可看实时进度。
    * 仍走 ``existing_sources`` 主键预查,**幂等**:重跑不重复写。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func as sa_func
from sqlalchemy import select as sa_select

from app.db import close_db, get_sessionmaker
from app.models import RawItem
from app.services.feishu.app_client import FeishuAppClient
from app.services.feishu.data_table import DataTableClient
from app.utils import get_logger

logger = get_logger(__name__)


async def _count_raw_items(*, since: Optional[datetime]) -> int:
    """统计满足条件的 RawItem 总数(给 dry-run 用)。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        stmt = sa_select(sa_func.count(RawItem.id))
        if since is not None:
            stmt = stmt.where(RawItem.fetched_at >= since)
        return int((await session.execute(stmt)).scalar_one() or 0)


async def _run_backfill(
    *,
    since: Optional[datetime],
    chunk_size: int,
    dry_run: bool,
) -> dict[str, Any]:
    """实际跑回填。``dry_run=True`` 时只 SELECT count。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        if dry_run:
            total = await _count_raw_items(since=since)
            return {
                "dry_run": True,
                "would_scan": total,
                "since": since.isoformat() if since else None,
            }

        settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
        app_client = FeishuAppClient(settings=settings)
        client = DataTableClient(app_client=app_client, settings=settings)

        last_inserted = 0

        async def _on_progress(inserted_so_far: int, total: int) -> None:
            nonlocal last_inserted
            # 每 +50 行打一条进度(避免日志洪水)
            if inserted_so_far - last_inserted >= 50 or inserted_so_far == total:
                last_inserted = inserted_so_far
                logger.info(
                    "backfill_progress",
                    inserted=inserted_so_far,
                    total=total,
                    pct=round(100.0 * inserted_so_far / total, 1) if total else 0,
                )

        result = await client.bulk_insert_raw_items_unbounded(
            session=session,
            since=since,
            chunk_size=chunk_size,
            on_progress=_on_progress,
        )
        return {
            "dry_run": False,
            **result,
            "since": since.isoformat() if since else None,
        }


async def main_async() -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="Backfill all RawItems into Feishu Data multi-dimensional table."
    )
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="(default) 只 SELECT count,不打飞书",
    )
    mode.add_argument(
        "--apply",
        action="store_false",
        dest="dry_run",
        help="真正写飞书 Data 表",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO 日期(YYYY-MM-DD),只回填这个时间点之后",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="每批 Feishu batch_create 行数(默认 500,与 Feishu 上限一致)",
    )
    args = parser.parse_args()

    since: Optional[datetime] = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since)
        except ValueError:
            print(f"Invalid --since: {args.since} (want YYYY-MM-DD)", file=sys.stderr)
            return {"error": "invalid_since"}

    result = await _run_backfill(
        since=since,
        chunk_size=args.chunk_size,
        dry_run=args.dry_run,
    )

    print("\n=== Backfill result ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    return result


async def main() -> None:
    try:
        await main_async()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())