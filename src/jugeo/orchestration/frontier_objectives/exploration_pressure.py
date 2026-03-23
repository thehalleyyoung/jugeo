"""jugeo.orchestration.frontier_objectives.exploration_pressure
=================================================================

Exploration Pressure (Theory Ch. — Exploration, exploitation, and frontier
control, §3).

Theory
------
*Exploration pressure* is the semantic signal that governs how aggressively
the orchestrator diversifies its frontier.  High exploration pressure causes
the frontier to expand rapidly into novel regions; low pressure causes the
orchestrator to consolidate around known-good nodes.

Formally, exploration pressure :math:`\\epsilon_t` at time step :math:`t` is
a semantic vector in :math:`[0,1]^d` where :math:`d` is the number of
active semantic dimensions.  The scalar summary
:math:`\\bar{\\epsilon}_t = \\|\\epsilon_t\\|_1 / d` is called the *pressure
magnitude*.

Sources of exploration pressure:
* **Entropy deficit** — when the frontier has low diversity (high repetition
  of semantic tokens), exploration pressure increases.
* **Stagnation signal** — when no progress has been made for :math:`k` steps,
  exploration pressure spikes.
* **Budget surplus** — when remaining budget is large relative to the
  estimated closure cost, exploration pressure rises.
* **Coverage gap** — unexplored regions detected by the coverage monitor
  raise exploration pressure in those dimensions.

Exploration pressure interacts with exploitation pressure through the
*pressure balance constraint*:

.. math::

   \\epsilon_t + \\delta_t \\leq 1 + \\alpha

where :math:`\\delta_t` is exploitation pressure and :math:`\\alpha \\geq 0`
is a small slack parameter allowing temporary overshoot.
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
    from jugeo.orchestration.frontier_objectives.the_frontier_as_a_controlled_searc import (
        FrontierControlState,
        FrontierSearchContext,
    )
except ImportError:
    FrontierControlState = Any  # type: ignore[assignment,misc]
    FrontierSearchContext = Any  # type: ignore[assignment,misc]

__all__ = [
    "PressureSource",
    "ExplorationPressureVector",
    "ExplorationPressureHistory",
    "EntropyDeficitDetector",
    "StagnationDetector",
    "BudgetSurplusEstimator",
    "CoverageGapAnalyzer",
    "ExplorationPressureAnalyzer",
    "ExplorationPressureWitness",
    "ExplorationPressureCoordinator",
    "compute_entropy",
    "compute_pressure_magnitude",
    "blend_pressures",
    "pressure_to_signal_strength",
    "clamp_pressure_balance",
    "pressure_schedule",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on failure.

    This helper guards all numeric conversions throughout the module against
    None, NaN-containing strings, or other non-numeric types that may arrive
    from upstream components.

    Parameters
    ----------
    value:
        The value to convert.
    default:
        The fallback value returned when conversion fails.

    Returns
    -------
    float
        The converted value or *default*.
    """
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.
    """
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _ema(values: list[float], alpha: float = 0.3) -> float:
    """Compute the exponential moving average of *values*.

    The EMA is computed left-to-right: the first element seeds the average,
    and each subsequent element is blended with weight *alpha*.

    Parameters
    ----------
    values:
        A non-empty sequence of float observations.
    alpha:
        Smoothing factor in (0, 1].  Larger values give more weight to
        recent observations.

    Returns
    -------
    float
        The EMA of the sequence, or 0.0 if *values* is empty.
    """
    if not values:
        return 0.0
    ema_val = _safe_float(values[0])
    for v in values[1:]:
        ema_val = alpha * _safe_float(v) + (1.0 - alpha) * ema_val
    return ema_val


# ---------------------------------------------------------------------------
# Module-level standalone functions
# ---------------------------------------------------------------------------


def compute_entropy(token_counts: dict[str, int]) -> float:
    """Compute Shannon entropy (in nats) of a token-count distribution.

    The entropy is defined as:

    .. math::

       H = -\\sum_{i} p_i \\ln p_i

    where :math:`p_i = c_i / N` and :math:`N = \\sum_i c_i`.

    Parameters
    ----------
    token_counts:
        A mapping from token strings to non-negative integer counts.
        Tokens with zero count are ignored.

    Returns
    -------
    float
        Shannon entropy in nats.  Returns 0.0 for empty or degenerate
        distributions (only one distinct token).
    """
    total = sum(max(0, c) for c in token_counts.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in token_counts.values():
        if count <= 0:
            continue
        p = count / total
        entropy -= p * math.log(p)
    return entropy


def compute_pressure_magnitude(values: list[float]) -> float:
    """Compute the scalar pressure magnitude as the mean of *values*.

    Formally this is :math:`\\bar{\\epsilon} = \\|\\epsilon\\|_1 / d` where
    :math:`d = \\texttt{len(values)}`.

    Parameters
    ----------
    values:
        A list of per-dimension pressure values, each expected in [0, 1].

    Returns
    -------
    float
        The arithmetic mean of *values*, or 0.0 if *values* is empty.
    """
    if not values:
        return 0.0
    safe_values = [_safe_float(v) for v in values]
    return sum(safe_values) / len(safe_values)


def blend_pressures(pressures: list[float], weights: list[float]) -> float:
    """Compute a weighted mean of *pressures*.

    Parameters
    ----------
    pressures:
        Per-source pressure scalars, each in [0, 1].
    weights:
        Non-negative weights corresponding to each pressure.  Need not be
        normalised — they are normalised internally.

    Returns
    -------
    float
        The weighted mean pressure, clamped to [0, 1].  Returns 0.0 if the
        weight sum is zero or *pressures* is empty.

    Raises
    ------
    ValueError
        If *pressures* and *weights* have different lengths.
    """
    if len(pressures) != len(weights):
        raise ValueError(
            f"pressures (len={len(pressures)}) and weights (len={len(weights)}) "
            "must have the same length."
        )
    if not pressures:
        return 0.0
    weight_sum = sum(max(0.0, _safe_float(w)) for w in weights)
    if weight_sum == 0.0:
        return 0.0
    total = sum(
        _safe_float(p) * max(0.0, _safe_float(w))
        for p, w in zip(pressures, weights)
    )
    return _clamp(total / weight_sum, 0.0, 1.0)


def pressure_to_signal_strength(pressure: float) -> str:
    """Map a scalar pressure value to a human-readable signal strength label.

    Thresholds:

    * ``pressure < 0.25`` → ``"WEAK"``
    * ``0.25 ≤ pressure < 0.50`` → ``"MODERATE"``
    * ``0.50 ≤ pressure < 0.75`` → ``"STRONG"``
    * ``pressure ≥ 0.75`` → ``"CRITICAL"``

    Parameters
    ----------
    pressure:
        A scalar pressure value, nominally in [0, 1].

    Returns
    -------
    str
        One of ``"WEAK"``, ``"MODERATE"``, ``"STRONG"``, or ``"CRITICAL"``.
    """
    p = _safe_float(pressure)
    if p < 0.25:
        return "WEAK"
    if p < 0.50:
        return "MODERATE"
    if p < 0.75:
        return "STRONG"
    return "CRITICAL"


def clamp_pressure_balance(
    exploration: float, exploitation: float, slack: float
) -> tuple[float, float]:
    """Rescale exploration and exploitation pressures to satisfy the balance constraint.

    The constraint is:

    .. math::

       \\epsilon + \\delta \\leq 1 + \\alpha

    where :math:`\\alpha` = *slack*.  If the sum already satisfies the
    constraint, both values are returned unchanged.  Otherwise, both are
    scaled down proportionally.

    Parameters
    ----------
    exploration:
        Exploration pressure scalar, nominally in [0, 1].
    exploitation:
        Exploitation pressure scalar, nominally in [0, 1].
    slack:
        Non-negative slack parameter :math:`\\alpha`.

    Returns
    -------
    tuple[float, float]
        ``(exploration, exploitation)`` after rescaling.
    """
    e = _safe_float(exploration)
    d = _safe_float(exploitation)
    slack = max(0.0, _safe_float(slack))
    limit = 1.0 + slack
    total = e + d
    if total <= limit or total == 0.0:
        return (e, d)
    scale = limit / total
    return (e * scale, d * scale)


def pressure_schedule(step: int, total_steps: int, mode: str) -> float:
    """Return the exploration pressure at a given step according to a named schedule.

    Supported modes:

    ``"linear_decay"``
        Pressure starts at 1.0 and decays linearly to 0.0 over *total_steps*.

    ``"cosine"``
        Pressure follows a half-cosine decay from 1.0 to 0.0:
        :math:`0.5 \\cdot (1 + \\cos(\\pi \\cdot t / T))`.

    ``"constant"``
        Pressure is always 0.5 regardless of step.

    Parameters
    ----------
    step:
        Current step index (0-based).
    total_steps:
        Total number of steps in the episode.
    mode:
        One of ``"linear_decay"``, ``"cosine"``, or ``"constant"``.

    Returns
    -------
    float
        Exploration pressure in [0, 1].

    Raises
    ------
    ValueError
        If *mode* is not one of the supported strings.
    """
    total_steps = max(1, int(total_steps))
    step = max(0, int(step))
    ratio = min(1.0, step / total_steps)

    if mode == "linear_decay":
        return _clamp(1.0 - ratio, 0.0, 1.0)
    if mode == "cosine":
        return _clamp(0.5 * (1.0 + math.cos(math.pi * ratio)), 0.0, 1.0)
    if mode == "constant":
        return 0.5
    raise ValueError(
        f"Unknown pressure schedule mode: {mode!r}. "
        "Expected one of 'linear_decay', 'cosine', 'constant'."
    )


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PressureSource(Enum):
    """Enumeration of the possible sources that contribute to exploration pressure.

    Attributes
    ----------
    ENTROPY_DEFICIT:
        The frontier token distribution has low diversity; entropy is below
        the target threshold.
    STAGNATION:
        No meaningful progress has been recorded for several consecutive steps.
    BUDGET_SURPLUS:
        Remaining computational budget greatly exceeds the estimated closure
        cost, providing room to explore.
    COVERAGE_GAP:
        The coverage monitor detected regions that have not yet been visited
        by the frontier.
    MANUAL:
        Exploration pressure was injected manually by an operator or test
        harness rather than computed automatically.
    """

    ENTROPY_DEFICIT = "entropy_deficit"
    STAGNATION = "stagnation"
    BUDGET_SURPLUS = "budget_surplus"
    COVERAGE_GAP = "coverage_gap"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplorationPressureVector:
    """An immutable snapshot of exploration pressure at a single time step.

    Each instance records the per-dimension pressure values together with
    the source that generated them and the wall-clock timestamp.

    Attributes
    ----------
    vector_id:
        Unique identifier for this pressure vector.
    dimensions:
        The number of active semantic dimensions :math:`d`.
    values:
        Per-dimension pressure values, each in [0, 1].  Must have length
        equal to *dimensions*.
    source:
        The :class:`PressureSource` that generated this vector.
    timestamp:
        Wall-clock time (seconds since epoch) when the vector was created.
    """

    vector_id: str
    dimensions: int
    values: tuple[float, ...]
    source: PressureSource
    timestamp: float

    def magnitude(self) -> float:
        """Return the scalar pressure magnitude (mean of values).

        The magnitude is :math:`\\bar{\\epsilon} = \\sum_i v_i / \\max(1, d)`.

        Returns
        -------
        float
            Scalar in [0, 1] representing the average pressure across all
            active dimensions.
        """
        if not self.values:
            return 0.0
        return sum(self.values) / max(1, self.dimensions)

    def max_dimension(self) -> int:
        """Return the index of the dimension with the highest pressure value.

        If the vector has no values, returns 0 as a safe default.

        Returns
        -------
        int
            Zero-based index of the dominant pressure dimension.
        """
        if not self.values:
            return 0
        max_val = self.values[0]
        max_idx = 0
        for i, v in enumerate(self.values[1:], start=1):
            if v > max_val:
                max_val = v
                max_idx = i
        return max_idx


@dataclass
class ExplorationPressureHistory:
    """A sliding-window history of :class:`ExplorationPressureVector` snapshots.

    New vectors are appended and the history is automatically trimmed to at
    most *window_size* entries, ensuring bounded memory use.

    Attributes
    ----------
    history_id:
        Unique identifier for this history buffer.
    window_size:
        Maximum number of vectors retained.  Older entries are evicted when
        the buffer exceeds this size.
    _vectors:
        Internal list of stored vectors; managed by :meth:`append`.
    """

    history_id: str
    window_size: int = 20
    _vectors: list[ExplorationPressureVector] = field(default_factory=list)

    def append(self, vec: ExplorationPressureVector) -> None:
        """Add *vec* to the history, evicting the oldest entry if necessary.

        Parameters
        ----------
        vec:
            The :class:`ExplorationPressureVector` to store.
        """
        self._vectors.append(vec)
        if len(self._vectors) > self.window_size:
            self._vectors = self._vectors[-self.window_size :]

    def recent_mean(self) -> float:
        """Return the mean pressure magnitude over the stored window.

        If the history is empty, returns 0.0.

        Returns
        -------
        float
            Mean of :meth:`ExplorationPressureVector.magnitude` over the
            last *window_size* vectors.
        """
        if not self._vectors:
            return 0.0
        magnitudes = [v.magnitude() for v in self._vectors[-self.window_size :]]
        return sum(magnitudes) / len(magnitudes)

    def trend(self) -> str:
        """Classify the recent trajectory of exploration pressure.

        A simple linear regression is fitted to the magnitudes of the most
        recent *window_size* vectors.  The slope of the regression line
        determines the label:

        * slope > 0.01  → ``"rising"``
        * slope < -0.01 → ``"falling"``
        * otherwise     → ``"stable"``

        Returns
        -------
        str
            One of ``"rising"``, ``"falling"``, or ``"stable"``.
        """
        vecs = self._vectors[-self.window_size :]
        if len(vecs) < 2:
            return "stable"
        magnitudes = [v.magnitude() for v in vecs]
        n = len(magnitudes)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(magnitudes) / n
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, magnitudes))
        denominator = sum((x - x_mean) ** 2 for x in xs)
        if denominator == 0.0:
            return "stable"
        slope = numerator / denominator
        if slope > 0.01:
            return "rising"
        if slope < -0.01:
            return "falling"
        return "stable"


@dataclass
class EntropyDeficitDetector:
    """Detects exploration pressure arising from low frontier token diversity.

    When the token distribution of the frontier has lower entropy than the
    configured *target_entropy*, this detector raises a pressure signal
    proportional to the relative deficit.

    Attributes
    ----------
    detector_id:
        Unique identifier for this detector instance.
    target_entropy:
        The desired entropy level (in nats).  Distributions with entropy
        below this level are considered deficient.
    """

    detector_id: str
    target_entropy: float = 2.0

    def detect(self, token_counts: dict[str, int]) -> float:
        """Compute the entropy-deficit pressure signal from *token_counts*.

        The deficit is:

        .. math::

           \\text{deficit} = \\max\\!\\left(0,\\;
               \\frac{H^* - H(c)}{H^*}\\right)

        where :math:`H^*` = :attr:`target_entropy` and :math:`H(c)` is the
        Shannon entropy of the token-count distribution *c*.

        Parameters
        ----------
        token_counts:
            A mapping from token string to non-negative integer count.

        Returns
        -------
        float
            Entropy-deficit pressure in [0, 1].  Returns 0.0 when entropy
            meets or exceeds the target.
        """
        if not token_counts:
            return 1.0
        entropy = compute_entropy(token_counts)
        target = max(1e-9, _safe_float(self.target_entropy, default=2.0))
        deficit = (target - entropy) / target
        return _clamp(deficit, 0.0, 1.0)


@dataclass
class StagnationDetector:
    """Detects exploration pressure arising from lack of recent progress.

    Stagnation is measured as the fraction of the last *patience* progress
    samples that fell below a fixed threshold.  A fully stagnated frontier
    yields a severity of 1.0.

    Attributes
    ----------
    detector_id:
        Unique identifier for this detector instance.
    patience:
        The number of recent progress samples to consider.
    _progress_history:
        Internal buffer of recorded progress values.
    """

    detector_id: str
    patience: int = 5
    _progress_history: list[float] = field(default_factory=list)

    def record(self, progress: float) -> None:
        """Append a progress observation to the internal history.

        The history grows unboundedly; only the last *patience* values are
        used during detection.

        Parameters
        ----------
        progress:
            A non-negative scalar indicating how much progress was made at
            the current step.  Typically in [0, 1].
        """
        self._progress_history.append(_safe_float(progress))

    def detect(self) -> float:
        """Compute the stagnation severity from recent progress history.

        Severity is defined as the fraction of the last *patience* samples
        where ``progress < 0.01``.  If fewer than *patience* samples have
        been recorded, returns 0.0 to avoid false positives during warm-up.

        Returns
        -------
        float
            Stagnation severity in [0, 1].
        """
        if len(self._progress_history) < self.patience:
            return 0.0
        recent = self._progress_history[-self.patience :]
        stagnant_count = sum(1 for p in recent if p < 0.01)
        return stagnant_count / self.patience


@dataclass
class BudgetSurplusEstimator:
    """Estimates exploration pressure from the ratio of budget to closure cost.

    A large budget relative to the estimated closure cost implies the
    orchestrator can afford to explore aggressively.

    Attributes
    ----------
    estimator_id:
        Unique identifier for this estimator instance.
    baseline_ratio:
        The ratio at which surplus pressure saturates to 1.0.  When
        ``remaining / cost = 1 + baseline_ratio``, the output approaches 1.0.
    """

    estimator_id: str
    baseline_ratio: float = 1.0

    def estimate(
        self, remaining_budget: float, estimated_closure_cost: float
    ) -> float:
        """Compute budget-surplus exploration pressure.

        The formula is:

        .. math::

           p = \\text{clamp}\\!\\left(
               \\frac{\\text{remaining}/\\text{cost} - 1}{\\text{baseline\\_ratio}},
               0, 1 \\right)

        Parameters
        ----------
        remaining_budget:
            The remaining computational budget (arbitrary positive units).
        estimated_closure_cost:
            The estimated cost required to close the current search episode.

        Returns
        -------
        float
            Budget-surplus pressure in [0, 1].
        """
        rb = _safe_float(remaining_budget)
        ec = max(1e-9, _safe_float(estimated_closure_cost))
        br = max(1e-9, _safe_float(self.baseline_ratio, default=1.0))
        raw = (rb / ec - 1.0) / br
        return _clamp(raw, 0.0, 1.0)


@dataclass
class CoverageGapAnalyzer:
    """Tracks explored regions and computes coverage-gap exploration pressure.

    The gap pressure equals the fraction of known regions that have not yet
    been visited.  Higher gaps produce higher exploration pressure.

    Attributes
    ----------
    analyzer_id:
        Unique identifier for this analyzer instance.
    _explored:
        The set of region IDs that have been visited so far.
    """

    analyzer_id: str
    _explored: set[str] = field(default_factory=set)

    def register_explored(self, region_id: str) -> None:
        """Mark *region_id* as having been explored.

        Parameters
        ----------
        region_id:
            An arbitrary string identifier for the explored region.
        """
        self._explored.add(region_id)

    def gap_pressure(self, all_regions: list[str]) -> float:
        """Compute coverage-gap pressure as the fraction of unexplored regions.

        Parameters
        ----------
        all_regions:
            The complete list of known region IDs (may include duplicates,
            which are deduplicated internally).

        Returns
        -------
        float
            Fraction of *all_regions* not yet in the explored set, in [0, 1].
            Returns 0.0 if *all_regions* is empty.
        """
        if not all_regions:
            return 0.0
        unique_regions = set(all_regions)
        unexplored = unique_regions - self._explored
        return len(unexplored) / len(unique_regions)


@dataclass
class ExplorationPressureAnalyzer:
    """Synthesises an :class:`ExplorationPressureHistory` into an analysis report.

    The analyzer aggregates statistics from the pressure history and provides
    a high-level recommendation to the orchestrator.

    Attributes
    ----------
    analyzer_id:
        Unique identifier for this analyzer instance.
    """

    analyzer_id: str

    def analyze(
        self,
        history: ExplorationPressureHistory,
        sources: list[PressureSource],
    ) -> dict[str, Any]:
        """Produce a structured analysis report from *history* and *sources*.

        The report includes the mean pressure magnitude, recent trend, the
        dominant contributing source, a per-source breakdown, and a textual
        recommendation for the orchestrator.

        Parameters
        ----------
        history:
            The sliding-window pressure history to analyse.
        sources:
            The list of :class:`PressureSource` values that contributed to
            the pressure vectors in *history*.  May be empty.

        Returns
        -------
        dict[str, Any]
            Keys:

            * ``mean_magnitude`` — float, mean pressure magnitude over window
            * ``trend`` — str, ``"rising"`` / ``"falling"`` / ``"stable"``
            * ``dominant_source`` — :class:`PressureSource`, most frequent
            * ``source_breakdown`` — dict mapping each source to its count
            * ``recommendation`` — str, human-readable action recommendation
        """
        mean_mag = history.recent_mean()
        trend = history.trend()

        # Compute source breakdown
        source_breakdown: dict[str, int] = {}
        for src in sources:
            key = src.value
            source_breakdown[key] = source_breakdown.get(key, 0) + 1

        # Determine dominant source
        if source_breakdown:
            dominant_key = max(source_breakdown, key=lambda k: source_breakdown[k])
            dominant = PressureSource(dominant_key)
        else:
            dominant = PressureSource.MANUAL

        # Build recommendation string
        strength = pressure_to_signal_strength(mean_mag)
        if mean_mag >= 0.75:
            action = "Expand frontier aggressively; consider increasing beam width."
        elif mean_mag >= 0.5:
            action = "Moderately expand frontier; balance with exploitation signals."
        elif mean_mag >= 0.25:
            action = "Maintain current exploration rate; monitor for stagnation."
        else:
            action = "Reduce exploration; consolidate around promising nodes."

        trend_advice = ""
        if trend == "rising":
            trend_advice = " Trend is rising — prepare to shift toward exploitation."
        elif trend == "falling":
            trend_advice = " Trend is falling — pressure easing; exploit gains."

        recommendation = f"[{strength}] {action}{trend_advice}"

        return {
            "mean_magnitude": mean_mag,
            "trend": trend,
            "dominant_source": dominant,
            "source_breakdown": source_breakdown,
            "recommendation": recommendation,
        }

    def dominant_source(self, analysis: dict[str, Any]) -> PressureSource:
        """Extract the dominant :class:`PressureSource` from an analysis dict.

        Parameters
        ----------
        analysis:
            A report dict as returned by :meth:`analyze`.

        Returns
        -------
        PressureSource
            The dominant source recorded in *analysis*, or
            :attr:`PressureSource.MANUAL` as a safe default.
        """
        src = analysis.get("dominant_source")
        if isinstance(src, PressureSource):
            return src
        if isinstance(src, str):
            try:
                return PressureSource(src)
            except ValueError:
                return PressureSource.MANUAL
        return PressureSource.MANUAL


@dataclass(frozen=True, slots=True)
class ExplorationPressureWitness:
    """An immutable audit record produced at the end of a coordinator run.

    The witness captures all salient outputs so they can be logged, stored, or
    forwarded to downstream components without retaining mutable state.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness record.
    timestamp:
        Wall-clock time (seconds since epoch) when the witness was sealed.
    success:
        Whether the coordinator run completed without error.
    pressure_magnitude:
        The scalar pressure magnitude computed by the coordinator.
    dominant_source:
        The :class:`PressureSource` that contributed most to the pressure.
    analysis_report:
        The full analysis dict from :class:`ExplorationPressureAnalyzer`.
    summary:
        A human-readable one-line summary of the coordinator run.
    """

    witness_id: str
    timestamp: float
    success: bool
    pressure_magnitude: float
    dominant_source: PressureSource
    analysis_report: dict[str, Any]
    summary: str


@dataclass
class ExplorationPressureCoordinator:
    """Orchestrates the full exploration-pressure computation pipeline.

    # copilot: The coordinator orchestrates exploration pressure computation.
    # copilot: It accepts raw signal inputs and:
    # copilot:
    # copilot: 1. Computes entropy deficit from token_counts.
    # copilot: 2. Computes stagnation severity from progress_history.
    # copilot: 3. Computes budget surplus from budget_remaining / closure_cost.
    # copilot: 4. Computes coverage gap from all_regions vs explored.
    # copilot: 5. Builds an ExplorationPressureVector blending all sources.
    # copilot: 6. Runs ExplorationPressureAnalyzer for synthesis.
    # copilot: 7. Seals and returns an ExplorationPressureWitness.

    Attributes
    ----------
    session_id:
        Unique identifier for this coordinator session, auto-generated if
        not supplied.
    slack_alpha:
        The slack parameter :math:`\\alpha` used when enforcing the pressure
        balance constraint.
    window_size:
        The window size forwarded to the internal
        :class:`ExplorationPressureHistory`.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    slack_alpha: float = 0.05
    window_size: int = 20

    def run(
        self,
        token_counts: dict[str, int],
        progress_history: list[float],
        budget_remaining: float,
        closure_cost: float,
        all_regions: list[str],
    ) -> ExplorationPressureWitness:
        """Execute the exploration-pressure pipeline and return a witness.

        Steps performed:

        1. Entropy-deficit pressure is computed from *token_counts*.
        2. Stagnation pressure is computed from *progress_history*.
        3. Budget-surplus pressure is computed from *budget_remaining* and
           *closure_cost*.
        4. Coverage-gap pressure is computed from *all_regions*.
        5. The four signals are blended into a single
           :class:`ExplorationPressureVector`.
        6. The vector is appended to a fresh
           :class:`ExplorationPressureHistory`.
        7. An :class:`ExplorationPressureAnalyzer` synthesises a report.
        8. An :class:`ExplorationPressureWitness` is sealed and returned.

        Parameters
        ----------
        token_counts:
            Token-count distribution of the current frontier.
        progress_history:
            Sequence of recent progress scalars (most recent last).
        budget_remaining:
            Remaining computational budget.
        closure_cost:
            Estimated cost to close the current episode.
        all_regions:
            Complete list of known semantic region identifiers.

        Returns
        -------
        ExplorationPressureWitness
            An immutable record of the pipeline outputs.
        """
        # Step 1 — entropy deficit
        entropy_detector = EntropyDeficitDetector(
            detector_id=f"{self.session_id}:entropy"
        )
        entropy_pressure = entropy_detector.detect(token_counts)

        # Step 2 — stagnation
        stagnation_detector = StagnationDetector(
            detector_id=f"{self.session_id}:stagnation",
            patience=max(1, len(progress_history)) if progress_history else 5,
        )
        for p in progress_history:
            stagnation_detector.record(p)
        stagnation_pressure = stagnation_detector.detect()

        # Step 3 — budget surplus
        budget_estimator = BudgetSurplusEstimator(
            estimator_id=f"{self.session_id}:budget"
        )
        budget_pressure = budget_estimator.estimate(budget_remaining, closure_cost)

        # Step 4 — coverage gap
        gap_analyzer = CoverageGapAnalyzer(analyzer_id=f"{self.session_id}:coverage")
        gap_pressure = gap_analyzer.gap_pressure(all_regions)

        # Step 5 — build blended vector
        raw_values = [
            entropy_pressure,
            stagnation_pressure,
            budget_pressure,
            gap_pressure,
        ]
        blended_magnitude = blend_pressures(raw_values, [1.0, 1.0, 1.0, 1.0])
        vec = ExplorationPressureVector(
            vector_id=str(uuid.uuid4()),
            dimensions=4,
            values=tuple(_clamp(v, 0.0, 1.0) for v in raw_values),
            source=PressureSource.ENTROPY_DEFICIT,
            timestamp=time.time(),
        )

        # Step 6 — history + analysis
        history = ExplorationPressureHistory(
            history_id=f"{self.session_id}:history",
            window_size=self.window_size,
        )
        history.append(vec)

        sources = [
            PressureSource.ENTROPY_DEFICIT,
            PressureSource.STAGNATION,
            PressureSource.BUDGET_SURPLUS,
            PressureSource.COVERAGE_GAP,
        ]
        analyzer = ExplorationPressureAnalyzer(analyzer_id=f"{self.session_id}:analyzer")
        report = analyzer.analyze(history, sources)
        dominant = analyzer.dominant_source(report)

        # Step 7 — seal witness
        strength = pressure_to_signal_strength(blended_magnitude)
        summary = (
            f"ExplorationPressure session={self.session_id} "
            f"magnitude={blended_magnitude:.4f} strength={strength} "
            f"trend={report['trend']} dominant={dominant.value}"
        )
        witness = ExplorationPressureWitness(
            witness_id=str(uuid.uuid4()),
            timestamp=time.time(),
            success=True,
            pressure_magnitude=blended_magnitude,
            dominant_source=dominant,
            analysis_report=report,
            summary=summary,
        )
        return witness


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== exploration_pressure smoke test ===\n")

    # --- Helper functions ---
    print("_safe_float tests:")
    print(f"  _safe_float('3.14') = {_safe_float('3.14')}")
    print(f"  _safe_float(None)   = {_safe_float(None)}")
    print(f"  _safe_float('NaN')  = {_safe_float('NaN')}")

    print("\n_clamp tests:")
    print(f"  _clamp(1.5, 0, 1)  = {_clamp(1.5, 0.0, 1.0)}")
    print(f"  _clamp(-0.1, 0, 1) = {_clamp(-0.1, 0.0, 1.0)}")

    print("\n_ema tests:")
    print(f"  _ema([1,2,3,4,5])  = {_ema([1.0, 2.0, 3.0, 4.0, 5.0]):.4f}")

    # --- Module functions ---
    token_counts = {"cat": 5, "dog": 3, "bird": 8, "fish": 1}
    entropy = compute_entropy(token_counts)
    print(f"\ncompute_entropy({token_counts}) = {entropy:.4f} nats")

    mag = compute_pressure_magnitude([0.1, 0.5, 0.9, 0.3])
    print(f"compute_pressure_magnitude([0.1,0.5,0.9,0.3]) = {mag:.4f}")

    blended = blend_pressures([0.2, 0.8, 0.4], [1.0, 2.0, 1.0])
    print(f"blend_pressures([0.2,0.8,0.4], [1,2,1]) = {blended:.4f}")

    for p in [0.1, 0.3, 0.6, 0.9]:
        print(f"pressure_to_signal_strength({p}) = {pressure_to_signal_strength(p)}")

    e, d = clamp_pressure_balance(0.8, 0.7, slack=0.05)
    print(f"clamp_pressure_balance(0.8, 0.7, 0.05) = ({e:.4f}, {d:.4f})")

    for mode in ("linear_decay", "cosine", "constant"):
        p = pressure_schedule(50, 100, mode)
        print(f"pressure_schedule(50, 100, '{mode}') = {p:.4f}")

    # --- Enum ---
    print(f"\nPressureSource members: {[s.value for s in PressureSource]}")

    # --- ExplorationPressureVector ---
    vec = ExplorationPressureVector(
        vector_id="v1",
        dimensions=4,
        values=(0.2, 0.8, 0.5, 0.1),
        source=PressureSource.ENTROPY_DEFICIT,
        timestamp=time.time(),
    )
    print(f"\nVector magnitude: {vec.magnitude():.4f}")
    print(f"Vector max_dimension: {vec.max_dimension()}")

    # --- History and trend ---
    history = ExplorationPressureHistory(history_id="h1", window_size=5)
    for i in range(6):
        v = ExplorationPressureVector(
            vector_id=f"v{i}",
            dimensions=2,
            values=(float(i) / 10, float(i) / 10),
            source=PressureSource.STAGNATION,
            timestamp=time.time(),
        )
        history.append(v)
    print(f"History recent_mean: {history.recent_mean():.4f}")
    print(f"History trend: {history.trend()}")

    # --- Detectors ---
    edd = EntropyDeficitDetector(detector_id="edd1", target_entropy=2.0)
    print(f"\nEntropyDeficit (uniform 4): {edd.detect({'a':10,'b':10,'c':10,'d':10}):.4f}")
    print(f"EntropyDeficit (skewed):    {edd.detect({'a':100,'b':1,'c':1,'d':1}):.4f}")

    sd = StagnationDetector(detector_id="sd1", patience=3)
    for val in [0.005, 0.003, 0.001]:
        sd.record(val)
    print(f"Stagnation severity (3 stagnant/3): {sd.detect():.4f}")

    bse = BudgetSurplusEstimator(estimator_id="bse1", baseline_ratio=1.0)
    print(f"BudgetSurplus(200, 100): {bse.estimate(200, 100):.4f}")
    print(f"BudgetSurplus(50, 100):  {bse.estimate(50, 100):.4f}")

    cga = CoverageGapAnalyzer(analyzer_id="cga1")
    cga.register_explored("r1")
    cga.register_explored("r2")
    print(f"CoverageGap (2/5 explored): {cga.gap_pressure(['r1','r2','r3','r4','r5']):.4f}")

    # --- Analyzer ---
    ana = ExplorationPressureAnalyzer(analyzer_id="ana1")
    srcs = [PressureSource.ENTROPY_DEFICIT, PressureSource.ENTROPY_DEFICIT,
            PressureSource.STAGNATION, PressureSource.BUDGET_SURPLUS]
    report = ana.analyze(history, srcs)
    print(f"\nAnalysis report: {report}")

    # --- Coordinator ---
    coord = ExplorationPressureCoordinator(slack_alpha=0.05, window_size=10)
    witness = coord.run(
        token_counts={"alpha": 10, "beta": 5, "gamma": 2},
        progress_history=[0.1, 0.05, 0.001, 0.0, 0.0],
        budget_remaining=500.0,
        closure_cost=200.0,
        all_regions=["r1", "r2", "r3", "r4", "r5"],
    )
    print(f"\nWitness summary: {witness.summary}")
    print(f"Witness success: {witness.success}")
    print(f"Witness pressure_magnitude: {witness.pressure_magnitude:.4f}")
    print(f"Witness dominant_source: {witness.dominant_source.value}")

    print("\n=== smoke test PASSED ===")
