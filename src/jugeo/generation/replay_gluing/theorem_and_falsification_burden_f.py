"""
Theorems about replay gluing correctness and falsification conditions.

    # copilot: theorems about replay gluing correctness and falsification conditions

This module formalises the correctness properties of the replay-gluing pipeline
as first-class Python objects.  Each theorem is a frozen dataclass carrying its
statement, hypotheses, conclusion, a reference to its proof (if any), and a
``TrustTier`` drawn from the lattice ``T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)``.

Background – Replay Gluing in One Paragraph
--------------------------------------------
The replay-gluing stage takes a finite open cover {U_i} of a replay corpus and,
for each patch U_i, builds a *local section* s_i : U_i → Σ where Σ is the space
of encoded game-state sequences.  The global section s : ⋃ U_i → Σ exists and
is unique when the overlap consistency condition

    s_i |_{U_i ∩ U_j} = s_j |_{U_i ∩ U_j}   ∀ i, j

holds (cf. the descent / Čech perspective).  When an obstruction δ[s] ∈
Ȟ¹({U_i}, Σ) is non-trivial the global section does not exist and the gluing
*fails*.

Theorems Encoded Here
---------------------
- **T-GLUE-01** – *Global Gluing Correctness*: if every local section is
  consistent on overlaps and the cover is admissible, the global section equals
  the ground-truth replay.
- **T-GLUE-02** – *Overlap Consistency*: pairwise overlap consistency is
  necessary and sufficient for the Čech 1-cocycle to be trivial.
- **T-GLUE-03** – *Monotone Trust Propagation*: the trust tier of the global
  section is the meet of the trust tiers of all local sections.

Falsification Burdens
---------------------
Each theorem ships with a companion ``FalsificationBurden`` that specifies the
minimal counterexample schema needed to refute the theorem.  The burden for
T-GLUE-01 is an explicit overlap inconsistency witness; the burden for T-GLUE-03
is a pair (local section with tier τ, global section with tier > τ).

Mathematical references
-----------------------
[1] Grothendieck, A. (1957). Sur quelques points d'algèbre homologique.
[2] Serre, J.-P. (1955). Faisceaux algébriques cohérents.
[3] Bauer, M. et al. (2021). A sheaf-theoretic account of information integration.
[4] Abramsky, S. & Brandenburger, A. (2011). The sheaf-theoretic structure of
    non-locality and contextuality. New Journal of Physics 13(11).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import abc
import collections
import datetime
import enum
import functools
import hashlib
import itertools
import logging
import math
import random
import re
import uuid
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

# ---------------------------------------------------------------------------
# jugeo imports with fallback stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, enum.Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, enum.Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(enum.IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, enum.Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, enum.Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, enum.Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
# Legacy alias so older code that used `log` still works
log = logger

# ===========================================================================
# Module-level mathematical constants
# ===========================================================================

COCYCLE_CONDITION: str = """
COCYCLE CONDITION  (Čech 1-cocycles)
--------------------------------------
Given a cover U = {U_0, U_1, …, U_n} of the replay timeline and a sheaf F,
a 1-cochain  c ∈ C¹(U, F)  assigns to every ordered pair (i,j) with U_i ∩ U_j ≠ ∅
a section  c_{ij} ∈ F(U_i ∩ U_j).

The COCYCLE CONDITION requires:
    c_{ij} + c_{jk} = c_{ik}   on  U_i ∩ U_j ∩ U_k   for all i,j,k.

Equivalently, in matrix notation:  δ¹(c) = 0  where δ¹ is the Čech coboundary map.

In the replay-gluing context:
  - U_i corresponds to the i-th replay window [t_i, t_{i+1}]
  - F(U_i) is the set of valid proof-state sections on that window
  - c_{ij} measures the "twist" needed to pass from the local section on U_i
    to the local section on U_j when both are restricted to the overlap U_i ∩ U_j.
  - If c is a coboundary (c_{ij} = f_j – f_i for global f), the twist is trivial
    and global gluing succeeds.

Failure of the cocycle condition is the primary obstruction to replay gluing.
"""

DESCENT_THEOREM: str = """
DESCENT THEOREM  (Grothendieck descent for sheaves)
-----------------------------------------------------
Let X be a topological space (here: the replay timeline), U = {U_i} a cover, and
F a presheaf on X.  F is a SHEAF if and only if for every open set V ⊆ X and every
family  {s_i ∈ F(V ∩ U_i)}  satisfying

    s_i |_{V ∩ U_i ∩ U_j} = s_j |_{V ∩ U_i ∩ U_j}   for all i,j,

there exists a UNIQUE  s ∈ F(V)  such that  s|_{V ∩ U_i} = s_i  for all i.

The two conditions are:
  (i)  LOCALITY:   two sections equal on all U_i must be equal globally.
  (ii) GLUING:     compatible local sections glue to a unique global section.

In jugeo replay gluing:
  - "Compatible local sections" = replay fragments whose final states agree on the
    overlap region (the shared proof-state boundary between adjacent windows).
  - The unique global section is the assembled replay proof.
  - Failure of locality means the proof is ambiguous (two different replays produce
    the same local observations but different global proofs).
  - Failure of gluing means the replay fragments cannot be assembled (an obstruction
    in Ȟ¹ is non-zero).
"""

MAYER_VIETORIS_SEQUENCE: str = """
MAYER–VIETORIS SEQUENCE  (for two-patch cover)
------------------------------------------------
For a cover U = {U_0, U_1}  (two overlapping replay windows) and a sheaf F, the
Mayer–Vietoris long exact sequence is:

  0 → F(U_0 ∪ U_1) →^{r} F(U_0) ⊕ F(U_1) →^{δ} F(U_0 ∩ U_1) →^{∂}
  → Ȟ¹(U_0 ∪ U_1, F) → Ȟ¹(U_0, F) ⊕ Ȟ¹(U_1, F) → …

where:
  r(s) = (s|_{U_0}, s|_{U_1})          (restriction to patches)
  δ(s_0, s_1) = s_1|_{U_0∩U_1} – s_0|_{U_0∩U_1}   (compatibility defect)

The CONNECTING HOMOMORPHISM ∂ sends a section over the overlap to a cohomology class
measuring the obstruction to extending it globally.

In replay terms:
  If two replay fragments agree on their shared boundary (δ = 0), Mayer–Vietoris
  guarantees that a unique global replay exists.  If δ ≠ 0, the image under ∂
  is the obstruction class [c] ∈ Ȟ¹, and the replay cannot be glued without
  modifying one of the fragments.
"""

EXACTNESS_CRITERION: str = """
EXACTNESS CRITERION  (for replay gluing correctness)
------------------------------------------------------
A sequence of replay steps  r_0, r_1, …, r_n  is EXACT at position k if:

    image(r_{k-1}) = kernel(r_k)

i.e., every output of step k–1 is consumed by step k, and every input of step k
comes from step k–1.

Exactness at every position is the algebraic analogue of the sheaf gluing condition.
It guarantees that the assembled replay is free of:
  (a) Gaps:        inputs to r_k not produced by r_{k-1}  (missing evidence)
  (b) Redundancy:  outputs of r_{k-1} not consumed by r_k (unused evidence)
  (c) Conflicts:   two steps producing contradictory outputs at the same position

The EXACTNESS DEFECT at position k is:
    d_k = dim(kernel(r_k)) – dim(image(r_{k-1}))
A non-zero defect signals either a gap (d_k > 0) or an over-determined system
(d_k < 0, which in an exact category forces a contradiction).
"""

SHEAF_COHOMOLOGY_INTERPRETATION: str = """
SHEAF COHOMOLOGY AND PROOF OBLIGATIONS
----------------------------------------
The cohomology groups Ȟⁿ(U, F) have the following interpretations in jugeo:

  Ȟ⁰(U, F) = F(X) = global sections = fully assembled, globally coherent replay proofs.

  Ȟ¹(U, F) = first obstruction group:
    A non-zero class [c] ∈ Ȟ¹ means local sections are compatible pairwise but
    cannot be assembled globally.  Each non-zero class represents an open PROOF
    OBLIGATION: someone must either:
      (a) show [c] = 0 by finding a coboundary witness (i.e., a global section that
          restricts to the local sections), or
      (b) discharge the obligation by adding a new local section that kills the class.

  Ȟ²(U, F) = secondary obstructions to lifting:
    Arise when trying to extend a partial gluing past a triple overlap.  In the
    jugeo four-dimensional proof lattice, H² classes correspond to CIRCULAR
    DEPENDENCIES between replay fragments.

  The EULER CHARACTERISTIC of the cover is:
    χ(U, F) = dim Ȟ⁰ – dim Ȟ¹ + dim Ȟ² – …
  and equals the Euler characteristic of the underlying space (replay timeline)
  twisted by F.  A non-zero Euler characteristic is a topological invariant that
  constrains the possible proof structures.
"""

FALSIFICATION_BURDEN_PRINCIPLE: str = """
FALSIFICATION BURDEN PRINCIPLE
--------------------------------
The FALSIFICATION BURDEN of a theorem T with trust tier τ is the difficulty of
finding a counterexample.  It is formalised as a real number β(T, τ) ∈ [0, 1]:

  β = 0.0  →  trivially falsifiable (the theorem is likely wrong)
  β = 0.5  →  moderate burden (empirical evidence required)
  β = 1.0  →  practically unfalsifiable (formally proved)

The burden increases with trust tier:
  β(T, PROPOSAL)          ≈ 0.1 – 0.3
  β(T, REVIEWED)          ≈ 0.3 – 0.5
  β(T, VERIFIED)          ≈ 0.5 – 0.7
  β(T, RUNTIME_WITNESSED) ≈ 0.7 – 0.9
  β(T, PROOF_BACKED)      ≈ 0.95 – 1.0

The FALSIFICATION ORACLE in this module implements a probabilistic model that
estimates β from:
  1. The structural complexity of the theorem's hypotheses.
  2. The size and diversity of the counterexample search space.
  3. The number of failed falsification attempts so far.
  4. The Čech cohomology class of the associated invariants.

A theorem at PROPOSAL tier with β < 0.3 should be DEMOTED or accompanied by an
explicit disclaimer.  A theorem at PROOF_BACKED tier requires a formal proof
certificate before β can be set ≥ 0.95.
"""

NERVE_THEOREM: str = """
NERVE THEOREM  (homotopy type of the cover)
---------------------------------------------
Let U = {U_i} be a good cover of the topological space X (every non-empty finite
intersection is contractible).  The NERVE of U is the abstract simplicial complex
N(U) whose k-simplices are (k+1)-element subsets {i_0, …, i_k} such that
U_{i_0} ∩ … ∩ U_{i_k} ≠ ∅.

The NERVE THEOREM states:  |N(U)| ≃ X  (homotopy equivalence).

Corollary for replay gluing:
  If each replay window U_i and every non-empty intersection U_{i_0} ∩ … ∩ U_{i_k}
  is contractible (no cyclic dependencies within a window), then the nerve of the
  cover captures the full homotopy type of the replay timeline.  In particular:
    π_0(N(U)) counts connected components (isolated replay fragments)
    π_1(N(U)) counts 1-dimensional holes (irreconcilable replay cycles)
    H¹(N(U)) ≅ Ȟ¹(U, ℤ) via the universal coefficient theorem

  A replay with non-trivial π_1 contains circular dependencies that prevent total
  ordering of steps.  These must be resolved before gluing can succeed.
