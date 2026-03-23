"""Evaluation and calibration: realized leverage, reuse, and failure analysis —
theory2.tex Ch58.

This module implements Stage 5 of the JuGeo discovery pipeline: post-hoc
evaluation of whether proposed theorems actually reduced obstructions (realized
leverage), how widely each theorem was reused across different problem instances
(reuse rate), and what systematic patterns explain proposal failures (failure
analysis).  The calibration subsystem uses the resulting data to update the
leverage-prediction model used by the upstream proposal generator.

Theory reference: theory2.tex Ch58 §6.2 — Evaluation, Calibration, and Realized
Leverage.

# copilot: shared-core marker

Background and motivation
--------------------------
The discovery engine produces *predicted* leverage scores for each theorem
proposal.  Predicted leverage estimates the expected reduction in total
obstruction severity that would result from proving the theorem.  However, until
the theorem is actually applied, this prediction is necessarily uncertain.

After a theorem is proven and applied to a collection of obstruction sets, we
can compute the *realized* leverage: the actual fractional reduction in
obstruction severity, averaged (possibly weighted) across all application
instances.  Comparing predicted leverage to realized leverage gives us a
calibration signal: if the predictor systematically over- or under-estimates
leverage for certain domain–structure combinations, we should adjust the
prediction formula accordingly.

Key concepts
------------
ObstructionSet
    A snapshot of the obstruction collection at a point in time, represented as
    a mapping from obstruction_id to severity.  By comparing the *before* and
    *after* sets for a theorem application, we compute the realized leverage.

Realized leverage
    Given a theorem T applied between time-point A (before) and B (after)::

        realized_leverage(T) = 1 - sum(severity_B) / sum(severity_A)

    A value of 1.0 means the theorem eliminated all obstructions; 0.0 means it
    had no effect; negative values (rare) indicate the theorem introduced new
    obstructions as side-effects.

Reuse rate
    The fraction of all theorem-application events in the usage log that
    involved a given theorem T.  A high reuse rate indicates that T is
    foundational (many downstream proofs depend on it) or overly broad
    (applied in contexts for which it was not designed).

Failure categories
------------------
Failed proposals are classified into one of four categories:

* **PROOF_GAP** — the proof sketch had a logical gap; the theorem could not be
  proven as stated.
* **DOMAIN_MISMATCH** — the theorem was proposed for one domain but the actual
  obstructions turned out to belong to a different domain.
* **INSUFFICIENT_EVIDENCE** — the supporting evidence was too weak to justify
  the proposal; no proof attempt was made.
* **LEVERAGE_OVERESTIMATED** — the theorem was proven but realized leverage was
  substantially below the predicted value.

Calibration
-----------
The calibration subsystem fits a linear correction to the predictor::

    realized_leverage ≈ α × predicted_leverage + β

where α and β are estimated by ordinary least squares from the historical
LeverageEvaluation records.  A well-calibrated predictor has α ≈ 1 and β ≈ 0.
Drift in these parameters over time signals distribution shift in the obstruction
landscape.

Typical usage::

    from jugeo.ideation.discovery_engine.evaluation_and_calibration_realize import (
        EvaluationCalibrationCoordinator,
        EvaluationCalibrationAnalyzer,
        EvaluationCalibrationWitness,
        EvalCalibConfig,
        ObstructionSet,
        run_evaluation_cycle,
        compute_realized_leverage,
        build_eval_cycle_data,
    )

    config = EvalCalibConfig()
    coord = EvaluationCalibrationCoordinator(config)
    before = ObstructionSet({"obs-1": 0.8, "obs-2": 0.6})
    after = ObstructionSet({"obs-1": 0.3, "obs-2": 0.1})
    eval_result = coord.evaluate_realized_leverage("prop-abc", before, after)
    print(eval_result.realized_leverage)   # 0.625

See also
--------
* ``a_real_mathematical_discovery_subs`` — generates the proposals evaluated here.
* ``theorem_and_falsification_burden_f`` — computes falsification burden for theorems.
* ``jugeo.ideation.discovery_engine.models`` — shared pipeline dataclasses.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enums
    "FailureCategory",
    # Frozen dataclasses
    "EvalCalibConfig",
    "ObstructionSet",
    "LeverageEvaluation",
    "TheoremUsageEvent",
    "ReuseEvaluation",
    "FailedProposal",
    "FailureAnalysisReport",
    "CalibrationResult",
    "EvalCycleData",
    "EvalCycleResult",
    "LeverageDistribution",
    "FailurePatternSummary",
    "CalibrationDriftReport",
    "LeverageWitnessReport",
    "CalibrationWitnessReport",
    "FailureWitnessReport",
    # Main classes
    "EvaluationCalibrationCoordinator",
    "EvaluationCalibrationAnalyzer",
    "EvaluationCalibrationWitness",
    # Free functions
    "run_evaluation_cycle",
    "compute_realized_leverage",
    "build_eval_cycle_data",
    # Helpers (exposed for testing)
    "_utcnow",
    "_uid",
    "_clamp",
    "_ols_fit",
    "_percentile",
    "_partition_by_category",
    "_leverage_bucket",
    "_drift_score",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
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
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate,
        DiscoveryConfig,
        DiscoveryResult,
        DiscoveryDiagnostics,
        DiscoveryStatus,
        PipelineStage,
        KindSignature,
        TheoremCandidate,
        PromotionDecision,
    )
except Exception:
    pass

try:
    from jugeo.ideation.discovery_engine.a_real_mathematical_discovery_subs import (
        ProposalRecord,
        ObstructionRecord,
        ArchiveTrace,
        ProposalOutcome,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX float timestamp."""
    return time.time()


