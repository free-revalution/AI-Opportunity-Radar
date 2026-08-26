"""Single-pass agglomerative clusterer.

The algorithm is intentionally simple:

  for each vector v (in insertion order):
      find existing cluster with centroid c that maximises
          cosine(v, c)
      if max_similarity >= threshold:
          append v to that cluster
      else:
          start a new cluster containing only v

The threshold is `embedding_cluster_threshold` from settings (default
0.82). At 0.82, near-duplicates and same-product stories collapse
together; at 0.95 only exact paraphrases match.

This is NOT a substitute for HDBSCAN / k-means; the spec explicitly
calls out "in-memory numpy for MVP" before pgvector lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.clustering.embedding import cosine_similarity


@dataclass(slots=True)
class Cluster:
    """A running cluster — centroid + member indices into the source batch."""

    members: list[int] = field(default_factory=list)
    centroid: np.ndarray | None = None  # unit vector, dtype float32

    def add(self, vec: np.ndarray, member_idx: int) -> None:
        self.members.append(member_idx)
        if self.centroid is None:
            self.centroid = vec.copy()
            return
        # Running unit-mean update — keeps centroid normalised and
        # avoids keeping every member in memory.
        new_centroid = self.centroid * (len(self.members) - 1) + vec
        norm = float(np.linalg.norm(new_centroid))
        if norm == 0.0:
            self.centroid = vec.copy()
        else:
            self.centroid = (new_centroid / norm).astype(np.float32)


@dataclass(slots=True)
class ClusteringResult:
    """Output of `Clusterer.cluster`."""

    clusters: list[Cluster]
    singleton_count: int  # how many clusters contain exactly 1 item

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)

    @property
    def merged_count(self) -> int:
        return sum(1 for c in self.clusters if len(c.members) > 1)

    @property
    def total_items(self) -> int:
        return sum(len(c.members) for c in self.clusters)


class Clusterer:
    """Single-pass similarity clusterer with a hard threshold."""

    def __init__(self, threshold: float = 0.82) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        self.threshold = threshold

    def cluster(self, vectors: np.ndarray) -> ClusteringResult:
        """Cluster a 2-D array of vectors (rows = items).

        Empty input → empty result, not an error.
        """
        if vectors.ndim != 2:
            raise ValueError("vectors must be a 2-D array")
        if vectors.shape[0] == 0:
            return ClusteringResult(clusters=[], singleton_count=0)

        # Re-normalise to be defensive — clusterer assumes unit vectors.
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        safe = np.where(norms == 0, 1.0, norms)
        unit = (vectors / safe).astype(np.float32)

        clusters: list[Cluster] = []
        centroids = np.empty((0, unit.shape[1]), dtype=np.float32)

        for idx, vec in enumerate(unit):
            best_cluster = -1
            best_score = self.threshold  # require AT LEAST threshold
            if centroids.shape[0] > 0:
                sims = centroids @ vec  # centroids are unit, vec is unit
                # numpy argmax with a sentinel threshold via masking
                masked = np.where(sims >= best_score, sims, -np.inf)
                local_best = int(np.argmax(masked))
                if masked[local_best] != -np.inf:
                    best_cluster = local_best
                    best_score = float(sims[local_best])

            if best_cluster < 0:
                new_cluster = Cluster()
                new_cluster.add(vec, idx)
                clusters.append(new_cluster)
                centroids = (
                    np.vstack([centroids, vec[None, :]])
                    if centroids.size
                    else vec[None, :]
                )
            else:
                clusters[best_cluster].add(vec, idx)
                # Re-normalise centroid in-place.
                c = clusters[best_cluster].centroid
                n = float(np.linalg.norm(c)) if c is not None else 0.0
                if n > 0:
                    clusters[best_cluster].centroid = (c / n).astype(np.float32)
                centroids[best_cluster] = clusters[best_cluster].centroid

            # Reference the score so static analysers don't complain; useful
            # for debugging hooks (logging) if added later.
            _ = best_score

        singletons = sum(1 for c in clusters if len(c.members) == 1)
        return ClusteringResult(clusters=clusters, singleton_count=singletons)

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------
    def member_to_cluster(
        self, result: ClusteringResult
    ) -> dict[int, int]:
        """Map item index → cluster index."""
        out: dict[int, int] = {}
        for ci, cluster in enumerate(result.clusters):
            for m in cluster.members:
                out[m] = ci
        return out


__all__ = ["Cluster", "Clusterer", "ClusteringResult"]