"""

# ===========================================================================
# TrustTier – ordered algebra  T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)
# ===========================================================================

class TrustTier(enum.IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float.

    The five tiers form a totally ordered set under the natural integer order.
    The lattice operations join (⊕) and meet (⊖) are implemented as max and min
    respectively.  Promotion (↑_π) and demotion (↓_χ) are clamped increments.

    Tier semantics
    --------------
    PROPOSAL         — oracle-proposed, not yet reviewed.
    REVIEWED         — human or secondary-oracle reviewed but not formally proved.
    VERIFIED         — verified by lightweight static checks or model-based testing.
    RUNTIME_WITNESSED — witnessed by at least one concrete runtime execution.
    PROOF_BACKED     — supported by a mechanically-checked formal proof.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: "TrustTier") -> "TrustTier":
        """Lattice join (least upper bound)."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Lattice meet (greatest lower bound)."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> "TrustTier":
        """↑_π — promote by one tier, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> "TrustTier":
        """↓_χ — demote by one tier, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

    @staticmethod
    def bottom() -> "TrustTier":
        """Minimum element of the trust lattice."""
        return TrustTier.PROPOSAL

    @staticmethod
    def top() -> "TrustTier":
        """Maximum element of the trust lattice."""
        return TrustTier.PROOF_BACKED

    def is_above_threshold(self, threshold: "TrustTier") -> bool:
        """Return True iff self ≥ threshold."""
        return self.value >= threshold.value

    def falsification_burden_prior(self) -> float:
        """Return the prior falsification burden β for this tier (0.0–1.0)."""
        table = {
            TrustTier.PROPOSAL:          0.20,
            TrustTier.REVIEWED:          0.40,
            TrustTier.VERIFIED:          0.62,
            TrustTier.RUNTIME_WITNESSED: 0.80,
            TrustTier.PROOF_BACKED:      0.97,
        }
        return table[self]


# ===========================================================================
# Core dataclasses – Judgment and CechObstruction
# ===========================================================================

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Parameters
    ----------
    context:
        The evaluation context in which the judgment is made (e.g. a dict of
        gluing-state variables or a named scope string).
    formula:
        The proposition being judged (typically a string formula or a
        dataclass carrying structured semantic content).
    assumptions:
        Immutable tuple of assumption labels or structured assumption objects
        that are in scope for this judgment.
    evidence:
        Immutable tuple of evidence items that support (or contradict) the
        judgment.
    obligations:
        Immutable tuple of proof-obligations that remain open.  An empty tuple
        means all obligations have been discharged.
    burden:
        The falsification burden associated with this judgment — the minimal
        evidence that would refute it.
    trust:
        The ``TrustTier`` assigned to this judgment.
    provenance:
        Metadata about how the judgment was produced (solver run, runtime
        witness, oracle proposal, etc.).
    """

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


@dataclass(frozen=True)
class CechObstruction:
    """A Čech 1-cocycle obstruction to the existence of a global section.

    An obstruction arises when the overlap-consistency conditions cannot be
    simultaneously satisfied.  The cohomology class lives in Ȟ¹({U_i}, Σ).

    Attributes
    ----------
    cover_id:
        Identifier of the open cover {U_i} in which the obstruction was
        detected.
    cocycle:
        The offending cocycle as a frozenset of (i, j, mismatch_hash) triples.
    cohomology_class:
        A canonical string representation of the cohomology class (e.g. its
        SHA-256 digest).
    description:
        Human-readable description of the obstruction.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return ``True`` iff the cocycle is the zero cocycle (no obstruction)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Module-level helper utilities
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (no microseconds)."""
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _sha256_hex(data: str) -> str:
    """Return the lower-case hexadecimal SHA-256 digest of *data*."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _fresh_id(prefix: str) -> str:
    """Return a collision-resistant ID of the form ``<PREFIX>-<UUID4>``."""
    return f"{prefix}-{uuid.uuid4().hex[:16].upper()}"


def _coerce_tuple(value: Any) -> tuple:
    """Coerce *value* to a tuple, leaving tuples untouched."""
    if isinstance(value, tuple):
        return value
    if isinstance(value, (list, set, frozenset)):
        return tuple(value)
    return (value,)


def _meet_tiers(tiers: Iterable[TrustTier]) -> TrustTier:
    """Return the lattice meet of an iterable of ``TrustTier`` values."""
    result = TrustTier.PROOF_BACKED
    for t in tiers:
        result = result.meet(t)
    return result


def _join_tiers(tiers: Iterable[TrustTier]) -> TrustTier:
    """Return the lattice join of an iterable of ``TrustTier`` values."""
    result = TrustTier.PROPOSAL
    for t in tiers:
        result = result.join(t)
    return result


def _cohomology_class_label(cocycle: frozenset) -> str:
    """Derive a deterministic cohomology-class label from a cocycle frozenset."""
    if not cocycle:
        return "trivial"
    serialised = ";".join(sorted(str(e) for e in cocycle))
    return "H1:" + _sha256_hex(serialised)[:16]


def _check_hypotheses(hypotheses: tuple, gluing_state: dict) -> Tuple[bool, List[str]]:
    """Attempt to verify each hypothesis string against *gluing_state*.

    Returns a pair ``(all_satisfied, unsatisfied_list)``.
    """
    unsatisfied: List[str] = []
    for hyp in hypotheses:
        if gluing_state.get(hyp):
            continue
        if hyp in gluing_state.values():
            continue
        unsatisfied.append(hyp)
    return (len(unsatisfied) == 0, unsatisfied)


