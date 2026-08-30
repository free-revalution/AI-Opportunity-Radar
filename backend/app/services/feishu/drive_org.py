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
    """Tokens of the 4 root sections + the root itself."""

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


__all__ = [
    "DailyDocRef",
    "DriveOrgService",
    "RootTokens",
    "SECTION_DAILY",
    "SECTION_HOME",
    "SECTION_SOURCES",
    "SECTION_TODAY",
]
