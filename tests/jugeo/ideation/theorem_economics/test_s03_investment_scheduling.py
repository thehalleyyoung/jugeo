from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.ideation.theorem_economics.s03_investment_scheduling import (
    GreedyInvestmentAllocator,
    LagrangianRelaxationAllocator,
    AdaptiveScheduler,
    InvestmentScheduler,
    ScheduleEvaluator,
    _compute_total_yield,
    _soft_normalize,
)
from jugeo.ideation.theorem_economics.models import TheoremYieldModel, InvestmentSchedule
from jugeo.ideation.scheduling import IdeationSchedule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_yield_model(
    regime_id: str = "r1",
    saturation: float = 10.0,
    rate: float = 0.4,
) -> TheoremYieldModel:
    return TheoremYieldModel(
        model_id=f"model-{regime_id}",
        regime_id=regime_id,
        saturation_yield=saturation,
        growth_rate=rate,
        current_budget=0.0,
        empirical_data=[],
    )


def _make_models(n: int = 3, base_saturation: float = 10.0) -> list[TheoremYieldModel]:
    return [
        _make_yield_model(f"r{i}", saturation=base_saturation + i * 2.0, rate=0.3 + i * 0.05)
        for i in range(n)
    ]


def _make_investment_schedule(
    regime_ids: list[str] | None = None,
    total_budget: float = 10.0,
    expected_yield: float = 5.0,
) -> InvestmentSchedule:
    if regime_ids is None:
        regime_ids = ["r0", "r1", "r2"]
    per = total_budget / len(regime_ids)
    return InvestmentSchedule(
        schedule_id="sched-test",
        regime_ids=regime_ids,
        allocations={rid: per for rid in regime_ids},
        total_budget=total_budget,
        expected_yield=expected_yield,
    )


# ---------------------------------------------------------------------------
# _compute_total_yield tests
# ---------------------------------------------------------------------------

def test_compute_total_yield_positive_for_positive_allocations() -> None:
    models = _make_models(3)
    allocations = {m.regime_id: 3.0 for m in models}
    total = _compute_total_yield(models, allocations)
    assert total > 0.0


def test_compute_total_yield_zero_for_zero_allocations() -> None:
    models = _make_models(3)
    allocations = {m.regime_id: 0.0 for m in models}
    total = _compute_total_yield(models, allocations)
    assert total == 0.0


def test_compute_total_yield_increases_with_budget() -> None:
    models = _make_models(3)
    low_allocs = {m.regime_id: 1.0 for m in models}
    high_allocs = {m.regime_id: 5.0 for m in models}
    assert _compute_total_yield(models, high_allocs) > _compute_total_yield(models, low_allocs)


# ---------------------------------------------------------------------------
# _soft_normalize tests
# ---------------------------------------------------------------------------

def test_soft_normalize_sums_to_total() -> None:
    raw = {"r0": 2.0, "r1": 5.0, "r2": 3.0}
    normalized = _soft_normalize(raw, total=20.0)
    assert abs(sum(normalized.values()) - 20.0) < 1e-9


def test_soft_normalize_preserves_keys() -> None:
    raw = {"r0": 1.0, "r1": 3.0}
    normalized = _soft_normalize(raw, total=10.0)
    assert set(normalized.keys()) == {"r0", "r1"}


def test_soft_normalize_all_non_negative() -> None:
    raw = {"r0": 3.0, "r1": 7.0}
    normalized = _soft_normalize(raw, total=5.0)
    for v in normalized.values():
        assert v >= 0.0


def test_soft_normalize_proportional() -> None:
    raw = {"r0": 1.0, "r1": 1.0}
    normalized = _soft_normalize(raw, total=10.0)
    assert abs(normalized["r0"] - normalized["r1"]) < 1e-9


# ---------------------------------------------------------------------------
# GreedyInvestmentAllocator tests
# ---------------------------------------------------------------------------

