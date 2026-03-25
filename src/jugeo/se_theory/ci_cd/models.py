"""CI/CD models: pipeline stages, verification tasks, and certificates.

In the judgment-geometry framework, CI/CD corresponds to the construction
and verification of local evidence sections at various pipeline stages.
A *certificate* is a global section attesting that descent holds across
all overlaps at a given trust level.

Pipeline stages mirror the standard CI/CD flow but with geometric
verification at each gate:

* PRE_COMMIT  — local checks on the developer's machine.
* PRE_MERGE   — checks on the PR branch before merge.
* POST_MERGE  — full verification after merge into the main branch.
* RELEASE_GATE — final gate before release.
* DEPLOYMENT   — post-deployment verification.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    # Enums
    "PipelineStage",
    # Dataclasses
    "StageRequirement",
    "VerificationTask",
    "StageResult",
    "PipelineResult",
    "Certificate",
    "IncrementalScope",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PipelineStage(str, Enum):
    """Stages of the CI/CD pipeline mapped to geometric verification gates."""

    PRE_COMMIT = "pre_commit"
    PRE_MERGE = "pre_merge"
    POST_MERGE = "post_merge"
    RELEASE_GATE = "release_gate"
    DEPLOYMENT = "deployment"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StageRequirement:
    """Requirements that must be met at a particular pipeline stage."""

    stage: PipelineStage = PipelineStage.PRE_COMMIT
    scope: str = "LOCAL"
    trust_minimum: str = "claim"
    max_duration_s: float = 60.0
    required_coverage: float = 0.0
    allow_known_obstructions: bool = True
    max_obstruction_severity: str = "low"

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "scope": self.scope,
            "trust_minimum": self.trust_minimum,
            "max_duration_s": self.max_duration_s,
            "required_coverage": self.required_coverage,
            "allow_known_obstructions": self.allow_known_obstructions,
            "max_obstruction_severity": self.max_obstruction_severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageRequirement:
        return cls(
            stage=PipelineStage(data["stage"]),
            scope=data.get("scope", "LOCAL"),
            trust_minimum=data.get("trust_minimum", "claim"),
            max_duration_s=data.get("max_duration_s", 60.0),
            required_coverage=data.get("required_coverage", 0.0),
            allow_known_obstructions=data.get("allow_known_obstructions", True),
            max_obstruction_severity=data.get("max_obstruction_severity", "low"),
        )


@dataclass
class VerificationTask:
    """A single verification task to be executed in a pipeline stage."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    stage: PipelineStage = PipelineStage.PRE_COMMIT
    coordinates: list[str] = field(default_factory=list)
    overlaps_to_check: list[str] = field(default_factory=list)
    trust_target: str = "claim"
    priority: float = 1.0
    estimated_duration_s: float = 1.0

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage.value,
            "coordinates": list(self.coordinates),
            "overlaps_to_check": list(self.overlaps_to_check),
            "trust_target": self.trust_target,
            "priority": self.priority,
            "estimated_duration_s": self.estimated_duration_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationTask:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            stage=PipelineStage(data["stage"]),
            coordinates=list(data.get("coordinates", [])),
            overlaps_to_check=list(data.get("overlaps_to_check", [])),
            trust_target=data.get("trust_target", "claim"),
            priority=data.get("priority", 1.0),
            estimated_duration_s=data.get("estimated_duration_s", 1.0),
        )


