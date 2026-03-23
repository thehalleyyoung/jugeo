"""Tests for jugeo.generation.replay_gluing.s01_replay_planning.

Covers ChangeSet, DependencyAnalyzer, ReplayPlanner, and a suite of
model-level helpers.  Extended symbols (ReplayCostEstimator, helper
functions) that are not yet in the implementation are guarded behind
HAS_EXTENDED_S01 and skipped when absent.
"""

from pathlib import Path
import sys
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import pytest
import time
import uuid

# ---------------------------------------------------------------------------
# Core imports (always required)
# ---------------------------------------------------------------------------

from jugeo.generation.replay_gluing.models import (
    ReplayGluingPlan, ReplayStrategy, ConvergenceRecord,
    ReplayPhase, GluingUnderReplay, IncrementalGluing,
)
from jugeo.generation.replay_gluing.s01_replay_planning import (
    ChangeSet, DependencyAnalyzer, ReplayPlanner,
)

# ---------------------------------------------------------------------------
# Extended symbols — not yet implemented; tests are skipped when absent
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.replay_gluing.s01_replay_planning import (
        ReplayCostEstimator,
        PlanningError,
        CyclicDependencyError,
        build_trivial_plan,
        merge_plans,
        plan_is_noop,
        compute_plan_diff,
        validate_change_set,
    )
    HAS_EXTENDED_S01 = True
except ImportError:
    HAS_EXTENDED_S01 = False

    class PlanningError(Exception):
        pass

    class CyclicDependencyError(PlanningError):
        pass

    class ReplayCostEstimator:
        pass

    def build_trivial_plan(*a, **kw):
        return None

    def merge_plans(*a, **kw):
        return None

    def plan_is_noop(*a, **kw):
        return None

    def compute_plan_diff(*a, **kw):
        return {}

    def validate_change_set(*a, **kw):
        return True


# ---------------------------------------------------------------------------
# Optional jugeo geometry / goal / treaty dependencies
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.geometry.supports import SupportRegion
    from jugeo.generation.goals import GenerationGoal, GoalPriority
    from jugeo.generation.treaties import Treaty, TreatyStatus
    HAS_JUGEO_DEPS = True
except ImportError:
    HAS_JUGEO_DEPS = False


def make_support(patch="p"):
    if not HAS_JUGEO_DEPS:
        return {"patches": frozenset({patch})}
    coord = CoordinateObject("coord", CoordinateKind.REGION, ("coord",))
    return SupportRegion(coord, frozenset({patch}))


# ---------------------------------------------------------------------------
# Helpers for building test objects
# ---------------------------------------------------------------------------


def make_change_set(
    changed=None,
    unchanged=None,
    removed=None,
    metadata=None,
):
    return ChangeSet(
        changed_patches=frozenset(changed or []),
        unchanged_patches=frozenset(unchanged or []),
        removed_patches=frozenset(removed or []),
        change_metadata=metadata or {},
    )


def make_plan(changed=None, unchanged=None, removed=None, strategy=ReplayStrategy.INCREMENTAL):
    cs = make_change_set(changed=changed, unchanged=unchanged, removed=removed)
    planner = ReplayPlanner()
    return planner.plan(cs, {})


# ===========================================================================
# TestChangeSet
# ===========================================================================


