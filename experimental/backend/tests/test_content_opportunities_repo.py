"""Tests for Phase 17A — `ContentOpportunityRepository`.

Covers CRUD + state-machine transitions:
  * create + get_by_id
  * list_paginated with status/signal_id filters
  * transition_status success path
  * transition_status illegal transition raises IllegalStatusTransition
  * transition_status missing id raises NotFound
"""

from __future__ import annotations

import pytest

from app.repositories import (
    ContentOpportunityRepository,
    IllegalStatusTransition,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
class TestCreate:
    async def test_create_returns_row_with_id(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        row = await repo.create(
            signal_id=42,
            platform="xiaohongshu",
            audience="creators",
            niche="writing",
            tone="专业",
            hook="AI 颠覆跨境电商",
            content_score=85.0,
            status="draft",
            metadata_json={"feishu_open_id": "ou_test"},
        )
        await sqlite_session.commit()
        assert row.id is not None
        assert row.platform == "xiaohongshu"
        assert row.hook == "AI 颠覆跨境电商"
        assert row.metadata_json["feishu_open_id"] == "ou_test"

    async def test_create_defaults_metadata_json_to_empty_dict(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        row = await repo.create(signal_id=1, status="draft")
        await sqlite_session.commit()
        # Empty dict lands as ``{}`` not NULL — keeps JSON_EXTRACT simple.
        assert row.metadata_json == {}


class TestGetById:
    async def test_returns_none_for_missing_id(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        assert await repo.get_by_id(999) is None

    async def test_returns_row_when_present(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        created = await repo.create(signal_id=1, status="draft")
        await sqlite_session.commit()
        got = await repo.get_by_id(created.id)
        assert got is not None
        assert got.id == created.id


# ---------------------------------------------------------------------------
# list_paginated
# ---------------------------------------------------------------------------
class TestListPaginated:
    async def test_empty_table_returns_zero(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        rows, total = await repo.list_paginated()
        assert rows == []
        assert total == 0

    async def test_filter_by_status(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        await repo.create(signal_id=1, status="draft")
        await repo.create(signal_id=2, status="approved")
        await repo.create(signal_id=3, status="published")
        await sqlite_session.commit()

        drafts, total_drafts = await repo.list_paginated(status="draft")
        assert total_drafts == 1
        assert drafts[0].signal_id == 1

    async def test_filter_by_signal_id(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        await repo.create(signal_id=10, status="draft")
        await repo.create(signal_id=10, status="approved")
        await repo.create(signal_id=20, status="draft")
        await sqlite_session.commit()

        rows, total = await repo.list_paginated(signal_id=10)
        assert total == 2
        assert all(r.signal_id == 10 for r in rows)

    async def test_pagination_offset(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        for i in range(5):
            await repo.create(signal_id=i, status="draft")
        await sqlite_session.commit()

        page1, total = await repo.list_paginated(limit=2, offset=0)
        page2, _ = await repo.list_paginated(limit=2, offset=2)
        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].id != page2[0].id


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
class TestTransitionStatus:
    async def _seed(self, sqlite_session, status: str = "draft") -> int:
        repo = ContentOpportunityRepository(sqlite_session)
        row = await repo.create(signal_id=1, status=status)
        await sqlite_session.commit()
        return row.id

    async def test_draft_to_approved_ok(self, sqlite_session):
        co_id = await self._seed(sqlite_session, "draft")
        repo = ContentOpportunityRepository(sqlite_session)
        row = await repo.transition_status(co_id, "approved")
        assert row.status == "approved"

    async def test_approved_to_published_ok(self, sqlite_session):
        co_id = await self._seed(sqlite_session, "approved")
        repo = ContentOpportunityRepository(sqlite_session)
        row = await repo.transition_status(co_id, "published")
        assert row.status == "published"

    async def test_draft_to_published_rejected(self, sqlite_session):
        """Cannot skip the approved state — draft → published must fail."""
        co_id = await self._seed(sqlite_session, "draft")
        repo = ContentOpportunityRepository(sqlite_session)
        with pytest.raises(IllegalStatusTransition):
            await repo.transition_status(co_id, "published")

    async def test_published_to_draft_rejected(self, sqlite_session):
        co_id = await self._seed(sqlite_session, "published")
        repo = ContentOpportunityRepository(sqlite_session)
        with pytest.raises(IllegalStatusTransition):
            await repo.transition_status(co_id, "draft")

    async def test_rejected_is_terminal(self, sqlite_session):
        co_id = await self._seed(sqlite_session, "rejected")
        repo = ContentOpportunityRepository(sqlite_session)
        with pytest.raises(IllegalStatusTransition):
            await repo.transition_status(co_id, "approved")

    async def test_any_to_rejected_works(self, sqlite_session):
        """Both draft and approved can be rejected."""
        repo = ContentOpportunityRepository(sqlite_session)
        draft_row = await repo.create(signal_id=1, status="draft")
        approved_row = await repo.create(signal_id=2, status="approved")
        await sqlite_session.commit()

        r1 = await repo.transition_status(draft_row.id, "rejected")
        r2 = await repo.transition_status(approved_row.id, "rejected")
        assert r1.status == "rejected"
        assert r2.status == "rejected"

    async def test_missing_id_raises_not_found(self, sqlite_session):
        repo = ContentOpportunityRepository(sqlite_session)
        with pytest.raises(ContentOpportunityRepository.NotFound):
            await repo.transition_status(999, "approved")
