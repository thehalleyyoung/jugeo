"""
jugeo.thesis.semantic_center.problem_classes_addressed
============================================================

Problem classes addressed by JuGeo: formal catalog of the categories of
semantic problems for which JuGeo's framework provides a systematic treatment.

This module corresponds to theory2.tex §2.3 (Problem Classes Addressed).

Problem classes
---------------
1. ``SemanticVerificationProblem``   — Verifying semantic properties of
   AI-generated artifacts (theorems, proofs, code).
2. ``LongHorizonGenerationProblem``  — Multi-step generation with semantic
   coherence over long horizons.
3. ``MixedEvidenceProblem``          — Combining heterogeneous evidence from
   solvers, runtime witnesses, and AI oracles.
4. ``MathematicalIdeationProblem``   — Computer-assisted discovery of novel
   mathematical conjectures.
5. ``ProblemClassCatalog``           — Assembles all problem classes and
   provides cross-class analysis.

Design
------
Each problem-class class provides:
* A formal definition of the problem class.
* A description of why the class is hard.
* How JuGeo's framework addresses it.
* Example instances.
* Open questions within the class.
* A ``copilot_summary()`` for navigation.

References
----------
* theory2.tex §2.3 — Problem Classes Addressed
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
    "SemanticVerificationProblem",
    "LongHorizonGenerationProblem",
    "MixedEvidenceProblem",
    "MathematicalIdeationProblem",
    "ProblemClassCatalog",
    "PROBLEM_CLASS_CATALOG",
]


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class _BaseProblemClass:
    """Abstract base for all problem class descriptions.

    Subclasses must implement:
    * ``problem_id()``         — canonical identifier
    * ``problem_name()``       — short name
    * ``formal_definition()``  — formal or semi-formal definition
    * ``why_hard()``           — what makes it difficult
    * ``jugeo_approach()``     — how JuGeo addresses it
    * ``example_instances()``  — concrete examples
    * ``open_questions()``     — still-open questions
    * ``trust_required()``     — minimum trust needed for a solution
    """

    PROBLEM_ID: str = ""
    PROBLEM_NAME: str = ""
    THEORY_SECTION: str = "§2.3"

    def formal_definition(self) -> str:
        """Return the formal definition of the problem class."""
        raise NotImplementedError

    def why_hard(self) -> str:
        """Return what makes this problem class hard."""
        raise NotImplementedError

    def jugeo_approach(self) -> str:
        """Return how JuGeo addresses this problem class."""
        raise NotImplementedError

    def example_instances(self) -> list[str]:
        """Return concrete problem instances."""
        raise NotImplementedError

    def open_questions(self) -> list[str]:
        """Return open questions within this problem class."""
        return []

    def trust_required(self) -> TrustLevel:
        """Return the minimum trust level required for a solution."""
        return TrustLevel.SOLVER_DISCHARGED

    def addressed_by_contributions(self) -> list[str]:
        """Return contribution IDs that address this problem class."""
        return []

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary.

        Returns
        -------
        str
        """
        examples = "\n".join(f"  · {e}" for e in self.example_instances())
        open_qs = "\n".join(f"  ? {q}" for q in self.open_questions())
        contribs = ", ".join(self.addressed_by_contributions()) or "none"
        return "\n".join([
            f"[{self.PROBLEM_ID}] {self.PROBLEM_NAME}",
            f"Theory: {self.THEORY_SECTION} | Trust required: {self.trust_required().name}",
            f"Addressed by: {contribs}",
            "",
            "Definition:",
            f"  {textwrap.fill(self.formal_definition(), 76)}",
            "",
            "Why hard:",
            f"  {textwrap.fill(self.why_hard(), 76)}",
            "",
            "JuGeo approach:",
            f"  {textwrap.fill(self.jugeo_approach(), 76)}",
            "",
            "Example instances:",
            examples,
        ] + (["", "Open questions:", open_qs] if open_qs else []))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "problem_id": self.PROBLEM_ID,
            "name": self.PROBLEM_NAME,
            "theory_section": self.THEORY_SECTION,
            "formal_definition": self.formal_definition(),
            "why_hard": self.why_hard(),
            "jugeo_approach": self.jugeo_approach(),
            "examples": self.example_instances(),
            "open_questions": self.open_questions(),
            "trust_required": self.trust_required().name,
            "addressed_by": self.addressed_by_contributions(),
        }


