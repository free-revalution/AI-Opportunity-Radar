"""Phase 26 — DriveManager.

Orchestration layer over :class:`FeishuDriveClient` +
:class:`DriveOrgService` + :class:`ConfirmStore`. The bot's
``/docs`` sub-commands never call :class:`FeishuDriveClient`
directly — they go through this manager so:

  * the 4-section resolution (`resolve_path`) is centralised,
  * destructive actions are gated through ``ConfirmStore``
    so operators have to type ``/docs confirm <token>`` after
    the first ``/docs rm``,
  * the anti-traversal guard (``within_root``) lives in one place
    rather than being duplicated in every handler.

The manager is **stateful per construction** — callers typically
create one per request scope and discard. Cache the underlying
``FeishuDriveClient`` separately if the bot needs long-running
state across requests.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config import Settings, get_settings
from app.services.feishu.confirm_store import (
    ConfirmStore,
    ConfirmStoreUnavailable,
    PendingAction,
)
from app.services.feishu.content_client import (
    FeishuContentError,
    FeishuDriveClient,
)
from app.services.feishu.drive_org import (
    SECTION_DAILY,
    SECTION_HOME,
    SECTION_SOURCES,
    SECTION_TODAY,
    DriveNode,
    DriveOrgService,
)
from app.utils import get_logger

logger = get_logger(__name__)


class DriveManager:
    """High-level 飞书云盘 management surface for the bot."""

    SECTION_SECTION_TOKEN_KEYS = {
        SECTION_HOME: "home",
        SECTION_TODAY: "today",
        SECTION_DAILY: "daily_reports",
        SECTION_SOURCES: "sources",
    }

    def __init__(
        self,
        *,
        drive: FeishuDriveClient,
        settings: Optional[Settings] = None,
        org: Optional[DriveOrgService] = None,
        confirm_store: Optional[ConfirmStore] = None,
    ) -> None:
        self.drive = drive
        self.settings = settings or drive.settings
        self.org = org or DriveOrgService(drive=drive, settings=self.settings)
        self.confirm_store = confirm_store

    # ------------------------------------------------------------------
    # Read surface
    # ------------------------------------------------------------------
    async def ensure_tree(self) -> dict[str, str]:
        """Ensure the 4 sections exist; return ``{section: token}``."""
        tokens = await self.org.ensure_root_tree()
        return tokens.as_dict()

    async def walk(self, *, max_depth: int = 3) -> dict[str, Any]:
        """Return the section tree as a nested dict.

        Calls :meth:`ensure_tree` first so the 4 sections are present
        even when the operator never explicitly initialised them.
        """
        await self.ensure_tree()
        return await self.org.walk_tree(max_depth=max_depth)

    async def resolve(self, *, path: str) -> Optional[DriveNode]:
        """Resolve a user path to a :class:`DriveNode` (or ``None``).

        Calls :meth:`ensure_tree` first so the 4 sections exist —
        ``resolve_path`` reads the tree, it doesn't create it.
        """
        await self.ensure_tree()
        return await self.org.resolve_path(path=path)

    async def list_section(
        self,
        *,
        section: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """List direct children of a top-level section.

        ``section`` accepts both the Chinese display name
        (``"📅 今日"``) and the English key (``"today"``). Returns
        the raw ``list_children`` payload (each item has ``token``,
        ``name``, ``type``).
        """
        tokens = await self.org.ensure_root_tree()
        section_token = self._section_token(section, tokens)
        if not section_token:
            return []
        items = await self.drive.list_children(folder_token=section_token)
        return items[:limit]

    async def find_files(
        self,
        *,
        keyword: str,
        scope: str = "all",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search by name across the 4 sections.

        ``scope`` ∈ ``"all"`` / ``"home"`` / ``"today"`` /
        ``"daily_reports"`` / ``"sources"``. Returns a list of
        ``{"name", "token", "type", "section"}`` dicts.
        """
        tokens = await self.org.ensure_root_tree()
        kw = (keyword or "").strip()
        if not kw:
            return []
        out: list[dict[str, Any]] = []
        sections = (
            list(self.SECTION_SECTION_TOKEN_KEYS.items())
            if scope == "all"
            else [
                (k, v)
                for k, v in self.SECTION_SECTION_TOKEN_KEYS.items()
                if v == scope
            ]
        )
        for section_name, _key in sections:
            section_token = self._section_token(section_name, tokens)
            if not section_token:
                continue
            hits = await self.drive.search_files(
                folder_token=section_token, keyword=kw, limit=limit
            )
            for h in hits:
                out.append({**h, "section": section_name})
                if len(out) >= limit:
                    return out
        return out

    # ------------------------------------------------------------------
    # Write surface — non-destructive
    # ------------------------------------------------------------------
    async def create_child_folder(
        self,
        *,
        section: str,
        name: str,
    ) -> dict[str, Any]:
        """Create a child folder inside ``section``.

        ``section`` accepts the Chinese display name or English key.
        Returns ``{"token", "section", "name"}``.
        """
        tokens = await self.org.ensure_root_tree()
        section_token = self._section_token(section, tokens)
        if not section_token:
            raise FeishuContentError(
                f"create_child_folder: unknown section {section!r}"
            )
        clean = (name or "").strip()
        if not clean:
            raise FeishuContentError("create_child_folder: name is empty")
        token = await self.drive.create_folder(
            parent_token=section_token, name=clean
        )
        return {
            "token": token,
            "section": section,
            "name": clean,
        }

    async def mkdir_path(self, *, path: str) -> dict[str, Any]:
        """Create a nested folder path, anchored at the root.

        ``path`` is slash-joined (e.g. ``"📚 信息源/AI"``).
        Returns ``{"token", "path"}``.
        """
        if not path or not path.strip():
            raise FeishuContentError("mkdir_path: path is empty")
        segments = [
            s.strip() for s in path.replace("\\", "/").split("/") if s.strip()
        ]
        if not segments:
            raise FeishuContentError("mkdir_path: path has no segments")
        # — Root walk — first segment must be one of the 4 sections.
        if not self._is_top_level(segments[0]):
            raise FeishuContentError(
                f"mkdir_path: top-level must be one of "
                f"{list(self.SECTION_SECTION_TOKEN_KEYS)}"
            )
        # — resolve or create top-level section (idempotent), then
        # walk the remainder.
        tokens = await self.org.ensure_root_tree()
        root = tokens.get(self._section_key(segments[0])) or self.drive.folder_token
        current = await self.drive.ensure_folder_path(
            parent_token=root, path=segments[1:]
        )
        return {"token": current, "path": "/".join(segments)}

    async def move_to_section(
        self,
        *,
        file_token: str,
        file_type: str,
        target_section: str,
    ) -> dict[str, Any]:
        """Move ``file_token`` into ``target_section``.

        Returns ``{"file_token", "target_folder_token"}``.
        """
        if not await self.within_root(token=file_token):
            raise FeishuContentError(
                "move_to_section: file_token outside root (refused)"
            )
        tokens = await self.org.ensure_root_tree()
        target_token = self._section_token(target_section, tokens)
        if not target_token:
            raise FeishuContentError(
                f"move_to_section: unknown section {target_section!r}"
            )
        return await self.drive.move_file(
            file_token=file_token,
            target_folder_token=target_token,
            file_type=file_type,
        )

    async def rename(
        self,
        *,
        file_token: str,
        file_type: str,
        new_name: str,
    ) -> dict[str, Any]:
        """Rename ``file_token`` in place."""
        if not await self.within_root(token=file_token):
            raise FeishuContentError(
                "rename: file_token outside root (refused)"
            )
        return await self.drive.rename_file(
            file_token=file_token,
            new_name=new_name,
            file_type=file_type,
        )

    # ------------------------------------------------------------------
    # Write surface — destructive (two-step confirmation)
    # ------------------------------------------------------------------
    async def request_delete(self, *, path: str) -> PendingAction:
        """Stage a delete — returns a :class:`PendingAction` with a token.

        Caller MUST echo the ``action_id`` to the operator and require
        them to send ``/docs confirm <action_id>`` to actually run
        the delete.
        """
        node = await self.resolve(path=path)
        if node is None:
            raise FeishuContentError(
                f"request_delete: cannot resolve {path!r}"
            )
        if not await self.within_root(token=node.token):
            raise FeishuContentError(
                f"request_delete: {path!r} resolves outside root (refused)"
            )
        if self.confirm_store is None:
            raise ConfirmStoreUnavailable(
                "ConfirmStore not wired — destructive actions disabled"
            )
        return await self.confirm_store.create(
            kind="drive_delete",
            payload={
                "path": path,
                "token": node.token,
                "type": node.type,
                "name": node.name,
            },
        )

    async def execute_delete(self, *, action: PendingAction) -> dict[str, Any]:
        """Actually delete the file behind ``action`` (called by
        ``/docs confirm``)."""
        if action.kind != "drive_delete":
            raise FeishuContentError(
                f"execute_delete: wrong kind {action.kind!r}"
            )
        payload = action.payload
        file_token = (payload.get("token") or "").strip()
        file_type = (payload.get("type") or "folder").strip() or "folder"
        if not file_token:
            raise FeishuContentError(
                "execute_delete: action missing file token"
            )
        if not await self.within_root(token=file_token):
            raise FeishuContentError(
                "execute_delete: target outside root (refused)"
            )
        submission = await self.drive.delete_file(
            file_token=file_token,
            file_type=file_type,
        )
        # — Poll the delete task so the IM reply reflects the actual
        # outcome (success / failed / timeout). Capped at 30s so we
        # don't blow Feishu's per-event reply window.
        poll = await self.drive.poll_delete_task(
            task_id=submission["task_id"],
            timeout_sec=30.0,
            interval_sec=1.5,
        )
        return {
            "path": payload.get("path"),
            "name": payload.get("name"),
            "submission": submission,
            "poll": poll,
        }

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------
    async def within_root(self, *, token: str) -> bool:
        """Anti-traversal guard — is ``token`` a descendant of root?

        Backed by :meth:`DriveOrgService.path_within_root` which
        BFS-walks the tree from the configured root token. Empty
        ``token`` short-circuits to ``True`` so callers can pass
        through unconfigured values safely.
        """
        return await self.org.path_within_root(token=token)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _section_token(
        self,
        section: str,
        tokens: dict[str, str],
    ) -> Optional[str]:
        """Map a section display name or English key to its token."""
        if not section:
            return None
        if section in self.SECTION_SECTION_TOKEN_KEYS:
            key = self.SECTION_SECTION_TOKEN_KEYS[section]
            return tokens.get(key)
        # — English key?
        if section in tokens:
            return tokens.get(section)
        return None

    def _section_key(self, section: str) -> Optional[str]:
        """Reverse of :meth:`_section_token` — display name or English
        key → English key."""
        if section in self.SECTION_SECTION_TOKEN_KEYS:
            return self.SECTION_SECTION_TOKEN_KEYS[section]
        if section in self.SECTION_SECTION_TOKEN_KEYS.values():
            return section
        return None

    def _is_top_level(self, segment: str) -> bool:
        return _is_top_level_segment(segment)


def _is_top_level_segment(segment: str) -> bool:
    """True if ``segment`` is one of the 4 root section names."""
    return segment.strip() in DriveManager.SECTION_SECTION_TOKEN_KEYS


__all__ = ["DriveManager"]
