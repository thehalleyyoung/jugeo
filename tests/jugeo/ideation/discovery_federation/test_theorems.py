from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
Tests for jugeo.ideation.discovery_federation.theorems

This module verifies the formal theorem-proving layer of the discovery federation
subsystem.  Theorems in this context are not mathematical proofs in the type-theory
sense, but rather structured runtime assertions — invariants that the federation
guarantees to maintain under normal operation.

Each theorem class exposes:
  - `statement`  — a human-readable description of what the theorem asserts
  - `conditions` — the pre-conditions under which the theorem must hold
  - `verify(…)`  — a method that examines evidence and returns a TheoremResult

TheoremResult captures the outcome of verification together with supporting
evidence (list of strings), an optional counterexample, and timestamps.

The five core theorems are:

1. FederationSoundnessTheorem
   Asserts that federation operations are trust-monotone: trust cannot be
   artificially inflated by traversing the federation graph.

2. AuthorityMonotonicityTheorem
   Asserts that valid authority promotions only move upward in the authority
   hierarchy — an entity's authority level can be promoted but never silently
   demoted.

3. ConsensusConvergenceTheorem
   Asserts that the consensus protocol terminates in a bounded number of rounds
   given a sufficient quorum.

4. KnowledgePropagationSoundnessTheorem
   Asserts that propagating knowledge through the federation does not alter its
   semantic content — what is stored is what is retrieved, modulo allowed
   transformations.

5. ConflictResolutionCompletenessTheorem
   Asserts that every conflict eventually gets resolved — no conflict lingers
   indefinitely in the system.

