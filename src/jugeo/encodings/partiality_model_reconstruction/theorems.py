r"""theory2.tex Ch31 — Theorem Statements, Proof Sketches, and Z3 Encodings.

This module contains the formal theorem statements from Chapter 31, along
with proof sketches and Z3/SMT2 encodings that can be checked automatically.

Theorems covered:

1. :data:`THEOREM_TOTALITY_UNDER_RESTRICTION` — restricting to the domain predicate yields totality
2. :data:`THEOREM_EXCEPTION_PROPAGATION_MONOTONICITY` — exception propagation is monotone
3. :data:`THEOREM_ALGEBRAIC_SURFACE_FAITHFULNESS` — Z3 datatype encoding faithfully represents the surface
4. :data:`THEOREM_MODEL_RECONSTRUCTION_SOUNDNESS` — reconstruction produces consistent evidence
5. :data:`THEOREM_BRANCH_SENSITIVITY_CORRECTNESS` — branch sensitivity correctly identifies live branches

.. math::

   \\forall f : A \\rightharpoonup B.\\;
   \\forall x \\in \\mathrm{dom}(f).\\;
   \\hat{f}(x) = f(x)
   \\quad \\text{(Theorem 31.1)}
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
import dataclasses
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo subpackage imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Decoder, Z3Result
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    class Z3Session: pass  # type: ignore[misc]
    class Z3Formula: pass  # type: ignore[misc]
    class Z3Encoder: pass  # type: ignore[misc]
    class Z3Decoder: pass  # type: ignore[misc]
    class Z3Result: pass  # type: ignore[misc]

try:
    from jugeo.solver.reconstruction import ModelReconstructor as SolverModelReconstruction
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    class SolverModelReconstruction: pass  # type: ignore[misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, Judgment
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    class JudgmentTerm: pass  # type: ignore[misc]
    class Judgment: pass  # type: ignore[misc]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    class TrustAlgebra: pass  # type: ignore[misc]
    class TrustLevel: pass  # type: ignore[misc]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class VerificationStatus(str, Enum):
    """Verification status of a formal theorem.

    Progresses from UNVERIFIED towards FULLY_VERIFIED as machine-checked
    or human-reviewed evidence accumulates.  FALSIFIED indicates that a
    counterexample has been found.
    """

    UNVERIFIED = "unverified"
    SKETCH_ONLY = "sketch_only"
    PARTIALLY_VERIFIED = "partially_verified"
    FULLY_VERIFIED = "fully_verified"
    FALSIFIED = "falsified"


class TheoremKind(str, Enum):
    """Classification of theorem by its logical role.

    - SOUNDNESS       : the procedure / encoding does not introduce spurious results
    - COMPLETENESS    : the procedure captures all intended results
    - CORRECTNESS     : the procedure produces exactly the right results
    - MONOTONICITY    : the procedure respects some ordering
    - FAITHFULNESS    : an encoding is isomorphic to what it represents
    - CHARACTERIZATION: a precise characterisation of a concept or operation
    """

    SOUNDNESS = "soundness"
    COMPLETENESS = "completeness"
    CORRECTNESS = "correctness"
    MONOTONICITY = "monotonicity"
    FAITHFULNESS = "faithfulness"
    CHARACTERIZATION = "characterization"


# ---------------------------------------------------------------------------
# Theorem dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Theorem:
    """An immutable record of a formal theorem from theory2.tex Ch31.

    Each theorem is self-contained: it carries the formal statement, a
    human-readable proof sketch, and an SMTLIB2 encoding that can be
    passed directly to Z3 for automated verification.

    Attributes
    ----------
    theorem_id:
        Unique stable identifier (slug) for this theorem.  Used as a
        foreign key in :attr:`TheoremRegistry.theorems` and in dependency
        chains.
    name:
        Short human-readable name.
    kind:
        The logical role of the theorem (see :class:`TheoremKind`).
    statement:
        The full formal statement of the theorem in mathematical English.
    proof_sketch:
        An informal but structured proof sketch.
    z3_encoding:
        An SMTLIB2 string that encodes the theorem for automated checking.
    verification_status:
        Current verification status.
    chapter_ref:
        Reference to the specific section in theory2.tex (e.g. "Ch31 §31.1").
    dependencies:
        Tuple of theorem_ids that this theorem logically depends on.
    counterexample:
        If the theorem has been FALSIFIED, a description of the counterexample.
    """

    theorem_id: str
    name: str
    kind: TheoremKind
    statement: str
    proof_sketch: str
    z3_encoding: str
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    chapter_ref: str = "Ch31"
    dependencies: tuple[str, ...] = ()
    counterexample: str = ""

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_verified(self) -> bool:
        """Return True iff the theorem is fully verified."""
        return self.verification_status == VerificationStatus.FULLY_VERIFIED

    def is_falsified(self) -> bool:
        """Return True iff the theorem has been falsified."""
        return self.verification_status == VerificationStatus.FALSIFIED

    def has_proof_sketch(self) -> bool:
        """Return True iff a non-empty proof sketch has been provided."""
        return len(self.proof_sketch.strip()) > 0

    # ------------------------------------------------------------------
    # Serialisation and derived views
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        All enum values are converted to their string representations.
        The ``dependencies`` tuple is converted to a list for JSON
        compatibility.

        Returns
        -------
        dict[str, Any]
            A plain dictionary representation of this theorem.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "kind": self.kind.value,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "z3_encoding": self.z3_encoding,
            "verification_status": self.verification_status.value,
            "chapter_ref": self.chapter_ref,
            "dependencies": list(self.dependencies),
            "counterexample": self.counterexample,
            "is_verified": self.is_verified(),
            "is_falsified": self.is_falsified(),
            "has_proof_sketch": self.has_proof_sketch(),
            "statement_length": len(self.statement),
            "z3_encoding_length": len(self.z3_encoding),
        }

    def with_status(self, status: VerificationStatus) -> Theorem:
        """Return a copy of this theorem with an updated verification status.

        Parameters
        ----------
        status:
            The new verification status to assign.

        Returns
        -------
        Theorem
            A new immutable :class:`Theorem` with the updated status.
        """
        return dataclasses.replace(self, verification_status=status)

    def with_z3_encoding(self, enc: str) -> Theorem:
        """Return a copy of this theorem with an updated Z3 encoding.

        Parameters
        ----------
        enc:
            The new SMTLIB2 encoding string.

        Returns
        -------
        Theorem
            A new immutable :class:`Theorem` with the updated encoding.
        """
        return dataclasses.replace(self, z3_encoding=enc)

    def summary_line(self) -> str:
        """Return a compact single-line summary of this theorem.

        The line includes the theorem kind, name, verification status, and
        the first 80 characters of the statement.

        Returns
        -------
        str
            A formatted summary string.
        """
        truncated_statement = self.statement[:80].replace("\n", " ")
        return (
            f"[{self.kind.value}] {self.name} "
            f"({self.verification_status.value}) — {truncated_statement}..."
        )

    def __str__(self) -> str:
        return self.summary_line()


# ---------------------------------------------------------------------------
# TheoremRegistry dataclass
# ---------------------------------------------------------------------------


@dataclass
class TheoremRegistry:
    """A mutable registry of :class:`Theorem` instances.

    Provides lookup, filtering, and serialisation methods for a collection
    of Ch31 theorems.

    Attributes
    ----------
    theorems:
        Dict mapping theorem_id to :class:`Theorem`.
    registry_id:
        Unique identifier for this registry instance.
    """

    theorems: dict[str, Theorem] = field(default_factory=dict)
    registry_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, theorem: Theorem) -> None:
        """Register a theorem.

        Parameters
        ----------
        theorem:
            The :class:`Theorem` to register.
        """
        self.theorems[theorem.theorem_id] = theorem

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, theorem_id: str) -> Theorem | None:
        """Look up a theorem by its ID.

        Parameters
        ----------
        theorem_id:
            The stable theorem ID to look up.

        Returns
        -------
        Theorem | None
            The theorem if found, else None.
        """
        return self.theorems.get(theorem_id)

    def by_kind(self, kind: TheoremKind) -> list[Theorem]:
        """Return all theorems of the given kind.

        Parameters
        ----------
        kind:
            The :class:`TheoremKind` to filter on.

        Returns
        -------
        list[Theorem]
            Theorems matching the given kind.
        """
        return [t for t in self.theorems.values() if t.kind == kind]

    def by_status(self, status: VerificationStatus) -> list[Theorem]:
        """Return all theorems with the given verification status.

        Parameters
        ----------
        status:
            The :class:`VerificationStatus` to filter on.

        Returns
        -------
        list[Theorem]
            Theorems matching the given status.
        """
        return [t for t in self.theorems.values() if t.verification_status == status]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def verified_count(self) -> int:
        """Return the number of fully-verified theorems."""
        return len(self.by_status(VerificationStatus.FULLY_VERIFIED))

    def unverified_count(self) -> int:
        """Return the number of unverified theorems."""
        return len(self.by_status(VerificationStatus.UNVERIFIED))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise the registry to a JSON string.

        Returns
        -------
        str
            A pretty-printed JSON string representing all registered theorems.
        """
        return json.dumps(
            {k: v.to_dict() for k, v in self.theorems.items()},
            indent=2,
        )

    def summary(self) -> dict[str, Any]:
        """Return a summary dictionary for this registry.

        Returns
        -------
        dict[str, Any]
            Keys: registry_id, total, verified, unverified, by_kind.
        """
        by_kind_counts: dict[str, int] = {}
        for kind in TheoremKind:
            by_kind_counts[kind.value] = len(self.by_kind(kind))

        return {
            "registry_id": self.registry_id,
            "total": len(self.theorems),
            "verified": self.verified_count(),
            "unverified": self.unverified_count(),
            "sketch_only": len(self.by_status(VerificationStatus.SKETCH_ONLY)),
            "partially_verified": len(self.by_status(VerificationStatus.PARTIALLY_VERIFIED)),
            "falsified": len(self.by_status(VerificationStatus.FALSIFIED)),
            "by_kind": by_kind_counts,
        }

    def __repr__(self) -> str:
        return (
            f"TheoremRegistry("
            f"id={self.registry_id[:8]}..., "
            f"theorems={len(self.theorems)}, "
            f"verified={self.verified_count()})"
        )


