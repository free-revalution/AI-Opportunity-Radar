"""Tests for the Phase 5 (v2.0) on-demand research endpoint.

* `POST /api/internal/research/on_demand`        — accept URL or topic, run inline
* `GET  /api/internal/research/on_demand/recent` — list recent jobs
* `GET  /api/internal/research/on_demand/{job_id}` — single job + report

The on-demand path bypasses the discovery pipeline: it bootstraps a
fresh Opportunity from the customer-supplied input, runs
`ResearchService.process_job` inline with `seed_urls` overrides, and
optionally attaches an Order. We patch the LLM + web providers to
deterministic mocks so the tests run offline and fast.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Provider patches — force the on-demand path to use deterministic mocks.
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_providers(monkeypatch):
    """Patch the LLM + web factories so the on-demand path uses the
    research-flavoured mocks. The default `MockLLMProvider` returns
    screening-shape JSON that won't satisfy the strict research-report
    parser; we need `MockResearchLLMProvider` which emits valid
    `ResearchReport` payloads.
    """
    from app.services.research.mock_llm import MockResearchLLMProvider
    from app.services.research.mock_web_data import MockWebDataProvider

    # ResearchService() resolves both factories internally via its
    # top-of-module `from … import build_*` bindings — patch those
    # module-local references, not the source modules.
    monkeypatch.setattr(
        "app.services.research.service.build_llm_provider",
        lambda settings: MockResearchLLMProvider(),
    )
    monkeypatch.setattr(
        "app.services.research.service.build_web_data_provider",
        lambda settings: MockWebDataProvider(),
    )
    # The endpoint's topic path imports `build_web_data_provider`
    # inline (`from … import build_web_data_provider` inside the
    # handler), so each call re-reads the attribute — patch the
    # source module so subsequent endpoint calls see the mock.
    monkeypatch.setattr(
        "app.services.research.web_data.build_web_data_provider",
        lambda settings: MockWebDataProvider(),
    )
    return MockResearchLLMProvider, MockWebDataProvider


# ---------------------------------------------------------------------------
# POST /research/on_demand
# ---------------------------------------------------------------------------
async def test_on_demand_url_creates_job_and_report(client, mock_providers) -> None:
    response = client.post(
        "/api/internal/research/on_demand",
        json={"url": "https://example.com/ai-product"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["opportunity_id"] > 0
    assert payload["opportunity_title"] == "https://example.com/ai-product"
    assert payload["opportunity_slug"].startswith("on-demand-")
    assert payload["job_id"] > 0
    assert payload["status"] == "completed"
    assert payload["executive_summary"]
    assert payload["confidence"] > 0
    assert payload["sources_count"] >= 1
    assert payload["order_id"] is None  # no customer info supplied


async def test_on_demand_topic_runs_search_and_creates_report(
    client, mock_providers
) -> None:
    response = client.post(
        "/api/internal/research/on_demand",
        json={"topic": "AI legal contract review"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["job_id"] > 0
    assert payload["executive_summary"]


async def test_on_demand_with_order_attaches_order_in_same_call(
    client, mock_providers
) -> None:
    """Customer info + amount_cny → one call creates the report AND the order."""
    from app.models import Order

    response = client.post(
        "/api/internal/research/on_demand",
        json={
            "url": "https://example.com/paid-report",
            "customer_name": "李四",
            "customer_contact": "wechat:lisi",
            "amount_cny": 299,
            "channel": "wechat",
            "payment_method": "wechat",
            "notes": "first paid report",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["order_id"] is not None and payload["order_id"] > 0
    assert payload["status"] == "completed"

    # Verify the Order row landed + opportunity status flipped to sold.
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        order = await session.get(Order, payload["order_id"])
        assert order is not None
        assert order.customer_name == "李四"
        assert order.amount_cny == Decimal("299.00")
        assert order.delivery_status == "delivered"  # report = delivered

        from app.models import Opportunity

        opp = await session.get(Opportunity, payload["opportunity_id"])
        assert opp is not None
        assert opp.content_status == "sold"
        assert opp.commercial_status == "promising"


async def test_on_demand_422_when_both_url_and_topic_missing(client, mock_providers) -> None:
    response = client.post(
        "/api/internal/research/on_demand",
        json={"customer_name": "x", "amount_cny": 100, "channel": "wechat"},
    )
    assert response.status_code == 422


async def test_on_demand_422_when_both_url_and_topic_provided(
    client, mock_providers
) -> None:
    response = client.post(
        "/api/internal/research/on_demand",
        json={
            "url": "https://example.com/a",
            "topic": "another topic",
        },
    )
    assert response.status_code == 422


async def test_on_demand_422_when_order_fields_incomplete(
    client, mock_providers
) -> None:
    """`customer_contact` set but `customer_name` missing → 422."""
    response = client.post(
        "/api/internal/research/on_demand",
        json={
            "url": "https://example.com/a",
            "customer_contact": "wechat:zx",
            "amount_cny": 100,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /research/on_demand/recent
# ---------------------------------------------------------------------------
async def test_list_recent_empty_initially(client, mock_providers) -> None:
    response = client.get("/api/internal/research/on_demand/recent")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 0


async def test_list_recent_returns_on_demand_jobs_newest_first(
    client, mock_providers
) -> None:
    # Two on-demand jobs + one normal opportunity (which must NOT appear).
    client.post(
        "/api/internal/research/on_demand",
        json={"url": "https://example.com/first"},
    )
    client.post(
        "/api/internal/research/on_demand",
        json={"topic": "second topic"},
    )

    # Insert a regular pipeline opp without an on-demand summary marker.
    from app.models import Opportunity

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        session.add(
            Opportunity(
                title="normal pipeline opp",
                slug="normal-pipeline",
                summary="discovered via daily discovery",
            )
        )
        await session.commit()

    response = client.get("/api/internal/research/on_demand/recent")
    payload = response.json()
    assert payload["total"] == 2
    # Newest first.
    assert payload["items"][0]["seed_topic"] == "second topic"
    assert payload["items"][1]["seed_url"] == "https://example.com/first"


async def test_list_recent_includes_report_summary(client, mock_providers) -> None:
    client.post(
        "/api/internal/research/on_demand",
        json={"url": "https://example.com/detail"},
    )

    response = client.get("/api/internal/research/on_demand/recent?limit=10")
    item = response.json()["items"][0]
    assert item["status"] == "completed"
    assert item["executive_summary"]
    assert item["confidence"] > 0
    assert item["sources_count"] >= 1
    assert item["started_at"] is not None
    assert item["completed_at"] is not None


# ---------------------------------------------------------------------------
# GET /research/on_demand/{job_id}
# ---------------------------------------------------------------------------
async def test_get_on_demand_returns_full_report(client, mock_providers) -> None:
    create_resp = client.post(
        "/api/internal/research/on_demand",
        json={"url": "https://example.com/detail-page"},
    )
    job_id = create_resp.json()["job_id"]

    response = client.get(f"/api/internal/research/on_demand/{job_id}")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["job_id"] == job_id
    assert payload["status"] == "completed"
    assert payload["error"] is None
    assert payload["seed_url"] == "https://example.com/detail-page"
    assert payload["seed_topic"] is None

    # Full report payload — all 7 sections.
    report = payload["report"]
    assert report is not None
    for section in (
        "executive_summary",
        "market_analysis",
        "competition_analysis",
        "china_analysis",
        "monetization_analysis",
        "mvp_analysis",
        "risk_analysis",
    ):
        assert report[section], f"missing {section}"
    assert report["recommendation"] in (
        "strongly_recommend",
        "recommend",
        "watch",
        "not_recommended",
        "insufficient_data",
    )
    assert isinstance(report["sources"], list)


async def test_get_on_demand_404_on_unknown_job(client, mock_providers) -> None:
    response = client.get("/api/internal/research/on_demand/99999")
    assert response.status_code == 404


async def test_get_on_demand_for_topic_preserves_seed(client, mock_providers) -> None:
    create_resp = client.post(
        "/api/internal/research/on_demand",
        json={"topic": "AI 法律合同审核"},
    )
    job_id = create_resp.json()["job_id"]

    response = client.get(f"/api/internal/research/on_demand/{job_id}")
    payload = response.json()
    assert payload["seed_topic"] == "AI 法律合同审核"
    assert payload["seed_url"] is None
