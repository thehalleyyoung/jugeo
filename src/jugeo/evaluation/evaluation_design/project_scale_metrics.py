"""
Project-Scale Metrics — Measuring JuGeo at the Level of a Full Research Project.

This module implements project-scale metric collection and analysis for the
JuGeo Evaluation Design subsystem (theory2.tex Ch72 §4). Project-scale
metrics aggregate clause-wise scores and ablation deltas into holistic
measures of a full research project's quality and impact.

Project-scale dimensions modelled:
  THEOREM_COVERAGE    — Fraction of claimed theorems that are verified.
  PROOF_DEPTH         — Maximum and average depth of the proof graph.
  FEDERATION_DENSITY  — How well theorems are integrated into existing packs.
  OBSTRUCTION_REDUCTION — Total reduction in obstruction field density.
  REVISION_STABILITY  — How many falsification loop revisions were required.

copilot: project-scale-metrics marker
theory2.tex Ch72 §4 — Project-Scale Metrics
"""

from __future__ import annotations

import math
import uuid
import statistics
import itertools
import functools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Sequence

try:
    from jugeo.evaluation.evaluation_design.ablation_philosophy import (
        AblationSchedule,
    )
except ImportError:
    AblationSchedule = None  # type: ignore

try:
    from jugeo.config import JugeoConfig  # type: ignore
except ImportError:
    JugeoConfig = None  # type: ignore


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPORT_VERSION: str = "1.0.0"
"""Version stamped into every ProjectScaleMetricsWitness report."""

_MISSING_FLOAT: float = float("nan")
"""Sentinel value indicating a metric measurement is unavailable."""

COMPOSITE_WEIGHT_TABLE: dict  # forward declaration — defined after enum

HEALTH_BAND_THRESHOLDS: dict  # forward declaration — defined after enum

_ANOMALY_Z_THRESHOLD: float = 2.5
"""Z-score threshold above which a metric sample is flagged as anomalous."""

_MIN_SAMPLES_FOR_TREND: int = 3
"""Minimum number of historical scorecards required to compute a trend."""

_PROOF_DEPTH_EXCELLENT_THRESHOLD: float = 8.0
"""Proof depth above which PROOF_DEPTH_MAX is considered indicative of deep results."""

_COVERAGE_POOR_THRESHOLD: float = 0.40
"""Theorem coverage below this value triggers a CRITICAL health-band flag."""

