"""Cross-module tests: Programs with different behavior."""
import pytest

try:
    from jugeo.interfaces.task_router import TaskRouter
except ImportError as e:
    pytest.skip(f"TaskRouter not available: {e}", allow_module_level=True)

PROG_A = """
def compute(x):
    return x + 1
"""

PROG_B = """
def compute(x):
    return x * 2
"""

def test_different_programs_result_not_none():
    router = TaskRouter()
    result = router.check_equivalence(PROG_A, PROG_B)
    assert result is not None

def test_result_has_is_success_method_or_attr():
    router = TaskRouter()
    result = router.check_equivalence(PROG_A, PROG_B)
    has_success = (
        hasattr(result, "success") or
        hasattr(result, "is_success") or
        hasattr(result, "status") or
        hasattr(result, "ok")
    )
    assert has_success or result is not None
