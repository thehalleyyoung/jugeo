"""Core data models for theorem schemas — copilot-assisted Ch36 encoding.

Defines the fundamental data structures for representing theorem schemas,
subsystem schemas, schema instances, proof obligations, and validators.
These models encode the abstract proof-theoretic structure of Chapter 36
of theory2.tex: what each subsystem must prove.

copilot: core models for theorem schema system.

Mathematical Background
-----------------------
A *theorem schema* in the JuGeo sense is a Hilbert-style axiom schema: a
statement containing free *metavariables* (placeholders) that become concrete
when instantiated with real witnesses.  For example, the schema

    "For every trust annotation T and support set S, propagating T through S
     preserves the monotone ordering on annotations"

contains metavariables ``T`` and ``S``.  When these are bound to actual
runtime objects the schema becomes a *proof obligation* — a concrete claim
that some automated or human prover must discharge.

Chapter 36 of theory2.tex lists, for each of the eight JuGeo subsystems, the
minimal set of theorem schemas that the subsystem is responsible for proving.
This module provides the Python data model for these schemas, instances,
obligations, and the validators that check their internal consistency.

Design Notes
------------
*   All classes use plain ``__init__`` (not ``@dataclass``) for maximum
    flexibility and to allow non-trivial field computation at construction time.
*   IDs default to ``uuid.uuid4()`` strings; timestamps default to
    ``time.time()`` floats.
*   Serialisation round-trips are guaranteed for all classes via ``to_json``
    and ``from_json``.
*   ``SchemaValidator`` is designed to be extended: custom rules can be
    injected via ``add_rule``.

Usage Example
-------------
::

    from jugeo.encodings.theorem_schemas.models import (
        TheoremSchema, SubsystemKind, ProofStyle
    )

    schema = TheoremSchema(
        name="trust-monotone",
        template_statement="For all {T} and {S}, T <= propagate(T, S)",
        variables={"T": "trust annotation", "S": "support set"},
        proof_style=ProofStyle.INDUCTIVE,
        subsystem=SubsystemKind.TRUST,
    )
    instance = schema.instantiate({"T": "HighTrust", "S": "S_root"})
    print(instance.instantiated_statement)
"""
from __future__ import annotations

import json
import re
import time
import uuid
from enum import Enum
from typing import Any

__all__ = [
    "ProofStyle",
    "InstanceStatus",
    "SubsystemKind",
    "ProofAgent",
    "TheoremSchema",
    "SubsystemSchema",
    "SchemaInstance",
    "ProofObligation",
    "SchemaValidator",
    "make_simple_schema",
    "batch_instantiate",
    "obligations_from_instances",
]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProofStyle(str, Enum):
    """Proof strategy used to discharge a theorem schema.

    Attributes
    ----------
    DIRECT:
        A direct proof that constructs the required witness or derives the
        conclusion from the hypotheses without case analysis.
    INDUCTIVE:
        A structural or well-founded induction argument.
    CONTRADICTION:
        A proof by assuming the negation and deriving False.
    CATEGORICAL:
        A proof using categorical / diagram-chasing arguments (common in
        JuGeo's descent and encoding subsystems).
    """

    DIRECT = "direct"
    INDUCTIVE = "inductive"
    CONTRADICTION = "contradiction"
    CATEGORICAL = "categorical"
    AUTOMATED = "automated"
    INVARIANT_CHECK = "invariant_check"
    ALGEBRAIC = "algebraic"
    SEMANTIC_ARGUMENT = "semantic_argument"
    DIAGRAM_CHASE = "diagram_chase"
    CONSTRUCTIVE = "constructive"
    COINDUCTION = "coinduction"
    STRUCTURAL_INDUCTION = "structural_induction"
    FORMAL = "formal"


class InstanceStatus(str, Enum):
    """Lifecycle status of a schema instance / proof obligation.

    Attributes
    ----------
    PENDING:
        Created but not yet assigned to a prover.
    ACTIVE:
        Assigned and under active proof attempt.
    DISCHARGED:
        Successfully proved and closed.
    FAILED:
        Proof attempt failed; the obligation must be re-raised or escalated.
    """

    PENDING = "pending"
    ACTIVE = "active"
    DISCHARGED = "discharged"
    FAILED = "failed"


class SubsystemKind(str, Enum):
    """Enumeration of the eight JuGeo subsystems defined in Chapter 36.

    Attributes
    ----------
    DESCENT:
        Descent-data coherence subsystem.
    TRUST:
        Trust-propagation and annotation subsystem.
    EVIDENCE:
        Evidence accumulation and archiving subsystem.
    FEDERATION:
        Multi-node federation and consensus subsystem.
    INVALIDATION:
        Cache/state invalidation and cascade subsystem.
    MEMORY:
        Semantic memory and snapshot subsystem.
    JUDGMENT:
        Judgment algebra and evidence-bundle subsystem.
    ENCODING:
        Data encoding and codec subsystem.
    """

    DESCENT = "descent"
    TRUST = "trust"
    EVIDENCE = "evidence"
    FEDERATION = "federation"
    INVALIDATION = "invalidation"
    MEMORY = "memory"
    JUDGMENT = "judgment"
    ENCODING = "encoding"


