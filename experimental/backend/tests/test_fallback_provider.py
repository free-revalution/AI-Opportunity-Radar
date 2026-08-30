"""Tests for the Phase 11 FallbackWebDataProvider composite."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.research.fallback_provider import FallbackWebDataProvider
from app.services.research.web_data import SourceDoc, WebDataProvider
from app.utils import ExternalServiceError


class _StubProvider(WebDataProvider):
    """Tiny stand-in — no HTTP, full control over behaviour per test."""

    def __init__(
        self,
        name: str,
        *,
        search_result: list[SourceDoc] | None = None,
        search_error: Exception | None = None,
        scrape_result: SourceDoc | None = None,
        scrape_error: Exception | None = None,
    ) -> None:
        self.name = name
        self._search_result = search_result if search_result is not None else []
        self._search_error = search_error
        self._scrape_result = scrape_result or SourceDoc(
            url="", title="", content="", via_provider=name
        )
        self._scrape_error = scrape_error
        self.search_calls = 0
        self.scrape_calls = 0

    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        self.search_calls += 1
        if self._search_error is not None:
            raise self._search_error
        return list(self._search_result)

    async def scrape(self, url: str) -> SourceDoc:
        self.scrape_calls += 1
        if self._scrape_error is not None:
            raise self._scrape_error
        return self._scrape_result


def _doc(url: str, provider: str = "p") -> SourceDoc:
    return SourceDoc(url=url, title=url, content="x", via_provider=provider)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError):
        FallbackWebDataProvider([])


def test_name_is_fallback() -> None:
    composite = FallbackWebDataProvider([_StubProvider("a")])
    assert composite.name == "fallback"


def test_chain_property_exposes_provider_names() -> None:
    composite = FallbackWebDataProvider(
        [_StubProvider("browser_use"), _StubProvider("firecrawl")]
    )
    assert composite.chain == ["browser_use", "firecrawl"]


# ---------------------------------------------------------------------------
# search() fallback behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_first_non_empty_result() -> None:
    a = _StubProvider("a", search_result=[_doc("https://a")])
    b = _StubProvider("b", search_result=[_doc("https://b")])
    composite = FallbackWebDataProvider([a, b])
    docs = await composite.search("q")
    assert [d.url for d in docs] == ["https://a"]
    assert a.search_calls == 1
    assert b.search_calls == 0


@pytest.mark.asyncio
async def test_search_falls_through_on_external_service_error() -> None:
    a = _StubProvider(
        "a",
        search_error=ExternalServiceError(
            "boom", provider="a", operation="search"
        ),
    )
    b = _StubProvider("b", search_result=[_doc("https://b", provider="b")])
    composite = FallbackWebDataProvider([a, b])
    docs = await composite.search("q")
    assert [d.url for d in docs] == ["https://b"]
    assert a.search_calls == 1 and b.search_calls == 1


@pytest.mark.asyncio
async def test_search_falls_through_on_empty_first_result() -> None:
    """Empty isn't an error — try the next provider for richer data."""
    a = _StubProvider("a", search_result=[])
    b = _StubProvider("b", search_result=[_doc("https://b")])
    composite = FallbackWebDataProvider([a, b])
    docs = await composite.search("q")
    assert [d.url for d in docs] == ["https://b"]


@pytest.mark.asyncio
async def test_search_returns_empty_when_all_providers_empty() -> None:
    a = _StubProvider("a", search_result=[])
    b = _StubProvider("b", search_result=[])
    composite = FallbackWebDataProvider([a, b])
    assert await composite.search("q") == []


@pytest.mark.asyncio
async def test_search_all_providers_fail_reraises_last_error() -> None:
    last = ExternalServiceError(
        "second boom", provider="b", operation="search"
    )
    a = _StubProvider(
        "a",
        search_error=ExternalServiceError(
            "first boom", provider="a", operation="search"
        ),
    )
    b = _StubProvider("b", search_error=last)
    composite = FallbackWebDataProvider([a, b])
    with pytest.raises(ExternalServiceError) as exc:
        await composite.search("q")
    assert exc.value.__cause__ is last
    assert exc.value.context.get("chain") == ["a", "b"]
    assert "chain=a -> b" in str(exc.value)


# ---------------------------------------------------------------------------
# scrape() fallback behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_falls_through_on_external_service_error() -> None:
    expected = _doc("https://b", provider="b")
    a = _StubProvider(
        "a",
        scrape_error=ExternalServiceError(
            "boom", provider="a", operation="scrape", url="x"
        ),
    )
    b = _StubProvider("b", scrape_result=expected)
    composite = FallbackWebDataProvider([a, b])
    doc = await composite.scrape("https://b")
    assert doc is expected
    assert a.scrape_calls == 1 and b.scrape_calls == 1


@pytest.mark.asyncio
async def test_scrape_first_success_wins() -> None:
    expected = _doc("https://a", provider="a")
    a = _StubProvider("a", scrape_result=expected)
    b = _StubProvider("b", scrape_result=_doc("https://b"))
    composite = FallbackWebDataProvider([a, b])
    doc = await composite.scrape("https://x")
    assert doc is expected
    assert b.scrape_calls == 0


@pytest.mark.asyncio
async def test_scrape_all_fail_raises_with_chain_context() -> None:
    a = _StubProvider(
        "a",
        scrape_error=ExternalServiceError(
            "boom", provider="a", operation="scrape", url="x"
        ),
    )
    b = _StubProvider(
        "b",
        scrape_error=ExternalServiceError(
            "second", provider="b", operation="scrape", url="x"
        ),
    )
    composite = FallbackWebDataProvider([a, b])
    with pytest.raises(ExternalServiceError) as exc:
        await composite.scrape("https://x")
    assert exc.value.context.get("chain") == ["a", "b"]


@pytest.mark.asyncio
async def test_scrape_does_not_swallow_non_external_errors() -> None:
    """Only `ExternalServiceError` triggers fallback; other exceptions propagate."""

    class _Boom(RuntimeError):
        pass

    a = _StubProvider("a")  # default success
    b = _StubProvider("b")
    # Force `a` to raise something that's NOT an ExternalServiceError
    a._scrape_error = _Boom("nope")  # type: ignore[attr-defined]
    composite = FallbackWebDataProvider([a, b])
    with pytest.raises(_Boom):
        await composite.scrape("https://x")
