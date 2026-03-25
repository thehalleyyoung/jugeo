"""Tests for ConvergenceMonitor."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jugeo.orchestration.large_scale.models import (
    ConvergenceCertificate,
    ConvergenceCriterion,
)
from jugeo.orchestration.large_scale.convergence import ConvergenceMonitor


class TestRecordStep:
    def test_record_step(self) -> None:
        cm = ConvergenceMonitor(window_size=10)
        cm.record_step(obligations=5, drift=0.3, coverage=0.5)
        assert len(cm._obligations) == 1
        assert len(cm._drifts) == 1
        assert len(cm._coverages) == 1


class TestIsConverging:
    def test_is_converging_true(self) -> None:
        cm = ConvergenceMonitor(window_size=20)
        # Decreasing obligations, decreasing drift, increasing coverage
        for i in range(10):
            cm.record_step(
                obligations=20 - i * 2,
                drift=0.5 - i * 0.05,
                coverage=0.1 + i * 0.08,
            )
        assert cm.is_converging() is True

    def test_is_converging_false(self) -> None:
        cm = ConvergenceMonitor(window_size=20)
        # Oscillating metrics
        for i in range(10):
            cm.record_step(
                obligations=10 + (i % 3) * 5,
                drift=0.3 + (i % 2) * 0.2,
                coverage=0.5 - (i % 2) * 0.1,
            )
        # Likely not converging
        assert cm.is_converging() is False


class TestDetectDivergence:
    def test_detect_divergence(self) -> None:
        cm = ConvergenceMonitor(window_size=20)
        # Lyapunov is INCREASING: obligations up, drift up, coverage down
        for i in range(10):
            cm.record_step(
                obligations=5 + i * 2,
                drift=0.1 + i * 0.05,
                coverage=0.8 - i * 0.05,
            )
        assert cm.detect_divergence() is True


class TestLyapunovDecreasing:
    def test_lyapunov_decreasing(self) -> None:
        cm = ConvergenceMonitor(window_size=20)
        for i in range(10):
            cm.record_step(
                obligations=20 - i * 2,
                drift=0.5 - i * 0.05,
                coverage=0.1 + i * 0.08,
            )
        assert cm._is_lyapunov_decreasing() is True


class TestIssueCertificate:
    def test_issue_certificate(self) -> None:
        cm = ConvergenceMonitor(window_size=20)
        for i in range(10):
            cm.record_step(
                obligations=20 - i * 2,
                drift=0.5 - i * 0.05,
                coverage=0.1 + i * 0.08,
                obstructions=0,
            )
        cert = cm.issue_certificate()
        assert cert is not None
        assert cert.is_converging is True
        assert ConvergenceCriterion.OBLIGATION_DECREASE in cert.criteria_met

    def test_no_certificate_when_diverging(self) -> None:
        cm = ConvergenceMonitor(window_size=20)
        for i in range(10):
            cm.record_step(
                obligations=5 + i * 3,
                drift=0.1 + i * 0.05,
                coverage=0.5,
            )
        cert = cm.issue_certificate()
        assert cert is None


class TestRecommendRecovery:
    def test_recommend_recovery_high_obligations(self) -> None:
        cm = ConvergenceMonitor()
        cm.record_step(obligations=50, drift=0.1, coverage=0.8)
        recs = cm.recommend_recovery()
        assert any("obligation" in r.lower() or "grounding" in r.lower() for r in recs)

    def test_recommend_recovery_high_drift(self) -> None:
        cm = ConvergenceMonitor()
        cm.record_step(obligations=2, drift=0.6, coverage=0.8)
        recs = cm.recommend_recovery()
        assert any("drift" in r.lower() or "sync" in r.lower() for r in recs)


class TestTrajectory:
    def test_trajectory(self) -> None:
        cm = ConvergenceMonitor(window_size=5)
        for i in range(3):
            cm.record_step(obligations=i, drift=0.1 * i, coverage=0.3 * i)
        traj = cm.trajectory()
        assert len(traj["obligations"]) == 3
        assert len(traj["drifts"]) == 3


class TestSummary:
    def test_summary(self) -> None:
        cm = ConvergenceMonitor()
        cm.record_step(obligations=5, drift=0.2, coverage=0.5)
        s = cm.summary()
        assert "steps_recorded" in s
        assert s["steps_recorded"] == 1


class TestEstimatedSteps:
    def test_estimated_steps_to_convergence(self) -> None:
        cm = ConvergenceMonitor(window_size=20)
        for i in range(10):
            cm.record_step(
                obligations=20 - i * 2,
                drift=0.5 - i * 0.05,
                coverage=0.1 + i * 0.08,
            )
        est = cm.estimated_steps_to_convergence()
        # Should be a positive integer or None
        if est is not None:
            assert est >= 1
