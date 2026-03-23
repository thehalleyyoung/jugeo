"""
JuGeo Evaluation – Scaling Limits: Phase Change Detection (s02)
================================================================
copilot: shared-core marker

Theory reference: theory2.tex Ch64

This module is the second file in the ``scaling_limits`` sub-package.  It
implements a complete pipeline for detecting, localising, and characterising
*phase changes* in empirical scaling data – discontinuities or rapid shifts in
the scaling exponent that signal a transition between qualitatively different
computational regimes.

Phase changes are ubiquitous in algorithm analysis: a sorting algorithm may
exhibit O(n log n) behaviour up to a cache-capacity threshold and then degrade
to O(n²) once the data no longer fits in L3 cache; a graph-search routine may
show linear cost in sparse graphs and quadratic cost in dense ones.  Detecting
these transitions automatically allows the JuGeo evaluation framework to issue
targeted performance advisories and to select the appropriate theoretical
complexity bound for each input-size regime.

Pipeline overview
-----------------
1. ``PhaseChangeScanner`` – scans a sequence of (size, timing) pairs using a
   sliding-window finite-difference detector to flag candidate transition
   indices.
2. ``TransitionPointFinder`` – refines each rough index to a precise floating-
   point transition point via binary search over the derivative landscape.
3. ``PhaseCharacterizer`` – fits a ``ScalingRegime`` to the data on each side
   of every transition point and assembles a ``PhaseChange`` record.
4. ``PhaseChangeRunner`` – orchestrates the full pipeline and exposes a clean
   one-shot ``run(xs, ys)`` API.

The two module-level free functions ``detect_phase_changes`` and
``characterize_phases`` provide a one-shot API for callers that do not need
fine-grained control over the pipeline stages.

Design notes
------------
* All dataclasses use ``slots=True`` for memory efficiency.
* Frozen value objects use ``frozen=True, slots=True``.
* Cross-module imports are guarded so this module can be imported in isolation.
* No third-party dependencies – stdlib only (``math``, ``statistics``,
  ``itertools``, ``functools``, ``json``, ``time``, ``uuid``,
  ``dataclasses``).

Changelog
---------
* 0.1.0 – initial implementation.
"""

from __future__ import annotations

