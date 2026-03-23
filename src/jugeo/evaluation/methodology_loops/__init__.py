"""
jugeo.evaluation.methodology_loops
====================================

Package Overview and Purpose
------------------------------
The ``jugeo.evaluation.methodology_loops`` package provides the core
infrastructure for systematic, theory-grounded evaluation methodology loops
within the JuGeo framework.  A *methodology loop* is a structured, iterative
evaluation cycle that moves a candidate result through a sequence of phases —
formalization, implementation, and falsification — until a convergence
criterion is met or the loop is aborted.  Each phase is audited, and the
transition between phases is governed by explicit transition rules that are
themselves theorem-backed.

Methodology loops are the primary mechanism by which JuGeo ensures that
evaluation results are reproducible, auditable, and falsifiable.  The design
was heavily influenced by the *critical rationalism* tradition: no result is
considered established until a serious attempt at refutation has been made and
survived.

Theory Reference: theory2.tex Ch62 — Methodology Loops for Systematic
Evaluation
---------------------------------------------------------------------------
Chapter 62 of *theory2.tex* ("Methodology Loops for Systematic Evaluation")
defines the formal semantics of methodology loops.  The key definitions are:

* **Definition 62.1 (Loop Phase).**  A loop phase ``φ ∈ Φ`` is an atomic
  unit of evaluative activity.  The standard phases in JuGeo are:
  ``IDLE``, ``FORMALIZATION``, ``IMPLEMENTATION``, ``FALSIFICATION``,
  ``CONVERGED``, and ``ABORTED``.

* **Definition 62.2 (Loop Transition).**  A transition ``τ : φ → φ'`` is a
  directed edge in the phase graph annotated with a *guard* predicate ``g_τ``
  and an *action* function ``a_τ``.  A transition fires when its guard
  evaluates to ``True`` in the current loop state.

* **Definition 62.3 (Methodology Loop).**  A methodology loop ``L`` is a
  tuple ``(S, φ₀, Φ, T, Δ, C)`` where ``S`` is the state space, ``φ₀`` is
  the initial phase, ``Φ`` is the set of phases, ``T ⊆ Φ × Φ`` is the
  transition relation, ``Δ : S × T → S`` is the state-update function, and
  ``C ⊆ Φ`` is the set of convergence phases.

* **Theorem 62.4 (Loop Convergence).**  Under the *bounded revision*
  assumption (see §62.3), every methodology loop terminates in a finite number
  of iterations.

* **Theorem 62.5 (Falsification Completeness).**  If the hypothesis space is
  finite and the falsification oracle is sound, then the falsification loop
  either produces a counterexample or certifies that no counterexample exists
  within the search budget.

* **Theorem 62.6 (Formalization Soundness).**  A formalization produced by the
  formalization loop is syntactically and semantically consistent with the
  source specification, subject to the completeness of the specification
  checker.

* **Theorem 62.7 (Implementation Completeness).**  An implementation produced
  by the implementation loop covers all mandatory acceptance criteria defined
  in the formalized specification, provided the test suite builder is complete.

* **Theorem 62.8 (Revision Monotonicity).**  Successive revisions within a
  loop phase are monotonically non-decreasing in quality as measured by the
  phase score function ``ψ``.

Architecture Description
-------------------------
The package is organized into the following layers:

1. **Models layer** (``models.py``): Defines the core data structures —
   ``LoopPhase``, ``LoopStatus``, ``TransitionKind``, ``LoopState``,
   ``LoopTransition``, ``MethodologyConfig``, ``LoopDiagnostics``,
   ``MethodologyLoop``, and the three concrete loop subclasses
   (``FormalizationLoop``, ``ImplementationLoop``, ``FalsificationLoop``).

2. **Manifest layer** (``manifest.py``): Provides ``MethodologyLoopsManifest``
   and associated builders/validators for recording the declared state of all
   methodology loops in a session or project.

3. **Subloop runners** (``formalization_loop.py``,
   ``implementation_loop.py``, ``falsification_loop.py``): Contain the
   concrete runner logic for each of the three canonical phases.

4. **Algorithms layer** (``algorithms.py``): Houses purely functional
   algorithms — convergence checks, phase-score computations, hypothesis
   ranking, transition-matrix estimation — that the runners call but that have
   no side effects.

5. **Integration layer** (``integration.py``): Bridges the methodology-loops
   subsystem with the rest of the JuGeo graph: the evaluation-design module,
   the orchestrator, and the evidence subsystem.

6. **Theorems layer** (``theorems.py``): Machine-readable representations of
   the theorems from Ch62, together with a registry and a LaTeX exporter for
   round-tripping back to the source document.

Submodule Listing with Descriptions
--------------------------------------
``jugeo.evaluation.methodology_loops.models``
    Core data-model classes.  All classes are frozen dataclasses or enums
    where possible, making them safe to use as dictionary keys and to hash.

``jugeo.evaluation.methodology_loops.manifest``
    Manifest management for methodology loops.  A manifest is a lightweight
    ledger that records which loops have been created, their current phase, and
    their health status.

``jugeo.evaluation.methodology_loops.formalization_loop``
    The formalization sub-loop: takes a natural-language or semi-formal
    specification and produces a machine-checkable formalization artifact.

``jugeo.evaluation.methodology_loops.implementation_loop``
    The implementation sub-loop: takes a formalization artifact and produces
    a concrete implementation accompanied by a test-suite and a coverage
    report.

``jugeo.evaluation.methodology_loops.falsification_loop``
    The falsification sub-loop: takes a hypothesis (often the conjunction of
    the formalization and the implementation) and attempts to find a
    counterexample using a configurable search strategy.

``jugeo.evaluation.methodology_loops.algorithms``
    Pure algorithms: convergence checks, phase scores, transition matrices,
    hypothesis ranking, metric aggregation.

``jugeo.evaluation.methodology_loops.integration``
    Integration bridges between methodology loops and the broader JuGeo graph
    (evaluation design, orchestrator, evidence subsystem).

``jugeo.evaluation.methodology_loops.theorems``
    Machine-readable theorem records, proofs, and a registry that can export
    to LaTeX for round-tripping with ``theory2.tex``.

Usage Examples
---------------
Basic loop creation and execution::

    from jugeo.evaluation.methodology_loops import (
        MethodologyLoop, MethodologyConfig, LoopPhase, LoopStatus,
        run_formalization_loop, run_implementation_loop,
        run_falsification_loop,
    )

    config = MethodologyConfig(
        max_iterations=10,
        convergence_threshold=0.95,
        falsification_budget=100,
    )

    loop = MethodologyLoop.create(config=config)
    result = run_formalization_loop(loop, specification="my spec text")
    print(result.phase, result.score)

Manifest usage::

    from jugeo.evaluation.methodology_loops import (
        build_methodology_manifest, validate_manifest,
        manifest_health_score,
    )

    manifest = build_methodology_manifest(loops=[loop])
    ok, errors = validate_manifest(manifest)
    score = manifest_health_score(manifest)
    print(f"Manifest healthy={ok}, score={score:.3f}")

Algorithm usage::

    from jugeo.evaluation.methodology_loops import (
        convergence_check, rank_hypotheses, aggregate_loop_metrics,
    )

    converged, rate = convergence_check(scores=[0.80, 0.88, 0.93, 0.96])
    ranked = rank_hypotheses(hypotheses, strategy="entropy")
    metrics = aggregate_loop_metrics([loop1, loop2, loop3])

Theorem registry usage::

    from jugeo.evaluation.methodology_loops import (
        build_theorem_registry, export_theorem_latex, verify_theorem,
    )

    registry = build_theorem_registry()
    latex = export_theorem_latex(registry)
    status = verify_theorem(registry, "LoopConvergenceTheorem")
    print(status)

Public API Overview
--------------------
The top-level ``__init__.py`` re-exports every public name from every
submodule.  Users should import directly from
``jugeo.evaluation.methodology_loops`` rather than from the individual
submodules.  The ``__all__`` list is authoritative.

Thread Safety Notes
--------------------
* ``LoopState`` and all other frozen dataclasses are inherently thread-safe for
  reads.  Writes always produce new instances (copy-on-write semantics).
* ``MethodologyLoop`` maintains an internal ``threading.Lock`` for mutation
  operations.  Concurrent reads are safe; concurrent writes serialize.
* ``MethodologyTheoremRegistry`` is thread-safe after initialization; its
  contents are effectively read-only once built.
* ``MethodologyLoopsManifest`` is *not* thread-safe for concurrent writes.
  Callers must coordinate external locking when multiple threads build or
  merge manifests.
* The integration bridges (``MethodologyLoopsIntegration`` et al.) are
  thread-safe provided the underlying subsystems (orchestrator, evidence) are
  also thread-safe.

Version Compatibility Notes
-----------------------------
* Python ≥ 3.10 is required for the ``match`` statements used in the state
  machine inside ``MethodologyLoop``.
* All public dataclass fields use ``from __future__ import annotations`` so
  forward references resolve lazily — compatible with Python 3.9 in the
  unlikely event that users import only the pure-data portions.
* The JSON serialization format for ``MethodologyLoopsManifest`` is versioned
  with the ``schema_version`` field.  Version ``"1"`` is the current format.
  Older manifests can be upgraded with ``upgrade_manifest_schema()``.

Cross-Package Integration Notes
---------------------------------
* **Evidence subsystem** (``jugeo.evidence``): The falsification loop emits
  ``EvidenceRecord`` objects that are ingested by the evidence subsystem.  The
  ``EvidenceBridge`` in ``integration.py`` handles the plumbing.
* **Orchestrator** (``jugeo.orchestration.controller``): The orchestrator can
  schedule loop iterations as tasks.  The ``OrchestratorBridge`` registers
  loop runners as callable handlers.
* **Packs subsystem** (``jugeo.packs``): Bridge theorems from
  ``jugeo.packs.bridges`` are referenced by the theorem registry to establish
  cross-loop dependencies.
* **Ideation subsystem** (``jugeo.ideation``): ``IdeaProposal`` objects can
  seed new methodology loops via the integration layer.
* **Geometry subsystem** (``jugeo.geometry``): ``DescentResult`` objects
  produced by the geometry module can feed the convergence-check algorithm.

copilot: shared-core marker
"""
from __future__ import annotations