# ---------------------------------------------------------------------------
# Theorem constants — Ch31
# ---------------------------------------------------------------------------

THEOREM_TOTALITY_UNDER_RESTRICTION = Theorem(
    theorem_id="thm-31-1-totality-restriction",
    name="Totality Under Domain Restriction",
    kind=TheoremKind.SOUNDNESS,
    statement="""Theorem 31.1 (Totality Under Domain Restriction).
Let f : A -> B be a total function and let dom_f : A -> Bool be a domain
predicate.  Define the partial function f|_{dom_f} by restricting f to
the subdomain where dom_f holds.  Then the totalization f_hat defined by:

    f_hat(x) = if dom_f(x) then f(x) else default

is total on A and agrees with f on the restricted domain:

    forall x : A. dom_f(x) => f_hat(x) = f(x)

Moreover, f_hat is the unique minimal total extension of f|_{dom_f} with
respect to the pointwise ordering on total functions extending partial ones,
provided that 'default' is the least element of B.""",
    proof_sketch="""Proof sketch (Theorem 31.1):

(1) Totality: For any x : A, the ite expression evaluates to either f(x)
    (when dom_f(x) holds) or default (when not dom_f(x)).  Both branches
    are well-defined total expressions, so f_hat(x) is defined for all x.

(2) Agreement on domain: If dom_f(x) holds, then ite(dom_f(x), f(x), default)
    = f(x) by the ite reduction rule in the underlying theory.

(3) Minimality: Any other total extension g with g(x) = f(x) for dom_f(x)
    must have g(x) >= default for all x outside the domain.  Since default
    is the least element, f_hat <= g in the pointwise order.

The proof uses only propositional reasoning and the semantics of ite in SMT2,
making it amenable to automated verification via Z3.""",
    z3_encoding="""
; Theorem 31.1: Totality Under Domain Restriction
; -------------------------------------------------
; We encode the theorem for a concrete instance: f : Int -> Int
; with domain predicate dom_f(x) := x >= 0

(declare-sort A 0)
(declare-fun f (A) Int)
(declare-fun dom_f (A) Bool)
(declare-fun default_val () Int)

; Define totalized function
(define-fun f_hat ((x A)) Int
  (ite (dom_f x) (f x) default_val))

; Theorem statement: f_hat agrees with f on the domain
(assert (forall ((x A))
  (=> (dom_f x) (= (f_hat x) (f x)))))

; Totality: f_hat is defined everywhere (trivially true by construction)
; but we can check agreement:
(push)
(declare-const x_test A)
(assert (dom_f x_test))
(assert (not (= (f_hat x_test) (f x_test))))
(check-sat)
; Expected: unsat (the negation of the theorem is unsatisfiable)
(pop)
""",
    verification_status=VerificationStatus.PARTIALLY_VERIFIED,
    chapter_ref="Ch31 §31.1",
    dependencies=(),
)

