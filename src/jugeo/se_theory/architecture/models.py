"""Data models for architectural analysis in sheaf-theoretic SE.

Software architecture maps to cover design in sheaf theory:
- Modules are cover members
- Interfaces are overlaps between cover members
- Coupling measures overlap density
- Cohesion measures cover compactness

All models use dataclasses with to_dict/from_dict for serialization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ArchitecturalMetric(str, Enum):
    """Metrics computed over architectural covers."""

    COUPLING = "coupling"
    COHESION = "cohesion"
    INTERFACE_WIDTH = "interface_width"
    DEPENDENCY_DEPTH = "dependency_depth"
    CIRCULAR_DEPS = "circular_deps"
    INSTABILITY = "instability"
    ABSTRACTNESS = "abstractness"
    DISTANCE_FROM_MAIN_SEQUENCE = "distance_from_main_sequence"


class CoverMemberKind(str, Enum):
    """Classification of a cover member's granularity."""

    PACKAGE = "package"
    MODULE = "module"
    CLASS = "class"
    COMPONENT = "component"
    SERVICE = "service"
    LIBRARY = "library"


class ArchitecturalDecisionKind(str, Enum):
    """Kinds of architectural refactoring decisions."""

    EXTRACT_MODULE = "extract_module"
    MERGE_MODULES = "merge_modules"
    DEFINE_INTERFACE = "define_interface"
    ENFORCE_BOUNDARY = "enforce_boundary"
    RESOLVE_CIRCULAR = "resolve_circular"
    SPLIT_PACKAGE = "split_package"
    INLINE_MODULE = "inline_module"


# ---------------------------------------------------------------------------
# Core data models
# ---------------------------------------------------------------------------


