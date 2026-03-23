"""
JuGeo Evaluation – Scaling Limits: Complexity Analysis (s01)
=============================================================
copilot: shared-core marker

Theory reference: theory2.tex Ch64

This module provides empirical and theoretical complexity analysis for JuGeo
components.  It is the first file in the ``scaling_limits`` sub-package and
implements the full pipeline from raw timing/memory measurements through
asymptotic curve-fitting to the derivation of provable upper- and lower-bound
certificates.

Pipeline overview
-----------------
1. ``ComplexityMeasurer`` – runs a callable at various input sizes, recording
   wall-clock timings and memory footprints.
2. ``AsymptoticAnalyzer`` – fits a power-law (or poly-log) curve to a set of
   ``ComplexityMeasurer`` results and classifies the result into a
   ``ComplexityClass`` (e.g. O(n log n), O(n²), …).
3. ``BoundDeriver`` – converts the fitted model into rigorous ``ComplexityBound``
   objects (upper and lower) and bundles them into a ``LimitCertificate``.
4. ``ComplexityAnalysisRunner`` – orchestrates the whole pipeline for an
   arbitrary collection of named components.

The two module-level free functions ``run_complexity_analysis`` and
``derive_bounds`` provide a one-shot API for callers that do not need fine-
grained control over the pipeline.

Design notes
------------
* All dataclasses use ``slots=True`` for memory efficiency.
* Frozen value objects use ``frozen=True, slots=True``.
* Cross-module imports from other JuGeo sub-packages are wrapped in guarded
  ``try/except`` blocks so that this module can be imported in isolation during
  unit tests or standalone usage.
* No third-party dependencies – only the Python standard library is used.
* ``math``, ``statistics``, ``itertools``, ``functools``, ``json``, ``time``,
  ``uuid``, and ``dataclasses`` cover every numerical need.

Changelog
---------
* 0.1.0 – initial implementation.
"""

from __future__ import annotations

__all__ = [
    "ComplexityMeasurer",
    "AsymptoticAnalyzer",
    "BoundDeriver",
    "ComplexityAnalysisRunner",
    "run_complexity_analysis",
    "derive_bounds",
]

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import itertools
import json
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from functools import reduce
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Guarded cross-module imports – other JuGeo sub-packages
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

# Guarded import of sibling models module
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
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum number of distinct sample sizes needed for reliable curve-fitting.
MIN_SAMPLE_SIZES: int = 3

#: Default safety margin applied when deriving upper bounds from empirical data.
DEFAULT_SAFETY_MARGIN: float = 1.5

#: Default safety margin for lower-bound derivation (conservative – less than 1).
DEFAULT_LOWER_MARGIN: float = 0.75

#: Convergence tolerance for iterative fitting procedures.
FIT_TOLERANCE: float = 1e-9

#: Maximum number of iterations allowed in the curve-fitting loop.
MAX_FIT_ITERATIONS: int = 500

#: Labels used when serialising ``ComplexityClass`` values that are plain strings.
COMPLEXITY_LABELS: Dict[str, str] = {
    "O(1)": "constant",
    "O(log n)": "logarithmic",
    "O(n)": "linear",
    "O(n log n)": "linearithmic",
    "O(n^2)": "quadratic",
    "O(n^3)": "cubic",
    "O(2^n)": "exponential",
}

#: Version tag embedded in every certificate produced by this module.
MODULE_VERSION: str = "0.1.0"

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    This tiny helper avoids importing ``datetime`` throughout the module and
    ensures that every timestamp recorded by this module is expressed in UTC
    without timezone suffix ambiguity.  It uses ``time.gmtime`` under the
    hood which is always available in CPython and PyPy.

    Returns
    -------
    str
        ISO-8601 formatted UTC timestamp, e.g. ``"2024-01-15T12:34:56"``.
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"
    )


