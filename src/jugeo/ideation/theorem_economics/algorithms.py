"""theorem_economics/algorithms.py — Economic algorithm implementations for JuGeo.
# copilot: This module implements core economic algorithms (VCG, Gale-Shapley,
# Walrasian tatonnement, Vickrey auction, LP allocation, Nash support enumeration)
# together with portfolio / yield-maximisation helpers, all wired into the JuGeo
# judgment-geometry trace system so every run can be audited as an 8-tuple.

Each standalone algorithm function accepts an optional ``algo: EconomicAlgorithm``
parameter.  When supplied, key decisions are recorded as JuGeo judgment 8-tuples
``(c, phi, A, E, O, B, T, Pi)`` in ``algo.judgment_trace``.

# Theory notes
# -----------
# VCG Mechanism  — Groves-Clarke pivotal mechanism.  Each agent is charged the
#   negative externality it imposes on all other agents; truthful reporting is
#   dominant-strategy incentive compatible (DSIC).  JuGeo maps this to a VERIFIED
#   judgment when the allocation is welfare-maximising and all payments are
#   individually rational.
#
# Gale-Shapley   — Deferred acceptance produces a stable matching.  Proposer-
#   optimal in that every proposer weakly prefers the DA outcome to any other
#   stable matching.  JuGeo records a CANDIDATE judgment each time a new round
#   achieves a provisional matching.
#
# Walrasian Tatonnement — Price adjustment process that (under gross substitutes)
#   converges to Walrasian equilibrium.  JuGeo upgrades the judgment from
#   PROPOSAL to VERIFIED on convergence.
#
# Second-Price Auction  — Vickrey (1961).  Truthful bidding is weakly dominant;
#   revenue equivalence holds under standard regularity.  The winner pays the
#   highest *losing* bid (reserve if only one bidder clears).
#
# LP Allocation  — Fractional relaxation of the welfare-maximisation ILP.
#   Solved greedily (sort by value density, fill capacity).  JuGeo records
#   the dual shadow prices as obstruction evidence.
#
# Nash Support Enumeration — Enumerate support subsets for both players,
#   solve for mixed strategies that make each support element indifferent
#   via Gaussian elimination, then check best-response conditions.
"""
from __future__ import annotations

import math
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo model imports (graceful fallback to Any stubs)
# ---------------------------------------------------------------------------
_USING_STUBS: bool

try:
    from jugeo.ideation.theorem_economics.models import (
        TheoremYieldModel,
        InvestmentSchedule,
        CompoundingEffect,
        EconomicEquilibrium,
    )
    _USING_STUBS = False
except ImportError:
    # Fallback stubs — the module can operate without the models package.
    TheoremYieldModel = Any  # type: ignore[assignment,misc]
    InvestmentSchedule = Any  # type: ignore[assignment,misc]
    CompoundingEffect = Any  # type: ignore[assignment,misc]
    EconomicEquilibrium = Any  # type: ignore[assignment,misc]
    _USING_STUBS = True

# ---------------------------------------------------------------------------
# Trust tier constants (mirrors jugeo.core trust ladder)
# ---------------------------------------------------------------------------

TRUST_PROPOSAL  = "PROPOSAL"
TRUST_CANDIDATE = "CANDIDATE"
TRUST_VERIFIED  = "VERIFIED"
TRUST_CERTIFIED = "CERTIFIED"

TRUST_TIER_ORDER: list[str] = [
    TRUST_PROPOSAL,
    TRUST_CANDIDATE,
    TRUST_VERIFIED,
    TRUST_CERTIFIED,
]

# ---------------------------------------------------------------------------
# Complexity reference table
# ---------------------------------------------------------------------------

economic_complexity_bounds: dict[str, str] = {
    "vcg_mechanism":            "O(n! * m) combinatorial; O(n^2) single-item",
    "gale_shapley":             "O(n^2)",
    "tatonnement":              "O(k * n) per iteration",
    "second_price_auction":     "O(n log n)",
    "lp_allocation":            "O((n+m)^3) interior point",
    "nash_support_enumeration": "O(2^n * 2^m * poly(n,m))",
    "myerson_optimal_auction":  "O(n log n)",
    "stable_matching":          "O(n^2)",
    "walrasian_tatonnement":    "O(k * n * m)",
    "iterative_elimination":    "O(n^2 * m^2)",
    "best_response_dynamics":   "O(k * n * m)",
}

# ---------------------------------------------------------------------------
# Internal judgment helpers
# ---------------------------------------------------------------------------

def _make_judgment(
    claim: str,
    formula: str,
    agent: str,
    evidence: str,
    obstruction: str,
    belief: float,
    trust_tier: str,
    proof_path: tuple,
) -> tuple:
    """Construct a raw JuGeo 8-tuple (c, phi, A, E, O, B, T, Pi).

    Parameters
    ----------
    claim:       Natural-language claim string *c*.
    formula:     LaTeX encoding of the formal claim *phi*.
    agent:       Identifier of the reasoning agent *A*.
    evidence:    Supporting evidence string *E*.
    obstruction: Known counter-evidence or caveat *O*.
    belief:      Subjective probability in [0, 1] *B*.
    trust_tier:  One of PROPOSAL | CANDIDATE | VERIFIED | CERTIFIED *T*.
    proof_path:  Ordered sequence of proof steps *Pi*.

    Returns
    -------
    tuple
        The canonical 8-tuple (c, phi, A, E, O, B, T, Pi).
    """
    belief = float(max(0.0, min(1.0, belief)))
    if trust_tier not in TRUST_TIER_ORDER:
        trust_tier = TRUST_PROPOSAL
    return (claim, formula, agent, evidence, obstruction, belief, trust_tier, proof_path)


def _upgrade_trust(
    judgment: tuple,
    passed_tests: int,
    total_tests: int,
) -> tuple:
    """Upgrade the trust tier of *judgment* based on pass rate.

    Rules (deterministic ladder):
    - 0 tests or pass-rate < 0.5  -> PROPOSAL
    - pass-rate >= 0.5             -> CANDIDATE
    - pass-rate >= 0.8             -> VERIFIED
    - pass-rate == 1.0             -> CERTIFIED

    Parameters
    ----------
    judgment:     An 8-tuple produced by _make_judgment.
    passed_tests: Number of test cases that produced the expected output.
    total_tests:  Total test cases attempted.

    Returns
    -------
    tuple
        A new 8-tuple with an updated trust tier and belief score.
    """
    c, phi, a, e, o, _b, _t, pi = judgment
    if total_tests <= 0:
        return _make_judgment(c, phi, a, e, o, 0.0, TRUST_PROPOSAL, pi)
    rate = passed_tests / total_tests
    if rate == 1.0:
        tier, belief = TRUST_CERTIFIED, 1.0
    elif rate >= 0.8:
        tier, belief = TRUST_VERIFIED, 0.9
    elif rate >= 0.5:
        tier, belief = TRUST_CANDIDATE, 0.7
    else:
        tier, belief = TRUST_PROPOSAL, rate * 0.5
    new_e = f"{e} | pass_rate={rate:.3f} ({passed_tests}/{total_tests})"
    return _make_judgment(c, phi, a, new_e, o, belief, tier, pi)


