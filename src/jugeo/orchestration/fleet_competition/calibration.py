"""Calibration trace management for fleet competition.

This module implements the calibration pipeline described in theory2.tex
Ch46 §46.8–46.11.  Each fleet member is associated with a
``CalibrationTrace`` that accumulates samples of (accuracy, latency, trust)
over time.  Periodic calibration runs summarise these samples into a
``CalibrationReport`` that drives bid weighting and member prioritisation.

Architecture overview
---------------------
``CalibrationSample``
    A single immutable observation collected after each bid evaluation round.

``CalibrationReport``
    A frozen summary produced at the end of a calibration run.

``AccuracyEstimator``
    Produces a single accuracy estimate from a trace using exponential
    weighted moving average (EWMA) and can detect trends via linear
    regression on recent samples.

``LatencyTracker``
    Computes percentile statistics from the latency history of a trace and
    detects whether latency is trending upward.

``TrustDecay``
    Models the evolution of trust as accuracy changes over time, including
    both decay (on bad performance) and recovery (on good performance).

``CalibrationScheduler``
    Decides *when* each member needs re-calibration based on trace age and
    status.

``CalibrationEngine``
    Orchestrates the full calibration pipeline for a single member.

``CrossMemberCalibrator``
    Runs calibration across the entire fleet, computing pairwise agreement
    and detecting outlier members.

All external jugeo imports are guarded with try/except so the module can be
used standalone or in partial installations.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Guarded external imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        CalibrationTrace,
        CalibrationStatus,
        BidDelta,
        CompetitiveBid,
    )
except Exception:
    CalibrationTrace = Any  # type: ignore[assignment,misc]
    CalibrationStatus = Any  # type: ignore[assignment,misc]
    BidDelta = Any  # type: ignore[assignment,misc]
    CompetitiveBid = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.fleet import FleetCalibration, FleetMember
except Exception:
    FleetCalibration = Any  # type: ignore[assignment,misc]
    FleetMember = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel, TrustComposition, TrustAttenuation
except Exception:
    TrustLevel = Any  # type: ignore[assignment,misc]
    TrustComposition = Any  # type: ignore[assignment,misc]
    TrustAttenuation = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum number of samples required for a meaningful calibration report.
MIN_SAMPLES_FOR_CALIBRATION: int = 3

#: Maximum samples retained in a trace's history lists (rolling window).
MAX_HISTORY_LENGTH: int = 200

#: Default accuracy assumed when a trace has no history.
DEFAULT_ACCURACY: float = 0.5

#: Default latency (seconds) assumed when a trace has no history.
DEFAULT_LATENCY: float = 1.0

#: Default trust level assumed when a trace has no history.
DEFAULT_TRUST: float = 0.5

#: Status string constants – mirrors CalibrationStatus enum values.
STATUS_FRESH: str = "FRESH"
STATUS_STALE: str = "STALE"
STATUS_DEGRADED: str = "DEGRADED"
STATUS_INVALID: str = "INVALID"

#: The EWMA smoothing factor used by AccuracyEstimator.
DEFAULT_EWMA_ALPHA: float = 0.3

#: Number of recent samples used for trend computation.
TREND_WINDOW: int = 10

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """A single immutable calibration observation for one fleet member.

    Samples are collected at the end of each bid evaluation round and appended
    to the member's ``CalibrationTrace``.

    Attributes
    ----------
    member_id:
        Identifier of the fleet member this sample was collected for.
    accuracy:
        Fraction of bids that were evaluated correctly in this round, in
        the range [0, 1].
    latency:
        Round-trip evaluation latency in seconds (non-negative).
    trust:
        Current trust level of the member at sample time, in [0, 1].
    timestamp:
        Unix epoch seconds when the sample was collected.
    bid_id:
        Optional reference to the specific bid that triggered this sample.
    """

    member_id: str
    accuracy: float
    latency: float
    trust: float
    timestamp: float = field(default_factory=time.time)
    bid_id: str = ""

    def is_valid(self) -> bool:
        """Return ``True`` when all fields are within their valid ranges.

        Validity criteria:
        * ``accuracy`` in [0, 1]
        * ``latency`` >= 0
        * ``trust`` in [0, 1]
        * ``member_id`` is non-empty
        """
        return (
            bool(self.member_id)
            and 0.0 <= self.accuracy <= 1.0
            and self.latency >= 0.0
            and 0.0 <= self.trust <= 1.0
        )


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Immutable calibration report produced after a full calibration run.

    The report summarises the statistical properties of a member's recent
    calibration history and includes a recommendation for action.

    Attributes
    ----------
    member_id:
        Identifier of the fleet member being reported on.
    calibration_score:
        Scalar in [0, 1] representing overall calibration quality.  Higher
        is better.
    accuracy_mean:
        Mean accuracy across the calibration window.
    accuracy_std:
        Standard deviation of accuracy; high values indicate instability.
    latency_mean:
        Mean latency in seconds.
    latency_p95:
        95th-percentile latency in seconds.
    trust_mean:
        Mean trust level across the calibration window.
    status:
        One of ``FRESH``, ``STALE``, ``DEGRADED``, ``INVALID`` (or a
        ``CalibrationStatus`` enum value when models are available).
    generated_at:
        Unix epoch seconds when the report was generated.
    recommendation:
        Human-readable action recommendation (e.g. "Increase calibration
        frequency", "Investigate latency spike").
    """

    member_id: str
    calibration_score: float
    accuracy_mean: float
    accuracy_std: float
    latency_mean: float
    latency_p95: float
    trust_mean: float
    status: Any  # CalibrationStatus or str
    generated_at: float = field(default_factory=time.time)
    recommendation: str = ""

    def to_dict(self) -> dict:
        """Serialise the report to a plain dictionary."""
        status_val = self.status
        if hasattr(status_val, "name"):
            status_val = status_val.name
        return {
            "member_id": self.member_id,
            "calibration_score": self.calibration_score,
            "accuracy_mean": self.accuracy_mean,
            "accuracy_std": self.accuracy_std,
            "latency_mean": self.latency_mean,
            "latency_p95": self.latency_p95,
            "trust_mean": self.trust_mean,
            "status": status_val,
            "generated_at": self.generated_at,
            "recommendation": self.recommendation,
        }

    def is_healthy(self) -> bool:
        """Return ``True`` when the report indicates a healthy member.

        A member is considered healthy when:
        * ``calibration_score > 0.5``
        * status is either ``FRESH`` or ``STALE`` (not ``DEGRADED`` or
          ``INVALID``).
        """
        if self.calibration_score <= 0.5:
            return False
        status_val = self.status
        if hasattr(status_val, "name"):
            status_val = status_val.name
        return str(status_val) in (STATUS_FRESH, STATUS_STALE)