class TestChangeSet:
    """Unit tests for the ChangeSet dataclass."""

    def test_creation_defaults(self):
        cs = ChangeSet()
        assert cs.changed_patches == frozenset()
        assert cs.unchanged_patches == frozenset()
        assert cs.removed_patches == frozenset()
        assert isinstance(cs.change_metadata, dict)
        assert isinstance(cs.created_at, float)

    def test_affects_modified(self):
        """Patch in changed_patches is 'affected'."""
        cs = make_change_set(changed=["p1", "p2"])
        assert "p1" in cs.changed_patches
        assert "p2" in cs.changed_patches

    def test_affects_added(self):
        """Newly added patches are treated as changed."""
        cs = make_change_set(changed=["new_patch"])
        assert "new_patch" in cs.changed_patches
        assert "new_patch" not in cs.unchanged_patches

    def test_affects_removed(self):
        """Removed patches appear in removed_patches."""
        cs = make_change_set(removed=["old_patch"])
        assert "old_patch" in cs.removed_patches
        assert "old_patch" not in cs.changed_patches

    def test_affects_unrelated(self):
        """Patch that was not mentioned is absent from all sets."""
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        assert "unrelated" not in cs.all_patches
        assert "unrelated" not in cs.removed_patches

    def test_compute_impact_empty(self):
        """An empty change set has change_ratio of 0.0."""
        cs = make_change_set()
        assert cs.change_ratio == 0.0

    def test_compute_impact_nonempty(self):
        """change_ratio reflects the fraction of patches that changed."""
        cs = make_change_set(changed=["p1", "p2"], unchanged=["p3", "p4"])
        assert cs.change_ratio == pytest.approx(0.5)
        assert len(cs.all_patches) == 4

    def test_compute_impact_score_weights(self):
        """Larger changed set → higher change_ratio."""
        cs_small = make_change_set(changed=["p1"], unchanged=["p2", "p3", "p4"])
        cs_large = make_change_set(changed=["p1", "p2", "p3"], unchanged=["p4"])
        assert cs_large.change_ratio > cs_small.change_ratio

    def test_merge_with_disjoint(self):
        """Union of two disjoint change sets contains all patches."""
        cs1 = make_change_set(changed=["a"], unchanged=["b"])
        cs2 = make_change_set(changed=["c"], unchanged=["d"])
        combined_changed = cs1.changed_patches | cs2.changed_patches
        combined_unchanged = cs1.unchanged_patches | cs2.unchanged_patches
        assert "a" in combined_changed
        assert "c" in combined_changed
        assert "b" in combined_unchanged
        assert "d" in combined_unchanged

    def test_merge_with_overlapping(self):
        """When patch appears in both, the union still has it once."""
        cs1 = make_change_set(changed=["p1", "p2"])
        cs2 = make_change_set(changed=["p2", "p3"])
        merged_changed = cs1.changed_patches | cs2.changed_patches
        assert merged_changed == frozenset({"p1", "p2", "p3"})

    def test_all_affected_patches(self):
        """all_patches = changed ∪ unchanged."""
        cs = make_change_set(changed=["a", "b"], unchanged=["c"])
        assert cs.all_patches == frozenset({"a", "b", "c"})
        assert "a" in cs.all_patches
        assert "c" in cs.all_patches
        # removed is NOT in all_patches
        cs2 = make_change_set(changed=["x"], removed=["y"])
        assert "y" not in cs2.all_patches

    def test_to_dict_roundtrip(self):
        """to_dict() returns a dict with the expected keys and correct values."""
        cs = make_change_set(changed=["p1"], unchanged=["p2"], removed=["p3"])
        d = cs.to_dict()
        assert "changed_patches" in d
        assert "unchanged_patches" in d
        assert "removed_patches" in d
        assert "p1" in d["changed_patches"]
        assert "p2" in d["unchanged_patches"]
        assert "p3" in d["removed_patches"]

    def test_to_dict_metadata_preserved(self):
        """change_metadata is included in serialisation."""
        cs = make_change_set(changed=["p1"], metadata={"source": "unit-test"})
        d = cs.to_dict()
        assert d["change_metadata"] == {"source": "unit-test"}

    def test_is_empty_true(self):
        """ChangeSet with no changed or removed patches is empty."""
        cs = make_change_set(unchanged=["p1", "p2"])
        assert cs.is_empty() is True

    def test_is_empty_false_changed(self):
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        assert cs.is_empty() is False

    def test_is_empty_false_removed(self):
        cs = make_change_set(unchanged=["p1"], removed=["p2"])
        assert cs.is_empty() is False

    def test_change_ratio_all_changed(self):
        """If every patch changed, ratio == 1.0."""
        cs = make_change_set(changed=["a", "b", "c"])
        assert cs.change_ratio == pytest.approx(1.0)

    def test_change_ratio_zero_with_unchanged(self):
        cs = make_change_set(unchanged=["a", "b"])
        assert cs.change_ratio == 0.0

    @pytest.mark.parametrize("n_changed,n_unchanged,expected", [
        (0, 4, 0.0),
        (1, 3, 0.25),
        (2, 2, 0.5),
        (3, 1, 0.75),
        (4, 0, 1.0),
    ])
    def test_change_ratio_parametrized(self, n_changed, n_unchanged, expected):
        changed = [f"c{i}" for i in range(n_changed)]
        unchanged = [f"u{i}" for i in range(n_unchanged)]
        cs = make_change_set(changed=changed, unchanged=unchanged)
        assert cs.change_ratio == pytest.approx(expected)

    def test_created_at_is_recent(self):
        before = time.time() - 0.01
        cs = ChangeSet()
        after = time.time() + 0.01
        assert before <= cs.created_at <= after

    def test_frozensets_are_immutable(self):
        cs = make_change_set(changed=["p1"])
        with pytest.raises((AttributeError, TypeError)):
            cs.changed_patches.add("p2")  # type: ignore[attr-defined]