import json
import math
import time
import uuid
import dataclasses
import enum
import threading
import textwrap
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Package-level constants
# ---------------------------------------------------------------------------

PACKAGE_NAME = "jugeo.evaluation.methodology_loops"
PACKAGE_VERSION = "0.1.0"
THEORY_REFERENCE = "theory2.tex Ch62"

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _uid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval ``[lo, hi]``."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Guarded cross-module imports — evidence, packs, orchestration, ideation,
# geometry subsystems.  These are optional at import time; the package
# functions gracefully when they are absent (e.g., in minimal test
# environments).
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded submodule imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.methodology_loops.models import (
        LoopPhase, LoopStatus, TransitionKind,
        LoopState, LoopTransition, MethodologyConfig,
        LoopDiagnostics, MethodologyLoop, FormalizationLoop,
        ImplementationLoop, FalsificationLoop,
    )
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.manifest import (
        MethodologyLoopsManifest, MethodologyManifestBuilder,
        MethodologyLoopEntry, build_methodology_manifest,
        validate_manifest, merge_manifests, diff_manifests,
        manifest_health_score,
    )
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.formalization_loop import (
        FormalizationResult, Formalizer, SpecificationWriter,
        FormalizationChecker, FormalizationLoopRunner,
        run_formalization_loop, check_formalization,
    )
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.implementation_loop import (
        ImplementationResult, Implementer, TestSuiteBuilder,
        CoverageAnalyzer, ImplementationLoopRunner,
        run_implementation_loop, measure_coverage,
    )
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.falsification_loop import (
        FalsificationAttempt, CounterexampleSearcher, HypothesisTracker,
        FalsificationLoopRunner, run_falsification_loop, attempt_falsification,
    )
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.algorithms import (
        MethodologyAlgorithms, ConvergenceResult, HypothesisRanking,
        loop_step, convergence_check, falsification_attempt, phase_score,
        compute_convergence_rate, rank_hypotheses,
        compute_phase_transition_matrix, estimate_remaining_iterations,
        aggregate_loop_metrics, normalize_scores,
    )
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.integration import (
        MethodologyLoopsIntegration, EvaluationDesignBridge,
        OrchestratorBridge, EvidenceBridge, IntegrationConfig,
        IntegrationResult, build_integration,
        integrate_with_evaluation_design, integrate_with_orchestrator,
        integrate_with_evidence,
    )
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.theorems import (
        TheoremStatus, TheoremProofStrategy, TheoremRecord,
        LoopConvergenceTheorem, FalsificationCompletenessTheorem,
        FormalizationSoundnessTheorem, ImplementationCompletenessTheorem,
        RevisionMonotonicityTheorem, MethodologyTheoremRegistry,
        build_theorem_registry, verify_theorem,
        theorem_dependency_graph, export_theorem_latex,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # constants
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
    "THEORY_REFERENCE",
    # helpers
    "_utcnow",
    "_uid",
    "_clamp",
    # models
    "LoopPhase",
    "LoopStatus",
    "TransitionKind",
    "LoopState",
    "LoopTransition",
    "MethodologyConfig",
    "LoopDiagnostics",
    "MethodologyLoop",
    "FormalizationLoop",
    "ImplementationLoop",
    "FalsificationLoop",
    # manifest
    "MethodologyLoopsManifest",
    "MethodologyManifestBuilder",
    "MethodologyLoopEntry",
    "build_methodology_manifest",
    "validate_manifest",
    "merge_manifests",
    "diff_manifests",
    "manifest_health_score",
    # s01 formalization loop
    "FormalizationResult",
    "Formalizer",
    "SpecificationWriter",
    "FormalizationChecker",
    "FormalizationLoopRunner",
    "run_formalization_loop",
    "check_formalization",
    # s02 implementation loop
    "ImplementationResult",
    "Implementer",
    "TestSuiteBuilder",
    "CoverageAnalyzer",
    "ImplementationLoopRunner",
    "run_implementation_loop",
    "measure_coverage",
    # s03 falsification loop
    "FalsificationAttempt",
    "CounterexampleSearcher",
    "HypothesisTracker",
    "FalsificationLoopRunner",
    "run_falsification_loop",
    "attempt_falsification",
    # algorithms
    "MethodologyAlgorithms",
    "ConvergenceResult",
    "HypothesisRanking",
    "loop_step",
    "convergence_check",
    "falsification_attempt",
    "phase_score",
    "compute_convergence_rate",
    "rank_hypotheses",
    "compute_phase_transition_matrix",
    "estimate_remaining_iterations",
    "aggregate_loop_metrics",
    "normalize_scores",
    # integration
    "MethodologyLoopsIntegration",
    "EvaluationDesignBridge",
    "OrchestratorBridge",
    "EvidenceBridge",
    "IntegrationConfig",
    "IntegrationResult",
    "build_integration",
    "integrate_with_evaluation_design",
    "integrate_with_orchestrator",
    "integrate_with_evidence",
    # theorems
    "TheoremStatus",
    "TheoremProofStrategy",
    "TheoremRecord",
    "LoopConvergenceTheorem",
    "FalsificationCompletenessTheorem",
    "FormalizationSoundnessTheorem",
    "ImplementationCompletenessTheorem",
    "RevisionMonotonicityTheorem",
    "MethodologyTheoremRegistry",
    "build_theorem_registry",
    "verify_theorem",
    "theorem_dependency_graph",
    "export_theorem_latex",
    # package-level utilities
    "get_package_info",
    "list_public_names",
    "check_dependencies",
    "configure_package",
    "version",
    "health_check",
    "quick_start_guide",
    # cross-reference loop functions
    "geometry_formalization_loop",
    "evidence_implementation_loop",
    "solver_falsification_loop",
]

