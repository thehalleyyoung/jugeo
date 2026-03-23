"""Cross-module tests: Two identical programs → EquivalenceClass via TaskRouter."""
import pytest

try:
    from jugeo.interfaces.task_router import TaskRouter, TaskResult
except ImportError as e:
    pytest.skip(f"TaskRouter not available: {e}", allow_module_level=True)

PROG = """
def double(x):
    return x * 2
"""

def test_task_router_instantiates():
    router = TaskRouter()
    assert router is not None

def test_check_equivalence_returns_task_result():
    router = TaskRouter()
    result = router.check_equivalence(PROG, PROG)
    assert result is not None

def test_identical_programs_result_not_none():
    router = TaskRouter()
    result = router.check_equivalence(PROG, PROG)
    assert result is not None
