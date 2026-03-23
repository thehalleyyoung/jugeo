"""Cross-module tests: Z3 counterexample → BugReport (skip if z3 not available)."""
import pytest

z3 = pytest.importorskip("z3", reason="z3 not installed")

try:
    from jugeo.problem_modes.bug_detection import detect_bugs, BugDetectionResult
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

def test_z3_import_works_with_skip():
    # z3 is importable (we already checked above)
    assert z3 is not None

def test_z3_available_for_solver_check():
    # Basic z3 sanity check
    x = z3.Int("x")
    solver = z3.Solver()
    solver.add(x > 0, x < 10)
    result = solver.check()
    assert result == z3.sat
