"""Tests for Phase 17D — `?sort=signal_score` on `/api/opportunities`.

The DB query is satisfied by a SQL subquery:
  AVG(Signal.signal_score) joined via OpportunitySource.raw_item_id.

The demo fallback always sorts by total_score (no Signal rows exist
in demo data).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import Opportunity, OpportunitySource, RawItem, Signal, Source

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers — seed an Opportunity + Signal rows (transitive FK via RawItem).
# ---------------------------------------------------------------------------
async def _seed_source(session) -> int:
    src = Source(name="test-src", type="rss", url="https://example.test")
    session.add(src)
    await session.flush()
    return src.id


async def _seed_opportunity_with_signals(
    session, *, title: str, total_score: float, signal_scores: list[float]
) -> Opportunity:
    src_id = await _seed_source(session)
    opp = Opportunity(
        slug=f"opp-{title.replace(' ', '-').lower()}",
        title=title,
        summary=title,
        category="AI",
        market="global",
        target_user="creators",
        total_score=total_score,
        status="researched",
    )
    session.add(opp)
    await session.flush()

    for i, score in enumerate(signal_scores):
        raw = RawItem(
            source_id=src_id,
            external_id=f"ext-{title}-{i}",
            url=f"https://example.test/{title}/{i}",
            title=f"raw-{i}",
            content_hash=f"h{title}{i}",
        )
        session.add(raw)
        await session.flush()
        sig = Signal(
            raw_item_id=raw.id,
            signal_type="trend",
            signal_score=score,
            confidence_score=score,
            status="verified",
        )
        session.add(sig)
        await session.flush()
        session.add(
            OpportunitySource(opportunity_id=opp.id, raw_item_id=raw.id, relevance=0.5)
        )
        await session.flush()
    return opp


# ---------------------------------------------------------------------------
# DB query — sort signal_score
# ---------------------------------------------------------------------------
class TestSortSignalScore:
    async def test_orders_by_avg_signal_score_desc(self, sqlite_session):
        # opp-a has avg ~85, opp-b has avg ~30 — signal_score sort
        # must put opp-a first; total_score sort would also do that
        # here, so use a counter-example.
        await _seed_opportunity_with_signals(
            sqlite_session,
            title="opp-hot-signal",
            total_score=50.0,  # intentionally low
            signal_scores=[80.0, 90.0],
        )
        await _seed_opportunity_with_signals(
            sqlite_session,
            title="opp-cold-signal",
            total_score=99.0,  # intentionally high
            signal_scores=[10.0, 20.0, 30.0],
        )
        await sqlite_session.commit()

        from app.repositories import OpportunityRepository

        repo = OpportunityRepository(sqlite_session)
        rows, total = await repo.list_paginated(sort="signal_score")
        assert total == 2
        # signal_score sort → opp-hot-signal (avg 85) comes first.
        assert rows[0].title == "opp-hot-signal"
        assert rows[1].title == "opp-cold-signal"

    async def test_default_sort_is_total_score(self, sqlite_session):
        await _seed_opportunity_with_signals(
            sqlite_session, title="opp-low-total",
            total_score=30.0, signal_scores=[90.0],
        )
        await _seed_opportunity_with_signals(
            sqlite_session, title="opp-high-total",
            total_score=95.0, signal_scores=[10.0],
        )
        await sqlite_session.commit()

        from app.repositories import OpportunityRepository

        repo = OpportunityRepository(sqlite_session)
        rows, _ = await repo.list_paginated()  # default
        assert rows[0].title == "opp-high-total"
        assert rows[1].title == "opp-low-total"

    async def test_explicit_total_score_sort(self, sqlite_session):
        await _seed_opportunity_with_signals(
            sqlite_session, title="opp-a", total_score=30.0, signal_scores=[90.0],
        )
        await _seed_opportunity_with_signals(
            sqlite_session, title="opp-b", total_score=95.0, signal_scores=[10.0],
        )
        await sqlite_session.commit()

        from app.repositories import OpportunityRepository

        repo = OpportunityRepository(sqlite_session)
        rows, _ = await repo.list_paginated(sort="total_score")
        assert rows[0].title == "opp-b"
        assert rows[1].title == "opp-a"

    async def test_opportunity_with_no_signals_lands_last(self, sqlite_session):
        # One opportunity with signals (high avg), one without.
        await _seed_opportunity_with_signals(
            sqlite_session, title="opp-with-signals",
            total_score=10.0, signal_scores=[90.0],
        )
        opp_no_signal = Opportunity(
            slug="opp-no-signal",
            title="opp-no-signal",
            summary="...",
            total_score=99.0,  # even higher total_score
            status="researched",
        )
        sqlite_session.add(opp_no_signal)
        await sqlite_session.commit()

        from app.repositories import OpportunityRepository

        repo = OpportunityRepository(sqlite_session)
        rows, total = await repo.list_paginated(sort="signal_score")
        assert total == 2
        # AVG is NULL → nullslast → opp-no-signal lands at end.
        assert rows[0].title == "opp-with-signals"
        assert rows[1].title == "opp-no-signal"


# ---------------------------------------------------------------------------
# HTTP endpoint — sort param
# ---------------------------------------------------------------------------
class TestSortQueryParam:
    def test_sort_signal_score_via_http(self, client):
        """`?sort=signal_score` is accepted by FastAPI and threaded through."""
        resp = client.get("/api/opportunities?sort=signal_score&limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body

    def test_invalid_sort_returns_422(self, client):
        resp = client.get("/api/opportunities?sort=bogus_key")
        # FastAPI Query(pattern=...) rejects with 422.
        assert resp.status_code == 422

    def test_default_sort_unchanged(self, client):
        resp = client.get("/api/opportunities")
        assert resp.status_code == 200
