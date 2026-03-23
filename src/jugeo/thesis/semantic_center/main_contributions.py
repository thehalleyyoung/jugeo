"""
jugeo.thesis.semantic_center.main_contributions
=====================================================

Main contributions of the JuGeo thesis: formal catalog of what JuGeo
contributes to the field, how each contribution is realized, and how they
relate to each other.

This module corresponds to theory2.tex §2.2 (Main Contributions).

Contributions
-------------
1. ``JudgmentGeometryContribution``       — The judgment geometry itself: the
   eight-component tuple as a semantic coordinate system.
2. ``EvidencePluralityContribution``      — Evidence has kinds: different channels
   cannot be silently conflated.
3. ``ObstructionPersistenceContribution`` — Obstructions are persistent
   cohomological objects, not transient errors.
4. ``TrustAlgebraContribution``           — Trust is an ordered algebra, not a
   scalar confidence value.
5. ``ContributionCatalog``                — Assembles all contributions and provides
   cross-contribution analysis.

Design notes
------------
Each contribution class:
* Provides a formal statement of the contribution.
* Documents what the field lacked before this contribution.
* Explains how the contribution addresses the lack.
* Records which modules in the codebase realize the contribution.
* Provides a ``trust_level()`` method giving the evidence confidence for the
  contribution's validity.
* Provides a ``copilot_summary()`` for Copilot navigation.

References
----------
* theory2.tex §2.2 — Main Contributions
* theory2.tex §2.3 — Evidence and Trust Algebra
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Sequence

from jugeo.errors import (
    FailureClassification,
    FailureScope,
    JuGeoError,
    StructuredFailure,
    raise_with_scope,
)
from jugeo.judgments.judgment_terms import (
    Judgment,
    Proposition,
    PropositionKind,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.evidence.trust import TrustAlgebra

__all__ = [
    "JudgmentGeometryContribution",
    "EvidencePluralityContribution",
    "ObstructionPersistenceContribution",
    "TrustAlgebraContribution",
    "ContributionCatalog",
    "CONTRIBUTION_CATALOG",
]


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class _BaseContribution:
    """Abstract base for all contribution classes.

    Provides the common interface:
    * ``formal_statement()`` — the claim as stated in theory2.tex
    * ``prior_state()``      — what the field had before this contribution
    * ``what_we_add()``      — what JuGeo contributes
    * ``realization_modules()`` — code modules that realize it
    * ``trust_level()``      — evidence confidence for the contribution
    * ``copilot_summary()``  — Copilot navigation aid
    * ``to_dict()``          — JSON serialization
    """

    CONTRIBUTION_ID: str = ""
    CONTRIBUTION_TITLE: str = ""
    THEORY_SECTION: str = "§2.2"
    KIND: str = "theoretical"

    def formal_statement(self) -> str:
        """Return the formal statement of the contribution.

        Returns
        -------
        str
        """
        raise NotImplementedError

    def prior_state(self) -> str:
        """Return what the field had before this contribution.

        Returns
        -------
        str
        """
        raise NotImplementedError

    def what_we_add(self) -> str:
        """Return what JuGeo contributes.

        Returns
        -------
        str
        """
        raise NotImplementedError

    def realization_modules(self) -> list[str]:
        """Return Python module names within this package that realize the contribution.

        Returns
        -------
        list[str]
        """
        raise NotImplementedError

    def trust_level(self) -> TrustLevel:
        """Return the evidence trust level for this contribution's validity.

        Returns
        -------
        TrustLevel
        """
        return TrustLevel.ORACLE_PROPOSED

    def novelty_claim(self) -> str:
        """Return a one-sentence novelty claim.

        Returns
        -------
        str
        """
        raise NotImplementedError

    def related_contributions(self) -> list[str]:
        """Return contribution IDs that this contribution builds on or enables.

        Returns
        -------
        list[str]
        """
        return []

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary of this contribution.

        Returns
        -------
        str
        """
        modules = ", ".join(self.realization_modules())
        related = ", ".join(self.related_contributions()) or "none"
        return "\n".join([
            f"[{self.CONTRIBUTION_ID}] {self.CONTRIBUTION_TITLE}",
            f"Kind: {self.KIND} | Theory: {self.THEORY_SECTION}",
            f"Trust: {self.trust_level().name}",
            "",
            "Formal statement:",
            f"  {textwrap.fill(self.formal_statement(), 76)}",
            "",
            "Prior state (what the field lacked):",
            f"  {textwrap.fill(self.prior_state(), 76)}",
            "",
            "What JuGeo adds:",
            f"  {textwrap.fill(self.what_we_add(), 76)}",
            "",
            f"Novelty: {self.novelty_claim()}",
            f"Realized in: {modules}",
            f"Related: {related}",
        ])

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "contribution_id": self.CONTRIBUTION_ID,
            "title": self.CONTRIBUTION_TITLE,
            "kind": self.KIND,
            "theory_section": self.THEORY_SECTION,
            "formal_statement": self.formal_statement(),
            "prior_state": self.prior_state(),
            "what_we_add": self.what_we_add(),
            "trust_level": self.trust_level().name,
            "novelty_claim": self.novelty_claim(),
            "realization_modules": self.realization_modules(),
            "related_contributions": self.related_contributions(),
        }


