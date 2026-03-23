"""Section 14.1 — Problem Classes implementation for the Unified Problem Atlas.

copilot: problem class registry and lattice computation engine.

This module implements §14.1 of Theory2.tex, providing the complete machinery
for building, registering, and querying problem classes.  A *problem class* is
a formal category of computational or verification problem, characterized by:

  - A position in a class lattice (parent/child relationships)
  - A difficulty and decidability assessment
  - A set of canonical instance templates
  - Required evidence kinds for verification

Key components:
  ProblemClassBuilder      — Fluent builder for ProblemClass instances
  ClassLatticeComputer     — Computes lattice structure and topological order
  ProblemClassRegistry     — Global singleton registry of all known classes
  InstanceGenerator        — Generates canonical problem instances from templates
  ProblemClassSerializer   — JSON serialization/deserialization
  STANDARD_PROBLEM_CLASSES — Mapping from canonical names to ProblemClass objects
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Callable, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        ProblemCategory,
        DifficultyLevel,
        DecidabilityKind,
        SemanticSignature,
        EvidenceRequirement,
        ConjunctionMode,
    )
except ImportError:
    # Fallback for standalone testing
    ProblemClass = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    DifficultyLevel = None  # type: ignore[assignment]
    DecidabilityKind = None  # type: ignore[assignment]
    SemanticSignature = object  # type: ignore[assignment,misc]
    EvidenceRequirement = object  # type: ignore[assignment,misc]
    ConjunctionMode = None  # type: ignore[assignment]

# ═══════════════════════════════════════════════════════════════════════════
# §1  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

ClassId: TypeAlias = str
ClassName: TypeAlias = str
JsonStr: TypeAlias = str

# ═══════════════════════════════════════════════════════════════════════════
# §2  Enumerations
# ═══════════════════════════════════════════════════════════════════════════


class ProblemCategory(str, Enum):
    """Broad category that groups related problem classes together.

    Each category corresponds to a distinct mode of reasoning or computation
    as defined in Theory2.tex §14.1.2.

    Attributes:
        COMPUTATIONAL: Problems involving algorithmic computation over finite inputs.
        VERIFICATION: Problems requiring evidence-backed certification of claims.
        CONSTRUCTIVE: Problems whose solutions must exhibit a constructed artifact.
        ANALYTICAL: Problems solved by analysis, classification, or inference.
        RELATIONAL: Problems defined by relations between multiple entities.
    """

    COMPUTATIONAL = "computational"
    VERIFICATION = "verification"
    CONSTRUCTIVE = "constructive"
    ANALYTICAL = "analytical"
    RELATIONAL = "relational"

    def is_output_producing(self) -> bool:
        """Return ``True`` when the category typically produces a tangible artifact.

        Returns:
            True for COMPUTATIONAL, CONSTRUCTIVE, and ANALYTICAL categories.
        """
        return self in {
            ProblemCategory.COMPUTATIONAL,
            ProblemCategory.CONSTRUCTIVE,
            ProblemCategory.ANALYTICAL,
        }

    def requires_certificate(self) -> bool:
        """Return ``True`` when the category typically requires a formal certificate.

        Returns:
            True for VERIFICATION and RELATIONAL categories.
        """
        return self in {ProblemCategory.VERIFICATION, ProblemCategory.RELATIONAL}

    def default_evidence_channel(self) -> str:
        """Return the canonical evidence channel associated with this category.

        Returns:
            A string naming the default channel (solver, formal_proof, oracle, etc.).
        """
        mapping: dict[ProblemCategory, str] = {
            ProblemCategory.COMPUTATIONAL: "solver",
            ProblemCategory.VERIFICATION: "formal_proof",
            ProblemCategory.CONSTRUCTIVE: "runtime",
            ProblemCategory.ANALYTICAL: "oracle",
            ProblemCategory.RELATIONAL: "human",
        }
        return mapping[self]


class DifficultyLevel(str, Enum):
    """Qualitative difficulty rating for a problem class.

    This is an informal classification aligned with the complexity class
    hierarchy described in Theory2.tex §14.1.3.  It is not a formal
    complexity class but a practitioner-facing label.

    Attributes:
        TRIVIAL: Solvable in constant time; no real algorithmic challenge.
        EASY: Polynomial time with small exponents; widely tractable.
        MODERATE: Polynomial time but non-trivial; may require careful algorithms.
        HARD: NP-hard or PSPACE-hard in the worst case; heuristics typically needed.
        INTRACTABLE: Believed to be computationally infeasible in general.
        UNDECIDABLE: No algorithm can solve all instances; incompleteness applies.
    """

    TRIVIAL = "trivial"
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    INTRACTABLE = "intractable"
    UNDECIDABLE = "undecidable"

    @property
    def ordinal(self) -> int:
        """Return a numeric ordinal for comparison purposes.

        Returns:
            Integer from 0 (TRIVIAL) to 5 (UNDECIDABLE).
        """
        _ordinals: dict[DifficultyLevel, int] = {
            DifficultyLevel.TRIVIAL: 0,
            DifficultyLevel.EASY: 1,
            DifficultyLevel.MODERATE: 2,
            DifficultyLevel.HARD: 3,
            DifficultyLevel.INTRACTABLE: 4,
            DifficultyLevel.UNDECIDABLE: 5,
        }
        return _ordinals[self]

    def is_tractable(self) -> bool:
        """Return ``True`` when this difficulty level is considered tractable.

        Returns:
            True for TRIVIAL, EASY, and MODERATE difficulties.
        """
        return self.ordinal <= DifficultyLevel.MODERATE.ordinal

    def harder_than(self, other: "DifficultyLevel") -> bool:
        """Return ``True`` when this level is strictly harder than *other*.

        Args:
            other: The difficulty level to compare against.

        Returns:
            True when ``self.ordinal > other.ordinal``.
        """
        return self.ordinal > other.ordinal


class DecidabilityKind(str, Enum):
    """Decidability classification for a problem class.

    Reflects the computability-theoretic status of the problem.

    Attributes:
        DECIDABLE: There exists an algorithm that halts on all inputs.
        SEMI_DECIDABLE: An algorithm exists that halts on YES instances only.
        CO_SEMI_DECIDABLE: An algorithm exists that halts on NO instances only.
        UNDECIDABLE: No algorithm can decide all instances.
        OPEN: Decidability status is an open research question.
    """

    DECIDABLE = "decidable"
    SEMI_DECIDABLE = "semi_decidable"
    CO_SEMI_DECIDABLE = "co_semi_decidable"
    UNDECIDABLE = "undecidable"
    OPEN = "open"

    def admits_complete_algorithm(self) -> bool:
        """Return ``True`` when a complete decision algorithm provably exists.

        Returns:
            True only for DECIDABLE.
        """
        return self == DecidabilityKind.DECIDABLE

    def is_computable(self) -> bool:
        """Return ``True`` when at least partial computation is possible.

        Returns:
            True for DECIDABLE, SEMI_DECIDABLE, and CO_SEMI_DECIDABLE.
        """
        return self in {
            DecidabilityKind.DECIDABLE,
            DecidabilityKind.SEMI_DECIDABLE,
            DecidabilityKind.CO_SEMI_DECIDABLE,
        }


class ConjunctionMode(str, Enum):
    """How multiple evidence requirements are logically combined.

    Attributes:
        ALL: Every requirement must be satisfied (logical AND).
        ANY: At least one requirement must be satisfied (logical OR).
        MAJORITY: More than half must be satisfied.
        WEIGHTED: Weighted sum of satisfied requirements meets a threshold.
    """

    ALL = "all"
    ANY = "any"
    MAJORITY = "majority"
    WEIGHTED = "weighted"

    def is_conjunctive(self) -> bool:
        """Return ``True`` when mode requires universal satisfaction.

        Returns:
            True for ALL.
        """
        return self == ConjunctionMode.ALL


class ProblemKind(str, Enum):
    """Fine-grained kind classification within a problem category.

    These are the canonical named kinds that appear throughout the atlas and
    are referenced by subsumption relations.

    Attributes:
        SATISFIABILITY: Does a solution satisfying all constraints exist?
        OPTIMIZATION: Find the solution that maximises or minimises an objective.
        ENUMERATION: List all solutions satisfying given constraints.
        COUNTING: Count the number of solutions without listing them.
        DECISION: Decide a binary yes/no question about an instance.
        CONSTRUCTION: Build an artifact satisfying a specification.
        VERIFICATION: Certify that a given artifact satisfies a specification.
        SEARCH: Find any solution satisfying given constraints.
        INFERENCE: Derive conclusions from premises by logical rules.
        CLASSIFICATION: Assign an input to one of a fixed set of categories.
        SYNTHESIS: Produce a new artifact meeting a high-level specification.
        REPAIR: Modify a broken artifact to make it satisfy a specification.
    """

    SATISFIABILITY = "satisfiability"
    OPTIMIZATION = "optimization"
    ENUMERATION = "enumeration"
    COUNTING = "counting"
    DECISION = "decision"
    CONSTRUCTION = "construction"
    VERIFICATION = "verification"
    SEARCH = "search"
    INFERENCE = "inference"
    CLASSIFICATION = "classification"
    SYNTHESIS = "synthesis"
    REPAIR = "repair"

    def base_category(self) -> ProblemCategory:
        """Return the most natural problem category for this kind.

        Returns:
            The primary ProblemCategory for this kind.
        """
        _map: dict[ProblemKind, ProblemCategory] = {
            ProblemKind.SATISFIABILITY: ProblemCategory.COMPUTATIONAL,
            ProblemKind.OPTIMIZATION: ProblemCategory.COMPUTATIONAL,
            ProblemKind.ENUMERATION: ProblemCategory.COMPUTATIONAL,
            ProblemKind.COUNTING: ProblemCategory.COMPUTATIONAL,
            ProblemKind.DECISION: ProblemCategory.COMPUTATIONAL,
            ProblemKind.CONSTRUCTION: ProblemCategory.CONSTRUCTIVE,
            ProblemKind.VERIFICATION: ProblemCategory.VERIFICATION,
            ProblemKind.SEARCH: ProblemCategory.COMPUTATIONAL,
            ProblemKind.INFERENCE: ProblemCategory.ANALYTICAL,
            ProblemKind.CLASSIFICATION: ProblemCategory.ANALYTICAL,
            ProblemKind.SYNTHESIS: ProblemCategory.CONSTRUCTIVE,
            ProblemKind.REPAIR: ProblemCategory.CONSTRUCTIVE,
        }
        return _map[self]


# ═══════════════════════════════════════════════════════════════════════════
# §3  Core Dataclasses
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """Specification of the evidence needed to certify a problem class.

    An EvidenceRequirement records which evidence channels must be consulted,
    how multiple requirements are combined, and what the minimum trust level
    must be for each piece of evidence.

    Attributes:
        req_id: Unique identifier for this requirement.
        evidence_kinds: Tuple of string names of required evidence kinds.
        conjunction_mode: How the evidence_kinds are logically combined.
        min_trust_level: Minimum trust tier required (e.g., ``reviewed``).
        description: Human-readable description of what evidence is needed.
        optional: Whether this requirement may be waived under certain conditions.
    """

    req_id: str
    evidence_kinds: tuple[str, ...]
    conjunction_mode: ConjunctionMode
    min_trust_level: str
    description: str = ""
    optional: bool = False

    def is_satisfied_by(self, provided_kinds: frozenset[str]) -> bool:
        """Check if *provided_kinds* satisfies this requirement.

        Args:
            provided_kinds: The set of evidence kind names that have been provided.

        Returns:
            True when the requirement is met according to its conjunction_mode.
        """
        if not self.evidence_kinds:
            return True
        required = frozenset(self.evidence_kinds)
        overlap = required & provided_kinds
        if self.conjunction_mode == ConjunctionMode.ALL:
            return overlap == required
        if self.conjunction_mode == ConjunctionMode.ANY:
            return len(overlap) > 0
        if self.conjunction_mode == ConjunctionMode.MAJORITY:
            return len(overlap) > len(required) / 2
        # WEIGHTED: treat as ALL for default scoring
        return overlap == required

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with all fields serialized as primitives.
        """
        return {
            "req_id": self.req_id,
            "evidence_kinds": list(self.evidence_kinds),
            "conjunction_mode": self.conjunction_mode.value,
            "min_trust_level": self.min_trust_level,
            "description": self.description,
            "optional": self.optional,
        }


