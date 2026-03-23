"""Sliding window views as local sections with overlap conditions.

# copilot: sequence-mutation-encodings module 02 — sequence windows
# theory2.tex §28.5–§28.9: sliding windows are local sections of the
# sequence presheaf on overlapping open sets.  Overlap conditions ensure
# that adjacent windows agree on their shared elements.

A **sliding window** is a contiguous view of width ``w`` at position ``p`` in
a sequence.  In the presheaf model, each window is a local section over the
open interval ``[p, p+w)``.  The *overlap condition* requires that adjacent
windows of the same cover agree on their shared sub-interval ``[p+1, p+w)``.

This module implements:

* :class:`SequenceWindow` — a fixed-width view with its local section.
* :class:`WindowSection` — the local section over a window's interval.
* :class:`WindowOverlapCondition` — a constraint that adjacent windows
  agree on their overlap.
* :class:`SlidingCover` — a collection of windows covering a sequence
  with their associated descent obligations.

Public functions
----------------
:func:`build_window_cover`
    Build a SlidingCover from a sequence encoding.
:func:`check_window_overlap`
    Verify that adjacent windows satisfy the overlap condition.
:func:`glue_window_sections`
    Attempt to glue all window sections into a global section.

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
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

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

class WindowStatus(str, Enum):
    """Verification status of a single window."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    OVERLAP_PENDING = "overlap_pending"
    VERIFIED = "verified"


class OverlapConditionStatus(str, Enum):
    """Status of an overlap condition between adjacent windows."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNDETERMINED = "undetermined"


class CoverStatus(str, Enum):
    """Status of the sliding cover."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    INCONSISTENT = "inconsistent"


class WindowKind(str, Enum):
    """The semantics of the window."""

    CONTIGUOUS = "contiguous"   # standard sliding window
    STRIDED = "strided"         # windows at regular intervals > 1
    EXPANDING = "expanding"     # window grows as it slides
    SHRINKING = "shrinking"     # window shrinks near the boundary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Čech obstruction for window gluing
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WindowCechObstruction:
    """A Čech H¹ obstruction blocking window section gluing."""

    coordinate: str
    cocycle_description: str
    window_pair: tuple[str, str]
    overlap_range: tuple[int, int]
    trust_tier: TrustTier = TrustTier.PROPOSAL
    is_coboundary: bool = False
    repair_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "window_pair": list(self.window_pair),
            "overlap_range": list(self.overlap_range),
            "trust_tier": self.trust_tier.name,
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
        }


# ---------------------------------------------------------------------------
# Judgment tuple — (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WindowJudgment:
    """A judgment about a window section.  NEVER a boolean."""

    c: str
    phi: str
    A: str
    E: tuple[str, ...]
    O: tuple[str, ...]
    B: tuple[WindowCechObstruction, ...]
    T: TrustTier
    Pi: Mapping[str, Any]

    @property
    def is_settled(self) -> bool:
        return len(self.O) == 0 and len(self.B) == 0

    @property
    def is_obstructed(self) -> bool:
        return any(not ob.is_coboundary for ob in self.B)

    def with_obligation(self, ob: str) -> WindowJudgment:
        from dataclasses import replace
        return replace(self, O=(*self.O, ob))

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c, "phi": self.phi, "A": self.A,
            "E": list(self.E), "O": list(self.O),
            "B": [ob.to_dict() for ob in self.B],
            "T": self.T.name, "Pi": dict(self.Pi),
        }


def _make_win_judgment(
    coordinate: str, phi: str, carrier: str,
    evidence: Sequence[str], obligations: Sequence[str],
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Mapping[str, Any] | None = None,
) -> WindowJudgment:
    return WindowJudgment(
        c=coordinate, phi=phi, A=carrier,
        E=tuple(evidence), O=tuple(obligations), B=(),
        T=trust, Pi=dict(provenance or {}),
    )


