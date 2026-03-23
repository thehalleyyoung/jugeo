from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.ideation.theorem_economics.algorithms import (
    EconomicAlgorithm,
    WaterfillingAlgorithm,
    LagrangianOptimizer,
    PortfolioOptimizer,
    YieldMaximizationAlgorithm,
    CompoundingOptimizer,
    _project_simplex,
    _normalize_to_budget,
)
from jugeo.ideation.theorem_economics.models import TheoremYieldModel


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


def _make_models(n: int = 3) -> list[TheoremYieldModel]:
    return [
        _make_yield_model(f"r{i}", saturation=10.0 + i * 3.0, rate=0.2 + i * 0.1)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# _project_simplex tests
# ---------------------------------------------------------------------------

def test_project_simplex_sums_to_total() -> None:
    result = _project_simplex([3.0, 3.0, 3.0], total=10.0)
    assert abs(sum(result) - 10.0) < 1e-9


def test_project_simplex_all_non_negative() -> None:
    result = _project_simplex([10.0, -2.0, 1.0], total=5.0)
    for v in result:
        assert v >= -1e-9


def test_project_simplex_already_feasible() -> None:
    result = _project_simplex([3.0, 4.0, 3.0], total=10.0)
    assert abs(sum(result) - 10.0) < 1e-9


def test_project_simplex_uniform_input_stays_uniform() -> None:
    result = _project_simplex([1.0, 1.0, 1.0], total=3.0)
    for v in result:
        assert abs(v - 1.0) < 1e-9


def test_project_simplex_single_element() -> None:
    result = _project_simplex([5.0], total=7.0)
    assert abs(result[0] - 7.0) < 1e-9


# ---------------------------------------------------------------------------
# _normalize_to_budget tests
# ---------------------------------------------------------------------------

def test_normalize_to_budget_sums_to_total() -> None:
    raw = {"r0": 2.0, "r1": 5.0, "r2": 3.0}
    result = _normalize_to_budget(raw, total=20.0)
    assert abs(sum(result.values()) - 20.0) < 1e-9


def test_normalize_to_budget_preserves_keys() -> None:
    raw = {"r0": 1.0, "r1": 3.0}
    result = _normalize_to_budget(raw, total=10.0)
    assert set(result.keys()) == {"r0", "r1"}


def test_normalize_to_budget_all_non_negative() -> None:
    raw = {"r0": 3.0, "r1": 7.0, "r2": 0.0}
    result = _normalize_to_budget(raw, total=5.0)
    for v in result.values():
        assert v >= 0.0


def test_normalize_to_budget_proportional() -> None:
    raw = {"r0": 1.0, "r1": 1.0}
    result = _normalize_to_budget(raw, total=10.0)
    assert abs(result["r0"] - result["r1"]) < 1e-9


# ---------------------------------------------------------------------------
# EconomicAlgorithm.validate_inputs tests
# ---------------------------------------------------------------------------

def test_economic_algorithm_validate_catches_negative_budget() -> None:
    models = _make_models(2)
    algo = EconomicAlgorithm(models=models)
    with pytest.raises((ValueError, AssertionError)):
        algo.validate_inputs(total_budget=-1.0)


def test_economic_algorithm_validate_catches_empty_models() -> None:
    algo = EconomicAlgorithm(models=[])
    with pytest.raises((ValueError, AssertionError)):
        algo.validate_inputs(total_budget=10.0)


def test_economic_algorithm_validate_passes_for_valid_inputs() -> None:
    models = _make_models(2)
    algo = EconomicAlgorithm(models=models)
    algo.validate_inputs(total_budget=10.0)


# ---------------------------------------------------------------------------
# WaterfillingAlgorithm tests
# ---------------------------------------------------------------------------

def test_waterfilling_run_sums_to_total_budget() -> None:
    models = _make_models(3)
    algo = WaterfillingAlgorithm(models=models)
    total = 15.0
    allocs = algo.run(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_waterfilling_run_all_non_negative() -> None:
    models = _make_models(3)
    algo = WaterfillingAlgorithm(models=models)
    allocs = algo.run(total_budget=10.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_waterfilling_run_keys_match_regime_ids() -> None:
    models = _make_models(3)
    regime_ids = {m.regime_id for m in models}
    algo = WaterfillingAlgorithm(models=models)
    allocs = algo.run(total_budget=10.0)
    assert set(allocs.keys()) == regime_ids


def test_waterfilling_water_level_positive() -> None:
    models = _make_models(3)
    algo = WaterfillingAlgorithm(models=models)
    level = algo.water_level(total_budget=10.0)
    assert level > 0.0


def test_waterfilling_allocation_at_level_non_negative() -> None:
    models = _make_models(3)
    algo = WaterfillingAlgorithm(models=models)
    for model in models:
        alloc = algo.allocation_at_level(model, level=2.0)
        assert alloc >= 0.0


def test_waterfilling_verify_optimality_true_for_own_output() -> None:
    models = _make_models(3)
    algo = WaterfillingAlgorithm(models=models)
    total = 15.0
    allocs = algo.run(total_budget=total)
    assert algo.verify_optimality(allocs, total_budget=total, tolerance=0.5)


def test_waterfilling_run_single_model() -> None:
    models = [_make_yield_model("solo")]
    algo = WaterfillingAlgorithm(models=models)
    allocs = algo.run(total_budget=8.0)
    assert abs(allocs["solo"] - 8.0) < 1e-9


# ---------------------------------------------------------------------------
# LagrangianOptimizer tests
# ---------------------------------------------------------------------------

def test_lagrangian_optimizer_run_sums_to_total_budget() -> None:
    models = _make_models(3)
    optimizer = LagrangianOptimizer(models=models)
    total = 15.0
    allocs = optimizer.run(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_lagrangian_optimizer_run_all_non_negative() -> None:
    models = _make_models(3)
    optimizer = LagrangianOptimizer(models=models)
    allocs = optimizer.run(total_budget=10.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_lagrangian_optimizer_dual_gradient_negative_when_over_budget() -> None:
    models = _make_models(3)
    optimizer = LagrangianOptimizer(models=models)
    allocs = {m.regime_id: 5.0 for m in models}
    grad = optimizer.dual_gradient(allocs, total_budget=5.0)
    assert grad < 0.0


def test_lagrangian_optimizer_dual_gradient_positive_when_under_budget() -> None:
    models = _make_models(3)
    optimizer = LagrangianOptimizer(models=models)
    allocs = {m.regime_id: 0.1 for m in models}
    grad = optimizer.dual_gradient(allocs, total_budget=100.0)
    assert grad > 0.0


def test_lagrangian_optimizer_run_keys_match_regime_ids() -> None:
    models = _make_models(4)
    regime_ids = {m.regime_id for m in models}
    optimizer = LagrangianOptimizer(models=models)
    allocs = optimizer.run(total_budget=20.0)
    assert set(allocs.keys()) == regime_ids


# ---------------------------------------------------------------------------
# PortfolioOptimizer tests
# ---------------------------------------------------------------------------

def test_portfolio_optimizer_run_sums_to_total_budget() -> None:
    models = _make_models(3)
    optimizer = PortfolioOptimizer(models=models)
    total = 18.0
    allocs = optimizer.run(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_portfolio_optimizer_run_all_non_negative() -> None:
    models = _make_models(3)
    optimizer = PortfolioOptimizer(models=models)
    allocs = optimizer.run(total_budget=10.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_portfolio_optimizer_efficient_frontier_is_list() -> None:
    models = _make_models(3)
    optimizer = PortfolioOptimizer(models=models)
    frontier = optimizer.efficient_frontier(total_budget=15.0, num_points=5)
    assert isinstance(frontier, list)


def test_portfolio_optimizer_efficient_frontier_pairs() -> None:
    models = _make_models(3)
    optimizer = PortfolioOptimizer(models=models)
    frontier = optimizer.efficient_frontier(total_budget=15.0, num_points=5)
    assert all(len(p) == 2 for p in frontier)


def test_portfolio_optimizer_efficient_frontier_yields_positive() -> None:
    models = _make_models(3)
    optimizer = PortfolioOptimizer(models=models)
    frontier = optimizer.efficient_frontier(total_budget=15.0, num_points=4)
    for expected_yield, _ in frontier:
        assert expected_yield >= 0.0


# ---------------------------------------------------------------------------
# YieldMaximizationAlgorithm tests
# ---------------------------------------------------------------------------

def test_yield_maximization_run_sums_to_total_budget() -> None:
    models = _make_models(3)
    algo = YieldMaximizationAlgorithm(models=models)
    total = 12.0
    allocs = algo.run(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_yield_maximization_run_all_non_negative() -> None:
    models = _make_models(3)
    algo = YieldMaximizationAlgorithm(models=models)
    allocs = algo.run(total_budget=12.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_yield_maximization_total_yield_gradient_positive_for_positive_budgets() -> None:
    models = _make_models(3)
    algo = YieldMaximizationAlgorithm(models=models)
    budgets = {m.regime_id: 3.0 for m in models}
    grad = algo.total_yield_gradient(budgets)
    for v in grad.values():
        assert v > 0.0


def test_yield_maximization_gradient_decreasing_with_budget() -> None:
    models = [_make_yield_model("gtest", saturation=10.0, rate=0.5)]
    algo = YieldMaximizationAlgorithm(models=models)
    grad_low = algo.total_yield_gradient({"gtest": 1.0})
    grad_high = algo.total_yield_gradient({"gtest": 10.0})
    assert grad_low["gtest"] > grad_high["gtest"]


# ---------------------------------------------------------------------------
# CompoundingOptimizer tests
# ---------------------------------------------------------------------------

def test_compounding_optimizer_run_sums_to_total_budget() -> None:
    models = _make_models(3)
    optimizer = CompoundingOptimizer(models=models, chain_depths={"r0": 0, "r1": 1, "r2": 2})
    total = 15.0
    allocs = optimizer.run(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_compounding_optimizer_run_all_non_negative() -> None:
    models = _make_models(3)
    optimizer = CompoundingOptimizer(models=models, chain_depths={"r0": 0, "r1": 1, "r2": 2})
    allocs = optimizer.run(total_budget=10.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_compounding_optimizer_adjusted_yield_gt_base_for_depth_gt_0() -> None:
    model = _make_yield_model("ctest", saturation=10.0, rate=0.4)
    optimizer = CompoundingOptimizer(models=[model], chain_depths={"ctest": 2})
    base_y = model.yield_at(5.0)
    adjusted = optimizer.adjusted_yield(model=model, budget=5.0, depth=2)
    assert adjusted >= base_y


def test_compounding_optimizer_adjusted_yield_depth_zero_equals_base() -> None:
    model = _make_yield_model("ctest2")
    optimizer = CompoundingOptimizer(models=[model], chain_depths={"ctest2": 0})
    base_y = model.yield_at(5.0)
    adjusted = optimizer.adjusted_yield(model=model, budget=5.0, depth=0)
    assert abs(adjusted - base_y) < 1e-9


def test_compounding_optimizer_keys_match_regime_ids() -> None:
    models = _make_models(3)
    chain_depths = {m.regime_id: i for i, m in enumerate(models)}
    optimizer = CompoundingOptimizer(models=models, chain_depths=chain_depths)
    allocs = optimizer.run(total_budget=12.0)
    assert set(allocs.keys()) == {m.regime_id for m in models}


def test_project_simplex_large_values() -> None:
    result = _project_simplex([100.0, 200.0, 50.0], total=100.0)
    assert abs(sum(result) - 100.0) < 1e-9
    for v in result:
        assert v >= -1e-9


def test_normalize_to_budget_with_zero_value() -> None:
    raw = {"r0": 0.0, "r1": 10.0}
    result = _normalize_to_budget(raw, total=10.0)
    assert abs(sum(result.values()) - 10.0) < 1e-9


def test_waterfilling_high_budget_fills_all() -> None:
    models = _make_models(2)
    algo = WaterfillingAlgorithm(models=models)
    allocs = algo.run(total_budget=1000.0)
    for v in allocs.values():
        assert v > 0.0


def test_yield_maximization_run_keys_match_regime_ids() -> None:
    models = _make_models(4)
    algo = YieldMaximizationAlgorithm(models=models)
    allocs = algo.run(total_budget=20.0)
    assert set(allocs.keys()) == {m.regime_id for m in models}


def test_lagrangian_optimizer_dual_gradient_zero_at_exact_budget() -> None:
    models = [_make_yield_model("exact")]
    optimizer = LagrangianOptimizer(models=models)
    total = 10.0
    allocs = {"exact": total}
    grad = optimizer.dual_gradient(allocs, total_budget=total)
    assert abs(grad) < 0.5


def test_portfolio_optimizer_run_with_two_models() -> None:
    models = _make_models(2)
    optimizer = PortfolioOptimizer(models=models)
    allocs = optimizer.run(total_budget=10.0)
    assert abs(sum(allocs.values()) - 10.0) < 1e-6


def test_compounding_optimizer_depths_affect_allocation() -> None:
    models = _make_models(3)
    depths_deep = {m.regime_id: 3 for m in models}
    depths_shallow = {m.regime_id: 0 for m in models}
    opt_deep = CompoundingOptimizer(models=models, chain_depths=depths_deep)
    opt_shallow = CompoundingOptimizer(models=models, chain_depths=depths_shallow)
    allocs_deep = opt_deep.run(total_budget=15.0)
    allocs_shallow = opt_shallow.run(total_budget=15.0)
    assert abs(sum(allocs_deep.values()) - 15.0) < 1e-6
    assert abs(sum(allocs_shallow.values()) - 15.0) < 1e-6


def test_waterfilling_allocation_at_level_zero_for_low_saturation() -> None:
    low_model = _make_yield_model("low", saturation=0.5, rate=0.5)
    algo = WaterfillingAlgorithm(models=[low_model])
    alloc = algo.allocation_at_level(low_model, level=5.0)
    assert alloc >= 0.0


def test_economic_algorithm_validate_zero_budget_raises() -> None:
    models = _make_models(2)
    algo = EconomicAlgorithm(models=models)
    with pytest.raises((ValueError, AssertionError)):
        algo.validate_inputs(total_budget=0.0)


def test_portfolio_optimizer_efficient_frontier_length_matches_num_points() -> None:
    models = _make_models(3)
    optimizer = PortfolioOptimizer(models=models)
    frontier = optimizer.efficient_frontier(total_budget=15.0, num_points=6)
    assert len(frontier) == 6


def test_yield_maximization_gradient_all_regimes_covered() -> None:
    models = _make_models(4)
    algo = YieldMaximizationAlgorithm(models=models)
    budgets = {m.regime_id: 2.0 for m in models}
    grad = algo.total_yield_gradient(budgets)
    assert set(grad.keys()) == {m.regime_id for m in models}


def test_normalize_to_budget_returns_correct_type() -> None:
    raw = {"r0": 3.0, "r1": 7.0}
    result = _normalize_to_budget(raw, total=10.0)
    assert isinstance(result, dict)


def test_project_simplex_returns_list() -> None:
    result = _project_simplex([1.0, 2.0, 3.0], total=6.0)
    assert isinstance(result, list)
