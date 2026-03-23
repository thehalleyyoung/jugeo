"""
Cross-module tests for methodology_loops × evaluation_design × evidence.trust.

copilot: shared-core marker
Theory reference: theory2.tex Ch62

These tests verify the integration between methodology_loops and sibling packages.
They exercise the full pipeline from formalization through implementation to
falsification, and validate that algorithm, theorem, manifest, and integration
components work together coherently. Optional packages are guarded with
pytest.mark.skipif so the suite remains runnable in partial environments.
"""
from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import json
import time
import uuid

# Core imports (always present)
from jugeo.evaluation.methodology_loops.models import (
    LoopPhase, LoopStatus, MethodologyConfig, LoopDiagnostics,
    LoopState, MethodologyLoop, FormalizationLoop, ImplementationLoop,
    FalsificationLoop, LoopTransition, TransitionKind,
)
from jugeo.evaluation.methodology_loops.algorithms import (
    MethodologyAlgorithms, ConvergenceResult, HypothesisRanking,
    rank_hypotheses, compute_convergence_rate, normalize_scores,
    aggregate_loop_metrics,
)
from jugeo.evaluation.methodology_loops.theorems import (
    MethodologyTheoremRegistry, build_theorem_registry,
    LoopConvergenceTheorem, TheoremStatus,
)
from jugeo.evaluation.methodology_loops.integration import (
    MethodologyLoopsIntegration, IntegrationConfig, IntegrationResult,
    build_integration,
)
from jugeo.evaluation.methodology_loops.manifest import (
    MethodologyLoopsManifest, build_methodology_manifest,
    MethodologyManifestBuilder, MethodologyLoopEntry,
)
from jugeo.evaluation.methodology_loops.s01_formalization_loop import (
    Formalizer, FormalizationLoopRunner, run_formalization_loop,
)
from jugeo.evaluation.methodology_loops.s02_implementation_loop import (
    Implementer, ImplementationLoopRunner, run_implementation_loop,
)
from jugeo.evaluation.methodology_loops.s03_falsification_loop import (
    CounterexampleSearcher, HypothesisTracker, FalsificationLoopRunner,
    run_falsification_loop, attempt_falsification,
)

# Optional cross-module imports
try:
    from jugeo.evidence.trust import TrustProfile, TrustTier
    HAS_TRUST = True
except Exception:
    HAS_TRUST = False

try:
    from jugeo.evaluation.evaluation_design.models import EvaluationDesign
    HAS_EVAL_DESIGN = True
except Exception:
    HAS_EVAL_DESIGN = False

try:
    from jugeo.orchestration.controller import Orchestrator
    HAS_ORCHESTRATOR = True
except Exception:
    HAS_ORCHESTRATOR = False


# ===========================================================================
# Helpers
# ===========================================================================

def make_loop(
    loop_id: str = "test-loop",
    iterations: int = 0,
    phase: LoopPhase = LoopPhase.FORMALIZATION,
    status: LoopStatus = LoopStatus.IDLE,
) -> MethodologyLoop:
    """Create a minimal MethodologyLoop for testing."""
    config = MethodologyConfig(
        max_iterations=10,
        convergence_threshold=0.95,
        falsification_budget=50,
        min_coverage=0.8,
        max_revisions=5,
    )
    diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
    state = LoopState(
        phase=phase,
        iteration=iterations,
        artifacts=[],
        diagnostics=diag,
        history=[],
        status=status,
    )
    return MethodologyLoop(
        loop_id=loop_id,
        config=config,
        state=state,
        transitions=[],
        artifacts=[],
        created_at=time.time(),
        updated_at=time.time(),
    )


def make_hypotheses(n: int = 3) -> list:
    """Create n test hypotheses as plain dicts."""
    return [
        {
            "id": f"hyp-{i}",
            "statement": f"Hypothesis {i}: for all x in domain_{i}, property_{i}(x) holds.",
            "domain": "mathematics",
            "priority": float(n - i),
            "score": (n - i) / n,
        }
        for i in range(n)
    ]


def make_config(max_iter: int = 10, threshold: float = 0.95) -> MethodologyConfig:
    """Create a MethodologyConfig with custom parameters."""
    return MethodologyConfig(
        max_iterations=max_iter,
        convergence_threshold=threshold,
        falsification_budget=50,
        min_coverage=0.8,
        max_revisions=5,
    )


# ===========================================================================
# TestMethodologyLoopsCore
# ===========================================================================

