"""Budget-constrained optimization for JuGeo ideation (Ch50).

Implements knapsack solvers, fractional relaxation, dynamic budget policies,
sensitivity analysis, and a top-level BudgetOptimizer.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import (
    OptimizationProblem,
    SolutionCandidate,
    SolutionStatus,
    ParetoFront,
    OptimizationResult,
)
from .objective_functions import ObjectiveEvaluator, ObjectiveFactory
from .pareto_optimization import ParetoOptimizer

try:
    from jugeo.ideation.ideas import IdeaProposal
except ImportError:
    IdeaProposal = Any  # type: ignore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Module-level helpers
# ---------------------------------------------------------------------------


def _to_budget_item(candidate: SolutionCandidate) -> BudgetItem:
    """Convert a :class:`SolutionCandidate` to a :class:`BudgetItem`.

    The candidate's ``total_score`` field becomes the item value.  The
    item's cost is taken from ``candidate.cost`` when available; otherwise
    it defaults to 1.0.  The candidate's ``candidate_id`` is reused as the
    ``item_id``.
    """
    cost = getattr(candidate, "cost", 1.0)
    value = float(getattr(candidate, "total_score", 0.0))
    idea = getattr(candidate, "idea", None)
    label = getattr(candidate, "label", "")
    return BudgetItem(
        item_id=str(getattr(candidate, "candidate_id", uuid.uuid4())),
        value=value,
        cost=float(cost),
        idea=idea,
        label=label,
    )


def _total_cost(items: list[BudgetItem]) -> float:
    """Return the sum of costs across all *items*."""
    return sum(it.cost for it in items)


def _total_value(items: list[BudgetItem]) -> float:
    """Return the sum of values across all *items*."""
    return sum(it.value for it in items)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Divide *a* by *b*, returning *default* when *b* is effectively zero."""
    if abs(b) < 1e-12:
        return default
    return a / b


# ---------------------------------------------------------------------------
# 2. BudgetItem
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetItem:
    """An immutable item eligible for budget-constrained selection.

    Attributes
    ----------
    item_id:
        Unique identifier, typically inherited from the source candidate.
    value:
        Expected gain from selecting this item (higher is better).
    cost:
        Resource units consumed when the item is selected.
    idea:
        Optional reference to the originating
        :class:`~jugeo.ideation.ideas.IdeaProposal`.  Accessing
        ``idea.payoff`` gives the raw payoff score.
    label:
        Optional human-readable tag.
    """

    item_id: str
    value: float
    cost: float
    idea: Any
    label: str = ""

    def value_density(self) -> float:
        """Return value per unit cost (bang-for-buck ratio).

        Avoids division by zero by clamping the denominator to at least 1e-9.
        """
        return self.value / max(self.cost, 1e-9)

    def summary(self) -> str:
        """Return a compact human-readable description of this item."""
        payoff_info = ""
        if self.idea is not None and hasattr(self.idea, "payoff"):
            payoff_info = f", payoff={self.idea.payoff}"
        lbl = f" [{self.label}]" if self.label else ""
        return (
            f"BudgetItem(id={self.item_id}{lbl}, value={self.value:.3f}, "
            f"cost={self.cost:.3f}, density={self.value_density():.3f}{payoff_info})"
        )

    def is_affordable(self, budget: float) -> bool:
        """Return ``True`` if this item's cost does not exceed *budget*."""
        return self.cost <= budget


# ---------------------------------------------------------------------------
# 3. KnapsackSolver
# ---------------------------------------------------------------------------


