r"""Chapter 40, Section 5 — Convergence detection.

Theory (theory2.tex §40.5):
    Convergence in the jugeo state space means reaching a GlobalSection:
    a state σ* such that dom(σ*) = P (all patches assigned) and the sheaf
    descent condition holds: ∀p_i, p_j ∈ P: σ*(p_i)|_{p_i∩p_j} = σ*(p_j)|_{p_i∩p_j}.
    Formally: ConvergedState = {σ | dom(σ) = P ∧ descent_check(σ) = SUCCESS
                                     ∧ ∀i,j: σ(p_i)|_{overlap} = σ(p_j)|_{overlap}}

    Partial convergence is measured by the fraction ρ(σ) = |dom(σ)| / |P|.
    Plateau detection: the search has stalled if ρ has not improved in k rounds.
    The ConvergenceMetric tracks a scalar quality score over rounds; the
    ConvergenceCoordinator monitors it and declares convergence when criteria fire.

    Four convergence criteria:
      - Threshold: σ(metric) < τ  (quality drops below threshold)
      - FixedPoint: σ_t = σ_{t-1}  (no change between rounds)
      - GoalState: is_goal_state flag set on state
      - MaxRounds: generation_round ≥ R_max  (give up after R_max rounds)

# copilot: s05-convergence-detection
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    from jugeo.generation.state_space.models import (
        SemanticState,
        ConvergenceMetric,
        GenerationStateSpace,
        make_initial_state,
        make_goal_state,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False
    SemanticState = Any  # type: ignore[misc,assignment]
    ConvergenceMetric = Any
    GenerationStateSpace = Any

    def make_initial_state(patches): return None
    def make_goal_state(patches, assignments): return None

__all__ = [
    "ConvergenceStatus",
    "ConvergenceReport",
    "ConvergenceHistory",
    "ConvergenceCriterion",
    "ThresholdCriterion",
    "FixedPointCriterion",
    "GoalStateCriterion",
    "MaxRoundsCriterion",
    "ConvergenceCoordinator",
    "ConvergenceAnalyzer",
    "ConvergenceWitness",
]


# ---------------------------------------------------------------------------
# ConvergenceStatus
# ---------------------------------------------------------------------------

class ConvergenceStatus(Enum):
    """Coarse-grained label for the current state of a search trajectory.

    This enum is intentionally coarse so that callers can branch on a small
    set of cases without inspecting individual criterion details.

    Members
    -------
    NOT_CONVERGED
        The search is active; no stopping condition has fired yet.  The
        coordinator will keep accepting new states and updating history.
    PARTIAL
        At least one patch has been assigned but the fraction ρ < 1.  The
        search is making forward progress and should continue.
    CONVERGED
        A goal criterion (GoalStateCriterion or ThresholdCriterion) has fired.
        The final state should be recorded as a ConvergenceWitness and the
        search loop should exit with a SUCCESS result.
    STALLED
        Either MaxRoundsCriterion fired (budget exhausted) or plateau detection
        determined that ρ has not moved in k consecutive rounds.  The loop
        should exit with a FAILURE / TIMEOUT result.
    """

    NOT_CONVERGED = auto()
    PARTIAL = auto()
    CONVERGED = auto()
    STALLED = auto()


# ---------------------------------------------------------------------------
# ConvergenceReport
# ---------------------------------------------------------------------------

@dataclass
class ConvergenceReport:
    """Snapshot of the convergence state at a single evaluation point.

    This dataclass is the primary output of ConvergenceCoordinator.check().
    It bundles every quantity a caller needs to decide what to do next:
    the machine-readable ``status``, the human-readable ``message``, and
    diagnostic scalars for logging and dashboards.

    Fields
    ------
    report_id
        A UUID4 string, unique per report instance.  Useful for correlating
        log lines when multiple coordinators run in the same process.
    state_id
        The ``state_id`` of the SemanticState that was evaluated.  Allows
        reports to be joined with state records in a post-mortem analysis.
    status
        The ConvergenceStatus label for this snapshot.
    fraction
        ρ(σ) = |dom(σ)| / |P|, the coverage fraction at evaluation time.
        A float in [0.0, 1.0]; exactly 1.0 implies all patches assigned.
    round_number
        The ``generation_round`` field of the evaluated state.
    criterion_fired
        The ``name()`` of the ConvergenceCriterion that triggered a status
        change, or the empty string if no criterion fired.
    metric_value
        The scalar quality value recorded for this round.  Semantics depend
        on the metric being tracked (e.g. fraction, log-likelihood, cost).
    plateau_detected
        True when ConvergenceHistory.detect_plateau() returns True for the
        window ending at this round.
    estimated_rounds_remaining
        Linear extrapolation of how many more rounds are needed before ρ = 1.
        None if the trend is flat, negative, or the window is too small.
    message
        A human-readable summary string.  Intended for log output and CLI
        progress displays, not for programmatic branching.
    timestamp
        Wall-clock seconds (``time.time()``) when the report was created.
    """

    report_id: str
    state_id: str
    status: ConvergenceStatus
    fraction: float
    round_number: int
    criterion_fired: str
    metric_value: float
    plateau_detected: bool
    estimated_rounds_remaining: Optional[int]
    message: str
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# ConvergenceHistory
# ---------------------------------------------------------------------------

@dataclass
class ConvergenceHistory:
    """Rolling-window buffer of per-round convergence scalars.

    The coordinator appends one record per generation round.  All four
    deques share the same ``maxlen`` so that the oldest record is evicted
    simultaneously from all of them, keeping the window aligned.

    Design rationale
    ----------------
    Using ``collections.deque(maxlen=N)`` gives O(1) append and automatic
    eviction without any manual bookkeeping.  The window size is kept small
    (default 20) to make plateau detection and trend estimation responsive
    while still filtering single-round noise.

    Fields
    ------
    history_id
        A UUID4 string that identifies this history buffer instance.
    window_size
        The maximum number of rounds retained.  Older rounds are silently
        dropped when the buffer is full.
    fractions
        Coverage fractions ρ in insertion order (oldest → newest).
    metric_values
        Raw quality-metric scalars in insertion order.
    round_numbers
        generation_round values in insertion order.
    timestamps
        Wall-clock times at record() call sites.
    """

    history_id: str
    window_size: int
    fractions: Deque[float]
    metric_values: Deque[float]
    round_numbers: Deque[int]
    timestamps: Deque[float]

    def __post_init__(self) -> None:
        """Ensure all deques carry the correct maxlen.

        __post_init__ is called by the auto-generated __init__ after field
        assignment.  We replace whatever container was passed in (possibly a
        plain list or an existing deque with a different maxlen) with a fresh
        deque of the right capacity, preserving the initial contents.
        """
        # Re-wrap each sequence as a deque with the configured maxlen so that
        # overflow eviction works correctly regardless of how the caller
        # constructed this instance (e.g. via dataclasses.replace).
        self.fractions = deque(self.fractions, maxlen=self.window_size)
        self.metric_values = deque(self.metric_values, maxlen=self.window_size)
        self.round_numbers = deque(self.round_numbers, maxlen=self.window_size)
        self.timestamps = deque(self.timestamps, maxlen=self.window_size)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        fraction: float,
        metric_value: float,
        round_num: int,
    ) -> None:
        """Append one round's scalars to all four deques.

        When the buffer is full the oldest entry is evicted automatically by
        the deque's maxlen mechanism.  All four deques are updated atomically
        (within the same Python call frame) so the index alignment invariant
        is maintained.

        Parameters
        ----------
        fraction:
            Coverage fraction ρ for this round, in [0.0, 1.0].
        metric_value:
            Scalar quality metric value for this round.
        round_num:
            The generation_round counter value for this round.
        """
        self.fractions.append(fraction)
        self.metric_values.append(metric_value)
        self.round_numbers.append(round_num)
        self.timestamps.append(time.time())

    def get_trend(self) -> str:
        """Classify the recent trajectory as improving, worsening, or stable.

        Trend is estimated by comparing the most recent fraction to the
        earliest fraction still in the window.  If the window contains fewer
        than two entries the result is "stable" (not enough data).

        Returns
        -------
        str
            One of ``"improving"``, ``"worsening"``, or ``"stable"``.
        """
        if len(self.fractions) < 2:
            # Cannot determine a trend from a single data point.
            return "stable"

        # Convert to list for indexed access — deque does not support slicing.
        fracs = list(self.fractions)
        delta = fracs[-1] - fracs[0]

        # Use a small dead-band (0.001) to avoid calling noise "improving".
        if delta > 0.001:
            return "improving"
        if delta < -0.001:
            return "worsening"
        return "stable"

    def detect_plateau(
        self,
        min_window: int = 5,
        tolerance: float = 0.001,
    ) -> bool:
        """Return True when the coverage fraction has stalled for min_window rounds.

        A plateau is declared when the maximum fraction observed in the last
        ``min_window`` rounds does not exceed the minimum fraction by more than
        ``tolerance``.  This is intentionally more conservative than checking
        only the last two values so that transient regressions do not trigger
        false positives.

        Parameters
        ----------
        min_window:
            Minimum number of rounds that must be in the buffer before a
            plateau can be declared.  Prevents spurious early-stall detection.
        tolerance:
            The maximum fraction range (max − min) that is still considered
            "stalled".  Defaults to 0.001 (0.1 percentage point).

        Returns
        -------
        bool
            True when the search appears to be stuck in a plateau.
        """
        if len(self.fractions) < min_window:
            # Not enough history to make a plateau determination.
            return False

        # Inspect only the most recent min_window entries.
        recent: List[float] = list(self.fractions)[-min_window:]
        span = max(recent) - min(recent)
        return span <= tolerance

    def avg_fraction(self) -> float:
        """Return the arithmetic mean of all fractions in the window.

        Returns 0.0 when the buffer is empty so that callers need not handle
        the empty-buffer edge case specially.
        """
        if not self.fractions:
            return 0.0
        return sum(self.fractions) / len(self.fractions)

    def last_fraction(self) -> Optional[float]:
        """Return the most recently recorded coverage fraction, or None.

        Returns None (rather than 0.0) when the buffer is empty so that
        callers can distinguish "no data yet" from "genuinely zero coverage".
        """
        if not self.fractions:
            return None
        return self.fractions[-1]


# ---------------------------------------------------------------------------
# ConvergenceCriterion (abstract base)
# ---------------------------------------------------------------------------

class ConvergenceCriterion(ABC):
    """Abstract base for all convergence-stopping rules.

    Each concrete subclass encapsulates a single stopping condition.  The
    ConvergenceCoordinator holds a list of criteria and evaluates them in
    order, short-circuiting on the first True result.

    Subclassing contract
    --------------------
    Implementors must override ``check`` and ``name``.  Optionally override
    ``description`` to provide richer human-readable text for reports.
    """

    @abstractmethod
    def check(self, state: Any, history: ConvergenceHistory) -> bool:
        """Return True when this criterion considers the search converged/stopped.

        Parameters
        ----------
        state:
            The current SemanticState (or any duck-typed substitute when models
            are unavailable).
        history:
            The rolling window of previously recorded scalars.

        Returns
        -------
        bool
            True ⇒ this criterion fires; the coordinator should stop or change
            status.  False ⇒ this criterion does not apply at this round.
        """

    @abstractmethod
    def name(self) -> str:
        """Return a short identifier string for this criterion.

        The name is stored in ConvergenceReport.criterion_fired and should be
        stable across releases so that log-analysis scripts can match on it.
        """

    def description(self) -> str:
        """Return a human-readable description of this criterion.

        Defaults to name() so that subclasses without elaborate descriptions
        still produce readable output.  Override to add parameter details.
        """
        return self.name()


# ---------------------------------------------------------------------------
# ThresholdCriterion
# ---------------------------------------------------------------------------

class ThresholdCriterion(ConvergenceCriterion):
    """Fire when the coverage fraction reaches or exceeds a target threshold.

    This is the primary "success" criterion for most jugeo search scenarios.
    The threshold τ represents the minimum acceptable fraction of patches that
    must be assigned before the result is considered usable.  Setting τ = 1.0
    requires full coverage; τ = 0.9 allows up to 10 % of patches to remain
    unassigned (useful when some patches are known to be optional).

    Formal criterion
    ----------------
    ``check`` returns True when:
        ρ(σ) ≥ τ   OR   state.is_goal_state = True

    The second clause is a safety net: if the state-machine itself marks a
    state as a goal (e.g. because an external oracle verified it), we respect
    that flag even if the numeric fraction has not reached τ.

    Parameters
    ----------
    threshold
        Convergence target in (0.0, 1.0].  Default 0.95 (95 % coverage).
    """

    def __init__(self, threshold: float = 0.95) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError(
                f"ThresholdCriterion threshold must be in (0, 1], got {threshold!r}"
            )
        self._threshold = threshold

    # ------------------------------------------------------------------
    # ConvergenceCriterion interface
    # ------------------------------------------------------------------

    def check(self, state: Any, history: ConvergenceHistory) -> bool:
        """Return True when fraction ≥ threshold OR the state is a goal state.

        We prefer the history fraction over recomputing from the state object
        so that the coordinator's bookkeeping (smoothing, metric updates) is
        the single source of truth.  If the history is empty we fall back to
        reading the state's ``patch_assignments`` directly.
        """
        # Fast path: the state machine already verified this is a goal state.
        is_goal = getattr(state, "is_goal_state", False)
        if is_goal:
            logger.debug(
                "ThresholdCriterion: is_goal_state=True on state %s",
                getattr(state, "state_id", "?"),
            )
            return True

        # Primary path: check the recorded coverage fraction.
        last = history.last_fraction()
        if last is not None and last >= self._threshold:
            logger.debug(
                "ThresholdCriterion: fraction %.4f >= threshold %.4f",
                last,
                self._threshold,
            )
            return True

        # Fallback: compute fraction directly from state fields when history
        # is empty (e.g. on the very first call before update_metric runs).
        assignments: Dict[str, str] = getattr(state, "patch_assignments", {})
        open_obs: Set[str] = getattr(state, "obligations_open", set())
        closed_obs: Set[str] = getattr(state, "obligations_closed", set())
        total = len(open_obs) + len(closed_obs)
        if total > 0:
            direct_fraction = len(assignments) / total
            if direct_fraction >= self._threshold:
                logger.debug(
                    "ThresholdCriterion: direct fraction %.4f >= threshold %.4f",
                    direct_fraction,
                    self._threshold,
                )
                return True

        return False

    def name(self) -> str:
        return "ThresholdCriterion"

    def description(self) -> str:
        return (
            f"ThresholdCriterion(threshold={self._threshold:.4f}): "
            f"fires when coverage fraction ≥ {self._threshold:.4f} or is_goal_state=True"
        )


# ---------------------------------------------------------------------------
# FixedPointCriterion
# ---------------------------------------------------------------------------

class FixedPointCriterion(ConvergenceCriterion):
    """Fire when successive metric values are within tolerance of each other.

    A fixed point in the metric sequence indicates that the search is no
    longer producing meaningful changes between rounds.  Whether this is a
    success or a stall depends on the current fraction — the coordinator
    uses this criterion as an auxiliary signal, not a standalone decision.

    Formal criterion
    ----------------
    ``check`` returns True when:
        |metric_t − metric_{t−1}| < ε

    where ε = ``tolerance`` (default 1e-6).

    The criterion also maintains an internal ``_prev_fingerprint`` field that
    stores a string encoding of the previous state's patch_assignments.  If
    the fingerprint has not changed the metric trivially satisfies the fixed-
    point condition, so we return True immediately without touching the deque.

    Parameters
    ----------
    tolerance
        Absolute tolerance for metric equality.  Default 1e-6.
    """

    def __init__(self, tolerance: float = 1e-6) -> None:
        self._tolerance = tolerance
        # Fingerprint of the previous state's patch_assignments dict.  None
        # before the first call.
        self._prev_fingerprint: Optional[str] = None

    # ------------------------------------------------------------------
    # ConvergenceCriterion interface
    # ------------------------------------------------------------------

    def check(self, state: Any, history: ConvergenceHistory) -> bool:
        """Return True when the metric has not moved since the previous round.

        We check both the metric deque (numerical) and the state fingerprint
        (structural) to catch cases where the metric is artificially stable
        but the state is still changing (or vice-versa).
        """
        # --- Structural fingerprint check ---------------------------------
        assignments: Dict[str, str] = getattr(state, "patch_assignments", {})
        # Sort items to make the fingerprint deterministic.
        fingerprint = repr(sorted(assignments.items()))
        if self._prev_fingerprint is not None and fingerprint == self._prev_fingerprint:
            logger.debug(
                "FixedPointCriterion: state fingerprint unchanged, fixed-point reached"
            )
            return True
        self._prev_fingerprint = fingerprint

        # --- Numerical metric check ----------------------------------------
        if len(history.metric_values) < 2:
            # Cannot determine fixed point with fewer than two data points.
            return False

        metrics = list(history.metric_values)
        delta = abs(metrics[-1] - metrics[-2])
        if delta < self._tolerance:
            logger.debug(
                "FixedPointCriterion: |Δmetric| = %.2e < tolerance %.2e",
                delta,
                self._tolerance,
            )
            return True

        return False

    def name(self) -> str:
        return "FixedPointCriterion"

    def description(self) -> str:
        return (
            f"FixedPointCriterion(tolerance={self._tolerance:.2e}): "
            f"fires when consecutive metrics differ by less than {self._tolerance:.2e} "
            f"or state fingerprint is unchanged"
        )


# ---------------------------------------------------------------------------
# GoalStateCriterion
# ---------------------------------------------------------------------------

class GoalStateCriterion(ConvergenceCriterion):
    """Fire immediately when the state's is_goal_state flag is True.

    This is the most direct convergence test: the state machine's own
    oracle has verified that all semantic obligations are satisfied and the
    sheaf descent condition holds.  When this fires the search is
    definitively complete regardless of the numeric metric values.

    This criterion intentionally has no parameters; it is a pure predicate
    on the state object.  Callers who need softer goal detection should use
    ThresholdCriterion instead.
    """

    def check(self, state: Any, history: ConvergenceHistory) -> bool:
        """Return True iff state.is_goal_state is truthy."""
        result = bool(getattr(state, "is_goal_state", False))
        if result:
            logger.info(
                "GoalStateCriterion: state %s is a goal state",
                getattr(state, "state_id", "?"),
            )
        return result

    def name(self) -> str:
        return "GoalStateCriterion"

    def description(self) -> str:
        return (
            "GoalStateCriterion: fires when state.is_goal_state is True; "
            "this is the most authoritative convergence signal"
        )


# ---------------------------------------------------------------------------
# MaxRoundsCriterion
# ---------------------------------------------------------------------------

class MaxRoundsCriterion(ConvergenceCriterion):
    """Fire when the generation round counter reaches or exceeds a budget limit.

    This criterion acts as a safety valve: it prevents infinite search loops
    when the other criteria never fire.  Unlike GoalStateCriterion and
    ThresholdCriterion, MaxRoundsCriterion firing indicates a *stall*, not
    a success.  The coordinator maps this to ``ConvergenceStatus.STALLED``.

    Parameters
    ----------
    max_rounds
        The round budget.  The search is terminated once
        ``state.generation_round >= max_rounds``.  Default 100.
    """

    def __init__(self, max_rounds: int = 100) -> None:
        if max_rounds < 1:
            raise ValueError(
                f"MaxRoundsCriterion max_rounds must be ≥ 1, got {max_rounds!r}"
            )
        self._max_rounds = max_rounds

    # ------------------------------------------------------------------
    # ConvergenceCriterion interface
    # ------------------------------------------------------------------

    def check(self, state: Any, history: ConvergenceHistory) -> bool:
        """Return True when state.generation_round >= max_rounds."""
        current_round: int = getattr(state, "generation_round", 0)
        if current_round >= self._max_rounds:
            logger.warning(
                "MaxRoundsCriterion: generation_round %d >= max_rounds %d — budget exhausted",
                current_round,
                self._max_rounds,
            )
            return True
        return False

    def name(self) -> str:
        return "MaxRoundsCriterion"

    def description(self) -> str:
        return (
            f"MaxRoundsCriterion(max_rounds={self._max_rounds}): "
            f"fires (as STALLED) when generation_round ≥ {self._max_rounds}"
        )


# ---------------------------------------------------------------------------
# ConvergenceCoordinator
# ---------------------------------------------------------------------------

class ConvergenceCoordinator:
    """Orchestrates multiple ConvergenceCriterion objects against a stream of states.

    The coordinator is the central object that a search loop interacts with
    each generation round.  Callers:

    1. Call ``update_metric(state, round_num)`` to record the current round's
       scalars in the history buffer.
    2. Call ``check(state)`` to evaluate all criteria and get a ConvergenceStatus.
    3. Optionally call ``get_convergence_report()`` for a full snapshot.
    4. Exit the loop when ``is_converged()`` returns True or status is STALLED.

    Criteria evaluation order
    -------------------------
    Criteria are evaluated left-to-right.  The first criterion that returns
    True determines the status, with one exception: MaxRoundsCriterion always
    maps to STALLED (not CONVERGED) regardless of position.

    Status mapping
    --------------
    - GoalStateCriterion fires  → CONVERGED
    - ThresholdCriterion fires  → CONVERGED
    - FixedPointCriterion fires → CONVERGED (only if fraction ≥ 0.5, else STALLED)
    - MaxRoundsCriterion fires  → STALLED
    - Plateau detected          → STALLED  (overrides PARTIAL)
    - fraction > 0              → PARTIAL
    - otherwise                 → NOT_CONVERGED

    Parameters
    ----------
    criteria
        List of ConvergenceCriterion instances.  If None, defaults to
        [ThresholdCriterion(), GoalStateCriterion(), MaxRoundsCriterion()].
    window_size
        Rolling window size forwarded to ConvergenceHistory.  Default 20.
    """

    def __init__(
        self,
        criteria: Optional[List[ConvergenceCriterion]] = None,
        window_size: int = 20,
    ) -> None:
        self._criteria: List[ConvergenceCriterion] = criteria if criteria is not None else [
            ThresholdCriterion(),
            GoalStateCriterion(),
            MaxRoundsCriterion(),
        ]
        self._window_size = window_size

        # History buffer — initialised with empty deques.
        self._history: ConvergenceHistory = ConvergenceHistory(
            history_id=str(uuid.uuid4()),
            window_size=window_size,
            fractions=deque(maxlen=window_size),
            metric_values=deque(maxlen=window_size),
            round_numbers=deque(maxlen=window_size),
            timestamps=deque(maxlen=window_size),
        )

        # Optional ConvergenceMetric from models.py, used for smoothed values.
        self._metric: Optional[Any] = None
        if _MODELS_AVAILABLE:
            try:
                self._metric = ConvergenceMetric(
                    metric_id=str(uuid.uuid4()),
                    current_value=0.0,
                    history=[],
                    smoothed_value=0.0,
                    trend="stable",
                    convergence_threshold=0.95,
                    window_size=window_size,
                    metadata={},
                )
            except Exception:
                # ConvergenceMetric signature differs — proceed without it.
                self._metric = None

        self._round: int = 0
        self._converged: bool = False
        self._fired_criterion: str = ""
        self._last_status: ConvergenceStatus = ConvergenceStatus.NOT_CONVERGED
        self._last_fraction: float = 0.0
        self._last_metric_value: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_metric(self, state: Any, round_num: int) -> None:
        """Record the current round's coverage fraction and metric value.

        This must be called *before* check() so that the history deques are
        populated when the criteria inspect them.

        Parameters
        ----------
        state:
            Current SemanticState (or duck-typed substitute).
        round_num:
            The current generation_round counter.
        """
        self._round = round_num

        # Compute coverage fraction directly from state fields.
        assignments: Dict[str, str] = getattr(state, "patch_assignments", {})
        open_obs: Set[str] = getattr(state, "obligations_open", set())
        closed_obs: Set[str] = getattr(state, "obligations_closed", set())
        total = len(open_obs) + len(closed_obs)

        if total > 0:
            fraction = min(len(assignments) / total, 1.0)
        else:
            # No obligations defined: treat as fully covered if any assignment exists.
            fraction = 1.0 if assignments else 0.0

        # Metric value: use compute_coverage_fraction() if available, else fraction.
        try:
            metric_value = float(state.compute_coverage_fraction())
        except Exception:
            metric_value = fraction

        self._last_fraction = fraction
        self._last_metric_value = metric_value

        # Push into the rolling window.
        self._history.record(fraction, metric_value, round_num)

        # Forward to the ConvergenceMetric helper if available.
        if self._metric is not None:
            try:
                self._metric.update(metric_value)
            except Exception:
                pass

        logger.debug(
            "ConvergenceCoordinator.update_metric: round=%d fraction=%.4f metric=%.4f",
            round_num,
            fraction,
            metric_value,
        )

    def check(self, state: Any) -> ConvergenceStatus:
        """Evaluate all criteria and return the current ConvergenceStatus.

        The method iterates criteria left-to-right and returns as soon as one
        fires, updating ``_converged`` and ``_fired_criterion`` as side effects
        so that subsequent calls to ``is_converged()`` and
        ``get_convergence_report()`` reflect the latest evaluation.

        Parameters
        ----------
        state:
            Current SemanticState (or duck-typed substitute).

        Returns
        -------
        ConvergenceStatus
            The status label for this round.
        """
        # Identify the stall criteria so we can map them to STALLED rather
        # than CONVERGED when they fire.
        stall_criterion_names: Set[str] = {"MaxRoundsCriterion"}

        fired_name: str = ""
        fired_is_stall: bool = False

        for criterion in self._criteria:
            try:
                fired = criterion.check(state, self._history)
            except Exception as exc:
                logger.warning(
                    "ConvergenceCoordinator: criterion %s raised %s — skipping",
                    criterion.name(),
                    exc,
                )
                fired = False

            if fired:
                fired_name = criterion.name()
                fired_is_stall = fired_name in stall_criterion_names

                # FixedPointCriterion is a stall if fraction is low.
                if fired_name == "FixedPointCriterion" and self._last_fraction < 0.5:
                    fired_is_stall = True

                logger.info(
                    "ConvergenceCoordinator: criterion %s fired (stall=%s) at round %d",
                    fired_name,
                    fired_is_stall,
                    self._round,
                )
                break

        # Determine plateau status independently of criteria.
        plateau = self._history.detect_plateau()

        # Build the final status.
        if fired_name:
            self._fired_criterion = fired_name
            if fired_is_stall:
                status = ConvergenceStatus.STALLED
                self._converged = False
            else:
                status = ConvergenceStatus.CONVERGED
                self._converged = True
        elif plateau:
            # No criterion fired but the search is stuck.
            status = ConvergenceStatus.STALLED
            self._fired_criterion = "PlateauDetection"
            self._converged = False
        elif self._last_fraction > 0.0:
            status = ConvergenceStatus.PARTIAL
            self._converged = False
        else:
            status = ConvergenceStatus.NOT_CONVERGED
            self._converged = False

        self._last_status = status
        return status

    def is_converged(self) -> bool:
        """Return True only when the last check() produced CONVERGED status."""
        return self._converged

    def get_convergence_report(self) -> ConvergenceReport:
        """Build and return a full ConvergenceReport for the last check() call.

        This can be called multiple times; each call creates a new report
        instance with a fresh report_id and timestamp.

        Returns
        -------
        ConvergenceReport
            A complete snapshot of the coordinator's current state.
        """
        plateau = self._history.detect_plateau()
        analyzer = ConvergenceAnalyzer()
        estimated = analyzer.estimate_rounds_to_convergence(self._history)

        # Build a human-readable message.
        trend = self._history.get_trend()
        message = (
            f"[round={self._round}] status={self._last_status.name} "
            f"fraction={self._last_fraction:.4f} trend={trend} "
            f"criterion='{self._fired_criterion}' plateau={plateau}"
        )

        # Determine a meaningful state_id — we do not keep a reference to the
        # last state, so we encode the round number instead.
        state_id = f"round-{self._round}"

        return ConvergenceReport(
            report_id=str(uuid.uuid4()),
            state_id=state_id,
            status=self._last_status,
            fraction=self._last_fraction,
            round_number=self._round,
            criterion_fired=self._fired_criterion,
            metric_value=self._last_metric_value,
            plateau_detected=plateau,
            estimated_rounds_remaining=estimated,
            message=message,
        )


# ---------------------------------------------------------------------------
# ConvergenceAnalyzer
# ---------------------------------------------------------------------------

class ConvergenceAnalyzer:
    """Stateless helper functions for analysing ConvergenceHistory objects.

    All methods are pure functions over their arguments (modulo logging).
    There is no internal state, so instances can be shared freely.
    """

    def compute_fraction(self, state: Any) -> float:
        """Return the coverage fraction ρ(σ) = |assigned patches| / |total obligations|.

        Falls back to 0.0 when the state carries no obligation metadata (e.g.
        during unit tests that use plain namespace objects).

        Parameters
        ----------
        state:
            SemanticState or any object with patch_assignments, obligations_open,
            and obligations_closed attributes.

        Returns
        -------
        float
            ρ in [0.0, 1.0].
        """
        assignments: Dict[str, str] = getattr(state, "patch_assignments", {})
        open_obs: Set[str] = getattr(state, "obligations_open", set())
        closed_obs: Set[str] = getattr(state, "obligations_closed", set())
        total = len(open_obs) + len(closed_obs)
        if total == 0:
            return 1.0 if assignments else 0.0
        return min(len(assignments) / total, 1.0)

    def detect_plateau(self, history: ConvergenceHistory) -> bool:
        """Delegate to ConvergenceHistory.detect_plateau with default parameters.

        Provided as a convenience so callers can use the analyzer as a
        single entry point for all convergence analysis, without needing to
        know the ConvergenceHistory API.
        """
        return history.detect_plateau()

    def estimate_rounds_to_convergence(
        self,
        history: ConvergenceHistory,
    ) -> Optional[int]:
        """Estimate remaining rounds needed to reach full coverage via linear extrapolation.

        Algorithm
        ---------
        1. Fit a linear trend to the fraction values in the current window.
        2. Extrapolate to find the round at which fraction = 1.0.
        3. Subtract the current round to get the remaining rounds.

        Returns None when:
        - The window has fewer than 3 entries (slope estimate unreliable).
        - The estimated slope ≤ 0 (no forward progress).
        - The current fraction is already ≥ 1.0.

        Returns
        -------
        Optional[int]
            Estimated rounds remaining, or None.
        """
        if len(history.fractions) < 3:
            return None

        fracs = list(history.fractions)
        rounds = list(history.round_numbers)

        if not rounds:
            return None

        last_fraction = fracs[-1]
        if last_fraction >= 1.0:
            # Already converged; zero rounds remaining.
            return 0

        # Compute the slope using a simple rise/run over the full window.
        # This is equivalent to the first-and-last-point linear regression,
        # which is fast and adequate for a progress bar estimate.
        n = len(fracs)
        delta_frac = fracs[-1] - fracs[0]
        delta_round = rounds[-1] - rounds[0]

        if delta_round <= 0 or delta_frac <= 0:
            # No positive progress in the window — cannot extrapolate.
            return None

        slope = delta_frac / delta_round  # fraction per round
        remaining_fraction = 1.0 - last_fraction
        remaining_rounds = remaining_fraction / slope

        # Return a ceiling integer and cap at a sane upper bound.
        estimate = int(remaining_rounds) + 1
        return min(estimate, 10_000)

    def check_global_section_condition(self, state: Any) -> bool:
        """Check whether the state satisfies the GlobalSection condition.

        The GlobalSection condition from theory2.tex §40.5 requires:
          1. dom(σ) = P  — all patches have been assigned.
          2. descent_check(σ) = SUCCESS — the assignments are consistent across
             patch overlaps.
          3. state.is_goal_state = True — the state machine confirms the result.

        In practice, condition (2) is verified by the state-machine oracle and
        encoded in the is_goal_state flag, so we check (1) and (3) here.

        Parameters
        ----------
        state:
            SemanticState or duck-typed substitute.

        Returns
        -------
        bool
            True iff the GlobalSection condition is satisfied.
        """
        # Condition 1: all obligations are covered by assignments.
        assignments: Dict[str, str] = getattr(state, "patch_assignments", {})
        open_obs: Set[str] = getattr(state, "obligations_open", set())
        closed_obs: Set[str] = getattr(state, "obligations_closed", set())
        all_obligations: Set[str] = open_obs | closed_obs

        # Every obligation must appear as a key in patch_assignments.
        obligations_covered = all_obligations.issubset(set(assignments.keys()))

        # Condition 3: the oracle flag.
        is_goal = bool(getattr(state, "is_goal_state", False))

        result = obligations_covered and is_goal
        logger.debug(
            "check_global_section_condition: covered=%s is_goal=%s → %s",
            obligations_covered,
            is_goal,
            result,
        )
        return result


# ---------------------------------------------------------------------------
# ConvergenceWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConvergenceWitness:
    """Immutable record certifying that a search reached a terminal status.

    A witness is the final artefact of a convergence event.  It is frozen
    (immutable after construction) and uses __slots__ for memory efficiency,
    since many witnesses may be stored in memory during batch evaluations.

    Fields
    ------
    witness_id
        A UUID4 string that uniquely identifies this witness record.
    state_id
        The state_id of the terminal SemanticState (or the round-encoded
        substitute used by the coordinator).
    fraction
        The coverage fraction ρ at the terminal state.
    is_converged
        True when the search ended with CONVERGED status; False for STALLED.
    rounds_taken
        The generation_round counter at the terminal state.
    plateau_detected
        True when a plateau was detected in the final window.
    timestamp
        Wall-clock time when this witness was created.
    """

    witness_id: str
    state_id: str
    fraction: float
    is_converged: bool
    rounds_taken: int
    plateau_detected: bool
    timestamp: float

    @classmethod
    def from_report(cls, report: ConvergenceReport) -> "ConvergenceWitness":
        """Construct a ConvergenceWitness from a ConvergenceReport.

        This factory method is the canonical way to create a witness after
        calling ``ConvergenceCoordinator.get_convergence_report()``.

        Parameters
        ----------
        report:
            The ConvergenceReport produced by the coordinator.

        Returns
        -------
        ConvergenceWitness
            An immutable witness record with a fresh witness_id and the
            current wall-clock timestamp.
        """
        return cls(
            witness_id=str(uuid.uuid4()),
            state_id=report.state_id,
            fraction=report.fraction,
            is_converged=(report.status == ConvergenceStatus.CONVERGED),
            rounds_taken=report.round_number,
            plateau_detected=report.plateau_detected,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Smoke test / demonstration
# ---------------------------------------------------------------------------

def _run_smoke_test() -> None:
    """Simulate a search sequence and demonstrate convergence detection.

    This function is not part of the public API.  It is called by the
    ``if __name__ == "__main__"`` guard at the bottom of the module and can
    be used as an integration sanity-check when no pytest infrastructure is
    available.

    Simulation
    ----------
    We create a sequence of 30 synthetic states whose coverage fraction
    increases from 0 % to 100 % over 25 rounds and then flattens.  We feed
    them to a ConvergenceCoordinator with a ThresholdCriterion(0.90) and a
    MaxRoundsCriterion(30), then print a report for every fifth round plus
    the terminal report.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logger.info("=== ConvergenceDetection smoke test ===")

    # Build a coordinator with a 90 % threshold and a 30-round budget.
    coordinator = ConvergenceCoordinator(
        criteria=[
            ThresholdCriterion(threshold=0.90),
            GoalStateCriterion(),
            FixedPointCriterion(tolerance=1e-4),
            MaxRoundsCriterion(max_rounds=30),
        ],
        window_size=10,
    )
    analyzer = ConvergenceAnalyzer()

    # Synthesise a simple namespace object that mimics SemanticState.
    class _FakeState:
        def __init__(
            self,
            state_id: str,
            patch_assignments: Dict[str, str],
            obligations_open: Set[str],
            obligations_closed: Set[str],
            generation_round: int,
            is_terminal: bool,
            is_goal_state: bool,
        ) -> None:
            self.state_id = state_id
            self.patch_assignments = patch_assignments
            self.obligations_open = obligations_open
            self.obligations_closed = obligations_closed
            self.generation_round = generation_round
            self.is_terminal = is_terminal
            self.is_goal_state = is_goal_state

        def compute_coverage_fraction(self) -> float:
            total = len(self.obligations_open) + len(self.obligations_closed)
            if total == 0:
                return 0.0
            return len(self.patch_assignments) / total

    # Total patch universe: 20 patches.
    all_patches = {f"p{i}" for i in range(20)}
    patch_list = sorted(all_patches)

    final_status: ConvergenceStatus = ConvergenceStatus.NOT_CONVERGED
    final_report: Optional[ConvergenceReport] = None

    for round_num in range(31):
        # Linear ramp: assign one more patch per round up to round 20.
        n_assigned = min(round_num, 20)
        assigned_patches = {p: "v1" for p in patch_list[:n_assigned]}
        unassigned = all_patches - set(assigned_patches.keys())

        is_goal = n_assigned == 20
        state = _FakeState(
            state_id=f"state-r{round_num}",
            patch_assignments=assigned_patches,
            obligations_open=unassigned,
            obligations_closed=set(assigned_patches.keys()),
            generation_round=round_num,
            is_terminal=is_goal,
            is_goal_state=is_goal,
        )

        # Update history then check.
        coordinator.update_metric(state, round_num)
        status = coordinator.check(state)
        final_status = status

        if round_num % 5 == 0 or status in (ConvergenceStatus.CONVERGED, ConvergenceStatus.STALLED):
            report = coordinator.get_convergence_report()
            final_report = report
            logger.info(
                "Round %2d | fraction=%.3f | status=%-15s | plateau=%s | est_remaining=%s",
                round_num,
                report.fraction,
                report.status.name,
                report.plateau_detected,
                report.estimated_rounds_remaining,
            )

        if status in (ConvergenceStatus.CONVERGED, ConvergenceStatus.STALLED):
            break

    # Produce a ConvergenceWitness from the final report.
    if final_report is not None:
        witness = ConvergenceWitness.from_report(final_report)
        logger.info(
            "ConvergenceWitness: id=%s converged=%s rounds=%d fraction=%.3f",
            witness.witness_id,
            witness.is_converged,
            witness.rounds_taken,
            witness.fraction,
        )
    else:
        logger.warning("No final report produced — history may be empty.")

    logger.info("Final status: %s", final_status.name)
    logger.info("=== smoke test complete ===")


if __name__ == "__main__":
    _run_smoke_test()