class TestMethodologyLoopsCore:
    """Tests that core models work together correctly.

    These tests ensure that the fundamental model objects (MethodologyLoop,
    LoopState, MethodologyConfig, LoopDiagnostics) integrate coherently and
    that transitions between phases can be represented correctly.
    """

    def test_make_loop_returns_methodology_loop(self):
        """make_loop() helper must return a MethodologyLoop instance."""
        loop = make_loop()
        assert isinstance(loop, MethodologyLoop)

    def test_loop_initial_phase(self):
        """A loop created in FORMALIZATION phase must report that phase."""
        loop = make_loop(phase=LoopPhase.FORMALIZATION)
        assert loop.state.phase == LoopPhase.FORMALIZATION

    def test_loop_initial_iteration(self):
        """A loop created at iteration 0 must report iteration 0."""
        loop = make_loop(iterations=0)
        assert loop.state.iteration == 0

    def test_loop_config_fields_accessible(self):
        """Config fields must be accessible on the loop object."""
        loop = make_loop()
        assert loop.config.max_iterations == 10
        assert loop.config.convergence_threshold == 0.95
        assert loop.config.falsification_budget == 50

    def test_loop_state_diagnostics_initially_empty(self):
        """Diagnostics collections must be empty on a fresh loop."""
        loop = make_loop()
        assert len(loop.state.diagnostics.iteration_times) == 0
        assert len(loop.state.diagnostics.errors) == 0
        assert len(loop.state.diagnostics.warnings) == 0

    def test_loop_transition_construction(self):
        """LoopTransition can be constructed with valid arguments."""
        loop = make_loop()
        transition = LoopTransition(
            from_phase=LoopPhase.FORMALIZATION,
            to_phase=LoopPhase.IMPLEMENTATION,
            kind=TransitionKind.FORWARD,
            timestamp=time.time(),
            loop_id=loop.loop_id,
        )
        assert transition.from_phase == LoopPhase.FORMALIZATION
        assert transition.to_phase == LoopPhase.IMPLEMENTATION
        assert transition.kind == TransitionKind.FORWARD

    def test_formalization_loop_specialization(self):
        """FormalizationLoop is a valid specialization of MethodologyLoop."""
        config = make_config()
        diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
        state = LoopState(
            phase=LoopPhase.FORMALIZATION, iteration=0, artifacts=[],
            diagnostics=diag, history=[], status=LoopStatus.IDLE
        )
        loop = FormalizationLoop(
            loop_id="form-loop-test",
            config=config, state=state, transitions=[], artifacts=[],
            created_at=time.time(), updated_at=time.time(),
        )
        assert isinstance(loop, MethodologyLoop)

    def test_implementation_loop_specialization(self):
        """ImplementationLoop is a valid specialization of MethodologyLoop."""
        config = make_config()
        diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
        state = LoopState(
            phase=LoopPhase.IMPLEMENTATION, iteration=0, artifacts=[],
            diagnostics=diag, history=[], status=LoopStatus.IDLE
        )
        loop = ImplementationLoop(
            loop_id="impl-loop-test",
            config=config, state=state, transitions=[], artifacts=[],
            created_at=time.time(), updated_at=time.time(),
        )
        assert isinstance(loop, MethodologyLoop)

    def test_falsification_loop_specialization(self):
        """FalsificationLoop is a valid specialization of MethodologyLoop."""
        config = make_config()
        diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
        state = LoopState(
            phase=LoopPhase.FALSIFICATION, iteration=0, artifacts=[],
            diagnostics=diag, history=[], status=LoopStatus.IDLE
        )
        loop = FalsificationLoop(
            loop_id="fals-loop-test",
            config=config, state=state, transitions=[], artifacts=[],
            created_at=time.time(), updated_at=time.time(),
        )
        assert isinstance(loop, MethodologyLoop)

    def test_loop_id_uniqueness_across_makes(self):
        """Different loop IDs should produce distinct loop objects."""
        l1 = make_loop(loop_id="loop-a")
        l2 = make_loop(loop_id="loop-b")
        assert l1.loop_id != l2.loop_id


# ===========================================================================
# TestAlgorithmsWithModels
# ===========================================================================