# ---------------------------------------------------------------------------
# SemanticVerificationProblem
# ---------------------------------------------------------------------------


class SemanticVerificationProblem(_BaseProblemClass):
    """Problem class 1: Semantic Verification of AI-Generated Artifacts.

    Given an artifact A (a theorem statement, a proof sketch, a code fragment,
    or a type annotation) generated by an AI system, and a semantic specification
    S, determine whether A satisfies S.

    This is the foundational problem that JuGeo is designed to solve.  It
    encompasses:
    * Type-checking AI-generated Lean/Coq/Agda code.
    * Verifying that an LLM-generated proof sketch can be elaborated into a
      complete formal proof.
    * Checking that an AI-generated conjecture is consistent with known results.
    * Validating that an AI-generated program meets its behavioral specification.

    The problem is hard because:
    1. AI-generated text can be syntactically correct but semantically wrong
       ('hallucinated' mathematical claims).
    2. Semantic specifications for mathematical text are often implicit or
       context-dependent.
    3. Evidence for semantic correctness comes from heterogeneous sources
       (solver, runtime, oracle) with different trust levels.
    4. Failures are often partial: the claim holds in some parts of the
       semantic space but not others.
    """

    PROBLEM_ID = "PC-01"
    PROBLEM_NAME = "Semantic Verification of AI-Generated Artifacts"
    THEORY_SECTION = "§2.3"

    def formal_definition(self) -> str:
        """Return the formal definition."""
        return (
            "Given: (1) an AI-generated artifact A ∈ ArtifactSpace, "
            "(2) a semantic specification S : ArtifactSpace → Prop, and "
            "(3) an evidence budget B (bounds on solver time, oracle calls, etc.), "
            "find: a judgment J = (c, φ, A, E, O, B_obs, T, Π) with "
            "φ ⊢ S(A), E non-empty, T.level ≥ SOLVER_DISCHARGED, and "
            "B_obs = ∅ (no unresolved obstructions), or else return a "
            "minimal obstruction record explaining why no such judgment exists."
        )

    def why_hard(self) -> str:
        """Return what makes this problem hard."""
        return (
            "Semantic verification of AI output is hard because: (1) LLMs "
            "generate plausible-but-incorrect claims ('hallucinations') that pass "
            "syntactic checks; (2) the semantic specification S may not be "
            "expressible in a single first-order formula (it may require higher-order "
            "logic or dependent types); (3) evidence comes from sources with "
            "heterogeneous trust levels and must be combined without silent conflation; "
            "(4) partial failures (the claim holds at some coordinates but not others) "
            "must be represented as persistent obstructions, not as binary failures."
        )

    def jugeo_approach(self) -> str:
        """Return the JuGeo approach."""
        return (
            "JuGeo addresses this via the judgment geometry: the artifact A is "
            "placed at a coordinate c in Σ, its semantic specification S becomes "
            "the proposition φ, evidence is collected in E via the evidence "
            "pipeline (solver, runtime, oracle), residual obligations are tracked "
            "in O, and any partial failures are recorded as obstructions in B.  "
            "The trust algebra T ensures that evidence from different sources is "
            "combined conservatively (meet rule).  A successful verification yields "
            "a judgment with T.level ≥ SOLVER_DISCHARGED and B = ∅."
        )

    def example_instances(self) -> list[str]:
        """Return example instances."""
        return [
            "Verify that an LLM-generated Lean 4 proof compiles and type-checks",
            "Check that an AI-generated conjecture is consistent with a known "
            "counterexample database",
            "Verify that an LLM-generated Python program satisfies its "
            "property-based test suite",
            "Check that an AI-generated mathematical definition is non-vacuous "
            "(has at least one instance)",
            "Verify that an LLM-generated proof sketch can be elaborated by "
            "an ATP (automated theorem prover)",
        ]

    def open_questions(self) -> list[str]:
        """Return open questions."""
        return [
            "What is the minimal trust level required to certify semantic "
            "correctness for a given artifact class?",
            "Can obstruction classes be computed efficiently for large AI-generated "
            "proof corpora?",
            "Is there a completion algorithm that transforms a partial judgment "
            "(with obstructions) into a complete one by narrowing the claim?",
        ]

    def trust_required(self) -> TrustLevel:
        """Return required trust level."""
        return TrustLevel.SOLVER_DISCHARGED

    def addressed_by_contributions(self) -> list[str]:
        """Return addressing contributions."""
        return ["CONTRIB-01", "CONTRIB-02", "CONTRIB-03", "CONTRIB-04"]


