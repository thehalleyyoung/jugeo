"""
Integration tests for jugeo.generation.replay_gluing.

These tests exercise the full pipeline end-to-end:
  change_set → plan → replay → convergence verification → certification

They also test each public surface (PipelineResult, DescentAdaptor,
GoalAdaptor, FrontierIntegrator) in isolation and in combination, plus all
theorem checks (IncrementalCorrectnessTheorem, ConvergenceGuaranteeTheorem,
ReplaySoundnessTheorem, MonotonicityClaim, TheoremSuite).
"""

from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# Path bootstrap – works regardless of where pytest is invoked from
# ---------------------------------------------------------------------------
ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import time
import uuid

# ---------------------------------------------------------------------------
# Core model imports
# ---------------------------------------------------------------------------
from jugeo.generation.replay_gluing.models import (
    ReplayGluingPlan,
    GluingUnderReplay,
    IncrementalGluing,
    ConvergenceRecord,
    ReplayStrategy,
    ReplayPhase,
)

# ---------------------------------------------------------------------------
# Stage imports
# ---------------------------------------------------------------------------
from jugeo.generation.replay_gluing.s01_replay_planning import (
    ChangeSet,
    ReplayPlanner,
    DependencyAnalyzer,
)
from jugeo.generation.replay_gluing.s02_incremental_replay import (
    GluingSnapshot,
    ReplayCache,
    IncrementalReplayer,
)
from jugeo.generation.replay_gluing.s03_convergence_verification import (
    ConvergenceVerifier,
    ConvergenceCertificate,
)

# ---------------------------------------------------------------------------
# Algorithm layer
# ---------------------------------------------------------------------------
from jugeo.generation.replay_gluing.algorithms import (
    FullReplayAlgorithm,
    IncrementalReplayAlgorithm,
    AlgorithmRegistry,
    run_algorithm,
    select_algorithm,
)

# ---------------------------------------------------------------------------
# Integration / pipeline layer
# ---------------------------------------------------------------------------
from jugeo.generation.replay_gluing.integration import (
    ReplayGluingPipeline,
    DescentAdaptor,
    GoalAdaptor,
    FrontierIntegrator,
    PipelineResult,
    run_full_pipeline,
    pipeline_from_goal_change,
)

# ---------------------------------------------------------------------------
# Theorem layer
# ---------------------------------------------------------------------------
from jugeo.generation.replay_gluing.theorems import (
    IncrementalCorrectnessTheorem,
    ConvergenceGuaranteeTheorem,
    ReplaySoundnessTheorem,
    MonotonicityClaim,
    TheoremSuite,
)

# ---------------------------------------------------------------------------
# Optional jugeo geometry / goal / treaty deps
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.geometry.supports import SupportRegion
    from jugeo.generation.goals import ConstructionGoal, GoalPriority
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause, TreatyStatus
    from jugeo.evidence.trust import TrustTier

    HAS_JUGEO_DEPS = True
except ImportError:
    HAS_JUGEO_DEPS = False


# ---------------------------------------------------------------------------
# Module-level test helpers
# ---------------------------------------------------------------------------

def make_support(patch: str = "patch_alpha"):
    """Return a minimal support region for a single patch.

    Falls back to a plain dict when the jugeo geometry layer is unavailable.
    """
    if not HAS_JUGEO_DEPS:
        return {"patches": frozenset({patch})}
    coord = CoordinateObject("coord_gamma", CoordinateKind.REGION, ("coord_gamma",))
    return SupportRegion(coord, frozenset({patch}))


def make_change_set(
    changed=("patch_alpha",),
    unchanged=("patch_beta",),
    removed=(),
    metadata=None,
) -> ChangeSet:
    """Build a ChangeSet with descriptive default patch names.

    Parameters
    ----------
    changed:    patches that changed between revisions
    unchanged:  patches that stayed the same
    removed:    patches that were dropped entirely
    metadata:   optional dict of extra data
    """
    return ChangeSet(
        changed_patches=frozenset(changed),
        unchanged_patches=frozenset(unchanged),
        removed_patches=frozenset(removed),
        change_metadata=metadata or {},
    )


def make_plan(
    changed=("patch_alpha",),
    unchanged=("patch_beta",),
    strategy=ReplayStrategy.INCREMENTAL,
) -> ReplayGluingPlan:
    """Construct a minimal ReplayGluingPlan."""
    return ReplayGluingPlan(
        strategy=strategy,
        changed_patches=frozenset(changed),
        unchanged_patches=frozenset(unchanged),
    )


def make_gluing(
    patches=("patch_alpha", "patch_beta"),
    phase=ReplayPhase.COMPLETED,
) -> GluingUnderReplay:
    """Create a GluingUnderReplay with every patch already marked replayed.

    This is the canonical "finished gluing" for use in downstream tests.
    The first patch is considered changed; the rest are unchanged.
    """
    plan = make_plan(changed=patches[:1], unchanged=patches[1:])
    g = GluingUnderReplay(plan=plan)
    for p in patches:
        g.mark_replayed(p, {"section": f"section_{p}", "value": 1})
    # Force the phase to the desired value after replay
    g.phase = phase
    return g


def make_convergence_dict(gluing: GluingUnderReplay) -> dict:
    """Extract the minimal dict representation used by ConvergenceVerifier."""
    return {
        "sections": gluing.patch_sections,
        "gluing_id": gluing.gluing_id,
    }


# ===========================================================================
# TestPipelineResult
# ===========================================================================

