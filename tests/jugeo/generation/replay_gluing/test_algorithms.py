"""Tests for jugeo.generation.replay_gluing.algorithms.

Covers:
    - ReplayAlgorithm ABC and all three concrete implementations
    - ChangeImpactAnalyzer (analyze, blast_radius, rank, severity)
    - GluingMerger (merge, resolve_conflict, verify_merge_coherence)
    - ReplayTask dataclass and ReplayScheduler
    - AlgorithmRegistry (get, get_for_strategy, register, list_names)
    - Module-level helpers: select_algorithm, run_algorithm, DEFAULT_ALGORITHM
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import uuid

from jugeo.generation.replay_gluing.models import (
    ReplayGluingPlan,
    GluingUnderReplay,
    ReplayStrategy,
    ReplayPhase,
)
from jugeo.generation.replay_gluing.s01_replay_planning import ChangeSet
from jugeo.generation.replay_gluing.algorithms import (
    ReplayAlgorithm,
    FullReplayAlgorithm,
    IncrementalReplayAlgorithm,
    LazyReplayAlgorithm,
    ChangeImpactAnalyzer,
    GluingMerger,
    ReplayTask,
    ReplayScheduler,
    AlgorithmRegistry,
    select_algorithm,
    run_algorithm,
    DEFAULT_ALGORITHM,
)

try:
    from jugeo.generation.replay_gluing.s02_incremental_replay import (
        GluingSnapshot,
        ReplayCache,
    )
    HAS_S02 = True
except ImportError:
    HAS_S02 = False

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.geometry.supports import SupportRegion
    HAS_JUGEO_DEPS = True
except ImportError:
    HAS_JUGEO_DEPS = False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def make_plan(
    changed: tuple[str, ...] = ("patch_alpha",),
    unchanged: tuple[str, ...] = ("patch_beta", "patch_gamma"),
    strategy: ReplayStrategy = ReplayStrategy.INCREMENTAL,
) -> ReplayGluingPlan:
    """Return a minimal but valid ReplayGluingPlan for use in tests."""
    return ReplayGluingPlan(
        strategy=strategy,
        changed_patches=frozenset(changed),
        unchanged_patches=frozenset(unchanged),
    )


def make_change_set(
    changed: tuple[str, ...] = ("patch_alpha",),
    unchanged: tuple[str, ...] = ("patch_beta",),
    removed: tuple[str, ...] = (),
) -> ChangeSet:
    """Return a ChangeSet for use in ChangeImpactAnalyzer tests."""
    return ChangeSet(
        changed_patches=frozenset(changed),
        unchanged_patches=frozenset(unchanged),
        removed_patches=frozenset(removed),
    )


# ---------------------------------------------------------------------------
# TestReplayAlgorithmABC
# ---------------------------------------------------------------------------


class TestReplayAlgorithmABC:
    """Verify that all concrete algorithms conform to the ReplayAlgorithm ABC."""

    def test_full_is_replay_algorithm(self):
        """FullReplayAlgorithm must be a ReplayAlgorithm subclass."""
        algo = FullReplayAlgorithm()
        assert isinstance(algo, ReplayAlgorithm)

    def test_incremental_is_replay_algorithm(self):
        """IncrementalReplayAlgorithm must be a ReplayAlgorithm subclass."""
        algo = IncrementalReplayAlgorithm()
        assert isinstance(algo, ReplayAlgorithm)

    def test_lazy_is_replay_algorithm(self):
        """LazyReplayAlgorithm must be a ReplayAlgorithm subclass."""
        algo = LazyReplayAlgorithm()
        assert isinstance(algo, ReplayAlgorithm)

    def test_get_name_full(self):
        """FullReplayAlgorithm reports name 'full'."""
        assert FullReplayAlgorithm().get_name() == "full"

    def test_get_name_incremental(self):
        """IncrementalReplayAlgorithm reports name 'incremental'."""
        assert IncrementalReplayAlgorithm().get_name() == "incremental"

    def test_get_name_lazy(self):
        """LazyReplayAlgorithm reports name 'lazy'."""
        assert LazyReplayAlgorithm().get_name() == "lazy"

    def test_pre_check_valid_plan(self):
        """pre_check returns an empty list for a non-overlapping plan."""
        algo = FullReplayAlgorithm()
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        errors = algo.pre_check(plan)
        assert isinstance(errors, list), "pre_check must return a list"
        assert len(errors) == 0, f"Expected no errors, got: {errors}"

    def test_pre_check_incremental_valid_plan(self):
        """pre_check on IncrementalReplayAlgorithm also returns empty list."""
        algo = IncrementalReplayAlgorithm()
        plan = make_plan(
            changed=("section_gamma",),
            unchanged=("section_delta", "section_epsilon"),
        )
        errors = algo.pre_check(plan)
        assert isinstance(errors, list)
        assert errors == []

    def test_pre_check_lazy_valid_plan(self):
        """pre_check on LazyReplayAlgorithm returns empty list for clean plan."""
        algo = LazyReplayAlgorithm()
        plan = make_plan(
            changed=("patch_one",),
            unchanged=("patch_two",),
            strategy=ReplayStrategy.LAZY,
        )
        errors = algo.pre_check(plan)
        assert isinstance(errors, list)
        assert len(errors) == 0

    @pytest.mark.parametrize(
        "algo_cls,name",
        [
            (FullReplayAlgorithm, "full"),
            (IncrementalReplayAlgorithm, "incremental"),
            (LazyReplayAlgorithm, "lazy"),
        ],
    )
    def test_algorithm_names(self, algo_cls, name):
        """Parameterised check that each concrete class reports the expected name."""
        instance = algo_cls()
        assert instance.get_name() == name
        assert isinstance(instance.get_name(), str)
        assert len(instance.get_name()) > 0

    def test_pre_check_returns_list_of_strings(self):
        """pre_check must always return a list of strings (not None, not other type)."""
        for algo_cls in (FullReplayAlgorithm, IncrementalReplayAlgorithm, LazyReplayAlgorithm):
            algo = algo_cls()
            plan = make_plan()
            result = algo.pre_check(plan)
            assert isinstance(result, list)
            for item in result:
                assert isinstance(item, str)


# ---------------------------------------------------------------------------
# TestFullReplayAlgorithm
# ---------------------------------------------------------------------------


class TestFullReplayAlgorithm:
    """Unit tests for FullReplayAlgorithm."""

    def test_creation(self):
        """FullReplayAlgorithm instantiates without arguments."""
        algo = FullReplayAlgorithm()
        assert algo is not None

    def test_supports_strategy_full(self):
        """FullReplayAlgorithm supports FULL strategy."""
        assert FullReplayAlgorithm().supports_strategy(ReplayStrategy.FULL) is True

    def test_supports_strategy_incremental(self):
        """FullReplayAlgorithm does NOT support INCREMENTAL."""
        assert FullReplayAlgorithm().supports_strategy(ReplayStrategy.INCREMENTAL) is False

    def test_supports_strategy_lazy(self):
        """FullReplayAlgorithm does NOT support LAZY."""
        assert FullReplayAlgorithm().supports_strategy(ReplayStrategy.LAZY) is False

    def test_supports_strategy_adaptive(self):
        """FullReplayAlgorithm does NOT support ADAPTIVE."""
        assert FullReplayAlgorithm().supports_strategy(ReplayStrategy.ADAPTIVE) is False

    def test_execute_returns_gluing(self):
        """execute must return a GluingUnderReplay instance."""
        algo = FullReplayAlgorithm()
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
            strategy=ReplayStrategy.FULL,
        )
        result = algo.execute(plan)
        assert isinstance(result, GluingUnderReplay)

    def test_execute_all_patches_replayed(self):
        """All changed and unchanged patches should appear in replayed_patches."""
        algo = FullReplayAlgorithm()
        plan = make_plan(
            changed=("patch_alpha", "patch_beta"),
            unchanged=("section_gamma",),
            strategy=ReplayStrategy.FULL,
        )
        result = algo.execute(plan)
        assert set(result.replayed_patches) == {"patch_alpha", "patch_beta", "section_gamma"}

    def test_execute_phase_done(self):
        """After execute, the GluingUnderReplay phase must be COMPLETED."""
        algo = FullReplayAlgorithm()
        plan = make_plan(strategy=ReplayStrategy.FULL)
        result = algo.execute(plan)
        assert result.phase == ReplayPhase.COMPLETED

    def test_execute_empty_plan(self):
        """execute on an empty plan should succeed and produce COMPLETED phase."""
        algo = FullReplayAlgorithm()
        plan = ReplayGluingPlan(strategy=ReplayStrategy.FULL)
        result = algo.execute(plan)
        assert result.phase == ReplayPhase.COMPLETED
        assert result.replayed_patches == []

    def test_execute_patch_sections_populated(self):
        """patch_sections dict should contain an entry for every replayed patch."""
        algo = FullReplayAlgorithm()
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
            strategy=ReplayStrategy.FULL,
        )
        result = algo.execute(plan)
        assert "patch_alpha" in result.patch_sections
        assert "patch_beta" in result.patch_sections

    def test_execute_plan_linked(self):
        """The returned GluingUnderReplay should reference the original plan."""
        algo = FullReplayAlgorithm()
        plan = make_plan(strategy=ReplayStrategy.FULL)
        result = algo.execute(plan)
        assert result.plan is plan

    @pytest.mark.parametrize(
        "n_changed,n_unchanged",
        [(1, 0), (0, 3), (2, 2), (5, 5)],
    )
    def test_execute_various_patch_counts(self, n_changed, n_unchanged):
        """execute correctly handles varying numbers of changed/unchanged patches."""
        changed = {f"changed_{i}" for i in range(n_changed)}
        unchanged = {f"unchanged_{i}" for i in range(n_unchanged)}
        plan = ReplayGluingPlan(
            strategy=ReplayStrategy.FULL,
            changed_patches=frozenset(changed),
            unchanged_patches=frozenset(unchanged),
        )
        result = FullReplayAlgorithm().execute(plan)
        assert set(result.replayed_patches) == changed | unchanged
        assert result.phase == ReplayPhase.COMPLETED


# ---------------------------------------------------------------------------
# TestIncrementalReplayAlgorithm
# ---------------------------------------------------------------------------


class TestIncrementalReplayAlgorithm:
    """Unit tests for IncrementalReplayAlgorithm."""

    def test_creation(self):
        """IncrementalReplayAlgorithm instantiates without arguments."""
        algo = IncrementalReplayAlgorithm()
        assert algo is not None

    def test_supports_strategy_incremental(self):
        """IncrementalReplayAlgorithm supports INCREMENTAL."""
        assert IncrementalReplayAlgorithm().supports_strategy(ReplayStrategy.INCREMENTAL) is True

    def test_supports_strategy_full(self):
        """IncrementalReplayAlgorithm does NOT support FULL."""
        assert IncrementalReplayAlgorithm().supports_strategy(ReplayStrategy.FULL) is False

    def test_supports_strategy_adaptive(self):
        """IncrementalReplayAlgorithm also supports ADAPTIVE."""
        assert IncrementalReplayAlgorithm().supports_strategy(ReplayStrategy.ADAPTIVE) is True

    def test_supports_strategy_lazy(self):
        """IncrementalReplayAlgorithm does NOT support LAZY."""
        assert IncrementalReplayAlgorithm().supports_strategy(ReplayStrategy.LAZY) is False

    def test_execute_returns_gluing(self):
        """execute must return a GluingUnderReplay instance."""
        algo = IncrementalReplayAlgorithm()
        plan = make_plan()
        result = algo.execute(plan)
        assert isinstance(result, GluingUnderReplay)

    def test_execute_changed_patches_replayed_with_new_data(self):
        """Changed patches should have section data indicating they were re-processed."""
        algo = IncrementalReplayAlgorithm()
        plan = make_plan(changed=("patch_alpha", "patch_beta"), unchanged=("section_gamma",))
        result = algo.execute(plan)
        # Changed patches appear in patch_sections with changed=True
        assert "patch_alpha" in result.patch_sections
        assert result.patch_sections["patch_alpha"].get("changed") is True
        assert "patch_beta" in result.patch_sections
        assert result.patch_sections["patch_beta"].get("changed") is True

    def test_execute_unchanged_patches_cached(self):
        """Unchanged patches should be replayed with cached metadata."""
        algo = IncrementalReplayAlgorithm()
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta", "section_gamma"))
        result = algo.execute(plan)
        assert "patch_beta" in result.patch_sections
        assert result.patch_sections["patch_beta"].get("changed") is False
        assert "section_gamma" in result.patch_sections
        assert result.patch_sections["section_gamma"].get("changed") is False

    def test_execute_phase_done(self):
        """After execute, phase must be COMPLETED."""
        algo = IncrementalReplayAlgorithm()
        plan = make_plan()
        result = algo.execute(plan)
        assert result.phase == ReplayPhase.COMPLETED

    def test_execute_all_patches_present(self):
        """Both changed and unchanged patches must appear in replayed_patches."""
        algo = IncrementalReplayAlgorithm()
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta", "section_gamma"),
        )
        result = algo.execute(plan)
        replayed = set(result.replayed_patches)
        assert "patch_alpha" in replayed
        assert "patch_beta" in replayed
        assert "section_gamma" in replayed

    def test_execute_plan_referenced(self):
        """The returned GluingUnderReplay must reference the input plan."""
        algo = IncrementalReplayAlgorithm()
        plan = make_plan()
        result = algo.execute(plan)
        assert result.plan is plan

    @pytest.mark.parametrize(
        "changed,unchanged",
        [
            (("c1",), ("u1", "u2")),
            (("c1", "c2", "c3"), ()),
            ((), ("u1", "u2", "u3")),
        ],
    )
    def test_execute_section_keys_match_patches(self, changed, unchanged):
        """Every patch in the plan should have a corresponding patch_sections entry."""
        plan = ReplayGluingPlan(
            strategy=ReplayStrategy.INCREMENTAL,
            changed_patches=frozenset(changed),
            unchanged_patches=frozenset(unchanged),
        )
        result = IncrementalReplayAlgorithm().execute(plan)
        for p in changed:
            assert p in result.patch_sections
        for p in unchanged:
            assert p in result.patch_sections


# ---------------------------------------------------------------------------
# TestLazyReplayAlgorithm
# ---------------------------------------------------------------------------


class TestLazyReplayAlgorithm:
    """Unit tests for LazyReplayAlgorithm."""

    def test_creation(self):
        """LazyReplayAlgorithm instantiates without arguments."""
        assert LazyReplayAlgorithm() is not None

    def test_supports_strategy_lazy(self):
        """LazyReplayAlgorithm supports LAZY."""
        assert LazyReplayAlgorithm().supports_strategy(ReplayStrategy.LAZY) is True

    def test_supports_strategy_full(self):
        """LazyReplayAlgorithm does NOT support FULL."""
        assert LazyReplayAlgorithm().supports_strategy(ReplayStrategy.FULL) is False

    def test_supports_strategy_incremental(self):
        """LazyReplayAlgorithm does NOT support INCREMENTAL."""
        assert LazyReplayAlgorithm().supports_strategy(ReplayStrategy.INCREMENTAL) is False

    def test_execute_returns_gluing(self):
        """execute must return a GluingUnderReplay instance."""
        result = LazyReplayAlgorithm().execute(
            make_plan(strategy=ReplayStrategy.LAZY)
        )
        assert isinstance(result, GluingUnderReplay)

    def test_execute_minimal_work_changed_replayed(self):
        """Only changed patches should appear in replayed_patches."""
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta", "section_gamma"),
            strategy=ReplayStrategy.LAZY,
        )
        result = LazyReplayAlgorithm().execute(plan)
        assert "patch_alpha" in result.replayed_patches

    def test_execute_unchanged_deferred(self):
        """Unchanged patches should be placed in deferred_patches, not replayed."""
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta", "section_gamma"),
            strategy=ReplayStrategy.LAZY,
        )
        result = LazyReplayAlgorithm().execute(plan)
        # At least one unchanged patch must land in deferred_patches
        assert "patch_beta" in result.deferred_patches or "section_gamma" in result.deferred_patches

    def test_execute_phase_done(self):
        """After execute, phase must be COMPLETED."""
        result = LazyReplayAlgorithm().execute(make_plan(strategy=ReplayStrategy.LAZY))
        assert result.phase == ReplayPhase.COMPLETED

    def test_execute_no_unnecessary_replays(self):
        """Unchanged patches should NOT appear in replayed_patches for LAZY strategy."""
        plan = make_plan(
            changed=("patch_only_changed",),
            unchanged=("section_deferred_1", "section_deferred_2"),
            strategy=ReplayStrategy.LAZY,
        )
        result = LazyReplayAlgorithm().execute(plan)
        assert "section_deferred_1" not in result.replayed_patches
        assert "section_deferred_2" not in result.replayed_patches

    def test_execute_empty_changed_all_deferred(self):
        """When there are no changed patches, everything should end up deferred."""
        plan = make_plan(
            changed=(),
            unchanged=("patch_beta", "section_gamma"),
            strategy=ReplayStrategy.LAZY,
        )
        result = LazyReplayAlgorithm().execute(plan)
        assert result.replayed_patches == []
        assert set(result.deferred_patches) == {"patch_beta", "section_gamma"}

    def test_execute_plan_referenced(self):
        """The returned GluingUnderReplay must reference the input plan."""
        plan = make_plan(strategy=ReplayStrategy.LAZY)
        result = LazyReplayAlgorithm().execute(plan)
        assert result.plan is plan


# ---------------------------------------------------------------------------
# TestChangeImpactAnalyzer
# ---------------------------------------------------------------------------


class TestChangeImpactAnalyzer:
    """Unit tests for ChangeImpactAnalyzer."""

    def test_analyze_empty_change_set(self):
        """Analyzing an empty ChangeSet returns an empty dict."""
        analyzer = ChangeImpactAnalyzer()
        cs = ChangeSet()
        impacts = analyzer.analyze(cs)
        assert isinstance(impacts, dict)
        assert len(impacts) == 0

    def test_analyze_single_change(self):
        """A ChangeSet with one changed patch yields one impact entry."""
        analyzer = ChangeImpactAnalyzer()
        cs = ChangeSet(changed_patches=frozenset({"patch_alpha"}))
        impacts = analyzer.analyze(cs)
        assert "patch_alpha" in impacts
        assert isinstance(impacts["patch_alpha"], dict)

    def test_analyze_multiple_changes(self):
        """Impact dict should have an entry for each changed patch."""
        analyzer = ChangeImpactAnalyzer()
        cs = ChangeSet(
            changed_patches=frozenset({"patch_alpha", "patch_beta", "section_gamma"})
        )
        impacts = analyzer.analyze(cs)
        assert len(impacts) == 3
        assert "patch_alpha" in impacts
        assert "patch_beta" in impacts
        assert "section_gamma" in impacts

    def test_analyze_removed_patches_included(self):
        """Removed patches should also appear in the impact analysis."""
        analyzer = ChangeImpactAnalyzer()
        cs = ChangeSet(
            changed_patches=frozenset({"patch_alpha"}),
            removed_patches=frozenset({"patch_removed"}),
        )
        impacts = analyzer.analyze(cs)
        assert "patch_removed" in impacts

    def test_analyze_severity_field_present(self):
        """Each impact entry must contain a 'severity' key with a positive int."""
        analyzer = ChangeImpactAnalyzer()
        cs = ChangeSet(changed_patches=frozenset({"patch_alpha"}))
        impacts = analyzer.analyze(cs)
        assert "severity" in impacts["patch_alpha"]
        assert isinstance(impacts["patch_alpha"]["severity"], int)
        assert impacts["patch_alpha"]["severity"] >= 1

    def test_compute_blast_radius_isolated(self):
        """A patch with no dependents has blast radius 0."""
        analyzer = ChangeImpactAnalyzer()
        deps: dict[str, frozenset[str]] = {
            "patch_alpha": frozenset(),
            "patch_beta": frozenset(),
        }
        radius = analyzer.compute_blast_radius("patch_alpha", deps)
        assert isinstance(radius, int)
        assert radius == 0

    def test_compute_blast_radius_one_dependent(self):
        """A patch with one direct dependent has blast radius >= 1."""
        analyzer = ChangeImpactAnalyzer()
        deps: dict[str, frozenset[str]] = {
            "patch_beta": frozenset({"patch_alpha"}),  # patch_beta depends on patch_alpha
        }
        radius = analyzer.compute_blast_radius("patch_alpha", deps)
        assert radius >= 1

    def test_compute_blast_radius_chain(self):
        """Transitively chained dependencies should all count toward blast radius."""
        analyzer = ChangeImpactAnalyzer()
        deps: dict[str, frozenset[str]] = {
            "patch_beta": frozenset({"patch_alpha"}),
            "section_gamma": frozenset({"patch_beta"}),
        }
        radius = analyzer.compute_blast_radius("patch_alpha", deps)
        assert radius >= 1  # patch_beta and section_gamma both transitively depend on patch_alpha

    def test_rank_by_impact_sorted_desc(self):
        """rank_by_impact must return patches sorted by descending severity."""
        analyzer = ChangeImpactAnalyzer()
        impacts = {
            "patch_low": {"severity": 1},
            "patch_high": {"severity": 3},
            "patch_mid": {"severity": 2},
        }
        ranked = analyzer.rank_by_impact(impacts)
        assert ranked[0] == "patch_high"
        assert ranked[-1] == "patch_low"
        assert len(ranked) == 3

    def test_rank_by_impact_empty(self):
        """rank_by_impact on an empty dict returns an empty list."""
        analyzer = ChangeImpactAnalyzer()
        assert analyzer.rank_by_impact({}) == []

    def test_rank_by_impact_returns_list(self):
        """rank_by_impact must return a list of patch name strings."""
        analyzer = ChangeImpactAnalyzer()
        impacts = {"patch_alpha": {"severity": 2}}
        result = analyzer.rank_by_impact(impacts)
        assert isinstance(result, list)
        assert result == ["patch_alpha"]

    def test_severity_removed_highest(self):
        """'removed' must score higher than 'modified'."""
        analyzer = ChangeImpactAnalyzer()
        assert analyzer.severity("removed") > analyzer.severity("modified")

    def test_severity_added_higher_than_modified(self):
        """'added' must score higher than 'modified'."""
        analyzer = ChangeImpactAnalyzer()
        assert analyzer.severity("added") > analyzer.severity("modified")

    def test_severity_removed_highest_of_all(self):
        """'removed' must score at least as high as 'added'."""
        analyzer = ChangeImpactAnalyzer()
        assert analyzer.severity("removed") >= analyzer.severity("added")

    @pytest.mark.parametrize(
        "change_type,expected_ge",
        [
            ("removed", 3),
            ("added", 2),
            ("modified", 1),
        ],
    )
    def test_impact_analysis_change_types(self, change_type, expected_ge):
        """severity returns values that meet minimum thresholds for each type."""
        analyzer = ChangeImpactAnalyzer()
        assert analyzer.severity(change_type) >= expected_ge

    def test_severity_unknown_type_defaults_to_one(self):
        """Unknown change types should default to severity 1 (not crash)."""
        analyzer = ChangeImpactAnalyzer()
        result = analyzer.severity("unknown_type")
        assert isinstance(result, int)
        assert result >= 1


# ---------------------------------------------------------------------------
# TestGluingMerger
# ---------------------------------------------------------------------------


class TestGluingMerger:
    """Unit tests for GluingMerger."""

    def test_merge_disjoint(self):
        """Merging two GluingUnderReplay objects with distinct patches includes both."""
        merger = GluingMerger()
        p1 = make_plan(changed=("patch_alpha",), unchanged=())
        p2 = make_plan(changed=("patch_beta",), unchanged=())
        g1 = GluingUnderReplay(plan=p1)
        g1.mark_replayed("patch_alpha", {"data": 1})
        g2 = GluingUnderReplay(plan=p2)
        g2.mark_replayed("patch_beta", {"data": 2})
        merged = merger.merge(g1, g2)
        assert "patch_alpha" in merged.patch_sections
        assert "patch_beta" in merged.patch_sections

    def test_merge_overlapping_g2_wins(self):
        """When both gluings have the same patch, g2's data takes precedence."""
        merger = GluingMerger()
        plan = make_plan(changed=("patch_alpha",), unchanged=())
        g1 = GluingUnderReplay(plan=plan)
        g1.mark_replayed("patch_alpha", {"v": "old_value"})
        g2 = GluingUnderReplay(plan=plan)
        g2.mark_replayed("patch_alpha", {"v": "new_value"})
        merged = merger.merge(g1, g2)
        assert merged.patch_sections["patch_alpha"]["v"] == "new_value"

    def test_merge_returns_gluing_under_replay(self):
        """merge must return a GluingUnderReplay instance."""
        merger = GluingMerger()
        plan = make_plan()
        g1 = GluingUnderReplay(plan=plan)
        g2 = GluingUnderReplay(plan=plan)
        merged = merger.merge(g1, g2)
        assert isinstance(merged, GluingUnderReplay)

    def test_resolve_conflict_dicts_merge(self):
        """resolve_conflict merges two dicts, with d2 overwriting shared keys."""
        merger = GluingMerger()
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 99, "c": 3}
        result = merger.resolve_conflict(d1, d2)
        assert isinstance(result, dict)
        assert result["b"] == 99   # d2 wins on conflict
        assert result["a"] == 1    # d1 value preserved when no conflict
        assert result["c"] == 3    # d2-only key present

    def test_resolve_conflict_primitives_d2_wins(self):
        """For non-dict primitives, resolve_conflict always returns d2."""
        merger = GluingMerger()
        assert merger.resolve_conflict("old", "new") == "new"
        assert merger.resolve_conflict(1, 42) == 42
        assert merger.resolve_conflict(None, "value") == "value"

    def test_resolve_conflict_both_none(self):
        """resolve_conflict with two Nones returns None (d2)."""
        merger = GluingMerger()
        assert merger.resolve_conflict(None, None) is None

    def test_verify_merge_coherence_valid(self):
        """A gluing with no duplicates passes coherence verification."""
        merger = GluingMerger()
        plan = make_plan()
        g = GluingUnderReplay(plan=plan)
        g.mark_replayed("patch_alpha", {"v": 1})
        errors = merger.verify_merge_coherence(g)
        assert isinstance(errors, list)
        assert errors == []

    def test_verify_merge_coherence_duplicate(self):
        """Duplicate entries in replayed_patches should be flagged as errors."""
        merger = GluingMerger()
        plan = make_plan()
        g = GluingUnderReplay(plan=plan)
        g.replayed_patches = ["patch_alpha", "patch_beta", "patch_alpha"]  # duplicate
        errors = merger.verify_merge_coherence(g)
        assert len(errors) > 0
        assert any("patch_alpha" in e for e in errors)

    @pytest.mark.parametrize("n", [1, 5, 10])
    def test_merge_various_sizes(self, n):
        """merge handles plans of varying patch counts without error."""
        merger = GluingMerger()
        plan1 = ReplayGluingPlan(
            changed_patches=frozenset(f"alpha_{i}" for i in range(n))
        )
        plan2 = ReplayGluingPlan(
            changed_patches=frozenset(f"beta_{i}" for i in range(n))
        )
        g1 = GluingUnderReplay(plan=plan1)
        g2 = GluingUnderReplay(plan=plan2)
        for i in range(n):
            g1.mark_replayed(f"alpha_{i}", {"idx": i})
            g2.mark_replayed(f"beta_{i}", {"idx": i})
        merged = merger.merge(g1, g2)
        assert isinstance(merged, GluingUnderReplay)
        assert len(merged.patch_sections) == 2 * n

    def test_merge_empty_gluings(self):
        """Merging two empty GluingUnderReplay objects yields an empty merged object."""
        merger = GluingMerger()
        plan = make_plan()
        g1 = GluingUnderReplay(plan=plan)
        g2 = GluingUnderReplay(plan=plan)
        merged = merger.merge(g1, g2)
        assert isinstance(merged, GluingUnderReplay)
        assert merged.patch_sections == {}


