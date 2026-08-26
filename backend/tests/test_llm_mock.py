"""Tests for the deterministic MockLLMProvider + factory selection."""

from __future__ import annotations

from app.services.llm import (
    LLMProvider,
    MockLLMProvider,
    build_llm_provider,
)


async def test_mock_provider_returns_a_dict():
    provider = MockLLMProvider()
    payload = await provider.complete_json(
        system="sys", user="AI sales coach for SDRs that summarises calls."
    )
    assert isinstance(payload, dict)


async def test_mock_provider_is_deterministic():
    provider = MockLLMProvider()
    user = "AI SaaS for sales teams that summarises calls in real time."
    p1 = await provider.complete_json(system="s", user=user)
    p2 = await provider.complete_json(system="s", user=user)
    assert p1 == p2


async def test_mock_provider_relevant_text_scores_high():
    provider = MockLLMProvider()
    payload = await provider.complete_json(
        system="s",
        user=(
            "title: AI Sales Coach\n"
            "summary: SaaS tool that helps founders monetise automation at scale. "
            "B2B platform with API, dashboard, growth analytics for enterprise."
        ),
    )
    assert payload["is_business_relevant"] is True
    assert payload["trend_strength"] > 50
    assert 0 <= payload["confidence"] <= 1.0


async def test_mock_provider_irrelevant_text_is_capped():
    provider = MockLLMProvider()
    payload = await provider.complete_json(
        system="s",
        user="Random poem about clouds and rainbows.",
    )
    # No business keywords → not relevant + capped scores.
    assert payload["is_business_relevant"] is False
    assert payload["trend_strength"] <= 35
    assert payload["demand_strength"] <= 35


async def test_mock_provider_handles_empty_input():
    provider = MockLLMProvider()
    payload = await provider.complete_json(system="s", user="")
    # Should still produce a valid response object.
    assert "is_business_relevant" in payload


async def test_mock_provider_batch_default_sequential():
    provider = MockLLMProvider()
    results = await provider.complete_json_batch(
        system="s",
        users=["AI sales tool", "AI sales tool", "Random text"],
    )
    assert len(results) == 3
    assert results[0] == results[1]  # deterministic


def test_factory_returns_mock_when_mocking_enabled(settings):
    # conftest sets MOCK_EXTERNAL_SERVICES=true via setdefault.
    settings.mock_external_services = True
    provider = build_llm_provider(settings)
    assert isinstance(provider, MockLLMProvider)


def test_factory_returns_mock_when_no_keys_present(settings):
    settings.mock_external_services = False
    settings.MiniMax_api_key = ""
    settings.openai_api_key = ""
    settings.anthropic_api_key = ""
    settings.gemini_api_key = ""
    provider = build_llm_provider(settings)
    assert isinstance(provider, MockLLMProvider)


def test_factory_respects_provider_choice(settings):
    settings.mock_external_services = False
    settings.MiniMax_api_key = ""  # no key → still mock
    provider = build_llm_provider(settings, prefer="MiniMax")
    assert isinstance(provider, MockLLMProvider)


def test_factory_selects_MiniMax_by_default(settings):
    """When `LLM_DEFAULT_PROVIDER=MiniMax` (the project default) and a key
    is present, `build_llm_provider` should return a `MiniMaxLLMProvider`."""
    from app.services.llm import MiniMaxLLMProvider

    settings.mock_external_services = False
    settings.MiniMax_api_key = "fake-test-key"
    settings.llm_default_provider = "MiniMax"
    provider = build_llm_provider(settings)
    assert isinstance(provider, MiniMaxLLMProvider)
    assert provider.name == "MiniMax"


def test_factory_prefers_MiniMax_when_prefer_arg_set(settings):
    from app.services.llm import MiniMaxLLMProvider

    settings.mock_external_services = False
    settings.MiniMax_api_key = "fake-test-key"
    settings.openai_api_key = "fake-openai"
    provider = build_llm_provider(settings, prefer="MiniMax")
    assert isinstance(provider, MiniMaxLLMProvider)


def test_MiniMax_provider_requires_api_key():
    """Empty key MUST raise before any network call."""
    from app.services.llm import MiniMaxLLMProvider
    from app.utils import ValidationError

    try:
        MiniMaxLLMProvider(api_key="", default_model="glm-4.7")
    except ValidationError as exc:
        assert "api_key" in str(exc).lower()
    else:
        raise AssertionError("MiniMaxLLMProvider should reject empty api_key")


def test_MiniMax_provider_uses_configured_base_url(settings):
    """The provider MUST honour Settings.MiniMax_api_url — no hard-coded URL."""
    from app.services.llm import build_MiniMax_provider

    settings.MiniMax_api_key = "fake-test-key"
    settings.MiniMax_api_url = "https://api.example.test/v1"
    provider = build_MiniMax_provider(settings)
    assert provider.api_url == "https://api.example.test/v1"


def test_provider_is_an_abc_subclass():
    assert issubclass(MockLLMProvider, LLMProvider)
    assert MockLLMProvider.name == "mock"


def test_response_has_all_required_keys():
    """Ensure the mock covers every key the schema requires."""
    import asyncio

    provider = MockLLMProvider()
    payload = asyncio.run(
        provider.complete_json(
            system="s",
            user="AI SaaS product for sales reps at b2b companies — API, dashboard.",
        )
    )
    required = {
        "is_business_relevant",
        "category",
        "problem",
        "potential_business",
        "trend_strength",
        "demand_strength",
        "monetization_potential",
        "competition_gap",
        "china_gap",
        "execution_feasibility",
        "keywords",
        "confidence",
    }
    assert required.issubset(set(payload.keys()))
