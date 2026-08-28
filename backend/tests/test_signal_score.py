"""Tests for ``app.services.signals`` — Signal Score / state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.signals import (
    SignalBand,
    SignalScoreInputs,
    SignalScoreResult,
    SignalStatus,
    WEIGHTS,
    band_for_score,
    can_transition,
    compute_signal_score,
    evidence_from_source_count,
    freshness_from_age,
)


# ---------------------------------------------------------------------------
# Weight & band helpers
# ---------------------------------------------------------------------------
class TestWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_required_subscores_present(self):
        for k in (
            "freshness",
            "velocity",
            "evidence",
            "novelty",
            "commercial_value",
            "actionability",
            "scarcity",
        ):
            assert k in WEIGHTS


class TestBandForScore:
    def test_low(self):
        assert band_for_score(0) is SignalBand.LOW
        assert band_for_score(49.9) is SignalBand.LOW

    def test_watch(self):
        assert band_for_score(50) is SignalBand.WATCH
        assert band_for_score(69.9) is SignalBand.WATCH

    def test_hot(self):
        assert band_for_score(70) is SignalBand.HOT
        assert band_for_score(84.9) is SignalBand.HOT

    def test_breaking(self):
        assert band_for_score(85) is SignalBand.BREAKING
        assert band_for_score(100) is SignalBand.BREAKING


# ---------------------------------------------------------------------------
# compute_signal_score
# ---------------------------------------------------------------------------
class TestComputeSignalScore:
    def test_zero_inputs_zero_total(self):
        result = compute_signal_score(SignalScoreInputs())
        assert result.total == 0.0
        assert result.band is SignalBand.LOW

    def test_perfect_inputs(self):
        result = compute_signal_score(
            SignalScoreInputs(
                freshness=100,
                velocity=100,
                evidence=100,
                novelty=100,
                commercial_value=100,
                actionability=100,
                scarcity=100,
            )
        )
        assert result.total == 100.0
        assert result.band is SignalBand.BREAKING

    def test_weights_match_formula(self):
        # Only freshness = 100, others 0:
        # total = 100 * 0.20 = 20.
        result = compute_signal_score(SignalScoreInputs(freshness=100))
        assert result.total == pytest.approx(20.0, abs=1e-6)

        # Only novelty = 100: total = 100 * 0.15 = 15.
        result = compute_signal_score(SignalScoreInputs(novelty=100))
        assert result.total == pytest.approx(15.0, abs=1e-6)

    def test_combined_score(self):
        result = compute_signal_score(
            SignalScoreInputs(
                freshness=80,
                velocity=70,
                evidence=60,
                novelty=50,
                commercial_value=40,
                actionability=30,
                scarcity=20,
            )
        )
        expected = (
            80 * 0.20
            + 70 * 0.20
            + 60 * 0.20
            + 50 * 0.15
            + 40 * 0.10
            + 30 * 0.10
            + 20 * 0.05
        )
        assert result.total == pytest.approx(expected, abs=1e-6)

    def test_components_clamped_to_0_100(self):
        result = compute_signal_score(
            SignalScoreInputs(
                freshness=150,   # clamped to 100
                velocity=-50,    # clamped to 0
            )
        )
        assert result.components["freshness"] == 100.0
        assert result.components["velocity"] == 0.0
        # weighted breakdown mirrors clamp:
        assert result.weighted_breakdown["freshness"] == pytest.approx(20.0, abs=1e-6)

    def test_from_signal_row(self):
        # Simulates pulling from an ORM row.
        class Row:
            freshness_score = 50
            velocity_score = 60
            evidence_score = 70
            novelty_score = 80
            commercial_value_score = 30
            actionability_score = 40
            scarcity_score = 20

        result = compute_signal_score(SignalScoreInputs.from_signal_row(Rrow := Row()))
        # expected: 50*.20 + 60*.20 + 70*.20 + 80*.15 + 30*.10 + 40*.10 + 20*.05
        # = 10 + 12 + 14 + 12 + 3 + 4 + 1 = 56
        assert result.total == pytest.approx(56.0, abs=1e-3)
        assert result.band is SignalBand.WATCH

    def test_from_dict_row(self):
        row = {
            "freshness_score": 100,
            "velocity_score": 100,
            "evidence_score": 100,
            "novelty_score": 100,
            "commercial_value_score": 100,
            "actionability_score": 100,
            "scarcity_score": 100,
        }
        result = compute_signal_score(row)
        assert result.total == 100.0
        assert result.band is SignalBand.BREAKING


# ---------------------------------------------------------------------------
# freshness_from_age
# ---------------------------------------------------------------------------
class TestFreshnessFromAge:
    def test_now_is_100(self):
        now = datetime.now(tz=timezone.utc)
        assert freshness_from_age(now, now=now) == pytest.approx(100.0, abs=0.5)

    def test_half_life_is_50(self):
        now = datetime.now(tz=timezone.utc)
        past = now - timedelta(hours=6)
        result = freshness_from_age(past, now=now, half_life_hours=6.0)
        assert result == pytest.approx(50.0, abs=1.0)

    def test_double_half_life_is_25(self):
        now = datetime.now(tz=timezone.utc)
        past = now - timedelta(hours=12)
        result = freshness_from_age(past, now=now, half_life_hours=6.0)
        assert result == pytest.approx(25.0, abs=1.0)

    def test_24h_old_is_zero(self):
        now = datetime.now(tz=timezone.utc)
        past = now - timedelta(hours=24)
        assert freshness_from_age(past, now=now) == 0.0

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime.now() - timedelta(hours=2)
        # Should not raise — naive dt is coerced to UTC.
        result = freshness_from_age(naive)
        assert 0 <= result <= 100

    def test_future_datetime_clamped(self):
        # "Detected in the future" — treat as 100.
        now = datetime.now(tz=timezone.utc)
        future = now + timedelta(hours=1)
        assert freshness_from_age(future, now=now) == 100.0

    def test_none_returns_zero(self):
        assert freshness_from_age(None) == 0.0


# ---------------------------------------------------------------------------
# evidence_from_source_count
# ---------------------------------------------------------------------------
class TestEvidenceFromSourceCount:
    def test_zero_is_zero(self):
        assert evidence_from_source_count(0) == 0.0

    def test_one_is_low(self):
        assert evidence_from_source_count(1) == 30.0

    def test_two_is_medium(self):
        assert evidence_from_source_count(2) == 60.0

    def test_three_is_high(self):
        assert evidence_from_source_count(3) == 85.0

    def test_many_sources_saturate(self):
        # 85 + 5*(n-3), capped at 100.
        assert evidence_from_source_count(6) == 100.0
        assert evidence_from_source_count(100) == 100.0


# ---------------------------------------------------------------------------
# Status state machine
# ---------------------------------------------------------------------------
class TestCanTransition:
    def test_happy_path(self):
        chain = [
            (SignalStatus.DISCOVERED, SignalStatus.VALIDATING),
            (SignalStatus.VALIDATING, SignalStatus.VERIFIED),
            (SignalStatus.VERIFIED, SignalStatus.ANALYZING),
            (SignalStatus.ANALYZING, SignalStatus.PUBLISHED),
            (SignalStatus.PUBLISHED, SignalStatus.EXPIRED),
        ]
        for cur, tgt in chain:
            assert can_transition(cur.value, tgt.value), f"{cur} → {tgt}"

    def test_can_reject_at_any_pre_published_state(self):
        for cur in (
            SignalStatus.DISCOVERED,
            SignalStatus.VALIDATING,
            SignalStatus.VERIFIED,
            SignalStatus.ANALYZING,
        ):
            assert can_transition(cur.value, SignalStatus.REJECTED.value)

    def test_cannot_jump_states(self):
        assert not can_transition("discovered", "verified")
        assert not can_transition("validating", "published")
        assert not can_transition("analyzing", "expired")

    def test_cannot_revert(self):
        assert not can_transition("verified", "discovered")
        assert not can_transition("published", "analyzing")

    def test_terminal_states_block(self):
        assert not can_transition("expired", "published")
        assert not can_transition("rejected", "validating")

    def test_unknown_status_fails_closed(self):
        assert not can_transition("unknown", "discovered")
        assert not can_transition("discovered", "alien")