# ---------------------------------------------------------------------------

THEOREM_EXCEPTION_PROPAGATION_MONOTONICITY = Theorem(
    theorem_id="thm-31-2-exception-monotonicity",
    name="Exception Propagation Monotonicity",
    kind=TheoremKind.MONOTONICITY,
    statement="""Theorem 31.2 (Exception Propagation Monotonicity).

Let E be a set of exception sorts and let propagate : Result * Cont -> Result
be the strict propagation combinator defined by:

    propagate(Err(e), k) = Err(e)
    propagate(Ok(v),  k) = k(v)

Then propagate is monotone with respect to the information ordering on
Result = Ok(B) | Err(E):

    r1 <= r2  =>  propagate(r1, k) <= propagate(r2, k)

where the ordering is defined by:  bottom <= Err(e) <= Ok(v) for all e, v.

Furthermore, for any chain r0 <= r1 <= ... in Result, the sequence
propagate(rn, k) is also non-decreasing, establishing continuity of
the propagation combinator.""",
    proof_sketch="""Proof sketch (Theorem 31.2):

Case analysis on the information ordering:

(a) r1 = bottom:  propagate(bottom, k) = bottom <= propagate(r2, k) for any r2.
    (bottom is the least element, so this case is trivial.)

(b) r1 = Err(e1), r2 = Err(e2):  The ordering requires e1 = e2 (errors are
    incomparable unless equal in our flat ordering).  Then both sides evaluate
    to Err(e1) = Err(e2).

(c) r1 = Err(e), r2 = Ok(v):  propagate(Err(e), k) = Err(e).
    propagate(Ok(v), k) = k(v).
    Since Err(e) <= Ok(v) in the ordering, and Err(e) is an exception value,
    we need Err(e) <= k(v).  By the ordering definition, all Err values are
    below all Ok values, so this holds.

(d) r1 = Ok(v1), r2 = Ok(v2):  Then propagate(Ok(vi), k) = k(vi).
    This reduces to monotonicity of k.

The proof is by structural induction on the Result sum type.""",
    z3_encoding="""
; Theorem 31.2: Exception Propagation Monotonicity
; -------------------------------------------------
(declare-datatypes ((Result 1))
  ((par (V E)
    ((Ok (ok-val V))
     (Err (err-val E))
     (Bottom)))))

; Specialise to Result Int String for concreteness
(define-sort IntResult () (Result Int String))

; Information ordering: Bottom <= Err <= Ok
(define-fun result-leq ((r1 IntResult) (r2 IntResult)) Bool
  (or ((_ is Bottom) r1)
      (and ((_ is Err) r1) ((_ is Err) r2)
           (= (err-val r1) (err-val r2)))
      (and ((_ is Err) r1) ((_ is Ok) r2))
      (and ((_ is Ok) r1) ((_ is Ok) r2)
           (= (ok-val r1) (ok-val r2)))))

; Propagation combinator (encoded as a relation since Z3 does not have HOF)
; We check: r1 <= r2 /\\ r1 = Err(e) => r2 = Err(e) or r2 = Ok(v)
(push)
(declare-const r1 IntResult)
(declare-const r2 IntResult)
(assert (result-leq r1 r2))
(assert ((_ is Err) r1))
(assert (not (or ((_ is Err) r2) ((_ is Ok) r2))))
(check-sat)
; Expected: unsat
(pop)
""",
    verification_status=VerificationStatus.SKETCH_ONLY,
    chapter_ref="Ch31 §31.2",
    dependencies=("thm-31-1-totality-restriction",),
)

