"""Tests that the fallback chain emits one counter increment per step."""

from __future__ import annotations

from app.services.research.fallback_provider import FallbackWebDataProvider
from app.services.research.web_data import SourceDoc, WebDataProvider
from app.utils import ExternalServiceError
from tests._phase12_helpers import counter_value


class _OkProvider(WebDataProvider):
    name = "fake_ok"

    def __init__(self, docs: list[SourceDoc] | None = None) -> None:
        self._docs = docs or [
            SourceDoc(url="https://x", title="t", content="m", via_provider=self.name)
        ]

    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        return self._docs

    async def scrape(self, url: str) -> SourceDoc:
        return SourceDoc(url=url, title="t", content="m", via_provider=self.name)


class _BoomProvider(WebDataProvider):
    name = "fake_boom"

    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        raise ExternalServiceError(
            "down", provider=self.name, operation="search"
        )

    async def scrape(self, url: str) -> SourceDoc:
        raise ExternalServiceError(
            "down", provider=self.name, operation="scrape"
        )


class _EmptyProvider(WebDataProvider):
    name = "fake_empty"

    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        return []

    async def scrape(self, url: str) -> SourceDoc:
        raise NotImplementedError


async def test_fallback_records_success_for_winner() -> None:
    chain = FallbackWebDataProvider([_OkProvider()])
    labels = {
        "provider": "fake_ok",
        "op": "search",
        "outcome": "success",
        "chain": "fake_ok",
    }
    before = counter_value("radar_web_data_requests_total", labels)
    await chain.search("ai")
    after = counter_value("radar_web_data_requests_total", labels)
    assert after == before + 1


async def test_fallback_records_error_per_step_and_success_for_winner() -> None:
    chain = FallbackWebDataProvider([_BoomProvider(), _OkProvider()])
    err_labels = {
        "provider": "fake_boom",
        "op": "search",
        "outcome": "error",
        "chain": "fake_boom,fake_ok",
    }
    ok_labels = {
        "provider": "fake_ok",
        "op": "search",
        "outcome": "success",
        "chain": "fake_boom,fake_ok",
    }
    err_before = counter_value("radar_web_data_requests_total", err_labels)
    ok_before = counter_value("radar_web_data_requests_total", ok_labels)
    await chain.search("ai")
    err_after = counter_value("radar_web_data_requests_total", err_labels)
    ok_after = counter_value("radar_web_data_requests_total", ok_labels)
    assert err_after == err_before + 1
    assert ok_after == ok_before + 1


async def test_fallback_records_empty_outcome() -> None:
    chain = FallbackWebDataProvider([_EmptyProvider(), _OkProvider()])
    empty_labels = {
        "provider": "fake_empty",
        "op": "search",
        "outcome": "empty",
        "chain": "fake_empty,fake_ok",
    }
    ok_labels = {
        "provider": "fake_ok",
        "op": "search",
        "outcome": "success",
        "chain": "fake_empty,fake_ok",
    }
    empty_before = counter_value("radar_web_data_requests_total", empty_labels)
    ok_before = counter_value("radar_web_data_requests_total", ok_labels)
    await chain.search("ai")
    empty_after = counter_value("radar_web_data_requests_total", empty_labels)
    ok_after = counter_value("radar_web_data_requests_total", ok_labels)
    assert empty_after == empty_before + 1
    assert ok_after == ok_before + 1


async def test_fallback_chain_label_lists_all_providers_in_order() -> None:
    """`chain` label must contain every provider that could have been tried."""
    chain = FallbackWebDataProvider([_BoomProvider(), _BoomProvider(), _OkProvider()])
    await chain.search("ai")
    # Read the registry and check at least one sample line for the
    # successful step uses the full ordered chain label.
    from app.metrics import render

    text = render().decode()
    assert 'chain="fake_boom,fake_boom,fake_ok"' in text
