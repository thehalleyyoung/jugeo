"""Convergence monitoring for multi-agent LLM verification pipelines.

Implements Lyapunov convergence theory, phase detection, and stall/divergence
diagnostics.  The central idea: define a non-negative *potential function*

    V(state) = α(1 − coverage) + β·obstruction_density + γ·trust_debt + δ·obligation_pressure

and track whether V is monotonically decreasing across verification rounds.
When V stops decreasing (or increases) we have a *convergence failure* — the
system is either stuck or actively diverging.

Classes
-------
LyapunovFunction       Compute and analyse V(state).
PhaseDetector          Infer the current convergence phase from V-components.
RateEstimator          Exponential-smoothing estimator for ΔV / round.
StallDetector          Diagnose when the pipeline is stuck.
DivergenceDetector     Detect when V is increasing.
ConvergenceMonitor     Top-level façade that ties everything together.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from jugeo_agents.types import (
    ConvergencePhase,
    ConvergenceSnapshot,
    ConvergenceStatus,
    CoverageReport,
    DescentResult,
    Obstruction,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CONVERGENCE_THRESHOLD: float = 0.05
"""V below this value is considered *converged*."""

_DEFAULT_STALL_PATIENCE: int = 3
"""Number of rounds with negligible improvement before declaring a stall."""

_DEFAULT_STALL_THRESHOLD: float = 0.01
"""Minimum absolute ΔV to count as "progress"."""

_DEFAULT_DIVERGE_WINDOW: int = 3
"""Window size for divergence detection."""

_PHASE_COVERAGE_CUTOFF: float = 0.50
"""Coverage below this → EXPLORATION phase."""

_PHASE_OBSTRUCTION_CUTOFF: float = 0.30
"""Obstruction density above this → CONSOLIDATION phase."""

_PHASE_TRUST_DEBT_CUTOFF: float = 0.40
"""Trust debt above this → VERIFICATION phase."""

_MAX_FORECAST_STEPS: int = 500
"""Upper-bound on forecast horizon to avoid infinite estimates."""


# ---------------------------------------------------------------------------
# 1. StallDiagnostic — returned by StallDetector
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StallDiagnostic:
    """Diagnostic record produced when the pipeline appears stuck.

    Attributes
    ----------
    rounds_stalled : int
        How many consecutive rounds showed negligible V improvement.
    bottleneck_component : str
        Which V-component (``coverage``, ``obstruction``, ``trust_debt``,
        ``obligation``) is contributing the most to the residual V.
    bottleneck_agent : str
        Agent identifier most associated with the bottleneck (best-effort;
        empty string if unknown).
    recommended_recovery : str
        Human-readable recovery suggestion.
    """

    rounds_stalled: int
    bottleneck_component: str
    bottleneck_agent: str = ""
    recommended_recovery: str = ""


# ---------------------------------------------------------------------------
# 2. LyapunovFunction — the core potential function
# ---------------------------------------------------------------------------

class LyapunovFunction:
    """Compute the Lyapunov potential V(state) and analyse its trajectory.

    The function is a weighted sum of four non-negative *deficiency* terms:

        V = α·(1 − coverage) + β·obstruction_density + γ·trust_debt + δ·obligation_pressure

    When V = 0 the system is perfect: full coverage, no obstructions, no
    trust debt, and no outstanding obligations.

    Parameters
    ----------
    alpha : float
        Weight for coverage deficiency ``(1 − coverage)``.
    beta : float
        Weight for obstruction density.
    gamma : float
        Weight for trust debt (fraction of claims below desired trust).
    delta : float
        Weight for obligation pressure (fraction of open obligations).
    """

    __slots__ = ("alpha", "beta", "gamma", "delta")

    def __init__(
        self,
        alpha: float = 0.3,
        beta: float = 0.3,
        gamma: float = 0.2,
        delta: float = 0.2,
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.delta = delta

    # -- core computation ---------------------------------------------------

    def compute(
        self,
        coverage: float,
        obstruction_density: float,
        trust_debt: float,
        obligation_pressure: float,
    ) -> float:
        """Evaluate V at the given state.

        All inputs are expected in [0, 1].  Values outside this range are
        clamped silently so that V stays non-negative.
        """
        cov = max(0.0, min(1.0, coverage))
        obs = max(0.0, min(1.0, obstruction_density))
        trd = max(0.0, min(1.0, trust_debt))
        obl = max(0.0, min(1.0, obligation_pressure))
        return (
            self.alpha * (1.0 - cov)
            + self.beta * obs
            + self.gamma * trd
            + self.delta * obl
        )

    def components(
        self,
        coverage: float,
        obstruction_density: float,
        trust_debt: float,
        obligation_pressure: float,
    ) -> dict[str, float]:
        """Return the *weighted* contribution of each component."""
        cov = max(0.0, min(1.0, coverage))
        obs = max(0.0, min(1.0, obstruction_density))
        trd = max(0.0, min(1.0, trust_debt))
        obl = max(0.0, min(1.0, obligation_pressure))
        return {
            "coverage": self.alpha * (1.0 - cov),
            "obstruction": self.beta * obs,
            "trust_debt": self.gamma * trd,
            "obligation": self.delta * obl,
        }

    def dominant_component(
        self,
        coverage: float,
        obstruction_density: float,
        trust_debt: float,
        obligation_pressure: float,
    ) -> str:
        """Name of the component contributing the most to V."""
        parts = self.components(coverage, obstruction_density, trust_debt, obligation_pressure)
        return max(parts, key=parts.get)  # type: ignore[arg-type]

    # -- trajectory analysis ------------------------------------------------

    def is_decreasing(self, history: list[float], window: int = 3) -> bool:
        """Return True if V has been strictly decreasing over the tail.

        Checks that each value in the last *window* entries is less than its
        predecessor.  If the history is shorter than *window + 1* entries
        we return ``False`` (not enough data).
        """
        if len(history) < window + 1:
            return False
        tail = history[-(window + 1):]
        return all(tail[i] > tail[i + 1] for i in range(len(tail) - 1))

    def is_increasing(self, history: list[float], window: int = 3) -> bool:
        """Return True if V has been strictly increasing over the tail."""
        if len(history) < window + 1:
            return False
        tail = history[-(window + 1):]
        return all(tail[i] < tail[i + 1] for i in range(len(tail) - 1))

    def delta_v(self, history: list[float]) -> float | None:
        """Return the last step change ΔV = V_n − V_{n−1}, or None."""
        if len(history) < 2:
            return None
        return history[-1] - history[-2]

    def mean_delta(self, history: list[float], window: int = 5) -> float | None:
        """Mean ΔV over the last *window* steps."""
        if len(history) < 2:
            return None
        n = min(window, len(history) - 1)
        deltas = [history[i + 1] - history[i] for i in range(len(history) - n, len(history) - 1)]
        return sum(deltas) / len(deltas) if deltas else None


# ---------------------------------------------------------------------------
# 3. PhaseDetector — infer convergence phase from V-components
# ---------------------------------------------------------------------------

class PhaseDetector:
    """Infer the current convergence phase from snapshot history.

    The phase is determined by which *deficiency component* dominates V:

    +-------------------+---------------------------------------------------+
    | Phase             | Heuristic                                         |
    +===================+===================================================+
    | EXPLORATION       | coverage < 0.50 (most of V comes from coverage)  |
    +-------------------+---------------------------------------------------+
    | CONSOLIDATION     | obstruction_density > 0.30 and coverage ≥ 0.50   |
    +-------------------+---------------------------------------------------+
    | RESOLUTION        | treaties/negotiations active (obligations > 0)    |
    +-------------------+---------------------------------------------------+
    | VERIFICATION      | trust_debt is the dominant V-component            |
    +-------------------+---------------------------------------------------+
    | COMPLETE          | V < convergence threshold                         |
    +-------------------+---------------------------------------------------+

    Parameters
    ----------
    convergence_threshold : float
        V below this is "COMPLETE".
    coverage_cutoff : float
        Coverage below this puts us in EXPLORATION.
    obstruction_cutoff : float
        Obstruction density above this triggers CONSOLIDATION.
    trust_debt_cutoff : float
        Trust debt above this triggers VERIFICATION.
    """

    __slots__ = (
        "_convergence_threshold",
        "_coverage_cutoff",
        "_obstruction_cutoff",
        "_trust_debt_cutoff",
        "_lyapunov",
    )

    def __init__(
        self,
        convergence_threshold: float = _DEFAULT_CONVERGENCE_THRESHOLD,
        coverage_cutoff: float = _PHASE_COVERAGE_CUTOFF,
        obstruction_cutoff: float = _PHASE_OBSTRUCTION_CUTOFF,
        trust_debt_cutoff: float = _PHASE_TRUST_DEBT_CUTOFF,
        lyapunov: LyapunovFunction | None = None,
    ) -> None:
        self._convergence_threshold = convergence_threshold
        self._coverage_cutoff = coverage_cutoff
        self._obstruction_cutoff = obstruction_cutoff
        self._trust_debt_cutoff = trust_debt_cutoff
        self._lyapunov = lyapunov or LyapunovFunction()

    def detect(self, snapshots: list[ConvergenceSnapshot]) -> ConvergencePhase:
        """Determine the current phase from the most recent snapshot(s).

        If *snapshots* is empty, returns ``EXPLORATION`` (no data yet).
        """
        if not snapshots:
            return ConvergencePhase.EXPLORATION

        latest = snapshots[-1]

        # 1) Already converged?
        if latest.lyapunov_v < self._convergence_threshold:
            return ConvergencePhase.COMPLETE

        # Derive component values from the snapshot
        coverage = latest.coverage
        obstruction_density = 1.0 - latest.consistency
        trust_debt = 1.0 - latest.trust_level
        obligation_pressure = latest.obligation_pressure

        # 2) Coverage is the bottleneck → still exploring
        if coverage < self._coverage_cutoff:
            return ConvergencePhase.EXPLORATION

        # 3) High obstruction density → consolidation (cross-checking)
        if obstruction_density > self._obstruction_cutoff:
            return ConvergencePhase.CONSOLIDATION

        # 4) Obligations pending → resolution / treaty negotiation
        if obligation_pressure > 0.0:
            return ConvergencePhase.RESOLUTION

        # 5) Trust debt dominant → verification
        dominant = self._lyapunov.dominant_component(
            coverage, obstruction_density, trust_debt, obligation_pressure,
        )
        if dominant == "trust_debt" and trust_debt > self._trust_debt_cutoff:
            return ConvergencePhase.VERIFICATION

        # 6) Fallback: if V is still high but no single component dominates
        #    we treat it as CONSOLIDATION (general clean-up).
        return ConvergencePhase.CONSOLIDATION

    def detect_from_components(
        self,
        coverage: float,
        obstruction_density: float,
        trust_debt: float,
        obligation_pressure: float,
        lyapunov_v: float,
    ) -> ConvergencePhase:
        """Convenience: detect phase without building a full snapshot."""
        if lyapunov_v < self._convergence_threshold:
            return ConvergencePhase.COMPLETE
        if coverage < self._coverage_cutoff:
            return ConvergencePhase.EXPLORATION
        if obstruction_density > self._obstruction_cutoff:
            return ConvergencePhase.CONSOLIDATION
        if obligation_pressure > 0.0:
            return ConvergencePhase.RESOLUTION
        dominant = self._lyapunov.dominant_component(
            coverage, obstruction_density, trust_debt, obligation_pressure,
        )
        if dominant == "trust_debt" and trust_debt > self._trust_debt_cutoff:
            return ConvergencePhase.VERIFICATION
        return ConvergencePhase.CONSOLIDATION


# ---------------------------------------------------------------------------
# 4. RateEstimator — exponential moving average of ΔV
# ---------------------------------------------------------------------------

class RateEstimator:
    """Exponential-smoothing estimator for the rate of V decrease.

    Maintains a running EMA (exponential moving average) of ΔV per round.
    A *negative* rate means V is decreasing (good).

    Parameters
    ----------
    smoothing : float
        EMA smoothing factor α ∈ (0, 1].  Higher values weight recent
        observations more heavily.
    """

    __slots__ = ("_smoothing", "_ema", "_count", "_prev_value")

    def __init__(self, smoothing: float = 0.3) -> None:
        if not 0.0 < smoothing <= 1.0:
            raise ValueError(f"smoothing must be in (0, 1], got {smoothing}")
        self._smoothing = smoothing
        self._ema: float | None = None
        self._count: int = 0
        self._prev_value: float | None = None

    def update(self, value: float) -> float:
        """Feed a new V value and return the updated smoothed ΔV rate.

        The *first* call establishes the baseline; the rate is 0.0.
        Subsequent calls compute ΔV = value − prev and update the EMA.
        """
        if self._prev_value is None:
            self._prev_value = value
            self._ema = 0.0
            self._count = 1
            return 0.0

        delta = value - self._prev_value
        self._prev_value = value
        self._count += 1

        if self._ema is None:
            self._ema = delta
        else:
            self._ema = self._smoothing * delta + (1.0 - self._smoothing) * self._ema
        return self._ema

    @property
    def current_rate(self) -> float:
        """Most recent smoothed ΔV rate, or 0.0 if not yet initialised."""
        return self._ema if self._ema is not None else 0.0

    @property
    def observations(self) -> int:
        """Number of values fed so far."""
        return self._count

    def forecast_steps_to_convergence(
        self,
        current_v: float,
        threshold: float = _DEFAULT_CONVERGENCE_THRESHOLD,
    ) -> int | None:
        """Estimate rounds remaining until V drops below *threshold*.

        Uses the current EMA rate.  Returns ``None`` if:
        - no rate has been computed yet,
        - the rate is non-negative (V isn't decreasing), or
        - the current V is already below the threshold.

        The estimate uses the simple linear projection:

            steps = ceil((current_v − threshold) / |rate|)

        clamped to ``_MAX_FORECAST_STEPS`` to avoid unbounded values.
        """
        if current_v <= threshold:
            return 0

        rate = self.current_rate
        if rate >= 0.0:
            return None  # not decreasing — cannot forecast

        steps = math.ceil((current_v - threshold) / abs(rate))
        return min(steps, _MAX_FORECAST_STEPS)


# ---------------------------------------------------------------------------
# 5. StallDetector — diagnose stuck pipelines
# ---------------------------------------------------------------------------

class StallDetector:
    """Detect when the pipeline's V has stopped decreasing.

    A *stall* is declared when V changes by less than *threshold* for
    *patience* consecutive rounds.

    Parameters
    ----------
    patience : int
        Number of rounds of negligible improvement before declaring a stall.
    threshold : float
        Minimum absolute ΔV to count as "progress".
    """

    __slots__ = ("_patience", "_threshold", "_lyapunov")

    def __init__(
        self,
        patience: int = _DEFAULT_STALL_PATIENCE,
        threshold: float = _DEFAULT_STALL_THRESHOLD,
        lyapunov: LyapunovFunction | None = None,
    ) -> None:
        self._patience = patience
        self._threshold = threshold
        self._lyapunov = lyapunov or LyapunovFunction()

    def check(self, snapshots: list[ConvergenceSnapshot]) -> StallDiagnostic | None:
        """Return a ``StallDiagnostic`` if the system appears stuck, else ``None``.

        Analyses the tail of *snapshots* to decide whether V has plateaued.
        """
        if len(snapshots) < self._patience + 1:
            return None  # not enough data

        tail = snapshots[-(self._patience + 1):]
        v_values = [s.lyapunov_v for s in tail]

        # Count consecutive rounds of negligible ΔV
        stalled_rounds = 0
        for i in range(len(v_values) - 1, 0, -1):
            if abs(v_values[i] - v_values[i - 1]) < self._threshold:
                stalled_rounds += 1
            else:
                break

        if stalled_rounds < self._patience:
            return None  # still making progress

        # Identify the bottleneck component in the latest snapshot
        latest = snapshots[-1]
        coverage = latest.coverage
        obstruction_density = 1.0 - latest.consistency
        trust_debt = 1.0 - latest.trust_level
        obligation_pressure = latest.obligation_pressure

        bottleneck = self._lyapunov.dominant_component(
            coverage, obstruction_density, trust_debt, obligation_pressure,
        )

        recovery = _suggest_recovery(bottleneck, latest)

        return StallDiagnostic(
            rounds_stalled=stalled_rounds,
            bottleneck_component=bottleneck,
            bottleneck_agent="",
            recommended_recovery=recovery,
        )


def _suggest_recovery(bottleneck: str, snapshot: ConvergenceSnapshot) -> str:
    """Generate a human-readable recovery suggestion for a stall."""
    suggestions: dict[str, str] = {
        "coverage": (
            f"Coverage is only {snapshot.coverage:.0%}. "
            "Consider adding agents for uncovered subtasks or "
            "re-decomposing the task into finer-grained sub-questions."
        ),
        "obstruction": (
            f"Obstruction density is {1.0 - snapshot.consistency:.0%}. "
            "Agents are contradicting each other. Try introducing a "
            "mediator agent or running a targeted treaty negotiation."
        ),
        "trust_debt": (
            f"Trust debt is {1.0 - snapshot.trust_level:.0%}. "
            "Claims lack grounding. Route high-value claims through "
            "stronger evidence channels (tool execution, RAG, or human review)."
        ),
        "obligation": (
            f"Obligation pressure is {snapshot.obligation_pressure:.0%}. "
            "Outstanding commitments are stalling progress. Prioritise "
            "fulfilling or explicitly cancelling pending obligations."
        ),
    }
    return suggestions.get(bottleneck, "Review pipeline configuration and agent prompts.")


# ---------------------------------------------------------------------------
# 6. DivergenceDetector — detect when V is increasing
# ---------------------------------------------------------------------------

class DivergenceDetector:
    """Detect when the Lyapunov potential is *increasing* — the system is
    getting worse rather than converging.

    Parameters
    ----------
    window : int
        Number of consecutive increasing rounds required to declare divergence.
    """

    __slots__ = ("_window", "_lyapunov")

    def __init__(
        self,
        window: int = _DEFAULT_DIVERGE_WINDOW,
        lyapunov: LyapunovFunction | None = None,
    ) -> None:
        self._window = window
        self._lyapunov = lyapunov or LyapunovFunction()

    def check(self, snapshots: list[ConvergenceSnapshot]) -> bool:
        """Return ``True`` if V is increasing over the detection window."""
        if len(snapshots) < self._window + 1:
            return False
        v_values = [s.lyapunov_v for s in snapshots]
        return self._lyapunov.is_increasing(v_values, window=self._window)

    def severity(self, snapshots: list[ConvergenceSnapshot]) -> float:
        """Return the cumulative V increase over the window, or 0.0."""
        if len(snapshots) < 2:
            return 0.0
        tail = snapshots[-min(self._window + 1, len(snapshots)):]
        v_values = [s.lyapunov_v for s in tail]
        total = sum(
            max(0.0, v_values[i + 1] - v_values[i])
            for i in range(len(v_values) - 1)
        )
        return total


# ---------------------------------------------------------------------------
# 7. ConvergenceMonitor — top-level façade
# ---------------------------------------------------------------------------

class ConvergenceMonitor:
    """Top-level convergence monitor for a multi-agent verification pipeline.

    Ties together the Lyapunov function, phase detector, rate estimator,
    stall detector, and divergence detector into a single, easy-to-use API.

    Typical usage::

        monitor = ConvergenceMonitor()
        for rnd in range(max_rounds):
            outputs = run_agents(...)
            cov, obs, trd, obl = compute_metrics(outputs)
            snap = monitor.record_round(cov, obs, trd, obl)
            if monitor.should_stop():
                break
        print(monitor.report())

    Parameters
    ----------
    lyapunov : LyapunovFunction | None
        Custom Lyapunov function.  Uses default weights if ``None``.
    phase_detector : PhaseDetector | None
        Custom phase detector.  Created automatically if ``None``.
    stall_patience : int
        Number of stalled rounds before declaring a stall.
    convergence_threshold : float
        V below this declares convergence.
    divergence_window : int
        Consecutive increasing rounds to declare divergence.
    """

    __slots__ = (
        "_lyapunov",
        "_phase_detector",
        "_rate_estimator",
        "_stall_detector",
        "_divergence_detector",
        "_snapshots",
        "_v_history",
        "_convergence_threshold",
    )

    def __init__(
        self,
        lyapunov: LyapunovFunction | None = None,
        phase_detector: PhaseDetector | None = None,
        stall_patience: int = _DEFAULT_STALL_PATIENCE,
        convergence_threshold: float = _DEFAULT_CONVERGENCE_THRESHOLD,
        divergence_window: int = _DEFAULT_DIVERGE_WINDOW,
    ) -> None:
        self._lyapunov = lyapunov or LyapunovFunction()
        self._phase_detector = phase_detector or PhaseDetector(
            convergence_threshold=convergence_threshold,
            lyapunov=self._lyapunov,
        )
        self._rate_estimator = RateEstimator(smoothing=0.3)
        self._stall_detector = StallDetector(
            patience=stall_patience,
            lyapunov=self._lyapunov,
        )
        self._divergence_detector = DivergenceDetector(
            window=divergence_window,
            lyapunov=self._lyapunov,
        )
        self._snapshots: list[ConvergenceSnapshot] = []
        self._v_history: list[float] = []
        self._convergence_threshold = convergence_threshold

    # -- recording ----------------------------------------------------------

    def record_round(
        self,
        coverage: float,
        obstruction_density: float,
        trust_debt: float,
        obligation_pressure: float = 0.0,
    ) -> ConvergenceSnapshot:
        """Record a single verification round and return its snapshot.

        Computes V, detects the phase, updates the rate estimator, and
        appends the snapshot to the internal history.
        """
        round_number = len(self._snapshots)

        v = self._lyapunov.compute(
            coverage, obstruction_density, trust_debt, obligation_pressure,
        )
        self._v_history.append(v)
        self._rate_estimator.update(v)

        # Build a provisional snapshot for phase detection
        consistency = 1.0 - max(0.0, min(1.0, obstruction_density))
        trust_level = 1.0 - max(0.0, min(1.0, trust_debt))

        snap = ConvergenceSnapshot(
            round_number=round_number,
            coverage=coverage,
            consistency=consistency,
            trust_level=trust_level,
            obligation_pressure=obligation_pressure,
            lyapunov_v=v,
            phase=ConvergencePhase.EXPLORATION,  # placeholder
            timestamp=time.time(),
        )
        self._snapshots.append(snap)

        # Detect phase using the full history
        phase = self._phase_detector.detect(self._snapshots)
        # Update the snapshot's phase in-place (slots dataclass allows setattr)
        object.__setattr__(snap, "phase", phase)

        return snap

    # -- status queries -----------------------------------------------------

    def status(self) -> ConvergenceStatus:
        """Determine the current convergence status.

        Priority order: CONVERGED > DIVERGING > STUCK > CONVERGING > UNKNOWN.
        """
        if not self._snapshots:
            return ConvergenceStatus.UNKNOWN

        latest_v = self._v_history[-1]

        if latest_v < self._convergence_threshold:
            return ConvergenceStatus.CONVERGED

        if self._divergence_detector.check(self._snapshots):
            return ConvergenceStatus.DIVERGING

        if self._stall_detector.check(self._snapshots) is not None:
            return ConvergenceStatus.STUCK

        if len(self._v_history) >= 2 and self._v_history[-1] < self._v_history[-2]:
            return ConvergenceStatus.CONVERGING

        if len(self._v_history) < 2:
            return ConvergenceStatus.UNKNOWN

        return ConvergenceStatus.CONVERGING

    def current_phase(self) -> ConvergencePhase:
        """Return the phase of the most recent snapshot."""
        if not self._snapshots:
            return ConvergencePhase.EXPLORATION
        return self._snapshots[-1].phase

    def should_stop(self) -> bool:
        """Return ``True`` if the pipeline should terminate.

        This is true when the system has converged *or* is diverging
        (continued rounds would only make things worse).
        """
        s = self.status()
        return s in (ConvergenceStatus.CONVERGED, ConvergenceStatus.DIVERGING)

    def forecast(self) -> int | None:
        """Estimated rounds to convergence, or ``None`` if unknown."""
        if not self._v_history:
            return None
        return self._rate_estimator.forecast_steps_to_convergence(
            self._v_history[-1],
            threshold=self._convergence_threshold,
        )

    def stall_diagnostic(self) -> StallDiagnostic | None:
        """Return a stall diagnostic if the pipeline is stuck."""
        return self._stall_detector.check(self._snapshots)

    def divergence_severity(self) -> float:
        """Cumulative V increase over the divergence window."""
        return self._divergence_detector.severity(self._snapshots)

    # -- history access -----------------------------------------------------

    @property
    def history(self) -> list[ConvergenceSnapshot]:
        """Full snapshot history (read-only copy)."""
        return list(self._snapshots)

    @property
    def v_history(self) -> list[float]:
        """Raw Lyapunov V values per round."""
        return list(self._v_history)

    @property
    def rounds(self) -> int:
        """Number of recorded rounds."""
        return len(self._snapshots)

    # -- reporting ----------------------------------------------------------

    def report(self) -> str:
        """Generate a human-readable convergence report."""
        if not self._snapshots:
            return "Convergence Monitor: no rounds recorded yet."

        latest = self._snapshots[-1]
        stat = self.status()
        phase = self.current_phase()
        fc = self.forecast()
        diag = self.stall_diagnostic()

        lines: list[str] = [
            "╔══════════════════════════════════════════════════════╗",
            "║           Convergence Monitor Report                ║",
            "╚══════════════════════════════════════════════════════╝",
            "",
            f"  Rounds completed : {self.rounds}",
            f"  Status           : {stat.value}",
            f"  Phase            : {phase.value}",
            f"  Lyapunov V       : {latest.lyapunov_v:.6f}",
            f"  Coverage         : {latest.coverage:.2%}",
            f"  Consistency      : {latest.consistency:.2%}",
            f"  Trust level      : {latest.trust_level:.2%}",
            f"  Obligation press.: {latest.obligation_pressure:.2%}",
            "",
        ]

        # Rate info
        rate = self._rate_estimator.current_rate
        lines.append(f"  ΔV rate (EMA)    : {rate:+.6f} / round")
        if fc is not None:
            lines.append(f"  Forecast         : ~{fc} rounds to convergence")
        else:
            lines.append("  Forecast         : unable to estimate")
        lines.append("")

        # V trajectory sparkline
        if len(self._v_history) >= 2:
            spark = _sparkline(self._v_history[-min(20, len(self._v_history)):])
            lines.append(f"  V trend (last {min(20, len(self._v_history))}): {spark}")
            lines.append("")

        # Stall diagnostic
        if diag is not None:
            lines.extend([
                "  ⚠ STALL DETECTED",
                f"    Rounds stalled     : {diag.rounds_stalled}",
                f"    Bottleneck         : {diag.bottleneck_component}",
                f"    Recovery suggestion: {diag.recommended_recovery}",
                "",
            ])

        # Divergence warning
        if stat == ConvergenceStatus.DIVERGING:
            sev = self.divergence_severity()
            lines.extend([
                "  🚨 DIVERGENCE DETECTED",
                f"    Cumulative V increase: {sev:.6f}",
                "    Recommendation: stop the pipeline and diagnose.",
                "",
            ])

        # Phase history summary
        phase_counts: dict[str, int] = {}
        for s in self._snapshots:
            p = s.phase.value
            phase_counts[p] = phase_counts.get(p, 0) + 1
        lines.append("  Phase history:")
        for p, c in phase_counts.items():
            lines.append(f"    {p:15s} : {c} round(s)")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    """Render a tiny Unicode sparkline from a list of floats."""
    if not values:
        return ""
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span < 1e-12:
        return _SPARK_CHARS[0] * len(values)
    return "".join(
        _SPARK_CHARS[min(int((v - lo) / span * (len(_SPARK_CHARS) - 1)), len(_SPARK_CHARS) - 1)]
        for v in values
    )