__all__ = [
    "PhaseChangeScanner",
    "TransitionPointFinder",
    "PhaseCharacterizer",
    "PhaseChangeRunner",
    "detect_phase_changes",
    "characterize_phases",
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
from typing import Any, Dict, List, Optional, Tuple

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

#: Minimum window size for the sliding-window finite-difference scanner.
MIN_WINDOW: int = 3

#: Default number of points to include in each sliding window.
DEFAULT_WINDOW: int = 5

#: Default jump-magnitude threshold for declaring a discontinuity.
DEFAULT_THRESHOLD: float = 0.5

#: Precision target for the binary-search transition-point refinement.
DEFAULT_PRECISION: float = 1e-3

#: Maximum depth of the binary-search recursion / iteration.
MAX_BINARY_SEARCH_DEPTH: int = 64

#: Small epsilon used to avoid division by zero in normalised differences.
_EPS: float = 1e-30

#: Version tag embedded in every serialised artefact produced by this module.
MODULE_VERSION: str = "0.1.0"

#: Label applied to a regime with no identified phase-change boundary.
REGIME_UNKNOWN: str = "unknown"

#: Descriptive labels for the two sides of a phase-change transition.
SIDE_LEFT: str = "left"
SIDE_RIGHT: str = "right"

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    This tiny helper avoids importing ``datetime`` throughout the module and
    ensures that every timestamp recorded by this module is expressed in UTC
    without timezone suffix ambiguity.  It uses ``time.gmtime`` under the
    hood, which is always available in CPython and PyPy.

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

    Uses ``uuid.uuid4()`` (random UUID) and strips hyphens, producing a
    32-character lowercase hex string.  Used to tag analysis artefacts –
    scanner runs, transition records, phase-change certificates – so they can
    be correlated across a distributed pipeline without collisions.

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
    in [0, 10], or jump magnitudes in [0, ∞)).

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
    """Compute the logarithm safely, returning 0 for non-positive inputs.

    When fitting power-law models to empirical scaling data we frequently need
    to take logarithms of sample sizes and timings.  A sample size of 0 or a
    timing of exactly 0 would raise a ``ValueError``; this helper silences
    that by returning 0, which is a neutral element in log-space regression.

    Parameters
    ----------
    x:
        The value whose logarithm is desired.
    base:
        The logarithm base (default: natural log, ``math.e``).

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


def _finite_differences(ys: List[float]) -> List[float]:
    """Compute first-order finite differences of a sequence.

    Returns a list of length ``len(ys) - 1`` where entry *i* is
    ``ys[i+1] - ys[i]``.  The differences are used by the scanner to detect
    abrupt jumps in the scaling behaviour of a sequence of timing values.

    Parameters
    ----------
    ys:
        A sequence of floating-point values (typically scaled exponent
        estimates or normalised timing ratios).

    Returns
    -------
    list[float]
        First-order finite differences.  Empty if *ys* has fewer than 2
        elements.
    """
    if len(ys) < 2:
        return []
    return [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]


def _rolling_mean(ys: List[float], window: int) -> List[float]:
    """Compute a causal rolling mean with a given window size.

    Each output element at position *i* is the mean of
    ``ys[max(0, i-window+1) : i+1]``.  This is a causal (non-centred) filter
    that does not look ahead, making it suitable for online processing of
    streaming measurement data.

    Parameters
    ----------
    ys:
        The input sequence.
    window:
        The number of elements to include in each average.  Clamped to
        ``[1, len(ys)]``.

    Returns
    -------
    list[float]
        Rolling mean values with the same length as *ys*.
    """
    n = len(ys)
    if n == 0:
        return []
    window = max(1, min(window, n))
    result: List[float] = []
    for i in range(n):
        lo = max(0, i - window + 1)
        result.append(statistics.mean(ys[lo : i + 1]))
    return result


def _gaussian_kernel(sigma: float, radius: int) -> List[float]:
    """Build a normalised 1-D Gaussian kernel.

    Constructs a discrete Gaussian kernel of half-width *radius* and standard
    deviation *sigma*.  The kernel is normalised so that its entries sum to
    1.0, making it suitable for convolution-based smoothing.

    Parameters
    ----------
    sigma:
        Standard deviation of the Gaussian, in index units.
    radius:
        Half-width of the kernel: the returned list has ``2*radius + 1``
        entries.

    Returns
    -------
    list[float]
        Normalised Gaussian kernel weights.
    """
    if sigma <= 0.0:
        # Degenerate: return a delta kernel (no smoothing).
        result = [0.0] * (2 * radius + 1)
        result[radius] = 1.0
        return result
    kernel = [
        math.exp(-0.5 * (k / sigma) ** 2)
        for k in range(-radius, radius + 1)
    ]
    total = sum(kernel)
    return [w / total for w in kernel]


def _convolve(ys: List[float], kernel: List[float]) -> List[float]:
    """Convolve a 1-D sequence with a symmetric kernel using boundary replication.

    Applies the kernel centred at each position in *ys*, replicating boundary
    values (``edge`` padding) to handle the borders.  The output has the same
    length as the input.

    Parameters
    ----------
    ys:
        The input sequence to smooth.
    kernel:
        A symmetric convolution kernel (e.g. Gaussian weights).

    Returns
    -------
    list[float]
        The convolved sequence.
    """
    n = len(ys)
    r = len(kernel) // 2
    result: List[float] = []
    for i in range(n):
        acc = 0.0
        for j, w in enumerate(kernel):
            idx = _clamp(i + j - r, 0, n - 1)
            acc += w * ys[int(idx)]
        result.append(acc)
    return result


def _linear_regression(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Perform ordinary-least-squares linear regression.

    Computes the slope *m* and intercept *b* of the line ``y = m*x + b`` that
    minimises the sum of squared residuals.  Used in log-log space to fit
    power-law models: ``log(T) = m * log(n) + b`` implies
    ``T ≈ exp(b) * n^m``.

    Parameters
    ----------
    xs:
        Independent variable observations.
    ys:
        Dependent variable observations.

    Returns
    -------
    tuple[float, float]
        ``(slope, intercept)``.  Returns ``(0.0, 0.0)`` for degenerate inputs.
    """
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys))
    den = sum((xi - mean_x) ** 2 for xi in xs)
    if den == 0.0:
        return 0.0, mean_y
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _local_exponent(xs: List[float], ys: List[float]) -> float:
    """Estimate the local power-law exponent for a small window of data.

    Fits ``log T = α log n + β`` to a short sub-sequence and returns the
    slope ``α``.  Used by the characterizer to determine the scaling regime on
    each side of a transition point.

    Parameters
    ----------
    xs:
        Input sizes (positive integers or floats).
    ys:
        Timing or cost values (positive floats).

    Returns
    -------
    float
        The estimated exponent.  Clamped to [0.0, 10.0].
    """
    if len(xs) < 2:
        return 1.0  # conservative fallback
    log_xs = [_safe_log(float(x)) for x in xs]
    log_ys = [_safe_log(max(float(y), 1e-12)) for y in ys]
    slope, _ = _linear_regression(log_xs, log_ys)
    return _clamp(slope, 0.0, 10.0)


# ===========================================================================
# PhaseChangeScanner
# ===========================================================================