def _uid() -> str:
    """Return a 32-character lowercase hex unique identifier."""
    return uuid.uuid4().hex


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lower*, *upper*].

    Parameters
    ----------
    value:
        The value to clamp.
    lower:
        Lower bound (inclusive).
    upper:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.
    """
    return max(lower, min(upper, value))


def _ols_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Fit a simple OLS linear model y = α·x + β to paired data.

    Uses the closed-form OLS estimator::

        α = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)
        β = (Σy − α·Σx) / n

    Falls back to (1.0, 0.0) when the denominator is zero (e.g. all x equal).

    Parameters
    ----------
    xs:
        Predictor values.
    ys:
        Response values.

    Returns
    -------
    tuple[float, float]
        The pair (α, β).
    """
    n = len(xs)
    if n < 2:
        return 1.0, 0.0
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-12:
        return 1.0, 0.0
    alpha = (n * sum_xy - sum_x * sum_y) / denom
    beta = (sum_y - alpha * sum_x) / n
    return alpha, beta


def _percentile(values: list[float], p: float) -> float:
    """Return the *p*-th percentile of *values* (0 ≤ p ≤ 100).

    Uses linear interpolation between adjacent sorted values.

    Parameters
    ----------
    values:
        The data set.
    p:
        The percentile to compute, in [0, 100].

    Returns
    -------
    float
        The estimated *p*-th percentile.
    """
    if not values:
        return 0.0
    sv = sorted(values)
    n = len(sv)
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sv[lo] * (1 - frac) + sv[hi] * frac


def _partition_by_category(
    failures: list[FailedProposal],
) -> dict[FailureCategory, list[FailedProposal]]:
    """Partition *failures* into groups by their failure category.

    Parameters
    ----------
    failures:
        The list of failed proposals to partition.

    Returns
    -------
    dict[FailureCategory, list[FailedProposal]]
        Mapping from each failure category to the proposals in that category.
    """
    groups: dict[FailureCategory, list[FailedProposal]] = {c: [] for c in FailureCategory}
    for f in failures:
        groups[f.category].append(f)
    return groups


def _leverage_bucket(leverage: float, n_buckets: int = 5) -> int:
    """Map *leverage* ∈ [0, 1] to an integer bucket index in [0, n_buckets − 1].

    Parameters
    ----------
    leverage:
        The leverage value to bucket.
    n_buckets:
        Number of equal-width buckets.

    Returns
    -------
    int
        Bucket index.
    """
    return min(int(leverage * n_buckets), n_buckets - 1)


def _drift_score(calibrations: list[CalibrationResult]) -> float:
    """Compute a scalar drift score from a sequence of calibration results.

    Drift is defined as the root-mean-square of the sequence of deviations
    (alpha_i − 1)² + beta_i² for each calibration, measuring how far each
    calibration is from the ideal (alpha=1, beta=0) identity mapping.

    Parameters
    ----------
    calibrations:
        Ordered sequence of calibration results (oldest first).

    Returns
    -------
    float
        Drift score ≥ 0; 0 means perfectly stable and well-calibrated.
    """
    if not calibrations:
        return 0.0
    deviations = [(c.alpha - 1.0) ** 2 + c.beta ** 2 for c in calibrations]
    return math.sqrt(sum(deviations) / len(deviations))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FailureCategory(Enum):
    """Classification of a failed theorem proposal.

    Attributes
    ----------
    PROOF_GAP:
        The proof sketch contained a logical gap that blocked completion.
    DOMAIN_MISMATCH:
        The theorem was misaligned with the actual domain of the obstruction.
    INSUFFICIENT_EVIDENCE:
        Supporting evidence was too weak; no proof was attempted.
    LEVERAGE_OVERESTIMATED:
        The theorem was proven but realized leverage was far below predicted.
    """

    PROOF_GAP = "proof_gap"
    DOMAIN_MISMATCH = "domain_mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    LEVERAGE_OVERESTIMATED = "leverage_overestimated"


# ---------------------------------------------------------------------------
# Frozen dataclasses — value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalCalibConfig:
    """Configuration for the evaluation and calibration subsystem.

    Attributes
    ----------
    min_history_for_calibration:
        Minimum number of LeverageEvaluation records needed before calibration.
    leverage_overestimate_threshold:
        A proposal is classified as LEVERAGE_OVERESTIMATED if
        ``predicted - realized > leverage_overestimate_threshold``.
    reuse_breadth_normalization:
        Total number of theorem-usage events used to normalize reuse rates.
        0 means auto-compute from the data.
    calibration_window:
        Maximum number of most-recent evaluations to include in each
        calibration fit.  0 means use all available history.
    drift_alarm_threshold:
        ``_drift_score`` value above which the calibration is flagged as drifted.
    """

    min_history_for_calibration: int = 5
    leverage_overestimate_threshold: float = 0.2
    reuse_breadth_normalization: int = 0
    calibration_window: int = 50
    drift_alarm_threshold: float = 0.3


@dataclass(frozen=True, slots=True)
class ObstructionSet:
    """A snapshot of obstruction severities at a point in time.

    Attributes
    ----------
    severity_map:
        Mapping from obstruction_id to severity ∈ [0, 1].
    snapshot_at:
        POSIX timestamp of this snapshot.
    """

    severity_map: dict[str, float]
    snapshot_at: float = field(default_factory=_utcnow)

    def total_severity(self) -> float:
        """Return the sum of all severity values in this snapshot.

        Returns
        -------
        float
            Total severity.
        """
        return sum(self.severity_map.values())

    def obstruction_ids(self) -> set[str]:
        """Return the set of obstruction IDs in this snapshot.

        Returns
        -------
        set[str]
            The obstruction IDs.
        """
        return set(self.severity_map.keys())


@dataclass(frozen=True, slots=True)
class LeverageEvaluation:
    """Evaluation record pairing predicted and realized leverage for a theorem.

    Attributes
    ----------
    eval_id:
        Unique ID for this evaluation record.
    proposal_id:
        ID of the theorem proposal being evaluated.
    predicted_leverage:
        The leverage predicted by the discovery subsystem at proposal time.
    realized_leverage:
        The actual leverage measured after applying the theorem.
    before_severity:
        Total obstruction severity before applying the theorem.
    after_severity:
        Total obstruction severity after applying the theorem.
    domain:
        Domain of the theorem.
    evaluated_at:
        POSIX timestamp of evaluation.
    """

    eval_id: str
    proposal_id: str
    predicted_leverage: float
    realized_leverage: float
    before_severity: float
    after_severity: float
    domain: str
    evaluated_at: float = field(default_factory=_utcnow)

    def prediction_error(self) -> float:
        """Return the signed prediction error (predicted − realized).

        Returns
        -------
        float
            Prediction error; positive means over-prediction.
        """
        return self.predicted_leverage - self.realized_leverage

    def absolute_error(self) -> float:
        """Return |predicted − realized|.

        Returns
        -------
        float
            Absolute prediction error.
        """
        return abs(self.prediction_error())


