"""Section 14.2 — Semantic Signatures for the Unified Problem Atlas.

copilot: semantic signature matching and composition engine.

This module implements §14.2 of Theory2.tex, providing the complete machinery
for constructing, composing, and matching semantic signatures.  A *semantic
signature* characterizes a problem in terms of its input/output type schemas,
preconditions, postconditions, invariants, and side effects.

Signatures support:
  - Type-level compatibility checking (can output of A feed input of B?)
  - Precondition/postcondition verification via symbolic evaluation
  - Sequential composition (pipeline construction)
  - Restriction (specialization) and generalization
  - Canonical hashing for fast indexing

Key components:
  SignatureBuilder       — Fluent builder for SemanticSignature instances
  SignatureComposer      — Composes signatures for pipeline and product problems
  SignatureMatcher       — Checks compatibility between two signatures
  PreconditionChecker    — Evaluates preconditions against runtime context
  SignatureNormalizer    — Puts signatures in a canonical normal form
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        SemanticSignature,
        DecidabilityKind,
        ProblemClass,
        ProblemCategory,
        DifficultyLevel,
    )
except ImportError:
    SemanticSignature = object  # type: ignore[assignment,misc]
    DecidabilityKind = None  # type: ignore[assignment]
    ProblemClass = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    DifficultyLevel = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.problem_atlas.problem_classes import (
        ProblemCategory,
        DifficultyLevel,
        DecidabilityKind,
        ProblemClass,
        ProblemKind,
        STANDARD_PROBLEM_CLASSES,
    )
except ImportError:
    ProblemCategory = None  # type: ignore[assignment]
    DifficultyLevel = None  # type: ignore[assignment]
    DecidabilityKind = None  # type: ignore[assignment]
    ProblemClass = object  # type: ignore[assignment,misc]
    ProblemKind = None  # type: ignore[assignment]
    STANDARD_PROBLEM_CLASSES = {}  # type: ignore[assignment]

# ═══════════════════════════════════════════════════════════════════════════
# §1  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

SigId: TypeAlias = str
SchemaDict: TypeAlias = dict[str, Any]
ConditionStr: TypeAlias = str

# ═══════════════════════════════════════════════════════════════════════════
# §2  Enumerations
# ═══════════════════════════════════════════════════════════════════════════


class SchemaKind(str, Enum):
    """Classification of a field's type in an I/O schema.

    Describes the structural shape of a schema field, used when checking
    type-level compatibility between signatures.

    Attributes:
        PRIMITIVE: A scalar value (int, float, bool, string).
        RECORD: A structured mapping with named fields (object/dict).
        ARRAY: An ordered sequence of homogeneous elements.
        UNION: A value that may be one of several types.
        OPTIONAL: A value that may be present or absent (nullable).
        REFERENCE: A reference to another named schema by identifier.
        ANY: An unconstrained value; compatible with all other kinds.
    """

    PRIMITIVE = "primitive"
    RECORD = "record"
    ARRAY = "array"
    UNION = "union"
    OPTIONAL = "optional"
    REFERENCE = "reference"
    ANY = "any"

    def is_composite(self) -> bool:
        """Return ``True`` when this kind contains sub-structure.

        Returns:
            True for RECORD, ARRAY, UNION, and OPTIONAL.
        """
        return self in {
            SchemaKind.RECORD,
            SchemaKind.ARRAY,
            SchemaKind.UNION,
            SchemaKind.OPTIONAL,
        }

    def allows_null(self) -> bool:
        """Return ``True`` when a ``None`` value is permitted.

        Returns:
            True for OPTIONAL, UNION, and ANY.
        """
        return self in {SchemaKind.OPTIONAL, SchemaKind.UNION, SchemaKind.ANY}

    def is_compatible_with(self, other: "SchemaKind") -> bool:
        """Return ``True`` when *self* can be consumed where *other* is expected.

        ANY accepts anything; OPTIONAL is compatible with PRIMITIVE of same base;
        strict structural equality otherwise.

        Args:
            other: The expected SchemaKind.

        Returns:
            True when *self* satisfies the constraint imposed by *other*.
        """
        if other == SchemaKind.ANY or self == SchemaKind.ANY:
            return True
        if other == SchemaKind.OPTIONAL:
            # optional accepts the underlying type or null
            return True
        return self == other


class SignatureKind(str, Enum):
    """Structural role classification of a SemanticSignature.

    Attributes:
        ATOMIC: A primitive, non-decomposable signature for one problem step.
        COMPOSITE: A signature produced by composing two or more signatures.
        PARAMETERIZED: A signature template with free type parameters.
        ABSTRACT: A signature that describes an interface, not a concrete class.
        CONCRETE: A fully instantiated signature for a specific problem class.
    """

    ATOMIC = "atomic"
    COMPOSITE = "composite"
    PARAMETERIZED = "parameterized"
    ABSTRACT = "abstract"
    CONCRETE = "concrete"

    def is_instantiable(self) -> bool:
        """Return ``True`` when this signature can be used directly.

        Returns:
            True for ATOMIC, COMPOSITE, and CONCRETE.
        """
        return self in {SignatureKind.ATOMIC, SignatureKind.COMPOSITE, SignatureKind.CONCRETE}

    def requires_specialization(self) -> bool:
        """Return ``True`` when specialization is needed before use.

        Returns:
            True for PARAMETERIZED and ABSTRACT.
        """
        return self in {SignatureKind.PARAMETERIZED, SignatureKind.ABSTRACT}


class SemanticCompatibility(str, Enum):
    """Result category of a compatibility check between two signatures.

    Attributes:
        FULLY_COMPATIBLE: All input/output types and conditions align perfectly.
        PARTIALLY_COMPATIBLE: Some fields are compatible; gaps exist but can be bridged.
        INCOMPATIBLE: Fundamental type or condition conflicts make composition impossible.
        UNKNOWN: Compatibility could not be determined (e.g., parameterised types).
    """

    FULLY_COMPATIBLE = "fully_compatible"
    PARTIALLY_COMPATIBLE = "partially_compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"

    def is_usable(self) -> bool:
        """Return ``True`` when the result permits attempted composition.

        Returns:
            True for FULLY_COMPATIBLE and PARTIALLY_COMPATIBLE.
        """
        return self in {
            SemanticCompatibility.FULLY_COMPATIBLE,
            SemanticCompatibility.PARTIALLY_COMPATIBLE,
        }


class CompositionStrategy(str, Enum):
    """How multiple signatures are combined into one.

    Attributes:
        SEQUENTIAL: Output of the first feeds the input of the second (pipeline).
        PARALLEL: Both run concurrently; schemas are merged by union.
        PRODUCT: Cartesian product of all schemas; all signatures run independently.
        SUM: At most one branch executes; schemas are unified via UNION type.
        CONDITIONAL: One branch executes depending on a boolean condition.
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    PRODUCT = "product"
    SUM = "sum"
    CONDITIONAL = "conditional"

    def supports_output_feed(self) -> bool:
        """Return ``True`` when this strategy connects output of one to input of another.

        Returns:
            True only for SEQUENTIAL and CONDITIONAL.
        """
        return self in {CompositionStrategy.SEQUENTIAL, CompositionStrategy.CONDITIONAL}

    def is_parallelizable(self) -> bool:
        """Return ``True`` when component signatures may execute concurrently.

        Returns:
            True for PARALLEL and PRODUCT.
        """
        return self in {CompositionStrategy.PARALLEL, CompositionStrategy.PRODUCT}