@dataclass(slots=True)
class PhaseChangeScanner:
    """Scans scaling data for discontinuities using a sliding-window detector.

    ``PhaseChangeScanner`` is the first stage of the phase-change detection
    pipeline.  It examines a sequence of (size, timing) pairs and identifies
    *candidate* transition indices – positions in the sequence where the local
    scaling behaviour changes abruptly.

    The detector works by computing local exponent estimates in a sliding window
    of width ``self.window`` and flagging positions where the absolute change
    in exponent exceeds ``self.threshold``.  The result is a list of candidate
    integer indices into the input sequence.

    Attributes
    ----------
    data:
        List of ``(size, timing)`` tuples accumulated by the caller.
    window:
        Width of the sliding window used to estimate local exponents.
        Larger windows produce smoother estimates but may miss short transitions.
    threshold:
        Minimum absolute change in local exponent needed to declare a
        candidate discontinuity.
    """

    data: List[Tuple[float, float]] = field(default_factory=list)
    window: int = DEFAULT_WINDOW
    threshold: float = DEFAULT_THRESHOLD

    def scan(self) -> List[int]:
        """Scan ``self.data`` and return candidate transition indices.

        Computes a sequence of local exponent estimates using overlapping
        windows of width ``self.window``, then identifies positions where the
        absolute first-order difference in exponents exceeds ``self.threshold``.
        Those positions are returned as candidate transition indices.

        The method requires at least ``2 * self.window`` data points to produce
        meaningful results.  Fewer points will yield an empty list.

        Returns
        -------
        list[int]
            Sorted list of candidate transition indices (into ``self.data``).
            May be empty if no discontinuities are detected.
        """
        if len(self.data) < 2 * max(self.window, MIN_WINDOW):
            # Insufficient data – cannot detect transitions reliably.
            return []

        xs = [float(d[0]) for d in self.data]
        ys = [float(d[1]) for d in self.data]
        n = len(xs)
        w = max(self.window, MIN_WINDOW)

        # Compute local exponent for each window position.
        local_exponents: List[float] = []
        for i in range(n):
            lo = max(0, i - w + 1)
            hi = i + 1
            local_exponents.append(_local_exponent(xs[lo:hi], ys[lo:hi]))

        # Compute first-order differences of the local exponent sequence.
        diffs = _finite_differences(local_exponents)

        # Flag positions where the jump magnitude exceeds the threshold.
        candidates: List[int] = []
        for i, d in enumerate(diffs):
            if abs(d) >= self.threshold:
                # i+1 is the first index *after* the jump in the original data.
                candidates.append(i + 1)

        return sorted(set(candidates))

    def jump_at(self, idx: int) -> float:
        """Compute the exponent jump magnitude at a candidate index.

        Returns the absolute difference between the local exponent estimates
        immediately before and after *idx*.  Larger values indicate more
        dramatic phase transitions.

        Parameters
        ----------
        idx:
            A candidate transition index previously returned by ``scan()``.
            Must satisfy ``0 < idx < len(self.data)``.

        Returns
        -------
        float
            The absolute exponent jump at *idx*.  Returns 0.0 for out-of-range
            indices or insufficient surrounding data.
        """
        n = len(self.data)
        if idx <= 0 or idx >= n:
            return 0.0
        xs = [float(d[0]) for d in self.data]
        ys = [float(d[1]) for d in self.data]
        w = max(self.window, MIN_WINDOW)
        # Left window: up to w points ending at idx-1.
        lo_l = max(0, idx - w)
        exp_left = _local_exponent(xs[lo_l:idx], ys[lo_l:idx])
        # Right window: up to w points starting at idx.
        hi_r = min(n, idx + w)
        exp_right = _local_exponent(xs[idx:hi_r], ys[idx:hi_r])
        return abs(exp_right - exp_left)

    def is_discontinuous(self, idx: int) -> bool:
        """Determine whether the jump at *idx* is large enough to be a phase change.

        A position is considered discontinuous if its jump magnitude
        (computed by ``jump_at``) is at or above ``self.threshold``.  This is
        a binary predicate that can be used to filter candidate indices.

        Parameters
        ----------
        idx:
            An index into ``self.data``.

        Returns
        -------
        bool
            ``True`` if ``jump_at(idx) >= self.threshold``, else ``False``.
        """
        return self.jump_at(idx) >= self.threshold

    def smooth(self, sigma: float = 1.0) -> List[float]:
        """Apply Gaussian smoothing to the timing values in ``self.data``.

        Convolves the sequence of timing values with a Gaussian kernel of
        standard deviation *sigma* (in index units).  Smoothing reduces the
        effect of noise on the finite-difference scanner while preserving the
        position of genuine jumps.

        The smoothed values are returned as a new list; ``self.data`` is not
        modified.

        Parameters
        ----------
        sigma:
            Standard deviation of the Gaussian kernel.  A value of 0 returns
            the raw timings unchanged (delta kernel).

        Returns
        -------
        list[float]
            The smoothed timing sequence, with the same length as ``self.data``.
        """
        ys = [float(d[1]) for d in self.data]
        if len(ys) == 0:
            return []
        # Determine kernel radius: 3 standard deviations covers 99.7% of mass.
        radius = max(1, int(math.ceil(3 * sigma)))
        kernel = _gaussian_kernel(sigma, radius)
        return _convolve(ys, kernel)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the scanner to a plain dictionary.

        Captures the configuration (window, threshold) and the accumulated
        data as a list of ``[size, timing]`` pairs.  Candidate transitions are
        also pre-computed and stored so the dict is self-contained.

        Returns
        -------
        dict
            A JSON-serialisable dictionary.
        """
        return {
            "_type": "PhaseChangeScanner",
            "_version": MODULE_VERSION,
            "window": self.window,
            "threshold": self.threshold,
            "n_points": len(self.data),
            "data": [[float(d[0]), float(d[1])] for d in self.data],
            "candidate_transitions": self.scan(),
        }

    def __repr__(self) -> str:
        """Return an unambiguous developer representation.

        Shows the number of data points, the window size, and the threshold so
        the scanner's configuration can be assessed without inspecting its
        attributes individually.
        """
        return (
            f"PhaseChangeScanner("
            f"n_points={len(self.data)}, "
            f"window={self.window}, "
            f"threshold={self.threshold!r})"
        )

    def __str__(self) -> str:
        """Return a concise human-readable description.

        Intended for log output and user-facing summaries.  Shows the number
        of data points, the configured window, and the number of candidate
        transitions found by the last ``scan()`` call.
        """
        candidates = self.scan()
        return (
            f"[PhaseChangeScanner] "
            f"{len(self.data)} points, "
            f"window={self.window}, "
            f"{len(candidates)} candidate transition(s)"
        )


# ===========================================================================
# TransitionPointFinder
# ===========================================================================


@dataclass(slots=True)
class TransitionPointFinder:
    """Refines rough transition indices to precise floating-point transition points.

    ``TransitionPointFinder`` is the second stage of the pipeline.  It accepts
    the list of candidate integer indices produced by ``PhaseChangeScanner`` and
    refines each one to a precise floating-point *x*-coordinate (input size)
    using binary search over the derivative landscape.

    The key insight is that the exponent derivative is highest near the true
    transition point.  By evaluating left/right exponent estimates at
    progressively finer bisections of the index interval, the finder converges
    to within ``self.precision`` of the true transition.

    Attributes
    ----------
    scanner_results:
        List of rough candidate indices accumulated from calls to ``refine``
        or ``all_transitions``.
    precision:
        Target precision for the binary search (in units of index spacing).
        The search terminates when the interval width falls below this value.
    """

    scanner_results: List[int] = field(default_factory=list)
    precision: float = DEFAULT_PRECISION

    def refine(
        self, rough_idx: int, xs: List[float], ys: List[float]
    ) -> float:
        """Refine a rough transition index to a precise x-coordinate.

        Starting from *rough_idx*, performs binary search in the interval
        ``[rough_idx - window, rough_idx + window]`` (where window defaults to
        5 or the array half-length, whichever is smaller) to find the
        x-coordinate at which the exponent jump is maximised.

        The result is returned as a floating-point value in the same units as
        *xs* (i.e. an interpolated input size), making it easier to compare
        transitions across datasets with different sampling densities.

        Parameters
        ----------
        rough_idx:
            The approximate index of the transition (as returned by the scanner).
        xs:
            Full list of input sizes.
        ys:
            Full list of timing values corresponding to *xs*.

        Returns
        -------
        float
            The refined transition x-coordinate.  If refinement fails (e.g.
            insufficient surrounding data), falls back to ``xs[rough_idx]``.
        """
        n = len(xs)
        if rough_idx <= 0 or rough_idx >= n:
            # Guard: return the boundary value if the index is out of range.
            return float(xs[0] if n > 0 else 0)

        # Determine the search interval (clamped to valid array bounds).
        half_win = min(5, max(1, n // 4))
        lo_idx = max(0, rough_idx - half_win)
        hi_idx = min(n - 1, rough_idx + half_win)

        # Map index bounds to x-coordinate bounds.
        x_lo = float(xs[lo_idx])
        x_hi = float(xs[hi_idx])

        return self.binary_search_transition(lo_idx, hi_idx, xs, ys)

    def binary_search_transition(
        self,
        lo: int,
        hi: int,
        xs: List[float],
        ys: List[float],
    ) -> float:
        """Locate the transition x-coordinate via binary search over indices.

        Iteratively bisects the integer index interval ``[lo, hi]`` and
        evaluates the exponent jump at the midpoint.  The half that contains
        the larger jump is retained and the search continues until the interval
        is narrower than ``self.precision`` or ``MAX_BINARY_SEARCH_DEPTH``
        iterations are exhausted.

        Parameters
        ----------
        lo:
            Left boundary index (inclusive).
        hi:
            Right boundary index (inclusive).
        xs:
            Full list of input sizes.
        ys:
            Full list of timing values.

        Returns
        -------
        float
            The interpolated x-coordinate of the detected transition.  Returns
            the midpoint x-value of the final interval when the search
            converges.
        """
        n = len(xs)
        if lo < 0:
            lo = 0
        if hi >= n:
            hi = n - 1
        if lo >= hi:
            return float(xs[lo]) if n > 0 else 0.0

        depth = 0
        while (hi - lo) > 1 and depth < MAX_BINARY_SEARCH_DEPTH:
            mid = (lo + hi) // 2
            # Compute jump magnitude on each side of the midpoint.
            jump_lo = _jump_magnitude(lo, mid, xs, ys)
            jump_hi = _jump_magnitude(mid, hi, xs, ys)
            if jump_lo >= jump_hi:
                hi = mid
            else:
                lo = mid
            depth += 1

        # Interpolate: return the x-value at the midpoint of the final interval.
        mid_idx = (lo + hi) // 2
        return float(xs[mid_idx])

    def all_transitions(
        self, xs: List[float], ys: List[float]
    ) -> List[float]:
        """Refine all stored scanner results to precise transition x-coordinates.

        Iterates over ``self.scanner_results`` and calls ``refine`` for each
        rough index, collecting the results into a deduplicated, sorted list.
        Duplicate refined coordinates (within ``self.precision``) are merged
        into a single entry.

        Parameters
        ----------
        xs:
            Full list of input sizes.
        ys:
            Full list of timing values.

        Returns
        -------
        list[float]
            Sorted list of refined transition x-coordinates.  May be empty
            if ``scanner_results`` is empty or all refinements fail.
        """
        refined: List[float] = []
        for idx in self.scanner_results:
            tx = self.refine(idx, xs, ys)
            refined.append(tx)

        # Deduplicate: merge values within precision of each other.
        if not refined:
            return []
        refined.sort()
        merged: List[float] = [refined[0]]
        for val in refined[1:]:
            if abs(val - merged[-1]) > self.precision:
                merged.append(val)
        return merged

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the finder to a plain dictionary.

        Captures the precision setting and the list of stored scanner results.

        Returns
        -------
        dict
            A JSON-serialisable dictionary.
        """
        return {
            "_type": "TransitionPointFinder",
            "_version": MODULE_VERSION,
            "precision": self.precision,
            "n_scanner_results": len(self.scanner_results),
            "scanner_results": list(self.scanner_results),
        }

    def __repr__(self) -> str:
        """Return an unambiguous developer representation.

        Shows the number of stored scanner results and the precision setting
        so the finder's state can be assessed at a glance.
        """
        return (
            f"TransitionPointFinder("
            f"n_scanner_results={len(self.scanner_results)}, "
            f"precision={self.precision!r})"
        )