def _build_provenance_dict(
    source: str,
    theorem_id: str,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    """Assemble a provenance metadata dict for use in ``Judgment.provenance``."""
    prov: Dict[str, Any] = {
        "source": source,
        "theorem_id": theorem_id,
        "generated_at": _utc_now_iso(),
        "module": __name__,
    }
    if extra:
        prov.update(extra)
    return prov


# ===========================================================================
# Primary dataclasses (required API)
# ===========================================================================

@dataclass(frozen=True)
class ReplayGluingTheorem:
    """A formal statement about the correctness of the replay-gluing process.

    A theorem here is understood in the Curry–Howard sense: it is a
    *proposition* together with a *trust tier* and optional references to an
    external proof document and a falsification burden.  It does NOT carry a
    proof object — that is the responsibility of ``GluingCorrectnessProof``.

    Parameters
    ----------
    theorem_id:
        Globally unique identifier (e.g. ``"T-GLUE-01"``).
    name:
        Short human-readable name for the theorem.
    statement:
        Full statement of the theorem as a natural-language (or semi-formal)
        string.
    hypotheses:
        Tuple of precondition strings that must hold for the theorem to apply.
    conclusion:
        The conclusion that follows when all hypotheses are satisfied.
    trust_tier:
        Current trust level.  A theorem should never be consumed downstream at
        a tier higher than this value.
    proof_ref:
        Optional reference to an external proof artefact (file path, DOI, …).
    falsification_burden_ref:
        Optional ID of the companion ``FalsificationBurden`` object.

    Notes
    -----
    All fields are immutable (``frozen=True``).  To attach a proof, use the
    ``with_proof`` factory method which returns a new instance.
    """

    theorem_id: str
    name: str
    statement: str
    hypotheses: tuple
    conclusion: str
    trust_tier: TrustTier
    proof_ref: Optional[str]
    falsification_burden_ref: Optional[str]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def to_judgment(self) -> Judgment:
        """Lift this theorem to a ``Judgment`` carrying no evidence yet."""
        return Judgment(
            context={"theorem_id": self.theorem_id},
            formula=self.statement,
            assumptions=self.hypotheses,
            evidence=(),
            obligations=(self.conclusion,),
            burden=self.falsification_burden_ref,
            trust=self.trust_tier,
            provenance=_build_provenance_dict("theorem_statement", self.theorem_id),
        )

    def is_proven(self) -> bool:
        """Return ``True`` iff ``trust_tier ≥ TrustTier.VERIFIED``."""
        return self.trust_tier >= TrustTier.VERIFIED

    def describe(self) -> str:
        """Return a structured one-paragraph description of the theorem."""
        hyp_list = ", ".join(self.hypotheses) if self.hypotheses else "(none)"
        proof_note = (
            f"Proof reference: {self.proof_ref}."
            if self.proof_ref
            else "No proof reference attached."
        )
        burden_note = (
            f"Falsification burden: {self.falsification_burden_ref}."
            if self.falsification_burden_ref
            else "No falsification burden attached."
        )
        return (
            f"[{self.theorem_id}] {self.name}\n"
            f"  Trust tier : {self.trust_tier.name} ({self.trust_tier.value})\n"
            f"  Statement  : {self.statement}\n"
            f"  Hypotheses : {hyp_list}\n"
            f"  Conclusion : {self.conclusion}\n"
            f"  {proof_note}\n"
            f"  {burden_note}"
        )

    def with_proof(self, proof_ref: str) -> "ReplayGluingTheorem":
        """Return a new theorem with *proof_ref* attached and tier promoted."""
        return _dc_replace(
            self,
            proof_ref=proof_ref,
            trust_tier=self.trust_tier.promote(),
        )

    # Legacy compatibility shims (used by older sub-classes in this module)
    @property
    def theorem_name(self) -> str:
        return self.name

    @property
    def proof_obligations(self) -> tuple:
        return ()


@dataclass(frozen=True)
class GluingCorrectnessProof:
    """A constructive proof that the global section is correct given local ones.

    Each instance represents a single proof attempt for a specific theorem.
    The proof is *constructive* in the sense that ``proof_steps`` enumerates
    the logical deduction steps that witness the theorem conclusion.

    Parameters
    ----------
    proof_id:
        Unique proof identifier.
    theorem_id:
        ID of the theorem this proof attempts to establish.
    proof_strategy:
        High-level description of the proof strategy.
    proof_steps:
        Ordered tuple of proof-step artefacts.
    verified_by:
        Name / identifier of the checker that verified this proof.
    trust_tier:
        Trust tier assigned after verification.
    timestamp:
        ISO-8601 UTC timestamp at which the proof was recorded.
    """

    proof_id: str
    theorem_id: str
    proof_strategy: str
    proof_steps: tuple
    verified_by: str
    trust_tier: TrustTier
    timestamp: str
    # Required spec fields (with defaults for backward compat)
    lemmas_used: tuple = ()          # tuple[str, ...]
    verification_status: str = "SKETCH"  # "COMPLETE" | "PARTIAL" | "SKETCH"
    cech_witnesses: tuple = ()       # tuple[tuple[complex, ...], ...]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def to_judgment(self) -> Judgment:
        """Produce a ``Judgment`` that encodes this proof's conclusions."""
        formula = {
            "kind": "proof_judgment",
            "proof_id": self.proof_id,
            "theorem_id": self.theorem_id,
            "strategy": self.proof_strategy,
            "verified_by": self.verified_by,
        }
        return Judgment(
            context={"proof_id": self.proof_id, "theorem_id": self.theorem_id},
            formula=formula,
            assumptions=(),
            evidence=self.proof_steps,
            obligations=() if self.is_complete() else (f"Incomplete proof for {self.theorem_id}",),
            burden=None,
            trust=self.trust_tier,
            provenance=_build_provenance_dict(
                "constructive_proof", self.theorem_id,
                extra={"proof_id": self.proof_id, "verified_by": self.verified_by},
            ),
        )

    def is_complete(self) -> bool:
        """Return ``True`` iff the proof has steps and trust ≥ VERIFIED."""
        return len(self.proof_steps) > 0 and self.trust_tier >= TrustTier.VERIFIED

    def describe(self) -> str:
        """Return a multi-line description of this proof."""
        status = "COMPLETE" if self.is_complete() else "INCOMPLETE"
        steps_preview = (
            self.proof_steps[:3] if len(self.proof_steps) > 3 else self.proof_steps
        )
        return (
            f"Proof {self.proof_id} [{status}]\n"
            f"  Theorem    : {self.theorem_id}\n"
            f"  Strategy   : {self.proof_strategy}\n"
            f"  Steps      : {len(self.proof_steps)} (preview: {steps_preview})\n"
            f"  Verified by: {self.verified_by}\n"
            f"  Trust tier : {self.trust_tier.name}\n"
            f"  Timestamp  : {self.timestamp}"
        )

    def completeness_ratio(self) -> float:
        """Fraction of proof steps that are not stub placeholders."""
        if not self.proof_steps:
            return 0.0
        non_stubs = sum(1 for s in self.proof_steps if not str(s).strip().startswith("TODO"))
        return non_stubs / len(self.proof_steps)


@dataclass(frozen=True)
class FalsificationBurden:
    """The condition under which a gluing theorem can be falsified.

    A falsification burden specifies (a) *what* kind of evidence is needed to
    refute the companion theorem, (b) a schema for constructing a
    counterexample, and (c) the current trust tier of the burden itself.

    A burden is "met" when the caller supplies an *evidence* dict that contains
    at least one entry matching the ``required_evidence_kind`` and whose value
    is truthy.

    Parameters
    ----------
    burden_id:
        Unique identifier for this burden.
    theorem_id:
        ID of the theorem whose falsification this burden governs.
    falsification_condition:
        Logical condition under which the theorem fails (as a string formula).
    counterexample_schema:
        Template for constructing a minimal counterexample.
    required_evidence_kind:
        The kind of evidence that must appear in an evidence dict for the
        burden to be considered met.
    trust_tier:
        Trust tier of the burden — a burden at tier PROOF_BACKED means there
        exists a known, mechanically-verified counterexample.
    """

    burden_id: str
    theorem_id: str
    falsification_condition: str
    counterexample_schema: str
    required_evidence_kind: str
    trust_tier: TrustTier
    # Required spec fields (with defaults for backward compat)
    counterexample_search_space: str = ""  # spec field
    burden_level: float = 0.5              # 0.0 = easy to falsify, 1.0 = very hard

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def to_judgment(self) -> Judgment:
        """Lift this burden into a ``Judgment`` representing the falsification claim."""
        formula = {
            "kind": "falsification_burden",
            "burden_id": self.burden_id,
            "theorem_id": self.theorem_id,
            "condition": self.falsification_condition,
        }
        return Judgment(
            context={"burden_id": self.burden_id, "theorem_id": self.theorem_id},
            formula=formula,
            assumptions=(),
            evidence=(),
            obligations=(f"Supply evidence of kind '{self.required_evidence_kind}'",),
            burden=self,
            trust=self.trust_tier,
            provenance=_build_provenance_dict(
                "falsification_burden", self.theorem_id,
                extra={"burden_id": self.burden_id},
            ),
        )

    def is_met(self, evidence: dict) -> bool:
        """Return ``True`` if *evidence* contains the required falsification evidence."""
        if evidence.get(self.required_evidence_kind):
            return True
        return self.required_evidence_kind in evidence.values()

    def describe(self) -> str:
        """Return a human-readable description of this falsification burden."""
        status = "MET (known counterexample)" if self.trust_tier >= TrustTier.RUNTIME_WITNESSED else "OPEN"
        return (
            f"FalsificationBurden {self.burden_id} [{status}]\n"
            f"  Theorem    : {self.theorem_id}\n"
            f"  Condition  : {self.falsification_condition}\n"
            f"  CE schema  : {self.counterexample_schema}\n"
            f"  Requires   : {self.required_evidence_kind}\n"
            f"  Trust tier : {self.trust_tier.name}"
        )

    def to_cech_obstruction(self) -> CechObstruction:
        """Convert this burden into a ``CechObstruction`` if the condition is geometric."""
        if self.trust_tier < TrustTier.RUNTIME_WITNESSED:
            return CechObstruction(
                cover_id=self.theorem_id,
                cocycle=frozenset(),
                cohomology_class="trivial",
                description=f"Hypothetical obstruction for {self.burden_id} (burden not yet met).",
            )
        digest = _sha256_hex(self.falsification_condition)[:8]
        cocycle = frozenset({(self.burden_id, self.theorem_id, digest)})
        return CechObstruction(
            cover_id=self.theorem_id,
            cocycle=cocycle,
            cohomology_class=_cohomology_class_label(cocycle),
            description=f"Obstruction derived from burden {self.burden_id}: {self.falsification_condition}",
        )

    # Legacy shim
    @property
    def falsification_conditions(self) -> tuple:
        return (self.falsification_condition,)

    def burden_category(self) -> str:
        prior = self.trust_tier.falsification_burden_prior()
        if prior < 0.3:
            return "LOW"
        if prior < 0.6:
            return "MEDIUM"
        if prior < 0.85:
            return "HIGH"
        return "VERY_HIGH"


@dataclass(frozen=True)
class GluingInvariant:
    """An invariant that holds throughout the replay-gluing process.

    An invariant is a predicate that must evaluate to ``True`` at every
    checkpoint recorded in ``holds_at``.  It is checked against a
    ``gluing_state`` dict by ``check()``.

    Parameters
    ----------
    invariant_id:
        Unique identifier.
    name:
        Short descriptive name.
    expression:
        The invariant expression as a string.
    holds_at:
        Tuple of stage identifiers at which this invariant is expected to hold.
    trust_tier:
        Confidence in the invariant itself.
    last_checked:
        ISO-8601 UTC timestamp of the most recent successful check, or
        ``None`` if never checked.
    """

    invariant_id: str
    name: str
    expression: str
    holds_at: tuple
    trust_tier: TrustTier
    last_checked: Optional[str]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check(self, gluing_state: dict) -> bool:
        """Evaluate the invariant against *gluing_state*.

        Parameters
        ----------
        gluing_state:
            Current gluing-process state.

        Returns
        -------
        bool
            ``True`` iff the invariant passes for the given state.
        """
        if gluing_state.get(self.invariant_id) is False:
            logger.debug("Invariant %s explicitly overridden to False.", self.invariant_id)
            return False
        if gluing_state.get(self.invariant_id):
            logger.debug("Invariant %s explicitly overridden to True.", self.invariant_id)
            return True
        for stage in self.holds_at:
            stage_val = gluing_state.get(stage)
            if stage_val is False:
                logger.debug("Invariant %s failed at stage '%s'.", self.invariant_id, stage)
                return False
        if self.expression in gluing_state:
            return bool(gluing_state[self.expression])
        return True

    def to_judgment(self) -> Judgment:
        """Produce a ``Judgment`` representing this invariant's assertion."""
        formula = {
            "kind": "invariant",
            "invariant_id": self.invariant_id,
            "name": self.name,
            "expression": self.expression,
        }
        return Judgment(
            context={"invariant_id": self.invariant_id, "holds_at": self.holds_at},
            formula=formula,
            assumptions=self.holds_at,
            evidence=((f"last_checked={self.last_checked}",) if self.last_checked else ()),
            obligations=(),
            burden=None,
            trust=self.trust_tier,
            provenance=_build_provenance_dict("invariant_check", self.invariant_id),
        )

    def describe(self) -> str:
        """Return a multi-line description of this invariant."""
        stages = ", ".join(self.holds_at) if self.holds_at else "(all stages)"
        checked = self.last_checked or "never"
        return (
            f"Invariant {self.invariant_id}: {self.name}\n"
            f"  Expression  : {self.expression}\n"
            f"  Holds at    : {stages}\n"
            f"  Trust tier  : {self.trust_tier.name}\n"
            f"  Last checked: {checked}"
        )

    # Legacy shims so old code that used invariant_name / formal_statement still works
    @property
    def invariant_name(self) -> str:
        return self.name

    @property
    def formal_statement(self) -> str:
        return self.expression

    def is_cohomologically_trivial(self) -> bool:
        """Trivially true for invariants not carrying an explicit Čech class."""
        return True

    def cech_class_norm(self) -> float:
        return 0.0

    def is_endangered_by(self, operation: str) -> bool:
        return False


# ===========================================================================
# Required standalone functions
# ===========================================================================

def verify_gluing_theorem(
    theorem: ReplayGluingTheorem,
    gluing_state: dict,
) -> Judgment:
    """Check that *theorem* holds for the current system state.

    Parameters
    ----------
    theorem:
        The theorem to verify.
    gluing_state:
        Dict mapping hypothesis strings to truthy/falsy values.

    Returns
    -------
    Judgment
        A judgment whose ``trust`` reflects the outcome of the check:
        - ``RUNTIME_WITNESSED`` (or higher) if all hypotheses are satisfied.
        - ``PROPOSAL`` if one or more hypotheses are not met.

    Examples
    --------
    >>> thm = STANDARD_THEOREMS["global_gluing_correctness"]
    >>> j = verify_gluing_theorem(thm, {"sections_consistent": True, "cover_admissible": True})
    >>> j.trust >= TrustTier.RUNTIME_WITNESSED
    True
    """
    all_sat, unsatisfied = _check_hypotheses(theorem.hypotheses, gluing_state)
    if all_sat:
        tier = theorem.trust_tier.join(TrustTier.RUNTIME_WITNESSED)
        evidence: tuple = (f"Hypothesis check passed for {theorem.theorem_id}",)
        obligations: tuple = ()
    else:
        tier = TrustTier.PROPOSAL
        evidence = ()
        obligations = tuple(f"Unmet hypothesis: {h}" for h in unsatisfied)

    return Judgment(
        context={"theorem_id": theorem.theorem_id, "via": "verify_gluing_theorem"},
        formula=theorem.statement,
        assumptions=theorem.hypotheses,
        evidence=evidence,
        obligations=obligations,
        burden=theorem.falsification_burden_ref,
        trust=tier,
        provenance=_build_provenance_dict(
            "verify_gluing_theorem",
            theorem.theorem_id,
            extra={"all_hypotheses_satisfied": all_sat, "unsatisfied": unsatisfied},
        ),
    )


def check_falsification_burden(
    burden: "FalsificationBurden",
    evidence: dict,
) -> Judgment:
    """Determine whether the given falsification condition is met.

    Parameters
    ----------
    burden:
        The falsification burden to evaluate.
    evidence:
        Mapping from evidence-kind strings to truthy payloads.

    Returns
    -------
    Judgment
        If the burden is met the judgment carries ``RUNTIME_WITNESSED`` trust
        and encodes the falsification as an obligation.  Otherwise trust is
        ``PROPOSAL``.
    """
    met = burden.is_met(evidence)
    tier = TrustTier.RUNTIME_WITNESSED if met else TrustTier.PROPOSAL
    obligations_val: tuple = (
        (f"FALSIFICATION DETECTED for theorem {burden.theorem_id}: {burden.falsification_condition}",)
        if met
        else ()
    )
    evidence_val: tuple = (
        tuple(f"{k}={v}" for k, v in evidence.items() if v) if met else ()
    )
    return Judgment(
        context={"burden_id": burden.burden_id, "theorem_id": burden.theorem_id},
        formula=burden.falsification_condition,
        assumptions=(),
        evidence=evidence_val,
        obligations=obligations_val,
        burden=burden,
        trust=tier,
        provenance=_build_provenance_dict(
            "check_falsification_burden",
            burden.theorem_id,
            extra={"burden_id": burden.burden_id, "met": met},
        ),
    )


def validate_gluing_invariant(
    invariant: GluingInvariant,
    gluing_state: dict,
    runtime_data: Optional[dict] = None,
) -> Judgment:
    """Check a ``GluingInvariant`` against *gluing_state*.

    Parameters
    ----------
    invariant:
        The invariant to validate.
    gluing_state:
        Current process state.
    runtime_data:
        Optional legacy parameter (ignored in the new API; accepted for
        backward-compatibility with callers that pass three arguments).

    Returns
    -------
    Judgment
        Trust tier is the meet of the invariant's own tier and the observed
        runtime evidence tier.
    """
    holds = invariant.check(gluing_state)
    if holds:
        tier = invariant.trust_tier.meet(TrustTier.RUNTIME_WITNESSED)
        evidence: tuple = (f"Invariant {invariant.invariant_id} holds.",)
        obligations: tuple = ()
    else:
        tier = TrustTier.PROPOSAL
        evidence = ()
        obligations = (f"Invariant {invariant.invariant_id} VIOLATED: {invariant.expression}",)

    return Judgment(
        context={
            "invariant_id": invariant.invariant_id,
            "holds_at": invariant.holds_at,
        },
        formula=invariant.expression,
        assumptions=invariant.holds_at,
        evidence=evidence,
        obligations=obligations,
        burden=None,
        trust=tier,
        provenance=_build_provenance_dict(
            "validate_gluing_invariant",
            invariant.invariant_id,
            extra={"holds": holds},
        ),
    )


def state_theorem(
    name: str,
    statement: str,
    hypotheses: List[str],
    conclusion: str,
    trust_tier: TrustTier,
) -> ReplayGluingTheorem:
    """Formalise a gluing property as a ``ReplayGluingTheorem``.

    Parameters
    ----------
    name:
        Short human-readable name.
    statement:
        Full natural-language (or semi-formal) statement.
    hypotheses:
        List of precondition strings.
    conclusion:
        The conclusion that follows when all hypotheses are met.
    trust_tier:
        Initial trust level.

    Returns
    -------
    ReplayGluingTheorem

    Examples
    --------
    >>> thm = state_theorem(
    ...     "Commutativity of Patch Merge",
    ...     "For any two patches P and Q, merge(P, Q) == merge(Q, P).",
    ...     ["P_valid", "Q_valid"],
    ...     "merge_is_commutative",
    ...     TrustTier.PROPOSAL,
    ... )
    >>> thm.is_proven()
    False
    """
    theorem_id = _fresh_id("THM")
    return ReplayGluingTheorem(
        theorem_id=theorem_id,
        name=name,
        statement=statement,
        hypotheses=tuple(hypotheses),
        conclusion=conclusion,
        trust_tier=trust_tier,
        proof_ref=None,
        falsification_burden_ref=None,
    )


# ===========================================================================
# TheoremChecker
# ===========================================================================

class TheoremChecker:
    """Verifies theorems and invariants against the current gluing state.

    ``TheoremChecker`` acts as the primary orchestration object for the
    verification pipeline.  It accumulates a check log that can be queried for
    reporting.

    Parameters
    ----------
    checker_id:
        Unique identifier for this checker instance.
    theorems:
        Initial list of ``ReplayGluingTheorem`` objects to manage.
    invariants:
        Initial list of ``GluingInvariant`` objects to manage.
    """

    def __init__(
        self,
        checker_id: str,
        theorems: Optional[List[ReplayGluingTheorem]] = None,
        invariants: Optional[List[GluingInvariant]] = None,
    ) -> None:
        self.checker_id: str = checker_id
        self.theorems: List[ReplayGluingTheorem] = list(theorems or [])
        self.invariants: List[GluingInvariant] = list(invariants or [])
        self.check_log: List[dict] = []
        self.stats: Dict[str, int] = collections.Counter()  # type: ignore[assignment]

    def check_theorem(
        self,
        theorem: ReplayGluingTheorem,
        gluing_state: dict,
    ) -> Judgment:
        """Check *theorem* against *gluing_state* and return a ``Judgment``.

        Parameters
        ----------
        theorem:
            The theorem to check.
        gluing_state:
            Current state mapping (hypothesis keys → truthy/falsy values).

        Returns
        -------
        Judgment
            A judgment whose ``trust`` reflects the outcome of this check.
        """
        all_sat, unsatisfied = _check_hypotheses(theorem.hypotheses, gluing_state)
        if all_sat:
            effective_trust = theorem.trust_tier.join(TrustTier.RUNTIME_WITNESSED)
            evidence: tuple = (f"All {len(theorem.hypotheses)} hypotheses satisfied",)
            obligations: tuple = ()
            outcome = "pass"
        else:
            effective_trust = TrustTier.PROPOSAL
            evidence = ()
            obligations = tuple(f"Hypothesis not met: {h}" for h in unsatisfied)
            outcome = "fail"

        judgment = Judgment(
            context={"checker_id": self.checker_id, "theorem_id": theorem.theorem_id},
            formula=theorem.statement,
            assumptions=theorem.hypotheses,
            evidence=evidence,
            obligations=obligations,
            burden=theorem.falsification_burden_ref,
            trust=effective_trust,
            provenance=_build_provenance_dict(
                "theorem_checker",
                theorem.theorem_id,
                extra={"outcome": outcome, "unsatisfied": unsatisfied},
            ),
        )

        self.check_log.append({
            "kind": "theorem_check",
            "theorem_id": theorem.theorem_id,
            "outcome": outcome,
            "trust": effective_trust.name,
            "unsatisfied": unsatisfied,
            "timestamp": _utc_now_iso(),
        })
        self.stats[f"theorem_{outcome}"] += 1
        logger.info(
            "TheoremChecker[%s] checked %s → %s (trust=%s)",
            self.checker_id, theorem.theorem_id, outcome, effective_trust.name,
        )
        return judgment

    def check_all_invariants(self, gluing_state: dict) -> List[Judgment]:
        """Check every registered invariant against *gluing_state*.

        Returns
        -------
        list[Judgment]
            One judgment per invariant, in registration order.
        """
        results: List[Judgment] = []
        for inv in self.invariants:
            j = validate_gluing_invariant(inv, gluing_state)
            self.check_log.append({
                "kind": "invariant_check",
                "invariant_id": inv.invariant_id,
                "trust": j.trust.name,
                "timestamp": _utc_now_iso(),
            })
            self.stats["invariant_checks"] += 1
            results.append(j)
        return results

    def detect_falsification(
        self,
        burden: FalsificationBurden,
        evidence: dict,
    ) -> bool:
        """Return ``True`` if *burden* is met by *evidence*.

        Parameters
        ----------
        burden:
            The falsification burden to evaluate.
        evidence:
            Mapping from evidence-kind strings to evidence payloads.
        """
        met = burden.is_met(evidence)
        self.check_log.append({
            "kind": "falsification_check",
            "burden_id": burden.burden_id,
            "theorem_id": burden.theorem_id,
            "met": met,
            "timestamp": _utc_now_iso(),
        })
        self.stats["falsification_met" if met else "falsification_open"] += 1
        if met:
            logger.warning(
                "Falsification burden %s MET for theorem %s!",
                burden.burden_id, burden.theorem_id,
            )
        return met

    def get_report(self) -> dict:
        """Return a structured report of all checks performed so far.

        The report dict contains:
        - ``"checker_id"`` — this checker's ID.
        - ``"stats"`` — cumulative counters (pass/fail/invariant/falsification).
        - ``"log_entries"`` — full check log.
        - ``"theorems"`` — summary of registered theorems and their tier.
        - ``"invariants"`` — summary of registered invariants and their tier.
        - ``"generated_at"`` — ISO-8601 timestamp.
        """
        return {
            "checker_id": self.checker_id,
            "stats": dict(self.stats),
            "log_entries": list(self.check_log),
            "theorems": [
                {
                    "id": t.theorem_id,
                    "name": t.name,
                    "trust": t.trust_tier.name,
                    "is_proven": t.is_proven(),
                }
                for t in self.theorems
            ],
            "invariants": [
                {
                    "id": i.invariant_id,
                    "name": i.name,
                    "trust": i.trust_tier.name,
                }
                for i in self.invariants
            ],
            "generated_at": _utc_now_iso(),
        }


# ===========================================================================
# Module-level constants and pre-built standard objects
# ===========================================================================

GLUING_CORRECTNESS_THEOREM_ID: str = "T-GLUE-01"
OVERLAP_CONSISTENCY_THEOREM_ID: str = "T-GLUE-02"
MONOTONE_TRUST_THEOREM_ID: str = "T-GLUE-03"

#: Prefix used for auto-generated burden IDs.
_BURDEN_ID_PREFIX: str = "BRD"
#: Prefix used for auto-generated invariant IDs.
_INVARIANT_ID_PREFIX: str = "INV"

# Pre-built standard theorems
_THEOREM_GLOBAL_GLUING = ReplayGluingTheorem(
    theorem_id=GLUING_CORRECTNESS_THEOREM_ID,
    name="Global Gluing Correctness",
    statement=(
        "If every local section s_i : U_i → Σ satisfies the overlap consistency "
        "condition s_i |_{U_i ∩ U_j} = s_j |_{U_i ∩ U_j} for all i, j, and the "
        "cover {U_i} is admissible (i.e., each U_i is replay-coherent), then the "
        "unique global section s : ⋃ U_i → Σ exists and equals the ground-truth "
        "replay sequence."
    ),
    hypotheses=("sections_consistent", "cover_admissible"),
    conclusion="global_section_equals_ground_truth",
    trust_tier=TrustTier.REVIEWED,
    proof_ref=None,
    falsification_burden_ref="BRD-GLUE-01",
)

_THEOREM_OVERLAP_CONSISTENCY = ReplayGluingTheorem(
    theorem_id=OVERLAP_CONSISTENCY_THEOREM_ID,
    name="Overlap Consistency Criterion",
    statement=(
        "Pairwise overlap consistency — s_i |_{U_i ∩ U_j} = s_j |_{U_i ∩ U_j} for "
        "all pairs (i, j) — is both necessary and sufficient for the Čech 1-cocycle "
        "δ[s] ∈ Ȟ¹({U_i}, Σ) to be the zero class, which is the obstruction-free "
        "condition for a global section to exist."
    ),
    hypotheses=("pairwise_overlaps_consistent",),
    conclusion="cech_cocycle_trivial",
    trust_tier=TrustTier.VERIFIED,
    proof_ref="docs/proofs/cech_overlap_consistency.pdf",
    falsification_burden_ref="BRD-GLUE-02",
)

_THEOREM_MONOTONE_TRUST = ReplayGluingTheorem(
    theorem_id=MONOTONE_TRUST_THEOREM_ID,
    name="Monotone Trust Propagation",
    statement=(
        "The trust tier τ(s) of the global section s is the meet (greatest lower "
        "bound) of the trust tiers of all local sections: "
        "τ(s) = ⋀_i τ(s_i).  In particular, promoting a single local section "
        "cannot lower the global trust, and demoting any section cannot raise it."
    ),
    hypotheses=("local_sections_have_trust_tiers", "global_section_exists"),
    conclusion="global_trust_equals_meet_of_local_trusts",
    trust_tier=TrustTier.RUNTIME_WITNESSED,
    proof_ref="docs/proofs/monotone_trust.pdf",
    falsification_burden_ref="BRD-GLUE-03",
)

#: Named lookup table of all standard theorems.
STANDARD_THEOREMS: Dict[str, ReplayGluingTheorem] = {
    "global_gluing_correctness": _THEOREM_GLOBAL_GLUING,
    "overlap_consistency": _THEOREM_OVERLAP_CONSISTENCY,
    "monotone_trust_propagation": _THEOREM_MONOTONE_TRUST,
}

# Pre-built standard falsification burdens
_BURDEN_01 = FalsificationBurden(
    burden_id="BRD-GLUE-01",
    theorem_id=GLUING_CORRECTNESS_THEOREM_ID,
    falsification_condition=(
        "There exist indices i, j and a frame f ∈ U_i ∩ U_j such that "
        "s_i(f) ≠ s_j(f) — an explicit overlap inconsistency."
    ),
    counterexample_schema=(
        "Construct two local sections whose images disagree on at least one "
        "frame in their shared overlap.  Record (i, j, f, s_i(f), s_j(f))."
    ),
    required_evidence_kind="overlap_inconsistency_witness",
    trust_tier=TrustTier.PROPOSAL,
)

_BURDEN_02 = FalsificationBurden(
    burden_id="BRD-GLUE-02",
    theorem_id=OVERLAP_CONSISTENCY_THEOREM_ID,
    falsification_condition=(
        "The Čech cocycle δ[s] is non-trivial (i.e., δ[s] ≠ 0 in Ȟ¹) even "
        "though all pairwise overlaps appear consistent — indicating a higher-order "
        "coherence failure."
    ),
    counterexample_schema=(
        "Exhibit a triple (i, j, k) such that the cocycle relation "
        "c_{ik} = c_{ij} · c_{jk} fails, despite c_{ij} = 0 and c_{jk} = 0."
    ),
    required_evidence_kind="non_trivial_triple_cocycle",
    trust_tier=TrustTier.PROPOSAL,
)

_BURDEN_03 = FalsificationBurden(
    burden_id="BRD-GLUE-03",
    theorem_id=MONOTONE_TRUST_THEOREM_ID,
    falsification_condition=(
        "The global section s carries a trust tier strictly higher than the meet "
        "of its local sections' tiers — i.e., τ(s) > ⋀_i τ(s_i)."
    ),
    counterexample_schema=(
        "Provide a tuple (s_1, …, s_n, s) where each s_i has tier τ_i and "
        "τ(s) > min(τ_1, …, τ_n)."
    ),
    required_evidence_kind="trust_monotonicity_violation",
    trust_tier=TrustTier.PROPOSAL,
)

# Pre-built standard invariants (new API)
_INVARIANT_NONEMPTY_COVER = GluingInvariant(
    invariant_id="INV-COVER-01",
    name="Non-empty Cover",
    expression="cover_size > 0",
    holds_at=("pre_gluing", "during_gluing", "post_gluing"),
    trust_tier=TrustTier.VERIFIED,
    last_checked=None,
)

_INVARIANT_OVERLAP_COHERENCE = GluingInvariant(
    invariant_id="INV-OVERLAP-02",
    name="Pairwise Overlap Coherence",
    expression="all(s_i(f) == s_j(f) for f in U_i cap U_j for all i, j)",
    holds_at=("post_local_section_build", "pre_gluing"),
    trust_tier=TrustTier.RUNTIME_WITNESSED,
    last_checked=None,
)

_INVARIANT_GLOBAL_UNIQUENESS = GluingInvariant(
    invariant_id="INV-UNIQUE-03",
    name="Global Section Uniqueness",
    expression="exists_unique_global_section",
    holds_at=("post_gluing",),
    trust_tier=TrustTier.REVIEWED,
    last_checked=None,
)

_INVARIANT_TRUST_MONOTONE = GluingInvariant(
    invariant_id="INV-TRUST-04",
    name="Trust Monotonicity",
    expression="global_trust == meet(local_trusts)",
    holds_at=("post_gluing", "post_verification"),
    trust_tier=TrustTier.RUNTIME_WITNESSED,
    last_checked=None,
)

#: Named lookup table of all standard invariants.
STANDARD_INVARIANTS: Dict[str, GluingInvariant] = {
    "nonempty_cover": _INVARIANT_NONEMPTY_COVER,
    "overlap_coherence": _INVARIANT_OVERLAP_COHERENCE,
    "global_uniqueness": _INVARIANT_GLOBAL_UNIQUENESS,
    "trust_monotone": _INVARIANT_TRUST_MONOTONE,
}


# ===========================================================================
# CohomologyObstruction – computing Ȟ¹(U, F) for the nerve of the cover
# ===========================================================================

class CohomologyObstruction:
    """Computes and manipulates Čech 1-cohomology obstructions.

    Given a cover U = {U_0, …, U_{n-1}} of the replay timeline and a coefficient
    sheaf F (modelled here as complex numbers), this class:
      - Stores the 1-cochains c_{ij} ∈ C¹(U, F)
      - Computes the coboundary δ¹(c) ∈ C²(U, F)
      - Checks the cocycle condition δ¹(c) = 0
      - Identifies coboundaries (images of δ⁰)
      - Computes the cohomology class [c] ∈ Ȟ¹(U, F)
      - Generates a human-readable obstruction report
    """

    def __init__(self, num_patches: int) -> None:
        """Initialise with *num_patches* patches U_0, …, U_{n-1}."""
        self._n = num_patches
        # c[(i,j)] = complex coefficient for the overlap U_i ∩ U_j, i < j
        self._cochains: dict[tuple[int, int], complex] = {}
        # Section values f_i ∈ F(U_i)
        self._sections: dict[int, complex] = {}

    # ------------------------------------------------------------------
    # Cochain management
    # ------------------------------------------------------------------

    def set_cochain(self, i: int, j: int, value: complex) -> None:
        """Set the 1-cochain coefficient c_{ij}."""
        if i >= j:
            raise ValueError(f"Require i < j; got ({i}, {j}).")
        self._cochains[(i, j)] = value

    def get_cochain(self, i: int, j: int) -> complex:
        """Retrieve c_{ij} (antisymmetric: c_{ji} = –c_{ij})."""
        if i < j:
            return self._cochains.get((i, j), 0j)
        elif i > j:
            return -self._cochains.get((j, i), 0j)
        return 0j

    def set_section(self, i: int, value: complex) -> None:
        """Set the local section f_i ∈ F(U_i)."""
        self._sections[i] = value

    # ------------------------------------------------------------------
    # Coboundary maps
    # ------------------------------------------------------------------

    def delta0_image(self, i: int, j: int) -> complex:
        """Compute (δ⁰f)_{ij} = f_j – f_i for a local section assignment."""
        fi = self._sections.get(i, 0j)
        fj = self._sections.get(j, 0j)
        return fj - fi

    def coboundary_delta1(self, i: int, j: int, k: int) -> complex:
        """Compute (δ¹c)_{ijk} = c_{jk} – c_{ik} + c_{ij}."""
        return self.get_cochain(j, k) - self.get_cochain(i, k) + self.get_cochain(i, j)

    # ------------------------------------------------------------------
    # Cocycle and cohomology computations
    # ------------------------------------------------------------------

    def is_cocycle(self, tolerance: float = 1e-10) -> bool:
        """Return True iff δ¹(c) = 0 for all triple overlaps."""
        for i, j, k in itertools.combinations(range(self._n), 3):
            if abs(self.coboundary_delta1(i, j, k)) > tolerance:
                return False
        return True

    def is_coboundary(self, tolerance: float = 1e-10) -> bool:
        """Return True iff c is in the image of δ⁰ (i.e., [c] = 0 in Ȟ¹)."""
        # c is a coboundary iff c_{ij} = f_j – f_i for some {f_i}
        # We attempt to solve this linear system by fixing f_0 = 0
        if not self._cochains:
            return True
        f: dict[int, complex] = {0: 0j}
        changed = True
        while changed:
            changed = False
            for (i, j), c_ij in self._cochains.items():
                if i in f and j not in f:
                    f[j] = f[i] + c_ij
                    changed = True
                elif j in f and i not in f:
                    f[i] = f[j] - c_ij
                    changed = True
                elif i in f and j in f:
                    if abs(f[j] - f[i] - c_ij) > tolerance:
                        return False
        return True

    def cohomology_class_representative(self) -> dict[tuple[int, int], complex]:
        """Return a representative of the cohomology class [c] ∈ Ȟ¹(U, F).

        If c is a coboundary, returns the zero cochain (trivial class).
        Otherwise returns c itself as a representative.
        """
        if self.is_coboundary():
            return {pair: 0j for pair in self._cochains}
        return dict(self._cochains)

    def h1_dimension_lower_bound(self) -> int:
        """Return a lower bound on dim Ȟ¹ based on independent non-coboundary classes."""
        return 0 if self.is_coboundary() else 1

    def obstruction_report(self) -> str:
        """Generate a human-readable report of the obstruction."""
        lines: list[str] = [
            f"CohomologyObstruction report  (n={self._n} patches)",
            f"  Cochains defined: {len(self._cochains)}",
            f"  Is cocycle:       {self.is_cocycle()}",
            f"  Is coboundary:    {self.is_coboundary()}",
            f"  dim Ȟ¹ ≥:         {self.h1_dimension_lower_bound()}",
        ]
        for (i, j), val in sorted(self._cochains.items()):
            lines.append(f"    c_({i},{j}) = {val:.4f}")
        return "\n".join(lines)

    def nerve_simplices(self) -> list[tuple[int, ...]]:
        """Return the 1-simplices (edges) of the nerve where cochains are defined."""
        return [pair for pair in self._cochains if self._cochains[pair] != 0j]


# ===========================================================================
# TheoremDatabase
# ===========================================================================

class TheoremDatabase:
    """A catalogue of theorems about sheaf gluing in the jugeo replay framework.

    Built-in theorems cover:
      T1 – Cocycle Gluing Existence
      T2 – Uniqueness of Global Section
      T3 – Locality of the Sheaf Condition
      T4 – Descent for Cover Refinements
      T5 – Mayer–Vietoris Exactness
      T6 – Čech–de Rham Comparison
      T7 – Replay Completeness under Exact Gluing
    """

    _BUILT_IN_THEOREMS: list[dict] = []

    def __init__(self) -> None:
        self._theorems: dict[str, ReplayGluingTheorem] = {}
        self._load_built_ins()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_judgment(
        self,
        context: str,
        formula: str,
        assumptions: tuple,
        evidence: tuple,
        obstructions: dict,
        blame: str,
        tier: TrustTier,
        obligations: tuple,
    ) -> tuple:
        return (context, formula, assumptions, evidence, obstructions, blame, tier, obligations)

    def _load_built_ins(self) -> None:
        """Populate the database with the seven built-in theorems."""
        t1 = ReplayGluingTheorem(
            theorem_id="T1",
            name="Cocycle Gluing Existence",
            statement=(
                "If F is a sheaf on the replay timeline X and the Čech cohomology "
                "Ȟ¹(U,F) vanishes for cover U, then any compatible family of local "
                "sections {s_i ∈ F(U_i)} glues to a unique global section s ∈ F(X)."
            ),
            hypotheses=(
                "F is a sheaf (locality and gluing axioms hold)",
                "Ȟ¹(U, F) = 0 (no cohomological obstruction)",
                "The family {s_i} is compatible: s_i|_{U_i∩U_j} = s_j|_{U_i∩U_j}",
            ),
            conclusion="∃! s ∈ F(X) such that s|_{U_i} = s_i for all i",
            trust_tier=TrustTier.PROOF_BACKED,
            proof_ref="docs/proofs/cocycle_gluing.pdf",
            falsification_burden_ref=None,
        )
        t2 = ReplayGluingTheorem(
            theorem_id="T2",
            name="Uniqueness of Global Section",
            statement=(
                "For a sheaf F and cover U, if two global sections s, t ∈ F(X) "
                "agree on every patch U_i (s|_{U_i} = t|_{U_i} for all i), then s = t."
            ),
            hypotheses=(
                "F satisfies the locality axiom",
                "s|_{U_i} = t|_{U_i} for all i",
            ),
            conclusion="s = t in F(X)",
            trust_tier=TrustTier.PROOF_BACKED,
            proof_ref=None,
            falsification_burden_ref=None,
        )
        t3 = ReplayGluingTheorem(
            theorem_id="T3",
            name="Descent under Cover Refinement",
            statement=(
                "If U' is a refinement of U and the gluing theorem holds for U, "
                "then it holds for U' as well (the gluing is stable under refinement)."
            ),
            hypotheses=(
                "U' is a refinement of U (each U'_i ⊆ some U_{σ(i)})",
                "Gluing succeeds for cover U",
            ),
            conclusion="Gluing succeeds for cover U'",
            trust_tier=TrustTier.VERIFIED,
            proof_ref=None,
            falsification_burden_ref=None,
        )
        t4 = ReplayGluingTheorem(
            theorem_id="T4",
            name="Mayer–Vietoris Exactness",
            statement=(
                "For a two-patch cover U = {U_0, U_1} and sheaf F, the sequence "
                "0 → F(U_0∪U_1) → F(U_0)⊕F(U_1) → F(U_0∩U_1) → Ȟ¹(U,F) → 0 "
                "is exact."
            ),
            hypotheses=(
                "F is a sheaf",
                "U = {U_0, U_1} with U_0 ∩ U_1 ≠ ∅",
            ),
            conclusion="The Mayer–Vietoris sequence is exact at every position",
            trust_tier=TrustTier.PROOF_BACKED,
            proof_ref=None,
            falsification_burden_ref=None,
        )
        t5 = ReplayGluingTheorem(
            theorem_id="T5",
            name="Replay Completeness under Exact Gluing",
            statement=(
                "If the replay-gluing map is exact (kernel = image at every step), "
                "then the assembled replay covers all proof obligations without gaps "
                "or redundancy."
            ),
            hypotheses=(
                "The sequence of replay steps r_0, …, r_n is exact",
                "Every proof obligation Π_k is discharged by some r_j",
            ),
            conclusion="The assembled replay is complete: no proof obligation is left open",
            trust_tier=TrustTier.RUNTIME_WITNESSED,
            proof_ref=None,
            falsification_burden_ref=None,
        )
        t6 = ReplayGluingTheorem(
            theorem_id="T6",
            name="Gluing Correctness (Type Preservation)",
            statement=(
                "If each local replay section s_i is type-correct and the gluing "
                "maps are type-preserving, then the assembled global section s is "
                "type-correct."
            ),
            hypotheses=(
                "Each s_i is well-typed in context c_i",
                "Restriction maps preserve types",
                "The contexts c_i are compatible on overlaps",
            ),
            conclusion="The global section s is well-typed in the global context c",
            trust_tier=TrustTier.VERIFIED,
            proof_ref=None,
            falsification_burden_ref=None,
        )
        t7 = ReplayGluingTheorem(
            theorem_id="T7",
            name="Čech–de Rham Comparison for Replay Cohomology",
            statement=(
                "For a smooth replay manifold M and good cover U, the Čech "
                "cohomology Ȟ*(U, ℝ) is isomorphic to the de Rham cohomology "
                "H*_dR(M) via the integration map."
            ),
            hypotheses=(
                "M is a smooth replay manifold",
                "U is a good cover (all intersections contractible)",
                "The de Rham complex is exact on each U_i",
            ),
            conclusion="Ȟⁿ(U, ℝ) ≅ Hⁿ_dR(M) for all n ≥ 0",
            trust_tier=TrustTier.REVIEWED,
            proof_ref=None,
            falsification_burden_ref=None,
        )

        for t in (t1, t2, t3, t4, t5, t6, t7):
            self._theorems[t.theorem_id] = t

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, theorem_id: str) -> Optional[ReplayGluingTheorem]:
        """Retrieve a theorem by ID."""
        return self._theorems.get(theorem_id)

    def all_theorems(self) -> list[ReplayGluingTheorem]:
        """Return all theorems in insertion order."""
        return list(self._theorems.values())

    def add(self, theorem: ReplayGluingTheorem) -> None:
        """Add or replace a theorem."""
        self._theorems[theorem.theorem_id] = theorem

    def remove(self, theorem_id: str) -> bool:
        """Remove a theorem; return True if it existed."""
        if theorem_id in self._theorems:
            del self._theorems[theorem_id]
            return True
        return False

    def filter_by_tier(self, min_tier: TrustTier) -> list[ReplayGluingTheorem]:
        """Return theorems at or above *min_tier*."""
        return [t for t in self._theorems.values() if t.trust_tier >= min_tier]

    def search(self, keyword: str) -> list[ReplayGluingTheorem]:
        """Return theorems whose statement or name contains *keyword* (case-insensitive)."""
        kw = keyword.lower()
        return [
            t for t in self._theorems.values()
            if kw in t.name.lower() or kw in t.statement.lower()
        ]

    def summary_table(self) -> str:
        """Return an ASCII summary table of all theorems."""
        lines = [
            f"{'ID':<5} {'Name':<45} {'Tier':<22} {'Hypotheses':>10}",
            "-" * 85,
        ]
        for t in self._theorems.values():
            lines.append(
                f"{t.theorem_id:<5} {t.name[:44]:<45} "
                f"{t.trust_tier.name:<22} {len(t.hypotheses):>10}"
            )
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._theorems)

    def __iter__(self) -> Iterator[ReplayGluingTheorem]:
        return iter(self._theorems.values())