# ---------------------------------------------------------------------------
# AccuracyEstimator
# ---------------------------------------------------------------------------


class AccuracyEstimator:
    """Produce accuracy estimates from calibration trace history.

    Uses exponentially weighted moving average (EWMA) to give more weight
    to recent observations.  Also computes linear-regression trends to
    detect improving or degrading accuracy over time.

    Parameters
    ----------
    decay_factor:
        The EWMA decay factor (*α* in the formula).  Values closer to 1
        give more weight to recent observations.
    """

    def __init__(self, decay_factor: float = 0.95) -> None:
        # We store decay_factor but use 1 - decay_factor as the EWMA alpha
        # (the "learning rate"), following the convention that higher
        # decay_factor => slower adaptation.
        self.decay_factor = decay_factor
        self._alpha: float = 1.0 - decay_factor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(self, trace: Any) -> float:
        """Compute an EWMA accuracy estimate from *trace*.

        Falls back to ``DEFAULT_ACCURACY`` when the trace has an empty
        ``accuracy_history``.

        Parameters
        ----------
        trace:
            A ``CalibrationTrace`` (or duck-typed equivalent) with an
            ``accuracy_history`` attribute (list of floats).
        """
        history = _safe_list(trace, "accuracy_history")
        if not history:
            return DEFAULT_ACCURACY
        return self._ewma(history, self._alpha)

    def estimate_from_samples(self, samples: list[CalibrationSample]) -> float:
        """Compute an EWMA accuracy estimate directly from a list of samples.

        Invalid samples (where ``is_valid()`` returns ``False``) are excluded.

        Parameters
        ----------
        samples:
            List of ``CalibrationSample`` instances.
        """
        valid_samples = [s for s in samples if s.is_valid()]
        if not valid_samples:
            return DEFAULT_ACCURACY
        values = [s.accuracy for s in valid_samples]
        return self._ewma(values, self._alpha)

    def trend(self, trace: Any) -> float:
        """Compute the linear regression slope over the last ``TREND_WINDOW`` accuracy samples.

        Returns a positive float when accuracy is improving, negative when
        degrading, and 0.0 when insufficient data is available.

        The slope is computed per-sample-index (not per-second), so it
        represents the expected accuracy change per additional observation.
        """
        history = _safe_list(trace, "accuracy_history")
        window = history[-TREND_WINDOW:]
        if len(window) < 2:
            return 0.0
        return _linear_slope(list(range(len(window))), window)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ewma(self, values: list[float], alpha: float) -> float:
        """Compute the EWMA of *values* with smoothing factor *alpha*.

        Formula: S_t = alpha * X_t + (1 - alpha) * S_{t-1}

        The series is initialised with the first value.

        Parameters
        ----------
        values:
            Ordered list of observations (oldest first).
        alpha:
            Smoothing factor in (0, 1].  Higher values make the EWMA
            more responsive to recent observations.
        """
        if not values:
            return DEFAULT_ACCURACY
        smoothed = float(values[0])
        for v in values[1:]:
            smoothed = alpha * float(v) + (1.0 - alpha) * smoothed
        return _clamp(smoothed, 0.0, 1.0)


