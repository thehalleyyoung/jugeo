from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jugeo.benchmarks.runner import run_all_benchmarks


def test_checked_in_example_suites_score_perfect_f1() -> None:
    reports = run_all_benchmarks()

    assert set(reports) == {"equivalence", "spec", "bug"}

    for name, report in reports.items():
        metrics = report.metrics
        assert metrics.total_cases == 100
        assert metrics.correct_cases == 100
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1 == 1.0
        assert metrics.accuracy == 1.0
        assert all(judgment.passed for judgment in report.judgments), name
