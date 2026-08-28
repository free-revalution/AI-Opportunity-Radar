"""Tests for the v2.0 content_generator module.

These tests use `FakeContentLLM` (defined below) — a hand-rolled stub
that returns deterministic, schema-conformant payloads for each
content generator's response_schema. We deliberately do NOT use the
production `MockLLMProvider` because that one's contract is for
screening/scoring and doesn't match our schemas.

Phase 9 (DRY cleanup) added direct unit tests for the module-level
helpers in `app.services.content_generator.base` so refactors stay
locked-down at the helper layer, not just the per-generator layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.content_generator.base import (
    append_block_if_missing,
    base_metadata,
    ensure_section_placeholders,
    extract_text_from_llm,
    extract_title_from_body,
)

from app.services.content_generator import (
    ContentGeneratorService,
    get_registry,
)
from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
)
from app.services.content_generator.daily_report import DailyReportGenerator
from app.services.content_generator.wechat_article import (
    MIN_CHARS,
    REQUIRED_CTA_PLACEHOLDERS,
    WechatArticleGenerator,
)
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
    - For Phase 8 wechat (system_prompt mentions "公众号") → returns a
      long-form ~1700-char article with 4 H2 sections, image
      placeholders, and NO CTA — so `_enforce_cta` must append the
      three-piece block on top of the LLM output.
    """

    name = "fake-content-llm"

    def __init__(self) -> None:
        self.last_call: dict[str, Any] | None = None

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
        self.last_call = {
            "system": system,
            "user": user,
            "max_tokens": max_tokens,
            "response_schema": response_schema,
        }
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
        # Phase 8 — wechat generator: long-form article (~1700 chars).
        if "公众号" in system or "数字钩子" in system:
            return _WECHAT_LLM_FIXTURE
        return (
            "# 今日AI商业机会\n\n"
            "## 机会名称\nAI 客服质量分析工具\n\n"
            "## 来源\nReddit: 多个客服团队反馈\n\n"
            "## 用户痛点\n客服质检依赖人工抽样,覆盖率 < 5%\n\n"
            "## 市场分析\n海外客服 SaaS 市场 5B USD,中国本土化空间大\n\n"
            "## 商业机会\n评分 87/100,SaaS 订阅模式\n\n"
            "## 推荐行动\n14 天可完成 MVP,首发钉钉/飞书生态"
        )