# ---------------------------------------------------------------------------

THEOREM_ALGEBRAIC_SURFACE_FAITHFULNESS = Theorem(
    theorem_id="thm-31-3-surface-faithfulness",
    name="Algebraic Surface Faithfulness",
    kind=TheoremKind.FAITHFULNESS,
    statement="""Theorem 31.3 (Algebraic Surface Faithfulness).

The Z3 algebraic datatype encoding of a surface S = (C, R, A) — consisting
of constructors C, recognizers R, and accessors A — faithfully represents the
algebraic structure in the following sense:

(1) Constructor injectivity: for all constructors c_i != c_j in C and
    arguments a, b:  c_i(a) != c_j(b)  (different constructors give different values).

(2) Recognizer soundness: for all constructors c_i and values v:
    is-c_i(c_i(a)) = true  and  is-c_i(c_j(a)) = false for i != j.

(3) Accessor correctness: for all constructors c_i with field accessor
    pi_j and argument a:  pi_j(c_i(a)) = a_j  where a = (a_1, ..., a_k).

These three properties together constitute the universal property of the
initial algebra for the functor defining the algebraic datatype.""",
    proof_sketch="""Proof sketch (Theorem 31.3):

Properties (1)-(3) are guaranteed by the Z3 algebraic datatype semantics,
which implements the initial algebra construction.

(1) Constructor injectivity follows from the free algebra property: in the
    initial algebra, distinct constructor applications are always distinct.
    Z3 enforces this via the testers and the algebraic datatype axioms.

(2) Recognizer soundness: Z3 generates the axiom schema
    (is-c_i v) <=> (exists a. v = c_i(a)) for each constructor c_i.
    These axioms are universally quantified and added to the theory.

(3) Accessor correctness: Z3 generates the axiom
    (= (pi_j (c_i a_1 ... a_k)) a_j)
    for each accessor pi_j of constructor c_i.  This is the standard
    'projection after injection' identity from algebraic datatypes.

The faithfulness theorem thus reduces to soundness of the Z3 algebraic
datatype theory, which is established by the model-theoretic semantics
of SMTLIB2 algebraic datatypes.""",
    z3_encoding="""
; Theorem 31.3: Algebraic Surface Faithfulness
; --------------------------------------------
; Concrete instance: a simple Tree datatype
(declare-datatypes ((Tree 0))
  (((Leaf)
    (Node (node-left Tree) (node-val Int) (node-right Tree)))))

; (1) Constructor injectivity
(push)
(declare-const t1 Tree)
(declare-const t2 Tree)
(assert ((_ is Node) t1))
(assert ((_ is Leaf) t2))
(assert (= t1 t2))
(check-sat)
; Expected: unsat (Leaf and Node are distinct)
(pop)

; (2) Recognizer soundness
(push)
(declare-const n Int)
(declare-const l Tree) (declare-const r Tree)
(assert (not ((_ is Node) (Node l n r))))
(check-sat)
; Expected: unsat
(pop)

; (3) Accessor correctness
(push)
(declare-const v Int)
(declare-const left Tree) (declare-const right Tree)
(assert (not (= (node-val (Node left v right)) v)))
(check-sat)
; Expected: unsat
(pop)
""",
    verification_status=VerificationStatus.PARTIALLY_VERIFIED,
    chapter_ref="Ch31 §31.3",
    dependencies=(),
)

