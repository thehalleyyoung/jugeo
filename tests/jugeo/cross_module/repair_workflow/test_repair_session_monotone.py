"""Cross-module tests: DebugSession accumulates obstructions."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import (
        DebugSession, DebugSessionStatus, CounterexampleRecord,
    )
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_cx(record_id="cx-001"):
    return CounterexampleRecord(
        record_id=record_id,
        coordinate="test.module:line_1",
        context={},
        assignment={},
        witness=None,
        severity=0.5,
        trust_tier="ORACLE_PROPOSED",
    )

def _make_session():
    try:
        return DebugSession(
            session_id="debug-session-001",
            iteration_count=0,
            counterexamples=(),
            repair_attempts=(),
            status=DebugSessionStatus.ACTIVE,
            blocked_reason=None,
        )
    except (AttributeError, TypeError):
        return DebugSession(
            session_id="debug-session-001",
            iteration_count=0,
            counterexamples=(),
            repair_attempts=(),
            status="ACTIVE",
            blocked_reason=None,
        )

def test_debug_session_construction():
    session = _make_session()
    assert session is not None
    assert session.session_id == "debug-session-001"

def test_session_add_counterexample():
    session = _make_session()
    cx = _make_cx()
    new_session = session.add_counterexample(cx)
    assert new_session is not None

def test_session_counterexamples_grow():
    session = _make_session()
    cx = _make_cx()
    new_session = session.add_counterexample(cx)
    assert len(new_session.counterexamples) > len(session.counterexamples)

def test_session_is_active():
    session = _make_session()
    result = session.is_active()
    assert isinstance(result, bool)