# ---------------------------------------------------------------------------
# LongHorizonGenerationProblem
# ---------------------------------------------------------------------------


class LongHorizonGenerationProblem(_BaseProblemClass):
    """Problem class 2: Long-Horizon Generation with Semantic Coherence.

    Multi-step AI generation (e.g., generating a long proof, a research paper,
    or a complex program) faces the problem of maintaining semantic coherence
    over many generation steps.  Each step's output becomes context for the
    next, and semantic errors compound.

    JuGeo's framework addresses this by providing the semantic product space
    as a persistent, addressable store of judgments: each generation step
    produces new judgments at new coordinates, and the sheaf gluing conditions
    ensure that judgments from different steps are mutually compatible.

    The trust algebra allows trust levels to be tracked across generation steps:
    if an early step's judgment has low trust (ORACLE_PROPOSED), all downstream
    judgments that depend on it inherit this trust attenuation via the ⊖
    operator.
    """

    PROBLEM_ID = "PC-02"
    PROBLEM_NAME = "Long-Horizon Generation with Semantic Coherence"
    THEORY_SECTION = "§2.3"

    def formal_definition(self) -> str:
        """Return the formal definition."""
        return (
            "Given: a sequence of generation steps G = (g₁, g₂, …, gₙ) where "
            "each step gᵢ takes a context Cᵢ and produces an artifact Aᵢ, and "
            "a global coherence specification S_global : ∏ᵢ ArtifactSpace → Prop, "
            "find: a collection of judgments {Jᵢ} with compatible trust levels "
            "such that the sheaf gluing of {Jᵢ} is a global section satisfying "
            "S_global, and the accumulated trust attenuation ⊖ⁿ(T₁.level) over "
            "n steps remains above the minimum viable trust floor."
        )

    def why_hard(self) -> str:
        """Return what makes this problem hard."""
        return (
            "Long-horizon generation is hard because: (1) errors at early steps "
            "propagate and compound at later steps; (2) the global coherence "
            "specification may not be decomposable into step-wise specifications; "
            "(3) the generation context grows monotonically, making re-verification "
            "expensive; (4) trust attenuation over many steps can reduce the "
            "aggregate trust level below the viable floor, even if each individual "
            "step is correct; (5) obstructions at one coordinate may not manifest "
            "until many steps later."
        )

    def jugeo_approach(self) -> str:
        """Return the JuGeo approach."""
        return (
            "JuGeo addresses long-horizon generation by using the semantic product "
            "space Σ as a persistent coordination substrate.  Each generation step "
            "places a judgment at a coordinate; the trust algebra tracks trust "
            "attenuation via ⊖; the sheaf gluing conditions detect incompatibilities "
            "between steps before they compound; and obstruction records in B "
            "mark coordinates where coherence has failed, enabling targeted "
            "re-generation rather than full restart.  The provenance component Π "
            "encodes the step-wise dependency chain."
        )

    def example_instances(self) -> list[str]:
        """Return example instances."""
        return [
            "Multi-chapter AI-generated mathematical research paper with cross-"
            "reference coherence",
            "Long AI-generated Lean 4 proof with 50+ intermediate lemmas",
            "AI-generated program with mutually recursive functions where type "
            "annotations must be consistent across all definitions",
            "Iterative conjecture-refinement loop where each AI proposal builds "
            "on the previous",
            "AI-generated mathematical survey where all cited results must be "
            "consistent with each other",
        ]

    def open_questions(self) -> list[str]:
        """Return open questions."""
        return [
            "What is the maximum viable trust attenuation depth (how many steps "
            "before trust falls below the viable floor)?",
            "Can obstructions be predicted before they manifest (prospective "
            "obstruction detection)?",
            "Is there a notion of 'trust renewal' that resets attenuation at "
            "verified checkpoints?",
        ]

    def trust_required(self) -> TrustLevel:
        """Return required trust level."""
        return TrustLevel.RUNTIME_WITNESSED

    def addressed_by_contributions(self) -> list[str]:
        """Return addressing contributions."""
        return ["CONTRIB-01", "CONTRIB-03", "CONTRIB-04"]