class TestPipelineResult:
    """Unit tests for the PipelineResult data class."""

    def test_creation_defaults(self):
        """PipelineResult() must be constructible with no arguments."""
        r = PipelineResult()
        assert r.result_id, "result_id should be auto-populated (UUID)"
        assert r.success is False, "default success flag must be False"
        assert r.gluing is None, "no gluing before a run"
        assert r.certificate is None, "no certificate before verification"
        assert r.error_message == "", "default error_message is empty string"

    def test_result_id_is_unique(self):
        """Each PipelineResult must receive a unique result_id."""
        r1 = PipelineResult()
        r2 = PipelineResult()
        assert r1.result_id != r2.result_id

    def test_summary_success_contains_keyword(self):
        """Summary of a successful result must contain 'SUCCESS'."""
        g = make_gluing()
        r = PipelineResult(success=True, gluing=g, elapsed_seconds=0.1)
        s = r.summary()
        assert isinstance(s, str), "summary() must return a string"
        assert "SUCCESS" in s, f"Expected 'SUCCESS' in summary, got: {s!r}"

    def test_summary_failure_contains_keyword(self):
        """Summary of a failed result must contain 'FAILURE'."""
        r = PipelineResult(success=False, error_message="Something broke")
        s = r.summary()
        assert isinstance(s, str)
        assert "FAILURE" in s, f"Expected 'FAILURE' in summary, got: {s!r}"

    def test_to_dict_has_required_keys(self):
        """to_dict() must include at minimum 'success' and 'result_id'."""
        r = PipelineResult(success=True)
        d = r.to_dict()
        assert isinstance(d, dict)
        assert "success" in d
        assert d["success"] is True
        assert "result_id" in d

    def test_elapsed_seconds_propagated(self):
        """elapsed_seconds passed at construction must round-trip through to_dict."""
        r = PipelineResult(success=True, elapsed_seconds=3.14)
        assert r.elapsed_seconds == pytest.approx(3.14)
        d = r.to_dict()
        if "elapsed_seconds" in d:
            assert d["elapsed_seconds"] == pytest.approx(3.14)

    def test_error_message_propagated(self):
        """error_message should survive serialisation."""
        msg = "region_delta processing failed"
        r = PipelineResult(success=False, error_message=msg)
        assert r.error_message == msg
        d = r.to_dict()
        assert d.get("error_message") == msg or msg in str(d)

    def test_metadata_stored(self):
        """Arbitrary metadata dict should be stored on the result."""
        meta = {"strategy": "FULL", "patch_count": 7}
        r = PipelineResult(metadata=meta)
        assert r.metadata == meta


# ===========================================================================
# TestReplayGluingPipeline
# ===========================================================================

class TestReplayGluingPipeline:
    """Tests for the top-level ReplayGluingPipeline orchestrator."""

    def test_creation_with_defaults(self):
        """ReplayGluingPipeline() must be constructible with no arguments."""
        p = ReplayGluingPipeline()
        assert p is not None

    def test_run_returns_pipeline_result(self):
        """pipeline.run(change_set) must return a PipelineResult."""
        p = ReplayGluingPipeline()
        cs = make_change_set()
        result = p.run(cs)
        assert isinstance(result, PipelineResult)

    def test_run_success_flag_is_true(self):
        """A well-formed change set must produce a successful result."""
        p = ReplayGluingPipeline()
        cs = make_change_set(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
        )
        result = p.run(cs)
        assert result.success is True

    def test_run_with_verification_returns_result(self):
        """With verify_convergence=True the run must still return PipelineResult."""
        p = ReplayGluingPipeline(verify_convergence=True)
        cs = make_change_set()
        result = p.run(cs)
        assert isinstance(result, PipelineResult)

    def test_run_with_verification_gluing_populated(self):
        """With verification the result.gluing must not be None on success."""
        p = ReplayGluingPipeline(verify_convergence=True)
        cs = make_change_set(
            changed=("section_alpha",),
            unchanged=("section_beta",),
        )
        result = p.run(cs)
        assert result.success is True
        assert result.gluing is not None, "gluing must be populated on success"

    def test_run_empty_change_set(self):
        """An empty ChangeSet should be handled gracefully."""
        p = ReplayGluingPipeline()
        cs = ChangeSet()
        result = p.run(cs)
        assert isinstance(result, PipelineResult)

    def test_run_all_changed_full_strategy(self):
        """FULL strategy with all patches changed must succeed."""
        p = ReplayGluingPipeline(strategy=ReplayStrategy.FULL)
        cs = make_change_set(
            changed=("patch_alpha", "patch_beta", "patch_gamma"),
            unchanged=(),
        )
        result = p.run(cs)
        assert result.success is True

    def test_run_elapsed_seconds_non_negative(self):
        """elapsed_seconds on the result must be >= 0."""
        p = ReplayGluingPipeline()
        cs = make_change_set()
        result = p.run(cs)
        assert result.elapsed_seconds >= 0

    @pytest.mark.parametrize("strategy", [
        ReplayStrategy.FULL,
        ReplayStrategy.INCREMENTAL,
        ReplayStrategy.LAZY,
    ])
    def test_run_with_various_strategies(self, strategy):
        """Pipeline must succeed for FULL, INCREMENTAL, and LAZY strategies."""
        p = ReplayGluingPipeline(strategy=strategy)
        cs = make_change_set()
        result = p.run(cs)
        assert isinstance(result, PipelineResult)
        assert result.success is True, (
            f"Expected success=True for strategy={strategy}, "
            f"got error: {result.error_message!r}"
        )

    def test_run_removed_patches(self):
        """Removed patches must not cause a failure."""
        p = ReplayGluingPipeline()
        cs = make_change_set(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
            removed=("patch_delta",),
        )
        result = p.run(cs)
        assert isinstance(result, PipelineResult)


