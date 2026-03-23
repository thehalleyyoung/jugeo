"""Collections encoded as families of sections over a sheaf of indices.

# copilot: collection-heap-encodings module 01 — families of sections
# theory2.tex §27: Collections are not flat arrays; they are indexed families
# of local sections over a sheaf of index objects.  Each element is a section
# over its index, and membership is a restriction map from the total sheaf to
# the stalk at that index.

A Python ``list``, ``dict``, or ``set`` is not simply a container of values —
it is a **family of sections** over a sheaf of indices.  This module
implements that view faithfully, providing:

* :class:`IndexedFamilyRepr` — an indexed family of sections, parameterised
  by an index set and a section functor.
* :class:`ElementSheaf` — the sheaf of elements over the index space, equipped
  with restriction maps that enable cover-based reasoning.
* :class:`CollectionCoverStrategy` — a strategy for choosing an open cover of
  the index space from which descent can be attempted.
* :class:`CollectionEncoding` — the top-level encoding record tying together
  the sheaf, cover, and descent obligation.

Public functions
----------------
:func:`encode_list_as_family`
    Encode a Python list as an indexed family over the natural-number index sheaf.
:func:`encode_dict_as_sheaf`
    Encode a Python dict as a sheaf over the key-space index.
:func:`encode_set_as_quotient`
    Encode a Python set as a quotient family (membership = section existence).

Governing invariants (from theory2.tex)
-----------------------------------------
* Judgments are tuples ``(c, φ, A, E, O, B, T, Π)`` — NEVER booleans.
* Trust is an element of the ordered algebra ``(E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ)``
  — NEVER a scalar float.
* TrustTier: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED.
* Obstructions are Čech H¹ cohomology classes, not ephemeral error messages.
* Descent returns a :class:`GlobalSection` or a :class:`DescentObstruction`
  — it NEVER raises.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Final, Generic, Iterable, Mapping, Sequence, TypeVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo imports — graceful degradation when running standalone
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
        GEOMETRY = "geometry"
        ENCODING = "encoding"
        UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"
        DESCENT_OBSTRUCTION = "descent_obstruction"
        UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None:
            self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind,
        JudgmentStatus,
        PropositionKind,
        ProvenanceSource,
        TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"

# ---------------------------------------------------------------------------
# Trust tier — PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust tiers for collection section judgments.

    These tiers form the ascending chain
    PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED
    as required by theory2.tex §§232–235.
    Trust is an algebraic object — NEVER a scalar float.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Least upper bound in the trust lattice (⊕ operation)."""
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: TrustTier) -> TrustTier:
        """Greatest lower bound in the trust lattice (⊖ operation)."""
        return TrustTier(min(int(self), int(other)))

    def is_at_least(self, threshold: TrustTier) -> bool:
        """True iff this tier satisfies *threshold* in the ordering ⪯."""
        return int(self) >= int(threshold)

    def promote(self) -> TrustTier:
        """One step up the trust ladder (↑_π), clamped at PROOF_BACKED."""
        return TrustTier(min(int(self) + 1, TrustTier.PROOF_BACKED))

    def demote(self) -> TrustTier:
        """One step down the trust ladder (↓_χ), clamped at PROPOSAL."""
        return TrustTier(max(int(self) - 1, TrustTier.PROPOSAL))


# ---------------------------------------------------------------------------
# Core encoding enumerations
# ---------------------------------------------------------------------------

class IndexKind(str, Enum):
    """The kind of index space used by a collection sheaf."""

    NATURAL_NUMBER = "natural_number"   # lists: ℕ₀…(n-1)
    KEY_SPACE = "key_space"             # dicts: arbitrary hashable keys
    MEMBERSHIP = "membership"           # sets: Boolean-valued membership
    SLICE = "slice"                     # windows/slices: contiguous sub-ranges
    PRODUCT = "product"                 # nested product index spaces


class SectionStatus(str, Enum):
    """Lifecycle status of a single section in the collection sheaf."""

    PRESENT = "present"         # element is confirmed to exist at this index
    ABSENT = "absent"           # element is confirmed absent (e.g. sparse)
    UNKNOWN = "unknown"         # existence not yet determined
    OBSTRUCTED = "obstructed"   # descent failed — cohomology class present


class CoverStrategyKind(str, Enum):
    """How to cover the index space for descent checking."""

    TRIVIAL = "trivial"         # single cover element = whole space
    DYADIC = "dyadic"           # halve the index range recursively
    PARTITION = "partition"     # explicit user-supplied partition
    ČECH = "cech"               # Čech nerve refinement


