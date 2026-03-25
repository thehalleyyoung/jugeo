"""Technical debt models: measuring and managing judgment-geometry site health.

In the judgment-geometry framework, *technical debt* manifests as degradation
of the site's geometric properties:

* Obstruction accumulation — growing number of coordinates where descent
  fails or sections are missing.
* Trust floor erosion — the minimum trust level across the site drops.
* Cover quality degradation — the covering becomes less cohesive or
  overly coupled.
* Evidence staleness — evidence sections become outdated relative to code
  changes.
* Repair backlog — the repair frontier grows as obstructions are deferred.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    # Enums
    "DebtKind",
    # Dataclasses
    "DebtMetric",
    "DebtReport",
    "DebtTrend",
    "DebtThreshold",
    "DebtAlert",
    "DebtPrioritization",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DebtKind(str, Enum):
    """Categories of technical debt in the judgment-geometry framework."""

    OBSTRUCTION_ACCUMULATION = "obstruction_accumulation"
    TRUST_FLOOR_EROSION = "trust_floor_erosion"
    COVER_QUALITY_DEGRADATION = "cover_quality_degradation"
    EVIDENCE_STALENESS = "evidence_staleness"
    REPAIR_BACKLOG = "repair_backlog"
    TREATY_VIOLATIONS = "treaty_violations"
    BOUNDARY_EROSION = "boundary_erosion"
    TEST_GAP = "test_gap"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DebtMetric:
    """A single debt measurement for a particular kind."""

    kind: DebtKind = DebtKind.OBSTRUCTION_ACCUMULATION
    value: float = 0.0
    threshold: float = 0.0
    exceeds_threshold: bool = False
    coordinate_scope: Optional[str] = None
    details: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "value": self.value,
            "threshold": self.threshold,
            "exceeds_threshold": self.exceeds_threshold,
            "coordinate_scope": self.coordinate_scope,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebtMetric:
        return cls(
            kind=DebtKind(data["kind"]),
            value=data.get("value", 0.0),
            threshold=data.get("threshold", 0.0),
            exceeds_threshold=data.get("exceeds_threshold", False),
            coordinate_scope=data.get("coordinate_scope"),
            details=data.get("details", ""),
        )


@dataclass
class DebtReport:
    """Comprehensive debt report for a judgment-geometry site."""

    site_id: str = ""
    metrics: list[DebtMetric] = field(default_factory=list)
    obstruction_density: float = 0.0
    trust_floor: str = "claim"
    avg_evidence_age_days: float = 0.0
    cover_coupling: float = 0.0
    cover_cohesion: float = 0.0
    repair_frontier_total: int = 0
    total_debt_score: float = 0.0
    by_package: dict[str, float] = field(default_factory=dict)
    computed_at: str = field(
        default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat()
    )

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "metrics": [m.to_dict() for m in self.metrics],
            "obstruction_density": self.obstruction_density,
            "trust_floor": self.trust_floor,
            "avg_evidence_age_days": self.avg_evidence_age_days,
            "cover_coupling": self.cover_coupling,
            "cover_cohesion": self.cover_cohesion,
            "repair_frontier_total": self.repair_frontier_total,
            "total_debt_score": self.total_debt_score,
            "by_package": dict(self.by_package),
            "computed_at": self.computed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebtReport:
        return cls(
            site_id=data.get("site_id", ""),
            metrics=[
                DebtMetric.from_dict(m) for m in data.get("metrics", [])
            ],
            obstruction_density=data.get("obstruction_density", 0.0),
            trust_floor=data.get("trust_floor", "claim"),
            avg_evidence_age_days=data.get("avg_evidence_age_days", 0.0),
            cover_coupling=data.get("cover_coupling", 0.0),
            cover_cohesion=data.get("cover_cohesion", 0.0),
            repair_frontier_total=data.get("repair_frontier_total", 0),
            total_debt_score=data.get("total_debt_score", 0.0),
            by_package=dict(data.get("by_package", {})),
            computed_at=data.get("computed_at", ""),
        )


@dataclass
class DebtTrend:
    """Tracks how a debt metric evolves over time."""

    metric_kind: DebtKind = DebtKind.OBSTRUCTION_ACCUMULATION
    timestamps: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    slope: float = 0.0
    is_improving: bool = False

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_kind": self.metric_kind.value,
            "timestamps": list(self.timestamps),
            "values": list(self.values),
            "slope": self.slope,
            "is_improving": self.is_improving,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebtTrend:
        return cls(
            metric_kind=DebtKind(data["metric_kind"]),
            timestamps=list(data.get("timestamps", [])),
            values=list(data.get("values", [])),
            slope=data.get("slope", 0.0),
            is_improving=data.get("is_improving", False),
        )


@dataclass
class DebtThreshold:
    """Warning/error/block thresholds for a particular debt kind."""

    kind: DebtKind = DebtKind.OBSTRUCTION_ACCUMULATION
    warning_level: float = 0.3
    error_level: float = 0.6
    block_level: float = 0.9
    scope: Optional[str] = None

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "warning_level": self.warning_level,
            "error_level": self.error_level,
            "block_level": self.block_level,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebtThreshold:
        return cls(
            kind=DebtKind(data["kind"]),
            warning_level=data.get("warning_level", 0.3),
            error_level=data.get("error_level", 0.6),
            block_level=data.get("block_level", 0.9),
            scope=data.get("scope"),
        )


@dataclass
class DebtAlert:
    """An alert raised when a debt metric breaches a threshold."""

    kind: DebtKind = DebtKind.OBSTRUCTION_ACCUMULATION
    level: str = "WARNING"
    current_value: float = 0.0
    threshold_value: float = 0.0
    scope: str = ""
    message: str = ""
    suggested_action: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "level": self.level,
            "current_value": self.current_value,
            "threshold_value": self.threshold_value,
            "scope": self.scope,
            "message": self.message,
            "suggested_action": self.suggested_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebtAlert:
        return cls(
            kind=DebtKind(data["kind"]),
            level=data.get("level", "WARNING"),
            current_value=data.get("current_value", 0.0),
            threshold_value=data.get("threshold_value", 0.0),
            scope=data.get("scope", ""),
            message=data.get("message", ""),
            suggested_action=data.get("suggested_action", ""),
        )


@dataclass
class DebtPrioritization:
    """Prioritisation of a single coordinate for debt repair."""

    coordinate_id: str = ""
    debt_score: float = 0.0
    repair_cost: float = 1.0
    roi: float = 0.0
    recommended_action: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate_id": self.coordinate_id,
            "debt_score": self.debt_score,
            "repair_cost": self.repair_cost,
            "roi": self.roi,
            "recommended_action": self.recommended_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebtPrioritization:
        return cls(
            coordinate_id=data.get("coordinate_id", ""),
            debt_score=data.get("debt_score", 0.0),
            repair_cost=data.get("repair_cost", 1.0),
            roi=data.get("roi", 0.0),
            recommended_action=data.get("recommended_action", ""),
        )