@dataclass
class CoverMember:
    """A member of an architectural cover — a module, package, or service.

    In sheaf theory terms, this is one open set in the cover of the
    architectural site.  Its coordinates are the semantic points it
    contains, and its morphisms describe internal structure and
    external connections.
    """

    id: str
    name: str
    kind: CoverMemberKind
    coordinates: list[str] = field(default_factory=list)
    internal_morphisms: list[str] = field(default_factory=list)
    external_morphisms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "coordinates": list(self.coordinates),
            "internal_morphisms": list(self.internal_morphisms),
            "external_morphisms": list(self.external_morphisms),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverMember:
        return cls(
            id=data["id"],
            name=data["name"],
            kind=CoverMemberKind(data["kind"]),
            coordinates=list(data.get("coordinates", [])),
            internal_morphisms=list(data.get("internal_morphisms", [])),
            external_morphisms=list(data.get("external_morphisms", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ArchitecturalOverlap:
    """Overlap between two cover members — the shared interface.

    In sheaf terms, this is the intersection of two open sets.  The
    width (number of shared coordinates) determines treaty negotiation
    cost.
    """

    member_a_id: str
    member_b_id: str
    shared_coordinates: list[str] = field(default_factory=list)
    interface_propositions: list[str] = field(default_factory=list)
    treaty_id: str | None = None
    width: int = 0

    def __post_init__(self) -> None:
        self.width = len(self.shared_coordinates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_a_id": self.member_a_id,
            "member_b_id": self.member_b_id,
            "shared_coordinates": list(self.shared_coordinates),
            "interface_propositions": list(self.interface_propositions),
            "treaty_id": self.treaty_id,
            "width": self.width,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitecturalOverlap:
        return cls(
            member_a_id=data["member_a_id"],
            member_b_id=data["member_b_id"],
            shared_coordinates=list(data.get("shared_coordinates", [])),
            interface_propositions=list(data.get("interface_propositions", [])),
            treaty_id=data.get("treaty_id"),
        )


@dataclass
class CoverQualityMetrics:
    """Quality metrics for an entire architectural cover.

    Aggregated scores measuring how well the cover decomposition
    supports efficient sheaf descent computation.
    """

    cover_id: str
    coupling_score: float = 0.0
    cohesion_score: float = 0.0
    avg_interface_width: float = 0.0
    max_interface_width: int = 0
    dependency_depth: int = 0
    circular_dependency_count: int = 0
    instability_scores: dict[str, float] = field(default_factory=dict)
    abstractness_scores: dict[str, float] = field(default_factory=dict)
    orphan_coordinate_count: int = 0
    total_members: int = 0
    total_overlaps: int = 0
    computed_at: str = ""

    def __post_init__(self) -> None:
        if not self.computed_at:
            self.computed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cover_id": self.cover_id,
            "coupling_score": self.coupling_score,
            "cohesion_score": self.cohesion_score,
            "avg_interface_width": self.avg_interface_width,
            "max_interface_width": self.max_interface_width,
            "dependency_depth": self.dependency_depth,
            "circular_dependency_count": self.circular_dependency_count,
            "instability_scores": dict(self.instability_scores),
            "abstractness_scores": dict(self.abstractness_scores),
            "orphan_coordinate_count": self.orphan_coordinate_count,
            "total_members": self.total_members,
            "total_overlaps": self.total_overlaps,
            "computed_at": self.computed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverQualityMetrics:
        return cls(
            cover_id=data["cover_id"],
            coupling_score=float(data.get("coupling_score", 0.0)),
            cohesion_score=float(data.get("cohesion_score", 0.0)),
            avg_interface_width=float(data.get("avg_interface_width", 0.0)),
            max_interface_width=int(data.get("max_interface_width", 0)),
            dependency_depth=int(data.get("dependency_depth", 0)),
            circular_dependency_count=int(data.get("circular_dependency_count", 0)),
            instability_scores=dict(data.get("instability_scores", {})),
            abstractness_scores=dict(data.get("abstractness_scores", {})),
            orphan_coordinate_count=int(data.get("orphan_coordinate_count", 0)),
            total_members=int(data.get("total_members", 0)),
            total_overlaps=int(data.get("total_overlaps", 0)),
            computed_at=data.get("computed_at", ""),
        )


@dataclass
class ArchitecturalDecision:
    """A suggested architectural refactoring decision.

    Produced by the cover analysis algorithms when the current cover
    structure has poor quality metrics.
    """

    id: str
    kind: ArchitecturalDecisionKind
    target_members: list[str] = field(default_factory=list)
    description: str = ""
    expected_coupling_change: float = 0.0
    expected_cohesion_change: float = 0.0
    blast_radius: int = 0
    confidence: float = 0.0
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "target_members": list(self.target_members),
            "description": self.description,
            "expected_coupling_change": self.expected_coupling_change,
            "expected_cohesion_change": self.expected_cohesion_change,
            "blast_radius": self.blast_radius,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitecturalDecision:
        return cls(
            id=data["id"],
            kind=ArchitecturalDecisionKind(data["kind"]),
            target_members=list(data.get("target_members", [])),
            description=data.get("description", ""),
            expected_coupling_change=float(data.get("expected_coupling_change", 0.0)),
            expected_cohesion_change=float(data.get("expected_cohesion_change", 0.0)),
            blast_radius=int(data.get("blast_radius", 0)),
            confidence=float(data.get("confidence", 0.0)),
            rationale=data.get("rationale", ""),
        )


@dataclass
class DeclaredBoundary:
    """A declared architectural boundary in the manifest.

    Specifies which coordinates belong to this boundary, what imports
    are allowed/disallowed, and trust requirements.
    """

    name: str
    coordinate_patterns: list[str] = field(default_factory=list)
    allowed_imports: list[str] = field(default_factory=list)
    disallowed_imports: list[str] = field(default_factory=list)
    trust_requirement: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "coordinate_patterns": list(self.coordinate_patterns),
            "allowed_imports": list(self.allowed_imports),
            "disallowed_imports": list(self.disallowed_imports),
            "trust_requirement": self.trust_requirement,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeclaredBoundary:
        return cls(
            name=data["name"],
            coordinate_patterns=list(data.get("coordinate_patterns", [])),
            allowed_imports=list(data.get("allowed_imports", [])),
            disallowed_imports=list(data.get("disallowed_imports", [])),
            trust_requirement=data.get("trust_requirement", "normal"),
        )


@dataclass
class ArchitecturalManifest:
    """Declared architectural structure — the desired cover design.

    The manifest declares what the architecture *should* look like.
    Enforcement checks compare the actual cover against this declaration.
    """

    id: str
    declared_covers: list[DeclaredBoundary] = field(default_factory=list)
    interface_contracts: list[str] = field(default_factory=list)
    boundary_rules: list[str] = field(default_factory=list)
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "declared_covers": [b.to_dict() for b in self.declared_covers],
            "interface_contracts": list(self.interface_contracts),
            "boundary_rules": list(self.boundary_rules),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitecturalManifest:
        return cls(
            id=data["id"],
            declared_covers=[
                DeclaredBoundary.from_dict(b)
                for b in data.get("declared_covers", [])
            ],
            interface_contracts=list(data.get("interface_contracts", [])),
            boundary_rules=list(data.get("boundary_rules", [])),
            version=data.get("version", "1.0"),
        )


@dataclass
class BoundaryViolation:
    """A violation of a declared architectural boundary.

    Detected when actual code structure doesn't match the manifest.
    """

    boundary_name: str
    violating_coordinate: str
    violation_kind: str  # UNDECLARED_IMPORT, INTERFACE_VIOLATION, TRUST_INSUFFICIENT
    details: str = ""
    severity: str = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_name": self.boundary_name,
            "violating_coordinate": self.violating_coordinate,
            "violation_kind": self.violation_kind,
            "details": self.details,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundaryViolation:
        return cls(
            boundary_name=data["boundary_name"],
            violating_coordinate=data["violating_coordinate"],
            violation_kind=data["violation_kind"],
            details=data.get("details", ""),
            severity=data.get("severity", "warning"),
        )


@dataclass
class ArchitecturalSnapshot:
    """A point-in-time snapshot of architectural quality.

    Used by ArchitectureTracker to monitor evolution.
    """

    id: str
    timestamp: str = ""
    cover_quality: CoverQualityMetrics | None = None
    member_count: int = 0
    overlap_count: int = 0
    violation_count: int = 0
    drift_from_manifest: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "cover_quality": self.cover_quality.to_dict() if self.cover_quality else None,
            "member_count": self.member_count,
            "overlap_count": self.overlap_count,
            "violation_count": self.violation_count,
            "drift_from_manifest": self.drift_from_manifest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitecturalSnapshot:
        cq_data = data.get("cover_quality")
        return cls(
            id=data["id"],
            timestamp=data.get("timestamp", ""),
            cover_quality=CoverQualityMetrics.from_dict(cq_data) if cq_data else None,
            member_count=int(data.get("member_count", 0)),
            overlap_count=int(data.get("overlap_count", 0)),
            violation_count=int(data.get("violation_count", 0)),
            drift_from_manifest=float(data.get("drift_from_manifest", 0.0)),
        )


@dataclass
class ArchitecturalDrift:
    """Drift between two architectural snapshots.

    Quantifies how much the architecture has changed and whether
    the change is degradation or improvement.
    """

    baseline_snapshot_id: str
    current_snapshot_id: str
    coupling_delta: float = 0.0
    cohesion_delta: float = 0.0
    new_violations: list[BoundaryViolation] = field(default_factory=list)
    resolved_violations: list[BoundaryViolation] = field(default_factory=list)
    drift_score: float = 0.0
    needs_attention: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "current_snapshot_id": self.current_snapshot_id,
            "coupling_delta": self.coupling_delta,
            "cohesion_delta": self.cohesion_delta,
            "new_violations": [v.to_dict() for v in self.new_violations],
            "resolved_violations": [v.to_dict() for v in self.resolved_violations],
            "drift_score": self.drift_score,
            "needs_attention": self.needs_attention,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitecturalDrift:
        return cls(
            baseline_snapshot_id=data["baseline_snapshot_id"],
            current_snapshot_id=data["current_snapshot_id"],
            coupling_delta=float(data.get("coupling_delta", 0.0)),
            cohesion_delta=float(data.get("cohesion_delta", 0.0)),
            new_violations=[
                BoundaryViolation.from_dict(v)
                for v in data.get("new_violations", [])
            ],
            resolved_violations=[
                BoundaryViolation.from_dict(v)
                for v in data.get("resolved_violations", [])
            ],
            drift_score=float(data.get("drift_score", 0.0)),
            needs_attention=bool(data.get("needs_attention", False)),
        )