# ---------------------------------------------------------------------------
# Judgment coordinate and evidence helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, payload: str) -> str:
    """Produce a deterministic short ID for logging and tracing."""
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Obstruction record — Čech H¹ cohomology class
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CechObstruction:
    """A Čech H¹ cohomology class blocking global section reconstruction.

    Obstructions are persistent semantic objects, not ephemeral errors.
    They record exactly *which* cocycle failed to be a coboundary and
    carry provenance for downstream repair planning.

    Attributes
    ----------
    coordinate : str
        The semantic coordinate where the obstruction lives.
    cocycle_description : str
        Human-readable description of the failing cocycle.
    cover_indices : tuple[str, ...]
        The cover elements on whose overlaps the cocycle was detected.
    trust_tier : TrustTier
        Trust level of the obstruction witness.
    provenance : Mapping[str, Any]
        How and where the obstruction was discovered.
    is_coboundary : bool
        If True, the obstruction is trivially resolvable (coboundary class).
    repair_suggestion : str
        One concrete repair suggestion for upstream consumers.
    """

    coordinate: str
    cocycle_description: str
    cover_indices: tuple[str, ...]
    trust_tier: TrustTier = TrustTier.PROPOSAL
    provenance: Mapping[str, Any] = field(default_factory=dict)
    is_coboundary: bool = False
    repair_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "cover_indices": list(self.cover_indices),
            "trust_tier": self.trust_tier.name,
            "provenance": dict(self.provenance),
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
        }


# ---------------------------------------------------------------------------
# Judgment tuple — (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CollectionJudgment:
    """A judgment about a collection encoding.

    This is THE central semantic object — a tuple
    ``(c, φ, A, E, O, B, T, Π)`` as specified in theory2.tex.
    It is NEVER a boolean.

    Attributes
    ----------
    c : str
        Coordinate (where in the project the claim lives).
    phi : str
        Proposition φ (what is claimed).
    A : str
        Carrier / type (what kind of thing the claim is about).
    E : tuple[str, ...]
        Evidence bundle (evidence items, each with channel and trust level).
    O : tuple[str, ...]
        Residual obligations (what remains to be verified).
    B : tuple[CechObstruction, ...]
        Obstructions (Čech H¹ cohomology classes blocking verification).
    T : TrustTier
        Trust annotation — an algebraic element, NEVER a float.
    Pi : Mapping[str, Any]
        Provenance (where the judgment came from).
    """

    c: str
    phi: str
    A: str
    E: tuple[str, ...]
    O: tuple[str, ...]
    B: tuple[CechObstruction, ...]
    T: TrustTier
    Pi: Mapping[str, Any]

    @property
    def is_settled(self) -> bool:
        """True iff there are no residual obligations and no obstructions."""
        return len(self.O) == 0 and len(self.B) == 0

    @property
    def is_obstructed(self) -> bool:
        """True iff there are non-coboundary Čech obstructions present."""
        return any(not ob.is_coboundary for ob in self.B)

    def with_obligation(self, obligation: str) -> CollectionJudgment:
        """Return a copy with an additional residual obligation."""
        from dataclasses import replace
        return replace(self, O=(*self.O, obligation))

    def with_obstruction(self, obs: CechObstruction) -> CollectionJudgment:
        """Return a copy with an additional Čech obstruction."""
        from dataclasses import replace
        return replace(self, B=(*self.B, obs))

    def promote_trust(self) -> CollectionJudgment:
        """Return a copy with trust tier promoted one step."""
        from dataclasses import replace
        return replace(self, T=self.T.promote())

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c,
            "phi": self.phi,
            "A": self.A,
            "E": list(self.E),
            "O": list(self.O),
            "B": [ob.to_dict() for ob in self.B],
            "T": self.T.name,
            "Pi": dict(self.Pi),
        }


# ---------------------------------------------------------------------------
# Global section and descent obstruction (descent NEVER raises)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GlobalSection:
    """A successfully reconstructed global section from a collection sheaf.

    Returned by descent when all local sections are consistent on overlaps.
    """

    coordinate: str
    element_map: Mapping[str, Any]
    judgment: CollectionJudgment
    reconstruction_time_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "global_section",
            "coordinate": self.coordinate,
            "element_map": dict(self.element_map),
            "judgment": self.judgment.to_dict(),
            "reconstruction_time_ns": self.reconstruction_time_ns,
        }


@dataclass(frozen=True, slots=True)
class DescentObstruction:
    """A Čech obstruction returned when descent fails.

    Descent NEVER raises — it returns a DescentObstruction when the local
    sections cannot be glued into a global section.
    """

    coordinate: str
    obstruction: CechObstruction
    partial_sections: tuple[tuple[str, Any], ...]
    diagnosis: str = ""
    repair_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "descent_obstruction",
            "coordinate": self.coordinate,
            "obstruction": self.obstruction.to_dict(),
            "partial_sections": list(self.partial_sections),
            "diagnosis": self.diagnosis,
            "repair_hints": list(self.repair_hints),
        }


