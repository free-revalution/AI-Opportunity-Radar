"""Xianyu (闲鱼) product listing generator — structured JSON.

闲鱼 is China's dominant second-hand / digital-products marketplace.
The output is a single product listing the operator copy-pastes into
the app: title, description, selling points, suggested price. The
JSON shape is locked because the operator pastes it into a fixed
spreadsheet — never trust the model to invent field names.

Pricing policy: keep suggestions inside ¥39-¥99 for the digital-
product line ("100 个海外 AI 创业机会"). Higher prices belong to
定制报告, which is sold off-platform.

Phase 9 — DRY cleanup:
  * `metadata_from_opportunity` collapsed into the base
    `base_metadata` + a tiny channel-specific add-on.
  * `user_prompt` field walk picks up the tighter context-list
    pattern via the `opportunity_context_fields` class attribute
    (JSON-shape wants fewer fields — we only feed the LLM the bits
    that map to listing JSON keys).
  * Schema validation moved to a `validate_required_fields` helper
    so it can be reused by future JSON generators (Phase 11's
    Xiaohongshu Publisher payload, etc.).
"""

from __future__ import annotations

from typing import Any, Iterable

from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
    register,
)
from app.services.llm.provider import LLMProvider


_XIANYU_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "闲鱼商品标题,20-30 字以内,带数字更好",
        },
        "description": {
            "type": "string",
            "description": "Markdown 商品描述,200-400 字,3-5 个 bullet",
        },
        "selling_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-5 条卖点,每条不超过 20 字",
            "minItems": 3,
            "maxItems": 5,
        },
        "price": {
            "type": "integer",
            "description": "建议售价 (元),整型,39-99 之间",
            "minimum": 39,
            "maximum": 99,
        },
        "category": {
            "type": "string",
            "description": "闲鱼分类:虚拟商品 / 资料 / 报告",
        },
        "delivery_method": {
            "type": "string",
            "description": "交付方式:网盘链接 / 邮件 / 微信文件",
        },
    },
    "required": [
        "title",
        "description",
        "selling_points",
        "price",
        "category",
        "delivery_method",
    ],
}

# Top-level keys the operator pastes into a fixed spreadsheet.
# Promoted to a class attribute so `validate_required_fields` can
# re-run the check elsewhere if we ever wrap the listing in an
# envelope (e.g. for the bundle export format).
REQUIRED_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "selling_points",
    "price",
)


def validate_required_fields(
    payload: dict[str, Any], required: Iterable[str], *, generator_name: str
) -> None:
    """Defensive schema check for JSON-shape generators.

    The LLM spec enforces the schema, but providers occasionally
    drop a field on token truncation. We re-check here so the
    operator never sees half-rendered listings.
    """
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(
            f"{generator_name} generator: model output missing {missing!r}"
        )


class XianyuProductGenerator(ContentGenerator):
    name = "xianyu_product"
    channel = "xianyu"
    format = "json"
    description = "闲鱼商品 listing — JSON,人工复制发布"
    # JSON-shape — tighter context list. We only need the fields
    # that map to listing JSON keys (title / summary / target /
    # market / business model / score).
    opportunity_context_fields = (
        "title",
        "summary",
        "target_customer",
        "market_size",
        "monetization_model",
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
        payload = await llm.complete_json(
            system=self.system_prompt(),
            user=self.user_prompt(opportunity=opportunity, report=report),
            response_schema=self.response_schema(),
        )
        validate_required_fields(payload, REQUIRED_FIELDS, generator_name=self.name)
        return GeneratedContent(
            opportunity_id=int(opportunity.id),
            generator=self.name,
            channel=self.channel,
            title=str(payload["title"]),
            format=self.format,
            content=payload,
            metadata=self.metadata_from_opportunity(opportunity)
            | {
                "price_cny": int(payload.get("price", 49) or 49),
                "delivery_method": payload.get("delivery_method"),
                "category": payload.get("category"),
            },
        )

    # ----- prompts ---------------------------------------------------
    def system_prompt(self) -> str:
        return (
            "你是闲鱼(二手/数字商品平台)的爆款商品文案写手。\n\n"
            "你的输出必须是一个完整的 JSON 商品 listing,字段严格遵守"
            "用户提供的 JSON Schema,不要添加任何额外字段、不要 markdown "
            "fence、不要解释文字。\n\n"
            "定价策略:\n"
            "* 数字商品/资料类,默认 ¥49\n"
            "* 内容特别丰富或包含独家信息的,可调到 ¥79-¥99\n"
            "* 不超过 ¥99(高价商品走线下定制渠道,不在闲鱼卖)\n\n"
            "标题要求:\n"
            "* 20-30 字\n"
            "* 包含数字、年份、'海外'/'AI'/'创业'等关键词\n"
            "* 用空格分隔短语,不用符号\n"
            "* 例:'2026 海外 AI 创业机会 100 个 完整 PDF'\n\n"
            "卖点 3-5 条,每条 20 字以内,具体、有数字、避免'颠覆'类空话。"
        )

    def response_schema(self) -> dict[str, Any]:
        return _XIANYU_SCHEMA


register(XianyuProductGenerator())


__all__ = ["XianyuProductGenerator", "_XIANYU_SCHEMA", "REQUIRED_FIELDS"]
