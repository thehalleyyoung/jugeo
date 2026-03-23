"""Section 1 — Specification Satisfaction for the Unified Problem Atlas.

copilot: spec-satisfaction router, atlas entry registry, and satisfaction witness engine.

This module implements the specification satisfaction chapter of the Unified
Problem Atlas.  Every well-formed problem in the atlas is rooted in a
*specification* — a formal or informal statement of what a correct solution
must achieve.  The atlas maps each specification kind to the subsystem that
adjudicates satisfaction and tracks the resulting witness.

Key components
--------------
SpecificationKind
    Enumeration of the recognised specification flavours (functional,
    temporal, resource, relational, emergent, meta).
SatisfactionStatus
    Lifecycle state of a satisfaction query through the atlas.
SatisfactionQuery
    Frozen record capturing a single specification-satisfaction request.
AtlasEntry
    Frozen record mapping a problem class to its specification and routing
    information.
ProblemRouter
    Stateful engine that selects the correct subsystem for each
    specification kind and records routing decisions.
AtlasWitness
    Frozen certificate produced when the router declares a specification
    satisfied.
SpecificationSatisfactionAnalyzer
    Deep analysis pass: checks specification completeness, consistency, and
    gap identification.
SpecificationSatisfactionCoordinator
    Orchestrates the full satisfaction pipeline: route → analyse → witness.
SpecificationSatisfactionWitness
    Top-level frozen certificate bundling all artefacts of a completed
    satisfaction check.

Design notes
------------
All model types are ``@dataclass(frozen=True, slots=True)``.  Mutations
return new instances via ``dataclasses.replace``.  The coordinator accepts
any mapping that provides ``specification`` and ``class_id`` keys so that
callers are not coupled to the internal ``SatisfactionQuery`` type.
"""

from __future__ import annotations

import uuid
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        ProblemCategory,
        DifficultyLevel,
        SemanticSignature,
        EvidenceRequirement,
        ConjunctionMode,
        AtlasCatalog,
    )
except ImportError:
    ProblemClass = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    DifficultyLevel = None  # type: ignore[assignment]
    SemanticSignature = object  # type: ignore[assignment,misc]
    EvidenceRequirement = object  # type: ignore[assignment,misc]
    ConjunctionMode = None  # type: ignore[assignment]
    AtlasCatalog = object  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.problem_atlas.problem_classes import (
        ProblemClassRegistry,
        STANDARD_PROBLEM_CLASSES,
    )
except ImportError:
    ProblemClassRegistry = object  # type: ignore[assignment,misc]
    STANDARD_PROBLEM_CLASSES = {}  # type: ignore[assignment]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = object  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.terms import JudgmentTuple
except ImportError:
    JudgmentTuple = object  # type: ignore[assignment,misc]

# ═══════════════════════════════════════════════════════════════════════════
# §1  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

SpecId: TypeAlias = str
ClassId: TypeAlias = str
QueryId: TypeAlias = str
WitnessId: TypeAlias = str
SubsystemId: TypeAlias = str
JsonDict: TypeAlias = dict[str, Any]
RoutingTable: TypeAlias = dict[str, SubsystemId]

# ═══════════════════════════════════════════════════════════════════════════
# §2  Enumerations
# ═══════════════════════════════════════════════════════════════════════════


class SpecificationKind(str, Enum):
    """Recognised flavours of problem specification in the atlas.

    Each kind maps to a distinct subsystem that is responsible for
    adjudicating satisfaction.  The mapping is recorded in the
    ``ProblemRouter`` routing table.

    Attributes:
        FUNCTIONAL: Input/output contract; pre- and post-condition style.
        TEMPORAL: Time-ordered property (LTL/CTL formula or trace assertion).
        RESOURCE: Obligation on memory, time, or other consumable resource.
        RELATIONAL: Cross-program or cross-version equivalence/refinement.
        EMERGENT: Property that arises from the interaction of components.
        META: Specification about specifications (e.g., completeness criteria).
        HYBRID: Composite specification combining two or more of the above.
    """

    FUNCTIONAL = "FUNCTIONAL"
    TEMPORAL = "TEMPORAL"
    RESOURCE = "RESOURCE"
    RELATIONAL = "RELATIONAL"
    EMERGENT = "EMERGENT"
    META = "META"
    HYBRID = "HYBRID"

    def canonical_subsystem(self) -> str:
        """Return the canonical subsystem identifier for this specification kind.

        Returns:
            A subsystem name string consistent with the atlas routing table.
        """
        mapping: dict[SpecificationKind, str] = {
            SpecificationKind.FUNCTIONAL: "specification_satisfaction",
            SpecificationKind.TEMPORAL: "temporal_verification",
            SpecificationKind.RESOURCE: "performance_obligations",
            SpecificationKind.RELATIONAL: "relational_refinement",
            SpecificationKind.EMERGENT: "emergent_analysis",
            SpecificationKind.META: "meta_verification",
            SpecificationKind.HYBRID: "hybrid_coordinator",
        }
        return mapping[self]

    def requires_formal_proof(self) -> bool:
        """Return ``True`` when the kind typically demands a machine-checkable proof.

        Returns:
            True for TEMPORAL, RELATIONAL, and META kinds.
        """
        return self in {
            SpecificationKind.TEMPORAL,
            SpecificationKind.RELATIONAL,
            SpecificationKind.META,
        }

    def minimum_confidence(self) -> float:
        """Return the minimum acceptable confidence score for satisfaction.

        Returns:
            A float in [0.0, 1.0]; higher values for safety-critical kinds.
        """
        thresholds: dict[SpecificationKind, float] = {
            SpecificationKind.FUNCTIONAL: 0.85,
            SpecificationKind.TEMPORAL: 0.95,
            SpecificationKind.RESOURCE: 0.90,
            SpecificationKind.RELATIONAL: 0.95,
            SpecificationKind.EMERGENT: 0.80,
            SpecificationKind.META: 0.99,
            SpecificationKind.HYBRID: 0.90,
        }
        return thresholds[self]