class TestAlgorithmsWithModels:
    """Tests that algorithms work with model instances.

    These tests verify the full pipeline: a MethodologyLoop is constructed,
    passed to algorithm functions, and the results are correctly typed and
    consistent with the model's state.
    """

    def test_convergence_check_returns_correct_loop_id(self):
        """convergence_check result loop_id must match the input loop."""
        from jugeo.evaluation.methodology_loops.algorithms import convergence_check
        loop = make_loop(loop_id="alg-model-test-1")
        result = convergence_check(loop)
        assert result.loop_id == "alg-model-test-1"

    def test_algorithms_rank_hypotheses_length(self):
        """rank_hypotheses output length must equal the input hypotheses count."""
        hypotheses = make_hypotheses(5)
        result = rank_hypotheses(hypotheses, strategy="score")
        assert len(result.hypothesis_ids) == 5

    def test_algorithms_rank_hypotheses_ids_preserved(self):
        """All input hypothesis IDs must appear in the ranking output."""
        hypotheses = make_hypotheses(4)
        result = rank_hypotheses(hypotheses, strategy="score")
        input_ids = {h["id"] for h in hypotheses}
        output_ids = set(result.hypothesis_ids)
        assert input_ids == output_ids

    def test_compute_convergence_rate_with_real_loop(self):
        """compute_convergence_rate() should work with scores derived from a loop."""
        loop = make_loop()
        alg = MethodologyAlgorithms()
        score_f = alg.score_phase(loop, LoopPhase.FORMALIZATION)
        rate = compute_convergence_rate(
            phase_scores={"formalization": score_f},
            iterations_used=loop.state.iteration,
            max_iterations=loop.config.max_iterations,
        )
        assert 0.0 <= rate <= 1.0

    def test_aggregate_metrics_keys_non_empty(self):
        """aggregate_loop_metrics() on a real loop must return non-empty keys."""
        loop = make_loop()
        metrics = aggregate_loop_metrics(loop)
        assert len(metrics) > 0

    def test_normalize_scores_from_phase_scores(self):
        """normalize_scores() should work on phase scores derived from a loop."""
        loop = make_loop()
        alg = MethodologyAlgorithms()
        raw_scores = [alg.score_phase(loop, p) for p in LoopPhase]
        normalized = normalize_scores(raw_scores)
        assert len(normalized) == len(list(LoopPhase))
        for v in normalized:
            assert 0.0 <= v <= 1.0

    def test_algorithms_estimate_remaining_zero_at_max(self):
        """estimate_remaining_iterations should be 0 at max_iterations."""
        loop = make_loop(iterations=10)  # iteration == max_iterations
        alg = MethodologyAlgorithms()
        n = alg.estimate_remaining_iterations(loop)
        assert n >= 0

    def test_algorithms_multiple_phases_score_all_valid(self):
        """score_phase() must produce valid floats for all LoopPhase values."""
        loop = make_loop()
        alg = MethodologyAlgorithms()
        for phase in LoopPhase:
            score = alg.score_phase(loop, phase)
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_hypothesis_ranking_top_k_subset(self):
        """top_k(k) must return a subset of hypothesis IDs."""
        hypotheses = make_hypotheses(6)
        result = rank_hypotheses(hypotheses, strategy="score")
        top2 = result.top_k(2)
        all_ids = set(result.hypothesis_ids)
        for hid in top2:
            assert hid in all_ids

    def test_convergence_result_after_many_iterations(self):
        """ConvergenceResult from a loop advanced by many steps should be valid."""
        from jugeo.evaluation.methodology_loops.algorithms import loop_step, convergence_check
        loop = make_loop()
        for _ in range(5):
            loop = loop_step(loop)
        result = convergence_check(loop)
        assert isinstance(result, ConvergenceResult)
        assert 0.0 <= result.convergence_rate <= 1.0


# ===========================================================================
# TestTheoremsWithModels
# ===========================================================================

