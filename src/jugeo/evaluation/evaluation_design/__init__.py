"""
evaluation_design: evaluation design package for JuGeo.

This package provides the core machinery for designing, running, and analyzing
evaluations of JuGeo-based systems.  It is grounded in the formal framework
of theory2.tex Chapter 63 (Evaluation Design and Verification).

Theory reference: theory2.tex Ch63
copilot: shared-core marker

Package architecture
--------------------
The evaluation_design package is organized into the following submodules:

* models          — Core data models: EvaluationDesign, ClauseResult,
                    AblationResult, CalibrationReport, EvaluationResult, etc.
* manifest        — Manifest tracking and registry for evaluation designs.
* clausewise_evaluation — Clausewise evaluation: ClauseSpecification,
                    ClausewiseScorer, scoring functions.
* ablation_design — Ablation design: AblationPlanner, AblationExecutor,
                    AblationAnalyzer.
* calibration_metrics — Calibration: ECE, MCE, Platt scaling, isotonic.
* algorithms      — Central algorithms: EvaluationAlgorithms, free functions.
* integration     — Integration with evidence, packs, orchestration, ideation,
                    geometry subsystems.
* theorems        — Formal theorems and the EvaluationTheoremRegistry.

Chapter 63 Background
---------------------
Chapter 63 of theory2.tex ("Evaluation Design and Verification") establishes
the formal framework for evaluating JuGeo systems.  The key contributions are:

1. Clausewise evaluation: decomposing evaluation into a set of clause-level
   criteria (soundness, completeness, consistency, precision, recall), each
   scored independently and then aggregated.

2. Ablation design: a systematic methodology for isolating the contribution
   of individual components through controlled removal experiments.

3. Calibration verification: ensuring that predicted confidence scores are
   consistent with empirical frequencies via Expected Calibration Error (ECE)
   and Maximum Calibration Error (MCE).

4. Formal theorems: soundness, ablation isolation, calibration consistency,
   clause completeness, and score monotonicity theorems that together provide
   guarantees about the evaluation procedure.

Usage examples
--------------
Basic clausewise evaluation::

    from jugeo.evaluation.evaluation_design import (
        EvaluationDesign, ClauseSpecification, run_clausewise_evaluation,
    )
    design = EvaluationDesign.create("my_evaluation")
    specs = [ClauseSpecification.create("soundness_check", ClauseType.SOUNDNESS, ...)]
    results = run_clausewise_evaluation(specs, system_output={"key": "value"})

Full evaluation pipeline::

    from jugeo.evaluation.evaluation_design import EvaluationAlgorithms
    result = EvaluationAlgorithms.run_full_evaluation(design, system_fn, predictions, labels)

Calibration measurement::

    from jugeo.evaluation.evaluation_design import compute_ece, measure_calibration
    ece = compute_ece(predictions=[0.9, 0.1, 0.7], labels=[1, 0, 1])
    report = measure_calibration(predictions, labels)

Ablation study::

    from jugeo.evaluation.evaluation_design import AblationPlanner, run_ablation
    planner = AblationPlanner()
    ablation_design = planner.plan(design, components=["retriever", "reasoner", "ranker"])
    ablation_results = run_ablation(ablation_design, system_fn)

Formal theorem verification::

    from jugeo.evaluation.evaluation_design import EvaluationTheoremRegistry
    outcomes = EvaluationTheoremRegistry.verify_all(context={"result": result, ...})
    latex_doc = EvaluationTheoremRegistry.to_latex_document()

Integration with JuGeo subsystems::

    from jugeo.evaluation.evaluation_design import FullEvaluationIntegration
    integration = FullEvaluationIntegration()
    integrated_result = integration.run_integrated_evaluation(design, system_fn, predictions, labels)
    report = integration.build_full_report(result)

Package-level utilities::

    from jugeo.evaluation.evaluation_design import get_package_info, _validate_package
    info = get_package_info()
    missing = _validate_package()
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Submodule imports (all guarded so the package degrades gracefully)
# ---------------------------------------------------------------------------

try:
    from .models import (
        EvaluationStatus, ClauseType, AblationKind, CalibrationMethod,
        EvaluationDesign, ClauseResult, AblationResult, CalibrationReport,
        EvaluationResult, ClausewiseEvaluator, AblationDesign,
    )
except Exception:
    pass

try:
    from .manifest import (
        EvaluationDesignManifest, EvaluationManifestBuilder,
        build_evaluation_manifest, validate_manifest, merge_manifests,
        EvaluationManifestRegistry,
    )
except Exception:
    pass

try:
    from .clausewise_evaluation import (
        ClauseSpecification, ClausewiseScorer, ClauseWeightCalculator,
        ClausewiseEvaluationRunner, run_clausewise_evaluation,
        aggregate_clause_scores,
    )
except Exception:
    pass

try:
    from .ablation_design import (
        AblationPlanner, AblationExecutor, AblationAnalyzer,
        AblationDesignRunner, design_ablation_study, run_ablation,
    )
except Exception:
    pass

try:
    from .calibration_metrics import (
        CalibrationMeasurer, CalibrationRecalibrator, ReliabilityDiagramBuilder,
        CalibrationMetricsRunner, measure_calibration, recalibrate,
    )
except Exception:
    pass

try:
    from .algorithms import (
        EvaluationAlgorithms,
        compute_ece, compute_precision_recall, compute_f1,
        compute_auc_roc, compute_brier_score, compute_consistency_score,
        compute_soundness_score,
        judgment_evaluation, descent_quality_score,
        evidence_completeness_score, encoding_evaluation,
        solver_performance_eval,
    )
except Exception:
    pass

try:
    from .integration import (
        EvaluationEvidenceIntegration, EvaluationPacksIntegration,
        EvaluationOrchestrationIntegration, EvaluationIdeationIntegration,
        EvaluationGeometryIntegration, FullEvaluationIntegration,
    )
except Exception:
    pass

try:
    from .theorems import (
        TheoremMetadata, EvaluationSoundnessTheorem,
        AblationIsolationTheorem, CalibrationConsistencyTheorem,
        ClauseCompletenessTheorem, ScoreMonotonicityTheorem,
        EvaluationTheoremRegistry,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # models
    "EvaluationStatus",
    "ClauseType",
    "AblationKind",
    "CalibrationMethod",
    "EvaluationDesign",
    "ClauseResult",
    "AblationResult",
    "CalibrationReport",
    "EvaluationResult",
    "ClausewiseEvaluator",
    "AblationDesign",
    # manifest
    "EvaluationDesignManifest",
    "EvaluationManifestBuilder",
    "build_evaluation_manifest",
    "validate_manifest",
    "merge_manifests",
    "EvaluationManifestRegistry",
    # clausewise_evaluation
    "ClauseSpecification",
    "ClausewiseScorer",
    "ClauseWeightCalculator",
    "ClausewiseEvaluationRunner",
    "run_clausewise_evaluation",
    "aggregate_clause_scores",
    # ablation_design
    "AblationPlanner",
    "AblationExecutor",
    "AblationAnalyzer",
    "AblationDesignRunner",
    "design_ablation_study",
    "run_ablation",
    # calibration_metrics
    "CalibrationMeasurer",
    "CalibrationRecalibrator",
    "ReliabilityDiagramBuilder",
    "CalibrationMetricsRunner",
    "measure_calibration",
    "recalibrate",
    # algorithms
    "EvaluationAlgorithms",
    "compute_ece",
    "compute_precision_recall",
    "compute_f1",
    "compute_auc_roc",
    "compute_brier_score",
    "compute_consistency_score",
    "compute_soundness_score",
    "judgment_evaluation",
    "descent_quality_score",
    "evidence_completeness_score",
    "encoding_evaluation",
    "solver_performance_eval",
    # integration
    "EvaluationEvidenceIntegration",
    "EvaluationPacksIntegration",
    "EvaluationOrchestrationIntegration",
    "EvaluationIdeationIntegration",
    "EvaluationGeometryIntegration",
    "FullEvaluationIntegration",
    # theorems
    "TheoremMetadata",
    "EvaluationSoundnessTheorem",
    "AblationIsolationTheorem",
    "CalibrationConsistencyTheorem",
    "ClauseCompletenessTheorem",
    "ScoreMonotonicityTheorem",
    "EvaluationTheoremRegistry",
    # package utilities
    "PackageInfo",
    "get_package_info",
    "_validate_package",
]

# ---------------------------------------------------------------------------
# Package utilities
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
import time
import uuid


def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp float.

    Returns:
        Seconds since the Unix epoch (UTC).
    """
    return time.time()