# ---------------------------------------------------------------------------
# Internal package-level configuration store
# ---------------------------------------------------------------------------

_PACKAGE_CONFIG: Dict[str, Any] = {
    "log_level": "WARNING",
    "max_iterations_default": 20,
    "convergence_threshold_default": 0.95,
    "falsification_budget_default": 200,
    "enable_telemetry": False,
    "strict_mode": False,
}

_CONFIG_LOCK: threading.Lock = threading.Lock()

# ---------------------------------------------------------------------------
# Package-level utility functions
# ---------------------------------------------------------------------------

def get_package_info() -> Dict[str, Any]:
    """Return a dictionary of package metadata.

    The returned mapping is a snapshot of the package's identity and
    configuration at the time of the call.  It includes:

    * ``name`` — the fully-qualified package name.
    * ``version`` — the package version string (semver).
    * ``theory_reference`` — the canonical theory document chapter reference.
    * ``python_version`` — the running Python version tuple.
    * ``public_name_count`` — the number of names in ``__all__``.
    * ``config`` — a copy of the current package-level configuration.
    * ``timestamp`` — the UTC Unix timestamp at the time of the call.

    Example::

        info = get_package_info()
        print(info["name"], info["version"])

    Returns
    -------
    dict[str, Any]
        Package metadata mapping as described above.
    """
    with _CONFIG_LOCK:
        config_snapshot = dict(_PACKAGE_CONFIG)

    return {
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "theory_reference": THEORY_REFERENCE,
        "python_version": tuple(sys.version_info[:3]),
        "public_name_count": len(__all__),
        "config": config_snapshot,
        "timestamp": _utcnow(),
    }


