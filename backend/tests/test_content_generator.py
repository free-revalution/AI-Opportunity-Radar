"""Tests for the v2.0 content_generator module.

These tests use `FakeContentLLM` (defined below) — a hand-rolled stub
that returns deterministic, schema-conformant payloads for each
content generator's response_schema. We deliberately do NOT use the
production `MockLLMProvider` because that one's contract is for
screening/scoring and doesn't match our schemas.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.content_generator import (
    ContentGeneratorService,
    get_registry,
)
from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
)
from app.services.content_generator.daily_report import DailyReportGenerator
from app.services.content_generator.xiaohongshu_post import (
    XiaohongshuPostGenerator,
)
from app.services.content_generator.xianyu_product import XianyuProductGenerator
from app.services.llm.provider import LLMProvider


# ---------------------------------------------------------------------------
# LLM stub — returns payloads matching the active response_schema.
# ---------------------------------------------------------------------------
class FakeContentLLM(LLMProvider):
    """Schema-aware LLM stub.

    - For the enrichment schema (used by the service) → returns the
      canonical enrichment payload.
    - For the Xianyu JSON schema → returns the canonical Xianyu
      product listing.
    - For Markdown generators (no schema) → returns a hand-written
      Markdown block.
    """

    name = "fake-content-llm"

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any] | str:
        # Enrichment call → JSON dict with the 6 commercial fields.
        if (
            response_schema is not None
            and "target_customer" in response_schema.get("properties", {})
        ):
            return {
                "target_customer": "海外 SaaS 创始人,月营收 10k-50k USD",
                "market_size": "100M-500M USD",
                "mvp_days": 21,
                "difficulty": "medium",
                "monetization_model": "SaaS 订阅 49 USD/月",
                "china_gap": "中国 SaaS 市场更分散,小程序 + 微信支付是关键渠道",
            }

        # Xianyu product call → JSON listing.
        if (
            response_schema is not None
            and "selling_points" in response_schema.get("properties", {})
        ):
            return {
                "title": "2026 海外 AI 创业机会 100 个 完整报告",
                "description": (
                    "## 这是 100 个正在海外跑出 MRR 的 AI 项目\n\n"
                    "* 每个项目含:市场分析 + MVP 拆解 + 定价建议\n"
                    "* 全部基于近 30 天真实 GitHub / Reddit 信号\n"
                    "* 中英文双语,可直接发布"
                ),
                "selling_points": [
                    "100 个真实海外项目",
                    "每个含 MVP 拆解",
                    "中英双语",
                    "持续更新",
                ],
                "price": 49,
                "category": "虚拟商品",
                "delivery_method": "网盘链接",
            }

        # Markdown generators → return markdown text.
        return (
            "# 今日AI商业机会\n\n"
            "## 机会名称\nAI 客服质量分析工具\n\n"
            "## 来源\nReddit: 多个客服团队反馈\n\n"
            "## 用户痛点\n客服质检依赖人工抽样,覆盖率 < 5%\n\n"
            "## 市场分析\n海外客服 SaaS 市场 5B USD,中国本土化空间大\n\n"
            "## 商业机会\n评分 87/100,SaaS 订阅模式\n\n"
            "## 推荐行动\n14 天可完成 MVP,首发钉钉/飞书生态"
        )


# ---------------------------------------------------------------------------
# Helpers — fake Opportunity + Report
# ---------------------------------------------------------------------------
def _make_opportunity(**overrides: Any) -> Any:
    base: dict[str, Any] = dict(
        id=1,
        title="AI 客服质量分析工具",
        summary="通过 LLM 自动分析客服对话质量",
        target_user="中小企业客服团队",
        category="AI SaaS",
        market="B2B",
        target_customer=None,
        market_size=None,
        mvp_days=0,
        difficulty=None,
        monetization_model=None,
        china_gap=None,
        content_status="new",
        commercial_status="unqualified",
        total_score=87.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_report(**overrides: Any) -> Any:
    base: dict[str, Any] = dict(
        executive_summary="海外 SaaS 团队用 LLM 自动化客服质检,已盈利",
        market_analysis="市场分散,中位客单价 5k USD/年",
        competition_analysis="头部玩家 5 家,均无中国本地化版本",
        china_analysis="国内客服市场更大,合规壁垒适中",
        monetization_analysis="按席位订阅",
        mvp_analysis="用 Whisper + GPT-4o 即可 MVP",
        risk_analysis="数据合规",
        recommendation="qualified",
        confidence=0.8,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------
def test_all_generators_are_registered_at_import() -> None:
    """Importing the package must auto-register every generator."""
    reg = get_registry()
    names = reg.names()
    assert "daily_report" in names
    assert "xianyu_product" in names
    assert "xiaohongshu_post" in names


def test_each_generator_has_distinct_channel() -> None:
    """Different channels so the notification feed can route correctly."""
    reg = get_registry()
    channels = {reg.get(n).channel for n in reg.names()}
    assert channels == {"feishu", "xianyu", "xiaohongshu"}


# ---------------------------------------------------------------------------
# DailyReportGenerator
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_daily_report_returns_markdown_with_required_sections() -> None:
    gen = DailyReportGenerator()
    opp = _make_opportunity()
    content = await gen.generate(opportunity=opp, report=_make_report(), llm=FakeContentLLM())
    assert isinstance(content, GeneratedContent)
    assert content.format == "markdown"
    assert content.channel == "feishu"
    assert content.opportunity_id == opp.id
    assert "今日AI商业机会" in content.content
    # Metadata echoes the score + mvp_days.
    assert content.metadata["score"] == pytest.approx(87.0)


@pytest.mark.asyncio
async def test_daily_report_uses_report_fields_in_user_prompt() -> None:
    gen = DailyReportGenerator()
    user = gen.user_prompt(opportunity=_make_opportunity(), report=_make_report())
    assert "executive_summary" in user.lower() or "executive" in user.lower()
    assert "market_analysis" in user.lower() or "市场" in user


# ---------------------------------------------------------------------------
# XianyuProductGenerator
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_xianyu_product_returns_valid_json_payload() -> None:
    gen = XianyuProductGenerator()
    opp = _make_opportunity()
    content = await gen.generate(opportunity=opp, report=_make_report(), llm=FakeContentLLM())
    assert content.format == "json"
    assert content.channel == "xianyu"
    payload = content.content
    assert isinstance(payload, dict)
    assert payload["price"] == 49
    assert len(payload["selling_points"]) >= 3
    assert content.metadata["price_cny"] == 49
    assert content.metadata["delivery_method"] == "网盘链接"


@pytest.mark.asyncio
async def test_xianyu_product_raises_when_model_skips_required_field() -> None:
    class _Incomplete(LLMProvider):
        name = "incomplete"

        async def complete_json(self, **_: Any) -> dict[str, Any]:
            # Missing 'selling_points' — schema-required.
            return {
                "title": "x",
                "description": "y",
                "price": 49,
                "category": "v",
                "delivery_method": "d",
            }

    gen = XianyuProductGenerator()
    with pytest.raises(ValueError, match="selling_points"):
        await gen.generate(
            opportunity=_make_opportunity(),
            report=_make_report(),
            llm=_Incomplete(),
        )


# ---------------------------------------------------------------------------
# XiaohongshuPostGenerator
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_xiaohongshu_post_appends_hashtags_and_cta_placeholder() -> None:
    gen = XiaohongshuPostGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(category="AI SaaS", market="B2B"),
        report=_make_report(),
        llm=FakeContentLLM(),
    )
    assert content.format == "markdown"
    assert content.channel == "xiaohongshu"
    body = content.content
    assert "#海外项目" in body
    assert "{{CTA_URL}}" in body
    assert content.metadata["cta_placeholder"] == "{{CTA_URL}}"
    # Hashtags must be a list with at least the 3 defaults.
    assert isinstance(content.metadata["hashtags"], list)
    assert len(content.metadata["hashtags"]) >= 3


@pytest.mark.asyncio
async def test_xiaohongshu_post_handles_dict_shaped_llm_output() -> None:
    """If the provider wraps the markdown in a dict, we still extract."""

    class _DictWrapper(LLMProvider):
        name = "wrapper"

        async def complete_json(self, **_: Any) -> dict[str, Any]:
            return {
                "text": (
                    "# 国外一个 AI 项目月入 5 万美元\n\n"
                    "中国还没有人做。"
                )
            }

    gen = XiaohongshuPostGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=_DictWrapper(),
    )
    assert "国外一个 AI 项目月入 5 万美元" in content.content


# ---------------------------------------------------------------------------
# ContentGeneratorService — orchestrator
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_service_enriches_opportunity_then_runs_all_generators(
    sqlite_session: Any,
) -> None:
    from app.models import Opportunity, Notification

    opp = Opportunity(
        title="AI 客服质量分析工具",
        slug="ai-customer-service-quality",
        summary="通过 LLM 分析客服对话质量",
        target_user="中小企业客服团队",
        total_score=87.0,
        commercial_status="unqualified",
        content_status="new",
    )
    sqlite_session.add(opp)
    await sqlite_session.flush()

    svc = ContentGeneratorService(session=sqlite_session, llm=FakeContentLLM())
    produced = await svc.run_for_opportunity(opp, report=None, enrich=True)

    # All three generators ran.
    generators = {p.generator for p in produced}
    assert generators == {"daily_report", "xianyu_product", "xiaohongshu_post"}

    # Enrichment fields were applied.
    assert opp.target_customer is not None
    assert opp.mvp_days == 21
    assert opp.difficulty == "medium"
    assert opp.commercial_status == "qualified"
    assert opp.content_status == "generated"

    # Each generator's output was mirrored to a Notification row.
    from sqlalchemy import select
    notif_rows = (
        await sqlite_session.execute(select(Notification))
    ).scalars().all()
    assert {n.channel for n in notif_rows} == {"feishu", "xianyu", "xiaohongshu"}


@pytest.mark.asyncio
async def test_service_skips_enrichment_when_already_enriched(
    sqlite_session: Any,
) -> None:
    from app.models import Opportunity

    opp = Opportunity(
        title="already enriched",
        slug="already-enriched",
        total_score=80.0,
        target_customer="pre-filled persona",
        mvp_days=10,
        commercial_status="qualified",
        content_status="new",
    )
    sqlite_session.add(opp)
    await sqlite_session.flush()

    svc = ContentGeneratorService(session=sqlite_session, llm=FakeContentLLM())
    await svc.run_for_opportunity(opp, report=None, enrich=True)
    # Existing target_customer must not have been overwritten by the
    # stub's canonical enrichment payload.
    assert opp.target_customer == "pre-filled persona"


@pytest.mark.asyncio
async def test_service_continues_when_one_generator_fails(sqlite_session: Any) -> None:
    """One generator's failure must not stop the others."""

    class _FlakyGen(ContentGenerator):
        name = "flaky"
        channel = "feishu"
        format = "markdown"
        description = "always blows up"

        async def generate(self, **_: Any) -> GeneratedContent:
            raise RuntimeError("synthetic boom")

    get_registry().register(_FlakyGen())
    try:
        from app.models import Opportunity

        opp = Opportunity(
            title="x",
            slug="x",
            total_score=80.0,
            commercial_status="qualified",
            content_status="new",
        )
        sqlite_session.add(opp)
        await sqlite_session.flush()

        svc = ContentGeneratorService(session=sqlite_session, llm=FakeContentLLM())
        produced = await svc.run_for_opportunity(opp, report=None)
        # Three real generators still produced output.
        real = [p for p in produced if p.generator != "flaky"]
        assert len(real) == 3
    finally:
        # Don't pollute other tests' registries.
        get_registry()._generators.pop("flaky", None)


@pytest.mark.asyncio
async def test_service_run_for_top_picks_top_n(sqlite_session: Any) -> None:
    from app.models import Opportunity

    rows = [
        Opportunity(
            title=f"opp-{i}",
            slug=f"opp-{i}",
            total_score=80.0 + i,
            commercial_status="qualified",
            content_status="new",
        )
        for i in range(5)
    ]
    sqlite_session.add_all(rows)
    await sqlite_session.flush()

    svc = ContentGeneratorService(session=sqlite_session, llm=FakeContentLLM())
    result = await svc.run_for_top_opportunities(limit=2, only_qualified=True)

    assert len(result.enriched_opportunity_ids) == 2
    # Top 2 by total_score are 84 and 83.
    top_titles = sorted(
        [r.title for r in rows], key=lambda t: int(t.split("-")[1]), reverse=True
    )[:2]
    enriched_titles = [
        (await sqlite_session.get(Opportunity, i)).title
        for i in result.enriched_opportunity_ids
    ]
    assert sorted(enriched_titles) == sorted(top_titles)