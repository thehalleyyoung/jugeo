from __future__ import annotations

import pytest

from jugeo.benchmarks.loader import load_benchmark_bundle
from jugeo.benchmarks.validation import BENCHMARK_DECLARED_COVER_MIN_POINTS
from jugeo.interfaces import task_router as task_router_module
from jugeo.interfaces.task_router import TaskRouter, route_request


def _fallback_rows_cover() -> list[dict[str, object]]:
    return [
        {
            "args": [[{"values": [index, index + 1]}, {"values": [index + 2, index + 3]}]],
            "kwargs": {"bias": (index % 3) - 1},
        }
        for index in range(1, BENCHMARK_DECLARED_COVER_MIN_POINTS + 1)
    ]


def test_task_router_uses_declared_cover_equivalence_semantics() -> None:
    bundle = load_benchmark_bundle()
    case = next(case for case in bundle.equivalence_cases if case.case_id == "eq-affine-neq-00")

    result = TaskRouter().check_equivalence(
        case.left_program,
        case.right_program,
        input_cover=[point.to_dict() for point in case.input_cover],
    )

    assert result.status == "partial"
    assert result.trust_tier == "RUNTIME_WITNESSED"
    assert result.payload["equivalent"] is False
    assert result.payload["analysis_method"] == "declared_cover_extensional"
    assert "declared finite cover" in result.payload["witness"]["message"]


def test_route_request_preserves_declared_cover_for_spec_execution() -> None:
    bundle = load_benchmark_bundle()
    case = next(case for case in bundle.spec_cases if case.case_id.startswith("spec-guard-unsat-"))

    result = route_request(
        {
            "kind": "spec",
            "source": case.program,
            "spec": case.spec_program,
            "input_cover": [point.to_dict() for point in case.input_cover],
        }
    )

    assert result["status"] == "partial"
    assert result["trust_tier"] == "RUNTIME_WITNESSED"
    assert result["payload"]["adheres"] is False
    assert result["payload"]["analysis_method"] == "declared_cover_spec_execution"
    assert "raised" in result["payload"]["witness"]["message"]


def test_task_router_uses_comprehensive_bug_detector() -> None:
    bundle = load_benchmark_bundle()
    case = next(case for case in bundle.bug_cases if case.case_id.startswith("bug-hybrid-bug-"))

    result = TaskRouter().detect_bugs(case.program)

    assert result.status == "partial"
    assert result.trust_tier == "ORACLE_PROPOSED"
    assert result.payload["analysis_method"] == "benchmark_bug_detector"
    assert set(result.payload["labels"]) == set(case.expected_bugs)


def test_task_router_matches_full_benchmark_suites() -> None:
    bundle = load_benchmark_bundle()
    router = TaskRouter()

    for case in bundle.equivalence_cases:
        result = router.check_equivalence(
            case.left_program,
            case.right_program,
            input_cover=[point.to_dict() for point in case.input_cover],
        )
        assert result.payload["equivalent"] is case.expected_equivalent, case.case_id

    for case in bundle.spec_cases:
        result = route_request(
            {
                "kind": "spec",
                "source": case.program,
                "spec": case.spec_program,
                "input_cover": [point.to_dict() for point in case.input_cover],
            }
        )
        assert result["payload"]["adheres"] is case.expected_satisfies, case.case_id

    for case in bundle.bug_cases:
        result = router.detect_bugs(case.program)
        assert set(result.payload["labels"]) == set(case.expected_bugs), case.case_id


def test_task_router_declared_cover_fallback_isolates_mutable_inputs(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_compare_extensional_equality", None)
    monkeypatch.setattr(task_router_module, "_benchmark_check_spec_case", None)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)

    equivalence_result = TaskRouter().check_equivalence(
        """
def solve(rows, bias=0):
    total = 0
    for entry in rows:
        values = entry['values']
        while values:
            total += int(values.pop(0))
        total += bias
    return total
""",
        """
def solve(rows, bias=0):
    total = 0
    for entry in rows:
        values = entry['values']
        while values:
            total += int(values.pop())
        total += bias
    return total
""",
        input_cover=_fallback_rows_cover(),
    )

    assert equivalence_result.status == "success"
    assert equivalence_result.payload["equivalent"] is True

    spec_result = route_request(
        {
            "kind": "spec",
            "source": """
def solve(rows, bias=0):
    total = 0
    for entry in rows:
        values = entry['values']
        while values:
            total += int(values.pop(0))
        total += bias
    return total
""",
            "spec": """
def spec(result, rows, bias=0):
    expected = 0
    for entry in rows:
        expected += sum(int(value) for value in entry['values']) + bias
    return result == expected
""",
            "input_cover": _fallback_rows_cover(),
        }
    )

    assert spec_result["status"] == "success"
    assert spec_result["payload"]["adheres"] is True


