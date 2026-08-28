"""Tests for the signal consolidator — Phase 14B v2.0.

Covers:

  * Helpers (normalize_title / jaccard) — pure-data edge cases.
  * Content-hash match attaches the new RawItem to an existing Signal.
  * Same-source duplicates (content_hash match from the same source)
    are ignored — we already have it.
  * Title Jaccard above threshold attaches.
  * Title Jaccard below threshold leaves the raw_item unattached.
  * threshold=0 disables the title fallback entirely.
  * Expired / rejected Signals are excluded from candidate matching.
  * Ancient Signals (older than ``title_lookback_days``) are skipped
    for the title path.
  * source_count increments + evidence_score recomputes on attach.
  * Idempotency: re-running with the same raw_item returns
    ``attached=False`` / ``match_basis='already_linked'`` without
    double-incrementing.
  * Unknown raw_item_id raises ``ValueError``.
  * No-match returns ``signal_id=None`` / ``match_basis=None``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers — pure-data
# ---------------------------------------------------------------------------
class TestNormalizeTitle:
    def test_lowercases_and_splits(self):
        from app.services.signals import normalize_title

        toks = normalize_title("Hello World Foo")
        assert toks == frozenset({"hello", "world", "foo"})

    def test_strips_punctuation(self):
        from app.services.signals import normalize_title

        toks = normalize_title("AI-startup, funding round!")
        assert "ai" in toks
        assert "startup" in toks
        assert "funding" in toks
        assert "round" in toks
        # punctuation dropped
        assert "-" not in "".join(toks)
        assert "," not in "".join(toks)

    def test_drops_short_tokens(self):
        from app.services.signals import normalize_title

        toks = normalize_title("a I to be or not")
        # "a", "I", "to", "be", "or" all < 2 chars or single char — filtered
        # "not" is 3 chars → kept
        assert "not" in toks
        assert "a" not in toks
        assert "i" not in toks

    def test_handles_empty_string(self):
        from app.services.signals import normalize_title

        assert normalize_title("") == frozenset()
        assert normalize_title(None) == frozenset()  # type: ignore[arg-type]

    def test_keeps_cjk_runs(self):
        from app.services.signals import normalize_title

        # CJK has no whitespace word boundaries — we treat each punctuation-
        # separated run as a token.
        toks = normalize_title("AI 工具 商业化")
        assert "ai" in toks
        # The CJK run is preserved as one token (lowercased trivially).
        assert any("工具" in t or "商业化" in t for t in toks)


class TestJaccard:
    def test_identical_sets(self):
        from app.services.signals import jaccard, normalize_title

        s = normalize_title("AI startup funding")
        assert jaccard(s, s) == 1.0

    def test_disjoint_sets(self):
        from app.services.signals import jaccard, normalize_title

        a = normalize_title("AI startup funding")
        b = normalize_title("recipe chocolate cake")
        assert jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        from app.services.signals import jaccard, normalize_title

        a = normalize_title("AI startup funding round")
        b = normalize_title("AI startup launch")
        # overlap: {ai, startup} → 2; union: {ai, startup, funding, round, launch} → 5
        assert jaccard(a, b) == pytest.approx(2 / 5)

    def test_empty_returns_zero(self):
        from app.services.signals import jaccard, normalize_title

        assert jaccard(frozenset(), normalize_title("anything")) == 0.0
        assert jaccard(frozenset(), frozenset()) == 0.0


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
async def _seed_source(sessionmaker, *, name: str = "src-A") -> int:
    from app.models import Source

    async with sessionmaker() as session:
        row = Source(name=name, type="rss", url=f"https://example.com/{name}")
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _seed_raw_item(
    sessionmaker,
    *,
    source_id: int,
    external_id: str = "ext-1",
    title: str = "Original headline",
    content: str = "Original content body",
) -> tuple[int, str]:
    from app.models import RawItem

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    async with sessionmaker() as session:
        row = RawItem(
            source_id=source_id,
            external_id=external_id,
            url=f"https://example.com/{external_id}",
            title=title,
            content=content,
            content_hash=content_hash,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id, content_hash


async def _seed_signal(
    sessionmaker,
    *,
    raw_item_id: int,
    source_count: int = 1,
    status: str = "discovered",
    detected_at: datetime | None = None,
    source_id: int | None = None,
) -> int:
    from app.models import Signal, SignalSource

    async with sessionmaker() as session:
        sig = Signal(
            raw_item_id=raw_item_id,
            signal_type="news",
            status=status,
            source_count=source_count,
            detected_at=detected_at or datetime.now(tz=timezone.utc),
            evidence_score=30.0,
        )
        session.add(sig)
        await session.flush()
        # Attach the anchor raw_item to the SignalSource link table as well,
        # so the consolidator can find it via the content-hash path.
        session.add(
            SignalSource(
                signal_id=sig.id,
                raw_item_id=raw_item_id,
                relevance=1.0,
                evidence_type="anchor",
            )
        )
        await session.commit()
        await session.refresh(sig)
        return sig.id


# ---------------------------------------------------------------------------
# Content-hash attach
# ---------------------------------------------------------------------------
class TestContentHashMatch:
    async def test_attach_when_same_hash_different_source(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]

        anchor_id, content_hash = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            content="Breaking: AI raises $50M Series A",
        )
        signal_id = await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
        )

        # New raw_item from src-B, same content hash, different title
        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-1",
            title="Different title, same body",
            content="Breaking: AI raises $50M Series A",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(session, raw_item_id=new_id)
            await session.commit()

        assert result.attached is True
        assert result.signal_id == signal_id
        assert result.match_basis == "content_hash"
        assert result.source_count == 2

        # SignalSource link table now has 2 rows for that signal
        from app.models import SignalSource as SS

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            links = list(
                (
                    await session.execute(
                        select(SS).where(SS.signal_id == signal_id)
                    )
                ).scalars().all()
            )
        assert len(links) == 2
        # evidence_score recomputed: source_count=2 → 60.0
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            from app.models import Signal

            sig = await session.get(Signal, signal_id)
        assert sig.source_count == 2
        assert sig.evidence_score == pytest.approx(60.0)

    async def test_same_source_duplicate_is_ignored(self, client):
        """Same source re-importing its own article shouldn't double-count."""
        from app.services.signals import consolidate_raw_item

        src = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        anchor_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src,
            external_id="anchor-1",
            content="Body",
        )
        await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
        )

        # New row with same hash from the SAME source — the SQL filters
        # this out so we don't loop on re-ingestion.
        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src,
            external_id="dup-1",
            title="Same source, same body",
            content="Body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(session, raw_item_id=new_id)
        assert result.attached is False
        assert result.match_basis is None


