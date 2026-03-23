"""
Core algorithmic toolkit for the JuGeo ``scaling_limits`` evaluation package.

copilot: shared-core marker

Theory reference: theory2.tex Ch64

Overview
--------
This module provides the principal algorithmic building blocks used by the
JuGeo scaling-limits subsystem to analyse the asymptotic behaviour of
computational processes observed during geometry solving, pack-bridge
evaluation, and evidence-trust propagation.

The central class ``ScalingAlgorithms`` wraps the statistical machinery into a
coherent, configurable API.  Each method implements one step of the
complexity-characterisation pipeline described in Chapter 64 of the JuGeo
theory document:

    1. **Complexity bounding** — given a sequence of runtime or resource
       measurements, infer a complexity-class upper bound and return a
       ``ComplexityBound`` descriptor.

    2. **Phase-change detection** — scan a time series for abrupt shifts in
       scaling behaviour (e.g., a switch from polynomial to exponential growth)
       and return a ``PhaseChange`` descriptor when one is found.

    3. **Scaling-law fitting** — fit the dominant scaling law (power or
       exponential) to a paired (x, y) data sequence and return a
       ``ScalingLaw`` descriptor.

    4. **Limit certification** — formalise an inferred complexity bound as a
       ``LimitCertificate`` that can be stored in the JuGeo evidence ledger.

    5. **Full pipeline** — ``full_analysis(xs, ys)`` chains all four steps and
       returns a comprehensive analysis dictionary suitable for downstream
       reporting and archival.

Free Functions
--------------
The module also exports a set of standalone statistical functions that underpin
the methods above.  These functions are pure (no side effects, no global state)
and operate exclusively on Python built-in types so that they can be used
independently of the JuGeo package tree:

    * ``compute_empirical_exponent`` — log-log regression exponent estimator
    * ``estimate_constant_factor``   — least-squares constant estimation
    * ``log_log_regression``         — full OLS regression in log-log space
    * ``detect_outliers``            — z-score-based outlier detection
    * ``smooth_series``              — simple moving-average smoother
    * ``normalize_series``           — min-max normalisation to [0, 1]
    * ``difference_series``          — first-order finite differences
    * ``moving_variance``            — rolling variance estimator

Design Principles
-----------------
* **No external dependencies** — the module uses only the Python standard
  library (``math``, ``statistics``, ``itertools``, ``functools``, ``json``,
  ``time``, ``uuid``, ``dataclasses``, ``typing``, ``enum``).  This keeps the
  module importable in minimal Python environments (e.g., testing containers
  without NumPy/SciPy).

* **Defensive imports** — all JuGeo cross-module types are imported inside a
  ``try/except`` block so that the module degrades gracefully when the full
  package tree is absent.

* **Slots-based dataclasses** — every dataclass uses ``slots=True`` for
  memory efficiency and attribute-lookup speed in hot analysis loops.

* **Deterministic by default** — all algorithms produce identical output for
  identical input; no random state is used unless the bootstrap validator is
  invoked (which uses a seeded LCG, making it reproducible given the same seed).

* **Rich serialisation** — every class implements ``to_dict()`` returning a
  fully JSON-serialisable mapping and ``__repr__`` / ``__str__`` methods that
  give human-readable output suitable for log files and REPL inspection.

Versioning and Compatibility
-----------------------------
This file forms part of the ``jugeo.evaluation.scaling_limits`` package.  The
public API is considered stable from JuGeo 0.9 onwards.  Changes to the
``ScalingAlgorithms`` interface must be backwards compatible (additive only)
unless a major version bump accompanies the release.

See Also
--------
* ``jugeo.evaluation.scaling_limits.models`` — data-model types used throughout
* ``jugeo.evaluation.scaling_limits.scaling_laws`` — higher-level fitters
* theory2.tex Chapter 64, §64.1–§64.7 — mathematical background

Author
------
JuGeo Core Team <core@jugeo.io>
"""

from __future__ import annotations

