from __future__ import annotations

import math
from collections import deque, defaultdict
from dataclasses import dataclass, field

from .models import CompoundingEffect, TheoremYieldModel


def _dfs_depth(root: str, adjacency: dict[str, list[str]]) -> int:
    children = adjacency.get(root, [])
    if not children:
        return 0
    return 1 + max(_dfs_depth(child, adjacency) for child in children)


def _bfs_all_derived(root: str, adjacency: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    queue: deque[str] = deque(adjacency.get(root, []))
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        queue.extend(adjacency.get(node, []))
    return seen


@dataclass(frozen=True)
class CompoundingFactor:
    base_factor: float
    depth: int

    def effective_factor(self) -> float:
        if self.depth <= 0:
            return 1.0
        return self.base_factor ** self.depth

    def is_superlinear(self) -> bool:
        return self.base_factor > 1.0 and self.depth > 0


@dataclass
class TheoremChainTracer:
    _adjacency: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    _parents: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_theorem(self, theorem_id: str) -> None:
        self._adjacency.setdefault(theorem_id, [])
        self._parents.setdefault(theorem_id, set())

    def add_dependency(self, parent: str, child: str) -> None:
        self.add_theorem(parent)
        self.add_theorem(child)
        if child not in self._adjacency[parent]:
            self._adjacency[parent].append(child)
        self._parents[child].add(parent)

    def chain_depth(self, theorem_id: str) -> int:
        return _dfs_depth(theorem_id, self._adjacency)

    def all_derived(self, theorem_id: str) -> set[str]:
        return _bfs_all_derived(theorem_id, self._adjacency)

    def roots(self) -> set[str]:
        return {node for node, parents in self._parents.items() if not parents}

    def all_theorems(self) -> set[str]:
        return set(self._adjacency.keys())


@dataclass
class CompoundingModel:
    yield_models: list[TheoremYieldModel]
    base_compounding_rate: float = 0.1

    def _base_model(self) -> TheoremYieldModel:
        return self.yield_models[0]

    def compute_compounding(self, theorem_id: str, tracer: TheoremChainTracer, budget: float) -> CompoundingEffect:
        base_model = self._base_model()
        base_yield = base_model.yield_at(budget)
        derived = tracer.all_derived(theorem_id)
        depth = tracer.chain_depth(theorem_id)
        return CompoundingEffect(
            base_theorem_id=theorem_id,
            base_yield=base_yield,
            derived_theorems=len(derived),
            chain_depth=depth,
            compounding_factor=1.0 + self.base_compounding_rate,
        )

    def total_portfolio_yield(self, *, tracer: TheoremChainTracer, theorem_budgets: dict[str, float]) -> float:
        return sum(self.compute_compounding(tid, tracer, budget).total_yield() for tid, budget in theorem_budgets.items())

    def marginal_theorem_value(self, theorem_id: str, *, tracer: TheoremChainTracer, budget: float) -> float:
        return self.compute_compounding(theorem_id, tracer, budget).total_yield()


class CompoundInterestAnalogy:
    def future_value(self, principal: float, rate: float, periods: int) -> float:
        return principal * ((1.0 + rate) ** periods)

    def present_value(self, future_value: float, rate: float, periods: int) -> float:
        return future_value / ((1.0 + rate) ** periods)

    def doubling_time(self, rate: float) -> float:
        if rate <= 0.0:
            return math.inf
        return math.log(2.0) / math.log(1.0 + rate)


@dataclass
class CompoundingPortfolioAnalyzer:
    compounding_model: CompoundingModel

    def compounding_index(self, tracer: TheoremChainTracer, theorem_ids: list[str]) -> float:
        if not theorem_ids:
            return 1.0
        depths = [tracer.chain_depth(tid) for tid in theorem_ids if tid in tracer.all_theorems()]
        if not depths:
            return 1.0
        return 1.0 + self.compounding_model.base_compounding_rate * (sum(depths) / len(depths))
