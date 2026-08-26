"""Clustering service package — Phase 4.

Public surface:

    HashingEmbedder          deterministic, offline embedder (MVP)
    Clusterer                single-pass agglomerative clusterer
    ClusteringService        embed → cluster → synthesise → persist
    synthesize_cluster       cluster → Opportunity + link rows
"""

from app.services.clustering.clusterer import Clusterer, ClusteringResult
from app.services.clustering.embedding import (
    DEFAULT_DIM,
    Embedder,
    HashingEmbedder,
    cosine_similarity,
    cosine_similarity_matrix,
)
from app.services.clustering.service import ClusteringReport, ClusteringService
from app.services.clustering.synthesizer import (
    SynthesisResult,
    aggregate_category,
    aggregate_summary,
    pick_representative,
    synthesize_cluster,
)

__all__ = [
    "DEFAULT_DIM",
    "Clusterer",
    "ClusteringReport",
    "ClusteringResult",
    "ClusteringService",
    "Embedder",
    "HashingEmbedder",
    "SynthesisResult",
    "aggregate_category",
    "aggregate_summary",
    "cosine_similarity",
    "cosine_similarity_matrix",
    "pick_representative",
    "synthesize_cluster",
]