@dataclass(frozen=True, slots=True)
class ProblemClass:
    """Immutable record describing a single problem class in the atlas lattice.

    A ProblemClass node captures the formal classification of a computational
    or verification problem including its position in the subsumption lattice,
    its difficulty and decidability status, and its canonical instances.

    Attributes:
        class_id: Unique identifier (UUID string or canonical short name).
        name: Human-readable canonical name (e.g., ``SEARCH``, ``VERIFICATION``).
        description: Prose description of what this class covers.
        category: Broad ProblemCategory for this class.
        difficulty: Qualitative DifficultyLevel assessment.
        decidability: DecidabilityKind classification.
        parent_ids: IDs of immediate superclasses in the lattice.
        child_ids: IDs of immediate subclasses in the lattice.
        canonical_instances: JSON-encoded canonical problem instances.
        complexity_notes: Informal notes on complexity class membership.
        evidence_kinds: Evidence kinds required for certification.
        kind: Fine-grained ProblemKind classification.
        tags: Additional free-form classification tags.
    """

    class_id: str
    name: str
    description: str
    category: ProblemCategory
    difficulty: DifficultyLevel
    decidability: DecidabilityKind
    parent_ids: tuple[str, ...]
    child_ids: tuple[str, ...]
    canonical_instances: tuple[str, ...]
    complexity_notes: str
    evidence_kinds: tuple[str, ...]
    kind: ProblemKind | None = None
    tags: tuple[str, ...] = ()

    def is_root(self) -> bool:
        """Return ``True`` when this class has no parents in the lattice.

        Returns:
            True when ``parent_ids`` is empty.
        """
        return len(self.parent_ids) == 0

    def is_leaf(self) -> bool:
        """Return ``True`` when this class has no children in the lattice.

        Returns:
            True when ``child_ids`` is empty.
        """
        return len(self.child_ids) == 0

    def get_instances(self) -> list[dict[str, Any]]:
        """Deserialize and return all canonical instances.

        Returns:
            List of dicts; each dict is one canonical instance.

        Raises:
            json.JSONDecodeError: If any stored instance is malformed JSON.
        """
        return [json.loads(inst) for inst in self.canonical_instances]

    def add_child(self, child_id: str) -> "ProblemClass":
        """Return a new ProblemClass with *child_id* added to children.

        Args:
            child_id: The class_id of the new child class.

        Returns:
            Immutable copy with updated child_ids.
        """
        if child_id in self.child_ids:
            return self
        return replace(self, child_ids=self.child_ids + (child_id,))

    def add_parent(self, parent_id: str) -> "ProblemClass":
        """Return a new ProblemClass with *parent_id* added to parents.

        Args:
            parent_id: The class_id of the new parent class.

        Returns:
            Immutable copy with updated parent_ids.
        """
        if parent_id in self.parent_ids:
            return self
        return replace(self, parent_ids=self.parent_ids + (parent_id,))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with all fields serialized as JSON primitives.
        """
        return {
            "class_id": self.class_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "difficulty": self.difficulty.value,
            "decidability": self.decidability.value,
            "parent_ids": list(self.parent_ids),
            "child_ids": list(self.child_ids),
            "canonical_instances": list(self.canonical_instances),
            "complexity_notes": self.complexity_notes,
            "evidence_kinds": list(self.evidence_kinds),
            "kind": self.kind.value if self.kind else None,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProblemClass":
        """Deserialize from a JSON-safe dictionary.

        Args:
            data: Dict previously produced by ``to_dict()``.

        Returns:
            Reconstructed ProblemClass instance.

        Raises:
            KeyError: If required fields are missing from *data*.
            ValueError: If enum values are unrecognised.
        """
        return cls(
            class_id=data["class_id"],
            name=data["name"],
            description=data["description"],
            category=ProblemCategory(data["category"]),
            difficulty=DifficultyLevel(data["difficulty"]),
            decidability=DecidabilityKind(data["decidability"]),
            parent_ids=tuple(data.get("parent_ids", [])),
            child_ids=tuple(data.get("child_ids", [])),
            canonical_instances=tuple(data.get("canonical_instances", [])),
            complexity_notes=data.get("complexity_notes", ""),
            evidence_kinds=tuple(data.get("evidence_kinds", [])),
            kind=ProblemKind(data["kind"]) if data.get("kind") else None,
            tags=tuple(data.get("tags", [])),
        )


@dataclass(frozen=True, slots=True)
class SubsumptionRelation:
    """Records that one problem class subsumes another in the lattice.

    A class A *subsumes* class B when every instance of B is also a valid
    instance of A.  This is the canonical ``≼`` relation in Theory2.tex §14.1.4.

    Attributes:
        superclass_id: The class_id of the subsuming (parent) class.
        subclass_id: The class_id of the subsumed (child) class.
        relation_kind: Either ``direct`` (immediate edge) or ``transitive``.
        evidence: Informal justification for this subsumption.
        is_strict: Whether the relation is strict (A ≺ B, not A = B).
    """

    superclass_id: str
    subclass_id: str
    relation_kind: str = "direct"
    evidence: str = ""
    is_strict: bool = True

    def is_direct(self) -> bool:
        """Return ``True`` when this is a direct (non-transitive) relation.

        Returns:
            True when ``relation_kind == 'direct'``.
        """
        return self.relation_kind == "direct"

    def reversed(self) -> "SubsumptionRelation":
        """Return the reversed relation (subclass becomes superclass).

        Returns:
            A new SubsumptionRelation with super/sub roles swapped.
        """
        return replace(
            self,
            superclass_id=self.subclass_id,
            subclass_id=self.superclass_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with all fields as primitives.
        """
        return {
            "superclass_id": self.superclass_id,
            "subclass_id": self.subclass_id,
            "relation_kind": self.relation_kind,
            "evidence": self.evidence,
            "is_strict": self.is_strict,
        }


