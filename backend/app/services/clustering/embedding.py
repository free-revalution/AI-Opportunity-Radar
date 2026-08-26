"""Embedding abstractions + deterministic hashing embedder.

The fake `HashingEmbedder` implements the "hashing trick" (Weinberger
et al., 2009). It is **deterministic, dependency-free** (uses stdlib
+ numpy), and good enough to cluster near-duplicate stories — it
captures lexical overlap, which is what matters for "same product on
GitHub, Reddit, and HN at the same time."

For production semantic similarity we will swap in `MiniMaxEmbedder`
(MiniMax `embedding-2`) or a hosted sentence-transformers endpoint;
the `Embedder` ABC here keeps the boundary clean.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

import numpy as np

DEFAULT_DIM = 384


class Embedder(ABC):
    """Maps a string to a fixed-dim unit vector."""

    dim: int

    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        """Return a 1-D float32 numpy array of length `dim`."""

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        """Default batched embedding — override for efficiency."""
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        return np.stack([self.embed(t) for t in texts]).astype(np.float32)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def l2_normalise(vec: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(vec))
        if norm == 0.0 or math.isclose(norm, 0.0):
            return vec
        return vec / norm


class HashingEmbedder(Embedder):
    """Hashing-trick embedder. No API, deterministic, ~384 dims."""

    _TOKEN_RE = re.compile(r"[a-z0-9]+")

    def __init__(self, dim: int = DEFAULT_DIM, ngram: int = 3) -> None:
        if dim <= 0:
            raise ValueError("dim must be > 0")
        if ngram < 1:
            raise ValueError("ngram must be >= 1")
        self.dim = dim
        self.ngram = ngram

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        if not text:
            return vec
        for token in self._tokens(text):
            for idx, sign in self._hash_indices(token):
                vec[idx] += sign
        return self.l2_normalise(vec)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _tokens(self, text: str) -> Iterable[str]:
        """Word + character-ngram tokens, lowercased."""
        lowered = text.lower()
        words = self._TOKEN_RE.findall(lowered)
        for w in words:
            yield w
        if self.ngram <= 1:
            return
        padded = f" {lowered} "
        for i in range(len(padded) - self.ngram + 1):
            gram = padded[i : i + self.ngram]
            if " " not in gram.strip():  # skip pure-space grams
                yield gram

    def _hash_indices(self, token: str) -> Iterable[tuple[int, int]]:
        """Two independent hashes → (bucket, sign).

        Uses MD5 split into two halves so the function is stable across
        Python versions (hash() would change between processes).
        """
        digest = hashlib.md5(token.encode("utf-8")).digest()
        first = int.from_bytes(digest[:8], "big", signed=False)
        second = int.from_bytes(digest[8:], "big", signed=False)
        idx = first % self.dim
        sign = 1 if (second & 1) else -1
        # A second independent sample reduces collisions for short tokens.
        idx2 = ((first >> 13) ^ second) % self.dim
        sign2 = 1 if ((second >> 1) & 1) else -1
        yield idx, sign
        yield idx2, sign2


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two unit (or arbitrary) vectors."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def cosine_similarity_matrix(vectors: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity matrix for a 2-D array of vectors."""
    if vectors.size == 0:
        return np.empty((0, 0), dtype=np.float32)
    # Embeddings are L2-normalised so dot == cosine.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    safe = np.where(norms == 0, 1.0, norms)
    unit = vectors / safe
    return unit @ unit.T


__all__ = [
    "DEFAULT_DIM",
    "Embedder",
    "HashingEmbedder",
    "cosine_similarity",
    "cosine_similarity_matrix",
]
