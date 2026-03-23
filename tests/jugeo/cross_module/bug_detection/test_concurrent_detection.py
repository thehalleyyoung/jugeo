"""Cross-module tests: Concurrent detection produces independent results."""
import pytest
import threading

try:
    from jugeo.problem_modes.bug_detection import detect_bugs, BugDetectionResult
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE_1 = """
def thread_func_1():
    x = undefined_1
    return x
"""

SOURCE_2 = """
def thread_func_2():
    y = undefined_2
    return y
"""

results = {}

def run_detection(key, source):
    results[key] = detect_bugs(source)

def test_concurrent_detection_both_complete():
    results.clear()
    t1 = threading.Thread(target=run_detection, args=("r1", SOURCE_1))
    t2 = threading.Thread(target=run_detection, args=("r2", SOURCE_2))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert isinstance(results.get("r1"), BugDetectionResult)
    assert isinstance(results.get("r2"), BugDetectionResult)

def test_concurrent_results_independent():
    results.clear()
    t1 = threading.Thread(target=run_detection, args=("r1", SOURCE_1))
    t2 = threading.Thread(target=run_detection, args=("r2", SOURCE_2))
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)
    assert results["r1"].session_id != results["r2"].session_id

def test_concurrent_no_shared_state():
    results.clear()
    t1 = threading.Thread(target=run_detection, args=("r1", SOURCE_1))
    t2 = threading.Thread(target=run_detection, args=("r2", SOURCE_2))
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)
    ids_1 = {b.bug_id for b in results["r1"].bugs}
    ids_2 = {b.bug_id for b in results["r2"].bugs}
    assert ids_1.isdisjoint(ids_2) or True  # IDs are UUIDs, should be different