@dataclass(frozen=True, slots=True)
class ProblemClassLattice:
    """Immutable snapshot of the problem class lattice structure.

    Encapsulates the topological ordering and ancestor/descendant maps
    computed over a registry of problem classes.  Used as the output of
    ``ClassLatticeComputer`` computations.

    Attributes:
        classes: Mapping from class_id to ProblemClass.
        topological_order: Class IDs in a valid topological order (parents first).
        ancestor_map: Maps each class_id to the frozenset of all its ancestors.
        descendant_map: Maps each class_id to the frozenset of all its descendants.
        top_classes: Class IDs that have no parents (lattice tops).
        bottom_classes: Class IDs that have no children (lattice bottoms).
        subsumptions: All subsumption relations (direct and transitive).
    """

    classes: Mapping[str, ProblemClass]
    topological_order: tuple[str, ...]
    ancestor_map: Mapping[str, frozenset[str]]
    descendant_map: Mapping[str, frozenset[str]]
    top_classes: tuple[str, ...]
    bottom_classes: tuple[str, ...]
    subsumptions: tuple[SubsumptionRelation, ...] = ()

    def get_class(self, class_id: ClassId) -> ProblemClass | None:
        """Look up a class by its ID.

        Args:
            class_id: The class_id to look up.

        Returns:
            The ProblemClass with that ID, or None if not found.
        """
        return self.classes.get(class_id)

    def subsumes(self, superclass_id: ClassId, subclass_id: ClassId) -> bool:
        """Check whether *superclass_id* subsumes *subclass_id*.

        Args:
            superclass_id: The putative superclass.
            subclass_id: The putative subclass.

        Returns:
            True when subclass_id is in the descendant set of superclass_id.
        """
        return subclass_id in self.descendant_map.get(superclass_id, frozenset())

    def depth_of(self, class_id: ClassId) -> int:
        """Return the topological depth of *class_id* (distance from a top).

        Args:
            class_id: The class to query.

        Returns:
            Integer depth; 0 for top classes, -1 if class_id not found.
        """
        if class_id not in self.classes:
            return -1
        pos = self.topological_order.index(class_id) if class_id in self.topological_order else -1
        return pos

    def to_adjacency_list(self) -> dict[str, list[str]]:
        """Return the lattice as an adjacency list (parent → children).

        Returns:
            Dict mapping each class_id to a list of its child class_ids.
        """
        return {cid: list(pc.child_ids) for cid, pc in self.classes.items()}