# ═══════════════════════════════════════════════════════════════════════════
# §3  Core Dataclasses
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    """Detailed result of a signature compatibility check.

    Records not just whether two signatures are compatible, but *why* and
    *to what degree*, making it useful for diagnostic and ranking purposes.

    Attributes:
        compatible: Overall boolean verdict — True when composition is possible.
        reason: Human-readable explanation of the compatibility assessment.
        input_gaps: Fields required by the target that are absent in the source output.
        output_gaps: Fields expected by callers that are absent in the target output.
        precondition_conflicts: Source preconditions that conflict with target's.
        score: Compatibility score in [0.0, 1.0]; 1.0 is perfect alignment.
    """

    compatible: bool
    reason: str
    input_gaps: tuple[str, ...]
    output_gaps: tuple[str, ...]
    precondition_conflicts: tuple[str, ...]
    score: float

    def is_full_match(self) -> bool:
        """Return ``True`` when the match is perfect with no gaps or conflicts.

        Returns:
            True when compatible, all gap tuples are empty, and score == 1.0.
        """
        return (
            self.compatible
            and not self.input_gaps
            and not self.output_gaps
            and not self.precondition_conflicts
            and abs(self.score - 1.0) < 1e-9
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with all fields as JSON primitives.
        """
        return {
            "compatible": self.compatible,
            "reason": self.reason,
            "input_gaps": list(self.input_gaps),
            "output_gaps": list(self.output_gaps),
            "precondition_conflicts": list(self.precondition_conflicts),
            "score": self.score,
        }

    @classmethod
    def full_match(cls) -> "CompatibilityResult":
        """Convenience constructor for a perfect compatibility result.

        Returns:
            CompatibilityResult with score=1.0 and no gaps.
        """
        return cls(
            compatible=True,
            reason="Full match: all fields and conditions align.",
            input_gaps=(),
            output_gaps=(),
            precondition_conflicts=(),
            score=1.0,
        )

    @classmethod
    def incompatible(cls, reason: str) -> "CompatibilityResult":
        """Convenience constructor for an incompatibility result.

        Args:
            reason: Explanation of why the signatures are incompatible.

        Returns:
            CompatibilityResult with compatible=False and score=0.0.
        """
        return cls(
            compatible=False,
            reason=reason,
            input_gaps=(),
            output_gaps=(),
            precondition_conflicts=(),
            score=0.0,
        )


@dataclass(frozen=True, slots=True)
class IOSchema:
    """Typed input/output schema for a semantic signature.

    Represents the type structure of inputs or outputs as a JSON Schema-like
    record.  Each field maps a name to a type descriptor string.

    Attributes:
        schema_id: Unique identifier for this schema.
        fields: Mapping from field name to type descriptor string.
        required_fields: Fields that must be present in every instance.
        optional_fields: Fields that may be absent.
        description: Human-readable description of this schema.
        schema_kind: The structural kind of the top-level schema.
    """

    schema_id: str
    fields: Mapping[str, str]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    description: str = ""
    schema_kind: SchemaKind = SchemaKind.RECORD

    def has_field(self, name: str) -> bool:
        """Check whether *name* is a declared field.

        Args:
            name: The field name to check.

        Returns:
            True when *name* is in the fields mapping.
        """
        return name in self.fields

    def get_required(self) -> frozenset[str]:
        """Return the set of required field names.

        Returns:
            FrozenSet of required field name strings.
        """
        return frozenset(self.required_fields)

    def is_compatible_with(self, other: "IOSchema") -> bool:
        """Check if *other*'s required fields are all provided by *self*.

        Used to determine if the output schema of one step can feed the input
        schema of the next step in a pipeline.

        Args:
            other: The target schema whose requirements must be met.

        Returns:
            True when all of *other*'s required fields are present in *self*.
        """
        return other.get_required().issubset(frozenset(self.fields.keys()))

    def missing_fields(self, other: "IOSchema") -> frozenset[str]:
        """Return the fields required by *other* that are absent from *self*.

        Args:
            other: The schema whose requirements are checked against *self*.

        Returns:
            FrozenSet of field names that *other* requires but *self* lacks.
        """
        return other.get_required() - frozenset(self.fields.keys())

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with schema_id, fields, required_fields, optional_fields.
        """
        return {
            "schema_id": self.schema_id,
            "fields": dict(self.fields),
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "description": self.description,
            "schema_kind": self.schema_kind.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IOSchema":
        """Deserialize from a JSON-safe dictionary.

        Args:
            data: Dict previously produced by ``to_dict()``.

        Returns:
            Reconstructed IOSchema instance.
        """
        return cls(
            schema_id=data.get("schema_id", str(uuid.uuid4())),
            fields=data.get("fields", {}),
            required_fields=tuple(data.get("required_fields", [])),
            optional_fields=tuple(data.get("optional_fields", [])),
            description=data.get("description", ""),
            schema_kind=SchemaKind(data.get("schema_kind", SchemaKind.RECORD.value)),
        )

    @classmethod
    def from_simple_dict(cls, schema: dict[str, Any]) -> "IOSchema":
        """Build an IOSchema from a simple ``{field: type}`` dict.

        Args:
            schema: Dict mapping field names to type descriptor strings.

        Returns:
            IOSchema treating all fields as required.
        """
        fields = {k: str(v) for k, v in schema.items()}
        return cls(
            schema_id=str(uuid.uuid4()),
            fields=fields,
            required_fields=tuple(fields.keys()),
            optional_fields=(),
        )


@dataclass(frozen=True, slots=True)
class SemanticSignature:
    """Full semantic signature characterising a computational problem.

    A SemanticSignature is the authoritative typed contract for a problem class,
    combining input/output type schemas with formal behavioural conditions.

    Attributes:
        sig_id: Unique identifier for this signature.
        problem_class_id: ID of the problem class this signature belongs to.
        input_schema: Mapping describing accepted input fields and types.
        output_schema: Mapping describing produced output fields and types.
        preconditions: Tuple of condition strings that must hold before execution.
        postconditions: Tuple of condition strings guaranteed after execution.
        invariants: Tuple of condition strings that hold throughout execution.
        side_effects: Observable state changes produced by execution.
        complexity_class: Informal complexity class (e.g., ``P``, ``NP``, ``#P``).
        decidability: Decidability classification string.
        kind: The SignatureKind of this signature.
        description: Human-readable description of what this signature contracts.
    """

    sig_id: str
    problem_class_id: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    invariants: tuple[str, ...]
    side_effects: tuple[str, ...]
    complexity_class: str
    decidability: str
    kind: SignatureKind = SignatureKind.ATOMIC
    description: str = ""

    def input_keys(self) -> frozenset[str]:
        """Return the set of input schema field names.

        Returns:
            FrozenSet of all field names declared in the input schema.
        """
        return frozenset(self.input_schema.keys())

    def output_keys(self) -> frozenset[str]:
        """Return the set of output schema field names.

        Returns:
            FrozenSet of all field names declared in the output schema.
        """
        return frozenset(self.output_schema.keys())

    def is_pure(self) -> bool:
        """Return ``True`` when this signature has no side effects.

        Returns:
            True when ``side_effects`` is empty.
        """
        return len(self.side_effects) == 0

    def can_feed(self, other: "SemanticSignature") -> bool:
        """Return ``True`` when this signature's output can feed *other*'s input.

        Checks that every field declared in *other*'s input schema is present
        in this signature's output schema.

        Args:
            other: The downstream signature whose input must be satisfied.

        Returns:
            True when output_keys() ⊇ other.input_keys().
        """
        return other.input_keys().issubset(self.output_keys())

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with all fields as JSON primitives.
        """
        return {
            "sig_id": self.sig_id,
            "problem_class_id": self.problem_class_id,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "invariants": list(self.invariants),
            "side_effects": list(self.side_effects),
            "complexity_class": self.complexity_class,
            "decidability": self.decidability,
            "kind": self.kind.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticSignature":
        """Deserialize from a JSON-safe dictionary.

        Args:
            data: Dict previously produced by ``to_dict()``.

        Returns:
            Reconstructed SemanticSignature instance.

        Raises:
            KeyError: If required keys are absent from *data*.
        """
        return cls(
            sig_id=data.get("sig_id", str(uuid.uuid4())),
            problem_class_id=data.get("problem_class_id", ""),
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            preconditions=tuple(data.get("preconditions", [])),
            postconditions=tuple(data.get("postconditions", [])),
            invariants=tuple(data.get("invariants", [])),
            side_effects=tuple(data.get("side_effects", [])),
            complexity_class=data.get("complexity_class", "UNKNOWN"),
            decidability=data.get("decidability", "decidable"),
            kind=SignatureKind(data.get("kind", SignatureKind.ATOMIC.value)),
            description=data.get("description", ""),
        )


@dataclass(frozen=True, slots=True)
class SemanticContract:
    """Full semantic contract binding a signature to behavioural guarantees.

    A SemanticContract extends a SemanticSignature by adding formal guarantees
    about determinism and purity, linking the signature to its problem class
    and recording its contract ID for reference.

    Attributes:
        contract_id: Unique identifier for this contract.
        signature: The underlying SemanticSignature.
        problem_class_id: The ID of the problem class this contract is for.
        is_deterministic: Whether the problem always produces the same output
            for the same input.
        is_total: Whether the problem is defined on all possible inputs.
        notes: Additional free-form notes about this contract.
    """

    contract_id: str
    signature: SemanticSignature
    problem_class_id: str
    is_deterministic: bool = True
    is_total: bool = True
    notes: str = ""

    def is_pure(self) -> bool:
        """Return ``True`` when the underlying signature is pure.

        Returns:
            True when ``self.signature.is_pure()`` holds.
        """
        return self.signature.is_pure()

    def is_functional(self) -> bool:
        """Return ``True`` when the contract is deterministic, total, and pure.

        Returns:
            True when all three properties hold simultaneously.
        """
        return self.is_deterministic and self.is_total and self.is_pure()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            Dict with all fields as JSON primitives.
        """
        return {
            "contract_id": self.contract_id,
            "signature": self.signature.to_dict(),
            "problem_class_id": self.problem_class_id,
            "is_deterministic": self.is_deterministic,
            "is_total": self.is_total,
            "notes": self.notes,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §4  SignatureBuilder — fluent builder
# ═══════════════════════════════════════════════════════════════════════════


class SignatureBuilder:
    """Fluent builder for constructing ``SemanticSignature`` instances.

    Provides a chainable API for progressively specifying all attributes of a
    semantic signature before calling ``build()`` to produce the immutable record.

    Example::

        sig = (
            SignatureBuilder()
            .for_problem_class("SEARCH")
            .with_input_schema({"items": "array", "predicate": "callable"})
            .with_output_schema({"result": "any", "found": "bool"})
            .with_precondition("items is not None")
            .with_postcondition("found == True implies result in items")
            .with_complexity_class("O(n)")
            .build()
        )
    """

    def __init__(self, sig_id: str | None = None) -> None:
        """Initialise the builder with an optional signature ID.

        Args:
            sig_id: Optional ID for the signature.  Auto-generated if not given.
        """
        self._sig_id: str = sig_id if sig_id is not None else str(uuid.uuid4())
        self._problem_class_id: str = ""
        self._input_schema: dict[str, Any] = {}
        self._output_schema: dict[str, Any] = {}
        self._preconditions: list[str] = []
        self._postconditions: list[str] = []
        self._invariants: list[str] = []
        self._side_effects: list[str] = []
        self._complexity_class: str = "UNKNOWN"
        self._decidability: str = "decidable"
        self._kind: SignatureKind = SignatureKind.ATOMIC
        self._description: str = ""

    def for_problem_class(self, class_id: str) -> "SignatureBuilder":
        """Bind this signature to a problem class.

        Args:
            class_id: The class_id of the problem class.

        Returns:
            ``self`` for method chaining.
        """
        self._problem_class_id = class_id
        return self

    def with_input_schema(self, schema: dict[str, Any]) -> "SignatureBuilder":
        """Set the input schema dict.

        Args:
            schema: Dict mapping field names to type descriptors.

        Returns:
            ``self`` for method chaining.
        """
        self._input_schema = dict(schema)
        return self

    def with_output_schema(self, schema: dict[str, Any]) -> "SignatureBuilder":
        """Set the output schema dict.

        Args:
            schema: Dict mapping field names to type descriptors.

        Returns:
            ``self`` for method chaining.
        """
        self._output_schema = dict(schema)
        return self

    def with_precondition(self, condition: str) -> "SignatureBuilder":
        """Add a precondition string.

        Args:
            condition: A condition that must hold before execution.

        Returns:
            ``self`` for method chaining.
        """
        if condition and condition not in self._preconditions:
            self._preconditions.append(condition)
        return self

    def with_postcondition(self, condition: str) -> "SignatureBuilder":
        """Add a postcondition string.

        Args:
            condition: A condition guaranteed to hold after successful execution.

        Returns:
            ``self`` for method chaining.
        """
        if condition and condition not in self._postconditions:
            self._postconditions.append(condition)
        return self

    def with_invariant(self, invariant: str) -> "SignatureBuilder":
        """Add an invariant that holds throughout execution.

        Args:
            invariant: An invariant condition string.

        Returns:
            ``self`` for method chaining.
        """
        if invariant and invariant not in self._invariants:
            self._invariants.append(invariant)
        return self

    def with_side_effect(self, effect: str) -> "SignatureBuilder":
        """Record an observable side effect of execution.

        Args:
            effect: String describing the side effect.

        Returns:
            ``self`` for method chaining.
        """
        if effect and effect not in self._side_effects:
            self._side_effects.append(effect)
        return self

    def with_complexity_class(self, cc: str) -> "SignatureBuilder":
        """Set the informal complexity class.

        Args:
            cc: Complexity class string, e.g. ``P``, ``NP``, ``O(n log n)``.

        Returns:
            ``self`` for method chaining.
        """
        self._complexity_class = cc
        return self

    def with_decidability(self, dk: Any) -> "SignatureBuilder":
        """Set the decidability classification.

        Args:
            dk: A DecidabilityKind enum value or a string representation.

        Returns:
            ``self`` for method chaining.
        """
        self._decidability = dk.value if hasattr(dk, "value") else str(dk)
        return self

    def with_kind(self, kind: SignatureKind) -> "SignatureBuilder":
        """Set the signature kind.

        Args:
            kind: The SignatureKind for this signature.

        Returns:
            ``self`` for method chaining.
        """
        self._kind = kind
        return self

    def with_description(self, description: str) -> "SignatureBuilder":
        """Set the human-readable description.

        Args:
            description: Prose explanation of what this signature contracts.

        Returns:
            ``self`` for method chaining.
        """
        self._description = description
        return self

    def build(self) -> SemanticSignature:
        """Validate and construct the immutable SemanticSignature.

        Returns:
            A fully constructed, frozen SemanticSignature.

        Raises:
            ValueError: When the problem_class_id is empty.
        """
        if not self._problem_class_id.strip():
            raise ValueError(
                "SemanticSignature must be bound to a problem class via "
                "for_problem_class()."
            )
        return SemanticSignature(
            sig_id=self._sig_id,
            problem_class_id=self._problem_class_id,
            input_schema=dict(self._input_schema),
            output_schema=dict(self._output_schema),
            preconditions=tuple(self._preconditions),
            postconditions=tuple(self._postconditions),
            invariants=tuple(self._invariants),
            side_effects=tuple(self._side_effects),
            complexity_class=self._complexity_class,
            decidability=self._decidability,
            kind=self._kind,
            description=self._description,
        )


# ═══════════════════════════════════════════════════════════════════════════
# §5  SignatureComposer — composing signatures into pipelines
# ═══════════════════════════════════════════════════════════════════════════


class SignatureComposer:
    """Composes two or more SemanticSignature instances into a combined signature.

    Supports sequential (pipeline), parallel, product, and conditional
    composition strategies.  Each composed signature records the strategy and
    merges the constituent schemas and conditions accordingly.
    """

    def __init__(self) -> None:
        """Initialise the composer with no state."""

    def compose_sequential(
        self,
        first: SemanticSignature,
        second: SemanticSignature,
    ) -> SemanticSignature:
        """Compose *first* and *second* as a sequential pipeline.

        The output of *first* feeds the input of *second*.  The composed
        signature's input schema is *first*'s input; its output schema is
        *second*'s output.  Conditions are merged from both signatures.

        Args:
            first: The upstream (producer) signature.
            second: The downstream (consumer) signature.

        Returns:
            A new SemanticSignature representing the sequential composition.

        Raises:
            ValueError: When the output of *first* cannot feed the input of *second*.
        """
        missing = second.input_keys() - first.output_keys()
        if missing:
            raise ValueError(
                f"Sequential composition failed: '{first.problem_class_id}' output "
                f"is missing fields required by '{second.problem_class_id}' input: "
                f"{sorted(missing)}"
            )
        composed_id = f"{first.problem_class_id}_THEN_{second.problem_class_id}"
        return SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=composed_id,
            input_schema=dict(first.input_schema),
            output_schema=dict(second.output_schema),
            preconditions=self._merge_conditions(first.preconditions, second.preconditions),
            postconditions=self._merge_conditions(first.postconditions, second.postconditions),
            invariants=self._merge_conditions(first.invariants, second.invariants),
            side_effects=self._merge_conditions(first.side_effects, second.side_effects),
            complexity_class=f"seq({first.complexity_class}, {second.complexity_class})",
            decidability=_compose_decidability(first.decidability, second.decidability),
            kind=SignatureKind.COMPOSITE,
            description=(
                f"Sequential composition of '{first.problem_class_id}' "
                f"then '{second.problem_class_id}'."
            ),
        )

    def compose_parallel(
        self,
        left: SemanticSignature,
        right: SemanticSignature,
    ) -> SemanticSignature:
        """Compose *left* and *right* as parallel concurrent signatures.

        Both run concurrently; input and output schemas are merged by union.
        Conditions from both signatures are merged.

        Args:
            left: The left-branch signature.
            right: The right-branch signature.

        Returns:
            A new SemanticSignature representing the parallel composition.
        """
        merged_input = self._merge_schemas(dict(left.input_schema), dict(right.input_schema))
        merged_output = self._merge_schemas(dict(left.output_schema), dict(right.output_schema))
        composed_id = f"{left.problem_class_id}_PAR_{right.problem_class_id}"
        return SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=composed_id,
            input_schema=merged_input,
            output_schema=merged_output,
            preconditions=self._merge_conditions(left.preconditions, right.preconditions),
            postconditions=self._merge_conditions(left.postconditions, right.postconditions),
            invariants=self._merge_conditions(left.invariants, right.invariants),
            side_effects=self._merge_conditions(left.side_effects, right.side_effects),
            complexity_class=f"par({left.complexity_class}, {right.complexity_class})",
            decidability=_compose_decidability(left.decidability, right.decidability),
            kind=SignatureKind.COMPOSITE,
            description=(
                f"Parallel composition of '{left.problem_class_id}' "
                f"and '{right.problem_class_id}'."
            ),
        )

    def compose_product(self, sigs: list[SemanticSignature]) -> SemanticSignature:
        """Compose a list of signatures as a product (Cartesian product).

        All signatures run independently with their own inputs/outputs; the
        composed schema is the union of all schemas.  No output feeds any input.

        Args:
            sigs: List of SemanticSignature instances to compose.

        Returns:
            A new SemanticSignature representing the product composition.

        Raises:
            ValueError: When *sigs* is empty.
        """
        if not sigs:
            raise ValueError("compose_product requires at least one signature.")
        if len(sigs) == 1:
            return sigs[0]
        result_input: dict[str, Any] = {}
        result_output: dict[str, Any] = {}
        all_pre: tuple[str, ...] = ()
        all_post: tuple[str, ...] = ()
        all_inv: tuple[str, ...] = ()
        all_se: tuple[str, ...] = ()
        complexity_parts: list[str] = []
        decidabilities: list[str] = []
        class_ids: list[str] = []
        for sig in sigs:
            result_input = self._merge_schemas(result_input, dict(sig.input_schema))
            result_output = self._merge_schemas(result_output, dict(sig.output_schema))
            all_pre = self._merge_conditions(all_pre, sig.preconditions)
            all_post = self._merge_conditions(all_post, sig.postconditions)
            all_inv = self._merge_conditions(all_inv, sig.invariants)
            all_se = self._merge_conditions(all_se, sig.side_effects)
            complexity_parts.append(sig.complexity_class)
            decidabilities.append(sig.decidability)
            class_ids.append(sig.problem_class_id)
        composed_id = "PRODUCT_OF_" + "_X_".join(class_ids[:4])
        decidability = _compose_decidability(*decidabilities)
        return SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=composed_id,
            input_schema=result_input,
            output_schema=result_output,
            preconditions=all_pre,
            postconditions=all_post,
            invariants=all_inv,
            side_effects=all_se,
            complexity_class="product(" + ", ".join(complexity_parts) + ")",
            decidability=decidability,
            kind=SignatureKind.COMPOSITE,
            description=f"Product composition of {len(sigs)} signatures.",
        )

    def compose_conditional(
        self,
        condition_sig: SemanticSignature,
        then_sig: SemanticSignature,
        else_sig: SemanticSignature,
    ) -> SemanticSignature:
        """Compose an if-then-else conditional over three signatures.

        ``condition_sig`` produces a boolean; ``then_sig`` runs when it is True,
        ``else_sig`` when it is False.  The composed input is the union of all
        inputs; output is the union of ``then_sig`` and ``else_sig`` outputs.

        Args:
            condition_sig: Signature producing the boolean branch condition.
            then_sig: Signature executed on the True branch.
            else_sig: Signature executed on the False branch.

        Returns:
            A new SemanticSignature representing the conditional composition.
        """
        merged_input = self._merge_schemas(
            self._merge_schemas(dict(condition_sig.input_schema), dict(then_sig.input_schema)),
            dict(else_sig.input_schema),
        )
        merged_output = self._merge_schemas(
            dict(then_sig.output_schema), dict(else_sig.output_schema)
        )
        cond_id = f"IF_{condition_sig.problem_class_id}_THEN_{then_sig.problem_class_id}_ELSE_{else_sig.problem_class_id}"
        all_pre = self._merge_conditions(
            self._merge_conditions(condition_sig.preconditions, then_sig.preconditions),
            else_sig.preconditions,
        )
        all_post = self._merge_conditions(then_sig.postconditions, else_sig.postconditions)
        all_inv = self._merge_conditions(
            self._merge_conditions(condition_sig.invariants, then_sig.invariants),
            else_sig.invariants,
        )
        all_se = self._merge_conditions(
            self._merge_conditions(condition_sig.side_effects, then_sig.side_effects),
            else_sig.side_effects,
        )
        return SemanticSignature(
            sig_id=str(uuid.uuid4()),
            problem_class_id=cond_id,
            input_schema=merged_input,
            output_schema=merged_output,
            preconditions=all_pre,
            postconditions=all_post,
            invariants=all_inv,
            side_effects=all_se,
            complexity_class=(
                f"cond({condition_sig.complexity_class}, "
                f"{then_sig.complexity_class}, {else_sig.complexity_class})"
            ),
            decidability=_compose_decidability(
                condition_sig.decidability, then_sig.decidability, else_sig.decidability
            ),
            kind=SignatureKind.COMPOSITE,
            description=f"Conditional composition guarded by '{condition_sig.problem_class_id}'.",
        )

    def _merge_schemas(
        self,
        schema_a: dict[str, Any],
        schema_b: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two JSON schemas by unioning their fields.

        When both schemas declare the same field, the type from *schema_b* takes
        precedence.  The merged schema contains all fields from both inputs.

        Args:
            schema_a: First schema dict.
            schema_b: Second schema dict.

        Returns:
            Merged schema dict containing keys from both inputs.
        """
        merged: dict[str, Any] = dict(schema_a)
        for key, val in schema_b.items():
            if key not in merged:
                merged[key] = val
            else:
                # Both declare this field — create a UNION type descriptor
                existing = merged[key]
                if existing != val:
                    merged[key] = f"union({existing}, {val})"
        return merged

    def _merge_conditions(
        self,
        conds_a: tuple[str, ...],
        conds_b: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Merge two condition tuples by deduplication and concatenation.

        Args:
            conds_a: First tuple of condition strings.
            conds_b: Second tuple of condition strings.

        Returns:
            Tuple containing all unique conditions from both inputs, in order.
        """
        seen: set[str] = set()
        result: list[str] = []
        for cond in conds_a + conds_b:
            if cond and cond not in seen:
                seen.add(cond)
                result.append(cond)
        return tuple(result)


# ═══════════════════════════════════════════════════════════════════════════
# §6  SignatureMatcher — compatibility checking
# ═══════════════════════════════════════════════════════════════════════════


class SignatureMatcher:
    """Checks and scores compatibility between pairs of SemanticSignatures.

    In strict mode, every output field of the source must match a required
    input field of the target.  In non-strict mode, a partial overlap is
    considered compatible with a reduced score.

    Args:
        strict: When True, require perfect field alignment for compatibility.
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialise the matcher.

        Args:
            strict: If True, partial matches are treated as incompatible.
        """
        self._strict = strict

    def check_compatibility(
        self,
        source: SemanticSignature,
        target: SemanticSignature,
    ) -> CompatibilityResult:
        """Perform a full compatibility check between *source* and *target*.

        Determines whether the output of *source* can feed the input of *target*
        and whether their conditions are mutually consistent.

        Args:
            source: The producing signature.
            target: The consuming signature.

        Returns:
            CompatibilityResult with a detailed diagnosis.
        """
        source_out = self._schema_keys(dict(source.output_schema))
        target_in = self._schema_keys(dict(target.input_schema))

        # Fields required by target that are absent from source output
        input_gaps = tuple(sorted(target_in - source_out))
        # Fields produced by source that target does not declare
        output_gaps = tuple(sorted(source_out - target_in)) if self._strict else ()

        # Simple precondition conflict detection: look for negations
        pre_conflicts: list[str] = []
        for pre in source.preconditions:
            negated = f"NOT {pre}" if not pre.startswith("NOT ") else pre[4:]
            if negated in target.preconditions:
                pre_conflicts.append(pre)
        precondition_conflicts = tuple(pre_conflicts)

        compatible = (
            not input_gaps
            and not precondition_conflicts
            and (not output_gaps if self._strict else True)
        )

        score = self.score_match(source, target)
        if compatible:
            reason = (
                "Full match." if score >= 1.0 - 1e-9
                else f"Partial match (score={score:.2f}): some output fields unused."
            )
        else:
            parts: list[str] = []
            if input_gaps:
                parts.append(f"missing input fields: {list(input_gaps)}")
            if precondition_conflicts:
                parts.append(f"conflicting preconditions: {list(precondition_conflicts)}")
            if output_gaps:
                parts.append(f"excess output fields: {list(output_gaps)}")
            reason = "Incompatible: " + "; ".join(parts) + "."

        return CompatibilityResult(
            compatible=compatible,
            reason=reason,
            input_gaps=input_gaps,
            output_gaps=output_gaps,
            precondition_conflicts=precondition_conflicts,
            score=score,
        )

    def check_input_subsumption(
        self,
        source: SemanticSignature,
        target: SemanticSignature,
    ) -> bool:
        """Check whether *target*'s input schema is a subset of *source*'s output.

        Returns True when every field required by *target* is provided by *source*.

        Args:
            source: The providing signature.
            target: The receiving signature whose input requirements are checked.

        Returns:
            True when source.output_keys() ⊇ target.input_keys().
        """
        return self._schema_keys(dict(target.input_schema)).issubset(
            self._schema_keys(dict(source.output_schema))
        )

    def check_output_subsumption(
        self,
        source: SemanticSignature,
        target: SemanticSignature,
    ) -> bool:
        """Check whether *source*'s output schema subsumes *target*'s output.

        Returns True when every output field of *target* is also produced by *source*.

        Args:
            source: The richer signature.
            target: The signature whose output requirements must be covered.

        Returns:
            True when source.output_keys() ⊇ target.output_keys().
        """
        return self._schema_keys(dict(target.output_schema)).issubset(
            self._schema_keys(dict(source.output_schema))
        )

    def score_match(
        self,
        source: SemanticSignature,
        target: SemanticSignature,
    ) -> float:
        """Compute a numerical compatibility score in [0.0, 1.0].

        Score is computed as the Jaccard index of the output fields of *source*
        and the input fields of *target*, with a penalty for condition conflicts.

        Args:
            source: The producing signature.
            target: The consuming signature.

        Returns:
            Float in [0.0, 1.0]; 1.0 is perfect alignment.
        """
        source_out = self._schema_keys(dict(source.output_schema))
        target_in = self._schema_keys(dict(target.input_schema))
        if not source_out and not target_in:
            return 1.0
        if not source_out or not target_in:
            return 0.0
        intersection = source_out & target_in
        union = source_out | target_in
        jaccard = len(intersection) / len(union)

        # Penalty for precondition conflicts
        conflict_penalty = 0.0
        for pre in source.preconditions:
            negated = f"NOT {pre}" if not pre.startswith("NOT ") else pre[4:]
            if negated in target.preconditions:
                conflict_penalty += 0.1
        return max(0.0, min(1.0, jaccard - conflict_penalty))

    def find_best_match(
        self,
        sig: SemanticSignature,
        candidates: list[SemanticSignature],
    ) -> SemanticSignature | None:
        """Find the candidate most compatible with *sig* as a downstream consumer.

        Args:
            sig: The producing signature.
            candidates: List of candidate consuming signatures.

        Returns:
            The candidate with the highest compatibility score, or None if
            *candidates* is empty.
        """
        if not candidates:
            return None
        best: SemanticSignature | None = None
        best_score: float = -1.0
        for candidate in candidates:
            result = self.check_compatibility(sig, candidate)
            if result.score > best_score:
                best_score = result.score
                best = candidate
        return best

    def _schema_keys(self, schema: dict[str, Any]) -> frozenset[str]:
        """Extract the set of field names from a schema dict.

        Args:
            schema: A schema dict mapping field names to type descriptors.

        Returns:
            FrozenSet of all field name strings.
        """
        return frozenset(schema.keys())


# ═══════════════════════════════════════════════════════════════════════════
# §7  PreconditionChecker — symbolic precondition evaluation
# ═══════════════════════════════════════════════════════════════════════════


class PreconditionChecker:
    """Evaluates precondition strings against a runtime context dict.

    Supports a simple DSL for conditions:
    - Bare key name: True when context[key] is truthy.
    - ``key == value``: True when str(context[key]) == value (stripped of quotes).
    - ``key != value``: True when str(context[key]) != value.
    - ``key in [v1, v2, ...]``: True when context[key] is in the listed values.
    - ``NOT cond``: Negation of *cond*.
    - ``key > number``, ``key < number``: Numeric comparison.
    """

    def __init__(self) -> None:
        """Initialise the checker with no configuration."""

    def check(
        self,
        preconditions: tuple[str, ...],
        context: dict[str, Any],
    ) -> dict[str, bool]:
        """Evaluate each precondition and return per-condition results.

        Args:
            preconditions: Tuple of condition strings to evaluate.
            context: Runtime context mapping variable names to values.

        Returns:
            Dict mapping each condition string to its boolean evaluation result.
        """
        return {cond: self._evaluate_condition(cond, context) for cond in preconditions}

    def check_all(
        self,
        preconditions: tuple[str, ...],
        context: dict[str, Any],
    ) -> bool:
        """Return ``True`` when every precondition holds.

        Args:
            preconditions: Tuple of condition strings.
            context: Runtime context dict.

        Returns:
            True only when all conditions evaluate to True.
        """
        return all(self._evaluate_condition(cond, context) for cond in preconditions)

    def check_any(
        self,
        preconditions: tuple[str, ...],
        context: dict[str, Any],
    ) -> bool:
        """Return ``True`` when at least one precondition holds.

        Args:
            preconditions: Tuple of condition strings.
            context: Runtime context dict.

        Returns:
            True when at least one condition evaluates to True.
        """
        return any(self._evaluate_condition(cond, context) for cond in preconditions)

    def explain_failures(
        self,
        preconditions: tuple[str, ...],
        context: dict[str, Any],
    ) -> list[str]:
        """Return human-readable explanations for failing conditions.

        Args:
            preconditions: Tuple of condition strings.
            context: Runtime context dict.

        Returns:
            List of strings describing each failing condition and its context value.
        """
        failures: list[str] = []
        for cond in preconditions:
            if not self._evaluate_condition(cond, context):
                failures.append(
                    f"Precondition failed: '{cond}' "
                    f"(context keys available: {sorted(context.keys())})"
                )
        return failures

    def _evaluate_condition(
        self,
        condition: str,
        context: dict[str, Any],
    ) -> bool:
        """Evaluate a single condition string against *context*.

        Supports: bare key, key==value, key!=value, key in [...],
        NOT condition, key > number, key < number, key >= number, key <= number.

        Args:
            condition: The condition string to evaluate.
            context: Runtime context mapping names to values.

        Returns:
            Boolean result of evaluating the condition.
        """
        condition = condition.strip()
        if not condition:
            return True

        # Handle NOT prefix
        if condition.upper().startswith("NOT "):
            inner = condition[4:].strip()
            return not self._evaluate_condition(inner, context)

        # Handle key == value
        eq_match = re.match(r'^(\w+)\s*==\s*(.+)$', condition)
        if eq_match:
            key, value = eq_match.group(1).strip(), eq_match.group(2).strip().strip("'\"")
            ctx_val = context.get(key)
            return str(ctx_val) == value

        # Handle key != value
        neq_match = re.match(r'^(\w+)\s*!=\s*(.+)$', condition)
        if neq_match:
            key, value = neq_match.group(1).strip(), neq_match.group(2).strip().strip("'\"")
            ctx_val = context.get(key)
            return str(ctx_val) != value

        # Handle key in [v1, v2, ...]
        in_match = re.match(r'^(\w+)\s+in\s+\[(.+)\]$', condition)
        if in_match:
            key = in_match.group(1).strip()
            values_str = in_match.group(2)
            values = [v.strip().strip("'\"") for v in values_str.split(",")]
            ctx_val = context.get(key)
            return str(ctx_val) in values

        # Handle key > number
        gt_match = re.match(r'^(\w+)\s*>\s*(-?\d+(?:\.\d+)?)$', condition)
        if gt_match:
            key, threshold = gt_match.group(1).strip(), float(gt_match.group(2))
            ctx_val = context.get(key)
            try:
                return float(ctx_val) > threshold  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False

        # Handle key < number
        lt_match = re.match(r'^(\w+)\s*<\s*(-?\d+(?:\.\d+)?)$', condition)
        if lt_match:
            key, threshold = lt_match.group(1).strip(), float(lt_match.group(2))
            ctx_val = context.get(key)
            try:
                return float(ctx_val) < threshold  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False

        # Handle key >= number
        gte_match = re.match(r'^(\w+)\s*>=\s*(-?\d+(?:\.\d+)?)$', condition)
        if gte_match:
            key, threshold = gte_match.group(1).strip(), float(gte_match.group(2))
            ctx_val = context.get(key)
            try:
                return float(ctx_val) >= threshold  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False

        # Handle key <= number
        lte_match = re.match(r'^(\w+)\s*<=\s*(-?\d+(?:\.\d+)?)$', condition)
        if lte_match:
            key, threshold = lte_match.group(1).strip(), float(lte_match.group(2))
            ctx_val = context.get(key)
            try:
                return float(ctx_val) <= threshold  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False

        # Fall back to truthiness check on key presence
        return bool(context.get(condition))


# ═══════════════════════════════════════════════════════════════════════════
# §8  SignatureNormalizer — canonical normal form
# ═══════════════════════════════════════════════════════════════════════════


class SignatureNormalizer:
    """Puts SemanticSignature instances into a canonical normal form.

    Normalization sorts conditions alphabetically, deduplicates them, and
    sorts schema keys.  The canonical hash enables fast equality checks and
    indexing of signatures.
    """

    def __init__(self) -> None:
        """Initialise the normalizer with no configuration."""

    def normalize(self, sig: SemanticSignature) -> SemanticSignature:
        """Return a canonical normalized copy of *sig*.

        Applies sorting and deduplication to all condition tuples and schema keys.

        Args:
            sig: The SemanticSignature to normalize.

        Returns:
            A new SemanticSignature in canonical normal form.
        """
        return SemanticSignature(
            sig_id=sig.sig_id,
            problem_class_id=sig.problem_class_id,
            input_schema=self.normalize_schema(dict(sig.input_schema)),
            output_schema=self.normalize_schema(dict(sig.output_schema)),
            preconditions=self.deduplicate_conditions(
                self.sort_conditions(sig.preconditions)
            ),
            postconditions=self.deduplicate_conditions(
                self.sort_conditions(sig.postconditions)
            ),
            invariants=self.deduplicate_conditions(
                self.sort_conditions(sig.invariants)
            ),
            side_effects=self.deduplicate_conditions(
                self.sort_conditions(sig.side_effects)
            ),
            complexity_class=sig.complexity_class.strip(),
            decidability=sig.decidability.strip().lower(),
            kind=sig.kind,
            description=sig.description.strip(),
        )

    def sort_conditions(self, conditions: tuple[str, ...]) -> tuple[str, ...]:
        """Return *conditions* sorted alphabetically.

        Args:
            conditions: Tuple of condition strings.

        Returns:
            Tuple of the same strings in case-insensitive alphabetical order.
        """
        return tuple(sorted(conditions, key=str.lower))

    def deduplicate_conditions(
        self, conditions: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Return *conditions* with duplicate entries removed.

        Preserves first-occurrence order after sorting.

        Args:
            conditions: Tuple of condition strings (may contain duplicates).

        Returns:
            Tuple with each distinct condition appearing at most once.
        """
        seen: set[str] = set()
        result: list[str] = []
        for cond in conditions:
            if cond not in seen:
                seen.add(cond)
                result.append(cond)
        return tuple(result)

    def normalize_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a normalized schema with keys sorted and type descriptors lowercased.

        Args:
            schema: Schema dict mapping field names to type descriptor strings.

        Returns:
            New dict with sorted keys and normalized type strings.
        """
        normalized: dict[str, Any] = {}
        for key in sorted(schema.keys()):
            val = schema[key]
            if isinstance(val, str):
                val = val.strip().lower()
            normalized[key] = val
        return normalized

    def canonical_hash(self, sig: SemanticSignature) -> str:
        """Compute the SHA-256 hash of the normalized signature's JSON representation.

        Args:
            sig: The SemanticSignature to hash.

        Returns:
            Hex-encoded SHA-256 digest string (64 characters).
        """
        normalized = self.normalize(sig)
        canonical_dict: dict[str, Any] = {
            "problem_class_id": normalized.problem_class_id,
            "input_schema": normalized.input_schema,
            "output_schema": normalized.output_schema,
            "preconditions": list(normalized.preconditions),
            "postconditions": list(normalized.postconditions),
            "invariants": list(normalized.invariants),
            "side_effects": list(normalized.side_effects),
            "complexity_class": normalized.complexity_class,
            "decidability": normalized.decidability,
        }
        canonical_json = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# §9  Private helpers
# ═══════════════════════════════════════════════════════════════════════════


def _compose_decidability(*decidabilities: str) -> str:
    """Compute the weakest decidability from a collection.

    The order from weakest to strongest: ``undecidable`` < ``co_semi_decidable``
    < ``semi_decidable`` < ``open`` < ``decidable``.

    Args:
        *decidabilities: Variadic decidability strings.

    Returns:
        The weakest decidability string among the inputs.
    """
    _rank: dict[str, int] = {
        "undecidable": 0,
        "co_semi_decidable": 1,
        "semi_decidable": 2,
        "open": 3,
        "decidable": 4,
    }
    weakest = min(decidabilities, key=lambda d: _rank.get(d.lower(), 3))
    return weakest


def _infer_complexity_class(problem_class_id: str) -> str:
    """Infer a complexity class string from a canonical problem class ID.

    Args:
        problem_class_id: The canonical class ID (e.g., ``SEARCH``, ``VERIFICATION``).

    Returns:
        An informal complexity class string.
    """
    _map: dict[str, str] = {
        "SEARCH": "O(n)",
        "OPTIMIZATION": "NP-hard",
        "DECISION": "P",
        "COUNTING": "#P",
        "ENUMERATION": "OutputPoly",
        "CONSTRUCTION": "FP",
        "VERIFICATION": "NP",
        "INFERENCE": "RE",
        "SYNTHESIS": "2EXPTIME",
        "REPAIR": "NP-hard",
        "CLASSIFICATION": "P",
    }
    return _map.get(problem_class_id.upper(), "UNKNOWN")


def _infer_decidability(problem_class_id: str) -> str:
    """Infer a decidability string from a canonical problem class ID.

    Args:
        problem_class_id: The canonical class ID.

    Returns:
        A decidability string.
    """
    _map: dict[str, str] = {
        "INFERENCE": "semi_decidable",
        "SYNTHESIS": "semi_decidable",
    }
    return _map.get(problem_class_id.upper(), "decidable")


# ═══════════════════════════════════════════════════════════════════════════
# §10  STANDARD_SIGNATURES — pre-built signatures for standard classes
# ═══════════════════════════════════════════════════════════════════════════


def _build_standard_signatures() -> dict[str, SemanticSignature]:
    """Build and return the dict of standard semantic signatures.

    Returns:
        Dict mapping canonical class name to SemanticSignature.
    """
    search_sig = (
        SignatureBuilder("SIG_SEARCH")
        .for_problem_class("SEARCH")
        .with_input_schema({"items": "array", "predicate": "callable"})
        .with_output_schema({"result": "any", "found": "bool"})
        .with_precondition("items is not None")
        .with_precondition("predicate is not None")
        .with_postcondition("found == True implies result in items")
        .with_postcondition("found == False implies no element in items satisfies predicate")
        .with_invariant("items length is finite")
        .with_complexity_class("O(n)")
        .with_decidability("decidable")
        .with_description("Find any element in items satisfying predicate.")
        .build()
    )

    verification_sig = (
        SignatureBuilder("SIG_VERIFICATION")
        .for_problem_class("VERIFICATION")
        .with_input_schema({"claim": "string", "evidence": "object"})
        .with_output_schema({"verdict": "bool", "certificate": "object"})
        .with_precondition("claim is not None")
        .with_precondition("evidence is not None")
        .with_postcondition("verdict == True implies certificate is valid")
        .with_postcondition("verdict == False implies refutation exists")
        .with_invariant("claim is well-formed")
        .with_complexity_class("NP")
        .with_decidability("decidable")
        .with_description("Verify that evidence certifies a given claim.")
        .build()
    )

    optimization_sig = (
        SignatureBuilder("SIG_OPTIMIZATION")
        .for_problem_class("OPTIMIZATION")
        .with_input_schema({
            "objective": "callable",
            "constraints": "array",
            "domain": "object",
        })
        .with_output_schema({"optimum": "any", "value": "number"})
        .with_precondition("domain is not empty")
        .with_precondition("objective is not None")
        .with_postcondition("value == objective(optimum)")
        .with_postcondition("optimum satisfies all constraints")
        .with_invariant("objective is well-defined on domain")
        .with_complexity_class("NP-hard")
        .with_decidability("decidable")
        .with_description("Find the element of domain minimising/maximising objective.")
        .build()
    )

    decision_sig = (
        SignatureBuilder("SIG_DECISION")
        .for_problem_class("DECISION")
        .with_input_schema({"instance": "any", "question": "string"})
        .with_output_schema({"answer": "bool"})
        .with_precondition("instance is well-formed")
        .with_precondition("question is a valid decision question")
        .with_postcondition("answer is True or False")
        .with_complexity_class("P")
        .with_decidability("decidable")
        .with_description("Decide a binary yes/no question about instance.")
        .build()
    )

    counting_sig = (
        SignatureBuilder("SIG_COUNTING")
        .for_problem_class("COUNTING")
        .with_input_schema({"formula": "any", "domain": "object"})
        .with_output_schema({"count": "number"})
        .with_precondition("formula is satisfiable")
        .with_postcondition("count >= 0")
        .with_postcondition("count equals number of solutions in domain")
        .with_invariant("domain is finite and enumerable")
        .with_complexity_class("#P")
        .with_decidability("decidable")
        .with_description("Count the number of solutions satisfying formula in domain.")
        .build()
    )

    construction_sig = (
        SignatureBuilder("SIG_CONSTRUCTION")
        .for_problem_class("CONSTRUCTION")
        .with_input_schema({"specification": "string", "context": "object"})
        .with_output_schema({"artifact": "any", "valid": "bool"})
        .with_precondition("specification is well-formed")
        .with_postcondition("valid == True implies artifact satisfies specification")
        .with_invariant("specification is consistent")
        .with_complexity_class("FP")
        .with_decidability("decidable")
        .with_description("Construct an artifact satisfying specification.")
        .build()
    )

    inference_sig = (
        SignatureBuilder("SIG_INFERENCE")
        .for_problem_class("INFERENCE")
        .with_input_schema({"premises": "array", "query": "string"})
        .with_output_schema({"conclusion": "string", "derivable": "bool"})
        .with_precondition("premises is not empty")
        .with_precondition("query is a well-formed formula")
        .with_postcondition("derivable == True implies conclusion follows from premises")
        .with_invariant("premises are logically consistent")
        .with_complexity_class("RE")
        .with_decidability("semi_decidable")
        .with_description("Derive whether query follows from premises.")
        .build()
    )

    synthesis_sig = (
        SignatureBuilder("SIG_SYNTHESIS")
        .for_problem_class("SYNTHESIS")
        .with_input_schema({"spec": "string", "target_language": "string", "constraints": "array"})
        .with_output_schema({"program": "string", "correct": "bool"})
        .with_precondition("spec is not empty")
        .with_precondition("target_language is supported")
        .with_postcondition("correct == True implies program satisfies spec")
        .with_side_effect("writes synthesized program to output")
        .with_complexity_class("2EXPTIME")
        .with_decidability("semi_decidable")
        .with_description("Synthesize a program satisfying the given specification.")
        .build()
    )

    repair_sig = (
        SignatureBuilder("SIG_REPAIR")
        .for_problem_class("REPAIR")
        .with_input_schema({"broken_artifact": "any", "spec": "string", "max_edits": "number"})
        .with_output_schema({"repaired_artifact": "any", "edit_distance": "number"})
        .with_precondition("broken_artifact is not None")
        .with_precondition("max_edits >= 0")
        .with_postcondition("repaired_artifact satisfies spec")
        .with_postcondition("edit_distance <= max_edits")
        .with_complexity_class("NP-hard")
        .with_decidability("decidable")
        .with_description("Repair broken_artifact so it satisfies spec within max_edits.")
        .build()
    )

    classification_sig = (
        SignatureBuilder("SIG_CLASSIFICATION")
        .for_problem_class("CLASSIFICATION")
        .with_input_schema({"input": "any", "classes": "array", "model": "object"})
        .with_output_schema({"label": "string", "confidence": "number"})
        .with_precondition("classes is not empty")
        .with_precondition("model is not None")
        .with_postcondition("label in classes")
        .with_postcondition("confidence >= 0.0")
        .with_postcondition("confidence <= 1.0")
        .with_complexity_class("P")
        .with_decidability("decidable")
        .with_description("Assign input to one of classes using model.")
        .build()
    )

    return {
        "SEARCH": search_sig,
        "VERIFICATION": verification_sig,
        "OPTIMIZATION": optimization_sig,
        "DECISION": decision_sig,
        "COUNTING": counting_sig,
        "CONSTRUCTION": construction_sig,
        "INFERENCE": inference_sig,
        "SYNTHESIS": synthesis_sig,
        "REPAIR": repair_sig,
        "CLASSIFICATION": classification_sig,
    }


STANDARD_SIGNATURES: dict[str, SemanticSignature] = _build_standard_signatures()


# ═══════════════════════════════════════════════════════════════════════════
# §11  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════


def build_signature_for_class(
    class_id: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    *,
    preconditions: list[str] | None = None,
    postconditions: list[str] | None = None,
) -> SemanticSignature:
    """Build a SemanticSignature for a given problem class.

    Convenience wrapper around SignatureBuilder that infers complexity and
    decidability from the class_id when possible.

    Args:
        class_id: The canonical problem class ID (e.g., ``SEARCH``).
        input_schema: Dict mapping input field names to type descriptors.
        output_schema: Dict mapping output field names to type descriptors.
        preconditions: Optional list of precondition strings.
        postconditions: Optional list of postcondition strings.

    Returns:
        A SemanticSignature bound to *class_id*.
    """
    builder = (
        SignatureBuilder()
        .for_problem_class(class_id)
        .with_input_schema(input_schema)
        .with_output_schema(output_schema)
        .with_complexity_class(_infer_complexity_class(class_id))
        .with_decidability(_infer_decidability(class_id))
    )
    for pre in (preconditions or []):
        builder = builder.with_precondition(pre)
    for post in (postconditions or []):
        builder = builder.with_postcondition(post)
    return builder.build()


def compose_signatures(
    sigs: list[SemanticSignature],
    strategy: CompositionStrategy = CompositionStrategy.SEQUENTIAL,
) -> SemanticSignature:
    """Compose a list of signatures using the given strategy.

    Args:
        sigs: List of SemanticSignature instances to compose.
        strategy: How to combine the signatures.

    Returns:
        A new SemanticSignature representing the composed result.

    Raises:
        ValueError: When *sigs* is empty, or when SEQUENTIAL composition
            fails due to incompatible schemas.
    """
    if not sigs:
        raise ValueError("compose_signatures requires at least one signature.")
    if len(sigs) == 1:
        return sigs[0]
    composer = SignatureComposer()
    if strategy == CompositionStrategy.SEQUENTIAL:
        result = sigs[0]
        for nxt in sigs[1:]:
            result = composer.compose_sequential(result, nxt)
        return result
    if strategy == CompositionStrategy.PARALLEL:
        result = sigs[0]
        for nxt in sigs[1:]:
            result = composer.compose_parallel(result, nxt)
        return result
    if strategy == CompositionStrategy.PRODUCT:
        return composer.compose_product(sigs)
    if strategy == CompositionStrategy.CONDITIONAL and len(sigs) == 3:
        return composer.compose_conditional(sigs[0], sigs[1], sigs[2])
    # Default: sequential
    result = sigs[0]
    for nxt in sigs[1:]:
        result = composer.compose_sequential(result, nxt)
    return result


def check_signature_match(
    source: SemanticSignature,
    target: SemanticSignature,
    *,
    strict: bool = False,
) -> CompatibilityResult:
    """Check compatibility between *source* and *target* signatures.

    Args:
        source: The producing signature.
        target: The consuming signature.
        strict: When True, require every output field to match an input field.

    Returns:
        CompatibilityResult with full diagnostic details.
    """
    matcher = SignatureMatcher(strict=strict)
    return matcher.check_compatibility(source, target)


def check_signature_compatibility(
    source: SemanticSignature,
    target: SemanticSignature,
) -> bool:
    """Return ``True`` when *source* is compatible with *target* as a pipeline feed.

    Provided for __init__.py compatibility.

    Args:
        source: The producing signature.
        target: The consuming signature.

    Returns:
        True when the output of *source* can feed the input of *target*.
    """
    result = check_signature_match(source, target)
    return result.compatible


def normalize_signature(sig: SemanticSignature) -> SemanticSignature:
    """Return a canonically normalized copy of *sig*.

    Args:
        sig: The SemanticSignature to normalize.

    Returns:
        Normalized SemanticSignature in canonical form.
    """
    return SignatureNormalizer().normalize(sig)


def infer_signature(problem_class: Any) -> SemanticSignature | None:
    """Infer a SemanticSignature for *problem_class* from the standard catalogue.

    Provided for __init__.py compatibility.  Looks up the class name in
    ``STANDARD_SIGNATURES`` and returns the result, or None if not found.

    Args:
        problem_class: A ProblemClass instance or a class name string.

    Returns:
        The corresponding SemanticSignature, or None if not found.
    """
    if problem_class is None:
        return None
    name = getattr(problem_class, "name", None) or str(problem_class)
    return STANDARD_SIGNATURES.get(name.upper())




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
    "CompositionStrategy",
    "SchemaKind",
    "SemanticCompatibility",
    "SignatureKind",
    # Dataclasses
    "CompatibilityResult",
    "IOSchema",
    "SemanticContract",
    "SemanticSignature",
    # Builder / composer / matcher / checker / normalizer
    "PreconditionChecker",
    "SignatureBuilder",
    "SignatureComposer",
    "SignatureMatcher",
    "SignatureNormalizer",
    # Module-level data
    "STANDARD_SIGNATURES",
    # Functions
    "build_signature_for_class",
    "check_signature_compatibility",
    "check_signature_match",
    "compose_signatures",
    "infer_signature",
    "normalize_signature",
    # Type aliases
    "ConditionStr",
    "SchemaDict",
    "SigId",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.
