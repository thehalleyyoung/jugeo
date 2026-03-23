"""Repair as controlled surgery on a partial section (theory2.tex Ch11 §11.3).

Stage 03 of the repair pipeline: given a :class:`SectionReplacement` (or a raw
``(old_section, new_section)`` pair at a coordinate), perform a **surgical
replacement** of exactly that local section within the partial section, then
verify the result satisfies the *descent condition* and the *scope preservation
invariant*.

Theory basis (theory2.tex §11.3 — Repair as Controlled Surgery)
----------------------------------------------------------------
A sheaf-theoretic *section* over an open set U is a consistent assignment of
values to all points of U.  A *partial section* defined on a proper sub-cover
{U_i}_{i∈S ⊊ I} can fail to extend to a global section exactly when the local
data on overlapping patches U_i ∩ U_j is inconsistent — the familiar *cocycle
obstruction* studied in Čech cohomology.

"Repair" in the JuGeo sense is **not** code patching: it is the surgical
replacement of a single local section s_i at a specific coordinate U_i.  The
surgery is controlled by three invariants:

1. **Scope preservation** — the surgery may touch *only* the target coordinate
   U_i.  It must not widen to any U_j with j ≠ i.  Formally: the set of
   coordinates whose section is altered must be ⊆ {U_i}.

2. **Descent condition** — after replacement, for every pair (i, j) such that
   U_i ∩ U_j ≠ ∅, the new sections must satisfy
   s_i|_{U_i ∩ U_j} = s_j|_{U_i ∩ U_j}.  In the discrete approximation used
   here, this means that every coordinate that was previously consistent with
   U_i must still appear in the partial section with compatible data.

3. **Obstruction resolution** — the surgery must change the section at the
   target coordinate (old ≠ new), otherwise the obstruction is not cleared.

A *partial section* is represented as a tuple of ``(coordinate, section_repr)``
pairs.  The "section_repr" is an opaque string (typically source code, a
JSON blob, or a predicate expression).  The surgery produces a new tuple with
exactly one entry changed (or appended if the coordinate was absent).

Implementing classes
--------------------
* :class:`SectionReplacement` — an immutable record of a proposed replacement
  together with its descent-check results.
* :class:`RepairSurgery` — a tracked execution of a replacement, with status,
  validation results, and a rollback token.
* :class:`RepairControlledSurgeryPartialWitness` — the central witness/record
  of a completed surgical repair, carrying both the before/after partial
  sections and all invariant-satisfaction flags.
* :class:`RepairControlledSurgeryPartialAnalyzer` — the core analyzer that
  plans, executes, validates, and can roll back a surgery.
* :class:`RepairControlledSurgeryPartialCoordinator` — orchestrates multiple
  analyzers over the same session, selects the best witness, and produces an
  aggregate report.

Invariants encoded in code
--------------------------
* ``is_minimal`` on :class:`SectionReplacement` ≡ the affected coordinate
  count does not exceed ``scope_limit``.
* ``scope_preserved`` on :class:`RepairControlledSurgeryPartialWitness` ≡ no
  coordinate outside the root prefix was touched.
* ``descent_condition_satisfied`` ≡ all affected coordinates still appear in
  the post-surgery partial section.
* ``obstruction_resolved`` ≡ ``new_section ≠ old_section`` and
  ``new_section ≠ ""``.

References
----------
* theory2.tex Ch11 §11.3 — Repair as Controlled Surgery
* theory2.tex Ch11 §11.2 — Repair as Local Section Replacement (prerequisite)
* Čech cohomology notes, appendix A.4

# copilot: s03 repair as controlled surgery on a partial section — theory2 ch11 §11.3
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.errors import (
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        StructuredFailure,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        JuGeoError,
        raise_with_scope,
    )
except ImportError:
    ObstructionRecord = Any; RepairHint = Any; RepairPriority = Any  # type: ignore
    StructuredFailure = Any; FailureScope = Any; FailureClassification = Any  # type: ignore
    EvidenceFamily = Any; JuGeoError = Exception; raise_with_scope = None  # type: ignore

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Provenance,
        ProvenanceSource,
        TrustLevel,
        TrustAnnotation,
        Obstruction,
    )
except ImportError:
    EvidenceBundle = Any; EvidenceItem = Any; EvidenceItemKind = Any  # type: ignore
    Provenance = Any; ProvenanceSource = Any; TrustLevel = Any  # type: ignore
    TrustAnnotation = Any; Obstruction = Any  # type: ignore

try:
    from jugeo.solver.countermodels import FailureClass, RepairType
except ImportError:
    FailureClass = Any; RepairType = Any  # type: ignore

try:
    from jugeo.problem_modes.repair_semantics.models import (
        CounterexampleRecord,
        DebugSession,
        RepairFrontier,
        RepairPlan,
        RepairValidator,
    )
except ImportError:
    CounterexampleRecord = Any; DebugSession = Any  # type: ignore
    RepairFrontier = Any; RepairPlan = Any; RepairValidator = Any  # type: ignore


# ---------------------------------------------------------------------------
# Module-level provenance constant
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "module": "repair_as_controlled_surgery_on_a",
    "chapter": "theory2 Ch11",
    "section": "§11.3",
    "title": "Repair as controlled surgery on a partial section",
    "stage": "03",
    "pipeline": "repair_semantics",
    "theory_file": "theory2.tex",
    "invariants": "scope_preservation, descent_condition, obstruction_resolution",
    "created": "2025",
}


# ---------------------------------------------------------------------------
# §03.0  Enumerations
# ---------------------------------------------------------------------------


class SurgeryKind(str, Enum):
    """Kind of surgical repair operation.

    Attributes
    ----------
    LOCAL_REPLACEMENT :
        Replace the local section at a single coordinate with a new value.
        This is the canonical surgery described in theory2.tex §11.3.
    SCOPE_CONTRACTION :
        Shrink the repair scope — remove a coordinate from the affected set
        without modifying any section value.  Used when the initial diagnosis
        over-estimated the affected region.
    DESCENT_PATCH :
        Patch the *gluing map* between two overlapping patches to restore the
        descent (cocycle) condition.  The section values themselves do not
        change; only the transition data is updated.
    SECTION_EXTENSION :
        Extend a partial section by *adding* a new coordinate that was
        previously absent from the partial cover.  This is a net-positive
        surgery: ``coverage_delta() > 0``.
    OBSTRUCTION_REMOVAL :
        Directly remove the obstruction record from a coordinate without
        replacing the section value.  Useful when the obstruction was
        spurious (false positive from an earlier analysis phase).
    COHERENCE_REPAIR :
        Repair a *coherence failure* — the section at U_i is internally
        consistent but disagrees with the global consistency predicate.
        Typically involves updating a type annotation or a proof term.
    """

    LOCAL_REPLACEMENT = "LOCAL_REPLACEMENT"
    SCOPE_CONTRACTION = "SCOPE_CONTRACTION"
    DESCENT_PATCH = "DESCENT_PATCH"
    SECTION_EXTENSION = "SECTION_EXTENSION"
    OBSTRUCTION_REMOVAL = "OBSTRUCTION_REMOVAL"
    COHERENCE_REPAIR = "COHERENCE_REPAIR"


class SurgeryStatus(str, Enum):
    """Lifecycle status of a :class:`RepairSurgery`.

    Attributes
    ----------
    PENDING :
        The surgery has been planned but not yet started.
    IN_PROGRESS :
        Execution has begun; partial results may be available.
    COMPLETED :
        Execution finished; the replacement has been applied to the partial
        section.  Validation has *not* yet run.
    VALIDATED :
        Both execution and all validation checks passed.
    FAILED :
        Execution or validation failed; the partial section may be in an
        inconsistent state.  Rollback should be attempted.
    ROLLED_BACK :
        The surgery was rolled back; the partial section is in its pre-surgery
        state.
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    VALIDATED = "VALIDATED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


# ---------------------------------------------------------------------------
# §03.1  Helper functions
# ---------------------------------------------------------------------------


def _iso_timestamp() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp with second precision, e.g. ``"2025-01-15T12:34:56Z"``.

    Notes
    -----
    Uses :func:`time.gmtime` rather than ``datetime`` to avoid importing the
    standard ``datetime`` module in environments where it may be monkey-patched.
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