# ---------------------------------------------------------------------------
# JudgmentGeometryContribution
# ---------------------------------------------------------------------------


class JudgmentGeometryContribution(_BaseContribution):
    """Contribution 1: Judgment Geometry — the eight-component tuple as a
    semantic coordinate system.

    JuGeo's first and most fundamental contribution is the *judgment geometry*
    itself: the formalization of semantic judgments as eight-component tuples
    J = (c, φ, A, E, O, B, T, Π) in a structured semantic product space.

    This is a contribution to the theory of programming languages, formal
    verification, and AI-assisted mathematics simultaneously: it provides a
    single unified coordinate system that subsumes and relates the partial
    views provided by each existing approach (type checkers, proof assistants,
    runtime monitors, and AI oracles).

    The key insight is that a semantic judgment is not just a truth value or
    a proof term — it is a *geometric object* with a coordinate (where it
    lives), a proposition (what it claims), a carrier (what it is about),
    evidence (why it holds), obligations (what remains), obstructions (what
    blocks it), trust (how confident we are), and provenance (where it came from).

    This contribution is the foundation for all others.
    """

    CONTRIBUTION_ID = "CONTRIB-01"
    CONTRIBUTION_TITLE = "Judgment Geometry: Eight-Component Semantic Coordinate System"
    THEORY_SECTION = "§2.2"
    KIND = "theoretical"

    def formal_statement(self) -> str:
        """Return the formal statement."""
        return (
            "We introduce the eight-component judgment tuple J = (c, φ, A, E, O, B, T, Π) "
            "as the fundamental semantic object for verification of AI-generated mathematics.  "
            "We prove that this tuple is a sufficient coordinate system for the "
            "semantic product space Σ: every semantic property relevant to "
            "AI-generated mathematical text can be expressed as a predicate on "
            "some subset of the eight components."
        )

    def prior_state(self) -> str:
        """Return the prior state."""
        return (
            "Prior to JuGeo, semantic verification approaches used fragmented "
            "representations: type checkers track types; proof assistants track proof "
            "terms; runtime monitors track execution traces; AI systems track "
            "confidence scores.  No single representation unified all of these into "
            "a geometric framework.  Evidence, obligations, and obstructions were "
            "implicit side effects, not first-class components."
        )

    def what_we_add(self) -> str:
        """Return what JuGeo adds."""
        return (
            "JuGeo adds the unified coordinate system J = (c, φ, A, E, O, B, T, Π).  "
            "Specifically: (1) coordinates c provide addressability in the semantic "
            "product space; (2) the evidence bundle E tracks evidence by kind and trust "
            "level; (3) residual obligations O record what remains unverified; "
            "(4) obstructions B are first-class cohomological objects rather than "
            "transient errors; (5) trust T is an ordered-algebra annotation rather "
            "than a scalar; (6) provenance Π records lineage for auditability.  "
            "Together, these make verification transparent, auditable, and composable."
        )

    def realization_modules(self) -> list[str]:
        """Return realization modules."""
        return [
            "jugeo.judgments.judgment_terms",
            "jugeo.thesis.semantic_center.judgment_geometry_as_the_semantic",
            "jugeo.thesis.semantic_center.models",
        ]

    def trust_level(self) -> TrustLevel:
        """Return trust level."""
        return TrustLevel.ORACLE_PROPOSED

    def novelty_claim(self) -> str:
        """Return the novelty claim."""
        return (
            "The eight-component judgment tuple is the first unified coordinate system "
            "for semantic verification that subsumes type checking, proof checking, "
            "runtime monitoring, and AI oracle evidence in a single geometric framework."
        )

    def tuple_component_roles(self) -> dict[str, str]:
        """Return a dictionary mapping each component to its role in the contribution.

        Returns
        -------
        dict[str, str]
        """
        return {
            "c": "Addressability — where in Σ this judgment lives",
            "φ": "Proposition — what the judgment claims (DTT formula)",
            "A": "Carrier — what kind of object the claim is about",
            "E": "Evidence — typed evidence bundle with channel identity",
            "O": "Obligations — residual unverified sub-claims",
            "B": "Obstructions — persistent blocking conditions (cohomological)",
            "T": "Trust — ordered-algebra annotation, not a scalar",
            "Π": "Provenance — lineage for auditability and replay",
        }

    def copilot_annotation(self) -> str:
        """Return a Copilot annotation on this contribution.

        Returns
        -------
        str
        """
        return (
            "Copilot: This is the foundational contribution.  When navigating "
            "the codebase, the class Judgment in jugeo.judgments.judgment_terms "
            "is the direct realization.  Every other module in the system ultimately "
            "produces, consumes, or transforms Judgment objects."
        )


