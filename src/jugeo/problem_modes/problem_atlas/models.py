"""Core domain models for the Unified Problem Atlas — Theory2.tex Ch14.

copilot: core domain models for problem classification and atlas operations.

This module defines the foundational data types for Chapter 14 of Theory2.tex.
The four central model classes are organized as follows:

  ProblemClass     (§14.1) — A node in the problem class lattice
  SemanticSignature (§14.2) — Input/output contract for a problem
  EvidenceRequirement (§14.3) — Evidence channel requirements for a problem
  AtlasCatalog     (§14.4) — Registry and index of all problem classes

Supporting enumerations:
  ProblemCategory   — Top-level categorization (COMPUTATIONAL, VERIFICATION, etc.)
  DifficultyLevel   — Problem difficulty tier (TRIVIAL through UNDECIDABLE)
  DecidabilityKind  — Decidability status (DECIDABLE, SEMI_DECIDABLE, UNDECIDABLE)
  ConjunctionMode   — How multiple evidence requirements combine (ALL, ANY, WEIGHTED)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite, CoordinateKind
except ImportError:
    CoordinateObject = object  # type: ignore[assignment,misc]
    SemanticSite = object  # type: ignore[assignment,misc]
    CoordinateKind = None  # type: ignore[assignment]

try:
    from jugeo.geometry.covers import Cover
except ImportError:
    Cover = object  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = object  # type: ignore[assignment,misc]
    CertificateStatus = None  # type: ignore[assignment]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = object  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustProfile
except ImportError:
    TrustProfile = object  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ClassRegistry: TypeAlias = dict[str, "ProblemClass"]
EvidenceMap: TypeAlias = dict[str, float]
JsonSchema: TypeAlias = dict[str, Any]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProblemCategory(str, Enum):
    """Top-level category of a problem class.  Theory2.tex §14.1 Table 1.

    Each category represents a distinct mode of engagement with a problem:
    what *kind* of answer is sought, and what constitutes a valid solution.
    The categories are mutually exclusive at the top level but a concrete
    problem instance may belong to multiple sub-categories.
    """

    COMPUTATIONAL = "COMPUTATIONAL"
    VERIFICATION = "VERIFICATION"
    CONSTRUCTIVE = "CONSTRUCTIVE"
    ANALYTICAL = "ANALYTICAL"
    RELATIONAL = "RELATIONAL"
    META = "META"

    @property
    def description(self) -> str:
        """Return a one-line human-readable description of this category.

        Returns:
            A concise string describing what the category captures.
        """
        _descriptions: dict[str, str] = {
            "COMPUTATIONAL": (
                "Problems requiring computation of a function value or transformation."
            ),
            "VERIFICATION": (
                "Problems requiring checking whether a given property holds."
            ),
            "CONSTRUCTIVE": (
                "Problems requiring explicit construction of a witnessing object."
            ),
            "ANALYTICAL": (
                "Problems requiring structural or quantitative analysis of an input."
            ),
            "RELATIONAL": (
                "Problems defined by relationships between two or more objects."
            ),
            "META": (
                "Problems about the problem-solving process itself (e.g., learnability)."
            ),
        }
        return _descriptions[self.value]


class DifficultyLevel(str, Enum):
    """Difficulty tier of a problem class.  Theory2.tex §14.1 Table 2.

    The tiers form a total order TRIVIAL < EASY < MODERATE < HARD <
    INTRACTABLE < UNDECIDABLE.  The score() method maps each tier to its
    ordinal integer.
    """

    TRIVIAL = "TRIVIAL"
    EASY = "EASY"
    MODERATE = "MODERATE"
    HARD = "HARD"
    INTRACTABLE = "INTRACTABLE"
    UNDECIDABLE = "UNDECIDABLE"

    def score(self) -> int:
        """Return the integer score (0–5) for this difficulty tier.

        Returns:
            Integer in [0, 5] where 0 is TRIVIAL and 5 is UNDECIDABLE.
        """
        _scores: dict[str, int] = {
            "TRIVIAL": 0,
            "EASY": 1,
            "MODERATE": 2,
            "HARD": 3,
            "INTRACTABLE": 4,
            "UNDECIDABLE": 5,
        }
        return _scores[self.value]

    def is_tractable(self) -> bool:
        """Return True iff this difficulty tier is computationally tractable.

        A difficulty level is tractable if it lies below INTRACTABLE, i.e.
        problems at this level admit polynomial-time algorithms in general.

        Returns:
            True for TRIVIAL, EASY, and MODERATE; False for the rest.
        """
        return self.score() <= DifficultyLevel.MODERATE.score()

    @classmethod
    def from_score(cls, score: int) -> "DifficultyLevel":
        """Return the DifficultyLevel corresponding to a numeric score.

        Args:
            score: Integer in [0, 5].

        Returns:
            The matching DifficultyLevel enum member.

        Raises:
            ValueError: If score is not in [0, 5].
        """
        _by_score: dict[int, DifficultyLevel] = {
            0: cls.TRIVIAL,
            1: cls.EASY,
            2: cls.MODERATE,
            3: cls.HARD,
            4: cls.INTRACTABLE,
            5: cls.UNDECIDABLE,
        }
        if score not in _by_score:
            raise ValueError(
                f"score must be in [0, 5]; got {score!r}"
            )
        return _by_score[score]


class DecidabilityKind(str, Enum):
    """Decidability status of a problem class.  Theory2.tex §14.2.

    Encodes the computability-theoretic status of membership in the problem
    class: whether a Turing machine can always halt and give a correct answer
    (DECIDABLE), only enumerate positive instances (SEMI_DECIDABLE), only
    enumerate negative instances (CO_SEMI_DECIDABLE), or neither (UNDECIDABLE).
    UNKNOWN indicates that the decidability status has not been established.
    """

    DECIDABLE = "DECIDABLE"
    SEMI_DECIDABLE = "SEMI_DECIDABLE"
    CO_SEMI_DECIDABLE = "CO_SEMI_DECIDABLE"
    UNDECIDABLE = "UNDECIDABLE"
    UNKNOWN = "UNKNOWN"

    def is_computable(self) -> bool:
        """Return True iff the problem class is (fully) decidable.

        A problem is computable in this sense if there exists a Turing machine
        that halts on all inputs and always returns the correct yes/no answer.

        Returns:
            True only for DECIDABLE; False for all other kinds.
        """
        return self is DecidabilityKind.DECIDABLE


class ConjunctionMode(str, Enum):
    """How multiple evidence requirements combine.  Theory2.tex §14.3.

    Evidence requirements for a problem class may demand that several
    evidence channels collectively satisfy some criterion.  ConjunctionMode
    specifies the logical structure of that combination.
    """

    ALL = "ALL"
    ANY = "ANY"
    WEIGHTED = "WEIGHTED"
    MAJORITY = "MAJORITY"
    THRESHOLD = "THRESHOLD"
    CRITICAL_PATH = "CRITICAL_PATH"

    def requires_all(self) -> bool:
        """Return True iff this mode demands that every channel is satisfied.

        Returns:
            True for ALL and CRITICAL_PATH; False otherwise.
        """
        return self in (ConjunctionMode.ALL, ConjunctionMode.CRITICAL_PATH)

    def allows_partial(self) -> bool:
        """Return True iff this mode can be satisfied by a strict subset of channels.

        Returns:
            True for ANY, MAJORITY, THRESHOLD, and WEIGHTED; False for ALL and
            CRITICAL_PATH.
        """
        return not self.requires_all()


# ---------------------------------------------------------------------------
# ProblemClass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProblemClass:
    """A category of computational/verification problem with a precise semantic
    characterization.  Problem classes are organized in a lattice: each class
    has super-classes (more general) and sub-classes (more specific).  The
    atlas maps every problem kind encountered in jugeo to its class.
    Theory2.tex §14.1.

    The lattice is defined by the partial order ≤ where A ≤ B iff A is a
    sub-class of B (every instance of A is also an instance of B).  The
    lattice has a unique top element (UNIVERSAL) and unique bottom (EMPTY).

    Attributes:
        class_id: Unique identifier for this problem class (UUID string).
        name: Short canonical name (e.g., "VERIFICATION", "SEARCH").
        description: Human-readable description of the problem class.
        category: ProblemCategory this class belongs to.
        difficulty_level: DifficultyLevel indicating typical hardness.
        parent_classes: IDs of direct super-classes in the lattice.
        child_classes: IDs of direct sub-classes in the lattice.
        canonical_instances: Example problem instances as JSON-compatible dicts.
        complexity_notes: Notes on complexity class membership (e.g., "NP-complete").
        required_evidence_kinds: Evidence kinds needed for this class.
    """

    class_id: str
    name: str
    description: str
    category: ProblemCategory
    difficulty_level: DifficultyLevel
    parent_classes: tuple[str, ...]
    child_classes: tuple[str, ...]
    canonical_instances: tuple[dict[str, Any], ...]
    complexity_notes: str
    required_evidence_kinds: tuple[str, ...]

    # ------------------------------------------------------------------
    # Lattice traversal
    # ------------------------------------------------------------------

    def is_subclass_of(self, other: "ProblemClass") -> bool:
        """Return True if *other* is a direct parent of this class.

        A full ancestor check requires the registry; this method performs only
        the single-hop check against ``parent_classes``.  For transitive
        closure use ``get_ancestors``.

        Args:
            other: The candidate super-class.

        Returns:
            True iff other.class_id appears in self.parent_classes.
        """
        return other.class_id in self.parent_classes

    def is_superclass_of(self, other: "ProblemClass") -> bool:
        """Return True if *other* is a direct child of this class.

        Args:
            other: The candidate sub-class.

        Returns:
            True iff other.class_id appears in self.child_classes.
        """
        return other.class_id in self.child_classes

    def get_ancestors(self, registry: ClassRegistry) -> list["ProblemClass"]:
        """Return all ancestors of this class via BFS over parent links.

        Args:
            registry: Mapping from class_id to ProblemClass for the full
                catalog.

        Returns:
            List of ProblemClass objects that are (transitive) super-classes of
            this class, in BFS order.  Does not include self.
        """
        visited: set[str] = set()
        queue: list[str] = list(self.parent_classes)
        result: list[ProblemClass] = []

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            node = registry.get(current_id)
            if node is not None:
                result.append(node)
                queue.extend(
                    pid for pid in node.parent_classes if pid not in visited
                )
        return result

    def get_descendants(self, registry: ClassRegistry) -> list["ProblemClass"]:
        """Return all descendants of this class via BFS over child links.

        Args:
            registry: Mapping from class_id to ProblemClass for the full
                catalog.

        Returns:
            List of ProblemClass objects that are (transitive) sub-classes of
            this class, in BFS order.  Does not include self.
        """
        visited: set[str] = set()
        queue: list[str] = list(self.child_classes)
        result: list[ProblemClass] = []

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            node = registry.get(current_id)
            if node is not None:
                result.append(node)
                queue.extend(
                    cid for cid in node.child_classes if cid not in visited
                )
        return result

    # ------------------------------------------------------------------
    # Instance access
    # ------------------------------------------------------------------

    def get_canonical_instance(self, index: int = 0) -> dict[str, Any]:
        """Return the canonical example instance at *index*.

        Args:
            index: Zero-based index into canonical_instances.  Defaults to 0.

        Returns:
            The dict at the given index.

        Raises:
            IndexError: If index is out of range for canonical_instances.
        """
        if index < 0 or index >= len(self.canonical_instances):
            raise IndexError(
                f"canonical instance index {index} out of range "
                f"[0, {len(self.canonical_instances)})"
            )
        return self.canonical_instances[index]

    # ------------------------------------------------------------------
    # Structural mutations (return new frozen instances)
    # ------------------------------------------------------------------

    def add_child_class(self, child_id: str) -> "ProblemClass":
        """Return a new ProblemClass with *child_id* added to child_classes.

        Because ProblemClass is frozen, this method creates a new instance
        via dataclasses.replace-equivalent manual construction.

        Args:
            child_id: The class_id of the child to add.

        Returns:
            A new ProblemClass identical to self except child_classes contains
            child_id (deduplicated).
        """
        new_children = tuple(dict.fromkeys((*self.child_classes, child_id)))
        return ProblemClass(
            class_id=self.class_id,
            name=self.name,
            description=self.description,
            category=self.category,
            difficulty_level=self.difficulty_level,
            parent_classes=self.parent_classes,
            child_classes=new_children,
            canonical_instances=self.canonical_instances,
            complexity_notes=self.complexity_notes,
            required_evidence_kinds=self.required_evidence_kinds,
        )

    def add_parent_class(self, parent_id: str) -> "ProblemClass":
        """Return a new ProblemClass with *parent_id* added to parent_classes.

        Args:
            parent_id: The class_id of the parent to add.

        Returns:
            A new ProblemClass identical to self except parent_classes contains
            parent_id (deduplicated).
        """
        new_parents = tuple(dict.fromkeys((*self.parent_classes, parent_id)))
        return ProblemClass(
            class_id=self.class_id,
            name=self.name,
            description=self.description,
            category=self.category,
            difficulty_level=self.difficulty_level,
            parent_classes=new_parents,
            child_classes=self.child_classes,
            canonical_instances=self.canonical_instances,
            complexity_notes=self.complexity_notes,
            required_evidence_kinds=self.required_evidence_kinds,
        )

    # ------------------------------------------------------------------
    # Difficulty and evidence
    # ------------------------------------------------------------------

    def compute_difficulty_score(self) -> int:
        """Return the numeric difficulty score for this problem class.

        Returns:
            Integer in [0, 5] derived from difficulty_level.score().
        """
        return self.difficulty_level.score()

    def check_evidence_sufficiency(self, available_kinds: Sequence[str]) -> bool:
        """Return True iff every required evidence kind is available.

        Args:
            available_kinds: Collection of evidence kind strings that have been
                gathered.

        Returns:
            True iff required_evidence_kinds ⊆ set(available_kinds).
        """
        available_set = set(available_kinds)
        return all(k in available_set for k in self.required_evidence_kinds)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this ProblemClass to a JSON-compatible dictionary.

        Returns:
            A dict whose values are all JSON-serializable primitives or
            containers.
        """
        return {
            "class_id": self.class_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "difficulty_level": self.difficulty_level.value,
            "parent_classes": list(self.parent_classes),
            "child_classes": list(self.child_classes),
            "canonical_instances": list(self.canonical_instances),
            "complexity_notes": self.complexity_notes,
            "required_evidence_kinds": list(self.required_evidence_kinds),
        }