class SatisfactionStatus(str, Enum):
    """Lifecycle state of a specification satisfaction query.

    Attributes:
        PENDING: Query created but routing not yet attempted.
        ROUTED: Router has selected a subsystem; analysis not yet complete.
        ANALYSING: Subsystem is actively checking satisfaction.
        SATISFIED: Specification satisfied; witness issued.
        PARTIAL: Specification partially satisfied; gaps remain.
        UNSATISFIED: Specification not satisfied; counter-example may exist.
        ERROR: Internal error prevented completion of the check.
        WITHDRAWN: Query withdrawn by the caller before completion.
    """

    PENDING = "PENDING"
    ROUTED = "ROUTED"
    ANALYSING = "ANALYSING"
    SATISFIED = "SATISFIED"
    PARTIAL = "PARTIAL"
    UNSATISFIED = "UNSATISFIED"
    ERROR = "ERROR"
    WITHDRAWN = "WITHDRAWN"

    def is_terminal(self) -> bool:
        """Return ``True`` when no further transitions are possible.

        Returns:
            True for SATISFIED, UNSATISFIED, ERROR, and WITHDRAWN.
        """
        return self in {
            SatisfactionStatus.SATISFIED,
            SatisfactionStatus.UNSATISFIED,
            SatisfactionStatus.ERROR,
            SatisfactionStatus.WITHDRAWN,
        }

    def is_positive(self) -> bool:
        """Return ``True`` when the status represents a positive (passing) outcome.

        Returns:
            True for SATISFIED only.
        """
        return self == SatisfactionStatus.SATISFIED


class RouterDecision(str, Enum):
    """Decision recorded by the ProblemRouter after processing a query.

    Attributes:
        DIRECT: Query routed to exactly one subsystem without ambiguity.
        SPLIT: Query split across multiple subsystems for parallel analysis.
        DELEGATED: Query delegated to a parent or sibling subsystem.
        DEFERRED: Routing deferred pending additional context.
        REJECTED: Query rejected as malformed or unroutable.
    """

    DIRECT = "DIRECT"
    SPLIT = "SPLIT"
    DELEGATED = "DELEGATED"
    DEFERRED = "DEFERRED"
    REJECTED = "REJECTED"

    def is_actionable(self) -> bool:
        """Return ``True`` when the decision leads to immediate analysis.

        Returns:
            True for DIRECT and SPLIT.
        """
        return self in {RouterDecision.DIRECT, RouterDecision.SPLIT}