# ---------------------------------------------------------------------------
# EvidencePluralityContribution
# ---------------------------------------------------------------------------


class EvidencePluralityContribution(_BaseContribution):
    """Contribution 2: Evidence Plurality — evidence has kinds.

    JuGeo's second major contribution is the *evidence plurality* principle:
    evidence items carry their kind (FORMAL_PROOF, SOLVER_PROOF,
    RUNTIME_WITNESS, ORACLE_PROPOSAL), and different kinds cannot be silently
    conflated.

    This is both a theoretical claim and a practical design principle.
    Theoretically, it says that the *type* of evidence matters for semantic
    verification: a solver certificate and a formal proof are fundamentally
    different epistemic objects, even if they both establish the same
    proposition.  Practically, it forces all verification pipelines to be
    explicit about what kind of evidence they are producing and consuming.

    The evidence plurality principle is enforced through:
    1. ``EvidenceItemKind`` enum distinguishing four evidence kinds.
    2. ``EvidenceItem.kind`` field in the judgment tuple's E component.
    3. ``TrustAlgebra`` meet rule: composing heterogeneous evidence yields
       the greatest lower bound, not a simple average.
    4. ``FailureScope`` and ``EvidenceFamily`` in error records, which preserve
       the channel identity of the failure's source.
    """

    CONTRIBUTION_ID = "CONTRIB-02"
    CONTRIBUTION_TITLE = "Evidence Plurality: Evidence Has Kinds"
    THEORY_SECTION = "§2.2"
    KIND = "theoretical"

    def formal_statement(self) -> str:
        """Return the formal statement."""
        return (
            "We introduce the evidence-plurality principle: every evidence item "
            "carries a kind κ ∈ {FORMAL_PROOF, SOLVER_PROOF, RUNTIME_WITNESS, "
            "ORACLE_PROPOSAL}, and the trust composition operator ⊕ in the trust "
            "algebra is defined as the meet (greatest lower bound) of the partial "
            "order on evidence strengths.  Conflation of evidence kinds is "
            "prohibited: a SOLVER_PROOF and an ORACLE_PROPOSAL cannot be "
            "composed to yield a FORMAL_PROOF."
        )

    def prior_state(self) -> str:
        """Return the prior state."""
        return (
            "Prior to JuGeo, AI verification pipelines typically aggregated evidence "
            "by averaging confidence scores or by taking the maximum of individual "
            "evidence values.  This conflated structurally different evidence kinds: "
            "a formal proof and an oracle proposal could be averaged to yield a "
            "medium-confidence score, even though the formal proof is a certificate "
            "and the oracle proposal is a conjecture.  The channel identity of "
            "evidence was lost in aggregation."
        )

    def what_we_add(self) -> str:
        """Return what JuGeo adds."""
        return (
            "JuGeo adds the evidence-kind type system: each EvidenceItem carries "
            "an EvidenceItemKind tag that identifies its channel, and the trust "
            "algebra's composition operator ⊕ uses the meet rule, which preserves "
            "the channel identity even after composition.  The meet of "
            "FORMAL_PROOF and ORACLE_PROPOSAL is ORACLE_PROPOSED — the weaker "
            "level — reflecting the fact that the combination is only as strong "
            "as its weakest component.  This prevents silent conflation and "
            "ensures that the verification pipeline is always explicit about what "
            "kind of evidence it is relying on."
        )

    def realization_modules(self) -> list[str]:
        """Return realization modules."""
        return [
            "jugeo.judgments.judgment_terms",
            "jugeo.evidence.trust",
            "jugeo.errors",
            "jugeo.thesis.semantic_center.jugeo_relative_to_theorem_provers",
        ]

    def trust_level(self) -> TrustLevel:
        """Return trust level."""
        return TrustLevel.RUNTIME_WITNESSED

    def novelty_claim(self) -> str:
        """Return the novelty claim."""
        return (
            "Evidence plurality is the first formal principle that prohibits "
            "conflation of different evidence kinds in a semantic verification "
            "framework for AI-generated mathematics."
        )

    def related_contributions(self) -> list[str]:
        """Return related contributions."""
        return ["CONTRIB-01", "CONTRIB-04"]

    def plurality_examples(self) -> list[str]:
        """Return concrete examples of evidence plurality in action.

        Returns
        -------
        list[str]
        """
        return [
            "A Z3 UNSAT cert (SOLVER_PROOF) + an LLM suggestion (ORACLE_PROPOSAL) "
            "compose to SOLVER_DISCHARGED ∧ ORACLE_PROPOSED = ORACLE_PROPOSED via meet",
            "A Lean proof term (FORMAL_PROOF) + a runtime assertion pass (RUNTIME_WITNESS) "
            "compose to VERIFIED_PROOF ∧ RUNTIME_WITNESSED = RUNTIME_WITNESSED via meet",
            "Two FORMAL_PROOF items compose to FORMAL_PROOF (homogeneous composition)",
            "An ORACLE_PROPOSAL attempting to claim SOLVER_DISCHARGED is blocked by "
            "the trust ceiling enforcement",
        ]

    def copilot_annotation(self) -> str:
        """Return a Copilot annotation on this contribution.

        Returns
        -------
        str
        """
        return (
            "Copilot: Evidence plurality is why you cannot average confidence "
            "scores in JuGeo.  When a Copilot suggestion arrives, it enters as "
            "ORACLE_PROPOSAL; it cannot claim to be a solver proof.  The meet rule "
            "in TrustAlgebra.compose() enforces this at every composition step."
        )


