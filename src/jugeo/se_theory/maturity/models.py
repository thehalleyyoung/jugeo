r"""Shared dataclass models for the ``jugeo.se_theory.maturity`` package.

Theory (JuGeo — "Continuous Maturity as Sheaf Descent", B10):
    Maturity levels correspond to descent conditions in the evidence sheaf:

    * LEVEL_0_RAW           — code exists; no evidence
    * LEVEL_1_LOCAL_EVIDENCE — local sections (tests) at ≥ 50 % of coords
    * LEVEL_2_LOCAL_DESCENT  — local sections glue within packages
    * LEVEL_3_GLOBAL_DESCENT — cross-package consistency via morphisms
    * LEVEL_4_CERTIFIED      — proof-carrying certificates for the full site

    The ``ImprovementCycle`` tracks a single ASSESS→PRIORITIZE→REPAIR→
    CERTIFY→COMPLETE loop.  ``MaturityTracker`` accumulates history and
    derives ``MaturityTrend`` statistics.

    copilot: se-theory-maturity-models
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

__all__ = [
    # Enums
    "MaturityLevel",
    # Dataclasses
    "MaturityCriterion",
    "MaturityAssessment",
    "ImprovementCycle",
    "ImprovementPlan",
    "MaturityTrend",
    "CyclicSchedule",
    "MaturityReport",
]


# ---------------------------------------------------------------------------
# Internal helpers (must come before dataclass field defaults)
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    import datetime

    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MaturityLevel(IntEnum):
    """Discrete maturity level of a site or package.

    Levels increase as the descent condition becomes more fully satisfied:

    * ``LEVEL_0_RAW``            — 0: code exists, zero evidence
    * ``LEVEL_1_LOCAL_EVIDENCE`` — 1: local tests / witnesses exist
    * ``LEVEL_2_LOCAL_DESCENT``  — 2: intra-package gluing verified
    * ``LEVEL_3_GLOBAL_DESCENT`` — 3: cross-package morphism consistency
    * ``LEVEL_4_CERTIFIED``      — 4: full certificates issued
    """

    LEVEL_0_RAW = 0
    LEVEL_1_LOCAL_EVIDENCE = 1
    LEVEL_2_LOCAL_DESCENT = 2
    LEVEL_3_GLOBAL_DESCENT = 3
    LEVEL_4_CERTIFIED = 4


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MaturityCriterion:
    """A single named criterion that must be satisfied to reach a maturity level.

    Attributes
    ----------
    level:
        The maturity level this criterion gates.
    name:
        Short identifier for the criterion.
    description:
        Human-readable explanation of what must hold.
    required_metrics:
        Mapping of metric names to their minimum threshold values,
        e.g. ``{"evidence_coverage": 0.5, "test_pass_rate": 0.9}``.
    """

    level: MaturityLevel = MaturityLevel.LEVEL_0_RAW
    name: str = ""
    description: str = ""
    required_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": int(self.level),
            "name": self.name,
            "description": self.description,
            "required_metrics": dict(self.required_metrics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaturityCriterion:
        return cls(
            level=MaturityLevel(int(data.get("level", 0))),
            name=data.get("name", ""),
            description=data.get("description", ""),
            required_metrics=dict(data.get("required_metrics", {})),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"MaturityCriterion(level={self.level.name}, name={self.name!r})"
        )


@dataclass
class MaturityAssessment:
    """Result of a full maturity assessment of a site.

    Attributes
    ----------
    site_id:
        Identifier of the site being assessed.
    overall_level:
        The minimum maturity level across all packages (the bottleneck level).
    by_package:
        Per-package maturity level, keyed by package ID string.
    criteria_met:
        Names of criteria that are currently satisfied.
    criteria_unmet:
        Names of criteria that are not yet satisfied.
    blocking_issues:
        List of human-readable issue descriptions preventing the next level.
    recommendations:
        List of recommended actions to improve maturity.
    computed_at:
        ISO-8601 timestamp of when the assessment was computed.
    """

    site_id: str = ""
    overall_level: MaturityLevel = MaturityLevel.LEVEL_0_RAW
    by_package: dict[str, MaturityLevel] = field(default_factory=dict)
    criteria_met: list[str] = field(default_factory=list)
    criteria_unmet: list[str] = field(default_factory=list)
    blocking_issues: list[Any] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    computed_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "overall_level": int(self.overall_level),
            "by_package": {k: int(v) for k, v in self.by_package.items()},
            "criteria_met": list(self.criteria_met),
            "criteria_unmet": list(self.criteria_unmet),
            "blocking_issues": list(self.blocking_issues),
            "recommendations": list(self.recommendations),
            "computed_at": self.computed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaturityAssessment:
        return cls(
            site_id=data.get("site_id", ""),
            overall_level=MaturityLevel(int(data.get("overall_level", 0))),
            by_package={
                k: MaturityLevel(int(v))
                for k, v in data.get("by_package", {}).items()
            },
            criteria_met=list(data.get("criteria_met", [])),
            criteria_unmet=list(data.get("criteria_unmet", [])),
            blocking_issues=list(data.get("blocking_issues", [])),
            recommendations=list(data.get("recommendations", [])),
            computed_at=data.get("computed_at", _iso_now()),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"MaturityAssessment(site={self.site_id!r}, "
            f"level={self.overall_level.name})"
        )


@dataclass
class ImprovementCycle:
    """A single ASSESS→PRIORITIZE→REPAIR→CERTIFY→COMPLETE improvement cycle.

    Attributes
    ----------
    cycle_id:
        Unique cycle identifier.
    phase:
        Current phase: ASSESS / PRIORITIZE / REPAIR / CERTIFY / COMPLETE.
    started_at:
        ISO-8601 timestamp when the cycle began.
    completed_at:
        Optional ISO-8601 timestamp when the cycle finished.
    assessment_before:
        ``MaturityAssessment`` taken at the start of the cycle.
    assessment_after:
        ``MaturityAssessment`` taken at the end (after repairs/certs).
    repairs_applied:
        IDs or descriptions of repairs applied in the REPAIR phase.
    certificates_issued:
        IDs of certificates issued in the CERTIFY phase.
    level_before:
        Overall maturity level at cycle start.
    level_after:
        Overall maturity level at cycle end (``None`` until COMPLETE).
    """

    cycle_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    phase: str = "ASSESS"
    started_at: str = field(default_factory=_iso_now)
    completed_at: Optional[str] = None
    assessment_before: Optional[MaturityAssessment] = None
    assessment_after: Optional[MaturityAssessment] = None
    repairs_applied: list[str] = field(default_factory=list)
    certificates_issued: list[str] = field(default_factory=list)
    level_before: MaturityLevel = MaturityLevel.LEVEL_0_RAW
    level_after: Optional[MaturityLevel] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "phase": self.phase,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "assessment_before": (
                self.assessment_before.to_dict()
                if self.assessment_before
                else None
            ),
            "assessment_after": (
                self.assessment_after.to_dict()
                if self.assessment_after
                else None
            ),
            "repairs_applied": list(self.repairs_applied),
            "certificates_issued": list(self.certificates_issued),
            "level_before": int(self.level_before),
            "level_after": (
                int(self.level_after) if self.level_after is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImprovementCycle:
        ab_raw = data.get("assessment_before")
        aa_raw = data.get("assessment_after")
        la_raw = data.get("level_after")
        return cls(
            cycle_id=data.get("cycle_id", uuid.uuid4().hex[:16]),
            phase=data.get("phase", "ASSESS"),
            started_at=data.get("started_at", _iso_now()),
            completed_at=data.get("completed_at"),
            assessment_before=(
                MaturityAssessment.from_dict(ab_raw) if ab_raw else None
            ),
            assessment_after=(
                MaturityAssessment.from_dict(aa_raw) if aa_raw else None
            ),
            repairs_applied=list(data.get("repairs_applied", [])),
            certificates_issued=list(data.get("certificates_issued", [])),
            level_before=MaturityLevel(int(data.get("level_before", 0))),
            level_after=(
                MaturityLevel(int(la_raw)) if la_raw is not None else None
            ),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ImprovementCycle(id={self.cycle_id!r}, phase={self.phase}, "
            f"level_before={self.level_before.name})"
        )


@dataclass
class ImprovementPlan:
    """A structured plan for advancing the maturity level.

    Attributes
    ----------
    id:
        Unique plan identifier.
    current_level:
        Current overall maturity level.
    target_level:
        Desired maturity level.
    required_actions:
        Ordered list of action dicts, each with keys:
        ``action`` (str), ``coordinates`` (list[str]),
        ``estimated_effort`` (float).
    estimated_cycles:
        Number of improvement cycles estimated to reach ``target_level``.
    blocking_dependencies:
        List of action IDs or descriptions that must complete first.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    current_level: MaturityLevel = MaturityLevel.LEVEL_0_RAW
    target_level: MaturityLevel = MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
    required_actions: list[dict[str, Any]] = field(default_factory=list)
    estimated_cycles: int = 1
    blocking_dependencies: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "current_level": int(self.current_level),
            "target_level": int(self.target_level),
            "required_actions": list(self.required_actions),
            "estimated_cycles": self.estimated_cycles,
            "blocking_dependencies": list(self.blocking_dependencies),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImprovementPlan:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            current_level=MaturityLevel(int(data.get("current_level", 0))),
            target_level=MaturityLevel(int(data.get("target_level", 1))),
            required_actions=list(data.get("required_actions", [])),
            estimated_cycles=int(data.get("estimated_cycles", 1)),
            blocking_dependencies=list(data.get("blocking_dependencies", [])),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ImprovementPlan(id={self.id!r}, "
            f"{self.current_level.name}→{self.target_level.name})"
        )


