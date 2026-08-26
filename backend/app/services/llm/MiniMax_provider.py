"""MiniMax(智谱 GLM)LLM provider.

MiniMax 提供 OpenAI 兼容的 Chat Completions 端点,本 provider 直接复用
官方 `openai` SDK,只改 `base_url` + `api_key`,不加新依赖。

可用模型(写入 Settings 后由运营配置覆盖):
  * `glm-4.7`       — strong,深度研究 / 评分
  * `glm-4-air`     — mid
  * `glm-4-flash`   — cheap,筛选 / 摘要
  * `embedding-2`    — 嵌入(独立端点;`embedding.py` 处理)

约束:必须返回 JSON 对象,否则算作 `ValidationError`。
任何传输 / 鉴权 / 5xx 故障翻译为 `ExternalServiceError`,
以便 screening / scoring 服务的重试逻辑保持统一。
"""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.services.llm.provider import LLMProvider
from app.utils import ExternalServiceError, ValidationError, get_logger

logger = get_logger(__name__)


class MiniMaxLLMProvider(LLMProvider):
    """JSON-mode chat completion via MiniMax's OpenAI-compatible API."""

    name = "MiniMax"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        api_url: str = "https://api.MiniMax.cn/v1",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValidationError("MiniMaxLLMProvider requires an api_key")
        self.api_key = api_key
        self.default_model = default_model
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        # Lazy import — openai SDK 很重,测试不需要它。
        import openai  # type: ignore[import-not-found]

        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=self.api_url,
            timeout=timeout,
        )

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        model_name = model or self.default_model
        schema_hint = ""
        if response_schema:
            schema_hint = (
                "\n\nReturn ONLY a JSON object that matches this schema:\n"
                + json.dumps(response_schema, indent=2)
            )
        messages = [
            {"role": "system", "content": system + schema_hint},
            {"role": "user", "content": user},
        ]
        try:
            response = await self._client.chat.completions.create(
                model=model_name,
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 — translate to AppError
            raise ExternalServiceError(
                f"MiniMax call failed: {exc}",
                provider="MiniMax",
                model=model_name,
            ) from exc

        try:
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
        except (IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                "MiniMax returned non-JSON response",
                provider="MiniMax",
                raw_excerpt=(content[:200] if "content" in locals() else ""),
            ) from exc

        if not isinstance(payload, dict):
            raise ValidationError(
                "MiniMax response was not a JSON object",
                provider="MiniMax",
            )
        return payload


def build_MiniMax_provider(settings: Settings) -> MiniMaxLLMProvider:
    """Convenience builder — reads config defaults."""
    return MiniMaxLLMProvider(
        api_key=settings.MiniMax_api_key,
        default_model=settings.MiniMax_model_strong,
        api_url=settings.MiniMax_api_url,
    )


__all__ = ["MiniMaxLLMProvider", "build_MiniMax_provider"]
