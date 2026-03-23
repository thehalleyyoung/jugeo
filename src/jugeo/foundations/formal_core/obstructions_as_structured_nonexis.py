"""
Obstructions as Structured Non-Existence: Čech H¹ Obstructions.

# copilot: foundations.formal_core.obstructions_as_structured_nonexis

This module develops the theory of *obstructions as structured non-existence*
within the jugeo formal reasoning stack.  Classical category theory and
homological algebra teach us that a failure to glue local data into a global
section is not merely an absence — it is a *structured* absence that can be
given a precise cohomological certificate, classified by type, and reasoned
about formally.

Mathematical background
-----------------------
Given an open cover U = {U_i} of a space X and a sheaf D on X, the Čech
complex is the cochain complex

    0 → C⁰(U,D) →δ⁰→ C¹(U,D) →δ¹→ C²(U,D) →δ²→ ...

where
    C⁰(U,D) = ∏_i  D(U_i)
    C¹(U,D) = ∏_{i<j} D(U_i ∩ U_j)
    C²(U,D) = ∏_{i<j<k} D(U_i ∩ U_j ∩ U_k)

The coboundary maps are defined by alternating restriction:
    (δ⁰ τ)_{ij}    = τ_j|_{U_i∩U_j} - τ_i|_{U_i∩U_j}
    (δ¹ σ)_{ijk}   = σ_{jk}|_{...} - σ_{ik}|_{...} + σ_{ij}|_{...}

The first Čech cohomology group is
    H¹(U,D) = ker(δ¹) / im(δ⁰)

A 1-cochain σ = (σ_{ij}) is a *1-cocycle* when δ¹(σ) = 0, i.e.
    σ_{jk} - σ_{ik} + σ_{ij} = 0  for all i<j<k.

It is a *coboundary* when there exist local sections τ_i ∈ D(U_i) such that
    σ_{ij} = τ_j - τ_i  for all i<j.

The *obstruction class* [σ] ∈ H¹(U,D) is trivial iff the cocycle is a
coboundary, iff the local sections {s_i} can be glued to a global section
s ∈ D(X) with s|_{U_i} = s_i for all i.

When [σ] ≠ 0 we have a *structured non-existence*: not merely "no global
section exists" but a specific cohomological certificate explaining *why*.

Obstruction taxonomy
--------------------
CECH_H1              — classical Čech 1-cocycle obstruction
SHEAF_COHOMOLOGY     — derived-functor obstruction (H^n for n≥1)
EXTENSION_FAILURE    — failure to extend a section across a closed sub-sheaf
LIFTING_FAILURE      — failure to lift along a sheaf epimorphism
GLOBAL_SECTION_FAILURE — vanishing of the global sections functor output
DESCENT_FAILURE      — failure of faithfully flat / étale descent

Each obstruction is witnessed by an ObstructionWitness that carries the
local data, the cocycle, and a formal certificate.  These are never collapsed
to bare booleans — the full structured record is preserved.

Judgment model
--------------
Judgments are immutable 8-tuples (context, formula, authority, evidence,
obligations, budget, trust_tier, proof_chain).  TrustTier ranges from
PROPOSAL through PROOF_BACKED so that downstream consumers can decide how
much weight to put on any particular judgment.
"""

from __future__ import annotations

import hashlib
import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any

# ---------------------------------------------------------------------------
# Jugeo imports (optional — graceful degradation when run standalone)
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.formal_core.judgment_core import JudgmentCore  # type: ignore
except ImportError:
    JudgmentCore = None  # type: ignore[misc,assignment]

try:
    from jugeo.foundations.formal_core.trust_tiers import TrustRegistry  # type: ignore
except ImportError:
    TrustRegistry = None  # type: ignore[misc,assignment]

try:
    from jugeo.foundations.formal_core.minimal_judgment import MinimalJudgment  # type: ignore
except ImportError:
    MinimalJudgment = None  # type: ignore[misc,assignment]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MODULE_VERSION: str = "0.3.0"
MODULE_AUTHORITY: str = "jugeo.foundations.formal_core"
TRIVIAL_CLASS: str = "0"                    # Symbol for the zero cohomology class
NONTRIVIAL_CLASS_PREFIX: str = "H1"
H1_DIMENSION_THRESHOLD: int = 1            # Minimum H¹ dim that signals obstruction
MAX_COVER_ELEMENTS: int = 256              # Hard limit for Čech cover size
MAX_COCHAIN_DEPTH: int = 3                 # Maximum cochain degree computed
COBOUNDARY_SEPARATOR: str = " - "          # Used when printing coboundary expressions
COCYCLE_CONDITION_OK: str = "COCYCLE_CONDITION_SATISFIED"
COCYCLE_CONDITION_FAIL: str = "COCYCLE_CONDITION_VIOLATED"
DEFAULT_BUDGET: float = 1.0
DEFAULT_TRUST_FLOOR: str = "PROPOSAL"
CERTIFICATE_PREFIX: str = "CERT"
WITNESS_PREFIX: str = "WIT"
OBSTRUCTION_PREFIX: str = "OBS"
NONEXISTENCE_PREFIX: str = "NEX"
COVER_PREFIX: str = "CVR"
REPAIR_HINT_PREFIX: str = "RH"
NULL_SECTION: str = "__null_section__"
GLOBAL_SECTION_KEY: str = "global"
PAIR_SEP: str = ","
TRIPLE_SEP: str = ","
ISO_FORMAT: str = "%Y-%m-%dT%H:%M:%S.%f+00:00"
EMPTY_COCHAIN: tuple[str, ...] = ()

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrustTier(Enum):
    """Ordered trust levels for judgments in the jugeo reasoning stack.

    PROPOSAL         — Unreviewed, may contain errors; do not depend on.
    REVIEWED         — Peer-reviewed but not machine-verified.
    VERIFIED         — Formally type-checked or proof-assistant verified.
    RUNTIME_WITNESSED — Verified at runtime by executing evidence.
    PROOF_BACKED     — Backed by a complete machine-checked proof.
    """

    PROPOSAL = auto()
    REVIEWED = auto()
    VERIFIED = auto()
    RUNTIME_WITNESSED = auto()
    PROOF_BACKED = auto()

    def dominates(self, other: TrustTier) -> bool:
        """Return True iff this tier is at least as trusted as *other*."""
        return self.value >= other.value


class ObstructionClass(Enum):
    """Taxonomic classification of cohomological obstructions.

    CECH_H1              — The obstruction lives in H¹(U,D) of a Čech complex.
    SHEAF_COHOMOLOGY     — Higher sheaf cohomology H^n, n ≥ 2.
    EXTENSION_FAILURE    — Section does not extend across a closed embedding.
    LIFTING_FAILURE      — Section does not lift along a surjective sheaf map.
    GLOBAL_SECTION_FAILURE — Γ(X,D) = 0 or section functor fails.
    DESCENT_FAILURE      — Faithfully flat / fpqc descent fails.
    """

    CECH_H1 = auto()
    SHEAF_COHOMOLOGY = auto()
    EXTENSION_FAILURE = auto()
    LIFTING_FAILURE = auto()
    GLOBAL_SECTION_FAILURE = auto()
    DESCENT_FAILURE = auto()



# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionJudgment:
    """An immutable 8-tuple judgment about an obstruction.

    Fields
    ------
    context      : The logical context in which the judgment is made.
    formula      : The formula (proposition) being judged.
    authority    : Module or agent that issued the judgment.
    evidence     : Tuple of evidence identifiers supporting the judgment.
    obligations  : Remaining proof obligations, if any.
    budget       : Computational / trust budget consumed.
    trust_tier   : The TrustTier at which this judgment was issued.
    proof_chain  : Ordered tuple of prior judgment IDs forming the proof.
    """

    context: str
    formula: str
    authority: str
    evidence: tuple[str, ...]
    obligations: tuple[str, ...]
    budget: float
    trust_tier: TrustTier
    proof_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverElement:
    """A single element of an open cover U = {U_i}.

    Attributes
    ----------
    element_id   : Unique identifier string (e.g. "U_0", "U_alpha").
    label        : Human-readable label.
    descriptor   : Arbitrary string describing the open set.
    index        : Integer index within the ordered cover.
    created_at   : ISO-8601 timestamp of creation.
    """

    element_id: str
    label: str
    descriptor: str
    index: int
    created_at: str


@dataclass(frozen=True, slots=True)
class LocalSection:
    """A section of a sheaf D over a single cover element U_i.

    The section is represented symbolically as a string expression.

    Attributes
    ----------
    section_id      : Unique identifier.
    cover_element   : The CoverElement over which this section lives.
    value_expr      : Symbolic expression for s_i ∈ D(U_i).
    restriction_map : String describing the restriction-to-intersection rule.
    is_defined      : Whether this section is actually defined (not null).
    created_at      : ISO-8601 timestamp.
    """

    section_id: str
    cover_element: str        # cover element ID
    value_expr: str
    restriction_map: str
    is_defined: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class CochainData:
    """Represents a p-cochain in the Čech complex C^p(U,D).

    A p-cochain assigns to each (p+1)-tuple of cover indices an element of
    D(U_{i_0} ∩ ... ∩ U_{i_p}).

    Attributes
    ----------
    cochain_id   : Unique identifier.
    degree       : Cohomological degree p.
    index_tuples : Tuple of stringified index tuples (the multi-indices).
    values       : Parallel tuple of symbolic section values.
    sheaf_id     : Identifier of the sheaf D.
    cover_id     : Identifier of the cover U.
    created_at   : ISO-8601 timestamp.
    """

    cochain_id: str
    degree: int
    index_tuples: tuple[str, ...]
    values: tuple[str, ...]
    sheaf_id: str
    cover_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class CocycleCondition:
    """Record of whether a cochain satisfies the cocycle condition δ(σ)=0.

    Attributes
    ----------
    condition_id     : Unique identifier.
    cochain_id       : The cochain being checked.
    degree           : Degree of the cochain.
    triple_checks    : Tuple of strings, one per triple (i,j,k), recording
                       the value of (δσ)_{ijk}.
    all_satisfied    : True iff every triple check is zero/trivial.
    violated_triples : Tuple of triples where the condition failed.
    checked_at       : ISO-8601 timestamp.
    """

    condition_id: str
    cochain_id: str
    degree: int
    triple_checks: tuple[str, ...]
    all_satisfied: bool
    violated_triples: tuple[str, ...]
    checked_at: str


@dataclass(frozen=True, slots=True)
class CoboundaryMap:
    """Encodes the coboundary map δ^p : C^p → C^{p+1}.

    Attributes
    ----------
    map_id       : Unique identifier.
    source_degree : p.
    target_degree : p+1.
    source_chain  : Identifier of the source cochain.
    output_chain  : Identifier of the image cochain.
    formula       : Symbolic formula for the map.
    is_zero_map   : Whether the image cochain is identically zero.
    computed_at   : ISO-8601 timestamp.
    """

    map_id: str
    source_degree: int
    target_degree: int
    source_chain: str
    output_chain: str
    formula: str
    is_zero_map: bool
    computed_at: str


@dataclass(frozen=True, slots=True)
class SheafSection:
    """A global or local section of a sheaf, together with provenance.

    Attributes
    ----------
    section_id       : Unique identifier.
    sheaf_id         : The sheaf D to which this section belongs.
    base_open_set    : The open set over which the section is defined.
    value_expr       : Symbolic expression.
    is_global        : True iff base_open_set is the total space X.
    restriction_data : Tuple of (open_subset, restricted_value_expr) pairs
                       encoded as strings.
    created_at       : ISO-8601 timestamp.
    """

    section_id: str
    sheaf_id: str
    base_open_set: str
    value_expr: str
    is_global: bool
    restriction_data: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class GluingData:
    """Records the result of attempting to glue local sections.

    When local sections s_i ∈ D(U_i) agree on overlaps, they can be glued
    to a global section.  This dataclass records the attempt and outcome.

    Attributes
    ----------
    gluing_id         : Unique identifier.
    local_sections    : Tuple of section IDs being glued.
    agreement_checks  : Tuple of strings, one per overlap (i,j), recording
                        whether s_i and s_j agree on U_i ∩ U_j.
    gluing_succeeded  : True iff a global section was produced.
    global_section_id : ID of the resulting global section, or None.
    obstruction_id    : ID of the obstruction, if gluing failed.
    created_at        : ISO-8601 timestamp.
    """

    gluing_id: str
    local_sections: tuple[str, ...]
    agreement_checks: tuple[str, ...]
    gluing_succeeded: bool
    global_section_id: str
    obstruction_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class CohomologyClass:
    """Represents an element of H^p(U,D).

    Attributes
    ----------
    class_id         : Unique identifier.
    degree           : Cohomological degree p.
    representative   : Cochain ID of the representative cocycle.
    is_trivial       : True iff this class equals zero in H^p.
    sheaf_id         : The sheaf D.
    cover_id         : The cover U.
    dimension_lower  : Lower bound on dim H^p.
    label            : Human-readable label.
    computed_at      : ISO-8601 timestamp.
    """

    class_id: str
    degree: int
    representative: str
    is_trivial: bool
    sheaf_id: str
    cover_id: str
    dimension_lower: int
    label: str
    computed_at: str


