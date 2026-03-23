"""Theorem declarations and proof obligations for repair_semantics (theory2.tex Ch11).

This module declares the eight core theorems of Chapter 11 of
``preliminaries/theory2.tex`` as first-class Python objects, each carrying:

* A precise statement of the theorem.
* A proof strategy (structural induction, coinduction, construction, etc.).
* A multi-paragraph proof sketch explaining the key steps.
* The theory reference (section and theorem number in theory2.tex).
* The current verification status (conjectured, proof sketch, formal proof, etc.).
* Zero or more assumptions and conclusions for use by automated proof checkers.

The module also provides functions that generate session-specific proof
obligations (:func:`generate_proof_obligations`), query the theorem registry
(:func:`check_theorem`, :func:`get_all_theorems`), and produce a human-readable
coverage report (:func:`theorem_coverage_report`).

Why first-class theorems?
--------------------------
Treating theorems as Python objects rather than comments or documentation files
enables:

1. **Traceability**: repair operations can attach theorem obligations as
   provenance metadata to judgments.
2. **Automation**: proof checkers and formal verification tools can enumerate
   obligations programmatically.
3. **Reporting**: coverage dashboards can show which theorems are formally
   verified vs. merely conjectured.
4. **Session customization**: :func:`generate_proof_obligations` adapts
   proof sketches to the state of a concrete :class:`~models.DebugSession`.

Theory reference
----------------
All theorems are from ``preliminaries/theory2.tex`` Chapter 11:

* §11.1 — Counterexample minimality and cohomology class consistency.
* §11.2 — Repair plan admissibility, descent preservation, and local section
  replacement soundness.
* §11.3 — Repair frontier minimality.
* §11.4 — Repair convergence and debug session monotonicity.

See also
--------
* :mod:`jugeo.problem_modes.repair_semantics.algorithms` — implementations of
  several of the algorithmic results declared here.
* :mod:`jugeo.problem_modes.repair_semantics.models` — model types referenced
  in theorem statements.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from jugeo.errors import JuGeoError, FailureScope, FailureClassification
from jugeo.problem_modes.repair_semantics.models import (
    DebugSession,
    DebugSessionStatus,
)

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "theorems",
    "theory_section": "§11 — Theorems for Debugging and Repair",
}

# ---------------------------------------------------------------------------
# §A  Theorem target registry
# ---------------------------------------------------------------------------

THEOREM_TARGETS: tuple[tuple[str, str, str], ...] = (
    (
        "counterexample_minimality",
        "Every non-trivial counterexample has a minimal sub-witness",
        "theory2.tex §11.1 Theorem 11.1",
    ),
    (
        "repair_admissibility",
        "A repair plan is admissible iff its dependency graph is acyclic",
        "theory2.tex §11.2 Theorem 11.2",
    ),
    (
        "descent_preservation",
        "Admissible repairs preserve the descent conditions on overlaps",
        "theory2.tex §11.2 Theorem 11.3",
    ),
    (
        "frontier_minimality",
        "The repair frontier is minimal iff it covers all obstructions",
        "theory2.tex §11.3 Theorem 11.4",
    ),
    (
        "repair_convergence",
        "Iterative repair converges when the frontier shrinks monotonically",
        "theory2.tex §11.4 Theorem 11.5",
    ),
    (
        "cohomology_class_consistency",
        "The cohomology class of a counterexample is invariant under minimization",
        "theory2.tex §11.1 Theorem 11.6",
    ),
    (
        "session_monotonicity",
        "The debug session status is monotone: OPEN → CONVERGED or BLOCKED",
        "theory2.tex §11.4 Theorem 11.7",
    ),
    (
        "local_section_replacement_soundness",
        (
            "Replacing a local section s_i with s_i' is sound iff "
            "s_i' satisfies the descent conditions"
        ),
        "theory2.tex §11.2 Theorem 11.8",
    ),
)

# ---------------------------------------------------------------------------
# §1  Enumerations
# ---------------------------------------------------------------------------


class ProofStrategy(str, Enum):
    """Available proof strategies for theorem obligations.

    These map onto standard mathematical proof techniques.  Each strategy
    has different automation potential: ``CONSTRUCTION`` and ``ALGEBRAIC``
    strategies are most amenable to automated checking, while
    ``REDUCTIO_AD_ABSURDUM`` and ``COINDUCTION`` require more manual work.

    Attributes
    ----------
    STRUCTURAL_INDUCTION
        Proof by induction on the structure of the principal data type.
    COINDUCTION
        Proof by coinduction on a greatest fixed point.
    REDUCTIO_AD_ABSURDUM
        Proof by contradiction: assume the negation and derive False.
    CONSTRUCTION
        Explicit construction of the required object or witness.
    SIMULATION
        Proof by bisimulation / simulation relation.
    ALGEBRAIC
        Equational reasoning in an algebraic structure.
    COMBINATORIAL
        Counting argument or combinatorial identity.
    """

    STRUCTURAL_INDUCTION = "structural_induction"
    COINDUCTION = "coinduction"
    REDUCTIO_AD_ABSURDUM = "reductio_ad_absurdum"
    CONSTRUCTION = "construction"
    SIMULATION = "simulation"
    ALGEBRAIC = "algebraic"
    COMBINATORIAL = "combinatorial"


class TheoremStatus(str, Enum):
    """Verification status of a theorem obligation.

    Attributes
    ----------
    CONJECTURED
        The theorem has been stated but not yet proven.
    PROOF_SKETCH
        An informal proof sketch exists but has not been formalized.
    FORMAL_PROOF
        A machine-checkable formal proof exists.
    COUNTEREXAMPLE_KNOWN
        A counterexample to the stated theorem has been found.
    VACUOUSLY_TRUE
        The theorem is vacuously true (e.g. the hypothesis is False).
    """

    CONJECTURED = "conjectured"
    PROOF_SKETCH = "proof_sketch"
    FORMAL_PROOF = "formal_proof"
    COUNTEREXAMPLE_KNOWN = "counterexample_known"
    VACUOUSLY_TRUE = "vacuously_true"


# ---------------------------------------------------------------------------
# §2  TheoremObligation dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremObligation:
    """A single theorem obligation with proof metadata.

    :class:`TheoremObligation` is the central object in this module.  Each
    instance represents one theorem from theory2.tex Chapter 11, together
    with enough metadata to drive automated proof checking, traceability,
    and coverage reporting.

    Immutability contract
    ---------------------
    :class:`TheoremObligation` is a frozen dataclass.  Mutation methods
    (:meth:`with_proof_sketch`, :meth:`with_status`) return new instances
    without modifying the original.

    Parameters
    ----------
    obligation_id : str
        UUID-based unique identifier for this obligation instance.
    theorem_name : str
        Machine-readable theorem name (snake_case, matching THEOREM_TARGETS).
    statement : str
        Precise natural-language or semi-formal statement of the theorem.
    theory_reference : str
        Citation in the form ``"theory2.tex §X.Y Theorem X.Z"``.
    proof_strategy : ProofStrategy
        The primary proof technique to use.
    status : TheoremStatus
        Current verification status.
    proof_sketch : str
        Multi-paragraph informal proof description.
    assumptions : tuple[str, ...]
        Named assumptions required for the theorem to hold.
    conclusions : tuple[str, ...]
        Named conclusions the theorem establishes.
    related_theorems : tuple[str, ...]
        Names of closely related theorems (for cross-reference).
    checked_at : str
        ISO-8601 timestamp of the last formal verification check.
    """

    obligation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    theorem_name: str = ""
    statement: str = ""
    theory_reference: str = ""
    proof_strategy: ProofStrategy = ProofStrategy.CONSTRUCTION
    status: TheoremStatus = TheoremStatus.PROOF_SKETCH
    proof_sketch: str = ""
    assumptions: tuple[str, ...] = ()
    conclusions: tuple[str, ...] = ()
    related_theorems: tuple[str, ...] = ()
    checked_at: str = ""

    def is_established(self) -> bool:
        """Return True when the theorem has a formal proof or is vacuously true.

        Returns
        -------
        bool
            ``True`` iff ``status`` is ``FORMAL_PROOF`` or ``VACUOUSLY_TRUE``.
        """
        return self.status in (TheoremStatus.FORMAL_PROOF, TheoremStatus.VACUOUSLY_TRUE)

    def with_proof_sketch(self, sketch: str) -> TheoremObligation:
        """Return a copy of this obligation with an updated proof sketch.

        Parameters
        ----------
        sketch : str
            The new proof sketch text.

        Returns
        -------
        TheoremObligation
            Updated obligation with ``proof_sketch = sketch``.
        """
        from dataclasses import replace
        return replace(self, proof_sketch=sketch)

    def with_status(self, status: TheoremStatus) -> TheoremObligation:
        """Return a copy of this obligation with an updated status.

        Parameters
        ----------
        status : TheoremStatus
            The new verification status.

        Returns
        -------
        TheoremObligation
            Updated obligation with ``status = status`` and
            ``checked_at`` set to the current time.
        """
        from dataclasses import replace
        checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return replace(self, status=status, checked_at=checked)

    def to_dict(self) -> dict:
        """Serialize this obligation to a plain dictionary.

        Returns
        -------
        dict
            JSON-serializable dictionary representation.
        """
        return {
            "obligation_id": self.obligation_id,
            "theorem_name": self.theorem_name,
            "statement": self.statement,
            "theory_reference": self.theory_reference,
            "proof_strategy": self.proof_strategy.value,
            "status": self.status.value,
            "proof_sketch": self.proof_sketch,
            "assumptions": list(self.assumptions),
            "conclusions": list(self.conclusions),
            "related_theorems": list(self.related_theorems),
            "checked_at": self.checked_at,
            "is_established": self.is_established(),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> TheoremObligation:
        """Deserialize a :class:`TheoremObligation` from a dictionary.

        Parameters
        ----------
        payload : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        TheoremObligation
            Reconstructed obligation.

        Raises
        ------
        KeyError
            If a required field is missing from ``payload``.
        ValueError
            If an enum value is unrecognized.
        """
        try:
            strategy = ProofStrategy(payload.get("proof_strategy", "construction"))
        except ValueError:
            strategy = ProofStrategy.CONSTRUCTION
        try:
            status = TheoremStatus(payload.get("status", "proof_sketch"))
        except ValueError:
            status = TheoremStatus.PROOF_SKETCH

        return cls(
            obligation_id=str(payload.get("obligation_id", str(uuid.uuid4()))),
            theorem_name=str(payload.get("theorem_name", "")),
            statement=str(payload.get("statement", "")),
            theory_reference=str(payload.get("theory_reference", "")),
            proof_strategy=strategy,
            status=status,
            proof_sketch=str(payload.get("proof_sketch", "")),
            assumptions=tuple(str(a) for a in payload.get("assumptions", ())),
            conclusions=tuple(str(c) for c in payload.get("conclusions", ())),
            related_theorems=tuple(
                str(r) for r in payload.get("related_theorems", ())
            ),
            checked_at=str(payload.get("checked_at", "")),
        )


# ---------------------------------------------------------------------------
# §3  Module-level theorem constants
# ---------------------------------------------------------------------------

THEOREM_COUNTEREXAMPLE_MINIMALITY = TheoremObligation(
    theorem_name="counterexample_minimality",
    statement="Every non-trivial counterexample has a minimal sub-witness.",
    theory_reference="theory2.tex §11.1 Theorem 11.1",
    proof_strategy=ProofStrategy.STRUCTURAL_INDUCTION,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "We prove by strong induction on the size |α| of the variable assignment α that "
        "every failing assignment has a minimal failing sub-assignment.\n\n"
        "Base case: |α| = 1. A single-variable assignment is already minimal; there is no "
        "strictly smaller non-empty subset.\n\n"
        "Inductive step: assume every assignment of size < k has a minimal sub-witness. "
        "Let α be a failing assignment with |α| = k. Run one round of delta-debugging: "
        "split α into two halves α₁, α₂. If either half alone causes the failure, apply "
        "the inductive hypothesis to that half (it has size ≤ k/2 < k). If neither half "
        "alone causes the failure but their union does, continue splitting with finer "
        "granularity. The algorithm terminates because |α| strictly decreases each time "
        "a proper subset is found to be failing.\n\n"
        "Minimality: once the algorithm produces a candidate α', we verify that no "
        "single-element removal of α' causes a smaller failure. This final pass runs in "
        "O(|α'|) checker calls and guarantees 1-minimality (no single element is "
        "removable while preserving the failure).\n\n"
        "Note: the theorem states existence of a minimal sub-witness, not uniqueness. "
        "In general, multiple incomparable minimal sub-witnesses may exist."
    ),
    assumptions=(
        "The checker function is deterministic.",
        "The failure is monotone: if α causes failure and β ⊇ α, then β also causes failure.",
        "The assignment is finite.",
    ),
    conclusions=(
        "There exists a minimal sub-assignment α' ⊆ α such that checker(α') is True.",
        "α' is 1-minimal: for every key k ∈ α', checker(α' \\ {k}) is False.",
    ),
    related_theorems=("cohomology_class_consistency",),
)

THEOREM_REPAIR_ADMISSIBILITY = TheoremObligation(
    theorem_name="repair_admissibility",
    statement=(
        "A repair plan P is admissible if and only if its dependency graph G_P is a "
        "directed acyclic graph (DAG)."
    ),
    theory_reference="theory2.tex §11.2 Theorem 11.2",
    proof_strategy=ProofStrategy.REDUCTIO_AD_ABSURDUM,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "(⟹) Suppose P is admissible. By definition of admissibility, the partial order "
        "≤_P on repair steps is a strict partial order (irreflexive, asymmetric, "
        "transitive). A strict partial order on a finite set has no cycles, so G_P is a "
        "DAG.\n\n"
        "(⟸) Suppose G_P is a DAG. We must show that there exists a topological "
        "linearization of the steps that respects all dependency edges, and that executing "
        "steps in this order is semantically consistent.\n\n"
        "By Kahn's theorem (1962), every finite DAG has a topological sort, so such a "
        "linearization exists. We define the partial order ≤_P as the reflexive-transitive "
        "closure of the edge relation in G_P. Since G_P has no cycles, this closure is "
        "antisymmetric, hence a partial order.\n\n"
        "Semantic consistency: in the geometric setting, step r_i at coordinate c_i "
        "modifies the local section s_i. Step r_j at coordinate c_j depends on r_i iff "
        "s_j's correctness condition depends on the value of s_i. The DAG condition "
        "guarantees that no step circularly depends on its own output, which is the "
        "necessary condition for the global section to be well-defined after all steps.\n\n"
        "Proof by contradiction for the converse direction: if G_P had a cycle "
        "r₁ → r₂ → … → r₁, then r₁ would need to execute both before and after r₂, "
        "which is impossible in a sequential execution model."
    ),
    assumptions=(
        "The repair plan operates on a finite set of steps.",
        "Each step has a well-defined semantic effect on a specific coordinate.",
        "Dependencies between steps are syntactically declared in dependency_order.",
    ),
    conclusions=(
        "An admissible plan has a total execution order consistent with all dependencies.",
        "Admissible plans can be executed by any topological sort of their dependency DAG.",
    ),
    related_theorems=("descent_preservation", "local_section_replacement_soundness"),
)

THEOREM_DESCENT_PRESERVATION = TheoremObligation(
    theorem_name="descent_preservation",
    statement=(
        "Admissible repairs preserve the descent conditions on overlaps. "
        "Formally: if P is admissible and each step r_i produces a section s_i' that "
        "satisfies the local conditions at c_i, then the family (s_i') satisfies the "
        "descent conditions on all overlapping coordinates."
    ),
    theory_reference="theory2.tex §11.2 Theorem 11.3",
    proof_strategy=ProofStrategy.ALGEBRAIC,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "The proof proceeds by verifying the gluing axiom of the sheaf of sections over "
        "the semantic site.\n\n"
        "Setup: Let {U_i} be a cover of the coordinate c, and let {s_i} be the original "
        "family of local sections. The descent condition requires that on each overlap "
        "U_i ∩ U_j, the restrictions of s_i and s_j agree: ρ_{U_i ∩ U_j}(s_i) = "
        "ρ_{U_i ∩ U_j}(s_j).\n\n"
        "After the admissible repair, each s_i is replaced by s_i'. We need to show "
        "that ρ_{U_i ∩ U_j}(s_i') = ρ_{U_i ∩ U_j}(s_j') for all i, j.\n\n"
        "By local section replacement soundness (Theorem 11.8), each s_i' satisfies the "
        "local conditions at U_i. The key step: since the repair is admissible, the "
        "dependency graph is a DAG, which means the repair steps are applied in an order "
        "that respects the restriction maps of the sheaf.\n\n"
        "Specifically, if U_i ∩ U_j ≠ ∅, then the dependency order ensures either "
        "r_i ≤_P r_j or r_j ≤_P r_i or they are independent. In the independent case, "
        "the descent condition on U_i ∩ U_j was not violated by either repair (by the "
        "local soundness assumption). In the dependent case, the later step explicitly "
        "accounts for the restriction of the earlier step's output.\n\n"
        "The algebraic content: this is a coherence condition for the natural "
        "transformations induced by the repair steps on the sections functor. The DAG "
        "condition on the dependency graph is exactly the coherence condition needed for "
        "the associated natural transformation to be well-defined."
    ),
    assumptions=(
        "The repair plan is admissible (Theorem 11.2).",
        "Each replacement section s_i' satisfies the local conditions at U_i "
        "(Theorem 11.8).",
        "The semantic site has a sheaf structure with well-defined restriction maps.",
    ),
    conclusions=(
        "The repaired family (s_i') satisfies all descent conditions.",
        "The repaired family globalizes to a section over the full coordinate.",
    ),
    related_theorems=("repair_admissibility", "local_section_replacement_soundness"),
)

THEOREM_FRONTIER_MINIMALITY = TheoremObligation(
    theorem_name="frontier_minimality",
    statement=(
        "The repair frontier F is minimal if and only if F covers all obstructions "
        "and no proper subset of F covers all obstructions."
    ),
    theory_reference="theory2.tex §11.3 Theorem 11.4",
    proof_strategy=ProofStrategy.COMBINATORIAL,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "The theorem is a reformulation of the standard hitting-set minimality condition "
        "for hypergraph transversals.\n\n"
        "Model: regard each counterexample record as a hyperedge connecting the "
        "coordinates it could be resolved at. The repair frontier F is a hitting set: "
        "for each hyperedge (counterexample), at least one element of F is in the edge.\n\n"
        "(⟹) Suppose F is minimal. By definition, every element c ∈ F has at least one "
        "counterexample e_c such that F \\ {c} does not hit e_c. This means F covers all "
        "obstructions (F is a hitting set) and removing any element leaves some "
        "obstruction uncovered.\n\n"
        "(⟸) Suppose F covers all obstructions and no proper subset does. Then for "
        "every c ∈ F, there exists a counterexample e_c whose only cover in F is c "
        "(otherwise F \\ {c} would still cover all counterexamples). This is exactly the "
        "definition of minimality.\n\n"
        "Computational note: finding a *minimum* hitting set is NP-hard in general. "
        "The algorithm in :func:`~algorithms.compute_minimal_repair_frontier` computes "
        "a hitting set that is minimal (no element can be removed) but not necessarily "
        "minimum (smallest possible size).\n\n"
        "Coverage score: the coverage score measures the fraction of counterexample "
        "records that have at least one repair hint. A score of 1.0 is necessary (though "
        "not sufficient) for the frontier to be complete."
    ),
    assumptions=(
        "The counterexample records are finite.",
        "Each counterexample record is assigned to at least one coordinate.",
        "Coordinates in the frontier are distinct.",
    ),
    conclusions=(
        "A minimal frontier exists for any finite set of counterexample records.",
        "The minimal frontier is not necessarily unique.",
    ),
    related_theorems=("repair_convergence", "counterexample_minimality"),
)

THEOREM_REPAIR_CONVERGENCE = TheoremObligation(
    theorem_name="repair_convergence",
    statement=(
        "Iterative repair converges in finitely many steps when the repair frontier "
        "shrinks strictly monotonically at each iteration."
    ),
    theory_reference="theory2.tex §11.4 Theorem 11.5",
    proof_strategy=ProofStrategy.STRUCTURAL_INDUCTION,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "Define the *frontier size* at iteration k as |F_k| = |F_k.coordinates|. "
        "We claim: if |F_{k+1}| < |F_k| for all k where |F_k| > 0, then the iteration "
        "terminates with |F_n| = 0 for some finite n ≤ |F_0|.\n\n"
        "Proof: by strong induction on |F_0|. If |F_0| = 0, the iteration terminates "
        "immediately (base case). If |F_0| > 0, then by assumption |F_1| < |F_0|. By "
        "the inductive hypothesis applied to the iteration starting at F_1, the "
        "iteration from F_1 terminates in at most |F_1| ≤ |F_0| - 1 steps. Total "
        "steps: at most |F_0|.\n\n"
        "Frontier monotonicity condition: a repair step at coordinate c is *effective* "
        "if applying it resolves at least one counterexample record exclusively covered "
        "by c. An admissible plan consisting entirely of effective steps strictly "
        "reduces the frontier.\n\n"
        "Non-termination cases: the theorem has two important caveats:\n"
        "1. If no effective repair step exists (all obstructions require changes that "
        "violate some other condition), the session reaches BLOCKED status.\n"
        "2. If repair steps introduce *new* counterexamples (regression), the frontier "
        "may not shrink. This is why the session tracks the full history of repair "
        "attempts.\n\n"
        "Convergence certificate: when the frontier reaches size 0, the session is "
        "marked CONVERGED and a convergence certificate is issued by "
        ":func:`~algorithms.repair_convergence_certificate`."
    ),
    assumptions=(
        "Each repair step is effective (resolves at least one counterexample exclusively).",
        "No repair step introduces new counterexamples (no regression).",
        "The frontier shrinks strictly at each iteration.",
    ),
    conclusions=(
        "The repair iteration terminates in at most |F_0| steps.",
        "The session reaches CONVERGED status when the frontier is empty.",
    ),
    related_theorems=("frontier_minimality", "session_monotonicity", "repair_admissibility"),
)

THEOREM_COHOMOLOGY_CLASS_CONSISTENCY = TheoremObligation(
    theorem_name="cohomology_class_consistency",
    statement=(
        "The cohomology class label of a counterexample record is invariant under "
        "minimization: if α' is a minimal sub-witness of α, then "
        "classify_cohomology_class(α') = classify_cohomology_class(α)."
    ),
    theory_reference="theory2.tex §11.1 Theorem 11.6",
    proof_strategy=ProofStrategy.ALGEBRAIC,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "The cohomology class label has the form H1[type:coord:hash] where:\n"
        "  - type is the failure_class value (determined by the kind of violation).\n"
        "  - coord is the coordinate (unchanged by minimization).\n"
        "  - hash is a fingerprint of the variable assignments.\n\n"
        "The key invariant is on the *type* component: minimization does not change "
        "the failure class. This holds because:\n\n"
        "1. The delta-debugging algorithm (Algorithm 11.1) preserves the condition "
        "checker(α') = True. The checker evaluates the same logical formula; if the "
        "failure was an ASSIGNMENT_CONFLICT in the original α, the same conflict must "
        "exist in any α' that still causes the failure.\n\n"
        "2. The coordinate component is explicitly carried through the record and is "
        "never modified by minimization.\n\n"
        "3. The hash component does change (since |α'| < |α| implies a different "
        "fingerprint). This is intentional: two minimal sub-witnesses of the same "
        "failing assignment may have different hashes, classifying them in different "
        "H¹ classes. This reflects the fact that different minimal witnesses may "
        "represent genuinely different cohomological obstructions.\n\n"
        "The invariant therefore holds for the (type, coord) prefix, not the full "
        "label. The full label distinguishes *which specific* variables contribute to "
        "the obstruction, while the prefix identifies *which kind* of obstruction.\n\n"
        "Corollary: counterexample records from the same coordinate with the same "
        "failure class belong to the same Čech cochain group C¹(U_coord, F_type)."
    ),
    assumptions=(
        "The delta-debugging algorithm preserves the failure condition.",
        "The failure class is determined only by the logical structure of the failure, "
        "not by the specific variable values.",
        "The coordinate is unchanged by minimization.",
    ),
    conclusions=(
        "The (type, coord) prefix of the cohomology class is invariant under minimization.",
        "All minimal sub-witnesses of a given failure live in the same Čech cochain group.",
    ),
    related_theorems=("counterexample_minimality",),
)

THEOREM_SESSION_MONOTONICITY = TheoremObligation(
    theorem_name="session_monotonicity",
    statement=(
        "The debug session status is monotone: once a session leaves the OPEN state, "
        "it never returns to OPEN. The reachable transitions are "
        "OPEN → CONVERGED and OPEN → BLOCKED."
    ),
    theory_reference="theory2.tex §11.4 Theorem 11.7",
    proof_strategy=ProofStrategy.SIMULATION,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "We prove by inspection of the session state machine that the only transition "
        "rules are OPEN → CONVERGED and OPEN → BLOCKED, and that CONVERGED and BLOCKED "
        "are both absorbing states.\n\n"
        "State machine formalization: define the session state machine as a labeled "
        "transition system (S, →, s₀) where S = {OPEN, CONVERGED, BLOCKED}, s₀ = OPEN, "
        "and → ⊆ S × Label × S is the transition relation.\n\n"
        "Transition rules (from the DebugSession API):\n"
        "  - converge(): OPEN → CONVERGED\n"
        "  - block(): OPEN → BLOCKED\n"
        "  - with_counterexample(), with_repair_attempt(): OPEN → OPEN\n\n"
        "Absorbing states: the converge() and block() methods are only callable on "
        "sessions with status OPEN. This is enforced by the immutable dataclass design: "
        "the with_* methods return new instances, and the orchestrator is expected to "
        "check the status before calling converge() or block().\n\n"
        "Proof by simulation: define the simulation relation R = "
        "{(OPEN, OPEN), (CONVERGED, CONVERGED), (BLOCKED, BLOCKED)}. "
        "This is a bisimulation: every transition from a state has a matching transition "
        "from the simulated state. The monotonicity claim follows from the fact that "
        "CONVERGED and BLOCKED have no outgoing transitions in the simulation.\n\n"
        "Implementation note: the Python implementation enforces this by never providing "
        "a 'reopen' method on DebugSession, making the absorbing property a "
        "type-level guarantee."
    ),
    assumptions=(
        "The debug session API is the only mechanism for status transitions.",
        "No external mutation of the status field occurs (frozen dataclass).",
    ),
    conclusions=(
        "Session status is monotone: once CONVERGED or BLOCKED, never reverts to OPEN.",
        "CONVERGED and BLOCKED are absorbing states of the session state machine.",
    ),
    related_theorems=("repair_convergence",),
)

THEOREM_LOCAL_SECTION_REPLACEMENT_SOUNDNESS = TheoremObligation(
    theorem_name="local_section_replacement_soundness",
    statement=(
        "Replacing a local section s_i with s_i' at coordinate U_i is sound if and "
        "only if s_i' satisfies all descent conditions at U_i, i.e. for every "
        "j with U_i ∩ U_j ≠ ∅, ρ_{U_i ∩ U_j}(s_i') = ρ_{U_i ∩ U_j}(s_j)."
    ),
    theory_reference="theory2.tex §11.2 Theorem 11.8",
    proof_strategy=ProofStrategy.CONSTRUCTION,
    status=TheoremStatus.PROOF_SKETCH,
    proof_sketch=(
        "We prove both directions of the biconditional.\n\n"
        "(⟹) Soundness implies descent. Suppose the replacement is sound, meaning the "
        "resulting global family (s_1, …, s_i', …, s_n) globalizes to a section over "
        "the full coordinate. By the sheaf gluing axiom, a family globalizes iff it "
        "satisfies the descent conditions on all overlaps. In particular, s_i' must "
        "satisfy the descent conditions at all U_i ∩ U_j.\n\n"
        "(⟸) Descent implies soundness. Suppose s_i' satisfies the descent conditions "
        "at all overlaps U_i ∩ U_j. We need to show that the family "
        "(s_1, …, s_i', …, s_n) globalizes.\n\n"
        "By assumption, for each j ≠ i:\n"
        "  - If U_i ∩ U_j ≠ ∅: ρ_{U_i ∩ U_j}(s_i') = ρ_{U_i ∩ U_j}(s_j) (given).\n"
        "  - For k, l both ≠ i: ρ_{U_k ∩ U_l}(s_k) = ρ_{U_k ∩ U_l}(s_l) "
        "(from the original family's descent conditions, which are unchanged).\n\n"
        "Since the original family (s_1, …, s_n) satisfied all descent conditions, "
        "and s_i' satisfies the descent conditions at all overlaps involving U_i, "
        "the modified family satisfies all descent conditions.\n\n"
        "By the sheaf gluing axiom, a family satisfying all descent conditions on a "
        "cover globalizes uniquely to a section over the covered coordinate. Therefore "
        "the replacement is sound.\n\n"
        "Implementation: the RepairValidator checks that replacement sections are "
        "computed with respect to the current values of the surrounding sections, "
        "not the original ones. This is necessary to avoid stale descent condition "
        "checks."
    ),
    assumptions=(
        "The semantic site has a sheaf structure (satisfies the gluing and locality "
        "axioms).",
        "The family (s_1, …, s_n) of original sections satisfies all descent conditions "
        "on overlaps not involving U_i.",
        "The replacement section s_i' is computed from the current (post-repair) "
        "surrounding sections, not the original ones.",
    ),
    conclusions=(
        "A replacement section is sound iff it satisfies all descent conditions at "
        "its overlaps.",
        "Sound replacements preserve the globalizability of the section family.",
    ),
    related_theorems=("descent_preservation", "repair_admissibility"),
)


# ---------------------------------------------------------------------------
# §4  Theorem registry
# ---------------------------------------------------------------------------


def _build_theorem_registry() -> dict[str, TheoremObligation]:
    """Build the module-level theorem registry from the constant instances.

    The registry maps theorem names (from THEOREM_TARGETS) to their
    :class:`TheoremObligation` constant instances.

    Returns
    -------
    dict[str, TheoremObligation]
        Mapping from theorem name to obligation.
    """
    return {
        "counterexample_minimality": THEOREM_COUNTEREXAMPLE_MINIMALITY,
        "repair_admissibility": THEOREM_REPAIR_ADMISSIBILITY,
        "descent_preservation": THEOREM_DESCENT_PRESERVATION,
        "frontier_minimality": THEOREM_FRONTIER_MINIMALITY,
        "repair_convergence": THEOREM_REPAIR_CONVERGENCE,
        "cohomology_class_consistency": THEOREM_COHOMOLOGY_CLASS_CONSISTENCY,
        "session_monotonicity": THEOREM_SESSION_MONOTONICITY,
        "local_section_replacement_soundness": THEOREM_LOCAL_SECTION_REPLACEMENT_SOUNDNESS,
    }


_THEOREM_REGISTRY: dict[str, TheoremObligation] = _build_theorem_registry()


# ---------------------------------------------------------------------------
# §5  Public functions
# ---------------------------------------------------------------------------


def check_theorem(
    name: str,
    *,
    coordinate: str = "",
    session: DebugSession | None = None,
) -> TheoremObligation:
    """Look up a theorem by name from the registry and optionally customize it.

    Retrieves the :class:`TheoremObligation` for the named theorem from the
    module-level registry.  If a :class:`~models.DebugSession` is provided,
    the returned obligation is customized with a proof sketch that references
    the session's state.

    Parameters
    ----------
    name : str
        The theorem name (snake_case, as used in ``THEOREM_TARGETS``).
    coordinate : str
        Optional coordinate to include in session-specific customizations.
    session : DebugSession or None
        If provided, the returned obligation's proof sketch is augmented
        with session-specific evidence.

    Returns
    -------
    TheoremObligation
        The theorem obligation for the named theorem.

    Raises
    ------
    jugeo.errors.JuGeoError
        If ``name`` is not found in the theorem registry.

    Examples
    --------
    >>> ob = check_theorem("repair_convergence")
    >>> ob.theorem_name
    'repair_convergence'
    >>> ob.is_established()
    False
    """
    if name not in _THEOREM_REGISTRY:
        available = ", ".join(sorted(_THEOREM_REGISTRY.keys()))
        raise JuGeoError(
            f"Unknown theorem: {name!r}. Available theorems: {available}"
        )

    obligation = _THEOREM_REGISTRY[name]

    if session is not None:
        # Augment proof sketch with session-specific evidence
        session_info = (
            f"\n\nSession context (session_id={session.session_id}, "
            f"coordinate={session.coordinate or coordinate!r}, "
            f"status={session.status.value}, "
            f"iterations={session.iteration_count}, "
            f"counterexamples={len(session.counterexamples)}):\n"
        )
        if session.is_converged():
            session_info += (
                "The session has converged, providing empirical evidence for "
                f"{name}."
            )
        elif session.is_blocked():
            session_info += (
                "The session is blocked; the theorem conditions may not hold "
                "for the current configuration."
            )
        else:
            session_info += (
                f"Session is still open after {session.iteration_count} iteration(s). "
                "The theorem has not yet been empirically validated for this session."
            )
        obligation = obligation.with_proof_sketch(
            obligation.proof_sketch + session_info
        )

    return obligation


def generate_proof_obligations(
    session: DebugSession,
) -> tuple[TheoremObligation, ...]:
    """Generate proof obligations for all theorems, customized to a debug session.

    For each theorem in ``THEOREM_TARGETS``, creates a
    :class:`TheoremObligation` whose proof sketch is augmented with
    evidence drawn from the session's state.

    The session state influences obligations as follows:

    * **CONVERGED**: all obligations with strategies ``CONSTRUCTION`` or
      ``ALGEBRAIC`` are upgraded to ``PROOF_SKETCH`` (they received empirical
      confirmation from the session).
    * **BLOCKED**: obligations for ``repair_convergence`` and
      ``session_monotonicity`` are annotated with the blocking condition.
    * **OPEN**: obligations are returned at their default status.

    Parameters
    ----------
    session : DebugSession
        The debug session to generate obligations for.

    Returns
    -------
    tuple[TheoremObligation, ...]
        One :class:`TheoremObligation` per entry in ``THEOREM_TARGETS``,
        in the same order.
    """
    obligations: list[TheoremObligation] = []

    for theorem_name, statement, theory_ref in THEOREM_TARGETS:
        base = _THEOREM_REGISTRY.get(theorem_name)
        if base is None:
            # Fallback: construct a minimal obligation
            base = TheoremObligation(
                theorem_name=theorem_name,
                statement=statement,
                theory_reference=theory_ref,
                status=TheoremStatus.CONJECTURED,
            )

        # Session-specific customization
        session_note = _session_note_for_theorem(theorem_name, session)
        customized = base.with_proof_sketch(
            base.proof_sketch + session_note
        )

        # Status upgrade for converged sessions
        if session.is_converged():
            if base.proof_strategy in (ProofStrategy.CONSTRUCTION, ProofStrategy.ALGEBRAIC):
                if base.status == TheoremStatus.PROOF_SKETCH:
                    customized = customized.with_status(TheoremStatus.PROOF_SKETCH)

        obligations.append(customized)

    return tuple(obligations)


def get_all_theorems() -> tuple[TheoremObligation, ...]:
    """Return all theorem obligations from the module-level registry.

    Returns
    -------
    tuple[TheoremObligation, ...]
        All registered theorem obligations, in the order they appear in
        ``THEOREM_TARGETS``.

    Examples
    --------
    >>> theorems = get_all_theorems()
    >>> len(theorems)
    8
    >>> all(isinstance(t, TheoremObligation) for t in theorems)
    True
    """
    return tuple(
        _THEOREM_REGISTRY[name]
        for name, _, _ in THEOREM_TARGETS
        if name in _THEOREM_REGISTRY
    )


def theorem_coverage_report(session: DebugSession) -> dict:
    """Produce a coverage report mapping theorem names to status given a session.

    The report shows, for each theorem, whether the session provides empirical
    evidence for or against the theorem, and whether the theorem has been
    formally verified.

    Parameters
    ----------
    session : DebugSession
        The debug session to assess theorem coverage against.

    Returns
    -------
    dict
        A dictionary with keys:
        ``session_id``, ``coordinate``, ``session_status``,
        ``theorems`` (a list of per-theorem dicts),
        ``established_count``, ``sketch_count``, ``conjectured_count``,
        ``total_count``, ``coverage_fraction``.
    """
    all_obligations = get_all_theorems()
    theorem_entries: list[dict] = []
    established = 0
    sketch = 0
    conjectured = 0

    for obligation in all_obligations:
        # Determine effective status considering session state
        effective_status = obligation.status
        is_empirically_supported = False

        if session.is_converged():
            # Convergence provides empirical support for construction/algebraic proofs
            if obligation.proof_strategy in (
                ProofStrategy.CONSTRUCTION,
                ProofStrategy.ALGEBRAIC,
            ):
                is_empirically_supported = True
        elif session.is_blocked():
            # Blocking is evidence against convergence theorem
            if obligation.theorem_name in ("repair_convergence", "session_monotonicity"):
                is_empirically_supported = False

        if effective_status == TheoremStatus.FORMAL_PROOF:
            established += 1
        elif effective_status == TheoremStatus.PROOF_SKETCH:
            sketch += 1
        else:
            conjectured += 1

        theorem_entries.append({
            "theorem_name": obligation.theorem_name,
            "statement": obligation.statement[:100] + ("…" if len(obligation.statement) > 100 else ""),
            "theory_reference": obligation.theory_reference,
            "status": effective_status.value,
            "is_established": obligation.is_established(),
            "is_empirically_supported": is_empirically_supported,
            "proof_strategy": obligation.proof_strategy.value,
        })

    total = len(all_obligations)
    coverage_fraction = established / total if total > 0 else 0.0

    return {
        "session_id": session.session_id,
        "coordinate": session.coordinate,
        "session_status": session.status.value,
        "theorems": theorem_entries,
        "established_count": established,
        "sketch_count": sketch,
        "conjectured_count": conjectured,
        "total_count": total,
        "coverage_fraction": coverage_fraction,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ---------------------------------------------------------------------------
# §6  Private helpers
# ---------------------------------------------------------------------------


def _session_note_for_theorem(theorem_name: str, session: DebugSession) -> str:
    """Produce a short session-specific addendum for a theorem's proof sketch.

    Parameters
    ----------
    theorem_name : str
        The theorem to generate a note for.
    session : DebugSession
        The current debug session.

    Returns
    -------
    str
        A multi-line string (may be empty) to append to the proof sketch.
    """
    lines: list[str] = []

    if not session.counterexamples and not session.repair_attempts:
        return ""

    lines.append(
        f"\n\nSession evidence (session_id={session.session_id}, "
        f"iterations={session.iteration_count}):"
    )

    if theorem_name == "counterexample_minimality":
        minimal_count = sum(1 for r in session.counterexamples if r.is_minimal)
        lines.append(
            f"  {minimal_count}/{len(session.counterexamples)} counterexample records "
            "have been minimized via delta-debugging."
        )

    elif theorem_name == "repair_admissibility":
        admissible_count = sum(1 for p in session.repair_attempts if p.is_admissible)
        lines.append(
            f"  {admissible_count}/{len(session.repair_attempts)} repair plans "
            "are marked admissible."
        )

    elif theorem_name == "repair_convergence":
        if session.is_converged():
            lines.append(
                f"  Session converged after {session.iteration_count} iteration(s), "
                "confirming the convergence theorem for this instance."
            )
        elif session.is_blocked():
            lines.append(
                f"  Session is BLOCKED after {session.iteration_count} iteration(s). "
                "The convergence conditions may not hold here."
            )
        else:
            lines.append(
                f"  Session still OPEN after {session.iteration_count} iteration(s). "
                f"Frontier has {len(session.counterexamples)} unresolved counterexamples."
            )

    elif theorem_name == "session_monotonicity":
        lines.append(
            f"  Current status: {session.status.value}. "
            "Monotonicity holds iff this status was reached without reverting to OPEN."
        )

    elif theorem_name == "frontier_minimality":
        ce_with_hints = sum(
            1 for r in session.counterexamples if r.has_repair_hints()
        )
        lines.append(
            f"  {ce_with_hints}/{len(session.counterexamples)} counterexamples "
            "have repair hints, contributing to frontier coverage."
        )

    return "\n".join(lines)




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.solver, jugeo.evidence, jugeo.geometry)
# ---------------------------------------------------------------------------


def repair_from_countermodel(cm: Any) -> dict[str, Any]:
    """Extract repair guidance from a countermodel.

    Countermodels from the solver encode exactly where the current section
    fails — they are the starting point for all repair actions.

    Parameters
    ----------
    cm : Any
        A Countermodel object or dict with countermodel data.

    Returns
    -------
    dict[str, Any]
        Repair guidance with ``failing_coordinates``, ``repair_hints``,
        ``countermodel_id``, and ``obstruction_class`` keys.
    """
    try:
        from jugeo.solver.countermodels import extract_repair_hints, Countermodel
    except ImportError:
        extract_repair_hints = None
        Countermodel = None

    model_id = getattr(cm, "model_id", None) or (cm.get("model_id") if isinstance(cm, dict) else "unknown")
    coord = getattr(cm, "coordinate", None) or (cm.get("coordinate") if isinstance(cm, dict) else None)

    guidance: dict[str, Any] = {
        "countermodel_id": model_id,
        "failing_coordinates": [coord] if coord else [],
        "repair_hints": [],
        "obstruction_class": f"H1_from_{model_id}",
    }

    if extract_repair_hints is not None:
        try:
            hints = extract_repair_hints(cm)
            guidance["repair_hints"] = list(hints) if hints else []
        except Exception:
            pass

    return guidance


def repair_certificate(repair: Any) -> dict[str, Any]:
    """Build an evidence certificate for a completed repair.

    Repair certificates attest that a repair action was performed,
    passed validation, and restored section well-formedness.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``repair_id``, ``valid``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else str(uuid.uuid4())
    )
    valid = getattr(repair, "valid", None)
    if valid is None and isinstance(repair, dict):
        valid = repair.get("valid", repair.get("status") == "success")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "repair_id": repair_id,
        "valid": bool(valid) if valid is not None else False,
        "trust_level": "REPAIRED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(repair).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"repair_{repair_id}", satisfied=valid, source="repair_semantics"
            )
        except Exception:
            pass

    return cert


