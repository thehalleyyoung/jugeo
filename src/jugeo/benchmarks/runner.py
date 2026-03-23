from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

try:
    from jugeo.problem_modes.bug_detection.models import BugKind, BugReport
except ModuleNotFoundError:  # pragma: no cover - compatibility for pytest import-order edge cases.
    class BugKind(str, Enum):
        LOGIC_ERROR = "LOGIC_ERROR"
        PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
        SCOPE_VIOLATION = "SCOPE_VIOLATION"
        RESOURCE_LEAK = "RESOURCE_LEAK"

        def cohomology_generator(self) -> str:
            generators = {
                "LOGIC_ERROR": "σ_logic",
                "PROTOCOL_VIOLATION": "σ_proto",
                "SCOPE_VIOLATION": "σ_scope",
                "RESOURCE_LEAK": "σ_resource",
            }
            return generators[self.value]

        def severity_baseline(self) -> float:
            baselines = {
                "LOGIC_ERROR": 0.8,
                "PROTOCOL_VIOLATION": 0.85,
                "SCOPE_VIOLATION": 0.75,
                "RESOURCE_LEAK": 0.65,
            }
            return baselines[self.value]

    @dataclass(frozen=True, slots=True)
    class BugReport:
        kind: BugKind
        coordinate: str
        severity: float
        description: str
        counterexample: Any
        trust_tier: str = "ORACLE_PROPOSED"
        cohomology_class: str = ""

        def with_cohomology_class(self, cls: str) -> "BugReport":
            return BugReport(
                kind=self.kind,
                coordinate=self.coordinate,
                severity=self.severity,
                description=self.description,
                counterexample=self.counterexample,
                trust_tier=self.trust_tier,
                cohomology_class=cls,
            )

from .loader import load_benchmark_bundle
from .models import (
    BenchmarkBundle,
    BenchmarkJudgment,
    BenchmarkReport,
    BugCase,
    EquivalenceCase,
    InputPoint,
    MetricSummary,
    ResidualObligation,
    SpecCase,
    Witness,
)
from .semantics import (
    call_fresh,
    detect_bug_observations,
    format_outcome,
    load_function,
    require_declared_cover,
    semantic_coordinate,
)
from .validation import (
    SUPPORTED_BUG_LABELS,
    SUPPORTED_RELATION_FAMILIES,
    SUPPORTED_RELATION_FAMILY_PROPERTIES,
)

TRUST_RUNTIME = "RUNTIME_WITNESSED"
TRUST_ORACLE = "ORACLE_PROPOSED"


def _equivalence_coordinate(case: EquivalenceCase, index: int) -> str:
    left_coordinate = semantic_coordinate(case.left_program)
    right_coordinate = semantic_coordinate(case.right_program)
    if left_coordinate is not None and right_coordinate is not None:
        return f"{left_coordinate}|{right_coordinate}#cover[{index}]"
    return f"{case.case_id}#cover[{index}]"


def _spec_coordinate(case: SpecCase, index: int) -> str:
    program_coordinate = semantic_coordinate(case.program)
    if program_coordinate is not None:
        return f"{program_coordinate}#cover[{index}]"
    return f"{case.case_id}#cover[{index}]"


def _relation_obstruction(case: EquivalenceCase, witness: Witness) -> str:
    relation = SUPPORTED_RELATION_FAMILY_PROPERTIES[case.relation_family]
    location = witness.coordinate or case.case_id
    return (
        f"{case.relation_family}:"
        f"{relation['overlap_law']}:"
        f"{location}"
    )


BUG_KIND_MAP = {
    "mutable-default": BugKind.LOGIC_ERROR,
    "bare-except": BugKind.PROTOCOL_VIOLATION,
    "late-binding-closure": BugKind.SCOPE_VIOLATION,
    "open-without-close": BugKind.RESOURCE_LEAK,
    "shadow-builtin": BugKind.SCOPE_VIOLATION,
    "identity-literal": BugKind.LOGIC_ERROR,
}

