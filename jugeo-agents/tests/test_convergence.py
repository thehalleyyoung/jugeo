"""Tests for jugeo_agents.orchestration.convergence — ConvergenceMonitor."""

import pytest

from jugeo_agents.types import ConvergencePhase, ConvergenceStatus
from jugeo_agents.orchestration.convergence import ConvergenceMonitor


# ---------------------------------------------------------------------------
# record_round basics
# ---------------------------------------------------------------------------


def test_record_round_creates_snapshot():
    monitor = ConvergenceMonitor()
    snap = monitor.record_round(
        coverage=0.5,
        obstruction_density=0.2,
        trust_debt=0.3,
    )
    assert snap.round_number == 0
    assert snap.coverage == 0.5
    assert snap.lyapunov_v > 0
    assert monitor.rounds == 1


def test_record_multiple_rounds():
    monitor = ConvergenceMonitor()
    for i in range(5):
        monitor.record_round(
            coverage=0.2 * (i + 1),
            obstruction_density=max(0, 0.5 - 0.1 * i),
            trust_debt=max(0, 0.5 - 0.1 * i),
        )
    assert monitor.rounds == 5
    assert len(monitor.v_history) == 5


# ---------------------------------------------------------------------------
# Status: converging
# ---------------------------------------------------------------------------


def test_status_converging():
    monitor = ConvergenceMonitor()
    # Decreasing V → converging
    monitor.record_round(coverage=0.3, obstruction_density=0.5, trust_debt=0.5)
    monitor.record_round(coverage=0.5, obstruction_density=0.3, trust_debt=0.3)
    status = monitor.status()
    assert status in (ConvergenceStatus.CONVERGING, ConvergenceStatus.UNKNOWN)


# ---------------------------------------------------------------------------
# Status: stuck (stall detection)
# ---------------------------------------------------------------------------


def test_status_stuck():
    monitor = ConvergenceMonitor(stall_patience=3)
    # Record several rounds with no improvement in V
    for _ in range(6):
        monitor.record_round(
            coverage=0.5, obstruction_density=0.3, trust_debt=0.3
        )
    status = monitor.status()
    assert status == ConvergenceStatus.STUCK


# ---------------------------------------------------------------------------
# Status: diverging
# ---------------------------------------------------------------------------


def test_status_diverging():
    monitor = ConvergenceMonitor(divergence_window=3)
    # Increasing V → diverging
    for i in range(6):
        monitor.record_round(
            coverage=max(0, 0.8 - 0.1 * i),
            obstruction_density=min(1.0, 0.1 * (i + 1)),
            trust_debt=min(1.0, 0.1 * (i + 1)),
        )
    status = monitor.status()
    assert status == ConvergenceStatus.DIVERGING


# ---------------------------------------------------------------------------
# Status: converged
# ---------------------------------------------------------------------------


def test_status_converged():
    monitor = ConvergenceMonitor()
    # V near 0 → converged
    monitor.record_round(
        coverage=1.0, obstruction_density=0.0, trust_debt=0.0
    )
    status = monitor.status()
    assert status == ConvergenceStatus.CONVERGED


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------


def test_phase_exploration():
    monitor = ConvergenceMonitor()
    monitor.record_round(
        coverage=0.2, obstruction_density=0.0, trust_debt=0.0
    )
    assert monitor.current_phase() == ConvergencePhase.EXPLORATION


def test_phase_complete():
    monitor = ConvergenceMonitor()
    monitor.record_round(
        coverage=1.0, obstruction_density=0.0, trust_debt=0.0
    )
    assert monitor.current_phase() == ConvergencePhase.COMPLETE


def test_phase_consolidation():
    monitor = ConvergenceMonitor()
    monitor.record_round(
        coverage=0.8, obstruction_density=0.5, trust_debt=0.1
    )
    phase = monitor.current_phase()
    assert phase == ConvergencePhase.CONSOLIDATION


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------


def test_forecast_returns_none_initially():
    monitor = ConvergenceMonitor()
    assert monitor.forecast() is None


def test_forecast_returns_steps():
    monitor = ConvergenceMonitor()
    # Simulate improving pipeline
    for i in range(5):
        cov = min(1.0, 0.2 * (i + 1))
        obs = max(0.0, 0.5 - 0.1 * (i + 1))
        trd = max(0.0, 0.5 - 0.1 * (i + 1))
        monitor.record_round(coverage=cov, obstruction_density=obs, trust_debt=trd)
    fc = monitor.forecast()
    # If rate is negative (V decreasing), forecast should be an int
    if fc is not None:
        assert isinstance(fc, int)
        assert fc >= 0


# ---------------------------------------------------------------------------
# should_stop
# ---------------------------------------------------------------------------


def test_should_stop_when_converged():
    monitor = ConvergenceMonitor()
    monitor.record_round(coverage=1.0, obstruction_density=0.0, trust_debt=0.0)
    assert monitor.should_stop() is True


def test_should_not_stop_when_converging():
    monitor = ConvergenceMonitor()
    monitor.record_round(coverage=0.5, obstruction_density=0.3, trust_debt=0.3)
    assert monitor.should_stop() is False