# ---------------------------------------------------------------------------
# LatencyTracker
# ---------------------------------------------------------------------------


class LatencyTracker:
    """Compute latency statistics from calibration trace history.

    Parameters
    ----------
    percentile_target:
        The primary percentile to track (default 0.95 = P95).
    """

    def __init__(self, percentile_target: float = 0.95) -> None:
        self.percentile_target = percentile_target

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def track(self, trace: Any) -> dict:
        """Compute full latency statistics from *trace*.

        Returns a dictionary with keys: ``mean``, ``std``, ``p50``, ``p95``,
        ``p99``.

        All values are in the same unit as the stored latency values
        (typically seconds).  When insufficient data is available, values
        default to ``DEFAULT_LATENCY``.
        """
        history = _safe_list(trace, "latency_history")
        if not history:
            return {
                "mean": DEFAULT_LATENCY,
                "std": 0.0,
                "p50": DEFAULT_LATENCY,
                "p95": DEFAULT_LATENCY,
                "p99": DEFAULT_LATENCY,
            }
        vals = [float(v) for v in history]
        mean_val = sum(vals) / len(vals)
        std_val = (
            statistics.stdev(vals) if len(vals) > 1 else 0.0
        )
        return {
            "mean": mean_val,
            "std": std_val,
            "p50": self._percentile(vals, 0.50),
            "p95": self._percentile(vals, 0.95),
            "p99": self._percentile(vals, 0.99),
        }

    def predict_next(self, trace: Any) -> float:
        """Predict the next latency value via linear extrapolation.

        Uses the last ``TREND_WINDOW`` samples.  Falls back to the mean when
        there is insufficient history for trend computation.
        """
        history = _safe_list(trace, "latency_history")
        if not history:
            return DEFAULT_LATENCY
        window = [float(v) for v in history[-TREND_WINDOW:]]
        if len(window) < 2:
            return window[0]
        slope = _linear_slope(list(range(len(window))), window)
        # Extrapolate one step beyond the last observation.
        return _clamp(window[-1] + slope, 0.0, 1e6)

    def is_degrading(self, trace: Any, threshold: float = 0.1) -> bool:
        """Return ``True`` when latency is trending up by more than *threshold* per sample.

        A latency trend greater than *threshold* per sample indicates the
        member is becoming slower over time and may need attention.

        Parameters
        ----------
        trace:
            The calibration trace to inspect.
        threshold:
            The per-sample latency increase (in trace units) above which
            the member is considered to be degrading.
        """
        history = _safe_list(trace, "latency_history")
        window = [float(v) for v in history[-TREND_WINDOW:]]
        if len(window) < 2:
            return False
        slope = _linear_slope(list(range(len(window))), window)
        return slope > threshold

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _percentile(self, values: list[float], p: float) -> float:
        """Compute the *p*-th percentile of *values* using linear interpolation.

        Parameters
        ----------
        values:
            Non-empty list of floats.
        p:
            Percentile in [0, 1].
        """
        if not values:
            return DEFAULT_LATENCY
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        index = p * (n - 1)
        lo = int(math.floor(index))
        hi = int(math.ceil(index))
        if lo == hi:
            return sorted_vals[lo]
        frac = index - lo
        return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


# ---------------------------------------------------------------------------
# TrustDecay
# ---------------------------------------------------------------------------