@dataclass
class StageResult:
    """Outcome of executing all tasks in a pipeline stage."""

    stage: PipelineStage = PipelineStage.PRE_COMMIT
    tasks_run: int = 0
    tasks_passed: int = 0
    tasks_failed: int = 0
    obstructions_found: list[str] = field(default_factory=list)
    trust_achieved: str = "claim"
    coverage_achieved: float = 0.0
    duration_s: float = 0.0
    passed: bool = False

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "tasks_run": self.tasks_run,
            "tasks_passed": self.tasks_passed,
            "tasks_failed": self.tasks_failed,
            "obstructions_found": list(self.obstructions_found),
            "trust_achieved": self.trust_achieved,
            "coverage_achieved": self.coverage_achieved,
            "duration_s": self.duration_s,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageResult:
        return cls(
            stage=PipelineStage(data["stage"]),
            tasks_run=data.get("tasks_run", 0),
            tasks_passed=data.get("tasks_passed", 0),
            tasks_failed=data.get("tasks_failed", 0),
            obstructions_found=list(data.get("obstructions_found", [])),
            trust_achieved=data.get("trust_achieved", "claim"),
            coverage_achieved=data.get("coverage_achieved", 0.0),
            duration_s=data.get("duration_s", 0.0),
            passed=data.get("passed", False),
        )


@dataclass
class PipelineResult:
    """Aggregated outcome of all pipeline stages."""

    stages: list[StageResult] = field(default_factory=list)
    overall_passed: bool = False
    certificate_issued: bool = False
    certificate_id: Optional[str] = None
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [s.to_dict() for s in self.stages],
            "overall_passed": self.overall_passed,
            "certificate_issued": self.certificate_issued,
            "certificate_id": self.certificate_id,
            "blocking_issues": list(self.blocking_issues),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineResult:
        return cls(
            stages=[
                StageResult.from_dict(s) for s in data.get("stages", [])
            ],
            overall_passed=data.get("overall_passed", False),
            certificate_issued=data.get("certificate_issued", False),
            certificate_id=data.get("certificate_id"),
            blocking_issues=list(data.get("blocking_issues", [])),
            warnings=list(data.get("warnings", [])),
        )


@dataclass
class Certificate:
    """A certificate attesting that descent holds at a given trust level."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    site_id: str = ""
    version: str = "1.0"
    trust_levels: dict[str, str] = field(default_factory=dict)
    coverage: float = 0.0
    residual_obligations: list[str] = field(default_factory=list)
    issued_at: str = field(
        default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat()
    )
    expires_at: Optional[str] = None
    issuer: str = "jugeo-ci"
    signature: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "site_id": self.site_id,
            "version": self.version,
            "trust_levels": dict(self.trust_levels),
            "coverage": self.coverage,
            "residual_obligations": list(self.residual_obligations),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issuer": self.issuer,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Certificate:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            site_id=data.get("site_id", ""),
            version=data.get("version", "1.0"),
            trust_levels=dict(data.get("trust_levels", {})),
            coverage=data.get("coverage", 0.0),
            residual_obligations=list(data.get("residual_obligations", [])),
            issued_at=data.get("issued_at", ""),
            expires_at=data.get("expires_at"),
            issuer=data.get("issuer", "jugeo-ci"),
            signature=data.get("signature", ""),
        )


@dataclass
class IncrementalScope:
    """Scope of an incremental verification pass triggered by a code change."""

    change_id: str = ""
    changed_files: list[str] = field(default_factory=list)
    affected_coordinates: list[str] = field(default_factory=list)
    affected_overlaps: list[str] = field(default_factory=list)
    stages_needed: list[PipelineStage] = field(default_factory=list)
    estimated_total_duration_s: float = 0.0

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "changed_files": list(self.changed_files),
            "affected_coordinates": list(self.affected_coordinates),
            "affected_overlaps": list(self.affected_overlaps),
            "stages_needed": [s.value for s in self.stages_needed],
            "estimated_total_duration_s": self.estimated_total_duration_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IncrementalScope:
        return cls(
            change_id=data.get("change_id", ""),
            changed_files=list(data.get("changed_files", [])),
            affected_coordinates=list(data.get("affected_coordinates", [])),
            affected_overlaps=list(data.get("affected_overlaps", [])),
            stages_needed=[
                PipelineStage(s) for s in data.get("stages_needed", [])
            ],
            estimated_total_duration_s=data.get("estimated_total_duration_s", 0.0),
        )
