from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from jugeo.ideation.scheduling import IdeationSchedule

from .models import InvestmentSchedule, TheoremYieldModel


def _compute_total_yield(models: list[TheoremYieldModel], allocations: dict[str, float]) -> float:
    return sum(model.yield_at(allocations.get(model.regime_id, 0.0)) for model in models)


def _soft_normalize(raw: dict[str, float], *, total: float) -> dict[str, float]:
    total = max(0.0, total)
    clipped = {k: max(0.0, float(v)) for k, v in raw.items()}
    denom = sum(clipped.values())
    if denom <= 0.0:
        share = total / len(clipped) if clipped else 0.0
        return {k: share for k in clipped}
    return {k: total * v / denom for k, v in clipped.items()}


@dataclass
class GreedyInvestmentAllocator:
    models: list[TheoremYieldModel]

    def allocate(self, *, total_budget: float) -> dict[str, float]:
        scores = {m.regime_id: m.marginal_yield(0.0) for m in self.models}
        return _soft_normalize(scores, total=total_budget)


@dataclass
class LagrangianRelaxationAllocator:
    models: list[TheoremYieldModel]

    def allocate(self, *, total_budget: float) -> dict[str, float]:
        scores = {m.regime_id: m.saturation_yield * m.growth_rate for m in self.models}
        return _soft_normalize(scores, total=total_budget)

    def primal_feasible(self, allocs: dict[str, float], *, total_budget: float) -> bool:
        return abs(sum(allocs.values()) - total_budget) < 1e-6 and all(v >= -1e-9 for v in allocs.values())


@dataclass
class AdaptiveScheduler:
    models: list[TheoremYieldModel]
    _history: list[dict[str, float]] = field(default_factory=list)

    def update(self, *, observed_yields: dict[str, float], budgets_used: dict[str, float]) -> None:
        record = {rid: observed_yields.get(rid, 0.0) / max(1e-9, budgets_used.get(rid, 1.0)) for rid in budgets_used}
        self._history.append(record)

    def performance_history(self) -> list[dict[str, float]]:
        return list(self._history)

    def allocate(self, *, total_budget: float) -> dict[str, float]:
        if not self._history:
            scores = {m.regime_id: m.marginal_yield(0.0) for m in self.models}
        else:
            latest = self._history[-1]
            scores = {m.regime_id: max(1e-9, latest.get(m.regime_id, 0.0)) for m in self.models}
        return _soft_normalize(scores, total=total_budget)


@dataclass
class InvestmentScheduler:
    models: list[TheoremYieldModel]
    allocator: LagrangianRelaxationAllocator | None = None

    def __post_init__(self) -> None:
        if self.allocator is None:
            self.allocator = LagrangianRelaxationAllocator(self.models)

    def schedule(self, *, total_budget: float) -> InvestmentSchedule:
        allocs = self.allocator.allocate(total_budget=total_budget)
        return InvestmentSchedule(
            schedule_id=uuid.uuid4().hex,
            regime_ids=[m.regime_id for m in self.models],
            allocations=allocs,
            total_budget=total_budget,
            expected_yield=_compute_total_yield(self.models, allocs),
        )

    def update(self, *, schedule: InvestmentSchedule, observed_yields: dict[str, float]) -> InvestmentSchedule:
        scores = {rid: max(1e-9, observed_yields.get(rid, 0.0)) for rid in schedule.regime_ids}
        allocs = _soft_normalize(scores, total=schedule.total_budget)
        return InvestmentSchedule(
            schedule_id=uuid.uuid4().hex,
            regime_ids=list(schedule.regime_ids),
            allocations=allocs,
            total_budget=schedule.total_budget,
            expected_yield=sum(observed_yields.values()),
        )

    def compare_schedules(self, schedules: list[InvestmentSchedule]) -> dict[str, float]:
        return {schedule.schedule_id: schedule.expected_yield for schedule in schedules}

    def to_ideation_schedule(self, schedule: InvestmentSchedule) -> IdeationSchedule:
        ordered = sorted(schedule.allocations.items(), key=lambda item: item[1], reverse=True)
        explorations = tuple(rid for rid, _ in ordered[: max(1, len(ordered)//2 or 1)])
        exploitations = tuple(rid for rid, _ in ordered[max(1, len(ordered)//2 or 1):])
        return IdeationSchedule(
            schedule_id=schedule.schedule_id,
            epoch=0,
            planned_explorations=explorations,
            planned_exploitations=exploitations,
            budget=schedule.total_budget,
            expected_yield=schedule.expected_yield,
            regime_allocations=schedule.allocations,
            created_at=time.time(),
        )


class ScheduleEvaluator:
    def forecast_accuracy(self, schedule: InvestmentSchedule, actual_yields: dict[str, float]) -> float:
        actual_total = sum(actual_yields.values())
        expected = schedule.expected_yield
        denom = max(abs(expected), 1e-9)
        return max(-1.0, min(1.0, 1.0 - abs(actual_total - expected) / denom))