class KnapsackSolver:
    """Exact 0-1 knapsack solver using dynamic programming.

    Capacity is discretized to an integer grid whose granularity is
    controlled by the minimum item cost.  The DP table is O(n × W) in
    time and space, so very large instances may be slow; consider
    :class:`FractionalKnapsack` for an upper-bound approximation.
    """

    def solve(self, items: list[BudgetItem], capacity: float) -> list[BudgetItem]:
        """Run the 0-1 knapsack DP and return the optimal item selection.

        Parameters
        ----------
        items:
            Candidate items available for selection.
        capacity:
            Maximum total cost of the chosen subset.

        Returns
        -------
        list[BudgetItem]
            The subset of items that maximises total value without exceeding
            *capacity*.
        """
        selected, _ = self.solve_with_values(items, capacity)
        return selected

    def solve_with_values(
        self,
        items: list[BudgetItem],
        capacity: float,
    ) -> tuple[list[BudgetItem], float]:
        """Run the 0-1 knapsack DP and return items together with total value.

        Returns
        -------
        tuple[list[BudgetItem], float]
            ``(selected_items, total_value)``
        """
        affordable = [it for it in items if it.is_affordable(capacity)]
        if not affordable:
            return [], 0.0

        cap_int = self._discretize_capacity(capacity, affordable)
        scale = cap_int / max(capacity, 1e-9)
        selected = self._dp_solve(affordable, cap_int, scale)
        total_value = _total_value(selected)
        _log.debug(
            "KnapsackSolver: selected %d/%d items, value=%.4f",
            len(selected),
            len(items),
            total_value,
        )
        return selected, total_value

    def _discretize_capacity(self, capacity: float, items: list[BudgetItem]) -> int:
        """Convert *capacity* to an integer scale based on minimum item cost.

        A precision of 100 is applied so that costs as small as 1 % of the
        minimum can be represented.  The result is capped at 10 000 to keep
        the DP table tractable.
        """
        if not items:
            return 1
        min_cost = min(it.cost for it in items)
        precision = 100
        cap_int = int(capacity * precision / max(min_cost, 1e-9))
        return max(1, min(cap_int, 10_000))

    def _dp_solve(
        self,
        items: list[BudgetItem],
        capacity_int: int,
        scale: float,
    ) -> list[BudgetItem]:
        """Core DP implementation for the 0-1 knapsack problem.

        Parameters
        ----------
        items:
            Items to choose from (all must be affordable at the original
            capacity).
        capacity_int:
            Integer-scaled capacity.
        scale:
            Factor used to convert each item's real cost to integer units:
            ``cost_int = round(item.cost * scale)``.

        Returns
        -------
        list[BudgetItem]
            Optimal selection.
        """
        n = len(items)
        W = capacity_int

        # Represent the DP table as a flat list of floats for cache efficiency.
        # dp[j] = best value achievable with integer capacity j.
        dp = [0.0] * (W + 1)
        # keep[i][j] = True iff item i was selected in the optimal solution
        # for capacity j.
        keep = [[False] * (W + 1) for _ in range(n)]

        for i, item in enumerate(items):
            cost_int = max(1, round(item.cost * scale))
            # Traverse in reverse to enforce the 0-1 constraint.
            for j in range(W, cost_int - 1, -1):
                candidate = dp[j - cost_int] + item.value
                if candidate > dp[j]:
                    dp[j] = candidate
                    keep[i][j] = True

        # Back-track to recover the selected items.
        selected: list[BudgetItem] = []
        j = W
        for i in range(n - 1, -1, -1):
            if keep[i][j]:
                selected.append(items[i])
                cost_int = max(1, round(items[i].cost * scale))
                j -= cost_int
                if j < 0:
                    break

        return selected


# ---------------------------------------------------------------------------
# 4. FractionalKnapsack
# ---------------------------------------------------------------------------


class FractionalKnapsack:
    """Greedy fractional-knapsack solver providing an LP-relaxation upper bound.

    In the fractional variant the last item that doesn't fit entirely may be
    included as a fraction, giving the LP relaxation of the 0-1 problem.  This
    upper bound is useful for branch-and-bound and sensitivity analysis.
    """

    def solve(
        self,
        items: list[BudgetItem],
        capacity: float,
    ) -> list[tuple[BudgetItem, float]]:
        """Solve the fractional knapsack by greedy value-density ordering.

        Parameters
        ----------
        items:
            Available items.
        capacity:
            Maximum total cost.

        Returns
        -------
        list[tuple[BudgetItem, float]]
            Each entry is ``(item, fraction)`` where ``fraction ∈ [0, 1]``.
            Fully selected items have fraction ``1.0``; the last (possibly
            partially selected) item has a fraction in ``(0, 1)``.
        """
        ordered = self.greedy_order(items)
        remaining = capacity
        result: list[tuple[BudgetItem, float]] = []

        for item in ordered:
            if remaining <= 0.0:
                break
            if item.cost <= remaining:
                result.append((item, 1.0))
                remaining -= item.cost
            else:
                frac = remaining / max(item.cost, 1e-9)
                if frac > 0:
                    result.append((item, frac))
                remaining = 0.0

        return result

    def upper_bound(self, items: list[BudgetItem], capacity: float) -> float:
        """Return the LP-relaxation upper bound (fractional knapsack value).

        This value is guaranteed to be ≥ the optimal 0-1 knapsack value for
        the same instance.
        """
        selections = self.solve(items, capacity)
        return sum(item.value * frac for item, frac in selections)

    def greedy_order(self, items: list[BudgetItem]) -> list[BudgetItem]:
        """Return *items* sorted by value density in descending order."""
        return sorted(items, key=lambda it: it.value_density(), reverse=True)


