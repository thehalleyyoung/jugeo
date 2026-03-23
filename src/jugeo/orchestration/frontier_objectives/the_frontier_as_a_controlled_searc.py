"""jugeo.orchestration.frontier_objectives.the_frontier_as_a_controlled_searc
===============================================================================

The Frontier as a Controlled Search Object (Theory Ch. — Exploration,
exploitation, and frontier control, §1).

Theory
------
A *frontier* in JuGeo is the boundary between the region of the semantic
space already explored by the orchestrator and the region not yet visited.
This module formalises the frontier as a *controlled search object*: a
structured set of candidate nodes equipped with a control law that governs
how the boundary expands, contracts, or is redirected.

Formally, let :math:`\\mathcal{F}_t \\subset \\mathcal{S}` be the frontier at
time step :math:`t`.  The frontier evolves under a control operator
:math:`C : \\mathcal{F}_t \\times \\Theta_t \\to \\mathcal{F}_{t+1}` where
:math:`\\Theta_t` is the set of current control parameters (budget, phase,
pressure signals).

The frontier has four primary attributes:

* **breadth** — the cardinality of :math:`\\mathcal{F}_t`.
* **depth** — the average semantic distance from the frontier to the root.
* **curvature** — a measure of how much the boundary bends (high curvature
  implies many concavities; low curvature implies a smooth expanding front).
* **velocity** — the rate of change of breadth over recent steps.

Control objective: keep breadth within [min_breadth, max_breadth], keep
curvature below max_curvature, and maintain velocity above min_velocity
unless the phase is CONVERGING.
"""
from __future__ import annotations

import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.orchestration.frontier_phases import FrontierPhase  # noqa: F401
except ImportError:
    FrontierPhase = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.budgets import BudgetState  # noqa: F401
except ImportError:
    BudgetState = Any  # type: ignore[assignment,misc]