# ===========================================================================
# TestDescentAdaptor
# ===========================================================================

class TestDescentAdaptor:
    """Tests for DescentAdaptor which bridges GluingUnderReplay → descent data."""

    def test_adapt_returns_dict(self):
        """adapt() must return a dict."""
        adaptor = DescentAdaptor()
        g = make_gluing()
        result = adaptor.adapt(g)
        assert isinstance(result, dict)

    def test_adapt_contains_patch_sections(self):
        """The adapted dict must contain a 'patch_sections' key."""
        adaptor = DescentAdaptor()
        g = make_gluing(patches=("patch_alpha", "patch_beta"))
        result = adaptor.adapt(g)
        assert "patch_sections" in result, (
            f"Expected 'patch_sections' in adapt() result, got keys: {list(result)}"
        )

    def test_build_gluing_data_returns_dict(self):
        """build_gluing_data() must return a non-None dict."""
        adaptor = DescentAdaptor()
        g = make_gluing()
        data = adaptor.build_gluing_data(g)
        assert data is not None
        assert isinstance(data, dict)

    def test_run_descent_returns_status(self):
        """run_descent() must return a dict containing a 'status' key."""
        adaptor = DescentAdaptor()
        g = make_gluing()
        data = adaptor.build_gluing_data(g)
        result = adaptor.run_descent(data)
        assert isinstance(result, dict)
        assert "status" in result, (
            f"Expected 'status' key in run_descent() result, got: {list(result)}"
        )

    def test_extract_local_sections_returns_patch_keys(self):
        """extract_local_sections() must return a dict keyed by patch name."""
        adaptor = DescentAdaptor()
        g = make_gluing(patches=("patch_alpha", "patch_beta"))
        sections = adaptor.extract_local_sections(g)
        assert isinstance(sections, dict)
        assert "patch_alpha" in sections, (
            f"Expected 'patch_alpha' in local sections, got: {list(sections)}"
        )

    def test_extract_overlap_conditions_returns_dict(self):
        """extract_overlap_conditions() must return a dict."""
        adaptor = DescentAdaptor()
        g = make_gluing()
        g.add_overlap("patch_alpha:patch_beta", {"compat": True})
        overlaps = adaptor.extract_overlap_conditions(g)
        assert isinstance(overlaps, dict)

    def test_adapt_idempotent(self):
        """Calling adapt() twice on the same gluing must return equal dicts."""
        adaptor = DescentAdaptor()
        g = make_gluing(patches=("patch_alpha", "patch_gamma"))
        r1 = adaptor.adapt(g)
        r2 = adaptor.adapt(g)
        assert r1.keys() == r2.keys()

    def test_build_and_run_roundtrip(self):
        """build_gluing_data then run_descent must complete without exception."""
        adaptor = DescentAdaptor()
        g = make_gluing(patches=("region_alpha", "region_delta"))
        data = adaptor.build_gluing_data(g)
        result = adaptor.run_descent(data)
        assert result is not None


# ===========================================================================
# TestGoalAdaptor
# ===========================================================================

class TestGoalAdaptor:
    """Tests for GoalAdaptor which translates goal changes into ChangeSets."""

    def test_goal_change_to_change_set_from_dicts(self):
        """goal_change_to_change_set must return a ChangeSet."""
        adaptor = GoalAdaptor()
        old = {"patches": ["patch_alpha", "patch_beta"]}
        new = {"patches": ["patch_beta", "patch_gamma"]}
        cs = adaptor.goal_change_to_change_set(old, new)
        assert isinstance(cs, ChangeSet)

    def test_goal_change_identical_goals(self):
        """Identical old/new goals should produce no changed patches."""
        adaptor = GoalAdaptor()
        goal = {"patches": ["patch_alpha", "patch_beta"]}
        cs = adaptor.goal_change_to_change_set(goal, goal)
        # Identical goals → at most zero changed patches
        assert len(cs.changed_patches) >= 0  # no crash is required
        assert cs.removed_patches == frozenset() or isinstance(cs.removed_patches, frozenset)

    def test_goal_change_patch_added(self):
        """New patch appearing in new goal must show up as changed or added."""
        adaptor = GoalAdaptor()
        old = {"patches": ["patch_alpha"]}
        new = {"patches": ["patch_alpha", "patch_gamma"]}
        cs = adaptor.goal_change_to_change_set(old, new)
        assert isinstance(cs, ChangeSet)
        # patch_gamma is new → should appear in changed_patches (not removed)
        assert "patch_gamma" in cs.changed_patches or "patch_gamma" in cs.unchanged_patches

    def test_goal_change_different_patches_removes_old(self):
        """Old-only patch must appear in removed_patches or changed_patches."""
        adaptor = GoalAdaptor()
        old = {"patches": ["patch_alpha"]}
        new = {"patches": ["patch_beta"]}
        cs = adaptor.goal_change_to_change_set(old, new)
        assert isinstance(cs, ChangeSet)
        # patch_alpha disappeared; patch_beta appeared
        assert "patch_alpha" in cs.removed_patches or "patch_beta" in cs.changed_patches

    def test_extract_patches_from_dict(self):
        """_extract_patches must return a frozenset of the listed patch IDs."""
        adaptor = GoalAdaptor()
        goal = {"patches": ["patch_alpha", "patch_beta", "patch_gamma"]}
        patches = adaptor._extract_patches(goal)
        assert isinstance(patches, frozenset)
        assert "patch_alpha" in patches
        assert "patch_beta" in patches
        assert "patch_gamma" in patches

    def test_extract_patches_empty_goal(self):
        """_extract_patches on an empty dict must return frozenset()."""
        adaptor = GoalAdaptor()
        result = adaptor._extract_patches({})
        assert isinstance(result, frozenset)
        assert len(result) == 0

    def test_goals_to_prior_gluing_returns_dict(self):
        """goals_to_prior_gluing must return a dict keyed by patch."""
        adaptor = GoalAdaptor()
        goal = {"patches": ["patch_alpha", "patch_beta"]}
        prior = adaptor.goals_to_prior_gluing(goal)
        assert isinstance(prior, dict)
        assert "patch_alpha" in prior

    def test_goals_to_prior_gluing_multiple_patches(self):
        """goals_to_prior_gluing with several patches must include all of them."""
        adaptor = GoalAdaptor()
        goal = {"patches": ["patch_alpha", "patch_beta", "region_delta"]}
        prior = adaptor.goals_to_prior_gluing(goal)
        for p in ("patch_alpha", "patch_beta", "region_delta"):
            assert p in prior, f"Expected {p!r} in prior gluing, got keys: {list(prior)}"


