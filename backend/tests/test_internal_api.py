"""Tests for the internal webhook endpoints used by n8n / workers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_discovery_run_returns_report(client):
    response = client.post("/api/internal/discovery/run", json={"sources": ["github"], "mock": True})
    assert response.status_code == 200
    body = response.json()
    assert body["sources_attempted"] == 1
    assert body["sources_succeeded"] == 1
    assert body["items_inserted"] >= 1


async def test_discovery_run_with_no_body_uses_defaults(client):
    response = client.post("/api/internal/discovery/run")
    assert response.status_code == 200


async def test_digest_build_returns_top_opportunities(client):
    # FREEZE — /api/internal/digest/build removed in MVP (simplify §6).
    pytest.skip("FREEZE endpoint removed in MVP")


# ---------------------------------------------------------------------------
# Phase 35 PR-35-B: /sources/healthy 顶层 data_view_url
# ---------------------------------------------------------------------------
async def test_sources_healthy_includes_data_view_url(client, monkeypatch):
    """Phase 35 PR-35-B: snapshot 顶层含 data_view_url,来自 _resolve_data_view_url。

    PR-35-D: stub 新版 ``_resolve_data_view_urls`` 返回 list(单元素),
    断言 endpoint JSON 顶层 ``data_view_url`` 单数字段取第一个。
    """
    from app.api import internal as internal_mod

    async def _fake_resolve(settings):
        # 新版返回 list[str]
        return ["https://feishu.cn/base/dummyapp?table=tblDummy"]

    monkeypatch.setattr(internal_mod, "_resolve_data_view_urls", _fake_resolve)
    # reset module-level TTL cache so we don't get a stale value from
    # other tests
    internal_mod._DATA_VIEW_URL_CACHE.clear()
    response = client.get("/api/internal/sources/healthy")
    assert response.status_code == 200
    body = response.json()
    # 单数字段(向后兼容)= list[0]
    assert body["data_view_url"] == (
        "https://feishu.cn/base/dummyapp?table=tblDummy"
    )
    # 复数字段也有
    assert body["data_view_urls"] == [
        "https://feishu.cn/base/dummyapp?table=tblDummy"
    ]
    # items list 仍存在(顶层 + items 结构不变)
    assert "items" in body
    assert "total" in body
    internal_mod._DATA_VIEW_URL_CACHE.clear()


async def test_sources_healthy_returns_data_view_urls_plural(
    client, monkeypatch
) -> None:
    """Phase 35 PR-35-D: /sources/healthy 顶层 data_view_urls 是 list。

    stub _resolve_data_view_urls → 返回固定 list,断言 endpoint JSON 透传。
    顶层 ``data_view_url`` 仍是单数(向后兼容)= list 的第一个。
    """
    from app.api import internal as internal_mod

    async def _fake_resolve(settings):
        return [
            "https://feishu.cn/base/appA?table=tblA",
            "https://icn3947lr1ic.feishu.cn/base/appB?table=tblB",
        ]

    monkeypatch.setattr(
        internal_mod, "_resolve_data_view_urls", _fake_resolve
    )
    internal_mod._DATA_VIEW_URL_CACHE.clear()
    response = client.get("/api/internal/sources/healthy")
    assert response.status_code == 200
    body = response.json()
    assert body["data_view_urls"] == [
        "https://feishu.cn/base/appA?table=tblA",
        "https://icn3947lr1ic.feishu.cn/base/appB?table=tblB",
    ]
    # 单数字段取第一个(向后兼容)
    assert body["data_view_url"] == "https://feishu.cn/base/appA?table=tblA"
    internal_mod._DATA_VIEW_URL_CACHE.clear()


async def test_sources_healthy_data_view_urls_empty_when_feishu_down(
    client, monkeypatch
) -> None:
    """PR-35-D: 飞书 down → data_view_urls=[] 且 data_view_url=None。"""
    from app.api import internal as internal_mod

    async def _boom(settings):
        return []

    monkeypatch.setattr(
        internal_mod, "_resolve_data_view_urls", _boom
    )
    internal_mod._DATA_VIEW_URL_CACHE.clear()
    response = client.get("/api/internal/sources/healthy")
    body = response.json()
    assert body["data_view_urls"] == []
    assert body["data_view_url"] is None
    internal_mod._DATA_VIEW_URL_CACHE.clear()


async def test_resolve_data_view_url_swallows_feishu_failure(monkeypatch):
    """Phase 35 PR-35-B: 飞书 down → resolve 返回 None,不抛。"""
    from app.api import internal as internal_mod

    internal_mod._DATA_VIEW_URL_CACHE.clear()

    # stub DataTableClient.ensure_table → 抛异常
    class _BoomClient:
        async def ensure_table(self):
            raise RuntimeError("feishu unreachable")

    class _FakeAppClient:
        def __init__(self, *, settings):
            pass

    monkeypatch.setattr(
        "app.services.feishu.data_table.DataTableClient", _BoomClient
    )
    monkeypatch.setattr(
        "app.services.feishu.app_client.FeishuAppClient", _FakeAppClient
    )

    settings = internal_mod.get_settings()
    url = await internal_mod._resolve_data_view_url(settings)
    assert url is None
    internal_mod._DATA_VIEW_URL_CACHE.clear()


async def test_resolve_data_view_urls_caches_result(monkeypatch):
    """Phase 35 PR-35-D: 5 分钟 TTL 内重复调用不再打 Feishu。"""
    from app.api import internal as internal_mod

    internal_mod._DATA_VIEW_URL_CACHE.clear()
    call_count = {"n": 0}

    from app.services.feishu import data_table as data_table_mod

    class _ResolvedDT:
        def __init__(self, *, app_client, settings):
            pass

        async def ensure_table(self):
            call_count["n"] += 1
            # PR-35-D: ensure_table 现在返回 list[(app_token, table_id)]
            return [("app_tok_xyz", "tbl_abc")]

    monkeypatch.setattr(data_table_mod, "DataTableClient", _ResolvedDT)

    class _FakeAppClient:
        def __init__(self, *, settings):
            pass

    monkeypatch.setattr(
        "app.services.feishu.app_client.FeishuAppClient", _FakeAppClient
    )

    # 让 settings.feishu_bitable_data_app_token 非空 — pydantic BaseSettings
    # 不支持 monkeypatch.setattr 直接加属性,改用对象 __dict__。
    from app.config import get_settings

    settings_obj = get_settings()
    object.__setattr__(
        settings_obj, "feishu_bitable_data_app_token", "app_tok_xyz"
    )

    urls1 = await internal_mod._resolve_data_view_urls(settings_obj)
    urls2 = await internal_mod._resolve_data_view_urls(settings_obj)
    assert urls1 == ["https://feishu.cn/base/app_tok_xyz?table=tbl_abc"]
    assert urls2 == urls1
    # 第一次 ensure_table 被调,第二次走 cache(0 增量)
    assert call_count["n"] == 1
    internal_mod._DATA_VIEW_URL_CACHE.clear()


# ---------------------------------------------------------------------------
# Phase 35 PR-35-C: n8n HTTP 入口 — POST /data_table/sync + GET /task/<id>
# ---------------------------------------------------------------------------
async def test_post_data_table_sync_returns_task_id(client, monkeypatch):
    """PR-35-C: POST /api/internal/data_table/sync 返回 task_id。"""
    from app.services.feishu import data_table as data_table_mod

    async def _fake_unbounded(self, *, session, since=None, chunk_size=500,
                              run_id_label=0, on_progress=None):
        return {
            "inserted": 0, "skipped_duplicate": 0,
            "skipped_orphan": 0, "scanned": 0,
        }

    monkeypatch.setattr(
        data_table_mod.DataTableClient,
        "bulk_insert_raw_items_unbounded",
        _fake_unbounded,
    )

    response = client.post(
        "/api/internal/data_table/sync",
        json={"chunk_size": 100, "trigger": "n8n"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "task_id" in body
    assert body["status"] == "running"

    # 等 background task 跑完 — 不堵下一个 test
    import asyncio as _aio
    await _aio.sleep(0.5)


async def test_post_data_table_sync_invalid_since_returns_400(client):
    """PR-35-C: since 非 ISO8601 → 400 invalid since。"""
    response = client.post(
        "/api/internal/data_table/sync",
        json={"since": "not-a-date"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 400
    assert "invalid since" in body["error"]


async def test_get_task_status_404_for_unknown(client):
    """PR-35-C: GET /api/internal/task/<unknown> → 404 status。"""
    response = client.get("/api/internal/task/nonexistent_id_xxx")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 404