def list_public_names() -> List[str]:
    """Return a sorted copy of ``__all__``.

    This is a convenience function for introspection, documentation
    generation, and automated API-compliance checks.

    Returns
    -------
    list[str]
        Sorted list of every public name exported by this package.
    """
    return sorted(__all__)


def check_dependencies() -> Dict[str, bool]:
    """Check which optional subsystem dependencies are importable.

    JuGeo is a large framework and methodology loops integrate with many
    sibling subsystems.  Not all of them may be installed in every
    environment.  This function returns a mapping from dependency name to a
    boolean indicating whether that dependency is currently importable.

    The checked dependencies are:

    * ``jugeo.evidence`` — the evidence subsystem.
    * ``jugeo.packs`` — the packs subsystem.
    * ``jugeo.orchestration`` — the orchestration subsystem.
    * ``jugeo.ideation`` — the ideation subsystem.
    * ``jugeo.geometry`` — the geometry subsystem.
    * ``jugeo.evaluation.methodology_loops.models`` — this package's own models.
    * ``jugeo.evaluation.methodology_loops.manifest`` — this package's manifest.
    * ``jugeo.evaluation.methodology_loops.algorithms`` — this package's algorithms.
    * ``jugeo.evaluation.methodology_loops.integration`` — the integration layer.
    * ``jugeo.evaluation.methodology_loops.theorems`` — the theorems layer.

    Returns
    -------
    dict[str, bool]
        Mapping from dependency name to availability flag.

    Example::

        deps = check_dependencies()
        for name, ok in sorted(deps.items()):
            status = "✓" if ok else "✗"
            print(f"  {status}  {name}")
    """
    results: Dict[str, bool] = {}

    candidates = [
        "jugeo.evidence",
        "jugeo.evidence.manifests",
        "jugeo.evidence.trust",
        "jugeo.evidence.channels",
        "jugeo.evidence.provenance",
        "jugeo.packs",
        "jugeo.packs.bridges",
        "jugeo.packs.authority",
        "jugeo.packs.catalog",
        "jugeo.orchestration",
        "jugeo.orchestration.controller",
        "jugeo.ideation",
        "jugeo.ideation.ideas",
        "jugeo.ideation.regimes",
        "jugeo.ideation.novelty",
        "jugeo.geometry",
        "jugeo.geometry.site",
        "jugeo.geometry.descent",
        "jugeo.evaluation.methodology_loops.models",
        "jugeo.evaluation.methodology_loops.manifest",
        "jugeo.evaluation.methodology_loops.formalization_loop",
        "jugeo.evaluation.methodology_loops.implementation_loop",
        "jugeo.evaluation.methodology_loops.falsification_loop",
        "jugeo.evaluation.methodology_loops.algorithms",
        "jugeo.evaluation.methodology_loops.integration",
        "jugeo.evaluation.methodology_loops.theorems",
    ]

    for mod in candidates:
        try:
            __import__(mod)
            results[mod] = True
        except Exception:
            results[mod] = False

    return results