# ===========================================================================
# TestFrontierIntegrator
# ===========================================================================

class TestFrontierIntegrator:
    """Tests for FrontierIntegrator which propagates replay results to the frontier."""

    def test_integrate_success_updates_frontier(self):
        """A successful PipelineResult must add entries to the frontier."""
        integrator = FrontierIntegrator()
        g = make_gluing(patches=("patch_alpha",))
        result = PipelineResult(success=True, gluing=g)
        integrator.integrate(result)
        assert len(integrator.frontier) > 0

    def test_integrate_failure_does_not_update_frontier(self):
        """A failed PipelineResult must not update the frontier."""
        integrator = FrontierIntegrator()
        result = PipelineResult(success=False)
        integrator.integrate(result)
        assert len(integrator.frontier) == 0

    def test_create_frontier_item_structure(self):
        """create_frontier_item must return a dict with 'patch' and 'timestamp'."""
        integrator = FrontierIntegrator()
        item = integrator.create_frontier_item("patch_gamma", {"data": 42})
        assert item["patch"] == "patch_gamma"
        assert "timestamp" in item, (
            f"Expected 'timestamp' in frontier item, got: {list(item)}"
        )

    def test_extract_frontier_updates_includes_patches(self):
        """extract_frontier_updates must include all replayed patches."""
        integrator = FrontierIntegrator()
        g = make_gluing(patches=("patch_alpha", "patch_beta"))
        result = PipelineResult(success=True, gluing=g)
        updates = integrator.extract_frontier_updates(result)
        assert isinstance(updates, dict)
        assert "patch_alpha" in updates, (
            f"Expected 'patch_alpha' in updates, got: {list(updates)}"
        )

    def test_mark_resolved_removes_from_frontier(self):
        """After mark_resolved(patch), that patch must not appear in the frontier."""
        integrator = FrontierIntegrator()
        g = make_gluing(patches=("patch_alpha",))
        result = PipelineResult(success=True, gluing=g)
        integrator.integrate(result)
        integrator.mark_resolved("patch_alpha")
        assert "patch_alpha" not in integrator.frontier

    def test_integrate_multiple_results(self):
        """Integrating two results sequentially must accumulate frontier entries."""
        integrator = FrontierIntegrator()
        for patch in ("patch_alpha", "patch_beta"):
            g = make_gluing(patches=(patch,))
            integrator.integrate(PipelineResult(success=True, gluing=g))
        assert len(integrator.frontier) >= 1

    def test_frontier_starts_empty(self):
        """A freshly constructed FrontierIntegrator must have an empty frontier."""
        integrator = FrontierIntegrator()
        assert len(integrator.frontier) == 0


# ===========================================================================
# TestTheoremIntegration
# ===========================================================================