# ---------------------------------------------------------------------------
# Index space and local sections
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IndexObject:
    """A single index object in the index sheaf.

    In the list case this is a natural number; in the dict case it is a
    hashable key; in the set case it is a membership witness.
    """

    index_id: str
    index_value: Any
    kind: IndexKind
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_id": self.index_id,
            "index_value": str(self.index_value),
            "kind": self.kind.value,
        }


@dataclass(frozen=True, slots=True)
class LocalSection:
    """A section of the collection sheaf over a single index object.

    The section carries the element value, a status (present/absent/unknown),
    and a judgment tuple recording what is known about this element.
    """

    index: IndexObject
    value: Any
    status: SectionStatus
    judgment: CollectionJudgment
    section_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def is_consistent_with(self, other: LocalSection) -> bool:
        """Check restriction compatibility with *other* on the overlap."""
        if self.index.index_value == other.index.index_value:
            return self.value == other.value
        return True  # sections at different indices are trivially compatible

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "index": self.index.to_dict(),
            "value": str(self.value),
            "status": self.status.value,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Element sheaf
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ElementSheaf:
    """The sheaf of elements over the collection's index space.

    The sheaf assigns to each open subset of the index space a set of
    compatible local sections.  Restriction maps move sections from larger
    opens to smaller ones.  Gluing moves from consistent local sections to
    a global section.

    Attributes
    ----------
    sheaf_id : str
        Unique identifier for this sheaf instance.
    index_kind : IndexKind
        The kind of the index space.
    sections : tuple[LocalSection, ...]
        All local sections currently tracked.
    cover_ids : tuple[str, ...]
        The index ids that form the current open cover.
    coordinate : str
        The semantic coordinate at which this sheaf lives.
    """

    sheaf_id: str
    index_kind: IndexKind
    sections: tuple[LocalSection, ...]
    cover_ids: tuple[str, ...]
    coordinate: str

    def restrict(self, index_id: str) -> LocalSection | None:
        """Restriction map: return the section stalk at *index_id*."""
        for sec in self.sections:
            if sec.index.index_id == index_id:
                return sec
        return None

    def sections_on_cover(self) -> tuple[LocalSection, ...]:
        """Return sections whose index is in the cover."""
        cover_set = frozenset(self.cover_ids)
        return tuple(s for s in self.sections if s.index.index_id in cover_set)

    def check_overlap_consistency(self) -> list[tuple[str, str, bool]]:
        """Check that all pairs of cover sections are compatible on overlaps.

        Returns a list of (id_a, id_b, is_consistent) triples.
        """
        cover_sections = self.sections_on_cover()
        results: list[tuple[str, str, bool]] = []
        for i, s_a in enumerate(cover_sections):
            for s_b in cover_sections[i + 1:]:
                consistent = s_a.is_consistent_with(s_b)
                results.append((s_a.section_id, s_b.section_id, consistent))
        return results

    def attempt_descent(self) -> GlobalSection | DescentObstruction:
        """Attempt to glue local sections into a global section.

        Descent NEVER raises — it returns either a GlobalSection or a
        DescentObstruction encoding the Čech H¹ cohomology obstruction.
        """
        t0 = time.monotonic_ns()
        inconsistencies = [
            (a, b) for a, b, ok in self.check_overlap_consistency() if not ok
        ]
        if inconsistencies:
            obs = CechObstruction(
                coordinate=self.coordinate,
                cocycle_description=(
                    f"Čech 1-cocycle: inconsistent sections on overlaps "
                    f"{inconsistencies[:3]!r}"
                ),
                cover_indices=self.cover_ids,
                trust_tier=TrustTier.PROPOSAL,
                provenance={"sheaf_id": self.sheaf_id, "detected_at": _now_iso()},
                is_coboundary=False,
                repair_suggestion="Refine the cover or discharge element obligations.",
            )
            return DescentObstruction(
                coordinate=self.coordinate,
                obstruction=obs,
                partial_sections=tuple(
                    (s.section_id, s.value)
                    for s in self.sections_on_cover()
                ),
                diagnosis=f"{len(inconsistencies)} overlap inconsistencies detected.",
                repair_hints=("refine-cover", "discharge-element-obligations"),
            )
        element_map: dict[str, Any] = {
            s.index.index_id: s.value for s in self.sections_on_cover()
        }
        jmt = CollectionJudgment(
            c=self.coordinate,
            phi="global_section_exists",
            A="collection_element_sheaf",
            E=tuple(s.section_id for s in self.sections_on_cover()),
            O=(),
            B=(),
            T=TrustTier.VERIFIED,
            Pi={
                "sheaf_id": self.sheaf_id,
                "descent_at": _now_iso(),
                "num_sections": len(self.sections),
            },
        )
        return GlobalSection(
            coordinate=self.coordinate,
            element_map=element_map,
            judgment=jmt,
            reconstruction_time_ns=time.monotonic_ns() - t0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheaf_id": self.sheaf_id,
            "index_kind": self.index_kind.value,
            "coordinate": self.coordinate,
            "num_sections": len(self.sections),
            "cover_ids": list(self.cover_ids),
        }