# ---------------------------------------------------------------------------
# 5. DynamicBudgetPolicy
# ---------------------------------------------------------------------------


class DynamicBudgetPolicy:
    """Allocates a fixed total budget across multiple rounds.

    Three allocation policies are supported:

    linear
        Each round receives an equal share ``total_budget / n_rounds``.
    geometric
        Earlier rounds receive more budget; allocation decreases by a
        constant ratio so that the sum equals ``total_budget``.
    performance
        Rounds with high recent performance receive larger allocations;
        rounds with low performance receive smaller ones.

    Parameters
    ----------
    total_budget:
        Total resource pool to be allocated.
    n_rounds:
        Number of rounds across which the budget should be spread.
    policy_type:
        One of ``"linear"``, ``"geometric"``, or ``"performance"``.
    """

    def __init__(
        self,
        total_budget: float,
        n_rounds: int,
        policy_type: str = "linear",
    ) -> None:
        self.total_budget: float = total_budget
        self.n_rounds: int = max(1, n_rounds)
        self.policy_type: str = policy_type
        self._spent: float = 0.0
        self._gained_value: float = 0.0
        self._round: int = 0
        self._performance_history: list[float] = []

    def allocate_round(self, round_idx: int, performance: float = 0.5) -> float:
        """Return the budget allocation for a given round index.

        Parameters
        ----------
        round_idx:
            Zero-based round index.
        performance:
            Recent performance score in [0, 1] (used by the ``"performance"``
            policy only).

        Returns
        -------
        float
            Budget allocated to this round.  Always ≥ 0 and ≤ remaining
            budget.
        """
        remaining = self.remaining_budget()
        if remaining <= 0.0:
            return 0.0

        if self.policy_type == "linear":
            allocation = self.total_budget / self.n_rounds

        elif self.policy_type == "geometric":
            # Geometric series: first round gets r^0, second r^1, ...
            # ratio r chosen so sum of r^0 + ... + r^(n-1) = n (balanced start)
            # Front-load with r = 0.8: each round gets (1-r^n)/(1-r)*r^t / sum
            r = 0.80
            n = self.n_rounds
            total_weight = sum(r ** i for i in range(n)) or 1.0
            weight_this_round = r ** round_idx
            allocation = self.total_budget * weight_this_round / total_weight

        else:  # performance
            # Boost allocation when performance is above average
            base = self.total_budget / self.n_rounds
            recent = self._performance_history[-3:] if self._performance_history else [0.5]
            avg_perf = sum(recent) / len(recent)
            factor = 0.5 + performance  # range [0.5, 1.5]
            adjustment = 1.0 + (performance - avg_perf)
            allocation = base * factor * max(0.5, adjustment)

        return max(0.0, min(allocation, remaining))

    def remaining_budget(self) -> float:
        """Return how much of the total budget has not yet been spent."""
        return max(0.0, self.total_budget - self._spent)

    def update(self, spent: float, gained_value: float) -> None:
        """Record the cost and value obtained during a completed round.

        Parameters
        ----------
        spent:
            Resource units consumed in the round.
        gained_value:
            Value (e.g. total payoff) obtained in the round.
        """
        self._spent += max(0.0, spent)
        self._gained_value += gained_value
        self._round += 1
        efficiency = _safe_div(gained_value, spent, default=0.0)
        self._performance_history.append(min(1.0, efficiency))
        _log.debug(
            "DynamicBudgetPolicy update: spent=%.3f, value=%.3f, round=%d",
            spent,
            gained_value,
            self._round,
        )

    def efficiency(self) -> float:
        """Return overall value-per-unit-cost ratio across all rounds."""
        return _safe_div(self._gained_value, self._spent, default=0.0)

    def reset(self) -> None:
        """Reset the policy to its initial state."""
        self._spent = 0.0
        self._gained_value = 0.0
        self._round = 0
        self._performance_history = []


# ---------------------------------------------------------------------------
# 6. BudgetSensitivityAnalysis
# ---------------------------------------------------------------------------


