"""
Tests for jugeo.orchestration.fleet_competition.models

Covers:
  - Module-level helpers: _clamp, _safe_mean, _safe_std, _moving_average
  - Enums: BidStatus, RoundPhase, CalibrationStatus
  - BidDelta (frozen value object)
  - CompetitiveBid (construction, serialisation, validation, delta_from)
  - FleetRound (phase machine, add_bid, determine_winner, summarize)
  - ChallengeRecord (resolve, is_pending, age_seconds, serialisation)
  - CalibrationTrace (add_sample, moving_average, calibration_score, export_csv)
  - Integration with jugeo.orchestration.fleet (FleetMember, FleetBid)
    and jugeo.evidence.trust (TrustLevel)
"""
from pathlib import Path
import sys
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math
import time
import uuid

import pytest

from jugeo.orchestration.fleet_competition.models import (
    # helpers
    _clamp,
    _safe_mean,
    _safe_std,
    # constants
    MAX_LATENCY,
    MIN_SAMPLES_FOR_FRESH,
    CALIBRATION_WEIGHT_ACCURACY,
    CALIBRATION_WEIGHT_LATENCY,
    CALIBRATION_WEIGHT_TRUST,
    CALIBRATION_TRAILING_WINDOW,
    # enums
    BidStatus,
    RoundPhase,
    CalibrationStatus,
    # classes
    BidDelta,
    CompetitiveBid,
    FleetRound,
    ChallengeRecord,
    CalibrationTrace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_bid(
    *,
    move_id: str = "move-1",
    bidder_id: str = "bidder-1",
    bid_value: float = 50.0,
    semantic_score: float = 0.8,
    uncertainty: float = 0.2,
    capabilities: list = None,
    trust_ceiling: float = 0.9,
    status: BidStatus = BidStatus.PENDING,
) -> CompetitiveBid:
    return CompetitiveBid(
        move_id=move_id,
        bidder_id=bidder_id,
        bid_value=bid_value,
        semantic_score=semantic_score,
        uncertainty=uncertainty,
        capabilities=capabilities or ["cap-a"],
        trust_ceiling=trust_ceiling,
        status=status,
    )


# ---------------------------------------------------------------------------
# _clamp
# ---------------------------------------------------------------------------

class TestClamp:
    def test_within_range(self):
        assert _clamp(0.5, 0.0, 1.0) == 0.5

    def test_below_lower(self):
        assert _clamp(-0.1, 0.0, 1.0) == 0.0

    def test_above_upper(self):
        assert _clamp(1.5, 0.0, 1.0) == 1.0

    def test_at_lower_bound(self):
        assert _clamp(0.0, 0.0, 1.0) == 0.0

    def test_at_upper_bound(self):
        assert _clamp(1.0, 0.0, 1.0) == 1.0

    def test_negative_range(self):
        assert _clamp(-5.0, -10.0, -1.0) == -5.0

    def test_negative_clamp_below(self):
        assert _clamp(-20.0, -10.0, 0.0) == -10.0

    def test_inverted_range_raises(self):
        with pytest.raises(ValueError):
            _clamp(0.5, 1.0, 0.0)

    def test_large_positive(self):
        assert _clamp(1e9, 0.0, 1.0) == 1.0

    def test_very_negative(self):
        assert _clamp(-1e9, 0.0, 1.0) == 0.0

    def test_equal_bounds(self):
        assert _clamp(0.5, 0.3, 0.3) == 0.3


# ---------------------------------------------------------------------------
# _safe_mean
# ---------------------------------------------------------------------------

class TestSafeMean:
    def test_empty_returns_zero(self):
        assert _safe_mean([]) == 0.0

    def test_single_element(self):
        assert _safe_mean([3.5]) == 3.5

    def test_multiple_elements(self):
        result = _safe_mean([1.0, 2.0, 3.0])
        assert abs(result - 2.0) < 1e-9

    def test_all_zeros(self):
        assert _safe_mean([0.0, 0.0, 0.0]) == 0.0

    def test_floats_close(self):
        result = _safe_mean([0.1, 0.2, 0.3])
        assert abs(result - 0.2) < 1e-9


# ---------------------------------------------------------------------------
# _safe_std
# ---------------------------------------------------------------------------

class TestSafeStd:
    def test_empty_returns_zero(self):
        assert _safe_std([]) == 0.0

    def test_single_element_returns_zero(self):
        assert _safe_std([5.0]) == 0.0

    def test_two_equal_elements(self):
        assert _safe_std([3.0, 3.0]) == 0.0

    def test_two_different_elements(self):
        result = _safe_std([0.0, 2.0])
        expected = 2.0 ** 0.5  # sample std of [0,2] = sqrt(2)
        assert abs(result - expected) < 1e-9

    def test_larger_sequence(self):
        result = _safe_std([1.0, 2.0, 3.0, 4.0, 5.0])
        assert result > 0.0


# ---------------------------------------------------------------------------
# BidStatus enum
# ---------------------------------------------------------------------------

class TestBidStatus:
    def test_all_values_exist(self):
        statuses = {s.value for s in BidStatus}
        assert "pending" in statuses
        assert "accepted" in statuses
        assert "rejected" in statuses
        assert "challenged" in statuses
        assert "expired" in statuses

    def test_is_str_enum(self):
        assert isinstance(BidStatus.PENDING, str)
        assert BidStatus.PENDING == "pending"

    def test_comparison(self):
        assert BidStatus.PENDING != BidStatus.ACCEPTED
        assert BidStatus.ACCEPTED == BidStatus.ACCEPTED

    def test_from_string(self):
        assert BidStatus("pending") is BidStatus.PENDING
        assert BidStatus("accepted") is BidStatus.ACCEPTED

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            BidStatus("nonexistent")


# ---------------------------------------------------------------------------
# RoundPhase enum
# ---------------------------------------------------------------------------

class TestRoundPhase:
    def test_all_values(self):
        values = {p.value for p in RoundPhase}
        assert "open" in values
        assert "evaluating" in values
        assert "closed" in values
        assert "archived" in values

    def test_is_str_enum(self):
        assert isinstance(RoundPhase.OPEN, str)
        assert RoundPhase.OPEN == "open"

    def test_ordering(self):
        phases = [RoundPhase.OPEN, RoundPhase.EVALUATING, RoundPhase.CLOSED, RoundPhase.ARCHIVED]
        assert len(phases) == 4


# ---------------------------------------------------------------------------
# CalibrationStatus enum
# ---------------------------------------------------------------------------

class TestCalibrationStatus:
    def test_all_values(self):
        values = {s.value for s in CalibrationStatus}
        assert "fresh" in values
        assert "stale" in values
        assert "degraded" in values
        assert "invalid" in values

    def test_string_equality(self):
        assert CalibrationStatus.FRESH == "fresh"

    def test_from_string(self):
        assert CalibrationStatus("degraded") is CalibrationStatus.DEGRADED


# ---------------------------------------------------------------------------
# BidDelta
# ---------------------------------------------------------------------------

class TestBidDelta:
    def test_construction(self):
        delta = BidDelta(
            bid_id_a="a",
            bid_id_b="b",
            value_delta=10.0,
            score_delta=0.2,
            uncertainty_delta=-0.1,
            dominant=True,
        )
        assert delta.bid_id_a == "a"
        assert delta.bid_id_b == "b"
        assert delta.value_delta == 10.0
        assert delta.score_delta == 0.2
        assert delta.uncertainty_delta == -0.1
        assert delta.dominant is True

    def test_frozen(self):
        delta = BidDelta("a", "b", 1.0, 0.1, -0.05, True)
        with pytest.raises(Exception):
            delta.value_delta = 99.0  # type: ignore

    def test_is_improvement_positive_both(self):
        delta = BidDelta("a", "b", 5.0, 0.1, -0.1, True)
        assert delta.is_improvement() is True

    def test_is_improvement_negative_value(self):
        delta = BidDelta("a", "b", -1.0, 0.1, -0.1, False)
        assert delta.is_improvement() is False

    def test_is_improvement_negative_score(self):
        delta = BidDelta("a", "b", 5.0, -0.1, -0.1, False)
        assert delta.is_improvement() is False

    def test_is_improvement_zero_deltas(self):
        delta = BidDelta("a", "b", 0.0, 0.0, 0.0, False)
        assert delta.is_improvement() is False

    def test_to_dict_keys(self):
        delta = BidDelta("a", "b", 2.0, 0.3, -0.05, True)
        d = delta.to_dict()
        assert "bid_id_a" in d
        assert "bid_id_b" in d
        assert "value_delta" in d
        assert "score_delta" in d
        assert "uncertainty_delta" in d
        assert "dominant" in d
        assert "is_improvement" in d

    def test_to_dict_values(self):
        delta = BidDelta("x", "y", 3.0, 0.5, 0.1, False)
        d = delta.to_dict()
        assert d["bid_id_a"] == "x"
        assert d["bid_id_b"] == "y"
        assert d["value_delta"] == 3.0
        assert d["dominant"] is False

    def test_dominant_flag_consistency(self):
        delta = BidDelta("a", "b", 1.0, 0.1, -0.05, True)
        assert delta.dominant is True

    def test_not_dominant_when_worse_score(self):
        delta = BidDelta("a", "b", 1.0, -0.1, -0.1, False)
        assert delta.dominant is False


# ---------------------------------------------------------------------------
# CompetitiveBid
# ---------------------------------------------------------------------------

class TestCompetitiveBid:
    def test_construction_defaults(self):
        bid = make_bid()
        assert bid.move_id == "move-1"
        assert bid.bidder_id == "bidder-1"
        assert bid.bid_value == 50.0
        assert bid.semantic_score == 0.8
        assert bid.uncertainty == 0.2
        assert bid.trust_ceiling == 0.9
        assert bid.status == BidStatus.PENDING
        assert isinstance(bid.bid_id, str) and len(bid.bid_id) > 0

    def test_auto_bid_id(self):
        b1 = make_bid()
        b2 = make_bid()
        assert b1.bid_id != b2.bid_id

    def test_explicit_bid_id(self):
        bid = CompetitiveBid(
            move_id="m", bidder_id="b", bid_value=10.0,
            semantic_score=0.5, uncertainty=0.5, capabilities=[],
            trust_ceiling=0.5, bid_id="fixed-id",
        )
        assert bid.bid_id == "fixed-id"

    def test_mutable_status(self):
        bid = make_bid()
        bid.status = BidStatus.ACCEPTED
        assert bid.status == BidStatus.ACCEPTED

    def test_to_dict_keys(self):
        bid = make_bid()
        d = bid.to_dict()
        required = {"bid_id", "move_id", "bidder_id", "bid_value",
                    "semantic_score", "uncertainty", "capabilities",
                    "trust_ceiling", "timestamp", "metadata", "status"}
        assert required.issubset(d.keys())

    def test_to_dict_status_string(self):
        bid = make_bid()
        d = bid.to_dict()
        assert d["status"] == "pending"

    def test_to_dict_accepted(self):
        bid = make_bid(status=BidStatus.ACCEPTED)
        d = bid.to_dict()
        assert d["status"] == "accepted"

    def test_from_dict_roundtrip(self):
        bid = make_bid()
        d = bid.to_dict()
        bid2 = CompetitiveBid.from_dict(d)
        assert bid2.bid_id == bid.bid_id
        assert bid2.move_id == bid.move_id
        assert bid2.bid_value == bid.bid_value
        assert bid2.semantic_score == bid.semantic_score
        assert bid2.uncertainty == bid.uncertainty
        assert bid2.trust_ceiling == bid.trust_ceiling
        assert bid2.status == bid.status

    def test_from_dict_missing_optional(self):
        d = {
            "move_id": "m", "bidder_id": "b",
            "bid_value": 5.0, "semantic_score": 0.5,
            "uncertainty": 0.3, "capabilities": [],
            "trust_ceiling": 0.7,
        }
        bid = CompetitiveBid.from_dict(d)
        assert bid.move_id == "m"
        assert bid.status == BidStatus.PENDING

    def test_from_dict_status_roundtrip(self):
        d = {
            "move_id": "m", "bidder_id": "b",
            "bid_value": 5.0, "semantic_score": 0.5,
            "uncertainty": 0.3, "capabilities": [],
            "trust_ceiling": 0.7, "status": "accepted",
        }
        bid = CompetitiveBid.from_dict(d)
        assert bid.status == BidStatus.ACCEPTED

    def test_validate_ok(self):
        bid = make_bid()
        assert bid.validate() == []

    def test_validate_empty_move_id(self):
        bid = make_bid(move_id="")
        errors = bid.validate()
        assert any("move_id" in e for e in errors)

    def test_validate_empty_bidder_id(self):
        bid = make_bid(bidder_id="")
        errors = bid.validate()
        assert any("bidder_id" in e for e in errors)

    def test_validate_negative_bid_value(self):
        bid = make_bid(bid_value=-1.0)
        errors = bid.validate()
        assert any("bid_value" in e for e in errors)

    def test_validate_semantic_score_out_of_range(self):
        bid = make_bid(semantic_score=1.5)
        errors = bid.validate()
        assert any("semantic_score" in e for e in errors)

    def test_validate_uncertainty_out_of_range(self):
        bid = make_bid(uncertainty=-0.1)
        errors = bid.validate()
        assert any("uncertainty" in e for e in errors)

    def test_validate_trust_ceiling_out_of_range(self):
        bid = make_bid(trust_ceiling=2.0)
        errors = bid.validate()
        assert any("trust_ceiling" in e for e in errors)

    def test_validate_nan_bid_value(self):
        bid = make_bid(bid_value=float("nan"))
        errors = bid.validate()
        assert errors  # nan is not finite

    def test_validate_multiple_errors(self):
        bid = make_bid(move_id="", bid_value=-5.0)
        errors = bid.validate()
        assert len(errors) >= 2

    def test_delta_from_dominant(self):
        a = make_bid(bid_value=60.0, semantic_score=0.9, uncertainty=0.1)
        b = make_bid(bid_value=50.0, semantic_score=0.8, uncertainty=0.2)
        delta = a.delta_from(b)
        assert delta.dominant is True
        assert delta.value_delta == pytest.approx(10.0)
        assert delta.score_delta == pytest.approx(0.1)
        assert delta.uncertainty_delta == pytest.approx(-0.1)

    def test_delta_from_not_dominant(self):
        a = make_bid(bid_value=40.0, semantic_score=0.7, uncertainty=0.3)
        b = make_bid(bid_value=50.0, semantic_score=0.8, uncertainty=0.2)
        delta = a.delta_from(b)
        assert delta.dominant is False

    def test_delta_from_equal(self):
        a = make_bid(bid_value=50.0, semantic_score=0.8, uncertainty=0.2)
        b = make_bid(bid_value=50.0, semantic_score=0.8, uncertainty=0.2)
        delta = a.delta_from(b)
        assert delta.dominant is False  # weakly equal but not strictly better

    def test_delta_from_returns_bid_delta(self):
        a = make_bid()
        b = make_bid(bidder_id="other")
        delta = a.delta_from(b)
        assert isinstance(delta, BidDelta)
        assert delta.bid_id_a == a.bid_id
        assert delta.bid_id_b == b.bid_id


# ---------------------------------------------------------------------------
# FleetRound
# ---------------------------------------------------------------------------

class TestFleetRound:
    def test_construction_defaults(self):
        r = FleetRound()
        assert isinstance(r.round_id, str)
        assert r.phase == RoundPhase.OPEN
        assert r.bids == []
        assert r.winner is None
        assert r.budget_remaining == 100.0

    def test_explicit_round_id(self):
        r = FleetRound(round_id="rnd-001")
        assert r.round_id == "rnd-001"

    def test_add_bid_open_phase(self):
        r = FleetRound()
        b = make_bid()
        r.add_bid(b)
        assert len(r.bids) == 1
        assert r.bids[0] is b

    def test_add_multiple_bids(self):
        r = FleetRound()
        for i in range(5):
            r.add_bid(make_bid(bidder_id=f"b{i}"))
        assert len(r.bids) == 5

    def test_add_bid_duplicate_id_raises(self):
        r = FleetRound()
        b = make_bid()
        r.add_bid(b)
        with pytest.raises(ValueError, match="already exists"):
            r.add_bid(b)

    def test_add_bid_closed_phase_raises(self):
        r = FleetRound()
        b = make_bid()
        r.add_bid(b)
        r.determine_winner(lambda bids: bids[0].bid_id)
        with pytest.raises(ValueError, match="phase"):
            r.add_bid(make_bid(bidder_id="new"))

    def test_add_bid_evaluating_phase_raises(self):
        r = FleetRound()
        r.phase = RoundPhase.EVALUATING
        with pytest.raises(ValueError):
            r.add_bid(make_bid())

    def test_add_bid_archived_phase_raises(self):
        r = FleetRound()
        r.phase = RoundPhase.ARCHIVED
        with pytest.raises(ValueError):
            r.add_bid(make_bid())

    def test_determine_winner_selects_correct_bid(self):
        r = FleetRound()
        b1 = make_bid(bidder_id="b1")
        b2 = make_bid(bidder_id="b2")
        r.add_bid(b1)
        r.add_bid(b2)
        winning_id = r.determine_winner(lambda bids: bids[1].bid_id)
        assert winning_id == b2.bid_id
        assert r.winner == b2.bid_id

    def test_determine_winner_sets_phase_closed(self):
        r = FleetRound()
        r.add_bid(make_bid())
        r.determine_winner(lambda bids: bids[0].bid_id)
        assert r.phase == RoundPhase.CLOSED

    def test_determine_winner_updates_bid_statuses(self):
        r = FleetRound()
        b1 = make_bid(bidder_id="b1")
        b2 = make_bid(bidder_id="b2")
        b3 = make_bid(bidder_id="b3")
        r.add_bid(b1)
        r.add_bid(b2)
        r.add_bid(b3)
        r.determine_winner(lambda bids: b2.bid_id)
        assert b2.status == BidStatus.ACCEPTED
        assert b1.status == BidStatus.REJECTED
        assert b3.status == BidStatus.REJECTED

    def test_determine_winner_no_winner_returns_none(self):
        r = FleetRound()
        b = make_bid()
        r.add_bid(b)
        result = r.determine_winner(lambda bids: None)
        assert result is None
        assert r.winner is None

    def test_determine_winner_closed_round_raises(self):
        r = FleetRound()
        r.add_bid(make_bid())
        r.determine_winner(lambda bids: bids[0].bid_id)
        with pytest.raises(ValueError, match="already"):
            r.determine_winner(lambda bids: bids[0].bid_id)

    def test_determine_winner_archived_round_raises(self):
        r = FleetRound()
        r.phase = RoundPhase.ARCHIVED
        with pytest.raises(ValueError):
            r.determine_winner(lambda bids: None)

    def test_summarize_empty(self):
        r = FleetRound()
        s = r.summarize()
        assert s["num_bids"] == 0
        assert s["winner"] is None
        assert s["top_bid"] is None
        assert s["phase"] == "open"

    def test_summarize_with_bids(self):
        r = FleetRound()
        b = make_bid(semantic_score=0.9)
        r.add_bid(b)
        s = r.summarize()
        assert s["num_bids"] == 1
        assert s["top_bid"] is not None
        assert s["top_bid"]["semantic_score"] == 0.9

    def test_summarize_after_winner(self):
        r = FleetRound()
        b = make_bid()
        r.add_bid(b)
        r.determine_winner(lambda bids: bids[0].bid_id)
        s = r.summarize()
        assert s["winner"] == b.bid_id
        assert s["phase"] == "closed"

    def test_summarize_top_bid_is_highest_semantic(self):
        r = FleetRound()
        b_low = make_bid(bidder_id="b1", semantic_score=0.5)
        b_high = make_bid(bidder_id="b2", semantic_score=0.95)
        r.add_bid(b_low)
        r.add_bid(b_high)
        s = r.summarize()
        assert s["top_bid"]["semantic_score"] == 0.95


# ---------------------------------------------------------------------------
# ChallengeRecord
# ---------------------------------------------------------------------------

class TestChallengeRecord:
    def test_construction_defaults(self):
        rec = ChallengeRecord()
        assert isinstance(rec.challenge_id, str)
        assert rec.challenger_id == ""
        assert rec.challenged_id == ""
        assert rec.bid_id == ""
        assert rec.outcome == "pending"
        assert rec.resolved_at is None
        assert rec.is_pending() is True

    def test_construction_with_fields(self):
        rec = ChallengeRecord(
            challenger_id="alice",
            challenged_id="bob",
            bid_id="bid-1",
            challenge_reason="Inflated score",
        )
        assert rec.challenger_id == "alice"
        assert rec.challenged_id == "bob"
        assert rec.bid_id == "bid-1"
        assert rec.challenge_reason == "Inflated score"

    def test_is_pending_true(self):
        rec = ChallengeRecord()
        assert rec.is_pending() is True

    def test_is_pending_false_after_resolve(self):
        rec = ChallengeRecord()
        rec.resolve("upheld", {"score": 0.9})
        assert rec.is_pending() is False

    def test_resolve_sets_outcome(self):
        rec = ChallengeRecord()
        rec.resolve("upheld", {})
        assert rec.outcome == "upheld"

    def test_resolve_sets_resolved_at(self):
        rec = ChallengeRecord()
        before = time.time()
        rec.resolve("overturned", {})
        after = time.time()
        assert before <= rec.resolved_at <= after

    def test_resolve_merges_evidence(self):
        rec = ChallengeRecord(evidence={"prior": "data"})
        rec.resolve("split", {"new_key": "value"})
        assert rec.evidence["prior"] == "data"
        assert rec.evidence["new_key"] == "value"

    def test_resolve_twice_raises(self):
        rec = ChallengeRecord()
        rec.resolve("upheld", {})
        with pytest.raises(ValueError, match="already been resolved"):
            rec.resolve("overturned", {})

    def test_resolve_empty_outcome_raises(self):
        rec = ChallengeRecord()
        with pytest.raises(ValueError, match="outcome"):
            rec.resolve("", {})

    def test_age_seconds(self):
        rec = ChallengeRecord()
        time.sleep(0.02)
        age = rec.age_seconds()
        assert age >= 0.01

    def test_to_dict_keys(self):
        rec = ChallengeRecord(
            challenger_id="a", challenged_id="b", bid_id="bid-1"
        )
        d = rec.to_dict()
        keys = {"challenge_id", "challenger_id", "challenged_id",
                "bid_id", "challenge_reason", "outcome", "evidence",
                "resolved_at", "created_at", "is_pending", "age_seconds"}
        assert keys.issubset(d.keys())

    def test_to_dict_is_pending_true(self):
        rec = ChallengeRecord()
        d = rec.to_dict()
        assert d["is_pending"] is True

    def test_to_dict_is_pending_false_after_resolve(self):
        rec = ChallengeRecord()
        rec.resolve("withdrawn", {})
        d = rec.to_dict()
        assert d["is_pending"] is False

    def test_from_dict_roundtrip(self):
        rec = ChallengeRecord(
            challenger_id="alice",
            challenged_id="bob",
            bid_id="b1",
            challenge_reason="Duplicate bid",
        )
        d = rec.to_dict()
        rec2 = ChallengeRecord.from_dict(d)
        assert rec2.challenge_id == rec.challenge_id
        assert rec2.challenger_id == "alice"
        assert rec2.challenged_id == "bob"
        assert rec2.challenge_reason == "Duplicate bid"

    def test_from_dict_with_resolved(self):
        rec = ChallengeRecord()
        rec.resolve("upheld", {"key": "val"})
        d = rec.to_dict()
        rec2 = ChallengeRecord.from_dict(d)
        assert rec2.resolved_at is not None
        assert rec2.outcome == "upheld"

    def test_from_dict_missing_fields_uses_defaults(self):
        rec2 = ChallengeRecord.from_dict({})
        assert rec2.outcome == "pending"
        assert rec2.resolved_at is None


# ---------------------------------------------------------------------------
# CalibrationTrace
# ---------------------------------------------------------------------------

class TestCalibrationTrace:
    def test_construction(self):
        ct = CalibrationTrace(member_id="m1")
        assert ct.member_id == "m1"
        assert ct.accuracy_history == []
        assert ct.latency_history == []
        assert ct.trust_history == []
        assert ct.timestamps == []
        assert ct.status == CalibrationStatus.FRESH

    def test_add_sample_appends(self):
        ct = CalibrationTrace(member_id="m1")
        ct.add_sample(0.9, 0.5, 0.8)
        assert len(ct.accuracy_history) == 1
        assert len(ct.latency_history) == 1
        assert len(ct.trust_history) == 1
        assert len(ct.timestamps) == 1

    def test_add_sample_clamps_accuracy(self):
        ct = CalibrationTrace(member_id="m1")
        ct.add_sample(1.5, 1.0, 0.5)
        assert ct.accuracy_history[0] == 1.0

    def test_add_sample_clamps_accuracy_low(self):
        ct = CalibrationTrace(member_id="m1")
        ct.add_sample(-0.5, 1.0, 0.5)
        assert ct.accuracy_history[0] == 0.0

    def test_add_sample_clamps_latency(self):
        ct = CalibrationTrace(member_id="m1")
        ct.add_sample(0.9, 1000.0, 0.8)
        assert ct.latency_history[0] == MAX_LATENCY

    def test_add_sample_clamps_trust(self):
        ct = CalibrationTrace(member_id="m1")
        ct.add_sample(0.9, 0.5, 2.0)
        assert ct.trust_history[0] == 1.0

    def test_status_degraded_with_few_samples(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH - 1):
            ct.add_sample(0.9, 0.5, 0.8)
        assert ct.status == CalibrationStatus.DEGRADED

    def test_status_fresh_with_enough_samples(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(0.9, 0.5, 0.8)
        assert ct.status == CalibrationStatus.FRESH

    def test_moving_average_accuracy(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(0.8, 1.0, 0.7)
        ma = ct.moving_average(window=3, series="accuracy")
        assert len(ma) == MIN_SAMPLES_FOR_FRESH
        assert all(isinstance(v, float) for v in ma)

    def test_moving_average_latency(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(0.8, 2.0, 0.7)
        ma = ct.moving_average(window=2, series="latency")
        assert len(ma) == MIN_SAMPLES_FOR_FRESH

    def test_moving_average_trust(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(0.8, 1.0, 0.9)
        ma = ct.moving_average(window=5, series="trust")
        assert len(ma) == MIN_SAMPLES_FOR_FRESH

    def test_moving_average_invalid_series_raises(self):
        ct = CalibrationTrace(member_id="m1")
        ct.add_sample(0.5, 1.0, 0.5)
        with pytest.raises(ValueError, match="Unknown series"):
            ct.moving_average(window=2, series="nonexistent")

    def test_calibration_score_no_samples(self):
        ct = CalibrationTrace(member_id="m1")
        assert ct.calibration_score() == 0.0

    def test_calibration_score_in_range(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(CALIBRATION_TRAILING_WINDOW):
            ct.add_sample(0.9, 5.0, 0.85)
        score = ct.calibration_score()
        assert 0.0 <= score <= 1.0

    def test_calibration_score_high_accuracy_low_latency(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(CALIBRATION_TRAILING_WINDOW):
            ct.add_sample(1.0, 0.0, 1.0)  # perfect accuracy, zero latency, full trust
        score = ct.calibration_score()
        assert score > 0.8

    def test_calibration_score_low_accuracy_high_latency(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(CALIBRATION_TRAILING_WINDOW):
            ct.add_sample(0.0, MAX_LATENCY, 0.0)  # worst case
        score = ct.calibration_score()
        assert score == pytest.approx(0.0, abs=0.01)

    def test_export_csv_empty(self):
        ct = CalibrationTrace(member_id="m1")
        csv_str = ct.export_csv()
        lines = csv_str.strip().splitlines()
        assert len(lines) == 1  # just header
        assert "timestamp" in lines[0]
        assert "accuracy" in lines[0]
        assert "latency" in lines[0]
        assert "trust" in lines[0]

    def test_export_csv_with_samples(self):
        ct = CalibrationTrace(member_id="m1")
        ct.add_sample(0.8, 1.5, 0.7)
        ct.add_sample(0.9, 0.5, 0.8)
        csv_str = ct.export_csv()
        lines = csv_str.strip().splitlines()
        assert len(lines) == 3  # header + 2 data rows

    def test_to_dict_roundtrip(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(0.85, 3.0, 0.75)
        d = ct.to_dict()
        assert d["member_id"] == "m1"
        assert len(d["accuracy_history"]) == MIN_SAMPLES_FOR_FRESH
        assert "summary" in d

    def test_from_dict_roundtrip(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(0.85, 3.0, 0.75)
        d = ct.to_dict()
        ct2 = CalibrationTrace.from_dict(d)
        assert ct2.member_id == "m1"
        assert len(ct2.accuracy_history) == len(ct.accuracy_history)

    def test_from_dict_invalid_status_defaults_to_invalid(self):
        d = {"member_id": "x", "status": "totally_wrong"}
        ct = CalibrationTrace.from_dict(d)
        assert ct.status == CalibrationStatus.INVALID

    def test_summary_keys(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(0.9, 1.0, 0.8)
        s = ct.summary()
        expected_keys = {"member_id", "n_samples", "status", "calibration_score",
                         "mean_accuracy", "mean_latency", "mean_trust",
                         "std_accuracy", "std_latency", "std_trust", "latest_timestamp"}
        assert expected_keys.issubset(s.keys())

    def test_summary_n_samples(self):
        ct = CalibrationTrace(member_id="m1")
        for _ in range(7):
            ct.add_sample(0.8, 1.0, 0.7)
        s = ct.summary()
        assert s["n_samples"] == 7


# ---------------------------------------------------------------------------
# Integration tests with fleet and trust modules
# ---------------------------------------------------------------------------

class TestModelsIntegration:
    def test_fleet_member_trust_ceiling_in_competitive_bid(self):
        from jugeo.orchestration.fleet import FleetMember
        member = FleetMember("worker-1", trust_ceiling=0.75)
        bid = CompetitiveBid(
            move_id="m1",
            bidder_id=member.member_id,
            bid_value=40.0,
            semantic_score=0.7,
            uncertainty=0.3,
            capabilities=["solve"],
            trust_ceiling=member.trust_ceiling,
        )
        assert bid.trust_ceiling == 0.75
        assert bid.validate() == []

    def test_fleet_member_capabilities_match_bid_capabilities(self):
        from jugeo.orchestration.fleet import FleetMember
        member = FleetMember("w2", capabilities=frozenset({"prove", "model"}))
        bid = CompetitiveBid(
            move_id="m2",
            bidder_id=member.member_id,
            bid_value=30.0,
            semantic_score=0.65,
            uncertainty=0.35,
            capabilities=list(member.capabilities),
            trust_ceiling=0.8,
        )
        assert set(bid.capabilities) == member.capabilities

    def test_trust_level_used_as_trust_ceiling(self):
        from jugeo.evidence.trust import TrustLevel
        level = TrustLevel.VERIFIED
        bid = CompetitiveBid(
            move_id="m3",
            bidder_id="b3",
            bid_value=20.0,
            semantic_score=0.85,
            uncertainty=0.15,
            capabilities=["verify"],
            trust_ceiling=level.value if isinstance(level.value, float) else 0.9,
        )
        assert bid.validate() == []

    def test_fleet_bid_created_from_member_check_semantic_score(self):
        from jugeo.orchestration.fleet import FleetMember
        member = FleetMember("w3", trust_ceiling=0.8)
        fleet_bid = member.bid_for("coord-1", "prove lemma")
        # Wrap into a CompetitiveBid for fleet_competition
        comp_bid = CompetitiveBid(
            move_id="coord-1",
            bidder_id=fleet_bid.member_id,
            bid_value=fleet_bid.confidence * 100.0,
            semantic_score=fleet_bid.confidence,
            uncertainty=fleet_bid.uncertainty_profile.get("epistemic", 0.5),
            capabilities=[],
            trust_ceiling=member.trust_ceiling,
        )
        assert 0.0 <= comp_bid.semantic_score <= 1.0
        assert comp_bid.validate() == []

    def test_calibration_trace_with_fleet_member_trust(self):
        from jugeo.orchestration.fleet import FleetMember
        member = FleetMember("w4", trust_ceiling=0.6)
        ct = CalibrationTrace(member_id=member.member_id)
        for _ in range(MIN_SAMPLES_FOR_FRESH):
            ct.add_sample(
                accuracy=0.8,
                latency=2.0,
                trust=member.trust_ceiling,
            )
        assert ct.calibration_score() > 0.0
        assert ct.status == CalibrationStatus.FRESH

    def test_fleet_round_with_member_bids(self):
        from jugeo.orchestration.fleet import FleetMember
        r = FleetRound()
        members = [FleetMember(f"w{i}", trust_ceiling=0.5 + i * 0.1) for i in range(3)]
        for i, member in enumerate(members):
            bid = CompetitiveBid(
                move_id="target",
                bidder_id=member.member_id,
                bid_value=float(10 * (i + 1)),
                semantic_score=0.5 + i * 0.1,
                uncertainty=0.5 - i * 0.1,
                capabilities=[],
                trust_ceiling=member.trust_ceiling,
            )
            r.add_bid(bid)
        assert len(r.bids) == 3
        winner_id = r.determine_winner(lambda bids: max(bids, key=lambda b: b.semantic_score).bid_id)
        assert winner_id is not None

    def test_challenge_record_with_fleet_members(self):
        from jugeo.orchestration.fleet import FleetMember
        alice = FleetMember("alice")
        bob = FleetMember("bob")
        bid = CompetitiveBid(
            move_id="target",
            bidder_id=bob.member_id,
            bid_value=50.0,
            semantic_score=0.9,
            uncertainty=0.05,
            capabilities=[],
            trust_ceiling=0.8,
        )
        rec = ChallengeRecord(
            challenger_id=alice.member_id,
            challenged_id=bob.member_id,
            bid_id=bid.bid_id,
            challenge_reason="Score too high",
        )
        assert rec.is_pending()
        rec.resolve("upheld", {"verified_by": "system"})
        assert not rec.is_pending()
        assert rec.outcome == "upheld"

    def test_bid_delta_with_two_fleet_bids(self):
        from jugeo.orchestration.fleet import FleetMember
        m1 = FleetMember("w5", trust_ceiling=0.9)
        m2 = FleetMember("w6", trust_ceiling=0.7)
        b1 = CompetitiveBid(
            move_id="x", bidder_id=m1.member_id,
            bid_value=80.0, semantic_score=0.95, uncertainty=0.05,
            capabilities=[], trust_ceiling=m1.trust_ceiling,
        )
        b2 = CompetitiveBid(
            move_id="x", bidder_id=m2.member_id,
            bid_value=60.0, semantic_score=0.80, uncertainty=0.20,
            capabilities=[], trust_ceiling=m2.trust_ceiling,
        )
        delta = b1.delta_from(b2)
        assert delta.dominant is True
        assert delta.is_improvement() is True


# ---------------------------------------------------------------------------
# Parametrized edge-case tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bid_value,semantic,uncertainty,trust,valid", [
    (0.0, 0.0, 0.0, 0.0, True),
    (0.0, 1.0, 1.0, 1.0, True),
    (100.0, 0.5, 0.5, 0.5, True),
    (-0.001, 0.5, 0.5, 0.5, False),  # negative bid_value
    (10.0, 1.001, 0.5, 0.5, False),  # semantic_score > 1
    (10.0, 0.5, -0.1, 0.5, False),   # uncertainty < 0
    (10.0, 0.5, 0.5, 1.01, False),   # trust_ceiling > 1
])
def test_competitive_bid_validate_parametrize(bid_value, semantic, uncertainty, trust, valid):
    bid = make_bid(
        bid_value=bid_value,
        semantic_score=semantic,
        uncertainty=uncertainty,
        trust_ceiling=trust,
    )
    errors = bid.validate()
    if valid:
        assert errors == []
    else:
        assert len(errors) >= 1


@pytest.mark.parametrize("a_score,a_unc,b_score,b_unc,expect_dominant", [
    (0.9, 0.1, 0.8, 0.2, True),   # a strictly better on both
    (0.9, 0.2, 0.8, 0.2, True),   # a better score only
    (0.8, 0.1, 0.8, 0.2, True),   # a better uncertainty only
    (0.8, 0.2, 0.9, 0.1, False),  # b dominates a
    (0.8, 0.2, 0.8, 0.2, False),  # equal, not dominant
])
def test_bid_delta_dominance_parametrize(a_score, a_unc, b_score, b_unc, expect_dominant):
    a = make_bid(semantic_score=a_score, uncertainty=a_unc, bidder_id="a")
    b = make_bid(semantic_score=b_score, uncertainty=b_unc, bidder_id="b")
    delta = a.delta_from(b)
    assert delta.dominant is expect_dominant


@pytest.mark.parametrize("window,expected_len", [
    (1, 5),
    (3, 5),
    (10, 5),
    (100, 5),
])
def test_moving_average_length(window, expected_len):
    ct = CalibrationTrace(member_id="m")
    for _ in range(expected_len):
        ct.add_sample(0.7, 1.0, 0.6)
    ma = ct.moving_average(window=window, series="accuracy")
    assert len(ma) == expected_len


@pytest.mark.parametrize("phases", [
    [RoundPhase.OPEN],
    [RoundPhase.EVALUATING],
    [RoundPhase.CLOSED],
    [RoundPhase.ARCHIVED],
])
def test_fleet_round_non_open_blocks_add_bid(phases):
    r = FleetRound()
    r.phase = phases[0]
    if phases[0] != RoundPhase.OPEN:
        with pytest.raises(ValueError):
            r.add_bid(make_bid())
    else:
        r.add_bid(make_bid())  # should succeed
        assert len(r.bids) == 1
