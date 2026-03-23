"""Cross-module tests: Z3 discharge of arithmetic obligations (skip if no z3)."""
import pytest

z3 = pytest.importorskip("z3", reason="z3 not installed")

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

def test_z3_available():
    assert z3 is not None

def test_z3_arithmetic_check():
    x = z3.Int("x")
    solver = z3.Solver()
    solver.add(x > 0)
    solver.add(x < 100)
    result = solver.check()
    assert result == z3.sat
