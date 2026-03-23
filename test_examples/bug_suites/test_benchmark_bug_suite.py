from __future__ import annotations

import pytest

from jugeo.benchmarks.loader import load_bug_suite
from jugeo.benchmarks.runner import _detect_bugs, run_bug_benchmark


BUG_CASES = load_bug_suite()


def test_bug_suite_has_requested_balance() -> None:
    assert len(BUG_CASES) == 100
    assert sum(bool(case.expected_bugs) for case in BUG_CASES) == 50
    assert sum(not case.expected_bugs for case in BUG_CASES) == 50


@pytest.mark.parametrize("case", BUG_CASES, ids=[case.case_id for case in BUG_CASES])
def test_bug_examples_match_expected_labels(case) -> None:
    predicted_labels, reports = _detect_bugs(case)

    assert set(predicted_labels) == set(case.expected_bugs)
    assert len(predicted_labels) == len(reports)


def test_bug_examples_score_perfect_f1() -> None:
    report = run_bug_benchmark()

    assert report.metrics.total_cases == 100
    assert report.metrics.correct_cases == 100
    assert report.metrics.precision == 1.0
    assert report.metrics.recall == 1.0
    assert report.metrics.f1 == 1.0
    assert report.metrics.accuracy == 1.0