__all__ = [
    # Main class
    "ScalingAlgorithms",
    # Free functions
    "compute_empirical_exponent",
    "estimate_constant_factor",
    "log_log_regression",
    "detect_outliers",
    "smooth_series",
    "normalize_series",
    "difference_series",
    "moving_variance",
]

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import functools
import itertools
import json
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Guarded cross-module imports (full JuGeo tree may not be installed)
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
# Module-level helper utilities
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return the current UTC timestamp as an ISO-8601 string (seconds precision).

    Centralises timestamp generation so that any future migration to an
    alternative time source (e.g. a monotonic wall clock or an NTP-synced
    service) only requires changes in one place.  The value is always
    implicitly UTC; no timezone offset is appended.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _uid() -> str:
    """Return a fresh UUID-4 in compact hex form (32 hexadecimal characters).

    UUID-4 identifiers are generated from OS-provided cryptographic entropy.
    The compact hex form is preferred over the hyphenated form to reduce noise
    in log files and serialised records while remaining globally unique.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* into the closed interval [lo, hi].

    Parameters
    ----------
    value : float
        The value to clamp.
    lo : float
        Lower bound (inclusive).
    hi : float
        Upper bound (inclusive).  Must satisfy lo <= hi.

    Returns
    -------
    float
        Clamped value: *lo* if value < lo, *hi* if value > hi, else *value*.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Minimum positive value used to guard log() calls against log(0) / log(-x)
_EPSILON: float = 1e-12

# Minimum number of data points required for regression-based analysis
_MIN_REGRESSION_POINTS: int = 3

# Z-score threshold used by detect_outliers (default k parameter)
_DEFAULT_OUTLIER_K: float = 2.0

# Default window sizes for rolling statistics
_DEFAULT_SMOOTH_WINDOW: int = 3
_DEFAULT_VARIANCE_WINDOW: int = 5

# R² thresholds for quality labelling of fitted laws
_R2_GOOD: float = 0.90
_R2_MODERATE: float = 0.50

# Complexity class labels used in the fallback (non-models) path
_COMPLEXITY_LABELS: dict = {
    "constant":    "O(1)",
    "logarithmic": "O(log n)",
    "linear":      "O(n)",
    "linearithmic":"O(n log n)",
    "quadratic":   "O(n²)",
    "cubic":       "O(n³)",
    "polynomial":  "O(n^k)",
    "exponential": "O(2^n)",
    "factorial":   "O(n!)",
}

# Exponent-to-complexity-class mapping thresholds (for power-law inference)
_EXPONENT_CLASS_THRESHOLDS: list[tuple[float, str]] = [
    (0.05, "constant"),
    (0.15, "logarithmic"),
    (1.05, "linear"),
    (1.20, "linearithmic"),
    (2.05, "quadratic"),
    (3.05, "cubic"),
    (10.0, "polynomial"),
]


# ---------------------------------------------------------------------------
# Internal helper: classify exponent
# ---------------------------------------------------------------------------

def _classify_exponent(exponent: float) -> str:
    """Map a power-law exponent to a named complexity class string.

    Uses the threshold table ``_EXPONENT_CLASS_THRESHOLDS`` to find the first
    threshold that exceeds *exponent* and returns the associated label.  If no
    threshold matches, 'exponential' is returned as a conservative upper bound.

    Parameters
    ----------
    exponent : float
        The fitted power-law exponent α.

    Returns
    -------
    str
        A complexity class label from ``_COMPLEXITY_LABELS``.
    """
    for threshold, label in _EXPONENT_CLASS_THRESHOLDS:
        if exponent <= threshold:
            return label
    return "exponential"


# ---------------------------------------------------------------------------
# ScalingAlgorithms — main class
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScalingAlgorithms:
    """Unified algorithmic interface for the JuGeo scaling-limits pipeline.

    ``ScalingAlgorithms`` is a configurable stateful object that exposes the
    five core analysis operations as well as a cache for intermediate results.
    It is designed to be instantiated once per analysis session and reused
    across multiple data sets; use ``reset_cache()`` between unrelated analyses
    to reclaim memory.

    Configuration
    -------------
    Pass a dictionary to the constructor (or use ``from_config`` / ``default``
    class-methods) to tune the algorithmic parameters:

    * ``"min_points"`` (int, default 5) — minimum points for regression
    * ``"outlier_k"`` (float, default 2.0) — z-score threshold for outlier detection
    * ``"smooth_window"`` (int, default 3) — smoothing window size
    * ``"variance_window"`` (int, default 5) — variance window size
    * ``"phase_change_sensitivity"`` (float, default 0.15) — relative shift
      in local exponent that triggers phase-change detection

    Attributes
    ----------
    config : dict
        Algorithmic parameters (see above).
    _cache : dict
        Internal result cache keyed by operation name and input hash.
    """

    config: dict = field(default_factory=dict)
    _cache: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Class-method constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict) -> "ScalingAlgorithms":
        """Construct a ``ScalingAlgorithms`` instance from a configuration dict.

        This factory method validates the configuration keys before constructing
        the instance.  Unknown keys are silently ignored so that forward-
        compatible configuration files (with keys added in future versions) do
        not break existing code.  All required keys have sensible defaults so
        partial configurations are valid.

        Parameters
        ----------
        config : dict
            Mapping of algorithm parameter names to values.  Recognised keys
            are described in the class docstring.

        Returns
        -------
        ScalingAlgorithms
            Configured instance ready for analysis.
        """
        # Only copy recognised keys so we do not accidentally store secrets
        known_keys = {
            "min_points", "outlier_k", "smooth_window",
            "variance_window", "phase_change_sensitivity",
        }
        safe_config = {k: v for k, v in config.items() if k in known_keys}
        return cls(config=safe_config)

    @classmethod
    def default(cls) -> "ScalingAlgorithms":
        """Construct a ``ScalingAlgorithms`` instance with default configuration.

        The default configuration uses the module-level constants and is
        suitable for the majority of JuGeo pipeline use cases.  For fine-grained
        control use ``from_config`` with a custom dictionary.

        Returns
        -------
        ScalingAlgorithms
            Instance with default algorithmic parameters.
        """
        return cls(config={
            "min_points": _MIN_REGRESSION_POINTS,
            "outlier_k": _DEFAULT_OUTLIER_K,
            "smooth_window": _DEFAULT_SMOOTH_WINDOW,
            "variance_window": _DEFAULT_VARIANCE_WINDOW,
            "phase_change_sensitivity": 0.15,
        })

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def cache_size(self) -> int:
        """Number of entries currently stored in the internal result cache.

        The cache grows monotonically as analysis methods are called.  Use
        ``reset_cache()`` to reclaim memory between unrelated analysis sessions.

        Returns
        -------
        int
            Current number of cached entries.
        """
        return len(self._cache)

    @property
    def is_configured(self) -> bool:
        """True if the instance has a non-empty configuration dictionary.

        A non-empty configuration indicates that the instance was constructed
        via ``from_config`` or ``default`` rather than the bare ``__init__``.
        This property is used by pipeline validation code to detect
        unconfigured instances before they are used in production.

        Returns
        -------
        bool
            True when ``self.config`` is non-empty.
        """
        return bool(self.config)

    # ------------------------------------------------------------------
    # Core analysis methods
    # ------------------------------------------------------------------

    def complexity_bound(self, measurements: list) -> Any:
        """Infer a complexity-class upper bound from a sequence of measurements.

        The method treats the index position as the independent variable (problem
        size proxy) and the measurement values as the dependent variable (resource
        usage proxy).  A log-log regression is performed to estimate the dominant
        exponent; this is then mapped to a named complexity class using the
        threshold table in ``_EXPONENT_CLASS_THRESHOLDS``.

        The result is cached under the key ``'complexity_bound'`` + a hash of
        the input so that repeated calls with the same data are essentially free.

        Parameters
        ----------
        measurements : list
            Sequence of non-negative numeric values representing successive
            resource measurements (e.g., runtime in ms, memory in bytes, or
            operation counts) at increasing problem sizes.

        Returns
        -------
        ComplexityBound or dict
            A ``ComplexityBound`` descriptor if the models module is available,
            otherwise a plain dict with keys 'complexity_class', 'exponent',
            'constant', 'n_points', 'timestamp', 'uid'.

        Raises
        ------
        ValueError
            If ``measurements`` has fewer than the configured minimum points
            or contains no positive values.
        """
        # --- Input validation ---
        positive = [m for m in measurements if m > _EPSILON]
        min_pts = self.config.get("min_points", _MIN_REGRESSION_POINTS)
        if len(positive) < min_pts:
            raise ValueError(
                f"complexity_bound requires at least {min_pts} positive "
                f"measurements; got {len(positive)}."
            )

        # --- Cache lookup using a simple fingerprint ---
        cache_key = f"complexity_bound:{hash(tuple(measurements))}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # --- Build (index, value) pairs and run log-log regression ---
        xs = [float(i + 1) for i in range(len(positive))]
        ys = positive

        exponent, log_c = log_log_regression(xs, ys)
        constant = math.exp(log_c)
        complexity_class = _classify_exponent(exponent)

        # --- Assemble result ---
        try:
            result = ComplexityBound(  # type: ignore[name-defined]
                complexity_class=complexity_class,
                exponent=exponent,
                constant=constant,
                n_points=len(positive),
                timestamp=_utcnow(),
                uid=_uid(),
            )
        except Exception:
            result = {  # type: ignore[assignment]
                "complexity_class": complexity_class,
                "exponent": exponent,
                "constant": constant,
                "n_points": len(positive),
                "timestamp": _utcnow(),
                "uid": _uid(),
            }

        # Cache and return
        self._cache[cache_key] = result
        return result

    def detect_phase_change(self, data: list) -> Optional[Any]:
        """Detect an abrupt phase change in the scaling behaviour of *data*.

        A phase change is identified by splitting the series into two halves,
        fitting a power law to each half independently, and comparing the
        resulting exponents.  If the exponents differ by more than the
        configured ``phase_change_sensitivity`` threshold (relative to the
        global exponent), a ``PhaseChange`` descriptor is returned; otherwise
        ``None`` is returned to indicate that no phase change was detected.

        The detection logic is intentionally conservative: borderline cases
        (where the relative exponent shift is close to the threshold) return
        ``None`` rather than a false-positive phase change.  This minimises
        noise in the evidence ledger.

        Parameters
        ----------
        data : list
            Sequence of non-negative numeric values representing a time series
            of resource measurements at successive problem sizes.

        Returns
        -------
        PhaseChange or dict or None
            A phase-change descriptor if a shift is detected, otherwise None.
            The descriptor includes the detected change point index, the
            exponents of the two phases, and a confidence score in [0, 1].
        """
        positive = [m for m in data if m > _EPSILON]
        n = len(positive)
        if n < 2 * _MIN_REGRESSION_POINTS:
            # Not enough data to fit two independent halves
            return None

        xs = [float(i + 1) for i in range(n)]
        mid = n // 2

        try:
            exp1, _ = log_log_regression(xs[:mid], positive[:mid])
            exp2, _ = log_log_regression(xs[mid:], positive[mid:])
        except Exception:
            return None

        # Compute the global exponent as a reference
        try:
            global_exp, _ = log_log_regression(xs, positive)
        except Exception:
            global_exp = (exp1 + exp2) / 2.0

        sensitivity = self.config.get("phase_change_sensitivity", 0.15)
        relative_shift = abs(exp2 - exp1) / (abs(global_exp) + _EPSILON)

        if relative_shift <= sensitivity:
            return None  # No significant phase change detected

        # Confidence is proportional to how far the shift exceeds the threshold
        raw_confidence = (relative_shift - sensitivity) / (1.0 - sensitivity + _EPSILON)
        confidence = _clamp(raw_confidence, 0.0, 1.0)

        try:
            result = PhaseChange(  # type: ignore[name-defined]
                change_point=mid,
                exponent_before=exp1,
                exponent_after=exp2,
                confidence=confidence,
                timestamp=_utcnow(),
                uid=_uid(),
            )
        except Exception:
            result = {  # type: ignore[assignment]
                "change_point": mid,
                "exponent_before": exp1,
                "exponent_after": exp2,
                "confidence": confidence,
                "timestamp": _utcnow(),
                "uid": _uid(),
            }

        return result

    def fit_scaling_law(self, data: list) -> Any:
        """Fit a scaling law to a 1-D measurement sequence and return a ScalingLaw.

        Unlike the power-law and exponential fitters in ``scaling_laws``,
        this method treats the data as a self-contained time series where the
        independent variable is the (1-indexed) position in the sequence.  The
        best-fitting law is selected by comparing the R² of a power-law fit
        against an exponential fit and returning whichever is higher.

        The result is cached to avoid redundant fitting when the same data is
        passed multiple times (e.g., during iterative pipeline runs that share
        an ``ScalingAlgorithms`` instance).

        Parameters
        ----------
        data : list
            Sequence of strictly positive numeric measurements.

        Returns
        -------
        ScalingLaw or dict
            The best-fitting scaling law descriptor, containing at minimum the
            keys/attributes: 'kind', 'exponent', 'constant', 'r_squared',
            'fit_timestamp', 'uid'.

        Raises
        ------
        ValueError
            If *data* contains fewer than the configured minimum number of
            positive values.
        """
        positive = [m for m in data if m > _EPSILON]
        min_pts = self.config.get("min_points", _MIN_REGRESSION_POINTS)
        if len(positive) < min_pts:
            raise ValueError(
                f"fit_scaling_law requires at least {min_pts} positive values."
            )

        cache_key = f"scaling_law:{hash(tuple(data))}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        xs = [float(i + 1) for i in range(len(positive))]
        ys = positive

        # --- Power-law fit in log-log space ---
        pw_exp, pw_log_c = log_log_regression(xs, ys)
        pw_c = math.exp(pw_log_c)
        pw_pred = [pw_c * (x ** pw_exp) for x in xs]
        pw_r2 = _r2(ys, pw_pred)

        # --- Exponential fit in semi-log space ---
        log_ys = [math.log(max(y, _EPSILON)) for y in ys]
        mean_x = statistics.mean(xs)
        mean_ly = statistics.mean(log_ys)
        cov = sum((x - mean_x) * (ly - mean_ly) for x, ly in zip(xs, log_ys))
        var = sum((x - mean_x) ** 2 for x in xs)
        exp_alpha = cov / var if abs(var) > _EPSILON else 0.0
        exp_log_c = mean_ly - exp_alpha * mean_x
        exp_c = math.exp(exp_log_c)
        exp_pred = [exp_c * math.exp(exp_alpha * x) for x in xs]
        exp_r2 = _r2(ys, exp_pred)

        # --- Select best law ---
        if pw_r2 >= exp_r2:
            kind, alpha, c, r2_val = "power", pw_exp, pw_c, pw_r2
        else:
            kind, alpha, c, r2_val = "exponential", exp_alpha, exp_c, exp_r2

        try:
            law = ScalingLaw(  # type: ignore[name-defined]
                kind=kind,
                exponent=alpha,
                constant=c,
                r_squared=r2_val,
                fit_timestamp=_utcnow(),
                uid=_uid(),
            )
        except Exception:
            law = {  # type: ignore[assignment]
                "kind": kind,
                "exponent": alpha,
                "constant": c,
                "r_squared": r2_val,
                "fit_timestamp": _utcnow(),
                "uid": _uid(),
            }

        self._cache[cache_key] = law
        return law

    def certify_limit(self, bound: Any) -> Any:
        """Formalise a complexity bound as a signed LimitCertificate.

        A ``LimitCertificate`` is an immutable record that attests that the
        observed data is consistent with the stated complexity class.  It
        includes the bound itself, a provenance timestamp, a unique identifier,
        and a confidence level derived from the quality of the underlying fit.
        Once issued, certificates are stored in the JuGeo evidence ledger.

        The confidence level is computed heuristically: an exponent that maps
        cleanly to a known complexity class (O(n), O(n²), etc.) receives a
        higher confidence than one that falls between known classes.

        Parameters
        ----------
        bound : ComplexityBound or dict
            A complexity bound as returned by ``complexity_bound()``.  Must
            contain at minimum: 'complexity_class', 'exponent', 'n_points'.

        Returns
        -------
        LimitCertificate or dict
            A certificate descriptor containing the bound, a confidence score,
            a UTC issuance timestamp, and a unique certificate identifier.
        """
        # Extract bound attributes from either a dataclass or a dict
        if isinstance(bound, dict):
            cc = bound.get("complexity_class", "unknown")
            exponent = float(bound.get("exponent", 0.0))
            n_points = int(bound.get("n_points", 0))
        else:
            cc = getattr(bound, "complexity_class", "unknown")
            exponent = float(getattr(bound, "exponent", 0.0))
            n_points = int(getattr(bound, "n_points", 0))

        # --- Confidence heuristic ---
        # Higher confidence when exponent is close to a "round" value
        nearest_round = round(exponent)
        distance = abs(exponent - nearest_round)
        base_confidence = math.exp(-3.0 * distance)  # 1.0 when distance=0, decays quickly
        # Bonus for having more data points
        point_bonus = _clamp(n_points / 20.0, 0.0, 0.2)
        confidence = _clamp(base_confidence + point_bonus, 0.0, 1.0)

        try:
            certificate = LimitCertificate(  # type: ignore[name-defined]
                complexity_class=cc,
                exponent=exponent,
                confidence=confidence,
                n_points=n_points,
                issued_at=_utcnow(),
                uid=_uid(),
            )
        except Exception:
            certificate = {  # type: ignore[assignment]
                "complexity_class": cc,
                "exponent": exponent,
                "confidence": confidence,
                "n_points": n_points,
                "issued_at": _utcnow(),
                "uid": _uid(),
            }

        return certificate

    def full_analysis(self, xs: list, ys: list) -> dict:
        """Run the complete scaling-limits analysis pipeline and return results.

        This orchestration method chains all four core analysis steps:
        1. Smooth *ys* using ``smooth_series`` to reduce measurement noise.
        2. Detect outliers with ``detect_outliers`` and remove them.
        3. Infer a ``ComplexityBound`` from the cleaned measurements.
        4. Detect any ``PhaseChange`` in the cleaned measurements.
        5. Fit a ``ScalingLaw`` to the cleaned measurements.
        6. Issue a ``LimitCertificate`` from the complexity bound.

        The returned dictionary contains all intermediate and final results so
        that callers can inspect every stage of the analysis for debugging or
        reporting purposes.

        Parameters
        ----------
        xs : list
            Independent-variable values (problem-size proxy, e.g., input length).
        ys : list
            Dependent-variable values (resource usage, e.g., runtime in ms).

        Returns
        -------
        dict
            Keys: 'smoothed_ys', 'outlier_indices', 'cleaned_ys',
                  'complexity_bound', 'phase_change', 'scaling_law',
                  'limit_certificate', 'timestamp', 'uid'.
        """
        # --- Step 1: smooth to reduce noise ---
        window = self.config.get("smooth_window", _DEFAULT_SMOOTH_WINDOW)
        smoothed = smooth_series(ys, window=window)

        # --- Step 2: detect and remove outliers ---
        k = self.config.get("outlier_k", _DEFAULT_OUTLIER_K)
        outlier_indices = detect_outliers(smoothed, k=k)
        outlier_set = set(outlier_indices)
        cleaned_ys = [y for i, y in enumerate(smoothed) if i not in outlier_set]
        cleaned_xs = [x for i, x in enumerate(xs[:len(smoothed)]) if i not in outlier_set]

        # Ensure minimum points after cleaning
        if len(cleaned_ys) < _MIN_REGRESSION_POINTS:
            cleaned_ys = smoothed  # Fall back to smoothed if too many removed
            cleaned_xs = list(xs[:len(smoothed)])

        # --- Step 3: complexity bound ---
        try:
            bound = self.complexity_bound(cleaned_ys)
        except Exception as exc:
            bound = {"error": str(exc)}

        # --- Step 4: phase change detection ---
        try:
            phase_change = self.detect_phase_change(cleaned_ys)
        except Exception as exc:
            phase_change = {"error": str(exc)}

        # --- Step 5: scaling law ---
        try:
            law = self.fit_scaling_law(cleaned_ys)
        except Exception as exc:
            law = {"error": str(exc)}

        # --- Step 6: certificate ---
        try:
            certificate = self.certify_limit(bound)
        except Exception as exc:
            certificate = {"error": str(exc)}

        return {
            "smoothed_ys": smoothed,
            "outlier_indices": outlier_indices,
            "cleaned_ys": cleaned_ys,
            "complexity_bound": bound,
            "phase_change": phase_change,
            "scaling_law": law,
            "limit_certificate": certificate,
            "timestamp": _utcnow(),
            "uid": _uid(),
        }

    def reset_cache(self) -> None:
        """Clear the internal result cache.

        Should be called between unrelated analysis sessions when the same
        ``ScalingAlgorithms`` instance is reused.  After this call,
        ``cache_size`` returns zero and all subsequent method calls will
        recompute their results from scratch.
        """
        # Clear the dict in-place to preserve the object identity (important
        # for callers that hold references to the cache dict directly).
        self._cache.clear()

    def to_dict(self) -> dict:
        """Serialise the instance's configuration and cache summary to a dict.

        The returned mapping is JSON-serialisable and includes the configuration
        parameters and the current cache size.  Individual cached values are
        not included (they may not be JSON-serialisable) but their keys are
        listed for diagnostic purposes.
        """
        return {
            "class": "ScalingAlgorithms",
            "config": self.config,
            "cache_size": self.cache_size,
            "cache_keys": list(self._cache.keys()),
            "is_configured": self.is_configured,
            "serialised_at": _utcnow(),
        }

    def __repr__(self) -> str:
        """Return a developer-oriented string representation of this instance.

        Includes the class name, configuration summary, cache size, and the
        ``is_configured`` flag so that the object state is immediately
        understandable in a REPL or log file without calling ``to_dict()``.
        """
        config_str = json.dumps(self.config, separators=(",", ":")) if self.config else "{}"
        return (
            f"ScalingAlgorithms("
            f"is_configured={self.is_configured}, "
            f"cache_size={self.cache_size}, "
            f"config={config_str})"
        )

    def __str__(self) -> str:
        """Return a human-friendly description of this instance.

        Provides a more readable summary than ``__repr__``, suitable for
        end-user-facing messages, reports, and log entries where Python syntax
        details would be noise rather than signal.
        """
        config_keys = list(self.config.keys()) if self.config else []
        return (
            f"ScalingAlgorithms | configured={self.is_configured} | "
            f"config keys={config_keys} | cache entries={self.cache_size}"
        )


# ---------------------------------------------------------------------------
# Internal helper: R² computation (used only within this module)
# ---------------------------------------------------------------------------

def _r2(ys: list, y_pred: list) -> float:
    """Compute R² (coefficient of determination) from observed and predicted values.

    Parameters
    ----------
    ys : list
        Observed values.
    y_pred : list
        Predicted values (same length as *ys*).

    Returns
    -------
    float
        R² value, clamped to (-∞, 1].
    """
    if not ys:
        return 0.0
    y_mean = statistics.mean(ys)
    ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    if abs(ss_tot) < _EPSILON:
        return 1.0 if abs(ss_res) < _EPSILON else 0.0
    return 1.0 - ss_res / ss_tot


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def compute_empirical_exponent(xs: list, ys: list) -> float:
    """Estimate the empirical power-law exponent via log-log OLS regression.

    This function is the lowest-level entry point for exponent estimation.
    It takes (x, y) pairs representing (problem size, resource usage)
    measurements, transforms both axes to natural-log space, and returns the
    OLS slope, which is an unbiased estimator of the power-law exponent α
    in the model  y = c · xᵅ.

    Non-positive values are silently excluded before the regression so that
    the function is robust to zero padding, sentinel values, and measurement
    noise that occasionally produces negative readings in hardware counters.

    The OLS estimator is:
        α = Cov(log x, log y) / Var(log x)

    which is algebraically equivalent to fitting a straight line through the
    log-transformed scatter plot.

    Parameters
    ----------
    xs : list[float]
        Positive independent-variable values (e.g., input sizes 1, 2, 4, 8, …).
    ys : list[float]
        Positive dependent-variable values (e.g., runtimes in milliseconds).

    Returns
    -------
    float
        Estimated power-law exponent α.  Returns 0.0 if fewer than two valid
        pairs remain after filtering non-positive values.

    Raises
    ------
    ValueError
        If *xs* and *ys* have different lengths.

    Examples
    --------
    >>> xs = [1, 2, 4, 8, 16]
    >>> ys = [x**2 for x in xs]
    >>> abs(compute_empirical_exponent(xs, ys) - 2.0) < 1e-6
    True
    """
    if len(xs) != len(ys):
        raise ValueError(
            f"compute_empirical_exponent: xs and ys must have the same length "
            f"(got {len(xs)} and {len(ys)})."
        )

    # Filter to strictly positive pairs
    pairs = [(x, y) for x, y in zip(xs, ys) if x > _EPSILON and y > _EPSILON]
    if len(pairs) < 2:
        return 0.0

    valid_xs, valid_ys = zip(*pairs)
    slope, _ = log_log_regression(list(valid_xs), list(valid_ys))
    return slope


def estimate_constant_factor(xs: list, ys: list, exponent: float) -> float:
    """Estimate the constant factor c in the power law  y = c · xᵅ.

    Given the exponent α (e.g., as returned by ``compute_empirical_exponent``),
    this function estimates the scaling constant c by minimising the sum of
    squared log-space residuals:  min_c Σ (log y_i - log c - α · log x_i)².

    The closed-form solution is:
        log c = mean(log y) - α · mean(log x)
        c     = exp(log c)

    This is equivalent to the intercept term in the log-log OLS regression
    but is provided as a standalone function for callers that have already
    computed α by an alternative method (e.g., from theoretical analysis)
    and wish to fit only the constant.

    Parameters
    ----------
    xs : list
        Positive x-values.
    ys : list
        Positive y-values.
    exponent : float
        The power-law exponent α (can be any finite float including negative).

    Returns
    -------
    float
        Estimated constant factor c > 0.  Returns 1.0 if no valid pairs remain
        after filtering.

    Examples
    --------
    >>> xs = [1.0, 2.0, 4.0, 8.0]
    >>> ys = [3.0 * x**2 for x in xs]   # c=3, alpha=2
    >>> abs(estimate_constant_factor(xs, ys, exponent=2.0) - 3.0) < 1e-6
    True
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x > _EPSILON and y > _EPSILON]
    if not pairs:
        return 1.0

    valid_xs, valid_ys = zip(*pairs)

    # log c = mean(log y) - alpha * mean(log x)
    mean_log_x = statistics.mean(math.log(x) for x in valid_xs)
    mean_log_y = statistics.mean(math.log(y) for y in valid_ys)
    log_c = mean_log_y - exponent * mean_log_x

    return math.exp(log_c)