# ---------------------------------------------------------------------------
# SemanticSignature
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticSignature:
    """Input/output contract for a problem class.  Theory2.tex §14.2.

    A SemanticSignature captures the formal interface of a problem: what
    inputs it expects, what outputs it produces, pre/post conditions, and the
    computational complexity class.  Signatures can be composed sequentially
    (chaining outputs to inputs) and restricted to sub-problems.

    Attributes:
        sig_id: Unique identifier for this signature (UUID string).
        problem_class_id: ID of the ProblemClass this signature belongs to.
        input_schema: JSON schema dict describing valid inputs.
        output_schema: JSON schema dict describing valid outputs.
        preconditions: Precondition predicates expressed as strings.
        postconditions: Postcondition predicates expressed as strings.
        invariants: Invariants that must hold throughout execution.
        side_effects: Allowed side effects as descriptive strings.
        complexity_class: Complexity class string (e.g. "P", "NP", "EXPTIME").
        decidability: DecidabilityKind for this problem.
    """

    sig_id: str
    problem_class_id: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    invariants: tuple[str, ...]
    side_effects: tuple[str, ...]
    complexity_class: str
    decidability: DecidabilityKind

    # ------------------------------------------------------------------
    # Schema compatibility
    # ------------------------------------------------------------------

    def check_input_compatibility(self, value: dict[str, Any]) -> bool:
        """Return True iff *value* satisfies the input schema's required fields.

        Performs a lightweight structural check: all keys marked ``required``
        in input_schema must be present in *value*.  Full JSON Schema
        validation is out of scope here.

        Args:
            value: The candidate input dict to check.

        Returns:
            True iff every required key from input_schema is present.
        """
        required = self.input_schema.get("required", [])
        return all(k in value for k in required)

    def check_output_compatibility(self, value: dict[str, Any]) -> bool:
        """Return True iff *value* satisfies the output schema's required fields.

        Args:
            value: The candidate output dict to check.

        Returns:
            True iff every required key from output_schema is present.
        """
        required = self.output_schema.get("required", [])
        return all(k in value for k in required)

    # ------------------------------------------------------------------
    # Composition and restriction
    # ------------------------------------------------------------------

    def compose_with(self, other: "SemanticSignature") -> "SemanticSignature":
        """Return a new signature representing sequential composition self >> other.

        The composed signature takes self's inputs and produces other's outputs.
        Preconditions are those of self; postconditions are those of other.
        Invariants and side effects are unioned.

        Args:
            other: The signature to compose after self.

        Returns:
            A new SemanticSignature representing the sequential pipeline.
        """
        composed_input = dict(self.input_schema)
        composed_output = dict(other.output_schema)
        composed_pre = self.preconditions
        composed_post = other.postconditions
        composed_inv = tuple(dict.fromkeys((*self.invariants, *other.invariants)))
        composed_fx = tuple(dict.fromkeys((*self.side_effects, *other.side_effects)))
        composed_complexity = f"compose({self.complexity_class},{other.complexity_class})"
        composed_decidability = (
            self.decidability
            if self.decidability == other.decidability
            else DecidabilityKind.UNKNOWN
        )
        return SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=other.problem_class_id,
            input_schema=composed_input,
            output_schema=composed_output,
            preconditions=composed_pre,
            postconditions=composed_post,
            invariants=composed_inv,
            side_effects=composed_fx,
            complexity_class=composed_complexity,
            decidability=composed_decidability,
        )

    def restrict_to_subproblem(
        self, subproblem_id: str, restrictions: dict[str, Any]
    ) -> "SemanticSignature":
        """Return a signature narrowed to a sub-problem via additional schema constraints.

        Args:
            subproblem_id: The class_id of the sub-problem this signature is
                being restricted to.
            restrictions: A dict of additional JSON schema properties to merge
                into the input schema (e.g. ``{"properties": {"n": {"maximum": 100}}}``).

        Returns:
            A new SemanticSignature with the restricted input schema and the
            given subproblem_id.
        """
        restricted_input = {**self.input_schema}
        for k, v in restrictions.items():
            if k == "properties" and isinstance(v, dict):
                existing_props = dict(restricted_input.get("properties", {}))
                for prop_name, prop_schema in v.items():
                    existing_props[prop_name] = {
                        **existing_props.get(prop_name, {}),
                        **prop_schema,
                    }
                restricted_input["properties"] = existing_props
            else:
                restricted_input[k] = v
        return SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=subproblem_id,
            input_schema=restricted_input,
            output_schema=self.output_schema,
            preconditions=self.preconditions,
            postconditions=self.postconditions,
            invariants=self.invariants,
            side_effects=self.side_effects,
            complexity_class=self.complexity_class,
            decidability=self.decidability,
        )

    def generalize_to_superclass(self, superclass_id: str) -> "SemanticSignature":
        """Return a signature widened to a super-class by relaxing required fields.

        Generalization removes all ``required`` constraints from the input
        schema, making acceptance maximally permissive.

        Args:
            superclass_id: The class_id of the super-class to generalize to.

        Returns:
            A new SemanticSignature with no required input fields and the
            given superclass_id.
        """
        widened_input = {k: v for k, v in self.input_schema.items() if k != "required"}
        widened_output = {k: v for k, v in self.output_schema.items() if k != "required"}
        return SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=superclass_id,
            input_schema=widened_input,
            output_schema=widened_output,
            preconditions=(),
            postconditions=self.postconditions,
            invariants=self.invariants,
            side_effects=self.side_effects,
            complexity_class=self.complexity_class,
            decidability=self.decidability,
        )

    # ------------------------------------------------------------------
    # Condition verification
    # ------------------------------------------------------------------

    def verify_precondition(self, context: dict[str, Any]) -> bool:
        """Return True iff all preconditions are satisfied by the given context.

        Each precondition string is interpreted as a required key that must be
        present and truthy in *context*.  This is a lightweight check; a full
        predicate evaluator is out of scope.

        Args:
            context: Runtime context dict to check against preconditions.

        Returns:
            True iff every precondition key is present and truthy in context.
        """
        return all(context.get(cond) for cond in self.preconditions)

    def verify_postcondition(self, context: dict[str, Any]) -> bool:
        """Return True iff all postconditions are satisfied by the given context.

        Args:
            context: Runtime context dict to check against postconditions.

        Returns:
            True iff every postcondition key is present and truthy in context.
        """
        return all(context.get(cond) for cond in self.postconditions)

    # ------------------------------------------------------------------
    # Structural matching and hashing
    # ------------------------------------------------------------------

    def matches_signature(self, other: "SemanticSignature") -> bool:
        """Return True iff this signature is structurally compatible with *other*.

        Two signatures are compatible if they share the same complexity class,
        decidability kind, and have overlapping required input fields (i.e.,
        every required field in other's input_schema is also required in self's
        input_schema, indicating self is at least as specific).

        Args:
            other: The signature to compare against.

        Returns:
            True iff structural compatibility holds.
        """
        if self.complexity_class != other.complexity_class:
            return False
        if self.decidability != other.decidability:
            return False
        other_required: set[str] = set(other.input_schema.get("required", []))
        self_required: set[str] = set(self.input_schema.get("required", []))
        return other_required.issubset(self_required)

    def compute_signature_hash(self) -> str:
        """Return a SHA-256 hex digest of the canonical JSON representation.

        The canonical form sorts all dict keys and uses compact separators to
        ensure deterministic output.

        Returns:
            64-character lowercase hex string.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this SemanticSignature to a JSON-compatible dictionary.

        Returns:
            A dict whose values are all JSON-serializable.
        """
        return {
            "sig_id": self.sig_id,
            "problem_class_id": self.problem_class_id,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "invariants": list(self.invariants),
            "side_effects": list(self.side_effects),
            "complexity_class": self.complexity_class,
            "decidability": self.decidability.value,
        }


