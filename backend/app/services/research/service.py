"""Research service — Phase 7.

Pipeline (per `ResearchJob`):

  1. Load the opportunity + its linked RawItems (URLs + titles).
  2. Optionally expand the URL set via `WebDataProvider.search()` using
     the opportunity's title + summary as the seed query.
  3. Scrape up to `deep_research_max_urls` pages, deduplicated by URL.
  4. Compose the synthesis prompt (see `prompts.build_synthesis_prompt`)
     and call the LLM provider in JSON-mode with the strict schema.
  5. Parse + validate the response (`parsers.parse_research_report`).
  6. Persist a `ResearchReport` row, update the opportunity status to
     `research_complete`, mark the job `completed`.

Failure policy matches Phase 5/6: a single bad job MUST NOT poison the
batch — the error is recorded on the job and we move on.

The MVP is a single-pass scrape → LLM synthesis. Iterative depth-N
expansion is left as a no-op stub for a later phase.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Opportunity, ResearchJob, ResearchReport
from app.repositories import (
    OpportunityRepository,
    OpportunitySourceRepository,
)
from app.services.llm import LLMProvider, build_llm_provider
from app.services.research.parsers import (
    parse_research_report,
    validate_research_report,
)
from app.services.research.prompts import (
    RESEARCH_REPORT_SCHEMA,
    SYSTEM_PROMPT,
    build_synthesis_prompt,
)
from app.services.research.web_data import (
    SourceDoc,
    WebDataProvider,
    build_web_data_provider,
)
from app.utils import get_logger
from app.utils.errors import ExternalServiceError

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ResearchReportOutcome:
    """Per-job result."""

    job_id: int
    opportunity_id: int
    status: str
    confidence: float
    recommendation: str
    sources_count: int
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(slots=True)
class ResearchReportSummary:
    """Aggregate outcome for a single `run_once()` call."""

    jobs_attempted: int = 0
    jobs_completed: int = 0
    jobs_failed: int = 0
    urls_scraped: int = 0
    reports_persisted: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class ResearchService:
    """Phase 7 orchestrator — runs `ResearchJob`s into `ResearchReport`s."""

    COMPLETED_STATUS = "research_complete"
    FAILED_STATUS = "failed"
    JOB_PENDING = "pending"
    JOB_RUNNING = "running"
    JOB_COMPLETED = "completed"
    JOB_FAILED = "failed"
    JOB_CANCELLED = "cancelled"

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        llm_provider: LLMProvider | None = None,
        web_provider: WebDataProvider | None = None,
        limit: int = 10,
        max_urls: int | None = None,
        max_depth: int | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.llm = llm_provider or build_llm_provider(self.settings)
        self.web = web_provider or build_web_data_provider(self.settings)
        self.limit = limit
        self.max_urls = (
            max_urls if max_urls is not None else self.settings.deep_research_max_urls
        )
        self.max_depth = (
            max_depth if max_depth is not None else self.settings.deep_research_max_depth
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def run_once(self) -> ResearchReportSummary:
        """Process every pending ResearchJob (capped at `self.limit`)."""
        summary = ResearchReportSummary()
        jobs = await self._list_pending_jobs(limit=self.limit)
        summary.jobs_attempted = len(jobs)
        if not jobs:
            logger.info("research_nothing_pending")
            return summary

        for job in jobs:
            try:
                outcome = await self.process_job(job.id)
            except Exception as exc:  # noqa: BLE001
                summary.jobs_failed += 1
                summary.errors.append(f"job {job.id}: {exc}")
                logger.exception("research_job_failed", job_id=job.id)
                await self._mark_job_failed(job.id, str(exc))
                continue

            if outcome.status == self.JOB_COMPLETED:
                summary.jobs_completed += 1
                summary.reports_persisted += 1
                summary.urls_scraped += outcome.sources_count
            else:
                summary.jobs_failed += 1
                if outcome.error:
                    summary.errors.append(f"job {job.id}: {outcome.error}")

        logger.info("research_run_complete", **summary.as_dict())
        return summary

    async def process_job(
        self,
        job: ResearchJob | int,
        *,
        use_mock_web: bool = False,
    ) -> ResearchReportOutcome:
        """Process one job end-to-end. Returns the structured outcome."""
        job_obj = await self._resolve_job(job)
        if job_obj is None:
            raise LookupError(f"research job not found: {job}")

        opp_repo = OpportunityRepository(self.session)
        opp = await opp_repo.get_by_id(job_obj.opportunity_id)
        if opp is None:
            raise LookupError(
                f"opportunity not found for job {job_obj.id} "
                f"(opportunity_id={job_obj.opportunity_id})"
            )

        await self._mark_job_running(job_obj)
        await self.session.commit()

        web = self.web
        if use_mock_web and getattr(web, "name", None) != "mock":
            from app.services.research.mock_web_data import MockWebDataProvider

            web = MockWebDataProvider()

        from app.metrics import observe_research_job

        started = time.perf_counter()
        try:
            urls, source_docs = await self._gather_sources(opp, web)
            parsed = await self._synthesise(opp, source_docs)
            warnings = validate_research_report(parsed)

            await self._persist_report(opp, parsed, source_docs)
            await self._mark_job_completed(job_obj)
            await self.session.commit()

            outcome = ResearchReportOutcome(
                job_id=job_obj.id,
                opportunity_id=opp.id,
                status=self.JOB_COMPLETED,
                confidence=parsed["confidence"],
                recommendation=parsed["recommendation"],
                sources_count=len(parsed["sources_json"]["items"]),
                warnings=warnings,
            )
            logger.info(
                "research_job_done",
                job_id=job_obj.id,
                opportunity_id=opp.id,
                recommendation=outcome.recommendation,
                confidence=outcome.confidence,
                sources=outcome.sources_count,
                warnings=len(warnings),
            )
            observe_research_job(time.perf_counter() - started)
            return outcome
        except Exception as exc:  # noqa: BLE001
            observe_research_job(time.perf_counter() - started)
            logger.exception(
                "research_job_error",
                job_id=job_obj.id,
                opportunity_id=opp.id,
            )
            await self._mark_job_failed(job_obj, str(exc))
            await self.session.commit()
            return ResearchReportOutcome(
                job_id=job_obj.id,
                opportunity_id=opp.id,
                status=self.JOB_FAILED,
                confidence=0.0,
                recommendation="insufficient_data",
                sources_count=0,
                error=str(exc),
            )

    async def cancel(self, job: ResearchJob | int) -> bool:
        """Mark a pending/running job as `cancelled`. Idempotent."""
        job_obj = await self._resolve_job(job)
        if job_obj is None:
            return False
        if job_obj.status not in (self.JOB_PENDING, self.JOB_RUNNING):
            return False
        job_obj.status = self.JOB_CANCELLED
        job_obj.completed_at = datetime.now(timezone.utc)
        await self.session.commit()
        logger.info("research_job_cancelled", job_id=job_obj.id)
        return True

    # ------------------------------------------------------------------
    # internals — sourcing
    # ------------------------------------------------------------------
    async def _gather_sources(
        self,
        opp: Opportunity,
        web: WebDataProvider,
    ) -> tuple[list[str], list[SourceDoc]]:
        """Return (urls_attempted, source_docs)."""
        seed_urls = await self._seed_urls_from_opp(opp)

        # Iterative expansion — depth-N, but the MVP only does depth=0.
        # We always run search() at least once to enrich the seed list.
        extra_docs: list[SourceDoc] = []
        if self.max_depth >= 1:
            query = self._search_query_for(opp)
            try:
                extra_docs = await web.search(query, limit=5)
            except ExternalServiceError as exc:
                logger.warning(
                    "research_search_failed",
                    opportunity_id=opp.id,
                    error=str(exc),
                )

        candidate_urls: list[str] = list(dict.fromkeys(seed_urls))
        candidate_urls.extend(d.url for d in extra_docs if d.url)
        # Dedupe + cap.
        candidate_urls = list(dict.fromkeys(candidate_urls))[: self.max_urls]

        docs: list[SourceDoc] = list(extra_docs)
        for url in candidate_urls:
            try:
                doc = await web.scrape(url)
            except ExternalServiceError as exc:
                logger.warning(
                    "research_scrape_failed",
                    opportunity_id=opp.id,
                    url=url,
                    error=str(exc),
                )
                continue
            if doc.content:
                docs.append(doc)
        return candidate_urls, docs

    async def _seed_urls_from_opp(self, opp: Opportunity) -> list[str]:
        link_repo = OpportunitySourceRepository(self.session)
        raw_items = await link_repo.list_raw_items_for_opportunity(Opportunity.id)
        urls: list[str] = []
        for item in raw_items:
            if item.url:
                urls.append(item.url)
            if len(urls) >= self.max_urls:
                break
        return urls

    @staticmethod
    def _search_query_for(opp: Opportunity) -> str:
        title = (opp.title or "").strip()
        summary = (opp.summary or "").strip()
        if summary:
            # Take the first sentence only — keep the search query tight.
            first = summary.split(".")[0].strip()
            return f"{title} — {first}" if first else title
        return title or "AI business opportunity"

    # ------------------------------------------------------------------
    # internals — LLM
    # ------------------------------------------------------------------
    async def _synthesise(
        self, opp: Opportunity, source_docs: Sequence[SourceDoc]
    ) -> dict[str, Any]:
        """Call the LLM in JSON-mode and parse the response."""
        sub_scores = {
            "trend": float(opp.trend_score),
            "demand": float(opp.demand_score),
            "monetization": float(opp.monetization_score),
            "competition_gap": float(opp.competition_gap_score),
            "china_gap": float(opp.china_gap_score),
            "execution": float(opp.execution_score),
        }
        user_prompt = build_synthesis_prompt(
            title=opp.title,
            summary=opp.summary or "",
            category=opp.category,
            target_user=opp.target_user,
            sub_scores=sub_scores,
            total_score=float(opp.total_score),
            source_docs=[d.to_dict() for d in source_docs],
        )
        payload = await self.llm.complete_json(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            response_schema=RESEARCH_REPORT_SCHEMA,
            model=self.settings.openai_model_strong,
            max_tokens=min(self.settings.deep_research_max_tokens, 4000),
        )
        return parse_research_report(payload)

    # ------------------------------------------------------------------
    # internals — persistence
    # ------------------------------------------------------------------
    async def _persist_report(
        self,
        opp: Opportunity,
        parsed: dict[str, Any],
        source_docs: Iterable[SourceDoc],
    ) -> ResearchReport:
        """Insert the ResearchReport + flip Opportunity status."""
        # Merge scrape-time metadata with the LLM-curated sources.
        llm_sources = parsed.get("sources_json", {}).get("items", []) or []
        llm_urls = {entry["url"] for entry in llm_sources if entry.get("url")}
        for d in source_docs:
            if d.url and d.url not in llm_urls:
                llm_sources.append(
                    {
                        "url": d.url,
                        "title": d.title or "",
                        "via_provider": d.via_provider,
                    }
                )
        parsed["sources_json"] = {"items": llm_sources[:20]}

        report = ResearchReport(
            opportunity_id=opp.id,
            executive_summary=parsed["executive_summary"],
            market_analysis=parsed["market_analysis"],
            competition_analysis=parsed["competition_analysis"],
            china_analysis=parsed["china_analysis"],
            monetization_analysis=parsed["monetization_analysis"],
            mvp_analysis=parsed["mvp_analysis"],
            risk_analysis=parsed["risk_analysis"],
            recommendation=parsed["recommendation"],
            confidence=parsed["confidence"],
            sources_json=parsed["sources_json"],
        )
        self.session.add(report)

        opp.status = self.COMPLETED_STATUS
        await self.session.flush()
        return report

    async def _mark_job_running(self, job: ResearchJob) -> None:
        job.status = self.JOB_RUNNING
        job.started_at = datetime.now(timezone.utc)
        job.provider = getattr(self.web, "name", None)
        await self.session.flush()

    async def _mark_job_completed(self, job: ResearchJob) -> None:
        job.status = self.JOB_COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        job.error = None
        await self.session.flush()

    async def _mark_job_failed(
        self, job: ResearchJob | int, error: str
    ) -> None:
        if isinstance(job, int):
            job_obj = await self._resolve_job(job)
            if job_obj is None:
                return
            job = job_obj
        job.status = self.JOB_FAILED
        job.error = error[:2000]
        job.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.commit()

    # ------------------------------------------------------------------
    # internals — query helpers
    # ------------------------------------------------------------------
    async def _list_pending_jobs(self, *, limit: int) -> list[ResearchJob]:
        result = await self.session.execute(
            select(ResearchJob)
            .where(ResearchJob.status.in_([self.JOB_PENDING]))
            .order_by(ResearchJob.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _resolve_job(
        self, job: ResearchJob | int
    ) -> Optional[ResearchJob]:
        if isinstance(job, ResearchJob):
            return job
        return await self.session.get(ResearchJob, int(job))


__all__ = [
    "ResearchReportOutcome",
    "ResearchReportSummary",
    "ResearchService",
]