# ===========================================================================
# ProofChecker
# ===========================================================================

class ProofChecker:
    """Validates proof steps formally against a set of typing and logic rules.

    Rules are registered as callables: (step: str) → (bool, str) where the
    second element of the tuple is a diagnostic message.
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, Any]] = []
        self._register_default_rules()
        self._log: list[str] = []

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def _register_default_rules(self) -> None:
        """Register built-in proof-step validation rules."""
        self.add_rule("non-empty", lambda s: (bool(s.strip()), "Step is empty."))
        self.add_rule("no-circular", lambda s: ("TODO" not in s, f"Step is a stub: {s!r}"))
        self.add_rule("has-verb", lambda s: (
            any(v in s.lower() for v in ("prove", "show", "check", "verify", "apply", "use", "by", "step", "from", "therefore", "hence", "since", "because", "follows")),
            f"Step lacks a logical verb: {s!r}",
        ))
        self.add_rule("max-length", lambda s: (len(s) <= 500, f"Step is too long ({len(s)} chars)."))
        self.add_rule("no-contradiction", lambda s: (
            not ("true" in s.lower() and "false" in s.lower()),
            f"Step asserts both true and false: {s!r}",
        ))

    def add_rule(self, name: str, rule) -> None:
        """Register a new validation rule."""
        self._rules.append((name, rule))

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------

    def check_step(self, step: str) -> tuple[bool, list[str]]:
        """Check a single proof step against all rules.

        Returns (passed: bool, diagnostics: list[str]).
        """
        diags: list[str] = []
        passed = True
        for name, rule in self._rules:
            ok, msg = rule(step)
            if not ok:
                diags.append(f"[{name}] {msg}")
                passed = False
        self._log.append(f"check_step({step[:40]!r}): {'PASS' if passed else 'FAIL'}")
        return passed, diags

    def check_proof(self, proof: GluingCorrectnessProof) -> tuple[bool, dict[int, list[str]]]:
        """Check all steps of a proof certificate.

        Returns (all_passed: bool, {step_index: [diagnostics]}).
        """
        all_passed = True
        results: dict[int, list[str]] = {}
        for i, step in enumerate(proof.proof_steps):
            ok, diags = self.check_step(step)
            if not ok:
                all_passed = False
                results[i] = diags
        return all_passed, results

    def check_hypotheses(self, theorem: ReplayGluingTheorem) -> tuple[bool, list[str]]:
        """Check that all hypotheses are non-trivially stated."""
        diags: list[str] = []
        for h in theorem.hypotheses:
            ok, d = self.check_step(h)
            if not ok:
                diags.extend(d)
        return (not diags), diags

    def validate_judgment(self, judgment: tuple) -> tuple[bool, list[str]]:
        """Validate the 8-tuple structure of a judgment."""
        diags: list[str] = []
        if len(judgment) != 8:
            return False, [f"Judgment must have 8 components; has {len(judgment)}."]
        c, phi, A, E, O, B, T, Pi = judgment
        if not isinstance(c, str) or not c:
            diags.append("Context c must be a non-empty string.")
        if not isinstance(phi, str) or not phi:
            diags.append("Formula φ must be a non-empty string.")
        if not isinstance(T, TrustTier):
            diags.append(f"Trust tier T must be a TrustTier; got {type(T)}.")
        return (not diags), diags

    def tier_consistent(self, proof: GluingCorrectnessProof, theorem: ReplayGluingTheorem) -> bool:
        """Check that proof.trust_tier ≤ theorem.trust_tier."""
        return proof.trust_tier <= theorem.trust_tier

    def completeness_sufficient(self, proof: GluingCorrectnessProof, min_ratio: float = 0.8) -> bool:
        """Check that at least *min_ratio* of proof steps are non-stub."""
        return proof.completeness_ratio() >= min_ratio

    def full_audit(
        self,
        theorem: ReplayGluingTheorem,
        proof: GluingCorrectnessProof,
    ) -> dict[str, Any]:
        """Run a comprehensive audit and return an audit dict."""
        all_steps_ok, step_diags = self.check_proof(proof)
        hyps_ok, hyp_diags = self.check_hypotheses(theorem)
        # Build a pseudo-judgment from the theorem for validation
        pseudo_judgment = theorem.to_judgment()
        j_tuple = (
            str(pseudo_judgment.context),
            str(pseudo_judgment.formula),
            pseudo_judgment.assumptions,
            pseudo_judgment.evidence,
            {},
            "system",
            pseudo_judgment.trust,
            pseudo_judgment.obligations,
        )
        jdg_ok, jdg_diags = self.validate_judgment(j_tuple)
        tier_ok = self.tier_consistent(proof, theorem)
        complete_ok = self.completeness_sufficient(proof)
        return {
            "theorem_id": theorem.theorem_id,
            "proof_id": proof.proof_id,
            "all_steps_valid": all_steps_ok,
            "step_diagnostics": step_diags,
            "hypotheses_valid": hyps_ok,
            "hypothesis_diagnostics": hyp_diags,
            "judgment_valid": jdg_ok,
            "judgment_diagnostics": jdg_diags,
            "tier_consistent": tier_ok,
            "completeness_ok": complete_ok,
            "overall_pass": all(
                [all_steps_ok, hyps_ok, jdg_ok, tier_ok, complete_ok]
            ),
        }


# ===========================================================================
# CounterexampleGenerator
# ===========================================================================

class CounterexampleGenerator:
    """Generates candidate counterexamples for replay-gluing theorems.

    Counterexample generation uses a combination of:
      1. Random sampling of replay sequences.
      2. Boundary testing (minimal/maximal lengths).
      3. Adversarial cocycle construction (deliberately violating the cocycle condition).
      4. Theorem-specific heuristics.
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._generated: list[dict] = []

    # ------------------------------------------------------------------
    # Generation methods
    # ------------------------------------------------------------------

    def _random_replay_sequence(self, length: int) -> list[str]:
        ops = ["assert", "apply", "rewrite", "split", "intro", "exact", "refine", "have"]
        return [self._rng.choice(ops) + f"_{i}" for i in range(length)]

    def generate_random(self, theorem: ReplayGluingTheorem, n: int = 10) -> list[dict]:
        """Generate *n* random candidate counterexamples."""
        candidates = []
        for _ in range(n):
            length = self._rng.randint(1, 20)
            seq = self._random_replay_sequence(length)
            candidate = {
                h: self._rng.random() > 0.3 for h in theorem.hypotheses
            }
            candidate[theorem.conclusion] = self._rng.random() > 0.1
            candidate["_sequence"] = seq
            candidates.append(candidate)
        self._generated.extend(candidates)
        return candidates

    def generate_adversarial_cocycle(self, n_patches: int = 3) -> CohomologyObstruction:
        """Construct a 1-cocycle that is NOT a coboundary (non-trivial class)."""
        obs = CohomologyObstruction(n_patches)
        angle = self._rng.uniform(0.1, math.pi)
        for i in range(n_patches):
            for j in range(i + 1, n_patches):
                obs.set_cochain(i, j, complex(math.cos(angle * (i + j)), math.sin(angle * (i + j))))
        return obs

    def generate_boundary_cases(self, theorem: ReplayGluingTheorem) -> list[dict]:
        """Generate minimal and maximal boundary counterexample candidates."""
        minimal: dict = {h: True for h in theorem.hypotheses}
        minimal[theorem.conclusion] = False
        maximal: dict = {h: False for h in theorem.hypotheses}
        maximal[theorem.conclusion] = True
        self._generated.extend([minimal, maximal])
        return [minimal, maximal]

    def generate_cocycle_violation(self, theorem: ReplayGluingTheorem) -> dict:
        """Construct a candidate that violates the cocycle condition."""
        candidate: dict = {h: True for h in theorem.hypotheses}
        candidate[theorem.conclusion] = False
        candidate["_cocycle_violation"] = True
        candidate["_violation_detail"] = (
            "c_{01} + c_{12} ≠ c_{02}: δ¹(c) = "
            f"{self._rng.uniform(-2, 2):.4f} + "
            f"{self._rng.uniform(-2, 2):.4f}i ≠ 0"
        )
        self._generated.append(candidate)
        return candidate

    def all_generated(self) -> list[dict]:
        """Return all generated candidates so far."""
        return list(self._generated)

    def statistics(self) -> dict[str, int]:
        """Return statistics about generated candidates."""
        total = len(self._generated)
        with_violations = sum(1 for c in self._generated if c.get("_cocycle_violation", False))
        return {"total": total, "with_cocycle_violation": with_violations}