# ---------------------------------------------------------------------------
# MixedEvidenceProblem
# ---------------------------------------------------------------------------


class MixedEvidenceProblem(_BaseProblemClass):
    """Problem class 3: Mixed-Evidence Reasoning.

    Given evidence from multiple heterogeneous sources (formal proofs, solver
    certificates, runtime witnesses, and AI oracle proposals), combine it
    into a single judgment with a well-defined trust level that reflects
    the relative strengths of the sources without silent conflation.

    This is the direct operational realization of the evidence-plurality
    principle (Contribution 2).  It arises whenever:
    * A partial formal proof is supplemented by solver automation.
    * An AI-generated proof sketch is partially verified by a runtime test.
    * A conjecture is supported by a combination of formal proofs (for special
      cases) and oracle proposals (for the general case).

    JuGeo's trust algebra handles this via the meet operator ⊕ and the
    channel-preserving composition rules.
    """

    PROBLEM_ID = "PC-03"
    PROBLEM_NAME = "Mixed-Evidence Reasoning"
    THEORY_SECTION = "§2.3"

    def formal_definition(self) -> str:
        """Return the formal definition."""
        return (
            "Given: a collection of evidence items E = {(κᵢ, τᵢ, fᵢ)} where "
            "κᵢ ∈ EvidenceItemKind, τᵢ ∈ TrustLevel, and fᵢ is the evidence "
            "fact, find: a combined trust annotation T_combined = ⊕ᵢ τᵢ (meet "
            "over all evidence trust levels) that (1) preserves the channel "
            "identity of each evidence kind, (2) does not silently promote any "
            "individual evidence item, and (3) satisfies the admissibility "
            "predicate E_adm(T_combined)."
        )

    def why_hard(self) -> str:
        """Return what makes this problem hard."""
        return (
            "Mixed-evidence reasoning is hard because: (1) different evidence "
            "sources have different epistemic standing (formal proofs vs. oracle "
            "proposals) that must not be conflated; (2) naïve aggregation (e.g., "
            "averaging) can silently promote weaker evidence to stronger levels; "
            "(3) the partial order on trust levels means some pairs are "
            "incomparable, and there is no canonical way to compose them; "
            "(4) channel identity must be preserved for auditability; "
            "(5) the admissibility predicate E_adm may rule out some combinations "
            "as internally inconsistent (e.g., CONTRADICTED alongside VERIFIED_PROOF)."
        )

    def jugeo_approach(self) -> str:
        """Return the JuGeo approach."""
        return (
            "JuGeo addresses mixed-evidence reasoning via the trust algebra's "
            "meet operator ⊕: given evidence items from different channels, "
            "TrustAlgebra.compose() returns the greatest lower bound of their "
            "trust levels in the partial order.  This is conservative (the "
            "result is never stronger than the weakest input) and channel-preserving "
            "(the EvidenceItem.kind field is preserved in the EvidenceBundle).  "
            "The admissibility predicate E_adm rules out inconsistent configurations "
            "at construction time.  The result is a TrustAnnotation with an explicit "
            "evidence_basis tuple recording which evidence items contributed."
        )

    def example_instances(self) -> list[str]:
        """Return example instances."""
        return [
            "Combine a Lean 4 proof of P(0) (FORMAL_PROOF) with a Z3 certificate "
            "for P(n)→P(n+1) (SOLVER_PROOF) to get P(n) for all n",
            "Combine an LLM conjecture (ORACLE_PROPOSAL) with a runtime test suite "
            "pass (RUNTIME_WITNESS) to get partial verification",
            "Combine formal proofs for three cases with an oracle proposal for the "
            "remaining case",
            "Combine solver certificates from Z3 and CVC5 on overlapping sub-goals",
            "Combine a Copilot annotation (ORACLE_PROPOSAL) with a human-attested "
            "claim (RUNTIME_WITNESS) for a non-formalizable mathematical heuristic",
        ]

    def open_questions(self) -> list[str]:
        """Return open questions."""
        return [
            "When is a combination of ORACLE_PROPOSAL and RUNTIME_WITNESS "
            "sufficient to justify SOLVER_DISCHARGED?",
            "Can the admissibility predicate be extended to handle probabilistic "
            "evidence (e.g., p-values from statistical testing)?",
            "Is there a canonical way to handle incomparable evidence types "
            "without resorting to the meet (which may be too conservative)?",
        ]

    def trust_required(self) -> TrustLevel:
        """Return required trust level."""
        return TrustLevel.SOLVER_DISCHARGED

    def addressed_by_contributions(self) -> list[str]:
        """Return addressing contributions."""
        return ["CONTRIB-02", "CONTRIB-04"]

    def meet_computation_example(self) -> str:
        """Return a worked example of meet computation for mixed evidence.

        Returns
        -------
        str
        """
        algebra = TrustAlgebra()
        formal = TrustLevel.VERIFIED_PROOF
        solver = TrustLevel.SOLVER_DISCHARGED
        oracle = TrustLevel.ORACLE_PROPOSED

        composed_formal_solver = algebra.compose(formal, solver)
        composed_result_oracle = algebra.compose(composed_formal_solver, oracle)

        return (
            "Example: FORMAL_PROOF ⊕ SOLVER_PROOF ⊕ ORACLE_PROPOSAL\n"
            f"  VERIFIED_PROOF ⊕ SOLVER_DISCHARGED = "
            f"{composed_formal_solver.name}\n"
            f"  {composed_formal_solver.name} ⊕ ORACLE_PROPOSED = "
            f"{composed_result_oracle.name}\n"
            f"  Result: {composed_result_oracle.name}\n"
            "  (The oracle proposal pulls the combined level down to "
            "ORACLE_PROPOSED — meet rule.)"
        )