# ---------------------------------------------------------------------------
# Cover strategies
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CollectionCoverStrategy:
    """A strategy for choosing an open cover of the collection's index space.

    The choice of cover determines what descent obligations are generated
    and what Čech cohomology classes can be detected.

    Attributes
    ----------
    kind : CoverStrategyKind
        The covering strategy.
    max_cover_elements : int
        Upper bound on the number of cover elements generated.
    overlap_fraction : float
        Fraction of overlap between adjacent cover elements (for DYADIC).
    custom_partition : tuple[tuple[str, ...], ...]
        User-supplied explicit partition (for PARTITION strategy).
    """

    kind: CoverStrategyKind = CoverStrategyKind.TRIVIAL
    max_cover_elements: int = 64
    overlap_fraction: float = 0.0
    custom_partition: tuple[tuple[str, ...], ...] = ()

    def build_cover(
        self, index_ids: Sequence[str]
    ) -> tuple[tuple[str, ...], ...]:
        """Build an open cover of *index_ids* according to the strategy."""
        n = len(index_ids)
        if self.kind == CoverStrategyKind.TRIVIAL:
            return (tuple(index_ids),)
        if self.kind == CoverStrategyKind.PARTITION:
            return self.custom_partition or (tuple(index_ids),)
        if self.kind == CoverStrategyKind.DYADIC:
            return self._dyadic_cover(list(index_ids))
        if self.kind == CoverStrategyKind.ČECH:
            return self._cech_nerve_cover(list(index_ids))
        return (tuple(index_ids),)

    def _dyadic_cover(
        self, ids: list[str], depth: int = 0
    ) -> tuple[tuple[str, ...], ...]:
        """Recursive dyadic halving with optional overlap."""
        n = len(ids)
        if n <= 2 or depth >= int(math.log2(self.max_cover_elements + 1)):
            return (tuple(ids),)
        mid = n // 2
        overlap = max(1, int(n * self.overlap_fraction))
        left = ids[: mid + overlap]
        right = ids[mid - overlap :]
        return (
            *self._dyadic_cover(left, depth + 1),
            *self._dyadic_cover(right, depth + 1),
        )

    def _cech_nerve_cover(
        self, ids: list[str]
    ) -> tuple[tuple[str, ...], ...]:
        """Build a Čech nerve cover: every singleton plus all pairwise overlaps."""
        singletons = tuple((iid,) for iid in ids)
        if len(ids) <= 1:
            return singletons
        pairs = tuple(
            (a, b) for a, b in itertools.combinations(ids, 2)
        )
        return singletons + pairs[:self.max_cover_elements]


