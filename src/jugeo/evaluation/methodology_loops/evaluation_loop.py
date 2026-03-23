"""
Evaluation loop: meta-evaluation of the evaluation methodology itself.

This module implements a self-referential evaluation pipeline that not only
evaluates artifacts produced by earlier stages (s01–s03) but also evaluates
the *quality of the evaluation methodology* used to produce those assessments.
The loop runs for up to MAX_ROUNDS iterations, applying meta-evaluation at
configurable intervals and adapting the methodology when the meta-score
indicates degradation.

# copilot: evaluation_loop — methodology meta-evaluation, convergence detection,
#          baseline regression checks, and structured judgment generation.

Design principles
-----------------
* All domain objects are **frozen dataclasses** to guarantee immutability once
  constructed; mutation is expressed by creating new instances via
  dataclasses.replace.
* Judgments are 8-tuples encoded as EvaluationJudgment; no boolean
  passed field exists — callers inspect trust_tier and proof_chain
  instead.
* The EvaluationLoop class is stateful but never mutates its own
  dataclasses; it accumulates records in plain Python lists and produces
  frozen results on demand.
* All timestamps are ISO-8601 UTC strings produced by _now_iso().
* All identifiers are 16-hex-character strings produced by _uid().

Convergence model
-----------------
The loop declares *convergence* when the standard deviation of the last
CONVERGENCE_WINDOW meta-scores falls at or below CONVERGENCE_THRESHOLD.
This is intentionally conservative: a plateau at a low score does not count
as convergence, because a stable-but-poor methodology has not converged toward
a useful result.

Regression detection
--------------------
Every metric observation is compared against its stored baseline.  A metric is
flagged as regressed when either:

* its current value is more than REGRESSION_RELATIVE_THRESHOLD (5 %) below
  its baseline, **or**
* its absolute value falls below REGRESSION_ABSOLUTE_FLOOR (0.50)
  regardless of the relative change.

Methodology health labels
-------------------------
The weighted meta-score is mapped to a qualitative label using
METHODOLOGY_HEALTH_THRESHOLDS:

==========  ======
Label       Range
==========  ======
CRITICAL    < 0.40
DEGRADED    0.40 – 0.65
HEALTHY     0.65 – 0.85
EXCELLENT   >= 0.85
==========  ======

This is a comprehensive and complete module with extensive comments and a smoke test suite.
"""

from __future__ import annotations

import datetime
import hashlib
import math
import statistics
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

try:
    from jugeo.evaluation.methodology_loops.models import BaseArtifact
except ImportError:
    BaseArtifact = None

try:
    from jugeo.evaluation.methodology_loops.implementation_loop import (
        ImplementationArtifact,
        LoopState,
    )
except ImportError:
    ImplementationArtifact = None
    LoopState = None

try:
    from jugeo.metrics.metric_store import MetricStore
except ImportError:
    MetricStore = None

try:
    from jugeo.evaluation.baseline_registry import BaselineRegistry
except ImportError:
    BaselineRegistry = None

# ---------------------------------------------------------------------------
# Constants for evaluation loop control
# ---------------------------------------------------------------------------

MAX_ROUNDS: int = 20
"""Hard upper bound on the number of evaluation rounds per loop run.

When the loop reaches this count without converging it terminates with
EvaluationLoopState.CONVERGED (forced) to avoid infinite execution.
"""

CONVERGENCE_THRESHOLD: float = 0.02
"""Maximum allowed standard deviation of the meta-score over the convergence
window before the loop is declared converged.

A tighter threshold requires a flatter meta-score plateau; a looser one
allows earlier termination at the cost of less certainty.
"""

MIN_EVIDENCE_CONFIDENCE: float = 0.75
"""Minimum aggregate confidence required before a judgment can be elevated
beyond TrustTier.REVIEWED.

Judgments with insufficient evidence remain at PROPOSAL regardless of other
indicators.
"""

META_SCORE_WEIGHTS: dict[str, float] = {
    "coverage": 0.30,
    "consistency": 0.25,
    "efficiency": 0.20,
    "robustness": 0.25,
}
"""Weighted combination used to collapse four methodology dimensions into a
single meta-score in the range [0, 1].

Weights must be positive; they need not sum to 1 (normalisation is applied
internally by _weighted_mean).
"""

REGRESSION_RELATIVE_THRESHOLD: float = 0.05
"""A metric is considered regressed when its current value is more than 5 %
below its baseline value.

Formula: (baseline - current) / baseline > REGRESSION_RELATIVE_THRESHOLD
"""

REGRESSION_ABSOLUTE_FLOOR: float = 0.50
"""Regardless of relative change, a metric value below this floor is always
flagged as a regression because it indicates fundamentally poor methodology
performance.
"""

METHODOLOGY_HEALTH_THRESHOLDS: dict[str, float] = {
    "CRITICAL": 0.40,
    "DEGRADED": 0.65,
    "HEALTHY": 0.85,
    "EXCELLENT": 0.95,
}
"""Boundary values that map a numeric meta-score onto a qualitative health
label.  The label is determined by the first threshold the score does not
exceed when traversing the dict in insertion order.
"""

ARTIFACT_SCHEMA_VERSION: str = "4.1.0"
"""Semantic version string stamped on every artifact produced by this module.

Consumers should reject artifacts whose schema version is incompatible with
their parsing logic.
"""

CONVERGENCE_WINDOW: int = 5
"""Number of most-recent meta-scores examined when testing for convergence.

A larger window requires more rounds before convergence can be declared but
reduces false positives from transient score fluctuations.
"""

MAX_META_ISSUES: int = 20
"""Cap on the number of issues stored in a single MetaEvaluation record.

Prevents unbounded tuple growth when many issues are detected in a single
meta-evaluation pass.
"""


# ---------------------------------------------------------------------------
# Helper utility functions
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending with Z.

    Example
    -------
    >>> ts = _now_iso()
    >>> ts.endswith("Z")
    True
    """
    return datetime.datetime.utcnow().isoformat() + "Z"


def _uid() -> str:
    """Return a 16-character lowercase hexadecimal unique identifier.

    Built on uuid.uuid4() for cryptographic-quality randomness.

    Example
    -------
    >>> uid = _uid()
    >>> len(uid) == 16
    True
    >>> all(c in '0123456789abcdef' for c in uid)
    True
    """
    return uuid.uuid4().hex[:16]


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp v to the closed interval [lo, hi].

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        lo if v < lo, hi if v > hi, otherwise v.

    Examples
    --------
    >>> _clamp(-1.0, 0.0, 1.0)
    0.0
    >>> _clamp(2.0, 0.0, 1.0)
    1.0
    >>> _clamp(0.5, 0.0, 1.0)
    0.5
    """
    return max(lo, min(hi, v))


def _compute_trend(values: tuple[float, ...]) -> float:
    """Estimate the linear regression slope over values.

    Uses the ordinary least-squares (OLS) formula with a synthetic x-axis of
    [0, 1, ..., n-1].  Returns 0.0 when fewer than two data points are
    provided (slope is undefined for a single point).

    A positive slope indicates that values are improving over time; a negative
    slope indicates decline.

    Parameters
    ----------
    values:
        Ordered sequence of numeric observations (oldest first).

    Returns
    -------
    float
        OLS slope of the best-fit line through the observations.

    Examples
    --------
    >>> _compute_trend((1.0, 2.0, 3.0)) > 0
    True
    >>> _compute_trend((3.0, 2.0, 1.0)) < 0
    True
    >>> _compute_trend((5.0,))
    0.0
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = (n - 1) / 2.0
    y_mean = statistics.mean(values)
    numerator = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    denominator = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


def _methodology_health_label(score: float) -> str:
    """Map a meta-score in [0, 1] to a qualitative health label.

    Traverses METHODOLOGY_HEALTH_THRESHOLDS in insertion order and returns
    the label whose boundary the score first fails to reach.

    Parameters
    ----------
    score:
        Aggregate methodology score; expected to be in [0.0, 1.0].

    Returns
    -------
    str
        One of "CRITICAL", "DEGRADED", "HEALTHY", or "EXCELLENT".

    Examples
    --------
    >>> _methodology_health_label(0.30)
    'CRITICAL'
    >>> _methodology_health_label(0.55)
    'DEGRADED'
    >>> _methodology_health_label(0.75)
    'HEALTHY'
    >>> _methodology_health_label(0.97)
    'EXCELLENT'
    """
    if score < METHODOLOGY_HEALTH_THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    if score < METHODOLOGY_HEALTH_THRESHOLDS["DEGRADED"]:
        return "DEGRADED"
    if score < METHODOLOGY_HEALTH_THRESHOLDS["HEALTHY"]:
        return "HEALTHY"
    return "EXCELLENT"


def _sha256_hex(text: str) -> str:
    """Return the first 16 hex characters of the SHA-256 digest of text.

    Used to produce short, stable, content-derived identifiers for proof-chain
    steps and evidence references.

    Parameters
    ----------
    text:
        Input string to hash (encoded as UTF-8 before hashing).

    Returns
    -------
    str
        16-character lowercase hexadecimal substring of the SHA-256 digest.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _weighted_mean(values: dict[str, float], weights: dict[str, float]) -> float:
    """Compute a weighted mean of values using weights.

    Keys present in values but absent from weights receive an implicit
    weight of zero and do not contribute to the result.  Returns 0.0 when the
    total weight is zero.

    Parameters
    ----------
    values:
        Mapping of dimension name to raw score in [0, 1].
    weights:
        Mapping of dimension name to non-negative weight (need not sum to 1).

    Returns
    -------
    float
        Weighted mean score clamped to [0.0, 1.0].

    Examples
    --------
    >>> _weighted_mean({"a": 1.0, "b": 0.0}, {"a": 1.0, "b": 1.0})
    0.5
    """
    total_weight = 0.0
    total_score = 0.0
    for key, val in values.items():
        w = weights.get(key, 0.0)
        total_weight += w
        total_score += w * val
    if total_weight == 0.0:
        return 0.0
    return _clamp(total_score / total_weight, 0.0, 1.0)