_STABILITY_REVISION_LIMIT: int = 10
"""Projects requiring more than this many revisions are flagged as unstable."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime object.

    This wrapper centralises the call so tests can monkeypatch it
    without touching the standard library directly.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Example:
        >>> ts = _utcnow()
        >>> ts.tzinfo is not None
        True
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a compact unique identifier string.

    Derived from UUID4 and shortened to 12 hex characters for legibility.

    Returns:
        A 12-character lowercase hex string.

    Example:
        >>> uid = _uid()
        >>> len(uid)
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
        ValueError: If ``lo > hi``.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    """Compute the weighted mean of *values* using *weights*.

    Args:
        values:  List of numeric values.
        weights: Corresponding non-negative weights (must sum > 0).

    Returns:
        Weighted mean as a float.

    Raises:
        ValueError: If lengths differ or total weight is zero.

    Example:
        >>> _weighted_mean([0.8, 0.6], [0.3, 0.7])
        0.66
    """
    if len(values) != len(weights):
        raise ValueError("_weighted_mean: values and weights must have the same length")
    total_weight = sum(weights)
    if total_weight <= 0.0:
        raise ValueError("_weighted_mean: total weight must be positive")
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def _normalise_to_unit(value: float, min_val: float, max_val: float) -> float:
    """Linearly normalise *value* from [min_val, max_val] to [0, 1].

    Args:
        value:   Raw metric value.
        min_val: Expected minimum of the metric range.
        max_val: Expected maximum of the metric range.

    Returns:
        Normalised float in [0, 1].

    Example:
        >>> _normalise_to_unit(5.0, 0.0, 10.0)
        0.5
    """
    if max_val <= min_val:
        return 0.0
    return _clamp((value - min_val) / (max_val - min_val), 0.0, 1.0)


def _trend_slope(values: list[float]) -> float:
    """Compute the linear trend slope of a time-ordered list of values.

    Uses least-squares linear regression.  Returns 0.0 for lists shorter
    than 2 elements.

    Args:
        values: Time-ordered list of floats.

    Returns:
        Slope of the best-fit line (Δy per step).

    Example:
        >>> _trend_slope([0.5, 0.6, 0.7])
        0.1
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(values)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    return cov / var_x if var_x > 0 else 0.0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProjectMetricKind(str, Enum):
    """Enumeration of all project-scale metric kinds tracked by JuGeo.

    Each kind corresponds to one measurable dimension of project quality.
    The composite scorer weights these kinds according to COMPOSITE_WEIGHT_TABLE.

    These metrics are designed to capture orthogonal aspects of a
    research project's verification progress and structural health.
    """

    THEOREM_COVERAGE = "theorem_coverage"
    """Fraction of claimed theorems that have been formally verified.

    A value of 1.0 means every theorem claimed in the project has a
    machine-verified proof; 0.0 means none have been verified.  This is
    the most direct measure of research completeness.
    """

    PROOF_DEPTH_MAX = "proof_depth_max"
    """Maximum depth (in proof steps) of the deepest proof in the project.

    Deep proofs indicate the system is tackling non-trivial mathematics.
    A low maximum depth may indicate shallow lemma coverage or that the
    project has not yet been fully expanded.
    """

    PROOF_DEPTH_MEAN = "proof_depth_mean"
    """Mean depth over all verified proofs in the project.

    Complements PROOF_DEPTH_MAX by capturing the typical difficulty of
    proofs.  High mean depth with low maximum depth is unusual and may
    signal measurement error.
    """

    FEDERATION_DENSITY = "federation_density"
    """Fraction of theorems that are tightly integrated into the pack federation.

    A theorem is 'federated' if it appears in at least one authority
    pack with cross-references from at least two other packs.  Dense
    federation indicates the project contributes robustly to the wider
    knowledge graph.
    """

    OBSTRUCTION_REDUCTION = "obstruction_reduction"
    """Total reduction in obstruction field density achieved by the project.

    Obstruction fields record the set of unresolved proof obligations.
    A positive value means the project eliminated more obstructions than
    it introduced; a negative value signals net regression.
    """

    REVISION_STABILITY = "revision_stability"
    """Inverse measure of how many falsification-loop revisions were required.

    Formally: ``1 / (1 + num_revisions)``.  A score of 1.0 means no
    revisions were needed; values approaching 0.0 indicate a highly
    unstable proof attempt that required many rewrites.
    """

    TOTAL_VERIFIED_THEOREMS = "total_verified_theorems"
    """Raw count of formally verified theorems in the project.

    Unlike THEOREM_COVERAGE (a fraction), this is an absolute number
    useful for comparing projects of different stated scope.  Large
    projects are expected to have higher values.
    """

    PACK_SPAN = "pack_span"
    """Number of distinct packs that contain at least one theorem from this project.

    High pack span indicates the project's contributions are distributed
    across the federation rather than confined to a single pack, which
    is generally desirable for integration and discoverability.
    """


class ProjectHealthBand(str, Enum):
    """Five-level health classification for a project's composite metric score.

    Each band corresponds to a range of composite scores and triggers
    different responses from the evaluation system.  CRITICAL projects
    are escalated immediately; EXCELLENT projects are candidates for
    authority promotion.
    """

    CRITICAL = "critical"
    """Composite score in [0.0, 0.30).

    The project has severe metric failures — typically very low theorem
    coverage or negative obstruction trend.  Immediate intervention is
    required before further development proceeds.
    """

    POOR = "poor"
    """Composite score in [0.30, 0.50).

    Multiple metrics are below acceptable thresholds.  The project
    requires significant rework before it can be considered for
    federation integration.
    """

    ACCEPTABLE = "acceptable"
    """Composite score in [0.50, 0.70).

    The project meets minimum quality requirements but has clear room
    for improvement.  Flagged items should be addressed in the next
    revision cycle.
    """

    GOOD = "good"
    """Composite score in [0.70, 0.85).

    The project is performing well on most dimensions.  A small number
    of metrics may be below their ideal values but none are critical.
    """

    EXCELLENT = "excellent"
    """Composite score in [0.85, 1.0].

    The project exceeds quality benchmarks on all major dimensions and
    is a candidate for authority-pack promotion.  Excellent projects
    are used as reference benchmarks for new projects.
    """


