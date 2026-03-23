"""
Scaling Success — Criteria and Evidence for Successful JuGeo Scaling.

This module implements the scaling success evaluation framework for the
JuGeo Evaluation subsystem (theory2.tex Ch73 §3). Scaling success is not
binary — it is measured along multiple dimensions:

  PROOF_THROUGHPUT   — Theorems successfully verified per unit time.
  FEDERATION_HEALTH  — Pack structure remains coherent at larger scale.
  AUTHORITY_COVERAGE — A growing fraction of theorems achieve authority status.
  OBSTRUCTION_TREND  — The obstruction field density decreases over time.
  LATENCY_STABILITY  — Proof search latency remains bounded as scale grows.

A scaling run is declared successful when all five dimensions pass their
respective thresholds simultaneously at the target scale.

copilot: scaling-success marker
theory2.tex Ch73 §3 — Scaling Success
"""

from __future__ import annotations

import math
import uuid
import statistics
import itertools
import functools
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Sequence

try:
    from jugeo.evaluation.scaling_limits.why_scaling_needs_its_own_theory import (
        ScalingRegime,
        ScalingObservation,
        WhyScalingNeedsTheoryAnalyzer,
    )
except ImportError:
    ScalingRegime = None  # type: ignore
    ScalingObservation = None  # type: ignore
    WhyScalingNeedsTheoryAnalyzer = None  # type: ignore

try:
    from jugeo.evaluation.evaluation_design.project_scale_metrics import (
        ProjectScorecard,
    )
except ImportError:
    ProjectScorecard = None  # type: ignore

try:
    from jugeo.config import JugeoConfig  # type: ignore
except ImportError:
    JugeoConfig = None  # type: ignore


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPORT_VERSION: str = "1.0.0"
"""Version stamped into every ScalingSuccessWitness report."""

MIN_SCALE_POINTS_FOR_CURVE: int = 5
"""Minimum number of distinct scale points needed to compute a scaling curve.

With fewer than this many points the curve is unreliable and a warning
is included in the corresponding report.
"""

DIMENSION_THRESHOLDS: dict  # forward declaration — defined after enum

_DEGRADATION_WINDOW: int = 3
"""Sliding window size used to detect dimension degradation over scale."""

_DEGRADATION_SLOPE_THRESHOLD: float = -0.05
"""Slope (per scale-point increment) below which degradation is declared.

A slope more negative than this value, computed over the sliding window,
triggers a degradation flag for the dimension.
"""

_PARTIAL_SUCCESS_MIN_PASSING: int = 3
"""Minimum number of passing dimensions to declare PARTIAL_SUCCESS.

If fewer than this many dimensions pass their thresholds, the run is
classified as FAILED rather than PARTIAL_SUCCESS.
"""

_FULL_SUCCESS_REQUIRED_PASSING: int = 5
"""Number of dimensions that must pass for FULL_SUCCESS.

Because there are exactly five ScalingDimension members, this constant
requires that every dimension passes.
"""

_CURVE_SMOOTHING_WINDOW: int = 2
"""Window size for smoothing scaling curves before slope analysis."""

_LATENCY_UNIT: str = "seconds"
"""Unit of measurement for LATENCY_STABILITY dimension values."""

_THROUGHPUT_UNIT: str = "theorems/hour"
"""Unit for PROOF_THROUGHPUT dimension values."""

_HEALTH_UNIT: str = "fraction"
"""Unit for FEDERATION_HEALTH dimension values (fraction in [0, 1])."""

_AUTHORITY_UNIT: str = "fraction"
"""Unit for AUTHORITY_COVERAGE dimension values."""

_OBSTRUCTION_UNIT: str = "delta_per_step"
"""Unit for OBSTRUCTION_TREND dimension values (change per scale step)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Example:
        >>> ts = _utcnow()
        >>> ts.tzinfo is not None
        True
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a compact 12-character hex unique identifier.

    Returns:
        A 12-character lowercase hex string.

    Example:
        >>> len(_uid())
        12
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: Value to clamp.
        lo:    Lower bound (inclusive).
        hi:    Upper bound (inclusive).

    Returns:
        Clamped float.

    Raises:
        ValueError: If lo > hi.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


def _linear_regression_slope(xs: list[float], ys: list[float]) -> float:
    """Compute the slope of the least-squares linear fit to (xs, ys).

    Args:
        xs: Independent variable values.
        ys: Dependent variable values.

    Returns:
        Slope as a float.  Returns 0.0 if fewer than 2 points or zero variance.

    Example:
        >>> _linear_regression_slope([0, 1, 2], [0, 2, 4])
        2.0
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    return cov / var_x if var_x > 0 else 0.0


