"""Opportunity synthesis — cluster of RawItems → Opportunity + links.

The synthesizer owns the decisions that turn a cluster of 1..N stories
into a single business opportunity. Choices:

  * representative_item   — the highest-engagement source in the cluster;
                            its title becomes the opportunity title
  * slug                  — deterministic sha256 over the sorted
                            (raw_item_id, content_hash) tuples so
                            re-clustering the same set of items
                            produces the SAME slug (idempotency)
  * summary               — concatenate top content snippets, capped
                            at 1000 characters
  * category              — most-common category keyword across items
                            (falls back to "general")
  * source_count          — number of items in the cluster

The opportunity scoring columns are intentionally left at 0 here —
Phase 6 populates them after AI screening.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.models import Opportunity, RawItem

SLUG_MAX_LEN = 240  # DB column is 512 — leave headroom
SUMMARY_MAX_LEN = 1000
TITLE_MAX_LEN = 500

# Engagement keys we know about, in priority order.
ENGAGEMENT_KEYS = (
    "stars",
    "points",
    "score",
    "upvotes",
    "votes",
    "rank",
    "comments",
    "comment_count",
    "comments_count",
    "forks",
)


@dataclass(slots=True)
class SynthesisResult:
    """A synthesised Opportunity ready to persist + the link rows."""

    opportunity_fields: dict[str, Any]
    links: list[dict[str, Any]]  # [{raw_item_id, relevance}]
    representative: RawItem


def _slugify(text: str, max_len: int = 60) -> str:
    """Lower-cased, hyphen-separated slug — only used as display fallback."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len] or "opportunity"


def _stable_slug(members: list[RawItem]) -> str:
    """sha256 over the sorted (raw_item_id, content_hash) tuples.

    Same set of stories → same slug → upsert_by_slug() returns the
    existing Opportunity rather than creating a duplicate.
    """
    seed = "|".join(
        f"{m.id}:{m.content_hash}" for m in sorted(members, key=lambda r: r.id)
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"opp-{digest[:SLUG_MAX_LEN - 4]}"


def _engagement_score(item: RawItem) -> float:
    md = item.metadata_json or {}
    score = 0.0
    for key in ENGAGEMENT_KEYS:
        value = md.get(key)
        if isinstance(value, (int, float)):
            score += float(value)
    return score


def pick_representative(items: list[RawItem]) -> RawItem:
    """Highest engagement wins; ties broken by oldest published_at."""
    if len(items) == 1:
        return items[0]
    return max(
        items,
        key=lambda r: (
            _engagement_score(r),
            -(r.published_at.timestamp() if r.published_at else 0.0),
        ),
    )


def aggregate_summary(items: list[RawItem]) -> str:
    """Concatenate the first non-empty content snippets, capped."""
    parts: list[str] = []
    for item in items:
        text = (item.content or "").strip()
        if not text:
            continue
        parts.append(f"[{item.title}] {text}")
    blob = " \n".join(parts)
    if len(blob) > SUMMARY_MAX_LEN:
        blob = blob[: SUMMARY_MAX_LEN - 3].rstrip() + "..."
    return blob


def aggregate_category(items: list[RawItem]) -> Optional[str]:
    """Most-common non-empty category hint in metadata, else None."""
    from collections import Counter

    counter: Counter[str] = Counter()
    for item in items:
        md = item.metadata_json or {}
        cat = md.get("category")
        if isinstance(cat, str) and cat.strip():
            counter[cat.strip().lower()] += 1
        topics = md.get("topics") or []
        if isinstance(topics, list):
            for t in topics:
                if isinstance(t, str) and t.strip():
                    counter[t.strip().lower()] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def synthesize_cluster(items: list[RawItem]) -> SynthesisResult:
    """Turn a non-empty list of RawItems into a SynthesisResult."""
    if not items:
        raise ValueError("synthesize_cluster requires at least one RawItem")

    representative = pick_representative(items)
    title = (representative.title or "(untitled)")[:TITLE_MAX_LEN]
    slug = _stable_slug(items)
    summary = aggregate_summary(items)
    category = aggregate_category(items)

    opportunity_fields: dict[str, Any] = {
        "title": title,
        "slug": slug,
        "summary": summary or None,
        "category": category,
        "market": None,
        "target_user": None,
        "source_count": len(items),
        "trend_score": 0.0,
        "demand_score": 0.0,
        "monetization_score": 0.0,
        "competition_gap_score": 0.0,
        "china_gap_score": 0.0,
        "execution_score": 0.0,
        "total_score": 0.0,
        "status": "detected",
    }

    # Cluster weight decays with rank — the representative carries
    # the most relevance; later items are echoes.
    links: list[dict[str, Any]] = []
    for rank, item in enumerate(items):
        relevance = 1.0 / (1 + rank)  # 1.0, 0.5, 0.33, …
        links.append({"raw_item_id": item.id, "relevance": float(relevance)})

    return SynthesisResult(
        opportunity_fields=opportunity_fields,
        links=links,
        representative=representative,
    )


__all__ = [
    "SLUG_MAX_LEN",
    "SynthesisResult",
    "_slugify",
    "aggregate_category",
    "aggregate_summary",
    "pick_representative",
    "synthesize_cluster",
    "_stable_slug",
]
