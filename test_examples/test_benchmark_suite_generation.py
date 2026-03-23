from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from jugeo.benchmarks.validation import validate_suite_payloads
from test_examples.build_suites import build_bug_suite, build_equivalence_suite, build_spec_suite


ROOT = Path(__file__).resolve().parent


def _read_payload(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text())


def test_checked_in_benchmark_suites_match_builder_output() -> None:
    payloads = {
        ROOT / "equivalence_suite.json": build_equivalence_suite(),
        ROOT / "spec_suite.json": build_spec_suite(),
        ROOT / "bug_suite.json": build_bug_suite(),
    }

    validate_suite_payloads(payloads)

    assert payloads[ROOT / "equivalence_suite.json"] == _read_payload("equivalence_suite.json")
    assert payloads[ROOT / "spec_suite.json"] == _read_payload("spec_suite.json")
    assert payloads[ROOT / "bug_suite.json"] == _read_payload("bug_suite.json")


def test_checked_in_benchmark_suites_publish_metadata() -> None:
    for name, semantics, family_count in (
        ("equivalence_suite.json", "extensional-equality-on-declared-cover", 8),
        ("spec_suite.json", "boolean-returning-specification-on-declared-cover", 8),
        ("bug_suite.json", "common-python-bug-checking", 7),
    ):
        payload = _read_payload(name)
        metadata = payload["metadata"]
        assert metadata["schema_version"] == 1
        assert metadata["theory_source"] == "preliminaries/theory2.tex"
        assert metadata["benchmark_semantics"] == semantics
        assert metadata["family_count"] == family_count
        assert len(metadata["families"]) == family_count


def test_checked_in_benchmark_suites_match_requested_contract() -> None:
    equivalence = _read_payload("equivalence_suite.json")["metadata"]
    assert equivalence["composition"] == {
        "total_cases": 100,
        "equivalent_cases": 50,
        "non_equivalent_cases": 50,
        "declared_cover_min_points": 10,
        "declared_cover_max_points": 10,
    }
    assert equivalence["longish_programs"]["left_program_min_lines"] >= 39
    assert equivalence["longish_programs"]["right_program_min_lines"] >= 39
    assert equivalence["certificate_projection"] == "declared-cover-observables-only"
    assert set(equivalence["families"]) == {
        "affine",
        "gaps",
        "guard",
        "matrix",
        "mutation",
        "records",
        "streak",
        "words",
    }

    spec = _read_payload("spec_suite.json")["metadata"]
    assert spec["composition"] == {
        "total_cases": 100,
        "satisfying_cases": 50,
        "unsatisfying_cases": 50,
        "declared_cover_min_points": 10,
        "declared_cover_max_points": 10,
    }
    assert spec["longish_programs"]["program_min_lines"] >= 37
    assert spec["longish_programs"]["spec_program_min_lines"] >= 37
    assert spec["spec_contract"] == "spec(result, *args, **kwargs) -> bool"
    assert spec["cover_truth_requirement"] == "spec must hold on every declared cover point"
    assert set(spec["families"]) == {
        "affine",
        "gaps",
        "guard",
        "matrix",
        "mutation",
        "records",
        "streak",
        "words",
    }

    bug = _read_payload("bug_suite.json")["metadata"]
    assert bug["composition"]["total_cases"] == 100
    assert bug["composition"]["bug_positive_cases"] == 50
    assert bug["composition"]["bug_negative_cases"] == 50
    assert bug["composition"]["multi_bug_cases"] >= 10
    assert bug["longish_programs"]["program_min_lines"] >= 28
    assert set(bug["families"]) == {"except", "hybrid", "identity", "late", "mutable", "open", "shadow"}
    assert set(bug["bug_labels"]) == {
        "bare-except",
        "identity-literal",
        "late-binding-closure",
        "mutable-default",
        "open-without-close",
        "shadow-builtin",
    }