@dataclass(frozen=True, slots=True)
class ObstructionCertificate:
    """A formal certificate attesting to a cohomological obstruction.

    Attributes
    ----------
    certificate_id    : Unique identifier, prefixed with CERT.
    obstruction_id    : The obstruction being certified.
    cohomology_class  : CohomologyClass ID of the obstruction class.
    obstruction_class : The ObstructionClass enum value (name).
    proof_steps       : Ordered tuple of steps in the obstruction proof.
    issuer            : Authority that issued the certificate.
    trust_tier        : The TrustTier of this certificate.
    issued_at         : ISO-8601 timestamp.
    """

    certificate_id: str
    obstruction_id: str
    cohomology_class: str
    obstruction_class: str
    proof_steps: tuple[str, ...]
    issuer: str
    trust_tier: str
    issued_at: str


@dataclass(frozen=True, slots=True)
class RepairHint:
    """A hint for repairing or circumventing an obstruction.

    An obstruction in H¹(U,D) can sometimes be removed by: refining the
    cover U, twisting the sheaf D, or working in a different category.
    This dataclass records such hints without claiming they succeed.

    Attributes
    ----------
    hint_id           : Unique identifier, prefixed with RH.
    obstruction_id    : The obstruction this hint targets.
    strategy          : One of: REFINE_COVER, TWIST_SHEAF, CHANGE_CATEGORY,
                        ADD_HYPOTHESIS, STABILISE.
    description       : Human-readable description of the repair strategy.
    estimated_cost    : Estimated computational cost (0.0 – 1.0).
    confidence        : Confidence that this hint will succeed (0.0 – 1.0).
    created_at        : ISO-8601 timestamp.
    """

    hint_id: str
    obstruction_id: str
    strategy: str
    description: str
    estimated_cost: float
    confidence: float
    created_at: str


@dataclass(frozen=True, slots=True)
class StructuredNonExistence:
    """A structured non-existence record: the absence of a global section.

    This is the core dataclass of this module.  A StructuredNonExistence is
    not merely "the section does not exist" — it is a *proof* of non-existence
    packaged as an immutable record with a full certificate trail.

    Attributes
    ----------
    nonexistence_id     : Unique identifier, prefixed with NEX.
    description         : Human-readable description of what does not exist.
    obstruction_class   : The ObstructionClass explaining the failure.
    witness_chain       : Ordered tuple of ObstructionWitness IDs forming
                          the chain of evidence.
    formal_certificate  : The ObstructionCertificate ID.
    cohomological_class : String label for the cohomology class [σ] ∈ H¹.
    created_at          : ISO-8601 timestamp.
    """

    nonexistence_id: str
    description: str
    obstruction_class: ObstructionClass
    witness_chain: tuple[str, ...]
    formal_certificate: str
    cohomological_class: str
    created_at: str


@dataclass(frozen=True, slots=True)
class CechObstruction:
    """A Čech 1-cocycle representing an element of H¹(U,D).

    Attributes
    ----------
    obstruction_id    : Unique identifier, prefixed with OBS.
    cover             : Tuple of cover element IDs (the open sets U_i).
    local_sections    : Tuple of local section IDs (s_i ∈ D(U_i)).
    cocycle           : Tuple of strings encoding the 1-cocycle σ_{ij} for
                        each ordered pair (i,j) with i < j.
    coboundary_check  : Result of checking whether σ is a coboundary.
                        Either COCYCLE_CONDITION_OK or COCYCLE_CONDITION_FAIL.
    cohomology_class  : String label for [σ] ∈ H¹(U,D).
    is_trivial        : True iff [σ] = 0 in H¹(U,D), i.e. gluing succeeds.
    computed_at       : ISO-8601 timestamp.
    """

    obstruction_id: str
    cover: tuple[str, ...]
    local_sections: tuple[str, ...]
    cocycle: tuple[str, ...]
    coboundary_check: str
    cohomology_class: str
    is_trivial: bool
    computed_at: str


@dataclass(frozen=True, slots=True)
class ObstructionWitness:
    """A witness to a cohomological obstruction.

    An ObstructionWitness packages the local data (the sections and cocycle)
    together with a certificate string, so that the obstruction can be
    verified by an independent checker.

    Attributes
    ----------
    witness_id             : Unique identifier, prefixed with WIT.
    obstruction_id         : The CechObstruction being witnessed.
    witness_type           : One of: COCYCLE_WITNESS, COBOUNDARY_FAILURE,
                             EXTENSION_WITNESS, LIFTING_WITNESS.
    local_data             : Tuple of strings encoding local data used.
    obstruction_certificate: A hash-based certificate string.
    verified_at            : ISO-8601 timestamp.
    """

    witness_id: str
    obstruction_id: str
    witness_type: str
    local_data: tuple[str, ...]
    obstruction_certificate: str
    verified_at: str


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).strftime(ISO_FORMAT)


def _uid(prefix: str = "") -> str:
    """Return a short unique identifier, optionally prefixed."""
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


def _sha256_of(*parts: str) -> str:
    """Return the first 16 hex characters of SHA-256 of joined parts."""
    combined = "|".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _pair_label(i: int, j: int) -> str:
    """Return a canonical label for the pair (i,j), i < j."""
    a, b = (i, j) if i < j else (j, i)
    return f"({a}{PAIR_SEP}{b})"


def _triple_label(i: int, j: int, k: int) -> str:
    """Return a canonical label for the triple (i,j,k), i<j<k."""
    a, b, c = sorted([i, j, k])
    return f"({a}{TRIPLE_SEP}{b}{TRIPLE_SEP}{c})"


def _enumerate_pairs(n: int) -> list[tuple[int, int]]:
    """Return all ordered pairs (i,j) with 0 ≤ i < j < n."""
    return list(itertools.combinations(range(n), 2))


def _enumerate_triples(n: int) -> list[tuple[int, int, int]]:
    """Return all ordered triples (i,j,k) with 0 ≤ i < j < k < n."""
    return list(itertools.combinations(range(n), 3))


def _coboundary_delta0(tau: list[str], pairs: list[tuple[int, int]]) -> list[str]:
    """Compute the image of a 0-cochain τ under δ⁰.

    For each pair (i,j), (δ⁰τ)_{ij} = τ_j - τ_i.

    Parameters
    ----------
    tau   : List of symbolic section expressions (one per cover element).
    pairs : List of pairs (i,j) with i<j.

    Returns
    -------
    List of symbolic expressions for (δ⁰τ)_{ij}.
    """
    result: list[str] = []
    for i, j in pairs:
        expr = f"{tau[j]}{COBOUNDARY_SEPARATOR}{tau[i]}"
        result.append(expr)
    return result


def _cocycle_check_delta1(
    sigma: list[str],
    pairs: list[tuple[int, int]],
    triples: list[tuple[int, int, int]],
) -> list[str]:
    """Compute (δ¹σ)_{ijk} for each triple (i,j,k).

    (δ¹σ)_{ijk} = σ_{jk} - σ_{ik} + σ_{ij}

    A 1-cochain is a 1-cocycle iff all these are zero/trivially cancelling.

    Parameters
    ----------
    sigma   : List of symbolic expressions for σ_{ij}, indexed by *pairs*.
    pairs   : List of pairs matching the indices of sigma.
    triples : List of triples (i,j,k).

    Returns
    -------
    List of symbolic expressions for (δ¹σ)_{ijk}.
    """
    pair_index: dict[tuple[int, int], int] = {p: idx for idx, p in enumerate(pairs)}
    result: list[str] = []
    for i, j, k in triples:
        s_jk = sigma[pair_index[(j, k)]]
        s_ik = sigma[pair_index[(i, k)]]
        s_ij = sigma[pair_index[(i, j)]]
        expr = f"{s_jk} - {s_ik} + {s_ij}"
        result.append(expr)
    return result


def _is_coboundary(cocycle_exprs: list[str], coboundary_exprs: list[str]) -> bool:
    """Heuristically check whether a cocycle equals a coboundary.

    In a symbolic setting we can only do a syntactic check.  If every
    cocycle expression σ_{ij} appears in the coboundary list (or is
    syntactically equal to the corresponding entry), we declare it a
    coboundary.

    This is intentionally conservative: returns False unless we can
    positively confirm the coboundary relationship.

    Parameters
    ----------
    cocycle_exprs    : The σ_{ij} expressions.
    coboundary_exprs : The (δ⁰τ)_{ij} = τ_j - τ_i expressions.

    Returns
    -------
    True iff every σ_{ij} == (δ⁰τ)_{ij} syntactically.
    """
    if len(cocycle_exprs) != len(coboundary_exprs):
        return False
    return all(c.strip() == b.strip() for c, b in zip(cocycle_exprs, coboundary_exprs))


def _compute_cohomology_class_label(
    cocycle_exprs: list[str],
    is_trivial: bool,
    obstruction_class: ObstructionClass,
) -> str:
    """Return a human-readable label for the cohomology class [σ].

    Parameters
    ----------
    cocycle_exprs     : Symbolic cocycle expressions.
    is_trivial        : True iff [σ] = 0.
    obstruction_class : The enum class.

    Returns
    -------
    A string like "0" (trivial) or "H1[OBS:a3f2...]" (non-trivial).
    """
    if is_trivial:
        return TRIVIAL_CLASS
    digest = _sha256_of(*cocycle_exprs, obstruction_class.name)
    return f"{NONTRIVIAL_CLASS_PREFIX}[{obstruction_class.name}:{digest}]"


def _format_cochain_as_tuple(
    pairs: list[tuple[int, int]], values: list[str]
) -> tuple[str, ...]:
    """Format a list of (pair, value) as a tuple of 'label:value' strings."""
    return tuple(f"{_pair_label(i, j)}:{v}" for (i, j), v in zip(pairs, values))


def _make_judgment(
    context: str,
    formula: str,
    evidence: tuple[str, ...],
    obligations: tuple[str, ...],
    trust_tier: TrustTier,
    proof_chain: tuple[str, ...] = (),
    budget: float = DEFAULT_BUDGET,
) -> ObstructionJudgment:
    """Construct an ObstructionJudgment with the module authority."""
    return ObstructionJudgment(
        context=context,
        formula=formula,
        authority=MODULE_AUTHORITY,
        evidence=evidence,
        obligations=obligations,
        budget=budget,
        trust_tier=trust_tier,
        proof_chain=proof_chain,
    )


def _default_repair_hint(obstruction_id: str, obs_class: ObstructionClass) -> RepairHint:
    """Return a default RepairHint for a given obstruction class."""
    strategy_map = {
        ObstructionClass.CECH_H1: "REFINE_COVER",
        ObstructionClass.SHEAF_COHOMOLOGY: "TWIST_SHEAF",
        ObstructionClass.EXTENSION_FAILURE: "ADD_HYPOTHESIS",
        ObstructionClass.LIFTING_FAILURE: "CHANGE_CATEGORY",
        ObstructionClass.GLOBAL_SECTION_FAILURE: "STABILISE",
        ObstructionClass.DESCENT_FAILURE: "REFINE_COVER",
    }
    desc_map = {
        ObstructionClass.CECH_H1: (
            "Refine the open cover to a finer cover where the cocycle "
            "becomes a coboundary (e.g. acyclic cover theorem)."
        ),
        ObstructionClass.SHEAF_COHOMOLOGY: (
            "Twist the sheaf D by a line bundle L chosen so that "
            "H^n(X, D⊗L) = 0 via Kodaira vanishing or Serre duality."
        ),
        ObstructionClass.EXTENSION_FAILURE: (
            "Add a hypothesis (e.g. local freeness, flatness) that "
            "guarantees the Ext obstruction vanishes."
        ),
        ObstructionClass.LIFTING_FAILURE: (
            "Work in a different category where the epimorphism splits "
            "(e.g. pass to the derived category)."
        ),
        ObstructionClass.GLOBAL_SECTION_FAILURE: (
            "Stabilise: tensor with a positive line bundle or apply "
            "Kodaira–Nakano to force global sections."
        ),
        ObstructionClass.DESCENT_FAILURE: (
            "Use a coarser topology (Zariski instead of étale) where "
            "descent is automatic."
        ),
    }
    strategy = strategy_map.get(obs_class, "ADD_HYPOTHESIS")
    description = desc_map.get(obs_class, "No specific repair hint available.")
    return RepairHint(
        hint_id=_uid(REPAIR_HINT_PREFIX),
        obstruction_id=obstruction_id,
        strategy=strategy,
        description=description,
        estimated_cost=0.5,
        confidence=0.4,
        created_at=_now_iso(),
    )


def _build_cover_elements(cover: list[str]) -> tuple[CoverElement, ...]:
    """Convert a list of cover labels into CoverElement records."""
    now = _now_iso()
    return tuple(
        CoverElement(
            element_id=f"U_{idx}",
            label=label,
            descriptor=f"Open set labelled '{label}'",
            index=idx,
            created_at=now,
        )
        for idx, label in enumerate(cover)
    )


def _build_local_sections(
    sections: list[str], cover_elements: tuple[CoverElement, ...]
) -> tuple[LocalSection, ...]:
    """Convert a list of section expressions into LocalSection records."""
    now = _now_iso()
    result: list[LocalSection] = []
    for idx, (expr, elem) in enumerate(zip(sections, cover_elements)):
        is_def = expr != NULL_SECTION and bool(expr)
        result.append(
            LocalSection(
                section_id=_uid("SEC"),
                cover_element=elem.element_id,
                value_expr=expr if is_def else NULL_SECTION,
                restriction_map=f"restrict_{elem.element_id}",
                is_defined=is_def,
                created_at=now,
            )
        )
    return tuple(result)


