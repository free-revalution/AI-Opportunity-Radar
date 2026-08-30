"""Tests for Phase 17B — `/content` compliance gate + persistence.

Phase 16 added the render path; Phase 17 wires the compliance gate
post-render and persists a ``content_opportunities`` row so the admin
Content Center can review the output.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.services.compliance.models import (
    ComplianceResult,
    RiskLevel,
    RiskType,
)
from app.services.feishu.inbound import BotCommand, FeishuCommandRouter
from app.services.paywall import PaywallVerdict


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_paywall(monkeypatch):
    """Bypass the DB-bound paywall check (Phase 16E test pattern)."""
    from app.services.feishu import inbound as inbound_module

    async def _creator_verdict(*, command, redis_client, sender_open_id):
        return PaywallVerdict(
            allowed=True,
            plan="creator",
            quota_type="content_full",
            quota_limit=10**9,
            quota_used=0,
        )

    monkeypatch.setattr(inbound_module, "_paywall_check", _creator_verdict)


def _make_router(handler: Any) -> FeishuCommandRouter:
    settings = get_settings()
    settings.app_base_url = "http://radar.test"
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://radar.test"
    )
    return FeishuCommandRouter(settings=settings, http_client=client)


def _completed_signal(opportunity_id: int = 42) -> dict[str, Any]:
    return {
        "id": opportunity_id,
        "slug": f"ai-signal-{opportunity_id}",
        "title": "AI 法律合同审核",
        "summary": "大模型解析 100 页 PDF 合同。",
        "category": "AI SaaS",
        "market": "legal",
        "target_user": "lawyers",
        "total_score": 88.5,
        "score": 88.5,
        "recommendation": "strongly_recommend",
    }


def _install_compliance(monkeypatch, *, blocked: bool):
    """Patch ``default_service()`` to return a fake whose
    ``check_content`` returns the desired verdict.

    ``ComplianceService`` is a ``@dataclass(slots=True)`` so we can't
    assign to its instance attributes; instead we replace the module-
    level ``_default_service`` singleton so ``default_service()``
    returns our fake on next call.
    """
    from app.services.compliance import service as comp_service_module

    class _FakeService:
        def check_content(self, output, source=None, *, context="content"):
            return ComplianceResult(
                allowed=not blocked,
                risk_score=0.9 if blocked else 0.0,
                risk_level=RiskLevel.BLOCKED if blocked else RiskLevel.LOW,
                risk_types=[RiskType.MEDICAL_ADVICE] if blocked else [],
                reason="forced_test_verdict",
                requires_human_review=blocked,
                metadata={"forced": True},
            )

    monkeypatch.setattr(
        comp_service_module, "_default_service", _FakeService()
    )


# ---------------------------------------------------------------------------
# /content — compliance verdict passed → no warning text, persisted
# ---------------------------------------------------------------------------
class TestContentCompliancePass:
    async def test_clean_content_persists_draft(self, client, monkeypatch):
        _install_compliance(monkeypatch, blocked=False)

        import app.db as db_module

        monkeypatch.setattr(
            db_module, "_sessionmaker", client.sessionmaker  # type: ignore[attr-defined]
        )
        monkeypatch.setattr(db_module, "get_engine", lambda: None)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_completed_signal(42), request=request
            )

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="content", args="42"))

        # Clean content → no compliance-block warning appended.
        assert "审核后才可见" not in reply.text
        assert reply.metadata["compliance_blocked"] is False
        assert reply.metadata["persisted"] is True

        # Verify the row was actually written.
        from sqlalchemy import select

        from app.models import ContentOpportunity

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            rows = (
                await session.execute(
                    select(ContentOpportunity).where(
                        ContentOpportunity.signal_id == 42
                    )
                )
            ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "draft"
        assert rows[0].metadata_json["compliance_blocked"] is False


# ---------------------------------------------------------------------------
# /content — compliance verdict blocked → warning text + draft + blocked flag
# ---------------------------------------------------------------------------
class TestContentComplianceBlocked:
    async def test_blocked_content_appends_warning_and_marks_draft(
        self, client, monkeypatch
    ):
        _install_compliance(monkeypatch, blocked=True)

        import app.db as db_module

        monkeypatch.setattr(
            db_module, "_sessionmaker", client.sessionmaker  # type: ignore[attr-defined]
        )
        monkeypatch.setattr(db_module, "get_engine", lambda: None)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_completed_signal(99), request=request
            )

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="content", args="99"))

        assert "⚠️" in reply.text
        assert "合规风险" in reply.text
        assert reply.metadata["compliance_blocked"] is True
        assert reply.metadata["persisted"] is True
        # RiskType serialises as its ``.value`` (lowercase).
        assert "medical_advice" in reply.metadata["compliance_risk_types"]

        # Row persisted as draft (NOT skipped) with blocked flag.
        from sqlalchemy import select

        from app.models import ContentOpportunity

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            row = (
                await session.execute(
                    select(ContentOpportunity).where(
                        ContentOpportunity.signal_id == 99
                    )
                )
            ).scalar_one()
        assert row.status == "draft"
        assert row.metadata_json["compliance_blocked"] is True
        assert "medical_advice" in row.metadata_json["compliance_risk_types"]


# ---------------------------------------------------------------------------
# Persistence fail-open — if the DB is unreachable, the user still sees
# the rendered content (persisted=False).
# ---------------------------------------------------------------------------
class TestPersistenceFailOpen:
    async def test_db_unreachable_still_returns_reply(self, monkeypatch):
        """Force ``get_sessionmaker()`` to raise so persistence fails —
        the handler must still return the rendered reply (fail-open)
        and mark ``persisted=False``."""
        _install_compliance(monkeypatch, blocked=False)

        import app.db as db_module

        def _boom():
            raise RuntimeError("forced db unavailable")

        monkeypatch.setattr(db_module, "get_sessionmaker", _boom)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_completed_signal(7), request=request
            )

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="content", args="7"))

        # Reply still contains the rendered sections — user isn't punished
        # for our DB outage.
        assert "📝 标题候选" in reply.text
        # metadata.persisted is False (DB write failed).
        assert reply.metadata["persisted"] is False