# ~1700-char fixture (≥ MIN_CHARS=1500, < MAX_CHARS=3000) with 4 H2 sections
# and 3 image placeholders, no CTA — the generator must append the
# canonical CTA block. Padded to comfortably exceed MIN_CHARS so the
# short-content warning path isn't accidentally exercised.
_WECHAT_LLM_FIXTURE = (
    "# 我用 14 天复刻了一个月入 5 万美元的 AI 客服质检项目\n\n"
    "上个月刷 Reddit,看到一个叫 ScaleOps 的小团队,"
    "用 GPT-4o 帮 Shopify 商家自动质检客服对话,ARR 已经 18 万美元。"
    "国内还没有人做。我盯着这个数字看了十分钟,然后决定动手。\n\n"
    "## 国外项目是什么\n"
    "ScaleOps 把每通客服对话转成文字,扔进 GPT-4o,"
    "按 5 个维度(礼貌度 / 解决率 / 风险词 / 转化信号 / 客户情绪)"
    "自动打分。Shopify 商家以前靠人工抽样 5%, 现在覆盖率 100%, "
    "客单价 499 USD/月。** 仅 12 个付费客户就 6 万美元 MRR **。"
    "这家团队一共才 3 个人,两个工程师 + 一个客服运营,"
    "典型的'小团队吃大市场'。"
    "他们用 Whisper 转录音 + GPT-4o 评分 + Linear 推送日报,"
    "整套技术栈不到 2000 行代码。\n\n"
    "![配图-1]({{IMAGE_1}})\n\n"
    "## 为什么中国没人做\n"
    "国内客服市场更大 — 淘宝、抖音电商、跨境电商每家都在堆客服。"
    "但大家习惯了用钉钉 / 飞书的内置质检,或者干脆人工抽样。"
    "问题是这些工具都不接 AI,覆盖率始终 < 10%。"
    "微信生态(视频号小店 + 公众号小店 + 小程序)的客服 IM 数据"
    "完全是另一套标准,这就是切入点。\n\n"
    "![配图-2]({{IMAGE_2}})\n\n"
    "## 个人开发者怎么上手\n"
    "技术栈:Whisper 转录音 + GPT-4o 评分 + 飞书机器人推送日报。"
    "MVP 7-10 天。定价:基础版 199 元/月(对标国内客服 SaaS 的价格带),"
    "高级版 499 元/月(多店铺 + API 接入)。"
    "首发渠道:抖音电商服务商社群 + 知乎跨境电商话题。"
    "可以先用 14 天做 MVP,第一周跑通客服对话打分,"
    "第二周接入飞书 / 钉钉推送。前 20 个客户可以免费体验换口碑。"
    "对比海外 499 USD/月 ≈ 3600 元/月,国内 199 元/月定价已经比他们便宜 18 倍,"
    "但毛利还能保持 70% 以上。\n\n"
    "![配图-3]({{IMAGE_3}})\n\n"
    "## 风险与提醒\n"
    "数据合规:客服对话可能含 PII, 需要本地化部署 + 数据脱敏。"
    "另外,Shopify 的 SaaS 不能直接搬 — 国内没有 Shopify 这种"
    "单一入口的电商生态,商家散落在抖音、淘宝、拼多多、小程序,"
    "集成成本会比国外高 3-5 倍。"
    "所以建议不要做通用平台,先挑一个细分(比如抖音电商),把质检做成"
    "插件 / SaaS,等跑通 PMF 再扩品类。"
    "如果团队超过 2 人,记得先想好'谁是销售' — 国内 SaaS 不是产品经理"
    "做得出来就能卖得动的。我见过太多产品做完卖不出去的例子,"
    "最后打折卖给大厂或者干脆开源做培训。"
    "所以我的建议是:**先验证付费意愿,再做产品**。第一周去找 20 个潜在客户,"
    "问他们愿不愿意付 199 元/月,愿意的超过 5 个再动手写代码。\n\n"
    "![配图-4]({{IMAGE_4}})\n\n"
    "建议先从微信小程序客服切入,验证 PMF 再扩。"
    "如果你也在找海外 AI 项目的中国化机会,"
    "扫码加我聊聊,星球里每周更新 3 个类似的项目拆解。"
    "目前我已经在跟踪 12 个类似的项目,覆盖客服 / 营销 / 内容运营 / 销售"
    "四个方向,每个都跑通了至少 50 万 ARR 的海外原型。"
    "想做这类选题的,可以参考我之前拆过的几个案例(《Notion AI 替代品》/"
    "《Loom 视频摘要工具》《Calendly 智能调度》),都验证过国内市场的"
    "可移植性。我的判断是:**中国个人开发者最好的时代正在到来**,"
    "AI 让我们一个人能做的事比 5 年前一个 10 人团队还多。"
    "关键不是技术,而是选对方向 + 跑通付费闭环。"
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
    assert "wechat_article" in names


def test_each_generator_has_distinct_channel() -> None:
    """Different channels so the notification feed can route correctly."""
    reg = get_registry()
    channels = {reg.get(n).channel for n in reg.names()}
    assert channels == {"feishu", "xianyu", "xiaohongshu", "wechat_article"}


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
    assert generators == {"daily_report", "xianyu_product", "xiaohongshu_post", "wechat_article"}

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
    assert {n.channel for n in notif_rows} == {"feishu", "xianyu", "xiaohongshu", "wechat_article"}


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
        assert len(real) == 4
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


# ---------------------------------------------------------------------------
# WechatArticleGenerator — Phase 8 v2.0
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wechat_article_registers_with_correct_name_and_channel() -> None:
    reg = get_registry()
    assert "wechat_article" in reg.names()
    gen = reg.get("wechat_article")
    assert gen.name == "wechat_article"
    assert gen.channel == "wechat_article"


@pytest.mark.asyncio
async def test_wechat_article_returns_markdown_format() -> None:
    llm = FakeContentLLM()
    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=llm,
    )
    assert isinstance(content, GeneratedContent)
    assert content.format == "markdown"
    assert content.channel == "wechat_article"
    assert content.opportunity_id == 1
    assert content.generator == "wechat_article"


