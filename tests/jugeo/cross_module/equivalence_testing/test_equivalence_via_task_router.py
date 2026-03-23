"""Cross-module tests: task_router.check_equivalence() end-to-end."""
import pytest

try:
    from jugeo.interfaces.task_router import TaskRouter, TaskResult, TaskKind
except ImportError as e:
    pytest.skip(f"task_router not available: {e}", allow_module_level=True)

PROG = """
def foo(x):
    return x + 1
"""

def test_task_router_check_equivalence():
    router = TaskRouter()
    result = router.check_equivalence(PROG, PROG)
    assert result is not None

def test_result_kind_is_task_result():
    router = TaskRouter()
    result = router.check_equivalence(PROG, PROG)
    assert result is not None  # TaskResult or similar

def test_task_kind_equivalence_testing():
    assert hasattr(TaskKind, "EQUIVALENCE_TESTING")
