"""Phase 30 — DetailDocxService tests.

Covers:

  * ``build_markdown`` — three states (no Phase 30 fields / full
    fields / research report) + slug truncation + sources list
  * ``write_to_drive`` — fresh write + Redis SETNX cache hit
  * ``render_chat_card_reply`` — interactive card JSON shape
  * Markdown → docx (via fake drive) — folder walk + title
  * Idempotency — same opportunity, same day → same doc_id,
    ``from_cache=True``
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

import pytest
import pytest_asyncio

from app.models import (
    Opportunity,
    OpportunitySource,
    RawItem,
    ResearchReport,
    Source,
)
from app.services.feishu.detail_docx import (
    DetailDocxResult,
    DetailDocxService,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeDrive:
    """Stand-in for FeishuDriveClient — captures the docx payload."""

    def __init__(self, *, settings: Any) -> None:
        self.settings = settings
        self._folders: dict[tuple[str, str], str] = {}
        self._counter = 0
        self.created: list[dict[str, Any]] = []

    @property
    def is_configured(self) -> bool:
        return bool(self.folder_token)

    @property
    def folder_token(self) -> str:
        return self.settings.feishu_drive_root_folder_token

    async def ensure_folder_path(
        self, *, parent_token: str, path: list[str]
    ) -> str:
        cur = parent_token
        for name in path:
            key = (cur, name)
            if key not in self._folders:
                self._counter += 1
                self._folders[key] = f"fld_{self._counter}_{name[:6]}"
            cur = self._folders[key]
        return cur

    async def create_docx_from_markdown(
        self,
        *,
        title: str,
        markdown: str,
        folder_token: Optional[str] = None,
    ) -> dict[str, Any]:
        self._counter += 1
        doc_id = f"doc_{self._counter:03d}"
        result = {
            "doc_id": doc_id,
            "url": f"https://feishu.cn/docx/{doc_id}",
            "folder_token": folder_token or self.folder_token,
            "title": title,
            "markdown_chars": len(markdown),
        }
        self.created.append(result)
        return result


class FakeRedis:
    """Tiny SETNX/GET fake — same surface as the Redis client wrapper."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self._kv.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: Optional[int] = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _settings_with_root() -> Any:
    from app.config import get_settings

    s = get_settings()
    s.feishu_drive_root_folder_token = "root_tok"
    s.radar_detail_doc_idempotency_ttl = 86_400
    return s


@pytest_asyncio.fixture
async def seeded_opportunity(sqlite_session: Any) -> int:
    """Seed Source + RawItem + Opportunity + 1 OpportunitySource row."""
    src = Source(
        id=1, name="TechCrunch", type="rss", url="https://tc.example/feed"
    )
    sqlite_session.add(src)
    await sqlite_session.flush()

    raw = RawItem(
        id=1,
        source_id=1,
        external_id="tc-123",
        url="https://tc.example/posts/123",
        title="AI tutor takes off",
        content="...",
        author="Jane",
        published_at=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
        content_hash="hash123",
        metadata_json={},
    )
    sqlite_session.add(raw)
    await sqlite_session.flush()

    opp = Opportunity(
        id=1,
        title="AI Tutor for Adult Learners",
        slug="ai-tutor-for-adult-learners",
        summary="Long summary...",
        category="education",
        total_score=88.5,
        source_count=3,
        trend_score=80.0,
        demand_score=75.0,
        monetization_score=90.0,
        competition_gap_score=70.0,
        china_gap_score=85.0,
        execution_score=65.0,
        problem="Adults struggle to keep up with AI skills.",
        potential_business="Subscription-based AI tutor for SMB teams.",
        keywords_json=["AI tutor", "adult learning", "upskilling"],
    )
    sqlite_session.add(opp)
    await sqlite_session.flush()

    link = OpportunitySource(opportunity_id=1, raw_item_id=1, relevance=0.9)
    sqlite_session.add(link)
    await sqlite_session.commit()
    return opp.id


@pytest_asyncio.fixture
async def seeded_minimal_opportunity(sqlite_session: Any) -> int:
    """Opportunity WITHOUT Phase 30 fields — for skip-section tests."""
    src = Source(
        id=2, name="HackerNews", type="rss", url="https://hn.example/feed"
    )
    sqlite_session.add(src)
    await sqlite_session.flush()
    opp = Opportunity(
        id=2,
        title="Legacy Opp",
        slug="legacy-opp",
        summary="Old summary",
        category="misc",
        total_score=55.0,
        source_count=1,
    )
    sqlite_session.add(opp)
    await sqlite_session.flush()
    raw = RawItem(
        id=2,
        source_id=2,
        external_id="hn-1",
        url="https://hn.example/item/1",
        title="Story",
        content_hash="hh",
        metadata_json={},
    )
    sqlite_session.add(raw)
    await sqlite_session.flush()
    sqlite_session.add(
        OpportunitySource(opportunity_id=2, raw_item_id=2, relevance=0.5)
    )
    await sqlite_session.commit()
    return opp.id


