from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.ideation.theorem_economics.s04_compounding import (
    CompoundingFactor,
    TheoremChainTracer,
    CompoundingModel,
    CompoundInterestAnalogy,
    CompoundingPortfolioAnalyzer,
    _dfs_depth,
    _bfs_all_derived,
)
from jugeo.ideation.theorem_economics.models import (
    TheoremYieldModel,
    CompoundingEffect,
)


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


def _make_chain_tracer_with_chain() -> TheoremChainTracer:
    tracer = TheoremChainTracer()
    tracer.add_theorem("t0")
    tracer.add_theorem("t1")
    tracer.add_theorem("t2")
    tracer.add_theorem("t3")
    tracer.add_dependency(parent="t0", child="t1")
    tracer.add_dependency(parent="t1", child="t2")
    tracer.add_dependency(parent="t2", child="t3")
    return tracer


def _make_compounding_model(num_regimes: int = 2) -> CompoundingModel:
    models = [
        _make_yield_model(f"r{i}", saturation=10.0 + i * 2.0, rate=0.3 + i * 0.1)
        for i in range(num_regimes)
    ]
    return CompoundingModel(yield_models=models, base_compounding_rate=0.1)


def _make_compounding_effect(
    base_yield: float = 5.0,
    derived_theorems: int = 3,
    chain_depth: int = 2,
    compounding_factor: float = 1.2,
) -> CompoundingEffect:
    return CompoundingEffect(
        base_theorem_id="t0",
        base_yield=base_yield,
        derived_theorems=derived_theorems,
        chain_depth=chain_depth,
        compounding_factor=compounding_factor,
    )


# ---------------------------------------------------------------------------
# _dfs_depth tests
# ---------------------------------------------------------------------------

def test_dfs_depth_single_node() -> None:
    adjacency = {"t0": []}
    depth = _dfs_depth("t0", adjacency)
    assert depth == 0


def test_dfs_depth_linear_chain() -> None:
    adjacency = {"t0": ["t1"], "t1": ["t2"], "t2": []}
    depth = _dfs_depth("t0", adjacency)
    assert depth == 2


def test_dfs_depth_two_branches() -> None:
    adjacency = {"t0": ["t1", "t2"], "t1": ["t3"], "t2": [], "t3": []}
    depth = _dfs_depth("t0", adjacency)
    assert depth == 2


def test_dfs_depth_returns_zero_for_leaf() -> None:
    adjacency = {"t0": ["t1"], "t1": []}
    depth = _dfs_depth("t1", adjacency)
    assert depth == 0


def test_dfs_depth_five_levels() -> None:
    nodes = [f"t{i}" for i in range(6)]
    adjacency = {nodes[i]: [nodes[i + 1]] for i in range(5)}
    adjacency[nodes[5]] = []
    depth = _dfs_depth("t0", adjacency)
    assert depth == 5


# ---------------------------------------------------------------------------
# _bfs_all_derived tests
# ---------------------------------------------------------------------------

def test_bfs_all_derived_empty_for_leaf() -> None:
    adjacency = {"t0": []}
    derived = _bfs_all_derived("t0", adjacency)
    assert len(derived) == 0


def test_bfs_all_derived_returns_all_reachable() -> None:
    adjacency = {"t0": ["t1", "t2"], "t1": ["t3"], "t2": [], "t3": []}
    derived = _bfs_all_derived("t0", adjacency)
    assert "t1" in derived
    assert "t2" in derived
    assert "t3" in derived


def test_bfs_all_derived_linear_chain() -> None:
    adjacency = {"t0": ["t1"], "t1": ["t2"], "t2": []}
    derived = _bfs_all_derived("t0", adjacency)
    assert derived == {"t1", "t2"}


def test_bfs_all_derived_does_not_include_root() -> None:
    adjacency = {"t0": ["t1"], "t1": []}
    derived = _bfs_all_derived("t0", adjacency)
    assert "t0" not in derived


def test_bfs_all_derived_handles_wide_tree() -> None:
    adjacency = {"root": [f"child{i}" for i in range(5)]}
    for i in range(5):
        adjacency[f"child{i}"] = []
    derived = _bfs_all_derived("root", adjacency)
    assert len(derived) == 5


# ---------------------------------------------------------------------------
# CompoundingFactor tests
# ---------------------------------------------------------------------------

def test_compounding_factor_depth_one_returns_base() -> None:
    cf = CompoundingFactor(base_factor=1.5, depth=1)
    assert abs(cf.effective_factor() - 1.5) < 1e-9


def test_compounding_factor_depth_two_is_larger() -> None:
    cf_depth1 = CompoundingFactor(base_factor=1.5, depth=1)
    cf_depth2 = CompoundingFactor(base_factor=1.5, depth=2)
    assert cf_depth2.effective_factor() > cf_depth1.effective_factor()


def test_compounding_factor_depth_zero_returns_one() -> None:
    cf = CompoundingFactor(base_factor=1.5, depth=0)
    assert abs(cf.effective_factor() - 1.0) < 1e-9