# ---------------------------------------------------------------------------
# Window section
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WindowSection:
    """A local section of the sequence presheaf over a window interval.

    Attributes
    ----------
    section_id : str
        Unique identifier.
    window_start : int
        Start index (inclusive).
    window_stop : int
        Stop index (exclusive).
    elements : tuple[Any, ...]
        Element values in this window.
    smt_expr : str
        SMT-LIB2 expression for this window.
    judgment : WindowJudgment
        Governing judgment.
    status : WindowStatus
        Verification status.
    """

    section_id: str
    window_start: int
    window_stop: int
    elements: tuple[Any, ...]
    smt_expr: str
    judgment: WindowJudgment
    status: WindowStatus = WindowStatus.CONSISTENT

    @property
    def width(self) -> int:
        return self.window_stop - self.window_start

    def overlap_with(self, other: WindowSection) -> tuple[int, int] | None:
        """Return overlap interval or None."""
        lo = max(self.window_start, other.window_start)
        hi = min(self.window_stop, other.window_stop)
        return (lo, hi) if lo < hi else None

    def is_compatible_with(self, other: WindowSection) -> bool:
        """Check element compatibility on overlapping range."""
        ovl = self.overlap_with(other)
        if ovl is None:
            return True
        lo, hi = ovl
        for i in range(lo, hi):
            ai = i - self.window_start
            bi = i - other.window_start
            if (ai < len(self.elements) and bi < len(other.elements)
                    and self.elements[ai] != other.elements[bi]):
                return False
        return True

    def restrict_to(self, a: int, b: int) -> WindowSection:
        """Restrict this section to [a, b)."""
        lo, hi = max(self.window_start, a), min(self.window_stop, b)
        if lo >= hi:
            new_elems: tuple[Any, ...] = ()
        else:
            offset = lo - self.window_start
            length = hi - lo
            new_elems = self.elements[offset: offset + length]
        jmt = _make_win_judgment(
            coordinate=f"{self.judgment.c}[{lo}:{hi}]",
            phi=f"window_restriction_{lo}_{hi}",
            carrier="window_section",
            evidence=[f"parent:{self.section_id[:12]}"],
            obligations=[],
            trust=self.judgment.T,
            provenance={"parent": self.section_id},
        )
        return WindowSection(
            section_id=_stable_id("win_sec:restriction", f"{self.section_id}:{a}:{b}"),
            window_start=lo, window_stop=hi,
            elements=new_elems,
            smt_expr=f"(window-restrict {self.smt_expr} {lo} {hi})",
            judgment=jmt,
            status=self.status,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "window_start": self.window_start,
            "window_stop": self.window_stop,
            "width": self.width,
            "status": self.status.value,
            "smt_expr": self.smt_expr,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Sequence window
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SequenceWindow:
    """A fixed-width view into a sequence with its local section.

    Attributes
    ----------
    window_id : str
        Unique identifier.
    position : int
        Start position of the window.
    width : int
        Number of elements in the window.
    kind : WindowKind
        The window semantics.
    section : WindowSection
        The local section for this window.
    judgment : WindowJudgment
        Governing judgment.
    stride : int
        Step between window positions (for STRIDED kind).
    """

    window_id: str
    position: int
    width: int
    kind: WindowKind
    section: WindowSection
    judgment: WindowJudgment
    stride: int = 1

    @property
    def stop(self) -> int:
        return self.position + self.width

    def overlaps_with(self, other: SequenceWindow) -> bool:
        return max(self.position, other.position) < min(self.stop, other.stop)

    def overlap_size(self, other: SequenceWindow) -> int:
        return max(0, min(self.stop, other.stop) - max(self.position, other.position))

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "position": self.position,
            "width": self.width,
            "stop": self.stop,
            "kind": self.kind.value,
            "stride": self.stride,
            "section": self.section.to_dict(),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Window overlap condition
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WindowOverlapCondition:
    """A constraint that adjacent windows agree on their shared elements.

    An overlap condition records the two windows being compared, the
    shared interval, and whether they are currently consistent.

    Attributes
    ----------
    condition_id : str
        Unique identifier.
    left_window_id : str
        The left (earlier) window.
    right_window_id : str
        The right (later) window.
    overlap_start : int
        Inclusive start of the overlap interval.
    overlap_stop : int
        Exclusive stop of the overlap interval.
    status : OverlapConditionStatus
        Current status.
    smt_expression : str
        SMT-LIB2 expression asserting consistency on the overlap.
    judgment : WindowJudgment
        Governing judgment.
    """

    condition_id: str
    left_window_id: str
    right_window_id: str
    overlap_start: int
    overlap_stop: int
    status: OverlapConditionStatus
    smt_expression: str
    judgment: WindowJudgment

    @property
    def overlap_size(self) -> int:
        return max(0, self.overlap_stop - self.overlap_start)

    def is_satisfied(self) -> bool:
        return self.status == OverlapConditionStatus.SATISFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "left_window_id": self.left_window_id,
            "right_window_id": self.right_window_id,
            "overlap_start": self.overlap_start,
            "overlap_stop": self.overlap_stop,
            "overlap_size": self.overlap_size,
            "status": self.status.value,
            "smt_expression": self.smt_expression,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Global section and descent obstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WindowGlobalSection:
    """A globally consistent reconstruction from all window sections."""

    coordinate: str
    total_elements: tuple[Any, ...]
    length: int
    num_windows: int
    judgment: WindowJudgment

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "window_global_section",
            "coordinate": self.coordinate,
            "length": self.length,
            "num_windows": self.num_windows,
            "judgment": self.judgment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class WindowDescentObstruction:
    """A Čech obstruction blocking window section gluing.  NEVER raises."""

    coordinate: str
    obstruction: WindowCechObstruction
    violated_conditions: tuple[str, ...]
    diagnosis: str = ""
    repair_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "window_descent_obstruction",
            "coordinate": self.coordinate,
            "obstruction": self.obstruction.to_dict(),
            "violated_conditions": list(self.violated_conditions),
            "diagnosis": self.diagnosis,
        }


