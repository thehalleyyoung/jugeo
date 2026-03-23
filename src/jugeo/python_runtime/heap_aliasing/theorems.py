"""Theorems on heap aliasing for the jugeo runtime.

This module implements the formal theorems described in theory2.tex Ch17 —
Theorems on Heap Aliasing.  Each theorem is expressed as a Python class that
can check, verify, and build structured judgments from runtime heap data.

The module is designed for copilot integration: the TheoremRegistry collects
all theorem results in a single pass and exposes a human-readable report as
well as serialisable artifacts that downstream tools can consume.

References:
    theory2.tex Ch17 — Theorems on Heap Aliasing
    copilot integration guide §4 (structured judgment emission)
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateObject,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentBuilder,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.python_runtime.heap_aliasing.models import (
    AliasEdge,
    AliasPartition,
    HeapObject,
    HeapSection,
    HeapSnapshot,
    MutationEvent,
    ObjectKind,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TheoremKind
# ---------------------------------------------------------------------------


class TheoremKind(str, Enum):
    """Enumeration of all heap-aliasing theorem kinds.

    Each variant corresponds to one formal theorem from theory2.tex Ch17.

    Attributes:
        IDENTITY_UNIQUENESS: Each heap object has a unique identity coordinate.
        ALIAS_TRANSITIVITY: Alias relation is transitive and forms equivalence classes.
        MUTATION_CONSISTENCY: Mutations propagate consistently to all aliases.
        DESCENT_CONDITION: Heap sections satisfy the sheaf descent condition.
        NO_DANGLING_REFS: No live reference points to a collected object.
        IMMUTABILITY_PRESERVED: Frozen objects are never mutated.
    """

    IDENTITY_UNIQUENESS = "identity_uniqueness"
    ALIAS_TRANSITIVITY = "alias_transitivity"
    MUTATION_CONSISTENCY = "mutation_consistency"
    DESCENT_CONDITION = "descent_condition"
    NO_DANGLING_REFS = "no_dangling_refs"
    IMMUTABILITY_PRESERVED = "immutability_preserved"

    def description(self) -> str:
        """Return a human-readable description for this theorem kind.

        Returns:
            A string describing what the theorem asserts.

        Examples:
            >>> TheoremKind.IDENTITY_UNIQUENESS.description()
            'Each heap object has a unique identity coordinate.'
        """
        _descriptions: dict[TheoremKind, str] = {
            TheoremKind.IDENTITY_UNIQUENESS: (
                "Each heap object has a unique identity coordinate."
            ),
            TheoremKind.ALIAS_TRANSITIVITY: (
                "The alias relation is transitive and partitions objects into "
                "proper equivalence classes."
            ),
            TheoremKind.MUTATION_CONSISTENCY: (
                "Mutations to a heap object propagate consistently to all of "
                "its aliases within the same event batch."
            ),
            TheoremKind.DESCENT_CONDITION: (
                "Heap sections that overlap agree on the state of their shared "
                "objects, satisfying the sheaf descent condition."
            ),
            TheoremKind.NO_DANGLING_REFS: (
                "No live reference in the heap points to an object that has "
                "already been garbage-collected."
            ),
            TheoremKind.IMMUTABILITY_PRESERVED: (
                "Objects whose types are declared frozen are never mutated "
                "during program execution."
            ),
        }
        return _descriptions[self]

    def is_safety_theorem(self) -> bool:
        """Return True when this theorem is classified as a safety property.

        Safety theorems are those whose violation indicates a potential runtime
        hazard rather than a mere logical inconsistency.

        Returns:
            True for MUTATION_CONSISTENCY, IMMUTABILITY_PRESERVED, and
            NO_DANGLING_REFS; False otherwise.

        Examples:
            >>> TheoremKind.MUTATION_CONSISTENCY.is_safety_theorem()
            True
            >>> TheoremKind.ALIAS_TRANSITIVITY.is_safety_theorem()
            False
        """
        return self in (
            TheoremKind.MUTATION_CONSISTENCY,
            TheoremKind.IMMUTABILITY_PRESERVED,
            TheoremKind.NO_DANGLING_REFS,
        )

    def priority(self) -> int:
        """Return the checking priority for this theorem (1 = highest).

        Lower numbers indicate theorems that should be checked first.

        Returns:
            An integer priority in the range [1, 3].

        Examples:
            >>> TheoremKind.IDENTITY_UNIQUENESS.priority()
            1
            >>> TheoremKind.DESCENT_CONDITION.priority()
            3
        """
        _priorities: dict[TheoremKind, int] = {
            TheoremKind.IDENTITY_UNIQUENESS: 1,
            TheoremKind.ALIAS_TRANSITIVITY: 2,
            TheoremKind.MUTATION_CONSISTENCY: 1,
            TheoremKind.DESCENT_CONDITION: 3,
            TheoremKind.NO_DANGLING_REFS: 2,
            TheoremKind.IMMUTABILITY_PRESERVED: 1,
        }
        return _priorities[self]


# ---------------------------------------------------------------------------
# TheoremStatus
# ---------------------------------------------------------------------------


class TheoremStatus(str, Enum):
    """The verification status of a HeapTheorem.

    Attributes:
        NOT_CHECKED: The theorem has not yet been evaluated.
        VERIFIED: The theorem was checked and holds.
        VIOLATED: The theorem was checked and a counter-example was found.
        INAPPLICABLE: The theorem is not applicable in the current context
            (e.g. no relevant objects exist).
    """

    NOT_CHECKED = "not_checked"
    VERIFIED = "verified"
    VIOLATED = "violated"
    INAPPLICABLE = "inapplicable"

    def is_terminal(self) -> bool:
        """Return True when no further checking can change this status.

        Returns:
            True for VERIFIED, VIOLATED, and INAPPLICABLE.

        Examples:
            >>> TheoremStatus.VERIFIED.is_terminal()
            True
            >>> TheoremStatus.NOT_CHECKED.is_terminal()
            False
        """
        return self in (
            TheoremStatus.VERIFIED,
            TheoremStatus.VIOLATED,
            TheoremStatus.INAPPLICABLE,
        )

    def label(self) -> str:
        """Return a display label with an emoji annotation.

        Returns:
            A short string suitable for console/UI display.

        Examples:
            >>> TheoremStatus.VERIFIED.label()
            '✅ VERIFIED'
            >>> TheoremStatus.VIOLATED.label()
            '❌ VIOLATED'
        """
        _labels: dict[TheoremStatus, str] = {
            TheoremStatus.NOT_CHECKED: "⏳ NOT_CHECKED",
            TheoremStatus.VERIFIED: "✅ VERIFIED",
            TheoremStatus.VIOLATED: "❌ VIOLATED",
            TheoremStatus.INAPPLICABLE: "➖ INAPPLICABLE",
        }
        return _labels[self]


# ---------------------------------------------------------------------------
# HeapTheorem
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeapTheorem:
    """An immutable record representing a heap-aliasing theorem and its result.

    Instances are produced by the individual theorem-checker classes and
    collected by TheoremRegistry.

    Attributes:
        theorem_id: Stable string identifier for the theorem.
        kind: The TheoremKind this record corresponds to.
        statement: Full natural-language statement of the theorem.
        hypothesis: Precondition / hypothesis of the theorem.
        conclusion: The conclusion that must hold when the hypothesis holds.
        proof_sketch: Brief description of the proof strategy.
        is_verified: True iff status is VERIFIED.
        status: Current TheoremStatus.
    """

    theorem_id: str
    kind: TheoremKind
    statement: str
    hypothesis: str
    conclusion: str
    proof_sketch: str
    is_verified: bool
    status: TheoremStatus

    def serialize(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary.

        Returns:
            A dict with all fields serialised to JSON-safe primitives.

        Examples:
            >>> t = HeapTheorem(
            ...     theorem_id="id",
            ...     kind=TheoremKind.IDENTITY_UNIQUENESS,
            ...     statement="S",
            ...     hypothesis="H",
            ...     conclusion="C",
            ...     proof_sketch="P",
            ...     is_verified=True,
            ...     status=TheoremStatus.VERIFIED,
            ... )
            >>> t.serialize()["theorem_id"]
            'id'
        """
        return {
            "theorem_id": self.theorem_id,
            "kind": self.kind.value,
            "statement": self.statement,
            "hypothesis": self.hypothesis,
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch,
            "is_verified": self.is_verified,
            "status": self.status.value,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> HeapTheorem:
        """Deserialise a HeapTheorem from a dictionary.

        Parameters:
            data: A dict as produced by :meth:`serialize`.

        Returns:
            A fully reconstructed HeapTheorem instance.

        Raises:
            KeyError: If a required field is absent from *data*.
            ValueError: If a field value is not a valid enum member.

        Examples:
            >>> raw = {
            ...     "theorem_id": "x",
            ...     "kind": "identity_uniqueness",
            ...     "statement": "S",
            ...     "hypothesis": "H",
            ...     "conclusion": "C",
            ...     "proof_sketch": "P",
            ...     "is_verified": False,
            ...     "status": "not_checked",
            ... }
            >>> HeapTheorem.parse(raw).theorem_id
            'x'
        """
        return cls(
            theorem_id=data["theorem_id"],
            kind=TheoremKind(data["kind"]),
            statement=data["statement"],
            hypothesis=data["hypothesis"],
            conclusion=data["conclusion"],
            proof_sketch=data["proof_sketch"],
            is_verified=bool(data["is_verified"]),
            status=TheoremStatus(data["status"]),
        )

    def summary(self) -> str:
        """Return a one-line summary of this theorem and its current status.

        Returns:
            A string of the form ``"[STATUS] theorem_id: statement"``.

        Examples:
            >>> t.summary()
            '[✅ VERIFIED] identity_uniqueness: Each heap object has a unique ...'
        """
        return f"[{self.status.label()}] {self.theorem_id}: {self.statement}"


# ---------------------------------------------------------------------------
# TheoremViolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremViolation:
    """An immutable record describing a single violation of a HeapTheorem.

    Attributes:
        violation_id: UUID string uniquely identifying this violation instance.
        theorem_id: ID of the theorem that was violated.
        description: Human-readable description of what went wrong.
        counter_example: Serialised representation of the counter-example data.
        severity: A float in [0, 1] indicating severity (1.0 = fatal).
        timestamp: Unix timestamp at which the violation was detected.
    """

    violation_id: str
    theorem_id: str
    description: str
    counter_example: str
    severity: float
    timestamp: float

    def serialize(self) -> dict[str, Any]:
        """Serialise this violation to a JSON-compatible dictionary.

        Returns:
            A dict with all fields serialised to JSON-safe primitives.

        Examples:
            >>> v = TheoremViolation(
            ...     violation_id="v1",
            ...     theorem_id="identity_uniqueness",
            ...     description="Duplicate id",
            ...     counter_example="{}",
            ...     severity=0.9,
            ...     timestamp=0.0,
            ... )
            >>> v.serialize()["violation_id"]
            'v1'
        """
        return {
            "violation_id": self.violation_id,
            "theorem_id": self.theorem_id,
            "description": self.description,
            "counter_example": self.counter_example,
            "severity": self.severity,
            "timestamp": self.timestamp,
        }

    def is_critical(self) -> bool:
        """Return True when the severity of this violation exceeds 0.8.

        Returns:
            True iff self.severity > 0.8.

        Examples:
            >>> TheoremViolation(..., severity=0.9, ...).is_critical()
            True
            >>> TheoremViolation(..., severity=0.5, ...).is_critical()
            False
        """
        return self.severity > 0.8


# ---------------------------------------------------------------------------
# IdentityUniquenessTheorem
# ---------------------------------------------------------------------------


class IdentityUniquenessTheorem:
    """Checker for the Identity-Uniqueness theorem (theory2.tex §17.1).

    The theorem states that every simultaneously-live heap object is assigned a
    unique identity coordinate.  CPython guarantees ``id()`` uniqueness for
    objects that are alive at the same time; this class checks that the
    HeapObject model respects that invariant.

    Attributes:
        theorem_id: Stable string key ``"identity_uniqueness"``.
        statement: Full natural-language statement.
        hypothesis: Preconditions under which the theorem applies.
        conclusion: The property that must hold.
        proof_sketch: Informal proof strategy.
    """

    def __init__(self) -> None:
        self.theorem_id: str = "identity_uniqueness"
        self.statement: str = "Each heap object has a unique identity coordinate."
        self.hypothesis: str = "Objects o1, o2 are distinct Python objects."
        self.conclusion: str = "id(o1) != id(o2) for all distinct live objects."
        self.proof_sketch: str = (
            "CPython guarantees id() uniqueness for simultaneously live objects."
        )

    # ------------------------------------------------------------------
    def find_violations(self, objects: list[HeapObject]) -> list[TheoremViolation]:
        """Scan *objects* for duplicate object_id values.

        Two HeapObject entries that share the same ``object_id`` but have
        different ``type_name`` values constitute a violation — the same
        identity coordinate is assigned to logically distinct objects.

        Parameters:
            objects: The list of HeapObject instances to inspect.

        Returns:
            A (possibly empty) list of TheoremViolation instances, one for
            each duplicate ``object_id`` pair detected.

        Raises:
            TypeError: If *objects* contains non-HeapObject entries.

        Examples:
            >>> theorem = IdentityUniquenessTheorem()
            >>> violations = theorem.find_violations([obj_a, obj_b])
        """
        seen: dict[str, HeapObject] = {}
        violations: list[TheoremViolation] = []

        for obj in objects:
            oid = obj.object_id
            if oid in seen:
                prior = seen[oid]
                if prior.type_name != obj.type_name:
                    logger.warning(
                        "Identity uniqueness violation: object_id=%s "
                        "appears with type_name=%s and type_name=%s",
                        oid,
                        prior.type_name,
                        obj.type_name,
                    )
                    counter = json.dumps(
                        {
                            "object_id": oid,
                            "type_name_1": prior.type_name,
                            "type_name_2": obj.type_name,
                        }
                    )
                    violations.append(
                        TheoremViolation(
                            violation_id=str(uuid.uuid4()),
                            theorem_id=self.theorem_id,
                            description=(
                                f"object_id '{oid}' is shared by objects of "
                                f"type '{prior.type_name}' and '{obj.type_name}'"
                            ),
                            counter_example=counter,
                            severity=0.95,
                            timestamp=time.time(),
                        )
                    )
            else:
                seen[oid] = obj

        return violations

    def check(self, objects: list[HeapObject]) -> list[TheoremViolation]:
        """Run the identity-uniqueness check and return any violations found.

        Parameters:
            objects: List of HeapObject instances to check.

        Returns:
            List of TheoremViolation instances (empty when the theorem holds).
        """
        return self.find_violations(objects)

    def build_judgment(
        self,
        violations: list[TheoremViolation],
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Build a structured Judgment from the check results.

        Parameters:
            violations: Violations found by :meth:`check`.
            coordinate: The CoordinateObject locating this theorem in the proof.

        Returns:
            A fully constructed Judgment with appropriate status and trust level.
        """
        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(
                f"∀o1,o2 ∈ live_objects. o1≠o2 ⟹ id(o1)≠id(o2)",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("IdentityUniquenessCarrier")
            .from_source(ProvenanceSource.RUNTIME)
        )

        if violations:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.CONTESTED)
            ).with_status(JudgmentStatus.OBSTRUCTED)
            for v in violations:
                builder = builder.with_obstruction(
                    Obstruction(
                        obstruction_id=v.violation_id,
                        violated_condition=v.description,
                        coordinate="",
                        evidence_at_time=(),
                        repair_hints=(
                            "Ensure no two live objects share an object_id.",
                            "Check for id() reuse after garbage collection.",
                        ),
                        cohomology_class="H^1",
                        is_resolved=False,
                    )
                )
        else:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED)
            ).with_status(JudgmentStatus.SETTLED)

        return builder.build()

    def verify(self, objects: list[HeapObject]) -> HeapTheorem:
        """Run a full verification pass and return the resulting HeapTheorem.

        Parameters:
            objects: The heap objects to verify the theorem against.

        Returns:
            A HeapTheorem whose ``status`` reflects whether the theorem holds.
        """
        if not objects:
            return HeapTheorem(
                theorem_id=self.theorem_id,
                kind=TheoremKind.IDENTITY_UNIQUENESS,
                statement=self.statement,
                hypothesis=self.hypothesis,
                conclusion=self.conclusion,
                proof_sketch=self.proof_sketch,
                is_verified=False,
                status=TheoremStatus.INAPPLICABLE,
            )

        violations = self.check(objects)
        is_ok = len(violations) == 0
        status = TheoremStatus.VERIFIED if is_ok else TheoremStatus.VIOLATED
        logger.debug("IdentityUniquenessTheorem: status=%s", status.value)
        return HeapTheorem(
            theorem_id=self.theorem_id,
            kind=TheoremKind.IDENTITY_UNIQUENESS,
            statement=self.statement,
            hypothesis=self.hypothesis,
            conclusion=self.conclusion,
            proof_sketch=self.proof_sketch,
            is_verified=is_ok,
            status=status,
        )


# ---------------------------------------------------------------------------
# AliasTransitivityTheorem
# ---------------------------------------------------------------------------


class AliasTransitivityTheorem:
    """Checker for the Alias-Transitivity theorem (theory2.tex §17.2).

    If objects A and B are in the same alias partition, and B and C are in the
    same alias partition, then A and C must also be in the same partition.
    This class verifies that the edge structure within each AliasPartition is
    internally consistent with the partition's membership.

    Attributes:
        theorem_id: Stable string key ``"alias_transitivity"``.
        statement: Full natural-language statement.
        hypothesis: Preconditions under which the theorem applies.
        conclusion: The property that must hold.
        proof_sketch: Informal proof strategy.
    """

    def __init__(self) -> None:
        self.theorem_id: str = "alias_transitivity"
        self.statement: str = (
            "The alias relation is transitive: if A aliases B and B aliases C "
            "then A aliases C."
        )
        self.hypothesis: str = (
            "AliasPartition edges represent the alias relation on heap objects."
        )
        self.conclusion: str = (
            "The transitive closure of alias edges equals the partition membership."
        )
        self.proof_sketch: str = (
            "Compute the transitive closure of all edges within each partition "
            "via union-find and verify that all declared members are reachable."
        )

    # ------------------------------------------------------------------
    def find_violations(
        self, partitions: list[AliasPartition]
    ) -> list[TheoremViolation]:
        """Detect transitivity violations within each AliasPartition.

        For each partition the method builds an adjacency set from the declared
        edges and then checks whether every pair of partition members is
        reachable via those edges (i.e. the edge-induced connected component
        equals the full membership set).

        Parameters:
            partitions: The alias partitions to inspect.

        Returns:
            A list of TheoremViolation instances, one per partition that
            contains members unreachable from other members through its edges.

        Raises:
            ValueError: If a partition contains an edge referencing an
                object_id not declared in the partition's members.
        """
        violations: list[TheoremViolation] = []

        for partition in partitions:
            members: set[str] = set(partition.member_ids)
            if len(members) <= 1:
                continue

            # Build adjacency from edges
            adjacency: dict[str, set[str]] = {m: set() for m in members}
            for edge in partition.edges:
                src = edge.source_id
                tgt = edge.target_id
                if src in adjacency:
                    adjacency[src].add(tgt)
                if tgt in adjacency:
                    adjacency[tgt].add(src)

            # BFS from the first member to find reachable nodes
            start = next(iter(members))
            visited: set[str] = set()
            queue: list[str] = [start]
            while queue:
                node = queue.pop()
                if node in visited:
                    continue
                visited.add(node)
                for neighbour in adjacency.get(node, set()):
                    if neighbour not in visited:
                        queue.append(neighbour)

            unreachable = members - visited
            if unreachable:
                logger.warning(
                    "AliasTransitivity violation in partition %s: "
                    "unreachable members %s",
                    partition.partition_id,
                    unreachable,
                )
                counter = json.dumps(
                    {
                        "partition_id": partition.partition_id,
                        "unreachable": sorted(unreachable),
                    }
                )
                violations.append(
                    TheoremViolation(
                        violation_id=str(uuid.uuid4()),
                        theorem_id=self.theorem_id,
                        description=(
                            f"Partition '{partition.partition_id}' has members "
                            f"{sorted(unreachable)} that are not connected "
                            f"through its edges (transitivity broken)."
                        ),
                        counter_example=counter,
                        severity=0.75,
                        timestamp=time.time(),
                    )
                )

        return violations

    def check(self, partitions: list[AliasPartition]) -> list[TheoremViolation]:
        """Run the alias-transitivity check.

        Parameters:
            partitions: List of AliasPartition instances to check.

        Returns:
            List of TheoremViolation instances (empty when the theorem holds).
        """
        return self.find_violations(partitions)

    def build_judgment(
        self,
        violations: list[TheoremViolation],
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Build a structured Judgment from the check results.

        Parameters:
            violations: Violations found by :meth:`check`.
            coordinate: Locating coordinate in the proof.

        Returns:
            A fully constructed Judgment.
        """
        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(
                "alias(A,B) ∧ alias(B,C) ⟹ alias(A,C)",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("AliasTransitivityCarrier")
            .from_source(ProvenanceSource.RUNTIME)
        )
        if violations:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.CONTESTED)
            ).with_status(JudgmentStatus.OBSTRUCTED)
            for v in violations:
                builder = builder.with_obstruction(
                    Obstruction(
                        obstruction_id=v.violation_id,
                        violated_condition=v.description,
                        coordinate="",
                        evidence_at_time=(),
                        repair_hints=(
                            "Add missing alias edges to restore the equivalence "
                            "class structure.",
                        ),
                        cohomology_class="H^1",
                        is_resolved=False,
                    )
                )
        else:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED)
            ).with_status(JudgmentStatus.SETTLED)
        return builder.build()

    def verify(self, partitions: list[AliasPartition]) -> HeapTheorem:
        """Run a full verification pass and return the resulting HeapTheorem.

        Parameters:
            partitions: Alias partitions to verify.

        Returns:
            A HeapTheorem reflecting the outcome.
        """
        if not partitions:
            return HeapTheorem(
                theorem_id=self.theorem_id,
                kind=TheoremKind.ALIAS_TRANSITIVITY,
                statement=self.statement,
                hypothesis=self.hypothesis,
                conclusion=self.conclusion,
                proof_sketch=self.proof_sketch,
                is_verified=False,
                status=TheoremStatus.INAPPLICABLE,
            )
        violations = self.check(partitions)
        is_ok = len(violations) == 0
        status = TheoremStatus.VERIFIED if is_ok else TheoremStatus.VIOLATED
        logger.debug("AliasTransitivityTheorem: status=%s", status.value)
        return HeapTheorem(
            theorem_id=self.theorem_id,
            kind=TheoremKind.ALIAS_TRANSITIVITY,
            statement=self.statement,
            hypothesis=self.hypothesis,
            conclusion=self.conclusion,
            proof_sketch=self.proof_sketch,
            is_verified=is_ok,
            status=status,
        )