class ProofAgent(str, Enum):
    """Agent responsible for discharging a proof obligation.

    Attributes
    ----------
    SOLVER:
        An automated constraint solver or SAT/SMT backend.
    HUMAN:
        A human mathematician or engineer.
    COPILOT:
        The GitHub Copilot-assisted proof assistant pipeline.
    ORACLE:
        A trusted oracle whose outputs are accepted without further proof.
    """

    SOLVER = "solver"
    HUMAN = "human"
    COPILOT = "copilot"
    ORACLE = "oracle"
    TACTIC_ENGINE = "tactic_engine"
    SMT = "smt"
    HUMAN_REVIEW = "human_review"
    AUTO_DISPATCH = "auto_dispatch"
    UNASSIGNED = "unassigned"


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_VAR_PATTERN = re.compile(r"\{(\w+)\}")
"""Regex for extracting ``{varname}`` placeholders from template strings."""

_VALID_PROOF_STYLES: frozenset[str] = frozenset(s.value for s in ProofStyle)
"""Set of valid proof style values for validation."""

_VALID_SUBSYSTEMS: frozenset[str] = frozenset(s.value for s in SubsystemKind)
"""Set of valid subsystem kind values for validation."""


# ---------------------------------------------------------------------------
# TheoremSchema
# ---------------------------------------------------------------------------


