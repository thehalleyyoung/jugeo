r"""Theorem-growth economics and semantic research scheduling for JuGeo.

This module implements the scheduling sub-system described in
``preliminaries/theory2.tex``, §6 "Theorem-Growth Economics and Semantic
Research Scheduling".  Scheduling ideation work balances *exploration*
(finding new mathematical kinds) versus *exploitation* (deepening known
areas), constrained by budget, evidence availability, and purpose
alignment.

Mathematical background
-----------------------

Let :math:`K` be the current set of known mathematical kinds and
:math:`\mathcal{R}` the set of research regimes.  The epoch-level
schedule :math:`\sigma_t` assigns budget fractions:

.. math::

    \sigma_t : \mathcal{R} \to [0, 1],
    \quad \sum_{r \in \mathcal{R}} \sigma_t(r) = 1

Theorem yield under regime *r* with budget :math:`b` follows a
concave growth law:

.. math::

    Y(r, b) = Y_{\infty}(r) \left(1 - e^{-\lambda_r b}\right)

where :math:`Y_{\infty}(r)` is the saturation yield and
:math:`\lambda_r` is the regime-specific growth rate.  The optimal
allocation solves:

.. math::

    \max_{\sigma} \sum_{r} Y\!\left(r,\; \sigma(r) \cdot B\right)
    \quad \text{s.t.}
    \quad \sum_r \sigma(r) = 1,\; \sigma(r) \ge 0

The copilot layer provides warm-start estimates for :math:`Y_{\infty}`
and :math:`\lambda_r` when empirical data are sparse.

Design overview
---------------

1. :class:`IdeationSchedule` — immutable schedule dataclass.
2. :class:`IdeationScheduler` — top-level scheduling orchestrator.
3. :class:`TheoremGrowthEconomics` — models theorem yield and saturation.
4. :class:`ExplorationBudget` — per-regime budget tracking and reallocation.
5. :class:`ExploitationPrioritizer` — ranks opportunities for deepening.
6. :class:`SchedulingPolicy` — configurable policy parameters.
7. :class:`IdeationClock` — epoch tracking and pace estimation.
8. :class:`ScheduleHistory` — records historical schedules and yields.
9. :class:`ScheduleOptimizer` — multi-objective Pareto optimization.
10. :class:`ScheduleDiagnostics` — human-readable reporting.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

from jugeo.ideation.ideas import IdeaProposal
from jugeo.orchestration.budgets import BudgetLedger

try:
    from jugeo.orchestration.controller import (
        Orchestrator,
        OrchestrationController,
        OrchestratorConfiguration,
        SemanticMove,
    )
except ImportError:  # pragma: no cover
    Orchestrator = None  # type: ignore[assignment,misc]
    OrchestrationController = None  # type: ignore[assignment,misc]
    OrchestratorConfiguration = None  # type: ignore[assignment,misc]
    SemanticMove = None  # type: ignore[assignment,misc]

try:
    from jugeo.runtime.cache import SemanticCache, CacheKey
except ImportError:  # pragma: no cover
    SemanticCache = None  # type: ignore[assignment,misc]
    CacheKey = None  # type: ignore[assignment,misc]

try:
    from jugeo.runtime.replay import ReplayEngine, ReplayPolicy, ReplayDecision
except ImportError:  # pragma: no cover
    ReplayEngine = None  # type: ignore[assignment,misc]
    ReplayPolicy = None  # type: ignore[assignment,misc]
    ReplayDecision = None  # type: ignore[assignment,misc]

try:
    import jugeo.evaluation as _evaluation_pkg
except ImportError:  # pragma: no cover
    _evaluation_pkg = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supporting enumerations
# ---------------------------------------------------------------------------


class ExhaustionPolicy(str, Enum):
    """What to do when a per-regime budget runs out mid-epoch.

    Attributes:
        HALT: Stop work in the regime immediately.
        BORROW: Borrow from the global reserve pool.
        REBALANCE: Redistribute the remaining global budget across regimes.
        DEFER: Move pending ideas to the next epoch without spending.
    """

    HALT = "halt"
    BORROW = "borrow"
    REBALANCE = "rebalance"
    DEFER = "defer"


class SchedulePhase(str, Enum):
    """High-level phase of the research schedule.

    Attributes:
        BOOTSTRAP: Early phase with high uncertainty; copilot priors dominate.
        GROWTH: Active theorem production; empirical data begins to accumulate.
        SATURATION: Diminishing returns detected; shift budget toward exploration.
        HARVEST: Consolidate known kinds; minimal exploration.
    """

    BOOTSTRAP = "bootstrap"
    GROWTH = "growth"
    SATURATION = "saturation"
    HARVEST = "harvest"


# ---------------------------------------------------------------------------
# 1. IdeationSchedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdeationSchedule:
    """Immutable snapshot of a single-epoch ideation schedule.

    An :class:`IdeationSchedule` captures every decision made for one
    epoch: which idea titles are slated for exploration or exploitation,
    the total budget committed, and the expected theorem yield predicted
    by :class:`TheoremGrowthEconomics`.

    Attributes:
        schedule_id: Unique identifier (UUID-4 string).
        epoch: Zero-based epoch index within the current research session.
        planned_explorations: Ordered tuple of idea titles scheduled for
            exploratory work (new mathematical kinds).
        planned_exploitations: Ordered tuple of idea titles scheduled for
            deepening work (known areas).
        budget: Total budget units committed to this epoch.
        expected_yield: Predicted theorem count from
            :class:`TheoremGrowthEconomics`.
        regime_allocations: Mapping from regime name to budget fraction
            ``[0, 1]``.
        phase: Current :class:`SchedulePhase` at schedule creation time.
        created_at: POSIX timestamp when this schedule was created.
        copilot_assisted: Whether a copilot prior was used to seed yield
            estimates (useful when empirical data are sparse).
        metadata: Arbitrary key-value pairs for downstream consumers.
    """

    schedule_id: str
    epoch: int
    planned_explorations: tuple[str, ...]
    planned_exploitations: tuple[str, ...]
    budget: float
    expected_yield: float
    regime_allocations: dict[str, float] = field(default_factory=dict)
    phase: SchedulePhase = SchedulePhase.GROWTH
    created_at: float = field(default_factory=time.time)
    copilot_assisted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def exploration_count(self) -> int:
        """Number of ideas scheduled for exploration."""
        return len(self.planned_explorations)

    @property
    def exploitation_count(self) -> int:
        """Number of ideas scheduled for exploitation."""
        return len(self.planned_exploitations)

    @property
    def total_ideas(self) -> int:
        """Total ideas scheduled across both modes."""
        return self.exploration_count + self.exploitation_count

    @property
    def exploration_ratio(self) -> float:
        """Fraction of scheduled ideas that are exploratory.

        Returns 0.0 when no ideas are scheduled to avoid division by zero.
        """
        if self.total_ideas == 0:
            return 0.0
        return self.exploration_count / self.total_ideas

    @property
    def yield_per_budget_unit(self) -> float:
        """Expected theorems per unit of budget committed.

        Returns 0.0 when budget is zero.
        """
        if self.budget <= 0.0:
            return 0.0
        return self.expected_yield / self.budget

    @property
    def accepted(self) -> tuple[str, ...]:
        return self.planned_explorations + self.planned_exploitations

    @property
    def deferred(self) -> tuple[str, ...]:
        return tuple(self.metadata.get("deferred", ()))

    def summary_line(self) -> str:
        """Return a one-line human-readable summary.

        Example::

            epoch=3 phase=growth explore=4 exploit=6 budget=100.0 yield≈8.2
        """
        return (
            f"epoch={self.epoch} phase={self.phase.value} "
            f"explore={self.exploration_count} exploit={self.exploitation_count} "
            f"budget={self.budget:.1f} yield≈{self.expected_yield:.2f}"
        )


# ---------------------------------------------------------------------------
# 6. SchedulingPolicy  (defined early — used by IdeationScheduler)
# ---------------------------------------------------------------------------


@dataclass
class SchedulingPolicy:
    """Configurable policy parameters governing how schedules are built.

    A :class:`SchedulingPolicy` is the single source of truth for tunable
    hyper-parameters.  It is consumed by :class:`IdeationScheduler`,
    :class:`ExplorationBudget`, and :class:`ScheduleOptimizer`.

    Attributes:
        exploration_ratio: Target fraction of budget allocated to
            exploration.  Must be in ``[0, 1]``.
        budget_per_regime: Mapping from regime name to absolute budget
            units.  Overrides proportional allocation when present.
        escalation_rules: Ordered list of ``(condition_fn, action_fn)``
            pairs.  ``condition_fn(schedule) -> bool`` fires
            ``action_fn(schedule, policy)`` when true.
        copilot_consultation_threshold: Minimum evidence count below
            which the copilot prior is consulted for yield estimation.
        min_exploitation_ideas: Guarantee at least this many exploitation
            ideas per epoch to prevent pure-exploration drift.
        max_exploitation_ideas: Cap exploitation ideas per epoch.
        saturation_yield_threshold: Fraction of ``Y_infinity`` at which
            a regime is considered saturated.
        rebalance_on_exhaustion: If ``True``, trigger budget rebalancing
            whenever any regime is exhausted mid-epoch.
        deadline_lookahead_epochs: How many epochs ahead the
            :class:`IdeationClock` should project for deadline warnings.
    """

    exploration_ratio: float = 0.4
    budget_per_regime: dict[str, float] = field(default_factory=dict)
    escalation_rules: list[tuple[Callable, Callable]] = field(default_factory=list)
    copilot_consultation_threshold: int = 5
    min_exploitation_ideas: int = 1
    max_exploitation_ideas: int = 20
    saturation_yield_threshold: float = 0.90
    rebalance_on_exhaustion: bool = True
    deadline_lookahead_epochs: int = 3

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty if valid).

        Checks that ``exploration_ratio`` is in ``[0, 1]``, that per-regime
        budgets are non-negative, and that min/max exploitation counts are
        consistent.
        """
        errors: list[str] = []
        if not 0.0 <= self.exploration_ratio <= 1.0:
            errors.append(
                f"exploration_ratio={self.exploration_ratio} not in [0, 1]"
            )
        for regime, budget in self.budget_per_regime.items():
            if budget < 0:
                errors.append(
                    f"budget_per_regime[{regime!r}]={budget} is negative"
                )
        if self.min_exploitation_ideas > self.max_exploitation_ideas:
            errors.append(
                f"min_exploitation_ideas={self.min_exploitation_ideas} "
                f"> max_exploitation_ideas={self.max_exploitation_ideas}"
            )
        if self.copilot_consultation_threshold < 0:
            errors.append(
                f"copilot_consultation_threshold="
                f"{self.copilot_consultation_threshold} is negative"
            )
        return errors

    def apply_escalation(
        self, schedule: IdeationSchedule
    ) -> list[str]:
        """Fire any escalation rules whose conditions are met.

        Each ``(condition_fn, action_fn)`` pair is evaluated in order.
        ``action_fn`` receives the schedule and this policy and may mutate
        ``self``.  Returns a list of rule indices (as strings) that fired.

        Args:
            schedule: The freshly-built schedule to evaluate.

        Returns:
            List of string labels for rules that fired, for audit purposes.
        """
        fired: list[str] = []
        for i, (cond, action) in enumerate(self.escalation_rules):
            try:
                if cond(schedule):
                    action(schedule, self)
                    fired.append(str(i))
            except Exception as exc:  # noqa: BLE001
                _log.warning("Escalation rule %d raised: %s", i, exc)
        return fired

    def clone_with(self, **overrides: Any) -> SchedulingPolicy:
        """Return a shallow copy with selected fields overridden.

        Useful for per-epoch policy variations without mutating the
        canonical policy object.

        Args:
            **overrides: Field names and new values.

        Returns:
            New :class:`SchedulingPolicy` instance.
        """
        import dataclasses

        return dataclasses.replace(self, **overrides)


