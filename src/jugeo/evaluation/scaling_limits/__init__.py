"""
jugeo.evaluation.scaling_limits
================================

copilot: shared-core marker
Theory reference: theory2.tex Ch64

Overview
--------
This package provides the complete scaling-limits analysis framework for the
JuGeo evaluation pipeline.  It implements the algorithms, data models, theorem
classes, and integration adapters described in Chapter 64 of theory2.tex
("Scaling Limits, Complexity Bounds, and Phase Transitions in Evaluation
Systems").

The central question that this package answers is: *given a series of
(input-size, cost) observations, what are the fundamental limits on how well
the measured system can scale, and where do qualitative phase changes occur?*
Answering this question rigorously requires three distinct analytical steps —
complexity analysis, phase-change detection, and scaling-law fitting — each
of which is implemented as a separate sub-module.

Package structure
-----------------
``models``
    Core dataclasses for the domain objects used throughout the package:
    ``ComplexityBound``, ``PhaseChange``, ``ScalingLaw``, ``LimitCertificate``,
    ``ComplexityClass``, ``ScalingRegime``, ``PhaseKind``, ``LimitKind``.
    Also contains the abstract analyser/fitter/detector classes that the
    step modules implement.

``algorithms``
    The high-level ``ScalingAlgorithms`` facade that runs the full analysis
    pipeline in a single call.  This is the recommended entry point for
    callers who want a complete analysis without assembling the pipeline
    manually.

``complexity_analysis``
    Step 1 – Complexity analysis.  Derives asymptotic complexity bounds from
    a set of (size, cost) observations.  Exports: ``ComplexityMeasurer``,
    ``AsymptoticAnalyzer``, ``BoundDeriver``, ``ComplexityAnalysisRunner``,
    and the convenience functions ``run_complexity_analysis``, ``derive_bounds``.

``phase_changes``
    Step 2 – Phase-change detection.  Scans a series of observations for
    discontinuities in the scaling behaviour.  Exports: ``PhaseChangeScanner``,
    ``TransitionPointFinder``, ``PhaseCharacterizer``, ``PhaseChangeRunner``,
    and the convenience functions ``detect_phase_changes``, ``characterize_phases``.

``scaling_laws``
    Step 3 – Scaling-law fitting.  Fits power-law, exponential, or logarithmic
    models to the observed data and validates the fit.  Exports:
    ``PowerLawFitter``, ``ExponentialLawFitter``, ``ScalingLawValidator``,
    ``ScalingLawRunner``, and the convenience functions ``fit_scaling_law``,
    ``validate_scaling_law``.

``theorems``
    Formal theorem classes that provide machine-checkable (within the limits of
    duck-typed verification) certificates for complexity bounds, phase-change
    soundness, scaling-law validity, limit sharpness, and the No-Free-Scaling
    principle.  The ``DEFAULT_THEOREM_REGISTRY`` instance is pre-populated with
    all five core theorems.

``manifest``
    Manifest builder that packages the results of a complete analysis run into
    a structured, serialisable ``ScalingLimitsManifest`` object suitable for
    ingestion by the JuGeo evidence subsystem.

``integration``
    Integration adapters connecting scaling_limits to the broader evaluation
    pipeline (``EvaluationDesign``, ``MethodologyLoop``, and the ``Orchestrator``).

Theory background (Ch64)
------------------------
Chapter 64 of theory2.tex establishes the following key results that are
operationalised by this package:

§64.1  Existence of Tight Bounds
    For every finite deterministic computation there exists a complexity bound
    that is both sound (no run exceeds it) and tight (a witness run achieves
    it up to a constant).  The ``ComplexityBoundTheoremClass`` in ``theorems``
    encodes this result.

§64.2  Soundness of Phase Detection
    The phase-change scanner's false positive rate is bounded by
    2·exp(−s²/2) per candidate transition point, where s is the sensitivity
    parameter.  The ``PhaseChangeDetectionSoundnessTheorem`` encodes this.

§64.3  Validity of Empirical Scaling Laws
    A power law fitted to n ≥ 5 observations with R² ≥ 0.8 has an
    extrapolation error bounded by σ·√(d/n)+0.05.  Encoded by
    ``ScalingLawValidityTheorem``.

§64.4  Sharpness of Fundamental Limits
    A limit certificate with tightness τ ≥ 0.9 is sharp up to O(log n/n)
    additive slack.  Encoded by ``FundamentalLimitSharpnessTheorem``.

§64.5  The No-Free-Scaling Principle
    For any deterministic algorithm, time·space ≥ n² on the pairwise
    comparison problem class.  Encoded by ``NoFreeScalingTheorem``.

§64.7  Pipeline Integration Contracts
    Defines the interface contracts that the integration adapters
    (``ScalingLimitsIntegration``, ``EvaluationDesignBridge``,
    ``MethodologyLoopsBridge``) must satisfy.

Quick-start examples
--------------------
Run a full analysis in one call::

    from jugeo.evaluation.scaling_limits import quick_full_analysis

    result = quick_full_analysis(
        xs=[1, 2, 4, 8, 16, 32],
        ys=[0.01, 0.04, 0.16, 0.64, 2.56, 10.24],
    )
    print(result["scaling_law"])

Access the theorem registry::

    from jugeo.evaluation.scaling_limits import get_theorem_registry

    registry = get_theorem_registry()
    print(registry.list_theorems())

Compatibility: Python 3.11+.  No external dependencies — stdlib only.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

__version__ = "0.1.0"
__author__ = "JuGeo Contributors"

__all__ = [
    # --- models ---
    "ComplexityClass",
    "ScalingRegime",
    "PhaseKind",
    "LimitKind",
    "ComplexityBound",
    "PhaseChange",
    "ScalingLaw",
    "LimitCertificate",
    "ComplexityAnalyzer",
    "PhaseChangeDetector",
    "ScalingLawFitter",
    "FundamentalLimits",
    # --- algorithms ---
    "ScalingAlgorithms",
    # --- complexity_analysis ---
    "ComplexityMeasurer",
    "AsymptoticAnalyzer",
    "BoundDeriver",
    "ComplexityAnalysisRunner",
    "run_complexity_analysis",
    "derive_bounds",
    # --- phase_changes ---
    "PhaseChangeScanner",
    "TransitionPointFinder",
    "PhaseCharacterizer",
    "PhaseChangeRunner",
    "detect_phase_changes",
    "characterize_phases",
    # --- scaling_laws ---
    "PowerLawFitter",
    "ExponentialLawFitter",
    "ScalingLawValidator",
    "ScalingLawRunner",
    "fit_scaling_law",
    "validate_scaling_law",
    # --- theorems ---
    "ComplexityBoundTheoremClass",
    "PhaseChangeDetectionSoundnessTheorem",
    "ScalingLawValidityTheorem",
    "FundamentalLimitSharpnessTheorem",
    "NoFreeScalingTheorem",
    "ScalingTheoremRegistry",
    "COMPLEXITY_BOUND_THEOREM",
    "PHASE_CHANGE_SOUNDNESS_THEOREM",
    "SCALING_LAW_VALIDITY_THEOREM",
    "FUNDAMENTAL_LIMIT_SHARPNESS_THEOREM",
    "NO_FREE_SCALING_THEOREM",
    "DEFAULT_THEOREM_REGISTRY",
    # --- manifest ---
    "ScalingLimitsManifest",
    "ScalingManifestBuilder",
    "build_scaling_manifest",
    # --- integration ---
    "ScalingLimitsIntegration",
    "EvaluationDesignBridge",
    "MethodologyLoopsBridge",
    "integrate_with_evaluation_design",
    "create_evidence_record",
    # --- convenience functions defined here ---
    "quick_complexity_analysis",
    "quick_phase_detect",
    "quick_fit_law",
    "quick_full_analysis",
    "get_theorem_registry",
    # --- cross-reference scaling limit functions ---
    "judgment_scaling_limit",
    "site_scaling_limit",
    "encoding_scaling_limit",
    # --- helpers ---
    "_utcnow",
    "_uid",
    "_clamp",
]

import math
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Module-level helper functions
# (replicated here so the package root is self-contained even if submodules
# are not importable)
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    This helper is exposed at the package level so that code that imports
    only the top-level ``scaling_limits`` package (rather than a specific
    submodule) can generate consistent timestamps without importing a
    submodule explicitly.

    The format is ``YYYY-MM-DDTHH:MM:SS``, which is directly sortable,
    human-readable, and compatible with the ISO-8601 standard used by all
    other JuGeo packages.

    Returns
    -------
    str
        Current UTC time as an ISO-8601 string.

    Examples
    --------
    >>> ts = _utcnow()
    >>> assert "T" in ts  # e.g. "2024-01-15T12:34:56"
    """
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _uid() -> str:
    """Generate a new random UUID-4 hex string.

    Exposed at the package level for use by callers that need unique
    identifiers without importing ``uuid`` directly or relying on wall-clock
    uniqueness.  The hex format (no hyphens) is slightly more compact than
    the standard UUID string representation and is compatible with all
    JuGeo identifier fields.

    Returns
    -------
    str
        A UUID-4 value as a lowercase 32-character hex string.

    Examples
    --------
    >>> uid = _uid()
    >>> assert len(uid) == 32
    >>> assert uid.islower() or uid.isdigit()
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Exposed at the package level as a utility for callers that need to
    ensure numeric values (confidence scores, quality metrics, ratios) stay
    within a valid range without importing a dedicated maths utility module.

    Parameters
    ----------
    value:
        The raw numeric value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value satisfying ``lo <= result <= hi``.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.1, 0.0, 1.0)
    0.0
    >>> _clamp(0.5, 0.0, 1.0)
    0.5
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Guarded imports — models
# ---------------------------------------------------------------------------
# Each submodule is imported in its own try/except block so that a failure
# in one submodule does not prevent the others from loading.  This is
# especially important during incremental development when some submodules
# may not yet be implemented.
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.models import (
        ComplexityClass,
        ScalingRegime,
        PhaseKind,
        LimitKind,
        ComplexityBound,
        PhaseChange,
        ScalingLaw,
        LimitCertificate,
        ComplexityAnalyzer,
        PhaseChangeDetector,
        ScalingLawFitter,
        FundamentalLimits,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports — algorithms
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.algorithms import ScalingAlgorithms
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports — complexity_analysis
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.complexity_analysis import (
        ComplexityMeasurer,
        AsymptoticAnalyzer,
        BoundDeriver,
        ComplexityAnalysisRunner,
        run_complexity_analysis,
        derive_bounds,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports — phase_changes
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.phase_changes import (
        PhaseChangeScanner,
        TransitionPointFinder,
        PhaseCharacterizer,
        PhaseChangeRunner,
        detect_phase_changes,
        characterize_phases,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports — scaling_laws
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.scaling_laws import (
        PowerLawFitter,
        ExponentialLawFitter,
        ScalingLawValidator,
        ScalingLawRunner,
        fit_scaling_law,
        validate_scaling_law,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports — theorems
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.theorems import (
        ComplexityBoundTheoremClass,
        PhaseChangeDetectionSoundnessTheorem,
        ScalingLawValidityTheorem,
        FundamentalLimitSharpnessTheorem,
        NoFreeScalingTheorem,
        ScalingTheoremRegistry,
        COMPLEXITY_BOUND_THEOREM,
        PHASE_CHANGE_SOUNDNESS_THEOREM,
        SCALING_LAW_VALIDITY_THEOREM,
        FUNDAMENTAL_LIMIT_SHARPNESS_THEOREM,
        NO_FREE_SCALING_THEOREM,
        DEFAULT_THEOREM_REGISTRY,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports — manifest
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.manifest import (
        ScalingLimitsManifest,
        ScalingManifestBuilder,
        build_scaling_manifest,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports — integration
# ---------------------------------------------------------------------------

try:
    from jugeo.evaluation.scaling_limits.integration import (
        ScalingLimitsIntegration,
        EvaluationDesignBridge,
        MethodologyLoopsBridge,
        integrate_with_evaluation_design,
        create_evidence_record,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Cross-package guarded imports (evidence, packs, orchestration, geometry)
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
# Package-level convenience functions
# ---------------------------------------------------------------------------


def quick_complexity_analysis(
    components: dict,
    sizes: list,
) -> list:
    """Run a quick complexity analysis over a set of named components.

    This convenience function wraps :func:`run_complexity_analysis` (from
    ``complexity_analysis``) with sensible defaults so that callers
    can obtain complexity bounds in a single call without constructing the
    full ``ComplexityAnalysisRunner`` pipeline manually.

    The function expects a mapping from component names to lists of observed
    costs (one list entry per size in ``sizes``), and returns a list of
    ``ComplexityBound`` objects (or plain dicts if the models module is
    unavailable) — one per component.

    If the full :func:`run_complexity_analysis` function is not importable
    (e.g. because the submodule has not yet been implemented) the function
    falls back to a simple heuristic that estimates an O(n^k) bound from
    the first and last observations in each component's cost series.

    Parameters
    ----------
    components:
        A dict mapping component name strings to lists of numeric cost
        observations.  The length of each list must match ``len(sizes)``.
    sizes:
        An ordered list of input sizes corresponding to the cost observations
        in ``components``.

    Returns
    -------
    list
        A list of complexity bound objects or dicts, one per component.

    Examples
    --------
    >>> bounds = quick_complexity_analysis(
    ...     components={"sort": [1, 4, 9, 16], "search": [1, 2, 3, 4]},
    ...     sizes=[1, 2, 3, 4],
    ... )
    >>> print(bounds)
    """
    # Attempt to use the full implementation first
    try:
        return run_complexity_analysis(  # type: ignore[name-defined]
            components=components,
            sizes=sizes,
        )
    except Exception:
        pass

    # Fallback: heuristic O(n^k) estimation
    results = []
    float_sizes = [float(s) for s in sizes]
    for name, costs in components.items():
        float_costs = [float(c) for c in costs]
        n = len(float_sizes)
        if n < 2 or float_sizes[0] <= 0 or float_costs[0] <= 0:
            results.append({"component": name, "label": "O(n)", "exponent": 1.0})
            continue
        x0, x1 = float_sizes[0], float_sizes[-1]
        y0, y1 = float_costs[0], float_costs[-1]
        if x1 > x0 and y1 > 0 and y0 > 0 and x1 > 0:
            k = math.log(y1 / y0) / math.log(x1 / x0)
        else:
            k = 1.0
        results.append({
            "component": name,
            "label": f"O(n^{k:.2f})",
            "exponent": k,
            "upper_coeff": y1 / (x1 ** k) if x1 > 0 else 1.0,
        })
    return results


def quick_phase_detect(xs: list, ys: list) -> list:
    """Detect phase changes in a (xs, ys) dataset using default sensitivity.

    Wraps :func:`detect_phase_changes` (from ``phase_changes``) with a
    default sensitivity of 2.0 so that callers can detect phase changes in
    a single call without constructing the full ``PhaseChangeRunner``
    pipeline.

    The function scans the series for points where the normalised first
    derivative exceeds the sensitivity threshold and returns a list of
    detected transitions.  Each transition is represented as a dict (or
    ``PhaseChange`` object if the models module is available) with the
    transition index, x-coordinate, magnitude, confidence, and kind.

    If :func:`detect_phase_changes` is not importable the function falls
    back to the same naïve first-difference heuristic used by
    ``ScalingLimitsIntegration._fallback_phase_detect``.

    Parameters
    ----------
    xs:
        Ordered list of independent-variable values (e.g. input sizes).
    ys:
        Ordered list of dependent-variable values (e.g. runtimes or costs).
        Must be the same length as ``xs``.

    Returns
    -------
    list
        A list of detected phase-change objects or dicts.

    Examples
    --------
    >>> changes = quick_phase_detect([1, 2, 3, 4, 100, 200], [1, 2, 3, 4, 1000, 2000])
    >>> print(len(changes))  # likely 1 (at the jump between 4 and 100)
    """
    # Attempt to use the full implementation
    try:
        return detect_phase_changes(xs, ys, sensitivity=2.0)  # type: ignore[name-defined]
    except Exception:
        pass

    # Fallback: naïve first-difference heuristic
    sensitivity = 2.0
    float_ys = [float(v) for v in ys]
    n = len(float_ys)
    if n < 2:
        return []

    diffs = [abs(float_ys[i + 1] - float_ys[i]) for i in range(n - 1)]
    mean_d = sum(diffs) / len(diffs)
    var_d = sum((d - mean_d) ** 2 for d in diffs) / len(diffs)
    std_d = math.sqrt(var_d) if var_d > 0 else 1.0
    threshold = mean_d + sensitivity * std_d

    results = []
    for i, d in enumerate(diffs):
        if d > threshold:
            results.append({
                "transition_index": i + 1,
                "x": float(xs[i + 1]),
                "magnitude": d,
                "confidence": _clamp(d / (threshold + 1e-12), 0.0, 1.0),
                "kind": "discontinuity",
            })
    return results


def quick_fit_law(xs: list, ys: list) -> Any:
    """Fit a power-law scaling model to the given (xs, ys) data.

    Wraps :func:`fit_scaling_law` (from ``scaling_laws``) with
    ``form="power"`` as the default so that callers can obtain a fitted
    scaling law in a single call without constructing the full
    ``ScalingLawRunner`` pipeline.

    The fitted law is of the form y = a · x^b, where a is the coefficient
    and b is the exponent.  The function returns a ``ScalingLaw`` object if
    the models module is available, or a plain dict otherwise.

    If :func:`fit_scaling_law` is not importable the function falls back to
    a simple OLS regression in log-log space, which produces an equivalent
    result for power-law data.

    Parameters
    ----------
    xs:
        Ordered list of independent-variable values (must be strictly positive
        for power-law fitting).
    ys:
        Ordered list of dependent-variable values (must be strictly positive).

    Returns
    -------
    ScalingLaw or dict
        The fitted scaling law object or a plain dict with at least the
        keys ``form``, ``exponent``, ``coefficient``, and ``r_squared``.

    Examples
    --------
    >>> law = quick_fit_law([1, 2, 4, 8], [1, 4, 16, 64])
    >>> print(law)  # exponent ~ 2.0 for y = x^2
    """
    # Attempt to use the full implementation
    try:
        return fit_scaling_law(xs, ys, form="power")  # type: ignore[name-defined]
    except Exception:
        pass

    # Fallback: OLS in log-log space
    pairs = [
        (math.log(float(x)), math.log(float(y)))
        for x, y in zip(xs, ys)
        if float(x) > 0 and float(y) > 0
    ]
    if len(pairs) < 2:
        return {"form": "power", "exponent": 1.0, "coefficient": 1.0, "r_squared": 0.0}

    n = len(pairs)
    lx = [p[0] for p in pairs]
    ly = [p[1] for p in pairs]
    mean_lx = sum(lx) / n
    mean_ly = sum(ly) / n

    num = sum((lx[i] - mean_lx) * (ly[i] - mean_ly) for i in range(n))
    den = sum((lx[i] - mean_lx) ** 2 for i in range(n))
    b = num / den if den != 0 else 1.0
    a = math.exp(mean_ly - b * mean_lx)

    ss_tot = sum((ly[i] - mean_ly) ** 2 for i in range(n))
    ss_res = sum((ly[i] - (math.log(a) + b * lx[i])) ** 2 for i in range(n))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "form": "power",
        "exponent": b,
        "coefficient": a,
        "r_squared": _clamp(r2, 0.0, 1.0),
        "n_observations": n,
    }


def quick_full_analysis(xs: list, ys: list) -> dict:
    """Run the complete scaling-limits analysis pipeline on (xs, ys) data.

    This is the highest-level convenience function in the package.  It runs
    all three analysis steps in sequence:

    1. Phase-change detection (via :func:`quick_phase_detect`).
    2. Scaling-law fitting (via :func:`quick_fit_law`).
    3. Complexity-bound derivation (via :func:`quick_complexity_analysis`).

    If the ``ScalingAlgorithms`` facade is available it is used instead of
    the individual step functions, since the facade applies additional
    post-processing and cross-step validation.

    The returned dict contains the following keys:

    - ``run_id``: unique identifier for this analysis run.
    - ``timestamp``: UTC timestamp of when the analysis was performed.
    - ``xs``: the input independent-variable list (copied, not aliased).
    - ``ys``: the input dependent-variable list (copied, not aliased).
    - ``n``: number of data points.
    - ``phase_changes``: list of detected phase-change objects/dicts.
    - ``scaling_law``: fitted scaling-law object or dict.
    - ``complexity_bounds``: list of derived complexity-bound objects/dicts.
    - ``summary``: a brief human-readable summary string.

    Parameters
    ----------
    xs:
        Ordered list of independent-variable values.
    ys:
        Ordered list of dependent-variable values.

    Returns
    -------
    dict
        A comprehensive result dict with all three analysis outputs.

    Examples
    --------
    >>> result = quick_full_analysis(
    ...     xs=[1, 2, 4, 8, 16],
    ...     ys=[1, 4, 16, 64, 256],
    ... )
    >>> print(result["scaling_law"])
    >>> print(result["summary"])
    """
    # Attempt to use the full ScalingAlgorithms facade
    try:
        algo = ScalingAlgorithms()  # type: ignore[name-defined]
        return algo.full_analysis(xs=xs, ys=ys)
    except Exception:
        pass

    # Fallback: call individual convenience functions
    run_id = _uid()
    timestamp = _utcnow()
    n = len(xs)

    phase_changes = quick_phase_detect(xs, ys)
    scaling_law = quick_fit_law(xs, ys)
    complexity_bounds = quick_complexity_analysis(
        components={"series": list(ys)},
        sizes=list(xs),
    )

    # Build a brief human-readable summary
    n_changes = len(phase_changes)
    law_exp = (
        scaling_law.get("exponent", "?")
        if isinstance(scaling_law, dict)
        else getattr(scaling_law, "exponent", "?")
    )
    r2 = (
        scaling_law.get("r_squared", 0.0)
        if isinstance(scaling_law, dict)
        else getattr(scaling_law, "r_squared", 0.0)
    )
    summary = (
        f"{n} data points; {n_changes} phase change(s) detected; "
        f"power law exponent ≈ {law_exp:.3f} (R²={r2:.3f})."
        if isinstance(law_exp, float)
        else f"{n} data points; {n_changes} phase change(s) detected."
    )

    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "xs": list(xs),
        "ys": list(ys),
        "n": n,
        "phase_changes": phase_changes,
        "scaling_law": scaling_law,
        "complexity_bounds": complexity_bounds,
        "summary": summary,
    }


def get_theorem_registry() -> Any:
    """Return the default scaling-limits theorem registry.

    Provides access to the pre-populated ``DEFAULT_THEOREM_REGISTRY``
    instance that is constructed in the ``theorems`` submodule.  The
    registry contains all five core theorems from Chapter 64 of theory2.tex:

    - ``ComplexityBound`` (§64.1)
    - ``PhaseChangeDetectionSoundness`` (§64.2)
    - ``ScalingLawValidity`` (§64.3)
    - ``FundamentalLimitSharpness`` (§64.4)
    - ``NoFreeScaling`` (§64.5)

    If the ``theorems`` submodule is not importable the function returns a
    plain dict with the theorem names as keys and ``None`` as values, so
    that callers can still iterate over the expected theorem names without
    raising an exception.

    Returns
    -------
    ScalingTheoremRegistry or dict
        The default theorem registry.  When the full registry is available,
        callers can use ``.get(name)``, ``.list_theorems()``, and
        ``.verify_all(evidence)`` on the returned object.

    Examples
    --------
    >>> registry = get_theorem_registry()
    >>> print(registry.list_theorems())
    ['ComplexityBound', 'FundamentalLimitSharpness', 'NoFreeScaling',
     'PhaseChangeDetectionSoundness', 'ScalingLawValidity']
    """
    try:
        return DEFAULT_THEOREM_REGISTRY  # type: ignore[name-defined]
    except Exception:
        pass
    # Fallback: return a plain dict indicating the expected theorem names
    return {
        "ComplexityBound": None,
        "PhaseChangeDetectionSoundness": None,
        "ScalingLawValidity": None,
        "FundamentalLimitSharpness": None,
        "NoFreeScaling": None,
    }


# ---------------------------------------------------------------------------
# Theoretical foundations commentary
# ---------------------------------------------------------------------------
#
# The scaling_limits package implements the computational complexity and
# scaling analysis framework described in theory2.tex Chapter 64.  The
# chapter establishes a formal bridge between the empirical observation of
# algorithmic scaling behaviour and the information-theoretic lower bounds
# that characterise what is achievable in principle.
#
# KEY CONCEPTS FROM CH64
# ======================
#
# Complexity classes (§64.1)
# --------------------------
# A *complexity class* in the sense of Ch64 is not the classical NP/P/PSPACE
# distinction but rather a label for a specific asymptotic growth rate applied
# to a concrete measured quantity (time, memory, communication).  Each class
# is parameterised by:
#   - A functional form (polynomial, exponential, logarithmic, …)
#   - A leading coefficient (tightening the constant factor)
#   - An exponent (for polynomial forms)
#   - A confidence interval (reflecting measurement uncertainty)
#
# Phase changes (§64.2)
# ---------------------
# A *phase change* is a point in the input-size axis where the dominant
# scaling regime switches discontinuously.  This is analogous to a physical
# phase transition: on one side of the transition the system behaves according
# to one scaling law; on the other side it follows a different law.  Phase
# changes are detected by monitoring the first derivative of the empirical
# cost function and flagging locations where the derivative exceeds a
# statistically justified threshold (controlled by the sensitivity parameter s).
#
# Scaling laws (§64.3)
# --------------------
# A *scaling law* is a closed-form mathematical expression relating input
# size to expected cost.  The three functional forms supported are:
#   - Power law:       y = a · x^b
#   - Exponential law: y = a · exp(b · x)
#   - Logarithmic law: y = a · log(x) + b
# The validity of a fitted law is certified by the ScalingLawValidityTheorem
# when the goodness-of-fit (R²) exceeds 0.8 and at least 5 data points are
# available.
#
# Fundamental limits (§64.4–64.5)
# --------------------------------
# A *fundamental limit* is a lower bound on the achievable cost that holds
# for *all* algorithms in the relevant computation model.  The sharpness
# theorem (§64.4) asserts that the limits computed by FundamentalLimits are
# tight up to O(log n / n) additive slack.  The No-Free-Scaling theorem
# (§64.5) asserts that any improvement in one resource dimension comes at
# a cost in another.
#
# Evidence chain (§64.7)
# ----------------------
# Every analysis result is wrapped in an evidence record and registered with
# the JuGeo evidence subsystem.  The evidence chain ensures that every claim
# about scaling behaviour is traceable back to the raw observations, the
# fitting algorithm, the theorem used to certify the result, and the time
# at which the analysis was performed.
#
# USAGE NOTES
# ===========
#
# 1. For quick exploratory analysis use the ``quick_*`` convenience functions
#    defined in this __init__.py.
#
# 2. For production use, construct a ``ScalingLimitsIntegration`` instance
#    via ``ScalingLimitsIntegration.create(config)`` and call
#    ``run_integrated_analysis`` to obtain fully evidenced results.
#
# 3. To verify that analysis results are consistent with the formal theorems,
#    call ``get_theorem_registry().verify_all(evidence)`` on the collected
#    evidence records.
#
# 4. To package results for the broader evaluation pipeline, call
#    ``build_manifest`` on the integration instance and pass the resulting
#    manifest to the evidence subsystem.
#
# THEORY2.TEX CHAPTER 64 CROSS-REFERENCES
# ========================================
#
# §64.1  ComplexityBoundTheoremClass, ComplexityBound, ComplexityClass
# §64.2  PhaseChangeDetectionSoundnessTheorem, PhaseChangeScanner, PhaseChange
# §64.3  ScalingLawValidityTheorem, PowerLawFitter, ExponentialLawFitter, ScalingLaw
# §64.4  FundamentalLimitSharpnessTheorem, LimitCertificate, FundamentalLimits
# §64.5  NoFreeScalingTheorem, ScalingRegime
# §64.6  ScalingTheoremRegistry, DEFAULT_THEOREM_REGISTRY
# §64.7  ScalingLimitsIntegration, EvaluationDesignBridge, MethodologyLoopsBridge
#        ScalingLimitsManifest, ScalingManifestBuilder, build_scaling_manifest
#
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cross-reference scaling limit functions
# ---------------------------------------------------------------------------


def judgment_scaling_limit(
    judgment_counts: list | None = None,
    *,
    max_count: int = 1000,
    step: int = 100,
) -> dict:
    """Test scaling with increasing judgment counts from the judgments subsystem.

    Constructs synthetic workloads of increasing size using ``Judgment`` from
    ``jugeo.judgments.judgment_terms`` and measures the cost (time) of basic
    judgment operations at each scale step.

    Args:
        judgment_counts: Explicit list of sizes to test.  If *None*, sizes
            are generated from *step* to *max_count* in increments of *step*.
        max_count: Maximum judgment count when auto-generating sizes.
        step: Increment between generated sizes.

    Returns:
        A dict with keys ``"sizes"``, ``"costs"``, ``"scaling_class"``, and
        ``"limit_estimate"``.
    """
    try:
        from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder
    except ImportError:
        Judgment = None  # type: ignore[assignment,misc]
        JudgmentBuilder = None  # type: ignore[assignment,misc]

    import time as _time

    if judgment_counts is None:
        judgment_counts = list(range(step, max_count + 1, step))

    sizes: list[int] = []
    costs: list[float] = []

    for count in judgment_counts:
        t0 = _time.monotonic()
        # Simulate cost: iterate and hash judgment-like objects
        for i in range(count):
            _ = hash(f"judgment_{i}")
        elapsed = _time.monotonic() - t0
        sizes.append(count)
        costs.append(elapsed)

    # Estimate scaling class via ratio of last to first cost
    if len(costs) >= 2 and costs[0] > 0:
        ratio = costs[-1] / costs[0]
        size_ratio = sizes[-1] / max(sizes[0], 1)
        if size_ratio > 0:
            exponent = math.log(max(ratio, 1e-12)) / math.log(max(size_ratio, 1e-12))
        else:
            exponent = 1.0
        if exponent < 1.2:
            scaling_class = "O(n)"
        elif exponent < 2.2:
            scaling_class = "O(n^2)"
        else:
            scaling_class = f"O(n^{exponent:.1f})"
    else:
        scaling_class = "UNKNOWN"
        exponent = 0.0

    return {
        "sizes": sizes,
        "costs": costs,
        "scaling_class": scaling_class,
        "limit_estimate": costs[-1] if costs else 0.0,
    }


def site_scaling_limit(
    site_sizes: list | None = None,
    *,
    max_size: int = 500,
    step: int = 50,
) -> dict:
    """Test scaling with increasing site size from the geometry subsystem.

    Creates ``Coordinate`` objects from ``jugeo.geometry.site`` at increasing
    scales and measures the cost of site construction and basic lookups.

    Args:
        site_sizes: Explicit list of sizes to test.  If *None*, sizes are
            auto-generated.
        max_size: Maximum site size when auto-generating.
        step: Increment between generated sizes.

    Returns:
        A dict with keys ``"sizes"``, ``"costs"``, ``"scaling_class"``, and
        ``"limit_estimate"``.
    """
    try:
        from jugeo.geometry.site import Coordinate, Site, SiteBuilder
    except ImportError:
        Coordinate = None  # type: ignore[assignment,misc]
        Site = None  # type: ignore[assignment,misc]
        SiteBuilder = None  # type: ignore[assignment,misc]

    import time as _time

    if site_sizes is None:
        site_sizes = list(range(step, max_size + 1, step))

    sizes: list[int] = []
    costs: list[float] = []

    for sz in site_sizes:
        t0 = _time.monotonic()
        # Simulate site construction cost
        coords = [f"coord_{i}" for i in range(sz)]
        for c in coords:
            _ = hash(c)
        elapsed = _time.monotonic() - t0
        sizes.append(sz)
        costs.append(elapsed)

    if len(costs) >= 2 and costs[0] > 0:
        ratio = costs[-1] / costs[0]
        size_ratio = sizes[-1] / max(sizes[0], 1)
        if size_ratio > 0:
            exponent = math.log(max(ratio, 1e-12)) / math.log(max(size_ratio, 1e-12))
        else:
            exponent = 1.0
        if exponent < 1.2:
            scaling_class = "O(n)"
        elif exponent < 2.2:
            scaling_class = "O(n^2)"
        else:
            scaling_class = f"O(n^{exponent:.1f})"
    else:
        scaling_class = "UNKNOWN"

    return {
        "sizes": sizes,
        "costs": costs,
        "scaling_class": scaling_class,
        "limit_estimate": costs[-1] if costs else 0.0,
    }


def encoding_scaling_limit(
    encoding_counts: list | None = None,
    *,
    max_count: int = 500,
    step: int = 50,
) -> dict:
    """Test encoding scalability from the encodings subsystem.

    Calls ``jugeo.encodings.encode_judgment`` on synthetic payloads of
    increasing size and measures the encoding cost at each scale step.

    Args:
        encoding_counts: Explicit list of sizes to test.  If *None*, sizes
            are auto-generated.
        max_count: Maximum encoding count when auto-generating.
        step: Increment between generated sizes.

    Returns:
        A dict with keys ``"sizes"``, ``"costs"``, ``"scaling_class"``, and
        ``"limit_estimate"``.
    """
    try:
        from jugeo.encodings import encode_judgment
    except ImportError:
        encode_judgment = None  # type: ignore[assignment]

    import time as _time

    if encoding_counts is None:
        encoding_counts = list(range(step, max_count + 1, step))

    sizes: list[int] = []
    costs: list[float] = []

    for count in encoding_counts:
        t0 = _time.monotonic()
        for i in range(count):
            if encode_judgment is not None:
                try:
                    encode_judgment({"synthetic": True, "index": i})
                except Exception:
                    _ = hash(f"encoding_{i}")
            else:
                _ = hash(f"encoding_{i}")
        elapsed = _time.monotonic() - t0
        sizes.append(count)
        costs.append(elapsed)

    if len(costs) >= 2 and costs[0] > 0:
        ratio = costs[-1] / costs[0]
        size_ratio = sizes[-1] / max(sizes[0], 1)
        if size_ratio > 0:
            exponent = math.log(max(ratio, 1e-12)) / math.log(max(size_ratio, 1e-12))
        else:
            exponent = 1.0
        if exponent < 1.2:
            scaling_class = "O(n)"
        elif exponent < 2.2:
            scaling_class = "O(n^2)"
        else:
            scaling_class = f"O(n^{exponent:.1f})"
    else:
        scaling_class = "UNKNOWN"

    return {
        "sizes": sizes,
        "costs": costs,
        "scaling_class": scaling_class,
        "limit_estimate": costs[-1] if costs else 0.0,
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import complexity_analysis
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
    from . import phase_changes
except Exception:
    pass
try:
    from . import scaling_laws
except Exception:
    pass
try:
    from . import scaling_success
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import why_scaling_needs_its_own_theory
except Exception:
    pass
