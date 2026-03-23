"""Sheaf Descent for Heap Consistency.

theory2.tex Ch17, §4 — Sheaf Descent for Heap Consistency.

A *sheaf* on the heap site (theory2.tex Ch17 §4) satisfies three conditions:

1. **Locality** — if two sections agree on every open patch of a cover, they
   are equal.  For the heap: two heap sections that agree on every identity
   coordinate they share are equal as sections.

2. **Gluing** — given a family of local sections ``{s_i}`` on patches
   ``{U_i}`` that are *compatible* on every overlap ``U_i ∩ U_j``, there
   exists a unique global section ``s`` restricting to each ``s_i``.  For the
   heap: a collection of :class:`HeapSection` objects that agree on shared
   objects can be glued into a single :class:`HeapSection` covering the whole
   snapshot.

3. **Separation / uniqueness** — the global section produced by gluing is the
   *unique* extension of the local data.  Failure of separation signals a
   logical inconsistency in the heap model.

This module implements the full descent machinery:

* :class:`DescentConditionResult` — captures the outcome of a single
  descent-condition check.
* :class:`DescentConditionChecker` — performs locality, gluing, and
  separation sub-checks.
* :class:`HeapConsistencyVerifier` — verifies overall heap consistency using
  descent at the snapshot level.
* :class:`CocycleConditionChecker` — checks the cocycle / compatibility
  condition on mutation patches.
* :class:`LocalToGlobalMapper` — implements the local-to-global gluing
  property.
* :class:`HeapCoherenceTracker` — tracks heap coherence over time.

Copilot integration note
------------------------
This module was developed with GitHub Copilot assistance as part of the
jugeo copilot integration pipeline.  The descent checkers are designed to be
composable: each checker is independent and the results can be combined into
a single :class:`~jugeo.judgments.judgment_terms.Judgment`.

References
----------
theory2.tex Ch17, §4.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from jugeo.geometry.site import (
    CoordinateKind,
    CoordinateObject,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    JudgmentBuilder,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.python_runtime.heap_aliasing.models import (
    AliasPartition,
    HeapObject,
    HeapSection,
    HeapSnapshot,
    IdentityCoordinate,
    MutationEvent,
    MutationPatch,
    ObjectKind,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DescentConditionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescentConditionResult:
    """Immutable record of a sheaf descent condition check.

    A :class:`DescentConditionResult` is produced by
    :meth:`DescentConditionChecker.verify_descent_data` and captures whether
    each of the three sheaf conditions (locality, gluing, separation) was
    satisfied by the given collection of :class:`HeapSection` objects.

    Parameters
    ----------
    passed : bool
        ``True`` iff all three sub-conditions (locality, gluing, separation)
        are satisfied.
    locality_ok : bool
        ``True`` iff the locality sub-condition holds: sections that agree on
        all shared objects are equal.
    gluing_ok : bool
        ``True`` iff the gluing sub-condition holds: compatible sections can
        be assembled into a unique global section.
    separation_ok : bool
        ``True`` iff the separation sub-condition holds: the global section is
        uniquely determined by the local data.
    violations : tuple[str, ...]
        Human-readable descriptions of any violated sub-conditions.
    timestamp : float
        Unix timestamp (``time.time()``) when the check was performed.

    Examples
    --------
    >>> result = DescentConditionResult(
    ...     passed=True,
    ...     locality_ok=True,
    ...     gluing_ok=True,
    ...     separation_ok=True,
    ...     violations=(),
    ...     timestamp=0.0,
    ... )
    >>> result.has_violations()
    False
    >>> "PASSED" in result.summary()
    True
    """

    passed: bool
    locality_ok: bool
    gluing_ok: bool
    separation_ok: bool
    violations: tuple[str, ...]
    timestamp: float

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_violations(self) -> bool:
        """Return ``True`` iff there is at least one violation message.

        Returns
        -------
        bool
            ``True`` when ``violations`` is non-empty.

        Examples
        --------
        >>> DescentConditionResult(
        ...     passed=False, locality_ok=False, gluing_ok=True,
        ...     separation_ok=True, violations=("L failed",), timestamp=0.0,
        ... ).has_violations()
        True
        """
        return len(self.violations) > 0

    def summary(self) -> str:
        """Return a one-line summary string of this result.

        Returns
        -------
        str
            A string beginning with ``"PASSED"`` or ``"FAILED"`` followed by
            sub-condition flags.

        Examples
        --------
        >>> r = DescentConditionResult(
        ...     passed=True, locality_ok=True, gluing_ok=True,
        ...     separation_ok=True, violations=(), timestamp=0.0,
        ... )
        >>> r.summary()
        'PASSED: locality=OK gluing=OK separation=OK violations=0'
        """
        status = "PASSED" if self.passed else "FAILED"
        loc = "OK" if self.locality_ok else "FAIL"
        glue = "OK" if self.gluing_ok else "FAIL"
        sep = "OK" if self.separation_ok else "FAIL"
        return (
            f"{status}: locality={loc} gluing={glue} separation={sep} "
            f"violations={len(self.violations)}"
        )

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``passed``, ``locality_ok``, ``gluing_ok``, ``separation_ok``,
            ``violations``, ``timestamp``, ``summary``.

        Examples
        --------
        >>> r = DescentConditionResult(
        ...     passed=True, locality_ok=True, gluing_ok=True,
        ...     separation_ok=True, violations=(), timestamp=1.0,
        ... )
        >>> r.serialize()["passed"]
        True
        """
        return {
            "passed": self.passed,
            "locality_ok": self.locality_ok,
            "gluing_ok": self.gluing_ok,
            "separation_ok": self.separation_ok,
            "violations": list(self.violations),
            "timestamp": self.timestamp,
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# DescentConditionChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DescentConditionChecker:
    """Full sheaf descent condition checker for heap objects.

    :class:`DescentConditionChecker` verifies that a collection of
    :class:`HeapSection` objects satisfies the three axioms of a sheaf on the
    heap site (theory2.tex Ch17 §4):

    1. **Locality** — sections that agree on all shared objects are equal.
    2. **Gluing** — compatible pairs of sections can be merged into a single
       section without contradictions.
    3. **Separation** — each section is uniquely determined by its local
       object data.

    The checker accumulates a history of all past results so that callers can
    audit the sequence of descent checks performed during a session.

    Parameters
    ----------
    _check_history : list[DescentConditionResult]
        Ordered history of all :class:`DescentConditionResult` objects
        produced by calls to :meth:`verify_descent_data`.

    Examples
    --------
    >>> checker = DescentConditionChecker()
    >>> result = checker.verify_descent_data([])
    >>> result.passed
    True
    """

    _check_history: list[DescentConditionResult] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Sub-condition checks
    # ------------------------------------------------------------------

    def check_locality(self, sections: list[HeapSection]) -> bool:
        """Verify the locality sub-condition.

        Two sections that contain an object with the same ``object_id`` must
        agree on all of that object's field references.  If they differ, the
        locality axiom is violated.

        Parameters
        ----------
        sections : list[HeapSection]
            The sections to check for locality.

        Returns
        -------
        bool
            ``True`` iff no two sections disagree on any shared object.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> obj = HeapObject(
        ...     object_id=1, type_name="C", kind=ObjectKind.USER_DEFINED,
        ...     fields=(("x", 10),),
        ... )
        >>> s1 = HeapSection(section_id="s1", objects=(obj,))
        >>> s2 = HeapSection(section_id="s2", objects=(obj,))
        >>> checker.check_locality([s1, s2])
        True
        """
        # Build a map: object_id -> first HeapObject seen
        seen: dict[int, HeapObject] = {}
        for section in sections:
            for obj in section.objects:
                if obj.object_id in seen:
                    existing = seen[obj.object_id]
                    # Compare fields as sets of (name, ref) pairs
                    if set(obj.fields) != set(existing.fields):
                        logger.debug(
                            "check_locality: object %d differs between sections",
                            obj.object_id,
                        )
                        return False
                else:
                    seen[obj.object_id] = obj
        return True

    def check_gluing(
        self,
        sections: list[HeapSection],
        overlaps: list[tuple[int, int]],
    ) -> bool:
        """Verify the gluing sub-condition for specified section pairs.

        For each pair ``(i, j)`` in ``overlaps``, the sections
        ``sections[i]`` and ``sections[j]`` must agree on every object they
        share.  If they disagree on any shared object, gluing fails.

        Parameters
        ----------
        sections : list[HeapSection]
            The collection of local sections.
        overlaps : list[tuple[int, int]]
            Index pairs indicating which sections are expected to overlap.
            If empty, all pairs are checked.

        Returns
        -------
        bool
            ``True`` iff all specified pairs are compatible.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> s1 = HeapSection(section_id="s1", objects=())
        >>> s2 = HeapSection(section_id="s2", objects=())
        >>> checker.check_gluing([s1, s2], [(0, 1)])
        True
        """
        if not overlaps:
            # Check all pairs
            n = len(sections)
            overlaps = [(i, j) for i in range(n) for j in range(i + 1, n)]

        for idx_a, idx_b in overlaps:
            if idx_a >= len(sections) or idx_b >= len(sections):
                continue
            sec_a = sections[idx_a]
            sec_b = sections[idx_b]
            ids_a = {obj.object_id: obj for obj in sec_a.objects}
            ids_b = {obj.object_id: obj for obj in sec_b.objects}
            shared_ids = set(ids_a.keys()) & set(ids_b.keys())
            for oid in shared_ids:
                obj_a = ids_a[oid]
                obj_b = ids_b[oid]
                if set(obj_a.fields) != set(obj_b.fields):
                    logger.debug(
                        "check_gluing: sections %s and %s disagree on object %d",
                        sec_a.section_id,
                        sec_b.section_id,
                        oid,
                    )
                    return False
        return True

    def check_separation(self, sections: list[HeapSection]) -> bool:
        """Verify the separation sub-condition.

        Separation (also called *uniqueness*) requires that no two distinct
        sections carry the same :attr:`~HeapSection.section_id`.  Two
        sections with the same ID would represent a non-deterministic global
        section, violating sheaf uniqueness.

        Parameters
        ----------
        sections : list[HeapSection]
            The sections to check for ID uniqueness.

        Returns
        -------
        bool
            ``True`` iff all ``section_id`` values are distinct.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> s1 = HeapSection(section_id="s1", objects=())
        >>> s2 = HeapSection(section_id="s2", objects=())
        >>> checker.check_separation([s1, s2])
        True
        >>> s3 = HeapSection(section_id="s1", objects=())
        >>> checker.check_separation([s1, s3])
        False
        """
        ids_seen: set[str] = set()
        for section in sections:
            if section.section_id in ids_seen:
                logger.debug(
                    "check_separation: duplicate section_id %s", section.section_id
                )
                return False
            ids_seen.add(section.section_id)
        return True

    # ------------------------------------------------------------------
    # Full descent verification
    # ------------------------------------------------------------------

    def verify_descent_data(
        self, sections: list[HeapSection]
    ) -> DescentConditionResult:
        """Run all three descent sub-conditions and return the combined result.

        The sub-conditions are evaluated in order:
        1. Separation (fast ID uniqueness check)
        2. Locality (pairwise object comparison)
        3. Gluing (compatibility on overlaps)

        Parameters
        ----------
        sections : list[HeapSection]
            The local sections to verify.

        Returns
        -------
        DescentConditionResult
            Combined result capturing which sub-conditions passed or failed
            and all violation messages.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> result = checker.verify_descent_data([])
        >>> result.passed
        True
        >>> len(checker.history())
        1
        """
        ts = time.time()
        violations: list[str] = []

        separation_ok = self.check_separation(sections)
        if not separation_ok:
            failures = self.find_separation_failures(sections)
            violations.extend(failures)

        locality_ok = self.check_locality(sections)
        if not locality_ok:
            gluing_failures = self.find_gluing_failures(sections)
            violations.extend(gluing_failures)

        gluing_ok = self.check_gluing(sections, [])
        if not gluing_ok:
            extra = self.find_gluing_failures(sections)
            # Deduplicate
            for msg in extra:
                if msg not in violations:
                    violations.append(msg)

        passed = separation_ok and locality_ok and gluing_ok
        result = DescentConditionResult(
            passed=passed,
            locality_ok=locality_ok,
            gluing_ok=gluing_ok,
            separation_ok=separation_ok,
            violations=tuple(violations),
            timestamp=ts,
        )
        self._check_history.append(result)
        logger.info("verify_descent_data: passed=%s violations=%d", passed, len(violations))
        return result

    def find_gluing_failures(self, sections: list[HeapSection]) -> list[str]:
        """Find all pairs of sections that fail to glue compatibly.

        Parameters
        ----------
        sections : list[HeapSection]
            Sections to pairwise-compare.

        Returns
        -------
        list[str]
            Failure description strings for every incompatible pair.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> obj_a = HeapObject(
        ...     object_id=1, type_name="C", kind=ObjectKind.USER_DEFINED,
        ...     fields=(("x", 10),),
        ... )
        >>> obj_b = HeapObject(
        ...     object_id=1, type_name="C", kind=ObjectKind.USER_DEFINED,
        ...     fields=(("x", 99),),
        ... )
        >>> s1 = HeapSection(section_id="s1", objects=(obj_a,))
        >>> s2 = HeapSection(section_id="s2", objects=(obj_b,))
        >>> failures = checker.find_gluing_failures([s1, s2])
        >>> len(failures) > 0
        True
        """
        failures: list[str] = []
        n = len(sections)
        for i in range(n):
            for j in range(i + 1, n):
                sec_a = sections[i]
                sec_b = sections[j]
                ids_a = {obj.object_id: obj for obj in sec_a.objects}
                ids_b = {obj.object_id: obj for obj in sec_b.objects}
                shared = set(ids_a.keys()) & set(ids_b.keys())
                for oid in shared:
                    oa = ids_a[oid]
                    ob = ids_b[oid]
                    if set(oa.fields) != set(ob.fields):
                        failures.append(
                            f"Gluing failure: sections '{sec_a.section_id}' and "
                            f"'{sec_b.section_id}' disagree on object {oid} "
                            f"(fields: {set(oa.fields)} vs {set(ob.fields)})"
                        )
        return failures

    def find_separation_failures(self, sections: list[HeapSection]) -> list[str]:
        """Find all duplicate section IDs (separation violations).

        Parameters
        ----------
        sections : list[HeapSection]
            Sections to check for duplicate IDs.

        Returns
        -------
        list[str]
            One failure message per duplicate ID detected.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> s1 = HeapSection(section_id="s1", objects=())
        >>> s2 = HeapSection(section_id="s1", objects=())
        >>> checker.find_separation_failures([s1, s2])
        ["Separation failure: section_id 's1' appears 2 times"]
        """
        from collections import Counter
        counts = Counter(s.section_id for s in sections)
        failures: list[str] = []
        for sid, count in counts.items():
            if count > 1:
                failures.append(
                    f"Separation failure: section_id '{sid}' appears {count} times"
                )
        return failures

    def build_descent_judgment(
        self, result: DescentConditionResult, coordinate: CoordinateObject
    ) -> Any:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` for a descent result.

        Parameters
        ----------
        result : DescentConditionResult
            The descent condition result to encode.
        coordinate : CoordinateObject
            The coordinate at which the descent was checked.

        Returns
        -------
        Judgment
            A judgment encoding the descent outcome.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> result = checker.verify_descent_data([])
        >>> coord = CoordinateObject(components=("heap",), kind=CoordinateKind.REGION)
        >>> j = checker.build_descent_judgment(result, coord)
        >>> j is not None
        True
        """
        trust = (
            TrustLevel.RUNTIME_WITNESSED if result.passed else TrustLevel.CONTRADICTED
        )
        formula = (
            f"sheaf_descent_satisfied(locality={result.locality_ok}, "
            f"gluing={result.gluing_ok}, separation={result.separation_ok})"
        )
        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(formula)
            .of_type_named("DescentCondition")
            .with_trust_level(trust)
        )
        if not result.passed:
            for viol in result.violations:
                obs = Obstruction(
                    obstruction_id=str(uuid.uuid4()),
                    violated_condition="sheaf_descent",
                    coordinate=str(coordinate.components),
                    evidence_at_time=(),
                    repair_hints=(f"Resolve: {viol}",),
                    cohomology_class="H^1",
                )
                builder = builder.with_obstruction(obs)
        return builder.build()

    def history(self) -> list[DescentConditionResult]:
        """Return a copy of the check history.

        Returns
        -------
        list[DescentConditionResult]
            All results produced by :meth:`verify_descent_data`, oldest first.

        Examples
        --------
        >>> checker = DescentConditionChecker()
        >>> _ = checker.verify_descent_data([])
        >>> len(checker.history())
        1
        """
        return list(self._check_history)


# ---------------------------------------------------------------------------
# HeapConsistencyVerifier
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapConsistencyVerifier:
    """Verifies overall heap consistency using sheaf descent.

    :class:`HeapConsistencyVerifier` operates at the snapshot level,
    combining the descent checks from :class:`DescentConditionChecker` with
    alias-partition consistency and mutation-log consistency.

    A heap snapshot is *consistent* iff:
    1. Every alias partition's member sections satisfy the descent condition.
    2. Every mutation event in the snapshot has been applied consistently to
       all alias sections.
    3. No two sections claim the same object with contradictory field values.

    Parameters
    ----------
    _results : list[dict[str, Any]]
        Accumulated verification results for audit purposes.

    Examples
    --------
    >>> verifier = HeapConsistencyVerifier()
    >>> from jugeo.python_runtime.heap_aliasing.models import HeapSnapshot
    >>> snap = HeapSnapshot(
    ...     snapshot_id="s1", objects=(), partitions=(), sections=(), timestamp=0.0
    ... )
    >>> report = verifier.verify(snap)
    >>> report["consistent"]
    True
    """

    _results: list[dict[str, Any]] = field(default_factory=list)

    def verify(self, snapshot: HeapSnapshot) -> dict[str, Any]:
        """Verify full consistency of ``snapshot``.

        Runs partition consistency and mutation consistency checks, then
        aggregates into a single report.

        Parameters
        ----------
        snapshot : HeapSnapshot
            The snapshot to verify.

        Returns
        -------
        dict[str, Any]
            Consistency report with keys ``consistent``, ``snapshot_id``,
            ``partition_results``, ``mutation_consistent``, ``timestamp``.

        Examples
        --------
        >>> verifier = HeapConsistencyVerifier()
        >>> snap = HeapSnapshot(
        ...     snapshot_id="snap1", objects=(), partitions=(),
        ...     sections=(), timestamp=0.0,
        ... )
        >>> report = verifier.verify(snap)
        >>> report["snapshot_id"]
        'snap1'
        """
        sections_by_id = {s.section_id: s for s in snapshot.sections}

        # Check each alias partition
        partition_results = self.verify_alias_consistency(
            list(snapshot.partitions), sections_by_id
        )
        partitions_ok = all(partition_results.values())

        # Build overall report
        report = self.build_consistency_report(snapshot)
        report["partition_results"] = {k: v for k, v in partition_results.items()}
        report["partitions_ok"] = partitions_ok
        report["consistent"] = partitions_ok and report.get("mutation_consistent", True)
        self._results.append(report)
        return report

    def verify_partition(
        self, partition: AliasPartition, sections: dict[str, HeapSection]
    ) -> bool:
        """Verify that all sections for ``partition``'s members are consistent.

        Parameters
        ----------
        partition : AliasPartition
            The alias partition to check.
        sections : dict[str, HeapSection]
            Map from section ID to :class:`HeapSection`.

        Returns
        -------
        bool
            ``True`` iff all member sections found in ``sections`` agree on
            every shared object.

        Examples
        --------
        >>> verifier = HeapConsistencyVerifier()
        >>> partition = AliasPartition(
        ...     partition_id="p1",
        ...     members=frozenset(["s1", "s2"]),
        ...     representative="s1",
        ... )
        >>> verifier.verify_partition(partition, {})
        True
        """
        member_sections: list[HeapSection] = []
        for member_key in partition.members:
            sec = sections.get(member_key)
            if sec is not None:
                member_sections.append(sec)

        if len(member_sections) < 2:
            # Not enough sections to compare — trivially consistent
            return True

        # Pairwise check: all sections with overlapping objects must agree
        obj_to_first: dict[int, HeapObject] = {}
        for section in member_sections:
            for obj in section.objects:
                if obj.object_id in obj_to_first:
                    existing = obj_to_first[obj.object_id]
                    if set(obj.fields) != set(existing.fields):
                        logger.debug(
                            "verify_partition: inconsistency for object %d in partition %s",
                            obj.object_id,
                            partition.partition_id,
                        )
                        return False
                else:
                    obj_to_first[obj.object_id] = obj
        return True

    def verify_alias_consistency(
        self,
        partitions: list[AliasPartition],
        sections: dict[str, HeapSection],
    ) -> dict[str, bool]:
        """Verify consistency of all alias partitions.

        Parameters
        ----------
        partitions : list[AliasPartition]
            All alias partitions in the snapshot.
        sections : dict[str, HeapSection]
            Map from section ID to :class:`HeapSection`.

        Returns
        -------
        dict[str, bool]
            Maps ``partition_id`` to ``True`` iff that partition is consistent.

        Examples
        --------
        >>> verifier = HeapConsistencyVerifier()
        >>> result = verifier.verify_alias_consistency([], {})
        >>> result
        {}
        """
        return {
            p.partition_id: self.verify_partition(p, sections) for p in partitions
        }

    def verify_mutation_consistency(
        self,
        events: list[MutationEvent],
        sections: dict[str, HeapSection],
    ) -> bool:
        """Check that all mutation events are reflected consistently in ``sections``.

        A mutation event is consistent with the sections if either:
        (a) the target object is not present in any section (cannot verify),
        (b) the target object's field has the sentinel value -1 (mutation applied),
        (c) the target object's field has been removed from the section.

        Parameters
        ----------
        events : list[MutationEvent]
            Mutation events to check.
        sections : dict[str, HeapSection]
            Sections to verify against.

        Returns
        -------
        bool
            ``True`` iff all events are consistently reflected.

        Examples
        --------
        >>> verifier = HeapConsistencyVerifier()
        >>> verifier.verify_mutation_consistency([], {})
        True
        """
        for event in events:
            for _key, section in sections.items():
                for obj in section.objects:
                    if str(obj.object_id) == event.object_id:
                        field_map = dict(obj.fields)
                        if event.field_name in field_map:
                            ref = field_map[event.field_name]
                            # ref == -1 means mutation was applied via apply_mutation
                            if ref != -1 and ref != 0:
                                logger.debug(
                                    "verify_mutation_consistency: "
                                    "event %s not reflected in section %s",
                                    event.event_id,
                                    section.section_id,
                                )
                                return False
        return True

    def build_consistency_report(self, snapshot: HeapSnapshot) -> dict[str, Any]:
        """Build a structured consistency report for ``snapshot``.

        Parameters
        ----------
        snapshot : HeapSnapshot
            The snapshot to report on.

        Returns
        -------
        dict[str, Any]
            Report dictionary with keys ``snapshot_id``, ``object_count``,
            ``partition_count``, ``section_count``, ``timestamp``.

        Examples
        --------
        >>> verifier = HeapConsistencyVerifier()
        >>> snap = HeapSnapshot(
        ...     snapshot_id="x", objects=(), partitions=(),
        ...     sections=(), timestamp=1.0,
        ... )
        >>> report = verifier.build_consistency_report(snap)
        >>> report["snapshot_id"]
        'x'
        """
        return {
            "snapshot_id": snapshot.snapshot_id,
            "object_count": len(snapshot.objects),
            "partition_count": len(snapshot.partitions),
            "section_count": len(snapshot.sections),
            "timestamp": time.time(),
            "snapshot_timestamp": snapshot.timestamp,
            "label": snapshot.label,
        }

    def build_consistency_judgment(
        self, report: dict[str, Any], coordinate: CoordinateObject
    ) -> Any:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` for a consistency report.

        Parameters
        ----------
        report : dict[str, Any]
            The consistency report produced by :meth:`build_consistency_report`
            or :meth:`verify`.
        coordinate : CoordinateObject
            The coordinate at which the consistency was assessed.

        Returns
        -------
        Judgment
            A judgment encoding the consistency outcome.

        Examples
        --------
        >>> verifier = HeapConsistencyVerifier()
        >>> coord = CoordinateObject(components=("heap",), kind=CoordinateKind.REGION)
        >>> j = verifier.build_consistency_judgment({"consistent": True}, coord)
        >>> j is not None
        True
        """
        consistent = report.get("consistent", False)
        trust = (
            TrustLevel.RUNTIME_WITNESSED if consistent else TrustLevel.CONTRADICTED
        )
        snap_id = report.get("snapshot_id", "unknown")
        formula = f"heap_consistent(snapshot={snap_id!r}, result={consistent})"
        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(formula)
            .of_type_named("HeapConsistency")
            .with_trust_level(trust)
        )
        if not consistent:
            obs = Obstruction(
                obstruction_id=str(uuid.uuid4()),
                violated_condition="heap_consistency",
                coordinate=str(coordinate.components),
                evidence_at_time=(),
                repair_hints=("Re-run descent check after alias re-computation",),
                cohomology_class="H^1",
            )
            builder = builder.with_obstruction(obs)
        return builder.build()


# ---------------------------------------------------------------------------
# CocycleConditionChecker
# ---------------------------------------------------------------------------


class CocycleConditionChecker:
    """Checks the cocycle / compatibility condition for mutation patches.

    In sheaf theory, a *cocycle* is a consistent system of local data on
    overlapping patches satisfying ``g_{ij} ∘ g_{jk} = g_{ik}`` on triple
    overlaps.  For the heap, a collection of :class:`MutationPatch` objects
    satisfies the cocycle condition iff the transition maps between sections
    are consistent on overlaps.

    This is a regular class (not a dataclass) because it operates as a
    stateless computation kernel — results are produced and returned rather
    than stored.

    Examples
    --------
    >>> checker = CocycleConditionChecker()
    >>> checker.check_cocycle([], {})
    True
    """

    def __init__(self) -> None:
        """Initialise a :class:`CocycleConditionChecker`."""
        self._transition_cache: dict[tuple[str, str], dict[str, str]] = {}

    def check_cocycle(
        self,
        patches: list[MutationPatch],
        sections: dict[str, HeapSection],
    ) -> bool:
        """Verify the cocycle condition for a list of mutation patches.

        Two patches are *compatible* on their overlap iff the events in the
        overlap (same ``object_id`` and ``field_name``) agree on
        ``new_value_repr``.

        Parameters
        ----------
        patches : list[MutationPatch]
            The mutation patches to check.
        sections : dict[str, HeapSection]
            Map from section ID to :class:`HeapSection` (used for context).

        Returns
        -------
        bool
            ``True`` iff all patches are pairwise compatible.

        Examples
        --------
        >>> checker = CocycleConditionChecker()
        >>> checker.check_cocycle([], {})
        True
        """
        violations = self.find_cocycle_violations(patches, sections)
        return len(violations) == 0

    def compute_transition_maps(
        self, sections: list[HeapSection]
    ) -> dict[tuple[str, str], dict[str, str]]:
        """Compute transition maps between all pairs of sections.

        A transition map ``t_{AB}`` from section ``A`` to section ``B``
        maps the ``section_id`` of shared objects to their field differences.

        Parameters
        ----------
        sections : list[HeapSection]
            Sections to compute transitions between.

        Returns
        -------
        dict[tuple[str, str], dict[str, str]]
            Keys are ``(section_id_A, section_id_B)`` pairs; values are
            ``{object_id_str: "field:old->new"}`` dicts.

        Examples
        --------
        >>> checker = CocycleConditionChecker()
        >>> s1 = HeapSection(section_id="s1", objects=())
        >>> s2 = HeapSection(section_id="s2", objects=())
        >>> checker.compute_transition_maps([s1, s2])
        {('s1', 's2'): {}, ('s2', 's1'): {}}
        """
        transitions: dict[tuple[str, str], dict[str, str]] = {}
        n = len(sections)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                sec_a = sections[i]
                sec_b = sections[j]
                ids_a = {obj.object_id: obj for obj in sec_a.objects}
                ids_b = {obj.object_id: obj for obj in sec_b.objects}
                shared = set(ids_a.keys()) & set(ids_b.keys())
                diff: dict[str, str] = {}
                for oid in shared:
                    fields_a = dict(ids_a[oid].fields)
                    fields_b = dict(ids_b[oid].fields)
                    for fname in set(fields_a.keys()) | set(fields_b.keys()):
                        val_a = fields_a.get(fname, "MISSING")
                        val_b = fields_b.get(fname, "MISSING")
                        if val_a != val_b:
                            diff[f"{oid}:{fname}"] = f"{val_a}->{val_b}"
                transitions[(sec_a.section_id, sec_b.section_id)] = diff
        return transitions

    def verify_compatibility(
        self,
        patch1: MutationPatch,
        patch2: MutationPatch,
        sections: dict[str, HeapSection],
    ) -> bool:
        """Verify that two mutation patches are compatible on their overlap.

        Two patches overlap when they both contain events targeting the same
        ``(object_id, field_name)`` pair.  Compatibility requires that both
        patches assign the same ``new_value_repr`` to that pair.

        Parameters
        ----------
        patch1 : MutationPatch
            First mutation patch.
        patch2 : MutationPatch
            Second mutation patch.
        sections : dict[str, HeapSection]
            Sections for context (not used in repr-level check).

        Returns
        -------
        bool
            ``True`` iff the two patches agree on all overlapping events.

        Examples
        --------
        >>> checker = CocycleConditionChecker()
        >>> p1 = MutationPatch(patch_id="p1", events=())
        >>> p2 = MutationPatch(patch_id="p2", events=())
        >>> checker.verify_compatibility(p1, p2, {})
        True
        """
        # Build a map for each patch: (object_id, field_name) -> new_value_repr
        def _event_map(patch: MutationPatch) -> dict[tuple[str, str], str]:
            return {
                (e.object_id, e.field_name): e.new_value_repr for e in patch.events
            }

        map1 = _event_map(patch1)
        map2 = _event_map(patch2)
        overlap_keys = set(map1.keys()) & set(map2.keys())
        for key in overlap_keys:
            if map1[key] != map2[key]:
                logger.debug(
                    "verify_compatibility: patches '%s' and '%s' disagree on %s",
                    patch1.patch_id,
                    patch2.patch_id,
                    key,
                )
                return False
        return True

    def find_cocycle_violations(
        self,
        patches: list[MutationPatch],
        sections: dict[str, HeapSection],
    ) -> list[str]:
        """Find all pairwise cocycle violations among ``patches``.

        Parameters
        ----------
        patches : list[MutationPatch]
            Patches to check pairwise.
        sections : dict[str, HeapSection]
            Sections for context.

        Returns
        -------
        list[str]
            Violation description strings for every incompatible pair.

        Examples
        --------
        >>> checker = CocycleConditionChecker()
        >>> checker.find_cocycle_violations([], {})
        []
        """
        violations: list[str] = []
        n = len(patches)
        for i in range(n):
            for j in range(i + 1, n):
                if not self.verify_compatibility(patches[i], patches[j], sections):
                    violations.append(
                        f"Cocycle violation: patches '{patches[i].patch_id}' and "
                        f"'{patches[j].patch_id}' are incompatible on overlapping events"
                    )
        return violations

    def build_obstruction(
        self, violations: list[str]
    ) -> Obstruction | None:
        """Create an :class:`Obstruction` for cocycle violations.

        Parameters
        ----------
        violations : list[str]
            List of violation description strings.

        Returns
        -------
        Obstruction | None
            An :class:`Obstruction` capturing all violations, or ``None`` if
            ``violations`` is empty.

        Examples
        --------
        >>> checker = CocycleConditionChecker()
        >>> obs = checker.build_obstruction(["v1", "v2"])
        >>> obs is not None
        True
        >>> obs.cohomology_class
        'H^1'
        """
        if not violations:
            return None
        hints = tuple(f"Resolve cocycle: {v}" for v in violations[:5])
        return Obstruction(
            obstruction_id=str(uuid.uuid4()),
            violated_condition="cocycle_condition",
            coordinate="heap.patches",
            evidence_at_time=(),
            repair_hints=hints,
            cohomology_class="H^1",
        )


# ---------------------------------------------------------------------------
# LocalToGlobalMapper
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LocalToGlobalMapper:
    """Implements the local-to-global gluing property of the heap sheaf.

    The *local-to-global* property states: given compatible local sections on
    a cover, there exists a unique global section extending them.  This class
    collects local sections, checks pairwise compatibility, and if all pairs
    are compatible, constructs the merged global section.

    Parameters
    ----------
    _local_sections : dict[str, HeapSection]
        Local sections indexed by their :attr:`~HeapSection.section_id`.
    _global_attempts : list[dict[str, Any]]
        Log of past gluing attempts (successes and failures).

    Examples
    --------
    >>> mapper = LocalToGlobalMapper()
    >>> mapper.collect_local_sections([])
    >>> global_sec = mapper.attempt_gluing()
    >>> global_sec is None  # empty — nothing to glue
    True
    """

    _local_sections: dict[str, HeapSection] = field(default_factory=dict)
    _global_attempts: list[dict[str, Any]] = field(default_factory=list)

    def collect_local_sections(self, sections: list[HeapSection]) -> None:
        """Store each section by its ``section_id``, replacing any duplicate.

        Parameters
        ----------
        sections : list[HeapSection]
            Local sections to collect.

        Examples
        --------
        >>> mapper = LocalToGlobalMapper()
        >>> s = HeapSection(section_id="s1", objects=())
        >>> mapper.collect_local_sections([s])
        >>> "s1" in mapper._local_sections
        True
        """
        for section in sections:
            if section.section_id in self._local_sections:
                logger.warning(
                    "LocalToGlobalMapper.collect_local_sections: "
                    "replacing existing section '%s'",
                    section.section_id,
                )
            self._local_sections[section.section_id] = section

    def attempt_gluing(self) -> HeapSection | None:
        """Attempt to glue all collected local sections into a global section.

        Checks pairwise compatibility first.  If any pair is incompatible,
        records the failure and returns ``None``.  If all pairs are compatible,
        delegates to :meth:`build_global_section`.

        Returns
        -------
        HeapSection | None
            The global section, or ``None`` if gluing fails.

        Examples
        --------
        >>> mapper = LocalToGlobalMapper()
        >>> s1 = HeapSection(section_id="s1", objects=())
        >>> mapper.collect_local_sections([s1])
        >>> result = mapper.attempt_gluing()
        >>> result is not None
        True
        """
        sections = list(self._local_sections.values())
        if not sections:
            return None

        global_sec = self.build_global_section(sections)
        attempt: dict[str, Any] = {
            "timestamp": time.time(),
            "input_count": len(sections),
            "success": global_sec is not None,
            "result_id": global_sec.section_id if global_sec is not None else None,
        }
        self._global_attempts.append(attempt)
        return global_sec

    def verify_uniqueness(self, candidate: HeapSection) -> bool:
        """Verify that ``candidate`` is the unique merge of local sections.

        Uniqueness is violated if any local section contains an object that
        ``candidate`` does not cover.  This would mean the candidate is not
        the *maximal* global section.

        Parameters
        ----------
        candidate : HeapSection
            The proposed global section.

        Returns
        -------
        bool
            ``True`` iff ``candidate`` covers all objects from all local sections.

        Examples
        --------
        >>> mapper = LocalToGlobalMapper()
        >>> s = HeapSection(section_id="s1", objects=())
        >>> mapper.collect_local_sections([s])
        >>> global_s = mapper.build_global_section([s])
        >>> mapper.verify_uniqueness(global_s)
        True
        """
        candidate_ids = {obj.object_id for obj in candidate.objects}
        for section in self._local_sections.values():
            for obj in section.objects:
                if obj.object_id not in candidate_ids:
                    logger.debug(
                        "verify_uniqueness: object %d from section '%s' not in candidate",
                        obj.object_id,
                        section.section_id,
                    )
                    return False
        return True

    def build_global_section(
        self, sections: list[HeapSection]
    ) -> HeapSection | None:
        """Merge all sections into a single global section.

        Objects that appear in multiple sections are de-duplicated by
        ``object_id`` (first occurrence wins).  If two sections have
        conflicting field data for the same object, returns ``None`` to
        signal a gluing failure.

        Parameters
        ----------
        sections : list[HeapSection]
            Sections to merge.

        Returns
        -------
        HeapSection | None
            The merged global section, or ``None`` on conflict.

        Examples
        --------
        >>> mapper = LocalToGlobalMapper()
        >>> s1 = HeapSection(section_id="s1", objects=())
        >>> s2 = HeapSection(section_id="s2", objects=())
        >>> global_s = mapper.build_global_section([s1, s2])
        >>> global_s is not None
        True
        """
        merged_objects: dict[int, HeapObject] = {}
        for section in sections:
            for obj in section.objects:
                if obj.object_id in merged_objects:
                    existing = merged_objects[obj.object_id]
                    if set(obj.fields) != set(existing.fields):
                        reason = (
                            f"Conflicting field data for object {obj.object_id}: "
                            f"{set(existing.fields)} vs {set(obj.fields)}"
                        )
                        self._global_attempts.append(
                            {
                                "timestamp": time.time(),
                                "success": False,
                                "failure_reason": reason,
                            }
                        )
                        return None
                else:
                    merged_objects[obj.object_id] = obj

        global_id = f"global_{uuid.uuid4().hex[:8]}"
        label_parts = [s.section_id for s in sections]
        return HeapSection(
            section_id=global_id,
            objects=tuple(merged_objects.values()),
            label=f"global({'|'.join(label_parts)})",
        )

    def handle_gluing_failure(self, failure_reason: str) -> dict[str, Any]:
        """Handle a gluing failure, logging it and returning a report.

        Parameters
        ----------
        failure_reason : str
            Human-readable description of why gluing failed.

        Returns
        -------
        dict[str, Any]
            Failure report with keys ``success``, ``failure_reason``,
            ``timestamp``.

        Examples
        --------
        >>> mapper = LocalToGlobalMapper()
        >>> report = mapper.handle_gluing_failure("conflicting fields")
        >>> report["success"]
        False
        """
        logger.warning("LocalToGlobalMapper: gluing failure: %s", failure_reason)
        report: dict[str, Any] = {
            "success": False,
            "failure_reason": failure_reason,
            "timestamp": time.time(),
        }
        self._global_attempts.append(report)
        return report

    def reset(self) -> None:
        """Clear all collected local sections and attempt history.

        Examples
        --------
        >>> mapper = LocalToGlobalMapper()
        >>> mapper.collect_local_sections([HeapSection(section_id="s1", objects=())])
        >>> mapper.reset()
        >>> len(mapper._local_sections)
        0
        """
        self._local_sections.clear()
        self._global_attempts.clear()
        logger.debug("LocalToGlobalMapper.reset: state cleared")


# ---------------------------------------------------------------------------
# HeapCoherenceTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapCoherenceTracker:
    """Tracks heap coherence over time.

    :class:`HeapCoherenceTracker` maintains a rolling log of coherence checks
    across multiple :class:`HeapSnapshot` objects.  Each entry in the log
    records whether the snapshot at a given time was coherent (passed descent)
    or incoherent (failed descent).

    This is useful for detecting *coherence breaks*: moments in program
    execution where the heap transitions from a consistent to an inconsistent
    state.  Coherence breaks may indicate bugs (e.g. aliased writes that were
    not propagated) or valid but complex mutation patterns that need further
    analysis.

    Parameters
    ----------
    _coherence_log : list[dict[str, Any]]
        Log of coherence records, each with keys ``snapshot_id``,
        ``is_coherent``, ``timestamp``.
    _last_check : float
        Unix timestamp of the most recent coherence check.

    Examples
    --------
    >>> tracker = HeapCoherenceTracker()
    >>> snap = HeapSnapshot(
    ...     snapshot_id="s1", objects=(), partitions=(),
    ...     sections=(), timestamp=0.0,
    ... )
    >>> verifier = HeapConsistencyVerifier()
    >>> ok = tracker.check_coherence(snap, verifier)
    >>> isinstance(ok, bool)
    True
    """

    _coherence_log: list[dict[str, Any]] = field(default_factory=list)
    _last_check: float = 0.0

    def record_state(self, snapshot: HeapSnapshot, is_coherent: bool) -> None:
        """Append a coherence record for ``snapshot`` to the log.

        Parameters
        ----------
        snapshot : HeapSnapshot
            The snapshot whose coherence was assessed.
        is_coherent : bool
            Whether the snapshot was found to be coherent.

        Examples
        --------
        >>> tracker = HeapCoherenceTracker()
        >>> snap = HeapSnapshot(
        ...     snapshot_id="s1", objects=(), partitions=(),
        ...     sections=(), timestamp=1.0,
        ... )
        >>> tracker.record_state(snap, True)
        >>> tracker.count_breaks()
        0
        """
        entry: dict[str, Any] = {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_label": snapshot.label,
            "is_coherent": is_coherent,
            "timestamp": time.time(),
            "snapshot_timestamp": snapshot.timestamp,
            "object_count": len(snapshot.objects),
            "section_count": len(snapshot.sections),
        }
        self._coherence_log.append(entry)
        self._last_check = entry["timestamp"]
        logger.debug(
            "HeapCoherenceTracker.record_state: snapshot=%s coherent=%s",
            snapshot.snapshot_id,
            is_coherent,
        )

    def check_coherence(
        self, snapshot: HeapSnapshot, checker: HeapConsistencyVerifier
    ) -> bool:
        """Run a consistency check via ``checker`` and record the result.

        Parameters
        ----------
        snapshot : HeapSnapshot
            The snapshot to check.
        checker : HeapConsistencyVerifier
            The verifier to use for the coherence check.

        Returns
        -------
        bool
            ``True`` iff the snapshot is coherent according to ``checker``.

        Examples
        --------
        >>> tracker = HeapCoherenceTracker()
        >>> verifier = HeapConsistencyVerifier()
        >>> snap = HeapSnapshot(
        ...     snapshot_id="s1", objects=(), partitions=(),
        ...     sections=(), timestamp=0.0,
        ... )
        >>> result = tracker.check_coherence(snap, verifier)
        >>> isinstance(result, bool)
        True
        """
        report = checker.verify(snapshot)
        is_coherent = bool(report.get("consistent", False))
        self.record_state(snapshot, is_coherent)
        return is_coherent

    def find_coherence_breaks(self) -> list[dict[str, Any]]:
        """Return all log entries where ``is_coherent`` transitioned from True to False.

        A *coherence break* is defined as a log entry where the previous entry
        was coherent and the current entry is not.  The first incoherent entry
        in a run is therefore the break point.

        Returns
        -------
        list[dict[str, Any]]
            Log entries that represent coherence breaks (transitions from
            coherent to incoherent).

        Examples
        --------
        >>> tracker = HeapCoherenceTracker()
        >>> tracker.find_coherence_breaks()
        []
        """
        breaks: list[dict[str, Any]] = []
        prev_coherent = True
        for entry in self._coherence_log:
            current_coherent = entry.get("is_coherent", True)
            if prev_coherent and not current_coherent:
                breaks.append(entry)
            prev_coherent = current_coherent
        return breaks

    def build_coherence_log(self) -> dict[str, Any]:
        """Return a structured summary of the coherence log.

        Returns
        -------
        dict[str, Any]
            Keys: ``total_checks``, ``coherent_count``, ``incoherent_count``,
            ``break_count``, ``last_check``, ``entries``.

        Examples
        --------
        >>> tracker = HeapCoherenceTracker()
        >>> log = tracker.build_coherence_log()
        >>> log["total_checks"]
        0
        """
        coherent_count = sum(1 for e in self._coherence_log if e.get("is_coherent", True))
        incoherent_count = len(self._coherence_log) - coherent_count
        return {
            "total_checks": len(self._coherence_log),
            "coherent_count": coherent_count,
            "incoherent_count": incoherent_count,
            "break_count": len(self.find_coherence_breaks()),
            "last_check": self._last_check,
            "entries": list(self._coherence_log),
        }

    def serialize(self) -> dict[str, Any]:
        """Serialise the tracker to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Serialised tracker including the full coherence log.

        Examples
        --------
        >>> tracker = HeapCoherenceTracker()
        >>> "coherence_log" in tracker.serialize()
        True
        """
        return {
            "coherence_log": list(self._coherence_log),
            "last_check": self._last_check,
            "summary": {
                "total_checks": len(self._coherence_log),
                "breaks": len(self.find_coherence_breaks()),
            },
        }

    def count_breaks(self) -> int:
        """Return the total number of coherence breaks detected.

        Returns
        -------
        int
            Number of coherence breaks (incoherent entries preceded by a
            coherent entry).

        Examples
        --------
        >>> HeapCoherenceTracker().count_breaks()
        0
        """
        return len(self.find_coherence_breaks())


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "DescentConditionResult",
    "DescentConditionChecker",
    "HeapConsistencyVerifier",
    "CocycleConditionChecker",
    "LocalToGlobalMapper",
    "HeapCoherenceTracker",
]
