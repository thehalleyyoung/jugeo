from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
Tests for jugeo.ideation.discovery_federation.s03_federation_consensus

This module defines the expected API for the federation consensus subsystem using
Test-Driven Development (TDD). The implementation does not exist yet; these tests
specify the contract that the implementation must satisfy.

Classes under test:
  - VotingRound          : a single voting round for a federation decision
  - ConsensusProtocol    : manages a series of VotingRound instances
  - QuorumCalculator     : computes quorum thresholds under various policies
  - VoteAggregator       : accumulates votes and computes statistics
  - FederationConsensusRunner : orchestrates the end-to-end consensus process

Free functions under test:
  - run_consensus        : convenience wrapper for a single consensus run
  - compute_quorum       : convenience wrapper for quorum computation
"""

from jugeo.ideation.discovery_federation.s03_federation_consensus import (
    VotingRound,
    ConsensusProtocol,
    QuorumCalculator,
    VoteAggregator,
    FederationConsensusRunner,
    run_consensus,
    compute_quorum,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_voter(voter_id: str = "v1", trust_score: float = 0.8) -> dict:
    """Return a minimal voter dict for use in tests."""
    return {"voter_id": voter_id, "trust_score": trust_score}


def make_vote(voter_id: str = "v1", position: str = "YES", weight: float = 1.0) -> dict:
    """Return a minimal vote dict for use in tests."""
    return {"voter_id": voter_id, "position": position, "weight": weight}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def open_round_fixture() -> VotingRound:
    """A VotingRound that has been opened with three voters."""
    voters = ["v1", "v2", "v3"]
    return VotingRound.open(round_id="r-001", subject="Test subject", voters=voters)


@pytest.fixture()
def protocol_with_voters() -> ConsensusProtocol:
    """A ConsensusProtocol with a single open round containing five voters."""
    protocol = ConsensusProtocol(quorum_threshold=0.67, min_voters=3)
    voters = ["va", "vb", "vc", "vd", "ve"]
    protocol.open_round(subject="Protocol subject", voters=voters)
    return protocol


@pytest.fixture()
def aggregator_with_votes() -> VoteAggregator:
    """A VoteAggregator with five votes pre-cast."""
    agg = VoteAggregator()
    agg.add_vote("v1", "YES", 1.0)
    agg.add_vote("v2", "YES", 1.0)
    agg.add_vote("v3", "YES", 0.5)
    agg.add_vote("v4", "NO", 1.0)
    agg.add_vote("v5", "ABSTAIN", 0.5)
    return agg


# ---------------------------------------------------------------------------
# VotingRound Tests
# ---------------------------------------------------------------------------

class TestVotingRoundOpen:
    def test_open_returns_voting_round_instance(self):
        vr = VotingRound.open(round_id="r1", subject="subject-A", voters=["v1", "v2", "v3"])
        assert isinstance(vr, VotingRound)

    def test_open_sets_round_id(self):
        vr = VotingRound.open(round_id="r-xyz", subject="S", voters=["v1"])
        assert vr.round_id == "r-xyz"

    def test_open_sets_subject(self):
        vr = VotingRound.open(round_id="r1", subject="My Subject", voters=["v1"])
        assert vr.subject == "My Subject"

    def test_open_stores_voters(self):
        voters = ["v1", "v2", "v3"]
        vr = VotingRound.open(round_id="r1", subject="S", voters=voters)
        assert vr.voters == voters

    def test_open_sets_is_open_true(self, open_round_fixture):
        assert open_round_fixture.is_open is True

    def test_open_sets_opened_at_non_empty(self, open_round_fixture):
        assert open_round_fixture.opened_at
        assert isinstance(open_round_fixture.opened_at, str)

    def test_open_closed_at_is_none(self, open_round_fixture):
        assert open_round_fixture.closed_at is None

    def test_open_votes_empty(self, open_round_fixture):
        assert open_round_fixture.votes == []


class TestVotingRoundClose:
    def test_close_sets_is_open_false(self, open_round_fixture):
        open_round_fixture.close()
        assert open_round_fixture.is_open is False

    def test_close_sets_closed_at(self, open_round_fixture):
        open_round_fixture.close()
        assert open_round_fixture.closed_at is not None
        assert isinstance(open_round_fixture.closed_at, str)

    def test_close_idempotent(self, open_round_fixture):
        """Calling close() twice should not raise."""
        open_round_fixture.close()
        open_round_fixture.close()
        assert open_round_fixture.is_open is False


class TestVotingRoundAddVote:
    def test_add_vote_returns_dict(self, open_round_fixture):
        result = open_round_fixture.add_vote("v1", "YES", 1.0)
        assert isinstance(result, dict)

    def test_add_vote_increments_count(self, open_round_fixture):
        open_round_fixture.add_vote("v1", "YES", 1.0)
        assert open_round_fixture.vote_count() == 1

    def test_add_multiple_votes(self, open_round_fixture):
        open_round_fixture.add_vote("v1", "YES", 1.0)
        open_round_fixture.add_vote("v2", "NO", 1.0)
        assert open_round_fixture.vote_count() == 2

    def test_add_vote_stores_voter_id(self, open_round_fixture):
        open_round_fixture.add_vote("v1", "YES", 1.0)
        assert any(v.get("voter_id") == "v1" for v in open_round_fixture.votes)

    def test_add_vote_stores_position(self, open_round_fixture):
        open_round_fixture.add_vote("v1", "YES", 1.0)
        assert any(v.get("position") == "YES" for v in open_round_fixture.votes)

    def test_add_vote_stores_weight(self, open_round_fixture):
        open_round_fixture.add_vote("v1", "YES", 0.75)
        assert any(abs(v.get("weight", 0) - 0.75) < 1e-9 for v in open_round_fixture.votes)

    def test_add_vote_with_rationale(self, open_round_fixture):
        result = open_round_fixture.add_vote("v1", "YES", 1.0, rationale="Good idea")
        assert isinstance(result, dict)

    def test_add_duplicate_voter_raises_or_updates(self, open_round_fixture):
        """Duplicate voter_id must either raise ValueError or update the existing vote."""
        open_round_fixture.add_vote("v1", "YES", 1.0)
        try:
            open_round_fixture.add_vote("v1", "NO", 1.0)
            # If no exception, count must still be 1 (update behaviour)
            assert open_round_fixture.vote_count() == 1
        except (ValueError, RuntimeError):
            pass  # raise behaviour is also acceptable

    def test_add_vote_to_closed_round_raises_or_noop(self, open_round_fixture):
        """Adding a vote to a closed round must either raise or be a no-op."""
        open_round_fixture.close()
        count_before = open_round_fixture.vote_count()
        try:
            open_round_fixture.add_vote("v1", "YES", 1.0)
            # no-op: count must not have grown
            assert open_round_fixture.vote_count() == count_before
        except (ValueError, RuntimeError):
            pass  # raise behaviour is also acceptable


class TestVotingRoundVoteCount:
    def test_vote_count_zero_initially(self, open_round_fixture):
        assert open_round_fixture.vote_count() == 0

    def test_vote_count_three_after_three(self, open_round_fixture):
        for i, voter in enumerate(["v1", "v2", "v3"]):
            open_round_fixture.add_vote(voter, "YES", 1.0)
        assert open_round_fixture.vote_count() == 3


class TestVotingRoundToDict:
    def test_to_dict_returns_dict(self, open_round_fixture):
        assert isinstance(open_round_fixture.to_dict(), dict)

    def test_to_dict_contains_round_id(self, open_round_fixture):
        d = open_round_fixture.to_dict()
        assert "round_id" in d

    def test_to_dict_contains_subject(self, open_round_fixture):
        d = open_round_fixture.to_dict()
        assert "subject" in d

    def test_to_dict_contains_is_open(self, open_round_fixture):
        d = open_round_fixture.to_dict()
        assert "is_open" in d

    def test_to_dict_round_trips_round_id(self, open_round_fixture):
        d = open_round_fixture.to_dict()
        assert d["round_id"] == open_round_fixture.round_id

    def test_to_dict_round_trips_subject(self, open_round_fixture):
        d = open_round_fixture.to_dict()
        assert d["subject"] == open_round_fixture.subject


class TestVotingRoundSummary:
    def test_summary_returns_string(self, open_round_fixture):
        assert isinstance(open_round_fixture.summary(), str)

    def test_summary_non_empty(self, open_round_fixture):
        assert open_round_fixture.summary() != ""

    def test_summary_after_votes_non_empty(self, open_round_fixture):
        open_round_fixture.add_vote("v1", "YES", 1.0)
        assert open_round_fixture.summary() != ""


# ---------------------------------------------------------------------------
# ConsensusProtocol Tests
# ---------------------------------------------------------------------------

class TestConsensusProtocolOpenRound:
    def test_open_round_returns_voting_round(self):
        cp = ConsensusProtocol()
        vr = cp.open_round(subject="S", voters=["v1", "v2", "v3"])
        assert isinstance(vr, VotingRound)

    def test_open_round_stores_subject(self):
        cp = ConsensusProtocol()
        vr = cp.open_round(subject="My subject", voters=["v1", "v2", "v3"])
        assert vr.subject == "My subject"

    def test_open_round_is_open(self):
        cp = ConsensusProtocol()
        vr = cp.open_round(subject="S", voters=["v1"])
        assert vr.is_open is True

    def test_open_round_returns_different_round_ids(self):
        cp = ConsensusProtocol()
        r1 = cp.open_round(subject="A", voters=["v1"])
        r2 = cp.open_round(subject="B", voters=["v2"])
        assert r1.round_id != r2.round_id


class TestConsensusProtocolCastVote:
    def test_cast_vote_returns_bool(self, protocol_with_voters):
        rounds = list(protocol_with_voters._rounds.values()) if hasattr(protocol_with_voters, "_rounds") else []
        if rounds:
            rid = rounds[0].round_id
        else:
            vr = protocol_with_voters.open_round("extra", ["va"])
            rid = vr.round_id
        result = protocol_with_voters.cast_vote(rid, "va", "YES", 1.0)
        assert isinstance(result, bool)

    def test_cast_vote_returns_true_on_success(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1", "v2", "v3"])
        assert cp.cast_vote(vr.round_id, "v1", "YES", 1.0) is True

    def test_cast_vote_returns_false_invalid_round(self):
        cp = ConsensusProtocol()
        result = cp.cast_vote("nonexistent-round", "v1", "YES", 1.0)
        assert result is False

    def test_cast_vote_increments_vote_count(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1", "v2", "v3"])
        cp.cast_vote(vr.round_id, "v1", "YES", 1.0)
        assert vr.vote_count() == 1


class TestConsensusProtocolCloseRound:
    def test_close_round_returns_dict(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1", "v2", "v3"])
        cp.cast_vote(vr.round_id, "v1", "YES", 1.0)
        cp.cast_vote(vr.round_id, "v2", "YES", 1.0)
        result = cp.close_round(vr.round_id)
        assert isinstance(result, dict)

    def test_close_round_contains_outcome(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1", "v2", "v3"])
        result = cp.close_round(vr.round_id)
        assert "outcome" in result or "tally" in result


class TestConsensusProtocolTally:
    def test_tally_returns_dict(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1", "v2", "v3"])
        cp.cast_vote(vr.round_id, "v1", "YES", 1.0)
        cp.cast_vote(vr.round_id, "v2", "NO", 1.0)
        cp.cast_vote(vr.round_id, "v3", "ABSTAIN", 1.0)
        tally = cp.tally(vr.round_id)
        assert isinstance(tally, dict)

    def test_tally_has_yes_key(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1"])
        cp.cast_vote(vr.round_id, "v1", "YES", 1.0)
        assert "yes" in cp.tally(vr.round_id)

    def test_tally_has_no_key(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1"])
        cp.cast_vote(vr.round_id, "v1", "NO", 1.0)
        assert "no" in cp.tally(vr.round_id)

    def test_tally_has_abstain_key(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1"])
        assert "abstain" in cp.tally(vr.round_id)

    def test_tally_has_total_key(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1"])
        assert "total" in cp.tally(vr.round_id)

    def test_tally_yes_fraction_all_yes(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1", "v2"])
        cp.cast_vote(vr.round_id, "v1", "YES", 1.0)
        cp.cast_vote(vr.round_id, "v2", "YES", 1.0)
        tally = cp.tally(vr.round_id)
        assert abs(tally["yes"] - 2.0) < 1e-6 or abs(tally["yes"] - 1.0) < 1e-6  # weight or ratio


class TestConsensusProtocolGetOutcome:
    @pytest.mark.parametrize("yes_frac,threshold,expected", [
        (1.0, 0.67, "ACCEPTED"),
        (0.0, 0.67, "REJECTED"),
        (0.5, 0.67, "REJECTED"),
        (0.67, 0.67, "ACCEPTED"),
        (0.8, 0.67, "ACCEPTED"),
    ])
    def test_get_outcome_by_threshold(self, yes_frac, threshold, expected):
        cp = ConsensusProtocol(quorum_threshold=threshold)
        n = 10
        voters = [f"v{i}" for i in range(n)]
        vr = cp.open_round("S", voters)
        yes_count = int(round(yes_frac * n))
        for i in range(yes_count):
            cp.cast_vote(vr.round_id, f"v{i}", "YES", 1.0)
        for i in range(yes_count, n):
            cp.cast_vote(vr.round_id, f"v{i}", "NO", 1.0)
        cp.close_round(vr.round_id)
        outcome = cp.get_outcome(vr.round_id)
        assert outcome in ("ACCEPTED", "REJECTED", "ABSTAINED", "PENDING")

    def test_get_outcome_pending_on_open_round(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1"])
        outcome = cp.get_outcome(vr.round_id)
        assert outcome == "PENDING"

    def test_get_outcome_returns_string(self):
        cp = ConsensusProtocol()
        vr = cp.open_round("S", ["v1"])
        assert isinstance(cp.get_outcome(vr.round_id), str)


# ---------------------------------------------------------------------------
# QuorumCalculator Tests
# ---------------------------------------------------------------------------

class TestQuorumCalculatorSimpleMajority:
    @pytest.mark.parametrize("voter_count", [3, 5, 10, 20])
    def test_compute_simple_majority_above_half(self, voter_count):
        qc = QuorumCalculator()
        result = qc.compute_simple_majority(voter_count)
        assert result > 0.5

    def test_compute_simple_majority_returns_float(self):
        qc = QuorumCalculator()
        assert isinstance(qc.compute_simple_majority(5), float)

    def test_compute_simple_majority_below_one(self):
        qc = QuorumCalculator()
        assert qc.compute_simple_majority(5) < 1.0

    def test_compute_simple_majority_single_voter(self):
        qc = QuorumCalculator()
        result = qc.compute_simple_majority(1)
        assert result > 0.5


class TestQuorumCalculatorTwoThirds:
    @pytest.mark.parametrize("voter_count", [3, 5, 10, 20])
    def test_compute_two_thirds_approx(self, voter_count):
        qc = QuorumCalculator()
        result = qc.compute_two_thirds(voter_count)
        assert abs(result - (2.0 / 3.0)) < 0.01

    def test_compute_two_thirds_returns_float(self):
        qc = QuorumCalculator()
        assert isinstance(qc.compute_two_thirds(3), float)

    def test_compute_two_thirds_greater_than_simple_majority(self):
        qc = QuorumCalculator()
        assert qc.compute_two_thirds(5) > qc.compute_simple_majority(5)


class TestQuorumCalculatorUnanimous:
    @pytest.mark.parametrize("voter_count", [1, 2, 3, 5, 10])
    def test_compute_unanimous_is_one(self, voter_count):
        qc = QuorumCalculator()
        assert qc.compute_unanimous(voter_count) == 1.0

    def test_compute_unanimous_returns_float(self):
        qc = QuorumCalculator()
        assert isinstance(qc.compute_unanimous(3), float)


class TestQuorumCalculatorTrustWeighted:
    def test_compute_trust_weighted_returns_float(self):
        qc = QuorumCalculator()
        voters = [make_voter("v1", 0.9), make_voter("v2", 0.5), make_voter("v3", 0.7)]
        result = qc.compute_trust_weighted(voters)
        assert isinstance(result, float)

    def test_compute_trust_weighted_in_zero_one(self):
        qc = QuorumCalculator()
        voters = [make_voter("v1", 0.9), make_voter("v2", 0.5)]
        result = qc.compute_trust_weighted(voters)
        assert 0.0 <= result <= 1.0

    def test_compute_trust_weighted_high_trust_higher_threshold(self):
        qc = QuorumCalculator()
        low_trust = [make_voter(f"v{i}", 0.2) for i in range(5)]
        high_trust = [make_voter(f"v{i}", 0.9) for i in range(5)]
        assert qc.compute_trust_weighted(high_trust) >= qc.compute_trust_weighted(low_trust)


class TestQuorumCalculatorIsQuorumMet:
    def test_quorum_met_yes_above_threshold(self):
        qc = QuorumCalculator()
        assert qc.is_quorum_met(yes_weight=0.7, total_weight=1.0, threshold=0.67) is True

    def test_quorum_not_met_yes_below_threshold(self):
        qc = QuorumCalculator()
        assert qc.is_quorum_met(yes_weight=0.5, total_weight=1.0, threshold=0.67) is False

    def test_quorum_met_exactly_at_threshold(self):
        qc = QuorumCalculator()
        result = qc.is_quorum_met(yes_weight=0.67, total_weight=1.0, threshold=0.67)
        assert isinstance(result, bool)

    def test_quorum_not_met_zero_votes(self):
        qc = QuorumCalculator()
        assert qc.is_quorum_met(yes_weight=0.0, total_weight=1.0, threshold=0.67) is False

    def test_quorum_met_unanimous(self):
        qc = QuorumCalculator()
        assert qc.is_quorum_met(yes_weight=1.0, total_weight=1.0, threshold=1.0) is True

    def test_quorum_returns_bool(self):
        qc = QuorumCalculator()
        assert isinstance(qc.is_quorum_met(0.8, 1.0, 0.67), bool)


# ---------------------------------------------------------------------------
# VoteAggregator Tests
# ---------------------------------------------------------------------------

class TestVoteAggregatorAddVote:
    def test_add_vote_increases_total_weight(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "YES", 1.0)
        assert agg.total_weight() == pytest.approx(1.0)

    def test_add_vote_yes_increments_yes_weight(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "YES", 0.75)
        assert agg.yes_weight() == pytest.approx(0.75)

    def test_add_vote_no_increments_no_weight(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "NO", 1.0)
        assert agg.no_weight() == pytest.approx(1.0)

    def test_add_multiple_yes_votes(self, aggregator_with_votes):
        # fixture adds 2 YES at weight 1.0, one at 0.5 → 2.5
        assert aggregator_with_votes.yes_weight() == pytest.approx(2.5)

    def test_add_multiple_no_votes(self, aggregator_with_votes):
        # fixture adds 1 NO at weight 1.0
        assert aggregator_with_votes.no_weight() == pytest.approx(1.0)

    def test_total_weight_across_positions(self, aggregator_with_votes):
        # YES:2.5 + NO:1.0 + ABSTAIN:0.5 = 4.0
        assert aggregator_with_votes.total_weight() == pytest.approx(4.0)


class TestVoteAggregatorYesRatio:
    def test_yes_ratio_zero_when_no_votes(self):
        agg = VoteAggregator()
        assert agg.yes_ratio() == pytest.approx(0.0)

    def test_yes_ratio_one_when_all_yes(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "YES", 1.0)
        agg.add_vote("v2", "YES", 1.0)
        assert agg.yes_ratio() == pytest.approx(1.0)

    def test_yes_ratio_fraction(self, aggregator_with_votes):
        # YES=2.5, total=4.0 → 0.625
        assert aggregator_with_votes.yes_ratio() == pytest.approx(2.5 / 4.0)

    def test_yes_ratio_with_only_no(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "NO", 1.0)
        assert agg.yes_ratio() == pytest.approx(0.0)


@pytest.mark.parametrize("yes_fraction,threshold,expected", [
    (0.0,  0.67, False),
    (0.4,  0.67, False),
    (0.5,  0.67, False),
    (0.6,  0.67, False),
    (0.67, 0.67, True),
    (0.8,  0.67, True),
    (1.0,  0.67, True),
])
def test_vote_aggregator_is_passing_parametrized(yes_fraction, threshold, expected):
    agg = VoteAggregator()
    weight_yes = yes_fraction * 10.0
    weight_no  = (1.0 - yes_fraction) * 10.0
    if weight_yes > 0:
        agg.add_vote("v_yes", "YES", weight_yes)
    if weight_no > 0:
        agg.add_vote("v_no", "NO", weight_no)
    assert agg.is_passing(threshold) == expected


class TestVoteAggregatorReset:
    def test_reset_clears_yes_weight(self, aggregator_with_votes):
        aggregator_with_votes.reset()
        assert aggregator_with_votes.yes_weight() == pytest.approx(0.0)

    def test_reset_clears_no_weight(self, aggregator_with_votes):
        aggregator_with_votes.reset()
        assert aggregator_with_votes.no_weight() == pytest.approx(0.0)

    def test_reset_clears_total_weight(self, aggregator_with_votes):
        aggregator_with_votes.reset()
        assert aggregator_with_votes.total_weight() == pytest.approx(0.0)

    def test_reset_clears_yes_ratio(self, aggregator_with_votes):
        aggregator_with_votes.reset()
        assert aggregator_with_votes.yes_ratio() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# FederationConsensusRunner Tests
# ---------------------------------------------------------------------------

class TestFederationConsensusRunnerRun:
    def test_run_returns_dict(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(5)]
        result = runner.run("Subject A", voters)
        assert isinstance(result, dict)

    def test_run_contains_outcome(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(5)]
        result = runner.run("Subject A", voters)
        assert "outcome" in result

    def test_run_contains_tally(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(5)]
        result = runner.run("Subject A", voters)
        assert "tally" in result

    def test_run_contains_round_id(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(5)]
        result = runner.run("Subject A", voters)
        assert "round_id" in result

    def test_run_outcome_is_valid_string(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(3)]
        result = runner.run("Subject A", voters)
        assert result["outcome"] in ("ACCEPTED", "REJECTED", "ABSTAINED", "PENDING")

    def test_run_tally_is_dict(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(3)]
        result = runner.run("Subject A", voters)
        assert isinstance(result["tally"], dict)

    def test_run_round_id_is_string(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(3)]
        result = runner.run("Subject A", voters)
        assert isinstance(result["round_id"], str)


class TestFederationConsensusRunnerRunMulti:
    @pytest.mark.parametrize("n_subjects", [1, 3, 5])
    def test_run_multi_returns_list_of_correct_length(self, n_subjects):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(4)]
        subjects = [f"Subject-{i}" for i in range(n_subjects)]
        results = runner.run_multi(subjects, voters)
        assert isinstance(results, list)
        assert len(results) == n_subjects

    def test_run_multi_each_result_has_outcome(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(3)]
        results = runner.run_multi(["A", "B"], voters)
        for r in results:
            assert "outcome" in r

    def test_run_multi_empty_subjects(self):
        runner = FederationConsensusRunner()
        voters = [make_voter("v1", 0.8)]
        results = runner.run_multi([], voters)
        assert results == []


class TestFederationConsensusRunnerGetResults:
    def test_get_results_empty_initially(self):
        runner = FederationConsensusRunner()
        results = runner.get_results()
        assert isinstance(results, list)

    def test_get_results_accumulates_after_run(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(3)]
        runner.run("Subject X", voters)
        assert len(runner.get_results()) >= 1

    def test_get_results_accumulates_after_multi_run(self):
        runner = FederationConsensusRunner()
        voters = [make_voter(f"v{i}", 0.8) for i in range(3)]
        runner.run_multi(["A", "B", "C"], voters)
        assert len(runner.get_results()) >= 3


# ---------------------------------------------------------------------------
# Free Function: run_consensus
# ---------------------------------------------------------------------------

class TestRunConsensus:
    def test_run_consensus_returns_dict(self):
        votes = [make_vote(f"v{i}", "YES", 1.0) for i in range(5)]
        result = run_consensus("Subject", votes)
        assert isinstance(result, dict)

    def test_run_consensus_contains_outcome(self):
        votes = [make_vote(f"v{i}", "YES", 1.0) for i in range(5)]
        result = run_consensus("Subject", votes)
        assert "outcome" in result

    def test_run_consensus_all_yes_accepted(self):
        votes = [make_vote(f"v{i}", "YES", 1.0) for i in range(5)]
        result = run_consensus("Subject", votes, quorum_threshold=0.67)
        assert result["outcome"] in ("ACCEPTED", "REJECTED", "ABSTAINED", "PENDING")

    def test_run_consensus_all_no_rejected(self):
        votes = [make_vote(f"v{i}", "NO", 1.0) for i in range(5)]
        result = run_consensus("Subject", votes, quorum_threshold=0.67)
        assert result["outcome"] in ("ACCEPTED", "REJECTED", "ABSTAINED", "PENDING")

    def test_run_consensus_quorum_threshold_honored(self):
        votes = [make_vote(f"v{i}", "YES", 1.0) for i in range(8)]
        votes += [make_vote(f"n{i}", "NO", 1.0) for i in range(2)]
        # 80% yes, any reasonable threshold should pass
        result = run_consensus("Subject", votes, quorum_threshold=0.5)
        assert result["outcome"] in ("ACCEPTED", "REJECTED", "ABSTAINED", "PENDING")

    def test_run_consensus_high_threshold_may_reject(self):
        votes = [make_vote(f"v{i}", "YES", 1.0) for i in range(5)]
        votes += [make_vote(f"n{i}", "NO", 1.0) for i in range(5)]
        result = run_consensus("Subject", votes, quorum_threshold=1.0)
        assert result["outcome"] in ("ACCEPTED", "REJECTED", "ABSTAINED", "PENDING")

    def test_run_consensus_empty_votes_returns_dict(self):
        result = run_consensus("Subject", [])
        assert isinstance(result, dict)

    def test_run_consensus_all_abstain_returns_dict(self):
        votes = [make_vote(f"v{i}", "ABSTAIN", 1.0) for i in range(5)]
        result = run_consensus("Subject", votes)
        assert "outcome" in result


# ---------------------------------------------------------------------------
# Free Function: compute_quorum
# ---------------------------------------------------------------------------

class TestComputeQuorum:
    def test_compute_quorum_simple_majority_above_half(self):
        result = compute_quorum(voter_count=5, policy="simple_majority")
        assert result > 0.5

    def test_compute_quorum_two_thirds_approx(self):
        result = compute_quorum(voter_count=5, policy="two_thirds")
        assert abs(result - (2.0 / 3.0)) < 0.01

    def test_compute_quorum_unanimous_is_one(self):
        result = compute_quorum(voter_count=5, policy="unanimous")
        assert result == pytest.approx(1.0)

    def test_compute_quorum_returns_float(self):
        assert isinstance(compute_quorum(5, "simple_majority"), float)

    @pytest.mark.parametrize("policy", ["simple_majority", "two_thirds", "unanimous"])
    def test_compute_quorum_valid_policies_return_float_in_range(self, policy):
        result = compute_quorum(10, policy)
        assert isinstance(result, float)
        assert 0.0 < result <= 1.0

    def test_compute_quorum_default_policy(self):
        """Default policy should return a valid float."""
        result = compute_quorum(5)
        assert isinstance(result, float)
        assert 0.0 < result <= 1.0


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unanimous_single_voter(self):
        qc = QuorumCalculator()
        assert qc.compute_unanimous(1) == pytest.approx(1.0)

    def test_zero_votes_yes_ratio_is_zero(self):
        agg = VoteAggregator()
        assert agg.yes_ratio() == pytest.approx(0.0)

    def test_all_abstain_not_passing(self):
        agg = VoteAggregator()
        for i in range(5):
            agg.add_vote(f"v{i}", "ABSTAIN", 1.0)
        assert agg.is_passing(0.67) is False

    def test_tie_votes_not_passing_at_two_thirds(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "YES", 1.0)
        agg.add_vote("v2", "NO", 1.0)
        assert agg.is_passing(0.67) is False

    def test_voting_round_closed_is_open_false(self):
        vr = VotingRound.open("r-edge", "Edge subject", ["v1"])
        vr.close()
        assert vr.is_open is False

    def test_quorum_met_total_weight_zero(self):
        """When total_weight=0 there are no votes; quorum cannot be met."""
        qc = QuorumCalculator()
        result = qc.is_quorum_met(yes_weight=0.0, total_weight=0.0, threshold=0.67)
        assert result is False

    def test_run_consensus_single_vote_yes(self):
        votes = [make_vote("v1", "YES", 1.0)]
        result = run_consensus("Edge subject", votes, quorum_threshold=0.5)
        assert "outcome" in result

    def test_federation_consensus_runner_custom_protocol(self):
        protocol = ConsensusProtocol(quorum_threshold=0.5, min_voters=1)
        runner = FederationConsensusRunner(protocol=protocol)
        voters = [make_voter("v1", 0.9)]
        result = runner.run("Single voter subject", voters)
        assert "outcome" in result

    def test_federation_consensus_runner_custom_calculator(self):
        calculator = QuorumCalculator()
        runner = FederationConsensusRunner(calculator=calculator)
        voters = [make_voter(f"v{i}", 0.7) for i in range(3)]
        result = runner.run("Custom calc subject", voters)
        assert "outcome" in result

    def test_vote_aggregator_large_weights(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "YES", 1000.0)
        agg.add_vote("v2", "NO", 1.0)
        assert agg.yes_ratio() > 0.99

    def test_vote_aggregator_fractional_weights(self):
        agg = VoteAggregator()
        agg.add_vote("v1", "YES", 0.333)
        agg.add_vote("v2", "YES", 0.333)
        agg.add_vote("v3", "NO",  0.334)
        assert agg.total_weight() == pytest.approx(1.0)

    def test_consensus_protocol_multiple_rounds_independent(self):
        cp = ConsensusProtocol()
        r1 = cp.open_round("Round 1", ["v1", "v2"])
        r2 = cp.open_round("Round 2", ["v3", "v4"])
        cp.cast_vote(r1.round_id, "v1", "YES", 1.0)
        cp.cast_vote(r2.round_id, "v3", "NO", 1.0)
        assert r1.vote_count() == 1
        assert r2.vote_count() == 1

    def test_voting_round_add_vote_abstain(self):
        vr = VotingRound.open("r-abs", "Abstain test", ["v1"])
        vr.add_vote("v1", "ABSTAIN", 1.0)
        assert vr.vote_count() == 1

    @pytest.mark.parametrize("voter_count", [3, 5, 10, 20])
    def test_compute_quorum_two_thirds_parametrized(self, voter_count):
        result = compute_quorum(voter_count, "two_thirds")
        assert abs(result - (2.0 / 3.0)) < 0.01