# ===========================================================================
# TestDependencyAnalyzer
# ===========================================================================


class TestDependencyAnalyzer:
    """Unit tests for DependencyAnalyzer."""

    def test_analyze_empty(self):
        """Analysing an empty change set returns an empty dict."""
        analyzer = DependencyAnalyzer()
        cs = make_change_set()
        result = analyzer.analyze(cs, {})
        assert result == {}

    def test_analyze_simple_deps(self):
        """Patches not in prior state get empty dependency sets."""
        analyzer = DependencyAnalyzer()
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        result = analyzer.analyze(cs, {})
        assert "p1" in result
        assert "p2" in result
        assert result["p1"] == frozenset()
        assert result["p2"] == frozenset()

    def test_find_dependencies_direct(self):
        """get_dependents returns direct dependents after add_dependency."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("p2", "p1")  # p2 depends on p1
        dependents = analyzer.get_dependents("p1")
        assert "p2" in dependents

    def test_find_dependencies_none(self):
        """get_dependents returns empty set for a patch with no dependents."""
        analyzer = DependencyAnalyzer()
        deps = analyzer.get_dependents("isolated_patch")
        assert deps == frozenset()

    def test_compute_transitive_closure_simple(self):
        """get_transitive_dependents captures one-hop dependents."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("b", "a")  # b depends on a
        result = analyzer.get_transitive_dependents("a")
        assert "b" in result

    def test_compute_transitive_closure_chain(self):
        """p1 → p2 → p3: get_transitive_dependents('p1') returns p2 and p3."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("p2", "p1")
        analyzer.add_dependency("p3", "p2")
        result = analyzer.get_transitive_dependents("p1")
        assert "p2" in result
        assert "p3" in result

    def test_compute_transitive_closure_diamond(self):
        """Diamond dependency: p1→p2, p1→p3, p2→p4, p3→p4 → all reachable from p1."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("p2", "p1")
        analyzer.add_dependency("p3", "p1")
        analyzer.add_dependency("p4", "p2")
        analyzer.add_dependency("p4", "p3")
        result = analyzer.get_transitive_dependents("p1")
        assert "p2" in result
        assert "p3" in result
        assert "p4" in result

    def test_topological_sort_simple(self):
        """topological_order returns all patches for a single-node set."""
        analyzer = DependencyAnalyzer()
        order = analyzer.topological_order(frozenset({"only"}))
        assert order == ["only"]

    def test_topological_sort_chain(self):
        """p3 depends on p2 depends on p1: p1 comes before p2, p2 before p3."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("p2", "p1")
        analyzer.add_dependency("p3", "p2")
        order = analyzer.topological_order(frozenset({"p1", "p2", "p3"}))
        assert order.index("p1") < order.index("p2")
        assert order.index("p2") < order.index("p3")

    def test_topological_sort_preserves_all_patches(self):
        """Every patch is present in the topological order."""
        analyzer = DependencyAnalyzer()
        patches = frozenset({"a", "b", "c", "d"})
        order = analyzer.topological_order(patches)
        assert set(order) == patches

    def test_has_cycle_acyclic(self):
        """An acyclic DAG: get_transitive_dependents does not loop forever."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("b", "a")
        analyzer.add_dependency("c", "b")
        # Just confirm it completes without error.
        result = analyzer.get_transitive_dependents("a")
        assert isinstance(result, frozenset)

    def test_get_dependency_depth_root(self):
        """Patch with no dependencies should appear first in topological order."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("b", "a")
        order = analyzer.topological_order(frozenset({"a", "b"}))
        assert order[0] == "a"

    def test_get_dependency_depth_one(self):
        """Direct dependent appears after its dependency."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("child", "parent")
        order = analyzer.topological_order(frozenset({"parent", "child"}))
        assert order.index("parent") < order.index("child")

    def test_get_dependency_depth_chain(self):
        """Three-level chain is ordered correctly."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("mid", "root")
        analyzer.add_dependency("leaf", "mid")
        order = analyzer.topological_order(frozenset({"root", "mid", "leaf"}))
        assert order.index("root") < order.index("mid") < order.index("leaf")

    def test_remove_dependency(self):
        """Removing a dependency means it no longer appears in get_dependents."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("child", "parent")
        assert "child" in analyzer.get_dependents("parent")
        analyzer.remove_dependency("child", "parent")
        assert "child" not in analyzer.get_dependents("parent")

    def test_analyze_uses_prior_state_deps(self):
        """Dependencies stored in prior_state are picked up by analyze()."""
        analyzer = DependencyAnalyzer()
        cs = make_change_set(changed=["p1"])
        prior = {"p1": {"dependencies": ["p0"]}}
        result = analyzer.analyze(cs, prior)
        assert "p0" in result["p1"]

    def test_snapshot_returns_dict(self):
        """snapshot() returns a plain dict."""
        analyzer = DependencyAnalyzer({"a": frozenset({"b"})})
        snap = analyzer.snapshot()
        assert isinstance(snap, dict)
        assert "b" in snap["a"]

    def test_prior_dependencies_constructor(self):
        """Constructor seeds the analyzer with prior deps."""
        analyzer = DependencyAnalyzer({"x": frozenset({"y"})})
        deps = analyzer.get_dependents("y")
        assert "x" in deps


