"""Tests for `record_pipeline_run` — the metrics wrapper for service calls."""

from __future__ import annotations

import pytest

from app.metrics import (
    record_pipeline_run,
)
from tests._phase12_helpers import counter_value, histogram_count


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_record_pipeline_run_success_label() -> None:
    labels = {"stage": "test_success", "outcome": "success", "kind": "none"}

    async def fake_run():
        class _Report:
            items_seen = 5

        return _Report()

    before = counter_value("radar_pipeline_runs_total", labels)
    result = await record_pipeline_run("test_success", fake_run)
    after = counter_value("radar_pipeline_runs_total", labels)
    assert after == before + 1
    assert result is not None


async def test_record_pipeline_run_empty_label_when_report_zero() -> None:
    labels = {"stage": "test_empty", "outcome": "empty", "kind": "none"}

    async def fake_run():
        class _Report:
            items_seen = 0

        return _Report()

    before = counter_value("radar_pipeline_runs_total", labels)
    await record_pipeline_run("test_empty", fake_run)
    after = counter_value("radar_pipeline_runs_total", labels)
    assert after == before + 1


async def test_record_pipeline_run_error_label_on_exception() -> None:
    labels = {
        "stage": "test_error",
        "outcome": "error",
        "kind": "RuntimeError",
    }

    async def fake_run():
        raise RuntimeError("boom")

    before = counter_value("radar_pipeline_runs_total", labels)
    with pytest.raises(RuntimeError):
        await record_pipeline_run("test_error", fake_run)
    after = counter_value("radar_pipeline_runs_total", labels)
    assert after == before + 1


async def test_record_pipeline_run_observes_histogram() -> None:
    async def fake_run():
        class _Report:
            items_seen = 1

        return _Report()

    before = histogram_count("radar_pipeline_duration_seconds", {"stage": "test_hist"})
    await record_pipeline_run("test_hist", fake_run)
    after = histogram_count("radar_pipeline_duration_seconds", {"stage": "test_hist"})
    assert after == before + 1
