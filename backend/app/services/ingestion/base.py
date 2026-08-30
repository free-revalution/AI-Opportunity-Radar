"""Base classes for source connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.services.ingestion.raw_item import RawItem


class SourceConnector(ABC):
    """All source connectors implement this interface.

    Contract:
        - `fetch()` returns a list of `RawItem`. Empty list is allowed.
        - `fetch()` MUST NOT raise on transient errors — instead, set
          `SourceConnectorResult.errors` and degrade gracefully.
        - `source` is a stable slug used as the foreign key into `sources`.

    Mocking:
        - If `mock=True` (or `MOCK_EXTERNAL_SERVICES=true` and no creds),
          subclasses return a fixture list instead of calling the network.
    """

    source: str = "unknown"
    requires_mock_without_keys: tuple[str, ...] = ()

    def __init__(self, *, mock: bool = False) -> None:
        self.mock = mock

    @abstractmethod
    async def fetch(self) -> "SourceConnectorResult":
        """Fetch a batch of items from this connector.

        Implementations return a `SourceConnectorResult` even on errors
        so the ingestion service can keep going with other connectors.
        """


class SourceConnectorResult:
    """Carries items + any non-fatal errors a connector encountered."""

    __slots__ = (
        "source",
        "items",
        "errors",
        "skipped_reason",
        "block_reason",
        "http_status",
    )

    def __init__(
        self,
        *,
        source: str,
        items: Sequence[RawItem] = (),
        errors: Sequence[str] = (),
        skipped_reason: str | None = None,
        block_reason: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.source = source
        self.items: list[RawItem] = list(items)
        self.errors: list[str] = list(errors)
        self.skipped_reason: str | None = skipped_reason
        # Phase 24 — when a connector (or its pre-fetch gate) hits a
        # policy block (HTTP 403/429, captcha, paywall) the ingestion
        # service writes this back to ``sources.source_block_reason``
        # so the operator surface can see WHY the connector stopped.
        # ``http_status`` carries the actual response code when
        # applicable (so audit rows can attach it without a second
        # HTTP call).
        self.block_reason: str | None = block_reason
        self.http_status: int | None = http_status

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def was_skipped(self) -> bool:
        return self.skipped_reason is not None

    @property
    def was_blocked(self) -> bool:
        return self.block_reason is not None

    def __repr__(self) -> str:
        return (
            f"SourceConnectorResult(source={self.source!r}, "
            f"items={len(self.items)}, errors={len(self.errors)}, "
            f"skipped={self.was_skipped}, blocked={self.was_blocked})"
        )


__all__ = ["SourceConnector", "SourceConnectorResult"]