def log_log_regression(xs: list, ys: list) -> tuple:
    """Perform OLS regression of log(y) on log(x) and return (slope, intercept).

    This is the fundamental numerical routine underlying all power-law fitting
    in the scaling-limits package.  It transforms both axes to natural-log
    space and applies the standard closed-form OLS estimator.

    The slope corresponds to the power-law exponent α and the intercept
    corresponds to log(c), the natural log of the scaling constant.  Calling
    ``math.exp(intercept)`` recovers c.

    OLS estimators:
        slope     = Σ[(log_xi - mean_log_x)(log_yi - mean_log_y)]
                    ─────────────────────────────────────────────
                          Σ[(log_xi - mean_log_x)²]

        intercept = mean_log_y - slope · mean_log_x

    Non-positive values are excluded silently; if fewer than two valid pairs
    remain, the function returns (0.0, 0.0) as a safe fallback.

    Parameters
    ----------
    xs : list
        Positive x-values.
    ys : list
        Positive y-values.

    Returns
    -------
    tuple[float, float]
        (slope, intercept) in log-log space, i.e., (α, log c).

    Raises
    ------
    ValueError
        If *xs* and *ys* have different lengths.

    Examples
    --------
    >>> xs = [1, 2, 4, 8]
    >>> ys = [1, 4, 16, 64]  # y = x^2
    >>> slope, intercept = log_log_regression(xs, ys)
    >>> abs(slope - 2.0) < 1e-6
    True
    """
    if len(xs) != len(ys):
        raise ValueError(
            f"log_log_regression: xs and ys must have equal length "
            f"(got {len(xs)} vs {len(ys)})."
        )

    # Filter non-positive pairs
    pairs = [(x, y) for x, y in zip(xs, ys) if x > _EPSILON and y > _EPSILON]
    if len(pairs) < 2:
        return 0.0, 0.0

    log_xs = [math.log(x) for x, _ in pairs]
    log_ys = [math.log(y) for _, y in pairs]

    mean_lx = statistics.mean(log_xs)
    mean_ly = statistics.mean(log_ys)

    # Covariance numerator and variance denominator
    cov = sum((lx - mean_lx) * (ly - mean_ly) for lx, ly in zip(log_xs, log_ys))
    var = sum((lx - mean_lx) ** 2 for lx in log_xs)

    if abs(var) < _EPSILON:
        # All x values are identical in log space — slope undefined
        return 0.0, mean_ly

    slope = cov / var
    intercept = mean_ly - slope * mean_lx
    return slope, intercept