# ---------------------------------------------------------------------------

THEOREM_MODEL_RECONSTRUCTION_SOUNDNESS = Theorem(
    theorem_id="thm-31-4-reconstruction-soundness",
    name="Model Reconstruction Soundness",
    kind=TheoremKind.SOUNDNESS,
    statement="""Theorem 31.4 (Model Reconstruction Soundness).

Let Q be a Z3 query, M a satisfying model for Q, and R = reconstruct(M, Q)
the evidence produced by the reconstruction procedure.  Then:

(1) Completeness of extraction: for every variable x declared in Q,
    R contains an assignment R(x) such that M |= (x = R(x)).

(2) Consistency: the assignments in R are mutually consistent under Q,
    i.e., M |= bigand_{x} (x = R(x)).

(3) Trust preservation: if M is annotated with trust level t, then
    all assignments in R inherit trust level <= t.

(4) Provenance tracking: every assignment in R carries a provenance
    record tracing it to the corresponding Z3 model entry.""",
    proof_sketch="""Proof sketch (Theorem 31.4):

(1) Extraction completeness: The extraction phase iterates over all
    declared variables in Q (tracked in the encoding metadata) and
    looks up their values in M.  Since M is a total function on the
    declared variables (Z3 models are total), every variable gets an
    assignment.

(2) Consistency: Z3 models are, by definition, satisfying assignments.
    The reconstruction procedure copies M faithfully without modification,
    so the reconstructed assignments satisfy Q.

(3) Trust preservation: The trust annotation phase applies a ceiling
    function to all assignments.  If the ceiling is t, then all
    assignments get trust level min(inferred_trust, t) <= t.

(4) Provenance tracking: Each extraction step records (variable, value,
    source_model_id, extraction_timestamp) in the provenance list.
    The reconstruction procedure ensures this record is complete.""",
    z3_encoding="""
; Theorem 31.4: Model Reconstruction Soundness
; ---------------------------------------------
; We encode a small instance: query Q asserts x > 0 and y = x + 1
; The reconstruction should produce x=1, y=2 (or similar satisfying assignment)

(push)
(declare-const x Int)
(declare-const y Int)
(assert (> x 0))
(assert (= y (+ x 1)))
(check-sat)
; Expected: sat
; Model: x -> 1, y -> 2 (or any x > 0 with y = x+1)
; The reconstruction procedure would extract:
;   R(x) = <whatever Z3 assigns to x>
;   R(y) = <whatever Z3 assigns to y>
; Soundness says R(x) > 0 and R(y) = R(x) + 1
(get-value (x y))
(pop)
""",
    verification_status=VerificationStatus.SKETCH_ONLY,
    chapter_ref="Ch31 §31.4",
    dependencies=("thm-31-1-totality-restriction", "thm-31-3-surface-faithfulness"),
)

# ---------------------------------------------------------------------------