def test_compounding_factor_is_superlinear_true_for_base_gt_one() -> None:
    cf = CompoundingFactor(base_factor=1.2, depth=2)
    assert cf.is_superlinear() is True


def test_compounding_factor_is_superlinear_false_for_base_lte_one() -> None:
    cf = CompoundingFactor(base_factor=0.9, depth=2)
    assert cf.is_superlinear() is False


def test_compounding_factor_is_superlinear_false_for_base_exactly_one() -> None:
    cf = CompoundingFactor(base_factor=1.0, depth=2)
    assert cf.is_superlinear() is False


def test_compounding_factor_large_depth() -> None:
    cf = CompoundingFactor(base_factor=1.1, depth=10)
    assert cf.effective_factor() > 1.0


# ---------------------------------------------------------------------------
# TheoremChainTracer tests
# ---------------------------------------------------------------------------

def test_chain_tracer_add_dependency_grows_adjacency() -> None:
    tracer = TheoremChainTracer()
    tracer.add_theorem("t0")
    tracer.add_theorem("t1")
    initial_size = len(tracer.all_theorems())
    tracer.add_dependency(parent="t0", child="t1")
    derived = tracer.all_derived("t0")
    assert "t1" in derived


def test_chain_tracer_chain_depth_correct() -> None:
    tracer = _make_chain_tracer_with_chain()
    assert tracer.chain_depth("t0") == 3


def test_chain_tracer_chain_depth_zero_for_leaf() -> None:
    tracer = _make_chain_tracer_with_chain()
    assert tracer.chain_depth("t3") == 0


def test_chain_tracer_all_derived_returns_all_reachable() -> None:
    tracer = _make_chain_tracer_with_chain()
    derived = tracer.all_derived("t0")
    assert "t1" in derived
    assert "t2" in derived
    assert "t3" in derived


def test_chain_tracer_all_derived_does_not_include_root() -> None:
    tracer = _make_chain_tracer_with_chain()
    derived = tracer.all_derived("t0")
    assert "t0" not in derived


def test_chain_tracer_roots_returns_theorems_with_no_parents() -> None:
    tracer = _make_chain_tracer_with_chain()
    roots = tracer.roots()
    assert "t0" in roots
    assert "t1" not in roots
    assert "t3" not in roots


def test_chain_tracer_roots_multiple_independent_roots() -> None:
    tracer = TheoremChainTracer()
    tracer.add_theorem("root1")
    tracer.add_theorem("root2")
    tracer.add_theorem("child")
    tracer.add_dependency("root1", "child")
    roots = tracer.roots()
    assert "root1" in roots
    assert "root2" in roots
    assert "child" not in roots


def test_chain_tracer_all_derived_partial_chain() -> None:
    tracer = _make_chain_tracer_with_chain()
    derived = tracer.all_derived("t1")
    assert "t2" in derived
    assert "t3" in derived
    assert "t0" not in derived


def test_chain_tracer_add_multiple_dependencies() -> None:
    tracer = TheoremChainTracer()
    for tid in ["t0", "t1", "t2", "t3"]:
        tracer.add_theorem(tid)
    tracer.add_dependency("t0", "t1")
    tracer.add_dependency("t0", "t2")
    tracer.add_dependency("t0", "t3")
    derived = tracer.all_derived("t0")
    assert len(derived) == 3


# ---------------------------------------------------------------------------
# CompoundingModel tests
# ---------------------------------------------------------------------------

def test_compounding_model_compute_compounding_returns_compounding_effect() -> None:
    model = _make_compounding_model()
    tracer = _make_chain_tracer_with_chain()
    ce = model.compute_compounding(theorem_id="t0", tracer=tracer, budget=5.0)
    assert isinstance(ce, CompoundingEffect)


def test_compounding_model_compute_compounding_positive_yield() -> None:
    model = _make_compounding_model()
    tracer = _make_chain_tracer_with_chain()
    ce = model.compute_compounding("t0", tracer, budget=5.0)
    assert ce.total_yield() > 0.0


def test_compounding_model_total_portfolio_yield_positive() -> None:
    model = _make_compounding_model()
    tracer = _make_chain_tracer_with_chain()
    theorem_budgets = {"t0": 5.0, "t1": 3.0}
    total = model.total_portfolio_yield(tracer=tracer, theorem_budgets=theorem_budgets)
    assert total > 0.0


def test_compounding_model_marginal_theorem_value_with_chain_gt_without() -> None:
    model = _make_compounding_model()
    tracer_chain = _make_chain_tracer_with_chain()
    tracer_single = TheoremChainTracer()
    tracer_single.add_theorem("t0")
    val_with_chain = model.marginal_theorem_value("t0", tracer=tracer_chain, budget=5.0)
    val_without_chain = model.marginal_theorem_value("t0", tracer=tracer_single, budget=5.0)
    assert val_with_chain >= val_without_chain