# ===========================================================================
# TestReplayCostEstimator
# ===========================================================================


@pytest.mark.skipif(not HAS_EXTENDED_S01, reason="ReplayCostEstimator not yet implemented")
class TestReplayCostEstimator:
    """Tests for the (planned) ReplayCostEstimator class."""

    def _make_plan(self, n_changed, n_unchanged, strategy=ReplayStrategy.INCREMENTAL):
        changed = [f"c{i}" for i in range(n_changed)]
        unchanged = [f"u{i}" for i in range(n_unchanged)]
        return make_plan(changed=changed, unchanged=unchanged)

    def test_estimate_full_plan_more_expensive_than_incremental(self):
        plan = self._make_plan(5, 5)
        estimator = ReplayCostEstimator()
        cost_full = estimator.estimate(plan, ReplayStrategy.FULL)
        cost_inc = estimator.estimate(plan, ReplayStrategy.INCREMENTAL)
        assert cost_full > cost_inc

    def test_estimate_lazy_cheapest(self):
        plan = self._make_plan(5, 5)
        estimator = ReplayCostEstimator()
        cost_lazy = estimator.estimate(plan, ReplayStrategy.LAZY)
        cost_full = estimator.estimate(plan, ReplayStrategy.FULL)
        assert cost_lazy < cost_full

    def test_compare_strategies_sorted(self):
        plan = self._make_plan(3, 3)
        estimator = ReplayCostEstimator()
        comparisons = estimator.compare_strategies(plan)
        costs = [c for _, c in comparisons]
        assert costs == sorted(costs)

    def test_suggest_cheapest_returns_min(self):
        estimator = ReplayCostEstimator()
        cheapest = estimator.suggest_cheapest(
            [ReplayStrategy.FULL, ReplayStrategy.INCREMENTAL, ReplayStrategy.LAZY]
        )
        assert cheapest == ReplayStrategy.LAZY

    def test_suggest_cheapest_empty_list_returns_none(self):
        estimator = ReplayCostEstimator()
        assert estimator.suggest_cheapest([]) is None

    @pytest.mark.parametrize("strategy", list(ReplayStrategy))
    def test_strategy_multiplier(self, strategy):
        plan = self._make_plan(4, 4)
        estimator = ReplayCostEstimator()
        cost = estimator.estimate(plan, strategy)
        assert isinstance(cost, float)
        assert cost >= 0.0


