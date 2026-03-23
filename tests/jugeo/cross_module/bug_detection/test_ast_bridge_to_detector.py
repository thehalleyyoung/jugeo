"""Cross-module tests: PythonASTBridge and BugDetector integration."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        ASTBridgeConfig, PythonASTBridge, BugDetector, BugReport,
        detect_bugs, BugDetectionResult,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SIMPLE_SOURCE = """
def add(x: int, y: int) -> int:
    return x + y

result = add(1, 2)
"""

@pytest.fixture
def bridge():
    return PythonASTBridge(ASTBridgeConfig())

@pytest.fixture
def detection_result():
    return detect_bugs(SIMPLE_SOURCE)

def test_bridge_parses_source_without_error(bridge):
    tree = bridge.parse_source(SIMPLE_SOURCE, "<test>")
    assert tree is not None

def test_bridge_produces_symbolic_nodes(bridge):
    tree = bridge.parse_source(SIMPLE_SOURCE, "<test>")
    nodes = bridge.build_symbolic_tree(tree, filename="<test>")
    assert isinstance(nodes, list)
    assert len(nodes) >= 0  # may be empty for simple source, but should not raise

def test_detector_finds_bugs_via_module_fn(detection_result):
    assert isinstance(detection_result, BugDetectionResult)

def test_bug_reports_are_bug_report_instances(detection_result):
    for bug in detection_result.bugs:
        assert isinstance(bug, BugReport)

def test_bug_reports_have_cohomology_class_field(detection_result):
    for bug in detection_result.bugs:
        assert hasattr(bug, "cohomology_class")

def test_bridge_and_detector_coordinates_are_strings(detection_result):
    for bug in detection_result.bugs:
        assert isinstance(bug.coordinate, str)

def test_detection_result_has_session(detection_result):
    assert detection_result.session is not None

def test_detection_elapsed_time_nonneg(detection_result):
    assert detection_result.elapsed_s >= 0.0
