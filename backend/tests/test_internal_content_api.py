"""Tests for the Phase 3 (v2.0) Content Center backend endpoints.

* `GET  /api/internal/content/by_opportunity`         — list with joined content
* `POST /api/internal/content/{id}/mark_published`    — flip content_status
* `POST /api/internal/content/{id}/mark_sold`         — flip content_status
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_opportunity_with_content(
    client,
    *,
    title: str,
    slug: str,
    score: float,
    commercial_status: str = "qualified",
    channels: list[str] | None = None,
) -> int:
    """Seed via the SAME engine the endpoint uses (`client.sessionmaker`).

    Using a separate sessionmaker from `sqlite_session` puts us in a
    different transaction; the endpoint then sees the row but the test's
    own `get()` returns None. Funnelling both through `client.sessionmaker`
    avoids that trap.
    """
    from app.models import Notification, Opportunity

    channels = channels or ["feishu", "xianyu", "xiaohongshu"]

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        opp = Opportunity(
            title=title,
            slug=slug,
            summary=f"summary for {title}",
            total_score=score,
            commercial_status=commercial_status,
            content_status="new",
        )
        session.add(opp)
        await session.flush()
        opp_id = opp.id

        for ch in channels:
            session.add(
                Notification(
                    channel=ch,
                    payload={
                        "generator": f"{ch}_gen",
                        "title": f"{title} - {ch}",
                        "body": f"Body content for {title} via {ch}",
                        "format": "markdown" if ch != "xianyu" else "json",
                        "metadata": {"score": score},
                        "opportunity_id": opp_id,
                    },
                )
            )
        await session.commit()
        return opp_id


async def _refresh_opportunity(client, opp_id: int):
    """Read the opp through `client.sessionmaker` (same engine)."""
    from app.models import Opportunity

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        return await session.get(Opportunity, opp_id)


# ---------------------------------------------------------------------------
# /content/by_opportunity
# ---------------------------------------------------------------------------
async def test_list_by_opportunity_returns_empty_when_no_opps(client) -> None:
    response = client.get(
        "/api/internal/content/by_opportunity?only_qualified=false&limit=5"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"] == []
    assert "generated_at" in payload


async def test_list_by_opportunity_joins_latest_content_per_channel(
    client,
) -> None:
    opp_id = await _seed_opportunity_with_content(
        client,
        title="AI 客服质量分析",
        slug="ai-customer-service",
        score=87.0,
    )

    response = client.get(
        "/api/internal/content/by_opportunity?only_qualified=false&limit=10"
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["opportunity"]["id"] == opp_id
    assert item["opportunity"]["title"] == "AI 客服质量分析"
    assert item["opportunity"]["total_score"] == pytest.approx(87.0)

    content = item["content"]
    assert set(content.keys()) == {"feishu", "xianyu", "xiaohongshu"}
    assert content["feishu"]["title"].endswith("- feishu")
    assert content["xianyu"]["format"] == "json"
    assert content["xiaohongshu"]["channel"] == "xiaohongshu"


async def test_list_by_opportunity_keeps_only_latest_per_channel(
    client,
) -> None:
    opp_id = await _seed_opportunity_with_content(
        client,
        title="multi",
        slug="multi",
        score=80.0,
        channels=["feishu"],
    )

    # Add a 2nd feishu notification — should not appear.
    from app.models import Notification

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        session.add(
            Notification(
                channel="feishu",
                payload={
                    "generator": "feishu_gen",
                    "title": "NEWER feishu body",
                    "body": "newer body",
                    "format": "markdown",
                    "metadata": {},
                    "opportunity_id": opp_id,
                },
            )
        )
        await session.commit()

    response = client.get(
        "/api/internal/content/by_opportunity?only_qualified=false"
    )
    assert response.status_code == 200
    payload = response.json()
    item = payload["items"][0]
    # The latest is the one we added last.
    assert item["content"]["feishu"]["title"] == "NEWER feishu body"


async def test_list_by_opportunity_filters_unqualified(client,) -> None:
    await _seed_opportunity_with_content(
        client, title="qualified", slug="qualified", score=80.0,
        commercial_status="qualified",
    )
    await _seed_opportunity_with_content(
        client, title="unqualified", slug="unqualified", score=60.0,
        commercial_status="unqualified",
    )

    response = client.get(
        "/api/internal/content/by_opportunity?only_qualified=true&limit=10"
    )
    payload = response.json()
    titles = [it["opportunity"]["title"] for it in payload["items"]]
    assert titles == ["qualified"]


async def test_list_by_opportunity_orders_by_total_score_desc(
    client,
) -> None:
    await _seed_opportunity_with_content(
        client, title="low", slug="low", score=70.0,
    )
    await _seed_opportunity_with_content(
        client, title="high", slug="high", score=95.0,
    )

    response = client.get(
        "/api/internal/content/by_opportunity?only_qualified=false"
    )
    payload = response.json()
    titles = [it["opportunity"]["title"] for it in payload["items"]]
    assert titles == ["high", "low"]


async def test_list_by_opportunity_ignores_orphan_notifications(
    client,
) -> None:
    """Notifications whose `opportunity_id` doesn't match a current opp
    are silently dropped — not surfaced under any opportunity card.
    """
    from app.models import Notification

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        session.add(
            Notification(
                channel="feishu",
                payload={
                    "title": "orphan",
                    "body": "x",
                    "opportunity_id": 99999,  # no such opportunity
                },
            )
        )
        await session.commit()

    response = client.get("/api/internal/content/by_opportunity?only_qualified=false")
    assert response.status_code == 200
    assert response.json()["items"] == []


# ---------------------------------------------------------------------------
# /content/{id}/mark_published
# ---------------------------------------------------------------------------
async def test_mark_published_flips_content_status(client,) -> None:
    opp_id = await _seed_opportunity_with_content(
        client, title="flip", slug="flip", score=80.0,
    )

    response = client.post(f"/api/internal/content/{opp_id}/mark_published")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["opportunity_id"] == opp_id
    assert payload["content_status"] == "published"
    assert payload["commercial_status"] == "qualified"  # unchanged by default

    # Confirm the DB write landed.
    refreshed = await _refresh_opportunity(client, opp_id)
    assert refreshed is not None and refreshed.content_status == "published"


async def test_mark_published_with_commercial_override(client,) -> None:
    opp_id = await _seed_opportunity_with_content(
        client, title="bump", slug="bump", score=80.0,
    )

    response = client.post(
        f"/api/internal/content/{opp_id}/mark_published",
        json={"commercial_status": "promising"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_status"] == "published"
    assert payload["commercial_status"] == "promising"


async def test_mark_published_rejects_bad_commercial_status(client,) -> None:
    opp_id = await _seed_opportunity_with_content(
        client, title="bad", slug="bad", score=80.0,
    )
    response = client.post(
        f"/api/internal/content/{opp_id}/mark_published",
        json={"commercial_status": "deleted"},
    )
    assert response.status_code == 200
    # Unknown values are ignored — content_status flips, commercial unchanged.
    payload = response.json()
    assert payload["content_status"] == "published"
    assert payload["commercial_status"] == "qualified"


async def test_mark_published_404_on_unknown_id(client) -> None:
    response = client.post("/api/internal/content/99999/mark_published")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /content/{id}/mark_sold
# ---------------------------------------------------------------------------
async def test_mark_sold_flips_both_statuses(client,) -> None:
    opp_id = await _seed_opportunity_with_content(
        client, title="sold", slug="sold", score=80.0,
    )
    response = client.post(f"/api/internal/content/{opp_id}/mark_sold")
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_status"] == "sold"
    assert payload["commercial_status"] == "promising"


async def test_mark_sold_404_on_unknown_id(client) -> None:
    response = client.post("/api/internal/content/99999/mark_sold")
    assert response.status_code == 404