# ---------------------------------------------------------------------------
# Indexed family representation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IndexedFamilyRepr:
    """An indexed family of sections representing a Python collection.

    This is the sheaf-theoretic view: the collection is a functor from
    the category of index objects to the category of section types.

    Attributes
    ----------
    family_id : str
        Unique identifier.
    collection_kind : str
        ``"list"``, ``"dict"``, or ``"set"``.
    index_kind : IndexKind
        The kind of index space.
    indices : tuple[IndexObject, ...]
        All index objects in the family.
    sections : tuple[LocalSection, ...]
        The local sections, one (or zero) per index.
    total_cardinality : int
        The known cardinality of the collection.
    judgment : CollectionJudgment
        The governing judgment tuple ``(c, φ, A, E, O, B, T, Π)``.
    """

    family_id: str
    collection_kind: str
    index_kind: IndexKind
    indices: tuple[IndexObject, ...]
    sections: tuple[LocalSection, ...]
    total_cardinality: int
    judgment: CollectionJudgment

    def get_section(self, index_id: str) -> LocalSection | None:
        for sec in self.sections:
            if sec.index.index_id == index_id:
                return sec
        return None

    def missing_sections(self) -> tuple[IndexObject, ...]:
        """Return indices for which no section is present."""
        present = frozenset(s.index.index_id for s in self.sections)
        return tuple(idx for idx in self.indices if idx.index_id not in present)

    def is_complete(self) -> bool:
        """True iff every index has a PRESENT section."""
        return len(self.missing_sections()) == 0 and all(
            s.status == SectionStatus.PRESENT for s in self.sections
        )

    def obligation_summary(self) -> list[str]:
        """Return a list of outstanding verification obligations."""
        obligations: list[str] = list(self.judgment.O)
        for idx in self.missing_sections():
            obligations.append(f"missing-section:{idx.index_id}")
        for sec in self.sections:
            if sec.status == SectionStatus.UNKNOWN:
                obligations.append(f"unknown-section:{sec.section_id}")
        return obligations

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "collection_kind": self.collection_kind,
            "index_kind": self.index_kind.value,
            "total_cardinality": self.total_cardinality,
            "num_indices": len(self.indices),
            "num_sections": len(self.sections),
            "is_complete": self.is_complete(),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Top-level encoding record
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CollectionEncoding:
    """Top-level encoding of a Python collection as a sheaf family.

    This is the object passed between encoding pipeline stages.

    Attributes
    ----------
    encoding_id : str
        Unique identifier for this encoding instance.
    source_type : str
        The Python type name of the original collection.
    family : IndexedFamilyRepr
        The indexed family representation.
    sheaf : ElementSheaf
        The element sheaf with restriction maps.
    cover_strategy : CollectionCoverStrategy
        The cover strategy used to partition the index space.
    descent_result : GlobalSection | DescentObstruction | None
        The result of the most recent descent attempt, if any.
    created_at : str
        ISO-8601 creation timestamp.
    encoding_metadata : Mapping[str, Any]
        Pipeline metadata (encoder version, parameters, etc.).
    """

    encoding_id: str
    source_type: str
    family: IndexedFamilyRepr
    sheaf: ElementSheaf
    cover_strategy: CollectionCoverStrategy
    descent_result: GlobalSection | DescentObstruction | None = None
    created_at: str = field(default_factory=_now_iso)
    encoding_metadata: Mapping[str, Any] = field(default_factory=dict)

    def is_globally_consistent(self) -> bool:
        """True iff descent succeeded and returned a GlobalSection."""
        return isinstance(self.descent_result, GlobalSection)

    def get_obstruction(self) -> CechObstruction | None:
        """Return the Čech H¹ obstruction if descent failed."""
        if isinstance(self.descent_result, DescentObstruction):
            return self.descent_result.obstruction
        return None

    def to_dict(self) -> dict[str, Any]:
        dr: dict[str, Any] | None = None
        if self.descent_result is not None:
            dr = self.descent_result.to_dict()
        return {
            "encoding_id": self.encoding_id,
            "source_type": self.source_type,
            "family": self.family.to_dict(),
            "sheaf": self.sheaf.to_dict(),
            "cover_strategy": self.cover_strategy.kind.value,
            "descent_result": dr,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Encoding factory helpers
# ---------------------------------------------------------------------------

def _make_judgment(
    coordinate: str,
    phi: str,
    carrier: str,
    evidence: Sequence[str],
    obligations: Sequence[str],
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Mapping[str, Any] | None = None,
) -> CollectionJudgment:
    """Construct a well-formed judgment tuple ``(c, φ, A, E, O, B, T, Π)``."""
    return CollectionJudgment(
        c=coordinate,
        phi=phi,
        A=carrier,
        E=tuple(evidence),
        O=tuple(obligations),
        B=(),
        T=trust,
        Pi=dict(provenance or {}),
    )


def _build_natural_number_index(
    length: int, coordinate: str
) -> tuple[IndexObject, ...]:
    """Build the natural-number index space 0..length-1."""
    return tuple(
        IndexObject(
            index_id=_stable_id(coordinate, str(i)),
            index_value=i,
            kind=IndexKind.NATURAL_NUMBER,
            metadata={"position": i},
        )
        for i in range(length)
    )


def _build_local_section(
    index: IndexObject, value: Any, coordinate: str
) -> LocalSection:
    """Build a single local section with a RUNTIME_WITNESSED judgment."""
    jmt = _make_judgment(
        coordinate=f"{coordinate}[{index.index_value}]",
        phi=f"element_at_index_{index.index_value}",
        carrier="element",
        evidence=[f"runtime_value:{id(value):x}"],
        obligations=[],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"source": "python_runtime", "index": str(index.index_value)},
    )
    return LocalSection(
        index=index,
        value=value,
        status=SectionStatus.PRESENT,
        judgment=jmt,
        section_id=_stable_id(f"sec:{coordinate}", str(index.index_id)),
    )


# ---------------------------------------------------------------------------
# Public encoding functions
# ---------------------------------------------------------------------------