# ---------------------------------------------------------------------------
# Post-enum constant initialisation
# ---------------------------------------------------------------------------

COMPOSITE_WEIGHT_TABLE: dict[ProjectMetricKind, float] = {
    ProjectMetricKind.THEOREM_COVERAGE:      0.30,
    ProjectMetricKind.PROOF_DEPTH_MAX:        0.08,
    ProjectMetricKind.PROOF_DEPTH_MEAN:       0.07,
    ProjectMetricKind.FEDERATION_DENSITY:     0.18,
    ProjectMetricKind.OBSTRUCTION_REDUCTION:  0.15,
    ProjectMetricKind.REVISION_STABILITY:     0.10,
    ProjectMetricKind.TOTAL_VERIFIED_THEOREMS: 0.07,
    ProjectMetricKind.PACK_SPAN:              0.05,
}
"""Weights used to compute the composite project score.

Weights sum to 1.0.  THEOREM_COVERAGE dominates because it most directly
reflects research completeness; PACK_SPAN has the smallest weight as it
is influenced by project size rather than quality.
"""

# Validate weights sum to 1.0 at import time
assert abs(sum(COMPOSITE_WEIGHT_TABLE.values()) - 1.0) < 1e-9, \
    "COMPOSITE_WEIGHT_TABLE weights do not sum to 1.0"

HEALTH_BAND_THRESHOLDS: dict[ProjectHealthBand, tuple[float, float]] = {
    ProjectHealthBand.CRITICAL:    (0.00, 0.30),
    ProjectHealthBand.POOR:        (0.30, 0.50),
    ProjectHealthBand.ACCEPTABLE:  (0.50, 0.70),
    ProjectHealthBand.GOOD:        (0.70, 0.85),
    ProjectHealthBand.EXCELLENT:   (0.85, 1.01),
}
"""Composite-score intervals defining each ProjectHealthBand.

Intervals are half-open: ``[lo, hi)``, except EXCELLENT which covers up to 1.0.
"""