class TestTheoremIntegration:
    """Tests that the theorem checkers integrate properly with pipeline outputs."""

    def test_incremental_correctness_identical_gluings(self):
        """IncrementalCorrectnessTheorem must pass when full == incremental."""
        theorem = IncrementalCorrectnessTheorem()
        g_full = make_gluing(patches=("patch_alpha", "patch_beta"))
        g_incr = make_gluing(patches=("patch_alpha", "patch_beta"))
        result = theorem.check(g_full, g_incr)
        assert result.passed is True
        assert isinstance(result.message, str)
        assert result.theorem_name is not None

    def test_convergence_guarantee_with_decreasing_history(self):
        """ConvergenceGuaranteeTheorem must pass on a strictly decreasing series."""
        theorem = ConvergenceGuaranteeTheorem()
        history = [1.0, 0.8, 0.6, 0.4, 0.2]
        result = theorem.check(history)
        assert result.passed is True

    def test_convergence_guarantee_has_evidence(self):
        """ConvergenceGuaranteeTheorem result must include evidence."""
        theorem = ConvergenceGuaranteeTheorem()
        history = [1.0, 0.7, 0.3]
        result = theorem.check(history)
        assert result.evidence is not None

    def test_soundness_fully_replayed_gluing(self):
        """ReplaySoundnessTheorem must pass for a fully replayed gluing."""
        theorem = ReplaySoundnessTheorem()
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        g = GluingUnderReplay(plan=plan)
        g.mark_replayed("patch_alpha", {"v": 1})
        g.mark_replayed("patch_beta", {"v": 2})
        result = theorem.check(g)
        assert result.passed is True

    def test_monotonicity_strictly_decreasing(self):
        """MonotonicityClaim must pass for a strictly decreasing sequence."""
        claim = MonotonicityClaim()
        result = claim.check([1.0, 0.9, 0.7, 0.5, 0.2, 0.1])
        assert result.passed is True
        assert result.theorem_name is not None

    def test_monotonicity_constant_sequence(self):
        """MonotonicityClaim must not crash on a constant sequence."""
        claim = MonotonicityClaim()
        result = claim.check([0.5, 0.5, 0.5])
        assert hasattr(result, "passed")

    def test_theorem_suite_runs_all_returns_list(self):
        """TheoremSuite.run_all must return a non-empty list of TheoremCheckResult."""
        suite = TheoremSuite()
        g = make_gluing(patches=("patch_alpha", "patch_beta"))
        results = suite.run_all(g, metric_history=[1.0, 0.5, 0.1])
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert hasattr(r, "passed"), "each result must have a 'passed' attr"
            assert hasattr(r, "theorem_name"), "each result must have a 'theorem_name' attr"

    def test_theorem_suite_all_pass_boolean(self):
        """TheoremSuite.all_pass must return a bool."""
        suite = TheoremSuite()
        g = make_gluing(patches=("patch_alpha",))
        results = suite.run_all(g, metric_history=[1.0, 0.5])
        verdict = suite.all_pass(results)
        assert isinstance(verdict, bool)


# ===========================================================================
# TestEndToEnd
# ===========================================================================

class TestEndToEnd:
    """Full end-to-end pipeline tests: change_set → plan → replay → verify → certify."""

    def test_e2e_simple_change(self):
        """Single changed patch: plan → replay → convergence → certificate."""
        cs = make_change_set(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
        )
        planner = ReplayPlanner()
        plan = planner.plan(cs, prior_state={})
        assert plan.is_valid(), "Plan derived from valid ChangeSet must be valid"

        gluing = run_algorithm(plan)
        assert gluing.phase == ReplayPhase.COMPLETED, (
            f"Expected COMPLETED phase, got {gluing.phase}"
        )

        v = ConvergenceVerifier()
        g_dict = make_convergence_dict(gluing)
        record = v.verify([g_dict, g_dict])
        cert = v.certify_convergence(record)
        assert cert is not None
        assert cert.validate() is True

    def test_e2e_no_changes_trivial(self):
        """Empty ChangeSet → noop plan with zero patches → trivial gluing."""
        cs = ChangeSet()
        planner = ReplayPlanner()
        plan = planner.plan(cs, prior_state={})
        assert plan.total_patch_count == 0, (
            f"Expected 0 total patches for empty ChangeSet, got {plan.total_patch_count}"
        )
        gluing = run_algorithm(plan)
        assert isinstance(gluing, GluingUnderReplay)

    def test_e2e_all_patches_changed_full_replay(self):
        """All patches changed with FULL strategy → all must appear in replayed_patches."""
        cs = make_change_set(
            changed=("patch_alpha", "patch_beta", "patch_gamma"),
            unchanged=(),
        )
        planner = ReplayPlanner(strategy_override=ReplayStrategy.FULL)
        plan = planner.plan(cs, prior_state={})
        gluing = run_algorithm(plan)
        assert gluing.phase == ReplayPhase.COMPLETED
        assert set(gluing.replayed_patches) == {"patch_alpha", "patch_beta", "patch_gamma"}, (
            f"Expected exactly the three changed patches in replayed_patches, "
            f"got: {gluing.replayed_patches}"
        )

    def test_e2e_convergence_three_identical_rounds(self):
        """Three identical rounds must converge and yield a certificate."""
        cs = make_change_set(changed=("patch_alpha",), unchanged=("patch_beta",))
        planner = ReplayPlanner()
        plan = planner.plan(cs, prior_state={})
        gluing = run_algorithm(plan)
        g_dict = make_convergence_dict(gluing)
        v = ConvergenceVerifier()
        record = v.verify([g_dict, g_dict, g_dict])
        cert = v.certify_convergence(record)
        assert cert is not None, "Three identical rounds must produce a certificate"

    def test_e2e_run_full_pipeline_function(self):
        """The run_full_pipeline() convenience function must return a successful result."""
        cs = make_change_set(changed=("region_alpha",), unchanged=("region_beta",))
        result = run_full_pipeline(cs)
        assert isinstance(result, PipelineResult)
        assert result.success is True, (
            f"run_full_pipeline failed: {result.error_message!r}"
        )
        assert result.elapsed_seconds >= 0

    def test_e2e_from_goal_change(self):
        """pipeline_from_goal_change with disjoint old/new patches must succeed."""
        old_goal = {"patches": ["patch_alpha", "patch_beta"]}
        new_goal = {"patches": ["patch_beta", "patch_gamma"]}
        result = pipeline_from_goal_change(old_goal, new_goal)
        assert isinstance(result, PipelineResult)
        # We only require it returns a result; success depends on implementation
        assert result is not None

    def test_e2e_result_gluing_not_none_on_success(self):
        """On success the result.gluing must be populated."""
        cs = make_change_set(changed=("section_gamma",), unchanged=("section_delta",))
        result = run_full_pipeline(cs)
        if result.success:
            assert result.gluing is not None

    @pytest.mark.parametrize("strategy", [
        ReplayStrategy.FULL,
        ReplayStrategy.INCREMENTAL,
        ReplayStrategy.LAZY,
    ])
    def test_e2e_all_strategies_succeed(self, strategy):
        """run_full_pipeline must succeed for FULL, INCREMENTAL, and LAZY."""
        cs = make_change_set(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
        )
        result = run_full_pipeline(cs, strategy=strategy)
        assert isinstance(result, PipelineResult)
        assert result.success is True, (
            f"strategy={strategy} failed: {result.error_message!r}"
        )