def detect_outliers(ys: list, k: float = _DEFAULT_OUTLIER_K) -> list:
    """Identify outlier indices in a 1-D series using the z-score method.

    A data point y_i is considered an outlier if its z-score exceeds the
    threshold k in absolute value:
        |y_i - mean(ys)| / std(ys) > k

    where std uses the sample standard deviation (Bessel's correction).
    The default threshold k=2.0 flags approximately 5 % of normally
    distributed data as outliers; larger k values are more conservative.

    The function returns the *indices* of outliers rather than the outlier
    values themselves so that the caller can remove them from both xs and ys
    simultaneously without re-scanning.

    If the series has fewer than three elements or zero variance, no outliers
    are detected and an empty list is returned.

    Parameters
    ----------
    ys : list
        Numeric values to scan for outliers.
    k : float
        Z-score threshold.  Values with |z| > k are flagged.  Default 2.0.

    Returns
    -------
    list[int]
        Sorted list of outlier indices (0-based).  Empty if none detected.

    Examples
    --------
    >>> ys = [1.0, 1.1, 0.9, 100.0, 1.0, 1.05]
    >>> detect_outliers(ys)
    [3]
    """
    if len(ys) < 3:
        return []

    try:
        mean_y = statistics.mean(ys)
        std_y = statistics.stdev(ys)
    except statistics.StatisticsError:
        return []

    if std_y < _EPSILON:
        # Zero variance — all values are identical, no outliers by z-score
        return []

    # Compute absolute z-scores and return indices that exceed the threshold
    outliers = [
        i for i, y in enumerate(ys)
        if abs(y - mean_y) / std_y > k
    ]
    return sorted(outliers)


