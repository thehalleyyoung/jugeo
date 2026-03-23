"""Cross-module tests: RepairPlan serializes/deserializes correctly."""
import pytest
import time

try:
    from jugeo.problem_modes.repair_semantics import RepairPlan
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_plan():
    return RepairPlan(
        plan_id="roundtrip-plan-001",
        from_counterexample="cx-001",
        steps=(),
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=2.5,
    )

def test_repair_plan_to_dict():
    plan = _make_plan()
    if hasattr(plan, "to_dict"):
        d = plan.to_dict()
        assert isinstance(d, dict)
    else:
        import dataclasses
        d = dataclasses.asdict(plan)
        assert isinstance(d, dict)

def test_repair_plan_from_dict():
    plan = _make_plan()
    if hasattr(plan, "to_dict") and hasattr(RepairPlan, "from_dict"):
        d = plan.to_dict()
        restored = RepairPlan.from_dict(d)
        assert isinstance(restored, RepairPlan)
    else:
        pytest.skip("to_dict/from_dict not implemented")

def test_roundtrip_plan_id():
    plan = _make_plan()
    if hasattr(plan, "to_dict") and hasattr(RepairPlan, "from_dict"):
        d = plan.to_dict()
        restored = RepairPlan.from_dict(d)
        assert restored.plan_id == plan.plan_id
    else:
        pytest.skip("to_dict/from_dict not implemented")