def _uid() -> str:
    """Return a fresh UUID4 string.

    Returns:
        A random UUID4 string suitable for use as a unique identifier.
    """
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class PackageInfo:
    """Metadata about the evaluation_design package.

    This frozen dataclass is returned by :func:`get_package_info` and provides
    a single place to look up package-level constants.

    Attributes:
        version: Semantic version string for this package release.
        chapter_ref: Reference to the theory chapter that grounds this package.
        description: One-paragraph description of the package's purpose.
        exports: Sorted list of all names exported via ``__all__``.
        created_at: Unix timestamp (float) when the PackageInfo was created.
    """

    version: str
    chapter_ref: str
    description: str
    exports: list[str]
    created_at: float


def get_package_info() -> PackageInfo:
    """Return metadata about the evaluation_design package.

    Constructs and returns a :class:`PackageInfo` instance describing the
    current state of the evaluation_design package.  The ``exports`` field
    is derived from the module's ``__all__`` list at call time.

    Returns:
        A frozen :class:`PackageInfo` dataclass with version, chapter_ref,
        description, exports, and created_at fields populated.

    Examples:
        >>> from jugeo.evaluation.evaluation_design import get_package_info
        >>> info = get_package_info()
        >>> print(info.version)
        '0.1.0'
        >>> print(info.chapter_ref)
        'theory2.tex Ch63'
    """
    description = (
        "evaluation_design provides the core machinery for designing, running, "
        "and analyzing evaluations of JuGeo-based systems.  It implements "
        "clausewise evaluation, ablation design, calibration verification, "
        "formal theorems, and integration with all JuGeo subsystems."
    )
    return PackageInfo(
        version="0.1.0",
        chapter_ref="theory2.tex Ch63",
        description=description,
        exports=sorted(__all__),
        created_at=_utcnow(),
    )