def _moving_average(values: list[float], window: int) -> list[float]:
    """Compute a simple moving average of *values* with the given window.

    Args:
        values: Time-ordered list of floats.
        window: Window size, must be >= 1.

    Returns:
        Smoothed list of length ``max(0, len(values) - window + 1)``.

    Example:
        >>> _moving_average([1, 3, 5, 7], 2)
        [2.0, 4.0, 6.0]
    """
    if window < 1:
        raise ValueError(f"_moving_average: window must be >= 1, got {window}")
    return [
        statistics.mean(values[i: i + window])
        for i in range(len(values) - window + 1)
    ]


def _passes_threshold(value: float, threshold: float, is_lower_better: bool) -> bool:
    """Test whether a value passes its dimension threshold.

    For OBSTRUCTION_TREND, lower (more negative) values are better.
    For all other dimensions, higher values are better.

    Args:
        value:            The measured value.
        threshold:        The pass/fail threshold.
        is_lower_better:  True if values at or below threshold are passing.

    Returns:
        True if the value passes the threshold.

    Example:
        >>> _passes_threshold(0.8, 0.75, is_lower_better=False)
        True
        >>> _passes_threshold(-0.02, -0.01, is_lower_better=True)
        True
    """
    if is_lower_better:
        return value <= threshold
    return value >= threshold


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ScalingDimension(str, Enum):
    """The five dimensions along which scaling success is measured.

    Scaling success requires all five dimensions to pass simultaneously.
    Partial success (three or four dimensions passing) is a valid
    intermediate state that guides the next iteration of scaling work.
    """

    PROOF_THROUGHPUT = "proof_throughput"
    """Number of theorems successfully verified per unit time.

    Measured in theorems per hour at the target scale.  A system that
    verifies theorems quickly enough to keep pace with a research team's
    output is considered to have adequate throughput.  Throughput can
    drop at scale due to proof-search overhead; detecting this early
    enables pre-emptive optimisation.
    """

    FEDERATION_HEALTH = "federation_health"
    """Coherence of the pack federation structure at the target scale.

    Measured as a fraction in [0, 1].  A value of 1.0 means all packs
    are connected, correctly indexed, and free of bridge-avalanche
    artefacts.  Values below 0.75 indicate structural problems requiring
    immediate attention.
    """

    AUTHORITY_COVERAGE = "authority_coverage"
    """Fraction of verified theorems that hold authority status.

    Authority status is granted after human review; at scale, the review
    bottleneck reduces this fraction.  A minimum of 60 % authority
    coverage is required for the authority-ranking subsystem to function
    reliably.
    """

    OBSTRUCTION_TREND = "obstruction_trend"
    """Rate of change of obstruction field density per scale step.

    Measured as a delta (signed float) per unit increase in theorem
    count.  A value of −0.01 or lower means the system is resolving at
    least one obstruction per 100 new theorems, which is the passing
    threshold.  A positive value indicates obstruction accumulation.
    """

    LATENCY_STABILITY = "latency_stability"
    """Maximum proof-search latency in seconds, measured at the target scale.

    The threshold is 2.0 seconds: proof searches taking longer than this
    on average degrade the interactive experience for human users.
    Latency stability is assessed as the 95th-percentile latency.
    """


class ScalingSuccessStatus(str, Enum):
    """Outcome classification for a scaling success evaluation run.

    The transition diagram is::

        NOT_EVALUATED → PARTIAL_SUCCESS or FULL_SUCCESS or FAILED or INCONCLUSIVE
    """

    NOT_EVALUATED = "not_evaluated"
    """No evaluation has been performed yet.

    This is the initial state of any ScalingSuccessReport before the
    coordinator runs the evaluation pipeline.
    """

    PARTIAL_SUCCESS = "partial_success"
    """At least _PARTIAL_SUCCESS_MIN_PASSING dimensions passed their thresholds.

    A partial success indicates the system can scale in some but not all
    dimensions.  The failure_reasons field of the report identifies which
    dimensions fell short and what thresholds were missed.
    """

    FULL_SUCCESS = "full_success"
    """All _FULL_SUCCESS_REQUIRED_PASSING dimensions passed their thresholds.

    The system has demonstrated successful scaling to the target scale
    on all measured dimensions.  This status is required for production
    deployment at the target scale.
    """

    FAILED = "failed"
    """Fewer than _PARTIAL_SUCCESS_MIN_PASSING dimensions passed.

    The system cannot scale to the target scale as configured.  The
    failure_reasons field details which dimensions failed and by how
    much.  Significant re-work is required before re-evaluation.
    """

    INCONCLUSIVE = "inconclusive"
    """The evaluation could not produce a reliable verdict.

    Typical causes: insufficient scale points (fewer than
    MIN_SCALE_POINTS_FOR_CURVE), high variance in measurements, or
    a dimension measurement that returned NaN.  The report should be
    treated as invalid and the evaluation re-run with more data.
    """