@pytest.mark.asyncio
async def test_wechat_article_min_chars_enforced_with_warning(monkeypatch) -> None:
    """Short fixture (< 1500 chars) → still produces content, but logs
    a `wechat_article_too_short` warning. No raise."""
    import app.services.content_generator.wechat_article as wx_mod

    captured: list[dict[str, Any]] = []
    real_warning = wx_mod.logger.warning

    def _capture(*args: Any, **kwargs: Any) -> Any:
        captured.append({"args": args, "kwargs": kwargs})
        return real_warning(*args, **kwargs)

    monkeypatch.setattr(wx_mod.logger, "warning", _capture)

    class _ShortLLM(LLMProvider):
        name = "short-wx"

        async def complete_json(self, **_: Any) -> str:
            return "# 标题只有十几个字\n\n一段很短的内容,字数不到 1500。"

    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=_ShortLLM(),
    )
    assert len(content.content) < MIN_CHARS
    # — Logged at least once with the right event name.
    assert any(
        (call["args"][0] == "wechat_article_too_short")
        for call in captured
        if call["args"]
    ), f"expected wechat_article_too_short warning; got {captured}"


@pytest.mark.asyncio
async def test_wechat_article_appends_three_cta_placeholders() -> None:
    llm = FakeContentLLM()
    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=llm,
    )
    body = content.content
    for placeholder in REQUIRED_CTA_PLACEHOLDERS:
        assert placeholder in body, f"missing CTA placeholder: {placeholder}"
    # — The canonical block contains all three placeholders and the
    # "扫码" / "微信号" / "知识星球" lead-in labels.
    assert "扫码" in body
    assert "微信号" in body
    assert "知识星球" in body
    # — metadata echoes the placeholder list verbatim.
    assert content.metadata["cta_placeholders"] == list(REQUIRED_CTA_PLACEHOLDERS)


@pytest.mark.asyncio
async def test_wechat_article_does_not_duplicate_cta_block() -> None:
    """If the LLM already appended our CTA block (it shouldn't, but if
    it does), our `_enforce_cta` must NOT add a second copy."""

    canonical_tail = (
        "扫码加我微信,围观我从0到1复刻的全过程:{{WECHAT_QR}}\n\n"
        "微信号:{{WECHAT_ID}}\n\n"
        "知识星球(每周更新海外 AI 项目拆解):{{KNOWLEDGE_PLANET_URL}}\n"
    )

    class _CtaLLM(LLMProvider):
        name = "cta-wx"

        async def complete_json(self, **_: Any) -> str:
            return (
                "# 一个标题 — 已含 CTA\n\n"
                "段落内容。\n\n"
                + canonical_tail
            )

    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=_CtaLLM(),
    )
    body = content.content
    # — Exactly one occurrence of each placeholder.
    assert body.count("{{WECHAT_QR}}") == 1
    assert body.count("{{WECHAT_ID}}") == 1
    assert body.count("{{KNOWLEDGE_PLANET_URL}}") == 1


@pytest.mark.asyncio
async def test_wechat_article_image_placeholders_match_sections() -> None:
    """Fixture has 4 H2 sections and 4 `{{IMAGE_N}}` placeholders —
    generator must preserve them, not duplicate."""
    llm = FakeContentLLM()
    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=llm,
    )
    body = content.content
    # — 4 distinct placeholders.
    for n in (1, 2, 3, 4):
        assert f"{{{{IMAGE_{n}}}}}" in body, f"missing IMAGE_{n}"
    assert body.count("{{IMAGE_") == 4
    # — metadata echoes the count.
    assert content.metadata["image_placeholders"] == 4