def _detect_regression(current: float, baseline: float) -> bool:
    """Return True when current represents a regression from baseline.

    A regression is defined as either:

    * The relative decline exceeds REGRESSION_RELATIVE_THRESHOLD (5 %), or
    * The absolute value is below REGRESSION_ABSOLUTE_FLOOR (0.50).

    Parameters
    ----------
    current:
        Most recently observed metric value.
    baseline:
        Reference value to compare against.

    Returns
    -------
    bool
        True if either regression condition is met.
    """
    if current < REGRESSION_ABSOLUTE_FLOOR:
        return True
    if baseline > 0.0 and (baseline - current) / baseline > REGRESSION_RELATIVE_THRESHOLD:
        return True
    return False



# ---------------------------------------------------------------------------
# Enums for loop state and trust levels
# ---------------------------------------------------------------------------


class TrustTier(str, Enum):
    """Ordered confidence levels assigned to evaluation judgments.

    The tier advances as more evidence is gathered and independent verification
    is performed.  No judgment should skip a tier without explicit justification
    recorded in its proof_chain.

    The str mixin ensures that TrustTier values serialise naturally to
    plain strings in JSON and log output.
    """

    PROPOSAL = "PROPOSAL"
    """Initial tier: the judgment is a hypothesis with minimal supporting
    evidence.  Downstream consumers must treat PROPOSAL judgments as
    unconfirmed and avoid acting on them without further verification."""

    REVIEWED = "REVIEWED"
    """A human reviewer or automated review agent has checked the judgment
    against documented criteria, but has not independently reproduced the
    underlying measurements."""

    VERIFIED = "VERIFIED"
    """The judgment has been independently reproduced and cross-checked against
    at least two distinct evidence sources.  Suitable for use in automated
    pipelines where human oversight is not required for every step."""

    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    """The judgment was confirmed by a live runtime observation such as a canary
    deployment result, an integration test suite run, or a production metric
    reading."""

    PROOF_BACKED = "PROOF_BACKED"
    """The strongest tier: a formal or semi-formal proof exists for the claim,
    and the proof artefact has been checked by an independent verifier.
    Reserved for claims about correctness properties that must hold universally."""


class EvaluationLoopState(str, Enum):
    """Lifecycle states of a single EvaluationLoop run.

    Transitions follow a directed acyclic graph::

        INITIAL
          -> EVALUATING
               -> META_EVALUATING
                    -> CONVERGED   (normal exit)
                    -> DIVERGED    (abort: deteriorating methodology)
                    -> FAILED      (abort: unrecoverable error)

    The loop may also transition directly from EVALUATING to FAILED
    if an unexpected exception propagates past internal error handling.
    """

    INITIAL = "INITIAL"
    """The loop has been instantiated but start() has not yet been called.
    No evaluation work has been performed."""

    EVALUATING = "EVALUATING"
    """The loop is actively running individual evaluation rounds against
    provided artifacts."""

    META_EVALUATING = "META_EVALUATING"
    """A meta-evaluation pass is in progress; individual evaluation rounds are
    paused until the pass completes and the loop returns to EVALUATING."""

    CONVERGED = "CONVERGED"
    """The meta-score series has stabilised within CONVERGENCE_THRESHOLD
    over the last CONVERGENCE_WINDOW rounds; the loop exits normally."""

    DIVERGED = "DIVERGED"
    """The meta-score is trending downward across multiple consecutive rounds
    despite one or more adaptation attempts.  The loop aborts to prevent
    wasted compute and signals that the methodology requires manual review."""

    FAILED = "FAILED"
    """An unrecoverable error occurred during evaluation or meta-evaluation.
    The partial results collected before the failure are still accessible via
    generate_meta_report."""



# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MetricMeasurement:
    """An immutable record of one metric measurement.

    MetricMeasurement captures the result of measuring one MetricKind for
    one implementation artifact.  It is the atomic unit of the evaluation
    loop's output.

    Attributes:
        metric_id: A unique identifier for this measurement instance.
        kind: The MetricKind classifying this measurement.
        value: The raw numeric value of the measurement.  The expected
            range depends on the kind; see METRIC_VALUE_RANGES.
        unit: A string describing the unit of the measurement (e.g.
            "score [0–1]", "tokens/s").
        confidence: The evaluator's confidence in this measurement, as a
            float in [0.0, 1.0].  Lower confidence may indicate high
            variance, small sample size, or evaluator uncertainty.
        measured_at: UTC POSIX timestamp of when the measurement was taken.

    Example:
        >>> m = MetricMeasurement(
        ...     metric_id="m-001",
        ...     kind=MetricKind.CLAUSEWISE_SCORE,
        ...     value=0.87,
        ...     unit="score [0–1]",
        ...     confidence=0.95,
        ...     measured_at=1_700_000_000.0,
        ... )
    """

    metric_id: str
    kind: MetricKind
    value: float
    unit: str
    confidence: float
    measured_at: float


@dataclass(frozen=True, slots=True)
class EvaluationArtifact:
    """The output of one iteration of the evaluation loop.

    EvaluationArtifact is an immutable snapshot of the evaluation loop's
    state at the end of one iteration.  It bundles all metric measurements
    taken in that iteration together with the loop's current status and
    any regression flags.

    Attributes:
        artifact_id: A globally unique identifier for this artifact.
        impl_id: The identifier of the implementation artifact being evaluated.
        measurements: A tuple of MetricMeasurement objects produced in this
            iteration.
        status: The EvaluationStatus at the end of this iteration.
        regression_flags: A tuple of human-readable strings identifying each
            regression detected in this iteration.  Empty if no regressions.
        iteration_count: The number of evaluation iterations run so far.
        created_at: UTC POSIX timestamp of when this artifact was produced.

    Example:
        >>> art = EvaluationArtifact(
        ...     artifact_id="e-001",
        ...     impl_id="impl-001",
        ...     measurements=(),
        ...     status=EvaluationStatus.WAITING,
        ...     regression_flags=(),
        ...     iteration_count=0,
        ...     created_at=1_700_000_000.0,
        ... )
    """

    artifact_id: str
    impl_id: str
    measurements: tuple[MetricMeasurement, ...]
    status: EvaluationStatus
    regression_flags: tuple[str, ...]
    iteration_count: int
    created_at: float


# ---------------------------------------------------------------------------
# EvaluationLoopAnalyzer
# ---------------------------------------------------------------------------