def _stable_hash8(s: str) -> str:
    """Return a stable 8-character hex digest of a string.

    Parameters
    ----------
    s : str
        The input string to hash.

    Returns
    -------
    str
        An 8-character hexadecimal string derived from the SHA-256 digest of
        the UTF-8 encoding of *s*.  Stable across Python interpreter restarts
        (unlike the built-in ``hash()``).

    Examples
    --------
    >>> _stable_hash8("hello")
    '2cf24dba'
    """
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _section_similarity(s1: str, s2: str) -> float:
    """Compute a Jaccard-like character-level similarity between two strings.

    The similarity is defined as ``|common_chars| / max(len(s1), len(s2))``,
    where *common_chars* is the multiset intersection of the characters of
    *s1* and *s2* (computed by iterating over the shorter string's character
    frequencies).

    Parameters
    ----------
    s1 : str
        First section representation.
    s2 : str
        Second section representation.

    Returns
    -------
    float
        A value in ``[0.0, 1.0]``.  Returns ``1.0`` if both strings are equal
        or both are empty; returns ``0.0`` if one is empty and the other is
        not.

    Notes
    -----
    This is a *heuristic* similarity, not a semantic one.  Its purpose is to
    give a rough signal about how much the replacement changed the section,
    not to detect semantic equivalence.
    """
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    # Build frequency maps
    freq1: dict[str, int] = {}
    for ch in s1:
        freq1[ch] = freq1.get(ch, 0) + 1
    freq2: dict[str, int] = {}
    for ch in s2:
        freq2[ch] = freq2.get(ch, 0) + 1
    common = sum(min(freq1.get(ch, 0), freq2.get(ch, 0)) for ch in freq1)
    return common / max_len


def _compute_surgery_confidence(
    scope_preserved: bool,
    descent_ok: bool,
    obstruction_resolved: bool,
) -> float:
    """Compute an aggregate confidence score for a surgery from its invariant flags.

    The score is a weighted average of three Boolean conditions:

    * scope preservation contributes weight 0.4,
    * descent condition satisfaction contributes weight 0.35,
    * obstruction resolution contributes weight 0.25.

    Parameters
    ----------
    scope_preserved : bool
        Whether the surgery did not widen scope beyond the target coordinate.
    descent_ok : bool
        Whether the descent condition is satisfied after surgery.
    obstruction_resolved : bool
        Whether the surgery actually resolved the obstruction.

    Returns
    -------
    float
        A value in ``[0.0, 1.0]``.
    """
    score = (
        (0.4 if scope_preserved else 0.0)
        + (0.35 if descent_ok else 0.0)
        + (0.25 if obstruction_resolved else 0.0)
    )
    return round(score, 4)


def _default_descent_checks(
    affected: Sequence[str],
) -> tuple[tuple[str, bool], ...]:
    """Return a tuple of ``(coordinate, True)`` pairs for each affected coordinate.

    Used as an optimistic default: all descent checks pass unless overridden
    by explicit verification.

    Parameters
    ----------
    affected : Sequence[str]
        The coordinates to include in the result.

    Returns
    -------
    tuple[tuple[str, bool], ...]
        One ``(coord, True)`` pair per coordinate in *affected*.

    Examples
    --------
    >>> _default_descent_checks(["a.b", "a.c"])
    (('a.b', True), ('a.c', True))
    """
    return tuple((coord, True) for coord in affected)


def _coordinate_depth(coord: str) -> int:
    """Return the depth of a dot-separated coordinate string.

    The depth equals the number of ``.`` characters plus 1.  A root
    coordinate with no dots has depth 1.

    Parameters
    ----------
    coord : str
        A dot-separated coordinate such as ``"root.module.function"``.

    Returns
    -------
    int
        Depth ≥ 1.

    Examples
    --------
    >>> _coordinate_depth("root")
    1
    >>> _coordinate_depth("root.module.function")
    3
    """
    return coord.count(".") + 1


