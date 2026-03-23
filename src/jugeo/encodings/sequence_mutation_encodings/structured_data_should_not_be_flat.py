"""Sequences encoded as structured presheaves, not flat arrays.

# copilot: sequence-mutation-encodings module 01 — structured sequence presheaves
# theory2.tex §28: sequences are not flat arrays — they are presheaves over the
# ordered index category, with restriction maps that enforce consistency between
# adjacent elements and slices.

A Python sequence (list, tuple, string, bytes) is often treated as a flat array,
but for semantic verification purposes it must be treated as a **presheaf** over
the *ordered index category*: the natural numbers ordered by ≤.  Under this view:

* An element at index ``i`` is a section over the singleton ``{i}``.
* A slice ``[a:b]`` is a section over the open interval ``[a, b)``.
* Two slices are *compatible* iff they agree on their overlap.
* Concatenation is *gluing* of compatible sections.

This module provides:

* :class:`SequenceSheaf` — the presheaf of sequence sections.
* :class:`IndexedSlice` — a contiguous sub-sequence with its index range.
* :class:`SequenceSection` — a local section of the sequence presheaf.
* :class:`StructuredSequenceEncoding` — the top-level encoding record.

Public functions
----------------
:func:`encode_sequence_as_sheaf`
    Encode a Python sequence as a structured presheaf.
:func:`slice_sequence`
    Extract a contiguous IndexedSlice with its local judgment.
:func:`sequence_restriction`
    Restrict a SequenceSection to a sub-interval.

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

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Generic, Iterable, Mapping, Sequence, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

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
        ENCODING_MISMATCH = "encoding_mismatch"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import TrustLevel
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5

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
# Enumerations
# ---------------------------------------------------------------------------

class SequenceKind(str, Enum):
    """The concrete Python type being encoded."""

    LIST = "list"
    TUPLE = "tuple"
    STRING = "string"
    BYTES = "bytes"
    RANGE = "range"
    CUSTOM = "custom"


class SliceStatus(str, Enum):
    """The verification status of an IndexedSlice."""

    CONSISTENT = "consistent"     # slice is internally consistent
    INCONSISTENT = "inconsistent"  # elements contradict expected values
    UNVERIFIED = "unverified"      # not yet checked
    OVERLAPPING = "overlapping"    # overlaps with another slice (may be compatible)


class RestrictionKind(str, Enum):
    """The kind of restriction map applied to a sequence section."""

    PREFIX = "prefix"          # restrict to first k elements
    SUFFIX = "suffix"          # restrict to last k elements
    SUBRANGE = "subrange"      # restrict to [a, b)
    SINGLETON = "singleton"    # restrict to single element {i}
    STEP = "step"              # restrict to elements at indices i, i+k, i+2k, ...


class PresheafStatus(str, Enum):
    """The global status of the sequence presheaf."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    PARTIAL = "partial"
    EMPTY = "empty"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Čech obstruction for sequence descent
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SequenceCechObstruction:
    """A Čech H¹ obstruction blocking sequence section gluing."""

    coordinate: str
    cocycle_description: str
    conflicting_sections: tuple[str, ...]
    overlap_range: tuple[int, int]
    trust_tier: TrustTier = TrustTier.PROPOSAL
    is_coboundary: bool = False
    repair_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "conflicting_sections": list(self.conflicting_sections),
            "overlap_range": list(self.overlap_range),
            "trust_tier": self.trust_tier.name,
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
        }


# ---------------------------------------------------------------------------
# Judgment tuple — (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SequenceJudgment:
    """A judgment about a sequence section.  NEVER a boolean."""

    c: str
    phi: str
    A: str
    E: tuple[str, ...]
    O: tuple[str, ...]
    B: tuple[SequenceCechObstruction, ...]
    T: TrustTier
    Pi: Mapping[str, Any]

    @property
    def is_settled(self) -> bool:
        return len(self.O) == 0 and len(self.B) == 0

    @property
    def is_obstructed(self) -> bool:
        return any(not ob.is_coboundary for ob in self.B)

    def with_obligation(self, ob: str) -> SequenceJudgment:
        from dataclasses import replace
        return replace(self, O=(*self.O, ob))

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c, "phi": self.phi, "A": self.A,
            "E": list(self.E), "O": list(self.O),
            "B": [ob.to_dict() for ob in self.B],
            "T": self.T.name, "Pi": dict(self.Pi),
        }