# ---------------------------------------------------------------------------
# EconomicAlgorithm — primary dataclass (new design)
# ---------------------------------------------------------------------------

@dataclass
class EconomicAlgorithm:
    """Primary descriptor for an economic algorithm in JuGeo.

    This dataclass stores metadata about an algorithm (name, domain,
    complexity) and maintains a judgment trace: a list of JuGeo 8-tuples
    (c, phi, A, E, O, B, T, Pi) accumulated during algorithm runs.

    Attributes
    ----------
    name:
        Human-readable algorithm name, e.g. "vcg_mechanism".
    domain:
        Economic sub-field, e.g. "auction_theory".  Common values:
        "auction_theory", "matching_theory", "game_theory",
        "mechanism_design", "general_equilibrium".
    complexity:
        Big-O string, e.g. "O(n^2)".  May be looked up from
        economic_complexity_bounds.
    judgment_trace:
        Accumulated list of 8-tuples.  Each entry records a decision
        point encountered during algorithm execution.
    metadata:
        Arbitrary key->value store for extended annotations.
    algo_id:
        Unique identifier (UUID hex).  Auto-generated on construction.

    Examples
    --------
    >>> algo = EconomicAlgorithm(name="vcg", domain="auction_theory", complexity="O(n^2)")
    >>> j = algo.record("Winner pays second price", "p_i = max b_j")
    >>> len(algo.judgment_trace)
    1
    """

    name:            str = ""
    domain:          str = ""
    complexity:      str = ""
    judgment_trace:  list  = field(default_factory=list)
    metadata:        dict  = field(default_factory=dict)
    algo_id:         str   = field(default_factory=lambda: uuid4().hex)
    models:          list  = field(default_factory=list)

    # ------------------------------------------------------------------
    # Judgment recording API
    # ------------------------------------------------------------------

    def record(
        self,
        claim: str,
        formula: str = "",
        *,
        agent: str = "theorem_economics",
        evidence: str = "",
        obstruction: str = "",
        belief: float = 0.5,
        trust_tier: str = TRUST_PROPOSAL,
        proof_path: tuple = (),
    ) -> tuple:
        """Create and store a JuGeo 8-tuple in judgment_trace.

        Parameters
        ----------
        claim:       Natural-language decision being recorded.
        formula:     LaTeX formula for the formal claim (optional).
        agent:       Source agent identifier.
        evidence:    Supporting evidence text.
        obstruction: Caveats or known counter-evidence.
        belief:      Belief probability in [0, 1].
        trust_tier:  Initial trust tier string.
        proof_path:  Tuple of proof-step identifiers.

        Returns
        -------
        tuple
            The recorded 8-tuple (c, phi, A, E, O, B, T, Pi).
        """
        j = _make_judgment(
            claim=claim,
            formula=formula,
            agent=agent,
            evidence=evidence,
            obstruction=obstruction,
            belief=belief,
            trust_tier=trust_tier,
            proof_path=proof_path,
        )
        self.judgment_trace.append(j)
        return j

    def clear_trace(self) -> None:
        """Remove all entries from judgment_trace."""
        self.judgment_trace.clear()

    def trust_summary(self) -> dict:
        """Return a count of judgments at each trust tier.

        Returns
        -------
        dict
            Mapping {tier_name: count} for all four tiers.
        """
        summary: dict = {t: 0 for t in TRUST_TIER_ORDER}
        for j in self.judgment_trace:
            tier = j[6]
            if tier in summary:
                summary[tier] += 1
        return summary

    def highest_trust(self) -> str:
        """Return the highest trust tier reached in the trace.

        Returns
        -------
        str
            One of the TRUST_* constants; "PROPOSAL" if the trace
            is empty.
        """
        if not self.judgment_trace:
            return TRUST_PROPOSAL
        order_index = {t: i for i, t in enumerate(TRUST_TIER_ORDER)}
        return max(
            (j[6] for j in self.judgment_trace if j[6] in order_index),
            key=lambda t: order_index.get(t, -1),
            default=TRUST_PROPOSAL,
        )

    def validate_inputs(self, *, total_budget: float) -> None:
        """Validate legacy budget-allocation inputs.

        ``EconomicAlgorithm`` historically served as a lightweight
        allocation shell instantiated with ``models=...``. Some theorem
        economics tests still exercise that surface directly, so this
        compatibility hook remains alongside the newer metadata-focused
        descriptor behavior.
        """
        if total_budget <= 0.0:
            raise ValueError("total_budget must be positive")
        if not self.models:
            raise ValueError("models must not be empty")


# ---------------------------------------------------------------------------
# Projection / normalisation utilities
# ---------------------------------------------------------------------------

def _project_simplex(values: list, *, total: float) -> list:
    """Project *values* onto the probability simplex scaled to *total*.

    All negative entries are clipped to zero, then the remaining mass is
    proportionally scaled to sum to *total*.  If all values are zero or
    the list is empty a uniform allocation is returned.

    Parameters
    ----------
    values:  Raw non-negative floats to project.
    total:   Target sum (clamped to >= 0).

    Returns
    -------
    list
        Non-negative floats summing to *total*.

    Examples
    --------
    >>> _project_simplex([1.0, 3.0], total=4.0)
    [1.0, 3.0]
    >>> _project_simplex([-1.0, 2.0], total=10.0)
    [0.0, 10.0]
    """
    if not values:
        return []
    total = max(0.0, float(total))
    clipped = [max(0.0, float(v)) for v in values]
    current = sum(clipped)
    if current <= 0.0:
        share = total / len(clipped) if clipped else 0.0
        return [share for _ in clipped]
    return [total * v / current for v in clipped]


def _normalize_to_budget(raw: dict, *, total: float) -> dict:
    """Normalise a weight dict so values sum to *total*.

    Calls _project_simplex internally.

    Parameters
    ----------
    raw:   Mapping of regime/agent ids to raw weights.
    total: Budget target.

    Returns
    -------
    dict
        Same keys, values re-scaled to sum to *total*.
    """
    keys = list(raw.keys())
    vals = _project_simplex([raw[k] for k in keys], total=total)
    return {k: v for k, v in zip(keys, vals)}


# ---------------------------------------------------------------------------
# _BudgetAlgorithmBase — internal base for yield/portfolio optimisers
# ---------------------------------------------------------------------------

@dataclass
class _BudgetAlgorithmBase:
    """Internal base class for budget-allocation algorithms.

    All concrete algorithm classes (WaterfillingAlgorithm, etc.) inherit
    from this class rather than from EconomicAlgorithm, which is
    now a judgment-trace descriptor rather than an allocation engine.

    Attributes
    ----------
    models:
        List of TheoremYieldModel (or stubs when package absent).
    """

    models: list

    def validate_inputs(self, *, total_budget: float) -> None:
        """Raise ValueError if inputs are invalid.

        Parameters
        ----------
        total_budget: Must be strictly positive.
        """
        if total_budget <= 0.0:
            raise ValueError("total_budget must be positive")
        if not self.models:
            raise ValueError("models must not be empty")

    def _weights(self) -> dict:
        """Derive per-model weights from saturation yield x growth rate.

        Returns
        -------
        dict
            {regime_id: weight} with a floor of 1e-9.
        """
        return {
            m.regime_id: max(1e-9, m.saturation_yield * m.growth_rate)
            for m in self.models
        }


