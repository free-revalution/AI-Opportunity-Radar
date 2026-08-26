"""Tests for the single-pass agglomerative clusterer."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.clustering.clusterer import Clusterer


def _unit(vecs):
    arr = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.where(norms == 0, 1.0, norms)


def test_empty_input_yields_empty_result():
    clusterer = Clusterer(threshold=0.5)
    result = clusterer.cluster(np.empty((0, 4), dtype=np.float32))
    assert result.cluster_count == 0
    assert result.total_items == 0


def test_single_item_becomes_single_cluster():
    vec = _unit([[1.0, 0.0, 0.0, 0.0]])
    result = Clusterer(threshold=0.5).cluster(vec)
    assert result.cluster_count == 1
    assert result.singleton_count == 1
    assert result.total_items == 1


def test_near_duplicates_collapse_into_single_cluster():
    # Two vectors identical → cosine sim = 1.0 → same cluster.
    vecs = _unit(
        [
            [1.0, 0.1, 0.0, 0.0],
            [1.0, 0.1, 0.0, 0.0],
        ]
    )
    result = Clusterer(threshold=0.8).cluster(vecs)
    assert result.cluster_count == 1
    assert result.merged_count == 1
    assert result.singleton_count == 0


def test_orthogonal_vectors_form_separate_clusters():
    vecs = _unit(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ]
    )
    result = Clusterer(threshold=0.5).cluster(vecs)
    assert result.cluster_count == 3
    assert result.singleton_count == 3
    assert result.merged_count == 0


def test_threshold_controls_merging():
    vecs = _unit(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.9, 0.4, 0.0, 0.0],  # cosine ≈ 0.913 → would merge at 0.8
        ]
    )
    # High threshold → two clusters.
    high = Clusterer(threshold=0.99).cluster(vecs)
    assert high.cluster_count == 2
    # Low threshold → one cluster.
    low = Clusterer(threshold=0.5).cluster(vecs)
    assert low.cluster_count == 1


def test_invalid_threshold_rejected():
    with pytest.raises(ValueError):
        Clusterer(threshold=1.5)
    with pytest.raises(ValueError):
        Clusterer(threshold=-0.1)


def test_member_to_cluster_mapping():
    vecs = _unit(
        [
            [1.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ]
    )
    clusterer = Clusterer(threshold=0.8)
    result = clusterer.cluster(vecs)
    mapping = clusterer.member_to_cluster(result)
    assert mapping[0] == mapping[1]
    assert mapping[2] != mapping[0]
    assert set(mapping.keys()) == {0, 1, 2}


def test_centroid_is_unit_norm_after_clustering():
    vecs = _unit(
        [
            [1.0, 0.0, 0.0, 0.0],
            [1.0, 0.1, 0.0, 0.0],
            [1.0, -0.1, 0.0, 0.0],
        ]
    )
    result = Clusterer(threshold=0.7).cluster(vecs)
    assert result.cluster_count == 1
    centroid = result.clusters[0].centroid
    assert centroid is not None
    assert abs(float(np.linalg.norm(centroid)) - 1.0) < 1e-4


def test_2d_input_required():
    clusterer = Clusterer(threshold=0.5)
    with pytest.raises(ValueError):
        clusterer.cluster(np.array([1.0, 0.0, 0.0]))  # 1-D


def test_run_with_zero_vectors_does_not_divide_by_zero():
    # Zero rows should not break the clustering (defensive).
    arr = np.zeros((2, 4), dtype=np.float32)
    result = Clusterer(threshold=0.5).cluster(arr)
    assert result.cluster_count == 2