# ---------------------------------------------------------------------------
# CompoundInterestAnalogy tests
# ---------------------------------------------------------------------------

def test_compound_interest_future_value_correct() -> None:
    analogy = CompoundInterestAnalogy()
    fv = analogy.future_value(principal=100.0, rate=0.1, periods=10)
    expected = 100.0 * (1 + 0.1) ** 10
    assert abs(fv - expected) < 1e-9


def test_compound_interest_future_value_increases_with_periods() -> None:
    analogy = CompoundInterestAnalogy()
    fv5 = analogy.future_value(100.0, rate=0.1, periods=5)
    fv10 = analogy.future_value(100.0, rate=0.1, periods=10)
    assert fv10 > fv5


def test_compound_interest_present_value_inverse_of_future_value() -> None:
    analogy = CompoundInterestAnalogy()
    principal = 100.0
    rate = 0.08
    periods = 5
    fv = analogy.future_value(principal, rate, periods)
    pv = analogy.present_value(fv, rate, periods)
    assert abs(pv - principal) < 1e-9


def test_compound_interest_present_value_less_than_future_value() -> None:
    analogy = CompoundInterestAnalogy()
    fv = 150.0
    pv = analogy.present_value(fv, rate=0.1, periods=5)
    assert pv < fv


def test_compound_interest_doubling_time_approx_one_for_rate_one() -> None:
    analogy = CompoundInterestAnalogy()
    dt = analogy.doubling_time(rate=1.0)
    assert abs(dt - 1.0) < 0.1


def test_compound_interest_doubling_time_rule_of_seventy() -> None:
    analogy = CompoundInterestAnalogy()
    rate = 0.1
    dt = analogy.doubling_time(rate=rate)
    rule_of_seventy = 0.693 / rate
    assert abs(dt - rule_of_seventy) < 1.0


def test_compound_interest_doubling_time_positive() -> None:
    analogy = CompoundInterestAnalogy()
    assert analogy.doubling_time(rate=0.05) > 0.0


def test_compound_interest_future_value_rate_zero_equals_principal() -> None:
    analogy = CompoundInterestAnalogy()
    fv = analogy.future_value(100.0, rate=0.0, periods=10)
    assert abs(fv - 100.0) < 1e-9


# ---------------------------------------------------------------------------
# CompoundingPortfolioAnalyzer tests
# ---------------------------------------------------------------------------

def test_compounding_portfolio_analyzer_index_gt_one_for_connected() -> None:
    tracer = _make_chain_tracer_with_chain()
    model = _make_compounding_model()
    analyzer = CompoundingPortfolioAnalyzer(compounding_model=model)
    index = analyzer.compounding_index(tracer=tracer, theorem_ids=["t0", "t1", "t2", "t3"])
    assert index > 1.0


def test_compounding_portfolio_analyzer_index_near_one_for_isolated() -> None:
    tracer = TheoremChainTracer()
    for i in range(4):
        tracer.add_theorem(f"iso{i}")
    model = _make_compounding_model()
    analyzer = CompoundingPortfolioAnalyzer(compounding_model=model)
    index = analyzer.compounding_index(tracer=tracer, theorem_ids=[f"iso{i}" for i in range(4)])
    assert 0.9 <= index <= 2.0


def test_compounding_portfolio_analyzer_larger_chain_higher_index() -> None:
    tracer_short = TheoremChainTracer()
    for tid in ["s0", "s1"]:
        tracer_short.add_theorem(tid)
    tracer_short.add_dependency("s0", "s1")

    tracer_long = _make_chain_tracer_with_chain()

    model = _make_compounding_model()
    analyzer = CompoundingPortfolioAnalyzer(compounding_model=model)

    idx_short = analyzer.compounding_index(tracer_short, ["s0", "s1"])
    idx_long = analyzer.compounding_index(tracer_long, ["t0", "t1", "t2", "t3"])
    assert idx_long >= idx_short


def test_compounding_factor_three_depths() -> None:
    cf1 = CompoundingFactor(base_factor=1.3, depth=1)
    cf2 = CompoundingFactor(base_factor=1.3, depth=2)
    cf3 = CompoundingFactor(base_factor=1.3, depth=3)
    assert cf3.effective_factor() > cf2.effective_factor() > cf1.effective_factor()


def test_compounding_model_base_rate_stored() -> None:
    model = CompoundingModel(
        yield_models=[_make_yield_model()],
        base_compounding_rate=0.25,
    )
    assert model.base_compounding_rate == 0.25


def test_chain_tracer_empty_all_derived() -> None:
    tracer = TheoremChainTracer()
    tracer.add_theorem("lone")
    derived = tracer.all_derived("lone")
    assert len(derived) == 0


def test_compound_interest_future_value_increases_with_rate() -> None:
    analogy = CompoundInterestAnalogy()
    fv_low = analogy.future_value(100.0, rate=0.05, periods=10)
    fv_high = analogy.future_value(100.0, rate=0.15, periods=10)
    assert fv_high > fv_low