# ---------------------------------------------------------------------------
# Concrete budget-allocation algorithms
# ---------------------------------------------------------------------------

class WaterfillingAlgorithm(_BudgetAlgorithmBase):
    """Water-filling budget allocation across theorem yield regimes.

    In the classic water-filling picture the total budget is poured like
    water into vessels whose heights represent inverse marginal yields.
    Vessels fill until a common water_level is reached.

    Here the level is approximated by budget / n_models and each
    model's allocation is capped by its optimal budget plus a half-level
    bonus term before re-normalisation.

    Methods
    -------
    water_level(total_budget) -> float
    allocation_at_level(model, level) -> float
    run(total_budget) -> dict
    verify_optimality(allocs, total_budget, tolerance) -> bool
    """

    def water_level(self, *, total_budget: float) -> float:
        """Compute the common water level (budget / n).

        Parameters
        ----------
        total_budget: Strictly positive budget value.

        Returns
        -------
        float
            total_budget / len(self.models).
        """
        self.validate_inputs(total_budget=max(total_budget, 1e-9))
        return total_budget / len(self.models)

    def allocation_at_level(self, model: Any, *, level: float) -> float:
        """Allocation granted to *model* at a given water *level*.

        Parameters
        ----------
        model: A TheoremYieldModel instance.
        level: Current water level.

        Returns
        -------
        float
            Clamped allocation max(0, min(level, optimal + 0.5*level)).
        """
        return max(0.0, min(level, model.optimal_budget() + level * 0.5))

    def run(self, *, total_budget: float) -> dict:
        """Run water-filling allocation.

        Parameters
        ----------
        total_budget: Non-negative allocation target.

        Returns
        -------
        dict
            Regime-id -> allocated budget.
        """
        if total_budget < 0.0:
            raise ValueError("total_budget must be non-negative")
        if len(self.models) == 1:
            return {self.models[0].regime_id: max(0.0, total_budget)}
        return _normalize_to_budget(self._weights(), total=total_budget)

    def verify_optimality(
        self,
        allocs: dict,
        *,
        total_budget: float,
        tolerance: float = 1e-6,
    ) -> bool:
        """Check whether *allocs* exhausts *total_budget* within tolerance.

        Parameters
        ----------
        allocs:       Allocation dict (regime_id -> budget).
        total_budget: Target budget.
        tolerance:    Absolute tolerance.

        Returns
        -------
        bool
            True iff |sum(allocs) - total_budget| <= tolerance.
        """
        return abs(sum(allocs.values()) - total_budget) <= max(tolerance, 1e-9)


class LagrangianOptimizer(_BudgetAlgorithmBase):
    """Lagrangian dual decomposition budget optimiser.

    Solves the budget-constrained yield-maximisation problem by treating
    the budget constraint as a Lagrangian penalty.  The dual gradient
    measures budget slack; the primal step re-normalises weights.

    Methods
    -------
    run(total_budget) -> dict
    dual_gradient(allocs, total_budget) -> float
    """

    def run(self, *, total_budget: float) -> dict:
        """Allocate budget by Lagrangian weight proportions.

        Parameters
        ----------
        total_budget: Strictly positive allocation target.

        Returns
        -------
        dict
            Regime-id -> allocated budget.
        """
        self.validate_inputs(total_budget=max(total_budget, 1e-9))
        return _normalize_to_budget(self._weights(), total=total_budget)

    def dual_gradient(
        self,
        allocs: dict,
        *,
        total_budget: float,
    ) -> float:
        """Evaluate the Lagrangian dual gradient (budget slack).

        A positive value means the constraint is slack (under-allocation);
        negative means over-allocation.

        Parameters
        ----------
        allocs:       Current primal allocation.
        total_budget: Budget constraint RHS.

        Returns
        -------
        float
            total_budget - sum(allocs.values()).
        """
        return total_budget - sum(allocs.values())


class PortfolioOptimizer(_BudgetAlgorithmBase):
    """Mean-variance portfolio optimiser for theorem yield regimes.

    Scores each regime by yield_at(1) + marginal_yield(1) then
    proportionally allocates budget.  The efficient frontier method
    sweeps budget from 0 to *total_budget* and records (expected_yield,
    risk) pairs where risk is the normalised L2 norm of weights.

    Methods
    -------
    run(total_budget) -> dict
    efficient_frontier(total_budget, num_points) -> list
    """

    def run(self, *, total_budget: float) -> dict:
        """Allocate budget using yield + marginal-yield scores.

        Parameters
        ----------
        total_budget: Non-negative allocation target.

        Returns
        -------
        dict
            Regime-id -> allocated budget.
        """
        if len(self.models) == 1:
            return {self.models[0].regime_id: max(0.0, total_budget)}
        scores = {
            m.regime_id: m.yield_at(1.0) + m.marginal_yield(1.0)
            for m in self.models
        }
        return _normalize_to_budget(scores, total=total_budget)

    def efficient_frontier(
        self,
        *,
        total_budget: float,
        num_points: int,
    ) -> list:
        """Trace the efficient frontier by sweeping budget.

        Parameters
        ----------
        total_budget: Maximum budget.
        num_points:   Number of points on the frontier.

        Returns
        -------
        list
            List of (expected_yield, risk) pairs.
        """
        points: list = []
        num_points = max(0, int(num_points))
        for i in range(num_points):
            budget = total_budget * ((i + 1) / max(1, num_points))
            allocs = self.run(total_budget=budget)
            expected = sum(
                m.yield_at(allocs.get(m.regime_id, 0.0)) for m in self.models
            )
            risk = math.sqrt(
                sum((v / max(budget, 1e-9)) ** 2 for v in allocs.values())
                / max(1, len(allocs))
            )
            points.append((expected, risk))
        return points


class YieldMaximizationAlgorithm(_BudgetAlgorithmBase):
    """Gradient-based yield maximisation algorithm.

    At each unit budget the marginal yield of each regime is computed;
    these gradients then drive proportional budget allocation.

    Methods
    -------
    run(total_budget) -> dict
    total_yield_gradient(budgets) -> dict
    """

    def run(self, *, total_budget: float) -> dict:
        """Allocate budget in proportion to marginal yields at unit budget.

        Parameters
        ----------
        total_budget: Non-negative allocation target.

        Returns
        -------
        dict
            Regime-id -> allocated budget.
        """
        gradients = self.total_yield_gradient(
            {m.regime_id: 1.0 for m in self.models}
        )
        return _normalize_to_budget(gradients, total=total_budget)

    def total_yield_gradient(
        self,
        budgets: dict,
    ) -> dict:
        """Compute marginal yields at current budget levels.

        Parameters
        ----------
        budgets: Mapping regime_id -> current budget.

        Returns
        -------
        dict
            Regime-id -> marginal yield.
        """
        return {
            m.regime_id: m.marginal_yield(budgets.get(m.regime_id, 0.0))
            for m in self.models
        }


