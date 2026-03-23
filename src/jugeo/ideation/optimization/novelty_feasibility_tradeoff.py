"""Novelty-feasibility tradeoff analysis for JuGeo ideation optimization (Ch50).

Maps the Pareto frontier between novelty and feasibility objectives, provides
interpolation, convex hull approximation, and regret minimization.
"""

from __future__ import annotations

import logging
import math
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import OptimizationProblem, SolutionCandidate, ParetoFront, OptimizationResult
from .objective_functions import ObjectiveEvaluator, ObjectiveFactory

try:
    from jugeo.ideation.ideas import IdeaProposal
except ImportError:
    IdeaProposal = Any  # type: ignore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Module-level helpers
# ---------------------------------------------------------------------------


def _euclidean(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compute the Euclidean distance between two equal-length tuples."""
    if len(a) != len(b):
        raise ValueError(f"Dimension mismatch: {len(a)} vs {len(b)}")
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _convex_hull_2d(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Compute the 2-D convex hull using Andrew's monotone chain algorithm.

    Returns the hull vertices in counter-clockwise order.  Collinear points
    are excluded.  Duplicate points are silently removed before processing.
    """
    unique = sorted(set(points))
    n = len(unique)
    if n <= 2:
        return unique

    def _cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in unique:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[tuple[float, float]] = []
    for p in reversed(unique):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def _normalize_weights(w: dict[str, float]) -> dict[str, float]:
    """Return a copy of *w* scaled so values sum to 1.0.

    If the total weight is zero or negative every key receives equal weight.
    """
    total = sum(max(v, 0.0) for v in w.values())
    if total <= 0.0:
        eq = 1.0 / len(w) if w else 0.0
        return {k: eq for k in w}
    return {k: max(v, 0.0) / total for k, v in w.items()}


def _trapezoidal_area(xs: list[float], ys: list[float]) -> float:
    """Integrate *ys* over *xs* using the trapezoidal rule.

    Both sequences must have the same length.  Returns 0.0 for fewer than
    two points.  The caller is responsible for ensuring *xs* is sorted.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    if len(xs) < 2:
        return 0.0
    area = 0.0
    for i in range(1, len(xs)):
        dx = xs[i] - xs[i - 1]
        area += dx * (ys[i - 1] + ys[i]) / 2.0
    return area


# ---------------------------------------------------------------------------
# 2. TradeoffPoint
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradeoffPoint:
    """A single evaluated point in novelty-feasibility objective space.

    Attributes
    ----------
    idea_id:
        Stable identifier linking back to the originating ``IdeaProposal``.
    novelty:
        Novelty score in [0, 1]; higher is more novel.
    feasibility:
        Feasibility score in [0, 1]; higher is more feasible.
    purpose:
        Purpose-alignment score in [0, 1]; used in composite scoring.
    cost:
        Estimated resource cost in abstract units.
    label:
        Optional human-readable tag (e.g. ``"high-payoff"``).
    """

    idea_id: str
    novelty: float
    feasibility: float
    purpose: float = 0.5
    cost: float = 0.5
    label: str = ""

    def distance_to(self, other: TradeoffPoint) -> float:
        """Return the Euclidean distance in (novelty, feasibility) space."""
        return _euclidean(self.as_tuple(), other.as_tuple())

    def dominated_by(self, other: TradeoffPoint) -> bool:
        """Return ``True`` if *other* weakly dominates *self* in both axes.

        Strict dominance on at least one axis is required; equal performance
        on both axes is *not* considered domination.
        """
        weakly_better = other.novelty >= self.novelty and other.feasibility >= self.feasibility
        strictly_better = other.novelty > self.novelty or other.feasibility > self.feasibility
        return weakly_better and strictly_better

    def summary(self) -> str:
        """Return a compact, human-readable description of this point."""
        lbl = f" [{self.label}]" if self.label else ""
        return (
            f"TradeoffPoint(id={self.idea_id}{lbl}, "
            f"novelty={self.novelty:.3f}, feasibility={self.feasibility:.3f}, "
            f"purpose={self.purpose:.3f}, cost={self.cost:.3f})"
        )

    def as_tuple(self) -> tuple[float, float]:
        """Return *(novelty, feasibility)* as a plain 2-tuple."""
        return (self.novelty, self.feasibility)

    def harmonic_mean(self) -> float:
        """Compute the harmonic mean of novelty and feasibility.

        Returns 0.0 when either component is zero to avoid division by zero.
        """
        n, f = self.novelty, self.feasibility
        if n <= 0.0 or f <= 0.0:
            return 0.0
        return 2.0 * n * f / (n + f)


# ---------------------------------------------------------------------------
# 3. NoveltyFeasibilityFrontier
# ---------------------------------------------------------------------------


class NoveltyFeasibilityFrontier:
    """Maintains and queries the Pareto frontier in novelty-feasibility space.

    Points are stored in insertion order; queries derive the non-dominated
    subset on demand.
    """

    def __init__(self) -> None:
        self._points: list[TradeoffPoint] = []

    def add_point(self, point: TradeoffPoint) -> None:
        """Add *point* to the internal collection."""
        self._points.append(point)
        _log.debug("Added tradeoff point %s", point.idea_id)

    def frontier_points(self) -> list[TradeoffPoint]:
        """Return only the non-dominated (Pareto-optimal) points.

        The result is sorted by ascending novelty score.
        """
        candidates = list(self._points)
        non_dominated: list[TradeoffPoint] = []
        for candidate in candidates:
            if not any(candidate.dominated_by(other) for other in candidates):
                non_dominated.append(candidate)
        return sorted(non_dominated, key=lambda p: p.novelty)

    def interpolate(self, novelty: float) -> float:
        """Linearly interpolate feasibility at a given novelty value.

        Returns 0.5 when the frontier is empty.  Values outside the range
        of known novelty scores are clamped to the nearest endpoint.
        """
        pts = self.frontier_points()
        if not pts:
            return 0.5
        if len(pts) == 1:
            return pts[0].feasibility

        xs = [p.novelty for p in pts]
        ys = [p.feasibility for p in pts]

        if novelty <= xs[0]:
            return ys[0]
        if novelty >= xs[-1]:
            return ys[-1]

        for i in range(1, len(xs)):
            if xs[i - 1] <= novelty <= xs[i]:
                span = xs[i] - xs[i - 1]
                if span < 1e-12:
                    return ys[i - 1]
                t = (novelty - xs[i - 1]) / span
                return ys[i - 1] + t * (ys[i] - ys[i - 1])

        return ys[-1]

    def convex_hull_approximation(self) -> list[TradeoffPoint]:
        """Return the subset of frontier points lying on the convex hull.

        Falls back to all frontier points when fewer than three are available.
        """
        pts = self.frontier_points()
        if len(pts) < 3:
            return pts

        raw: list[tuple[float, float]] = [p.as_tuple() for p in pts]
        hull_coords = set(_convex_hull_2d(raw))

        hull_pts = [p for p in pts if p.as_tuple() in hull_coords]
        if not hull_pts:
            return pts
        return sorted(hull_pts, key=lambda p: p.novelty)

    def area_under_curve(self) -> float:
        """Compute the area under the frontier curve via the trapezoidal rule.

        Returns 0.0 when there are fewer than two frontier points.
        """
        pts = self.frontier_points()
        if len(pts) < 2:
            return 0.0
        xs = [p.novelty for p in pts]
        ys = [p.feasibility for p in pts]
        return _trapezoidal_area(xs, ys)

    def size(self) -> int:
        """Return the total number of stored points (including dominated)."""
        return len(self._points)

    def best_by_harmonic(self) -> TradeoffPoint | None:
        """Return the frontier point with the highest harmonic mean score."""
        pts = self.frontier_points()
        if not pts:
            return None
        return max(pts, key=lambda p: p.harmonic_mean())

    def summary(self) -> str:
        """Return a multi-line summary of the frontier."""
        pts = self.frontier_points()
        lines = [
            f"NoveltyFeasibilityFrontier: {len(self._points)} total points, "
            f"{len(pts)} on frontier",
            f"  AUC = {self.area_under_curve():.4f}",
        ]
        best = self.best_by_harmonic()
        if best is not None:
            lines.append(f"  Best harmonic: {best.summary()}")
        if pts:
            lines.append(
                f"  Novelty range: [{pts[0].novelty:.3f}, {pts[-1].novelty:.3f}]"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. TradeoffAnalyzer
# ---------------------------------------------------------------------------


class TradeoffAnalyzer:
    """Evaluates ideas in novelty-feasibility space and classifies regions.

    The analyzer consumes an :class:`ObjectiveEvaluator` to score ideas on
    multiple dimensions and maps those scores into :class:`TradeoffPoint`
    objects, which are then gathered into a :class:`NoveltyFeasibilityFrontier`.
    """

    def __init__(self) -> None:
        pass

    def analyze(
        self,
        ideas: list[Any],
        evaluator: ObjectiveEvaluator,
    ) -> NoveltyFeasibilityFrontier:
        """Evaluate each idea and construct a novelty-feasibility frontier.

        For each idea a :class:`TradeoffPoint` is created from the evaluator's
        ``novelty`` and ``feasibility`` objective scores.  The ``payoff``
        field drives an additional ``purpose`` bonus.

        Parameters
        ----------
        ideas:
            List of :class:`~jugeo.ideation.ideas.IdeaProposal` instances
            (or any duck-typed object with ``idea_id`` and ``payoff``).
        evaluator:
            Configured :class:`ObjectiveEvaluator` that can score each idea.

        Returns
        -------
        NoveltyFeasibilityFrontier
            Populated frontier ready for querying.
        """
        frontier = NoveltyFeasibilityFrontier()
        for idea in ideas:
            try:
                scores = evaluator.evaluate(idea)
                novelty = float(scores.get("novelty", 0.5))
                feasibility = float(scores.get("feasibility", 0.5))
                purpose = float(scores.get("purpose", 0.5))
                payoff_bonus = min(1.0, max(0.0, idea.payoff / 100.0)) if hasattr(idea, "payoff") else 0.5
                cost = float(scores.get("cost", 0.5))
                idea_id = getattr(idea, "idea_id", str(uuid.uuid4()))
                label = f"payoff={getattr(idea, 'payoff', '?')}"
                point = TradeoffPoint(
                    idea_id=idea_id,
                    novelty=novelty,
                    feasibility=feasibility,
                    purpose=max(0.0, min(1.0, (purpose + payoff_bonus) / 2.0)),
                    cost=cost,
                    label=label,
                )
                frontier.add_point(point)
            except Exception as exc:  # noqa: BLE001
                _log.warning("Failed to evaluate idea %s: %s", getattr(idea, "idea_id", "?"), exc)
        _log.info("Analysis complete: %d ideas → %d frontier points", len(ideas), len(frontier.frontier_points()))
        return frontier

    def tradeoff_score(self, point: TradeoffPoint) -> float:
        """Compute a composite tradeoff score for a single point.

        Score = 0.4 × novelty + 0.4 × feasibility + 0.2 × purpose
        """
        return 0.4 * point.novelty + 0.4 * point.feasibility + 0.2 * point.purpose

    def classify_region(self, novelty: float, feasibility: float) -> str:
        """Classify a (novelty, feasibility) coordinate into a named region.

        Returns one of:
        ``"high-novelty-low-feasibility"``, ``"low-novelty-high-feasibility"``,
        ``"balanced"``, ``"low-value"``, ``"high-value"``.
        """
        threshold_high = 0.65
        threshold_low = 0.35

        high_n = novelty >= threshold_high
        low_n = novelty < threshold_low
        high_f = feasibility >= threshold_high
        low_f = feasibility < threshold_low

        if high_n and high_f:
            return "high-value"
        if low_n and low_f:
            return "low-value"
        if high_n and low_f:
            return "high-novelty-low-feasibility"
        if low_n and high_f:
            return "low-novelty-high-feasibility"
        return "balanced"

    def summary(self, frontier: NoveltyFeasibilityFrontier) -> str:
        """Produce a rich multi-line analysis of the given frontier."""
        pts = frontier.frontier_points()
        if not pts:
            return "TradeoffAnalyzer: no frontier points available."

        scores = [self.tradeoff_score(p) for p in pts]
        regions = [self.classify_region(p.novelty, p.feasibility) for p in pts]
        region_counts: dict[str, int] = {}
        for r in regions:
            region_counts[r] = region_counts.get(r, 0) + 1

        lines = [
            "=== TradeoffAnalyzer Summary ===",
            frontier.summary(),
            f"  Tradeoff scores: min={min(scores):.3f}, "
            f"mean={statistics.mean(scores):.3f}, max={max(scores):.3f}",
            "  Region distribution:",
        ]
        for region, count in sorted(region_counts.items(), key=lambda x: -x[1]):
            lines.append(f"    {region}: {count}")
        best = max(pts, key=self.tradeoff_score)
        lines.append(f"  Best overall: {best.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. AdaptiveWeightSchedule
# ---------------------------------------------------------------------------


class AdaptiveWeightSchedule:
    """Manages an annealing schedule for multi-objective optimization weights.

    Supports linear, cosine, and performance-adaptive schedules.  Weights
    begin at ``initial_weights`` and evolve over ``iterations`` steps.

    Parameters
    ----------
    initial_weights:
        Mapping of objective name → initial weight (need not sum to 1).
    iterations:
        Total number of scheduled iterations.
    schedule_type:
        One of ``"linear"``, ``"cosine"``, or ``"adaptive"``.
    """

    def __init__(
        self,
        initial_weights: dict[str, float],
        iterations: int,
        schedule_type: str = "cosine",
    ) -> None:
        self.initial_weights: dict[str, float] = dict(initial_weights)
        self.iterations: int = max(1, iterations)
        self.schedule_type: str = schedule_type
        self._current_iteration: int = 0
        self._performance_history: list[float] = []

    def weights_at(self, iteration: int) -> dict[str, float]:
        """Return the scheduled weights at a given iteration index.

        linear
            Weights linearly interpolate toward the uniform distribution.
        cosine
            Each weight follows a cosine annealing curve that decays to a
            small positive base value (0.05) by the final iteration.
        adaptive
            Keys whose recent performance improved most receive a proportional
            bonus; otherwise reverts to cosine schedule.
        """
        t = max(0, min(iteration, self.iterations))
        T = self.iterations
        keys = list(self.initial_weights.keys())
        eq = 1.0 / len(keys) if keys else 1.0

        if self.schedule_type == "linear":
            alpha = t / T
            raw = {k: (1.0 - alpha) * self.initial_weights[k] + alpha * eq for k in keys}

        elif self.schedule_type == "cosine":
            base = 0.05
            raw = {
                k: self.initial_weights[k] * (1.0 + math.cos(math.pi * t / T)) / 2.0 + base
                for k in keys
            }

        else:  # adaptive
            if len(self._performance_history) >= 2:
                recent = self._performance_history[-4:]
                if len(recent) >= 2:
                    improvement = max(0.0, recent[-1] - recent[0])
                else:
                    improvement = 0.0
                base_cosine = {
                    k: self.initial_weights[k] * (1.0 + math.cos(math.pi * t / T)) / 2.0 + 0.05
                    for k in keys
                }
                bonus = improvement * 0.3
                raw = {k: base_cosine[k] + bonus for k in keys}
            else:
                raw = {
                    k: self.initial_weights[k] * (1.0 + math.cos(math.pi * t / T)) / 2.0 + 0.05
                    for k in keys
                }

        return _normalize_weights(raw)

    def update(self, performance_metric: float) -> None:
        """Record a performance observation for the adaptive schedule."""
        self._performance_history.append(performance_metric)

    def current_weights(self) -> dict[str, float]:
        """Return the scheduled weights at the current iteration."""
        return self.weights_at(self._current_iteration)

    def advance(self) -> None:
        """Increment the internal iteration counter by one."""
        self._current_iteration = min(self._current_iteration + 1, self.iterations)

    def reset(self) -> None:
        """Reset iteration counter and performance history to initial state."""
        self._current_iteration = 0
        self._performance_history = []


# ---------------------------------------------------------------------------
# 6. RegretMinimizer
# ---------------------------------------------------------------------------


class RegretMinimizer:
    """Minimax-regret decision support over a Pareto frontier.

    Given uncertainty in the true objective weights, this class identifies
    which frontier point minimises the worst-case regret across a set of
    plausible weight scenarios.

    Parameters
    ----------
    frontier:
        A :class:`NoveltyFeasibilityFrontier` whose non-dominated points
        serve as the feasible outcome set.
    """

    def __init__(self, frontier: NoveltyFeasibilityFrontier) -> None:
        self.frontier = frontier

    def regret(self, chosen: TradeoffPoint, realized_weights: dict[str, float]) -> float:
        """Compute the regret of choosing *chosen* under *realized_weights*.

        Regret is defined as the gap between the best achievable value and the
        value actually obtained, clipped to the non-negative reals.

        Parameters
        ----------
        chosen:
            The point that was selected prior to weights being revealed.
        realized_weights:
            The true weight vector used to evaluate outcomes.
        """
        w_n = realized_weights.get("novelty", 0.5)
        w_f = realized_weights.get("feasibility", 0.5)
        realized_value = w_n * chosen.novelty + w_f * chosen.feasibility

        pts = self.frontier.frontier_points()
        if not pts:
            return 0.0

        best_value = max(w_n * p.novelty + w_f * p.feasibility for p in pts)
        return max(0.0, best_value - realized_value)

    def min_regret_choice(
        self,
        candidates: list[TradeoffPoint],
        weight_scenarios: list[dict[str, float]],
    ) -> TradeoffPoint:
        """Return the candidate with minimum maximum regret across all scenarios.

        The classical minimax-regret criterion: for each candidate compute the
        worst-case regret over *weight_scenarios*, then return the candidate
        with the smallest such worst-case value.

        Raises
        ------
        ValueError
            If *candidates* is empty.
        """
        if not candidates:
            raise ValueError("candidates list must not be empty")
        if not weight_scenarios:
            return candidates[0]

        best_candidate = candidates[0]
        best_max_regret = float("inf")

        for candidate in candidates:
            max_regret = max(self.regret(candidate, w) for w in weight_scenarios)
            if max_regret < best_max_regret:
                best_max_regret = max_regret
                best_candidate = candidate

        return best_candidate

    def expected_regret(
        self,
        candidate: TradeoffPoint,
        weight_distribution: list[dict[str, float]],
    ) -> float:
        """Return the average regret of *candidate* across *weight_distribution*.

        All weight scenarios are assumed equally probable.
        """
        if not weight_distribution:
            return 0.0
        total = sum(self.regret(candidate, w) for w in weight_distribution)
        return total / len(weight_distribution)

    def minimax_regret(
        self,
        candidates: list[TradeoffPoint],
        weight_scenarios: list[dict[str, float]],
    ) -> dict[str, float]:
        """Compute the maximum regret for every candidate.

        Returns
        -------
        dict[str, float]
            Mapping ``{candidate.idea_id: max_regret}`` for all candidates.
        """
        result: dict[str, float] = {}
        for candidate in candidates:
            if weight_scenarios:
                max_regret = max(self.regret(candidate, w) for w in weight_scenarios)
            else:
                max_regret = 0.0
            result[candidate.idea_id] = max_regret
        return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "TradeoffPoint",
    "NoveltyFeasibilityFrontier",
    "TradeoffAnalyzer",
    "AdaptiveWeightSchedule",
    "RegretMinimizer",
]