# ---------------------------------------------------------------------------
# MathematicalIdeationProblem
# ---------------------------------------------------------------------------


class MathematicalIdeationProblem(_BaseProblemClass):
    """Problem class 4: Computer-Assisted Mathematical Ideation.

    Mathematical ideation is the process of generating novel mathematical
    conjectures, definitions, and proof strategies.  With AI systems, this
    process can be accelerated, but the output must be systematically
    evaluated for novelty, consistency, and verifiability.

    JuGeo addresses the ideation problem by providing:
    1. A coordinate system for addressing conjectures in the semantic space.
    2. A trust level system that tracks the epistemic status of each conjecture
       (from ORACLE_PROPOSED for fresh AI proposals to VERIFIED_PROOF for
       machine-verified theorems).
    3. A framework for tracking the *ideation trajectory*: the sequence of
       refinements, counterexamples, and verifications that transform a raw
       conjecture into a theorem.
    4. An obstruction model for conjectures that cannot be proved in a given
       formal system (independence results, undecidable propositions).

    Copilot plays a significant role here: as a mathematical oracle, it proposes
    conjectures and proof strategies that are then routed through JuGeo's
    verification pipeline.
    """

    PROBLEM_ID = "PC-04"
    PROBLEM_NAME = "Computer-Assisted Mathematical Ideation"
    THEORY_SECTION = "§2.3"

    def formal_definition(self) -> str:
        """Return the formal definition."""
        return (
            "Given: a mathematical context C (a set of known theorems, definitions, "
            "and open problems) and an AI ideation function f : C → Conjectures, "
            "find: for each conjecture Q ∈ f(C), either (1) a judgment J with "
            "φ = Q and T.level ≥ SOLVER_DISCHARGED (Q is verified), or "
            "(2) a counterexample E_counter (Q is refuted and an obstruction "
            "is recorded), or (3) an independence certificate (Q is independent "
            "of C's axioms, the obstruction is in the meta-logical layer)."
        )

    def why_hard(self) -> str:
        """Return what makes this problem hard."""
        return (
            "Mathematical ideation is hard because: (1) AI-generated conjectures "
            "are often false or trivially true, requiring efficient filtering; "
            "(2) novelty is hard to define formally (what counts as genuinely new?); "
            "(3) conjectures may be true but currently unprovable within the "
            "available formal system; (4) the ideation space is vast and must be "
            "pruned by semantic criteria; (5) the interaction between AI creativity "
            "(which generates the conjectures) and formal verification (which checks "
            "them) requires a shared semantic framework — which is what JuGeo provides."
        )

    def jugeo_approach(self) -> str:
        """Return the JuGeo approach."""
        return (
            "JuGeo addresses ideation by treating each conjecture as an "
            "ORACLE_PROPOSED judgment at a fresh coordinate.  The evidence "
            "pipeline then attempts to promote the trust level: solver calls "
            "attempt SOLVER_DISCHARGED; formal-proof attempts aim for VERIFIED_PROOF.  "
            "Counterexamples become obstruction records in B.  Independence results "
            "are special obstructions at the meta-logical layer.  The ideation "
            "trajectory (the sequence of trust-level changes from ORACLE_PROPOSED "
            "to VERIFIED_PROOF or OBSTRUCTED) is recorded in the provenance "
            "component Π and the trust audit log.  Copilot proposes; JuGeo verifies."
        )

    def example_instances(self) -> list[str]:
        """Return example instances."""
        return [
            "Copilot proposes: 'Every prime p ≡ 1 (mod 4) is a sum of two squares' "
            "— verify via Lean 4 formalization",
            "AI generates 50 conjectures about graph-theoretic properties; "
            "JuGeo filters to those satisfiable by Z3",
            "Copilot suggests a new definition of 'semantic coherence' for "
            "AI-generated proofs; JuGeo checks it is non-vacuous",
            "AI proposes a generalization of the Riemann Hypothesis; JuGeo "
            "records it as ORACLE_PROPOSED with an obstruction noting "
            "the original is still open",
            "AI suggests a counterexample to a conjectured type-system property; "
            "JuGeo verifies the counterexample is well-typed",
        ]

    def open_questions(self) -> list[str]:
        """Return open questions."""
        return [
            "Can JuGeo's framework formalize 'mathematical novelty' in terms "
            "of distance in the semantic product space?",
            "How should independence results (undecidable conjectures) be "
            "represented as obstructions?",
            "Is there a notion of 'ideation efficiency' that measures the "
            "expected number of oracle calls needed to reach SOLVER_DISCHARGED?",
            "Can Copilot's proposals be pre-filtered by a lightweight semantic "
            "consistency check before entering the full verification pipeline?",
        ]

    def trust_required(self) -> TrustLevel:
        """Return required trust level."""
        return TrustLevel.ORACLE_PROPOSED

    def addressed_by_contributions(self) -> list[str]:
        """Return addressing contributions."""
        return ["CONTRIB-01", "CONTRIB-02", "CONTRIB-03", "CONTRIB-04"]

    def ideation_trajectory(self) -> list[str]:
        """Return the typical ideation trajectory as trust-level stages.

        Returns
        -------
        list[str]
        """
        return [
            "Stage 1: AI/Copilot proposes conjecture → ORACLE_PROPOSED judgment",
            "Stage 2: Quick consistency check (Z3) → SOLVER_DISCHARGED or OBSTRUCTED",
            "Stage 3: Proof sketch generation (Copilot) → still ORACLE_PROPOSED",
            "Stage 4: Proof elaboration (Lean 4/Coq) → VERIFIED_PROOF or new obstructions",
            "Stage 5: Publication / archiving → provenance Π records full trajectory",
        ]

    def copilot_role_in_ideation(self) -> str:
        """Return a description of Copilot's role in the ideation process.

        Returns
        -------
        str
        """
        return (
            "Copilot's role in mathematical ideation:\n"
            "  · Stage 1: Copilot proposes conjectures (ORACLE_PROPOSED)\n"
            "  · Stage 3: Copilot generates proof sketches (still ORACLE_PROPOSED)\n"
            "  · Copilot cannot advance trust level beyond ORACLE_PROPOSED alone\n"
            "  · Copilot can suggest which solver to use for Stage 2\n"
            "  · Copilot can annotate obstruction records with natural-language "
            "explanations of why a conjecture is blocked\n"
            "  · Copilot is the primary user-facing interface to JuGeo's "
            "ideation pipeline"
        )