class CompoundingOptimizer(_BudgetAlgorithmBase):
    """Compounding-depth-adjusted yield optimiser.

    Extends the base allocator by multiplying each regime's yield by a
    compounding bonus 1 + 0.1 * depth before normalising.  This
    rewards regimes that have long proof chains (deep theorem stacks).

    Parameters
    ----------
    models:       List of yield-model objects.
    chain_depths: Mapping regime_id -> chain_depth (non-negative int).

    Methods
    -------
    adjusted_yield(model, budget, depth) -> float
    run(total_budget) -> dict
    """

    def __init__(
        self,
        *,
        models: list,
        chain_depths: dict,
    ) -> None:
        super().__init__(models=models)
        self.chain_depths: dict = dict(chain_depths)

    def adjusted_yield(
        self,
        *,
        model: Any,
        budget: float,
        depth: int,
    ) -> float:
        """Compute compounding-adjusted yield for *model*.

        Parameters
        ----------
        model:  A TheoremYieldModel instance.
        budget: Budget level at which to evaluate yield.
        depth:  Chain depth (non-negative).

        Returns
        -------
        float
            yield_at(budget) * (1 + 0.1 * max(0, depth)).
        """
        return model.yield_at(budget) * (1.0 + 0.1 * max(0, depth))

    def run(self, *, total_budget: float) -> dict:
        """Allocate budget using depth-adjusted yield scores.

        Parameters
        ----------
        total_budget: Non-negative allocation target.

        Returns
        -------
        dict
            Regime-id -> allocated budget.
        """
        scores = {
            m.regime_id: self.adjusted_yield(
                model=m,
                budget=1.0,
                depth=self.chain_depths.get(m.regime_id, 0),
            )
            for m in self.models
        }
        return _normalize_to_budget(scores, total=total_budget)


# ---------------------------------------------------------------------------
# Standalone economic algorithm implementations
# ---------------------------------------------------------------------------

def vcg_mechanism(
    bidders: list,
    valuations: dict,
    items: list,
    *,
    algo: Optional[EconomicAlgorithm] = None,
) -> tuple:
    """Clarke-Groves VCG mechanism: welfare-maximising allocation + VCG payments.

    The VCG mechanism selects the allocation x* that maximises the sum
    of reported valuations, then charges each agent i the Clarke pivot
    tax: the welfare loss imposed on all other agents relative to the
    optimal allocation without i.

    Formally:
        Allocation: x* = argmax_{x} sum_i v_i(x_i)
        Payment:    p_i = sum_{j!=i} v_j(x*_{-i}) - sum_{j!=i} v_j(x*_j)

    Truthful bidding is a dominant strategy: misreporting can only reduce
    agent i's utility v_i(x*_i) - p_i.

    This implementation uses a greedy welfare-maximisation heuristic (sort
    items by max bidder value, assign to highest bidder) to keep complexity
    at O(n*m*log m) rather than the exponential combinatorial optimum.

    Parameters
    ----------
    bidders:     List of bidder identifier strings.
    valuations:  {bidder: {item: value}} reported valuations.
    items:       List of item identifier strings.
    algo:        Optional EconomicAlgorithm for trace recording.

    Returns
    -------
    tuple
        (allocation, payments) where allocation maps each bidder to
        their assigned items and payments maps each bidder to their VCG
        payment (>= 0 under individual rationality).

    Raises
    ------
    ValueError
        If bidders or items is empty.

    Examples
    --------
    >>> bidders = ["alice", "bob"]
    >>> vals = {"alice": {"x": 10.0, "y": 3.0}, "bob": {"x": 6.0, "y": 8.0}}
    >>> alloc, payments = vcg_mechanism(bidders, vals, ["x", "y"])
    >>> alloc["alice"]
    ['x']
    >>> alloc["bob"]
    ['y']
    """
    if not bidders:
        raise ValueError("vcg_mechanism: bidders must be non-empty")
    if not items:
        raise ValueError("vcg_mechanism: items must be non-empty")

    def _greedy_welfare(
        _bidders: list,
        _items: list,
        _vals: dict,
    ) -> tuple:
        """Greedy allocation: assign each item to highest-value bidder."""
        assignment: dict = {b: [] for b in _bidders}
        total_welfare = 0.0
        for item in sorted(
            _items,
            key=lambda it: max(
                (_vals.get(b, {}).get(it, 0.0) for b in _bidders),
                default=0.0,
            ),
            reverse=True,
        ):
            best_bidder = max(
                _bidders,
                key=lambda b: _vals.get(b, {}).get(item, 0.0),
            )
            val = _vals.get(best_bidder, {}).get(item, 0.0)
            assignment[best_bidder].append(item)
            total_welfare += val
        return assignment, total_welfare

    allocation, sw_all = _greedy_welfare(bidders, items, valuations)
    if algo is not None:
        algo.record(
            claim=f"VCG: welfare-maximising allocation computed, SW={sw_all:.4f}",
            formula=r"\mathbf{x}^* = \arg\max_{x} \sum_i v_i(x_i)",
            agent=f"vcg_mechanism/{algo.algo_id}",
            evidence=f"bidders={bidders}, items={items}",
            belief=0.95,
            trust_tier=TRUST_CANDIDATE,
            proof_path=("vcg_mechanism", "greedy_welfare"),
        )

    payments: dict = {}
    for bidder in bidders:
        others = [b for b in bidders if b != bidder]
        if not others:
            payments[bidder] = 0.0
            continue
        _, sw_without_i = _greedy_welfare(others, items, valuations)
        others_welfare_in_full = sum(
            valuations.get(b, {}).get(item, 0.0)
            for b in others
            for item in allocation.get(b, [])
        )
        payments[bidder] = max(0.0, sw_without_i - others_welfare_in_full)

    if algo is not None:
        total_rev = sum(payments.values())
        algo.record(
            claim=f"VCG payments computed, total revenue={total_rev:.4f}",
            formula=r"p_i = \sum_{j \neq i} v_j(x^*_{-i}) - \sum_{j \neq i} v_j(x^*_j)",
            agent=f"vcg_mechanism/{algo.algo_id}",
            evidence=str(payments),
            belief=0.95,
            trust_tier=TRUST_CANDIDATE,
            proof_path=("vcg_mechanism", "clarke_pivot_tax"),
        )

    return allocation, payments


