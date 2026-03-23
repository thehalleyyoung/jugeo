from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
Tests for jugeo.ideation.discovery_federation.algorithms

This module defines the expected API for the federation algorithm subsystem using
Test-Driven Development (TDD). The implementation does not exist yet; these tests
specify the contract that the implementation must satisfy.

Classes under test:
  - FederationAlgorithms : collection of federation computation algorithms

Free functions under test:
  - compute_federation_distance : non-negative, symmetric pairwise distance
  - rank_federation_candidates   : returns candidates sorted by criteria
"""

from jugeo.ideation.discovery_federation.algorithms import (
    FederationAlgorithms,
    compute_federation_distance,
    rank_federation_candidates,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_node(
    node_id: str = "n1",
    trust_score: float = 0.8,
    authority_level: str = "LOCAL",
) -> dict:
    """Return a minimal node dict for use in tests."""
    return {
        "node_id": node_id,
        "trust_score": trust_score,
        "authority_level": authority_level,
    }


def make_authority(
    node_id: str = "n1",
    level: str = "LOCAL",
    granted_at: float = 0.0,
) -> dict:
    """Return a minimal authority dict for use in tests."""
    return {
        "node_id": node_id,
        "level": level,
        "granted_at": granted_at,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_knowledge_dict() -> dict:
    """A knowledge dict containing an 'items' list and metadata."""
    return {
        "items": ["fact_A", "fact_B", "fact_C", "fact_D"],
        "source": "node_001",
        "version": 1,
    }


@pytest.fixture()
def sample_nodes_list() -> list:
    """A list of five node dicts with varying trust scores."""
    return [
        make_node("n1", trust_score=0.9),
        make_node("n2", trust_score=0.7),
        make_node("n3", trust_score=0.5),
        make_node("n4", trust_score=0.3),
        make_node("n5", trust_score=0.1),
    ]


@pytest.fixture()
def sample_discovery_dict() -> dict:
    """A discovery dict with trust_score, node_id, and payload."""
    return {
        "node_id": "discovery_node_001",
        "trust_score": 0.75,
        "payload": {"data": "sample_payload", "tags": ["alpha", "beta"]},
    }


@pytest.fixture()
def algorithms() -> FederationAlgorithms:
    """A default FederationAlgorithms instance."""
    return FederationAlgorithms()


# ---------------------------------------------------------------------------
# FederationAlgorithms.propagate Tests
# ---------------------------------------------------------------------------

class TestPropagateBasic:
    def test_propagate_returns_dict(self, algorithms, sample_knowledge_dict):
        result = algorithms.propagate(sample_knowledge_dict, ["n1", "n2", "n3"])
        assert isinstance(result, dict)

    def test_propagate_empty_nodes_returns_dict(self, algorithms, sample_knowledge_dict):
        result = algorithms.propagate(sample_knowledge_dict, [])
        assert isinstance(result, dict)

    def test_propagate_empty_knowledge_returns_dict(self, algorithms):
        result = algorithms.propagate({}, ["n1", "n2"])
        assert isinstance(result, dict)

    def test_propagate_single_node(self, algorithms, sample_knowledge_dict):
        result = algorithms.propagate(sample_knowledge_dict, ["n1"])
        assert isinstance(result, dict)

    def test_propagate_contains_node_keys_or_status(self, algorithms, sample_knowledge_dict):
        nodes = ["n1", "n2", "n3"]
        result = algorithms.propagate(sample_knowledge_dict, nodes)
        # The result must reference the nodes or contain a status/summary key
        has_nodes = any(n in result for n in nodes)
        has_summary = any(k in result for k in ("status", "nodes", "summary", "propagated"))
        assert has_nodes or has_summary

    @pytest.mark.parametrize("node_count", [1, 3, 5, 10])
    def test_propagate_scales_to_node_count(self, algorithms, sample_knowledge_dict, node_count):
        nodes = [f"n{i}" for i in range(node_count)]
        result = algorithms.propagate(sample_knowledge_dict, nodes)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# FederationAlgorithms.consensus_vote Tests
# ---------------------------------------------------------------------------

class TestConsensusVoteBasic:
    VALID_OUTCOMES = {"ACCEPTED", "REJECTED", "ABSTAINED", "PENDING"}

    def test_consensus_vote_returns_dict(self, algorithms):
        voters = [{"voter_id": f"v{i}", "vote": "YES", "weight": 1.0} for i in range(3)]
        result = algorithms.consensus_vote(voters, "Test subject")
        assert isinstance(result, dict)

    def test_consensus_vote_contains_outcome(self, algorithms):
        voters = [{"voter_id": "v1", "vote": "YES", "weight": 1.0}]
        result = algorithms.consensus_vote(voters, "Test subject")
        assert "outcome" in result

    def test_consensus_vote_contains_yes_ratio(self, algorithms):
        voters = [{"voter_id": "v1", "vote": "YES", "weight": 1.0}]
        result = algorithms.consensus_vote(voters, "Test subject")
        assert "yes_ratio" in result

    def test_consensus_vote_outcome_valid_string(self, algorithms):
        voters = [{"voter_id": f"v{i}", "vote": "YES", "weight": 1.0} for i in range(3)]
        result = algorithms.consensus_vote(voters, "Subject")
        assert result["outcome"] in self.VALID_OUTCOMES

    def test_consensus_vote_yes_ratio_in_zero_one(self, algorithms):
        voters = [{"voter_id": f"v{i}", "vote": "YES", "weight": 1.0} for i in range(3)]
        result = algorithms.consensus_vote(voters, "Subject")
        assert 0.0 <= result["yes_ratio"] <= 1.0

    def test_consensus_vote_empty_voters(self, algorithms):
        result = algorithms.consensus_vote([], "Empty subject")
        assert isinstance(result, dict)
        assert "outcome" in result

    @pytest.mark.parametrize("yes_fraction", [0.0, 0.3, 0.5, 0.7, 1.0])
    def test_consensus_vote_yes_ratio_reflects_input(self, algorithms, yes_fraction):
        n = 10
        yes_count = int(round(yes_fraction * n))
        voters = [{"voter_id": f"y{i}", "vote": "YES", "weight": 1.0} for i in range(yes_count)]
        voters += [{"voter_id": f"n{i}", "vote": "NO", "weight": 1.0} for i in range(n - yes_count)]
        result = algorithms.consensus_vote(voters, "Fraction subject")
        assert abs(result["yes_ratio"] - yes_fraction) < 0.01

    def test_consensus_vote_all_no_low_yes_ratio(self, algorithms):
        voters = [{"voter_id": f"v{i}", "vote": "NO", "weight": 1.0} for i in range(5)]
        result = algorithms.consensus_vote(voters, "All NO")
        assert result["yes_ratio"] == pytest.approx(0.0)

    def test_consensus_vote_all_abstain(self, algorithms):
        voters = [{"voter_id": f"v{i}", "vote": "ABSTAIN", "weight": 1.0} for i in range(4)]
        result = algorithms.consensus_vote(voters, "All abstain")
        assert result["outcome"] in self.VALID_OUTCOMES


# ---------------------------------------------------------------------------
# FederationAlgorithms.authority_grant Tests
# ---------------------------------------------------------------------------

class TestAuthorityGrant:
    def test_authority_grant_returns_dict(self, algorithms):
        result = algorithms.authority_grant({"verified": True, "trusted": True}, "LOCAL")
        assert isinstance(result, dict)

    def test_authority_grant_contains_granted(self, algorithms):
        result = algorithms.authority_grant({}, "LOCAL")
        assert "granted" in result

    def test_authority_grant_all_true_conditions_grants(self, algorithms):
        conditions = {"verified": True, "trusted": True, "active": True}
        result = algorithms.authority_grant(conditions, "LOCAL")
        assert result["granted"] is True

    def test_authority_grant_all_false_conditions_denies(self, algorithms):
        conditions = {"verified": False, "trusted": False, "active": False}
        result = algorithms.authority_grant(conditions, "LOCAL")
        assert result["granted"] is False

    def test_authority_grant_empty_conditions_returns_dict(self, algorithms):
        result = algorithms.authority_grant({}, "GLOBAL")
        assert isinstance(result, dict)

    def test_authority_grant_mixed_conditions(self, algorithms):
        conditions = {"verified": True, "trusted": False}
        result = algorithms.authority_grant(conditions, "LOCAL")
        assert isinstance(result["granted"], bool)

    def test_authority_grant_different_levels_return_dict(self, algorithms):
        for level in ("LOCAL", "REGIONAL", "GLOBAL"):
            result = algorithms.authority_grant({"verified": True}, level)
            assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# FederationAlgorithms.conflict_resolve Tests
# ---------------------------------------------------------------------------

class TestConflictResolve:
    def test_conflict_resolve_returns_dict(self, algorithms):
        conflict = {"node_a": "n1", "node_b": "n2", "type": "data_mismatch"}
        result = algorithms.conflict_resolve(conflict)
        assert isinstance(result, dict)

    def test_conflict_resolve_contains_resolution(self, algorithms):
        conflict = {"node_a": "n1", "node_b": "n2", "type": "data_mismatch"}
        result = algorithms.conflict_resolve(conflict)
        assert "resolution" in result

    def test_conflict_resolve_resolution_non_empty(self, algorithms):
        conflict = {"node_a": "n1", "node_b": "n2", "type": "authority_clash"}
        result = algorithms.conflict_resolve(conflict)
        assert result["resolution"] != "" and result["resolution"] is not None

    def test_conflict_resolve_empty_conflict(self, algorithms):
        result = algorithms.conflict_resolve({})
        assert isinstance(result, dict)

    def test_conflict_resolve_unknown_type(self, algorithms):
        conflict = {"type": "unknown_type_xyz"}
        result = algorithms.conflict_resolve(conflict)
        assert "resolution" in result

    def test_conflict_resolve_resolution_is_string(self, algorithms):
        conflict = {"node_a": "n1", "node_b": "n2", "type": "version_conflict"}
        result = algorithms.conflict_resolve(conflict)
        assert isinstance(result["resolution"], str)


# ---------------------------------------------------------------------------
# FederationAlgorithms.federation_score Tests
# ---------------------------------------------------------------------------

class TestFederationScore:
    def test_federation_score_returns_float(self, algorithms, sample_discovery_dict):
        score = algorithms.federation_score(sample_discovery_dict)
        assert isinstance(score, float)

    def test_federation_score_in_zero_one(self, algorithms, sample_discovery_dict):
        score = algorithms.federation_score(sample_discovery_dict)
        assert 0.0 <= score <= 1.0

    def test_federation_score_high_trust_higher_score(self, algorithms):
        high = make_node("h", trust_score=0.9)
        low  = make_node("l", trust_score=0.1)
        assert algorithms.federation_score(high) >= algorithms.federation_score(low)

    def test_federation_score_empty_node(self, algorithms):
        score = algorithms.federation_score({})
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    @pytest.mark.parametrize("trust_score", [0.0, 0.1, 0.5, 0.9, 1.0])
    def test_federation_score_parametrized_trust(self, algorithms, trust_score):
        node = make_node("v", trust_score=trust_score)
        score = algorithms.federation_score(node)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# FederationAlgorithms.compute_trust_distance Tests
# ---------------------------------------------------------------------------

class TestComputeTrustDistance:
    def test_trust_distance_returns_float(self, algorithms):
        a = make_node("a", trust_score=0.8)
        b = make_node("b", trust_score=0.5)
        result = algorithms.compute_trust_distance(a, b)
        assert isinstance(result, float)

    def test_trust_distance_non_negative(self, algorithms):
        a = make_node("a", trust_score=0.8)
        b = make_node("b", trust_score=0.2)
        assert algorithms.compute_trust_distance(a, b) >= 0.0

    def test_trust_distance_symmetric(self, algorithms):
        a = make_node("a", trust_score=0.9)
        b = make_node("b", trust_score=0.3)
        assert algorithms.compute_trust_distance(a, b) == pytest.approx(
            algorithms.compute_trust_distance(b, a)
        )

    def test_trust_distance_same_node_zero(self, algorithms):
        a = make_node("a", trust_score=0.7)
        assert algorithms.compute_trust_distance(a, a) == pytest.approx(0.0)

    def test_trust_distance_identical_dicts_zero(self, algorithms):
        a = make_node("a", trust_score=0.7)
        b = make_node("a", trust_score=0.7)
        assert algorithms.compute_trust_distance(a, b) == pytest.approx(0.0)

    def test_trust_distance_extreme_values(self, algorithms):
        a = make_node("a", trust_score=0.0)
        b = make_node("b", trust_score=1.0)
        d = algorithms.compute_trust_distance(a, b)
        assert d >= 0.0
        assert algorithms.compute_trust_distance(b, a) == pytest.approx(d)


# ---------------------------------------------------------------------------
# FederationAlgorithms.rank_nodes_by_trust Tests
# ---------------------------------------------------------------------------

class TestRankNodesByTrust:
    def test_rank_returns_list(self, algorithms, sample_nodes_list):
        result = algorithms.rank_nodes_by_trust(sample_nodes_list)
        assert isinstance(result, list)

    def test_rank_returns_same_length(self, algorithms, sample_nodes_list):
        result = algorithms.rank_nodes_by_trust(sample_nodes_list)
        assert len(result) == len(sample_nodes_list)

    def test_rank_descending_order(self, algorithms, sample_nodes_list):
        result = algorithms.rank_nodes_by_trust(sample_nodes_list)
        scores = [n["trust_score"] for n in result]
        assert scores == sorted(scores, reverse=True)

    def test_rank_empty_list(self, algorithms):
        result = algorithms.rank_nodes_by_trust([])
        assert result == []

    def test_rank_single_node(self, algorithms):
        node = make_node("only", trust_score=0.5)
        result = algorithms.rank_nodes_by_trust([node])
        assert len(result) == 1

    def test_rank_all_same_trust(self, algorithms):
        nodes = [make_node(f"n{i}", trust_score=0.5) for i in range(4)]
        result = algorithms.rank_nodes_by_trust(nodes)
        assert len(result) == 4

    @pytest.mark.parametrize("n_nodes", [1, 3, 5, 10])
    def test_rank_parametrized_lengths(self, algorithms, n_nodes):
        nodes = [make_node(f"n{i}", trust_score=float(i) / n_nodes) for i in range(n_nodes)]
        result = algorithms.rank_nodes_by_trust(nodes)
        assert len(result) == n_nodes
        scores = [r["trust_score"] for r in result]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# FederationAlgorithms.compute_knowledge_overlap Tests
# ---------------------------------------------------------------------------

class TestComputeKnowledgeOverlap:
    def test_overlap_identical_sets_is_one(self, algorithms):
        s = {"a", "b", "c"}
        assert algorithms.compute_knowledge_overlap(s, s) == pytest.approx(1.0)

    def test_overlap_identical_separate_sets_is_one(self, algorithms):
        s1 = {"a", "b", "c"}
        s2 = {"a", "b", "c"}
        assert algorithms.compute_knowledge_overlap(s1, s2) == pytest.approx(1.0)

    def test_overlap_disjoint_sets_is_zero(self, algorithms):
        s1 = {"a", "b", "c"}
        s2 = {"d", "e", "f"}
        assert algorithms.compute_knowledge_overlap(s1, s2) == pytest.approx(0.0)

    def test_overlap_partial_overlap_in_range(self, algorithms):
        s1 = {"a", "b", "c", "d"}
        s2 = {"c", "d", "e", "f"}
        result = algorithms.compute_knowledge_overlap(s1, s2)
        assert 0.0 < result < 1.0

    def test_overlap_symmetric(self, algorithms):
        s1 = {"a", "b", "c"}
        s2 = {"b", "c", "d"}
        assert algorithms.compute_knowledge_overlap(s1, s2) == pytest.approx(
            algorithms.compute_knowledge_overlap(s2, s1)
        )

    def test_overlap_returns_float(self, algorithms):
        result = algorithms.compute_knowledge_overlap({"a"}, {"a"})
        assert isinstance(result, float)

    def test_overlap_empty_sets(self, algorithms):
        """Both empty — expected to return 0.0 or 1.0 (implementation defined)."""
        result = algorithms.compute_knowledge_overlap(set(), set())
        assert result == pytest.approx(0.0) or result == pytest.approx(1.0)

    def test_overlap_one_empty_set(self, algorithms):
        result = algorithms.compute_knowledge_overlap(set(), {"a", "b"})
        assert result == pytest.approx(0.0)

    def test_overlap_subset_relation(self, algorithms):
        full = {"a", "b", "c", "d"}
        sub  = {"a", "b"}
        partial = algorithms.compute_knowledge_overlap(sub, full)
        assert 0.0 < partial < 1.0

    def test_overlap_value_in_zero_one(self, algorithms):
        s1 = {"x", "y"}
        s2 = {"y", "z"}
        result = algorithms.compute_knowledge_overlap(s1, s2)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# FederationAlgorithms.compute_authority_decay Tests
# ---------------------------------------------------------------------------

class TestComputeAuthorityDecay:
    def test_decay_returns_float(self, algorithms):
        authority = make_authority("n1", "LOCAL", granted_at=0.0)
        result = algorithms.compute_authority_decay(authority, current_time=0.0)
        assert isinstance(result, float)

    def test_decay_in_zero_one(self, algorithms):
        authority = make_authority("n1", "LOCAL", granted_at=0.0)
        result = algorithms.compute_authority_decay(authority, current_time=100.0)
        assert 0.0 <= result <= 1.0

    def test_decay_at_time_zero_close_to_one(self, algorithms):
        authority = make_authority("n1", "LOCAL", granted_at=0.0)
        result = algorithms.compute_authority_decay(authority, current_time=0.0)
        assert result >= 0.9

    def test_decay_at_large_time_close_to_zero(self, algorithms):
        authority = make_authority("n1", "LOCAL", granted_at=0.0)
        result = algorithms.compute_authority_decay(authority, current_time=1_000_000.0)
        assert result < 0.1

    def test_decay_strictly_decreasing(self, algorithms):
        authority = make_authority("n1", "LOCAL", granted_at=0.0)
        times = [0.0, 10.0, 100.0, 1000.0]
        values = [algorithms.compute_authority_decay(authority, t) for t in times]
        for i in range(len(values) - 1):
            assert values[i] >= values[i + 1]

    def test_decay_larger_time_less_or_equal(self, algorithms):
        authority = make_authority("n1", "LOCAL", granted_at=0.0)
        d1 = algorithms.compute_authority_decay(authority, 50.0)
        d2 = algorithms.compute_authority_decay(authority, 200.0)
        assert d1 >= d2

    def test_decay_empty_authority_returns_float(self, algorithms):
        result = algorithms.compute_authority_decay({}, current_time=0.0)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# FederationAlgorithms.select_merge_candidates Tests
# ---------------------------------------------------------------------------

class TestSelectMergeCandidates:
    def test_returns_list(self, algorithms, sample_nodes_list):
        result = algorithms.select_merge_candidates(sample_nodes_list, top_k=3)
        assert isinstance(result, list)

    def test_length_at_most_top_k(self, algorithms, sample_nodes_list):
        result = algorithms.select_merge_candidates(sample_nodes_list, top_k=3)
        assert len(result) <= 3

    def test_is_subset_of_input(self, algorithms, sample_nodes_list):
        result = algorithms.select_merge_candidates(sample_nodes_list, top_k=3)
        input_ids = {n["node_id"] for n in sample_nodes_list}
        for node in result:
            assert node["node_id"] in input_ids

    def test_empty_input_returns_empty(self, algorithms):
        result = algorithms.select_merge_candidates([], top_k=3)
        assert result == []

    def test_top_k_larger_than_nodes_returns_all(self, algorithms, sample_nodes_list):
        result = algorithms.select_merge_candidates(sample_nodes_list, top_k=100)
        assert len(result) == len(sample_nodes_list)

    def test_default_top_k_returns_at_most_three(self, algorithms, sample_nodes_list):
        result = algorithms.select_merge_candidates(sample_nodes_list)
        assert len(result) <= 3

    def test_top_k_one_returns_single_node(self, algorithms, sample_nodes_list):
        result = algorithms.select_merge_candidates(sample_nodes_list, top_k=1)
        assert len(result) == 1

    @pytest.mark.parametrize("n_nodes", [1, 3, 5, 10])
    def test_select_parametrized_node_counts(self, algorithms, n_nodes):
        nodes = [make_node(f"n{i}", trust_score=float(i) / max(n_nodes, 1)) for i in range(n_nodes)]
        result = algorithms.select_merge_candidates(nodes, top_k=3)
        assert len(result) <= min(3, n_nodes)


# ---------------------------------------------------------------------------
# Free Function: compute_federation_distance Tests
# ---------------------------------------------------------------------------

class TestComputeFederationDistance:
    def test_returns_float(self):
        a = make_node("a", trust_score=0.8)
        b = make_node("b", trust_score=0.4)
        result = compute_federation_distance(a, b)
        assert isinstance(result, float)

    def test_non_negative(self):
        a = make_node("a", trust_score=0.9)
        b = make_node("b", trust_score=0.1)
        assert compute_federation_distance(a, b) >= 0.0

    def test_symmetric(self):
        a = make_node("a", trust_score=0.9)
        b = make_node("b", trust_score=0.2)
        assert compute_federation_distance(a, b) == pytest.approx(
            compute_federation_distance(b, a)
        )

    def test_same_node_zero(self):
        a = make_node("a", trust_score=0.7)
        assert compute_federation_distance(a, a) == pytest.approx(0.0)

    def test_identical_dicts_zero(self):
        a = make_node("x", trust_score=0.6)
        b = make_node("x", trust_score=0.6)
        assert compute_federation_distance(a, b) == pytest.approx(0.0)

    def test_empty_nodes(self):
        result = compute_federation_distance({}, {})
        assert isinstance(result, float)
        assert result >= 0.0

    @pytest.mark.parametrize("trust_a,trust_b", [
        (0.0, 1.0),
        (0.5, 0.5),
        (0.9, 0.1),
        (1.0, 0.0),
    ])
    def test_symmetry_parametrized(self, trust_a, trust_b):
        a = make_node("a", trust_score=trust_a)
        b = make_node("b", trust_score=trust_b)
        assert compute_federation_distance(a, b) == pytest.approx(
            compute_federation_distance(b, a)
        )


# ---------------------------------------------------------------------------
# Free Function: rank_federation_candidates Tests
# ---------------------------------------------------------------------------

class TestRankFederationCandidates:
    def test_returns_list(self):
        candidates = [make_node(f"n{i}", trust_score=float(i) / 5) for i in range(5)]
        result = rank_federation_candidates(candidates, criteria={"trust_score": "desc"})
        assert isinstance(result, list)

    def test_same_length_as_input(self):
        candidates = [make_node(f"n{i}", trust_score=0.5) for i in range(4)]
        result = rank_federation_candidates(candidates, criteria={})
        assert len(result) == 4

    def test_empty_input_returns_empty(self):
        result = rank_federation_candidates([], criteria={})
        assert result == []

    def test_single_candidate_returns_single(self):
        candidates = [make_node("solo", trust_score=0.7)]
        result = rank_federation_candidates(candidates, criteria={})
        assert len(result) == 1

    def test_trust_score_desc_ordering(self):
        candidates = [
            make_node("n1", trust_score=0.3),
            make_node("n2", trust_score=0.9),
            make_node("n3", trust_score=0.6),
        ]
        result = rank_federation_candidates(candidates, criteria={"trust_score": "desc"})
        scores = [n["trust_score"] for n in result]
        assert scores == sorted(scores, reverse=True)

    def test_empty_criteria_returns_list(self):
        candidates = [make_node(f"n{i}", trust_score=float(i) / 3) for i in range(3)]
        result = rank_federation_candidates(candidates, criteria={})
        assert isinstance(result, list)
        assert len(result) == 3

    def test_result_contains_same_node_ids(self):
        candidates = [make_node(f"n{i}", trust_score=float(i) / 5) for i in range(5)]
        result = rank_federation_candidates(candidates, criteria={})
        input_ids = {n["node_id"] for n in candidates}
        result_ids = {n["node_id"] for n in result}
        assert input_ids == result_ids

    @pytest.mark.parametrize("n_candidates", [1, 3, 5, 10])
    def test_rank_parametrized_lengths(self, n_candidates):
        candidates = [
            make_node(f"n{i}", trust_score=float(i) / max(n_candidates, 1))
            for i in range(n_candidates)
        ]
        result = rank_federation_candidates(candidates, criteria={"trust_score": "desc"})
        assert len(result) == n_candidates


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_propagate_large_node_list(self, algorithms, sample_knowledge_dict):
        nodes = [f"node_{i}" for i in range(50)]
        result = algorithms.propagate(sample_knowledge_dict, nodes)
        assert isinstance(result, dict)

    def test_consensus_vote_single_voter_yes(self, algorithms):
        voters = [{"voter_id": "v1", "vote": "YES", "weight": 1.0}]
        result = algorithms.consensus_vote(voters, "single voter")
        assert result["yes_ratio"] == pytest.approx(1.0)

    def test_consensus_vote_single_voter_no(self, algorithms):
        voters = [{"voter_id": "v1", "vote": "NO", "weight": 1.0}]
        result = algorithms.consensus_vote(voters, "single no")
        assert result["yes_ratio"] == pytest.approx(0.0)

    def test_federation_score_trust_zero(self, algorithms):
        node = make_node("z", trust_score=0.0)
        score = algorithms.federation_score(node)
        assert 0.0 <= score <= 1.0

    def test_federation_score_trust_one(self, algorithms):
        node = make_node("o", trust_score=1.0)
        score = algorithms.federation_score(node)
        assert 0.0 <= score <= 1.0

    def test_select_merge_top_k_zero(self, algorithms, sample_nodes_list):
        result = algorithms.select_merge_candidates(sample_nodes_list, top_k=0)
        assert len(result) == 0

    def test_rank_nodes_preserves_all_fields(self, algorithms):
        node = {"node_id": "n_full", "trust_score": 0.5, "authority_level": "LOCAL", "extra": "data"}
        result = algorithms.rank_nodes_by_trust([node])
        assert result[0].get("extra") == "data"

    def test_overlap_single_element_sets_overlap(self, algorithms):
        assert algorithms.compute_knowledge_overlap({"a"}, {"a"}) == pytest.approx(1.0)

    def test_overlap_single_element_sets_no_overlap(self, algorithms):
        assert algorithms.compute_knowledge_overlap({"a"}, {"b"}) == pytest.approx(0.0)

    def test_compute_trust_distance_triangle_inequality(self, algorithms):
        a = make_node("a", trust_score=0.9)
        b = make_node("b", trust_score=0.5)
        c = make_node("c", trust_score=0.1)
        d_ab = algorithms.compute_trust_distance(a, b)
        d_bc = algorithms.compute_trust_distance(b, c)
        d_ac = algorithms.compute_trust_distance(a, c)
        assert d_ac <= d_ab + d_bc + 1e-9

    def test_authority_decay_monotone_over_many_steps(self, algorithms):
        authority = make_authority("n", "LOCAL", granted_at=0.0)
        prev = algorithms.compute_authority_decay(authority, 0.0)
        for t in [1.0, 5.0, 10.0, 50.0, 100.0, 500.0]:
            cur = algorithms.compute_authority_decay(authority, t)
            assert cur <= prev + 1e-9
            prev = cur

    def test_rank_federation_candidates_stable_on_equal_scores(self):
        candidates = [make_node(f"n{i}", trust_score=0.5) for i in range(5)]
        result = rank_federation_candidates(candidates, criteria={"trust_score": "desc"})
        assert len(result) == 5

    def test_compute_federation_distance_triangle_inequality(self):
        a = make_node("a", trust_score=0.9)
        b = make_node("b", trust_score=0.5)
        c = make_node("c", trust_score=0.1)
        d_ab = compute_federation_distance(a, b)
        d_bc = compute_federation_distance(b, c)
        d_ac = compute_federation_distance(a, c)
        assert d_ac <= d_ab + d_bc + 1e-9

    def test_authority_grant_single_true_condition(self, algorithms):
        result = algorithms.authority_grant({"verified": True}, "LOCAL")
        assert isinstance(result["granted"], bool)

    def test_conflict_resolve_with_competing_truths(self, algorithms):
        conflict = {
            "node_a": "n1", "claim_a": "X is true",
            "node_b": "n2", "claim_b": "X is false",
            "type": "data_conflict",
        }
        result = algorithms.conflict_resolve(conflict)
        assert "resolution" in result
        assert result["resolution"]
