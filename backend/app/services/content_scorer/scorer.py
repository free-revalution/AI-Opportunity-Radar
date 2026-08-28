"""LLM-as-judge content quality scoring.

Phase 10 — gives the operator a single number per generated piece so
they can spot the "LLM produced something but it's 4/10" cases without
manually reading all 4 channels × N opportunities.

The scorer is intentionally cheap and deterministic — one LLM call per
piece, fixed JSON schema, fixed weights. We don't try to be clever;
operators want a number they can sort by, not a research report.

Public API
==========

* `ContentQualityScorer.score(notification, channel_meta, llm)` — returns
  a `ContentQualityScore` with five sub-scores + a weighted total + a
  one-line rationale. The total is a float 1.0-10.0.
* `DEFAULT_THRESHOLD = 6.0` — below this we auto-regenerate (only
  triggered when the caller opts in via `auto_regenerate=True`).

What gets scored (5 dimensions, each 1-10):

1. **hook_strength** — does the first 100 chars hook the reader? Are
   numbers / contrast / 故事 used?
2. **cta_naturalness** — does the CTA flow or feel bolted on? (For
   JSON-shape channels like xianyu, this maps to "title + selling
   points + price CTA").
3. **data_accuracy** — does the body cite specific numbers from the
   source opportunity (mvp_days / market_size / etc.) or is it hand-
   wavy? This is the dimension most likely to catch LLM hallucination.
4. **char_count_compliance** — within `min_chars`..`max_chars`? (1.0
   if <50% of min, 10.0 if in band, gradient out to either side).
5. **platform_style_match** — does it sound like the channel? (公众号
   first-person + 数字钩子 vs xiaohongshu emoji + punchy bullets vs
   feishu section-of-fields report vs xianyu ecommerce pitch).

Weights are channel-agnostic; each dimension is equally important
(0.20). If channel-specific weighting turns out to matter in practice,
override `weights()` on a subclass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from app.services.llm.provider import LLMProvider
from app.utils import get_logger

logger = get_logger(__name__)


# Below this total score (and below `dimension_floor` on any single
# dimension) the operator wants to auto-regenerate. 6.0 / 10 = "good
# enough to ship"; below that the LLM is likely producing filler.
DEFAULT_THRESHOLD: float = 6.0
DEFAULT_DIMENSION_FLOOR: float = 4.0

# Default equal weights — overridable per channel via `weights()`.
_DIMENSION_NAMES: tuple[str, ...] = (
    "hook_strength",
    "cta_naturalness",
    "data_accuracy",
    "char_count_compliance",
    "platform_style_match",
)


@dataclass
class ContentQualityScore:
    """Five-dimensional quality verdict on one piece of generated content."""

    hook_strength: float
    cta_naturalness: float
    data_accuracy: float
    char_count_compliance: float
    platform_style_match: float
    total: float
    rationale: str
    # Convenience flag — True if any dimension is below floor OR total
    # below threshold. Set by `score()` after LLM returns.
    below_threshold: bool = False
    threshold_used: float = DEFAULT_THRESHOLD
    dimension_floor_used: float = DEFAULT_DIMENSION_FLOOR

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _ScoreRaw:
    """Raw shape the LLM must return — kept separate from the public
    DTO so we can validate fields before computing the total."""

    hook_strength: float
    cta_naturalness: float
    data_accuracy: float
    char_count_compliance: float
    platform_style_match: float
    rationale: str = ""
    # Extra keys the LLM may emit (we ignore them, but parse_json
    # silently drops them rather than erroring).


# JSON schema the LLM must fill — strict mode keeps the response cheap.
_SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hook_strength": {
            "type": "number",
            "minimum": 1,
            "maximum": 10,
            "description": "开头 hook 强度(1-10)。前 100 字是否抓住读者:数字反差 / 故事场景 / 痛点提问。",
        },
        "cta_naturalness": {
            "type": "number",
            "minimum": 1,
            "maximum": 10,
            "description": "CTA 自然度(1-10)。行动号召是否流畅而非生硬拼接。JSON 形态渠道映射为卖点 + 价格。",
        },
        "data_accuracy": {
            "type": "number",
            "minimum": 1,
            "maximum": 10,
            "description": "数据准确性(1-10)。正文是否引用了具体的数字 / 价格 / 天数 / 用户量,还是泛泛而谈。",
        },
        "char_count_compliance": {
            "type": "number",
            "minimum": 1,
            "maximum": 10,
            "description": "字数合规(1-10)。是否符合该渠道的字数区间。10=完全合规,<5=严重偏离。",
        },
        "platform_style_match": {
            "type": "number",
            "minimum": 1,
            "maximum": 10,
            "description": "平台风格匹配(1-10)。是否符合该渠道的文体调性(公众号第一人称 / 小红书 emoji / 闲鱼电商 / 飞书简报)。",
        },
        "rationale": {
            "type": "string",
            "description": "一句话总结(20 字以内),用于前端展示。",
        },
    },
    "required": [
        "hook_strength",
        "cta_naturalness",
        "data_accuracy",
        "char_count_compliance",
        "platform_style_match",
    ],
}


_SYSTEM_PROMPT = (
    "你是一名资深中文内容运营,负责给 AI 生成的销售文案打分。\n\n"
    "你的工作:读一段 AI 生成的渠道文案,以及该渠道对应的元数据"
    "(机会背景 / 字数区间 / 风格要求),按 5 个维度各打 1-10 分。\n\n"
    "打分原则:\n"
    "* 严格 — 大部分 AI 产出是 5-7 分,8+ 必须真的好\n"
    "* 不给同情分 — '至少是个东西' = 4 分\n"
    "* 字数严重偏离(超出 50%):直接 1-2 分,不论其他维度多好\n"
    "* 没有具体数字 / 价格 / 用户的泛泛文案,data_accuracy 不会高于 6\n\n"
    "返回严格符合 JSON Schema 的对象,不要解释,不要 markdown。"
)


# Per-channel style descriptions — the LLM needs to know what "platform
# style match" means. Kept short to avoid token waste.
_CHANNEL_STYLE: dict[str, str] = {
    "feishu": "飞书内部简报 — 第三人称 / 客观 / 结构化,字段齐全即可,不需要文学性",
    "xianyu": "闲鱼电商挂单 — 标题党 / 卖点密集 / 价格明确 / emoji 点缀,目标是快速促成成交",
    "xiaohongshu": "小红书种草 — 第一人称体验 / 大量 emoji / 短句 / 情绪化 / 数字 + 对比",
    "wechat_article": "公众号长文 — 第一人称 / 故事化 / 数字钩子标题 / 1500-3000 字 / H2 分节",
}


def _channel_style(channel: str) -> str:
    return _CHANNEL_STYLE.get(
        channel,
        f"{channel} 渠道 — 按通用中文销售文案标准评分",
    )


def _coerce_float(value: Any, *, default: float = 5.0) -> float:
    """Tolerate LLM emitting ints / strings for number fields."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v < 1.0:
        return 1.0
    if v > 10.0:
        return 10.0
    return round(v, 2)