THEOREM_BRANCH_SENSITIVITY_CORRECTNESS = Theorem(
    theorem_id="thm-31-5-branch-sensitivity",
    name="Branch Sensitivity Correctness",
    kind=TheoremKind.CORRECTNESS,
    statement="""Theorem 31.5 (Branch Sensitivity Correctness).

Let P be a program with branch conditions B = {b_1, ..., b_n} and M a
satisfying model for a query encoding P.  Define the *live branches* under M as:

    Live(M) = {i : M |= b_i}

The branch sensitivity analysis BranchSens(M, B) produces a set
active_branches such that:

    active_branches = Live(M)

That is, the analysis correctly identifies exactly those branches whose
conditions are satisfied in the model M.

Corollary: For any two models M1, M2 with M1 |= b_i <=> M2 |= b_i for all i,
the branch sensitivity analysis produces the same result:
BranchSens(M1, B) = BranchSens(M2, B).""",
    proof_sketch="""Proof sketch (Theorem 31.5):

By definition, BranchSens(M, B) checks, for each condition b_i:
  - if b_i is a variable in M, return M(b_i) as a boolean
  - if b_i is a compound condition, evaluate under M

Since M is a complete model (total function on declared variables) and b_i
is a well-formed formula over those variables, M(b_i) is well-defined and
equals True iff M |= b_i.

Therefore active_branches = {i : BranchSens evaluates b_i to True under M}
                           = {i : M |= b_i}
                           = Live(M).

The corollary follows immediately since the analysis is deterministic and
depends only on the model values of the branch conditions.""",
    z3_encoding="""
; Theorem 31.5: Branch Sensitivity Correctness
; ---------------------------------------------
; Encode a small example: two branch conditions b1, b2
; and check that the model correctly identifies which are live

(declare-const x Int)
(declare-const b1 Bool)
(declare-const b2 Bool)

; Branch conditions
(assert (= b1 (> x 0)))   ; b1: x > 0
(assert (= b2 (< x 10)))  ; b2: x < 10

; Find a model
(push)
(assert (and b1 b2))  ; Both branches live
(check-sat)
; Expected: sat, with x in (0, 10)
; Branch sensitivity should report active_branches = {0, 1}
(get-value (x b1 b2))
(pop)

(push)
(assert (and b1 (not b2)))  ; Only b1 live
(check-sat)
; Expected: sat, with x >= 10
; Branch sensitivity should report active_branches = {0}
(get-value (x b1 b2))
(pop)
""",
    verification_status=VerificationStatus.UNVERIFIED,
    chapter_ref="Ch31 §31.5",
    dependencies=("thm-31-4-reconstruction-soundness",),
)

# ---------------------------------------------------------------------------
# Module-level theorem registry
# ---------------------------------------------------------------------------

THEOREM_REGISTRY = TheoremRegistry()
THEOREM_REGISTRY.register(THEOREM_TOTALITY_UNDER_RESTRICTION)
THEOREM_REGISTRY.register(THEOREM_EXCEPTION_PROPAGATION_MONOTONICITY)
THEOREM_REGISTRY.register(THEOREM_ALGEBRAIC_SURFACE_FAITHFULNESS)
THEOREM_REGISTRY.register(THEOREM_MODEL_RECONSTRUCTION_SOUNDNESS)
THEOREM_REGISTRY.register(THEOREM_BRANCH_SENSITIVITY_CORRECTNESS)

# ---------------------------------------------------------------------------
# CopilotTheoremAssist
# ---------------------------------------------------------------------------


