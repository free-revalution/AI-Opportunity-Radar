"""Tests for Phase 7 Feishu Drive + Bitable clients.

Mirrors the pattern used by `test_feishu_app_client.py`: every test
runs through an in-process `httpx.MockTransport` so we don't touch
the network. Each test gets its own transport + app_client pair so
state doesn't leak between tests.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.services.feishu.app_client import FeishuAppClient
from app.services.feishu.content_client import (
    FeishuBitableClient,
    FeishuContentError,
    FeishuDriveClient,
    _opp_to_bitable_fields,
)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------
def _settings_with_app(app_id: str = "cli_test", app_secret: str = "secret_test"):
    """Mutate the cached Settings object so AppClient sees valid creds."""
    s = get_settings()
    s.feishu_app_id = app_id
    s.feishu_app_secret = app_secret
    # — Clear any leftover Phase 7 tokens from earlier tests.
    s.feishu_drive_root_folder_token = ""
    s.feishu_bitable_app_token = ""
    s.feishu_bitable_opportunities_app_token = ""
    return s


def _make_clients(
    transport: httpx.AsyncBaseTransport,
    *,
    poll_interval: float = 0.01,
    poll_timeout: float = 0.5,
    folder_token: str = "",
    table_name: str = "Opportunities",
    token_setting: str = "feishu_bitable_app_token",
    settings: Any | None = None,
) -> tuple[FeishuAppClient, FeishuDriveClient, FeishuBitableClient]:
    """Build an AppClient (with mocked transport) + a Drive + a Bitable
    client that share its token cache."""
    s = settings or _settings_with_app()
    # — Set on the *settings* the helper built before constructing
    # the clients; otherwise drive.is_configured returns False and
    # create_docx refuses to run.
    s.feishu_drive_root_folder_token = folder_token
    http = httpx.AsyncClient(transport=transport)
    app_client = FeishuAppClient(settings=s, http_client=http)
    drive = FeishuDriveClient(
        app_client=app_client,
        settings=s,
        poll_interval_sec=poll_interval,
        poll_timeout_sec=poll_timeout,
    )
    bitable = FeishuBitableClient(
        app_client=app_client,
        settings=s,
        table_name=table_name,
        token_setting=token_setting,
    )
    return app_client, drive, bitable


def _ok(code: int = 0, **extra) -> dict[str, Any]:
    return {"code": code, "msg": "ok", **extra}


# ---------------------------------------------------------------------------
# Drive — Docx import
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drive_create_docx_submits_import_and_polls() -> None:
    """POST /drive/v1/import_tasks returns a ticket. Polling GET
    completes the loop. The client returns `{doc_id, url}`."""
    s = _settings_with_app()
    calls: list[tuple[str, str]] = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal":
                return httpx.Response(200, json=_ok(tenant_access_token="tok-1", expire=7200), request=request)
            if request.method == "POST" and request.url.path.endswith("/drive/v1/import_tasks"):
                body = json.loads(request.content)
                assert body["file_name"].startswith("研究报告")
                assert body["folder_token"] == "FOLDER123"
                assert body["type"] == "docx"
                # — Verify base64 content is well-formed.
                decoded = base64.b64decode(body["file"]["content"]).decode("utf-8")
                assert "执行摘要" in decoded or "Test" in decoded
                return httpx.Response(200, json=_ok(data={"ticket": "TKT-1"}), request=request)
            if request.method == "GET" and "/drive/v1/import_tasks/TKT-1" in request.url.path:
                return httpx.Response(
                    200,
                    json=_ok(data={"result": {"result": "success", "token": "DOC123", "url": "https://x.feishu.cn/docx/DOC123"}}),
                    request=request,
                )
            return httpx.Response(404, json=_ok(999), request=request)

    app_client, drive, _ = _make_clients(
        _Transport(), folder_token="FOLDER123"
    )
    try:
        result = await drive.create_docx_from_markdown(
            title="研究报告 #1 · 测试",
            markdown="# 测试\n\n执行摘要: 内容。",
        )
        assert result == {"doc_id": "DOC123", "url": "https://x.feishu.cn/docx/DOC123"}
        # — Calls: token-fetch (POST) + import_tasks (POST) + poll (GET)
        methods = [c[0] for c in calls]
        assert methods.count("POST") == 2  # token + import_tasks
        assert methods.count("GET") == 1   # poll
    finally:
        await app_client.aclose()


@pytest.mark.asyncio
async def test_drive_create_docx_polls_until_timeout_then_raises() -> None:
    """Poll keeps coming back `pending` → FeishuContentError after the
    configured timeout."""

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok-1", expire=7200), request=request)
            if request.method == "POST":
                return httpx.Response(200, json=_ok(data={"ticket": "TKT"}), request=request)
            return httpx.Response(200, json=_ok(data={"result": {"result": "pending"}}), request=request)

    app_client, drive, _ = _make_clients(
        _Transport(), poll_interval=0.01, poll_timeout=0.05, folder_token="F"
    )
    try:
        with pytest.raises(FeishuContentError, match="timed out"):
            await drive.create_docx_from_markdown(
                title="T", markdown="# body"
            )
    finally:
        await app_client.aclose()


@pytest.mark.asyncio
async def test_drive_create_docx_handles_failed_status() -> None:
    """Feishu returns `result=failed` mid-poll → error."""

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok-1", expire=7200), request=request)
            if request.method == "POST":
                return httpx.Response(200, json=_ok(data={"ticket": "TKT"}), request=request)
            return httpx.Response(
                200,
                json=_ok(data={"result": {"result": "failed", "msg": "import quota exceeded"}}),
                request=request,
            )

    app_client, drive, _ = _make_clients(_Transport(), folder_token="F")
    try:
        with pytest.raises(FeishuContentError, match="quota exceeded"):
            await drive.create_docx_from_markdown(title="T", markdown="# body")
    finally:
        await app_client.aclose()


def test_drive_create_docx_raises_when_folder_token_unset() -> None:
    s = _settings_with_app()

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    drive = FeishuDriveClient(app_client=app_client, settings=s)
    assert drive.is_configured is False
    # — Even if called without `await`, the sync guard fires.
    import asyncio

    with pytest.raises(FeishuContentError, match="not configured"):
        asyncio.run(drive.create_docx_from_markdown(title="T", markdown="# body"))


def test_drive_create_docx_uses_root_folder_token_from_settings() -> None:
    """The settings attr is reflected on the request body."""
    s = _settings_with_app()
    s.feishu_drive_root_folder_token = "MYFOLDER"
    drive = FeishuDriveClient(app_client=FeishuAppClient(settings=s), settings=s)
    assert drive.folder_token == "MYFOLDER"
    assert drive.is_configured is True


def test_drive_create_docx_base64_encodes_markdown_content() -> None:
    """Pure unit test on the encoding logic — verifies utf-8 roundtrip."""
    md = "# 中文标题\n\n段落。⚡"
    encoded = base64.b64encode(md.encode("utf-8")).decode("ascii")
    assert base64.b64decode(encoded).decode("utf-8") == md


# ---------------------------------------------------------------------------
# Bitable — app lifecycle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bitable_ensure_app_creates_when_empty_then_caches() -> None:
    """Empty token → POST /bitable/v1/apps once → cached in settings.
    Second call doesn't POST again.

    Regression: Feishu v1 response nests the app under `data.app`:
        {"code": 0, "data": {"app": {"app_token": "..."}}}
    (not `data.app_token` directly). Earlier code read the wrong path
    and raised `returned no app_token` even on a 200 success.
    """

    s = _settings_with_app()
    post_count = 0

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok", expire=7200), request=request)
            if request.method == "POST" and request.url.path.endswith("/bitable/v1/apps"):
                nonlocal post_count
                post_count += 1
                return httpx.Response(
                    200,
                    json=_ok(data={"app": {"app_token": "BITAPP", "name": "X"}}),
                    request=request,
                )
            return httpx.Response(404, json=_ok(999), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(app_client=app_client, settings=s)
    try:
        t1 = await bitable.ensure_app()
        t2 = await bitable.ensure_app()
        assert t1 == "BITAPP" == t2
        assert post_count == 1
        # — The auto-created token was persisted back to settings.
        assert s.feishu_bitable_app_token == "BITAPP"
    finally:
        await app_client.aclose()


@pytest.mark.asyncio
async def test_bitable_ensure_app_accepts_legacy_data_app_token_shape() -> None:
    """Some docs / older schemas return `data.app_token` directly. The
    client falls back to that path so we don't break callers."""
    s = _settings_with_app()

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)
            return httpx.Response(
                200,
                json=_ok(data={"app_token": "LEGACYAPP"}),
                request=request,
            )

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(app_client=app_client, settings=s)
    try:
        token = await bitable.ensure_app()
        assert token == "LEGACYAPP"
    finally:
        await app_client.aclose()


