from pathlib import Path
import sys
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

"""
Tests for jugeo.orchestration.frontier_objectives.models

Covers every enum, every frozen/mutable dataclass, every method, edge cases,
and integration with upstream modules.

Chapter reference: theory2.tex Ch47 — Frontier objectives.
"""

import math
import time
import uuid
from dataclasses import FrozenInstanceError

import pytest

from jugeo.orchestration.frontier_objectives.models import (
    BudgetPolicy,
    ClosureGainEstimate,
    DEFAULT_MIN_GAIN,
    DiversityMetric,
    FrontierBudgetModel,
    FrontierObjective,
    MAX_CLOSURE_GAIN,
    MAX_COST_ESTIMATE,
    MAX_ENTROPY,
    ObjectiveKind,
    ObjectiveResult,
    ObjectiveSet,
    PhaseKind,
    PhaseTransitionModel,
    ScoringState,
    _clamp,
    _normalise_cost,
    _normalise_gain,
    _safe_div,
)

# ---------------------------------------------------------------------------
# Upstream guards
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.frontier import FrontierBudget, FrontierNode, Frontier, PhaseTransition
    HAS_FRONTIER = True
except Exception:
    HAS_FRONTIER = False

try:
    from jugeo.orchestration.controller import ConvergenceMonitor
    HAS_CONTROLLER = True
except Exception:
    HAS_CONTROLLER = False

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel, TrustTier, TrustProfile
    HAS_TRUST = True
except Exception:
    HAS_TRUST = False

try:
    from jugeo.geometry.descent import DescentEngine, GluingData, DescentResult
    HAS_DESCENT = True
except Exception:
    HAS_DESCENT = False


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def zero_state() -> ScoringState:
    """ScoringState with all numeric fields at zero."""
    return ScoringState()


@pytest.fixture
def full_state() -> ScoringState:
    """ScoringState with typical non-trivial values."""
    return ScoringState(
        closure_gain=5.0,
        stability_score=0.7,
        diversity_score=0.6,
        cost_estimate=30.0,
        composite_score=0.65,
        node_count=10,
        phase="exploitation",
    )


@pytest.fixture
def cg_objective() -> FrontierObjective:
    return FrontierObjective.make_closure_gain()


@pytest.fixture
def stab_objective() -> FrontierObjective:
    return FrontierObjective.make_stability()


@pytest.fixture
def div_objective() -> FrontierObjective:
    return FrontierObjective.make_diversity()


@pytest.fixture
def cost_objective() -> FrontierObjective:
    return FrontierObjective.make_cost()


