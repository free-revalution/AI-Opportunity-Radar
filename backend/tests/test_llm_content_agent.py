"""Tests for ``LLMContentRadarAgent`` — Phase 13A.

Covers:

  * Registry: both ``"content"`` and ``"llm_content"`` present by default.
  * Factory: ``build_llm_content_agent`` constructs a configured agent.
  * No-provider fallback → heuristic path.
  * Provider returns valid JSON → LLM path used.
  * Provider raises → fallback with ``llm_call_failed:<ExcName>``.
  * Provider returns non-dict / missing required field → fallback.
  * Provider returns valid but compliance-blocked output → fallback.
  * Confidence cap (LLM ≤ 0.9, fallback ≤ 0.7).
  * Payload always carries ``platform/audience/niche/tone/status``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.agents import (
    HeuristicContentRadarAgent,
    LLMContentRadarAgent,
    VerticalContext,
    VerticalResult,
    build_llm_content_agent,
    names,
    try_get_agent,
)
from app.services.compliance import ComplianceService


# ---------------------------------------------------------------------------
# Tiny LLM stubs
# ---------------------------------------------------------------------------
class _FakeLLM:
    """Stub LLMProvider — returns ``payload`` from ``complete_json``."""

    name = "fake"

    def __init__(self, payload: Any, raise_exc: Exception | None = None) -> None:
        self.payload = payload
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema": response_schema,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.payload  # type: ignore[return-value]


class _ExplodingLLM(_FakeLLM):
    """Always raises."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(payload=None, raise_exc=exc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
VALID_PAYLOAD: dict[str, Any] = {
    "title": "3 分钟看懂 AI Agent 为何突然爆火",
    "hook": "本周 AI Agent 突然在多个社区同时出现 — 这件事为什么突然火了?",
    "content_angle": "从产品经理的视角,拆解 AI Agent 的商业机会与风险",
    "title_candidates": [
        "3 分钟看懂 AI Agent 为何突然爆火",
        "AI Agent 到底意味着什么?一文给你讲清楚",
        "深度拆解:AI Agent 背后的产业逻辑",
    ],
    "script_outline": (
        "- 开场 (5s): 3 分钟看懂 AI Agent 为何突然爆火\n"
        "- 背景 (10s): 本周 AI Agent 突然在多个社区同时出现\n"
        "- 分析 (10s): 为什么会发生、对产品经理意味着什么\n"
        "- 收尾 (5s): 一句话总结 + CTA"
    ),
    "material_ideas": [
        "数据图: AI Agent 搜索量趋势",
        "对比表: AI Agent 与去年同期变化",
    ],
    "cta": "关注公众号,持续追踪下一个风口",
    "risk_warning": "发布前请核对原始来源链接",
}


def _signal_dict(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": 1,
        "title": "AI Agent 多社区同时爆发",
        "summary": "本周 AI Agent 在多个社区同时出现,引发广泛关注",
        "keyword": "AI Agent",
        "signal_type": "trend",
        "source_count": 3,
        "signal_score": 78.0,
        "risk_score": 0.1,
    }
    base.update(overrides)
    return base


def _ctx(**overrides: Any) -> VerticalContext:
    base = dict(
        platform="douyin",
        audience="产品经理",
        niche="AI 工具",
        tone="通俗",
    )
    base.update(overrides)
    return VerticalContext(**base)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Registry / factory
# ---------------------------------------------------------------------------
class TestRegistryAndFactory:
    def test_llm_content_registered_by_default(self):
        names_list = names()
        assert "llm_content" in names_list
        assert "content" in names_list

    def test_llm_content_lookup(self):
        agent = try_get_agent("llm_content")
        assert isinstance(agent, LLMContentRadarAgent)

    def test_factory_returns_fresh_instance(self):
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        assert isinstance(agent, LLMContentRadarAgent)
        assert agent.provider is fake
        assert agent.compliance_service is None
        assert agent.fallback is not None

    def test_factory_with_compliance(self):
        fake = _FakeLLM(VALID_PAYLOAD)
        svc = ComplianceService()
        agent = build_llm_content_agent(provider=fake, compliance_service=svc)
        assert agent.compliance_service is svc


# ---------------------------------------------------------------------------
# No-provider fallback
# ---------------------------------------------------------------------------
class TestNoProviderFallback:
    def test_provider_none_returns_heuristic_shape(self):
        agent = LLMContentRadarAgent(provider=None)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert result.vertical == "llm_content"
        # Fallback rationale tagged with reason
        assert "llm_fallback:no_provider_configured" in result.rationale
        # Fallback payload structure (heuristic)
        assert result.payload["platform"] == "douyin"
        assert result.payload["audience"] == "产品经理"
        assert result.payload["status"] == "draft"

    def test_fallback_confidence_capped_at_0_7(self):
        agent = LLMContentRadarAgent(provider=None)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert result.confidence <= 0.7