# ---------------------------------------------------------------------------
# TestReplayTask
# ---------------------------------------------------------------------------


class TestReplayTask:
    """Unit tests for the ReplayTask dataclass."""

    def test_default_construction(self):
        """ReplayTask can be created with all defaults."""
        task = ReplayTask()
        assert task.patch == ""
        assert task.is_changed is True
        assert task.priority == 0
        assert task.dependencies == frozenset()
        assert task.estimated_ms == pytest.approx(10.0)
        assert task.status == "pending"

    def test_custom_construction(self):
        """ReplayTask fields are set correctly when passed explicitly."""
        task = ReplayTask(
            patch="patch_alpha",
            is_changed=False,
            priority=7,
            dependencies=frozenset({"patch_beta"}),
            estimated_ms=25.0,
            status="in_progress",
        )
        assert task.patch == "patch_alpha"
        assert task.is_changed is False
        assert task.priority == 7
        assert "patch_beta" in task.dependencies
        assert task.estimated_ms == pytest.approx(25.0)
        assert task.status == "in_progress"

    def test_task_id_auto_generated(self):
        """Two ReplayTask instances should receive distinct task_ids."""
        t1 = ReplayTask()
        t2 = ReplayTask()
        assert t1.task_id != t2.task_id

    def test_task_id_is_string(self):
        """task_id must be a non-empty string."""
        task = ReplayTask()
        assert isinstance(task.task_id, str)
        assert len(task.task_id) > 0

    def test_dependencies_is_frozenset(self):
        """dependencies field must be a frozenset."""
        task = ReplayTask(dependencies=frozenset({"dep_one", "dep_two"}))
        assert isinstance(task.dependencies, frozenset)
        assert "dep_one" in task.dependencies