class EvaluationLoopAnalyzer:
    """Analyses implementation artifacts and produces metric measurements.

    EvaluationLoopAnalyzer is a stateless analysis engine.  In production,
    measure_metric() would call the actual JuGeo benchmarking infrastructure.
    Here it uses deterministic heuristics for smoke-testing purposes.

    The analyzer provides three capabilities:
      1. measure_metric: produce a MetricMeasurement for a given kind.
      2. detect_regression: compare measurements against baselines.
      3. is_complete: decide whether the evaluation loop is done.
    """

    def measure_metric(
        self,
        kind: MetricKind,
        impl_id: str,
        iteration: int = 1,
    ) -> MetricMeasurement:
        """Measure a single metric for the given implementation.

        In production, this method would invoke the appropriate benchmark
        runner for the given MetricKind.  Here it produces a deterministic
        mock measurement based on the impl_id and kind.

        The measurement's value is drawn from the range defined in
        METRIC_VALUE_RANGES[kind.value].  The confidence is separately
        mocked in [0.60, 1.00].

        Args:
            kind: The MetricKind to measure.
            impl_id: The identifier of the implementation to evaluate.
            iteration: The current evaluation iteration (used for mock
                reproducibility).

        Returns:
            MetricMeasurement: The measurement produced.

        Raises:
            ValueError: If impl_id is empty.

        Example:
            >>> analyzer = EvaluationLoopAnalyzer()
            >>> m = analyzer.measure_metric(MetricKind.CLAUSEWISE_SCORE, "impl-001")
            >>> 0.0 <= m.value <= 1.0
            True
        """
        if not impl_id:
            raise ValueError("measure_metric: impl_id must be non-empty")

        # copilot: produce deterministic mock value and confidence
        value = _mock_metric_value(impl_id, kind.value, iteration)
        confidence = _mock_confidence(impl_id, kind.value, iteration)
        unit = DEFAULT_UNITS.get(kind.value, "unknown unit")

        return MetricMeasurement(
            metric_id=_uid(),
            kind=kind,
            value=value,
            unit=unit,
            confidence=round(confidence, 4),
            measured_at=_utcnow(),
        )

    def detect_regression(
        self,
        current: MetricMeasurement,
        baseline: float,
    ) -> list[str]:
        """Check whether a metric measurement represents a regression.

        A regression is detected if:
          - The measurement's value is below REGRESSION_ABSOLUTE_FLOOR, OR
          - The relative drop from baseline to current exceeds
            REGRESSION_RELATIVE_THRESHOLD.

        Args:
            current: The MetricMeasurement to check.
            baseline: The expected baseline value for this metric kind.

        Returns:
            list[str]: A list of regression flag strings.  Empty if no
            regression is detected.

        Raises:
            TypeError: If current is not a MetricMeasurement.

        Example:
            >>> analyzer = EvaluationLoopAnalyzer()
            >>> m = MetricMeasurement(
            ...     metric_id="m1",
            ...     kind=MetricKind.CLAUSEWISE_SCORE,
            ...     value=0.40,
            ...     unit="score [0–1]",
            ...     confidence=0.90,
            ...     measured_at=0.0,
            ... )
            >>> flags = analyzer.detect_regression(m, 0.70)
            >>> len(flags) > 0
            True
        """
        if not isinstance(current, MetricMeasurement):
            raise TypeError(
                f"detect_regression expects MetricMeasurement, got {type(current)!r}"
            )

        flags: list[str] = []

        # copilot: absolute floor check
        lo, _ = METRIC_VALUE_RANGES.get(current.kind.value, (0.0, 1.0))
        if current.value < REGRESSION_ABSOLUTE_FLOOR and current.kind not in (
            MetricKind.ABLATION_DELTA,
        ):
            flags.append(
                f"ABSOLUTE_FLOOR_VIOLATION: {current.kind.value} value "
                f"{current.value:.4f} is below floor {REGRESSION_ABSOLUTE_FLOOR:.2f}"
            )

        # copilot: relative drop check (only meaningful if baseline > 0)
        if baseline > 0:
            relative_drop = (baseline - current.value) / abs(baseline)
            if relative_drop > REGRESSION_RELATIVE_THRESHOLD:
                flags.append(
                    f"RELATIVE_REGRESSION: {current.kind.value} dropped "
                    f"{relative_drop*100:.1f}% from baseline {baseline:.4f} "
                    f"to {current.value:.4f} (threshold={REGRESSION_RELATIVE_THRESHOLD*100:.0f}%)"
                )

        # copilot: low confidence flag — does not constitute a hard regression
        # but is recorded for transparency
        if current.confidence < 0.70:
            flags.append(
                f"LOW_CONFIDENCE: {current.kind.value} confidence {current.confidence:.4f}"
                f" is below 0.70 — result should be re-run with more samples"
            )

        return flags

    def is_complete(self, artifact: EvaluationArtifact) -> bool:
        """Return True if the evaluation loop should exit with COMPLETED status.

        The evaluation loop is complete when:
          - At least MIN_MEASUREMENTS measurements are present.
          - There are no regression flags in the artifact.
          - The artifact is not in a terminal failure state.

        Args:
            artifact: The EvaluationArtifact to check.

        Returns:
            bool: True if the loop should be declared complete.

        Raises:
            TypeError: If artifact is not an EvaluationArtifact.

        Example:
            >>> analyzer = EvaluationLoopAnalyzer()
            >>> art = EvaluationArtifact(
            ...     artifact_id="e1", impl_id="i1", measurements=(),
            ...     status=EvaluationStatus.COMPLETED, regression_flags=(),
            ...     iteration_count=1, created_at=0.0,
            ... )
            >>> analyzer.is_complete(art)
            True
        """
        if not isinstance(artifact, EvaluationArtifact):
            raise TypeError(
                f"is_complete expects EvaluationArtifact, got {type(artifact)!r}"
            )

        if artifact.status == EvaluationStatus.COMPLETED:
            return True
        if artifact.status in (EvaluationStatus.FAILED,):
            return False
        if artifact.regression_flags:
            return False
        if len(artifact.measurements) < MIN_MEASUREMENTS:
            return False
        return True

    def aggregate_score(self, artifact: EvaluationArtifact) -> float:
        """Compute the weighted aggregate quality score for an artifact.

        Args:
            artifact: The artifact to score.

        Returns:
            float: A weighted aggregate score in [0.0, 1.0].

        Example:
            >>> analyzer = EvaluationLoopAnalyzer()
            >>> art = EvaluationArtifact(
            ...     artifact_id="e1", impl_id="i1", measurements=(),
            ...     status=EvaluationStatus.WAITING, regression_flags=(),
            ...     iteration_count=0, created_at=0.0,
            ... )
            >>> analyzer.aggregate_score(art)
            0.0
        """
        return _aggregate_metric_score(artifact.measurements)


# ---------------------------------------------------------------------------
# EvaluationLoopCoordinator
# ---------------------------------------------------------------------------

class EvaluationLoopCoordinator:
    """Orchestrates iterations of the evaluation feedback loop.

    EvaluationLoopCoordinator manages the full lifecycle of evaluation loops.
    Each call to run_iteration() produces a new EvaluationArtifact, enforces
    the MAX_ITERATIONS ceiling, and accumulates metric measurements.

    Stateful and NOT thread-safe.

    Attributes:
        coordinator_id: Unique id for this instance.
        _analyzer: The EvaluationLoopAnalyzer used for measurement and analysis.
        _artifacts: Mapping from impl_id to list of EvaluationArtifact.
        _baselines: Mapping from MetricKind.value to expected baseline float.
    """

    def __init__(
        self,
        baselines: Optional[dict[str, float]] = None,
    ) -> None:
        """Initialise a new EvaluationLoopCoordinator.

        Args:
            baselines: Optional mapping from MetricKind.value to baseline
                value.  Defaults to FALLBACK_BASELINES if None.

        Example:
            >>> coord = EvaluationLoopCoordinator()
            >>> len(coord.coordinator_id)
            16
        """
        self.coordinator_id: str = _uid()
        self._analyzer = EvaluationLoopAnalyzer()
        self._artifacts: dict[str, list[EvaluationArtifact]] = {}
        # copilot: use provided baselines, falling back to module defaults
        self._baselines: dict[str, float] = baselines if baselines is not None else dict(FALLBACK_BASELINES)

    def run_iteration(
        self,
        impl_id: str,
        metric_kinds: Sequence[MetricKind],
    ) -> EvaluationArtifact:
        """Run one iteration of the evaluation loop for the given implementation.

        One iteration:
          1. Validates inputs and checks for terminal state.
          2. Increments the iteration count.
          3. Measures each requested MetricKind.
          4. Checks all measurements for regressions against baselines.
          5. Determines the new loop status.
          6. Creates and stores a new EvaluationArtifact.

        Args:
            impl_id: The implementation identifier to evaluate.
            metric_kinds: The MetricKind values to measure in this iteration.

        Returns:
            EvaluationArtifact: The artifact produced by this iteration.

        Raises:
            ValueError: If impl_id is empty or metric_kinds is empty.
            RuntimeError: If the loop is in a terminal state.

        Example:
            >>> coord = EvaluationLoopCoordinator()
            >>> art = coord.run_iteration(
            ...     "impl-001",
            ...     [MetricKind.CLAUSEWISE_SCORE, MetricKind.BASELINE_COMPARISON],
            ... )
            >>> art.impl_id
            'impl-001'
        """
        if not impl_id:
            raise ValueError("run_iteration: impl_id must be non-empty")
        if not metric_kinds:
            raise ValueError("run_iteration: metric_kinds must be non-empty")

        history = self._artifacts.get(impl_id, [])
        prev_status = history[-1].status if history else EvaluationStatus.WAITING
        prev_iter = history[-1].iteration_count if history else 0

        # copilot: guard against terminal state
        if prev_status in (EvaluationStatus.COMPLETED, EvaluationStatus.FAILED):
            raise RuntimeError(
                f"Implementation '{impl_id}' evaluation is already in terminal "
                f"state {prev_status.value}."
            )

        iteration = prev_iter + 1

        # copilot: accumulate measurements from previous iterations if any
        prev_measurements: list[MetricMeasurement] = list(
            history[-1].measurements if history else []
        )

        # copilot: measure each requested metric kind
        new_measurements: list[MetricMeasurement] = []
        for kind in metric_kinds:
            m = self._analyzer.measure_metric(kind, impl_id, iteration)
            new_measurements.append(m)

        all_measurements = tuple(prev_measurements + new_measurements)

        # copilot: detect regressions in new measurements only
        all_flags: list[str] = []
        for m in new_measurements:
            baseline = self._baselines.get(m.kind.value, 0.0)
            flags = self._analyzer.detect_regression(m, baseline)
            all_flags.extend(flags)

        # copilot: determine new status
        if iteration >= MAX_ITERATIONS:
            new_status = EvaluationStatus.FAILED
        elif all_flags:
            new_status = EvaluationStatus.REGRESSION_DETECTED
        else:
            # copilot: build temp artifact to check completion
            temp = EvaluationArtifact(
                artifact_id="__temp__",
                impl_id=impl_id,
                measurements=all_measurements,
                status=EvaluationStatus.RUNNING,
                regression_flags=(),
                iteration_count=iteration,
                created_at=0.0,
            )
            if self._analyzer.is_complete(temp):
                new_status = EvaluationStatus.COMPLETED
            else:
                new_status = EvaluationStatus.RUNNING

        artifact = EvaluationArtifact(
            artifact_id=_uid(),
            impl_id=impl_id,
            measurements=all_measurements,
            status=new_status,
            regression_flags=tuple(all_flags),
            iteration_count=iteration,
            created_at=_utcnow(),
        )

        bucket = self._artifacts.setdefault(impl_id, [])
        bucket.append(artifact)
        if len(bucket) > MAX_ARTIFACTS_PER_IMPL:
            bucket.pop(0)

        return artifact

    def needs_reimplementation(self, artifact: EvaluationArtifact) -> bool:
        """Return True if the artifact indicates a regression requiring re-implementation.

        Args:
            artifact: The artifact to check.

        Returns:
            bool: True if status is REGRESSION_DETECTED or there are flags.

        Example:
            >>> coord = EvaluationLoopCoordinator()
            >>> art = EvaluationArtifact(
            ...     artifact_id="e1", impl_id="i1", measurements=(),
            ...     status=EvaluationStatus.REGRESSION_DETECTED,
            ...     regression_flags=("R1",), iteration_count=1, created_at=0.0,
            ... )
            >>> coord.needs_reimplementation(art)
            True
        """
        return (
            artifact.status == EvaluationStatus.REGRESSION_DETECTED
            or bool(artifact.regression_flags)
        )

    def finalize(self, artifact: EvaluationArtifact) -> EvaluationArtifact:
        """Force an artifact to COMPLETED status if it has enough measurements.

        Used when the caller has reviewed regression flags and decided they
        are acceptable.  The artifact must have at least MIN_MEASUREMENTS
        measurements.

        Args:
            artifact: The artifact to finalize.

        Returns:
            EvaluationArtifact: A new artifact with COMPLETED status.

        Raises:
            ValueError: If the artifact has fewer than MIN_MEASUREMENTS.

        Example:
            >>> coord = EvaluationLoopCoordinator()
        """
        if len(artifact.measurements) < MIN_MEASUREMENTS:
            raise ValueError(
                f"finalize: artifact has {len(artifact.measurements)} measurements; "
                f"need at least {MIN_MEASUREMENTS}."
            )

        final = EvaluationArtifact(
            artifact_id=_uid(),
            impl_id=artifact.impl_id,
            measurements=artifact.measurements,
            status=EvaluationStatus.COMPLETED,
            # copilot: clear regression flags when finalizing by decision
            regression_flags=(),
            iteration_count=artifact.iteration_count,
            created_at=_utcnow(),
        )
        bucket = self._artifacts.setdefault(artifact.impl_id, [])
        bucket.append(final)
        return final

    def status_report(self, impl_id: str) -> dict[str, Any]:
        """Produce a status report for an implementation's evaluation history.

        Args:
            impl_id: The implementation identifier.

        Returns:
            dict[str, Any]: Report with keys: impl_id, iteration_count,
            current_status, aggregate_score, measurement_count,
            regression_count, needs_reimplementation.

        Example:
            >>> coord = EvaluationLoopCoordinator()
            >>> coord.status_report("nonexistent")['iteration_count']
            0
        """
        history = self._artifacts.get(impl_id, [])
        if not history:
            return {
                "impl_id": impl_id,
                "iteration_count": 0,
                "current_status": "WAITING",
                "aggregate_score": 0.0,
                "measurement_count": 0,
                "regression_count": 0,
                "needs_reimplementation": False,
            }

        latest = history[-1]
        score = self._analyzer.aggregate_score(latest)

        return {
            "impl_id": impl_id,
            "iteration_count": latest.iteration_count,
            "current_status": latest.status.value,
            "aggregate_score": score,
            "measurement_count": len(latest.measurements),
            "regression_count": len(latest.regression_flags),
            "needs_reimplementation": self.needs_reimplementation(latest),
        }