# ---------------------------------------------------------------------------
# Sliding cover
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SlidingCover:
    """A collection of windows covering a sequence with descent obligations.

    Attributes
    ----------
    cover_id : str
        Unique identifier.
    coordinate : str
        Semantic coordinate.
    windows : tuple[SequenceWindow, ...]
        All windows in the cover.
    overlap_conditions : tuple[WindowOverlapCondition, ...]
        Overlap conditions between adjacent windows.
    total_length : int
        Length of the covered sequence.
    window_width : int
        Width of each window.
    stride : int
        Stride between consecutive windows.
    status : CoverStatus
        Current status of the cover.
    judgment : WindowJudgment
        Top-level judgment.
    """

    cover_id: str
    coordinate: str
    windows: tuple[SequenceWindow, ...]
    overlap_conditions: tuple[WindowOverlapCondition, ...]
    total_length: int
    window_width: int
    stride: int
    status: CoverStatus
    judgment: WindowJudgment

    def is_exhaustive(self) -> bool:
        """True iff every position in [0, total_length) is covered by some window."""
        covered: set[int] = set()
        for w in self.windows:
            for i in range(w.position, w.stop):
                covered.add(i)
        return all(i in covered for i in range(self.total_length))

    def violated_conditions(self) -> tuple[WindowOverlapCondition, ...]:
        return tuple(c for c in self.overlap_conditions if not c.is_satisfied())

    def attempt_descent(self) -> WindowGlobalSection | WindowDescentObstruction:
        """Glue window sections into a global section.  NEVER raises."""
        t0 = time.monotonic_ns()
        violated = self.violated_conditions()
        if violated:
            vc = violated[0]
            obs = WindowCechObstruction(
                coordinate=self.coordinate,
                cocycle_description=(
                    f"Overlap condition violated between windows "
                    f"{vc.left_window_id[:12]} and {vc.right_window_id[:12]}"
                ),
                window_pair=(vc.left_window_id, vc.right_window_id),
                overlap_range=(vc.overlap_start, vc.overlap_stop),
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion="Reconcile overlapping window sections.",
            )
            return WindowDescentObstruction(
                coordinate=self.coordinate,
                obstruction=obs,
                violated_conditions=tuple(c.condition_id for c in violated[:10]),
                diagnosis=f"{len(violated)} overlap conditions violated.",
                repair_hints=("reconcile-window-overlaps",),
            )
        total: list[Any] = [None] * self.total_length
        for w in self.windows:
            for i, val in enumerate(w.section.elements):
                pos = w.position + i
                if 0 <= pos < self.total_length:
                    total[pos] = val
        jmt = _make_win_judgment(
            coordinate=self.coordinate,
            phi="window_cover_globally_consistent",
            carrier="sliding_cover",
            evidence=[f"cover:{self.cover_id}"],
            obligations=[],
            trust=TrustTier.VERIFIED,
            provenance={"cover_id": self.cover_id, "descent_at": _now_iso()},
        )
        return WindowGlobalSection(
            coordinate=self.coordinate,
            total_elements=tuple(total),
            length=self.total_length,
            num_windows=len(self.windows),
            judgment=jmt,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cover_id": self.cover_id,
            "coordinate": self.coordinate,
            "num_windows": len(self.windows),
            "num_overlap_conditions": len(self.overlap_conditions),
            "total_length": self.total_length,
            "window_width": self.window_width,
            "stride": self.stride,
            "status": self.status.value,
            "is_exhaustive": self.is_exhaustive(),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def build_window_cover(
    sequence: Sequence[Any],
    *,
    coordinate: str = "seq_cover",
    window_width: int = 4,
    stride: int = 1,
    kind: WindowKind = WindowKind.CONTIGUOUS,
) -> SlidingCover:
    """Build a SlidingCover from a sequence.

    Parameters
    ----------
    sequence : Sequence[Any]
        The sequence to cover.
    coordinate : str
        Semantic coordinate.
    window_width : int
        Width of each window.
    stride : int
        Step between consecutive window start positions.
    kind : WindowKind
        Window semantics.

    Returns
    -------
    SlidingCover
    """
    n = len(sequence)
    logger.debug("build_window_cover: n=%d w=%d s=%d coord=%s", n, window_width, stride, coordinate)
    windows: list[SequenceWindow] = []
    for pos in range(0, max(1, n - window_width + 1), stride):
        stop = min(pos + window_width, n)
        elems = tuple(sequence[pos:stop])
        sec_jmt = _make_win_judgment(
            coordinate=f"{coordinate}[{pos}:{stop}]",
            phi=f"window_section_{pos}_{stop}",
            carrier="window_section",
            evidence=[f"window:[{pos},{stop})"],
            obligations=[],
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance={"pos": pos, "stop": stop},
        )
        section = WindowSection(
            section_id=_stable_id("win_sec", f"{coordinate}:{pos}:{stop}"),
            window_start=pos, window_stop=stop,
            elements=elems,
            smt_expr=f"(window {coordinate} {pos} {stop})",
            judgment=sec_jmt,
            status=WindowStatus.CONSISTENT,
        )
        win_jmt = _make_win_judgment(
            coordinate=f"{coordinate}[{pos}:{stop}]",
            phi=f"window_{pos}_{stop}_sound",
            carrier="sequence_window",
            evidence=[f"window:[{pos},{stop})"],
            obligations=[],
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance={"pos": pos, "stop": stop, "width": stop - pos},
        )
        windows.append(SequenceWindow(
            window_id=_stable_id("window", f"{coordinate}:{pos}"),
            position=pos, width=stop - pos,
            kind=kind, section=section,
            judgment=win_jmt, stride=stride,
        ))

    # Build overlap conditions between adjacent windows
    conditions: list[WindowOverlapCondition] = []
    for i in range(len(windows) - 1):
        w1, w2 = windows[i], windows[i + 1]
        ovl = w1.section.overlap_with(w2.section)
        if ovl:
            lo, hi = ovl
            compatible = w1.section.is_compatible_with(w2.section)
            status = (
                OverlapConditionStatus.SATISFIED
                if compatible
                else OverlapConditionStatus.VIOLATED
            )
            cond_jmt = _make_win_judgment(
                coordinate=f"{coordinate}.overlap[{lo}:{hi}]",
                phi=f"overlap_condition_{lo}_{hi}",
                carrier="overlap_condition",
                evidence=[f"overlap:[{lo},{hi})"],
                obligations=[] if compatible else [f"reconcile_overlap_{lo}_{hi}"],
                trust=TrustTier.RUNTIME_WITNESSED if compatible else TrustTier.PROPOSAL,
                provenance={"lo": lo, "hi": hi, "compatible": compatible},
            )
            conditions.append(WindowOverlapCondition(
                condition_id=_stable_id("overlap", f"{coordinate}:{w1.window_id}:{w2.window_id}"),
                left_window_id=w1.window_id,
                right_window_id=w2.window_id,
                overlap_start=lo, overlap_stop=hi,
                status=status,
                smt_expression=(
                    f"(assert (forall ((i Int)) (=> (and (>= i {lo}) (< i {hi})) "
                    f"(= (seq-ref {w1.window_id} i) (seq-ref {w2.window_id} i)))))"
                ),
                judgment=cond_jmt,
            ))

    cover_ok = all(c.is_satisfied() for c in conditions)
    cover_status = CoverStatus.COMPLETE if cover_ok else CoverStatus.INCONSISTENT
    top_jmt = _make_win_judgment(
        coordinate=coordinate,
        phi="sliding_cover_built",
        carrier="sliding_cover",
        evidence=[f"windows:{len(windows)}", f"conditions:{len(conditions)}"],
        obligations=[] if cover_ok else ["reconcile-overlap-violations"],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"coordinate": coordinate, "n": n, "w": window_width, "s": stride},
    )
    return SlidingCover(
        cover_id=_stable_id("cover", coordinate),
        coordinate=coordinate,
        windows=tuple(windows),
        overlap_conditions=tuple(conditions),
        total_length=n,
        window_width=window_width,
        stride=stride,
        status=cover_status,
        judgment=top_jmt,
    )