def encode_list_as_family(
    lst: list[Any],
    *,
    coordinate: str = "list_root",
    cover_strategy: CollectionCoverStrategy | None = None,
) -> CollectionEncoding:
    """Encode a Python list as an indexed family over ℕ₀..(n-1).

    Each element ``lst[i]`` becomes a local section over the index object
    ``i``.  The cover strategy determines how descent is performed.

    Parameters
    ----------
    lst : list[Any]
        The Python list to encode.
    coordinate : str
        The semantic coordinate at which this encoding lives.
    cover_strategy : CollectionCoverStrategy or None
        The cover strategy to use; defaults to dyadic halving.

    Returns
    -------
    CollectionEncoding
        The fully constructed encoding with descent result.
    """
    logger.debug("encode_list_as_family: n=%d coordinate=%s", len(lst), coordinate)
    if cover_strategy is None:
        cover_strategy = CollectionCoverStrategy(
            kind=CoverStrategyKind.DYADIC,
            max_cover_elements=min(64, max(4, len(lst) // 2)),
            overlap_fraction=0.1,
        )
    indices = _build_natural_number_index(len(lst), coordinate)
    sections = tuple(
        _build_local_section(idx, val, coordinate)
        for idx, val in zip(indices, lst)
    )
    cover_parts = cover_strategy.build_cover([idx.index_id for idx in indices])
    flat_cover = tuple(dict.fromkeys(iid for part in cover_parts for iid in part))

    family_judgment = _make_judgment(
        coordinate=coordinate,
        phi="list_encoded_as_indexed_family",
        carrier="list_section_sheaf",
        evidence=[f"python_list_len:{len(lst)}"],
        obligations=(
            ["verify_index_ordering", "verify_cardinality_constraint"]
            if len(lst) > 0 else ["empty_list_trivially_satisfied"]
        ),
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"encoder": "encode_list_as_family", "timestamp": _now_iso()},
    )
    family = IndexedFamilyRepr(
        family_id=_stable_id("family", coordinate + str(len(lst))),
        collection_kind="list",
        index_kind=IndexKind.NATURAL_NUMBER,
        indices=indices,
        sections=sections,
        total_cardinality=len(lst),
        judgment=family_judgment,
    )
    sheaf = ElementSheaf(
        sheaf_id=_stable_id("sheaf", coordinate),
        index_kind=IndexKind.NATURAL_NUMBER,
        sections=sections,
        cover_ids=flat_cover,
        coordinate=coordinate,
    )
    descent_result = sheaf.attempt_descent()
    encoding_id = str(uuid.uuid4())
    return CollectionEncoding(
        encoding_id=encoding_id,
        source_type="list",
        family=family,
        sheaf=sheaf,
        cover_strategy=cover_strategy,
        descent_result=descent_result,
        created_at=_now_iso(),
        encoding_metadata={
            "python_len": len(lst),
            "cover_kind": cover_strategy.kind.value,
            "num_cover_elements": len(flat_cover),
        },
    )


def encode_dict_as_sheaf(
    dct: dict[Any, Any],
    *,
    coordinate: str = "dict_root",
    cover_strategy: CollectionCoverStrategy | None = None,
) -> CollectionEncoding:
    """Encode a Python dict as a sheaf over its key-space index.

    Each key–value pair ``(k, v)`` becomes a local section at the index
    object ``k``.  Key identity is tracked via the index's ``index_id``.

    Parameters
    ----------
    dct : dict[Any, Any]
        The Python dict to encode.
    coordinate : str
        The semantic coordinate.
    cover_strategy : CollectionCoverStrategy or None
        Cover strategy; defaults to trivial (single cover element).

    Returns
    -------
    CollectionEncoding
    """
    logger.debug("encode_dict_as_sheaf: keys=%d coordinate=%s", len(dct), coordinate)
    if cover_strategy is None:
        cover_strategy = CollectionCoverStrategy(kind=CoverStrategyKind.TRIVIAL)
    indices: list[IndexObject] = []
    sections: list[LocalSection] = []
    for i, (key, val) in enumerate(dct.items()):
        idx_id = _stable_id(f"{coordinate}.key", f"{i}:{key!r}")
        idx = IndexObject(
            index_id=idx_id,
            index_value=key,
            kind=IndexKind.KEY_SPACE,
            metadata={"key_repr": repr(key), "position": i},
        )
        indices.append(idx)
        sections.append(_build_local_section(idx, val, coordinate))

    cover_parts = cover_strategy.build_cover([idx.index_id for idx in indices])
    flat_cover = tuple(dict.fromkeys(iid for part in cover_parts for iid in part))

    family_judgment = _make_judgment(
        coordinate=coordinate,
        phi="dict_encoded_as_key_space_sheaf",
        carrier="dict_section_sheaf",
        evidence=[f"python_dict_len:{len(dct)}"],
        obligations=["verify_key_uniqueness", "verify_value_type_invariants"],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"encoder": "encode_dict_as_sheaf", "timestamp": _now_iso()},
    )
    family = IndexedFamilyRepr(
        family_id=_stable_id("family", coordinate + "dict"),
        collection_kind="dict",
        index_kind=IndexKind.KEY_SPACE,
        indices=tuple(indices),
        sections=tuple(sections),
        total_cardinality=len(dct),
        judgment=family_judgment,
    )
    sheaf = ElementSheaf(
        sheaf_id=_stable_id("sheaf", coordinate + "dict"),
        index_kind=IndexKind.KEY_SPACE,
        sections=tuple(sections),
        cover_ids=flat_cover,
        coordinate=coordinate,
    )
    descent_result = sheaf.attempt_descent()
    return CollectionEncoding(
        encoding_id=str(uuid.uuid4()),
        source_type="dict",
        family=family,
        sheaf=sheaf,
        cover_strategy=cover_strategy,
        descent_result=descent_result,
        created_at=_now_iso(),
        encoding_metadata={
            "python_len": len(dct),
            "cover_kind": cover_strategy.kind.value,
        },
    )