# ---------------------------------------------------------------------------
# 3. TheoremGrowthEconomics
# ---------------------------------------------------------------------------


class TheoremGrowthEconomics:
    r"""Models theorem yield as a function of budget using concave growth laws.

    The core model is the saturating exponential:

    .. math::

        Y(r, b) = Y_{\infty}(r)\bigl(1 - e^{-\lambda_r b}\bigr)

    Parameters :math:`Y_{\infty}` and :math:`\lambda_r` are estimated from
    empirical history or seeded by the copilot prior when data are scarce.

    Attributes:
        regime_saturation_yields: Mapping from regime name to
            :math:`Y_{\infty}(r)`.
        regime_growth_rates: Mapping from regime name to
            :math:`\lambda_r`.
        empirical_counts: How many empirical observations back each
            regime's parameter estimates.
        copilot_prior_weight: Weight given to copilot priors when blending
            with empirical estimates (0 = pure empirical, 1 = pure prior).
    """

    def __init__(
        self,
        regime_saturation_yields: dict[str, float] | None = None,
        regime_growth_rates: dict[str, float] | None = None,
        copilot_prior_weight: float = 0.3,
    ) -> None:
        self.regime_saturation_yields: dict[str, float] = (
            regime_saturation_yields or {}
        )
        self.regime_growth_rates: dict[str, float] = regime_growth_rates or {}
        self.empirical_counts: dict[str, int] = defaultdict(int)
        self.copilot_prior_weight = copilot_prior_weight

    # ------------------------------------------------------------------
    # Parameter management
    # ------------------------------------------------------------------

    def _y_inf(self, regime: str) -> float:
        """Return saturation yield for *regime*, defaulting to a copilot prior."""
        return self.regime_saturation_yields.get(regime, 10.0)

    def _lambda(self, regime: str) -> float:
        """Return growth-rate constant for *regime*, defaulting to copilot prior."""
        return self.regime_growth_rates.get(regime, 0.05)

    # ------------------------------------------------------------------
    # Core model
    # ------------------------------------------------------------------

    def model_growth(self, regime: str, budget: float) -> float:
        r"""Predict theorem yield for *regime* given *budget* units.

        Uses the saturating exponential:
        :math:`Y = Y_{\infty}(1 - e^{-\lambda b})`.

        Args:
            regime: Research regime identifier.
            budget: Budget units to invest.

        Returns:
            Expected theorem yield as a non-negative float.
        """
        if budget < 0:
            raise ValueError(f"budget must be non-negative, got {budget}")
        y_inf = self._y_inf(regime)
        lam = self._lambda(regime)
        return y_inf * (1.0 - math.exp(-lam * budget))

    def predict_yield(
        self, regime_budgets: dict[str, float]
    ) -> dict[str, float]:
        """Predict yields for a mapping of *regime → budget*.

        Args:
            regime_budgets: Dict mapping regime names to budget allocations.

        Returns:
            Dict mapping regime names to predicted theorem yields.
        """
        return {r: self.model_growth(r, b) for r, b in regime_budgets.items()}

    def marginal_value(self, regime: str, budget: float, delta: float = 1.0) -> float:
        r"""Return the marginal yield of investing one more *delta* unit.

        Computes :math:`Y(r, b + \delta) - Y(r, b)`.  This is the
        derivative approximation used by :class:`ScheduleOptimizer` to
        reallocate budget at the margin.

        Args:
            regime: Research regime identifier.
            budget: Current budget already invested.
            delta: Incremental budget unit (default 1.0).

        Returns:
            Marginal theorem yield for the next delta units.
        """
        return self.model_growth(regime, budget + delta) - self.model_growth(
            regime, budget
        )

    def saturation_detection(
        self, regime: str, budget: float, threshold: float = 0.90
    ) -> bool:
        r"""Return ``True`` if *regime* is at or beyond the saturation threshold.

        A regime is saturated when its predicted yield exceeds *threshold*
        times the saturation yield :math:`Y_{\infty}`.

        Args:
            regime: Research regime identifier.
            budget: Budget currently invested.
            threshold: Fraction of :math:`Y_{\infty}` considered saturated.

        Returns:
            ``True`` when the regime is saturated at the given budget.
        """
        y_inf = self._y_inf(regime)
        if y_inf <= 0:
            return True
        return self.model_growth(regime, budget) / y_inf >= threshold

    def optimal_investment(
        self,
        regimes: Sequence[str],
        total_budget: float,
        n_steps: int = 100,
    ) -> dict[str, float]:
        """Allocate *total_budget* across *regimes* to maximise total yield.

        Uses a greedy marginal-value algorithm: at each of *n_steps*
        steps, assign one budget unit to the regime with the highest
        marginal value.

        Args:
            regimes: Sequence of regime identifiers to consider.
            total_budget: Total budget to distribute.
            n_steps: Number of greedy steps (resolution of the allocation).

        Returns:
            Dict mapping each regime to its optimal budget allocation.
        """
        allocation: dict[str, float] = {r: 0.0 for r in regimes}
        if not regimes or total_budget <= 0:
            return allocation
        step_size = total_budget / n_steps
        for _ in range(n_steps):
            best_regime = max(
                regimes,
                key=lambda r: self.marginal_value(r, allocation[r], step_size),
            )
            allocation[best_regime] += step_size
        return allocation

    def update_from_observation(
        self, regime: str, budget_spent: float, theorems_produced: int
    ) -> None:
        r"""Update model parameters from an empirical observation.

        Performs a single-step exponential-moving-average update of
        :math:`\lambda_r` using the observed yield.  Uses the copilot
        prior weight to blend old and new estimates.

        Args:
            regime: Regime where the observation was made.
            budget_spent: Budget that was consumed.
            theorems_produced: Number of theorems actually produced.
        """
        if budget_spent <= 0:
            return
        y_inf = self._y_inf(regime)
        # Invert the growth model to estimate implied lambda.
        ratio = min(theorems_produced / max(y_inf, 1e-9), 1.0 - 1e-9)
        if ratio <= 0:
            return
        implied_lambda = -math.log(1.0 - ratio) / budget_spent
        old_lambda = self._lambda(regime)
        alpha = 1.0 - self.copilot_prior_weight
        new_lambda = alpha * implied_lambda + (1.0 - alpha) * old_lambda
        self.regime_growth_rates[regime] = max(new_lambda, 1e-6)
        self.empirical_counts[regime] += 1

    def total_predicted_yield(self, regime_budgets: dict[str, float]) -> float:
        """Sum predicted yields across all regimes.

        Args:
            regime_budgets: Mapping from regime name to allocated budget.

        Returns:
            Total expected theorem count.
        """
        return sum(self.predict_yield(regime_budgets).values())


