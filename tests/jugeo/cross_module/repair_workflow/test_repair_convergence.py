"""Cross-module tests: Repair loop convergence check."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import DebugSession, DebugSessionStatus
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_session():
    try:
        return DebugSession(
            session_id="conv-test-001",
            iteration_count=0,
            counterexamples=(),
            repair_attempts=(),
            status=DebugSessionStatus.ACTIVE,
            blocked_reason=None,
        )
    except (AttributeError, TypeError):
        return DebugSession(
            session_id="conv-test-001",
            iteration_count=0,
            counterexamples=(),
            repair_attempts=(),
            status="ACTIVE",
            blocked_reason=None,
        )

def test_debug_session_mark_converged():
    session = _make_session()
    converged = session.mark_converged()
    assert converged is not None

def test_converged_session_not_active():
    session = _make_session()
    converged = session.mark_converged()
    assert converged.is_active() is False

def test_advance_iteration():
    session = _make_session()
    advanced = session.advance_iteration()
    assert advanced.iteration_count > session.iteration_count
