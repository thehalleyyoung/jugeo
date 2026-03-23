"""Cross-module tests: Z3 integration with counterexample records (skip if no z3)."""
import pytest

z3 = pytest.importorskip("z3", reason="z3 not installed")

try:
    from jugeo.problem_modes.repair_semantics import CounterexampleRecord
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def test_z3_available_for_counterexample():
    # z3 is importable; test basic z3 sat check as counterexample witness
    x = z3.Int("x")
    solver = z3.Solver()
    solver.add(x > 0, x < 5)
    result = solver.check()
    assert result == z3.sat
    
    # Get the model as a witness
    model = solver.model()
    witness = {"x": model[x].as_long()}
    
    cx = CounterexampleRecord(
        record_id="z3-cx-001",
        coordinate="test:line_1",
        context={},
        assignment={"x": witness["x"]},
        witness=witness,
        severity=0.9,
        trust_tier="ORACLE_PROPOSED",
    )
    assert cx.witness is not None