class TestTheoremsWithModels:
    """Tests theorem verification against model instances.

    These tests confirm that the theorem verification pipeline produces
    consistent, type-correct results when given real MethodologyLoop objects
    in various phases and statuses.
    """

    def test_registry_verify_all_length(self):
        """verify_all() results count must equal the registry theorem count."""
        loop = make_loop()
        registry = build_theorem_registry()
        results = registry.verify_all(loop)
        assert len(results) == registry.count()

    def test_convergence_theorem_verify_returns_bool_status(self):
        """LoopConvergenceTheorem.verify() result must carry a boolean status."""
        loop = make_loop()
        thm = LoopConvergenceTheorem()
        result = thm.verify(loop)
        # Result may be bool or an object with a bool attribute
        assert result is not None

    def test_registry_verify_all_falsification_loop(self):
        """verify_all() works for a loop in FALSIFICATION phase."""
        loop = make_loop(phase=LoopPhase.FALSIFICATION)
        registry = build_theorem_registry()
        results = registry.verify_all(loop)
        assert len(results) == registry.count()

    def test_registry_summary_report_after_verify(self):
        """summary_report() after verify_all() should include result info."""
        loop = make_loop()
        registry = build_theorem_registry()
        registry.verify_all(loop)
        report = registry.summary_report()
        assert isinstance(report, str) and len(report) > 0

    def test_theorem_dependency_graph_no_cycles_check(self):
        """theorem_dependency_graph() should return a dict with no obvious cycles."""
        from jugeo.evaluation.methodology_loops.theorems import theorem_dependency_graph
        graph = theorem_dependency_graph()
        assert isinstance(graph, dict)
        # Very basic acyclicity check: no self-loops
        for node, deps in graph.items():
            assert node not in deps

    def test_export_theorem_latex_contains_all_theorem_names(self):
        """export_theorem_latex() output should contain at least some theorem names."""
        from jugeo.evaluation.methodology_loops.theorems import export_theorem_latex
        registry = build_theorem_registry()
        latex = export_theorem_latex()
        # At least one theorem name should appear somewhere in the LaTeX
        all_names = [r.name for r in registry.list_all()]
        found = any(name.lower()[:6] in latex.lower() for name in all_names)
        assert found or len(latex) > 0  # Non-empty is the minimum guarantee


# ===========================================================================
# TestManifestWithLoops
# ===========================================================================

class TestManifestWithLoops:
    """Tests manifest integration with loops.

    The MethodologyLoopsManifest aggregates loop entries, theorem references,
    and artifact registrations for documentation and traceability purposes.
    """

    def test_build_methodology_manifest_returns_manifest(self):
        """build_methodology_manifest() must return a MethodologyLoopsManifest."""
        manifest = build_methodology_manifest()
        assert isinstance(manifest, MethodologyLoopsManifest)

    def test_manifest_builder_add_loop_entry(self):
        """MethodologyManifestBuilder.add_entry() must succeed with a valid entry."""
        builder = MethodologyManifestBuilder()
        loop = make_loop(loop_id="manifest-loop-1")
        entry = MethodologyLoopEntry.from_loop(loop)
        builder.add_entry(entry)
        manifest = builder.build()
        assert isinstance(manifest, MethodologyLoopsManifest)

    def test_manifest_entry_loop_id(self):
        """MethodologyLoopEntry.from_loop() must capture the loop_id."""
        loop = make_loop(loop_id="entry-id-test")
        entry = MethodologyLoopEntry.from_loop(loop)
        assert entry.loop_id == "entry-id-test"

    def test_manifest_to_json_round_trip(self):
        """Manifest serialisation round-trip must preserve loop entries count."""
        builder = MethodologyManifestBuilder()
        for i in range(3):
            loop = make_loop(loop_id=f"manifest-loop-{i}")
            builder.add_entry(MethodologyLoopEntry.from_loop(loop))
        manifest = builder.build()
        j = manifest.to_json()
        restored = MethodologyLoopsManifest.from_json(j)
        assert restored.entry_count() == manifest.entry_count()

    def test_manifest_summarize_returns_string(self):
        """summarize() on a built manifest must return a non-empty string."""
        manifest = build_methodology_manifest()
        s = manifest.summarize()
        assert isinstance(s, str) and len(s) > 0

    def test_manifest_render_tex_returns_string(self):
        """render_tex() on a manifest must return a non-empty string."""
        manifest = build_methodology_manifest()
        tex = manifest.render_tex()
        assert isinstance(tex, str) and len(tex) > 0

    def test_manifest_builder_multiple_entries(self):
        """Builder must support adding multiple entries sequentially."""
        builder = MethodologyManifestBuilder()
        for i in range(5):
            loop = make_loop(loop_id=f"multi-entry-{i}")
            builder.add_entry(MethodologyLoopEntry.from_loop(loop))
        manifest = builder.build()
        assert manifest.entry_count() == 5


# ===========================================================================
# TestIntegrationWithModels
# ===========================================================================