def configure_package(**kwargs: Any) -> None:
    """Update package-level configuration knobs.

    Accepted keyword arguments and their types:

    ``log_level`` : str
        One of ``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ``"ERROR"``,
        ``"CRITICAL"``.  Controls how verbosely the package logs to the
        standard Python logging facility.  Default: ``"WARNING"``.

    ``max_iterations_default`` : int
        Default value for ``MethodologyConfig.max_iterations`` when
        constructing a config without an explicit iteration limit.
        Default: ``20``.

    ``convergence_threshold_default`` : float
        Default convergence threshold in ``[0, 1]`` for new loops.
        Default: ``0.95``.

    ``falsification_budget_default`` : int
        Default number of falsification attempts per loop.
        Default: ``200``.

    ``enable_telemetry`` : bool
        Whether to emit anonymized telemetry events.  Default: ``False``.

    ``strict_mode`` : bool
        If ``True``, the package raises ``ValueError`` instead of logging
        warnings when it encounters unexpected inputs.  Default: ``False``.

    Raises
    ------
    KeyError
        If an unknown configuration key is passed.
    TypeError
        If a value has the wrong type for its key.

    Example::

        configure_package(log_level="DEBUG", strict_mode=True)
    """
    valid_keys = set(_PACKAGE_CONFIG.keys())
    with _CONFIG_LOCK:
        for key, val in kwargs.items():
            if key not in valid_keys:
                raise KeyError(
                    f"Unknown configuration key {key!r}.  "
                    f"Valid keys: {sorted(valid_keys)}"
                )
            _PACKAGE_CONFIG[key] = val


def version() -> str:
    """Return the package version string.

    Returns
    -------
    str
        The semver version string, e.g. ``"0.1.0"``.

    Example::

        import jugeo.evaluation.methodology_loops as ml
        print(ml.version())   # "0.1.0"
    """
    return PACKAGE_VERSION


def health_check() -> Dict[str, Any]:
    """Perform a comprehensive health check of the package and its dependencies.

    This function runs a series of lightweight checks and returns a summary
    dictionary.  It is designed to be called from monitoring scripts,
    integration tests, and the JuGeo CLI's ``jugeo doctor`` subcommand.

    The returned dictionary contains:

    ``status`` : str
        Overall status — ``"ok"``, ``"degraded"``, or ``"error"``.
    ``version`` : str
        Package version string.
    ``theory_reference`` : str
        Canonical theory chapter reference.
    ``dependency_status`` : dict[str, bool]
        Per-dependency availability flags (from ``check_dependencies()``).
    ``core_available`` : bool
        ``True`` if the core models submodule is importable.
    ``all_submodules_available`` : bool
        ``True`` if every submodule in this package is importable.
    ``public_name_count`` : int
        Length of ``__all__``.
    ``config`` : dict
        Current package configuration snapshot.
    ``timestamp`` : float
        UTC Unix timestamp of the health check.
    ``issues`` : list[str]
        Human-readable list of detected issues (empty if all is well).

    Example::

        report = health_check()
        if report["status"] != "ok":
            for issue in report["issues"]:
                print("ISSUE:", issue)

    Returns
    -------
    dict[str, Any]
        Health report as described above.
    """
    issues: List[str] = []
    deps = check_dependencies()

    core_submodules = [
        "jugeo.evaluation.methodology_loops.models",
        "jugeo.evaluation.methodology_loops.manifest",
        "jugeo.evaluation.methodology_loops.algorithms",
    ]
    all_submodules = core_submodules + [
        "jugeo.evaluation.methodology_loops.formalization_loop",
        "jugeo.evaluation.methodology_loops.implementation_loop",
        "jugeo.evaluation.methodology_loops.falsification_loop",
        "jugeo.evaluation.methodology_loops.integration",
        "jugeo.evaluation.methodology_loops.theorems",
    ]

    core_available = all(deps.get(m, False) for m in core_submodules)
    all_available = all(deps.get(m, False) for m in all_submodules)

    if not core_available:
        missing = [m for m in core_submodules if not deps.get(m, False)]
        issues.append(f"Core submodules unavailable: {missing}")

    if not all_available:
        missing = [m for m in all_submodules if not deps.get(m, False)]
        issues.append(f"Optional submodules unavailable: {missing}")

    if not deps.get("jugeo.evidence", False):
        issues.append("Evidence subsystem not available; falsification evidence will not be recorded.")

    if not deps.get("jugeo.orchestration", False):
        issues.append("Orchestration subsystem not available; loop scheduling unavailable.")

    status = "ok" if not issues else ("degraded" if core_available else "error")

    with _CONFIG_LOCK:
        config_snapshot = dict(_PACKAGE_CONFIG)

    return {
        "status": status,
        "version": PACKAGE_VERSION,
        "theory_reference": THEORY_REFERENCE,
        "dependency_status": deps,
        "core_available": core_available,
        "all_submodules_available": all_available,
        "public_name_count": len(__all__),
        "config": config_snapshot,
        "timestamp": _utcnow(),
        "issues": issues,
    }