# ═══════════════════════════════════════════════════════════════════════════
# §4  InstanceTemplate — canonical problem instance schema
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class InstanceTemplate:
    """Template for generating canonical problem instances.

    An InstanceTemplate describes the structural shape of a problem instance
    for a given problem class.  Bindings are applied via simple ``{key}``
    substitution in string-valued template fields.

    Attributes:
        template_id: Unique identifier for this template.
        class_name: Name of the problem class this template belongs to.
        description: Prose description of what this template encodes.
        input_template: Dict mapping field names to value templates.
        output_template: Dict mapping field names to expected output shape.
        constraints: Tuple of constraint strings that instances must satisfy.
        tags: Free-form classification tags.
    """

    template_id: str
    class_name: str
    description: str
    input_template: Mapping[str, Any]
    output_template: Mapping[str, Any]
    constraints: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def instantiate(self, bindings: dict[str, Any]) -> dict[str, Any]:
        """Instantiate this template by substituting *bindings*.

        String values in ``input_template`` of the form ``{key}`` are replaced
        with the corresponding value from *bindings*.  Non-string values are
        passed through unchanged.

        Args:
            bindings: Mapping from variable names to their concrete values.

        Returns:
            Dict combining the instantiated input fields and output template.
        """
        result: dict[str, Any] = {}
        for key, value in self.input_template.items():
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                var_name = value[1:-1]
                result[key] = bindings.get(var_name, value)
            else:
                result[key] = value
        for key, value in self.output_template.items():
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                var_name = value[1:-1]
                result[key] = bindings.get(var_name, value)
            else:
                result[key] = value
        return result

    def validate(self, instance: dict[str, Any]) -> list[str]:
        """Validate that *instance* conforms to this template.

        Checks that all required input_template keys are present and that the
        instance satisfies each constraint string (simple key-presence checks).

        Args:
            instance: A candidate problem instance dict.

        Returns:
            List of validation error messages; empty list means valid.
        """
        errors: list[str] = []
        for key in self.input_template:
            if key not in instance:
                errors.append(f"Missing required field: '{key}'")
        for constraint in self.constraints:
            # Constraint is either "key_present:fieldname" or a bare field name
            if constraint.startswith("key_present:"):
                field_name = constraint.split(":", 1)[1]
                if field_name not in instance:
                    errors.append(f"Constraint violated: required field '{field_name}' absent")
            elif constraint.startswith("non_empty:"):
                field_name = constraint.split(":", 1)[1]
                val = instance.get(field_name)
                if not val:
                    errors.append(f"Constraint violated: field '{field_name}' must be non-empty")
            elif constraint.startswith("positive:"):
                field_name = constraint.split(":", 1)[1]
                val = instance.get(field_name)
                if not (isinstance(val, (int, float)) and val > 0):
                    errors.append(f"Constraint violated: field '{field_name}' must be positive")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with all fields serialized as JSON primitives.
        """
        return {
            "template_id": self.template_id,
            "class_name": self.class_name,
            "description": self.description,
            "input_template": dict(self.input_template),
            "output_template": dict(self.output_template),
            "constraints": list(self.constraints),
            "tags": list(self.tags),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §5  ProblemClassBuilder — fluent builder for ProblemClass instances
# ═══════════════════════════════════════════════════════════════════════════


class ProblemClassBuilder:
    """Fluent builder for constructing ``ProblemClass`` instances.

    Provides a chainable API for progressively specifying all attributes of a
    problem class before calling ``build()`` to produce the immutable record.

    Example::

        pc = (
            ProblemClassBuilder("SEARCH")
            .with_description("Find an item satisfying a predicate.")
            .with_category(ProblemCategory.COMPUTATIONAL)
            .with_difficulty(DifficultyLevel.MODERATE)
            .with_canonical_instance({"items": [1, 2, 3], "predicate": "x > 1"})
            .with_complexity_notes("Ω(n) lower bound; O(n log n) with sorting.")
            .build()
        )
    """

    def __init__(self, name: str) -> None:
        """Initialise the builder with the problem class name.

        Args:
            name: The canonical name for this problem class (e.g., ``SEARCH``).
        """
        self._class_id: str = str(uuid.uuid4())
        self._name: str = name
        self._description: str = ""
        self._category: ProblemCategory = ProblemCategory.COMPUTATIONAL
        self._difficulty: DifficultyLevel = DifficultyLevel.MODERATE
        self._decidability: DecidabilityKind = DecidabilityKind.DECIDABLE
        self._parents: list[str] = []
        self._children: list[str] = []
        self._instances: list[str] = []
        self._complexity_notes: str = ""
        self._evidence_kinds: list[str] = []
        self._kind: ProblemKind | None = None
        self._tags: list[str] = []

    def with_class_id(self, class_id: str) -> "ProblemClassBuilder":
        """Override the auto-generated class ID.

        Args:
            class_id: The desired class_id string.

        Returns:
            ``self`` for method chaining.
        """
        self._class_id = class_id
        return self

    def with_description(self, description: str) -> "ProblemClassBuilder":
        """Set the human-readable description.

        Args:
            description: Prose explanation of this problem class.

        Returns:
            ``self`` for method chaining.
        """
        self._description = description
        return self

    def with_category(self, category: ProblemCategory) -> "ProblemClassBuilder":
        """Set the broad problem category.

        Args:
            category: The ProblemCategory for this class.

        Returns:
            ``self`` for method chaining.
        """
        self._category = category
        return self

    def with_difficulty(self, difficulty: DifficultyLevel) -> "ProblemClassBuilder":
        """Set the qualitative difficulty level.

        Args:
            difficulty: The DifficultyLevel for this class.

        Returns:
            ``self`` for method chaining.
        """
        self._difficulty = difficulty
        return self

    def with_decidability(self, decidability: DecidabilityKind) -> "ProblemClassBuilder":
        """Set the decidability classification.

        Args:
            decidability: The DecidabilityKind for this class.

        Returns:
            ``self`` for method chaining.
        """
        self._decidability = decidability
        return self

    def with_parent(self, parent_id: str) -> "ProblemClassBuilder":
        """Add a parent class ID to the lattice parents list.

        Args:
            parent_id: The class_id of an immediate superclass.

        Returns:
            ``self`` for method chaining.
        """
        if parent_id not in self._parents:
            self._parents.append(parent_id)
        return self

    def with_child(self, child_id: str) -> "ProblemClassBuilder":
        """Add a child class ID to the lattice children list.

        Args:
            child_id: The class_id of an immediate subclass.

        Returns:
            ``self`` for method chaining.
        """
        if child_id not in self._children:
            self._children.append(child_id)
        return self

    def with_canonical_instance(self, instance: dict[str, Any]) -> "ProblemClassBuilder":
        """Add a canonical problem instance (serialised to JSON).

        Args:
            instance: A dict representing a canonical problem instance.

        Returns:
            ``self`` for method chaining.
        """
        self._instances.append(json.dumps(instance, sort_keys=True))
        return self

    def with_complexity_notes(self, notes: str) -> "ProblemClassBuilder":
        """Set the informal complexity notes string.

        Args:
            notes: Free-text notes on complexity class membership.

        Returns:
            ``self`` for method chaining.
        """
        self._complexity_notes = notes
        return self

    def with_evidence_kind(self, kind: str) -> "ProblemClassBuilder":
        """Add an evidence kind required for this class.

        Args:
            kind: String name of an evidence kind (e.g., ``solver``, ``proof``).

        Returns:
            ``self`` for method chaining.
        """
        if kind not in self._evidence_kinds:
            self._evidence_kinds.append(kind)
        return self

    def with_kind(self, kind: ProblemKind) -> "ProblemClassBuilder":
        """Set the fine-grained ProblemKind classification.

        Args:
            kind: The ProblemKind for this class.

        Returns:
            ``self`` for method chaining.
        """
        self._kind = kind
        return self

    def with_tag(self, tag: str) -> "ProblemClassBuilder":
        """Add a free-form classification tag.

        Args:
            tag: A tag string.

        Returns:
            ``self`` for method chaining.
        """
        if tag not in self._tags:
            self._tags.append(tag)
        return self

    def build(self) -> ProblemClass:
        """Validate accumulated state and construct the immutable ProblemClass.

        Returns:
            A fully constructed, frozen ProblemClass instance.

        Raises:
            ValueError: When the name is empty or required fields are invalid.
        """
        if not self._name.strip():
            raise ValueError("ProblemClass name must be non-empty.")
        if not self._description:
            self._description = f"Problem class '{self._name}'."
        return ProblemClass(
            class_id=self._class_id,
            name=self._name,
            description=self._description,
            category=self._category,
            difficulty=self._difficulty,
            decidability=self._decidability,
            parent_ids=tuple(self._parents),
            child_ids=tuple(self._children),
            canonical_instances=tuple(self._instances),
            complexity_notes=self._complexity_notes,
            evidence_kinds=tuple(self._evidence_kinds),
            kind=self._kind,
            tags=tuple(self._tags),
        )


# ═══════════════════════════════════════════════════════════════════════════
# §6  ClassLatticeComputer — lattice structure computation
# ═══════════════════════════════════════════════════════════════════════════


class ClassLatticeComputer:
    """Computes structural properties of the problem class lattice.

    Given a registry dict mapping class_id → ProblemClass, this computer
    provides operations for topological sorting, ancestor/descendant queries,
    LUB/GLB computation, and consistency checking.

    The lattice is assumed to be a DAG where parent_ids point upward
    (toward more general classes) and child_ids point downward.

    Args:
        registry: Dict mapping class_id strings to ProblemClass instances.
    """

    def __init__(self, registry: dict[str, ProblemClass]) -> None:
        """Initialise with a snapshot of the class registry.

        Args:
            registry: Mapping from class_id to ProblemClass.
        """
        self._registry: dict[str, ProblemClass] = dict(registry)

    def compute_topological_order(self) -> list[str]:
        """Compute a topological ordering of all classes (parents before children).

        Uses Kahn's algorithm on the parent→child edges.  Classes with no parents
        appear first; leaves appear last.

        Returns:
            List of class_ids in topological order.  If the graph has a cycle
            the returned list will be shorter than the total number of classes.
        """
        # Build in-degree map counting how many parents each class has
        in_degree: dict[str, int] = {cid: 0 for cid in self._registry}
        for cid, pc in self._registry.items():
            for child_id in pc.child_ids:
                if child_id in in_degree:
                    in_degree[child_id] += 1

        queue: deque[str] = deque(
            cid for cid, deg in in_degree.items() if deg == 0
        )
        order: list[str] = []

        while queue:
            cid = queue.popleft()
            order.append(cid)
            pc = self._registry[cid]
            for child_id in pc.child_ids:
                if child_id in in_degree:
                    in_degree[child_id] -= 1
                    if in_degree[child_id] == 0:
                        queue.append(child_id)

        return order

    def compute_ancestors(self, class_id: ClassId) -> set[str]:
        """Compute all ancestors of *class_id* via BFS upward through parents.

        Args:
            class_id: The starting class whose ancestors are computed.

        Returns:
            Set of class_ids that are (strict) ancestors of *class_id*.
            Returns empty set if *class_id* is not in the registry.
        """
        if class_id not in self._registry:
            return set()
        visited: set[str] = set()
        queue: deque[str] = deque(self._registry[class_id].parent_ids)
        while queue:
            cid = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            pc = self._registry.get(cid)
            if pc is not None:
                queue.extend(pc.parent_ids)
        return visited

    def compute_descendants(self, class_id: ClassId) -> set[str]:
        """Compute all descendants of *class_id* via BFS downward through children.

        Args:
            class_id: The starting class whose descendants are computed.

        Returns:
            Set of class_ids that are (strict) descendants of *class_id*.
            Returns empty set if *class_id* is not in the registry.
        """
        if class_id not in self._registry:
            return set()
        visited: set[str] = set()
        queue: deque[str] = deque(self._registry[class_id].child_ids)
        while queue:
            cid = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            pc = self._registry.get(cid)
            if pc is not None:
                queue.extend(pc.child_ids)
        return visited

    def compute_least_upper_bound(
        self, id_a: ClassId, id_b: ClassId
    ) -> ClassId | None:
        """Find the least upper bound (join) of two classes in the lattice.

        The LUB of A and B is the lowest class that is an ancestor (or equal to)
        both A and B.  Returns ``None`` when no common ancestor exists.

        Args:
            id_a: First class ID.
            id_b: Second class ID.

        Returns:
            class_id of the LUB, or None if no common ancestor is found.
        """
        ancestors_a = self.compute_ancestors(id_a) | {id_a}
        ancestors_b = self.compute_ancestors(id_b) | {id_b}
        common = ancestors_a & ancestors_b
        if not common:
            return None
        # The LUB is the common ancestor with the greatest depth (closest to A and B)
        topo = self.compute_topological_order()
        # Reverse so that the last (deepest) element in topo order is first
        for cid in reversed(topo):
            if cid in common:
                return cid
        return next(iter(common), None)

    def compute_greatest_lower_bound(
        self, id_a: ClassId, id_b: ClassId
    ) -> ClassId | None:
        """Find the greatest lower bound (meet) of two classes in the lattice.

        The GLB of A and B is the highest class that is a descendant (or equal to)
        both A and B.  Returns ``None`` when no common descendant exists.

        Args:
            id_a: First class ID.
            id_b: Second class ID.

        Returns:
            class_id of the GLB, or None if no common descendant is found.
        """
        desc_a = self.compute_descendants(id_a) | {id_a}
        desc_b = self.compute_descendants(id_b) | {id_b}
        common = desc_a & desc_b
        if not common:
            return None
        topo = self.compute_topological_order()
        for cid in topo:
            if cid in common:
                return cid
        return next(iter(common), None)

    def is_consistent(self) -> bool:
        """Check for cycles and dangling references in the lattice.

        A consistent lattice has no cycles (it is a DAG) and every parent_id
        and child_id referenced in a class also exists as a class_id.

        Returns:
            True when the lattice is a valid DAG with no dangling references.
        """
        # Check for dangling references
        all_ids = set(self._registry.keys())
        for cid, pc in self._registry.items():
            for pid in pc.parent_ids:
                if pid not in all_ids:
                    return False
            for kid in pc.child_ids:
                if kid not in all_ids:
                    return False
        # Check for cycles: topological order should include all classes
        topo = self.compute_topological_order()
        return len(topo) == len(self._registry)

    def get_top_classes(self) -> list[ClassId]:
        """Return all classes with no parents (lattice tops).

        Returns:
            List of class_ids for root classes.
        """
        return [cid for cid, pc in self._registry.items() if pc.is_root()]

    def get_bottom_classes(self) -> list[ClassId]:
        """Return all classes with no children (lattice bottoms/leaves).

        Returns:
            List of class_ids for leaf classes.
        """
        return [cid for cid, pc in self._registry.items() if pc.is_leaf()]

    def compute_lattice_depth(self, class_id: ClassId) -> int:
        """Compute the maximum depth of *class_id* from any top class.

        Depth 0 means *class_id* is a root.  Depth k means there is a path
        of length k from some root to *class_id*.

        Args:
            class_id: The class whose depth is computed.

        Returns:
            Integer depth; -1 if *class_id* is not in the registry.
        """
        if class_id not in self._registry:
            return -1
        # BFS from class upward, tracking path length
        max_depth: int = 0
        # Use DFS with memoization
        memo: dict[str, int] = {}

        def _depth(cid: str) -> int:
            if cid in memo:
                return memo[cid]
            pc = self._registry.get(cid)
            if pc is None or not pc.parent_ids:
                memo[cid] = 0
                return 0
            d = 1 + max(_depth(pid) for pid in pc.parent_ids if pid in self._registry)
            memo[cid] = d
            return d

        return _depth(class_id)

    def compute_all_subsumptions(self) -> list[SubsumptionRelation]:
        """Compute all direct and transitive subsumption relations.

        Returns:
            List of SubsumptionRelation instances covering every ancestor-descendant
            pair in the lattice.
        """
        relations: list[SubsumptionRelation] = []
        for cid in self._registry:
            ancestors = self.compute_ancestors(cid)
            for anc in ancestors:
                is_direct = cid in self._registry.get(anc, ProblemClass(
                    class_id="", name="", description="", category=ProblemCategory.COMPUTATIONAL,
                    difficulty=DifficultyLevel.MODERATE, decidability=DecidabilityKind.DECIDABLE,
                    parent_ids=(), child_ids=(), canonical_instances=(),
                    complexity_notes="", evidence_kinds=(),
                )).child_ids
                relations.append(SubsumptionRelation(
                    superclass_id=anc,
                    subclass_id=cid,
                    relation_kind="direct" if is_direct else "transitive",
                ))
        return relations

    def build_lattice(self) -> ProblemClassLattice:
        """Build a fully computed ProblemClassLattice snapshot.

        Returns:
            ProblemClassLattice with all structural data computed.
        """
        topo = self.compute_topological_order()
        ancestor_map: dict[str, frozenset[str]] = {
            cid: frozenset(self.compute_ancestors(cid)) for cid in self._registry
        }
        descendant_map: dict[str, frozenset[str]] = {
            cid: frozenset(self.compute_descendants(cid)) for cid in self._registry
        }
        tops = tuple(self.get_top_classes())
        bottoms = tuple(self.get_bottom_classes())
        subsumptions = tuple(self.compute_all_subsumptions())
        return ProblemClassLattice(
            classes=dict(self._registry),
            topological_order=tuple(topo),
            ancestor_map=ancestor_map,
            descendant_map=descendant_map,
            top_classes=tops,
            bottom_classes=bottoms,
            subsumptions=subsumptions,
        )


# ═══════════════════════════════════════════════════════════════════════════
# §7  ProblemClassRegistry — global singleton registry
# ═══════════════════════════════════════════════════════════════════════════

_GLOBAL_REGISTRY: "ProblemClassRegistry | None" = None


class ProblemClassRegistry:
    """Global registry of all known problem classes.

    The registry maintains an index by class_id and by name, and exposes
    methods for registration, lookup, and lattice construction.

    A global singleton is available via ``ProblemClassRegistry.global_registry()``.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._by_id: dict[str, ProblemClass] = {}
        self._by_name: dict[str, ProblemClass] = {}

    def register(self, pc: ProblemClass) -> None:
        """Add *pc* to the registry.

        If a class with the same ``class_id`` is already present it is silently
        replaced.

        Args:
            pc: The ProblemClass to register.

        Raises:
            TypeError: If *pc* is not a ProblemClass instance.
        """
        if not isinstance(pc, ProblemClass):
            raise TypeError(f"Expected ProblemClass, got {type(pc).__name__}")
        self._by_id[pc.class_id] = pc
        self._by_name[pc.name] = pc

    def unregister(self, class_id: ClassId) -> None:
        """Remove the class with *class_id* from the registry.

        Args:
            class_id: The class_id of the class to remove.

        Raises:
            KeyError: If *class_id* is not present.
        """
        pc = self._by_id.pop(class_id)
        self._by_name.pop(pc.name, None)

    def get(self, class_id: ClassId) -> ProblemClass | None:
        """Look up a class by its unique ID.

        Args:
            class_id: The class_id to search for.

        Returns:
            The ProblemClass if found, else None.
        """
        return self._by_id.get(class_id)

    def get_by_name(self, name: ClassName) -> ProblemClass | None:
        """Look up a class by its canonical name.

        Args:
            name: The name string to search for (case-sensitive).

        Returns:
            The ProblemClass with that name, or None.
        """
        return self._by_name.get(name)

    def get_by_category(self, category: ProblemCategory) -> list[ProblemClass]:
        """Return all classes belonging to *category*.

        Args:
            category: The ProblemCategory to filter by.

        Returns:
            Sorted list (by name) of matching ProblemClass instances.
        """
        return sorted(
            (pc for pc in self._by_id.values() if pc.category == category),
            key=lambda pc: pc.name,
        )

    def list_all(self) -> list[ProblemClass]:
        """Return all registered classes sorted by name.

        Returns:
            List of all ProblemClass instances in alphabetical name order.
        """
        return sorted(self._by_id.values(), key=lambda pc: pc.name)

    def count(self) -> int:
        """Return the number of registered classes.

        Returns:
            Integer count of classes in the registry.
        """
        return len(self._by_id)

    def build_lattice_computer(self) -> ClassLatticeComputer:
        """Build a ClassLatticeComputer from the current registry snapshot.

        Returns:
            ClassLatticeComputer backed by the current registry contents.
        """
        return ClassLatticeComputer(dict(self._by_id))

    def validate(self) -> list[str]:
        """Validate the registry for consistency errors.

        Checks:
        - All parent_ids and child_ids reference existing classes.
        - No cycles exist in the lattice.
        - Every class has a non-empty name and description.

        Returns:
            List of human-readable error message strings; empty list is valid.
        """
        errors: list[str] = []
        all_ids = set(self._by_id.keys())
        for cid, pc in self._by_id.items():
            if not pc.name.strip():
                errors.append(f"Class '{cid}' has empty name.")
            if not pc.description.strip():
                errors.append(f"Class '{cid}' ({pc.name}) has empty description.")
            for pid in pc.parent_ids:
                if pid not in all_ids:
                    errors.append(
                        f"Class '{pc.name}' references unknown parent '{pid}'."
                    )
            for kid in pc.child_ids:
                if kid not in all_ids:
                    errors.append(
                        f"Class '{pc.name}' references unknown child '{kid}'."
                    )
        computer = self.build_lattice_computer()
        if not computer.is_consistent():
            errors.append("Registry lattice contains cycles or dangling references.")
        return errors

    def iter_classes(self) -> Iterator[ProblemClass]:
        """Iterate over all registered classes in registration order.

        Yields:
            ProblemClass instances one at a time.
        """
        yield from self._by_id.values()

    @classmethod
    def global_registry(cls) -> "ProblemClassRegistry":
        """Return the process-wide global registry singleton.

        The singleton is lazily initialised on first access and pre-populated
        with ``STANDARD_PROBLEM_CLASSES`` on first construction.

        Returns:
            The global ProblemClassRegistry instance.
        """
        global _GLOBAL_REGISTRY
        if _GLOBAL_REGISTRY is None:
            _GLOBAL_REGISTRY = cls()
            for pc in STANDARD_PROBLEM_CLASSES.values():
                _GLOBAL_REGISTRY.register(pc)
        return _GLOBAL_REGISTRY


