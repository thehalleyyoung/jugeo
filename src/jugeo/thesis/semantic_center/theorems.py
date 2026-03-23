"""
jugeo.thesis.semantic_center.theorems
=======================================

Formal theorem statements from theory2.tex Chapters 1–2.

This module provides machine-readable representations of the theorems,
lemmas, and definitions stated in theory2.tex Chapters 1–2 (Introduction
and Research Claims).

Each theorem is an instance of ``TheoremStatement`` — a frozen dataclass
that records the theorem's number, name, statement, proof strategy, evidence
status, and Copilot annotation.

``TheoremCatalog`` assembles all theorems and provides query and summary
methods.

Theorems from Chapter 1
-----------------------
* T1.1  — Semantic-Center Existence
* T1.2  — Judgment-Tuple Sufficiency
* T1.3  — Trust-Algebra Partial-Order Theorem
* T1.4  — Sheaf-Gluing Theorem for Semantic Sections
* D1.1  — (Definition) The Eight-Component Judgment Tuple
* D1.2  — (Definition) The Trust Ordered Algebra
* D1.3  — (Definition) The Semantic Product Space

Theorems from Chapter 2
-----------------------
* T2.1  — AG+DTT+AI Synthesis Theorem
* T2.2  — No-Silent-Promotion Enforcement Theorem
* T2.3  — Obstruction-Persistence Theorem
* L2.1  — (Lemma) Meet-Operator Conservatism

References
----------
* theory2.tex §1–§2 — Introduction and Research Claims
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from jugeo.errors import (
    FailureClassification,
    FailureScope,
    JuGeoError,
    StructuredFailure,
    raise_with_scope,
)
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    Judgment,
    Proposition,
    PropositionKind,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.evidence.trust import TrustAlgebra

__all__ = [
    "TheoremKind",
    "ProofStrategy",
    "TheoremStatement",
    "TheoremCatalog",
    "THEOREM_CATALOG",
]


# ---------------------------------------------------------------------------
# Supporting enumerations
# ---------------------------------------------------------------------------


class TheoremKind(str, Enum):
    """Kind of formal mathematical statement.

    Values
    ------
    THEOREM
        A major result with a full proof.
    LEMMA
        A subsidiary result used in the proof of a theorem.
    COROLLARY
        A result that follows directly from a theorem.
    DEFINITION
        A formal definition introducing a new concept.
    AXIOM
        An assumption taken as a primitive.
    CONJECTURE
        A statement believed to be true but not yet proved.
    PROPOSITION
        A minor result with a brief proof.
    """

    THEOREM = "theorem"
    LEMMA = "lemma"
    COROLLARY = "corollary"
    DEFINITION = "definition"
    AXIOM = "axiom"
    CONJECTURE = "conjecture"
    PROPOSITION = "proposition"

    def abbreviation(self) -> str:
        """Return the abbreviation used in theorem numbering.

        Returns
        -------
        str
        """
        return {
            "theorem": "T",
            "lemma": "L",
            "corollary": "C",
            "definition": "D",
            "axiom": "A",
            "conjecture": "Conj",
            "proposition": "P",
        }[self.value]


class ProofStrategy(str, Enum):
    """Primary proof strategy for a theorem.

    Values
    ------
    SHEAF_THEORY
        Proof by sheaf-theoretic methods (sections, restriction maps, gluing).
    TRUST_ALGEBRA
        Proof by algebraic manipulation of the trust ordered algebra.
    INDUCTION
        Proof by structural or mathematical induction.
    DIRECT
        Direct proof from definitions.
    COUNTEREXAMPLE
        Proof by counterexample (for negative results).
    CONSTRUCTION
        Proof by explicit construction of the claimed object.
    COHOMOLOGY
        Proof using Čech cohomology computation.
    TYPE_THEORY
        Proof by type-theoretic methods.
    PENDING
        Proof not yet formalized.
    """

    SHEAF_THEORY = "sheaf_theory"
    TRUST_ALGEBRA = "trust_algebra"
    INDUCTION = "induction"
    DIRECT = "direct"
    COUNTEREXAMPLE = "counterexample"
    CONSTRUCTION = "construction"
    COHOMOLOGY = "cohomology"
    TYPE_THEORY = "type_theory"
    PENDING = "pending"


# ---------------------------------------------------------------------------
# TheoremStatement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremStatement:
    """A formal theorem (or definition/lemma/etc.) from theory2.tex.

    Parameters
    ----------
    theorem_id:
        Canonical identifier (e.g. ``"T1.1"``).
    kind:
        The kind of statement (theorem, definition, lemma, etc.).
    chapter:
        Chapter number in theory2.tex.
    section:
        Section reference in theory2.tex.
    name:
        Short name for the theorem (e.g. ``"Semantic-Center Existence"``).
    statement:
        The formal or semi-formal statement of the theorem.
    proof_sketch:
        A brief sketch of the proof or construction.
    proof_strategy:
        The primary proof strategy used.
    assumptions:
        Tuple of assumptions/hypotheses required for the theorem.
    conclusions:
        Tuple of conclusions asserted by the theorem.
    evidence_trust_level:
        Trust level of the current evidence for this theorem's validity.
    formalized:
        Whether a machine-checkable formalization exists.
    references:
        Tuple of references (other theorem IDs or external sources).
    copilot_annotation:
        Copilot annotation on this theorem.
    metadata:
        Additional metadata.

    Notes
    -----
    ``TheoremStatement`` is frozen (immutable).  It is a static declaration of
    a theorem, not a live claim.  For live evidence tracking, use
    ``ThesisClaim`` from ``models.py``.

    Examples
    --------
    >>> t = THEOREM_CATALOG.get("T1.1")
    >>> assert t is not None
    >>> print(t.summary())
    """

    theorem_id: str
    kind: TheoremKind
    chapter: int
    section: str
    name: str
    statement: str
    proof_sketch: str
    proof_strategy: ProofStrategy
    assumptions: tuple[str, ...]
    conclusions: tuple[str, ...]
    evidence_trust_level: TrustLevel = TrustLevel.ORACLE_PROPOSED
    formalized: bool = False
    references: tuple[str, ...] = ()
    copilot_annotation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_proved(self) -> bool:
        """Return ``True`` iff the evidence trust level is at least SOLVER_DISCHARGED.

        Returns
        -------
        bool
        """
        return self.evidence_trust_level >= TrustLevel.SOLVER_DISCHARGED

    def is_definition(self) -> bool:
        """Return ``True`` iff this is a definition (not a theorem).

        Returns
        -------
        bool
        """
        return self.kind == TheoremKind.DEFINITION

    def label(self) -> str:
        """Return the canonical label for this theorem.

        Returns
        -------
        str
            E.g. ``"Theorem 1.1 (Semantic-Center Existence)"``.
        """
        kind_name = self.kind.value.capitalize()
        return f"{kind_name} {self.theorem_id.lstrip('TLDCAConj')} ({self.name})"

    def summary(self) -> str:
        """Return a one-to-three line summary.

        Returns
        -------
        str
        """
        status = "✓" if self.is_proved() else "○"
        formal = "[formalized]" if self.formalized else "[informal]"
        return (
            f"{status} {self.kind.abbreviation()}{self.theorem_id[1:]} "
            f"({self.section}): {self.name} {formal}\n"
            f"  Trust: {self.evidence_trust_level.name}\n"
            f"  Statement: {self.statement[:120]}…"
            if len(self.statement) > 120
            else f"  Statement: {self.statement}"
        )

    def as_proposition(self) -> Proposition:
        """Return the theorem statement as a ``Proposition`` object.

        Returns
        -------
        Proposition
        """
        return Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=self.statement,
            free_variables=(),
            metadata={
                "theorem_id": self.theorem_id,
                "section": self.section,
                "strategy": self.proof_strategy.value,
            },
        )

    def trust_annotation(self) -> TrustAnnotation:
        """Return a ``TrustAnnotation`` reflecting this theorem's evidence status.

        Returns
        -------
        TrustAnnotation
        """
        return TrustAnnotation(
            level=self.evidence_trust_level,
            evidence_basis=(self.theorem_id,),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"{self.theorem_id}: {self.name}",),
        )

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary.

        Returns
        -------
        str
        """
        kind_str = self.kind.value.upper()
        status = "PROVED" if self.is_proved() else "PENDING"
        asms = "\n".join(f"  · {a}" for a in self.assumptions) if self.assumptions else "  (none)"
        cons = "\n".join(f"  · {c}" for c in self.conclusions) if self.conclusions else "  (none)"
        refs = ", ".join(self.references) if self.references else "none"
        ann = f"\nCopilot: {self.copilot_annotation}" if self.copilot_annotation else ""
        return "\n".join([
            f"[{self.theorem_id}] {kind_str}: {self.name}",
            f"Section: {self.section} | Strategy: {self.proof_strategy.value} | Status: {status}",
            f"Trust: {self.evidence_trust_level.name} | Formalized: {self.formalized}",
            "",
            "Statement:",
            f"  {textwrap.fill(self.statement, 76)}",
            "",
            "Proof sketch:",
            f"  {textwrap.fill(self.proof_sketch, 76)}",
            "",
            "Assumptions:",
            asms,
            "",
            "Conclusions:",
            cons,
            "",
            f"References: {refs}",
        ]) + ann

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorem_id": self.theorem_id,
            "kind": self.kind.value,
            "chapter": self.chapter,
            "section": self.section,
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "proof_strategy": self.proof_strategy.value,
            "assumptions": list(self.assumptions),
            "conclusions": list(self.conclusions),
            "evidence_trust_level": self.evidence_trust_level.name,
            "formalized": self.formalized,
            "references": list(self.references),
            "copilot_annotation": self.copilot_annotation,
        }