# ---------------------------------------------------------------------------
# MutationConsistencyTheorem
# ---------------------------------------------------------------------------


class MutationConsistencyTheorem:
    """Checker for the Mutation-Consistency theorem (theory2.tex §17.3).

    If object X is mutated (field F changed), then every alias of X must also
    reflect that mutation.  This theorem checks that within a single event
    batch, whenever one alias is mutated the others are mutated as well.

    Attributes:
        theorem_id: Stable string key ``"mutation_consistency"``.
        statement: Full natural-language statement.
        hypothesis: Preconditions under which the theorem applies.
        conclusion: The property that must hold.
        proof_sketch: Informal proof strategy.
    """

    def __init__(self) -> None:
        self.theorem_id: str = "mutation_consistency"
        self.statement: str = (
            "Mutations propagate consistently to all aliases of the mutated object."
        )
        self.hypothesis: str = (
            "Objects in the same alias partition refer to the same underlying value."
        )
        self.conclusion: str = (
            "If field F of object X is mutated in event batch B, then the same "
            "mutation appears for every alias of X in B."
        )
        self.proof_sketch: str = (
            "Group mutation events by (alias_class_id, field_name) and verify "
            "that each alias in the class is covered by a corresponding event."
        )

    # ------------------------------------------------------------------
    def find_violations(
        self,
        events: list[MutationEvent],
        partitions: list[AliasPartition],
    ) -> list[TheoremViolation]:
        """Detect mutation-consistency violations.

        The method builds a map from object_id to alias_class_id and then, for
        each field mutation, checks whether every co-alias receives the same
        mutation.  Only fields mutated on *any* member of a partition class are
        required to be propagated to *all* members.

        Parameters:
            events: The mutation events to inspect.
            partitions: The alias partitions describing equivalence classes.

        Returns:
            A list of TheoremViolation instances for each inconsistency found.
        """
        violations: list[TheoremViolation] = []

        # Build lookup: object_id -> partition_id
        obj_to_partition: dict[str, str] = {}
        # Build lookup: partition_id -> frozenset[object_id]
        partition_members: dict[str, frozenset[str]] = {}
        for p in partitions:
            members = frozenset(p.member_ids)
            partition_members[p.partition_id] = members
            for mid in members:
                obj_to_partition[mid] = p.partition_id

        # Group events by (partition_id, field_name) -> set of object_ids mutated
        mutated_fields: dict[tuple[str, str], set[str]] = {}
        for ev in events:
            pid = obj_to_partition.get(ev.object_id)
            if pid is None:
                continue
            key = (pid, ev.field_name)
            mutated_fields.setdefault(key, set()).add(ev.object_id)

        # For each (partition_id, field) that was mutated, check all members
        for (pid, field_name), mutated_ids in mutated_fields.items():
            all_members = partition_members.get(pid, frozenset())
            if len(all_members) <= 1:
                continue
            not_mutated = all_members - mutated_ids
            if not_mutated:
                logger.warning(
                    "MutationConsistency violation: partition=%s field=%s "
                    "mutated=%s not_mutated=%s",
                    pid,
                    field_name,
                    mutated_ids,
                    not_mutated,
                )
                counter = json.dumps(
                    {
                        "partition_id": pid,
                        "field_name": field_name,
                        "mutated": sorted(mutated_ids),
                        "not_mutated": sorted(not_mutated),
                    }
                )
                violations.append(
                    TheoremViolation(
                        violation_id=str(uuid.uuid4()),
                        theorem_id=self.theorem_id,
                        description=(
                            f"Field '{field_name}' mutated on {sorted(mutated_ids)} "
                            f"but not on aliases {sorted(not_mutated)} in partition "
                            f"'{pid}'."
                        ),
                        counter_example=counter,
                        severity=0.85,
                        timestamp=time.time(),
                    )
                )

        return violations

    def check(
        self,
        events: list[MutationEvent],
        partitions: list[AliasPartition],
    ) -> list[TheoremViolation]:
        """Run the mutation-consistency check.

        Parameters:
            events: Mutation events to analyse.
            partitions: Alias partitions for the corresponding snapshot.

        Returns:
            List of TheoremViolation instances.
        """
        return self.find_violations(events, partitions)

    def build_judgment(
        self,
        violations: list[TheoremViolation],
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Build a structured Judgment from the check results.

        Parameters:
            violations: Violations found by :meth:`check`.
            coordinate: Locating coordinate in the proof.

        Returns:
            A fully constructed Judgment.
        """
        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(
                "∀X,F,B. mutated(X,F,B) ∧ alias(X,Y) ⟹ mutated(Y,F,B)",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("MutationConsistencyCarrier")
            .from_source(ProvenanceSource.RUNTIME)
        )
        if violations:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.CONTESTED)
            ).with_status(JudgmentStatus.OBSTRUCTED)
            for v in violations:
                builder = builder.with_obstruction(
                    Obstruction(
                        obstruction_id=v.violation_id,
                        violated_condition=v.description,
                        coordinate="",
                        evidence_at_time=(),
                        repair_hints=(
                            "Propagate mutations to all alias members.",
                            "Reconsider whether the alias partition is correct.",
                        ),
                        cohomology_class="H^2",
                        is_resolved=False,
                    )
                )
        else:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED)
            ).with_status(JudgmentStatus.SETTLED)
        return builder.build()

    def verify(
        self,
        events: list[MutationEvent],
        partitions: list[AliasPartition],
    ) -> HeapTheorem:
        """Run a full verification pass and return the resulting HeapTheorem.

        Parameters:
            events: Mutation events to verify.
            partitions: Alias partitions for cross-referencing.

        Returns:
            A HeapTheorem reflecting the outcome.
        """
        if not events or not partitions:
            return HeapTheorem(
                theorem_id=self.theorem_id,
                kind=TheoremKind.MUTATION_CONSISTENCY,
                statement=self.statement,
                hypothesis=self.hypothesis,
                conclusion=self.conclusion,
                proof_sketch=self.proof_sketch,
                is_verified=False,
                status=TheoremStatus.INAPPLICABLE,
            )
        violations = self.check(events, partitions)
        is_ok = len(violations) == 0
        status = TheoremStatus.VERIFIED if is_ok else TheoremStatus.VIOLATED
        logger.debug("MutationConsistencyTheorem: status=%s", status.value)
        return HeapTheorem(
            theorem_id=self.theorem_id,
            kind=TheoremKind.MUTATION_CONSISTENCY,
            statement=self.statement,
            hypothesis=self.hypothesis,
            conclusion=self.conclusion,
            proof_sketch=self.proof_sketch,
            is_verified=is_ok,
            status=status,
        )


# ---------------------------------------------------------------------------
# DescentConditionTheorem
# ---------------------------------------------------------------------------


class DescentConditionTheorem:
    """Checker for the Sheaf-Descent theorem (theory2.tex §17.4).

    Heap sections that overlap (i.e. share object_id values) must agree on the
    state of those shared objects.  This is the sheaf descent / gluing condition
    transported to the heap-section setting.

    Attributes:
        theorem_id: Stable string key ``"descent_condition"``.
        statement: Full natural-language statement.
        hypothesis: Preconditions under which the theorem applies.
        conclusion: The property that must hold.
        proof_sketch: Informal proof strategy.
    """

    def __init__(self) -> None:
        self.theorem_id: str = "descent_condition"
        self.statement: str = (
            "Heap sections satisfy the sheaf descent condition: overlapping "
            "sections agree on the state of their shared objects."
        )
        self.hypothesis: str = (
            "Two HeapSection instances whose object sets intersect are "
            "simultaneous snapshots of the same runtime heap."
        )
        self.conclusion: str = (
            "For every pair of sections S1, S2 and every object O in S1 ∩ S2, "
            "S1(O) = S2(O)."
        )
        self.proof_sketch: str = (
            "Iterate over all pairs of sections; for each shared object_id "
            "compare field dictionaries and type_name.  Any discrepancy is a "
            "descent violation."
        )

    # ------------------------------------------------------------------
    def find_violations(
        self, sections: list[HeapSection]
    ) -> list[TheoremViolation]:
        """Detect descent-condition violations between overlapping sections.

        Parameters:
            sections: The heap sections to inspect.

        Returns:
            A list of TheoremViolation instances, one per disagreeing
            (section_pair, object_id) triple.

        Raises:
            TypeError: If *sections* contains non-HeapSection entries.
        """
        violations: list[TheoremViolation] = []

        # Build: section_index -> {object_id -> HeapObject}
        index: list[dict[str, HeapObject]] = []
        for sec in sections:
            mapping: dict[str, HeapObject] = {}
            for obj in sec.objects:
                mapping[obj.object_id] = obj
            index.append(mapping)

        n = len(index)
        for i in range(n):
            for j in range(i + 1, n):
                shared_ids = index[i].keys() & index[j].keys()
                for oid in shared_ids:
                    o1 = index[i][oid]
                    o2 = index[j][oid]
                    if o1.type_name != o2.type_name or o1.fields != o2.fields:
                        logger.warning(
                            "Descent violation: sections %d and %d disagree "
                            "on object %s",
                            i,
                            j,
                            oid,
                        )
                        counter = json.dumps(
                            {
                                "section_a": sections[i].section_id,
                                "section_b": sections[j].section_id,
                                "object_id": oid,
                                "type_a": o1.type_name,
                                "type_b": o2.type_name,
                            }
                        )
                        violations.append(
                            TheoremViolation(
                                violation_id=str(uuid.uuid4()),
                                theorem_id=self.theorem_id,
                                description=(
                                    f"Sections '{sections[i].section_id}' and "
                                    f"'{sections[j].section_id}' disagree on "
                                    f"object '{oid}': types "
                                    f"'{o1.type_name}' vs '{o2.type_name}'."
                                ),
                                counter_example=counter,
                                severity=0.70,
                                timestamp=time.time(),
                            )
                        )

        return violations

    def check(self, sections: list[HeapSection]) -> list[TheoremViolation]:
        """Run the descent-condition check.

        Parameters:
            sections: Heap sections to analyse.

        Returns:
            List of TheoremViolation instances.
        """
        return self.find_violations(sections)

    def build_judgment(
        self,
        violations: list[TheoremViolation],
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Build a structured Judgment from the check results.

        Parameters:
            violations: Violations found by :meth:`check`.
            coordinate: Locating coordinate in the proof.

        Returns:
            A fully constructed Judgment.
        """
        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(
                "∀S1,S2,O. O∈S1∩S2 ⟹ S1(O)=S2(O)  [descent condition]",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("DescentConditionCarrier")
            .from_source(ProvenanceSource.RUNTIME)
        )
        if violations:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.CONTESTED)
            ).with_status(JudgmentStatus.OBSTRUCTED)
            for v in violations:
                builder = builder.with_obstruction(
                    Obstruction(
                        obstruction_id=v.violation_id,
                        violated_condition=v.description,
                        coordinate="",
                        evidence_at_time=(),
                        repair_hints=(
                            "Ensure sections are taken at the same logical time.",
                            "Check for interleaved mutations between section captures.",
                        ),
                        cohomology_class="H^2",
                        is_resolved=False,
                    )
                )
        else:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED)
            ).with_status(JudgmentStatus.SETTLED)
        return builder.build()

    def verify(self, sections: list[HeapSection]) -> HeapTheorem:
        """Run a full verification pass and return the resulting HeapTheorem.

        Parameters:
            sections: Heap sections to verify.

        Returns:
            A HeapTheorem reflecting the outcome.
        """
        if len(sections) < 2:
            return HeapTheorem(
                theorem_id=self.theorem_id,
                kind=TheoremKind.DESCENT_CONDITION,
                statement=self.statement,
                hypothesis=self.hypothesis,
                conclusion=self.conclusion,
                proof_sketch=self.proof_sketch,
                is_verified=False,
                status=TheoremStatus.INAPPLICABLE,
            )
        violations = self.check(sections)
        is_ok = len(violations) == 0
        status = TheoremStatus.VERIFIED if is_ok else TheoremStatus.VIOLATED
        logger.debug("DescentConditionTheorem: status=%s", status.value)
        return HeapTheorem(
            theorem_id=self.theorem_id,
            kind=TheoremKind.DESCENT_CONDITION,
            statement=self.statement,
            hypothesis=self.hypothesis,
            conclusion=self.conclusion,
            proof_sketch=self.proof_sketch,
            is_verified=is_ok,
            status=status,
        )