__all__ = [
    "FrontierControlState",
    "FrontierControlSignal",
    "FrontierBoundaryDescriptor",
    "FrontierControlLaw",
    "FrontierCurvatureEstimator",
    "FrontierVelocityTracker",
    "FrontierSearchContext",
    "FrontierControlledSearchAnalyzer",
    "FrontierControlledSearchWitness",
    "FrontierControlledSearchCoordinator",
    "compute_frontier_breadth",
    "compute_frontier_depth",
    "compute_frontier_curvature",
    "normalize_frontier_signal",
    "classify_frontier_phase",
    "frontier_health_score",
    "_safe_float",
    "_clamp",
    "_ema",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _safe_float(x: Any, default: float = 0.0) -> float:
    """Safely convert *x* to a float, returning *default* on failure.

    Parameters
    ----------
    x:
        Value to convert.
    default:
        Fallback value returned when conversion raises ``ValueError`` or
        ``TypeError``.

    Returns
    -------
    float
        The converted value or *default*.
    """
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    """Clamp *x* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    x:
        Value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.

    Raises
    ------
    ValueError
        If ``lo > hi``.
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo ({lo}) must be <= hi ({hi})")
    return max(lo, min(hi, x))


def _ema(values: list[float], alpha: float = 0.3) -> float:
    """Compute the exponential moving average of *values*.

    The EMA is computed left-to-right so the most recent value has the
    highest influence.  If *values* is empty, returns 0.0.

    Parameters
    ----------
    values:
        Ordered sequence of observations.
    alpha:
        Smoothing factor in (0, 1].  Higher values give more weight to
        recent observations.

    Returns
    -------
    float
        Exponential moving average of *values*.
    """
    if not values:
        return 0.0
    alpha = _clamp(alpha, 1e-9, 1.0)
    ema = _safe_float(values[0])
    for v in values[1:]:
        ema = alpha * _safe_float(v) + (1.0 - alpha) * ema
    return ema


# ---------------------------------------------------------------------------
# Module-level standalone functions
# ---------------------------------------------------------------------------


def compute_frontier_breadth(node_ids: list[str]) -> int:
    """Return the cardinality of the frontier node set.

    Duplicate node identifiers are counted only once, matching the
    mathematical definition of frontier breadth as the cardinality of
    the set :math:`\\mathcal{F}_t`.

    Parameters
    ----------
    node_ids:
        List of node identifier strings currently on the frontier.

    Returns
    -------
    int
        Number of unique node identifiers.
    """
    return len(set(node_ids))


def compute_frontier_depth(distances: list[float]) -> float:
    """Compute frontier depth as the mean semantic distance from the root.

    The depth of the frontier is the arithmetic mean of the individual
    node distances.  When *distances* is empty the depth is defined to
    be 0.0 (frontier coincides with the root).

    Parameters
    ----------
    distances:
        Non-negative float distances from each frontier node to the
        search root.

    Returns
    -------
    float
        Arithmetic mean of *distances*, or 0.0 if the list is empty.
    """
    if not distances:
        return 0.0
    cleaned = [_safe_float(d) for d in distances]
    return statistics.mean(cleaned)


def compute_frontier_curvature(distances: list[float]) -> float:
    """Estimate frontier curvature via second-order finite differences.

    Curvature is approximated by sorting the distances, computing the
    first-order differences, then the second-order differences (finite
    analogue of the second derivative), and returning the mean absolute
    value normalised by the mean distance.

    A flat frontier (constant distances) yields curvature 0.  Highly
    irregular frontiers yield larger values.

    Parameters
    ----------
    distances:
        Semantic distances of frontier nodes from the search root.

    Returns
    -------
    float
        Non-negative curvature estimate.  Returns 0.0 if fewer than
        three distances are provided.
    """
    if len(distances) < 3:
        return 0.0
    sorted_d = sorted(_safe_float(d) for d in distances)
    mean_d = statistics.mean(sorted_d) or 1.0
    first_diff = [sorted_d[i + 1] - sorted_d[i] for i in range(len(sorted_d) - 1)]
    second_diff = [first_diff[i + 1] - first_diff[i] for i in range(len(first_diff) - 1)]
    if not second_diff:
        return 0.0
    mean_abs_second = statistics.mean(abs(v) for v in second_diff)
    return mean_abs_second / mean_d


def normalize_frontier_signal(signal: FrontierControlSignal) -> FrontierControlSignal:
    """Return a copy of *signal* with magnitude clamped to [0, 1].

    Frontier control signals must carry a magnitude in the unit interval.
    This function enforces that invariant by creating a new immutable
    signal with the corrected magnitude while preserving all other fields.

    Parameters
    ----------
    signal:
        The frontier control signal to normalise.

    Returns
    -------
    FrontierControlSignal
        A new signal identical to *signal* except that ``magnitude`` is
        guaranteed to lie in [0, 1].
    """
    clamped = _clamp(signal.magnitude, 0.0, 1.0)
    return FrontierControlSignal(
        signal_id=signal.signal_id,
        direction=signal.direction,
        magnitude=clamped,
        reason=signal.reason,
    )


def classify_frontier_phase(
    breadth: int,
    velocity: float,
    curvature: float,
) -> str:
    """Classify the current frontier phase from summary statistics.

    Phase classification rules (applied in priority order):

    1. If curvature > 2.0 → ``"TURBULENT"``
    2. If velocity > 1.0 → ``"EXPANDING"``
    3. If velocity < -0.5 → ``"CONVERGING"``
    4. Otherwise → ``"STABLE"``

    Parameters
    ----------
    breadth:
        Current frontier breadth (cardinality).
    velocity:
        Rate of change of breadth (nodes per iteration step).
    curvature:
        Current curvature estimate.

    Returns
    -------
    str
        One of ``"EXPANDING"``, ``"CONVERGING"``, ``"STABLE"``,
        ``"TURBULENT"``.
    """
    if curvature > 2.0:
        return "TURBULENT"
    if velocity > 1.0:
        return "EXPANDING"
    if velocity < -0.5:
        return "CONVERGING"
    return "STABLE"


def frontier_health_score(
    state: FrontierControlState,
    descriptor: FrontierBoundaryDescriptor,
) -> float:
    """Compute a composite health score for the frontier in [0, 1].

    The health score aggregates four sub-scores:

    * **breadth_score** — 1.0 if breadth is within [min_breadth, max_breadth],
      decaying exponentially outside the range.
    * **curvature_score** — 1.0 if curvature <= max_curvature, decaying
      toward 0 as curvature exceeds the threshold.
    * **velocity_score** — 1.0 if velocity >= min_velocity (or phase is
      CONVERGING), decaying below the threshold.
    * **validity_score** — 1.0 if ``state.is_valid()``, else 0.0.

    The final score is the arithmetic mean of the four sub-scores.

    Parameters
    ----------
    state:
        Current frontier control state.
    descriptor:
        Boundary descriptor defining the control constraints.

    Returns
    -------
    float
        Composite health score in [0, 1].
    """
    # breadth sub-score
    min_b, max_b = descriptor.breadth_range()
    if min_b <= state.breadth <= max_b:
        breadth_score = 1.0
    elif state.breadth < min_b:
        deficit = (min_b - state.breadth) / max(min_b, 1)
        breadth_score = math.exp(-2.0 * deficit)
    else:
        excess = (state.breadth - max_b) / max(max_b, 1)
        breadth_score = math.exp(-2.0 * excess)

    # curvature sub-score
    if state.curvature <= descriptor.max_curvature:
        curvature_score = 1.0
    else:
        excess_c = (state.curvature - descriptor.max_curvature) / max(descriptor.max_curvature, 1e-9)
        curvature_score = math.exp(-excess_c)

    # velocity sub-score
    if state.phase == "CONVERGING" or state.velocity >= descriptor.min_velocity:
        velocity_score = 1.0
    else:
        deficit_v = (descriptor.min_velocity - state.velocity) / max(abs(descriptor.min_velocity), 1e-9)
        velocity_score = math.exp(-deficit_v)

    # validity sub-score
    validity_score = 1.0 if state.is_valid() else 0.0

    return statistics.mean([breadth_score, curvature_score, velocity_score, validity_score])


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrontierControlState:
    """Immutable snapshot of the frontier's control-relevant state.

    Attributes
    ----------
    frontier_id:
        Unique identifier for this frontier instance.
    breadth:
        Number of unique nodes currently on the frontier.
    depth:
        Mean semantic distance from the frontier nodes to the search root.
    curvature:
        Curvature estimate of the frontier boundary.
    velocity:
        Rate of change of breadth over recent iteration steps.
    phase:
        Qualitative phase label (e.g. ``"EXPANDING"``, ``"CONVERGING"``).
    metadata:
        Arbitrary additional key-value data attached to this snapshot.
    """

    frontier_id: str
    breadth: int
    depth: float
    curvature: float
    velocity: float
    phase: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Return True when all numeric fields satisfy basic sanity checks.

        Validation rules:

        * ``breadth >= 0``
        * ``depth >= 0.0``
        * ``0.0 <= curvature <= 10.0``
        * ``phase`` is one of the recognised strings

        Returns
        -------
        bool
            ``True`` iff all checks pass.
        """
        valid_phases = {"EXPANDING", "CONVERGING", "STABLE", "TURBULENT", "UNKNOWN"}
        if self.breadth < 0:
            return False
        if self.depth < 0.0:
            return False
        if not (0.0 <= self.curvature <= 10.0):
            return False
        if self.phase not in valid_phases:
            return False
        return True


@dataclass(frozen=True, slots=True)
class FrontierControlSignal:
    """Immutable directional control signal emitted by the control law.

    A control signal instructs the frontier expansion machinery to change
    its behaviour in a specific direction with a specific magnitude.

    Attributes
    ----------
    signal_id:
        Unique identifier for this signal instance.
    direction:
        One of ``"expand"``, ``"contract"``, ``"redirect"``, ``"hold"``.
    magnitude:
        Relative strength of the signal in [0, 1].  A magnitude of 0.0
        is effectively a no-op; 1.0 is a maximum-strength intervention.
    reason:
        Human-readable justification for the signal.
    """

    signal_id: str
    direction: str
    magnitude: float
    reason: str

    def is_expansive(self) -> bool:
        """Return True when this signal drives the frontier outward.

        A signal is expansive if and only if its direction is
        ``"expand"`` *and* its magnitude is positive.

        Returns
        -------
        bool
            ``True`` iff the signal is expansive.
        """
        return self.direction == "expand" and self.magnitude > 0.0


@dataclass(frozen=True, slots=True)
class FrontierBoundaryDescriptor:
    """Immutable specification of the frontier's control boundary constraints.

    This descriptor encodes the allowed operating envelope for the
    frontier: the breadth range, maximum curvature, minimum velocity,
    and the phase the descriptor applies to.

    Attributes
    ----------
    descriptor_id:
        Unique identifier for this boundary specification.
    min_breadth:
        Minimum acceptable frontier breadth.
    max_breadth:
        Maximum acceptable frontier breadth.
    max_curvature:
        Upper bound on curvature beyond which the frontier is considered
        too irregular and a redirect signal should be issued.
    min_velocity:
        Lower bound on velocity below which the frontier is considered
        stalled (unless the phase is CONVERGING).
    phase_label:
        The intended operating phase for this descriptor.
    """

    descriptor_id: str
    min_breadth: int
    max_breadth: int
    max_curvature: float
    min_velocity: float
    phase_label: str

    def breadth_range(self) -> tuple[int, int]:
        """Return the allowed breadth interval as a (min, max) tuple.

        Returns
        -------
        tuple[int, int]
            The ``(min_breadth, max_breadth)`` pair.
        """
        return (self.min_breadth, self.max_breadth)


@dataclass
class FrontierControlLaw:
    """Stateful control law that maps frontier state to a control signal.

    The control law implements a simple threshold-based policy:

    * breadth > max_breadth → ``contract`` with magnitude proportional
      to the relative overshoot.
    * breadth < min_breadth → ``expand`` with magnitude proportional to
      the relative deficit.
    * curvature > max_curvature → ``redirect`` with magnitude proportional
      to the relative curvature excess.
    * otherwise → ``hold`` with magnitude 0.0.

    Attributes
    ----------
    law_id:
        Unique identifier for this control-law instance.
    sensitivity:
        Scaling factor applied to magnitude calculations.  Higher values
        produce stronger signals for the same deviation.
    history:
        Ordered log of signal IDs produced by this law instance.
    """

    law_id: str = field(default_factory=lambda: f"law-{uuid.uuid4().hex[:8]}")
    sensitivity: float = 0.5
    history: list[str] = field(default_factory=list)

    def apply(
        self,
        state: FrontierControlState,
        descriptor: FrontierBoundaryDescriptor,
    ) -> FrontierControlSignal:
        """Apply the control law and return a directional signal.

        The decision logic is:

        1. Curvature check (highest priority — irregular frontiers need
           redirection before breadth correction).
        2. Breadth upper-bound check (overshoot → contract).
        3. Breadth lower-bound check (undershoot → expand).
        4. Default: hold.

        Magnitude is computed as a sigmoid-scaled proportion of the
        deviation from the violated constraint boundary, so that small
        deviations produce gentle signals and large deviations produce
        near-maximum signals.

        Parameters
        ----------
        state:
            Current frontier control state snapshot.
        descriptor:
            Active boundary descriptor.

        Returns
        -------
        FrontierControlSignal
            The resulting directional control signal.
        """
        min_b, max_b = descriptor.breadth_range()

        def _sigmoid_magnitude(ratio: float) -> float:
            """Map a normalised deviation ratio to a [0,1] magnitude."""
            x = self.sensitivity * ratio
            return _clamp(1.0 / (1.0 + math.exp(-x + 2.0)), 0.0, 1.0)

        direction: str
        magnitude: float
        reason: str

        if state.curvature > descriptor.max_curvature:
            excess = (state.curvature - descriptor.max_curvature) / max(descriptor.max_curvature, 1e-9)
            direction = "redirect"
            magnitude = _sigmoid_magnitude(excess * 3.0)
            reason = (
                f"Curvature {state.curvature:.4f} exceeds max_curvature "
                f"{descriptor.max_curvature:.4f} by {excess * 100:.1f}%"
            )
        elif state.breadth > max_b:
            excess = (state.breadth - max_b) / max(max_b, 1)
            direction = "contract"
            magnitude = _sigmoid_magnitude(excess * 5.0)
            reason = (
                f"Breadth {state.breadth} exceeds max_breadth {max_b} "
                f"by {state.breadth - max_b} nodes"
            )
        elif state.breadth < min_b:
            deficit = (min_b - state.breadth) / max(min_b, 1)
            direction = "expand"
            magnitude = _sigmoid_magnitude(deficit * 5.0)
            reason = (
                f"Breadth {state.breadth} is below min_breadth {min_b} "
                f"by {min_b - state.breadth} nodes"
            )
        else:
            direction = "hold"
            magnitude = 0.0
            reason = "Frontier is within control bounds; no intervention required"

        sig = FrontierControlSignal(
            signal_id=f"sig-{uuid.uuid4().hex[:8]}",
            direction=direction,
            magnitude=magnitude,
            reason=reason,
        )
        self.history.append(sig.signal_id)
        return sig


@dataclass
class FrontierCurvatureEstimator:
    """Online estimator for frontier curvature from distance observations.

    Curvature is estimated using second-order finite differences of the
    sorted distance array, normalised by the mean distance.  A smoothing
    exponential moving average is applied to stabilise the estimate over
    time.

    Attributes
    ----------
    estimator_id:
        Unique identifier for this estimator instance.
    smoothing:
        EMA smoothing factor applied to successive curvature estimates.
    """

    estimator_id: str
    smoothing: float = 0.1

    def estimate(self, distances: list[float]) -> float:
        """Estimate curvature from a list of frontier node distances.

        The algorithm:

        1. Sort and clean the distance list.
        2. Compute first differences :math:`\\Delta d_i = d_{i+1} - d_i`.
        3. Compute second differences :math:`\\Delta^2 d_i`.
        4. Compute mean absolute second difference.
        5. Normalise by the mean distance to obtain a dimensionless quantity.
        6. Apply EMA smoothing with ``self.smoothing``.

        Parameters
        ----------
        distances:
            Observed semantic distances of frontier nodes from the root.

        Returns
        -------
        float
            Non-negative smoothed curvature estimate.
        """
        if len(distances) < 3:
            return 0.0
        cleaned = sorted(_safe_float(d) for d in distances)
        mean_d = statistics.mean(cleaned) or 1.0
        first_diff = [cleaned[i + 1] - cleaned[i] for i in range(len(cleaned) - 1)]
        second_diff = [first_diff[i + 1] - first_diff[i] for i in range(len(first_diff) - 1)]
        raw_curvature = statistics.mean(abs(v) for v in second_diff) / mean_d
        smoothed = _ema([raw_curvature], alpha=self.smoothing)
        return max(0.0, smoothed)


@dataclass
class FrontierVelocityTracker:
    """Tracks frontier velocity as the regression slope of breadth over time.

    Velocity is defined as the least-squares linear regression slope of
    the breadth history over the most recent ``window`` observations.
    Positive velocity means the frontier is expanding; negative means it
    is contracting.

    Attributes
    ----------
    tracker_id:
        Unique identifier for this tracker instance.
    window:
        Number of recent breadth observations used in the regression.
    _history:
        Internal ring buffer of breadth observations.
    """

    tracker_id: str
    window: int = 5
    _history: list[int] = field(default_factory=list)

    def record(self, breadth: int) -> None:
        """Record a new breadth observation.

        Older observations beyond ``window`` are discarded to keep the
        buffer bounded.

        Parameters
        ----------
        breadth:
            Frontier breadth at the current iteration step.
        """
        self._history.append(breadth)
        if len(self._history) > self.window:
            self._history = self._history[-self.window :]

    def velocity(self) -> float:
        """Return the current velocity estimate via linear regression.

        If fewer than two observations are available, velocity is 0.0.
        Otherwise the slope of the ordinary least-squares regression line
        of breadth-against-time-index is returned.

        Returns
        -------
        float
            Regression slope (breadth change per iteration step).
        """
        n = len(self._history)
        if n < 2:
            return 0.0
        xs = list(range(n))
        ys = [float(b) for b in self._history]
        x_mean = statistics.mean(xs)
        y_mean = statistics.mean(ys)
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        denominator = sum((x - x_mean) ** 2 for x in xs)
        if abs(denominator) < 1e-12:
            return 0.0
        return numerator / denominator


@dataclass(frozen=True, slots=True)
class FrontierSearchContext:
    """Immutable contextual information attached to a frontier search session.

    The context bundles together the set of node identifiers, the current
    phase, the iteration counter, and the remaining budget, providing a
    read-only view that can be safely shared across components.

    Attributes
    ----------
    context_id:
        Unique identifier for this search context.
    node_ids:
        Tuple of node identifier strings currently on the frontier.
    phase:
        Qualitative phase label for this context.
    iteration:
        Zero-based iteration counter.
    budget_remaining:
        Fractional budget remaining in [0, 1].
    metadata:
        Arbitrary additional contextual data.
    """

    context_id: str
    node_ids: tuple[str, ...]
    phase: str
    iteration: int
    budget_remaining: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def node_count(self) -> int:
        """Return the number of nodes currently in the frontier context.

        Returns
        -------
        int
            Length of ``node_ids``.
        """
        return len(self.node_ids)


@dataclass
class FrontierControlledSearchAnalyzer:
    """Qualitative and quantitative analyzer for frontier control decisions.

    The analyzer inspects a ``FrontierControlState`` against a
    ``FrontierBoundaryDescriptor`` and produces a structured report
    containing pass/fail flags, a recommended action, and a composite
    quality score.

    Attributes
    ----------
    analyzer_id:
        Unique identifier for this analyzer instance.
    """

    analyzer_id: str

    def analyze(
        self,
        state: FrontierControlState,
        descriptor: FrontierBoundaryDescriptor,
    ) -> dict[str, Any]:
        """Produce a structured analysis report for the frontier state.

        The report contains:

        * ``health`` — composite health score in [0, 1].
        * ``breadth_ok`` — bool, True when breadth is within the allowed range.
        * ``curvature_ok`` — bool, True when curvature is within threshold.
        * ``velocity_ok`` — bool, True when velocity satisfies the minimum.
        * ``recommended_action`` — string suggestion ("hold", "expand", etc.).
        * ``score`` — alias for ``health`` for convenience.

        Parameters
        ----------
        state:
            Current frontier control state.
        descriptor:
            Active boundary descriptor encoding constraints.

        Returns
        -------
        dict[str, Any]
            Structured analysis report.
        """
        min_b, max_b = descriptor.breadth_range()
        breadth_ok = min_b <= state.breadth <= max_b
        curvature_ok = state.curvature <= descriptor.max_curvature
        velocity_ok = state.phase == "CONVERGING" or state.velocity >= descriptor.min_velocity

        if not state.is_valid():
            recommended_action = "reset"
        elif not curvature_ok:
            recommended_action = "redirect"
        elif not breadth_ok and state.breadth > max_b:
            recommended_action = "contract"
        elif not breadth_ok and state.breadth < min_b:
            recommended_action = "expand"
        elif not velocity_ok:
            recommended_action = "accelerate"
        else:
            recommended_action = "hold"

        health = frontier_health_score(state, descriptor)

        return {
            "health": health,
            "breadth_ok": breadth_ok,
            "curvature_ok": curvature_ok,
            "velocity_ok": velocity_ok,
            "recommended_action": recommended_action,
            "score": health,
            "phase": state.phase,
            "valid": state.is_valid(),
        }

    def summary(self, state: FrontierControlState) -> str:
        """Return a compact human-readable summary of the frontier state.

        The summary includes the frontier ID, phase, breadth, depth,
        curvature, and velocity in a single formatted string.

        Parameters
        ----------
        state:
            Frontier control state to summarise.

        Returns
        -------
        str
            Single-line summary string.
        """
        return (
            f"[{state.frontier_id}] phase={state.phase} "
            f"breadth={state.breadth} depth={state.depth:.3f} "
            f"curvature={state.curvature:.4f} velocity={state.velocity:.4f}"
        )


@dataclass(frozen=True, slots=True)
class FrontierControlledSearchWitness:
    """Immutable record of a single frontier-controlled search execution.

    A witness captures everything that happened during one invocation of
    the controlled search coordinator: the control signal that was
    produced, the analysis report, and whether the run was deemed
    successful.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness record.
    timestamp:
        Unix timestamp of when the witness was sealed.
    success:
        True if the coordinator run completed without errors and the
        frontier health score exceeded the success threshold.
    context_id:
        Identifier of the search context associated with this run.
    control_signal:
        The control signal produced by the control law.
    analysis_report:
        Structured dict produced by the analyzer.
    summary_text:
        Human-readable summary of the run outcome.
    """

    witness_id: str
    timestamp: float
    success: bool
    context_id: str
    control_signal: FrontierControlSignal
    analysis_report: dict[str, Any]
    summary_text: str


@dataclass
class FrontierControlledSearchCoordinator:
    """Top-level orchestrator for frontier-controlled search execution.

    # copilot: The coordinator is the top-level entry point for frontier control.
    # copilot: It accepts a FrontierControlState and:
    # copilot:
    # copilot: 1. Validates state breadth and depth for sanity.
    # copilot: 2. Estimates curvature from state geometry.
    # copilot: 3. Tracks velocity using the breadth history.
    # copilot: 4. Applies the control law to produce a FrontierControlSignal.
    # copilot: 5. Runs the FrontierControlledSearchAnalyzer for quality metrics.
    # copilot: 6. Seals and returns a FrontierControlledSearchWitness.

    Attributes
    ----------
    session_id:
        Unique identifier for the coordinator session.
    descriptor:
        Active boundary descriptor used by the control law.
    max_iterations:
        Maximum number of internal correction iterations before the
        coordinator forces a ``hold`` signal.
    """

    session_id: str = field(default_factory=lambda: f"session-{uuid.uuid4().hex[:12]}")
    descriptor: FrontierBoundaryDescriptor = field(
        default_factory=lambda: FrontierBoundaryDescriptor(
            descriptor_id="default-descriptor",
            min_breadth=2,
            max_breadth=20,
            max_curvature=2.0,
            min_velocity=0.1,
            phase_label="STABLE",
        )
    )
    max_iterations: int = 10

    def run(self, state: FrontierControlState) -> FrontierControlledSearchWitness:
        """Execute the controlled search pipeline and return a witness.

        Pipeline steps:

        1. **Validate** state breadth and depth for basic sanity.  If the
           state is invalid, emit a ``hold`` signal with low confidence.
        2. **Estimate** curvature using the ``FrontierCurvatureEstimator``
           seeded from the state's depth as a proxy for the distance array.
        3. **Track** velocity by recording the current breadth into a
           ``FrontierVelocityTracker`` and reading back the slope.
        4. **Apply** the control law to produce a ``FrontierControlSignal``.
        5. **Analyze** quality metrics with the analyzer.
        6. **Seal** and return an immutable ``FrontierControlledSearchWitness``.

        Parameters
        ----------
        state:
            Current frontier control state snapshot.

        Returns
        -------
        FrontierControlledSearchWitness
            Immutable record of this coordinator run.
        """
        run_start = time.monotonic()

        # Step 1: Validate
        if not state.is_valid():
            signal = FrontierControlSignal(
                signal_id=f"sig-invalid-{uuid.uuid4().hex[:6]}",
                direction="hold",
                magnitude=0.0,
                reason=f"State {state.frontier_id} failed validation; issuing hold.",
            )
            report: dict[str, Any] = {
                "health": 0.0,
                "breadth_ok": False,
                "curvature_ok": False,
                "velocity_ok": False,
                "recommended_action": "reset",
                "score": 0.0,
                "phase": state.phase,
                "valid": False,
            }
            elapsed = time.monotonic() - run_start
            return FrontierControlledSearchWitness(
                witness_id=f"witness-{uuid.uuid4().hex[:10]}",
                timestamp=time.time(),
                success=False,
                context_id=state.frontier_id,
                control_signal=signal,
                analysis_report=report,
                summary_text=(
                    f"INVALID state {state.frontier_id}: run aborted after "
                    f"{elapsed * 1000:.1f}ms"
                ),
            )

        # Step 2: Curvature estimation
        # Use depth and a small synthetic distance array derived from breadth
        # and depth as a proxy when actual node distances are unavailable.
        curvature_estimator = FrontierCurvatureEstimator(
            estimator_id=f"ce-{self.session_id}",
            smoothing=0.2,
        )
        synthetic_distances = [
            state.depth * (1.0 + 0.1 * (i - state.breadth / 2.0))
            for i in range(max(state.breadth, 3))
        ]
        estimated_curvature = curvature_estimator.estimate(synthetic_distances)

        # Step 3: Velocity tracking
        velocity_tracker = FrontierVelocityTracker(
            tracker_id=f"vt-{self.session_id}",
            window=5,
        )
        # Simulate recent breadth history using EMA decay from current breadth
        for step in range(4):
            historical_breadth = max(
                0,
                int(state.breadth - state.velocity * (4 - step)),
            )
            velocity_tracker.record(historical_breadth)
        velocity_tracker.record(state.breadth)
        tracked_velocity = velocity_tracker.velocity()

        # Step 4: Apply control law
        control_law = FrontierControlLaw(
            law_id=f"cl-{self.session_id}",
            sensitivity=0.6,
        )
        control_signal = control_law.apply(state, self.descriptor)
        control_signal = normalize_frontier_signal(control_signal)

        # Step 5: Analyze
        analyzer = FrontierControlledSearchAnalyzer(
            analyzer_id=f"analyzer-{self.session_id}",
        )
        report = analyzer.analyze(state, self.descriptor)
        summary_text = analyzer.summary(state)

        # Enrich report with tracking details
        report["estimated_curvature"] = estimated_curvature
        report["tracked_velocity"] = tracked_velocity
        report["elapsed_ms"] = (time.monotonic() - run_start) * 1000.0
        report["session_id"] = self.session_id

        success = report["health"] >= 0.4 and state.is_valid()

        # Step 6: Seal witness
        return FrontierControlledSearchWitness(
            witness_id=f"witness-{uuid.uuid4().hex[:10]}",
            timestamp=time.time(),
            success=success,
            context_id=state.frontier_id,
            control_signal=control_signal,
            analysis_report=report,
            summary_text=(
                f"{'OK' if success else 'DEGRADED'} | {summary_text} | "
                f"signal={control_signal.direction}({control_signal.magnitude:.3f}) | "
                f"health={report['health']:.4f}"
            ),
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== FrontierControlledSearch smoke test ===\n")

    # 1. Create a FrontierBoundaryDescriptor
    descriptor = FrontierBoundaryDescriptor(
        descriptor_id="desc-smoke-001",
        min_breadth=3,
        max_breadth=15,
        max_curvature=1.5,
        min_velocity=0.2,
        phase_label="STABLE",
    )
    print(f"Descriptor: {descriptor.descriptor_id}, breadth_range={descriptor.breadth_range()}")

    # 2. Create a FrontierControlState with various values
    state_healthy = FrontierControlState(
        frontier_id="frontier-001",
        breadth=8,
        depth=3.5,
        curvature=0.7,
        velocity=0.5,
        phase="EXPANDING",
        metadata={"origin": "smoke-test"},
    )
    print(f"State valid: {state_healthy.is_valid()}")

    state_overshoot = FrontierControlState(
        frontier_id="frontier-002",
        breadth=25,
        depth=5.0,
        curvature=0.3,
        velocity=1.8,
        phase="EXPANDING",
    )

    state_turbulent = FrontierControlState(
        frontier_id="frontier-003",
        breadth=6,
        depth=2.0,
        curvature=3.0,
        velocity=0.1,
        phase="TURBULENT",
    )

    # 3. Run the coordinator on each state
    coordinator = FrontierControlledSearchCoordinator(
        descriptor=descriptor,
        max_iterations=10,
    )

    for state in [state_healthy, state_overshoot, state_turbulent]:
        witness = coordinator.run(state)
        print(f"\nWitness [{witness.witness_id}]")
        print(f"  success={witness.success}")
        print(f"  summary={witness.summary_text}")
        print(f"  signal={witness.control_signal.direction} "
              f"mag={witness.control_signal.magnitude:.4f}")
        print(f"  health={witness.analysis_report['health']:.4f}")

    # 4. Test standalone functions
    node_ids = [f"node-{i}" for i in range(10)]
    breadth = compute_frontier_breadth(node_ids)
    depths = [1.0, 2.0, 3.5, 4.0, 5.5]
    depth = compute_frontier_depth(depths)
    curvature = compute_frontier_curvature(depths)
    phase = classify_frontier_phase(breadth, velocity=0.8, curvature=0.5)
    health = frontier_health_score(state_healthy, descriptor)

    print(f"\nStandalone functions:")
    print(f"  breadth={breadth}, depth={depth:.3f}, curvature={curvature:.4f}")
    print(f"  phase={phase}, health={health:.4f}")

    # 5. Assert success
    witness_healthy = coordinator.run(state_healthy)
    assert witness_healthy.success, "Healthy state should produce a successful witness"
    assert witness_healthy.control_signal.direction in {"expand", "contract", "redirect", "hold"}
    assert 0.0 <= witness_healthy.analysis_report["health"] <= 1.0

    print("\n✓ All assertions passed.")