# ---------------------------------------------------------------------------
# §03.2  SectionReplacement dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionReplacement:
    """An immutable record of a proposed section replacement at a coordinate.

    A :class:`SectionReplacement` captures everything needed to perform one
    surgical step: the target coordinate, the old and new section
    representations, the kind of surgery, and the result of all descent checks
    that have been run so far.

    Parameters
    ----------
    replacement_id : str
        A unique identifier for this replacement (UUID or stable hash).
    coordinate : str
        The dot-separated coordinate at which the replacement is proposed,
        e.g. ``"root.module.function"``.
    old_section_repr : str
        A string representation of the section value *before* replacement.
    new_section_repr : str
        A string representation of the section value *after* replacement.
    surgery_kind : str
        The :class:`SurgeryKind` name (string) for the type of surgery.
    affected_coordinates : tuple[str, ...]
        All coordinates that must be checked for descent after the replacement.
        Typically contains ``coordinate`` plus any overlapping-patch neighbours.
    descent_checks : tuple[tuple[str, bool], ...]
        Pairs of ``(coordinate, passed)`` recording whether each affected
        coordinate passed its descent check.
    confidence : float
        A value in ``[0.0, 1.0]`` indicating how confident the planner is
        that this replacement is correct.
    is_minimal : bool
        ``True`` iff the number of affected coordinates does not exceed the
        planner's ``scope_limit`` — i.e. the surgery does not widen scope.
    timestamp : str
        ISO-8601 UTC timestamp at which the replacement was planned.

    Notes
    -----
    This class is frozen (immutable) and slot-based for performance.  All
    mutating operations must return a new instance via
    :func:`dataclasses.replace`.
    """

    replacement_id: str
    coordinate: str
    old_section_repr: str
    new_section_repr: str
    surgery_kind: str
    affected_coordinates: tuple[str, ...]
    descent_checks: tuple[tuple[str, bool], ...]
    confidence: float
    is_minimal: bool
    timestamp: str

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return ``True`` if the replacement passes all quality gates.

        A replacement is valid iff:

        1. ``is_minimal`` is ``True`` (surgery does not widen scope).
        2. ``confidence > 0.7``.
        3. All descent checks have passed (every ``(coord, bool)`` pair has
           ``bool == True``).

        Returns
        -------
        bool
            ``True`` if all three conditions hold, ``False`` otherwise.
        """
        if not self.is_minimal:
            return False
        if self.confidence <= 0.7:
            return False
        return all(passed for _, passed in self.descent_checks)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields represented as JSON-serialisable Python objects.
            Tuples are converted to lists.
        """
        return {
            "replacement_id": self.replacement_id,
            "coordinate": self.coordinate,
            "old_section_repr": self.old_section_repr,
            "new_section_repr": self.new_section_repr,
            "surgery_kind": self.surgery_kind,
            "affected_coordinates": list(self.affected_coordinates),
            "descent_checks": [
                {"coordinate": c, "passed": p} for c, p in self.descent_checks
            ],
            "confidence": self.confidence,
            "is_minimal": self.is_minimal,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of the replacement.

        Returns
        -------
        str
            A string of the form
            ``"[<id>] <coord> <kind>: '<old[:20]>' → '<new[:20]>' (conf=0.95, valid=True)"``.
        """
        old_snip = self.old_section_repr[:20].replace("\n", "↵")
        new_snip = self.new_section_repr[:20].replace("\n", "↵")
        return (
            f"[{self.replacement_id[:8]}] {self.coordinate} {self.surgery_kind}: "
            f"'{old_snip}' → '{new_snip}' "
            f"(conf={self.confidence:.2f}, valid={self.is_valid()})"
        )


# ---------------------------------------------------------------------------
# §03.3  RepairSurgery dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairSurgery:
    """A tracked execution of a :class:`SectionReplacement`.

    :class:`RepairSurgery` wraps a :class:`SectionReplacement` with
    lifecycle metadata: a status field following :class:`SurgeryStatus`,
    named validation results, a rollback token, surgeon identity, and
    start/end timestamps.

    Parameters
    ----------
    surgery_id : str
        Unique identifier for this execution (UUID).
    coordinate : str
        The coordinate being operated on (mirrors ``replacement.coordinate``
        for quick access).
    replacement : SectionReplacement
        The immutable replacement record that describes what to change.
    status : str
        Current :class:`SurgeryStatus` name.  Defaults to ``"PENDING"``.
    validation_results : tuple[tuple[str, bool], ...]
        ``(check_name, passed)`` pairs populated by the validation phase.
        Empty before validation runs.
    rollback_token : str
        A stable hash that identifies the pre-surgery state and can be used
        to locate the original partial section for rollback.
    surgeon_id : str
        Identity of the analyzer instance that performed the surgery (its
        ``analyzer_id``).
    timestamp_start : str
        ISO-8601 UTC timestamp when execution began.
    timestamp_end : str
        ISO-8601 UTC timestamp when execution finished (may be empty string
        if still in progress).

    Notes
    -----
    Like all JuGeo dataclasses, this is frozen and slot-based.  State
    transitions are represented by creating new instances via
    :func:`dataclasses.replace`.
    """

    surgery_id: str
    coordinate: str
    replacement: SectionReplacement
    status: str
    validation_results: tuple[tuple[str, bool], ...]
    rollback_token: str
    surgeon_id: str
    timestamp_start: str
    timestamp_end: str

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def is_successful(self) -> bool:
        """Return ``True`` iff the surgery status is ``COMPLETED``.

        Note that ``COMPLETED`` means *execution* finished, not that
        *validation* passed.  Use :meth:`has_all_validations_passed` for the
        latter.

        Returns
        -------
        bool
        """
        return self.status == SurgeryStatus.COMPLETED.value

    def has_all_validations_passed(self) -> bool:
        """Return ``True`` iff all named validation checks passed.

        Returns ``False`` if no validation results are recorded (empty tuple).

        Returns
        -------
        bool
        """
        if not self.validation_results:
            return False
        return all(passed for _, passed in self.validation_results)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "surgery_id": self.surgery_id,
            "coordinate": self.coordinate,
            "replacement": self.replacement.to_dict(),
            "status": self.status,
            "validation_results": [
                {"check": c, "passed": p} for c, p in self.validation_results
            ],
            "rollback_token": self.rollback_token,
            "surgeon_id": self.surgeon_id,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
        }


# ---------------------------------------------------------------------------
# §03.4  RepairControlledSurgeryPartialWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairControlledSurgeryPartialWitness:
    """A witness/record for a completed surgical repair of a partial section.

    This is the central artifact produced by
    :meth:`RepairControlledSurgeryPartialAnalyzer.execute_surgery`.  It
    captures the full before/after state of the partial section and records
    whether each of the three key invariants was satisfied.

    Parameters
    ----------
    witness_id : str
        Unique identifier (UUID) for this witness record.
    coordinate : str
        The coordinate at which the surgery was performed.
    surgery : RepairSurgery
        The completed surgery execution record.
    partial_section_before : tuple[tuple[str, str], ...]
        The partial section *before* surgery, as ``(coord, section_repr)``
        pairs.
    partial_section_after : tuple[tuple[str, str], ...]
        The partial section *after* surgery.  Typically one entry differs
        from ``partial_section_before``, or a new entry has been appended.
    descent_condition_satisfied : bool
        ``True`` iff for every affected coordinate, the coordinate still
        appears in ``partial_section_after`` with compatible data (descent
        condition per theory2.tex §11.3).
    scope_preserved : bool
        ``True`` iff the surgery did not alter any coordinate outside the
        target coordinate's scope prefix.
    obstruction_resolved : bool
        ``True`` iff the section at the target coordinate was genuinely
        changed (old ≠ new, new ≠ "").
    confidence : float
        Aggregate confidence score in ``[0.0, 1.0]``, computed from the
        three invariant flags via :func:`_compute_surgery_confidence`.
    provenance : tuple[tuple[str, str], ...]
        ``(key, value)`` pairs recording the provenance of this witness
        (analyzer id, session id, theory reference, etc.).
    timestamp : str
        ISO-8601 UTC timestamp of witness creation.

    Notes
    -----
    The three predicates ``descent_condition_satisfied``, ``scope_preserved``,
    and ``surgery.is_successful()`` form the *validity triple* for a repair
    (see :meth:`is_valid_repair`).

    Examples
    --------
    Build a minimal witness and check validity::

        surgery = RepairSurgery(
            surgery_id="s-001",
            coordinate="root.f",
            replacement=repl,
            status="COMPLETED",
            ...
        )
        witness = RepairControlledSurgeryPartialWitness(
            witness_id="w-001",
            coordinate="root.f",
            surgery=surgery,
            ...
            descent_condition_satisfied=True,
            scope_preserved=True,
            obstruction_resolved=True,
            confidence=1.0,
            provenance=(),
            timestamp=_iso_timestamp(),
        )
        assert witness.is_valid_repair()
    """

    witness_id: str
    coordinate: str
    surgery: RepairSurgery
    partial_section_before: tuple[tuple[str, str], ...]
    partial_section_after: tuple[tuple[str, str], ...]
    descent_condition_satisfied: bool
    scope_preserved: bool
    obstruction_resolved: bool
    confidence: float
    provenance: tuple[tuple[str, str], ...]
    timestamp: str

    # ------------------------------------------------------------------
    # §03.4.1  Validity and coverage
    # ------------------------------------------------------------------

    def is_valid_repair(self) -> bool:
        """Return ``True`` iff all three invariants are satisfied.

        Validity requires:

        1. ``descent_condition_satisfied`` — sections agree on overlaps.
        2. ``scope_preserved`` — surgery did not widen scope.
        3. ``surgery.is_successful()`` — execution status is ``COMPLETED``.

        Returns
        -------
        bool
            ``True`` only when all three conditions hold simultaneously.
        """
        return (
            self.descent_condition_satisfied
            and self.scope_preserved
            and self.surgery.is_successful()
        )

    def coverage_delta(self) -> int:
        """Return the change in coverage: (after count) - (before count).

        A positive delta means the surgery *extended* the partial section
        (added a new coordinate).  Zero means an in-place replacement.
        A negative delta indicates coordinates were erroneously removed
        (should not happen under correct surgery logic).

        Returns
        -------
        int
            ``len(partial_section_after) - len(partial_section_before)``.
            Should be ≥ 0 for a well-behaved surgery.
        """
        return len(self.partial_section_after) - len(self.partial_section_before)

    # ------------------------------------------------------------------
    # §03.4.2  Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields serialised.  Nested dataclasses are serialised
            recursively via their own ``to_dict`` methods.
        """
        return {
            "witness_id": self.witness_id,
            "coordinate": self.coordinate,
            "surgery": self.surgery.to_dict(),
            "partial_section_before": [
                {"coord": c, "section": s} for c, s in self.partial_section_before
            ],
            "partial_section_after": [
                {"coord": c, "section": s} for c, s in self.partial_section_after
            ],
            "descent_condition_satisfied": self.descent_condition_satisfied,
            "scope_preserved": self.scope_preserved,
            "obstruction_resolved": self.obstruction_resolved,
            "confidence": self.confidence,
            "provenance": [{"key": k, "value": v} for k, v in self.provenance],
            "timestamp": self.timestamp,
            "is_valid_repair": self.is_valid_repair(),
            "coverage_delta": self.coverage_delta(),
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any]
    ) -> "RepairControlledSurgeryPartialWitness":
        """Reconstruct a :class:`RepairControlledSurgeryPartialWitness` from a dict.

        This is the inverse of :meth:`to_dict`.  All nested structures are
        reconstructed from their serialised forms.  The ``surgery`` field is
        reconstructed to a :class:`RepairSurgery` whose ``replacement`` is
        reconstructed as a :class:`SectionReplacement`.

        Parameters
        ----------
        d : dict[str, Any]
            A dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        RepairControlledSurgeryPartialWitness
            A fully reconstructed witness.
        """
        # Reconstruct SectionReplacement
        rd = d["surgery"]["replacement"]
        repl = SectionReplacement(
            replacement_id=rd["replacement_id"],
            coordinate=rd["coordinate"],
            old_section_repr=rd["old_section_repr"],
            new_section_repr=rd["new_section_repr"],
            surgery_kind=rd["surgery_kind"],
            affected_coordinates=tuple(rd["affected_coordinates"]),
            descent_checks=tuple(
                (item["coordinate"], item["passed"])
                for item in rd["descent_checks"]
            ),
            confidence=rd["confidence"],
            is_minimal=rd["is_minimal"],
            timestamp=rd["timestamp"],
        )
        # Reconstruct RepairSurgery
        sd = d["surgery"]
        surgery = RepairSurgery(
            surgery_id=sd["surgery_id"],
            coordinate=sd["coordinate"],
            replacement=repl,
            status=sd["status"],
            validation_results=tuple(
                (item["check"], item["passed"])
                for item in sd["validation_results"]
            ),
            rollback_token=sd["rollback_token"],
            surgeon_id=sd["surgeon_id"],
            timestamp_start=sd["timestamp_start"],
            timestamp_end=sd["timestamp_end"],
        )
        return cls(
            witness_id=d["witness_id"],
            coordinate=d["coordinate"],
            surgery=surgery,
            partial_section_before=tuple(
                (item["coord"], item["section"])
                for item in d["partial_section_before"]
            ),
            partial_section_after=tuple(
                (item["coord"], item["section"])
                for item in d["partial_section_after"]
            ),
            descent_condition_satisfied=d["descent_condition_satisfied"],
            scope_preserved=d["scope_preserved"],
            obstruction_resolved=d["obstruction_resolved"],
            confidence=d["confidence"],
            provenance=tuple(
                (item["key"], item["value"]) for item in d["provenance"]
            ),
            timestamp=d["timestamp"],
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary of the witness.

        Returns
        -------
        str
            A descriptive string including the witness id, coordinate,
            validity, confidence, and coverage delta.
        """
        return (
            f"[{self.witness_id[:8]}] coord={self.coordinate} "
            f"valid={self.is_valid_repair()} "
            f"conf={self.confidence:.2f} "
            f"delta={self.coverage_delta():+d} "
            f"scope={self.scope_preserved} "
            f"descent={self.descent_condition_satisfied} "
            f"obstruction={self.obstruction_resolved}"
        )


# ---------------------------------------------------------------------------
# §03.5  RepairControlledSurgeryPartialAnalyzer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairControlledSurgeryPartialAnalyzer:
    """Core analyzer for controlled surgical repair of a partial section.

    The analyzer is responsible for four phases:

    1. **Planning** (:meth:`plan_surgery`) — compute a :class:`SectionReplacement`
       given an old and new section representation, verifying scope limits and
       performing initial descent checks.
    2. **Execution** (:meth:`execute_surgery`) — apply the replacement to a
       live partial section, producing a :class:`RepairControlledSurgeryPartialWitness`.
    3. **Validation** (:meth:`validate_surgery`) — run a battery of named checks
       on the witness and return a ``(check_name, passed)`` tuple.
    4. **Rollback** (:meth:`rollback`) — recover the pre-surgery partial section
       from a witness (simulated rollback; no mutable state is stored).

    Parameters
    ----------
    analyzer_id : str
        Unique identifier for this analyzer instance.  Auto-generated from a
        UUID if not supplied by the caller.
    coordinate : str
        The dot-separated coordinate that this analyzer is responsible for,
        e.g. ``"root.module.function"``.
    surgery_kind : str
        The :class:`SurgeryKind` name for the type of surgery this analyzer
        performs.  Defaults to ``"LOCAL_REPLACEMENT"``.
    scope_limit : int
        Maximum number of coordinates that can be touched in a single surgery.
        Repairs touching more coordinates than ``scope_limit`` are flagged as
        non-minimal.  Defaults to ``1``.
    require_descent_check : bool
        If ``True``, the analyzer will verify the descent condition during
        execution.  Defaults to ``True``.
    confidence_threshold : float
        Minimum confidence required for a surgery to be considered valid in
        :meth:`is_valid_repair`.  Defaults to ``0.8``.
    strict_mode : bool
        If ``True``, any validation failure raises an exception.  If ``False``
        (default), failures are recorded but do not raise.

    Notes
    -----
    This class is **frozen** and **slot-based**.  It holds no mutable state.
    All outputs are new immutable objects.  This makes it safe to share
    analyzer instances across threads or coroutines without locking.

    The ``analyzer_id`` field has a default factory so callers can omit it::

        analyzer = RepairControlledSurgeryPartialAnalyzer(
            coordinate="root.module.f",
            scope_limit=2,
        )
    """

    analyzer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    coordinate: str = "root"
    surgery_kind: str = SurgeryKind.LOCAL_REPLACEMENT.value
    scope_limit: int = 1
    require_descent_check: bool = True
    confidence_threshold: float = 0.8
    strict_mode: bool = False

    # ------------------------------------------------------------------
    # §Z.1  Surgery planning
    # ------------------------------------------------------------------

    def plan_surgery(
        self,
        old_section: str,
        new_section: str,
        affected_coords: Sequence[str],
    ) -> SectionReplacement:
        """Plan a surgical replacement and return a :class:`SectionReplacement`.

        Creates an immutable :class:`SectionReplacement` record capturing the
        proposed change, initial descent checks (optimistically all passing),
        and a computed confidence score.

        Parameters
        ----------
        old_section : str
            The current section representation at :attr:`coordinate`.
        new_section : str
            The proposed replacement section representation.
        affected_coords : Sequence[str]
            All coordinates whose descent condition must be verified.  Should
            contain :attr:`coordinate` itself and any overlapping-patch neighbours.

        Returns
        -------
        SectionReplacement
            An immutable replacement record ready for execution.

        Notes
        -----
        The ``is_minimal`` flag is set to ``True`` iff ``len(affected_coords) <=
        scope_limit``.  The confidence is computed by
        :meth:`_compute_replacement_confidence`.
        """
        affected_tuple: tuple[str, ...] = tuple(affected_coords)
        confidence = self._compute_replacement_confidence(
            old_section, new_section, len(affected_tuple)
        )
        is_minimal = len(affected_tuple) <= self.scope_limit
        descent_checks = _default_descent_checks(affected_tuple)
        replacement_id = f"repl-{_stable_hash8(self.coordinate + old_section + new_section)}"
        return SectionReplacement(
            replacement_id=replacement_id,
            coordinate=self.coordinate,
            old_section_repr=old_section,
            new_section_repr=new_section,
            surgery_kind=self.surgery_kind,
            affected_coordinates=affected_tuple,
            descent_checks=descent_checks,
            confidence=confidence,
            is_minimal=is_minimal,
            timestamp=_iso_timestamp(),
        )

    def _compute_replacement_confidence(
        self,
        old_section: str,
        new_section: str,
        affected_count: int,
    ) -> float:
        """Compute a confidence score for the proposed replacement.

        The base formula is::

            confidence = 1.0 - (affected_count / max(scope_limit, 1)) * 0.3

        This is then penalised by 0.2 if ``new_section`` is empty (no
        improvement) and clamped to ``[0.0, 1.0]``.

        Parameters
        ----------
        old_section : str
            The old section representation (used for similarity check).
        new_section : str
            The proposed new section representation.
        affected_count : int
            Number of coordinates affected by the replacement.

        Returns
        -------
        float
            Confidence in ``[0.0, 1.0]``.
        """
        base = 1.0 - (affected_count / max(self.scope_limit, 1)) * 0.3
        if not new_section:
            base -= 0.2
        # Small additional penalty for very similar old/new (no real change)
        similarity = _section_similarity(old_section, new_section)
        if similarity > 0.99:
            base -= 0.15
        return max(0.0, min(1.0, round(base, 4)))

    def _check_scope_preserved(
        self,
        affected_coords: Sequence[str],
        root_coordinate: str,
    ) -> bool:
        """Return ``True`` iff all affected coordinates are within scope.

        "Within scope" means the coordinate starts with ``root_coordinate``
        or is equal to it.  This encodes the invariant that surgery must
        not touch coordinates outside the root's subtree.

        Parameters
        ----------
        affected_coords : Sequence[str]
            The coordinates to check.
        root_coordinate : str
            The root (target) coordinate defining the scope boundary.

        Returns
        -------
        bool
            ``True`` if every coordinate in *affected_coords* has
            ``root_coordinate`` as a prefix.
        """
        for coord in affected_coords:
            if coord != root_coordinate and not coord.startswith(root_coordinate + "."):
                return False
        return True

    # ------------------------------------------------------------------
    # §Z.2  Surgery execution
    # ------------------------------------------------------------------

    def execute_surgery(
        self,
        replacement: SectionReplacement,
        partial_section: Sequence[tuple[str, str]],
    ) -> RepairControlledSurgeryPartialWitness:
        """Execute a surgery and return a :class:`RepairControlledSurgeryPartialWitness`.

        This method applies the replacement to *partial_section* by replacing
        (or appending) the entry for ``replacement.coordinate``, then checks
        the three core invariants:

        1. Scope preservation — :meth:`_check_scope_preserved`.
        2. Descent condition — :meth:`_check_descent_condition`.
        3. Obstruction resolution — :meth:`compute_obstruction_resolution`.

        Parameters
        ----------
        replacement : SectionReplacement
            The planned replacement to execute.
        partial_section : Sequence[tuple[str, str]]
            The current partial section as ``(coordinate, section_repr)`` pairs.

        Returns
        -------
        RepairControlledSurgeryPartialWitness
            A complete witness recording the before/after state and all
            invariant flags.

        Notes
        -----
        The returned witness contains a :class:`RepairSurgery` with status
        ``COMPLETED`` and an empty ``validation_results`` tuple (validation
        is a separate phase).
        """
        ts_start = _iso_timestamp()
        partial_before: tuple[tuple[str, str], ...] = tuple(partial_section)

        # Apply the replacement
        partial_after = self._apply_replacement(partial_before, replacement)

        # Check invariants
        scope_preserved = self._check_scope_preserved(
            replacement.affected_coordinates, self.coordinate
        )
        descent_result = self._check_descent_condition(
            partial_before, partial_after, replacement.affected_coordinates
        )
        descent_ok = all(passed for _, passed in descent_result)
        obstruction_resolved = self.compute_obstruction_resolution(
            replacement.old_section_repr, replacement.new_section_repr
        )

        # Aggregate confidence
        confidence = _compute_surgery_confidence(
            scope_preserved, descent_ok, obstruction_resolved
        )

        # Build rollback token
        rollback_token = self.generate_rollback_token(
            self.coordinate, replacement.old_section_repr
        )

        ts_end = _iso_timestamp()

        # Construct RepairSurgery (COMPLETED status)
        updated_replacement = replace(replacement, descent_checks=descent_result)
        surgery = RepairSurgery(
            surgery_id=f"surg-{str(uuid.uuid4())[:8]}",
            coordinate=self.coordinate,
            replacement=updated_replacement,
            status=SurgeryStatus.COMPLETED.value,
            validation_results=(),
            rollback_token=rollback_token,
            surgeon_id=self.analyzer_id,
            timestamp_start=ts_start,
            timestamp_end=ts_end,
        )

        provenance: tuple[tuple[str, str], ...] = (
            ("analyzer_id", self.analyzer_id),
            ("surgery_kind", self.surgery_kind),
            ("theory_ref", "theory2.tex Ch11 §11.3"),
            ("scope_limit", str(self.scope_limit)),
        )

        return RepairControlledSurgeryPartialWitness(
            witness_id=f"wit-{str(uuid.uuid4())[:8]}",
            coordinate=self.coordinate,
            surgery=surgery,
            partial_section_before=partial_before,
            partial_section_after=partial_after,
            descent_condition_satisfied=descent_ok,
            scope_preserved=scope_preserved,
            obstruction_resolved=obstruction_resolved,
            confidence=confidence,
            provenance=provenance,
            timestamp=ts_end,
        )

    def _apply_replacement(
        self,
        partial: tuple[tuple[str, str], ...],
        replacement: SectionReplacement,
    ) -> tuple[tuple[str, str], ...]:
        """Apply a replacement to a partial section tuple.

        Replaces the ``(coordinate, _)`` entry whose coordinate matches
        ``replacement.coordinate`` with ``(coordinate, new_section_repr)``.
        If no such entry exists, appends a new entry at the end.

        Parameters
        ----------
        partial : tuple[tuple[str, str], ...]
            The current partial section.
        replacement : SectionReplacement
            The replacement to apply.

        Returns
        -------
        tuple[tuple[str, str], ...]
            A new tuple with the replacement applied.  The original tuple is
            not modified.
        """
        target = replacement.coordinate
        new_val = replacement.new_section_repr
        result: list[tuple[str, str]] = []
        found = False
        for coord, section in partial:
            if coord == target:
                result.append((coord, new_val))
                found = True
            else:
                result.append((coord, section))
        if not found:
            result.append((target, new_val))
        return tuple(result)

    def _check_descent_condition(
        self,
        partial_before: tuple[tuple[str, str], ...],
        partial_after: tuple[tuple[str, str], ...],
        affected: tuple[str, ...],
    ) -> tuple[tuple[str, bool], ...]:
        """Check the descent condition for each affected coordinate.

        For each coordinate in *affected*:

        * Pass if the coordinate is present in *partial_after*.
        * Additionally, for the *target* coordinate (``self.coordinate``),
          verify that the section value has actually changed relative to
          *partial_before* (the replacement is non-trivial).
        * For non-target affected coordinates, verify that their section
          value is *unchanged* — the surgery should not have altered them
          (scope preservation as a descent proxy).

        Parameters
        ----------
        partial_before : tuple[tuple[str, str], ...]
            Partial section before the surgery.
        partial_after : tuple[tuple[str, str], ...]
            Partial section after the surgery.
        affected : tuple[str, ...]
            Coordinates to check.

        Returns
        -------
        tuple[tuple[str, bool], ...]
            ``(coordinate, passed)`` pairs, one per affected coordinate.
        """
        after_map: dict[str, str] = dict(partial_after)
        before_map: dict[str, str] = dict(partial_before)
        results: list[tuple[str, bool]] = []
        for coord in affected:
            if coord not in after_map:
                results.append((coord, False))
                continue
            if coord == self.coordinate:
                # Target: value must have changed (or have been newly added)
                old_val = before_map.get(coord, "")
                new_val = after_map[coord]
                passed = new_val != old_val or coord not in before_map
            else:
                # Non-target: value must be *unchanged* by the surgery
                old_val = before_map.get(coord, "")
                new_val = after_map[coord]
                passed = new_val == old_val
            results.append((coord, passed))
        return tuple(results)

    # ------------------------------------------------------------------
    # §Z.3  Validation
    # ------------------------------------------------------------------

    def validate_surgery(
        self,
        witness: RepairControlledSurgeryPartialWitness,
    ) -> tuple[tuple[str, bool], ...]:
        """Run the standard validation battery on a repair witness.

        The five checks are:

        1. ``scope_check`` — ``witness.scope_preserved`` is ``True``.
        2. ``descent_check`` — ``witness.descent_condition_satisfied`` is ``True``.
        3. ``obstruction_check`` — ``witness.obstruction_resolved`` is ``True``.
        4. ``coverage_check`` — ``witness.coverage_delta() >= 0`` (no coordinates lost).
        5. ``confidence_check`` — ``witness.confidence >= self.confidence_threshold``.

        Parameters
        ----------
        witness : RepairControlledSurgeryPartialWitness
            The witness to validate.

        Returns
        -------
        tuple[tuple[str, bool], ...]
            A tuple of ``(check_name, passed)`` pairs in the order listed above.
        """
        checks: list[tuple[str, bool]] = [
            ("scope_check", witness.scope_preserved),
            ("descent_check", witness.descent_condition_satisfied),
            ("obstruction_check", witness.obstruction_resolved),
            ("coverage_check", witness.coverage_delta() >= 0),
            ("confidence_check", witness.confidence >= self.confidence_threshold),
        ]
        return tuple(checks)

    def is_valid_repair(
        self,
        witness: RepairControlledSurgeryPartialWitness,
    ) -> bool:
        """Return ``True`` iff all validation checks pass for *witness*.

        Parameters
        ----------
        witness : RepairControlledSurgeryPartialWitness
            The witness to validate.

        Returns
        -------
        bool
            ``True`` only if every check in :meth:`validate_surgery` passes.
        """
        return all(passed for _, passed in self.validate_surgery(witness))

    def build_validation_report(
        self,
        witness: RepairControlledSurgeryPartialWitness,
    ) -> dict[str, Any]:
        """Build a structured validation report for a witness.

        Parameters
        ----------
        witness : RepairControlledSurgeryPartialWitness
            The witness to report on.

        Returns
        -------
        dict[str, Any]
            A dictionary with the following keys:

            * ``"witness_id"`` — the witness's unique ID.
            * ``"coordinate"`` — the surgery coordinate.
            * ``"valid"`` — overall validity (all checks passed).
            * ``"checks"`` — list of ``{"check": name, "passed": bool}`` dicts.
            * ``"failures"`` — list of check names that failed.
            * ``"confidence"`` — the witness's confidence score.
        """
        checks = self.validate_surgery(witness)
        failures = [name for name, passed in checks if not passed]
        return {
            "witness_id": witness.witness_id,
            "coordinate": witness.coordinate,
            "valid": len(failures) == 0,
            "checks": [{"check": name, "passed": passed} for name, passed in checks],
            "failures": failures,
            "confidence": witness.confidence,
        }

    # ------------------------------------------------------------------
    # §Z.4  Section analysis
    # ------------------------------------------------------------------

    def analyze_partial_section(
        self,
        partial: Sequence[tuple[str, str]],
    ) -> dict[str, Any]:
        """Analyse a partial section and return a summary dict.

        Parameters
        ----------
        partial : Sequence[tuple[str, str]]
            The partial section to analyse, as ``(coordinate, section_repr)``
            pairs.

        Returns
        -------
        dict[str, Any]
            A dictionary with the following keys:

            * ``"coordinate_count"`` — number of coordinates in the partial section.
            * ``"has_target"`` — ``True`` iff :attr:`coordinate` appears in the
              partial section.
            * ``"coverage_ratio"`` — ``coordinate_count / max(scope_limit, 1)``,
              clamped to ``[0.0, 1.0]``.
            * ``"section_lengths"`` — list of ``len(section_repr)`` for each entry.
        """
        coords = [c for c, _ in partial]
        lengths = [len(s) for _, s in partial]
        has_target = self.coordinate in coords
        coverage_ratio = min(1.0, len(coords) / max(self.scope_limit, 1))
        return {
            "coordinate_count": len(coords),
            "has_target": has_target,
            "coverage_ratio": round(coverage_ratio, 4),
            "section_lengths": lengths,
        }

    def compute_obstruction_resolution(
        self,
        before_section: str,
        after_section: str,
    ) -> bool:
        """Heuristically determine whether the obstruction has been resolved.

        The obstruction is considered resolved iff:

        * ``after_section`` is non-empty, **and**
        * ``after_section != before_section`` (the section was genuinely changed).

        Parameters
        ----------
        before_section : str
            Section representation before the surgery.
        after_section : str
            Section representation after the surgery.

        Returns
        -------
        bool
            ``True`` iff the obstruction appears to have been cleared.
        """
        return bool(after_section) and after_section != before_section

    # ------------------------------------------------------------------
    # §Z.5  Rollback
    # ------------------------------------------------------------------

    def generate_rollback_token(self, coordinate: str, old_section: str) -> str:
        """Generate a stable rollback token for identifying the pre-surgery state.

        The token is an 8-character hex string derived from the coordinate
        and the old section representation.  It is *stable* (deterministic
        for the same inputs) and *opaque* to external observers.

        Parameters
        ----------
        coordinate : str
            The coordinate at which the surgery is performed.
        old_section : str
            The section representation before the surgery.

        Returns
        -------
        str
            An 8-character hex rollback token.
        """
        return _stable_hash8(f"{coordinate}::{old_section}")

    def can_rollback(
        self, witness: RepairControlledSurgeryPartialWitness
    ) -> bool:
        """Return ``True`` iff the surgery has a non-empty rollback token.

        Parameters
        ----------
        witness : RepairControlledSurgeryPartialWitness
            The witness to inspect.

        Returns
        -------
        bool
            ``True`` if ``witness.surgery.rollback_token`` is a non-empty string.
        """
        return bool(witness.surgery.rollback_token)

    def rollback(
        self,
        witness: RepairControlledSurgeryPartialWitness,
    ) -> tuple[tuple[str, str], ...]:
        """Simulate a rollback by returning the pre-surgery partial section.

        This is a *simulated* rollback: since all state is immutable and
        captured in the witness, the rollback simply returns
        ``witness.partial_section_before``.

        Parameters
        ----------
        witness : RepairControlledSurgeryPartialWitness
            The witness whose pre-surgery state to restore.

        Returns
        -------
        tuple[tuple[str, str], ...]
            The partial section as it was before the surgery was executed.

        Raises
        ------
        ValueError
            If :meth:`can_rollback` returns ``False`` for the witness (no
            rollback token recorded).
        """
        if not self.can_rollback(witness):
            raise ValueError(
                f"Cannot rollback witness {witness.witness_id}: "
                "surgery has no rollback token."
            )
        return witness.partial_section_before

    # ------------------------------------------------------------------
    # §Z.6  Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the analyzer configuration to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            All fields serialised.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "coordinate": self.coordinate,
            "surgery_kind": self.surgery_kind,
            "scope_limit": self.scope_limit,
            "require_descent_check": self.require_descent_check,
            "confidence_threshold": self.confidence_threshold,
            "strict_mode": self.strict_mode,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RepairControlledSurgeryPartialAnalyzer":
        """Reconstruct an analyzer from a serialised dict.

        Parameters
        ----------
        d : dict[str, Any]
            A dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        RepairControlledSurgeryPartialAnalyzer
            A fully reconstructed analyzer instance.
        """
        return cls(
            analyzer_id=d.get("analyzer_id", str(uuid.uuid4())),
            coordinate=d.get("coordinate", "root"),
            surgery_kind=d.get("surgery_kind", SurgeryKind.LOCAL_REPLACEMENT.value),
            scope_limit=d.get("scope_limit", 1),
            require_descent_check=d.get("require_descent_check", True),
            confidence_threshold=d.get("confidence_threshold", 0.8),
            strict_mode=d.get("strict_mode", False),
        )


# ---------------------------------------------------------------------------
# §03.6  RepairControlledSurgeryPartialCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairControlledSurgeryPartialCoordinator:
    """Coordinator that orchestrates multiple repair surgery analyzers.

    The coordinator holds a tuple of :class:`RepairControlledSurgeryPartialAnalyzer`
    instances and runs them all against the same surgery inputs.  It then
    aggregates the resulting witnesses, selects the best one, and produces a
    summary report.

    This pattern supports multi-strategy repair: different analyzers can use
    different ``surgery_kind``, ``scope_limit``, or ``confidence_threshold``
    values, and the coordinator selects whichever strategy produces the
    highest-confidence valid witness.

    Parameters
    ----------
    coordinator_id : str
        Unique identifier for this coordinator.  Auto-generated if omitted.
    analyzers : tuple[RepairControlledSurgeryPartialAnalyzer, ...]
        The analyzers to run.  Starts empty; use :meth:`add_analyzer` to
        build up the collection immutably.
    root_coordinate : str
        The root coordinate for the repair session.  All analyzer coordinates
        should be descendants of this root.
    session_id : str
        Identifier for the repair session (links witnesses to a
        :class:`DebugSession`).
    max_surgeries : int
        Maximum number of surgeries to execute before stopping.  Defaults
        to ``16``.  Guards against runaway coordinator loops.
    require_all_valid : bool
        If ``True``, :meth:`build_surgery_report` will mark the overall
        result as failed if any witness is invalid.  Defaults to ``False``.

    Notes
    -----
    Like all JuGeo coordinators, this class is frozen.  Adding an analyzer
    returns a *new* coordinator via :meth:`add_analyzer`.

    Examples
    --------
    ::

        coordinator = RepairControlledSurgeryPartialCoordinator(
            root_coordinate="root",
            session_id="sess-001",
        )
        coordinator = coordinator.add_analyzer(analyzer_a)
        coordinator = coordinator.add_analyzer(analyzer_b)
        witnesses = coordinator.run_surgery(old, new, partial, affected)
        best = coordinator.select_best_witness(witnesses)
    """

    coordinator_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    analyzers: tuple["RepairControlledSurgeryPartialAnalyzer", ...] = field(
        default_factory=tuple
    )
    root_coordinate: str = "root"
    session_id: str = ""
    max_surgeries: int = 16
    require_all_valid: bool = False

    # ------------------------------------------------------------------
    # §03.6.1  Analyzer management
    # ------------------------------------------------------------------

    def add_analyzer(
        self,
        analyzer: "RepairControlledSurgeryPartialAnalyzer",
    ) -> "RepairControlledSurgeryPartialCoordinator":
        """Return a new coordinator with *analyzer* appended to its tuple.

        Parameters
        ----------
        analyzer : RepairControlledSurgeryPartialAnalyzer
            The analyzer to add.

        Returns
        -------
        RepairControlledSurgeryPartialCoordinator
            A new coordinator instance with the analyzer included.
        """
        return replace(self, analyzers=self.analyzers + (analyzer,))

    # ------------------------------------------------------------------
    # §03.6.2  Surgery execution
    # ------------------------------------------------------------------

    def run_surgery(
        self,
        old_section: str,
        new_section: str,
        partial: Sequence[tuple[str, str]],
        affected: Sequence[str],
    ) -> tuple[RepairControlledSurgeryPartialWitness, ...]:
        """Run all analyzers and collect the resulting witnesses.

        For each analyzer in :attr:`analyzers` (up to :attr:`max_surgeries`):

        1. Call :meth:`~RepairControlledSurgeryPartialAnalyzer.plan_surgery`
           to get a :class:`SectionReplacement`.
        2. Call :meth:`~RepairControlledSurgeryPartialAnalyzer.execute_surgery`
           with the replacement and *partial*.
        3. Collect the returned :class:`RepairControlledSurgeryPartialWitness`.

        Parameters
        ----------
        old_section : str
            The old section representation for all analyzers.
        new_section : str
            The new section representation for all analyzers.
        partial : Sequence[tuple[str, str]]
            The current partial section.
        affected : Sequence[str]
            Coordinates to check for descent.

        Returns
        -------
        tuple[RepairControlledSurgeryPartialWitness, ...]
            One witness per analyzer that was run (up to :attr:`max_surgeries`).
        """
        witnesses: list[RepairControlledSurgeryPartialWitness] = []
        for analyzer in self.analyzers[: self.max_surgeries]:
            try:
                replacement = analyzer.plan_surgery(old_section, new_section, affected)
                witness = analyzer.execute_surgery(replacement, partial)
                witnesses.append(witness)
            except Exception:
                # Individual analyzer failures should not abort the coordinator
                pass
        return tuple(witnesses)

    # ------------------------------------------------------------------
    # §03.6.3  Best-witness selection
    # ------------------------------------------------------------------

    def select_best_witness(
        self,
        witnesses: Sequence[RepairControlledSurgeryPartialWitness],
    ) -> "RepairControlledSurgeryPartialWitness | None":
        """Select the best witness from a collection.

        Selection strategy:

        1. Among all witnesses for which :meth:`~RepairControlledSurgeryPartialWitness.is_valid_repair`
           returns ``True``, choose the one with the highest ``confidence``.
        2. If no valid witnesses exist, choose the witness with the highest
           ``confidence`` overall (graceful degradation).
        3. If *witnesses* is empty, return ``None``.

        Parameters
        ----------
        witnesses : Sequence[RepairControlledSurgeryPartialWitness]
            The witnesses to choose from.

        Returns
        -------
        RepairControlledSurgeryPartialWitness or None
            The best witness, or ``None`` if the sequence is empty.
        """
        if not witnesses:
            return None
        valid = [w for w in witnesses if w.is_valid_repair()]
        pool = valid if valid else list(witnesses)
        return max(pool, key=lambda w: w.confidence)

    # ------------------------------------------------------------------
    # §03.6.4  Reporting
    # ------------------------------------------------------------------

    def build_surgery_report(
        self,
        witnesses: Sequence[RepairControlledSurgeryPartialWitness],
    ) -> dict[str, Any]:
        """Build an aggregate report over a collection of witnesses.

        Parameters
        ----------
        witnesses : Sequence[RepairControlledSurgeryPartialWitness]
            The witnesses to aggregate.

        Returns
        -------
        dict[str, Any]
            A dictionary with the following keys:

            * ``"total_witnesses"`` — total number of witnesses.
            * ``"valid_count"`` — number of valid witnesses.
            * ``"invalid_count"`` — number of invalid witnesses.
            * ``"best_confidence"`` — highest confidence seen (0.0 if empty).
            * ``"obstruction_resolution_rate"`` — fraction of witnesses where
              ``obstruction_resolved`` is ``True``.
            * ``"scope_preservation_rate"`` — fraction where ``scope_preserved``
              is ``True``.
            * ``"coordinator_id"`` — this coordinator's ID.
            * ``"session_id"`` — the repair session ID.
            * ``"overall_valid"`` — ``True`` iff ``valid_count > 0`` (or, if
              ``require_all_valid`` is set, ``valid_count == total_witnesses``).
        """
        total = len(witnesses)
        if total == 0:
            return {
                "total_witnesses": 0,
                "valid_count": 0,
                "invalid_count": 0,
                "best_confidence": 0.0,
                "obstruction_resolution_rate": 0.0,
                "scope_preservation_rate": 0.0,
                "coordinator_id": self.coordinator_id,
                "session_id": self.session_id,
                "overall_valid": False,
            }
        valid_count = sum(1 for w in witnesses if w.is_valid_repair())
        invalid_count = total - valid_count
        best_confidence = max(w.confidence for w in witnesses)
        obstruction_resolved_count = sum(
            1 for w in witnesses if w.obstruction_resolved
        )
        scope_preserved_count = sum(
            1 for w in witnesses if w.scope_preserved
        )
        if self.require_all_valid:
            overall_valid = valid_count == total
        else:
            overall_valid = valid_count > 0
        return {
            "total_witnesses": total,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "best_confidence": round(best_confidence, 4),
            "obstruction_resolution_rate": round(
                obstruction_resolved_count / total, 4
            ),
            "scope_preservation_rate": round(scope_preserved_count / total, 4),
            "coordinator_id": self.coordinator_id,
            "session_id": self.session_id,
            "overall_valid": overall_valid,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise the coordinator to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields serialised.  Each analyzer is serialised via its own
            :meth:`~RepairControlledSurgeryPartialAnalyzer.to_dict`.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "root_coordinate": self.root_coordinate,
            "session_id": self.session_id,
            "max_surgeries": self.max_surgeries,
            "require_all_valid": self.require_all_valid,
            "analyzers": [a.to_dict() for a in self.analyzers],
        }


# ---------------------------------------------------------------------------
# §03.7  Convenience factory functions
# ---------------------------------------------------------------------------


def make_local_replacement_analyzer(
    coordinate: str,
    scope_limit: int = 1,
    confidence_threshold: float = 0.8,
) -> RepairControlledSurgeryPartialAnalyzer:
    """Create a :class:`RepairControlledSurgeryPartialAnalyzer` for ``LOCAL_REPLACEMENT``.

    Parameters
    ----------
    coordinate : str
        The target coordinate.
    scope_limit : int
        Maximum affected coordinates.
    confidence_threshold : float
        Minimum confidence for validation.

    Returns
    -------
    RepairControlledSurgeryPartialAnalyzer
        Configured for local replacement surgery.
    """
    return RepairControlledSurgeryPartialAnalyzer(
        coordinate=coordinate,
        surgery_kind=SurgeryKind.LOCAL_REPLACEMENT.value,
        scope_limit=scope_limit,
        confidence_threshold=confidence_threshold,
    )


def make_section_extension_analyzer(
    coordinate: str,
    scope_limit: int = 2,
    confidence_threshold: float = 0.75,
) -> RepairControlledSurgeryPartialAnalyzer:
    """Create an analyzer configured for ``SECTION_EXTENSION`` surgery.

    Parameters
    ----------
    coordinate : str
        The root coordinate from which to extend.
    scope_limit : int
        Maximum number of new coordinates to introduce.
    confidence_threshold : float
        Minimum confidence for validation.

    Returns
    -------
    RepairControlledSurgeryPartialAnalyzer
        Configured for section extension surgery.
    """
    return RepairControlledSurgeryPartialAnalyzer(
        coordinate=coordinate,
        surgery_kind=SurgeryKind.SECTION_EXTENSION.value,
        scope_limit=scope_limit,
        confidence_threshold=confidence_threshold,
    )


def make_descent_patch_analyzer(
    coordinate: str,
    scope_limit: int = 3,
    confidence_threshold: float = 0.7,
) -> RepairControlledSurgeryPartialAnalyzer:
    """Create an analyzer configured for ``DESCENT_PATCH`` surgery.

    Parameters
    ----------
    coordinate : str
        The coordinate at which the descent failure was detected.
    scope_limit : int
        Maximum affected coordinates (descent patches may touch more).
    confidence_threshold : float
        Minimum confidence for validation.

    Returns
    -------
    RepairControlledSurgeryPartialAnalyzer
        Configured for descent patch surgery.
    """
    return RepairControlledSurgeryPartialAnalyzer(
        coordinate=coordinate,
        surgery_kind=SurgeryKind.DESCENT_PATCH.value,
        scope_limit=scope_limit,
        confidence_threshold=confidence_threshold,
    )


def make_default_coordinator(
    root_coordinate: str,
    session_id: str,
    target_coordinate: str | None = None,
) -> RepairControlledSurgeryPartialCoordinator:
    """Create a :class:`RepairControlledSurgeryPartialCoordinator` with three default analyzers.

    Adds one analyzer of each of the three most common surgery kinds:
    ``LOCAL_REPLACEMENT``, ``SECTION_EXTENSION``, and ``DESCENT_PATCH``,
    all targeting *target_coordinate* (defaults to *root_coordinate*).

    Parameters
    ----------
    root_coordinate : str
        The root coordinate for the session.
    session_id : str
        The repair session identifier.
    target_coordinate : str, optional
        The coordinate to target.  Defaults to *root_coordinate*.

    Returns
    -------
    RepairControlledSurgeryPartialCoordinator
        A coordinator pre-loaded with three analyzers.
    """
    tc = target_coordinate or root_coordinate
    return (
        RepairControlledSurgeryPartialCoordinator(
            root_coordinate=root_coordinate,
            session_id=session_id,
        )
        .add_analyzer(make_local_replacement_analyzer(tc))
        .add_analyzer(make_section_extension_analyzer(tc))
        .add_analyzer(make_descent_patch_analyzer(tc))
    )


# ---------------------------------------------------------------------------
# §03.8  Batch / pipeline helpers
# ---------------------------------------------------------------------------


def run_full_surgery_pipeline(
    coordinator: RepairControlledSurgeryPartialCoordinator,
    old_section: str,
    new_section: str,
    partial: Sequence[tuple[str, str]],
    affected: Sequence[str],
) -> dict[str, Any]:
    """Run a full surgery pipeline and return a comprehensive result dict.

    This convenience function:

    1. Runs all analyzers via :meth:`~RepairControlledSurgeryPartialCoordinator.run_surgery`.
    2. Selects the best witness via :meth:`~RepairControlledSurgeryPartialCoordinator.select_best_witness`.
    3. Builds the aggregate report via :meth:`~RepairControlledSurgeryPartialCoordinator.build_surgery_report`.

    Parameters
    ----------
    coordinator : RepairControlledSurgeryPartialCoordinator
        The coordinator to use.
    old_section : str
        Old section representation.
    new_section : str
        New section representation.
    partial : Sequence[tuple[str, str]]
        Current partial section.
    affected : Sequence[str]
        Affected coordinates for descent checking.

    Returns
    -------
    dict[str, Any]
        A dictionary with keys:

        * ``"report"`` — the aggregate report from :meth:`build_surgery_report`.
        * ``"best_witness"`` — the best witness dict (or ``None``).
        * ``"all_witnesses"`` — list of all witness summaries.
        * ``"pipeline_version"`` — ``"v1"``.
    """
    witnesses = coordinator.run_surgery(old_section, new_section, partial, affected)
    best = coordinator.select_best_witness(witnesses)
    report = coordinator.build_surgery_report(witnesses)
    return {
        "report": report,
        "best_witness": best.to_dict() if best is not None else None,
        "all_witnesses": [w.summary() for w in witnesses],
        "pipeline_version": "v1",
    }


def partial_section_to_dict(
    partial: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """Convert a partial section sequence to a plain dictionary.

    Parameters
    ----------
    partial : Sequence[tuple[str, str]]
        A partial section as ``(coordinate, section_repr)`` pairs.

    Returns
    -------
    dict[str, str]
        A dictionary mapping coordinates to section representations.
        Later entries overwrite earlier ones for duplicate coordinates.
    """
    return dict(partial)


def dict_to_partial_section(
    d: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    """Convert a plain dictionary to a partial section tuple.

    Parameters
    ----------
    d : dict[str, str]
        A mapping from coordinate strings to section representations.

    Returns
    -------
    tuple[tuple[str, str], ...]
        An ordered tuple of ``(coordinate, section_repr)`` pairs.  The order
        is the insertion order of the dictionary.
    """
    return tuple(d.items())


def merge_partial_sections(
    base: Sequence[tuple[str, str]],
    patch: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """Merge two partial sections, with *patch* taking precedence.

    For each coordinate in *patch*, the value from *patch* overwrites any
    existing value from *base*.  Coordinates in *base* that are not in
    *patch* are preserved.

    Parameters
    ----------
    base : Sequence[tuple[str, str]]
        The base partial section.
    patch : Sequence[tuple[str, str]]
        The patch partial section (takes precedence on conflicts).

    Returns
    -------
    tuple[tuple[str, str], ...]
        The merged partial section.
    """
    merged: dict[str, str] = dict(base)
    for coord, section in patch:
        merged[coord] = section
    return tuple(merged.items())


def filter_partial_section_by_prefix(
    partial: Sequence[tuple[str, str]],
    prefix: str,
) -> tuple[tuple[str, str], ...]:
    """Return only the entries in *partial* whose coordinate starts with *prefix*.

    Parameters
    ----------
    partial : Sequence[tuple[str, str]]
        The partial section to filter.
    prefix : str
        The coordinate prefix to match.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Only the entries whose coordinate equals *prefix* or starts with
        ``prefix + "."``.
    """
    return tuple(
        (c, s)
        for c, s in partial
        if c == prefix or c.startswith(prefix + ".")
    )


def compute_coverage_report(
    partial: Sequence[tuple[str, str]],
    expected_coordinates: Sequence[str],
) -> dict[str, Any]:
    """Compute a coverage report comparing a partial section to an expected set.

    Parameters
    ----------
    partial : Sequence[tuple[str, str]]
        The current partial section.
    expected_coordinates : Sequence[str]
        The full set of coordinates that should be covered.

    Returns
    -------
    dict[str, Any]
        A dictionary with:

        * ``"covered"`` — list of expected coordinates that are present.
        * ``"missing"`` — list of expected coordinates that are absent.
        * ``"extra"`` — list of coordinates in the partial section not in
          expected.
        * ``"coverage_ratio"`` — ``len(covered) / len(expected)``, or 1.0
          if *expected_coordinates* is empty.
    """
    actual_set = {c for c, _ in partial}
    expected_set = set(expected_coordinates)
    covered = sorted(actual_set & expected_set)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if not expected_set:
        ratio = 1.0
    else:
        ratio = round(len(covered) / len(expected_set), 4)
    return {
        "covered": covered,
        "missing": missing,
        "extra": extra,
        "coverage_ratio": ratio,
    }


# ---------------------------------------------------------------------------
# §03.9  Theory predicates (formal encoding of §11.3 conditions)
# ---------------------------------------------------------------------------


def predicate_scope_not_widened(
    surgery: RepairSurgery,
    root_coordinate: str,
) -> bool:
    """Formal encoding of the scope-preservation predicate from §11.3.

    Returns ``True`` iff the surgery's affected coordinates are all within
    the subtree rooted at *root_coordinate*.

    Parameters
    ----------
    surgery : RepairSurgery
        The surgery to check.
    root_coordinate : str
        The root coordinate defining the permitted scope.

    Returns
    -------
    bool
    """
    for coord in surgery.replacement.affected_coordinates:
        if coord != root_coordinate and not coord.startswith(root_coordinate + "."):
            return False
    return True


def predicate_descent_condition(
    witness: RepairControlledSurgeryPartialWitness,
) -> bool:
    """Formal encoding of the descent (cocycle) condition from §11.3.

    Returns ``True`` iff ``witness.descent_condition_satisfied`` is ``True``
    and all descent checks in ``witness.surgery.replacement.descent_checks``
    have passed.

    Parameters
    ----------
    witness : RepairControlledSurgeryPartialWitness
        The witness to inspect.

    Returns
    -------
    bool
    """
    if not witness.descent_condition_satisfied:
        return False
    return all(passed for _, passed in witness.surgery.replacement.descent_checks)


def predicate_obstruction_cleared(
    witness: RepairControlledSurgeryPartialWitness,
) -> bool:
    """Return ``True`` iff the obstruction at the surgery coordinate was cleared.

    Parameters
    ----------
    witness : RepairControlledSurgeryPartialWitness
        The witness to inspect.

    Returns
    -------
    bool
    """
    return witness.obstruction_resolved


def predicate_global_section_candidate(
    witness: RepairControlledSurgeryPartialWitness,
    expected_coordinates: Sequence[str],
) -> bool:
    """Return ``True`` iff the post-surgery partial section covers all expected coords.

    This predicate approximates the condition "the partial section is now a
    global section candidate" — all expected coordinates are present.

    Parameters
    ----------
    witness : RepairControlledSurgeryPartialWitness
        The witness to inspect.
    expected_coordinates : Sequence[str]
        The full set of coordinates required for a global section.

    Returns
    -------
    bool
    """
    after_coords = {c for c, _ in witness.partial_section_after}
    return all(c in after_coords for c in expected_coordinates)




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
    # Constants
    "MANIFEST_SPEC_PROVENANCE",
    # Enumerations
    "SurgeryKind",
    "SurgeryStatus",
    # Data models
    "SectionReplacement",
    "RepairSurgery",
    "RepairControlledSurgeryPartialWitness",
    # Core classes
    "RepairControlledSurgeryPartialAnalyzer",
    "RepairControlledSurgeryPartialCoordinator",
    # Factory functions
    "make_local_replacement_analyzer",
    "make_section_extension_analyzer",
    "make_descent_patch_analyzer",
    "make_default_coordinator",
    # Pipeline helpers
    "run_full_surgery_pipeline",
    "partial_section_to_dict",
    "dict_to_partial_section",
    "merge_partial_sections",
    "filter_partial_section_by_prefix",
    "compute_coverage_report",
    # Theory predicates
    "predicate_scope_not_widened",
    "predicate_descent_condition",
    "predicate_obstruction_cleared",
    "predicate_global_section_candidate",
    # Helper functions
    "_iso_timestamp",
    "_stable_hash8",
    "_section_similarity",
    "_compute_surgery_confidence",
    "_default_descent_checks",
    "_coordinate_depth",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end of s03 repair as controlled surgery on a partial section


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    analyzer = RepairControlledSurgeryPartialAnalyzer(
        coordinate="root.module.function",
        surgery_kind="LOCAL_REPLACEMENT",
        scope_limit=2,
    )
    partial_before = [
        ("root.module.function", "def f(x): return x + 1"),
        ("root.module", "module_header = True"),
        ("root", "project_root = True"),
    ]
    replacement = analyzer.plan_surgery(
        old_section="def f(x): return x + 1",
        new_section="def f(x): return max(x, 0) + 1",
        affected_coords=["root.module.function"],
    )
    print(f"Replacement id: {replacement.replacement_id}")
    print(f"Is valid: {replacement.is_valid()}")

    witness = analyzer.execute_surgery(replacement, partial_before)
    print(f"Witness id: {witness.witness_id}")
    print(f"Is valid repair: {witness.is_valid_repair()}")
    print(f"Scope preserved: {witness.scope_preserved}")
    print(f"Coverage delta: {witness.coverage_delta()}")

    checks = analyzer.validate_surgery(witness)
    print(f"Validation checks: {[c for c, _ in checks]}")

    coordinator = RepairControlledSurgeryPartialCoordinator(
        root_coordinate="root",
        session_id="test-session",
    )
    coordinator = coordinator.add_analyzer(analyzer)
    witnesses = coordinator.run_surgery(
        "def f(x): return x + 1",
        "def f(x): return max(x, 0) + 1",
        partial_before,
        ["root.module.function"],
    )
    report = coordinator.build_surgery_report(witnesses)
    print(f"Report: valid={report['valid_count']}/{report['total_witnesses']}")
    print("s03 smoke test passed")
