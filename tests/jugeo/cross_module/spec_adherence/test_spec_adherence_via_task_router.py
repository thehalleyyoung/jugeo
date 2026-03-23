"""Cross-module tests: task_router.check_spec_adherence() end-to-end."""
import pytest

try:
    from jugeo.interfaces.task_router import TaskRouter, TaskKind
except ImportError as e:
    pytest.skip(f"task_router not available: {e}", allow_module_level=True)

SOURCE = """
def add(x: int, y: int) -> int:
    return x + y
"""

SPEC_STR = "The function must return the sum of x and y. Precondition: both x and y are integers."

def test_check_spec_adherence_runs():
    router = TaskRouter()
    result = router.check_spec_adherence(SOURCE, SPEC_STR)
    assert result is not None

def test_result_not_none():
    router = TaskRouter()
    result = router.check_spec_adherence(SOURCE, SPEC_STR)
    assert result is not None

def test_task_kind_spec_adherence():
    assert hasattr(TaskKind, "SPEC_ADHERENCE")
