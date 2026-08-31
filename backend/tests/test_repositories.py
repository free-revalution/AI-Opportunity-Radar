"""Repository integration tests (SQLite in-memory)."""

from __future__ import annotations

import pytest

from app.models import Opportunity
from app.repositories import (
    OpportunityRepository,
    RawItemRepository,
    SignalRepository,
    SourceRepository,
    compute_content_hash,
)


pytestmark = pytest.mark.asyncio


# ----------------- opportunities -----------------
async def test_opportunity_create_and_get(sqlite_session):
    repo = OpportunityRepository(sqlite_session)
    opp = await repo.create(
        title="AI Foo",
        slug="ai-foo",
        summary="desc",
        category="AI SaaS",
        total_score=80,
        trend_score=80,
        demand_score=80,
        monetization_score=80,
        competition_gap_score=80,
        china_gap_score=80,
        execution_score=80,
    )
    assert opp.id is not None

    fetched = await repo.get_by_id(opp.id)
    assert fetched is not None
    assert fetched.title == "AI Foo"
    assert fetched.slug == "ai-foo"

    by_slug = await repo.get_by_slug("ai-foo")
    assert by_slug is not None
    assert by_slug.id == opp.id


async def test_opportunity_upsert_by_slug_is_idempotent(sqlite_session):
    repo = OpportunityRepository(sqlite_session)
    fields = dict(
        title="AI Foo", slug="ai-foo", summary="desc",
        total_score=80, trend_score=80, demand_score=80,
        monetization_score=80, competition_gap_score=80,
        china_gap_score=80, execution_score=80,
    )
    first = await repo.upsert_by_slug(**fields)
    second = await repo.upsert_by_slug(**fields)
    assert first.id == second.id


async def test_opportunity_pagination_orders_by_score(sqlite_session):
    repo = OpportunityRepository(sqlite_session)
    for slug, score in [("low", 40), ("high", 90), ("mid", 60)]:
        await repo.create(
            title=slug, slug=slug, summary="",
            total_score=score, trend_score=score, demand_score=score,
            monetization_score=score, competition_gap_score=score,
            china_gap_score=score, execution_score=score,
        )

    page, total = await repo.list_paginated(limit=2, offset=0)
    assert total == 3
    assert [o.slug for o in page] == ["high", "mid"]

    page2, _ = await repo.list_paginated(limit=2, offset=2)
    assert [o.slug for o in page2] == ["low"]


async def test_opportunity_pagination_filters(sqlite_session):
    repo = OpportunityRepository(sqlite_session)
    await repo.create(
        title="x", slug="x", summary="", category="AI SaaS",
        total_score=10, trend_score=10, demand_score=10, monetization_score=10,
        competition_gap_score=10, china_gap_score=10, execution_score=10,
    )
    await repo.create(
        title="y", slug="y", summary="", category="Agent",
        total_score=90, trend_score=90, demand_score=90, monetization_score=90,
        competition_gap_score=90, china_gap_score=90, execution_score=90,
    )

    page, total = await repo.list_paginated(category="Agent", min_total_score=50)
    assert total == 1
    assert page[0].slug == "y"


# ----------------- raw items -----------------
async def test_content_hash_is_stable():
    h1 = compute_content_hash("https://example.com/x", "Hello World")
    h2 = compute_content_hash("https://example.com/x", "hello world")
    assert h1 == h2  # case + whitespace insensitive
    h3 = compute_content_hash("https://example.com/x", "different")
    assert h1 != h3


async def test_raw_item_upsert_dedup_by_source_external(sqlite_session):
    repo = RawItemRepository(sqlite_session)
    item1, created1 = await repo.upsert(
        source_id=1, external_id="abc",
        url="https://example.com/post",
        title="Hello",
    )
    assert created1 is True
    item2, created2 = await repo.upsert(
        source_id=1, external_id="abc",
        url="https://example.com/post",
        title="Hello",
    )
    assert created2 is False
    assert item1.id == item2.id


async def test_raw_item_upsert_dedup_by_content_hash_across_sources(sqlite_session):
    repo = RawItemRepository(sqlite_session)
    # Different source_id / external_id but same (url, title) ⇒ same content_hash.
    item1, created1 = await repo.upsert(
        source_id=1, external_id="a",
        url="https://example.com/x", title="Foo",
    )
    item2, created2 = await repo.upsert(
        source_id=2, external_id="b",
        url="https://example.com/x", title="Foo",
    )
    assert created1 is True
    assert created2 is False
    assert item1.id == item2.id


# ----------------- signals -----------------
async def test_signal_create_and_query(sqlite_session):
    repo = SignalRepository(sqlite_session)
    s = await repo.create(
        raw_item_id=1, signal_type="velocity",
        keyword="ai avatar", velocity_score=80.0,
        engagement_score=70.0, relevance_score=90.0,
    )
    assert s.id is not None
    found = await repo.get_by_raw_item(1)
    assert any(x.id == s.id for x in found)


# ----------------- sources -----------------
async def test_source_upsert(sqlite_session):
    repo = SourceRepository(sqlite_session)
    src = await repo.upsert(
        name="GitHub", type="api",
        url="https://github.com/trending", enabled=True,
    )
    assert src.id is not None
    again = await repo.upsert(
        name="GitHub", type="api",
        url="https://github.com/trending", enabled=False,
    )
    assert again.id == src.id
    assert again.enabled is False


async def test_source_upsert_idempotent_for_names_with_spaces(sqlite_session):
    """Phase 28 regression — names like ``"Hacker News"`` contain a space
    so the old ``Source.url.contains(slug)`` lookup never matched and
    every pipeline run appended a new row. The fix queries by
    ``Source.name.ilike(slug)`` so the second upsert returns the same
    row instead of creating a duplicate.
    """
    repo = SourceRepository(sqlite_session)
    first = await repo.upsert(
        name="Hacker News", type="api",
        url="https://example.com/hackernews", enabled=True,
    )
    second = await repo.upsert(
        name="Hacker News", type="api",
        url="https://example.com/hackernews", enabled=False,
    )
    assert second.id == first.id
    assert second.enabled is False


async def test_source_get_by_slug_uses_name_not_url(sqlite_session):
    """Direct check on :meth:`SourceRepository.get_by_slug`.

    The bug fix changed the lookup from ``Source.url.contains(slug)``
    to ``Source.name.ilike(slug)``. We seed one row with name
    ``"Product Hunt"`` and url ``"https://example.com/producthunt"``
    and verify ``get_by_slug`` finds it by name, not by URL.
    """
    repo = SourceRepository(sqlite_session)
    seeded = await repo.upsert(
        name="Product Hunt", type="api",
        url="https://example.com/producthunt", enabled=True,
    )
    found = await repo.get_by_slug("product hunt")
    assert found is not None
    assert found.id == seeded.id
    # — negative case: looking up by URL substring must NOT pick up
    # unrelated rows whose URL contains the same substring.
    other = await repo.upsert(
        name="Wikipedia", type="rss",
        url="https://example.com/producthunt-alt", enabled=True,
    )
    again = await repo.get_by_slug("product hunt")
    assert again is not None
    assert again.id == seeded.id  # — name match wins, not URL match
    assert again.id != other.id