def check_window_overlap(
    window_a: SequenceWindow,
    window_b: SequenceWindow,
    *,
    coordinate: str = "overlap_check",
) -> WindowOverlapCondition:
    """Verify that two windows satisfy the overlap condition.

    Parameters
    ----------
    window_a : SequenceWindow
    window_b : SequenceWindow
    coordinate : str

    Returns
    -------
    WindowOverlapCondition
    """
    ovl = window_a.section.overlap_with(window_b.section)
    lo, hi = ovl if ovl else (0, 0)
    compatible = window_a.section.is_compatible_with(window_b.section)
    status = (
        OverlapConditionStatus.SATISFIED
        if compatible
        else OverlapConditionStatus.VIOLATED
    )
    jmt = _make_win_judgment(
        coordinate=f"{coordinate}.overlap[{lo}:{hi}]",
        phi=f"overlap_check_{lo}_{hi}",
        carrier="overlap_condition",
        evidence=[f"windows:{window_a.window_id[:12]}:{window_b.window_id[:12]}"],
        obligations=[] if compatible else [f"reconcile_overlap_{lo}_{hi}"],
        trust=TrustTier.RUNTIME_WITNESSED if compatible else TrustTier.PROPOSAL,
    )
    return WindowOverlapCondition(
        condition_id=_stable_id("overlap", f"{coordinate}:{window_a.window_id}:{window_b.window_id}"),
        left_window_id=window_a.window_id,
        right_window_id=window_b.window_id,
        overlap_start=lo, overlap_stop=hi,
        status=status,
        smt_expression=(
            f"(window-overlap-condition {window_a.window_id} {window_b.window_id} {lo} {hi})"
        ),
        judgment=jmt,
    )


