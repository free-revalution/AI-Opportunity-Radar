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

    async def find_child_by_name(self, *, folder_token: str, name: str) -> dict | None:
        matches = await self.list_children(folder_token=folder_token, name=name)
        return matches[0] if matches else None

    async def ensure_folder_path(self, *, parent_token: str, path: list[str]) -> str:
        cur = parent_token
        for name in path:
            found = await self.find_child_by_name(folder_token=cur, name=name)
            if found is not None:
                cur = found["token"]
                continue
            cur = await self.create_folder(name=name, parent_token=cur)
        return cur

    async def create_docx_from_markdown(self, *, title: str, markdown: str, folder_token: str | None = None) -> dict[str, Any]:
        return {
            "doc_id": f"doc_{title[:8]}",
            "url": f"https://feishu.cn/docx/{title[:8]}",
            "folder_token": folder_token or self.folder_token,
        }


def _configure(settings: Any, *, root: str = "root_folder_token") -> Any:
    settings.feishu_drive_root_folder_token = root
    return settings


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
        resp = client.get("/api/internal/docs/tree")
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
    resp = client.get("/api/internal/docs/tree")
    assert resp.status_code == 503
    assert "FEISHU_DRIVE_ROOT_FOLDER_TOKEN" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/internal/docs/daily
# ---------------------------------------------------------------------------
def test_docs_daily_unknown_date_returns_found_false(client: Any) -> None:
    resp = client.get("/api/internal/docs/daily?date=2020-01-01")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"found": False, "date": "2020-01-01"}


def test_docs_daily_invalid_date_returns_400(client: Any) -> None:
    resp = client.get("/api/internal/docs/daily?date=not-a-date")
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
    resp = client.get("/api/internal/docs/daily?date=2026-08-30")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["found"] is True
    assert body["date"] == "2026-08-30"
    assert body["doc_id"] == "doc_seed_001"
    assert body["doc_url"] == "https://feishu.cn/docx/seed"
    assert body["run_id"] == 99
    assert body["raw_count"] == 10
    assert body["signal_count"] == 4