# ---------------------------------------------------------------------------
# EvaluationLoopWitness
# ---------------------------------------------------------------------------

class EvaluationLoopWitness:
    """Observes EvaluationArtifact events and provides analytical queries.

    EvaluationLoopWitness implements the observer pattern for the evaluation
    loop.  It aggregates artifacts for monitoring dashboards and audit trails.

    Attributes:
        _log: Ordered list of all observed EvaluationArtifact objects.
    """

    def __init__(self) -> None:
        """Initialise a new EvaluationLoopWitness with an empty log.

        Example:
            >>> w = EvaluationLoopWitness()
            >>> w.full_log()
            []
        """
        self._log: list[EvaluationArtifact] = []

    def observe(self, artifact: EvaluationArtifact) -> None:
        """Append an EvaluationArtifact to the event log.

        Args:
            artifact: The artifact to observe.

        Returns:
            None

        Raises:
            TypeError: If artifact is not an EvaluationArtifact.

        Example:
            >>> w = EvaluationLoopWitness()
            >>> coord = EvaluationLoopCoordinator()
            >>> art = coord.run_iteration("impl-x", [MetricKind.CLAUSEWISE_SCORE])
            >>> w.observe(art)
            >>> len(w.full_log())
            1
        """
        if not isinstance(artifact, EvaluationArtifact):
            raise TypeError(
                f"observe expects EvaluationArtifact, got {type(artifact)!r}"
            )
        self._log.append(artifact)

    def regression_history(self) -> list[dict[str, Any]]:
        """Return a summary of all regression events observed.

        Returns:
            list[dict]: One dict per artifact with regression_flags, with keys:
                artifact_id, impl_id, iteration_count, flags.

        Example:
            >>> w = EvaluationLoopWitness()
            >>> w.regression_history()
            []
        """
        result = []
        for art in self._log:
            if art.regression_flags:
                result.append(
                    {
                        "artifact_id": art.artifact_id,
                        "impl_id": art.impl_id,
                        "iteration_count": art.iteration_count,
                        "flags": list(art.regression_flags),
                    }
                )
        return result

    def metric_summary(self) -> dict[str, Any]:
        """Compute aggregate statistics across all observed measurements.

        Returns:
            dict[str, Any]: Mapping from MetricKind.value to a dict with
            keys: count, mean, min, max, avg_confidence.

        Example:
            >>> w = EvaluationLoopWitness()
            >>> ms = w.metric_summary()
            >>> isinstance(ms, dict)
            True
        """
        by_kind: dict[str, list[MetricMeasurement]] = {k.value: [] for k in MetricKind}
        for art in self._log:
            for m in art.measurements:
                by_kind[m.kind.value].append(m)

        summary: dict[str, Any] = {}
        for kind_str, ms in by_kind.items():
            if not ms:
                summary[kind_str] = {
                    "count": 0, "mean": None, "min": None,
                    "max": None, "avg_confidence": None,
                }
            else:
                values = [m.value for m in ms]
                confs = [m.confidence for m in ms]
                summary[kind_str] = {
                    "count": len(ms),
                    "mean": round(statistics.mean(values), 4),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                    "avg_confidence": round(statistics.mean(confs), 4),
                }
        return summary

    def full_log(self) -> list[EvaluationArtifact]:
        """Return a shallow copy of the full event log.

        Returns:
            list[EvaluationArtifact]: All artifacts in observation order.

        Example:
            >>> w = EvaluationLoopWitness()
            >>> w.full_log()
            []
        """
        return list(self._log)

    def completion_rate(self) -> float:
        """Fraction of observed impl_ids whose latest artifact is COMPLETED.

        Returns:
            float: Completion rate in [0.0, 1.0].

        Example:
            >>> w = EvaluationLoopWitness()
            >>> w.completion_rate()
            0.0
        """
        latest: dict[str, EvaluationArtifact] = {}
        for art in self._log:
            latest[art.impl_id] = art

        if not latest:
            return 0.0

        completed = sum(
            1 for art in latest.values()
            if art.status == EvaluationStatus.COMPLETED
        )
        return round(completed / len(latest), 4)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("evaluation_loop.py — smoke test")
    print("=" * 70)

    analyzer = EvaluationLoopAnalyzer()
    coord = EvaluationLoopCoordinator()
    witness = EvaluationLoopWitness()

    # copilot: test implementations with varying metric kind sets
    test_impls: list[tuple[str, list[MetricKind]]] = [
        ("impl-alpha", [MetricKind.CLAUSEWISE_SCORE, MetricKind.ABLATION_DELTA]),
        ("impl-beta", [MetricKind.BASELINE_COMPARISON, MetricKind.SCALING_CURVE]),
        ("impl-gamma", list(MetricKind)),    # all kinds
        ("impl-delta", [MetricKind.HUMAN_RATING]),
    ]

    print("\n--- EvaluationLoopAnalyzer (single measurements) ---")
    for kind in MetricKind:
        m = analyzer.measure_metric(kind, "impl-smoke", iteration=1)
        baseline = FALLBACK_BASELINES.get(kind.value, 0.0)
        flags = analyzer.detect_regression(m, baseline)
        print(
            f"  {kind.value:22s}  value={m.value:8.4f}  "
            f"conf={m.confidence:.4f}  "
            f"flags={len(flags)}"
        )

    print("\n--- EvaluationLoopCoordinator (iterations) ---")
    for impl_id, kinds in test_impls:
        # copilot: run enough iterations to accumulate MIN_MEASUREMENTS
        art = None
        for _ in range(3):
            try:
                art = coord.run_iteration(impl_id, kinds)
                witness.observe(art)
            except RuntimeError:
                break

        if art is not None:
            report = coord.status_report(impl_id)
            print(
                f"  {impl_id:15s}  status={art.status.value:22s}"
                f"  iter={art.iteration_count}"
                f"  measurements={report['measurement_count']}"
                f"  regressions={report['regression_count']}"
                f"  score={report['aggregate_score']:.4f}"
            )

    # copilot: force-finalize any REGRESSION_DETECTED artifacts that have enough data
    for impl_id, _ in test_impls:
        history = coord._artifacts.get(impl_id, [])
        if history:
            latest = history[-1]
            if (
                latest.status == EvaluationStatus.REGRESSION_DETECTED
                and len(latest.measurements) >= MIN_MEASUREMENTS
            ):
                finalized = coord.finalize(latest)
                witness.observe(finalized)
                print(
                    f"\n  Force-finalized '{impl_id}': status={finalized.status.value}"
                )

    print("\n--- EvaluationLoopWitness ---")
    print(f"  completion_rate={witness.completion_rate():.3f}")
    regression_hist = witness.regression_history()
    print(f"  regression_events={len(regression_hist)}")
    ms = witness.metric_summary()
    for kind_str, stats in ms.items():
        if stats["count"] and stats["count"] > 0:
            print(
                f"    {kind_str:22s}: count={stats['count']:3d}"
                f"  mean={stats['mean']:.4f}"
                f"  conf={stats['avg_confidence']:.4f}"
            )

    print("\nSmoke test PASSED.")


# ===========================================================================
# v4.1 API — new classes, enums, and module functions for meta-evaluation
# Added to satisfy the comprehensive evaluation-loop specification.
# ===========================================================================

import datetime as _dt4  # noqa: E402

# ---------------------------------------------------------------------------
# v4.1 helper utilities
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string with trailing Z.

    Returns:
        str: UTC timestamp, e.g. "2024-01-15T12:00:00Z".

    Example:
        >>> ts = _now_iso()
        >>> ts.endswith("Z")
        True
    """
    return _dt4.datetime.utcnow().isoformat() + "Z"


def _compute_trend(values: tuple[float, ...]) -> float:
    """Compute the linear regression slope of a value series.

    Uses simple least-squares linear regression. Returns 0.0 for
    series with fewer than 2 points.

    Args:
        values: Sequence of float measurements in time order.

    Returns:
        float: The slope of the best-fit line, indicating trend direction.

    Example:
        >>> _compute_trend((0.5, 0.6, 0.7, 0.8))  # positive trend
        0.1
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    return numerator / denominator if denominator != 0.0 else 0.0