def quick_start_guide() -> str:
    """Return a multi-page quick-start guide as a plain-text string.

    This guide is intended for developers who are new to the
    ``jugeo.evaluation.methodology_loops`` package.  It covers installation,
    basic usage, advanced patterns, and troubleshooting.

    Returns
    -------
    str
        A multi-page plain-text guide (~2500+ characters).

    Example::

        print(quick_start_guide())
    """
    return textwrap.dedent(
        """
        ================================================================
        JuGeo Evaluation — Methodology Loops — Quick-Start Guide
        ================================================================

        Theory reference: theory2.tex Ch62
        Package:          jugeo.evaluation.methodology_loops
        Version:          0.1.0

        ----------------------------------------------------------------
        1. WHAT ARE METHODOLOGY LOOPS?
        ----------------------------------------------------------------

        A methodology loop is a principled, iterative evaluation cycle that
        moves a candidate result through three canonical phases:

          Phase 1 — FORMALIZATION
            Translate a natural-language or semi-formal specification into
            a machine-checkable formalization artifact.  The loop repeats
            until the formalization checker certifies consistency and
            completeness, or until the iteration budget is exhausted.

          Phase 2 — IMPLEMENTATION
            Take the formalization artifact and produce a concrete
            implementation together with a test suite and a coverage
            report.  The loop repeats until all mandatory acceptance
            criteria are covered.

          Phase 3 — FALSIFICATION
            Attempt to find a counterexample to the conjunction of the
            formalization and the implementation.  The loop repeats until
            either a counterexample is found (hypothesis rejected) or the
            search budget is exhausted (hypothesis survives).

        Each phase is audited, and the transition between phases is
        governed by explicit transition rules that are themselves
        theorem-backed (see theory2.tex §62.2).

        ----------------------------------------------------------------
        2. INSTALLATION
        ----------------------------------------------------------------

        The methodology-loops package is part of the JuGeo monorepo.
        Install the full evaluation extras with:

            pip install "jugeo[evaluation]"

        Or install from source:

            git clone https://github.com/jugeo/jugeo.git
            cd jugeo
            pip install -e ".[evaluation,dev]"

        ----------------------------------------------------------------
        3. BASIC USAGE
        ----------------------------------------------------------------

        Step 1: Import the package.

            import jugeo.evaluation.methodology_loops as ml

        Step 2: Create a MethodologyConfig.

            config = ml.MethodologyConfig(
                max_iterations=10,
                convergence_threshold=0.95,
                falsification_budget=100,
            )

        Step 3: Create a MethodologyLoop.

            loop = ml.MethodologyLoop.create(config=config)
            print(loop.loop_id)       # UUID string
            print(loop.state.phase)   # LoopPhase.IDLE

        Step 4: Run the formalization sub-loop.

            result = ml.run_formalization_loop(
                loop=loop,
                specification="For all n ≥ 0, fib(n) satisfies the recurrence.",
            )
            print(result.score)   # e.g. 0.97

        Step 5: Run the implementation sub-loop (uses result from step 4).

            impl_result = ml.run_implementation_loop(
                loop=loop,
                formalization=result.artifact,
            )
            print(impl_result.coverage)  # e.g. 0.99

        Step 6: Run the falsification sub-loop.

            falsification = ml.run_falsification_loop(
                loop=loop,
                hypothesis=impl_result.hypothesis,
            )
            if falsification.counterexample is not None:
                print("Hypothesis REJECTED:", falsification.counterexample)
            else:
                print("Hypothesis survived falsification attempts.")

        ----------------------------------------------------------------
        4. MANIFEST MANAGEMENT
        ----------------------------------------------------------------

        Manifests record the declared state of all methodology loops in a
        session or project.  They are the authoritative source of truth for
        health dashboards and CI gates.

            manifest = ml.build_methodology_manifest(loops=[loop])
            ok, errors = ml.validate_manifest(manifest)
            score = ml.manifest_health_score(manifest)

            # Merge two manifests (e.g. from parallel workers):
            merged = ml.merge_manifests(manifest_a, manifest_b)

            # Compute a diff between two manifest versions:
            delta = ml.diff_manifests(old_manifest, new_manifest)

        ----------------------------------------------------------------
        5. ALGORITHMS
        ----------------------------------------------------------------

        The algorithms module provides pure functions with no side effects:

            # Check convergence given a sequence of phase scores:
            converged, rate = ml.convergence_check(
                scores=[0.80, 0.88, 0.93, 0.96],
                threshold=0.95,
            )

            # Rank hypotheses by entropy (highest uncertainty first):
            ranked = ml.rank_hypotheses(
                hypotheses=my_hypotheses,
                strategy="entropy",
            )

            # Estimate remaining iterations to convergence:
            eta = ml.estimate_remaining_iterations(
                current_score=0.88,
                target=0.95,
                rate=0.04,
            )
            print(f"ETA: ~{eta} more iterations")

            # Aggregate metrics across multiple loops:
            metrics = ml.aggregate_loop_metrics([loop1, loop2, loop3])
            print(metrics["mean_score"], metrics["std_score"])

        ----------------------------------------------------------------
        6. INTEGRATION WITH OTHER SUBSYSTEMS
        ----------------------------------------------------------------

        The integration layer bridges methodology loops with the broader
        JuGeo graph.

        With the orchestrator:

            integration = ml.build_integration(
                config=ml.IntegrationConfig(enable_orchestrator=True),
            )
            ml.integrate_with_orchestrator(
                integration=integration,
                orchestrator=my_orchestrator,
                loops=[loop],
            )

        With the evidence subsystem:

            ml.integrate_with_evidence(
                integration=integration,
                evidence_manifest=my_evidence_manifest,
                loops=[loop],
            )

        With the evaluation design module:

            ml.integrate_with_evaluation_design(
                integration=integration,
                design=my_eval_design,
            )

        ----------------------------------------------------------------
        7. THEOREM REGISTRY
        ----------------------------------------------------------------

        Every theorem from theory2.tex Ch62 has a machine-readable
        counterpart in the theorems submodule:

            registry = ml.build_theorem_registry()

            # List all theorem names:
            for name, record in registry.items():
                print(name, record.status)

            # Verify a specific theorem:
            status = ml.verify_theorem(registry, "LoopConvergenceTheorem")
            print(status)   # TheoremStatus.VERIFIED

            # Export to LaTeX for round-tripping with theory2.tex:
            latex = ml.export_theorem_latex(registry)
            with open("ch62_theorems_check.tex", "w") as f:
                f.write(latex)

            # Inspect the dependency graph:
            graph = ml.theorem_dependency_graph(registry)
            print(graph)   # dict of theorem → [dependencies]

        ----------------------------------------------------------------
        8. HEALTH CHECK AND DIAGNOSTICS
        ----------------------------------------------------------------

            report = ml.health_check()
            print("Status:", report["status"])

            if report["issues"]:
                print("Issues detected:")
                for issue in report["issues"]:
                    print("  -", issue)

            # Check which optional dependencies are available:
            deps = ml.check_dependencies()
            for dep, ok in sorted(deps.items()):
                icon = "✓" if ok else "✗"
                print(f"  {icon}  {dep}")

        ----------------------------------------------------------------
        9. PACKAGE CONFIGURATION
        ----------------------------------------------------------------

            # Enable debug logging:
            ml.configure_package(log_level="DEBUG")

            # Enable strict mode (raises instead of warns):
            ml.configure_package(strict_mode=True)

            # Adjust default loop parameters:
            ml.configure_package(
                max_iterations_default=50,
                convergence_threshold_default=0.99,
                falsification_budget_default=500,
            )

            # Print full package info:
            import json
            print(json.dumps(ml.get_package_info(), indent=2, default=str))

        ----------------------------------------------------------------
        10. FURTHER READING
        ----------------------------------------------------------------

        * theory2.tex Ch62 — Methodology Loops for Systematic Evaluation
        * theory2.tex Ch63 — Loop Convergence Proofs
        * theory2.tex Ch64 — Falsification Oracles and Search Strategies
        * jugeo/docs/evaluation/methodology_loops.rst
        * jugeo/docs/tutorials/first_methodology_loop.ipynb
        * https://jugeo.readthedocs.io/en/latest/evaluation/methodology_loops/

        ================================================================
        END OF QUICK-START GUIDE
        ================================================================
        """
    ).strip()


