"""Feishu content ecosystem client — Phase 7 of v2.0.

Phase 6 飞书双向通信 only pushed messages to chat. Phase 7 elevates
the bot to a content publishing surface:

  * **Docx** — `/research` and `/report` push the on-demand research
    report into a 飞书云文档 (Docx) so users can search, archive,
    and share the report instead of losing it in chat history.
  * **Bitable** — `/daily` and `/table` sync opportunity records into
    a 飞书多维表格 (Bitable) so users get a queryable, persistent
    structured view instead of ephemeral chat cards.

This module hosts two thin async clients (`FeishuDriveClient`,
`FeishuBitableClient`) that share an `_TokenMixin` for the common
plumbing: Bearer headers, error-code translation, and the
`99991663` / `99991664` token-expired retry. They piggyback on the
`FeishuAppClient` created in Phase 6 for token caching so the same
2-hour `tenant_access_token` is reused across chat + Drive + Bitable.

Reference (Feishu Open API):
  * https://open.feishu.cn/document/server-docs/docs/drive-v1/import_task/import-task-create
  * https://open.feishu.cn/document/server-docs/bitable-v1/app/create
  * https://open.feishu.cn/document/server-docs/bitable-v1/bitable-structure/create
  * https://open.feishu.cn/document/server-docs/bitable-v1/record/batch_create
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.services.feishu.app_client import FeishuAppClient, FeishuAppError
from app.utils import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://open.feishu.cn/open-apis"

# — Token-expiry codes that trigger a one-shot retry with a fresh token.
_TOKEN_EXPIRED_CODES = (99991663, 99991664)

# — Docx import polling defaults (overridable for tests via the
# `_poll_interval_sec` / `_poll_timeout_sec` constructor kwargs).
_DEFAULT_POLL_INTERVAL_SEC = 1.5
_DEFAULT_POLL_TIMEOUT_SEC = 60.0

# — Bitable bulk-insert chunk size. Feishu limits batch_create to 1000
# rows per call; 500 keeps well under that and keeps response size sane.
_BITABLE_BATCH_SIZE = 500

# — Default columns created when we auto-create an Opportunities table.
_OPP_FIELDS: list[dict[str, Any]] = [
    {"field_name": "Title",        "type": 1},     # 1 = text
    {"field_name": "Score",        "type": 1},
    {"field_name": "Category",     "type": 1},
    {"field_name": "Market Size",  "type": 1},
    {"field_name": "MVP Days",     "type": 1},
    {"field_name": "Difficulty",   "type": 1},
    {"field_name": "Radar URL",    "type": 1},
]
_OPP_TABLE_NAME = "Opportunities"


class FeishuContentError(RuntimeError):
    """Raised when a Feishu Drive or Bitable API call fails."""


class _TokenMixin:
    """Shared Bearer-header + token-expired retry plumbing.

    Sibling classes hold a reference to the Phase 6 `FeishuAppClient`
    so token caching (2-hour TTL + refresh-on-expiry) is reused instead
    of duplicated. The mixin only knows about `self.app_client`; the
    subclasses define which Feishu API family they talk to.
    """

    app_client: FeishuAppClient
    base_url: str

    async def _request(
        self,
        *,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one HTTP request against the Feishu API and translate
        the response into a dict. Retries once on `99991663` / `99991664`
        (token expired/invalid) by invalidating the cached token first.
        """
        if not self.app_client.is_configured:
            raise FeishuContentError(
                "feishu app not configured (set FEISHU_APP_ID + FEISHU_APP_SECRET)"
            )
        token = await self.app_client.get_token()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        for attempt in range(2):
            try:
                if method.upper() == "GET":
                    response = await self.app_client._http.get(url, headers=headers)
                elif method.upper() == "POST":
                    response = await self.app_client._http.post(
                        url, json=json_body or {}, headers=headers
                    )
                else:  # pragma: no cover — defensive
                    raise FeishuContentError(f"unsupported method: {method}")
            except httpx.HTTPError as exc:
                raise FeishuContentError(
                    f"{method} {path} request failed: {exc}"
                ) from exc

            if response.status_code != 200:
                raise FeishuContentError(
                    f"{method} {path} HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )

            data = response.json()
            if data.get("code") not in _TOKEN_EXPIRED_CODES:
                return data

            # — token-expired branch. Invalidate cache + retry once.
            logger.warning(
                "feishu_content_token_expired_retry",
                method=method,
                path=path,
                code=data.get("code"),
                attempt=attempt,
            )
            self.app_client._token = None  # force refresh
            token = await self.app_client.get_token()
            headers["Authorization"] = f"Bearer {token}"

        # — Second attempt also failed; surface the error.
        return data


# ---------------------------------------------------------------------------
# Drive — Docx import
# ---------------------------------------------------------------------------
class FeishuDriveClient(_TokenMixin):
    """Async client for creating 飞书云文档 (Docx) via the import-task API.

    The Feishu Drive import workflow is two-stage:

      1. `POST /drive/v1/import_tasks` with the markdown content
         base64-encoded. Feishu returns a `ticket` synchronously.
      2. Poll `GET /drive/v1/import_tasks/{ticket}` until
         `result == "success"` (or `failed` / timeout).

    Most imports complete in 1-3 seconds; the 60s default timeout is a
    safety net for unusually large reports. Tests override
    `_poll_interval_sec` and `_poll_timeout_sec` to keep the test
    suite fast.
    """

    def __init__(
        self,
        *,
        app_client: FeishuAppClient,
        settings: Optional[Settings] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        base_url: str = _BASE_URL,
        poll_interval_sec: float = _DEFAULT_POLL_INTERVAL_SEC,
        poll_timeout_sec: float = _DEFAULT_POLL_TIMEOUT_SEC,
    ) -> None:
        self.app_client = app_client
        self.settings = settings or get_settings()
        # — http_client is owned by the app_client; we don't hold
        # our own (the mixin's `_request` uses app_client._http).
        _ = http_client  # accepted for symmetry, currently unused
        self.base_url = base_url.rstrip("/")
        self._poll_interval_sec = poll_interval_sec
        self._poll_timeout_sec = poll_timeout_sec

    @property
    def is_configured(self) -> bool:
        """Whether we have a folder token to write into.

        Without one, callers should skip Docx creation entirely
        (the chat reply should still go out).
        """
        return bool((self.settings.feishu_drive_root_folder_token or "").strip())

    @property
    def folder_token(self) -> str:
        return (self.settings.feishu_drive_root_folder_token or "").strip()

    async def create_docx_from_markdown(
        self,
        *,
        title: str,
        markdown: str,
        folder_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Submit a markdown import and poll until done.

        ``folder_token`` overrides the configured root for this
        call (Phase 25 v2.1 — :class:`DriveOrgService` uses it to
        write per-date Docx into ``每日报告/YYYY-MM-DD/`` instead
        of the root folder). When omitted, falls back to
        :attr:`folder_token` (the configured root).

        Returns:
          `{"doc_id": "...", "url": "https://<tenant>.feishu.cn/docx/<id>"}`

        Raises:
          FeishuContentError — on import failure, timeout, or
            configuration error.
        """
        if not self.is_configured:
            raise FeishuContentError(
                "feishu drive not configured "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN)"
            )
        if not title.strip():
            raise FeishuContentError("create_docx_from_markdown: title is empty")
        if not markdown.strip():
            raise FeishuContentError("create_docx_from_markdown: markdown is empty")

        target_folder = (folder_token or self.folder_token or "").strip()
        if not target_folder:
            raise FeishuContentError(
                "create_docx_from_markdown: no folder_token resolved "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN or pass folder_token)"
            )

        encoded = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
        body = {
            "file_name": title.strip()[:200],
            "folder_token": target_folder,
            "type": "docx",
            "file": {"content": encoded, "mime_type": "text/markdown"},
        }
        submit = await self._request(
            method="POST", path="/drive/v1/import_tasks", json_body=body
        )
        if submit.get("code") != 0:
            raise FeishuContentError(
                f"drive/v1/import_tasks rejected: code={submit.get('code')} "
                f"msg={submit.get('msg')}"
            )
        ticket = ((submit.get("data") or {}).get("ticket") or "").strip()
        if not ticket:
            raise FeishuContentError(
                "drive/v1/import_tasks returned no ticket"
            )

        # — Poll until the import is success / failed / timeout.
        doc_id, doc_url = await self._poll_import(ticket)
        logger.info(
            "feishu_docx_imported",
            doc_id=doc_id,
            title=title[:80],
            ticket=ticket,
        )
        return {"doc_id": doc_id, "url": doc_url}

    async def _poll_import(self, ticket: str) -> tuple[str, str]:
        """Block until the import is success / failed / timeout.

        Feishu returns `{result: "success"|"pending"|"failed", token, url}`.
        """
        deadline = asyncio.get_event_loop().time() + self._poll_timeout_sec
        last_status = "pending"
        while True:
            response = await self._request(
                method="GET", path=f"/drive/v1/import_tasks/{ticket}"
            )
            if response.get("code") != 0:
                raise FeishuContentError(
                    f"drive/v1/import_tasks/{ticket} poll failed: "
                    f"code={response.get('code')} msg={response.get('msg')}"
                )
            payload = (response.get("data") or {}).get("result") or {}
            last_status = payload.get("result") or "pending"

            if last_status == "success":
                doc_id = (payload.get("token") or "").strip()
                doc_url = (payload.get("url") or "").strip()
                if not doc_id or not doc_url:
                    raise FeishuContentError(
                        "drive/v1/import_tasks success without token/url"
                    )
                return doc_id, doc_url

            if last_status == "failed":
                raise FeishuContentError(
                    f"drive/v1/import_tasks failed: {payload.get('msg') or 'unknown'}"
                )

            if asyncio.get_event_loop().time() >= deadline:
                raise FeishuContentError(
                    f"drive/v1/import_tasks poll timed out "
                    f"(last_status={last_status})"
                )

            await asyncio.sleep(self._poll_interval_sec)

    # ------------------------------------------------------------------
    # Phase 25 v2.1 — folder management (Drive Org surface)
    # ------------------------------------------------------------------
    async def create_folder(
        self,
        *,
        name: str,
        parent_token: Optional[str] = None,
    ) -> str:
        """Create a new folder under ``parent_token`` (or the root).

        ``POST /open-apis/drive/v1/files?type=folder`` — body
        ``{"folder_token": "<parent>", "name": "<name>"}``.

        Returns the new folder's ``token``. The folder name is
        idempotent at the API level (Feishu will create a duplicate
        with a numeric suffix if the name already exists), so
        production callers should use :meth:`ensure_folder_path` for
        repeat-write safety.
        """
        if not name.strip():
            raise FeishuContentError("create_folder: name is empty")
        parent = (parent_token or self.folder_token or "").strip()
        if not parent:
            raise FeishuContentError(
                "create_folder: parent_token missing "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN or pass parent_token)"
            )
        body = {"folder_token": parent, "name": name.strip()[:200]}
        response = await self._request(
            method="POST",
            path="/drive/v1/files?type=folder",
            json_body=body,
        )
        if response.get("code") != 0:
            raise FeishuContentError(
                f"drive/v1/files create_folder failed: "
                f"code={response.get('code')} msg={response.get('msg')}"
            )
        token = (response.get("data") or {}).get("token") or ""
        if not token:
            raise FeishuContentError(
                f"drive/v1/files create_folder returned no token: {response!r}"
            )
        logger.info(
            "feishu_drive_folder_created",
            name=name[:80],
            parent=parent[:24],
            token=token[:24],
        )
        return token

    async def list_children(
        self,
        *,
        folder_token: str,
    ) -> list[dict[str, Any]]:
        """List direct children of ``folder_token``.

        ``GET /open-apis/drive/v1/files?folder_token=<token>``.
        Returns the raw ``data.files`` list (each entry has
        ``name``, ``token``, ``type``). Empty list on a 404 (treated
        as "no children" — common when a folder was just deleted).
        """
        if not folder_token:
            raise FeishuContentError("list_children: folder_token missing")
        response = await self._request(
            method="GET",
            path=f"/drive/v1/files?folder_token={folder_token}",
        )
        if response.get("code") != 0:
            raise FeishuContentError(
                f"drive/v1/files list failed: "
                f"code={response.get('code')} msg={response.get('msg')}"
            )
        return list((response.get("data") or {}).get("files") or [])

    async def find_child_by_name(
        self,
        *,
        folder_token: str,
        name: str,
    ) -> Optional[str]:
        """Return the token of the first direct child whose name matches.

        Used by :meth:`ensure_folder_path` for idempotent
        nested-folder creation. Returns ``None`` when no match.
        """
        target = name.strip()
        for child in await self.list_children(folder_token=folder_token):
            if (child.get("name") or "").strip() == target:
                tok = (child.get("token") or "").strip()
                if tok:
                    return tok
        return None

    async def ensure_folder_path(
        self,
        *,
        parent_token: Optional[str],
        path: list[str],
    ) -> str:
        """Ensure each segment of ``path`` exists under ``parent_token``.

        Walks the path segment-by-segment — for each name, either
        reuses an existing folder with that name or creates a new
        one. Returns the token of the **last** segment. Idempotent:
        running twice with the same path produces the same final
        token and creates no extra folders.

        Empty segments are skipped (so callers can build the path
        conditionally without writing ``if x: path.append(x)``
        everywhere).
        """
        if not path:
            raise FeishuContentError("ensure_folder_path: path is empty")
        root = (parent_token or self.folder_token or "").strip()
        if not root:
            raise FeishuContentError(
                "ensure_folder_path: parent_token missing "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN or pass parent_token)"
            )
        current = root
        for segment in path:
            name = (segment or "").strip()
            if not name:
                continue
            existing = await self.find_child_by_name(
                folder_token=current, name=name
            )
            if existing:
                current = existing
                continue
            current = await self.create_folder(parent_token=current, name=name)
        return current


# ---------------------------------------------------------------------------
# Bitable — auto-create + bulk insert
# ---------------------------------------------------------------------------
class FeishuBitableClient(_TokenMixin):
    """Async client for the 飞书多维表格 (Bitable) API.

    Handles three concerns:

      1. **App lifecycle** — if `feishu_bitable_app_token` is empty,
         auto-creates the app on first use and logs the new token so
         operators can persist it to `.env` for cross-restart reuse.
      2. **Table lifecycle** — if the named table doesn't exist,
         creates it with the default schema.
      3. **Bulk insert** — chunked batches (max 500 rows per call)
         with auto-conversion from the internal `Opportunity` shape
         to Bitable field values.
    """

    def __init__(
        self,
        *,
        app_client: FeishuAppClient,
        settings: Optional[Settings] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        base_url: str = _BASE_URL,
        table_name: str = _OPP_TABLE_NAME,
        token_setting: Optional[str] = None,
    ) -> None:
        self.app_client = app_client
        self.settings = settings or get_settings()
        _ = http_client
        self.base_url = base_url.rstrip("/")
        self._table_name = table_name
        # — `token_setting` names which settings attr holds the Bitable
        # app_token. We support two distinct bitable apps (digest vs
        # opportunities) and need to address each by name.
        self._token_setting = token_setting or "feishu_bitable_app_token"
        self._cached_app_token: Optional[str] = None
        self._cached_table_id: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        """Whether we have a Bitable app_token (cached or in settings).

        Without one, we may auto-create on the first call.
        """
        if self._cached_app_token:
            return True
        return bool((getattr(self.settings, self._token_setting, "") or "").strip())

    async def ensure_app(self) -> str:
        """Return a usable app_token, auto-creating if necessary.

        The first call writes the new token into the in-process
        settings object + logs `feishu_bitable_app_created`. Operators
        should persist the token to `.env` for cross-restart reuse.
        """
        # — Try cached / settings first.
        existing = (
            self._cached_app_token
            or (getattr(self.settings, self._token_setting, "") or "").strip()
        )
        if existing:
            self._cached_app_token = existing
            return existing

        # — Auto-create.
        # — NOTE: Feishu's Bitable App Create response nests the
        # created app under `data.app` (not `data.app_token` directly):
        #   {
        #     "code": 0,
        #     "data": {
        #       "app": {
        #         "app_token": "bascn...",
        #         "name": "...",
        #         "folder_token": "...",
        #         "url": "..."
        #       }
        #     }
        #   }
        # Older docs / pre-v1 schemas returned `data.app_token` directly,
        # so we fall back to that path for forward compatibility.
        body = {"name": f"AI 机会雷达 - {self._table_name}"}
        response = await self._request(
            method="POST", path="/bitable/v1/apps", json_body=body
        )
        if response.get("code") != 0:
            raise FeishuContentError(
                f"bitable/v1/apps create rejected: code={response.get('code')} "
                f"msg={response.get('msg')}"
            )
        data = response.get("data") or {}
        app_token = (
            ((data.get("app") or {}).get("app_token") or "").strip()
            or ((data.get("app_token") or "").strip())  # legacy
        )
        if not app_token:
            # — Surface the raw body so operators can see what Feishu
            # actually returned (helps diagnose permission / folder
            # issues quickly).
            raise FeishuContentError(
                f"bitable/v1/apps create returned no app_token "
                f"(data keys: {list(data.keys())})"
            )

        self._cached_app_token = app_token
        # — Write back to settings so subsequent calls in the same
        # process don't re-create. Operators must persist to .env.
        setattr(self.settings, self._token_setting, app_token)
        logger.warning(
            "feishu_bitable_app_created",
            table_name=self._table_name,
            app_token=app_token,
            hint=(
                "persist this app_token to .env "
                f"({self._token_setting.upper()}) to avoid recreating on restart"
            ),
        )
        return app_token

    async def ensure_table(self) -> tuple[str, str]:
        """Return `(app_token, table_id)` for the table this client manages.

        Auto-creates the app (via `ensure_app`) and the table itself
        (with default schema) on first use. Idempotent: subsequent
        calls reuse existing resources.
        """
        if self._cached_app_token and self._cached_table_id:
            return self._cached_app_token, self._cached_table_id

        app_token = await self.ensure_app()

        # — List existing tables.
        list_path = f"/bitable/v1/apps/{app_token}/tables"
        response = await self._request(method="GET", path=list_path)
        if response.get("code") != 0:
            raise FeishuContentError(
                f"bitable/v1/apps/{app_token}/tables list rejected: "
                f"code={response.get('code')} msg={response.get('msg')}"
            )
        items = ((response.get("data") or {}).get("items") or [])
        for item in items:
            if item.get("name") == self._table_name:
                table_id = (item.get("table_id") or "").strip()
                if table_id:
                    self._cached_app_token = app_token
                    self._cached_table_id = table_id
                    return app_token, table_id

        # — Not found → create.
        create_path = f"/bitable/v1/apps/{app_token}/tables"
        create_response = await self._request(
            method="POST",
            path=create_path,
            json_body={"table": {"name": self._table_name}},
        )
        if create_response.get("code") != 0:
            raise FeishuContentError(
                f"bitable table create rejected: code={create_response.get('code')} "
                f"msg={create_response.get('msg')}"
            )
        table_id = (
            (create_response.get("data") or {}).get("table_id") or ""
        ).strip()
        if not table_id:
            raise FeishuContentError("bitable table create returned no table_id")

        # — Create default fields.
        for field in _OPP_FIELDS:
            field_path = (
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
            )
            field_resp = await self._request(
                method="POST", path=field_path, json_body=field
            )
            if field_resp.get("code") != 0:
                logger.warning(
                    "feishu_bitable_field_create_failed",
                    field_name=field["field_name"],
                    code=field_resp.get("code"),
                    msg=field_resp.get("msg"),
                )
                # — Keep going; one bad field shouldn't block everything.

        self._cached_app_token = app_token
        self._cached_table_id = table_id
        logger.info(
            "feishu_bitable_table_ready",
            table_name=self._table_name,
            app_token=app_token,
            table_id=table_id,
        )
        return app_token, table_id

    async def bulk_insert_opportunities(
        self,
        *,
        items: list[dict[str, Any]],
        base_url_for_links: str = "http://localhost:3000",
        chunk_size: int = _BITABLE_BATCH_SIZE,
    ) -> int:
        """Convert `Opportunity` dicts to Bitable rows and insert.

        Args:
          items: list of dicts with `id`, `title`, `total_score`, etc.
          base_url_for_links: public URL prefix for the "Radar URL" column.
          chunk_size: rows per `batch_create` call (Feishu limit is 1000;
            we default to 500 to keep response sizes sane).

        Returns:
          Total rows inserted across all chunks.
        """
        if not items:
            return 0

        app_token, table_id = await self.ensure_table()

        total_inserted = 0
        for chunk_start in range(0, len(items), chunk_size):
            chunk = items[chunk_start : chunk_start + chunk_size]
            records = [
                _opp_to_bitable_fields(item, base_url_for_links) for item in chunk
            ]
            chunk_path = (
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
            )
            response = await self._request(
                method="POST",
                path=chunk_path,
                json_body={"records": records},
            )
            if response.get("code") != 0:
                raise FeishuContentError(
                    f"bitable batch_create rejected: code={response.get('code')} "
                    f"msg={response.get('msg')}"
                )
            total_inserted += len(chunk)
        logger.info(
            "feishu_bitable_bulk_insert",
            table_name=self._table_name,
            inserted=total_inserted,
            chunks=((len(items) + chunk_size - 1) // chunk_size),
        )
        return total_inserted

    def public_url(self, *, app_token: str | None = None) -> str:
        """Return the public Bitable URL for the current app.

        Feishu Bitable URLs follow the shape
        `https://<tenant>.feishu.cn/base/<app_token>`. The tenant
        subdomain is part of the Feishu tenant; we don't have it here,
        so we return the canonical URL — the user clicking through
        resolves the tenant automatically.
        """
        token = app_token or self._cached_app_token or (
            getattr(self.settings, self._token_setting, "") or ""
        ).strip()
        if not token:
            return ""
        return f"https://feishu.cn/base/{token}"


def _opp_to_bitable_fields(
    opp: dict[str, Any], base_url: str
) -> dict[str, Any]:
    """Map one `Opportunity` dict to Bitable `fields` shape.

    Bitable `fields` is `{ "<column_name>": <value> }` — for a Text
    column the value is a string. We strip + truncate so very long
    titles don't blow up the cell.
    """
    opp_id = opp.get("id")
    title = (opp.get("title") or "(无标题)").strip()[:200]
    score = opp.get("total_score")
    score_str = "" if score is None else str(int(round(float(score))))
    category = (opp.get("category") or "").strip()[:100]
    market_size = (opp.get("market_size") or "").strip()[:100]
    mvp_days = opp.get("mvp_days")
    mvp_days_str = "" if mvp_days is None else str(int(mvp_days))
    difficulty = (opp.get("difficulty") or "").strip()[:50]
    radar_url = f"{base_url.rstrip('/')}/opportunities/{opp_id}" if opp_id else ""

    return {
        "fields": {
            "Title": title,
            "Score": score_str,
            "Category": category,
            "Market Size": market_size,
            "MVP Days": mvp_days_str,
            "Difficulty": difficulty,
            "Radar URL": radar_url,
        }
    }


__all__ = [
    "FeishuContentError",
    "FeishuDriveClient",
    "FeishuBitableClient",
]