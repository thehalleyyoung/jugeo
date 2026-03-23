from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _saturating_yield(*, budget: float, saturation_yield: float, growth_rate: float) -> float:
    budget = max(0.0, float(budget))
    saturation_yield = max(0.0, float(saturation_yield))
    growth_rate = max(0.0, float(growth_rate))
    return saturation_yield * (1.0 - math.exp(-growth_rate * budget))


def _marginal_saturating_yield(*, budget: float, saturation_yield: float, growth_rate: float) -> float:
    budget = max(0.0, float(budget))
    saturation_yield = max(0.0, float(saturation_yield))
    growth_rate = max(0.0, float(growth_rate))
    return saturation_yield * growth_rate * math.exp(-growth_rate * budget)


def _fit_saturating_model(data: list[tuple[float, float]]) -> tuple[float, float]:
    if not data:
        return 1.0, 0.1
    saturation = max(y for _, y in data)
    positive = [(b, y) for b, y in data if b > 0.0 and y > 0.0]
    if not positive or saturation <= 0.0:
        return max(1.0, saturation), 0.1
    estimates = []
    for budget, value in positive:
        ratio = max(1e-9, 1.0 - min(0.999999, value / max(saturation, 1e-9)))
        estimates.append(-math.log(ratio) / budget)
    growth = sum(estimates) / len(estimates)
    return max(1.0, saturation), max(1e-6, growth)


@dataclass
class TheoremYieldModel:
    model_id: str
    regime_id: str
    saturation_yield: float
    growth_rate: float
    current_budget: float = 0.0
    empirical_data: list[tuple[float, float]] = field(default_factory=list)

    def yield_at(self, budget: float) -> float:
        return _saturating_yield(budget=budget, saturation_yield=self.saturation_yield, growth_rate=self.growth_rate)

    def marginal_yield(self, budget: float) -> float:
        return _marginal_saturating_yield(budget=budget, saturation_yield=self.saturation_yield, growth_rate=self.growth_rate)

    def calibrate(self, data: list[tuple[float, float]]) -> None:
        self.saturation_yield, self.growth_rate = _fit_saturating_model(data)
        self.empirical_data.extend(data)

    def optimal_budget(self) -> float:
        return 1.0 / max(self.growth_rate, 1e-9)

    def forecast(self, max_budget: float, steps: int) -> list[tuple[float, float]]:
        if steps <= 0:
            return []
        if steps == 1:
            return [(max(0.0, max_budget), self.yield_at(max_budget))]
        return [
            (max_budget * i / (steps - 1), self.yield_at(max_budget * i / (steps - 1)))
            for i in range(steps)
        ]


@dataclass(frozen=True)
class MarginalValue:
    regime_id: str
    budget_points: list[float]
    marginal_yields: list[float]

    def is_decreasing(self) -> bool:
        return all(a >= b for a, b in zip(self.marginal_yields, self.marginal_yields[1:]))


@dataclass
class InvestmentSchedule:
    schedule_id: str
    regime_ids: list[str]
    allocations: dict[str, float]
    total_budget: float
    expected_yield: float

    def reallocate(self, new_allocs: dict[str, float]) -> None:
        self.allocations = dict(new_allocs)
        self.regime_ids = list(new_allocs.keys())
        self.total_budget = sum(new_allocs.values())

    def efficiency_ratio(self) -> float:
        return 0.0 if self.total_budget <= 0.0 else self.expected_yield / self.total_budget

    def allocation_fractions(self) -> dict[str, float]:
        total = sum(self.allocations.values())
        if total <= 0.0:
            return {rid: 0.0 for rid in self.regime_ids}
        return {rid: self.allocations.get(rid, 0.0) / total for rid in self.regime_ids}


@dataclass
class CompoundingEffect:
    base_theorem_id: str
    base_yield: float
    derived_theorems: int
    chain_depth: int
    compounding_factor: float = 1.0

    def total_yield(self) -> float:
        depth = max(self.chain_depth, self.derived_theorems)
        return self.base_yield * (self.compounding_factor ** max(0, depth))

    def add_derived(self, theorem_id: str) -> None:
        self.derived_theorems += 1
        self.chain_depth = max(self.chain_depth, self.derived_theorems)


@dataclass
class TheoremPortfolioValue:
    portfolio_id: str
    theorem_ids: list[str]
    yields: dict[str, float]
    discount_rate: float
    time_horizon: float

    def present_value(self) -> float:
        total = sum(max(0.0, self.yields.get(tid, 0.0)) for tid in self.theorem_ids)
        return total / ((1.0 + max(0.0, self.discount_rate)) ** max(0.0, self.time_horizon))

    def add_theorem(self, theorem_id: str, *, yield_value: float) -> None:
        if theorem_id not in self.theorem_ids:
            self.theorem_ids.append(theorem_id)
        self.yields[theorem_id] = max(0.0, float(yield_value))


@dataclass
class RegimeEconomics:
    regime_id: str
    yield_model: TheoremYieldModel
    novelty_premium: float
    exploration_cost: float
    regime_weight: float

    def adjusted_yield(self, budget: float, novelty_score: float = 0.0) -> float:
        premium = 1.0 + max(0.0, self.novelty_premium) * max(0.0, float(novelty_score))
        return self.yield_model.yield_at(budget) * premium


@dataclass
class BudgetAllocation:
    allocation_id: str
    regime_ids: list[str]
    amounts: dict[str, float]
    total_budget: float

    def fractions(self) -> dict[str, float]:
        if self.total_budget <= 0.0:
            return {rid: 0.0 for rid in self.regime_ids}
        return {rid: self.amounts.get(rid, 0.0) / self.total_budget for rid in self.regime_ids}


@dataclass(frozen=True)
class YieldForecast:
    model_id: str
    budget_points: list[float]
    yield_points: list[float]
    confidence: float
    forecast_horizon: float

    def is_reliable(self) -> bool:
        return self.confidence >= 0.7


@dataclass(frozen=True)
class EconomicEquilibrium:
    equilibrium_id: str
    regime_ids: list[str]
    marginal_values: dict[str, float]
    allocations: dict[str, float]
    total_budget: float

    def max_marginal_deviation(self) -> float:
        values = list(self.marginal_values.values())
        if not values:
            return 0.0
        return max(values) - min(values)


@dataclass
class LinearYieldModel:
    model_id: str
    regime_id: str
    slope: float
    current_budget: float = 0.0

    def yield_at(self, budget: float) -> float:
        return max(0.0, self.slope * max(0.0, budget))

    def marginal_yield(self, budget: float) -> float:
        return max(0.0, self.slope)


__all__ = [
    '_saturating_yield',
    'TheoremYieldModel',
    'MarginalValue',
    'InvestmentSchedule',
    'CompoundingEffect',
    'TheoremPortfolioValue',
    'RegimeEconomics',
    'BudgetAllocation',
    'YieldForecast',
    'EconomicEquilibrium',
    'LinearYieldModel',
]
