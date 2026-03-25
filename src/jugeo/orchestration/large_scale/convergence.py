"""
Convergence monitoring with Lyapunov-style certificates.

Tracks obligation count, drift score, coverage, and obstruction rate over time.
Issues convergence certificates when the Lyapunov candidate function is
monotonically decreasing.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any, Optional

from .models import ConvergenceCertificate, ConvergenceCriterion

__all__ = ["ConvergenceMonitor"]


# Trust floor ordering used for trend computation
_TRUST_ORDER = {
    "conjecture": 0,
    "tested": 1,
    "proved": 2,
    "certified": 3,
}


class ConvergenceMonitor:
    """Lyapunov-style convergence monitor for the co-evolution loop."""

    def __init__(self, window_size: int = 50) -> None:
        self._window_size = window_size
        self._obligations: deque[int] = deque(maxlen=window_size)
        self._drifts: deque[float] = deque(maxlen=window_size)
        self._coverages: deque[float] = deque(maxlen=window_size)
        self._trust_floors: deque[str] = deque(maxlen=window_size)
        self._obstructions: deque[int] = deque(maxlen=window_size)
        self._lyapunov_values: deque[float] = deque(maxlen=window_size)
        self._certificates: list[ConvergenceCertificate] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_step(
        self,
        obligations: int,
        drift: float,
        coverage: float,
        trust_floor: str = "conjecture",
        obstructions: int = 0,
    ) -> None:
        """Record one orchestration step's metrics."""
        self._obligations.append(obligations)
        self._drifts.append(drift)
        self._coverages.append(coverage)
        self._trust_floors.append(trust_floor)
        self._obstructions.append(obstructions)
        self._lyapunov_values.append(self._lyapunov_candidate())

    # ------------------------------------------------------------------
    # Convergence queries
    # ------------------------------------------------------------------

    def is_converging(self) -> bool:
        """True if obligations↓, drift≤, coverage≥ (all trending correctly)."""
        if len(self._obligations) < 3:
            return False
        return (
            self._obligation_trend() < 0
            and self._drift_trend() <= 0
            and self._coverage_trend() >= 0
        )

    def convergence_rate(self) -> float:
        """Rate at which the Lyapunov function is decreasing.

        Negative → converging, positive → diverging.
        """
        if len(self._lyapunov_values) < 2:
            return 0.0
        vals = list(self._lyapunov_values)
        diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        return sum(diffs) / len(diffs)

    def estimated_steps_to_convergence(
        self, target: dict[str, Any] | None = None
    ) -> Optional[int]:
        """Estimate remaining steps until convergence target is reached.

        Returns ``None`` if the system is not converging or if there is
        insufficient data.
        """
        if not self.is_converging():
            return None
        rate = self.convergence_rate()
        if rate >= 0:
            return None

        target = target or {}
        target_obligations = target.get("obligations", 0)
        target_drift = target.get("drift", 0.05)

        current_obligations = self._obligations[-1] if self._obligations else 0
        current_drift = self._drifts[-1] if self._drifts else 0.0

        ob_trend = self._obligation_trend()
        drift_trend = self._drift_trend()

        steps_ob: float = float("inf")
        steps_drift: float = float("inf")

        if ob_trend < 0 and current_obligations > target_obligations:
            steps_ob = (current_obligations - target_obligations) / abs(ob_trend)
        elif current_obligations <= target_obligations:
            steps_ob = 0

        if drift_trend < 0 and current_drift > target_drift:
            steps_drift = (current_drift - target_drift) / abs(drift_trend)
        elif current_drift <= target_drift:
            steps_drift = 0

        est = max(steps_ob, steps_drift)
        if est == float("inf"):
            return None
        return max(1, int(est))

    # ------------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------------

    def issue_certificate(self) -> Optional[ConvergenceCertificate]:
        """Issue a convergence certificate if warranted."""
        if len(self._obligations) < 5:
            return None
        if not self.is_converging():
            return None

        criteria: list[ConvergenceCriterion] = []
        if self._obligation_trend() < 0:
            criteria.append(ConvergenceCriterion.OBLIGATION_DECREASE)
        if self._drift_trend() <= 0:
            criteria.append(ConvergenceCriterion.DRIFT_DECREASE)
        if self._coverage_trend() >= 0:
            criteria.append(ConvergenceCriterion.COVERAGE_INCREASE)

        recent_obs = list(self._obstructions)[-5:]
        if all(o == 0 for o in recent_obs):
            criteria.append(ConvergenceCriterion.NO_NEW_OBSTRUCTIONS)

        cert = ConvergenceCertificate(
            id=str(uuid.uuid4()),
            criteria_met=criteria,
            obligation_trajectory=list(self._obligations),
            drift_trajectory=list(self._drifts),
            coverage_trajectory=list(self._coverages),
            is_converging=True,
            estimated_steps_to_convergence=self.estimated_steps_to_convergence(),
            issued_at=time.time(),
        )
        self._certificates.append(cert)
        return cert

    # ------------------------------------------------------------------
    # Divergence
    # ------------------------------------------------------------------

    def detect_divergence(self) -> bool:
        """True if the Lyapunov function is increasing."""
        if len(self._lyapunov_values) < 3:
            return False
        return self._is_lyapunov_increasing()

    def recommend_recovery(self) -> list[str]:
        """Suggest recovery actions based on current trends."""
        suggestions: list[str] = []
        if self._obligations and self._obligations[-1] > 10:
            suggestions.append("High obligation count — prioritise grounding and discharge")
        if self._drifts and self._drifts[-1] > 0.3:
            suggestions.append("High drift — run synchronisation plan")
        if len(self._coverages) >= 3:
            cov_trend = self._coverage_trend()
            if cov_trend <= 0:
                suggestions.append("Coverage stalled — try exploration moves")
        if self._obstructions and self._obstructions[-1] > 3:
            suggestions.append("High obstruction rate — consider repair moves")
        if not suggestions:
            suggestions.append("No specific recovery needed")
        return suggestions

    # ------------------------------------------------------------------
    # Trend helpers
    # ------------------------------------------------------------------

    def _obligation_trend(self) -> float:
        """Average change in obligation count per step."""
        return self._trend(list(self._obligations))

    def _drift_trend(self) -> float:
        """Average change in drift per step."""
        return self._trend(list(self._drifts))

    def _coverage_trend(self) -> float:
        """Average change in coverage per step."""
        return self._trend(list(self._coverages))

    @staticmethod
    def _trend(values: list[float]) -> float:
        """Simple linear trend (second half mean − first half mean)."""
        if len(values) < 2:
            return 0.0
        mid = len(values) // 2
        first = sum(values[:mid]) / max(1, mid)
        second = sum(values[mid:]) / max(1, len(values) - mid)
        return second - first

    # ------------------------------------------------------------------
    # Lyapunov
    # ------------------------------------------------------------------

    def _lyapunov_candidate(self) -> float:
        """Potential function: normalised obligations + drift − coverage."""
        ob = self._obligations[-1] if self._obligations else 0
        dr = self._drifts[-1] if self._drifts else 0.0
        co = self._coverages[-1] if self._coverages else 0.0
        # Normalise obligations to [0, 1] range (cap at 100)
        ob_norm = min(ob / 100.0, 1.0) if ob else 0.0
        return ob_norm + dr - co

    def _is_lyapunov_decreasing(self) -> bool:
        """True if Lyapunov has been decreasing over recent steps."""
        vals = list(self._lyapunov_values)
        if len(vals) < 3:
            return False
        diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        recent = diffs[-min(5, len(diffs)) :]
        return all(d <= 0.001 for d in recent)

    def _is_lyapunov_increasing(self) -> bool:
        """True if Lyapunov has been increasing over recent steps."""
        vals = list(self._lyapunov_values)
        if len(vals) < 3:
            return False
        diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        recent = diffs[-min(5, len(diffs)) :]
        return all(d >= 0 for d in recent) and any(d > 0 for d in recent)

    # ------------------------------------------------------------------
    # Trajectory / summary
    # ------------------------------------------------------------------

    def trajectory(self) -> dict[str, Any]:
        """Full metric trajectories."""
        return {
            "obligations": list(self._obligations),
            "drifts": list(self._drifts),
            "coverages": list(self._coverages),
            "trust_floors": list(self._trust_floors),
            "obstructions": list(self._obstructions),
            "lyapunov": list(self._lyapunov_values),
        }

    def summary(self) -> dict[str, Any]:
        """High-level convergence summary."""
        return {
            "steps_recorded": len(self._obligations),
            "is_converging": self.is_converging(),
            "convergence_rate": self.convergence_rate(),
            "estimated_steps": self.estimated_steps_to_convergence(),
            "is_diverging": self.detect_divergence(),
            "certificates_issued": len(self._certificates),
            "obligation_trend": self._obligation_trend(),
            "drift_trend": self._drift_trend(),
            "coverage_trend": self._coverage_trend(),
        }