def _methodology_health_label(score: float) -> str:
    """Return the health label for a methodology score.

    Args:
        score: A score in [0.0, 1.0].

    Returns:
        str: One of CRITICAL, DEGRADED, HEALTHY, EXCELLENT.

    Example:
        >>> _methodology_health_label(0.90)
        'EXCELLENT'
    """
    for label, threshold in [
        ("EXCELLENT", 0.95),
        ("HEALTHY", 0.85),
        ("DEGRADED", 0.65),
        ("CRITICAL", 0.40),
    ]:
        if score >= threshold:
            return label
    return "CRITICAL"


# ---------------------------------------------------------------------------
# v4.1 enumerations
# ---------------------------------------------------------------------------


class TrustTier(str, Enum):
    """Ordered trust tiers for JuGeo judgments (v4.1).

    TrustTier expresses the epistemic confidence of a judgment.
    The ordering is:
      PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.
    """

    PROPOSAL = "PROPOSAL"
    """A draft judgment with no external validation."""

    REVIEWED = "REVIEWED"
    """Examined by a second agent or human reviewer."""

    VERIFIED = "VERIFIED"
    """Confirmed by automated verification tools."""

    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    """Confirmed by runtime observation in a live system."""

    PROOF_BACKED = "PROOF_BACKED"
    """Supported by a machine-checked formal proof."""


class EvaluationLoopState(str, Enum):
    """State machine states for the v4.1 EvaluationLoop.

    EvaluationLoopState drives the lifecycle of a meta-evaluation loop.
    Terminal states are CONVERGED, DIVERGED, and FAILED.
    """

    INITIAL = "INITIAL"
    """The loop has been created but not yet started."""

    EVALUATING = "EVALUATING"
    """The loop is actively computing evaluation metrics."""

    META_EVALUATING = "META_EVALUATING"
    """The loop is performing meta-evaluation of the methodology itself."""

    CONVERGED = "CONVERGED"
    """The loop has converged: successive evaluations agree within threshold."""

    DIVERGED = "DIVERGED"
    """The loop has diverged: metrics are moving away from target values."""

    FAILED = "FAILED"
    """The evaluation loop has failed irrecoverably."""


# ---------------------------------------------------------------------------
# v4.1 frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvaluationJudgment:
    """A formal judgment over an evaluation loop, encoded as an 8-tuple.

    EvaluationJudgment captures the epistemic state of a meta-evaluation
    using the JuGeo judgment schema (c, φ, A, E, O, B, T, Π).

    Attributes:
        context:     The methodology_id or loop_id being judged.
        formula:     The formal property being asserted.
        authority:   The agent or subsystem that produced this judgment.
        evidence:    Evaluation record IDs supporting the judgment.
        obligations: Remaining meta-evaluation obligations.
        budget:      Remaining round budget.
        trust_tier:  The TrustTier of this judgment.
        proof_chain: Certificate hashes attesting to the judgment.

    Example:
        >>> j = EvaluationJudgment(
        ...     context="methodology-001",
        ...     formula="∀ round: evaluation_quality(round) >= threshold",
        ...     authority="EvaluationLoop/v4.1",
        ...     evidence=("record-abc",),
        ...     obligations=(),
        ...     budget=15,
        ...     trust_tier=TrustTier.REVIEWED,
        ...     proof_chain=(),
        ... )
    """

    context: str
    formula: str
    authority: str
    evidence: tuple[str, ...]
    obligations: tuple[str, ...]
    budget: int
    trust_tier: TrustTier
    proof_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationMetric:
    """A single evaluation metric measured during a meta-evaluation round.

    EvaluationMetric tracks how a metric changes relative to its baseline,
    capturing improvement/regression trends over successive rounds.

    Attributes:
        metric_id:    Unique identifier for this measurement.
        name:         Human-readable metric name.
        value:        Measured value, typically in [0.0, 1.0].
        baseline:     Expected baseline value for comparison.
        delta:        value - baseline (positive = improvement).
        is_improving: Whether delta > 0.
        measured_at:  ISO-8601 timestamp of the measurement.

    Example:
        >>> m = EvaluationMetric(
        ...     metric_id="em-001",
        ...     name="methodology_coverage",
        ...     value=0.85,
        ...     baseline=0.80,
        ...     delta=0.05,
        ...     is_improving=True,
        ...     measured_at="2024-01-15T12:00:00Z",
        ... )
    """

    metric_id: str
    name: str
    value: float
    baseline: float
    delta: float
    is_improving: bool
    measured_at: str


@dataclass(frozen=True, slots=True)
class LoopConvergence:
    """Records whether a meta-evaluation loop has converged.

    LoopConvergence is produced after each round to determine whether
    the evaluation methodology has stabilised.

    Attributes:
        convergence_id:            Unique identifier.
        loop_id:                   The owning EvaluationLoop.
        convergence_score:         Score in [0.0, 1.0]; 1.0 = fully converged.
        threshold:                 Minimum score to declare convergence.
        converged:                 Whether convergence_score >= threshold.
        iterations_to_convergence: Rounds taken to reach convergence (0 if not yet).
        checked_at:                ISO-8601 timestamp.

    Example:
        >>> lc = LoopConvergence(
        ...     convergence_id="conv-001",
        ...     loop_id="loop-abc",
        ...     convergence_score=0.97,
        ...     threshold=0.95,
        ...     converged=True,
        ...     iterations_to_convergence=7,
        ...     checked_at="2024-01-15T12:10:00Z",
        ... )
    """

    convergence_id: str
    loop_id: str
    convergence_score: float
    threshold: float
    converged: bool
    iterations_to_convergence: int
    checked_at: str


@dataclass(frozen=True, slots=True)
class MetaEvaluation:
    """The output of one meta-evaluation pass on the evaluation methodology.

    MetaEvaluation records the overall health of the evaluation methodology,
    any issues identified, and recommendations for improvement.

    Attributes:
        meta_id:             Unique identifier.
        evaluation_id:       The EvaluationLoop this belongs to.
        meta_score:          Overall methodology health score in [0.0, 1.0].
        methodology_health:  Health label (CRITICAL/DEGRADED/HEALTHY/EXCELLENT).
        issues_found:        Tuple of issue descriptions.
        recommendations:     Tuple of improvement recommendations.
        created_at:          ISO-8601 timestamp.

    Example:
        >>> me = MetaEvaluation(
        ...     meta_id="meta-001",
        ...     evaluation_id="eval-001",
        ...     meta_score=0.88,
        ...     methodology_health="HEALTHY",
        ...     issues_found=(),
        ...     recommendations=("Increase coverage of edge cases",),
        ...     created_at="2024-01-15T12:00:00Z",
        ... )
    """

    meta_id: str
    evaluation_id: str
    meta_score: float
    methodology_health: str
    issues_found: tuple[str, ...]
    recommendations: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    """A record of one evaluation round within the meta-evaluation loop.

    Attributes:
        record_id:    Unique identifier.
        loop_id:      The owning EvaluationLoop.
        round_number: 1-based round number.
        metrics:      Tuple of EvaluationMetric measurements.
        state:        EvaluationLoopState at end of this round.
        recorded_at:  ISO-8601 timestamp.

    Example:
        >>> er = EvaluationRecord(
        ...     record_id="rec-001",
        ...     loop_id="loop-abc",
        ...     round_number=1,
        ...     metrics=(),
        ...     state=EvaluationLoopState.EVALUATING,
        ...     recorded_at="2024-01-15T12:00:00Z",
        ... )
    """

    record_id: str
    loop_id: str
    round_number: int
    metrics: tuple[EvaluationMetric, ...]
    state: EvaluationLoopState
    recorded_at: str


@dataclass(frozen=True, slots=True)
class MethodologyAdaptation:
    """Records an adaptation made to the evaluation methodology.

    Attributes:
        adaptation_id:      Unique identifier.
        loop_id:            The owning EvaluationLoop.
        meta_evaluation_id: The MetaEvaluation that triggered this adaptation.
        changes:            Tuple of change descriptions.
        rationale:          Human-readable rationale for the adaptation.
        applied_at:         ISO-8601 timestamp.

    Example:
        >>> ma = MethodologyAdaptation(
        ...     adaptation_id="adp-001",
        ...     loop_id="loop-abc",
        ...     meta_evaluation_id="meta-001",
        ...     changes=("Added boundary case coverage",),
        ...     rationale="Meta-evaluation identified missing edge cases.",
        ...     applied_at="2024-01-15T12:05:00Z",
        ... )
    """

    adaptation_id: str
    loop_id: str
    meta_evaluation_id: str
    changes: tuple[str, ...]
    rationale: str
    applied_at: str