def _validate_package() -> list[str]:
    """Validate that all expected exports are importable.

    Iterates over the names in ``__all__`` and checks that each name is
    present in the current module's global namespace.  Names that cannot
    be resolved indicate that the corresponding submodule failed to import
    (typically because an optional dependency is absent).

    Returns:
        List of missing export name strings.  An empty list means all
        expected exports are present and importable.

    Examples:
        >>> from jugeo.evaluation.evaluation_design import _validate_package
        >>> missing = _validate_package()
        >>> if missing:
        ...     print(f"Missing exports: {missing}")
    """
    import sys
    this_module = sys.modules[__name__]
    missing: list[str] = []
    for name in __all__:
        if not hasattr(this_module, name):
            missing.append(name)
    return missing


# ===========================================================================
# SUBMODULE DEPENDENCY GRAPH
# ===========================================================================
#
# The evaluation_design package is structured so that lower-level modules never
# import from higher-level modules.  The dependency order is strictly:
#
#   models  <──────────────────────────────────────────────────────────────
#     │  (provides EvaluationDesign, ClauseResult, AblationResult, etc.)   │
#     ▼                                                                     │
#   manifest                                                                │
#     │  (imports EvaluationDesign from models;                             │
#     │   provides EvaluationDesignManifest and registry)                   │
#     ▼                                                                     │
#   clausewise_evaluation                                               │
#     │  (imports ClauseResult, ClauseType from models;                     │
#     │   provides ClauseSpecification, ClausewiseScorer)                   │
#     ▼                                                                     │
#   ablation_design                                                     │
#     │  (imports AblationResult, AblationKind from models;                 │
#     │   imports ClausewiseScorer from s01;                                │
#     │   provides AblationPlanner, AblationExecutor, AblationAnalyzer)     │
#     ▼                                                                     │
#   calibration_metrics                                                 │
#     │  (imports CalibrationReport, CalibrationMethod from models;         │
#     │   provides CalibrationMeasurer, CalibrationRecalibrator)            │
#     ▼                                                                     │
#   algorithms                                                              │
#     │  (imports from models, s01, s02, s03;                               │
#     │   provides EvaluationAlgorithms and all compute_* free functions)   │
#     ▼                                                                     │
#   integration                                                             │
#     │  (imports from algorithms and all subsystem packages;               │
#     │   provides *Integration classes and FullEvaluationIntegration)      │
#     ▼                                                                     │
#   theorems                                                                │
#     │  (imports from models, algorithms, integration;                     │
#     │   provides theorem classes and EvaluationTheoremRegistry)           │
#     ▼                                                                     │
#   __init__  (this file — re-exports everything, adds PackageInfo)        ─┘
#
# Circular imports are prevented by the strict layering above.  Any new
# submodule must respect this ordering: only import from modules that appear
# earlier in the graph.
#
# The ``try/except`` guards at the top of this file mean that a missing
# optional dependency in one submodule will not prevent the rest of the
# package from loading.  Call ``_validate_package()`` after import to check
# which names failed to resolve.
#