# Metric normalisation ranges used when converting raw values to [0,1]
_METRIC_NORM_RANGE: dict[ProjectMetricKind, tuple[float, float]] = {
    ProjectMetricKind.THEOREM_COVERAGE:       (0.0, 1.0),
    ProjectMetricKind.PROOF_DEPTH_MAX:        (0.0, 20.0),
    ProjectMetricKind.PROOF_DEPTH_MEAN:       (0.0, 15.0),
    ProjectMetricKind.FEDERATION_DENSITY:     (0.0, 1.0),
    ProjectMetricKind.OBSTRUCTION_REDUCTION:  (-1.0, 1.0),
    ProjectMetricKind.REVISION_STABILITY:     (0.0, 1.0),
    ProjectMetricKind.TOTAL_VERIFIED_THEOREMS: (0.0, 500.0),
    ProjectMetricKind.PACK_SPAN:              (0.0, 50.0),
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectMetricSample:
    """A single numeric measurement of one project-scale metric dimension.

    ProjectMetricSample is the atomic unit of project-scale measurement.
    Multiple samples of the same kind for the same project are collected
    over time and aggregated by the analyzer to produce a ProjectScorecard.

    The ``unit`` field records the measurement unit as a free-form string
    (e.g., ``"fraction"``, ``"count"``, ``"dimensionless"``).  It is
    informational and does not affect computation.

    The ``sample_id`` is auto-generated if not provided; callers may
    supply a deterministic ID for reproducible test scenarios.

    Attributes:
        project_id:  Identifies the project this sample belongs to.
        kind:        Which project-scale metric dimension was measured.
        value:       The numeric value of the measurement.
        unit:        Human-readable unit string.
        measured_at: UTC datetime when the measurement was taken.
        sample_id:   Unique identifier for this sample.
    """

    project_id: str
    kind: ProjectMetricKind
    value: float
    unit: str
    measured_at: datetime
    sample_id: str


@dataclass(frozen=True, slots=True)
class ProjectScorecard:
    """Aggregated quality report for a single research project.

    A ProjectScorecard is produced by the analyzer after collecting one
    sample per metric kind.  It stores the raw samples alongside derived
    artefacts: the health band classification, the composite score, and
    any anomaly flags raised during analysis.

    The ``flags`` tuple contains short human-readable strings identifying
    specific concerns (e.g., ``"low_theorem_coverage"``,
    ``"negative_obstruction_trend"``).  An empty tuple means no anomalies
    were found.

    The ``composite_score`` lies in [0, 1] and is computed as the
    weighted mean of normalised metric values using COMPOSITE_WEIGHT_TABLE.

    Attributes:
        project_id:       Identifies the project.
        samples:          All metric samples contributing to this scorecard.
        health_band:      The health classification assigned by the analyzer.
        composite_score:  Weighted composite quality score in [0, 1].
        flags:            Tuple of anomaly flag strings.
        generated_at:     UTC datetime when the scorecard was created.
    """

    project_id: str
    samples: tuple[ProjectMetricSample, ...]
    health_band: ProjectHealthBand
    composite_score: float
    flags: tuple[str, ...]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class ProjectScaleMetricsAnalyzer:
    """Computes, classifies, and validates project-scale metrics.

    The analyzer is the computational core of the project-scale metrics
    subsystem.  It is stateless: all methods are pure functions of their
    inputs.  Stateful accumulation is handled by the Coordinator and
    Witness.

    Workflow::

        analyzer = ProjectScaleMetricsAnalyzer()
        samples  = [analyzer.sample_metric(pid, kind) for kind in ProjectMetricKind]
        score    = analyzer.compute_composite(samples)
        band     = analyzer.classify_health(score)
        flags    = analyzer.flag_anomalies(samples)
    """

    def sample_metric(
        self,
        project_id: str,
        kind: ProjectMetricKind,
        value: Optional[float] = None,
    ) -> ProjectMetricSample:
        """Create a ProjectMetricSample for the given project and metric kind.

        If *value* is None, a synthetic value is generated from a
        deterministic hash of the project_id and kind, suitable for
        testing purposes.  In production, callers supply the actual
        measured value.

        Args:
            project_id: Identifier of the project being measured.
            kind:       The metric dimension to sample.
            value:      Measured value, or None to use a synthetic value.

        Returns:
            A new ProjectMetricSample with a fresh sample_id and current
            UTC timestamp.

        Raises:
            ValueError: If *project_id* is empty.

        Example:
            >>> s = analyzer.sample_metric("proj_001", ProjectMetricKind.THEOREM_COVERAGE, 0.82)
            >>> s.value
            0.82
        """
        if not project_id:
            raise ValueError("sample_metric: project_id must not be empty")

        # copilot: generate synthetic value from hash when value is None
        if value is None:
            seed = hash((project_id, kind.value)) % 10000
            lo, hi = _METRIC_NORM_RANGE[kind]
            synthetic = lo + (seed / 10000.0) * (hi - lo)
            value = round(synthetic, 4)

        unit_map: dict[ProjectMetricKind, str] = {
            ProjectMetricKind.THEOREM_COVERAGE:       "fraction",
            ProjectMetricKind.PROOF_DEPTH_MAX:        "steps",
            ProjectMetricKind.PROOF_DEPTH_MEAN:       "steps",
            ProjectMetricKind.FEDERATION_DENSITY:     "fraction",
            ProjectMetricKind.OBSTRUCTION_REDUCTION:  "delta",
            ProjectMetricKind.REVISION_STABILITY:     "fraction",
            ProjectMetricKind.TOTAL_VERIFIED_THEOREMS: "count",
            ProjectMetricKind.PACK_SPAN:              "count",
        }

        return ProjectMetricSample(
            project_id=project_id,
            kind=kind,
            value=value,
            unit=unit_map.get(kind, "dimensionless"),
            measured_at=_utcnow(),
            sample_id=_uid(),
        )

    # ------------------------------------------------------------------
    def compute_composite(self, samples: list[ProjectMetricSample]) -> float:
        """Compute the weighted composite score from a list of metric samples.

        Each sample's value is first normalised to [0, 1] using
        ``_METRIC_NORM_RANGE``, then combined as a weighted sum using
        ``COMPOSITE_WEIGHT_TABLE``.  Missing kinds receive a score of 0.0
        and their weight is redistributed proportionally to present kinds.

        Args:
            samples: List of ProjectMetricSample objects.  May contain
                     multiple samples of the same kind; only the most
                     recent is used per kind.

        Returns:
            Composite score in [0, 1].

        Raises:
            ValueError: If *samples* is empty.

        Example:
            >>> score = analyzer.compute_composite(my_samples)
            >>> 0.0 <= score <= 1.0
            True
        """
        if not samples:
            raise ValueError("compute_composite: samples list is empty")

        # copilot: keep only the most recently measured sample per kind
        latest_per_kind: dict[ProjectMetricKind, ProjectMetricSample] = {}
        for s in sorted(samples, key=lambda x: x.measured_at):
            latest_per_kind[s.kind] = s

        normalised: dict[ProjectMetricKind, float] = {}
        for kind, sample in latest_per_kind.items():
            lo, hi = _METRIC_NORM_RANGE.get(kind, (0.0, 1.0))
            normalised[kind] = _normalise_to_unit(sample.value, lo, hi)

        # copilot: compute weighted sum, redistributing absent-kind weights
        present_weight = sum(COMPOSITE_WEIGHT_TABLE.get(k, 0.0) for k in normalised)
        if present_weight <= 0.0:
            return 0.0

        raw_weighted = sum(
            normalised[k] * COMPOSITE_WEIGHT_TABLE.get(k, 0.0) for k in normalised
        )
        # Normalise by present weight to handle missing kinds gracefully
        return _clamp(raw_weighted / present_weight, 0.0, 1.0)

    # ------------------------------------------------------------------
    def classify_health(self, composite: float) -> ProjectHealthBand:
        """Map a composite score to a ProjectHealthBand.

        Uses the half-open intervals defined in HEALTH_BAND_THRESHOLDS.
        The lookup is linear over five bands and returns CRITICAL for any
        score below 0.0 (which can arise from negative obstruction
        reduction values).

        Args:
            composite: Composite score, typically in [0, 1].

        Returns:
            The matching ProjectHealthBand.

        Raises:
            ValueError: If *composite* is NaN.

        Example:
            >>> analyzer.classify_health(0.75)
            <ProjectHealthBand.GOOD: 'good'>
        """
        if math.isnan(composite):
            raise ValueError("classify_health: composite score is NaN")

        # copilot: iterate bands in ascending order to find the right interval
        for band in [
            ProjectHealthBand.CRITICAL,
            ProjectHealthBand.POOR,
            ProjectHealthBand.ACCEPTABLE,
            ProjectHealthBand.GOOD,
            ProjectHealthBand.EXCELLENT,
        ]:
            lo, hi = HEALTH_BAND_THRESHOLDS[band]
            if lo <= composite < hi:
                return band

        # Clamp extremely high values to EXCELLENT
        return ProjectHealthBand.EXCELLENT

    # ------------------------------------------------------------------
    def flag_anomalies(self, samples: list[ProjectMetricSample]) -> list[str]:
        """Identify anomalous or concerning metric values in a sample set.

        Three classes of anomalies are detected:

        1. **Coverage crisis**: THEOREM_COVERAGE below _COVERAGE_POOR_THRESHOLD.
        2. **Obstruction regression**: OBSTRUCTION_REDUCTION is negative.
        3. **Stability failure**: REVISION_STABILITY implies > _STABILITY_REVISION_LIMIT revisions.

        In addition, any sample whose z-score within its kind exceeds
        _ANOMALY_Z_THRESHOLD is flagged as a statistical outlier if multiple
        samples of the same kind are present.

        Args:
            samples: List of ProjectMetricSample objects to inspect.

        Returns:
            A list of human-readable flag strings.  Empty list if no anomalies.

        Raises:
            ValueError: If *samples* is empty.

        Example:
            >>> flags = analyzer.flag_anomalies(samples)
            >>> isinstance(flags, list)
            True
        """
        if not samples:
            raise ValueError("flag_anomalies: samples list is empty")

        flags: list[str] = []

        # copilot: index samples by kind for quick access
        by_kind: dict[ProjectMetricKind, list[float]] = {}
        for s in samples:
            by_kind.setdefault(s.kind, []).append(s.value)

        # Coverage crisis check
        cov_vals = by_kind.get(ProjectMetricKind.THEOREM_COVERAGE, [])
        if cov_vals:
            latest_cov = cov_vals[-1]
            if latest_cov < _COVERAGE_POOR_THRESHOLD:
                flags.append(
                    f"low_theorem_coverage: {latest_cov:.2%} < "
                    f"{_COVERAGE_POOR_THRESHOLD:.0%} threshold"
                )

        # Obstruction regression check
        obs_vals = by_kind.get(ProjectMetricKind.OBSTRUCTION_REDUCTION, [])
        if obs_vals and obs_vals[-1] < 0.0:
            flags.append(
                f"negative_obstruction_reduction: {obs_vals[-1]:.4f} "
                "— project introduced more obstructions than it resolved"
            )

        # Stability check — reverse-engineer revision count
        stab_vals = by_kind.get(ProjectMetricKind.REVISION_STABILITY, [])
        if stab_vals:
            stab = _clamp(stab_vals[-1], 1e-6, 1.0)
            implied_revisions = int(round((1.0 / stab) - 1.0))
            if implied_revisions > _STABILITY_REVISION_LIMIT:
                flags.append(
                    f"revision_instability: implied {implied_revisions} revisions "
                    f"> limit {_STABILITY_REVISION_LIMIT}"
                )

        # Statistical outlier check within each kind
        for kind, vals in by_kind.items():
            if len(vals) >= 3:
                m = statistics.mean(vals)
                sd = statistics.stdev(vals)
                if sd > 0:
                    for v in vals:
                        z = (v - m) / sd
                        if abs(z) > _ANOMALY_Z_THRESHOLD:
                            flags.append(
                                f"outlier_{kind.value}: value={v:.4f} z={z:.2f}"
                            )

        return flags


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ProjectScaleMetricsCoordinator:
    """Orchestrates project-scale metric collection and scorecard generation.

    The coordinator ties together the analyzer, witness, and sample
    collection logic.  Its main entry point is ``evaluate``, which
    gathers one sample per metric kind and returns a full ProjectScorecard.
    The ``compare`` and ``trend`` methods support multi-project analysis
    and longitudinal tracking respectively.

    Attributes:
        _analyzer: The ProjectScaleMetricsAnalyzer used by this instance.
        _witness:  The ProjectScaleMetricsWitness receiving scorecard observations.
        _cache:    Internal dict mapping project_id to list of scorecards.
    """

    def __init__(
        self,
        analyzer: Optional[ProjectScaleMetricsAnalyzer] = None,
        witness: Optional["ProjectScaleMetricsWitness"] = None,
    ) -> None:
        """Initialise coordinator with optional analyzer and witness.

        Args:
            analyzer: Pre-built analyzer, or None for a default instance.
            witness:  Pre-built witness, or None for a default instance.

        Example:
            >>> coord = ProjectScaleMetricsCoordinator()
        """
        self._analyzer: ProjectScaleMetricsAnalyzer = analyzer or ProjectScaleMetricsAnalyzer()
        self._witness: ProjectScaleMetricsWitness = witness or ProjectScaleMetricsWitness()
        self._cache: dict[str, list[ProjectScorecard]] = {}

    # ------------------------------------------------------------------
    def evaluate(
        self,
        project_id: str,
        overrides: Optional[dict[ProjectMetricKind, float]] = None,
    ) -> ProjectScorecard:
        """Measure all metric kinds for *project_id* and return a scorecard.

        If *overrides* is provided, those values are used instead of
        synthetic ones, enabling reproducible evaluation in tests.

        Args:
            project_id: Identifier of the project to evaluate.
            overrides:  Optional dict mapping kinds to measured values.

        Returns:
            A freshly generated ProjectScorecard.

        Raises:
            ValueError: If *project_id* is empty.

        Example:
            >>> sc = coord.evaluate("my_project")
            >>> 0.0 <= sc.composite_score <= 1.0
            True
        """
        if not project_id:
            raise ValueError("evaluate: project_id must not be empty")

        overrides = overrides or {}
        samples: list[ProjectMetricSample] = []

        # copilot: collect one sample per metric kind
        for kind in ProjectMetricKind:
            val = overrides.get(kind)
            s = self._analyzer.sample_metric(project_id, kind, val)
            samples.append(s)

        composite = self._analyzer.compute_composite(samples)
        band = self._analyzer.classify_health(composite)
        flags = self._analyzer.flag_anomalies(samples)

        scorecard = ProjectScorecard(
            project_id=project_id,
            samples=tuple(samples),
            health_band=band,
            composite_score=round(composite, 6),
            flags=tuple(flags),
            generated_at=_utcnow(),
        )

        self._cache.setdefault(project_id, []).append(scorecard)
        self._witness.observe(scorecard)
        return scorecard

    # ------------------------------------------------------------------
    def compare(self, project_ids: list[str]) -> dict:
        """Compare multiple projects by evaluating each and returning a summary.

        Each project is evaluated using synthetic data unless a prior
        evaluation exists in the cache (the most recent cached scorecard
        is used when available).

        Args:
            project_ids: List of project identifiers to compare.

        Returns:
            A dict mapping project_id to a summary with keys
            ``composite_score``, ``health_band``, ``flags``.

        Raises:
            ValueError: If *project_ids* is empty.

        Example:
            >>> cmp = coord.compare(["p1", "p2", "p3"])
            >>> "p1" in cmp
            True
        """
        if not project_ids:
            raise ValueError("compare: project_ids must not be empty")

        comparison: dict[str, dict] = {}
        for pid in project_ids:
            if pid in self._cache and self._cache[pid]:
                sc = self._cache[pid][-1]
            else:
                sc = self.evaluate(pid)
            comparison[pid] = {
                "composite_score": sc.composite_score,
                "health_band": sc.health_band.value,
                "flags": list(sc.flags),
            }
        return comparison

    # ------------------------------------------------------------------
    def trend(
        self,
        project_id: str,
        history: list[ProjectScorecard],
    ) -> dict:
        """Analyse the longitudinal trend of a project's composite score.

        Requires at least _MIN_SAMPLES_FOR_TREND historical scorecards to
        compute a meaningful trend.  For shorter histories a flat trend
        is reported.

        Args:
            project_id: Identifier for labelling purposes.
            history:    Time-ordered list of ProjectScorecard objects.

        Returns:
            A dict with keys ``project_id``, ``n_points``, ``slope``,
            ``first_score``, ``last_score``, ``direction``.

        Raises:
            ValueError: If *history* is empty.

        Example:
            >>> t = coord.trend("p1", [sc1, sc2, sc3])
            >>> t["direction"] in ("improving", "declining", "stable")
            True
        """
        if not history:
            raise ValueError("trend: history must not be empty")

        scores = [sc.composite_score for sc in history]
        slope = _trend_slope(scores)
        direction = "improving" if slope > 0.005 else ("declining" if slope < -0.005 else "stable")

        return {
            "project_id": project_id,
            "n_points": len(scores),
            "slope": round(slope, 6),
            "first_score": round(scores[0], 4),
            "last_score": round(scores[-1], 4),
            "direction": direction,
        }

    # ------------------------------------------------------------------
    def status_report(self, project_id: str) -> dict:
        """Return a status report for *project_id* based on cached data.

        If no cached scorecard exists, a fresh one is generated.

        Args:
            project_id: Identifier of the project.

        Returns:
            Dict with keys ``project_id``, ``latest_composite``,
            ``health_band``, ``n_evaluations``, ``flags``.

        Example:
            >>> rep = coord.status_report("p1")
            >>> "health_band" in rep
            True
        """
        if project_id not in self._cache or not self._cache[project_id]:
            sc = self.evaluate(project_id)
        else:
            sc = self._cache[project_id][-1]

        return {
            "project_id": project_id,
            "latest_composite": sc.composite_score,
            "health_band": sc.health_band.value,
            "n_evaluations": len(self._cache.get(project_id, [])),
            "flags": list(sc.flags),
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class ProjectScaleMetricsWitness:
    """Observes and stores all project scorecards for audit and analysis.

    The witness receives a ProjectScorecard each time the coordinator
    completes an evaluation.  It supports post-hoc queries: which
    projects are in a critical state, what is the distribution of
    composite scores, and retrieval of the full log.

    Attributes:
        _log: Ordered list of all observed ProjectScorecard objects.
    """

    def __init__(self) -> None:
        """Initialise an empty witness log.

        Example:
            >>> w = ProjectScaleMetricsWitness()
            >>> w.all_scorecards()
            []
        """
        self._log: list[ProjectScorecard] = []

    # ------------------------------------------------------------------
    def observe(self, scorecard: ProjectScorecard) -> None:
        """Append a ProjectScorecard to the witness log.

        Args:
            scorecard: The scorecard to record.

        Returns:
            None.

        Raises:
            TypeError: If *scorecard* is not a ProjectScorecard.

        Example:
            >>> w.observe(sc)
        """
        if not isinstance(scorecard, ProjectScorecard):
            raise TypeError(
                f"observe: expected ProjectScorecard, got {type(scorecard).__name__}"
            )
        self._log.append(scorecard)

    # ------------------------------------------------------------------
    def critical_projects(self) -> list[str]:
        """Return project_ids whose most recent scorecard is in the CRITICAL band.

        Returns:
            List of project_id strings for CRITICAL projects (deduped).

        Example:
            >>> crit = w.critical_projects()
        """
        # copilot: keep only the latest scorecard per project for this query
        latest: dict[str, ProjectScorecard] = {}
        for sc in self._log:
            latest[sc.project_id] = sc
        return [
            pid for pid, sc in latest.items()
            if sc.health_band == ProjectHealthBand.CRITICAL
        ]

    # ------------------------------------------------------------------
    def composite_distribution(self) -> dict:
        """Return a distribution summary of all observed composite scores.

        Returns:
            Dict with keys ``count``, ``mean``, ``stdev``, ``min``, ``max``,
            ``band_counts`` (mapping health band names to counts).

        Example:
            >>> dist = w.composite_distribution()
            >>> "mean" in dist
            True
        """
        if not self._log:
            return {"count": 0, "mean": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0, "band_counts": {}}

        scores = [sc.composite_score for sc in self._log]
        band_counts: dict[str, int] = {}
        for sc in self._log:
            band_counts[sc.health_band.value] = band_counts.get(sc.health_band.value, 0) + 1

        return {
            "count": len(scores),
            "mean": round(statistics.mean(scores), 4),
            "stdev": round(statistics.stdev(scores) if len(scores) > 1 else 0.0, 4),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
            "band_counts": band_counts,
        }

    # ------------------------------------------------------------------
    def all_scorecards(self) -> list[ProjectScorecard]:
        """Return a copy of all observed scorecards in observation order.

        Returns:
            List of ProjectScorecard objects.

        Example:
            >>> all_sc = w.all_scorecards()
        """
        return list(self._log)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== ProjectScaleMetrics smoke test ===")

    _analyzer = ProjectScaleMetricsAnalyzer()
    _witness = ProjectScaleMetricsWitness()
    _coord = ProjectScaleMetricsCoordinator(analyzer=_analyzer, witness=_witness)

    # copilot: evaluate several synthetic projects
    _project_ids = ["jugeo_alpha", "jugeo_beta", "jugeo_gamma", "jugeo_delta"]

    for _pid in _project_ids:
        _sc = _coord.evaluate(_pid)
        print(
            f"  {_pid}: composite={_sc.composite_score:.4f}, "
            f"band={_sc.health_band.value}, flags={list(_sc.flags)[:2]}"
        )

    # Compare all projects
    _cmp = _coord.compare(_project_ids)
    print(f"\n  Comparison (health bands): "
          f"{ {k: v['health_band'] for k, v in _cmp.items()} }")

    # Trend analysis with synthetic history
    _history = [_coord.evaluate("jugeo_alpha") for _ in range(5)]
    _trend = _coord.trend("jugeo_alpha", _history)
    print(f"\n  Trend for jugeo_alpha: {_trend}")

    # Witness queries
    _dist = _witness.composite_distribution()
    print(f"\n  Composite distribution: {_dist}")
    _crit = _witness.critical_projects()
    print(f"  Critical projects: {_crit}")

    # Status report
    _rep = _coord.status_report("jugeo_beta")
    print(f"  Status report: {_rep}")

    print("=== Smoke test PASSED ===")