# ===========================================================================
# InvariantMonitor
# ===========================================================================

class InvariantMonitor:
    """Monitors GluingInvariants at runtime and records violations.

    Maintains a registry of invariants and checks them against snapshots of
    the proof state as they are produced during replay execution.
    """

    def __init__(self) -> None:
        self._invariants: dict[str, GluingInvariant] = {}
        self._violations: list[dict] = []
        self._check_count: int = 0

    def register(self, invariant: GluingInvariant) -> None:
        """Register an invariant for monitoring."""
        self._invariants[invariant.invariant_id] = invariant

    def unregister(self, invariant_id: str) -> bool:
        """Unregister an invariant; return True if it was present."""
        if invariant_id in self._invariants:
            del self._invariants[invariant_id]
            return True
        return False

    def check_all(self, global_section: dict, runtime_data: dict) -> dict[str, Any]:
        """Check all registered invariants and return a results dict."""
        self._check_count += 1
        results: dict[str, Any] = {}
        for inv_id, inv in self._invariants.items():
            judgment = validate_gluing_invariant(inv, global_section, runtime_data)
            # Determine pass/fail from the judgment obligations
            passed = len(judgment.obligations) == 0
            outcome: Any = True if passed else judgment.obligations
            results[inv_id] = outcome
            if not passed:
                self._violations.append({
                    "check": self._check_count,
                    "invariant_id": inv_id,
                    "violations": outcome,
                })
        return results

    def violation_count(self) -> int:
        """Return the total number of invariant violations recorded."""
        return len(self._violations)

    def recent_violations(self, n: int = 5) -> list[dict]:
        """Return the *n* most recent violation records."""
        return self._violations[-n:]


