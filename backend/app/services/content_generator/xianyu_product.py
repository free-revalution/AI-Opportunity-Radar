"""Xianyu (闲鱼) product listing generator — structured JSON.

闲鱼 is China's dominant second-hand / digital-products marketplace.
The output is a single product listing the operator copy-pastes into
the app: title, description, selling points, suggested price. The
JSON shape is locked because the operator pastes it into a fixed
spreadsheet — never trust the model to invent field names.

Pricing policy: keep suggestions inside ¥39-¥99 for the digital-
product line ("100 个海外 AI 创业机会"). Higher prices belong to
定制报告, which is sold off-platform.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
    register,
)
from app.services.llm.provider import LLMProvider
from app.utils import get_logger

logger = get_logger(__name__)


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


class XianyuProductGenerator(ContentGenerator):
    name = "xianyu_product"
    channel = "xianyu"
    format = "json"
    description = "闲鱼商品 listing — JSON,人工复制发布"

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
        # Defensive: ensure required fields are present.
        for field in ("title", "description", "selling_points", "price"):
            if field not in payload:
                raise ValueError(
                    f"xianyu generator: model output missing {field!r}"
                )
        return GeneratedContent(
            opportunity_id=int(opportunity.id),
            generator=self.name,
            channel=self.channel,
            title=str(payload["title"]),
            format=self.format,
            content=payload,
            metadata={
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

    def user_prompt(self, *, opportunity: Any, report: Any | None) -> str:
        parts: list[str] = [f"机会:{opportunity.title}"]
        if getattr(opportunity, "summary", None):
            parts.append(f"摘要:{opportunity.summary}")
        if getattr(opportunity, "target_customer", None):
            parts.append(f"目标客户:{opportunity.target_customer}")
        if getattr(opportunity, "market_size", None):
            parts.append(f"市场规模:{opportunity.market_size}")
        if getattr(opportunity, "monetization_model", None):
            parts.append(f"商业模式:{opportunity.monetization_model}")
        if getattr(opportunity, "total_score", None):
            parts.append(f"评分:{opportunity.total_score}/100")
        if report is not None and getattr(report, "executive_summary", None):
            parts.append(f"\n深度研究:{report.executive_summary}")
        return "\n".join(parts)

    def response_schema(self) -> dict[str, Any]:
        return _XIANYU_SCHEMA

    def metadata_from_opportunity(self, opportunity: Any) -> dict[str, Any]:
        return {"score": float(getattr(opportunity, "total_score", 0.0) or 0.0)}


register(XianyuProductGenerator())


__all__ = ["XianyuProductGenerator", "_XIANYU_SCHEMA"]