@pytest_asyncio.fixture
async def seeded_with_research(seeded_opportunity: int, sqlite_session: Any) -> int:
    """Add a full 7-section ResearchReport on top of the seeded opp."""
    report = ResearchReport(
        opportunity_id=seeded_opportunity,
        executive_summary="Market is ripe.",
        market_analysis="USD 5B TAM.",
        competition_analysis="Fragmented, 3 main players.",
        china_analysis="Under-served.",
        monetization_analysis="Subscription + B2B license.",
        mvp_analysis="2 months with 1 engineer.",
        risk_analysis="Regulatory risk in EU.",
        recommendation="go",
        confidence=0.78,
        sources_json={},
    )
    sqlite_session.add(report)
    await sqlite_session.commit()
    return seeded_opportunity


# ---------------------------------------------------------------------------
# build_markdown
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_build_markdown_full_state(
    sqlite_session: Any, seeded_with_research: int
) -> None:
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    md = await svc.build_markdown(opportunity_id=seeded_with_research)

    # — Header
    assert "# AI Tutor for Adult Learners" in md
    assert "**Score:** 88.5" in md
    assert "**Category:** education" in md
    assert "**Sources:** 3" in md
    # — Phase 30 sections
    assert "## 摘要" in md
    assert "Adults struggle to keep up with AI skills." in md
    assert "## 潜在商业" in md
    assert "Subscription-based AI tutor for SMB teams." in md
    assert "## 关键词" in md
    assert "- AI tutor" in md
    assert "- adult learning" in md
    assert "- upskilling" in md
    # — Research report sections
    assert "## 研究报告" in md
    assert "### 执行摘要" in md
    assert "Market is ripe." in md
    assert "### 市场分析" in md
    assert "USD 5B TAM." in md
    assert "### 竞争分析" in md
    assert "### 中国市场分析" in md
    assert "### 盈利模式" in md
    assert "### MVP 分析" in md
    assert "### 风险评估" in md
    assert "**推荐:** `go`" in md
    assert "**置信度:** 78%" in md
    # — Sources
    assert "## 信息源" in md
    assert "[AI tutor takes off](https://tc.example/posts/123)" in md
    assert "TechCrunch" in md


@pytest.mark.asyncio
async def test_build_markdown_skips_empty_sections(
    sqlite_session: Any, seeded_minimal_opportunity: int
) -> None:
    """Pre-Phase 30 opps have NULL problem / business / keywords.
    build_markdown should skip those sections, not print 'None'."""
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    md = await svc.build_markdown(opportunity_id=seeded_minimal_opportunity)
    assert "## 摘要" not in md
    assert "## 潜在商业" not in md
    assert "## 关键词" not in md
    assert "## 研究报告" not in md  # — no ResearchReport seeded


@pytest.mark.asyncio
async def test_build_markdown_research_with_partial_fields(
    sqlite_session: Any, seeded_minimal_opportunity: int
) -> None:
    """A ResearchReport with only 1 filled field should still render
    the section header (other sub-sections are skipped, not 'None')."""
    partial = ResearchReport(
        opportunity_id=seeded_minimal_opportunity,
        executive_summary="Brief.",
        sources_json={},
    )
    sqlite_session.add(partial)
    await sqlite_session.commit()

    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    md = await svc.build_markdown(opportunity_id=seeded_minimal_opportunity)
    assert "## 研究报告" in md
    assert "### 执行摘要" in md
    assert "Brief." in md
    assert "### 市场分析" not in md  # — NULL → skipped


@pytest.mark.asyncio
async def test_build_markdown_sources_empty_shows_placeholder(
    sqlite_session: Any,
) -> None:
    """Opportunity with no source links → 信息源 lists '暂无'."""
    opp = Opportunity(
        id=99,
        title="Source-less Opp",
        slug="source-less",
        total_score=50.0,
        source_count=0,
    )
    sqlite_session.add(opp)
    await sqlite_session.commit()

    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    md = await svc.build_markdown(opportunity_id=99)
    assert "## 信息源" in md
    assert "（暂无）" in md


@pytest.mark.asyncio
async def test_build_markdown_opportunity_not_found(
    sqlite_session: Any,
) -> None:
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    import pytest as _pytest

    from app.services.feishu.content_client import FeishuContentError

    with _pytest.raises(FeishuContentError, match="not found"):
        await svc.build_markdown(opportunity_id=99999)