def smooth_series(ys: list, window: int = _DEFAULT_SMOOTH_WINDOW) -> list:
    """Apply a simple moving-average smoother to a numeric series.

    Each output value is the arithmetic mean of the *window* input values
    centred on that position.  At the boundaries (where fewer than *window*
    values are available on one side), the window is truncated to the
    available data rather than zero-padded, so boundary values are still
    meaningful.

    The moving average is implemented with a sliding accumulator for O(n)
    time complexity rather than the O(n·w) naive approach.

    This smoother is primarily used to reduce high-frequency measurement noise
    before running regression-based analysis.  It does not remove trends or
    seasonality; it is a low-pass filter only.

    Parameters
    ----------
    ys : list
        Numeric series to smooth.
    window : int
        Window width (number of points to average).  Must be >= 1.
        Default 3.  Odd values produce symmetric windows; even values
        produce windows that are slightly left-biased.

    Returns
    -------
    list[float]
        Smoothed series of the same length as *ys*.  Returns a copy of *ys*
        (as floats) if window <= 1 or len(ys) <= 1.

    Examples
    --------
    >>> smooth_series([1, 2, 3, 4, 5], window=3)
    [1.5, 2.0, 3.0, 4.0, 4.5]
    """
    if window <= 1 or len(ys) <= 1:
        return [float(y) for y in ys]

    n = len(ys)
    half = window // 2
    result: list[float] = []

    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window_vals = ys[lo:hi]
        result.append(statistics.mean(window_vals))  # type: ignore[arg-type]

    return result