@dataclass(frozen=True, slots=True)
class TheoremUsageEvent:
    """A single recorded usage of a theorem during proof search.

    Attributes
    ----------
    event_id:
        Unique ID for this usage event.
    theorem_id:
        The theorem that was used.
    context_domain:
        Domain in which the theorem was applied.
    target_obstruction_id:
        The obstruction the theorem was used to address.
    usage_timestamp:
        When this usage occurred.
    was_successful:
        Whether the usage successfully reduced the obstruction.
    """

    event_id: str
    theorem_id: str
    context_domain: str
    target_obstruction_id: str
    usage_timestamp: float
    was_successful: bool = True


@dataclass(frozen=True, slots=True)
class ReuseEvaluation:
    """Reuse evaluation for a single theorem.

    Attributes
    ----------
    theorem_id:
        The theorem being evaluated.
    total_usages:
        Total number of times the theorem appeared in the usage log.
    successful_usages:
        Number of usages that were marked successful.
    reuse_rate:
        Fraction of all events in the log attributable to this theorem.
    domain_distribution:
        Mapping from domain to usage count for this theorem.
    """

    theorem_id: str
    total_usages: int
    successful_usages: int
    reuse_rate: float
    domain_distribution: dict[str, int]

    def success_rate(self) -> float:
        """Return the fraction of usages that were successful.

        Returns
        -------
        float
            Success rate ∈ [0, 1], or 0 if no usages.
        """
        return self.successful_usages / self.total_usages if self.total_usages > 0 else 0.0


@dataclass(frozen=True, slots=True)
class FailedProposal:
    """Record of a theorem proposal that failed, along with its failure category.

    Attributes
    ----------
    proposal_id:
        ID of the failed proposal.
    predicted_leverage:
        Leverage predicted at proposal time.
    realized_leverage:
        Actual leverage, if the theorem was attempted (may be 0).
    category:
        The diagnosed failure category.
    failure_description:
        Human-readable explanation of why the proposal failed.
    domain:
        Domain of the failed proposal.
    """

    proposal_id: str
    predicted_leverage: float
    realized_leverage: float
    category: FailureCategory
    failure_description: str
    domain: str


@dataclass(frozen=True, slots=True)
class FailureAnalysisReport:
    """Summary of a failure analysis run.

    Attributes
    ----------
    total_failures:
        Total number of failed proposals analysed.
    category_counts:
        Mapping from FailureCategory to count.
    dominant_category:
        The most frequent failure category.
    mean_leverage_loss:
        Mean of (predicted − realized) across all failures.
    domain_failure_counts:
        Mapping from domain name to failure count.
    recommendations:
        List of diagnostic recommendations for improving the proposal pipeline.
    """

    total_failures: int
    category_counts: dict[FailureCategory, int]
    dominant_category: FailureCategory
    mean_leverage_loss: float
    domain_failure_counts: dict[str, int]
    recommendations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """Result of a calibration fit on historical leverage evaluations.

    Attributes
    ----------
    calibration_id:
        Unique ID for this calibration run.
    alpha:
        Slope of the OLS fit (ideal: 1.0).
    beta:
        Intercept of the OLS fit (ideal: 0.0).
    n_samples:
        Number of evaluation records used.
    r_squared:
        R² of the OLS fit (1.0 = perfect linear relationship).
    mean_absolute_error:
        Mean absolute prediction error on the training set.
    calibrated_at:
        POSIX timestamp.
    """

    calibration_id: str
    alpha: float
    beta: float
    n_samples: int
    r_squared: float
    mean_absolute_error: float
    calibrated_at: float = field(default_factory=_utcnow)

    def apply(self, predicted: float) -> float:
        """Apply the calibration to a raw predicted leverage value.

        Parameters
        ----------
        predicted:
            Raw predicted leverage value from the discovery subsystem.

        Returns
        -------
        float
            Calibrated leverage prediction ∈ [0, 1].
        """
        return _clamp(self.alpha * predicted + self.beta)


@dataclass(frozen=True, slots=True)
class EvalCycleData:
    """Input bundle for a single evaluation cycle.

    Attributes
    ----------
    cycle_id:
        Unique identifier for this evaluation cycle.
    leverage_evals:
        All leverage evaluation records for this cycle.
    usage_events:
        All theorem usage events recorded during this cycle.
    failed_proposals:
        All proposals that failed during this cycle.
    """

    cycle_id: str
    leverage_evals: tuple[LeverageEvaluation, ...]
    usage_events: tuple[TheoremUsageEvent, ...]
    failed_proposals: tuple[FailedProposal, ...]


@dataclass(frozen=True, slots=True)
class EvalCycleResult:
    """Output of a full evaluation cycle.

    Attributes
    ----------
    cycle_id:
        ID echoed from the input ``EvalCycleData``.
    leverage_distribution:
        Distribution statistics for realized leverage.
    failure_analysis:
        Failure analysis report for this cycle.
    calibration:
        The calibration fit produced from the cycle's evaluation data.
    mean_reuse_rate:
        Mean reuse rate across all theorem-usage events in this cycle.
    cycle_duration_s:
        Wall-clock duration.
    """

    cycle_id: str
    leverage_distribution: LeverageDistribution
    failure_analysis: FailureAnalysisReport
    calibration: CalibrationResult
    mean_reuse_rate: float
    cycle_duration_s: float


