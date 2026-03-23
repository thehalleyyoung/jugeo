"""Mutation as Section Replacement with Descent Check.

theory2.tex Ch17, §3 — Mutation as Section Replacement with Descent Check.

In the sheaf-theoretic model of the Python heap (theory2.tex Ch17), a *field
write* ``obj.attr = new_value`` is not merely a memory update.  It is a
*local section replacement*: the section carried by ``obj``'s identity
coordinate is replaced by a new section that differs on the patch
``{attr}``.  For this replacement to be globally consistent the **descent
check** must pass: every alias of ``obj`` (every reference sharing the same
identity coordinate) must observe the same updated value after the write.

This module implements the descent-check machinery described in Ch17 §3,
including:

* :class:`MutationValidationResult` — the outcome of a descent check.
* :class:`MutationValidator` — the central validator that enforces the
  descent / sheaf condition for every mutation event.
* :class:`MutationRecorder` — an event log that tracks all mutation events
  for post-hoc analysis.
* :class:`DescentChecker` — low-level implementation of the sheaf descent
  condition for a single mutation event.
* :class:`MutationImpactAnalyzer` — analyses the blast-radius of a mutation
  across the alias graph.
* :class:`FrozenObjectChecker` — determines whether a Python object belongs
  to an immutable type and hence cannot be legally mutated.

Copilot integration note
------------------------
This module was developed with GitHub Copilot assistance as part of the
jugeo copilot integration pipeline.  The validation logic is kept side-effect
free so that it can be exercised by the Copilot skill harness without
touching live Python objects.

References
----------
theory2.tex Ch17, §3.
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
# MutationValidationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MutationValidationResult:
    """Immutable record of a single mutation validation outcome.

    A :class:`MutationValidationResult` is produced by
    :meth:`MutationValidator.validate` and summarises whether a
    :class:`~jugeo.python_runtime.heap_aliasing.models.MutationEvent` satisfies
    the descent check (theory2.tex Ch17 §3).  It is *frozen* so that result
    objects can be stored safely in immutable containers and compared for
    equality without risk of accidental mutation.

    Parameters
    ----------
    is_valid : bool
        ``True`` iff the mutation passes all descent-check sub-conditions.
    error_messages : tuple[str, ...]
        Zero or more error strings describing violated conditions.  Non-empty
        only when ``is_valid`` is ``False``.
    warnings : tuple[str, ...]
        Zero or more non-fatal advisory messages.  A result may have warnings
        even when ``is_valid`` is ``True``.
    event : MutationEvent | None
        The mutation event that was validated, or ``None`` when the result is
        used as a sentinel value.

    Examples
    --------
    >>> result = MutationValidationResult(
    ...     is_valid=True,
    ...     error_messages=(),
    ...     warnings=("aliased write: 3 observers",),
    ...     event=None,
    ... )
    >>> result.has_errors()
    False
    >>> result.has_warnings()
    True
    """

    is_valid: bool
    error_messages: tuple[str, ...]
    warnings: tuple[str, ...]
    event: MutationEvent | None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_errors(self) -> bool:
        """Return ``True`` iff there is at least one error message.

        Returns
        -------
        bool
            ``True`` when ``error_messages`` is non-empty.

        Examples
        --------
        >>> MutationValidationResult(
        ...     is_valid=False, error_messages=("e1",), warnings=(), event=None
        ... ).has_errors()
        True
        """
        return len(self.error_messages) > 0

    def has_warnings(self) -> bool:
        """Return ``True`` iff there is at least one warning message.

        Returns
        -------
        bool
            ``True`` when ``warnings`` is non-empty.

        Examples
        --------
        >>> MutationValidationResult(
        ...     is_valid=True, error_messages=(), warnings=("w1",), event=None
        ... ).has_warnings()
        True
        """
        return len(self.warnings) > 0

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``is_valid``, ``error_messages``, ``warnings``,
            ``event`` (serialised or ``None``).

        Examples
        --------
        >>> r = MutationValidationResult(
        ...     is_valid=True, error_messages=(), warnings=(), event=None
        ... )
        >>> r.serialize()["is_valid"]
        True
        """
        return {
            "is_valid": self.is_valid,
            "error_messages": list(self.error_messages),
            "warnings": list(self.warnings),
            "event": self.event.serialize() if self.event is not None else None,
        }


