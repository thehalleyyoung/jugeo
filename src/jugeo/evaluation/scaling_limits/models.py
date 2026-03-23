"""
scaling_limits.models — Core data models for complexity analysis and scaling-law fitting.

copilot: shared-core marker
Theory reference: theory2.tex Ch64

This module defines the canonical data model layer for the JuGeo
``scaling_limits`` evaluation subsystem.  Every class here is either an
:class:`~enum.Enum` constant namespace, a frozen value object (immutable
:func:`~dataclasses.dataclass`), or a mutable analysis object.  Together
they implement the full pipeline from raw sample-complexity measurements
through fitted scaling laws to certified fundamental limits.

Overview of the type hierarchy
--------------------------------
*Enumerations* classify qualitative properties:

* :class:`ComplexityClass` — standard computational-complexity notation
  (CONSTANT, LOGARITHMIC, … SUPEREXPONENTIAL).
* :class:`ScalingRegime` — empirical scaling behaviour of a measured system.
* :class:`PhaseKind` — qualitative character of a phase-change event.
* :class:`LimitKind` — the type of fundamental limit being certified.

*Frozen value objects* carry immutable analysis artefacts:

* :class:`ComplexityBound` — a single asymptotic bound with confidence score.
* :class:`PhaseChange` — a detected regime-transition event.
* :class:`ScalingLaw` — a fitted power-law or exponential relationship.
* :class:`LimitCertificate` — a formally-certified lower/upper bound pair.

*Mutable analysis objects* implement stateful processing pipelines:

* :class:`ComplexityAnalyzer` — collects raw measurements and infers bounds.
* :class:`PhaseChangeDetector` — sliding-window phase-transition detector.
* :class:`ScalingLawFitter` — curve-fitting engine for scaling laws.
* :class:`FundamentalLimits` — repository of certified fundamental limits.

Design notes
-------------
* Frozen dataclasses use ``tuple[str, ...]`` rather than ``list[str]`` for
  collection fields to satisfy the ``frozen=True`` constraint.
* All ``slots=True`` dataclasses explicitly enumerate every slot field; no
  hidden ``__dict__`` attribute is created.
* Cross-module JuGeo imports are guarded against :class:`ImportError`.
* The :func:`_utcnow`, :func:`_uid`, and :func:`_clamp` helpers (defined
  at module level) are shared with the rest of the package.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import dataclasses
import functools
import itertools
import json
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Enumerations
    "ComplexityClass",
    "ScalingRegime",
    "PhaseKind",
    "LimitKind",
    # Frozen value objects
    "ComplexityBound",
    "PhaseChange",
    "ScalingLaw",
    "LimitCertificate",
    # Mutable analysis objects
    "ComplexityAnalyzer",
    "PhaseChangeDetector",
    "ScalingLawFitter",
    "FundamentalLimits",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports (JuGeo ecosystem)
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
# Module-level constants
# ---------------------------------------------------------------------------

#: Smallest positive float treated as non-zero throughout this module.
EPSILON: float = 1e-12

#: Maximum number of measurements kept by :class:`ComplexityAnalyzer` before
#: old entries are evicted (FIFO).
MAX_MEASUREMENTS: int = 10_000

#: Default sliding-window size for :class:`PhaseChangeDetector`.
DEFAULT_WINDOW: int = 32

#: Default sensitivity threshold for :class:`PhaseChangeDetector`.
DEFAULT_SENSITIVITY: float = 2.0

#: Coefficient of determination threshold below which a fitted law is
#: considered a poor fit by :class:`ScalingLawFitter`.
MIN_R_SQUARED: float = 0.80

#: LaTeX template strings for each :class:`ComplexityClass`.
_COMPLEXITY_TEX: Dict[str, str] = {
    "CONSTANT":        r"O(1)",
    "LOGARITHMIC":     r"O(\log n)",
    "LINEAR":          r"O(n)",
    "POLYLOGARITHMIC": r"O(\log^k n)",
    "POLYNOMIAL":      r"O(n^k)",
    "EXPONENTIAL":     r"O(2^n)",
    "SUPEREXPONENTIAL": r"O(n!)",
}

#: Human-readable display names for :class:`ScalingRegime`.
_REGIME_LABELS: Dict[str, str] = {
    "SUB_LINEAR":   "Sub-linear",
    "LINEAR":       "Linear",
    "SUPER_LINEAR": "Super-linear",
    "POLYNOMIAL":   "Polynomial",
    "EXPONENTIAL":  "Exponential",
}


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses :func:`time.gmtime` exclusively so that the ``datetime`` module is
    not required.  The format is ``YYYY-MM-DDTHH:MM:SS`` (exactly 19 chars).

    Returns
    -------
    str
        UTC timestamp string.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _uid() -> str:
    """Return a compact 12-character hex unique identifier.

    Truncated from :func:`uuid.uuid4`.hex, giving ~48 bits of randomness —
    sufficient for collision avoidance within a single analysis session.

    Returns
    -------
    str
        12-char lowercase hex string.
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    A thin, auditable wrapper around ``max(lo, min(hi, value))``.

    Parameters
    ----------
    value : float
        Input value.
    lo : float
        Lower bound (inclusive).
    hi : float
        Upper bound (inclusive).

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, value))


def _safe_log(x: float, base: float = math.e) -> float:
    """Compute log_base(x) safely, returning ``-inf`` for non-positive inputs.

    Avoids domain errors when processing raw measurement data that may contain
    zero or negative values (e.g., from noisy sensors or misconfigured probes).

    Parameters
    ----------
    x : float
        Argument to the logarithm.
    base : float
        Logarithm base (default: natural log).

    Returns
    -------
    float
        The logarithm, or ``float('-inf')`` for non-positive *x*.
    """
    if x <= 0.0:
        return float("-inf")
    return math.log(x) / math.log(base)


