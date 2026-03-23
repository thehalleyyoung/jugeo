"""Cross-module tests: RepairPlan structural invariants."""
import pytest
import time

try:
    from jugeo.problem_modes.repair_semantics import RepairPlan, RepairStep
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_step(step_id="step-001"):
    return RepairStep(
        step_id=step_id,
        kind="PATCH",
        coordinate="module.func:line_10",
        action="replace_expression",
        parameters={"old": "x / 0", "new": "x / max(y, 1)"},
    )

def _make_plan(plan_id="plan-001", steps=()):
    return RepairPlan(
        plan_id=plan_id,
        from_counterexample="cx-001",
        steps=steps,
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=1.0,
    )

def test_repair_plan_construction():
    plan = _make_plan()
    assert plan is not None
    assert plan.plan_id == "plan-001"

def test_repair_plan_total_effort():
    plan = _make_plan()
    effort = plan.total_effort()
    assert isinstance(effort, float)
    assert effort >= 0.0

def test_repair_plan_is_admissible():
    plan = _make_plan()
    result = plan.is_admissible()
    assert isinstance(result, bool)

def test_repair_plan_topological_sort():
    step = _make_step()
    plan = _make_plan(steps=(step,))
    sorted_steps = plan.topological_sort()
    assert hasattr(sorted_steps, "__iter__")