# ===========================================================================
# DATA FLOW
# ===========================================================================
#
# The evaluation pipeline moves data through the following stages:
#
# Stage 0 — Design construction
#   An EvaluationDesign is created (via EvaluationDesign.create or loaded
#   from a manifest).  It carries: an evaluation_id, a name, a list of
#   ClauseSpecifications, ablation component names, calibration settings,
#   and metadata such as theory_chapter_ref and created_at.
#
# Stage 1 — Clausewise scoring  (clausewise_evaluation)
#   Each ClauseSpecification defines a named criterion (e.g. SOUNDNESS,
#   COMPLETENESS, CONSISTENCY, PRECISION, RECALL).  The ClausewiseScorer
#   applies each criterion's scoring function to the system output and
#   produces a ClauseResult per specification.  ClauseWeightCalculator
#   computes normalized importance weights, and aggregate_clause_scores
#   combines the individual scores into a single weighted aggregate.
#
# Stage 2 — Ablation analysis  (ablation_design)
#   An AblationPlanner constructs an AblationDesign listing all component
#   removal combinations (full system, minus-one, minus-two, etc.).
#   AblationExecutor runs each ablated configuration through the system
#   function and collects AblationResult objects.  AblationAnalyzer
#   computes marginal contributions (delta scores) and ranks components
#   by their impact on overall performance.
#
# Stage 3 — Calibration verification  (calibration_metrics)
#   CalibrationMeasurer computes ECE (Expected Calibration Error) and
#   MCE (Maximum Calibration Error) over confidence-prediction pairs.
#   CalibrationRecalibrator applies Platt scaling or isotonic regression
#   to produce recalibrated confidence estimates.  ReliabilityDiagramBuilder
#   assembles the per-bin data needed for reliability diagrams.  The
#   CalibrationReport dataclass captures all metrics and recommendations.
#
# Stage 4 — Result aggregation  (algorithms)
#   EvaluationAlgorithms.run_full_evaluation orchestrates stages 1–3 and
#   merges the outputs into a single EvaluationResult.  The EvaluationResult
#   contains the clause_results list, ablation_results list,
#   calibration_report, and a top-level aggregate_score.
#
# Stage 5 — Integration  (integration)
#   FullEvaluationIntegration wraps the core pipeline with calls into the
#   evidence, packs, orchestration, ideation, and geometry subsystems.
#   Each subsystem integration class exposes a run_* method that enriches
#   the EvaluationResult with subsystem-specific metadata.
#
# Stage 6 — Theorem verification  (theorems)
#   After a full EvaluationResult is available, EvaluationTheoremRegistry
#   .verify_all(context) checks all five formal theorems against the result.
#   Each theorem returns a TheoremVerificationOutcome (PASS / FAIL / SKIP).
#   The registry can also render a LaTeX document summarizing the outcomes.
#


