from __future__ import annotations

import json
import runpy
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from jugeo.benchmarks.loader import BUG_SUITE, EQUIVALENCE_SUITE, SPEC_SUITE, load_benchmark_bundle
from jugeo.benchmarks import loader as benchmark_loader
from jugeo.benchmarks.models import BenchmarkBundle, BugCase, EquivalenceCase, InputPoint, SpecCase
from jugeo.benchmarks.runner import (
    _check_spec_case,
    _compare_extensional_equality,
    _detect_bugs,
    run_all_benchmarks,
    run_bug_benchmark,
)
from jugeo.benchmarks.validation import (
    BENCHMARK_DECLARED_COVER_MIN_POINTS,
    SUPPORTED_BUG_LABELS,
    SUPPORTED_RELATION_FAMILIES,
    SUPPORTED_RELATION_FAMILY_PROPERTIES,
    _validate_spec_functions_return_booleans,
)

ROOT = Path(__file__).resolve().parents[3]


def _case_family(case_id: str) -> str:
    return case_id.split("-")[1]


def _benchmark_scalar_cover(*, start: int = 0) -> tuple[InputPoint, ...]:
    return tuple(InputPoint(args=(value,)) for value in range(start, start + BENCHMARK_DECLARED_COVER_MIN_POINTS))


def _benchmark_rows_cover() -> tuple[InputPoint, ...]:
    return tuple(
        InputPoint(
            args=([{"values": [index, index + 1]}, {"values": [index + 2, index + 3]}],),
            kwargs={"bias": (index % 3) - 1},
        )
        for index in range(1, BENCHMARK_DECLARED_COVER_MIN_POINTS + 1)
    )


def test_benchmark_suite_sizes_and_balance() -> None:
    bundle = load_benchmark_bundle()

    assert len(bundle.equivalence_cases) == 100
    assert sum(case.expected_equivalent for case in bundle.equivalence_cases) == 50
    assert len(bundle.spec_cases) == 100
    assert sum(case.expected_satisfies for case in bundle.spec_cases) == 50
    assert len(bundle.bug_cases) == 100
    assert sum(bool(case.expected_bugs) for case in bundle.bug_cases) == 50


def test_checked_in_suite_metadata_matches_requested_shape() -> None:
    equivalence_payload = json.loads(EQUIVALENCE_SUITE.read_text())
    spec_payload = json.loads(SPEC_SUITE.read_text())
    bug_payload = json.loads(BUG_SUITE.read_text())

    assert equivalence_payload["metadata"]["benchmark_semantics"] == "extensional-equality-on-declared-cover"
    assert equivalence_payload["metadata"]["composition"] == {
        "total_cases": 100,
        "equivalent_cases": 50,
        "non_equivalent_cases": 50,
        "declared_cover_min_points": equivalence_payload["metadata"]["composition"]["declared_cover_min_points"],
        "declared_cover_max_points": equivalence_payload["metadata"]["composition"]["declared_cover_max_points"],
    }
    assert equivalence_payload["metadata"]["composition"]["declared_cover_min_points"] == BENCHMARK_DECLARED_COVER_MIN_POINTS
    assert equivalence_payload["metadata"]["composition"]["declared_cover_max_points"] == BENCHMARK_DECLARED_COVER_MIN_POINTS

    assert spec_payload["metadata"]["benchmark_semantics"] == "boolean-returning-specification-on-declared-cover"
    assert spec_payload["metadata"]["composition"] == {
        "total_cases": 100,
        "satisfying_cases": 50,
        "unsatisfying_cases": 50,
        "declared_cover_min_points": spec_payload["metadata"]["composition"]["declared_cover_min_points"],
        "declared_cover_max_points": spec_payload["metadata"]["composition"]["declared_cover_max_points"],
    }
    assert spec_payload["metadata"]["composition"]["declared_cover_min_points"] == BENCHMARK_DECLARED_COVER_MIN_POINTS
    assert spec_payload["metadata"]["composition"]["declared_cover_max_points"] == BENCHMARK_DECLARED_COVER_MIN_POINTS
    assert spec_payload["metadata"]["spec_contract"] == "spec(result, *args, **kwargs) -> bool"

    assert bug_payload["metadata"]["benchmark_semantics"] == "common-python-bug-checking"
    assert bug_payload["metadata"]["composition"] == {
        "total_cases": 100,
        "bug_positive_cases": 50,
        "bug_negative_cases": 50,
        "multi_bug_cases": bug_payload["metadata"]["composition"]["multi_bug_cases"],
    }
    assert bug_payload["metadata"]["composition"]["multi_bug_cases"] >= 6
    assert sorted(bug_payload["metadata"]["bug_labels"]) == sorted(SUPPORTED_BUG_LABELS)