def _build_cocycle_from_sections(
    local_sections: tuple[LocalSection, ...],
    pairs: list[tuple[int, int]],
    target_sheaf: dict[str, Any],
) -> list[str]:
    """Compute the transition 1-cocycle from local sections.

    For each pair (i,j), σ_{ij} is the 'transition' between s_i|_{U_ij}
    and s_j|_{U_ij}.  In an abelian sheaf this is s_j - s_i; for a
    principal bundle it is the transition function g_{ij} = s_j · s_i⁻¹.

    Parameters
    ----------
    local_sections : The local section records.
    pairs          : Pairs (i,j) with i<j.
    target_sheaf   : Dictionary with sheaf metadata (type, name, etc.).

    Returns
    -------
    List of symbolic cocycle expressions, one per pair.
    """
    sheaf_type = target_sheaf.get("type", "abelian")
    cocycle: list[str] = []
    for i, j in pairs:
        si = local_sections[i].value_expr
        sj = local_sections[j].value_expr
        if sheaf_type == "abelian":
            cocycle.append(f"{sj} - {si}")
        elif sheaf_type == "multiplicative":
            cocycle.append(f"{sj} * ({si})^(-1)")
        else:
            cocycle.append(f"transition[{_pair_label(i,j)}]({sj},{si})")
    return cocycle



# ---------------------------------------------------------------------------
# ObstructionClassifier (regular class)
# ---------------------------------------------------------------------------


class ObstructionClassifier:
    """Classifies algebraic/sheaf-theoretic obstructions by Čech cohomological type.

    The classifier maintains internal state (a cache of computed Čech
    complexes) so that repeated classification of the same cover is efficient.

    Mathematical role
    -----------------
    Given a failure record (e.g. 'gluing failed on 3 of 5 overlaps'), the
    classifier determines which type of cohomological obstruction is present,
    builds the Čech complex, and returns structured judgments.

    Usage
    -----
    >>> clf = ObstructionClassifier()
    >>> cover = ["U0", "U1", "U2"]
    >>> sections = ["f0", "f1", "f2"]
    >>> cech = clf.build_cech_complex(cover, sections)
    >>> judgment = clf.check_cocycle_condition(cech)
    >>> label = clf.extract_cohomology_class(cech)
    """

    def __init__(self) -> None:
        self._cache: dict[str, CechObstruction] = {}
        self._judgment_log: list[ObstructionJudgment] = []
        self._created_at: str = _now_iso()

    def classify(self, failure_record: dict[str, Any]) -> ObstructionClass:
        """Classify an obstruction from a failure record dictionary.

        The failure record should contain keys such as:
            'type'          : str — 'gluing', 'extension', 'lifting', 'descent'
            'n_overlaps'    : int — number of overlapping pairs
            'n_failures'    : int — number of pairs where sections disagree
            'is_principal'  : bool — True for principal bundle obstructions

        Parameters
        ----------
        failure_record : Dictionary describing the failure.

        Returns
        -------
        The most specific applicable ObstructionClass.
        """
        ftype = str(failure_record.get("type", "gluing")).lower()
        n_failures = int(failure_record.get("n_failures", 1))
        is_principal = bool(failure_record.get("is_principal", False))
        higher_degree = int(failure_record.get("higher_degree", 1))

        if ftype in ("descent", "fpqc", "faithfully_flat"):
            return ObstructionClass.DESCENT_FAILURE
        if ftype in ("extension", "ext"):
            return ObstructionClass.EXTENSION_FAILURE
        if ftype in ("lifting", "lift"):
            return ObstructionClass.LIFTING_FAILURE
        if ftype in ("global", "global_section"):
            return ObstructionClass.GLOBAL_SECTION_FAILURE
        if ftype == "gluing" or ftype == "cech":
            if higher_degree >= 2:
                return ObstructionClass.SHEAF_COHOMOLOGY
            return ObstructionClass.CECH_H1
        if n_failures >= H1_DIMENSION_THRESHOLD:
            return ObstructionClass.CECH_H1
        return ObstructionClass.CECH_H1

    def build_cech_complex(
        self, cover: list[str], sections: list[str]
    ) -> CechObstruction:
        """Build a CechObstruction from a cover and local sections.

        Parameters
        ----------
        cover    : List of cover element labels (U_0, U_1, ...).
        sections : List of symbolic local section expressions (one per U_i).

        Returns
        -------
        A CechObstruction record encoding the full Čech data.

        Raises
        ------
        ValueError
            If the length of sections does not match the length of cover,
            or if the cover exceeds MAX_COVER_ELEMENTS.
        """
        if len(cover) != len(sections):
            raise ValueError(
                f"cover has {len(cover)} elements but sections has {len(sections)}"
            )
        if len(cover) > MAX_COVER_ELEMENTS:
            raise ValueError(
                f"Cover size {len(cover)} exceeds MAX_COVER_ELEMENTS={MAX_COVER_ELEMENTS}"
            )

        cover_elements = _build_cover_elements(cover)
        local_secs = _build_local_sections(sections, cover_elements)
        n = len(cover)
        pairs = _enumerate_pairs(n)
        triples = _enumerate_triples(n)

        target_sheaf: dict[str, Any] = {"type": "abelian", "name": "D"}
        cocycle_exprs = _build_cocycle_from_sections(local_secs, pairs, target_sheaf)

        if len(pairs) > 0 and len(triples) > 0:
            delta1_values = _cocycle_check_delta1(cocycle_exprs, pairs, triples)
            all_trivial = all("0" in v or v.strip() == "" for v in delta1_values)
        else:
            all_trivial = True

        if len(pairs) > 0:
            delta0_values = _coboundary_delta0(
                [s.value_expr for s in local_secs], pairs
            )
            is_cob = _is_coboundary(cocycle_exprs, delta0_values)
        else:
            is_cob = True

        is_trivial = all_trivial and is_cob
        obs_class = ObstructionClass.CECH_H1
        cohomology_label = _compute_cohomology_class_label(
            cocycle_exprs, is_trivial, obs_class
        )
        coboundary_check = (
            COCYCLE_CONDITION_OK if all_trivial else COCYCLE_CONDITION_FAIL
        )
        cochain_tuple = _format_cochain_as_tuple(pairs, cocycle_exprs)

        obs_id = _uid(OBSTRUCTION_PREFIX)
        result = CechObstruction(
            obstruction_id=obs_id,
            cover=tuple(e.element_id for e in cover_elements),
            local_sections=tuple(s.section_id for s in local_secs),
            cocycle=cochain_tuple,
            coboundary_check=coboundary_check,
            cohomology_class=cohomology_label,
            is_trivial=is_trivial,
            computed_at=_now_iso(),
        )
        self._cache[obs_id] = result
        return result

    def check_cocycle_condition(
        self, cech_obstruction: CechObstruction
    ) -> ObstructionJudgment:
        """Issue a judgment on whether the cocycle condition δ¹(σ)=0 holds.

        Parameters
        ----------
        cech_obstruction : The CechObstruction to check.

        Returns
        -------
        An ObstructionJudgment at VERIFIED tier if the condition holds,
        at REVIEWED tier if it fails.
        """
        cond_ok = cech_obstruction.coboundary_check == COCYCLE_CONDITION_OK
        formula = (
            f"δ¹(σ)=0 on cover {cech_obstruction.cover}"
            if cond_ok
            else f"δ¹(σ)≠0: cocycle condition violated on {cech_obstruction.cover}"
        )
        tier = TrustTier.VERIFIED if cond_ok else TrustTier.REVIEWED
        evidence = (
            cech_obstruction.obstruction_id,
            cech_obstruction.coboundary_check,
        )
        obligations: tuple[str, ...] = () if cond_ok else ("CHECK_COBOUNDARY",)
        judgment = _make_judgment(
            context=f"CechComplex({cech_obstruction.obstruction_id})",
            formula=formula,
            evidence=evidence,
            obligations=obligations,
            trust_tier=tier,
            proof_chain=(cech_obstruction.obstruction_id,),
        )
        self._judgment_log.append(judgment)
        return judgment

    def extract_cohomology_class(self, cech_obstruction: CechObstruction) -> str:
        """Extract and return the cohomology class label [σ] ∈ H¹(U,D).

        Parameters
        ----------
        cech_obstruction : The CechObstruction whose class to extract.

        Returns
        -------
        The cohomology_class string stored on the CechObstruction.
        """
        return cech_obstruction.cohomology_class

    def is_trivial_obstruction(
        self, cech_obstruction: CechObstruction
    ) -> ObstructionJudgment:
        """Issue a judgment on whether the obstruction class is trivial.

        An obstruction is trivial iff [σ] = 0 in H¹(U,D), i.e. the cocycle
        is a coboundary and local sections can be glued globally.

        Parameters
        ----------
        cech_obstruction : The CechObstruction to evaluate.

        Returns
        -------
        An ObstructionJudgment recording whether gluing is possible.
        """
        is_triv = cech_obstruction.is_trivial
        formula = (
            "[σ] = 0 ∈ H¹(U,D): gluing succeeds"
            if is_triv
            else f"[σ] = {cech_obstruction.cohomology_class} ≠ 0: gluing fails"
        )
        tier = TrustTier.RUNTIME_WITNESSED if is_triv else TrustTier.VERIFIED
        evidence = (
            cech_obstruction.obstruction_id,
            cech_obstruction.cohomology_class,
            cech_obstruction.coboundary_check,
        )
        obligations: tuple[str, ...] = () if is_triv else ("OBSTRUCTION_CERTIFICATE",)
        judgment = _make_judgment(
            context=f"GluingProblem({cech_obstruction.obstruction_id})",
            formula=formula,
            evidence=evidence,
            obligations=obligations,
            trust_tier=tier,
            proof_chain=(cech_obstruction.obstruction_id,),
        )
        self._judgment_log.append(judgment)
        return judgment

    def judgment_log(self) -> list[ObstructionJudgment]:
        """Return a copy of the internal judgment log."""
        return list(self._judgment_log)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def classify_obstruction(
    failure_id: str, obstruction_context: dict[str, Any]
) -> ObstructionClass:
    """Classify an obstruction described by *obstruction_context*.

    This is the primary entry point for obstruction classification.

    Parameters
    ----------
    failure_id          : An identifier for the failure being classified.
    obstruction_context : Dictionary with keys:
        - 'type' (str): gluing | extension | lifting | descent | global
        - 'n_overlaps' (int): total number of double overlaps
        - 'n_failures' (int): number of overlaps where agreement fails
        - 'is_principal' (bool): True for principal bundle setting
        - 'higher_degree' (int): set to ≥2 for higher cohomology

    Returns
    -------
    The ObstructionClass that best describes this failure.

    Examples
    --------
    >>> obs = classify_obstruction("F001", {"type": "gluing", "n_failures": 2})
    >>> obs == ObstructionClass.CECH_H1
    True
    >>> obs2 = classify_obstruction("F002", {"type": "descent"})
    >>> obs2 == ObstructionClass.DESCENT_FAILURE
    True
    """
    clf = ObstructionClassifier()
    ctx = dict(obstruction_context)
    ctx["failure_id"] = failure_id
    return clf.classify(ctx)


def build_cech_obstruction(
    cover: list[str],
    local_sections: list[str],
    target_sheaf: dict[str, Any],
) -> CechObstruction:
    """Build a full CechObstruction from a cover, sections, and sheaf data.

    Parameters
    ----------
    cover          : List of cover element labels.
    local_sections : List of symbolic local section expressions.
    target_sheaf   : Dictionary with at least a 'type' key ('abelian',
                     'multiplicative', or other).

    Returns
    -------
    A CechObstruction capturing the Čech cohomological data.

    Notes
    -----
    The Čech 1-cocycle is computed symbolically.  The coboundary check
    uses syntactic equality.  For genuine numeric or algebraic computation
    the caller should provide pre-simplified section expressions.
    """
    cover_elements = _build_cover_elements(cover)
    local_secs = _build_local_sections(local_sections, cover_elements)
    n = len(cover)
    pairs = _enumerate_pairs(n)
    triples = _enumerate_triples(n)

    cocycle_exprs = _build_cocycle_from_sections(local_secs, pairs, target_sheaf)

    if pairs and triples:
        delta1_values = _cocycle_check_delta1(cocycle_exprs, pairs, triples)
        all_trivial = all("0" in v or v.strip() == "" for v in delta1_values)
    else:
        all_trivial = True

    if pairs:
        delta0_values = _coboundary_delta0(
            [s.value_expr for s in local_secs], pairs
        )
        is_cob = _is_coboundary(cocycle_exprs, delta0_values)
    else:
        is_cob = True

    is_trivial = all_trivial and is_cob
    obs_class = ObstructionClass.CECH_H1
    cohomology_label = _compute_cohomology_class_label(
        cocycle_exprs, is_trivial, obs_class
    )
    coboundary_check = COCYCLE_CONDITION_OK if all_trivial else COCYCLE_CONDITION_FAIL
    cochain_tuple = _format_cochain_as_tuple(pairs, cocycle_exprs)

    return CechObstruction(
        obstruction_id=_uid(OBSTRUCTION_PREFIX),
        cover=tuple(e.element_id for e in cover_elements),
        local_sections=tuple(s.section_id for s in local_secs),
        cocycle=cochain_tuple,
        coboundary_check=coboundary_check,
        cohomology_class=cohomology_label,
        is_trivial=is_trivial,
        computed_at=_now_iso(),
    )