def _make_seq_judgment(
    coordinate: str, phi: str, carrier: str,
    evidence: Sequence[str], obligations: Sequence[str],
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Mapping[str, Any] | None = None,
) -> SequenceJudgment:
    return SequenceJudgment(
        c=coordinate, phi=phi, A=carrier,
        E=tuple(evidence), O=tuple(obligations), B=(),
        T=trust, Pi=dict(provenance or {}),
    )


# ---------------------------------------------------------------------------
# Indexed slice
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IndexedSlice:
    """A contiguous sub-sequence with its index range and local judgment.

    An IndexedSlice corresponds to an open interval [start, stop) in the
    ordered index category.  The elements field stores the actual values
    for concrete slices, while smt_array stores the SMT-LIB2 array expression.

    Attributes
    ----------
    slice_id : str
        Unique identifier.
    start : int
        Inclusive start index.
    stop : int
        Exclusive stop index.
    step : int
        Step size (1 for contiguous).
    elements : tuple[Any, ...]
        The concrete element values (may be None for symbolic slices).
    smt_array_expr : str
        SMT-LIB2 array expression for this slice.
    judgment : SequenceJudgment
        Governing judgment.
    status : SliceStatus
        Current verification status.
    """

    slice_id: str
    start: int
    stop: int
    step: int
    elements: tuple[Any, ...]
    smt_array_expr: str
    judgment: SequenceJudgment
    status: SliceStatus = SliceStatus.UNVERIFIED

    @property
    def length(self) -> int:
        return max(0, math.ceil((self.stop - self.start) / self.step))

    def overlaps_with(self, other: IndexedSlice) -> bool:
        """True iff [self.start, self.stop) ∩ [other.start, other.stop) ≠ ∅."""
        return max(self.start, other.start) < min(self.stop, other.stop)

    def overlap_range(self, other: IndexedSlice) -> tuple[int, int] | None:
        """Return the overlap interval or None."""
        lo = max(self.start, other.start)
        hi = min(self.stop, other.stop)
        if lo < hi:
            return (lo, hi)
        return None

    def is_compatible_with(self, other: IndexedSlice) -> bool:
        """Check that overlapping elements have the same values."""
        ovl = self.overlap_range(other)
        if ovl is None:
            return True
        lo, hi = ovl
        for i in range(lo, hi, self.step):
            a_idx = i - self.start
            b_idx = i - other.start
            if (a_idx < len(self.elements) and b_idx < len(other.elements)
                    and self.elements[a_idx] != other.elements[b_idx]):
                return False
        return True

    def restrict_to(self, a: int, b: int) -> IndexedSlice:
        """Return a new IndexedSlice restricted to [a, b)."""
        lo = max(self.start, a)
        hi = min(self.stop, b)
        if lo >= hi:
            return IndexedSlice(
                slice_id=_stable_id("slice", f"{self.slice_id}:{a}:{b}"),
                start=lo, stop=hi, step=self.step,
                elements=(),
                smt_array_expr=f"(slice {self.smt_array_expr} {lo} {hi})",
                judgment=self.judgment,
                status=self.status,
            )
        new_elems = self.elements[lo - self.start: hi - self.start: self.step]
        return IndexedSlice(
            slice_id=_stable_id("slice", f"{self.slice_id}:{a}:{b}"),
            start=lo, stop=hi, step=self.step,
            elements=new_elems,
            smt_array_expr=f"(slice {self.smt_array_expr} {lo} {hi})",
            judgment=self.judgment,
            status=self.status,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "start": self.start,
            "stop": self.stop,
            "step": self.step,
            "length": self.length,
            "status": self.status.value,
            "smt_array_expr": self.smt_array_expr,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Sequence section
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SequenceSection:
    """A local section of the sequence presheaf over a sub-interval.

    Attributes
    ----------
    section_id : str
        Unique identifier.
    coordinate : str
        Semantic coordinate.
    slice_ : IndexedSlice
        The underlying slice.
    restriction_kind : RestrictionKind
        How this section was obtained.
    predecessor_section_id : str or None
        The section from which this was restricted, if any.
    judgment : SequenceJudgment
        Governing judgment.
    """

    section_id: str
    coordinate: str
    slice_: IndexedSlice
    restriction_kind: RestrictionKind
    predecessor_section_id: str | None
    judgment: SequenceJudgment

    def is_consistent_with(self, other: SequenceSection) -> bool:
        """Check compatibility on overlapping ranges."""
        return self.slice_.is_compatible_with(other.slice_)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "coordinate": self.coordinate,
            "slice": self.slice_.to_dict(),
            "restriction_kind": self.restriction_kind.value,
            "predecessor_section_id": self.predecessor_section_id,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Global section and descent obstruction for sequences
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SequenceGlobalSection:
    """A globally consistent sequence section reconstructed by descent."""

    coordinate: str
    total_elements: tuple[Any, ...]
    length: int
    judgment: SequenceJudgment

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "sequence_global_section",
            "coordinate": self.coordinate,
            "length": self.length,
            "judgment": self.judgment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SequenceDescentObstruction:
    """A Čech obstruction blocking sequence section gluing.  NEVER raises."""

    coordinate: str
    obstruction: SequenceCechObstruction
    conflicting_section_pairs: tuple[tuple[str, str], ...]
    diagnosis: str = ""
    repair_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "sequence_descent_obstruction",
            "coordinate": self.coordinate,
            "obstruction": self.obstruction.to_dict(),
            "conflicting_section_pairs": list(self.conflicting_section_pairs),
            "diagnosis": self.diagnosis,
        }