# ---------------------------------------------------------------------------
# Title-Jaccard attach
# ---------------------------------------------------------------------------
class TestTitleJaccardMatch:
    async def test_attach_when_jaccard_above_threshold(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        anchor_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            title="AI startup raises 50 million series funding",
            content="body A",
        )
        signal_id = await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
        )

        # Distinct content hash but very similar title → Jaccard above 0.6
        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-2",
            title="AI startup raises 50 million series funding round",
            content="body B — totally different",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(
                session, raw_item_id=new_id, title_jaccard_threshold=0.6
            )
            await session.commit()

        assert result.attached is True
        assert result.signal_id == signal_id
        assert result.match_basis == "title_jaccard"

    async def test_no_attach_when_jaccard_below_threshold(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        anchor_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            title="AI startup raises 50 million series funding",
            content="body A",
        )
        await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
        )

        # Different topic, share 1 token → Jaccard << 0.6
        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="unrelated",
            title="chocolate cake recipe best oven",
            content="body B",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(
                session, raw_item_id=new_id, title_jaccard_threshold=0.6
            )
        assert result.attached is False
        assert result.signal_id is None
        assert result.match_basis is None

    async def test_threshold_zero_disables_title_fallback(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        anchor_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            title="AI startup raises 50 million series funding",
            content="body A",
        )
        await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
        )

        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-3",
            title="AI startup raises 50 million series funding round",
            content="totally different body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(
                session, raw_item_id=new_id, title_jaccard_threshold=0.0
            )
        assert result.attached is False
        assert result.match_basis is None