@pytest.mark.asyncio
async def test_wechat_article_title_extracted_from_first_line() -> None:
    """Title is the first line stripped of `#`, ≤ 26 chars.

    The fixture title is 31 chars long, so the generator must
    truncate it with an ellipsis and flip the `title_truncated` flag
    — that's the "≤ 26 chars" contract. Test the truncation path
    here; the negative case (short title stays short) is covered by
    `test_wechat_article_title_truncated_when_over_limit` which uses
    an LLM stub that returns a title just under the limit.
    """
    llm = FakeContentLLM()
    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=llm,
    )
    title = content.title
    assert 8 <= len(title) <= 30, f"公众号标题长度应在 8-30 字, got {len(title)}: {title!r}"
    assert "复刻" in title or "5 万美元" in title
    # — 31-char fixture → generator truncates with `…`.
    assert title.endswith("…")
    assert content.metadata["title_truncated"] is True


@pytest.mark.asyncio
async def test_wechat_article_title_stays_short_when_under_limit() -> None:
    """Negative case: a 14-char title (under TITLE_MAX_CHARS) is
    returned verbatim — no ellipsis, no `title_truncated` flag."""

    class _ShortTitleLLM(LLMProvider):
        name = "short-title"

        async def complete_json(self, **_: Any) -> str:
            return (
                "# 14字刚好合规标题"
                "\n\n内容。\n\n## 第一节\n更多内容。\n\n"
                "## 第二节\n继续填充。\n\n## 第三节\n收尾。\n"
            )

    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=_ShortTitleLLM(),
    )
    assert content.title == "14字刚好合规标题"
    assert content.metadata["title_truncated"] is False


@pytest.mark.asyncio
async def test_wechat_article_title_truncated_when_over_limit() -> None:
    """A title that's clearly longer than the 26-char limit gets cut
    with an ellipsis and the `title_truncated` flag flips on."""

    class _LongTitleLLM(LLMProvider):
        name = "long-title"

        async def complete_json(self, **_: Any) -> str:
            # — 31-char title (after stripping `# `) so it's strictly
            # longer than TITLE_MAX_CHARS (26).
            return (
                "# 这是一个非常非常长的公众号标题用来测试截断的逻辑哦哈呀"
                "\n\n内容。\n\n## 第一节\n更多内容填充字数。\n\n"
                "## 第二节\n继续填充。\n\n## 第三节\n收尾。\n"
            )

    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=_LongTitleLLM(),
    )
    assert len(content.title) <= 26
    assert content.title.endswith("…")
    assert content.metadata["title_truncated"] is True


@pytest.mark.asyncio
async def test_wechat_article_user_prompt_includes_research_fields() -> None:
    gen = WechatArticleGenerator()
    user = gen.user_prompt(
        opportunity=_make_opportunity(), report=_make_report()
    )
    # — opportunity fields
    assert "AI 客服质量分析工具" in user
    assert "executive_summary" in user or "executive" in user.lower()
    assert "market_analysis" in user or "市场分析" in user
    assert "monetization_analysis" in user or "monetization" in user.lower()
    assert "mvp_analysis" in user or "mvp_days" in user
    # — language directive moved to system_prompt (Phase 9 DRY):
    # it's a system-level constraint, not per-opportunity context.
    assert "简体中文" in gen.system_prompt()


@pytest.mark.asyncio
async def test_wechat_article_handles_dict_shaped_llm_output() -> None:
    class _DictWrapper(LLMProvider):
        name = "wrapper-wx"

        async def complete_json(self, **_: Any) -> dict[str, Any]:
            return {
                "text": "# 字典封装的公众号长文标题\n\n"
                "## 第一节\n短段落。\n\n"
                "## 第二节\n另一段。\n\n"
                "## 第三节\n还有一段。\n"
            }

    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=_DictWrapper(),
    )
    # — Provider-agnostic extraction pulled the text out of `{"text": …}`.
    assert "字典封装的公众号长文标题" in content.content
    # — CTA still appended regardless of input shape.
    assert "{{WECHAT_QR}}" in content.content


@pytest.mark.asyncio
async def test_wechat_article_metadata_includes_char_count_and_read_minutes() -> None:
    llm = FakeContentLLM()
    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=llm,
    )
    assert content.metadata["char_count"] >= MIN_CHARS
    # — ~1700 chars / 400 ≈ 4 minutes minimum.
    assert content.metadata["read_minutes"] >= 1
    assert content.metadata["score"] == pytest.approx(87.0)


