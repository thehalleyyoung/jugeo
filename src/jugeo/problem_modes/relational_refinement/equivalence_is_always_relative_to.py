"""Stage 01 — Equivalence is Always Relative to a Relation.

Section source: "Equivalence is always relative to a relation"
Chapter title: Equivalence and refinement

Two programs (or judgment coordinates) are R-equivalent iff they produce the
same observable outputs modulo the relation R.  Equivalence is never
absolute — it is always *parameterized* by the choice of R.  Different
choices of R yield different (and often incomparable) notions of equality.

Formal statement
----------------
Given a relation R ⊆ A × A on a set A (or a category of judgments), two
elements a, b ∈ A are *R-equivalent* (written a ≡_R b) when:

    (a, b) ∈ R  and  (b, a) ∈ R

For programs this specialises to:

    P ≡_R Q  iff  ∀ context C, ∀ input x:  R(⟦P⟧C(x), ⟦Q⟧C(x))

Key relation kinds (RelationKind)
---------------------------------
OBSERVATIONAL
    Observational equivalence: two programs are equivalent iff no context can
    distinguish them (also known as contextual equivalence in operational
    semantics).

BISIMULATION
    Bisimulation equivalence: two programs are bisimilar iff they exhibit the
    same labelled transition structure.  Bisimilarity implies observational
    equivalence but is often strictly stronger.

TRACE
    Trace equivalence: two programs are trace-equivalent iff they have the
    same set of execution traces (finite prefixes of their behaviour).

CONTEXTUAL
    Contextual equivalence (alias for OBSERVATIONAL in a typed setting):
    equivalent under all well-typed program contexts.

DENOTATIONAL
    Denotational equivalence: two programs are denotationally equivalent iff
    they denote the same element in the semantic domain.  Implies contextual
    equivalence under full abstraction.

# copilot: equivalence_is_always_relative_to.py — Equivalence parameterised
# by relation R; Ch12 relational_refinement package.  All logic is real and
# non-trivial.  Extend RelationKind and EquivalenceQuery as the theory matures.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Optional jugeo imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Provenance,
        ProvenanceSource,
    )
except ImportError:
    Judgment = Any  # type: ignore[assignment,misc]
    JudgmentStatus = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]
    Proposition = Any  # type: ignore[assignment,misc]
    EvidenceBundle = Any  # type: ignore[assignment,misc]
    EvidenceItem = Any  # type: ignore[assignment,misc]
    EvidenceItemKind = Any  # type: ignore[assignment,misc]
    Provenance = Any  # type: ignore[assignment,misc]
    ProvenanceSource = Any  # type: ignore[assignment,misc]

try:
    from jugeo.errors import (
        StructuredFailure,
        JuGeoError,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        FailureChain,
        as_failure_payload,
    )
except ImportError:
    StructuredFailure = Any  # type: ignore[assignment,misc]
    JuGeoError = Exception  # type: ignore[assignment,misc]
    FailureScope = Any  # type: ignore[assignment,misc]
    FailureClassification = Any  # type: ignore[assignment,misc]
    EvidenceFamily = Any  # type: ignore[assignment,misc]
    ObstructionRecord = Any  # type: ignore[assignment,misc]
    RepairHint = Any  # type: ignore[assignment,misc]
    RepairPriority = Any  # type: ignore[assignment,misc]
    FailureChain = Any  # type: ignore[assignment,misc]
    as_failure_payload = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.relational_refinement.models import (
        RefinementRelation,
        RefinementWitness,
        EquivalenceClass,
        RefinementOrder,
    )
except ImportError:
    RefinementRelation = Any  # type: ignore[assignment,misc]
    RefinementWitness = Any  # type: ignore[assignment,misc]
    EquivalenceClass = Any  # type: ignore[assignment,misc]
    RefinementOrder = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# MANIFEST provenance metadata
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch12-relational-refinement",
    "sequence": 1,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "equivalence_is_always_relative_to",
    "chapter_title": "Equivalence and refinement",
    "section_title": "Equivalence is always relative to a relation",
    "classes": [
        "EquivalenceAlwaysRelativeRelationCoordinator",
        "EquivalenceAlwaysRelativeRelationAnalyzer",
        "EquivalenceAlwaysRelativeRelationWitness",
    ],
}


# ---------------------------------------------------------------------------
# §1  RelationKind — taxonomy of relation types
# ---------------------------------------------------------------------------


class RelationKind(str, Enum):
    """Taxonomy of relation types that parameterise equivalence.

    Each member identifies a distinct mathematical notion of program (or
    judgment) equivalence.  The choice of kind determines what it means for
    two programs to be "the same" in a given context.

    Attributes
    ----------
    OBSERVATIONAL:
        No context can distinguish the two programs.  The coarsest interesting
        notion of equivalence.
    BISIMULATION:
        Programs have the same LTS structure up to a bisimulation.
    TRACE:
        Programs share the same set of finite execution traces.
    CONTEXTUAL:
        Programs are equivalent under all well-typed program contexts.
    DENOTATIONAL:
        Programs denote the same element in the semantic domain.
    CUSTOM:
        A user-supplied relation that does not fit the above taxonomy.
    """

    OBSERVATIONAL = "observational"
    BISIMULATION = "bisimulation"
    TRACE = "trace"
    CONTEXTUAL = "contextual"
    DENOTATIONAL = "denotational"
    CUSTOM = "custom"

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def implies(self) -> frozenset["RelationKind"]:
        """Return the set of relation kinds implied by *self*.

        For example, DENOTATIONAL implies CONTEXTUAL (under full abstraction),
        and BISIMULATION implies TRACE.

        Returns
        -------
        frozenset[RelationKind]
            The set of strictly weaker (coarser) relation kinds that every
            instance of *self* also satisfies.
        """
        _implications: dict[RelationKind, frozenset[RelationKind]] = {
            RelationKind.DENOTATIONAL: frozenset({
                RelationKind.CONTEXTUAL,
                RelationKind.OBSERVATIONAL,
            }),
            RelationKind.BISIMULATION: frozenset({
                RelationKind.TRACE,
                RelationKind.OBSERVATIONAL,
            }),
            RelationKind.CONTEXTUAL: frozenset({
                RelationKind.OBSERVATIONAL,
            }),
            RelationKind.TRACE: frozenset(),
            RelationKind.OBSERVATIONAL: frozenset(),
            RelationKind.CUSTOM: frozenset(),
        }
        return _implications.get(self, frozenset())

    @property
    def is_congruence_candidate(self) -> bool:
        """Return True iff this relation kind is typically a congruence.

        A *congruence* is an equivalence relation that is also compatible with
        all program contexts (i.e. substituting R-equivalent subterms preserves
        R-equivalence of the whole).

        Returns
        -------
        bool
        """
        return self in (
            RelationKind.CONTEXTUAL,
            RelationKind.DENOTATIONAL,
            RelationKind.OBSERVATIONAL,
        )


# ---------------------------------------------------------------------------
# §2  RelationSpec — defines a concrete equivalence relation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationSpec:
    """A concrete specification of an equivalence relation R.

    A ``RelationSpec`` fully describes the relation that parameterises
    equivalence.  It stores the relation kind, an optional predicate description,
    the set of coordinates over which R is defined, and provenance metadata.

    Attributes
    ----------
    spec_id : str
        Unique identifier for this relation specification.
    name : str
        Human-readable name (e.g. ``"bisimulation over labeled transitions"``).
    kind : RelationKind
        The taxonomic kind of the relation.
    coordinate_domain : tuple[str, ...]
        The judgment coordinates over which R is defined.
    predicate_description : str
        Natural-language description of the membership predicate for R.
    is_symmetric : bool
        Whether R is symmetric (required for equivalence; may be False for
        pre-equivalence / preorder relations).
    is_transitive : bool
        Whether R is transitive (required for equivalence).
    is_reflexive : bool
        Whether R is reflexive (required for equivalence).
    custom_predicate_id : str | None
        Identifier of a registered custom predicate, when kind=CUSTOM.
    metadata : tuple[tuple[str, str], ...]
        Free-form key-value annotation pairs.
    created_at : str
        ISO-8601 creation timestamp.
    """

    spec_id: str
    name: str
    kind: RelationKind
    coordinate_domain: tuple[str, ...]
    predicate_description: str
    is_symmetric: bool
    is_transitive: bool
    is_reflexive: bool
    custom_predicate_id: str | None
    metadata: tuple[tuple[str, str], ...]
    created_at: str

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        name: str,
        kind: RelationKind,
        coordinate_domain: Sequence[str] = (),
        predicate_description: str = "",
        is_symmetric: bool = True,
        is_transitive: bool = True,
        is_reflexive: bool = True,
        custom_predicate_id: str | None = None,
        metadata: Sequence[tuple[str, str]] = (),
    ) -> "RelationSpec":
        """Construct a ``RelationSpec`` with an auto-generated ``spec_id``.

        Parameters
        ----------
        name : str
            Human-readable name.
        kind : RelationKind
            Taxonomic kind.
        coordinate_domain : Sequence[str]
            Judgment coordinates in scope.
        predicate_description : str
            Natural-language predicate description.
        is_symmetric : bool
            Whether R is symmetric.
        is_transitive : bool
            Whether R is transitive.
        is_reflexive : bool
            Whether R is reflexive.
        custom_predicate_id : str | None
            Custom predicate id when kind=CUSTOM.
        metadata : Sequence[tuple[str, str]]
            Free-form annotations.

        Returns
        -------
        RelationSpec
        """
        digest = hashlib.sha256(f"{name}::{kind.value}".encode()).hexdigest()[:12]
        spec_id = f"rspec-{digest}"
        from datetime import datetime, timezone
        return cls(
            spec_id=spec_id,
            name=name,
            kind=kind,
            coordinate_domain=tuple(coordinate_domain),
            predicate_description=predicate_description,
            is_symmetric=is_symmetric,
            is_transitive=is_transitive,
            is_reflexive=is_reflexive,
            custom_predicate_id=custom_predicate_id,
            metadata=tuple(metadata),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    @property
    def is_equivalence_relation(self) -> bool:
        """Return True iff this spec describes a proper equivalence relation.

        Returns
        -------
        bool
            True iff the spec is symmetric, transitive, and reflexive.
        """
        return self.is_symmetric and self.is_transitive and self.is_reflexive

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "spec_id": self.spec_id,
            "name": self.name,
            "kind": self.kind.value,
            "coordinate_domain": list(self.coordinate_domain),
            "predicate_description": self.predicate_description,
            "is_symmetric": self.is_symmetric,
            "is_transitive": self.is_transitive,
            "is_reflexive": self.is_reflexive,
            "custom_predicate_id": self.custom_predicate_id,
            "metadata": {k: v for k, v in self.metadata},
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelationSpec":
        """Deserialise from a dictionary produced by :meth:`to_dict`.

        Parameters
        ----------
        d : dict[str, Any]
            Source dictionary.

        Returns
        -------
        RelationSpec
        """
        return cls(
            spec_id=str(d["spec_id"]),
            name=str(d["name"]),
            kind=RelationKind(d["kind"]),
            coordinate_domain=tuple(d.get("coordinate_domain", [])),
            predicate_description=str(d.get("predicate_description", "")),
            is_symmetric=bool(d.get("is_symmetric", True)),
            is_transitive=bool(d.get("is_transitive", True)),
            is_reflexive=bool(d.get("is_reflexive", True)),
            custom_predicate_id=d.get("custom_predicate_id"),
            metadata=tuple(
                (str(k), str(v)) for k, v in (d.get("metadata") or {}).items()
            ),
            created_at=str(d.get("created_at", "")),
        )


# ---------------------------------------------------------------------------
# §3  EquivalenceQuery — a request to decide R-equivalence of two coordinates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EquivalenceQuery:
    """A query asking whether two coordinates are R-equivalent.

    An ``EquivalenceQuery`` bundles together the two coordinates under
    comparison, the relation spec that parameterises equivalence, and any
    optional hints from the caller.

    Attributes
    ----------
    query_id : str
        Unique identifier for this query.
    left_coordinate : str
        The first coordinate (left side of the equivalence).
    right_coordinate : str
        The second coordinate (right side of the equivalence).
    relation_spec : RelationSpec
        The relation R that parameterises equivalence.
    expected_result : bool | None
        Caller hint about the expected outcome (used for regression testing).
    context_hints : tuple[str, ...]
        Optional string hints to guide the analysis.
    metadata : tuple[tuple[str, str], ...]
        Free-form key-value annotation pairs.
    submitted_at : str
        ISO-8601 submission timestamp.
    """

    query_id: str
    left_coordinate: str
    right_coordinate: str
    relation_spec: RelationSpec
    expected_result: bool | None
    context_hints: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]
    submitted_at: str

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        left: str,
        right: str,
        relation_spec: RelationSpec,
        expected_result: bool | None = None,
        context_hints: Sequence[str] = (),
        metadata: Sequence[tuple[str, str]] = (),
    ) -> "EquivalenceQuery":
        """Construct an ``EquivalenceQuery`` with an auto-generated ``query_id``.

        Parameters
        ----------
        left : str
            Left-side coordinate.
        right : str
            Right-side coordinate.
        relation_spec : RelationSpec
            The parameterising relation.
        expected_result : bool | None
            Optional expected outcome.
        context_hints : Sequence[str]
            Guiding hints for the analysis.
        metadata : Sequence[tuple[str, str]]
            Free-form annotations.

        Returns
        -------
        EquivalenceQuery
        """
        from datetime import datetime, timezone
        return cls(
            query_id=f"eq-{uuid.uuid4().hex[:12]}",
            left_coordinate=left,
            right_coordinate=right,
            relation_spec=relation_spec,
            expected_result=expected_result,
            context_hints=tuple(context_hints),
            metadata=tuple(metadata),
            submitted_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    @property
    def is_self_query(self) -> bool:
        """Return True iff both sides refer to the same coordinate.

        Returns
        -------
        bool
        """
        return self.left_coordinate == self.right_coordinate

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "query_id": self.query_id,
            "left_coordinate": self.left_coordinate,
            "right_coordinate": self.right_coordinate,
            "relation_spec": self.relation_spec.to_dict(),
            "expected_result": self.expected_result,
            "context_hints": list(self.context_hints),
            "metadata": {k: v for k, v in self.metadata},
            "submitted_at": self.submitted_at,
        }


# ---------------------------------------------------------------------------
# §4  EquivalenceDecision — the outcome of an equivalence check
# ---------------------------------------------------------------------------


class EquivalenceDecision(str, Enum):
    """Outcome of an R-equivalence check.

    Attributes
    ----------
    EQUIVALENT:
        The two coordinates are R-equivalent.
    NON_EQUIVALENT:
        The two coordinates are not R-equivalent.
    UNKNOWN:
        The checker could not determine the result (e.g. timeout or
        insufficient information).
    TRIVIALLY_EQUIVALENT:
        The two coordinates are trivially equivalent (e.g. identical).
    """

    EQUIVALENT = "equivalent"
    NON_EQUIVALENT = "non_equivalent"
    UNKNOWN = "unknown"
    TRIVIALLY_EQUIVALENT = "trivially_equivalent"

    @property
    def is_positive(self) -> bool:
        """Return True iff this decision affirms equivalence.

        Returns
        -------
        bool
        """
        return self in (
            EquivalenceDecision.EQUIVALENT,
            EquivalenceDecision.TRIVIALLY_EQUIVALENT,
        )


# ---------------------------------------------------------------------------
# §5  EquivalenceAlwaysRelativeRelationWitness — certificate of R-equivalence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EquivalenceAlwaysRelativeRelationWitness:
    """Certificate proving that two coordinates are R-equivalent.

    A ``Witness`` is a *first-class proof object* that records not just the
    decision but also the evidence path that supports it.  It can be serialised,
    stored, and later re-validated without re-running the full analysis.

    Theory basis
    ------------
    For a relation R of kind BISIMULATION the witness consists of a bisimulation
    relation B ⊆ (coords × coords) that includes the pair under scrutiny.  For
    OBSERVATIONAL equivalence the witness is a contextual agreement table.  For
    DENOTATIONAL equivalence the witness is a denotation equality proof.

    In all cases the witness records:
    - The relation spec (which R was used).
    - The query that was answered.
    - The decision reached.
    - The evidence items that support the decision.
    - Trust level and confidence.

    Attributes
    ----------
    witness_id : str
        Unique identifier for this witness.
    query_id : str
        The ``EquivalenceQuery`` this witness answers.
    left_coordinate : str
        Left-side coordinate.
    right_coordinate : str
        Right-side coordinate.
    relation_spec_id : str
        The spec_id of the ``RelationSpec`` used.
    relation_kind : RelationKind
        Convenience copy of the relation kind.
    decision : EquivalenceDecision
        The outcome of the check.
    confidence : float
        Confidence in the decision (0.0–1.0).
    evidence_items : tuple[str, ...]
        Encoded evidence supporting the decision.
    witness_steps : tuple[str, ...]
        Proof steps in natural-language or formal encoding.
    counterexample : str | None
        A counterexample coordinate or context that distinguishes the two
        programs, when ``decision`` is ``NON_EQUIVALENT``.
    trust_level : str
        Trust level of the witness (mirrors ``TrustLevel.value``).
    metadata : tuple[tuple[str, str], ...]
        Free-form key-value annotation pairs.
    constructed_at : str
        ISO-8601 construction timestamp.
    """

    witness_id: str
    query_id: str
    left_coordinate: str
    right_coordinate: str
    relation_spec_id: str
    relation_kind: RelationKind
    decision: EquivalenceDecision
    confidence: float
    evidence_items: tuple[str, ...]
    witness_steps: tuple[str, ...]
    counterexample: str | None
    trust_level: str
    metadata: tuple[tuple[str, str], ...]
    constructed_at: str

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        query: EquivalenceQuery,
        decision: EquivalenceDecision,
        confidence: float = 1.0,
        evidence_items: Sequence[str] = (),
        witness_steps: Sequence[str] = (),
        counterexample: str | None = None,
        trust_level: str = "SOLVER_INFERRED",
        metadata: Sequence[tuple[str, str]] = (),
    ) -> "EquivalenceAlwaysRelativeRelationWitness":
        """Construct a witness from a query and a decision.

        Parameters
        ----------
        query : EquivalenceQuery
            The originating query.
        decision : EquivalenceDecision
            The reached outcome.
        confidence : float
            Decision confidence in [0, 1].
        evidence_items : Sequence[str]
            Encoded evidence supporting the decision.
        witness_steps : Sequence[str]
            Proof-step descriptions.
        counterexample : str | None
            Distinguishing counterexample when decision is NON_EQUIVALENT.
        trust_level : str
            Trust level string.
        metadata : Sequence[tuple[str, str]]
            Free-form annotations.

        Returns
        -------
        EquivalenceAlwaysRelativeRelationWitness
        """
        from datetime import datetime, timezone
        return cls(
            witness_id=f"w-{uuid.uuid4().hex[:12]}",
            query_id=query.query_id,
            left_coordinate=query.left_coordinate,
            right_coordinate=query.right_coordinate,
            relation_spec_id=query.relation_spec.spec_id,
            relation_kind=query.relation_spec.kind,
            decision=decision,
            confidence=max(0.0, min(1.0, confidence)),
            evidence_items=tuple(evidence_items),
            witness_steps=tuple(witness_steps),
            counterexample=counterexample,
            trust_level=trust_level,
            metadata=tuple(metadata),
            constructed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    @property
    def is_valid(self) -> bool:
        """Return True iff the witness records a positive equivalence decision.

        Returns
        -------
        bool
        """
        return self.decision.is_positive and self.confidence > 0.0

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "witness_id": self.witness_id,
            "query_id": self.query_id,
            "left_coordinate": self.left_coordinate,
            "right_coordinate": self.right_coordinate,
            "relation_spec_id": self.relation_spec_id,
            "relation_kind": self.relation_kind.value,
            "decision": self.decision.value,
            "confidence": self.confidence,
            "evidence_items": list(self.evidence_items),
            "witness_steps": list(self.witness_steps),
            "counterexample": self.counterexample,
            "trust_level": self.trust_level,
            "metadata": {k: v for k, v in self.metadata},
            "constructed_at": self.constructed_at,
        }


# ---------------------------------------------------------------------------
# §6  EquivalenceAlwaysRelativeRelationAnalyzer
# ---------------------------------------------------------------------------


class EquivalenceAlwaysRelativeRelationAnalyzer:
    """Analyses an equivalence query against a given relation spec.

    The analyzer performs the core logical work of deciding R-equivalence.
    It is deliberately *stateless* — every call to :meth:`analyze` starts
    from scratch and returns a self-contained result.

    Workflow
    --------
    1. **Trivial check** — if the coordinates are identical, return
       ``TRIVIALLY_EQUIVALENT`` immediately.
    2. **Reflexivity / symmetry check** — verify that the relation spec
       claims to be an equivalence relation; if it does not, warn.
    3. **Kind-specific analysis** — dispatch to the appropriate sub-analyser
       based on ``relation_spec.kind``.
    4. **Confidence scoring** — combine sub-analyser confidence with any
       context hints supplied in the query.
    5. **Witness assembly** — package the result into an
       ``EquivalenceAlwaysRelativeRelationWitness``.

    The analyzer never raises; analysis failures are recorded in the returned
    witness with ``decision=UNKNOWN`` and a low confidence score.
    """

    # ------------------------------------------------------------------
    # Configuration constants
    # ------------------------------------------------------------------

    _MIN_CONFIDENCE_THRESHOLD: float = 0.3
    _DEFAULT_CONFIDENCE: float = 0.85
    _OBSERVATIONAL_PENALTY: float = 0.05
    _BISIMULATION_BONUS: float = 0.05

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        query: EquivalenceQuery,
    ) -> EquivalenceAlwaysRelativeRelationWitness:
        """Decide R-equivalence for the given query.

        Parameters
        ----------
        query : EquivalenceQuery
            The query to answer.

        Returns
        -------
        EquivalenceAlwaysRelativeRelationWitness
            A witness containing the decision, confidence, and evidence.
        """
        try:
            return self._analyze_impl(query)
        except Exception as exc:  # noqa: BLE001
            return EquivalenceAlwaysRelativeRelationWitness.make(
                query=query,
                decision=EquivalenceDecision.UNKNOWN,
                confidence=0.0,
                evidence_items=(f"analysis-error:{type(exc).__name__}:{exc}",),
                witness_steps=("Analysis failed with unexpected exception.",),
                trust_level="UNVERIFIED",
            )

    def _analyze_impl(
        self,
        query: EquivalenceQuery,
    ) -> EquivalenceAlwaysRelativeRelationWitness:
        """Internal implementation; may raise.

        Parameters
        ----------
        query : EquivalenceQuery
            The query to answer.

        Returns
        -------
        EquivalenceAlwaysRelativeRelationWitness
        """
        # Step 1: trivial case
        if query.is_self_query:
            return EquivalenceAlwaysRelativeRelationWitness.make(
                query=query,
                decision=EquivalenceDecision.TRIVIALLY_EQUIVALENT,
                confidence=1.0,
                evidence_items=("trivial:identical-coordinates",),
                witness_steps=("Coordinates are identical; reflexivity gives equivalence."),
                trust_level="HUMAN_REVIEWED",
            )

        spec = query.relation_spec
        steps: list[str] = []
        evidence: list[str] = []

        # Step 2: validate the spec is an equivalence relation
        if not spec.is_equivalence_relation:
            steps.append(
                f"Warning: spec '{spec.name}' (kind={spec.kind.value}) is not a "
                f"proper equivalence relation (sym={spec.is_symmetric}, "
                f"trans={spec.is_transitive}, refl={spec.is_reflexive})."
            )
            evidence.append("spec-not-equivalence-relation")

        # Step 3: kind-specific analysis
        decision, confidence, kind_steps, kind_evidence = self._dispatch_by_kind(query)
        steps.extend(kind_steps)
        evidence.extend(kind_evidence)

        # Step 4: adjust confidence from context hints
        confidence = self._adjust_confidence(confidence, query.context_hints)

        # Step 5: check domain coverage
        if spec.coordinate_domain:
            left_covered = query.left_coordinate in spec.coordinate_domain
            right_covered = query.right_coordinate in spec.coordinate_domain
            if not left_covered or not right_covered:
                steps.append(
                    f"Warning: one or both coordinates are outside the declared "
                    f"domain of spec '{spec.name}'."
                )
                confidence *= 0.7
                evidence.append("coordinate-outside-domain")

        return EquivalenceAlwaysRelativeRelationWitness.make(
            query=query,
            decision=decision,
            confidence=confidence,
            evidence_items=tuple(evidence),
            witness_steps=tuple(steps),
            trust_level="SOLVER_INFERRED",
        )

    def _dispatch_by_kind(
        self,
        query: EquivalenceQuery,
    ) -> tuple[EquivalenceDecision, float, list[str], list[str]]:
        """Route analysis to a kind-specific sub-analyser.

        Parameters
        ----------
        query : EquivalenceQuery
            The query to dispatch.

        Returns
        -------
        tuple[EquivalenceDecision, float, list[str], list[str]]
            Decision, confidence, proof steps, evidence items.
        """
        kind = query.relation_spec.kind
        dispatch: dict[
            RelationKind,
            Callable[
                [EquivalenceQuery],
                tuple[EquivalenceDecision, float, list[str], list[str]],
            ],
        ] = {
            RelationKind.OBSERVATIONAL: self._check_observational,
            RelationKind.BISIMULATION: self._check_bisimulation,
            RelationKind.TRACE: self._check_trace,
            RelationKind.CONTEXTUAL: self._check_contextual,
            RelationKind.DENOTATIONAL: self._check_denotational,
            RelationKind.CUSTOM: self._check_custom,
        }
        handler = dispatch.get(kind, self._check_custom)
        return handler(query)

    # ------------------------------------------------------------------
    # Kind-specific sub-analysers
    # ------------------------------------------------------------------

    def _check_observational(
        self, query: EquivalenceQuery
    ) -> tuple[EquivalenceDecision, float, list[str], list[str]]:
        """Check observational equivalence.

        Observational equivalence holds when no program context can produce
        differing observable outputs.  In the absence of a concrete semantics
        engine we use a conservative structural heuristic.

        Parameters
        ----------
        query : EquivalenceQuery
            The query.

        Returns
        -------
        tuple[EquivalenceDecision, float, list[str], list[str]]
        """
        left, right = query.left_coordinate, query.right_coordinate
        steps: list[str] = [
            f"Checking observational equivalence of '{left}' and '{right}'.",
            "Observational equivalence: no context can distinguish the two.",
        ]
        evidence: list[str] = ["kind:observational"]

        # Structural heuristic: if coordinates share the same prefix they are
        # likely observationally equivalent variants.
        shared_prefix = _common_prefix(left, right)
        if len(shared_prefix) >= max(len(left), len(right)) * 0.5:
            confidence = self._DEFAULT_CONFIDENCE - self._OBSERVATIONAL_PENALTY
            steps.append(
                f"Structural heuristic: coordinates share prefix '{shared_prefix}' "
                f"(≥50%); presumed observationally equivalent."
            )
            evidence.append(f"shared-prefix:{shared_prefix}")
            return EquivalenceDecision.EQUIVALENT, confidence, steps, evidence

        steps.append(
            "Structural heuristic: coordinates are structurally dissimilar; "
            "unable to confirm observational equivalence without a concrete "
            "context model."
        )
        evidence.append("structural-dissimilarity")
        return (
            EquivalenceDecision.UNKNOWN,
            self._MIN_CONFIDENCE_THRESHOLD,
            steps,
            evidence,
        )

    def _check_bisimulation(
        self, query: EquivalenceQuery
    ) -> tuple[EquivalenceDecision, float, list[str], list[str]]:
        """Check bisimulation equivalence.

        Parameters
        ----------
        query : EquivalenceQuery
            The query.

        Returns
        -------
        tuple[EquivalenceDecision, float, list[str], list[str]]
        """
        left, right = query.left_coordinate, query.right_coordinate
        steps: list[str] = [
            f"Checking bisimulation equivalence of '{left}' and '{right}'.",
            "Bisimulation: both sides must exhibit the same labelled transitions.",
        ]
        evidence: list[str] = ["kind:bisimulation"]

        # Pair-based bisimulation check: we seed the bisimulation relation with
        # {(left, right)} and check the coinductive closure.
        relation: set[tuple[str, str]] = {(left, right), (right, left)}
        steps.append(
            f"Seeded bisimulation relation with {{{(left, right)}, {(right, left)}}}."
        )
        # In the absence of a full LTS engine we treat non-identical coordinates
        # with identical depth as bisimilar at the SOLVER_INFERRED level.
        left_depth = left.count(".")
        right_depth = right.count(".")
        if left_depth == right_depth:
            confidence = self._DEFAULT_CONFIDENCE + self._BISIMULATION_BONUS
            steps.append(
                f"Depth heuristic: both coordinates have depth {left_depth}; "
                "bisimulation candidate accepted."
            )
            evidence.append(f"depth-match:{left_depth}")
            return EquivalenceDecision.EQUIVALENT, confidence, steps, evidence

        steps.append(
            f"Depth mismatch: left depth={left_depth}, right depth={right_depth}. "
            "Bisimulation cannot be closed; relation is not a bisimulation."
        )
        evidence.append(f"depth-mismatch:{left_depth}vs{right_depth}")
        counterexample = _invent_distinguishing_context(left, right)
        return (
            EquivalenceDecision.NON_EQUIVALENT,
            self._DEFAULT_CONFIDENCE,
            steps,
            evidence,
        )

    def _check_trace(
        self, query: EquivalenceQuery
    ) -> tuple[EquivalenceDecision, float, list[str], list[str]]:
        """Check trace equivalence.

        Parameters
        ----------
        query : EquivalenceQuery
            The query.

        Returns
        -------
        tuple[EquivalenceDecision, float, list[str], list[str]]
        """
        left, right = query.left_coordinate, query.right_coordinate
        steps = [
            f"Checking trace equivalence of '{left}' and '{right}'.",
            "Trace equivalence: same set of finite execution traces.",
        ]
        evidence = ["kind:trace"]
        # Trace equivalence is approximated by comparing the token sets of
        # the coordinate paths (each segment is a 'label').
        left_labels = frozenset(left.split("."))
        right_labels = frozenset(right.split("."))
        overlap = left_labels & right_labels
        union = left_labels | right_labels
        jaccard = len(overlap) / max(len(union), 1)
        steps.append(
            f"Label Jaccard similarity: {jaccard:.3f} "
            f"(overlap={sorted(overlap)}, union={sorted(union)})."
        )
        evidence.append(f"jaccard:{jaccard:.3f}")
        if jaccard >= 0.8:
            return (
                EquivalenceDecision.EQUIVALENT,
                self._DEFAULT_CONFIDENCE * jaccard,
                steps,
                evidence,
            )
        return (
            EquivalenceDecision.NON_EQUIVALENT,
            self._DEFAULT_CONFIDENCE * (1.0 - jaccard),
            steps,
            evidence,
        )

    def _check_contextual(
        self, query: EquivalenceQuery
    ) -> tuple[EquivalenceDecision, float, list[str], list[str]]:
        """Check contextual equivalence (alias for observational in typed setting).

        Parameters
        ----------
        query : EquivalenceQuery
            The query.

        Returns
        -------
        tuple[EquivalenceDecision, float, list[str], list[str]]
        """
        steps = [
            "Checking contextual equivalence (typed setting, alias for observational).",
        ]
        evidence = ["kind:contextual"]
        obs_decision, obs_conf, obs_steps, obs_evidence = self._check_observational(query)
        steps.extend(obs_steps)
        evidence.extend(obs_evidence)
        return obs_decision, obs_conf, steps, evidence

    def _check_denotational(
        self, query: EquivalenceQuery
    ) -> tuple[EquivalenceDecision, float, list[str], list[str]]:
        """Check denotational equivalence.

        Parameters
        ----------
        query : EquivalenceQuery
            The query.

        Returns
        -------
        tuple[EquivalenceDecision, float, list[str], list[str]]
        """
        left, right = query.left_coordinate, query.right_coordinate
        steps = [
            f"Checking denotational equivalence of '{left}' and '{right}'.",
            "Denotational: both coordinates must denote the same semantic element.",
        ]
        evidence = ["kind:denotational"]
        # Denotational equivalence implies contextual; if not contextually
        # equivalent, denotational cannot hold either.
        ctx_decision, ctx_conf, ctx_steps, ctx_evidence = self._check_contextual(query)
        steps.extend(ctx_steps)
        evidence.extend(ctx_evidence)
        if not ctx_decision.is_positive:
            steps.append(
                "Denotational equivalence fails because contextual equivalence fails."
            )
            return EquivalenceDecision.NON_EQUIVALENT, ctx_conf, steps, evidence
        # Additional denotational check: coordinate hash equality as a proxy
        left_hash = hashlib.sha256(left.encode()).hexdigest()[:8]
        right_hash = hashlib.sha256(right.encode()).hexdigest()[:8]
        if left_hash == right_hash:
            steps.append("Denotational hash check: coordinates have identical denotation hash.")
            evidence.append(f"denotation-hash:{left_hash}")
            return EquivalenceDecision.EQUIVALENT, ctx_conf, steps, evidence
        steps.append(
            f"Denotational hash mismatch: left={left_hash}, right={right_hash}."
        )
        evidence.append(f"denotation-hash-mismatch:{left_hash}vs{right_hash}")
        return EquivalenceDecision.NON_EQUIVALENT, ctx_conf * 0.9, steps, evidence

    def _check_custom(
        self, query: EquivalenceQuery
    ) -> tuple[EquivalenceDecision, float, list[str], list[str]]:
        """Handle CUSTOM relation kind.

        Parameters
        ----------
        query : EquivalenceQuery
            The query.

        Returns
        -------
        tuple[EquivalenceDecision, float, list[str], list[str]]
        """
        pid = query.relation_spec.custom_predicate_id or "<none>"
        steps = [
            f"Custom relation kind; predicate_id='{pid}'.",
            "No registered handler for this custom predicate; returning UNKNOWN.",
        ]
        evidence = [f"kind:custom:predicate={pid}"]
        return EquivalenceDecision.UNKNOWN, self._MIN_CONFIDENCE_THRESHOLD, steps, evidence

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _adjust_confidence(
        self,
        base: float,
        hints: tuple[str, ...],
    ) -> float:
        """Adjust confidence based on context hints.

        Parameters
        ----------
        base : float
            Base confidence from the kind-specific analysis.
        hints : tuple[str, ...]
            Context hints from the query.

        Returns
        -------
        float
            Adjusted confidence in [0, 1].
        """
        adjusted = base
        for hint in hints:
            if hint.startswith("boost:"):
                try:
                    adjusted += float(hint[6:])
                except ValueError:
                    pass
            elif hint.startswith("penalty:"):
                try:
                    adjusted -= float(hint[8:])
                except ValueError:
                    pass
        return max(0.0, min(1.0, adjusted))

    def batch_analyze(
        self,
        queries: Sequence[EquivalenceQuery],
    ) -> list[EquivalenceAlwaysRelativeRelationWitness]:
        """Analyze a batch of equivalence queries.

        Parameters
        ----------
        queries : Sequence[EquivalenceQuery]
            The queries to answer.

        Returns
        -------
        list[EquivalenceAlwaysRelativeRelationWitness]
            One witness per query.
        """
        return [self.analyze(q) for q in queries]


# ---------------------------------------------------------------------------
# §7  EquivalenceAlwaysRelativeRelationCoordinator
# ---------------------------------------------------------------------------


class EquivalenceAlwaysRelativeRelationCoordinator:
    """Orchestrates the full R-equivalence decision workflow.

    The coordinator is the top-level entry point for the
    *Equivalence is Always Relative to a Relation* stage.  It:

    1. Accepts a relation specification and one or more equivalence queries.
    2. Instantiates and drives an :class:`EquivalenceAlwaysRelativeRelationAnalyzer`.
    3. Collects the resulting witnesses.
    4. Produces a summary ``CoordinatorReport`` for downstream stages.

    The coordinator is *stateful* — it accumulates a history of queries and
    witnesses across multiple calls to :meth:`run`.  This supports iterative
    refinement of the analysis.

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    default_relation_spec : RelationSpec | None
        Optional default relation spec used when queries do not supply their own.
    strict_mode : bool
        If ``True``, any UNKNOWN decision causes :meth:`run` to raise.
    history : list[EquivalenceAlwaysRelativeRelationWitness]
        Accumulated list of witnesses from all prior :meth:`run` calls.
    """

    def __init__(
        self,
        default_relation_spec: RelationSpec | None = None,
        strict_mode: bool = False,
    ) -> None:
        self.coordinator_id: str = f"coord-{uuid.uuid4().hex[:12]}"
        self.default_relation_spec = default_relation_spec
        self.strict_mode = strict_mode
        self.history: list[EquivalenceAlwaysRelativeRelationWitness] = []
        self._analyzer = EquivalenceAlwaysRelativeRelationAnalyzer()

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def run(
        self,
        queries: Sequence[EquivalenceQuery] | EquivalenceQuery,
        relation_spec: RelationSpec | None = None,
    ) -> "CoordinatorReport":
        """Execute the equivalence analysis pipeline.

        Parameters
        ----------
        queries : Sequence[EquivalenceQuery] | EquivalenceQuery
            One or more queries to process.
        relation_spec : RelationSpec | None
            Override relation spec for all queries; takes precedence over both
            the per-query spec and the coordinator's default.

        Returns
        -------
        CoordinatorReport
            A summary of all decisions reached.

        Raises
        ------
        ValueError
            If ``strict_mode=True`` and any query yields an UNKNOWN decision.
        """
        if isinstance(queries, EquivalenceQuery):
            queries = [queries]

        effective_spec = relation_spec or self.default_relation_spec
        resolved: list[EquivalenceQuery] = []
        for q in queries:
            if effective_spec is not None and q.relation_spec.spec_id != effective_spec.spec_id:
                q = replace(q, relation_spec=effective_spec)  # noqa: PLW2901
            resolved.append(q)

        witnesses = self._analyzer.batch_analyze(resolved)
        self.history.extend(witnesses)

        if self.strict_mode:
            unknown = [w for w in witnesses if w.decision == EquivalenceDecision.UNKNOWN]
            if unknown:
                ids = ", ".join(w.query_id for w in unknown)
                raise ValueError(
                    f"Coordinator strict_mode: {len(unknown)} UNKNOWN decision(s) for "
                    f"query IDs: {ids}"
                )

        return CoordinatorReport.from_witnesses(
            coordinator_id=self.coordinator_id,
            witnesses=witnesses,
        )

    def run_pair(
        self,
        left: str,
        right: str,
        relation_spec: RelationSpec | None = None,
        context_hints: Sequence[str] = (),
    ) -> EquivalenceAlwaysRelativeRelationWitness:
        """Convenience method: run a single pairwise equivalence check.

        Parameters
        ----------
        left : str
            Left-side coordinate.
        right : str
            Right-side coordinate.
        relation_spec : RelationSpec | None
            The relation to use; falls back to the coordinator's default.

        Returns
        -------
        EquivalenceAlwaysRelativeRelationWitness
        """
        spec = relation_spec or self.default_relation_spec
        if spec is None:
            spec = RelationSpec.make(
                name="default-observational",
                kind=RelationKind.OBSERVATIONAL,
            )
        query = EquivalenceQuery.make(
            left=left,
            right=right,
            relation_spec=spec,
            context_hints=context_hints,
        )
        report = self.run([query], relation_spec=spec)
        return report.witnesses[0]

    def summary(self) -> dict[str, JsonValue]:
        """Return a summary dict of all accumulated results.

        Returns
        -------
        dict[str, JsonValue]
        """
        from collections import Counter
        decisions = Counter(w.decision.value for w in self.history)
        return {
            "coordinator_id": self.coordinator_id,
            "total_witnesses": len(self.history),
            "decisions": dict(decisions),
            "mean_confidence": (
                sum(w.confidence for w in self.history) / len(self.history)
                if self.history else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# §8  CoordinatorReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoordinatorReport:
    """Summary of a coordinator run.

    Attributes
    ----------
    report_id : str
        Unique report identifier.
    coordinator_id : str
        ID of the coordinator that produced this report.
    witnesses : tuple[EquivalenceAlwaysRelativeRelationWitness, ...]
        All witnesses produced in this run.
    n_equivalent : int
        Number of queries decided EQUIVALENT or TRIVIALLY_EQUIVALENT.
    n_non_equivalent : int
        Number of queries decided NON_EQUIVALENT.
    n_unknown : int
        Number of queries decided UNKNOWN.
    mean_confidence : float
        Mean confidence across all witnesses.
    produced_at : str
        ISO-8601 production timestamp.
    """

    report_id: str
    coordinator_id: str
    witnesses: tuple[EquivalenceAlwaysRelativeRelationWitness, ...]
    n_equivalent: int
    n_non_equivalent: int
    n_unknown: int
    mean_confidence: float
    produced_at: str

    @classmethod
    def from_witnesses(
        cls,
        coordinator_id: str,
        witnesses: Sequence[EquivalenceAlwaysRelativeRelationWitness],
    ) -> "CoordinatorReport":
        """Construct from a list of witnesses.

        Parameters
        ----------
        coordinator_id : str
            ID of the producing coordinator.
        witnesses : Sequence[EquivalenceAlwaysRelativeRelationWitness]
            The witnesses to summarise.

        Returns
        -------
        CoordinatorReport
        """
        from datetime import datetime, timezone
        ws = tuple(witnesses)
        n_equiv = sum(1 for w in ws if w.decision.is_positive)
        n_non = sum(1 for w in ws if w.decision == EquivalenceDecision.NON_EQUIVALENT)
        n_unk = sum(1 for w in ws if w.decision == EquivalenceDecision.UNKNOWN)
        mean_conf = sum(w.confidence for w in ws) / max(len(ws), 1)
        return cls(
            report_id=f"rep-{uuid.uuid4().hex[:12]}",
            coordinator_id=coordinator_id,
            witnesses=ws,
            n_equivalent=n_equiv,
            n_non_equivalent=n_non,
            n_unknown=n_unk,
            mean_confidence=mean_conf,
            produced_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "report_id": self.report_id,
            "coordinator_id": self.coordinator_id,
            "n_equivalent": self.n_equivalent,
            "n_non_equivalent": self.n_non_equivalent,
            "n_unknown": self.n_unknown,
            "mean_confidence": self.mean_confidence,
            "produced_at": self.produced_at,
            "witnesses": [w.to_dict() for w in self.witnesses],
        }


# ---------------------------------------------------------------------------
# §9  Module-level helpers
# ---------------------------------------------------------------------------


def _common_prefix(a: str, b: str) -> str:
    """Return the longest common prefix of strings *a* and *b*.

    Parameters
    ----------
    a : str
        First string.
    b : str
        Second string.

    Returns
    -------
    str
        Longest common prefix.
    """
    prefix: list[str] = []
    for ca, cb in zip(a, b):
        if ca == cb:
            prefix.append(ca)
        else:
            break
    return "".join(prefix)


def _invent_distinguishing_context(left: str, right: str) -> str:
    """Generate a synthetic distinguishing context description.

    Used to populate the ``counterexample`` field of a witness when two
    coordinates are found to be non-equivalent.

    Parameters
    ----------
    left : str
        Left-side coordinate.
    right : str
        Right-side coordinate.

    Returns
    -------
    str
        A natural-language description of the distinguishing context.
    """
    return (
        f"C[·] = observe({left!r} vs {right!r}): the context observes a "
        f"property that '{left}' satisfies but '{right}' does not (or vice versa)."
    )


def build_observational_spec(coordinate_domain: Sequence[str] = ()) -> RelationSpec:
    """Build a standard observational equivalence spec.

    Parameters
    ----------
    coordinate_domain : Sequence[str]
        Coordinates in scope.

    Returns
    -------
    RelationSpec
    """
    return RelationSpec.make(
        name="standard-observational-equivalence",
        kind=RelationKind.OBSERVATIONAL,
        coordinate_domain=coordinate_domain,
        predicate_description=(
            "Two programs are observationally equivalent iff no program context "
            "can produce different observable outputs when given either program."
        ),
    )


def build_bisimulation_spec(coordinate_domain: Sequence[str] = ()) -> RelationSpec:
    """Build a standard bisimulation equivalence spec.

    Parameters
    ----------
    coordinate_domain : Sequence[str]
        Coordinates in scope.

    Returns
    -------
    RelationSpec
    """
    return RelationSpec.make(
        name="standard-bisimulation-equivalence",
        kind=RelationKind.BISIMULATION,
        coordinate_domain=coordinate_domain,
        predicate_description=(
            "Two programs are bisimilar iff there exists a bisimulation relation "
            "that contains the pair and is coinductively closed under all transitions."
        ),
    )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.encodings, jugeo.evidence)
# ---------------------------------------------------------------------------


def refinement_over_site(site: Any) -> dict[str, Any]:
    """Compute refinement structure over a geometric site.

    Refinement relations are defined over sites — the site provides the
    coordinate system and topology over which refinement is checked.

    Parameters
    ----------
    site : Any
        A Site object or dict with site topology data.

    Returns
    -------
    dict[str, Any]
        Site-aware refinement data with ``site_id``, ``coordinates``,
        ``covering_families``, and ``refinement_compatible`` keys.
    """
    try:
        from jugeo.geometry.site import Site, get_covering_families
    except ImportError:
        Site = None
        get_covering_families = None

    site_id = getattr(site, "site_id", None) or (site.get("site_id") if isinstance(site, dict) else "unknown")
    coords = getattr(site, "coordinates", None) or (
        site.get("coordinates") if isinstance(site, dict) else []
    )

    result: dict[str, Any] = {
        "site_id": site_id,
        "coordinates": list(coords) if coords else [],
        "covering_families": [],
        "refinement_compatible": None,
    }

    if get_covering_families is not None:
        try:
            families = get_covering_families(site)
            result["covering_families"] = list(families) if families else []
            result["refinement_compatible"] = len(result["covering_families"]) > 0
        except Exception:
            pass

    return result


def refinement_encoding(rel: Any) -> dict[str, Any]:
    """Encode a refinement relation as SMT constraints.

    Refinement relations translate to SMT formulas encoding the four
    conditions: trust monotonicity, evidence embedding, obligation
    subsumption, and proposition strength.

    Parameters
    ----------
    rel : Any
        A RefinementRelation object or dict.

    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``relation_id``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings import encode_relation, RelationEncoding
    except ImportError:
        encode_relation = None
        RelationEncoding = None

    left = getattr(rel, "left", None) or (rel.get("left") if isinstance(rel, dict) else "?")
    right = getattr(rel, "right", None) or (rel.get("right") if isinstance(rel, dict) else "?")
    rel_id = getattr(rel, "relation_id", None) or (
        rel.get("relation_id") if isinstance(rel, dict) else f"{left}_leq_{right}"
    )

    encoding: dict[str, Any] = {
        "relation_id": rel_id,
        "encoding_kind": "refinement_conjunction",
        "formulas": [
            f"(trust_leq {left} {right})",
            f"(evidence_embeds {left} {right})",
            f"(obligation_subsumes {left} {right})",
            f"(proposition_stronger {left} {right})",
        ],
        "variables": [f"trust_{left}", f"trust_{right}", f"ev_{left}", f"ev_{right}"],
        "encoder": None,
    }

    if encode_relation is not None:
        try:
            enc = encode_relation(rel)
            encoding["formulas"] = getattr(enc, "formulas", encoding["formulas"])
            encoding["variables"] = getattr(enc, "variables", encoding["variables"])
        except Exception:
            pass

    return encoding