# ═══════════════════════════════════════════════════════════════════════════
# §3  Frozen dataclasses
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class SatisfactionQuery:
    """A single specification-satisfaction request submitted to the atlas.

    Attributes:
        query_id: UUID string uniquely identifying this query.
        class_id: Identifier of the problem class being checked.
        specification: Human-readable or formal specification text.
        spec_kind: Which flavour of specification this query represents.
        submitter_id: Identifier of the agent or subsystem that submitted.
        priority: Integer priority (higher = more urgent); default 0.
        context: Arbitrary key-value metadata supplied by the caller.
        status: Current lifecycle status of this query.
        target_subsystem: Subsystem selected by routing (empty until routed).
        created_at: ISO-8601 timestamp string when the query was created.
    """

    query_id: str
    class_id: str
    specification: str
    spec_kind: SpecificationKind
    submitter_id: str
    priority: int
    context: tuple[tuple[str, str], ...]
    status: SatisfactionStatus
    target_subsystem: str
    created_at: str

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        class_id: str,
        specification: str,
        spec_kind: SpecificationKind,
        submitter_id: str = "anonymous",
        priority: int = 0,
        context: tuple[tuple[str, str], ...] = (),
        created_at: str = "",
    ) -> "SatisfactionQuery":
        """Create a new PENDING SatisfactionQuery with a generated UUID.

        Args:
            class_id: Problem class identifier.
            specification: Specification text or formula.
            spec_kind: Kind of specification.
            submitter_id: Identifier of the submitting agent.
            priority: Integer urgency priority.
            context: Extra key-value metadata pairs.
            created_at: Optional ISO-8601 timestamp; auto-generated if empty.

        Returns:
            A new SatisfactionQuery in PENDING status.
        """
        import datetime

        ts = created_at or datetime.datetime.utcnow().isoformat() + "Z"
        return cls(
            query_id=str(uuid.uuid4()),
            class_id=class_id,
            specification=specification,
            spec_kind=spec_kind,
            submitter_id=submitter_id,
            priority=priority,
            context=context,
            status=SatisfactionStatus.PENDING,
            target_subsystem="",
            created_at=ts,
        )

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_pending(self) -> bool:
        """Return ``True`` when the query has not yet been routed.

        Returns:
            True for PENDING status.
        """
        return self.status == SatisfactionStatus.PENDING

    def is_complete(self) -> bool:
        """Return ``True`` when no further transitions are possible.

        Returns:
            True for terminal statuses.
        """
        return self.status.is_terminal()

    def context_dict(self) -> dict[str, str]:
        """Materialise the context tuple as a plain dict.

        Returns:
            Dict mapping context keys to values.
        """
        return dict(self.context)

    # ------------------------------------------------------------------
    # State transitions (return new frozen instances)
    # ------------------------------------------------------------------

    def with_status(self, status: SatisfactionStatus) -> "SatisfactionQuery":
        """Return a copy of this query with an updated status.

        Args:
            status: The new status to apply.

        Returns:
            A new SatisfactionQuery with the updated status.
        """
        return replace(self, status=status)

    def with_subsystem(self, subsystem: str) -> "SatisfactionQuery":
        """Return a copy with the target_subsystem set.

        Args:
            subsystem: Identifier of the routing target.

        Returns:
            A new SatisfactionQuery with target_subsystem set and status ROUTED.
        """
        return replace(
            self,
            target_subsystem=subsystem,
            status=SatisfactionStatus.ROUTED,
        )

    def to_dict(self) -> JsonDict:
        """Serialise this query to a plain JSON-compatible dict.

        Returns:
            Dict with all fields serialised to JSON-safe types.
        """
        return {
            "query_id": self.query_id,
            "class_id": self.class_id,
            "specification": self.specification,
            "spec_kind": self.spec_kind.value,
            "submitter_id": self.submitter_id,
            "priority": self.priority,
            "context": list(self.context),
            "status": self.status.value,
            "target_subsystem": self.target_subsystem,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "SatisfactionQuery":
        """Deserialise a SatisfactionQuery from a plain dict.

        Args:
            data: Dict produced by ``to_dict``.

        Returns:
            A reconstructed SatisfactionQuery.
        """
        return cls(
            query_id=data["query_id"],
            class_id=data["class_id"],
            specification=data["specification"],
            spec_kind=SpecificationKind(data["spec_kind"]),
            submitter_id=data["submitter_id"],
            priority=int(data.get("priority", 0)),
            context=tuple(tuple(p) for p in data.get("context", [])),  # type: ignore[misc]
            status=SatisfactionStatus(data["status"]),
            target_subsystem=data.get("target_subsystem", ""),
            created_at=data.get("created_at", ""),
        )


@dataclass(frozen=True, slots=True)
class AtlasEntry:
    """An entry in the Unified Problem Atlas mapping a problem class to its spec.

    AtlasEntry is the canonical record linking a ProblemClass to the
    specification that defines its correctness criterion.  Every atlas
    entry is uniquely identified and carries routing metadata so that the
    ProblemRouter can dispatch satisfaction queries without inspecting the
    full ProblemClass object.

    Attributes:
        entry_id: UUID string uniquely identifying this atlas entry.
        class_id: Identifier of the problem class this entry describes.
        class_name: Short canonical name of the problem class.
        specification: The specification text for this entry.
        spec_kind: Kind of specification used in this entry.
        canonical_subsystem: The subsystem responsible for adjudication.
        trust_ceiling: Maximum trust score achievable for this entry.
        evidence_kinds: Ordered evidence channel kinds required.
        is_active: Whether this entry is currently used for routing.
        metadata: Free-form annotations as key-value pairs.
    """

    entry_id: str
    class_id: str
    class_name: str
    specification: str
    spec_kind: SpecificationKind
    canonical_subsystem: str
    trust_ceiling: float
    evidence_kinds: tuple[str, ...]
    is_active: bool
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        class_id: str,
        class_name: str,
        specification: str,
        spec_kind: SpecificationKind,
        trust_ceiling: float = 1.0,
        evidence_kinds: tuple[str, ...] = (),
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "AtlasEntry":
        """Create a new active AtlasEntry with a generated UUID.

        Args:
            class_id: Problem class identifier.
            class_name: Short name for the class.
            specification: Specification text.
            spec_kind: Kind of specification.
            trust_ceiling: Maximum achievable trust score.
            evidence_kinds: Required evidence channel identifiers.
            metadata: Extra key-value annotations.

        Returns:
            A new active AtlasEntry.
        """
        return cls(
            entry_id=str(uuid.uuid4()),
            class_id=class_id,
            class_name=class_name,
            specification=specification,
            spec_kind=spec_kind,
            canonical_subsystem=spec_kind.canonical_subsystem(),
            trust_ceiling=max(0.0, min(1.0, trust_ceiling)),
            evidence_kinds=evidence_kinds,
            is_active=True,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def metadata_dict(self) -> dict[str, str]:
        """Materialise the metadata tuple as a plain dict.

        Returns:
            Dict of annotation key-value pairs.
        """
        return dict(self.metadata)

    def effective_trust_ceiling(self) -> float:
        """Return the effective trust ceiling, clamped to [0.0, 1.0].

        Returns:
            Float in [0.0, 1.0].
        """
        return max(0.0, min(1.0, self.trust_ceiling))

    def deactivate(self) -> "AtlasEntry":
        """Return a copy of this entry with is_active set to False.

        Returns:
            New AtlasEntry with is_active=False.
        """
        return replace(self, is_active=False)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "entry_id": self.entry_id,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "specification": self.specification,
            "spec_kind": self.spec_kind.value,
            "canonical_subsystem": self.canonical_subsystem,
            "trust_ceiling": self.trust_ceiling,
            "evidence_kinds": list(self.evidence_kinds),
            "is_active": self.is_active,
            "metadata": list(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class RoutingRecord:
    """Immutable record of a single routing decision made by the ProblemRouter.

    Attributes:
        record_id: UUID for this routing record.
        query_id: The query that was routed.
        class_id: Problem class of the query.
        spec_kind: Specification kind of the query.
        decision: The type of routing decision taken.
        selected_subsystems: Subsystems selected by this decision.
        confidence: Router's confidence in the routing decision [0, 1].
        rationale: Short human-readable explanation of the decision.
        timestamp: ISO-8601 timestamp of the routing event.
    """

    record_id: str
    query_id: str
    class_id: str
    spec_kind: SpecificationKind
    decision: RouterDecision
    selected_subsystems: tuple[str, ...]
    confidence: float
    rationale: str
    timestamp: str

    @classmethod
    def make(
        cls,
        query_id: str,
        class_id: str,
        spec_kind: SpecificationKind,
        decision: RouterDecision,
        selected_subsystems: tuple[str, ...],
        confidence: float = 1.0,
        rationale: str = "",
    ) -> "RoutingRecord":
        """Create a new RoutingRecord with a generated UUID and timestamp.

        Args:
            query_id: The ID of the query being routed.
            class_id: Problem class identifier.
            spec_kind: Kind of specification.
            decision: Routing decision taken.
            selected_subsystems: Subsystems chosen.
            confidence: Routing confidence score.
            rationale: Human-readable reason.

        Returns:
            A new RoutingRecord.
        """
        import datetime

        return cls(
            record_id=str(uuid.uuid4()),
            query_id=query_id,
            class_id=class_id,
            spec_kind=spec_kind,
            decision=decision,
            selected_subsystems=selected_subsystems,
            confidence=max(0.0, min(1.0, confidence)),
            rationale=rationale,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
        )


@dataclass(frozen=True, slots=True)
class AtlasWitness:
    """Certificate produced when the router declares a specification satisfied.

    An AtlasWitness is the formal artefact of the atlas's satisfaction
    adjudication.  It bundles the query, the atlas entry, routing record,
    and evidence scores into an immutable certificate that can be verified
    independently of the router state.

    Attributes:
        witness_id: UUID uniquely identifying this witness.
        query_id: The query that this witness resolves.
        entry_id: The atlas entry against which satisfaction was checked.
        class_id: Problem class identifier.
        status: Final satisfaction status (SATISFIED or PARTIAL).
        subsystem_verdicts: Mapping subsystem → verdict string.
        evidence_scores: Mapping evidence channel → trust score.
        aggregate_confidence: Weighted aggregate confidence score.
        is_complete: Whether all specification facets were covered.
        rationale: Human-readable explanation of the verdict.
        issued_at: ISO-8601 timestamp.
    """

    witness_id: str
    query_id: str
    entry_id: str
    class_id: str
    status: SatisfactionStatus
    subsystem_verdicts: tuple[tuple[str, str], ...]
    evidence_scores: tuple[tuple[str, float], ...]
    aggregate_confidence: float
    is_complete: bool
    rationale: str
    issued_at: str

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        query_id: str,
        entry_id: str,
        class_id: str,
        status: SatisfactionStatus,
        subsystem_verdicts: tuple[tuple[str, str], ...] = (),
        evidence_scores: tuple[tuple[str, float], ...] = (),
        aggregate_confidence: float = 1.0,
        is_complete: bool = True,
        rationale: str = "",
    ) -> "AtlasWitness":
        """Create a new AtlasWitness with a generated UUID and timestamp.

        Args:
            query_id: The resolved query's ID.
            entry_id: Atlas entry ID.
            class_id: Problem class ID.
            status: Final status.
            subsystem_verdicts: Per-subsystem verdict pairs.
            evidence_scores: Per-channel evidence scores.
            aggregate_confidence: Weighted confidence.
            is_complete: Whether coverage is full.
            rationale: Human-readable explanation.

        Returns:
            A new AtlasWitness.
        """
        import datetime

        return cls(
            witness_id=str(uuid.uuid4()),
            query_id=query_id,
            entry_id=entry_id,
            class_id=class_id,
            status=status,
            subsystem_verdicts=subsystem_verdicts,
            evidence_scores=evidence_scores,
            aggregate_confidence=max(0.0, min(1.0, aggregate_confidence)),
            is_complete=is_complete,
            rationale=rationale,
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
        )

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return ``True`` when the witness represents a genuine positive result.

        Returns:
            True when status is SATISFIED and is_complete is True.
        """
        return self.status == SatisfactionStatus.SATISFIED and self.is_complete

    def verdict_dict(self) -> dict[str, str]:
        """Materialise subsystem verdicts as a plain dict.

        Returns:
            Dict mapping subsystem name to verdict string.
        """
        return dict(self.subsystem_verdicts)

    def evidence_dict(self) -> dict[str, float]:
        """Materialise evidence scores as a plain dict.

        Returns:
            Dict mapping channel ID to trust score.
        """
        return dict(self.evidence_scores)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation of this witness.
        """
        return {
            "witness_id": self.witness_id,
            "query_id": self.query_id,
            "entry_id": self.entry_id,
            "class_id": self.class_id,
            "status": self.status.value,
            "subsystem_verdicts": list(self.subsystem_verdicts),
            "evidence_scores": list(self.evidence_scores),
            "aggregate_confidence": self.aggregate_confidence,
            "is_complete": self.is_complete,
            "rationale": self.rationale,
            "issued_at": self.issued_at,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §4  ProblemRouter
# ═══════════════════════════════════════════════════════════════════════════


class ProblemRouter:
    """Selects the correct subsystem for each specification kind.

    The ProblemRouter maintains a routing table that maps
    SpecificationKind values to subsystem identifiers.  When a
    SatisfactionQuery is submitted, the router:

    1. Looks up the spec kind in the routing table.
    2. Records a RoutingRecord capturing the decision.
    3. Returns the routed query with target_subsystem populated.

    The routing table can be customised by registering overrides via
    ``register_override``.  The router is stateful (it accumulates
    RoutingRecord history) but is safe for sequential access.

    Attributes:
        _routing_table: Mapping from SpecificationKind to subsystem id.
        _history: Ordered list of RoutingRecord entries.
        _overrides: Custom overrides applied on top of defaults.
    """

    def __init__(self) -> None:
        self._routing_table: dict[SpecificationKind, str] = {
            kind: kind.canonical_subsystem() for kind in SpecificationKind
        }
        self._history: list[RoutingRecord] = []
        self._overrides: dict[SpecificationKind, str] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def register_override(self, kind: SpecificationKind, subsystem: str) -> None:
        """Register a custom routing override for a specification kind.

        Args:
            kind: The SpecificationKind to override.
            subsystem: The subsystem identifier to route to instead.
        """
        self._overrides[kind] = subsystem
        self._routing_table[kind] = subsystem

    def clear_overrides(self) -> None:
        """Remove all custom overrides and restore default routing."""
        self._overrides.clear()
        for kind in SpecificationKind:
            self._routing_table[kind] = kind.canonical_subsystem()

    def routing_table_snapshot(self) -> dict[str, str]:
        """Return a snapshot of the current routing table.

        Returns:
            Dict mapping SpecificationKind name to subsystem identifier.
        """
        return {kind.value: subsystem for kind, subsystem in self._routing_table.items()}

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route(self, query: SatisfactionQuery) -> tuple[SatisfactionQuery, RoutingRecord]:
        """Route a query to the appropriate subsystem.

        Args:
            query: The SatisfactionQuery to route.

        Returns:
            A 2-tuple of (routed SatisfactionQuery, RoutingRecord).
        """
        subsystem = self._routing_table.get(query.spec_kind)
        if subsystem is None:
            decision = RouterDecision.REJECTED
            subsystems: tuple[str, ...] = ()
            rationale = f"No subsystem registered for kind {query.spec_kind.value!r}"
            confidence = 0.0
        else:
            decision = RouterDecision.DIRECT
            subsystems = (subsystem,)
            rationale = (
                f"Routing {query.spec_kind.value!r} query to {subsystem!r}"
                f" (min_confidence={query.spec_kind.minimum_confidence():.2f})"
            )
            confidence = 1.0

        record = RoutingRecord.make(
            query_id=query.query_id,
            class_id=query.class_id,
            spec_kind=query.spec_kind,
            decision=decision,
            selected_subsystems=subsystems,
            confidence=confidence,
            rationale=rationale,
        )
        self._history.append(record)

        routed_query = (
            query.with_subsystem(subsystem)
            if subsystem is not None
            else replace(query, status=SatisfactionStatus.ERROR)
        )
        return routed_query, record

    def route_batch(
        self, queries: Sequence[SatisfactionQuery]
    ) -> list[tuple[SatisfactionQuery, RoutingRecord]]:
        """Route a sequence of queries, returning results in the same order.

        Args:
            queries: Ordered sequence of SatisfactionQuery objects.

        Returns:
            List of (routed_query, record) pairs in input order.
        """
        return [self.route(q) for q in queries]

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def routing_history(self) -> list[RoutingRecord]:
        """Return a copy of the routing history.

        Returns:
            List of RoutingRecord entries, oldest first.
        """
        return list(self._history)

    def last_record(self) -> RoutingRecord | None:
        """Return the most recent RoutingRecord, or None if history is empty.

        Returns:
            The latest RoutingRecord or None.
        """
        return self._history[-1] if self._history else None

    def clear_history(self) -> None:
        """Clear all accumulated routing history."""
        self._history.clear()


# ═══════════════════════════════════════════════════════════════════════════
# §5  Atlas entry registry
# ═══════════════════════════════════════════════════════════════════════════


class AtlasEntryRegistry:
    """Global registry of AtlasEntry objects indexed by class_id and entry_id.

    The registry is a singleton-by-convention: callers should use
    ``global_registry()`` rather than constructing multiple instances.

    Attributes:
        _by_entry_id: Dict mapping entry_id → AtlasEntry.
        _by_class_id: Dict mapping class_id → list of AtlasEntry.
    """

    _global: "AtlasEntryRegistry | None" = None

    def __init__(self) -> None:
        self._by_entry_id: dict[str, AtlasEntry] = {}
        self._by_class_id: dict[str, list[AtlasEntry]] = defaultdict(list)

    @classmethod
    def global_registry(cls) -> "AtlasEntryRegistry":
        """Return the process-wide global registry (created on first call).

        Returns:
            The singleton AtlasEntryRegistry.
        """
        if cls._global is None:
            cls._global = cls()
            cls._global._populate_defaults()
        return cls._global

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def register(self, entry: AtlasEntry) -> None:
        """Register an AtlasEntry; silently replaces any existing entry with the same ID.

        Args:
            entry: The AtlasEntry to register.
        """
        self._by_entry_id[entry.entry_id] = entry
        entries_for_class = self._by_class_id[entry.class_id]
        entries_for_class[:] = [e for e in entries_for_class if e.entry_id != entry.entry_id]
        entries_for_class.append(entry)

    def deregister(self, entry_id: str) -> bool:
        """Remove an entry by its entry_id.

        Args:
            entry_id: The entry to remove.

        Returns:
            True if the entry existed and was removed; False otherwise.
        """
        entry = self._by_entry_id.pop(entry_id, None)
        if entry is not None:
            entries = self._by_class_id.get(entry.class_id, [])
            self._by_class_id[entry.class_id] = [
                e for e in entries if e.entry_id != entry_id
            ]
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, entry_id: str) -> AtlasEntry | None:
        """Look up an entry by entry_id.

        Args:
            entry_id: The UUID of the entry.

        Returns:
            The AtlasEntry if found, else None.
        """
        return self._by_entry_id.get(entry_id)

    def get_for_class(self, class_id: str) -> list[AtlasEntry]:
        """Return all active entries for a given class_id.

        Args:
            class_id: Problem class identifier.

        Returns:
            List of active AtlasEntry objects for the class.
        """
        return [e for e in self._by_class_id.get(class_id, []) if e.is_active]

    def all_entries(self) -> Iterator[AtlasEntry]:
        """Iterate over all registered entries.

        Yields:
            AtlasEntry objects in insertion order.
        """
        yield from self._by_entry_id.values()

    def count(self) -> int:
        """Return the total number of registered entries.

        Returns:
            Integer count.
        """
        return len(self._by_entry_id)

    # ------------------------------------------------------------------
    # Default population
    # ------------------------------------------------------------------

    def _populate_defaults(self) -> None:
        """Populate with canonical atlas entries for standard problem kinds."""
        defaults = [
            AtlasEntry.make(
                class_id="VERIFICATION",
                class_name="Verification",
                specification="For all inputs satisfying the precondition, the output satisfies the postcondition.",
                spec_kind=SpecificationKind.FUNCTIONAL,
                trust_ceiling=0.99,
                evidence_kinds=("formal_proof", "solver"),
            ),
            AtlasEntry.make(
                class_id="SEARCH",
                class_name="Search",
                specification="There exists an element in the domain satisfying the predicate.",
                spec_kind=SpecificationKind.FUNCTIONAL,
                trust_ceiling=0.95,
                evidence_kinds=("solver", "oracle"),
            ),
            AtlasEntry.make(
                class_id="EQUIVALENCE",
                class_name="Equivalence",
                specification="Two programs produce identical outputs on all inputs in the shared domain.",
                spec_kind=SpecificationKind.RELATIONAL,
                trust_ceiling=0.99,
                evidence_kinds=("formal_proof", "human"),
            ),
            AtlasEntry.make(
                class_id="TEMPORAL",
                class_name="Temporal Property",
                specification="The system satisfies the given LTL/CTL formula on all reachable traces.",
                spec_kind=SpecificationKind.TEMPORAL,
                trust_ceiling=0.99,
                evidence_kinds=("model_checker", "formal_proof"),
            ),
            AtlasEntry.make(
                class_id="RESOURCE",
                class_name="Resource Bound",
                specification="The program terminates within the specified time and space bounds.",
                spec_kind=SpecificationKind.RESOURCE,
                trust_ceiling=0.95,
                evidence_kinds=("profiler", "solver"),
            ),
        ]
        for entry in defaults:
            self.register(entry)


# ═══════════════════════════════════════════════════════════════════════════
# §6  SpecificationSatisfactionAnalyzer
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """The output of a SpecificationSatisfactionAnalyzer pass.

    Attributes:
        result_id: UUID for this analysis result.
        query_id: Query that was analysed.
        completeness_score: Fraction of specification facets that were covered.
        consistency_score: Degree to which evidence is internally consistent.
        gap_count: Number of specification facets not yet covered by evidence.
        gap_descriptions: Human-readable descriptions of each gap.
        recommendations: Ordered list of recommended next steps.
        confidence: Analyst confidence in these findings [0, 1].
    """

    result_id: str
    query_id: str
    completeness_score: float
    consistency_score: float
    gap_count: int
    gap_descriptions: tuple[str, ...]
    recommendations: tuple[str, ...]
    confidence: float

    @classmethod
    def make(
        cls,
        query_id: str,
        completeness_score: float,
        consistency_score: float,
        gap_descriptions: tuple[str, ...] = (),
        recommendations: tuple[str, ...] = (),
        confidence: float = 1.0,
    ) -> "AnalysisResult":
        """Create a new AnalysisResult with a generated UUID.

        Args:
            query_id: The query being analysed.
            completeness_score: Coverage fraction in [0, 1].
            consistency_score: Consistency score in [0, 1].
            gap_descriptions: Human-readable gap summaries.
            recommendations: Ordered recommendation strings.
            confidence: Analyst confidence.

        Returns:
            A new AnalysisResult.
        """
        return cls(
            result_id=str(uuid.uuid4()),
            query_id=query_id,
            completeness_score=max(0.0, min(1.0, completeness_score)),
            consistency_score=max(0.0, min(1.0, consistency_score)),
            gap_count=len(gap_descriptions),
            gap_descriptions=gap_descriptions,
            recommendations=recommendations,
            confidence=max(0.0, min(1.0, confidence)),
        )

    def is_acceptable(self, threshold: float = 0.8) -> bool:
        """Return ``True`` when both scores exceed *threshold*.

        Args:
            threshold: Minimum acceptable score for both completeness and consistency.

        Returns:
            True when completeness_score ≥ threshold and consistency_score ≥ threshold.
        """
        return self.completeness_score >= threshold and self.consistency_score >= threshold


class SpecificationSatisfactionAnalyzer:
    """Deep analysis pass over a SatisfactionQuery and its AtlasEntry.

    The analyzer examines:
    - Specification completeness: does the specification address all facets?
    - Specification consistency: is the specification internally coherent?
    - Gap identification: which facets are not yet covered?
    - Recommendation generation: what next steps would close the gaps?

    It is stateless from the caller's perspective — each ``analyse`` call
    is independent.
    """

    def __init__(self, entry_registry: AtlasEntryRegistry | None = None) -> None:
        self._registry = entry_registry or AtlasEntryRegistry.global_registry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(
        self,
        query: SatisfactionQuery,
        evidence: dict[str, float] | None = None,
    ) -> AnalysisResult:
        """Analyse a SatisfactionQuery against its atlas entry.

        Args:
            query: The query to analyse.
            evidence: Optional mapping from evidence channel name to trust score.

        Returns:
            An AnalysisResult capturing completeness, consistency, gaps, and
            recommendations.
        """
        evidence = evidence or {}
        entries = self._registry.get_for_class(query.class_id)

        if not entries:
            return AnalysisResult.make(
                query_id=query.query_id,
                completeness_score=0.0,
                consistency_score=0.0,
                gap_descriptions=(
                    f"No atlas entry found for class_id={query.class_id!r}",
                ),
                recommendations=("Register an AtlasEntry for this problem class.",),
                confidence=0.5,
            )

        entry = entries[0]
        completeness, gaps = self._check_completeness(query, entry, evidence)
        consistency = self._check_consistency(query, evidence)
        recommendations = self._generate_recommendations(gaps, evidence, entry)

        return AnalysisResult.make(
            query_id=query.query_id,
            completeness_score=completeness,
            consistency_score=consistency,
            gap_descriptions=tuple(gaps),
            recommendations=tuple(recommendations),
            confidence=0.9 if entries else 0.5,
        )

    def analyse_batch(
        self,
        queries: Sequence[SatisfactionQuery],
        evidence_map: dict[str, dict[str, float]] | None = None,
    ) -> list[AnalysisResult]:
        """Analyse a batch of queries.

        Args:
            queries: Queries to analyse.
            evidence_map: Optional mapping from query_id to evidence dict.

        Returns:
            List of AnalysisResult in the same order as input queries.
        """
        evidence_map = evidence_map or {}
        return [self.analyse(q, evidence_map.get(q.query_id)) for q in queries]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_completeness(
        self,
        query: SatisfactionQuery,
        entry: AtlasEntry,
        evidence: dict[str, float],
    ) -> tuple[float, list[str]]:
        """Compute completeness score and identify gaps.

        Args:
            query: The query under analysis.
            entry: The atlas entry.
            evidence: Current evidence scores.

        Returns:
            Tuple of (completeness score in [0,1], list of gap descriptions).
        """
        required = set(entry.evidence_kinds)
        provided = set(evidence.keys())
        missing = required - provided
        gaps: list[str] = []

        for channel in missing:
            gaps.append(f"Missing evidence for channel {channel!r}")

        if not required:
            return 1.0, gaps

        covered = required & provided
        score = len(covered) / len(required)
        return score, gaps

    def _check_consistency(
        self,
        query: SatisfactionQuery,
        evidence: dict[str, float],
    ) -> float:
        """Compute internal consistency of the evidence.

        Consistency degrades when evidence scores diverge significantly.

        Args:
            query: The query under analysis.
            evidence: Current evidence scores.

        Returns:
            Consistency score in [0, 1].
        """
        if not evidence:
            return 1.0
        scores = list(evidence.values())
        if len(scores) == 1:
            return scores[0]
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        stddev = math.sqrt(variance)
        return max(0.0, 1.0 - stddev)

    def _generate_recommendations(
        self,
        gaps: list[str],
        evidence: dict[str, float],
        entry: AtlasEntry,
    ) -> list[str]:
        """Generate recommendations to close identified gaps.

        Args:
            gaps: Gap descriptions from _check_completeness.
            evidence: Current evidence scores.
            entry: The atlas entry.

        Returns:
            List of actionable recommendation strings.
        """
        recs: list[str] = []
        for gap in gaps:
            channel = gap.split("channel ")[-1].strip("'\"")
            recs.append(
                f"Provide evidence for {channel} to improve specification coverage."
            )
        low_scores = [ch for ch, sc in evidence.items() if sc < entry.effective_trust_ceiling() * 0.7]
        if low_scores:
            recs.append(
                f"Improve evidence quality for channels: {', '.join(low_scores)}"
            )
        if not evidence:
            recs.append(
                "Begin by gathering evidence from the canonical subsystem: "
                f"{entry.canonical_subsystem}"
            )
        return recs


# ═══════════════════════════════════════════════════════════════════════════
# §7  SpecificationSatisfactionCoordinator
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class SpecificationSatisfactionWitness:
    """Top-level certificate bundling all artefacts of a completed satisfaction check.

    This is the outermost witness type for the specification satisfaction
    subsystem.  It aggregates the AtlasWitness, AnalysisResult, and routing
    provenance into a single immutable record suitable for persistence or
    downstream consumption.

    Attributes:
        witness_id: UUID for this top-level witness.
        query: The resolved SatisfactionQuery.
        atlas_witness: The certificate from the atlas adjudication.
        analysis_result: The AnalysisResult from the analyzer.
        routing_record: The RoutingRecord from the router.
        overall_status: Final adjudication status.
        overall_confidence: Weighted aggregate confidence across all components.
        issued_at: ISO-8601 timestamp.
        notes: Free-form human-readable notes.
    """

    witness_id: str
    query: SatisfactionQuery
    atlas_witness: AtlasWitness
    analysis_result: AnalysisResult
    routing_record: RoutingRecord
    overall_status: SatisfactionStatus
    overall_confidence: float
    issued_at: str
    notes: str

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        query: SatisfactionQuery,
        atlas_witness: AtlasWitness,
        analysis_result: AnalysisResult,
        routing_record: RoutingRecord,
        notes: str = "",
    ) -> "SpecificationSatisfactionWitness":
        """Create a SpecificationSatisfactionWitness from component artefacts.

        Args:
            query: The resolved query.
            atlas_witness: The atlas certificate.
            analysis_result: The analysis result.
            routing_record: The routing record.
            notes: Optional free-form notes.

        Returns:
            A new SpecificationSatisfactionWitness.
        """
        import datetime

        status = atlas_witness.status
        if not analysis_result.is_acceptable():
            status = SatisfactionStatus.PARTIAL

        confidence = (
            atlas_witness.aggregate_confidence * 0.6
            + analysis_result.confidence * 0.4
        )

        return cls(
            witness_id=str(uuid.uuid4()),
            query=query,
            atlas_witness=atlas_witness,
            analysis_result=analysis_result,
            routing_record=routing_record,
            overall_status=status,
            overall_confidence=max(0.0, min(1.0, confidence)),
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
            notes=notes,
        )

    def is_fully_satisfied(self) -> bool:
        """Return ``True`` iff the specification is fully and completely satisfied.

        Returns:
            True when overall_status is SATISFIED and analysis coverage is full.
        """
        return (
            self.overall_status == SatisfactionStatus.SATISFIED
            and self.analysis_result.is_acceptable()
        )

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "witness_id": self.witness_id,
            "query": self.query.to_dict(),
            "atlas_witness": self.atlas_witness.to_dict(),
            "analysis_result": {
                "result_id": self.analysis_result.result_id,
                "completeness_score": self.analysis_result.completeness_score,
                "consistency_score": self.analysis_result.consistency_score,
                "gap_count": self.analysis_result.gap_count,
            },
            "routing_record": {
                "record_id": self.routing_record.record_id,
                "decision": self.routing_record.decision.value,
                "selected_subsystems": list(self.routing_record.selected_subsystems),
            },
            "overall_status": self.overall_status.value,
            "overall_confidence": self.overall_confidence,
            "issued_at": self.issued_at,
            "notes": self.notes,
        }