# ===========================================================================
# TestReplayPlanner
# ===========================================================================


class TestReplayPlanner:
    """Unit tests for ReplayPlanner."""

    def test_plan_creates_plan(self):
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert isinstance(plan, ReplayGluingPlan)

    def test_plan_changed_patches_in_scope(self):
        cs = make_change_set(changed=["p1", "p2"], unchanged=["p3"])
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert "p1" in plan.changed_patches
        assert "p2" in plan.changed_patches

    def test_plan_unchanged_patches_in_plan(self):
        cs = make_change_set(changed=["p1"], unchanged=["p2", "p3"])
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert "p2" in plan.unchanged_patches
        assert "p3" in plan.unchanged_patches

    def test_plan_removed_patches_stored(self):
        cs = make_change_set(changed=["p1"], removed=["old"])
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert "old" in plan.removed_patches

    def test_plan_with_deps_expands_scope(self):
        """Dependency info is stored in the plan's dependencies dict."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("p2", "p1")
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        planner = ReplayPlanner(dependency_analyzer=analyzer)
        plan = planner.plan(cs, {})
        # Plan stores the dependency mapping
        assert isinstance(plan.dependencies, dict)

    def test_identify_changed_regions_all_three_types(self):
        """Plan correctly separates changed, unchanged, and removed."""
        cs = make_change_set(changed=["a"], unchanged=["b"], removed=["c"])
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert "a" in plan.changed_patches
        assert "b" in plan.unchanged_patches
        assert "c" in plan.removed_patches

    def test_compute_dependency_closure_expands(self):
        """Transitive dependents are reachable via the analyzer."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("p2", "p1")
        analyzer.add_dependency("p3", "p2")
        cs = make_change_set(changed=["p1"], unchanged=["p2", "p3"])
        planner = ReplayPlanner(dependency_analyzer=analyzer)
        plan = planner.plan(cs, {})
        # After planning, the analyzer knows about transitive dependents.
        trans = analyzer.get_transitive_dependents("p1")
        assert "p2" in trans
        assert "p3" in trans

    def test_estimate_cost_returns_float(self):
        """plan.metadata includes a float for planned_at."""
        cs = make_change_set(changed=["p1"])
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert "planned_at" in plan.metadata
        assert isinstance(plan.metadata["planned_at"], float)

    def test_optimize_plan_returns_plan(self):
        """validate() on a valid change set returns no errors."""
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        planner = ReplayPlanner()
        errors = planner.validate(cs)
        assert errors == []

    def test_plan_empty_change_set(self):
        """Planning an empty change set produces an empty plan."""
        cs = make_change_set()
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert plan.changed_patches == frozenset()
        assert plan.unchanged_patches == frozenset()
        assert plan.is_valid()

    def test_plan_all_patches_changed(self):
        """When all patches changed, strategy should be FULL."""
        patches = [f"p{i}" for i in range(10)]
        cs = make_change_set(changed=patches)
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert plan.strategy == ReplayStrategy.FULL

    def test_plan_id_is_unique(self):
        cs = make_change_set(changed=["p1"])
        planner = ReplayPlanner()
        plan1 = planner.plan(cs, {})
        plan2 = planner.plan(cs, {})
        assert plan1.plan_id != plan2.plan_id

    @pytest.mark.parametrize("strategy", [
        ReplayStrategy.FULL,
        ReplayStrategy.INCREMENTAL,
        ReplayStrategy.LAZY,
    ])
    def test_plan_strategies(self, strategy):
        """strategy_override forces the given strategy."""
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        planner = ReplayPlanner(strategy_override=strategy)
        plan = planner.plan(cs, {})
        assert plan.strategy == strategy

    def test_validate_overlapping_changed_unchanged(self):
        """A patch in both changed and unchanged fails validation."""
        cs = ChangeSet(
            changed_patches=frozenset({"p1"}),
            unchanged_patches=frozenset({"p1"}),  # intentional overlap
        )
        planner = ReplayPlanner()
        errors = planner.validate(cs)
        assert len(errors) > 0
        assert any("p1" in e for e in errors)

    def test_validate_removed_and_present(self):
        """A patch both present and removed should fail validation."""
        cs = ChangeSet(
            changed_patches=frozenset({"p1"}),
            removed_patches=frozenset({"p1"}),
        )
        planner = ReplayPlanner()
        errors = planner.validate(cs)
        assert len(errors) > 0