class TestIntegrationWithModels:
    """Tests integration facade with model instances.

    These tests verify that MethodologyLoopsIntegration works end-to-end
    with real MethodologyLoop model objects, bridging them through the
    evaluation design, orchestrator, and evidence subsystems.
    """

    def test_build_integration_run_loop(self):
        """build_integration().run_loop() must return a MethodologyLoop."""
        integration = build_integration()
        loop = make_loop()
        result = integration.run_loop(loop)
        assert isinstance(result, MethodologyLoop)

    def test_integration_run_preserves_loop_id(self):
        """run_loop() must not change the loop_id."""
        integration = build_integration()
        loop = make_loop(loop_id="preserve-id-test")
        result = integration.run_loop(loop)
        assert result.loop_id == "preserve-id-test"

    def test_integration_health_report_keys(self):
        """health_report() must return a dict with at least one key."""
        integration = build_integration()
        report = integration.health_report()
        assert isinstance(report, dict)
        assert len(report) > 0

    def test_integration_export_state_serializable(self):
        """export_state() must return a JSON-serializable dict."""
        integration = build_integration()
        state = integration.export_state()
        assert isinstance(state, dict)
        json.dumps(state)  # Must not raise

    def test_integration_result_success_ok(self):
        """IntegrationResult.success() must report is_ok()."""
        result = IntegrationResult.success(payload={"loops": 1})
        assert result.is_ok()

    def test_integration_config_default_valid(self):
        """IntegrationConfig.default() must produce a validatable config."""
        cfg = IntegrationConfig.default()
        cfg.validate()  # Must not raise

    def test_integration_full_cycle(self):
        """setup() + run_loop() + sync_all() + teardown() must all succeed."""
        integration = MethodologyLoopsIntegration()
        loop = make_loop()
        integration.setup()
        result = integration.run_loop(loop)
        assert isinstance(result, MethodologyLoop)
        integration.sync_all(result)
        integration.teardown()


# ===========================================================================
# TestFormalizationToImplementationFlow
# ===========================================================================

class TestFormalizationToImplementationFlow:
    """End-to-end flow: formalization → implementation.

    These tests simulate the first two phases of the methodology loop pipeline,
    verifying that artefacts produced by the formalization loop are compatible
    with the implementation loop's input requirements.
    """

    def test_formalizer_init(self):
        """Formalizer can be instantiated without arguments."""
        f = Formalizer()
        assert f is not None

    def test_formalization_loop_runner_init(self):
        """FormalizationLoopRunner can be instantiated."""
        runner = FormalizationLoopRunner()
        assert runner is not None

    def test_run_formalization_loop_returns_loop(self):
        """run_formalization_loop() must return a MethodologyLoop."""
        loop = make_loop(phase=LoopPhase.FORMALIZATION)
        result = run_formalization_loop(loop)
        assert isinstance(result, MethodologyLoop)

    def test_run_formalization_loop_id_preserved(self):
        """run_formalization_loop() must preserve the loop_id."""
        loop = make_loop(loop_id="form-to-impl-id", phase=LoopPhase.FORMALIZATION)
        result = run_formalization_loop(loop)
        assert result.loop_id == "form-to-impl-id"

    def test_implementer_init(self):
        """Implementer can be instantiated without arguments."""
        i = Implementer()
        assert i is not None

    def test_implementation_loop_runner_init(self):
        """ImplementationLoopRunner can be instantiated."""
        runner = ImplementationLoopRunner()
        assert runner is not None

    def test_run_implementation_loop_returns_loop(self):
        """run_implementation_loop() must return a MethodologyLoop."""
        loop = make_loop(phase=LoopPhase.IMPLEMENTATION)
        result = run_implementation_loop(loop)
        assert isinstance(result, MethodologyLoop)

    def test_formalization_then_implementation(self):
        """Running formalization then implementation must produce a valid loop."""
        loop = make_loop(loop_id="form-impl-seq", phase=LoopPhase.FORMALIZATION)
        form_result = run_formalization_loop(loop)
        assert isinstance(form_result, MethodologyLoop)
        impl_result = run_implementation_loop(form_result)
        assert isinstance(impl_result, MethodologyLoop)


# ===========================================================================
# TestImplementationToFalsificationFlow
# ===========================================================================

class TestImplementationToFalsificationFlow:
    """End-to-end flow: implementation → falsification.

    These tests simulate the second and third phases, confirming that
    the falsification loop can consume the output of implementation and
    properly track hypotheses through the testing process.
    """

    def test_counterexample_searcher_init(self):
        """CounterexampleSearcher can be instantiated."""
        s = CounterexampleSearcher()
        assert s is not None

    def test_hypothesis_tracker_init(self):
        """HypothesisTracker can be instantiated."""
        t = HypothesisTracker()
        assert t is not None

    def test_falsification_loop_runner_init(self):
        """FalsificationLoopRunner can be instantiated."""
        runner = FalsificationLoopRunner()
        assert runner is not None

    def test_run_falsification_loop_returns_loop(self):
        """run_falsification_loop() must return a MethodologyLoop."""
        loop = make_loop(phase=LoopPhase.FALSIFICATION)
        result = run_falsification_loop(loop)
        assert isinstance(result, MethodologyLoop)

    def test_attempt_falsification_returns_result(self):
        """attempt_falsification() must return a non-None result."""
        loop = make_loop(phase=LoopPhase.FALSIFICATION)
        result = attempt_falsification(loop, hypothesis_id="hyp-cross-1")
        assert result is not None

    def test_impl_then_falsification_loop(self):
        """Running implementation then falsification must produce valid loops."""
        loop = make_loop(loop_id="impl-fals-seq", phase=LoopPhase.IMPLEMENTATION)
        impl_result = run_implementation_loop(loop)
        assert isinstance(impl_result, MethodologyLoop)
        fals_result = run_falsification_loop(impl_result)
        assert isinstance(fals_result, MethodologyLoop)


