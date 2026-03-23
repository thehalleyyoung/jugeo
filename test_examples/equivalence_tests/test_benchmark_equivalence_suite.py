from __future__ import annotations

import pytest

from jugeo.benchmarks.loader import load_equivalence_suite
from jugeo.benchmarks.runner import _compare_extensional_equality, run_equivalence_benchmark


EQUIVALENCE_CASES = load_equivalence_suite()


def test_equivalence_suite_has_requested_balance() -> None:
    assert len(EQUIVALENCE_CASES) == 100
    assert sum(case.expected_equivalent for case in EQUIVALENCE_CASES) == 50
    assert sum(not case.expected_equivalent for case in EQUIVALENCE_CASES) == 50


@pytest.mark.parametrize("case", EQUIVALENCE_CASES, ids=[case.case_id for case in EQUIVALENCE_CASES])
def test_declared_cover_equivalence_examples(case) -> None:
    predicted, witness = _compare_extensional_equality(case)

    assert predicted is case.expected_equivalent
    if case.expected_equivalent:
        assert witness is None
    else:
        assert witness is not None
        assert witness.coordinate is not None
        assert witness.cover_index is not None


def test_equivalence_examples_score_perfect_f1() -> None:
    report = run_equivalence_benchmark()

    assert report.metrics.total_cases == 100
    assert report.metrics.correct_cases == 100
    assert report.metrics.precision == 1.0
    assert report.metrics.recall == 1.0
    assert report.metrics.f1 == 1.0
    assert report.metrics.accuracy == 1.0
