"""Xiaohongshu (小红书) post generator — Markdown for mobile reading.

小红书 posts are short (300-600 字), personal-voice, and hashtag-heavy.
Title length is capped by the platform (~20 字). The output must read
like a human telling their friend "我看到一个项目,国内还没人做".

The doc spec calls for:
* project intro
* market analysis (海外 vs 国内)
* 创业建议 (actionable)
* 引流 (link back to where the reader can buy / learn more)

We render 引流 as a fixed placeholder `{{CTA_URL}}` so the
distribution layer can substitute the per-channel landing page
(Xianyu listing, 知识星球 invite, etc.) without re-running the
generator.

Phase 9 — DRY cleanup: `_extract_text` / `_extract_title` /
`append_block_if_missing` come from `base.py`. Channel-specific
logic that's still local: `_suggest_hashtags` (heuristic tag set
that depends on `opportunity.category`).
"""

from __future__ import annotations

from typing import Any

from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
    append_block_if_missing,
    extract_text_from_llm,
    extract_title_from_body,
    register,
)
from app.services.llm.provider import LLMProvider


class XiaohongshuPostGenerator(ContentGenerator):
    name = "xiaohongshu_post"
    channel = "xiaohongshu"
    format = "markdown"
    description = "小红书图文笔记 — Markdown 短文,emoji + 话题标签"
    # 小红书 — tighter context list (no research_report_fields); the
    # 300-600 字 budget doesn't leave room for 8 sections of report.
    opportunity_context_fields = (
        "title",
        "summary",
        "target_user",
        "market_size",
        "mvp_days",
        "china_gap",
        "total_score",
    )
    research_report_fields = ("executive_summary",)

    async def generate(
        self,
        *,
        opportunity: Any,
        report: Any | None,
        llm: LLMProvider,
    ) -> GeneratedContent:
        raw = await llm.complete_json(
            system=self.system_prompt(),
            user=self.user_prompt(opportunity=opportunity, report=report),
            response_schema=None,
        )
        body = extract_text_from_llm(raw)
        # 30-char cap (no ellipsis) — 小红书 平台会自动截断更长的标题,
        # 加省略号反而显得 prompty.
        title, _ = extract_title_from_body(body, max_chars=30, ellipsis="")
        if not title:
            title = opportunity.title
        hashtags = self._suggest_hashtags(opportunity)
        # CTA line is appended unconditionally via the shared helper —
        # never trust the LLM to remember it.
        body = append_block_if_missing(
            body, "{{CTA_URL}}", "👉 完整项目分析:{{CTA_URL}}"
        )
        body_with_hashtags = body.rstrip() + "\n\n" + " ".join(hashtags)
        return GeneratedContent(
            opportunity_id=int(opportunity.id),
            generator=self.name,
            channel=self.channel,
            title=title,
            format=self.format,
            content=body_with_hashtags,
            metadata={
                "hashtags": hashtags,
                "cta_placeholder": "{{CTA_URL}}",
            },
        )

    # ----- prompts ---------------------------------------------------
    def system_prompt(self) -> str:
        return (
            "你是一名小红书博主,写'海外 AI 项目拆解'类的图文笔记。\n\n"
            "目标读者:中国互联网圈的个人开发者和独立创业者。\n\n"
            "写作要求:\n"
            "* 总长 300-600 字\n"
            "* 第一行必须是一个 18-22 字的爆款标题(用中文标点,不堆 emoji)\n"
            "* 语气:像在跟闺蜜/兄弟聊,'我看到一个项目,觉得挺有意思'\n"
            "* 段落用 1-3 句短段落,不要长文\n"
            "* 结尾必须有一行 `👉 完整项目分析:{{CTA_URL}}`\n"
            "* 不要写 '颠覆/革命/划时代' 这类词,会被读者跳过\n\n"
            "结构(自由发挥顺序,但都要覆盖):\n"
            "* 项目介绍(国外哪个项目,做什么)\n"
            "* 海外 vs 国内对比(中国为什么还没人做)\n"
            "* 个人开发者的实操建议(MVP 几天、首发渠道、定价区间)\n"
            "* 引流(用上面的 CTA 占位符,不要自己填 URL)\n"
        )

    # ----- helpers ---------------------------------------------------
    @staticmethod
    def _suggest_hashtags(opportunity: Any) -> list[str]:
        # Heuristic tag set — never trust the model here. Operators can
        # override by editing the markdown before pasting.
        tags = [
            "#海外项目",
            "#AI创业",
            "#独立开发",
        ]
        cat = getattr(opportunity, "category", None) or ""
        if cat:
            tags.append(f"#{cat}")
        mkt = getattr(opportunity, "market", None) or ""
        if mkt and mkt != cat:
            tags.append(f"#{mkt}")
        return tags[:6]


register(XiaohongshuPostGenerator())


__all__ = ["XiaohongshuPostGenerator"]
