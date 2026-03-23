"""Exact boundaries: where collection membership is definitively known vs unknown.

# copilot: collection-heap-encodings module 05 — exact boundaries and explicit non-membership
# theory2.tex §27.21–§27.25: a collection's membership function is defined only
# on an *exact boundary* — the set of indices for which membership is
# definitively established.  Outside this boundary, membership is unknown and
# must be treated as a proof obligation.

In the sheaf-theoretic collection model, a **membership obligation** is a local
section on an index whose presence or absence in the collection has not yet been
definitively established.  The **exact boundary** is the open cover of the index
space on which membership is known.

This module implements:

* :class:`ExactBoundaryEncoding` — records the known-membership / known-absent /
  unknown boundary for a collection encoding.
* :class:`MembershipObligation` — a proof obligation that must be discharged to
  extend the exact boundary to a new index.
* :class:`NonMembershipWitness` — a witness that an element is definitively
  NOT a member of the collection.
* :class:`BoundaryProof` — a completed proof that the exact boundary is
  definitive on some region.

Public functions
----------------
:func:`encode_exact_boundary`
    Build an ExactBoundaryEncoding from a collection and its index space.
:func:`generate_nonmembership_witness`
    Generate a NonMembershipWitness for an element provably not in the collection.
:func:`check_boundary_completeness`
    Check whether the exact boundary covers the full index space.

Theory invariants
-----------------
* Judgments are tuples ``(c, φ, A, E, O, B, T, Π)`` — NEVER booleans.
* Trust is an ordered algebra element — NEVER a float.
* TrustTier: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED.
* Obstructions are Čech H¹ cohomology classes.
* Descent returns GlobalSection OR DescentObstruction — never raises.
* ``raise_with_scope(code, message=..., provenance=...)`` signature.
"""

from __future__ import annotations

import abc
import collections
import functools
import hashlib
import itertools
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from functools import reduce
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)
_LOGGER = logger

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
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
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Trust tier algebra
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust tiers — PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED."""

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: TrustTier) -> TrustTier:
        return TrustTier(min(int(self), int(other)))

    def promote(self) -> TrustTier:
        return TrustTier(min(int(self) + 1, TrustTier.PROOF_BACKED))

    def demote(self) -> TrustTier:
        return TrustTier(max(int(self) - 1, TrustTier.PROPOSAL))

    def is_at_least(self, other: TrustTier) -> bool:
        return int(self) >= int(other)


# ---------------------------------------------------------------------------
# Judgment dataclass — (c, φ, A, E, O, B, T, Π) — NEVER a boolean
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Fields mirror the theory2.tex tuple: context, formula, assumptions,
    evidence, obligations, burden, trust (TrustTier), provenance.
    """
    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