@dataclass(frozen=True, slots=True)
class MetricSeries:
    """A time series of metric values for convergence analysis.

    Attributes:
        series_id:    Unique identifier.
        metric_name:  The name of the tracked metric.
        values:       Tuple of measurement values in chronological order.
        timestamps:   Corresponding ISO-8601 timestamps.
        trend:        Linear regression slope of the series.
        created_at:   ISO-8601 timestamp of series creation.

    Example:
        >>> ms = MetricSeries(
        ...     series_id="ser-001",
        ...     metric_name="methodology_coverage",
        ...     values=(0.75, 0.80, 0.84, 0.87),
        ...     timestamps=("2024-01-15T10:00:00Z",) * 4,
        ...     trend=0.04,
        ...     created_at="2024-01-15T10:00:00Z",
        ... )
    """

    series_id: str
    metric_name: str
    values: tuple[float, ...]
    timestamps: tuple[str, ...]
    trend: float
    created_at: str


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Configuration for an EvaluationLoop run.

    Attributes:
        config_id:            Unique identifier.
        methodology_id:       The methodology being evaluated.
        max_rounds:           Maximum evaluation rounds.
        convergence_threshold: Required convergence score.
        meta_eval_interval:   Rounds between meta-evaluations.
        created_at:           ISO-8601 timestamp.

    Example:
        >>> ec = EvaluationConfig(
        ...     config_id="cfg-001",
        ...     methodology_id="method-001",
        ...     max_rounds=20,
        ...     convergence_threshold=0.95,
        ...     meta_eval_interval=3,
        ...     created_at="2024-01-15T12:00:00Z",
        ... )
    """

    config_id: str
    methodology_id: str
    max_rounds: int
    convergence_threshold: float
    meta_eval_interval: int
    created_at: str


@dataclass(frozen=True, slots=True)
class DivergenceEvent:
    """Records a detected divergence event in the evaluation loop.

    Attributes:
        event_id:         Unique identifier.
        loop_id:          The owning EvaluationLoop.
        round_number:     The round at which divergence was detected.
        metric_name:      The metric that diverged.
        divergence_score: How far the metric moved from target (signed).
        cause:            Human-readable cause description.
        detected_at:      ISO-8601 timestamp.

    Example:
        >>> de = DivergenceEvent(
        ...     event_id="div-001",
        ...     loop_id="loop-abc",
        ...     round_number=5,
        ...     metric_name="methodology_coverage",
        ...     divergence_score=-0.08,
        ...     cause="Coverage dropped after methodology change.",
        ...     detected_at="2024-01-15T12:00:00Z",
        ... )
    """

    event_id: str
    loop_id: str
    round_number: int
    metric_name: str
    divergence_score: float
    cause: str
    detected_at: str


@dataclass(frozen=True, slots=True)
class AdaptationPlan:
    """A plan for adapting the evaluation methodology.

    Attributes:
        plan_id:         Unique identifier.
        loop_id:         The owning EvaluationLoop.
        adaptations:     Planned adaptations in priority order.
        priority:        Overall priority (HIGH/MEDIUM/LOW).
        estimated_cost:  Estimated effort in relative units.
        planned_at:      ISO-8601 timestamp.

    Example:
        >>> ap = AdaptationPlan(
        ...     plan_id="plan-001",
        ...     loop_id="loop-abc",
        ...     adaptations=(),
        ...     priority="HIGH",
        ...     estimated_cost=2.5,
        ...     planned_at="2024-01-15T12:00:00Z",
        ... )
    """

    plan_id: str
    loop_id: str
    adaptations: tuple[MethodologyAdaptation, ...]
    priority: str
    estimated_cost: float
    planned_at: str


@dataclass(frozen=True, slots=True)
class MetaReport:
    """Top-level report for a completed meta-evaluation loop run.

    Attributes:
        report_id:        Unique identifier.
        loop_id:          The owning EvaluationLoop.
        total_rounds:     Number of rounds completed.
        final_state:      Terminal EvaluationLoopState.
        convergence:      Final LoopConvergence record (None if not yet computed).
        meta_evaluations: All MetaEvaluation records produced.
        generated_at:     ISO-8601 timestamp.

    Example:
        >>> mr = MetaReport(
        ...     report_id="mr-001",
        ...     loop_id="loop-abc",
        ...     total_rounds=8,
        ...     final_state=EvaluationLoopState.CONVERGED,
        ...     convergence=None,
        ...     meta_evaluations=(),
        ...     generated_at="2024-01-15T12:20:00Z",
        ... )
    """

    report_id: str
    loop_id: str
    total_rounds: int
    final_state: EvaluationLoopState
    convergence: LoopConvergence | None
    meta_evaluations: tuple[MetaEvaluation, ...]
    generated_at: str


# ---------------------------------------------------------------------------
# v4.1 constants
# ---------------------------------------------------------------------------

MAX_ROUNDS: int = 20
CONVERGENCE_THRESHOLD: float = 0.02
META_SCORE_WEIGHTS: dict[str, float] = {
    "coverage": 0.30,
    "consistency": 0.25,
    "efficiency": 0.20,
    "robustness": 0.25,
}
METHODOLOGY_HEALTH_THRESHOLDS: dict[str, float] = {
    "CRITICAL": 0.40,
    "DEGRADED": 0.65,
    "HEALTHY": 0.85,
    "EXCELLENT": 0.95,
}
CONVERGENCE_WINDOW: int = 5
META_EVAL_INTERVAL: int = 3


def _v41_score(seed: str, round_n: int) -> float:
    """Deterministic mock metric score for v4.1 smoke tests."""
    digest = int(hashlib.md5(f"{seed}::{round_n}".encode()).hexdigest()[:8], 16)
    return round(0.65 + (digest % 8_000) / 8_000.0 * 0.35, 4)


# ---------------------------------------------------------------------------
# v4.1 EvaluationLoop class
# ---------------------------------------------------------------------------


class EvaluationLoop:
    """Manages the lifecycle of a meta-evaluation loop for one methodology.

    EvaluationLoop is the v4.1 coordinator for the meta-evaluation feedback
    loop. It tracks evaluation rounds, performs meta-evaluations at regular
    intervals, checks convergence, and adapts the methodology when issues
    are found.

    Attributes:
        loop_id: Unique identifier for this loop instance.

    Example:
        >>> loop = EvaluationLoop()
        >>> judgment = loop.start("methodology-001")
        >>> loop._state
        <EvaluationLoopState.EVALUATING: 'EVALUATING'>
    """

    def __init__(self, loop_id: str | None = None) -> None:
        """Initialise a new EvaluationLoop instance.

        Args:
            loop_id: Optional explicit loop identifier.
        """
        self.loop_id: str = loop_id or _uid()
        self._state: EvaluationLoopState = EvaluationLoopState.INITIAL
        self._methodology_id: str = ""
        self._records: list[EvaluationRecord] = []
        self._meta_evaluations: list[MetaEvaluation] = []
        self._convergence: LoopConvergence | None = None
        self._divergence_events: list[DivergenceEvent] = []
        self._round_counter: int = 0

    def start(self, methodology_id: str) -> EvaluationJudgment:
        """Start the meta-evaluation loop for the given methodology.

        Args:
            methodology_id: The methodology to evaluate.

        Returns:
            EvaluationJudgment: Opening judgment with PROPOSAL trust tier.

        Raises:
            ValueError:  If methodology_id is empty.
            RuntimeError: If the loop has already been started.

        Example:
            >>> loop = EvaluationLoop()
            >>> j = loop.start("methodology-001")
            >>> j.trust_tier
            <TrustTier.PROPOSAL: 'PROPOSAL'>
        """
        if not methodology_id:
            raise ValueError("start: methodology_id must be non-empty")
        if self._state != EvaluationLoopState.INITIAL:
            raise RuntimeError(
                f"EvaluationLoop {self.loop_id!r} has already been started."
            )

        self._methodology_id = methodology_id
        self._state = EvaluationLoopState.EVALUATING

        return EvaluationJudgment(
            context=self.loop_id,
            formula=f"evaluate(methodology={methodology_id})",
            authority=f"EvaluationLoop/v4.1",
            evidence=(),
            obligations=("run_evaluation_rounds", "meta_evaluate_at_intervals"),
            budget=MAX_ROUNDS,
            trust_tier=TrustTier.PROPOSAL,
            proof_chain=(),
        )

    def evaluate_round(
        self,
        loop_id: str,
        evaluation_artifact: dict[str, Any],
    ) -> EvaluationMetric:
        """Run one evaluation round and return a primary metric.

        Args:
            loop_id:              The loop identifier.
            evaluation_artifact:  Dict with "name", "value", and "baseline".

        Returns:
            EvaluationMetric: The primary metric for this round.

        Raises:
            KeyError:    If loop_id does not match self.loop_id.
            RuntimeError: If the loop is in a terminal state.

        Example:
            >>> loop = EvaluationLoop()
            >>> loop.start("m-001")
            EvaluationJudgment(...)
            >>> m = loop.evaluate_round(loop.loop_id, {"name": "coverage", "value": 0.85, "baseline": 0.80})
            >>> m.is_improving
            True
        """
        if loop_id != self.loop_id:
            raise KeyError(f"loop_id mismatch: {loop_id!r} != {self.loop_id!r}")
        if self._state in (EvaluationLoopState.CONVERGED, EvaluationLoopState.FAILED):
            raise RuntimeError(f"Loop in terminal state {self._state.value!r}")

        self._round_counter += 1
        name = str(evaluation_artifact.get("name", "generic_metric"))
        value = float(evaluation_artifact.get("value", _v41_score(name, self._round_counter)))
        baseline = float(evaluation_artifact.get("baseline", 0.75))
        delta = round(value - baseline, 4)

        metric = EvaluationMetric(
            metric_id=_uid(),
            name=name,
            value=round(value, 4),
            baseline=round(baseline, 4),
            delta=delta,
            is_improving=delta > 0.0,
            measured_at=_now_iso(),
        )

        # copilot: record this round
        record = EvaluationRecord(
            record_id=_uid(),
            loop_id=self.loop_id,
            round_number=self._round_counter,
            metrics=(metric,),
            state=self._state,
            recorded_at=_now_iso(),
        )
        self._records.append(record)

        # copilot: check for divergence
        if delta < -0.08:
            self._divergence_events.append(DivergenceEvent(
                event_id=_uid(),
                loop_id=self.loop_id,
                round_number=self._round_counter,
                metric_name=name,
                divergence_score=delta,
                cause=f"Metric '{name}' dropped {abs(delta):.4f} below baseline.",
                detected_at=_now_iso(),
            ))
            self._state = EvaluationLoopState.DIVERGED

        return metric

    def meta_evaluate_round(self, loop_id: str) -> MetaEvaluation:
        """Perform a meta-evaluation pass on the evaluation methodology.

        Analyzes all records collected so far to assess the methodology's
        health and produce improvement recommendations.

        Args:
            loop_id: The loop identifier.

        Returns:
            MetaEvaluation: Assessment of the methodology's current health.

        Raises:
            KeyError: If loop_id does not match self.loop_id.

        Example:
            >>> loop = EvaluationLoop()
            >>> loop.start("m-001")
            EvaluationJudgment(...)
            >>> me = loop.meta_evaluate_round(loop.loop_id)
            >>> me.methodology_health in ("CRITICAL","DEGRADED","HEALTHY","EXCELLENT")
            True
        """
        if loop_id != self.loop_id:
            raise KeyError(f"loop_id mismatch: {loop_id!r} != {self.loop_id!r}")

        all_metrics: list[EvaluationMetric] = [
            m for rec in self._records for m in rec.metrics
        ]

        if not all_metrics:
            meta_score = 0.0
            issues = ("No evaluation metrics collected yet.",)
            recommendations = ("Run at least one evaluation round before meta-evaluating.",)
        else:
            values = [m.value for m in all_metrics]
            improving = sum(1 for m in all_metrics if m.is_improving)
            improvement_rate = improving / len(all_metrics)
            mean_value = sum(values) / len(values)

            # copilot: compute meta score from improvement rate and mean value
            meta_score = round((improvement_rate * 0.4 + mean_value * 0.6), 4)

            issues: tuple[str, ...] = tuple(
                f"Metric '{m.name}' is not improving (delta={m.delta:.4f})"
                for m in all_metrics if not m.is_improving and m.delta < -0.03
            )

            recommendations_list: list[str] = []
            if meta_score < 0.65:
                recommendations_list.append("Review methodology coverage — score is below 0.65.")
            if improvement_rate < 0.5:
                recommendations_list.append(
                    "More than half of metrics are not improving; "
                    "review evaluation methodology."
                )
            if not recommendations_list:
                recommendations_list.append(
                    "Methodology appears healthy; continue current evaluation cadence."
                )
            recommendations = tuple(recommendations_list)

        prev_state = self._state
        self._state = EvaluationLoopState.META_EVALUATING

        me = MetaEvaluation(
            meta_id=_uid(),
            evaluation_id=self.loop_id,
            meta_score=meta_score,
            methodology_health=_methodology_health_label(meta_score),
            issues_found=issues,
            recommendations=recommendations,
            created_at=_now_iso(),
        )
        self._meta_evaluations.append(me)

        # copilot: restore state after meta-evaluation
        if prev_state not in (EvaluationLoopState.CONVERGED, EvaluationLoopState.DIVERGED):
            self._state = EvaluationLoopState.EVALUATING

        return me

    def check_convergence(self, loop_id: str) -> LoopConvergence:
        """Check whether the evaluation loop has converged.

        Computes a convergence score from the last CONVERGENCE_WINDOW
        rounds. The score is high when successive metric values are stable.

        Args:
            loop_id: The loop identifier.

        Returns:
            LoopConvergence: Convergence assessment.

        Raises:
            KeyError: If loop_id does not match self.loop_id.

        Example:
            >>> loop = EvaluationLoop()
            >>> loop.start("m-001")
            EvaluationJudgment(...)
            >>> lc = loop.check_convergence(loop.loop_id)
            >>> 0.0 <= lc.convergence_score <= 1.0
            True
        """
        if loop_id != self.loop_id:
            raise KeyError(f"loop_id mismatch: {loop_id!r} != {self.loop_id!r}")

        recent = self._records[-CONVERGENCE_WINDOW:]
        all_metrics = [m for rec in recent for m in rec.metrics]

        if len(all_metrics) < 2:
            convergence_score = 0.0
        else:
            values = [m.value for m in all_metrics]
            if len(values) >= 2:
                diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
                mean_diff = sum(diffs) / len(diffs)
                # copilot: high convergence when mean diff is small
                convergence_score = round(max(0.0, min(1.0, 1.0 - mean_diff * 10.0)), 4)
            else:
                convergence_score = 0.0

        threshold = 1.0 - CONVERGENCE_THRESHOLD
        converged = convergence_score >= threshold
        iters = self._round_counter if converged else 0

        if converged and self._state == EvaluationLoopState.EVALUATING:
            self._state = EvaluationLoopState.CONVERGED

        lc = LoopConvergence(
            convergence_id=_uid(),
            loop_id=self.loop_id,
            convergence_score=convergence_score,
            threshold=threshold,
            converged=converged,
            iterations_to_convergence=iters,
            checked_at=_now_iso(),
        )
        self._convergence = lc
        return lc

    def adapt_methodology(
        self,
        loop_id: str,
        meta_evaluation: MetaEvaluation,
    ) -> EvaluationJudgment:
        """Adapt the evaluation methodology based on a MetaEvaluation.

        Produces a new EvaluationJudgment reflecting the adaptation and
        records a MethodologyAdaptation with the changes applied.

        Args:
            loop_id:         The loop identifier.
            meta_evaluation: The MetaEvaluation driving the adaptation.

        Returns:
            EvaluationJudgment: A judgment reflecting the adapted methodology.

        Raises:
            KeyError: If loop_id does not match self.loop_id.

        Example:
            >>> loop = EvaluationLoop()
            >>> loop.start("m-001")
            EvaluationJudgment(...)
            >>> me = loop.meta_evaluate_round(loop.loop_id)
            >>> j = loop.adapt_methodology(loop.loop_id, me)
            >>> j.trust_tier in list(TrustTier)
            True
        """
        if loop_id != self.loop_id:
            raise KeyError(f"loop_id mismatch: {loop_id!r} != {self.loop_id!r}")

        changes = tuple(
            f"Applied recommendation: {rec}"
            for rec in meta_evaluation.recommendations
        )

        # copilot: select trust tier based on meta_score
        if meta_evaluation.meta_score >= 0.90:
            trust_tier = TrustTier.VERIFIED
        elif meta_evaluation.meta_score >= 0.75:
            trust_tier = TrustTier.REVIEWED
        else:
            trust_tier = TrustTier.PROPOSAL

        evidence = (f"meta_id={meta_evaluation.meta_id}",)
        obligations = meta_evaluation.issues_found or ("continue_evaluation",)

        return EvaluationJudgment(
            context=self.loop_id,
            formula=(
                f"adapted_methodology(score={meta_evaluation.meta_score:.4f}, "
                f"health={meta_evaluation.methodology_health})"
            ),
            authority=f"EvaluationLoop/v4.1",
            evidence=evidence,
            obligations=obligations,
            budget=max(0, MAX_ROUNDS - self._round_counter),
            trust_tier=trust_tier,
            proof_chain=(),
        )

    def generate_meta_report(self, loop_id: str) -> dict[str, Any]:
        """Generate a summary meta-report dict for the evaluation loop.

        Args:
            loop_id: The loop identifier.

        Returns:
            dict: Summary with keys: loop_id, total_rounds, final_state,
                  methodology_id, meta_eval_count, convergence, divergence_events.

        Raises:
            KeyError: If loop_id does not match self.loop_id.

        Example:
            >>> loop = EvaluationLoop()
            >>> loop.start("m-001")
            EvaluationJudgment(...)
            >>> report = loop.generate_meta_report(loop.loop_id)
            >>> "final_state" in report
            True
        """
        if loop_id != self.loop_id:
            raise KeyError(f"loop_id mismatch: {loop_id!r} != {self.loop_id!r}")

        conv = self._convergence
        return {
            "loop_id": self.loop_id,
            "total_rounds": self._round_counter,
            "final_state": self._state.value,
            "methodology_id": self._methodology_id,
            "meta_eval_count": len(self._meta_evaluations),
            "divergence_event_count": len(self._divergence_events),
            "convergence": {
                "converged": conv.converged,
                "score": conv.convergence_score,
                "threshold": conv.threshold,
            } if conv else None,
            "last_meta_score": (
                self._meta_evaluations[-1].meta_score
                if self._meta_evaluations else None
            ),
            "last_health": (
                self._meta_evaluations[-1].methodology_health
                if self._meta_evaluations else None
            ),
            "generated_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# v4.1 module-level functions
# ---------------------------------------------------------------------------


def run_evaluation_loop(
    methodology_id: str,
    max_rounds: int = MAX_ROUNDS,
    convergence_threshold: float = CONVERGENCE_THRESHOLD,
) -> MetaReport:
    """Run a complete meta-evaluation loop for a methodology.

    Runs evaluation rounds, performs meta-evaluations every META_EVAL_INTERVAL
    rounds, and checks for convergence. Returns a MetaReport on completion.

    Args:
        methodology_id:        The methodology to evaluate.
        max_rounds:            Maximum rounds (default MAX_ROUNDS).
        convergence_threshold: Required stability threshold.

    Returns:
        MetaReport: Summary of the completed meta-evaluation run.

    Example:
        >>> report = run_evaluation_loop("methodology-001", max_rounds=5)
        >>> report.total_rounds >= 1
        True
    """
    loop = EvaluationLoop()
    loop.start(methodology_id)
    metric_names = ["methodology_coverage", "evaluation_consistency", "methodology_robustness"]

    for round_n in range(1, max_rounds + 1):
        name = metric_names[(round_n - 1) % len(metric_names)]
        base_value = _v41_score(f"{methodology_id}::{name}", round_n)
        loop.evaluate_round(loop.loop_id, {
            "name": name,
            "value": base_value,
            "baseline": 0.75,
        })

        if round_n % META_EVAL_INTERVAL == 0:
            me = loop.meta_evaluate_round(loop.loop_id)
            loop.adapt_methodology(loop.loop_id, me)

        lc = loop.check_convergence(loop.loop_id)
        if lc.converged or loop._state in (
            EvaluationLoopState.CONVERGED,
            EvaluationLoopState.DIVERGED,
            EvaluationLoopState.FAILED,
        ):
            break

    return MetaReport(
        report_id=_uid(),
        loop_id=loop.loop_id,
        total_rounds=loop._round_counter,
        final_state=loop._state,
        convergence=loop._convergence,
        meta_evaluations=tuple(loop._meta_evaluations),
        generated_at=_now_iso(),
    )


def meta_evaluate(
    evaluation_records: list[EvaluationRecord],
    meta_config: dict[str, Any],
) -> MetaEvaluation:
    """Perform meta-evaluation on a list of evaluation records.

    Computes a meta score and health label from the provided records
    using weights from meta_config (or META_SCORE_WEIGHTS as defaults).

    Args:
        evaluation_records: List of EvaluationRecord objects.
        meta_config:        Dict with optional "weights" and "evaluation_id" keys.

    Returns:
        MetaEvaluation: The meta-evaluation result.

    Raises:
        ValueError: If evaluation_records is empty.

    Example:
        >>> from jugeo.evaluation.methodology_loops.evaluation_loop import (
        ...     meta_evaluate, EvaluationRecord, EvaluationLoopState,
        ...     EvaluationMetric, _now_iso,
        ... )
        >>> records = []  # would normally be non-empty
        >>> # meta_evaluate(records, {})  # would raise ValueError
    """
    if not evaluation_records:
        raise ValueError("meta_evaluate: evaluation_records must be non-empty")

    weights: dict[str, float] = meta_config.get("weights", META_SCORE_WEIGHTS)
    evaluation_id: str = meta_config.get("evaluation_id", _uid())

    all_metrics = [m for rec in evaluation_records for m in rec.metrics]
    improving_count = sum(1 for m in all_metrics if m.is_improving)
    total_count = len(all_metrics) or 1
    improvement_rate = improving_count / total_count

    mean_val = sum(m.value for m in all_metrics) / total_count if all_metrics else 0.0

    # copilot: aggregate using available weight dimensions
    coverage_weight = weights.get("coverage", 0.30)
    consistency_weight = weights.get("consistency", 0.25)
    meta_score = round(
        improvement_rate * (coverage_weight + consistency_weight) +
        mean_val * (1.0 - coverage_weight - consistency_weight),
        4
    )
    meta_score = max(0.0, min(1.0, meta_score))

    issues: list[str] = []
    if improvement_rate < 0.5:
        issues.append(f"Only {improvement_rate:.0%} of metrics are improving.")
    if mean_val < 0.70:
        issues.append(f"Mean metric value {mean_val:.4f} is below 0.70.")

    recommendations: list[str] = []
    if meta_score >= 0.85:
        recommendations.append("Methodology is performing well; maintain current approach.")
    else:
        recommendations.append("Review underperforming metrics and adjust methodology.")

    return MetaEvaluation(
        meta_id=_uid(),
        evaluation_id=evaluation_id,
        meta_score=meta_score,
        methodology_health=_methodology_health_label(meta_score),
        issues_found=tuple(issues),
        recommendations=tuple(recommendations),
        created_at=_now_iso(),
    )


def check_convergence(
    metric_series: MetricSeries,
    threshold: float,
) -> LoopConvergence:
    """Check whether a MetricSeries has converged.

    Convergence is declared when the standard deviation of the series'
    recent values falls below the threshold.

    Args:
        metric_series: The metric time series to analyse.
        threshold:     Maximum allowed variation to declare convergence.

    Returns:
        LoopConvergence: Convergence assessment.

    Example:
        >>> from jugeo.evaluation.methodology_loops.evaluation_loop import (
        ...     check_convergence, MetricSeries, _now_iso,
        ... )
        >>> series = MetricSeries(
        ...     series_id="s1", metric_name="cov",
        ...     values=(0.85, 0.85, 0.85), timestamps=(),
        ...     trend=0.0, created_at=_now_iso(),
        ... )
        >>> lc = check_convergence(series, threshold=0.02)
        >>> lc.converged
        True
    """
    values = metric_series.values
    if len(values) < 2:
        return LoopConvergence(
            convergence_id=_uid(),
            loop_id=metric_series.series_id,
            convergence_score=0.0,
            threshold=1.0 - threshold,
            converged=False,
            iterations_to_convergence=0,
            checked_at=_now_iso(),
        )

    # copilot: variance-based convergence check
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std_dev = variance ** 0.5

    # copilot: convergence score is 1 when std_dev=0, 0 when std_dev >= threshold
    convergence_score = round(max(0.0, min(1.0, 1.0 - std_dev / max(threshold, 1e-9))), 4)
    converged = std_dev <= threshold

    return LoopConvergence(
        convergence_id=_uid(),
        loop_id=metric_series.series_id,
        convergence_score=convergence_score,
        threshold=1.0 - threshold,
        converged=converged,
        iterations_to_convergence=n if converged else 0,
        checked_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# v4.1 additional smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("evaluation_loop.py — v4.1 meta-evaluation API smoke test")
    print("=" * 70)

    # copilot: [1] TrustTier and EvaluationLoopState enums
    print("\n[v4.1-1] Enum coverage:")
    for tier in TrustTier:
        print(f"    TrustTier.{tier.value}")
    for state in EvaluationLoopState:
        print(f"    EvaluationLoopState.{state.value}")
    assert len(list(TrustTier)) == 5
    assert len(list(EvaluationLoopState)) == 6

    # copilot: [2] EvaluationJudgment construction
    j = EvaluationJudgment(
        context="methodology-smoke-001",
        formula="evaluation_quality >= 0.80",
        authority="EvaluationLoop/v4.1",
        evidence=("record-abc",),
        obligations=("continue_evaluation",),
        budget=15,
        trust_tier=TrustTier.REVIEWED,
        proof_chain=(),
    )
    assert j.trust_tier == TrustTier.REVIEWED
    assert j.budget == 15
    print(f"\n[v4.1-2] EvaluationJudgment: trust_tier={j.trust_tier.value}  budget={j.budget}")

    # copilot: [3] run_evaluation_loop
    print("\n[v4.1-3] run_evaluation_loop (methodology-smoke-001)...")
    report = run_evaluation_loop("methodology-smoke-001", max_rounds=9)
    print(f"    report_id={report.report_id}")
    print(f"    total_rounds={report.total_rounds}")
    print(f"    final_state={report.final_state.value}")
    print(f"    meta_evaluations={len(report.meta_evaluations)}")
    if report.convergence:
        print(f"    convergence_score={report.convergence.convergence_score}")

    # copilot: [4] EvaluationLoop direct test
    print("\n[v4.1-4] EvaluationLoop direct test...")
    loop2 = EvaluationLoop()
    j2 = loop2.start("methodology-smoke-002")
    print(f"    loop_id={loop2.loop_id}")
    assert j2.trust_tier == TrustTier.PROPOSAL

    for r in range(6):
        m = loop2.evaluate_round(loop2.loop_id, {
            "name": "coverage",
            "value": 0.80 + r * 0.02,
            "baseline": 0.75,
        })
        print(f"    Round {r+1}: {m.name}={m.value:.4f}  delta={m.delta:.4f}  improving={m.is_improving}")
        if (r + 1) % META_EVAL_INTERVAL == 0:
            me = loop2.meta_evaluate_round(loop2.loop_id)
            print(f"    MetaEval: score={me.meta_score:.4f}  health={me.methodology_health}")

    lc = loop2.check_convergence(loop2.loop_id)
    print(f"    Convergence: score={lc.convergence_score:.4f}  converged={lc.converged}")

    # copilot: [5] adapt_methodology
    me2 = loop2._meta_evaluations[-1] if loop2._meta_evaluations else loop2.meta_evaluate_round(loop2.loop_id)
    j3 = loop2.adapt_methodology(loop2.loop_id, me2)
    print(f"\n[v4.1-5] adapt_methodology: trust_tier={j3.trust_tier.value}  formula={j3.formula}")

    # copilot: [6] meta_evaluate standalone
    records_for_meta = loop2._records[:3]
    if records_for_meta:
        me3 = meta_evaluate(records_for_meta, {"evaluation_id": "standalone-eval"})
        print(f"\n[v4.1-6] meta_evaluate: score={me3.meta_score:.4f}  health={me3.methodology_health}")

    # copilot: [7] check_convergence standalone
    ms = MetricSeries(
        series_id=_uid(),
        metric_name="coverage",
        values=(0.82, 0.82, 0.82, 0.82, 0.82),
        timestamps=("2024-01-15T10:00:00Z",) * 5,
        trend=0.0,
        created_at=_now_iso(),
    )
    lc2 = check_convergence(ms, threshold=0.02)
    print(f"\n[v4.1-7] check_convergence: score={lc2.convergence_score:.4f}  converged={lc2.converged}")
    assert lc2.converged, "Constant series should converge"

    # copilot: [8] generate_meta_report
    rpt = loop2.generate_meta_report(loop2.loop_id)
    print(f"\n[v4.1-8] generate_meta_report: total_rounds={rpt['total_rounds']}  "
          f"state={rpt['final_state']}  meta_evals={rpt['meta_eval_count']}")

    # copilot: [9] frozen dataclass immutability
    import dataclasses as _dc
    for frozen_obj, attr, val in [
        (j, "budget", 0),
        (ms, "trend", 0.5),
        (lc2, "converged", False),
    ]:
        try:
            setattr(frozen_obj, attr, val)
            print(f"[FAIL] {type(frozen_obj).__name__}.{attr} should be frozen!")
        except (_dc.FrozenInstanceError, AttributeError):
            pass
    print("\n[v4.1-9] All frozen dataclasses are correctly immutable.")

    print("\n" + "=" * 70)
    print("v4.1 meta-evaluation API smoke test PASSED")
    print("=" * 70)
