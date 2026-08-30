"""Phase 24 — source pre-fetch compliance gate tests.

Covers:
  * A-level Source → fetch proceeds normally
  * D-level Source → pre-fetch blocked, fetch NOT invoked
  * E-level Source → pre-fetch blocked, fetch NOT invoked
  * Connector returns ``SourceConnectorResult(block_reason=HTTP_403)``
    → Source.source_block_reason + last_compliance_check updated
  * Connector returns ``SourceConnectorResult(block_reason=HTTP_429)``
    → same write-back
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ingestion.base import SourceConnector, SourceConnectorResult


# ---------------------------------------------------------------------------
# Stub connector — records invocation count + returns scripted result
# ---------------------------------------------------------------------------
class _ScriptedConnector(SourceConnector):
    source = "test_stub"

    def __init__(self, *, result: SourceConnectorResult) -> None:
        super().__init__(mock=True)
        self._result = result
        self.calls = 0

    async def fetch(self) -> SourceConnectorResult:
        self.calls += 1
        return self._result


# ---------------------------------------------------------------------------
# Build a real IngestionService against an arbitrary session. We
# bypass the registry by injecting the connector via monkeypatch —
# the gate is exercised before ``build_connector`` anyway, so we just
# need `_resolve_slugs` to return our slug.
# ---------------------------------------------------------------------------
def _make_service(
    session: Any, *, slug: str = "test_stub"
) -> Any:
    from app.services.ingestion.service import IngestionService

    svc = IngestionService(session=session, source_slugs=[slug])
    svc._resolve_slugs = lambda: [slug]  # type: ignore[assignment]
    return svc


async def _make_source_row(
    session: Any,
    *,
    slug: str = "test_stub",
    compliance_level: str = "A",
    source_block_reason: str | None = None,
) -> Any:
    from datetime import datetime, timezone

    from app.models import Source

    row = Source(
        name=slug,
        type="api",
        url=f"https://example.com/{slug}",
        enabled=True,
        compliance_level=compliance_level,
        commercial_use_status="allowed",
        access_method="rss",
        retention_policy="session",
        source_block_reason=source_block_reason,
        last_compliance_check=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestSourcePreFetchGate:
    async def test_a_level_source_proceeds_to_fetch(self, sqlite_session: Any) -> None:
        await _make_source_row(sqlite_session, compliance_level="A")
        connector = _ScriptedConnector(
            result=SourceConnectorResult(source="test_stub")
        )
        svc = _make_service(sqlite_session)
        # Inject our stub connector. service.py does
        # `from .registry import build_connector`, so the name is bound
        # in the service module — patch THAT, not the registry module.
        from app.services.ingestion import service as svc_module

        original = svc_module.build_connector
        try:
            svc_module.build_connector = (  # type: ignore[assignment]
                lambda slug, settings, mock=False: connector
            )
            report = await svc.run_once()
        finally:
            svc_module.build_connector = original  # type: ignore[assignment]

        assert connector.calls == 1
        assert report.sources_succeeded == 1
        assert report.sources_skipped == 0

    async def test_d_level_source_blocked_pre_fetch(self, sqlite_session: Any) -> None:
        await _make_source_row(sqlite_session, compliance_level="D")
        connector = _ScriptedConnector(
            result=SourceConnectorResult(source="test_stub")
        )
        svc = _make_service(sqlite_session)
        from app.services.ingestion import service as svc_module

        original = svc_module.build_connector
        try:
            svc_module.build_connector = (  # type: ignore[assignment]
                lambda slug, settings, mock=False: connector
            )
            report = await svc.run_once()
        finally:
            svc_module.build_connector = original  # type: ignore[assignment]

        # No HTTP call made.
        assert connector.calls == 0
        assert report.sources_skipped == 1
        assert report.per_source["test_stub"]["block_reason"] in {
            "policy_block",
            "terms_violation",
        }

    async def test_e_level_source_blocked_pre_fetch(self, sqlite_session: Any) -> None:
        await _make_source_row(sqlite_session, compliance_level="E")
        connector = _ScriptedConnector(
            result=SourceConnectorResult(source="test_stub")
        )
        svc = _make_service(sqlite_session)
        from app.services.ingestion import service as svc_module

        original = svc_module.build_connector
        try:
            svc_module.build_connector = (  # type: ignore[assignment]
                lambda slug, settings, mock=False: connector
            )
            report = await svc.run_once()
        finally:
            svc_module.build_connector = original  # type: ignore[assignment]

        assert connector.calls == 0
        assert report.sources_skipped == 1

    async def test_connector_returns_http_403_writes_back_to_source(
        self, sqlite_session: Any
    ) -> None:
        source_row = await _make_source_row(
            sqlite_session, compliance_level="A"
        )
        connector = _ScriptedConnector(
            result=SourceConnectorResult(
                source="test_stub",
                block_reason="http_403",
                http_status=403,
            )
        )
        svc = _make_service(sqlite_session)
        from app.services.ingestion import service as svc_module

        original = svc_module.build_connector
        try:
            svc_module.build_connector = (  # type: ignore[assignment]
                lambda slug, settings, mock=False: connector
            )
            await svc.run_once()
        finally:
            svc_module.build_connector = original  # type: ignore[assignment]
        await sqlite_session.commit()

        await sqlite_session.refresh(source_row)
        assert source_row.source_block_reason == "http_403"
        assert source_row.last_compliance_check is not None
        assert source_row.last_error_at is not None

    async def test_connector_returns_http_429_writes_back_to_source(
        self, sqlite_session: Any
    ) -> None:
        source_row = await _make_source_row(
            sqlite_session, compliance_level="A"
        )
        connector = _ScriptedConnector(
            result=SourceConnectorResult(
                source="test_stub",
                block_reason="http_429",
                http_status=429,
            )
        )
        svc = _make_service(sqlite_session)
        from app.services.ingestion import service as svc_module

        original = svc_module.build_connector
        try:
            svc_module.build_connector = (  # type: ignore[assignment]
                lambda slug, settings, mock=False: connector
            )
            await svc.run_once()
        finally:
            svc_module.build_connector = original  # type: ignore[assignment]
        await sqlite_session.commit()

        await sqlite_session.refresh(source_row)
        assert source_row.source_block_reason == "http_429"
        assert source_row.last_error_at is not None
