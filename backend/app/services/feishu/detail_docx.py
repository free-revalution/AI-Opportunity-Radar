"""Phase 30 — 详情 Docx 服务 (DetailDocxService).

用户点击 ``/run`` 卡片的 ``[生成报告]`` 按钮时,飞书会把
``card.action.trigger_v1`` 事件打到 ``/api/feishu/event``;本服务
负责把对应的 :class:`Opportunity` 全文 + 摘要 + 研究报告 + 信息源
装配成 Markdown,通过 :class:`FeishuDriveClient` 写到
``📁 每日报告/{YYYY-MM-DD}/detail-<slug>.docx``,然后再发一张
带 ``[查看详情]`` 跳转按钮的交互卡回去。

幂等性
------

Redis ``SETNX`` key ``radar:detail:{opp_id}:{YYYY-MM-DD}`` TTL 24h:

* 首次点击 → 拿到锁 → 写 docx → 缓存 ``doc_id``。
* 后续点击 → SETNX 失败 → 直接返回缓存的 ``doc_id``,不重写。

Redis 不可达时 ``SETNX`` 退化为 "每次都写"(fail-open),日志 warn。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import (
    Opportunity,
    OpportunitySource,
    RawItem,
    ResearchReport,
    Source,
)
from app.services.feishu.content_client import (
    FeishuContentError,
    FeishuDriveClient,
)
from app.services.feishu.drive_org import SECTION_DAILY, DriveOrgService
from app.services.redis_client import get_redis
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DetailDocxResult:
    """``write_to_drive`` 的产物 — 用于发卡 reply。"""

    opportunity_id: int
    title: str
    slug: str
    doc_id: str
    doc_url: str
    folder_token: str
    from_cache: bool = False
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    def to_metadata(self) -> dict[str, Any]:
        """紧凑 metadata — 给 ``CommandReply`` / ``app_client`` 发卡用。"""
        return {
            "opportunity_id": self.opportunity_id,
            "title": self.title,
            "slug": self.slug,
            "doc_id": self.doc_id,
            "doc_url": self.doc_url,
            "folder_token": self.folder_token,
            "from_cache": self.from_cache,
        }


# ---------------------------------------------------------------------------
# Markdown assembler
# ---------------------------------------------------------------------------
def _md_escape_inline(text: str) -> str:
    """Inline 文本里去掉会让 docx 块解析出乱的反引号、跳格等。"""
    if not text:
        return ""
    # — Docx blocks 把 ``|`` 当表格分隔,出现多段时会乱拼。
    return text.replace("|", "\\|").replace("\r\n", "\n").strip()


def _format_dt(value: Optional[datetime]) -> str:
    if value is None:
        return "未知"
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _slugify_for_docx_title(slug: str, *, max_chars: int = 80) -> str:
    """Docx title 里 slug 太长会显示不下 — 简单截断。"""
    if not slug:
        return "opportunity"
    return slug[:max_chars]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class DetailDocxService:
    """详情 docx 装配 + 写入 + 发卡 metadata。

    Phase 30 替代 Phase 29 的"统一日报"心智模型 — 每条 Opportunity
    一个独立 docx,生成时机由用户决定。
    """

    CONTENT_KEY = "detail_docx"  # — Redis SETNX value tag

    def __init__(
        self,
        *,
        session: AsyncSession,
        drive: FeishuDriveClient,
        settings: Optional[Settings] = None,
    ) -> None:
        self.session = session
        self.drive = drive
        self.settings = settings or drive.settings or get_settings()

    # ------------------------------------------------------------------
    # 1. Markdown assembler
    # ------------------------------------------------------------------
    async def build_markdown(self, *, opportunity_id: int) -> str:
        """拼装 Markdown 字符串 — 不写盘,便于单测和 dry-run。

        三种内容组合:
          1. 顶部 meta(score / category / sources)
          2. 摘要 / 潜在商业 / 关键词(任一为空则跳过对应 section)
          3. 研究报告 7 段(每个字段都为空则跳过整段)
          4. 信息源列表(始终展示,空时显示 "暂无")
        """
        opp = await self._load_opportunity(opportunity_id=opportunity_id)
        if opp is None:
            raise FeishuContentError(
                f"detail_docx: opportunity {opportunity_id} not found"
            )

        report = await self._load_research_report(opportunity_id=opportunity_id)
        sources = await self._load_sources_with_meta(opportunity_id=opportunity_id)

        md_lines: list[str] = []

        # — Header ---------------------------------------------------------
        title = _md_escape_inline(opp.title or "(untitled)")
        md_lines.append(f"# {title}")
        md_lines.append("")
        md_lines.append(
            f"**Score:** {opp.total_score:.1f}  |  "
            f"**Category:** {opp.category or '—'}  |  "
            f"**Sources:** {opp.source_count}"
        )
        md_lines.append("")
        md_lines.append(f"**Slug:** `{opp.slug}`")
        md_lines.append("")

        # — 摘要 / 潜在商业 / 关键词 (任一缺失则跳过对应小节) -----------
        if opp.problem:
            md_lines.append("## 摘要")
            md_lines.append("")
            md_lines.append(_md_escape_inline(opp.problem))
            md_lines.append("")

        if opp.potential_business:
            md_lines.append("## 潜在商业")
            md_lines.append("")
            md_lines.append(_md_escape_inline(opp.potential_business))
            md_lines.append("")

        keywords = list(opp.keywords_json or [])
        if keywords:
            md_lines.append("## 关键词")
            md_lines.append("")
            md_lines.extend(f"- {_md_escape_inline(kw)}" for kw in keywords)
            md_lines.append("")

        # — 研究报告 (任一字段非空才出整段) ------------------------------
        if report is not None and any(
            getattr(report, field_name, None)
            for field_name in (
                "executive_summary",
                "market_analysis",
                "competition_analysis",
                "china_analysis",
                "monetization_analysis",
                "mvp_analysis",
                "risk_analysis",
            )
        ):
            md_lines.append("## 研究报告")
            md_lines.append("")
            for heading, field_name in (
                ("执行摘要", "executive_summary"),
                ("市场分析", "market_analysis"),
                ("竞争分析", "competition_analysis"),
                ("中国市场分析", "china_analysis"),
                ("盈利模式", "monetization_analysis"),
                ("MVP 分析", "mvp_analysis"),
                ("风险评估", "risk_analysis"),
            ):
                body = getattr(report, field_name, None)
                if not body:
                    continue
                md_lines.append(f"### {heading}")
                md_lines.append("")
                md_lines.append(_md_escape_inline(body))
                md_lines.append("")
            if report.recommendation:
                md_lines.append(f"**推荐:** `{report.recommendation}`")
            if report.confidence:
                md_lines.append(
                    f"**置信度:** {report.confidence:.0%}"
                )
            md_lines.append("")

        # — 信息源 (始终展示) ---------------------------------------------
        md_lines.append("## 信息源")
        md_lines.append("")
        if not sources:
            md_lines.append("（暂无）")
        else:
            for s in sources:
                url = s.get("url") or ""
                name = s.get("title") or "(无标题)"
                src = s.get("source_name") or "未知来源"
                pub = _format_dt(s.get("published_at"))
                if url:
                    md_lines.append(
                        f"- [{_md_escape_inline(name)}]({url}) "
                        f"— {src} · {pub}"
                    )
                else:
                    md_lines.append(
                        f"- {_md_escape_inline(name)} — {src} · {pub}"
                    )
        md_lines.append("")

        # — Footer ---------------------------------------------------------
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        md_lines.append("---")
        md_lines.append(
            f"_生成时间: {now}_"
        )
        return "\n".join(md_lines).rstrip() + "\n"

    # ------------------------------------------------------------------
    # 2. Drive writer
    # ------------------------------------------------------------------
    async def write_to_drive(
        self,
        *,
        opportunity_id: int,
        target_day: Optional[date] = None,
        force: bool = False,
    ) -> DetailDocxResult:
        """装配 Markdown → 写 docx → 返回 :class:`DetailDocxResult`。

        幂等性:
          - Redis SETNX key ``radar:detail:{opp_id}:{day}`` TTL 24h。
          - 命中缓存 → 直接返回旧 ``doc_id`` (``from_cache=True``)。
          - Redis 不可达 → fail-open (记 warn, 仍尝试写)。
          - ``force=True`` → 跳过 SETNX 缓存检查,直接重写。
        """
        if not self.drive.is_configured:
            raise FeishuContentError(
                "detail_docx: feishu drive not configured "
                "(set FEISHU_DRIVE_ROOT_FOLDER_TOKEN)"
            )

        day = target_day or date.today()
        day_str = day.strftime("%Y-%m-%d")
        redis_key = f"radar:detail:{opportunity_id}:{day_str}"
        ttl = int(self.settings.radar_detail_doc_idempotency_ttl or 86_400)

        # — Load opportunity early so we know its title/slug for both the
        # cache-hit and the fresh-write branches.
        opp = await self._load_opportunity(opportunity_id=opportunity_id)
        if opp is None:
            raise FeishuContentError(
                f"detail_docx: opportunity {opportunity_id} not found"
            )

        # — Cache hit ------------------------------------------------------
        if not force:
            cached = await self._redis_get(key=redis_key)
            if cached:
                logger.info(
                    "detail_docx_cache_hit",
                    opportunity_id=opportunity_id,
                    day=day_str,
                    doc_id=cached[:24],
                )
                return DetailDocxResult(
                    opportunity_id=opportunity_id,
                    title=opp.title,
                    slug=opp.slug,
                    doc_id=cached.split("|", 1)[0],
                    doc_url=cached.split("|", 1)[1]
                    if "|" in cached
                    else f"https://feishu.cn/docx/{cached}",
                    folder_token="",  # — cached, no longer known
                    from_cache=True,
                )

        # — Acquire write lock (SETNX) ------------------------------------
        lock_token = f"{datetime.now(tz=timezone.utc).timestamp()}"
        acquired = await self._redis_setnx(key=redis_key, value=lock_token, ttl=ttl)
        if not acquired and not force:
            # — Someone else won the lock — wait briefly and re-read.
            cached_doc_id = await self._redis_get(key=redis_key)
            if cached_doc_id and cached_doc_id != lock_token:
                return DetailDocxResult(
                    opportunity_id=opportunity_id,
                    title=opp.title,
                    slug=opp.slug,
                    doc_id=cached_doc_id.split("|", 1)[0],
                    doc_url=cached_doc_id.split("|", 1)[1]
                    if "|" in cached_doc_id
                    else f"https://feishu.cn/docx/{cached_doc_id}",
                    folder_token="",
                    from_cache=True,
                )

        # — Fresh write ----------------------------------------------------
        markdown = await self.build_markdown(opportunity_id=opportunity_id)
        org = DriveOrgService(drive=self.drive, settings=self.settings)
        day_folder = await org.get_or_create_day_folder(day=day)
        title_slug = _slugify_for_docx_title(opp.slug)
        doc_title = f"{title_slug} 详情报告"
        try:
            result = await self.drive.create_docx_from_markdown(
                title=doc_title,
                markdown=markdown,
                folder_token=day_folder,
            )
        except Exception:
            # Phase 35 PR-follow-up: 详情 docx 写入失败 → 防止空 day_folder 残留
            # (用户原话: "云文档每日报告目录下确实新建了个 XXXX-XX-XX 的日期目录,
            # 但是目录中没有对应文件,是个空目录")。best-effort 清理后继续抛。
            try:
                await org.delete_day_folder_if_empty(day=day)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "detail_docx_cleanup_after_failure_swallowed",
                    day=day_str,
                )
            raise

        # — Replace lock with the actual doc_id ----------------------------
        cache_value = f"{result['doc_id']}|{result['url']}"
        await self._redis_set_value(
            key=redis_key, value=cache_value, ttl=ttl
        )

        logger.info(
            "detail_docx_written",
            opportunity_id=opportunity_id,
            day=day_str,
            doc_id=result["doc_id"][:24],
            folder_token=day_folder[:12] if day_folder else "",
        )
        return DetailDocxResult(
            opportunity_id=opportunity_id,
            title=opp.title,
            slug=opp.slug,
            doc_id=result["doc_id"],
            doc_url=result["url"],
            folder_token=day_folder,
            from_cache=False,
        )

    # ------------------------------------------------------------------
    # 3. Chat card reply
    # ------------------------------------------------------------------
    def render_chat_card_reply(
        self,
        result: DetailDocxResult,
    ) -> dict[str, Any]:
        """返回飞书交互卡 JSON — 用于按钮回调后回推消息。

        卡片结构:
          header:  {title, template}  ("绿色"/"蓝色")
          elements:
            - <markdown>: 标题 + 摘要
            - <action> [查看详情] → doc_url
            - <note>: from_cache 提示
        """
        title = result.title or "详情"
        body_lines = [
            f"📄 **{title}**",
            "",
            f"doc_id: `{result.doc_id[:18]}`",
            "已生成详情报告,点下方按钮查看。",
        ]
        if result.from_cache:
            body_lines.append("")
            body_lines.append("_(当天已生成过,直接复用旧 docx)_")

        body_md = "\n".join(body_lines)
        template = "green" if not result.from_cache else "blue"
        note_text = (
            "Via /run · radar:detail"
            if not result.from_cache
            else "Via 缓存(幂等命中)· radar:detail"
        )
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "✅ 详情已生成"},
                "template": template,
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": body_md},
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "type": "primary",
                            "text": {
                                "tag": "plain_text",
                                "content": "查看详情",
                            },
                            "url": result.doc_url,
                        }
                    ],
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": note_text,
                        }
                    ],
                },
            ],
        }

    # ------------------------------------------------------------------
    # Internals — DB loaders
    # ------------------------------------------------------------------
    async def _load_opportunity(
        self, *, opportunity_id: int
    ) -> Optional[Opportunity]:
        stmt = select(Opportunity).where(Opportunity.id == opportunity_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_research_report(
        self, *, opportunity_id: int
    ) -> Optional[ResearchReport]:
        stmt = (
            select(ResearchReport)
            .where(ResearchReport.opportunity_id == opportunity_id)
            .order_by(ResearchReport.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_sources_with_meta(
        self, *, opportunity_id: int
    ) -> list[dict[str, Any]]:
        """JOIN opportunity_sources → raw_items → sources,投影成行 dict。"""
        stmt = (
            select(
                RawItem.title,
                RawItem.url,
                RawItem.published_at,
                Source.name.label("source_name"),
            )
            .join(OpportunitySource, OpportunitySource.raw_item_id == RawItem.id)
            .join(Source, Source.id == RawItem.source_id)
            .where(OpportunitySource.opportunity_id == opportunity_id)
            .order_by(RawItem.published_at.desc().nullslast())
            .limit(50)
        )
        result = await self.session.execute(stmt)
        return [
            {
                "title": row.title,
                "url": row.url,
                "published_at": row.published_at,
                "source_name": row.source_name,
            }
            for row in result.all()
        ]

    # ------------------------------------------------------------------
    # Internals — Redis shims (fail-open)
    # ------------------------------------------------------------------
    async def _redis_get(self, *, key: str) -> Optional[str]:
        try:
            client = await get_redis()
            if client is None:
                return None
            return await client.get(key)
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning("detail_docx_redis_get_failed", error=str(exc)[:120])
            return None

    async def _redis_setnx(
        self, *, key: str, value: str, ttl: int
    ) -> bool:
        """``SET key value NX EX ttl`` — 拿到锁返回 True。"""
        try:
            client = await get_redis()
            if client is None:
                return True  # — fail-open: 没 Redis 也允许写
            return bool(await client.set(key, value, nx=True, ex=ttl))
        except Exception as exc:  # noqa: BLE001
            logger.warning("detail_docx_redis_setnx_failed", error=str(exc)[:120])
            return True

    async def _redis_set_value(
        self, *, key: str, value: str, ttl: int
    ) -> None:
        try:
            client = await get_redis()
            if client is None:
                return
            await client.set(key, value, ex=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("detail_docx_redis_set_failed", error=str(exc)[:120])


__all__ = ["DetailDocxResult", "DetailDocxService"]


# — Tell type-checkers about SECTION_DAILY usage in module docstring.
_ = SECTION_DAILY