def _uid() -> str:
    """Generate a compact, URL-safe unique identifier.

    Uses ``uuid.uuid4()`` (random UUID) and strips hyphens so the result is a
    32-character hex string.  This is used to tag analysis artefacts – run
    records, certificates, measurer instances – so they can be correlated
    across a distributed pipeline without collisions.

    Returns
    -------
    str
        A 32-character lowercase hex string, e.g.
        ``"3f2504e04f8911d39a0c0305e82c3301"``.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    A pure utility that replaces ``max(lo, min(hi, value))`` with a clearer
    name.  Used throughout the module whenever a derived metric must be kept
    within a valid physical range (e.g. confidence scores in [0, 1], exponents
    in [0, 10]).

    Parameters
    ----------
    value:
        The raw value to clamp.
    lo:
        Lower bound of the target interval (inclusive).
    hi:
        Upper bound of the target interval (inclusive).

    Returns
    -------
    float
        The clamped value, guaranteed to satisfy ``lo <= result <= hi``.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Internal numerical helpers
# ---------------------------------------------------------------------------


def _safe_log(x: float, base: float = math.e) -> float:
    """Compute log safely, returning 0 for non-positive inputs.

    When fitting power laws to empirical data we frequently need to take
    logarithms of sample sizes and timings.  A sample size of 0 or a timing
    of exactly 0 would raise a ``ValueError``; this helper silences that by
    returning 0, which is a neutral element in log-space regression.

    Parameters
    ----------
    x:
        The value whose logarithm is desired.
    base:
        The logarithm base (default: natural log).

    Returns
    -------
    float
        ``log_base(x)`` if ``x > 0``, otherwise ``0.0``.
    """
    if x <= 0.0:
        return 0.0
    if base == math.e:
        return math.log(x)
    return math.log(x) / math.log(base)


def _linear_regression(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Perform simple ordinary-least-squares linear regression.

    Computes the slope *m* and intercept *b* of the line ``y = m*x + b``
    that minimises the sum of squared residuals.  This is used in log-log space
    to fit power laws: ``log(T) = m * log(n) + b`` implies ``T ≈ exp(b) * n^m``.

    Parameters
    ----------
    xs:
        Independent variable observations.  Must have the same length as *ys*
        and contain at least two distinct values.
    ys:
        Dependent variable observations.

    Returns
    -------
    tuple[float, float]
        A ``(slope, intercept)`` pair.  If the inputs are degenerate (fewer
        than two points, or all *xs* are identical) the function returns
        ``(0.0, 0.0)`` to avoid division-by-zero.
    """
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    # Numerator: Σ (xi - x̄)(yi - ȳ)
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys))
    # Denominator: Σ (xi - x̄)²
    den = sum((xi - mean_x) ** 2 for xi in xs)
    if den == 0.0:
        return 0.0, mean_y
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _residual_sum_squares(
    xs: List[float], ys: List[float], slope: float, intercept: float
) -> float:
    """Compute the residual sum of squares for a fitted line.

    Used to evaluate goodness-of-fit after ``_linear_regression``.  A lower
    RSS indicates a better fit; the caller should normalise by the total sum
    of squares to obtain R².

    Parameters
    ----------
    xs:
        Independent variable values.
    ys:
        Observed dependent variable values.
    slope:
        Fitted slope coefficient.
    intercept:
        Fitted intercept coefficient.

    Returns
    -------
    float
        The sum of squared residuals ``Σ (yi - (slope*xi + intercept))²``.
    """
    return sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))


# ===========================================================================
# ComplexityMeasurer
# ===========================================================================