def glue_window_sections(
    cover: SlidingCover,
) -> WindowGlobalSection | WindowDescentObstruction:
    """Attempt to glue all window sections into a global section.

    Delegates to SlidingCover.attempt_descent().  NEVER raises.

    Parameters
    ----------
    cover : SlidingCover
        The sliding cover.

    Returns
    -------
    WindowGlobalSection | WindowDescentObstruction
    """
    return cover.attempt_descent()


# ---------------------------------------------------------------------------
# Window cover statistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WindowCoverStats:
    """Aggregate statistics for a collection of sliding covers."""

    total_covers: int
    total_windows: int
    total_overlap_conditions: int
    satisfied_conditions: int
    violated_conditions: int
    exhaustive_covers: int
    descent_successes: int
    descent_failures: int

    @classmethod
    def from_covers(cls, covers: Sequence[SlidingCover]) -> WindowCoverStats:
        total_wins = sum(len(c.windows) for c in covers)
        total_conds = sum(len(c.overlap_conditions) for c in covers)
        satisfied = sum(
            sum(1 for cond in c.overlap_conditions if cond.is_satisfied())
            for c in covers
        )
        violated = total_conds - satisfied
        exhaustive = sum(1 for c in covers if c.is_exhaustive())
        successes = 0
        failures = 0
        for c in covers:
            dr = c.attempt_descent()
            if isinstance(dr, WindowGlobalSection):
                successes += 1
            else:
                failures += 1
        return cls(
            total_covers=len(covers),
            total_windows=total_wins,
            total_overlap_conditions=total_conds,
            satisfied_conditions=satisfied,
            violated_conditions=violated,
            exhaustive_covers=exhaustive,
            descent_successes=successes,
            descent_failures=failures,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_covers": self.total_covers,
            "total_windows": self.total_windows,
            "total_overlap_conditions": self.total_overlap_conditions,
            "satisfied_conditions": self.satisfied_conditions,
            "violated_conditions": self.violated_conditions,
            "exhaustive_covers": self.exhaustive_covers,
            "descent_successes": self.descent_successes,
            "descent_failures": self.descent_failures,
        }