# ---------------------------------------------------------------------------
# Sequence sheaf
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SequenceSheaf:
    """The presheaf of sequence sections over the ordered index category.

    The sheaf assigns to each interval [a, b) a set of compatible sections.
    Restriction maps take sections on larger intervals to sections on smaller ones.
    Gluing takes compatible sections on overlapping intervals to a section on
    their union.

    Attributes
    ----------
    sheaf_id : str
        Unique identifier.
    kind : SequenceKind
        The Python sequence type.
    total_length : int
        Length of the complete sequence.
    sections : tuple[SequenceSection, ...]
        All tracked sections.
    coordinate : str
        Semantic coordinate.
    """

    sheaf_id: str
    kind: SequenceKind
    total_length: int
    sections: tuple[SequenceSection, ...]
    coordinate: str

    def get_section_covering(self, a: int, b: int) -> SequenceSection | None:
        """Return a section whose range contains [a, b), if any."""
        for sec in self.sections:
            if sec.slice_.start <= a and sec.slice_.stop >= b:
                return sec
        return None

    def find_overlap_inconsistencies(self) -> list[tuple[str, str, tuple[int, int]]]:
        """Find pairs of sections that are inconsistent on their overlap."""
        inconsistencies: list[tuple[str, str, tuple[int, int]]] = []
        sec_list = list(self.sections)
        for i, s1 in enumerate(sec_list):
            for s2 in sec_list[i + 1:]:
                ovl = s1.slice_.overlap_range(s2.slice_)
                if ovl and not s1.is_consistent_with(s2):
                    inconsistencies.append((s1.section_id, s2.section_id, ovl))
        return inconsistencies

    def attempt_descent(self) -> SequenceGlobalSection | SequenceDescentObstruction:
        """Attempt to glue all sections into a global section.  NEVER raises."""
        t0 = time.monotonic_ns()
        inconsistencies = self.find_overlap_inconsistencies()
        if inconsistencies:
            sid1, sid2, ovl = inconsistencies[0]
            obs = SequenceCechObstruction(
                coordinate=self.coordinate,
                cocycle_description=(
                    f"Sections {sid1[:12]} and {sid2[:12]} disagree on "
                    f"overlap [{ovl[0]}, {ovl[1]})"
                ),
                conflicting_sections=(sid1, sid2),
                overlap_range=ovl,
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion="Reconcile overlapping sequence sections.",
            )
            return SequenceDescentObstruction(
                coordinate=self.coordinate,
                obstruction=obs,
                conflicting_section_pairs=tuple(
                    (a, b) for a, b, _ in inconsistencies[:10]
                ),
                diagnosis=f"{len(inconsistencies)} overlap inconsistencies.",
                repair_hints=("reconcile-overlapping-sections",),
            )
        # Reconstruct total sequence by merging sections
        total: list[Any] = [None] * self.total_length
        for sec in self.sections:
            for i, val in enumerate(sec.slice_.elements):
                idx = sec.slice_.start + i * sec.slice_.step
                if 0 <= idx < self.total_length:
                    total[idx] = val
        jmt = _make_seq_judgment(
            coordinate=self.coordinate,
            phi="sequence_globally_consistent",
            carrier="sequence_sheaf",
            evidence=[f"sheaf:{self.sheaf_id}"],
            obligations=[],
            trust=TrustTier.VERIFIED,
            provenance={"sheaf_id": self.sheaf_id, "descent_at": _now_iso()},
        )
        return SequenceGlobalSection(
            coordinate=self.coordinate,
            total_elements=tuple(total),
            length=self.total_length,
            judgment=jmt,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheaf_id": self.sheaf_id,
            "kind": self.kind.value,
            "total_length": self.total_length,
            "num_sections": len(self.sections),
            "coordinate": self.coordinate,
        }