@dataclass(frozen=True, slots=True)
class LeverageDistribution:
    """Distribution statistics for a collection of realized leverage values.

    Attributes
    ----------
    n:
        Sample size.
    mean:
        Arithmetic mean.
    std:
        Sample standard deviation.
    p10:
        10th percentile.
    p50:
        50th percentile (median).
    p90:
        90th percentile.
    bucket_counts:
        Frequency table: bucket_index → count, using ``_leverage_bucket``.
    """

    n: int
    mean: float
    std: float
    p10: float
    p50: float
    p90: float
    bucket_counts: dict[int, int]


@dataclass(frozen=True, slots=True)
class FailurePatternSummary:
    """Summary of patterns identified in a FailureAnalysisReport.

    Attributes
    ----------
    dominant_category:
        Most frequent failure category.
    domain_hotspots:
        Domains with unusually high failure rates.
    over_prediction_rate:
        Fraction of failures due to leverage over-estimation.
    proof_gap_rate:
        Fraction of failures due to proof gaps.
    actionable_recommendations:
        High-priority recommendations derived from the patterns.
    """

    dominant_category: FailureCategory
    domain_hotspots: tuple[str, ...]
    over_prediction_rate: float
    proof_gap_rate: float
    actionable_recommendations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalibrationDriftReport:
    """Drift analysis over a sequence of calibration results.

    Attributes
    ----------
    n_calibrations:
        Number of calibration results analysed.
    drift_score:
        Aggregate drift score from ``_drift_score``.
    is_drifted:
        Whether drift_score exceeds the configured alarm threshold.
    alpha_trend:
        Direction of alpha trend: 'increasing', 'decreasing', or 'stable'.
    beta_trend:
        Direction of beta trend: 'increasing', 'decreasing', or 'stable'.
    latest_mae:
        MAE of the most recent calibration result.
    """

    n_calibrations: int
    drift_score: float
    is_drifted: bool
    alpha_trend: str
    beta_trend: str
    latest_mae: float


