"""Phase 26 — FeishuBitableClient new CRUD method tests.

Covers list_tables / list_records / create_record / update_record /
delete_record / batch_create_records / batch_delete_records via
the same FakeAppClient pattern used in earlier bitable tests.

This file is a stub for the new CRUD surface — the existing
``test_feishu_bitable.py`` covers the auto-create / bulk_insert
path. We keep the test isolated so a future refactor of one path
doesn't drag the other with it.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from app.services.feishu.content_client import (
    FeishuBitableClient,
    FeishuContentError,
)


# ---------------------------------------------------------------------------
# Fake app client — captures request shape + returns scripted body
# ---------------------------------------------------------------------------
class _FakeAppClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.scripted: dict[str, Any] = {}

    @property
    def is_configured(self) -> bool:
        return True

    async def get_token(self) -> str:
        return "fake_token"

    @property
    def _http(self) -> "_FakeHTTP":
        return _FakeHTTP(self)


class _FakeHTTP:
    def __init__(self, owner: _FakeAppClient) -> None:
        self._owner = owner

    async def get(self, url: str, headers: dict[str, str]) -> "_FakeResp":
        self._owner.calls.append({"method": "GET", "url": url})
        return _FakeResp(self._owner._script_for("GET", url))

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> "_FakeResp":
        self._owner.calls.append({"method": "POST", "url": url, "body": json})
        return _FakeResp(self._owner._script_for("POST", url))

    async def put(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> "_FakeResp":
        self._owner.calls.append({"method": "PUT", "url": url, "body": json})
        return _FakeResp(self._owner._script_for("PUT", url))

    async def delete(self, url: str, headers: dict[str, str]) -> "_FakeResp":
        self._owner.calls.append({"method": "DELETE", "url": url})
        return _FakeResp(self._owner._script_for("DELETE", url))


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self) -> dict[str, Any]:
        return self._payload


# Bind the script helpers onto _FakeAppClient after class definition.
def _script_for(self: _FakeAppClient, method: str, url: str) -> dict[str, Any]:
    # — Default successful body keyed by URL substring.
    for substr, body in self.scripted.items():
        if substr in url:
            return {"code": 0, "msg": "ok", "data": body}
    # — Per-method default empty body.
    if method == "GET":
        return {"code": 0, "data": {"items": []}}
    if method in ("POST", "PUT"):
        return {"code": 0, "data": {"record": {"record_id": "rec_001", "fields": {}}}}
    if method == "DELETE":
        return {"code": 0, "data": {}}
    return {"code": 0, "data": {}}


_FakeAppClient._script_for = _script_for  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def _make_client() -> FeishuBitableClient:
    from app.config import get_settings

    fake = _FakeAppClient()
    return FeishuBitableClient(
        app_client=fake,  # type: ignore[arg-type]
        settings=get_settings(),
        token_setting="feishu_bitable_app_token",
    )


@pytest.mark.asyncio
async def test_list_tables_returns_items() -> None:
    client = _make_client()
    client._cached_app_token = "app_xxx"  # type: ignore[attr-defined]
    client.app_client.scripted["tables"] = {  # type: ignore[attr-defined]
        "items": [{"table_id": "tb1", "name": "Opps"}]
    }
    out = await client.list_tables()
    assert out == [{"table_id": "tb1", "name": "Opps"}]


@pytest.mark.asyncio
async def test_list_records_paginates() -> None:
    client = _make_client()
    client._cached_app_token = "app_xxx"  # type: ignore[attr-defined]
    client.app_client.scripted["records?"] = {  # type: ignore[attr-defined]
        "items": [{"record_id": "r1", "fields": {}}],
        "page_token": "next",
    }
    items, page = await client.list_records(table_id="tb1")
    assert len(items) == 1
    assert page == "next"


@pytest.mark.asyncio
async def test_create_record_returns_id() -> None:
    client = _make_client()
    client._cached_app_token = "app_xxx"  # type: ignore[attr-defined]
    client.app_client.scripted["records"] = {  # type: ignore[attr-defined]
        "record": {"record_id": "rec_new", "fields": {"Title": "X"}}
    }
    out = await client.create_record(table_id="tb1", fields={"Title": "X"})
    assert out["record_id"] == "rec_new"
    assert out["fields"]["Title"] == "X"


@pytest.mark.asyncio
async def test_update_record_uses_put() -> None:
    client = _make_client()
    client._cached_app_token = "app_xxx"  # type: ignore[attr-defined]
    client.app_client.scripted["records/rec_001"] = {  # type: ignore[attr-defined]
        "record": {"record_id": "rec_001", "fields": {"Title": "Y"}}
    }
    out = await client.update_record(
        table_id="tb1", record_id="rec_001", fields={"Title": "Y"}
    )
    assert out["record_id"] == "rec_001"
    assert out["fields"]["Title"] == "Y"
    # — Verify the request method was PUT.
    assert any(
        c.get("method") == "PUT" and "rec_001" in c.get("url", "")
        for c in client.app_client.calls  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_delete_record_uses_delete() -> None:
    client = _make_client()
    client._cached_app_token = "app_xxx"  # type: ignore[attr-defined]
    out = await client.delete_record(table_id="tb1", record_id="rec_001")
    assert out["deleted"] is True
    assert any(
        c.get("method") == "DELETE" and "rec_001" in c.get("url", "")
        for c in client.app_client.calls  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_batch_create_chunks() -> None:
    client = _make_client()
    client._cached_app_token = "app_xxx"  # type: ignore[attr-defined]
    records = [{"fields": {"i": i}} for i in range(3)]
    total = await client.batch_create_records(table_id="tb1", records=records)
    assert total == 3
    # — Single batch (3 < chunk_size=500).
    assert len(client.app_client.calls) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_batch_delete_returns_count() -> None:
    client = _make_client()
    client._cached_app_token = "app_xxx"  # type: ignore[attr-defined]
    total = await client.batch_delete_records(
        table_id="tb1", record_ids=["r1", "r2", "r3"]
    )
    assert total == 3


@pytest.mark.asyncio
async def test_list_records_requires_table_id() -> None:
    client = _make_client()
    with pytest.raises(FeishuContentError):
        await client.list_records(table_id="")


@pytest.mark.asyncio
async def test_create_record_rejects_empty_fields() -> None:
    client = _make_client()
    with pytest.raises(FeishuContentError):
        await client.create_record(table_id="tb1", fields={})