# ---------------------------------------------------------------------------
# Internal helper used by TransitionPointFinder
# ---------------------------------------------------------------------------


def _jump_magnitude(
    lo: int, hi: int, xs: List[float], ys: List[float]
) -> float:
    """Compute the exponent-jump magnitude across the midpoint of [lo, hi].

    Splits the index interval at ``mid = (lo + hi) // 2`` and estimates the
    local exponent on each half.  Returns the absolute difference, which is
    a proxy for how dramatic the phase change is at the midpoint.

    Parameters
    ----------
    lo:
        Left boundary index.
    hi:
        Right boundary index.
    xs:
        Full input-size list.
    ys:
        Full timing list.

    Returns
    -------
    float
        Absolute exponent difference across the midpoint.
    """
    mid = (lo + hi) // 2
    # Guard against degenerate intervals.
    if mid <= lo or mid >= hi:
        return 0.0
    exp_left = _local_exponent(xs[lo:mid], ys[lo:mid])
    exp_right = _local_exponent(xs[mid:hi], ys[mid:hi])
    return abs(exp_right - exp_left)


# ===========================================================================
# PhaseCharacterizer
# ===========================================================================


@dataclass(slots=True)
class PhaseCharacterizer:
    """Characterises scaling regimes on each side of detected transition points.

    ``PhaseCharacterizer`` is the third and final analysis stage.  For each
    transition point (a floating-point x-coordinate returned by
    ``TransitionPointFinder``) it:

    1. Splits the dataset into the *left* sub-sequence (data before the
       transition) and the *right* sub-sequence (data after the transition).
    2. Fits a power-law exponent to each sub-sequence.
    3. Wraps the results in a ``PhaseChange`` record (or an equivalent dict
       when the models module is unavailable).

    The characterizer also supports aggregating all transitions via
    ``characterize_all()``, which processes every stored transition in order.

    Attributes
    ----------
    transitions:
        List of transition x-coordinates (floats) to characterise.
    data:
        List of ``(size, timing)`` tuples from which sub-sequences are
        extracted.
    domain_size:
        Nominal domain size used for scaling the characterization metadata.
    """

    transitions: List[float] = field(default_factory=list)
    data: List[Tuple[float, float]] = field(default_factory=list)
    domain_size: int = 1000

    def characterize_left(self, t: float) -> Any:
        """Characterise the scaling regime to the left of transition *t*.

        Extracts all data points with x < *t* and fits a power-law exponent to
        them.  The result is returned as a ``ScalingRegime``-compatible dict
        (or enum value when the models module is available).

        Parameters
        ----------
        t:
            The transition x-coordinate (input size) separating the two phases.

        Returns
        -------
        ScalingRegime or dict
            A regime description for the sub-sequence to the left of *t*.
            Returns a dict with ``"side": "left"`` and the fitted exponent.
        """
        xs = [float(d[0]) for d in self.data if float(d[0]) < t]
        ys = [float(d[1]) for d in self.data if float(d[0]) < t]
        alpha = _local_exponent(xs, ys) if len(xs) >= 2 else 1.0
        return {
            "side": SIDE_LEFT,
            "transition_x": t,
            "n_points": len(xs),
            "exponent": round(alpha, 6),
            "label": _label_from_exponent(alpha),
        }

    def characterize_right(self, t: float) -> Any:
        """Characterise the scaling regime to the right of transition *t*.

        Extracts all data points with x >= *t* and fits a power-law exponent
        to them.  Symmetric to ``characterize_left``.

        Parameters
        ----------
        t:
            The transition x-coordinate (input size) separating the two phases.

        Returns
        -------
        ScalingRegime or dict
            A regime description for the sub-sequence to the right of *t*.
            Returns a dict with ``"side": "right"`` and the fitted exponent.
        """
        xs = [float(d[0]) for d in self.data if float(d[0]) >= t]
        ys = [float(d[1]) for d in self.data if float(d[0]) >= t]
        alpha = _local_exponent(xs, ys) if len(xs) >= 2 else 1.0
        return {
            "side": SIDE_RIGHT,
            "transition_x": t,
            "n_points": len(xs),
            "exponent": round(alpha, 6),
            "label": _label_from_exponent(alpha),
        }

    def build_phase_change(self, t: float) -> Any:
        """Build a complete ``PhaseChange`` record for transition *t*.

        Combines the left and right characterizations produced by
        ``characterize_left`` and ``characterize_right`` into a single record
        that captures the full context of the phase transition.

        Parameters
        ----------
        t:
            The transition x-coordinate (input size).

        Returns
        -------
        PhaseChange or dict
            A ``PhaseChange``-compatible dictionary containing both regimes,
            the transition x-coordinate, a UID, and a timestamp.
        """
        left_regime = self.characterize_left(t)
        right_regime = self.characterize_right(t)
        # Compute the jump magnitude: how large is the exponent change?
        delta_alpha = abs(
            right_regime.get("exponent", 1.0) - left_regime.get("exponent", 1.0)
        )
        return {
            "_type": "PhaseChange",
            "_version": MODULE_VERSION,
            "uid": _uid(),
            "transition_x": t,
            "delta_exponent": round(delta_alpha, 6),
            "left_regime": left_regime,
            "right_regime": right_regime,
            "domain_size": self.domain_size,
            "created_at": _utcnow(),
            "significant": delta_alpha >= DEFAULT_THRESHOLD,
        }

    def characterize_all(self) -> List[Any]:
        """Characterise all stored transitions and return a list of phase changes.

        Iterates over ``self.transitions``, calls ``build_phase_change`` for
        each, and returns the assembled list of ``PhaseChange``-compatible
        dicts.  Transitions that cannot be characterised (e.g. because there
        are insufficient data points on one side) are still included but
        marked with ``"significant": False``.

        Returns
        -------
        list[PhaseChange or dict]
            One phase-change record per transition in ``self.transitions``.
        """
        results: List[Any] = []
        for t in sorted(self.transitions):
            pc = self.build_phase_change(t)
            results.append(pc)
        return results

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the characterizer to a plain dictionary.

        Captures the transition list, domain size, and the number of data
        points.  The full dataset is also serialised as a compact list of
        ``[x, y]`` pairs.

        Returns
        -------
        dict
            A JSON-serialisable dictionary.
        """
        return {
            "_type": "PhaseCharacterizer",
            "_version": MODULE_VERSION,
            "transitions": list(self.transitions),
            "domain_size": self.domain_size,
            "n_points": len(self.data),
            "data": [[float(d[0]), float(d[1])] for d in self.data],
        }

    def __repr__(self) -> str:
        """Return an unambiguous developer representation.

        Shows the number of stored transitions and data points so the
        characterizer's state can be assessed at a glance in a REPL.
        """
        return (
            f"PhaseCharacterizer("
            f"n_transitions={len(self.transitions)}, "
            f"n_points={len(self.data)}, "
            f"domain_size={self.domain_size})"
        )


# ---------------------------------------------------------------------------
# Internal helper used by PhaseCharacterizer
# ---------------------------------------------------------------------------


def _label_from_exponent(alpha: float) -> str:
    """Map a numeric exponent to a human-readable complexity label.

    Uses the same threshold logic as ``AsymptoticAnalyzer.detect_regime`` in
    ``complexity_analysis.py`` so that the two modules produce consistent
    complexity labels.

    Parameters
    ----------
    alpha:
        The power-law exponent to classify.

    Returns
    -------
    str
        A label such as ``"O(n)"``, ``"O(n^2)"``, or ``"O(2^n)"``.
    """
    if alpha < 0.05:
        return "O(1)"
    if alpha < 0.15:
        return "O(log n)"
    if alpha < 0.65:
        return "O(sqrt n)"
    if alpha < 1.25:
        return "O(n)"
    if alpha < 1.55:
        return "O(n log n)"
    if alpha < 2.25:
        return "O(n^2)"
    if alpha < 3.25:
        return "O(n^3)"
    if alpha < 6.0:
        return f"O(n^{alpha:.1f})"
    return "O(2^n)"


# ===========================================================================
# PhaseChangeRunner
# ===========================================================================


@dataclass(slots=True)
class PhaseChangeRunner:
    """Orchestrates the full phase-change detection pipeline.

    ``PhaseChangeRunner`` ties together ``PhaseChangeScanner``,
    ``TransitionPointFinder``, and ``PhaseCharacterizer`` into a single
    cohesive object.  Callers invoke ``run(xs, ys)`` with a sequence of input
    sizes and timing values; the runner executes all three pipeline stages and
    returns a list of ``PhaseChange`` records.

    The runner keeps its sub-components accessible as attributes so that
    callers can inspect intermediate results (e.g. the raw scan candidates, the
    refined transition coordinates, or the per-regime characterizations) after
    the pipeline has run.

    Attributes
    ----------
    scanner:
        The ``PhaseChangeScanner`` instance used for candidate detection.
    finder:
        The ``TransitionPointFinder`` instance used for refinement.
    characterizer:
        The ``PhaseCharacterizer`` instance used for regime characterization.
    _results:
        Accumulated list of ``PhaseChange`` records from previous ``run``
        calls.
    """

    scanner: PhaseChangeScanner = field(default_factory=PhaseChangeScanner)
    finder: TransitionPointFinder = field(default_factory=TransitionPointFinder)
    characterizer: PhaseCharacterizer = field(default_factory=PhaseCharacterizer)
    _results: List[Any] = field(default_factory=list)

    def run(self, xs: List[float], ys: List[float]) -> List[Any]:
        """Execute the full phase-change detection pipeline.

        Performs the following steps in order:
        1. Populates the scanner's ``data`` attribute with ``zip(xs, ys)``.
        2. Calls ``scanner.scan()`` to obtain candidate transition indices.
        3. Stores the candidates in ``finder.scanner_results``.
        4. Calls ``finder.all_transitions(xs, ys)`` to refine each candidate.
        5. Populates the characterizer's ``data`` and ``transitions``
           attributes.
        6. Calls ``characterizer.characterize_all()`` and appends the results
           to ``self._results``.

        Parameters
        ----------
        xs:
            List of input sizes (independent variable).
        ys:
            List of timing or cost values (dependent variable).

        Returns
        -------
        list[PhaseChange or dict]
            A list of phase-change records for the current run.  Empty if no
            transitions are detected.
        """
        # Step 1: Populate the scanner.
        self.scanner.data = list(zip([float(x) for x in xs], [float(y) for y in ys]))

        # Step 2: Scan for candidate indices.
        candidates = self.scanner.scan()

        # Step 3: Store candidates in the finder.
        self.finder.scanner_results = list(candidates)

        # Step 4: Refine candidates to precise x-coordinates.
        float_xs = [float(x) for x in xs]
        float_ys = [float(y) for y in ys]
        transitions = self.finder.all_transitions(float_xs, float_ys)

        # Step 5: Populate the characterizer.
        self.characterizer.data = self.scanner.data
        self.characterizer.transitions = transitions

        # Step 6: Characterise all transitions.
        phase_changes = self.characterizer.characterize_all()
        self._results.extend(phase_changes)

        return phase_changes

    def summary(self) -> Dict[str, Any]:
        """Return a structured summary of all accumulated phase changes.

        Produces a dictionary with metadata (run timestamp, total transition
        count, module version) and a compact list of transition summaries that
        includes the transition x-coordinate, delta exponent, and side labels.

        Returns
        -------
        dict
            A JSON-serialisable summary dictionary.
        """
        transition_summaries = []
        for pc in self._results:
            if isinstance(pc, dict):
                transition_summaries.append({
                    "transition_x": pc.get("transition_x"),
                    "delta_exponent": pc.get("delta_exponent"),
                    "significant": pc.get("significant", False),
                    "left_label": pc.get("left_regime", {}).get("label"),
                    "right_label": pc.get("right_regime", {}).get("label"),
                })
        return {
            "_type": "PhaseChangeSummary",
            "_version": MODULE_VERSION,
            "generated_at": _utcnow(),
            "n_transitions": len(self._results),
            "transitions": transition_summaries,
        }

    def reset(self) -> None:
        """Reset the runner and all sub-components to a pristine state.

        Clears the scanner's data, the finder's scanner results, the
        characterizer's transitions and data, and the accumulated results
        list.  Preserves the configuration of each sub-component (window,
        threshold, precision).
        """
        self.scanner.data = []
        self.finder.scanner_results = []
        self.characterizer.data = []
        self.characterizer.transitions = []
        self._results.clear()

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the runner and all sub-components to a plain dictionary.

        Includes serialisations of the scanner, finder, and characterizer as
        well as the accumulated summary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary capturing the complete runner state.
        """
        return {
            "_type": "PhaseChangeRunner",
            "_version": MODULE_VERSION,
            "scanner": self.scanner.to_dict(),
            "finder": self.finder.to_dict(),
            "characterizer": self.characterizer.to_dict(),
            "summary": self.summary(),
        }

    def __repr__(self) -> str:
        """Return an unambiguous developer representation.

        Shows the number of accumulated phase-change results and the state of
        the scanner (number of data points) so the runner can be assessed at
        a glance.
        """
        return (
            f"PhaseChangeRunner("
            f"n_results={len(self._results)}, "
            f"scanner_points={len(self.scanner.data)})"
        )