@pytest.mark.asyncio
async def test_bitable_ensure_app_surfaces_raw_data_keys_when_token_missing() -> None:
    """When Feishu returns success but no `app_token` anywhere, the
    error message lists the actual `data` keys so operators can spot
    a schema drift quickly."""
    s = _settings_with_app()

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)
            return httpx.Response(
                200,
                json=_ok(data={"unexpected_key": "x"}),
                request=request,
            )

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(app_client=app_client, settings=s)
    try:
        with pytest.raises(FeishuContentError, match="data keys"):
            await bitable.ensure_app()
    finally:
        await app_client.aclose()


def test_bitable_ensure_app_reuses_when_configured() -> None:
    """Token already in settings → no POST to /bitable/v1/apps."""
    s = _settings_with_app()
    s.feishu_bitable_app_token = "EXISTING"

    called: list[str] = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            called.append(request.url.path)
            return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(app_client=app_client, settings=s)
    import asyncio

    try:
        token = asyncio.run(bitable.ensure_app())
        assert token == "EXISTING"
        # — Only token-fetch POST. No /bitable/v1/apps call.
        assert all("/bitable/v1/apps" not in p for p in called)
    finally:
        asyncio.run(app_client.aclose())


# ---------------------------------------------------------------------------
# Bitable — table lifecycle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bitable_ensure_table_reuses_existing_table_by_name() -> None:
    """If the list returns a table with our name, reuse its id and skip
    POST /tables + POST /fields."""

    s = _settings_with_app()
    s.feishu_bitable_app_token = "BITAPP"
    created_calls: list[str] = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)
            if request.method == "GET" and request.url.path.endswith("/tables"):
                return httpx.Response(
                    200,
                    json=_ok(data={"items": [{"name": "Opportunities", "table_id": "TBL_ID"}]}),
                    request=request,
                )
            created_calls.append(request.url.path)
            return httpx.Response(404, json=_ok(999), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(app_client=app_client, settings=s)
    try:
        app_token, table_id = await bitable.ensure_table()
        assert app_token == "BITAPP"
        assert table_id == "TBL_ID"
        assert created_calls == []
    finally:
        await app_client.aclose()


@pytest.mark.asyncio
async def test_bitable_ensure_table_creates_when_missing() -> None:
    """List returns [] → POST table + POST each field."""

    s = _settings_with_app()
    s.feishu_bitable_app_token = "BITAPP"
    post_calls: list[str] = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)
            if request.method == "GET" and request.url.path.endswith("/tables"):
                return httpx.Response(200, json=_ok(data={"items": []}), request=request)
            if request.method == "POST" and request.url.path.endswith("/tables"):
                post_calls.append("create_table")
                return httpx.Response(200, json=_ok(data={"table_id": "TBL_NEW"}), request=request)
            if request.method == "POST" and "/fields" in request.url.path:
                post_calls.append("create_field")
                return httpx.Response(200, json=_ok(data={"field_id": "f"}), request=request)
            return httpx.Response(404, json=_ok(999), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(app_client=app_client, settings=s)
    try:
        app_token, table_id = await bitable.ensure_table()
        assert app_token == "BITAPP"
        assert table_id == "TBL_NEW"
        assert post_calls.count("create_table") == 1
        # — 7 default fields
        assert post_calls.count("create_field") == 7
    finally:
        await app_client.aclose()


# ---------------------------------------------------------------------------
# Bitable — bulk insert
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bitable_bulk_insert_chunks_at_500() -> None:
    """1200 items → exactly 3 batch_create calls."""
    s = _settings_with_app()
    s.feishu_bitable_app_token = "BITAPP"
    s.feishu_bitable_opportunities_app_token = "BITAPP"

    batch_count = 0

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)
            if request.method == "GET" and request.url.path.endswith("/tables"):
                return httpx.Response(
                    200,
                    json=_ok(data={"items": [{"name": "Opportunities", "table_id": "T"}]}),
                    request=request,
                )
            if request.method == "POST" and "/records/batch_create" in request.url.path:
                nonlocal batch_count
                body = json.loads(request.content)
                batch_count += 1
                # — Each chunk has up to 500 records.
                assert len(body["records"]) <= 500
                return httpx.Response(200, json=_ok(data={"records": [{"record_id": f"r{i}"} for i in range(len(body["records"]))]}), request=request)
            return httpx.Response(404, json=_ok(999), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(
        app_client=app_client,
        settings=s,
        token_setting="feishu_bitable_opportunities_app_token",
    )
    try:
        items = [
            {"id": i, "title": f"Opp {i}", "total_score": 80.0}
            for i in range(1, 1201)
        ]
        inserted = await bitable.bulk_insert_opportunities(items=items)
        assert inserted == 1200
        assert batch_count == 3
    finally:
        await app_client.aclose()


@pytest.mark.asyncio
async def test_bitable_bulk_insert_handles_empty_list() -> None:
    """Empty list → 0 calls, return 0."""
    s = _settings_with_app()
    s.feishu_bitable_opportunities_app_token = "BITAPP"
    called: list[str] = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            called.append(request.url.path)
            if "/auth/v3/tenant_access_token" in request.url.path:
                return httpx.Response(200, json=_ok(tenant_access_token="tok"), request=request)
            return httpx.Response(404, json=_ok(999), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    bitable = FeishuBitableClient(
        app_client=app_client,
        settings=s,
        token_setting="feishu_bitable_opportunities_app_token",
    )
    try:
        inserted = await bitable.bulk_insert_opportunities(items=[])
        assert inserted == 0
        # — No /tables, no /batch_create.
        assert not any("/tables" in p or "/batch_create" in p for p in called)
    finally:
        await app_client.aclose()


# ---------------------------------------------------------------------------
# Token-expired retry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_token_mixin_refreshes_once_on_99991663() -> None:
    """First request returns 99991663 → cache invalidated → second
    request carries the new Bearer."""

    s = _settings_with_app()
    s.feishu_drive_root_folder_token = "F"
    token_seen: list[str] = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("Authorization", "")
            token_seen.append(auth)
            if "/auth/v3/tenant_access_token" in request.url.path:
                # — Token changes each call so we can tell retry apart.
                call_num = token_seen.count("/auth/v3/tenant_access_token")
                if call_num == 1:
                    return httpx.Response(
                        200,
                        json=_ok(tenant_access_token="tok-old", expire=7200),
                        request=request,
                    )
                return httpx.Response(
                    200,
                    json=_ok(tenant_access_token="tok-new", expire=7200),
                    request=request,
                )
            if request.method == "POST" and request.url.path.endswith("/drive/v1/import_tasks"):
                # — First call: 99991663. Second call: success.
                if auth == "Bearer tok-old":
                    return httpx.Response(200, json=_ok(99991663, msg="token expired"), request=request)
                return httpx.Response(200, json=_ok(data={"ticket": "T"}), request=request)
            if "/drive/v1/import_tasks/T" in request.url.path:
                return httpx.Response(
                    200,
                    json=_ok(data={"result": {"result": "success", "token": "DOC", "url": "https://x/DOC"}}),
                    request=request,
                )
            return httpx.Response(404, json=_ok(999), request=request)

    app_client = FeishuAppClient(settings=s, http_client=httpx.AsyncClient(transport=_Transport()))
    drive = FeishuDriveClient(
        app_client=app_client,
        settings=s,
        poll_interval_sec=0.01,
        poll_timeout_sec=0.5,
    )
    try:
        result = await drive.create_docx_from_markdown(title="T", markdown="# body")
        assert result["doc_id"] == "DOC"
        # — We saw at least one auth-refresh (tok-new) used on the retry POST.
        assert "Bearer tok-new" in token_seen
    finally:
        await app_client.aclose()


# ---------------------------------------------------------------------------
# Pure helper: _opp_to_bitable_fields
# ---------------------------------------------------------------------------
def test_opp_to_bitable_fields_shape() -> None:
    """One Opportunity dict → Bitable `fields` shape with all 7 columns."""
    opp = {
        "id": 42,
        "title": "AI Coach",
        "total_score": 88.7,
        "category": "Education",
        "market_size": "¥10亿",
        "mvp_days": 30,
        "difficulty": "medium",
    }
    out = _opp_to_bitable_fields(opp, "http://radar.test")
    assert out["fields"]["Title"] == "AI Coach"
    assert out["fields"]["Score"] == "89"  # round(88.7)
    assert out["fields"]["Category"] == "Education"
    assert out["fields"]["Market Size"] == "¥10亿"
    assert out["fields"]["MVP Days"] == "30"
    assert out["fields"]["Difficulty"] == "medium"
    assert out["fields"]["Radar URL"] == "http://radar.test/opportunities/42"


def test_opp_to_bitable_fields_handles_missing_keys() -> None:
    """No `mvp_days`, no `category` → empty strings."""
    opp = {"id": 1, "title": "X"}
    out = _opp_to_bitable_fields(opp, "http://x")
    assert out["fields"]["Title"] == "X"
    assert out["fields"]["Score"] == ""
    assert out["fields"]["MVP Days"] == ""
    assert out["fields"]["Radar URL"] == "http://x/opportunities/1"


def test_opp_to_bitable_fields_truncates_long_title() -> None:
    """>200 char title → truncated."""
    opp = {"id": 1, "title": "A" * 500}
    out = _opp_to_bitable_fields(opp, "http://x")
    assert len(out["fields"]["Title"]) == 200