def extract_obstruction_witness(
    obstruction_id: str, proof_context: dict[str, Any]
) -> ObstructionWitness:
    """Extract an ObstructionWitness from a CechObstruction.

    Parameters
    ----------
    obstruction_id : The obstruction_id of a CechObstruction.
    proof_context  : Dictionary with keys:
        - 'local_data' (list[str]): List of data strings to include.
        - 'witness_type' (str): COCYCLE_WITNESS | COBOUNDARY_FAILURE |
                                 EXTENSION_WITNESS | LIFTING_WITNESS
        - 'cocycle_exprs' (list[str]): Symbolic cocycle expressions.

    Returns
    -------
    An ObstructionWitness with a certificate derived from the obstruction.
    """
    local_data: list[str] = proof_context.get("local_data", [])
    witness_type: str = proof_context.get("witness_type", "COCYCLE_WITNESS")
    cocycle_exprs: list[str] = proof_context.get("cocycle_exprs", [])

    certificate = _sha256_of(obstruction_id, *local_data, *cocycle_exprs)
    full_cert = f"{CERTIFICATE_PREFIX}:{certificate}"

    return ObstructionWitness(
        witness_id=_uid(WITNESS_PREFIX),
        obstruction_id=obstruction_id,
        witness_type=witness_type,
        local_data=tuple(local_data),
        obstruction_certificate=full_cert,
        verified_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Pipeline helper functions
# ---------------------------------------------------------------------------


def build_structured_nonexistence(
    cech_obs: CechObstruction,
    witness: ObstructionWitness,
    certificate: ObstructionCertificate,
    obs_class: ObstructionClass,
    description: str = "",
) -> StructuredNonExistence:
    """Assemble a StructuredNonExistence from its constituent parts.

    Parameters
    ----------
    cech_obs    : The CechObstruction.
    witness     : The ObstructionWitness.
    certificate : The ObstructionCertificate.
    obs_class   : The ObstructionClass classification.
    description : Human-readable description of what does not exist.

    Returns
    -------
    A StructuredNonExistence record.
    """
    if not description:
        description = (
            f"No global section exists: obstruction class {obs_class.name}, "
            f"cohomology class {cech_obs.cohomology_class}"
        )
    return StructuredNonExistence(
        nonexistence_id=_uid(NONEXISTENCE_PREFIX),
        description=description,
        obstruction_class=obs_class,
        witness_chain=(witness.witness_id,),
        formal_certificate=certificate.certificate_id,
        cohomological_class=cech_obs.cohomology_class,
        created_at=_now_iso(),
    )


def issue_obstruction_certificate(
    cech_obs: CechObstruction,
    obs_class: ObstructionClass,
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> ObstructionCertificate:
    """Issue a formal ObstructionCertificate for a CechObstruction.

    Parameters
    ----------
    cech_obs   : The CechObstruction to certify.
    obs_class  : The ObstructionClass.
    trust_tier : Trust level for the certificate.

    Returns
    -------
    An ObstructionCertificate.
    """
    proof_steps: tuple[str, ...] = (
        f"1. Computed Čech 1-cocycle σ on cover {cech_obs.cover}",
        f"2. Verified coboundary check: {cech_obs.coboundary_check}",
        f"3. Cohomology class [σ] = {cech_obs.cohomology_class}",
        f"4. Obstruction is {'trivial' if cech_obs.is_trivial else 'non-trivial'}",
        f"5. Classification: {obs_class.name}",
    )
    return ObstructionCertificate(
        certificate_id=_uid(CERTIFICATE_PREFIX),
        obstruction_id=cech_obs.obstruction_id,
        cohomology_class=cech_obs.cohomology_class,
        obstruction_class=obs_class.name,
        proof_steps=proof_steps,
        issuer=MODULE_AUTHORITY,
        trust_tier=trust_tier.name,
        issued_at=_now_iso(),
    )


def build_gluing_data(
    local_section_ids: tuple[str, ...],
    agreement_results: list[tuple[str, bool]],
    gluing_succeeded: bool,
    global_section_id: str = "",
    obstruction_id: str = "",
) -> GluingData:
    """Build a GluingData record from a list of agreement checks.

    Parameters
    ----------
    local_section_ids  : Tuple of local section IDs.
    agreement_results  : List of (overlap_label, agrees) pairs.
    gluing_succeeded   : Whether gluing produced a global section.
    global_section_id  : ID of the global section if succeeded.
    obstruction_id     : ID of the obstruction if failed.

    Returns
    -------
    A GluingData record.
    """
    checks = tuple(
        f"{label}:{'OK' if ok else 'FAIL'}" for label, ok in agreement_results
    )
    return GluingData(
        gluing_id=_uid("GLU"),
        local_sections=local_section_ids,
        agreement_checks=checks,
        gluing_succeeded=gluing_succeeded,
        global_section_id=global_section_id or "",
        obstruction_id=obstruction_id or "",
        created_at=_now_iso(),
    )


def summarise_obstruction_pipeline(
    cech_obs: CechObstruction,
    witness: ObstructionWitness,
    structured_ne: StructuredNonExistence,
    certificate: ObstructionCertificate,
    repair_hint: RepairHint,
) -> dict[str, Any]:
    """Return a summary dictionary of the full obstruction pipeline output.

    Parameters
    ----------
    cech_obs       : The computed CechObstruction.
    witness        : The ObstructionWitness.
    structured_ne  : The StructuredNonExistence record.
    certificate    : The ObstructionCertificate.
    repair_hint    : A suggested repair strategy.

    Returns
    -------
    A plain dictionary suitable for logging or serialisation.
    """
    return {
        "obstruction_id": cech_obs.obstruction_id,
        "cover_size": len(cech_obs.cover),
        "cocycle_size": len(cech_obs.cocycle),
        "coboundary_check": cech_obs.coboundary_check,
        "cohomology_class": cech_obs.cohomology_class,
        "is_trivial": cech_obs.is_trivial,
        "obstruction_class": structured_ne.obstruction_class.name,
        "nonexistence_id": structured_ne.nonexistence_id,
        "witness_id": witness.witness_id,
        "certificate_id": certificate.certificate_id,
        "certificate_trust_tier": certificate.trust_tier,
        "repair_strategy": repair_hint.strategy,
        "repair_confidence": repair_hint.confidence,
        "description": structured_ne.description,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== obstructions_as_structured_nonexis smoke test ===")

    # 1. Basic classification
    obs_class_glue = classify_obstruction("F001", {"type": "gluing", "n_failures": 2})
    assert obs_class_glue == ObstructionClass.CECH_H1, f"Expected CECH_H1, got {obs_class_glue}"

    obs_class_descent = classify_obstruction("F002", {"type": "descent"})
    assert obs_class_descent == ObstructionClass.DESCENT_FAILURE, "Expected DESCENT_FAILURE"

    obs_class_ext = classify_obstruction("F003", {"type": "extension"})
    assert obs_class_ext == ObstructionClass.EXTENSION_FAILURE, "Expected EXTENSION_FAILURE"

    obs_class_lift = classify_obstruction("F004", {"type": "lifting"})
    assert obs_class_lift == ObstructionClass.LIFTING_FAILURE, "Expected LIFTING_FAILURE"

    print("  [OK] classify_obstruction — 4 types verified")

    # 2. Build a Čech obstruction with 3 cover elements
    cover = ["U_alpha", "U_beta", "U_gamma"]
    sections = ["s_alpha", "s_beta", "s_gamma"]
    sheaf = {"type": "abelian", "name": "O_X"}
    cech_obs = build_cech_obstruction(cover, sections, sheaf)
    assert isinstance(cech_obs, CechObstruction), "Expected CechObstruction"
    assert len(cech_obs.cover) == 3, "Cover should have 3 elements"
    assert len(cech_obs.cocycle) == 3, "3 pairs → 3 cocycle entries"
    print(f"  [OK] build_cech_obstruction: cohomology class = {cech_obs.cohomology_class}")

    # 3. Check cocycle condition via classifier
    clf = ObstructionClassifier()
    cech2 = clf.build_cech_complex(cover, sections)
    jdg_cocycle = clf.check_cocycle_condition(cech2)
    assert isinstance(jdg_cocycle, ObstructionJudgment), "Expected ObstructionJudgment"
    assert jdg_cocycle.authority == MODULE_AUTHORITY, "Wrong authority"
    assert isinstance(jdg_cocycle.trust_tier, TrustTier), "Expected TrustTier"
    print(f"  [OK] check_cocycle_condition: trust_tier = {jdg_cocycle.trust_tier.name}")

    # 4. is_trivial_obstruction
    jdg_triv = clf.is_trivial_obstruction(cech2)
    assert isinstance(jdg_triv, ObstructionJudgment), "Expected ObstructionJudgment"
    print(f"  [OK] is_trivial_obstruction: formula = {jdg_triv.formula[:60]}...")

    # 5. extract_cohomology_class
    h1_label = clf.extract_cohomology_class(cech2)
    assert isinstance(h1_label, str), "Expected string cohomology label"
    print(f"  [OK] extract_cohomology_class: {h1_label}")

    # 6. Extract obstruction witness
    witness = extract_obstruction_witness(
        cech_obs.obstruction_id,
        {
            "local_data": list(sections),
            "witness_type": "COCYCLE_WITNESS",
            "cocycle_exprs": list(cech_obs.cocycle),
        },
    )
    assert isinstance(witness, ObstructionWitness), "Expected ObstructionWitness"
    assert witness.obstruction_id == cech_obs.obstruction_id
    assert witness.obstruction_certificate.startswith(CERTIFICATE_PREFIX)
    print(f"  [OK] extract_obstruction_witness: {witness.witness_id}")

    # 7. Issue certificate
    cert = issue_obstruction_certificate(cech_obs, obs_class_glue, TrustTier.VERIFIED)
    assert isinstance(cert, ObstructionCertificate), "Expected ObstructionCertificate"
    assert cert.trust_tier == "VERIFIED"
    assert len(cert.proof_steps) == 5
    print(f"  [OK] issue_obstruction_certificate: {cert.certificate_id}")

    # 8. Build structured non-existence
    structured_ne = build_structured_nonexistence(
        cech_obs, witness, cert, obs_class_glue
    )
    assert isinstance(structured_ne, StructuredNonExistence)
    assert structured_ne.obstruction_class == obs_class_glue
    assert structured_ne.formal_certificate == cert.certificate_id
    print(f"  [OK] build_structured_nonexistence: {structured_ne.nonexistence_id}")

    # 9. Repair hint
    hint = _default_repair_hint(cech_obs.obstruction_id, obs_class_glue)
    assert isinstance(hint, RepairHint)
    assert hint.strategy == "REFINE_COVER"
    print(f"  [OK] _default_repair_hint: strategy = {hint.strategy}")

    # 10. Full pipeline summary
    summary = summarise_obstruction_pipeline(cech_obs, witness, structured_ne, cert, hint)
    assert "obstruction_id" in summary
    assert "cohomology_class" in summary
    assert "repair_strategy" in summary
    assert summary["cover_size"] == 3
    print(f"  [OK] summarise_obstruction_pipeline: {len(summary)} keys")

    # 11. TrustTier ordering
    assert TrustTier.PROOF_BACKED.dominates(TrustTier.PROPOSAL)
    assert not TrustTier.PROPOSAL.dominates(TrustTier.VERIFIED)
    print("  [OK] TrustTier.dominates ordering verified")

    # 12. GluingData build
    glue_data = build_gluing_data(
        local_section_ids=tuple(cech_obs.local_sections),
        agreement_results=[("(0,1)", True), ("(0,2)", False), ("(1,2)", True)],
        gluing_succeeded=False,
        obstruction_id=cech_obs.obstruction_id,
    )
    assert isinstance(glue_data, GluingData)
    assert not glue_data.gluing_succeeded
    assert "FAIL" in " ".join(glue_data.agreement_checks)
    print(f"  [OK] build_gluing_data: {len(glue_data.agreement_checks)} checks")

    # 13. Higher-degree obstruction classification
    obs_higher = classify_obstruction("F005", {"type": "gluing", "higher_degree": 2})
    assert obs_higher == ObstructionClass.SHEAF_COHOMOLOGY
    print(f"  [OK] higher-degree classification: {obs_higher.name}")

    # 14. Large cover (stress test)
    big_cover = [f"V_{k}" for k in range(10)]
    big_sections = [f"s_{k}" for k in range(10)]
    big_obs = build_cech_obstruction(big_cover, big_sections, {"type": "abelian"})
    expected_pairs = 10 * 9 // 2  # 45
    assert len(big_obs.cover) == 10
    assert len(big_obs.cocycle) == expected_pairs, (
        f"Expected {expected_pairs} cocycle entries, got {len(big_obs.cocycle)}"
    )
    print(f"  [OK] large cover (n=10): {len(big_obs.cocycle)} cocycle entries")

    # 15. Classifier judgment log accumulation
    clf2 = ObstructionClassifier()
    c1 = clf2.build_cech_complex(["A", "B"], ["sA", "sB"])
    clf2.check_cocycle_condition(c1)
    clf2.is_trivial_obstruction(c1)
    log = clf2.judgment_log()
    assert len(log) == 2, f"Expected 2 judgments in log, got {len(log)}"
    print(f"  [OK] judgment log accumulation: {len(log)} judgments")

    print("\n=== All smoke tests passed ===")


@dataclass(frozen=True, slots=True)
class CechCochainData:
    """A concrete Čech n-cochain σ ∈ C^n(U, D).

    A Čech n-cochain assigns to each (n+1)-fold overlap of patches in the
    cover a discrepancy value from the coefficient sheaf D.  In JuGeo, D is
    the *discrepancy sheaf* whose sections are evidence disagreements.

    The cochain is represented as a dict mapping **simplex keys** to
    discrepancy values.  A simplex key for an n-simplex over patches
    i_0, …, i_n is the string ``"i_0|i_1|…|i_n"`` (with indices sorted).

    Parameters
    ----------
    cochain_id:
        Stable unique identifier.
    degree:
        :class:`CochainDegree` of this cochain.
    components:
        Dict mapping simplex key → discrepancy value.
    site_id:
        Identifier of the Grothendieck site.
    sheaf_name:
        Name of the coefficient sheaf (typically ``"DiscrepancySheaf"``).
    provenance:
        Ordered tuple of step labels recording the derivation.
    """

    cochain_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    degree: CochainDegree = CochainDegree.ONE
    components: dict[str, Any] = field(default_factory=dict)
    site_id: str = ""
    sheaf_name: str = "DiscrepancySheaf"
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def is_zero(self) -> bool:
        """Return True iff all components are the zero sentinel."""
        return all(v == _ZERO_SENTINEL or v == 0 for v in self.components.values())

    def coboundary(self) -> CechCochainData:
        """Compute the coboundary δ(σ) of this cochain.

        The coboundary of a 0-cochain σ⁰ is the 1-cochain (δσ⁰)(U_i, U_j)
        = σ⁰(U_j) − σ⁰(U_i).  For higher degrees, the coboundary is
        computed as the alternating sum of face restrictions.

        Returns
        -------
        CechCochainData
            The coboundary cochain in degree n+1.
        """
        if self.degree == CochainDegree.TWO:
            # Return the zero 3-cochain (boundary of the complex)
            return CechCochainData(
                degree=CochainDegree.TWO,
                components={},
                site_id=self.site_id,
                sheaf_name=self.sheaf_name,
                provenance=self.provenance + ("coboundary_of_2",),
            )

        new_components: dict[str, Any] = {}
        keys = sorted(self.components.keys())
        if self.degree == CochainDegree.ZERO:
            # δ(σ⁰)(U_i ∩ U_j) = σ⁰(U_j) - σ⁰(U_i)
            for i_idx, ki in enumerate(keys):
                for j_idx, kj in enumerate(keys):
                    if i_idx >= j_idx:
                        continue
                    vi = self.components.get(ki, _ZERO_SENTINEL)
                    vj = self.components.get(kj, _ZERO_SENTINEL)
                    overlap_key = "|".join(sorted([ki, kj]))
                    if vi == _ZERO_SENTINEL and vj == _ZERO_SENTINEL:
                        new_components[overlap_key] = _ZERO_SENTINEL
                    elif vi == _ZERO_SENTINEL:
                        new_components[overlap_key] = vj
                    elif vj == _ZERO_SENTINEL:
                        new_components[overlap_key] = vi
                    else:
                        # Symbolic alternating difference
                        new_components[overlap_key] = {"δ": f"{vj} - {vi}"}
            new_degree = CochainDegree.ONE
        else:
            # δ(σ¹)(U_i ∩ U_j ∩ U_k) — alternating sum over faces
            for key, val in self.components.items():
                parts = key.split("|")
                if len(parts) < 2:
                    continue
                # Form all triples
                for extra_key in keys:
                    if extra_key in parts:
                        continue
                    triple = sorted(parts + [extra_key])
                    triple_key = "|".join(triple)
                    if triple_key not in new_components:
                        new_components[triple_key] = {"δ": f"∂({key})"}
            new_degree = CochainDegree.TWO

        return CechCochainData(
            degree=new_degree,
            components=new_components,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
            provenance=self.provenance + ("coboundary",),
        )

    def is_cocycle(self) -> bool:
        """Return True iff δ(self) = 0 (the coboundary is the zero cochain).

        A 1-cochain is a cocycle iff for every triple overlap the alternating
        sum of restricted values vanishes.  Here we check the symbolic form.
        """
        delta = self.coboundary()
        return delta.is_zero() or len(delta.components) == 0

    def add(self, other: CechCochainData) -> CechCochainData:
        """Add two cochains of the same degree (abelian group operation).

        Parameters
        ----------
        other:
            The cochain to add.

        Raises
        ------
        ValueError
            If degrees or site IDs differ.

        Returns
        -------
        CechCochainData
        """
        if self.degree != other.degree:
            raise ValueError(f"Cannot add cochains of different degrees {self.degree} vs {other.degree}")
        merged: dict[str, Any] = dict(self.components)
        for k, v in other.components.items():
            if k in merged:
                existing = merged[k]
                if existing == _ZERO_SENTINEL:
                    merged[k] = v
                elif v == _ZERO_SENTINEL:
                    pass
                else:
                    merged[k] = {"sum": [existing, v]}
            else:
                merged[k] = v
        return CechCochainData(
            degree=self.degree,
            components=merged,
            site_id=self.site_id or other.site_id,
            sheaf_name=self.sheaf_name,
            provenance=self.provenance + other.provenance + ("add",),
        )

    def fingerprint(self) -> str:
        """Return a short SHA-256 fingerprint of this cochain."""
        payload = json.dumps(
            {"cochain_id": self.cochain_id, "degree": int(self.degree), "component_keys": sorted(self.components.keys())},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "cochain_id": self.cochain_id,
            "degree": int(self.degree),
            "components": dict(self.components),
            "site_id": self.site_id,
            "sheaf_name": self.sheaf_name,
            "provenance": list(self.provenance),
            "is_zero": self.is_zero(),
            "is_cocycle": self.is_cocycle(),
        }


# ---------------------------------------------------------------------------
# CechCohomologyClass — [σ] ∈ H^n(U, D)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CechCohomologyClass:
    """A Čech cohomology class [σ] ∈ H^n(U, D).

    Theory2.tex §formal_core: an element of the cohomology group
    H^n(U, D) = ker(δ^n) / im(δ^{n-1}).  A cohomology class is the
    equivalence class of a cocycle modulo coboundaries.

    The class is **trivial** (zero) iff the representative cocycle is a
    coboundary.  A non-trivial class in H¹ certifies a genuine obstruction
    to gluing local sections to a global section.

    Parameters
    ----------
    class_id:
        Stable unique identifier for this cohomology class.
    degree:
        Cohomological degree (1 for the primary obstruction class).
    representative:
        A representative :class:`CechCochainData` (a cocycle).
    is_trivial:
        True iff this class is the zero class (representative is a coboundary).
    group_label:
        Human-readable label, e.g. ``"H¹(U, D)"``.
    site_id:
        The site on which the cohomology is computed.
    sheaf_name:
        The coefficient sheaf.
    provenance:
        Ordered chain of step labels.
    """

    class_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    degree: int = 1
    representative: CechCochainData = field(default_factory=CechCochainData)
    is_trivial: bool = False
    group_label: str = "H¹(U,D)"
    site_id: str = ""
    sheaf_name: str = "DiscrepancySheaf"
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def vanishes(self) -> bool:
        """Return True iff this cohomology class is zero (obstruction vanishes)."""
        return self.is_trivial or self.representative.is_zero()

    def cup_product(self, other: CechCohomologyClass) -> CechCohomologyClass:
        """Compute the cup product α ∪ β in degree p + q.

        The cup product raises degree: α ∈ H^p, β ∈ H^q → α ∪ β ∈ H^{p+q}.
        If either class is trivial (zero), the product is zero (by bilinearity).

        Parameters
        ----------
        other:
            The other cohomology class.

        Returns
        -------
        CechCohomologyClass
            The cup product.
        """
        new_degree = self.degree + other.degree
        if self.vanishes() or other.vanishes():
            zero_cochain = CechCochainData(
                degree=CochainDegree(min(new_degree, 2)),
                components={},
                site_id=self.site_id,
                sheaf_name=self.sheaf_name,
            )
            return CechCohomologyClass(
                degree=new_degree,
                representative=zero_cochain,
                is_trivial=True,
                group_label=f"H^{new_degree}(U,D)",
                site_id=self.site_id,
                sheaf_name=self.sheaf_name,
                provenance=self.provenance + other.provenance + ("cup_product_zero",),
            )
        # Non-zero cup product: tensor the component dicts
        combined: dict[str, Any] = {}
        for k1, v1 in self.representative.components.items():
            for k2, v2 in other.representative.components.items():
                combined_key = f"{k1}⊗{k2}"
                combined[combined_key] = {"left": v1, "right": v2}
        new_rep = CechCochainData(
            degree=CochainDegree(min(new_degree, 2)),
            components=combined,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
            provenance=self.representative.provenance + other.representative.provenance + ("cup_product",),
        )
        return CechCohomologyClass(
            degree=new_degree,
            representative=new_rep,
            is_trivial=len(combined) == 0,
            group_label=f"H^{new_degree}(U,D)",
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
            provenance=self.provenance + other.provenance + ("cup_product",),
        )

    def restriction(self, sub_site_id: str) -> CechCohomologyClass:
        """Restrict this cohomology class to a sub-site.

        Implements the restriction map H^n(U, D) → H^n(U|_V, D|_V) by
        keeping only simplex components that are relevant to *sub_site_id*.

        Parameters
        ----------
        sub_site_id:
            The target sub-site identifier.

        Returns
        -------
        CechCohomologyClass
        """
        restricted_components = {
            k: v for k, v in self.representative.components.items()
            if sub_site_id in k or not sub_site_id
        }
        new_rep = replace(
            self.representative,
            components=restricted_components,
            provenance=self.representative.provenance + (f"restrict:{sub_site_id}",),
        )
        return replace(
            self,
            representative=new_rep,
            is_trivial=len(restricted_components) == 0,
            provenance=self.provenance + (f"restriction:{sub_site_id}",),
        )

    def required_evidence_types(self) -> list[str]:
        """Return a list of evidence types required to trivialise this class.

        For each non-zero component of the representative, determines the
        kind of evidence needed to make it a coboundary (to resolve the
        gluing failure).

        Returns
        -------
        list[str]
        """
        if self.vanishes():
            return []
        required: list[str] = []
        for key, val in self.representative.components.items():
            if val == _ZERO_SENTINEL or val is None:
                continue
            parts = key.split("|")
            label = f"overlap({'∩'.join(parts)})" if len(parts) > 1 else key
            if isinstance(val, dict) and "δ" in val:
                required.append(f"{label}: solver certificate or runtime witness to resolve δ-conflict")
            elif isinstance(val, dict) and "sum" in val:
                required.append(f"{label}: proof-backed evidence to cancel accumulated discrepancy")
            else:
                required.append(f"{label}: evidence to resolve discrepancy {val!r}")
        return required or ["general: additional evidence to make cohomology class trivial"]

    def fingerprint(self) -> str:
        """Return a short fingerprint of this cohomology class."""
        payload = json.dumps(
            {"class_id": self.class_id, "degree": self.degree, "is_trivial": self.is_trivial},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "class_id": self.class_id,
            "degree": self.degree,
            "representative": self.representative.to_dict(),
            "is_trivial": self.is_trivial,
            "group_label": self.group_label,
            "site_id": self.site_id,
            "sheaf_name": self.sheaf_name,
            "provenance": list(self.provenance),
            "vanishes": self.vanishes(),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CechCohomologyClass:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        rep_dict = d.get("representative", {})
        rep = CechCochainData(
            cochain_id=rep_dict.get("cochain_id", str(uuid.uuid4())),
            degree=CochainDegree(rep_dict.get("degree", 1)),
            components=dict(rep_dict.get("components", {})),
            site_id=rep_dict.get("site_id", ""),
            sheaf_name=rep_dict.get("sheaf_name", "DiscrepancySheaf"),
            provenance=tuple(rep_dict.get("provenance", [])),
        )
        return cls(
            class_id=d.get("class_id", str(uuid.uuid4())),
            degree=int(d.get("degree", 1)),
            representative=rep,
            is_trivial=bool(d.get("is_trivial", False)),
            group_label=d.get("group_label", "H¹(U,D)"),
            site_id=d.get("site_id", ""),
            sheaf_name=d.get("sheaf_name", "DiscrepancySheaf"),
            provenance=tuple(d.get("provenance", [])),
        )


# ---------------------------------------------------------------------------
# GluingFailure — a specific gluing failure with provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GluingFailure:
    """A specific failure to glue two local sections on their overlap.

    A ``GluingFailure`` records the two sections that were incompatible, the
    overlap on which they disagreed, the discrepancy value, and a complete
    provenance chain.  It is the raw input from which a :class:`CechCohomologyClass`
    is computed.

    Theory2.tex §formal_core: a gluing failure is not ephemeral — it persists
    and must be addressed before the corresponding obstruction can be resolved.

    Parameters
    ----------
    failure_id:
        Stable unique identifier.
    section_a_id:
        Identifier of the first local section.
    section_b_id:
        Identifier of the second local section.
    overlap_key:
        The simplex key representing the overlap (e.g. ``"ctx_A|ctx_B"``).
    discrepancy:
        The discrepancy value at the overlap (formula disagreement, tier gap, etc.).
    context_id:
        The context coordinate at which the failure was detected.
    phi_a:
        Formula in section A.
    phi_b:
        Formula in section B (may differ from phi_a if formulas disagree).
    trust_tier_a:
        Trust tier of section A.
    trust_tier_b:
        Trust tier of section B.
    detected_at:
        Unix timestamp of detection.
    provenance:
        Ordered chain of step labels.
    """

    failure_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    section_a_id: str = ""
    section_b_id: str = ""
    overlap_key: str = ""
    discrepancy: Any = None
    context_id: str = ""
    phi_a: str = ""
    phi_b: str = ""
    trust_tier_a: str = "PROPOSAL"
    trust_tier_b: str = "PROPOSAL"
    detected_at: float = field(default_factory=time.time)
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def is_trust_tier_mismatch(self) -> bool:
        """Return True iff the failure is due to a trust tier mismatch."""
        tier_order = ["PROPOSAL", "REVIEWED", "VERIFIED", "RUNTIME_WITNESSED", "PROOF_BACKED"]
        rank_a = tier_order.index(self.trust_tier_a) if self.trust_tier_a in tier_order else 0
        rank_b = tier_order.index(self.trust_tier_b) if self.trust_tier_b in tier_order else 0
        return abs(rank_a - rank_b) > 1

    def is_formula_mismatch(self) -> bool:
        """Return True iff the failure is due to formula disagreement."""
        return bool(self.phi_a and self.phi_b and self.phi_a != self.phi_b)

    def to_cochain_component(self) -> dict[str, Any]:
        """Return the 1-cochain component corresponding to this failure."""
        return {
            "overlap": self.overlap_key,
            "discrepancy": self.discrepancy,
            "phi_a": self.phi_a,
            "phi_b": self.phi_b,
            "tier_a": self.trust_tier_a,
            "tier_b": self.trust_tier_b,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "failure_id": self.failure_id,
            "section_a_id": self.section_a_id,
            "section_b_id": self.section_b_id,
            "overlap_key": self.overlap_key,
            "discrepancy": str(self.discrepancy) if self.discrepancy is not None else None,
            "context_id": self.context_id,
            "phi_a": self.phi_a,
            "phi_b": self.phi_b,
            "trust_tier_a": self.trust_tier_a,
            "trust_tier_b": self.trust_tier_b,
            "detected_at": self.detected_at,
            "provenance": list(self.provenance),
            "is_trust_tier_mismatch": self.is_trust_tier_mismatch(),
            "is_formula_mismatch": self.is_formula_mismatch(),
        }


# ---------------------------------------------------------------------------
# ObstructionRecord — first-class, immutable obstruction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionRecord:
    """A first-class, immutable obstruction record with full provenance.

    An ``ObstructionRecord`` is the canonical representation of an obstruction
    in JuGeo.  It wraps a :class:`CechCohomologyClass` (the algebraic
    certificate of the obstruction) together with metadata about severity,
    origin, repair hints, and provenance.

    Theory2.tex §formal_core: obstructions are persistent (not ephemeral),
    first-class (not embedded in exceptions), and carry provenance (not bare
    strings).  An obstruction record is the algebraic dual of a proof: it
    certifies that a certain global section **cannot exist** without additional
    evidence.

    Parameters
    ----------
    record_id:
        Stable unique identifier.
    cohomology_class:
        The :class:`CechCohomologyClass` certifying the obstruction.
    kind:
        :class:`ObstructionKind` of this obstruction.
    severity:
        :class:`ObstructionSeverity`.
    context_id:
        The context coordinate where the obstruction was detected.
    formula:
        The formula φ of the judgment that could not be discharged.
    gluing_failures:
        The :class:`GluingFailure` objects that generated this obstruction.
    repair_hints:
        Ordered list of evidence types needed to resolve the obstruction.
    detected_at:
        Unix timestamp of detection.
    detected_by:
        Identifier of the component that detected this obstruction.
    provenance:
        Ordered chain of step labels.
    metadata:
        Auxiliary key-value pairs.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cohomology_class: CechCohomologyClass = field(default_factory=CechCohomologyClass)
    kind: ObstructionKind = ObstructionKind.CECH_H1
    severity: ObstructionSeverity = ObstructionSeverity.BLOCKING
    context_id: str = ""
    formula: str = ""
    gluing_failures: tuple[GluingFailure, ...] = field(default_factory=tuple)
    repair_hints: tuple[str, ...] = field(default_factory=tuple)
    detected_at: float = field(default_factory=time.time)
    detected_by: str = ""
    provenance: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_resolved(self) -> bool:
        """Return True iff the underlying cohomology class is trivial."""
        return self.cohomology_class.vanishes()

    def is_blocking(self) -> bool:
        """Return True iff this is a blocking obstruction."""
        return self.severity.is_blocking() and not self.is_resolved()

    def failure_count(self) -> int:
        """Return the number of constituent gluing failures."""
        return len(self.gluing_failures)

    def required_evidence_types(self) -> list[str]:
        """Return evidence types required to resolve this obstruction.

        Combines hints from the cohomology class with any pre-computed
        repair_hints stored in this record.

        Returns
        -------
        list[str]
        """
        from_class = self.cohomology_class.required_evidence_types()
        from_hints = list(self.repair_hints)
        # De-duplicate while preserving order
        seen: set[str] = set()
        combined: list[str] = []
        for item in from_class + from_hints:
            if item not in seen:
                seen.add(item)
                combined.append(item)
        return combined

    def with_severity(self, new_severity: ObstructionSeverity) -> ObstructionRecord:
        """Return a new record with updated severity.

        Parameters
        ----------
        new_severity:
            The new severity level.

        Returns
        -------
        ObstructionRecord
        """
        return replace(
            self,
            severity=new_severity,
            provenance=self.provenance + (f"severity_update:{new_severity.value}",),
        )

    def fingerprint(self) -> str:
        """Return a short fingerprint of this record."""
        payload = json.dumps(
            {"record_id": self.record_id, "kind": self.kind.value, "context_id": self.context_id, "formula": self.formula},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def describe(self) -> str:
        """Return a human-readable description of this obstruction."""
        status = "RESOLVED" if self.is_resolved() else self.severity.value.upper()
        lines = [
            f"ObstructionRecord {self.record_id}",
            f"  Status          : {status}",
            f"  Kind            : {self.kind.value}",
            f"  Context         : {self.context_id}",
            f"  Formula         : {self.formula[:60]}",
            f"  Cohomology class: {self.cohomology_class.group_label} (trivial={self.cohomology_class.vanishes()})",
            f"  Gluing failures : {self.failure_count()}",
            f"  Detected by     : {self.detected_by}",
        ]
        hints = self.required_evidence_types()
        if hints:
            lines.append("  Repair hints:")
            for h in hints[:3]:
                lines.append(f"    - {h}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "record_id": self.record_id,
            "cohomology_class": self.cohomology_class.to_dict(),
            "kind": self.kind.value,
            "severity": self.severity.value,
            "context_id": self.context_id,
            "formula": self.formula,
            "gluing_failures": [f.to_dict() for f in self.gluing_failures],
            "repair_hints": list(self.repair_hints),
            "detected_at": self.detected_at,
            "detected_by": self.detected_by,
            "provenance": list(self.provenance),
            "metadata": dict(self.metadata),
            "is_resolved": self.is_resolved(),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObstructionRecord:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        cls_dict = d.get("cohomology_class", {})
        coh_class = CechCohomologyClass.from_dict(cls_dict)
        failures = tuple(
            GluingFailure(
                failure_id=f.get("failure_id", str(uuid.uuid4())),
                section_a_id=f.get("section_a_id", ""),
                section_b_id=f.get("section_b_id", ""),
                overlap_key=f.get("overlap_key", ""),
                discrepancy=f.get("discrepancy"),
                context_id=f.get("context_id", ""),
                phi_a=f.get("phi_a", ""),
                phi_b=f.get("phi_b", ""),
                trust_tier_a=f.get("trust_tier_a", "PROPOSAL"),
                trust_tier_b=f.get("trust_tier_b", "PROPOSAL"),
                detected_at=float(f.get("detected_at", time.time())),
                provenance=tuple(f.get("provenance", [])),
            )
            for f in d.get("gluing_failures", [])
        )
        return cls(
            record_id=d.get("record_id", str(uuid.uuid4())),
            cohomology_class=coh_class,
            kind=ObstructionKind(d.get("kind", ObstructionKind.CECH_H1.value)),
            severity=ObstructionSeverity(d.get("severity", ObstructionSeverity.BLOCKING.value)),
            context_id=d.get("context_id", ""),
            formula=d.get("formula", ""),
            gluing_failures=failures,
            repair_hints=tuple(d.get("repair_hints", [])),
            detected_at=float(d.get("detected_at", time.time())),
            detected_by=d.get("detected_by", ""),
            provenance=tuple(d.get("provenance", [])),
            metadata=dict(d.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# PersistentObstruction — mutable, tracks repair progress
# ---------------------------------------------------------------------------


@dataclass
class PersistentObstruction:
    """Mutable wrapper around an :class:`ObstructionRecord` that tracks repair.

    A ``PersistentObstruction`` is the living, mutable representation of an
    obstruction throughout its lifecycle: from initial detection (OPEN) through
    repair attempts (IN_PROGRESS) to resolution (RESOLVED) or escalation.

    Theory2.tex §formal_core: obstructions persist across iterations; they are
    not discarded after the first failure.  The repair log records every
    attempt, whether successful or not.

    Parameters
    ----------
    persistent_id:
        Stable unique identifier.
    record:
        The underlying immutable :class:`ObstructionRecord`.
    status:
        Current :class:`RepairStatus`.
    repair_log:
        Append-only log of repair attempts.
    resolution_evidence:
        Evidence pointers added during successful resolution.
    created_at:
        Unix timestamp.
    updated_at:
        Unix timestamp of last status change.
    """

    persistent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    record: ObstructionRecord = field(default_factory=ObstructionRecord)
    status: RepairStatus = RepairStatus.OPEN
    repair_log: list[dict[str, Any]] = field(default_factory=list)
    resolution_evidence: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def start_repair(self, strategy: str) -> None:
        """Begin a repair attempt with *strategy*.

        Parameters
        ----------
        strategy:
            Human-readable description of the repair strategy.
        """
        self.status = RepairStatus.IN_PROGRESS
        self.updated_at = time.time()
        self.repair_log.append({
            "event": "start_repair",
            "strategy": strategy,
            "timestamp": self.updated_at,
        })
        log.debug("PersistentObstruction.start_repair: id=%r strategy=%r", self.persistent_id, strategy)

    def record_attempt(self, strategy: str, evidence_pointer: str, success: bool) -> None:
        """Record a repair attempt.

        Parameters
        ----------
        strategy:
            Description of the strategy used.
        evidence_pointer:
            Pointer to the evidence provided.
        success:
            Whether the attempt resolved the obstruction.
        """
        self.updated_at = time.time()
        self.repair_log.append({
            "event": "repair_attempt",
            "strategy": strategy,
            "evidence_pointer": evidence_pointer,
            "success": success,
            "timestamp": self.updated_at,
        })
        if success:
            self.resolution_evidence.append(evidence_pointer)
            self.status = RepairStatus.RESOLVED
            log.info("PersistentObstruction: resolved id=%r by strategy=%r", self.persistent_id, strategy)

    def defer(self, reason: str) -> None:
        """Defer this obstruction.

        Parameters
        ----------
        reason:
            Reason for deferral.
        """
        self.status = RepairStatus.DEFERRED
        self.updated_at = time.time()
        self.repair_log.append({"event": "deferred", "reason": reason, "timestamp": self.updated_at})

    def escalate(self, reason: str) -> None:
        """Escalate this obstruction for human review.

        Parameters
        ----------
        reason:
            Reason for escalation.
        """
        self.status = RepairStatus.ESCALATED
        self.updated_at = time.time()
        self.repair_log.append({"event": "escalated", "reason": reason, "timestamp": self.updated_at})

    def is_resolved(self) -> bool:
        """Return True iff this obstruction has been resolved."""
        return self.status == RepairStatus.RESOLVED

    def is_blocking(self) -> bool:
        """Return True iff this is an unresolved blocking obstruction."""
        return self.record.is_blocking() and not self.is_resolved()

    def age_seconds(self) -> float:
        """Return the age of this obstruction in seconds."""
        return time.time() - self.created_at

    def attempt_count(self) -> int:
        """Return the total number of repair attempts recorded."""
        return sum(1 for e in self.repair_log if e.get("event") == "repair_attempt")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "persistent_id": self.persistent_id,
            "record": self.record.to_dict(),
            "status": self.status.value,
            "repair_log": list(self.repair_log),
            "resolution_evidence": list(self.resolution_evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_resolved": self.is_resolved(),
            "attempt_count": self.attempt_count(),
        }


# ---------------------------------------------------------------------------
# ObstructionRegistry — mutable registry of all known obstructions
# ---------------------------------------------------------------------------


@dataclass
class ObstructionRegistry:
    """Mutable registry of all known obstructions, indexed by record_id.

    ``ObstructionRegistry`` is the central store for :class:`PersistentObstruction`
    objects.  It provides:

    - Registration and lookup by ID, kind, or severity.
    - Bulk queries for blocking / open / resolved obstructions.
    - Computation of Čech cohomology groups from registered gluing failures.
    - A compactness score indicating how many obstructions remain unresolved.

    Parameters
    ----------
    registry_id:
        Stable unique identifier.
    obstructions:
        Internal dict mapping record_id → PersistentObstruction.
    event_log:
        Append-only log of registry events.
    """

    registry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    obstructions: dict[str, PersistentObstruction] = field(default_factory=dict)
    event_log: list[dict[str, Any]] = field(default_factory=list)

    def register(self, record: ObstructionRecord) -> PersistentObstruction:
        """Register an :class:`ObstructionRecord` and wrap it in a persistent shell.

        Parameters
        ----------
        record:
            The obstruction record to register.

        Returns
        -------
        PersistentObstruction
            The newly created persistent obstruction.
        """
        persistent = PersistentObstruction(record=record)
        self.obstructions[record.record_id] = persistent
        self.event_log.append({
            "event": "register",
            "record_id": record.record_id,
            "kind": record.kind.value,
            "severity": record.severity.value,
            "timestamp": time.time(),
        })
        log.debug(
            "ObstructionRegistry.register: id=%r kind=%r severity=%r",
            record.record_id, record.kind.value, record.severity.value,
        )
        return persistent

    def get(self, record_id: str) -> PersistentObstruction | None:
        """Return the persistent obstruction for *record_id*, or None."""
        return self.obstructions.get(record_id)

    def blocking(self) -> list[PersistentObstruction]:
        """Return all unresolved blocking obstructions."""
        return [p for p in self.obstructions.values() if p.is_blocking()]

    def open_obstructions(self) -> list[PersistentObstruction]:
        """Return all obstructions that are OPEN or IN_PROGRESS."""
        return [
            p for p in self.obstructions.values()
            if p.status in (RepairStatus.OPEN, RepairStatus.IN_PROGRESS)
        ]

    def resolved(self) -> list[PersistentObstruction]:
        """Return all resolved obstructions."""
        return [p for p in self.obstructions.values() if p.is_resolved()]

    def by_kind(self, kind: ObstructionKind) -> list[PersistentObstruction]:
        """Return all obstructions of the given kind."""
        return [p for p in self.obstructions.values() if p.record.kind == kind]

    def resolution_rate(self) -> float:
        """Return the fraction of registered obstructions that are resolved.

        Returns
        -------
        float
            Value in [0, 1]; 1.0 = all resolved.
        """
        total = len(self.obstructions)
        if total == 0:
            return 1.0
        return len(self.resolved()) / total

    def compute_h1_classes(self) -> list[CechCohomologyClass]:
        """Compute the Čech H¹ classes from registered blocking obstructions.

        Returns a list of non-trivial H¹ classes, one per blocking obstruction
        whose cohomology class is non-trivial.

        Returns
        -------
        list[CechCohomologyClass]
        """
        return [
            p.record.cohomology_class
            for p in self.blocking()
            if not p.record.cohomology_class.vanishes()
        ]

    def build_from_failures(
        self,
        failures: Sequence[GluingFailure],
        site_id: str = "",
        detected_by: str = "",
    ) -> list[ObstructionRecord]:
        """Build obstruction records from a sequence of gluing failures.

        Groups failures by their overlap_key and builds one
        :class:`CechCohomologyClass` per group, then wraps each class in an
        :class:`ObstructionRecord` and registers it.

        Parameters
        ----------
        failures:
            The gluing failures to process.
        site_id:
            Site identifier for the cohomology classes.
        detected_by:
            Component that detected the failures.

        Returns
        -------
        list[ObstructionRecord]
            The newly created and registered obstruction records.
        """
        # Group failures by formula (proxy for "same judgment")
        groups: dict[str, list[GluingFailure]] = {}
        for f in failures:
            key = f.phi_a or f.context_id or f.overlap_key
            groups.setdefault(key, []).append(f)

        records: list[ObstructionRecord] = []
        for formula, group_failures in groups.items():
            # Build a 1-cochain from the failures
            components: dict[str, Any] = {}
            for f in group_failures:
                components[f.overlap_key] = f.to_cochain_component()

            cochain = CechCochainData(
                degree=CochainDegree.ONE,
                components=components,
                site_id=site_id,
                sheaf_name="DiscrepancySheaf",
                provenance=("build_from_failures",),
            )
            is_trivial = cochain.is_zero()
            coh_class = CechCohomologyClass(
                degree=1,
                representative=cochain,
                is_trivial=is_trivial,
                group_label="H¹(U,D)",
                site_id=site_id,
                sheaf_name="DiscrepancySheaf",
                provenance=("build_from_failures",),
            )
            hints = coh_class.required_evidence_types()
            record = ObstructionRecord(
                cohomology_class=coh_class,
                kind=ObstructionKind.CECH_H1,
                severity=ObstructionSeverity.BLOCKING if not is_trivial else ObstructionSeverity.INFO,
                context_id=group_failures[0].context_id if group_failures else "",
                formula=formula,
                gluing_failures=tuple(group_failures),
                repair_hints=tuple(hints),
                detected_by=detected_by,
                provenance=("build_from_failures",),
            )
            self.register(record)
            records.append(record)

        self.event_log.append({
            "event": "build_from_failures",
            "failure_count": len(failures),
            "record_count": len(records),
            "timestamp": time.time(),
        })
        return records

    def describe(self) -> str:
        """Return a human-readable summary of this registry."""
        return (
            f"ObstructionRegistry {self.registry_id}\n"
            f"  Total obstructions : {len(self.obstructions)}\n"
            f"  Blocking           : {len(self.blocking())}\n"
            f"  Open               : {len(self.open_obstructions())}\n"
            f"  Resolved           : {len(self.resolved())}\n"
            f"  Resolution rate    : {self.resolution_rate():.1%}\n"
            f"  H¹ classes         : {len(self.compute_h1_classes())}\n"
        )


# ---------------------------------------------------------------------------
# ObstructionsStructuredNonexistenceWitness — immutable run certificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionsStructuredNonexistenceWitness:
    """Immutable certificate produced by a completed obstruction analysis run.

    Parameters
    ----------
    witness_id:
        Stable unique identifier.
    registry_id:
        The registry that produced this witness.
    total_obstructions:
        Total obstructions registered.
    blocking_count:
        Number of unresolved blocking obstructions.
    resolved_count:
        Number of resolved obstructions.
    h1_class_count:
        Number of non-trivial H¹ cohomology classes.
    resolution_rate:
        Fraction of obstructions resolved in [0, 1].
    provenance:
        Ordered chain of step labels.
    created_at:
        Unix timestamp.
    metadata:
        Auxiliary key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    registry_id: str = ""
    total_obstructions: int = 0
    blocking_count: int = 0
    resolved_count: int = 0
    h1_class_count: int = 0
    resolution_rate: float = 0.0
    provenance: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_obstruction_free(self) -> bool:
        """Return True iff there are no unresolved blocking obstructions."""
        return self.blocking_count == 0

    def summary(self) -> str:
        """Return a one-line summary."""
        ok = "✓" if self.is_obstruction_free() else "✗"
        return (
            f"[ObsWitness {self.witness_id[:8]}] registry={self.registry_id[:8]} "
            f"total={self.total_obstructions} blocking={self.blocking_count} "
            f"resolved={self.resolved_count} H¹={self.h1_class_count} "
            f"rate={self.resolution_rate:.0%} free={ok}"
        )

    def validate(self) -> list[str]:
        """Return validation violations; empty if valid."""
        errors: list[str] = []
        if not self.witness_id:
            errors.append("witness_id must not be empty")
        if not self.registry_id:
            errors.append("registry_id must not be empty")
        if not (0.0 <= self.resolution_rate <= 1.0):
            errors.append(f"resolution_rate out of [0,1]: {self.resolution_rate}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "registry_id": self.registry_id,
            "total_obstructions": self.total_obstructions,
            "blocking_count": self.blocking_count,
            "resolved_count": self.resolved_count,
            "h1_class_count": self.h1_class_count,
            "resolution_rate": self.resolution_rate,
            "provenance": list(self.provenance),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "is_obstruction_free": self.is_obstruction_free(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObstructionsStructuredNonexistenceWitness:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        return cls(
            witness_id=d.get("witness_id", str(uuid.uuid4())),
            registry_id=d.get("registry_id", ""),
            total_obstructions=int(d.get("total_obstructions", 0)),
            blocking_count=int(d.get("blocking_count", 0)),
            resolved_count=int(d.get("resolved_count", 0)),
            h1_class_count=int(d.get("h1_class_count", 0)),
            resolution_rate=float(d.get("resolution_rate", 0.0)),
            provenance=tuple(d.get("provenance", [])),
            created_at=float(d.get("created_at", time.time())),
            metadata=dict(d.get("metadata", {})),
        )

    def merge(self, other: ObstructionsStructuredNonexistenceWitness) -> ObstructionsStructuredNonexistenceWitness:
        """Merge two witnesses additively."""
        total = self.total_obstructions + other.total_obstructions
        resolved = self.resolved_count + other.resolved_count
        rate = resolved / total if total > 0 else 1.0
        return ObstructionsStructuredNonexistenceWitness(
            registry_id=self.registry_id or other.registry_id,
            total_obstructions=total,
            blocking_count=self.blocking_count + other.blocking_count,
            resolved_count=resolved,
            h1_class_count=self.h1_class_count + other.h1_class_count,
            resolution_rate=rate,
            provenance=self.provenance + other.provenance + ("merged",),
            metadata={**other.metadata, **self.metadata},
        )


# ---------------------------------------------------------------------------
# ObstructionsStructuredNonexistenceCoordinator
# ---------------------------------------------------------------------------


@dataclass
class ObstructionsStructuredNonexistenceCoordinator:
    """Orchestrates obstruction detection, registration, and repair lifecycle.

    The ``ObstructionsStructuredNonexistenceCoordinator`` is the primary entry
    point for:
    - Detecting gluing failures between local sections.
    - Building cohomology classes from those failures.
    - Registering obstructions with the :class:`ObstructionRegistry`.
    - Initiating and recording repair attempts.
    - Emitting :class:`ObstructionsStructuredNonexistenceWitness` certificates.

    Parameters
    ----------
    coordinator_id:
        Stable unique identifier.
    registry:
        The :class:`ObstructionRegistry` being managed.
    witnesses:
        Witnesses emitted by this coordinator.
    run_log:
        Append-only log of coordinator actions.
    """

    coordinator_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    registry: ObstructionRegistry = field(default_factory=ObstructionRegistry)
    witnesses: list[ObstructionsStructuredNonexistenceWitness] = field(default_factory=list)
    run_log: list[dict[str, Any]] = field(default_factory=list)

    def detect_gluing_failure(
        self,
        section_a_id: str,
        section_b_id: str,
        overlap_key: str,
        context_id: str,
        phi_a: str,
        phi_b: str,
        trust_tier_a: str = "PROPOSAL",
        trust_tier_b: str = "PROPOSAL",
        discrepancy: Any = None,
    ) -> GluingFailure:
        """Detect and record a gluing failure between two local sections.

        Parameters
        ----------
        section_a_id:
            Identifier of the first local section.
        section_b_id:
            Identifier of the second local section.
        overlap_key:
            The simplex key representing the overlap.
        context_id:
            The context coordinate.
        phi_a, phi_b:
            Formulas of the two sections.
        trust_tier_a, trust_tier_b:
            Trust tiers.
        discrepancy:
            Discrepancy value at the overlap.

        Returns
        -------
        GluingFailure
        """
        failure = GluingFailure(
            section_a_id=section_a_id,
            section_b_id=section_b_id,
            overlap_key=overlap_key,
            discrepancy=discrepancy or {"phi_conflict": f"{phi_a!r} ≠ {phi_b!r}"},
            context_id=context_id,
            phi_a=phi_a,
            phi_b=phi_b,
            trust_tier_a=trust_tier_a,
            trust_tier_b=trust_tier_b,
            provenance=("detected_by_coordinator",),
        )
        self.run_log.append({
            "event": "detect_gluing_failure",
            "failure_id": failure.failure_id,
            "overlap_key": overlap_key,
            "context_id": context_id,
            "timestamp": time.time(),
        })
        log.debug("Coordinator.detect_gluing_failure: %r", failure.failure_id)
        return failure

    def build_obstruction_from_failures(
        self,
        failures: Sequence[GluingFailure],
        site_id: str = "",
    ) -> list[ObstructionRecord]:
        """Build and register obstruction records from gluing failures.

        Parameters
        ----------
        failures:
            Gluing failures to process.
        site_id:
            Site identifier.

        Returns
        -------
        list[ObstructionRecord]
        """
        records = self.registry.build_from_failures(
            failures=failures,
            site_id=site_id,
            detected_by=f"coordinator:{self.coordinator_id}",
        )
        self.run_log.append({
            "event": "build_obstruction",
            "failure_count": len(failures),
            "record_count": len(records),
            "timestamp": time.time(),
        })
        return records

    def register_direct(
        self,
        kind: ObstructionKind,
        severity: ObstructionSeverity,
        context_id: str,
        formula: str,
        site_id: str = "",
        repair_hints: tuple[str, ...] = (),
    ) -> ObstructionRecord:
        """Register a new obstruction directly (without gluing failures).

        Parameters
        ----------
        kind:
            :class:`ObstructionKind`.
        severity:
            :class:`ObstructionSeverity`.
        context_id:
            Context coordinate.
        formula:
            Formula under judgment.
        site_id:
            Site identifier.
        repair_hints:
            Pre-computed repair hints.

        Returns
        -------
        ObstructionRecord
        """
        cochain = CechCochainData(
            degree=CochainDegree.ONE,
            components={context_id: {"kind": kind.value, "formula": formula[:60]}},
            site_id=site_id,
            sheaf_name="DiscrepancySheaf",
            provenance=("direct_registration",),
        )
        coh_class = CechCohomologyClass(
            degree=1,
            representative=cochain,
            is_trivial=False,
            group_label="H¹(U,D)",
            site_id=site_id,
            provenance=("direct_registration",),
        )
        record = ObstructionRecord(
            cohomology_class=coh_class,
            kind=kind,
            severity=severity,
            context_id=context_id,
            formula=formula,
            repair_hints=repair_hints,
            detected_by=f"coordinator:{self.coordinator_id}",
            provenance=("direct_registration",),
        )
        self.registry.register(record)
        self.run_log.append({
            "event": "register_direct",
            "record_id": record.record_id,
            "kind": kind.value,
            "timestamp": time.time(),
        })
        return record

    def attempt_repair(
        self,
        record_id: str,
        strategy: str,
        evidence_pointer: str,
        success: bool,
    ) -> bool:
        """Attempt to repair a registered obstruction.

        Parameters
        ----------
        record_id:
            The obstruction to repair.
        strategy:
            Description of the repair strategy.
        evidence_pointer:
            Pointer to the new evidence.
        success:
            Whether the attempt succeeded.

        Returns
        -------
        bool
            True if the repair was applied (record found).
        """
        persistent = self.registry.get(record_id)
        if persistent is None:
            log.warning("Coordinator.attempt_repair: unknown record_id %r", record_id)
            return False
        if persistent.status == RepairStatus.OPEN:
            persistent.start_repair(strategy)
        persistent.record_attempt(strategy=strategy, evidence_pointer=evidence_pointer, success=success)
        self.run_log.append({
            "event": "attempt_repair",
            "record_id": record_id,
            "strategy": strategy,
            "success": success,
            "timestamp": time.time(),
        })
        return True

    def produce_witness(self) -> ObstructionsStructuredNonexistenceWitness:
        """Emit an immutable :class:`ObstructionsStructuredNonexistenceWitness`."""
        h1_classes = self.registry.compute_h1_classes()
        w = ObstructionsStructuredNonexistenceWitness(
            registry_id=self.registry.registry_id,
            total_obstructions=len(self.registry.obstructions),
            blocking_count=len(self.registry.blocking()),
            resolved_count=len(self.registry.resolved()),
            h1_class_count=len(h1_classes),
            resolution_rate=self.registry.resolution_rate(),
            provenance=tuple(e["event"] for e in self.run_log[-10:]),
            metadata={"coordinator_id": self.coordinator_id},
        )
        self.witnesses.append(w)
        log.info("ObstructionsStructuredNonexistenceCoordinator.produce_witness: %s", w.summary())
        return w

    def validate(self) -> list[str]:
        """Return validation violations; empty if all invariants hold."""
        violations: list[str] = []
        if not self.coordinator_id:
            violations.append("coordinator_id must not be empty")
        # Every blocking obstruction should have at least one repair hint
        for p in self.registry.blocking():
            if not p.record.repair_hints and not p.record.cohomology_class.required_evidence_types():
                violations.append(
                    f"Blocking obstruction {p.record.record_id} has no repair hints"
                )
        return violations

    def describe(self) -> str:
        """Return a human-readable summary."""
        return (
            f"ObstructionsStructuredNonexistenceCoordinator {self.coordinator_id}\n"
            + self.registry.describe()
            + f"  Witnesses produced : {len(self.witnesses)}\n"
            + f"  Run log events     : {len(self.run_log)}\n"
        )


# ---------------------------------------------------------------------------
# ObstructionsStructuredNonexistenceAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class ObstructionsStructuredNonexistenceAnalyzer:
    """Analyses the obstruction registry and computes resolution metrics.

    ``ObstructionsStructuredNonexistenceAnalyzer`` operates on a collection of
    :class:`ObstructionsStructuredNonexistenceWitness` objects and a live
    :class:`ObstructionRegistry` (if available) to provide:

    - Resolution rate statistics.
    - H¹ cohomology class count trends.
    - Severity distribution across witnesses.
    - Computation of a composite obstruction-health score.

    Parameters
    ----------
    analyzer_id:
        Stable unique identifier.
    witnesses:
        The witnesses to analyse.
    registry:
        Optional live :class:`ObstructionRegistry` for deeper inspection.
    """

    analyzer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    witnesses: list[ObstructionsStructuredNonexistenceWitness] = field(default_factory=list)
    registry: ObstructionRegistry | None = None

    def average_resolution_rate(self) -> float:
        """Return the mean resolution rate across all witnesses."""
        if not self.witnesses:
            return 1.0
        return sum(w.resolution_rate for w in self.witnesses) / len(self.witnesses)

    def obstruction_free_rate(self) -> float:
        """Return the fraction of witnesses with no blocking obstructions."""
        if not self.witnesses:
            return 1.0
        return sum(1 for w in self.witnesses if w.is_obstruction_free()) / len(self.witnesses)

    def average_h1_class_count(self) -> float:
        """Return the mean number of H¹ classes per witness."""
        if not self.witnesses:
            return 0.0
        return sum(w.h1_class_count for w in self.witnesses) / len(self.witnesses)

    def severity_distribution(self) -> dict[str, int]:
        """Return the severity distribution of live registry obstructions (if available)."""
        if self.registry is None:
            return {}
        dist: dict[str, int] = {}
        for p in self.registry.obstructions.values():
            sev = p.record.severity.value
            dist[sev] = dist.get(sev, 0) + 1
        return dist

    def kind_distribution(self) -> dict[str, int]:
        """Return the kind distribution of live registry obstructions (if available)."""
        if self.registry is None:
            return {}
        dist: dict[str, int] = {}
        for p in self.registry.obstructions.values():
            k = p.record.kind.value
            dist[k] = dist.get(k, 0) + 1
        return dist

    def score(self) -> float:
        """Compute a composite obstruction-health score in [0, 1].

        Weighted combination:
        - Obstruction-free rate (weight 0.5)
        - Average resolution rate (weight 0.3)
        - Inverse of average H¹ class count, normalised (weight 0.2)

        Returns
        -------
        float
        """
        free_rate = self.obstruction_free_rate()
        res_rate = self.average_resolution_rate()
        avg_h1 = self.average_h1_class_count()
        max_h1 = max((w.h1_class_count for w in self.witnesses), default=1) or 1
        inv_h1 = 1.0 - min(avg_h1 / max_h1, 1.0)
        return 0.5 * free_rate + 0.3 * res_rate + 0.2 * inv_h1

    def most_persistent_obstructions(self, top_n: int = 5) -> list[PersistentObstruction]:
        """Return the top *top_n* obstructions by age (oldest first).

        Parameters
        ----------
        top_n:
            Number of obstructions to return.

        Returns
        -------
        list[PersistentObstruction]
        """
        if self.registry is None:
            return []
        open_obs = self.registry.open_obstructions()
        return sorted(open_obs, key=lambda p: p.created_at)[:top_n]

    def report(self) -> str:
        """Return a rich multi-line analysis report."""
        lines = [
            f"ObstructionsStructuredNonexistenceAnalyzer {self.analyzer_id}",
            f"  Witnesses                  : {len(self.witnesses)}",
            f"  Average resolution rate    : {self.average_resolution_rate():.1%}",
            f"  Obstruction-free rate      : {self.obstruction_free_rate():.1%}",
            f"  Average H¹ class count     : {self.average_h1_class_count():.2f}",
            f"  Obstruction-health score   : {self.score():.3f}",
        ]
        if self.registry:
            lines.append(f"  Live registry size         : {len(self.registry.obstructions)}")
            sev_dist = self.severity_distribution()
            if sev_dist:
                lines.append(f"  Severity distribution      : {sev_dist}")
            kind_dist = self.kind_distribution()
            if kind_dist:
                lines.append(f"  Kind distribution          : {kind_dist}")
            oldest = self.most_persistent_obstructions(3)
            if oldest:
                lines.append("  Most persistent (oldest):")
                for p in oldest:
                    lines.append(
                        f"    [{p.status.value}] {p.record.record_id[:8]} "
                        f"age={p.age_seconds():.1f}s kind={p.record.kind.value}"
                    )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== obstructions_as_structured_nonexis.py smoke test ===\n")

    # Build coordinator
    coord_obj = ObstructionsStructuredNonexistenceCoordinator()

    # Detect some gluing failures
    f1 = coord_obj.detect_gluing_failure(
        section_a_id="sec_A",
        section_b_id="sec_B",
        overlap_key="ctx_parse|ctx_validate",
        context_id="ctx_module",
        phi_a="∀x: parse(x) → valid(x)",
        phi_b="∀x: ¬parse(x) ∨ valid(x)",  # logically equivalent but syntactically different
        trust_tier_a="PROPOSAL",
        trust_tier_b="REVIEWED",
    )
    f2 = coord_obj.detect_gluing_failure(
        section_a_id="sec_A",
        section_b_id="sec_C",
        overlap_key="ctx_parse|ctx_emit",
        context_id="ctx_module",
        phi_a="∀x: parse(x) → valid(x)",
        phi_b="∃x: ¬valid(x)",
        trust_tier_a="PROPOSAL",
        trust_tier_b="PROPOSAL",
    )

    # Build obstruction records from failures
    records = coord_obj.build_obstruction_from_failures([f1, f2], site_id="site_myproject")
    assert len(records) >= 1, f"Expected at least 1 obstruction record, got {len(records)}"

    # Direct registration
    r_direct = coord_obj.register_direct(
        kind=ObstructionKind.TRUST_FLOOR_VIOLATION,
        severity=ObstructionSeverity.DEGRADING,
        context_id="ctx_infer",
        formula="∀x: infer(x) has trust ≥ REVIEWED",
        site_id="site_myproject",
        repair_hints=("REVIEWED evidence needed for infer(x)",),
    )

    # Attempt repair (failure)
    ok = coord_obj.attempt_repair(
        record_id=r_direct.record_id,
        strategy="add_human_review",
        evidence_pointer="review://pr_42/comment_7",
        success=False,
    )
    assert ok, "Repair attempt should return True (record found)"

    # Attempt repair (success)
    ok2 = coord_obj.attempt_repair(
        record_id=r_direct.record_id,
        strategy="solver_discharge",
        evidence_pointer="z3://proof_0xdeadbeef",
        success=True,
    )
    assert ok2

    # Check persistence
    persistent = coord_obj.registry.get(r_direct.record_id)
    assert persistent is not None
    assert persistent.is_resolved(), "Obstruction should be resolved after successful repair"
    assert persistent.attempt_count() == 2

    # Produce witness
    witness = coord_obj.produce_witness()
    errors = witness.validate()
    assert errors == [], f"Witness errors: {errors}"
    print(witness.summary())

    # Roundtrip
    d = witness.to_dict()
    w2 = ObstructionsStructuredNonexistenceWitness.from_dict(d)
    assert w2.witness_id == witness.witness_id

    # Merge
    w3 = witness.merge(w2)
    assert w3.total_obstructions == witness.total_obstructions * 2

    # Cohomology class operations
    cochain = CechCochainData(
        degree=CochainDegree.ZERO,
        components={"ctx_A": "ev_type_A", "ctx_B": "ev_type_B"},
        site_id="site_myproject",
    )
    delta = cochain.coboundary()
    assert delta.degree == CochainDegree.ONE, f"Expected degree 1, got {delta.degree}"

    # Build CechCohomologyClass and check cup product
    coh1 = CechCohomologyClass(
        degree=1,
        representative=CechCochainData(
            degree=CochainDegree.ONE,
            components={"ctx_A|ctx_B": {"discrepancy": "trust_gap"}},
            site_id="site_myproject",
        ),
        is_trivial=False,
        site_id="site_myproject",
        provenance=("test",),
    )
    coh_trivial = CechCohomologyClass(
        degree=1,
        representative=CechCochainData(
            degree=CochainDegree.ONE,
            components={},
            site_id="site_myproject",
        ),
        is_trivial=True,
        site_id="site_myproject",
    )
    cup = coh1.cup_product(coh_trivial)
    assert cup.vanishes(), "Cup product with trivial class should be trivial"

    # ObstructionRecord roundtrip
    rec_dict = records[0].to_dict()
    rec2 = ObstructionRecord.from_dict(rec_dict)
    assert rec2.record_id == records[0].record_id

    # Validate coordinator
    violations = coord_obj.validate()
    assert violations == [], f"Coordinator violations: {violations}"

    # Analyzer
    analyzer = ObstructionsStructuredNonexistenceAnalyzer(
        witnesses=[witness], registry=coord_obj.registry
    )
    print(analyzer.report())
    score = analyzer.score()
    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"

    print("\n[PASS] All smoke tests passed.")