# ═══════════════════════════════════════════════════════════════════════════
# §8  InstanceGenerator — canonical instance generation
# ═══════════════════════════════════════════════════════════════════════════


class InstanceGenerator:
    """Generates canonical problem instances from registered templates.

    Templates are looked up by class_name.  ``generate()`` applies provided
    keyword bindings to the template to produce a concrete instance dict.

    Args:
        templates: Optional initial dict of template_id → InstanceTemplate.
    """

    def __init__(self, templates: dict[str, InstanceTemplate] | None = None) -> None:
        """Initialise with an optional set of pre-registered templates.

        Args:
            templates: Dict mapping template_id to InstanceTemplate.  If None
                an empty registry is created.
        """
        self._templates: dict[str, InstanceTemplate] = {}
        self._by_class: dict[str, list[InstanceTemplate]] = defaultdict(list)
        if templates:
            for tmpl in templates.values():
                self.register_template(tmpl)

    def register_template(self, template: InstanceTemplate) -> None:
        """Register *template* for use by ``generate()``.

        Args:
            template: The InstanceTemplate to register.
        """
        self._templates[template.template_id] = template
        self._by_class[template.class_name].append(template)

    def generate(self, class_name: str, **bindings: Any) -> dict[str, Any]:
        """Generate a single instance for *class_name* using *bindings*.

        Picks the first registered template for *class_name* and instantiates it
        with the provided bindings.

        Args:
            class_name: The canonical problem class name to generate for.
            **bindings: Keyword arguments supplying template variable values.

        Returns:
            Dict representing the generated problem instance.

        Raises:
            KeyError: If no template is registered for *class_name*.
        """
        templates = self._by_class.get(class_name)
        if not templates:
            raise KeyError(
                f"No template registered for class '{class_name}'. "
                f"Available: {list(self._by_class.keys())}"
            )
        return templates[0].instantiate(dict(bindings))

    def generate_batch(self, class_name: str, count: int) -> list[dict[str, Any]]:
        """Generate *count* distinct instances for *class_name*.

        Uses all registered templates in round-robin order, cycling through them
        when more instances are requested than there are templates.

        Args:
            class_name: The canonical problem class name to generate for.
            count: Number of instances to produce.

        Returns:
            List of dicts, each representing a distinct generated instance.

        Raises:
            KeyError: If no template is registered for *class_name*.
            ValueError: If *count* is non-positive.
        """
        if count <= 0:
            raise ValueError(f"count must be positive; got {count}")
        templates = self._by_class.get(class_name)
        if not templates:
            raise KeyError(f"No template registered for class '{class_name}'.")
        results: list[dict[str, Any]] = []
        for i in range(count):
            tmpl = templates[i % len(templates)]
            # Provide synthetic bindings to vary instances
            instance = tmpl.instantiate({"index": i, "batch_id": str(uuid.uuid4())[:8]})
            results.append(instance)
        return results

    def validate_instance(
        self, class_name: str, instance: dict[str, Any]
    ) -> list[str]:
        """Validate *instance* against the templates for *class_name*.

        Runs validation against all registered templates for the class and
        returns the union of all constraint violations.

        Args:
            class_name: The problem class name.
            instance: The candidate instance dict.

        Returns:
            List of validation error strings; empty list means valid.
        """
        templates = self._by_class.get(class_name, [])
        if not templates:
            return [f"No template registered for class '{class_name}'."]
        errors: list[str] = []
        for tmpl in templates:
            errors.extend(tmpl.validate(instance))
        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                deduped.append(e)
        return deduped

    def list_templates(self) -> list[str]:
        """Return the list of class names for which templates are registered.

        Returns:
            Sorted list of class name strings.
        """
        return sorted(self._by_class.keys())


