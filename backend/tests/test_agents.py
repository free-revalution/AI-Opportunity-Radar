"""Tests for ``app.services.agents`` — Vertical Agent protocol + registry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.services.agents import (
    HeuristicContentRadarAgent,
    VerticalAgent,
    VerticalContext,
    VerticalResult,
    agents,
    get_agent,
    names,
    register,
    reset,
    try_get_agent,
)
from app.services.agents.base import VerticalAgent as ProtocolFromBase
from app.services.agents.content import _generate_angle, _generate_hook, _generate_title_candidates


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------------------------------------------------------------------------
# VerticalContext
# ---------------------------------------------------------------------------
class TestVerticalContext:
    def test_defaults(self):
        ctx = VerticalContext()
        assert ctx.platform == "general"
        assert ctx.language == "zh"
        assert ctx.audience == ""

    def test_extra_isolated(self):
        ctx1 = VerticalContext()
        ctx2 = VerticalContext()
        ctx1.extra["x"] = 1
        # Default-factory dict — each instance owns its own dict.
        assert "x" not in ctx2.extra


# ---------------------------------------------------------------------------
# HeuristicContentRadarAgent
# ---------------------------------------------------------------------------
def _signal(**kw):
    """Make a dict-like Signal for tests."""
    base = {
        "id": 1,
        "title": None,
        "summary": None,
        "signal_type": "trend",
        "keyword": "AI video tool",
        "category": "ai",
        "risk_score": 0.0,
    }
    base.update(kw)
    return base


def _report(confidence=0.7, sources=None):
    return {
        "confidence": confidence,
        "sources_json": {"urls": sources or ["https://example.com/a"]},
    }


class TestHeuristicContentRadarAgent:
    def test_satisfies_protocol(self):
        agent = HeuristicContentRadarAgent()
        # Runtime protocol check — confirms the shape matches the spec.
        assert isinstance(agent, ProtocolFromBase)

    def test_name_is_content(self):
        assert HeuristicContentRadarAgent().name == "content"

    def test_basic_analyze(self):
        agent = HeuristicContentRadarAgent()
        result = asyncio.run(
            agent.analyze(_signal(), VerticalContext(platform="xiaohongshu"))
        )
        assert result.vertical == "content"
        assert "title_candidates" in result.payload
        assert len(result.payload["title_candidates"]) == 3
        assert "script_outline" in result.payload
        assert result.payload["platform"] == "xiaohongshu"

    def test_title_candidates_for_each_platform(self):
        agent = HeuristicContentRadarAgent()
        for platform in ("douyin", "xiaohongshu", "wechat", "general"):
            result = asyncio.run(
                agent.analyze(_signal(), VerticalContext(platform=platform))
            )
            assert len(result.payload["title_candidates"]) == 3

    def test_risk_warning_when_risk_score_high(self):
        agent = HeuristicContentRadarAgent()
        result = asyncio.run(
            agent.analyze(_signal(risk_score=0.6), VerticalContext())
        )
        assert "合规" in result.payload["risk_warning"]

    def test_risk_warning_when_low_confidence_report(self):
        agent = HeuristicContentRadarAgent()
        result = asyncio.run(
            agent.analyze(
                _signal(), VerticalContext(), report=_report(confidence=0.3)
            )
        )
        assert "置信度" in result.payload["risk_warning"]

    def test_default_risk_warning(self):
        agent = HeuristicContentRadarAgent()
        result = asyncio.run(
            agent.analyze(_signal(), VerticalContext())
        )
        assert result.payload["risk_warning"]

    def test_recommended_length_per_platform(self):
        agent = HeuristicContentRadarAgent()
        for plat, expected in (
            ("douyin", 60),
            ("xiaohongshu", 200),
            ("bilibili", 600),
            ("wechat", 2500),
            ("general", 800),
        ):
            r = asyncio.run(agent.analyze(_signal(), VerticalContext(platform=plat)))
            assert r.payload["recommended_length"] == expected

    def test_sources_extracted_from_report(self):
        agent = HeuristicContentRadarAgent()
        r = asyncio.run(
            agent.analyze(
                _signal(),
                VerticalContext(),
                report=_report(sources=["https://a.com", "https://b.com"]),
            )
        )
        assert r.sources_used == ["https://a.com", "https://b.com"]

    def test_confidence_scales_with_signal_richness(self):
        agent = HeuristicContentRadarAgent()
        sparse = asyncio.run(agent.analyze({"signal_type": "trend"}, VerticalContext()))
        rich = asyncio.run(
            agent.analyze(
                _signal(title="X", summary="Y", keyword="k", source_count=3),
                VerticalContext(),
            )
        )
        assert rich.confidence > sparse.confidence
        assert rich.confidence <= 0.7  # heuristic cap

    def test_accepts_orm_signal(self):
        @dataclass
        class Sig:
            id: int = 1
            title: str = "AI video trend"
            summary: str = "Multiple sources report a sudden uptick"
            signal_type: str = "trend"
            keyword: str = "ai video"
            risk_score: float = 0.0

        agent = HeuristicContentRadarAgent()
        result = asyncio.run(agent.analyze(Sig(), VerticalContext()))
        assert result.payload["title"] == "AI video trend"

    def test_payload_status_starts_as_draft(self):
        agent = HeuristicContentRadarAgent()
        r = asyncio.run(agent.analyze(_signal(), VerticalContext()))
        assert r.payload["status"] == "draft"

    def test_hook_uses_summary_first_sentence(self):
        agent = HeuristicContentRadarAgent()
        r = asyncio.run(
            agent.analyze(
                _signal(summary="多家媒体报道了这件事。原因是 XX"),
                VerticalContext(),
            )
        )
        assert r.payload["hook"].startswith("多家媒体")

    def test_title_candidate_helper_keywords_present(self):
        titles = _generate_title_candidates(
            keyword="AI video", summary="x", signal_type="trend", platform="douyin"
        )
        assert any("AI video" in t for t in titles)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class TestRegistry:
    def setup_method(self):
        reset()

    def test_content_agent_registered_by_default(self):
        assert "content" in names()
        assert try_get_agent("content") is not None

    def test_strict_lookup_unknown_raises(self):
        with pytest.raises(KeyError):
            get_agent("sales")

    def test_lenient_lookup_unknown(self):
        assert try_get_agent("sales") is None

    def test_register_custom(self):
        class CustomAgent:
            name = "custom"
            async def analyze(self, signal, context, *, report=None):
                return VerticalResult(
                    vertical="custom",
                    opportunity_id=None,
                    payload={},
                )

        register(CustomAgent())
        assert "custom" in names()
        assert get_agent("custom").name == "custom"

    def test_register_duplicate_raises(self):
        class A:
            name = "dup"
            async def analyze(self, *a, **k): return VerticalResult(vertical="dup", opportunity_id=None, payload={})

        register(A())
        with pytest.raises(ValueError):
            register(A())

    def test_register_empty_name_raises(self):
        class A:
            name = ""
            async def analyze(self, *a, **k): return VerticalResult(vertical="x", opportunity_id=None, payload={})

        with pytest.raises(ValueError):
            register(A())

    def test_agents_returns_all(self):
        before = len(list(agents()))
        register(type("X", (), {
            "name": "y",
            "analyze": lambda self, *a, **k: asyncio.sleep(0, result=VerticalResult(vertical="y", opportunity_id=None, payload={})),
        })())
        after = len(list(agents()))
        assert after == before + 1

    def test_reset_clears_then_reinstalls_defaults(self):
        assert "content" in names()
        # Manually blow it away:
        from app.services.agents.registry import _registry
        _registry._agents.clear()
        assert names() == []
        # reset() restores defaults.
        reset()
        assert "content" in names()