def test_greedy_allocator_sums_to_total_budget() -> None:
    models = _make_models(3)
    allocator = GreedyInvestmentAllocator(models=models)
    total = 15.0
    allocs = allocator.allocate(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_greedy_allocator_all_non_negative() -> None:
    models = _make_models(3)
    allocator = GreedyInvestmentAllocator(models=models)
    allocs = allocator.allocate(total_budget=10.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_greedy_allocator_keys_match_regime_ids() -> None:
    models = _make_models(3)
    regime_ids = {m.regime_id for m in models}
    allocator = GreedyInvestmentAllocator(models=models)
    allocs = allocator.allocate(total_budget=10.0)
    assert set(allocs.keys()) == regime_ids


def test_greedy_allocator_gives_more_to_high_value_regime() -> None:
    low_model = _make_yield_model("low-val", saturation=2.0, rate=0.1)
    high_model = _make_yield_model("high-val", saturation=20.0, rate=0.8)
    allocator = GreedyInvestmentAllocator(models=[low_model, high_model])
    allocs = allocator.allocate(total_budget=10.0)
    assert allocs["high-val"] >= allocs["low-val"]


def test_greedy_allocator_with_single_model() -> None:
    models = [_make_yield_model("solo")]
    allocator = GreedyInvestmentAllocator(models=models)
    allocs = allocator.allocate(total_budget=7.5)
    assert abs(allocs["solo"] - 7.5) < 1e-9


def test_greedy_allocator_with_many_models() -> None:
    models = _make_models(10)
    allocator = GreedyInvestmentAllocator(models=models)
    allocs = allocator.allocate(total_budget=100.0)
    assert abs(sum(allocs.values()) - 100.0) < 1e-6


# ---------------------------------------------------------------------------
# LagrangianRelaxationAllocator tests
# ---------------------------------------------------------------------------

def test_lagrangian_allocator_sums_to_total_budget() -> None:
    models = _make_models(3)
    allocator = LagrangianRelaxationAllocator(models=models)
    total = 15.0
    allocs = allocator.allocate(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_lagrangian_allocator_all_non_negative() -> None:
    models = _make_models(3)
    allocator = LagrangianRelaxationAllocator(models=models)
    allocs = allocator.allocate(total_budget=10.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_lagrangian_allocator_keys_match_regime_ids() -> None:
    models = _make_models(4)
    regime_ids = {m.regime_id for m in models}
    allocator = LagrangianRelaxationAllocator(models=models)
    allocs = allocator.allocate(total_budget=12.0)
    assert set(allocs.keys()) == regime_ids


def test_lagrangian_allocator_primal_feasible_true() -> None:
    models = _make_models(3)
    allocator = LagrangianRelaxationAllocator(models=models)
    total = 15.0
    allocs = allocator.allocate(total_budget=total)
    assert allocator.primal_feasible(allocs, total_budget=total) is True


def test_lagrangian_allocator_primal_feasible_false_for_bad_allocs() -> None:
    models = _make_models(2)
    allocator = LagrangianRelaxationAllocator(models=models)
    bad_allocs = {models[0].regime_id: 3.0, models[1].regime_id: 3.0}
    assert allocator.primal_feasible(bad_allocs, total_budget=10.0) is False


# ---------------------------------------------------------------------------
# AdaptiveScheduler tests
# ---------------------------------------------------------------------------

def test_adaptive_scheduler_update_incorporates_yields() -> None:
    models = _make_models(3)
    scheduler = AdaptiveScheduler(models=models)
    obs = {m.regime_id: m.yield_at(5.0) for m in models}
    scheduler.update(observed_yields=obs, budgets_used={m.regime_id: 5.0 for m in models})
    assert len(scheduler.performance_history()) > 0


def test_adaptive_scheduler_performance_history_grows_after_updates() -> None:
    models = _make_models(2)
    scheduler = AdaptiveScheduler(models=models)
    initial_len = len(scheduler.performance_history())
    for _ in range(3):
        obs = {m.regime_id: m.yield_at(3.0) for m in models}
        scheduler.update(observed_yields=obs, budgets_used={m.regime_id: 3.0 for m in models})
    assert len(scheduler.performance_history()) > initial_len


def test_adaptive_scheduler_allocate_after_update_sums_to_budget() -> None:
    models = _make_models(3)
    scheduler = AdaptiveScheduler(models=models)
    obs = {m.regime_id: m.yield_at(4.0) for m in models}
    scheduler.update(observed_yields=obs, budgets_used={m.regime_id: 4.0 for m in models})
    allocs = scheduler.allocate(total_budget=12.0)
    assert abs(sum(allocs.values()) - 12.0) < 1e-6


def test_adaptive_scheduler_performance_history_contains_regime_ids() -> None:
    models = _make_models(2)
    scheduler = AdaptiveScheduler(models=models)
    obs = {m.regime_id: 1.0 for m in models}
    scheduler.update(observed_yields=obs, budgets_used={m.regime_id: 2.0 for m in models})
    history = scheduler.performance_history()
    assert len(history) > 0


# ---------------------------------------------------------------------------
# InvestmentScheduler tests
# ---------------------------------------------------------------------------

def test_investment_scheduler_schedule_returns_investment_schedule() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    sched = scheduler.schedule(total_budget=12.0)
    assert isinstance(sched, InvestmentSchedule)


def test_investment_scheduler_schedule_correct_total_budget() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    total = 12.0
    sched = scheduler.schedule(total_budget=total)
    assert abs(sched.total_budget - total) < 1e-9


def test_investment_scheduler_schedule_allocations_sum_to_budget() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    total = 12.0
    sched = scheduler.schedule(total_budget=total)
    assert abs(sum(sched.allocations.values()) - total) < 1e-6


def test_investment_scheduler_update_returns_updated_schedule() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    sched = scheduler.schedule(total_budget=10.0)
    obs = {m.regime_id: m.yield_at(3.0) for m in models}
    updated = scheduler.update(schedule=sched, observed_yields=obs)
    assert isinstance(updated, InvestmentSchedule)


def test_investment_scheduler_update_preserves_total_budget() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    total = 10.0
    sched = scheduler.schedule(total_budget=total)
    obs = {m.regime_id: m.yield_at(3.0) for m in models}
    updated = scheduler.update(schedule=sched, observed_yields=obs)
    assert abs(sum(updated.allocations.values()) - total) < 1e-6


def test_investment_scheduler_compare_schedules_returns_dict() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    s1 = scheduler.schedule(total_budget=10.0)
    s2 = scheduler.schedule(total_budget=15.0)
    result = scheduler.compare_schedules([s1, s2])
    assert isinstance(result, dict)


def test_investment_scheduler_compare_schedules_has_schedule_ids() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    s1 = scheduler.schedule(total_budget=10.0)
    s2 = scheduler.schedule(total_budget=15.0)
    result = scheduler.compare_schedules([s1, s2])
    assert len(result) >= 1


def test_investment_scheduler_to_ideation_schedule_returns_correct_type() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    sched = scheduler.schedule(total_budget=10.0)
    ideation = scheduler.to_ideation_schedule(sched)
    assert isinstance(ideation, IdeationSchedule)


# ---------------------------------------------------------------------------
# ScheduleEvaluator tests
# ---------------------------------------------------------------------------

def test_schedule_evaluator_forecast_accuracy_in_range() -> None:
    sched = _make_investment_schedule(total_budget=10.0, expected_yield=5.0)
    actual_yields = {rid: 4.5 for rid in sched.regime_ids}
    evaluator = ScheduleEvaluator()
    acc = evaluator.forecast_accuracy(sched, actual_yields)
    assert -1.0 <= acc <= 1.0


def test_schedule_evaluator_forecast_accuracy_perfect_is_one() -> None:
    sched = _make_investment_schedule(
        regime_ids=["r0", "r1"],
        total_budget=10.0,
        expected_yield=5.0,
    )
    actual_yields = {"r0": 2.5, "r1": 2.5}
    evaluator = ScheduleEvaluator()
    acc = evaluator.forecast_accuracy(sched, actual_yields)
    assert abs(acc - 1.0) < 0.01


def test_schedule_evaluator_forecast_accuracy_bad_is_lower() -> None:
    sched = _make_investment_schedule(total_budget=10.0, expected_yield=5.0)
    bad_yields = {rid: 0.0 for rid in sched.regime_ids}
    good_yields = {rid: 5.0 / len(sched.regime_ids) for rid in sched.regime_ids}
    evaluator = ScheduleEvaluator()
    assert evaluator.forecast_accuracy(sched, good_yields) > evaluator.forecast_accuracy(sched, bad_yields)


def test_lagrangian_allocator_with_high_budget() -> None:
    models = _make_models(3)
    allocator = LagrangianRelaxationAllocator(models=models)
    total = 1000.0
    allocs = allocator.allocate(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_greedy_allocator_total_budget_zero() -> None:
    models = _make_models(2)
    allocator = GreedyInvestmentAllocator(models=models)
    allocs = allocator.allocate(total_budget=0.0)
    assert abs(sum(allocs.values()) - 0.0) < 1e-9


def test_investment_scheduler_with_five_regimes() -> None:
    models = _make_models(5)
    scheduler = InvestmentScheduler(models=models)
    sched = scheduler.schedule(total_budget=25.0)
    assert len(sched.allocations) == 5
    assert abs(sum(sched.allocations.values()) - 25.0) < 1e-6


def test_adaptive_scheduler_allocation_non_negative() -> None:
    models = _make_models(3)
    scheduler = AdaptiveScheduler(models=models)
    obs = {m.regime_id: m.yield_at(3.0) for m in models}
    scheduler.update(observed_yields=obs, budgets_used={m.regime_id: 3.0 for m in models})
    allocs = scheduler.allocate(total_budget=15.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_investment_scheduler_compare_schedules_two_entries() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    s1 = scheduler.schedule(total_budget=10.0)
    s2 = scheduler.schedule(total_budget=20.0)
    result = scheduler.compare_schedules([s1, s2])
    assert len(result) >= 2


def test_schedule_evaluator_zero_actual_yield_gives_low_accuracy() -> None:
    sched = _make_investment_schedule(total_budget=10.0, expected_yield=5.0)
    zero_yields = {rid: 0.0 for rid in sched.regime_ids}
    evaluator = ScheduleEvaluator()
    acc = evaluator.forecast_accuracy(sched, zero_yields)
    assert acc < 0.5


def test_lagrangian_allocator_two_models() -> None:
    models = _make_models(2)
    allocator = LagrangianRelaxationAllocator(models=models)
    allocs = allocator.allocate(total_budget=10.0)
    assert abs(sum(allocs.values()) - 10.0) < 1e-6


def test_compute_total_yield_sums_all_regimes() -> None:
    models = _make_models(4)
    allocations = {m.regime_id: 5.0 for m in models}
    total = _compute_total_yield(models, allocations)
    assert total > 0.0


def test_soft_normalize_large_total() -> None:
    raw = {"r0": 1.0, "r1": 2.0}
    normalized = _soft_normalize(raw, total=999.0)
    assert abs(sum(normalized.values()) - 999.0) < 1e-9


def test_ideation_schedule_from_scheduler_has_content() -> None:
    models = _make_models(3)
    scheduler = InvestmentScheduler(models=models)
    sched = scheduler.schedule(total_budget=10.0)
    ideation = scheduler.to_ideation_schedule(sched)
    assert ideation is not None