# ===========================================================================
# Module-level free functions
# ===========================================================================


def detect_phase_changes(
    xs: List[float], ys: List[float]
) -> List[Any]:
    """Detect phase changes in a scaling dataset using the full pipeline.

    This is the primary public API for one-shot phase-change detection.  It
    constructs a ``PhaseChangeRunner`` with default configuration, runs it on
    the provided data, and returns the list of detected ``PhaseChange`` records.

    Internally this function constructs a ``PhaseChangeScanner`` with the
    default window and threshold, a ``TransitionPointFinder`` with the default
    precision, and a ``PhaseCharacterizer`` with the default domain size.
    Callers needing non-default configuration should construct the runner
    manually.

    Parameters
    ----------
    xs:
        List of input sizes (independent variable).  Should be positive and
        strictly increasing for best results.
    ys:
        List of timing or cost values (dependent variable) corresponding to
        each entry in *xs*.  Should be non-negative.

    Returns
    -------
    list[PhaseChange or dict]
        A list of phase-change records, one per detected transition.  The list
        may be empty if no phase changes are found.  Each record is a
        dictionary with keys including ``"transition_x"``, ``"delta_exponent"``,
        ``"left_regime"``, ``"right_regime"``, ``"significant"``, ``"uid"``,
        and ``"created_at"``.

    Examples
    --------
    >>> import math
    >>> xs = list(range(1, 201))
    >>> ys = [math.log(n) * n if n <= 100 else n ** 2 for n in xs]
    >>> changes = detect_phase_changes(xs, ys)
    >>> len(changes) >= 1
    True
    """
    runner = PhaseChangeRunner()
    return runner.run(xs, ys)


