"""Cross-module tests: RepairValidator governance."""
import pytest
import time

try:
    from jugeo.problem_modes.repair_semantics import RepairValidator, RepairPlan
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_plan():
    return RepairPlan(
        plan_id="governance-plan-001",
        from_counterexample="cx-001",
        steps=(),
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=0.5,
    )

@pytest.fixture
def validator():
    return RepairValidator(validator_id="validator-001", rules=())

@pytest.fixture
def plan():
    return _make_plan()

def test_repair_validator_construction(validator):
    assert validator is not None
    assert validator.validator_id == "validator-001"

def test_validate_plan_returns_result(validator, plan):
    result = validator.validate_plan(plan)
    assert result is not None

def test_check_admissibility(validator, plan):
    result = validator.check_admissibility(plan)
    assert result is not None