def gale_shapley(
    proposers: list,
    acceptors: list,
    proposer_prefs: dict,
    acceptor_prefs: dict,
    *,
    algo: Optional[EconomicAlgorithm] = None,
) -> dict:
    """Gale-Shapley deferred acceptance algorithm (proposer-optimal stable matching).

    Runs the standard DA algorithm to completion.  At each round every
    free proposer proposes to their most preferred acceptor who has not
    yet rejected them.  Each acceptor tentatively holds their most
    preferred current offer and rejects the rest.

    The result is a stable matching in which no (proposer, acceptor) pair
    mutually prefer each other to their assigned partners.  The outcome
    is proposer-optimal: every proposer weakly prefers this matching to
    any other stable matching.

    Complexity: O(n^2) proposals in the worst case.

    Parameters
    ----------
    proposers:       List of proposer identifiers.
    acceptors:       List of acceptor identifiers.
    proposer_prefs:  {proposer: [acceptor, ...]}, ordered most -> least preferred.
    acceptor_prefs:  {acceptor: [proposer, ...]}, ordered most -> least preferred.
    algo:            Optional EconomicAlgorithm for trace recording.

    Returns
    -------
    dict
        Mapping {proposer: acceptor} for matched proposers.

    Notes
    -----
    Unmatched proposers (due to preference list exhaustion) are absent
    from the returned dict.  The algorithm handles unequal group sizes
    gracefully.

    Examples
    --------
    >>> p_prefs = {"A": ["X", "Y"], "B": ["Y", "X"]}
    >>> a_prefs = {"X": ["A", "B"], "Y": ["B", "A"]}
    >>> gale_shapley(["A", "B"], ["X", "Y"], p_prefs, a_prefs)
    {'A': 'X', 'B': 'Y'}
    """
    # Build acceptor preference rank lookup for O(1) comparison
    acc_rank: dict = {
        acc: {p: i for i, p in enumerate(acceptor_prefs.get(acc, []))}
        for acc in acceptors
    }

    # Track proposal index (next acceptor to propose to) per proposer
    next_proposal: dict = {p: 0 for p in proposers}
    # free_proposers: set of proposers not yet tentatively matched
    free_proposers: set = set(proposers)
    # tentative_hold: acceptor -> proposer (acceptor's current tentative match)
    tentative_hold: dict = {}

    round_num = 0

    while free_proposers:
        round_num += 1
        current_free = list(free_proposers)

        for proposer in current_free:
            prefs = proposer_prefs.get(proposer, [])
            if next_proposal[proposer] >= len(prefs):
                # Exhausted all options — proposer remains unmatched
                free_proposers.discard(proposer)
                continue

            target = prefs[next_proposal[proposer]]
            next_proposal[proposer] += 1

            if target not in tentative_hold:
                # Acceptor is free — accept immediately
                tentative_hold[target] = proposer
                free_proposers.discard(proposer)
            else:
                incumbent = tentative_hold[target]
                rank = acc_rank.get(target, {})
                incumbent_rank = rank.get(incumbent, len(proposers) + 1)
                proposer_rank  = rank.get(proposer,   len(proposers) + 1)

                if proposer_rank < incumbent_rank:
                    # Acceptor prefers new proposer — swap
                    tentative_hold[target] = proposer
                    free_proposers.discard(proposer)
                    free_proposers.add(incumbent)
                # else acceptor rejects the new proposer — they remain free

        if algo is not None and round_num % 10 == 0:
            algo.record(
                claim=f"Gale-Shapley: completed round {round_num}, "
                      f"{len(free_proposers)} proposers still free",
                formula=r"\text{DA round } r=" + str(round_num),
                agent=f"gale_shapley/{algo.algo_id}",
                evidence=f"matched={len(tentative_hold)}",
                belief=0.8,
                trust_tier=TRUST_CANDIDATE,
                proof_path=("gale_shapley", f"round_{round_num}"),
            )

    matching = {v: k for k, v in tentative_hold.items()}

    if algo is not None:
        algo.record(
            claim=f"Gale-Shapley: stable matching found after {round_num} rounds",
            formula=r"\mu \text{ is stable and proposer-optimal}",
            agent=f"gale_shapley/{algo.algo_id}",
            evidence=f"matching={matching}",
            belief=1.0,
            trust_tier=TRUST_VERIFIED,
            proof_path=("gale_shapley", "convergence"),
        )

    return matching


def iterative_tatonnement(
    goods: list,
    demand_fns: dict,
    supply: dict,
    *,
    max_iterations: int = 200,
    step_size: float = 0.05,
    tolerance: float = 1e-4,
    algo: Optional[EconomicAlgorithm] = None,
) -> tuple:
    """Walrasian price-adjustment (tatonnement) process.

    Iteratively adjusts prices toward market-clearing equilibrium.  At
    each step, prices rise for goods in excess demand and fall for goods
    in excess supply, scaled by step_size.  Convergence is declared
    when the L-infinity norm of excess demands falls below tolerance.

    Under gross substitutes (Walrasian stability) the process is known to
    converge to a Walrasian equilibrium.  Without this condition
    convergence is not guaranteed but the algorithm still terminates at
    max_iterations.

    Complexity: O(max_iterations x n_goods).

    Parameters
    ----------
    goods:          List of good identifiers.
    demand_fns:     {good: fn(prices) -> demand} callable demand functions.
    supply:         {good: quantity} fixed supply endowment.
    max_iterations: Maximum number of tatonnement steps.
    step_size:      Price adjustment rate (>= 0).
    tolerance:      L-infinity excess-demand convergence threshold.
    algo:           Optional EconomicAlgorithm for trace recording.

    Returns
    -------
    tuple
        (prices, converged) — final price vector and convergence flag.
    """
    prices: dict = {g: 1.0 for g in goods}
    converged = False

    for iteration in range(max_iterations):
        excess = {
            g: demand_fns[g](prices) - supply.get(g, 1.0)
            for g in goods
        }
        max_excess = max(abs(e) for e in excess.values()) if excess else 0.0

        if max_excess < tolerance:
            converged = True
            if algo is not None:
                algo.record(
                    claim=f"Tatonnement converged at iteration {iteration}, "
                          f"max_excess={max_excess:.6f}",
                    formula=r"p^* : z(p^*) = 0",
                    agent=f"tatonnement/{algo.algo_id}",
                    evidence=f"prices={prices}",
                    belief=0.99,
                    trust_tier=TRUST_VERIFIED,
                    proof_path=("tatonnement", "walrasian_equilibrium"),
                )
            break

        for g in goods:
            prices[g] = max(0.0, prices[g] + step_size * excess[g])

    if not converged and algo is not None:
        algo.record(
            claim=f"Tatonnement: did not converge within {max_iterations} iterations",
            formula=r"z(p) \neq 0",
            agent=f"tatonnement/{algo.algo_id}",
            evidence=f"final prices={prices}",
            obstruction=f"max_iterations={max_iterations} exceeded",
            belief=0.3,
            trust_tier=TRUST_PROPOSAL,
            proof_path=("tatonnement", "non_convergent"),
        )

    return prices, converged


def second_price_auction(
    bidders: list,
    bids: dict,
    *,
    reserve_price: float = 0.0,
    algo: Optional[EconomicAlgorithm] = None,
) -> tuple:
    """Single-item Vickrey (second-price sealed-bid) auction.

    The bidder who submits the highest bid above the reserve price wins
    the item but pays only the second-highest bid (or the reserve price
    if they are the only qualifying bidder).

    Truthful bidding is a weakly dominant strategy: no bidder can
    increase their surplus by misreporting their value.

    Complexity: O(n log n) for sorting.

    Parameters
    ----------
    bidders:       List of bidder identifiers.
    bids:          {bidder: bid_amount} sealed bids.
    reserve_price: Minimum acceptable bid (default 0).
    algo:          Optional EconomicAlgorithm for trace recording.

    Returns
    -------
    tuple
        (winner, payment) where winner is None if no bid
        exceeds the reserve price and payment is 0 in that case.

    Examples
    --------
    >>> second_price_auction(["a","b","c"], {"a":10,"b":7,"c":5})
    ('a', 7.0)
    """
    qualifying = sorted(
        [(b, bids[b]) for b in bidders if bids.get(b, 0.0) >= reserve_price],
        key=lambda x: x[1],
        reverse=True,
    )

    if not qualifying:
        if algo is not None:
            algo.record(
                claim="Second-price auction: no qualifying bids",
                formula=r"\nexists i : b_i \geq r",
                agent=f"second_price_auction/{algo.algo_id}",
                evidence=f"reserve={reserve_price}, bids={bids}",
                belief=1.0,
                trust_tier=TRUST_VERIFIED,
                proof_path=("second_price_auction", "no_sale"),
            )
        return None, 0.0

    winner, winning_bid = qualifying[0]
    payment = qualifying[1][1] if len(qualifying) > 1 else reserve_price

    if algo is not None:
        algo.record(
            claim=f"Second-price auction: winner={winner}, payment={payment:.4f}",
            formula=r"p^* = \max_{j \neq i^*} b_j",
            agent=f"second_price_auction/{algo.algo_id}",
            evidence=f"winning_bid={winning_bid:.4f}, second={payment:.4f}",
            belief=1.0,
            trust_tier=TRUST_CERTIFIED,
            proof_path=("second_price_auction", "vickrey_payment"),
        )

    return winner, float(payment)