# ---------------------------------------------------------------------------
# EvidenceRequirement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """Evidence channel requirements for a problem class.  Theory2.tex §14.3.

    An EvidenceRequirement specifies which evidence channels must contribute
    trust above a minimum threshold, how those channels combine (conjunction
    mode), which residuals are allowed or forbidden, and optional temporal
    constraints on when evidence must be gathered.

    Attributes:
        req_id: Unique identifier for this requirement (UUID string).
        problem_class_id: ID of the ProblemClass this requirement belongs to.
        required_channels: Names of evidence channels that must contribute.
        minimum_trust_level: Minimum trust level in [0.0, 1.0] each required
            channel must achieve.
        conjunction_mode: How required_channels combine (ALL, ANY, etc.).
        allowed_residuals: Residual kinds that are acceptable after verification.
        forbidden_residuals: Residual kinds that must not appear.
        temporal_constraints: Dict describing temporal ordering or deadline
            constraints (e.g. ``{"ordering": "before", "deadline_seconds": 3600}``).
        override_conditions: Conditions under which the requirement can be
            waived entirely.
    """

    req_id: str
    problem_class_id: str
    required_channels: tuple[str, ...]
    minimum_trust_level: float
    conjunction_mode: ConjunctionMode
    allowed_residuals: tuple[str, ...]
    forbidden_residuals: tuple[str, ...]
    temporal_constraints: dict[str, Any]
    override_conditions: tuple[str, ...]

    # ------------------------------------------------------------------
    # Satisfaction checks
    # ------------------------------------------------------------------

    def check_satisfied_by(self, evidence_map: EvidenceMap) -> bool:
        """Return True iff the evidence_map satisfies this requirement.

        Satisfaction logic depends on conjunction_mode:
          ALL         — every required channel must meet minimum_trust_level.
          ANY         — at least one required channel meets minimum_trust_level.
          MAJORITY    — strictly more than half meet minimum_trust_level.
          THRESHOLD   — mean trust across required channels >= minimum_trust_level.
          WEIGHTED    — same as THRESHOLD (weights not yet tracked in EvidenceMap).
          CRITICAL_PATH — same as ALL, but order is enforced separately.

        Args:
            evidence_map: Mapping from channel name to trust level float in
                [0.0, 1.0].

        Returns:
            True iff the requirement is satisfied per the conjunction_mode.
        """
        if not self.required_channels:
            return True

        channel_results = [
            evidence_map.get(ch, 0.0) >= self.minimum_trust_level
            for ch in self.required_channels
        ]

        mode = self.conjunction_mode
        if mode in (ConjunctionMode.ALL, ConjunctionMode.CRITICAL_PATH):
            return all(channel_results)
        if mode is ConjunctionMode.ANY:
            return any(channel_results)
        if mode is ConjunctionMode.MAJORITY:
            return sum(channel_results) > len(channel_results) / 2
        if mode in (ConjunctionMode.THRESHOLD, ConjunctionMode.WEIGHTED):
            trust_values = [
                evidence_map.get(ch, 0.0) for ch in self.required_channels
            ]
            return (sum(trust_values) / len(trust_values)) >= self.minimum_trust_level
        return all(channel_results)

    def compute_missing_evidence(self, evidence_map: EvidenceMap) -> list[str]:
        """Return the list of channels that have insufficient trust.

        Args:
            evidence_map: Mapping from channel name to trust level.

        Returns:
            List of channel names from required_channels whose trust level in
            evidence_map is below minimum_trust_level.
        """
        return [
            ch
            for ch in self.required_channels
            if evidence_map.get(ch, 0.0) < self.minimum_trust_level
        ]

    def compute_trust_gap(self, evidence_map: EvidenceMap) -> float:
        """Return the maximum shortfall across all required channels.

        The trust gap for a channel c is max(0, minimum_trust_level - trust(c)).
        This method returns the maximum such gap across all required channels.

        Args:
            evidence_map: Mapping from channel name to trust level.

        Returns:
            Non-negative float; 0.0 if all channels meet the minimum.
        """
        if not self.required_channels:
            return 0.0
        gaps = [
            max(0.0, self.minimum_trust_level - evidence_map.get(ch, 0.0))
            for ch in self.required_channels
        ]
        return max(gaps)

    # ------------------------------------------------------------------
    # Structural mutations (return new frozen instances)
    # ------------------------------------------------------------------

    def add_channel_requirement(self, channel: str) -> "EvidenceRequirement":
        """Return a new EvidenceRequirement with *channel* added.

        Args:
            channel: Name of the evidence channel to add.

        Returns:
            A new EvidenceRequirement identical to self except required_channels
            contains *channel* (deduplicated).
        """
        new_channels = tuple(dict.fromkeys((*self.required_channels, channel)))
        return EvidenceRequirement(
            req_id=self.req_id,
            problem_class_id=self.problem_class_id,
            required_channels=new_channels,
            minimum_trust_level=self.minimum_trust_level,
            conjunction_mode=self.conjunction_mode,
            allowed_residuals=self.allowed_residuals,
            forbidden_residuals=self.forbidden_residuals,
            temporal_constraints=self.temporal_constraints,
            override_conditions=self.override_conditions,
        )

    def remove_channel_requirement(self, channel: str) -> "EvidenceRequirement":
        """Return a new EvidenceRequirement with *channel* removed.

        Args:
            channel: Name of the evidence channel to remove.

        Returns:
            A new EvidenceRequirement identical to self except required_channels
            does not contain *channel*.
        """
        new_channels = tuple(ch for ch in self.required_channels if ch != channel)
        return EvidenceRequirement(
            req_id=self.req_id,
            problem_class_id=self.problem_class_id,
            required_channels=new_channels,
            minimum_trust_level=self.minimum_trust_level,
            conjunction_mode=self.conjunction_mode,
            allowed_residuals=self.allowed_residuals,
            forbidden_residuals=self.forbidden_residuals,
            temporal_constraints=self.temporal_constraints,
            override_conditions=self.override_conditions,
        )

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def is_weaker_than(self, other: "EvidenceRequirement") -> bool:
        """Return True iff self imposes strictly fewer constraints than other.

        Self is weaker than other iff self has a proper subset of required
        channels and a lower or equal minimum_trust_level.

        Args:
            other: The requirement to compare against.

        Returns:
            True iff self is strictly weaker.
        """
        self_set = set(self.required_channels)
        other_set = set(other.required_channels)
        return (
            self_set.issubset(other_set)
            and self_set != other_set
            and self.minimum_trust_level <= other.minimum_trust_level
        )

    def is_stronger_than(self, other: "EvidenceRequirement") -> bool:
        """Return True iff self imposes strictly more constraints than other.

        Args:
            other: The requirement to compare against.

        Returns:
            True iff self is strictly stronger.
        """
        return other.is_weaker_than(self)

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose_requirements(
        self,
        other: "EvidenceRequirement",
        mode: ConjunctionMode,
    ) -> "EvidenceRequirement":
        """Combine self and other into a single EvidenceRequirement.

        The combined requirement uses the union of required channels,
        the maximum of the two minimum trust levels, and the given mode.

        Args:
            other: The second requirement to combine with self.
            mode: The ConjunctionMode for the combined requirement.

        Returns:
            A new EvidenceRequirement representing the composition.
        """
        combined_channels = tuple(
            dict.fromkeys((*self.required_channels, *other.required_channels))
        )
        combined_allowed = tuple(
            dict.fromkeys((*self.allowed_residuals, *other.allowed_residuals))
        )
        combined_forbidden = tuple(
            dict.fromkeys((*self.forbidden_residuals, *other.forbidden_residuals))
        )
        combined_overrides = tuple(
            dict.fromkeys((*self.override_conditions, *other.override_conditions))
        )
        combined_temporal = {**self.temporal_constraints, **other.temporal_constraints}
        return EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=self.problem_class_id,
            required_channels=combined_channels,
            minimum_trust_level=max(
                self.minimum_trust_level, other.minimum_trust_level
            ),
            conjunction_mode=mode,
            allowed_residuals=combined_allowed,
            forbidden_residuals=combined_forbidden,
            temporal_constraints=combined_temporal,
            override_conditions=combined_overrides,
        )

    def get_critical_path(self) -> list[str]:
        """Return the channels in dependency order for CRITICAL_PATH mode.

        For CRITICAL_PATH mode the order of required_channels encodes the
        dependency sequence.  For other modes the channels are returned sorted
        alphabetically.

        Returns:
            Ordered list of channel names.
        """
        if self.conjunction_mode is ConjunctionMode.CRITICAL_PATH:
            return list(self.required_channels)
        return sorted(self.required_channels)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this EvidenceRequirement to a JSON-compatible dictionary.

        Returns:
            A dict whose values are all JSON-serializable.
        """
        return {
            "req_id": self.req_id,
            "problem_class_id": self.problem_class_id,
            "required_channels": list(self.required_channels),
            "minimum_trust_level": self.minimum_trust_level,
            "conjunction_mode": self.conjunction_mode.value,
            "allowed_residuals": list(self.allowed_residuals),
            "forbidden_residuals": list(self.forbidden_residuals),
            "temporal_constraints": self.temporal_constraints,
            "override_conditions": list(self.override_conditions),
        }