# ---------------------------------------------------------------------------
# TheoremCatalog
# ---------------------------------------------------------------------------


class TheoremCatalog:
    """Catalog of formal theorem statements from theory2.tex Chapters 1–2.

    ``TheoremCatalog`` is the machine-readable index of all theorems,
    definitions, and lemmas in theory2.tex Chapters 1–2.

    It provides:
    * Lookup by theorem ID.
    * Filtering by chapter, kind, proof strategy, or evidence trust level.
    * A combined evidence trust level for the entire chapter.
    * A Copilot-navigable summary.

    Parameters
    ----------
    theorems:
        The tuple of ``TheoremStatement`` objects in this catalog.

    Examples
    --------
    >>> catalog = THEOREM_CATALOG
    >>> t = catalog.get("T1.1")
    >>> print(t.copilot_summary())
    >>> proved = catalog.proved_theorems()
    """

    def __init__(
        self,
        theorems: tuple[TheoremStatement, ...] | None = None,
    ) -> None:
        """Initialize the catalog.

        Parameters
        ----------
        theorems:
            Theorems to include.  If ``None``, the canonical theorems are used.
        """
        self._theorems: dict[str, TheoremStatement] = {}
        stmts = theorems if theorems is not None else _build_canonical_theorems()
        for t in stmts:
            self._theorems[t.theorem_id] = t
        self._algebra = TrustAlgebra()

    def get(self, theorem_id: str) -> TheoremStatement | None:
        """Return the theorem with the given ID.

        Parameters
        ----------
        theorem_id:
            Theorem identifier (e.g. ``"T1.1"``).

        Returns
        -------
        TheoremStatement | None
        """
        return self._theorems.get(theorem_id)

    def all_theorems(self) -> list[TheoremStatement]:
        """Return all theorem statements.

        Returns
        -------
        list[TheoremStatement]
        """
        return list(self._theorems.values())

    def by_chapter(self, chapter: int) -> list[TheoremStatement]:
        """Return all theorems in a given chapter.

        Parameters
        ----------
        chapter:
            Chapter number (1 or 2).

        Returns
        -------
        list[TheoremStatement]
        """
        return [t for t in self._theorems.values() if t.chapter == chapter]

    def by_kind(self, kind: TheoremKind) -> list[TheoremStatement]:
        """Return all statements of a given kind.

        Parameters
        ----------
        kind:
            ``TheoremKind`` value to filter by.

        Returns
        -------
        list[TheoremStatement]
        """
        return [t for t in self._theorems.values() if t.kind == kind]

    def proved_theorems(self) -> list[TheoremStatement]:
        """Return all theorems with evidence trust level ≥ SOLVER_DISCHARGED.

        Returns
        -------
        list[TheoremStatement]
        """
        return [t for t in self._theorems.values() if t.is_proved()]

    def pending_theorems(self) -> list[TheoremStatement]:
        """Return all theorems with evidence trust level < SOLVER_DISCHARGED.

        Returns
        -------
        list[TheoremStatement]
        """
        return [t for t in self._theorems.values() if not t.is_proved()]

    def formalized_theorems(self) -> list[TheoremStatement]:
        """Return all formalized theorems.

        Returns
        -------
        list[TheoremStatement]
        """
        return [t for t in self._theorems.values() if t.formalized]

    def combined_trust(self) -> TrustLevel:
        """Return the combined trust level across all theorems.

        Returns
        -------
        TrustLevel
        """
        levels = [t.evidence_trust_level for t in self._theorems.values()]
        if not levels:
            return TrustLevel.UNVERIFIED
        result = levels[0]
        for level in levels[1:]:
            result = self._algebra.compose(result, level)
        return result

    def validate(self) -> list[StructuredFailure]:
        """Validate all theorem records.

        Returns
        -------
        list[StructuredFailure]
        """
        failures: list[StructuredFailure] = []
        seen_ids: set[str] = set()
        for t in self._theorems.values():
            if t.theorem_id in seen_ids:
                failures.append(StructuredFailure(
                    message=f"Duplicate theorem ID: {t.theorem_id!r}",
                    scope=FailureScope.CHAPTER,
                    classification=FailureClassification.INVALID_VALUE,
                ))
            seen_ids.add(t.theorem_id)
            if not t.statement:
                failures.append(StructuredFailure(
                    message=f"Theorem {t.theorem_id!r} has empty statement",
                    scope=FailureScope.CHAPTER,
                    classification=FailureClassification.INVALID_VALUE,
                ))
        return failures

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary of the catalog.

        Returns
        -------
        str
        """
        total = len(self._theorems)
        proved = len(self.proved_theorems())
        formalized = len(self.formalized_theorems())
        combined = self.combined_trust()

        ch1 = self.by_chapter(1)
        ch2 = self.by_chapter(2)

        ch1_lines = "\n".join(f"  {t.summary()}" for t in ch1)
        ch2_lines = "\n".join(f"  {t.summary()}" for t in ch2)

        return "\n".join([
            "TheoremCatalog (theory2.tex Chapters 1–2)",
            f"Total: {total} | Proved: {proved} | Formalized: {formalized}",
            f"Combined trust: {combined.name}",
            "",
            "Chapter 1 theorems:",
            ch1_lines,
            "",
            "Chapter 2 theorems:",
            ch2_lines,
            "",
            "Copilot: Use catalog.get('T1.1') to retrieve a specific theorem.",
            "  Use proved_theorems() to see which are solver-discharged or above.",
        ])

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "total": len(self._theorems),
            "combined_trust": self.combined_trust().name,
            "theorems": [t.to_dict() for t in self._theorems.values()],
        }


# ---------------------------------------------------------------------------
# Canonical theorem instances
# ---------------------------------------------------------------------------


def _build_canonical_theorems() -> tuple[TheoremStatement, ...]:
    """Build the canonical theorem statements for Chapters 1–2.

    Returns
    -------
    tuple[TheoremStatement, ...]
    """
    return (
        # ---------------------------------------------------------------
        # Chapter 1 Definitions
        # ---------------------------------------------------------------
        TheoremStatement(
            theorem_id="D1.1",
            kind=TheoremKind.DEFINITION,
            chapter=1,
            section="§1.2",
            name="The Eight-Component Judgment Tuple",
            statement=(
                "A judgment J is an eight-tuple J = (c, φ, A, E, O, B, T, Π) "
                "where: c is a coordinate in the semantic product space Σ; "
                "φ is a proposition (dependent type formula); "
                "A is a carrier type; "
                "E is an evidence bundle; "
                "O is a tuple of residual obligations; "
                "B is a tuple of obstructions; "
                "T is a trust annotation from the ordered algebra; "
                "Π is a provenance record."
            ),
            proof_sketch=(
                "This is a definition, not a theorem.  The eight components "
                "are motivated by the need for a coordinate system that subsumes "
                "all existing semantic verification approaches."
            ),
            proof_strategy=ProofStrategy.DIRECT,
            assumptions=(),
            conclusions=(
                "The eight-component tuple is well-typed: each component has a "
                "distinct type in the dependent-type theory.",
            ),
            evidence_trust_level=TrustLevel.VERIFIED_PROOF,
            formalized=True,
            references=("T1.2",),
            copilot_annotation=(
                "Copilot: This is the central definition of JuGeo.  All other "
                "definitions and theorems refer back to this tuple."
            ),
        ),
        TheoremStatement(
            theorem_id="D1.2",
            kind=TheoremKind.DEFINITION,
            chapter=1,
            section="§1.3",
            name="The Trust Ordered Algebra",
            statement=(
                "The trust ordered algebra is the tuple T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) "
                "where: E_adm is the set of admissible evidence configurations; "
                "⪯ is a partial order on trust levels; "
                "⊕ is the meet (greatest lower bound) in ⪯; "
                "⊖ is monotone attenuation; "
                "↑_π is promotion with explicit justification π; "
                "↓_χ is demotion (challenge) by contradictory evidence χ."
            ),
            proof_sketch=(
                "This is a definition.  The algebra is well-defined: ⊕ is "
                "associative and commutative; ↑_π is injective in the justification "
                "parameter; ↓_χ is idempotent.  These properties are verified "
                "in T1.3."
            ),
            proof_strategy=ProofStrategy.DIRECT,
            assumptions=(),
            conclusions=(
                "T is a well-defined algebra with the stated operators.",
            ),
            evidence_trust_level=TrustLevel.SOLVER_DISCHARGED,
            formalized=True,
            references=("T1.3",),
            copilot_annotation=(
                "Copilot: Trust is an algebra, not a float.  This definition "
                "is what 'no silent promotion' means formally."
            ),
        ),
        TheoremStatement(
            theorem_id="D1.3",
            kind=TheoremKind.DEFINITION,
            chapter=1,
            section="§1.4",
            name="The Semantic Product Space",
            statement=(
                "The semantic product space is Σ = ∏_{c:C} F(c) where C is the "
                "coordinate type and F : C → Type is a sheaf of judgment types.  "
                "A point in Σ is a judgment J = (c, φ, A, E, O, B, T, Π)."
            ),
            proof_sketch=(
                "This is a definition.  The product structure is well-typed "
                "as a dependent product in the underlying DTT.  The sheaf "
                "structure is defined in T1.4."
            ),
            proof_strategy=ProofStrategy.TYPE_THEORY,
            assumptions=("The coordinate type C is a set.",),
            conclusions=(
                "Σ is a well-defined dependent product type.",
                "The eight-component judgment tuple is a section of the sheaf F.",
            ),
            evidence_trust_level=TrustLevel.SOLVER_DISCHARGED,
            formalized=True,
            references=("D1.1", "T1.4"),
            copilot_annotation=(
                "Copilot: This definition gives the semantic product space its "
                "geometric meaning.  Points in Σ are judgments; open sets are "
                "evidence channels; sections are evidence bundles."
            ),
        ),
        # ---------------------------------------------------------------
        # Chapter 1 Theorems
        # ---------------------------------------------------------------
        TheoremStatement(
            theorem_id="T1.1",
            kind=TheoremKind.THEOREM,
            chapter=1,
            section="§1.3",
            name="Semantic-Center Existence",
            statement=(
                "For any finite corpus of AI-generated mathematical artifacts A "
                "and any evidence pipeline P, there exists a non-empty sub-space "
                "Σ* ⊆ Σ (the semantic center) such that: (1) every judgment in Σ* "
                "is at trust level ≥ ORACLE_PROPOSED; (2) every pair of judgments "
                "in Σ* satisfies the gluing condition; (3) Σ* is maximal with "
                "respect to properties (1) and (2)."
            ),
            proof_sketch=(
                "Take Σ* = {J ∈ Σ : T.level ≥ ORACLE_PROPOSED ∧ J is compatible "
                "with all other judgments in Σ*}.  Non-emptiness follows from the "
                "fact that every artifact in A produces at least one ORACLE_PROPOSED "
                "judgment via the AI oracle.  Maximality follows from Zorn's lemma "
                "on the poset of gluing-compatible, trust-satisfying subspaces."
            ),
            proof_strategy=ProofStrategy.SHEAF_THEORY,
            assumptions=(
                "The artifact corpus A is finite.",
                "The evidence pipeline P is deterministic.",
                "The trust algebra T is well-defined (see D1.2).",
            ),
            conclusions=(
                "The semantic center Σ* exists and is non-empty.",
                "Σ* is unique (by maximality).",
            ),
            evidence_trust_level=TrustLevel.ORACLE_PROPOSED,
            formalized=False,
            references=("D1.1", "D1.2", "D1.3"),
            copilot_annotation=(
                "Copilot: T1.1 justifies the existence of the semantic center.  "
                "The SemanticCenter class in judgment_geometry_as_the_semantic.py "
                "is the computational realization of Σ*."
            ),
        ),
        TheoremStatement(
            theorem_id="T1.2",
            kind=TheoremKind.THEOREM,
            chapter=1,
            section="§1.2",
            name="Judgment-Tuple Sufficiency",
            statement=(
                "The eight-component judgment tuple J = (c, φ, A, E, O, B, T, Π) "
                "is sufficient for semantic verification of AI-generated mathematics: "
                "for every semantic property S that can be expressed in dependent "
                "type theory, there exists a predicate P : Judgment → Bool such that "
                "S(A) holds iff P(J) = True for the judgment J associated with A."
            ),
            proof_sketch=(
                "By induction on the structure of S.  Base cases: S is a proposition "
                "about the proposition φ (use the φ component); S is a trust "
                "requirement (use the T component); S is an obligation requirement "
                "(use the O component); S is an obstruction requirement (use the B "
                "component).  Inductive cases: S = S₁ ∧ S₂ (conjoin predicates); "
                "S = ∃x.S(x) (use the free-variable structure of φ).  The carrier A "
                "handles type-dependent properties; provenance Π handles lineage "
                "requirements; coordinate c handles locality requirements."
            ),
            proof_strategy=ProofStrategy.INDUCTION,
            assumptions=(
                "S is expressible in dependent type theory.",
                "The judgment J is well-formed (all components type-check).",
            ),
            conclusions=(
                "Every DTT-expressible semantic property is captured by the tuple.",
                "The tuple is a complete coordinate system for semantic verification.",
            ),
            evidence_trust_level=TrustLevel.ORACLE_PROPOSED,
            formalized=False,
            references=("D1.1", "D1.3"),
            copilot_annotation=(
                "Copilot: T1.2 is the 'completeness' claim for the judgment tuple.  "
                "It says you don't need more than eight components — the tuple is "
                "sufficient.  The proof sketch is an induction on the DTT grammar "
                "of semantic properties."
            ),
        ),
        TheoremStatement(
            theorem_id="T1.3",
            kind=TheoremKind.THEOREM,
            chapter=1,
            section="§1.3",
            name="Trust-Algebra Partial-Order Theorem",
            statement=(
                "The trust ordered algebra T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) forms "
                "a meet-semilattice under ⪯ and ⊕.  Specifically: (1) ⪯ is a "
                "partial order on E_adm; (2) ⊕ is the meet (greatest lower bound) "
                "under ⪯; (3) ⊖ is monotone (t₁ ⪯ t₂ → ⊖(t₁) ⪯ ⊖(t₂)); "
                "(4) ↑_π is the step-up function (requires non-empty π); "
                "(5) ↓_χ is the step-down function (requires challenge χ)."
            ),
            proof_sketch=(
                "Properties (1)–(5) follow directly from the definition of the "
                "algebra (D1.2) and the implementation in evidence/trust.py.  "
                "The partial order is computed via the precomputed transitive closure "
                "of the trust-level dependency graph.  Monotonicity of ⊖ is "
                "immediate from the definition.  ↑_π and ↓_χ are step functions "
                "clamped at the top and bottom of the partial order, respectively."
            ),
            proof_strategy=ProofStrategy.TRUST_ALGEBRA,
            assumptions=(
                "The trust levels form a finite directed acyclic graph (DAG).",
                "The transitive closure of the DAG is precomputed.",
            ),
            conclusions=(
                "(E_adm, ⪯, ⊕) is a meet-semilattice.",
                "⊖ is monotone.",
                "↑_π is injective in the justification argument.",
                "No silent promotion is possible (↑_π requires non-empty π).",
            ),
            evidence_trust_level=TrustLevel.SOLVER_DISCHARGED,
            formalized=True,
            references=("D1.2",),
            copilot_annotation=(
                "Copilot: T1.3 is the algebraic backbone of 'no silent promotion'.  "
                "The key conclusion is the last one: ↑_π requires a non-empty "
                "justification — promotion without reason is blocked at the algebra level."
            ),
        ),
        TheoremStatement(
            theorem_id="T1.4",
            kind=TheoremKind.THEOREM,
            chapter=1,
            section="§1.5",
            name="Sheaf-Gluing Theorem for Semantic Sections",
            statement=(
                "Let U = {U_i}_{i∈I} be the open cover of Σ by evidence channels.  "
                "Let {σ_i : U_i → F(U_i)} be a collection of local sections.  "
                "If σ_i|_{U_ij} = σ_j|_{U_ij} for all overlaps U_ij = U_i ∩ U_j, "
                "then there exists a unique global section σ : ⋃U_i → F(⋃U_i) "
                "such that σ|_{U_i} = σ_i for all i.  Failure of the compatibility "
                "condition determines an element of H¹(U, F) (Čech 1-cocycle)."
            ),
            proof_sketch=(
                "Standard sheaf theory: (1) existence: define σ(x) = σ_i(x) for "
                "any i such that x ∈ U_i; the compatibility condition ensures this "
                "is well-defined on overlaps.  (2) Uniqueness: any two sections "
                "agreeing on each U_i must agree on their union by the sheaf axiom.  "
                "(3) Obstruction: the Čech coboundary of {σ_i - σ_j}_{ij} is a "
                "well-defined Čech 1-cocycle in Z¹(U, F)."
            ),
            proof_strategy=ProofStrategy.SHEAF_THEORY,
            assumptions=(
                "F is a sheaf of sets over Σ with the Grothendieck topology "
                "induced by the evidence channels.",
                "The open cover U is finite.",
            ),
            conclusions=(
                "Compatible local sections glue to a unique global section.",
                "Incompatible sections determine a Čech 1-cocycle obstruction.",
            ),
            evidence_trust_level=TrustLevel.VERIFIED_PROOF,
            formalized=False,
            references=("D1.3", "T1.1"),
            copilot_annotation=(
                "Copilot: T1.4 is the gluing theorem.  It is what makes JuGeo "
                "'sheaf-theoretic' rather than merely geometric.  The B component "
                "of the judgment tuple is the computational representation of the "
                "Čech 1-cocycle obstruction."
            ),
        ),
        # ---------------------------------------------------------------
        # Chapter 2 Theorems
        # ---------------------------------------------------------------
        TheoremStatement(
            theorem_id="T2.1",
            kind=TheoremKind.THEOREM,
            chapter=2,
            section="§2.1",
            name="AG+DTT+AI Synthesis Theorem",
            statement=(
                "The three intellectual traditions AG (sheaf theory), DTT "
                "(dependent type theory), and AI (LLMs as oracles) are mutually "
                "reinforcing in the semantic verification setting: each tradition "
                "contributes capabilities the other two lack, and their combination "
                "in the judgment geometry J = (c, φ, A, E, O, B, T, Π) is strictly "
                "more expressive than any proper subset of the three traditions."
            ),
            proof_sketch=(
                "Proof by example + structural argument.  (1) AG without DTT: "
                "the sheaf structure exists but propositions lack dependent-type "
                "precision.  (2) DTT without AG: type checking is local; no "
                "global coordination of evidence from multiple channels.  "
                "(3) AI without AG or DTT: oracle proposals exist but cannot be "
                "systematically verified.  (4) AG+DTT: complete for non-AI math "
                "but does not address the AI oracle trust-ceiling problem.  "
                "(5) AG+DTT+AI: all three capabilities present and coordinated."
            ),
            proof_strategy=ProofStrategy.CONSTRUCTION,
            assumptions=(
                "The three traditions are formalized as in D1.1–D1.3 and §2.1.",
            ),
            conclusions=(
                "No proper subset of {AG, DTT, AI} suffices for the full "
                "semantic verification framework.",
                "The judgment tuple J is the locus of the synthesis.",
            ),
            evidence_trust_level=TrustLevel.ORACLE_PROPOSED,
            formalized=False,
            references=("D1.1", "T1.2", "T1.4"),
            copilot_annotation=(
                "Copilot: T2.1 is the 'necessity' claim — each of AG, DTT, AI "
                "is necessary for the full framework.  The falsifiability conditions "
                "in THE_AG_DTT_AI_THESIS (s03) are the conditions under which T2.1 "
                "would fail."
            ),
        ),
        TheoremStatement(
            theorem_id="T2.2",
            kind=TheoremKind.THEOREM,
            chapter=2,
            section="§2.3",
            name="No-Silent-Promotion Enforcement Theorem",
            statement=(
                "In the trust algebra T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ), "
                "for any evidence configuration e ∈ E_adm and any trust level t, "
                "there is no algorithmic procedure that can increase t to t' > t "
                "without providing an explicit justification π ≠ ε (empty string).  "
                "In particular, the composition operator ⊕ never increases trust "
                "(it is the meet, hence ≤ both operands), and ↑_π requires π ≠ ε."
            ),
            proof_sketch=(
                "By inspection of the trust algebra operations: (1) ⊕ = meet, so "
                "⊕(t₁, t₂) ≤ min(t₁, t₂) in ⪯.  (2) ↑_π is defined iff π ≠ ε.  "
                "(3) ⊖ is monotone weakening.  (4) ↓_χ is challenge (weakening).  "
                "The only strengthening operation is ↑_π, and it requires π ≠ ε "
                "by the TrustAlgebra.promote() implementation."
            ),
            proof_strategy=ProofStrategy.TRUST_ALGEBRA,
            assumptions=(
                "The trust algebra is implemented as specified in T1.3.",
            ),
            conclusions=(
                "No trust promotion is possible without an explicit justification.",
                "The meet operator ⊕ is trust-conservative.",
                "Copilot/oracle evidence cannot self-promote.",
            ),
            evidence_trust_level=TrustLevel.SOLVER_DISCHARGED,
            formalized=True,
            references=("T1.3", "D1.2"),
            copilot_annotation=(
                "Copilot: T2.2 formalizes 'no silent promotion'.  The key clause "
                "is: π ≠ ε is a syntactic pre-condition for ↑_π.  This means Copilot "
                "cannot promote its own evidence without providing a non-empty "
                "justification string."
            ),
        ),
        TheoremStatement(
            theorem_id="T2.3",
            kind=TheoremKind.THEOREM,
            chapter=2,
            section="§2.2",
            name="Obstruction-Persistence Theorem",
            statement=(
                "Let J be a judgment with a non-empty obstruction component B.  "
                "Then: (1) there is no algorithm that silently removes obstructions "
                "from B without providing either a resolution certificate or a "
                "residualization proof; (2) any judgment J' that depends on J "
                "(i.e., J is in J'.Π's parent set) inherits all unresolved "
                "obstructions from J; (3) the set of obstruction classes in B "
                "forms an abelian group under the Čech coboundary operator."
            ),
            proof_sketch=(
                "(1): By design of the JuGeo framework — obstructions are frozen "
                "fields in the Judgment dataclass; the only operations that modify "
                "B are resolve_obstruction() and restrict_to() (residualization), "
                "both of which require explicit arguments.  "
                "(2): By the provenance propagation rules — Provenance.parent_coordinates "
                "includes J's coordinate; the inheritance rule is enforced by the "
                "JudgmentAlgebra.compose() method.  "
                "(3): Standard Čech cohomology: the obstruction classes are "
                "1-cocycles in Z¹(U, F); their group structure is the additive "
                "group of H¹(U, F)."
            ),
            proof_strategy=ProofStrategy.COHOMOLOGY,
            assumptions=(
                "Judgments are frozen (immutable) dataclass instances.",
                "The JudgmentAlgebra methods enforce the inheritance rule.",
            ),
            conclusions=(
                "Obstructions cannot be silently removed.",
                "Obstruction inheritance is enforced by provenance.",
                "Obstruction classes form an abelian group (H¹).",
            ),
            evidence_trust_level=TrustLevel.RUNTIME_WITNESSED,
            formalized=False,
            references=("T1.4", "D1.1"),
            copilot_annotation=(
                "Copilot: T2.3 formalizes 'obstructions persist'.  The implementation "
                "guarantees (frozen dataclass, explicit resolution/residualization "
                "methods) realize the mathematical claims in the theorem."
            ),
        ),
        TheoremStatement(
            theorem_id="L2.1",
            kind=TheoremKind.LEMMA,
            chapter=2,
            section="§2.3",
            name="Meet-Operator Conservatism",
            statement=(
                "For all t₁, t₂ ∈ E_adm, ⊕(t₁, t₂) ⪯ t₁ and ⊕(t₁, t₂) ⪯ t₂.  "
                "That is, the meet operator ⊕ never produces a result stronger "
                "than either operand."
            ),
            proof_sketch=(
                "By definition: ⊕(t₁, t₂) is the greatest lower bound of t₁ and t₂ "
                "in the partial order ⪯.  The greatest lower bound satisfies "
                "glb ⪯ t₁ and glb ⪯ t₂ by definition.  Hence ⊕(t₁, t₂) ⪯ t₁ "
                "and ⊕(t₁, t₂) ⪯ t₂."
            ),
            proof_strategy=ProofStrategy.DIRECT,
            assumptions=(
                "(E_adm, ⪯) is a partial order with meets (see T1.3).",
            ),
            conclusions=(
                "⊕(t₁, t₂) ⪯ t₁",
                "⊕(t₁, t₂) ⪯ t₂",
                "Composition never increases trust.",
            ),
            evidence_trust_level=TrustLevel.VERIFIED_PROOF,
            formalized=True,
            references=("T1.3", "D1.2"),
            copilot_annotation=(
                "Copilot: L2.1 is the technical core of evidence plurality.  "
                "It says: mixing evidence from different channels never gives you "
                "more trust than the weakest channel.  This is why meet = ⊕."
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Canonical instance
# ---------------------------------------------------------------------------

THEOREM_CATALOG: TheoremCatalog = TheoremCatalog()