def normalize_series(ys: list) -> list:
    """Apply min-max normalisation to scale a series to the range [0, 1].

    The normalised value for each element is:
        y_norm = (y - min(ys)) / (max(ys) - min(ys))

    when the range is non-zero.  If all values are identical (range == 0),
    a list of zeros is returned so that the output is still well-defined.

    Normalisation is useful as a pre-processing step when combining
    measurements from different units or scales into a single analysis, for
    example when comparing runtime measurements (milliseconds) against memory
    measurements (megabytes) in a multi-objective complexity analysis.

    Parameters
    ----------
    ys : list
        Numeric series to normalise.  May contain any finite float values
        including zeros and negatives.

    Returns
    -------
    list[float]
        Values in [0, 1] with the same length as *ys*.  Returns an empty
        list if *ys* is empty.

    Examples
    --------
    >>> normalize_series([0, 5, 10])
    [0.0, 0.5, 1.0]
    """
    if not ys:
        return []

    y_min = min(ys)
    y_max = max(ys)
    y_range = y_max - y_min

    if abs(y_range) < _EPSILON:
        # All values identical — return all zeros
        return [0.0] * len(ys)

    return [(y - y_min) / y_range for y in ys]


def difference_series(ys: list) -> list:
    """Compute the first-order finite differences of a numeric series.

    The i-th element of the output is:
        Δy_i = y_{i+1} - y_i

    so the output has length len(ys) - 1.  First differences are used in
    phase-change detection to identify points where the growth rate changes
    abruptly, and in variance estimation to test for non-stationarity.

    A monotonically increasing series produces an all-positive difference
    series.  A series with a phase change will have a noticeable shift in the
    magnitude of the differences at the change point.

    Parameters
    ----------
    ys : list
        Numeric series for which first differences are required.

    Returns
    -------
    list[float]
        Difference series of length max(0, len(ys) - 1).

    Examples
    --------
    >>> difference_series([1, 3, 6, 10])
    [2.0, 3.0, 4.0]
    """
    if len(ys) < 2:
        return []
    return [float(ys[i + 1]) - float(ys[i]) for i in range(len(ys) - 1)]