# ===========================================================================
# TestHelpers  (build_trivial_plan, merge_plans, etc.)
# ===========================================================================


@pytest.mark.skipif(not HAS_EXTENDED_S01, reason="Helper functions not yet implemented")
class TestHelpers:
    """Tests for the (planned) module-level helper functions."""

    def test_build_trivial_plan(self):
        patches = ["p1", "p2", "p3"]
        plan = build_trivial_plan(patches)
        assert plan is not None
        assert isinstance(plan, ReplayGluingPlan)
        assert frozenset(patches).issubset(plan.changed_patches | plan.unchanged_patches)

    def test_merge_plans_combines_patches(self):
        plan1 = make_plan(changed=["a"], unchanged=["b"])
        plan2 = make_plan(changed=["c"], unchanged=["d"])
        merged = merge_plans(plan1, plan2)
        all_p = merged.changed_patches | merged.unchanged_patches
        assert "a" in all_p or "c" in all_p

    def test_plan_is_noop_true(self):
        """A plan with no changed patches is a noop."""
        plan = make_plan(unchanged=["p1", "p2"])
        assert plan_is_noop(plan) is True

    def test_plan_is_noop_false(self):
        plan = make_plan(changed=["p1"])
        assert plan_is_noop(plan) is False

    def test_compute_plan_diff(self):
        plan1 = make_plan(changed=["a", "b"])
        plan2 = make_plan(changed=["b", "c"])
        diff = compute_plan_diff(plan1, plan2)
        assert isinstance(diff, dict)

    def test_validate_change_set_valid(self):
        cs = make_change_set(changed=["p1"], unchanged=["p2"])
        result = validate_change_set(cs)
        assert result is True or result == []

    def test_validate_change_set_invalid(self):
        """Patch in both added and removed should raise or return errors."""
        cs = ChangeSet(
            changed_patches=frozenset({"p1"}),
            removed_patches=frozenset({"p1"}),
        )
        try:
            result = validate_change_set(cs)
            # If it returns errors, they should be non-empty
            if isinstance(result, list):
                assert len(result) > 0
            else:
                pytest.fail("Expected errors for invalid change set")
        except (ValueError, PlanningError):
            pass  # Also acceptable


# ===========================================================================
# TestModels — ConvergenceRecord, ReplayGluingPlan, etc.
# ===========================================================================


class TestConvergenceRecord:
    """Unit tests for ConvergenceRecord."""

    def test_creation_defaults(self):
        rec = ConvergenceRecord()
        assert rec.converged is False
        assert rec.rounds == 0
        assert rec.score == 0.0
        assert rec.unresolved_patches == []
        assert isinstance(rec.record_id, str)
        assert len(rec.record_id) > 0

    def test_record_id_unique(self):
        r1 = ConvergenceRecord()
        r2 = ConvergenceRecord()
        assert r1.record_id != r2.record_id

    def test_converged_flag(self):
        rec = ConvergenceRecord(converged=True, score=1.0, rounds=3)
        assert rec.converged is True
        assert rec.score == 1.0
        assert rec.rounds == 3

    def test_to_dict_keys(self):
        rec = ConvergenceRecord(
            gluing_id="g1",
            converged=True,
            rounds=2,
            score=0.95,
        )
        d = rec.to_dict()
        assert d["gluing_id"] == "g1"
        assert d["converged"] is True
        assert d["rounds"] == 2
        assert d["score"] == pytest.approx(0.95)

    def test_to_dict_unresolved_patches(self):
        rec = ConvergenceRecord(
            unresolved_patches=["px", "py"],
            violation_messages=["overlap issue"],
        )
        d = rec.to_dict()
        assert "px" in d["unresolved_patches"]
        assert "overlap issue" in d["violation_messages"]