def test_validation_rejects_semantically_mislabelled_equivalence_case() -> None:
    payload = copy.deepcopy(build_equivalence_suite())
    eq_index = next(index for index, case in enumerate(payload["cases"]) if case["expected_equivalent"])
    neq_index = next(index for index, case in enumerate(payload["cases"]) if not case["expected_equivalent"])
    payload["cases"][eq_index]["expected_equivalent"] = False
    payload["cases"][neq_index]["expected_equivalent"] = True

    with pytest.raises(ValueError, match="expected_equivalent disagrees with declared-cover execution semantics"):
        validate_suite_payloads({ROOT / "equivalence_suite.json": payload})


def test_validation_rejects_semantically_mislabelled_spec_case() -> None:
    payload = copy.deepcopy(build_spec_suite())
    sat_index = next(index for index, case in enumerate(payload["cases"]) if case["expected_satisfies"])
    unsat_index = next(index for index, case in enumerate(payload["cases"]) if not case["expected_satisfies"])
    payload["cases"][sat_index]["expected_satisfies"] = False
    payload["cases"][unsat_index]["expected_satisfies"] = True

    with pytest.raises(ValueError, match="expected_satisfies disagrees with declared-cover execution semantics"):
        validate_suite_payloads({ROOT / "spec_suite.json": payload})


def test_validation_rejects_semantically_mislabelled_bug_case() -> None:
    payload = copy.deepcopy(build_bug_suite())
    positive_index = next(index for index, case in enumerate(payload["cases"]) if case["expected_bugs"])
    negative_index = next(index for index, case in enumerate(payload["cases"]) if not case["expected_bugs"])
    payload["cases"][negative_index]["expected_bugs"] = list(payload["cases"][positive_index]["expected_bugs"])
    payload["cases"][positive_index]["expected_bugs"] = []

    with pytest.raises(ValueError, match="expected_bugs disagrees with benchmark bug-label semantics"):
        validate_suite_payloads({ROOT / "bug_suite.json": payload})


def test_validation_rejects_spec_case_that_never_realizes_declared_cover_values() -> None:
    payload = copy.deepcopy(build_spec_suite())
    unsat_index = next(index for index, case in enumerate(payload["cases"]) if not case["expected_satisfies"])
    program_source = payload["cases"][unsat_index]["program"]
    spec_source = payload["cases"][unsat_index]["spec_program"]
    program_prefix, _, _ = str(program_source).partition("def solve(")
    spec_prefix, _, _ = str(spec_source).partition("def spec(")
    payload["cases"][unsat_index]["program"] = (
        program_prefix
        + "def solve(*args, **kwargs):\n"
        + "    raise RuntimeError('no section realized on the declared cover')\n"
    )
    payload["cases"][unsat_index]["spec_program"] = (
        spec_prefix
        + "def spec(result, *args, **kwargs):\n"
        + "    return 'not a bool'\n"
    )

    with pytest.raises(ValueError, match="must realize at least one value on the declared finite cover"):
        validate_suite_payloads({ROOT / "spec_suite.json": payload})


def test_validation_rejects_equivalence_case_with_malformed_coordinate_runtime_contract() -> None:
    payload = copy.deepcopy(build_equivalence_suite())
    case_index = next(index for index, case in enumerate(payload["cases"]) if case["case_id"] == "eq-affine-eq-00")
    left_program = str(payload["cases"][case_index]["left_program"])
    payload["cases"][case_index]["left_program"] = left_program.replace(
        'return "equivalence.affine.left.00"',
        'return "equivalence.affine.left.bad"',
        1,
    )

    with pytest.raises(ValueError, match="must return semantic coordinate 'equivalence\\.affine\\.left\\.00'"):
        validate_suite_payloads({ROOT / "equivalence_suite.json": payload})


def test_validation_rejects_spec_case_with_non_tuple_marker_summary() -> None:
    payload = copy.deepcopy(build_spec_suite())
    case_index = next(index for index, case in enumerate(payload["cases"]) if case["case_id"].startswith("spec-affine-sat-"))
    spec_program = str(payload["cases"][case_index]["spec_program"])
    payload["cases"][case_index]["spec_program"] = spec_program.replace(
        "return (0, len(values), len(_spec_affine_spec_0_descent_profile(values)))",
        "return len(values)",
        1,
    )

    with pytest.raises(ValueError, match="semantic marker helper must return a tuple-valued descent summary"):
        validate_suite_payloads({ROOT / "spec_suite.json": payload})
