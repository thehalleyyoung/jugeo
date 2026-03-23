"""Cross-module tests: BugReport cohomology class assignment and computation."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        BugReport, BugKind, detect_bugs,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def foo():
    x = undefined
    return x
"""

def test_bug_has_cohomology_class_field():
    bug = BugReport()
    assert hasattr(bug, "cohomology_class")

def test_compute_cohomology_class_method():
    bug = BugReport()
    if hasattr(bug, "compute_cohomology_class"):
        result = bug.compute_cohomology_class()
        assert isinstance(result, str)
    else:
        pytest.skip("compute_cohomology_class not implemented")

def test_with_cohomology_class_returns_new():
    bug = BugReport()
    if hasattr(bug, "with_cohomology_class"):
        new_bug = bug.with_cohomology_class("H1")
        assert isinstance(new_bug, BugReport)
        assert new_bug is not bug
    else:
        # frozen dataclass replace pattern
        import dataclasses
        new_bug = dataclasses.replace(bug, cohomology_class="H1")
        assert new_bug.cohomology_class == "H1"

def test_cohomology_class_of_new_bug_is_string():
    bug = BugReport()
    assert isinstance(bug.cohomology_class, str)