@dataclass
class MaturityTrend:
    """Historical trend of maturity levels over a rolling window.

    Attributes
    ----------
    timestamps:
        ISO-8601 timestamps of each recorded assessment.
    levels:
        Overall maturity level (int) at each timestamp.
    by_package_trends:
        Per-package level history, keyed by package ID.
    improving_packages:
        Package IDs whose level increased in the window.
    degrading_packages:
        Package IDs whose level decreased in the window.
    stagnant_packages:
        Package IDs whose level did not change in the window.
    """

    timestamps: list[str] = field(default_factory=list)
    levels: list[int] = field(default_factory=list)
    by_package_trends: dict[str, list[int]] = field(default_factory=dict)
    improving_packages: list[str] = field(default_factory=list)
    degrading_packages: list[str] = field(default_factory=list)
    stagnant_packages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamps": list(self.timestamps),
            "levels": list(self.levels),
            "by_package_trends": {
                k: list(v) for k, v in self.by_package_trends.items()
            },
            "improving_packages": list(self.improving_packages),
            "degrading_packages": list(self.degrading_packages),
            "stagnant_packages": list(self.stagnant_packages),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaturityTrend:
        return cls(
            timestamps=list(data.get("timestamps", [])),
            levels=list(data.get("levels", [])),
            by_package_trends={
                k: list(v)
                for k, v in data.get("by_package_trends", {}).items()
            },
            improving_packages=list(data.get("improving_packages", [])),
            degrading_packages=list(data.get("degrading_packages", [])),
            stagnant_packages=list(data.get("stagnant_packages", [])),
        )