# ---------------------------------------------------------------------------
# ObstructionPersistenceContribution
# ---------------------------------------------------------------------------


class ObstructionPersistenceContribution(_BaseContribution):
    """Contribution 3: Obstruction Persistence — obstructions are cohomological.

    JuGeo's third major contribution is the *obstruction persistence* principle:
    when local sections fail to glue (when evidence from different channels
    disagrees at the same semantic coordinate), the obstruction is a persistent
    *cohomological object* — a Čech 1-cocycle in the first cohomology group of
    the sheaf — rather than a transient error to be silently discarded.

    This has profound practical consequences:

    1. **Tracking**: Obstructions are recorded in the B component of the
       judgment tuple and in ``ObstructionRecord`` objects attached to
       ``StructuredFailure`` payloads.  They cannot be lost.

    2. **Residualization**: An obstruction may be *residualized* by narrowing
       the scope of the claim: instead of claiming the proposition holds
       everywhere, we claim it holds outside the obstruction's support.
       This is the obstruction's ``residualization_frontier``.

    3. **Resolution**: An obstruction may be *resolved* by providing new
       evidence that re-establishes the gluing condition.  Resolution requires
       explicit evidence at the appropriate trust level.

    4. **Persistence**: An obstruction that cannot be residualized or resolved
       is a *persistent obstruction* — it is recorded permanently in the
       judgment's B component and propagates to any judgment that depends on
       the obstructed judgment.
    """

    CONTRIBUTION_ID = "CONTRIB-03"
    CONTRIBUTION_TITLE = "Obstruction Persistence: Obstructions as Cohomological Objects"
    THEORY_SECTION = "§2.2"
    KIND = "theoretical"

    def formal_statement(self) -> str:
        """Return the formal statement."""
        return (
            "We prove that semantic obstructions to local-to-global assembly in "
            "JuGeo's sheaf framework are elements of the first Čech cohomology "
            "group H¹(U, F) of the sheaf F over the open cover U.  Every "
            "obstruction is represented by a Čech 1-cocycle, and the space of "
            "obstruction classes has the structure of an abelian group.  "
            "Obstructions can be residualized (by restricting the claim's support) "
            "or resolved (by providing new gluing evidence); in either case, the "
            "operation is explicit and audited."
        )

    def prior_state(self) -> str:
        """Return the prior state."""
        return (
            "Prior to JuGeo, verification failures in AI-assisted mathematics were "
            "treated as transient errors: a failed proof attempt would raise an "
            "exception, which would be caught and either retried or discarded.  "
            "The failure's mathematical content (why the proof failed, what "
            "structural disagreement it revealed) was not preserved.  This meant "
            "that repeated failures at the same semantic coordinate would not "
            "accumulate into a persistent obstruction record — each failure was "
            "ephemeral."
        )

    def what_we_add(self) -> str:
        """Return what JuGeo adds."""
        return (
            "JuGeo adds the first-class obstruction model: every verification "
            "failure that arises from a local-section disagreement is promoted to "
            "an ObstructionRecord, which is stored in the judgment's B component "
            "and in the StructuredFailure payload.  The ObstructionRecord carries: "
            "the coordinate, the violated condition, the evidence that witnesses "
            "the disagreement, the residualization frontier, and downstream "
            "obligations.  Obstructions compose under the ⊕ operation (B is a "
            "module over the trust algebra); they can be filtered, merged, and "
            "serialized.  They persist until explicitly resolved."
        )

    def realization_modules(self) -> list[str]:
        """Return realization modules."""
        return [
            "jugeo.errors",
            "jugeo.judgments.judgment_terms",
            "jugeo.thesis.semantic_center.theorems",
            "jugeo.thesis.semantic_center.judgment_geometry_as_the_semantic",
        ]

    def trust_level(self) -> TrustLevel:
        """Return trust level."""
        return TrustLevel.RUNTIME_WITNESSED

    def novelty_claim(self) -> str:
        """Return the novelty claim."""
        return (
            "Obstruction persistence is the first framework that treats semantic "
            "verification failures as persistent cohomological objects rather than "
            "transient exceptions, enabling tracking, residualization, and "
            "systematic resolution."
        )

    def related_contributions(self) -> list[str]:
        """Return related contributions."""
        return ["CONTRIB-01", "CONTRIB-02"]

    def obstruction_lifecycle(self) -> list[str]:
        """Return the lifecycle stages of an obstruction.

        Returns
        -------
        list[str]
        """
        return [
            "DETECTED: Local sections disagree on an overlap U_ij",
            "RECORDED: An ObstructionRecord is created and placed in B",
            "ANALYZED: The obstruction class is computed in H¹(U, F)",
            "RESIDUALIZED (optional): The claim's support is narrowed to exclude "
            "the obstruction's support",
            "RESOLVED (optional): New evidence re-establishes the gluing condition",
            "PROPAGATED: The obstruction propagates to dependent judgments "
            "until resolved",
        ]

    def copilot_annotation(self) -> str:
        """Return a Copilot annotation.

        Returns
        -------
        str
        """
        return (
            "Copilot: When you see a StructuredFailure with an obstruction field, "
            "that is a first-class obstruction record.  Do not discard it.  "
            "Instead, either residualize the claim (narrow its scope) or provide "
            "new evidence to resolve the gluing failure.  The B component of the "
            "judgment tuple is the persistent store of such records."
        )