# ===========================================================================
# THEOREM VERIFICATION WORKFLOW
# ===========================================================================
#
# The evaluation_design package ships five formal theorems derived from
# theory2.tex Ch63.  Each theorem is a class implementing the interface:
#
#   class SomeTheorem:
#       metadata: TheoremMetadata          # name, label, statement, proof_sketch
#       def verify(self, context: dict) -> TheoremVerificationOutcome: ...
#       def to_latex(self) -> str: ...
#
# The five theorems are:
#
#   1. EvaluationSoundnessTheorem (Ch63 Theorem 63.1)
#      Asserts: if every ClauseResult passes its individual soundness check,
#      the aggregate EvaluationResult is sound.
#      Context keys required: "result" (EvaluationResult).
#
#   2. AblationIsolationTheorem (Ch63 Theorem 63.2)
#      Asserts: the marginal contribution computed by AblationAnalyzer
#      correctly isolates the effect of each removed component.
#      Context keys required: "ablation_results" (list[AblationResult]),
#      "baseline_score" (float).
#
#   3. CalibrationConsistencyTheorem (Ch63 Theorem 63.3)
#      Asserts: ECE < epsilon implies that predicted confidence and empirical
#      frequency agree within epsilon across all calibration bins.
#      Context keys required: "calibration_report" (CalibrationReport),
#      "epsilon" (float, default 0.05).
#
#   4. ClauseCompletenessTheorem (Ch63 Theorem 63.4)
#      Asserts: the set of ClauseSpecifications covers all five canonical
#      clause types (SOUNDNESS, COMPLETENESS, CONSISTENCY, PRECISION, RECALL).
#      Context keys required: "clause_specs" (list[ClauseSpecification]).
#
#   5. ScoreMonotonicityTheorem (Ch63 Theorem 63.5)
#      Asserts: adding more evidence or removing false positives cannot
#      decrease the aggregate score, i.e. the scoring functions are
#      monotone non-decreasing in relevant inputs.
#      Context keys required: "score_sequence" (list[float]).
#
# Workflow:
#   context = {"result": result, "ablation_results": ..., ...}
#   outcomes = EvaluationTheoremRegistry.verify_all(context)
#   for name, outcome in outcomes.items():
#       print(f"{name}: {outcome.status}")
#   latex_doc = EvaluationTheoremRegistry.to_latex_document()
#
# The registry also supports selective verification:
#   outcome = EvaluationTheoremRegistry.verify_one("soundness", context)
#
# Theorems that lack required context keys return status=SKIP with a
# human-readable reason string, so partial contexts are safe to pass.
#


# ===========================================================================
# INTEGRATION ARCHITECTURE
# ===========================================================================
#
# The integration submodule provides five subsystem-specific integration
# classes plus a FullEvaluationIntegration facade.
#
# 1. EvaluationEvidenceIntegration
#    Connects the evaluation pipeline to the JuGeo evidence subsystem.
#    Responsibilities:
#      - Loads evidence bundles associated with the evaluation design.
#      - Passes evidence sets as additional context to ClausewiseScorer.
#      - Attaches evidence provenance metadata to each ClauseResult.
#      - Validates that all clause specifications cite resolvable evidence IDs.
#    Key method: run_evidence_evaluation(design, system_fn, evidence_bundle)
#
# 2. EvaluationPacksIntegration
#    Connects the evaluation pipeline to the JuGeo packs subsystem.
#    Responsibilities:
#      - Retrieves the active pack configuration for the evaluation run.
#      - Ensures that ablation experiments use consistent pack snapshots.
#      - Records pack version hashes in the EvaluationResult metadata.
#      - Provides pack-level score breakdowns in the calibration report.
#    Key method: run_packs_evaluation(design, system_fn, pack_config)
#
# 3. EvaluationOrchestrationIntegration
#    Connects the evaluation pipeline to the orchestration subsystem.
#    Responsibilities:
#      - Schedules evaluation runs as orchestration tasks with priorities.
#      - Reports progress events back to the orchestration bus.
#      - Respects orchestration cancellation signals during long ablations.
#      - Persists intermediate results to the orchestration state store.
#    Key method: run_orchestrated_evaluation(design, system_fn, orchestrator)
#
# 4. EvaluationIdeationIntegration
#    Connects the evaluation pipeline to the ideation subsystem.
#    Responsibilities:
#      - Uses ideation-generated hypotheses as candidate evaluation criteria.
#      - Converts ideation output into ClauseSpecification objects.
#      - Feeds evaluation outcomes back into the ideation feedback loop.
#      - Generates new ablation component suggestions from low-scoring clauses.
#    Key method: run_ideation_evaluation(design, system_fn, ideation_context)
#
# 5. EvaluationGeometryIntegration
#    Connects the evaluation pipeline to the geometry subsystem.
#    Responsibilities:
#      - Maps clause scores into the geometric embedding space.
#      - Computes cosine similarity between clause-score vectors across runs.
#      - Detects score drift using geometric distance metrics.
#      - Produces 2-D reliability diagram projections via PCA or UMAP.
#    Key method: run_geometry_evaluation(design, system_fn, geometry_model)
#
# FullEvaluationIntegration composes all five integrations into a single
# pipeline.  It accepts an optional config dict that enables/disables each
# subsystem integration independently:
#
#   integration = FullEvaluationIntegration(config={
#       "evidence": True,
#       "packs": True,
#       "orchestration": False,
#       "ideation": True,
#       "geometry": False,
#   })
#   result = integration.run_integrated_evaluation(design, system_fn, preds, labels)
#