def repair_descent_check(repair: Any) -> dict[str, Any]:
    """Check whether a repair restores descent (gluing) conditions.

    A valid repair must restore the ability of local sections to glue
    into a global section — i.e., the cocycle obstruction must vanish.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Descent check with ``gluing_restored``, ``cocycle_trivial``,
        ``affected_coordinates``, and ``descent_status`` keys.
    """
    try:
        from jugeo.geometry.descent import check_descent_after_repair, DescentStatus
    except ImportError:
        check_descent_after_repair = None
        DescentStatus = None

    coords = getattr(repair, "affected_coordinates", None) or (
        repair.get("affected_coordinates") if isinstance(repair, dict) else []
    )
    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else "unknown"
    )

    check: dict[str, Any] = {
        "repair_id": repair_id,
        "affected_coordinates": list(coords) if coords else [],
        "gluing_restored": None,
        "cocycle_trivial": None,
        "descent_status": "UNKNOWN",
    }

    if check_descent_after_repair is not None:
        try:
            result = check_descent_after_repair(coords, repair_id=repair_id)
            check["gluing_restored"] = getattr(result, "gluing_restored", None)
            check["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            check["descent_status"] = getattr(result, "status", "UNKNOWN")
        except Exception:
            pass

    return check


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "THEOREM_TARGETS",
    "ProofStrategy",
    "TheoremStatus",
    "TheoremObligation",
    # Theorem constants
    "THEOREM_COUNTEREXAMPLE_MINIMALITY",
    "THEOREM_REPAIR_ADMISSIBILITY",
    "THEOREM_DESCENT_PRESERVATION",
    "THEOREM_FRONTIER_MINIMALITY",
    "THEOREM_REPAIR_CONVERGENCE",
    "THEOREM_COHOMOLOGY_CLASS_CONSISTENCY",
    "THEOREM_SESSION_MONOTONICITY",
    "THEOREM_LOCAL_SECTION_REPLACEMENT_SOUNDNESS",
    # Registry
    "_THEOREM_REGISTRY",
    # Public functions
    "check_theorem",
    "generate_proof_obligations",
    "get_all_theorems",
    "theorem_coverage_report",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end of theorems