# ---------------------------------------------------------------------------
# Top-level structured sequence encoding
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StructuredSequenceEncoding:
    """Top-level encoding of a Python sequence as a structured presheaf.

    Attributes
    ----------
    encoding_id : str
        Unique identifier.
    source_type : SequenceKind
        The Python type of the original sequence.
    sheaf : SequenceSheaf
        The presheaf.
    root_section : SequenceSection
        The full-length root section.
    descent_result : SequenceGlobalSection | SequenceDescentObstruction | None
        Most recent descent result.
    judgment : SequenceJudgment
        Top-level judgment.
    created_at : str
        ISO-8601 creation timestamp.
    encoding_metadata : Mapping[str, Any]
        Pipeline metadata.
    """

    encoding_id: str
    source_type: SequenceKind
    sheaf: SequenceSheaf
    root_section: SequenceSection
    descent_result: SequenceGlobalSection | SequenceDescentObstruction | None
    judgment: SequenceJudgment
    created_at: str = field(default_factory=_now_iso)
    encoding_metadata: Mapping[str, Any] = field(default_factory=dict)

    def is_globally_consistent(self) -> bool:
        return isinstance(self.descent_result, SequenceGlobalSection)

    def to_dict(self) -> dict[str, Any]:
        dr = self.descent_result.to_dict() if self.descent_result else None
        return {
            "encoding_id": self.encoding_id,
            "source_type": self.source_type.value,
            "sheaf": self.sheaf.to_dict(),
            "descent_result": dr,
            "is_globally_consistent": self.is_globally_consistent(),
            "created_at": self.created_at,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def encode_sequence_as_sheaf(
    seq: list[Any] | tuple[Any, ...] | str | bytes,
    *,
    coordinate: str = "seq_root",
    chunk_size: int = 8,
) -> StructuredSequenceEncoding:
    """Encode a Python sequence as a structured presheaf.

    The sequence is divided into overlapping chunks, each becoming a local
    section.  Descent is performed to verify global consistency.

    Parameters
    ----------
    seq : list | tuple | str | bytes
        The sequence to encode.
    coordinate : str
        Semantic coordinate.
    chunk_size : int
        Maximum size of each section chunk.

    Returns
    -------
    StructuredSequenceEncoding
    """
    logger.debug(
        "encode_sequence_as_sheaf: len=%d type=%s coordinate=%s",
        len(seq), type(seq).__name__, coordinate,
    )
    if isinstance(seq, list):
        kind = SequenceKind.LIST
    elif isinstance(seq, tuple):
        kind = SequenceKind.TUPLE
    elif isinstance(seq, str):
        kind = SequenceKind.STRING
    elif isinstance(seq, bytes):
        kind = SequenceKind.BYTES
    else:
        kind = SequenceKind.CUSTOM

    n = len(seq)
    sections: list[SequenceSection] = []

    # Root (full) section
    root_jmt = _make_seq_judgment(
        coordinate=coordinate,
        phi="full_sequence_section",
        carrier="sequence_section",
        evidence=[f"type:{kind.value}", f"length:{n}"],
        obligations=["verify_all_elements"],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"kind": kind.value, "length": n},
    )
    full_slice = IndexedSlice(
        slice_id=_stable_id("slice:full", coordinate),
        start=0, stop=n, step=1,
        elements=tuple(seq),
        smt_array_expr=f"(declare-seq {coordinate} {n})",
        judgment=root_jmt,
        status=SliceStatus.CONSISTENT,
    )
    root_section = SequenceSection(
        section_id=_stable_id("section:full", coordinate),
        coordinate=coordinate,
        slice_=full_slice,
        restriction_kind=RestrictionKind.SUBRANGE,
        predecessor_section_id=None,
        judgment=root_jmt,
    )
    sections.append(root_section)

    # Chunked sections
    for chunk_start in range(0, n, chunk_size):
        chunk_stop = min(chunk_start + chunk_size, n)
        chunk_elems = tuple(seq[chunk_start:chunk_stop])
        chunk_jmt = _make_seq_judgment(
            coordinate=f"{coordinate}[{chunk_start}:{chunk_stop}]",
            phi=f"chunk_{chunk_start}_{chunk_stop}",
            carrier="sequence_chunk",
            evidence=[f"chunk:[{chunk_start},{chunk_stop})"],
            obligations=[],
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance={"chunk_start": chunk_start, "chunk_stop": chunk_stop},
        )
        chunk_slice = IndexedSlice(
            slice_id=_stable_id("slice:chunk", f"{coordinate}:{chunk_start}:{chunk_stop}"),
            start=chunk_start, stop=chunk_stop, step=1,
            elements=chunk_elems,
            smt_array_expr=(
                f"(slice {coordinate} {chunk_start} {chunk_stop})"
            ),
            judgment=chunk_jmt,
            status=SliceStatus.CONSISTENT,
        )
        chunk_section = SequenceSection(
            section_id=_stable_id("section:chunk", f"{coordinate}:{chunk_start}:{chunk_stop}"),
            coordinate=f"{coordinate}[{chunk_start}:{chunk_stop}]",
            slice_=chunk_slice,
            restriction_kind=RestrictionKind.SUBRANGE,
            predecessor_section_id=root_section.section_id,
            judgment=chunk_jmt,
        )
        sections.append(chunk_section)

    sheaf = SequenceSheaf(
        sheaf_id=_stable_id("sheaf", coordinate),
        kind=kind,
        total_length=n,
        sections=tuple(sections),
        coordinate=coordinate,
    )
    top_jmt = _make_seq_judgment(
        coordinate=coordinate,
        phi="sequence_encoded_as_presheaf",
        carrier="structured_sequence",
        evidence=[f"length:{n}", f"kind:{kind.value}"],
        obligations=[],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"coordinate": coordinate, "encoded_at": _now_iso()},
    )
    descent_result = sheaf.attempt_descent()
    return StructuredSequenceEncoding(
        encoding_id=str(uuid.uuid4()),
        source_type=kind,
        sheaf=sheaf,
        root_section=root_section,
        descent_result=descent_result,
        judgment=top_jmt,
        created_at=_now_iso(),
        encoding_metadata={"chunk_size": chunk_size, "length": n},
    )