# ---------------------------------------------------------------------------
# TrustAlgebraContribution
# ---------------------------------------------------------------------------


class TrustAlgebraContribution(_BaseContribution):
    """Contribution 4: Trust Algebra — trust is an ordered algebra, not a scalar.

    JuGeo's fourth major contribution is the *trust algebra* itself:
    T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ), an ordered algebra on admissible
    evidence configurations that replaces scalar confidence values.

    The trust algebra has the following operators:

    * ⪯  (partial order): The primary ordering on trust levels.  Stronger
         levels are higher in the partial order.  Some levels are incomparable
         (e.g., ORACLE_PROPOSED and RUNTIME_WITNESSED in some orderings).
    * ⊕  (composition / meet): Conservative composition of trust from
         multiple evidence items.  Result is the greatest lower bound (meet)
         of the operands' levels.
    * ⊖  (attenuation): Trust weakening under restriction or channel crossing.
         Saturates at CONTRADICTED.
    * ↑_π (promotion): Trust strengthening with explicit justification.
         Requires a non-empty justification string; copilot cannot self-promote.
    * ↓_χ (demotion / challenge): Trust demotion when contradictory evidence
         arrives.  Explicit; cannot be silently triggered.

    This algebra is the operational realization of JuGeo's 'no silent promotion'
    principle and the evidential plurality principle.
    """

    CONTRIBUTION_ID = "CONTRIB-04"
    CONTRIBUTION_TITLE = "Trust Algebra T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ)"
    THEORY_SECTION = "§2.3"
    KIND = "theoretical"

    def __init__(self) -> None:
        """Initialize the trust algebra contribution."""
        super().__init__()
        self._algebra = TrustAlgebra()

    def formal_statement(self) -> str:
        """Return the formal statement."""
        return (
            "We define the trust algebra T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) where: "
            "E_adm is the set of admissible evidence configurations; ⪯ is a partial "
            "order on trust levels (NOT a total order); ⊕ is the meet (greatest "
            "lower bound) in the partial order; ⊖ is monotone attenuation "
            "(weakening); ↑_π is promotion (strengthening) requiring explicit "
            "justification π; ↓_χ is demotion (challenge) by contradictory "
            "evidence χ.  We prove that T is a lattice (with meets) and that ↑_π "
            "is injective (different justifications yield distinguishable states)."
        )

    def prior_state(self) -> str:
        """Return the prior state."""
        return (
            "Prior to JuGeo, AI verification systems typically represented trust "
            "as a scalar float in [0, 1], aggregated by weighted averaging.  "
            "This approach conflates the provenance of trust (whether it came from "
            "a formal proof or an oracle), loses the channel identity of evidence, "
            "and makes 'no silent promotion' impossible to enforce (since any "
            "averaging can silently increase the composite trust level).  "
            "The ordered-algebra structure was missing."
        )

    def what_we_add(self) -> str:
        """Return what JuGeo adds."""
        return (
            "JuGeo adds the full ordered trust algebra T = (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ).  "
            "The partial-order structure encodes the epistemic hierarchy of evidence "
            "sources without imposing a spurious total order.  The meet operator ⊕ "
            "is the conservative aggregation rule (weakest-wins).  The promotion "
            "operator ↑_π requires an explicit justification string, making all "
            "trust increases auditable.  The demotion operator ↓_χ allows "
            "challenges to be applied explicitly.  The algebra is implemented in "
            "TrustAlgebra (evidence/trust.py) and TrustAnnotation "
            "(judgments/judgment_terms.py) with an append-only audit log."
        )

    def realization_modules(self) -> list[str]:
        """Return realization modules."""
        return [
            "jugeo.evidence.trust",
            "jugeo.judgments.judgment_terms",
            "jugeo.thesis.semantic_center.jugeo_relative_to_theorem_provers",
            "jugeo.thesis.semantic_center.theorems",
        ]

    def trust_level(self) -> TrustLevel:
        """Return trust level."""
        return TrustLevel.SOLVER_DISCHARGED

    def novelty_claim(self) -> str:
        """Return the novelty claim."""
        return (
            "The trust algebra T=(E_adm,⪯,⊕,⊖,↑_π,↓_χ) is the first ordered-algebra "
            "formalization of trust for semantic verification that prohibits scalar "
            "aggregation, preserves evidence channel identity, and audits all "
            "trust operations."
        )

    def related_contributions(self) -> list[str]:
        """Return related contributions."""
        return ["CONTRIB-01", "CONTRIB-02", "CONTRIB-03"]

    def algebra_operations_summary(self) -> str:
        """Return a summary table of the algebra's operations.

        Returns
        -------
        str
        """
        rows = [
            ("⪯", "Partial order", "t₁ ⪯ t₂ iff t₁ is weaker evidence than t₂"),
            ("⊕", "Meet (composition)", "t₁ ⊕ t₂ = greatest lower bound of t₁, t₂"),
            ("⊖", "Attenuation", "⊖ⁿ(t) = t weakened by n steps, ≥ CONTRADICTED"),
            ("↑_π", "Promotion", "↑_π(t) = t strengthened by 1 step with justification π"),
            ("↓_χ", "Demotion/challenge", "↓_χ(t) = t weakened by contradicting evidence χ"),
        ]
        header = f"{'Op':<6} {'Name':<20} {'Semantics'}"
        sep = "-" * 78
        lines = [header, sep]
        for op, name, sem in rows:
            lines.append(f"{op:<6} {name:<20} {sem}")
        return "\n".join(lines)

    def no_silent_promotion_enforcement(self) -> str:
        """Return an explanation of how the algebra enforces no-silent-promotion.

        Returns
        -------
        str
        """
        return (
            "No-silent-promotion enforcement in TrustAlgebra:\n"
            "\n"
            "  1. TrustAlgebra.promote(t, justification) raises JuGeoError if\n"
            "     justification is empty — promotion without reason is blocked.\n"
            "\n"
            "  2. TrustAlgebra.compose(a, b) uses the meet rule — composition\n"
            "     never increases trust; it can only maintain or decrease.\n"
            "\n"
            "  3. TrustAnnotation.compose() records the composition in the\n"
            "     audit trail (reasons tuple) — all changes are traceable.\n"
            "\n"
            "  4. EvidenceItemKind.ORACLE_PROPOSAL has a hard ceiling at\n"
            "     ORACLE_PROPOSED — no oracle can claim SOLVER_DISCHARGED.\n"
            "\n"
            "  5. The TrustAuditLog records every promotion, composition, and\n"
            "     demotion with timestamps, justifications, and evidence keys."
        )

    def copilot_annotation(self) -> str:
        """Return a Copilot annotation.

        Returns
        -------
        str
        """
        return (
            "Copilot: The trust algebra is why JuGeo uses TrustAnnotation instead "
            "of a float.  When you see trust.level in a judgment, that is an element "
            "of the partial order E_adm.  All trust operations go through TrustAlgebra "
            "methods — never set trust.level directly.  The audit trail in TrustAuditLog "
            "is the receipts for every trust change."
        )


