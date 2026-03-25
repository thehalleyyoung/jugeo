"""Debugging models: obstructions, repair frontiers, countermodels, triage reports.

In JG, a bug is an obstruction at a coordinate where the local section fails.
Debugging is localizing descent failures, extracting countermodels, and
computing repair frontiers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "obj") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CohomologyClass(str, Enum):
    """Topological class of the obstruction — the 'type' of descent failure."""
    TYPE_ERROR = "type_error"
    LOGIC_ERROR = "logic_error"
    PROTOCOL_VIOLATION = "protocol_violation"
    RESOURCE_LEAK = "resource_leak"
    CONCURRENCY_HAZARD = "concurrency_hazard"
    NULL_REFERENCE = "null_reference"
    BOUNDS_VIOLATION = "bounds_violation"
    ENCODING_MISMATCH = "encoding_mismatch"
    CONTRACT_VIOLATION = "contract_violation"
    ASSERTION_FAILURE = "assertion_failure"
    IMPORT_ERROR = "import_error"
    PERMISSION_ERROR = "permission_error"
    STATE_CORRUPTION = "state_corruption"
    DEADLOCK = "deadlock"
    RACE_CONDITION = "race_condition"
    MEMORY_LEAK = "memory_leak"
    API_MISUSE = "api_misuse"
    CONFIGURATION_ERROR = "configuration_error"
    UNKNOWN = "unknown"


class ObstructionSeverity(str, Enum):
    """How severe the obstruction is in the sheaf-theoretic sense."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    BLOCKER = "blocker"

    def __lt__(self, other: ObstructionSeverity) -> bool:
        _order = [
            ObstructionSeverity.INFO,
            ObstructionSeverity.WARNING,
            ObstructionSeverity.ERROR,
            ObstructionSeverity.CRITICAL,
            ObstructionSeverity.BLOCKER,
        ]
        return _order.index(self) < _order.index(other)

    def __le__(self, other: ObstructionSeverity) -> bool:
        return self == other or self < other

    def __gt__(self, other: ObstructionSeverity) -> bool:
        return not self <= other

    def __ge__(self, other: ObstructionSeverity) -> bool:
        return not self < other

    @property
    def numeric_weight(self) -> int:
        """Numeric weight for scoring (INFO=1 … BLOCKER=5)."""
        return {
            ObstructionSeverity.INFO: 1,
            ObstructionSeverity.WARNING: 2,
            ObstructionSeverity.ERROR: 3,
            ObstructionSeverity.CRITICAL: 4,
            ObstructionSeverity.BLOCKER: 5,
        }[self]


class RepairStrategy(str, Enum):
    """Strategy for repairing the obstruction."""
    LOCAL_FIX = "local_fix"
    PROPAGATED_FIX = "propagated_fix"
    INTERFACE_RENEGOTIATION = "interface_renegotiation"
    COVER_REFINEMENT = "cover_refinement"
    TRUST_DEMOTION = "trust_demotion"
    EVIDENCE_REFRESH = "evidence_refresh"
    MANUAL_REVIEW = "manual_review"


# ---------------------------------------------------------------------------
# Core Models
# ---------------------------------------------------------------------------