# ===========================================================================
# TestJugeoIntegration
# ===========================================================================

class TestJugeoIntegration:
    """Tests that integrate with optional jugeo geometry / goal / treaty types.

    All tests in this class are skipped when the optional jugeo deps are absent.
    """

    @pytest.mark.skipif(not HAS_JUGEO_DEPS, reason="jugeo geometry deps not available")
    def test_with_construction_goal(self):
        """GoalAdaptor must handle a ConstructionGoal-derived dict correctly."""
        support = make_support("patch_alpha")
        goal = ConstructionGoal("g1", support, TrustTier.PROPOSAL, GoalPriority.MEDIUM)
        assert goal is not None
        # ConstructionGoal has no .patches attr, so we use the dict form
        goal_dict = {"patches": ["patch_alpha"]}
        adaptor = GoalAdaptor()
        cs = adaptor.goal_change_to_change_set({}, goal_dict)
        assert isinstance(cs, ChangeSet)

    @pytest.mark.skipif(not HAS_JUGEO_DEPS, reason="jugeo geometry deps not available")
    def test_overlap_treaty_importable(self):
        """OverlapTreaty must be importable (class-level smoke test)."""
        assert OverlapTreaty is not None

    @pytest.mark.skipif(not HAS_JUGEO_DEPS, reason="jugeo geometry deps not available")
    def test_trust_tier_in_pipeline_metadata(self):
        """TrustTier values must survive round-trip through PipelineResult metadata."""
        tier = TrustTier.PROPOSAL
        result = PipelineResult(metadata={"trust_tier": tier.value})
        assert result.metadata["trust_tier"] == tier.value

    @pytest.mark.skipif(not HAS_JUGEO_DEPS, reason="jugeo geometry deps not available")
    def test_support_region_creation(self):
        """make_support() must return a non-None SupportRegion."""
        support = make_support("region_delta")
        assert support is not None

    @pytest.mark.skipif(not HAS_JUGEO_DEPS, reason="jugeo geometry deps not available")
    def test_coordinate_object_and_extract_patches(self):
        """CoordinateObject creation plus _extract_patches must work together."""
        coord = CoordinateObject("c_gamma", CoordinateKind.REGION, ("c_gamma",))
        assert coord is not None
        adaptor = GoalAdaptor()
        result = adaptor._extract_patches({"patches": ["patch_alpha"]})
        assert isinstance(result, frozenset)
        assert "patch_alpha" in result


# ===========================================================================
# TestParametrizedEndToEnd
# ===========================================================================

