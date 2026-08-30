"""Phase 26 — BitableManager.

Thin orchestration layer over :class:`FeishuBitableClient` +
:class:`ConfirmStore`. Used by the bot's ``/docs bitable:*``
sub-commands and the matching ``/api/internal/docs/bitable/*``
endpoints. Mirrors :class:`DriveManager` but is intentionally
smaller — Bitable ops don't need the path resolver or tree walk
since the bot only ever addresses one table at a time.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config import Settings
from app.services.feishu.confirm_store import (
    ConfirmStore,
    ConfirmStoreUnavailable,
    PendingAction,
)
from app.services.feishu.content_client import FeishuBitableClient, FeishuContentError
from app.utils import get_logger

logger = get_logger(__name__)


class BitableManager:
    """High-level 飞书多维表格 management surface for the bot."""

    def __init__(
        self,
        *,
        client: FeishuBitableClient,
        settings: Optional[Settings] = None,
        confirm_store: Optional[ConfirmStore] = None,
    ) -> None:
        self.client = client
        self.settings = settings or client.settings
        self.confirm_store = confirm_store

    async def list_tables(self) -> list[dict[str, Any]]:
        """List tables in the configured Bitable app."""
        return await self.client.list_tables()

    async def find_records(
        self,
        *,
        table_name: Optional[str] = None,
        keyword: str = "",
        limit: int = 10,
        table_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Find records by substring match across their ``fields`` values.

        ``table_name`` is resolved by listing the app's tables; pass
        ``table_id`` to skip that lookup. ``keyword`` is matched
        case-insensitively against the stringified value of each
        field. Returns up to ``limit`` matching records, each shaped
        ``{"record_id", "fields", "matched_field"}``.
        """
        app_token, resolved_table_id = await self._resolve_table(
            table_name=table_name, table_id=table_id
        )
        items: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        kw_lower = (keyword or "").strip().lower()
        if not kw_lower:
            # — No keyword → return first `limit` rows.
            while True:
                records, page_token = await self.client.list_records(
                    app_token=app_token,
                    table_id=resolved_table_id,
                    page_size=min(limit, 100),
                    page_token=page_token,
                )
                items.extend(records)
                if len(items) >= limit or not page_token:
                    return items[:limit]
        while True:
            records, page_token = await self.client.list_records(
                app_token=app_token,
                table_id=resolved_table_id,
                page_size=100,
                page_token=page_token,
            )
            for rec in records:
                matched = _match_record(rec, kw_lower)
                if matched is not None:
                    items.append(
                        {
                            "record_id": rec.get("record_id"),
                            "fields": rec.get("fields") or {},
                            "matched_field": matched,
                        }
                    )
                    if len(items) >= limit:
                        return items
            if not page_token:
                break
        return items

    async def add_record(
        self,
        *,
        table_name: Optional[str] = None,
        fields: dict[str, Any],
        table_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create one record in the named table.

        ``fields`` is the dict that maps each Bitable column name to
        its value (string for text columns, etc.). Returns the
        created record with its ``record_id``.
        """
        app_token, resolved_table_id = await self._resolve_table(
            table_name=table_name, table_id=table_id
        )
        return await self.client.create_record(
            app_token=app_token,
            table_id=resolved_table_id,
            fields=fields,
        )

    async def update_record(
        self,
        *,
        table_name: Optional[str] = None,
        record_id: str = "",
        fields: dict[str, Any],
        table_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Update one record (partial overwrite)."""
        app_token, resolved_table_id = await self._resolve_table(
            table_name=table_name, table_id=table_id
        )
        return await self.client.update_record(
            app_token=app_token,
            table_id=resolved_table_id,
            record_id=record_id,
            fields=fields,
        )

    # ------------------------------------------------------------------
    # Destructive — two-step confirmation
    # ------------------------------------------------------------------
    async def request_delete(
        self,
        *,
        record_id: str,
        table_name: Optional[str] = None,
        table_id: Optional[str] = None,
    ) -> PendingAction:
        """Stage a record delete — returns a :class:`PendingAction`."""
        if not record_id:
            raise FeishuContentError("request_delete: record_id required")
        if self.confirm_store is None:
            raise ConfirmStoreUnavailable(
                "ConfirmStore not wired — destructive actions disabled"
            )
        app_token, resolved_table_id = await self._resolve_table(
            table_name=table_name, table_id=table_id
        )
        return await self.confirm_store.create(
            kind="bitable_rm",
            payload={
                "record_id": record_id,
                "app_token": app_token,
                "table_id": resolved_table_id,
            },
        )

    async def execute_delete(self, *, action: PendingAction) -> dict[str, Any]:
        """Actually delete the record behind ``action`` (called by
        ``/docs confirm``)."""
        if action.kind != "bitable_rm":
            raise FeishuContentError(
                f"execute_delete: wrong kind {action.kind!r}"
            )
        payload = action.payload
        return await self.client.delete_record(
            app_token=payload.get("app_token") or None,
            table_id=payload.get("table_id") or "",
            record_id=payload.get("record_id") or "",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _resolve_table(
        self,
        *,
        table_name: Optional[str],
        table_id: Optional[str],
    ) -> tuple[str, str]:
        """Return ``(app_token, table_id)`` for the requested table.

        Resolution order:
          1. ``table_id`` (if provided) + the client's cached/auto-created app_token.
          2. ``table_name`` lookup via ``list_tables``.
          3. Fall back to ``ensure_table`` (creates the default
             ``Opportunities`` table when neither matches).
        """
        if table_id:
            app_token = await self.client.ensure_app()
            return app_token, table_id
        # — If table_name provided, find it among existing tables.
        if table_name:
            app_token = await self.client.ensure_app()
            tables = await self.client.list_tables(app_token=app_token)
            for t in tables:
                if (t.get("name") or "").strip() == table_name.strip():
                    tid = (t.get("table_id") or "").strip()
                    if tid:
                        return app_token, tid
            raise FeishuContentError(
                f"bitable: no table named {table_name!r} "
                f"(known: {[t.get('name') for t in tables]})"
            )
        # — Neither provided → ensure the default table.
        return await self.client.ensure_table()


def _match_record(record: dict[str, Any], keyword_lower: str) -> Optional[str]:
    """Return the first field name whose stringified value contains
    ``keyword_lower`` (case-insensitive), or ``None``."""
    fields = record.get("fields") or {}
    if not isinstance(fields, dict):
        return None
    for key, value in fields.items():
        text = _stringify(value)
        if keyword_lower in text.lower():
            return key
    return None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_stringify(v) for v in value)
    if isinstance(value, dict):
        return " ".join(f"{k}={_stringify(v)}" for k, v in value.items())
    return str(value)


__all__ = ["BitableManager"]