def characterize_phases(
    transitions: List[float],
    xs: List[float],
    ys: List[float],
) -> List[Any]:
    """Characterise scaling regimes around a list of pre-identified transitions.

    Unlike ``detect_phase_changes``, which runs the full detection pipeline
    from scratch, this function accepts *externally provided* transition
    x-coordinates (e.g. from a theoretical model or a previous analysis run)
    and uses a ``PhaseCharacterizer`` to fit regimes on each side.

    This is useful when the transition points are known a priori (e.g. from
    cache-size thresholds or theoretical break-even points) and only the
    regime characterization step needs to be performed.

    Parameters
    ----------
    transitions:
        A list of transition x-coordinates (input sizes) at which phase
        changes are believed to occur.  May be empty, in which case an empty
        list is returned.
    xs:
        Full list of input sizes.
    ys:
        Full list of timing or cost values corresponding to *xs*.

    Returns
    -------
    list[PhaseChange or dict]
        A list of ``PhaseChange``-compatible dicts, one per entry in
        *transitions*.  Each dict contains the left and right regime
        characterisations, the delta exponent, and metadata.

    Notes
    -----
    * Transitions outside the range ``[min(xs), max(xs)]`` produce
      characterisations where one side has zero data points; those are still
      included but ``significant`` will be ``False``.
    * The function does *not* modify *xs*, *ys*, or *transitions* in place.
    """
    char = PhaseCharacterizer(
        transitions=list(transitions),
        data=list(zip([float(x) for x in xs], [float(y) for y in ys])),
        domain_size=max(int(x) for x in xs) if xs else 1000,
    )
    return char.characterize_all()