# ---------------------------------------------------------------------------
# CechObstruction dataclass — Čech H¹ cohomology class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology class witnessing descent failure.

    Attributes
    ----------
    cover_id:      Identifier of the open cover {U_i}.
    cocycle:       frozenset of (i, j, σ_ij) triples.
    cohomology_class: Canonical string representative of [σ] ∈ Ȟ¹.
    description:   Human-readable explanation of the obstruction.
    """
    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff the Čech class is the trivial (zero) element."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MembershipStatus(str, Enum):
    """The definitive membership status for an index in the boundary."""

    MEMBER = "member"             # definitively in the collection
    NON_MEMBER = "non_member"     # definitively not in the collection
    UNKNOWN = "unknown"           # not yet determined
    CONDITIONAL = "conditional"   # member iff some obligation is discharged


class BoundaryKind(str, Enum):
    """What kind of boundary is being tracked."""

    LIST_INDEX = "list_index"       # natural number positions
    DICT_KEY = "dict_key"           # dictionary key membership
    SET_ELEMENT = "set_element"     # set element membership
    RANGE = "range"                 # contiguous integer range
    CUSTOM = "custom"               # user-supplied membership predicate


class WitnessKind(str, Enum):
    """How a non-membership witness was established."""

    EXHAUSTIVE_SEARCH = "exhaustive_search"  # full search confirmed absence
    RUNTIME_CHECK = "runtime_check"          # runtime __contains__ returned False
    TYPE_MISMATCH = "type_mismatch"          # element type cannot match key type
    BOUNDS_CHECK = "bounds_check"            # index out of bounds
    SOLVER_PROOF = "solver_proof"            # SMT solver proved non-membership
    CARDINALITY = "cardinality"              # cardinality argument


class CompletenessStatus(str, Enum):
    """The completeness of the exact boundary over the index space."""

    COMPLETE = "complete"           # every index in the known boundary
    PARTIAL = "partial"             # some indices are still unknown
    EMPTY_KNOWN = "empty_known"     # no indices have known membership
    TRIVIALLY_COMPLETE = "trivially_complete"  # empty collection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Čech obstruction for boundary descent
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BoundaryCechObstruction:
    """A Čech H¹ obstruction blocking exact boundary descent."""

    coordinate: str
    cocycle_description: str
    conflicting_indices: tuple[str, ...]
    trust_tier: TrustTier = TrustTier.PROPOSAL
    is_coboundary: bool = False
    repair_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "conflicting_indices": list(self.conflicting_indices),
            "trust_tier": self.trust_tier.name,
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
        }


# ---------------------------------------------------------------------------
# Judgment tuple — (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BoundaryJudgment:
    """A judgment about a collection boundary.  NEVER a boolean."""

    c: str
    phi: str
    A: str
    E: tuple[str, ...]
    O: tuple[str, ...]
    B: tuple[BoundaryCechObstruction, ...]
    T: TrustTier
    Pi: Mapping[str, Any]

    @property
    def is_settled(self) -> bool:
        return len(self.O) == 0 and len(self.B) == 0

    @property
    def is_obstructed(self) -> bool:
        return any(not ob.is_coboundary for ob in self.B)

    def with_obligation(self, ob: str) -> BoundaryJudgment:
        from dataclasses import replace
        return replace(self, O=(*self.O, ob))

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c, "phi": self.phi, "A": self.A,
            "E": list(self.E), "O": list(self.O),
            "B": [ob.to_dict() for ob in self.B],
            "T": self.T.name, "Pi": dict(self.Pi),
        }


def _make_boundary_judgment(
    coordinate: str, phi: str, carrier: str,
    evidence: Sequence[str], obligations: Sequence[str],
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Mapping[str, Any] | None = None,
) -> BoundaryJudgment:
    return BoundaryJudgment(
        c=coordinate, phi=phi, A=carrier,
        E=tuple(evidence), O=tuple(obligations), B=(),
        T=trust, Pi=dict(provenance or {}),
    )


# ---------------------------------------------------------------------------
# Index boundary entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IndexBoundaryEntry:
    """A single index with its known membership status.

    Attributes
    ----------
    index_id : str
        Unique index identifier.
    index_repr : str
        Human-readable representation of the index value.
    status : MembershipStatus
        The current membership status.
    membership_smt : str
        SMT-LIB2 expression asserting membership or non-membership.
    judgment : BoundaryJudgment
        The governing judgment.
    discharge_evidence : tuple[str, ...]
        Evidence that established this status.
    """

    index_id: str
    index_repr: str
    status: MembershipStatus
    membership_smt: str
    judgment: BoundaryJudgment
    discharge_evidence: tuple[str, ...] = ()

    def is_definitive(self) -> bool:
        """True iff membership status is conclusively established."""
        return self.status in (MembershipStatus.MEMBER, MembershipStatus.NON_MEMBER)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_id": self.index_id,
            "index_repr": self.index_repr,
            "status": self.status.value,
            "membership_smt": self.membership_smt,
            "is_definitive": self.is_definitive(),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Non-membership witness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NonMembershipWitness:
    """A witness that an element is definitively NOT a member of the collection.

    Non-membership witnesses are first-class semantic objects.  They are
    proof burdens that can be discharged via solver, runtime, or type analysis.

    Attributes
    ----------
    witness_id : str
        Unique identifier.
    element_repr : str
        Representation of the element proven absent.
    collection_coordinate : str
        Semantic coordinate of the collection.
    kind : WitnessKind
        How the non-membership was established.
    smt_expression : str
        SMT-LIB2 expression proving non-membership.
    judgment : BoundaryJudgment
        The governing judgment.
    explanation : str
        Human-readable explanation of why element is absent.
    """

    witness_id: str
    element_repr: str
    collection_coordinate: str
    kind: WitnessKind
    smt_expression: str
    judgment: BoundaryJudgment
    explanation: str = ""

    def is_runtime_witnessed(self) -> bool:
        return self.kind == WitnessKind.RUNTIME_CHECK

    def is_solver_proved(self) -> bool:
        return self.kind == WitnessKind.SOLVER_PROOF

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id,
            "element_repr": self.element_repr,
            "collection_coordinate": self.collection_coordinate,
            "kind": self.kind.value,
            "smt_expression": self.smt_expression,
            "explanation": self.explanation,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Membership obligation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MembershipObligation:
    """A proof obligation to determine membership of an element in a collection.

    Attributes
    ----------
    obligation_id : str
        Unique identifier.
    element_repr : str
        Representation of the element whose membership is unknown.
    collection_coordinate : str
        Semantic coordinate of the collection.
    index_id : str
        The index whose status is unknown.
    required_trust : TrustTier
        Minimum trust tier needed to discharge this obligation.
    judgment : BoundaryJudgment
        The governing judgment.
    discharge_candidates : tuple[str, ...]
        SMT-LIB2 expressions that would discharge this obligation.
    is_critical_path : bool
        Whether discharging this obligation is on the critical path.
    """

    obligation_id: str
    element_repr: str
    collection_coordinate: str
    index_id: str
    required_trust: TrustTier
    judgment: BoundaryJudgment
    discharge_candidates: tuple[str, ...] = ()
    is_critical_path: bool = False

    def is_dischargeable_at_runtime(self) -> bool:
        return self.required_trust.is_at_least(TrustTier.RUNTIME_WITNESSED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "element_repr": self.element_repr,
            "collection_coordinate": self.collection_coordinate,
            "index_id": self.index_id,
            "required_trust": self.required_trust.name,
            "is_critical_path": self.is_critical_path,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Boundary proof
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BoundaryProof:
    """A completed proof that the exact boundary is definitive on some region.

    Attributes
    ----------
    proof_id : str
        Unique identifier.
    boundary_coordinate : str
        Semantic coordinate of the boundary being proved.
    proven_member_indices : tuple[str, ...]
        Indices definitively in the collection.
    proven_nonmember_indices : tuple[str, ...]
        Indices definitively not in the collection.
    nonmembership_witnesses : tuple[NonMembershipWitness, ...]
        Witnesses for non-member indices.
    judgment : BoundaryJudgment
        The governing judgment.
    proof_technique : str
        Description of how the boundary was proved.
    """

    proof_id: str
    boundary_coordinate: str
    proven_member_indices: tuple[str, ...]
    proven_nonmember_indices: tuple[str, ...]
    nonmembership_witnesses: tuple[NonMembershipWitness, ...]
    judgment: BoundaryJudgment
    proof_technique: str = ""

    def total_proven(self) -> int:
        return len(self.proven_member_indices) + len(self.proven_nonmember_indices)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "boundary_coordinate": self.boundary_coordinate,
            "num_proven_members": len(self.proven_member_indices),
            "num_proven_nonmembers": len(self.proven_nonmember_indices),
            "total_proven": self.total_proven(),
            "proof_technique": self.proof_technique,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Global section and descent obstruction for boundary
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BoundaryGlobalSection:
    """A globally consistent exact boundary — all indices have definitive status."""

    coordinate: str
    member_indices: frozenset[str]
    nonmember_indices: frozenset[str]
    judgment: BoundaryJudgment

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "boundary_global_section",
            "coordinate": self.coordinate,
            "num_members": len(self.member_indices),
            "num_nonmembers": len(self.nonmember_indices),
            "judgment": self.judgment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BoundaryDescentObstruction:
    """A Čech obstruction blocking exact boundary verification.  NEVER raises."""

    coordinate: str
    obstruction: BoundaryCechObstruction
    unknown_indices: tuple[str, ...]
    diagnosis: str = ""
    repair_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "boundary_descent_obstruction",
            "coordinate": self.coordinate,
            "obstruction": self.obstruction.to_dict(),
            "unknown_indices": list(self.unknown_indices),
            "diagnosis": self.diagnosis,
            "repair_hints": list(self.repair_hints),
        }


# ---------------------------------------------------------------------------
# Exact boundary encoding
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ExactBoundaryEncoding:
    """Records the known-membership boundary for a collection.

    The exact boundary partitions the collection's index space into three
    disjoint regions:
    1. **Known-member** indices — membership is definitively established.
    2. **Known-nonmember** indices — absence is definitively established.
    3. **Unknown** indices — membership has not been determined.

    The *completeness* of the boundary is the fraction of indices that fall
    into regions 1 or 2.

    Attributes
    ----------
    boundary_id : str
        Unique identifier.
    collection_coordinate : str
        Semantic coordinate of the collection.
    kind : BoundaryKind
        The kind of boundary.
    entries : tuple[IndexBoundaryEntry, ...]
        All boundary entries, one per index.
    obligations : tuple[MembershipObligation, ...]
        Outstanding membership obligations for unknown indices.
    proofs : tuple[BoundaryProof, ...]
        Completed proofs of boundary segments.
    judgment : BoundaryJudgment
        The governing judgment.
    completeness : CompletenessStatus
        The completeness status of the boundary.
    """

    boundary_id: str
    collection_coordinate: str
    kind: BoundaryKind
    entries: tuple[IndexBoundaryEntry, ...]
    obligations: tuple[MembershipObligation, ...]
    proofs: tuple[BoundaryProof, ...]
    judgment: BoundaryJudgment
    completeness: CompletenessStatus = CompletenessStatus.PARTIAL

    def known_members(self) -> tuple[IndexBoundaryEntry, ...]:
        return tuple(e for e in self.entries if e.status == MembershipStatus.MEMBER)

    def known_nonmembers(self) -> tuple[IndexBoundaryEntry, ...]:
        return tuple(e for e in self.entries if e.status == MembershipStatus.NON_MEMBER)

    def unknown_entries(self) -> tuple[IndexBoundaryEntry, ...]:
        return tuple(e for e in self.entries if e.status == MembershipStatus.UNKNOWN)

    def completeness_fraction(self) -> float:
        if len(self.entries) == 0:
            return 1.0
        definitive = sum(1 for e in self.entries if e.is_definitive())
        return definitive / len(self.entries)

    def attempt_descent(self) -> BoundaryGlobalSection | BoundaryDescentObstruction:
        """Attempt to verify that the boundary is globally complete.  NEVER raises."""
        unknown = self.unknown_entries()
        if unknown:
            obs = BoundaryCechObstruction(
                coordinate=self.collection_coordinate,
                cocycle_description=(
                    f"{len(unknown)} indices with unknown membership status"
                ),
                conflicting_indices=tuple(e.index_id for e in unknown[:10]),
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion="Discharge membership obligations to extend the exact boundary.",
            )
            return BoundaryDescentObstruction(
                coordinate=self.collection_coordinate,
                obstruction=obs,
                unknown_indices=tuple(e.index_id for e in unknown[:20]),
                diagnosis=(
                    f"Exact boundary incomplete: {len(unknown)}/{len(self.entries)} "
                    f"indices have unknown membership."
                ),
                repair_hints=("discharge-membership-obligations", "extend-exact-boundary"),
            )
        jmt = _make_boundary_judgment(
            coordinate=self.collection_coordinate,
            phi="exact_boundary_complete",
            carrier="exact_boundary",
            evidence=[f"boundary:{self.boundary_id}"],
            obligations=[],
            trust=TrustTier.VERIFIED,
            provenance={
                "boundary_id": self.boundary_id,
                "descent_at": _now_iso(),
                "num_members": len(self.known_members()),
                "num_nonmembers": len(self.known_nonmembers()),
            },
        )
        return BoundaryGlobalSection(
            coordinate=self.collection_coordinate,
            member_indices=frozenset(e.index_id for e in self.known_members()),
            nonmember_indices=frozenset(e.index_id for e in self.known_nonmembers()),
            judgment=jmt,
        )

    def emit_smt_membership_axioms(self) -> list[str]:
        """Emit SMT-LIB2 membership axioms for all definitive entries."""
        return [e.membership_smt for e in self.entries if e.is_definitive()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "collection_coordinate": self.collection_coordinate,
            "kind": self.kind.value,
            "num_entries": len(self.entries),
            "num_known_members": len(self.known_members()),
            "num_known_nonmembers": len(self.known_nonmembers()),
            "num_unknown": len(self.unknown_entries()),
            "completeness_fraction": self.completeness_fraction(),
            "completeness": self.completeness.value,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def encode_exact_boundary(
    collection: list[Any] | dict[Any, Any] | set[Any],
    *,
    coordinate: str = "boundary_root",
    candidate_elements: Sequence[Any] | None = None,
) -> ExactBoundaryEncoding:
    """Build an ExactBoundaryEncoding from a collection and its index space.

    Parameters
    ----------
    collection : list | dict | set
        The Python collection to encode.
    coordinate : str
        Semantic coordinate.
    candidate_elements : Sequence[Any] or None
        Additional candidate elements to check for non-membership.

    Returns
    -------
    ExactBoundaryEncoding
    """
    logger.debug("encode_exact_boundary: type=%s coord=%s", type(collection).__name__, coordinate)
    entries: list[IndexBoundaryEntry] = []
    obligations: list[MembershipObligation] = []

    if isinstance(collection, list):
        kind = BoundaryKind.LIST_INDEX
        for i, val in enumerate(collection):
            idx_id = _stable_id(f"bdx:{coordinate}", str(i))
            jmt = _make_boundary_judgment(
                coordinate=f"{coordinate}[{i}]",
                phi=f"index_{i}_is_member",
                carrier="list_membership",
                evidence=[f"python_list_index:{i}"],
                obligations=[],
                trust=TrustTier.RUNTIME_WITNESSED,
                provenance={"index": i, "value_repr": repr(val)[:80]},
            )
            entries.append(IndexBoundaryEntry(
                index_id=idx_id,
                index_repr=str(i),
                status=MembershipStatus.MEMBER,
                membership_smt=f"(= (list-ref {coordinate} {i}) {repr(val)!r})",
                judgment=jmt,
                discharge_evidence=(f"runtime_index:{i}",),
            ))
        # Candidate non-members (out-of-bounds)
        n = len(collection)
        for candidate in (candidate_elements or [-1, n, n + 1]):
            if isinstance(candidate, int) and (candidate < 0 or candidate >= n):
                idx_id = _stable_id(f"bdx:{coordinate}", f"nonmember:{candidate}")
                nm_jmt = _make_boundary_judgment(
                    coordinate=f"{coordinate}[{candidate}]",
                    phi=f"index_{candidate}_out_of_bounds",
                    carrier="list_nonmembership",
                    evidence=[f"bounds_check:{candidate}>={n}"],
                    obligations=[],
                    trust=TrustTier.RUNTIME_WITNESSED,
                    provenance={"index": candidate, "length": n},
                )
                entries.append(IndexBoundaryEntry(
                    index_id=idx_id,
                    index_repr=str(candidate),
                    status=MembershipStatus.NON_MEMBER,
                    membership_smt=(
                        f"(or (< {candidate} 0) (>= {candidate} (list-len {coordinate})))"
                    ),
                    judgment=nm_jmt,
                    discharge_evidence=(f"bounds_check:{candidate}",),
                ))

    elif isinstance(collection, dict):
        kind = BoundaryKind.DICT_KEY
        for key, val in collection.items():
            idx_id = _stable_id(f"bdx:{coordinate}", repr(key))
            jmt = _make_boundary_judgment(
                coordinate=f"{coordinate}[{key!r}]",
                phi=f"key_{repr(key)}_is_member",
                carrier="dict_key_membership",
                evidence=[f"python_dict_key:{repr(key)!r}"],
                obligations=[],
                trust=TrustTier.RUNTIME_WITNESSED,
                provenance={"key_repr": repr(key)[:80]},
            )
            entries.append(IndexBoundaryEntry(
                index_id=idx_id,
                index_repr=repr(key)[:40],
                status=MembershipStatus.MEMBER,
                membership_smt=f"(contains-key {coordinate} {repr(key)!r})",
                judgment=jmt,
                discharge_evidence=(f"runtime_key:{repr(key)!r}",),
            ))
        for candidate in (candidate_elements or []):
            if candidate not in collection:
                idx_id = _stable_id(f"bdx:{coordinate}", f"nonmember:{repr(candidate)}")
                nm_jmt = _make_boundary_judgment(
                    coordinate=f"{coordinate}[nonmember:{repr(candidate)!r}]",
                    phi=f"key_{repr(candidate)}_absent",
                    carrier="dict_key_nonmembership",
                    evidence=[f"runtime_not_in:{repr(candidate)!r}"],
                    obligations=[],
                    trust=TrustTier.RUNTIME_WITNESSED,
                    provenance={"key_repr": repr(candidate)[:80]},
                )
                entries.append(IndexBoundaryEntry(
                    index_id=idx_id,
                    index_repr=repr(candidate)[:40],
                    status=MembershipStatus.NON_MEMBER,
                    membership_smt=f"(not (contains-key {coordinate} {repr(candidate)!r}))",
                    judgment=nm_jmt,
                    discharge_evidence=(f"runtime_not_contains:{repr(candidate)!r}",),
                ))

    else:  # set
        kind = BoundaryKind.SET_ELEMENT
        for elem in sorted(collection, key=repr):
            idx_id = _stable_id(f"bdx:{coordinate}", repr(elem))
            jmt = _make_boundary_judgment(
                coordinate=f"{coordinate}.elem[{repr(elem)!r}]",
                phi=f"element_{repr(elem)}_is_member",
                carrier="set_membership",
                evidence=[f"python_set_contains:{repr(elem)!r}"],
                obligations=[],
                trust=TrustTier.RUNTIME_WITNESSED,
                provenance={"elem_repr": repr(elem)[:80]},
            )
            entries.append(IndexBoundaryEntry(
                index_id=idx_id,
                index_repr=repr(elem)[:40],
                status=MembershipStatus.MEMBER,
                membership_smt=f"(set-contains {coordinate} {repr(elem)!r})",
                judgment=jmt,
                discharge_evidence=(f"runtime_contains:{repr(elem)!r}",),
            ))
        for candidate in (candidate_elements or []):
            if candidate not in collection:
                idx_id = _stable_id(f"bdx:{coordinate}", f"nm:{repr(candidate)}")
                nm_jmt = _make_boundary_judgment(
                    coordinate=f"{coordinate}.nonmember[{repr(candidate)!r}]",
                    phi=f"element_{repr(candidate)}_absent",
                    carrier="set_nonmembership",
                    evidence=[f"runtime_not_in:{repr(candidate)!r}"],
                    obligations=[],
                    trust=TrustTier.RUNTIME_WITNESSED,
                    provenance={"elem_repr": repr(candidate)[:80]},
                )
                entries.append(IndexBoundaryEntry(
                    index_id=idx_id,
                    index_repr=repr(candidate)[:40],
                    status=MembershipStatus.NON_MEMBER,
                    membership_smt=f"(not (set-contains {coordinate} {repr(candidate)!r}))",
                    judgment=nm_jmt,
                    discharge_evidence=(f"runtime_not_contains:{repr(candidate)!r}",),
                ))

    # Compute completeness
    all_definitive = all(e.is_definitive() for e in entries)
    has_any = len(entries) > 0
    if not has_any:
        completeness = CompletenessStatus.TRIVIALLY_COMPLETE
    elif all_definitive:
        completeness = CompletenessStatus.COMPLETE
    else:
        completeness = CompletenessStatus.PARTIAL

    top_jmt = _make_boundary_judgment(
        coordinate=coordinate,
        phi="exact_boundary_encoded",
        carrier="exact_boundary",
        evidence=[f"type:{type(collection).__name__}", f"size:{len(collection)}"],
        obligations=[] if all_definitive else ["discharge-unknown-memberships"],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"coordinate": coordinate, "encoded_at": _now_iso()},
    )
    return ExactBoundaryEncoding(
        boundary_id=_stable_id("boundary", coordinate),
        collection_coordinate=coordinate,
        kind=kind,
        entries=tuple(entries),
        obligations=tuple(obligations),
        proofs=(),
        judgment=top_jmt,
        completeness=completeness,
    )


def generate_nonmembership_witness(
    element: Any,
    collection: list[Any] | dict[Any, Any] | set[Any],
    *,
    collection_coordinate: str = "collection_root",
) -> NonMembershipWitness | None:
    """Generate a NonMembershipWitness for an element provably not in the collection.

    Returns None if the element IS in the collection or membership cannot be determined.

    Parameters
    ----------
    element : Any
        The element to check for non-membership.
    collection : list | dict | set
        The Python collection.
    collection_coordinate : str
        Semantic coordinate.

    Returns
    -------
    NonMembershipWitness | None
    """
    # Runtime check first
    try:
        if isinstance(collection, list):
            is_member = element in collection
        elif isinstance(collection, dict):
            is_member = element in collection
        else:
            is_member = element in collection
    except TypeError:
        # Type error means definitely not a member (e.g., unhashable key in dict)
        kind = WitnessKind.TYPE_MISMATCH
        jmt = _make_boundary_judgment(
            coordinate=f"{collection_coordinate}.nonmember",
            phi=f"type_mismatch_non_membership",
            carrier="nonmembership_witness",
            evidence=["type_mismatch"],
            obligations=[],
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance={"element_repr": repr(element)[:80]},
        )
        return NonMembershipWitness(
            witness_id=_stable_id("nm_witness", collection_coordinate + repr(element)),
            element_repr=repr(element)[:80],
            collection_coordinate=collection_coordinate,
            kind=kind,
            smt_expression=f"(not (type-compatible {repr(element)!r} {collection_coordinate}))",
            judgment=jmt,
            explanation=f"Type of {repr(element)} is incompatible with collection key type.",
        )

    if is_member:
        return None  # element IS a member — no non-membership witness

    # Determine witness kind
    if isinstance(collection, list) and isinstance(element, int):
        kind = WitnessKind.BOUNDS_CHECK
        explanation = f"Index {element} is out of bounds for list of length {len(collection)}."
        smt = f"(or (< {element} 0) (>= {element} (list-len {collection_coordinate})))"
    else:
        kind = WitnessKind.RUNTIME_CHECK
        explanation = f"{repr(element)!r} is not contained in {collection_coordinate}."
        smt = f"(not (contains {collection_coordinate} {repr(element)!r}))"

    trust = TrustTier.RUNTIME_WITNESSED
    jmt = _make_boundary_judgment(
        coordinate=f"{collection_coordinate}.nonmember",
        phi="element_absent",
        carrier="nonmembership_witness",
        evidence=[f"runtime_check:{repr(element)!r}"],
        obligations=[],
        trust=trust,
        provenance={"element_repr": repr(element)[:80], "kind": kind.value},
    )
    return NonMembershipWitness(
        witness_id=_stable_id("nm_witness", collection_coordinate + repr(element)),
        element_repr=repr(element)[:80],
        collection_coordinate=collection_coordinate,
        kind=kind,
        smt_expression=smt,
        judgment=jmt,
        explanation=explanation,
    )


def check_boundary_completeness(
    boundary: ExactBoundaryEncoding,
) -> BoundaryGlobalSection | BoundaryDescentObstruction:
    """Check whether the exact boundary covers the full index space.

    Delegates to the boundary's own descent attempt.
    NEVER raises.

    Parameters
    ----------
    boundary : ExactBoundaryEncoding
        The boundary to check.

    Returns
    -------
    BoundaryGlobalSection | BoundaryDescentObstruction
    """
    return boundary.attempt_descent()


# ---------------------------------------------------------------------------
# Boundary statistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BoundaryStats:
    """Aggregate statistics for a collection of boundary encodings."""

    total_boundaries: int
    complete_boundaries: int
    partial_boundaries: int
    total_entries: int
    total_members: int
    total_nonmembers: int
    total_unknown: int
    total_obligations: int
    total_proofs: int

    @classmethod
    def from_boundaries(cls, boundaries: Sequence[ExactBoundaryEncoding]) -> BoundaryStats:
        complete = sum(1 for b in boundaries if b.completeness == CompletenessStatus.COMPLETE)
        partial = sum(1 for b in boundaries if b.completeness == CompletenessStatus.PARTIAL)
        entries = sum(len(b.entries) for b in boundaries)
        members = sum(len(b.known_members()) for b in boundaries)
        nonmembers = sum(len(b.known_nonmembers()) for b in boundaries)
        unknown = sum(len(b.unknown_entries()) for b in boundaries)
        obligations = sum(len(b.obligations) for b in boundaries)
        proofs = sum(len(b.proofs) for b in boundaries)
        return cls(
            total_boundaries=len(boundaries),
            complete_boundaries=complete,
            partial_boundaries=partial,
            total_entries=entries,
            total_members=members,
            total_nonmembers=nonmembers,
            total_unknown=unknown,
            total_obligations=obligations,
            total_proofs=proofs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_boundaries": self.total_boundaries,
            "complete_boundaries": self.complete_boundaries,
            "partial_boundaries": self.partial_boundaries,
            "total_entries": self.total_entries,
            "total_members": self.total_members,
            "total_nonmembers": self.total_nonmembers,
            "total_unknown": self.total_unknown,
            "total_obligations": self.total_obligations,
        }




# ---------------------------------------------------------------------------
# BoundaryChecker
# ---------------------------------------------------------------------------


class BoundaryChecker:
    """Stateful checker for exact boundary completeness and membership obligations.

    BoundaryChecker accepts a stream of ExactBoundaryEncodings and checks them
    for completeness, outstanding obligations, and Čech obstructions.

    Invariants
    ----------
    - Judgments produced are 8-tuples — never booleans
    - Trust is an ordered algebra — never a float
    - Obstructions are Čech H¹ cohomology classes
    """

    def __init__(self, trust_threshold: TrustTier = TrustTier.VERIFIED) -> None:
        """Initialise the checker with a minimum trust threshold."""
        self.trust_threshold = trust_threshold
        self._boundaries: list[ExactBoundaryEncoding] = []
        self._results: list = []

    def add_boundary(self, boundary: ExactBoundaryEncoding) -> None:
        """Add a boundary encoding to the checker's queue."""
        self._boundaries.append(boundary)

    def check_all(self) -> list:
        """Check all registered boundaries and return a list of descent results.

        Returns a list of BoundaryGlobalSection | BoundaryDescentObstruction,
        one per registered boundary.
        """
        self._results = [b.attempt_descent() for b in self._boundaries]
        return list(self._results)

    def is_fully_discharged(self) -> bool:
        """Return True iff every registered boundary has no outstanding obligations."""
        if not self._results:
            self.check_all()
        return all(
            isinstance(r, BoundaryGlobalSection) for r in self._results
        )

    def obligations_summary(self) -> list[str]:
        """Return a list of outstanding obligation strings across all boundaries."""
        items = []
        for b in self._boundaries:
            for o in b.obligations:
                items.append(f"{b.boundary_id}: {o.description}")
        return items

    def report(self) -> str:
        """Return a human-readable report of the checker's state."""
        if not self._results:
            self.check_all()
        lines = [
            f"BoundaryChecker report (threshold={self.trust_threshold.name}):",
            f"  boundaries: {len(self._boundaries)}",
        ]
        for b, r in zip(self._boundaries, self._results):
            status = "✓ complete" if isinstance(r, BoundaryGlobalSection) else "✗ obstructed"
            lines.append(f"    {b.boundary_id} — {status}")
        return "\n".join(lines)