class BudgetSensitivityAnalysis:
    """Analyses how the optimal selection and value change with budget.

    This is a parametric analysis: by solving the knapsack at multiple budget
    levels we can observe thresholds, marginal returns, and shadow prices.
    """

    _solver: KnapsackSolver

    def __init__(self) -> None:
        self._solver = KnapsackSolver()

    def analyze(
        self,
        items: list[BudgetItem],
        budgets: list[float],
    ) -> dict[float, list[BudgetItem]]:
        """Run the 0-1 knapsack at each budget level and return results.

        Parameters
        ----------
        items:
            Items available for selection.
        budgets:
            Budget levels to evaluate.

        Returns
        -------
        dict[float, list[BudgetItem]]
            Mapping ``{budget: selected_items}``.
        """
        results: dict[float, list[BudgetItem]] = {}
        for b in sorted(budgets):
            selected = self._solver.solve(items, b)
            results[b] = selected
            _log.debug("Sensitivity: budget=%.3f → %d items selected", b, len(selected))
        return results

    def marginal_value(
        self,
        items: list[BudgetItem],
        budget: float,
        delta: float = 1.0,
    ) -> float:
        """Estimate the marginal value of an additional *delta* units of budget.

        Returns
        -------
        float
            ``(value(budget + delta) - value(budget)) / delta``
        """
        _, v_base = self._solver.solve_with_values(items, budget)
        _, v_plus = self._solver.solve_with_values(items, budget + delta)
        return _safe_div(v_plus - v_base, delta, default=0.0)

    def shadow_price(self, items: list[BudgetItem], budget: float) -> float:
        """Return the shadow price (marginal value) at *budget*.

        The shadow price estimates how much each additional unit of budget is
        worth.  It is computed as the marginal value with a small delta equal
        to 1 % of the budget (minimum 0.01).
        """
        delta = max(0.01, budget * 0.01)
        return self.marginal_value(items, budget, delta)

    def plot_data(
        self,
        items: list[BudgetItem],
        budgets: list[float],
    ) -> list[tuple[float, float]]:
        """Return ``[(budget, total_value)]`` pairs suitable for plotting.

        The budget axis is sorted in ascending order.
        """
        pairs: list[tuple[float, float]] = []
        for b in sorted(budgets):
            _, v = self._solver.solve_with_values(items, b)
            pairs.append((b, v))
        return pairs

    def breakpoints(
        self,
        items: list[BudgetItem],
        max_budget: float,
        steps: int = 20,
    ) -> list[float]:
        """Return budgets at which the optimal item selection changes.

        Parameters
        ----------
        items:
            Items to analyse.
        max_budget:
            Upper limit of the budget range to scan.
        steps:
            Number of evenly spaced budget levels to evaluate.

        Returns
        -------
        list[float]
            Budget values where the set of selected items differs from the
            previous level.  The first budget is always included.
        """
        if steps < 2:
            return [max_budget]

        step_size = max_budget / max(steps - 1, 1)
        budgets = [i * step_size for i in range(steps)]
        prev_ids: frozenset[str] | None = None
        change_points: list[float] = []

        for b in budgets:
            selected = self._solver.solve(items, b)
            current_ids = frozenset(it.item_id for it in selected)
            if current_ids != prev_ids:
                change_points.append(b)
                prev_ids = current_ids

        return change_points


# ---------------------------------------------------------------------------
# 7. BudgetOptimizer
# ---------------------------------------------------------------------------