def test_generated_programs_are_longish() -> None:
    bundle = load_benchmark_bundle()

    assert min(case.left_program.count("\n") for case in bundle.equivalence_cases) >= 24
    assert min(case.right_program.count("\n") for case in bundle.equivalence_cases) >= 24
    assert min(case.program.count("\n") for case in bundle.spec_cases) >= 22
    assert min(case.spec_program.count("\n") for case in bundle.spec_cases) >= 22
    assert min(case.program.count("\n") for case in bundle.bug_cases) >= 8


def test_bug_suite_contains_multi_bug_examples() -> None:
    bundle = load_benchmark_bundle()

    multi_bug_cases = [
        case for case in bundle.bug_cases
        if len(set(case.expected_bugs)) >= 2
    ]
    assert len(multi_bug_cases) >= 6


def test_equivalence_and_spec_declared_covers_have_contract_cardinality() -> None:
    bundle = load_benchmark_bundle()

    assert min(len(case.input_cover) for case in bundle.equivalence_cases) == BENCHMARK_DECLARED_COVER_MIN_POINTS
    assert max(len(case.input_cover) for case in bundle.equivalence_cases) == BENCHMARK_DECLARED_COVER_MIN_POINTS
    assert min(len(case.input_cover) for case in bundle.spec_cases) == BENCHMARK_DECLARED_COVER_MIN_POINTS
    assert max(len(case.input_cover) for case in bundle.spec_cases) == BENCHMARK_DECLARED_COVER_MIN_POINTS


def test_benchmark_suites_cover_multiple_semantic_families() -> None:
    bundle = load_benchmark_bundle()

    assert len({_case_family(case.case_id) for case in bundle.equivalence_cases}) >= 7
    assert len({_case_family(case.case_id) for case in bundle.spec_cases}) >= 7
    assert len({_case_family(case.case_id) for case in bundle.bug_cases}) >= 7


def test_benchmark_suite_case_ids_are_unique() -> None:
    bundle = load_benchmark_bundle()

    assert len({case.case_id for case in bundle.equivalence_cases}) == len(bundle.equivalence_cases)
    assert len({case.case_id for case in bundle.spec_cases}) == len(bundle.spec_cases)
    assert len({case.case_id for case in bundle.bug_cases}) == len(bundle.bug_cases)


def test_bug_suite_covers_expected_bug_labels() -> None:
    bundle = load_benchmark_bundle()

    assert {
        label
        for case in bundle.bug_cases
        for label in case.expected_bugs
    } == SUPPORTED_BUG_LABELS


def test_runner_and_validator_share_supported_vocabularies() -> None:
    from jugeo.benchmarks.runner import BUG_KIND_MAP

    assert frozenset(BUG_KIND_MAP) == SUPPORTED_BUG_LABELS
    assert SUPPORTED_RELATION_FAMILIES == frozenset({"extensional-equality-on-declared-cover"})
    assert SUPPORTED_RELATION_FAMILY_PROPERTIES["extensional-equality-on-declared-cover"]["support_scope"] == (
        "declared-finite-cover"
    )


def test_benchmark_runner_reaches_perfect_f1() -> None:
    reports = run_all_benchmarks()

    for report in reports.values():
        assert report.metrics.precision == 1.0
        assert report.metrics.recall == 1.0
        assert report.metrics.f1 == 1.0
        assert report.metrics.accuracy == 1.0


def test_bug_benchmark_execution_suppresses_intentional_syntax_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report = run_bug_benchmark()

    assert report.metrics.f1 == 1.0
    assert not [warning for warning in caught if issubclass(warning.category, SyntaxWarning)]


def test_negative_cases_emit_witnesses_when_the_relation_fails() -> None:
    reports = run_all_benchmarks()

    failed_equivalence = [j for j in reports["equivalence"].judgments if not j.predicted]
    failed_specs = [j for j in reports["spec"].judgments if not j.predicted]
    assert all(j.witness is not None for j in failed_equivalence)
    assert all(j.witness is not None for j in failed_specs)
    assert all(j.witness.cover_index is not None for j in failed_equivalence)
    assert all(j.witness.cover_index is not None for j in failed_specs)
    assert all(j.witness.coordinate is not None for j in failed_equivalence)
    assert all(j.witness.coordinate is not None for j in failed_specs)
    assert all(j.obstruction_class == "H1/declared-cover-mismatch" for j in failed_equivalence)
    assert all(j.obstruction_class == "H1/specification-obstruction" for j in failed_specs)
    assert all(j.residual_obligations for j in failed_equivalence)
    assert all(j.residual_obligations for j in failed_specs)
    assert all("equivalence." in j.witness.coordinate for j in failed_equivalence if j.witness is not None)
    assert all("spec." in j.witness.coordinate for j in failed_specs if j.witness is not None)


def test_spec_suite_includes_exception_witness_pressure_cases() -> None:
    bundle = load_benchmark_bundle()

    guard_cases = [case for case in bundle.spec_cases if case.case_id.startswith("spec-guard-unsat-")]
    assert guard_cases
    predicted, witness = _check_spec_case(guard_cases[0])
    assert predicted is False
    assert witness is not None
    assert "raised" in witness.message