# ---------------------------------------------------------------------------
# 4. ExplorationBudget
# ---------------------------------------------------------------------------


class ExplorationBudget:
    """Tracks and manages per-regime exploration budgets within an epoch.

    Each research regime receives an initial budget allocation.
    :class:`ExplorationBudget` enforces that spending stays within bounds,
    supports reallocation between regimes, and applies an
    :class:`ExhaustionPolicy` when a regime runs out of budget.

    Attributes:
        initial_allocations: Snapshot of the starting per-regime budgets.
        current_allocations: Mutable per-regime remaining budgets.
        spent: Per-regime cumulative spend.
        exhaustion_policy: Behaviour when a regime budget hits zero.
        reserve: Global reserve pool available for borrowing.
    """

    def __init__(
        self,
        allocations: dict[str, float],
        exhaustion_policy: ExhaustionPolicy = ExhaustionPolicy.REBALANCE,
        reserve: float = 0.0,
    ) -> None:
        self.initial_allocations: dict[str, float] = dict(allocations)
        self.current_allocations: dict[str, float] = dict(allocations)
        self.spent: dict[str, float] = defaultdict(float)
        self.exhaustion_policy = exhaustion_policy
        self.reserve = reserve

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def remaining(self, regime: str) -> float:
        """Return remaining budget for *regime*.

        Args:
            regime: Regime identifier.

        Returns:
            Non-negative remaining budget units.
        """
        return max(self.current_allocations.get(regime, 0.0), 0.0)

    def total_remaining(self) -> float:
        """Return total remaining budget summed across all regimes plus reserve."""
        return sum(self.current_allocations.values()) + self.reserve

    def is_exhausted(self, regime: str) -> bool:
        """Return ``True`` if *regime* has no remaining budget.

        Args:
            regime: Regime identifier.
        """
        return self.remaining(regime) <= 0.0

    def utilisation(self, regime: str) -> float:
        """Return fraction of initial allocation spent for *regime*.

        Returns 0.0 if the regime had zero initial allocation.

        Args:
            regime: Regime identifier.
        """
        initial = self.initial_allocations.get(regime, 0.0)
        if initial <= 0.0:
            return 0.0
        return self.spent[regime] / initial

    # ------------------------------------------------------------------
    # Mutating
    # ------------------------------------------------------------------

    def consume(self, regime: str, amount: float) -> float:
        """Spend *amount* units from *regime*'s budget.

        If insufficient budget remains, the exhaustion policy is applied:

        * :attr:`ExhaustionPolicy.HALT` — raises :class:`ValueError`.
        * :attr:`ExhaustionPolicy.BORROW` — draws from the reserve pool.
        * :attr:`ExhaustionPolicy.REBALANCE` — redistributes surplus from
          other regimes.
        * :attr:`ExhaustionPolicy.DEFER` — returns 0 without spending.

        Args:
            regime: Regime to debit.
            amount: Budget units to consume.

        Returns:
            Amount actually consumed (may be less than *amount* under DEFER).

        Raises:
            ValueError: Under HALT policy when budget is insufficient.
        """
        available = self.remaining(regime)
        if amount <= available:
            self.current_allocations[regime] = available - amount
            self.spent[regime] += amount
            return amount

        shortfall = amount - available
        if self.exhaustion_policy == ExhaustionPolicy.HALT:
            raise ValueError(
                f"Regime {regime!r} budget exhausted "
                f"(need {amount}, have {available})"
            )
        elif self.exhaustion_policy == ExhaustionPolicy.BORROW:
            borrowed = min(shortfall, self.reserve)
            self.reserve -= borrowed
            self.current_allocations[regime] = 0.0
            actual = available + borrowed
            self.spent[regime] += actual
            return actual
        elif self.exhaustion_policy == ExhaustionPolicy.REBALANCE:
            self.current_allocations[regime] = 0.0
            self.spent[regime] += available
            self._rebalance_surplus(shortfall)
            return available
        else:  # DEFER
            return 0.0

    def reallocate(self, from_regime: str, to_regime: str, amount: float) -> float:
        """Move *amount* budget units from one regime to another.

        Only moves up to the available amount in *from_regime*.

        Args:
            from_regime: Source regime.
            to_regime: Destination regime.
            amount: Budget units to transfer.

        Returns:
            Amount actually transferred.
        """
        transferable = min(amount, self.remaining(from_regime))
        self.current_allocations[from_regime] = (
            self.current_allocations.get(from_regime, 0.0) - transferable
        )
        self.current_allocations[to_regime] = (
            self.current_allocations.get(to_regime, 0.0) + transferable
        )
        return transferable

    def _rebalance_surplus(self, needed: float) -> None:
        """Redistribute *needed* units from surplus regimes proportionally."""
        surplus_regimes = {
            r: v for r, v in self.current_allocations.items() if v > 0
        }
        total_surplus = sum(surplus_regimes.values())
        if total_surplus <= 0:
            return
        fraction = min(needed / total_surplus, 1.0)
        for r, v in surplus_regimes.items():
            self.current_allocations[r] = v * (1.0 - fraction)

    def add_regime(self, regime: str, budget: float) -> None:
        """Add a new regime with an initial budget allocation.

        Args:
            regime: New regime identifier.
            budget: Initial budget units.
        """
        self.initial_allocations[regime] = budget
        self.current_allocations[regime] = budget
        self.spent.setdefault(regime, 0.0)

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Return a dict snapshot of current state for logging.

        Returns:
            Dict with keys ``"remaining"``, ``"spent"``,
            ``"utilisation"`` mapping regime → value.
        """
        regimes = set(self.initial_allocations)
        return {
            "remaining": {r: self.remaining(r) for r in regimes},
            "spent": {r: self.spent[r] for r in regimes},
            "utilisation": {r: self.utilisation(r) for r in regimes},
        }


# ---------------------------------------------------------------------------
# 5. ExploitationPrioritizer
# ---------------------------------------------------------------------------


class ExploitationPrioritizer:
    """Ranks known areas by the value of deepening work.

    Exploitation means investing further in mathematical kinds that are
    already partially understood, compounding earlier theorem yields.
    This class scores opportunities, estimates compounding returns, and
    provides confidence intervals for ranking decisions.

    Attributes:
        depth_scores: Mapping from area name to current depth score
            ``[0, 1]``, where 1.0 means fully exhausted.
        prior_yields: Mapping from area name to historical theorem yield.
        evidence_counts: Mapping from area name to number of empirical
            observations (used for confidence intervals).
    """

    def __init__(
        self,
        depth_scores: dict[str, float] | None = None,
        prior_yields: dict[str, float] | None = None,
        evidence_counts: dict[str, int] | None = None,
    ) -> None:
        self.depth_scores: dict[str, float] = depth_scores or {}
        self.prior_yields: dict[str, float] = prior_yields or {}
        self.evidence_counts: dict[str, int] = evidence_counts or {}

    def score_depth_opportunity(self, area: str, budget: float) -> float:
        """Score the opportunity of deepening *area* with *budget* units.

        The score combines:

        * Prior yield — how productive the area has been historically.
        * Remaining depth — ``1 - depth_score`` (fully explored areas get 0).
        * Budget sensitivity — square-root dampening for large budgets.

        Args:
            area: Identifier for the mathematical area.
            budget: Budget units available for deepening.

        Returns:
            Non-negative opportunity score.
        """
        if budget <= 0:
            return 0.0
        prior = self.prior_yields.get(area, 1.0)
        remaining_depth = max(1.0 - self.depth_scores.get(area, 0.0), 0.0)
        budget_factor = math.sqrt(budget)
        return prior * remaining_depth * budget_factor

    def rank(
        self, areas: Sequence[str], budget_per_area: float
    ) -> list[tuple[str, float]]:
        """Return areas sorted by deepening opportunity score, descending.

        Args:
            areas: Candidate area identifiers.
            budget_per_area: Budget available per area (same for all).

        Returns:
            List of ``(area, score)`` tuples, highest score first.
        """
        scored = [
            (area, self.score_depth_opportunity(area, budget_per_area))
            for area in areas
        ]
        return sorted(scored, key=lambda t: t[1], reverse=True)

    def expected_compounding(self, area: str, n_epochs: int) -> float:
        """Estimate compound yield over *n_epochs* of sustained deepening.

        Models compounding as geometric decay: each epoch the marginal
        gain is ``prior_yield * (1 - depth_score)^epoch_index``.

        Args:
            area: Area identifier.
            n_epochs: Number of consecutive deepening epochs.

        Returns:
            Total expected compounded yield.
        """
        prior = self.prior_yields.get(area, 1.0)
        depth = self.depth_scores.get(area, 0.0)
        survival = max(1.0 - depth, 0.0)
        total = 0.0
        for i in range(n_epochs):
            total += prior * (survival ** i)
        return total

    def confidence_interval(
        self, area: str, confidence: float = 0.95
    ) -> tuple[float, float]:
        """Return a confidence interval for the deepening score of *area*.

        Uses a simple Normal approximation based on the number of
        empirical observations.  When observations are fewer than 2, the
        interval defaults to ``[0, 2 * prior_yield]``.

        Args:
            area: Area identifier.
            confidence: Desired confidence level (e.g., 0.95).

        Returns:
            ``(lower, upper)`` bound tuple.
        """
        prior = self.prior_yields.get(area, 1.0)
        n = self.evidence_counts.get(area, 0)
        if n < 2:
            return (0.0, 2.0 * prior)
        std_err = prior / math.sqrt(n)
        z = _z_score(confidence)
        return (max(0.0, prior - z * std_err), prior + z * std_err)

    def update_depth(self, area: str, depth_increment: float) -> None:
        """Record additional depth explored in *area*.

        Args:
            area: Area identifier.
            depth_increment: Amount by which depth has increased (clipped
                to keep score in ``[0, 1]``).
        """
        current = self.depth_scores.get(area, 0.0)
        self.depth_scores[area] = min(current + depth_increment, 1.0)

    def record_yield(self, area: str, theorems_produced: int) -> None:
        """Update the prior yield estimate for *area* from an observation.

        Uses exponential moving average with weight 0.5.

        Args:
            area: Area identifier.
            theorems_produced: Actual theorem count observed.
        """
        old = self.prior_yields.get(area, float(theorems_produced))
        self.prior_yields[area] = 0.5 * old + 0.5 * float(theorems_produced)
        self.evidence_counts[area] = self.evidence_counts.get(area, 0) + 1


def _z_score(confidence: float) -> float:
    """Return approximate Normal z-score for a two-tailed *confidence* level."""
    # Simple lookup table for common levels.
    table = {0.90: 1.645, 0.95: 1.960, 0.99: 2.576}
    return table.get(round(confidence, 2), 1.960)


# ---------------------------------------------------------------------------
# 7. IdeationClock
# ---------------------------------------------------------------------------


class IdeationClock:
    """Tracks epoch progression, wall-clock time, and deadline proximity.

    The clock is the temporal spine of the scheduler.  It drives
    deadline-aware policy adjustments and provides pace estimates that
    the copilot layer can surface to a researcher.

    Attributes:
        current_epoch: Current zero-based epoch index.
        epoch_start_times: POSIX timestamps when each epoch began.
        target_epochs: Total number of epochs in the research plan.
        wall_deadline: Optional POSIX timestamp for a hard deadline.
        epoch_duration_estimate: Running estimate of seconds per epoch.
    """

    def __init__(
        self,
        target_epochs: int = 10,
        wall_deadline: float | None = None,
    ) -> None:
        self.current_epoch: int = 0
        self.epoch_start_times: list[float] = [time.time()]
        self.target_epochs = target_epochs
        self.wall_deadline = wall_deadline
        self.epoch_duration_estimate: float = 60.0  # seconds

    # ------------------------------------------------------------------
    # Advancing
    # ------------------------------------------------------------------

    def advance(self) -> int:
        """Advance to the next epoch and record the start time.

        Returns:
            New current epoch index.
        """
        now = time.time()
        if len(self.epoch_start_times) >= 2:
            last_duration = now - self.epoch_start_times[-1]
            alpha = 0.3
            self.epoch_duration_estimate = (
                alpha * last_duration + (1 - alpha) * self.epoch_duration_estimate
            )
        self.current_epoch += 1
        self.epoch_start_times.append(now)
        return self.current_epoch

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def epochs_remaining(self) -> int:
        """Return the number of epochs remaining before the plan is complete."""
        return max(0, self.target_epochs - self.current_epoch)

    def time_to_deadline(self) -> float | None:
        """Return seconds until the wall-clock deadline, or ``None``.

        Returns a negative value if the deadline has already passed.
        """
        if self.wall_deadline is None:
            return None
        return self.wall_deadline - time.time()

    def pace_estimate(self) -> float:
        """Return estimated seconds per epoch based on recent history.

        Returns the running EMA estimate, or 60.0 seconds as a default
        when insufficient data exist.
        """
        return self.epoch_duration_estimate

    def is_past_deadline(self) -> bool:
        """Return ``True`` if the wall-clock deadline has passed."""
        ttd = self.time_to_deadline()
        return ttd is not None and ttd < 0.0

    def projected_completion_time(self) -> float:
        """Return estimated POSIX timestamp of plan completion.

        Uses the current pace estimate and remaining epoch count.
        """
        return time.time() + self.epochs_remaining() * self.pace_estimate()

    def deadline_pressure(self) -> float:
        """Return a ``[0, 1]`` deadline-pressure score.

        0.0 = no pressure (deadline is far away or nonexistent).
        1.0 = maximum pressure (deadline has passed or imminent).
        """
        ttd = self.time_to_deadline()
        if ttd is None:
            return 0.0
        projected = self.epochs_remaining() * self.pace_estimate()
        if projected <= 0:
            return 1.0
        ratio = 1.0 - (ttd / projected)
        return max(0.0, min(1.0, ratio))

    def epoch_summary(self) -> dict[str, Any]:
        """Return a dict summary of clock state for logging.

        Returns:
            Dict with keys ``current_epoch``, ``epochs_remaining``,
            ``pace_estimate_s``, ``deadline_pressure``, ``past_deadline``.
        """
        return {
            "current_epoch": self.current_epoch,
            "epochs_remaining": self.epochs_remaining(),
            "pace_estimate_s": round(self.pace_estimate(), 2),
            "deadline_pressure": round(self.deadline_pressure(), 3),
            "past_deadline": self.is_past_deadline(),
        }


# ---------------------------------------------------------------------------
# 8. ScheduleHistory
# ---------------------------------------------------------------------------


class ScheduleHistory:
    """Persistent record of all schedules produced in a session.

    :class:`ScheduleHistory` accumulates :class:`IdeationSchedule`
    objects as they are produced and exposes time-series views over yield,
    budget consumption, and regime time allocation.

    Attributes:
        records: Ordered list of recorded schedules.
    """

    def __init__(self) -> None:
        self.records: list[IdeationSchedule] = []

    def record(self, schedule: IdeationSchedule) -> None:
        """Append *schedule* to the history.

        Args:
            schedule: Completed schedule to persist.
        """
        self.records.append(schedule)
        _log.debug(
            "ScheduleHistory: recorded epoch=%d yield=%.2f",
            schedule.epoch,
            schedule.expected_yield,
        )

    def yield_over_time(self) -> list[tuple[int, float]]:
        """Return ``(epoch, expected_yield)`` pairs ordered by epoch.

        Returns:
            List of tuples, one per recorded schedule.
        """
        return [(s.epoch, s.expected_yield) for s in self.records]

    def budget_use_over_time(self) -> list[tuple[int, float]]:
        """Return ``(epoch, budget)`` pairs showing budget consumption per epoch.

        Returns:
            List of tuples, one per recorded schedule.
        """
        return [(s.epoch, s.budget) for s in self.records]

    def regime_time_allocation(self) -> dict[str, list[tuple[int, float]]]:
        """Return per-regime budget fraction series ``{regime: [(epoch, frac)]}``.

        Returns:
            Dict mapping regime name to list of ``(epoch, fraction)`` tuples.
        """
        result: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for s in self.records:
            for regime, frac in s.regime_allocations.items():
                result[regime].append((s.epoch, frac))
        return dict(result)

    def cumulative_yield(self) -> float:
        """Return the sum of expected yields across all recorded epochs."""
        return sum(s.expected_yield for s in self.records)

    def average_exploration_ratio(self) -> float:
        """Return the mean exploration ratio across all recorded epochs.

        Returns 0.0 when no schedules have been recorded.
        """
        if not self.records:
            return 0.0
        return statistics.mean(s.exploration_ratio for s in self.records)

    def most_productive_epoch(self) -> IdeationSchedule | None:
        """Return the schedule with the highest expected yield, or ``None``."""
        if not self.records:
            return None
        return max(self.records, key=lambda s: s.expected_yield)

    def phase_distribution(self) -> dict[str, int]:
        """Return a count of schedules per :class:`SchedulePhase`.

        Returns:
            Dict mapping phase name to epoch count.
        """
        counts: dict[str, int] = defaultdict(int)
        for s in self.records:
            counts[s.phase.value] += 1
        return dict(counts)


# ---------------------------------------------------------------------------
# 9. ScheduleOptimizer
# ---------------------------------------------------------------------------


class ScheduleOptimizer:
    """Produces optimal schedules via multi-objective Pareto analysis.

    The optimizer searches the space of budget allocations to find
    schedules that are not dominated in the yield / exploration-ratio
    objective plane.  It also provides sensitivity analysis to identify
    which parameters most affect total yield.

    Attributes:
        economics: :class:`TheoremGrowthEconomics` used for yield prediction.
        policy: :class:`SchedulingPolicy` providing constraints.
    """

    def __init__(
        self,
        economics: TheoremGrowthEconomics,
        policy: SchedulingPolicy,
    ) -> None:
        self.economics = economics
        self.policy = policy

    # ------------------------------------------------------------------
    # Core optimization
    # ------------------------------------------------------------------

    def optimize(
        self,
        regimes: Sequence[str],
        total_budget: float,
        exploration_weight: float | None = None,
    ) -> dict[str, float]:
        """Find the budget allocation that maximises weighted total yield.

        Combines total theorem yield and exploration ratio in a weighted
        objective:

            ``objective = (1 - w) * total_yield + w * exploration_yield``

        where ``w = exploration_weight`` (defaults to
        ``policy.exploration_ratio``).

        Args:
            regimes: Sequence of regime identifiers to consider.
            total_budget: Total budget units available.
            exploration_weight: Override for the exploration–exploitation
                weight (uses ``policy.exploration_ratio`` when ``None``).

        Returns:
            Dict mapping each regime to its optimized budget allocation.
        """
        if exploration_weight is None:
            exploration_weight = self.policy.exploration_ratio

        # Start from the purely greedy optimal allocation.
        base = self.economics.optimal_investment(regimes, total_budget)

        # Impose a minimum exploration budget on at least
        # floor(exploration_ratio * n_regimes) regimes.
        n_explore = max(1, round(exploration_weight * len(regimes)))
        explore_regimes = list(regimes)[:n_explore]
        exploit_regimes = list(regimes)[n_explore:]

        explore_budget = exploration_weight * total_budget
        exploit_budget = total_budget - explore_budget

        explore_alloc = self.economics.optimal_investment(
            explore_regimes, explore_budget
        )
        exploit_alloc = self.economics.optimal_investment(
            exploit_regimes, exploit_budget
        )
        return {**explore_alloc, **exploit_alloc}

    def multi_objective(
        self,
        regimes: Sequence[str],
        total_budget: float,
        n_candidates: int = 20,
    ) -> list[dict[str, float]]:
        """Generate *n_candidates* allocations sweeping the objective space.

        Varies the exploration weight from 0 to 1 in *n_candidates* equal
        steps to produce a front of candidate schedules.

        Args:
            regimes: Regime identifiers.
            total_budget: Total budget.
            n_candidates: Number of candidate allocations to generate.

        Returns:
            List of allocation dicts, one per candidate.
        """
        candidates = []
        for i in range(n_candidates):
            w = i / max(n_candidates - 1, 1)
            candidates.append(self.optimize(regimes, total_budget, w))
        return candidates

    def pareto_schedule(
        self,
        regimes: Sequence[str],
        total_budget: float,
        n_candidates: int = 20,
    ) -> list[dict[str, float]]:
        """Return Pareto-dominant allocations in the yield/explore plane.

        Filters the candidate set produced by :meth:`multi_objective` to
        retain only non-dominated solutions (higher yield is better;
        higher exploration ratio is better).

        Args:
            regimes: Regime identifiers.
            total_budget: Total budget.
            n_candidates: Candidate pool size before filtering.

        Returns:
            List of Pareto-optimal allocation dicts.
        """
        candidates = self.multi_objective(regimes, total_budget, n_candidates)
        # Compute objective vector for each candidate.
        objectives: list[tuple[float, float]] = []
        for alloc in candidates:
            total_yield = self.economics.total_predicted_yield(alloc)
            n_explore = max(1, round(self.policy.exploration_ratio * len(regimes)))
            explore_yield = self.economics.total_predicted_yield(
                {r: v for r, v in list(alloc.items())[:n_explore]}
            )
            objectives.append((total_yield, explore_yield))

        pareto: list[dict[str, float]] = []
        for i, (y_i, e_i) in enumerate(objectives):
            dominated = any(
                y_j >= y_i and e_j >= e_i and (y_j > y_i or e_j > e_i)
                for j, (y_j, e_j) in enumerate(objectives)
                if j != i
            )
            if not dominated:
                pareto.append(candidates[i])
        return pareto

    def sensitivity_analysis(
        self,
        regime: str,
        budget: float,
        param_range: tuple[float, float] = (0.01, 0.20),
        n_points: int = 10,
    ) -> list[tuple[float, float]]:
        r"""Analyse how sensitive yield is to the growth-rate parameter.

        Varies :math:`\lambda_r` from ``param_range[0]`` to
        ``param_range[1]`` and reports predicted yield at each value.

        Args:
            regime: Regime to analyse.
            budget: Fixed budget for the analysis.
            param_range: ``(min_lambda, max_lambda)`` range to sweep.
            n_points: Number of sample points.

        Returns:
            List of ``(lambda_value, predicted_yield)`` tuples.
        """
        results: list[tuple[float, float]] = []
        lo, hi = param_range
        for i in range(n_points):
            lam = lo + (hi - lo) * i / max(n_points - 1, 1)
            # Temporarily override.
            original = self.economics.regime_growth_rates.get(regime)
            self.economics.regime_growth_rates[regime] = lam
            y = self.economics.model_growth(regime, budget)
            if original is None:
                self.economics.regime_growth_rates.pop(regime, None)
            else:
                self.economics.regime_growth_rates[regime] = original
            results.append((lam, y))
        return results


# ---------------------------------------------------------------------------
# 2. IdeationScheduler
# ---------------------------------------------------------------------------


class IdeationScheduler:
    """Top-level orchestrator for ideation scheduling across epochs.

    :class:`IdeationScheduler` assembles the other components—
    :class:`TheoremGrowthEconomics`, :class:`ExplorationBudget`,
    :class:`ExploitationPrioritizer`, :class:`ScheduleOptimizer`,
    :class:`IdeationClock`, and :class:`ScheduleHistory`—into a coherent
    scheduling loop.  It also integrates with the copilot layer for
    warm-start priors and advisory output.

    Attributes:
        policy: Active :class:`SchedulingPolicy`.
        economics: Theorem yield model.
        clock: Epoch and deadline tracker.
        history: Accumulated schedule history.
        optimizer: Multi-objective schedule optimizer.
        exploitation_prioritizer: Deepening opportunity ranker.
        _budget_ledger: Optional legacy :class:`BudgetLedger` integration.
    """

    def __init__(
        self,
        policy: SchedulingPolicy | None = None,
        economics: TheoremGrowthEconomics | None = None,
        clock: IdeationClock | None = None,
        budget_ledger: BudgetLedger | None = None,
    ) -> None:
        self.policy = policy or SchedulingPolicy()
        self.economics = economics or TheoremGrowthEconomics()
        self.clock = clock or IdeationClock()
        self.history = ScheduleHistory()
        self.optimizer = ScheduleOptimizer(self.economics, self.policy)
        self.exploitation_prioritizer = ExploitationPrioritizer()
        self._budget_ledger = budget_ledger

    # ------------------------------------------------------------------
    # Primary interface
    # ------------------------------------------------------------------

    def schedule(
        self,
        ideas: tuple[IdeaProposal, ...],
        total_budget: float,
        regimes: Sequence[str] | None = None,
    ) -> IdeationSchedule:
        """Produce a full :class:`IdeationSchedule` for the current epoch.

        Splits ideas into explorations and exploitations according to
        ``policy.exploration_ratio``, optimises budget allocation, and
        records the result in :attr:`history`.

        Args:
            ideas: Proposed ideas for this epoch.
            total_budget: Budget available for this epoch.
            regimes: Regime identifiers to consider; defaults to idea
                titles when not provided.

        Returns:
            A fully-populated :class:`IdeationSchedule`.
        """
        errors = self.policy.validate()
        if errors:
            _log.warning("Policy validation errors: %s", errors)

        if regimes is None:
            regimes = [idea.title for idea in ideas]

        # Split ideas into explore / exploit.
        explorations, exploitations = self._split_ideas(ideas, total_budget)

        # Optimize budget allocation.
        regime_alloc = self._compute_regime_allocations(list(regimes), total_budget)

        # Predict yield.
        expected_yield = self.economics.total_predicted_yield(regime_alloc)

        # Determine phase.
        phase = self._detect_phase(regime_alloc)

        # Decide whether copilot assistance was used.
        copilot_assisted = any(
            self.economics.empirical_counts.get(r, 0)
            < self.policy.copilot_consultation_threshold
            for r in regimes
        )

        sched = IdeationSchedule(
            schedule_id=str(uuid.uuid4()),
            epoch=self.clock.current_epoch,
            planned_explorations=tuple(explorations),
            planned_exploitations=tuple(exploitations),
            budget=total_budget,
            expected_yield=expected_yield,
            regime_allocations=regime_alloc,
            phase=phase,
            copilot_assisted=copilot_assisted,
        )
        self.history.record(sched)
        self.policy.apply_escalation(sched)
        return sched

    # ------------------------------------------------------------------
    # Sub-routines
    # ------------------------------------------------------------------

    def prioritize(
        self, ideas: Sequence[IdeaProposal]
    ) -> list[IdeaProposal]:
        """Return *ideas* sorted by descending priority score.

        Priority combines payoff and novelty (ideas not seen before score
        higher).  Ideas with the same payoff are sorted alphabetically for
        determinism.

        Args:
            ideas: Ideas to rank.

        Returns:
            Sorted list, highest priority first.
        """
        seen = {s.title for s in self.history.records}
        return sorted(
            ideas,
            key=lambda idea: (
                idea.payoff * (1.5 if idea.title not in seen else 1.0),
                idea.title,
            ),
            reverse=True,
        )

    def balance_exploration_exploitation(
        self, ideas: Sequence[IdeaProposal], budget: float
    ) -> tuple[list[IdeaProposal], list[IdeaProposal]]:
        """Split *ideas* into exploration and exploitation lists.

        Uses ``policy.exploration_ratio`` to determine the split, then
        enforces ``policy.min_exploitation_ideas`` and
        ``policy.max_exploitation_ideas``.

        Args:
            ideas: All candidate ideas.
            budget: Available budget (used to adjust split under pressure).

        Returns:
            ``(explorations, exploitations)`` tuple.
        """
        return self._split_ideas(list(ideas), budget)

    def budget_aware(
        self,
        ideas: Sequence[IdeaProposal],
        budget: float,
    ) -> list[IdeaProposal]:
        """Return the subset of *ideas* affordable within *budget*.

        Each idea costs 1 budget unit.  Ideas are admitted in priority
        order until the budget is exhausted.

        Args:
            ideas: All candidate ideas.
            budget: Total budget available.

        Returns:
            Affordable subset in priority order.
        """
        prioritized = self.prioritize(list(ideas))
        affordable: list[IdeaProposal] = []
        remaining = budget
        for idea in prioritized:
            if remaining >= 1.0:
                affordable.append(idea)
                remaining -= 1.0
            else:
                break
        return affordable

    def deadline_aware(
        self,
        ideas: Sequence[IdeaProposal],
        budget: float,
    ) -> list[IdeaProposal]:
        """Adjust the candidate idea set based on deadline pressure.

        Under high deadline pressure (> 0.7) the scheduler reduces
        exploration in favour of exploitation to maximise near-term yield.

        Args:
            ideas: All candidate ideas.
            budget: Available budget.

        Returns:
            Adjusted idea list, possibly truncated or reordered.
        """
        pressure = self.clock.deadline_pressure()
        if pressure > 0.7:
            # Favour exploitations under high pressure.
            _, exploitations = self._split_ideas(list(ideas), budget)
            explorations, _ = self._split_ideas(list(ideas), budget)
            # Increase exploitation share.
            adjusted_explore = explorations[: max(1, len(explorations) // 2)]
            return list(adjusted_explore) + list(exploitations)
        return self.prioritize(list(ideas))

    def copilot_schedule_advice(
        self,
        ideas: tuple[IdeaProposal, ...],
        budget: float,
    ) -> str:
        """Return a natural-language scheduling advisory for the copilot layer.

        Generates a human-readable summary of the recommended schedule,
        including phase, exploration ratio, and deadline pressure.  This
        advisory is intended for display in copilot interfaces or for
        seeding further LLM-based reasoning.

        Args:
            ideas: Current idea pool.
            budget: Available budget.

        Returns:
            Multi-line advisory string.
        """
        sched = self.schedule(ideas, budget)
        pressure = self.clock.deadline_pressure()
        lines = [
            "=== Copilot Schedule Advisory ===",
            sched.summary_line(),
            f"Deadline pressure: {pressure:.0%}",
            f"Exploration ratio: {sched.exploration_ratio:.0%}",
            f"Copilot prior used: {sched.copilot_assisted}",
            "",
        ]
        if pressure > 0.7:
            lines.append(
                "⚠ High deadline pressure detected. "
                "Recommend increasing exploitation to harvest known gains."
            )
        if sched.phase == SchedulePhase.SATURATION:
            lines.append(
                "ℹ Saturation phase: consider redirecting budget to new regimes."
            )
        lines.append("=================================")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_ideas(
        self, ideas: list[IdeaProposal], budget: float
    ) -> tuple[list[str], list[str]]:
        """Split idea titles into exploration and exploitation lists."""
        prioritized = self.prioritize(ideas)
        n_total = min(len(prioritized), max(1, int(budget)))
        n_explore = round(self.policy.exploration_ratio * n_total)
        n_exploit = n_total - n_explore
        # Enforce min/max on exploitation.
        n_exploit = max(self.policy.min_exploitation_ideas, n_exploit)
        n_exploit = min(self.policy.max_exploitation_ideas, n_exploit)
        n_explore = max(0, n_total - n_exploit)

        exploration_titles = [idea.title for idea in prioritized[:n_explore]]
        exploitation_titles = [
            idea.title for idea in prioritized[n_explore: n_explore + n_exploit]
        ]
        return exploration_titles, exploitation_titles

    def _compute_regime_allocations(
        self, regimes: list[str], total_budget: float
    ) -> dict[str, float]:
        """Compute per-regime budget allocations using the optimizer."""
        if not regimes:
            return {}
        alloc = self.optimizer.optimize(regimes, total_budget)
        # Normalise to fractions.
        total = sum(alloc.values()) or 1.0
        return {r: v / total for r, v in alloc.items()}

    def _detect_phase(self, regime_alloc: dict[str, float]) -> SchedulePhase:
        """Infer the current schedule phase from yield and saturation data."""
        if self.clock.current_epoch < 2:
            return SchedulePhase.BOOTSTRAP
        saturated = sum(
            1
            for r, frac in regime_alloc.items()
            if self.economics.saturation_detection(
                r, frac * 100, self.policy.saturation_yield_threshold
            )
        )
        total = max(len(regime_alloc), 1)
        sat_ratio = saturated / total
        if sat_ratio > 0.6:
            return SchedulePhase.SATURATION
        if self.history.cumulative_yield() > 0:
            return SchedulePhase.GROWTH
        return SchedulePhase.BOOTSTRAP

    # ------------------------------------------------------------------
    # Judgment-geometric integration
    # ------------------------------------------------------------------

    def orchestration_aware_scheduling(
        self,
        ideas: tuple[IdeaProposal, ...],
        total_budget: float,
        *,
        controller: Any | None = None,
        regimes: Sequence[str] | None = None,
    ) -> IdeationSchedule:
        """Coordinate scheduling with the orchestration controller.

        Uses :mod:`jugeo.orchestration.controller` to query the current
        orchestration state and adjust the schedule accordingly.  When the
        controller reports high convergence pressure, exploration budget is
        reduced in favour of exploitation.

        Parameters
        ----------
        ideas:
            Proposed ideas for this epoch.
        total_budget:
            Budget available for this epoch.
        controller:
            An :class:`~jugeo.orchestration.controller.OrchestrationController`
            or :class:`~jugeo.orchestration.controller.Orchestrator` instance.
            When ``None`` the method falls back to :meth:`schedule`.
        regimes:
            Regime identifiers to consider.

        Returns
        -------
        IdeationSchedule
            A schedule adjusted for orchestration state.
        """
        if OrchestrationController is None or controller is None:
            return self.schedule(ideas, total_budget, regimes=regimes)

        # Query orchestrator state for convergence pressure.
        state = controller.state if hasattr(controller, "state") else None
        convergence_score = 0.0
        if state is not None and hasattr(state, "score"):
            convergence_score = float(state.score)

        # Adjust policy: under high convergence, reduce exploration.
        adjusted_policy = self.policy
        if convergence_score > 0.7:
            adjusted_policy = self.policy.clone_with(
                exploration_ratio=max(0.1, self.policy.exploration_ratio * 0.5),
            )
            _log.info(
                "Orchestration convergence %.2f > 0.7 — reducing exploration to %.2f",
                convergence_score,
                adjusted_policy.exploration_ratio,
            )

        # Temporarily swap policy, schedule, then restore.
        original_policy = self.policy
        self.policy = adjusted_policy
        self.optimizer = ScheduleOptimizer(self.economics, self.policy)
        try:
            sched = self.schedule(ideas, total_budget, regimes=regimes)
        finally:
            self.policy = original_policy
            self.optimizer = ScheduleOptimizer(self.economics, self.policy)

        return sched

    def cache_replay_schedule(
        self,
        ideas: tuple[IdeaProposal, ...],
        total_budget: float,
        *,
        cache: Any | None = None,
        replay_engine: Any | None = None,
        regimes: Sequence[str] | None = None,
    ) -> IdeationSchedule:
        """Use cache and replay for efficient re-scheduling.

        Uses :mod:`jugeo.runtime.cache` and :mod:`jugeo.runtime.replay`
        to check whether a suitable schedule already exists in the cache
        or can be replayed from a previous epoch.  Only computes a fresh
        schedule when no cached/replayed result is available.

        Parameters
        ----------
        ideas:
            Proposed ideas for this epoch.
        total_budget:
            Budget available for this epoch.
        cache:
            A :class:`~jugeo.runtime.cache.SemanticCache` instance.
        replay_engine:
            A :class:`~jugeo.runtime.replay.ReplayEngine` instance.
        regimes:
            Regime identifiers to consider.

        Returns
        -------
        IdeationSchedule
            A schedule, potentially retrieved from cache or replayed.
        """
        cache_key_str = f"schedule:{','.join(i.title for i in ideas)}:{total_budget}"

        # Try cache first.
        if SemanticCache is not None and cache is not None:
            if hasattr(cache, "get"):
                cached = cache.get(cache_key_str)
                if cached is not None and isinstance(cached, IdeationSchedule):
                    _log.info("Schedule cache hit for epoch %d", self.clock.current_epoch)
                    return cached

        # Try replay from prior epoch.
        if ReplayEngine is not None and replay_engine is not None:
            if hasattr(replay_engine, "replay") and self.history.records:
                last_schedule = self.history.records[-1]
                # Replay if the idea set hasn't changed much.
                current_titles = {i.title for i in ideas}
                prev_titles = set(last_schedule.planned_explorations + last_schedule.planned_exploitations)
                overlap = len(current_titles & prev_titles) / max(len(current_titles | prev_titles), 1)
                if overlap >= 0.8:
                    _log.info(
                        "Replaying prior schedule (%.0f%% idea overlap)",
                        overlap * 100,
                    )
                    return last_schedule

        # Compute fresh schedule.
        sched = self.schedule(ideas, total_budget, regimes=regimes)

        # Store in cache for future re-use.
        if SemanticCache is not None and cache is not None and hasattr(cache, "put"):
            cache.put(cache_key_str, sched)

        return sched

    def evaluation_feedback_scheduling(
        self,
        ideas: tuple[IdeaProposal, ...],
        total_budget: float,
        *,
        evaluation_scores: Mapping[str, float] | None = None,
        regimes: Sequence[str] | None = None,
    ) -> IdeationSchedule:
        """Integrate evaluation scores into the scheduling process.

        Uses :mod:`jugeo.evaluation` feedback to re-rank ideas before
        scheduling.  Ideas that received high evaluation scores in prior
        epochs are prioritised; poorly-scored ideas are deferred.

        Parameters
        ----------
        ideas:
            Proposed ideas for this epoch.
        total_budget:
            Budget available for this epoch.
        evaluation_scores:
            Mapping from idea title to evaluation score in [0, 1].
            Ideas not in the mapping receive a neutral score of 0.5.
        regimes:
            Regime identifiers to consider.

        Returns
        -------
        IdeationSchedule
            A schedule adjusted for evaluation feedback.
        """
        if not evaluation_scores:
            return self.schedule(ideas, total_budget, regimes=regimes)

        # Re-rank ideas using evaluation feedback.
        scored_ideas = []
        for idea in ideas:
            eval_score = evaluation_scores.get(idea.title, 0.5)
            # Combine payoff with evaluation feedback.
            adjusted_payoff = float(idea.payoff) * (0.5 + 0.5 * eval_score)
            scored_ideas.append((idea, adjusted_payoff))
        scored_ideas.sort(key=lambda item: item[1], reverse=True)

        # Rebuild the idea tuple in adjusted priority order.
        reranked = tuple(idea for idea, _ in scored_ideas)
        return self.schedule(reranked, total_budget, regimes=regimes)


# ---------------------------------------------------------------------------
# 10. ScheduleDiagnostics
# ---------------------------------------------------------------------------


class ScheduleDiagnostics:
    """Produces human-readable diagnostic reports over schedule history.

    :class:`ScheduleDiagnostics` is the reporting layer: it reads
    :class:`ScheduleHistory` and :class:`TheoremGrowthEconomics` state
    and formats them as plain-text or structured-dict reports.

    Attributes:
        history: Schedule history to analyse.
        economics: Theorem yield model for cross-referencing predictions.
        clock: Clock for temporal context.
    """

    def __init__(
        self,
        history: ScheduleHistory,
        economics: TheoremGrowthEconomics,
        clock: IdeationClock,
    ) -> None:
        self.history = history
        self.economics = economics
        self.clock = clock

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line plain-text session summary.

        Covers total epochs run, cumulative yield, average exploration
        ratio, and clock state.

        Returns:
            Formatted string for terminal output.
        """
        n = len(self.history.records)
        cum_yield = self.history.cumulative_yield()
        avg_explore = self.history.average_exploration_ratio()
        best = self.history.most_productive_epoch()
        clock_info = self.clock.epoch_summary()
        lines = [
            "=== IdeationScheduler Diagnostics ===",
            f"Epochs recorded  : {n}",
            f"Cumulative yield : {cum_yield:.2f} theorems",
            f"Avg explore ratio: {avg_explore:.1%}",
        ]
        if best is not None:
            lines.append(
                f"Best epoch       : {best.epoch} (yield={best.expected_yield:.2f})"
            )
        lines += [
            f"Current epoch    : {clock_info['current_epoch']}",
            f"Epochs remaining : {clock_info['epochs_remaining']}",
            f"Deadline pressure: {clock_info['deadline_pressure']:.1%}",
            "=====================================",
        ]
        return "\n".join(lines)

    def yield_report(self) -> dict[str, Any]:
        """Return a structured yield report as a plain dict.

        Returns:
            Dict with keys ``epochs``, ``yields``, ``cumulative``,
            ``mean``, ``std_dev``, ``best_epoch``.
        """
        series = self.history.yield_over_time()
        yields = [y for _, y in series]
        mean = statistics.mean(yields) if yields else 0.0
        std = statistics.stdev(yields) if len(yields) > 1 else 0.0
        best = self.history.most_productive_epoch()
        return {
            "epochs": [e for e, _ in series],
            "yields": yields,
            "cumulative": self.history.cumulative_yield(),
            "mean": round(mean, 4),
            "std_dev": round(std, 4),
            "best_epoch": best.epoch if best else None,
        }

    def balance_report(self) -> dict[str, Any]:
        """Return exploration/exploitation balance statistics.

        Returns:
            Dict with keys ``average_exploration_ratio``,
            ``phase_distribution``, ``regime_time_allocation``.
        """
        return {
            "average_exploration_ratio": round(
                self.history.average_exploration_ratio(), 4
            ),
            "phase_distribution": self.history.phase_distribution(),
            "regime_time_allocation": {
                regime: [(e, round(f, 4)) for e, f in series]
                for regime, series in self.history.regime_time_allocation().items()
            },
        }

    def copilot_schedule_summary(self) -> str:
        """Return a copilot-ready narrative summary of the scheduling session.

        Synthesises yield trends, balance, deadline pressure, and phase
        history into a paragraph suitable for display in a copilot
        interface or for seeding downstream LLM reasoning.

        Returns:
            Multi-paragraph narrative string.
        """
        n = len(self.history.records)
        if n == 0:
            return (
                "No schedules have been recorded yet. "
                "Run IdeationScheduler.schedule() to begin."
            )
        cum_yield = self.history.cumulative_yield()
        avg_explore = self.history.average_exploration_ratio()
        best = self.history.most_productive_epoch()
        phases = self.history.phase_distribution()
        pressure = self.clock.deadline_pressure()

        paras = [
            f"The scheduling session has completed {n} epoch(s) with a "
            f"cumulative theorem yield of {cum_yield:.1f}.  "
            f"The average exploration ratio was {avg_explore:.0%}, "
            f"reflecting a balance between finding new mathematical kinds "
            f"and deepening known areas.",
        ]
        if best is not None:
            paras.append(
                f"The most productive epoch was epoch {best.epoch}, "
                f"which yielded an estimated {best.expected_yield:.1f} theorems "
                f"(phase: {best.phase.value})."
            )
        dominant_phase = max(phases, key=phases.get) if phases else "unknown"
        paras.append(
            f"The session spent most time in the '{dominant_phase}' phase.  "
            f"Deadline pressure is currently {pressure:.0%}."
        )
        if pressure > 0.7:
            paras.append(
                "Given high deadline pressure, the copilot recommends "
                "shifting toward exploitation to consolidate gains."
            )
        return "\n\n".join(paras)

    def regime_saturation_report(
        self, regimes: Sequence[str], budget_per_regime: float
    ) -> dict[str, bool]:
        """Report whether each regime is saturated at *budget_per_regime*.

        Args:
            regimes: Regime identifiers to check.
            budget_per_regime: Budget invested per regime for saturation check.

        Returns:
            Dict mapping regime name to saturation flag.
        """
        return {
            r: self.economics.saturation_detection(r, budget_per_regime)
            for r in regimes
        }

    def budget_efficiency_report(self) -> dict[str, Any]:
        """Return budget-efficiency statistics across recorded epochs.

        Returns:
            Dict with keys ``mean_yield_per_budget``, ``total_budget_spent``,
            ``total_yield``, ``efficiency_series``.
        """
        series = [
            (s.epoch, s.yield_per_budget_unit) for s in self.history.records
        ]
        efficiencies = [e for _, e in series]
        mean_eff = statistics.mean(efficiencies) if efficiencies else 0.0
        total_budget = sum(s.budget for s in self.history.records)
        return {
            "mean_yield_per_budget": round(mean_eff, 4),
            "total_budget_spent": round(total_budget, 2),
            "total_yield": round(self.history.cumulative_yield(), 2),
            "efficiency_series": [(e, round(v, 4)) for e, v in series],
        }


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------