# ---------------------------------------------------------------------------
# write_to_drive
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_write_to_drive_fresh(
    sqlite_session: Any, seeded_opportunity: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.feishu.detail_docx.get_redis", _async_return(FakeRedis())
    )
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)

    result = await svc.write_to_drive(opportunity_id=seeded_opportunity)
    assert isinstance(result, DetailDocxResult)
    assert result.from_cache is False
    assert result.opportunity_id == seeded_opportunity
    assert result.doc_id.startswith("doc_")
    assert result.doc_url.startswith("https://feishu.cn/docx/doc_")
    # — Folder path: 📁 每日报告/2026-08-31/ created
    assert ("root_tok", "📁 每日报告") in drive._folders
    day_token = drive._folders[("root_tok", "📁 每日报告")]
    today = date.today().strftime("%Y-%m-%d")
    assert (day_token, today) in drive._folders
    # — Docx was created
    assert len(drive.created) == 1
    assert drive.created[0]["title"].endswith("详情报告")


@pytest.mark.asyncio
async def test_write_to_drive_idempotent_same_day(
    sqlite_session: Any, seeded_opportunity: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setattr(
        "app.services.feishu.detail_docx.get_redis", _async_return(fake_redis)
    )
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)

    first = await svc.write_to_drive(opportunity_id=seeded_opportunity)
    second = await svc.write_to_drive(opportunity_id=seeded_opportunity)
    assert second.from_cache is True
    assert second.doc_id == first.doc_id
    assert second.doc_url == first.doc_url
    # — Drive only saw ONE create call.
    assert len(drive.created) == 1


@pytest.mark.asyncio
async def test_write_to_drive_force_rewrites(
    sqlite_session: Any, seeded_opportunity: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setattr(
        "app.services.feishu.detail_docx.get_redis", _async_return(fake_redis)
    )
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)

    await svc.write_to_drive(opportunity_id=seeded_opportunity)
    forced = await svc.write_to_drive(
        opportunity_id=seeded_opportunity, force=True
    )
    assert forced.from_cache is False
    assert len(drive.created) == 2


@pytest.mark.asyncio
async def test_write_to_drive_no_drive_raises(
    sqlite_session: Any, seeded_opportunity: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.feishu.detail_docx.get_redis", _async_return(FakeRedis())
    )
    settings = _settings_with_root()
    settings.feishu_drive_root_folder_token = ""
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    from app.services.feishu.content_client import FeishuContentError

    with pytest.raises(FeishuContentError, match="not configured"):
        await svc.write_to_drive(opportunity_id=seeded_opportunity)


# ---------------------------------------------------------------------------
# render_chat_card_reply
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_render_chat_card_reply_fresh(
    sqlite_session: Any, seeded_opportunity: int
) -> None:
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    result = await svc.write_to_drive(opportunity_id=seeded_opportunity)
    # — Just check shape (write_to_drive needs Redis for full coverage,
    # this seat of the test bypasses that with a hand-built result).
    card = svc.render_chat_card_reply(
        DetailDocxResult(
            opportunity_id=result.opportunity_id,
            title=result.title,
            slug=result.slug,
            doc_id=result.doc_id,
            doc_url=result.doc_url,
            folder_token=result.folder_token,
            from_cache=False,
        )
    )
    assert card["header"]["template"] == "green"
    assert card["header"]["title"]["content"] == "✅ 详情已生成"
    body_md = card["elements"][0]["text"]["content"]
    assert "AI Tutor for Adult Learners" in body_md
    assert "已生成详情报告" in body_md
    # — Action button
    action = card["elements"][1]
    assert action["tag"] == "action"
    button = action["actions"][0]
    assert button["tag"] == "button"
    assert button["text"]["content"] == "查看详情"
    assert button["url"].startswith("https://feishu.cn/docx/")


@pytest.mark.asyncio
async def test_render_chat_card_reply_from_cache(
    sqlite_session: Any, seeded_opportunity: int
) -> None:
    settings = _settings_with_root()
    drive = FakeDrive(settings=settings)
    svc = DetailDocxService(session=sqlite_session, drive=drive, settings=settings)
    card = svc.render_chat_card_reply(
        DetailDocxResult(
            opportunity_id=1,
            title="X",
            slug="x",
            doc_id="doc_cached",
            doc_url="https://feishu.cn/docx/doc_cached",
            folder_token="",
            from_cache=True,
        )
    )
    assert card["header"]["template"] == "blue"
    body_md = card["elements"][0]["text"]["content"]
    assert "当天已生成过" in body_md
    assert "缓存" in card["elements"][2]["elements"][0]["content"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
def test_detail_docx_result_to_metadata() -> None:
    r = DetailDocxResult(
        opportunity_id=42,
        title="Title",
        slug="slug",
        doc_id="doc_001",
        doc_url="https://feishu.cn/docx/doc_001",
        folder_token="fld_001",
        from_cache=False,
    )
    md = r.to_metadata()
    assert md["opportunity_id"] == 42
    assert md["doc_id"] == "doc_001"
    assert md["from_cache"] is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _async_return(value: Any):
    """Build an async callable returning ``value`` for monkeypatching.

    detail_docx calls ``await get_redis()`` — for tests we want the
    fake to resolve immediately.
    """

    async def _inner(*args: Any, **kwargs: Any) -> Any:
        return value

    return _inner