class SpecificationSatisfactionCoordinator:
    """Orchestrates the full specification satisfaction pipeline.

    The coordinator ties together the ProblemRouter, AtlasEntryRegistry,
    and SpecificationSatisfactionAnalyzer into a single pipeline:

    1. ``submit`` — create and record a SatisfactionQuery.
    2. ``route`` — dispatch the query via the ProblemRouter.
    3. ``analyse`` — run the SpecificationSatisfactionAnalyzer.
    4. ``adjudicate`` — produce an AtlasWitness from evidence.
    5. ``finalise`` — bundle everything into a SpecificationSatisfactionWitness.

    The coordinator is stateful (stores submitted and resolved queries) but
    makes no I/O calls.

    Attributes:
        router: The ProblemRouter instance.
        registry: The AtlasEntryRegistry.
        analyzer: The SpecificationSatisfactionAnalyzer.
        _queries: Dict from query_id to current SatisfactionQuery.
        _witnesses: Dict from query_id to SpecificationSatisfactionWitness.
    """

    def __init__(
        self,
        router: ProblemRouter | None = None,
        registry: AtlasEntryRegistry | None = None,
        analyzer: SpecificationSatisfactionAnalyzer | None = None,
    ) -> None:
        self.router = router or ProblemRouter()
        self.registry = registry or AtlasEntryRegistry.global_registry()
        self.analyzer = analyzer or SpecificationSatisfactionAnalyzer(self.registry)
        self._queries: dict[str, SatisfactionQuery] = {}
        self._witnesses: dict[str, SpecificationSatisfactionWitness] = {}

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def submit(
        self,
        class_id: str,
        specification: str,
        spec_kind: SpecificationKind,
        submitter_id: str = "coordinator",
        priority: int = 0,
        context: tuple[tuple[str, str], ...] = (),
    ) -> SatisfactionQuery:
        """Create and register a new SatisfactionQuery.

        Args:
            class_id: Problem class identifier.
            specification: Specification text.
            spec_kind: Kind of specification.
            submitter_id: Identifier of the submitter.
            priority: Integer urgency priority.
            context: Extra metadata key-value pairs.

        Returns:
            The newly created SatisfactionQuery in PENDING status.
        """
        query = SatisfactionQuery.make(
            class_id=class_id,
            specification=specification,
            spec_kind=spec_kind,
            submitter_id=submitter_id,
            priority=priority,
            context=context,
        )
        self._queries[query.query_id] = query
        return query

    def run(
        self,
        query: SatisfactionQuery,
        evidence: dict[str, float] | None = None,
    ) -> SpecificationSatisfactionWitness:
        """Run the full pipeline for a single query.

        Args:
            query: The SatisfactionQuery to process.
            evidence: Optional evidence scores to incorporate.

        Returns:
            A SpecificationSatisfactionWitness bundling all results.
        """
        evidence = evidence or {}

        # Step 1: route
        routed_query, routing_record = self.router.route(query)
        self._queries[query.query_id] = routed_query

        # Step 2: analyse
        analysis = self.analyzer.analyse(routed_query, evidence)

        # Step 3: adjudicate
        entries = self.registry.get_for_class(routed_query.class_id)
        entry_id = entries[0].entry_id if entries else "unknown"

        if routing_record.decision == RouterDecision.REJECTED:
            status = SatisfactionStatus.ERROR
        elif analysis.is_acceptable():
            status = SatisfactionStatus.SATISFIED
        elif analysis.completeness_score > 0.0:
            status = SatisfactionStatus.PARTIAL
        else:
            status = SatisfactionStatus.UNSATISFIED

        atlas_witness = AtlasWitness.make(
            query_id=routed_query.query_id,
            entry_id=entry_id,
            class_id=routed_query.class_id,
            status=status,
            evidence_scores=tuple(evidence.items()),
            aggregate_confidence=analysis.confidence,
            is_complete=analysis.gap_count == 0,
            rationale=routing_record.rationale,
        )

        # Step 4: finalise
        final_query = routed_query.with_status(status)
        self._queries[query.query_id] = final_query

        witness = SpecificationSatisfactionWitness.make(
            query=final_query,
            atlas_witness=atlas_witness,
            analysis_result=analysis,
            routing_record=routing_record,
        )
        self._witnesses[query.query_id] = witness
        return witness

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_query(self, query_id: str) -> SatisfactionQuery | None:
        """Look up a query by its ID.

        Args:
            query_id: The UUID of the query.

        Returns:
            The SatisfactionQuery if found, else None.
        """
        return self._queries.get(query_id)

    def get_witness(self, query_id: str) -> SpecificationSatisfactionWitness | None:
        """Look up a completed witness by query ID.

        Args:
            query_id: The UUID of the resolved query.

        Returns:
            The SpecificationSatisfactionWitness if available, else None.
        """
        return self._witnesses.get(query_id)

    def all_witnesses(self) -> list[SpecificationSatisfactionWitness]:
        """Return all completed witnesses.

        Returns:
            List of SpecificationSatisfactionWitness in insertion order.
        """
        return list(self._witnesses.values())

    def satisfaction_rate(self) -> float:
        """Return fraction of completed queries that are fully satisfied.

        Returns:
            Float in [0.0, 1.0]; 0.0 if no witnesses exist.
        """
        if not self._witnesses:
            return 0.0
        satisfied = sum(
            1 for w in self._witnesses.values() if w.is_fully_satisfied()
        )
        return satisfied / len(self._witnesses)


