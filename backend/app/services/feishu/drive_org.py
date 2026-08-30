"""Phase 25 v2.1 — 飞书云盘 4 段结构编排 (Drive Org service).

Implements the simplify-plan §27 4-section Drive Org layout:

  <root>
  ├── 📌 首页        (HomePage)
  ├── 📅 今日        (Today folder)
  ├── 📁 每日报告    (Daily Reports folder)
  │   ├── YYYY-MM-DD          (per-day subfolder)
  │   │   └── YYYY-MM-DD AI 商业日报   (Docx — the digest content)
  │   ├── YYYY-MM-DD (older)
  │   └── …
  └── 📚 信息源      (Sources)

The bot keeps three responsibilities in this layout:

  1. **Ensure the 4 root children exist** (idempotent — re-runs
     find-or-create each section by name).
  2. **Write the per-day Docx** under ``每日报告/YYYY-MM-DD/``
     with a deterministic title so the docx search surface
     (and the operator) can find it by date.
  3. **Persist the date → doc_id mapping** to the
     ``daily_digest_docs`` table so internal APIs can resolve
     a date back to the Docx URL.

Folder / file creation is delegated to :class:`FeishuDriveClient`
(the existing Phase 7 client). The two pieces — folder + Docx —
both go through :func:`ensure_folder_path` and
:func:`create_docx_from_markdown` so the per-folder creation
flow is already idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import DailyDigestDoc
from app.services.feishu.content_client import FeishuContentError, FeishuDriveClient
from app.utils import get_logger

logger = get_logger(__name__)


# --- Section names (Chinese, kept here so the bot reply can
# echo them when it shows the tree to the user).
SECTION_HOME = "📌 首页"
SECTION_TODAY = "📅 今日"
SECTION_DAILY = "📁 每日报告"
SECTION_SOURCES = "📚 信息源"


@dataclass(slots=True)
class RootTokens:
    """Tokens of the 4 root sections + the root itself.

    Acts like a dict (``as_dict`` / ``__getitem__`` / ``get``) so
    callers can use either dataclass-attribute or dict-key style
    interchangeably — DriveManager and Phase 26 code paths do both.
    """

    root: str
    home: str
    today: str
    daily_reports: str
    sources: str

    def as_dict(self) -> dict[str, str]:
        return {
            "root": self.root,
            "home": self.home,
            "today": self.today,
            "daily_reports": self.daily_reports,
            "sources": self.sources,
        }

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return getattr(self, key, default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(self, key)


@dataclass(slots=True)
class DailyDocRef:
    """Result of writing a per-day Docx into the Drive Org tree."""

    date: date
    doc_id: str
    doc_url: str
    folder_token: str
    run_id: Optional[int] = None
    raw_count: int = 0
    signal_count: int = 0


@dataclass(slots=True)
class DriveNode:
    """Resolved drive node — returned by :meth:`DriveOrgService.resolve_path`.

    ``path`` is the user-friendly slash-joined form
    (e.g. ``"📁 每日报告/2026-08-30"``); ``token`` is the Feishu token
    usable with the lower-level Drive client; ``type`` is the Feishu
    file type (``"folder"`` / ``"docx"`` / ``"file"``).
    """

    name: str
    token: str
    type: str
    path: str  # human-readable, slash-joined
    parent_path: Optional[str] = None  # for nested resolution


# Top-level section names — kept in a tuple so `resolve_path` can
# detect a user trying to escape the root via absolute paths.
_VALID_TOP_LEVEL_SECTIONS: tuple[str, ...] = (
    SECTION_HOME,
    SECTION_TODAY,
    SECTION_DAILY,
    SECTION_SOURCES,
)


def _is_valid_top_level(segment: str) -> bool:
    return segment.strip() in _VALID_TOP_LEVEL_SECTIONS


class DriveOrgService:
    """Orchestrates the 飞书云盘 4 段结构 (Drive Org) tree.

    Stateless beyond the constructor — every call re-reads the
    current root folder token from :class:`Settings`. Idempotent
    end-to-end so the daily cron can safely re-run on the same
    day without creating duplicate folders or docx.
    """

    def __init__(
        self,
        *,
        drive: FeishuDriveClient,
        settings: Optional[Settings] = None,
        session: Optional[AsyncSession] = None,
    ) -> None:
        self.drive = drive
        self.settings = settings or drive.settings
        self.session = session

    # ------------------------------------------------------------------
    # Tree management
    # ------------------------------------------------------------------
    async def ensure_root_tree(self) -> RootTokens:
        """Ensure each of the 4 sections exists; return their tokens.

        Uses the configured ``feishu_drive_root_folder_token`` as
        the root. Each section is created via
        :meth:`ensure_folder_path` so re-runs are safe.
        """
        if not self.drive.is_configured:
            raise FeishuContentError(
                "drive_org: feishu drive not configured "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN)"
            )
        root = self.drive.folder_token
        # — Each section gets its own sub-folder. ``ensure_folder_path``
        # handles the "already exists" branch by name lookup.
        home_token = await self.drive.ensure_folder_path(
            parent_token=root, path=[SECTION_HOME]
        )
        today_token = await self.drive.ensure_folder_path(
            parent_token=root, path=[SECTION_TODAY]
        )
        daily_token = await self.drive.ensure_folder_path(
            parent_token=root, path=[SECTION_DAILY]
        )
        sources_token = await self.drive.ensure_folder_path(
            parent_token=root, path=[SECTION_SOURCES]
        )
        logger.info(
            "drive_org_root_tree_ensured",
            root=root[:24],
            sections=4,
        )
        return RootTokens(
            root=root,
            home=home_token,
            today=today_token,
            daily_reports=daily_token,
            sources=sources_token,
        )

    # ------------------------------------------------------------------
    # Per-day digest writing
    # ------------------------------------------------------------------
    async def write_daily_digest(
        self,
        *,
        day: date,
        markdown: str,
        title: Optional[str] = None,
        run_id: Optional[int] = None,
        raw_count: int = 0,
        signal_count: int = 0,
    ) -> DailyDocRef:
        """Write ``markdown`` into ``每日报告/YYYY-MM-DD/<title>.docx``.

        Idempotent: if a ``DailyDigestDoc`` already exists for
        ``day``, the existing doc_id + doc_url are returned and
        no new docx is created. The folder walk also reuses
        existing folders — see :meth:`ensure_folder_path`.

        The ``DailyDigestDoc`` row is written when ``session`` is
        provided; otherwise the caller can persist it later via
        :func:`persist_daily_doc`.
        """
        if not self.drive.is_configured:
            raise FeishuContentError(
                "drive_org: feishu drive not configured "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN)"
            )
        # — Idempotency check: same-day re-run returns the prior doc.
        if self.session is not None:
            existing = await self.session.get(DailyDigestDoc, day)
            if existing is not None:
                logger.info(
                    "drive_org_daily_doc_already_exists",
                    day=str(day),
                    doc_id=existing.doc_id[:24],
                )
                return DailyDocRef(
                    date=existing.date,
                    doc_id=existing.doc_id,
                    doc_url=existing.doc_url,
                    folder_token=existing.folder_token,
                    run_id=existing.run_id,
                    raw_count=existing.raw_count,
                    signal_count=existing.signal_count,
                )

        # — Walk the per-day folder path.
        day_str = day.strftime("%Y-%m-%d")
        daily_root = await self.drive.ensure_folder_path(
            parent_token=self.drive.folder_token, path=[SECTION_DAILY]
        )
        day_folder = await self.drive.ensure_folder_path(
            parent_token=daily_root, path=[day_str]
        )

        # — Docx title — operators read this in the cloud drive UI.
        effective_title = (title or f"{day_str} AI 商业日报").strip()[:200]
        created = await self.drive.create_docx_from_markdown(
            title=effective_title,
            markdown=markdown,
            folder_token=day_folder,
        )

        ref = DailyDocRef(
            date=day,
            doc_id=created["doc_id"],
            doc_url=created["url"],
            folder_token=day_folder,
            run_id=run_id,
            raw_count=raw_count,
            signal_count=signal_count,
        )

        # — Persist to the index table when we have a session.
        if self.session is not None:
            await self.persist_daily_doc(ref)
        logger.info(
            "drive_org_daily_doc_written",
            day=day_str,
            doc_id=ref.doc_id[:24],
            run_id=run_id,
        )
        return ref

    async def persist_daily_doc(self, ref: DailyDocRef) -> None:
        """Upsert a ``DailyDigestDoc`` row for ``ref``.

        Uses a real UPSERT (insert-or-replace on PK) so a
        manual re-write of the same day's docx replaces the
        prior row's doc_id / doc_url but keeps ``created_at``.
        """
        if self.session is None:
            raise RuntimeError(
                "persist_daily_doc requires a session "
                "(set session= when constructing DriveOrgService)"
            )
        # — Try insert; on PK conflict update.
        stmt_select = select(DailyDigestDoc).where(DailyDigestDoc.date == ref.date)
        existing = (await self.session.execute(stmt_select)).scalar_one_or_none()
        if existing is None:
            row = DailyDigestDoc(
                date=ref.date,
                doc_id=ref.doc_id,
                doc_url=ref.doc_url,
                folder_token=ref.folder_token,
                run_id=ref.run_id,
                raw_count=ref.raw_count,
                signal_count=ref.signal_count,
            )
            self.session.add(row)
        else:
            existing.doc_id = ref.doc_id
            existing.doc_url = ref.doc_url
            existing.folder_token = ref.folder_token
            existing.run_id = ref.run_id
            existing.raw_count = ref.raw_count
            existing.signal_count = ref.signal_count
        await self.session.flush()

    # ------------------------------------------------------------------
    # Read helpers — used by ``/api/internal/docs/*`` endpoints.
    # ------------------------------------------------------------------
    async def get_daily_doc(self, day: date) -> Optional[DailyDigestDoc]:
        """Fetch the ``DailyDigestDoc`` for ``day`` (or ``None``)."""
        if self.session is None:
            raise RuntimeError(
                "get_daily_doc requires a session "
                "(set session= when constructing DriveOrgService)"
            )
        return await self.session.get(DailyDigestDoc, day)

    # ------------------------------------------------------------------
    # Phase 26 — path resolution + tree walk (used by /docs commands)
    # ------------------------------------------------------------------
    async def resolve_path(self, *, path: str) -> Optional[DriveNode]:
        """Walk a user-supplied ``"📅 今日/foo/bar"`` against the tree.

        Rules:

        * The first segment must be one of the 4 root sections
          (anti-traversal — prevents ``..`` / ``/`` / arbitrary paths
          from escaping the root).
        * Empty segments are skipped (``a//b`` is treated as ``a/b``).
        * Returns ``None`` when any segment doesn't resolve.
        * ``path`` with no separator returns the top-level section.

        The returned :class:`DriveNode` carries the resolved
        ``token``, ``type``, and the human-readable ``path`` that was
        walked (useful for replies like "moved 旧报告.docx from
        每日报告/2026-08-30 to 信息源").
        """
        if not path or not path.strip():
            return None
        if not self.drive.is_configured:
            raise FeishuContentError(
                "drive_org: feishu drive not configured "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN)"
            )
        segments = [seg.strip() for seg in path.replace("\\", "/").split("/") if seg.strip()]
        if not segments:
            return None
        head = segments[0]
        if not _is_valid_top_level(head):
            # — refuse to walk paths that don't start with one of the
            # 4 known sections. This is the anti-traversal guard.
            return None

        # — Locate the section token (don't re-create on resolve).
        section_token = await self.drive.find_child_by_name(
            folder_token=self.drive.folder_token, name=head
        )
        if not section_token:
            return None

        current_token = section_token
        current_type = "folder"
        walked_path = head
        parent_path: Optional[str] = None

        for segment in segments[1:]:
            child_token = await self.drive.find_child_by_name(
                folder_token=current_token, name=segment
            )
            if not child_token:
                return None
            # — Probe the type via metas batch_query (1 element) so
            # the caller knows whether they're moving a folder vs a
            # file. metas returns both tokens unchanged; for nested
            # unknown types we default to "folder".
            metas = await self.drive.get_file_meta(
                file_tokens=[child_token], file_type="folder"
            )
            current_type = (
                (metas[0].get("type") if metas else None) or "folder"
            )
            current_token = child_token
            parent_path = walked_path
            walked_path = f"{walked_path}/{segment}"

        return DriveNode(
            name=segments[-1],
            token=current_token,
            type=current_type,
            path=walked_path,
            parent_path=parent_path,
        )

    async def walk_tree(self, *, max_depth: int = 3) -> dict[str, Any]:
        """Recursive tree dump of the 4-section layout.

        Returns a nested dict shaped like::

            {
              "name": "<root>",
              "token": "<root>",
              "type": "folder",
              "children": [
                {"name": "📌 首页", "token": "...", "type": "folder",
                 "children": [...]},
                ...
              ]
            }

        ``max_depth`` is a recursion guard — the IM reply has a 4 000
        char limit, so we cap the tree depth to keep the message
        compact. ``max_depth=3`` reaches a daily folder + a few files.
        """
        if not self.drive.is_configured:
            raise FeishuContentError(
                "drive_org: feishu drive not configured "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN)"
            )
        root_token = self.drive.folder_token
        root_node: dict[str, Any] = {
            "name": "root",
            "token": root_token,
            "type": "folder",
            "children": [],
        }

        async def _walk(token: str, depth: int) -> list[dict[str, Any]]:
            if depth >= max_depth:
                return []
            children_raw = await self.drive.list_children(folder_token=token)
            out: list[dict[str, Any]] = []
            for child in children_raw:
                node: dict[str, Any] = {
                    "name": child.get("name") or "",
                    "token": child.get("token") or "",
                    "type": child.get("type") or "folder",
                }
                if node["type"] == "folder" and depth + 1 < max_depth:
                    node["children"] = await _walk(node["token"], depth + 1)
                out.append(node)
            return out

        root_node["children"] = await _walk(root_token, depth=1)
        return root_node

    async def path_within_root(self, *, token: str) -> bool:
        """Ancestry check — does ``token`` live somewhere under the root?

        Returns ``True`` for ``None`` / empty so callers can pass
        through unconfigured values safely. Otherwise BFS-walks the
        tree from ``folder_token`` until it either finds ``token`` or
        runs out of nodes. Bounded by ``max_depth`` to keep IM reply
        latency low.
        """
        if not token:
            return True
        root = (self.drive.folder_token or "").strip()
        if not root:
            return False
        if token == root:
            return True
        # — BFS up to 4 levels — the 4-section tree is at most 2-3
        # deep so this is more than enough.
        max_depth = 4
        frontier = [root]
        seen: set[str] = {root}
        for _ in range(max_depth):
            next_frontier: list[str] = []
            for parent in frontier:
                children = await self.drive.list_children(folder_token=parent)
                for child in children:
                    child_token = child.get("token") if isinstance(child, dict) else None
                    if not child_token or child_token in seen:
                        continue
                    if child_token == token:
                        return True
                    seen.add(child_token)
                    next_frontier.append(child_token)
            if not next_frontier:
                break
            frontier = next_frontier
        return False


__all__ = [
    "DailyDocRef",
    "DriveNode",
    "DriveOrgService",
    "RootTokens",
    "SECTION_DAILY",
    "SECTION_HOME",
    "SECTION_SOURCES",
    "SECTION_TODAY",
]