def test_equivalence_requires_supported_relation_family() -> None:
    case = EquivalenceCase(
        case_id="unsupported-relation",
        description="unsupported relation family",
        relation_family="unsupported-relation-family",
        left_program="def solve(x):\n    return x\n",
        right_program="def solve(x):\n    return x\n",
        input_cover=(InputPoint(args=(1,)),),
        expected_equivalent=True,
    )

    try:
        _compare_extensional_equality(case)
    except ValueError as exc:
        assert "unsupported equivalence relation family" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("unsupported relation family should raise ValueError")


def test_spec_checker_requires_boolean_returning_specs() -> None:
    case = SpecCase(
        case_id="non-boolean-spec",
        description="spec must return bool",
        program="def solve(x):\n    return x + 1\n",
        spec_program="def spec(result, x):\n    return 1\n",
        input_cover=_benchmark_scalar_cover(start=1),
        expected_satisfies=False,
    )

    predicted, witness = _check_spec_case(case)
    assert predicted is False
    assert witness is not None
    assert "must return a boolean" in witness.message


def test_spec_validation_requires_at_least_one_realized_cover_value() -> None:
    with pytest.raises(ValueError, match="must realize at least one value on the declared finite cover"):
        _validate_spec_functions_return_booleans(
            [
                {
                    "case_id": "always-raising-spec-case",
                    "program": "def solve(x):\n    raise RuntimeError('boom')\n",
                    "spec_program": "def spec(result, x):\n    return 'not a bool'\n",
                    "input_cover": [point.to_dict() for point in _benchmark_scalar_cover(start=1)],
                }
            ]
        )


def test_equivalence_checker_requires_a_ten_point_declared_cover() -> None:
    case = EquivalenceCase(
        case_id="too-small-cover",
        description="cover must have at least ten points",
        relation_family="extensional-equality-on-declared-cover",
        left_program="def solve(x):\n    return x\n",
        right_program="def solve(x):\n    return x\n",
        input_cover=tuple(InputPoint(args=(value,)) for value in range(BENCHMARK_DECLARED_COVER_MIN_POINTS - 1)),
        expected_equivalent=True,
    )

    try:
        _compare_extensional_equality(case)
    except ValueError as exc:
        assert f"at least {BENCHMARK_DECLARED_COVER_MIN_POINTS} points" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("underspecified finite cover should raise ValueError")


def test_spec_checker_requires_a_ten_point_declared_cover() -> None:
    case = SpecCase(
        case_id="too-small-spec-cover",
        description="cover must have at least ten points",
        program="def solve(x):\n    return x\n",
        spec_program="def spec(result, x):\n    return result == x\n",
        input_cover=tuple(InputPoint(args=(value,)) for value in range(BENCHMARK_DECLARED_COVER_MIN_POINTS - 1)),
        expected_satisfies=True,
    )

    try:
        _check_spec_case(case)
    except ValueError as exc:
        assert f"at least {BENCHMARK_DECLARED_COVER_MIN_POINTS} points" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("underspecified finite cover should raise ValueError")