# ---------------------------------------------------------------------------
# Happy path — LLM returns valid payload
# ---------------------------------------------------------------------------
class TestLLMHappyPath:
    def test_uses_llm_payload(self):
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert "LLM Content Radar projection" in result.rationale
        assert "llm_fallback" not in result.rationale
        assert result.payload["title"] == VALID_PAYLOAD["title"]
        assert result.payload["hook"] == VALID_PAYLOAD["hook"]
        assert result.payload["title_candidates"] == VALID_PAYLOAD["title_candidates"]

    def test_payload_carries_user_context(self):
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert result.payload["platform"] == "douyin"
        assert result.payload["audience"] == "产品经理"
        assert result.payload["niche"] == "AI 工具"
        assert result.payload["tone"] == "通俗"
        assert result.payload["status"] == "draft"

    def test_passes_schema_and_prompt_to_provider(self):
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake, model="test-model", max_tokens=2048)
        _run(agent.analyze(_signal_dict(), _ctx()))
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["schema"] is not None
        assert call["schema"]["required"]  # non-empty required array
        assert call["model"] == "test-model"
        assert call["max_tokens"] == 2048
        # Prompt contains UNTRUSTED marker
        assert "UNTRUSTED_SOURCE_CONTENT" in call["user"]
        assert call["system"].startswith("你是一个\"内容雷达\"AI")

    def test_confidence_capped_at_0_9(self):
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        # Maximum source richness
        sig = _signal_dict(source_count=5)
        result = _run(agent.analyze(sig, _ctx()))
        assert 0.0 <= result.confidence <= 0.9


# ---------------------------------------------------------------------------
# Provider failure → fallback
# ---------------------------------------------------------------------------
class TestProviderFailure:
    def test_provider_raises_falls_back(self):
        boom = _ExplodingLLM(RuntimeError("upstream down"))
        agent = build_llm_content_agent(provider=boom)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert "llm_fallback:llm_call_failed:RuntimeError" in result.rationale
        assert result.payload["status"] == "draft"  # heuristic payload
        # No leak of LLM-only fields that heuristic wouldn't emit
        assert "compliance" not in result.payload

    def test_provider_returns_non_dict_falls_back(self):
        fake = _FakeLLM(payload=["not", "a", "dict"])  # type: ignore[arg-type]
        agent = build_llm_content_agent(provider=fake)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert "llm_fallback:llm_non_dict_response" in result.rationale

    def test_provider_missing_required_field_falls_back(self):
        bad = dict(VALID_PAYLOAD)
        del bad["title"]
        fake = _FakeLLM(bad)
        agent = build_llm_content_agent(provider=fake)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert "llm_fallback:llm_shape_validation_failed" in result.rationale

    def test_provider_empty_title_candidates_falls_back(self):
        bad = dict(VALID_PAYLOAD, title_candidates=[])
        fake = _FakeLLM(bad)
        agent = build_llm_content_agent(provider=fake)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert "llm_fallback:llm_shape_validation_failed" in result.rationale


# ---------------------------------------------------------------------------
# Compliance gate
# ---------------------------------------------------------------------------
class TestComplianceGate:
    def test_blocked_output_falls_back(self):
        svc = ComplianceService()
        # Payload containing financial advice → compliance blocks
        bad_payload = dict(VALID_PAYLOAD)
        bad_payload["script_outline"] = (
            "强烈推荐买入,目标价 100 元,保证翻倍收益,稳赚不赔"
        )
        fake = _FakeLLM(bad_payload)
        agent = build_llm_content_agent(provider=fake, compliance_service=svc)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert "llm_fallback:compliance_blocked" in result.rationale

    def test_clean_output_passes_compliance(self):
        svc = ComplianceService()
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake, compliance_service=svc)
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert "compliance" in result.payload
        assert result.payload["compliance"]["allowed"] is True
        assert "LLM Content Radar projection" in result.rationale


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_signal_as_orm_like_object(self):
        """Accept ORM-style objects with attribute access, not just dicts."""

        class _Signal:
            id = 1
            title = "title"
            summary = "summary"
            keyword = "kw"
            signal_type = "trend"
            source_count = 2
            signal_score = 60.0
            risk_score = 0.0

        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        result = _run(agent.analyze(_Signal(), _ctx()))
        assert "UNTRUSTED_SOURCE_CONTENT" in fake.calls[0]["user"]
        assert "title" in result.payload

    def test_long_string_gets_truncated_in_user_prompt(self):
        big_summary = "x" * 5000
        sig = _signal_dict(summary=big_summary)
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        _run(agent.analyze(sig, _ctx()))
        user_prompt = fake.calls[0]["user"]
        # signal_summary is clamped to 1000 chars in the prompt
        assert "x" * 1001 not in user_prompt
        assert big_summary[:1000] in user_prompt

    def test_empty_summary_handled(self):
        sig = _signal_dict(summary="")
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        result = _run(agent.analyze(sig, _ctx()))
        # Falls through cleanly — payload still has all fields
        assert result.payload["title"] == VALID_PAYLOAD["title"]
        assert "(no summary)" in fake.calls[0]["user"]

    def test_untrusted_marker_present_in_prompt(self):
        fake = _FakeLLM(VALID_PAYLOAD)
        agent = build_llm_content_agent(provider=fake)
        _run(agent.analyze(_signal_dict(), _ctx()))
        prompt = fake.calls[0]["user"]
        # Per docs §69 — UNTRUSTED_SOURCE_CONTENT marker required
        assert "[UNTRUSTED_SOURCE_CONTENT" in prompt
        assert "[END UNTRUSTED_SOURCE_CONTENT]" in prompt
        # User profile marker too
        assert "[USER_PROFILE]" in prompt

    def test_heuristic_baseline_still_runs(self):
        """Sanity — HeuristicContentRadarAgent still in registry and works."""
        agent = HeuristicContentRadarAgent()
        result = _run(agent.analyze(_signal_dict(), _ctx()))
        assert result.vertical == "content"
        assert result.payload["status"] == "draft"