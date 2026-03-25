"""
Data models for the large-scale co-evolution orchestration engine.

All models follow the JuGeo conventions:
- ``@dataclass(slots=True)`` for mutable records
- ``@dataclass(frozen=True, slots=True)`` for value objects
- ``(str, Enum)`` for string enums
- ``to_dict()`` / ``from_dict()`` for serialisation
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


__all__ = [
    # Co-evolution site
    "Surface", "SurfaceState", "DriftEdge", "CoEvolutionState",
    # Obligation presheaf
    "ObligationKind", "TypedObligation", "ObligationPresheaf", "SupportAwareDecay",
    # Hierarchical controllers
    "ControllerLevel", "ControllerState", "LocalController",
    "RegionalController", "GlobalController",
    # Phase detection
    "Phase", "PhaseSignal", "PhaseTransition",
    # Fleet competition
    "Strategy", "Bid", "FleetResult",
    # Convergence
    "ConvergenceCriterion", "ConvergenceCertificate",
    # Moves
    "MoveCategory", "SemanticMove", "MoveResult", "MoveHistory",
    # Budget
    "BudgetAllocation", "BudgetUsage",
]


# ---------------------------------------------------------------------------
# 1. Co-evolution Site Models
# ---------------------------------------------------------------------------

class Surface(str, Enum):
    """A surface in the co-evolution site."""

    SPECIFICATION = "specification"
    CODE = "code"
    EVIDENCE = "evidence"
    CLAIMS = "claims"
    BENCHMARKS = "benchmarks"
    DEPLOYMENT = "deployment"
    DOCUMENTATION = "documentation"
    USER_FEEDBACK = "user_feedback"


@dataclass(slots=True)
class SurfaceState:
    """Snapshot of a single surface in the co-evolution site."""

    surface: Surface
    coordinate_ids: list[str] = field(default_factory=list)
    version: int = 0
    last_modified_at: float = field(default_factory=time.time)
    trust_floor: str = "conjecture"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface.value,
            "coordinate_ids": list(self.coordinate_ids),
            "version": self.version,
            "last_modified_at": self.last_modified_at,
            "trust_floor": self.trust_floor,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SurfaceState:
        return cls(
            surface=Surface(d["surface"]),
            coordinate_ids=list(d.get("coordinate_ids", [])),
            version=d.get("version", 0),
            last_modified_at=d.get("last_modified_at", 0.0),
            trust_floor=d.get("trust_floor", "conjecture"),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(slots=True)
class DriftEdge:
    """An edge connecting two surfaces with measured drift."""

    surface_a: Surface
    surface_b: Surface
    overlap_coordinates: list[str] = field(default_factory=list)
    drift_score: float = 0.0
    last_checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_a": self.surface_a.value,
            "surface_b": self.surface_b.value,
            "overlap_coordinates": list(self.overlap_coordinates),
            "drift_score": self.drift_score,
            "last_checked_at": self.last_checked_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DriftEdge:
        return cls(
            surface_a=Surface(d["surface_a"]),
            surface_b=Surface(d["surface_b"]),
            overlap_coordinates=list(d.get("overlap_coordinates", [])),
            drift_score=d.get("drift_score", 0.0),
            last_checked_at=d.get("last_checked_at", 0.0),
        )


@dataclass(slots=True)
class CoEvolutionState:
    """Full snapshot of the co-evolution site."""

    surfaces: dict[str, SurfaceState] = field(default_factory=dict)
    drift_edges: list[DriftEdge] = field(default_factory=list)
    overall_drift_score: float = 0.0
    is_synchronized: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "surfaces": {k: v.to_dict() for k, v in self.surfaces.items()},
            "drift_edges": [e.to_dict() for e in self.drift_edges],
            "overall_drift_score": self.overall_drift_score,
            "is_synchronized": self.is_synchronized,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoEvolutionState:
        return cls(
            surfaces={
                k: SurfaceState.from_dict(v)
                for k, v in d.get("surfaces", {}).items()
            },
            drift_edges=[
                DriftEdge.from_dict(e) for e in d.get("drift_edges", [])
            ],
            overall_drift_score=d.get("overall_drift_score", 0.0),
            is_synchronized=d.get("is_synchronized", True),
        )


# ---------------------------------------------------------------------------
# 2. Obligation Presheaf Models
# ---------------------------------------------------------------------------

class ObligationKind(str, Enum):
    """Typed obligation categories."""

    VERIFICATION = "verification"
    GROUNDING = "grounding"
    AUDIT = "audit"
    DOCUMENTATION = "documentation"
    BENCHMARK = "benchmark"
    TESTING = "testing"
    REVIEW = "review"
    DEPLOYMENT = "deployment"
    CLEANUP = "cleanup"
    CUSTOM = "custom"


@dataclass(slots=True)
class TypedObligation:
    """A single typed obligation in the presheaf."""

    id: str
    kind: ObligationKind
    coordinate_id: str
    proposition: str
    trust_target: str = "conjecture"
    priority: float = 1.0
    status: str = "PENDING"
    support_coordinates: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    deadline: Optional[float] = None
    assigned_to: Optional[str] = None
    discharge_evidence_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "trust_target": self.trust_target,
            "priority": self.priority,
            "status": self.status,
            "support_coordinates": list(self.support_coordinates),
            "created_at": self.created_at,
            "deadline": self.deadline,
            "assigned_to": self.assigned_to,
            "discharge_evidence_id": self.discharge_evidence_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TypedObligation:
        return cls(
            id=d["id"],
            kind=ObligationKind(d["kind"]),
            coordinate_id=d["coordinate_id"],
            proposition=d["proposition"],
            trust_target=d.get("trust_target", "conjecture"),
            priority=d.get("priority", 1.0),
            status=d.get("status", "PENDING"),
            support_coordinates=list(d.get("support_coordinates", [])),
            created_at=d.get("created_at", 0.0),
            deadline=d.get("deadline"),
            assigned_to=d.get("assigned_to"),
            discharge_evidence_id=d.get("discharge_evidence_id"),
        )


@dataclass(slots=True)
class ObligationPresheaf:
    """Snapshot of the full obligation presheaf."""

    obligations: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_kind: dict[str, list[str]] = field(default_factory=dict)
    by_coordinate: dict[str, list[str]] = field(default_factory=dict)
    total_pressure: float = 0.0
    pressure_by_kind: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligations": dict(self.obligations),
            "by_kind": {k: list(v) for k, v in self.by_kind.items()},
            "by_coordinate": {k: list(v) for k, v in self.by_coordinate.items()},
            "total_pressure": self.total_pressure,
            "pressure_by_kind": dict(self.pressure_by_kind),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObligationPresheaf:
        return cls(
            obligations=dict(d.get("obligations", {})),
            by_kind={k: list(v) for k, v in d.get("by_kind", {}).items()},
            by_coordinate={
                k: list(v) for k, v in d.get("by_coordinate", {}).items()
            },
            total_pressure=d.get("total_pressure", 0.0),
            pressure_by_kind=dict(d.get("pressure_by_kind", {})),
        )


@dataclass(slots=True)
class SupportAwareDecay:
    """Staleness detection based on code-change timestamps, not magic constants."""

    coordinate_id: str
    last_code_change_at: float = 0.0
    evidence_timestamp: float = 0.0
    is_stale: bool = False
    staleness_days: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate_id": self.coordinate_id,
            "last_code_change_at": self.last_code_change_at,
            "evidence_timestamp": self.evidence_timestamp,
            "is_stale": self.is_stale,
            "staleness_days": self.staleness_days,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SupportAwareDecay:
        return cls(
            coordinate_id=d["coordinate_id"],
            last_code_change_at=d.get("last_code_change_at", 0.0),
            evidence_timestamp=d.get("evidence_timestamp", 0.0),
            is_stale=d.get("is_stale", False),
            staleness_days=d.get("staleness_days", 0.0),
        )


# ---------------------------------------------------------------------------
# 3. Hierarchical Controller Models
# ---------------------------------------------------------------------------

class ControllerLevel(str, Enum):
    """Hierarchy level for a controller."""

    LOCAL = "local"
    REGIONAL = "regional"
    GLOBAL = "global"


@dataclass(slots=True)
class ControllerState:
    """Observable state of a single controller at any level."""

    level: ControllerLevel
    scope: str = ""
    phase: str = "exploration"
    frontier_size: int = 0
    obligation_count: int = 0
    budget_remaining: float = 1.0
    active_moves: int = 0
    convergence_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "scope": self.scope,
            "phase": self.phase,
            "frontier_size": self.frontier_size,
            "obligation_count": self.obligation_count,
            "budget_remaining": self.budget_remaining,
            "active_moves": self.active_moves,
            "convergence_score": self.convergence_score,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ControllerState:
        return cls(
            level=ControllerLevel(d["level"]),
            scope=d.get("scope", ""),
            phase=d.get("phase", "exploration"),
            frontier_size=d.get("frontier_size", 0),
            obligation_count=d.get("obligation_count", 0),
            budget_remaining=d.get("budget_remaining", 1.0),
            active_moves=d.get("active_moves", 0),
            convergence_score=d.get("convergence_score", 0.0),
        )


@dataclass(slots=True)
class LocalController:
    """A controller handling a single package / module scope."""

    id: str
    scope: str
    state: ControllerState = field(
        default_factory=lambda: ControllerState(level=ControllerLevel.LOCAL)
    )
    coordinates: list[str] = field(default_factory=list)
    morphisms: list[str] = field(default_factory=list)
    frontier: list[dict[str, Any]] = field(default_factory=list)
    obligations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "state": self.state.to_dict(),
            "coordinates": list(self.coordinates),
            "morphisms": list(self.morphisms),
            "frontier": [dict(f) for f in self.frontier],
            "obligations": list(self.obligations),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LocalController:
        return cls(
            id=d["id"],
            scope=d["scope"],
            state=ControllerState.from_dict(d.get("state", {"level": "local"})),
            coordinates=list(d.get("coordinates", [])),
            morphisms=list(d.get("morphisms", [])),
            frontier=[dict(f) for f in d.get("frontier", [])],
            obligations=list(d.get("obligations", [])),
        )


@dataclass(slots=True)
class RegionalController:
    """A controller overseeing a cluster of local controllers."""

    id: str
    scope: str
    state: ControllerState = field(
        default_factory=lambda: ControllerState(level=ControllerLevel.REGIONAL)
    )
    local_controllers: list[str] = field(default_factory=list)
    cross_package_edges: list[dict[str, Any]] = field(default_factory=list)
    treaties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "state": self.state.to_dict(),
            "local_controllers": list(self.local_controllers),
            "cross_package_edges": [dict(e) for e in self.cross_package_edges],
            "treaties": list(self.treaties),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegionalController:
        return cls(
            id=d["id"],
            scope=d["scope"],
            state=ControllerState.from_dict(
                d.get("state", {"level": "regional"})
            ),
            local_controllers=list(d.get("local_controllers", [])),
            cross_package_edges=[
                dict(e) for e in d.get("cross_package_edges", [])
            ],
            treaties=list(d.get("treaties", [])),
        )


@dataclass(slots=True)
class GlobalController:
    """The single top-level controller for the entire project."""

    id: str
    state: ControllerState = field(
        default_factory=lambda: ControllerState(level=ControllerLevel.GLOBAL)
    )
    regional_controllers: list[str] = field(default_factory=list)
    global_budget: float = 1.0
    global_phase: str = "exploration"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.to_dict(),
            "regional_controllers": list(self.regional_controllers),
            "global_budget": self.global_budget,
            "global_phase": self.global_phase,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GlobalController:
        return cls(
            id=d["id"],
            state=ControllerState.from_dict(
                d.get("state", {"level": "global"})
            ),
            regional_controllers=list(d.get("regional_controllers", [])),
            global_budget=d.get("global_budget", 1.0),
            global_phase=d.get("global_phase", "exploration"),
        )


# ---------------------------------------------------------------------------
# 4. Phase Detection Models
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    """Automatically detected orchestration phase."""

    EXPLORATION = "exploration"
    EXPLOITATION = "exploitation"
    RECOVERY = "recovery"
    HARDENING = "hardening"
    TAIL = "tail"
    CONVERGED = "converged"


@dataclass(slots=True)
class PhaseSignal:
    """A single signal used for phase detection."""

    signal_name: str
    value: float = 0.0
    threshold: float = 0.0
    triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_name": self.signal_name,
            "value": self.value,
            "threshold": self.threshold,
            "triggered": self.triggered,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PhaseSignal:
        return cls(
            signal_name=d["signal_name"],
            value=d.get("value", 0.0),
            threshold=d.get("threshold", 0.0),
            triggered=d.get("triggered", False),
        )


@dataclass(slots=True)
class PhaseTransition:
    """Recorded transition between two phases."""

    from_phase: Phase
    to_phase: Phase
    signals: list[PhaseSignal] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_phase": self.from_phase.value,
            "to_phase": self.to_phase.value,
            "signals": [s.to_dict() for s in self.signals],
            "timestamp": self.timestamp,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PhaseTransition:
        return cls(
            from_phase=Phase(d["from_phase"]),
            to_phase=Phase(d["to_phase"]),
            signals=[PhaseSignal.from_dict(s) for s in d.get("signals", [])],
            timestamp=d.get("timestamp", 0.0),
            reason=d.get("reason", ""),
        )


# ---------------------------------------------------------------------------
# 5. Fleet Competition Models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Strategy:
    """A named strategy that generates and scores moves."""

    id: str
    name: str
    move_generator: str = "default"
    scoring_weights: dict[str, float] = field(default_factory=dict)
    domain: str = "general"
    budget_fraction: float = 0.2

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "move_generator": self.move_generator,
            "scoring_weights": dict(self.scoring_weights),
            "domain": self.domain,
            "budget_fraction": self.budget_fraction,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Strategy:
        return cls(
            id=d["id"],
            name=d["name"],
            move_generator=d.get("move_generator", "default"),
            scoring_weights=dict(d.get("scoring_weights", {})),
            domain=d.get("domain", "general"),
            budget_fraction=d.get("budget_fraction", 0.2),
        )


@dataclass(slots=True)
class Bid:
    """A bid from a strategy for a particular move."""

    strategy_id: str
    move_id: str
    score: float = 0.0
    confidence: float = 1.0
    estimated_cost: float = 0.0
    surface_targets: list[Surface] = field(default_factory=list)
    expected_drift_reduction: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "move_id": self.move_id,
            "score": self.score,
            "confidence": self.confidence,
            "estimated_cost": self.estimated_cost,
            "surface_targets": [s.value for s in self.surface_targets],
            "expected_drift_reduction": self.expected_drift_reduction,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Bid:
        return cls(
            strategy_id=d["strategy_id"],
            move_id=d["move_id"],
            score=d.get("score", 0.0),
            confidence=d.get("confidence", 1.0),
            estimated_cost=d.get("estimated_cost", 0.0),
            surface_targets=[
                Surface(s) for s in d.get("surface_targets", [])
            ],
            expected_drift_reduction=d.get("expected_drift_reduction", 0.0),
        )


@dataclass(slots=True)
class FleetResult:
    """Result of fleet competition for a single round."""

    winning_bid: Bid
    runner_up_bids: list[Bid] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "winning_bid": self.winning_bid.to_dict(),
            "runner_up_bids": [b.to_dict() for b in self.runner_up_bids],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FleetResult:
        return cls(
            winning_bid=Bid.from_dict(d["winning_bid"]),
            runner_up_bids=[
                Bid.from_dict(b) for b in d.get("runner_up_bids", [])
            ],
            reason=d.get("reason", ""),
        )


# ---------------------------------------------------------------------------
# 6. Convergence Models
# ---------------------------------------------------------------------------

class ConvergenceCriterion(str, Enum):
    """Criteria that make up a convergence certificate."""

    OBLIGATION_DECREASE = "obligation_decrease"
    DRIFT_DECREASE = "drift_decrease"
    COVERAGE_INCREASE = "coverage_increase"
    TRUST_FLOOR_INCREASE = "trust_floor_increase"
    NO_NEW_OBSTRUCTIONS = "no_new_obstructions"


@dataclass(slots=True)
class ConvergenceCertificate:
    """A certificate attesting that the system is converging."""

    id: str
    criteria_met: list[ConvergenceCriterion] = field(default_factory=list)
    obligation_trajectory: list[float] = field(default_factory=list)
    drift_trajectory: list[float] = field(default_factory=list)
    coverage_trajectory: list[float] = field(default_factory=list)
    is_converging: bool = False
    estimated_steps_to_convergence: Optional[int] = None
    issued_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "criteria_met": [c.value for c in self.criteria_met],
            "obligation_trajectory": list(self.obligation_trajectory),
            "drift_trajectory": list(self.drift_trajectory),
            "coverage_trajectory": list(self.coverage_trajectory),
            "is_converging": self.is_converging,
            "estimated_steps_to_convergence": self.estimated_steps_to_convergence,
            "issued_at": self.issued_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConvergenceCertificate:
        return cls(
            id=d["id"],
            criteria_met=[
                ConvergenceCriterion(c) for c in d.get("criteria_met", [])
            ],
            obligation_trajectory=list(d.get("obligation_trajectory", [])),
            drift_trajectory=list(d.get("drift_trajectory", [])),
            coverage_trajectory=list(d.get("coverage_trajectory", [])),
            is_converging=d.get("is_converging", False),
            estimated_steps_to_convergence=d.get(
                "estimated_steps_to_convergence"
            ),
            issued_at=d.get("issued_at", 0.0),
        )


# ---------------------------------------------------------------------------
# 7. Move Models
# ---------------------------------------------------------------------------

class MoveCategory(str, Enum):
    """Semantic category for a move."""

    IDEATION = "ideation"
    CONSTRUCTION = "construction"
    VERIFICATION = "verification"
    GROUNDING = "grounding"
    AUDIT = "audit"
    REPAIR = "repair"
    REFINEMENT = "refinement"
    DOCUMENTATION = "documentation"
    TESTING = "testing"
    BENCHMARKING = "benchmarking"
    DEPLOYMENT = "deployment"
    REVIEW = "review"


@dataclass(slots=True)
class SemanticMove:
    """A single move the controller can execute."""

    id: str
    category: MoveCategory
    name: str
    description: str = ""
    preconditions: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    target_surfaces: list[Surface] = field(default_factory=list)
    generates_obligations: list[ObligationKind] = field(default_factory=list)
    estimated_cost: float = 1.0
    priority: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "name": self.name,
            "description": self.description,
            "preconditions": list(self.preconditions),
            "effects": list(self.effects),
            "target_surfaces": [s.value for s in self.target_surfaces],
            "generates_obligations": [
                o.value for o in self.generates_obligations
            ],
            "estimated_cost": self.estimated_cost,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticMove:
        return cls(
            id=d["id"],
            category=MoveCategory(d["category"]),
            name=d["name"],
            description=d.get("description", ""),
            preconditions=list(d.get("preconditions", [])),
            effects=list(d.get("effects", [])),
            target_surfaces=[
                Surface(s) for s in d.get("target_surfaces", [])
            ],
            generates_obligations=[
                ObligationKind(o)
                for o in d.get("generates_obligations", [])
            ],
            estimated_cost=d.get("estimated_cost", 1.0),
            priority=d.get("priority", 1.0),
        )


@dataclass(slots=True)
class MoveResult:
    """Result of executing a semantic move."""

    move_id: str
    success: bool = True
    sections_modified: list[str] = field(default_factory=list)
    obligations_generated: list[str] = field(default_factory=list)
    obligations_discharged: list[str] = field(default_factory=list)
    obstructions_found: list[str] = field(default_factory=list)
    drift_changes: dict[str, float] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "move_id": self.move_id,
            "success": self.success,
            "sections_modified": list(self.sections_modified),
            "obligations_generated": list(self.obligations_generated),
            "obligations_discharged": list(self.obligations_discharged),
            "obstructions_found": list(self.obstructions_found),
            "drift_changes": dict(self.drift_changes),
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MoveResult:
        return cls(
            move_id=d["move_id"],
            success=d.get("success", True),
            sections_modified=list(d.get("sections_modified", [])),
            obligations_generated=list(d.get("obligations_generated", [])),
            obligations_discharged=list(d.get("obligations_discharged", [])),
            obstructions_found=list(d.get("obstructions_found", [])),
            drift_changes=dict(d.get("drift_changes", {})),
            duration_ms=d.get("duration_ms", 0.0),
        )


@dataclass(slots=True)
class MoveHistory:
    """Tracked history of moves with compaction support."""

    moves: list[MoveResult] = field(default_factory=list)
    compacted_moves: list[dict[str, Any]] = field(default_factory=list)
    total_moves: int = 0
    moves_since_last_compaction: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "moves": [m.to_dict() for m in self.moves],
            "compacted_moves": [dict(c) for c in self.compacted_moves],
            "total_moves": self.total_moves,
            "moves_since_last_compaction": self.moves_since_last_compaction,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MoveHistory:
        return cls(
            moves=[MoveResult.from_dict(m) for m in d.get("moves", [])],
            compacted_moves=[
                dict(c) for c in d.get("compacted_moves", [])
            ],
            total_moves=d.get("total_moves", 0),
            moves_since_last_compaction=d.get(
                "moves_since_last_compaction", 0
            ),
        )


# ---------------------------------------------------------------------------
# 8. Budget Models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BudgetAllocation:
    """How budget is allocated across regions and categories."""

    total: float = 1.0
    by_region: dict[str, float] = field(default_factory=dict)
    by_category: dict[str, float] = field(default_factory=dict)
    reserved_for_audit: float = 0.0
    reserved_for_grounding: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_region": dict(self.by_region),
            "by_category": dict(self.by_category),
            "reserved_for_audit": self.reserved_for_audit,
            "reserved_for_grounding": self.reserved_for_grounding,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BudgetAllocation:
        return cls(
            total=d.get("total", 1.0),
            by_region=dict(d.get("by_region", {})),
            by_category=dict(d.get("by_category", {})),
            reserved_for_audit=d.get("reserved_for_audit", 0.0),
            reserved_for_grounding=d.get("reserved_for_grounding", 0.0),
        )


@dataclass(slots=True)
class BudgetUsage:
    """Current budget usage snapshot."""

    spent: float = 0.0
    remaining: float = 1.0
    by_region: dict[str, float] = field(default_factory=dict)
    by_category: dict[str, float] = field(default_factory=dict)
    budget_exhaustion_eta: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "spent": self.spent,
            "remaining": self.remaining,
            "by_region": dict(self.by_region),
            "by_category": dict(self.by_category),
            "budget_exhaustion_eta": self.budget_exhaustion_eta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BudgetUsage:
        return cls(
            spent=d.get("spent", 0.0),
            remaining=d.get("remaining", 1.0),
            by_region=dict(d.get("by_region", {})),
            by_category=dict(d.get("by_category", {})),
            budget_exhaustion_eta=d.get("budget_exhaustion_eta"),
        )
