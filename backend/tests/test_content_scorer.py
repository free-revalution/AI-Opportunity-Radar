"""Phase 10 — ContentQualityScorer + quality endpoint tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.content_scorer import (
    DEFAULT_DIMENSION_FLOOR,
    DEFAULT_THRESHOLD,
    ContentQualityScorer,
    ContentQualityScore,
)
from app.services.llm.provider import LLMProvider


# ---------------------------------------------------------------------------
# Stub LLM
# ---------------------------------------------------------------------------
class _StubScorerLLM(LLMProvider):
    """Returns a fixed score envelope. Records every call so tests can
    assert prompt content."""

    name = "stub-scorer-llm"

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "hook_strength": 7.5,
            "cta_naturalness": 8.0,
            "data_accuracy": 5.0,
            "char_count_compliance": 9.0,
            "platform_style_match": 7.0,
            "rationale": "数据可以更具体",
        }
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "response_schema": response_schema,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        # Return a deep copy so the test can mutate freely.
        return json.loads(json.dumps(self.payload))


class _FailingLLM(LLMProvider):
    name = "failing-scorer-llm"

    async def complete_json(self, **_: Any) -> dict[str, Any]:
        raise RuntimeError("LLM down")


# ---------------------------------------------------------------------------
# Unit tests — scorer in isolation
# ---------------------------------------------------------------------------
class TestContentQualityScorerUnit:
    def test_default_threshold_constants(self) -> None:
        assert DEFAULT_THRESHOLD == 6.0
        assert DEFAULT_DIMENSION_FLOOR == 4.0

    @pytest.mark.asyncio
    async def test_score_computes_weighted_total(self) -> None:
        # All dimensions at 8.0 → total = 8.0 with default weights.
        llm = _StubScorerLLM(
            payload={
                "hook_strength": 8.0,
                "cta_naturalness": 8.0,
                "data_accuracy": 8.0,
                "char_count_compliance": 8.0,
                "platform_style_match": 8.0,
                "rationale": "ok",
            }
        )
        score = await ContentQualityScorer().score(
            notification_payload={
                "channel": "wechat_article",
                "title": "T",
                "body": "Body content here",
                "metadata": {},
            },
            llm=llm,
        )
        assert score.total == 8.0
        assert not score.below_threshold  # 8.0 >= 6.0 threshold

    @pytest.mark.asyncio
    async def test_score_below_threshold_when_total_low(self) -> None:
        llm = _StubScorerLLM(
            payload={
                "hook_strength": 3.0,
                "cta_naturalness": 4.0,
                "data_accuracy": 3.0,
                "char_count_compliance": 5.0,
                "platform_style_match": 4.0,
            }
        )
        score = await ContentQualityScorer().score(
            notification_payload={
                "channel": "xianyu",
                "title": "T",
                "body": "Body",
                "metadata": {},
            },
            llm=llm,
        )
        assert score.below_threshold
        # total ≈ (3+4+3+5+4)/5 = 3.8
        assert score.total < 5.0

    @pytest.mark.asyncio
    async def test_score_below_threshold_when_any_dimension_below_floor(self) -> None:
        llm = _StubScorerLLM(
            payload={
                "hook_strength": 9.0,
                "cta_naturalness": 9.0,
                "data_accuracy": 9.0,
                "char_count_compliance": 9.0,
                # platform_style_match drops below the 4.0 floor.
                "platform_style_match": 2.0,
            }
        )
        score = await ContentQualityScorer().score(
            notification_payload={
                "channel": "wechat_article",
                "title": "T",
                "body": "Body",
                "metadata": {},
            },
            llm=llm,
        )
        assert score.below_threshold
        # Total would otherwise be ≥ threshold; the single bad dim
        # flips the flag.

    @pytest.mark.asyncio
    async def test_score_clamps_out_of_range_values(self) -> None:
        llm = _StubScorerLLM(
            payload={
                "hook_strength": 99.0,  # out of range — clamp to 10
                "cta_naturalness": -3.0,  # clamp to 1
                "data_accuracy": 5.0,
                "char_count_compliance": 5.0,
                "platform_style_match": 5.0,
                "rationale": "x" * 200,  # truncated
            }
        )
        score = await ContentQualityScorer().score(
            notification_payload={
                "channel": "feishu",
                "title": "T",
                "body": "Body",
                "metadata": {},
            },
            llm=llm,
        )
        assert score.hook_strength == 10.0
        assert score.cta_naturalness == 1.0
        assert len(score.rationale) <= 63  # 60 + ellipsis
        assert score.rationale.endswith("…")

    @pytest.mark.asyncio
    async def test_score_short_circuits_on_empty_body(self) -> None:
        llm = _StubScorerLLM()
        score = await ContentQualityScorer().score(
            notification_payload={
                "channel": "feishu",
                "title": "T",
                "body": "",
                "metadata": {},
            },
            llm=llm,
        )
        assert score.total == 1.0
        assert score.below_threshold
        assert llm.calls == []  # no LLM call at all

    @pytest.mark.asyncio
    async def test_score_short_circuits_on_whitespace_body(self) -> None:
        llm = _StubScorerLLM()
        score = await ContentQualityScorer().score(
            notification_payload={
                "channel": "feishu",
                "title": "T",
                "body": "   \n\n  ",
                "metadata": {},
            },
            llm=llm,
        )
        assert score.total == 1.0
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_score_returns_neutral_on_llm_failure(self) -> None:
        score = await ContentQualityScorer().score(
            notification_payload={
                "channel": "feishu",
                "title": "T",
                "body": "Body",
                "metadata": {},
            },
            llm=_FailingLLM(),
        )
        # Soft-fail — neutral 5.0, below_threshold=False so we don't
        # trigger a retry loop on a scoring failure.
        assert score.total == 5.0
        assert not score.below_threshold
        assert "异常" in score.rationale or "跳过" in score.rationale

    @pytest.mark.asyncio
    async def test_score_prompt_includes_channel_style(self) -> None:
        llm = _StubScorerLLM()
        await ContentQualityScorer().score(
            notification_payload={
                "channel": "wechat_article",
                "title": "我用 14 天复刻了...",
                "body": "Body text here that is long enough to score.",
                "metadata": {},
            },
            llm=llm,
        )
        assert len(llm.calls) == 1
        prompt = llm.calls[0]["user"]
        assert "wechat_article" in prompt
        assert "第一人称" in prompt  # channel style desc for wechat
        # The body should be present.
        assert "Body text here" in prompt

    @pytest.mark.asyncio
    async def test_score_prompt_includes_source_opportunity_when_metadata_provided(
        self,
    ) -> None:
        llm = _StubScorerLLM()
        await ContentQualityScorer().score(
            notification_payload={
                "channel": "feishu",
                "title": "T",
                "body": "Body",
                "metadata": {
                    "source_opportunity": {
                        "market_size": "100M-500M USD",
                        "mvp_days": 21,
                        "monetization_model": "SaaS 49 USD/月",
                    }
                },
            },
            llm=llm,
        )
        prompt = llm.calls[0]["user"]
        assert "100M-500M USD" in prompt
        assert "SaaS 49 USD/月" in prompt
        assert "机会背景" in prompt

    @pytest.mark.asyncio
    async def test_score_prompt_uses_low_max_tokens(self) -> None:
        llm = _StubScorerLLM()
        await ContentQualityScorer().score(
            notification_payload={
                "channel": "feishu",
                "title": "T",
                "body": "Body",
                "metadata": {},
            },
            llm=llm,
        )
        # A score is small — cap tokens so the LLM doesn't waste them.
        assert llm.calls[0]["max_tokens"] <= 600

    @pytest.mark.asyncio
    async def test_score_truncates_oversized_body(self) -> None:
        llm = _StubScorerLLM()
        long_body = "x" * 8000
        await ContentQualityScorer().score(
            notification_payload={
                "channel": "feishu",
                "title": "T",
                "body": long_body,
                "metadata": {},
            },
            llm=llm,
        )
        prompt = llm.calls[0]["user"]
        # 8000-char body should be truncated to 4000 + ellipsis in the prompt.
        assert "x" * 4500 not in prompt
        assert "…" in prompt

    def test_score_dataclass_as_dict_round_trip(self) -> None:
        score = ContentQualityScore(
            hook_strength=7.0,
            cta_naturalness=8.0,
            data_accuracy=5.0,
            char_count_compliance=9.0,
            platform_style_match=7.0,
            total=7.2,
            rationale="ok",
        )
        d = score.as_dict()
        assert d["total"] == 7.2
        assert d["hook_strength"] == 7.0
        assert "below_threshold" in d
        assert "threshold_used" in d


# ---------------------------------------------------------------------------
# Endpoint tests — POST /content/{id}/quality
# ---------------------------------------------------------------------------
class TestQualityEndpoint:
    @pytest.mark.asyncio
    async def test_quality_returns_score_envelope(
        self, client, sqlite_engine
    ) -> None:
        from app.models import Notification
        from sqlalchemy import insert

        # Seed a notification with non-empty body so scoring runs.
        async with sqlite_engine.begin() as conn:
            await conn.execute(
                insert(Notification).values(
                    channel="wechat_article",
                    payload={
                        "generator": "wechat_article",
                        "opportunity_id": 1,
                        "title": "我用 14 天复刻了一个 AI 项目",
                        "body": "上个月刷 Reddit,看到一个叫 ScaleOps 的小团队...",
                        "format": "markdown",
                        "metadata": {"char_count": 1700},
                    },
                )
            )

        # Patch the LLM at the dep level by swapping build_llm_provider.
        from app.services import llm as _llm_module

        stub = _StubScorerLLM(
            payload={
                "hook_strength": 8.0,
                "cta_naturalness": 9.0,
                "data_accuracy": 7.0,
                "char_count_compliance": 8.0,
                "platform_style_match": 8.0,
                "rationale": "good",
            }
        )
        original = _llm_module.build_llm_provider
        _llm_module.build_llm_provider = lambda: stub  # type: ignore[assignment]
        try:
            r = client.post("/api/internal/content/1/quality")
        finally:
            _llm_module.build_llm_provider = original  # type: ignore[assignment]

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["notification_id"] == 1
        assert body["channel"] == "wechat_article"
        score = body["score"]
        assert score["hook_strength"] == 8.0
        assert score["total"] == 8.0
        assert not score["below_threshold"]

    @pytest.mark.asyncio
    async def test_quality_404_on_unknown_notification(self, client) -> None:
        r = client.post("/api/internal/content/9999/quality")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_quality_persists_when_requested(
        self, client, sqlite_engine
    ) -> None:
        from app.models import Notification
        from sqlalchemy import insert, select

        async with sqlite_engine.begin() as conn:
            await conn.execute(
                insert(Notification).values(
                    channel="xianyu",
                    payload={
                        "generator": "xianyu_product",
                        "opportunity_id": 1,
                        "title": "T",
                        "body": "Body content here",
                        "format": "json",
                        "metadata": {},
                    },
                )
            )

        from app.services import llm as _llm_module

        stub = _StubScorerLLM()
        original = _llm_module.build_llm_provider
        _llm_module.build_llm_provider = lambda: stub  # type: ignore[assignment]
        try:
            r = client.post(
                "/api/internal/content/1/quality", json={"persist": True}
            )
        finally:
            _llm_module.build_llm_provider = original  # type: ignore[assignment]

        assert r.status_code == 200, r.text

        # Reload and check the payload now contains the quality_score.
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(sqlite_engine, expire_on_commit=False)
        async with maker() as session:
            notif = (
                await session.execute(select(Notification).where(Notification.id == 1))
            ).scalars().first()
        assert notif is not None
        assert "quality_score" in notif.payload
        assert notif.payload["quality_score"]["total"] == pytest.approx(7.05, abs=0.5)

    @pytest.mark.asyncio
    async def test_quality_short_circuits_on_empty_body(self, client, sqlite_engine) -> None:
        from app.models import Notification
        from sqlalchemy import insert

        async with sqlite_engine.begin() as conn:
            await conn.execute(
                insert(Notification).values(
                    channel="feishu",
                    payload={
                        "generator": "daily_report",
                        "opportunity_id": 1,
                        "title": "T",
                        "body": "",
                        "format": "markdown",
                        "metadata": {},
                    },
                )
            )

        from app.services import llm as _llm_module

        stub = _StubScorerLLM()  # should not be called
        original = _llm_module.build_llm_provider
        _llm_module.build_llm_provider = lambda: stub  # type: ignore[assignment]
        try:
            r = client.post("/api/internal/content/1/quality")
        finally:
            _llm_module.build_llm_provider = original  # type: ignore[assignment]

        assert r.status_code == 200, r.text
        score = r.json()["score"]
        assert score["total"] == 1.0
        assert score["below_threshold"] is True
        assert len(stub.calls) == 0  # no LLM call on empty body