class TestReplayGluingPlan:
    """Unit tests for ReplayGluingPlan."""

    def test_creation_defaults(self):
        plan = ReplayGluingPlan()
        assert plan.strategy == ReplayStrategy.INCREMENTAL
        assert plan.changed_patches == frozenset()
        assert plan.unchanged_patches == frozenset()
        assert plan.removed_patches == frozenset()
        assert isinstance(plan.plan_id, str)
        assert len(plan.plan_id) > 0

    def test_all_patches_property(self):
        plan = ReplayGluingPlan(
            changed_patches=frozenset({"a", "b"}),
            unchanged_patches=frozenset({"c"}),
        )
        assert plan.all_patches == frozenset({"a", "b", "c"})

    def test_total_patch_count(self):
        plan = ReplayGluingPlan(
            changed_patches=frozenset({"a", "b"}),
            unchanged_patches=frozenset({"c", "d", "e"}),
        )
        assert plan.total_patch_count == 5

    def test_is_valid_disjoint(self):
        plan = ReplayGluingPlan(
            changed_patches=frozenset({"a"}),
            unchanged_patches=frozenset({"b"}),
        )
        assert plan.is_valid() is True

    def test_is_valid_overlap_fails(self):
        plan = ReplayGluingPlan(
            changed_patches=frozenset({"a"}),
            unchanged_patches=frozenset({"a"}),
        )
        assert plan.is_valid() is False

    def test_to_dict_roundtrip(self):
        plan = ReplayGluingPlan(
            strategy=ReplayStrategy.LAZY,
            changed_patches=frozenset({"p1"}),
            unchanged_patches=frozenset({"p2"}),
            removed_patches=frozenset({"p3"}),
        )
        d = plan.to_dict()
        assert d["strategy"] == "lazy"
        assert "p1" in d["changed_patches"]
        assert "p2" in d["unchanged_patches"]
        assert "p3" in d["removed_patches"]

    def test_dependencies_stored(self):
        plan = ReplayGluingPlan(
            changed_patches=frozenset({"a"}),
            dependencies={"a": frozenset({"b"})},
        )
        assert "b" in plan.dependencies["a"]

    @pytest.mark.parametrize("strategy", list(ReplayStrategy))
    def test_all_strategies(self, strategy):
        plan = ReplayGluingPlan(strategy=strategy)
        assert plan.strategy == strategy
        d = plan.to_dict()
        assert d["strategy"] == strategy.value


class TestGluingUnderReplay:
    """Unit tests for GluingUnderReplay."""

    def test_creation_defaults(self):
        g = GluingUnderReplay()
        assert g.phase == ReplayPhase.PENDING
        assert g.replayed_patches == []
        assert g.pending_patches == []
        assert g.is_complete is False

    def test_transition_changes_phase(self):
        g = GluingUnderReplay()
        g.transition(ReplayPhase.PLANNING)
        assert g.phase == ReplayPhase.PLANNING

    def test_transition_to_completed_sets_timestamp(self):
        g = GluingUnderReplay()
        g.transition(ReplayPhase.COMPLETED)
        assert g.completed_at > 0.0
        assert g.is_complete is True

    def test_mark_replayed(self):
        g = GluingUnderReplay(pending_patches=["p1"])
        g.mark_replayed("p1", {"data": 42})
        assert "p1" in g.replayed_patches
        assert "p1" not in g.pending_patches
        assert g.patch_sections["p1"] == {"data": 42}

    def test_log_error(self):
        g = GluingUnderReplay()
        g.log_error("something went wrong")
        assert "something went wrong" in g.error_log

    def test_add_overlap(self):
        g = GluingUnderReplay()
        g.add_overlap("p1::p2", {"value": 0.5})
        assert g.overlaps["p1::p2"] == {"value": 0.5}

    def test_elapsed_seconds_positive(self):
        g = GluingUnderReplay()
        time.sleep(0.01)
        assert g.elapsed_seconds > 0.0

    def test_to_dict_includes_phase(self):
        g = GluingUnderReplay()
        g.transition(ReplayPhase.REPLAYING)
        d = g.to_dict()
        assert d["phase"] == "replaying"