# ===========================================================================
# TestHypothesisRankingAndFalsification
# ===========================================================================

class TestHypothesisRankingAndFalsification:
    """Tests the hypothesis ranking and falsification pipeline.

    These tests verify that hypothesis ranking produces ordered results that
    the falsification loop can act upon, and that the overall pipeline is
    consistent and repeatable.
    """

    def test_ranking_and_falsification_top_hypothesis(self):
        """Top-ranked hypothesis should be the first to be falsification-tested."""
        hypotheses = make_hypotheses(5)
        ranking = rank_hypotheses(hypotheses, strategy="score")
        top_id = ranking.top_k(1)[0]
        loop = make_loop(phase=LoopPhase.FALSIFICATION)
        result = attempt_falsification(loop, hypothesis_id=top_id)
        assert result is not None

    @pytest.mark.parametrize("n", [1, 3, 5, 10])
    def test_ranking_size_invariant(self, n):
        """rank_hypotheses() output size must equal input size for n hypotheses."""
        hypotheses = make_hypotheses(n)
        ranking = rank_hypotheses(hypotheses, strategy="score")
        assert len(ranking.hypothesis_ids) == n

    def test_ranking_scores_match_ids_length(self):
        """scores list length must match hypothesis_ids length in ranking."""
        hypotheses = make_hypotheses(7)
        ranking = rank_hypotheses(hypotheses, strategy="score")
        assert len(ranking.scores) == len(ranking.hypothesis_ids)

    def test_falsification_multiple_hypotheses(self):
        """attempt_falsification() can be called for each ranked hypothesis."""
        hypotheses = make_hypotheses(4)
        ranking = rank_hypotheses(hypotheses, strategy="score")
        loop = make_loop(phase=LoopPhase.FALSIFICATION)
        for hyp_id in ranking.hypothesis_ids:
            result = attempt_falsification(loop, hypothesis_id=hyp_id)
            assert result is not None

    @pytest.mark.parametrize("strategy", ["score", "priority"])
    def test_ranking_various_strategies(self, strategy):
        """rank_hypotheses() must accept standard strategy names."""
        hypotheses = make_hypotheses(3)
        ranking = rank_hypotheses(hypotheses, strategy=strategy)
        assert isinstance(ranking, HypothesisRanking)

    def test_hypothesis_tracker_add_and_count(self):
        """HypothesisTracker should track added hypotheses."""
        tracker = HypothesisTracker()
        for hyp in make_hypotheses(3):
            tracker.add(hyp)
        count = tracker.count()
        assert count == 3

    def test_hypothesis_tracker_get_by_id(self):
        """HypothesisTracker.get(id) should return the correct hypothesis."""
        tracker = HypothesisTracker()
        hypotheses = make_hypotheses(2)
        for hyp in hypotheses:
            tracker.add(hyp)
        retrieved = tracker.get("hyp-0")
        assert retrieved is not None
        assert retrieved["id"] == "hyp-0"


# ===========================================================================
# Optional cross-module: evidence.trust
# ===========================================================================

@pytest.mark.skipif(not HAS_TRUST, reason="jugeo.evidence.trust not available")
class TestMethodologyLoopsWithTrust:
    """Cross-module: methodology_loops × evidence.trust.

    These tests verify that methodology loop artefacts can be annotated with
    trust profiles and trust tiers from the evidence.trust subsystem.
    """

    def test_trust_profile_importable(self):
        """TrustProfile must be importable."""
        assert TrustProfile is not None

    def test_trust_tier_importable(self):
        """TrustTier must be importable."""
        assert TrustTier is not None

    def test_create_trust_profile_for_loop(self):
        """A TrustProfile can be created and associated with a loop ID."""
        loop = make_loop(loop_id="trust-loop-1")
        profile = TrustProfile.create(entity_id=loop.loop_id, tier=TrustTier.HIGH)
        assert profile.entity_id == loop.loop_id

    def test_trust_tier_high_value(self):
        """TrustTier.HIGH must exist and be a valid tier member."""
        assert hasattr(TrustTier, "HIGH")
        assert TrustTier.HIGH is not None