@dataclass
class CopilotTheoremAssist:
    """Copilot integration hook for theorem-level assistance in Ch31.

    Provides proof strategy suggestions, Z3 encoding checks, and
    explanation generation for developers working with Ch31 theorems.

    Attributes
    ----------
    assist_id:
        Unique identifier for this assist instance.
    """

    assist_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Proof strategy suggestions
    # ------------------------------------------------------------------

    def suggest_proof_approach(self, theorem: Theorem) -> str:
        """Suggest an appropriate proof strategy based on the theorem's kind.

        Strategies are mapped from :class:`TheoremKind` to standard
        proof techniques used in Ch31:

        - SOUNDNESS      -> model-theoretic argument
        - MONOTONICITY   -> structural induction on the ordering
        - FAITHFULNESS   -> bisimulation / isomorphism
        - CORRECTNESS    -> case analysis on all execution paths
        - COMPLETENESS   -> fixed-point / Kleene iteration argument
        - CHARACTERIZATION -> definition unfolding and normalization

        Parameters
        ----------
        theorem:
            The :class:`Theorem` for which a strategy is sought.

        Returns
        -------
        str
            A multi-line string describing the suggested proof approach.
        """
        kind = theorem.kind

        if kind == TheoremKind.SOUNDNESS:
            approach_name = "model-theoretic argument"
            details = (
                "Build a concrete model satisfying the antecedents and show the "
                "conclusion holds by interpretation.  For SMT-encodable theorems, "
                "negate the conclusion and check for unsatisfiability via Z3."
            )
        elif kind == TheoremKind.MONOTONICITY:
            approach_name = "structural induction on the ordering"
            details = (
                "Identify the partial order and induct on the structure of elements.  "
                "For each constructor case, verify the monotonicity condition holds "
                "by case splitting on the comparison of the two operands."
            )
        elif kind == TheoremKind.FAITHFULNESS:
            approach_name = "bisimulation / initial algebra isomorphism"
            details = (
                "Define a bisimulation relation between the abstract and concrete "
                "representations, then show it is an isomorphism.  For Z3 algebraic "
                "datatypes, appeal to the initial algebra property."
            )
        elif kind == TheoremKind.CORRECTNESS:
            approach_name = "case analysis on execution paths"
            details = (
                "Enumerate all cases (constructor arms, branch conditions) and verify "
                "correctness for each.  In Z3, assert the negation and check-sat "
                "for each case separately using push/pop."
            )
        elif kind == TheoremKind.COMPLETENESS:
            approach_name = "fixed-point / Kleene iteration argument"
            details = (
                "Show that the procedure converges to the least fixed point of a "
                "monotone operator.  By Kleene's theorem, the fixed point is reached "
                "in at most omega steps; for finite domains, termination is immediate."
            )
        elif kind == TheoremKind.CHARACTERIZATION:
            approach_name = "definition unfolding and normalization"
            details = (
                "Unfold all definitions to a normal form, then verify the "
                "characterization holds by equational reasoning.  Apply rewrite rules "
                "until both sides reduce to the same normal form."
            )
        else:
            approach_name = "general proof search"
            details = (
                "No specialized strategy is known for this theorem kind.  "
                "Consider a direct proof, contradiction, or interactive theorem prover."
            )

        return (
            f"Suggested proof approach for '{theorem.name}' ({kind.value}):\n"
            f"Strategy: {approach_name}\n\n"
            f"{details}\n\n"
            f"Dependencies to resolve first: {list(theorem.dependencies) or ['none']}\n"
            f"Chapter reference: {theorem.chapter_ref}"
        )

    # ------------------------------------------------------------------
    # Z3 encoding checks
    # ------------------------------------------------------------------

    def check_z3_encoding(self, encoding: str) -> dict[str, Any]:
        """Perform syntactic checks on an SMTLIB2 encoding string.

        Checks performed:
        - Presence of ``(check-sat)``
        - Presence of at least one ``(assert ...)``
        - Balanced parentheses
        - Line count and character count
        - Presence of ``(push)`` / ``(pop)`` pairs

        Parameters
        ----------
        encoding:
            The SMTLIB2 encoding string to check.

        Returns
        -------
        dict[str, Any]
            A dictionary of syntactic check results.
        """
        open_count = encoding.count("(")
        close_count = encoding.count(")")
        balanced = open_count == close_count

        has_check_sat = "(check-sat)" in encoding
        has_assert = "(assert" in encoding
        has_push = "(push)" in encoding
        has_pop = "(pop)" in encoding
        push_pop_balanced = encoding.count("(push)") == encoding.count("(pop)")

        lines = encoding.splitlines()
        non_comment_lines = [ln for ln in lines if not ln.strip().startswith(";")]
        comment_lines = [ln for ln in lines if ln.strip().startswith(";")]

        # Count the number of check-sat occurrences
        check_sat_count = encoding.count("(check-sat)")

        # Identify declared constants and functions via simple scan
        declares = [
            ln.strip()
            for ln in lines
            if "(declare-" in ln or "(define-" in ln
        ]

        issues: list[str] = []
        if not balanced:
            issues.append(
                f"Unbalanced parentheses: {open_count} '(' vs {close_count} ')'"
            )
        if not has_check_sat:
            issues.append("No (check-sat) found — the encoding may not be checkable")
        if not has_assert:
            issues.append("No (assert ...) found — the encoding has no constraints")
        if has_push and not push_pop_balanced:
            issues.append(
                f"Unbalanced push/pop: {encoding.count('(push)')} push vs "
                f"{encoding.count('(pop)')} pop"
            )

        return {
            "has_check_sat": has_check_sat,
            "balanced_parens": balanced,
            "open_parens": open_count,
            "close_parens": close_count,
            "length": len(encoding),
            "lines": len(lines),
            "non_comment_lines": len(non_comment_lines),
            "comment_lines": len(comment_lines),
            "has_assert": has_assert,
            "has_push_pop": has_push and has_pop,
            "push_pop_balanced": push_pop_balanced,
            "check_sat_count": check_sat_count,
            "declare_count": len(declares),
            "issues": issues,
            "is_well_formed": len(issues) == 0,
        }

    # ------------------------------------------------------------------
    # Explanation
    # ------------------------------------------------------------------

    def explain_theorem(self, theorem: Theorem) -> str:
        """Return a formatted human-readable explanation of a theorem.

        Combines the theorem name, kind, verification status, and the
        first 200 characters of the statement for a quick overview.

        Parameters
        ----------
        theorem:
            The :class:`Theorem` to explain.

        Returns
        -------
        str
            A formatted explanation string.
        """
        truncated = theorem.statement[:200].replace("\n", " ").strip()
        dep_list = ", ".join(theorem.dependencies) if theorem.dependencies else "none"

        lines = [
            f"Theorem: {theorem.name}",
            f"ID:      {theorem.theorem_id}",
            f"Kind:    {theorem.kind.value}",
            f"Status:  {theorem.verification_status.value}",
            f"Ref:     {theorem.chapter_ref}",
            f"Deps:    {dep_list}",
            f"",
            f"Statement (excerpt):",
            f"  {truncated}{'...' if len(theorem.statement) > 200 else ''}",
        ]

        if theorem.is_falsified() and theorem.counterexample:
            lines.append(f"")
            lines.append(f"Counterexample: {theorem.counterexample}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Counterexample suggestions
    # ------------------------------------------------------------------

    def suggest_counterexample(self, theorem: Theorem) -> str:
        """Suggest a heuristic counterexample approach for an unverified theorem.

        Returns a problem-specific hint based on the theorem's kind.
        Only makes suggestions for theorems that are not yet falsified.

        Parameters
        ----------
        theorem:
            The :class:`Theorem` to suggest a counterexample for.

        Returns
        -------
        str
            A string suggesting how to search for a counterexample.
        """
        if theorem.is_falsified():
            return (
                f"Theorem '{theorem.name}' is already falsified.  "
                f"Counterexample: {theorem.counterexample or '(not recorded)'}"
            )

        if theorem.is_verified():
            return (
                f"Theorem '{theorem.name}' is fully verified — no counterexample exists."
            )

        kind = theorem.kind

        if kind == TheoremKind.SOUNDNESS:
            hint = (
                "Consider a partial function with an empty domain (dom_f = false everywhere).  "
                "Check whether the totalization still satisfies the agreement property.  "
                "In Z3: (assert (forall ((x A)) (not (dom_f x)))) then check the main assertion."
            )
        elif kind == TheoremKind.MONOTONICITY:
            hint = (
                "Look for elements r1 <= r2 in the ordering where the propagation reverses.  "
                "Try r1 = Err('e') and r2 = Ok(0), and verify that propagate(r1,k) <= propagate(r2,k).  "
                "A counterexample would require propagate(r1,k) to be strictly greater."
            )
        elif kind == TheoremKind.FAITHFULNESS:
            hint = (
                "Attempt to construct two distinct algebraic values that Z3 maps to the same model element.  "
                "For tree types, try Node(Leaf, 0, Leaf) = Node(Leaf, 1, Leaf) — this should be unsat."
            )
        elif kind == TheoremKind.CORRECTNESS:
            hint = (
                "Find a model M and branch condition b_i such that M |= b_i but BranchSens reports b_i inactive.  "
                "This could happen if the branch condition variable is not directly present in the model dict."
            )
        elif kind == TheoremKind.COMPLETENESS:
            hint = (
                "Construct a query Q with a variable x not tracked in the encoding metadata.  "
                "The reconstruction would miss x, producing an incomplete extraction."
            )
        else:
            hint = (
                f"For theorems of kind '{kind.value}', try boundary cases: "
                "empty inputs, maximal inputs, or self-referential structures."
            )

        return (
            f"Counterexample search hint for '{theorem.name}' ({kind.value}):\n"
            f"{hint}\n\n"
            f"Verification status: {theorem.verification_status.value}\n"
            f"Note: This is a heuristic suggestion, not a proven counterexample."
        )

    def __repr__(self) -> str:
        return f"CopilotTheoremAssist(id={self.assist_id[:8]}...)"


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "VerificationStatus",
    "TheoremKind",
    # Dataclasses
    "Theorem",
    "TheoremRegistry",
    "CopilotTheoremAssist",
    # Theorem constants
    "THEOREM_TOTALITY_UNDER_RESTRICTION",
    "THEOREM_EXCEPTION_PROPAGATION_MONOTONICITY",
    "THEOREM_ALGEBRAIC_SURFACE_FAITHFULNESS",
    "THEOREM_MODEL_RECONSTRUCTION_SOUNDNESS",
    "THEOREM_BRANCH_SENSITIVITY_CORRECTNESS",
    # Module-level registry
    "THEOREM_REGISTRY",
]