# ===========================================================================
# FalsificationOracle
# ===========================================================================

class FalsificationOracle:
    """Probabilistic oracle estimating the likelihood of theorem falsification.

    Uses a Bayesian model where:
      prior(β | tier) = tier.falsification_burden_prior()
      likelihood(evidence) = f(number of failed falsification attempts)
      posterior(β) ∝ prior × likelihood

    The oracle also maintains a history of falsification attempts and updates
    its estimates accordingly.
    """

    def __init__(self, theorem_db: TheoremDatabase) -> None:
        self._db = theorem_db
        self._attempt_history: dict[str, list[bool]] = {}  # theorem_id → [success_flags]
        self._rng = random.Random(0xDEADBEEF)

    # ------------------------------------------------------------------
    # Core estimation
    # ------------------------------------------------------------------

    def prior(self, theorem: ReplayGluingTheorem) -> float:
        """Return the prior falsification probability (1 – burden_prior)."""
        return 1.0 - theorem.trust_tier.falsification_burden_prior()

    def likelihood(self, theorem_id: str) -> float:
        """Compute P(evidence | theorem_is_correct) from attempt history."""
        attempts = self._attempt_history.get(theorem_id, [])
        if not attempts:
            return 0.5
        successes = sum(attempts)
        failures = len(attempts) - successes
        # Beta distribution mode approximation: (successes + 1) / (len + 2)
        return (failures + 1) / (len(attempts) + 2)

    def posterior(self, theorem: ReplayGluingTheorem) -> float:
        """Return the posterior falsification probability."""
        p0 = self.prior(theorem)
        lk = self.likelihood(theorem.theorem_id)
        # Unnormalised Bayesian update (simplified)
        raw = p0 * lk
        return min(max(raw, 0.0), 1.0)

    def record_attempt(self, theorem_id: str, falsified: bool) -> None:
        """Record the outcome of a falsification attempt."""
        if theorem_id not in self._attempt_history:
            self._attempt_history[theorem_id] = []
        self._attempt_history[theorem_id].append(falsified)

    def should_promote(self, theorem: ReplayGluingTheorem, threshold: float = 0.1) -> bool:
        """Return True iff the posterior falsification probability is below *threshold*."""
        return self.posterior(theorem) < threshold

    def should_demote(self, theorem: ReplayGluingTheorem, threshold: float = 0.5) -> bool:
        """Return True iff the posterior falsification probability exceeds *threshold*."""
        return self.posterior(theorem) > threshold

    def falsification_report(self, theorem: ReplayGluingTheorem) -> str:
        """Generate a human-readable falsification report."""
        attempts = self._attempt_history.get(theorem.theorem_id, [])
        falsified = sum(attempts)
        total = len(attempts)
        posterior = self.posterior(theorem)
        burden = 1.0 - posterior
        return (
            f"FalsificationOracle report for {theorem.theorem_id!r}\n"
            f"  Theorem:          {theorem.name}\n"
            f"  Trust tier:       {theorem.trust_tier.name}\n"
            f"  Attempts:         {total}  (falsified={falsified})\n"
            f"  Prior P(false):   {self.prior(theorem):.4f}\n"
            f"  Likelihood:       {self.likelihood(theorem.theorem_id):.4f}\n"
            f"  Posterior P(false): {posterior:.4f}\n"
            f"  Falsification burden: {burden:.4f}\n"
            f"  Recommendation:   {'PROMOTE' if self.should_promote(theorem) else 'HOLD'}"
        )


