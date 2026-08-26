"""FallbackWebDataProvider — chain several WebDataProviders behind one.

Implements the project rule: *"如果 Browser Use 服务不可用：必须让系统
继续使用 Firecrawl"*. The composite tries each provider in order and
catches `ExternalServiceError` per attempt so a single failure never
takes down a research job. Search falls through on an empty result too,
because richer data often lives behind the next provider.

Public surface:
    FallbackWebDataProvider     the composite itself
    chain_from_settings         helper that materialises a chain from
                                app settings (used by the factory)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.metrics import (
    record_external_error,
    record_web_data_call,
)
from app.services.research.web_data import SourceDoc, WebDataProvider
from app.utils import ExternalServiceError, get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Step:
    """Internal record of one provider attempt. Used only for logging."""

    name: str
    error: str | None = None
    returned: int | None = None


class FallbackWebDataProvider(WebDataProvider):
    """Ordered composite — every `WebDataProvider` is fair game.

    Behaviour:

    * `search(query, limit=...)` — try each provider in turn. Move on when
      one raises `ExternalServiceError` **or** returns an empty list
      (empty isn't an error, but the next provider often has richer data).
    * `scrape(url)` — try each provider. Move on only on
      `ExternalServiceError`; success wins immediately.
    * If every step fails, the last error is re-raised wrapped with the
      chain context so the operator can see what was tried.

    The composite's `name = "fallback"` so `_mark_job_running` keeps
    recording a value on the ResearchJob. Callers that need the
    individual providers for richer telemetry should inspect `chain`.
    """

    name = "fallback"

    def __init__(self, providers: Sequence[WebDataProvider]) -> None:
        if not providers:
            raise ValueError("FallbackWebDataProvider requires >=1 provider")
        self._providers = list(providers)

    @property
    def chain(self) -> list[str]:
        """Ordered names of the providers in the chain."""
        return [getattr(p, "name", "?") for p in self._providers]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def search(self, query: str, *, limit: int = 5) -> list[SourceDoc]:
        last_error: ExternalServiceError | None = None
        chain = self.chain
        for provider in self._providers:
            pname = getattr(provider, "name", "?")
            try:
                docs = await provider.search(query, limit=limit)
            except ExternalServiceError as exc:
                record_web_data_call(pname, "search", "error", chain)
                record_external_error(pname, type(exc).__name__)
                logger.warning(
                    "web_fallback_search_step_failed",
                    provider=pname,
                    chain=chain,
                    error=str(exc),
                )
                last_error = exc
                continue
            if not docs:
                # Empty isn't an error — but try the next provider in case
                # it can produce richer results.
                record_web_data_call(pname, "search", "empty", chain)
                logger.info(
                    "web_fallback_search_empty",
                    provider=pname,
                    chain=chain,
                )
                continue
            record_web_data_call(pname, "search", "success", chain)
            return docs

        if last_error is not None:
            self._raise_chained(last_error)
        # All providers returned empty; surface as no results (the engine
        # already tolerates an empty list, but we want to log it).
        logger.warning("web_fallback_search_all_empty", chain=chain)
        return []

    async def scrape(self, url: str) -> SourceDoc:
        last_error: ExternalServiceError | None = None
        chain = self.chain
        for provider in self._providers:
            pname = getattr(provider, "name", "?")
            try:
                doc = await provider.scrape(url)
            except ExternalServiceError as exc:
                record_web_data_call(pname, "scrape", "error", chain)
                record_external_error(pname, type(exc).__name__)
                logger.warning(
                    "web_fallback_scrape_step_failed",
                    provider=pname,
                    chain=chain,
                    error=str(exc),
                    url=url,
                )
                last_error = exc
                continue
            record_web_data_call(pname, "scrape", "success", chain)
            return doc

        # Unreachable if the chain is non-empty — defend against None.
        if last_error is None:  # pragma: no cover
            raise ExternalServiceError(
                "fallback chain returned no providers",
                provider=self.name,
                operation="scrape",
                url=url,
            )
        self._raise_chained(last_error)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _raise_chained(self, last_error: ExternalServiceError) -> None:
        """Re-raise *last_error* with the chain context attached."""
        chain_str = " -> ".join(self.chain)
        original = str(last_error)
        new_message = f"{original} (chain={chain_str})"
        # `ExternalServiceError` is an `AppError` that accepts arbitrary
        # kwargs into `self.context`. Re-raise with the chain appended
        # without losing the original exception object.
        raise ExternalServiceError(
            new_message,
            **{
                **getattr(last_error, "context", {}),
                "chain": self.chain,
            },
        ) from last_error


__all__ = ["FallbackWebDataProvider"]