__all__ = [
    "BoundaryChecker",
    "BoundaryDescentObstruction",
    "BoundaryGlobalSection",
    "BoundaryJudgment",
    "BoundaryKind",
    "BoundaryProof",
    "BoundaryCechObstruction",
    "BoundaryStats",
    "CompletenessStatus",
    "ExactBoundaryEncoding",
    "IndexBoundaryEntry",
    "MembershipObligation",
    "MembershipStatus",
    "NonMembershipWitness",
    "TrustTier",
    "WitnessKind",
    "check_boundary_completeness",
    "encode_exact_boundary",
    "generate_nonmembership_witness",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== exact_boundaries_and_explicit_non — smoke test ===")

    # List boundary
    lst = [10, 20, 30, 40]
    b_list = encode_exact_boundary(lst, coordinate="test.list")
    print(f"List boundary: entries={len(b_list.entries)} "
          f"members={len(b_list.known_members())} "
          f"completeness={b_list.completeness.value}")
    assert b_list.completeness == CompletenessStatus.COMPLETE
    result = check_boundary_completeness(b_list)
    print(f"Descent: {type(result).__name__}")
    assert isinstance(result, BoundaryGlobalSection)

    # Dict boundary with non-members
    dct = {"a": 1, "b": 2}
    b_dict = encode_exact_boundary(dct, coordinate="test.dict",
                                   candidate_elements=["c", "d"])
    print(f"Dict boundary: entries={len(b_dict.entries)} "
          f"nonmembers={len(b_dict.known_nonmembers())}")
    assert len(b_dict.known_nonmembers()) == 2

    # Set boundary
    st = {1, 2, 3}
    b_set = encode_exact_boundary(st, coordinate="test.set",
                                  candidate_elements=[4, 5])
    print(f"Set boundary: members={len(b_set.known_members())} "
          f"nonmembers={len(b_set.known_nonmembers())}")

    # Non-membership witness
    w1 = generate_nonmembership_witness(99, lst, collection_coordinate="test.list")
    assert w1 is not None, "Should produce witness for 99 not in [10,20,30,40]"
    print(f"NonMembershipWitness: kind={w1.kind.value} expr={w1.smt_expression[:50]}")

    w2 = generate_nonmembership_witness(20, lst, collection_coordinate="test.list")
    assert w2 is None, "20 IS in the list, should return None"
    print("Non-membership witness for member: correctly None")

    # Partial boundary (unknown entries)
    partial_boundary = ExactBoundaryEncoding(
        boundary_id="partial_test",
        collection_coordinate="test.partial",
        kind=BoundaryKind.LIST_INDEX,
        entries=(
            IndexBoundaryEntry(
                index_id="i0",
                index_repr="0",
                status=MembershipStatus.MEMBER,
                membership_smt="(= (list-ref x 0) 10)",
                judgment=_make_boundary_judgment(
                    "test", "test", "test", [], [], TrustTier.PROPOSAL),
            ),
            IndexBoundaryEntry(
                index_id="i1",
                index_repr="1",
                status=MembershipStatus.UNKNOWN,
                membership_smt="(unknown (list-ref x 1))",
                judgment=_make_boundary_judgment(
                    "test", "test", "test", [], [], TrustTier.PROPOSAL),
            ),
        ),
        obligations=(),
        proofs=(),
        judgment=_make_boundary_judgment(
            "test.partial", "boundary", "boundary", [], [], TrustTier.PROPOSAL),
        completeness=CompletenessStatus.PARTIAL,
    )
    obstruction_result = check_boundary_completeness(partial_boundary)
    print(f"Partial boundary descent: {type(obstruction_result).__name__}")
    assert isinstance(obstruction_result, BoundaryDescentObstruction)

    # SMT axioms
    axioms = b_list.emit_smt_membership_axioms()
    print(f"SMT membership axioms ({len(axioms)}): {axioms[:2]}")

    # Statistics
    stats = BoundaryStats.from_boundaries([b_list, b_dict, b_set])
    print(f"Stats: {stats.to_dict()}")
    assert stats.total_boundaries == 3
    assert stats.complete_boundaries == 3

    # Trust algebra
    t = TrustTier.RUNTIME_WITNESSED
    assert t.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    print("TrustTier: OK")

    # JSON serialization
    d = b_list.to_dict()
    j = json.dumps(d, default=str)
    assert "boundary_id" in j
    print("JSON serialization: OK")

    print("All assertions passed.")
    sys.exit(0)
