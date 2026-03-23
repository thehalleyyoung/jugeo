from __future__ import annotations

from jugeo.benchmarks.loader import load_benchmark_bundle
from jugeo.interfaces.task_router import check_equivalence, check_spec_adherence, detect_bugs


def test_task_router_equivalence_examples_score_perfect_accuracy() -> None:
    bundle = load_benchmark_bundle()

    correct = 0
    for case in bundle.equivalence_cases:
        result = check_equivalence(
            case.left_program,
            case.right_program,
            input_cover=[point.to_dict() for point in case.input_cover],
        )
        payload = result.payload if isinstance(result.payload, dict) else {}
        correct += int(payload.get("equivalent") is case.expected_equivalent)

    assert correct == len(bundle.equivalence_cases) == 100


def test_task_router_spec_examples_score_perfect_accuracy() -> None:
    bundle = load_benchmark_bundle()

    correct = 0
    for case in bundle.spec_cases:
        result = check_spec_adherence(
            case.program,
            case.spec_program,
            input_cover=[point.to_dict() for point in case.input_cover],
        )
        payload = result.payload if isinstance(result.payload, dict) else {}
        correct += int(payload.get("adheres") is case.expected_satisfies)

    assert correct == len(bundle.spec_cases) == 100


def test_task_router_bug_examples_score_perfect_accuracy() -> None:
    bundle = load_benchmark_bundle()

    correct = 0
    for case in bundle.bug_cases:
        result = detect_bugs(case.program)
        payload = result.payload if isinstance(result.payload, dict) else {}
        correct += int(set(payload.get("labels", [])) == set(case.expected_bugs))

    assert correct == len(bundle.bug_cases) == 100