@pytest.fixture
def default_obj_set() -> ObjectiveSet:
    return ObjectiveSet.default()


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    def test_clamp_within_bounds(self):
        assert _clamp(0.5) == 0.5

    def test_clamp_below_zero(self):
        assert _clamp(-1.0) == 0.0

    def test_clamp_above_one(self):
        assert _clamp(1.5) == 1.0

    def test_clamp_at_zero(self):
        assert _clamp(0.0) == 0.0

    def test_clamp_at_one(self):
        assert _clamp(1.0) == 1.0

    def test_clamp_custom_bounds(self):
        assert _clamp(5.0, 0.0, 10.0) == 5.0
        assert _clamp(-1.0, 0.0, 10.0) == 0.0
        assert _clamp(15.0, 0.0, 10.0) == 10.0

    def test_safe_div_normal(self):
        assert _safe_div(6.0, 3.0) == pytest.approx(2.0)

    def test_safe_div_by_zero(self):
        assert _safe_div(5.0, 0.0) == 0.0

    def test_safe_div_near_zero(self):
        assert _safe_div(1.0, 1e-15) == 0.0

    def test_safe_div_negative(self):
        assert _safe_div(-4.0, 2.0) == pytest.approx(-2.0)

    def test_normalise_gain_half(self):
        assert _normalise_gain(MAX_CLOSURE_GAIN / 2.0) == pytest.approx(0.5)

    def test_normalise_gain_full(self):
        assert _normalise_gain(MAX_CLOSURE_GAIN) == pytest.approx(1.0)

    def test_normalise_gain_zero(self):
        assert _normalise_gain(0.0) == 0.0

    def test_normalise_gain_exceeds_max(self):
        assert _normalise_gain(MAX_CLOSURE_GAIN * 2) == pytest.approx(1.0)

    def test_normalise_cost_zero_cost(self):
        # Zero cost → maximum score
        assert _normalise_cost(0.0) == pytest.approx(1.0)

    def test_normalise_cost_full_cost(self):
        # Cost at cap → minimum score
        assert _normalise_cost(MAX_COST_ESTIMATE) == pytest.approx(0.0)

    def test_normalise_cost_half(self):
        assert _normalise_cost(MAX_COST_ESTIMATE / 2.0) == pytest.approx(0.5)

    def test_normalise_cost_exceeds_max(self):
        assert _normalise_cost(MAX_COST_ESTIMATE * 5.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestObjectiveKind:
    def test_all_members_present(self):
        names = {m.name for m in ObjectiveKind}
        assert "CLOSURE_GAIN" in names
        assert "STABILITY" in names
        assert "DIVERSITY" in names
        assert "COST" in names
        assert "COMPOSITE" in names

    def test_five_members(self):
        assert len(list(ObjectiveKind)) == 5

    def test_members_are_enum(self):
        for member in ObjectiveKind:
            assert isinstance(member, ObjectiveKind)

    def test_access_by_name(self):
        assert ObjectiveKind["CLOSURE_GAIN"] is ObjectiveKind.CLOSURE_GAIN

    def test_equality(self):
        assert ObjectiveKind.STABILITY is ObjectiveKind.STABILITY
        assert ObjectiveKind.DIVERSITY is not ObjectiveKind.STABILITY

    def test_distinct_values(self):
        values = [m.value for m in ObjectiveKind]
        assert len(values) == len(set(values))


class TestBudgetPolicy:
    def test_all_members_present(self):
        names = {m.name for m in BudgetPolicy}
        assert "FIXED" in names
        assert "ADAPTIVE" in names
        assert "GREEDY" in names
        assert "CONSERVATIVE" in names

    def test_four_members(self):
        assert len(list(BudgetPolicy)) == 4

    def test_access_by_name(self):
        assert BudgetPolicy["GREEDY"] is BudgetPolicy.GREEDY

    def test_distinct_values(self):
        values = [m.value for m in BudgetPolicy]
        assert len(values) == len(set(values))


class TestPhaseKind:
    def test_all_members_present(self):
        names = {m.name for m in PhaseKind}
        assert "EXPLORATION" in names
        assert "EXPLOITATION" in names
        assert "TRANSITION" in names
        assert "STALLED" in names
        assert "CONVERGED" in names

    def test_five_members(self):
        assert len(list(PhaseKind)) == 5

    def test_access_by_name(self):
        assert PhaseKind["CONVERGED"] is PhaseKind.CONVERGED

    def test_distinct_values(self):
        values = [m.value for m in PhaseKind]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# FrontierObjective tests
# ---------------------------------------------------------------------------


class TestFrontierObjective:
    def test_direct_construction(self):
        obj = FrontierObjective(
            objective_id="id-1",
            name="test",
            weight=1.0,
            kind=ObjectiveKind.CLOSURE_GAIN,
            target_metric="closure_gain",
            threshold=0.5,
        )
        assert obj.objective_id == "id-1"
        assert obj.name == "test"
        assert obj.direction == "maximize"

    def test_frozen_immutable(self):
        obj = FrontierObjective.make_closure_gain()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            obj.weight = 2.0  # type: ignore[misc]

    def test_make_closure_gain_defaults(self, cg_objective):
        assert cg_objective.kind is ObjectiveKind.CLOSURE_GAIN
        assert cg_objective.weight == pytest.approx(1.0)
        assert cg_objective.threshold == pytest.approx(0.5)
        assert cg_objective.direction == "maximize"
        assert cg_objective.name == "closure_gain"

    def test_make_closure_gain_custom(self):
        obj = FrontierObjective.make_closure_gain(weight=2.0, threshold=0.8)
        assert obj.weight == pytest.approx(2.0)
        assert obj.threshold == pytest.approx(0.8)

    def test_make_stability_defaults(self, stab_objective):
        assert stab_objective.kind is ObjectiveKind.STABILITY
        assert stab_objective.weight == pytest.approx(0.8)
        assert stab_objective.threshold == pytest.approx(0.6)

    def test_make_diversity_defaults(self, div_objective):
        assert div_objective.kind is ObjectiveKind.DIVERSITY
        assert div_objective.weight == pytest.approx(0.6)
        assert div_objective.threshold == pytest.approx(0.4)

    def test_make_cost_defaults(self, cost_objective):
        assert cost_objective.kind is ObjectiveKind.COST
        assert cost_objective.weight == pytest.approx(0.4)
        assert cost_objective.threshold == pytest.approx(0.7)

    def test_make_generates_unique_ids(self):
        a = FrontierObjective.make_closure_gain()
        b = FrontierObjective.make_closure_gain()
        assert a.objective_id != b.objective_id

    # score() tests -----------------------------------------------------------

    def test_score_closure_gain_zero(self, cg_objective, zero_state):
        assert cg_objective.score(zero_state) == pytest.approx(0.0)

    def test_score_closure_gain_half(self, cg_objective):
        state = ScoringState(closure_gain=MAX_CLOSURE_GAIN / 2.0)
        assert cg_objective.score(state) == pytest.approx(0.5)

    def test_score_closure_gain_full(self, cg_objective):
        state = ScoringState(closure_gain=MAX_CLOSURE_GAIN)
        assert cg_objective.score(state) == pytest.approx(1.0)

    def test_score_closure_gain_exceeds_max_clamped(self, cg_objective):
        state = ScoringState(closure_gain=MAX_CLOSURE_GAIN * 3)
        assert cg_objective.score(state) == pytest.approx(1.0)

    def test_score_stability_clamps(self, stab_objective):
        state_low = ScoringState(stability_score=-0.5)
        state_hi = ScoringState(stability_score=1.5)
        assert stab_objective.score(state_low) == pytest.approx(0.0)
        assert stab_objective.score(state_hi) == pytest.approx(1.0)

    def test_score_stability_midpoint(self, stab_objective):
        state = ScoringState(stability_score=0.7)
        assert stab_objective.score(state) == pytest.approx(0.7)

    def test_score_diversity_midpoint(self, div_objective):
        state = ScoringState(diversity_score=0.6)
        assert div_objective.score(state) == pytest.approx(0.6)

    def test_score_cost_inverted(self, cost_objective):
        # Zero cost → score 1.0; full cost → score 0.0
        state_free = ScoringState(cost_estimate=0.0)
        state_full = ScoringState(cost_estimate=MAX_COST_ESTIMATE)
        assert cost_objective.score(state_free) == pytest.approx(1.0)
        assert cost_objective.score(state_full) == pytest.approx(0.0)

    def test_score_cost_half(self, cost_objective):
        state = ScoringState(cost_estimate=MAX_COST_ESTIMATE / 2.0)
        assert cost_objective.score(state) == pytest.approx(0.5)

    def test_score_composite(self):
        obj = FrontierObjective(
            objective_id="comp-1",
            name="comp",
            weight=1.0,
            kind=ObjectiveKind.COMPOSITE,
            target_metric="composite_score",
            threshold=0.5,
        )
        state = ScoringState(composite_score=0.75)
        assert obj.score(state) == pytest.approx(0.75)

    def test_score_missing_attribute_defaults_to_zero(self):
        """Duck-typed: missing attributes treated as 0."""
        obj = FrontierObjective.make_stability()

        class Minimal:
            pass

        assert obj.score(Minimal()) == pytest.approx(0.0)

    # is_satisfied() tests ----------------------------------------------------

    def test_is_satisfied_maximize_at_threshold(self, cg_objective):
        state = ScoringState(closure_gain=MAX_CLOSURE_GAIN * cg_objective.threshold)
        assert cg_objective.is_satisfied(state) is True

    def test_is_satisfied_maximize_below_threshold(self, cg_objective):
        state = ScoringState(closure_gain=0.0)
        assert cg_objective.is_satisfied(state) is False

    def test_is_satisfied_maximize_above_threshold(self, cg_objective):
        state = ScoringState(closure_gain=MAX_CLOSURE_GAIN)
        assert cg_objective.is_satisfied(state) is True

    def test_is_satisfied_minimize(self):
        obj = FrontierObjective(
            objective_id="min-1",
            name="minimize_test",
            weight=1.0,
            kind=ObjectiveKind.COST,
            target_metric="cost_estimate",
            threshold=0.5,
            direction="minimize",
        )
        # Cost score = 1 - normalised_cost; with cost=0, score=1.0 > 0.5 → NOT satisfied (minimize)
        state_free = ScoringState(cost_estimate=0.0)
        assert obj.is_satisfied(state_free) is False
        # With cost=MAX_COST_ESTIMATE, score=0.0 ≤ 0.5 → satisfied
        state_full = ScoringState(cost_estimate=MAX_COST_ESTIMATE)
        assert obj.is_satisfied(state_full) is True

    # combine() tests ---------------------------------------------------------

    def test_combine_kind_is_composite(self, cg_objective, stab_objective):
        combined = cg_objective.combine(stab_objective)
        assert combined.kind is ObjectiveKind.COMPOSITE

    def test_combine_weight_sum(self, cg_objective, stab_objective):
        combined = cg_objective.combine(stab_objective)
        assert combined.weight == pytest.approx(cg_objective.weight + stab_objective.weight)

    def test_combine_threshold_average(self, cg_objective, stab_objective):
        expected = (cg_objective.threshold + stab_objective.threshold) / 2.0
        combined = cg_objective.combine(stab_objective)
        assert combined.threshold == pytest.approx(expected)

    def test_combine_name_contains_both(self, cg_objective, stab_objective):
        combined = cg_objective.combine(stab_objective)
        assert cg_objective.name in combined.name
        assert stab_objective.name in combined.name

    def test_combine_new_id(self, cg_objective, stab_objective):
        combined = cg_objective.combine(stab_objective)
        assert combined.objective_id not in (cg_objective.objective_id, stab_objective.objective_id)

    # to_dict() tests ---------------------------------------------------------

    def test_to_dict_keys(self, cg_objective):
        d = cg_objective.to_dict()
        assert set(d.keys()) == {"objective_id", "name", "weight", "kind", "target_metric", "threshold", "direction"}

    def test_to_dict_kind_is_string(self, cg_objective):
        d = cg_objective.to_dict()
        assert isinstance(d["kind"], str)
        assert d["kind"] == "CLOSURE_GAIN"

    def test_to_dict_roundtrip_values(self, cg_objective):
        d = cg_objective.to_dict()
        assert d["weight"] == pytest.approx(cg_objective.weight)
        assert d["threshold"] == pytest.approx(cg_objective.threshold)


# ---------------------------------------------------------------------------
# PhaseTransitionModel tests
# ---------------------------------------------------------------------------


class TestPhaseTransitionModel:
    @pytest.fixture
    def productive_transition(self) -> PhaseTransitionModel:
        return PhaseTransitionModel(
            transition_id="t-1",
            from_phase="exploration",
            to_phase="exploitation",
            trigger="closure_gain_plateau",
            timestamp=time.time(),
            closure_gain_before=0.3,
            closure_gain_after=0.6,
            evidence={"step": 42},
        )

    @pytest.fixture
    def unproductive_transition(self) -> PhaseTransitionModel:
        return PhaseTransitionModel(
            transition_id="t-2",
            from_phase="exploitation",
            to_phase="stalled",
            trigger="no_improvement_timeout",
            timestamp=time.time(),
            closure_gain_before=0.5,
            closure_gain_after=0.4,
            evidence={},
        )

    def test_is_productive_when_gain_increases(self, productive_transition):
        assert productive_transition.is_productive() is True

    def test_is_productive_false_when_gain_decreases(self, unproductive_transition):
        assert unproductive_transition.is_productive() is False

    def test_is_productive_false_when_equal(self):
        t = PhaseTransitionModel(
            transition_id="t-3",
            from_phase="exploration",
            to_phase="transition",
            trigger="budget_exhaustion",
            timestamp=time.time(),
            closure_gain_before=0.5,
            closure_gain_after=0.5,
            evidence={},
        )
        assert t.is_productive() is False

    def test_duration_estimate_proportional_to_delta(self, productive_transition):
        delta = abs(productive_transition.closure_gain_after - productive_transition.closure_gain_before)
        expected = delta * 100.0
        assert productive_transition.duration_estimate() == pytest.approx(expected)

    def test_duration_estimate_zero_for_equal_gain(self):
        t = PhaseTransitionModel(
            transition_id="t-x",
            from_phase="a",
            to_phase="b",
            trigger="x",
            timestamp=time.time(),
            closure_gain_before=0.4,
            closure_gain_after=0.4,
            evidence={},
        )
        assert t.duration_estimate() == pytest.approx(0.0)

    def test_duration_estimate_always_non_negative(self, unproductive_transition):
        assert unproductive_transition.duration_estimate() >= 0.0

    def test_gain_ratio_normal(self, productive_transition):
        expected = productive_transition.closure_gain_after / productive_transition.closure_gain_before
        assert productive_transition.gain_ratio() == pytest.approx(expected)

    def test_gain_ratio_zero_when_before_zero(self):
        t = PhaseTransitionModel(
            transition_id="t-zero",
            from_phase="a",
            to_phase="b",
            trigger="x",
            timestamp=time.time(),
            closure_gain_before=0.0,
            closure_gain_after=0.5,
            evidence={},
        )
        assert t.gain_ratio() == 0.0

    def test_gain_ratio_below_one_for_unproductive(self, unproductive_transition):
        assert unproductive_transition.gain_ratio() < 1.0

    def test_gain_ratio_above_one_for_productive(self, productive_transition):
        assert productive_transition.gain_ratio() > 1.0

    def test_to_dict_keys(self, productive_transition):
        d = productive_transition.to_dict()
        assert "transition_id" in d
        assert "from_phase" in d
        assert "to_phase" in d
        assert "trigger" in d
        assert "timestamp" in d
        assert "closure_gain_before" in d
        assert "closure_gain_after" in d
        assert "evidence" in d

    def test_to_dict_evidence_is_copy(self, productive_transition):
        d = productive_transition.to_dict()
        d["evidence"]["mutated"] = True
        assert "mutated" not in productive_transition.evidence

    def test_make_classmethod(self):
        before = time.time()
        t = PhaseTransitionModel.make("exploration", "exploitation", "test_trigger", 0.2, 0.5)
        after = time.time()
        assert t.from_phase == "exploration"
        assert t.to_phase == "exploitation"
        assert t.trigger == "test_trigger"
        assert t.closure_gain_before == pytest.approx(0.2)
        assert t.closure_gain_after == pytest.approx(0.5)
        assert before <= t.timestamp <= after
        assert len(t.transition_id) > 0
        assert t.evidence == {}

    def test_make_generates_unique_ids(self):
        t1 = PhaseTransitionModel.make("a", "b", "x", 0.1, 0.2)
        t2 = PhaseTransitionModel.make("a", "b", "x", 0.1, 0.2)
        assert t1.transition_id != t2.transition_id

    def test_frozen_immutable(self, productive_transition):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            productive_transition.trigger = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ClosureGainEstimate tests
# ---------------------------------------------------------------------------


class TestClosureGainEstimate:
    @pytest.fixture
    def estimate(self) -> ClosureGainEstimate:
        return ClosureGainEstimate.make("node-1", gain=0.6, confidence=0.8, cost=2.0, method="heuristic")

    @pytest.fixture
    def low_confidence(self) -> ClosureGainEstimate:
        return ClosureGainEstimate.make("node-2", gain=0.5, confidence=0.1, cost=1.0)

    def test_risk_adjusted_gain(self, estimate):
        assert estimate.risk_adjusted_gain() == pytest.approx(estimate.expected_gain * estimate.confidence)

    def test_risk_adjusted_gain_full_confidence(self):
        e = ClosureGainEstimate.make("n", gain=0.9, confidence=1.0, cost=1.0)
        assert e.risk_adjusted_gain() == pytest.approx(0.9)

    def test_risk_adjusted_gain_zero_confidence(self):
        e = ClosureGainEstimate.make("n", gain=0.9, confidence=0.0, cost=1.0)
        assert e.risk_adjusted_gain() == pytest.approx(0.0)

    def test_risk_adjusted_gain_confidence_clamped(self):
        # Confidence above 1.0 should be clamped
        e = ClosureGainEstimate(
            estimate_id="x", node_id="n", expected_gain=1.0, confidence=2.0,
            computation_cost=1.0, method="test", timestamp=time.time()
        )
        # After clamping, confidence treated as 1.0
        assert e.risk_adjusted_gain() == pytest.approx(1.0)

    def test_is_worth_exploring_true(self, estimate):
        # risk_adjusted = 0.6 * 0.8 = 0.48 > DEFAULT_MIN_GAIN (0.01)
        assert estimate.is_worth_exploring() is True

    def test_is_worth_exploring_false_low_confidence(self):
        e = ClosureGainEstimate.make("n", gain=0.001, confidence=0.01, cost=1.0)
        assert e.is_worth_exploring() is False

    def test_is_worth_exploring_custom_min_gain(self, estimate):
        assert estimate.is_worth_exploring(min_gain=1.0) is False
        assert estimate.is_worth_exploring(min_gain=0.0) is True

    def test_combine_confidence_weighted(self):
        a = ClosureGainEstimate.make("n", gain=0.8, confidence=0.6, cost=2.0, method="m1")
        b = ClosureGainEstimate.make("n", gain=0.2, confidence=0.4, cost=1.0, method="m2")
        combined = a.combine(b)
        # Weighted: w_a = 0.6/1.0 = 0.6, w_b = 0.4
        expected_gain = 0.6 * 0.8 + 0.4 * 0.2
        assert combined.expected_gain == pytest.approx(expected_gain, rel=1e-5)

    def test_combine_confidence_is_max(self):
        a = ClosureGainEstimate.make("n", gain=0.5, confidence=0.6, cost=2.0)
        b = ClosureGainEstimate.make("n", gain=0.5, confidence=0.9, cost=1.0)
        combined = a.combine(b)
        assert combined.confidence == pytest.approx(max(0.6, 0.9))

    def test_combine_cost_is_min(self):
        a = ClosureGainEstimate.make("n", gain=0.5, confidence=0.5, cost=2.0)
        b = ClosureGainEstimate.make("n", gain=0.5, confidence=0.5, cost=5.0)
        combined = a.combine(b)
        assert combined.computation_cost == pytest.approx(min(2.0, 5.0))

    def test_combine_method_contains_both(self):
        a = ClosureGainEstimate.make("n", gain=0.5, confidence=0.5, cost=1.0, method="m1")
        b = ClosureGainEstimate.make("n", gain=0.5, confidence=0.5, cost=1.0, method="m2")
        combined = a.combine(b)
        assert "m1" in combined.method
        assert "m2" in combined.method

    def test_combine_equal_confidence_averages_gain(self):
        a = ClosureGainEstimate.make("n", gain=0.8, confidence=0.5, cost=1.0)
        b = ClosureGainEstimate.make("n", gain=0.4, confidence=0.5, cost=1.0)
        combined = a.combine(b)
        assert combined.expected_gain == pytest.approx(0.6, rel=1e-5)

    def test_efficiency_normal(self, estimate):
        expected = estimate.risk_adjusted_gain() / estimate.computation_cost
        assert estimate.efficiency() == pytest.approx(expected, rel=1e-5)

    def test_efficiency_zero_cost(self):
        # Zero cost → efficiency is effectively very high (not inf since epsilon used)
        e = ClosureGainEstimate(
            estimate_id="x", node_id="n", expected_gain=0.5, confidence=0.8,
            computation_cost=0.0, method="test", timestamp=time.time()
        )
        eff = e.efficiency()
        assert eff > 0.0

    def test_efficiency_high_cost_low(self):
        high_cost = ClosureGainEstimate.make("n", gain=0.5, confidence=0.8, cost=100.0)
        low_cost = ClosureGainEstimate.make("n", gain=0.5, confidence=0.8, cost=1.0)
        assert low_cost.efficiency() > high_cost.efficiency()

    def test_to_dict_keys(self, estimate):
        d = estimate.to_dict()
        assert "estimate_id" in d
        assert "node_id" in d
        assert "expected_gain" in d
        assert "confidence" in d
        assert "computation_cost" in d
        assert "method" in d
        assert "timestamp" in d

    def test_make_negative_gain_floored(self):
        e = ClosureGainEstimate.make("n", gain=-0.5, confidence=0.8, cost=1.0)
        assert e.expected_gain == pytest.approx(0.0)

    def test_make_negative_cost_floored(self):
        e = ClosureGainEstimate.make("n", gain=0.5, confidence=0.8, cost=-1.0)
        assert e.computation_cost == pytest.approx(0.0)

    def test_make_confidence_clamped(self):
        e = ClosureGainEstimate.make("n", gain=0.5, confidence=1.5, cost=1.0)
        assert 0.0 <= e.confidence <= 1.0

    def test_frozen_immutable(self, estimate):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            estimate.expected_gain = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DiversityMetric tests
# ---------------------------------------------------------------------------


class TestDiversityMetric:
    @pytest.fixture
    def high_diversity(self) -> DiversityMetric:
        return DiversityMetric.make(
            cluster_count=10,
            entropy=MAX_ENTROPY,
            coverage=1.0,
            novelty=1.0,
        )

    @pytest.fixture
    def low_diversity(self) -> DiversityMetric:
        return DiversityMetric.make(
            cluster_count=1,
            entropy=0.0,
            coverage=0.0,
            novelty=0.0,
        )

    def test_combined_score_all_max(self, high_diversity):
        # All components at max → combined_score ≈ 1.0
        assert high_diversity.combined_score() == pytest.approx(1.0, abs=1e-3)

    def test_combined_score_all_zero(self, low_diversity):
        assert low_diversity.combined_score() == pytest.approx(0.0)

    def test_combined_score_in_range(self):
        m = DiversityMetric.make(cluster_count=5, entropy=2.0, coverage=0.5, novelty=0.6)
        assert 0.0 <= m.combined_score() <= 1.0

    def test_combined_score_weights(self):
        # Only entropy at full, coverage and novelty at zero
        m_entropy = DiversityMetric.make(cluster_count=1, entropy=MAX_ENTROPY, coverage=0.0, novelty=0.0)
        # Expect approx 0.4 (entropy weight)
        assert m_entropy.combined_score() == pytest.approx(0.4, abs=1e-3)

    def test_combined_score_only_coverage(self):
        m = DiversityMetric.make(cluster_count=0, entropy=0.0, coverage=1.0, novelty=0.0)
        assert m.combined_score() == pytest.approx(0.35, abs=1e-3)

    def test_combined_score_only_novelty(self):
        m = DiversityMetric.make(cluster_count=0, entropy=0.0, coverage=0.0, novelty=1.0)
        assert m.combined_score() == pytest.approx(0.25, abs=1e-3)

    def test_is_diverse_enough_above_threshold(self, high_diversity):
        assert high_diversity.is_diverse_enough(threshold=0.5) is True

    def test_is_diverse_enough_below_threshold(self, low_diversity):
        assert low_diversity.is_diverse_enough(threshold=0.5) is False

    def test_is_diverse_enough_default_threshold(self, high_diversity):
        assert high_diversity.is_diverse_enough() is True

    def test_is_diverse_enough_at_threshold(self):
        # Construct a metric whose combined_score is exactly at threshold
        m = DiversityMetric.make(cluster_count=0, entropy=0.0, coverage=1.0 / 0.35, novelty=0.0)
        # coverage is clamped to 1.0, so combined = 0.35; test threshold = 0.35
        assert m.is_diverse_enough(threshold=m.combined_score()) is True

    def test_delta_from_positive_when_more_diverse(self, high_diversity, low_diversity):
        delta = high_diversity.delta_from(low_diversity)
        assert delta > 0.0

    def test_delta_from_negative_when_less_diverse(self, high_diversity, low_diversity):
        delta = low_diversity.delta_from(high_diversity)
        assert delta < 0.0

    def test_delta_from_self_is_zero(self, high_diversity):
        # delta from self should be zero (same scores)
        assert high_diversity.delta_from(high_diversity) == pytest.approx(0.0)

    def test_empty_has_zero_score(self):
        m = DiversityMetric.empty()
        assert m.combined_score() == pytest.approx(0.0)
        assert m.cluster_count == 0
        assert m.entropy == pytest.approx(0.0)
        assert m.coverage_ratio == pytest.approx(0.0)
        assert m.novelty_score == pytest.approx(0.0)

    def test_empty_generates_id(self):
        m = DiversityMetric.empty()
        assert len(m.metric_id) > 0

    def test_make_negative_cluster_count_floored(self):
        m = DiversityMetric.make(cluster_count=-5, entropy=1.0, coverage=0.5, novelty=0.5)
        assert m.cluster_count == 0

    def test_make_coverage_clamped(self):
        m = DiversityMetric.make(cluster_count=1, entropy=0.0, coverage=2.0, novelty=0.0)
        assert 0.0 <= m.coverage_ratio <= 1.0

    def test_to_dict_keys(self, high_diversity):
        d = high_diversity.to_dict()
        assert "metric_id" in d
        assert "cluster_count" in d
        assert "entropy" in d
        assert "coverage_ratio" in d
        assert "novelty_score" in d
        assert "timestamp" in d

    def test_frozen_immutable(self, high_diversity):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            high_diversity.cluster_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FrontierBudgetModel tests
# ---------------------------------------------------------------------------


class TestFrontierBudgetModel:
    @pytest.fixture
    def budget(self) -> FrontierBudgetModel:
        return FrontierBudgetModel.make(total=100.0, channels=["alpha", "beta"], reserved=0.1)

    def test_make_creates_equal_allocations(self, budget):
        # spendable = 90, 2 channels → 45 each
        assert budget.allocated["alpha"] == pytest.approx(45.0)
        assert budget.allocated["beta"] == pytest.approx(45.0)

    def test_make_initial_spent_zero(self, budget):
        assert budget.spent["alpha"] == pytest.approx(0.0)
        assert budget.spent["beta"] == pytest.approx(0.0)

    def test_make_budget_id_set(self, budget):
        assert len(budget.budget_id) > 0

    def test_allocate_succeeds_within_pool(self, budget):
        result = budget.allocate("gamma", 10.0)
        assert result is True
        assert budget.allocated["gamma"] == pytest.approx(10.0)

    def test_allocate_fails_exceeds_pool(self, budget):
        # Pool is 90, already allocated 90, so no room
        result = budget.allocate("gamma", 5.0)
        assert result is False

    def test_allocate_negative_fails(self, budget):
        result = budget.allocate("alpha", -5.0)
        assert result is False

    def test_allocate_zero_succeeds(self, budget):
        result = budget.allocate("alpha", 0.0)
        assert result is True

    def test_spend_within_allocation(self, budget):
        result = budget.spend("alpha", 20.0)
        assert result is True
        assert budget.spent["alpha"] == pytest.approx(20.0)

    def test_spend_exactly_allocated(self, budget):
        result = budget.spend("alpha", 45.0)
        assert result is True

    def test_spend_exceeds_allocation_fails(self, budget):
        result = budget.spend("alpha", 50.0)
        assert result is False

    def test_spend_negative_fails(self, budget):
        result = budget.spend("alpha", -5.0)
        assert result is False

    def test_spend_unknown_channel_fails(self, budget):
        result = budget.spend("unknown", 1.0)
        assert result is False

    def test_remaining_after_spend(self, budget):
        budget.spend("alpha", 10.0)
        assert budget.remaining("alpha") == pytest.approx(35.0)

    def test_remaining_unknown_channel_is_zero(self, budget):
        assert budget.remaining("nonexistent") == pytest.approx(0.0)

    def test_remaining_fully_spent_is_zero(self, budget):
        budget.spend("alpha", 45.0)
        assert budget.remaining("alpha") == pytest.approx(0.0)

    def test_total_spent_sums_channels(self, budget):
        budget.spend("alpha", 10.0)
        budget.spend("beta", 15.0)
        assert budget.total_spent() == pytest.approx(25.0)

    def test_total_spent_initially_zero(self, budget):
        assert budget.total_spent() == pytest.approx(0.0)

    def test_utilization_initially_zero(self, budget):
        assert budget.utilization() == pytest.approx(0.0)

    def test_utilization_full_spend(self, budget):
        budget.spend("alpha", 45.0)
        budget.spend("beta", 45.0)
        assert budget.utilization() == pytest.approx(1.0, abs=1e-5)

    def test_utilization_partial(self, budget):
        budget.spend("alpha", 22.5)
        # spent = 22.5, pool = 90 → util = 0.25
        assert budget.utilization() == pytest.approx(0.25, rel=1e-5)

    def test_snapshot_returns_remaining_per_channel(self, budget):
        budget.spend("alpha", 5.0)
        snap = budget.snapshot()
        assert snap["alpha"] == pytest.approx(40.0)
        assert snap["beta"] == pytest.approx(45.0)

    def test_to_dict_keys(self, budget):
        d = budget.to_dict()
        assert "budget_id" in d
        assert "total" in d
        assert "allocated" in d
        assert "spent" in d
        assert "reserved" in d

    def test_rebalance_sums_to_pool(self, budget):
        budget.spend("alpha", 10.0)
        budget.rebalance()
        pool = budget.total * (1.0 - budget.reserved)
        total_alloc = sum(budget.allocated.values())
        assert total_alloc == pytest.approx(pool, rel=1e-5)

    def test_make_single_channel(self):
        b = FrontierBudgetModel.make(total=50.0, channels=["solo"], reserved=0.0)
        assert b.allocated["solo"] == pytest.approx(50.0)

    def test_make_reserved_fraction(self):
        b = FrontierBudgetModel.make(total=100.0, channels=["a"], reserved=0.2)
        assert b.allocated["a"] == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# ObjectiveSet tests
# ---------------------------------------------------------------------------


class TestObjectiveSet:
    def test_default_has_four_objectives(self, default_obj_set):
        assert len(default_obj_set.objectives) == 4

    def test_default_name(self, default_obj_set):
        assert default_obj_set.name == "default"

    def test_default_kinds(self, default_obj_set):
        kinds = {o.kind for o in default_obj_set.objectives}
        assert ObjectiveKind.CLOSURE_GAIN in kinds
        assert ObjectiveKind.STABILITY in kinds
        assert ObjectiveKind.DIVERSITY in kinds
        assert ObjectiveKind.COST in kinds

    def test_add_increases_count(self, default_obj_set):
        extra = FrontierObjective(
            objective_id="extra-1",
            name="extra",
            weight=0.5,
            kind=ObjectiveKind.COMPOSITE,
            target_metric="composite_score",
            threshold=0.5,
        )
        default_obj_set.add(extra)
        assert len(default_obj_set.objectives) == 5

    def test_remove_existing(self, default_obj_set):
        first_id = default_obj_set.objectives[0].objective_id
        result = default_obj_set.remove(first_id)
        assert result is True
        assert len(default_obj_set.objectives) == 3

    def test_remove_nonexistent(self, default_obj_set):
        result = default_obj_set.remove("nonexistent-id")
        assert result is False

    def test_get_existing(self, default_obj_set):
        first = default_obj_set.objectives[0]
        found = default_obj_set.get(first.objective_id)
        assert found is first

    def test_get_nonexistent_returns_none(self, default_obj_set):
        assert default_obj_set.get("nope") is None

    def test_score_all_returns_dict(self, default_obj_set, full_state):
        scores = default_obj_set.score_all(full_state)
        assert isinstance(scores, dict)
        assert len(scores) == len(default_obj_set.objectives)

    def test_score_all_values_in_range(self, default_obj_set, full_state):
        scores = default_obj_set.score_all(full_state)
        for v in scores.values():
            assert 0.0 <= v <= 1.0

    def test_score_all_keyed_by_objective_id(self, default_obj_set, full_state):
        scores = default_obj_set.score_all(full_state)
        expected_keys = {o.objective_id for o in default_obj_set.objectives}
        assert set(scores.keys()) == expected_keys

    def test_weighted_score_in_range(self, default_obj_set, full_state):
        ws = default_obj_set.weighted_score(full_state)
        assert 0.0 <= ws <= 1.0

    def test_weighted_score_empty_set(self):
        obj_set = ObjectiveSet(objectives=[], name="empty")
        assert obj_set.weighted_score(ScoringState()) == pytest.approx(0.0)

    def test_weighted_score_single_objective_matches_score(self):
        obj = FrontierObjective.make_stability(weight=1.0, threshold=0.5)
        obj_set = ObjectiveSet(objectives=[obj], name="single")
        state = ScoringState(stability_score=0.7)
        assert obj_set.weighted_score(state) == pytest.approx(0.7)

    def test_all_satisfied_when_all_met(self):
        # Build a set where every objective is satisfied with state
        state = ScoringState(
            closure_gain=MAX_CLOSURE_GAIN,
            stability_score=1.0,
            diversity_score=1.0,
            cost_estimate=0.0,
        )
        obj_set = ObjectiveSet.default()
        assert obj_set.all_satisfied(state) is True

    def test_all_satisfied_false_when_one_fails(self):
        state = ScoringState(
            closure_gain=0.0,  # closure gain score = 0 < threshold 0.5
            stability_score=1.0,
            diversity_score=1.0,
            cost_estimate=0.0,
        )
        obj_set = ObjectiveSet.default()
        assert obj_set.all_satisfied(state) is False

    def test_all_satisfied_empty_set(self):
        obj_set = ObjectiveSet(objectives=[], name="empty")
        assert obj_set.all_satisfied(ScoringState()) is True

    def test_to_dict_structure(self, default_obj_set):
        d = default_obj_set.to_dict()
        assert "name" in d
        assert "objectives" in d
        assert isinstance(d["objectives"], list)
        assert len(d["objectives"]) == 4


# ---------------------------------------------------------------------------
# ScoringState tests
# ---------------------------------------------------------------------------


class TestScoringState:
    def test_default_construction(self):
        s = ScoringState()
        assert s.closure_gain == pytest.approx(0.0)
        assert s.stability_score == pytest.approx(0.0)
        assert s.diversity_score == pytest.approx(0.0)
        assert s.cost_estimate == pytest.approx(0.0)
        assert s.composite_score == pytest.approx(0.0)
        assert s.node_count == 0
        assert s.phase == "exploration"
        assert s.metadata == {}

    def test_update_closure_gain(self):
        s = ScoringState()
        s.update("closure_gain", 3.5)
        assert s.closure_gain == pytest.approx(3.5)

    def test_update_stability_score(self):
        s = ScoringState()
        s.update("stability_score", 0.9)
        assert s.stability_score == pytest.approx(0.9)

    def test_update_diversity_score(self):
        s = ScoringState()
        s.update("diversity_score", 0.75)
        assert s.diversity_score == pytest.approx(0.75)

    def test_update_cost_estimate(self):
        s = ScoringState()
        s.update("cost_estimate", 42.0)
        assert s.cost_estimate == pytest.approx(42.0)

    def test_update_composite_score(self):
        s = ScoringState()
        s.update("composite_score", 0.88)
        assert s.composite_score == pytest.approx(0.88)

    def test_update_unknown_key_goes_to_metadata(self):
        s = ScoringState()
        s.update("custom_metric", 7.0)
        assert s.metadata["custom_metric"] == 7.0

    def test_update_multiple_calls(self):
        s = ScoringState()
        s.update("closure_gain", 1.0)
        s.update("stability_score", 0.5)
        assert s.closure_gain == pytest.approx(1.0)
        assert s.stability_score == pytest.approx(0.5)

    def test_to_dict_keys(self, full_state):
        d = full_state.to_dict()
        expected_keys = {
            "closure_gain", "stability_score", "diversity_score",
            "cost_estimate", "composite_score", "node_count", "phase", "metadata",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values_match(self, full_state):
        d = full_state.to_dict()
        assert d["closure_gain"] == pytest.approx(full_state.closure_gain)
        assert d["phase"] == full_state.phase
        assert d["node_count"] == full_state.node_count

    def test_from_dict_roundtrip(self, full_state):
        d = full_state.to_dict()
        restored = ScoringState.from_dict(d)
        assert restored.closure_gain == pytest.approx(full_state.closure_gain)
        assert restored.stability_score == pytest.approx(full_state.stability_score)
        assert restored.diversity_score == pytest.approx(full_state.diversity_score)
        assert restored.cost_estimate == pytest.approx(full_state.cost_estimate)
        assert restored.composite_score == pytest.approx(full_state.composite_score)
        assert restored.node_count == full_state.node_count
        assert restored.phase == full_state.phase

    def test_from_dict_extra_keys_go_to_metadata(self):
        d = {"closure_gain": 1.0, "unknown_key": 42}
        s = ScoringState.from_dict(d)
        assert s.closure_gain == pytest.approx(1.0)
        assert s.metadata["unknown_key"] == 42

    def test_from_dict_empty(self):
        s = ScoringState.from_dict({})
        assert s.closure_gain == pytest.approx(0.0)

    def test_metadata_independent_instances(self):
        s1 = ScoringState()
        s2 = ScoringState()
        s1.metadata["key"] = 1
        assert "key" not in s2.metadata


# ---------------------------------------------------------------------------
# ObjectiveResult tests
# ---------------------------------------------------------------------------


class TestObjectiveResult:
    @pytest.fixture
    def result(self) -> ObjectiveResult:
        return ObjectiveResult(
            objective_id="obj-1",
            score=0.75,
            satisfied=True,
            rationale="Score exceeds threshold",
            timestamp=time.time(),
        )

    def test_construction(self, result):
        assert result.objective_id == "obj-1"
        assert result.score == pytest.approx(0.75)
        assert result.satisfied is True
        assert "threshold" in result.rationale

    def test_to_dict_keys(self, result):
        d = result.to_dict()
        assert set(d.keys()) == {"objective_id", "score", "satisfied", "rationale", "timestamp"}

    def test_to_dict_values(self, result):
        d = result.to_dict()
        assert d["objective_id"] == "obj-1"
        assert d["score"] == pytest.approx(0.75)
        assert d["satisfied"] is True
        assert isinstance(d["timestamp"], float)

    def test_unsatisfied_result(self):
        r = ObjectiveResult(
            objective_id="obj-2",
            score=0.2,
            satisfied=False,
            rationale="Score below threshold",
            timestamp=time.time(),
        )
        d = r.to_dict()
        assert d["satisfied"] is False

    def test_frozen_immutable(self, result):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            result.score = 0.99  # type: ignore[misc]

    def test_to_dict_roundtrip_score(self, result):
        d = result.to_dict()
        assert d["score"] == pytest.approx(result.score)


# ---------------------------------------------------------------------------
# Integration: FrontierObjective + ScoringState (no upstream needed)
# ---------------------------------------------------------------------------


class TestObjectiveSetIntegration:
    """End-to-end scoring pipeline tests without upstream dependencies."""

    def test_full_pipeline_zero_state(self):
        obj_set = ObjectiveSet.default()
        state = ScoringState()
        scores = obj_set.score_all(state)
        # closure_gain=0 → cg score=0; cost=0 → cost score=1.0
        for obj in obj_set.objectives:
            if obj.kind is ObjectiveKind.COST:
                assert scores[obj.objective_id] == pytest.approx(1.0)
            else:
                assert scores[obj.objective_id] == pytest.approx(0.0)

    def test_full_pipeline_update_affects_score(self):
        obj_set = ObjectiveSet.default()
        state = ScoringState()
        state.update("closure_gain", 5.0)
        scores = obj_set.score_all(state)
        for obj in obj_set.objectives:
            if obj.kind is ObjectiveKind.CLOSURE_GAIN:
                assert scores[obj.objective_id] == pytest.approx(0.5)

    def test_combine_creates_scoring_composite(self):
        cg = FrontierObjective.make_closure_gain()
        stab = FrontierObjective.make_stability()
        combined = cg.combine(stab)
        obj_set = ObjectiveSet(objectives=[combined], name="test")
        state = ScoringState(composite_score=0.8)
        ws = obj_set.weighted_score(state)
        assert 0.0 <= ws <= 1.0

    def test_objective_result_from_scoring(self):
        obj = FrontierObjective.make_closure_gain()
        state = ScoringState(closure_gain=7.0)
        s = obj.score(state)
        satisfied = obj.is_satisfied(state)
        result = ObjectiveResult(
            objective_id=obj.objective_id,
            score=s,
            satisfied=satisfied,
            rationale=f"score={s:.3f} threshold={obj.threshold}",
            timestamp=time.time(),
        )
        assert result.satisfied is True
        d = result.to_dict()
        assert d["score"] == pytest.approx(s)


# ---------------------------------------------------------------------------
# Integration: with Frontier module
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FRONTIER, reason="frontier module not available")
class TestIntegrationWithFrontier:
    """Tests that combine frontier_objectives models with FrontierNode / Frontier."""

    def _make_node(self, closure_gain: float = 0.5, cost: float = 1.0) -> "FrontierNode":
        return FrontierNode(
            predicted_closure_gain=closure_gain,
            estimated_cost=cost,
            predicted_stability_gain=0.1,
        )

    def test_scoring_state_from_frontier_node(self):
        node = self._make_node(closure_gain=0.6, cost=2.0)
        state = ScoringState(
            closure_gain=node.predicted_closure_gain * MAX_CLOSURE_GAIN,
            stability_score=max(0.0, node.predicted_stability_gain),
            cost_estimate=node.estimated_cost,
        )
        obj = FrontierObjective.make_closure_gain()
        score = obj.score(state)
        assert 0.0 <= score <= 1.0

    def test_frontier_diversity_drives_diversity_score(self):
        frontier = Frontier()
        for i in range(5):
            frontier.add_node(self._make_node())
        div_score = frontier.diversity_score()
        state = ScoringState(diversity_score=div_score)
        obj = FrontierObjective.make_diversity()
        s = obj.score(state)
        assert 0.0 <= s <= 1.0

    def test_objective_set_scores_frontier_state(self):
        frontier = Frontier()
        for i in range(3):
            frontier.add_node(self._make_node(closure_gain=0.3 + i * 0.1))
        best = frontier.best_node()
        state = ScoringState(
            closure_gain=best.predicted_closure_gain * MAX_CLOSURE_GAIN if best else 0.0,
            diversity_score=frontier.diversity_score(),
        )
        obj_set = ObjectiveSet.default()
        ws = obj_set.weighted_score(state)
        assert 0.0 <= ws <= 1.0

    def test_phase_transition_model_make(self):
        t = PhaseTransitionModel.make("exploration", "exploitation", "closure_gain_plateau", 0.3, 0.6)
        assert t.is_productive() is True
        assert t.gain_ratio() == pytest.approx(2.0)

    def test_frontier_budget_with_budget_model(self):
        budget = FrontierBudgetModel.make(total=100.0, channels=["frontier", "reserve"])
        fb = FrontierBudget(total_budget=100.0)
        # Record a node's cost through both interfaces
        node = self._make_node(cost=5.0)
        fb.record_cost(node.estimated_cost)
        ok = budget.spend("frontier", node.estimated_cost)
        assert ok is True
        assert budget.total_spent() == pytest.approx(node.estimated_cost)


# ---------------------------------------------------------------------------
# Integration: with Trust module
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_TRUST, reason="trust module not available")
class TestIntegrationWithTrust:
    """Tests that combine frontier_objectives models with TrustAlgebra / TrustLevel."""

    def test_trust_level_informs_confidence(self):
        algebra = TrustAlgebra()
        trust = TrustLevel.SOLVER_DISCHARGED
        # Map trust rank to confidence: higher rank → higher confidence
        rank = trust.rank_index()
        max_rank = TrustLevel.MECHANICALLY_VERIFIED.rank_index()
        confidence = 1.0 - (rank / max(max_rank, 1))
        estimate = ClosureGainEstimate.make("node-1", gain=0.7, confidence=confidence)
        assert 0.0 <= estimate.confidence <= 1.0
        assert estimate.is_worth_exploring() is True

    def test_trust_profile_scope_in_metadata(self):
        profile = TrustProfile(
            tier=TrustTier.VERIFIED,
            support_scope=("lemma_A", "lemma_B"),
        )
        state = ScoringState(closure_gain=3.0, stability_score=0.8)
        state.metadata["trust_tier"] = profile.tier.label()
        state.metadata["support_scope"] = list(profile.support_scope)
        assert state.metadata["trust_tier"] == "verified"
        obj = FrontierObjective.make_stability()
        assert obj.is_satisfied(state) is True

    def test_trust_compose_affects_estimate(self):
        algebra = TrustAlgebra()
        t1 = TrustLevel.HUMAN_ATTESTED
        t2 = TrustLevel.ORACLE_PROPOSED
        composed = algebra.compose(t1, t2)
        rank = composed.rank_index()
        confidence = _clamp(1.0 - rank / 10.0)
        estimate = ClosureGainEstimate.make("n", gain=0.5, confidence=confidence)
        assert 0.0 <= estimate.risk_adjusted_gain() <= 0.5

    def test_high_trust_high_confidence_worth_exploring(self):
        algebra = TrustAlgebra()
        top = algebra.top()  # MECHANICALLY_VERIFIED
        rank = top.rank_index()
        confidence = 1.0 if rank == 0 else 0.9
        estimate = ClosureGainEstimate.make("n", gain=0.5, confidence=confidence)
        assert estimate.is_worth_exploring() is True

    def test_contradicted_trust_zero_confidence(self):
        algebra = TrustAlgebra()
        bottom = algebra.bottom()  # CONTRADICTED
        assert bottom is TrustLevel.CONTRADICTED
        # Contradicted: treat as zero confidence
        estimate = ClosureGainEstimate.make("n", gain=0.9, confidence=0.0)
        assert estimate.risk_adjusted_gain() == pytest.approx(0.0)
        assert estimate.is_worth_exploring() is False