if frozenset(BUG_KIND_MAP) != SUPPORTED_BUG_LABELS:  # pragma: no cover - import-time configuration guard.
    raise RuntimeError("benchmark runner bug labels must stay synchronized with benchmark validation")


def _compare_extensional_equality(case: EquivalenceCase) -> tuple[bool, Witness | None]:
    if case.relation_family not in SUPPORTED_RELATION_FAMILIES:
        raise ValueError(
            f"unsupported equivalence relation family {case.relation_family!r}; "
            f"supported families: {sorted(SUPPORTED_RELATION_FAMILIES)!r}"
        )
    points = require_declared_cover(case.input_cover, case_id=case.case_id, category="equivalence")
    for index, point in enumerate(points):
        left_outcome = call_fresh(case.left_program, "solve", point)
        right_outcome = call_fresh(case.right_program, "solve", point)
        if left_outcome != right_outcome:
            coordinate = _equivalence_coordinate(case, index)
            return (
                False,
                Witness(
                    message=(
                        f"relation {case.relation_family!r} failed on the declared finite cover: "
                        f"left {format_outcome(left_outcome)} vs right {format_outcome(right_outcome)}"
                    ),
                    input_point=point,
                    coordinate=coordinate,
                    cover_index=index,
                ),
            )
    return True, None


def run_equivalence_benchmark(bundle: BenchmarkBundle | None = None) -> BenchmarkReport:
    bundle = bundle or load_benchmark_bundle()
    judgments: list[BenchmarkJudgment] = []
    tp = fp = fn = 0
    correct = 0
    for case in bundle.equivalence_cases:
        predicted, witness = _compare_extensional_equality(case)
        passed = predicted == case.expected_equivalent
        correct += int(passed)
        if predicted and case.expected_equivalent:
            tp += 1
        elif predicted and not case.expected_equivalent:
            fp += 1
        elif (not predicted) and case.expected_equivalent:
            fn += 1
        judgments.append(
            BenchmarkJudgment(
                category="equivalence",
                case_id=case.case_id,
                expected=case.expected_equivalent,
                predicted=predicted,
                passed=passed,
                trust_tier=TRUST_RUNTIME,
                witness=witness,
                residuals=() if passed else ("finite-cover mismatch witness retained",),
                obstructions=() if witness is None else (_relation_obstruction(case, witness),),
                residual_obligations=()
                if witness is None
                else (
                    ResidualObligation(
                        obligation="finite-cover extensional agreement remains open",
                        support_indices=() if witness.cover_index is None else (witness.cover_index,),
                        reopen_condition="if-declared-cover-refined",
                    ),
                ),
                obstruction_class=None if witness is None else "H1/declared-cover-mismatch",
                support_indices=() if witness is None or witness.cover_index is None else (witness.cover_index,),
                repair_feasibility=None if witness is None else "local-cover-repair",
            )
        )
    return BenchmarkReport(
        category="equivalence",
        metrics=MetricSummary(tp, fp, fn, len(bundle.equivalence_cases), correct),
        judgments=tuple(judgments),
    )


def _check_spec_case(case: SpecCase) -> tuple[bool, Witness | None]:
    points = require_declared_cover(case.input_cover, case_id=case.case_id, category="spec")
    for index, point in enumerate(points):
        coordinate = _spec_coordinate(case, index)
        result = call_fresh(case.program, "solve", point)
        if result.tag != "return":
            return False, Witness(
                "program raised instead of realizing a section on the declared cover",
                point,
                coordinate,
                index,
            )
        try:
            spec = load_function(case.spec_program, "spec")
            spec_result = spec(result.value, *point.args, **point.kwargs)
        except Exception as exc:  # pragma: no cover - benchmark cases avoid this path.
            return False, Witness(f"spec raised {type(exc).__name__}", point, coordinate, index)
        if not isinstance(spec_result, bool):
            return False, Witness(
                "specification must return a boolean on the declared finite cover",
                point,
                coordinate,
                index,
            )
        satisfied = spec_result
        if not satisfied:
            return False, Witness(
                "specification returned False on the declared finite cover",
                point,
                coordinate,
                index,
            )
    return True, None


