"""WeChat Official Account article generator — long-form Markdown.

公众号 articles differ from `daily_report.py` in five ways:

  1. Magazine-voice, story-driven — not a section-of-fields report.
  2. 1500-3000 字 with H2/H3 hierarchy + image placeholders.
  3. Three CTA placeholders at the tail for the distribution layer
     (公众号 editor flow = LLM 写正文 → 人工贴二维码/微信号/链接 →
     公众号后台配图 → 发布).
  4. First-person POV with opinions and stories.
  5. Title with a numeric hook (e.g. "我用 14 天复刻了一个月入 5 万
     美元的 AI 项目").

Why Markdown and not JSON: 公众号 editor does not support structured
import — the operator copy-pastes into微信公众平台编辑器 which
accepts rich Markdown. Any structured data (image suggestions, CTA
slots) goes into `metadata`, not into `content`.

This is intentionally NOT a re-skin of `daily_report.py` — different
字数, different 视角, different 结构, different CTA. Sharing only the
field list (which opportunity + report fields feed the user prompt).

Phase 9 — DRY cleanup: `_extract_text` / `_extract_title` /
`_enforce_image_placeholders` come from `base.py`. Channel-specific
logic that's still local:

  * `_cta_block_present()` — the three-CTA check is unique to
    公众号 (other channels have a single CTA marker), so it stays
    inline rather than going into base.
"""

from __future__ import annotations

from typing import Any

from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
    base_metadata,
    ensure_section_placeholders,
    extract_text_from_llm,
    extract_title_from_body,
    register,
)
from app.services.llm.provider import LLMProvider
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants — shared with the distribution layer (Content Center, export).
# ---------------------------------------------------------------------------
REQUIRED_CTA_PLACEHOLDERS: tuple[str, ...] = (
    "{{WECHAT_QR}}",
    "{{WECHAT_ID}}",
    "{{KNOWLEDGE_PLANET_URL}}",
)

MIN_CHARS = 1500
TARGET_CHARS = 2200
MAX_CHARS = 3000
TITLE_MAX_CHARS = 26

# CTA block appended unconditionally. Never trust the LLM to remember.
_CTA_BLOCK = (
    "\n\n---\n\n"
    "扫码加我微信,围观我从0到1复刻的全过程:{{WECHAT_QR}}\n\n"
    "微信号:{{WECHAT_ID}}\n\n"
    "知识星球(每周更新海外 AI 项目拆解):{{KNOWLEDGE_PLANET_URL}}\n"
)


def _all_cta_placeholders_present(body: str) -> bool:
    """Three-CTA check — every placeholder must appear or we
    re-append the block. The single-marker `append_block_if_missing`
    helper would append for each missing one (3× duplication); this
    channel needs a single `all-or-nothing` rule."""
    return all(marker in body for marker in REQUIRED_CTA_PLACEHOLDERS)