@pytest.mark.asyncio
async def test_wechat_article_system_prompt_has_key_constraints() -> None:
    """system_prompt must explicitly mention the hard constraints so
    they're visible in audit / regression reviews."""
    gen = WechatArticleGenerator()
    sp = gen.system_prompt()
    assert "第一人称" in sp
    assert "数字钩子" in sp
    assert "颠覆" in sp or "革命" in sp  # — explicitly banned list
    assert "{{WECHAT_QR}}" in sp
    assert "{{WECHAT_ID}}" in sp
    assert "{{KNOWLEDGE_PLANET_URL}}" in sp
    # — word-count hard limits
    assert str(MIN_CHARS) in sp
    assert str(MAX_CHARS := 3000) in sp  # noqa: F841 — confirms constant matches


@pytest.mark.asyncio
async def test_wechat_article_uses_higher_max_tokens() -> None:
    """公众号长文需要 ~4096 tokens 给 LLM;不能跟 daily_report 一样用 1024 默认。"""
    llm = FakeContentLLM()
    gen = WechatArticleGenerator()
    await gen.generate(
        opportunity=_make_opportunity(),
        report=_make_report(),
        llm=llm,
    )
    assert llm.last_call is not None
    assert llm.last_call["max_tokens"] >= 4096


@pytest.mark.asyncio
async def test_wechat_article_handles_missing_report() -> None:
    """`report=None` must not raise; generator falls back to opportunity
    fields only."""
    llm = FakeContentLLM()
    gen = WechatArticleGenerator()
    content = await gen.generate(
        opportunity=_make_opportunity(),
        report=None,
        llm=llm,
    )
    # — Still produced a body + CTA even with no deep-research context.
    assert len(content.content) > 500
    assert "{{WECHAT_QR}}" in content.content


# ---------------------------------------------------------------------------
# Phase 9 — direct unit tests for the base helpers (DRY cleanup).
# These were promoted out of daily_report / xiaohongshu_post /
# wechat_article in Phase 9; locking them down at the helper layer
# keeps the per-generator subclasses honest about which behaviour is
# "shared" vs "channel-specific".
# ---------------------------------------------------------------------------
class TestExtractTextFromLlm:
    """`extract_text_from_llm` accepts strings + 4 dict shapes."""

    def test_passthrough_when_already_string(self) -> None:
        assert extract_text_from_llm("# Hello\n\nbody") == "# Hello\n\nbody"

    def test_extracts_from_text_key(self) -> None:
        assert extract_text_from_llm({"text": "body"}) == "body"

    def test_extracts_from_markdown_key(self) -> None:
        assert extract_text_from_llm({"markdown": "# md"}) == "# md"

    def test_extracts_from_content_key(self) -> None:
        assert extract_text_from_llm({"content": "raw text"}) == "raw text"

    def test_extracts_from_body_key(self) -> None:
        assert extract_text_from_llm({"body": "real body"}) == "real body"

    def test_falls_back_to_json_fence_when_no_known_key(self) -> None:
        out = extract_text_from_llm({"foo": "bar"})
        # We don't want to silently drop model output — surface it as
        # a JSON fence so the operator sees what came back.
        assert "```json" in out
        assert '"foo"' in out

    def test_empty_string_in_text_key_falls_through(self) -> None:
        # Empty string is treated as missing — try the next key.
        out = extract_text_from_llm({"text": "", "body": "real"})
        assert out == "real"

    def test_non_string_value_skipped(self) -> None:
        out = extract_text_from_llm({"text": 42, "body": "fallback"})
        assert out == "fallback"