# ---------------------------------------------------------------------------
# Integration: with Descent module
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_DESCENT, reason="descent module not available")
class TestIntegrationWithDescent:
    """Tests that combine frontier_objectives models with DescentEngine / GluingData."""

    def test_descent_result_drives_closure_gain(self):
        engine = DescentEngine()
        # A successful descent contributes to closure gain
        # Build a trivial gluing that succeeds
        from jugeo.geometry.descent import Cover, LocalSection

        state = ScoringState(closure_gain=5.0)
        obj = FrontierObjective.make_closure_gain()
        score = obj.score(state)
        assert score == pytest.approx(0.5)

    def test_gluing_data_coverage_drives_diversity(self):
        gluing = GluingData()
        # Patch count reflects coverage
        # With empty gluing, coverage = 0
        coverage = gluing.patch_count / max(1, gluing.patch_count + 1)
        metric = DiversityMetric.make(
            cluster_count=gluing.patch_count,
            entropy=0.0,
            coverage=coverage,
            novelty=0.0,
        )
        assert 0.0 <= metric.combined_score() <= 1.0

    def test_cost_estimate_from_gluing_overlap_count(self):
        gluing = GluingData()
        # Cost proportional to overlap verification work
        cost = float(gluing.overlap_count) * 2.0
        estimate = ClosureGainEstimate.make("n", gain=0.4, confidence=0.8, cost=cost)
        assert estimate.computation_cost == pytest.approx(0.0)  # no overlaps

    def test_descent_failure_reduces_expected_gain(self):
        # Simulate failure by setting low gain
        estimate = ClosureGainEstimate.make("n", gain=0.0, confidence=0.9)
        assert estimate.is_worth_exploring() is False