def slice_sequence(
    encoding: StructuredSequenceEncoding,
    start: int,
    stop: int,
    *,
    step: int = 1,
) -> IndexedSlice:
    """Extract a contiguous IndexedSlice from a structured sequence encoding.

    Parameters
    ----------
    encoding : StructuredSequenceEncoding
        The encoding to slice.
    start : int
        Inclusive start index.
    stop : int
        Exclusive stop index.
    step : int
        Step size.

    Returns
    -------
    IndexedSlice
    """
    n = encoding.sheaf.total_length
    a = max(0, start)
    b = min(n, stop)
    coordinate = f"{encoding.sheaf.coordinate}[{a}:{b}:{step}]"
    elements = tuple(encoding.root_section.slice_.elements[a:b:step])
    jmt = _make_seq_judgment(
        coordinate=coordinate,
        phi=f"slice_{a}_{b}_{step}",
        carrier="indexed_slice",
        evidence=[f"parent:{encoding.encoding_id[:12]}"],
        obligations=[],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"start": a, "stop": b, "step": step},
    )
    return IndexedSlice(
        slice_id=_stable_id("slice:explicit", coordinate),
        start=a, stop=b, step=step,
        elements=elements,
        smt_array_expr=f"(slice {encoding.sheaf.coordinate} {a} {b} {step})",
        judgment=jmt,
        status=SliceStatus.CONSISTENT,
    )


