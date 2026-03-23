from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import MarginalValue, TheoremYieldModel


def _bisect(fn: Callable[[float], float], *, lo: float, hi: float, tol: float = 1e-6, max_iter: int = 200) -> float:
    f_lo = fn(lo)
    f_hi = fn(hi)
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = fn(mid)
        if abs(f_mid) <= tol or abs(hi - lo) <= tol:
            return mid
        if f_lo == 0:
            return lo
        if f_hi == 0:
            return hi
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def _normalize_allocations(raw: dict[str, float], *, total: float) -> dict[str, float]:
    if not raw:
        return {}
    total = max(0.0, total)
    clipped = {k: max(0.0, float(v)) for k, v in raw.items()}
    denom = sum(clipped.values())
    if denom <= 0.0:
        share = total / len(clipped)
        return {k: share for k in clipped}
    return {k: total * v / denom for k, v in clipped.items()}


@dataclass(frozen=True)
class MarginalValueCurve:
    model: TheoremYieldModel
    budget_range: tuple[float, float]
    resolution: int = 10

    def evaluate(self) -> list[tuple[float, float]]:
        lo, hi = self.budget_range
        if self.resolution <= 1:
            budgets = [lo]
        else:
            budgets = [lo + (hi - lo) * i / (self.resolution - 1) for i in range(self.resolution)]
        return [(b, self.model.marginal_yield(b)) for b in budgets]

    def at_budget(self, budget: float) -> float:
        return self.model.marginal_yield(budget)

    def diminishing_returns_onset(self) -> float:
        threshold = self.model.marginal_yield(self.budget_range[0]) * 0.5
        for budget, value in self.evaluate():
            if value <= threshold:
                return budget
        return self.budget_range[1]


@dataclass
class EquimarginalPrinciple:
    models: list[TheoremYieldModel]

    def optimal_allocation(self, *, total_budget: float) -> dict[str, float]:
        weights = {m.regime_id: max(1e-9, m.saturation_yield * m.growth_rate) for m in self.models}
        return _normalize_allocations(weights, total=total_budget)

    def is_equimarginal(self, allocs: dict[str, float], *, tolerance: float = 0.1) -> bool:
        marginals = [m.marginal_yield(allocs.get(m.regime_id, 0.0)) for m in self.models]
        return max(marginals, default=0.0) - min(marginals, default=0.0) <= tolerance


@dataclass
class MarginalAnalyzer:
    models: list[TheoremYieldModel]

    def _model(self, regime_id: str) -> TheoremYieldModel:
        for model in self.models:
            if model.regime_id == regime_id:
                return model
        raise KeyError(regime_id)

    def compute_marginal(self, *, regime_id: str, at_budget: float) -> MarginalValue:
        model = self._model(regime_id)
        return MarginalValue(regime_id=regime_id, budget_points=[at_budget], marginal_yields=[model.marginal_yield(at_budget)])

    def marginal_schedule(self, *, regime_id: str, budgets: list[float]) -> list[float]:
        model = self._model(regime_id)
        return [model.marginal_yield(budget) for budget in budgets]

    def equimarginal_allocation(self, *, total_budget: float) -> dict[str, float]:
        return EquimarginalPrinciple(self.models).optimal_allocation(total_budget=total_budget)

    def rank_by_marginal(self, *, at_budget: float) -> list[tuple[str, float]]:
        ranked = [(m.regime_id, m.marginal_yield(at_budget)) for m in self.models]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked


@dataclass(frozen=True)
class MarginalReturnDiminishment:
    model: TheoremYieldModel

    def saturation_index(self, *, budget: float) -> float:
        return min(1.0, self.model.yield_at(budget) / max(self.model.saturation_yield, 1e-9))

    def is_diminishing(self, *, budget: float) -> bool:
        return budget > 0.0 and self.saturation_index(budget=budget) >= 0.5
