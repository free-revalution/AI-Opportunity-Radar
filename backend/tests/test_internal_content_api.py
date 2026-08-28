"""Tests for the Phase 3 (v2.0) Content Center backend endpoints.

* `GET  /api/internal/content/by_opportunity`         — list with joined content
* `POST /api/internal/content/{id}/mark_published`    — flip content_status
* `POST /api/internal/content/{id}/mark_sold`         — flip content_status

Phase 8 (v2.0) additions:
* `GET  /content/by_opportunity?channel=`             — per-channel filter
* `POST /content/generate` body `generators`          — restrict subset
* `POST /content/regenerate/{id}`                     — append / delete_previous
* `POST /content/export`                              — csv / json / bundle
* `POST /content/{id}/mark_published` body `channel`  — per-channel stamp
* `Opportunity.channel_published` JSON map            — visible in by_opportunity
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

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

    # `channels is None` → default to the 3-Phase-3 channels.
    # An explicit empty list `[]` means "don't seed any channel rows"
    # so Phase 8 tests can isolate exactly what /generate adds.
    if channels is None:
        channels = ["feishu", "xianyu", "xiaohongshu"]

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


# ---------------------------------------------------------------------------
# Phase 8 (v2.0) — `generators` whitelist on /content/generate
# ---------------------------------------------------------------------------
async def test_generate_rejects_unknown_generator(client) -> None:
    """`POST /content/generate {generators: ["nope"]}` → 422 listing the
    known set, so the operator can self-correct without reading source."""
    response = client.post(
        "/api/internal/content/generate", json={"generators": ["nope"]}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "unknown generator(s) requested"
    assert detail["unknown"] == ["nope"]
    # Must list at least the 4 known generators so the operator can fix it.
    assert set(detail["allowed"]) >= {
        "daily_report",
        "xianyu_product",
        "xiaohongshu_post",
        "wechat_article",
    }


async def test_generate_rejects_non_list_generators(client) -> None:
    """`generators` must be a list — not a string, dict, or null-as-wrong-type."""
    response = client.post(
        "/api/internal/content/generate", json={"generators": "wechat_article"}
    )
    assert response.status_code == 422
    assert "list[str]" in response.json()["detail"]


async def test_generate_with_generators_filter_runs_only_subset(
    client,
) -> None:
    """`{generators: ["wechat_article"]}` runs ONLY that channel.

    Strategy: seed one opportunity (so the fallback to `run_for_top_...`
    picks it up), POST with the filter, then assert exactly one
    Notification with `channel="wechat_article"` exists — and no
    `daily_report` / `xianyu_product` / `xiaohongshu_post` rows.
    """
    opp_id = await _seed_opportunity_with_content(
        client,
        title="filter-target",
        slug="filter-target",
        score=80.0,
        channels=[],  # start empty so we can detect exactly what /generate adds
    )

    response = client.post(
        "/api/internal/content/generate",
        json={"opportunity_ids": [opp_id], "generators": ["wechat_article"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["generated_count"] >= 1

    # Read back the notifications — only `wechat_article` should be added.
    from app.models import Notification

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        notifs = (
            await session.execute(
                select(Notification).where(
                    Notification.payload["opportunity_id"].as_integer() == opp_id
                )
            )
        ).scalars().all()

    channels = {n.channel for n in notifs}
    assert channels == {"wechat_article"}


# ---------------------------------------------------------------------------
# Phase 8 — `channel` filter on /content/by_opportunity
# ---------------------------------------------------------------------------
async def test_by_opportunity_with_channel_filter(client) -> None:
    """`?channel=wechat_article` returns the opp card with ONLY that
    channel's content payload — others are stripped server-side so the
    frontend tab "只看公众号" doesn't have to filter client-side."""
    await _seed_opportunity_with_content(
        client, title="single-chan", slug="single-chan", score=80.0,
    )

    response = client.get(
        "/api/internal/content/by_opportunity"
        "?only_qualified=false&channel=wechat_article"
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    # Only wechat_article content should be present (none seeded → empty).
    assert item["content"] == {}


async def test_by_opportunity_with_known_channel_returns_matching_only(
    client,
) -> None:
    """When we seed wechat_article content + other channels, the channel
    filter surfaces only the requested one."""
    opp_id = await _seed_opportunity_with_content(
        client, title="multi-chan", slug="multi-chan", score=80.0,
    )

    # Add a wechat_article notification that the seed helper didn't create.
    from app.models import Notification

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        session.add(
            Notification(
                channel="wechat_article",
                payload={
                    "generator": "wechat_article",
                    "title": "WECHAT title",
                    "body": "公众号 body",
                    "format": "markdown",
                    "metadata": {},
                    "opportunity_id": opp_id,
                },
            )
        )
        await session.commit()

    response = client.get(
        "/api/internal/content/by_opportunity?only_qualified=false"
        "&channel=wechat_article"
    )
    payload = response.json()
    item = payload["items"][0]
    assert set(item["content"].keys()) == {"wechat_article"}
    assert item["content"]["wechat_article"]["title"] == "WECHAT title"


async def test_by_opportunity_with_unknown_channel_returns_empty_content(
    client,
) -> None:
    """Unknown channel name → 200 + empty content (not 4xx). The endpoint
    is filter-as-view, not validation — let the operator explore without
    a hard error."""
    await _seed_opportunity_with_content(
        client, title="any-chan", slug="any-chan", score=80.0,
    )
    response = client.get(
        "/api/internal/content/by_opportunity"
        "?only_qualified=false&channel=bogus_channel"
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["content"] == {}


# ---------------------------------------------------------------------------
# Phase 8 — per-channel `mark_published`
# ---------------------------------------------------------------------------
async def test_mark_published_with_channel_writes_per_channel_map(
    client,
) -> None:
    """`POST /{id}/mark_published {channel: "wechat_article"}` writes
    just that one entry into `channel_published`."""
    opp_id = await _seed_opportunity_with_content(
        client, title="per-chan", slug="per-chan", score=80.0,
    )

    response = client.post(
        f"/api/internal/content/{opp_id}/mark_published",
        json={"channel": "wechat_article"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_status"] == "published"
    # Only wechat_article is stamped.
    cp = payload["channel_published"]
    assert set(cp.keys()) == {"wechat_article"}
    assert cp["wechat_article"]  # ISO timestamp non-empty


async def test_mark_published_without_channel_marks_all_legacy_behavior(
    client,
) -> None:
    """Empty body → legacy "mark all 4 channels" path. Backwards
    compatible with Phase 3 callers that don't know about per-channel."""
    opp_id = await _seed_opportunity_with_content(
        client, title="all-channels", slug="all-channels", score=80.0,
    )

    response = client.post(f"/api/internal/content/{opp_id}/mark_published")
    assert response.status_code == 200
    payload = response.json()
    cp = payload["channel_published"]
    assert set(cp.keys()) == {"feishu", "xianyu", "xiaohongshu", "wechat_article"}
    assert all(isinstance(ts, str) and ts for ts in cp.values())


async def test_mark_published_rejects_non_string_channel(client) -> None:
    """`channel` must be a non-empty string when present."""
    opp_id = await _seed_opportunity_with_content(
        client, title="bad-chan", slug="bad-chan", score=80.0,
    )
    response = client.post(
        f"/api/internal/content/{opp_id}/mark_published",
        json={"channel": ""},
    )
    assert response.status_code == 422


async def test_channel_published_appears_in_by_opportunity_response(
    client,
) -> None:
    """After mark_published, the `by_opportunity` response surfaces the
    full `channel_published` map per opportunity — frontend uses this
    to render the ✓/○ badges."""
    opp_id = await _seed_opportunity_with_content(
        client, title="badge", slug="badge", score=80.0,
    )
    client.post(
        f"/api/internal/content/{opp_id}/mark_published",
        json={"channel": "wechat_article"},
    )

    response = client.get(
        "/api/internal/content/by_opportunity?only_qualified=false"
    )
    item = response.json()["items"][0]
    assert "channel_published" in item["opportunity"]
    cp = item["opportunity"]["channel_published"]
    assert "wechat_article" in cp
    assert "feishu" not in cp  # not stamped → not present


# ---------------------------------------------------------------------------
# Phase 8 — /content/regenerate/{id}
# ---------------------------------------------------------------------------
async def test_regenerate_creates_new_notifications(client) -> None:
    """Default `delete_previous=False` → APPEND mode. The pre-seeded
    notifications stay; new ones get added on the requested channels."""
    from app.models import Notification
    from sqlalchemy import func, select

    opp_id = await _seed_opportunity_with_content(
        client, title="regen-append", slug="regen-append", score=80.0,
    )
    # Count pre-seed notifications for this opp.
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        before = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.payload["opportunity_id"].as_integer() == opp_id
                )
            )
        ).scalar_one()

    response = client.post(
        f"/api/internal/content/regenerate/{opp_id}",
        json={"generators": ["wechat_article"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["opportunity_id"] == opp_id
    assert payload["regenerated_count"] >= 1
    assert "wechat_article" in payload["generators"]
    # Items have the shape the frontend renders in the toast.
    item = payload["items"][0]
    assert item["generator"] == "wechat_article"
    assert item["channel"] == "wechat_article"
    assert item["title"]  # non-empty

    # Verify new notifications were actually persisted (append mode).
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        after = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.payload["opportunity_id"].as_integer() == opp_id
                )
            )
        ).scalar_one()
    assert after > before


async def test_regenerate_with_delete_previous_removes_old(client) -> None:
    """`delete_previous=True` + subset of generators → drops prior
    Notifications on those channels, keeps the others."""
    from app.models import Notification
    from sqlalchemy import func, select

    opp_id = await _seed_opportunity_with_content(
        client,
        title="regen-delete",
        slug="regen-delete",
        score=80.0,
        # Seed multiple channels; we regenerate just one.
        channels=["feishu", "xianyu"],
    )

    response = client.post(
        f"/api/internal/content/regenerate/{opp_id}",
        json={"generators": ["wechat_article"], "delete_previous": False},
    )
    assert response.status_code == 200, response.text

    # Now: feishu + xianyu still present (we didn't touch them);
    # wechat_article has at least 1 fresh notification.
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        notifs = (
            await session.execute(
                select(Notification).where(
                    Notification.payload["opportunity_id"].as_integer() == opp_id
                )
            )
        ).scalars().all()
    channels = {n.channel for n in notifs}
    assert "wechat_article" in channels
    # Legacy channels were not deleted because we only touched wechat_article.
    assert "feishu" in channels
    assert "xianyu" in channels


async def test_regenerate_with_full_delete_previous(client) -> None:
    """`delete_previous=True` without generators subset → clears ALL
    channel notifications for the opp first, then regenerates everything."""
    from app.models import Notification
    from sqlalchemy import select

    opp_id = await _seed_opportunity_with_content(
        client, title="full-delete", slug="full-delete", score=80.0,
    )

    response = client.post(
        f"/api/internal/content/regenerate/{opp_id}",
        json={"delete_previous": True},
    )
    assert response.status_code == 200, response.text

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        notifs = (
            await session.execute(
                select(Notification).where(
                    Notification.payload["opportunity_id"].as_integer() == opp_id
                )
            )
        ).scalars().all()

    # Old notifications wiped, new ones for all 4 channels now exist.
    channels = {n.channel for n in notifs}
    assert channels == {"feishu", "xianyu", "xiaohongshu", "wechat_article"}


async def test_regenerate_404_on_unknown_id(client) -> None:
    response = client.post(
        "/api/internal/content/regenerate/99999",
        json={"generators": ["wechat_article"]},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_regenerate_rejects_unknown_generator(client) -> None:
    """Same whitelist as /content/generate — typo protection."""
    opp_id = await _seed_opportunity_with_content(
        client, title="bad-gen", slug="bad-gen", score=80.0,
    )
    response = client.post(
        f"/api/internal/content/regenerate/{opp_id}",
        json={"generators": ["nope"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["unknown"] == ["nope"]


# ---------------------------------------------------------------------------
# Phase 8 — /content/export
# ---------------------------------------------------------------------------
async def test_export_csv_returns_csv_with_all_channels(client) -> None:
    """`{format: "csv"}` returns `text/csv; charset=utf-8` with the
    standard header row and one row per opportunity-channel."""
    opp_id = await _seed_opportunity_with_content(
        client, title="csv-test", slug="csv-test", score=80.0,
    )

    response = client.post(
        "/api/internal/content/export",
        json={"format": "csv", "only_qualified": False, "opportunity_ids": [opp_id]},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    cd = response.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "content_export.csv" in cd

    lines = response.text.strip().splitlines()
    header = lines[0].split(",")
    # csv.QUOTE_ALL wraps every field in quotes → strip them for the assert.
    assert [c.strip('"') for c in header] == [
        "opportunity_id",
        "opportunity_title",
        "channel",
        "title",
        "format",
        "body",
        "metadata",
        "generator",
        "created_at",
    ]
    # 3 channels seeded → 3 data rows.
    assert len(lines) == 1 + 3


async def test_export_json_returns_envelope(client) -> None:
    """`{format: "json"}` returns a JSON envelope with `items[]` — each
    item groups by opportunity, then by channel."""
    opp_id = await _seed_opportunity_with_content(
        client, title="json-test", slug="json-test", score=80.0,
    )
    response = client.post(
        "/api/internal/content/export",
        json={"format": "json", "only_qualified": False, "opportunity_ids": [opp_id]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "exported_at" in payload
    assert isinstance(payload["items"], list)
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["opportunity_id"] == opp_id
    assert set(item["content"].keys()) == {"feishu", "xianyu", "xiaohongshu"}


async def test_export_bundle_returns_files_array(client) -> None:
    """`{format: "bundle"}` returns `{files: [{filename, content_type,
    content}]}` — frontend wraps in a Blob for browser download."""
    await _seed_opportunity_with_content(
        client, title="bundle slug test", slug="bundle-test", score=80.0,
    )
    response = client.post(
        "/api/internal/content/export",
        json={"format": "bundle", "only_qualified": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "files" in payload
    files = payload["files"]
    assert len(files) == 3  # feishu/xianyu/xiaohongshu seeded

    # Filenames follow `{slug}-{channel}.{md|json}` so the operator can
    # drag them straight into Finder / 公众号编辑器.
    filenames = [f["filename"] for f in files]
    for fn in filenames:
        assert fn.endswith(".md") or fn.endswith(".json")
        # Format flag decides extension — markdown for non-xianyu.
        if fn.endswith(".md"):
            ch = fn.split("-")[-1].split(".")[0]
            assert ch != "xianyu"

    # Each file has the trio of fields the frontend downloader needs.
    for f in files:
        assert "content_type" in f
        assert "content" in f
        assert f["content_type"].startswith(("text/markdown", "application/json"))


async def test_export_with_channels_filter(client) -> None:
    """`{channels: ["xianyu"]}` restricts export to one channel."""
    opp_id = await _seed_opportunity_with_content(
        client, title="chan-filter", slug="chan-filter", score=80.0,
    )
    response = client.post(
        "/api/internal/content/export",
        json={
            "format": "csv",
            "channels": ["xianyu"],
            "opportunity_ids": [opp_id],
            "only_qualified": False,
        },
    )
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    # Header + exactly one row (xianyu only).
    assert len(lines) == 2
    assert "xianyu" in lines[1]


async def test_export_rejects_unknown_format(client) -> None:
    response = client.post(
        "/api/internal/content/export", json={"format": "pdf"}
    )
    assert response.status_code == 400
    assert "csv|json|bundle" in response.json()["detail"]


async def test_export_rejects_non_int_opportunity_ids(client) -> None:
    response = client.post(
        "/api/internal/content/export",
        json={"format": "csv", "opportunity_ids": ["x", "y"]},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Phase 8 — end-to-end smoke
# ---------------------------------------------------------------------------
async def test_e2e_generate_to_by_opportunity_to_mark_published(
    client,
) -> None:
    """The full operator flow:
      1. POST /content/generate {generators: ["wechat_article"]}
      2. GET  /content/by_opportunity?channel=wechat_article
      3. POST /content/{id}/mark_published {channel: "wechat_article"}
      4. GET  /content/by_opportunity  →  ✓/○ reflects wechat_article

    Catches contract drift between endpoints + the per-channel map.
    """
    opp_id = await _seed_opportunity_with_content(
        client, title="e2e", slug="e2e", score=80.0, channels=[],
    )

    # 1) generate just wechat_article
    r = client.post(
        "/api/internal/content/generate",
        json={"opportunity_ids": [opp_id], "generators": ["wechat_article"]},
    )
    assert r.status_code == 200, r.text

    # 2) by_opportunity channel filter surfaces it
    r = client.get(
        "/api/internal/content/by_opportunity"
        "?only_qualified=false&channel=wechat_article"
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert "wechat_article" in items[0]["content"]
    # Pre-mark, the badge is OFF.
    assert "wechat_article" not in items[0]["opportunity"]["channel_published"]

    # 3) mark just wechat_article as published
    r = client.post(
        f"/api/internal/content/{opp_id}/mark_published",
        json={"channel": "wechat_article"},
    )
    assert r.status_code == 200
    assert r.json()["content_status"] == "published"
    assert "wechat_article" in r.json()["channel_published"]

    # 4) subsequent by_opportunity surfaces the per-channel ✓
    r = client.get("/api/internal/content/by_opportunity?only_qualified=false")
    items = r.json()["items"]
    assert len(items) == 1
    cp = items[0]["opportunity"]["channel_published"]
    assert "wechat_article" in cp
    # Other channels still un-marked (NOT stamped to a timestamp).
    assert "feishu" not in cp
    assert "xianyu" not in cp
    assert "xiaohongshu" not in cp


# ---------------------------------------------------------------------------
# Phase 9 — content editing + version history
# ---------------------------------------------------------------------------
async def test_edit_creates_new_version_with_audit_trail(client) -> None:
    """POST /content/{notification_id}/edit creates a NEW notification
    on the same channel — the source row stays untouched so the
    audit trail is preserved."""
    from app.models import Notification
    from sqlalchemy import func, select

    opp_id = await _seed_opportunity_with_content(
        client, title="edit-test", slug="edit-test", score=80.0,
    )
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        before = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.payload["opportunity_id"].as_integer() == opp_id
                )
            )
        ).scalar_one()
        first = (
            await session.execute(
                select(Notification)
                .where(Notification.payload["opportunity_id"].as_integer() == opp_id)
                .order_by(Notification.id.asc())
                .limit(1)
            )
        ).scalars().first()
        assert first is not None
        src_id = first.id

    r = client.post(
        f"/api/internal/content/{src_id}/edit",
        json={"body": "edited body text", "edit_note": "fixed typo in CTA"},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["body"] == "edited body text"
    assert payload["edited_from_notification_id"] == src_id
    new_id = payload["notification_id"]
    assert new_id != src_id

    # Source row untouched, new row appended.
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        after = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.payload["opportunity_id"].as_integer() == opp_id
                )
            )
        ).scalar_one()
    assert after == before + 1

    # Audit fields land in the new row's payload.
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        new_row = await session.get(Notification, new_id)
        assert new_row is not None
        assert new_row.payload["edited_from_notification_id"] == src_id
        assert new_row.payload["edit_note"] == "fixed typo in CTA"
        assert new_row.payload["body"] == "edited body text"


async def test_edit_requires_at_least_one_field(client) -> None:
    """Empty body → 422 — operator must signal what they're editing."""
    opp_id = await _seed_opportunity_with_content(
        client, title="noop-edit", slug="noop-edit", score=80.0,
    )
    from app.models import Notification
    from sqlalchemy import select

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        first = (
            await session.execute(
                select(Notification)
                .where(Notification.payload["opportunity_id"].as_integer() == opp_id)
                .order_by(Notification.id.asc())
                .limit(1)
            )
        ).scalars().first()
        assert first is not None
        src_id = first.id

    r = client.post(f"/api/internal/content/{src_id}/edit", json={})
    assert r.status_code == 422
    assert "body" in r.json()["detail"]


async def test_edit_404_on_unknown_notification(client) -> None:
    r = client.post("/api/internal/content/99999/edit", json={"body": "x"})
    assert r.status_code == 404


async def test_edit_can_override_title_only(client) -> None:
    """Operator might want to fix the title without touching body —
    title-only edit must work and propagate."""
    from app.models import Notification
    from sqlalchemy import select

    opp_id = await _seed_opportunity_with_content(
        client, title="title-fix", slug="title-fix", score=80.0,
    )
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        first = (
            await session.execute(
                select(Notification)
                .where(Notification.payload["opportunity_id"].as_integer() == opp_id)
                .order_by(Notification.id.asc())
                .limit(1)
            )
        ).scalars().first()
        assert first is not None
        src_id = first.id

    r = client.post(
        f"/api/internal/content/{src_id}/edit",
        json={"title": "New Improved Title"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "New Improved Title"


async def test_versions_returns_full_history(client) -> None:
    """GET /content/{opp_id}/versions returns all notifications for
    the opportunity in DESC order, with previews so the operator
    doesn't have to load each one."""
    from app.models import Notification

    opp_id = await _seed_opportunity_with_content(
        client, title="history", slug="history", score=80.0,
    )

    # Add 2 more notifications so we have a real history.
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        for body in ("v2 regenerated body", "v3 edited body"):
            session.add(
                Notification(
                    channel="feishu",
                    payload={
                        "title": "title",
                        "body": body,
                        "metadata": {},
                        "opportunity_id": opp_id,
                    },
                )
            )
        await session.commit()

    r = client.get(f"/api/internal/content/{opp_id}/versions")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["opportunity_id"] == opp_id
    assert payload["total"] == 5  # 3 seeded + 2 added
    # Newest first.
    assert payload["items"][0]["preview"] == "v3 edited body"
    assert payload["items"][-1]["preview"] == "Body content for history via feishu"

    # Each item has the full metadata + edit trail fields.
    for item in payload["items"]:
        assert "notification_id" in item
        assert "channel" in item
        assert "created_at" in item


async def test_versions_filter_by_channel(client) -> None:
    """`?channel=feishu` restricts to one channel."""
    opp_id = await _seed_opportunity_with_content(
        client, title="chan-filter-hist", slug="chan-filter-hist", score=80.0,
    )
    r = client.get(f"/api/internal/content/{opp_id}/versions?channel=xianyu")
    assert r.status_code == 200
    payload = r.json()
    assert payload["channel"] == "xianyu"
    assert all(item["channel"] == "xianyu" for item in payload["items"])


async def test_versions_404_on_unknown_opp(client) -> None:
    r = client.get("/api/internal/content/99999/versions")
    assert r.status_code == 404


async def test_edited_version_surfaces_in_by_opportunity(client) -> None:
    """E2E: edit a notification → /by_opportunity shows the edited
    version as the latest for that channel (and `channel_published`
    flips if the edit triggers a regen — but here we just want the
    body update to propagate)."""
    from app.models import Notification
    from sqlalchemy import select

    opp_id = await _seed_opportunity_with_content(
        client, title="edit-e2e", slug="edit-e2e", score=80.0,
    )
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        first = (
            await session.execute(
                select(Notification)
                .where(Notification.payload["opportunity_id"].as_integer() == opp_id)
                .where(Notification.channel == "feishu")
                .order_by(Notification.id.asc())
                .limit(1)
            )
        ).scalars().first()
        assert first is not None
        src_id = first.id

    # Edit the feishu notification with new body.
    client.post(
        f"/api/internal/content/{src_id}/edit",
        json={"body": "EDITED E2E BODY", "edit_note": "round-trip test"},
    )

    # The latest feishu body should now be the edited one.
    r = client.get("/api/internal/content/by_opportunity?only_qualified=false")
    assert r.status_code == 200
    item = next(
        it
        for it in r.json()["items"]
        if it["opportunity"]["id"] == opp_id
    )
    assert item["content"]["feishu"]["body"] == "EDITED E2E BODY"