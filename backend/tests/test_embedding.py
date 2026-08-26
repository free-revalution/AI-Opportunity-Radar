"""Tests for the hashing embedder + cosine similarity."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.clustering.embedding import (
    HashingEmbedder,
    cosine_similarity,
    cosine_similarity_matrix,
)


def test_embed_empty_string_returns_zero_vector():
    embedder = HashingEmbedder(dim=64)
    vec = embedder.embed("")
    assert vec.shape == (64,)
    assert float(np.linalg.norm(vec)) == 0.0


def test_embed_is_deterministic():
    embedder = HashingEmbedder(dim=128)
    v1 = embedder.embed("AI Sales Coach")
    v2 = embedder.embed("AI Sales Coach")
    assert np.array_equal(v1, v2)


def test_embed_output_is_unit_norm():
    embedder = HashingEmbedder(dim=256)
    vec = embedder.embed("Some product title about AI sales automation tools")
    norm = float(np.linalg.norm(vec))
    assert math.isclose(norm, 1.0, abs_tol=1e-5)


def test_embed_batch_shapes_correct():
    embedder = HashingEmbedder(dim=64)
    batch = embedder.embed_batch(["alpha", "beta", ""])
    assert batch.shape == (3, 64)
    # empty string → zero vector stays zero
    assert float(np.linalg.norm(batch[2])) == 0.0


def test_embed_batch_empty_input():
    embedder = HashingEmbedder(dim=32)
    assert embedder.embed_batch([]).shape == (0, 32)


def test_near_duplicate_texts_have_high_similarity():
    embedder = HashingEmbedder(dim=512, ngram=3)
    a = embedder.embed(
        "AI Sales Coach: real-time call summaries and CRM enrichment for sales teams"
    )
    b = embedder.embed(
        "AI Sales Coach — real-time call summaries + CRM enrichment for sales teams"
    )
    sim = cosine_similarity(a, b)
    assert sim > 0.7, f"expected near-duplicates to score high, got {sim:.3f}"


def test_unrelated_texts_have_lower_similarity():
    embedder = HashingEmbedder(dim=512, ngram=3)
    a = embedder.embed("AI Sales Coach for outbound sales teams")
    b = embedder.embed("Authentic Italian pasta recipes from Bologna")
    sim = cosine_similarity(a, b)
    assert sim < 0.3, f"expected unrelated texts to score low, got {sim:.3f}"


def test_cosine_similarity_matrix_identity_diagonal():
    embedder = HashingEmbedder(dim=64)
    vectors = embedder.embed_batch(["alpha", "beta", "gamma"])
    sim = cosine_similarity_matrix(vectors)
    assert sim.shape == (3, 3)
    for i in range(3):
        assert math.isclose(sim[i, i], 1.0, abs_tol=1e-4)
    # Symmetric.
    assert np.allclose(sim, sim.T)


def test_cosine_similarity_matrix_empty():
    embedder = HashingEmbedder(dim=8)
    sim = cosine_similarity_matrix(np.empty((0, 8), dtype=np.float32))
    assert sim.shape == (0, 0)


def test_dim_must_be_positive():
    with pytest.raises(ValueError):
        HashingEmbedder(dim=0)
    with pytest.raises(ValueError):
        HashingEmbedder(dim=-10)


def test_ngram_must_be_positive():
    with pytest.raises(ValueError):
        HashingEmbedder(dim=64, ngram=0)