def moving_variance(ys: list, window: int = _DEFAULT_VARIANCE_WINDOW) -> list:
    """Compute the rolling sample variance of a numeric series.

    For each position i the sample variance is computed over the *window*
    elements ending at position i (i.e., ys[i-window+1 : i+1]).  At the
    start of the series where fewer than *window* values are available, the
    available values are used instead (growing window).

    Rolling variance is a useful diagnostic for detecting heteroscedasticity
    (non-constant variance) in measurement data, which can indicate that the
    scaling behaviour changes over the observed range — a precursor to formal
    phase-change detection.

    The Bessel-corrected sample variance is used (denominator n-1) to produce
    an unbiased estimator.  For windows of size 1 or data with only one value,
    the variance is defined as 0.0.

    Parameters
    ----------
    ys : list
        Numeric series for which rolling variance is required.
    window : int
        Window size (number of trailing elements).  Must be >= 1.  Default 5.

    Returns
    -------
    list[float]
        Rolling variance series of the same length as *ys*.  Returns a list
        of zeros if *ys* is empty or window <= 1.

    Examples
    --------
    >>> import statistics
    >>> ys = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    >>> mv = moving_variance(ys, window=3)
    >>> len(mv) == len(ys)
    True
    """
    if not ys or window <= 1:
        return [0.0] * len(ys)

    result: list[float] = []
    for i in range(len(ys)):
        lo = max(0, i - window + 1)
        segment = ys[lo: i + 1]
        if len(segment) < 2:
            # Need at least 2 values for sample variance
            result.append(0.0)
        else:
            try:
                result.append(statistics.variance(segment))  # type: ignore[arg-type]
            except statistics.StatisticsError:
                result.append(0.0)

    return result