@dataclass
class Obstruction:
    """An obstruction at a coordinate where the local section fails.

    Corresponds to a non-trivial cohomology class: a local section that
    cannot be extended to a global section over the cover.
    """
    id: str
    coordinate_id: str
    proposition: str
    cohomology_class: CohomologyClass
    severity: ObstructionSeverity
    countermodel: dict[str, Any] | None = None
    repair_frontier: list[str] = field(default_factory=list)
    blast_radius: int = 0
    downstream_ids: list[str] = field(default_factory=list)
    overlap_id: str | None = None
    morphism_chain: list[str] | None = None
    created_at: str = field(default_factory=_now_iso)
    resolved_at: str | None = None
    resolution_note: str | None = None

    @classmethod
    def make(
        cls,
        coordinate_id: str,
        proposition: str,
        cohomology_class: CohomologyClass,
        severity: ObstructionSeverity,
        **kwargs: Any,
    ) -> Obstruction:
        return cls(
            id=_new_id("obs"),
            coordinate_id=coordinate_id,
            proposition=proposition,
            cohomology_class=cohomology_class,
            severity=severity,
            created_at=_now_iso(),
            **kwargs,
        )

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def is_active(self) -> bool:
        return not self.is_resolved

    def resolve(self, note: str) -> Obstruction:
        self.resolved_at = _now_iso()
        self.resolution_note = note
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "cohomology_class": self.cohomology_class.value,
            "severity": self.severity.value,
            "countermodel": self.countermodel,
            "repair_frontier": list(self.repair_frontier),
            "blast_radius": self.blast_radius,
            "downstream_ids": list(self.downstream_ids),
            "overlap_id": self.overlap_id,
            "morphism_chain": list(self.morphism_chain) if self.morphism_chain else None,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution_note": self.resolution_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Obstruction:
        return cls(
            id=str(data["id"]),
            coordinate_id=str(data["coordinate_id"]),
            proposition=str(data["proposition"]),
            cohomology_class=CohomologyClass(data["cohomology_class"]),
            severity=ObstructionSeverity(data["severity"]),
            countermodel=data.get("countermodel"),
            repair_frontier=list(data.get("repair_frontier") or []),
            blast_radius=int(data.get("blast_radius", 0)),
            downstream_ids=list(data.get("downstream_ids") or []),
            overlap_id=data.get("overlap_id"),
            morphism_chain=list(data["morphism_chain"]) if data.get("morphism_chain") else None,
            created_at=str(data.get("created_at", _now_iso())),
            resolved_at=data.get("resolved_at"),
            resolution_note=data.get("resolution_note"),
        )


