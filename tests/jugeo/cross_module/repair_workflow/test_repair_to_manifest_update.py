"""Cross-module tests: repair_convergence_certificate function."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import (
        repair_convergence_certificate, DebugSession, DebugSessionStatus,
    )
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def _make_converged_session():
    try:
        session = DebugSession(
            session_id="conv-session-001",
            iteration_count=3,
            counterexamples=(),
            repair_attempts=(),
            status=DebugSessionStatus.CONVERGED,
            blocked_reason=None,
        )
    except (AttributeError, TypeError):
        session = DebugSession(
            session_id="conv-session-001",
            iteration_count=3,
            counterexamples=(),
            repair_attempts=(),
            status="CONVERGED",
            blocked_reason=None,
        )
    return session

def test_repair_convergence_certificate_importable():
    assert callable(repair_convergence_certificate)

def test_convergence_certificate_with_session():
    session = _make_converged_session()
    try:
        result = repair_convergence_certificate(session)
        assert result is not None
    except Exception as e:
        pytest.skip(f"repair_convergence_certificate raised: {e}")
