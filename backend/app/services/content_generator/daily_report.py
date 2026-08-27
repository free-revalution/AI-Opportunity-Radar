"""Daily-report generator — long-form Markdown.

The output is the canonical "AI 机会雷达日报". It feeds:

  * the **Feishu daily bot** (Phase 2 — full Feishu card)
  * the **公众号 long article** (re-skinned in wechat_article.py)
  * the **dashboard Content Center** preview pane

The structure follows the doc spec (机会名称 / 来源 / 用户痛点 /
市场分析 / 商业机会 / 推荐行动) and is enforced via a Markdown
template rather than free-form prose — that way downstream
distributors can re-parse sections reliably.
"""

from __future__ import annotations

from typing import Any

from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
    register,
)
from app.services.llm.provider import LLMProvider
from app.utils import get_logger

logger = get_logger(__name__)


class DailyReportGenerator(ContentGenerator):
    """Markdown daily-report for one opportunity."""

    name = "daily_report"
    channel = "feishu"
    format = "markdown"
    description = "AI 机会雷达日报 — Markdown 长文,用于飞书 / 公众号"

    async def generate(
        self,
        *,
        opportunity: Any,
        report: Any | None,
        llm: LLMProvider,
    ) -> GeneratedContent:
        user_prompt = self.user_prompt(opportunity=opportunity, report=report)
        # No JSON schema — Markdown; we enforce structure via the
        # system prompt + a closing template the model is told to fill.
        raw = await llm.complete_json(
            system=self.system_prompt(),
            user=user_prompt,
            response_schema=None,
        )
        # Some providers return {"text": "..."} when no schema is given.
        body = self._extract_text(raw)
        title = opportunity.title or "未命名机会"
        return GeneratedContent(
            opportunity_id=int(opportunity.id),
            generator=self.name,
            channel=self.channel,
            title=f"今日AI商业机会: {title}",
            format=self.format,
            content=body,
            metadata={
                "score": float(getattr(opportunity, "total_score", 0.0) or 0.0),
                "category": getattr(opportunity, "category", None),
                "market": getattr(opportunity, "market", None),
                "mvp_days": int(getattr(opportunity, "mvp_days", 0) or 0),
            },
        )

    # ----- prompts ---------------------------------------------------
    def system_prompt(self) -> str:
        return (
            "你是一名面向中国个人开发者的 AI 商业分析师,负责把海外 AI "
            "产品机会翻译成可落地的中文商业简报。\n\n"
            "输出必须是 Markdown,严格按以下章节顺序,每节 1-3 句话,"
            "不要添加章节之外的任何内容:\n\n"
            "# 今日AI商业机会\n\n"
            "## 机会名称\n<产品名 + 一句话定位>\n\n"
            "## 来源\n<列出 1-3 个一手信息源,例如 'Reddit: xxx 讨论' "
            "或 'GitHub: xxx 项目本月增长 50%'>\n\n"
            "## 用户痛点\n<目标用户当前的痛点,具体到场景>\n\n"
            "## 市场分析\n<市场规模 + 主要竞品 + 这条赛道的差异化窗口>\n\n"
            "## 商业机会\n<给出 0-100 评分 + 为什么这个分数; 同时写出一句话 "
            "monetization model, 例如 'SaaS 订阅 / 49 USD/月'>\n\n"
            "## 推荐行动\n<给中国个人开发者的具体建议,例如 '14 天可完成 "
            "MVP,优先做 X 功能,首发渠道 Y'>\n\n"
            "写作风格:客观、克制、有数字。避免'颠覆''革命'这类夸张词。"
        )

    def user_prompt(self, *, opportunity: Any, report: Any | None) -> str:
        parts: list[str] = []
        parts.append(f"# 机会标题:{opportunity.title}")
        if getattr(opportunity, "summary", None):
            parts.append(f"\n## 简短摘要\n{opportunity.summary}")
        if getattr(opportunity, "target_user", None):
            parts.append(f"\n## 目标用户\n{opportunity.target_user}")
        if getattr(opportunity, "target_customer", None):
            parts.append(f"\n## 目标客户画像\n{opportunity.target_customer}")
        if getattr(opportunity, "market_size", None):
            parts.append(f"\n## 估算市场规模\n{opportunity.market_size}")
        if getattr(opportunity, "monetization_model", None):
            parts.append(f"\n## 商业模式\n{opportunity.monetization_model}")
        if getattr(opportunity, "mvp_days", None):
            parts.append(f"\n## 估算 MVP 天数\n{opportunity.mvp_days}")
        if getattr(opportunity, "difficulty", None):
            parts.append(f"\n## 实现难度\n{opportunity.difficulty}")
        if getattr(opportunity, "china_gap", None):
            parts.append(f"\n## 中国市场空白\n{opportunity.china_gap}")
        if getattr(opportunity, "total_score", None):
            parts.append(f"\n## 当前评分(0-100)\n{opportunity.total_score}")

        if report is not None:
            parts.append("\n## 深度研究输出")
            for k in (
                "executive_summary",
                "market_analysis",
                "competition_analysis",
                "china_analysis",
                "monetization_analysis",
                "mvp_analysis",
                "risk_analysis",
                "recommendation",
                "confidence",
            ):
                v = getattr(report, k, None)
                if v:
                    parts.append(f"\n### {k}\n{v}")

        return "\n".join(parts).strip()

    # ----- helpers ---------------------------------------------------
    @staticmethod
    def _extract_text(raw: dict[str, Any]) -> str:
        """Provider-agnostic: pull the markdown out of whatever the LLM
        returned. Some providers wrap in `{"text": ...}`, some return
        the dict that *is* the markdown string, some nest under
        `content`. We accept all three."""
        if not isinstance(raw, dict):
            return str(raw)
        for key in ("text", "markdown", "content", "body"):
            v = raw.get(key)
            if isinstance(v, str) and v.strip():
                return v
        # Last resort — dump the whole dict as a JSON fenced block.
        import json

        return "```json\n" + json.dumps(raw, ensure_ascii=False, indent=2) + "\n```"


# Self-register on import.
register(DailyReportGenerator())


__all__ = ["DailyReportGenerator"]