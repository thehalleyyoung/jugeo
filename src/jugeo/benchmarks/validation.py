from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from .models import InputPoint
from .semantics import (
    BENCHMARK_DECLARED_COVER_MIN_POINTS,
    call_fresh,
    detect_bug_labels,
    load_function,
    require_declared_cover,
    semantic_coordinate,
)

SUPPORTED_RELATION_FAMILIES = frozenset({"extensional-equality-on-declared-cover"})
SUPPORTED_RELATION_FAMILY_PROPERTIES = {
    "extensional-equality-on-declared-cover": {
        "support_scope": "declared-finite-cover",
        "witness_shape": "paired-execution-outcome",
        "overlap_law": "pointwise-extensional-agreement",
        "certificate_projection": "declared-cover-observables-only",
    }
}
SUPPORTED_BUG_LABELS = frozenset(
    {
        "mutable-default",
        "bare-except",
        "late-binding-closure",
        "open-without-close",
        "shadow-builtin",
        "identity-literal",
    }
)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _case_family(case_id: str) -> str:
    parts = case_id.split("-")
    _expect(len(parts) >= 4, f"unexpected case id format: {case_id!r}")
    return parts[1]


def _case_index(case_id: str) -> int:
    parts = case_id.split("-")
    _expect(len(parts) >= 4, f"unexpected case id format: {case_id!r}")
    try:
        return int(parts[-1])
    except ValueError as exc:
        raise ValueError(f"unexpected case id format: {case_id!r}") from exc


def _program_lines(source: str) -> int:
    return source.count("\n")


def _validate_metadata(
    payload: dict[str, object],
    *,
    suite_name: str,
    cases: list[dict[str, object]],
    benchmark_semantics: str,
    composition: dict[str, object],
    longish_programs: dict[str, int],
    extra: dict[str, object] | None = None,
) -> None:
    metadata = payload.get("metadata")
    if metadata is None:
        return
    _expect(isinstance(metadata, dict), f"{suite_name} suite metadata must be a mapping")
    _expect(metadata.get("schema_version") == 1, f"{suite_name} suite metadata must declare schema version 1")
    _expect(
        metadata.get("theory_source") == "preliminaries/theory2.tex",
        f"{suite_name} suite metadata must record preliminaries/theory2.tex as its semantic source",
    )
    _expect(
        metadata.get("benchmark_semantics") == benchmark_semantics,
        f"{suite_name} suite metadata must declare benchmark semantics {benchmark_semantics!r}",
    )
    families = sorted({_case_family(str(case["case_id"])) for case in cases})
    _expect(metadata.get("family_count") == len(families), f"{suite_name} suite metadata family_count is inconsistent")
    _expect(metadata.get("families") == families, f"{suite_name} suite metadata families are inconsistent")
    _expect(metadata.get("composition") == composition, f"{suite_name} suite metadata composition is inconsistent")
    _expect(
        metadata.get("longish_programs") == longish_programs,
        f"{suite_name} suite metadata longish program summary is inconsistent",
    )
    for key, value in (extra or {}).items():
        _expect(metadata.get(key) == value, f"{suite_name} suite metadata field {key!r} is inconsistent")


def _validate_unique_case_ids(cases: list[dict[str, object]], *, suite_name: str) -> None:
    case_ids = [str(case["case_id"]) for case in cases]
    _expect(len(case_ids) == len(set(case_ids)), f"{suite_name} suite must not reuse case ids")


def _validate_declared_cover(cases: list[dict[str, object]], *, suite_name: str) -> None:
    for case in cases:
        cover = case.get("input_cover")
        _expect(
            isinstance(cover, list) and len(cover) >= BENCHMARK_DECLARED_COVER_MIN_POINTS,
            (
                f"{suite_name} case {case['case_id']!r} must declare a finite cover with at least "
                f"{BENCHMARK_DECLARED_COVER_MIN_POINTS} points"
            ),
        )
        for index, point in enumerate(cover):
            _expect(isinstance(point, dict), f"{suite_name} case {case['case_id']!r} cover point {index} must be a mapping")
            args = point.get("args", [])
            kwargs = point.get("kwargs", {})
            _expect(
                isinstance(args, list),
                f"{suite_name} case {case['case_id']!r} cover point {index} args must be a list",
            )
            _expect(
                isinstance(kwargs, dict) and all(isinstance(key, str) for key in kwargs),
                f"{suite_name} case {case['case_id']!r} cover point {index} kwargs must be a string-keyed mapping",
            )
        signatures = [
            json.dumps({"args": point.get("args", []), "kwargs": point.get("kwargs", {})}, sort_keys=True)
            for point in cover
        ]
        _expect(
            len(signatures) == len(set(signatures)),
            f"{suite_name} case {case['case_id']!r} must use distinct points in its declared finite cover",
        )