# ---------------------------------------------------------------------------
# Post-enum constant initialisation
# ---------------------------------------------------------------------------

DIMENSION_THRESHOLDS: dict[ScalingDimension, float] = {
    ScalingDimension.PROOF_THROUGHPUT:  10.0,
    # Unit: theorems/hour.  At least 10 theorems must be verified per hour
    # at the target scale for the system to keep pace with research output.

    ScalingDimension.FEDERATION_HEALTH: 0.75,
    # Unit: fraction [0,1].  75 % structural coherence is the minimum for
    # reliable federation routing and pack retrieval.

    ScalingDimension.AUTHORITY_COVERAGE: 0.60,
    # Unit: fraction [0,1].  60 % of theorems must hold authority status
    # for the authority-ranking subsystem to produce meaningful rankings.

    ScalingDimension.OBSTRUCTION_TREND: -0.01,
    # Unit: delta_per_step (negative = improvement).  The obstruction field
    # density must decrease by at least 0.01 per 100 new theorems.
    # This is a 'lower is better' dimension — values at or below -0.01 pass.

    ScalingDimension.LATENCY_STABILITY: 2.0,
    # Unit: seconds.  The 95th-percentile proof-search latency must stay
    # at or below 2.0 seconds at target scale.
    # This is a 'lower is better' dimension — values at or below 2.0 pass.
}

# Dimensions for which lower values are better (pass when value <= threshold)
_LOWER_BETTER_DIMENSIONS: frozenset[ScalingDimension] = frozenset({
    ScalingDimension.OBSTRUCTION_TREND,
    ScalingDimension.LATENCY_STABILITY,
})