def test_bug_detector_handles_common_python_bug_variants() -> None:
    bug_case = BugCase(
        case_id="mutable-call-default",
        description="mutable default created via list()",
        program=(
            "def solve(value, bucket=list()):\n"
            "    bucket.append(int(value))\n"
            "    return tuple(bucket)\n"
        ),
        expected_bugs=("mutable-default",),
    )
    clean_case = BugCase(
        case_id="late-bound-default-frozen",
        description="nested function freezes loop variable in default",
        program=(
            "def build(values):\n"
            "    callbacks = []\n"
            "    for factor in values:\n"
            "        def apply(item, factor=factor):\n"
            "            return item + factor\n"
            "        callbacks.append(apply)\n"
            "    return callbacks\n"
        ),
        expected_bugs=(),
    )
    identity_case = BugCase(
        case_id="identity-tuple-literal",
        description="tuple literal identity comparison is unreliable",
        program=(
            "def solve(text):\n"
            "    left, right = text.split(':')\n"
            "    pair = (left, right)\n"
            "    return pair is ('a', 'b')\n"
        ),
        expected_bugs=("identity-literal",),
    )
    shadow_case = BugCase(
        case_id="shadow-builtin-parameter",
        description="parameter named after builtin",
        program=(
            "def solve(sum, values):\n"
            "    running_total = int(sum)\n"
            "    for value in values:\n"
            "        running_total += int(value)\n"
            "    return running_total\n"
        ),
        expected_bugs=("shadow-builtin",),
    )
    loop_shadow_case = BugCase(
        case_id="shadow-builtin-loop-target",
        description="loop target named after builtin",
        program=(
            "def solve(values):\n"
            "    callbacks = []\n"
            "    for sum in values:\n"
            "        callbacks.append(lambda item: item + sum)\n"
            "    return callbacks\n"
        ),
        expected_bugs=("shadow-builtin", "late-binding-closure"),
    )
    bytearray_case = BugCase(
        case_id="mutable-bytearray-default",
        description="bytearray default is mutable shared state",
        program=(
            "def solve(value, bucket=bytearray()):\n"
            "    bucket.extend(bytes([int(value) % 251]))\n"
            "    return tuple(bucket)\n"
        ),
        expected_bugs=("mutable-default",),
    )
    comprehension_case = BugCase(
        case_id="late-bound-comprehension",
        description="list comprehension lambda captures loop variable late",
        program=(
            "def build(values):\n"
            "    return [lambda item: item + factor for factor in values]\n"
        ),
        expected_bugs=("late-binding-closure",),
    )
    frozen_comprehension_case = BugCase(
        case_id="late-bound-comprehension-frozen",
        description="list comprehension lambda freezes loop variable in default",
        program=(
            "def build(values):\n"
            "    return [lambda item, factor=factor: item + factor for factor in values]\n"
        ),
        expected_bugs=(),
    )
    tuple_open_case = BugCase(
        case_id="tuple-open-without-close",
        description="tuple unpacking still leaks file handles if never closed",
        program=(
            "def solve(path):\n"
            "    left, right = open(path, 'r', encoding='utf-8'), open(path, 'r', encoding='utf-8')\n"
            "    return left.read() + right.read()\n"
        ),
        expected_bugs=("open-without-close",),
    )

    assert set(_detect_bugs(bug_case)[0]) == {"mutable-default"}
    assert _detect_bugs(clean_case)[0] == ()
    assert set(_detect_bugs(identity_case)[0]) == {"identity-literal"}
    assert set(_detect_bugs(shadow_case)[0]) == {"shadow-builtin"}
    assert set(_detect_bugs(loop_shadow_case)[0]) == {"shadow-builtin", "late-binding-closure"}
    assert set(_detect_bugs(bytearray_case)[0]) == {"mutable-default"}
    assert set(_detect_bugs(comprehension_case)[0]) == {"late-binding-closure"}
    assert _detect_bugs(frozen_comprehension_case)[0] == ()
    assert set(_detect_bugs(tuple_open_case)[0]) == {"open-without-close"}


def test_bug_detector_reports_structural_counterexample_metadata() -> None:
    case = BugCase(
        case_id="mutable-default-metadata",
        description="mutable default metadata should be preserved in the report",
        program=(
            "def solve(value, bucket=[]):\n"
            "    bucket.append(int(value))\n"
            "    return tuple(bucket)\n"
        ),
        expected_bugs=("mutable-default",),
    )

    labels, reports = _detect_bugs(case)

    assert labels == ("mutable-default",)
    assert len(reports) == 1
    counterexample = reports[0].counterexample
    assert counterexample["bug_code"] == "mutable-default"
    assert counterexample["coordinate"] == reports[0].coordinate
    assert counterexample["lineno"] == 1
    assert counterexample["node_type"] in {"List", "Call"}
    assert counterexample["evidence_kind"] == "ast-pattern"
    assert counterexample["provenance"]["case_id"] == case.case_id


def test_bug_detector_reports_repeated_bug_occurrences() -> None:
    case = BugCase(
        case_id="repeated-bug-occurrences",
        description="repeated benchmark bug instances should each retain their own witness",
        program=(
            "def alpha(value, left=[]):\n"
            "    left.append(value)\n"
            "    return tuple(left)\n"
            "\n"
            "def beta(value, right=[]):\n"
            "    right.append(value)\n"
            "    return tuple(right)\n"
            "\n"
            "def gamma(path):\n"
            "    first = open(path, 'r', encoding='utf-8')\n"
            "    second = open(path, 'r', encoding='utf-8')\n"
            "    return first.readline() + second.readline()\n"
        ),
        expected_bugs=("mutable-default", "open-without-close"),
    )

    labels, reports = _detect_bugs(case)

    assert labels.count("mutable-default") == 2
    assert labels.count("open-without-close") == 2
    assert len(reports) == 4
    assert [report.counterexample["lineno"] for report in reports if report.counterexample["bug_code"] == "mutable-default"] == [1, 5]
    assert [report.counterexample["lineno"] for report in reports if report.counterexample["bug_code"] == "open-without-close"] == [10, 11]


def test_bug_detector_catches_nested_mutable_defaults_and_namedexpr_leaks() -> None:
    nested_mutable_case = BugCase(
        case_id="tuple-nested-mutable-default",
        description="tuple-wrapped mutable defaults still leak shared state",
        program=(
            "def solve(value, state=({'items': []},)):\n"
            "    state[0]['items'].append(int(value))\n"
            "    return tuple(state[0]['items'])\n"
        ),
        expected_bugs=("mutable-default",),
    )
    namedexpr_open_case = BugCase(
        case_id="namedexpr-open-without-close",
        description="walrus-bound file handles still need an explicit close or context manager",
        program=(
            "def solve(path):\n"
            "    if (handle := open(path, 'r', encoding='utf-8')):\n"
            "        return handle.readline().strip()\n"
            "    return ''\n"
        ),
        expected_bugs=("open-without-close",),
    )

    assert set(_detect_bugs(nested_mutable_case)[0]) == {"mutable-default"}
    assert set(_detect_bugs(namedexpr_open_case)[0]) == {"open-without-close"}