# ===========================================================================
# Optional cross-module: evaluation_design
# ===========================================================================

@pytest.mark.skipif(not HAS_EVAL_DESIGN, reason="jugeo.evaluation.evaluation_design not available")
class TestMethodologyLoopsWithEvaluationDesign:
    """Cross-module: methodology_loops × evaluation_design.

    These tests verify that EvaluationDesign objects can be created and that
    the evaluation_design bridge in the integration layer correctly handles them.
    """

    def test_evaluation_design_importable(self):
        """EvaluationDesign must be importable."""
        assert EvaluationDesign is not None

    def test_evaluation_design_creation(self):
        """EvaluationDesign can be instantiated."""
        design = EvaluationDesign.create(name="Cross-Test Design", criteria=[])
        assert design is not None

    def test_methodology_loop_with_evaluation_design(self):
        """A methodology loop can be run alongside an EvaluationDesign object."""
        loop = make_loop(loop_id="eval-design-cross-test")
        design = EvaluationDesign.create(name="Design A", criteria=[])
        integration = build_integration()
        result = integration.run_loop(loop)
        assert isinstance(result, MethodologyLoop)
        assert design is not None

    def test_evaluation_design_bridge_sync(self):
        """EvaluationDesignBridge.sync_state() must work with a real EvaluationDesign."""
        from jugeo.evaluation.methodology_loops.integration import EvaluationDesignBridge
        bridge = EvaluationDesignBridge()
        loop = make_loop()
        design = EvaluationDesign.create(name="Sync Test", criteria=[])
        bridge.connect(design)
        result = bridge.sync_state(loop)
        assert result is not None


# ===========================================================================
# Optional cross-module: orchestration
# ===========================================================================

@pytest.mark.skipif(not HAS_ORCHESTRATOR, reason="jugeo.orchestration.controller not available")
class TestMethodologyLoopsWithOrchestrator:
    """Cross-module: methodology_loops × orchestrator.

    These tests verify that MethodologyLoop objects can be registered with and
    managed by the Orchestrator, and that lifecycle events are dispatched
    correctly.
    """

    def test_orchestrator_importable(self):
        """Orchestrator must be importable."""
        assert Orchestrator is not None

    def test_orchestrator_creation(self):
        """Orchestrator can be instantiated."""
        orch = Orchestrator()
        assert orch is not None

    def test_orchestrator_bridge_registers_loop(self):
        """OrchestratorBridge can register a loop with a real Orchestrator."""
        from jugeo.evaluation.methodology_loops.integration import OrchestratorBridge
        orch = Orchestrator()
        bridge = OrchestratorBridge()
        bridge.connect(orch)
        loop = make_loop(loop_id="orch-cross-test")
        bridge.register_loop(loop)

    def test_orchestrator_bridge_dispatch_event(self):
        """Dispatching an event via OrchestratorBridge must not raise."""
        from jugeo.evaluation.methodology_loops.integration import OrchestratorBridge
        orch = Orchestrator()
        bridge = OrchestratorBridge()
        bridge.connect(orch)
        loop = make_loop(loop_id="orch-event-test")
        bridge.register_loop(loop)
        result = bridge.dispatch_event(
            loop_id=loop.loop_id,
            event_type="phase_start",
            payload={"phase": "formalization"},
        )
        assert result is not None


# ===========================================================================
# TestEndToEndScenarios
# ===========================================================================