def sequence_restriction(
    section: SequenceSection,
    a: int,
    b: int,
) -> SequenceSection:
    """Restrict a SequenceSection to the sub-interval [a, b).

    Parameters
    ----------
    section : SequenceSection
        The section to restrict.
    a : int
        New inclusive start.
    b : int
        New exclusive stop.

    Returns
    -------
    SequenceSection
    """
    new_slice = section.slice_.restrict_to(a, b)
    new_coord = f"{section.coordinate}[{a}:{b}]"
    jmt = _make_seq_judgment(
        coordinate=new_coord,
        phi=f"restriction_{a}_{b}",
        carrier="sequence_restriction",
        evidence=[f"parent:{section.section_id[:12]}"],
        obligations=[],
        trust=section.judgment.T,
        provenance={"parent": section.section_id, "a": a, "b": b},
    )
    return SequenceSection(
        section_id=_stable_id("section:restriction", new_coord),
        coordinate=new_coord,
        slice_=new_slice,
        restriction_kind=RestrictionKind.SUBRANGE,
        predecessor_section_id=section.section_id,
        judgment=jmt,
    )


# ---------------------------------------------------------------------------
# SequenceCover
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceCover:
    """An open cover of a sequence by indexed slices, used for sheaf descent.

    A SequenceCover partitions (or covers with overlaps) a sequence into a
    collection of IndexedSlice patches.  It is the input to the sheaf gluing
    problem: given compatible local sections on each patch, do they glue to a
    global section?

    Fields
    ------
    cover_id    : unique identifier for this cover
    coordinate  : symbolic name for the sequence being covered
    patches     : tuple of IndexedSlice objects forming the cover
    total_length: total length of the sequence
    trust       : TrustTier of this cover
    """

    cover_id: str
    coordinate: str
    patches: tuple  # tuple[IndexedSlice, ...]
    total_length: int
    trust: TrustTier

    def patch_count(self) -> int:
        """Return the number of patches in this cover."""
        return len(self.patches)

    def is_exhaustive(self) -> bool:
        """Return True iff the patches together cover the full index range [0, total_length)."""
        covered: set = set()
        for p in self.patches:
            covered.update(range(p.start, p.stop, p.step))
        return all(i in covered for i in range(self.total_length))

    def overlapping_pairs(self) -> list:
        """Return pairs of patches that have overlapping index ranges."""
        pairs = []
        ps = list(self.patches)
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                a, b = ps[i], ps[j]
                lo = max(a.start, b.start)
                hi = min(a.stop, b.stop)
                if lo < hi:
                    pairs.append((a, b))
        return pairs

    def are_patches_compatible(self) -> bool:
        """Return True iff all overlapping patch pairs agree on their overlapping elements."""
        for a, b in self.overlapping_pairs():
            lo = max(a.start, b.start)
            hi = min(a.stop, b.stop)
            for idx in range(lo, hi):
                ea = a.elements[idx - a.start] if idx - a.start < len(a.elements) else None
                eb = b.elements[idx - b.start] if idx - b.start < len(b.elements) else None
                if ea != eb:
                    return False
        return True

    def to_judgment_tuple(self) -> tuple:
        """Return an 8-tuple (c, φ, A, E, O, B, T, Π) representing this cover."""
        return (
            self.cover_id,
            f"sequence_cover({self.coordinate})",
            "SequenceCover",
            f"patches:{self.patch_count()}",
            "" if self.is_exhaustive() else "NON_EXHAUSTIVE",
            f"compatible:{self.are_patches_compatible()}",
            self.trust.name,
            "sequence_cover",
        )