class TrustDecay:
    """Model the evolution of trust as a function of accuracy over time.

    Trust decays when accuracy is low and recovers when accuracy is high.
    The rates are controlled by ``decay_rate`` and ``recovery_rate``
    respectively, following the model in theory2.tex Ch46 §46.10.

    Parameters
    ----------
    decay_rate:
        Rate coefficient for trust decay.  Higher values cause faster
        decay on poor performance.
    recovery_rate:
        Rate coefficient for trust recovery.  Higher values cause faster
        recovery on good performance.
    """

    def __init__(
        self,
        decay_rate: float = 0.05,
        recovery_rate: float = 0.02,
    ) -> None:
        self.decay_rate = decay_rate
        self.recovery_rate = recovery_rate

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_decay(self, current_trust: float, accuracy_drop: float) -> float:
        """Apply a single decay step and return the new trust level.

        The decay amount is proportional to the accuracy drop and the
        current trust level, ensuring trust cannot go negative:

            new_trust = current_trust - decay_rate * (1 - accuracy) * current_trust

        where ``accuracy = 1 - accuracy_drop``.

        Parameters
        ----------
        current_trust:
            Trust value before decay, in [0, 1].
        accuracy_drop:
            The (1 - accuracy) value, i.e. the error rate.  In [0, 1].
        """
        current_trust = _clamp(float(current_trust), 0.0, 1.0)
        accuracy_drop = _clamp(float(accuracy_drop), 0.0, 1.0)
        delta = self.decay_rate * accuracy_drop * current_trust
        return _clamp(current_trust - delta, 0.0, 1.0)

    def apply_recovery(self, current_trust: float, accuracy: float) -> float:
        """Apply a single recovery step and return the new trust level.

        The recovery amount is proportional to the accuracy and the room
        remaining for trust to grow:

            new_trust = current_trust + recovery_rate * accuracy * (1 - current_trust)

        Parameters
        ----------
        current_trust:
            Trust value before recovery, in [0, 1].
        accuracy:
            The accuracy of the member, in [0, 1].
        """
        current_trust = _clamp(float(current_trust), 0.0, 1.0)
        accuracy = _clamp(float(accuracy), 0.0, 1.0)
        delta = self.recovery_rate * accuracy * (1.0 - current_trust)
        return _clamp(current_trust + delta, 0.0, 1.0)

    def project(self, trace: Any, steps: int = 10) -> list[float]:
        """Simulate a trust trajectory for *steps* future time steps.

        Uses the most recent accuracy value from the trace to drive the
        simulation.  If the most recent accuracy is above 0.5, recovery is
        applied; otherwise decay is applied.

        Parameters
        ----------
        trace:
            The calibration trace to project from.
        steps:
            Number of simulation steps to compute.

        Returns
        -------
        list[float]
            Trust values for steps 1 … *steps* (not including the current
            value).
        """
        accuracy_history = _safe_list(trace, "accuracy_history")
        trust_history = _safe_list(trace, "trust_history")

        current_trust = float(trust_history[-1]) if trust_history else DEFAULT_TRUST
        last_accuracy = float(accuracy_history[-1]) if accuracy_history else DEFAULT_ACCURACY

        trajectory: list[float] = []
        trust = current_trust
        for _ in range(steps):
            if last_accuracy >= 0.5:
                trust = self.apply_recovery(trust, last_accuracy)
            else:
                trust = self.apply_decay(trust, 1.0 - last_accuracy)
            trajectory.append(trust)
        return trajectory


# ---------------------------------------------------------------------------
# CalibrationScheduler
# ---------------------------------------------------------------------------