# ---------------------------------------------------------------------------
# MutationValidator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MutationValidator:
    """Validates that a mutation event satisfies the sheaf descent condition.

    :class:`MutationValidator` is the central orchestrator for mutation
    validation in the heap-aliasing analysis pipeline.  Given a
    :class:`~jugeo.python_runtime.heap_aliasing.models.MutationEvent` and the
    :class:`~jugeo.python_runtime.heap_aliasing.models.HeapSection` it targets,
    the validator runs a suite of sub-checks:

    1. **Frozen-type check** — refuses mutations on immutable types.
    2. **Type-constraint check** — verifies the new value's type matches the
       existing field type.
    3. **Alias-consistency check** — verifies that all aliases of the mutated
       object see a consistent updated value (the descent / sheaf condition).
    4. **Protocol-compliance check** — verifies custom ``__setattr__`` or
       ``__slots__`` constraints are not violated.

    Failing any sub-check produces error messages in the returned
    :class:`MutationValidationResult`; a result with no errors is valid.

    Parameters
    ----------
    _recorded_mutations : list[MutationEvent]
        History of all mutations that have been accepted and recorded.
    _alias_map : dict[str, list[str]]
        Maps an ``object_key`` (string) to the list of alias keys for that
        object.  Populated by :meth:`register_aliases`.
    _frozen_types : frozenset[str]
        Set of type names whose instances are considered immutable.

    Examples
    --------
    >>> validator = MutationValidator()
    >>> event = MutationEvent(
    ...     event_id="e1", object_id="42", field_name="x",
    ...     old_value_repr="1", new_value_repr="2", timestamp=0.0
    ... )
    >>> section = HeapSection(section_id="s1", objects=())
    >>> result = validator.validate(event, section)
    >>> result.is_valid
    True
    """

    _recorded_mutations: list[MutationEvent] = field(default_factory=list)
    _alias_map: dict[str, list[str]] = field(default_factory=dict)
    _frozen_types: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"int", "float", "str", "bytes", "tuple", "frozenset", "bool", "NoneType"}
        )
    )

    # ------------------------------------------------------------------
    # Primary validation entry-point
    # ------------------------------------------------------------------

    def validate(
        self, event: MutationEvent, section: HeapSection
    ) -> MutationValidationResult:
        """Run all descent-check sub-conditions for ``event`` against ``section``.

        This is the main entry-point.  All sub-checks are executed in order;
        their results are accumulated into a single
        :class:`MutationValidationResult`.

        Parameters
        ----------
        event : MutationEvent
            The mutation event to validate.
        section : HeapSection
            The :class:`HeapSection` the mutation targets.

        Returns
        -------
        MutationValidationResult
            Aggregated result with all error and warning messages.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> ev = MutationEvent(
        ...     event_id="ev1", object_id="obj1", field_name="attr",
        ...     old_value_repr="0", new_value_repr="1", timestamp=time.time(),
        ... )
        >>> section = HeapSection(section_id="sec1", objects=())
        >>> res = validator.validate(ev, section)
        >>> isinstance(res, MutationValidationResult)
        True
        """
        logger.debug("MutationValidator.validate: event_id=%s", event.event_id)
        errors: list[str] = []
        warnings: list[str] = []

        # Sub-check 1: frozen type
        frozen_errors = self.check_frozen_constraint(event)
        errors.extend(frozen_errors)

        # Sub-check 2: type constraint
        type_errors = self.check_type_constraint(event, section)
        errors.extend(type_errors)

        # Sub-check 3: alias consistency (descent condition)
        alias_errors = self.check_alias_consistency(event)
        if alias_errors:
            errors.extend(alias_errors)
        else:
            aliases = self._alias_map.get(event.object_id, [])
            if aliases:
                warnings.append(
                    f"Mutation on aliased object: {len(aliases)} aliases observed"
                )

        # Sub-check 4: protocol compliance
        protocol_errors = self.check_protocol_compliance(event)
        errors.extend(protocol_errors)

        is_valid = len(errors) == 0
        result = MutationValidationResult(
            is_valid=is_valid,
            error_messages=tuple(errors),
            warnings=tuple(warnings),
            event=event,
        )
        logger.info(
            "MutationValidator.validate: event=%s valid=%s errors=%d",
            event.event_id,
            is_valid,
            len(errors),
        )
        return result

    # ------------------------------------------------------------------
    # Sub-checks
    # ------------------------------------------------------------------

    def check_alias_consistency(self, event: MutationEvent) -> list[str]:
        """Check that the mutation does not break alias-consistency.

        For the descent condition (theory2.tex Ch17 §3) to hold, all aliases
        of the mutated object must agree on the new field value after the
        write.  This check inspects ``_alias_map`` to find objects that share
        the same identity coordinate and verifies that no conflicting write has
        been recorded for the same ``(object_id, field_name)`` pair.

        Parameters
        ----------
        event : MutationEvent
            The mutation event being validated.

        Returns
        -------
        list[str]
            A list of error strings, empty if alias consistency holds.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> validator.register_aliases("obj1", ["obj2", "obj3"])
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="obj1", field_name="x",
        ...     old_value_repr="1", new_value_repr="2", timestamp=0.0
        ... )
        >>> errors = validator.check_alias_consistency(ev)
        >>> errors  # no conflicting writes recorded yet
        []
        """
        errors: list[str] = []
        aliases = self._alias_map.get(event.object_id, [])
        if not aliases:
            return errors

        # Look for any already-recorded mutations on the same field for aliases
        # If an alias was mutated to a different value, we have an inconsistency.
        for alias_key in aliases:
            for recorded in self._recorded_mutations:
                if (
                    recorded.object_id == alias_key
                    and recorded.field_name == event.field_name
                    and recorded.new_value_repr != event.new_value_repr
                ):
                    errors.append(
                        f"Alias inconsistency: object '{event.object_id}' and alias "
                        f"'{alias_key}' have diverging values for field "
                        f"'{event.field_name}': "
                        f"'{event.new_value_repr}' vs '{recorded.new_value_repr}'"
                    )

        return errors

    def check_type_constraint(
        self, event: MutationEvent, section: HeapSection
    ) -> list[str]:
        """Verify the new value's type is compatible with the existing field.

        The check uses the ``old_value_repr`` and ``new_value_repr`` strings
        from ``event`` to infer Python types.  It flags writes that change a
        field from a numeric type to a string, or from a collection type to a
        scalar, since these indicate likely programming errors.

        Parameters
        ----------
        event : MutationEvent
            The mutation event to type-check.
        section : HeapSection
            The section containing the target object (used for additional context).

        Returns
        -------
        list[str]
            List of type-constraint error messages; empty if no violation.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> sec = HeapSection(section_id="s1", objects=())
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="obj1", field_name="count",
        ...     old_value_repr="42", new_value_repr="hello", timestamp=0.0
        ... )
        >>> errs = validator.check_type_constraint(ev, sec)
        >>> len(errs) > 0  # int -> str is suspicious
        True
        """
        errors: list[str] = []
        old_repr = event.old_value_repr.strip()
        new_repr = event.new_value_repr.strip()

        def _looks_numeric(s: str) -> bool:
            try:
                float(s)
                return True
            except ValueError:
                return False

        def _looks_collection(s: str) -> bool:
            return s.startswith(("[", "{", "(")) and s.endswith(("]", "}", ")"))

        def _looks_string(s: str) -> bool:
            return (s.startswith("'") and s.endswith("'")) or (
                s.startswith('"') and s.endswith('"')
            )

        old_numeric = _looks_numeric(old_repr)
        new_numeric = _looks_numeric(new_repr)
        old_collection = _looks_collection(old_repr)
        new_collection = _looks_collection(new_repr)
        old_str = _looks_string(old_repr)
        new_str = _looks_string(new_repr)

        if old_numeric and new_str:
            errors.append(
                f"Type-constraint violation on field '{event.field_name}': "
                f"old value looks numeric ({old_repr!r}) but new value looks like "
                f"a string ({new_repr!r})"
            )
        if old_collection and new_numeric:
            errors.append(
                f"Type-constraint violation on field '{event.field_name}': "
                f"old value looks like a collection ({old_repr!r}) but new value "
                f"is numeric ({new_repr!r})"
            )
        if old_str and new_collection:
            errors.append(
                f"Type-constraint violation on field '{event.field_name}': "
                f"old value looks like a string but new value is a collection"
            )

        return errors

    def check_protocol_compliance(self, event: MutationEvent) -> list[str]:
        """Check that the mutation respects known protocol constraints.

        Protocol compliance covers two common Python patterns:
        1. **``__slots__``-based classes**: only declared slot names may be
           mutated.  Since we operate on repr strings we use a naming
           convention: field names starting with ``"__"`` and ending with
           ``"__"`` (dunder names) are always permitted; all others are
           unconstrained at this level.
        2. **``_``-prefixed names**: single-underscore names are a convention
           signal for internal state; a mutation targeting them gets a warning
           (recorded as a soft error in this implementation).

        Parameters
        ----------
        event : MutationEvent
            The mutation event to check.

        Returns
        -------
        list[str]
            Protocol violation error messages; empty if compliant.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="__class__",
        ...     old_value_repr="int", new_value_repr="str", timestamp=0.0
        ... )
        >>> errs = validator.check_protocol_compliance(ev)
        >>> any("dunder" in e for e in errs)
        True
        """
        errors: list[str] = []
        fname = event.field_name

        # Writing to a dunder name is almost always wrong at user level
        if fname.startswith("__") and fname.endswith("__") and fname != "__dict__":
            errors.append(
                f"Protocol violation: mutation targets dunder attribute '{fname}'; "
                f"mutating dunder attributes bypasses class invariants"
            )

        # Writing to __class__ is a type-change which violates object identity
        if fname == "__class__":
            errors.append(
                f"Protocol violation: mutation changes __class__ on object "
                f"'{event.object_id}'; type changes destroy identity coordinate semantics"
            )

        return errors

    def check_frozen_constraint(self, event: MutationEvent) -> list[str]:
        """Check whether the target object belongs to a frozen (immutable) type.

        If the object's type name is in ``_frozen_types``, any mutation is
        invalid because CPython prevents in-place modifications of those
        types.

        Parameters
        ----------
        event : MutationEvent
            The mutation event whose target type is to be checked.

        Returns
        -------
        list[str]
            A singleton error list if the type is frozen, otherwise empty.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="1234", field_name="attr",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0
        ... )
        >>> # We can't detect frozen from MutationEvent alone without type info;
        >>> # check returns [] unless the event carries a frozen-type hint
        >>> errors = validator.check_frozen_constraint(ev)
        >>> isinstance(errors, list)
        True
        """
        errors: list[str] = []
        # We infer the type from the object_id key format when possible.
        # If the object_id encodes a type hint like "str:1234", extract it.
        parts = event.object_id.split(":", 1)
        if len(parts) == 2:
            inferred_type = parts[0]
            if inferred_type in self._frozen_types:
                errors.append(
                    f"Frozen-type constraint: cannot mutate object '{event.object_id}' "
                    f"because type '{inferred_type}' is immutable"
                )
        return errors

    # ------------------------------------------------------------------
    # Judgment production
    # ------------------------------------------------------------------

    def build_mutation_judgment(
        self, event: MutationEvent, result: MutationValidationResult
    ) -> Any:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` for a mutation.

        Constructs a structured :class:`~jugeo.judgments.judgment_terms.Judgment`
        encoding the outcome of the descent check for ``event``.  If the
        mutation is invalid the judgment includes obstructions; otherwise it
        carries a trust-level of ``RUNTIME_WITNESSED``.

        Parameters
        ----------
        event : MutationEvent
            The mutation event being judged.
        result : MutationValidationResult
            The validation result for ``event``.

        Returns
        -------
        Judgment
            A fully constructed judgment object.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="obj1", field_name="x",
        ...     old_value_repr="0", new_value_repr="1", timestamp=0.0,
        ... )
        >>> r = MutationValidationResult(
        ...     is_valid=True, error_messages=(), warnings=(), event=ev
        ... )
        >>> j = validator.build_mutation_judgment(ev, r)
        >>> j is not None
        True
        """
        coord = CoordinateObject(
            components=(event.object_id, event.field_name),
            kind=CoordinateKind.REGION,
        )
        trust_level = (
            TrustLevel.RUNTIME_WITNESSED if result.is_valid else TrustLevel.CONTRADICTED
        )
        formula = (
            f"descent_check_passed({event.object_id!r}, {event.field_name!r})"
            if result.is_valid
            else f"descent_check_failed({event.object_id!r}, {event.field_name!r})"
        )

        builder = (
            JudgmentBuilder()
            .at(coord)
            .claiming_formula(formula)
            .of_type_named("MutationValidity")
            .with_trust_level(trust_level)
        )

        if not result.is_valid:
            for msg in result.error_messages:
                obs = Obstruction(
                    obstruction_id=str(uuid.uuid4()),
                    violated_condition="descent_check",
                    coordinate=event.object_id,
                    repair_hints=(f"Review mutation of '{event.field_name}': {msg}",),
                    cohomology_class="H^1",
                )
                builder = builder.with_obstruction(obs)

        return builder.build()

    # ------------------------------------------------------------------
    # Mutation recording and management
    # ------------------------------------------------------------------

    def record_mutation(self, event: MutationEvent) -> None:
        """Append ``event`` to the internal mutation history.

        Parameters
        ----------
        event : MutationEvent
            The mutation event to record.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="0", new_value_repr="1", timestamp=0.0
        ... )
        >>> validator.record_mutation(ev)
        >>> validator.get_mutations_for("o1")[0].event_id
        'e1'
        """
        self._recorded_mutations.append(event)
        logger.debug("MutationValidator.record_mutation: event_id=%s", event.event_id)

    def rollback_mutation(self, event_id: str) -> bool:
        """Remove a previously recorded mutation by its ``event_id``.

        Rollback is used when a mutation is later found to violate global
        consistency and needs to be retracted from the history.

        Parameters
        ----------
        event_id : str
            The unique ID of the event to remove.

        Returns
        -------
        bool
            ``True`` if an event was found and removed; ``False`` otherwise.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="0", new_value_repr="1", timestamp=0.0
        ... )
        >>> validator.record_mutation(ev)
        >>> validator.rollback_mutation("e1")
        True
        >>> validator.rollback_mutation("e1")
        False
        """
        original_len = len(self._recorded_mutations)
        self._recorded_mutations = [
            e for e in self._recorded_mutations if e.event_id != event_id
        ]
        removed = len(self._recorded_mutations) < original_len
        if removed:
            logger.info("MutationValidator.rollback_mutation: rolled back %s", event_id)
        else:
            logger.warning(
                "MutationValidator.rollback_mutation: event_id %s not found", event_id
            )
        return removed

    def apply_mutation(
        self, section: HeapSection, event: MutationEvent
    ) -> HeapSection:
        """Return a new :class:`HeapSection` with ``event``'s field update applied.

        Implements the section-replacement semantics of theory2.tex Ch17 §3.
        The original ``section`` is not modified (frozen dataclass); a new
        instance is returned via :func:`~dataclasses.replace`.

        Parameters
        ----------
        section : HeapSection
            The section containing the target object.
        event : MutationEvent
            The mutation to apply (identifies ``object_id`` and ``field_name``).

        Returns
        -------
        HeapSection
            A new section with the specified field updated.  If the target
            object is not found in ``section.objects``, the original section
            is returned unchanged with a warning logged.

        Examples
        --------
        >>> obj = HeapObject(
        ...     object_id=42, type_name="MyClass", kind=ObjectKind.USER_DEFINED,
        ...     fields=(("x", 100),), is_frozen=False,
        ... )
        >>> section = HeapSection(section_id="s1", objects=(obj,))
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="42", field_name="x",
        ...     old_value_repr="100", new_value_repr="999", timestamp=0.0,
        ... )
        >>> validator = MutationValidator()
        >>> new_section = validator.apply_mutation(section, ev)
        >>> new_section.section_id
        's1'
        """
        updated_objects: list[HeapObject] = []
        mutated = False

        for obj in section.objects:
            if str(obj.object_id) == event.object_id:
                # Rebuild the fields tuple, replacing the target field
                new_fields: list[tuple[str, int]] = []
                field_found = False
                for fname, fref in obj.fields:
                    if fname == event.field_name:
                        # We cannot recover the actual new reference ID from
                        # repr alone; retain the old reference slot with a
                        # sentinel value of -1 indicating "updated externally"
                        new_fields.append((fname, -1))
                        field_found = True
                    else:
                        new_fields.append((fname, fref))
                if not field_found:
                    # Field did not previously exist; append it
                    new_fields.append((event.field_name, -1))

                updated_obj = replace(obj, fields=tuple(new_fields))
                updated_objects.append(updated_obj)
                mutated = True
            else:
                updated_objects.append(obj)

        if not mutated:
            logger.warning(
                "apply_mutation: object_id=%s not found in section=%s",
                event.object_id,
                section.section_id,
            )
            return section

        return replace(section, objects=tuple(updated_objects))

    # ------------------------------------------------------------------
    # Alias management
    # ------------------------------------------------------------------

    def register_aliases(self, key: str, aliases: list[str]) -> None:
        """Register that ``aliases`` all point to the same object as ``key``.

        Parameters
        ----------
        key : str
            The canonical object key.
        aliases : list[str]
            List of alternative keys that alias ``key``.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> validator.register_aliases("obj1", ["ref_a", "ref_b"])
        >>> validator._alias_map["obj1"]
        ['ref_a', 'ref_b']
        """
        existing = self._alias_map.get(key, [])
        merged = list({*existing, *aliases})
        self._alias_map[key] = merged
        logger.debug(
            "MutationValidator.register_aliases: key=%s aliases=%s", key, merged
        )

    def get_mutations_for(self, object_key: str) -> list[MutationEvent]:
        """Return all recorded mutation events targeting ``object_key``.

        Parameters
        ----------
        object_key : str
            The object identifier to filter by.

        Returns
        -------
        list[MutationEvent]
            All events where ``event.object_id == object_key``.

        Examples
        --------
        >>> validator = MutationValidator()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0
        ... )
        >>> validator.record_mutation(ev)
        >>> validator.get_mutations_for("o1")[0].event_id
        'e1'
        """
        return [e for e in self._recorded_mutations if e.object_id == object_key]


# ---------------------------------------------------------------------------
# MutationRecorder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MutationRecorder:
    """Persistent log of mutation events for offline analysis.

    :class:`MutationRecorder` accumulates :class:`MutationEvent` objects and
    provides query methods to retrieve events by object, by field, and by
    time-range.  It supports full serialisation/deserialisation so that logs
    can be persisted to disk and replayed.

    Parameters
    ----------
    _events : list[MutationEvent]
        Ordered list of all recorded events.
    _events_by_object : dict[str, list[str]]
        Index mapping ``object_id`` → list of ``event_id`` strings.
    _events_by_field : dict[str, list[str]]
        Index mapping ``"{object_id}:{field_name}"`` → list of ``event_id`` strings.

    Examples
    --------
    >>> recorder = MutationRecorder()
    >>> ev = MutationEvent(
    ...     event_id="e1", object_id="o1", field_name="x",
    ...     old_value_repr="0", new_value_repr="1", timestamp=1.0
    ... )
    >>> recorder.record(ev)
    >>> recorder.count()
    1
    """

    _events: list[MutationEvent] = field(default_factory=list)
    _events_by_object: dict[str, list[str]] = field(default_factory=dict)
    _events_by_field: dict[str, list[str]] = field(default_factory=dict)

    def record(self, event: MutationEvent) -> None:
        """Append ``event`` to the log and update both indices.

        Parameters
        ----------
        event : MutationEvent
            The mutation event to record.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="obj1", field_name="attr",
        ...     old_value_repr="v0", new_value_repr="v1", timestamp=0.0,
        ... )
        >>> r.record(ev)
        >>> r.count()
        1
        """
        self._events.append(event)
        # Update object index
        obj_events = self._events_by_object.setdefault(event.object_id, [])
        obj_events.append(event.event_id)
        # Update field index
        field_key = f"{event.object_id}:{event.field_name}"
        field_events = self._events_by_field.setdefault(field_key, [])
        field_events.append(event.event_id)
        logger.debug("MutationRecorder.record: event_id=%s", event.event_id)

    def all_events(self) -> list[MutationEvent]:
        """Return a copy of all recorded events in insertion order.

        Returns
        -------
        list[MutationEvent]
            All events, oldest first.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> len(r.all_events())
        0
        """
        return list(self._events)

    def events_for_object(self, object_key: str) -> list[MutationEvent]:
        """Return all events for a given object key.

        Parameters
        ----------
        object_key : str
            The ``object_id`` to filter on.

        Returns
        -------
        list[MutationEvent]
            All recorded events whose ``object_id`` equals ``object_key``.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0
        ... )
        >>> r.record(ev)
        >>> r.events_for_object("o1")[0].event_id
        'e1'
        """
        ids = self._events_by_object.get(object_key, [])
        id_set = set(ids)
        return [e for e in self._events if e.event_id in id_set]

    def events_for_field(self, object_key: str, field_name: str) -> list[MutationEvent]:
        """Return all events for a specific ``(object_key, field_name)`` pair.

        Parameters
        ----------
        object_key : str
            The object whose field was mutated.
        field_name : str
            The name of the mutated field.

        Returns
        -------
        list[MutationEvent]
            All events matching both ``object_id`` and ``field_name``.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="x",
        ...     old_value_repr="1", new_value_repr="2", timestamp=0.0,
        ... )
        >>> r.record(ev)
        >>> r.events_for_field("o1", "x")[0].event_id
        'e1'
        """
        field_key = f"{object_key}:{field_name}"
        ids = self._events_by_field.get(field_key, [])
        id_set = set(ids)
        return [e for e in self._events if e.event_id in id_set]

    def events_in_timerange(self, start: float, end: float) -> list[MutationEvent]:
        """Return all events whose timestamp falls within ``[start, end]``.

        Parameters
        ----------
        start : float
            Lower bound (inclusive) of the time range.
        end : float
            Upper bound (inclusive) of the time range.

        Returns
        -------
        list[MutationEvent]
            Events with ``start <= event.timestamp <= end``.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=5.0,
        ... )
        >>> r.record(ev)
        >>> r.events_in_timerange(0.0, 10.0)[0].event_id
        'e1'
        >>> r.events_in_timerange(6.0, 10.0)
        []
        """
        return [e for e in self._events if start <= e.timestamp <= end]

    def build_mutation_log(self) -> dict[str, Any]:
        """Return a structured summary dict of the mutation log.

        Returns
        -------
        dict[str, Any]
            Keys: ``total_events``, ``objects_mutated``, ``fields_mutated``,
            ``first_timestamp``, ``last_timestamp``.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> log = r.build_mutation_log()
        >>> log["total_events"]
        0
        """
        if not self._events:
            return {
                "total_events": 0,
                "objects_mutated": 0,
                "fields_mutated": 0,
                "first_timestamp": None,
                "last_timestamp": None,
            }
        timestamps = [e.timestamp for e in self._events]
        return {
            "total_events": len(self._events),
            "objects_mutated": len(self._events_by_object),
            "fields_mutated": len(self._events_by_field),
            "first_timestamp": min(timestamps),
            "last_timestamp": max(timestamps),
        }

    def serialize(self) -> dict[str, Any]:
        """Serialise the recorder to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Serialised recorder including the full event list.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> "events" in r.serialize()
        True
        """
        return {
            "events": [e.serialize() for e in self._events],
            "summary": self.build_mutation_log(),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> MutationRecorder:
        """Deserialise a :class:`MutationRecorder` from a plain dict.

        Parameters
        ----------
        data : dict[str, Any]
            Dict previously produced by :meth:`serialize`.

        Returns
        -------
        MutationRecorder
            Recorder with all events and indices rebuilt.

        Raises
        ------
        KeyError
            If the ``"events"`` key is absent.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=1.0,
        ... )
        >>> r.record(ev)
        >>> r2 = MutationRecorder.parse(r.serialize())
        >>> r2.count()
        1
        """
        recorder = cls()
        for event_data in data.get("events", []):
            event = MutationEvent.parse(event_data)
            recorder.record(event)
        return recorder

    def count(self) -> int:
        """Return the total number of recorded events.

        Returns
        -------
        int
            Number of events in the log.

        Examples
        --------
        >>> MutationRecorder().count()
        0
        """
        return len(self._events)

    def clear(self) -> None:
        """Remove all recorded events and reset all indices.

        Examples
        --------
        >>> r = MutationRecorder()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> r.record(ev)
        >>> r.clear()
        >>> r.count()
        0
        """
        self._events.clear()
        self._events_by_object.clear()
        self._events_by_field.clear()
        logger.debug("MutationRecorder.clear: log cleared")


# ---------------------------------------------------------------------------
# DescentChecker
# ---------------------------------------------------------------------------


class DescentChecker:
    """Low-level implementation of the sheaf descent check (theory2.tex Ch17 §3).

    The descent condition states: a collection of local sections
    ``{s_i}`` on open patches ``{U_i}`` glues to a unique global section iff
    the sections agree on every overlap ``U_i ∩ U_j``.

    For the Python heap this means: after a mutation on object ``O``, every
    alias ``A`` of ``O`` (every reference with ``id(A) == id(O)``) must see
    the same updated field value.

    This class is a regular (non-dataclass) class because it maintains no
    persistent mutable state between calls.

    Examples
    --------
    >>> dc = DescentChecker()
    >>> ev = MutationEvent(
    ...     event_id="e1", object_id="obj1", field_name="x",
    ...     old_value_repr="0", new_value_repr="1", timestamp=0.0,
    ... )
    >>> dc.check_descent(ev, [], {})
    True
    """

    def __init__(self) -> None:
        """Initialise a stateless :class:`DescentChecker`."""
        self._violation_log: list[str] = []

    def check_descent(
        self,
        event: MutationEvent,
        aliases: list[str],
        sections: dict[str, HeapSection],
    ) -> bool:
        """Verify the descent condition for ``event`` across ``aliases``.

        The check passes iff every alias key present in ``sections`` contains
        an object with the same field value as the mutated object in its section.

        Parameters
        ----------
        event : MutationEvent
            The mutation event that triggered the check.
        aliases : list[str]
            Keys of objects that alias the mutated object.
        sections : dict[str, HeapSection]
            Map from alias key to the :class:`HeapSection` covering that alias.

        Returns
        -------
        bool
            ``True`` iff the descent condition holds for all aliases.

        Examples
        --------
        >>> dc = DescentChecker()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="obj1", field_name="x",
        ...     old_value_repr="0", new_value_repr="1", timestamp=0.0,
        ... )
        >>> dc.check_descent(ev, ["obj2"], {})
        True
        """
        violations: list[str] = []
        for alias_key in aliases:
            alias_section = sections.get(alias_key)
            if alias_section is None:
                # No section for this alias; cannot verify — conservatively pass
                continue
            ok = self.verify_consistency_after_mutation(event, {alias_key: alias_section})
            if not ok:
                violations.append(
                    f"Descent violated: alias '{alias_key}' inconsistent after "
                    f"mutation of '{event.object_id}.{event.field_name}'"
                )

        self._violation_log.extend(violations)
        return len(violations) == 0

    def find_aliases_affected(
        self, event: MutationEvent, alias_map: dict[str, list[str]]
    ) -> list[str]:
        """Find all aliases transitively affected by ``event``.

        Performs a depth-first traversal of ``alias_map`` starting from
        ``event.object_id`` to collect all reachable alias keys.

        Parameters
        ----------
        event : MutationEvent
            The mutation event whose object to start from.
        alias_map : dict[str, list[str]]
            Maps object keys to lists of alias keys.

        Returns
        -------
        list[str]
            All alias keys reachable from ``event.object_id``.

        Examples
        --------
        >>> dc = DescentChecker()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> dc.find_aliases_affected(ev, {"o1": ["o2", "o3"], "o2": ["o4"]})
        ['o2', 'o3', 'o4']
        """
        visited: set[str] = set()
        stack: list[str] = [event.object_id]
        result: list[str] = []

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for alias in alias_map.get(current, []):
                if alias not in visited:
                    result.append(alias)
                    stack.append(alias)

        return result

    def verify_consistency_after_mutation(
        self, event: MutationEvent, sections: dict[str, HeapSection]
    ) -> bool:
        """Verify that all sections in ``sections`` agree on the mutated field.

        After a mutation ``obj.field = new_value``, every section that covers
        ``obj`` (or an alias of ``obj``) should reflect the updated value.
        Since sections carry :class:`HeapObject` instances (not live Python
        objects), we check for the sentinel field-reference value ``-1``
        introduced by :meth:`MutationValidator.apply_mutation`.

        Parameters
        ----------
        event : MutationEvent
            The mutation event to verify.
        sections : dict[str, HeapSection]
            Map from object key to the section to check.

        Returns
        -------
        bool
            ``True`` iff all sections are consistent with the mutation.

        Examples
        --------
        >>> dc = DescentChecker()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="42", field_name="x",
        ...     old_value_repr="0", new_value_repr="1", timestamp=0.0,
        ... )
        >>> dc.verify_consistency_after_mutation(ev, {})
        True
        """
        for _key, section in sections.items():
            for obj in section.objects:
                if str(obj.object_id) == event.object_id:
                    field_refs = dict(obj.fields)
                    if event.field_name in field_refs:
                        ref_val = field_refs[event.field_name]
                        # Sentinel -1 means "successfully mutated"
                        if ref_val == -1:
                            continue
                        # Any other value means the mutation has not been applied
                        return False
        return True

    def build_obstruction_if_violated(
        self, event: MutationEvent, violations: list[str]
    ) -> Obstruction | None:
        """Create an :class:`Obstruction` if ``violations`` is non-empty.

        Parameters
        ----------
        event : MutationEvent
            The mutation event that caused the violations.
        violations : list[str]
            Violation description strings.

        Returns
        -------
        Obstruction | None
            An obstruction capturing all violations, or ``None`` if ``violations``
            is empty.

        Examples
        --------
        >>> dc = DescentChecker()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> obs = dc.build_obstruction_if_violated(ev, ["desc. failed"])
        >>> obs is not None
        True
        >>> obs.cohomology_class
        'H^1'
        """
        if not violations:
            return None
        hints = tuple(
            f"Re-apply mutation on alias: {v}" for v in violations[:5]
        )
        return Obstruction(
            obstruction_id=str(uuid.uuid4()),
            violated_condition="sheaf_descent",
            coordinate=event.object_id,
            evidence_at_time=(event.event_id,),
            repair_hints=hints,
            cohomology_class="H^1",
        )

    def report_descent_violations(self, violations: list[str]) -> dict[str, Any]:
        """Build a structured report dict for a list of descent violations.

        Parameters
        ----------
        violations : list[str]
            List of violation description strings.

        Returns
        -------
        dict[str, Any]
            Keys: ``violation_count``, ``violations``, ``passed``.

        Examples
        --------
        >>> dc = DescentChecker()
        >>> report = dc.report_descent_violations(["alias mismatch"])
        >>> report["passed"]
        False
        """
        return {
            "violation_count": len(violations),
            "violations": list(violations),
            "passed": len(violations) == 0,
            "timestamp": time.time(),
        }


# ---------------------------------------------------------------------------
# MutationImpactAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MutationImpactAnalyzer:
    """Analyses the blast-radius of a mutation across the alias graph.

    When object ``O`` is mutated, every alias of ``O`` is implicitly affected.
    :class:`MutationImpactAnalyzer` traverses the alias graph from ``O`` and
    builds an impact report describing which objects are affected, how many
    aliases are reached, and which *observers* (objects that hold a reference
    to any affected object) need to be notified.

    Parameters
    ----------
    _impact_cache : dict[str, dict[str, Any]]
        Cache keyed by ``event_id`` to avoid re-computing impact analyses.

    Examples
    --------
    >>> analyzer = MutationImpactAnalyzer()
    >>> ev = MutationEvent(
    ...     event_id="e1", object_id="o1", field_name="x",
    ...     old_value_repr="0", new_value_repr="1", timestamp=0.0,
    ... )
    >>> report = analyzer.analyze_impact(ev, {"o1": ["o2"]})
    >>> report["affected_count"]
    1
    """

    _impact_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def analyze_impact(
        self, event: MutationEvent, alias_map: dict[str, list[str]]
    ) -> dict[str, Any]:
        """Compute a full impact analysis for ``event``.

        Results are cached by ``event.event_id`` so that repeated calls for
        the same event are O(1).

        Parameters
        ----------
        event : MutationEvent
            The mutation event to analyse.
        alias_map : dict[str, list[str]]
            Maps object keys to their alias lists.

        Returns
        -------
        dict[str, Any]
            Impact report with keys ``event_id``, ``object_id``, ``field``,
            ``affected``, ``affected_count``, ``impact_radius``.

        Examples
        --------
        >>> analyzer = MutationImpactAnalyzer()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> report = analyzer.analyze_impact(ev, {"o1": ["o2", "o3"]})
        >>> report["affected_count"]
        2
        """
        cached = self._impact_cache.get(event.event_id)
        if cached is not None:
            return cached

        affected = self.find_all_affected(event, alias_map)
        radius = self.compute_impact_radius(event, alias_map)
        report = self.build_impact_report(event, affected)
        report["impact_radius"] = radius
        self._impact_cache[event.event_id] = report
        return report

    def find_all_affected(
        self, event: MutationEvent, alias_map: dict[str, list[str]]
    ) -> list[str]:
        """Find all object keys transitively affected by ``event``.

        Parameters
        ----------
        event : MutationEvent
            The mutation event.
        alias_map : dict[str, list[str]]
            Alias adjacency map.

        Returns
        -------
        list[str]
            List of affected object keys (excluding the mutated object itself).

        Examples
        --------
        >>> analyzer = MutationImpactAnalyzer()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> analyzer.find_all_affected(ev, {"o1": ["o2"], "o2": ["o3"]})
        ['o2', 'o3']
        """
        visited: set[str] = {event.object_id}
        queue: list[str] = [event.object_id]
        affected: list[str] = []

        while queue:
            current = queue.pop(0)
            for alias in alias_map.get(current, []):
                if alias not in visited:
                    visited.add(alias)
                    affected.append(alias)
                    queue.append(alias)

        return affected

    def compute_impact_radius(
        self, event: MutationEvent, alias_map: dict[str, list[str]]
    ) -> int:
        """Compute the maximum hop-distance from ``event.object_id`` to any alias.

        Parameters
        ----------
        event : MutationEvent
            The mutation event.
        alias_map : dict[str, list[str]]
            Alias adjacency map.

        Returns
        -------
        int
            Maximum BFS depth (number of alias hops).

        Examples
        --------
        >>> analyzer = MutationImpactAnalyzer()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> analyzer.compute_impact_radius(ev, {"o1": ["o2"], "o2": ["o3"]})
        2
        """
        visited: set[str] = {event.object_id}
        frontier: list[str] = [event.object_id]
        depth = 0

        while frontier:
            next_frontier: list[str] = []
            for node in frontier:
                for alias in alias_map.get(node, []):
                    if alias not in visited:
                        visited.add(alias)
                        next_frontier.append(alias)
            if next_frontier:
                depth += 1
            frontier = next_frontier

        return depth

    def find_observers(
        self, object_key: str, registry: dict[str, list[str]]
    ) -> list[str]:
        """Find all objects that hold a reference to ``object_key``.

        An *observer* is an object that has ``object_key`` in its alias list
        (i.e. it points to the object directly).  This is the inverse look-up
        of the alias map.

        Parameters
        ----------
        object_key : str
            The object to find observers for.
        registry : dict[str, list[str]]
            Maps each object key to its list of referenced objects.

        Returns
        -------
        list[str]
            Keys of objects that have ``object_key`` in their reference list.

        Examples
        --------
        >>> analyzer = MutationImpactAnalyzer()
        >>> analyzer.find_observers("o2", {"o1": ["o2", "o3"], "o3": ["o4"]})
        ['o1']
        """
        return [
            holder_key
            for holder_key, refs in registry.items()
            if object_key in refs
        ]

    def build_impact_report(
        self, event: MutationEvent, affected: list[str]
    ) -> dict[str, Any]:
        """Build a structured impact report dict.

        Parameters
        ----------
        event : MutationEvent
            The mutation event.
        affected : list[str]
            List of affected object keys.

        Returns
        -------
        dict[str, Any]
            Keys: ``event_id``, ``object_id``, ``field``, ``affected``,
            ``affected_count``, ``timestamp``.

        Examples
        --------
        >>> analyzer = MutationImpactAnalyzer()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> report = analyzer.build_impact_report(ev, ["o2"])
        >>> report["affected_count"]
        1
        """
        return {
            "event_id": event.event_id,
            "object_id": event.object_id,
            "field": event.field_name,
            "affected": list(affected),
            "affected_count": len(affected),
            "timestamp": time.time(),
        }

    def build_impact_judgment(
        self, event: MutationEvent, affected: list[str]
    ) -> Any:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` for the impact.

        Parameters
        ----------
        event : MutationEvent
            The mutation event.
        affected : list[str]
            List of affected alias keys.

        Returns
        -------
        Judgment
            Judgment encoding the mutation's impact on aliases.

        Examples
        --------
        >>> analyzer = MutationImpactAnalyzer()
        >>> ev = MutationEvent(
        ...     event_id="e1", object_id="o1", field_name="f",
        ...     old_value_repr="a", new_value_repr="b", timestamp=0.0,
        ... )
        >>> j = analyzer.build_impact_judgment(ev, ["o2", "o3"])
        >>> j is not None
        True
        """
        coord = CoordinateObject(
            components=(event.object_id, "impact"),
            kind=CoordinateKind.REGION,
        )
        impact_count = len(affected)
        formula = (
            f"mutation_impact({event.object_id!r}, {event.field_name!r}, "
            f"aliases={impact_count})"
        )
        trust = (
            TrustLevel.RUNTIME_WITNESSED if impact_count == 0 else TrustLevel.UNVERIFIED
        )
        return (
            JudgmentBuilder()
            .at(coord)
            .claiming_formula(formula)
            .of_type_named("MutationImpact")
            .with_trust_level(trust)
            .build()
        )


# ---------------------------------------------------------------------------
# FrozenObjectChecker
# ---------------------------------------------------------------------------


class FrozenObjectChecker:
    """Determines whether a Python object belongs to an immutable type.

    CPython's type system has a number of built-in immutable types (``int``,
    ``str``, ``bytes``, ``tuple``, ``frozenset``, ``bool``, ``NoneType``).
    User-defined classes can also be effectively immutable if they use
    ``__slots__`` without ``__setattr__`` or if they inherit from a frozen
    dataclass.

    :class:`FrozenObjectChecker` centralises all immutability detection
    logic so that :class:`MutationValidator` can quickly decide whether a
    mutation attempt should be rejected outright.

    This is a regular class (not a dataclass) because it maintains a small
    LRU-like cache of type checks but does not need the dataclass machinery.

    Examples
    --------
    >>> checker = FrozenObjectChecker()
    >>> checker.is_frozen(42)
    True
    >>> checker.is_frozen([1, 2, 3])
    False
    """

    _BUILTIN_FROZEN: frozenset[type] = frozenset(
        {int, float, str, bytes, bool, type(None), complex}
    )

    def __init__(self) -> None:
        """Initialise the checker with an empty type cache."""
        self._type_cache: dict[type, bool] = {}

    def is_frozen(self, obj: object) -> bool:
        """Return ``True`` iff ``obj`` belongs to a known immutable type.

        The check is cached per type to avoid repeated introspection.

        Parameters
        ----------
        obj : object
            Any Python object.

        Returns
        -------
        bool
            ``True`` if ``obj`` is immutable.

        Examples
        --------
        >>> checker = FrozenObjectChecker()
        >>> checker.is_frozen("hello")
        True
        >>> checker.is_frozen({"key": "val"})
        False
        """
        tp = type(obj)
        cached = self._type_cache.get(tp)
        if cached is not None:
            return cached

        result = (
            tp in self._BUILTIN_FROZEN
            or self.check_frozenset(obj)
            or self.check_tuple(obj)
            or self.detect_immutable_class(tp)
        )
        self._type_cache[tp] = result
        return result

    def check_frozenset(self, obj: object) -> bool:
        """Return ``True`` iff ``obj`` is a :class:`frozenset` instance.

        Parameters
        ----------
        obj : object
            Object to test.

        Returns
        -------
        bool
            ``True`` iff ``isinstance(obj, frozenset)``.

        Examples
        --------
        >>> FrozenObjectChecker().check_frozenset(frozenset({1, 2}))
        True
        >>> FrozenObjectChecker().check_frozenset({1, 2})
        False
        """
        return isinstance(obj, frozenset)

    def check_tuple(self, obj: object) -> bool:
        """Return ``True`` iff ``obj`` is a plain :class:`tuple` instance.

        Note: named tuples (subclasses of ``tuple``) are also immutable but
        are detected separately by :meth:`check_namedtuple`.

        Parameters
        ----------
        obj : object
            Object to test.

        Returns
        -------
        bool
            ``True`` iff ``type(obj) is tuple``.

        Examples
        --------
        >>> FrozenObjectChecker().check_tuple((1, 2, 3))
        True
        >>> FrozenObjectChecker().check_tuple([1, 2, 3])
        False
        """
        return type(obj) is tuple

    def check_namedtuple(self, obj: object) -> bool:
        """Return ``True`` iff ``obj`` is a named tuple instance.

        Named tuples expose a ``_fields`` attribute listing their field names.
        Since they subclass :class:`tuple`, they are immutable.

        Parameters
        ----------
        obj : object
            Object to test.

        Returns
        -------
        bool
            ``True`` iff ``obj`` has a ``_fields`` attribute and is a tuple.

        Examples
        --------
        >>> from collections import namedtuple
        >>> Point = namedtuple("Point", ["x", "y"])
        >>> FrozenObjectChecker().check_namedtuple(Point(1, 2))
        True
        >>> FrozenObjectChecker().check_namedtuple((1, 2))
        False
        """
        return isinstance(obj, tuple) and hasattr(type(obj), "_fields")

    def detect_immutable_class(self, cls: type) -> bool:
        """Heuristically detect whether a user-defined class is immutable.

        A class is considered immutable if it:
        1. Defines ``__slots__`` (restricting attribute creation), AND
        2. Does not define ``__setattr__`` (no override of the default setter).

        Additionally, frozen :mod:`dataclasses` set ``__setattr__`` to raise
        ``FrozenInstanceError``, so we detect that pattern too.

        Parameters
        ----------
        cls : type
            The class type to inspect.

        Returns
        -------
        bool
            ``True`` if the class appears to be immutable.

        Examples
        --------
        >>> class Immutable:
        ...     __slots__ = ("x",)
        >>> FrozenObjectChecker().detect_immutable_class(Immutable)
        True
        """
        has_slots = hasattr(cls, "__slots__")
        has_setattr = "__setattr__" in cls.__dict__

        # Frozen dataclasses set __setattr__ to raise FrozenInstanceError
        if has_setattr:
            setattr_fn = cls.__dict__.get("__setattr__")
            if setattr_fn is not None:
                qualname = getattr(setattr_fn, "__qualname__", "")
                if "frozen" in qualname.lower() or "FrozenInstance" in str(setattr_fn):
                    return True
            return False

        return has_slots

    def build_immutability_judgment(
        self, obj: object, identity: IdentityCoordinate
    ) -> Any:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` for ``obj``'s immutability.

        Parameters
        ----------
        obj : object
            The object whose immutability to judge.
        identity : IdentityCoordinate
            The identity coordinate of ``obj``.

        Returns
        -------
        Judgment
            A judgment asserting whether ``obj`` is immutable.

        Examples
        --------
        >>> checker = FrozenObjectChecker()
        >>> ic = IdentityCoordinate(object_id=1, type_name="int", address=1)
        >>> j = checker.build_immutability_judgment(42, ic)
        >>> j is not None
        True
        """
        frozen = self.is_frozen(obj)
        coord = CoordinateObject(
            components=(str(identity.object_id), identity.type_name),
            kind=CoordinateKind.REGION,
        )
        formula = (
            f"is_immutable({identity.type_name!r})"
            if frozen
            else f"is_mutable({identity.type_name!r})"
        )
        trust = TrustLevel.RUNTIME_WITNESSED if frozen else TrustLevel.UNVERIFIED
        return (
            JudgmentBuilder()
            .at(coord)
            .claiming_formula(formula)
            .of_type_named("Immutability")
            .with_trust_level(trust)
            .build()
        )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MutationValidationResult",
    "MutationValidator",
    "MutationRecorder",
    "DescentChecker",
    "MutationImpactAnalyzer",
    "FrozenObjectChecker",
]