def run_spec_benchmark(bundle: BenchmarkBundle | None = None) -> BenchmarkReport:
    bundle = bundle or load_benchmark_bundle()
    judgments: list[BenchmarkJudgment] = []
    tp = fp = fn = 0
    correct = 0
    for case in bundle.spec_cases:
        predicted, witness = _check_spec_case(case)
        passed = predicted == case.expected_satisfies
        correct += int(passed)
        if predicted and case.expected_satisfies:
            tp += 1
        elif predicted and not case.expected_satisfies:
            fp += 1
        elif (not predicted) and case.expected_satisfies:
            fn += 1
        judgments.append(
            BenchmarkJudgment(
                category="spec",
                case_id=case.case_id,
                expected=case.expected_satisfies,
                predicted=predicted,
                passed=passed,
                trust_tier=TRUST_RUNTIME,
                witness=witness,
                residuals=() if predicted else ("one or more obligations remain undischargeable on the declared cover",),
                obstructions=()
                if witness is None
                else (f"specification:declared-cover-obligation:{witness.coordinate or case.case_id}",),
                residual_obligations=()
                if predicted
                else (
                    ResidualObligation(
                        obligation="specification truth does not descend over the declared finite cover",
                        support_indices=() if witness.cover_index is None else (witness.cover_index,),
                        reopen_condition="if-program-or-spec-changes",
                    ),
                ),
                obstruction_class=None if predicted else "H1/specification-obstruction",
                support_indices=() if witness is None or witness.cover_index is None else (witness.cover_index,),
                repair_feasibility=None if predicted else "local-program-or-spec-repair",
            )
        )
    return BenchmarkReport(
        category="spec",
        metrics=MetricSummary(tp, fp, fn, len(bundle.spec_cases), correct),
        judgments=tuple(judgments),
    )


def _detect_bugs(case: BugCase) -> tuple[tuple[str, ...], tuple[BugReport, ...]]:
    observations = detect_bug_observations(case.program, filename=case.case_id)
    reports: list[BugReport] = []
    labels: list[str] = []
    for observation in observations:
        labels.append(observation.code)
        coordinate = f"{case.case_id}:{observation.lineno}:{observation.col}:{observation.node_type}"
        reports.append(
            BugReport(
                kind=BUG_KIND_MAP[observation.code],
                coordinate=coordinate,
                severity=BUG_KIND_MAP[observation.code].severity_baseline(),
                description=observation.message,
                counterexample={
                    "bug_code": observation.code,
                    "coordinate": coordinate,
                    "lineno": observation.lineno,
                    "column": observation.col,
                    "node_type": observation.node_type,
                    "message": observation.message,
                    "evidence_kind": "ast-pattern",
                    "provenance": {
                        "semantic_source": "preliminaries/theory2.tex",
                        "mode": "benchmark-bug-detection",
                        "case_id": case.case_id,
                    },
                },
                trust_tier=TRUST_ORACLE,
            ).with_cohomology_class(BUG_KIND_MAP[observation.code].cohomology_generator() + f":{case.case_id}")
        )
    return tuple(sorted(labels)), tuple(reports)


def run_bug_benchmark(bundle: BenchmarkBundle | None = None) -> BenchmarkReport:
    bundle = bundle or load_benchmark_bundle()
    judgments: list[BenchmarkJudgment] = []
    tp = fp = fn = 0
    correct = 0
    for case in bundle.bug_cases:
        predicted_labels, reports = _detect_bugs(case)
        expected_labels = tuple(sorted(case.expected_bugs))
        predicted_set = set(predicted_labels)
        expected_set = set(expected_labels)
        passed = predicted_set == expected_set
        correct += int(passed)
        expected_buggy = bool(expected_set)
        predicted_buggy = bool(predicted_set)
        if passed and expected_buggy:
            tp += 1
        if not passed and predicted_buggy:
            fp += 1
        if not passed and expected_buggy:
            fn += 1
        witness = None
        if predicted_set != expected_set:
            missing = sorted(expected_set - predicted_set)
            extra = sorted(predicted_set - expected_set)
            witness = Witness(
                message=f"expected {missing or 'no missing labels'}; extra {extra or 'no extra labels'}",
                coordinate=case.case_id,
            )
        judgments.append(
            BenchmarkJudgment(
                category="bug",
                case_id=case.case_id,
                expected=expected_labels,
                predicted=predicted_labels,
                passed=passed,
                trust_tier=TRUST_ORACLE,
                witness=witness,
                obstructions=tuple(report.cohomology_class for report in reports),
                residual_obligations=()
                if passed
                else (
                    ResidualObligation(
                        obligation="bug-label disagreement remains unresolved",
                        reopen_condition="if-detector-rules-change",
                    ),
                ),
                obstruction_class=None if passed else "H1/bug-label-disagreement",
                repair_feasibility=None if passed else "local-detector-or-suite-repair",
            )
        )
    return BenchmarkReport(
        category="bug",
        metrics=MetricSummary(tp, fp, fn, len(bundle.bug_cases), correct),
        judgments=tuple(judgments),
    )