class CalibrationScheduler:
    """Determine when fleet members should be re-calibrated.

    The scheduler assigns a ``CalibrationStatus`` to each trace based on
    its age and can prioritise a list of traces to determine the calibration
    order.

    Parameters
    ----------
    interval_seconds:
        How often (in seconds) a healthy member should be re-calibrated.
    stale_after:
        Age in seconds at which a trace becomes ``STALE``.
    degrade_after:
        Age in seconds at which a ``STALE`` trace becomes ``DEGRADED``.
    """

    def __init__(
        self,
        interval_seconds: float = 3600.0,
        stale_after: float = 7200.0,
        degrade_after: float = 14400.0,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.stale_after = stale_after
        self.degrade_after = degrade_after

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_calibrate(self, trace: Any) -> bool:
        """Return ``True`` when *trace* is due for calibration.

        A trace is due when it has never been calibrated, is ``STALE``,
        is ``DEGRADED``, is ``INVALID``, or is older than
        ``interval_seconds``.
        """
        status = self.update_status(trace)
        status_str = str(status.name if hasattr(status, "name") else status)
        if status_str in (STATUS_DEGRADED, STATUS_INVALID):
            return True
        last_calibrated = float(_safe_attr(trace, "last_calibrated_at", 0.0) or 0.0)
        age = time.time() - last_calibrated
        return age >= self.interval_seconds

    def next_calibration_time(self, trace: Any) -> float:
        """Return the Unix timestamp of the next scheduled calibration.

        When the trace is already due (``should_calibrate`` is ``True``),
        returns the current time.
        """
        if self.should_calibrate(trace):
            return time.time()
        last_calibrated = float(_safe_attr(trace, "last_calibrated_at", 0.0) or 0.0)
        return last_calibrated + self.interval_seconds

    def update_status(self, trace: Any) -> Any:
        """Determine and return the current ``CalibrationStatus`` for *trace*.

        Status transitions:
        * No calibration ever performed → ``INVALID``
        * Age < stale_after → ``FRESH``
        * stale_after <= age < degrade_after → ``STALE``
        * age >= degrade_after → ``DEGRADED``
        """
        last_calibrated = _safe_attr(trace, "last_calibrated_at", None)
        if last_calibrated is None or float(last_calibrated or 0.0) == 0.0:
            return STATUS_INVALID

        age = time.time() - float(last_calibrated)

        if age >= self.degrade_after:
            return STATUS_DEGRADED
        elif age >= self.stale_after:
            return STATUS_STALE
        else:
            return STATUS_FRESH

    def prioritize(self, traces: list[Any]) -> list[Any]:
        """Return *traces* sorted from most to least urgent for calibration.

        Urgency is determined by age since last calibration: older traces
        are more urgent.  Traces with ``INVALID`` status are placed first.
        """

        def urgency(t: Any) -> float:
            last = float(_safe_attr(t, "last_calibrated_at", 0.0) or 0.0)
            # Larger time since calibration → higher urgency.
            return time.time() - last

        return sorted(traces, key=urgency, reverse=True)


# ---------------------------------------------------------------------------
# CalibrationEngine
# ---------------------------------------------------------------------------


class CalibrationEngine:
    """Orchestrate the full calibration pipeline for a single fleet member.

    The engine combines accuracy estimation, latency tracking, and trust
    decay into a single ``CalibrationReport``.

    Parameters
    ----------
    scheduler:
        Optional ``CalibrationScheduler``; a default is constructed if None.
    accuracy_estimator:
        Optional ``AccuracyEstimator``; a default is constructed if None.
    latency_tracker:
        Optional ``LatencyTracker``; a default is constructed if None.
    trust_decay:
        Optional ``TrustDecay``; a default is constructed if None.
    """

    def __init__(
        self,
        scheduler: Any | None = None,
        accuracy_estimator: Any | None = None,
        latency_tracker: Any | None = None,
        trust_decay: Any | None = None,
    ) -> None:
        self.scheduler = scheduler if scheduler is not None else CalibrationScheduler()
        self.accuracy_estimator = (
            accuracy_estimator
            if accuracy_estimator is not None
            else AccuracyEstimator()
        )
        self.latency_tracker = (
            latency_tracker if latency_tracker is not None else LatencyTracker()
        )
        self.trust_decay = trust_decay if trust_decay is not None else TrustDecay()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calibrate(self, trace: Any) -> CalibrationReport:
        """Run the full calibration pipeline for *trace* and return a report.

        Steps:
        1. Compute accuracy statistics (mean, std, EWMA estimate, trend).
        2. Compute latency statistics (mean, P95, degrading flag).
        3. Compute trust statistics (mean, trend).
        4. Determine calibration status.
        5. Compute an overall calibration score.
        6. Generate a recommendation.

        Parameters
        ----------
        trace:
            A ``CalibrationTrace`` (or duck-typed equivalent) with
            ``accuracy_history``, ``latency_history``, ``trust_history``,
            and optional ``last_calibrated_at`` attributes.
        """
        accuracy_history = [float(v) for v in _safe_list(trace, "accuracy_history")]
        latency_history = [float(v) for v in _safe_list(trace, "latency_history")]
        trust_history = [float(v) for v in _safe_list(trace, "trust_history")]

        # --- Accuracy ---
        acc_mean = (sum(accuracy_history) / len(accuracy_history)) if accuracy_history else DEFAULT_ACCURACY
        acc_std = statistics.stdev(accuracy_history) if len(accuracy_history) > 1 else 0.0
        acc_estimate = self.accuracy_estimator.estimate(trace)
        acc_trend = self.accuracy_estimator.trend(trace)

        # --- Latency ---
        lat_stats = self.latency_tracker.track(trace)
        lat_mean = lat_stats["mean"]
        lat_p95 = lat_stats["p95"]
        lat_degrading = self.latency_tracker.is_degrading(trace)

        # --- Trust ---
        trust_mean = (sum(trust_history) / len(trust_history)) if trust_history else DEFAULT_TRUST

        # --- Status ---
        status = self.scheduler.update_status(trace)

        # --- Calibration score ---
        # Combines accuracy estimate, trust mean, and latency normalised penalty.
        # Score in [0, 1].
        lat_penalty = _clamp(lat_mean / 10.0, 0.0, 0.5)  # 10s latency → full penalty
        raw_score = acc_estimate * 0.5 + trust_mean * 0.3 + (0.2 - lat_penalty * 0.4)
        calibration_score = _clamp(raw_score, 0.0, 1.0)

        # --- Build report data for recommendation ---
        report_data = {
            "accuracy_mean": acc_mean,
            "accuracy_std": acc_std,
            "accuracy_estimate": acc_estimate,
            "accuracy_trend": acc_trend,
            "latency_mean": lat_mean,
            "latency_p95": lat_p95,
            "latency_degrading": lat_degrading,
            "trust_mean": trust_mean,
            "calibration_score": calibration_score,
            "status": status,
            "sample_count": len(accuracy_history),
        }
        recommendation = self._generate_recommendation(report_data)

        member_id = str(_safe_attr(trace, "member_id", "unknown"))
        return CalibrationReport(
            member_id=member_id,
            calibration_score=calibration_score,
            accuracy_mean=acc_mean,
            accuracy_std=acc_std,
            latency_mean=lat_mean,
            latency_p95=lat_p95,
            trust_mean=trust_mean,
            status=status,
            generated_at=time.time(),
            recommendation=recommendation,
        )

    def calibrate_from_bids(
        self,
        member_id: str,
        bids: list[Any],
        outcomes: list[bool],
    ) -> Any:
        """Build a ``CalibrationTrace`` from bid history and ground-truth outcomes.

        Each bid in *bids* is paired with the corresponding boolean in
        *outcomes* (True = bid was accepted/correct).  The trace is populated
        with accuracy, latency, and trust estimates derived from the bids.

        Parameters
        ----------
        member_id:
            ID of the fleet member.
        bids:
            List of ``CompetitiveBid`` objects (or duck-typed equivalents).
        outcomes:
            List of booleans parallel to *bids*.

        Returns
        -------
        A trace object (plain namespace when models are unavailable).
        """
        if len(bids) != len(outcomes):
            raise ValueError(
                f"bids (len={len(bids)}) and outcomes (len={len(outcomes)}) must be the same length"
            )

        accuracy_history: list[float] = []
        latency_history: list[float] = []
        trust_history: list[float] = []

        for bid, outcome in zip(bids, outcomes):
            accuracy_history.append(1.0 if outcome else 0.0)
            latency_history.append(float(_safe_attr(bid, "latency", DEFAULT_LATENCY)))
            trust_history.append(float(_safe_attr(bid, "trust_score", DEFAULT_TRUST)))

        trace = _SimpleNamespace(
            member_id=member_id,
            accuracy_history=accuracy_history,
            latency_history=latency_history,
            trust_history=trust_history,
            last_calibrated_at=time.time(),
        )
        return trace

    def add_sample(self, trace: Any, sample: CalibrationSample) -> None:
        """Append *sample* to *trace*'s rolling history lists.

        Automatically trims history to ``MAX_HISTORY_LENGTH`` to keep memory
        bounded.

        Parameters
        ----------
        trace:
            Mutable calibration trace.
        sample:
            The new observation to add.
        """
        if not sample.is_valid():
            logger.warning("Skipping invalid CalibrationSample for member %s", sample.member_id)
            return

        acc_history = _safe_list(trace, "accuracy_history")
        lat_history = _safe_list(trace, "latency_history")
        tru_history = _safe_list(trace, "trust_history")

        acc_history.append(sample.accuracy)
        lat_history.append(sample.latency)
        tru_history.append(sample.trust)

        # Enforce rolling window.
        if len(acc_history) > MAX_HISTORY_LENGTH:
            acc_history[:] = acc_history[-MAX_HISTORY_LENGTH:]
        if len(lat_history) > MAX_HISTORY_LENGTH:
            lat_history[:] = lat_history[-MAX_HISTORY_LENGTH:]
        if len(tru_history) > MAX_HISTORY_LENGTH:
            tru_history[:] = tru_history[-MAX_HISTORY_LENGTH:]

        _try_set(trace, "accuracy_history", acc_history)
        _try_set(trace, "latency_history", lat_history)
        _try_set(trace, "trust_history", tru_history)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate_recommendation(self, report_data: dict) -> str:
        """Generate a human-readable recommendation string from report data.

        Uses a rule-based approach to surface the most actionable issue.

        Priority (highest first):
        1. Insufficient samples → "Collect more samples before calibrating"
        2. INVALID status → "Member has never been calibrated; run initial calibration"
        3. Accuracy < 0.4 → "Low accuracy detected; review member configuration"
        4. Latency degrading → "Latency trend is rising; investigate infrastructure"
        5. Low calibration score → "Overall calibration score is low; reduce load"
        6. Negative accuracy trend → "Accuracy is declining; monitor closely"
        7. Default → "Calibration is healthy"
        """
        count = int(report_data.get("sample_count", 0))
        if count < MIN_SAMPLES_FOR_CALIBRATION:
            return "Collect more samples before calibrating"

        status = report_data.get("status", STATUS_FRESH)
        status_str = str(status.name if hasattr(status, "name") else status)
        if status_str == STATUS_INVALID:
            return "Member has never been calibrated; run initial calibration"

        acc_mean = float(report_data.get("accuracy_mean", DEFAULT_ACCURACY))
        if acc_mean < 0.4:
            return f"Low accuracy detected ({acc_mean:.2%}); review member configuration"

        if report_data.get("latency_degrading", False):
            lat_mean = float(report_data.get("latency_mean", DEFAULT_LATENCY))
            return (
                f"Latency trend is rising (mean={lat_mean:.3f}s); "
                "investigate infrastructure"
            )

        score = float(report_data.get("calibration_score", 1.0))
        if score < 0.5:
            return f"Overall calibration score is low ({score:.2f}); consider reducing load"

        trend = float(report_data.get("accuracy_trend", 0.0))
        if trend < -0.02:
            return f"Accuracy is declining (slope={trend:.4f}); monitor closely"

        return "Calibration is healthy"


# ---------------------------------------------------------------------------
# CrossMemberCalibrator
# ---------------------------------------------------------------------------


class CrossMemberCalibrator:
    """Calibrate fleet members relative to each other's performance.

    Cross-calibration identifies outliers and measures consensus accuracy
    across the fleet.

    Parameters
    ----------
    engine:
        Optional ``CalibrationEngine``; a default is constructed if None.
    """

    def __init__(self, engine: Any | None = None) -> None:
        self.engine = engine if engine is not None else CalibrationEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cross_calibrate(self, traces: list[Any]) -> dict[str, CalibrationReport]:
        """Calibrate each member relative to peer performance.

        After individual calibration, adjusts each member's
        ``calibration_score`` by normalising against the fleet mean.
        Members significantly above the mean receive a bonus; those below
        receive a penalty.

        Parameters
        ----------
        traces:
            List of ``CalibrationTrace`` objects, one per fleet member.

        Returns
        -------
        dict[str, CalibrationReport]
            Mapping from ``member_id`` to adjusted ``CalibrationReport``.
        """
        if not traces:
            return {}

        # Individual calibration.
        reports: dict[str, CalibrationReport] = {}
        for trace in traces:
            mid = str(_safe_attr(trace, "member_id", "unknown"))
            try:
                report = self.engine.calibrate(trace)
                reports[mid] = report
            except Exception as exc:
                logger.warning("Calibration failed for member %s: %s", mid, exc)

        if not reports:
            return reports

        # Compute fleet-mean calibration score.
        scores = [r.calibration_score for r in reports.values()]
        fleet_mean = sum(scores) / len(scores)
        fleet_std = statistics.stdev(scores) if len(scores) > 1 else 0.0

        # Adjust scores relative to fleet mean.
        adjusted: dict[str, CalibrationReport] = {}
        for mid, report in reports.items():
            if fleet_std > 0:
                z = (report.calibration_score - fleet_mean) / fleet_std
                # Apply a small peer-normalisation adjustment (±5% max).
                adjustment = _clamp(z * 0.025, -0.05, 0.05)
            else:
                adjustment = 0.0
            new_score = _clamp(report.calibration_score + adjustment, 0.0, 1.0)
            # Rebuild frozen dataclass with adjusted score.
            adjusted[mid] = CalibrationReport(
                member_id=report.member_id,
                calibration_score=new_score,
                accuracy_mean=report.accuracy_mean,
                accuracy_std=report.accuracy_std,
                latency_mean=report.latency_mean,
                latency_p95=report.latency_p95,
                trust_mean=report.trust_mean,
                status=report.status,
                generated_at=report.generated_at,
                recommendation=report.recommendation,
            )
        return adjusted

    def agreement_matrix(
        self, traces: list[Any]
    ) -> dict[tuple[str, str], float]:
        """Compute pairwise agreement scores between all fleet members.

        Agreement between members A and B is defined as:

            agreement(A, B) = 1 - |accuracy_A - accuracy_B|

        where accuracy is the EWMA estimate from their respective traces.

        Parameters
        ----------
        traces:
            List of calibration traces.

        Returns
        -------
        dict[tuple[str, str], float]
            Mapping from ``(member_id_a, member_id_b)`` to agreement score.
        """
        estimator = AccuracyEstimator()
        member_accuracy: dict[str, float] = {}
        for trace in traces:
            mid = str(_safe_attr(trace, "member_id", "unknown"))
            member_accuracy[mid] = estimator.estimate(trace)

        matrix: dict[tuple[str, str], float] = {}
        members = list(member_accuracy.keys())
        for i, a in enumerate(members):
            for j, b in enumerate(members):
                if i >= j:
                    continue  # skip self-pairs and duplicates
                agreement = 1.0 - abs(member_accuracy[a] - member_accuracy[b])
                matrix[(a, b)] = _clamp(agreement, 0.0, 1.0)
        return matrix

    def consensus_accuracy(self, traces: list[Any]) -> float:
        """Compute the trust-weighted mean accuracy across all members.

        Members with higher mean trust contribute more to the consensus
        estimate.

        Parameters
        ----------
        traces:
            List of calibration traces.

        Returns
        -------
        float
            Weighted mean accuracy in [0, 1].
        """
        if not traces:
            return DEFAULT_ACCURACY

        estimator = AccuracyEstimator()
        weighted_sum = 0.0
        weight_total = 0.0
        for trace in traces:
            acc = estimator.estimate(trace)
            trust_history = _safe_list(trace, "trust_history")
            trust = (
                sum(float(v) for v in trust_history) / len(trust_history)
                if trust_history
                else DEFAULT_TRUST
            )
            weighted_sum += acc * trust
            weight_total += trust

        return _safe_div(weighted_sum, weight_total, DEFAULT_ACCURACY)

    def outlier_members(
        self, traces: list[Any], threshold: float = 2.0
    ) -> list[str]:
        """Return member IDs whose calibration score is more than *threshold* std devs from the fleet mean.

        Useful for identifying members that need human review.

        Parameters
        ----------
        traces:
            List of calibration traces.
        threshold:
            Z-score threshold.  Members with |z| > threshold are returned.

        Returns
        -------
        list[str]
            List of member IDs that are statistical outliers.
        """
        if len(traces) < 2:
            return []

        reports = self.cross_calibrate(traces)
        scores = {mid: r.calibration_score for mid, r in reports.items()}
        values = list(scores.values())
        mean_score = sum(values) / len(values)
        std_score = statistics.stdev(values) if len(values) > 1 else 0.0

        if std_score == 0.0:
            return []

        outliers: list[str] = []
        for mid, score in scores.items():
            z = abs(score - mean_score) / std_score
            if z > threshold:
                outliers.append(mid)
        return outliers


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _SimpleNamespace:
    """Minimal mutable namespace used as a fallback for missing dataclasses."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)


def _safe_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Return ``getattr(obj, attr, default)`` without raising.

    Handles both attribute access and dict access transparently.
    """
    if isinstance(obj, dict):
        return obj.get(attr, default)
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _safe_list(obj: Any, attr: str) -> list:
    """Return the list attribute *attr* of *obj*, or an empty list.

    Always returns a mutable list copy to avoid side-effects when the
    caller modifies the result.
    """
    val = _safe_attr(obj, attr, None)
    if isinstance(val, list):
        return list(val)
    return []


def _try_set(obj: Any, attr: str, value: Any) -> None:
    """Attempt to set *attr* = *value* on *obj*, silently ignoring failures."""
    if isinstance(obj, dict):
        obj[attr] = value
        return
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return ``numerator / denominator`` or *default* when denominator is zero."""
    if denominator == 0.0:
        return default
    return numerator / denominator


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    """Compute the ordinary least-squares slope of *ys* regressed on *xs*.

    Returns 0.0 when there are fewer than 2 points or when the variance of
    *xs* is zero.

    Parameters
    ----------
    xs:
        Independent variable values.
    ys:
        Dependent variable values, parallel to *xs*.
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def build_calibration_pipeline(
    interval_seconds: float = 3600.0,
    decay_rate: float = 0.05,
    recovery_rate: float = 0.02,
) -> tuple[CalibrationEngine, CrossMemberCalibrator]:
    """Construct a ready-to-use calibration pipeline with default components.

    Returns a 2-tuple: ``(engine, cross_calibrator)``.

    Example
    -------
    >>> engine, cross_cal = build_calibration_pipeline()
    >>> trace = engine.calibrate_from_bids("m1", bids, outcomes)
    >>> report = engine.calibrate(trace)
    >>> print(report.recommendation)
    """
    scheduler = CalibrationScheduler(interval_seconds=interval_seconds)
    accuracy_est = AccuracyEstimator()
    latency_trk = LatencyTracker()
    trust_dec = TrustDecay(decay_rate=decay_rate, recovery_rate=recovery_rate)
    engine = CalibrationEngine(
        scheduler=scheduler,
        accuracy_estimator=accuracy_est,
        latency_tracker=latency_trk,
        trust_decay=trust_dec,
    )
    cross_cal = CrossMemberCalibrator(engine=engine)
    return engine, cross_cal


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import random as _random

    logging.basicConfig(level=logging.INFO)

    engine, cross_cal = build_calibration_pipeline()

    # Create synthetic bids and outcomes for three members.
    member_ids = ["alpha", "beta", "gamma"]
    traces = []
    for mid in member_ids:
        n = 30
        bids = [
            _SimpleNamespace(
                bid_id=str(uuid.uuid4()),
                latency=_random.uniform(0.1, 2.0),
                trust_score=_random.uniform(0.4, 0.9),
                semantic_score=_random.random(),
                bid_value=_random.random(),
                uncertainty=_random.uniform(0.0, 0.3),
            )
            for _ in range(n)
        ]
        outcomes = [_random.random() > 0.3 for _ in range(n)]
        trace = engine.calibrate_from_bids(mid, bids, outcomes)
        traces.append(trace)

    # Individual calibration.
    for t in traces:
        report = engine.calibrate(t)
        print(f"[{report.member_id}] score={report.calibration_score:.3f} "
              f"status={report.status} rec='{report.recommendation}'")

    # Cross-calibration and outlier detection.
    outliers = cross_cal.outlier_members(traces)
    print(f"Outlier members: {outliers}")
    consensus = cross_cal.consensus_accuracy(traces)
    print(f"Consensus accuracy: {consensus:.4f}")
