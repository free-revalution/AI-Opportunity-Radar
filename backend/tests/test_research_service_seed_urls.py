"""Phase 34 PR-34-D — ResearchService._seed_urls_from_opp bug 回归。

原 bug:_seed_urls_from_opp 误用 ``Opportunity.id``(SQLAlchemy
InstrumentedAttribute) 传给 ``list_raw_items_for_opportunity(opportunity_id: int)``,
结果总是返回空 list — production /run 的 research 阶段在没 seed_urls 的
路径下用空 URL 集合跑(走 _search_query 搜索兜底,得到的相关材料是
LLM 幻觉而非真实 raw_items 关联的链接)。

修: 改成 ``opp.id``。每个 pipeline-level test 都 stub 了
ResearchService.run_once,所以这个 bug 从来没被 CI 抓到。
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest


pytestmark = pytest.mark.asyncio


async def _seed_opp_with_raw_items(session: Any) -> int:
    """Seed: 1 Source + 3 RawItem + 1 Opportunity + 3 OpportunitySource link。
    返回 Opportunity.id。"""
    from app.models import (
        Opportunity,
        OpportunitySource,
        RawItem,
        Source,
    )

    src = Source(
        name="github",
        type="api",
        url="https://api.github.com",
        enabled=True,
        crawl_interval=3600,
    )
    session.add(src)
    await session.flush()

    items = []
    for i, (eid, url) in enumerate(
        [
            ("A", "https://example.com/a"),
            ("B", "https://example.com/b"),
            ("C", "https://example.com/c"),
        ]
    ):
        ri = RawItem(
            source_id=src.id,
            external_id=eid,
            title=f"Item {eid}",
            url=url,
            content="c" * 100,
            author=None,
            published_at=None,
            metadata_json={},
            content_hash=hashlib.sha256(eid.encode()).hexdigest(),
        )
        items.append(ri)
    session.add_all(items)
    await session.flush()

    opp = Opportunity(
        title="research seed opp",
        slug="research-seed-opp",
        status="screened",
        category="AI",
    )
    session.add(opp)
    await session.flush()

    session.add_all(
        [
            OpportunitySource(opportunity_id=opp.id, raw_item_id=ri.id)
            for ri in items
        ]
    )
    await session.commit()
    return opp.id


async def test_seed_urls_from_opp_returns_opp_urls(sqlite_session: Any) -> None:
    """PR-34-D 回归: _seed_urls_from_opp 必须返回**仅**该 opp 关联 raw_items 的 URL。

    修 bug 前: ``Opportunity.id`` 在 WHERE 被 SQLAlchemy 当成列,
    产生 ``... JOIN opportunity_sources, opportunities WHERE opp_id = opportunities.id``
    cross join — 实际返回**所有** raw_items,而不是 opp 关联的。
    我们的 SQL 注入两个 opp 区分。
    """
    from app.services.research.service import ResearchService
    from app.models import (
        Opportunity,
        OpportunitySource,
        RawItem,
        Source,
    )
    import hashlib as _hl
    from sqlalchemy import select as _sel

    # Seed 2 个 Source + 2 个 opp + 各自关联不同 raw_items
    src_a = Source(
        name="src-a", type="api", url="https://a.com", enabled=True, crawl_interval=3600
    )
    src_b = Source(
        name="src-b", type="rss", url="https://b.com", enabled=True, crawl_interval=3600
    )
    sqlite_session.add_all([src_a, src_b])
    await sqlite_session.flush()

    ri_a1 = RawItem(
        source_id=src_a.id, external_id="A1", title="a1", url="https://a.com/1",
        content="x", metadata_json={}, content_hash=_hl.sha256(b"A1").hexdigest(),
    )
    ri_a2 = RawItem(
        source_id=src_a.id, external_id="A2", title="a2", url="https://a.com/2",
        content="x", metadata_json={}, content_hash=_hl.sha256(b"A2").hexdigest(),
    )
    ri_b1 = RawItem(
        source_id=src_b.id, external_id="B1", title="b1", url="https://b.com/1",
        content="y", metadata_json={}, content_hash=_hl.sha256(b"B1").hexdigest(),
    )
    sqlite_session.add_all([ri_a1, ri_a2, ri_b1])
    await sqlite_session.flush()

    opp_target = Opportunity(
        title="target", slug="target-opp", status="screened"
    )
    opp_other = Opportunity(
        title="other", slug="other-opp", status="screened"
    )
    sqlite_session.add_all([opp_target, opp_other])
    await sqlite_session.flush()

    # target opp 只链 a1, a2
    sqlite_session.add_all(
        [
            OpportunitySource(opportunity_id=opp_target.id, raw_item_id=ri_a1.id),
            OpportunitySource(opportunity_id=opp_target.id, raw_item_id=ri_a2.id),
            OpportunitySource(opportunity_id=opp_other.id, raw_item_id=ri_b1.id),
        ]
    )
    await sqlite_session.commit()

    # Re-fetch target opp
    opp_target = (
        await sqlite_session.execute(
            _sel(Opportunity).where(Opportunity.id == opp_target.id)
        )
    ).scalar_one()

    svc = ResearchService(sqlite_session, max_urls=10)
    urls = await svc._seed_urls_from_opp(opp_target)

    # 关键: 必须只含 a1/a2 的 url,不能含 b1(另一个 opp 的 raw_item)
    assert "https://a.com/1" in urls
    assert "https://a.com/2" in urls
    assert "https://b.com/1" not in urls, (
        f"PR-34-D regression: _seed_urls_from_opp 漏了其它 opp 的 raw_item URL — "
        f"likely cross-join bug. Got: {urls}"
    )
    # 数量必须 == 2(不是 3 cross-join 全部)
    assert len(urls) == 2, (
        f"expected exactly 2 URLs (opp_target's linked raw_items), got {len(urls)}: {urls}"
    )


async def test_seed_urls_from_opp_respects_max_urls(sqlite_session: Any) -> None:
    """PR-34-D 回归: max_urls 限制生效 — 只取前 N 个。"""
    from app.services.research.service import ResearchService
    from app.models import Opportunity
    from sqlalchemy import select as _sel

    opp_id = await _seed_opp_with_raw_items(sqlite_session)
    opp = (
        await sqlite_session.execute(
            _sel(Opportunity).where(Opportunity.id == opp_id)
        )
    ).scalar_one()

    svc = ResearchService(sqlite_session, max_urls=2)
    urls = await svc._seed_urls_from_opp(opp)
    assert len(urls) == 2


async def test_seed_urls_from_opp_filters_items_without_url(
    sqlite_session: Any,
) -> None:
    """PR-34-D: 过滤掉 url=None 的 raw_item。"""
    import hashlib

    from app.models import (
        Opportunity,
        OpportunitySource,
        RawItem,
        Source,
    )
    from app.services.research.service import ResearchService
    from sqlalchemy import select as _sel

    src = Source(
        name="rss",
        type="rss",
        url="https://rss.example.com",
        enabled=True,
        crawl_interval=3600,
    )
    sqlite_session.add(src)
    await sqlite_session.flush()

    ri_with = RawItem(
        source_id=src.id,
        external_id="X",
        title="with url",
        url="https://example.com/x",
        content="x",
        metadata_json={},
        content_hash=hashlib.sha256(b"X").hexdigest(),
    )
    ri_without = RawItem(
        source_id=src.id,
        external_id="Y",
        title="without url",
        url="",  # 空 url
        content="y",
        metadata_json={},
        content_hash=hashlib.sha256(b"Y").hexdigest(),
    )
    sqlite_session.add_all([ri_with, ri_without])
    await sqlite_session.flush()

    opp = Opportunity(
        title="filter opp",
        slug="filter-opp",
        status="screened",
    )
    sqlite_session.add(opp)
    await sqlite_session.flush()
    sqlite_session.add_all(
        [
            OpportunitySource(opportunity_id=opp.id, raw_item_id=ri_with.id),
            OpportunitySource(opportunity_id=opp.id, raw_item_id=ri_without.id),
        ]
    )
    await sqlite_session.commit()

    opp = (
        await sqlite_session.execute(
            _sel(Opportunity).where(Opportunity.id == opp.id)
        )
    ).scalar_one()
    svc = ResearchService(sqlite_session, max_urls=10)
    urls = await svc._seed_urls_from_opp(opp)
    assert urls == ["https://example.com/x"]