# ---------------------------------------------------------------------------
# TestReplayScheduler
# ---------------------------------------------------------------------------


class TestReplayScheduler:
    """Unit tests for ReplayScheduler."""

    def test_schedule_returns_list_of_tasks(self):
        """schedule must return a list of ReplayTask instances."""
        sched = ReplayScheduler()
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        tasks = sched.schedule(plan)
        assert isinstance(tasks, list)
        assert all(isinstance(t, ReplayTask) for t in tasks)

    def test_schedule_total_task_count(self):
        """schedule should produce one task per patch (changed + unchanged)."""
        sched = ReplayScheduler()
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
        )
        tasks = sched.schedule(plan)
        assert len(tasks) == 2

    def test_schedule_changed_has_higher_priority(self):
        """Changed patch tasks must have higher priority than unchanged ones."""
        sched = ReplayScheduler()
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        tasks = sched.schedule(plan)
        changed_tasks = [t for t in tasks if t.is_changed]
        unchanged_tasks = [t for t in tasks if not t.is_changed]
        assert len(changed_tasks) == 1
        assert len(unchanged_tasks) == 1
        assert changed_tasks[0].priority > unchanged_tasks[0].priority

    def test_schedule_marks_is_changed(self):
        """Changed patches are marked is_changed=True; unchanged is_changed=False."""
        sched = ReplayScheduler()
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        tasks = sched.schedule(plan)
        by_patch = {t.patch: t for t in tasks}
        assert by_patch["patch_alpha"].is_changed is True
        assert by_patch["patch_beta"].is_changed is False

    def test_get_ready_tasks_no_deps(self):
        """Tasks with empty dependencies are immediately ready."""
        sched = ReplayScheduler()
        tasks = [
            ReplayTask(patch="patch_alpha", dependencies=frozenset()),
            ReplayTask(patch="patch_beta", dependencies=frozenset()),
        ]
        ready = sched.get_ready_tasks(tasks, completed=set())
        assert len(ready) == 2

    def test_get_ready_tasks_with_unmet_dep(self):
        """A task whose dependency has not been completed is NOT ready."""
        sched = ReplayScheduler()
        tasks = [
            ReplayTask(patch="patch_alpha", dependencies=frozenset()),
            ReplayTask(patch="patch_beta", dependencies=frozenset({"patch_alpha"})),
        ]
        ready = sched.get_ready_tasks(tasks, completed=set())
        assert len(ready) == 1
        assert ready[0].patch == "patch_alpha"

    def test_get_ready_tasks_with_met_dep(self):
        """Once a dependency is completed, the dependent task becomes ready."""
        sched = ReplayScheduler()
        tasks = [
            ReplayTask(patch="patch_alpha", dependencies=frozenset()),
            ReplayTask(patch="patch_beta", dependencies=frozenset({"patch_alpha"})),
        ]
        ready = sched.get_ready_tasks(tasks, completed={"patch_alpha"})
        patches = {t.patch for t in ready}
        assert "patch_beta" in patches

    def test_estimate_completion_time_sum(self):
        """estimate_completion_time must return sum of estimated_ms across tasks."""
        sched = ReplayScheduler()
        tasks = [
            ReplayTask(estimated_ms=10.0),
            ReplayTask(estimated_ms=20.0),
            ReplayTask(estimated_ms=5.0),
        ]
        total = sched.estimate_completion_time(tasks)
        assert total == pytest.approx(35.0)

    def test_estimate_completion_time_empty(self):
        """estimate_completion_time returns 0.0 for an empty task list."""
        sched = ReplayScheduler()
        assert sched.estimate_completion_time([]) == pytest.approx(0.0)

    def test_schedule_empty_plan(self):
        """schedule on an empty plan returns an empty list."""
        sched = ReplayScheduler()
        plan = ReplayGluingPlan()
        tasks = sched.schedule(plan)
        assert isinstance(tasks, list)
        assert len(tasks) == 0

    @pytest.mark.parametrize(
        "n_changed,n_unchanged",
        [(1, 1), (3, 0), (0, 3), (5, 5)],
    )
    def test_schedule_various_plans(self, n_changed, n_unchanged):
        """schedule handles a variety of changed/unchanged patch configurations."""
        sched = ReplayScheduler()
        plan = ReplayGluingPlan(
            changed_patches=frozenset(f"changed_{i}" for i in range(n_changed)),
            unchanged_patches=frozenset(f"unchanged_{i}" for i in range(n_unchanged)),
        )
        tasks = sched.schedule(plan)
        assert len(tasks) == n_changed + n_unchanged
        assert all(isinstance(t, ReplayTask) for t in tasks)

    def test_get_ready_tasks_excludes_non_pending(self):
        """Tasks not in 'pending' status are never returned as ready."""
        sched = ReplayScheduler()
        tasks = [
            ReplayTask(patch="patch_alpha", dependencies=frozenset(), status="done"),
            ReplayTask(patch="patch_beta", dependencies=frozenset(), status="pending"),
        ]
        ready = sched.get_ready_tasks(tasks, completed=set())
        assert len(ready) == 1
        assert ready[0].patch == "patch_beta"