def encode_set_as_quotient(
    st: set[Any],
    *,
    coordinate: str = "set_root",
    cover_strategy: CollectionCoverStrategy | None = None,
) -> CollectionEncoding:
    """Encode a Python set as a quotient family.

    Membership is a section existence predicate: ``x ∈ S`` iff there exists
    a local section over the membership-index at ``x``.  The quotient
    structure handles deduplication.

    Parameters
    ----------
    st : set[Any]
        The Python set to encode.
    coordinate : str
        The semantic coordinate.
    cover_strategy : CollectionCoverStrategy or None
        Cover strategy; defaults to trivial.

    Returns
    -------
    CollectionEncoding
    """
    logger.debug("encode_set_as_quotient: n=%d coordinate=%s", len(st), coordinate)
    if cover_strategy is None:
        cover_strategy = CollectionCoverStrategy(kind=CoverStrategyKind.TRIVIAL)
    sorted_elements = sorted(st, key=lambda x: repr(x))
    indices: list[IndexObject] = []
    sections: list[LocalSection] = []
    for i, elem in enumerate(sorted_elements):
        idx_id = _stable_id(f"{coordinate}.member", f"{i}:{elem!r}")
        idx = IndexObject(
            index_id=idx_id,
            index_value=elem,
            kind=IndexKind.MEMBERSHIP,
            metadata={"element_repr": repr(elem), "position": i},
        )
        indices.append(idx)
        jmt = _make_judgment(
            coordinate=f"{coordinate}.member[{i}]",
            phi="element_is_member",
            carrier="set_membership_witness",
            evidence=[f"set_contains:{repr(elem)}"],
            obligations=[],
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance={"element": repr(elem)},
        )
        sections.append(
            LocalSection(
                index=idx,
                value=elem,
                status=SectionStatus.PRESENT,
                judgment=jmt,
                section_id=idx_id,
            )
        )
    cover_parts = cover_strategy.build_cover([idx.index_id for idx in indices])
    flat_cover = tuple(dict.fromkeys(iid for part in cover_parts for iid in part))

    family_judgment = _make_judgment(
        coordinate=coordinate,
        phi="set_encoded_as_membership_quotient",
        carrier="set_section_sheaf",
        evidence=[f"python_set_len:{len(st)}"],
        obligations=["verify_membership_uniqueness"],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"encoder": "encode_set_as_quotient", "timestamp": _now_iso()},
    )
    family = IndexedFamilyRepr(
        family_id=_stable_id("family", coordinate + "set"),
        collection_kind="set",
        index_kind=IndexKind.MEMBERSHIP,
        indices=tuple(indices),
        sections=tuple(sections),
        total_cardinality=len(st),
        judgment=family_judgment,
    )
    sheaf = ElementSheaf(
        sheaf_id=_stable_id("sheaf", coordinate + "set"),
        index_kind=IndexKind.MEMBERSHIP,
        sections=tuple(sections),
        cover_ids=flat_cover,
        coordinate=coordinate,
    )
    descent_result = sheaf.attempt_descent()
    return CollectionEncoding(
        encoding_id=str(uuid.uuid4()),
        source_type="set",
        family=family,
        sheaf=sheaf,
        cover_strategy=cover_strategy,
        descent_result=descent_result,
        created_at=_now_iso(),
        encoding_metadata={"python_len": len(st), "cover_kind": cover_strategy.kind.value},
    )