@dataclass
class ObstructionCluster:
    """A cluster of related obstructions sharing cohomology class and coordinate pattern.

    Clustering reduces human review load by exposing common root causes.
    """
    cluster_id: str
    cohomology_class: CohomologyClass
    coordinate_pattern: str
    obstructions: list[str] = field(default_factory=list)
    count: int = 0
    common_root_cause: str | None = None
    suggested_batch_fix: str | None = None

    @classmethod
    def make(
        cls,
        cohomology_class: CohomologyClass,
        coordinate_pattern: str,
        obstructions: list[str],
        common_root_cause: str | None = None,
        suggested_batch_fix: str | None = None,
    ) -> ObstructionCluster:
        return cls(
            cluster_id=_new_id("clust"),
            cohomology_class=cohomology_class,
            coordinate_pattern=coordinate_pattern,
            obstructions=list(obstructions),
            count=len(obstructions),
            common_root_cause=common_root_cause,
            suggested_batch_fix=suggested_batch_fix,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "cohomology_class": self.cohomology_class.value,
            "coordinate_pattern": self.coordinate_pattern,
            "obstructions": list(self.obstructions),
            "count": self.count,
            "common_root_cause": self.common_root_cause,
            "suggested_batch_fix": self.suggested_batch_fix,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObstructionCluster:
        return cls(
            cluster_id=str(data["cluster_id"]),
            cohomology_class=CohomologyClass(data["cohomology_class"]),
            coordinate_pattern=str(data["coordinate_pattern"]),
            obstructions=list(data.get("obstructions") or []),
            count=int(data.get("count", 0)),
            common_root_cause=data.get("common_root_cause"),
            suggested_batch_fix=data.get("suggested_batch_fix"),
        )


@dataclass
class DescentTrace:
    """Trace of a descent attempt through the morphism chain.

    Records the path taken and where descent first fails.
    """
    start_coordinate: str
    end_coordinate: str
    morphism_chain: list[tuple[str, str, str]]
    failure_point: str | None = None
    trace_depth: int = 0

    @classmethod
    def make(
        cls,
        start_coordinate: str,
        end_coordinate: str,
        morphism_chain: list[tuple[str, str, str]],
        failure_point: str | None = None,
    ) -> DescentTrace:
        return cls(
            start_coordinate=start_coordinate,
            end_coordinate=end_coordinate,
            morphism_chain=morphism_chain,
            failure_point=failure_point,
            trace_depth=len(morphism_chain),
        )

    @property
    def failed(self) -> bool:
        return self.failure_point is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_coordinate": self.start_coordinate,
            "end_coordinate": self.end_coordinate,
            "morphism_chain": [list(m) for m in self.morphism_chain],
            "failure_point": self.failure_point,
            "trace_depth": self.trace_depth,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DescentTrace:
        return cls(
            start_coordinate=str(data["start_coordinate"]),
            end_coordinate=str(data["end_coordinate"]),
            morphism_chain=[
                (str(m[0]), str(m[1]), str(m[2]))
                for m in (data.get("morphism_chain") or [])
            ],
            failure_point=data.get("failure_point"),
            trace_depth=int(data.get("trace_depth", 0)),
        )


@dataclass
class RootCauseAnalysis:
    """Root cause analysis for a symptom obstruction.

    Traces the causal chain from the earliest failing coordinate
    to the observed symptom.
    """
    symptom_id: str
    root_coordinate_id: str
    root_proposition: str
    causal_chain: list[str] = field(default_factory=list)
    confidence: float = 1.0
    alternative_roots: list[str] = field(default_factory=list)

    @classmethod
    def make(
        cls,
        symptom_id: str,
        root_coordinate_id: str,
        root_proposition: str,
        causal_chain: list[str],
        confidence: float = 1.0,
        alternative_roots: list[str] | None = None,
    ) -> RootCauseAnalysis:
        return cls(
            symptom_id=symptom_id,
            root_coordinate_id=root_coordinate_id,
            root_proposition=root_proposition,
            causal_chain=list(causal_chain),
            confidence=confidence,
            alternative_roots=list(alternative_roots or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symptom_id": self.symptom_id,
            "root_coordinate_id": self.root_coordinate_id,
            "root_proposition": self.root_proposition,
            "causal_chain": list(self.causal_chain),
            "confidence": self.confidence,
            "alternative_roots": list(self.alternative_roots),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RootCauseAnalysis:
        return cls(
            symptom_id=str(data["symptom_id"]),
            root_coordinate_id=str(data["root_coordinate_id"]),
            root_proposition=str(data["root_proposition"]),
            causal_chain=list(data.get("causal_chain") or []),
            confidence=float(data.get("confidence", 1.0)),
            alternative_roots=list(data.get("alternative_roots") or []),
        )


@dataclass
class RepairFrontier:
    """Minimal set of coordinates to modify to resolve an obstruction.

    Corresponds to a minimum vertex cut in the morphism graph.
    """
    obstruction_id: str
    minimal_coordinates: list[str] = field(default_factory=list)
    estimated_effort: float = 1.0
    strategy: RepairStrategy = RepairStrategy.LOCAL_FIX
    prerequisites: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)

    @classmethod
    def make(
        cls,
        obstruction_id: str,
        minimal_coordinates: list[str],
        estimated_effort: float = 1.0,
        strategy: RepairStrategy = RepairStrategy.LOCAL_FIX,
        prerequisites: list[str] | None = None,
        side_effects: list[str] | None = None,
    ) -> RepairFrontier:
        return cls(
            obstruction_id=obstruction_id,
            minimal_coordinates=list(minimal_coordinates),
            estimated_effort=estimated_effort,
            strategy=strategy,
            prerequisites=list(prerequisites or []),
            side_effects=list(side_effects or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "minimal_coordinates": list(self.minimal_coordinates),
            "estimated_effort": self.estimated_effort,
            "strategy": self.strategy.value,
            "prerequisites": list(self.prerequisites),
            "side_effects": list(self.side_effects),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepairFrontier:
        return cls(
            obstruction_id=str(data["obstruction_id"]),
            minimal_coordinates=list(data.get("minimal_coordinates") or []),
            estimated_effort=float(data.get("estimated_effort", 1.0)),
            strategy=RepairStrategy(data.get("strategy", RepairStrategy.LOCAL_FIX.value)),
            prerequisites=list(data.get("prerequisites") or []),
            side_effects=list(data.get("side_effects") or []),
        )


@dataclass
class RepairPlan:
    """Topologically ordered repair plan for a set of obstructions."""
    id: str
    obstructions: list[str] = field(default_factory=list)
    ordered_repairs: list[RepairFrontier] = field(default_factory=list)
    total_estimated_effort: float = 0.0
    blast_radius: int = 0
    strategy_summary: str = ""

    @classmethod
    def make(
        cls,
        obstructions: list[str],
        ordered_repairs: list[RepairFrontier],
        blast_radius: int = 0,
        strategy_summary: str = "",
    ) -> RepairPlan:
        total_effort = sum(r.estimated_effort for r in ordered_repairs)
        return cls(
            id=_new_id("plan"),
            obstructions=list(obstructions),
            ordered_repairs=list(ordered_repairs),
            total_estimated_effort=total_effort,
            blast_radius=blast_radius,
            strategy_summary=strategy_summary,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "obstructions": list(self.obstructions),
            "ordered_repairs": [r.to_dict() for r in self.ordered_repairs],
            "total_estimated_effort": self.total_estimated_effort,
            "blast_radius": self.blast_radius,
            "strategy_summary": self.strategy_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepairPlan:
        return cls(
            id=str(data["id"]),
            obstructions=list(data.get("obstructions") or []),
            ordered_repairs=[
                RepairFrontier.from_dict(r)
                for r in (data.get("ordered_repairs") or [])
            ],
            total_estimated_effort=float(data.get("total_estimated_effort", 0.0)),
            blast_radius=int(data.get("blast_radius", 0)),
            strategy_summary=str(data.get("strategy_summary", "")),
        )


@dataclass
class CountermodelReport:
    """Concrete witness to a descent failure — the countermodel.

    A countermodel is a specific set of inputs that demonstrates
    the failure, making it a concrete test obligation.
    """
    obstruction_id: str
    coordinate_id: str
    proposition: str
    concrete_inputs: dict[str, Any] = field(default_factory=dict)
    expected_output: Any = None
    actual_output: Any = None
    reproducible: bool = True
    convertible_to_test: bool = True
    suggested_test: str | None = None

    @classmethod
    def make(
        cls,
        obstruction_id: str,
        coordinate_id: str,
        proposition: str,
        concrete_inputs: dict[str, Any],
        expected_output: Any = None,
        actual_output: Any = None,
        reproducible: bool = True,
        suggested_test: str | None = None,
    ) -> CountermodelReport:
        convertible = reproducible and bool(concrete_inputs)
        return cls(
            obstruction_id=obstruction_id,
            coordinate_id=coordinate_id,
            proposition=proposition,
            concrete_inputs=dict(concrete_inputs),
            expected_output=expected_output,
            actual_output=actual_output,
            reproducible=reproducible,
            convertible_to_test=convertible,
            suggested_test=suggested_test,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "concrete_inputs": dict(self.concrete_inputs),
            "expected_output": self.expected_output,
            "actual_output": self.actual_output,
            "reproducible": self.reproducible,
            "convertible_to_test": self.convertible_to_test,
            "suggested_test": self.suggested_test,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CountermodelReport:
        return cls(
            obstruction_id=str(data["obstruction_id"]),
            coordinate_id=str(data["coordinate_id"]),
            proposition=str(data["proposition"]),
            concrete_inputs=dict(data.get("concrete_inputs") or {}),
            expected_output=data.get("expected_output"),
            actual_output=data.get("actual_output"),
            reproducible=bool(data.get("reproducible", True)),
            convertible_to_test=bool(data.get("convertible_to_test", True)),
            suggested_test=data.get("suggested_test"),
        )


@dataclass
class TriageReport:
    """Full triage report for a set of obstructions.

    Aggregates obstruction data into actionable summary for engineering teams.
    """
    total_obstructions: int
    by_class: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)
    clusters: list[ObstructionCluster] = field(default_factory=list)
    top_blast_radius: list[Obstruction] = field(default_factory=list)
    estimated_total_effort: float = 0.0
    auto_fixable_count: int = 0
    needs_manual_count: int = 0
    computed_at: str = field(default_factory=_now_iso)

    @classmethod
    def make(
        cls,
        obstructions: list[Obstruction],
        clusters: list[ObstructionCluster],
        estimated_total_effort: float,
        auto_fixable_count: int,
        needs_manual_count: int,
    ) -> TriageReport:
        by_class: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for obs in obstructions:
            by_class[obs.cohomology_class.value] = by_class.get(obs.cohomology_class.value, 0) + 1
            by_severity[obs.severity.value] = by_severity.get(obs.severity.value, 0) + 1

        top_blast = sorted(obstructions, key=lambda o: o.blast_radius, reverse=True)[:10]

        return cls(
            total_obstructions=len(obstructions),
            by_class=by_class,
            by_severity=by_severity,
            clusters=list(clusters),
            top_blast_radius=top_blast,
            estimated_total_effort=estimated_total_effort,
            auto_fixable_count=auto_fixable_count,
            needs_manual_count=needs_manual_count,
            computed_at=_now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_obstructions": self.total_obstructions,
            "by_class": dict(self.by_class),
            "by_severity": dict(self.by_severity),
            "clusters": [c.to_dict() for c in self.clusters],
            "top_blast_radius": [o.to_dict() for o in self.top_blast_radius],
            "estimated_total_effort": self.estimated_total_effort,
            "auto_fixable_count": self.auto_fixable_count,
            "needs_manual_count": self.needs_manual_count,
            "computed_at": self.computed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TriageReport:
        return cls(
            total_obstructions=int(data["total_obstructions"]),
            by_class=dict(data.get("by_class") or {}),
            by_severity=dict(data.get("by_severity") or {}),
            clusters=[
                ObstructionCluster.from_dict(c)
                for c in (data.get("clusters") or [])
            ],
            top_blast_radius=[
                Obstruction.from_dict(o)
                for o in (data.get("top_blast_radius") or [])
            ],
            estimated_total_effort=float(data.get("estimated_total_effort", 0.0)),
            auto_fixable_count=int(data.get("auto_fixable_count", 0)),
            needs_manual_count=int(data.get("needs_manual_count", 0)),
            computed_at=str(data.get("computed_at", _now_iso())),
        )


# ---------------------------------------------------------------------------
# Section and morphism primitives (used by algorithms — standalone)
# ---------------------------------------------------------------------------

@dataclass
class LocalSection:
    """A local section over a coordinate — the proposed value at a site node."""
    coordinate_id: str
    proposition: str
    value: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    is_valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "value": self.value,
            "metadata": dict(self.metadata),
            "is_valid": self.is_valid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalSection:
        return cls(
            coordinate_id=str(data["coordinate_id"]),
            proposition=str(data["proposition"]),
            value=data.get("value"),
            metadata=dict(data.get("metadata") or {}),
            is_valid=bool(data.get("is_valid", True)),
        )


@dataclass
class Morphism:
    """A morphism between coordinates in the site — a directed dependency."""
    source: str
    target: str
    kind: str = "dependency"
    weight: float = 1.0
    is_critical_path: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "weight": self.weight,
            "is_critical_path": self.is_critical_path,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Morphism:
        return cls(
            source=str(data["source"]),
            target=str(data["target"]),
            kind=str(data.get("kind", "dependency")),
            weight=float(data.get("weight", 1.0)),
            is_critical_path=bool(data.get("is_critical_path", False)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Overlap:
    """An overlap between two coordinates — where sections must agree."""
    overlap_id: str
    coordinate_a: str
    coordinate_b: str
    shared_coordinates: list[str] = field(default_factory=list)
    agreement_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "overlap_id": self.overlap_id,
            "coordinate_a": self.coordinate_a,
            "coordinate_b": self.coordinate_b,
            "shared_coordinates": list(self.shared_coordinates),
            "agreement_required": self.agreement_required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Overlap:
        return cls(
            overlap_id=str(data["overlap_id"]),
            coordinate_a=str(data["coordinate_a"]),
            coordinate_b=str(data["coordinate_b"]),
            shared_coordinates=list(data.get("shared_coordinates") or []),
            agreement_required=bool(data.get("agreement_required", True)),
        )
