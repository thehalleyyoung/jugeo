"""Budget allocation helpers for semantic-futures workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

__all__ = [
    "BudgetAllocator",
    "BudgetConstraint",
    "CostEstimator",
    "BudgetTracker",
    "AllocationStrategy",
]


class AllocationStrategy(str, Enum):
    GREEDY = "greedy"
    EVEN = "even"
    PRIORITY = "priority"


@dataclass(frozen=True)
class BudgetConstraint:
    total_budget: float
    spent_budget: float = 0.0

    @property
    def remaining_budget(self) -> float:
        return max(0.0, float(self.total_budget) - float(self.spent_budget))

    def can_afford(self, amount: float) -> bool:
        return float(amount) <= self.remaining_budget


@dataclass
class BudgetTracker:
    total_budget: float
    spent_budget: float = 0.0

    @property
    def remaining_budget(self) -> float:
        return max(0.0, float(self.total_budget) - float(self.spent_budget))

    def spend(self, amount: float) -> float:
        amount = max(0.0, float(amount))
        self.spent_budget = min(float(self.total_budget), float(self.spent_budget) + amount)
        return self.remaining_budget


class CostEstimator:
    def estimate(self, item: Any) -> float:
        if hasattr(item, "cost_estimate"):
            return max(0.0, float(getattr(item, "cost_estimate")))
        if hasattr(item, "expected_cost"):
            return max(0.0, float(getattr(item, "expected_cost")))
        return 0.0


class BudgetAllocator:
    def __init__(self, strategy: AllocationStrategy | str = AllocationStrategy.GREEDY, estimator: CostEstimator | None = None) -> None:
        self.strategy = AllocationStrategy(str(strategy)) if not isinstance(strategy, AllocationStrategy) else strategy
        self.estimator = estimator or CostEstimator()

    def allocate(self, items: Iterable[Any], budget: float) -> list[Any]:
        remaining = max(0.0, float(budget))
        selected: list[Any] = []
        for item in items:
            cost = self.estimator.estimate(item)
            if cost <= remaining:
                selected.append(item)
                remaining -= cost
        return selected