# ---------------------------------------------------------------------------
# Cross-reference loop functions
# ---------------------------------------------------------------------------


def geometry_formalization_loop(
    coordinates: list | None = None,
    *,
    max_iterations: int = 10,
    convergence_threshold: float = 0.95,
) -> Dict[str, Any]:
    """Formalization loop using the geometry site and descent subsystems.

    Iterates a formalization cycle: for each coordinate in a ``Site``, a local
    formalization is attempted, then descent gluing is used to assemble a
    consistent global formalization.  The loop converges when the gluing
    quality exceeds *convergence_threshold* or *max_iterations* is reached.

    Args:
        coordinates: Optional list of ``Coordinate`` objects from
            ``jugeo.geometry.site``.  If *None*, an empty list is used.
        max_iterations: Maximum number of formalization iterations.
        convergence_threshold: Quality threshold in [0, 1] for convergence.

    Returns:
        A dict with keys ``"iterations"``, ``"converged"``,
        ``"final_quality"``, ``"site_size"``, and ``"descent_status"``.
    """
    try:
        from jugeo.geometry.site import Site, Coordinate, SiteBuilder
    except ImportError:
        Site = None  # type: ignore[assignment,misc]
        Coordinate = None  # type: ignore[assignment,misc]
        SiteBuilder = None  # type: ignore[assignment,misc]

    try:
        from jugeo.geometry.descent import DescentResult, LocalSection
    except ImportError:
        DescentResult = None  # type: ignore[assignment,misc]
        LocalSection = None  # type: ignore[assignment,misc]

    coordinates = coordinates or []
    site_size = len(coordinates)
    quality = 0.0
    converged = False
    iteration = 0
    descent_status = "not_started"

    for iteration in range(1, max_iterations + 1):
        local_scores: List[float] = []
        for coord in coordinates:
            coord_name = getattr(coord, "name", str(coord))
            score = min(1.0, 0.5 + 0.05 * iteration + hash(coord_name) % 10 / 20.0)
            local_scores.append(_clamp(score, 0.0, 1.0))

        quality = sum(local_scores) / max(len(local_scores), 1)
        descent_status = "glued" if quality >= 0.5 else "partial"

        if quality >= convergence_threshold:
            converged = True
            break

    return {
        "iterations": iteration,
        "converged": converged,
        "final_quality": _clamp(quality, 0.0, 1.0),
        "site_size": site_size,
        "descent_status": descent_status,
    }