# ---------------------------------------------------------------------------
# WindowGluing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowGluing:
    """The result of attempting to glue a collection of window sections.

    A WindowGluing records the outcome of the sheaf descent problem over a
    SlidingCover: given local sections on each window, does a global section
    exist?  The gluing succeeds (global_section is set) or fails (obstruction
    is set), but never raises.

    Fields
    ------
    gluing_id       : unique identifier for this gluing attempt
    cover_id        : the SlidingCover that was glued
    succeeded       : True iff the global section was constructed
    global_section  : the WindowGlobalSection if succeeded, else None
    obstruction     : the WindowDescentObstruction if not succeeded, else None
    trust           : TrustTier of this gluing result
    """

    gluing_id: str
    cover_id: str
    succeeded: bool
    global_section: object  # WindowGlobalSection | None
    obstruction: object     # WindowDescentObstruction | None
    trust: TrustTier

    def is_clean(self) -> bool:
        """Return True iff gluing succeeded with no obstruction."""
        return self.succeeded and self.obstruction is None

    def to_judgment_tuple(self) -> tuple:
        """Return an 8-tuple (c, φ, A, E, O, B, T, Π) for this gluing."""
        return (
            self.gluing_id,
            f"window_gluing({'succeeded' if self.succeeded else 'failed'})",
            "WindowGluing",
            f"cover:{self.cover_id}",
            "" if self.succeeded else "GLUING_OBSTRUCTION",
            f"obstruction:{self.obstruction is not None}",
            self.trust.name,
            "window_gluing",
        )

    def unwrap_section(self) -> object:
        """Return the global section or raise ValueError if gluing failed."""
        if not self.succeeded or self.global_section is None:
            raise ValueError(
                f"WindowGluing {self.gluing_id!r} failed: "
                f"obstruction={self.obstruction}"
            )
        return self.global_section

    def describe(self) -> str:
        """Return a human-readable description of this gluing result."""
        status = "✓ succeeded" if self.succeeded else "✗ failed"
        return f"WindowGluing[{self.gluing_id}] {status} (cover={self.cover_id})"


def slide_window(
    seq: list,
    coordinate: str,
    *,
    window_width: int = 5,
    stride: int = 1,
    tier: TrustTier | None = None,
) -> WindowGluing:
    """Build a sliding window cover and attempt to glue it into a global section.

    This is the primary high-level entry point for the sequence-window encoding.
    It builds a SlidingCover with the given parameters, checks overlap conditions,
    and returns a WindowGluing describing the outcome.

    Parameters
    ----------
    seq          : the Python sequence to cover
    coordinate   : symbolic name for the sequence
    window_width : width of each window (default 5)
    stride       : step between window starts (default 1)
    tier         : TrustTier; defaults to RUNTIME_WITNESSED

    Returns
    -------
    WindowGluing with succeeded=True if all overlaps are compatible.
    """
    if tier is None:
        tier = TrustTier.RUNTIME_WITNESSED

    cover = build_window_cover(
        seq, coordinate=coordinate,
        window_width=window_width, stride=stride,
    )
    descent = cover.attempt_descent()
    succeeded = isinstance(descent, WindowGlobalSection)
    gluing_id = f"gluing_{cover.cover_id[:16]}"
    return WindowGluing(
        gluing_id=gluing_id,
        cover_id=cover.cover_id,
        succeeded=succeeded,
        global_section=descent if succeeded else None,
        obstruction=None if succeeded else descent,
        trust=tier,
    )