class WechatArticleGenerator(ContentGenerator):
    """Markdown 公众号长文 — 1500-3000 字,数字钩子标题,文末 CTA 三件套."""

    name = "wechat_article"
    channel = "wechat_article"
    format = "markdown"
    description = "微信公众号长文 — 1500-3000 字,数字钩子标题,文末 CTA 三件套"

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
            max_tokens=4096,  # 公众号长文必须给足
        )
        body = extract_text_from_llm(raw)
        body = ensure_section_placeholders(body)
        body = self._enforce_cta(body)

        char_count = len(body)
        if char_count < MIN_CHARS:
            # 软警告,不 raise — 短文也能用,只是运营可能想重跑
            logger.warning(
                "wechat_article_too_short",
                opportunity_id=getattr(opportunity, "id", None),
                chars=char_count,
                min_chars=MIN_CHARS,
            )

        title, title_truncated = extract_title_from_body(
            body, max_chars=TITLE_MAX_CHARS
        )
        if not title:
            title = (
                f"我用 AI 复刻了一个月入 5 万美元的"
                f"{getattr(opportunity, 'category', None) or '海外项目'}"
            )

        meta = base_metadata(opportunity)
        meta.update(
            {
                "char_count": char_count,
                "cta_placeholders": list(REQUIRED_CTA_PLACEHOLDERS),
                "image_placeholders": self._count_image_placeholders(body),
                "read_minutes": max(1, char_count // 400),
                "title_truncated": title_truncated,
            }
        )
        return GeneratedContent(
            opportunity_id=int(opportunity.id),
            generator=self.name,
            channel=self.channel,
            title=title,
            format=self.format,
            content=body,
            metadata=meta,
        )

    # ----- prompts ---------------------------------------------------
    def system_prompt(self) -> str:
        return (
            "你是一名中文公众号头部作者,擅长把海外 AI 项目写成"
            "'我亲眼看到一个赚钱机会'的故事型长文。\n\n"
            "目标读者:中国互联网圈的个人开发者和独立创业者,40 岁以下,"
            "想找副业 / 创业方向。\n\n"
            "写作硬规则(不达标 = 重写):\n"
            f"* 总字数 {MIN_CHARS}-{MAX_CHARS} 字,目标 {TARGET_CHARS}\n"
            "* 第一行(去掉 `# ` 前缀后)就是标题:14-26 字,"
            "必须有数字钩子或反差钩子\n"
            "  例如 '我用 14 天复刻了一个月入 5 万美元的 AI 项目'\n"
            "* 开头 hook 段(标题之后第一段):2-3 句,以场景 / 故事 / "
            "反差数据切入,不要写'今天给大家分享'\n"
            "* 主体 3-5 个 H2 小节(`##` 开头),每节 200-400 字,"
            "可用 H3 子节\n"
            "* 段落短,3-5 行,关键判断 / 数字用 `**...**` 加粗\n"
            "* 每节配 1 张图占位符:`![配图-N]({{IMAGE_N}})`,N 从 1 开始递增\n"
            "* 第一人称,有观点,有故事性;不要堆 emoji;"
            "不要'颠覆/革命/划时代/王炸/炸裂'\n\n"
            "文末固定三件套 CTA(我会自动追加,你自己也可以写):\n"
            "```\n"
            "扫码加我微信,围观我从0到1复刻的全过程:{{WECHAT_QR}}\n"
            "微信号:{{WECHAT_ID}}\n"
            "知识星球(每周更新海外 AI 项目拆解):{{KNOWLEDGE_PLANET_URL}}\n"
            "```\n\n"
            "结构(自由发挥顺序,但都要覆盖):\n"
            "* Hook(故事 / 反差 / 痛点场景)\n"
            "* 国外项目是什么,谁在用,赚多少钱\n"
            "* 为什么中国没有人做 — 切入点 + 中国市场空白\n"
            "* 个人开发者怎么上手 — MVP 几天、技术栈、首发渠道、定价\n"
            "* 风险 / 提醒 — 什么情况下别做\n\n"
            "输出语言:简体中文,不要任何开场白、不要解释、不要 markdown "
            "fence 包裹整篇文章。"
        )

    def response_schema(self) -> dict[str, Any] | None:
        return None

    # ----- helpers ---------------------------------------------------
    @classmethod
    def _enforce_cta(cls, body: str) -> str:
        """Append the canonical three-piece CTA block. Never trust the
        model — and never duplicate if all three placeholders are
        already present (e.g. the LLM echoed them back verbatim)."""
        if _all_cta_placeholders_present(body):
            return body
        return body.rstrip() + _CTA_BLOCK

    @staticmethod
    def _count_image_placeholders(body: str) -> int:
        import re as _re

        return len(_re.findall(r"!\[\s*配图-\d+\s*\]\(\{\{IMAGE_\d+\}\}\)", body))


register(WechatArticleGenerator())


__all__ = [
    "WechatArticleGenerator",
    "REQUIRED_CTA_PLACEHOLDERS",
    "MIN_CHARS",
    "TARGET_CHARS",
    "MAX_CHARS",
]
