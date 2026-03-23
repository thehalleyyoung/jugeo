"""Convergence monitoring and certification for JuGeo semantic control (theory2.tex Ch44).

This module provides the full convergence-monitoring stack for the semantic control
layer of JuGeo's orchestration system.  Every component here corresponds to a
concept from theory2.tex Chapter 44 ("Project-scale Convergence in Semantic Sites"):

*   **ConvergenceMetrics** — a frozen snapshot of all measurable convergence signals
    at a single time-step, including the Lyapunov value and rate estimate.
*   **ObligationTracker** — stateful register of outstanding proof obligations and
    their resolutions.  Obligations are the "residual debt" of the semantic site.
*   **CoverageAnalyzer** — multi-dimensional analysis of how well the current state
    covers the target specification, with per-dimension and weighted totals.
*   **ConvergenceRateEstimator** — exponential-smoothing estimator for the rate of
    coverage improvement, used to forecast steps-to-convergence.
*   **DivergenceDetector** — monitors for regression (negative rate) and stalls,
    firing registered alert callbacks when anomalies are detected.
*   **CertificationAuthority** — issues ``ConvergenceCertificate`` objects when all
    convergence criteria are satisfied, subject to validity expiry.
*   **ConvergenceMonitor** — top-level orchestrator for the above components.  It
    consumes ``SemanticControlState`` snapshots, runs all sub-analyses, and provides
    a unified ``report()`` and ``try_certify()`` interface.

References
──────────
*   theory2.tex §44      — Convergence and Certification
*   theory2.tex §44.1   — Lyapunov Functions on Semantic Sites
*   theory2.tex §44.2   — Obligation Pressure and Discharge
*   theory2.tex §44.3   — Coverage Dimensions and Weighted Analysis
*   theory2.tex §44.4   — Rate Estimation and Stall Detection
*   theory2.tex §44.5   — Convergence Certificates and Validity Periods
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# ── Internal JuGeo imports (guarded) ────────────────────────────────────────

try:
    from jugeo.orchestration.semantic_control.models import (
        ConvergenceCertificate,
        ConvergenceMode,
        SemanticControlState,
        SemanticTrajectory,
        StateHealthStatus,
    )
except Exception:  # pragma: no cover
    import enum

    class ConvergenceMode(enum.Enum):  # type: ignore[no-redef]
        GREEDY = "greedy"
        LOOKAHEAD = "lookahead"
        BALANCED = "balanced"
        ADAPTIVE = "adaptive"

    class StateHealthStatus(enum.Enum):  # type: ignore[no-redef]
        HEALTHY = "healthy"
        DEGRADED = "degraded"
        CRITICAL = "critical"

    @dataclass(frozen=True, slots=True)
    class ConvergenceCertificate:  # type: ignore[no-redef]
        cert_id: str
        state_id: str
        coverage_ratio: float
        obligation_count: int
        issued_at: float
        valid_for: float
        evidence: dict

        def is_valid(self) -> bool:
            return not self.is_expired()

        def is_expired(self) -> bool:
            return time.time() > self.issued_at + self.valid_for

        def summary(self) -> str:
            return (
                f"Certificate {self.cert_id}: coverage={self.coverage_ratio:.3f}, "
                f"obligations={self.obligation_count}, "
                f"valid={'yes' if self.is_valid() else 'no'}"
            )

        def to_dict(self) -> dict:
            return {
                "cert_id": self.cert_id,
                "state_id": self.state_id,
                "coverage_ratio": self.coverage_ratio,
                "obligation_count": self.obligation_count,
                "issued_at": self.issued_at,
                "valid_for": self.valid_for,
                "evidence": self.evidence,
            }

    @dataclass(slots=True)
    class SemanticControlState:  # type: ignore[no-redef]
        state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        cover_ids: list[str] = field(default_factory=list)
        context_ids: list[str] = field(default_factory=list)
        section_ids: list[str] = field(default_factory=list)
        treaty_ids: list[str] = field(default_factory=list)
        obligation_ids: list[str] = field(default_factory=list)
        channel_ids: list[str] = field(default_factory=list)
        budget: dict[str, Any] = field(default_factory=dict)
        timestamp: float = field(default_factory=time.time)
        metadata: dict[str, Any] = field(default_factory=dict)

        def is_admissible(self) -> bool:
            return bool(self.cover_ids)

        def coverage_ratio(self) -> float:
            if not self.cover_ids:
                return 0.0
            return len(self.section_ids) / len(self.cover_ids)

        def attainability_score(self) -> float:
            return self.coverage_ratio()

        def to_dict(self) -> dict:
            return {"state_id": self.state_id, "cover_ids": self.cover_ids}

        def health_status(self) -> StateHealthStatus:
            if self.coverage_ratio() >= 0.9:
                return StateHealthStatus.HEALTHY
            if self.coverage_ratio() >= 0.5:
                return StateHealthStatus.DEGRADED
            return StateHealthStatus.CRITICAL

    @dataclass(slots=True)
    class SemanticTrajectory:  # type: ignore[no-redef]
        trajectory_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        states: list[SemanticControlState] = field(default_factory=list)
        moves: list[Any] = field(default_factory=list)
        timestamps: list[float] = field(default_factory=list)

        def append(self, state: SemanticControlState, move: Any = None) -> None:
            self.states.append(state)
            self.moves.append(move)
            self.timestamps.append(time.time())

        def length(self) -> int:
            return len(self.states)

        def is_converging(self) -> bool:
            if len(self.states) < 2:
                return False
            return (
                self.states[-1].coverage_ratio() > self.states[-2].coverage_ratio()
            )

        def latest_state(self) -> SemanticControlState | None:
            return self.states[-1] if self.states else None

        def score_history(self) -> list[float]:
            return [s.coverage_ratio() for s in self.states]

        def export(self) -> dict:
            return {
                "trajectory_id": self.trajectory_id,
                "length": self.length(),
                "scores": self.score_history(),
            }

        def replay(self) -> list[SemanticControlState]:
            return list(self.states)


try:
    from jugeo.orchestration.controller import (  # noqa: F401
        ConvergenceMonitor as _ControllerConvergenceMonitor,
        MoveGenerator,
        MoveHistory,
        MoveKind,
        OrchestratorState,
    )
except Exception:  # pragma: no cover
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier  # noqa: F401
except Exception:  # pragma: no cover
    import enum

    class TrustLevel(enum.Enum):  # type: ignore[no-redef]
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"

    class TrustTier(enum.IntEnum):  # type: ignore[no-redef]
        UNTRUSTED = 0
        PROVISIONAL = 1
        TRUSTED = 2
        CERTIFIED = 3

    @dataclass(frozen=True, slots=True)
    class TrustProfile:  # type: ignore[no-redef]
        level: TrustLevel = TrustLevel.MEDIUM
        tier: TrustTier = TrustTier.PROVISIONAL


# ── Module-level constants ────────────────────────────────────────────────────

log = logging.getLogger(__name__)

#: Default convergence threshold (coverage ratio ≥ this ⇒ converged).
DEFAULT_CONVERGENCE_THRESHOLD: float = 0.95

#: Default validity period for issued certificates, in seconds.
DEFAULT_VALIDITY_PERIOD: float = 300.0

#: Minimum number of observations before rate estimation is meaningful.
MIN_OBSERVATIONS_FOR_RATE: int = 3

#: Maximum age (seconds) for an obligation before it is expired automatically.
DEFAULT_MAX_OBLIGATION_AGE: float = 3600.0

#: Window size for divergence / stall detection.
DIVERGENCE_WINDOW: int = 5

#: A rate below this value (per step) is classified as "stalling".
STALL_RATE_THRESHOLD: float = 1e-4

#: A rate below this value is classified as "diverging".
DIVERGING_RATE_THRESHOLD: float = -1e-4


# ═══════════════════════════════════════════════════════════════════════════════
#  1.  ConvergenceMetrics — immutable snapshot of all convergence signals
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ConvergenceMetrics:
    """Immutable snapshot of all convergence signals at a single time-step.

    ``ConvergenceMetrics`` acts as a tagged summary of one observation of
    the semantic control state.  Being frozen, it is safe to store in
    history lists and compare across epochs without defensive copying.

    Attributes:
        coverage_ratio:    Fraction of cover items for which a section exists (0–1).
        obligation_count:  Number of outstanding, unresolved proof obligations.
        attainability:     Composite attainability score in [0, 1].
        lyapunov_value:    Non-negative Lyapunov function value; 0 ⟺ converged.
        rate_estimate:     Estimated per-step improvement in coverage_ratio.
        step_count:        Number of steps executed when this snapshot was taken.
        timestamp:         Wall-clock time (``time.time()``) of snapshot.

    References
    ──────────
    theory2.tex §44.1 — Lyapunov Functions on Semantic Sites
    """

    coverage_ratio: float
    obligation_count: int
    attainability: float
    lyapunov_value: float
    rate_estimate: float
    step_count: int
    timestamp: float

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_converged(self, threshold: float = DEFAULT_CONVERGENCE_THRESHOLD) -> bool:
        """Return ``True`` when the system is considered converged.

        Convergence requires:
        1.  ``coverage_ratio >= threshold`` (sufficient semantic coverage).
        2.  ``obligation_count == 0``       (no residual proof obligations).
        3.  ``lyapunov_value < 1e-6``       (Lyapunov function effectively zero).

        Args:
            threshold: Coverage ratio above which we declare convergence.

        Returns:
            ``True`` iff all convergence criteria are satisfied.
        """
        return (
            self.coverage_ratio >= threshold
            and self.obligation_count == 0
            and self.lyapunov_value < 1e-6
        )

    # ------------------------------------------------------------------
    # Export / display
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for logging and diagnostics.

        Returns:
            Dict with all field names as string keys.
        """
        return {
            "coverage_ratio": self.coverage_ratio,
            "obligation_count": self.obligation_count,
            "attainability": self.attainability,
            "lyapunov_value": self.lyapunov_value,
            "rate_estimate": self.rate_estimate,
            "step_count": self.step_count,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """Return a compact human-readable one-liner.

        Returns:
            A string like ``"step=42 cov=0.873 oblig=3 L=0.127 rate=+0.012"``.
        """
        sign = "+" if self.rate_estimate >= 0 else ""
        return (
            f"step={self.step_count} "
            f"cov={self.coverage_ratio:.3f} "
            f"oblig={self.obligation_count} "
            f"L={self.lyapunov_value:.4f} "
            f"rate={sign}{self.rate_estimate:.4f}"
        )

    def delta(self, other: ConvergenceMetrics) -> dict[str, float]:
        """Compute signed differences from *other* to *self*.

        Useful for monitoring improvement between consecutive snapshots.

        Args:
            other: The baseline ``ConvergenceMetrics`` to compare against.

        Returns:
            Dict with keys ``coverage_ratio``, ``obligation_count``,
            ``attainability``, ``lyapunov_value``, ``rate_estimate``,
            ``step_count``.
        """
        return {
            "coverage_ratio": self.coverage_ratio - other.coverage_ratio,
            "obligation_count": float(
                self.obligation_count - other.obligation_count
            ),
            "attainability": self.attainability - other.attainability,
            "lyapunov_value": self.lyapunov_value - other.lyapunov_value,
            "rate_estimate": self.rate_estimate - other.rate_estimate,
            "step_count": float(self.step_count - other.step_count),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  2.  ObligationTracker — stateful register of proof obligations
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class ObligationTracker:
    """Stateful register of outstanding proof obligations and their resolutions.

    In theory2.tex §44.2, "obligations" are the residual semantic debts that
    arise when a local section is constructed but its overlap with neighbouring
    sections has not yet been verified.  The tracker maintains two separate
    dictionaries — pending obligations and resolved ones — and supports
    age-based expiry of stale obligations.

    Attributes:
        obligations: Map ``obligation_id → data_dict`` of pending obligations.
        resolved:    Map ``obligation_id → resolution_dict`` for resolved items.
        max_age:     Seconds after which a pending obligation is auto-expired.
    """

    obligations: dict[str, dict] = field(default_factory=dict)
    resolved: dict[str, dict] = field(default_factory=dict)
    max_age: float = DEFAULT_MAX_OBLIGATION_AGE

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, obligation_id: str, data: dict) -> None:
        """Register a new pending obligation.

        If *obligation_id* is already tracked (pending or resolved), this
        call is a no-op to preserve idempotency.

        Args:
            obligation_id: Unique identifier for the obligation.
            data:          Arbitrary metadata (coordinate, rule, source, …).
        """
        if obligation_id in self.obligations or obligation_id in self.resolved:
            log.debug("ObligationTracker.add: %s already tracked, skipping", obligation_id)
            return
        entry = dict(data)
        entry.setdefault("added_at", time.time())
        self.obligations[obligation_id] = entry
        log.debug("ObligationTracker: added obligation %s", obligation_id)

    def resolve(self, obligation_id: str, resolution: dict) -> bool:
        """Mark an obligation as resolved with a resolution record.

        Args:
            obligation_id: The ID of the obligation to resolve.
            resolution:    Evidence or proof that discharged the obligation.

        Returns:
            ``True`` if the obligation was pending and has now been resolved,
            ``False`` if it was not found in the pending set.
        """
        if obligation_id not in self.obligations:
            log.debug(
                "ObligationTracker.resolve: %s not pending", obligation_id
            )
            return False
        rec = dict(self.obligations.pop(obligation_id))
        rec["resolved_at"] = time.time()
        rec["resolution"] = resolution
        self.resolved[obligation_id] = rec
        log.debug("ObligationTracker: resolved obligation %s", obligation_id)
        return True

    def expire_old(self) -> list[str]:
        """Remove obligations older than ``max_age`` seconds.

        Returns:
            List of obligation IDs that were expired and removed.
        """
        now = time.time()
        expired: list[str] = []
        for oid, data in list(self.obligations.items()):
            age = now - data.get("added_at", now)
            if age > self.max_age:
                expired.append(oid)
                del self.obligations[oid]
                log.info("ObligationTracker: expired obligation %s (age=%.1fs)", oid, age)
        return expired

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def pending_count(self) -> int:
        """Return the number of outstanding obligations."""
        return len(self.obligations)

    def resolved_count(self) -> int:
        """Return the number of resolved obligations."""
        return len(self.resolved)

    def is_resolved(self, obligation_id: str) -> bool:
        """Return ``True`` iff *obligation_id* appears in the resolved set.

        Args:
            obligation_id: ID to query.

        Returns:
            ``True`` if resolved, ``False`` if pending or unknown.
        """
        return obligation_id in self.resolved

    def pending_obligations(self) -> list[dict]:
        """Return a list of data dicts for all pending obligations.

        Returns:
            Copies of the obligation data dicts, each augmented with the
            ``obligation_id`` key for convenience.
        """
        result: list[dict] = []
        for oid, data in self.obligations.items():
            entry = dict(data)
            entry["obligation_id"] = oid
            result.append(entry)
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full tracker state for diagnostics.

        Returns:
            Dict with ``pending``, ``resolved``, and ``max_age`` keys.
        """
        return {
            "pending": {k: dict(v) for k, v in self.obligations.items()},
            "resolved": {k: dict(v) for k, v in self.resolved.items()},
            "max_age": self.max_age,
        }

    def summary(self) -> str:
        """Return a compact human-readable status string.

        Returns:
            E.g. ``"ObligationTracker: 3 pending / 17 resolved"``.
        """
        return (
            f"ObligationTracker: {self.pending_count()} pending / "
            f"{self.resolved_count()} resolved"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  3.  CoverageAnalyzer — multi-dimensional coverage analysis
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class CoverageAnalyzer:
    """Multi-dimensional analysis of semantic site coverage.

    The analyzer decomposes overall coverage into per-dimension ratios
    (covers, contexts, sections, treaties, channels) and supports weighted
    aggregation.  The ``weights`` attribute allows downstream components to
    express which dimensions matter most for their convergence criterion.

    Attributes:
        target_coverage: The global target coverage fraction (typically 1.0).
        weights:         Per-dimension weights for ``weighted_coverage``.

    References
    ──────────
    theory2.tex §44.3 — Coverage Dimensions and Weighted Analysis
    """

    target_coverage: float = 1.0
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "covers": 1.0,
            "sections": 1.5,
            "treaties": 1.0,
            "channels": 0.5,
            "contexts": 0.75,
        }
    )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(self, state: SemanticControlState) -> dict[str, float]:
        """Compute per-dimension coverage ratios.

        Dimensions:
        *   ``covers``   — fraction of cover items vs. target_coverage * total.
        *   ``sections`` — fraction of sections vs. cover items.
        *   ``treaties`` — treaty-to-cover ratio (treaties should equal covers − 1
                            for a linear site; here we use a simple ratio).
        *   ``channels`` — fraction of channels that are active (non-empty id list).
        *   ``contexts`` — fraction of contexts vs. cover items.

        Args:
            state: The current semantic control state.

        Returns:
            Dict mapping dimension name → ratio in [0, 1].
        """
        n_covers = max(len(state.cover_ids), 1)
        n_sections = len(state.section_ids)
        n_treaties = len(state.treaty_ids)
        n_channels = len(state.channel_ids)
        n_contexts = len(state.context_ids)

        # Sections vs covers is the primary coverage signal.
        section_ratio = min(n_sections / n_covers, 1.0)
        # Treaties: expect (n_covers − 1) treaties for a path-like cover.
        expected_treaties = max(n_covers - 1, 1)
        treaty_ratio = min(n_treaties / expected_treaties, 1.0)
        # Channels and contexts are secondary signals.
        channel_ratio = min(n_channels / max(n_covers, 1), 1.0)
        context_ratio = min(n_contexts / n_covers, 1.0)
        # Cover ratio: fraction of the specification that is addressed.
        cover_ratio = min(n_covers / max(n_covers, 1), 1.0)  # always 1.0

        return {
            "covers": cover_ratio,
            "sections": section_ratio,
            "treaties": treaty_ratio,
            "channels": channel_ratio,
            "contexts": context_ratio,
        }

    def overall_coverage(self, state: SemanticControlState) -> float:
        """Return the simple mean of all per-dimension coverage ratios.

        Args:
            state: The current semantic control state.

        Returns:
            Mean coverage in [0, 1].
        """
        dim = self.analyze(state)
        if not dim:
            return 0.0
        return sum(dim.values()) / len(dim)

    def coverage_gaps(self, state: SemanticControlState) -> list[str]:
        """Return dimension names where coverage is below ``target_coverage``.

        Args:
            state: The current semantic control state.

        Returns:
            List of dimension names with coverage < target_coverage.
        """
        dim = self.analyze(state)
        return [k for k, v in dim.items() if v < self.target_coverage]

    def weighted_coverage(self, state: SemanticControlState) -> float:
        """Return the weighted average of per-dimension coverage ratios.

        Uses ``self.weights`` to weight dimensions.  Unknown dimensions in
        the analysis result are assigned weight 1.0.

        Args:
            state: The current semantic control state.

        Returns:
            Weighted coverage in [0, 1].
        """
        dim = self.analyze(state)
        if not self.weights:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        for k, v in dim.items():
            w = self.weights.get(k, 1.0)
            weighted_sum += w * v
            total_weight += w
        if total_weight == 0.0:
            return 0.0
        return weighted_sum / total_weight

    def coverage_trend(self, history: list[SemanticControlState]) -> list[float]:
        """Compute the overall coverage at each state in *history*.

        Args:
            history: Ordered list of ``SemanticControlState`` snapshots.

        Returns:
            List of overall coverage values in the same order as *history*.
        """
        return [self.overall_coverage(s) for s in history]


# ═══════════════════════════════════════════════════════════════════════════════
#  4.  ConvergenceRateEstimator — smoothed per-step convergence rate
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class ConvergenceRateEstimator:
    """Exponential-smoothing estimator for the per-step convergence rate.

    At each step the estimator ingests a new metric value (typically the
    coverage ratio) and updates an exponentially-weighted moving average of
    the first differences.  The estimated rate is then used to forecast how
    many additional steps will be needed to reach the convergence threshold.

    Attributes:
        window_size: Maximum number of observations to keep in the history
                     buffer (older values are evicted FIFO).
        smoothing:   EMA smoothing factor α ∈ (0, 1].  Higher ⇒ more
                     weight on recent observations.

    References
    ──────────
    theory2.tex §44.4 — Rate Estimation and Stall Detection
    """

    window_size: int = 10
    smoothing: float = 0.3
    _history: list[float] = field(default_factory=list)
    _ema_rate: float = field(default=0.0)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, metric: float) -> None:
        """Ingest a new metric observation and update the rate estimate.

        Args:
            metric: Latest coverage (or other scalar) value in [0, 1].
        """
        self._history.append(metric)
        # Evict oldest observation when window is full.
        if len(self._history) > self.window_size:
            self._history.pop(0)

        # Update EMA of first differences.
        if len(self._history) >= 2:
            diff = self._history[-1] - self._history[-2]
            self._ema_rate = (
                self.smoothing * diff + (1.0 - self.smoothing) * self._ema_rate
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def estimated_rate(self) -> float:
        """Return the current EMA-smoothed convergence rate (first derivative).

        Returns:
            Estimated per-step change in coverage.  Positive ⟹ improving,
            negative ⟹ diverging.
        """
        return self._ema_rate

    def steps_to_convergence(
        self, current: float, target: float
    ) -> int | None:
        """Estimate steps remaining to reach *target* from *current*.

        Uses the current EMA rate.  Returns ``None`` if the rate is
        non-positive (diverging or stalled).

        Args:
            current: Current metric value.
            target:  Target metric value.

        Returns:
            Estimated integer steps remaining, or ``None`` if not reachable.
        """
        gap = target - current
        if gap <= 0.0:
            return 0
        rate = self._ema_rate
        if rate <= STALL_RATE_THRESHOLD:
            return None
        return max(1, math.ceil(gap / rate))

    def is_stalling(self) -> bool:
        """Return ``True`` if the estimated rate is below the stall threshold.

        Returns:
            ``True`` when |rate| < STALL_RATE_THRESHOLD and the system is
            not yet converged.
        """
        return abs(self._ema_rate) < STALL_RATE_THRESHOLD

    def trend(self) -> str:
        """Classify the current convergence trend.

        Returns:
            One of ``"converged"``, ``"improving"``, ``"stalling"``,
            ``"diverging"``.
        """
        if self._history and self._history[-1] >= DEFAULT_CONVERGENCE_THRESHOLD:
            return "converged"
        if self._ema_rate < DIVERGING_RATE_THRESHOLD:
            return "diverging"
        if self._ema_rate < STALL_RATE_THRESHOLD:
            return "stalling"
        return "improving"

    def history(self) -> list[float]:
        """Return a copy of the internal observation history.

        Returns:
            List of metric values in order of observation (oldest first).
        """
        return list(self._history)


# ═══════════════════════════════════════════════════════════════════════════════
#  5.  DivergenceDetector — monitors for regression and stalls
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class DivergenceDetector:
    """Monitors convergence history for regression (divergence) and stalls.

    When divergence or a stall is detected the detector fires all registered
    alert callbacks with the current state and metrics as arguments.  This
    allows higher-level components (such as the ``ConvergenceMonitor``) to
    react — e.g., by switching the control law or requesting a cover
    refinement.

    Attributes:
        divergence_threshold: Rate below which divergence is declared.
        alert_callbacks:      List of callables to invoke on alerts.

    References
    ──────────
    theory2.tex §44.4 — Rate Estimation and Stall Detection
    """

    divergence_threshold: float = -0.05
    alert_callbacks: list[Callable] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def check(
        self,
        current_metrics: ConvergenceMetrics,
        history: list[ConvergenceMetrics],
    ) -> bool:
        """Return ``True`` if the system is currently diverging.

        Divergence is declared when the EMA rate in *current_metrics* falls
        below ``divergence_threshold``.  Stall detection is a secondary
        check — a stall that persists too long is also classified as
        divergence.

        Args:
            current_metrics: The most recent ``ConvergenceMetrics`` snapshot.
            history:         All previous snapshots (oldest first).

        Returns:
            ``True`` if divergence is detected.
        """
        if current_metrics.rate_estimate < self.divergence_threshold:
            log.warning(
                "DivergenceDetector: divergence detected (rate=%.4f < threshold=%.4f)",
                current_metrics.rate_estimate,
                self.divergence_threshold,
            )
            return True
        return self.detect_stall(history)

    def add_alert(self, callback: Callable) -> None:
        """Register an alert callback.

        The callback will be invoked as ``callback(state, metrics)`` when
        divergence or a stall is detected.

        Args:
            callback: A callable accepting two positional arguments:
                      ``(SemanticControlState, ConvergenceMetrics)``.
        """
        self.alert_callbacks.append(callback)

    def trigger_alerts(
        self,
        state: SemanticControlState,
        metrics: ConvergenceMetrics,
    ) -> None:
        """Fire all registered alert callbacks.

        Args:
            state:   The current semantic control state.
            metrics: The current convergence metrics.
        """
        for cb in self.alert_callbacks:
            try:
                cb(state, metrics)
            except Exception as exc:  # pragma: no cover
                log.exception(
                    "DivergenceDetector: alert callback raised: %s", exc
                )

    def divergence_score(self, history: list[ConvergenceMetrics]) -> float:
        """Compute a scalar divergence score from the most recent history.

        The score is defined as the mean of the last ``DIVERGENCE_WINDOW``
        rate estimates, negated (positive score ⟹ diverging).

        Args:
            history: List of ``ConvergenceMetrics`` snapshots (oldest first).

        Returns:
            Divergence score in ℝ (higher ⟹ more divergence).
        """
        if not history:
            return 0.0
        window = history[-DIVERGENCE_WINDOW:]
        rates = [m.rate_estimate for m in window]
        return -sum(rates) / len(rates)

    def detect_stall(
        self, history: list[ConvergenceMetrics], window: int = DIVERGENCE_WINDOW
    ) -> bool:
        """Return ``True`` if coverage has not improved over the last *window* steps.

        A stall is detected when the range of ``coverage_ratio`` values in
        the window is below ``STALL_RATE_THRESHOLD``.

        Args:
            history: List of ``ConvergenceMetrics`` snapshots (oldest first).
            window:  Number of most recent snapshots to examine.

        Returns:
            ``True`` if the coverage has been flat for *window* steps.
        """
        if len(history) < window:
            return False
        recent = history[-window:]
        coverages = [m.coverage_ratio for m in recent]
        return max(coverages) - min(coverages) < STALL_RATE_THRESHOLD


# ═══════════════════════════════════════════════════════════════════════════════
#  6.  CertificationAuthority — issues ConvergenceCertificates
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class CertificationAuthority:
    """Issues ``ConvergenceCertificate`` objects when convergence criteria are met.

    The authority checks both the latest state (via coverage ratio and
    obligation count) and the trajectory (via the rate estimator) before
    issuing a certificate.  Certificates expire after ``validity_period``
    seconds and can be explicitly revoked.

    Attributes:
        threshold:       Minimum coverage ratio required for certification.
        validity_period: Lifetime of issued certificates in seconds.
        issued:          List of all certificates issued this session.

    References
    ──────────
    theory2.tex §44.5 — Convergence Certificates and Validity Periods
    """

    threshold: float = DEFAULT_CONVERGENCE_THRESHOLD
    validity_period: float = DEFAULT_VALIDITY_PERIOD
    issued: list[ConvergenceCertificate] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Certification
    # ------------------------------------------------------------------

    def can_certify(
        self,
        state: SemanticControlState,
        trajectory: SemanticTrajectory,
    ) -> bool:
        """Return ``True`` if *state* and *trajectory* satisfy certification criteria.

        Criteria:
        1.  ``coverage_ratio >= threshold``.
        2.  No pending obligations (``obligation_ids`` empty).
        3.  Trajectory has at least two states and is converging (monotone
            non-decreasing coverage over the last two states).

        Args:
            state:      The candidate final state.
            trajectory: The full trajectory leading to *state*.

        Returns:
            ``True`` iff all criteria are met.
        """
        coverage = state.coverage_ratio()
        if coverage < self.threshold:
            return False
        if state.obligation_ids:
            return False
        if trajectory.length() < 2:
            return False
        # At least the last step must not have regressed.
        scores = trajectory.score_history()
        if len(scores) >= 2 and scores[-1] < scores[-2] - 1e-9:
            return False
        return True

    def certify(
        self,
        state: SemanticControlState,
        trajectory: SemanticTrajectory,
    ) -> ConvergenceCertificate:
        """Issue a ``ConvergenceCertificate`` for *state*.

        This method does *not* re-check ``can_certify`` — the caller is
        responsible for calling ``can_certify`` first.

        Args:
            state:      The converged semantic control state.
            trajectory: The trajectory that produced *state*.

        Returns:
            A new ``ConvergenceCertificate`` with a fresh UUID.
        """
        cert = ConvergenceCertificate(
            cert_id=str(uuid.uuid4()),
            state_id=state.state_id,
            coverage_ratio=state.coverage_ratio(),
            obligation_count=len(state.obligation_ids),
            issued_at=time.time(),
            valid_for=self.validity_period,
            evidence={
                "trajectory_length": trajectory.length(),
                "score_history": trajectory.score_history()[-5:],
                "trajectory_id": trajectory.trajectory_id,
            },
        )
        self.issued.append(cert)
        log.info(
            "CertificationAuthority: issued certificate %s (coverage=%.3f)",
            cert.cert_id,
            cert.coverage_ratio,
        )
        return cert

    def revoke(self, cert_id: str) -> bool:
        """Revoke a certificate by ID.

        Revocation removes the certificate from ``self.issued`` so that
        ``list_valid`` will no longer return it.

        Args:
            cert_id: The ID of the certificate to revoke.

        Returns:
            ``True`` if the certificate was found and removed, ``False``
            if not found.
        """
        before = len(self.issued)
        self.issued = [c for c in self.issued if c.cert_id != cert_id]
        revoked = len(self.issued) < before
        if revoked:
            log.info("CertificationAuthority: revoked certificate %s", cert_id)
        else:
            log.warning(
                "CertificationAuthority: certificate %s not found for revocation",
                cert_id,
            )
        return revoked

    def list_valid(self) -> list[ConvergenceCertificate]:
        """Return all certificates that are currently valid (not expired).

        Returns:
            List of non-expired ``ConvergenceCertificate`` objects.
        """
        return [c for c in self.issued if c.is_valid() and not c.is_expired()]

    def audit(self) -> dict[str, Any]:
        """Return an audit summary of issued certificates.

        Returns:
            Dict with ``total_issued``, ``valid_count``, ``expired_count``,
            and a ``certificates`` list.
        """
        valid = self.list_valid()
        return {
            "total_issued": len(self.issued),
            "valid_count": len(valid),
            "expired_count": len(self.issued) - len(valid),
            "certificates": [c.to_dict() for c in self.issued],
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  7.  ConvergenceMonitor — top-level orchestrator for convergence components
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class ConvergenceMonitor:
    """Top-level orchestrator for all convergence-monitoring components.

    This is the *semantic control* layer's convergence monitor — distinct
    from the ``controller.ConvergenceMonitor`` which operates on
    ``OrchestratorState``.  It consumes ``SemanticControlState`` snapshots,
    runs the full sub-analysis pipeline, and provides a unified interface
    for the control loop.

    Pipeline on each ``observe`` call:
    1.  Compute the Lyapunov value via ``lyapunov_function``.
    2.  Update the rate estimator with the latest coverage ratio.
    3.  Build a ``ConvergenceMetrics`` snapshot.
    4.  Run divergence detection; fire alerts if needed.
    5.  Append snapshot to ``metrics_history``.

    Attributes:
        metrics_history:     Ordered list of ``ConvergenceMetrics`` snapshots.
        obligation_tracker:  Tracks pending/resolved obligations.
        coverage_analyzer:   Multi-dimensional coverage analysis.
        rate_estimator:      EMA-based rate estimator.
        divergence_detector: Monitors for divergence and stalls.
        authority:           Issues convergence certificates.
        mode:                The active ``ConvergenceMode`` (informational).

    References
    ──────────
    theory2.tex §44 — Convergence and Certification
    """

    metrics_history: list[ConvergenceMetrics] = field(default_factory=list)
    obligation_tracker: ObligationTracker = field(
        default_factory=ObligationTracker
    )
    coverage_analyzer: CoverageAnalyzer = field(
        default_factory=CoverageAnalyzer
    )
    rate_estimator: ConvergenceRateEstimator = field(
        default_factory=ConvergenceRateEstimator
    )
    divergence_detector: DivergenceDetector = field(
        default_factory=DivergenceDetector
    )
    authority: CertificationAuthority = field(
        default_factory=CertificationAuthority
    )
    mode: ConvergenceMode = field(default_factory=lambda: next(iter(ConvergenceMode)))

    # ------------------------------------------------------------------
    # Core observe / is_converged
    # ------------------------------------------------------------------

    def observe(self, state: SemanticControlState) -> ConvergenceMetrics:
        """Consume a new state snapshot and update all sub-components.

        This is the primary method the control loop calls at each step.
        It produces and stores a ``ConvergenceMetrics`` snapshot.

        Args:
            state: The latest ``SemanticControlState`` from the control loop.

        Returns:
            The freshly computed ``ConvergenceMetrics`` for this step.
        """
        # Expire stale obligations before analysis.
        self.obligation_tracker.expire_old()

        # Sync obligation tracker with state.
        for oid in state.obligation_ids:
            self.obligation_tracker.add(oid, {"source": "state"})

        # Compute coverage and attainability.
        coverage = self.coverage_analyzer.weighted_coverage(state)
        attainability = state.attainability_score()

        # Lyapunov value: import here to avoid circular dep at module level.
        try:
            from jugeo.orchestration.semantic_control.algorithms import (
                lyapunov_function,
            )
            lyapunov_val = lyapunov_function(state)
        except Exception:  # pragma: no cover
            # Inline fallback if algorithms module not yet available.
            lyapunov_val = max(
                0.0,
                (1.0 - coverage)
                + 0.1 * len(state.obligation_ids)
                + 0.05 * (1.0 - attainability),
            )

        # Update rate estimator with raw coverage ratio.
        raw_coverage = state.coverage_ratio()
        self.rate_estimator.update(raw_coverage)
        rate = self.rate_estimator.estimated_rate()

        step_count = len(self.metrics_history)

        metrics = ConvergenceMetrics(
            coverage_ratio=raw_coverage,
            obligation_count=self.obligation_tracker.pending_count(),
            attainability=attainability,
            lyapunov_value=lyapunov_val,
            rate_estimate=rate,
            step_count=step_count,
            timestamp=time.time(),
        )

        # Run divergence detection.
        is_diverging = self.divergence_detector.check(
            metrics, self.metrics_history
        )
        if is_diverging:
            self.divergence_detector.trigger_alerts(state, metrics)

        self.metrics_history.append(metrics)
        log.debug("ConvergenceMonitor.observe: %s", metrics.summary())
        return metrics

    def is_converged(self) -> bool:
        """Return ``True`` if the most recent metrics indicate convergence.

        Returns:
            ``True`` if the latest ``ConvergenceMetrics.is_converged()``
            returns ``True``.  ``False`` if no observations have been made.
        """
        m = self.latest_metrics()
        if m is None:
            return False
        return m.is_converged()

    # ------------------------------------------------------------------
    # Certification
    # ------------------------------------------------------------------

    def try_certify(
        self,
        state: SemanticControlState,
        trajectory: SemanticTrajectory,
    ) -> ConvergenceCertificate | None:
        """Attempt to issue a convergence certificate for *state*.

        Args:
            state:      The candidate final state.
            trajectory: The full trajectory leading to *state*.

        Returns:
            A ``ConvergenceCertificate`` if all criteria are met, else ``None``.
        """
        if self.authority.can_certify(state, trajectory):
            return self.authority.certify(state, trajectory)
        return None

    # ------------------------------------------------------------------
    # Reporting / management
    # ------------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Produce a comprehensive diagnostic report.

        Returns:
            Dict with keys:
            ``is_converged``, ``step_count``, ``latest_metrics``,
            ``rate_trend``, ``divergence_score``, ``obligations``,
            ``certificates``.
        """
        latest = self.latest_metrics()
        return {
            "is_converged": self.is_converged(),
            "step_count": len(self.metrics_history),
            "latest_metrics": latest.to_dict() if latest else None,
            "rate_trend": self.rate_estimator.trend(),
            "divergence_score": self.divergence_detector.divergence_score(
                self.metrics_history
            ),
            "obligations": self.obligation_tracker.to_dict(),
            "certificates": self.authority.audit(),
            "mode": self.mode.value,
        }

    def reset(self) -> None:
        """Clear all history and reset sub-components to initial state.

        Useful when restarting a control run without creating a new monitor.
        """
        self.metrics_history.clear()
        self.obligation_tracker = ObligationTracker()
        self.rate_estimator = ConvergenceRateEstimator()
        log.info("ConvergenceMonitor: reset complete")

    def latest_metrics(self) -> ConvergenceMetrics | None:
        """Return the most recently recorded ``ConvergenceMetrics``, or ``None``.

        Returns:
            The last element of ``metrics_history``, or ``None`` if empty.
        """
        if not self.metrics_history:
            return None
        return self.metrics_history[-1]