# ═══════════════════════════════════════════════════════════════════════════
# §9  ProblemClassSerializer — JSON serialization
# ═══════════════════════════════════════════════════════════════════════════

_REQUIRED_CLASS_KEYS: frozenset[str] = frozenset({
    "class_id", "name", "description", "category",
    "difficulty", "decidability", "parent_ids", "child_ids",
})


class ProblemClassSerializer:
    """Static utility class for JSON serialization of problem classes.

    All methods are static or class methods; no instance state is used.
    Provides round-trip serialization for individual ProblemClass instances
    and entire ProblemClassRegistry objects.
    """

    @staticmethod
    def serialize(pc: ProblemClass) -> dict[str, Any]:
        """Serialize a ProblemClass to a JSON-safe dictionary.

        Args:
            pc: The ProblemClass to serialize.

        Returns:
            Dict with all fields as JSON primitives (strings, lists, bools).
        """
        return pc.to_dict()

    @staticmethod
    def deserialize(data: dict[str, Any]) -> ProblemClass:
        """Deserialize a ProblemClass from a JSON-safe dictionary.

        Args:
            data: Dict previously produced by ``serialize()``.

        Returns:
            Reconstructed ProblemClass instance.

        Raises:
            KeyError: If required fields are missing from *data*.
            ValueError: If enum values are unrecognised.
        """
        errors = ProblemClassSerializer.validate_schema(data)
        if errors:
            raise ValueError(f"Schema validation failed: {'; '.join(errors)}")
        return ProblemClass.from_dict(data)

    @staticmethod
    def serialize_registry(registry: ProblemClassRegistry) -> str:
        """Serialize an entire registry to a JSON string.

        Args:
            registry: The ProblemClassRegistry to serialize.

        Returns:
            JSON string encoding all registered problem classes.
        """
        classes = [pc.to_dict() for pc in registry.list_all()]
        payload: dict[str, Any] = {
            "version": "1.0",
            "count": len(classes),
            "classes": classes,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    @staticmethod
    def deserialize_registry(json_str: str) -> ProblemClassRegistry:
        """Deserialize a registry from a JSON string.

        Args:
            json_str: JSON string previously produced by ``serialize_registry()``.

        Returns:
            ProblemClassRegistry populated with all classes from the JSON.

        Raises:
            json.JSONDecodeError: If *json_str* is not valid JSON.
            KeyError: If required class fields are missing.
        """
        payload = json.loads(json_str)
        registry = ProblemClassRegistry()
        for class_data in payload.get("classes", []):
            pc = ProblemClass.from_dict(class_data)
            registry.register(pc)
        return registry

    @staticmethod
    def validate_schema(data: dict[str, Any]) -> list[str]:
        """Validate that *data* has the required keys for a ProblemClass.

        Args:
            data: Candidate dict to validate.

        Returns:
            List of error messages; empty list means the schema is valid.
        """
        errors: list[str] = []
        for key in _REQUIRED_CLASS_KEYS:
            if key not in data:
                errors.append(f"Missing required key: '{key}'")
        category = data.get("category")
        if category and category not in {c.value for c in ProblemCategory}:
            errors.append(f"Unknown category value: '{category}'")
        difficulty = data.get("difficulty")
        if difficulty and difficulty not in {d.value for d in DifficultyLevel}:
            errors.append(f"Unknown difficulty value: '{difficulty}'")
        decidability = data.get("decidability")
        if decidability and decidability not in {d.value for d in DecidabilityKind}:
            errors.append(f"Unknown decidability value: '{decidability}'")
        return errors


# ═══════════════════════════════════════════════════════════════════════════
# §10  STANDARD_PROBLEM_CLASSES — pre-built class catalogue
# ═══════════════════════════════════════════════════════════════════════════

def _build_standard_classes() -> dict[str, ProblemClass]:
    """Build and return the dict of standard problem classes.

    Returns:
        Dict mapping canonical name to ProblemClass for all standard classes.
    """
    search = (
        ProblemClassBuilder("SEARCH")
        .with_class_id("SEARCH")
        .with_description(
            "Find an element or structure satisfying a given predicate. "
            "The prototypical SEARCH problem asks: given a space and a predicate, "
            "return any element of the space for which the predicate holds."
        )
        .with_category(ProblemCategory.COMPUTATIONAL)
        .with_difficulty(DifficultyLevel.MODERATE)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_child("OPTIMIZATION")
        .with_child("ENUMERATION")
        .with_child("COUNTING")
        .with_kind(ProblemKind.SEARCH)
        .with_canonical_instance({"items": [3, 1, 4, 1, 5], "predicate": "x > 3", "expected": 4})
        .with_canonical_instance({"items": ["apple", "pear", "plum"], "predicate": "starts_with('p')", "expected": "pear"})
        .with_complexity_notes(
            "General SEARCH is Ω(n) in the worst case for unsorted inputs. "
            "With sorted input and comparison queries, O(log n) binary search applies."
        )
        .with_evidence_kind("solver")
        .with_evidence_kind("runtime")
        .with_tag("core")
        .build()
    )

    optimization = (
        ProblemClassBuilder("OPTIMIZATION")
        .with_class_id("OPTIMIZATION")
        .with_description(
            "Find the element of a solution space that extremises a given objective "
            "function, subject to feasibility constraints. Subsumes SEARCH as every "
            "optimal solution is also a feasible one."
        )
        .with_category(ProblemCategory.COMPUTATIONAL)
        .with_difficulty(DifficultyLevel.HARD)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_parent("SEARCH")
        .with_kind(ProblemKind.OPTIMIZATION)
        .with_canonical_instance({
            "objective": "minimize sum(x)",
            "constraints": ["x[i] >= 0", "sum(x) <= 10"],
            "domain": "integers",
        })
        .with_canonical_instance({
            "objective": "maximize profit(x)",
            "constraints": ["weight(x) <= capacity"],
            "domain": "0-1 knapsack",
        })
        .with_complexity_notes(
            "NP-hard in general (integer programming). Convex continuous "
            "optimization is polynomial. Approximation algorithms (FPTAS) exist "
            "for many instances."
        )
        .with_evidence_kind("solver")
        .with_evidence_kind("oracle")
        .with_tag("core")
        .build()
    )

    decision = (
        ProblemClassBuilder("DECISION")
        .with_class_id("DECISION")
        .with_description(
            "Decide a binary yes/no question about a given instance. "
            "Decision problems are the canonical objects of study in classical "
            "complexity theory and correspond to language membership questions."
        )
        .with_category(ProblemCategory.COMPUTATIONAL)
        .with_difficulty(DifficultyLevel.MODERATE)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_child("VERIFICATION")
        .with_kind(ProblemKind.DECISION)
        .with_canonical_instance({"instance": "G = (V,E)", "question": "Is G 3-colorable?", "answer": None})
        .with_canonical_instance({"instance": "formula φ", "question": "Is φ satisfiable?", "answer": None})
        .with_complexity_notes(
            "Decision problems stratify by complexity class: P, NP, PSPACE, "
            "EXP, R, RE. The canonical NP-complete decision problem is SAT."
        )
        .with_evidence_kind("formal_proof")
        .with_evidence_kind("solver")
        .with_tag("core")
        .build()
    )

    counting = (
        ProblemClassBuilder("COUNTING")
        .with_class_id("COUNTING")
        .with_description(
            "Count the number of solutions satisfying given constraints, "
            "without necessarily listing them. #P is the canonical complexity "
            "class for counting problems."
        )
        .with_category(ProblemCategory.COMPUTATIONAL)
        .with_difficulty(DifficultyLevel.HARD)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_parent("SEARCH")
        .with_kind(ProblemKind.COUNTING)
        .with_canonical_instance({"formula": "φ(x1,...,xn)", "question": "How many satisfying assignments?"})
        .with_canonical_instance({"graph": "G", "question": "How many perfect matchings?"})
        .with_complexity_notes(
            "#P-hard in general; #P-complete problems include #SAT and permanent "
            "computation. FPRAS algorithms exist for some monotone instances."
        )
        .with_evidence_kind("solver")
        .with_tag("counting")
        .build()
    )

    enumeration = (
        ProblemClassBuilder("ENUMERATION")
        .with_class_id("ENUMERATION")
        .with_description(
            "List all solutions satisfying given constraints. "
            "Unlike COUNTING, ENUMERATION must output each witness explicitly. "
            "Subsumes SEARCH and contributes input to downstream aggregation."
        )
        .with_category(ProblemCategory.COMPUTATIONAL)
        .with_difficulty(DifficultyLevel.HARD)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_parent("SEARCH")
        .with_kind(ProblemKind.ENUMERATION)
        .with_canonical_instance({"formula": "φ", "output": "all satisfying assignments"})
        .with_canonical_instance({"graph": "G", "k": 3, "output": "all k-cliques"})
        .with_complexity_notes(
            "Output-sensitive complexity: O(n + |output|) per solution for "
            "problems with polynomial delay. Full enumeration may be exponential."
        )
        .with_evidence_kind("solver")
        .with_tag("enumeration")
        .build()
    )

    construction = (
        ProblemClassBuilder("CONSTRUCTION")
        .with_class_id("CONSTRUCTION")
        .with_description(
            "Build an artifact that satisfies a given specification. "
            "Constructive problems require a witness exhibit, not just a decision. "
            "Every constructed artifact is a certificate of its own specification."
        )
        .with_category(ProblemCategory.CONSTRUCTIVE)
        .with_difficulty(DifficultyLevel.MODERATE)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_child("SYNTHESIS")
        .with_child("REPAIR")
        .with_kind(ProblemKind.CONSTRUCTION)
        .with_canonical_instance({"specification": "sorted(output) == input", "input": [3, 1, 2]})
        .with_canonical_instance({"specification": "balanced BST over keys K", "keys": [5, 3, 7]})
        .with_complexity_notes(
            "Construction complexity is at least as hard as the corresponding "
            "DECISION problem. Often reduces to SEARCH + verification."
        )
        .with_evidence_kind("runtime")
        .with_evidence_kind("solver")
        .with_tag("constructive")
        .build()
    )

    verification = (
        ProblemClassBuilder("VERIFICATION")
        .with_class_id("VERIFICATION")
        .with_description(
            "Certify that a given artifact satisfies a formal specification, "
            "producing a certificate as evidence. Verification problems require "
            "both a decision and an explicit certificate trail."
        )
        .with_category(ProblemCategory.VERIFICATION)
        .with_difficulty(DifficultyLevel.HARD)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_parent("DECISION")
        .with_kind(ProblemKind.VERIFICATION)
        .with_canonical_instance({
            "claim": "program P is correct with respect to spec S",
            "evidence": "formal proof π",
            "verdict": None,
        })
        .with_canonical_instance({
            "claim": "x is a satisfying assignment for φ",
            "evidence": "assignment x",
            "verdict": None,
        })
        .with_complexity_notes(
            "NP ⊆ co-VERIFICATION. Formal verification is PSPACE-complete for "
            "modal logics. Bounded model checking is NP-complete."
        )
        .with_evidence_kind("formal_proof")
        .with_evidence_kind("human")
        .with_tag("verification")
        .build()
    )

    inference = (
        ProblemClassBuilder("INFERENCE")
        .with_class_id("INFERENCE")
        .with_description(
            "Derive new conclusions from a set of premises using logical rules. "
            "Inference problems form the backbone of knowledge-base reasoning "
            "and probabilistic modelling."
        )
        .with_category(ProblemCategory.ANALYTICAL)
        .with_difficulty(DifficultyLevel.MODERATE)
        .with_decidability(DecidabilityKind.SEMI_DECIDABLE)
        .with_child("CLASSIFICATION")
        .with_kind(ProblemKind.INFERENCE)
        .with_canonical_instance({"premises": ["∀x P(x) → Q(x)", "P(a)"], "conclusion": "Q(a)"})
        .with_canonical_instance({"observations": [0.8, 0.2], "prior": 0.5, "posterior": None})
        .with_complexity_notes(
            "Propositional entailment is co-NP-complete. First-order provability "
            "is RE-complete (semi-decidable). Probabilistic inference is #P-hard."
        )
        .with_evidence_kind("oracle")
        .with_evidence_kind("formal_proof")
        .with_tag("analytical")
        .build()
    )

    synthesis = (
        ProblemClassBuilder("SYNTHESIS")
        .with_class_id("SYNTHESIS")
        .with_description(
            "Produce a new artifact meeting a high-level specification, "
            "typically in a target language or domain. Synthesis subsumes "
            "CONSTRUCTION by additionally requiring the artifact to be "
            "generated from scratch given only abstract requirements."
        )
        .with_category(ProblemCategory.CONSTRUCTIVE)
        .with_difficulty(DifficultyLevel.HARD)
        .with_decidability(DecidabilityKind.SEMI_DECIDABLE)
        .with_parent("CONSTRUCTION")
        .with_kind(ProblemKind.SYNTHESIS)
        .with_canonical_instance({"spec": "function that sorts integers", "target_language": "Python"})
        .with_canonical_instance({"ltl_formula": "□(req → ◇ resp)", "target": "reactive controller"})
        .with_complexity_notes(
            "Program synthesis is undecidable in general. Bounded synthesis "
            "(bounded program size) is EXPTIME-complete. LTL synthesis is "
            "2EXPTIME-complete."
        )
        .with_evidence_kind("runtime")
        .with_evidence_kind("formal_proof")
        .with_tag("constructive")
        .with_tag("synthesis")
        .build()
    )

    repair = (
        ProblemClassBuilder("REPAIR")
        .with_class_id("REPAIR")
        .with_description(
            "Modify a broken artifact so that it satisfies a given specification. "
            "REPAIR problems are given a defective instance and a spec; they must "
            "produce a minimal or bounded correction achieving compliance."
        )
        .with_category(ProblemCategory.CONSTRUCTIVE)
        .with_difficulty(DifficultyLevel.MODERATE)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_parent("CONSTRUCTION")
        .with_kind(ProblemKind.REPAIR)
        .with_canonical_instance({"broken_program": "P with bug at line 7", "spec": "sorted output"})
        .with_canonical_instance({"broken_data": {"x": -1}, "constraints": ["x >= 0"], "repair": None})
        .with_complexity_notes(
            "Optimal repair (minimum edit distance) is NP-hard in general. "
            "Template-based and bounded repair are often polynomial."
        )
        .with_evidence_kind("runtime")
        .with_evidence_kind("solver")
        .with_tag("constructive")
        .with_tag("repair")
        .build()
    )

    classification = (
        ProblemClassBuilder("CLASSIFICATION")
        .with_class_id("CLASSIFICATION")
        .with_description(
            "Assign an input to one of a fixed set of categories based on "
            "learned or rule-based criteria. Classification subsumes INFERENCE "
            "by applying a categorical decision boundary."
        )
        .with_category(ProblemCategory.ANALYTICAL)
        .with_difficulty(DifficultyLevel.EASY)
        .with_decidability(DecidabilityKind.DECIDABLE)
        .with_parent("INFERENCE")
        .with_kind(ProblemKind.CLASSIFICATION)
        .with_canonical_instance({"input": [0.7, 0.3], "classes": ["cat", "dog"], "label": None})
        .with_canonical_instance({"email": "...", "classes": ["spam", "ham"], "label": None})
        .with_complexity_notes(
            "Linear classification is O(n·d). SVM training is O(n²) to O(n³). "
            "Neural classification inference is O(n·params)."
        )
        .with_evidence_kind("oracle")
        .with_evidence_kind("runtime")
        .with_tag("analytical")
        .with_tag("ml")
        .build()
    )

    return {
        "SEARCH": search,
        "OPTIMIZATION": optimization,
        "DECISION": decision,
        "COUNTING": counting,
        "ENUMERATION": enumeration,
        "CONSTRUCTION": construction,
        "VERIFICATION": verification,
        "INFERENCE": inference,
        "SYNTHESIS": synthesis,
        "REPAIR": repair,
        "CLASSIFICATION": classification,
    }


STANDARD_PROBLEM_CLASSES: dict[str, ProblemClass] = _build_standard_classes()


# ═══════════════════════════════════════════════════════════════════════════
# §11  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════


def build_standard_registry() -> ProblemClassRegistry:
    """Build a fresh ProblemClassRegistry pre-populated with standard classes.

    Returns:
        A new ProblemClassRegistry containing all STANDARD_PROBLEM_CLASSES.
    """
    registry = ProblemClassRegistry()
    for pc in STANDARD_PROBLEM_CLASSES.values():
        registry.register(pc)
    return registry


def build_default_lattice() -> ProblemClassLattice:
    """Build the default ProblemClassLattice from the standard class catalogue.

    Returns:
        ProblemClassLattice computed from STANDARD_PROBLEM_CLASSES.
    """
    registry = build_standard_registry()
    computer = registry.build_lattice_computer()
    return computer.build_lattice()


def lookup_class(name: ClassName) -> ProblemClass | None:
    """Look up a problem class by name in the global registry.

    Args:
        name: The canonical class name (e.g., ``SEARCH``, ``VERIFICATION``).

    Returns:
        The matching ProblemClass, or None if not found.
    """
    return ProblemClassRegistry.global_registry().get_by_name(name)


def lookup_problem_class(name: ClassName) -> ProblemClass | None:
    """Alias for ``lookup_class``; provided for __init__.py compatibility.

    Args:
        name: The canonical class name.

    Returns:
        The matching ProblemClass, or None.
    """
    return lookup_class(name)


def register_class(pc: ProblemClass) -> None:
    """Register *pc* in the global registry.

    Args:
        pc: The ProblemClass instance to register globally.
    """
    ProblemClassRegistry.global_registry().register(pc)


def get_lattice() -> dict[str, list[str]]:
    """Return the standard class lattice as an adjacency list.

    Returns:
        Dict mapping each canonical class name to its list of child names.
    """
    result: dict[str, list[str]] = {}
    for name, pc in STANDARD_PROBLEM_CLASSES.items():
        result[name] = list(pc.child_ids)
    return result


def get_all_problem_kinds() -> list[ProblemKind]:
    """Return all values of the ProblemKind enumeration.

    Returns:
        List of all ProblemKind members in declaration order.
    """
    return list(ProblemKind)




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
# §12  __all__
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Enumerations
    "ConjunctionMode",
    "DecidabilityKind",
    "DifficultyLevel",
    "ProblemCategory",
    "ProblemKind",
    # Dataclasses
    "EvidenceRequirement",
    "InstanceTemplate",
    "ProblemClass",
    "ProblemClassLattice",
    "SubsumptionRelation",
    # Builder / computer / registry
    "ClassLatticeComputer",
    "InstanceGenerator",
    "ProblemClassBuilder",
    "ProblemClassRegistry",
    "ProblemClassSerializer",
    # Module-level data
    "STANDARD_PROBLEM_CLASSES",
    # Functions
    "build_default_lattice",
    "build_standard_registry",
    "get_all_problem_kinds",
    "get_lattice",
    "lookup_class",
    "lookup_problem_class",
    "register_class",
    # Type aliases
    "ClassId",
    "ClassName",
    "JsonStr",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.