# ---------------------------------------------------------------------------
# Statistics and diagnostics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EncodingStatistics:
    """Aggregate statistics about a batch of collection encodings."""

    total_encodings: int
    list_count: int
    dict_count: int
    set_count: int
    descent_successes: int
    descent_failures: int
    total_sections: int
    total_obligations: int

    @classmethod
    def from_encodings(cls, encodings: Sequence[CollectionEncoding]) -> EncodingStatistics:
        list_count = sum(1 for e in encodings if e.source_type == "list")
        dict_count = sum(1 for e in encodings if e.source_type == "dict")
        set_count = sum(1 for e in encodings if e.source_type == "set")
        successes = sum(1 for e in encodings if isinstance(e.descent_result, GlobalSection))
        failures = sum(1 for e in encodings if isinstance(e.descent_result, DescentObstruction))
        sections = sum(len(e.sheaf.sections) for e in encodings)
        obligations = sum(len(e.family.obligation_summary()) for e in encodings)
        return cls(
            total_encodings=len(encodings),
            list_count=list_count,
            dict_count=dict_count,
            set_count=set_count,
            descent_successes=successes,
            descent_failures=failures,
            total_sections=sections,
            total_obligations=obligations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_encodings": self.total_encodings,
            "list_count": self.list_count,
            "dict_count": self.dict_count,
            "set_count": self.set_count,
            "descent_successes": self.descent_successes,
            "descent_failures": self.descent_failures,
            "total_sections": self.total_sections,
            "total_obligations": self.total_obligations,
        }


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "CechObstruction",
    "CollectionCoverStrategy",
    "CollectionEncoding",
    "CollectionJudgment",
    "CoverStrategyKind",
    "DescentObstruction",
    "ElementSheaf",
    "EncodingStatistics",
    "GlobalSection",
    "IndexKind",
    "IndexObject",
    "IndexedFamilyRepr",
    "LocalSection",
    "SectionStatus",
    "TrustTier",
    "encode_dict_as_sheaf",
    "encode_list_as_family",
    "encode_set_as_quotient",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== collection_encodings_should_be_fam — smoke test ===")

    # Encode a list
    lst = [10, 20, 30, 40, 50]
    enc_list = encode_list_as_family(lst, coordinate="test.list")
    print(f"List encoding: id={enc_list.encoding_id[:12]}… "
          f"descent={'OK' if enc_list.is_globally_consistent() else 'OBSTRUCTED'}")
    assert enc_list.is_globally_consistent(), "List descent should succeed"
    assert enc_list.family.is_complete(), "All list sections should be present"

    # Encode a dict
    dct = {"alpha": 1, "beta": 2, "gamma": 3}
    enc_dict = encode_dict_as_sheaf(dct, coordinate="test.dict")
    print(f"Dict encoding: id={enc_dict.encoding_id[:12]}… "
          f"descent={'OK' if enc_dict.is_globally_consistent() else 'OBSTRUCTED'}")
    assert enc_dict.is_globally_consistent(), "Dict descent should succeed"

    # Encode a set
    st = {7, 14, 21, 28}
    enc_set = encode_set_as_quotient(st, coordinate="test.set")
    print(f"Set encoding: id={enc_set.encoding_id[:12]}… "
          f"descent={'OK' if enc_set.is_globally_consistent() else 'OBSTRUCTED'}")
    assert enc_set.is_globally_consistent(), "Set descent should succeed"

    # Trust tier algebra
    t1 = TrustTier.PROPOSAL
    t2 = TrustTier.VERIFIED
    joined = t1.join(t2)
    assert joined == TrustTier.VERIFIED, "join should give VERIFIED"
    met = t1.meet(t2)
    assert met == TrustTier.PROPOSAL, "meet should give PROPOSAL"
    print(f"TrustTier algebra: {t1.name} ⊕ {t2.name} = {joined.name}, "
          f"{t1.name} ⊗ {t2.name} = {met.name}")

    # Statistics
    stats = EncodingStatistics.from_encodings([enc_list, enc_dict, enc_set])
    print(f"Statistics: {stats.to_dict()}")
    assert stats.total_encodings == 3
    assert stats.descent_successes == 3

    # Serialization round-trip
    enc_json = json.dumps(enc_list.to_dict(), default=str)
    reloaded = json.loads(enc_json)
    assert reloaded["source_type"] == "list"
    print("JSON round-trip: OK")

    # Cover strategy test — Čech nerve
    strat = CollectionCoverStrategy(kind=CoverStrategyKind.ČECH, max_cover_elements=10)
    cover = strat.build_cover(["a", "b", "c"])
    print(f"Čech nerve cover: {cover}")
    assert any(len(part) == 2 for part in cover), "Čech cover should have pairs"

    print("All assertions passed.")
    sys.exit(0)
