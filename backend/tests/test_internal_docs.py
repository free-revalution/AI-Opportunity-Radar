"""Phase 25 v2.1 — /api/internal/docs/{tree,daily} endpoint tests.

These tests pin the contract the Feishu bot relies on when it
``/docs tree`` or ``/docs daily 2026-08-30`` replies.

A fake ``FeishuDriveClient`` is monkey-patched into the imports
inside ``app.api.internal`` so the suite stays offline.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest


class _FakeDrive:
    """Same shape as FeishuDriveClient; in-memory state only."""

    def __init__(
        self,
        *,
        app_client: Any | None = None,
        settings: Any | None = None,
        http_client: Any | None = None,
        base_url: str = "",
        poll_interval_sec: float = 0.1,
        poll_timeout_sec: float = 1.0,
    ) -> None:
        from app.config import get_settings

        self.app_client = app_client
        self.settings = settings or get_settings()
        self._folders: dict[tuple[str, str], str] = {}
        self._counter = 0
        # — Phase 26 — track per-token moves/renames/deletes for assertions.
        self.moved: list[tuple[str, str, str]] = []  # (token, new_parent, type)
        self.renamed: list[tuple[str, str, str]] = []  # (token, new_name, type)
        self.deleted: list[tuple[str, str]] = []  # (token, type)
        # — ConfirmStore stash — endpoints that stage a delete
        # (docs_rm) read from this dict when docs_confirm runs.
        self._pending_actions: dict[str, dict[str, Any]] = {}

    @classmethod
    def create_default(
        cls,
        *,
        settings: Any | None = None,
    ) -> _FakeDrive:
        """Mirror :meth:`FeishuDriveClient.create_default` for tests.

        Returns the module-level ``_FAKE`` instance (if set) so that
        state survives across multiple ``_build_docs_services`` calls
        within the same test. Falls back to a fresh instance when the
        helper hasn't been run.
        """
        global _FAKE  # type: ignore[misc]
        if _FAKE is not None:
            return _FAKE
        return cls(settings=settings)

    @property
    def is_configured(self) -> bool:
        return bool(self.folder_token)

    @property
    def folder_token(self) -> str:
        return self.settings.feishu_drive_root_folder_token

    async def create_folder(self, *, name: str, parent_token: str | None = None) -> str:
        self._counter += 1
        tok = f"fld_{self._counter}_{name[:6]}"
        self._folders[(parent_token or self.folder_token, name)] = tok
        return tok

    async def list_children(self, *, folder_token: str, name: str | None = None) -> list[dict]:
        out = []
        for (parent, n), tok in self._folders.items():
            if parent != folder_token:
                continue
            if name is not None and n != name:
                continue
            out.append({"token": tok, "name": n})
        return out

    async def find_child_by_name(self, *, folder_token: str, name: str) -> str | None:
        """Phase 26 contract — return just the token (str), not the dict.

        Matches :meth:`FeishuDriveClient.find_child_by_name` signature.
        """
        for (parent, n), tok in self._folders.items():
            if parent == folder_token and n == name:
                return tok
        return None

    async def ensure_folder_path(self, *, parent_token: str, path: list[str]) -> str:
        cur = parent_token
        for name in path:
            found = await self.find_child_by_name(folder_token=cur, name=name)
            if found is not None:
                cur = found
                continue
            cur = await self.create_folder(name=name, parent_token=cur)
        return cur

    async def create_docx_from_markdown(self, *, title: str, markdown: str, folder_token: str | None = None) -> dict[str, Any]:
        return {
            "doc_id": f"doc_{title[:8]}",
            "url": f"https://feishu.cn/docx/{title[:8]}",
            "folder_token": folder_token or self.folder_token,
        }

    # -- Phase 26 ---------------------------------------------------------

    async def search_files(
        self,
        *,
        folder_token: str | None = None,
        keyword: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        target = folder_token or self.folder_token
        kw = (keyword or "").strip().lower()
        for (parent, name), tok in self._folders.items():
            if parent != target:
                continue
            if kw and kw not in name.lower():
                continue
            out.append({"token": tok, "name": name, "type": "folder"})
            if len(out) >= limit:
                break
        return out

    async def get_file_meta(
        self,
        *,
        file_tokens: list[str],
        file_type: str = "folder",
    ) -> list[dict[str, Any]]:
        return [
            {"token": t, "type": file_type, "url": f"https://feishu.cn/{file_type}/{t}"}
            for t in file_tokens
        ]

    async def move_file(
        self,
        *,
        file_token: str,
        target_folder_token: str,
        file_type: str = "folder",
    ) -> dict[str, Any]:
        self.moved.append((file_token, target_folder_token, file_type))
        return {
            "file_token": file_token,
            "target_folder_token": target_folder_token,
        }

    async def rename_file(
        self,
        *,
        file_token: str,
        new_name: str,
        file_type: str = "folder",
    ) -> dict[str, Any]:
        self.renamed.append((file_token, new_name, file_type))
        return {"file_token": file_token, "name": new_name}

    async def delete_file(
        self,
        *,
        file_token: str,
        file_type: str = "folder",
    ) -> dict[str, Any]:
        self.deleted.append((file_token, file_type))
        return {"task_id": f"task_{file_token}", "file_token": file_token}

    async def poll_delete_task(
        self,
        *,
        task_id: str,
        timeout_sec: float = 60.0,
        interval_sec: float = 1.5,
    ) -> dict[str, Any]:
        return {"status": "success", "raw": {"task_id": task_id}}


def _configure(settings: Any, *, root: str = "root_folder_token") -> Any:
    settings.feishu_drive_root_folder_token = root
    # — Default: gate the docs endpoints behind require_admin. Tests
    # use the X-Feishu-Open-Id header to satisfy the dependency.
    if not settings.admin_open_ids:
        settings.admin_open_ids = ["ou_test_admin"]
    return settings


_ADMIN_HEADERS = {"X-Feishu-Open-Id": "ou_test_admin"}


@pytest.fixture(autouse=True)
def _restore_settings_after_test(settings: Any):
    """Snapshot settings before each Phase 26 test, restore on teardown.

    The docs tests intentionally set ``admin_open_ids`` to gate the
    endpoints behind ``require_admin``. Without this autouse fixture
    those mutations leak into later test files (e.g. scoring /
    screening) which rely on the dev shortcut where every auth
    source is empty.
    """
    from copy import deepcopy

    saved = deepcopy(settings)
    yield
    for field in settings.__dict__:
        if field.startswith("_") or field in ("model_config",):
            continue
        try:
            setattr(settings, field, getattr(saved, field))
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# /api/internal/docs/tree
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_docs_tree_returns_4_sections(client: Any, settings: Any) -> None:
    _configure(settings)
    # — the endpoint lazy-imports ``FeishuDriveClient`` from
    # ``app.services.feishu.content_client`` so we patch the source
    # module (the lookup site) rather than ``app.api.internal``.
    with pytest.MonkeyPatch.context() as mp:
        from app.services.feishu import content_client as cc_module

        mp.setattr(cc_module, "FeishuDriveClient", _FakeDrive)
        resp = client.get("/api/internal/docs/tree", headers=_ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["sections"] == ["home", "today", "daily_reports", "sources"]
    tokens = body["tokens"]
    assert tokens["root"] == "root_folder_token"
    for key in ("home", "today", "daily_reports", "sources"):
        assert tokens[key].startswith("fld_")


@pytest.mark.asyncio
async def test_docs_tree_without_root_returns_503(client: Any, settings: Any) -> None:
    _configure(settings, root="")
    resp = client.get("/api/internal/docs/tree", headers=_ADMIN_HEADERS)
    assert resp.status_code == 503
    assert "FEISHU_DRIVE_ROOT_FOLDER_TOKEN" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/internal/docs/daily
# ---------------------------------------------------------------------------
def test_docs_daily_unknown_date_returns_found_false(client: Any) -> None:
    resp = client.get(
        "/api/internal/docs/daily?date=2020-01-01", headers=_ADMIN_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"found": False, "date": "2020-01-01"}


def test_docs_daily_invalid_date_returns_400(client: Any) -> None:
    resp = client.get(
        "/api/internal/docs/daily?date=not-a-date", headers=_ADMIN_HEADERS
    )
    assert resp.status_code == 400
    assert "YYYY-MM-DD" in resp.json()["detail"]


def test_docs_daily_resolves_existing_row(
    client: Any, sqlite_session: Any
) -> None:
    """Seed a DailyDigestDoc and verify the endpoint surfaces it."""
    import asyncio

    from app.models import DailyDigestDoc

    async def _seed() -> None:
        row = DailyDigestDoc(
            date=date(2026, 8, 30),
            doc_id="doc_seed_001",
            doc_url="https://feishu.cn/docx/seed",
            folder_token="fld_seed",
            run_id=99,
            raw_count=10,
            signal_count=4,
        )
        sqlite_session.add(row)
        await sqlite_session.commit()

    asyncio.get_event_loop().run_until_complete(_seed())
    resp = client.get(
        "/api/internal/docs/daily?date=2026-08-30", headers=_ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["found"] is True
    assert body["date"] == "2026-08-30"
    assert body["doc_id"] == "doc_seed_001"
    assert body["doc_url"] == "https://feishu.cn/docx/seed"
    assert body["run_id"] == 99
    assert body["raw_count"] == 10
    assert body["signal_count"] == 4


# ===========================================================================
# Phase 26 — /api/internal/docs/* CRUD endpoints
# ===========================================================================
class _FakeBitableClient:
    """In-memory stand-in for FeishuBitableClient."""

    def __init__(self) -> None:
        from app.config import get_settings

        self.settings = get_settings()
        self._tables: list[dict[str, Any]] = [
            {"table_id": "tb1", "name": "Opportunities"},
        ]
        self._records: list[dict[str, Any]] = []
        self._counter = 0

    async def list_tables(
        self,
        *,
        app_token: str | None = None,
    ) -> list[dict[str, Any]]:
        return list(self._tables)

    async def ensure_app(self) -> str:
        """Return a fake app_token for tests."""
        return "fake_app_token"

    async def ensure_table(self, **kwargs: Any) -> dict[str, Any]:
        """Used by BitableManager._resolve_table fallback."""
        if self._tables:
            return self._tables[0]
        self._tables.append({"table_id": "tb1", "name": "Default"})
        return self._tables[0]

    async def list_records(
        self,
        *,
        app_token: str | None = None,
        table_id: str | None = None,
        page_size: int = 20,
        page_token: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        items = [
            r for r in self._records
            if (table_id is None or r.get("table_id") == table_id)
        ]
        return items[:page_size], None

    async def create_record(
        self,
        *,
        app_token: str | None = None,
        table_id: str | None = None,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        self._counter += 1
        rid = f"rec_{self._counter:03d}"
        self._records.append(
            {"record_id": rid, "table_id": table_id, "fields": dict(fields)}
        )
        return {"record_id": rid, "fields": dict(fields)}

    async def update_record(
        self,
        *,
        app_token: str | None = None,
        table_id: str | None = None,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        for r in self._records:
            if r["record_id"] == record_id:
                r["fields"] = dict(fields)
                return {"record_id": record_id, "fields": dict(fields)}
        return {"record_id": record_id, "fields": dict(fields)}

    async def delete_record(
        self,
        *,
        app_token: str | None = None,
        table_id: str | None = None,
        record_id: str,
    ) -> dict[str, Any]:
        self._records = [r for r in self._records if r["record_id"] != record_id]
        return {"deleted": True, "record_id": record_id}

    async def batch_create_records(
        self,
        *,
        app_token: str | None = None,
        table_id: str | None = None,
        records: list[dict[str, Any]],
        chunk_size: int = 500,
    ) -> int:
        for rec in records:
            await self.create_record(
                app_token=app_token, table_id=table_id, fields=rec.get("fields", {})
            )
        return len(records)

    async def batch_delete_records(
        self,
        *,
        app_token: str | None = None,
        table_id: str | None = None,
        record_ids: list[str],
        chunk_size: int = 500,
    ) -> int:
        self._records = [r for r in self._records if r["record_id"] not in record_ids]
        return len(record_ids)


class _FakeAppClient:
    """No-op stand-in for FeishuAppClient — only the constructor
    signature is exercised by ``_build_docs_services``."""

    def __init__(self, *, settings: Any | None = None) -> None:
        from app.config import get_settings

        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return True

    async def get_token(self) -> str:
        return "fake"


def _patch_docs_endpoints(monkeypatch: pytest.MonkeyPatch, *, settings: Any) -> _FakeDrive:
    """Patch all the seams ``_build_docs_services`` reaches for and
    wire a real ConfirmStore against an in-memory Redis fake.

    Returns the :class:`_FakeDrive` so tests can introspect
    move / rename / delete calls.

    Note: every call to :func:`_build_docs_services` constructs a
    fresh ``FeishuDriveClient.create_default(...)`` — to keep state
    across endpoints (e.g. ``docs/create`` followed by
    ``docs/rm``) we expose the fake via a module-level ``_FAKE``
    attribute and have ``create_default`` return the SAME instance.
    """
    from app.services.feishu import content_client as cc_module
    from app.services.feishu import app_client as app_module
    import app.services.redis_client as rc_module

    global _FAKE
    _FAKE = _FakeDrive(settings=settings)
    fake_bitable = _FakeBitableClient()
    fake_app = _FakeAppClient(settings=settings)

    monkeypatch.setattr(cc_module, "FeishuDriveClient", _FakeDrive)
    monkeypatch.setattr(cc_module, "FeishuBitableClient", lambda **kw: fake_bitable)
    monkeypatch.setattr(app_module, "FeishuAppClient", lambda *, settings=None: fake_app)

    # — Inject a fake Redis client for ConfirmStore.
    fake_redis = _MinimalRedis()

    async def fake_get_redis() -> Any:
        return fake_redis

    monkeypatch.setattr(rc_module, "get_redis", fake_get_redis)
    # — internal.py uses ``from app.services.redis_client import
    # get_redis`` inside _build_docs_services, so patching the source
    # module is enough — no need to also patch the importer.
    return _FAKE


_FAKE: _FakeDrive | None = None  # type: ignore[assignment]


class _MinimalRedis:
    """Tiny in-memory Redis fake — just set + getdel."""

    def __init__(self) -> None:
        from time import time
        import time as _t

        self._store: dict[str, tuple[str, float]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        import time as _t
        if not ex:
            return False
        self._store[key] = (value, _t.time() + ex)
        return True

    async def get(self, key: str) -> str | None:
        import time as _t
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if _t.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    async def getdel(self, key: str) -> str | None:
        import time as _t
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        self._store.pop(key, None)
        if _t.time() >= expires_at:
            return None
        return value


# ---------------------------------------------------------------------------
# /docs/ls
# ---------------------------------------------------------------------------
def test_docs_ls_returns_section_items(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    fake_drive = _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.get(
        "/api/internal/docs/ls",
        params={"section": "📅 今日"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["section"] == "📅 今日"
    assert "items" in body
    assert fake_drive.folder_token  # smoke


def test_docs_ls_rejects_without_admin(client: Any, settings: Any) -> None:
    _configure(settings)
    resp = client.get("/api/internal/docs/ls")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /docs/find
# ---------------------------------------------------------------------------
def test_docs_find(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.get(
        "/api/internal/docs/find",
        params={"keyword": "AI"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["keyword"] == "AI"
    assert isinstance(body["items"], list)


# ---------------------------------------------------------------------------
# /docs/info
# ---------------------------------------------------------------------------
def test_docs_info_resolves_section(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.get(
        "/api/internal/docs/info",
        params={"path": "📅 今日"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "📅 今日"
    assert body["type"] == "folder"
    assert body["token"]


def test_docs_info_unknown_path_returns_404(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.get(
        "/api/internal/docs/info",
        params={"path": "/etc/passwd"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /docs/mkdir
# ---------------------------------------------------------------------------
def test_docs_mkdir(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/mkdir",
        json={"path": "📁 每日报告/2026-08-30"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "📁 每日报告/2026-08-30"
    assert body["token"].startswith("fld_")


def test_docs_mkdir_missing_path_returns_400(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/mkdir",
        json={},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /docs/create
# ---------------------------------------------------------------------------
def test_docs_create_child_folder(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/create",
        json={"section": "📚 信息源", "name": "News"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "News"
    assert body["section"] == "📚 信息源"


def test_docs_create_missing_name_returns_400(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/create",
        json={"section": "📅 今日"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /docs/mv
# ---------------------------------------------------------------------------
def test_docs_mv(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    fake_drive = _patch_docs_endpoints(monkeypatch, settings=settings)
    # — Pre-seed a child folder inside 📅 今日 so move has a target.
    resp = client.post(
        "/api/internal/docs/create",
        json={"section": "📅 今日", "name": "victim"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    created_token = resp.json()["token"]
    resp = client.post(
        "/api/internal/docs/mv",
        json={"path": "📅 今日/victim", "target_section": "📚 信息源"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file_token"] == created_token
    # — FakeDrive should record the move.
    moved_tokens = [m[0] for m in fake_drive.moved]
    assert created_token in moved_tokens


def test_docs_mv_unknown_path_returns_404(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/mv",
        json={"path": "📅 今日/nope", "target_section": "📚 信息源"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /docs/rename
# ---------------------------------------------------------------------------
def test_docs_rename(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    fake_drive = _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/create",
        json={"section": "📅 今日", "name": "old"},
        headers=_ADMIN_HEADERS,
    )
    created_token = resp.json()["token"]
    resp = client.post(
        "/api/internal/docs/rename",
        json={"path": "📅 今日/old", "new_name": "new"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "new"
    assert any(r[0] == created_token for r in fake_drive.renamed)


# ---------------------------------------------------------------------------
# /docs/rm + /docs/confirm (two-step confirmation)
# ---------------------------------------------------------------------------
def test_docs_rm_stages_pending_action(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    client.post(
        "/api/internal/docs/create",
        json={"section": "📅 今日", "name": "doomed"},
        headers=_ADMIN_HEADERS,
    )
    resp = client.post(
        "/api/internal/docs/rm",
        json={"path": "📅 今日/doomed"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage"] == "pending"
    assert body["kind"] == "drive_delete"
    assert body["action_id"]
    assert body["expires_at"] > 0


def test_docs_confirm_executes_pending(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    fake_drive = _patch_docs_endpoints(monkeypatch, settings=settings)
    client.post(
        "/api/internal/docs/create",
        json={"section": "📅 今日", "name": "victim"},
        headers=_ADMIN_HEADERS,
    )
    resp = client.post(
        "/api/internal/docs/rm",
        json={"path": "📅 今日/victim"},
        headers=_ADMIN_HEADERS,
    )
    action_id = resp.json()["action_id"]
    resp = client.post(
        "/api/internal/docs/confirm",
        json={"action_id": action_id},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "drive_delete"
    # — FakeDrive should have seen the delete call.
    assert any(t.startswith("fld_") for t, _ in fake_drive.deleted)


def test_docs_confirm_unknown_action_returns_404(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/confirm",
        json={"action_id": "nonexistent_id_xx"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /docs/bitable/*
# ---------------------------------------------------------------------------
def test_docs_bitable_ls(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.get("/api/internal/docs/bitable/ls", headers=_ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] >= 1
    assert body["items"][0]["name"] == "Opportunities"


def test_docs_bitable_find(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.get(
        "/api/internal/docs/bitable/find",
        params={"keyword": "AI", "table": "Opportunities"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["keyword"] == "AI"


def test_docs_bitable_add(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    resp = client.post(
        "/api/internal/docs/bitable/add",
        json={
            "table": "Opportunities",
            "fields": {"Title": "AI雷达", "Score": "88"},
        },
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record_id"].startswith("rec_")
    assert body["fields"]["Title"] == "AI雷达"


def test_docs_bitable_rm_stages_pending(
    client: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(settings)
    _patch_docs_endpoints(monkeypatch, settings=settings)
    # — Add a record first so we have something to delete.
    resp = client.post(
        "/api/internal/docs/bitable/add",
        json={"table": "Opportunities", "fields": {"Title": "x"}},
        headers=_ADMIN_HEADERS,
    )
    record_id = resp.json()["record_id"]
    resp = client.post(
        "/api/internal/docs/bitable/rm",
        json={"record_id": record_id, "table": "Opportunities"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "bitable_rm"
    assert body["record_id"] == record_id
    assert body["action_id"]
