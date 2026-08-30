"""Tests for the Phase 4 (v2.0) commercial-orders endpoints.

* `POST /api/internal/orders`                            — create + mark opp sold
* `GET  /api/internal/orders`                            — paginated list + filters
* `GET  /api/internal/orders/stats`                      — aggregate revenue / counts
* `GET  /api/internal/orders/{id}`                       — single order detail
* `POST /api/internal/orders/{id}/status`                — delivery_status transitions
* `POST /api/internal/content/{id}/mark_sold` with body  — extended for Phase 4
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers (mirror test_internal_content_api.py — same SQLite cross-session
# pattern: every test must use `client.sessionmaker` for both seeding AND
# verification, because in-memory SQLite gives each connection its own DB).
# ---------------------------------------------------------------------------
async def _seed_opportunity(
    client,
    *,
    title: str,
    slug: str,
    score: float = 85.0,
    commercial_status: str = "qualified",
    content_status: str = "generated",
) -> int:
    from app.models import Opportunity

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        opp = Opportunity(
            title=title,
            slug=slug,
            summary=f"summary for {title}",
            total_score=score,
            commercial_status=commercial_status,
            content_status=content_status,
        )
        session.add(opp)
        await session.flush()
        opp_id = opp.id
        await session.commit()
        return opp_id


async def _read_opportunity(client, opp_id: int):
    from app.models import Opportunity

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        return await session.get(Opportunity, opp_id)


async def _read_order(client, order_id: int):
    from app.models import Order

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        return await session.get(Order, order_id)


_BASE_ORDER = {
    "customer_name": "张三",
    "customer_contact": "wechat:zx123",
    "amount_cny": 49.0,
    "channel": "xianyu",
    "payment_method": "wechat",
    "payment_reference": "xy-2026-0001",
    "notes": "first sale",
}


def _create_body(opportunity_id: int, **overrides):
    body = {"opportunity_id": opportunity_id, **_BASE_ORDER}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# POST /orders
# ---------------------------------------------------------------------------
async def test_create_order_persists_row_and_flips_opp(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="legal AI", slug="legal-ai", score=88.0,
    )

    response = client.post("/api/internal/orders", json=_create_body(opp_id))
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["opportunity_id"] == opp_id
    assert payload["customer_name"] == "张三"
    assert payload["amount_cny"] == pytest.approx(49.0)
    assert payload["delivery_status"] == "pending"
    assert payload["channel"] == "xianyu"
    assert payload["opportunity_title"] == "legal AI"
    assert payload["commercial_status_snapshot"] == "qualified"

    # DB side-effects — both order row and opp status flip.
    order_id = payload["id"]
    order = await _read_order(client, order_id)
    assert order is not None and order.customer_name == "张三"

    opp = await _read_opportunity(client, opp_id)
    assert opp is not None
    assert opp.content_status == "sold"
    assert opp.commercial_status == "promising"


async def test_create_order_with_mark_opportunity_sold_false(client) -> None:
    """Operator can record a sale without auto-flipping the opp status."""
    opp_id = await _seed_opportunity(
        client, title="audit", slug="audit", score=80.0,
    )

    response = client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, mark_opportunity_sold=False),
    )
    assert response.status_code == 200
    assert response.json()["delivery_status"] == "pending"

    opp = await _read_opportunity(client, opp_id)
    # content_status should NOT have flipped.
    assert opp is not None and opp.content_status == "generated"


async def test_create_order_with_custom_delivery_status(client) -> None:
    """`delivered` is allowed at creation time (e.g. digital download)."""
    opp_id = await _seed_opportunity(
        client, title="digital", slug="digital", score=80.0,
    )

    response = client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, delivery_status="delivered"),
    )
    assert response.status_code == 200
    assert response.json()["delivery_status"] == "delivered"


async def test_create_order_404_on_unknown_opportunity(client) -> None:
    response = client.post("/api/internal/orders", json=_create_body(99999))
    assert response.status_code == 404


async def test_create_order_rejects_negative_amount(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="bad", slug="bad", score=80.0,
    )
    response = client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, amount_cny=-1.0),
    )
    assert response.status_code == 422


async def test_create_order_rejects_invalid_channel(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="bad-ch", slug="bad-ch", score=80.0,
    )
    response = client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, channel="not-a-platform"),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /orders
# ---------------------------------------------------------------------------
async def test_list_orders_empty(client) -> None:
    response = client.get("/api/internal/orders")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 0


async def test_list_orders_newest_first(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="list-opp", slug="list-opp", score=80.0,
    )
    client.post("/api/internal/orders", json=_create_body(opp_id, customer_name="first"))
    client.post("/api/internal/orders", json=_create_body(opp_id, customer_name="second"))
    client.post("/api/internal/orders", json=_create_body(opp_id, customer_name="third"))

    response = client.get("/api/internal/orders")
    payload = response.json()
    assert payload["total"] == 3
    names = [it["customer_name"] for it in payload["items"]]
    assert names == ["third", "second", "first"]


async def test_list_orders_filter_by_channel(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="filter", slug="filter", score=80.0,
    )
    client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, channel="xianyu", customer_name="xy"),
    )
    client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, channel="xiaohongshu", customer_name="xhs"),
    )

    response = client.get("/api/internal/orders?channel=xianyu")
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["customer_name"] == "xy"


async def test_list_orders_filter_by_delivery_status(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="ds", slug="ds", score=80.0,
    )
    client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, customer_name="pending"),
    )
    client.post(
        "/api/internal/orders",
        json=_create_body(
            opp_id, customer_name="delivered", delivery_status="delivered"
        ),
    )

    response = client.get("/api/internal/orders?delivery_status=delivered")
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["customer_name"] == "delivered"


async def test_list_orders_resolves_opportunity_title(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="Best Opp Ever", slug="best", score=88.0,
    )
    client.post("/api/internal/orders", json=_create_body(opp_id))

    response = client.get("/api/internal/orders")
    assert response.json()["items"][0]["opportunity_title"] == "Best Opp Ever"


# ---------------------------------------------------------------------------
# GET /orders/stats
# ---------------------------------------------------------------------------
async def test_stats_aggregates_revenue_and_counts(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="stats", slug="stats", score=80.0,
    )
    client.post(
        "/api/internal/orders",
        json=_create_body(opp_id, channel="xianyu", amount_cny=49.0),
    )
    client.post(
        "/api/internal/orders",
        json=_create_body(
            opp_id, channel="xianyu", amount_cny=99.0, delivery_status="delivered"
        ),
    )
    client.post(
        "/api/internal/orders",
        json=_create_body(
            opp_id, channel="xiaohongshu", amount_cny=29.0, delivery_status="confirmed"
        ),
    )

    response = client.get("/api/internal/orders/stats")
    assert response.status_code == 200
    stats = response.json()

    assert stats["total_orders"] == 3
    assert stats["total_revenue_cny"] == pytest.approx(177.0)
    assert stats["delivered_count"] == 1
    assert stats["confirmed_count"] == 1
    assert stats["pending_count"] == 1

    by_channel = {row["channel"]: row for row in stats["by_channel"]}
    assert by_channel["xianyu"]["count"] == 2
    assert by_channel["xianyu"]["revenue_cny"] == pytest.approx(148.0)
    assert by_channel["xiaohongshu"]["count"] == 1
    assert by_channel["xiaohongshu"]["revenue_cny"] == pytest.approx(29.0)

    assert stats["by_delivery_status"]["pending"] == 1
    assert stats["by_delivery_status"]["delivered"] == 1
    assert stats["by_delivery_status"]["confirmed"] == 1


async def test_stats_empty_database(client) -> None:
    response = client.get("/api/internal/orders/stats")
    stats = response.json()
    assert stats["total_orders"] == 0
    assert stats["total_revenue_cny"] == 0.0
    assert stats["by_channel"] == []
    assert stats["by_delivery_status"] == {}


# ---------------------------------------------------------------------------
# GET /orders/{id}
# ---------------------------------------------------------------------------
async def test_get_order_returns_full_record(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="detail", slug="detail", score=80.0,
    )
    create_response = client.post("/api/internal/orders", json=_create_body(opp_id))
    order_id = create_response.json()["id"]

    response = client.get(f"/api/internal/orders/{order_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == order_id
    assert payload["customer_name"] == "张三"
    assert payload["opportunity_title"] == "detail"


async def test_get_order_404_on_unknown(client) -> None:
    response = client.get("/api/internal/orders/99999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /orders/{id}/status
# ---------------------------------------------------------------------------
async def test_update_order_status_pending_to_delivered(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="status", slug="status", score=80.0,
    )
    create_response = client.post("/api/internal/orders", json=_create_body(opp_id))
    order_id = create_response.json()["id"]

    response = client.post(
        f"/api/internal/orders/{order_id}/status",
        json={"delivery_status": "delivered"},
    )
    assert response.status_code == 200
    assert response.json()["delivery_status"] == "delivered"

    refreshed = await _read_order(client, order_id)
    assert refreshed is not None and refreshed.delivery_status == "delivered"


async def test_update_order_status_to_confirmed(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="confirmed", slug="confirmed", score=80.0,
    )
    create_response = client.post("/api/internal/orders", json=_create_body(opp_id))
    order_id = create_response.json()["id"]

    response = client.post(
        f"/api/internal/orders/{order_id}/status",
        json={"delivery_status": "confirmed"},
    )
    assert response.status_code == 200
    assert response.json()["delivery_status"] == "confirmed"


async def test_update_order_status_to_refunded(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="refund", slug="refund", score=80.0,
    )
    create_response = client.post("/api/internal/orders", json=_create_body(opp_id))
    order_id = create_response.json()["id"]

    response = client.post(
        f"/api/internal/orders/{order_id}/status",
        json={"delivery_status": "refunded"},
    )
    assert response.status_code == 200
    assert response.json()["delivery_status"] == "refunded"


async def test_update_order_status_rejects_invalid(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="bad-status", slug="bad-status", score=80.0,
    )
    create_response = client.post("/api/internal/orders", json=_create_body(opp_id))
    order_id = create_response.json()["id"]

    response = client.post(
        f"/api/internal/orders/{order_id}/status",
        json={"delivery_status": "not-a-status"},
    )
    assert response.status_code == 422


async def test_update_order_status_404(client) -> None:
    response = client.post(
        "/api/internal/orders/99999/status",
        json={"delivery_status": "delivered"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /content/{id}/mark_sold — Phase 4 extension (order payload)
# ---------------------------------------------------------------------------
async def test_mark_sold_without_order_body_remains_backwards_compatible(
    client,
) -> None:
    """Phase 3 callers that POST empty body still get the same response."""
    opp_id = await _seed_opportunity(
        client, title="compat", slug="compat", score=80.0,
    )
    response = client.post(f"/api/internal/content/{opp_id}/mark_sold")
    assert response.status_code == 200
    payload = response.json()
    assert payload["opportunity_id"] == opp_id
    assert payload["content_status"] == "sold"
    assert payload["commercial_status"] == "promising"
    # No order key in the response — we did NOT create an Order row.
    assert "order" not in payload


async def test_mark_sold_with_order_creates_order_in_same_call(client) -> None:
    """Phase 4 — combined 'mark sold + record sale' one-shot."""
    opp_id = await _seed_opportunity(
        client, title="bundle", slug="bundle", score=85.0,
    )
    response = client.post(
        f"/api/internal/content/{opp_id}/mark_sold",
        json={
            "order": {
                "customer_name": "李四",
                "amount_cny": 99,
                "channel": "wechat",
                "payment_method": "wechat",
            }
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_status"] == "sold"
    assert payload["commercial_status"] == "promising"
    assert "order" in payload
    assert payload["order"]["customer_name"] == "李四"
    assert payload["order"]["amount_cny"] == pytest.approx(99.0)
    assert payload["order"]["delivery_status"] == "pending"


async def test_mark_sold_with_order_422_on_invalid_payload(client) -> None:
    opp_id = await _seed_opportunity(
        client, title="bad-bundle", slug="bad-bundle", score=80.0,
    )
    response = client.post(
        f"/api/internal/content/{opp_id}/mark_sold",
        json={
            "order": {
                "customer_name": "",
                "amount_cny": -10,
                "channel": "xianyu",
            }
        },
    )
    assert response.status_code == 422
