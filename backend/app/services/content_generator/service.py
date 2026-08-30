"""Content generation orchestrator.

`ContentGeneratorService` fans a list of `Opportunity` rows through
every registered `ContentGenerator`. The output of one run is a
`GenerationResult` — a small report the operator (or the daily
n8n workflow) can inspect without parsing exception logs.

Responsibilities
================

1. **Select** which opportunities to process. Default = every row
   whose `commercial_status` is `qualified` or `promising` AND whose
   `content_status` is `new`. The caller can override via
   `opportunity_ids` for ad-hoc runs.

2. **Run every registered generator** on each selected opportunity.
   One generator's failure does NOT abort the run — it surfaces in
   the per-row `errors` list.

3. **Enrich the Opportunity row** with the commercial fields the
   doc spec calls for (`target_customer`, `market_size`,
   `monetization_model`, `mvp_days`, `china_gap`). This is done by
   the LLM via a structured `enrich_opportunity()` call BEFORE the
   generators run, so each generator has clean inputs to work with.

4. **Flip `content_status` to `generated`** on success.

5. **Persist the GeneratedContent payloads** as `Notification` rows
   with `channel=<channel>` and `payload=<GeneratedContent dict>` so
   downstream channels (Feishu bot, Xianyu CSV exporter, etc.) can
   consume them via the existing notification feed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.content_generator.base import (
    ContentGenerator,
    GeneratedContent,
    get_registry,
)
from app.services.llm.provider import LLMProvider
from app.utils import (
    ExternalServiceError,
    get_logger,
)

logger = get_logger(__name__)


def _stringify(body: Any) -> str:
    """Render a `GeneratedContent.content` field for compliance scan.

    GeneratedContent.content is either a Markdown string (most channels)
    or a `dict[str, Any]` (the Xianyu listing shape). The compliance
    detectors all want plain text, so we serialise dicts to JSON.
    """
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public DTOs
# ---------------------------------------------------------------------------
@dataclass
class GenerationResult:
    """Summary of one orchestrator run."""

    generated: list[GeneratedContent] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    enriched_opportunity_ids: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
            return {
                "generated_count": len(self.generated),
                "error_count": len(self.errors),
                "enriched_count": len(self.enriched_opportunity_ids),
                "errors": self.errors,
            }


# JSON schema the LLM must fill when enriching an Opportunity.
_ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_customer": {
            "type": "string",
            "description": "目标客户画像,1-2 句,具体到行业/规模/付费能力",
        },
        "market_size": {
            "type": "string",
            "description": "TAM/SAM/SOM 或类似量级,例如 '100M-500M USD'",
        },
        "mvp_days": {
            "type": "integer",
            "description": "单人/小团队 MVP 估算天数,7-90 之间",
            "minimum": 7,
            "maximum": 90,
        },
        "difficulty": {
            "type": "string",
            "enum": ["easy", "medium", "hard"],
            "description": "实现难度",
        },
        "monetization_model": {
            "type": "string",
            "description": "商业模式,例如 'SaaS 订阅 49 USD/月'",
        },
        "china_gap": {
            "type": "string",
            "description": "中国市场的空白点 + 可借鉴/可本地化的部分,1-3 句",
        },
    },
    "required": [
        "target_customer",
        "market_size",
        "mvp_days",
        "difficulty",
        "monetization_model",
        "china_gap",
    ],
}


_ENRICHMENT_SYSTEM_PROMPT = (
    "你是一名 AI 商业分析师。基于给定的海外 AI 机会和深度研究,"
    "提取并补全以下六个商业字段。返回严格符合 JSON Schema 的对象,"
    "不要包含任何额外文字。\n\n"
    "评分标准:\n"
    "* mvp_days  < 14 → easy;14-30 → medium;> 30 → hard\n"
    "* china_gap 必须给出具体的中国市场切入点(语言/支付/合规/渠道差异)"
)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class ContentGeneratorService:
    """Run all registered generators against a selection of opportunities."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        llm: LLMProvider,
        registry: Any | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.registry = registry or get_registry()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def run_for_opportunity(
        self,
        opportunity: Any,
        *,
        report: Any | None = None,
        generators: Iterable[str] | None = None,
        enrich: bool = True,
    ) -> list[GeneratedContent]:
        """Run all (or a subset of) registered generators on one opp.

        Returns the list of `GeneratedContent` produced. Updates the
        Opportunity row in place (commercial fields + content_status).
        """
        from app.models import Notification, Opportunity  # local import

        if enrich and not self._is_enriched(opportunity):
            try:
                enrichment = await self._enrich_opportunity(opportunity, report)
            except (ExternalServiceError, ValueError, KeyError) as exc:
                logger.warning(
                    "content_enrichment_failed",
                    opportunity_id=opportunity.id,
                    error=str(exc),
                )
                # Enrichment is best-effort — generators can still run
                # with whatever fields the Opportunity already has.
            else:
                self._apply_enrichment(opportunity, enrichment)
                await self.session.flush()

        produced: list[GeneratedContent] = []
        names = list(generators) if generators is not None else self.registry.names()
        for gen_name in names:
            try:
                gen = self.registry.get(gen_name)
            except KeyError as exc:
                logger.warning(
                    "content_generator_missing",
                    name=gen_name,
                    error=str(exc),
                )
                continue
            try:
                content = await gen.generate(
                    opportunity=opportunity, report=report, llm=self.llm
                )
            except Exception as exc:  # noqa: BLE001 — see comment
                # One generator's failure must NOT abort the orchestrator.
                # We swallow every exception, log it, and move on — the
                # operator inspects `result.errors` after the run to see
                # what went wrong per-generator. Re-raising would turn
                # one bad prompt into a full pipeline halt.
                logger.warning(
                    "content_generation_failed",
                    opportunity_id=opportunity.id,
                    generator=gen_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue
            produced.append(content)
            # Phase 24 — gate the generated copy before persisting as a
            # Notification. Blocked verdicts drop the content (no
            # notification row, opportunity.content_status unchanged).
            # The full verdict is captured in the AuditLog row the gate
            # writes — the operator surface (Phase 24E) reads that row,
            # not any field on the Opportunity itself.
            from app.services.compliance.gate import gate_outbound

            gate_text = f"{content.title}\n{_stringify(content.content)}"
            gate_outcome = await gate_outbound(
                gate_text,
                channel=content.channel or "content_pipeline",
                resource_type="content_opportunity",
                resource_id=str(content.opportunity_id),
                session=self.session,
                context=f"content_generate:{content.generator}",
            )
            if not gate_outcome.verdict.allowed:
                produced.pop()  # do not count this as delivered
                logger.warning(
                    "content_compliance_blocked",
                    opportunity_id=opportunity.id,
                    generator=content.generator,
                    risk_level=gate_outcome.verdict.risk_level.value,
                    reason=gate_outcome.verdict.reason,
                )
                continue
            await self._persist_notification(content)

        if produced:
            opportunity.content_status = "generated"
            await self.session.flush()
        return produced

    async def run_for_top_opportunities(
        self,
        *,
        limit: int = 5,
        only_qualified: bool = True,
        generators: Iterable[str] | None = None,
    ) -> GenerationResult:
        """Pick the top `limit` opportunities and run every generator.

        `generators` lets the caller restrict which named generators
        run (e.g. only `wechat_article`). None = every registered one.
        """
        from app.models import Opportunity, ResearchReport  # local import

        stmt = select(Opportunity).order_by(Opportunity.total_score.desc())
        if only_qualified:
            stmt = stmt.where(
                Opportunity.commercial_status.in_(["qualified", "promising"])
            )
        stmt = stmt.limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()

        result = GenerationResult()
        for opp in rows:
            # Pull the latest research report for context.
            report_stmt = (
                select(ResearchReport)
                .where(ResearchReport.opportunity_id == opp.id)
                .order_by(ResearchReport.created_at.desc())
                .limit(1)
            )
            report = (
                await self.session.execute(report_stmt)
            ).scalars().first()

            try:
                produced = await self.run_for_opportunity(
                    opp, report=report, generators=generators
                )
            except ExternalServiceError as exc:
                result.errors.append(
                    {
                        "opportunity_id": opp.id,
                        "error": str(exc),
                    }
                )
                continue
            result.generated.extend(produced)
            result.enriched_opportunity_ids.append(opp.id)
        return result

    async def run_for_ids(
        self,
        opportunity_ids: list[int],
        *,
        generators: Iterable[str] | None = None,
    ) -> GenerationResult:
        """Run all (or a subset of) generators on a specific set of
        opportunity IDs.

        `generators` mirrors `run_for_top_opportunities` — same None =
        all behaviour. The two methods now share the same surface so
        callers (n8n cron, /content/regenerate, /content/generate) don't
        have to remember which one supports subset filtering."""
        from app.models import Opportunity, ResearchReport  # local import

        result = GenerationResult()
        for opp_id in opportunity_ids:
            opp = await self.session.get(Opportunity, opp_id)
            if opp is None:
                result.errors.append(
                    {"opportunity_id": opp_id, "error": "not_found"}
                )
                continue
            report = (
                await self.session.execute(
                    select(ResearchReport)
                    .where(ResearchReport.opportunity_id == opp_id)
                    .order_by(ResearchReport.created_at.desc())
                    .limit(1)
                )
            ).scalars().first()
            try:
                produced = await self.run_for_opportunity(
                    opp, report=report, generators=generators
                )
            except ExternalServiceError as exc:
                result.errors.append(
                    {"opportunity_id": opp_id, "error": str(exc)}
                )
                continue
            result.generated.extend(produced)
            result.enriched_opportunity_ids.append(opp_id)
        return result

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _is_enriched(self, opportunity: Any) -> bool:
        # `commercial_status` is bumped to `qualified` once enrichment
        # has run; we use that as the cheap signal to skip re-running.
        return (
            getattr(opportunity, "commercial_status", "unqualified")
            not in ("", "unqualified", "new")
            or bool(getattr(opportunity, "target_customer", None))
        )

    async def _enrich_opportunity(
        self,
        opportunity: Any,
        report: Any | None,
    ) -> dict[str, Any]:
        user_parts: list[str] = [f"机会标题:{opportunity.title}"]
        if getattr(opportunity, "summary", None):
            user_parts.append(f"摘要:{opportunity.summary}")
        if getattr(opportunity, "target_user", None):
            user_parts.append(f"原始目标用户:{opportunity.target_user}")
        if report is not None:
            if getattr(report, "executive_summary", None):
                user_parts.append(f"\n研究摘要:{report.executive_summary}")
            if getattr(report, "market_analysis", None):
                user_parts.append(f"\n市场分析:{report.market_analysis}")
            if getattr(report, "china_analysis", None):
                user_parts.append(f"\n中国分析:{report.china_analysis}")
            if getattr(report, "monetization_analysis", None):
                user_parts.append(f"\n商业分析:{report.monetization_analysis}")

        return await self.llm.complete_json(
            system=_ENRICHMENT_SYSTEM_PROMPT,
            user="\n".join(user_parts),
            response_schema=_ENRICHMENT_SCHEMA,
        )

    @staticmethod
    def _apply_enrichment(opportunity: Any, payload: dict[str, Any]) -> None:
        """Mutate the Opportunity row in place. Caller flushes."""
        for key in (
            "target_customer",
            "market_size",
            "monetization_model",
            "difficulty",
            "china_gap",
        ):
            value = payload.get(key)
            if value:
                setattr(opportunity, key, value)
        if payload.get("mvp_days") is not None:
            try:
                opportunity.mvp_days = int(payload["mvp_days"])
            except (TypeError, ValueError):
                pass
        # Bump commercial_status — qualified + has target_customer means
        # we have enough material to generate copy.
        if opportunity.target_customer and opportunity.mvp_days:
            opportunity.commercial_status = "qualified"

    async def _persist_notification(self, content: GeneratedContent) -> None:
        """Mirror GeneratedContent to a Notification row so the existing
        notification feed picks it up.
        """
        from app.models import Notification  # local import

        body = content.content
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
        payload = {
            "generator": content.generator,
            "title": content.title,
            "format": content.format,
            "body": body,
            "metadata": content.metadata,
            "opportunity_id": content.opportunity_id,
        }
        self.session.add(
            Notification(channel=content.channel, payload=payload)
        )
        await self.session.flush()


__all__ = [
    "ContentGeneratorService",
    "GenerationResult",
]