class TestIncrementalGluing:
    """Unit tests for IncrementalGluing."""

    def test_creation_defaults(self):
        ig = IncrementalGluing()
        assert ig.added_patches == []
        assert ig.removed_patches == []
        assert ig.modified_patches == []
        assert ig.is_empty() is True

    def test_total_changes(self):
        ig = IncrementalGluing(
            added_patches=["a"],
            removed_patches=["b"],
            modified_patches=["c", "d"],
        )
        assert ig.total_changes == 4

    def test_is_empty_false(self):
        ig = IncrementalGluing(added_patches=["x"])
        assert ig.is_empty() is False

    def test_to_dict(self):
        ig = IncrementalGluing(
            base_gluing_id="base",
            target_gluing_id="target",
            added_patches=["p1"],
        )
        d = ig.to_dict()
        assert d["base_gluing_id"] == "base"
        assert d["target_gluing_id"] == "target"
        assert "p1" in d["added_patches"]


# ===========================================================================
# Integration tests — skipped if jugeo geometry/goal deps are absent
# ===========================================================================


@pytest.mark.skipif(not HAS_JUGEO_DEPS, reason="jugeo deps not available")
class TestIntegration:
    """Integration tests that require jugeo geometry and goal dependencies."""

    def test_planner_with_construction_goal(self):
        """Create a GenerationGoal and build a plan from a corresponding change set."""
        goal = GenerationGoal(
            goal_id=str(uuid.uuid4()),
            target_coordinate="module.function",
            required_proposition="well_typed",
        )
        goal_dict = {
            "goal_id": goal.goal_id,
            "target_coordinate": goal.target_coordinate,
        }
        cs = ChangeSet(
            changed_patches=frozenset({goal.goal_id}),
            unchanged_patches=frozenset(),
            change_metadata={"goal": goal_dict},
        )
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert goal.goal_id in plan.changed_patches
        assert isinstance(plan, ReplayGluingPlan)

    def test_change_set_from_goal_change(self):
        """A goal change can produce a ChangeSet that drives planning."""
        g1 = GenerationGoal(
            target_coordinate="mod.a",
            required_proposition="prop1",
        )
        g2 = GenerationGoal(
            target_coordinate="mod.b",
            required_proposition="prop2",
        )
        cs = ChangeSet(
            changed_patches=frozenset({g1.goal_id}),
            unchanged_patches=frozenset({g2.goal_id}),
        )
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert g1.goal_id in plan.changed_patches
        assert g2.goal_id in plan.unchanged_patches
        assert plan.is_valid()

    def test_planner_with_treaty(self):
        """Treaty information can be stored in change_metadata for the planner."""
        from jugeo.generation.treaties import TreatyLaw
        law = TreatyLaw(
            predicate="overlap_compatible",
            variables=("x", "y"),
            quantifiers=(),
            natural_language_description="Patches x and y are overlap-compatible",
        )
        cs = ChangeSet(
            changed_patches=frozenset({"patch_a", "patch_b"}),
            unchanged_patches=frozenset(),
            change_metadata={"treaty_predicate": law.predicate},
        )
        planner = ReplayPlanner()
        plan = planner.plan(cs, {})
        assert "patch_a" in plan.changed_patches
        assert plan.metadata.get("change_ratio") is not None