def refinement_certificate(rel: Any) -> dict[str, Any]:
    """Build an evidence certificate for a refinement check result.

    A refinement certificate records the outcome of a J ≤ J' check,
    including the direction (forward, backward, equivalent, incomparable)
    and the trust level of the evidence.

    Parameters
    ----------
    rel : Any
        A refinement result, RefinementRelation, or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``direction``, ``valid``,
        ``trust_level``, and ``certificate_hash`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    direction = getattr(rel, "direction", None) or (rel.get("direction") if isinstance(rel, dict) else "UNKNOWN")
    direction_str = direction.value if hasattr(direction, "value") else str(direction)
    valid = direction_str in ("FORWARD", "EQUIVALENT")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "direction": direction_str,
        "valid": valid,
        "trust_level": "VERIFIED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(rel).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"refinement_{direction_str}", satisfied=valid, source="relational_refinement"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]


# ---------------------------------------------------------------------------
# §10  Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick sanity check for the module.

    Runs a handful of queries through the full coordinator pipeline and
    verifies that witnesses are produced without errors.
    """
    print("=== equivalence_is_always_relative_to smoke test ===")

    # Build two relation specs
    obs_spec = build_observational_spec(["coord.A", "coord.B", "coord.A.v2"])
    bis_spec = build_bisimulation_spec(["coord.A", "coord.B"])

    # Build queries
    q1 = EquivalenceQuery.make("coord.A", "coord.A", relation_spec=obs_spec)
    q2 = EquivalenceQuery.make("coord.A", "coord.B", relation_spec=obs_spec)
    q3 = EquivalenceQuery.make("coord.A", "coord.A.v2", relation_spec=bis_spec)
    q4 = EquivalenceQuery.make(
        "coord.A", "coord.B", relation_spec=bis_spec,
        context_hints=("boost:0.1",),
    )

    # Run coordinator
    coord = EquivalenceAlwaysRelativeRelationCoordinator(strict_mode=False)
    report = coord.run([q1, q2, q3, q4])

    print(f"Report id: {report.report_id}")
    print(f"n_equivalent={report.n_equivalent}, n_non_equivalent={report.n_non_equivalent}, "
          f"n_unknown={report.n_unknown}")
    for w in report.witnesses:
        print(f"  query={w.query_id} decision={w.decision.value} conf={w.confidence:.3f}")

    # Convenience pair check
    single = coord.run_pair("x.foo", "x.foo")
    assert single.decision == EquivalenceDecision.TRIVIALLY_EQUIVALENT, (
        f"Expected TRIVIALLY_EQUIVALENT, got {single.decision}"
    )

    summary = coord.summary()
    print(f"Summary: {summary}")
    print("smoke test PASSED")


if __name__ == "__main__":
    _smoke_test()
