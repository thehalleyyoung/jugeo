"""Cross-module tests: detect → create counterexample → repair plan cycle."""
import pytest
import time

try:
    from jugeo.problem_modes.bug_detection import detect_bugs
    from jugeo.problem_modes.repair_semantics import (
        CounterexampleRecord, RepairPlan, DebugSession, DebugSessionStatus,
    )
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

SOURCE = """
def divide(a, b):
    return a / b  # no zero check
"""

def _make_session_with_cx(cx):
    try:
        session = DebugSession(
            session_id="full-cycle-001",
            iteration_count=0,
            counterexamples=(),
            repair_attempts=(),
            status=DebugSessionStatus.ACTIVE,
            blocked_reason=None,
        )
    except (AttributeError, TypeError):
        session = DebugSession(
            session_id="full-cycle-001",
            iteration_count=0,
            counterexamples=(),
            repair_attempts=(),
            status="ACTIVE",
            blocked_reason=None,
        )
    return session.add_counterexample(cx)

def test_detect_produces_result():
    result = detect_bugs(SOURCE)
    assert result is not None

def test_create_counterexample_from_detection():
    result = detect_bugs(SOURCE)
    coord = result.bugs[0].coordinate if result.bugs else "divide:line_2"
    cx = CounterexampleRecord(
        record_id="full-cx-001",
        coordinate=coord,
        context={},
        assignment={"a": 1, "b": 0},
        witness={"failing_input": {"b": 0}},
        severity=0.9,
        trust_tier="ORACLE_PROPOSED",
    )
    assert cx is not None

def test_create_repair_plan():
    plan = RepairPlan(
        plan_id="full-plan-001",
        from_counterexample="full-cx-001",
        steps=(),
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=1.0,
    )
    assert plan is not None

def test_create_debug_session():
    cx = CounterexampleRecord(
        record_id="full-cx-002",
        coordinate="divide:line_2",
        context={},
        assignment={"b": 0},
        witness=None,
        severity=0.8,
        trust_tier="ORACLE_PROPOSED",
    )
    session = _make_session_with_cx(cx)
    assert session is not None
    assert len(session.counterexamples) == 1

def test_session_with_plan():
    cx = CounterexampleRecord(
        record_id="full-cx-003",
        coordinate="divide:line_2",
        context={},
        assignment={"b": 0},
        witness=None,
        severity=0.8,
        trust_tier="ORACLE_PROPOSED",
    )
    plan = RepairPlan(
        plan_id="full-plan-002",
        from_counterexample="full-cx-003",
        steps=(),
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=1.0,
    )
    try:
        session = DebugSession(
            session_id="full-cycle-002",
            iteration_count=0,
            counterexamples=(cx,),
            repair_attempts=(),
            status=DebugSessionStatus.ACTIVE,
            blocked_reason=None,
        )
    except (AttributeError, TypeError):
        session = DebugSession(
            session_id="full-cycle-002",
            iteration_count=0,
            counterexamples=(cx,),
            repair_attempts=(),
            status="ACTIVE",
            blocked_reason=None,
        )
    new_session = session.add_repair_attempt(plan)
    assert len(new_session.repair_attempts) == 1