# ---------------------------------------------------------------------------
# ContributionCatalog
# ---------------------------------------------------------------------------


class ContributionCatalog:
    """Catalog of all JuGeo thesis contributions with cross-contribution analysis.

    ``ContributionCatalog`` assembles all four main contributions into a single
    object that provides:
    * A topologically-sorted ordering of contributions (by dependency).
    * A cross-contribution dependency graph.
    * A combined trust level for the thesis as a whole.
    * A full Copilot-navigable summary of all contributions.

    This class corresponds to theory2.tex §2.2 (Main Contributions) in its
    entirety.

    Parameters
    ----------
    theory_section:
        Reference to theory2.tex section.
    """

    def __init__(self, theory_section: str = "§2.2") -> None:
        """Initialize the contribution catalog.

        Parameters
        ----------
        theory_section:
            Theory reference.
        """
        self.theory_section = theory_section
        self.judgment_geometry = JudgmentGeometryContribution()
        self.evidence_plurality = EvidencePluralityContribution()
        self.obstruction_persistence = ObstructionPersistenceContribution()
        self.trust_algebra = TrustAlgebraContribution()
        self._algebra = TrustAlgebra()

    def all_contributions(self) -> list[_BaseContribution]:
        """Return all contributions in dependency order.

        Returns
        -------
        list[_BaseContribution]
            Ordered with foundational contributions first.
        """
        return [
            self.judgment_geometry,
            self.evidence_plurality,
            self.obstruction_persistence,
            self.trust_algebra,
        ]

    def combined_trust_level(self) -> TrustLevel:
        """Return the combined trust level across all contributions.

        The combined trust is the meet (⊕) of all individual contribution
        trust levels — the overall thesis is only as strong as its weakest
        contribution.

        Returns
        -------
        TrustLevel
        """
        levels = [c.trust_level() for c in self.all_contributions()]
        result = levels[0]
        for level in levels[1:]:
            result = self._algebra.compose(result, level)
        return result

    def contribution_by_id(self, contribution_id: str) -> _BaseContribution | None:
        """Return the contribution with the given ID.

        Parameters
        ----------
        contribution_id:
            Contribution identifier (e.g. ``"CONTRIB-01"``).

        Returns
        -------
        _BaseContribution | None
        """
        for c in self.all_contributions():
            if c.CONTRIBUTION_ID == contribution_id:
                return c
        return None

    def dependency_graph(self) -> dict[str, list[str]]:
        """Return the dependency graph as an adjacency dict.

        Returns
        -------
        dict[str, list[str]]
            Maps contribution ID to list of contribution IDs it depends on.
        """
        return {
            c.CONTRIBUTION_ID: c.related_contributions()
            for c in self.all_contributions()
        }

    def topological_order(self) -> list[str]:
        """Return contribution IDs in topological order (foundations first).

        Returns
        -------
        list[str]
        """
        graph = self.dependency_graph()
        visited: set[str] = set()
        order: list[str] = []

        def visit(node: str) -> None:
            if node in visited:
                return
            visited.add(node)
            for dep in graph.get(node, []):
                visit(dep)
            order.append(node)

        for node in graph:
            visit(node)
        return order

    def realization_coverage(self) -> dict[str, list[str]]:
        """Return a dict mapping each module to the contributions it realizes.

        Returns
        -------
        dict[str, list[str]]
            Maps module name to list of contribution IDs.
        """
        coverage: dict[str, list[str]] = {}
        for c in self.all_contributions():
            for mod in c.realization_modules():
                coverage.setdefault(mod, []).append(c.CONTRIBUTION_ID)
        return coverage

    def thesis_proposition(self) -> Proposition:
        """Return the overall thesis as a Proposition object.

        Returns
        -------
        Proposition
        """
        return Proposition(
            kind=PropositionKind.SEMANTIC,
            formula=(
                "JuGeo contributes (CONTRIB-01) judgment geometry, "
                "(CONTRIB-02) evidence plurality, "
                "(CONTRIB-03) obstruction persistence, and "
                "(CONTRIB-04) the trust algebra, forming a complete semantic "
                "verification framework for AI-generated mathematics."
            ),
            free_variables=(),
            metadata={"theory_section": self.theory_section},
        )

    def validate_all(self) -> list[StructuredFailure]:
        """Validate all contributions and return any failures.

        Returns
        -------
        list[StructuredFailure]
            List of validation failures (empty if all pass).
        """
        failures: list[StructuredFailure] = []
        seen_ids: set[str] = set()
        for c in self.all_contributions():
            if c.CONTRIBUTION_ID in seen_ids:
                failures.append(StructuredFailure(
                    message=f"Duplicate contribution ID: {c.CONTRIBUTION_ID!r}",
                    scope=FailureScope.CHAPTER,
                    classification=FailureClassification.INVALID_VALUE,
                ))
            seen_ids.add(c.CONTRIBUTION_ID)
            if not c.formal_statement():
                failures.append(StructuredFailure(
                    message=f"Contribution {c.CONTRIBUTION_ID!r} has empty formal_statement",
                    scope=FailureScope.CHAPTER,
                    classification=FailureClassification.INVALID_VALUE,
                ))
        return failures

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary of the entire catalog.

        Returns
        -------
        str
        """
        combined = self.combined_trust_level()
        topo = self.topological_order()
        dep_graph = self.dependency_graph()
        dep_lines = "\n".join(
            f"  {cid} → [{', '.join(deps) or 'none'}]"
            for cid, deps in dep_graph.items()
        )
        contrib_summaries = "\n\n".join(
            c.copilot_summary() for c in self.all_contributions()
        )
        coverage = self.realization_coverage()
        cov_lines = "\n".join(
            f"  {mod}: {', '.join(cids)}"
            for mod, cids in sorted(coverage.items())
        )
        return "\n".join([
            f"ContributionCatalog ({self.theory_section})",
            f"Combined trust level: {combined.name}",
            f"Topological order: {' → '.join(topo)}",
            "",
            "Dependency graph:",
            dep_lines,
            "",
            "Realization coverage (module → contributions):",
            cov_lines,
            "",
            "Contributions:",
            contrib_summaries,
        ])

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theory_section": self.theory_section,
            "combined_trust_level": self.combined_trust_level().name,
            "topological_order": self.topological_order(),
            "contributions": [c.to_dict() for c in self.all_contributions()],
        }


# ---------------------------------------------------------------------------
# Canonical instance
# ---------------------------------------------------------------------------

CONTRIBUTION_CATALOG: ContributionCatalog = ContributionCatalog()
