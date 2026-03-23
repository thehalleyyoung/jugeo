"""Section 3 — Migration and Donor Inheritance for the Unified Problem Atlas.

copilot: migration entry registry, donor relationships, and inheritance graph engine.

This module implements the migration and donor inheritance chapter of the
Unified Problem Atlas.  *Migration* is the process of moving a program
artefact from one context to another (language, framework, platform, or
paradigm) while preserving its externally observable behaviour.  *Donor
inheritance* is the mechanism by which a migrated artefact carries over
verified properties, trust, and evidence from its donor (the original
source artefact).

Key components
--------------
MigrationKind
    Enumeration of migration flavours (language, framework, platform,
    paradigm, data, protocol, version).
DonorRelationshipKind
    How the migrated artefact relates to its donor (clone, derivation,
    partial, annotated, synthetic).
InheritancePolicy
    Policy controlling which donor properties are inherited vs. re-verified.
MigrationEntry
    Frozen record capturing a migration specification: source, target,
    donor reference, and correctness obligations.
DonorRecord
    Frozen record capturing a donor artefact and its verified properties.
InheritanceEdge
    Frozen directed edge in the inheritance graph.
InheritanceGraph
    Mutable graph tracking donor–inheritor relationships across migrations.
MigrationDonorInheritanceAnalyzer
    Analyses migration entries for completeness and inheritance validity.
MigrationDonorInheritanceCoordinator
    Orchestrates the full migration pipeline: register → analyse → witness.
MigrationDonorInheritanceWitness
    Frozen top-level certificate for a completed migration with inheritance.

Design notes
------------
All model types are ``@dataclass(frozen=True, slots=True)`` except
``InheritanceGraph``, which is mutable.  The graph is represented as an
adjacency list of InheritanceEdge objects indexed by donor_id.
"""

from __future__ import annotations

import uuid
import math
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterator, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        ProblemCategory,
        AtlasCatalog,
    )
except ImportError:
    ProblemClass = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    AtlasCatalog = object  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.problem_atlas.specification_satisfaction import (
        SatisfactionStatus,
    )
except ImportError:
    SatisfactionStatus = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.problem_atlas.repair_and_program_transformation import (
        RepairStatus,
        TransformationKind,
    )
except ImportError:
    RepairStatus = None  # type: ignore[assignment]
    TransformationKind = None  # type: ignore[assignment]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = object  # type: ignore[assignment,misc]

# ═══════════════════════════════════════════════════════════════════════════
# §1  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

DonorId: TypeAlias = str
InheritorId: TypeAlias = str
MigrationId: TypeAlias = str
WitnessId: TypeAlias = str
JsonDict: TypeAlias = dict[str, Any]
PropertySet: TypeAlias = frozenset[str]

# ═══════════════════════════════════════════════════════════════════════════
# §2  Enumerations
# ═══════════════════════════════════════════════════════════════════════════


class MigrationKind(str, Enum):
    """Recognised migration flavours in the atlas.

    Attributes:
        LANGUAGE: Source code translated to a different programming language.
        FRAMEWORK: Code ported from one framework to another (e.g., React→Vue).
        PLATFORM: Deployment platform change (e.g., x86→ARM, bare metal→cloud).
        PARADIGM: Programming paradigm shift (e.g., OOP→functional).
        DATA: Data schema or format migration (e.g., SQL→NoSQL, CSV→Parquet).
        PROTOCOL: Communication protocol migration (e.g., REST→gRPC).
        VERSION: Major-version upgrade within the same ecosystem.
        SECURITY: Security-hardening migration (e.g., TLS 1.2→1.3).
    """

    LANGUAGE = "LANGUAGE"
    FRAMEWORK = "FRAMEWORK"
    PLATFORM = "PLATFORM"
    PARADIGM = "PARADIGM"
    DATA = "DATA"
    PROTOCOL = "PROTOCOL"
    VERSION = "VERSION"
    SECURITY = "SECURITY"

    def preserves_semantics(self) -> bool:
        """Return ``True`` when this migration is expected to preserve full semantics.

        Returns:
            True for LANGUAGE, VERSION, and SECURITY.
        """
        return self in {MigrationKind.LANGUAGE, MigrationKind.VERSION, MigrationKind.SECURITY}

    def risk_factor(self) -> float:
        """Return a base risk factor for this migration kind in [0.0, 1.0].

        Returns:
            Float risk factor.
        """
        risks: dict[MigrationKind, float] = {
            MigrationKind.LANGUAGE: 0.40,
            MigrationKind.FRAMEWORK: 0.35,
            MigrationKind.PLATFORM: 0.25,
            MigrationKind.PARADIGM: 0.50,
            MigrationKind.DATA: 0.30,
            MigrationKind.PROTOCOL: 0.30,
            MigrationKind.VERSION: 0.15,
            MigrationKind.SECURITY: 0.20,
        }
        return risks[self]

    def minimum_evidence_channels(self) -> tuple[str, ...]:
        """Return the minimum evidence channels required for this migration kind.

        Returns:
            Tuple of evidence channel identifiers.
        """
        base = ("test_suite",)
        if self.preserves_semantics():
            return base + ("formal_proof",)
        return base