def schedule_ideas(
    ideas: tuple[IdeaProposal, ...],
    budgets: BudgetLedger,
    *,
    budget_key: str = "ideation",
) -> IdeationSchedule:
    """Backward-compatible convenience wrapper around :class:`IdeationScheduler`.

    Preserves the original API while delegating to the full scheduler.
    ``budgets.consume(budget_key, 1)`` is still called for each admitted idea
    to maintain ledger consistency.

    Args:
        ideas: Proposed ideas to schedule.
        budgets: Legacy :class:`BudgetLedger` instance.
        budget_key: Dimension key for budget consumption.

    Returns:
        A minimal :class:`IdeationSchedule` with accepted and deferred ideas.
    """
    accepted: list[str] = []
    deferred: list[str] = []
    for idea in sorted(ideas, key=lambda item: item.payoff, reverse=True):
        if budgets.consume(budget_key, 1):
            accepted.append(idea.title)
        else:
            deferred.append(idea.title)
    total = len(accepted)
    n_explore = round(0.4 * total)
    return IdeationSchedule(
        schedule_id=str(uuid.uuid4()),
        epoch=0,
        planned_explorations=tuple(accepted[:n_explore]),
        planned_exploitations=tuple(accepted[n_explore:]),
        budget=float(total),
        expected_yield=float(total) * 0.8,
        metadata={"deferred": tuple(deferred)},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "ExhaustionPolicy",
    "SchedulePhase",
    # Dataclasses
    "IdeationSchedule",
    # Main classes
    "IdeationScheduler",
    "TheoremGrowthEconomics",
    "ExplorationBudget",
    "ExploitationPrioritizer",
    "SchedulingPolicy",
    "IdeationClock",
    "ScheduleHistory",
    "ScheduleOptimizer",
    "ScheduleDiagnostics",
    # Legacy
    "schedule_ideas",
]

# copilot: shared-core marker for future LLM orchestration.