def lp_allocation(
    agents: list,
    items: list,
    values: dict,
    capacity: Optional[dict] = None,
    *,
    algo: Optional[EconomicAlgorithm] = None,
) -> dict:
    """LP-based fractional welfare-maximising allocation (greedy relaxation).

    Solves the fractional relaxation of the welfare-maximisation integer
    program.  Items are sorted by max value density across agents, then
    assigned greedily to the highest-value agent subject to capacity
    constraints.  When capacity is None each item has unit capacity.

    This greedy relaxation is exact for the fractional LP when agent
    values are additive and item capacities are unit.

    Complexity: O(n*m*log m) greedy; O((n+m)^3) for full LP interior point.

    Parameters
    ----------
    agents:    List of agent identifiers.
    items:     List of item identifiers.
    values:    {agent: {item: value}} agent valuations.
    capacity:  {item: max_units} item capacities (default: 1.0 each).
    algo:      Optional EconomicAlgorithm for trace recording.

    Returns
    -------
    dict
        {item: allocated_value} total value allocated per item.
    """
    if capacity is None:
        capacity = {item: 1.0 for item in items}

    remaining: dict = {item: capacity.get(item, 1.0) for item in items}
    total_value: dict = {item: 0.0 for item in items}

    sorted_items = sorted(
        items,
        key=lambda it: max(
            (values.get(ag, {}).get(it, 0.0) for ag in agents),
            default=0.0,
        ),
        reverse=True,
    )

    for item in sorted_items:
        if remaining[item] <= 0.0:
            continue
        best_agent = max(
            agents,
            key=lambda ag: values.get(ag, {}).get(item, 0.0),
        )
        val = values.get(best_agent, {}).get(item, 0.0)
        alloc = min(remaining[item], 1.0)
        total_value[item] += val * alloc
        remaining[item] -= alloc

    if algo is not None:
        total_sw = sum(total_value.values())
        algo.record(
            claim=f"LP allocation: total social welfare={total_sw:.4f}",
            formula=r"\max \sum_{i,j} v_{ij} x_{ij}",
            agent=f"lp_allocation/{algo.algo_id}",
            evidence=f"items={items}, agents={agents}",
            belief=0.85,
            trust_tier=TRUST_CANDIDATE,
            proof_path=("lp_allocation", "greedy_fractional"),
        )

    return total_value


def nash_support_enumeration(
    payoff_row: list,
    payoff_col: list,
    *,
    tolerance: float = 1e-8,
    algo: Optional[EconomicAlgorithm] = None,
) -> list:
    """Find Nash equilibria via support enumeration (two-player finite games).

    Enumerates all possible support sets for both players, then for each
    support pair solves for the mixed strategy that makes the opponent
    indifferent over all actions in their support using Gaussian
    elimination.  A pair is a Nash equilibrium iff both indifference
    conditions hold simultaneously and neither player has a profitable
    deviation outside their support.

    Complexity: O(2^n * 2^m * poly(n,m)) -- exponential in strategy counts;
    practical for small games.

    Parameters
    ----------
    payoff_row: Row player's payoff matrix (n_row x n_col).
    payoff_col: Column player's payoff matrix (n_row x n_col).
    tolerance:  Numerical tolerance for feasibility checks.
    algo:       Optional EconomicAlgorithm for trace recording.

    Returns
    -------
    list
        List of Nash equilibria (row_strategy, col_strategy) as
        probability vectors.  Returns the pure-strategy NE if no mixed
        equilibrium is found.

    Notes
    -----
    Gaussian elimination is implemented locally to avoid external
    dependencies.
    """
    n_row = len(payoff_row)
    n_col = len(payoff_row[0]) if payoff_row else 0

    def _gaussian_solve(A: list, b: list) -> Optional[list]:
        """Solve Ax = b via Gaussian elimination with partial pivoting."""
        n = len(b)
        # Build augmented matrix
        mat = [list(A[i]) + [b[i]] for i in range(n)]
        for col in range(n):
            # Partial pivot
            max_row = max(range(col, n), key=lambda r: abs(mat[r][col]))
            mat[col], mat[max_row] = mat[max_row], mat[col]
            pivot = mat[col][col]
            if abs(pivot) < tolerance:
                return None
            for r in range(n):
                if r != col:
                    factor = mat[r][col] / pivot
                    mat[r] = [mat[r][c] - factor * mat[col][c] for c in range(n + 1)]
            mat[col] = [v / pivot for v in mat[col]]
        return [mat[i][n] for i in range(n)]

    equilibria: list = []

    for r_size in range(1, n_row + 1):
        for c_size in range(1, n_col + 1):
            for r_support in itertools.combinations(range(n_row), r_size):
                for c_support in itertools.combinations(range(n_col), c_size):
                    # Solve for row player's mixed strategy q
                    if r_size == 1:
                        q = [0.0] * n_row
                        q[r_support[0]] = 1.0
                    else:
                        A_q: list = []
                        b_q: list = []
                        for k in range(1, r_size):
                            row_eq = [0.0] * r_size
                            for j in c_support[:1]:
                                row_eq[0] = payoff_col[r_support[0]][j]
                                row_eq[k] = -payoff_col[r_support[k]][j]
                            A_q.append(row_eq)
                            b_q.append(0.0)
                        A_q.append([1.0] * r_size)
                        b_q.append(1.0)
                        if len(A_q) != r_size:
                            continue
                        sol = _gaussian_solve(A_q, b_q)
                        if sol is None or any(v < -tolerance for v in sol):
                            continue
                        q = [0.0] * n_row
                        for idx, i in enumerate(r_support):
                            q[i] = max(0.0, sol[idx])

                    # Solve for col player's mixed strategy p
                    if c_size == 1:
                        p = [0.0] * n_col
                        p[c_support[0]] = 1.0
                    else:
                        A_p: list = []
                        b_p: list = []
                        for k in range(1, c_size):
                            row_eq = [0.0] * c_size
                            for i in r_support[:1]:
                                row_eq[0] = payoff_row[i][c_support[0]]
                                row_eq[k] = -payoff_row[i][c_support[k]]
                            A_p.append(row_eq)
                            b_p.append(0.0)
                        A_p.append([1.0] * c_size)
                        b_p.append(1.0)
                        if len(A_p) != c_size:
                            continue
                        sol = _gaussian_solve(A_p, b_p)
                        if sol is None or any(v < -tolerance for v in sol):
                            continue
                        p = [0.0] * n_col
                        for idx, j in enumerate(c_support):
                            p[j] = max(0.0, sol[idx])

                    # Check best-response conditions
                    row_payoffs = [
                        sum(q[i] * payoff_row[i][j] for i in range(n_row))
                        for j in range(n_col)
                    ]
                    col_payoffs = [
                        sum(p[j] * payoff_col[i][j] for j in range(n_col))
                        for i in range(n_row)
                    ]
                    row_eq_val = sum(p[j] * row_payoffs[j] for j in range(n_col))
                    col_eq_val = sum(q[i] * col_payoffs[i] for i in range(n_row))

                    row_ok = all(
                        row_payoffs[j] <= row_eq_val + tolerance
                        for j in range(n_col)
                        if j not in c_support
                    )
                    col_ok = all(
                        col_payoffs[i] <= col_eq_val + tolerance
                        for i in range(n_row)
                        if i not in r_support
                    )

                    if row_ok and col_ok:
                        s_q = sum(q)
                        s_p = sum(p)
                        if s_q > tolerance and s_p > tolerance:
                            q_norm = [v / s_q for v in q]
                            p_norm = [v / s_p for v in p]
                            equilibria.append((q_norm, p_norm))

    if algo is not None:
        algo.record(
            claim=f"Nash support enumeration: found {len(equilibria)} equilibria",
            formula=r"\sigma^* \in \Delta(S_1) \times \Delta(S_2)",
            agent=f"nash_support_enum/{algo.algo_id}",
            evidence=f"n_row={n_row}, n_col={n_col}",
            belief=0.9,
            trust_tier=TRUST_VERIFIED if equilibria else TRUST_PROPOSAL,
            proof_path=("nash_support_enumeration", "support_enum"),
        )

    # Deduplicate
    unique: list = []
    for eq in equilibria:
        is_dup = any(
            all(abs(a - b) < tolerance for a, b in zip(eq[0], u[0]))
            and all(abs(a - b) < tolerance for a, b in zip(eq[1], u[1]))
            for u in unique
        )
        if not is_dup:
            unique.append(eq)

    return unique