class DonorRelationshipKind(str, Enum):
    """How a migrated artefact relates to its donor.

    Attributes:
        CLONE: Direct copy; all donor properties are inherited.
        DERIVATION: Derived with modifications; partial inheritance.
        PARTIAL: Only a subset of the donor was migrated.
        ANNOTATED: Donor annotations (specs, contracts) are inherited.
        SYNTHETIC: New artefact generated from donor specification only.
    """

    CLONE = "CLONE"
    DERIVATION = "DERIVATION"
    PARTIAL = "PARTIAL"
    ANNOTATED = "ANNOTATED"
    SYNTHETIC = "SYNTHETIC"

    def inheritance_fraction(self) -> float:
        """Return the expected fraction of donor properties inherited.

        Returns:
            Float in [0.0, 1.0].
        """
        fractions: dict[DonorRelationshipKind, float] = {
            DonorRelationshipKind.CLONE: 1.0,
            DonorRelationshipKind.DERIVATION: 0.7,
            DonorRelationshipKind.PARTIAL: 0.4,
            DonorRelationshipKind.ANNOTATED: 0.6,
            DonorRelationshipKind.SYNTHETIC: 0.2,
        }
        return fractions[self]

    def requires_re_verification(self) -> bool:
        """Return ``True`` when inherited properties must be re-verified.

        Returns:
            True for DERIVATION, PARTIAL, and SYNTHETIC.
        """
        return self in {
            DonorRelationshipKind.DERIVATION,
            DonorRelationshipKind.PARTIAL,
            DonorRelationshipKind.SYNTHETIC,
        }


class InheritancePolicy(str, Enum):
    """Policy controlling which donor properties are inherited.

    Attributes:
        FULL: All verified donor properties are inherited without re-verification.
        SELECTIVE: Only explicitly listed properties are inherited.
        NONE_: No properties are inherited; full re-verification required.
        CONDITIONAL: Inheritance depends on successful re-verification of a subset.
        TRUST_BOUNDED: Inherited trust is capped at a specified ceiling.
    """

    FULL = "FULL"
    SELECTIVE = "SELECTIVE"
    NONE_ = "NONE"
    CONDITIONAL = "CONDITIONAL"
    TRUST_BOUNDED = "TRUST_BOUNDED"

    def allows_automatic_inheritance(self) -> bool:
        """Return ``True`` when inheritance can proceed without human review.

        Returns:
            True for FULL and TRUST_BOUNDED.
        """
        return self in {InheritancePolicy.FULL, InheritancePolicy.TRUST_BOUNDED}


class MigrationStatus(str, Enum):
    """Lifecycle status of a migration entry.

    Attributes:
        PROPOSED: Migration specified but not yet analysed.
        ANALYSING: Feasibility and inheritance analysis in progress.
        APPROVED: Analysis passed; migration approved for execution.
        IN_PROGRESS: Migration currently being applied.
        COMPLETED: Migration applied and inheritance resolved.
        VERIFIED: Post-migration verification successful.
        FAILED: Migration failed; artefact in unknown state.
        ABORTED: Migration aborted by caller before completion.
    """

    PROPOSED = "PROPOSED"
    ANALYSING = "ANALYSING"
    APPROVED = "APPROVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

    def is_terminal(self) -> bool:
        """Return ``True`` when no further transitions are expected.

        Returns:
            True for VERIFIED, FAILED, and ABORTED.
        """
        return self in {
            MigrationStatus.VERIFIED,
            MigrationStatus.FAILED,
            MigrationStatus.ABORTED,
        }

    def is_positive(self) -> bool:
        """Return ``True`` when the status represents a successful outcome.

        Returns:
            True for COMPLETED and VERIFIED.
        """
        return self in {MigrationStatus.COMPLETED, MigrationStatus.VERIFIED}


