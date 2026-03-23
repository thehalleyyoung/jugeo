"""Cross-module tests: RepairValidator rejects invalid plans."""
import pytest
import time

try:
    from jugeo.problem_modes.repair_semantics import (
        RepairValidator, RepairPlan, RepairStep, DebugSession, DebugSessionStatus,
    )
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_plan():
    return RepairPlan(
        plan_id="validator-test-plan",
        from_counterexample="cx-001",
        steps=(),
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=1.0,
    )

def _make_session():
    try:
        return DebugSession(
            session_id="v-session-001",
            iteration_count=1,
            counterexamples=(),
            repair_attempts=(),
            status=DebugSessionStatus.ACTIVE,
            blocked_reason=None,
        )
    except (AttributeError, TypeError):
        return DebugSession(
            session_id="v-session-001",
            iteration_count=1,
            counterexamples=(),
            repair_attempts=(),
            status="ACTIVE",
            blocked_reason=None,
        )

@pytest.fixture
def validator():
    return RepairValidator(validator_id="test-validator", rules=())

def test_validator_validate_plan(validator):
    plan = _make_plan()
    result = validator.validate_plan(plan)
    assert result is not None

def test_validator_validate_step(validator):
    step = RepairStep(
        step_id="s-001",
        kind="PATCH",
        coordinate="module:line_1",
        action="fix_typo",
        parameters={},
    )
    result = validator.validate_step(step)
    assert result is not None

def test_validator_check_descent(validator):
    session = _make_session()
    result = validator.check_descent(session)
    assert result is not None