@dataclass(frozen=True, slots=True)
class LeverageWitnessReport:
    """Witness report for a single leverage evaluation.

    Attributes
    ----------
    eval_id:
        The evaluation being witnessed.
    is_valid:
        Whether the evaluation is internally valid.
    issues:
        List of validation issues found.
    confidence:
        Witness confidence ∈ [0, 1].
    """

    eval_id: str
    is_valid: bool
    issues: tuple[str, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class CalibrationWitnessReport:
    """Witness report for a calibration result.

    Attributes
    ----------
    calibration_id:
        The calibration being witnessed.
    alpha_plausible:
        Whether alpha is in a plausible range (0.5, 1.5).
    beta_plausible:
        Whether beta is in a plausible range (-0.3, 0.3).
    sufficient_data:
        Whether enough samples were used.
    overall_valid:
        All checks passed.
    notes:
        Explanatory notes.
    """

    calibration_id: str
    alpha_plausible: bool
    beta_plausible: bool
    sufficient_data: bool
    overall_valid: bool
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FailureWitnessReport:
    """Witness report for a FailureAnalysisReport.

    Attributes
    ----------
    total_failures_checked:
        Number of failures validated.
    categories_complete:
        Whether all FailureCategory values are represented in the report.
    inconsistencies:
        List of detected inconsistencies.
    is_valid:
        Whether no inconsistencies were found.
    """

    total_failures_checked: int
    categories_complete: bool
    inconsistencies: tuple[str, ...]
    is_valid: bool


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def compute_realized_leverage(
    before: ObstructionSet,
    after: ObstructionSet,
) -> float:
    """Compute the realized leverage between two obstruction snapshots.

    Realized leverage is::

        1 - total_severity(after) / total_severity(before)

    Returns 0.0 if ``before`` has zero total severity (no reduction possible).
    Clamps to [-1, 1] to handle edge cases where new obstructions appear.

    Parameters
    ----------
    before:
        Obstruction snapshot before applying the theorem.
    after:
        Obstruction snapshot after applying the theorem.

    Returns
    -------
    float
        Realized leverage ∈ [-1, 1]; positive means reduction.
    """
    total_before = before.total_severity()
    if total_before == 0.0:
        return 0.0
    total_after = after.total_severity()
    raw = 1.0 - total_after / total_before
    return _clamp(raw, -1.0, 1.0)


def build_eval_cycle_data(
    proposal_eval_pairs: list[tuple[str, float, float, str]],
    usage_events: list[TheoremUsageEvent] | None = None,
    failed_proposals: list[FailedProposal] | None = None,
    cycle_id: str | None = None,
) -> EvalCycleData:
    """Convenience function to construct an ``EvalCycleData`` bundle.

    Parameters
    ----------
    proposal_eval_pairs:
        List of (proposal_id, predicted_leverage, realized_leverage, domain) tuples.
    usage_events:
        Optional usage events; defaults to empty list.
    failed_proposals:
        Optional failed proposals; defaults to empty list.
    cycle_id:
        Optional cycle ID; one is generated if not provided.

    Returns
    -------
    EvalCycleData
        The constructed bundle.
    """
    cid = cycle_id or f"eval-cycle-{_uid()[:8]}"
    evals: list[LeverageEvaluation] = []
    for proposal_id, predicted, realized, domain in proposal_eval_pairs:
        # Reconstruct plausible before/after severities from realized leverage
        before_sev = 1.0
        after_sev = max(0.0, before_sev * (1.0 - realized))
        evals.append(
            LeverageEvaluation(
                eval_id=f"eval-{_uid()[:8]}",
                proposal_id=proposal_id,
                predicted_leverage=predicted,
                realized_leverage=realized,
                before_severity=before_sev,
                after_severity=after_sev,
                domain=domain,
                evaluated_at=_utcnow(),
            )
        )
    return EvalCycleData(
        cycle_id=cid,
        leverage_evals=tuple(evals),
        usage_events=tuple(usage_events or []),
        failed_proposals=tuple(failed_proposals or []),
    )


def run_evaluation_cycle(
    cycle_data: EvalCycleData,
    config: EvalCalibConfig | None = None,
) -> EvalCycleResult:
    """Run a complete evaluation cycle as a free function (convenience API).

    Parameters
    ----------
    cycle_data:
        The evaluation cycle data bundle.
    config:
        Optional configuration; uses defaults if not provided.

    Returns
    -------
    EvalCycleResult
        The full evaluation cycle result.
    """
    cfg = config or EvalCalibConfig()
    coord = EvaluationCalibrationCoordinator(cfg)
    return coord.run_evaluation_cycle(cycle_data)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class EvaluationCalibrationCoordinator:
    """Coordinates evaluation and calibration runs for the discovery engine.

    Maintains an internal history of leverage evaluations used as input to
    the calibration subsystem.

    Parameters
    ----------
    config:
        Configuration for this coordinator.
    """

    def __init__(self, config: EvalCalibConfig) -> None:
        self._config = config
        self._eval_history: list[LeverageEvaluation] = []
        self._calibration_history: list[CalibrationResult] = []

    def evaluate_realized_leverage(
        self,
        proposal_id: str,
        before: ObstructionSet,
        after: ObstructionSet,
        domain: str = "unknown",
        predicted_leverage: float = 0.5,
    ) -> LeverageEvaluation:
        """Compute and record the realized leverage for a theorem application.

        Parameters
        ----------
        proposal_id:
            ID of the proposal (theorem) being evaluated.
        before:
            Obstruction snapshot before applying the theorem.
        after:
            Obstruction snapshot after applying the theorem.
        domain:
            Domain of the theorem.
        predicted_leverage:
            The leverage that was predicted at proposal time.

        Returns
        -------
        LeverageEvaluation
            The evaluation record, which is also stored in internal history.
        """
        realized = compute_realized_leverage(before, after)
        ev = LeverageEvaluation(
            eval_id=f"eval-{_uid()[:8]}",
            proposal_id=proposal_id,
            predicted_leverage=predicted_leverage,
            realized_leverage=realized,
            before_severity=before.total_severity(),
            after_severity=after.total_severity(),
            domain=domain,
            evaluated_at=_utcnow(),
        )
        self._eval_history.append(ev)
        return ev

    def evaluate_reuse_rate(
        self,
        theorem_id: str,
        usage_log: list[TheoremUsageEvent],
    ) -> ReuseEvaluation:
        """Compute the reuse rate for *theorem_id* from *usage_log*.

        Parameters
        ----------
        theorem_id:
            The theorem whose reuse rate is being computed.
        usage_log:
            All theorem usage events (not just for this theorem).

        Returns
        -------
        ReuseEvaluation
            Reuse statistics for the given theorem.
        """
        theorem_events = [e for e in usage_log if e.theorem_id == theorem_id]
        total_events = len(usage_log)
        total_usages = len(theorem_events)
        successful = sum(1 for e in theorem_events if e.was_successful)

        norm = (
            self._config.reuse_breadth_normalization
            if self._config.reuse_breadth_normalization > 0
            else total_events
        )
        reuse_rate = total_usages / norm if norm > 0 else 0.0

        domain_dist: dict[str, int] = {}
        for e in theorem_events:
            domain_dist[e.context_domain] = domain_dist.get(e.context_domain, 0) + 1

        return ReuseEvaluation(
            theorem_id=theorem_id,
            total_usages=total_usages,
            successful_usages=successful,
            reuse_rate=_clamp(reuse_rate),
            domain_distribution=domain_dist,
        )

    def run_failure_analysis(
        self,
        failed_proposals: list[FailedProposal],
    ) -> FailureAnalysisReport:
        """Analyse a collection of failed proposals to identify systematic patterns.

        Parameters
        ----------
        failed_proposals:
            The failed proposals to analyse.

        Returns
        -------
        FailureAnalysisReport
            Structured failure analysis report.
        """
        if not failed_proposals:
            return FailureAnalysisReport(
                total_failures=0,
                category_counts={c: 0 for c in FailureCategory},
                dominant_category=FailureCategory.PROOF_GAP,
                mean_leverage_loss=0.0,
                domain_failure_counts={},
                recommendations=("No failures to analyse.",),
            )

        groups = _partition_by_category(failed_proposals)
        category_counts = {c: len(v) for c, v in groups.items()}
        dominant = max(category_counts, key=lambda c: category_counts[c])

        leverage_losses = [
            f.predicted_leverage - f.realized_leverage for f in failed_proposals
        ]
        mean_loss = sum(leverage_losses) / len(leverage_losses)

        domain_counts: dict[str, int] = {}
        for f in failed_proposals:
            domain_counts[f.domain] = domain_counts.get(f.domain, 0) + 1

        recommendations = _build_recommendations(category_counts, domain_counts, mean_loss)

        return FailureAnalysisReport(
            total_failures=len(failed_proposals),
            category_counts=category_counts,
            dominant_category=dominant,
            mean_leverage_loss=mean_loss,
            domain_failure_counts=domain_counts,
            recommendations=tuple(recommendations),
        )

    def calibrate_leverage_predictor(
        self,
        historical: list[LeverageEvaluation],
    ) -> CalibrationResult:
        """Fit a linear calibration from predicted to realized leverage.

        Parameters
        ----------
        historical:
            Historical leverage evaluation records.

        Returns
        -------
        CalibrationResult
            OLS-fitted calibration result.
        """
        window = self._config.calibration_window
        records = historical[-window:] if window > 0 else historical

        if len(records) < self._config.min_history_for_calibration:
            return CalibrationResult(
                calibration_id=f"cal-{_uid()[:8]}",
                alpha=1.0,
                beta=0.0,
                n_samples=len(records),
                r_squared=0.0,
                mean_absolute_error=float("nan"),
                calibrated_at=_utcnow(),
            )

        xs = [e.predicted_leverage for e in records]
        ys = [e.realized_leverage for e in records]
        alpha, beta = _ols_fit(xs, ys)

        # Compute R² and MAE
        y_mean = sum(ys) / len(ys)
        ss_tot = sum((y - y_mean) ** 2 for y in ys)
        y_hats = [alpha * x + beta for x in xs]
        ss_res = sum((y - yh) ** 2 for y, yh in zip(ys, y_hats))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0
        mae = sum(abs(y - yh) for y, yh in zip(ys, y_hats)) / len(ys)

        result = CalibrationResult(
            calibration_id=f"cal-{_uid()[:8]}",
            alpha=alpha,
            beta=beta,
            n_samples=len(records),
            r_squared=_clamp(r2, -1.0, 1.0),
            mean_absolute_error=mae,
            calibrated_at=_utcnow(),
        )
        self._calibration_history.append(result)
        return result

    def run_evaluation_cycle(
        self,
        cycle_data: EvalCycleData,
    ) -> EvalCycleResult:
        """Run a full evaluation cycle from an ``EvalCycleData`` bundle.

        Steps:
        1. Record all leverage evaluations in history.
        2. Analyse the leverage distribution.
        3. Run failure analysis.
        4. Calibrate the predictor.
        5. Compute mean reuse rate.

        Parameters
        ----------
        cycle_data:
            The evaluation data for this cycle.

        Returns
        -------
        EvalCycleResult
            Full cycle result.
        """
        t0 = _utcnow()

        self._eval_history.extend(cycle_data.leverage_evals)

        analyzer = EvaluationCalibrationAnalyzer()
        dist = analyzer.analyze_leverage_distribution(list(cycle_data.leverage_evals))
        failure_report = self.run_failure_analysis(list(cycle_data.failed_proposals))
        calibration = self.calibrate_leverage_predictor(self._eval_history)

        unique_theorems = {e.theorem_id for e in cycle_data.usage_events}
        if unique_theorems and cycle_data.usage_events:
            reuse_evals = [
                self.evaluate_reuse_rate(tid, list(cycle_data.usage_events))
                for tid in unique_theorems
            ]
            mean_reuse = analyzer.compute_reuse_breadth(reuse_evals)
        else:
            mean_reuse = 0.0

        duration = _utcnow() - t0
        return EvalCycleResult(
            cycle_id=cycle_data.cycle_id,
            leverage_distribution=dist,
            failure_analysis=failure_report,
            calibration=calibration,
            mean_reuse_rate=mean_reuse,
            cycle_duration_s=duration,
        )


def _build_recommendations(
    category_counts: dict[FailureCategory, int],
    domain_counts: dict[str, int],
    mean_loss: float,
) -> list[str]:
    """Build a list of diagnostic recommendations from failure analysis data.

    Parameters
    ----------
    category_counts:
        Frequency table of failure categories.
    domain_counts:
        Frequency table of failure domains.
    mean_loss:
        Mean leverage loss across all failures.

    Returns
    -------
    list[str]
        Ordered list of recommendations (most important first).
    """
    recommendations: list[str] = []

    proof_gap_count = category_counts.get(FailureCategory.PROOF_GAP, 0)
    if proof_gap_count > 0:
        recommendations.append(
            f"Address {proof_gap_count} proof-gap failure(s) by strengthening proof sketches "
            f"with explicit intermediate lemmas."
        )

    mismatch_count = category_counts.get(FailureCategory.DOMAIN_MISMATCH, 0)
    if mismatch_count > 0:
        recommendations.append(
            f"Resolve {mismatch_count} domain-mismatch failure(s) by refining the domain "
            f"classifier in the proposal generator."
        )

    overest_count = category_counts.get(FailureCategory.LEVERAGE_OVERESTIMATED, 0)
    if overest_count > 0 or mean_loss > 0.15:
        recommendations.append(
            f"Mean leverage loss {mean_loss:.3f} suggests systematic over-prediction; "
            f"recalibrate the leverage predictor."
        )

    if domain_counts:
        top_domain = max(domain_counts, key=lambda d: domain_counts[d])
        recommendations.append(
            f"Domain '{top_domain}' accounts for the most failures "
            f"({domain_counts[top_domain]}); investigate structural gaps there."
        )

    if not recommendations:
        recommendations.append("No dominant failure pattern detected; no specific action needed.")

    return recommendations


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class EvaluationCalibrationAnalyzer:
    """Analyses evaluation and calibration data for the discovery engine.

    Stateless: all information is passed as parameters.
    """

    def analyze_leverage_distribution(
        self,
        evals: list[LeverageEvaluation],
    ) -> LeverageDistribution:
        """Compute distribution statistics for a set of leverage evaluations.

        Parameters
        ----------
        evals:
            The leverage evaluations to analyse.

        Returns
        -------
        LeverageDistribution
            Distribution statistics.
        """
        if not evals:
            return LeverageDistribution(0, 0.0, 0.0, 0.0, 0.0, 0.0, {})

        values = [e.realized_leverage for e in evals]
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
        std = math.sqrt(variance)
        p10 = _percentile(values, 10)
        p50 = _percentile(values, 50)
        p90 = _percentile(values, 90)

        bucket_counts: dict[int, int] = {}
        for v in values:
            b = _leverage_bucket(v)
            bucket_counts[b] = bucket_counts.get(b, 0) + 1

        return LeverageDistribution(
            n=n,
            mean=mean,
            std=std,
            p10=p10,
            p50=p50,
            p90=p90,
            bucket_counts=bucket_counts,
        )

    def analyze_failure_patterns(
        self,
        report: FailureAnalysisReport,
    ) -> FailurePatternSummary:
        """Extract actionable patterns from a FailureAnalysisReport.

        Parameters
        ----------
        report:
            The failure analysis report to summarise.

        Returns
        -------
        FailurePatternSummary
            Distilled pattern summary.
        """
        total = report.total_failures if report.total_failures > 0 else 1
        over_pred_count = report.category_counts.get(FailureCategory.LEVERAGE_OVERESTIMATED, 0)
        proof_gap_count = report.category_counts.get(FailureCategory.PROOF_GAP, 0)
        over_pred_rate = over_pred_count / total
        proof_gap_rate = proof_gap_count / total

        domain_hotspots = tuple(
            d
            for d, cnt in sorted(
                report.domain_failure_counts.items(), key=lambda x: x[1], reverse=True
            )
            if cnt >= 2
        )

        actionable: list[str] = []
        if over_pred_rate > 0.3:
            actionable.append("Reduce leverage threshold to filter over-optimistic proposals.")
        if proof_gap_rate > 0.3:
            actionable.append("Require more rigorous proof sketches before archiving proposals.")
        for d in domain_hotspots[:2]:
            actionable.append(f"Investigate structural issues in domain '{d}'.")

        return FailurePatternSummary(
            dominant_category=report.dominant_category,
            domain_hotspots=domain_hotspots,
            over_prediction_rate=over_pred_rate,
            proof_gap_rate=proof_gap_rate,
            actionable_recommendations=tuple(actionable) if actionable else ("No critical patterns.",),
        )

    def analyze_calibration_drift(
        self,
        calibrations: list[CalibrationResult],
        alarm_threshold: float = 0.3,
    ) -> CalibrationDriftReport:
        """Analyse drift in a sequence of calibration results.

        Parameters
        ----------
        calibrations:
            Ordered list of calibration results (oldest first).
        alarm_threshold:
            Drift score above which drift is flagged.

        Returns
        -------
        CalibrationDriftReport
            Drift analysis report.
        """
        n = len(calibrations)
        if n == 0:
            return CalibrationDriftReport(0, 0.0, False, "stable", "stable", 0.0)

        drift = _drift_score(calibrations)
        is_drifted = drift > alarm_threshold

        def _trend(values: list[float]) -> str:
            if len(values) < 2:
                return "stable"
            delta = values[-1] - values[0]
            if delta > 0.05:
                return "increasing"
            elif delta < -0.05:
                return "decreasing"
            return "stable"

        alphas = [c.alpha for c in calibrations]
        betas = [c.beta for c in calibrations]
        alpha_trend = _trend(alphas)
        beta_trend = _trend(betas)
        latest_mae = calibrations[-1].mean_absolute_error if calibrations else 0.0

        return CalibrationDriftReport(
            n_calibrations=n,
            drift_score=drift,
            is_drifted=is_drifted,
            alpha_trend=alpha_trend,
            beta_trend=beta_trend,
            latest_mae=latest_mae,
        )

    def compute_reuse_breadth(
        self,
        reuse_evals: list[ReuseEvaluation],
    ) -> float:
        """Compute the mean reuse rate across a collection of reuse evaluations.

        Parameters
        ----------
        reuse_evals:
            The reuse evaluations to summarise.

        Returns
        -------
        float
            Mean reuse rate ∈ [0, 1].
        """
        if not reuse_evals:
            return 0.0
        return sum(r.reuse_rate for r in reuse_evals) / len(reuse_evals)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class EvaluationCalibrationWitness:
    """Verifies the correctness and plausibility of evaluations and calibrations.

    All methods are pure and produce immutable witness reports.
    """

    def witness_leverage_evaluation(
        self,
        eval_rec: LeverageEvaluation,
    ) -> LeverageWitnessReport:
        """Check that a leverage evaluation record is internally valid.

        Checks:
        * Realized leverage ∈ [-1, 1].
        * Before severity ≥ 0.
        * After severity ≥ 0.
        * after_severity ≤ before_severity + 1e-6 (obstruction reduction claim).
        * predicted_leverage ∈ [0, 1].

        Parameters
        ----------
        eval_rec:
            The evaluation record to witness.

        Returns
        -------
        LeverageWitnessReport
            Witness report.
        """
        issues: list[str] = []
        if not (-1.0 <= eval_rec.realized_leverage <= 1.0):
            issues.append(f"realized_leverage {eval_rec.realized_leverage:.4f} outside [-1, 1].")
        if eval_rec.before_severity < 0:
            issues.append(f"before_severity {eval_rec.before_severity:.4f} is negative.")
        if eval_rec.after_severity < 0:
            issues.append(f"after_severity {eval_rec.after_severity:.4f} is negative.")
        if eval_rec.after_severity > eval_rec.before_severity + 1e-6:
            issues.append(
                f"after_severity ({eval_rec.after_severity:.4f}) exceeds "
                f"before_severity ({eval_rec.before_severity:.4f})."
            )
        if not (0.0 <= eval_rec.predicted_leverage <= 1.0):
            issues.append(f"predicted_leverage {eval_rec.predicted_leverage:.4f} outside [0, 1].")

        is_valid = len(issues) == 0
        confidence = _clamp(1.0 - 0.25 * len(issues))
        return LeverageWitnessReport(
            eval_id=eval_rec.eval_id,
            is_valid=is_valid,
            issues=tuple(issues),
            confidence=confidence,
        )

    def witness_calibration(
        self,
        result: CalibrationResult,
        historical: list[LeverageEvaluation],
    ) -> CalibrationWitnessReport:
        """Verify that a calibration result is plausible.

        Parameters
        ----------
        result:
            The calibration result to witness.
        historical:
            The historical data used to produce the calibration.

        Returns
        -------
        CalibrationWitnessReport
            Calibration witness report.
        """
        notes: list[str] = []
        alpha_ok = 0.5 <= result.alpha <= 1.5
        if not alpha_ok:
            notes.append(f"alpha={result.alpha:.4f} is outside the plausible range [0.5, 1.5].")
        beta_ok = -0.3 <= result.beta <= 0.3
        if not beta_ok:
            notes.append(f"beta={result.beta:.4f} is outside the plausible range [-0.3, 0.3].")
        sufficient = result.n_samples >= 5
        if not sufficient:
            notes.append(f"Only {result.n_samples} samples — calibration may be unreliable.")
        if result.r_squared < 0.3 and result.n_samples >= 5:
            notes.append(f"Low R²={result.r_squared:.4f}; linear model may not fit data well.")

        overall = alpha_ok and beta_ok and sufficient
        return CalibrationWitnessReport(
            calibration_id=result.calibration_id,
            alpha_plausible=alpha_ok,
            beta_plausible=beta_ok,
            sufficient_data=sufficient,
            overall_valid=overall,
            notes=tuple(notes),
        )

    def witness_failure_analysis(
        self,
        report: FailureAnalysisReport,
    ) -> FailureWitnessReport:
        """Verify that a failure analysis report is self-consistent.

        Parameters
        ----------
        report:
            The failure analysis report to witness.

        Returns
        -------
        FailureWitnessReport
            Failure witness report.
        """
        inconsistencies: list[str] = []
        # Check that category counts sum to total_failures
        count_sum = sum(report.category_counts.values())
        if count_sum != report.total_failures:
            inconsistencies.append(
                f"Category count sum ({count_sum}) != total_failures ({report.total_failures})."
            )
        # Check dominant category is actually the maximum
        if report.category_counts:
            true_max_cat = max(report.category_counts, key=lambda c: report.category_counts[c])
            if report.dominant_category != true_max_cat:
                # Allow ties
                max_count = report.category_counts[true_max_cat]
                reported_count = report.category_counts.get(report.dominant_category, 0)
                if reported_count < max_count:
                    inconsistencies.append(
                        f"dominant_category {report.dominant_category.value} has count "
                        f"{reported_count} but {true_max_cat.value} has count {max_count}."
                    )
        # Check all categories are represented in the report
        categories_complete = set(report.category_counts.keys()) == set(FailureCategory)

        return FailureWitnessReport(
            total_failures_checked=report.total_failures,
            categories_complete=categories_complete,
            inconsistencies=tuple(inconsistencies),
            is_valid=len(inconsistencies) == 0,
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== EvaluationCalibration smoke test ===\n")

    cfg = EvalCalibConfig(
        min_history_for_calibration=3,
        leverage_overestimate_threshold=0.15,
        calibration_window=20,
        drift_alarm_threshold=0.25,
    )
    coord = EvaluationCalibrationCoordinator(cfg)

    # --- Realized leverage ---
    before = ObstructionSet({"obs-1": 0.8, "obs-2": 0.6, "obs-3": 0.4})
    after = ObstructionSet({"obs-1": 0.3, "obs-2": 0.1, "obs-3": 0.0})
    ev = coord.evaluate_realized_leverage(
        "prop-001", before, after, domain="algebraic-geometry", predicted_leverage=0.7
    )
    print(f"Realized leverage  : {ev.realized_leverage:.4f}")
    print(f"Prediction error   : {ev.prediction_error():.4f}")

    # Build some history
    pairs = [
        ("prop-001", 0.70, 0.65, "algebraic-geometry"),
        ("prop-002", 0.55, 0.50, "number-theory"),
        ("prop-003", 0.80, 0.60, "topology"),
        ("prop-004", 0.40, 0.42, "category-theory"),
        ("prop-005", 0.65, 0.55, "algebraic-geometry"),
        ("prop-006", 0.30, 0.25, "analysis"),
    ]
    cycle_data = build_eval_cycle_data(pairs)

    # --- Failure analysis ---
    failed = [
        FailedProposal("p-f01", 0.75, 0.0, FailureCategory.PROOF_GAP,
                       "Intermediate lemma was missing.", "algebraic-geometry"),
        FailedProposal("p-f02", 0.60, 0.05, FailureCategory.LEVERAGE_OVERESTIMATED,
                       "Theorem proven but barely reduced obstructions.", "number-theory"),
        FailedProposal("p-f03", 0.50, 0.0, FailureCategory.DOMAIN_MISMATCH,
                       "Theorem stated for sheaves but obstruction is arithmetic.", "topology"),
    ]
    failure_report = coord.run_failure_analysis(failed)
    print(f"\nFailure Analysis:")
    print(f"  total={failure_report.total_failures}  dominant={failure_report.dominant_category.value}")
    print(f"  mean_leverage_loss={failure_report.mean_leverage_loss:.4f}")
    for rec in failure_report.recommendations:
        print(f"  → {rec}")

    # --- Calibration ---
    cal = coord.calibrate_leverage_predictor(
        [LeverageEvaluation(
            f"e{i}", f"p{i}", p, r, 1.0, 1.0 - r, "unknown", _utcnow()
        ) for i, (_, p, r, _) in enumerate(pairs)]
    )
    print(f"\nCalibration: alpha={cal.alpha:.4f}  beta={cal.beta:.4f}  "
          f"R²={cal.r_squared:.4f}  MAE={cal.mean_absolute_error:.4f}")

    # --- Eval cycle ---
    result = coord.run_evaluation_cycle(cycle_data)
    print(f"\nEval Cycle Result '{result.cycle_id}':")
    print(f"  mean_realized_leverage={result.leverage_distribution.mean:.4f}")
    print(f"  mean_reuse_rate={result.mean_reuse_rate:.4f}")

    # --- Analyzer ---
    analyzer = EvaluationCalibrationAnalyzer()
    drift = analyzer.analyze_calibration_drift([cal])
    print(f"\nCalibration Drift: score={drift.drift_score:.4f}  "
          f"is_drifted={drift.is_drifted}  alpha_trend={drift.alpha_trend}")

    pattern = analyzer.analyze_failure_patterns(failure_report)
    print(f"\nFailure Patterns: dominant={pattern.dominant_category.value}  "
          f"over_pred_rate={pattern.over_prediction_rate:.3f}")

    # --- Witness ---
    witness = EvaluationCalibrationWitness()
    lev_report = witness.witness_leverage_evaluation(ev)
    print(f"\nLeverage Witness: valid={lev_report.is_valid}  confidence={lev_report.confidence:.3f}")

    cal_report = witness.witness_calibration(cal, [])
    print(f"Calibration Witness: overall_valid={cal_report.overall_valid}  "
          f"alpha_ok={cal_report.alpha_plausible}")

    fail_wit = witness.witness_failure_analysis(failure_report)
    print(f"Failure Witness: is_valid={fail_wit.is_valid}  "
          f"categories_complete={fail_wit.categories_complete}")

    # --- Free-function API ---
    result2 = run_evaluation_cycle(cycle_data, config=cfg)
    print(f"\nFree-function cycle: {result2.cycle_id}  "
          f"cal_alpha={result2.calibration.alpha:.4f}")

    print("\n=== Smoke test passed ===")