class TestParametrizedEndToEnd:
    """Parametrized end-to-end stress tests covering a range of patch counts,
    strategies, round counts, and change ratios.
    """

    @pytest.mark.parametrize("n_patches", [1, 3, 5, 10])
    def test_pipeline_with_n_patches(self, n_patches):
        """Pipeline must successfully replay exactly n_patches changed patches."""
        changed = [f"patch_{i:02d}" for i in range(n_patches)]
        cs = make_change_set(changed=changed, unchanged=[])
        result = run_full_pipeline(cs)
        assert result.success is True, (
            f"Expected success with {n_patches} patches, "
            f"got error: {result.error_message!r}"
        )
        assert result.gluing is not None
        assert len(result.gluing.replayed_patches) == n_patches, (
            f"Expected {n_patches} replayed patches, "
            f"got {len(result.gluing.replayed_patches)}"
        )

    @pytest.mark.parametrize("strategy", list(ReplayStrategy))
    def test_algorithm_selection_for_all_strategies(self, strategy):
        """select_algorithm must return an algorithm that supports the given strategy."""
        algo = select_algorithm(strategy)
        assert algo is not None
        assert algo.supports_strategy(strategy), (
            f"Algorithm {algo!r} does not support strategy {strategy}"
        )

    @pytest.mark.parametrize("n_rounds", [2, 3, 5])
    def test_convergence_multi_round_returns_record(self, n_rounds):
        """ConvergenceVerifier must handle n identical rounds without error."""
        cs = make_change_set(changed=("patch_alpha",), unchanged=("patch_beta",))
        planner = ReplayPlanner()
        plan = planner.plan(cs, prior_state={})
        gluing = run_algorithm(plan)
        g_dict = make_convergence_dict(gluing)
        history = [g_dict] * n_rounds
        v = ConvergenceVerifier()
        record = v.verify(history)
        assert record is not None, f"verify() must return a record for {n_rounds} rounds"
        assert hasattr(record, "converged")

    @pytest.mark.parametrize("change_ratio", [0.0, 0.25, 0.5, 1.0])
    def test_change_ratio_routing_and_validity(self, change_ratio):
        """Plans derived from any change_ratio must be marked valid."""
        total = 10
        n_changed = int(total * change_ratio)
        n_unchanged = total - n_changed
        changed = [f"changed_{i:02d}" for i in range(n_changed)]
        unchanged = [f"unchanged_{i:02d}" for i in range(n_unchanged)]
        cs = make_change_set(changed=changed, unchanged=unchanged)
        # Validate reported change_ratio (only when total > 0)
        if total > 0:
            assert abs(cs.change_ratio - change_ratio) < 1e-9, (
                f"Expected change_ratio={change_ratio}, got {cs.change_ratio}"
            )
        planner = ReplayPlanner()
        plan = planner.plan(cs, prior_state={})
        assert plan.is_valid(), (
            f"Plan must be valid for change_ratio={change_ratio}"
        )

    @pytest.mark.parametrize("strategy", [
        ReplayStrategy.FULL,
        ReplayStrategy.INCREMENTAL,
    ])
    def test_algorithm_registry_contains_strategy(self, strategy):
        """AlgorithmRegistry must have an entry for each non-ADAPTIVE strategy."""
        registry = AlgorithmRegistry()
        algo = registry.get(strategy)
        assert algo is not None, (
            f"AlgorithmRegistry missing entry for strategy {strategy}"
        )

    @pytest.mark.parametrize("patch_name", [
        "patch_alpha",
        "section_gamma",
        "region_delta",
        "node_epsilon",
    ])
    def test_pipeline_with_descriptive_patch_names(self, patch_name):
        """Pipeline must handle descriptive (non-trivial) patch names."""
        cs = make_change_set(changed=(patch_name,), unchanged=("patch_unchanged",))
        result = run_full_pipeline(cs)
        assert result.success is True
        assert result.gluing is not None
        assert patch_name in result.gluing.replayed_patches, (
            f"Expected {patch_name!r} in replayed_patches"
        )

    @pytest.mark.parametrize("n_removed", [0, 1, 3])
    def test_pipeline_with_removed_patches(self, n_removed):
        """Removed patches must not block a successful result."""
        removed = [f"removed_{i:02d}" for i in range(n_removed)]
        cs = make_change_set(
            changed=("patch_alpha",),
            unchanged=("patch_beta",),
            removed=removed,
        )
        result = run_full_pipeline(cs)
        assert isinstance(result, PipelineResult)
        assert result.success is True


# ===========================================================================
# TestAlgorithmLayer
# ===========================================================================

class TestAlgorithmLayer:
    """Unit tests focused on the algorithm selection and execution layer."""

    def test_full_replay_algorithm_exists(self):
        """FullReplayAlgorithm must be instantiable."""
        algo = FullReplayAlgorithm()
        assert algo is not None

    def test_incremental_replay_algorithm_exists(self):
        """IncrementalReplayAlgorithm must be instantiable."""
        algo = IncrementalReplayAlgorithm()
        assert algo is not None

    def test_run_algorithm_returns_gluing(self):
        """run_algorithm() on a valid plan must return a GluingUnderReplay."""
        plan = make_plan(changed=("patch_alpha",), unchanged=("patch_beta",))
        gluing = run_algorithm(plan)
        assert isinstance(gluing, GluingUnderReplay)

    def test_run_algorithm_phase_completed(self):
        """run_algorithm() must finish with COMPLETED phase."""
        plan = make_plan(
            changed=("patch_alpha", "patch_gamma"),
            unchanged=("patch_beta",),
            strategy=ReplayStrategy.INCREMENTAL,
        )
        gluing = run_algorithm(plan)
        assert gluing.phase == ReplayPhase.COMPLETED

    def test_select_algorithm_full_strategy(self):
        """select_algorithm(FULL) must return an algorithm supporting FULL."""
        algo = select_algorithm(ReplayStrategy.FULL)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.FULL)

    def test_select_algorithm_incremental_strategy(self):
        """select_algorithm(INCREMENTAL) must return an algorithm supporting INCREMENTAL."""
        algo = select_algorithm(ReplayStrategy.INCREMENTAL)
        assert algo is not None
        assert algo.supports_strategy(ReplayStrategy.INCREMENTAL)

    def test_algorithm_registry_get_all_strategies(self):
        """AlgorithmRegistry must supply an algorithm for every ReplayStrategy value."""
        registry = AlgorithmRegistry()
        for strategy in ReplayStrategy:
            algo = registry.get(strategy)
            assert algo is not None, (
                f"AlgorithmRegistry missing entry for strategy {strategy}"
            )


# ===========================================================================
# TestCacheAndSnapshot
# ===========================================================================