# ---------------------------------------------------------------------------
# AtlasCatalog
# ---------------------------------------------------------------------------


@dataclass
class AtlasCatalog:
    """Registry and index of all problem classes.  Theory2.tex §14.4.

    AtlasCatalog acts as the single authoritative source of truth for the
    problem class lattice within a jugeo session.  It maintains several
    secondary indexes to support efficient lookup by category, evidence
    channel, and signature.

    This class is intentionally *not* frozen because it maintains mutable
    internal state for incremental registration of new problem classes.

    Attributes:
        catalog_id: UUID string identifying this catalog instance.
        name: Human-readable name for this catalog.
        version: Semantic version string (e.g. "1.0.0").
        entries: Primary store mapping class_id to an entry dict with keys
            ``"class"``, ``"signature"``, and ``"requirement"``.
        category_index: Secondary index mapping category name → list[class_id].
        channel_index: Secondary index mapping channel name → list[class_id].
        signature_index: Secondary index mapping sig_id → class_id.
        created_at: ISO-8601 timestamp string recording creation time.
        metadata: Arbitrary additional metadata as a JSON-compatible dict.
    """

    catalog_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "UnifiedProblemAtlas"
    version: str = "1.0.0"
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    category_index: dict[str, list[str]] = field(default_factory=dict)
    channel_index: dict[str, list[str]] = field(default_factory=dict)
    signature_index: dict[str, str] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_problem_class(
        self,
        pc: ProblemClass,
        sig: SemanticSignature | None = None,
        req: EvidenceRequirement | None = None,
    ) -> None:
        """Register a ProblemClass (and optional signature/requirement) in the catalog.

        Updates the primary entries store and all secondary indexes.

        Args:
            pc: The ProblemClass to register.
            sig: Optional SemanticSignature associated with this class.
            req: Optional EvidenceRequirement associated with this class.

        Raises:
            ValueError: If a class with the same class_id is already registered
                and its name differs (duplicate detection).
        """
        existing = self.entries.get(pc.class_id)
        if existing is not None and existing["class"].name != pc.name:
            raise ValueError(
                f"class_id {pc.class_id!r} is already registered under a "
                f"different name: {existing['class'].name!r} vs {pc.name!r}"
            )

        self.entries[pc.class_id] = {
            "class": pc,
            "signature": sig,
            "requirement": req,
        }

        # Update category index
        cat_key = pc.category.value
        if cat_key not in self.category_index:
            self.category_index[cat_key] = []
        if pc.class_id not in self.category_index[cat_key]:
            self.category_index[cat_key].append(pc.class_id)

        # Update channel index
        if req is not None:
            for ch in req.required_channels:
                if ch not in self.channel_index:
                    self.channel_index[ch] = []
                if pc.class_id not in self.channel_index[ch]:
                    self.channel_index[ch].append(pc.class_id)

        # Update signature index
        if sig is not None:
            self.signature_index[sig.sig_id] = pc.class_id

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup_by_name(self, name: str) -> ProblemClass | None:
        """Return the ProblemClass with the given name, or None.

        Performs a linear scan; names are expected to be unique within a
        well-formed catalog.

        Args:
            name: The canonical name to search for (case-sensitive).

        Returns:
            The matching ProblemClass, or None if not found.
        """
        for entry in self.entries.values():
            pc: ProblemClass = entry["class"]
            if pc.name == name:
                return pc
        return None

    def lookup_by_category(
        self, category: str | ProblemCategory
    ) -> list[ProblemClass]:
        """Return all ProblemClass instances belonging to *category*.

        Args:
            category: Either a ProblemCategory enum member or its string value.

        Returns:
            List of ProblemClass instances in the category (may be empty).
        """
        cat_key = category.value if isinstance(category, ProblemCategory) else category
        class_ids = self.category_index.get(cat_key, [])
        return [
            self.entries[cid]["class"]
            for cid in class_ids
            if cid in self.entries
        ]

    def lookup_by_channel(self, channel: str) -> list[ProblemClass]:
        """Return all ProblemClass instances that require the given evidence channel.

        Args:
            channel: The evidence channel name to look up.

        Returns:
            List of ProblemClass instances requiring *channel*.
        """
        class_ids = self.channel_index.get(channel, [])
        return [
            self.entries[cid]["class"]
            for cid in class_ids
            if cid in self.entries
        ]

    def find_compatible_classes(
        self, signature: SemanticSignature
    ) -> list[ProblemClass]:
        """Return all ProblemClass instances whose signatures match *signature*.

        Compatibility is checked via SemanticSignature.matches_signature.

        Args:
            signature: The query signature to match against.

        Returns:
            List of ProblemClass instances with compatible registered signatures.
        """
        results: list[ProblemClass] = []
        for entry in self.entries.values():
            stored_sig: SemanticSignature | None = entry.get("signature")
            if stored_sig is not None and stored_sig.matches_signature(signature):
                results.append(entry["class"])
        return results

    def get_evidence_requirements(
        self, class_id: str
    ) -> EvidenceRequirement | None:
        """Return the EvidenceRequirement registered for *class_id*, or None.

        Args:
            class_id: The UUID string of the target problem class.

        Returns:
            The EvidenceRequirement, or None if not registered or not provided.
        """
        entry = self.entries.get(class_id)
        if entry is None:
            return None
        return entry.get("requirement")

    # ------------------------------------------------------------------
    # Lattice
    # ------------------------------------------------------------------

    def compute_class_lattice(self) -> dict[str, list[str]]:
        """Return an adjacency dictionary representing the problem class lattice.

        Each key is a class_id and its value is the list of direct child
        class_ids (i.e., the sub-classes of that node).

        Returns:
            Dict mapping class_id → list of child class_ids.
        """
        lattice: dict[str, list[str]] = {}
        for class_id, entry in self.entries.items():
            pc: ProblemClass = entry["class"]
            lattice[class_id] = list(pc.child_classes)
        return lattice

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def export_to_json(self) -> str:
        """Serialize this catalog to a JSON string.

        Returns:
            A JSON string representing the full catalog state.
        """
        data: dict[str, Any] = {
            "catalog_id": self.catalog_id,
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "entries": {
                cid: {
                    "class": entry["class"].to_dict(),
                    "signature": (
                        entry["signature"].to_dict()
                        if entry["signature"] is not None
                        else None
                    ),
                    "requirement": (
                        entry["requirement"].to_dict()
                        if entry["requirement"] is not None
                        else None
                    ),
                }
                for cid, entry in self.entries.items()
            },
        }
        return json.dumps(data, indent=2, sort_keys=True)

    @classmethod
    def import_from_json(cls, json_str: str) -> "AtlasCatalog":
        """Reconstruct an AtlasCatalog from a JSON string.

        Args:
            json_str: A JSON string produced by export_to_json.

        Returns:
            A new AtlasCatalog populated from the JSON data.

        Raises:
            ValueError: If json_str is malformed or missing required fields.
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        catalog = cls(
            catalog_id=data.get("catalog_id", str(uuid.uuid4())),
            name=data.get("name", "UnifiedProblemAtlas"),
            version=data.get("version", "1.0.0"),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )

        for _cid, entry_data in data.get("entries", {}).items():
            class_data = entry_data.get("class", {})
            pc = ProblemClass(
                class_id=class_data["class_id"],
                name=class_data["name"],
                description=class_data["description"],
                category=ProblemCategory(class_data["category"]),
                difficulty_level=DifficultyLevel(class_data["difficulty_level"]),
                parent_classes=tuple(class_data.get("parent_classes", [])),
                child_classes=tuple(class_data.get("child_classes", [])),
                canonical_instances=tuple(class_data.get("canonical_instances", [])),
                complexity_notes=class_data.get("complexity_notes", ""),
                required_evidence_kinds=tuple(
                    class_data.get("required_evidence_kinds", [])
                ),
            )

            sig: SemanticSignature | None = None
            sig_data = entry_data.get("signature")
            if sig_data is not None:
                sig = SemanticSignature(
                    sig_id=sig_data["sig_id"],
                    problem_class_id=sig_data["problem_class_id"],
                    input_schema=sig_data.get("input_schema", {}),
                    output_schema=sig_data.get("output_schema", {}),
                    preconditions=tuple(sig_data.get("preconditions", [])),
                    postconditions=tuple(sig_data.get("postconditions", [])),
                    invariants=tuple(sig_data.get("invariants", [])),
                    side_effects=tuple(sig_data.get("side_effects", [])),
                    complexity_class=sig_data.get("complexity_class", ""),
                    decidability=DecidabilityKind(sig_data["decidability"]),
                )

            req: EvidenceRequirement | None = None
            req_data = entry_data.get("requirement")
            if req_data is not None:
                req = EvidenceRequirement(
                    req_id=req_data["req_id"],
                    problem_class_id=req_data["problem_class_id"],
                    required_channels=tuple(req_data.get("required_channels", [])),
                    minimum_trust_level=req_data.get("minimum_trust_level", 0.5),
                    conjunction_mode=ConjunctionMode(req_data["conjunction_mode"]),
                    allowed_residuals=tuple(req_data.get("allowed_residuals", [])),
                    forbidden_residuals=tuple(req_data.get("forbidden_residuals", [])),
                    temporal_constraints=req_data.get("temporal_constraints", {}),
                    override_conditions=tuple(req_data.get("override_conditions", [])),
                )

            catalog.register_problem_class(pc, sig, req)

        return catalog

    # ------------------------------------------------------------------
    # Integrity validation
    # ------------------------------------------------------------------

    def validate_catalog_integrity(self) -> list[str]:
        """Check catalog for structural integrity and return a list of errors.

        Checks performed:
          1. Every parent_class reference points to a registered class_id.
          2. Every child_class reference points to a registered class_id.
          3. Parent/child links are symmetric (if A is child of B then B is
             parent of A).
          4. Every signature's problem_class_id matches the entry it is stored
             under.
          5. Every requirement's problem_class_id matches the entry.

        Returns:
            A list of error message strings.  Empty list means no errors.
        """
        errors: list[str] = []
        known_ids = set(self.entries.keys())

        for class_id, entry in self.entries.items():
            pc: ProblemClass = entry["class"]

            # Check class_id consistency
            if pc.class_id != class_id:
                errors.append(
                    f"Entry key {class_id!r} does not match pc.class_id {pc.class_id!r}"
                )

            # Check parent references
            for parent_id in pc.parent_classes:
                if parent_id not in known_ids:
                    errors.append(
                        f"Class {pc.name!r} ({class_id}) references unknown parent "
                        f"{parent_id!r}"
                    )
                else:
                    parent_pc: ProblemClass = self.entries[parent_id]["class"]
                    if class_id not in parent_pc.child_classes:
                        errors.append(
                            f"Asymmetric link: {pc.name!r} lists {parent_pc.name!r} "
                            f"as parent but {parent_pc.name!r} does not list "
                            f"{pc.name!r} as child"
                        )

            # Check child references
            for child_id in pc.child_classes:
                if child_id not in known_ids:
                    errors.append(
                        f"Class {pc.name!r} ({class_id}) references unknown child "
                        f"{child_id!r}"
                    )

            # Check signature consistency
            sig = entry.get("signature")
            if sig is not None and sig.problem_class_id != class_id:
                errors.append(
                    f"Signature {sig.sig_id!r} under class {class_id!r} has "
                    f"problem_class_id={sig.problem_class_id!r}"
                )

            # Check requirement consistency
            req = entry.get("requirement")
            if req is not None and req.problem_class_id != class_id:
                errors.append(
                    f"Requirement {req.req_id!r} under class {class_id!r} has "
                    f"problem_class_id={req.problem_class_id!r}"
                )

        return errors

    # ------------------------------------------------------------------
    # Default catalog factory
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "AtlasCatalog":
        """Build and return the default AtlasCatalog with all standard problem classes.

        Populates the catalog with 10 canonical problem classes:
          SEARCH, OPTIMIZATION, DECISION, COUNTING, CONSTRUCTION,
          VERIFICATION, INFERENCE, SYNTHESIS, REPAIR, CLASSIFICATION.

        Each class is registered with a SemanticSignature and an
        EvidenceRequirement.

        Returns:
            A fully populated AtlasCatalog ready for use.
        """
        catalog = cls(
            catalog_id=str(uuid.uuid4()),
            name="UnifiedProblemAtlas",
            version="1.0.0",
            metadata={"source": "Theory2.tex §14.4", "standard": True},
        )

        # Pre-allocate stable class IDs so parent/child links work
        ids: dict[str, str] = {
            name: str(uuid.uuid5(uuid.NAMESPACE_DNS, f"jugeo.atlas.{name}"))
            for name in [
                "SEARCH", "OPTIMIZATION", "DECISION", "COUNTING",
                "CONSTRUCTION", "VERIFICATION", "INFERENCE",
                "SYNTHESIS", "REPAIR", "CLASSIFICATION",
            ]
        }

        # ---- SEARCH ----
        search = ProblemClass(
            class_id=ids["SEARCH"],
            name="SEARCH",
            description=(
                "Given a search space and a goal predicate, find an element of the "
                "space satisfying the predicate.  Covers combinatorial search, "
                "constraint satisfaction, and satisfiability."
            ),
            category=ProblemCategory.COMPUTATIONAL,
            difficulty_level=DifficultyLevel.MODERATE,
            parent_classes=(),
            child_classes=(ids["OPTIMIZATION"], ids["DECISION"]),
            canonical_instances=(
                {"name": "SAT", "encoding": "CNF", "goal": "satisfying_assignment"},
                {"name": "TSP", "encoding": "adjacency_matrix", "goal": "min_tour"},
            ),
            complexity_notes="NP-hard in general; P for restricted variants",
            required_evidence_kinds=("TESTING", "FORMAL_PROOF"),
        )
        search_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["SEARCH"],
            input_schema={
                "type": "object",
                "required": ["search_space", "goal_predicate"],
                "properties": {
                    "search_space": {"type": "object"},
                    "goal_predicate": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["result"],
                "properties": {
                    "result": {"oneOf": [{"type": "object"}, {"type": "null"}]},
                    "found": {"type": "boolean"},
                },
            },
            preconditions=("search_space",),
            postconditions=("result",),
            invariants=("goal_predicate_immutable",),
            side_effects=(),
            complexity_class="NP",
            decidability=DecidabilityKind.DECIDABLE,
        )
        search_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["SEARCH"],
            required_channels=("TESTING", "FORMAL_PROOF"),
            minimum_trust_level=0.7,
            conjunction_mode=ConjunctionMode.ALL,
            allowed_residuals=("APPROXIMATE",),
            forbidden_residuals=("UNSOUND",),
            temporal_constraints={"ordering": "TESTING_before_FORMAL_PROOF"},
            override_conditions=("EXHAUSTIVE_TESTING_CONFIRMED",),
        )
        catalog.register_problem_class(search, search_sig, search_req)

        # ---- OPTIMIZATION ----
        optimization = ProblemClass(
            class_id=ids["OPTIMIZATION"],
            name="OPTIMIZATION",
            description=(
                "Given a search space and an objective function, find an element "
                "minimizing or maximizing the objective.  Subsumes combinatorial, "
                "continuous, and multi-objective optimization."
            ),
            category=ProblemCategory.COMPUTATIONAL,
            difficulty_level=DifficultyLevel.HARD,
            parent_classes=(ids["SEARCH"],),
            child_classes=(),
            canonical_instances=(
                {"name": "LP", "type": "linear_program", "objective": "min_cost"},
                {"name": "ILP", "type": "integer_linear_program", "objective": "min_cost"},
            ),
            complexity_notes="NP-hard in general; polynomial for LP via interior point",
            required_evidence_kinds=("TESTING", "FORMAL_PROOF", "BENCHMARK"),
        )
        opt_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["OPTIMIZATION"],
            input_schema={
                "type": "object",
                "required": ["search_space", "objective_function"],
                "properties": {
                    "search_space": {"type": "object"},
                    "objective_function": {"type": "string"},
                    "direction": {"type": "string", "enum": ["min", "max"]},
                },
            },
            output_schema={
                "type": "object",
                "required": ["optimal_value", "optimal_element"],
                "properties": {
                    "optimal_value": {"type": "number"},
                    "optimal_element": {"type": "object"},
                },
            },
            preconditions=("search_space", "objective_function"),
            postconditions=("optimal_value", "optimal_element"),
            invariants=("objective_unchanged",),
            side_effects=(),
            complexity_class="NP",
            decidability=DecidabilityKind.DECIDABLE,
        )
        opt_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["OPTIMIZATION"],
            required_channels=("TESTING", "FORMAL_PROOF", "BENCHMARK"),
            minimum_trust_level=0.8,
            conjunction_mode=ConjunctionMode.MAJORITY,
            allowed_residuals=("EPSILON_OPTIMAL",),
            forbidden_residuals=("INFEASIBLE_CLAIM",),
            temporal_constraints={"deadline_seconds": 7200},
            override_conditions=(),
        )
        catalog.register_problem_class(optimization, opt_sig, opt_req)

        # ---- DECISION ----
        decision = ProblemClass(
            class_id=ids["DECISION"],
            name="DECISION",
            description=(
                "Given an instance and a yes/no question, determine which answer is "
                "correct.  The canonical form of complexity-theoretic problems."
            ),
            category=ProblemCategory.VERIFICATION,
            difficulty_level=DifficultyLevel.HARD,
            parent_classes=(ids["SEARCH"],),
            child_classes=(ids["COUNTING"],),
            canonical_instances=(
                {"name": "CLIQUE", "encoding": "graph+k", "question": "k_clique_exists"},
                {"name": "REACHABILITY", "encoding": "graph+s+t", "question": "path_exists"},
            ),
            complexity_notes="NP-complete for canonical problems; PSPACE for others",
            required_evidence_kinds=("FORMAL_PROOF", "TYPE_CHECKING"),
        )
        decision_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["DECISION"],
            input_schema={
                "type": "object",
                "required": ["instance", "question"],
                "properties": {
                    "instance": {"type": "object"},
                    "question": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["answer"],
                "properties": {
                    "answer": {"type": "boolean"},
                    "witness": {"type": "object"},
                },
            },
            preconditions=("instance",),
            postconditions=("answer",),
            invariants=(),
            side_effects=(),
            complexity_class="NP",
            decidability=DecidabilityKind.DECIDABLE,
        )
        decision_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["DECISION"],
            required_channels=("FORMAL_PROOF", "TYPE_CHECKING"),
            minimum_trust_level=0.9,
            conjunction_mode=ConjunctionMode.ALL,
            allowed_residuals=(),
            forbidden_residuals=("UNSOUND", "INCOMPLETE"),
            temporal_constraints={},
            override_conditions=(),
        )
        catalog.register_problem_class(decision, decision_sig, decision_req)

        # ---- COUNTING ----
        counting = ProblemClass(
            class_id=ids["COUNTING"],
            name="COUNTING",
            description=(
                "Given a search space and a predicate, count the number of elements "
                "satisfying the predicate.  Harder than the corresponding decision "
                "problem in general (#P-complete for counting satisfying assignments)."
            ),
            category=ProblemCategory.ANALYTICAL,
            difficulty_level=DifficultyLevel.INTRACTABLE,
            parent_classes=(ids["DECISION"],),
            child_classes=(),
            canonical_instances=(
                {"name": "#SAT", "encoding": "CNF", "output": "num_satisfying"},
                {"name": "#MATCHING", "encoding": "bipartite_graph", "output": "num_perfect_matchings"},
            ),
            complexity_notes="#P-complete in general",
            required_evidence_kinds=("FORMAL_PROOF", "STATISTICAL_TESTING"),
        )
        counting_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["COUNTING"],
            input_schema={
                "type": "object",
                "required": ["search_space", "predicate"],
                "properties": {
                    "search_space": {"type": "object"},
                    "predicate": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["count"],
                "properties": {"count": {"type": "integer", "minimum": 0}},
            },
            preconditions=("search_space", "predicate"),
            postconditions=("count",),
            invariants=("predicate_fixed",),
            side_effects=(),
            complexity_class="#P",
            decidability=DecidabilityKind.DECIDABLE,
        )
        counting_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["COUNTING"],
            required_channels=("FORMAL_PROOF", "STATISTICAL_TESTING"),
            minimum_trust_level=0.85,
            conjunction_mode=ConjunctionMode.WEIGHTED,
            allowed_residuals=("APPROXIMATE_COUNT",),
            forbidden_residuals=("EXACT_CLAIM_UNVERIFIED",),
            temporal_constraints={},
            override_conditions=("FPRAS_VERIFIED",),
        )
        catalog.register_problem_class(counting, counting_sig, counting_req)

        # ---- CONSTRUCTION ----
        construction = ProblemClass(
            class_id=ids["CONSTRUCTION"],
            name="CONSTRUCTION",
            description=(
                "Given a specification, explicitly construct a concrete object "
                "satisfying it (e.g., a graph, a program, a proof term).  The "
                "output is a first-class artifact, not merely a yes/no answer."
            ),
            category=ProblemCategory.CONSTRUCTIVE,
            difficulty_level=DifficultyLevel.HARD,
            parent_classes=(),
            child_classes=(ids["SYNTHESIS"], ids["REPAIR"]),
            canonical_instances=(
                {"name": "GRAPH_CONSTRUCTION", "spec": "degree_sequence", "output": "graph"},
                {"name": "PROGRAM_SYNTHESIS", "spec": "IO_examples", "output": "program"},
            ),
            complexity_notes="Varies widely; often PSPACE or harder",
            required_evidence_kinds=("TESTING", "FORMAL_PROOF", "TYPE_CHECKING"),
        )
        construction_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["CONSTRUCTION"],
            input_schema={
                "type": "object",
                "required": ["specification"],
                "properties": {"specification": {"type": "object"}},
            },
            output_schema={
                "type": "object",
                "required": ["artifact"],
                "properties": {
                    "artifact": {"type": "object"},
                    "proof_of_correctness": {"type": "object"},
                },
            },
            preconditions=("specification",),
            postconditions=("artifact",),
            invariants=("spec_unchanged",),
            side_effects=("artifact_stored",),
            complexity_class="PSPACE",
            decidability=DecidabilityKind.SEMI_DECIDABLE,
        )
        construction_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["CONSTRUCTION"],
            required_channels=("TESTING", "FORMAL_PROOF", "TYPE_CHECKING"),
            minimum_trust_level=0.75,
            conjunction_mode=ConjunctionMode.CRITICAL_PATH,
            allowed_residuals=("PARTIAL_PROOF",),
            forbidden_residuals=("UNSOUND",),
            temporal_constraints={"ordering": "TYPE_CHECKING_before_FORMAL_PROOF"},
            override_conditions=(),
        )
        catalog.register_problem_class(construction, construction_sig, construction_req)

        # ---- VERIFICATION ----
        verification = ProblemClass(
            class_id=ids["VERIFICATION"],
            name="VERIFICATION",
            description=(
                "Given a system and a property, verify that the system satisfies "
                "the property.  Encompasses model checking, theorem proving, and "
                "static analysis."
            ),
            category=ProblemCategory.VERIFICATION,
            difficulty_level=DifficultyLevel.HARD,
            parent_classes=(),
            child_classes=(),
            canonical_instances=(
                {"name": "MODEL_CHECKING", "system": "Kripke_structure", "property": "LTL_formula"},
                {"name": "TYPE_CHECKING", "system": "program", "property": "type_annotation"},
            ),
            complexity_notes="Co-NP-complete; PSPACE for LTL model checking",
            required_evidence_kinds=("FORMAL_PROOF", "TYPE_CHECKING"),
        )
        verification_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["VERIFICATION"],
            input_schema={
                "type": "object",
                "required": ["system", "property"],
                "properties": {
                    "system": {"type": "object"},
                    "property": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["verdict"],
                "properties": {
                    "verdict": {"type": "boolean"},
                    "counterexample": {"type": "object"},
                },
            },
            preconditions=("system", "property"),
            postconditions=("verdict",),
            invariants=("property_immutable",),
            side_effects=(),
            complexity_class="co-NP",
            decidability=DecidabilityKind.DECIDABLE,
        )
        verification_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["VERIFICATION"],
            required_channels=("FORMAL_PROOF", "TYPE_CHECKING"),
            minimum_trust_level=0.95,
            conjunction_mode=ConjunctionMode.ALL,
            allowed_residuals=(),
            forbidden_residuals=("UNSOUND", "INCOMPLETE", "CIRCULAR"),
            temporal_constraints={},
            override_conditions=(),
        )
        catalog.register_problem_class(verification, verification_sig, verification_req)

        # ---- INFERENCE ----
        inference = ProblemClass(
            class_id=ids["INFERENCE"],
            name="INFERENCE",
            description=(
                "Given a knowledge base and a query, determine whether the query "
                "follows from the knowledge base under a given inference system.  "
                "Covers deductive, inductive, and abductive reasoning."
            ),
            category=ProblemCategory.ANALYTICAL,
            difficulty_level=DifficultyLevel.HARD,
            parent_classes=(),
            child_classes=(),
            canonical_instances=(
                {"name": "PROPOSITIONAL_ENTAILMENT", "KB": "CNF", "query": "clause"},
                {"name": "FOL_THEOREM_PROVING", "KB": "axioms", "query": "theorem"},
            ),
            complexity_notes="co-NP for propositional; undecidable for FOL",
            required_evidence_kinds=("FORMAL_PROOF", "LOGICAL_WITNESS"),
        )
        inference_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["INFERENCE"],
            input_schema={
                "type": "object",
                "required": ["knowledge_base", "query"],
                "properties": {
                    "knowledge_base": {"type": "array"},
                    "query": {"type": "string"},
                    "inference_system": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["entailed"],
                "properties": {
                    "entailed": {"type": "boolean"},
                    "derivation": {"type": "array"},
                },
            },
            preconditions=("knowledge_base", "query"),
            postconditions=("entailed",),
            invariants=("KB_closed_under_inference",),
            side_effects=(),
            complexity_class="co-NP",
            decidability=DecidabilityKind.SEMI_DECIDABLE,
        )
        inference_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["INFERENCE"],
            required_channels=("FORMAL_PROOF", "LOGICAL_WITNESS"),
            minimum_trust_level=0.9,
            conjunction_mode=ConjunctionMode.ALL,
            allowed_residuals=("PARTIAL_DERIVATION",),
            forbidden_residuals=("CIRCULAR_PROOF",),
            temporal_constraints={},
            override_conditions=("REFUTATION_COMPLETE",),
        )
        catalog.register_problem_class(inference, inference_sig, inference_req)

        # ---- SYNTHESIS ----
        synthesis = ProblemClass(
            class_id=ids["SYNTHESIS"],
            name="SYNTHESIS",
            description=(
                "Given a high-level specification (examples, types, or logical "
                "formula), synthesize a program or structure satisfying it.  "
                "Subsumes program synthesis, reactive synthesis, and sketch "
                "completion."
            ),
            category=ProblemCategory.CONSTRUCTIVE,
            difficulty_level=DifficultyLevel.INTRACTABLE,
            parent_classes=(ids["CONSTRUCTION"],),
            child_classes=(),
            canonical_instances=(
                {"name": "REACTIVE_SYNTHESIS", "spec": "LTL_formula", "output": "transducer"},
                {"name": "FLASHFILL", "spec": "IO_pairs", "output": "string_program"},
            ),
            complexity_notes="EXPTIME-complete for reactive synthesis",
            required_evidence_kinds=("TESTING", "FORMAL_PROOF", "TYPE_CHECKING", "BENCHMARK"),
        )
        synthesis_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["SYNTHESIS"],
            input_schema={
                "type": "object",
                "required": ["specification"],
                "properties": {
                    "specification": {"type": "object"},
                    "grammar": {"type": "object"},
                    "examples": {"type": "array"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["program"],
                "properties": {
                    "program": {"type": "object"},
                    "correctness_certificate": {"type": "object"},
                },
            },
            preconditions=("specification",),
            postconditions=("program",),
            invariants=("grammar_fixed",),
            side_effects=("program_written_to_store",),
            complexity_class="EXPTIME",
            decidability=DecidabilityKind.SEMI_DECIDABLE,
        )
        synthesis_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["SYNTHESIS"],
            required_channels=("TESTING", "FORMAL_PROOF", "TYPE_CHECKING", "BENCHMARK"),
            minimum_trust_level=0.8,
            conjunction_mode=ConjunctionMode.CRITICAL_PATH,
            allowed_residuals=("COUNTEREXAMPLE_GUIDED_REFINEMENT",),
            forbidden_residuals=("UNSOUND", "INCOMPLETE"),
            temporal_constraints={"ordering": "TESTING_before_FORMAL_PROOF"},
            override_conditions=(),
        )
        catalog.register_problem_class(synthesis, synthesis_sig, synthesis_req)

        # ---- REPAIR ----
        repair = ProblemClass(
            class_id=ids["REPAIR"],
            name="REPAIR",
            description=(
                "Given a broken artifact (program with bugs, inconsistent database, "
                "failing proof) and a correctness criterion, produce a minimally "
                "modified artifact that satisfies the criterion."
            ),
            category=ProblemCategory.CONSTRUCTIVE,
            difficulty_level=DifficultyLevel.HARD,
            parent_classes=(ids["CONSTRUCTION"],),
            child_classes=(),
            canonical_instances=(
                {"name": "PROGRAM_REPAIR", "artifact": "buggy_program", "spec": "test_suite"},
                {"name": "ALLOY_REPAIR", "artifact": "broken_model", "spec": "invariant"},
            ),
            complexity_notes="NP-hard for syntactic repair; harder for semantic repair",
            required_evidence_kinds=("TESTING", "FORMAL_PROOF", "DIFFERENTIAL_TESTING"),
        )
        repair_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["REPAIR"],
            input_schema={
                "type": "object",
                "required": ["artifact", "correctness_criterion"],
                "properties": {
                    "artifact": {"type": "object"},
                    "correctness_criterion": {"type": "object"},
                    "minimality_metric": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["repaired_artifact"],
                "properties": {
                    "repaired_artifact": {"type": "object"},
                    "diff": {"type": "array"},
                    "correctness_proof": {"type": "object"},
                },
            },
            preconditions=("artifact", "correctness_criterion"),
            postconditions=("repaired_artifact",),
            invariants=("correctness_criterion_unchanged",),
            side_effects=("repaired_artifact_persisted",),
            complexity_class="NP",
            decidability=DecidabilityKind.SEMI_DECIDABLE,
        )
        repair_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["REPAIR"],
            required_channels=("TESTING", "FORMAL_PROOF", "DIFFERENTIAL_TESTING"),
            minimum_trust_level=0.75,
            conjunction_mode=ConjunctionMode.MAJORITY,
            allowed_residuals=("KNOWN_LIMITATION",),
            forbidden_residuals=("REGRESSION_INTRODUCED",),
            temporal_constraints={"ordering": "DIFFERENTIAL_TESTING_before_FORMAL_PROOF"},
            override_conditions=("ALL_TESTS_PASS",),
        )
        catalog.register_problem_class(repair, repair_sig, repair_req)

        # ---- CLASSIFICATION ----
        classification = ProblemClass(
            class_id=ids["CLASSIFICATION"],
            name="CLASSIFICATION",
            description=(
                "Given a labeled training set and an unseen instance, assign the "
                "instance to one of a fixed set of classes.  Covers both rule-based "
                "and learned classifiers; central to the RELATIONAL problem category."
            ),
            category=ProblemCategory.RELATIONAL,
            difficulty_level=DifficultyLevel.MODERATE,
            parent_classes=(),
            child_classes=(),
            canonical_instances=(
                {"name": "BINARY_CLASSIFICATION", "features": "vector", "classes": ["pos", "neg"]},
                {"name": "MULTICLASS", "features": "vector", "classes": ["c1", "c2", "c3"]},
            ),
            complexity_notes="P for linear classifiers; NP-hard for optimal decision trees",
            required_evidence_kinds=("STATISTICAL_TESTING", "BENCHMARK", "PEER_REVIEW"),
        )
        classification_sig = SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=ids["CLASSIFICATION"],
            input_schema={
                "type": "object",
                "required": ["instance", "label_set"],
                "properties": {
                    "instance": {"type": "object"},
                    "label_set": {"type": "array"},
                    "classifier": {"type": "object"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["label"],
                "properties": {
                    "label": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
            preconditions=("instance", "label_set"),
            postconditions=("label",),
            invariants=("label_set_fixed",),
            side_effects=(),
            complexity_class="P",
            decidability=DecidabilityKind.DECIDABLE,
        )
        classification_req = EvidenceRequirement(
            req_id=str(uuid.uuid4()),
            problem_class_id=ids["CLASSIFICATION"],
            required_channels=("STATISTICAL_TESTING", "BENCHMARK", "PEER_REVIEW"),
            minimum_trust_level=0.7,
            conjunction_mode=ConjunctionMode.ANY,
            allowed_residuals=("DISTRIBUTION_SHIFT_WARNING",),
            forbidden_residuals=("LABEL_LEAKAGE",),
            temporal_constraints={"ordering": "BENCHMARK_before_PEER_REVIEW"},
            override_conditions=("CERTIFIED_EVALUATION",),
        )
        catalog.register_problem_class(
            classification, classification_sig, classification_req
        )

        return catalog


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "ProblemCategory",
    "DifficultyLevel",
    "DecidabilityKind",
    "ConjunctionMode",
    # Domain models
    "ProblemClass",
    "SemanticSignature",
    "EvidenceRequirement",
    "AtlasCatalog",
    # Type aliases
    "ClassRegistry",
    "EvidenceMap",
    "JsonSchema",
]

# copilot: shared-core marker for future LLM orchestration.