def _validate_python_source(label: str, source: str, *, case_id: str) -> None:
    try:
        ast.parse(source, filename=f"<{label}:{case_id}>")
    except SyntaxError as exc:  # pragma: no cover - defensive guard for generated payloads.
        raise ValueError(f"{label} source for case {case_id!r} is not valid Python: {exc.msg}") from exc


def _load_function(source: str, function_name: str, *, case_id: str) -> object:
    try:
        return load_function(source, function_name)
    except KeyError as exc:  # pragma: no cover - defensive guard for generated payloads.
        raise ValueError(f"{function_name!r} is missing from case {case_id!r}") from exc
    except TypeError as exc:  # pragma: no cover - defensive guard for generated payloads.
        raise ValueError(f"{function_name!r} for case {case_id!r} must be callable") from exc


def _expect_scaffold_markers(source: str, *, case_id: str, label: str, markers: tuple[str, ...]) -> None:
    missing = tuple(marker for marker in markers if marker not in source)
    _expect(
        not missing,
        f"{label} for case {case_id!r} is missing semantic scaffold markers: {', '.join(missing)}",
    )


def _count_parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = function.args
    return len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)


def _expect_scaffold_function_shapes(
    source: str,
    *,
    case_id: str,
    label: str,
    require_marker: bool,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    module = ast.parse(source, filename=f"<{label}:{case_id}>")
    functions = [node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    zero_arg_coordinates = [
        node
        for node in functions
        if "_coordinate" in node.name and _count_parameters(node) == 0 and node.args.vararg is None and node.args.kwarg is None
    ]
    support_functions = [
        node
        for node in functions
        if "_support" in node.name and _count_parameters(node) >= 1
    ]
    descent_profile_functions = [
        node
        for node in functions
        if "_descent_profile" in node.name and _count_parameters(node) >= 1
    ]
    marker_functions = [
        node
        for node in functions
        if "_marker" in node.name and (node.args.vararg is not None or _count_parameters(node) >= 1)
    ]
    _expect(
        len(zero_arg_coordinates) == 1,
        f"{label} for case {case_id!r} must define exactly one zero-argument semantic coordinate helper",
    )
    _expect(
        len(support_functions) == 1,
        f"{label} for case {case_id!r} must define exactly one semantic support helper",
    )
    _expect(
        len(descent_profile_functions) == 1,
        f"{label} for case {case_id!r} must define exactly one semantic descent-profile helper",
    )
    if require_marker:
        _expect(
            len(marker_functions) == 1,
            f"{label} for case {case_id!r} must define exactly one semantic marker helper",
        )
    return {
        "coordinate": zero_arg_coordinates[0],
        "support": support_functions[0],
        "descent_profile": descent_profile_functions[0],
        **({"marker": marker_functions[0]} if require_marker else {}),
    }


def _expected_scaffold_coordinate(*, suite_name: str, case_id: str, label: str) -> str:
    family = _case_family(case_id)
    index = _case_index(case_id)
    if suite_name == "equivalence":
        role = "left" if label == "left_program" else "right"
        return f"equivalence.{family}.{role}.{index:02d}"
    if suite_name == "spec":
        role = "program" if label == "program" else "spec"
        return f"spec.{family}.{role}.{index:02d}"
    return f"bug.*.{index:02d}"


def _validate_scaffold_runtime_contract(
    source: str,
    *,
    suite_name: str,
    case_id: str,
    label: str,
    require_marker: bool,
) -> None:
    function_nodes = _expect_scaffold_function_shapes(
        source,
        case_id=case_id,
        label=label,
        require_marker=require_marker,
    )
    expected_coordinate = _expected_scaffold_coordinate(
        suite_name=suite_name,
        case_id=case_id,
        label=label,
    )
    actual_coordinate = semantic_coordinate(source)
    if suite_name == "bug":
        _expect(
            isinstance(actual_coordinate, str)
            and actual_coordinate.startswith("bug.")
            and actual_coordinate.endswith(f".{_case_index(case_id):02d}"),
            f"{label} for case {case_id!r} must return a bug semantic coordinate ending in '.{_case_index(case_id):02d}'",
        )
    else:
        _expect(
            actual_coordinate == expected_coordinate,
            f"{label} for case {case_id!r} must return semantic coordinate {expected_coordinate!r}",
        )
    sample_values = (0, 1, True)
    support = _load_function(source, function_nodes["support"].name, case_id=case_id)(sample_values)
    _expect(
        isinstance(support, tuple),
        f"{label} for case {case_id!r} semantic support helper must return a tuple",
    )
    _expect(
        all(isinstance(item, tuple) and len(item) == 3 for item in support),
        f"{label} for case {case_id!r} semantic support helper must return coordinate-offset-value triples",
    )
    _expect(
        all(item[0] == actual_coordinate for item in support),
        f"{label} for case {case_id!r} semantic support helper must use the declared semantic coordinate",
    )
    descent_profile = _load_function(source, function_nodes["descent_profile"].name, case_id=case_id)(sample_values)
    _expect(
        isinstance(descent_profile, tuple),
        f"{label} for case {case_id!r} semantic descent-profile helper must return a tuple",
    )
    _expect(
        len(descent_profile) == len(support),
        f"{label} for case {case_id!r} semantic descent-profile helper must preserve support cardinality",
    )
    _expect(
        all(isinstance(item, tuple) and len(item) == 3 for item in descent_profile),
        f"{label} for case {case_id!r} semantic descent-profile helper must return coordinate-class-value triples",
    )
    if require_marker:
        marker_value = _load_function(source, function_nodes["marker"].name, case_id=case_id)(sample_values)
        _expect(
            isinstance(marker_value, tuple),
            f"{label} for case {case_id!r} semantic marker helper must return a tuple-valued descent summary",
        )
        _expect(
            len(marker_value) == 3,
            f"{label} for case {case_id!r} semantic marker helper must summarize index, arity, and descent size",
        )


def _validate_spec_functions_return_booleans(cases: list[dict[str, object]]) -> None:
    for case in cases:
        case_id = str(case["case_id"])
        program_source = str(case["program"])
        spec_source = str(case["spec_program"])
        solve = _load_function(program_source, "solve", case_id=case_id)
        spec = _load_function(spec_source, "spec", case_id=case_id)
        successful_points = 0
        for raw_point in case["input_cover"]:
            args = copy.deepcopy(list(raw_point.get("args", [])))
            kwargs = copy.deepcopy(dict(raw_point.get("kwargs", {})))
            try:
                result = solve(*args, **kwargs)
            except Exception:
                continue
            successful_points += 1
            try:
                spec_result = spec(result, *copy.deepcopy(args), **copy.deepcopy(kwargs))
            except Exception as exc:  # pragma: no cover - defensive guard for generated payloads.
                raise ValueError(
                    f"spec function for case {case_id!r} must be total on the declared finite cover: {exc}"
                ) from exc
            _expect(
                isinstance(spec_result, bool),
                f"spec function for case {case_id!r} must return a boolean on the declared finite cover",
            )
        _expect(
            successful_points > 0,
            f"spec case {case_id!r} must realize at least one value on the declared finite cover",
        )


def _coerce_cover(raw_cover: list[dict[str, object]], *, case_id: str) -> tuple[InputPoint, ...]:
    try:
        return tuple(InputPoint.from_dict(point) for point in raw_cover)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"case {case_id!r} declares an invalid finite cover: {exc}") from exc


def _declared_cover_equivalence(case: dict[str, object]) -> bool:
    case_id = str(case["case_id"])
    points = require_declared_cover(_coerce_cover(case["input_cover"], case_id=case_id), case_id=case_id, category="equivalence")
    left_program = str(case["left_program"])
    right_program = str(case["right_program"])
    for point in points:
        if call_fresh(left_program, "solve", point) != call_fresh(right_program, "solve", point):
            return False
    return True


def _declared_cover_spec_satisfaction(case: dict[str, object]) -> bool:
    case_id = str(case["case_id"])
    points = require_declared_cover(_coerce_cover(case["input_cover"], case_id=case_id), case_id=case_id, category="spec")
    program = str(case["program"])
    spec_program = str(case["spec_program"])
    for point in points:
        result = call_fresh(program, "solve", point)
        if result.tag != "return":
            return False
        spec = load_function(spec_program, "spec")
        spec_result = spec(result.value, *copy.deepcopy(point.args), **copy.deepcopy(point.kwargs))
        if not isinstance(spec_result, bool) or not spec_result:
            return False
    return True


def _validate_equivalence_truth_matches_declared_cover(cases: list[dict[str, object]]) -> None:
    for case in cases:
        actual = _declared_cover_equivalence(case)
        expected = bool(case["expected_equivalent"])
        _expect(
            actual is expected,
            f"equivalence case {case['case_id']!r} expected_equivalent disagrees with declared-cover execution semantics",
        )


def _validate_spec_truth_matches_declared_cover(cases: list[dict[str, object]]) -> None:
    for case in cases:
        actual = _declared_cover_spec_satisfaction(case)
        expected = bool(case["expected_satisfies"])
        _expect(
            actual is expected,
            f"spec case {case['case_id']!r} expected_satisfies disagrees with declared-cover execution semantics",
        )


def _validate_bug_labels_match_expected(cases: list[dict[str, object]]) -> None:
    for case in cases:
        actual = set(detect_bug_labels(str(case["program"]), filename=str(case["case_id"])))
        expected = set(case["expected_bugs"])
        _expect(
            actual == expected,
            f"bug case {case['case_id']!r} expected_bugs disagrees with benchmark bug-label semantics",
        )


def _validate_equivalence_suite(payload: dict[str, object]) -> None:
    cases = payload["cases"]
    _expect(isinstance(cases, list), "equivalence suite must contain a case list")
    _expect(len(cases) == 100, "equivalence suite must contain exactly 100 cases")
    _validate_unique_case_ids(cases, suite_name="equivalence")
    _validate_declared_cover(cases, suite_name="equivalence")
    _expect(
        sum(bool(case["expected_equivalent"]) for case in cases) == 50,
        "equivalence suite must contain 50 equivalent and 50 non-equivalent pairs",
    )
    _expect(
        min(str(case["left_program"]).count("\n") for case in cases) >= 24,
        "equivalence left programs must all be longish",
    )
    _expect(
        min(str(case["right_program"]).count("\n") for case in cases) >= 24,
        "equivalence right programs must all be longish",
    )
    _expect(
        len({_case_family(str(case["case_id"])) for case in cases}) >= 7,
        "equivalence suite must cover at least seven semantic families",
    )
    _expect(
        {str(case["relation_family"]) for case in cases} == SUPPORTED_RELATION_FAMILIES,
        "equivalence suite must use the declared-cover extensional equality relation family",
    )
    for case in cases:
        _expect(
            isinstance(case.get("expected_equivalent"), bool),
            f"equivalence case {case['case_id']!r} must use a boolean expected_equivalent flag",
        )
        left_program = str(case["left_program"])
        right_program = str(case["right_program"])
        _validate_python_source("left_program", left_program, case_id=str(case["case_id"]))
        _validate_python_source("right_program", right_program, case_id=str(case["case_id"]))
        _load_function(left_program, "solve", case_id=str(case["case_id"]))
        _load_function(right_program, "solve", case_id=str(case["case_id"]))
        _expect_scaffold_markers(
            left_program,
            case_id=str(case["case_id"]),
            label="left_program",
            markers=("_coordinate()", "_support(", "_descent_profile(", "_marker("),
        )
        _validate_scaffold_runtime_contract(
            left_program,
            suite_name="equivalence",
            case_id=str(case["case_id"]),
            label="left_program",
            require_marker=True,
        )
        _expect_scaffold_markers(
            right_program,
            case_id=str(case["case_id"]),
            label="right_program",
            markers=("_coordinate()", "_support(", "_descent_profile(", "_marker("),
        )
        _validate_scaffold_runtime_contract(
            right_program,
            suite_name="equivalence",
            case_id=str(case["case_id"]),
            label="right_program",
            require_marker=True,
        )
    _validate_equivalence_truth_matches_declared_cover(cases)
    _validate_metadata(
        payload,
        suite_name="equivalence",
        cases=cases,
        benchmark_semantics="extensional-equality-on-declared-cover",
        composition={
            "total_cases": len(cases),
            "equivalent_cases": sum(bool(case["expected_equivalent"]) for case in cases),
            "non_equivalent_cases": sum(not bool(case["expected_equivalent"]) for case in cases),
            "declared_cover_min_points": min(len(case["input_cover"]) for case in cases),
            "declared_cover_max_points": max(len(case["input_cover"]) for case in cases),
        },
        longish_programs={
            "left_program_min_lines": min(_program_lines(str(case["left_program"])) for case in cases),
            "right_program_min_lines": min(_program_lines(str(case["right_program"])) for case in cases),
        },
        extra={
            "relation_families": ["extensional-equality-on-declared-cover"],
            "certificate_projection": "declared-cover-observables-only",
        },
    )


def _validate_spec_suite(payload: dict[str, object]) -> None:
    cases = payload["cases"]
    _expect(isinstance(cases, list), "spec suite must contain a case list")
    _expect(len(cases) == 100, "spec suite must contain exactly 100 cases")
    _validate_unique_case_ids(cases, suite_name="spec")
    _validate_declared_cover(cases, suite_name="spec")
    _expect(
        sum(bool(case["expected_satisfies"]) for case in cases) == 50,
        "spec suite must contain 50 satisfying and 50 non-satisfying programs",
    )
    _expect(min(str(case["program"]).count("\n") for case in cases) >= 22, "spec programs must all be longish")
    _expect(
        min(str(case["spec_program"]).count("\n") for case in cases) >= 22,
        "specification programs must all be longish",
    )
    _expect(
        len({_case_family(str(case["case_id"])) for case in cases}) >= 7,
        "spec suite must cover at least seven semantic families",
    )
    for case in cases:
        _expect(
            isinstance(case.get("expected_satisfies"), bool),
            f"spec case {case['case_id']!r} must use a boolean expected_satisfies flag",
        )
        program = str(case["program"])
        spec_program = str(case["spec_program"])
        _validate_python_source("program", program, case_id=str(case["case_id"]))
        _validate_python_source("spec_program", spec_program, case_id=str(case["case_id"]))
        _load_function(program, "solve", case_id=str(case["case_id"]))
        _load_function(spec_program, "spec", case_id=str(case["case_id"]))
        _expect_scaffold_markers(
            program,
            case_id=str(case["case_id"]),
            label="program",
            markers=("_coordinate()", "_support(", "_descent_profile(", "_marker("),
        )
        _validate_scaffold_runtime_contract(
            program,
            suite_name="spec",
            case_id=str(case["case_id"]),
            label="program",
            require_marker=True,
        )
        _expect_scaffold_markers(
            spec_program,
            case_id=str(case["case_id"]),
            label="spec_program",
            markers=("_coordinate()", "_support(", "_descent_profile(", "_marker("),
        )
        _validate_scaffold_runtime_contract(
            spec_program,
            suite_name="spec",
            case_id=str(case["case_id"]),
            label="spec_program",
            require_marker=True,
        )
    _validate_spec_functions_return_booleans(cases)
    _validate_spec_truth_matches_declared_cover(cases)
    _validate_metadata(
        payload,
        suite_name="spec",
        cases=cases,
        benchmark_semantics="boolean-returning-specification-on-declared-cover",
        composition={
            "total_cases": len(cases),
            "satisfying_cases": sum(bool(case["expected_satisfies"]) for case in cases),
            "unsatisfying_cases": sum(not bool(case["expected_satisfies"]) for case in cases),
            "declared_cover_min_points": min(len(case["input_cover"]) for case in cases),
            "declared_cover_max_points": max(len(case["input_cover"]) for case in cases),
        },
        longish_programs={
            "program_min_lines": min(_program_lines(str(case["program"])) for case in cases),
            "spec_program_min_lines": min(_program_lines(str(case["spec_program"])) for case in cases),
        },
        extra={
            "spec_contract": "spec(result, *args, **kwargs) -> bool",
            "cover_truth_requirement": "spec must hold on every declared cover point",
        },
    )


def _validate_bug_suite(payload: dict[str, object]) -> None:
    cases = payload["cases"]
    _expect(isinstance(cases, list), "bug suite must contain a case list")
    _expect(len(cases) == 100, "bug suite must contain exactly 100 cases")
    _validate_unique_case_ids(cases, suite_name="bug")
    _expect(
        sum(bool(case["expected_bugs"]) for case in cases) == 50,
        "bug suite must contain 50 bug-positive and 50 bug-negative programs",
    )
    _expect(
        sum(len(set(case["expected_bugs"])) >= 2 for case in cases) >= 6,
        "bug suite must contain at least six multi-bug examples",
    )
    _expect(
        len({_case_family(str(case["case_id"])) for case in cases}) >= 7,
        "bug suite must cover at least seven bug families",
    )
    _expect(
        SUPPORTED_BUG_LABELS <= {label for case in cases for label in case["expected_bugs"]},
        "bug suite must exercise the full benchmark bug label set",
    )
    _expect(min(str(case["program"]).count("\n") for case in cases) >= 8, "bug programs must all be longish")
    for case in cases:
        expected_bugs = case.get("expected_bugs")
        _expect(isinstance(expected_bugs, list), f"bug case {case['case_id']!r} must provide expected_bugs as a list")
        _expect(
            all(isinstance(label, str) for label in expected_bugs),
            f"bug case {case['case_id']!r} must use string bug labels",
        )
        _expect(
            len(expected_bugs) == len(set(expected_bugs)),
            f"bug case {case['case_id']!r} must not duplicate bug labels",
        )
        _expect(
            set(expected_bugs) <= SUPPORTED_BUG_LABELS,
            f"bug case {case['case_id']!r} uses unsupported bug labels",
        )
        program = str(case["program"])
        _validate_python_source("program", program, case_id=str(case["case_id"]))
        _expect_scaffold_markers(
            program,
            case_id=str(case["case_id"]),
            label="program",
            markers=("_coordinate_", "_support_", "_descent_profile_"),
        )
        _validate_scaffold_runtime_contract(
            program,
            suite_name="bug",
            case_id=str(case["case_id"]),
            label="program",
            require_marker=False,
        )
    _validate_bug_labels_match_expected(cases)
    _validate_metadata(
        payload,
        suite_name="bug",
        cases=cases,
        benchmark_semantics="common-python-bug-checking",
        composition={
            "total_cases": len(cases),
            "bug_positive_cases": sum(bool(case["expected_bugs"]) for case in cases),
            "bug_negative_cases": sum(not bool(case["expected_bugs"]) for case in cases),
            "multi_bug_cases": sum(len(set(case["expected_bugs"])) >= 2 for case in cases),
        },
        longish_programs={
            "program_min_lines": min(_program_lines(str(case["program"])) for case in cases),
        },
        extra={
            "bug_labels": sorted({label for case in cases for label in case["expected_bugs"]}),
        },
    )


def validate_suite_payloads(payloads: dict[Path, dict[str, object]]) -> None:
    validators = {
        "equivalence": _validate_equivalence_suite,
        "spec": _validate_spec_suite,
        "bug": _validate_bug_suite,
    }
    for payload in payloads.values():
        suite_name = payload["suite"]
        _expect(isinstance(suite_name, str), "suite payload must declare a suite name")
        validators[suite_name](payload)


# ---------------------------------------------------------------------------
# Unified judgment-geometric validation helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import validate_judgment_form  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    validate_judgment_form = None

try:
    from jugeo.geometry.descent import validate_descent_result  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    validate_descent_result = None

try:
    from jugeo.evidence import validate_manifest as _validate_evidence_manifest  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _validate_evidence_manifest = None


def validate_judgment(judgment: object) -> bool:
    """Validate a judgment object using jugeo.judgments.

    Returns ``True`` when the judgment passes structural validation,
    ``False`` when the validation module is unavailable or the judgment
    is malformed.
    """
    if validate_judgment_form is None:
        return False
    try:
        validate_judgment_form(judgment)
        return True
    except Exception:
        return False


def validate_descent(result: object) -> bool:
    """Validate a descent result using jugeo.geometry.descent.

    Returns ``True`` when the result satisfies the descent invariants.
    """
    if validate_descent_result is None:
        return False
    try:
        validate_descent_result(result)
        return True
    except Exception:
        return False


def validate_evidence(manifest: object) -> bool:
    """Validate an evidence manifest using jugeo.evidence.

    Returns ``True`` when the manifest conforms to the evidence schema.
    """
    if _validate_evidence_manifest is None:
        return False
    try:
        _validate_evidence_manifest(manifest)
        return True
    except Exception:
        return False