def test_bug_detector_handles_shadowing_and_negative_identity_variants() -> None:
    function_shadow_case = BugCase(
        case_id="function-shadow-builtin",
        description="builtin-shadowing function names should be surfaced",
        program=(
            "def list(values):\n"
            "    total = 0\n"
            "    for value in values:\n"
            "        total += int(value)\n"
            "    return total\n"
        ),
        expected_bugs=("shadow-builtin",),
    )
    with_shadow_case = BugCase(
        case_id="with-shadow-builtin",
        description="with-targets named after builtins should be surfaced",
        program=(
            "def solve(path):\n"
            "    with open(path, 'r', encoding='utf-8') as list:\n"
            "        return list.readline().strip()\n"
        ),
        expected_bugs=("shadow-builtin",),
    )
    exception_shadow_case = BugCase(
        case_id="except-shadow-builtin",
        description="exception aliases named after builtins should be surfaced",
        program=(
            "def solve(value):\n"
            "    try:\n"
            "        return 10 // int(value)\n"
            "    except ZeroDivisionError as list:\n"
            "        return str(list)\n"
        ),
        expected_bugs=("shadow-builtin",),
    )
    comprehension_shadow_case = BugCase(
        case_id="comprehension-shadow-builtin",
        description="comprehension targets named after builtins should be surfaced",
        program=(
            "def solve(values):\n"
            "    return [list + 1 for list in values]\n"
        ),
        expected_bugs=("shadow-builtin",),
    )
    negative_identity_case = BugCase(
        case_id="negative-identity-literal",
        description="identity comparisons against signed literals are unreliable",
        program=(
            "def solve(value):\n"
            "    return value is -1\n"
        ),
        expected_bugs=("identity-literal",),
    )

    assert set(_detect_bugs(function_shadow_case)[0]) == {"shadow-builtin"}
    assert set(_detect_bugs(with_shadow_case)[0]) == {"shadow-builtin"}
    assert set(_detect_bugs(exception_shadow_case)[0]) == {"shadow-builtin"}
    assert set(_detect_bugs(comprehension_shadow_case)[0]) == {"shadow-builtin"}
    assert set(_detect_bugs(negative_identity_case)[0]) == {"identity-literal"}


def test_bug_detector_treats_with_managed_handles_as_closed() -> None:
    managed_case = BugCase(
        case_id="with-managed-handle",
        description="handles re-entered through with should not be flagged as leaks",
        program=(
            "def solve(path):\n"
            "    handle = open(path, 'r', encoding='utf-8')\n"
            "    with handle:\n"
            "        return handle.readline().strip()\n"
        ),
        expected_bugs=(),
    )

    assert _detect_bugs(managed_case)[0] == ()


def test_bug_detector_requires_unconditional_close_calls() -> None:
    conditional_close_case = BugCase(
        case_id="conditional-close-leak",
        description="conditionally closed handles still leak on uncovered control-flow branches",
        program=(
            "def solve(path, should_close):\n"
            "    handle = open(path, 'r', encoding='utf-8')\n"
            "    if should_close:\n"
            "        handle.close()\n"
            "    return should_close\n"
        ),
        expected_bugs=("open-without-close",),
    )
    finally_close_case = BugCase(
        case_id="finally-close-safe",
        description="finally blocks provide an unconditional close witness",
        program=(
            "def solve(path):\n"
            "    handle = open(path, 'r', encoding='utf-8')\n"
            "    try:\n"
            "        return handle.readline().strip()\n"
            "    finally:\n"
            "        handle.close()\n"
        ),
        expected_bugs=(),
    )

    assert set(_detect_bugs(conditional_close_case)[0]) == {"open-without-close"}
    assert _detect_bugs(finally_close_case)[0] == ()


def test_bug_detector_tracks_branch_local_resource_management() -> None:
    safe_branch_case = BugCase(
        case_id="branch-local-open-close-safe",
        description="opening and closing within the same branch should not be flagged",
        program=(
            "def solve(path, should_read):\n"
            "    if should_read:\n"
            "        handle = open(path, 'r', encoding='utf-8')\n"
            "        try:\n"
            "            return handle.readline().strip()\n"
            "        finally:\n"
            "            handle.close()\n"
            "    return ''\n"
        ),
        expected_bugs=(),
    )
    leaked_branch_case = BugCase(
        case_id="branch-local-open-close-leak",
        description="opening inside a branch without a matching close still leaks",
        program=(
            "def solve(path, should_read):\n"
            "    if should_read:\n"
            "        handle = open(path, 'r', encoding='utf-8')\n"
            "        return handle.readline().strip()\n"
            "    return ''\n"
        ),
        expected_bugs=("open-without-close",),
    )
    rebound_handle_case = BugCase(
        case_id="rebound-open-handle-leak",
        description="rebinding an open handle before closing the previous one leaks the first resource",
        program=(
            "def solve(path):\n"
            "    handle = open(path, 'r', encoding='utf-8')\n"
            "    handle = open(path, 'r', encoding='utf-8')\n"
            "    handle.close()\n"
            "    return path\n"
        ),
        expected_bugs=("open-without-close",),
    )

    assert _detect_bugs(safe_branch_case)[0] == ()
    assert set(_detect_bugs(leaked_branch_case)[0]) == {"open-without-close"}
    assert set(_detect_bugs(rebound_handle_case)[0]) == {"open-without-close"}