# ---------------------------------------------------------------------------
# ProblemClassCatalog
# ---------------------------------------------------------------------------


class ProblemClassCatalog:
    """Catalog of all problem classes addressed by JuGeo.

    ``ProblemClassCatalog`` assembles the four main problem classes and
    provides cross-class analysis: which contributions address which classes,
    what the combined trust requirements are, and how the classes relate.

    Parameters
    ----------
    theory_section:
        Reference to theory2.tex section.
    """

    def __init__(self, theory_section: str = "§2.3") -> None:
        """Initialize the catalog.

        Parameters
        ----------
        theory_section:
            Theory reference.
        """
        self.theory_section = theory_section
        self.semantic_verification = SemanticVerificationProblem()
        self.long_horizon = LongHorizonGenerationProblem()
        self.mixed_evidence = MixedEvidenceProblem()
        self.ideation = MathematicalIdeationProblem()
        self._algebra = TrustAlgebra()

    def all_problem_classes(self) -> list[_BaseProblemClass]:
        """Return all problem classes.

        Returns
        -------
        list[_BaseProblemClass]
        """
        return [
            self.semantic_verification,
            self.long_horizon,
            self.mixed_evidence,
            self.ideation,
        ]

    def problem_class_by_id(
        self, problem_id: str
    ) -> _BaseProblemClass | None:
        """Return the problem class with the given ID.

        Parameters
        ----------
        problem_id:
            Problem class identifier (e.g. ``"PC-01"``).

        Returns
        -------
        _BaseProblemClass | None
        """
        for pc in self.all_problem_classes():
            if pc.PROBLEM_ID == problem_id:
                return pc
        return None

    def contribution_coverage(self) -> dict[str, list[str]]:
        """Return a dict mapping each contribution ID to the problem classes it addresses.

        Returns
        -------
        dict[str, list[str]]
            Maps contribution ID to list of problem class IDs.
        """
        coverage: dict[str, list[str]] = {}
        for pc in self.all_problem_classes():
            for contrib_id in pc.addressed_by_contributions():
                coverage.setdefault(contrib_id, []).append(pc.PROBLEM_ID)
        return coverage

    def hardest_trust_requirement(self) -> TrustLevel:
        """Return the highest trust level required across all problem classes.

        Returns
        -------
        TrustLevel
        """
        levels = [pc.trust_required() for pc in self.all_problem_classes()]
        hardest = levels[0]
        for level in levels[1:]:
            if level > hardest:
                hardest = level
        return hardest

    def open_question_count(self) -> int:
        """Return the total number of open questions across all problem classes.

        Returns
        -------
        int
        """
        return sum(len(pc.open_questions()) for pc in self.all_problem_classes())

    def problem_domain_map(self) -> dict[str, str]:
        """Return a mapping from problem ID to problem name.

        Returns
        -------
        dict[str, str]
        """
        return {pc.PROBLEM_ID: pc.PROBLEM_NAME for pc in self.all_problem_classes()}

    def validate_all(self) -> list[StructuredFailure]:
        """Validate all problem class records.

        Returns
        -------
        list[StructuredFailure]
        """
        failures: list[StructuredFailure] = []
        seen_ids: set[str] = set()
        for pc in self.all_problem_classes():
            if pc.PROBLEM_ID in seen_ids:
                failures.append(StructuredFailure(
                    message=f"Duplicate problem class ID: {pc.PROBLEM_ID!r}",
                    scope=FailureScope.CHAPTER,
                    classification=FailureClassification.INVALID_VALUE,
                ))
            seen_ids.add(pc.PROBLEM_ID)
            if not pc.formal_definition():
                failures.append(StructuredFailure(
                    message=f"Problem class {pc.PROBLEM_ID!r} has empty formal_definition",
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
        hardest = self.hardest_trust_requirement()
        open_q_total = self.open_question_count()
        contrib_coverage = self.contribution_coverage()
        cov_lines = "\n".join(
            f"  {cid}: {', '.join(pids)}"
            for cid, pids in sorted(contrib_coverage.items())
        )
        class_summaries = "\n\n".join(
            pc.copilot_summary() for pc in self.all_problem_classes()
        )
        return "\n".join([
            f"ProblemClassCatalog ({self.theory_section})",
            f"Problem classes: {len(self.all_problem_classes())}",
            f"Hardest trust requirement: {hardest.name}",
            f"Total open questions: {open_q_total}",
            "",
            "Contribution → Problem class coverage:",
            cov_lines,
            "",
            "Problem classes:",
            class_summaries,
        ])

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theory_section": self.theory_section,
            "hardest_trust_requirement": self.hardest_trust_requirement().name,
            "open_question_count": self.open_question_count(),
            "problem_classes": [pc.to_dict() for pc in self.all_problem_classes()],
        }


# ---------------------------------------------------------------------------
# Canonical instance
# ---------------------------------------------------------------------------

PROBLEM_CLASS_CATALOG: ProblemClassCatalog = ProblemClassCatalog()
