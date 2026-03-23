r"""Theorem catalog for JuGeo Chapter 2: research questions and thesis claims.

This module enumerates all formal theorems, lemmas, corollaries, and
definitions from Theory2.tex Chapter 2.  Each entry in the catalog carries:

* A unique theorem identifier.
* The full statement (as it appears in the theory).
* Proof status and proof sketch.
* Dependencies on other theorems/lemmas.
* The implementing Python module.
* Copilot involvement notes.

The catalog serves three purposes:

1. **Traceability** — reviewers can navigate from a theorem number to its
   proof status and implementing code.
2. **Dependency analysis** — cycles and missing dependencies are detectable.
3. **CI gating** — theorems with proof status below ``MECHANICALLY_VERIFIED``
   that are required by the thesis claims can be flagged.

Theorem numbering
-----------------

Theorems are numbered as ``T-2.X.Y`` (theorem), ``L-2.X.Y`` (lemma),
``C-2.X.Y`` (corollary), and ``D-2.X.Y`` (definition), where ``X`` is the
section number and ``Y`` is the index within that section.

Theory alignment
----------------

Section 290 of Theory2.tex lists the theorem catalog.  This module is the
machine-readable counterpart to that list.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TheoremKind(Enum):
    """Kind of formal statement."""

    THEOREM = "theorem"
    LEMMA = "lemma"
    COROLLARY = "corollary"
    DEFINITION = "definition"
    PROPOSITION = "proposition"
    CONJECTURE = "conjecture"
    REMARK = "remark"


class ProofStatus(Enum):
    """Proof status of a theorem entry."""

    INFORMAL_SKETCH = "informal_sketch"
    FORMAL_SKETCH = "formal_sketch"
    PARTIALLY_VERIFIED = "partially_verified"
    SOLVER_VERIFIED = "solver_verified"
    MECHANICALLY_VERIFIED = "mechanically_verified"
    OPEN = "open"
    FALSIFIED = "falsified"
    COPILOT_DRAFT = "copilot_draft"

    @property
    def ordinal(self) -> int:
        ranks = {
            "open": -1,
            "falsified": -2,
            "copilot_draft": 0,
            "informal_sketch": 1,
            "formal_sketch": 2,
            "partially_verified": 3,
            "solver_verified": 4,
            "mechanically_verified": 5,
        }
        return ranks[self.value]

    def is_verified(self) -> bool:
        """Return True if the proof reaches solver or mechanical verification."""
        return self in (
            ProofStatus.SOLVER_VERIFIED,
            ProofStatus.MECHANICALLY_VERIFIED,
        )


class ClaimReference(Enum):
    """Thesis claim that a theorem supports."""

    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# TheoremEntry
# ---------------------------------------------------------------------------


@dataclass
class ProofSketch:
    """A proof sketch for a theorem entry.

    Parameters
    ----------
    sketch_id:
        Short identifier.
    method:
        Proof method: ``"induction"``, ``"contradiction"``, ``"construction"``,
        ``"algebraic"``, ``"topological"``, or ``"computational"``.
    outline:
        Numbered list of proof steps as strings.
    copilot_assisted:
        Whether the sketch was drafted with copilot assistance.
        Copilot-drafted sketches carry ``COPILOT_SUGGESTED`` trust until
        verified.
    notes:
        Additional notes.
    """

    sketch_id: str
    method: str
    outline: list[str]
    copilot_assisted: bool = False
    notes: str = ""

    def step_count(self) -> int:
        """Return the number of proof steps."""
        return len(self.outline)

    def is_complete(self) -> bool:
        """Return True if the sketch has at least three steps."""
        return len(self.outline) >= 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "sketch_id": self.sketch_id,
            "method": self.method,
            "outline": self.outline,
            "copilot_assisted": self.copilot_assisted,
            "notes": self.notes,
        }


@dataclass
class TheoremEntry:
    """A single entry in the theorem catalog.

    Parameters
    ----------
    theorem_id:
        Unique identifier following the ``T-2.X.Y`` / ``L-2.X.Y`` scheme.
    kind:
        :class:`TheoremKind`.
    title:
        Short title.
    statement:
        Full formal statement.
    theory_section:
        Theory2.tex section number where this theorem appears.
    proof_status:
        Current :class:`ProofStatus`.
    proof_sketch:
        :class:`ProofSketch` if available, else ``None``.
    dependencies:
        Identifiers of theorems this entry depends on.
    supporting_claims:
        :class:`ClaimReference` values for claims this theorem supports.
    implementing_module:
        Dotted Python path to the module that implements this theorem.
    copilot_involvement:
        Description of copilot involvement in the theorem formulation or proof.
    added_at:
        Unix timestamp when the entry was added.
    """

    theorem_id: str
    kind: TheoremKind
    title: str
    statement: str
    theory_section: str
    proof_status: ProofStatus
    proof_sketch: ProofSketch | None
    dependencies: tuple[str, ...]
    supporting_claims: tuple[ClaimReference, ...]
    implementing_module: str
    copilot_involvement: str = ""
    added_at: float = field(default_factory=time.time)

    def is_proven(self) -> bool:
        """Return True if the proof has been at least solver-verified."""
        return self.proof_status.is_verified()

    def is_open(self) -> bool:
        """Return True if the proof is open (no sketch or verification)."""
        return self.proof_status == ProofStatus.OPEN

    def has_copilot_draft(self) -> bool:
        """Return True if the proof sketch was produced by copilot."""
        return (
            self.proof_status == ProofStatus.COPILOT_DRAFT
            or (
                self.proof_sketch is not None
                and self.proof_sketch.copilot_assisted
            )
        )

    def supports_claim(self, claim: ClaimReference) -> bool:
        """Return True if this theorem supports the given claim."""
        return claim in self.supporting_claims

    def dependency_count(self) -> int:
        """Return the number of direct dependencies."""
        return len(self.dependencies)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theorem_id": self.theorem_id,
            "kind": self.kind.value,
            "title": self.title,
            "statement": self.statement,
            "theory_section": self.theory_section,
            "proof_status": self.proof_status.value,
            "proof_sketch": (
                self.proof_sketch.to_dict()
                if self.proof_sketch is not None
                else None
            ),
            "dependencies": list(self.dependencies),
            "supporting_claims": [c.value for c in self.supporting_claims],
            "implementing_module": self.implementing_module,
            "copilot_involvement": self.copilot_involvement,
            "added_at": self.added_at,
            "is_proven": self.is_proven(),
            "has_copilot_draft": self.has_copilot_draft(),
        }


# ---------------------------------------------------------------------------
# TheoremCatalog
# ---------------------------------------------------------------------------


@dataclass
class TheoremCatalog:
    """Catalog of all theorems, lemmas, and corollaries from Chapter 2.

    Parameters
    ----------
    name:
        Identifier.
    entries:
        List of :class:`TheoremEntry` objects.
    """

    name: str
    entries: list[TheoremEntry] = field(default_factory=list)

    def add(self, entry: TheoremEntry) -> None:
        """Add a theorem entry to the catalog."""
        self.entries.append(entry)

    def get(self, theorem_id: str) -> TheoremEntry | None:
        """Return the entry with the given ID, or None."""
        for e in self.entries:
            if e.theorem_id == theorem_id:
                return e
        return None

    def by_claim(self, claim: ClaimReference) -> list[TheoremEntry]:
        """Return all entries that support the given claim."""
        return [e for e in self.entries if e.supports_claim(claim)]

    def by_kind(self, kind: TheoremKind) -> list[TheoremEntry]:
        """Return all entries of the given kind."""
        return [e for e in self.entries if e.kind == kind]

    def open_theorems(self) -> list[TheoremEntry]:
        """Return theorems whose proof is still open."""
        return [e for e in self.entries if e.is_open()]

    def unproven_required(
        self, required_ids: Sequence[str]
    ) -> list[TheoremEntry]:
        """Return required theorems that are not yet proven.

        Parameters
        ----------
        required_ids:
            Theorem IDs that must be proven for some claim to be verified.

        Returns
        -------
        list[TheoremEntry]
            Entries in *required_ids* whose proof_status is not verified.
        """
        result: list[TheoremEntry] = []
        for tid in required_ids:
            entry = self.get(tid)
            if entry is not None and not entry.is_proven():
                result.append(entry)
        return result

    def copilot_drafted_entries(self) -> list[TheoremEntry]:
        """Return entries with copilot-drafted proof sketches."""
        return [e for e in self.entries if e.has_copilot_draft()]

    def dependency_graph(self) -> dict[str, list[str]]:
        """Return the dependency graph as an adjacency list."""
        return {e.theorem_id: list(e.dependencies) for e in self.entries}

    def topological_order(self) -> list[str] | None:
        """Return a topological ordering of theorem IDs.

        Returns ``None`` if the dependency graph contains a cycle.

        Uses Kahn's algorithm.
        """
        graph = self.dependency_graph()
        in_degree: dict[str, int] = {tid: 0 for tid in graph}
        for tid, deps in graph.items():
            for dep in deps:
                in_degree[dep] = in_degree.get(dep, 0)
                in_degree[tid] = in_degree.get(tid, 0) + 1
        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for tid, deps in graph.items():
                if node in deps:
                    in_degree[tid] -= 1
                    if in_degree[tid] == 0:
                        queue.append(tid)
        if len(order) != len(self.entries):
            return None  # cycle detected
        return order

    def proof_status_summary(self) -> dict[str, int]:
        """Return a count of entries by proof status."""
        counts: dict[str, int] = {}
        for e in self.entries:
            counts[e.proof_status.value] = counts.get(e.proof_status.value, 0) + 1
        return counts

    def ci_gate_report(self, required_theorem_ids: Sequence[str] | None = None) -> dict[str, Any]:
        """Produce a CI gate report for the theorem catalog.

        Parameters
        ----------
        required_theorem_ids:
            If provided, checks that these theorems are proven.

        Returns
        -------
        dict[str, Any]
            ``passed`` (bool), unproven required theorems, open theorems,
            copilot-drafted count.
        """
        required_ids = list(required_theorem_ids or [])
        unproven = self.unproven_required(required_ids) if required_ids else []
        open_list = self.open_theorems()
        copilot_count = len(self.copilot_drafted_entries())
        passed = not unproven and not open_list
        return {
            "passed": passed,
            "n_entries": len(self.entries),
            "unproven_required": [e.theorem_id for e in unproven],
            "open_theorems": [e.theorem_id for e in open_list],
            "n_copilot_drafted": copilot_count,
            "proof_status_summary": self.proof_status_summary(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n_entries": len(self.entries),
            "proof_status_summary": self.proof_status_summary(),
            "entries": [e.to_dict() for e in self.entries],
        }


# ---------------------------------------------------------------------------
# Canonical catalog
# ---------------------------------------------------------------------------


def _sketch(
    sid: str,
    method: str,
    outline: list[str],
    copilot: bool = False,
    notes: str = "",
) -> ProofSketch:
    return ProofSketch(
        sketch_id=sid,
        method=method,
        outline=outline,
        copilot_assisted=copilot,
        notes=notes,
    )


def build_canonical_catalog() -> TheoremCatalog:
    """Construct the canonical theorem catalog for JuGeo Chapter 2.

    Returns
    -------
    TheoremCatalog
        Populated with all theorems, lemmas, corollaries, and definitions
        from Theory2.tex Chapter 2.
    """
    cat = TheoremCatalog(name="jugeo_ch2_theorems")

    # -----------------------------------------------------------------------
    # §231 — Presheaf axioms
    # -----------------------------------------------------------------------

    cat.add(TheoremEntry(
        theorem_id="D-2.3.1",
        kind=TheoremKind.DEFINITION,
        title="Judgment Presheaf",
        statement=(
            "Let Ctx be the poset of judgment contexts ordered by refinement. "
            "The judgment presheaf F : Ctx^op → Set assigns to each context U "
            "the set F(U) of admissible judgment-tuple sections over U, "
            "and to each morphism i : U → V the restriction map ρ_{UV} : F(V) → F(U). "
            "F is a presheaf if it satisfies PF-1 (identity) and PF-2 (composition)."
        ),
        theory_section="§231",
        proof_status=ProofStatus.FORMAL_SKETCH,
        proof_sketch=_sketch(
            "PS-D-2.3.1",
            "algebraic",
            [
                "Define Ctx as the poset of judgment contexts.",
                "For each U in Ctx, define F(U) as the set of judgment-tuple sections.",
                "For each morphism i : U → V, define ρ_{UV} as the restriction map.",
                "Verify PF-1: ρ_{UU} = id_{F(U)} for all U.",
                "Verify PF-2: ρ_{UW} = ρ_{UV} ∘ ρ_{VW} for all U ⊆ V ⊆ W.",
            ],
        ),
        dependencies=(),
        supporting_claims=(ClaimReference.C1,),
        implementing_module="jugeo.thesis.research_program.representation",
    ))

    cat.add(TheoremEntry(
        theorem_id="T-2.3.1",
        kind=TheoremKind.THEOREM,
        title="Presheaf Composition Law",
        statement=(
            "For the judgment presheaf F, restriction maps satisfy the "
            "composition law: for all morphisms i : U → V and j : V → W, "
            "ρ_{UW} = ρ_{UV} ∘ ρ_{VW}."
        ),
        theory_section="§231",
        proof_status=ProofStatus.SOLVER_VERIFIED,
        proof_sketch=_sketch(
            "PS-T-2.3.1",
            "algebraic",
            [
                "Expand ρ_{UW}(σ) for arbitrary section σ in F(W).",
                "Show that (ρ_{UV} ∘ ρ_{VW})(σ) = ρ_{UV}(ρ_{VW}(σ)).",
                "Apply PF-2 to conclude equality.",
                "Verified for all tested morphisms by JudgmentPresheaf.check_composition_law().",
            ],
        ),
        dependencies=("D-2.3.1",),
        supporting_claims=(ClaimReference.C1,),
        implementing_module="jugeo.thesis.research_program.representation",
    ))

    cat.add(TheoremEntry(
        theorem_id="L-2.3.1",
        kind=TheoremKind.LEMMA,
        title="Identity Restriction",
        statement=(
            "For the judgment presheaf F and any context U, "
            "ρ_{UU} = id_{F(U)}: restricting along the identity morphism is identity."
        ),
        theory_section="§231",
        proof_status=ProofStatus.SOLVER_VERIFIED,
        proof_sketch=_sketch(
            "PS-L-2.3.1",
            "algebraic",
            [
                "The identity morphism id_U : U → U sends every point to itself.",
                "By functoriality, F(id_U) = id_{F(U)}.",
                "Therefore ρ_{UU}(σ) = σ for all σ in F(U).",
            ],
        ),
        dependencies=("D-2.3.1",),
        supporting_claims=(ClaimReference.C1,),
        implementing_module="jugeo.thesis.research_program.representation",
    ))

    # -----------------------------------------------------------------------
    # §232 — Coordinate completeness
    # -----------------------------------------------------------------------

    cat.add(TheoremEntry(
        theorem_id="T-2.3.2",
        kind=TheoremKind.THEOREM,
        title="Coordinate Injectivity",
        statement=(
            "The coordinate assignment φ : SemanticState → Coord is injective: "
            "for all states s1, s2 in SemanticState, φ(s1) = φ(s2) implies s1 = s2."
        ),
        theory_section="§232",
        proof_status=ProofStatus.PARTIALLY_VERIFIED,
        proof_sketch=_sketch(
            "PS-T-2.3.2",
            "construction",
            [
                "Define the six-component coordinate: (c, hash(φ), A, fp(E), T, hash(Π)).",
                "Suppose φ(s1) = φ(s2), i.e., all six components agree.",
                "From c(s1) = c(s2) and hash(φ(s1)) = hash(φ(s2)), "
                "conclude c and φ agree (modulo SHA-256 collision).",
                "By construction, A, E, T, Π also agree.",
                "Therefore s1 = s2 (assuming collision-free hashing).",
            ],
            notes="SHA-256 collision assumption is standard; full mechanical proof is future work.",
        ),
        dependencies=("D-2.3.1",),
        supporting_claims=(ClaimReference.C1,),
        implementing_module="jugeo.thesis.research_program.representation",
    ))

    cat.add(TheoremEntry(
        theorem_id="C-2.3.1",
        kind=TheoremKind.COROLLARY,
        title="Coordinate Completeness",
        statement=(
            "Every admissible semantic state has a unique coordinate in the "
            "six-dimensional judgment space."
        ),
        theory_section="§232",
        proof_status=ProofStatus.PARTIALLY_VERIFIED,
        proof_sketch=_sketch(
            "PS-C-2.3.1",
            "construction",
            [
                "Follows directly from T-2.3.2 (injectivity) and the surjectivity "
                "of the coordinate construction (every state can be assigned a coordinate).",
            ],
        ),
        dependencies=("T-2.3.2",),
        supporting_claims=(ClaimReference.C1,),
        implementing_module="jugeo.thesis.research_program.representation",
    ))

    # -----------------------------------------------------------------------
    # §233 — Cover soundness
    # -----------------------------------------------------------------------

    cat.add(TheoremEntry(
        theorem_id="T-2.3.3",
        kind=TheoremKind.THEOREM,
        title="Cover Locality",
        statement=(
            "For the judgment presheaf cover {U_i} of X, the locality axiom "
            "holds: if σ, τ in F(X) agree on every U_i, then σ = τ."
        ),
        theory_section="§233",
        proof_status=ProofStatus.FORMAL_SKETCH,
        proof_sketch=_sketch(
            "PS-T-2.3.3",
            "topological",
            [
                "Suppose σ|_{U_i} = τ|_{U_i} for all i.",
                "Since {U_i} covers X, every point of X lies in some U_i.",
                "At each point, σ and τ agree (by restriction).",
                "Therefore σ = τ globally.",
            ],
        ),
        dependencies=("D-2.3.1",),
        supporting_claims=(ClaimReference.C1,),
        implementing_module="jugeo.thesis.research_program.representation",
    ))

    # -----------------------------------------------------------------------
    # §241 — Channel jurisdiction
    # -----------------------------------------------------------------------

    cat.add(TheoremEntry(
        theorem_id="D-2.4.1",
        kind=TheoremKind.DEFINITION,
        title="Channel Jurisdiction",
        statement=(
            "A channel jurisdiction J(ch) = (K_ch, T_ch) consists of a set K_ch "
            "of authorised support kinds and a trust ceiling T_ch. A channel ch "
            "may only produce evidence of kinds in K_ch at trust levels ≤ T_ch. "
            "In particular, J(copilot) = ({semantic_proposal, behavioral_proposal}, "
            "COPILOT_SUGGESTED)."
        ),
        theory_section="§241",
        proof_status=ProofStatus.FORMAL_SKETCH,
        proof_sketch=None,
        dependencies=(),
        supporting_claims=(ClaimReference.C2,),
        implementing_module="jugeo.thesis.research_program.mixed_evidence",
    ))

    cat.add(TheoremEntry(
        theorem_id="T-2.4.1",
        kind=TheoremKind.THEOREM,
        title="No-Silent-Promotion",
        statement=(
            "Under the channel jurisdiction constraints, no evidence atom from "
            "the copilot/oracle channel can carry trust above COPILOT_SUGGESTED "
            "without an explicit promotion record. Formally: for all atoms a with "
            "channel(a) ∈ {copilot, oracle}, trust(a) ≤ COPILOT_SUGGESTED or "
            "promotion_record(a) ≠ ∅."
        ),
        theory_section="§241",
        proof_status=ProofStatus.SOLVER_VERIFIED,
        proof_sketch=_sketch(
            "PS-T-2.4.1",
            "algebraic",
            [
                "Every atom enters through ChannelBoundary.enforce_ceiling().",
                "enforce_ceiling() clamps to the channel's declared ceiling.",
                "For copilot/oracle channels, the ceiling is COPILOT_SUGGESTED.",
                "Promotion requires an explicit promotion_record (non-empty string).",
                "Therefore no atom from copilot/oracle can carry higher trust "
                "without an explicit promotion record.",
            ],
        ),
        dependencies=("D-2.4.1",),
        supporting_claims=(ClaimReference.C2,),
        implementing_module="jugeo.thesis.research_program.mixed_evidence",
    ))

    cat.add(TheoremEntry(
        theorem_id="T-2.4.2",
        kind=TheoremKind.THEOREM,
        title="Federation Kind Preservation",
        statement=(
            "The federation operation ⊕ preserves support kinds: "
            "for any set of input evidence pluralities P = {P_1, ..., P_n}, "
            "kinds(⊕(P)) ⊇ ∪ kinds(P_i). No support kind is lost in federation."
        ),
        theory_section="§242",
        proof_status=ProofStatus.SOLVER_VERIFIED,
        proof_sketch=_sketch(
            "PS-T-2.4.2",
            "algebraic",
            [
                "FederationProtocol.federate() collects all atoms from all input pluralities.",
                "Each atom retains its kind label after ceiling enforcement.",
                "The output FederatedEvidence.distinct_kinds() equals the union of input kinds.",
                "FederationProtocol.verify_kind_preservation() checks this invariant.",
            ],
        ),
        dependencies=("D-2.4.1", "T-2.4.1"),
        supporting_claims=(ClaimReference.C2,),
        implementing_module="jugeo.thesis.research_program.mixed_evidence",
    ))

    # -----------------------------------------------------------------------
    # §252 — Lyapunov convergence
    # -----------------------------------------------------------------------

    cat.add(TheoremEntry(
        theorem_id="D-2.5.1",
        kind=TheoremKind.DEFINITION,
        title="Semantic Lyapunov Function",
        statement=(
            "A function V : J → R≥0 is a semantic Lyapunov function for an "
            "orchestrator O if: (i) V(J) ≥ 0 for all J; "
            "(ii) V(J) = 0 iff J is a goal state; "
            "(iii) V(π(J)) ≤ V(J) for all J, where π(J) is the next state under O."
        ),
        theory_section="§252",
        proof_status=ProofStatus.FORMAL_SKETCH,
        proof_sketch=None,
        dependencies=(),
        supporting_claims=(ClaimReference.C3,),
        implementing_module="jugeo.thesis.research_program.long_horizon_orchestration",
        copilot_involvement=(
            "Lyapunov function structure was initially outlined with copilot assistance. "
            "Definition reviewed and promoted."
        ),
    ))

    cat.add(TheoremEntry(
        theorem_id="T-2.5.1",
        kind=TheoremKind.THEOREM,
        title="Orchestrator Convergence",
        statement=(
            "If V is a semantic Lyapunov function for orchestrator O and "
            "the state space J is finite, then O converges to a goal state "
            "in at most |J| steps."
        ),
        theory_section="§252",
        proof_status=ProofStatus.PARTIALLY_VERIFIED,
        proof_sketch=_sketch(
            "PS-T-2.5.1",
            "induction",
            [
                "By definition, V(J_t) is non-increasing and bounded below by 0.",
                "Since J is finite, V takes finitely many values.",
                "A non-increasing sequence on a finite value set must eventually stabilise.",
                "Stabilisation at V = 0 is the goal state (by D-2.5.1).",
                "ConvergenceCondition.verify_on_trajectory() tests this for concrete instances.",
            ],
            notes="Full mechanical verification requires a finite-state bound, which is future work.",
        ),
        dependencies=("D-2.5.1",),
        supporting_claims=(ClaimReference.C3,),
        implementing_module="jugeo.thesis.research_program.long_horizon_orchestration",
    ))

    cat.add(TheoremEntry(
        theorem_id="L-2.5.1",
        kind=TheoremKind.LEMMA,
        title="Greedy Control Law Decreases V",
        statement=(
            "The greedy control law π_G (select action with most negative expected "
            "trust delta) is admissible and satisfies V(π_G(J)) ≤ V(J) - ε for "
            "some ε > 0 whenever J is not a goal state."
        ),
        theory_section="§253",
        proof_status=ProofStatus.INFORMAL_SKETCH,
        proof_sketch=_sketch(
            "PS-L-2.5.1",
            "construction",
            [
                "At each non-goal state J, there exists at least one action a with "
                "expected_trust_delta < 0 (by the progress assumption).",
                "π_G selects the action with the most negative delta.",
                "After applying the transition function, V decreases by at least ε = min |delta|.",
            ],
        ),
        dependencies=("D-2.5.1",),
        supporting_claims=(ClaimReference.C3,),
        implementing_module="jugeo.thesis.research_program.long_horizon_orchestration",
    ))

    # -----------------------------------------------------------------------
    # §261 — Novelty measure
    # -----------------------------------------------------------------------

    cat.add(TheoremEntry(
        theorem_id="D-2.6.1",
        kind=TheoremKind.DEFINITION,
        title="Novelty Measure",
        statement=(
            "A novelty measure μ : S → R≥0 is non-degenerate if: "
            "(i) there exists s in S with μ(s) > θ_novel (genuinely novel), and "
            "(ii) there exists s' in S with μ(s') ≤ θ_novel (known or trivial). "
            "Here θ_novel is the declared novelty threshold."
        ),
        theory_section="§261",
        proof_status=ProofStatus.FORMAL_SKETCH,
        proof_sketch=None,
        dependencies=(),
        supporting_claims=(ClaimReference.C4,),
        implementing_module="jugeo.thesis.research_program.mathematical_ideation",
    ))

    cat.add(TheoremEntry(
        theorem_id="T-2.6.1",
        kind=TheoremKind.THEOREM,
        title="Discovery Engine Termination",
        statement=(
            "For any IdeationSpec with horizon H < ∞, the DiscoveryEngine "
            "terminates in at most H rounds."
        ),
        theory_section="§263",
        proof_status=ProofStatus.SOLVER_VERIFIED,
        proof_sketch=_sketch(
            "PS-T-2.6.1",
            "induction",
            [
                "The outer loop iterates at most spec.horizon times.",
                "Each iteration produces a finite list of candidates (bounded by |templates|).",
                "The loop terminates when the budget is exhausted or the target is reached.",
                "Therefore termination in at most H rounds is guaranteed by construction.",
            ],
        ),
        dependencies=("D-2.6.1",),
        supporting_claims=(ClaimReference.C4,),
        implementing_module="jugeo.thesis.research_program.mathematical_ideation",
    ))

    cat.add(TheoremEntry(
        theorem_id="L-2.6.1",
        kind=TheoremKind.LEMMA,
        title="Copilot Proposal Requires Evaluation",
        statement=(
            "A copilot-generated candidate structure carries COPILOT_SUGGESTED trust "
            "until it passes novelty and purpose evaluation; after evaluation, it may "
            "be promoted through the standard route."
        ),
        theory_section="§263",
        proof_status=ProofStatus.SOLVER_VERIFIED,
        proof_sketch=_sketch(
            "PS-L-2.6.1",
            "algebraic",
            [
                "CandidateStructure.source == 'copilot' implies copilot_origin=True.",
                "The discovery engine runs the novelty measure on all candidates.",
                "Candidates that fail the novelty threshold are discarded.",
                "Accepted candidates may be promoted by the researcher (explicit action).",
                "No auto-promotion occurs in the ideation loop.",
            ],
        ),
        dependencies=("D-2.6.1",),
        supporting_claims=(ClaimReference.C4,),
        implementing_module="jugeo.thesis.research_program.mathematical_ideation",
        copilot_involvement="This lemma directly concerns copilot evidence handling.",
    ))

    # -----------------------------------------------------------------------
    # §220 — Falsifiability
    # -----------------------------------------------------------------------

    cat.add(TheoremEntry(
        theorem_id="T-2.2.1",
        kind=TheoremKind.THEOREM,
        title="Thesis Falsifiability",
        statement=(
            "Each of the four thesis claims C1–C4 has at least one fatal "
            "falsification condition: a testable property whose failure would "
            "directly refute the claim."
        ),
        theory_section="§220",
        proof_status=ProofStatus.FORMAL_SKETCH,
        proof_sketch=_sketch(
            "PS-T-2.2.1",
            "construction",
            [
                "For C1: the coordinate injectivity test (TP-C1.2) is fatal.",
                "For C2: the copilot ceiling violation test (TP-C2.1) is fatal.",
                "For C3: the Lyapunov non-decrease test (TP-C3.1) is fatal.",
                "For C4: the novelty discrimination test (TP-C4.1) is fatal.",
                "Each test is implemented in falsifiability and can be run automatically.",
            ],
        ),
        dependencies=("T-2.3.2", "T-2.4.1", "T-2.5.1", "T-2.6.1"),
        supporting_claims=(
            ClaimReference.C1,
            ClaimReference.C2,
            ClaimReference.C3,
            ClaimReference.C4,
        ),
        implementing_module="jugeo.thesis.research_program.falsifiability",
    ))

    return cat


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

THEOREM_CATALOG: TheoremCatalog = build_canonical_catalog()
"""The canonical theorem catalog for jugeo.thesis.research_program."""


def get_theorem_catalog() -> TheoremCatalog:
    """Return the module-level :data:`THEOREM_CATALOG` singleton."""
    return THEOREM_CATALOG