class ContentQualityScorer:
    """LLM-as-judge. Stateless — safe to construct once and reuse."""

    threshold: float = DEFAULT_THRESHOLD
    dimension_floor: float = DEFAULT_DIMENSION_FLOOR

    def weights(self) -> Mapping[str, float]:
        """Equal weights by default. Override in a subclass for
        channel-specific tuning."""
        return {name: 0.20 for name in _DIMENSION_NAMES}

    async def score(
        self,
        *,
        notification_payload: Mapping[str, Any],
        llm: LLMProvider,
    ) -> ContentQualityScore:
        """Score one Notification row. Pure — does NOT mutate the
        payload. The caller decides whether to persist the result.

        `notification_payload` is the raw dict stored on `Notification.payload`
        (the same shape `ContentCenter` returns to the frontend)."""
        title = str(notification_payload.get("title") or "")
        body = notification_payload.get("body") or ""
        if not isinstance(body, str):
            body = str(body)
        metadata = notification_payload.get("metadata") or {}
        channel = str(notification_payload.get("channel") or "unknown")
        opportunity_id = notification_payload.get("opportunity_id")

        if not body.strip():
            # Empty piece — short-circuit, no point paying for an LLM call.
            logger.warning(
                "content_scorer_empty_body",
                notification_id=notification_payload.get("_id"),
                opportunity_id=opportunity_id,
            )
            return ContentQualityScore(
                hook_strength=1.0,
                cta_naturalness=1.0,
                data_accuracy=1.0,
                char_count_compliance=1.0,
                platform_style_match=1.0,
                total=1.0,
                rationale="正文为空,跳过评分",
                below_threshold=True,
                threshold_used=self.threshold,
                dimension_floor_used=self.dimension_floor,
            )

        user = self._build_user_prompt(
            title=title,
            body=body,
            metadata=metadata,
            channel=channel,
        )
        try:
            raw = await llm.complete_json(
                system=_SYSTEM_PROMPT,
                user=user,
                response_schema=_SCORE_SCHEMA,
                max_tokens=400,  # a score is small
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "content_scorer_llm_failed",
                opportunity_id=opportunity_id,
                channel=channel,
                error=str(exc),
            )
            # Soft-fail: return a neutral 5/10 so the operator sees the
            # piece without a scary error toast. The `below_threshold`
            # flag is False — we don't want to auto-regenerate on a
            # scoring failure (that would compound the cost).
            return ContentQualityScore(
                hook_strength=5.0,
                cta_naturalness=5.0,
                data_accuracy=5.0,
                char_count_compliance=5.0,
                platform_style_match=5.0,
                total=5.0,
                rationale="评分服务异常,跳过",
                below_threshold=False,
                threshold_used=self.threshold,
                dimension_floor_used=self.dimension_floor,
            )

        score = self._build_score(raw)
        return score

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    @staticmethod
    def _build_user_prompt(
        *,
        title: str,
        body: str,
        metadata: Mapping[str, Any],
        channel: str,
    ) -> str:
        # Body cap so the prompt doesn't explode on a 5000-char piece.
        # 4000 chars is well above what any reasonable scorer needs.
        body_for_prompt = body if len(body) <= 4000 else body[:4000] + "…"
        # Char count hint from metadata (each generator writes it).
        char_hint = metadata.get("char_count")
        char_range = metadata.get("char_range")
        lines: list[str] = [
            f"渠道:{channel}",
            f"渠道风格要求:{_channel_style(channel)}",
        ]
        if char_hint is not None or char_range is not None:
            if char_range:
                lines.append(f"字数区间:{char_range}")
            elif char_hint is not None:
                lines.append(f"当前字数:{char_hint}")
        # Surface the source opportunity context so the judge can check
        # `data_accuracy`. Most relevant: mvp_days / market_size /
        # monetization_model / target_customer.
        src = metadata.get("source_opportunity") or {}
        if src:
            lines.append("\n机会背景(用于核查 data_accuracy):")
            for key in (
                "title",
                "summary",
                "target_customer",
                "market_size",
                "mvp_days",
                "monetization_model",
                "china_gap",
                "total_score",
            ):
                value = src.get(key)
                if value not in (None, ""):
                    lines.append(f"- {key}: {value}")
        lines.append("\n— 待评分的标题 —")
        lines.append(title or "(空)")
        lines.append("\n— 待评分的正文 —")
        lines.append(body_for_prompt)
        lines.append(
            "\n— 输出 —\n"
            "按 JSON Schema 返回 5 个 1-10 分 + 一句话 rationale。"
        )
        return "\n".join(lines)

    def _build_score(self, raw: Mapping[str, Any]) -> ContentQualityScore:
        weights = self.weights()
        dims = {
            "hook_strength": _coerce_float(raw.get("hook_strength")),
            "cta_naturalness": _coerce_float(raw.get("cta_naturalness")),
            "data_accuracy": _coerce_float(raw.get("data_accuracy")),
            "char_count_compliance": _coerce_float(raw.get("char_count_compliance")),
            "platform_style_match": _coerce_float(raw.get("platform_style_match")),
        }
        # Weighted total — weights already sum to 1.0 with the default
        # implementation; we normalise defensively in case a subclass
        # tweaks them.
        weight_sum = sum(weights.values()) or 1.0
        total = sum(dims[k] * weights.get(k, 0.0) for k in _DIMENSION_NAMES) / weight_sum
        total = round(total, 2)

        rationale = str(raw.get("rationale") or "").strip()
        if len(rationale) > 60:
            rationale = rationale[:60] + "…"

        below = (
            total < self.threshold
            or any(dims[k] < self.dimension_floor for k in _DIMENSION_NAMES)
        )
        return ContentQualityScore(
            **dims,
            total=total,
            rationale=rationale,
            below_threshold=below,
            threshold_used=self.threshold,
            dimension_floor_used=self.dimension_floor,
        )


__all__ = [
    "ContentQualityScorer",
    "ContentQualityScore",
    "DEFAULT_THRESHOLD",
    "DEFAULT_DIMENSION_FLOOR",
]


# ---------------------------------------------------------------------------
# Suppress unused-import warnings under pyflakes — `field` / `Iterable`
# kept around for downstream subclasses / future fields.
_ = (field, Iterable)