# ---------------------------------------------------------------------------
# Exclusion of expired / rejected / ancient signals
# ---------------------------------------------------------------------------
class TestExclusions:
    async def test_expired_signal_excluded(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        anchor_id, hash_ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            title="Old headline",
            content="Old body",
        )
        await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
            status="expired",
        )

        # New raw_item with the same content_hash from a different source.
        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-1",
            title="New headline",
            content="Old body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(session, raw_item_id=new_id)
        assert result.attached is False
        assert result.signal_id is None

    async def test_rejected_signal_excluded(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        anchor_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            title="Headline",
            content="Body",
        )
        await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
            status="rejected",
        )

        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-1",
            title="Headline",
            content="Body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(session, raw_item_id=new_id)
        assert result.attached is False

    async def test_ancient_signal_excluded_from_title_path(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        anchor_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            title="AI startup raises 50 million series funding",
            content="body A",
        )
        await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
            detected_at=datetime.now(tz=timezone.utc) - timedelta(days=30),
        )

        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-1",
            title="AI startup raises 50 million series funding round",
            content="body B — different",
        )
        # default lookback is 7 days, anchor is 30 days old → excluded
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(
                session,
                raw_item_id=new_id,
                title_jaccard_threshold=0.6,
            )
        assert result.attached is False


# ---------------------------------------------------------------------------
# Idempotency + edge cases
# ---------------------------------------------------------------------------
class TestIdempotency:
    async def test_already_linked_returns_already_linked_basis(self, client):
        from app.services.signals import consolidate_raw_item

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        anchor_id, hash_ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            content="Body",
        )
        signal_id = await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=1,
        )

        # Attach once.
        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-1",
            content="Body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r1 = await consolidate_raw_item(session, raw_item_id=new_id)
            await session.commit()
        assert r1.attached is True

        # Run again — must NOT re-increment source_count.
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r2 = await consolidate_raw_item(session, raw_item_id=new_id)
            await session.commit()
        assert r2.attached is False
        assert r2.match_basis == "already_linked"
        assert r2.signal_id == signal_id
        assert r2.source_count == 2  # still 2, not 3

    async def test_no_match_returns_none_signal_id(self, client):
        from app.services.signals import consolidate_raw_item

        src = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src,
            external_id="lonely-1",
            title="Lone headline",
            content="Lone body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            result = await consolidate_raw_item(session, raw_item_id=new_id)
        assert result.attached is False
        assert result.signal_id is None
        assert result.match_basis is None
        assert result.source_count == 0

    async def test_unknown_raw_item_id_raises(self, client):
        from app.services.signals import consolidate_raw_item

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            with pytest.raises(ValueError, match="not found"):
                await consolidate_raw_item(session, raw_item_id=999_999)

    async def test_attach_with_three_sources_recomputes_evidence(self, client):
        """source_count=3 → evidence_score=85.0 per scorer curve."""
        from app.services.signals import consolidate_raw_item
        from app.models import Signal

        src_a = await _seed_source(client.sessionmaker, name="src-A")  # type: ignore[attr-defined]
        src_b = await _seed_source(client.sessionmaker, name="src-B")  # type: ignore[attr-defined]
        src_c = await _seed_source(client.sessionmaker, name="src-C")  # type: ignore[attr-defined]

        anchor_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_a,
            external_id="anchor-1",
            content="Body",
        )
        signal_id = await _seed_signal(
            client.sessionmaker,  # type: ignore[attr-defined]
            raw_item_id=anchor_id,
            source_count=2,  # pretend two sources already attached
        )

        new_id, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_b,
            external_id="dup-1",
            content="Body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r1 = await consolidate_raw_item(session, raw_item_id=new_id)
            await session.commit()
        assert r1.source_count == 3

        new_id2, _ = await _seed_raw_item(
            client.sessionmaker,  # type: ignore[attr-defined]
            source_id=src_c,
            external_id="dup-2",
            content="Body",
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            r2 = await consolidate_raw_item(session, raw_item_id=new_id2)
            await session.commit()
        assert r2.source_count == 4
        # 4 sources → 85 + 5*1 = 90
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            sig = await session.get(Signal, signal_id)
        assert sig.evidence_score == pytest.approx(90.0)