def run_all_benchmarks(bundle: BenchmarkBundle | None = None) -> dict[str, BenchmarkReport]:
    bundle = bundle or load_benchmark_bundle()
    return {
        "equivalence": run_equivalence_benchmark(bundle),
        "spec": run_spec_benchmark(bundle),
        "bug": run_bug_benchmark(bundle),
    }


def main() -> int:
    reports = run_all_benchmarks()
    payload = {name: report.metrics.to_dict() for name, report in reports.items()}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Unified judgment-geometric benchmark helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import construct_judgment  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    construct_judgment = None

try:
    from jugeo.geometry.descent import descend  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    descend = None

try:
    from jugeo.solver.z3_session import Z3Session  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    Z3Session = None

try:
    from jugeo.encodings import encode_program  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    encode_program = None


import time as _time


def judgment_benchmark(judgments: Any) -> dict[str, Any]:
    """Benchmark judgment construction via jugeo.judgments.

    *judgments* is an iterable of raw judgment descriptors.  Each descriptor is
    passed through ``construct_judgment`` (when available) and the wall-clock
    time for the full batch is recorded.
    """
    if construct_judgment is None:
        return {"error": "jugeo.judgments not available", "elapsed_seconds": 0.0}
    start = _time.monotonic()
    results = []
    for j in judgments:
        results.append(construct_judgment(j))
    elapsed = _time.monotonic() - start
    return {
        "constructed": len(results),
        "elapsed_seconds": elapsed,
    }


def descent_benchmark(site: Any) -> dict[str, Any]:
    """Benchmark descent performance via jugeo.geometry.descent.

    *site* is a geometric site object passed to ``descend``.
    """
    if descend is None:
        return {"error": "jugeo.geometry.descent not available", "elapsed_seconds": 0.0}
    start = _time.monotonic()
    result = descend(site)
    elapsed = _time.monotonic() - start
    return {
        "descent_result": result,
        "elapsed_seconds": elapsed,
    }


def solver_benchmark(formulas: Any) -> dict[str, Any]:
    """Benchmark Z3 solving via jugeo.solver.z3_session.

    *formulas* is an iterable of formula objects fed to a Z3 session.
    """
    if Z3Session is None:
        return {"error": "jugeo.solver.z3_session not available", "elapsed_seconds": 0.0}
    start = _time.monotonic()
    session = Z3Session()
    solved = 0
    for formula in formulas:
        session.add(formula)
        solved += 1
    check = session.check()
    elapsed = _time.monotonic() - start
    return {
        "formulas_added": solved,
        "check_result": str(check),
        "elapsed_seconds": elapsed,
    }


def encoding_benchmark(programs: Any) -> dict[str, Any]:
    """Benchmark encoding via jugeo.encodings.

    *programs* is an iterable of program source strings to encode.
    """
    if encode_program is None:
        return {"error": "jugeo.encodings not available", "elapsed_seconds": 0.0}
    start = _time.monotonic()
    encoded = []
    for prog in programs:
        encoded.append(encode_program(prog))
    elapsed = _time.monotonic() - start
    return {
        "encoded": len(encoded),
        "elapsed_seconds": elapsed,
    }