class BudgetOptimizer:
    """Top-level optimizer that combines knapsack selection with sensitivity.

    Given an :class:`~jugeo.ideation.optimization.models.OptimizationProblem`
    and a total budget, this class converts solution candidates into
    :class:`BudgetItem` objects, runs the 0-1 knapsack solver, and assembles
    an :class:`~jugeo.ideation.optimization.models.OptimizationResult`.

    The ``idea.payoff`` attribute is respected when converting candidates: a
    candidate whose underlying idea has a higher payoff will produce a
    :class:`BudgetItem` with a proportionally boosted value, ensuring
    high-payoff ideas are preferred under equal cost conditions.

    Parameters
    ----------
    problem:
        The optimization problem supplying solution candidates and metadata.
    total_budget:
        Total resource budget available for the selection.
    """

    def __init__(self, problem: OptimizationProblem, total_budget: float) -> None:
        self.problem: OptimizationProblem = problem
        self.total_budget: float = total_budget
        self._knapsack: KnapsackSolver = KnapsackSolver()
        self._sensitivity: BudgetSensitivityAnalysis = BudgetSensitivityAnalysis()

    def optimize(self) -> OptimizationResult:
        """Run the budget-constrained optimization and return a result object.

        Candidates are converted to :class:`BudgetItem` instances; the payoff
        of the underlying idea (``candidate.idea.payoff``) is used to scale
        value when available.  The 0-1 knapsack is then solved and the
        selected candidates are assembled into an
        :class:`~jugeo.ideation.optimization.models.OptimizationResult`.

        Returns
        -------
        OptimizationResult
            Contains the selected candidates, total value, total cost, and
            convergence metadata.
        """
        candidates = list(getattr(self.problem, "candidates", []))
        items = [self._enrich_item(_to_budget_item(c), c) for c in candidates]

        selected_items, total_value = self._knapsack.solve_with_values(items, self.total_budget)
        selected_ids = frozenset(it.item_id for it in selected_items)
        selected_candidates = [c for c in candidates if str(getattr(c, "candidate_id", "")) in selected_ids]

        _log.info(
            "BudgetOptimizer: selected %d/%d candidates, value=%.4f, cost=%.4f",
            len(selected_candidates),
            len(candidates),
            total_value,
            _total_cost(selected_items),
        )

        result_id = str(uuid.uuid4())
        return OptimizationResult(
            result_id=result_id,
            problem_id=str(getattr(self.problem, "problem_id", "")),
            selected_candidates=selected_candidates,
            total_value=total_value,
            total_cost=_total_cost(selected_items),
            metadata={
                "solver": "KnapsackSolver",
                "budget": self.total_budget,
                "n_candidates": len(candidates),
                "n_selected": len(selected_candidates),
            },
        )

    def select_portfolio(
        self,
        candidates: list[SolutionCandidate],
        budget: float,
    ) -> list[SolutionCandidate]:
        """Select a portfolio of candidates within *budget* using the knapsack.

        This is a convenience wrapper around :class:`KnapsackSolver` that
        accepts raw candidates rather than requiring a full
        :class:`OptimizationProblem`.

        Parameters
        ----------
        candidates:
            Pool of candidates to choose from.
        budget:
            Maximum total cost of the selected portfolio.

        Returns
        -------
        list[SolutionCandidate]
            The chosen subset of *candidates*.
        """
        items = [self._enrich_item(_to_budget_item(c), c) for c in candidates]
        selected_items = self._knapsack.solve(items, budget)
        selected_ids = frozenset(it.item_id for it in selected_items)
        return [c for c in candidates if str(getattr(c, "candidate_id", "")) in selected_ids]

    def sensitivity_report(self, budgets: list[float] | None = None) -> str:
        """Run sensitivity analysis and return a formatted text report.

        Parameters
        ----------
        budgets:
            Budget levels to evaluate.  Defaults to ten evenly-spaced points
            from zero to ``total_budget``.

        Returns
        -------
        str
            Multi-line human-readable sensitivity report.
        """
        candidates = list(getattr(self.problem, "candidates", []))
        items = [self._enrich_item(_to_budget_item(c), c) for c in candidates]

        if budgets is None:
            n = 10
            step = self.total_budget / max(n - 1, 1)
            budgets = [i * step for i in range(n)]

        plot = self._sensitivity.plot_data(items, budgets)
        bps = self._sensitivity.breakpoints(items, self.total_budget)

        lines = [
            "=== BudgetOptimizer Sensitivity Report ===",
            f"Total budget: {self.total_budget:.3f}",
            f"Candidates: {len(candidates)}",
            "",
            "Budget → Total Value:",
        ]
        for b, v in plot:
            shadow = self._sensitivity.shadow_price(items, b)
            lines.append(f"  {b:8.3f}  →  value={v:.4f}  shadow_price={shadow:.4f}")

        lines += ["", f"Selection-change breakpoints ({len(bps)}):"]
        for bp in bps:
            lines.append(f"  budget={bp:.3f}")

        return "\n".join(lines)

    @staticmethod
    def _enrich_item(item: BudgetItem, candidate: SolutionCandidate) -> BudgetItem:
        """Return a copy of *item* whose value is boosted by idea.payoff.

        If the underlying idea exposes a ``payoff`` attribute the item's value
        is increased by a small proportional bonus:
        ``value *= 1 + payoff_bonus`` where ``payoff_bonus ∈ [0, 0.5]``.
        This ensures high-payoff ideas are preferred among similarly valued
        candidates without overwhelming the primary objective scores.
        """
        idea = getattr(candidate, "idea", None)
        if idea is None or not hasattr(idea, "payoff"):
            return item
        payoff_bonus = min(0.5, max(0.0, idea.payoff / 200.0))
        enriched_value = item.value * (1.0 + payoff_bonus)
        return BudgetItem(
            item_id=item.item_id,
            value=enriched_value,
            cost=item.cost,
            idea=item.idea,
            label=item.label or f"payoff={idea.payoff}",
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "BudgetItem",
    "KnapsackSolver",
    "FractionalKnapsack",
    "DynamicBudgetPolicy",
    "BudgetSensitivityAnalysis",
    "BudgetOptimizer",
]