class TheoremSchema:
    """A parameterised theorem schema with free metavariables.

    A schema captures an *abstract* claim that can be specialised to many
    concrete theorems by substituting values for its metavariables.  The
    template statement uses ``{varname}`` syntax for placeholders, following
    Python's ``str.format`` convention.

    The relationship to Chapter 36
    --------------------------------
    Chapter 36 lists, for each subsystem, a collection of theorem schemas
    organised into *required* and *optional* theorems.  Each entry in that
    list corresponds to one ``TheoremSchema`` object.

    Parameters
    ----------
    name:
        Short, stable identifier for the schema.
    template_statement:
        Human-readable statement with ``{var}`` placeholders.
    variables:
        Mapping from variable name to English description of the variable.
    proof_style:
        Default ``ProofStyle`` for this schema.
    subsystem:
        The ``SubsystemKind`` that owns this schema.
    schema_id:
        Unique UUID4 string.  Auto-generated if omitted.
    tags:
        Arbitrary string labels.
    """

    def __init__(
        self,
        name: str,
        template_statement: str,
        variables: dict[str, str],
        proof_style: ProofStyle,
        subsystem: SubsystemKind,
        schema_id: str | None = None,
        tags: list[str] | None = None,
        description: str = "",
        metadata: dict | None = None,
        created_at: float | None = None,
    ) -> None:
        """Initialise a new TheoremSchema.

        Parameters
        ----------
        name:
            Short identifier for this schema.
        template_statement:
            Statement template containing ``{var}`` placeholders.
        variables:
            Mapping from placeholder name to English description.
        proof_style:
            The proof strategy for this schema.
        subsystem:
            The owning subsystem.
        schema_id:
            Optional pre-existing UUID string; generated if omitted.
        tags:
            Optional list of string labels.
        description:
            Extended prose description of what this theorem asserts.
        metadata:
            Optional dict of arbitrary extra metadata (difficulty, prerequisites, etc.).
        created_at:
            Unix timestamp of creation.  Defaults to ``time.time()``.
        """
        self.schema_id: str = schema_id if schema_id else str(uuid.uuid4())
        self.name: str = name
        self.template_statement: str = template_statement
        self.variables: dict[str, str] = dict(variables)
        self.proof_style: ProofStyle = proof_style
        self.subsystem: SubsystemKind = subsystem
        self.tags: list[str] = list(tags) if tags else []
        self.description: str = description
        self.metadata: dict = dict(metadata) if metadata else {}
        self.created_at: float = created_at if created_at is not None else time.time()

    def get_free_vars(self) -> list[str]:
        """Parse the template statement and return the list of placeholder names.

        Uses a regex to find all ``{varname}`` occurrences.  The returned list
        preserves order of first appearance and deduplicates.

        Returns
        -------
        list[str]
            Ordered, deduplicated list of variable names found in the template.
        """
        seen: list[str] = []
        seen_set: set[str] = set()
        for match in re.finditer(r"\{(\w+)\}", self.template_statement):
            var = match.group(1)
            if var not in seen_set:
                seen.append(var)
                seen_set.add(var)
        return seen

    def validate_bindings(self, bindings: dict[str, str]) -> list[str]:
        """Check that ``bindings`` is a valid instantiation for this schema.

        A binding is valid iff every free variable is bound, no extra
        variables are provided, and all bound values are non-empty strings.

        Parameters
        ----------
        bindings:
            Mapping from variable name to concrete value.

        Returns
        -------
        list[str]
            List of error descriptions.  Empty means the bindings are valid.
        """
        errors: list[str] = []
        free = set(self.get_free_vars())
        bound = set(bindings.keys())
        for var in sorted(free - bound):
            errors.append(f"Missing binding for variable {var!r}")
        for var in sorted(bound - free):
            errors.append(
                f"Extra binding for variable {var!r} "
                "(not present in template)"
            )
        for var, val in bindings.items():
            if not isinstance(val, str) or not val.strip():
                errors.append(
                    f"Binding for {var!r} must be a non-empty string "
                    f"(got {val!r})"
                )
        return errors

    def instantiate(self, bindings: dict[str, str]) -> "SchemaInstance":
        """Create a concrete ``SchemaInstance`` by substituting ``bindings``.

        Parameters
        ----------
        bindings:
            Mapping from variable name to concrete value string.

        Returns
        -------
        SchemaInstance
            A new instance with all placeholders substituted.

        Raises
        ------
        ValueError
            If ``validate_bindings`` returns any errors.
        """
        errors = self.validate_bindings(bindings)
        if errors:
            raise ValueError(
                f"Cannot instantiate schema {self.schema_id!r}: "
                + "; ".join(errors)
            )
        instantiated = self.template_statement
        for var, val in bindings.items():
            instantiated = instantiated.replace("{" + var + "}", val)
        return SchemaInstance(
            schema_id=self.schema_id,
            bindings=dict(bindings),
            instantiated_statement=instantiated,
            status=InstanceStatus.PENDING,
        )

    def render_tex(self) -> str:
        """Return a LaTeX string representation of the schema.

        The rendered string wraps the template in a theorem environment
        and lists the metavariables.

        Returns
        -------
        str
            LaTeX snippet suitable for embedding in a ``.tex`` document.
        """
        vars_str = ", ".join(
            f"${v}$: {desc}" for v, desc in self.variables.items()
        )
        escaped_stmt = self.template_statement.replace("_", r"\_")
        return (
            "\\begin{theorem}[" + self.name + "]\n"
            "  \\textit{Schema} (" + self.proof_style.value + ").\n"
            "  Variables: " + vars_str + ".\n\n"
            "  " + escaped_stmt + "\n"
            "\\end{theorem}"
        )

    def compose_with(self, other: "TheoremSchema") -> "TheoremSchema":
        """Create a new schema whose statement is the conjunction of both schemas.

        The composed schema inherits the subsystem and proof style of ``self``.
        Variables from both schemas are merged; descriptions from ``other``
        take precedence on name collision.

        Parameters
        ----------
        other:
            The second schema to compose with.

        Returns
        -------
        TheoremSchema
            A new composite schema.
        """
        combined_stmt = (
            "(" + self.template_statement + ") AND (" + other.template_statement + ")"
        )
        combined_vars = {**self.variables, **other.variables}
        combined_tags = list(set(self.tags + other.tags))
        return TheoremSchema(
            name=self.name + "__AND__" + other.name,
            template_statement=combined_stmt,
            variables=combined_vars,
            proof_style=self.proof_style,
            subsystem=self.subsystem,
            tags=combined_tags,
        )

    def check_consistency(self) -> bool:
        """Verify that the ``variables`` dict matches the template free vars.

        A schema is *consistent* when every free variable in the template has
        an entry in ``self.variables`` and every key in ``self.variables``
        appears in the template.

        Returns
        -------
        bool
            True iff the schema is internally consistent.
        """
        template_vars = set(self.get_free_vars())
        declared_vars = set(self.variables.keys())
        return template_vars == declared_vars

    def summarize(self) -> str:
        """Return a human-readable one-paragraph summary.

        Returns
        -------
        str
            Multi-line string describing this schema.
        """
        free = self.get_free_vars()
        consistent = "yes" if self.check_consistency() else "NO (inconsistent)"
        return (
            "TheoremSchema '" + self.name + "' [" + self.subsystem.value + "] "
            "(id=" + self.schema_id[:8] + "...)\n"
            "  Style: " + self.proof_style.value + "\n"
            "  Free vars: " + str(free) + "\n"
            "  Consistent: " + consistent + "\n"
            "  Template: " + self.template_statement[:120]
        )

    def to_json(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict
            All fields encoded as JSON-safe types.
        """
        return {
            "schema_id": self.schema_id,
            "name": self.name,
            "template_statement": self.template_statement,
            "variables": dict(self.variables),
            "proof_style": self.proof_style.value,
            "subsystem": self.subsystem.value,
            "tags": list(self.tags),
        }

    @classmethod
    def from_json(cls, d: dict) -> "TheoremSchema":
        """Deserialise from a dictionary produced by ``to_json``.

        Parameters
        ----------
        d:
            Source dictionary.

        Returns
        -------
        TheoremSchema
            New instance populated from ``d``.
        """
        return cls(
            name=d["name"],
            template_statement=d["template_statement"],
            variables=dict(d.get("variables", {})),
            proof_style=ProofStyle(d["proof_style"]),
            subsystem=SubsystemKind(d["subsystem"]),
            schema_id=d.get("schema_id"),
            tags=list(d.get("tags", [])),
        )

    def __repr__(self) -> str:
        """Return a concise developer representation."""
        return (
            "TheoremSchema(name=" + repr(self.name) + ", "
            "subsystem=" + repr(self.subsystem.value) + ", "
            "id=" + self.schema_id[:8] + "...)"
        )

    def __eq__(self, other: object) -> bool:
        """Two schemas are equal iff they share the same schema_id."""
        if not isinstance(other, TheoremSchema):
            return NotImplemented
        return self.schema_id == other.schema_id

    def __hash__(self) -> int:
        """Hash based on schema_id for use in sets and dict keys."""
        return hash(self.schema_id)


# ---------------------------------------------------------------------------
# SubsystemSchema
# ---------------------------------------------------------------------------


class SubsystemSchema:
    """Container for all theorem schemas belonging to a single subsystem.

    Each JuGeo subsystem is assigned a ``SubsystemSchema`` that tracks which
    theorems are *required* (the subsystem cannot be considered sound without
    proving them) and which are *optional* (nice-to-have but not mandatory).

    Parameters
    ----------
    subsystem_name:
        The ``SubsystemKind`` this schema group belongs to.
    subsystem_id:
        UUID4 string.  Auto-generated if omitted.
    required_theorems:
        Names of theorems that must be proved.
    optional_theorems:
        Names of theorems that may optionally be proved.

    Notes
    -----
    The ``required_theorems`` and ``optional_theorems`` lists hold *names*
    (not schema_ids).  This allows a registry entry to be declared before the
    actual ``TheoremSchema`` object is created.
    """

    def __init__(
        self,
        subsystem_name: SubsystemKind,
        subsystem_id: str | None = None,
        required_theorems: list[str] | None = None,
        optional_theorems: list[str] | None = None,
    ) -> None:
        """Initialise a SubsystemSchema.

        Parameters
        ----------
        subsystem_name:
            The owning subsystem kind.
        subsystem_id:
            Optional pre-existing UUID string; generated if omitted.
        required_theorems:
            Names of required theorems.
        optional_theorems:
            Names of optional theorems.
        """
        self.subsystem_id: str = subsystem_id if subsystem_id else str(uuid.uuid4())
        self.subsystem_name: SubsystemKind = subsystem_name
        self.schemas: dict[str, TheoremSchema] = {}
        self.required_theorems: list[str] = list(required_theorems or [])
        self.optional_theorems: list[str] = list(optional_theorems or [])

    def add_schema(self, schema: TheoremSchema) -> None:
        """Register a theorem schema in this subsystem group.

        Parameters
        ----------
        schema:
            The schema to add.

        Raises
        ------
        ValueError
            If a schema with the same ``schema_id`` is already registered.
        """
        if schema.schema_id in self.schemas:
            raise ValueError(
                f"Schema {schema.schema_id!r} already registered in "
                f"subsystem {self.subsystem_name.value!r}. "
                "Call remove_schema first if replacement is intended."
            )
        self.schemas[schema.schema_id] = schema

    def remove_schema(self, schema_id: str) -> bool:
        """Remove a schema by ID.

        Parameters
        ----------
        schema_id:
            ID of the schema to remove.

        Returns
        -------
        bool
            True if the schema was found and removed; False otherwise.
        """
        if schema_id in self.schemas:
            del self.schemas[schema_id]
            return True
        return False

    def get_required(self) -> list[TheoremSchema]:
        """Return the schemas whose names appear in ``required_theorems``.

        Schemas are returned in the order they appear in
        ``required_theorems``.  Theorems listed but not yet registered are
        silently omitted.

        Returns
        -------
        list[TheoremSchema]
        """
        result: list[TheoremSchema] = []
        for req_name in self.required_theorems:
            for schema in self.schemas.values():
                if schema.name == req_name:
                    result.append(schema)
                    break
        return result

    def get_optional(self) -> list[TheoremSchema]:
        """Return the schemas whose names appear in ``optional_theorems``.

        Returns
        -------
        list[TheoremSchema]
        """
        result: list[TheoremSchema] = []
        for opt_name in self.optional_theorems:
            for schema in self.schemas.values():
                if schema.name == opt_name:
                    result.append(schema)
                    break
        return result

    def validate_completeness(self) -> bool:
        """Return True iff all required theorems have a corresponding schema.

        Returns
        -------
        bool
            True when ``missing_proofs()`` returns an empty list.
        """
        return len(self.missing_proofs()) == 0

    def missing_proofs(self) -> list[str]:
        """Return the names of required theorems that lack a registered schema.

        A required theorem is considered present if at least one registered
        schema carries a matching ``name``.

        Returns
        -------
        list[str]
            Possibly empty list of theorem names.
        """
        registered_names = {s.name for s in self.schemas.values()}
        return [t for t in self.required_theorems if t not in registered_names]

    def count(self) -> int:
        """Return the total number of registered schemas.

        Returns
        -------
        int
        """
        return len(self.schemas)

    def instantiate_all(
        self, bindings: dict[str, dict[str, str]]
    ) -> list["SchemaInstance"]:
        """Instantiate all schemas whose IDs appear as keys in ``bindings``.

        Parameters
        ----------
        bindings:
            Mapping from ``schema_id`` to a binding dict.

        Returns
        -------
        list[SchemaInstance]
            One instance per matched schema whose bindings are valid.

        Notes
        -----
        Schemas not mentioned in ``bindings`` are silently skipped.
        """
        instances: list[SchemaInstance] = []
        for schema_id, binding_map in bindings.items():
            schema = self.schemas.get(schema_id)
            if schema is None:
                continue
            instances.append(schema.instantiate(binding_map))
        return instances

    def to_json(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict
            Contains subsystem fields and nested schemas.
        """
        return {
            "subsystem_id": self.subsystem_id,
            "subsystem_name": self.subsystem_name.value,
            "schemas": {sid: s.to_json() for sid, s in self.schemas.items()},
            "required_theorems": list(self.required_theorems),
            "optional_theorems": list(self.optional_theorems),
        }

    @classmethod
    def from_json(cls, d: dict) -> "SubsystemSchema":
        """Deserialise from a dictionary produced by ``to_json``.

        Parameters
        ----------
        d:
            Source dictionary.

        Returns
        -------
        SubsystemSchema
        """
        obj = cls(
            subsystem_name=SubsystemKind(d["subsystem_name"]),
            subsystem_id=d.get("subsystem_id"),
            required_theorems=list(d.get("required_theorems", [])),
            optional_theorems=list(d.get("optional_theorems", [])),
        )
        for schema_dict in d.get("schemas", {}).values():
            obj.add_schema(TheoremSchema.from_json(schema_dict))
        return obj

    def __repr__(self) -> str:
        """Return a concise developer representation."""
        return (
            "SubsystemSchema(subsystem=" + repr(self.subsystem_name.value) + ", "
            "count=" + str(self.count()) + ", "
            "complete=" + str(self.validate_completeness()) + ")"
        )


# ---------------------------------------------------------------------------
# SchemaInstance
# ---------------------------------------------------------------------------


class SchemaInstance:
    """A fully instantiated theorem schema with concrete variable bindings.

    A ``SchemaInstance`` is produced by calling ``TheoremSchema.instantiate``
    with a concrete binding map.  It tracks the lifecycle of the corresponding
    proof obligation from ``PENDING`` through ``DISCHARGED`` or ``FAILED``.

    Parameters
    ----------
    schema_id:
        ID of the parent ``TheoremSchema``.
    bindings:
        The concrete variable substitutions used to produce this instance.
    instantiated_statement:
        The statement with all ``{var}`` placeholders replaced.
    status:
        Initial status (typically ``InstanceStatus.PENDING``).
    instance_id:
        UUID4 string.  Auto-generated if omitted.
    """

    def __init__(
        self,
        schema_id: str,
        bindings: dict[str, str],
        instantiated_statement: str,
        status: InstanceStatus = InstanceStatus.PENDING,
        instance_id: str | None = None,
    ) -> None:
        """Initialise a new SchemaInstance.

        Parameters
        ----------
        schema_id:
            Parent schema UUID string.
        bindings:
            Variable bindings used during instantiation.
        instantiated_statement:
            Fully substituted statement text.
        status:
            Initial lifecycle status.
        instance_id:
            Optional pre-existing UUID; generated if omitted.
        """
        self.instance_id: str = instance_id if instance_id else str(uuid.uuid4())
        self.schema_id: str = schema_id
        self.bindings: dict[str, str] = dict(bindings)
        self.instantiated_statement: str = instantiated_statement
        self.status: InstanceStatus = status
        self.created_at: float = time.time()
        self.discharged_at: float | None = None
        self.discharge_evidence: dict | None = None

    def refresh(self) -> None:
        """Transition from ``PENDING`` to ``ACTIVE``.

        Activates the instance so that it can be assigned to a prover.  If
        the instance is not ``PENDING``, this is a no-op so that callers do
        not need to guard against double-activation.
        """
        if self.status == InstanceStatus.PENDING:
            self.status = InstanceStatus.ACTIVE

    def discharge(self, evidence: dict) -> None:
        """Mark this instance as successfully proved.

        Parameters
        ----------
        evidence:
            Arbitrary dictionary recording the proof artifact.

        Raises
        ------
        RuntimeError
            If the instance is already in a terminal status.
        """
        if self.status in (InstanceStatus.DISCHARGED, InstanceStatus.FAILED):
            raise RuntimeError(
                f"Cannot discharge instance {self.instance_id!r} "
                f"in terminal status {self.status.value!r}"
            )
        self.status = InstanceStatus.DISCHARGED
        self.discharged_at = time.time()
        self.discharge_evidence = dict(evidence)

    def fail(self, reason: str) -> None:
        """Mark this instance as failed.

        Parameters
        ----------
        reason:
            Human-readable explanation of why the proof failed.
        """
        self.status = InstanceStatus.FAILED
        self.discharge_evidence = {
            "reason": reason,
            "failed_at": time.time(),
            "instance_id": self.instance_id,
        }

    def is_active(self) -> bool:
        """Return True iff the status is ``ACTIVE``.

        Returns
        -------
        bool
        """
        return self.status == InstanceStatus.ACTIVE

    def get_age(self) -> float:
        """Return the number of seconds since this instance was created.

        Returns
        -------
        float
            Elapsed seconds as a non-negative float.
        """
        return time.time() - self.created_at

    def verify_bindings(self) -> bool:
        """Return True iff the instantiated statement has no remaining placeholders.

        A fully substituted statement should have no ``{var}`` patterns left.

        Returns
        -------
        bool
        """
        return not bool(re.search(r"\{\w+\}", self.instantiated_statement))

    def to_proof_obligation(self) -> "ProofObligation":
        """Convert this instance to a ``ProofObligation``.

        The hypothesis list and conclusion are derived by splitting the
        statement at " -> " if present; otherwise the full statement is
        treated as the conclusion.

        Returns
        -------
        ProofObligation
            A new obligation with default priority 5 assigned to SOLVER.
        """
        stmt = self.instantiated_statement
        if " -> " in stmt:
            parts = stmt.rsplit(" -> ", maxsplit=1)
            hypotheses = [h.strip() for h in parts[0].split(" AND ")]
            conclusion = parts[1].strip()
        else:
            hypotheses = []
            conclusion = stmt
        return ProofObligation(
            instance_id=self.instance_id,
            statement=self.instantiated_statement,
            hypothesis_list=hypotheses,
            conclusion=conclusion,
            proof_method=ProofStyle.DIRECT,
            assigned_to=ProofAgent.SOLVER,
        )

    def to_json(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict
        """
        return {
            "instance_id": self.instance_id,
            "schema_id": self.schema_id,
            "bindings": dict(self.bindings),
            "instantiated_statement": self.instantiated_statement,
            "status": self.status.value,
            "created_at": self.created_at,
            "discharged_at": self.discharged_at,
            "discharge_evidence": self.discharge_evidence,
        }

    def __repr__(self) -> str:
        """Return a concise developer representation."""
        return (
            "SchemaInstance(id=" + self.instance_id[:8] + "..., "
            "schema=" + self.schema_id[:8] + "..., "
            "status=" + repr(self.status.value) + ")"
        )


# ---------------------------------------------------------------------------
# ProofObligation
# ---------------------------------------------------------------------------


class ProofObligation:
    """A concrete proof task derived from a ``SchemaInstance``.

    A ``ProofObligation`` packages together the statement to be proved, the
    list of hypotheses, the conclusion, the proof method, and assignment
    metadata so that the obligation can be routed to the appropriate prover.

    Parameters
    ----------
    instance_id:
        ID of the originating ``SchemaInstance``.
    statement:
        Full natural-language or formal statement.
    hypothesis_list:
        Ordered list of premises / assumptions.
    conclusion:
        The consequent that must be derived.
    proof_method:
        Strategy for attacking the proof.
    assigned_to:
        Which ``ProofAgent`` is responsible.
    obligation_id:
        UUID4 string.  Auto-generated if omitted.
    deadline:
        Optional Unix timestamp by which the proof must be delivered.
    priority:
        Integer 0-10 (10 = most urgent).
    """

    def __init__(
        self,
        instance_id: str,
        statement: str,
        hypothesis_list: list[str],
        conclusion: str,
        proof_method: ProofStyle,
        assigned_to: ProofAgent,
        obligation_id: str | None = None,
        deadline: float | None = None,
        priority: int = 5,
    ) -> None:
        """Initialise a new ProofObligation.

        Parameters
        ----------
        instance_id:
            ID of the parent SchemaInstance.
        statement:
            Full statement text.
        hypothesis_list:
            List of hypothesis strings.
        conclusion:
            Conclusion string.
        proof_method:
            Proof strategy to use.
        assigned_to:
            The responsible agent.
        obligation_id:
            Optional pre-existing UUID; generated if omitted.
        deadline:
            Optional deadline as a Unix timestamp float.
        priority:
            Priority integer clamped to [0, 10].
        """
        self.obligation_id: str = obligation_id if obligation_id else str(uuid.uuid4())
        self.instance_id: str = instance_id
        self.statement: str = statement
        self.hypothesis_list: list[str] = list(hypothesis_list)
        self.conclusion: str = conclusion
        self.proof_method: ProofStyle = proof_method
        self.assigned_to: ProofAgent = assigned_to
        self.deadline: float | None = deadline
        self.priority: int = max(0, min(10, int(priority)))

    def assign(self, agent: ProofAgent) -> None:
        """Re-assign this obligation to a different agent.

        Parameters
        ----------
        agent:
            The new responsible ``ProofAgent``.
        """
        self.assigned_to = agent

    def discharge_with(self, proof: dict) -> dict:
        """Record a proof artifact and return a discharge record dict.

        Parameters
        ----------
        proof:
            Arbitrary proof artifact dictionary.

        Returns
        -------
        dict
            A discharge record containing obligation ID, agent, timestamp,
            and the supplied proof artifact.
        """
        return {
            "obligation_id": self.obligation_id,
            "instance_id": self.instance_id,
            "discharged_by": self.assigned_to.value,
            "discharged_at": time.time(),
            "proof_method": self.proof_method.value,
            "proof_artifact": proof,
            "statement": self.statement,
            "conclusion": self.conclusion,
        }

    def escalate(self) -> None:
        """Increase priority by 1, capped at 10."""
        self.priority = min(10, self.priority + 1)

    def lower_priority(self) -> None:
        """Decrease priority by 1, floored at 0."""
        self.priority = max(0, self.priority - 1)

    def is_overdue(self) -> bool:
        """Return True iff a deadline is set and has already passed.

        Returns
        -------
        bool
        """
        if self.deadline is None:
            return False
        return time.time() > self.deadline

    def to_judgment_dict(self) -> dict:
        """Return a structured representation suitable for the Judgment layer.

        Returns
        -------
        dict
            Keys: ``statement``, ``hypotheses``, ``conclusion``, ``method``,
            ``agent``, ``priority``, ``overdue``, ``obligation_id``.
        """
        return {
            "obligation_id": self.obligation_id,
            "statement": self.statement,
            "hypotheses": list(self.hypothesis_list),
            "conclusion": self.conclusion,
            "method": self.proof_method.value,
            "agent": self.assigned_to.value,
            "priority": self.priority,
            "overdue": self.is_overdue(),
            "hypothesis_count": len(self.hypothesis_list),
        }

    def summarize(self) -> str:
        """Return a human-readable summary string.

        Returns
        -------
        str
            Multi-line formatted summary of this obligation.
        """
        overdue_tag = " [OVERDUE]" if self.is_overdue() else ""
        hyp_count = len(self.hypothesis_list)
        conclusion_preview = (
            self.conclusion[:80] + "..."
            if len(self.conclusion) > 80
            else self.conclusion
        )
        return (
            "ProofObligation(" + self.obligation_id[:8] + "...)" + overdue_tag + "\n"
            "  Assigned to: " + self.assigned_to.value + "\n"
            "  Priority: " + str(self.priority) + "/10\n"
            "  Method: " + self.proof_method.value + "\n"
            "  Hypotheses: " + str(hyp_count) + "\n"
            "  Conclusion: " + conclusion_preview
        )

    def to_json(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict
        """
        return {
            "obligation_id": self.obligation_id,
            "instance_id": self.instance_id,
            "statement": self.statement,
            "hypothesis_list": list(self.hypothesis_list),
            "conclusion": self.conclusion,
            "proof_method": self.proof_method.value,
            "assigned_to": self.assigned_to.value,
            "deadline": self.deadline,
            "priority": self.priority,
        }

    def __repr__(self) -> str:
        """Return a concise developer representation."""
        return (
            "ProofObligation(id=" + self.obligation_id[:8] + "..., "
            "agent=" + repr(self.assigned_to.value) + ", "
            "priority=" + str(self.priority) + ")"
        )


# ---------------------------------------------------------------------------
# SchemaValidator
# ---------------------------------------------------------------------------


class SchemaValidator:
    """Validates theorem schemas, instances, and obligations against a rule set.

    Rules are stored as plain dictionaries with at minimum a ``rule_id``
    (string) and a ``description`` (string).  Additional fields are
    rule-type-specific and interpreted by the relevant ``validate_*`` method.

    Parameters
    ----------
    strict_mode:
        If True, warnings are promoted to errors.
    validator_id:
        UUID4 string.  Auto-generated if omitted.
    """

    def __init__(
        self,
        strict_mode: bool = False,
        validator_id: str | None = None,
    ) -> None:
        """Initialise a new SchemaValidator.

        Parameters
        ----------
        strict_mode:
            Promote warnings to errors when True.
        validator_id:
            Optional pre-existing UUID; generated if omitted.
        """
        self.validator_id: str = validator_id if validator_id else str(uuid.uuid4())
        self.strict_mode: bool = strict_mode
        self.rules: list[dict] = []

    def add_rule(self, rule: dict) -> None:
        """Append a rule to the validator's rule list.

        Parameters
        ----------
        rule:
            Dictionary with at least ``rule_id`` (str) and
            ``description`` (str) keys.

        Raises
        ------
        ValueError
            If ``rule`` is missing ``rule_id`` or ``description``.
        """
        if "rule_id" not in rule:
            raise ValueError(
                "Rule dict must contain a 'rule_id' key (got: "
                + str(sorted(rule.keys()))
                + ")"
            )
        if "description" not in rule:
            raise ValueError(
                f"Rule {rule['rule_id']!r} must have a 'description' key"
            )
        self.rules.append(dict(rule))

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by its ``rule_id``.

        Parameters
        ----------
        rule_id:
            ID of the rule to remove.

        Returns
        -------
        bool
            True if a matching rule was found and removed.
        """
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.get("rule_id") != rule_id]
        return len(self.rules) < before

    def validate_schema(self, schema: TheoremSchema) -> list[str]:
        """Validate a ``TheoremSchema`` against built-in checks.

        Built-in checks:
        -   ``name`` must be non-empty.
        -   ``template_statement`` must be non-empty.
        -   Template must contain at least one ``{var}`` placeholder.
        -   ``variables`` must be non-empty.
        -   Schema must be internally consistent (``check_consistency``).

        Parameters
        ----------
        schema:
            Schema to validate.

        Returns
        -------
        list[str]
            List of error strings.  Empty means the schema passed.
        """
        errors: list[str] = []
        if not schema.name or not schema.name.strip():
            errors.append(
                f"[{schema.schema_id}] 'name' must be a non-empty string"
            )
        if not schema.template_statement or not schema.template_statement.strip():
            errors.append(
                f"[{schema.schema_id}] 'template_statement' must be non-empty"
            )
        free = schema.get_free_vars()
        if not free:
            msg = (
                f"[{schema.schema_id}] template has no free variables — "
                "this is a ground proposition, not a schema"
            )
            errors.append(msg if self.strict_mode else "WARNING: " + msg)
        if not schema.variables:
            errors.append(
                f"[{schema.schema_id}] 'variables' dict must be non-empty"
            )
        if not schema.check_consistency():
            template_vars = set(schema.get_free_vars())
            declared_vars = set(schema.variables.keys())
            in_tmpl_not_decl = sorted(template_vars - declared_vars)
            decl_not_in_tmpl = sorted(declared_vars - template_vars)
            errors.append(
                f"[{schema.schema_id}] schema inconsistency: "
                f"in_template_not_declared={in_tmpl_not_decl}; "
                f"declared_not_in_template={decl_not_in_tmpl}"
            )
        return errors

    def validate_instance(self, instance: SchemaInstance) -> list[str]:
        """Validate a ``SchemaInstance``.

        Parameters
        ----------
        instance:
            Instance to validate.

        Returns
        -------
        list[str]
            List of error strings; empty means the instance is valid.
        """
        errors: list[str] = []
        if not instance.schema_id or not instance.schema_id.strip():
            errors.append(
                f"[{instance.instance_id}] 'schema_id' must be non-empty"
            )
        if not instance.bindings:
            errors.append(
                f"[{instance.instance_id}] 'bindings' must be non-empty"
            )
        if not instance.verify_bindings():
            errors.append(
                f"[{instance.instance_id}] instantiated_statement still "
                "contains unresolved placeholders"
            )
        return errors

    def validate_obligation(self, obligation: ProofObligation) -> list[str]:
        """Validate a ``ProofObligation``.

        Parameters
        ----------
        obligation:
            Obligation to validate.

        Returns
        -------
        list[str]
            List of error strings; empty means the obligation is valid.
        """
        errors: list[str] = []
        if not obligation.statement or not obligation.statement.strip():
            errors.append(
                f"[{obligation.obligation_id}] 'statement' must be non-empty"
            )
        if not obligation.conclusion or not obligation.conclusion.strip():
            errors.append(
                f"[{obligation.obligation_id}] 'conclusion' must be non-empty"
            )
        if not (0 <= obligation.priority <= 10):
            errors.append(
                f"[{obligation.obligation_id}] 'priority' must be in [0,10], "
                f"got {obligation.priority}"
            )
        if obligation.is_overdue():
            msg = (
                f"[{obligation.obligation_id}] obligation is overdue "
                f"(deadline={obligation.deadline})"
            )
            errors.append(msg if self.strict_mode else "WARNING: " + msg)
        return errors

    def run_all_checks(
        self, schemas: list[TheoremSchema]
    ) -> dict[str, list[str]]:
        """Run ``validate_schema`` on every schema and collect results.

        Parameters
        ----------
        schemas:
            List of schemas to check.

        Returns
        -------
        dict[str, list[str]]
            Mapping from ``schema_id`` to list of error strings.
        """
        results: dict[str, list[str]] = {}
        for schema in schemas:
            results[schema.schema_id] = self.validate_schema(schema)
        return results

    def report_violations(self, results: dict[str, list[str]]) -> str:
        """Format a human-readable violation report.

        Parameters
        ----------
        results:
            Dictionary as returned by ``run_all_checks``.

        Returns
        -------
        str
            Multi-line report string.  Returns ``"No violations found."`` if
            all result lists are empty.
        """
        lines: list[str] = []
        total_violations = 0
        schemas_with_violations = 0
        for schema_id, errors in results.items():
            if errors:
                schemas_with_violations += 1
                lines.append("Schema " + schema_id + ":")
                for err in errors:
                    lines.append("  - " + err)
                total_violations += len(errors)
        if not lines:
            return "No violations found."
        header = (
            "Validation report: " + str(total_violations) + " violation(s) "
            "across " + str(schemas_with_violations) + " schema(s).\n"
        )
        return header + "\n".join(lines)

    def to_json(self) -> dict:
        """Serialise validator configuration to a JSON-compatible dictionary.

        Returns
        -------
        dict
        """
        return {
            "validator_id": self.validator_id,
            "strict_mode": self.strict_mode,
            "rules": [dict(r) for r in self.rules],
        }

    def __repr__(self) -> str:
        """Return a concise developer representation."""
        return (
            "SchemaValidator(id=" + self.validator_id[:8] + "..., "
            "strict=" + str(self.strict_mode) + ", "
            "rules=" + str(len(self.rules)) + ")"
        )


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def make_simple_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
    subsystem: SubsystemKind,
    proof_style: ProofStyle = ProofStyle.DIRECT,
) -> TheoremSchema:
    """Convenience factory for creating a simple ``TheoremSchema``.

    Parameters
    ----------
    name:
        Short name for the schema.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Mapping from variable name to description.
    subsystem:
        Owning subsystem.
    proof_style:
        Proof strategy (default: ``DIRECT``).

    Returns
    -------
    TheoremSchema

    Examples
    --------
    ::

        schema = make_simple_schema(
            "encoding-roundtrip",
            "encode(decode({x})) = {x}",
            {"x": "encoded value"},
            SubsystemKind.ENCODING,
        )
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=proof_style,
        subsystem=subsystem,
    )


def batch_instantiate(
    schemas: list[TheoremSchema],
    common_bindings: dict[str, str],
) -> list[SchemaInstance]:
    """Instantiate a list of schemas with a shared binding dictionary.

    Schemas whose template variables are not fully covered by
    ``common_bindings`` are silently skipped.

    Parameters
    ----------
    schemas:
        Schemas to attempt to instantiate.
    common_bindings:
        Shared variable bindings to apply to each schema.

    Returns
    -------
    list[SchemaInstance]
        Successfully instantiated schemas (order preserved from input).
    """
    instances: list[SchemaInstance] = []
    for schema in schemas:
        errors = schema.validate_bindings(common_bindings)
        if not errors:
            instances.append(schema.instantiate(common_bindings))
    return instances


def obligations_from_instances(
    instances: list[SchemaInstance],
) -> list[ProofObligation]:
    """Convert a list of ``SchemaInstance`` objects to ``ProofObligation`` objects.

    Each instance is activated (``refresh()``) before conversion so that
    the resulting obligations are in ``ACTIVE`` state.

    Parameters
    ----------
    instances:
        Instances to convert.

    Returns
    -------
    list[ProofObligation]
        One obligation per instance (order preserved).
    """
    obligations: list[ProofObligation] = []
    for inst in instances:
        inst.refresh()
        obligations.append(inst.to_proof_obligation())
    return obligations