# ---------------------------------------------------------------------------
# TestAlgorithmRegistry
# ---------------------------------------------------------------------------


class TestAlgorithmRegistry:
    """Unit tests for AlgorithmRegistry."""

    def test_default_algorithms_registered(self):
        """A freshly created registry contains 'full', 'incremental', and 'lazy'."""
        reg = AlgorithmRegistry()
        names = reg.list_names()
        assert "full" in names
        assert "incremental" in names
        assert "lazy" in names

    def test_get_existing_full(self):
        """get('full') returns a FullReplayAlgorithm instance."""
        reg = AlgorithmRegistry()
        algo = reg.get("full")
        assert algo is not None
        assert isinstance(algo, FullReplayAlgorithm)

    def test_get_existing_incremental(self):
        """get('incremental') returns an IncrementalReplayAlgorithm instance."""
        reg = AlgorithmRegistry()
        algo = reg.get("incremental")
        assert algo is not None
        assert isinstance(algo, IncrementalReplayAlgorithm)

    def test_get_existing_lazy(self):
        """get('lazy') returns a LazyReplayAlgorithm instance."""
        reg = AlgorithmRegistry()
        algo = reg.get("lazy")
        assert algo is not None
        assert isinstance(algo, LazyReplayAlgorithm)

    def test_get_nonexistent_returns_none(self):
        """get on an unknown name must return None, not raise."""
        reg = AlgorithmRegistry()
        result = reg.get("nonexistent_algorithm")
        assert result is None

    def test_get_for_strategy_full(self):
        """get_for_strategy(FULL) returns an algorithm supporting FULL."""
        reg = AlgorithmRegistry()
        algo = reg.get_for_strategy(ReplayStrategy.FULL)
        assert algo is not None
        assert algo.get_name() == "full"
        assert algo.supports_strategy(ReplayStrategy.FULL)

    def test_get_for_strategy_incremental(self):
        """get_for_strategy(INCREMENTAL) returns an algorithm supporting INCREMENTAL."""
        reg = AlgorithmRegistry()
        algo = reg.get_for_strategy(ReplayStrategy.INCREMENTAL)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.INCREMENTAL)

    def test_get_for_strategy_lazy(self):
        """get_for_strategy(LAZY) returns an algorithm supporting LAZY."""
        reg = AlgorithmRegistry()
        algo = reg.get_for_strategy(ReplayStrategy.LAZY)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.LAZY)

    def test_get_for_strategy_adaptive(self):
        """get_for_strategy(ADAPTIVE) returns an algorithm supporting ADAPTIVE."""
        reg = AlgorithmRegistry()
        algo = reg.get_for_strategy(ReplayStrategy.ADAPTIVE)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.ADAPTIVE)

    def test_register_new_algorithm(self):
        """register adds an algorithm so it is retrievable via get."""
        reg = AlgorithmRegistry()
        new_algo = FullReplayAlgorithm()
        reg.register(new_algo)
        retrieved = reg.get("full")
        assert retrieved is not None
        assert retrieved.get_name() == "full"

    def test_register_custom_algorithm(self):
        """A custom algorithm subclass can be registered and retrieved."""

        class CustomAlgo(ReplayAlgorithm):
            def get_name(self) -> str:
                return "custom_test_algo"

            def supports_strategy(self, strategy: ReplayStrategy) -> bool:
                return False

            def execute(self, plan: ReplayGluingPlan) -> GluingUnderReplay:
                return GluingUnderReplay(plan=plan)

        reg = AlgorithmRegistry()
        reg.register(CustomAlgo())
        assert reg.get("custom_test_algo") is not None
        assert isinstance(reg.get("custom_test_algo"), CustomAlgo)

    def test_list_names_nonempty(self):
        """list_names must return at least the three built-in algorithm names."""
        reg = AlgorithmRegistry()
        names = reg.list_names()
        assert isinstance(names, list)
        assert len(names) >= 3

    def test_list_names_all_strings(self):
        """Every name returned by list_names must be a non-empty string."""
        reg = AlgorithmRegistry()
        for name in reg.list_names():
            assert isinstance(name, str)
            assert len(name) > 0

    def test_registry_is_independent(self):
        """Two AlgorithmRegistry instances are independent; registering in one
        does not affect the other."""
        reg1 = AlgorithmRegistry()
        reg2 = AlgorithmRegistry()

        class TempAlgo(ReplayAlgorithm):
            def get_name(self) -> str:
                return "temp_algo_isolated"

            def supports_strategy(self, strategy: ReplayStrategy) -> bool:
                return False

            def execute(self, plan: ReplayGluingPlan) -> GluingUnderReplay:
                return GluingUnderReplay(plan=plan)

        reg1.register(TempAlgo())
        assert reg1.get("temp_algo_isolated") is not None
        assert reg2.get("temp_algo_isolated") is None


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for module-level helper functions and constants."""

    def test_select_algorithm_full_strategy(self):
        """select_algorithm(FULL) returns an algorithm that supports FULL."""
        algo = select_algorithm(ReplayStrategy.FULL)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.FULL)

    def test_select_algorithm_incremental_strategy(self):
        """select_algorithm(INCREMENTAL) returns an algorithm supporting INCREMENTAL."""
        algo = select_algorithm(ReplayStrategy.INCREMENTAL)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.INCREMENTAL)

    def test_select_algorithm_lazy_strategy(self):
        """select_algorithm(LAZY) returns an algorithm that supports LAZY."""
        algo = select_algorithm(ReplayStrategy.LAZY)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.LAZY)

    def test_select_algorithm_adaptive_strategy(self):
        """select_algorithm(ADAPTIVE) returns an algorithm that supports ADAPTIVE."""
        algo = select_algorithm(ReplayStrategy.ADAPTIVE)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.ADAPTIVE)

    def test_select_algorithm_returns_replay_algorithm(self):
        """select_algorithm always returns a ReplayAlgorithm instance."""
        for strategy in ReplayStrategy:
            algo = select_algorithm(strategy)
            assert isinstance(algo, ReplayAlgorithm)

    def test_run_algorithm_returns_gluing(self):
        """run_algorithm must return a GluingUnderReplay instance."""
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        result = run_algorithm(plan)
        assert isinstance(result, GluingUnderReplay)

    def test_run_algorithm_phase_completed(self):
        """run_algorithm must produce a COMPLETED phase gluing."""
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        result = run_algorithm(plan)
        assert result.phase == ReplayPhase.COMPLETED

    def test_run_algorithm_full_strategy(self):
        """run_algorithm with FULL strategy replays all patches."""
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
            strategy=ReplayStrategy.FULL,
        )
        result = run_algorithm(plan)
        assert isinstance(result, GluingUnderReplay)
        assert set(result.replayed_patches) == {"patch_alpha", "patch_beta"}

    def test_run_algorithm_lazy_strategy(self):
        """run_algorithm with LAZY strategy defers unchanged patches."""
        plan = make_plan(
            changed=("patch_alpha",),
            unchanged=("patch_beta", "section_gamma"),
            strategy=ReplayStrategy.LAZY,
        )
        result = run_algorithm(plan)
        assert isinstance(result, GluingUnderReplay)
        assert "patch_alpha" in result.replayed_patches

    def test_default_algorithm_constant(self):
        """DEFAULT_ALGORITHM must be a non-empty string."""
        assert isinstance(DEFAULT_ALGORITHM, str)
        assert len(DEFAULT_ALGORITHM) > 0

    def test_default_algorithm_is_incremental(self):
        """DEFAULT_ALGORITHM should be 'incremental' by convention."""
        assert DEFAULT_ALGORITHM == "incremental"

    def test_default_algorithm_is_registered(self):
        """DEFAULT_ALGORITHM name must correspond to a registered algorithm."""
        reg = AlgorithmRegistry()
        algo = reg.get(DEFAULT_ALGORITHM)
        assert algo is not None
        assert isinstance(algo, ReplayAlgorithm)