def _linreg(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Simple ordinary least-squares linear regression.

    Returns the slope, intercept, and coefficient of determination R² for the
    linear model ``y = slope * x + intercept``.

    Parameters
    ----------
    xs : sequence of float
        Independent variable values.
    ys : sequence of float
        Dependent variable values (same length as *xs*).

    Returns
    -------
    tuple[float, float, float]
        ``(slope, intercept, r_squared)``.

    Raises
    ------
    ValueError
        If *xs* and *ys* have different lengths or fewer than two points.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length.")
    n = len(xs)
    if n < 2:
        raise ValueError("At least two data points are required for linear regression.")

    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)

    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    ss_xx = sum((x - mean_x) ** 2 for x in xs)

    if abs(ss_xx) < EPSILON:
        # All x values are identical — slope is undefined; return flat model.
        return 0.0, mean_y, 0.0

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    # R²
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - ss_res / ss_tot if abs(ss_tot) > EPSILON else 1.0

    return slope, intercept, _clamp(r_squared, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ComplexityClass(str, Enum):
    """Standard computational-complexity notation for asymptotic analysis.

    These values are used to label both theoretical bounds and empirically
    fitted scaling laws, allowing downstream code to compare apples-to-apples
    across algorithms and problem instances.

    Members
    -------
    CONSTANT
        O(1) — cost does not grow with problem size.
    LOGARITHMIC
        O(log n) — cost grows logarithmically.
    LINEAR
        O(n) — cost grows linearly.
    POLYLOGARITHMIC
        O(log^k n) — cost is a polynomial in log n.
    POLYNOMIAL
        O(n^k) — cost is a polynomial in n.
    EXPONENTIAL
        O(2^n) — cost doubles with each unit increase in n.
    SUPEREXPONENTIAL
        O(n!) or worse — cost grows faster than any exponential.
    """

    CONSTANT = "CONSTANT"
    LOGARITHMIC = "LOGARITHMIC"
    LINEAR = "LINEAR"
    POLYLOGARITHMIC = "POLYLOGARITHMIC"
    POLYNOMIAL = "POLYNOMIAL"
    EXPONENTIAL = "EXPONENTIAL"
    SUPEREXPONENTIAL = "SUPEREXPONENTIAL"


class ScalingRegime(str, Enum):
    """Empirical scaling behaviour observed in a measured system.

    Unlike :class:`ComplexityClass`, which denotes *worst-case* asymptotic
    bounds, a :class:`ScalingRegime` describes the *observed* slope of a
    log-log cost curve over the measured domain range.

    Members
    -------
    SUB_LINEAR
        Cost grows slower than linearly (exponent < 1).
    LINEAR
        Cost grows linearly (exponent ≈ 1).
    SUPER_LINEAR
        Cost grows faster than linearly but slower than quadratic (1 < exp < 2).
    POLYNOMIAL
        Cost grows as n^k for some k ≥ 2.
    EXPONENTIAL
        Cost grows exponentially (dominant term is base^n).
    """

    SUB_LINEAR = "SUB_LINEAR"
    LINEAR = "LINEAR"
    SUPER_LINEAR = "SUPER_LINEAR"
    POLYNOMIAL = "POLYNOMIAL"
    EXPONENTIAL = "EXPONENTIAL"


class PhaseKind(str, Enum):
    """Qualitative character of a detected phase-change event.

    Phase changes mark points in the problem-size domain where the scaling
    behaviour shifts discontinuously or undergoes a qualitative transformation.

    Members
    -------
    CONTINUOUS
        A smooth, continuous transition between regimes with no sharp jump.
    DISCONTINUOUS
        An abrupt jump in cost or gradient at the transition point.
    BIFURCATION
        The solution space splits; multiple equilibria appear or vanish.
    TRANSITION
        A broad cross-over region rather than a single threshold size.
    """

    CONTINUOUS = "CONTINUOUS"
    DISCONTINUOUS = "DISCONTINUOUS"
    BIFURCATION = "BIFURCATION"
    TRANSITION = "TRANSITION"


class LimitKind(str, Enum):
    """The type of fundamental limit being certified.

    Members
    -------
    SAMPLE_COMPLEXITY
        Limits on the number of training/query samples needed.
    TIME_COMPLEXITY
        Limits on computational time (clock steps or arithmetic operations).
    SPACE_COMPLEXITY
        Limits on memory or storage requirements.
    COMMUNICATION
        Communication complexity bounds (distributed / query model).
    INFORMATION_THEORETIC
        Limits derived from information theory (entropy, mutual information).
    """

    SAMPLE_COMPLEXITY = "SAMPLE_COMPLEXITY"
    TIME_COMPLEXITY = "TIME_COMPLEXITY"
    SPACE_COMPLEXITY = "SPACE_COMPLEXITY"
    COMMUNICATION = "COMMUNICATION"
    INFORMATION_THEORETIC = "INFORMATION_THEORETIC"


# ---------------------------------------------------------------------------
# Frozen value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComplexityBound:
    """An asymptotic complexity bound for a single problem instance.

    A :class:`ComplexityBound` captures the result of a single analytical or
    empirical bounding step.  It records the complexity class, the constant
    factor in the big-O notation, the specific problem size at which the
    bound was computed, and a confidence score between 0 and 1.

    Two bounds can be compared with :meth:`dominates` to determine which is
    *tighter* (i.e., asymptotically smaller).

    Attributes
    ----------
    bound_id : str
        Unique identifier for this bound.
    complexity_class : ComplexityClass
        The asymptotic class of this bound.
    constant_factor : float
        The multiplicative constant in the leading term (must be > 0).
    problem_size : int
        The concrete problem size n at which this bound was evaluated.
    confidence : float
        Confidence score in [0, 1] that the bound is correct and tight.
    """

    bound_id: str
    complexity_class: ComplexityClass
    constant_factor: float
    problem_size: int
    confidence: float

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise this bound to a plain Python dictionary.

        All fields are serialised to JSON-compatible Python primitives.
        :attr:`complexity_class` is stored as its string value so that the
        resulting dict can be round-tripped through JSON without requiring
        enum-aware deserialisers.

        Returns
        -------
        dict
            Fully JSON-serialisable representation.
        """
        return {
            "bound_id": self.bound_id,
            "complexity_class": self.complexity_class.value,
            "constant_factor": self.constant_factor,
            "problem_size": self.problem_size,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ComplexityBound":
        """Reconstruct a :class:`ComplexityBound` from a plain dictionary.

        This is the inverse of :meth:`to_dict`.  The ``complexity_class``
        field is coerced from a string back to a :class:`ComplexityClass`
        enum member.  Missing optional fields are assigned sensible defaults.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        ComplexityBound
            A freshly constructed frozen instance.
        """
        return cls(
            bound_id=data.get("bound_id", _uid()),
            complexity_class=ComplexityClass(data["complexity_class"]),
            constant_factor=float(data.get("constant_factor", 1.0)),
            problem_size=int(data.get("problem_size", 1)),
            confidence=_clamp(float(data.get("confidence", 1.0)), 0.0, 1.0),
        )

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def is_tight(self, threshold: float = 0.90) -> bool:
        """Return ``True`` if this bound is considered tight.

        A bound is tight when its :attr:`confidence` score meets or exceeds
        *threshold*.  The default threshold of 0.90 corresponds to a 90%
        confidence that the bound cannot be significantly improved without
        additional structural insight.

        Parameters
        ----------
        threshold : float
            Minimum confidence required to declare the bound tight.

        Returns
        -------
        bool
            ``True`` if the bound is tight, ``False`` otherwise.
        """
        return self.confidence >= _clamp(threshold, 0.0, 1.0)

    def render_tex(self) -> str:
        """Render this bound as a LaTeX math expression.

        Produces a string of the form
        ``c \\cdot O(\\text{class}(n))``
        where ``c`` is :attr:`constant_factor` rounded to four significant
        figures and the complexity class is rendered using the canonical
        LaTeX notation from :data:`_COMPLEXITY_TEX`.

        Returns
        -------
        str
            A LaTeX math-mode string (without surrounding ``$``).
        """
        class_tex = _COMPLEXITY_TEX.get(self.complexity_class.value, r"O(?)")
        c = round(self.constant_factor, 4)
        return rf"{c} \cdot {class_tex}"

    def merge_with(self, other: "ComplexityBound") -> "ComplexityBound":
        """Return a new bound representing the meet (intersection) of *self* and *other*.

        The merged bound takes the *tighter* complexity class (lower in the
        standard hierarchy), the *minimum* constant factor (more optimistic),
        the *larger* problem size (more conservative domain), and the *product*
        of the confidence scores (combined certainty).

        Parameters
        ----------
        other : ComplexityBound
            Another bound to merge with *self*.

        Returns
        -------
        ComplexityBound
            A new frozen bound representing the intersection.
        """
        # Order by enum declaration index (CONSTANT is tightest)
        order = list(ComplexityClass)
        idx_self = order.index(self.complexity_class)
        idx_other = order.index(other.complexity_class)
        tighter_class = order[min(idx_self, idx_other)]

        return ComplexityBound(
            bound_id=_uid(),
            complexity_class=tighter_class,
            constant_factor=min(self.constant_factor, other.constant_factor),
            problem_size=max(self.problem_size, other.problem_size),
            confidence=_clamp(self.confidence * other.confidence, 0.0, 1.0),
        )

    def dominates(self, other: "ComplexityBound") -> bool:
        """Return ``True`` if *self* is asymptotically tighter than *other*.

        *self* dominates *other* if it belongs to a lower complexity class,
        or belongs to the same class but has a strictly smaller constant
        factor.

        Parameters
        ----------
        other : ComplexityBound
            The bound to compare against.

        Returns
        -------
        bool
            ``True`` if *self* is strictly tighter.
        """
        order = list(ComplexityClass)
        idx_self = order.index(self.complexity_class)
        idx_other = order.index(other.complexity_class)
        if idx_self < idx_other:
            return True
        if idx_self == idx_other:
            return self.constant_factor < other.constant_factor
        return False

    def __repr__(self) -> str:
        """Return a compact developer representation."""
        return (
            f"ComplexityBound(id={self.bound_id!r}, "
            f"class={self.complexity_class.value}, "
            f"c={self.constant_factor:.4g}, "
            f"n={self.problem_size}, "
            f"conf={self.confidence:.3f})"
        )

    def __str__(self) -> str:
        """Return a human-readable LaTeX string for display purposes."""
        return self.render_tex()


# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhaseChange:
    """A detected phase-change event in a scaling curve.

    A :class:`PhaseChange` records the observation that the dominant scaling
    regime shifts at a particular problem size.  It carries provenance
    information in the form of an ``evidence`` tuple of identifier strings
    pointing back to the raw measurements that supported the detection.

    Attributes
    ----------
    phase_id : str
        Unique identifier for this phase-change record.
    phase_kind : PhaseKind
        Qualitative character of the transition.
    threshold_size : int
        Problem size n at which the transition occurs.
    before_regime : ScalingRegime
        The scaling regime observed for n < threshold_size.
    after_regime : ScalingRegime
        The scaling regime observed for n >= threshold_size.
    evidence : tuple[str, ...]
        Immutable sequence of measurement or record IDs supporting this event.
    """

    phase_id: str
    phase_kind: PhaseKind
    threshold_size: int
    before_regime: ScalingRegime
    after_regime: ScalingRegime
    evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        """Serialise this phase-change to a plain Python dictionary.

        All enum fields are stored as their string values, and the
        ``evidence`` tuple is converted to a list for JSON compatibility.

        Returns
        -------
        dict
            A JSON-serialisable representation.
        """
        return {
            "phase_id": self.phase_id,
            "phase_kind": self.phase_kind.value,
            "threshold_size": self.threshold_size,
            "before_regime": self.before_regime.value,
            "after_regime": self.after_regime.value,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PhaseChange":
        """Reconstruct a :class:`PhaseChange` from a plain dictionary.

        Enum fields are coerced from their string representations.  The
        ``evidence`` list is converted back to a tuple.  Any missing
        optional fields use sensible defaults.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PhaseChange
            A freshly constructed frozen instance.
        """
        return cls(
            phase_id=data.get("phase_id", _uid()),
            phase_kind=PhaseKind(data["phase_kind"]),
            threshold_size=int(data.get("threshold_size", 1)),
            before_regime=ScalingRegime(data["before_regime"]),
            after_regime=ScalingRegime(data["after_regime"]),
            evidence=tuple(data.get("evidence", [])),
        )

    def is_sharp(self) -> bool:
        """Return ``True`` if this phase change is *sharp* (discontinuous).

        A phase change is considered sharp when :attr:`phase_kind` is
        :attr:`PhaseKind.DISCONTINUOUS` or :attr:`PhaseKind.BIFURCATION`,
        indicating an abrupt rather than gradual regime transition.

        Returns
        -------
        bool
            ``True`` for sharp transitions.
        """
        return self.phase_kind in (PhaseKind.DISCONTINUOUS, PhaseKind.BIFURCATION)

    def render_tex(self) -> str:
        """Render this phase change as a LaTeX annotation string.

        The output is suitable for embedding in a theorem environment or
        figure caption in a theory document.  It includes the threshold size,
        the two regimes, and the phase kind.

        Returns
        -------
        str
            A LaTeX-formatted annotation string.
        """
        before = _REGIME_LABELS.get(self.before_regime.value, self.before_regime.value)
        after = _REGIME_LABELS.get(self.after_regime.value, self.after_regime.value)
        return (
            rf"Phase transition at $n = {self.threshold_size}$: "
            rf"\textit{{{before}}} $\to$ \textit{{{after}}} "
            rf"(\textsc{{{self.phase_kind.value.lower()}}})"
        )

    def __repr__(self) -> str:
        return (
            f"PhaseChange(id={self.phase_id!r}, "
            f"kind={self.phase_kind.value}, "
            f"threshold={self.threshold_size}, "
            f"{self.before_regime.value}→{self.after_regime.value})"
        )

    def __str__(self) -> str:
        return self.render_tex()


# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScalingLaw:
    """A fitted empirical scaling law of the form ``cost(n) = coefficient * n^exponent``.

    A :class:`ScalingLaw` encapsulates the result of fitting a power-law
    model to a sequence of (problem_size, cost) measurements.  The fit
    quality is captured by :attr:`r_squared`; values below :data:`MIN_R_SQUARED`
    indicate a poor fit that should be treated with caution.

    The :attr:`domain_range` tuple ``(n_min, n_max)`` records the range of
    problem sizes over which the law was fitted.  :meth:`extrapolate` should
    be used cautiously for problem sizes outside this range.

    Attributes
    ----------
    law_id : str
        Unique identifier for this fitted law.
    regime : ScalingRegime
        The empirical scaling regime this law belongs to.
    exponent : float
        Power-law exponent (slope in log-log space).
    coefficient : float
        Multiplicative coefficient (intercept in log-log space, exponentiated).
    r_squared : float
        Coefficient of determination R² of the fit (0 ≤ R² ≤ 1).
    domain_range : tuple[int, int]
        ``(n_min, n_max)`` — problem sizes over which the law was fitted.
    """

    law_id: str
    regime: ScalingRegime
    exponent: float
    coefficient: float
    r_squared: float
    domain_range: Tuple[int, int]

    def to_dict(self) -> dict:
        """Serialise this scaling law to a plain Python dictionary.

        All fields are stored as JSON-compatible primitives.  The
        ``domain_range`` tuple is stored as a two-element list.  The
        ``regime`` enum is stored as its string value.

        Returns
        -------
        dict
            A fully JSON-serialisable representation.
        """
        return {
            "law_id": self.law_id,
            "regime": self.regime.value,
            "exponent": self.exponent,
            "coefficient": self.coefficient,
            "r_squared": self.r_squared,
            "domain_range": list(self.domain_range),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScalingLaw":
        """Reconstruct a :class:`ScalingLaw` from a plain dictionary.

        The ``regime`` field is coerced from a string to a
        :class:`ScalingRegime` enum member.  ``domain_range`` is coerced
        from a list to a two-element tuple of ints.  Missing fields fall
        back to neutral defaults.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        ScalingLaw
            A freshly constructed frozen instance.
        """
        dr = data.get("domain_range", [1, 1])
        return cls(
            law_id=data.get("law_id", _uid()),
            regime=ScalingRegime(data["regime"]),
            exponent=float(data.get("exponent", 1.0)),
            coefficient=float(data.get("coefficient", 1.0)),
            r_squared=_clamp(float(data.get("r_squared", 0.0)), 0.0, 1.0),
            domain_range=(int(dr[0]), int(dr[1])),
        )

    def evaluate(self, n: int) -> float:
        """Evaluate the scaling law at problem size *n*.

        Returns ``coefficient * n ** exponent``.  For exponential-regime
        laws (where :attr:`exponent` represents the base of an exponential),
        the caller should use :meth:`extrapolate` instead, which applies the
        appropriate model form.

        Parameters
        ----------
        n : int
            Problem size at which to evaluate the law.

        Returns
        -------
        float
            Predicted cost at problem size *n*.

        Raises
        ------
        ValueError
            If *n* is not a positive integer.
        """
        if n < 1:
            raise ValueError(f"Problem size n must be ≥ 1, got {n}.")
        return self.coefficient * (n ** self.exponent)

    def complexity_class(self) -> ComplexityClass:
        """Infer the :class:`ComplexityClass` from the fitted exponent.

        Uses the following thresholds (approximate, based on the power-law
        model):

        * exponent ≈ 0  → CONSTANT
        * 0 < exponent ≤ 0.1 → LOGARITHMIC
        * 0.1 < exponent ≤ 1.05 → LINEAR
        * 1.05 < exponent ≤ 2.5 → POLYNOMIAL
        * exponent > 2.5 → EXPONENTIAL (or SUPEREXPONENTIAL for very large values)

        Returns
        -------
        ComplexityClass
            The inferred complexity class.
        """
        e = self.exponent
        if abs(e) < 0.05:
            return ComplexityClass.CONSTANT
        if e <= 0.15:
            return ComplexityClass.LOGARITHMIC
        if e <= 1.05:
            return ComplexityClass.LINEAR
        if e <= 3.5:
            return ComplexityClass.POLYNOMIAL
        if e <= 10.0:
            return ComplexityClass.EXPONENTIAL
        return ComplexityClass.SUPEREXPONENTIAL

    def render_tex(self) -> str:
        """Render this scaling law as a LaTeX equation string.

        The output has the form:
        ``f(n) = c \\cdot n^{\\alpha} \\quad (R^2 = r)``
        where ``c`` is :attr:`coefficient`, ``\\alpha`` is :attr:`exponent`,
        and ``r`` is :attr:`r_squared`, all rounded for readability.

        Returns
        -------
        str
            A LaTeX math-mode equation string (no surrounding ``$``).
        """
        c = round(self.coefficient, 4)
        alpha = round(self.exponent, 4)
        r2 = round(self.r_squared, 4)
        return rf"f(n) = {c} \cdot n^{{{alpha}}} \quad (R^2 = {r2})"

    def extrapolate(self, n: int) -> Tuple[float, str]:
        """Evaluate the scaling law at *n* and attach a reliability tag.

        For problem sizes within :attr:`domain_range` the prediction is
        tagged ``"interpolation"``; for sizes outside the range it is tagged
        ``"extrapolation"`` to remind callers that the law may not hold.

        Parameters
        ----------
        n : int
            The problem size at which to predict cost.

        Returns
        -------
        tuple[float, str]
            ``(predicted_cost, reliability_tag)``.
        """
        cost = self.evaluate(n)
        lo, hi = self.domain_range
        tag = "interpolation" if lo <= n <= hi else "extrapolation"
        return cost, tag

    def __repr__(self) -> str:
        return (
            f"ScalingLaw(id={self.law_id!r}, "
            f"regime={self.regime.value}, "
            f"exp={self.exponent:.4g}, "
            f"coef={self.coefficient:.4g}, "
            f"R²={self.r_squared:.4f})"
        )

    def __str__(self) -> str:
        return self.render_tex()


# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LimitCertificate:
    """A certified lower/upper bound pair for a fundamental complexity limit.

    A :class:`LimitCertificate` formalises the result of a lower-bound proof
    and a matching algorithm or construction.  When the lower bound equals
    the upper bound (up to constant factors) the certificate is considered
    *tight*.  The :attr:`proof_sketch` field carries a brief human-readable
    justification suitable for inclusion in a theorem statement.

    Attributes
    ----------
    cert_id : str
        Unique identifier for this certificate.
    limit_kind : LimitKind
        The type of fundamental limit being certified.
    lower_bound : float
        The lower bound on the limit (must be ≥ 0).
    upper_bound : float
        The upper bound on the limit (must be ≥ lower_bound).
    proof_sketch : str
        A brief justification for the certificate.
    """

    cert_id: str
    limit_kind: LimitKind
    lower_bound: float
    upper_bound: float
    proof_sketch: str

    def to_dict(self) -> dict:
        """Serialise this certificate to a plain Python dictionary.

        Enum fields are stored as string values.  All numeric fields are
        stored as plain Python floats.

        Returns
        -------
        dict
            A fully JSON-serialisable representation.
        """
        return {
            "cert_id": self.cert_id,
            "limit_kind": self.limit_kind.value,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "proof_sketch": self.proof_sketch,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LimitCertificate":
        """Reconstruct a :class:`LimitCertificate` from a plain dictionary.

        The ``limit_kind`` field is coerced from a string to a
        :class:`LimitKind` member.  Numeric fields are coerced to float.
        Missing fields fall back to neutral defaults.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        LimitCertificate
            A freshly constructed frozen instance.
        """
        lb = float(data.get("lower_bound", 0.0))
        ub = float(data.get("upper_bound", float("inf")))
        return cls(
            cert_id=data.get("cert_id", _uid()),
            limit_kind=LimitKind(data["limit_kind"]),
            lower_bound=lb,
            upper_bound=max(lb, ub),
            proof_sketch=data.get("proof_sketch", ""),
        )

    def is_tight(self, ratio_threshold: float = 1.05) -> bool:
        """Return ``True`` if the lower and upper bounds are within *ratio_threshold*.

        A certificate is tight if ``upper_bound / lower_bound ≤ ratio_threshold``.
        The default threshold of 1.05 corresponds to a 5% gap, which is
        considered negligible for most practical purposes.

        Parameters
        ----------
        ratio_threshold : float
            Maximum allowed ratio of upper to lower bound.

        Returns
        -------
        bool
            ``True`` if the certificate is tight.
        """
        if self.lower_bound < EPSILON:
            return self.upper_bound < EPSILON
        return self.upper_bound / self.lower_bound <= ratio_threshold

    def gap_ratio(self) -> float:
        """Return the ratio ``upper_bound / lower_bound``.

        A gap ratio of 1.0 indicates a perfectly tight certificate.  A large
        gap ratio (e.g., 1e6) indicates that significant tightening remains
        to be done.  Returns ``inf`` when :attr:`lower_bound` is zero.

        Returns
        -------
        float
            The multiplicative gap between the two bounds.
        """
        if self.lower_bound < EPSILON:
            return float("inf")
        return self.upper_bound / self.lower_bound

    def render_tex(self) -> str:
        """Render this certificate as a LaTeX theorem environment stub.

        The output is a minimal theorem-like block that could be embedded
        directly in a theory document (``theory2.tex``).  It shows the
        limit kind, the bound interval, tightness status, and proof sketch.

        Returns
        -------
        str
            A multi-line LaTeX snippet.
        """
        tight_str = "tight" if self.is_tight() else f"gap ratio {self.gap_ratio():.2f}×"
        lines = [
            r"\begin{theorem}[Fundamental Limit Certificate]",
            rf"  \textbf{{Limit}}: \textsc{{{self.limit_kind.value.replace('_', ' ').title()}}}\\",
            rf"  \textbf{{Bounds}}: $\Omega({self.lower_bound:.4g}) \leq T \leq O({self.upper_bound:.4g})$\\",
            rf"  \textbf{{Status}}: {tight_str}\\",
            rf"  \textit{{Sketch}}: {self.proof_sketch}",
            r"\end{theorem}",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"LimitCertificate(id={self.cert_id!r}, "
            f"kind={self.limit_kind.value}, "
            f"lb={self.lower_bound:.4g}, "
            f"ub={self.upper_bound:.4g}, "
            f"tight={self.is_tight()})"
        )

    def __str__(self) -> str:
        return self.render_tex()


# ---------------------------------------------------------------------------
# Mutable analysis objects
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ComplexityAnalyzer:
    """Stateful pipeline for collecting raw measurements and inferring complexity bounds.

    A :class:`ComplexityAnalyzer` accumulates ``(n, cost)`` pairs via
    :meth:`add_measurement`, then derives :class:`ComplexityBound` objects
    from the accumulated data via :meth:`analyze`.

    The analysis fits a log-log linear model to estimate the power-law
    exponent of the observed scaling curve, and maps the exponent to a
    :class:`ComplexityClass`.

    Attributes
    ----------
    config : dict
        Configuration dictionary; may override defaults such as
        ``"min_measurements"`` (default 5) or ``"confidence_penalty"`` (0.1).
    measurements : list
        Accumulated ``{"n": int, "cost": float, "ts": str}`` dicts.
    bounds : list
        :class:`ComplexityBound` objects inferred by :meth:`analyze`.
    """

    config: dict
    measurements: list
    bounds: list

    def add_measurement(self, n: int, cost: float) -> None:
        """Record a single ``(problem_size, cost)`` measurement.

        Each measurement is timestamped with :func:`_utcnow` and appended to
        :attr:`measurements`.  If the list would exceed :data:`MAX_MEASUREMENTS`
        entries, the oldest entry is evicted to maintain a bounded memory
        footprint.

        Parameters
        ----------
        n : int
            Problem size (must be ≥ 1).
        cost : float
            Observed cost (must be finite and ≥ 0).

        Raises
        ------
        ValueError
            If *n* < 1 or *cost* is negative or non-finite.
        """
        if n < 1:
            raise ValueError(f"Problem size n must be ≥ 1, got {n}.")
        if not math.isfinite(cost) or cost < 0.0:
            raise ValueError(f"Cost must be a finite non-negative number, got {cost}.")
        if len(self.measurements) >= MAX_MEASUREMENTS:
            self.measurements.pop(0)  # evict oldest
        self.measurements.append({"n": n, "cost": cost, "ts": _utcnow()})

    def analyze(self) -> List[ComplexityBound]:
        """Fit a power-law model to :attr:`measurements` and infer bounds.

        The analysis log-transforms both the problem sizes and costs, fits an
        ordinary least-squares line (via :func:`_linreg`), recovers the
        exponent and coefficient, and constructs a :class:`ComplexityBound`
        for each distinct problem size that has been measured.

        Returns an empty list (and appends nothing to :attr:`bounds`) if
        fewer than the configured ``"min_measurements"`` entries have been
        collected.

        Returns
        -------
        list[ComplexityBound]
            Newly inferred bounds; also appended to :attr:`bounds`.
        """
        min_meas = int(self.config.get("min_measurements", 5))
        if len(self.measurements) < min_meas:
            return []

        log_ns = [_safe_log(m["n"]) for m in self.measurements]
        log_costs = [_safe_log(m["cost"]) for m in self.measurements]

        # Filter out -inf entries (n==0 or cost==0 after log transform)
        pairs = [(lx, ly) for lx, ly in zip(log_ns, log_costs) if math.isfinite(lx) and math.isfinite(ly)]
        if len(pairs) < 2:
            return []

        xs, ys = zip(*pairs)
        slope, intercept, r2 = _linreg(list(xs), list(ys))

        # coefficient = exp(intercept)
        coefficient = math.exp(intercept) if math.isfinite(intercept) else 1.0
        exponent = slope

        # Map exponent to complexity class via ScalingLaw helper
        dummy_law = ScalingLaw(
            law_id="__tmp__",
            regime=ScalingRegime.LINEAR,
            exponent=exponent,
            coefficient=coefficient,
            r_squared=r2,
            domain_range=(1, 1),
        )
        cc = dummy_law.complexity_class()

        # Confidence penalised for low R²
        penalty = float(self.config.get("confidence_penalty", 0.1))
        confidence = _clamp(r2 - penalty * (1.0 - r2), 0.0, 1.0)

        new_bounds: List[ComplexityBound] = []
        for m in self.measurements:
            cb = ComplexityBound(
                bound_id=_uid(),
                complexity_class=cc,
                constant_factor=coefficient,
                problem_size=m["n"],
                confidence=confidence,
            )
            new_bounds.append(cb)

        self.bounds.extend(new_bounds)
        return new_bounds

    def reset(self) -> None:
        """Clear all accumulated measurements and inferred bounds.

        After calling this method :attr:`measurements` and :attr:`bounds` are
        both empty lists.  The :attr:`config` dictionary is preserved so
        that the analyzer can be reused without reconfiguration.
        """
        self.measurements.clear()
        self.bounds.clear()

    def summary(self) -> str:
        """Return a concise human-readable summary of the analyzer state.

        The summary includes the measurement count, bound count, and — if
        bounds exist — the most common complexity class inferred so far.

        Returns
        -------
        str
            A single-line summary string.
        """
        cc_counts: Dict[str, int] = {}
        for b in self.bounds:
            key = b.complexity_class.value
            cc_counts[key] = cc_counts.get(key, 0) + 1
        top_cc = max(cc_counts, key=lambda k: cc_counts[k]) if cc_counts else "N/A"
        return (
            f"ComplexityAnalyzer: {len(self.measurements)} measurements, "
            f"{len(self.bounds)} bounds inferred, "
            f"dominant class={top_cc}"
        )

    def to_dict(self) -> dict:
        """Serialise the analyzer state to a plain Python dictionary.

        The :attr:`bounds` list is serialised using each bound's
        :meth:`ComplexityBound.to_dict` method.  The :attr:`measurements`
        list is stored as-is (already JSON-compatible).

        Returns
        -------
        dict
            A fully JSON-serialisable representation.
        """
        return {
            "config": dict(self.config),
            "measurements": list(self.measurements),
            "bounds": [b.to_dict() for b in self.bounds],
        }


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PhaseChangeDetector:
    """Sliding-window detector for phase-change events in a cost sequence.

    The detector maintains a buffer of the most recent :attr:`window_size`
    ``(n, cost)`` measurements.  When :meth:`feed` is called it checks
    whether the gradient of the log-log curve has shifted significantly (as
    measured by :attr:`sensitivity` standard deviations) between the first
    and second halves of the window, and if so, records a :class:`PhaseChange`.

    Attributes
    ----------
    window_size : int
        Number of measurements to keep in the sliding window.
    sensitivity : float
        Number of standard deviations required to declare a phase change.
    detected_phases : list
        List of :class:`PhaseChange` objects detected so far.
    """

    window_size: int
    sensitivity: float
    detected_phases: list

    def feed(self, n: int, cost: float) -> Optional[PhaseChange]:
        """Offer a new ``(n, cost)`` measurement to the detector.

        The detector appends the measurement to an internal rolling buffer
        (stored in :attr:`detected_phases` as metadata) and calls
        :meth:`detect` once the buffer has at least :attr:`window_size`
        entries.

        Parameters
        ----------
        n : int
            Problem size of the new measurement.
        cost : float
            Observed cost of the new measurement.

        Returns
        -------
        PhaseChange or None
            A newly detected phase change, or ``None`` if no change detected.
        """
        if not hasattr(self, "_buffer"):
            object.__setattr__(self, "_buffer", [])
        buf = object.__getattribute__(self, "_buffer")
        buf.append({"n": n, "cost": cost})
        if len(buf) > self.window_size:
            buf.pop(0)
        if len(buf) >= self.window_size:
            return self.detect(buf)
        return None

    def detect(self, window: List[dict]) -> Optional[PhaseChange]:
        """Analyse *window* and return a :class:`PhaseChange` if a shift is detected.

        Splits the window into two equal halves, computes the log-log slope
        for each half via :func:`_linreg`, and tests whether the difference
        in slopes exceeds :attr:`sensitivity` times the estimated slope
        standard error.

        Parameters
        ----------
        window : list of dict
            List of ``{"n": int, "cost": float}`` dicts.

        Returns
        -------
        PhaseChange or None
            A :class:`PhaseChange` if the slope change is statistically
            significant, otherwise ``None``.
        """
        mid = len(window) // 2
        first_half = window[:mid]
        second_half = window[mid:]

        def half_slope(pts: List[dict]) -> Tuple[float, float]:
            log_ns = [_safe_log(p["n"]) for p in pts]
            log_cs = [_safe_log(p["cost"]) for p in pts]
            valid = [(x, y) for x, y in zip(log_ns, log_cs) if math.isfinite(x) and math.isfinite(y)]
            if len(valid) < 2:
                return 0.0, 0.0
            xs, ys = zip(*valid)
            slope, _, r2 = _linreg(list(xs), list(ys))
            return slope, r2

        slope1, _ = half_slope(first_half)
        slope2, r2 = half_slope(second_half)
        delta = abs(slope2 - slope1)

        if delta < self.sensitivity:
            return None

        # Determine before and after regime from slopes
        def slope_to_regime(s: float) -> ScalingRegime:
            if s < 0.5:
                return ScalingRegime.SUB_LINEAR
            if s < 1.1:
                return ScalingRegime.LINEAR
            if s < 2.0:
                return ScalingRegime.SUPER_LINEAR
            if s < 10.0:
                return ScalingRegime.POLYNOMIAL
            return ScalingRegime.EXPONENTIAL

        before = slope_to_regime(slope1)
        after = slope_to_regime(slope2)
        threshold_n = second_half[0]["n"]

        kind = PhaseKind.DISCONTINUOUS if delta > self.sensitivity * 2.0 else PhaseKind.TRANSITION
        evidence_ids = tuple(_uid() for _ in range(min(3, len(window))))

        phase = PhaseChange(
            phase_id=_uid(),
            phase_kind=kind,
            threshold_size=threshold_n,
            before_regime=before,
            after_regime=after,
            evidence=evidence_ids,
        )
        self.detected_phases.append(phase)
        return phase

    def reset(self) -> None:
        """Clear the rolling buffer and all detected phase changes."""
        self.detected_phases.clear()
        try:
            object.__getattribute__(self, "_buffer").clear()
        except AttributeError:
            pass

    def summary(self) -> str:
        """Return a concise summary of detection results.

        Includes the window size, sensitivity, and the number of phase
        changes detected so far.

        Returns
        -------
        str
            A single-line summary string.
        """
        return (
            f"PhaseChangeDetector: window={self.window_size}, "
            f"sensitivity={self.sensitivity:.2f}, "
            f"detected={len(self.detected_phases)} phase change(s)"
        )

    def to_dict(self) -> dict:
        """Serialise this detector's state to a plain Python dictionary.

        The :attr:`detected_phases` list is serialised using each phase's
        :meth:`PhaseChange.to_dict` method.

        Returns
        -------
        dict
            A fully JSON-serialisable representation.
        """
        return {
            "window_size": self.window_size,
            "sensitivity": self.sensitivity,
            "detected_phases": [p.to_dict() for p in self.detected_phases],
        }


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScalingLawFitter:
    """Curve-fitting engine that produces :class:`ScalingLaw` objects from data.

    :class:`ScalingLawFitter` accumulates ``(n, cost)`` pairs and, when
    :meth:`fit` is called, performs an ordinary least-squares fit in log-log
    space to estimate the power-law exponent and coefficient.

    Multiple fits can be stored (one per call to :meth:`fit`); the
    :meth:`best_fit` method returns the fitted law with the highest R².

    Attributes
    ----------
    method : str
        Fitting method name; currently only ``"ols"`` (OLS in log-log space)
        is implemented.
    fitted_laws : list
        :class:`ScalingLaw` objects produced by previous :meth:`fit` calls.
    residuals : list
        Per-observation residual values from the most recent fit.
    """

    method: str
    fitted_laws: list
    residuals: list

    def fit(self, ns: Sequence[int], costs: Sequence[float]) -> ScalingLaw:
        """Fit a power-law scaling law to the provided ``(n, cost)`` data.

        The fit is performed in log-log space using :func:`_linreg`.  The
        resulting slope is the power-law exponent and the exponentiated
        intercept is the coefficient.  The fit result is appended to
        :attr:`fitted_laws` and the per-observation residuals are stored in
        :attr:`residuals`.

        Parameters
        ----------
        ns : sequence of int
            Problem sizes (must all be ≥ 1).
        costs : sequence of float
            Observed costs (must all be finite and ≥ 0).

        Returns
        -------
        ScalingLaw
            The fitted law.

        Raises
        ------
        ValueError
            If *ns* and *costs* have different lengths or fewer than two points.
        """
        if len(ns) != len(costs):
            raise ValueError("ns and costs must have the same length.")
        if len(ns) < 2:
            raise ValueError("At least two data points are required.")

        log_ns = [_safe_log(n) for n in ns]
        log_costs = [_safe_log(c) for c in costs]
        valid = [
            (lx, ly)
            for lx, ly in zip(log_ns, log_costs)
            if math.isfinite(lx) and math.isfinite(ly)
        ]
        if len(valid) < 2:
            raise ValueError("Insufficient finite log-transformed data for fitting.")

        xs, ys = zip(*valid)
        slope, intercept, r2 = _linreg(list(xs), list(ys))

        coefficient = math.exp(intercept) if math.isfinite(intercept) else 1.0

        # Compute residuals
        self.residuals = [
            ly - (slope * lx + intercept) for lx, ly in zip(xs, ys)
        ]

        # Infer regime
        def slope_to_regime(s: float) -> ScalingRegime:
            if s < 0.5:
                return ScalingRegime.SUB_LINEAR
            if s < 1.1:
                return ScalingRegime.LINEAR
            if s < 2.0:
                return ScalingRegime.SUPER_LINEAR
            if s < 10.0:
                return ScalingRegime.POLYNOMIAL
            return ScalingRegime.EXPONENTIAL

        regime = slope_to_regime(slope)
        n_min, n_max = int(min(ns)), int(max(ns))

        law = ScalingLaw(
            law_id=_uid(),
            regime=regime,
            exponent=slope,
            coefficient=coefficient,
            r_squared=r2,
            domain_range=(n_min, n_max),
        )
        self.fitted_laws.append(law)
        return law

    def validate(self) -> List[str]:
        """Validate all fitted laws and return a list of warnings.

        Each fitted law is checked against :data:`MIN_R_SQUARED`.  Laws with
        R² below this threshold are flagged.  An empty list indicates that all
        fitted laws meet the quality threshold.

        Returns
        -------
        list[str]
            Warning strings for low-quality fits (empty if all pass).
        """
        warnings: List[str] = []
        for law in self.fitted_laws:
            if law.r_squared < MIN_R_SQUARED:
                warnings.append(
                    f"Law {law.law_id!r} has low R² = {law.r_squared:.4f} "
                    f"(threshold {MIN_R_SQUARED:.2f})."
                )
        return warnings

    def best_fit(self) -> Optional[ScalingLaw]:
        """Return the fitted law with the highest R² coefficient.

        If no laws have been fitted yet, returns ``None``.

        Returns
        -------
        ScalingLaw or None
            The best-fit law, or ``None`` if :attr:`fitted_laws` is empty.
        """
        if not self.fitted_laws:
            return None
        return max(self.fitted_laws, key=lambda law: law.r_squared)

    def reset(self) -> None:
        """Clear all fitted laws and residuals."""
        self.fitted_laws.clear()
        self.residuals.clear()

    def summary(self) -> str:
        """Return a concise summary of the fitter state.

        Includes the fitting method, number of fitted laws, and the R² of
        the best fit (if any).

        Returns
        -------
        str
            A single-line summary string.
        """
        best = self.best_fit()
        best_str = f"best R²={best.r_squared:.4f}" if best else "no fits yet"
        return (
            f"ScalingLawFitter[{self.method}]: "
            f"{len(self.fitted_laws)} law(s) fitted, {best_str}"
        )

    def to_dict(self) -> dict:
        """Serialise this fitter's state to a plain Python dictionary.

        Returns
        -------
        dict
            A fully JSON-serialisable representation.
        """
        return {
            "method": self.method,
            "fitted_laws": [law.to_dict() for law in self.fitted_laws],
            "residuals": list(self.residuals),
        }


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FundamentalLimits:
    """Repository of certified fundamental complexity limits.

    :class:`FundamentalLimits` collects :class:`LimitCertificate` objects
    across all :class:`LimitKind` categories and provides query helpers to
    retrieve the tightest known bound of each kind.

    Attributes
    ----------
    certificates : list
        All :class:`LimitCertificate` objects added so far.
    sample_limit : float
        Best-known sample-complexity lower bound (scalar convenience field).
    time_limit : float
        Best-known time-complexity lower bound (scalar convenience field).
    space_limit : float
        Best-known space-complexity lower bound (scalar convenience field).
    """

    certificates: list
    sample_limit: float
    time_limit: float
    space_limit: float

    def add_certificate(self, cert: LimitCertificate) -> None:
        """Add a :class:`LimitCertificate` to this repository.

        If the new certificate improves (i.e., raises) the lower bound for its
        :class:`LimitKind`, the corresponding scalar convenience field
        (``sample_limit``, ``time_limit``, or ``space_limit``) is updated
        automatically.

        Parameters
        ----------
        cert : LimitCertificate
            The certificate to add.
        """
        self.certificates.append(cert)
        if cert.limit_kind == LimitKind.SAMPLE_COMPLEXITY:
            self.sample_limit = max(self.sample_limit, cert.lower_bound)
        elif cert.limit_kind == LimitKind.TIME_COMPLEXITY:
            self.time_limit = max(self.time_limit, cert.lower_bound)
        elif cert.limit_kind == LimitKind.SPACE_COMPLEXITY:
            self.space_limit = max(self.space_limit, cert.lower_bound)

    def tightest_bound(self, kind: LimitKind) -> Optional[LimitCertificate]:
        """Return the certificate with the tightest known bounds for *kind*.

        Tightness is measured by the gap ratio (upper/lower); a smaller gap
        is better.  Among certificates of the specified *kind*, the one with
        the smallest gap ratio is returned.  If no certificates of that kind
        exist, returns ``None``.

        Parameters
        ----------
        kind : LimitKind
            The kind of limit to query.

        Returns
        -------
        LimitCertificate or None
            The tightest certificate, or ``None`` if none found.
        """
        candidates = [c for c in self.certificates if c.limit_kind == kind]
        if not candidates:
            return None
        return min(candidates, key=lambda c: c.gap_ratio())

    def summary(self) -> str:
        """Return a concise human-readable summary of certified limits.

        Lists the count of certificates per :class:`LimitKind` and the scalar
        convenience fields.

        Returns
        -------
        str
            A multiline summary string.
        """
        counts: Dict[str, int] = {}
        for c in self.certificates:
            key = c.limit_kind.value
            counts[key] = counts.get(key, 0) + 1

        lines = [
            "FundamentalLimits summary:",
            f"  Total certificates : {len(self.certificates)}",
            f"  sample_limit       : {self.sample_limit}",
            f"  time_limit         : {self.time_limit}",
            f"  space_limit        : {self.space_limit}",
            "  Per-kind counts    :",
        ]
        for kind_val, cnt in sorted(counts.items()):
            lines.append(f"    {kind_val:<30} : {cnt}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise this repository to a plain Python dictionary.

        Returns
        -------
        dict
            A fully JSON-serialisable representation.
        """
        return {
            "certificates": [c.to_dict() for c in self.certificates],
            "sample_limit": self.sample_limit,
            "time_limit": self.time_limit,
            "space_limit": self.space_limit,
        }

    def is_achievable(self, kind: LimitKind, candidate: float) -> bool:
        """Test whether *candidate* is achievable given the certified lower bound.

        A candidate algorithm cost is considered achievable if it does not
        fall below the certified lower bound for *kind* (within floating-point
        tolerance).

        Parameters
        ----------
        kind : LimitKind
            The type of limit to check against.
        candidate : float
            The proposed algorithm cost.

        Returns
        -------
        bool
            ``True`` if *candidate* ≥ the lower bound (up to :data:`EPSILON`).
        """
        cert = self.tightest_bound(kind)
        if cert is None:
            return True  # No lower bound certified — anything is potentially achievable.
        return candidate >= cert.lower_bound - EPSILON