# ═══════════════════════════════════════════════════════════════════════════
# §3  Frozen dataclasses — DonorRecord
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class DonorRecord:
    """A donor artefact and its verified properties.

    DonorRecord captures the source artefact of a migration together with
    all the properties that have been formally or informally verified.
    These properties may be partially inherited by the migrated artefact
    depending on the InheritancePolicy.

    Attributes:
        donor_id: UUID uniquely identifying this donor record.
        artefact_id: Identifier of the donor source artefact (e.g., commit SHA).
        artefact_name: Human-readable name of the donor artefact.
        verified_properties: Frozenset of property identifiers verified for donor.
        trust_score: Aggregate trust score of the donor artefact [0, 1].
        evidence_channels: Evidence channels that contributed to donor trust.
        language: Programming language of the donor artefact.
        framework: Framework or ecosystem of the donor.
        created_at: ISO-8601 timestamp of donor record creation.
        metadata: Free-form annotations.
    """

    donor_id: str
    artefact_id: str
    artefact_name: str
    verified_properties: tuple[str, ...]
    trust_score: float
    evidence_channels: tuple[str, ...]
    language: str
    framework: str
    created_at: str
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        artefact_id: str,
        artefact_name: str,
        verified_properties: tuple[str, ...] = (),
        trust_score: float = 1.0,
        evidence_channels: tuple[str, ...] = (),
        language: str = "unknown",
        framework: str = "unknown",
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "DonorRecord":
        """Create a new DonorRecord with a generated UUID and timestamp.

        Args:
            artefact_id: Source artefact identifier.
            artefact_name: Human-readable artefact name.
            verified_properties: Property identifiers verified for this donor.
            trust_score: Aggregate trust score.
            evidence_channels: Contributing evidence channel names.
            language: Source language.
            framework: Source framework/ecosystem.
            metadata: Extra annotations.

        Returns:
            A new DonorRecord.
        """
        import datetime

        return cls(
            donor_id=str(uuid.uuid4()),
            artefact_id=artefact_id,
            artefact_name=artefact_name,
            verified_properties=verified_properties,
            trust_score=max(0.0, min(1.0, trust_score)),
            evidence_channels=evidence_channels,
            language=language,
            framework=framework,
            created_at=datetime.datetime.utcnow().isoformat() + "Z",
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def property_set(self) -> frozenset[str]:
        """Return verified_properties as a frozenset.

        Returns:
            Frozenset of property identifiers.
        """
        return frozenset(self.verified_properties)

    def inheritable_properties(
        self, policy: InheritancePolicy, fraction: float = 1.0
    ) -> frozenset[str]:
        """Return the subset of properties that may be inherited under *policy*.

        Args:
            policy: The inheritance policy to apply.
            fraction: Maximum fraction of properties to include (for PARTIAL).

        Returns:
            Frozenset of inheritable property identifiers.
        """
        all_props = self.property_set()
        if policy == InheritancePolicy.NONE_:
            return frozenset()
        if policy == InheritancePolicy.FULL:
            return all_props
        n = max(1, int(len(all_props) * fraction))
        return frozenset(list(sorted(all_props))[:n])

    def metadata_dict(self) -> dict[str, str]:
        """Materialise metadata as a plain dict.

        Returns:
            Dict of annotation key-value pairs.
        """
        return dict(self.metadata)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "donor_id": self.donor_id,
            "artefact_id": self.artefact_id,
            "artefact_name": self.artefact_name,
            "verified_properties": list(self.verified_properties),
            "trust_score": self.trust_score,
            "evidence_channels": list(self.evidence_channels),
            "language": self.language,
            "framework": self.framework,
            "created_at": self.created_at,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §4  Frozen dataclasses — MigrationEntry
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    """A single migration specification in the atlas.

    Captures the full specification of a migration operation: source and
    target contexts, the donor relationship, inheritance policy, and all
    correctness obligations.

    Attributes:
        migration_id: UUID uniquely identifying this migration entry.
        class_id: Problem class this migration belongs to.
        donor_id: Identifier of the donor artefact.
        source_context: Description of the source context (language, platform, etc.).
        target_context: Description of the target context.
        migration_kind: Kind of migration operation.
        donor_relationship: How the result relates to the donor.
        inheritance_policy: Policy for property inheritance.
        obligation_ids: IDs of correctness obligations to discharge.
        inherited_properties: Properties already inherited from donor.
        re_verification_required: Properties that must be re-verified.
        status: Current lifecycle status.
        confidence: Analyst confidence [0, 1].
        provenance: Ordered provenance chain.
        metadata: Free-form annotations.
    """

    migration_id: str
    class_id: str
    donor_id: str
    source_context: str
    target_context: str
    migration_kind: MigrationKind
    donor_relationship: DonorRelationshipKind
    inheritance_policy: InheritancePolicy
    obligation_ids: tuple[str, ...]
    inherited_properties: tuple[str, ...]
    re_verification_required: tuple[str, ...]
    status: MigrationStatus
    confidence: float
    provenance: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        class_id: str,
        donor_id: str,
        source_context: str,
        target_context: str,
        migration_kind: MigrationKind,
        donor_relationship: DonorRelationshipKind = DonorRelationshipKind.DERIVATION,
        inheritance_policy: InheritancePolicy = InheritancePolicy.SELECTIVE,
        obligation_ids: tuple[str, ...] = (),
        inherited_properties: tuple[str, ...] = (),
        re_verification_required: tuple[str, ...] = (),
        confidence: float = 1.0,
        provenance: tuple[str, ...] = (),
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "MigrationEntry":
        """Create a new PROPOSED MigrationEntry with a generated UUID.

        Args:
            class_id: Problem class identifier.
            donor_id: Donor artefact identifier.
            source_context: Source language/platform/framework description.
            target_context: Target language/platform/framework description.
            migration_kind: Kind of migration.
            donor_relationship: Relationship to the donor.
            inheritance_policy: Property inheritance policy.
            obligation_ids: Correctness obligation identifiers.
            inherited_properties: Properties already inherited.
            re_verification_required: Properties requiring re-verification.
            confidence: Analyst confidence.
            provenance: Provenance chain entries.
            metadata: Extra annotations.

        Returns:
            A new PROPOSED MigrationEntry.
        """
        return cls(
            migration_id=str(uuid.uuid4()),
            class_id=class_id,
            donor_id=donor_id,
            source_context=source_context,
            target_context=target_context,
            migration_kind=migration_kind,
            donor_relationship=donor_relationship,
            inheritance_policy=inheritance_policy,
            obligation_ids=obligation_ids,
            inherited_properties=inherited_properties,
            re_verification_required=re_verification_required,
            status=MigrationStatus.PROPOSED,
            confidence=max(0.0, min(1.0, confidence)),
            provenance=provenance,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Accessors and transitions
    # ------------------------------------------------------------------

    def inherited_property_set(self) -> frozenset[str]:
        """Return inherited_properties as a frozenset.

        Returns:
            Frozenset of inherited property identifiers.
        """
        return frozenset(self.inherited_properties)

    def re_verification_set(self) -> frozenset[str]:
        """Return re_verification_required as a frozenset.

        Returns:
            Frozenset of property identifiers requiring re-verification.
        """
        return frozenset(self.re_verification_required)

    def with_status(self, status: MigrationStatus) -> "MigrationEntry":
        """Return a copy with the given status.

        Args:
            status: New lifecycle status.

        Returns:
            New MigrationEntry with updated status.
        """
        return replace(self, status=status)

    def add_inherited(self, prop: str) -> "MigrationEntry":
        """Return a copy with *prop* added to inherited_properties.

        Args:
            prop: Property identifier to add.

        Returns:
            New MigrationEntry with updated inherited_properties.
        """
        new_props = tuple(dict.fromkeys((*self.inherited_properties, prop)))
        return replace(self, inherited_properties=new_props)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "migration_id": self.migration_id,
            "class_id": self.class_id,
            "donor_id": self.donor_id,
            "source_context": self.source_context,
            "target_context": self.target_context,
            "migration_kind": self.migration_kind.value,
            "donor_relationship": self.donor_relationship.value,
            "inheritance_policy": self.inheritance_policy.value,
            "obligation_ids": list(self.obligation_ids),
            "inherited_properties": list(self.inherited_properties),
            "re_verification_required": list(self.re_verification_required),
            "status": self.status.value,
            "confidence": self.confidence,
            "provenance": list(self.provenance),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §5  InheritanceEdge and InheritanceGraph
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class InheritanceEdge:
    """A directed edge in the inheritance graph.

    An edge connects a donor artefact to its inheritor, recording which
    properties were transferred and under which policy.

    Attributes:
        edge_id: UUID for this edge.
        donor_id: Source (donor) node identifier.
        inheritor_id: Target (inheritor) node identifier.
        migration_id: The migration that created this edge.
        transferred_properties: Properties transferred via this edge.
        policy: Inheritance policy applied.
        weight: Edge weight representing strength of inheritance [0, 1].
        created_at: ISO-8601 timestamp.
    """

    edge_id: str
    donor_id: str
    inheritor_id: str
    migration_id: str
    transferred_properties: tuple[str, ...]
    policy: InheritancePolicy
    weight: float
    created_at: str

    @classmethod
    def make(
        cls,
        donor_id: str,
        inheritor_id: str,
        migration_id: str,
        transferred_properties: tuple[str, ...] = (),
        policy: InheritancePolicy = InheritancePolicy.SELECTIVE,
        weight: float = 1.0,
    ) -> "InheritanceEdge":
        """Create a new InheritanceEdge with a generated UUID and timestamp.

        Args:
            donor_id: Donor node identifier.
            inheritor_id: Inheritor node identifier.
            migration_id: Migration that created this edge.
            transferred_properties: Properties transferred.
            policy: Inheritance policy.
            weight: Edge weight.

        Returns:
            A new InheritanceEdge.
        """
        import datetime

        return cls(
            edge_id=str(uuid.uuid4()),
            donor_id=donor_id,
            inheritor_id=inheritor_id,
            migration_id=migration_id,
            transferred_properties=transferred_properties,
            policy=policy,
            weight=max(0.0, min(1.0, weight)),
            created_at=datetime.datetime.utcnow().isoformat() + "Z",
        )

    def property_set(self) -> frozenset[str]:
        """Return transferred_properties as a frozenset.

        Returns:
            Frozenset of transferred property identifiers.
        """
        return frozenset(self.transferred_properties)


class InheritanceGraph:
    """Mutable directed graph tracking donor–inheritor relationships.

    The graph is an adjacency list indexed by donor_id.  Nodes are artefact
    IDs (strings); edges are InheritanceEdge objects.

    Attributes:
        _edges_by_donor: Dict mapping donor_id → list of InheritanceEdge.
        _edges_by_inheritor: Dict mapping inheritor_id → list of InheritanceEdge.
        _all_edges: Dict mapping edge_id → InheritanceEdge.
    """

    def __init__(self) -> None:
        self._edges_by_donor: dict[str, list[InheritanceEdge]] = defaultdict(list)
        self._edges_by_inheritor: dict[str, list[InheritanceEdge]] = defaultdict(list)
        self._all_edges: dict[str, InheritanceEdge] = {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_edge(self, edge: InheritanceEdge) -> None:
        """Add an edge to the graph.

        Args:
            edge: The InheritanceEdge to add.
        """
        self._all_edges[edge.edge_id] = edge
        self._edges_by_donor[edge.donor_id].append(edge)
        self._edges_by_inheritor[edge.inheritor_id].append(edge)

    def remove_edge(self, edge_id: str) -> bool:
        """Remove an edge by its ID.

        Args:
            edge_id: The edge to remove.

        Returns:
            True if the edge existed and was removed; False otherwise.
        """
        edge = self._all_edges.pop(edge_id, None)
        if edge is None:
            return False
        self._edges_by_donor[edge.donor_id] = [
            e for e in self._edges_by_donor[edge.donor_id] if e.edge_id != edge_id
        ]
        self._edges_by_inheritor[edge.inheritor_id] = [
            e for e in self._edges_by_inheritor[edge.inheritor_id] if e.edge_id != edge_id
        ]
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def successors(self, donor_id: str) -> list[str]:
        """Return all direct inheritors of a donor.

        Args:
            donor_id: The donor node identifier.

        Returns:
            List of inheritor node identifiers.
        """
        return [e.inheritor_id for e in self._edges_by_donor.get(donor_id, [])]

    def predecessors(self, inheritor_id: str) -> list[str]:
        """Return all direct donors of an inheritor.

        Args:
            inheritor_id: The inheritor node identifier.

        Returns:
            List of donor node identifiers.
        """
        return [e.donor_id for e in self._edges_by_inheritor.get(inheritor_id, [])]

    def edges_from(self, donor_id: str) -> list[InheritanceEdge]:
        """Return all edges originating from a donor.

        Args:
            donor_id: Donor identifier.

        Returns:
            List of InheritanceEdge objects.
        """
        return list(self._edges_by_donor.get(donor_id, []))

    def edges_to(self, inheritor_id: str) -> list[InheritanceEdge]:
        """Return all edges pointing to an inheritor.

        Args:
            inheritor_id: Inheritor identifier.

        Returns:
            List of InheritanceEdge objects.
        """
        return list(self._edges_by_inheritor.get(inheritor_id, []))

    def all_nodes(self) -> frozenset[str]:
        """Return the set of all node identifiers in the graph.

        Returns:
            Frozenset of node identifiers.
        """
        nodes: set[str] = set()
        for edge in self._all_edges.values():
            nodes.add(edge.donor_id)
            nodes.add(edge.inheritor_id)
        return frozenset(nodes)

    def edge_count(self) -> int:
        """Return the total number of edges in the graph.

        Returns:
            Integer edge count.
        """
        return len(self._all_edges)

    def reachable_from(self, start_id: str) -> frozenset[str]:
        """Return all nodes reachable from *start_id* via directed edges.

        Args:
            start_id: Starting node identifier.

        Returns:
            Frozenset of reachable node identifiers (including start_id).
        """
        visited: set[str] = set()
        queue: deque[str] = deque([start_id])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            queue.extend(self.successors(node))
        return frozenset(visited)

    def topological_order(self) -> list[str]:
        """Return nodes in topological order (sources first).

        Returns:
            List of node identifiers in topological order.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        in_degree: dict[str, int] = defaultdict(int)
        for node in self.all_nodes():
            in_degree.setdefault(node, 0)
        for edge in self._all_edges.values():
            in_degree[edge.inheritor_id] += 1

        queue: deque[str] = deque(
            sorted(n for n, d in in_degree.items() if d == 0)
        )
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for succ in sorted(self.successors(node)):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if len(order) != len(in_degree):
            raise ValueError("InheritanceGraph contains a cycle.")
        return order

    def aggregate_trust(self, inheritor_id: str, donors: dict[str, float]) -> float:
        """Compute the aggregated inherited trust score for *inheritor_id*.

        The aggregated trust is the weighted average of donor trust scores
        for all edges pointing to the inheritor.

        Args:
            inheritor_id: The inheritor node identifier.
            donors: Mapping from donor_id to donor trust score.

        Returns:
            Aggregated trust score in [0.0, 1.0].
        """
        edges = self.edges_to(inheritor_id)
        if not edges:
            return 0.0
        total_weight = sum(e.weight for e in edges)
        if total_weight == 0.0:
            return 0.0
        weighted_sum = sum(
            e.weight * donors.get(e.donor_id, 0.0) for e in edges
        )
        return weighted_sum / total_weight


# ═══════════════════════════════════════════════════════════════════════════
# §6  MigrationDonorInheritanceAnalyzer
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class MigrationAnalysisResult:
    """Output of a MigrationDonorInheritanceAnalyzer pass.

    Attributes:
        result_id: UUID for this result.
        migration_id: The migration being analysed.
        is_feasible: Whether the migration is feasible.
        completeness_score: Fraction of required evidence present.
        inheritance_score: Fraction of donor properties successfully inherited.
        risk_score: Aggregate risk in [0.0, 1.0].
        gaps: Descriptions of missing evidence or property gaps.
        warnings: Non-blocking concerns.
        recommendations: Recommended actions.
    """

    result_id: str
    migration_id: str
    is_feasible: bool
    completeness_score: float
    inheritance_score: float
    risk_score: float
    gaps: tuple[str, ...]
    warnings: tuple[str, ...]
    recommendations: tuple[str, ...]

    @classmethod
    def make(
        cls,
        migration_id: str,
        is_feasible: bool,
        completeness_score: float,
        inheritance_score: float,
        risk_score: float,
        gaps: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
        recommendations: tuple[str, ...] = (),
    ) -> "MigrationAnalysisResult":
        """Create a new MigrationAnalysisResult with a generated UUID.

        Args:
            migration_id: Migration identifier.
            is_feasible: Feasibility verdict.
            completeness_score: Evidence completeness fraction.
            inheritance_score: Property inheritance fraction.
            risk_score: Aggregate risk.
            gaps: Gap descriptions.
            warnings: Non-blocking concerns.
            recommendations: Recommended actions.

        Returns:
            A new MigrationAnalysisResult.
        """
        return cls(
            result_id=str(uuid.uuid4()),
            migration_id=migration_id,
            is_feasible=is_feasible,
            completeness_score=max(0.0, min(1.0, completeness_score)),
            inheritance_score=max(0.0, min(1.0, inheritance_score)),
            risk_score=max(0.0, min(1.0, risk_score)),
            gaps=gaps,
            warnings=warnings,
            recommendations=recommendations,
        )

    def is_acceptable(self) -> bool:
        """Return ``True`` when migration may proceed.

        Returns:
            True when feasible, completeness ≥ 0.7, and risk ≤ 0.5.
        """
        return (
            self.is_feasible
            and self.completeness_score >= 0.7
            and self.risk_score <= 0.5
        )


class MigrationDonorInheritanceAnalyzer:
    """Analyses migration entries for completeness and inheritance validity.

    The analyzer examines:
    - Evidence completeness: does evidence cover required channels?
    - Inheritance validity: are inherited properties consistent with donor?
    - Re-verification obligations: which properties must be re-checked?
    - Risk aggregation: composite risk based on migration kind and evidence.
    """

    def __init__(self, donor_registry: dict[str, DonorRecord] | None = None) -> None:
        self._donors: dict[str, DonorRecord] = donor_registry or {}

    def register_donor(self, donor: DonorRecord) -> None:
        """Register a DonorRecord for use during analysis.

        Args:
            donor: The donor record to register.
        """
        self._donors[donor.donor_id] = donor

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyse(
        self,
        entry: MigrationEntry,
        evidence: dict[str, float] | None = None,
    ) -> MigrationAnalysisResult:
        """Analyse a MigrationEntry for feasibility and inheritance.

        Args:
            entry: The migration entry to analyse.
            evidence: Optional mapping from evidence channel to trust score.

        Returns:
            A MigrationAnalysisResult.
        """
        evidence = evidence or {}
        donor = self._donors.get(entry.donor_id)
        gaps: list[str] = []
        warnings: list[str] = []
        recs: list[str] = []

        # Evidence completeness
        required_channels = set(entry.migration_kind.minimum_evidence_channels())
        provided_channels = set(evidence.keys())
        missing_channels = required_channels - provided_channels
        for ch in missing_channels:
            gaps.append(f"Missing evidence channel: {ch!r}")
            recs.append(f"Provide {ch!r} evidence for {entry.migration_kind.value} migration.")

        completeness = (
            len(required_channels & provided_channels) / len(required_channels)
            if required_channels
            else 1.0
        )

        # Inheritance score
        if donor is None:
            warnings.append(f"Donor {entry.donor_id!r} not found in registry.")
            inheritance_score = 0.0
        else:
            donor_props = donor.property_set()
            inherited = frozenset(entry.inherited_properties)
            if donor_props:
                inheritance_score = len(inherited & donor_props) / len(donor_props)
            else:
                inheritance_score = 1.0

        # Re-verification completeness
        re_verify = frozenset(entry.re_verification_required)
        verified = frozenset(evidence.keys())
        pending_re_verify = re_verify - verified
        if pending_re_verify:
            warnings.append(
                f"{len(pending_re_verify)} propert(y/ies) still need re-verification: "
                + ", ".join(sorted(pending_re_verify))
            )

        # Risk
        risk = entry.migration_kind.risk_factor()
        if entry.donor_relationship.requires_re_verification():
            risk += 0.1
        if entry.confidence < 0.7:
            risk += 0.15
        if missing_channels:
            risk += 0.05 * len(missing_channels)

        is_feasible = len(gaps) == 0 or completeness >= 0.5

        return MigrationAnalysisResult.make(
            migration_id=entry.migration_id,
            is_feasible=is_feasible,
            completeness_score=completeness,
            inheritance_score=inheritance_score,
            risk_score=min(1.0, risk),
            gaps=tuple(gaps),
            warnings=tuple(warnings),
            recommendations=tuple(recs),
        )

    def analyse_batch(
        self,
        entries: Sequence[MigrationEntry],
        evidence_map: dict[str, dict[str, float]] | None = None,
    ) -> list[MigrationAnalysisResult]:
        """Analyse a batch of migration entries.

        Args:
            entries: Entries to analyse.
            evidence_map: Mapping from migration_id to evidence dict.

        Returns:
            List of MigrationAnalysisResult in input order.
        """
        ev_map = evidence_map or {}
        return [self.analyse(e, ev_map.get(e.migration_id)) for e in entries]


# ═══════════════════════════════════════════════════════════════════════════
# §7  MigrationDonorInheritanceWitness
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class MigrationDonorInheritanceWitness:
    """Top-level certificate for a completed migration with donor inheritance.

    Attributes:
        witness_id: UUID for this top-level witness.
        migration_id: The migration this witness covers.
        class_id: Problem class.
        donor_id: The donor artefact.
        analysis_result: The MigrationAnalysisResult from the analyzer.
        final_status: Terminal MigrationStatus.
        inherited_properties: Properties successfully inherited.
        re_verified_properties: Properties confirmed via re-verification.
        inherited_trust: Aggregated trust score from donor inheritance.
        overall_confidence: Aggregate confidence.
        issued_at: ISO-8601 timestamp.
        notes: Free-form notes.
    """

    witness_id: str
    migration_id: str
    class_id: str
    donor_id: str
    analysis_result: MigrationAnalysisResult
    final_status: MigrationStatus
    inherited_properties: tuple[str, ...]
    re_verified_properties: tuple[str, ...]
    inherited_trust: float
    overall_confidence: float
    issued_at: str
    notes: str

    @classmethod
    def make(
        cls,
        entry: MigrationEntry,
        analysis: MigrationAnalysisResult,
        donor: DonorRecord | None = None,
        re_verified: tuple[str, ...] = (),
        notes: str = "",
    ) -> "MigrationDonorInheritanceWitness":
        """Create a MigrationDonorInheritanceWitness from pipeline artefacts.

        Args:
            entry: The resolved migration entry.
            analysis: The analysis result.
            donor: The donor record, if available.
            re_verified: Properties confirmed by re-verification.
            notes: Free-form notes.

        Returns:
            A new MigrationDonorInheritanceWitness.
        """
        import datetime

        status = (
            MigrationStatus.VERIFIED if analysis.is_acceptable() else MigrationStatus.FAILED
        )
        inherited_trust = (donor.trust_score * analysis.inheritance_score) if donor else 0.0
        confidence = analysis.completeness_score * (1.0 - analysis.risk_score)

        return cls(
            witness_id=str(uuid.uuid4()),
            migration_id=entry.migration_id,
            class_id=entry.class_id,
            donor_id=entry.donor_id,
            analysis_result=analysis,
            final_status=status,
            inherited_properties=entry.inherited_properties,
            re_verified_properties=re_verified,
            inherited_trust=max(0.0, min(1.0, inherited_trust)),
            overall_confidence=max(0.0, min(1.0, confidence)),
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
            notes=notes,
        )

    def is_successful(self) -> bool:
        """Return ``True`` when the migration completed and was verified.

        Returns:
            True when final_status is VERIFIED.
        """
        return self.final_status == MigrationStatus.VERIFIED

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "witness_id": self.witness_id,
            "migration_id": self.migration_id,
            "class_id": self.class_id,
            "donor_id": self.donor_id,
            "final_status": self.final_status.value,
            "inherited_properties": list(self.inherited_properties),
            "re_verified_properties": list(self.re_verified_properties),
            "inherited_trust": self.inherited_trust,
            "overall_confidence": self.overall_confidence,
            "issued_at": self.issued_at,
            "notes": self.notes,
            "analysis": {
                "is_feasible": self.analysis_result.is_feasible,
                "completeness_score": self.analysis_result.completeness_score,
                "inheritance_score": self.analysis_result.inheritance_score,
                "risk_score": self.analysis_result.risk_score,
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# §8  MigrationDonorInheritanceCoordinator
# ═══════════════════════════════════════════════════════════════════════════


class MigrationDonorInheritanceCoordinator:
    """Orchestrates the full migration and donor inheritance pipeline.

    The coordinator manages:
    - A registry of DonorRecord and MigrationEntry objects.
    - The InheritanceGraph linking donors to inheritors.
    - Feasibility analysis via MigrationDonorInheritanceAnalyzer.
    - Witness production and accumulation.

    Attributes:
        analyzer: The MigrationDonorInheritanceAnalyzer.
        graph: The InheritanceGraph.
        _donors: Dict from donor_id to DonorRecord.
        _migrations: Dict from migration_id to MigrationEntry.
        _witnesses: Dict from migration_id to witness.
    """

    def __init__(
        self,
        analyzer: MigrationDonorInheritanceAnalyzer | None = None,
    ) -> None:
        self.analyzer = analyzer or MigrationDonorInheritanceAnalyzer()
        self.graph = InheritanceGraph()
        self._donors: dict[str, DonorRecord] = {}
        self._migrations: dict[str, MigrationEntry] = {}
        self._witnesses: dict[str, MigrationDonorInheritanceWitness] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_donor(self, donor: DonorRecord) -> DonorRecord:
        """Register a DonorRecord.

        Args:
            donor: The donor record to register.

        Returns:
            The registered donor.
        """
        self._donors[donor.donor_id] = donor
        self.analyzer.register_donor(donor)
        return donor

    def register_migration(self, entry: MigrationEntry) -> MigrationEntry:
        """Register a MigrationEntry.

        Args:
            entry: The migration entry to register.

        Returns:
            The registered migration entry.
        """
        self._migrations[entry.migration_id] = entry
        return entry

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def execute(
        self,
        migration_id: str,
        inheritor_id: str,
        evidence: dict[str, float] | None = None,
    ) -> MigrationDonorInheritanceWitness:
        """Analyse, apply, and witness a migration operation.

        Args:
            migration_id: The migration to execute.
            inheritor_id: The identifier of the resulting artefact.
            evidence: Optional evidence scores.

        Returns:
            A MigrationDonorInheritanceWitness.

        Raises:
            KeyError: If migration_id is not registered.
        """
        entry = self._migrations[migration_id]
        donor = self._donors.get(entry.donor_id)
        analysis = self.analyzer.analyse(entry, evidence)

        re_verified = tuple(evidence.keys()) if evidence else ()

        if analysis.is_acceptable():
            entry = entry.with_status(MigrationStatus.VERIFIED)
            # Update inheritance graph
            inherited_props = entry.inherited_properties
            if donor:
                inheritable = donor.inheritable_properties(
                    entry.inheritance_policy,
                    entry.donor_relationship.inheritance_fraction(),
                )
                inherited_props = tuple(inheritable)
                entry = replace(entry, inherited_properties=inherited_props)
            edge = InheritanceEdge.make(
                donor_id=entry.donor_id,
                inheritor_id=inheritor_id,
                migration_id=migration_id,
                transferred_properties=inherited_props,
                policy=entry.inheritance_policy,
                weight=analysis.inheritance_score,
            )
            self.graph.add_edge(edge)
        else:
            entry = entry.with_status(MigrationStatus.FAILED)

        self._migrations[migration_id] = entry
        witness = MigrationDonorInheritanceWitness.make(
            entry=entry, analysis=analysis, donor=donor, re_verified=re_verified
        )
        self._witnesses[migration_id] = witness
        return witness

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_witness(self, migration_id: str) -> MigrationDonorInheritanceWitness | None:
        """Return the witness for a migration, if available.

        Args:
            migration_id: The migration identifier.

        Returns:
            The MigrationDonorInheritanceWitness or None.
        """
        return self._witnesses.get(migration_id)

    def all_witnesses(self) -> list[MigrationDonorInheritanceWitness]:
        """Return all completed witnesses.

        Returns:
            List of MigrationDonorInheritanceWitness.
        """
        return list(self._witnesses.values())

    def success_rate(self) -> float:
        """Return fraction of migrations successfully verified.

        Returns:
            Float in [0.0, 1.0].
        """
        if not self._witnesses:
            return 0.0
        n = sum(1 for w in self._witnesses.values() if w.is_successful())
        return n / len(self._witnesses)


# ═══════════════════════════════════════════════════════════════════════════
# §9  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════


def quick_migrate(
    class_id: str,
    artefact_id: str,
    artefact_name: str,
    source_context: str,
    target_context: str,
    migration_kind: MigrationKind,
    verified_properties: tuple[str, ...] = (),
    evidence: dict[str, float] | None = None,
) -> MigrationDonorInheritanceWitness:
    """Create, register, and execute a migration in one call.

    Args:
        class_id: Problem class identifier.
        artefact_id: Source artefact identifier.
        artefact_name: Human-readable artefact name.
        source_context: Source language/platform description.
        target_context: Target language/platform description.
        migration_kind: Kind of migration.
        verified_properties: Properties verified in the source artefact.
        evidence: Optional evidence scores.

    Returns:
        A MigrationDonorInheritanceWitness.
    """
    coord = MigrationDonorInheritanceCoordinator()
    donor = DonorRecord.make(
        artefact_id=artefact_id,
        artefact_name=artefact_name,
        verified_properties=verified_properties,
    )
    coord.register_donor(donor)
    entry = MigrationEntry.make(
        class_id=class_id,
        donor_id=donor.donor_id,
        source_context=source_context,
        target_context=target_context,
        migration_kind=migration_kind,
    )
    coord.register_migration(entry)
    return coord.execute(entry.migration_id, f"inheritor::{artefact_id}", evidence)


def get_all_migration_kinds() -> list[MigrationKind]:
    """Return all MigrationKind values.

    Returns:
        List of all MigrationKind members.
    """
    return list(MigrationKind)




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
# §10  __all__
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Enumerations
    "DonorRelationshipKind",
    "InheritancePolicy",
    "MigrationKind",
    "MigrationStatus",
    # Frozen dataclasses
    "DonorRecord",
    "InheritanceEdge",
    "MigrationAnalysisResult",
    "MigrationDonorInheritanceWitness",
    "MigrationEntry",
    # Classes
    "InheritanceGraph",
    "MigrationDonorInheritanceAnalyzer",
    "MigrationDonorInheritanceCoordinator",
    # Functions
    "get_all_migration_kinds",
    "quick_migrate",
    # Type aliases
    "DonorId",
    "InheritorId",
    "JsonDict",
    "MigrationId",
    "PropertySet",
    "WitnessId",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.


# ═══════════════════════════════════════════════════════════════════════════
# §11  Smoke test
# ═══════════════════════════════════════════════════════════════════════════

def _smoke() -> None:
    """Minimal self-test: create and execute a LANGUAGE migration."""
    w = quick_migrate(
        class_id="MIGRATION",
        artefact_id="commit::abc123",
        artefact_name="legacy_parser.py",
        source_context="Python 2.7 / stdlib",
        target_context="Python 3.12 / stdlib",
        migration_kind=MigrationKind.LANGUAGE,
        verified_properties=("type_safety", "null_safety", "test_coverage"),
        evidence={"test_suite": 0.91, "formal_proof": 0.85},
    )
    assert w.final_status in MigrationStatus, f"Unexpected status: {w.final_status}"
    d = w.to_dict()
    assert "witness_id" in d and "migration_id" in d

    # Graph smoke
    g = InheritanceGraph()
    edge = InheritanceEdge.make(
        donor_id="donor::1",
        inheritor_id="inheritor::1",
        migration_id="mig::1",
        transferred_properties=("prop_a", "prop_b"),
    )
    g.add_edge(edge)
    assert "inheritor::1" in g.successors("donor::1")
    assert g.edge_count() == 1
    print(
        f"[smoke] status={w.final_status.value} "
        f"trust={w.inherited_trust:.3f} "
        f"confidence={w.overall_confidence:.3f}"
    )


if __name__ == "__main__":
    _smoke()
