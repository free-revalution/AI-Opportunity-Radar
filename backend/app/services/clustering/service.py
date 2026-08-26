"""Clustering service — orchestrates the Phase 4 pipeline.

Pipeline:

    1. SELECT raw_items not yet linked to any opportunity
    2. Embed their (title + url + content)
    3. Single-pass cluster with cosine similarity threshold
    4. Synthesize one Opportunity per cluster + N link rows
    5. INSERT (idempotent via slug upsert + link-by-id)

Re-running this service MUST NOT create duplicate Opportunities:
  * `OpportunityRepository.upsert_by_slug()` returns the existing row
    when the cluster's stable slug is already known.
  * `OpportunitySourceRepository.link()` swallows IntegrityError
    (uq_opp_raw) and updates relevance on conflict.

This is "in-memory numpy" clustering per docs/ARCHITECTURE.md; pgvector
will replace the embedder step in a later phase.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import RawItem
from app.repositories import (
    OpportunityRepository,
    OpportunitySourceRepository,
    RawItemRepository,
)
from app.services.clustering.clusterer import Clusterer, ClusteringResult
from app.services.clustering.embedding import HashingEmbedder
from app.services.clustering.synthesizer import SynthesisResult, synthesize_cluster
from app.utils import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ClusteringReport:
    """Aggregated outcome of a single `run_once()` call."""

    raw_items_seen: int = 0
    clusters_formed: int = 0
    merged_clusters: int = 0
    opportunities_created: int = 0
    opportunities_updated: int = 0
    opportunities_skipped_invalid: int = 0
    links_created: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


class ClusteringService:
    """Run the embed → cluster → synthesise pipeline against the DB."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        embedder: HashingEmbedder | None = None,
        clusterer: Clusterer | None = None,
        raw_item_limit: int = 500,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.raw_item_limit = raw_item_limit

        # Injected for tests; default uses real config + hashing embedder.
        self.embedder = embedder or HashingEmbedder(dim=384)
        self.clusterer = clusterer or Clusterer(
            threshold=self.settings.embedding_cluster_threshold
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def run_once(self) -> ClusteringReport:
        """Run one end-to-end clustering pass."""
        report = ClusteringReport()

        items = await self._load_unclustered_items()
        report.raw_items_seen = len(items)
        if not items:
            logger.info("clustering_nothing_to_do")
            return report

        vectors = self._embed_items(items)
        if vectors.shape[0] == 0:
            return report

        result = self.clusterer.cluster(vectors)
        report.clusters_formed = result.cluster_count
        report.merged_clusters = result.merged_count

        # Load the items by index in cluster order.
        for cluster in result.clusters:
            members = [items[m] for m in cluster.members]
            try:
                outcome = await self._persist_cluster(members)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"cluster persist failed: {exc}")
                logger.exception("cluster_persist_failed")
                continue
            report.opportunities_created += int(outcome["created"])
            report.opportunities_updated += int(outcome["updated"])
            report.opportunities_skipped_invalid += int(
                outcome.get("skipped_invalid", 0)
            )
            report.links_created += int(outcome["links"])

        logger.info(
            "clustering_run_complete",
            **report.as_dict(),
        )
        return report

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _load_unclustered_items(self) -> list[RawItem]:
        repo = RawItemRepository(self.session)
        return await repo.list_unclustered(limit=self.raw_item_limit)

    def _embed_items(self, items: Iterable[RawItem]) -> np.ndarray:
        texts = [_text_for(r) for r in items]
        if not texts:
            return np.empty((0, self.embedder.dim), dtype=np.float32)
        return self.embedder.embed_batch(texts)

    async def _persist_cluster(self, members: list[RawItem]) -> dict[str, int]:
        opp_repo = OpportunityRepository(self.session)
        link_repo = OpportunitySourceRepository(self.session)

        synthesis: SynthesisResult = synthesize_cluster(members)

        # Determine whether the slug already exists BEFORE we upsert so
        # the report can say "created" vs "updated".
        existing = await opp_repo.get_by_slug(synthesis.opportunity_fields["slug"])
        opp = await opp_repo.upsert_by_slug(**synthesis.opportunity_fields)
        await self.session.flush()

        created = 0 if existing is not None else 1
        updated = 1 if existing is not None else 0

        links = 0
        for link in synthesis.links:
            await link_repo.link(
                opportunity_id=opp.id,
                raw_item_id=link["raw_item_id"],
                relevance=link["relevance"],
            )
            links += 1
        await self.session.flush()

        # Commit so the writes survive the request boundary — FastAPI
        # does not auto-commit when the route returns.
        await self.session.commit()

        return {"created": created, "updated": updated, "links": links}


def _text_for(item: RawItem) -> str:
    """Compact text used for embedding — title dominates, then content."""
    parts: list[str] = [(item.title or "").strip()]
    if item.content:
        parts.append(item.content.strip())
    if item.url:
        parts.append(item.url)
    return "\n".join(p for p in parts if p)


__all__ = ["ClusteringReport", "ClusteringService"]