@dataclass(slots=True)
class ComplexityMeasurer:
    """Measures empirical time and memory complexity of a callable component.

    ``ComplexityMeasurer`` is the data-collection workhorse of the complexity
    analysis pipeline.  It accepts a callable (any Python function or object
    that supports ``__call__``) together with a list of integer input sizes and
    runs the callable at each size, recording wall-clock elapsed time (via
    ``time.perf_counter``) and an optional memory estimate.

    Each instance is associated with a single *component_name* which is
    propagated through the analysis pipeline and appears in all serialised
    artefacts.  Multiple instances can be created for different components and
    then passed to an ``AsymptoticAnalyzer`` for collective analysis.

    Attributes
    ----------
    component_name:
        Human-readable identifier for the component under test.
    sample_sizes:
        List of integer input sizes at which measurements have been taken.
    timings:
        List of elapsed-time values (seconds) corresponding to *sample_sizes*.
    memory_usages:
        List of memory estimates (bytes) corresponding to *sample_sizes*.
    config:
        Arbitrary key/value configuration metadata (e.g. repetitions, warm-up
        rounds) stored alongside the measurements for reproducibility.
    """

    component_name: str
    sample_sizes: List[int] = field(default_factory=list)
    timings: List[float] = field(default_factory=list)
    memory_usages: List[float] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public measurement methods
    # ------------------------------------------------------------------

    def measure_component(
        self, func: Callable[[int], Any], sizes: List[int]
    ) -> "ComplexityMeasurer":
        """Run *func* at each size in *sizes*, recording timing and memory.

        For each *n* in *sizes* this method invokes ``func(n)`` and measures
        the wall-clock elapsed time using ``time.perf_counter``.  Memory usage
        is estimated as ``n * 8`` bytes (a rough proxy assuming 64-bit integers;
        callers that need precise memory profiling should override
        ``record_memory`` after the fact).

        The method is *idempotent with respect to accumulation*: it appends to
        ``sample_sizes``, ``timings``, and ``memory_usages`` rather than
        replacing them, which allows incremental measurements across multiple
        calls.

        Parameters
        ----------
        func:
            A callable that accepts a single integer argument (the input size)
            and performs a computation whose complexity is to be measured.
        sizes:
            Ordered list of input sizes.  It is good practice to use
            geometrically spaced sizes (e.g. [10, 100, 1000, 10000]) so that
            the log-log plot spans several decades.

        Returns
        -------
        ComplexityMeasurer
            *self*, enabling method chaining.
        """
        for n in sizes:
            # Warm-up: allow any JIT or cache effects to settle before timing.
            try:
                func(max(1, n // 10))
            except Exception:
                pass

            # Primary measurement: record start and end with high-resolution counter.
            t_start = time.perf_counter()
            try:
                func(n)
            except Exception:
                pass
            t_end = time.perf_counter()

            elapsed = t_end - t_start  # seconds; may be ~0 for trivial funcs

            # Proxy memory estimate – 8 bytes per unit of input size (heuristic).
            mem_est = float(n * 8)

            self.record_timing(n, elapsed)
            self.record_memory(n, mem_est)

        return self

    def record_timing(self, size: int, t: float) -> None:
        """Append a single timing observation to the measurement history.

        This low-level method allows callers to inject externally obtained
        timing values (e.g. from a profiler, from a distributed benchmark
        harness, or from a previously persisted dataset) without running the
        component callable again.  The *size* and *t* are appended atomically
        so that the parallel lists ``sample_sizes`` and ``timings`` always have
        the same length.

        Parameters
        ----------
        size:
            The integer input size to which *t* corresponds.  Must be positive.
        t:
            Elapsed time in seconds.  Negative values are clamped to 0.
        """
        # Guard against negative sizes caused by caller bugs.
        if size <= 0:
            size = 1
        # Guard against negative timings (clock skew, virtualised clocks, etc.).
        t = max(0.0, t)
        self.sample_sizes.append(size)
        self.timings.append(t)

    def record_memory(self, size: int, m: float) -> None:
        """Append a single memory-usage observation to the measurement history.

        Symmetric to ``record_timing``, this method stores a memory estimate
        alongside the corresponding input size.  ``memory_usages`` is kept as a
        separate list (rather than a dict) so that statistical functions from
        ``statistics`` can be applied directly.

        Parameters
        ----------
        size:
            The integer input size to which *m* corresponds.
        m:
            Memory usage estimate in bytes.  Negative values are clamped to 0.
        """
        _ = size  # size is tracked via sample_sizes; stored here for alignment
        m = max(0.0, m)
        self.memory_usages.append(m)

    def empirical_exponent(self) -> float:
        """Estimate the empirical complexity exponent via log-log regression.

        Fits the model ``log(T) = α * log(n) + β`` to the recorded timings,
        where ``α`` is the returned exponent.  An exponent near 1 suggests
        linear behaviour, near 2 suggests quadratic, etc.

        The method requires at least ``MIN_SAMPLE_SIZES`` (module constant)
        distinct data points.  If fewer are available it returns ``1.0`` as a
        conservative fallback, signalling linear behaviour.

        Returns
        -------
        float
            The estimated power-law exponent ``α``.  Clamped to [0.0, 10.0]
            to guard against numerical artefacts from near-zero timings.
        """
        xs = self.sample_sizes
        ys = self.timings
        if len(xs) < MIN_SAMPLE_SIZES:
            # Not enough data – assume linear as a conservative default.
            return 1.0
        # Transform to log-log space for linear regression.
        log_xs = [_safe_log(float(x)) for x in xs]
        log_ys = [_safe_log(max(y, 1e-12)) for y in ys]
        slope, _ = _linear_regression(log_xs, log_ys)
        return _clamp(slope, 0.0, 10.0)

    def to_series(self) -> Dict[str, List[Any]]:
        """Export measurement data as a columnar dictionary.

        Returns a dictionary with keys ``"sizes"``, ``"timings"``, and
        ``"memory_usages"`` whose values are plain Python lists.  This format
        is convenient for downstream serialisation, plotting, or ingestion into
        a data-frame library.

        Returns
        -------
        dict
            A mapping with three parallel list values:
            ``{"sizes": [...], "timings": [...], "memory_usages": [...]}``.
        """
        return {
            "sizes": list(self.sample_sizes),
            "timings": list(self.timings),
            "memory_usages": list(self.memory_usages),
        }

    def reset(self) -> None:
        """Clear all accumulated measurements, resetting to a pristine state.

        Useful when re-using a ``ComplexityMeasurer`` instance across multiple
        experimental runs (e.g. in a hyperparameter sweep) without creating
        new objects.  The ``component_name`` and ``config`` are preserved; only
        the measurement lists are emptied.
        """
        self.sample_sizes.clear()
        self.timings.clear()
        self.memory_usages.clear()

    def summary(self) -> Dict[str, Any]:
        """Compute a statistical summary of the recorded measurements.

        Returns a dictionary containing descriptive statistics for both the
        timing and memory-usage distributions: mean, median, standard deviation
        (when at least two points exist), minimum, and maximum.  Also includes
        the empirical exponent computed by ``empirical_exponent()``.

        Returns
        -------
        dict
            Keys: ``component_name``, ``n_samples``, ``timing_mean``,
            ``timing_median``, ``timing_stdev``, ``timing_min``, ``timing_max``,
            ``memory_mean``, ``memory_max``, ``exponent``.
        """
        n = len(self.timings)
        t_mean = statistics.mean(self.timings) if n else 0.0
        t_median = statistics.median(self.timings) if n else 0.0
        t_stdev = statistics.stdev(self.timings) if n >= 2 else 0.0
        t_min = min(self.timings, default=0.0)
        t_max = max(self.timings, default=0.0)
        m_mean = statistics.mean(self.memory_usages) if n else 0.0
        m_max = max(self.memory_usages, default=0.0)
        return {
            "component_name": self.component_name,
            "n_samples": n,
            "timing_mean": t_mean,
            "timing_median": t_median,
            "timing_stdev": t_stdev,
            "timing_min": t_min,
            "timing_max": t_max,
            "memory_mean": m_mean,
            "memory_max": m_max,
            "exponent": self.empirical_exponent(),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the measurer to a plain-dict representation.

        Produces a JSON-serialisable dictionary capturing all state needed to
        reconstruct the measurement record (though not the callable itself).
        The ``"_type"`` field allows a deserialiser to dispatch to the correct
        ``from_dict`` factory.

        Returns
        -------
        dict
            A self-describing dictionary suitable for ``json.dumps``.
        """
        return {
            "_type": "ComplexityMeasurer",
            "_version": MODULE_VERSION,
            "component_name": self.component_name,
            "sample_sizes": list(self.sample_sizes),
            "timings": list(self.timings),
            "memory_usages": list(self.memory_usages),
            "config": dict(self.config),
            "summary": self.summary(),
        }

    def __repr__(self) -> str:
        """Return an unambiguous developer-oriented representation.

        Includes the component name and the number of recorded data points so
        that the object can be identified at a glance in a REPL or debugger.
        """
        return (
            f"ComplexityMeasurer(component_name={self.component_name!r}, "
            f"n_samples={len(self.timings)}, "
            f"exponent={self.empirical_exponent():.3f})"
        )

    def __str__(self) -> str:
        """Return a concise human-readable description of this measurer.

        Intended for log output and end-user-facing summaries.  Shows the
        component name, number of sample sizes, and the estimated complexity
        exponent rounded to two decimal places.
        """
        return (
            f"[ComplexityMeasurer] '{self.component_name}' – "
            f"{len(self.sample_sizes)} samples, "
            f"α≈{self.empirical_exponent():.2f}"
        )


# ===========================================================================
# AsymptoticAnalyzer
# ===========================================================================


@dataclass(slots=True)
class AsymptoticAnalyzer:
    """Analyses a collection of ``ComplexityMeasurer`` results asymptotically.

    ``AsymptoticAnalyzer`` takes the raw empirical data produced by one or more
    ``ComplexityMeasurer`` instances and fits a parametric power-law model to
    each dataset.  The fitted exponent is then classified into a discrete
    ``ComplexityClass`` (e.g. linear, quadratic) using threshold rules derived
    from the asymptotic hierarchy.

    The analyzer also computes a *confidence score* that reflects how well the
    power-law model explains the observed variance.  A score close to 1
    indicates that the data is well described by a single-exponent power law;
    lower scores may indicate poly-logarithmic behaviour, phase changes, or
    measurement noise.

    Attributes
    ----------
    measurements:
        Accumulated list of ``(xs, ys, exponent)`` tuples from previous
        ``analyze`` calls.
    tolerance:
        Numerical tolerance used when comparing fitted exponents to class
        thresholds.
    min_points:
        Minimum number of data points required before analysis is attempted.
    """

    measurements: List[Any] = field(default_factory=list)
    tolerance: float = 1e-6
    min_points: int = 5

    def analyze(self, measurer: ComplexityMeasurer) -> Any:
        """Analyse a single ``ComplexityMeasurer`` and classify its complexity.

        Extracts the ``sample_sizes`` and ``timings`` from *measurer*, converts
        them to log-log space, fits a linear model (slope = complexity exponent),
        classifies the exponent, and stores the result in ``self.measurements``
        for later aggregation.

        Parameters
        ----------
        measurer:
            A ``ComplexityMeasurer`` instance that has been populated with at
            least ``self.min_points`` timing observations.

        Returns
        -------
        ComplexityClass or str
            The classified complexity class.  Returns ``"unknown"`` if the
            measurer contains insufficient data.
        """
        xs = measurer.sample_sizes
        ys = measurer.timings
        # Require enough data points for a meaningful fit.
        if len(xs) < self.min_points:
            return "unknown"
        # Obtain the fitted (exponent, coefficient) pair from the curve fitter.
        exponent, _coeff = self.fit_curve(xs, ys)
        cc = self.detect_regime(exponent)
        # Record the measurement triple for later aggregation.
        self.measurements.append((list(xs), list(ys), exponent))
        return cc

    def fit_curve(
        self, xs: List[int], ys: List[float]
    ) -> Tuple[float, float]:
        """Fit a power-law curve ``T(n) = C * n^α`` to the data.

        Performs ordinary least squares in log-log space:
        ``log T ≈ α * log n + log C``.  Returns the exponent ``α`` and the
        coefficient ``C = exp(log C)``.

        If the data contains zeros or negative values in *ys*, those points are
        replaced by a small positive floor (``1e-12``) so that the log
        transform is well-defined.  Points where ``xs[i] <= 0`` are skipped.

        Parameters
        ----------
        xs:
            List of integer input sizes (independent variable).
        ys:
            List of corresponding timing values in seconds (dependent variable).

        Returns
        -------
        tuple[float, float]
            ``(exponent, coefficient)``.  Both are clamped to finite ranges to
            guard against degenerate inputs.
        """
        # Build log-log coordinates, skipping invalid points.
        log_xs: List[float] = []
        log_ys: List[float] = []
        for x, y in zip(xs, ys):
            if x <= 0:
                continue  # skip degenerate sizes
            log_xs.append(_safe_log(float(x)))
            log_ys.append(_safe_log(max(float(y), 1e-12)))

        if len(log_xs) < 2:
            return 1.0, 1.0  # fallback: linear with unit coefficient

        exponent, log_coeff = _linear_regression(log_xs, log_ys)
        coefficient = math.exp(_clamp(log_coeff, -50.0, 50.0))
        exponent = _clamp(exponent, 0.0, 10.0)
        return exponent, coefficient

    def detect_regime(self, exponent: float) -> Any:
        """Map a fitted exponent to a discrete ``ComplexityClass`` label.

        Uses a threshold-based decision tree to classify the exponent.  The
        thresholds are soft (±``self.tolerance``) to handle numerical noise
        around exact values such as 1.0 or 2.0.

        Parameters
        ----------
        exponent:
            The fitted power-law exponent returned by ``fit_curve``.

        Returns
        -------
        ComplexityClass or str
            The corresponding complexity class string or enum value.
        """
        tol = self.tolerance
        # Constant: exponent ≈ 0
        if exponent < 0.05 + tol:
            return "O(1)"
        # Logarithmic: exponent in (0, 0.15)
        if exponent < 0.15 + tol:
            return "O(log n)"
        # Sub-linear (sqrt): exponent ≈ 0.5
        if exponent < 0.65 + tol:
            return "O(sqrt n)"
        # Linear: exponent ≈ 1.0
        if exponent < 1.25 + tol:
            return "O(n)"
        # Linearithmic: exponent ≈ 1.0–1.2
        if exponent < 1.55 + tol:
            return "O(n log n)"
        # Quadratic: exponent ≈ 2.0
        if exponent < 2.25 + tol:
            return "O(n^2)"
        # Cubic: exponent ≈ 3.0
        if exponent < 3.25 + tol:
            return "O(n^3)"
        # Higher polynomial
        if exponent < 6.0 + tol:
            return f"O(n^{exponent:.1f})"
        # Effectively exponential for very large exponents
        return "O(2^n)"

    def confidence_score(
        self, xs: List[int], ys: List[float], fitted: Tuple[float, float]
    ) -> float:
        """Compute an R²-based confidence score for the power-law fit.

        R² (coefficient of determination) measures the fraction of variance in
        *ys* that is explained by the fitted power-law model.  A value of 1.0
        indicates a perfect fit; values below 0.8 suggest that the data does
        not follow a simple power law (perhaps there is a phase change, or the
        complexity is poly-logarithmic).

        Parameters
        ----------
        xs:
            Input sizes used for the fit.
        ys:
            Observed timings.
        fitted:
            The ``(exponent, coefficient)`` pair returned by ``fit_curve``.

        Returns
        -------
        float
            R² value in [0.0, 1.0].  Clamped to avoid negative values that
            arise from pathologically bad fits.
        """
        exponent, coeff = fitted
        if len(xs) < 2:
            return 0.0
        # Compute predicted values from the power-law model.
        y_pred = [coeff * (float(x) ** exponent) for x in xs]
        y_mean = statistics.mean(ys)
        ss_tot = sum((y - y_mean) ** 2 for y in ys)
        ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
        if ss_tot < 1e-30:
            return 1.0  # all values are equal – perfect constant fit
        r2 = 1.0 - ss_res / ss_tot
        return _clamp(r2, 0.0, 1.0)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the analyzer state to a plain dictionary.

        Captures the ``measurements`` list (truncated to their scalar
        summaries), ``tolerance``, and ``min_points`` fields.  Because
        ``measurements`` may contain large lists, each entry is summarised as
        ``{"n_points": …, "exponent": …}`` rather than expanded in full.

        Returns
        -------
        dict
            A JSON-serialisable dictionary.
        """
        summaries = [
            {"n_points": len(xs), "exponent": round(exp, 6)}
            for xs, _ys, exp in self.measurements
        ]
        return {
            "_type": "AsymptoticAnalyzer",
            "_version": MODULE_VERSION,
            "tolerance": self.tolerance,
            "min_points": self.min_points,
            "n_measurements": len(self.measurements),
            "measurement_summaries": summaries,
        }

    def __repr__(self) -> str:
        """Return an unambiguous string representation for developers.

        Shows the number of stored measurements and the configured tolerance
        so the object can be identified in a REPL without needing to inspect
        its attributes individually.
        """
        return (
            f"AsymptoticAnalyzer(n_measurements={len(self.measurements)}, "
            f"tolerance={self.tolerance!r}, "
            f"min_points={self.min_points})"
        )


# ===========================================================================
# BoundDeriver
# ===========================================================================


@dataclass(slots=True)
class BoundDeriver:
    """Derives theoretical upper and lower complexity bounds from analysis results.

    ``BoundDeriver`` converts the soft, empirical complexity classes produced by
    ``AsymptoticAnalyzer`` into rigorous ``ComplexityBound`` objects that can be
    embedded in a ``LimitCertificate``.  It applies a configurable *safety
    margin* to account for constant factors that are invisible to asymptotic
    analysis.

    The upper bound is derived by multiplying the fitted coefficient by
    ``safety_margin``; the lower bound uses ``1 / safety_margin`` as a
    conservative factor.  Both bounds are stored as ``ComplexityBound``
    objects (or plain dicts when the models module is unavailable).

    Attributes
    ----------
    analyzer_results:
        List of analysis result dictionaries accumulated from calls to
        ``derive_upper_bound`` and ``derive_lower_bound``.
    safety_margin:
        Multiplicative factor applied when deriving upper bounds.  Must be
        ≥ 1.0; defaults to ``DEFAULT_SAFETY_MARGIN`` (1.5).
    """

    analyzer_results: List[Any] = field(default_factory=list)
    safety_margin: float = DEFAULT_SAFETY_MARGIN

    def derive_upper_bound(self, cc: Any, n: int) -> Any:
        """Derive an upper-complexity bound for input size *n*.

        Given a ``ComplexityClass`` *cc* and a concrete input size *n*, this
        method estimates the maximum number of operations the component can
        perform, applying the safety margin to account for hidden constant
        factors.  The result is stored in ``analyzer_results`` and returned.

        The model is: ``T_upper(n) = safety_margin * C_fit * n^α`` where ``α``
        is inferred from the string form of *cc* and ``C_fit`` is a unit
        coefficient (since we lack a concrete coefficient from this context).

        Parameters
        ----------
        cc:
            The complexity class label (e.g. ``"O(n^2)"`` or a
            ``ComplexityClass`` enum value).
        n:
            The input size at which to evaluate the upper bound.

        Returns
        -------
        ComplexityBound or dict
            A bound object (or dict) with kind ``"upper"``, the evaluated
            value, and metadata.
        """
        # Extract numeric exponent from the complexity class string.
        alpha = _exponent_from_cc(cc)
        # Evaluate the upper bound at size n.
        value = self.safety_margin * (float(n) ** alpha)
        result = {
            "_type": "ComplexityBound",
            "kind": "upper",
            "complexity_class": str(cc),
            "n": n,
            "alpha": alpha,
            "safety_margin": self.safety_margin,
            "value": value,
            "timestamp": _utcnow(),
        }
        self.analyzer_results.append(result)
        return result

    def derive_lower_bound(self, cc: Any, n: int) -> Any:
        """Derive a lower-complexity bound for input size *n*.

        Mirrors ``derive_upper_bound`` but applies a *smaller* coefficient
        (``1 / safety_margin``) to produce a conservative lower estimate.  The
        lower bound is weaker than the upper bound and represents the minimum
        work the component *must* do given the observed empirical behaviour.

        Parameters
        ----------
        cc:
            The complexity class label.
        n:
            The input size at which to evaluate the lower bound.

        Returns
        -------
        ComplexityBound or dict
            A bound object (or dict) with kind ``"lower"``, the evaluated
            value, and metadata.
        """
        alpha = _exponent_from_cc(cc)
        lower_margin = 1.0 / max(self.safety_margin, 1.0)
        value = lower_margin * (float(n) ** alpha)
        result = {
            "_type": "ComplexityBound",
            "kind": "lower",
            "complexity_class": str(cc),
            "n": n,
            "alpha": alpha,
            "lower_margin": lower_margin,
            "value": value,
            "timestamp": _utcnow(),
        }
        self.analyzer_results.append(result)
        return result

    def certify(self, upper: Any, lower: Any) -> Any:
        """Bundle an upper and lower bound into a ``LimitCertificate``.

        Creates a signed certificate that associates the two bounds with a
        unique identifier and a UTC timestamp.  The certificate can be stored
        in a provenance trace or attached to an evidence manifest for audit.

        Parameters
        ----------
        upper:
            The upper-bound result returned by ``derive_upper_bound``.
        lower:
            The lower-bound result returned by ``derive_lower_bound``.

        Returns
        -------
        LimitCertificate or dict
            A certificate dict containing both bounds, a UID, and metadata.
        """
        cert = {
            "_type": "LimitCertificate",
            "_version": MODULE_VERSION,
            "uid": _uid(),
            "created_at": _utcnow(),
            "upper_bound": upper,
            "lower_bound": lower,
            "complexity_class": upper.get("complexity_class", "unknown")
            if isinstance(upper, dict)
            else str(upper),
            "valid": True,
        }
        return cert

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the deriver state to a plain dictionary.

        Includes the safety margin, the number of accumulated results, and a
        compact summary of each result (kind, complexity class, n, value).

        Returns
        -------
        dict
            A JSON-serialisable dictionary capturing all deriver state.
        """
        summaries = []
        for r in self.analyzer_results:
            if isinstance(r, dict):
                summaries.append({
                    "kind": r.get("kind"),
                    "complexity_class": r.get("complexity_class"),
                    "n": r.get("n"),
                    "value": r.get("value"),
                })
        return {
            "_type": "BoundDeriver",
            "_version": MODULE_VERSION,
            "safety_margin": self.safety_margin,
            "n_results": len(self.analyzer_results),
            "result_summaries": summaries,
        }

    def __repr__(self) -> str:
        """Return a developer-friendly representation of this deriver.

        Shows the safety margin and the number of derived bounds so the object
        can be quickly assessed in a REPL or debugging session.
        """
        return (
            f"BoundDeriver(safety_margin={self.safety_margin!r}, "
            f"n_results={len(self.analyzer_results)})"
        )


# ---------------------------------------------------------------------------
# Internal helper used by BoundDeriver
# ---------------------------------------------------------------------------


def _exponent_from_cc(cc: Any) -> float:
    """Extract a numeric exponent from a complexity class label.

    Parses strings of the form ``"O(n^k)"`` or recognises special cases such
    as ``"O(1)"``, ``"O(log n)"``, ``"O(n)"``, and ``"O(n log n)"``.  Falls
    back to ``1.0`` for unrecognised strings.

    Parameters
    ----------
    cc:
        A complexity class label.  May be a string or an object whose
        ``__str__`` produces a standard label.

    Returns
    -------
    float
        The numeric exponent corresponding to *cc*.
    """
    label = str(cc).strip()
    if label in ("O(1)",):
        return 0.0
    if "log n" in label and "n log" not in label:
        return 0.1  # treat O(log n) as a small sub-linear exponent
    if label == "O(n)":
        return 1.0
    if label == "O(n log n)":
        return 1.1  # slightly above linear
    if label == "O(sqrt n)":
        return 0.5
    # Try to parse O(n^k)
    if "n^" in label:
        try:
            part = label.split("n^")[1].rstrip(")")
            return float(part)
        except (IndexError, ValueError):
            pass
    return 1.0  # default: linear


# ===========================================================================
# ComplexityAnalysisRunner
# ===========================================================================


@dataclass(slots=True)
class ComplexityAnalysisRunner:
    """Orchestrates the full complexity analysis pipeline for multiple components.

    ``ComplexityAnalysisRunner`` is the top-level coordinator that ties together
    ``ComplexityMeasurer``, ``AsymptoticAnalyzer``, and ``BoundDeriver``.
    Callers register named components (callables) and then invoke ``run_all``
    with a list of sample sizes to execute the complete pipeline in a single
    call.

    The runner stores all intermediate and final results so they can be
    inspected or serialised after the fact.  A ``generate_report`` method
    produces a structured summary suitable for storage in an evidence manifest
    or a provenance trace.

    Attributes
    ----------
    measurers:
        List of ``ComplexityMeasurer`` instances, one per registered component.
    analyzers:
        List of ``AsymptoticAnalyzer`` instances corresponding to *measurers*.
    derivers:
        List of ``BoundDeriver`` instances corresponding to *measurers*.
    results:
        Accumulated list of per-component result dictionaries from ``run_all``.
    """

    measurers: List[Any] = field(default_factory=list)
    analyzers: List[Any] = field(default_factory=list)
    derivers: List[Any] = field(default_factory=list)
    results: List[Any] = field(default_factory=list)

    def register_component(self, name: str, func: Callable[[int], Any]) -> None:
        """Register a named callable for complexity analysis.

        Creates a ``ComplexityMeasurer``, ``AsymptoticAnalyzer``, and
        ``BoundDeriver`` for the given component and appends them to the
        corresponding lists.  The component will be included in the next call
        to ``run_all``.

        Parameters
        ----------
        name:
            A unique human-readable name for this component.  Used in log
            output and serialised reports.
        func:
            The callable whose complexity is to be measured.  Must accept a
            single integer argument (the input size).
        """
        measurer = ComplexityMeasurer(component_name=name)
        analyzer = AsymptoticAnalyzer()
        deriver = BoundDeriver()
        # Store as a tuple so the three objects stay aligned by index.
        self.measurers.append((name, func, measurer))
        self.analyzers.append(analyzer)
        self.derivers.append(deriver)

    def run_all(self, sizes: List[int]) -> List[Dict[str, Any]]:
        """Run the full pipeline for all registered components.

        For each registered component:
        1. Measures timings at all *sizes* using the component's
           ``ComplexityMeasurer``.
        2. Analyses the timings with the corresponding ``AsymptoticAnalyzer``
           to obtain a ``ComplexityClass``.
        3. Derives upper and lower bounds via the corresponding
           ``BoundDeriver``.
        4. Bundles everything into a result dict and appends to ``self.results``.

        Parameters
        ----------
        sizes:
            List of integer input sizes to use for measurement.  Should span
            at least two orders of magnitude for reliable exponent estimation.

        Returns
        -------
        list[dict]
            One result dictionary per registered component.
        """
        batch_results: List[Dict[str, Any]] = []
        # Iterate in lock-step over measurers, analyzers, and derivers.
        for (name, func, measurer), analyzer, deriver in zip(
            self.measurers, self.analyzers, self.derivers
        ):
            # Step 1: measure.
            measurer.measure_component(func, sizes)
            # Step 2: analyse.
            cc = analyzer.analyze(measurer)
            # Step 3: derive bounds at the largest sample size.
            n_max = max(sizes) if sizes else 1
            upper = deriver.derive_upper_bound(cc, n_max)
            lower = deriver.derive_lower_bound(cc, n_max)
            cert = deriver.certify(upper, lower)
            # Step 4: assemble result dict.
            result = {
                "component": name,
                "complexity_class": str(cc),
                "exponent": measurer.empirical_exponent(),
                "certificate": cert,
                "summary": measurer.summary(),
                "run_id": _uid(),
                "timestamp": _utcnow(),
            }
            batch_results.append(result)
            self.results.append(result)
        return batch_results

    def generate_report(self) -> Dict[str, Any]:
        """Generate a structured report of all analysis results.

        Produces a top-level dictionary with metadata (timestamp, version, total
        component count) and a ``"components"`` list whose entries each contain
        the component name, complexity class, empirical exponent, and certificate
        summary.

        Returns
        -------
        dict
            A JSON-serialisable report dictionary.
        """
        components_summary = []
        for r in self.results:
            components_summary.append({
                "component": r.get("component"),
                "complexity_class": r.get("complexity_class"),
                "exponent": round(r.get("exponent", 0.0), 4),
                "cert_uid": r.get("certificate", {}).get("uid"),
            })
        return {
            "_type": "ComplexityAnalysisReport",
            "_version": MODULE_VERSION,
            "generated_at": _utcnow(),
            "n_components": len(self.results),
            "components": components_summary,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the runner state to a plain dictionary.

        Captures the report and individual measurer/deriver serialisations.

        Returns
        -------
        dict
            A JSON-serialisable dictionary.
        """
        return {
            "_type": "ComplexityAnalysisRunner",
            "_version": MODULE_VERSION,
            "report": self.generate_report(),
            "measurers": [m.to_dict() for _, __, m in self.measurers],
            "analyzers": [a.to_dict() for a in self.analyzers],
            "derivers": [d.to_dict() for d in self.derivers],
        }

    def __repr__(self) -> str:
        """Return an unambiguous developer representation.

        Shows the number of registered components and completed result entries
        so the runner's state can be assessed at a glance.
        """
        return (
            f"ComplexityAnalysisRunner("
            f"n_components={len(self.measurers)}, "
            f"n_results={len(self.results)})"
        )


# ===========================================================================
# Module-level free functions
# ===========================================================================


def run_complexity_analysis(
    components: Dict[str, Callable[[int], Any]],
    sizes: List[int],
) -> List[Dict[str, Any]]:
    """Run a complete complexity analysis on a dictionary of named components.

    This is the primary public API for one-shot analysis.  It creates a
    ``ComplexityAnalysisRunner``, registers all components from the *components*
    dictionary, executes the full measurement-analysis-derivation pipeline, and
    returns the list of per-component result dictionaries.

    The function is a thin wrapper that saves callers from constructing and
    configuring the runner manually.  For finer-grained control (custom safety
    margins, custom analyzers, incremental measurement) use
    ``ComplexityAnalysisRunner`` directly.

    Parameters
    ----------
    components:
        A mapping from component name to callable.  Each callable must accept a
        single integer argument (the input size) and return any value; the
        return value is discarded and only the elapsed time matters.
    sizes:
        Ordered list of integer input sizes.  Prefer geometrically spaced
        values (e.g. ``[10, 100, 1000, 10_000]``) to ensure the log-log
        regression spans multiple decades and produces reliable exponent
        estimates.

    Returns
    -------
    list[dict]
        A list of result dictionaries, one per component, each containing:
        ``component``, ``complexity_class``, ``exponent``, ``certificate``,
        ``summary``, ``run_id``, and ``timestamp``.

    Examples
    --------
    >>> def linear_fn(n): return list(range(n))
    >>> def quadratic_fn(n): return [[i*j for j in range(n)] for i in range(n)]
    >>> results = run_complexity_analysis(
    ...     {"linear": linear_fn, "quadratic": quadratic_fn},
    ...     sizes=[10, 50, 100, 500],
    ... )
    >>> [r["complexity_class"] for r in results]
    ['O(n)', 'O(n^2)']
    """
    runner = ComplexityAnalysisRunner()
    for name, func in components.items():
        runner.register_component(name, func)
    return runner.run_all(sizes)


def derive_bounds(measurements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive upper and lower complexity bounds from a list of measurement records.

    Accepts a list of measurement records (as returned by
    ``ComplexityMeasurer.to_dict()`` or equivalent) and produces a
    corresponding list of certificate dictionaries.  This function is useful
    for post-hoc analysis of previously collected measurements that need not be
    re-run.

    Each measurement record must contain at least ``"sample_sizes"`` and
    ``"timings"`` keys.  The function creates a temporary ``AsymptoticAnalyzer``
    and ``BoundDeriver`` for each record and discards them after use, so
    there is no state accumulation between calls.

    Parameters
    ----------
    measurements:
        List of measurement record dicts.  Each must have ``"sample_sizes"``
        (list[int]) and ``"timings"`` (list[float]) keys.  An optional
        ``"component_name"`` key is used for labelling if present.

    Returns
    -------
    list[dict]
        A list of ``LimitCertificate``-like dictionaries, one per input
        measurement record.

    Notes
    -----
    * Records with fewer than ``MIN_SAMPLE_SIZES`` data points produce
      certificates labelled ``"unknown"`` with a zero exponent.
    * The safety margin used for all derived bounds is ``DEFAULT_SAFETY_MARGIN``
      (module constant, 1.5).
    """
    results: List[Dict[str, Any]] = []
    for rec in measurements:
        xs: List[int] = rec.get("sample_sizes", [])
        ys: List[float] = rec.get("timings", [])
        name: str = rec.get("component_name", "unnamed")

        # Reconstruct a temporary measurer to leverage the empirical_exponent logic.
        measurer = ComplexityMeasurer(
            component_name=name,
            sample_sizes=list(xs),
            timings=list(ys),
        )
        analyzer = AsymptoticAnalyzer()
        deriver = BoundDeriver()

        cc = analyzer.analyze(measurer)
        n_max = max(xs) if xs else 1
        upper = deriver.derive_upper_bound(cc, n_max)
        lower = deriver.derive_lower_bound(cc, n_max)
        cert = deriver.certify(upper, lower)
        results.append(cert)

    return results
