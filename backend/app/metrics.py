"""Prometheus metrics registry + instrumentation helpers (Phase 12).

All metrics live on the default `prometheus_client.REGISTRY`. Helpers are
provided so that callers never import the bare `Counter` / `Histogram`
classes — tests monkey-patch the helpers (not the registry) to assert
that a specific label combo ticked.

Conventions:
* Counters end with `_total` and have an `outcome` label when applicable.
* Histograms end with `_seconds`.
* Labels are deliberately bounded so cardinality stays low (no per-URL
  path labels; the HTTP middleware uses an allowlist).
* No code path may mutate the registry outside this module.

Public surface:
* `record_pipeline_run(stage, coro)` — wraps a service's `run_once`,
  observes duration, increments `radar_pipeline_runs_total`.
* `record_web_data_call(provider, op, outcome, chain)` — records one
  provider attempt (success/error) inside the fallback chain.
* `record_external_error(provider, kind)` — coarse external-service
  error counter (used by the fallback chain on ExternalServiceError).
* `record_notification(kind, provider, outcome)` — fires from
  `NotificationService._dispatch`.
* `render()` — returns the current Prometheus text-format payload.
* `RESET_TOKEN` — sentinel used by tests to clear the registry between
  test runs (so two tests don't share Counter values).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ---------------------------------------------------------------------------
# Counters / Histograms / Gauges
# ---------------------------------------------------------------------------

PIPELINE_RUNS = Counter(
    "radar_pipeline_runs_total",
    "Number of pipeline runs completed (one tick per /api/internal/*/run).",
    ["stage", "outcome", "kind"],
)
PIPELINE_DURATION = Histogram(
    "radar_pipeline_duration_seconds",
    "Wall-clock duration of a pipeline run.",
    ["stage"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

WEB_DATA_REQUESTS = Counter(
    "radar_web_data_requests_total",
    "Per-attempt outcome of a WebDataProvider call (inside the fallback chain).",
    ["provider", "op", "outcome", "chain"],
)
EXTERNAL_ERRORS = Counter(
    "radar_external_service_errors_total",
    "Coarse count of external-service errors (raised to the operator).",
    ["provider", "kind"],
)

NOTIFICATIONS = Counter(
    "radar_notifications_total",
    "Telegram notification attempts.",
    ["kind", "provider", "outcome"],
)

HTTP_REQUESTS = Counter(
    "radar_http_requests_total",
    "HTTP requests handled by the backend.",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "radar_http_request_duration_seconds",
    "Wall-clock duration of an HTTP request.",
    ["method", "path"],
    buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

LLM_CALL_DURATION = Histogram(
    "radar_llm_call_duration_seconds",
    "Wall-clock duration of an LLM provider call.",
    ["provider", "op"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

RESEARCH_JOB_DURATION = Histogram(
    "radar_research_job_duration_seconds",
    "Wall-clock duration of one research job.",
    buckets=(1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)
RESEARCH_JOBS_PENDING = Gauge(
    "radar_research_jobs_pending",
    "Number of ResearchJob rows with status=pending.",
)
OPPORTUNITIES_BY_STATUS = Gauge(
    "radar_opportunities_by_status",
    "Number of Opportunity rows by status.",
    ["status"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T = TypeVar("T")


def render() -> bytes:
    """Return the current Prometheus text-format payload.

    The default encoding is the standard text format; the matching
    content type is exposed via `CONTENT_TYPE_LATEST`.
    """
    return generate_latest(REGISTRY)


def content_type() -> str:
    """Return the Prometheus exposition content type."""
    return CONTENT_TYPE_LATEST


def _empty_classifier(stage: str, report: Any) -> str:
    """Best-effort `outcome=empty` detection across report shapes.

    Each pipeline service has its own report dataclass with its own
    counters. We look at the two most informative fields; if neither
    matches an "empty" condition we fall through to "success".
    """
    if report is None:
        return "success"
    items_seen = getattr(report, "items_seen", None)
    jobs_attempted = getattr(report, "jobs_attempted", None)
    opps_attempted = getattr(report, "opportunities_attempted", None)
    raw_items_seen = getattr(report, "raw_items_seen", None)
    notifications_attempted = getattr(report, "notifications_attempted", None)
    candidates = (
        items_seen,
        jobs_attempted,
        opps_attempted,
        raw_items_seen,
        notifications_attempted,
    )
    for v in candidates:
        if isinstance(v, (int, float)) and v == 0:
            return "empty"
    return "success"


async def record_pipeline_run(
    stage: str, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any
) -> T:
    """Wrap an async service `run_once`-style callable with metrics.

    Increments `radar_pipeline_runs_total{stage, outcome, kind}` and
    observes `radar_pipeline_duration_seconds{stage}`. `kind` is the
    exception class name on errors, the literal string `"none"` on
    success/empty.

    The wrapper is intentionally tiny — services stay metric-agnostic
    and the endpoint layer (`app/api/internal.py`) is the only call site.
    """
    started = time.perf_counter()
    try:
        report = await fn(*args, **kwargs)
    except Exception as exc:
        kind = type(exc).__name__
        PIPELINE_RUNS.labels(stage=stage, outcome="error", kind=kind).inc()
        PIPELINE_DURATION.labels(stage=stage).observe(time.perf_counter() - started)
        raise
    outcome = _empty_classifier(stage, report)
    PIPELINE_RUNS.labels(stage=stage, outcome=outcome, kind="none").inc()
    PIPELINE_DURATION.labels(stage=stage).observe(time.perf_counter() - started)
    return report


def record_web_data_call(provider: str, op: str, outcome: str, chain: list[str]) -> None:
    """One increment per provider attempt inside the fallback chain."""
    WEB_DATA_REQUESTS.labels(
        provider=provider,
        op=op,
        outcome=outcome,
        chain=",".join(chain),
    ).inc()


def record_external_error(provider: str, kind: str) -> None:
    """Coarse external-service error counter (raised out of a provider)."""
    EXTERNAL_ERRORS.labels(provider=provider, kind=kind).inc()


def record_notification(kind: str, provider: str, outcome: str) -> None:
    """One increment per notification dispatch attempt."""
    NOTIFICATIONS.labels(kind=kind, provider=provider, outcome=outcome).inc()


def observe_llm_call(provider: str, op: str, seconds: float) -> None:
    """Manual observation for LLM call sites that want to skip the helper.

    The LLM provider base class calls this directly to avoid pulling a
    full helper into every call site.
    """
    LLM_CALL_DURATION.labels(provider=provider, op=op).observe(seconds)


def observe_research_job(seconds: float) -> None:
    """Manual observation for one research job (single-process_job)."""
    RESEARCH_JOB_DURATION.observe(seconds)


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

def _registry_for_tests() -> CollectorRegistry:
    """Return the live registry. Used by tests that want to read raw
    metric families without going through `render()`."""
    return REGISTRY


def reset_for_tests() -> None:
    """Clear all counter values so test runs don't bleed into each other.

    WARNING: this wipes every metric on the default REGISTRY. Only call
    it from test fixtures, never from production code.
    """
    # `unregister` + re-create is more invasive than necessary. The
    # pragmatic move is to walk the registry's collectors and call
    # `_metrics.clear()` on each — but that's an internal API. The
    # simplest reliable approach for tests is to create a fresh
    # CollectorRegistry per test; see `conftest.py` for the helper.
    raise NotImplementedError(
        "Use `conftest.metrics_registry` (fresh registry per test) instead."
    )


__all__ = [
    "EXTERNAL_ERRORS",
    "HTTP_DURATION",
    "HTTP_REQUESTS",
    "LLM_CALL_DURATION",
    "NOTIFICATIONS",
    "OPPORTUNITIES_BY_STATUS",
    "PIPELINE_DURATION",
    "PIPELINE_RUNS",
    "RESEARCH_JOB_DURATION",
    "RESEARCH_JOBS_PENDING",
    "WEB_DATA_REQUESTS",
    "content_type",
    "observe_llm_call",
    "observe_research_job",
    "record_external_error",
    "record_notification",
    "record_pipeline_run",
    "record_web_data_call",
    "render",
]