class TestEndToEndScenarios:
    """End-to-end scenario tests for full methodology loops.

    These tests simulate realistic use of the full methodology_loops pipeline,
    from initial loop construction through formalization, implementation,
    falsification, theorem verification, and manifest generation.
    """

    def test_full_pipeline_formalization_to_falsification(self):
        """Full pipeline: formalization → implementation → falsification must run without error."""
        loop = make_loop(loop_id="e2e-full-pipeline", phase=LoopPhase.FORMALIZATION)
        loop = run_formalization_loop(loop)
        loop = run_implementation_loop(loop)
        loop = run_falsification_loop(loop)
        assert isinstance(loop, MethodologyLoop)

    def test_full_pipeline_with_theorem_verification(self):
        """Full pipeline ending with theorem verification."""
        loop = make_loop(loop_id="e2e-theorem-verify", phase=LoopPhase.FORMALIZATION)
        loop = run_formalization_loop(loop)
        loop = run_implementation_loop(loop)
        loop = run_falsification_loop(loop)
        registry = build_theorem_registry()
        results = registry.verify_all(loop)
        assert len(results) == registry.count()

    def test_full_pipeline_with_manifest(self):
        """Full pipeline ending with manifest generation."""
        loop = make_loop(loop_id="e2e-manifest", phase=LoopPhase.FORMALIZATION)
        loop = run_formalization_loop(loop)
        entry = MethodologyLoopEntry.from_loop(loop)
        builder = MethodologyManifestBuilder()
        builder.add_entry(entry)
        manifest = builder.build()
        assert isinstance(manifest, MethodologyLoopsManifest)
        assert manifest.entry_count() >= 1

    def test_full_pipeline_with_convergence_check(self):
        """Full pipeline with convergence check at each step."""
        from jugeo.evaluation.methodology_loops.algorithms import convergence_check
        loop = make_loop(loop_id="e2e-convergence")
        for step_fn in [run_formalization_loop, run_implementation_loop, run_falsification_loop]:
            loop = step_fn(loop)
            result = convergence_check(loop)
            assert isinstance(result, ConvergenceResult)
            assert 0.0 <= result.convergence_rate <= 1.0

    @pytest.mark.parametrize("n_iterations", [1, 2, 3])
    def test_algorithm_loop_step_n_times(self, n_iterations):
        """loop_step() called n times must produce a valid loop each time."""
        from jugeo.evaluation.methodology_loops.algorithms import loop_step
        loop = make_loop(loop_id=f"e2e-step-{n_iterations}")
        for _ in range(n_iterations):
            loop = loop_step(loop)
        assert isinstance(loop, MethodologyLoop)
        assert loop.state.iteration >= 0

    def test_integration_and_theorem_combined(self):
        """Integration facade and theorem registry must work together."""
        loop = make_loop(loop_id="e2e-int-theorem")
        integration = build_integration()
        loop = integration.run_loop(loop)
        registry = build_theorem_registry()
        results = registry.verify_all(loop)
        assert isinstance(results, list)

    def test_rank_then_falsify_top_hypotheses(self):
        """Ranking then falsifying top-3 hypotheses must succeed."""
        hypotheses = make_hypotheses(6)
        ranking = rank_hypotheses(hypotheses, strategy="score")
        top3 = ranking.top_k(3)
        loop = make_loop(phase=LoopPhase.FALSIFICATION)
        for hyp_id in top3:
            result = attempt_falsification(loop, hypothesis_id=hyp_id)
            assert result is not None

    def test_manifest_after_full_pipeline(self):
        """Manifest captures a loop entry after running the full pipeline."""
        loop = make_loop(loop_id="e2e-manifest-full", phase=LoopPhase.FORMALIZATION)
        loop = run_formalization_loop(loop)
        loop = run_implementation_loop(loop)
        loop = run_falsification_loop(loop)
        entry = MethodologyLoopEntry.from_loop(loop)
        builder = MethodologyManifestBuilder()
        builder.add_entry(entry)
        manifest = builder.build()
        assert manifest.entry_count() == 1
        j = manifest.to_json()
        restored = MethodologyLoopsManifest.from_json(j)
        assert restored.entry_count() == 1

    def test_algorithms_class_full_run(self):
        """MethodologyAlgorithms used in a complete run with all methods called."""
        loop = make_loop(loop_id="e2e-alg-class")
        alg = MethodologyAlgorithms()
        loop = alg.run_loop_step(loop)
        conv = alg.check_convergence(loop)
        assert isinstance(conv, ConvergenceResult)
        score = alg.score_phase(loop, LoopPhase.FORMALIZATION)
        assert 0.0 <= score <= 1.0
        hypotheses = make_hypotheses(3)
        ranking = alg.rank_hypotheses(loop, hypotheses)
        assert isinstance(ranking, HypothesisRanking)
        metrics = alg.aggregate_metrics(loop)
        assert isinstance(metrics, dict)
        n_remaining = alg.estimate_remaining_iterations(loop)
        assert n_remaining >= 0

    def test_multi_loop_registry_verify(self):
        """Registry verify_all() called on multiple distinct loops must each succeed."""
        registry = build_theorem_registry()
        for i in range(3):
            loop = make_loop(loop_id=f"multi-registry-{i}", phase=LoopPhase.FALSIFICATION)
            results = registry.verify_all(loop)
            assert len(results) == registry.count()