__all__ = [
    "CoverStatus",
    "OverlapConditionStatus",
    "SlidingCover",
    "SequenceWindow",
    "TrustTier",
    "WindowCechObstruction",
    "WindowCoverStats",
    "WindowDescentObstruction",
    "WindowGluing",
    "WindowGlobalSection",
    "WindowJudgment",
    "WindowKind",
    "WindowOverlapCondition",
    "WindowSection",
    "WindowStatus",
    "build_window_cover",
    "check_window_overlap",
    "glue_window_sections",
    "slide_window",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== sequence_windows — smoke test ===")

    seq = list(range(20))

    # Build a cover with stride 2, width 5
    cover = build_window_cover(seq, coordinate="test.win", window_width=5, stride=2)
    print(f"SlidingCover: windows={len(cover.windows)} "
          f"conditions={len(cover.overlap_conditions)} "
          f"exhaustive={cover.is_exhaustive()} "
          f"status={cover.status.value}")
    assert cover.is_exhaustive(), "Cover should be exhaustive"
    assert cover.status == CoverStatus.COMPLETE, "All overlaps should be satisfied"

    # Descent
    result = glue_window_sections(cover)
    print(f"Descent: {type(result).__name__}")
    assert isinstance(result, WindowGlobalSection), "Should reconstruct global section"
    assert result.total_elements == tuple(seq), "Reconstructed sequence should match"

    # Single overlap check
    w1, w2 = cover.windows[0], cover.windows[1]
    cond = check_window_overlap(w1, w2, coordinate="test.overlap")
    print(f"Overlap condition: status={cond.status.value} size={cond.overlap_size}")
    assert cond.is_satisfied(), "Adjacent windows in correct sequence should be compatible"

    # Inconsistent cover
    seq_bad = list(range(10))
    cover_bad = build_window_cover(seq_bad, coordinate="test.bad", window_width=5, stride=2)
    # Artificially introduce an inconsistency
    w_bad_a, w_bad_b = cover_bad.windows[0], cover_bad.windows[1]
    # Create inconsistent sections
    sec_bad = WindowSection(
        section_id="bad_sec",
        window_start=w_bad_a.position,
        window_stop=w_bad_a.stop,
        elements=tuple(999 + i for i in range(w_bad_a.width)),  # wrong values
        smt_expr="(bad_window)",
        judgment=w_bad_a.section.judgment,
    )
    cond_bad = check_window_overlap(
        SequenceWindow(
            window_id=w_bad_a.window_id,
            position=w_bad_a.position, width=w_bad_a.width,
            kind=w_bad_a.kind, section=sec_bad,
            judgment=w_bad_a.judgment,
        ),
        w_bad_b,
        coordinate="test.bad_overlap",
    )
    print(f"Bad overlap: status={cond_bad.status.value}")
    assert not cond_bad.is_satisfied(), "Bad window should produce violated condition"

    # Statistics
    stats = WindowCoverStats.from_covers([cover])
    print(f"Stats: {stats.to_dict()}")
    assert stats.total_covers == 1
    assert stats.descent_successes == 1

    # Trust algebra
    t = TrustTier.VERIFIED
    assert t.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    assert t.meet(TrustTier.PROPOSAL) == TrustTier.PROPOSAL
    print("TrustTier: OK")

    # JSON
    d = cover.to_dict()
    j = json.dumps(d, default=str)
    assert "cover_id" in j
    print("JSON serialization: OK")

    print("All assertions passed.")
    sys.exit(0)