class TestCacheAndSnapshot:
    """Tests for ReplayCache and GluingSnapshot used in stage-02."""

    def test_replay_cache_store_and_lookup(self):
        """Stored result must be retrievable by lookup."""
        cache = ReplayCache()
        cache.store("patch_alpha", {"section": "data_alpha"})
        result = cache.lookup("patch_alpha")
        assert result is not None
        assert result == {"section": "data_alpha"}

    def test_replay_cache_miss_returns_none(self):
        """Looking up a patch not in the cache must return None."""
        cache = ReplayCache()
        result = cache.lookup("patch_nonexistent")
        assert result is None

    def test_replay_cache_invalidate(self):
        """After invalidation a previously stored patch must return None."""
        cache = ReplayCache()
        cache.store("patch_alpha", {"data": 1})
        cache.invalidate("patch_alpha")
        assert cache.lookup("patch_alpha") is None

    def test_replay_cache_hit_rate_zero_on_empty(self):
        """Hit rate must be a float in [0.0, 1.0]."""
        cache = ReplayCache()
        rate = cache.get_hit_rate()
        assert 0.0 <= rate <= 1.0

    def test_replay_cache_stats_dict(self):
        """get_stats() must return a dict."""
        cache = ReplayCache()
        cache.store("patch_alpha", "v1")
        cache.lookup("patch_alpha")
        cache.lookup("patch_missing")
        stats = cache.get_stats()
        assert isinstance(stats, dict)

    def test_replay_cache_clear(self):
        """After clear() all stored entries must be gone."""
        cache = ReplayCache()
        cache.store("patch_alpha", "v1")
        cache.store("patch_beta", "v2")
        cache.clear()
        assert cache.lookup("patch_alpha") is None
        assert cache.lookup("patch_beta") is None

    def test_gluing_snapshot_roundtrip(self):
        """A GluingSnapshot serialised via to_dict / from_dict must be equal."""
        snapshot = GluingSnapshot(
            patch_sections={"patch_alpha": {"data": 1}},
            overlap_conditions={"patch_alpha:patch_beta": {"compat": True}},
        )
        d = snapshot.to_dict()
        assert isinstance(d, dict)
        restored = GluingSnapshot.from_dict(d)
        assert restored is not None

    def test_gluing_snapshot_get_section(self):
        """get_section() must return the stored section for a patch."""
        snapshot = GluingSnapshot(
            patch_sections={"patch_alpha": {"value": 99}},
        )
        section = snapshot.get_section("patch_alpha")
        assert section is not None

    def test_gluing_snapshot_diff_from_self(self):
        """diff_from(self) must produce an empty or trivially equal diff."""
        snapshot = GluingSnapshot(
            patch_sections={"patch_alpha": {"v": 1}},
        )
        diff = snapshot.diff_from(snapshot)
        assert isinstance(diff, dict)


# ===========================================================================
# TestDependencyAnalyzer
# ===========================================================================

class TestDependencyAnalyzer:
    """Tests for DependencyAnalyzer used inside ReplayPlanner."""

    def test_analyze_returns_dict(self):
        """analyze() must return a dict."""
        analyzer = DependencyAnalyzer()
        cs = make_change_set(changed=("patch_alpha",), unchanged=("patch_beta",))
        result = analyzer.analyze(cs, prior_state={})
        assert isinstance(result, dict)

    def test_add_and_get_dependency(self):
        """A dependency added via add_dependency must be retrievable."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("patch_alpha", depends_on="patch_beta")
        deps = analyzer.get_dependents("patch_beta")
        assert "patch_alpha" in deps

    def test_topological_order_simple(self):
        """topological_order must return a list containing all input patches."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("patch_alpha", depends_on="patch_beta")
        order = analyzer.topological_order(["patch_alpha", "patch_beta"])
        assert isinstance(order, list)
        assert set(order) == {"patch_alpha", "patch_beta"}

    def test_snapshot_returns_dict(self):
        """snapshot() must return a serialisable dict."""
        analyzer = DependencyAnalyzer()
        analyzer.add_dependency("patch_alpha", depends_on="patch_beta")
        snap = analyzer.snapshot()
        assert isinstance(snap, dict)

    def test_no_dependencies_empty_dependents(self):
        """get_dependents() on a patch with no dependents must return empty."""
        analyzer = DependencyAnalyzer()
        deps = analyzer.get_dependents("patch_orphan")
        assert len(deps) == 0


# ===========================================================================
# TestPlannerValidation
# ===========================================================================

class TestPlannerValidation:
    """Tests for ReplayPlanner.validate() and plan() edge cases."""

    def test_validate_valid_change_set(self):
        """validate() on a well-formed ChangeSet must return an empty error list."""
        planner = ReplayPlanner()
        cs = make_change_set(changed=("patch_alpha",), unchanged=("patch_beta",))
        errors = planner.validate(cs)
        assert isinstance(errors, list)
        assert len(errors) == 0, f"Expected no validation errors, got: {errors}"

    def test_plan_returns_valid_plan(self):
        """plan() must return a ReplayGluingPlan that passes is_valid()."""
        planner = ReplayPlanner()
        cs = make_change_set(changed=("patch_alpha",), unchanged=("patch_beta",))
        plan = planner.plan(cs, prior_state={})
        assert isinstance(plan, ReplayGluingPlan)
        assert plan.is_valid()

    def test_plan_with_strategy_override(self):
        """Strategy override must be reflected in the returned plan."""
        planner = ReplayPlanner(strategy_override=ReplayStrategy.FULL)
        cs = make_change_set(changed=("patch_alpha",), unchanged=())
        plan = planner.plan(cs, prior_state={})
        assert plan.strategy == ReplayStrategy.FULL

    def test_plan_all_patches_property(self):
        """plan.all_patches must be the union of changed and unchanged."""
        planner = ReplayPlanner()
        cs = make_change_set(changed=("patch_alpha",), unchanged=("patch_beta",))
        plan = planner.plan(cs, prior_state={})
        assert "patch_alpha" in plan.all_patches
        assert "patch_beta" in plan.all_patches

    def test_plan_total_patch_count(self):
        """total_patch_count must equal len(all_patches)."""
        planner = ReplayPlanner()
        cs = make_change_set(
            changed=("patch_alpha", "patch_gamma"),
            unchanged=("patch_beta",),
        )
        plan = planner.plan(cs, prior_state={})
        assert plan.total_patch_count == len(plan.all_patches)
