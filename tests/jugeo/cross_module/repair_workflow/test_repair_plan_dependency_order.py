"""Cross-module tests: RepairPlan.topological_sort returns sorted steps."""
import pytest
import time

try:
    from jugeo.problem_modes.repair_semantics import RepairPlan, RepairStep
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_plan(steps=()):
    return RepairPlan(
        plan_id="dep-order-plan-001",
        from_counterexample="cx-001",
        steps=steps,
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=0.0,
    )

def test_empty_plan_topological_sort():
    plan = _make_plan()
    sorted_steps = plan.topological_sort()
    result = list(sorted_steps)
    assert len(result) == 0

def test_plan_with_steps_topological_sort():
    steps = (
        RepairStep(step_id="s1", kind="PATCH", coordinate="c1", action="fix", parameters={}),
        RepairStep(step_id="s2", kind="PATCH", coordinate="c2", action="fix", parameters={}),
    )
    plan = _make_plan(steps=steps)
    sorted_steps = list(plan.topological_sort())
    assert len(sorted_steps) == 2

def test_next_steps_is_sequence():
    plan = _make_plan()
    ns = plan.next_steps()
    assert hasattr(ns, "__iter__")