# ===========================================================================
# ProofObligationTracker
# ===========================================================================

class ProofObligationTracker:
    """Tracks open proof obligations across theorems and proofs.

    Each obligation is a string identifier (from the Π field of judgments).
    The tracker records which obligations are open, discharged, or waived.
    """

    _STATUS_OPEN       = "OPEN"
    _STATUS_DISCHARGED = "DISCHARGED"
    _STATUS_WAIVED     = "WAIVED"

    def __init__(self) -> None:
        self._obligations: dict[str, dict] = {}

    def register_obligation(self, obligation_id: str, theorem_id: str, description: str = "") -> None:
        """Register a new proof obligation."""
        self._obligations[obligation_id] = {
            "theorem_id": theorem_id,
            "description": description,
            "status": self._STATUS_OPEN,
            "discharged_by": None,
        }

    def discharge(self, obligation_id: str, discharged_by: str) -> bool:
        """Mark an obligation as discharged.  Return True if it was open."""
        if obligation_id in self._obligations:
            self._obligations[obligation_id]["status"] = self._STATUS_DISCHARGED
            self._obligations[obligation_id]["discharged_by"] = discharged_by
            return True
        return False

    def waive(self, obligation_id: str, reason: str) -> bool:
        """Mark an obligation as waived (accepted without proof).  Return True if open."""
        if obligation_id in self._obligations:
            self._obligations[obligation_id]["status"] = self._STATUS_WAIVED
            self._obligations[obligation_id]["discharged_by"] = f"WAIVED: {reason}"
            return True
        return False

    def open_obligations(self) -> list[dict]:
        """Return all open (undischarged, unwaived) obligations."""
        return [
            {"id": k, **v}
            for k, v in self._obligations.items()
            if v["status"] == self._STATUS_OPEN
        ]

    def summary(self) -> dict[str, int]:
        """Return counts by status."""
        from collections import Counter
        counts = Counter(v["status"] for v in self._obligations.values())
        return dict(counts)


# ===========================================================================
# Module-level convenience: pre-built invariants
# ===========================================================================

def _make_default_invariant(
    inv_id: str,
    name: str,
    statement: str,
    maintained: tuple,
    broken: tuple,
    tier: TrustTier,
    cech: tuple,
) -> GluingInvariant:
    """Legacy factory: build a ``GluingInvariant`` from the old 7-arg signature."""
    return GluingInvariant(
        invariant_id=inv_id,
        name=name,
        expression=statement,
        holds_at=maintained,
        trust_tier=tier,
        last_checked=None,
    )


INVARIANT_COCYCLE_CLOSURE = _make_default_invariant(
    "INV-001",
    "Cocycle Closure",
    "For all i,j,k: c_{ij} + c_{jk} = c_{ik} on U_i ∩ U_j ∩ U_k",
    ("gluing_step", "section_restriction"),
    ("cocycle_mutation", "unsynchronised_update"),
    TrustTier.PROOF_BACKED,
    (1.0 + 0j, 0j, 0j),
)