def build_sequence_cover(
    seq: list,
    coordinate: str,
    *,
    chunk_size: int = 8,
    tier: TrustTier | None = None,
) -> SequenceCover:
    """Build a SequenceCover by partitioning a sequence into fixed-size chunks.

    Each chunk becomes an IndexedSlice patch.  Chunks may overlap by one element
    at the boundaries to ensure the gluing condition can be checked.

    Parameters
    ----------
    seq         : the Python sequence to cover
    coordinate  : symbolic name for the sequence
    chunk_size  : size of each patch (default 8)
    tier        : TrustTier; defaults to RUNTIME_WITNESSED

    Returns
    -------
    SequenceCover whose patches partition [0, len(seq))
    """
    if tier is None:
        tier = TrustTier.RUNTIME_WITNESSED
    n = len(seq)
    patches: list = []
    for start in range(0, max(n, 1), max(chunk_size, 1)):
        stop = min(start + chunk_size, n)
        elems = tuple(seq[start:stop])
        coord = f"{coordinate}[{start}:{stop}]"
        jmt = _make_seq_judgment(
            coordinate=coord,
            phi=f"cover_patch_{start}_{stop}",
            carrier="indexed_slice",
            evidence=[f"parent:{coordinate}"],
            obligations=[],
            trust=tier,
            provenance={"patch_start": start, "patch_stop": stop},
        )
        patches.append(IndexedSlice(
            slice_id=_stable_id("cover_patch", coord),
            start=start,
            stop=stop,
            step=1,
            elements=elems,
            smt_array_expr=f"(cover-patch {coordinate} {start} {stop})",
            judgment=jmt,
            status=SliceStatus.CONSISTENT,
        ))
        if stop >= n:
            break
    return SequenceCover(
        cover_id=_stable_id("cover", coordinate),
        coordinate=coordinate,
        patches=tuple(patches),
        total_length=n,
        trust=tier,
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "IndexedSlice",
    "PresheafStatus",
    "RestrictionKind",
    "SequenceCechObstruction",
    "SequenceCover",
    "SequenceDescentObstruction",
    "SequenceGlobalSection",
    "SequenceJudgment",
    "SequenceKind",
    "SequenceSection",
    "SequenceSheaf",
    "SliceStatus",
    "StructuredSequenceEncoding",
    "TrustTier",
    "build_sequence_cover",
    "encode_sequence_as_sheaf",
    "sequence_restriction",
    "slice_sequence",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== structured_data_should_not_be_flat — smoke test ===")

    # Encode a list
    lst = list(range(20))
    enc = encode_sequence_as_sheaf(lst, coordinate="test.seq", chunk_size=4)
    print(f"StructuredSequenceEncoding: id={enc.encoding_id[:12]}… "
          f"sections={len(enc.sheaf.sections)} "
          f"descent={'OK' if enc.is_globally_consistent() else 'FAIL'}")
    assert enc.is_globally_consistent(), "List sequence descent should succeed"

    # Slice
    sl = slice_sequence(enc, 3, 10)
    print(f"IndexedSlice [3:10]: length={sl.length} elements={sl.elements}")
    assert sl.length == 7
    assert sl.elements == tuple(range(3, 10))

    # Restriction
    sec = enc.root_section
    restricted = sequence_restriction(sec, 5, 12)
    print(f"Restriction [5:12]: length={restricted.slice_.length}")
    assert restricted.slice_.elements == tuple(range(5, 12))

    # String encoding
    enc_str = encode_sequence_as_sheaf("hello world", coordinate="test.str")
    print(f"String encoding: length={enc_str.sheaf.total_length} "
          f"descent={'OK' if enc_str.is_globally_consistent() else 'FAIL'}")
    assert enc_str.is_globally_consistent()

    # Overlap check — consistent sections
    slice_a = IndexedSlice(
        slice_id="a", start=0, stop=6, step=1,
        elements=(0, 1, 2, 3, 4, 5),
        smt_array_expr="(seq_a)",
        judgment=_make_seq_judgment("test", "test", "test", [], [], TrustTier.PROPOSAL),
    )
    slice_b = IndexedSlice(
        slice_id="b", start=4, stop=10, step=1,
        elements=(4, 5, 6, 7, 8, 9),
        smt_array_expr="(seq_b)",
        judgment=_make_seq_judgment("test", "test", "test", [], [], TrustTier.PROPOSAL),
    )
    assert slice_a.is_compatible_with(slice_b), "Slices should be compatible on [4,6)"
    print("Overlap compatibility: OK")

    # Inconsistent slices
    slice_c = IndexedSlice(
        slice_id="c", start=4, stop=10, step=1,
        elements=(99, 5, 6, 7, 8, 9),  # disagreement at index 4
        smt_array_expr="(seq_c)",
        judgment=_make_seq_judgment("test", "test", "test", [], [], TrustTier.PROPOSAL),
    )
    assert not slice_a.is_compatible_with(slice_c), "Slices should be incompatible"
    print("Inconsistency detection: OK")

    # Trust algebra
    t = TrustTier.RUNTIME_WITNESSED
    assert t.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    assert t.meet(TrustTier.PROPOSAL) == TrustTier.PROPOSAL
    print("TrustTier algebra: OK")

    # JSON
    d = enc.to_dict()
    j = json.dumps(d, default=str)
    assert "encoding_id" in j
    print("JSON serialization: OK")

    print("All assertions passed.")
    sys.exit(0)