# ═══════════════════════════════════════════════════════════════════════════
# §8  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════


def quick_check(
    class_id: str,
    specification: str,
    spec_kind: SpecificationKind,
    evidence: dict[str, float] | None = None,
) -> SpecificationSatisfactionWitness:
    """Run a one-shot satisfaction check using the global coordinator.

    Args:
        class_id: Problem class identifier.
        specification: Specification text or formula.
        spec_kind: Kind of specification.
        evidence: Optional evidence scores.

    Returns:
        A SpecificationSatisfactionWitness with the result.
    """
    coord = SpecificationSatisfactionCoordinator()
    query = coord.submit(class_id, specification, spec_kind)
    return coord.run(query, evidence)


def build_default_coordinator() -> SpecificationSatisfactionCoordinator:
    """Construct a SpecificationSatisfactionCoordinator with default components.

    Returns:
        A ready-to-use SpecificationSatisfactionCoordinator.
    """
    return SpecificationSatisfactionCoordinator()


def get_all_specification_kinds() -> list[SpecificationKind]:
    """Return all SpecificationKind values in declaration order.

    Returns:
        List of all SpecificationKind members.
    """
    return list(SpecificationKind)




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ═══════════════════════════════════════════════════════════════════════════
# §9  __all__
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Enumerations
    "RouterDecision",
    "SatisfactionStatus",
    "SpecificationKind",
    # Frozen dataclasses
    "AnalysisResult",
    "AtlasEntry",
    "AtlasWitness",
    "RoutingRecord",
    "SatisfactionQuery",
    "SpecificationSatisfactionWitness",
    # Classes
    "AtlasEntryRegistry",
    "ProblemRouter",
    "SpecificationSatisfactionAnalyzer",
    "SpecificationSatisfactionCoordinator",
    # Functions
    "build_default_coordinator",
    "get_all_specification_kinds",
    "quick_check",
    # Type aliases
    "ClassId",
    "JsonDict",
    "QueryId",
    "RoutingTable",
    "SpecId",
    "SubsystemId",
    "WitnessId",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.


# ═══════════════════════════════════════════════════════════════════════════
# §10  Smoke test
# ═══════════════════════════════════════════════════════════════════════════

def _smoke() -> None:
    """Minimal self-test: submit a FUNCTIONAL query and verify the witness."""
    coord = SpecificationSatisfactionCoordinator()
    query = coord.submit(
        class_id="VERIFICATION",
        specification="Output is always greater than input.",
        spec_kind=SpecificationKind.FUNCTIONAL,
        submitter_id="smoke_test",
    )
    assert query.is_pending(), "Query should start as PENDING"

    witness = coord.run(query, evidence={"formal_proof": 0.97, "solver": 0.91})
    assert witness.overall_confidence > 0.0, "Confidence should be positive"
    assert witness.overall_status in SatisfactionStatus, "Status should be valid"
    assert witness.routing_record.decision == RouterDecision.DIRECT, (
        "FUNCTIONAL spec should route DIRECT"
    )
    d = witness.to_dict()
    assert "witness_id" in d and "overall_status" in d
    print(
        f"[smoke] status={witness.overall_status.value} "
        f"confidence={witness.overall_confidence:.3f}"
    )


if __name__ == "__main__":
    _smoke()