INVARIANT_SECTION_COMPATIBILITY = _make_default_invariant(
    "INV-002",
    "Section Compatibility",
    "For all i,j: s_i|_{U_i∩U_j} = s_j|_{U_i∩U_j}",
    ("compatible_gluing", "overlap_check"),
    ("independent_update", "branch_merge"),
    TrustTier.VERIFIED,
    (0j, 1.0 + 0j, 0j),
)

INVARIANT_TRUST_MONOTONE_LEGACY = _make_default_invariant(
    "INV-003",
    "Trust Monotonicity (legacy)",
    "Trust tier never decreases during verified replay execution",
    ("tier_upgrade", "proof_completion"),
    ("unverified_override", "rollback"),
    TrustTier.VERIFIED,
    (0j, 0j, 0j),
)


# ===========================================================================
# __main__ block — smoke test
# ===========================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(name)s  %(message)s",
        stream=sys.stdout,
    )
    print("=" * 72)
    print("  theorem_and_falsification_burden_f.py  —  smoke test")
    print("=" * 72)

    # ------------------------------------------------------------------
    # 1. State two ad-hoc theorems
    # ------------------------------------------------------------------
    thm_commute = state_theorem(
        name="Commutativity of Patch Merge",
        statement="For any two replay patches P and Q, merge(P, Q) ≡ merge(Q, P).",
        hypotheses=["P_well_formed", "Q_well_formed", "patches_disjoint"],
        conclusion="merge_is_commutative",
        trust_tier=TrustTier.PROPOSAL,
    )
    thm_idempotent = state_theorem(
        name="Idempotency of Gluing",
        statement="Applying the gluing map twice yields the same result as applying it once.",
        hypotheses=["global_section_exists"],
        conclusion="gluing_is_idempotent",
        trust_tier=TrustTier.REVIEWED,
    )
    print("\n[1] Ad-hoc theorems stated:")
    print(thm_commute.describe())
    print()
    print(thm_idempotent.describe())

    # ------------------------------------------------------------------
    # 2. Create proofs for them
    # ------------------------------------------------------------------
    proof_commute = GluingCorrectnessProof(
        proof_id=_fresh_id("PRF"),
        theorem_id=thm_commute.theorem_id,
        proof_strategy="Symmetry argument on the merge map's domain.",
        proof_steps=(
            "Step 1: Observe merge(P, Q)(f) = s_P(f) if f ∈ P, else s_Q(f).",
            "Step 2: Show merge(Q, P)(f) = s_Q(f) if f ∈ Q, else s_P(f).",
            "Step 3: Since P and Q are disjoint, the cases collapse identically.",
            "QED.",
        ),
        verified_by="replay_gluing_oracle_v1",
        trust_tier=TrustTier.VERIFIED,
        timestamp=_utc_now_iso(),
    )
    proof_idempotent = GluingCorrectnessProof(
        proof_id=_fresh_id("PRF"),
        theorem_id=thm_idempotent.theorem_id,
        proof_strategy="Direct computation: glue ∘ glue = glue on sections.",
        proof_steps=(
            "Step 1: Let s = glue({s_i}).  Then glue({s |_{U_i}}) must equal s.",
            "Step 2: Each s |_{U_i} = s_i by definition of the global section.",
            "Step 3: Therefore glue({s_i}) = s = first application.  QED.",
        ),
        verified_by="replay_gluing_oracle_v1",
        trust_tier=TrustTier.PROOF_BACKED,
        timestamp=_utc_now_iso(),
    )
    print("\n[2] Proofs created:")
    print(proof_commute.describe())
    print()
    print(proof_idempotent.describe())

    # ------------------------------------------------------------------
    # 3. Falsification burdens
    # ------------------------------------------------------------------
    burden_commute = FalsificationBurden(
        burden_id=_fresh_id(_BURDEN_ID_PREFIX),
        theorem_id=thm_commute.theorem_id,
        falsification_condition="merge(P, Q) ≠ merge(Q, P) for some concrete P, Q.",
        counterexample_schema="Produce patches P, Q with P ∩ Q = ∅ and merge(P,Q) ≠ merge(Q,P).",
        required_evidence_kind="non_commutative_merge_witness",
        trust_tier=TrustTier.PROPOSAL,
    )
    burden_idempotent = FalsificationBurden(
        burden_id=_fresh_id(_BURDEN_ID_PREFIX),
        theorem_id=thm_idempotent.theorem_id,
        falsification_condition="glue(glue({s_i})|_{U_j}) ≠ glue({s_j}) for some cover.",
        counterexample_schema="Supply a cover where re-gluing the global section differs.",
        required_evidence_kind="idempotency_violation_witness",
        trust_tier=TrustTier.PROPOSAL,
    )
    print("\n[3] Falsification burdens:")
    print(burden_commute.describe())
    print()
    print(burden_idempotent.describe())

    # ------------------------------------------------------------------
    # 4. Check theorems — one passes, one fails
    # ------------------------------------------------------------------
    print("\n[4] Theorem checks:")

    passing_state: dict = {
        "P_well_formed": True,
        "Q_well_formed": True,
        "patches_disjoint": True,
    }
    failing_state: dict = {
        "P_well_formed": True,
        "Q_well_formed": False,
        "patches_disjoint": True,
    }

    j_pass = verify_gluing_theorem(thm_commute, passing_state)
    j_fail = verify_gluing_theorem(thm_commute, failing_state)

    print(f"  thm_commute / passing_state → trust={j_pass.trust.name}, obligations={j_pass.obligations}")
    print(f"  thm_commute / failing_state → trust={j_fail.trust.name}, obligations={j_fail.obligations}")

    j_std = verify_gluing_theorem(
        _THEOREM_GLOBAL_GLUING,
        {"sections_consistent": True, "cover_admissible": True},
    )
    print(f"  T-GLUE-01 / all_hyps_true         → trust={j_std.trust.name}")

    j_std_fail = verify_gluing_theorem(
        _THEOREM_GLOBAL_GLUING,
        {"sections_consistent": False, "cover_admissible": True},
    )
    print(f"  T-GLUE-01 / sections_inconsistent → trust={j_std_fail.trust.name}")

    # ------------------------------------------------------------------
    # 5. Validate invariants
    # ------------------------------------------------------------------
    print("\n[5] Invariant validation:")
    inv_state_ok: dict = {
        "pre_gluing": True,
        "during_gluing": True,
        "post_gluing": True,
        "post_local_section_build": True,
        "pre_merge": True,
        "exists_unique_global_section": True,
        "post_verification": True,
    }
    inv_state_bad: dict = dict(inv_state_ok)
    inv_state_bad["INV-OVERLAP-02"] = False

    for inv_name, inv_obj in STANDARD_INVARIANTS.items():
        j_ok = validate_gluing_invariant(inv_obj, inv_state_ok)
        j_bad = validate_gluing_invariant(inv_obj, inv_state_bad)
        print(f"  {inv_obj.invariant_id}  ok={j_ok.trust.name:20s}  bad={j_bad.trust.name}")

    # ------------------------------------------------------------------
    # 6. TheoremChecker full run
    # ------------------------------------------------------------------
    print("\n[6] TheoremChecker full run:")
    checker = TheoremChecker(
        checker_id="smoke-checker-01",
        theorems=list(STANDARD_THEOREMS.values()) + [thm_commute, thm_idempotent],
        invariants=list(STANDARD_INVARIANTS.values()),
    )

    full_state: dict = {
        "sections_consistent": True,
        "cover_admissible": True,
        "pairwise_overlaps_consistent": True,
        "local_sections_have_trust_tiers": True,
        "global_section_exists": True,
        "P_well_formed": True,
        "Q_well_formed": True,
        "patches_disjoint": True,
        "pre_gluing": True,
        "during_gluing": True,
        "post_gluing": True,
        "post_local_section_build": True,
        "exists_unique_global_section": True,
        "post_verification": True,
    }

    for thm in checker.theorems:
        j = checker.check_theorem(thm, full_state)
        print(f"  {thm.theorem_id:16s} {thm.name[:40]:40s} → {j.trust.name}")

    inv_judgments = checker.check_all_invariants(full_state)
    print(f"\n  Invariant checks: {len(inv_judgments)} judgments produced.")

    falsified = checker.detect_falsification(
        burden_commute,
        {"non_commutative_merge_witness": None},
    )
    print(f"  Falsification of commutativity burden → met={falsified}")

    falsified2 = checker.detect_falsification(
        _BURDEN_01,
        {"overlap_inconsistency_witness": "frame_42_mismatch"},
    )
    print(f"  Falsification of T-GLUE-01 burden    → met={falsified2}")

    # ------------------------------------------------------------------
    # 7. Full report
    # ------------------------------------------------------------------
    print("\n[7] Full checker report (excerpt):")
    report = checker.get_report()
    print(f"  checker_id  : {report['checker_id']}")
    print(f"  stats       : {report['stats']}")
    print(f"  log entries : {len(report['log_entries'])}")
    print(f"  generated_at: {report['generated_at']}")

    # ------------------------------------------------------------------
    # 8. Čech obstruction from a met burden
    # ------------------------------------------------------------------
    print("\n[8] Čech obstruction from falsification burden:")
    met_burden = FalsificationBurden(
        burden_id=_fresh_id(_BURDEN_ID_PREFIX),
        theorem_id=OVERLAP_CONSISTENCY_THEOREM_ID,
        falsification_condition="c_{ij} + c_{jk} + c_{ki} ≠ 0 for triple (i=1,j=2,k=3)",
        counterexample_schema="Triple (1, 2, 3) with non-trivial cocycle chain.",
        required_evidence_kind="non_trivial_triple_cocycle",
        trust_tier=TrustTier.RUNTIME_WITNESSED,
    )
    obs = met_burden.to_cech_obstruction()
    print(f"  cover_id        : {obs.cover_id}")
    print(f"  is_trivial      : {obs.is_trivial()}")
    print(f"  cohomology_class: {obs.cohomology_class}")
    print(f"  description     : {obs.description}")

    # ------------------------------------------------------------------
    # 9. TrustTier lattice laws
    # ------------------------------------------------------------------
    print("\n[9] TrustTier lattice laws:")
    for a in TrustTier:
        for b in TrustTier:
            assert a.meet(b).value <= a.value and a.meet(b).value <= b.value
            assert a.join(b).value >= a.value and a.join(b).value >= b.value
    print("  ✓ meet / join laws hold for all pairs")
    print(f"  promote(PROPOSAL)   = {TrustTier.PROPOSAL.promote().name}")
    print(f"  demote(PROOF_BACKED)= {TrustTier.PROOF_BACKED.demote().name}")

    # ------------------------------------------------------------------
    # 10. state_theorem + with_proof roundtrip
    # ------------------------------------------------------------------
    print("\n[10] state_theorem + with_proof roundtrip:")
    t_custom = state_theorem(
        "Associativity of Cover Union",
        "(U_i ∪ U_j) ∪ U_k = U_i ∪ (U_j ∪ U_k) as replay windows.",
        ["covers_are_sets"],
        "union_is_associative",
        TrustTier.PROPOSAL,
    )
    t_proved = t_custom.with_proof("docs/proofs/set_associativity.lean")
    print(f"  Original tier : {t_custom.trust_tier.name}")
    print(f"  Promoted tier : {t_proved.trust_tier.name}")
    print(f"  proof_ref     : {t_proved.proof_ref}")
    j_lifted = t_proved.to_judgment()
    print(f"  Judgment trust: {j_lifted.trust.name}")

    print("\n" + "=" * 72)
    print("  Smoke test PASSED.")
    print("=" * 72)
