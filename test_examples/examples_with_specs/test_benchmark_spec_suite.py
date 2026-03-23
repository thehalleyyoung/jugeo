from __future__ import annotations

import pytest

from jugeo.benchmarks.loader import load_spec_suite
from jugeo.benchmarks.runner import _check_spec_case, run_spec_benchmark


SPEC_CASES = load_spec_suite()


def test_spec_suite_has_requested_balance() -> None:
    assert len(SPEC_CASES) == 100
    assert sum(case.expected_satisfies for case in SPEC_CASES) == 50
    assert sum(not case.expected_satisfies for case in SPEC_CASES) == 50


@pytest.mark.parametrize("case", SPEC_CASES, ids=[case.case_id for case in SPEC_CASES])
def test_boolean_spec_examples(case) -> None:
    predicted, witness = _check_spec_case(case)

    assert predicted is case.expected_satisfies
    if case.expected_satisfies:
        assert witness is None
    else:
        assert witness is not None
        assert witness.coordinate is not None
        assert witness.cover_index is not None


def test_spec_examples_score_perfect_f1() -> None:
    report = run_spec_benchmark()

    assert report.metrics.total_cases == 100
    assert report.metrics.correct_cases == 100
    assert report.metrics.precision == 1.0
    assert report.metrics.recall == 1.0
    assert report.metrics.f1 == 1.0
    assert report.metrics.accuracy == 1.0