# ===========================================================================
# PERFORMANCE CONSIDERATIONS
# ===========================================================================
#
# Clausewise evaluation (s01) is typically the fastest stage because each
# scoring function is applied independently to a fixed system output dict.
# However, if scoring functions call external models or APIs, they should
# implement the optional async_score(output) coroutine interface so that
# ClausewiseEvaluationRunner can schedule them concurrently via asyncio.
#
# Ablation design (s02) is the most computationally expensive stage because
# it requires running the system_fn once per ablation configuration.  For
# N components the number of minus-one ablations is N, but full factorial
# ablations grow as 2^N.  AblationPlanner supports three strategies:
#   - "minus_one"   : N runs (one component removed at a time)
#   - "minus_two"   : N*(N-1)/2 runs (pairs removed)
#   - "full_factorial": 2^N runs (all subsets, use only for N <= 10)
# For large component sets, prefer "minus_one" or "minus_two".
# AblationExecutor supports parallel execution via a configurable
# max_workers parameter backed by concurrent.futures.ThreadPoolExecutor.
#
# Calibration metrics (s03) are O(n log n) in the number of predictions
# due to the sorting step in bin construction.  For datasets with more than
# 10^6 predictions, CalibrationMeasurer supports a streaming mode that
# accumulates bin statistics incrementally without storing all predictions.
#
# EvaluationAlgorithms.run_full_evaluation accepts a cache_dir parameter.
# When provided, it will serialize intermediate EvaluationResult objects to
# disk (via pickle) so that re-running after a partial failure resumes from
# the last completed stage rather than restarting from scratch.
#
# Memory usage is dominated by the ablation stage.  Each AblationResult
# stores the full system output for that configuration.  If system outputs
# are large, set AblationExecutor(store_outputs=False) to discard raw
# outputs and retain only aggregate scores.
#
# Theorem verification (theorems) is O(1) per theorem because each theorem
# operates on already-computed aggregates rather than raw data.  The most
# expensive theorem is CalibrationConsistencyTheorem when it recalibrates
# and re-measures ECE to verify the recalibration claim; this can be
# disabled by setting context["skip_recalibration_check"] = True.
#


# ===========================================================================
# EXTENSION POINTS
# ===========================================================================
#
# Adding a new ClauseType:
#   1. Add a new member to the ClauseType enum in models.py.
#   2. Implement a scoring function with signature:
#        def score_my_clause(output: dict, spec: ClauseSpecification) -> float
#   3. Register it in ClausewiseScorer._SCORING_REGISTRY:
#        ClausewiseScorer._SCORING_REGISTRY[ClauseType.MY_TYPE] = score_my_clause
#   4. Add a weight entry in ClauseWeightCalculator._DEFAULT_WEIGHTS.
#   5. Update ClauseCompletenessTheorem._REQUIRED_TYPES if your new type
#      should be required in every complete evaluation.
#
# Adding a new CalibrationMethod:
#   1. Add a new member to the CalibrationMethod enum in models.py.
#   2. Implement a recalibrator class with fit(probs, labels) and
#      transform(probs) methods.
#   3. Register it in CalibrationRecalibrator._METHOD_REGISTRY.
#   4. Optionally add a reliability diagram variant in
#      ReliabilityDiagramBuilder._DIAGRAM_BUILDERS.
#
# Adding a new Theorem:
#   1. Create a new class that implements the theorem protocol:
#        class MyNewTheorem:
#            metadata: TheoremMetadata
#            def verify(self, context: dict) -> TheoremVerificationOutcome: ...
#            def to_latex(self) -> str: ...
#   2. Register it with the registry:
#        EvaluationTheoremRegistry.register("my_theorem", MyNewTheorem())
#   3. Document the required context keys in the class docstring.
#
# Adding a new Integration:
#   1. Create a new class in the integration submodule that follows the
#      pattern of existing *Integration classes.
#   2. Expose a run_*_evaluation(design, system_fn, ...) method.
#   3. Add the new integration class to FullEvaluationIntegration and its
#      config dict with a boolean enable key.
#   4. Export the new class from integration/__init__.py and from here.
#
# Adding a new AblationKind:
#   1. Add a new member to the AblationKind enum in models.py.
#   2. Implement the combination-generation logic in AblationPlanner
#      under a new branch of the _generate_combinations method.
#   3. Update AblationDesignRunner to handle the new kind.
#