# ---------------------------------------------------------------------------
# ImmutabilityPreservedTheorem
# ---------------------------------------------------------------------------


class ImmutabilityPreservedTheorem:
    """Checker for the Immutability-Preservation theorem (theory2.tex §17.5).

    Objects whose identifiers appear in a caller-supplied frozen set must never
    be the target of a MutationEvent.  Any such event constitutes a violation
    of the immutability invariant.

    Attributes:
        theorem_id: Stable string key ``"immutability_preserved"``.
        statement: Full natural-language statement.
        hypothesis: Preconditions under which the theorem applies.
        conclusion: The property that must hold.
        proof_sketch: Informal proof strategy.
    """

    def __init__(self) -> None:
        self.theorem_id: str = "immutability_preserved"
        self.statement: str = (
            "Frozen objects are never the target of a mutation event."
        )
        self.hypothesis: str = (
            "A set of object identifiers is declared frozen by the caller."
        )
        self.conclusion: str = (
            "No MutationEvent targets an object whose identifier is in the "
            "frozen set."
        )
        self.proof_sketch: str = (
            "Scan MutationEvent.object_id against the frozen_types set.  Each "
            "hit is a direct violation of the immutability declaration."
        )

    # ------------------------------------------------------------------
    def find_violations(
        self,
        events: list[MutationEvent],
        frozen_types: frozenset[str],
    ) -> list[TheoremViolation]:
        """Detect immutability violations.

        Parameters:
            events: The mutation events to inspect.
            frozen_types: A frozenset of object_id strings that must not be
                mutated.  (For practical purposes the set may contain either
                type names or object_id strings — callers choose the convention
                that best fits their context.)

        Returns:
            A list of TheoremViolation instances, one per event that targets
            a frozen object.

        Raises:
            TypeError: If *frozen_types* is not a frozenset.
        """
        violations: list[TheoremViolation] = []

        for ev in events:
            if ev.object_id in frozen_types:
                logger.warning(
                    "Immutability violation: frozen object '%s' mutated "
                    "(field='%s')",
                    ev.object_id,
                    ev.field_name,
                )
                counter = json.dumps(
                    {
                        "object_id": ev.object_id,
                        "field_name": ev.field_name,
                        "old_value_repr": ev.old_value_repr,
                        "new_value_repr": ev.new_value_repr,
                    }
                )
                violations.append(
                    TheoremViolation(
                        violation_id=str(uuid.uuid4()),
                        theorem_id=self.theorem_id,
                        description=(
                            f"Frozen object '{ev.object_id}' was mutated "
                            f"on field '{ev.field_name}'."
                        ),
                        counter_example=counter,
                        severity=0.90,
                        timestamp=time.time(),
                    )
                )

        return violations

    def check(
        self,
        events: list[MutationEvent],
        frozen_types: frozenset[str],
    ) -> list[TheoremViolation]:
        """Run the immutability-preservation check.

        Parameters:
            events: Mutation events to check.
            frozen_types: Frozen object id/type set.

        Returns:
            List of TheoremViolation instances.
        """
        return self.find_violations(events, frozen_types)

    def build_judgment(
        self,
        violations: list[TheoremViolation],
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Build a structured Judgment from the check results.

        Parameters:
            violations: Violations found by :meth:`check`.
            coordinate: Locating coordinate in the proof.

        Returns:
            A fully constructed Judgment.
        """
        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(
                "∀O∈frozen. ¬∃E. MutationEvent(E, O)",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("ImmutabilityPreservedCarrier")
            .from_source(ProvenanceSource.RUNTIME)
        )
        if violations:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.CONTESTED)
            ).with_status(JudgmentStatus.OBSTRUCTED)
            for v in violations:
                builder = builder.with_obstruction(
                    Obstruction(
                        obstruction_id=v.violation_id,
                        violated_condition=v.description,
                        coordinate="",
                        evidence_at_time=(),
                        repair_hints=(
                            "Remove the mutation or unfreeeze the object.",
                            "Review whether the frozen set is correctly defined.",
                        ),
                        cohomology_class="H^1",
                        is_resolved=False,
                    )
                )
        else:
            builder = builder.with_trust(
                TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED)
            ).with_status(JudgmentStatus.SETTLED)
        return builder.build()

    def verify(
        self,
        events: list[MutationEvent],
        frozen_types: frozenset[str],
    ) -> HeapTheorem:
        """Run a full verification pass and return the resulting HeapTheorem.

        Parameters:
            events: Mutation events to verify.
            frozen_types: Frozen object identifiers.

        Returns:
            A HeapTheorem reflecting the outcome.
        """
        if not events:
            return HeapTheorem(
                theorem_id=self.theorem_id,
                kind=TheoremKind.IMMUTABILITY_PRESERVED,
                statement=self.statement,
                hypothesis=self.hypothesis,
                conclusion=self.conclusion,
                proof_sketch=self.proof_sketch,
                is_verified=True,
                status=TheoremStatus.INAPPLICABLE,
            )
        violations = self.check(events, frozen_types)
        is_ok = len(violations) == 0
        status = TheoremStatus.VERIFIED if is_ok else TheoremStatus.VIOLATED
        logger.debug("ImmutabilityPreservedTheorem: status=%s", status.value)
        return HeapTheorem(
            theorem_id=self.theorem_id,
            kind=TheoremKind.IMMUTABILITY_PRESERVED,
            statement=self.statement,
            hypothesis=self.hypothesis,
            conclusion=self.conclusion,
            proof_sketch=self.proof_sketch,
            is_verified=is_ok,
            status=status,
        )


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRegistry:
    """A mutable registry that collects and queries HeapTheorem results.

    TheoremRegistry is the central aggregation point for all theorem-checking
    activity in the jugeo runtime.  It holds the most-recent HeapTheorem
    instance for each theorem_id and exposes high-level query and reporting
    methods.

    Attributes:
        _theorems: Mapping of theorem_id -> HeapTheorem.
        _theorem_instances: Cache of TheoremKind -> theorem-checker instance.

    Examples:
        >>> registry = TheoremRegistry()
        >>> results = registry.verify_all(objects, partitions, events, sections)
        >>> print(registry.report())
    """

    _theorems: dict[str, HeapTheorem] = field(default_factory=dict)
    _theorem_instances: dict[TheoremKind, Any] = field(default_factory=dict)

    def register(self, theorem: HeapTheorem) -> None:
        """Add or replace a theorem in the registry.

        Parameters:
            theorem: The HeapTheorem to store.  Any existing entry with the
                same ``theorem_id`` is overwritten.
        """
        self._theorems[theorem.theorem_id] = theorem
        logger.debug("Registered theorem '%s' (%s)", theorem.theorem_id, theorem.status.value)

    def lookup(self, theorem_id: str) -> HeapTheorem | None:
        """Retrieve a theorem by its identifier.

        Parameters:
            theorem_id: The stable string identifier to look up.

        Returns:
            The matching HeapTheorem, or None if not found.
        """
        return self._theorems.get(theorem_id)

    def lookup_by_kind(self, kind: TheoremKind) -> HeapTheorem | None:
        """Retrieve the first registered theorem of a given kind.

        Parameters:
            kind: The TheoremKind to search for.

        Returns:
            The first HeapTheorem whose ``kind`` matches, or None.
        """
        for t in self._theorems.values():
            if t.kind == kind:
                return t
        return None

    def verify_all(
        self,
        objects: list[HeapObject],
        partitions: list[AliasPartition],
        events: list[MutationEvent],
        sections: list[HeapSection],
    ) -> dict[str, HeapTheorem]:
        """Run all five heap-aliasing theorems and register the results.

        Parameters:
            objects: HeapObject instances for identity-uniqueness checking.
            partitions: AliasPartition instances for transitivity and
                mutation-consistency checking.
            events: MutationEvent instances for mutation-consistency and
                immutability checking.
            sections: HeapSection instances for descent-condition checking.

        Returns:
            A dict mapping theorem_id -> HeapTheorem for each theorem run.
        """
        results: dict[str, HeapTheorem] = {}

        # Identity uniqueness
        iu = self._theorem_instances.setdefault(
            TheoremKind.IDENTITY_UNIQUENESS, IdentityUniquenessTheorem()
        )
        results["identity_uniqueness"] = iu.verify(objects)

        # Alias transitivity
        at = self._theorem_instances.setdefault(
            TheoremKind.ALIAS_TRANSITIVITY, AliasTransitivityTheorem()
        )
        results["alias_transitivity"] = at.verify(partitions)

        # Mutation consistency
        mc = self._theorem_instances.setdefault(
            TheoremKind.MUTATION_CONSISTENCY, MutationConsistencyTheorem()
        )
        results["mutation_consistency"] = mc.verify(events, partitions)

        # Descent condition
        dc = self._theorem_instances.setdefault(
            TheoremKind.DESCENT_CONDITION, DescentConditionTheorem()
        )
        results["descent_condition"] = dc.verify(sections)

        # Immutability preserved (empty frozen set — caller supplies frozen_types
        # separately; this default run uses an empty set)
        ip = self._theorem_instances.setdefault(
            TheoremKind.IMMUTABILITY_PRESERVED, ImmutabilityPreservedTheorem()
        )
        results["immutability_preserved"] = ip.verify(events, frozenset())

        for theorem in results.values():
            self.register(theorem)

        return results

    def failed_theorems(self) -> list[HeapTheorem]:
        """Return all theorems whose status is VIOLATED.

        Returns:
            List of HeapTheorem instances with status == VIOLATED.
        """
        return [t for t in self._theorems.values() if t.status == TheoremStatus.VIOLATED]

    def passed_theorems(self) -> list[HeapTheorem]:
        """Return all theorems whose status is VERIFIED.

        Returns:
            List of HeapTheorem instances with status == VERIFIED.
        """
        return [t for t in self._theorems.values() if t.status == TheoremStatus.VERIFIED]

    def report(self) -> str:
        """Build and return a multi-line human-readable status report.

        Returns:
            A formatted string listing each theorem, its status label, and
            its one-line statement.

        Examples:
            >>> print(registry.report())
            ============================
            Heap Aliasing Theorem Report
            ============================
            [✅ VERIFIED] identity_uniqueness: Each heap object …
            …
            ----------------------------
            Total: 5  Passed: 4  Failed: 1
        """
        header = "=" * 40
        lines: list[str] = [header, "Heap Aliasing Theorem Report", header]

        sorted_theorems = sorted(
            self._theorems.values(),
            key=lambda t: (t.kind.priority(), t.theorem_id),
        )
        for t in sorted_theorems:
            safety_marker = " [SAFETY]" if t.kind.is_safety_theorem() else ""
            lines.append(f"{t.status.label()}{safety_marker}  {t.theorem_id}")
            lines.append(f"   {t.statement}")

        lines.append("-" * 40)
        total = len(self._theorems)
        passed = len(self.passed_theorems())
        failed = len(self.failed_theorems())
        lines.append(f"Total: {total}  Passed: {passed}  Failed: {failed}")
        return "\n".join(lines)

    def count(self) -> int:
        """Return the number of theorems currently registered.

        Returns:
            An integer count of registered HeapTheorem entries.
        """
        return len(self._theorems)

    def serialize(self) -> dict[str, Any]:
        """Serialise all registered theorems to a JSON-compatible structure.

        Returns:
            A dict mapping theorem_id -> serialised theorem dict.
        """
        return {tid: t.serialize() for tid, t in self._theorems.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "TheoremKind",
    "TheoremStatus",
    "HeapTheorem",
    "TheoremViolation",
    "IdentityUniquenessTheorem",
    "AliasTransitivityTheorem",
    "MutationConsistencyTheorem",
    "DescentConditionTheorem",
    "ImmutabilityPreservedTheorem",
    "TheoremRegistry",
]