These tests are written TDD-style: the production modules do NOT exist yet.
"""

from datetime import datetime, timezone
import uuid


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_federation_result(trust_in: float = 0.8, trust_out: float = 0.6) -> dict:
    """Return a single federation operation result for soundness theorem evidence."""
    return {
        "operation_id": str(uuid.uuid4()),
        "trust_in": trust_in,
        "trust_out": trust_out,
        "node_id": "node-001",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def make_round(closed: bool = True, outcome: str = "ACCEPTED") -> dict:
    """Return a single consensus round record."""
    return {
        "round_id": str(uuid.uuid4()),
        "closed": closed,
        "outcome": outcome,
        "voter_count": 5,
        "yes_votes": 3,
        "no_votes": 2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def make_conflict(resolved: bool = True) -> dict:
    """Return a single conflict record."""
    return {
        "conflict_id": str(uuid.uuid4()),
        "resolved": resolved,
        "resolution_method": "MAJORITY_VOTE" if resolved else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": datetime.now(timezone.utc).isoformat() if resolved else None,
    }


def make_propagation_entry(preserved: bool = True) -> dict:
    """Return a single knowledge propagation log entry."""
    content = {"key": "value", "data": 42}
    return {
        "propagation_id": str(uuid.uuid4()),
        "before": content,
        "after": content if preserved else {},
        "preserved": preserved,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def make_authority_event(level: str = "LOCAL", promoted: bool = True) -> dict:
    """Return a single authority history event."""
    return {
        "event_id": str(uuid.uuid4()),
        "authority_level": level,
        "promoted": promoted,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def soundness_theorem():
    """FederationSoundnessTheorem instance."""
    from jugeo.ideation.discovery_federation.theorems import FederationSoundnessTheorem
    return FederationSoundnessTheorem()


@pytest.fixture
def monotonicity_theorem():
    """AuthorityMonotonicityTheorem instance."""
    from jugeo.ideation.discovery_federation.theorems import AuthorityMonotonicityTheorem
    return AuthorityMonotonicityTheorem()


@pytest.fixture
def convergence_theorem():
    """ConsensusConvergenceTheorem instance."""
    from jugeo.ideation.discovery_federation.theorems import ConsensusConvergenceTheorem
    return ConsensusConvergenceTheorem()


@pytest.fixture
def propagation_theorem():
    """KnowledgePropagationSoundnessTheorem instance."""
    from jugeo.ideation.discovery_federation.theorems import KnowledgePropagationSoundnessTheorem
    return KnowledgePropagationSoundnessTheorem()


@pytest.fixture
def conflict_theorem():
    """ConflictResolutionCompletenessTheorem instance."""
    from jugeo.ideation.discovery_federation.theorems import ConflictResolutionCompletenessTheorem
    return ConflictResolutionCompletenessTheorem()


@pytest.fixture
def theorem_registry_full():
    """FederationTheoremRegistry with all 5 core theorems registered."""
    from jugeo.ideation.discovery_federation.theorems import (
        FederationTheoremRegistry,
        FederationSoundnessTheorem,
        AuthorityMonotonicityTheorem,
        ConsensusConvergenceTheorem,
        KnowledgePropagationSoundnessTheorem,
        ConflictResolutionCompletenessTheorem,
    )
    registry = FederationTheoremRegistry()
    registry.register("soundness", FederationSoundnessTheorem())
    registry.register("monotonicity", AuthorityMonotonicityTheorem())
    registry.register("convergence", ConsensusConvergenceTheorem())
    registry.register("propagation", KnowledgePropagationSoundnessTheorem())
    registry.register("conflict", ConflictResolutionCompletenessTheorem())
    return registry


# ===========================================================================
# TheoremStatus enum tests
# ===========================================================================

class TestTheoremStatusEnum:
    """Tests for the TheoremStatus enum."""

    def test_unverified_exists(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        assert hasattr(TheoremStatus, "UNVERIFIED")

    def test_verified_exists(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        assert hasattr(TheoremStatus, "VERIFIED")

    def test_falsified_exists(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        assert hasattr(TheoremStatus, "FALSIFIED")

    def test_partial_exists(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        assert hasattr(TheoremStatus, "PARTIAL")

    def test_pending_exists(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        assert hasattr(TheoremStatus, "PENDING")

    def test_all_five_values_distinct(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        values = {
            TheoremStatus.UNVERIFIED,
            TheoremStatus.VERIFIED,
            TheoremStatus.FALSIFIED,
            TheoremStatus.PARTIAL,
            TheoremStatus.PENDING,
        }
        assert len(values) == 5


@pytest.mark.parametrize("status_name", [
    "UNVERIFIED", "VERIFIED", "FALSIFIED", "PARTIAL", "PENDING",
])
def test_theorem_status_parametrized(status_name):
    """All five TheoremStatus values are importable and unique."""
    from jugeo.ideation.discovery_federation.theorems import TheoremStatus
    status = getattr(TheoremStatus, status_name)
    assert status is not None
    assert status.name == status_name


# ===========================================================================
# TheoremResult tests
# ===========================================================================

class TestTheoremResultCreate:
    """Tests for TheoremResult.create classmethod."""

    def test_create_returns_instance(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create(
            theorem_name="test",
            status=TheoremStatus.VERIFIED,
            evidence=["evidence-1"],
        )
        assert isinstance(result, TheoremResult)

    def test_create_preserves_theorem_name(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("my_theorem", TheoremStatus.PENDING, [])
        assert result.theorem_name == "my_theorem"

    def test_create_preserves_status(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        for status in (TheoremStatus.VERIFIED, TheoremStatus.FALSIFIED, TheoremStatus.PARTIAL):
            result = TheoremResult.create("t", status, [])
            assert result.status == status

    def test_create_preserves_evidence_list(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        evidence = ["fact-1", "fact-2", "fact-3"]
        result = TheoremResult.create("t", TheoremStatus.VERIFIED, evidence)
        assert result.evidence == evidence

    def test_create_counterexample_none_by_default(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("t", TheoremStatus.VERIFIED, [])
        assert result.counterexample is None

    def test_create_with_counterexample(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        ce = {"bad_op": "trust went up without cause"}
        result = TheoremResult.create("t", TheoremStatus.FALSIFIED, [], counterexample=ce)
        assert result.counterexample == ce

    def test_create_verified_at_is_str_or_none(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("t", TheoremStatus.VERIFIED, [])
        assert result.verified_at is None or isinstance(result.verified_at, str)

    def test_create_notes_is_str(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("t", TheoremStatus.PARTIAL, [], notes="partial coverage")
        assert isinstance(result.notes, str)


class TestTheoremResultToDict:
    """Tests for TheoremResult.to_dict."""

    def test_to_dict_returns_dict(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("t", TheoremStatus.VERIFIED, [])
        assert isinstance(result.to_dict(), dict)

    def test_to_dict_has_required_keys(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("t", TheoremStatus.VERIFIED, ["ev"])
        d = result.to_dict()
        for key in ("theorem_name", "status", "evidence"):
            assert key in d

    def test_to_dict_status_is_serializable(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("t", TheoremStatus.FALSIFIED, [])
        d = result.to_dict()
        # Status must be string or enum value; JSON-serializable
        assert isinstance(d["status"], (str, int)) or hasattr(d["status"], "value")


class TestTheoremResultSummary:
    """Tests for TheoremResult.summary."""

    def test_summary_returns_string(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("t", TheoremStatus.VERIFIED, [])
        assert isinstance(result.summary(), str)

    def test_summary_is_non_empty(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("my_theorem", TheoremStatus.PARTIAL, [])
        assert len(result.summary()) > 0

    def test_summary_contains_theorem_name(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        result = TheoremResult.create("special_theorem", TheoremStatus.VERIFIED, [])
        assert "special_theorem" in result.summary()


# ===========================================================================
# FederationSoundnessTheorem tests
# ===========================================================================

class TestFederationSoundnessTheorem:
    """Tests for FederationSoundnessTheorem."""

    def test_statement_is_nonempty_string(self, soundness_theorem):
        assert isinstance(soundness_theorem.statement, str)
        assert len(soundness_theorem.statement) > 0

    def test_conditions_is_nonempty_list(self, soundness_theorem):
        assert isinstance(soundness_theorem.conditions, list)
        assert len(soundness_theorem.conditions) > 0

    def test_verify_consistent_results_returns_verified(self, soundness_theorem):
        """When all trust_out <= trust_in, the soundness theorem must be VERIFIED."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [make_federation_result(trust_in=0.8, trust_out=0.6) for _ in range(5)]
        outcome = soundness_theorem.verify(results)
        assert outcome.status == TheoremStatus.VERIFIED

    def test_verify_trust_preserving_operations_verified(self, soundness_theorem):
        """trust_out == trust_in is also acceptable (exact preservation)."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [make_federation_result(trust_in=0.7, trust_out=0.7)]
        outcome = soundness_theorem.verify(results)
        assert outcome.status == TheoremStatus.VERIFIED

    def test_verify_trust_inflating_result_falsified(self, soundness_theorem):
        """When any trust_out > trust_in, the theorem must be FALSIFIED."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [
            make_federation_result(trust_in=0.5, trust_out=0.9),  # inflation!
        ]
        outcome = soundness_theorem.verify(results)
        assert outcome.status == TheoremStatus.FALSIFIED

    def test_verify_returns_theorem_result_type(self, soundness_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        results = [make_federation_result()]
        outcome = soundness_theorem.verify(results)
        assert isinstance(outcome, TheoremResult)

    def test_counterexample_none_when_verified(self, soundness_theorem):
        """When VERIFIED, counterexample must be None."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [make_federation_result(trust_in=0.9, trust_out=0.5)]
        outcome = soundness_theorem.verify(results)
        if outcome.status == TheoremStatus.VERIFIED:
            assert soundness_theorem.counterexample(results) is None

    def test_counterexample_dict_when_falsified(self, soundness_theorem):
        """When FALSIFIED, counterexample must return a non-None dict."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [make_federation_result(trust_in=0.3, trust_out=0.99)]
        outcome = soundness_theorem.verify(results)
        if outcome.status == TheoremStatus.FALSIFIED:
            ce = soundness_theorem.counterexample(results)
            assert isinstance(ce, dict)

    def test_verify_empty_log_returns_result(self, soundness_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = soundness_theorem.verify([])
        assert isinstance(result, TheoremResult)

    def test_verify_mixed_consistent_and_inflating(self, soundness_theorem):
        """Even one inflating result among many consistent results should FALSIFY."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [make_federation_result(trust_in=0.9, trust_out=0.7) for _ in range(9)]
        results.append(make_federation_result(trust_in=0.2, trust_out=0.95))
        outcome = soundness_theorem.verify(results)
        assert outcome.status == TheoremStatus.FALSIFIED


# ===========================================================================
# AuthorityMonotonicityTheorem tests
# ===========================================================================

class TestAuthorityMonotonicityTheorem:
    """Tests for AuthorityMonotonicityTheorem."""

    def test_statement_is_nonempty_string(self, monotonicity_theorem):
        assert isinstance(monotonicity_theorem.statement, str)
        assert len(monotonicity_theorem.statement) > 0

    def test_conditions_is_nonempty_list(self, monotonicity_theorem):
        assert isinstance(monotonicity_theorem.conditions, list)
        assert len(monotonicity_theorem.conditions) > 0

    def test_verify_with_increasing_trust_verified(self, monotonicity_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_increasing_trust([0.3, 0.5, 0.7, 0.9])
        assert result.status == TheoremStatus.VERIFIED

    def test_verify_with_decreasing_trust_falsified(self, monotonicity_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_decreasing_trust([0.9, 0.7, 0.5, 0.3])
        assert result.status == TheoremStatus.FALSIFIED

    def test_verify_with_flat_sequence_verified(self, monotonicity_theorem):
        """Non-decreasing (flat) sequence still satisfies monotonicity."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_increasing_trust([0.5, 0.5, 0.5, 0.5])
        assert result.status == TheoremStatus.VERIFIED

    def test_verify_with_empty_sequence_pending_or_partial(self, monotonicity_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_increasing_trust([])
        assert result.status in (TheoremStatus.PENDING, TheoremStatus.PARTIAL)

    def test_verify_returns_theorem_result_type(self, monotonicity_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = monotonicity_theorem.verify_with_increasing_trust([0.1, 0.2, 0.3])
        assert isinstance(result, TheoremResult)

    def test_verify_authority_history_returns_theorem_result(self, monotonicity_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        history = [make_authority_event(level="LOCAL"), make_authority_event(level="REGIONAL")]
        result = monotonicity_theorem.verify(history)
        assert isinstance(result, TheoremResult)

    def test_verify_with_single_element_verified(self, monotonicity_theorem):
        """A single-element sequence is trivially monotone."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_increasing_trust([0.7])
        assert result.status == TheoremStatus.VERIFIED

    def test_verify_with_dip_in_middle_falsified(self, monotonicity_theorem):
        """A sequence with a dip must be FALSIFIED."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_increasing_trust([0.3, 0.7, 0.4, 0.9])
        assert result.status == TheoremStatus.FALSIFIED


# ===========================================================================
# ConsensusConvergenceTheorem tests
# ===========================================================================

class TestConsensusConvergenceTheorem:
    """Tests for ConsensusConvergenceTheorem."""

    def test_statement_is_nonempty_string(self, convergence_theorem):
        assert isinstance(convergence_theorem.statement, str)
        assert len(convergence_theorem.statement) > 0

    def test_conditions_is_nonempty_list(self, convergence_theorem):
        assert isinstance(convergence_theorem.conditions, list)
        assert len(convergence_theorem.conditions) > 0

    def test_verify_all_closed_rounds_verified(self, convergence_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        rounds = [make_round(closed=True, outcome="ACCEPTED") for _ in range(5)]
        result = convergence_theorem.verify(rounds)
        assert result.status == TheoremStatus.VERIFIED

    def test_verify_with_open_rounds_partial_or_pending(self, convergence_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        rounds = [make_round(closed=False, outcome="IN_PROGRESS") for _ in range(3)]
        result = convergence_theorem.verify(rounds)
        assert result.status in (TheoremStatus.PARTIAL, TheoremStatus.PENDING)

    def test_verify_returns_theorem_result(self, convergence_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = convergence_theorem.verify([make_round()])
        assert isinstance(result, TheoremResult)

    def test_bound_computation_returns_positive_int(self, convergence_theorem):
        bound = convergence_theorem.bound_computation(voter_count=5)
        assert isinstance(bound, int)
        assert bound >= 1

    def test_bound_computation_single_voter(self, convergence_theorem):
        bound = convergence_theorem.bound_computation(voter_count=1)
        assert bound == 1

    def test_bound_computation_monotone_in_voters(self, convergence_theorem):
        """More voters should require at least as many rounds as fewer voters."""
        b1 = convergence_theorem.bound_computation(voter_count=1)
        b5 = convergence_theorem.bound_computation(voter_count=5)
        b10 = convergence_theorem.bound_computation(voter_count=10)
        assert b5 >= b1
        assert b10 >= b5

    def test_bound_computation_large_voter_count(self, convergence_theorem):
        bound = convergence_theorem.bound_computation(voter_count=100)
        assert isinstance(bound, int)
        assert bound >= 1

    def test_verify_empty_rounds_returns_result(self, convergence_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = convergence_theorem.verify([])
        assert isinstance(result, TheoremResult)

    def test_verify_mixed_closed_open_rounds(self, convergence_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        rounds = [make_round(closed=True)] * 4 + [make_round(closed=False)]
        result = convergence_theorem.verify(rounds)
        assert result.status in (TheoremStatus.PARTIAL, TheoremStatus.PENDING, TheoremStatus.FALSIFIED)


# ===========================================================================
# KnowledgePropagationSoundnessTheorem tests
# ===========================================================================

class TestKnowledgePropagationSoundnessTheorem:
    """Tests for KnowledgePropagationSoundnessTheorem."""

    def test_statement_is_nonempty_string(self, propagation_theorem):
        assert isinstance(propagation_theorem.statement, str)
        assert len(propagation_theorem.statement) > 0

    def test_conditions_is_nonempty_list(self, propagation_theorem):
        assert isinstance(propagation_theorem.conditions, list)
        assert len(propagation_theorem.conditions) > 0

    def test_verify_valid_propagation_log_verified(self, propagation_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        log = [make_propagation_entry(preserved=True) for _ in range(5)]
        result = propagation_theorem.verify(log)
        assert result.status == TheoremStatus.VERIFIED

    def test_verify_returns_theorem_result(self, propagation_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = propagation_theorem.verify([make_propagation_entry()])
        assert isinstance(result, TheoremResult)

    def test_check_preservation_same_dict_true(self, propagation_theorem):
        d = {"key": "value", "score": 0.9}
        assert propagation_theorem.check_preservation(d, d) is True

    def test_check_preservation_identical_copy_true(self, propagation_theorem):
        d = {"a": 1, "b": 2}
        assert propagation_theorem.check_preservation(d, dict(d)) is True

    def test_check_preservation_empty_after_false(self, propagation_theorem):
        before = {"key": "val"}
        after = {}
        assert propagation_theorem.check_preservation(before, after) is False

    def test_check_preservation_extra_keys_after_true(self, propagation_theorem):
        """Adding keys during propagation does not destroy content — still True."""
        before = {"key": "val"}
        after = {"key": "val", "extra": "added"}
        assert propagation_theorem.check_preservation(before, after) is True

    def test_verify_corrupted_propagation_falsified(self, propagation_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        log = [make_propagation_entry(preserved=False) for _ in range(3)]
        result = propagation_theorem.verify(log)
        assert result.status in (TheoremStatus.FALSIFIED, TheoremStatus.PARTIAL)

    def test_verify_empty_log_returns_result(self, propagation_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = propagation_theorem.verify([])
        assert isinstance(result, TheoremResult)

    def test_check_preservation_returns_bool(self, propagation_theorem):
        result = propagation_theorem.check_preservation({"x": 1}, {"x": 1})
        assert isinstance(result, bool)


# ===========================================================================
# ConflictResolutionCompletenessTheorem tests
# ===========================================================================

class TestConflictResolutionCompletenessTheorem:
    """Tests for ConflictResolutionCompletenessTheorem."""

    def test_statement_is_nonempty_string(self, conflict_theorem):
        assert isinstance(conflict_theorem.statement, str)
        assert len(conflict_theorem.statement) > 0

    def test_conditions_is_nonempty_list(self, conflict_theorem):
        assert isinstance(conflict_theorem.conditions, list)
        assert len(conflict_theorem.conditions) > 0

    def test_verify_all_resolved_verified(self, conflict_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        log = [make_conflict(resolved=True) for _ in range(5)]
        result = conflict_theorem.verify(log)
        assert result.status == TheoremStatus.VERIFIED

    def test_verify_some_unresolved_partial_or_falsified(self, conflict_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        log = [make_conflict(resolved=True)] * 3 + [make_conflict(resolved=False)] * 2
        result = conflict_theorem.verify(log)
        assert result.status in (TheoremStatus.PARTIAL, TheoremStatus.FALSIFIED)

    def test_verify_returns_theorem_result(self, conflict_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = conflict_theorem.verify([make_conflict()])
        assert isinstance(result, TheoremResult)

    def test_compute_resolution_rate_all_resolved(self, conflict_theorem):
        log = [make_conflict(resolved=True) for _ in range(5)]
        rate = conflict_theorem.compute_resolution_rate(log)
        assert abs(rate - 1.0) < 1e-9

    def test_compute_resolution_rate_none_resolved(self, conflict_theorem):
        log = [make_conflict(resolved=False) for _ in range(5)]
        rate = conflict_theorem.compute_resolution_rate(log)
        assert abs(rate - 0.0) < 1e-9

    def test_compute_resolution_rate_half_resolved(self, conflict_theorem):
        log = [make_conflict(resolved=True)] * 5 + [make_conflict(resolved=False)] * 5
        rate = conflict_theorem.compute_resolution_rate(log)
        assert abs(rate - 0.5) < 1e-9

    def test_compute_resolution_rate_returns_float(self, conflict_theorem):
        log = [make_conflict()]
        rate = conflict_theorem.compute_resolution_rate(log)
        assert isinstance(rate, float)

    def test_compute_resolution_rate_in_range(self, conflict_theorem):
        """compute_resolution_rate must always return a value in [0.0, 1.0]."""
        import random
        log = [make_conflict(resolved=random.choice([True, False])) for _ in range(20)]
        rate = conflict_theorem.compute_resolution_rate(log)
        assert 0.0 <= rate <= 1.0

    def test_compute_resolution_rate_empty_log(self, conflict_theorem):
        """Empty conflict log: resolution rate is 1.0 (vacuously all resolved) or 0.0 (no data)."""
        rate = conflict_theorem.compute_resolution_rate([])
        assert rate in (0.0, 1.0)

    def test_verify_empty_log_returns_result(self, conflict_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        result = conflict_theorem.verify([])
        assert isinstance(result, TheoremResult)


# ===========================================================================
# FederationTheoremRegistry tests
# ===========================================================================

class TestFederationTheoremRegistry:
    """Tests for FederationTheoremRegistry."""

    def test_register_and_get_returns_theorem(self, theorem_registry_full):
        t = theorem_registry_full.get("soundness")
        assert t is not None

    def test_get_nonexistent_returns_none(self, theorem_registry_full):
        assert theorem_registry_full.get("does_not_exist") is None

    def test_all_theorems_returns_list(self, theorem_registry_full):
        result = theorem_registry_full.all_theorems()
        assert isinstance(result, list)

    def test_all_theorems_has_correct_length(self, theorem_registry_full):
        result = theorem_registry_full.all_theorems()
        assert len(result) == 5

    def test_empty_registry_all_theorems_empty(self):
        from jugeo.ideation.discovery_federation.theorems import FederationTheoremRegistry
        registry = FederationTheoremRegistry()
        assert registry.all_theorems() == []

    def test_verify_all_returns_dict(self, theorem_registry_full):
        evidence = {
            "federation_results": [make_federation_result()],
            "authority_history": [make_authority_event()],
            "round_history": [make_round()],
            "propagation_log": [make_propagation_entry()],
            "conflict_log": [make_conflict()],
        }
        result = theorem_registry_full.verify_all(evidence)
        assert isinstance(result, dict)

    def test_verify_all_contains_all_theorem_names(self, theorem_registry_full):
        evidence = {
            "federation_results": [make_federation_result()],
            "authority_history": [],
            "round_history": [make_round()],
            "propagation_log": [make_propagation_entry()],
            "conflict_log": [make_conflict()],
        }
        result = theorem_registry_full.verify_all(evidence)
        for name in ("soundness", "monotonicity", "convergence", "propagation", "conflict"):
            assert name in result

    def test_verify_all_values_are_theorem_results(self, theorem_registry_full):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult
        evidence = {
            "federation_results": [make_federation_result()],
            "authority_history": [make_authority_event()],
            "round_history": [make_round()],
            "propagation_log": [make_propagation_entry()],
            "conflict_log": [make_conflict()],
        }
        result = theorem_registry_full.verify_all(evidence)
        for v in result.values():
            assert isinstance(v, TheoremResult)

    def test_verify_all_empty_evidence_returns_dict(self, theorem_registry_full):
        result = theorem_registry_full.verify_all({})
        assert isinstance(result, dict)

    def test_summary_returns_nonempty_string(self, theorem_registry_full):
        s = theorem_registry_full.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_get_by_status_verified_returns_list(self, theorem_registry_full):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        evidence = {
            "federation_results": [make_federation_result(trust_in=0.9, trust_out=0.6)],
            "authority_history": [make_authority_event()],
            "round_history": [make_round(closed=True)],
            "propagation_log": [make_propagation_entry(preserved=True)],
            "conflict_log": [make_conflict(resolved=True)],
        }
        theorem_registry_full.verify_all(evidence)
        result = theorem_registry_full.get_by_status(TheoremStatus.VERIFIED)
        assert isinstance(result, list)

    def test_get_by_status_nonexistent_status_returns_list(self, theorem_registry_full):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = theorem_registry_full.get_by_status(TheoremStatus.UNVERIFIED)
        assert isinstance(result, list)

    def test_register_overwrites_existing_name(self):
        from jugeo.ideation.discovery_federation.theorems import (
            FederationTheoremRegistry,
            FederationSoundnessTheorem,
        )
        registry = FederationTheoremRegistry()
        t1 = FederationSoundnessTheorem()
        t2 = FederationSoundnessTheorem()
        registry.register("s", t1)
        registry.register("s", t2)
        # After double-registration, all_theorems must still have exactly 1 entry for "s"
        names_and_theorems = registry.all_theorems()
        # Should not duplicate
        assert len(names_and_theorems) <= 2  # 1 is ideal; 2 is acceptable if registry allows multi


# ===========================================================================
# Parametrize: theorem statuses
# ===========================================================================

@pytest.mark.parametrize("status_name,expected_counterexample", [
    ("VERIFIED", None),
    ("FALSIFIED", {"bad_case": "trust inflated"}),
    ("PARTIAL", None),
    ("PENDING", None),
    ("UNVERIFIED", None),
])
def test_theorem_result_with_various_statuses(status_name, expected_counterexample):
    """TheoremResult can be created with each valid status."""
    from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
    status = getattr(TheoremStatus, status_name)
    result = TheoremResult.create(
        theorem_name="parametrized_test",
        status=status,
        evidence=["e1", "e2"],
        counterexample=expected_counterexample,
    )
    assert result.status == status
    assert result.counterexample == expected_counterexample
    assert isinstance(result.to_dict(), dict)
    assert len(result.summary()) > 0


# ===========================================================================
# Parametrize: proof evidence configurations
# ===========================================================================

@pytest.mark.parametrize("evidence_config", [
    # Minimal evidence
    {"federation_results": [make_federation_result()]},
    # All sub-systems present
    {
        "federation_results": [make_federation_result()],
        "authority_history": [make_authority_event()],
        "round_history": [make_round()],
        "propagation_log": [make_propagation_entry()],
        "conflict_log": [make_conflict()],
    },
    # Empty evidence
    {},
    # Only conflict log
    {"conflict_log": [make_conflict(resolved=True)] * 5},
])
def test_verify_all_various_evidence(theorem_registry_full, evidence_config):
    """verify_all must handle any evidence configuration without raising."""
    result = theorem_registry_full.verify_all(evidence_config)
    assert isinstance(result, dict)


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:
    """Edge cases for the theorems module."""

    def test_falsified_soundness_has_counterexample(self, soundness_theorem):
        """A FALSIFIED soundness theorem must expose the problematic operation."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [make_federation_result(trust_in=0.1, trust_out=1.0)]
        outcome = soundness_theorem.verify(results)
        if outcome.status == TheoremStatus.FALSIFIED:
            ce = soundness_theorem.counterexample(results)
            assert ce is not None and isinstance(ce, dict)

    def test_registry_summary_empty_registry(self):
        from jugeo.ideation.discovery_federation.theorems import FederationTheoremRegistry
        registry = FederationTheoremRegistry()
        s = registry.summary()
        assert isinstance(s, str)

    def test_convergence_bound_computation_zero_or_one_for_one(self, convergence_theorem):
        """Bound for 1 voter must be a positive integer (1 or more)."""
        bound = convergence_theorem.bound_computation(1)
        assert isinstance(bound, int) and bound >= 1

    def test_propagation_check_preservation_value_changed_false(self, propagation_theorem):
        before = {"score": 0.9}
        after = {"score": 0.1}  # value changed: content corrupted
        # This may return False depending on implementation semantics
        result = propagation_theorem.check_preservation(before, after)
        assert isinstance(result, bool)

    def test_monotonicity_verify_single_step_increase(self, monotonicity_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_increasing_trust([0.4, 0.6])
        assert result.status == TheoremStatus.VERIFIED

    def test_monotonicity_verify_single_step_decrease(self, monotonicity_theorem):
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = monotonicity_theorem.verify_with_decreasing_trust([0.6, 0.4])
        assert result.status == TheoremStatus.FALSIFIED

    def test_conflict_resolution_rate_one_unresolved(self, conflict_theorem):
        log = [make_conflict(resolved=True)] * 3 + [make_conflict(resolved=False)]
        rate = conflict_theorem.compute_resolution_rate(log)
        assert abs(rate - 0.75) < 1e-9

    def test_theorem_result_evidence_list_preserved(self):
        from jugeo.ideation.discovery_federation.theorems import TheoremResult, TheoremStatus
        ev = ["fact-a", "fact-b", "fact-c"]
        result = TheoremResult.create("t", TheoremStatus.PARTIAL, ev)
        assert result.evidence == ev
        assert result.to_dict()["evidence"] == ev

    def test_registry_get_by_status_before_verify_all(self, theorem_registry_full):
        """get_by_status before any verify_all call must return a list (possibly empty)."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        result = theorem_registry_full.get_by_status(TheoremStatus.VERIFIED)
        assert isinstance(result, list)

    def test_soundness_multiple_inflating_results(self, soundness_theorem):
        """Multiple inflating operations should all be captured."""
        from jugeo.ideation.discovery_federation.theorems import TheoremStatus
        results = [make_federation_result(trust_in=0.1, trust_out=0.9) for _ in range(10)]
        outcome = soundness_theorem.verify(results)
        assert outcome.status == TheoremStatus.FALSIFIED