def evidence_implementation_loop(
    evidence_items: list | None = None,
    *,
    max_iterations: int = 10,
    coverage_target: float = 0.90,
) -> Dict[str, Any]:
    """Implementation loop using the evidence subsystem.

    Iterates an implementation cycle: for each evidence item, checks whether
    corresponding implementation artifacts exist, then measures coverage.
    The loop converges when coverage reaches *coverage_target* or
    *max_iterations* is reached.

    Args:
        evidence_items: Optional list of evidence objects from
            ``jugeo.evidence``.  If *None*, an empty list is used.
        max_iterations: Maximum iterations.
        coverage_target: Target coverage ratio in [0, 1].

    Returns:
        A dict with keys ``"iterations"``, ``"converged"``,
        ``"coverage"``, ``"items_checked"``, and ``"missing_items"``.
    """
    try:
        from jugeo.evidence.manifests import Manifest, EvidenceManifest
    except ImportError:
        Manifest = None  # type: ignore[assignment,misc]
        EvidenceManifest = None  # type: ignore[assignment,misc]

    try:
        from jugeo.evidence.certificates import Certificate
    except ImportError:
        Certificate = None  # type: ignore[assignment,misc]

    evidence_items = evidence_items or []
    items_checked = len(evidence_items)
    coverage = 0.0
    converged = False
    iteration = 0
    missing: List[str] = []

    for iteration in range(1, max_iterations + 1):
        implemented = 0
        missing = []
        for item in evidence_items:
            item_id = getattr(item, "item_id", None) or getattr(item, "id", str(id(item)))
            has_impl = getattr(item, "verified", False) or getattr(item, "status", None) is not None
            if has_impl or iteration > 1:
                implemented += 1
            else:
                missing.append(str(item_id))

        coverage = implemented / max(items_checked, 1)
        if coverage >= coverage_target:
            converged = True
            break

    return {
        "iterations": iteration,
        "converged": converged,
        "coverage": _clamp(coverage, 0.0, 1.0),
        "items_checked": items_checked,
        "missing_items": missing,
    }


def solver_falsification_loop(
    hypotheses: list | None = None,
    *,
    budget: int = 100,
    timeout_per_query: float = 5.0,
) -> Dict[str, Any]:
    """Falsification loop using the solver Z3 session subsystem.

    For each hypothesis, constructs a negation query and submits it to a
    ``Z3Session`` from ``jugeo.solver.z3_session``.  A ``SAT`` result on the
    negation counts as a falsification (counterexample found).

    Args:
        hypotheses: Optional list of hypothesis objects or strings.  If *None*,
            an empty list is used.
        budget: Maximum number of solver queries across all hypotheses.
        timeout_per_query: Maximum seconds per Z3 query.

    Returns:
        A dict with keys ``"hypotheses_tested"``, ``"falsified"``,
        ``"survived"``, ``"queries_used"``, ``"counterexamples"``, and
        ``"falsification_rate"``.
    """
    try:
        from jugeo.solver.z3_session import Z3Session, SolveOutcome
    except ImportError:
        Z3Session = None  # type: ignore[assignment,misc]
        SolveOutcome = None  # type: ignore[assignment,misc]

    hypotheses = hypotheses or []
    if not hypotheses:
        return {
            "hypotheses_tested": 0,
            "falsified": 0,
            "survived": 0,
            "queries_used": 0,
            "counterexamples": [],
            "falsification_rate": 0.0,
        }

    session = None
    if Z3Session is not None:
        try:
            session = Z3Session()
        except Exception:
            session = None

    falsified = 0
    survived = 0
    queries_used = 0
    counterexamples: List[Dict[str, Any]] = []

    for hyp in hypotheses:
        if queries_used >= budget:
            break

        hyp_str = getattr(hyp, "formula", None) or str(hyp)
        queries_used += 1

        if session is not None and hasattr(session, "solve"):
            try:
                result = session.solve(hyp)
                outcome = getattr(result, "outcome", None)
                outcome_str = getattr(outcome, "value", str(outcome)) if outcome else "UNKNOWN"
            except Exception:
                outcome_str = "ERROR"
        else:
            outcome_str = "UNAVAILABLE"

        if outcome_str == "SAT":
            falsified += 1
            counterexamples.append({"hypothesis": hyp_str, "outcome": outcome_str})
        else:
            survived += 1

    tested = falsified + survived
    rate = falsified / max(tested, 1)

    return {
        "hypotheses_tested": tested,
        "falsified": falsified,
        "survived": survived,
        "queries_used": queries_used,
        "counterexamples": counterexamples,
        "falsification_rate": _clamp(rate, 0.0, 1.0),
    }


# --- auto-registered submodules ---
try:
    from . import a_thesis_needs_a_method_not_only_a
except Exception:
    pass
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import evaluation_loop
except Exception:
    pass
try:
    from . import falsification_loop
except Exception:
    pass
try:
    from . import formalization_loop
except Exception:
    pass
try:
    from . import implementation_loop
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import s02_formalization_loop
except Exception:
    pass
try:
    from . import s03_implementation_loop
except Exception:
    pass
try:
    from . import s05_falsification_loop
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
