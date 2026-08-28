"""Signal consolidation — Phase 14B v2.0.

When the same news / release / product shows up on multiple sources,
we don't want to spawn a parallel Signal that duplicates everything the
first one already tracks. Instead, the second source's `RawItem` gets
**attached** to the existing `Signal` via `signal_sources`, the parent
Signal's `source_count` goes up, and its `evidence_score` is recomputed.

Two match strategies, in priority order:

  1. ``content_hash`` — exact SHA-256 match on the RawItem body. Free,
     unambiguous, and already populated by the ingestion pipeline.
  2. ``title_jaccard`` — token-set Jaccard on the normalized title,
     against any *recent, live* Signal. Skipped when ``threshold == 0``.

Expired / rejected Signals are never consolidated into — we want fresh
evidence to back fresh signals, not pile up on dead ones.

The function never **creates** a Signal. If nothing matches it returns
``attached=False`` and the caller is expected to create a new Signal
via the detector path. This keeps consolidation a pure "merge or pass"
operation.

This module is async + session-bound (the table joins require a DB).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawItem, Signal, SignalSource
# ``scorer`` is a re-exported surface on the signals package itself
# (the scoring primitives live in ``signals/__init__.py``).
from app.services.signals import evidence_from_source_count


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TITLE_JACCARD_THRESHOLD = 0.6
# Look-back window for title-Jaccard candidates. Content-hash matches have
# no time bound (they're exact duplicates), but a title-based match against
# a 6-month-old Signal is almost always a different story with similar
# wording — we cap candidate age to keep this conservative.
DEFAULT_TITLE_LOOKBACK_DAYS = 7

# Signal lifecycle states we won't merge new evidence into. Anything
# terminal or invalidated should stay that way — adding more sources
# wouldn't make an "expired" signal come back to life.
_EXCLUDED_STATUSES: frozenset[str] = frozenset({"expired", "rejected"})

# Short / noise tokens dropped during title normalization.
_MIN_TOKEN_LEN = 2
_TOKEN_RE = re.compile(r"[^\w一-鿿]+", re.UNICODE)
# Chinese-character runs are kept as single tokens (one token = one "word"
# is the wrong model for CJK; we instead split by punctuation only and
# treat each CJK run as one bag-of-chars). For mixed titles this still
# gives reasonable overlap on the English half.


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ConsolidationResult:
    """Outcome of one consolidation attempt.

    Attributes:
      signal_id:         Existing Signal the raw_item was attached to
                         (``None`` if no match).
      attached:          True if a new ``SignalSource`` row was inserted
                         during this call. False if the raw_item was
                         already linked, no match found, or skipped.
      raw_item_id:       The RawItem we tried to attach.
      source_count:      ``Signal.source_count`` *after* this call.
                         ``0`` when no match was found.
      match_basis:       ``"content_hash"`` / ``"title_jaccard"`` /
                         ``"already_linked"`` / ``None``.
      matched_signal_status: status of the matched Signal, for logging.
    """

    signal_id: Optional[int]
    attached: bool
    raw_item_id: int
    source_count: int
    match_basis: Optional[str]
    matched_signal_status: Optional[str]


# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------
def normalize_title(title: str) -> frozenset[str]:
    """Tokenize a title into a frozenset of lowercase word tokens.

    Strips punctuation (ASCII + CJK), drops short tokens (<2 chars),
    lower-cases the result. The output is hashable so it composes
    nicely in Jaccard calculations.
    """
    if not title:
        return frozenset()
    cleaned = _TOKEN_RE.sub(" ", title.lower())
    return frozenset(t for t in cleaned.split() if len(t) >= _MIN_TOKEN_LEN)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Token-set Jaccard similarity in [0, 1]. Empty sets return 0."""
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def consolidate_raw_item(
    session: AsyncSession,
    *,
    raw_item_id: int,
    title_jaccard_threshold: float = DEFAULT_TITLE_JACCARD_THRESHOLD,
    title_lookback_days: int = DEFAULT_TITLE_LOOKBACK_DAYS,
    now: datetime | None = None,
) -> ConsolidationResult:
    """Try to attach ``raw_item_id`` to an existing Signal.

    The function is **idempotent**: if the raw_item is already linked to
    a Signal it returns ``attached=False`` with ``match_basis =
    "already_linked"``. Re-running the consolidation on the same input
    is safe.

    Side-effects on match: inserts one ``SignalSource`` row, increments
    ``Signal.source_count`` by 1, recomputes ``Signal.evidence_score``
    via ``evidence_from_source_count``. The caller is responsible for
    committing the transaction.

    Returns a :class:`ConsolidationResult`. ``signal_id is None`` and
    ``match_basis is None`` when no candidate matched — the caller
    should then create a new Signal from this raw_item.
    """
    raw_item = await session.get(RawItem, raw_item_id)
    if raw_item is None:
        raise ValueError(f"RawItem id={raw_item_id} not found")

    # --- 1. Idempotency guard -----------------------------------------
    existing_link: Optional[SignalSource] = await session.scalar(
        select(SignalSource).where(SignalSource.raw_item_id == raw_item_id)
    )
    if existing_link is not None:
        sig = await session.get(Signal, existing_link.signal_id)
        return ConsolidationResult(
            signal_id=existing_link.signal_id,
            attached=False,
            raw_item_id=raw_item_id,
            source_count=sig.source_count if sig is not None else 0,
            match_basis="already_linked",
            matched_signal_status=sig.status if sig is not None else None,
        )

    # --- 2. Content-hash match (highest priority, no time bound) ------
    matched: Optional[Signal] = None
    basis: Optional[str] = None

    if raw_item.content_hash:
        sid: Optional[int] = await session.scalar(
            select(SignalSource.signal_id)
            .join(RawItem, RawItem.id == SignalSource.raw_item_id)
            .where(
                RawItem.content_hash == raw_item.content_hash,
                RawItem.id != raw_item_id,
                RawItem.source_id != raw_item.source_id,
            )
            .limit(1)
        )
        if sid is not None:
            cand = await session.get(Signal, sid)
            if cand is not None and cand.status not in _EXCLUDED_STATUSES:
                matched = cand
                basis = "content_hash"

    # --- 3. Title-Jaccard fallback (time-bounded) ---------------------
    if matched is None and title_jaccard_threshold > 0:
        now = now or datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(days=title_lookback_days)
        target_tokens = normalize_title(raw_item.title)
        if target_tokens:
            rows = await session.execute(
                select(Signal, RawItem)
                .join(RawItem, RawItem.id == Signal.raw_item_id)
                .where(
                    Signal.status.notin_(_EXCLUDED_STATUSES),
                    Signal.detected_at.is_not(None),
                    Signal.detected_at >= cutoff,
                    RawItem.id != raw_item_id,
                )
            )
            best_signal: Optional[Signal] = None
            best_score = 0.0
            for sig, anchor in rows.all():
                anchor_tokens = normalize_title(anchor.title)
                if not anchor_tokens:
                    continue
                score = jaccard(target_tokens, anchor_tokens)
                if score >= title_jaccard_threshold and score > best_score:
                    best_signal = sig
                    best_score = score
            if best_signal is not None:
                matched = best_signal
                basis = "title_jaccard"

    # --- 4. No match — caller creates a new Signal ---------------------
    if matched is None:
        return ConsolidationResult(
            signal_id=None,
            attached=False,
            raw_item_id=raw_item_id,
            source_count=0,
            match_basis=None,
            matched_signal_status=None,
        )

    # --- 5. Attach: new SignalSource + score update -------------------
    session.add(
        SignalSource(
            signal_id=matched.id,
            raw_item_id=raw_item_id,
            relevance=1.0,
            evidence_type="consolidated",
        )
    )
    matched.source_count = (matched.source_count or 0) + 1
    matched.evidence_score = evidence_from_source_count(matched.source_count)
    await session.flush()

    return ConsolidationResult(
        signal_id=matched.id,
        attached=True,
        raw_item_id=raw_item_id,
        source_count=matched.source_count,
        match_basis=basis,
        matched_signal_status=matched.status,
    )


__all__ = [
    "ConsolidationResult",
    "DEFAULT_TITLE_JACCARD_THRESHOLD",
    "DEFAULT_TITLE_LOOKBACK_DAYS",
    "consolidate_raw_item",
    "jaccard",
    "normalize_title",
]
