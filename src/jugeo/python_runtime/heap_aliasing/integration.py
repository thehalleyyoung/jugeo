"""Heap aliasing integration layer for the JuGeo framework.

This module implements the integration bridge between the Python runtime heap
aliasing analysis subsystem and the broader JuGeo judgment / geometry pipeline,
as described in *theory2.tex Ch17 — JuGeo Framework Integration*.

The module provides five primary classes:

* :class:`HeapJudgmentEmitter` — translates heap observations into first-class
  :class:`~jugeo.judgments.judgment_terms.Judgment` objects that can participate
  in the global judgment lattice.
* :class:`Z3HeapEncoder` — encodes heap invariants as SMT-LIB2 formulae and
  submits them to :class:`~jugeo.solver.z3_session.Z3Session` for verification.
* :class:`HeapCoordinateMapper` — assigns canonical
  :class:`~jugeo.geometry.site.Coordinate` objects to heap objects, fields,
  alias classes, and mutation sites.
* :class:`SupportRegionBuilder` — constructs
  :class:`~jugeo.geometry.supports.SupportRegion` objects describing which parts
  of the heap a given judgment depends upon.
* :class:`CopilotHeapAdvisor` — provides copilot integration for AI-powered
  advisory messages about aliasing, immutability, and copy semantics.

Design notes
------------
All mutable state containers use ``@dataclass(slots=True)`` for deterministic
memory layout and fast attribute access.  No ``Optional[X]`` spelling is used;
the codebase adopts the PEP 604 ``X | None`` union form throughout.

The module is intentionally self-contained: every class carries enough context
in its own fields to be instantiated and used without global state.

References
----------
* theory2.tex Ch17 — JuGeo Framework Integration
* copilot integration design document (internal)

See Also
--------
jugeo.python_runtime.heap_aliasing.models : Core data models used here.
jugeo.judgments.judgment_terms : Judgment building blocks.
jugeo.solver.z3_session : SMT solver session abstraction.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateObject,
)
from jugeo.geometry.supports import SupportRegion, SupportSet
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentBuilder,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.solver.z3_session import SolveOutcome, Z3Formula, Z3Session, z3_available
from jugeo.python_runtime.heap_aliasing.models import (
    AliasEdge,
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
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_HEAP_COORD = Coordinate(
    components=("heap", "aliasing", "default"),
    kind=CoordinateKind.REGION,
)


def _make_coord(*components: str) -> CoordinateObject:
    """Return a :class:`Coordinate` for the given component path.

    Parameters
    ----------
    *components:
        Variable-length sequence of string path segments forming the coordinate
        component tuple.

    Returns
    -------
    CoordinateObject
        A new ``Coordinate`` with ``kind=CoordinateKind.REGION``.
    """
    return Coordinate(components=tuple(components), kind=CoordinateKind.REGION)


# ---------------------------------------------------------------------------
# HeapJudgmentEmitter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapJudgmentEmitter:
    """Translates heap aliasing observations into JuGeo :class:`Judgment` objects.

    Each heap observation — an identity coordinate, an alias partition, a
    mutation event, or a sheaf-descent check — is converted to a fully
    attributed ``Judgment`` carrying trust annotations, evidence items, and
    optional obstructions.  Emitted judgments accumulate in ``_emitted`` and
    can be retrieved via :meth:`all_emitted`.

    Parameters
    ----------
    _emitted:
        Mutable list of judgments produced since the last :meth:`clear` call.
    _source_tag:
        Short string tag recorded on every :class:`EvidenceItem` produced by
        this emitter (default ``"heap_aliasing"``).

    Examples
    --------
    >>> emitter = HeapJudgmentEmitter()
    >>> identity = IdentityCoordinate(object_id=42, type_name="MyClass")
    >>> j = emitter.emit_identity_judgment(identity)
    >>> j.status == JudgmentStatus.VALID
    True
    """

    _emitted: list[Judgment] = field(default_factory=list)
    _source_tag: str = "heap_aliasing"

    # ------------------------------------------------------------------
    # Public emit methods
    # ------------------------------------------------------------------

    def emit_identity_judgment(self, identity: IdentityCoordinate) -> Judgment:
        """Build and emit a :class:`Judgment` asserting that an identity coordinate is unique.

        The judgment encodes the invariant that each ``IdentityCoordinate`` in
        the heap refers to exactly one live object whose identity cannot be
        aliased at the Python runtime level.

        Parameters
        ----------
        identity:
            The identity coordinate representing a specific heap object.

        Returns
        -------
        Judgment
            A fully constructed judgment with ``RUNTIME_WITNESSED`` trust,
            appended to ``self._emitted``.

        Raises
        ------
        ValueError
            If ``identity.object_id`` is negative.

        Examples
        --------
        >>> emitter = HeapJudgmentEmitter()
        >>> identity = IdentityCoordinate(object_id=7, type_name="str")
        >>> j = emitter.emit_identity_judgment(identity)
        >>> "identity_unique" in j.proposition.formula
        True
        """
        if identity.object_id < 0:
            raise ValueError(
                f"object_id must be non-negative; got {identity.object_id}"
            )

        coord = _make_coord("heap", "identity", str(identity.object_id))
        evidence = self.build_evidence(
            source="identity_check",
            payload=f"object_id={identity.object_id} type={identity.type_name}",
        )
        provenance = self.build_provenance([])

        judgment = (
            JudgmentBuilder()
            .at(coord)
            .claiming_formula(
                f"identity_unique: id={identity.object_id} type={identity.type_name}",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("IdentityUniqueness")
            .with_trust(TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED))
            .from_source(ProvenanceSource.RUNTIME)
            .with_evidence(evidence)
            .build()
        )

        self._emitted.append(judgment)
        logger.debug(
            "Emitted identity judgment for object_id=%d type=%s",
            identity.object_id,
            identity.type_name,
        )
        return judgment

    def emit_alias_judgment(
        self,
        partition: AliasPartition,
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Build and emit a :class:`Judgment` describing an alias partition.

        Records the size of the alias class, the representative member, and
        attaches an evidence item whose payload enumerates all members.

        Parameters
        ----------
        partition:
            The alias partition to describe.
        coordinate:
            The coordinate at which this alias judgment is located.

        Returns
        -------
        Judgment
            A fully constructed alias judgment appended to ``self._emitted``.

        Raises
        ------
        ValueError
            If ``partition`` has no members.

        Examples
        --------
        >>> emitter = HeapJudgmentEmitter()
        >>> p = AliasPartition(partition_id="p0", representative="42",
        ...                    members=frozenset({"42", "43"}))
        >>> coord = _make_coord("heap", "alias", "p0")
        >>> j = emitter.emit_alias_judgment(p, coord)
        >>> "alias_class" in j.proposition.formula
        True
        """
        if not partition.members:
            raise ValueError("AliasPartition must have at least one member.")

        member_list = sorted(partition.members)
        evidence = self.build_evidence(
            source="alias_analysis",
            payload=f"members={member_list} rep={partition.representative}",
        )
        provenance = self.build_provenance([])

        judgment = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(
                f"alias_class: size={partition.size()} rep={partition.representative}",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("AliasPartition")
            .with_trust(TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED))
            .from_source(ProvenanceSource.RUNTIME)
            .with_evidence(evidence)
            .build()
        )

        self._emitted.append(judgment)
        logger.debug(
            "Emitted alias judgment for partition_id=%s size=%d",
            partition.partition_id,
            partition.size(),
        )
        return judgment

    def emit_mutation_judgment(
        self,
        event: MutationEvent,
        is_valid: bool,
    ) -> Judgment:
        """Build and emit a :class:`Judgment` about a heap mutation event.

        If the mutation is invalid, an :class:`Obstruction` is attached and the
        judgment status is set to ``OBSTRUCTED``.

        Parameters
        ----------
        event:
            The mutation event to analyse.
        is_valid:
            Whether the mutation passes all invariant checks.

        Returns
        -------
        Judgment
            A judgment reflecting the validity of the mutation, appended to
            ``self._emitted``.

        Examples
        --------
        >>> emitter = HeapJudgmentEmitter()
        >>> ev = MutationEvent(object_id="10", field_name="x", old_value=0, new_value=1)
        >>> j = emitter.emit_mutation_judgment(ev, is_valid=True)
        >>> j.status == JudgmentStatus.VALID
        True
        """
        coord = _make_coord("heap", "mutation", event.object_id, event.field_name)
        formula = (
            f"mutation_valid={is_valid}: {event.object_id}.{event.field_name}"
        )
        evidence = self.build_evidence(
            source="mutation_check",
            payload=(
                f"field={event.field_name} object={event.object_id} valid={is_valid}"
            ),
        )

        builder = (
            JudgmentBuilder()
            .at(coord)
            .claiming_formula(formula, kind=PropositionKind.STRUCTURAL)
            .of_type_named("MutationValidity")
            .with_trust(TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED))
            .from_source(ProvenanceSource.RUNTIME)
            .with_evidence(evidence)
        )

        if not is_valid:
            obstruction = Obstruction(
                obstruction_id=str(uuid.uuid4()),
                violated_condition=(
                    f"mutation to {event.object_id}.{event.field_name} "
                    "violates heap invariant"
                ),
                coordinate=str(coord),
                repair_hints=(
                    "Ensure the object is not frozen before mutation.",
                    "Check that no aliased reference expects the old value.",
                ),
                cohomology_class="H^1",
            )
            builder = builder.with_obstruction(obstruction).with_status(
                JudgmentStatus.OBSTRUCTED
            )

        judgment = builder.build()
        self._emitted.append(judgment)
        logger.debug(
            "Emitted mutation judgment for %s.%s valid=%s",
            event.object_id,
            event.field_name,
            is_valid,
        )
        return judgment

    def emit_descent_judgment(
        self,
        is_descent_ok: bool,
        violations: list[str],
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Build and emit a :class:`Judgment` about the sheaf descent condition.

        The sheaf descent condition requires that local section data on overlapping
        alias regions glue consistently to a global section.  Each element of
        *violations* describes one failed gluing and is encoded as a separate
        :class:`Obstruction`.

        Parameters
        ----------
        is_descent_ok:
            ``True`` if all local sections agree on overlaps.
        violations:
            List of human-readable violation descriptions.  May be empty.
        coordinate:
            The coordinate at which the descent check is located.

        Returns
        -------
        Judgment
            A descent judgment, possibly with obstructions, appended to
            ``self._emitted``.

        Examples
        --------
        >>> emitter = HeapJudgmentEmitter()
        >>> coord = _make_coord("heap", "descent", "root")
        >>> j = emitter.emit_descent_judgment(True, [], coord)
        >>> j.status == JudgmentStatus.VALID
        True
        """
        formula = (
            f"sheaf_descent_ok={is_descent_ok}, violations={len(violations)}"
        )
        evidence = self.build_evidence(
            source="descent_check",
            payload=f"violations={violations}",
        )

        builder = (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(formula, kind=PropositionKind.COHOMOLOGICAL)
            .of_type_named("SheafDescent")
            .with_trust(TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED))
            .from_source(ProvenanceSource.RUNTIME)
            .with_evidence(evidence)
        )

        for i, violation in enumerate(violations):
            obs = Obstruction(
                obstruction_id=str(uuid.uuid4()),
                violated_condition=violation,
                coordinate=str(coordinate),
                repair_hints=(
                    f"Resolve overlap inconsistency #{i + 1}.",
                    "Check that alias partitions form a valid cover.",
                ),
                cohomology_class="H^1",
            )
            builder = builder.with_obstruction(obs)

        if violations:
            builder = builder.with_status(JudgmentStatus.OBSTRUCTED)

        judgment = builder.build()
        self._emitted.append(judgment)
        logger.debug(
            "Emitted descent judgment ok=%s violations=%d",
            is_descent_ok,
            len(violations),
        )
        return judgment

    def batch_emit(self, items: list[tuple[str, Any]]) -> list[Judgment]:
        """Emit judgments for a heterogeneous list of tagged heap objects.

        Parameters
        ----------
        items:
            A list of ``(type_tag, object)`` pairs.  Supported type tags are
            ``"identity"`` (:class:`IdentityCoordinate`), ``"alias"``
            (:class:`AliasPartition`), ``"mutation"`` (:class:`MutationEvent`),
            and ``"descent"`` (``dict`` with keys ``"is_ok"`` and
            ``"violations"``).

        Returns
        -------
        list[Judgment]
            The list of newly emitted judgments (one per item).

        Raises
        ------
        ValueError
            If an unrecognised *type_tag* is encountered.

        Examples
        --------
        >>> emitter = HeapJudgmentEmitter()
        >>> identity = IdentityCoordinate(object_id=1, type_name="int")
        >>> results = emitter.batch_emit([("identity", identity)])
        >>> len(results) == 1
        True
        """
        results: list[Judgment] = []
        default_coord = _DEFAULT_HEAP_COORD

        for type_tag, obj in items:
            if type_tag == "identity":
                results.append(self.emit_identity_judgment(obj))
            elif type_tag == "alias":
                coord = _make_coord("heap", "alias", obj.partition_id)
                results.append(self.emit_alias_judgment(obj, coord))
            elif type_tag == "mutation":
                results.append(self.emit_mutation_judgment(obj, True))
            elif type_tag == "descent":
                is_ok = obj.get("is_ok", True) if isinstance(obj, dict) else True
                violations = (
                    obj.get("violations", []) if isinstance(obj, dict) else []
                )
                results.append(
                    self.emit_descent_judgment(is_ok, violations, default_coord)
                )
            else:
                raise ValueError(
                    f"Unknown type_tag {type_tag!r}; expected one of "
                    "'identity', 'alias', 'mutation', 'descent'."
                )

        return results

    # ------------------------------------------------------------------
    # Helper constructors
    # ------------------------------------------------------------------

    def build_evidence(self, source: str, payload: str) -> EvidenceItem:
        """Construct a runtime-witnessed :class:`EvidenceItem`.

        Parameters
        ----------
        source:
            A short identifier for the analysis that produced the evidence.
        payload:
            A human-readable description of the evidence payload.

        Returns
        -------
        EvidenceItem
            An evidence item with ``kind=RUNTIME_WITNESS`` and the given
            payload dict.
        """
        return EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"source": source, "detail": payload},
            trust_level=TrustLevel.RUNTIME_WITNESSED,
            channel=self._source_tag,
        )

    def build_provenance(self, parent_ids: list[str]) -> Provenance:
        """Construct a :class:`Provenance` object for a runtime-derived judgment.

        Parameters
        ----------
        parent_ids:
            List of judgment IDs that serve as logical antecedents.

        Returns
        -------
        Provenance
            A provenance record with ``source=RUNTIME``.
        """
        return Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=tuple(parent_ids),
        )

    # ------------------------------------------------------------------
    # Accessor helpers
    # ------------------------------------------------------------------

    def all_emitted(self) -> list[Judgment]:
        """Return a shallow copy of all emitted judgments.

        Returns
        -------
        list[Judgment]
            A new list containing every judgment emitted since the last
            :meth:`clear` call.
        """
        return list(self._emitted)

    def clear(self) -> None:
        """Clear the internal list of emitted judgments.

        This method resets the emitter state so that subsequent calls to
        :meth:`all_emitted` return an empty list.
        """
        self._emitted.clear()
        logger.debug("HeapJudgmentEmitter cleared.")


# ---------------------------------------------------------------------------
# Z3HeapEncoder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Z3HeapEncoder:
    """Encodes heap invariants as SMT-LIB2 formulae for Z3 verification.

    Each ``encode_*`` method translates a specific heap property into a
    :class:`~jugeo.solver.z3_session.Z3Formula` which can be passed to a
    :class:`~jugeo.solver.z3_session.Z3Session` for satisfiability checking.
    When Z3 is not available in the current environment the methods return
    ``None`` gracefully.

    Parameters
    ----------
    _session:
        An active :class:`Z3Session`, or ``None`` if no session has been
        created yet.
    _formulas:
        Accumulated formulae added during this encoding pass.

    Examples
    --------
    >>> encoder = Z3HeapEncoder()
    >>> objects = [HeapObject(object_id=1, type_name="A", fields=(), is_frozen=False,
    ...                       kind=ObjectKind.INSTANCE)]
    >>> formula = encoder.encode_identity_uniqueness(objects)
    >>> formula is None or hasattr(formula, 'expression')
    True
    """

    _session: Z3Session | None = None
    _formulas: list[Z3Formula] = field(default_factory=list)

    def encode_identity_uniqueness(
        self, objects: list[HeapObject]
    ) -> Z3Formula | None:
        """Encode that all heap object identities are distinct.

        Produces an SMT-LIB2 ``(assert (distinct ...))`` constraint over integer
        constants representing each object's ``object_id``.

        Parameters
        ----------
        objects:
            The heap objects whose identities must be proven distinct.

        Returns
        -------
        Z3Formula | None
            An SMT formula encoding distinctness, or ``None`` if Z3 is
            unavailable or the object list is empty.

        Examples
        --------
        >>> encoder = Z3HeapEncoder()
        >>> objs = [HeapObject(object_id=i, type_name="T", fields=(), is_frozen=False,
        ...                    kind=ObjectKind.INSTANCE) for i in range(3)]
        >>> f = encoder.encode_identity_uniqueness(objs)
        """
        if not z3_available() or not objects:
            return None

        from jugeo.solver.z3_session import FormulaKind

        id_vars = " ".join(f"obj_{o.object_id}" for o in objects)
        decls = "\n".join(
            f"(declare-const obj_{o.object_id} Int)" for o in objects
        )
        value_asserts = "\n".join(
            f"(assert (= obj_{o.object_id} {o.object_id}))" for o in objects
        )
        distinctness = f"(assert (distinct {id_vars}))"
        expression = f"{decls}\n{value_asserts}\n{distinctness}"

        formula = Z3Formula(kind=FormulaKind.INT, expression=expression)
        self._formulas.append(formula)
        logger.debug("Encoded identity uniqueness for %d objects.", len(objects))
        return formula

    def encode_alias_transitivity(
        self, partitions: list[AliasPartition]
    ) -> Z3Formula | None:
        """Encode alias transitivity: if a~b and b~c then a~c.

        Each alias partition is modelled as an integer equivalence class.
        The formula asserts that every pair within the same partition shares a
        class constant, enforcing transitivity by construction.

        Parameters
        ----------
        partitions:
            The alias partitions whose transitivity must be verified.

        Returns
        -------
        Z3Formula | None
            A Z3Formula or ``None`` if Z3 is unavailable.
        """
        if not z3_available() or not partitions:
            return None

        from jugeo.solver.z3_session import FormulaKind

        lines: list[str] = []
        for idx, partition in enumerate(partitions):
            class_const = f"alias_class_{idx}"
            lines.append(f"(declare-const {class_const} Int)")
            lines.append(f"(assert (= {class_const} {idx}))")
            for member in sorted(partition.members):
                safe = member.replace("-", "_")
                lines.append(f"(declare-const member_{safe} Int)")
                lines.append(f"(assert (= member_{safe} {class_const}))")

        expression = "\n".join(lines)
        formula = Z3Formula(kind=FormulaKind.INT, expression=expression)
        self._formulas.append(formula)
        logger.debug(
            "Encoded alias transitivity for %d partitions.", len(partitions)
        )
        return formula

    def encode_mutation_consistency(
        self, events: list[MutationEvent]
    ) -> Z3Formula | None:
        """Encode that valid mutations do not produce conflicting writes.

        Two mutation events conflict when they target the same
        ``(object_id, field_name)`` with different new values.  The formula
        asserts that no two events share a target with distinct values.

        Parameters
        ----------
        events:
            The mutation events to check for consistency.

        Returns
        -------
        Z3Formula | None
            A Z3Formula or ``None`` if Z3 is unavailable or no events exist.
        """
        if not z3_available() or not events:
            return None

        from jugeo.solver.z3_session import FormulaKind

        lines: list[str] = []
        # Group events by (object_id, field_name)
        groups: dict[str, list[MutationEvent]] = {}
        for ev in events:
            key = f"{ev.object_id}__{ev.field_name}"
            groups.setdefault(key, []).append(ev)

        for key, group in groups.items():
            safe_key = key.replace("-", "_")
            lines.append(f"(declare-const mutation_target_{safe_key} Int)")
            for i, ev in enumerate(group):
                val_repr = hash(str(ev.new_value)) & 0x7FFFFFFF
                lines.append(
                    f"(declare-const mut_val_{safe_key}_{i} Int)"
                )
                lines.append(
                    f"(assert (= mut_val_{safe_key}_{i} {val_repr}))"
                )
            if len(group) > 1:
                val_names = [
                    f"mut_val_{safe_key}_{i}" for i in range(len(group))
                ]
                lines.append(
                    f"(assert (= {val_names[0]} {val_names[1]}))"
                )

        expression = "\n".join(lines) if lines else "(assert true)"
        formula = Z3Formula(kind=FormulaKind.BOOL, expression=expression)
        self._formulas.append(formula)
        logger.debug(
            "Encoded mutation consistency for %d events.", len(events)
        )
        return formula

    def encode_no_dangling_refs(
        self,
        objects: list[HeapObject],
        valid_ids: frozenset[int],
    ) -> Z3Formula | None:
        """Encode that no heap object field references a dead object.

        For each integer-typed field whose value represents an object ID, asserts
        that the ID belongs to the set of currently live objects.

        Parameters
        ----------
        objects:
            The heap objects to inspect.
        valid_ids:
            The set of object IDs that are currently alive.

        Returns
        -------
        Z3Formula | None
            A Z3Formula asserting no dangling references, or ``None`` if Z3 is
            unavailable.
        """
        if not z3_available():
            return None

        from jugeo.solver.z3_session import FormulaKind

        lines: list[str] = []
        valid_set_str = " ".join(str(vid) for vid in sorted(valid_ids))

        for obj in objects:
            for fname, fval in obj.fields:
                if isinstance(fval, int) and fval not in valid_ids:
                    safe_name = f"field_{obj.object_id}_{fname}"
                    lines.append(f"(declare-const {safe_name} Int)")
                    lines.append(f"(assert (= {safe_name} {fval}))")
                    # Assert it must equal one of the valid IDs (will be unsat if not)
                    if valid_ids:
                        members_str = " ".join(
                            f"(= {safe_name} {vid})" for vid in sorted(valid_ids)
                        )
                        lines.append(f"(assert (or {members_str}))")

        expression = "\n".join(lines) if lines else "(assert true)"
        formula = Z3Formula(kind=FormulaKind.BOOL, expression=expression)
        self._formulas.append(formula)
        logger.debug("Encoded no-dangling-refs constraint.")
        return formula

    def check_heap_constraints(
        self,
        objects: list[HeapObject],
        partitions: list[AliasPartition],
    ) -> SolveOutcome:
        """Run all heap constraint encodings and check satisfiability.

        Encodes identity uniqueness, alias transitivity, and no-dangling-refs
        in sequence, then invokes the Z3 session.  If any encoding step returns
        ``None`` the overall outcome is ``UNKNOWN``.

        Parameters
        ----------
        objects:
            The heap objects participating in the check.
        partitions:
            The alias partitions derived from those objects.

        Returns
        -------
        SolveOutcome
            The satisfiability result, or :attr:`SolveOutcome.UNKNOWN` if Z3
            is unavailable or encoding fails.
        """
        if not z3_available():
            logger.info("Z3 not available; returning UNKNOWN outcome.")
            return SolveOutcome.UNKNOWN

        valid_ids = frozenset(o.object_id for o in objects)
        f1 = self.encode_identity_uniqueness(objects)
        f2 = self.encode_alias_transitivity(partitions)
        f3 = self.encode_no_dangling_refs(objects, valid_ids)

        if f1 is None:
            logger.warning("Identity uniqueness encoding failed; returning UNKNOWN.")
            return SolveOutcome.UNKNOWN

        try:
            session = Z3Session()
            self._session = session
            for formula in self._formulas:
                session.add_formula(formula)
            outcome = session.check()
            logger.info("Heap constraint check returned %s.", outcome)
            return outcome
        except Exception as exc:  # noqa: BLE001
            logger.error("Z3 check raised exception: %s", exc)
            return SolveOutcome.UNKNOWN

    def get_session(self) -> Z3Session | None:
        """Return the most recently created :class:`Z3Session`, if any.

        Returns
        -------
        Z3Session | None
            The active session or ``None``.
        """
        return self._session

    def reset(self) -> None:
        """Discard all accumulated formulae and the current session.

        After calling this method the encoder is in the same state as a
        freshly constructed instance.
        """
        self._formulas.clear()
        self._session = None
        logger.debug("Z3HeapEncoder reset.")


# ---------------------------------------------------------------------------
# HeapCoordinateMapper
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapCoordinateMapper:
    """Maps heap objects and structural sites to canonical :class:`Coordinate` objects.

    Coordinates are cached so that repeated lookups for the same object or
    field return the same :class:`Coordinate` instance, preserving reference
    identity throughout the pipeline.

    Parameters
    ----------
    _mapping:
        Cache from ``object_id`` to :class:`CoordinateObject`.
    _field_mapping:
        Cache from ``"<object_id>.<field_name>"`` to :class:`CoordinateObject`.

    Examples
    --------
    >>> mapper = HeapCoordinateMapper()
    >>> obj = HeapObject(object_id=1, type_name="Foo", fields=(), is_frozen=False,
    ...                  kind=ObjectKind.INSTANCE)
    >>> coord = mapper.map_object(obj)
    >>> "object" in coord.components
    True
    """

    _mapping: dict[int, CoordinateObject] = field(default_factory=dict)
    _field_mapping: dict[str, CoordinateObject] = field(default_factory=dict)

    def map_object(self, obj: HeapObject) -> CoordinateObject:
        """Return the canonical coordinate for a heap object.

        Parameters
        ----------
        obj:
            The heap object to map.

        Returns
        -------
        CoordinateObject
            A ``Coordinate`` with components ``("heap", "object", str(obj.object_id),
            obj.type_name)`` and ``kind=REGION``.
        """
        if obj.object_id in self._mapping:
            return self._mapping[obj.object_id]

        coord = Coordinate(
            components=("heap", "object", str(obj.object_id), obj.type_name),
            kind=CoordinateKind.REGION,
        )
        self._mapping[obj.object_id] = coord
        return coord

    def map_field(self, obj: HeapObject, field_name: str) -> CoordinateObject:
        """Return the canonical coordinate for a specific object field.

        Parameters
        ----------
        obj:
            The heap object that owns the field.
        field_name:
            The name of the field.

        Returns
        -------
        CoordinateObject
            A ``Coordinate`` with components
            ``("heap", "field", str(obj.object_id), field_name)`` and
            ``kind=REGION``.
        """
        key = f"{obj.object_id}.{field_name}"
        if key in self._field_mapping:
            return self._field_mapping[key]

        coord = Coordinate(
            components=("heap", "field", str(obj.object_id), field_name),
            kind=CoordinateKind.REGION,
        )
        self._field_mapping[key] = coord
        return coord

    def map_alias_class(self, partition: AliasPartition) -> CoordinateObject:
        """Return the coordinate for an alias partition.

        Parameters
        ----------
        partition:
            The alias partition to map.

        Returns
        -------
        CoordinateObject
            A ``Coordinate`` with components
            ``("heap", "alias", partition.partition_id)`` and ``kind=REGION``.
        """
        return Coordinate(
            components=("heap", "alias", partition.partition_id),
            kind=CoordinateKind.REGION,
        )

    def map_mutation_site(self, event: MutationEvent) -> CoordinateObject:
        """Return the coordinate for a mutation event site.

        Parameters
        ----------
        event:
            The mutation event to map.

        Returns
        -------
        CoordinateObject
            A ``Coordinate`` encoding the specific object field targeted by the
            mutation.
        """
        return Coordinate(
            components=("heap", "mutation", event.object_id, event.field_name),
            kind=CoordinateKind.REGION,
        )

    def build_heap_index(self) -> dict[str, CoordinateObject]:
        """Return a unified string-keyed index of all cached coordinates.

        Merges ``_mapping`` (keyed by string-converted object IDs) and
        ``_field_mapping`` (already string-keyed) into a single dictionary.

        Returns
        -------
        dict[str, CoordinateObject]
            Combined index with string keys.
        """
        index: dict[str, CoordinateObject] = {
            str(k): v for k, v in self._mapping.items()
        }
        index.update(self._field_mapping)
        return index

    def lookup(self, object_id: int) -> CoordinateObject | None:
        """Look up the coordinate for an object ID without creating a new entry.

        Parameters
        ----------
        object_id:
            The integer identity of the heap object.

        Returns
        -------
        CoordinateObject | None
            The cached coordinate, or ``None`` if the object has not been mapped.
        """
        return self._mapping.get(object_id)

    def count(self) -> int:
        """Return the total number of cached coordinate entries.

        Returns
        -------
        int
            Sum of object-level and field-level cache sizes.
        """
        return len(self._mapping) + len(self._field_mapping)


# ---------------------------------------------------------------------------
# SupportRegionBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SupportRegionBuilder:
    """Constructs :class:`~jugeo.geometry.supports.SupportRegion` objects for heap entities.

    Each region records which patch keys (object IDs, field identifiers, etc.)
    a given judgment depends upon.  Regions are cached internally so that
    repeated calls for the same key are free.

    Parameters
    ----------
    _regions:
        Cache from arbitrary string keys to :class:`SupportRegion`.

    Examples
    --------
    >>> builder = SupportRegionBuilder()
    >>> obj = HeapObject(object_id=5, type_name="Bar", fields=(("x", 1),),
    ...                  is_frozen=False, kind=ObjectKind.INSTANCE)
    >>> region = builder.build_object_support(obj)
    >>> "5" in region.patch_keys
    True
    """

    _regions: dict[str, SupportRegion] = field(default_factory=dict)

    def build_object_support(self, obj: HeapObject) -> SupportRegion:
        """Build a support region for a heap object and all its fields.

        Parameters
        ----------
        obj:
            The heap object whose support region is requested.

        Returns
        -------
        SupportRegion
            A region whose patch keys include the object ID and every referenced
            field value (as strings), labelled with the object's type name.
        """
        coord = Coordinate(
            components=("heap", "object", str(obj.object_id)),
            kind=CoordinateKind.REGION,
        )
        field_value_keys = frozenset(str(v) for _, v in obj.fields)
        patch_keys: frozenset[str] = frozenset({str(obj.object_id)}) | field_value_keys

        region = SupportRegion(
            coordinate=coord,
            patch_keys=patch_keys,
            labels=frozenset({obj.type_name}),
        )
        self._regions[str(obj.object_id)] = region
        return region

    def build_alias_support(
        self,
        partition: AliasPartition,
        objects: list[HeapObject],
    ) -> SupportRegion:
        """Build a support region for an alias partition.

        Parameters
        ----------
        partition:
            The alias partition whose support is being constructed.
        objects:
            The full list of heap objects, used to derive extra labels.

        Returns
        -------
        SupportRegion
            A region whose patch keys are the partition's member IDs.
        """
        coord = Coordinate(
            components=("heap", "alias", partition.partition_id),
            kind=CoordinateKind.REGION,
        )
        type_labels = frozenset(
            o.type_name
            for o in objects
            if str(o.object_id) in partition.members
        )
        region = SupportRegion(
            coordinate=coord,
            patch_keys=partition.members,
            labels=type_labels | frozenset({"alias_partition"}),
        )
        self._regions[partition.partition_id] = region
        return region

    def build_mutation_support(self, event: MutationEvent) -> SupportRegion:
        """Build a support region for a heap mutation event.

        Parameters
        ----------
        event:
            The mutation event whose support is being constructed.

        Returns
        -------
        SupportRegion
            A region keyed by the event's object ID and field name.
        """
        coord = Coordinate(
            components=("heap", "mutation", event.object_id, event.field_name),
            kind=CoordinateKind.REGION,
        )
        region = SupportRegion(
            coordinate=coord,
            patch_keys=frozenset({event.object_id, event.field_name}),
            labels=frozenset({"mutation"}),
        )
        key = f"mutation.{event.object_id}.{event.field_name}"
        self._regions[key] = region
        return region

    def merge_supports(self, regions: list[SupportRegion]) -> SupportRegion:
        """Merge multiple support regions into a single combined region.

        Parameters
        ----------
        regions:
            The list of regions to merge.  Must be non-empty.

        Returns
        -------
        SupportRegion
            A new region whose ``patch_keys`` and ``labels`` are the unions of
            those of all input regions.  The ``coordinate`` is taken from the
            first region.

        Raises
        ------
        ValueError
            If ``regions`` is empty.
        """
        if not regions:
            raise ValueError("Cannot merge an empty list of SupportRegions.")

        combined_keys: frozenset[str] = frozenset()
        combined_labels: frozenset[str] = frozenset()
        for region in regions:
            combined_keys = combined_keys | region.patch_keys
            combined_labels = combined_labels | region.labels

        return SupportRegion(
            coordinate=regions[0].coordinate,
            patch_keys=combined_keys,
            labels=combined_labels,
        )

    def validate_support_coverage(
        self,
        regions: list[SupportRegion],
        required_keys: frozenset[str],
    ) -> bool:
        """Check whether the union of regions covers all required patch keys.

        Parameters
        ----------
        regions:
            The support regions to inspect.
        required_keys:
            The set of keys that must be covered.

        Returns
        -------
        bool
            ``True`` iff every key in *required_keys* appears in at least one
            region's ``patch_keys``.
        """
        covered: frozenset[str] = frozenset()
        for region in regions:
            covered = covered | region.patch_keys
        missing = required_keys - covered
        if missing:
            logger.debug(
                "Support coverage gap: %d key(s) missing: %s",
                len(missing),
                missing,
            )
        return len(missing) == 0

    def get_or_build(
        self,
        key: str,
        builder_fn: Any,
    ) -> SupportRegion:
        """Return a cached region or build one using *builder_fn*.

        Parameters
        ----------
        key:
            The cache key under which to look up or store the region.
        builder_fn:
            A zero-argument callable that constructs and returns a
            :class:`SupportRegion` if the key is not already cached.

        Returns
        -------
        SupportRegion
            The cached or newly built region.
        """
        if key not in self._regions:
            self._regions[key] = builder_fn()
        return self._regions[key]


# ---------------------------------------------------------------------------
# CopilotHeapAdvisor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CopilotHeapAdvisor:
    """AI-powered copilot integration for heap aliasing analysis advice.

    This class provides copilot integration that translates low-level heap
    analysis results into actionable, human-readable advisory messages.  It is
    designed to be called after :class:`HeapJudgmentEmitter` has completed its
    pass so that the advisor has access to fully attributed judgment data.

    The advisor logs every piece of advice to ``_advice_log`` so that a session
    summary can be generated at any point via :meth:`format_heap_report`.

    Parameters
    ----------
    _advice_log:
        Chronological list of advice entries, each a ``dict`` with at least the
        keys ``"timestamp"``, ``"kind"``, and ``"message"``.
    _enabled:
        When ``False``, advice methods still execute but skip logging.  Useful
        for disabling advisor output in production hot paths.

    Examples
    --------
    >>> advisor = CopilotHeapAdvisor()
    >>> obj = HeapObject(object_id=1, type_name="Config", fields=(("x", 1),),
    ...                  is_frozen=False, kind=ObjectKind.INSTANCE)
    >>> advice = advisor.suggest_immutability(obj)
    >>> "frozen" in advice.lower() or "immutable" in advice.lower()
    True

    Notes
    -----
    This copilot advisor is intentionally stateless with respect to the
    broader JuGeo pipeline — it reads heap data structures but never mutates
    them.  All state is confined to ``_advice_log`` and ``_enabled``.
    """

    _advice_log: list[dict[str, Any]] = field(default_factory=list)
    _enabled: bool = True

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _log(self, kind: str, message: str) -> None:
        """Append an advice entry to the log if the advisor is enabled.

        Parameters
        ----------
        kind:
            Short category label (e.g. ``"immutability"``, ``"aliasing"``).
        message:
            The advice string.
        """
        if self._enabled:
            self._advice_log.append(
                {
                    "timestamp": time.monotonic(),
                    "kind": kind,
                    "message": message,
                }
            )

    # ------------------------------------------------------------------
    # Advice methods
    # ------------------------------------------------------------------

    def suggest_immutability(self, obj: HeapObject) -> str:
        """Suggest whether and how a heap object should be made immutable.

        Parameters
        ----------
        obj:
            The heap object to analyse.

        Returns
        -------
        str
            A human-readable recommendation string.

        Examples
        --------
        >>> advisor = CopilotHeapAdvisor()
        >>> frozen_obj = HeapObject(object_id=2, type_name="Point",
        ...                         fields=(("x", 0),), is_frozen=True,
        ...                         kind=ObjectKind.INSTANCE)
        >>> "already immutable" in advisor.suggest_immutability(frozen_obj)
        True
        """
        if obj.is_frozen:
            advice = "Object is already immutable; no action needed."
        elif obj.kind == ObjectKind.PRIMITIVE:
            advice = "Primitives are inherently immutable in Python."
        elif obj.kind == ObjectKind.CONTAINER and not obj.fields:
            advice = (
                "Consider using a frozenset or tuple instead of an empty "
                "mutable container — they are inherently immutable and "
                "eliminate aliasing risk entirely."
            )
        else:
            field_count = len(obj.fields)
            if field_count <= 5:
                advice = (
                    f"The object has {field_count} field(s).  "
                    "Consider converting it to a @dataclass(frozen=True) or "
                    "a collections.namedtuple to prevent unintended mutation "
                    "through aliased references."
                )
            else:
                advice = (
                    f"The object has {field_count} field(s), which is above "
                    "the typical threshold for frozen dataclasses.  Consider "
                    "splitting it into smaller frozen dataclasses or using "
                    "dataclasses.replace() for copy-on-write semantics."
                )

        logger.debug("Immutability advice for %s: %s", obj.type_name, advice)
        self._log("immutability", advice)
        return advice

    def explain_aliasing(self, partition: AliasPartition) -> str:
        """Produce a human-readable explanation of an alias partition.

        Parameters
        ----------
        partition:
            The alias partition to explain.

        Returns
        -------
        str
            A multi-sentence explanation including member count, representative,
            and a complexity warning if the partition is large.

        Examples
        --------
        >>> advisor = CopilotHeapAdvisor()
        >>> p = AliasPartition(partition_id="p1", representative="10",
        ...                    members=frozenset({"10", "11"}))
        >>> "alias" in advisor.explain_aliasing(p).lower()
        True
        """
        size = partition.size()
        member_list = sorted(partition.members)
        explanation_parts = [
            f"Alias partition '{partition.partition_id}' contains {size} "
            f"object reference(s).",
            f"The canonical representative is object ID {partition.representative}.",
            f"All members: {', '.join(member_list)}.",
        ]
        if size > 5:
            explanation_parts.append(
                f"Warning: this partition has {size} members, which indicates "
                "complex aliasing.  Mutations to any one member will be "
                "visible through all other aliases.  Consider refactoring to "
                "reduce the aliasing degree, or ensure that all mutations are "
                "broadcast to every member consistently."
            )
        explanation = "  ".join(explanation_parts)
        self._log("aliasing", explanation)
        return explanation

    def detect_mutation_bugs(
        self,
        events: list[MutationEvent],
        partitions: list[AliasPartition],
    ) -> list[str]:
        """Detect potential bugs in a sequence of heap mutation events.

        Two categories of bugs are detected:

        1. Mutations targeting a frozen object.
        2. Mutations to one member of an alias partition without a corresponding
           mutation to all other aliases (potential stale-alias bug).

        Parameters
        ----------
        events:
            The mutation events to analyse.
        partitions:
            The alias partitions currently known to the heap analyser.

        Returns
        -------
        list[str]
            A list of bug description strings.  Empty if no bugs are found.

        Examples
        --------
        >>> advisor = CopilotHeapAdvisor()
        >>> bugs = advisor.detect_mutation_bugs([], [])
        >>> bugs == []
        True
        """
        bugs: list[str] = []

        # Build a map from object_id (str) -> set of field_names mutated
        mutated_fields: dict[str, set[str]] = {}
        for ev in events:
            mutated_fields.setdefault(ev.object_id, set()).add(ev.field_name)

        for partition in partitions:
            mutated_members = {
                m for m in partition.members if m in mutated_fields
            }
            if 0 < len(mutated_members) < partition.size():
                unmutated = sorted(partition.members - mutated_members)
                bugs.append(
                    f"Stale-alias bug: partition '{partition.partition_id}' "
                    f"had {len(mutated_members)} member(s) mutated but "
                    f"{len(unmutated)} aliased member(s) were not updated: "
                    f"{unmutated}.  All aliases should be updated consistently."
                )

        for ev in events:
            if ev.is_frozen_target:
                bugs.append(
                    f"Immutability violation: mutation of field "
                    f"'{ev.field_name}' on frozen object '{ev.object_id}' "
                    "will raise AttributeError at runtime."
                )

        for bug in bugs:
            self._log("mutation_bug", bug)
        return bugs

    def suggest_copy_semantics(self, obj: HeapObject) -> str:
        """Recommend appropriate copy or clone semantics for a heap object.

        Parameters
        ----------
        obj:
            The heap object to analyse.

        Returns
        -------
        str
            A human-readable recommendation for how to clone or copy the object.

        Examples
        --------
        >>> advisor = CopilotHeapAdvisor()
        >>> obj = HeapObject(object_id=3, type_name="Cfg", fields=(("a", 1),),
        ...                  is_frozen=True, kind=ObjectKind.INSTANCE)
        >>> "immutable" in advisor.suggest_copy_semantics(obj)
        True
        """
        if obj.is_frozen:
            advice = (
                "Object is immutable; no copy-on-write is needed.  "
                "References can be shared freely without risk of aliasing bugs."
            )
        elif obj.kind == ObjectKind.CONTAINER:
            advice = (
                "For mutable containers, prefer copy.deepcopy() when you need "
                "a fully independent clone, or structural sharing via tuple "
                "replacement (e.g. obj.items + (new_item,)) to avoid "
                "materialising a full copy."
            )
        else:
            advice = (
                f"'{obj.type_name}' is a mutable instance with "
                f"{len(obj.fields)} field(s).  If it is a dataclass, use "
                "dataclasses.replace(obj, field=new_value) for efficient "
                "copy-on-write semantics.  For deep graphs, consider "
                "copy.deepcopy() or a hand-rolled structural-sharing clone."
            )

        self._log("copy_semantics", advice)
        return advice

    def format_heap_report(self, analyzer_output: dict[str, Any]) -> str:
        """Format an analyser output dictionary as a human-readable text report.

        Parameters
        ----------
        analyzer_output:
            A dictionary produced by the heap analyser.  Expected keys (all
            optional) are ``"object_count"``, ``"alias_count"``,
            ``"cycle_count"``, and ``"dangling_refs"``.

        Returns
        -------
        str
            A multi-line formatted report string.

        Examples
        --------
        >>> advisor = CopilotHeapAdvisor()
        >>> report = advisor.format_heap_report({"object_count": 10, "alias_count": 3})
        >>> "Object Count" in report
        True
        """
        object_count = analyzer_output.get("object_count", 0)
        alias_count = analyzer_output.get("alias_count", 0)
        cycle_count = analyzer_output.get("cycle_count", 0)
        dangling_refs = analyzer_output.get("dangling_refs", 0)

        separator = "-" * 50
        lines = [
            "=" * 50,
            "  JuGeo Heap Aliasing Analysis Report",
            "  (copilot integration summary)",
            "=" * 50,
            "",
            "  Heap Statistics",
            separator,
            f"  Object Count   : {object_count}",
            f"  Alias Count    : {alias_count}",
            f"  Cycle Count    : {cycle_count}",
            f"  Dangling Refs  : {dangling_refs}",
            "",
        ]

        if cycle_count > 0:
            lines += [
                "  ⚠  Cycles Detected",
                separator,
                f"  {cycle_count} reference cycle(s) found.  These may prevent",
                "  garbage collection and indicate hidden aliasing bugs.",
                "",
            ]

        if dangling_refs > 0:
            lines += [
                "  ✗  Dangling References",
                separator,
                f"  {dangling_refs} dangling reference(s) detected.  Objects",
                "  referenced from live heap objects no longer exist.",
                "",
            ]

        advice_count = len(self._advice_log)
        lines += [
            "  Copilot Advice Log",
            separator,
            f"  {advice_count} advisory message(s) recorded this session.",
            "=" * 50,
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Accessor and control methods
    # ------------------------------------------------------------------

    def all_advice(self) -> list[dict[str, Any]]:
        """Return a shallow copy of all logged advice entries.

        Returns
        -------
        list[dict[str, Any]]
            A new list of advice entry dictionaries, each with keys
            ``"timestamp"``, ``"kind"``, and ``"message"``.
        """
        return list(self._advice_log)

    def disable(self) -> None:
        """Disable advice logging.

        While disabled, advice methods still return their string results but
        do not append entries to :attr:`_advice_log`.
        """
        self._enabled = False
        logger.debug("CopilotHeapAdvisor disabled.")

    def enable(self) -> None:
        """Re-enable advice logging.

        Restores normal operation after a prior :meth:`disable` call.
        """
        self._enabled = True
        logger.debug("CopilotHeapAdvisor enabled.")


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    "HeapJudgmentEmitter",
    "Z3HeapEncoder",
    "HeapCoordinateMapper",
    "SupportRegionBuilder",
    "CopilotHeapAdvisor",
]