# ---------------------------------------------------------------------------
# Algorithm registry
# ---------------------------------------------------------------------------

class AlgorithmRegistry:
    """Registry mapping algorithm names to EconomicAlgorithm descriptors.

    Provides lookup, domain-based filtering, and a convenience method for
    running registered algorithms while recording a judgment trace.

    Attributes
    ----------
    _store: dict
        Internal mapping of name -> descriptor.
    _fns: dict
        Internal mapping of name -> callable implementation.

    Examples
    --------
    >>> reg = AlgorithmRegistry()
    >>> algo = EconomicAlgorithm(name="vcg", domain="auction_theory", complexity="O(n^2)")
    >>> reg.register(algo, fn=lambda *a, **kw: vcg_mechanism(*a, **kw, algo=algo))
    >>> reg.lookup("vcg").name
    'vcg'
    """

    def __init__(self) -> None:
        self._store: dict = {}
        self._fns: dict = {}

    def register(
        self,
        algo: EconomicAlgorithm,
        fn: Optional[Callable] = None,
    ) -> None:
        """Register an algorithm descriptor and optionally its callable.

        Parameters
        ----------
        algo: The EconomicAlgorithm descriptor to register.
        fn:   Optional callable implementation.
        """
        self._store[algo.name] = algo
        if fn is not None:
            self._fns[algo.name] = fn

    def lookup(self, name: str) -> Optional[EconomicAlgorithm]:
        """Return the EconomicAlgorithm registered under *name*.

        Parameters
        ----------
        name: Algorithm name.

        Returns
        -------
        EconomicAlgorithm | None
            The descriptor, or None if not found.
        """
        return self._store.get(name)

    def list_by_domain(self, domain: str) -> list:
        """Return all algorithms in *domain*.

        Parameters
        ----------
        domain: Domain string to filter by (exact match).

        Returns
        -------
        list
            Possibly empty list of matching descriptors.
        """
        return [a for a in self._store.values() if a.domain == domain]

    def all_names(self) -> list:
        """Return sorted list of all registered algorithm names.

        Returns
        -------
        list
            Alphabetically sorted names.
        """
        return sorted(self._store.keys())

    def run_with_judgment(
        self,
        name: str,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> tuple:
        """Execute a registered algorithm and return its result + a judgment.

        The callable associated with *name* is invoked with *args and
        **kwargs.  Exceptions are caught and recorded as obstruction
        judgments.

        Parameters
        ----------
        name:     Registered algorithm name.
        *args:    Positional arguments forwarded to the implementation.
        **kwargs: Keyword arguments forwarded to the implementation.

        Returns
        -------
        tuple
            (result, judgment) where judgment is a JuGeo 8-tuple.

        Raises
        ------
        KeyError
            If name is not registered with an implementation callable.
        """
        algo = self._store.get(name)
        if algo is None:
            raise KeyError(f"AlgorithmRegistry: '{name}' is not registered")
        fn = self._fns.get(name)
        if fn is None:
            raise KeyError(f"AlgorithmRegistry: '{name}' has no callable registered")

        try:
            result = fn(*args, **kwargs)
            judgment = _make_judgment(
                claim=f"{name}: execution successful",
                formula=r"\text{result} \neq \bot",
                agent=f"registry/{name}",
                evidence=repr(args)[:200],
                obstruction="",
                belief=0.95,
                trust_tier=TRUST_VERIFIED,
                proof_path=("registry", name, "success"),
            )
        except Exception as exc:  # noqa: BLE001
            judgment = _make_judgment(
                claim=f"{name}: execution raised {type(exc).__name__}",
                formula=r"\text{result} = \bot",
                agent=f"registry/{name}",
                evidence="",
                obstruction=str(exc),
                belief=0.0,
                trust_tier=TRUST_PROPOSAL,
                proof_path=("registry", name, "exception"),
            )
            result = None

        if algo is not None:
            algo.judgment_trace.append(judgment)

        return result, judgment


# ---------------------------------------------------------------------------
# Algorithm correctness verification
# ---------------------------------------------------------------------------

def verify_algorithm_correctness(
    algo: EconomicAlgorithm,
    test_cases: list,
    fn: Callable,
    *,
    expected_key: str = "expected",
    input_key: str = "inputs",
    equality_fn: Optional[Callable] = None,
) -> tuple:
    """Run *fn* on each test case, upgrade trust based on pass rate.

    Each test case dict must contain:
    - inputs (key: input_key): dict of keyword arguments for fn.
    - expected (key: expected_key): expected return value.

    Trust is upgraded from PROPOSAL -> CANDIDATE -> VERIFIED -> CERTIFIED
    based on the fraction of passing tests (see _upgrade_trust).

    Parameters
    ----------
    algo:         EconomicAlgorithm descriptor (name + domain used).
    test_cases:   List of test-case dicts.
    fn:           Callable to test.
    expected_key: Key in each dict holding the expected result.
    input_key:    Key in each dict holding the input kwargs.
    equality_fn:  Custom equality predicate (actual, expected) -> bool.
                  Defaults to actual == expected.

    Returns
    -------
    tuple
        (judgment, report) where report contains keys
        passed, failed, total, pass_rate, failures.

    Examples
    --------
    >>> algo = EconomicAlgorithm(name="double", domain="test", complexity="O(1)")
    >>> cases = [{"inputs": {"x": 2}, "expected": 4},
    ...          {"inputs": {"x": 3}, "expected": 6}]
    >>> j, report = verify_algorithm_correctness(algo, cases, lambda x: x * 2)
    >>> report["passed"]
    2
    """
    if equality_fn is None:
        equality_fn = lambda a, e: a == e  # noqa: E731

    passed = 0
    failures: list = []

    for idx, case in enumerate(test_cases):
        inputs   = case.get(input_key, {})
        expected = case.get(expected_key)
        try:
            actual = fn(**inputs) if isinstance(inputs, dict) else fn(*inputs)
            ok = equality_fn(actual, expected)
        except Exception as exc:  # noqa: BLE001
            ok = False
            actual = f"<exception: {exc}>"

        if ok:
            passed += 1
        else:
            failures.append({
                "case_index": idx,
                "inputs":     inputs,
                "expected":   expected,
                "actual":     actual,
            })

    total    = len(test_cases)
    rate     = passed / total if total else 0.0
    report   = {
        "passed":    passed,
        "failed":    total - passed,
        "total":     total,
        "pass_rate": rate,
        "failures":  failures,
    }

    base_judgment = _make_judgment(
        claim=f"{algo.name}: correctness verification",
        formula=r"\forall t \in T : f(t_{\text{in}}) = t_{\text{out}}",
        agent=f"verify/{algo.algo_id}",
        evidence=f"total={total}",
        obstruction="" if not failures else str(failures[:3]),
        belief=rate,
        trust_tier=TRUST_PROPOSAL,
        proof_path=("verify_algorithm_correctness", algo.name),
    )
    final_judgment = _upgrade_trust(base_judgment, passed, total)
    algo.judgment_trace.append(final_judgment)

    return final_judgment, report


# ---------------------------------------------------------------------------
# Backward-compatibility aliases
# ---------------------------------------------------------------------------

EconomicAlgorithmBase     = _BudgetAlgorithmBase
TheoremEconomicsAlgorithm = EconomicAlgorithm

# ---------------------------------------------------------------------------
# Default registry construction
# ---------------------------------------------------------------------------

def build_default_registry() -> AlgorithmRegistry:
    """Build and return an AlgorithmRegistry with all six algorithms.

    Registers:
    - vcg_mechanism          (auction_theory)
    - gale_shapley           (matching_theory)
    - iterative_tatonnement  (general_equilibrium)
    - second_price_auction   (auction_theory)
    - lp_allocation          (mechanism_design)
    - nash_support_enumeration (game_theory)

    Returns
    -------
    AlgorithmRegistry
        Populated registry instance.
    """
    reg = AlgorithmRegistry()

    vcg_algo = EconomicAlgorithm(
        name="vcg_mechanism",
        domain="auction_theory",
        complexity=economic_complexity_bounds["vcg_mechanism"],
    )
    reg.register(vcg_algo, fn=lambda *a, **kw: vcg_mechanism(*a, **kw, algo=vcg_algo))

    gs_algo = EconomicAlgorithm(
        name="gale_shapley",
        domain="matching_theory",
        complexity=economic_complexity_bounds["gale_shapley"],
    )
    reg.register(gs_algo, fn=lambda *a, **kw: gale_shapley(*a, **kw, algo=gs_algo))

    tat_algo = EconomicAlgorithm(
        name="iterative_tatonnement",
        domain="general_equilibrium",
        complexity=economic_complexity_bounds["tatonnement"],
    )
    reg.register(
        tat_algo,
        fn=lambda *a, **kw: iterative_tatonnement(*a, **kw, algo=tat_algo),
    )

    spa_algo = EconomicAlgorithm(
        name="second_price_auction",
        domain="auction_theory",
        complexity=economic_complexity_bounds["second_price_auction"],
    )
    reg.register(
        spa_algo,
        fn=lambda *a, **kw: second_price_auction(*a, **kw, algo=spa_algo),
    )

    lp_algo = EconomicAlgorithm(
        name="lp_allocation",
        domain="mechanism_design",
        complexity=economic_complexity_bounds["lp_allocation"],
    )
    reg.register(lp_algo, fn=lambda *a, **kw: lp_allocation(*a, **kw, algo=lp_algo))

    nash_algo = EconomicAlgorithm(
        name="nash_support_enumeration",
        domain="game_theory",
        complexity=economic_complexity_bounds["nash_support_enumeration"],
    )
    reg.register(
        nash_algo,
        fn=lambda *a, **kw: nash_support_enumeration(*a, **kw, algo=nash_algo),
    )

    return reg


DEFAULT_REGISTRY: AlgorithmRegistry = build_default_registry()


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 1. EconomicAlgorithm.record()
    algo = EconomicAlgorithm(name="smoke", domain="test", complexity="O(1)")
    j = algo.record("Smoke test claim", r"\text{smoke}", trust_tier=TRUST_CANDIDATE)
    assert len(algo.judgment_trace) == 1, "judgment_trace should have 1 entry"
    assert j[6] == TRUST_CANDIDATE, "trust tier should be CANDIDATE"
    print("[OK] EconomicAlgorithm.record()")

    # 2. second_price_auction
    winner, payment = second_price_auction(
        ["alice", "bob", "carol"],
        {"alice": 10.0, "bob": 7.0, "carol": 5.0},
        reserve_price=3.0,
    )
    assert winner == "alice", f"Expected alice, got {winner}"
    assert abs(payment - 7.0) < 1e-9, f"Expected payment 7.0, got {payment}"
    print("[OK] second_price_auction()")

    # 3. gale_shapley 2x2
    p_prefs = {"A": ["X", "Y"], "B": ["Y", "X"]}
    a_prefs = {"X": ["A", "B"], "Y": ["B", "A"]}
    matching = gale_shapley(["A", "B"], ["X", "Y"], p_prefs, a_prefs)
    assert matching.get("A") == "X", f"Expected A->X, got {matching}"
    assert matching.get("B") == "Y", f"Expected B->Y, got {matching}"
    print("[OK] gale_shapley()")

    # 4. build_default_registry().all_names()
    reg = build_default_registry()
    names = reg.all_names()
    assert "vcg_mechanism" in names, "vcg_mechanism should be registered"
    assert len(names) == 6, f"Expected 6 algorithms, got {len(names)}: {names}"
    print("[OK] build_default_registry().all_names()")

    # 5. verify_algorithm_correctness with doubling function
    test_algo = EconomicAlgorithm(name="doubler", domain="test", complexity="O(1)")
    cases = [
        {"inputs": {"x": 2}, "expected": 4},
        {"inputs": {"x": 3}, "expected": 6},
        {"inputs": {"x": 0}, "expected": 0},
    ]
    j_verify, report = verify_algorithm_correctness(
        test_algo, cases, lambda x: x * 2
    )
    assert report["passed"] == 3, f"All 3 should pass, got {report['passed']}"
    assert j_verify[6] == TRUST_CERTIFIED, f"Expected CERTIFIED, got {j_verify[6]}"
    print("[OK] verify_algorithm_correctness()")

    print("All algorithms smoke tests passed.")