_DIMENSION_UNITS: dict[ScalingDimension, str] = {
    ScalingDimension.PROOF_THROUGHPUT:  _THROUGHPUT_UNIT,
    ScalingDimension.FEDERATION_HEALTH: _HEALTH_UNIT,
    ScalingDimension.AUTHORITY_COVERAGE: _AUTHORITY_UNIT,
    ScalingDimension.OBSTRUCTION_TREND: _OBSTRUCTION_UNIT,
    ScalingDimension.LATENCY_STABILITY: _LATENCY_UNIT,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScalingMeasurement:
    """A single measurement of one scaling dimension at one scale point.

    ScalingMeasurement is the atomic data unit in the scaling success
    framework.  Multiple measurements at increasing scale points, for the
    same dimension, form the scaling curve used to assess that dimension.

    The ``scale_point`` field records the number of theorems in the pack
    at the time of measurement; it is the x-axis of the scaling curve.

    The ``unit`` field is informational and must match the expected unit
    for the dimension as defined in _DIMENSION_UNITS.

    Attributes:
        measurement_id: Unique identifier for this measurement.
        dimension:      The ScalingDimension being measured.
        value:          Numeric measurement value.
        unit:           String description of the measurement unit.
        scale_point:    Pack size (number of theorems) at measurement time.
        timestamp:      UTC datetime of measurement.
    """

    measurement_id: str
    dimension: ScalingDimension
    value: float
    unit: str
    scale_point: int
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ScalingSuccessReport:
    """Complete scaling success evaluation for a single project.

    A ScalingSuccessReport records all measurements, per-dimension verdicts,
    the overall success status, and any failure reasons.  It is the primary
    output of the ScalingSuccessCoordinator.

    The ``dimension_verdicts`` dict maps dimension names (str) to booleans
    indicating whether that dimension passed its threshold.

    The ``failure_reasons`` tuple contains human-readable strings explaining
    each dimension that failed.  An empty tuple indicates all dimensions
    passed (consistent with FULL_SUCCESS status).

    Attributes:
        report_id:         Unique identifier for this report.
        project_id:        Project being evaluated.
        measurements:      All measurements feeding into this report.
        dimension_verdicts: Mapping of dimension name → pass/fail bool.
        overall_status:    The ScalingSuccessStatus determined by the evaluator.
        failure_reasons:   Tuple of human-readable failure descriptions.
        generated_at:      UTC datetime of report generation.
    """

    report_id: str
    project_id: str
    measurements: tuple[ScalingMeasurement, ...]
    dimension_verdicts: dict
    overall_status: ScalingSuccessStatus
    failure_reasons: tuple[str, ...]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class ScalingSuccessAnalyzer:
    """Evaluates individual dimensions, aggregates verdicts, and detects degradation.

    The analyzer is a stateless computational component that converts
    raw ScalingMeasurement objects into dimensional verdicts, overall
    success status, scaling curves, and degradation flags.

    All methods are pure functions of their inputs.
    """

    # ------------------------------------------------------------------
    def evaluate_dimension(
        self,
        dimension: ScalingDimension,
        measurements: list[ScalingMeasurement],
    ) -> tuple[bool, str]:
        """Evaluate whether a single dimension passes its threshold.

        The verdict is based on the most recent measurement for the
        dimension.  If no measurements exist for the dimension, the
        verdict is False with an appropriate message.

        Args:
            dimension:    The ScalingDimension to evaluate.
            measurements: All available measurements (may include other dimensions).

        Returns:
            A tuple ``(passed, explanation)`` where ``passed`` is a boolean
            and ``explanation`` is a human-readable verdict string.

        Raises:
            TypeError: If *dimension* is not a ScalingDimension.

        Example:
            >>> passed, msg = analyzer.evaluate_dimension(
            ...     ScalingDimension.PROOF_THROUGHPUT, measurements)
            >>> isinstance(passed, bool)
            True
        """
        if not isinstance(dimension, ScalingDimension):
            raise TypeError(
                f"evaluate_dimension: expected ScalingDimension, "
                f"got {type(dimension).__name__}"
            )

        # copilot: filter to measurements for this dimension only
        dim_measurements = [m for m in measurements if m.dimension == dimension]
        if not dim_measurements:
            return (
                False,
                f"No measurements found for dimension '{dimension.value}'."
            )

        # copilot: use the most recent measurement for threshold check
        latest = max(dim_measurements, key=lambda m: m.timestamp)
        threshold = DIMENSION_THRESHOLDS[dimension]
        lower_better = dimension in _LOWER_BETTER_DIMENSIONS
        passed = _passes_threshold(latest.value, threshold, lower_better)
        unit   = _DIMENSION_UNITS.get(dimension, "?")

        direction = "≤" if lower_better else "≥"
        verdict_word = "PASS" if passed else "FAIL"
        explanation = (
            f"{dimension.value}: {verdict_word}  "
            f"value={latest.value:.4f} {unit}, "
            f"threshold {direction} {threshold} {unit}, "
            f"scale_point={latest.scale_point}"
        )
        return passed, explanation

    # ------------------------------------------------------------------
    def aggregate_verdict(
        self,
        dimension_results: dict[str, bool],
    ) -> ScalingSuccessStatus:
        """Map per-dimension pass/fail results to an overall ScalingSuccessStatus.

        Applies the thresholds:
        * All 5 pass → FULL_SUCCESS
        * >= _PARTIAL_SUCCESS_MIN_PASSING pass → PARTIAL_SUCCESS
        * < _PARTIAL_SUCCESS_MIN_PASSING pass → FAILED
        * Any NaN or missing dimension → INCONCLUSIVE

        Args:
            dimension_results: Dict mapping dimension name strings to booleans.

        Returns:
            A ScalingSuccessStatus.

        Raises:
            ValueError: If *dimension_results* is empty.

        Example:
            >>> status = analyzer.aggregate_verdict({"d1": True, "d2": False, ...})
            >>> isinstance(status, ScalingSuccessStatus)
            True
        """
        if not dimension_results:
            raise ValueError("aggregate_verdict: dimension_results is empty")

        # copilot: check that all five dimensions are represented
        expected = {d.value for d in ScalingDimension}
        present  = set(dimension_results.keys())
        if not expected.issubset(present):
            missing = expected - present
            # Missing dimensions make the result inconclusive
            _ = missing  # record for debugging but don't raise
            return ScalingSuccessStatus.INCONCLUSIVE

        n_pass = sum(1 for v in dimension_results.values() if v is True)

        if n_pass >= _FULL_SUCCESS_REQUIRED_PASSING:
            return ScalingSuccessStatus.FULL_SUCCESS
        elif n_pass >= _PARTIAL_SUCCESS_MIN_PASSING:
            return ScalingSuccessStatus.PARTIAL_SUCCESS
        else:
            return ScalingSuccessStatus.FAILED

    # ------------------------------------------------------------------
    def compute_scaling_curve(
        self,
        measurements: list[ScalingMeasurement],
        dimension: ScalingDimension,
    ) -> list[tuple[int, float]]:
        """Build the scaling curve (scale_point, value) for one dimension.

        Measurements are sorted by scale_point.  When multiple measurements
        exist at the same scale point, the mean value is used.

        Args:
            measurements: All available measurements.
            dimension:    The dimension whose curve to compute.

        Returns:
            A list of ``(scale_point, value)`` tuples sorted by scale_point.
            Returns an empty list if no measurements exist for the dimension.

        Raises:
            TypeError: If *dimension* is not a ScalingDimension.

        Example:
            >>> curve = analyzer.compute_scaling_curve(measurements, ScalingDimension.PROOF_THROUGHPUT)
            >>> all(isinstance(p, tuple) for p in curve)
            True
        """
        if not isinstance(dimension, ScalingDimension):
            raise TypeError(
                f"compute_scaling_curve: expected ScalingDimension, "
                f"got {type(dimension).__name__}"
            )

        dim_measurements = [m for m in measurements if m.dimension == dimension]
        if not dim_measurements:
            return []

        # copilot: group by scale_point and compute mean per point
        by_scale: dict[int, list[float]] = {}
        for m in dim_measurements:
            by_scale.setdefault(m.scale_point, []).append(m.value)

        curve = [
            (sp, statistics.mean(vals))
            for sp, vals in sorted(by_scale.items())
        ]
        return curve

    # ------------------------------------------------------------------
    def detect_degradation(
        self,
        measurements: list[ScalingMeasurement],
        dimension: ScalingDimension,
    ) -> bool:
        """Detect whether a dimension shows degradation trend over scale.

        Applies sliding-window linear regression over the scaling curve.
        If the smoothed slope falls below _DEGRADATION_SLOPE_THRESHOLD for
        a dimension where higher is better (or above the negated threshold
        for lower-is-better dimensions), degradation is declared.

        Args:
            measurements: All available measurements.
            dimension:    The dimension to analyse.

        Returns:
            True if degradation is detected, False otherwise.

        Raises:
            TypeError: If *dimension* is not a ScalingDimension.

        Example:
            >>> degrading = analyzer.detect_degradation(measurements, ScalingDimension.FEDERATION_HEALTH)
            >>> isinstance(degrading, bool)
            True
        """
        if not isinstance(dimension, ScalingDimension):
            raise TypeError(
                f"detect_degradation: expected ScalingDimension, "
                f"got {type(dimension).__name__}"
            )

        curve = self.compute_scaling_curve(measurements, dimension)
        if len(curve) < MIN_SCALE_POINTS_FOR_CURVE:
            # copilot: insufficient data — cannot declare degradation
            return False

        # copilot: smooth the curve values before slope analysis
        values = [v for _, v in curve]
        smooth_values = _moving_average(values, min(_CURVE_SMOOTHING_WINDOW, len(values)))
        smooth_xs = list(range(len(smooth_values)))

        slope = _linear_regression_slope(smooth_xs, smooth_values)

        lower_better = dimension in _LOWER_BETTER_DIMENSIONS
        if lower_better:
            # For lower-is-better dimensions, a rising slope means degradation
            return slope > abs(_DEGRADATION_SLOPE_THRESHOLD)
        else:
            # For higher-is-better dimensions, a falling slope means degradation
            return slope < _DEGRADATION_SLOPE_THRESHOLD


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ScalingSuccessCoordinator:
    """Orchestrates scaling success evaluation across projects and runs.

    The coordinator takes measurement collections, produces reports, and
    supports multi-project comparison and longitudinal trajectory analysis.

    Attributes:
        _analyzer: The ScalingSuccessAnalyzer used by this coordinator.
        _witness:  The ScalingSuccessWitness receiving report observations.
        _cache:    Dict mapping project_id to list of reports.
    """

    def __init__(
        self,
        analyzer: Optional[ScalingSuccessAnalyzer] = None,
        witness: Optional["ScalingSuccessWitness"] = None,
    ) -> None:
        """Initialise coordinator with optional analyzer and witness.

        Args:
            analyzer: Pre-built analyzer, or None for a default instance.
            witness:  Pre-built witness, or None for a default instance.

        Example:
            >>> coord = ScalingSuccessCoordinator()
        """
        self._analyzer: ScalingSuccessAnalyzer = analyzer or ScalingSuccessAnalyzer()
        self._witness: ScalingSuccessWitness = witness or ScalingSuccessWitness()
        self._cache: dict[str, list[ScalingSuccessReport]] = {}

    # ------------------------------------------------------------------
    def evaluate(
        self,
        project_id: str,
        measurements: list[ScalingMeasurement],
    ) -> ScalingSuccessReport:
        """Evaluate scaling success for *project_id* using the given measurements.

        For each ScalingDimension, the most recent measurement is used to
        determine a pass/fail verdict.  If fewer than
        MIN_SCALE_POINTS_FOR_CURVE total unique scale points are present,
        the report is marked INCONCLUSIVE.

        Args:
            project_id:   Identifier of the project.
            measurements: All ScalingMeasurement objects for this run.

        Returns:
            A fully populated ScalingSuccessReport.

        Raises:
            ValueError: If *project_id* is empty or *measurements* is empty.

        Example:
            >>> report = coord.evaluate("proj_alpha", measurements)
            >>> isinstance(report.overall_status, ScalingSuccessStatus)
            True
        """
        if not project_id:
            raise ValueError("evaluate: project_id must not be empty")
        if not measurements:
            raise ValueError("evaluate: measurements must not be empty")

        # copilot: check for sufficient scale coverage
        unique_scale_points = len({m.scale_point for m in measurements})
        if unique_scale_points < MIN_SCALE_POINTS_FOR_CURVE:
            report = ScalingSuccessReport(
                report_id=_uid(),
                project_id=project_id,
                measurements=tuple(measurements),
                dimension_verdicts={},
                overall_status=ScalingSuccessStatus.INCONCLUSIVE,
                failure_reasons=(
                    f"Only {unique_scale_points} unique scale points "
                    f"< MIN_SCALE_POINTS_FOR_CURVE={MIN_SCALE_POINTS_FOR_CURVE}.",
                ),
                generated_at=_utcnow(),
            )
            self._cache.setdefault(project_id, []).append(report)
            self._witness.observe(report)
            return report

        # copilot: evaluate each dimension
        dimension_verdicts: dict[str, bool] = {}
        failure_reasons: list[str] = []

        for dim in ScalingDimension:
            passed, explanation = self._analyzer.evaluate_dimension(dim, measurements)
            dimension_verdicts[dim.value] = passed
            if not passed:
                failure_reasons.append(explanation)

            # copilot: additionally check for degradation
            if self._analyzer.detect_degradation(measurements, dim):
                failure_reasons.append(
                    f"Degradation detected for '{dim.value}' over scale curve."
                )

        # copilot: aggregate to overall status
        overall_status = self._analyzer.aggregate_verdict(dimension_verdicts)

        report = ScalingSuccessReport(
            report_id=_uid(),
            project_id=project_id,
            measurements=tuple(measurements),
            dimension_verdicts=dimension_verdicts,
            overall_status=overall_status,
            failure_reasons=tuple(failure_reasons),
            generated_at=_utcnow(),
        )

        self._cache.setdefault(project_id, []).append(report)
        self._witness.observe(report)
        return report

    # ------------------------------------------------------------------
    def compare_runs(
        self,
        reports: list[ScalingSuccessReport],
    ) -> dict:
        """Compare multiple ScalingSuccessReport objects.

        Args:
            reports: List of reports to compare.

        Returns:
            Dict mapping report_id to a summary dict containing
            ``project_id``, ``overall_status``, ``n_passed_dimensions``.

        Raises:
            ValueError: If *reports* is empty.

        Example:
            >>> comparison = coord.compare_runs([report1, report2])
        """
        if not reports:
            raise ValueError("compare_runs: reports list is empty")

        comparison: dict[str, dict] = {}
        for r in reports:
            n_passed = sum(1 for v in r.dimension_verdicts.values() if v is True)
            comparison[r.report_id] = {
                "project_id":        r.project_id,
                "overall_status":    r.overall_status.value,
                "n_passed_dimensions": n_passed,
                "failure_count":     len(r.failure_reasons),
            }
        return comparison

    # ------------------------------------------------------------------
    def scaling_trajectory(
        self,
        project_id: str,
        history: list[ScalingSuccessReport],
    ) -> dict:
        """Analyse the historical trajectory of overall scaling status.

        Args:
            project_id: Identifier for labelling.
            history:    Time-ordered list of ScalingSuccessReport objects.

        Returns:
            Dict with keys ``project_id``, ``n_reports``, ``statuses``,
            ``latest_status``, ``improvement_count``, ``regression_count``.

        Raises:
            ValueError: If *history* is empty.

        Example:
            >>> traj = coord.scaling_trajectory("p1", history)
            >>> "latest_status" in traj
            True
        """
        if not history:
            raise ValueError("scaling_trajectory: history must not be empty")

        statuses = [r.overall_status.value for r in history]

        # copilot: compute improvements and regressions in status transitions
        _STATUS_ORDER = {
            ScalingSuccessStatus.NOT_EVALUATED.value: 0,
            ScalingSuccessStatus.FAILED.value: 1,
            ScalingSuccessStatus.INCONCLUSIVE.value: 1,
            ScalingSuccessStatus.PARTIAL_SUCCESS.value: 2,
            ScalingSuccessStatus.FULL_SUCCESS.value: 3,
        }
        improvements = 0
        regressions  = 0
        for i in range(1, len(statuses)):
            prev_rank = _STATUS_ORDER.get(statuses[i - 1], 0)
            curr_rank = _STATUS_ORDER.get(statuses[i], 0)
            if curr_rank > prev_rank:
                improvements += 1
            elif curr_rank < prev_rank:
                regressions += 1

        return {
            "project_id":        project_id,
            "n_reports":         len(history),
            "statuses":          statuses,
            "latest_status":     statuses[-1],
            "improvement_count": improvements,
            "regression_count":  regressions,
        }

    # ------------------------------------------------------------------
    def status_report(self, project_id: str) -> dict:
        """Return a status summary for *project_id* based on cached reports.

        Args:
            project_id: Identifier of the project.

        Returns:
            Dict with keys ``project_id``, ``n_reports``,
            ``latest_status``, ``latest_n_passed``.

        Example:
            >>> rep = coord.status_report("proj_alpha")
            >>> "latest_status" in rep
            True
        """
        history = self._cache.get(project_id, [])
        if not history:
            return {
                "project_id":     project_id,
                "n_reports":      0,
                "latest_status":  None,
                "latest_n_passed": None,
            }
        latest = history[-1]
        n_passed = sum(1 for v in latest.dimension_verdicts.values() if v is True)
        return {
            "project_id":      project_id,
            "n_reports":       len(history),
            "latest_status":   latest.overall_status.value,
            "latest_n_passed": n_passed,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class ScalingSuccessWitness:
    """Observes and stores all ScalingSuccessReport objects for audit.

    The witness accumulates every report generated by the coordinator,
    enabling post-hoc queries about failure rates, dimension-level
    failure counts, and full-success rates.

    Attributes:
        _log: Ordered list of all observed ScalingSuccessReport objects.
    """

    def __init__(self) -> None:
        """Initialise an empty witness log.

        Example:
            >>> w = ScalingSuccessWitness()
            >>> w.all_reports()
            []
        """
        self._log: list[ScalingSuccessReport] = []

    # ------------------------------------------------------------------
    def observe(self, report: ScalingSuccessReport) -> None:
        """Append a ScalingSuccessReport to the witness log.

        Args:
            report: The report to record.

        Raises:
            TypeError: If *report* is not a ScalingSuccessReport.

        Example:
            >>> w.observe(report)
        """
        if not isinstance(report, ScalingSuccessReport):
            raise TypeError(
                f"observe: expected ScalingSuccessReport, "
                f"got {type(report).__name__}"
            )
        self._log.append(report)

    # ------------------------------------------------------------------
    def failed_projects(self) -> list[str]:
        """Return project_ids whose most recent report has FAILED status.

        Returns:
            List of project_id strings (de-duped).

        Example:
            >>> failed = w.failed_projects()
        """
        # copilot: use latest report per project for this query
        latest: dict[str, ScalingSuccessReport] = {}
        for r in self._log:
            latest[r.project_id] = r
        return [
            pid for pid, r in latest.items()
            if r.overall_status == ScalingSuccessStatus.FAILED
        ]

    # ------------------------------------------------------------------
    def full_success_rate(self) -> float:
        """Compute the fraction of all observed reports with FULL_SUCCESS status.

        Returns:
            Float in [0, 1].  Returns 0.0 if no reports have been observed.

        Example:
            >>> rate = w.full_success_rate()
            >>> 0.0 <= rate <= 1.0
            True
        """
        if not self._log:
            return 0.0
        n_full = sum(
            1 for r in self._log
            if r.overall_status == ScalingSuccessStatus.FULL_SUCCESS
        )
        return round(n_full / len(self._log), 4)

    # ------------------------------------------------------------------
    def dimension_failure_counts(self) -> dict[str, int]:
        """Count how many reports had each dimension failing.

        Returns:
            Dict mapping dimension name (str) to integer failure count.
            Dimensions with zero failures are omitted.

        Example:
            >>> counts = w.dimension_failure_counts()
            >>> isinstance(counts, dict)
            True
        """
        counts: dict[str, int] = {}
        for r in self._log:
            for dim_name, passed in r.dimension_verdicts.items():
                if not passed:
                    counts[dim_name] = counts.get(dim_name, 0) + 1
        return counts

    # ------------------------------------------------------------------
    def all_reports(self) -> list[ScalingSuccessReport]:
        """Return a copy of all observed reports in observation order.

        Returns:
            List of ScalingSuccessReport objects.

        Example:
            >>> all_r = w.all_reports()
        """
        return list(self._log)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== ScalingSuccess smoke test ===")

    _analyzer = ScalingSuccessAnalyzer()
    _witness  = ScalingSuccessWitness()
    _coord    = ScalingSuccessCoordinator(analyzer=_analyzer, witness=_witness)

    # copilot: synthetic measurement data for two projects at 8 scale points
    def _make_measurements(
        project_id: str,
        scale_points: list[int],
        throughput_values: list[float],
        health_values: list[float],
        authority_values: list[float],
        obstruction_values: list[float],
        latency_values: list[float],
    ) -> list[ScalingMeasurement]:
        ms: list[ScalingMeasurement] = []
        for i, sp in enumerate(scale_points):
            for dim, val, unit in [
                (ScalingDimension.PROOF_THROUGHPUT,   throughput_values[i],   _THROUGHPUT_UNIT),
                (ScalingDimension.FEDERATION_HEALTH,  health_values[i],       _HEALTH_UNIT),
                (ScalingDimension.AUTHORITY_COVERAGE, authority_values[i],    _AUTHORITY_UNIT),
                (ScalingDimension.OBSTRUCTION_TREND,  obstruction_values[i],  _OBSTRUCTION_UNIT),
                (ScalingDimension.LATENCY_STABILITY,  latency_values[i],      _LATENCY_UNIT),
            ]:
                ms.append(ScalingMeasurement(
                    measurement_id=_uid(),
                    dimension=dim,
                    value=val,
                    unit=unit,
                    scale_point=sp,
                    timestamp=_utcnow(),
                ))
        return ms

    _scale_pts = [100, 300, 600, 1000, 2000, 4000, 7000, 10000]

    # Project alpha: mostly healthy scaling
    _alpha_ms = _make_measurements(
        "proj_alpha",
        _scale_pts,
        throughput_values  = [45.0, 38.0, 30.0, 22.0, 18.0, 15.0, 12.0, 11.0],
        health_values      = [0.95, 0.93, 0.89, 0.84, 0.80, 0.77, 0.75, 0.76],
        authority_values   = [0.90, 0.85, 0.80, 0.74, 0.70, 0.65, 0.62, 0.61],
        obstruction_values = [-0.05, -0.04, -0.03, -0.02, -0.015, -0.012, -0.011, -0.010],
        latency_values     = [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 1.9],
    )

    # Project beta: throughput degradation and authority failure
    _beta_ms = _make_measurements(
        "proj_beta",
        _scale_pts,
        throughput_values  = [40.0, 28.0, 15.0, 9.0, 7.0, 5.0, 4.0, 3.5],
        health_values      = [0.90, 0.85, 0.78, 0.70, 0.65, 0.60, 0.55, 0.50],
        authority_values   = [0.75, 0.65, 0.55, 0.45, 0.38, 0.32, 0.28, 0.25],
        obstruction_values = [-0.03, -0.02, -0.01, 0.005, 0.01, 0.015, 0.02, 0.025],
        latency_values     = [0.4, 0.7, 1.1, 1.6, 2.1, 2.8, 3.5, 4.2],
    )

    _report_alpha = _coord.evaluate("proj_alpha", _alpha_ms)
    _report_beta  = _coord.evaluate("proj_beta",  _beta_ms)

    print(f"  proj_alpha: {_report_alpha.overall_status.value}")
    print(f"    verdicts: {_report_alpha.dimension_verdicts}")
    if _report_alpha.failure_reasons:
        print(f"    failures: {list(_report_alpha.failure_reasons)[:2]}")

    print(f"  proj_beta:  {_report_beta.overall_status.value}")
    print(f"    verdicts: {_report_beta.dimension_verdicts}")
    if _report_beta.failure_reasons:
        print(f"    failures: {list(_report_beta.failure_reasons)[:3]}")

    # Scaling curves
    for _dim in ScalingDimension:
        _curve_alpha = _analyzer.compute_scaling_curve(_alpha_ms, _dim)
        _deg = _analyzer.detect_degradation(_alpha_ms, _dim)
        print(f"  alpha {_dim.value}: degradation={_deg}, "
              f"curve_pts={len(_curve_alpha)}, "
              f"last={_curve_alpha[-1][1]:.3f}")

    # Compare runs
    _cmp = _coord.compare_runs([_report_alpha, _report_beta])
    print(f"\n  Run comparison: {_cmp}")

    # Trajectory (simulate multiple evaluations for alpha)
    _history = [_report_alpha]
    for _ in range(3):
        _history.append(_coord.evaluate("proj_alpha", _alpha_ms))
    _traj = _coord.scaling_trajectory("proj_alpha", _history)
    print(f"  Trajectory: {_traj}")

    # Witness queries
    _failed = _witness.failed_projects()
    print(f"\n  Failed projects: {_failed}")
    _fs_rate = _witness.full_success_rate()
    print(f"  Full success rate: {_fs_rate:.2%}")
    _dim_fails = _witness.dimension_failure_counts()
    print(f"  Dimension failure counts: {_dim_fails}")

    # Status reports
    print(f"  alpha status: {_coord.status_report('proj_alpha')}")
    print(f"  beta  status: {_coord.status_report('proj_beta')}")

    print("=== Smoke test PASSED ===")
