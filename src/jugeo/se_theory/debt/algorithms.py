"""Technical debt algorithms: analysis, tracking, prioritization, gating.

Implements the computational core of the debt module:

* **DebtAnalyzer** — computes individual debt metrics (obstruction density,
  trust floor, evidence staleness, cover quality, repair backlog) and
  assembles a full :class:`DebtReport`.
* **DebtTracker** — records snapshots over time and computes trends
  (slope, improving/worsening, projection).
* **DebtPrioritizer** — ranks obstructions by ROI and allocates a repair
  budget greedily.
* **DebtGatekeeper** — checks metrics against thresholds and decides
  whether a release should be blocked.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from jugeo.se_theory.debt.models import (
    DebtAlert,
    DebtKind,
    DebtMetric,
    DebtPrioritization,
    DebtReport,
    DebtThreshold,
    DebtTrend,
)


# ---------------------------------------------------------------------------
# Trust ordering helper
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = ["claim", "conjecture", "heuristic", "proof", "verified"]


def _trust_rank(level: str) -> int:
    try:
        return _TRUST_ORDER.index(level.lower().strip())
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# DebtAnalyzer
# ---------------------------------------------------------------------------


class DebtAnalyzer:
    """Computes individual debt metrics and assembles full reports."""

    TRUST_ORDER: list[str] = _TRUST_ORDER

    def compute_obstruction_density(
        self,
        obstructions: list[Any],
        coordinate_count: int,
    ) -> float:
        """Return ``len(obstructions) / max(coordinate_count, 1)``."""
        return len(obstructions) / max(coordinate_count, 1)

    def compute_trust_floor(
        self,
        trust_levels: dict[str, str],
    ) -> str:
        """Return the minimum trust level across all values, default ``"claim"``."""
        if not trust_levels:
            return "claim"
        min_rank = min(_trust_rank(v) for v in trust_levels.values())
        if min_rank < 0:
            return "claim"
        return self.TRUST_ORDER[min_rank]

    def compute_evidence_staleness(
        self,
        evidence_timestamps: dict[str, float],
        code_change_times: dict[str, float],
    ) -> float:
        """Compute average staleness in days.

        For each coordinate, if last code change timestamp > last evidence
        timestamp, count the difference as staleness.
        """
        if not evidence_timestamps and not code_change_times:
            return 0.0

        total_staleness = 0.0
        count = 0
        all_coords = set(evidence_timestamps) | set(code_change_times)
        for coord in all_coords:
            ev_time = evidence_timestamps.get(coord, 0.0)
            code_time = code_change_times.get(coord, 0.0)
            if code_time > ev_time:
                staleness_seconds = code_time - ev_time
                total_staleness += staleness_seconds / 86400.0  # convert to days
            count += 1

        return total_staleness / max(count, 1)

    def compute_cover_quality(
        self,
        cover_members: list[str],
        morphisms: dict[str, list[str]],
    ) -> tuple[float, float]:
        """Compute coupling and cohesion of the cover.

        * coupling = edges_between_members / max(total_possible_edges, 1)
        * cohesion = 1.0 - coupling  (simplified)

        Returns ``(coupling, cohesion)``.
        """
        member_set = set(cover_members)
        n = len(member_set)
        if n <= 1:
            return (0.0, 1.0)

        total_possible = n * (n - 1) / 2
        edge_count = 0
        counted: set[tuple[str, str]] = set()
        for member in member_set:
            for neighbour in morphisms.get(member, []):
                if neighbour in member_set:
                    edge = (min(member, neighbour), max(member, neighbour))
                    if edge not in counted:
                        counted.add(edge)
                        edge_count += 1

        coupling = edge_count / max(total_possible, 1)
        cohesion = 1.0 - coupling
        return (coupling, cohesion)

    def compute_repair_backlog(
        self,
        repair_frontiers: dict[str, list[str]],
    ) -> int:
        """Return total count of coordinates across all repair frontiers."""
        return sum(len(coords) for coords in repair_frontiers.values())

    def full_debt_report(
        self,
        obstructions: list[Any],
        trust_levels: dict[str, str],
        evidence: dict[str, float],
        covers: list[str],
        morphisms: dict[str, list[str]],
        code_changes: dict[str, float],
        repair_frontiers: Optional[dict[str, list[str]]] = None,
        site_id: str = "",
    ) -> DebtReport:
        """Compute all metrics and assemble a :class:`DebtReport`.

        ``total_debt_score`` is a weighted average of normalised metrics (0–100).
        """
        coord_count = len(trust_levels) if trust_levels else 1
        obstruction_density = self.compute_obstruction_density(
            obstructions, coord_count
        )
        trust_floor = self.compute_trust_floor(trust_levels)
        avg_staleness = self.compute_evidence_staleness(evidence, code_changes)
        coupling, cohesion = self.compute_cover_quality(covers, morphisms)
        backlog = self.compute_repair_backlog(repair_frontiers or {})

        metrics: list[DebtMetric] = [
            DebtMetric(
                kind=DebtKind.OBSTRUCTION_ACCUMULATION,
                value=obstruction_density,
                details=f"{len(obstructions)} obstructions in {coord_count} coordinates",
            ),
            DebtMetric(
                kind=DebtKind.TRUST_FLOOR_EROSION,
                value=float(_trust_rank(trust_floor)),
                details=f"Trust floor is '{trust_floor}'",
            ),
            DebtMetric(
                kind=DebtKind.EVIDENCE_STALENESS,
                value=avg_staleness,
                details=f"Average evidence staleness: {avg_staleness:.1f} days",
            ),
            DebtMetric(
                kind=DebtKind.COVER_QUALITY_DEGRADATION,
                value=coupling,
                details=f"Cover coupling={coupling:.2f}, cohesion={cohesion:.2f}",
            ),
            DebtMetric(
                kind=DebtKind.REPAIR_BACKLOG,
                value=float(backlog),
                details=f"{backlog} coordinates in repair frontiers",
            ),
        ]

        # Weighted debt score (0-100)
        # obstruction_density: weight 30, max meaningful value ~1.0
        # trust floor: weight 20, normalised 0-4 -> 0-1
        # staleness: weight 20, cap at 30 days
        # coupling: weight 15, already 0-1
        # backlog: weight 15, cap at 50
        w_obstruction = 30.0 * min(obstruction_density, 1.0)
        trust_floor_norm = 1.0 - (_trust_rank(trust_floor) / max(len(_TRUST_ORDER) - 1, 1))
        w_trust = 20.0 * trust_floor_norm
        w_staleness = 20.0 * min(avg_staleness / 30.0, 1.0)
        w_coupling = 15.0 * coupling
        w_backlog = 15.0 * min(backlog / 50.0, 1.0)
        total_debt_score = w_obstruction + w_trust + w_staleness + w_coupling + w_backlog

        return DebtReport(
            site_id=site_id,
            metrics=metrics,
            obstruction_density=obstruction_density,
            trust_floor=trust_floor,
            avg_evidence_age_days=avg_staleness,
            cover_coupling=coupling,
            cover_cohesion=cohesion,
            repair_frontier_total=backlog,
            total_debt_score=max(0.0, min(100.0, total_debt_score)),
        )

    def debt_by_package(
        self,
        report_data: dict[str, Any],
        package_prefixes: list[str],
    ) -> dict[str, float]:
        """Group coordinates by package prefix and return average debt scores.

        ``report_data`` maps coord_id -> debt_score (numeric).
        """
        result: dict[str, float] = {}
        for prefix in package_prefixes:
            scores = [
                v
                for k, v in report_data.items()
                if k.startswith(prefix)
            ]
            if scores:
                result[prefix] = sum(scores) / len(scores)
            else:
                result[prefix] = 0.0
        return result


# ---------------------------------------------------------------------------
# DebtTracker
# ---------------------------------------------------------------------------


class DebtTracker:
    """Records debt report snapshots over time and computes trends."""

    def __init__(self) -> None:
        self._snapshots: list[DebtReport] = []

    def record_snapshot(self, report: DebtReport) -> None:
        """Append a snapshot to the history."""
        self._snapshots.append(report)

    def compute_trends(self, window_size: int = 10) -> list[DebtTrend]:
        """For each :class:`DebtKind`, extract values from the last
        *window_size* snapshots and compute slope via simple linear regression.

        ``is_improving = slope < 0`` (debt decreasing).
        """
        recent = self._snapshots[-window_size:]
        kind_values: dict[DebtKind, list[tuple[str, float]]] = {}

        for snap in recent:
            for metric in snap.metrics:
                kind_values.setdefault(metric.kind, []).append(
                    (snap.computed_at, metric.value)
                )

        trends: list[DebtTrend] = []
        for kind in DebtKind:
            entries = kind_values.get(kind, [])
            timestamps = [e[0] for e in entries]
            values = [e[1] for e in entries]

            if len(values) < 2:
                slope = 0.0
            else:
                slope = self._simple_slope(values)

            trends.append(
                DebtTrend(
                    metric_kind=kind,
                    timestamps=timestamps,
                    values=values,
                    slope=slope,
                    is_improving=slope < 0,
                )
            )
        return trends

    def is_improving(self, kind: DebtKind) -> bool:
        """Check if slope for *kind* is negative."""
        trends = self.compute_trends()
        for trend in trends:
            if trend.metric_kind == kind:
                return trend.is_improving
        return False

    def projected_value(self, kind: DebtKind, steps_ahead: int) -> float:
        """Linear projection: ``last_value + slope * steps_ahead``."""
        trends = self.compute_trends()
        for trend in trends:
            if trend.metric_kind == kind:
                last = trend.values[-1] if trend.values else 0.0
                return last + trend.slope * steps_ahead
        return 0.0

    @staticmethod
    def _simple_slope(values: list[float]) -> float:
        """Simple linear regression slope over integer indices."""
        n = len(values)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0:
            return 0.0
        return numerator / denominator


# ---------------------------------------------------------------------------
# DebtPrioritizer
# ---------------------------------------------------------------------------


class DebtPrioritizer:
    """Ranks obstructions by ROI and allocates a repair budget greedily."""

    _SEVERITY_WEIGHTS: dict[str, float] = {
        "high": 1.0,
        "medium": 0.6,
        "low": 0.3,
    }

    def prioritize_repairs(
        self,
        obstructions: list[Any],
        morphisms: dict[str, list[str]],
        code_complexity: dict[str, float],
    ) -> list[DebtPrioritization]:
        """For each obstruction, compute debt_score, repair_cost, and ROI.

        Obstructions are dicts with ``"coordinate_id"`` and optional
        ``"severity"`` keys.

        ``debt_score = severity_weight * (1 + morphisms_count * 0.1)``
        ``repair_cost = code_complexity.get(coord_id, 1.0)``
        ``roi = debt_score / max(repair_cost, 0.01)``

        Returns list sorted by ROI descending.
        """
        items: list[DebtPrioritization] = []
        for obs in obstructions:
            if isinstance(obs, dict):
                coord_id = obs.get("coordinate_id", "")
                severity = obs.get("severity", "medium")
            else:
                coord_id = str(obs)
                severity = "medium"

            severity_weight = self._SEVERITY_WEIGHTS.get(severity.lower(), 0.6)
            morphism_count = len(morphisms.get(coord_id, []))
            debt_score = severity_weight * (1 + morphism_count * 0.1)
            repair_cost = code_complexity.get(coord_id, 1.0)
            roi = debt_score / max(repair_cost, 0.01)

            items.append(
                DebtPrioritization(
                    coordinate_id=coord_id,
                    debt_score=debt_score,
                    repair_cost=repair_cost,
                    roi=roi,
                    recommended_action=f"Repair obstruction at {coord_id}",
                )
            )

        items.sort(key=lambda x: x.roi, reverse=True)
        return items

    def budget_allocation(
        self,
        total_budget: float,
        priorities: list[DebtPrioritization],
    ) -> dict[str, float]:
        """Greedy: allocate budget to highest ROI items first.

        Returns ``{coord_id: allocated_budget}``.
        """
        allocated: dict[str, float] = {}
        remaining = total_budget
        for item in priorities:
            if remaining <= 0:
                break
            alloc = min(item.repair_cost, remaining)
            allocated[item.coordinate_id] = alloc
            remaining -= alloc
        return allocated

    def quick_wins(
        self,
        priorities: list[DebtPrioritization],
        max_cost: float,
    ) -> list[DebtPrioritization]:
        """Return items where ``repair_cost <= max_cost``, sorted by ROI descending."""
        wins = [p for p in priorities if p.repair_cost <= max_cost]
        wins.sort(key=lambda x: x.roi, reverse=True)
        return wins


# ---------------------------------------------------------------------------
# DebtGatekeeper
# ---------------------------------------------------------------------------


class DebtGatekeeper:
    """Checks metrics against thresholds and decides whether to block release."""

    def check_thresholds(
        self,
        report: DebtReport,
        thresholds: list[DebtThreshold],
    ) -> list[DebtAlert]:
        """For each threshold, find the matching metric and raise alerts."""
        alerts: list[DebtAlert] = []
        metric_by_kind: dict[DebtKind, DebtMetric] = {}
        for metric in report.metrics:
            metric_by_kind[metric.kind] = metric

        for threshold in thresholds:
            metric = metric_by_kind.get(threshold.kind)
            if metric is None:
                continue
            value = metric.value

            if value >= threshold.block_level:
                alerts.append(
                    DebtAlert(
                        kind=threshold.kind,
                        level="BLOCK",
                        current_value=value,
                        threshold_value=threshold.block_level,
                        scope=threshold.scope or "",
                        message=f"{threshold.kind.value} at {value:.2f} >= block threshold {threshold.block_level:.2f}",
                        suggested_action=f"Reduce {threshold.kind.value} below {threshold.block_level:.2f}.",
                    )
                )
            elif value >= threshold.error_level:
                alerts.append(
                    DebtAlert(
                        kind=threshold.kind,
                        level="ERROR",
                        current_value=value,
                        threshold_value=threshold.error_level,
                        scope=threshold.scope or "",
                        message=f"{threshold.kind.value} at {value:.2f} >= error threshold {threshold.error_level:.2f}",
                        suggested_action=f"Reduce {threshold.kind.value} below {threshold.error_level:.2f}.",
                    )
                )
            elif value >= threshold.warning_level:
                alerts.append(
                    DebtAlert(
                        kind=threshold.kind,
                        level="WARNING",
                        current_value=value,
                        threshold_value=threshold.warning_level,
                        scope=threshold.scope or "",
                        message=f"{threshold.kind.value} at {value:.2f} >= warning threshold {threshold.warning_level:.2f}",
                        suggested_action=f"Monitor {threshold.kind.value}.",
                    )
                )

        return alerts

    def should_block_release(self, alerts: list[DebtAlert]) -> bool:
        """Return True if any alert has level ``BLOCK``."""
        return any(a.level == "BLOCK" for a in alerts)

    def gate_report(
        self,
        report: DebtReport,
        thresholds: list[DebtThreshold],
    ) -> dict[str, Any]:
        """Return a summary dict with pass/block status, alerts, and metrics."""
        alerts = self.check_thresholds(report, thresholds)
        blocked = self.should_block_release(alerts)

        metrics_summary: dict[str, float] = {}
        for metric in report.metrics:
            metrics_summary[metric.kind.value] = metric.value

        return {
            "passed": not blocked,
            "blocked": blocked,
            "alerts": [a.to_dict() for a in alerts],
            "metrics_summary": metrics_summary,
        }