@dataclass
class CyclicSchedule:
    """Configuration for automated improvement cycle scheduling.

    Attributes
    ----------
    frequency:
        Cadence: CONTINUOUS / DAILY / WEEKLY / SPRINT.
    next_cycle_at:
        ISO-8601 timestamp of the next scheduled cycle.
    auto_repair_enabled:
        Whether the REPAIR phase runs automatically.
    auto_certify_enabled:
        Whether the CERTIFY phase runs automatically.
    notification_targets:
        Team IDs or email addresses to notify on cycle events.
    max_cycle_duration_s:
        Maximum allowed wall-clock seconds for a single cycle.
    """

    frequency: str = "WEEKLY"
    next_cycle_at: str = field(default_factory=_iso_now)
    auto_repair_enabled: bool = False
    auto_certify_enabled: bool = False
    notification_targets: list[str] = field(default_factory=list)
    max_cycle_duration_s: float = 3600.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency,
            "next_cycle_at": self.next_cycle_at,
            "auto_repair_enabled": self.auto_repair_enabled,
            "auto_certify_enabled": self.auto_certify_enabled,
            "notification_targets": list(self.notification_targets),
            "max_cycle_duration_s": self.max_cycle_duration_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CyclicSchedule:
        return cls(
            frequency=data.get("frequency", "WEEKLY"),
            next_cycle_at=data.get("next_cycle_at", _iso_now()),
            auto_repair_enabled=bool(data.get("auto_repair_enabled", False)),
            auto_certify_enabled=bool(data.get("auto_certify_enabled", False)),
            notification_targets=list(data.get("notification_targets", [])),
            max_cycle_duration_s=float(
                data.get("max_cycle_duration_s", 3600.0)
            ),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CyclicSchedule(frequency={self.frequency}, "
            f"next={self.next_cycle_at!r})"
        )


@dataclass
class MaturityReport:
    """Comprehensive maturity report for a site.

    Attributes
    ----------
    assessment:
        Most recent maturity assessment.
    trend:
        Historical trend data.
    current_cycle:
        The currently active improvement cycle, if any.
    plan:
        Improvement plan to reach the next level.
    schedule:
        Cycle scheduling configuration.
    """

    assessment: MaturityAssessment = field(
        default_factory=MaturityAssessment
    )
    trend: MaturityTrend = field(default_factory=MaturityTrend)
    current_cycle: Optional[ImprovementCycle] = None
    plan: ImprovementPlan = field(default_factory=ImprovementPlan)
    schedule: CyclicSchedule = field(default_factory=CyclicSchedule)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment": self.assessment.to_dict(),
            "trend": self.trend.to_dict(),
            "current_cycle": (
                self.current_cycle.to_dict() if self.current_cycle else None
            ),
            "plan": self.plan.to_dict(),
            "schedule": self.schedule.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaturityReport:
        cc_raw = data.get("current_cycle")
        return cls(
            assessment=MaturityAssessment.from_dict(
                data.get("assessment", {})
            ),
            trend=MaturityTrend.from_dict(data.get("trend", {})),
            current_cycle=(
                ImprovementCycle.from_dict(cc_raw) if cc_raw else None
            ),
            plan=ImprovementPlan.from_dict(data.get("plan", {})),
            schedule=CyclicSchedule.from_dict(data.get("schedule", {})),
        )

