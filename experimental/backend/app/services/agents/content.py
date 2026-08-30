"""ContentRadarAgent — the first vertical agent (Content Radar MVP).

Per docs/下一阶段开发技术方案.md §15-17:

> ContentRadarAgent 输入: Signal + 相关 ResearchReport + source evidence
>                          + 用户画像 + platform
> 输出: ContentOpportunity

The agent's job is to translate a *raw* signal (a change in the world)
into a *creator-ready* content brief (an angle, a hook, title candidates,
a 30-second script outline, material ideas, a CTA, and a risk warning).

Two implementation layers:

  * ``HeuristicContentRadarAgent`` — the always-available, deterministic
    implementation that doesn't call an LLM. Used as a fallback when no
    LLM is configured, and used as the test-fixture baseline.

  * The real LLM-backed implementation plugs in via Phase 13 once the
    Compliance + Signal flows are stable.

Both share the same protocol so the registry can swap them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .base import VerticalAgent, VerticalContext, VerticalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _signal_title(signal: Any) -> str:
    """Extract a human title from a Signal row or dict.

    Falls back to ``signal_type:keyword`` when no title is set.
    """
    title = _get(signal, "title")
    if title:
        return str(title)
    signal_type = _get(signal, "signal_type", "signal")
    keyword = _get(signal, "keyword", "")
    if keyword:
        return f"{signal_type}: {keyword}"
    return str(signal_type)


def _signal_summary(signal: Any) -> str:
    """Extract a one-paragraph summary."""
    summary = _get(signal, "summary")
    if summary:
        return str(summary)
    keyword = _get(signal, "keyword", "")
    category = _get(signal, "category", "")
    return f"{keyword} {category}".strip() or "趋势信号"


def _signal_signal_type(signal: Any) -> str:
    return str(_get(signal, "signal_type", ""))


def _signal_keyword(signal: Any) -> str:
    return str(_get(signal, "keyword", ""))


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


# ---------------------------------------------------------------------------
# HeuristicContentRadarAgent — deterministic, LLM-free.
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class HeuristicContentRadarAgent:
    """Always-available, deterministic Content Radar.

    Generates a 30-second script outline + 3 title candidates + 2
    material ideas + a soft CTA + a risk warning, all from the Signal's
    own fields. Used as the test baseline and as the fallback when no
    LLM is configured.
    """

    name: str = "content"

    async def analyze(
        self,
        signal: Any,
        context: VerticalContext,
        *,
        report: Any | None = None,
    ) -> VerticalResult:
        title = _signal_title(signal)
        summary = _signal_summary(signal)
        keyword = _signal_keyword(signal)
        signal_type = _signal_signal_type(signal)
        platform = context.platform or "general"

        # Title candidates --------------------------------------------------
        title_candidates = _generate_title_candidates(
            keyword=keyword, summary=summary, signal_type=signal_type, platform=platform
        )

        # Hook --------------------------------------------------------------
        hook = _generate_hook(summary, keyword)

        # Script outline (30 seconds) --------------------------------------
        script_outline = _generate_script_outline(
            title=title, summary=summary, audience=context.audience
        )

        # Material ideas ---------------------------------------------------
        material_ideas = _generate_material_ideas(keyword, summary, signal_type)

        # CTA --------------------------------------------------------------
        cta = _generate_cta(platform)

        # Risk warning (always populated — never silently empty) ----------
        risk_warning = _generate_risk_warning(signal, report)

        # Recommended length (per platform) -------------------------------
        recommended_length = _platform_recommended_length(platform)

        payload: dict[str, Any] = {
            "title": title,
            "summary": summary,
            "platform": platform,
            "audience": context.audience,
            "niche": context.niche,
            "tone": context.tone,
            "content_angle": _generate_angle(summary, keyword, context),
            "hook": hook,
            "title_candidates": title_candidates,
            "script_outline": script_outline,
            "material_ideas": material_ideas,
            "recommended_length": recommended_length,
            "cta": cta,
            "risk_warning": risk_warning,
            "status": "draft",
        }

        # Confidence — the more fields the Signal carries, the higher the
        # confidence in the projection. Heuristic maxes out at 0.7.
        confidence = _confidence_from_signal(signal)

        # Sources used -----------------------------------------------------
        sources = _extract_sources(report)

        return VerticalResult(
            vertical=self.name,
            opportunity_id=None,  # caller assigns when persisting
            payload=payload,
            rationale=(
                "Heuristic Content Radar projection based on Signal.title, "
                "Signal.summary, and user-provided platform/audience."
            ),
            sources_used=sources,
            confidence=confidence,
        )


# ---------------------------------------------------------------------------
# Generation helpers (deterministic, no LLM).
# ---------------------------------------------------------------------------
def _generate_title_candidates(
    keyword: str, summary: str, signal_type: str, platform: str
) -> list[str]:
    """Generate 3 title candidates.

    Format convention: 14-26 Chinese characters, includes a numeric or
    emoji hook when the platform calls for it (抖音 / 小红书).
    """
    base = keyword or signal_type or "热点"
    safe_summary = summary[:24].rstrip("。. ") if summary else base
    candidates: list[str] = []

    # Candidate 1 — numeric hook (most platforms)
    candidates.append(f"3 分钟看懂 {base} 为何突然爆火")

    # Candidate 2 — question form (high CTR on 小红书)
    candidates.append(f"{base} 到底意味着什么?一文给你讲清楚")

    # Candidate 3 — platform-flavored
    if platform in {"xiaohongshu", "douyin"}:
        candidates.append(f"🔥 {base} 还能火多久?普通人怎么抓住机会")
    elif platform == "wechat":
        candidates.append(f"深度拆解:{base} 背后的产业逻辑")
    else:
        candidates.append(f"{safe_summary} — 给我们什么启发")

    return candidates


def _generate_hook(summary: str, keyword: str) -> str:
    """First 2-3 sentences — strong opener."""
    if summary:
        first = summary.split("。")[0].strip()
        if first:
            return f"{first}。这件事为什么突然火了?一句话给你说透。"
    return f"{keyword or '这个信号'} 正在多个社区同时出现 — 意味着什么?"


def _generate_script_outline(title: str, summary: str, audience: str) -> str:
    """30-second script outline (4 beats)."""
    bullets = [
        f"开场 (5s): {title}",
        f"背景 (10s): {summary[:60] if summary else '这件事的来龙去脉'}",
        f"分析 (10s): 为什么会发生、对 {audience or '普通用户'} 意味着什么",
        "收尾 (5s): 一句话总结 + CTA",
    ]
    return "\n".join(f"- {b}" for b in bullets)


def _generate_material_ideas(
    keyword: str, summary: str, signal_type: str
) -> list[str]:
    """2-3 material / B-roll ideas."""
    base = keyword or "主题"
    return [
        f"数据图: {base} 在主流社区的搜索量趋势(过去 7 天)",
        f"对比表: {base} 与去年同期 / 上个月的变化",
        "专家/媒体评论截图: 至少 2 个独立来源",
    ]


def _generate_cta(platform: str) -> str:
    """Platform-appropriate CTA placeholder."""
    if platform == "xiaohongshu":
        return "评论区告诉我你的看法,点赞收藏不迷路"
    if platform == "douyin":
        return "主页更多 AI 商业机会拆解,关注不错过"
    if platform == "wechat":
        return "点击在看 + 关注公众号,持续追踪下一个风口"
    return "想看更多同类信号?关注我们"


def _generate_risk_warning(signal: Any, report: Any | None) -> str:
    """Always populate a risk warning — never silently empty.

    Triggers a longer warning when the Signal carries a high risk_score
    or the research report flagged low confidence.
    """
    risk_score = float(_get(signal, "risk_score", 0.0) or 0.0)
    confidence = float(_get(report, "confidence", 1.0) if report else 1.0)
    parts: list[str] = []
    if risk_score > 0.3:
        parts.append(
            f"本信号合规风险分 {risk_score:.2f},需经管理员审核后再发布"
        )
    if report is not None and confidence < 0.5:
        parts.append(
            f"研究置信度 {confidence:.2f},建议附加更多独立来源再下结论"
        )
    if not parts:
        parts.append("发布前请再次核对原始来源链接,不要凭印象做判断")
    return "。".join(parts)


def _generate_angle(summary: str, keyword: str, context: VerticalContext) -> str:
    """Single-sentence content angle."""
    who = context.audience or "普通用户"
    return (
        f"从 {who} 的视角,拆解 {keyword or '这一信号'} 的商业机会与风险"
    )


def _platform_recommended_length(platform: str) -> int:
    """Recommended length (chars / seconds) by platform."""
    return {
        "douyin": 60,
        "xiaohongshu": 200,
        "bilibili": 600,
        "wechat": 2500,
        "general": 800,
    }.get(platform, 800)


def _confidence_from_signal(signal: Any) -> float:
    """Confidence in the projection — depends on Signal field richness."""
    score = 0.3  # baseline
    if _get(signal, "title"):
        score += 0.1
    if _get(signal, "summary"):
        score += 0.15
    if _get(signal, "keyword"):
        score += 0.05
    if _get(signal, "source_count", 0) and _get(signal, "source_count") > 1:
        score += 0.1
    return min(0.7, score)


def _extract_sources(report: Any | None) -> list[str]:
    """Pull source URLs out of a ResearchReport row."""
    if not report:
        return []
    raw = _get(report, "sources_json", None) or {}
    if isinstance(raw, dict):
        urls = raw.get("urls") or raw.get("sources") or []
        return [str(u) for u in urls if u]
    if isinstance(raw, list):
        return [str(u) for u in raw if u]
    return []


__all__ = ["HeuristicContentRadarAgent"]