def test_bug_benchmark_tolerates_duplicate_bug_instances_inside_one_program() -> None:
    bundle = BenchmarkBundle(
        equivalence_cases=(),
        spec_cases=(),
        bug_cases=(
            BugCase(
                case_id="duplicate-bug-program",
                description="duplicate instances of the same label should still count as one correct program-level judgment",
                program=(
                    "def left(value, bucket=[]):\n"
                    "    bucket.append(value)\n"
                    "    return tuple(bucket)\n"
                    "\n"
                    "def right(value, other=[]):\n"
                    "    other.append(value)\n"
                    "    return tuple(other)\n"
                ),
                expected_bugs=("mutable-default",),
            ),
        ),
    )

    report = run_bug_benchmark(bundle)

    assert report.metrics.total_cases == 1
    assert report.metrics.correct_cases == 1
    assert report.metrics.precision == 1.0
    assert report.metrics.recall == 1.0
    assert report.judgments[0].predicted == ("mutable-default", "mutable-default")


def test_bug_benchmark_metrics_score_exact_program_level_judgments() -> None:
    bundle = BenchmarkBundle(
        equivalence_cases=(),
        spec_cases=(),
        bug_cases=(
            BugCase(
                case_id="bug-metric-correct-positive",
                description="correct positive exact-match judgment",
                program="def solve(value, bucket=[]):\n    bucket.append(value)\n    return tuple(bucket)\n",
                expected_bugs=("mutable-default",),
            ),
            BugCase(
                case_id="bug-metric-mislabeled-positive",
                description="predicted buggy but with the wrong label set",
                program="def solve(value):\n    try:\n        return value\n    except:\n        return 0\n",
                expected_bugs=("mutable-default",),
            ),
            BugCase(
                case_id="bug-metric-false-positive",
                description="clean expectation with a detected bug",
                program="def solve(value):\n    list = [value]\n    return tuple(list)\n",
                expected_bugs=(),
            ),
            BugCase(
                case_id="bug-metric-false-negative",
                description="buggy expectation with a clean program",
                program="def solve(value):\n    return value + 1\n",
                expected_bugs=("bare-except",),
            ),
        ),
    )

    report = run_bug_benchmark(bundle)

    assert report.metrics.total_cases == 4
    assert report.metrics.correct_cases == 1
    assert report.metrics.true_positives == 1
    assert report.metrics.false_positives == 2
    assert report.metrics.false_negatives == 2
    assert report.metrics.precision == pytest.approx(1 / 3)
    assert report.metrics.recall == pytest.approx(1 / 3)
    assert report.metrics.f1 == pytest.approx(1 / 3)
    assert report.metrics.accuracy == pytest.approx(0.25)


def test_equivalence_checker_isolates_declared_cover_points() -> None:
    case = EquivalenceCase(
        case_id="fresh-cover-equivalence",
        description="cover points should be checked in fresh execution environments",
        relation_family="extensional-equality-on-declared-cover",
        left_program=(
            "def solve(value, bucket=[]):\n"
            "    bucket.append(int(value))\n"
            "    snapshot = []\n"
            "    for item in bucket:\n"
            "        snapshot.append(item)\n"
            "    return tuple(snapshot)\n"
        ),
        right_program=(
            "def solve(value):\n"
            "    bucket = [int(value)]\n"
            "    snapshot = []\n"
            "    for item in bucket:\n"
            "        snapshot.append(item)\n"
            "    return tuple(snapshot)\n"
        ),
        input_cover=_benchmark_scalar_cover(start=1),
        expected_equivalent=True,
    )

    predicted, witness = _compare_extensional_equality(case)
    assert predicted is True
    assert witness is None


