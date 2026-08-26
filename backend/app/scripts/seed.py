"""Seed demo opportunities into the database.

Usage:
    make seed
    # or
    python -m app.scripts.seed
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import close_db, get_sessionmaker
from app.models import Opportunity
from app.utils import get_logger

logger = get_logger(__name__)

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "demo_opportunities.json"


async def seed_opportunities() -> int:
    """Insert demo opportunities (idempotent — skip if slug already exists)."""
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(FIXTURE_PATH)

    payload: list[dict[str, Any]] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sessionmaker = get_sessionmaker()
    inserted = 0

    async with sessionmaker() as session:
        for item in payload:
            slug = item.get("slug") or item["id"]
            existing = await session.execute(select(Opportunity).where(Opportunity.slug == slug))
            if existing.scalar_one_or_none() is not None:
                logger.info("seed_skip", slug=slug)
                continue
            opp = Opportunity(
                title=item["title"],
                slug=slug,
                summary=item.get("summary"),
                category=item.get("category"),
                market=item.get("market"),
                target_user=item.get("target_user"),
                source_count=item.get("source_count", 0),
                trend_score=item.get("trend_score", 0),
                demand_score=item.get("demand_score", 0),
                monetization_score=item.get("monetization_score", 0),
                competition_gap_score=item.get("competition_gap_score", 0),
                china_gap_score=item.get("china_gap_score", 0),
                execution_score=item.get("execution_score", 0),
                total_score=item.get("total_score", 0),
                status=item.get("status", "detected"),
            )
            session.add(opp)
            inserted += 1
        await session.commit()

    return inserted


async def main() -> None:
    try:
        inserted = await seed_opportunities()
        logger.info("seed_done", inserted=inserted)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())