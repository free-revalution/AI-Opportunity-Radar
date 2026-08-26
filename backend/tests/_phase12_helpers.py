"""Helpers shared by Phase 12 metrics tests.

We can't reset the default `prometheus_client.REGISTRY` between tests, so
we measure the *delta* on a Counter or HistogramCount before vs after a
specific call. This keeps each test isolated even though every test
shares the registry.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Histogram

from app.metrics import render


def counter_value(name: str, labels: dict[str, str]) -> float:
    """Return the current value of *name* with the exact *labels* combo."""
    text = render().decode()
    needle = _format_label_block(name, labels)
    for line in text.splitlines():
        if line.startswith(needle):
            parts = line.rsplit(" ", 1)
            return float(parts[-1])
    return 0.0


def histogram_count(name: str, labels: dict[str, str] | None = None) -> float:
    """Sum of `_count` samples for a histogram (single + cumulative series)."""
    target = f"{name}_count"
    text = render().decode()
    label_block = _format_label_block(target, labels or {})
    total = 0.0
    for line in text.splitlines():
        if line.startswith(label_block):
            total += float(line.rsplit(" ", 1)[-1])
    return total


def _format_label_block(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return f"{name} "
    # Sort by name so the order matches `prometheus_client` (it sorts
    # alphabetically before emitting).
    body = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{body}}} "


def labels_of(counter: Counter | Histogram, **kwargs: Any) -> Any:
    """Convenience wrapper around `.labels(...)` for tests."""
    return counter.labels(**kwargs)


__all__ = ["counter_value", "histogram_count", "labels_of"]