def test_equivalence_checker_isolates_nested_mutable_inputs_between_programs() -> None:
    case = EquivalenceCase(
        case_id="fresh-cover-mutable-input-equivalence",
        description="left and right implementations should see independent deep copies of each declared cover point",
        relation_family="extensional-equality-on-declared-cover",
        left_program=(
            "def solve(rows, bias=0):\n"
            "    total = 0\n"
            "    for entry in rows:\n"
            "        values = entry['values']\n"
            "        while values:\n"
            "            total += int(values.pop(0))\n"
            "        total += bias\n"
            "    return total\n"
        ),
        right_program=(
            "def solve(rows, bias=0):\n"
            "    total = 0\n"
            "    for entry in rows:\n"
            "        values = entry['values']\n"
            "        while values:\n"
            "            total += int(values.pop())\n"
            "        total += bias\n"
            "    return total\n"
        ),
        input_cover=_benchmark_rows_cover(),
        expected_equivalent=True,
    )

    predicted, witness = _compare_extensional_equality(case)
    assert predicted is True
    assert witness is None


def test_spec_checker_isolates_declared_cover_points() -> None:
    case = SpecCase(
        case_id="fresh-cover-spec",
        description="spec checks should not inherit state across cover points",
        program=(
            "def solve(value, bucket=[]):\n"
            "    bucket.append(int(value))\n"
            "    snapshot = []\n"
            "    for item in bucket:\n"
            "        snapshot.append(item)\n"
            "    return tuple(snapshot)\n"
        ),
        spec_program=(
            "def spec(result, value):\n"
            "    expected = (int(value),)\n"
            "    return result == expected\n"
        ),
        input_cover=_benchmark_scalar_cover(start=4),
        expected_satisfies=True,
    )

    predicted, witness = _check_spec_case(case)
    assert predicted is True
    assert witness is None


def test_spec_checker_preserves_original_mutable_inputs_for_spec_evaluation() -> None:
    case = SpecCase(
        case_id="fresh-cover-mutable-input-spec",
        description="spec evaluation should receive the original declared cover point, not the mutated runtime copy",
        program=(
            "def solve(rows, bias=0):\n"
            "    total = 0\n"
            "    for entry in rows:\n"
            "        values = entry['values']\n"
            "        while values:\n"
            "            total += int(values.pop(0))\n"
            "        total += bias\n"
            "    return total\n"
        ),
        spec_program=(
            "def spec(result, rows, bias=0):\n"
            "    expected = 0\n"
            "    for entry in rows:\n"
            "        expected += sum(int(value) for value in entry['values']) + bias\n"
            "    return result == expected\n"
        ),
        input_cover=_benchmark_rows_cover(),
        expected_satisfies=True,
    )

    predicted, witness = _check_spec_case(case)
    assert predicted is True
    assert witness is None


def test_declared_cover_points_must_be_distinct() -> None:
    equivalence_case = EquivalenceCase(
        case_id="duplicate-cover-equivalence",
        description="duplicate points are not an admissible declared cover",
        relation_family="extensional-equality-on-declared-cover",
        left_program="def solve(x):\n    return x\n",
        right_program="def solve(x):\n    return x\n",
        input_cover=(InputPoint(args=(1,)),) + _benchmark_scalar_cover(start=1),
        expected_equivalent=True,
    )

    try:
        _compare_extensional_equality(equivalence_case)
    except ValueError as exc:
        assert "distinct points" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("duplicate cover points should be rejected for equivalence")

    spec_case = SpecCase(
        case_id="duplicate-cover-spec",
        description="duplicate points are not an admissible declared cover",
        program="def solve(x):\n    return x\n",
        spec_program="def spec(result, x):\n    return result == x\n",
        input_cover=(InputPoint(args=(1,)),) + _benchmark_scalar_cover(start=1),
        expected_satisfies=True,
    )

    try:
        _check_spec_case(spec_case)
    except ValueError as exc:
        assert "distinct points" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("duplicate cover points should be rejected for specs")


def test_spec_validation_allows_some_raising_cover_points_when_one_value_is_realized() -> None:
    _validate_spec_functions_return_booleans(
        [
            {
                "case_id": "spec-mixed-program-validation",
                "program": (
                    "def solve(x):\n"
                    "    if x < 0:\n"
                    "        raise ValueError('boom')\n"
                    "    return {'ok': x % 2 == 0}\n"
                ),
                "spec_program": "def spec(result, x):\n    return isinstance(result['ok'], bool)\n",
                "input_cover": [
                    {"args": [-1], "kwargs": {}},
                    {"args": [0], "kwargs": {}},
                    {"args": [1], "kwargs": {}},
                ],
            }
        ]
    )


def test_generated_suites_match_checked_in_payloads() -> None:
    module = runpy.run_path(str(ROOT / "test_examples" / "build_suites.py"))

    assert module["build_equivalence_suite"]() == json.loads(EQUIVALENCE_SUITE.read_text())
    assert module["build_spec_suite"]() == json.loads(SPEC_SUITE.read_text())
    assert module["build_bug_suite"]() == json.loads(BUG_SUITE.read_text())


def test_build_suites_main_recreates_all_suite_files(tmp_path) -> None:
    module = runpy.run_path(str(ROOT / "test_examples" / "build_suites.py"))
    module["main"].__globals__["ROOT"] = tmp_path

    module["main"]()

    assert json.loads((tmp_path / "equivalence_suite.json").read_text()) == module["build_equivalence_suite"]()
    assert json.loads((tmp_path / "spec_suite.json").read_text()) == module["build_spec_suite"]()
    assert json.loads((tmp_path / "bug_suite.json").read_text()) == module["build_bug_suite"]()