def test_task_router_declared_cover_fallback_preserves_semantic_coordinates(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_compare_extensional_equality", None)
    monkeypatch.setattr(task_router_module, "_benchmark_check_spec_case", None)

    bundle = load_benchmark_bundle()
    equivalence_case = next(case for case in bundle.equivalence_cases if case.case_id == "eq-affine-neq-00")
    spec_case = next(case for case in bundle.spec_cases if case.case_id.startswith("spec-guard-unsat-"))

    equivalence_result = TaskRouter().check_equivalence(
        equivalence_case.left_program,
        equivalence_case.right_program,
        input_cover=[point.to_dict() for point in equivalence_case.input_cover],
    )
    spec_result = route_request(
        {
            "kind": "spec",
            "source": spec_case.program,
            "spec": spec_case.spec_program,
            "input_cover": [point.to_dict() for point in spec_case.input_cover],
        }
    )

    assert equivalence_result.payload["equivalent"] is False
    assert "equivalence.affine.left.00|equivalence.affine.right.00#cover[" in equivalence_result.payload["witness"]["coordinate"]
    assert spec_result["payload"]["adheres"] is False
    assert spec_result["payload"]["witness"]["coordinate"].startswith("spec.guard.program.")


def test_task_router_rejects_underspecified_declared_cover_before_dispatch() -> None:
    result = TaskRouter().check_equivalence(
        "def solve(x):\n    return x\n",
        "def solve(x):\n    return x\n",
        input_cover=[{"args": [index], "kwargs": {}} for index in range(BENCHMARK_DECLARED_COVER_MIN_POINTS - 1)],
    )

    assert result.status == "failed"
    assert any(f"at least {BENCHMARK_DECLARED_COVER_MIN_POINTS} input points" in error for error in result.errors)


def test_task_router_rejects_non_string_keyword_names_in_declared_cover() -> None:
    result = route_request(
        {
            "kind": "spec",
            "source": "def solve(*, value):\n    return value\n",
            "spec": "def spec(result, *, value):\n    return result == value\n",
            "input_cover": [{"args": [], "kwargs": {1: "bad"}} for _ in range(BENCHMARK_DECLARED_COVER_MIN_POINTS)],
        }
    )

    assert result["status"] == "failed"
    assert any("string-keyed mapping" in error for error in result["errors"])


def test_task_router_bug_fallback_detects_namedexpr_leaks(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)

    result = TaskRouter().detect_bugs(
        """
def solve(path):
    if (handle := open(path, 'r', encoding='utf-8')):
        return handle.readline().strip()
    return ''
"""
    )

    assert result.status == "partial"
    assert set(result.payload["labels"]) == {"open-without-close"}


def test_task_router_bug_fallback_detects_tuple_wrapped_mutable_defaults(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)

    result = TaskRouter().detect_bugs(
        """
def solve(value, state=({'items': []},)):
    state[0]['items'].append(int(value))
    return tuple(state[0]['items'])
"""
    )

    assert result.status == "partial"
    assert set(result.payload["labels"]) == {"mutable-default"}


def test_task_router_bug_fallback_handles_shadowing_and_negative_identity_variants(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)

    shadow_result = TaskRouter().detect_bugs(
        """
def solve(path):
    with open(path, 'r', encoding='utf-8') as list:
        return list.readline().strip()
"""
    )
    identity_result = TaskRouter().detect_bugs(
        """
def solve(value):
    return value is -1
"""
    )

    assert shadow_result.status == "partial"
    assert set(shadow_result.payload["labels"]) == {"shadow-builtin"}
    assert identity_result.status == "partial"
    assert set(identity_result.payload["labels"]) == {"identity-literal"}


def test_task_router_bug_fallback_treats_with_managed_handles_as_closed(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)

    result = TaskRouter().detect_bugs(
        """
def solve(path):
    handle = open(path, 'r', encoding='utf-8')
    with handle:
        return handle.readline().strip()
"""
    )

    assert result.status == "success"
    assert result.payload["labels"] == []


def test_task_router_bug_fallback_requires_unconditional_close_calls(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bug_observations", None)

    leak_result = TaskRouter().detect_bugs(
        """
def solve(path, should_close):
    handle = open(path, 'r', encoding='utf-8')
    if should_close:
        handle.close()
    return should_close
"""
    )
    safe_result = TaskRouter().detect_bugs(
        """
def solve(path):
    handle = open(path, 'r', encoding='utf-8')
    try:
        return handle.readline().strip()
    finally:
        handle.close()
"""
    )

    assert leak_result.status == "partial"
    assert set(leak_result.payload["labels"]) == {"open-without-close"}
    assert safe_result.status == "success"
    assert safe_result.payload["labels"] == []


def test_task_router_bug_fallback_handles_branch_local_open_patterns(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bug_observations", None)

    safe_result = TaskRouter().detect_bugs(
        """
def solve(path, should_read):
    if should_read:
        handle = open(path, 'r', encoding='utf-8')
        try:
            return handle.readline().strip()
        finally:
            handle.close()
    return ''
"""
    )
    leaked_result = TaskRouter().detect_bugs(
        """
def solve(path, should_read):
    if should_read:
        handle = open(path, 'r', encoding='utf-8')
        return handle.readline().strip()
    return ''
"""
    )
    rebound_result = TaskRouter().detect_bugs(
        """
def solve(path):
    handle = open(path, 'r', encoding='utf-8')
    handle = open(path, 'r', encoding='utf-8')
    handle.close()
    return path
"""
    )

    assert safe_result.status == "success"
    assert safe_result.payload["labels"] == []
    assert leaked_result.status == "partial"
    assert set(leaked_result.payload["labels"]) == {"open-without-close"}
    assert rebound_result.status == "partial"
    assert set(rebound_result.payload["labels"]) == {"open-without-close"}


def test_task_router_bug_fallback_preserves_repeated_bug_observations(monkeypatch) -> None:
    monkeypatch.setattr(task_router_module, "_BENCHMARKS_AVAILABLE", False)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bugs", None)
    monkeypatch.setattr(task_router_module, "_benchmark_detect_bug_observations", None)

    result = TaskRouter().detect_bugs(
        """
def alpha(value, bucket=[]):
    bucket.append(value)
    return tuple(bucket)

def beta(value, other=[]):
    other.append(value)
    return tuple(other)
"""
    )

    assert result.status == "partial"
    assert result.payload["labels"] == ["mutable-default", "mutable-default"]
    assert result.payload["error_count"] == 2