# ===========================================================================
# THEORY GROUNDING  (theory2.tex Ch63 Key Definitions)
# ===========================================================================
#
# Definition 63.1 — Evaluation Design
#   An evaluation design D = (C, A, K) is a triple where:
#     C = {c_1, ..., c_m} is the set of clause specifications,
#     A = {a_1, ..., a_n} is the set of ablation component names,
#     K is the calibration configuration (method, n_bins, epsilon).
#
# Definition 63.2 — Clause Score
#   For clause specification c_i with type tau_i and weight w_i,
#   the clause score is s_i = sigma_{tau_i}(output, c_i) in [0, 1],
#   where sigma_{tau} is the scoring function for type tau.
#   The aggregate clause score is S_C = sum_i w_i * s_i / sum_i w_i.
#
# Definition 63.3 — Ablation Contribution
#   Let score(config) denote the aggregate clause score of the system
#   running with component configuration config.  The marginal contribution
#   of component a_j is:
#     delta_j = score(A) - score(A \ {a_j})
#   where A is the full component set.  A component with delta_j > 0
#   contributes positively; delta_j < 0 indicates it is harmful.
#
# Definition 63.4 — Expected Calibration Error (ECE)
#   Partition predictions into B equal-width bins {B_1, ..., B_B}.
#   For each bin B_b, let acc(B_b) = mean label and conf(B_b) = mean
#   predicted probability over instances in B_b.
#   ECE = sum_b (|B_b| / n) * |acc(B_b) - conf(B_b)|
#   where n is the total number of predictions.
#
# Definition 63.5 — Maximum Calibration Error (MCE)
#   MCE = max_b |acc(B_b) - conf(B_b)|
#   MCE is a worst-case analog to ECE and is more sensitive to
#   extreme miscalibration in any single bin.
#
# Definition 63.6 — Calibration Consistency
#   A system is epsilon-calibrated if ECE < epsilon.  The canonical
#   threshold from Ch63 is epsilon = 0.05, i.e. mean absolute
#   bin-level miscalibration below 5 percentage points.
#
# Theorem 63.1 — Evaluation Soundness
#   If all clause scores s_i satisfy s_i >= threshold_i, then the
#   evaluation design certifies the system as sound under D.
#
# Theorem 63.2 — Ablation Isolation
#   Under the assumption that system components contribute independently
#   (no interaction effects), the marginal contribution delta_j correctly
#   measures the isolated effect of component a_j.
#
# Theorem 63.3 — Calibration Consistency Guarantee
#   If ECE < epsilon, then for any bin B_b:
#     |acc(B_b) - conf(B_b)| <= MCE <= ECE * B
#   so the per-bin miscalibration is bounded above by ECE * B.
#
# Theorem 63.4 — Clause Completeness
#   A complete evaluation design must include at least one clause
#   specification for each of the five canonical types: SOUNDNESS,
#   COMPLETENESS, CONSISTENCY, PRECISION, and RECALL.
#
# Theorem 63.5 — Score Monotonicity
#   For any two system outputs o1 and o2 where o2 dominates o1
#   component-wise, the clause scoring functions satisfy:
#     sigma_{tau}(o2, c) >= sigma_{tau}(o1, c)  for all tau, c.
#   This guarantees that strictly improving a system cannot lower
#   its evaluation score under any clause type.
#


# --- auto-registered submodules ---
try:
    from . import ablation_philosophy
except Exception:
    pass
try:
    from . import human_facing_evaluation
except Exception:
    pass
try:
    from . import project_scale_metrics
except Exception:
    pass