def test_checked_in_suite_payloads_pass_generator_validation() -> None:
    module = runpy.run_path(str(ROOT / "test_examples" / "build_suites.py"))

    module["validate_suite_payloads"](
        {
            EQUIVALENCE_SUITE: json.loads(EQUIVALENCE_SUITE.read_text()),
            SPEC_SUITE: json.loads(SPEC_SUITE.read_text()),
            BUG_SUITE: json.loads(BUG_SUITE.read_text()),
        }
    )


def test_loader_rejects_invalid_suite_payloads(tmp_path, monkeypatch) -> None:
    bad_suite = tmp_path / "equivalence_suite.json"
    bad_suite.write_text(json.dumps({"suite": "equivalence", "cases": []}))
    monkeypatch.setattr(benchmark_loader, "EQUIVALENCE_SUITE", bad_suite)

    try:
        benchmark_loader.load_equivalence_suite()
    except ValueError as exc:
        assert "exactly 100 cases" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("invalid benchmark suites should be rejected during load")


def test_loader_rejects_equivalence_cases_without_solve_functions(tmp_path, monkeypatch) -> None:
    bad_suite = tmp_path / "equivalence_suite.json"
    payload = json.loads(EQUIVALENCE_SUITE.read_text())
    payload["cases"][0]["left_program"] = (
        "def _coerce(value):\n"
        "    if isinstance(value, bool):\n"
        "        return int(value)\n"
        "    return value\n"
        "\n"
        "def _snapshot(values):\n"
        "    copied = []\n"
        "    for value in values:\n"
        "        copied.append(_coerce(value))\n"
        "    return tuple(copied)\n"
        "\n"
        "def _marker(*values):\n"
        "    return (0, len(values))\n"
        "\n"
        "def helper(values, bias, mod, keep):\n"
        "    cleaned = []\n"
        "    for value in values:\n"
        "        cleaned.append(int(value))\n"
        "    eligible = []\n"
        "    for value in cleaned:\n"
        "        if value % mod == keep:\n"
        "            eligible.append(value)\n"
        "    total = 0\n"
        "    for value in eligible:\n"
        "        total += value + bias\n"
        "    return total\n"
    )
    bad_suite.write_text(json.dumps(payload))
    monkeypatch.setattr(benchmark_loader, "EQUIVALENCE_SUITE", bad_suite)

    try:
        benchmark_loader.load_equivalence_suite()
    except ValueError as exc:
        assert "'solve' is missing" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("equivalence programs without solve should be rejected during load")


def test_loader_rejects_non_boolean_expected_flags(tmp_path, monkeypatch) -> None:
    bad_equivalence = tmp_path / "equivalence_suite.json"
    payload = json.loads(EQUIVALENCE_SUITE.read_text())
    payload["cases"][0]["expected_equivalent"] = "true"
    bad_equivalence.write_text(json.dumps(payload))
    monkeypatch.setattr(benchmark_loader, "EQUIVALENCE_SUITE", bad_equivalence)

    try:
        benchmark_loader.load_equivalence_suite()
    except ValueError as exc:
        assert "boolean expected_equivalent" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("truthy non-boolean equivalence expectations should be rejected")

    bad_spec = tmp_path / "spec_suite.json"
    payload = json.loads(SPEC_SUITE.read_text())
    payload["cases"][0]["expected_satisfies"] = 1
    bad_spec.write_text(json.dumps(payload))
    monkeypatch.setattr(benchmark_loader, "SPEC_SUITE", bad_spec)

    try:
        benchmark_loader.load_spec_suite()
    except ValueError as exc:
        assert "boolean expected_satisfies" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("truthy non-boolean spec expectations should be rejected")


def test_loader_rejects_unknown_bug_labels(tmp_path, monkeypatch) -> None:
    bad_suite = tmp_path / "bug_suite.json"
    payload = json.loads(BUG_SUITE.read_text())
    payload["cases"][0]["expected_bugs"] = ["mutable-default", "imaginary-bug"]
    bad_suite.write_text(json.dumps(payload))
    monkeypatch.setattr(benchmark_loader, "BUG_SUITE", bad_suite)

    try:
        benchmark_loader.load_bug_suite()
    except ValueError as exc:
        assert "unsupported bug labels" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("unknown bug labels should be rejected during load")


def test_benchmark_cli_reports_perfect_metrics_from_repo_root() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "jugeo.benchmarks.runner"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert set(payload) == {"equivalence", "spec", "bug"}
    for metrics in payload.values():
        assert metrics["precision"] == 1.0
        assert metrics["recall"] == 1.0
        assert metrics["f1"] == 1.0
        assert metrics["accuracy"] == 1.0