class TestExtractTitleFromBody:
    def test_first_non_empty_line_strips_hashes(self) -> None:
        body = "# Title here\n\nbody body body"
        title, truncated = extract_title_from_body(body)
        assert title == "Title here"
        assert truncated is False

    def test_returns_empty_when_body_blank(self) -> None:
        title, truncated = extract_title_from_body("\n\n\n")
        assert title == ""
        assert truncated is False

    def test_truncates_with_ellipsis_when_over_max(self) -> None:
        body = "# " + ("x" * 50)
        title, truncated = extract_title_from_body(body, max_chars=20)
        assert len(title) <= 20
        assert title.endswith("…")
        assert truncated is True

    def test_no_truncation_when_under_max(self) -> None:
        body = "# short title"
        title, truncated = extract_title_from_body(body, max_chars=30)
        assert title == "short title"
        assert truncated is False

    def test_max_chars_none_disables_truncation(self) -> None:
        body = "# " + ("x" * 1000)
        title, truncated = extract_title_from_body(body)
        assert truncated is False
        assert len(title) == 1000

    def test_custom_ellipsis_marker(self) -> None:
        body = "# " + ("x" * 50)
        title, _ = extract_title_from_body(body, max_chars=10, ellipsis="...")
        assert title.endswith("...")

    def test_strips_multiple_hash_levels(self) -> None:
        # Some LLMs produce ## or ### for the first line if they
        # mistake the body for a section heading.
        body = "### My Title\n\nbody"
        title, _ = extract_title_from_body(body)
        assert title == "My Title"


class TestAppendBlockIfMissing:
    def test_appends_when_marker_absent(self) -> None:
        out = append_block_if_missing(
            "hello", "{{CTA}}", "\n\n👉 {{CTA}}"
        )
        assert out == "hello\n\n👉 {{CTA}}"

    def test_skips_when_marker_present(self) -> None:
        out = append_block_if_missing(
            "hello {{CTA}}", "{{CTA}}", "EXTRA"
        )
        assert out == "hello {{CTA}}"

    def test_strips_trailing_whitespace_before_append(self) -> None:
        out = append_block_if_missing(
            "hello   \n\n", "{{X}}", "block"
        )
        # No double blank lines from sloppy whitespace handling.
        assert out == "hello\n\nblock"

    def test_strips_leading_whitespace_on_block(self) -> None:
        out = append_block_if_missing("body", "{{X}}", "   \nblock")
        assert out == "body\n\nblock"


class TestEnsureSectionPlaceholders:
    def test_no_op_when_enough_placeholders_already(self) -> None:
        body = (
            "## A\n\n![配图-1]({{IMAGE_1}})\n\n"
            "## B\n\nbody\n\n"
            "## C\n\n![配图-2]({{IMAGE_2}})\n\n"
        )
        # 2 sections, 2 placeholders, >= max(1, 2-1)=1 → unchanged
        out = ensure_section_placeholders(body)
        assert out == body

    def test_inserts_missing_placeholders(self) -> None:
        body = (
            "## A\n\nbody\n\n"
            "## B\n\nbody\n\n"
            "## C\n\nbody\n\n"
        )
        out = ensure_section_placeholders(body)
        # Each H2 section should now have a placeholder after it.
        assert out.count("![配图-") == 3
        assert "{{IMAGE_1}}" in out
        assert "{{IMAGE_3}}" in out

    def test_handles_no_sections(self) -> None:
        body = "just text, no headings"
        out = ensure_section_placeholders(body)
        # Single-section-less body shouldn't be mutated.
        assert out == body

    def test_handles_short_article(self) -> None:
        # Single section, no placeholders. Contract: max(1, 1-1)=1,
        # so the helper MUST insert 1 placeholder for single-section
        # articles — operators paste this into the editor and the
        # 公众号后台 won't auto-pick a cover image otherwise.
        body = "## A\n\nbody"
        out = ensure_section_placeholders(body)
        assert out.count("![配图-") == 1
        assert "{{IMAGE_1}}" in out


class TestBaseMetadata:
    def test_returns_score_category_market_block(self) -> None:
        opp = SimpleNamespace(
            total_score=87.5, category="AI", market="海外"
        )
        meta = base_metadata(opportunity=opp)
        assert meta["score"] == 87.5
        assert meta["category"] == "AI"
        assert meta["market"] == "海外"

    def test_handles_missing_attributes(self) -> None:
        # The helper uses getattr + defaults so missing fields
        # shouldn't raise.
        opp = SimpleNamespace()
        meta = base_metadata(opportunity=opp)
        assert meta["score"] == 0.0
        assert meta["category"] is None
        assert meta["market"] is None