from __future__ import annotations

"""s04 — Monkey Patching and Late Rebinding (Ch23 §4).

Monkey patching creates late-binding obstructions in the sheaf of types.
When an attribute is replaced at runtime, the type-theoretic section
over that module becomes inconsistent — an obstruction in the sheaf sense.
This module tracks rebinding events, detects obstructions, and witnesses
the severity of late-binding mutations.
"""

import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.sheaf import ObstructionDetector  # type: ignore
except ImportError:
    class ObstructionDetector:  # type: ignore
        """Inline stub for jugeo.sheaf.ObstructionDetector.

        This stub is used when the jugeo.sheaf module is not available.
        It provides the minimum interface required by this module.
        """

        def detect(self, module: str, attr: str) -> bool:
            """Detect an obstruction in the given module attribute.

            Args:
                module: The module name to inspect.
                attr: The attribute name within the module.

            Returns:
                Always False in the stub implementation.
            """
            return False

try:
    from jugeo.types import TypeSection  # type: ignore
except ImportError:
    class TypeSection:  # type: ignore
        """Inline stub for jugeo.types.TypeSection.

        This stub is used when the jugeo.types module is not available.
        It provides the minimum interface required by this module.
        """

        def __init__(self, type_name: str = "") -> None:
            """Initialise the stub TypeSection.

            Args:
                type_name: The name of the type this section represents.
            """
            self.type_name = type_name

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _new_record_id() -> str:
    """Generate a unique rebinding-record identifier.

    Returns:
        A string of the form ``"rb_<10 hex chars>"``.

    Example:
        >>> rid = _new_record_id()
        >>> rid.startswith("rb_")
        True
        >>> len(rid)
        13
    """
    return "rb_" + uuid.uuid4().hex[:10]


def _new_obstruction_id() -> str:
    """Generate a unique obstruction identifier.

    Returns:
        A string of the form ``"ob_<10 hex chars>"``.

    Example:
        >>> oid = _new_obstruction_id()
        >>> oid.startswith("ob_")
        True
    """
    return "ob_" + uuid.uuid4().hex[:10]


def _new_witness_id() -> str:
    """Generate a unique witness identifier.

    Returns:
        A string of the form ``"wt_<10 hex chars>"``.

    Example:
        >>> wid = _new_witness_id()
        >>> wid.startswith("wt_")
        True
    """
    return "wt_" + uuid.uuid4().hex[:10]


def _severity_from_type_change(old_type: str | None, new_type: str) -> int:
    """Compute a heuristic severity score for a type-change rebinding.

    The scoring logic attempts to capture how disruptive a particular
    type substitution is likely to be at runtime.  The scale runs from
    0 (no change) to 4 (completely different, unrelated types).

    Args:
        old_type: The ``__name__`` of the previous type, or ``None`` if the
            attribute did not exist before the rebinding.
        new_type: The ``__name__`` of the replacement type.

    Returns:
        An integer severity in the range ``[0, 4]``:

        * ``0`` — old_type and new_type are identical (no semantic change).
        * ``1`` — old_type was ``None`` (new binding, not a replacement).
        * ``2`` — one side is a ``"function"`` and the other is a ``"method"``,
          indicating a mild structural difference.
        * ``3`` — one of the types involves ``NoneType``, suggesting a
          nullability shift.
        * ``4`` — arbitrary incompatible type swap.

    Example:
        >>> _severity_from_type_change(None, "int")
        1
        >>> _severity_from_type_change("int", "int")
        0
        >>> _severity_from_type_change("function", "method")
        2
        >>> _severity_from_type_change("NoneType", "str")
        3
        >>> _severity_from_type_change("str", "int")
        4
    """
    if old_type is None:
        return 1
    if old_type == new_type:
        return 0
    if "NoneType" in old_type or "NoneType" in new_type:
        return 3
    function_like = {"function", "method", "builtin_function_or_method", "method-wrapper"}
    if old_type in function_like and new_type in function_like:
        return 2
    return 4


def _obstruction_kind_from_rebinding(record: RebindingRecord) -> str:
    """Determine the obstruction kind string from a rebinding record.

    This function maps a :class:`RebindingKind` value (and supporting
    metadata on the record) to a human-readable obstruction-kind label
    that can be converted into an :class:`ObstructionKind` enum member.

    Args:
        record: The :class:`RebindingRecord` whose rebinding kind should
            be examined.

    Returns:
        A lowercase string matching one of the :class:`ObstructionKind`
        member names:

        * ``"type_inconsistency"`` — for :attr:`RebindingKind.METHOD_REPLACEMENT`.
        * ``"class_divergence"`` — for :attr:`RebindingKind.CLASS_MUTATION`.
        * ``"descriptor_conflict"`` — for :attr:`RebindingKind.DESCRIPTOR_OVERRIDE`.
        * ``"module_leak"`` — for :attr:`RebindingKind.MODULE_ATTRIBUTE`.
        * ``"binding_cycle"`` — for :attr:`RebindingKind.ATTRIBUTE_SWAP` or any
          unrecognised kind.

    Example:
        >>> from types import SimpleNamespace
        >>> r = SimpleNamespace(kind=RebindingKind.METHOD_REPLACEMENT)
        >>> _obstruction_kind_from_rebinding(r)  # doctest: +SKIP
        'type_inconsistency'
    """
    mapping: dict[RebindingKind, str] = {
        RebindingKind.METHOD_REPLACEMENT: "type_inconsistency",
        RebindingKind.CLASS_MUTATION: "class_divergence",
        RebindingKind.DESCRIPTOR_OVERRIDE: "descriptor_conflict",
        RebindingKind.MODULE_ATTRIBUTE: "module_leak",
        RebindingKind.ATTRIBUTE_SWAP: "binding_cycle",
    }
    return mapping.get(record.kind, "binding_cycle")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RebindingKind(str, Enum):
    """Categorises the nature of a runtime attribute rebinding.

    Each variant represents a distinct mechanism through which a name in a
    module's or class's namespace can be replaced after the original binding
    was established.

    Attributes:
        METHOD_REPLACEMENT: A method on a class or instance is replaced with
            a new callable, typically a function or lambda.
        ATTRIBUTE_SWAP: A plain data attribute is overwritten with a value of
            possibly a different type.
        CLASS_MUTATION: The class object itself — or its ``__dict__`` — is
            mutated (e.g. adding/replacing a class-level variable).
        MODULE_ATTRIBUTE: An attribute in a module's global namespace is
            rebound, potentially affecting all importers of that module.
        DESCRIPTOR_OVERRIDE: A descriptor (``__get__``/``__set__``/
            ``__delete__``) is replaced with a non-descriptor or a different
            descriptor implementation.
    """

    METHOD_REPLACEMENT = "METHOD_REPLACEMENT"
    ATTRIBUTE_SWAP = "ATTRIBUTE_SWAP"
    CLASS_MUTATION = "CLASS_MUTATION"
    MODULE_ATTRIBUTE = "MODULE_ATTRIBUTE"
    DESCRIPTOR_OVERRIDE = "DESCRIPTOR_OVERRIDE"


class ObstructionKind(str, Enum):
    """Categorises the theoretical nature of a sheaf obstruction.

    In the sheaf-of-types model, an obstruction arises when local sections
    (type assignments for individual attributes) cannot be glued into a
    globally consistent section over the module.  Each variant identifies
    a distinct failure mode.

    Attributes:
        TYPE_INCONSISTENCY: The type of an attribute changed in an
            incompatible way, making the local section inconsistent with
            downstream consumers.
        CLASS_DIVERGENCE: The class definition diverged from the original
            type signature, potentially breaking subclass contracts.
        DESCRIPTOR_CONFLICT: A descriptor protocol was violated, e.g. a
            ``__get__`` replaced by a plain function.
        MODULE_LEAK: A module-level rebinding propagated unexpected type
            changes to all importers ("leaked" through the module boundary).
        BINDING_CYCLE: A cyclic chain of rebidings was detected, where
            attribute A depends on B which was later rebound back toward A.
    """

    TYPE_INCONSISTENCY = "TYPE_INCONSISTENCY"
    CLASS_DIVERGENCE = "CLASS_DIVERGENCE"
    DESCRIPTOR_CONFLICT = "DESCRIPTOR_CONFLICT"
    MODULE_LEAK = "MODULE_LEAK"
    BINDING_CYCLE = "BINDING_CYCLE"


# ---------------------------------------------------------------------------
# Value-type dataclasses (frozen, slots)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RebindingRecord:
    """An immutable record of a single attribute-rebinding event.

    A :class:`RebindingRecord` is created each time an attribute is
    replaced at runtime.  It captures enough information to reconstruct
    the sequence of mutations applied to a given name and to detect
    obstructions in the sheaf of types.

    Attributes:
        record_id: A unique identifier for this record (``"rb_..."``).
        module_name: The fully-qualified module (or class) in which the
            rebinding occurred, e.g. ``"mypackage.utils"``.
        attribute: The name of the attribute that was rebound.
        old_type_name: The ``type.__name__`` of the previous value, or
            ``None`` if the attribute did not exist before.
        new_type_name: The ``type.__name__`` of the replacement value.
        kind: The :class:`RebindingKind` that best describes how the
            rebinding was performed.
        is_late_binding: ``True`` if the rebinding occurred after the
            module was first imported (i.e. a genuine monkey-patch).
        rebind_at: Unix timestamp (from :func:`time.time`) recording when
            the rebinding was observed.
        reverted_at: Unix timestamp of when the rebinding was reverted, or
            ``None`` if it is still active.
    """

    record_id: str
    module_name: str
    attribute: str
    old_type_name: str | None
    new_type_name: str
    kind: RebindingKind
    is_late_binding: bool
    rebind_at: float
    reverted_at: float | None

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    def age(self) -> float:
        """Return the elapsed time (seconds) since this rebinding was recorded.

        Returns:
            A non-negative float representing the age of this record in
            seconds.  For very recent records this may be close to zero.

        Example:
            >>> import time
            >>> r = RebindingRecord(
            ...     record_id="rb_0000000000",
            ...     module_name="mymod",
            ...     attribute="fn",
            ...     old_type_name=None,
            ...     new_type_name="function",
            ...     kind=RebindingKind.METHOD_REPLACEMENT,
            ...     is_late_binding=True,
            ...     rebind_at=time.time() - 5.0,
            ...     reverted_at=None,
            ... )
            >>> r.age() >= 5.0
            True
        """
        return time.time() - self.rebind_at

    def is_active(self) -> bool:
        """Return ``True`` if this rebinding has not been reverted.

        A rebinding is considered active when :attr:`reverted_at` is
        ``None``, meaning no revert event has been recorded for it.

        Returns:
            Boolean indicating whether the rebinding is still in effect.

        Example:
            >>> r = RebindingRecord(
            ...     record_id="rb_0000000001",
            ...     module_name="mymod",
            ...     attribute="fn",
            ...     old_type_name=None,
            ...     new_type_name="function",
            ...     kind=RebindingKind.METHOD_REPLACEMENT,
            ...     is_late_binding=True,
            ...     rebind_at=time.time(),
            ...     reverted_at=None,
            ... )
            >>> r.is_active()
            True
        """
        return self.reverted_at is None

    def type_changed(self) -> bool:
        """Return ``True`` if the type of the attribute changed during rebinding.

        Compares :attr:`old_type_name` to :attr:`new_type_name`.  If
        :attr:`old_type_name` is ``None`` (new attribute), the type is
        considered to have changed.

        Returns:
            Boolean indicating a type-level change.

        Example:
            >>> r = RebindingRecord(
            ...     record_id="rb_abc",
            ...     module_name="m",
            ...     attribute="x",
            ...     old_type_name="int",
            ...     new_type_name="str",
            ...     kind=RebindingKind.ATTRIBUTE_SWAP,
            ...     is_late_binding=False,
            ...     rebind_at=time.time(),
            ...     reverted_at=None,
            ... )
            >>> r.type_changed()
            True
        """
        return self.old_type_name != self.new_type_name

    def label(self) -> str:
        """Return a compact human-readable label for this record.

        The label encodes the module, attribute name, and the type
        transition in a single string suitable for logging and reports.

        Returns:
            A string in the form
            ``"<module_name>.<attribute>(<old_type_name>→<new_type_name>)"``.

        Example:
            >>> r.label()  # doctest: +SKIP
            'mymod.fn(None→function)'
        """
        return f"{self.module_name}.{self.attribute}({self.old_type_name}→{self.new_type_name})"

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain :class:`dict`.

        All fields are included, with enum members converted to their
        string values and timestamps preserved as floats.

        Returns:
            A dictionary containing all record fields suitable for JSON
            serialisation.

        Example:
            >>> d = r.to_dict()
            >>> d["record_id"].startswith("rb_")
            True
        """
        return {
            "record_id": self.record_id,
            "module_name": self.module_name,
            "attribute": self.attribute,
            "old_type_name": self.old_type_name,
            "new_type_name": self.new_type_name,
            "kind": self.kind.value,
            "is_late_binding": self.is_late_binding,
            "rebind_at": self.rebind_at,
            "reverted_at": self.reverted_at,
            "age": self.age(),
            "is_active": self.is_active(),
            "type_changed": self.type_changed(),
            "label": self.label(),
        }


@dataclass(frozen=True, slots=True)
class ObstructionRecord:
    """An immutable record describing a detected sheaf obstruction.

    An obstruction is created when a rebinding event is found to produce
    an inconsistency in the type-theoretic section over a module.  It
    carries provenance information linking it back to the causal
    :class:`RebindingRecord`.

    Attributes:
        obstruction_id: Unique identifier for this obstruction (``"ob_..."``).
        affected_module: The module in which the obstruction was detected.
        affected_attribute: The specific attribute whose type section became
            inconsistent.
        obstruction_kind: The :class:`ObstructionKind` classifying the
            failure mode.
        severity: An integer from 0 to 10 indicating how disruptive the
            obstruction is expected to be.  Values ≥ 7 are considered
            critical.
        detected_at: Unix timestamp when the obstruction was first detected.
        source_record_id: The :attr:`RebindingRecord.record_id` that caused
            this obstruction.
    """

    obstruction_id: str
    affected_module: str
    affected_attribute: str
    obstruction_kind: ObstructionKind
    severity: int
    detected_at: float
    source_record_id: str

    def label(self) -> str:
        """Return a concise label encoding the obstruction identity and severity.

        Returns:
            A string of the form
            ``"obstruction[<id>]@<module>.<attr>(sev=<severity>)"``.

        Example:
            >>> obs.label()  # doctest: +SKIP
            'obstruction[ob_abc1234567]@mymod.fn(sev=4)'
        """
        return (
            f"obstruction[{self.obstruction_id}]"
            f"@{self.affected_module}.{self.affected_attribute}"
            f"(sev={self.severity})"
        )

    def is_critical(self) -> bool:
        """Return ``True`` if the severity of this obstruction is critical.

        Obstructions with :attr:`severity` ≥ 7 are considered critical and
        should be surfaced immediately in reports.

        Returns:
            Boolean indicating critical severity.

        Example:
            >>> obs.is_critical()  # doctest: +SKIP
            False
        """
        return self.severity >= 7

    def age(self) -> float:
        """Return the elapsed time (seconds) since this obstruction was detected.

        Returns:
            A non-negative float representing the age of this obstruction.

        Example:
            >>> obs.age() >= 0.0
            True
        """
        return time.time() - self.detected_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise this obstruction record to a plain :class:`dict`.

        Returns:
            A dictionary containing all obstruction fields suitable for
            JSON serialisation.

        Example:
            >>> d = obs.to_dict()
            >>> "obstruction_id" in d
            True
        """
        return {
            "obstruction_id": self.obstruction_id,
            "affected_module": self.affected_module,
            "affected_attribute": self.affected_attribute,
            "obstruction_kind": self.obstruction_kind.value,
            "severity": self.severity,
            "detected_at": self.detected_at,
            "source_record_id": self.source_record_id,
            "is_critical": self.is_critical(),
            "age": self.age(),
            "label": self.label(),
        }


@dataclass(frozen=True, slots=True)
class RebindingChain:
    """An immutable record representing a chain of rebidings for one attribute.

    When the same attribute is rebound multiple times, the sequence of
    :class:`RebindingRecord` identifiers forms a *chain*.  This dataclass
    captures that chain along with its computed depth.

    Attributes:
        chain_id: Unique identifier for this chain (``"rb_chain_..."``).
        module_name: The module containing the repeatedly-rebound attribute.
        attribute: The attribute name.
        record_ids: An ordered tuple of :attr:`RebindingRecord.record_id`
            values, earliest first.
        chain_depth: The number of distinct rebinding events in the chain
            (equal to ``len(record_ids)``).
        computed_at: Unix timestamp when this chain object was created.
    """

    chain_id: str
    module_name: str
    attribute: str
    record_ids: tuple[str, ...]
    chain_depth: int
    computed_at: float

    def label(self) -> str:
        """Return a human-readable label for this chain.

        Returns:
            A string of the form
            ``"chain[<module>.<attr>] depth=<chain_depth>"``.

        Example:
            >>> ch.label()  # doctest: +SKIP
            'chain[mymod.fn] depth=3'
        """
        return f"chain[{self.module_name}.{self.attribute}] depth={self.chain_depth}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise this chain to a plain :class:`dict`.

        Returns:
            A dictionary containing all chain fields suitable for JSON
            serialisation.

        Example:
            >>> d = ch.to_dict()
            >>> d["chain_depth"] == len(d["record_ids"])
            True
        """
        return {
            "chain_id": self.chain_id,
            "module_name": self.module_name,
            "attribute": self.attribute,
            "record_ids": list(self.record_ids),
            "chain_depth": self.chain_depth,
            "computed_at": self.computed_at,
            "label": self.label(),
        }


# ---------------------------------------------------------------------------
# Mutable manager: LateRebindingAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class LateRebindingAnalyzer:
    """Tracks and analyses late-binding rebinding events across modules.

    The analyser maintains an append-only log of :class:`RebindingRecord`
    objects together with auxiliary indexes that support efficient queries
    for cascading rebidings, type-stability scores, and obstruction
    detection.

    Attributes:
        _records: All rebinding records in insertion order.
        _obstructions: All obstruction records detected so far.
        _chains: Maps ``"<module>.<attr>"`` to the list of record IDs that
            form the rebinding chain for that attribute.
        _reverted: Set of record IDs that have been reverted.
    """

    _records: list[RebindingRecord] = field(default_factory=list)
    _obstructions: list[ObstructionRecord] = field(default_factory=list)
    _chains: dict[str, list[str]] = field(default_factory=dict)
    _reverted: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def record_rebinding(
        self,
        module_name: str,
        attribute: str,
        old_type: str | None,
        new_type: str,
        is_late: bool,
        kind: RebindingKind | None = None,
    ) -> RebindingRecord:
        """Record a single attribute-rebinding event.

        If ``kind`` is not provided, it is inferred from the type names
        using a simple heuristic:

        * ``new_type`` contains ``"function"`` or ``"method"`` → :attr:`RebindingKind.METHOD_REPLACEMENT`
        * ``new_type`` contains ``"type"`` or ``"class"`` → :attr:`RebindingKind.CLASS_MUTATION`
        * ``new_type`` contains ``"property"`` or ``"descriptor"`` → :attr:`RebindingKind.DESCRIPTOR_OVERRIDE`
        * attribute starts with a module-path pattern → :attr:`RebindingKind.MODULE_ATTRIBUTE`
        * otherwise → :attr:`RebindingKind.ATTRIBUTE_SWAP`

        The new record is appended to :attr:`_records` and its ID is
        appended to the chain for ``"<module_name>.<attribute>"``.

        Args:
            module_name: The module (or class path) where the rebinding
                occurred.
            attribute: The name of the attribute being rebound.
            old_type: The ``type.__name__`` of the previous value, or
                ``None`` if it did not exist.
            new_type: The ``type.__name__`` of the new value.
            is_late: Whether the rebinding occurred after initial import.
            kind: Explicit :class:`RebindingKind`, or ``None`` to
                auto-detect.

        Returns:
            The newly created and stored :class:`RebindingRecord`.

        Example:
            >>> analyzer = LateRebindingAnalyzer()
            >>> rec = analyzer.record_rebinding(
            ...     "mymod", "helper", "int", "function", True
            ... )
            >>> rec.kind == RebindingKind.METHOD_REPLACEMENT
            True
        """
        if kind is None:
            nt_lower = new_type.lower()
            if "function" in nt_lower or "method" in nt_lower:
                kind = RebindingKind.METHOD_REPLACEMENT
            elif "type" in nt_lower or "class" in nt_lower:
                kind = RebindingKind.CLASS_MUTATION
            elif "property" in nt_lower or "descriptor" in nt_lower:
                kind = RebindingKind.DESCRIPTOR_OVERRIDE
            elif re.search(r"\.", module_name):
                kind = RebindingKind.MODULE_ATTRIBUTE
            else:
                kind = RebindingKind.ATTRIBUTE_SWAP

        record = RebindingRecord(
            record_id=_new_record_id(),
            module_name=module_name,
            attribute=attribute,
            old_type_name=old_type,
            new_type_name=new_type,
            kind=kind,
            is_late_binding=is_late,
            rebind_at=time.time(),
            reverted_at=None,
        )
        self._records.append(record)
        chain_key = f"{module_name}.{attribute}"
        if chain_key not in self._chains:
            self._chains[chain_key] = []
        self._chains[chain_key].append(record.record_id)
        _log.debug(
            "Recorded rebinding %s for %s.%s (%s → %s), kind=%s, late=%s",
            record.record_id,
            module_name,
            attribute,
            old_type,
            new_type,
            kind.value,
            is_late,
        )
        return record

    def detect_late_bindings(
        self, records: list[RebindingRecord]
    ) -> list[RebindingRecord]:
        """Filter a list of records to those representing active late bindings.

        A record is included in the result if both of the following hold:

        * :attr:`RebindingRecord.is_late_binding` is ``True``.
        * :meth:`RebindingRecord.is_active` returns ``True`` (i.e. not
          yet reverted).

        Args:
            records: The list of :class:`RebindingRecord` objects to
                filter.

        Returns:
            A (possibly empty) list containing only the active late-binding
            records from the input.

        Example:
            >>> analyzer = LateRebindingAnalyzer()
            >>> late = analyzer.detect_late_bindings(analyzer._records)
            >>> all(r.is_late_binding and r.is_active() for r in late)
            True
        """
        late = [r for r in records if r.is_late_binding and r.is_active()]
        _log.debug("Detected %d active late bindings from %d input records", len(late), len(records))
        return late

    def compute_obstruction(
        self, record: RebindingRecord
    ) -> ObstructionRecord | None:
        """Compute and store an obstruction record for a rebinding, if applicable.

        An obstruction is raised only when:

        * The rebinding changed the type (``record.type_changed()`` is ``True``).
        * The rebinding is a late binding (``record.is_late_binding`` is ``True``).

        If both conditions are met, the severity is computed via
        :func:`_severity_from_type_change`, the kind is determined via
        :func:`_obstruction_kind_from_rebinding`, and a new
        :class:`ObstructionRecord` is created and appended to
        :attr:`_obstructions`.

        Args:
            record: The :class:`RebindingRecord` to evaluate.

        Returns:
            The newly created :class:`ObstructionRecord` if an obstruction
            was detected, or ``None`` otherwise.

        Example:
            >>> obs = analyzer.compute_obstruction(rec)
            >>> obs is not None or not rec.type_changed()
            True
        """
        if not (record.type_changed() and record.is_late_binding):
            _log.debug(
                "No obstruction for %s: type_changed=%s, is_late=%s",
                record.record_id,
                record.type_changed(),
                record.is_late_binding,
            )
            return None

        raw_severity = _severity_from_type_change(record.old_type_name, record.new_type_name)
        # Map raw severity (0-4) to 0-10 scale with a logarithmic stretch
        scaled_severity = min(10, math.ceil(raw_severity * 2.5))

        kind_str = _obstruction_kind_from_rebinding(record)
        obstruction_kind = ObstructionKind[kind_str.upper()]

        obs = ObstructionRecord(
            obstruction_id=_new_obstruction_id(),
            affected_module=record.module_name,
            affected_attribute=record.attribute,
            obstruction_kind=obstruction_kind,
            severity=scaled_severity,
            detected_at=time.time(),
            source_record_id=record.record_id,
        )
        self._obstructions.append(obs)
        _log.debug(
            "Obstruction %s detected for record %s — kind=%s, severity=%d",
            obs.obstruction_id,
            record.record_id,
            obstruction_kind.value,
            scaled_severity,
        )
        return obs

    def rebinding_depth(self, module_name: str) -> int:
        """Return the total number of rebinding records for a module.

        Counts every record (active or reverted) whose
        :attr:`~RebindingRecord.module_name` matches ``module_name``.

        Args:
            module_name: The module name to query.

        Returns:
            A non-negative integer count of rebinding events.

        Example:
            >>> analyzer = LateRebindingAnalyzer()
            >>> analyzer.record_rebinding("mod", "x", None, "int", False)  # doctest: +SKIP
            >>> analyzer.rebinding_depth("mod")
            1
        """
        depth = sum(1 for r in self._records if r.module_name == module_name)
        _log.debug("Rebinding depth for module '%s': %d", module_name, depth)
        return depth

    def find_cascading_rebidings(
        self, records: list[RebindingRecord]
    ) -> dict[str, list[str]]:
        """Identify attributes that have been rebound more than once (cascades).

        A cascade is defined as a ``(module_name, attribute)`` pair that
        appears in more than one record within the supplied list.

        Args:
            records: The list of :class:`RebindingRecord` objects to
                analyse.

        Returns:
            A dictionary mapping ``"<module_name>.<attribute>"`` to the
            list of :attr:`~RebindingRecord.record_id` values for all
            records in that cascade.  Only attributes with two or more
            records are included.

        Example:
            >>> cascades = analyzer.find_cascading_rebidings(analyzer._records)
            >>> all(len(v) > 1 for v in cascades.values())
            True
        """
        groups: dict[str, list[str]] = {}
        for rec in records:
            key = f"{rec.module_name}.{rec.attribute}"
            if key not in groups:
                groups[key] = []
            groups[key].append(rec.record_id)

        cascades = {k: v for k, v in groups.items() if len(v) > 1}
        _log.debug(
            "Found %d cascading rebinding groups out of %d total groups",
            len(cascades),
            len(groups),
        )
        return cascades

    def type_stability_score(self, module_name: str) -> float:
        """Compute the type-stability score for a module.

        The score is the fraction of rebinding records for the module
        where the type did *not* change (i.e. a value was replaced with
        another value of the same type).

        Args:
            module_name: The module name to evaluate.

        Returns:
            A float in ``[0.0, 1.0]``.  Returns ``1.0`` if no records
            exist for the module (vacuously stable).

        Example:
            >>> analyzer.type_stability_score("nonexistent_module")
            1.0
        """
        module_records = [r for r in self._records if r.module_name == module_name]
        if not module_records:
            _log.debug("No records for module '%s'; returning perfect stability", module_name)
            return 1.0

        stable = sum(1 for r in module_records if not r.type_changed())
        score = stable / len(module_records)
        _log.debug(
            "Type stability for '%s': %d stable / %d total = %.3f",
            module_name,
            stable,
            len(module_records),
            score,
        )
        return score

    def export_records(self) -> list[dict[str, Any]]:
        """Serialise all stored rebinding records to a list of dicts.

        Returns:
            A list of dictionaries, one per record, in insertion order.
            Each dictionary is produced by :meth:`RebindingRecord.to_dict`.

        Example:
            >>> data = analyzer.export_records()
            >>> all("record_id" in d for d in data)
            True
        """
        _log.debug("Exporting %d rebinding records", len(self._records))
        return [r.to_dict() for r in self._records]

    def stats(self) -> dict[str, Any]:
        """Return aggregate statistics for all tracked rebinding events.

        The returned dictionary contains:

        * ``"total_records"`` — total number of rebinding records.
        * ``"late_binding_count"`` — number of late (active) binding records.
        * ``"obstructions_count"`` — number of obstruction records.
        * ``"avg_severity"`` — mean severity across all obstructions (or
          ``0.0`` if none).
        * ``"type_stability_per_module"`` — dictionary mapping each unique
          module name to its :meth:`type_stability_score`.

        Returns:
            A dictionary of aggregate statistics.

        Example:
            >>> s = analyzer.stats()
            >>> s["total_records"] >= 0
            True
        """
        total = len(self._records)
        late_count = len(self.detect_late_bindings(self._records))
        obs_count = len(self._obstructions)
        avg_sev = (
            sum(o.severity for o in self._obstructions) / obs_count
            if obs_count > 0
            else 0.0
        )
        modules = {r.module_name for r in self._records}
        stability = {m: self.type_stability_score(m) for m in modules}
        result = {
            "total_records": total,
            "late_binding_count": late_count,
            "obstructions_count": obs_count,
            "avg_severity": avg_sev,
            "type_stability_per_module": stability,
        }
        _log.debug("Analyzer stats: %s", result)
        return result

    def active_records(self) -> list[RebindingRecord]:
        """Return all rebinding records that have not been reverted.

        Returns:
            A list of :class:`RebindingRecord` objects for which
            :meth:`~RebindingRecord.is_active` returns ``True``.

        Example:
            >>> active = analyzer.active_records()
            >>> all(r.is_active() for r in active)
            True
        """
        active = [r for r in self._records if r.is_active()]
        _log.debug("Active records: %d / %d total", len(active), len(self._records))
        return active

    def revert_record(self, record_id: str) -> bool:
        """Mark a rebinding record as reverted.

        Because :class:`RebindingRecord` is a frozen dataclass, reverting
        is implemented by replacing the existing record in :attr:`_records`
        with a new instance that has :attr:`~RebindingRecord.reverted_at`
        set to the current time.  The record ID is also added to the
        :attr:`_reverted` set.

        Args:
            record_id: The :attr:`~RebindingRecord.record_id` of the record
                to revert.

        Returns:
            ``True`` if the record was found and reverted; ``False`` if no
            record with the given ID was found.

        Example:
            >>> result = analyzer.revert_record(rec.record_id)
            >>> result
            True
        """
        for i, rec in enumerate(self._records):
            if rec.record_id == record_id:
                reverted = RebindingRecord(
                    record_id=rec.record_id,
                    module_name=rec.module_name,
                    attribute=rec.attribute,
                    old_type_name=rec.old_type_name,
                    new_type_name=rec.new_type_name,
                    kind=rec.kind,
                    is_late_binding=rec.is_late_binding,
                    rebind_at=rec.rebind_at,
                    reverted_at=time.time(),
                )
                self._records[i] = reverted
                self._reverted.add(record_id)
                _log.debug("Reverted record %s at %f", record_id, reverted.reverted_at)
                return True
        _log.debug("Record %s not found; revert failed", record_id)
        return False

    def obstruction_count(self) -> int:
        """Return the total number of obstruction records.

        Returns:
            A non-negative integer equal to ``len(self._obstructions)``.

        Example:
            >>> analyzer.obstruction_count()
            0
        """
        return len(self._obstructions)

    def records_for_module(self, module_name: str) -> list[RebindingRecord]:
        """Return all rebinding records associated with a given module.

        Args:
            module_name: The fully-qualified module name to filter by.

        Returns:
            A list of :class:`RebindingRecord` objects (possibly empty)
            whose :attr:`~RebindingRecord.module_name` matches
            ``module_name``.

        Example:
            >>> recs = analyzer.records_for_module("mymod")
            >>> all(r.module_name == "mymod" for r in recs)
            True
        """
        result = [r for r in self._records if r.module_name == module_name]
        _log.debug("Records for module '%s': %d", module_name, len(result))
        return result


# ---------------------------------------------------------------------------
# Value-type: PatchEvidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatchEvidence:
    """Immutable evidence record linking a patch to an optional obstruction.

    :class:`PatchEvidence` is created each time the
    :class:`PatchObstructionWitness` observes a patch event.  It provides
    an audit trail connecting rebinding records to any obstructions they
    triggered.

    Attributes:
        evidence_id: Unique identifier for this evidence (``"wt_..."``).
        record_id: The :attr:`~RebindingRecord.record_id` that was
            observed.
        obstruction_id: The :attr:`~ObstructionRecord.obstruction_id` that
            was detected as a consequence of the patch, or ``None`` if no
            obstruction was raised.
        witnessed_at: Unix timestamp when the patch was observed.
        witness_note: A human-readable note generated at the time of
            witnessing.
    """

    evidence_id: str
    record_id: str
    obstruction_id: str | None
    witnessed_at: float
    witness_note: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise this evidence record to a plain :class:`dict`.

        Returns:
            A dictionary of all fields suitable for JSON serialisation.

        Example:
            >>> d = ev.to_dict()
            >>> "evidence_id" in d
            True
        """
        return {
            "evidence_id": self.evidence_id,
            "record_id": self.record_id,
            "obstruction_id": self.obstruction_id,
            "witnessed_at": self.witnessed_at,
            "witness_note": self.witness_note,
            "age": self.age(),
        }

    def age(self) -> float:
        """Return the elapsed time (seconds) since this evidence was recorded.

        Returns:
            A non-negative float representing the age of this evidence.

        Example:
            >>> ev.age() >= 0.0
            True
        """
        return time.time() - self.witnessed_at


# ---------------------------------------------------------------------------
# Mutable manager: PatchObstructionWitness
# ---------------------------------------------------------------------------


@dataclass
class PatchObstructionWitness:
    """Observes patch events and manages the lifecycle of active obstructions.

    The witness maintains a collection of :class:`PatchEvidence` objects
    (one per observed patch), a dictionary of currently-active
    :class:`ObstructionRecord` objects keyed by their IDs, and a timeline
    deque that provides chronological ordering for reports.

    Attributes:
        _evidence: All evidence records in creation order.
        _active_obstructions: Maps obstruction IDs to active
            :class:`ObstructionRecord` objects.
        _reverted_records: Set of record IDs that have been successfully
            reverted; obstructions sourced from these records are removed.
        _timeline: A :class:`~collections.deque` of event dictionaries in
            chronological order.
    """

    _evidence: list[PatchEvidence] = field(default_factory=list)
    _active_obstructions: dict[str, ObstructionRecord] = field(default_factory=dict)
    _reverted_records: set[str] = field(default_factory=set)
    _timeline: deque = field(default_factory=deque)

    def observe_patch(self, record: RebindingRecord) -> str:
        """Observe a patch event and create evidence for it.

        A :class:`PatchEvidence` is created for each call.  The evidence
        is appended to :attr:`_evidence` and an event entry is pushed onto
        :attr:`_timeline`.

        Args:
            record: The :class:`RebindingRecord` representing the patch
                that was applied.

        Returns:
            The :attr:`~PatchEvidence.evidence_id` of the newly created
            evidence.

        Example:
            >>> witness = PatchObstructionWitness()
            >>> eid = witness.observe_patch(record)
            >>> eid.startswith("wt_")
            True
        """
        note = (
            f"Patch observed: {record.label()} "
            f"(late={record.is_late_binding}, kind={record.kind.value})"
        )
        obs_id = None
        for obs in self._active_obstructions.values():
            if obs.source_record_id == record.record_id:
                obs_id = obs.obstruction_id
                break

        ev = PatchEvidence(
            evidence_id=_new_witness_id(),
            record_id=record.record_id,
            obstruction_id=obs_id,
            witnessed_at=time.time(),
            witness_note=note,
        )
        self._evidence.append(ev)
        self._timeline.append({
            "event": "patch_observed",
            "evidence_id": ev.evidence_id,
            "record_id": record.record_id,
            "ts": ev.witnessed_at,
        })
        _log.debug("Observed patch: %s → evidence %s", record.record_id, ev.evidence_id)
        return ev.evidence_id

    def detect_obstruction(
        self, module_name: str, attribute: str
    ) -> ObstructionRecord | None:
        """Find an active obstruction affecting the given module attribute.

        Searches :attr:`_active_obstructions` for any obstruction whose
        :attr:`~ObstructionRecord.affected_module` and
        :attr:`~ObstructionRecord.affected_attribute` match the arguments.

        Args:
            module_name: The module name to search for.
            attribute: The attribute name to search for.

        Returns:
            The first matching :class:`ObstructionRecord` if found, or
            ``None``.

        Example:
            >>> witness.detect_obstruction("mymod", "fn") is None
            True
        """
        for obs in self._active_obstructions.values():
            if obs.affected_module == module_name and obs.affected_attribute == attribute:
                _log.debug(
                    "Active obstruction found for %s.%s: %s",
                    module_name,
                    attribute,
                    obs.obstruction_id,
                )
                return obs
        _log.debug("No active obstruction for %s.%s", module_name, attribute)
        return None

    def witness_revert(self, record_id: str) -> bool:
        """Record that a rebinding has been reverted and clean up obstructions.

        Adds ``record_id`` to :attr:`_reverted_records` and removes all
        active obstructions that were sourced from that record.

        Args:
            record_id: The :attr:`~RebindingRecord.record_id` to mark as
                reverted.

        Returns:
            ``True`` if at least one obstruction was removed or the record
            was not yet in :attr:`_reverted_records`; ``False`` if the
            record was already reverted.

        Example:
            >>> witness.witness_revert(record_id)
            True
        """
        if record_id in self._reverted_records:
            _log.debug("Record %s already marked as reverted", record_id)
            return False
        self._reverted_records.add(record_id)
        removed = [
            oid for oid, obs in self._active_obstructions.items()
            if obs.source_record_id == record_id
        ]
        for oid in removed:
            del self._active_obstructions[oid]
        self._timeline.append({
            "event": "revert_witnessed",
            "record_id": record_id,
            "obstructions_removed": removed,
            "ts": time.time(),
        })
        _log.debug(
            "Witnessed revert of %s; removed %d obstructions", record_id, len(removed)
        )
        return True

    def get_active_obstructions(self) -> list[ObstructionRecord]:
        """Return all currently active obstruction records.

        Returns:
            A list of :class:`ObstructionRecord` objects from
            :attr:`_active_obstructions`.

        Example:
            >>> witness.get_active_obstructions()
            []
        """
        return list(self._active_obstructions.values())

    def obstruction_severity(self, module_name: str) -> float:
        """Return the average severity of active obstructions for a module.

        Args:
            module_name: The module name to aggregate over.

        Returns:
            The mean severity as a float, or ``0.0`` if there are no
            active obstructions for the given module.

        Example:
            >>> witness.obstruction_severity("nonexistent")
            0.0
        """
        relevant = [
            obs for obs in self._active_obstructions.values()
            if obs.affected_module == module_name
        ]
        if not relevant:
            return 0.0
        avg = sum(o.severity for o in relevant) / len(relevant)
        _log.debug(
            "Average obstruction severity for '%s': %.2f (%d obstructions)",
            module_name,
            avg,
            len(relevant),
        )
        return avg

    def generate_obstruction_report(self) -> dict[str, Any]:
        """Generate a comprehensive report of all obstruction activity.

        The report includes:

        * ``"total_evidence"`` — total number of evidence records.
        * ``"active_obstructions_count"`` — count of currently active
          obstructions.
        * ``"critical_obstructions"`` — list of serialised critical
          obstructions (severity ≥ 7).
        * ``"severity_distribution"`` — dictionary mapping each severity
          integer to the count of active obstructions at that level.
        * ``"timeline_span"`` — a dict with ``"first"`` and ``"last"``
          timestamps from the timeline, or ``None`` if the timeline is
          empty.

        Returns:
            A dictionary suitable for JSON serialisation.

        Example:
            >>> report = witness.generate_obstruction_report()
            >>> "total_evidence" in report
            True
        """
        active_obs = list(self._active_obstructions.values())
        critical = [o.to_dict() for o in active_obs if o.is_critical()]
        severity_dist: dict[int, int] = {}
        for obs in active_obs:
            severity_dist[obs.severity] = severity_dist.get(obs.severity, 0) + 1

        if self._timeline:
            times = [e.get("ts", 0.0) for e in self._timeline]
            span: dict[str, Any] | None = {"first": min(times), "last": max(times)}
        else:
            span = None

        report = {
            "total_evidence": len(self._evidence),
            "active_obstructions_count": len(active_obs),
            "critical_obstructions": critical,
            "severity_distribution": severity_dist,
            "timeline_span": span,
        }
        _log.debug("Generated obstruction report: %s", report)
        return report

    def export_evidence(self) -> list[dict[str, Any]]:
        """Serialise all evidence records to a list of dicts.

        Returns:
            A list of dictionaries, one per evidence record, in creation
            order.  Each dictionary is produced by
            :meth:`PatchEvidence.to_dict`.

        Example:
            >>> data = witness.export_evidence()
            >>> all("evidence_id" in d for d in data)
            True
        """
        _log.debug("Exporting %d evidence records", len(self._evidence))
        return [e.to_dict() for e in self._evidence]

    def add_obstruction(self, obs: ObstructionRecord) -> None:
        """Register an active obstruction in the witness.

        Args:
            obs: The :class:`ObstructionRecord` to register.  If an
                obstruction with the same ID is already registered, it is
                overwritten.

        Example:
            >>> witness.add_obstruction(obstruction_record)
        """
        self._active_obstructions[obs.obstruction_id] = obs
        self._timeline.append({
            "event": "obstruction_added",
            "obstruction_id": obs.obstruction_id,
            "severity": obs.severity,
            "ts": time.time(),
        })
        _log.debug(
            "Added obstruction %s (severity=%d) to witness", obs.obstruction_id, obs.severity
        )

    def critical_obstructions(self) -> list[ObstructionRecord]:
        """Return all active obstructions with severity ≥ 7.

        Returns:
            A list of :class:`ObstructionRecord` objects from
            :attr:`_active_obstructions` where
            :meth:`~ObstructionRecord.is_critical` returns ``True``.

        Example:
            >>> witness.critical_obstructions()
            []
        """
        critical = [obs for obs in self._active_obstructions.values() if obs.is_critical()]
        _log.debug("Critical obstructions: %d", len(critical))
        return critical

    def timeline_entries(self) -> list[dict[str, Any]]:
        """Return the timeline of events in chronological order.

        Returns:
            A list of event dictionaries sorted by their ``"ts"`` key
            (ascending).  Each entry has at minimum a ``"ts"`` key and an
            ``"event"`` key.

        Example:
            >>> entries = witness.timeline_entries()
            >>> entries == sorted(entries, key=lambda e: e["ts"])
            True
        """
        entries = list(self._timeline)
        entries.sort(key=lambda e: e.get("ts", 0.0))
        _log.debug("Timeline entries: %d", len(entries))
        return entries


# ---------------------------------------------------------------------------
# Top-level coordinator
# ---------------------------------------------------------------------------


@dataclass
class MonkeyPatchingLateRebindingCoordinator:
    """Coordinates rebinding analysis and obstruction witnessing.

    This class is the primary entry-point for the module.  It owns a
    :class:`LateRebindingAnalyzer` and a :class:`PatchObstructionWitness`,
    and provides high-level operations that integrate both components.

    Attributes:
        analyzer: The :class:`LateRebindingAnalyzer` tracking rebinding
            events.
        witness: The :class:`PatchObstructionWitness` tracking patch
            evidence and obstructions.
        _session_id: A short random hex string identifying this coordinator
            session.
        _created_at: Unix timestamp when this coordinator was created.
    """

    analyzer: LateRebindingAnalyzer = field(default_factory=LateRebindingAnalyzer)
    witness: PatchObstructionWitness = field(default_factory=PatchObstructionWitness)
    _session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _created_at: float = field(default_factory=time.time)

    def apply_rebinding(
        self,
        module_name: str,
        attribute: str,
        old_type: str | None,
        new_type: str,
        is_late: bool,
    ) -> dict[str, Any]:
        """Apply a rebinding, compute any obstruction, and return a summary.

        This is the primary mutation operation of the coordinator.  It:

        1. Calls :meth:`LateRebindingAnalyzer.record_rebinding`.
        2. Calls :meth:`LateRebindingAnalyzer.compute_obstruction`.
        3. If an obstruction was produced, registers it with the witness
           via :meth:`PatchObstructionWitness.add_obstruction`.
        4. Calls :meth:`PatchObstructionWitness.observe_patch`.

        Args:
            module_name: The module where the rebinding occurs.
            attribute: The attribute being rebound.
            old_type: The previous type name, or ``None``.
            new_type: The new type name.
            is_late: Whether this is a late (post-import) binding.

        Returns:
            A summary dictionary with keys ``"record_id"``,
            ``"obstruction_id"`` (or ``None``), ``"severity"``
            (or ``None``), ``"evidence_id"``, and ``"label"``.

        Example:
            >>> coordinator = MonkeyPatchingLateRebindingCoordinator()
            >>> result = coordinator.apply_rebinding(
            ...     "mymod", "fn", "int", "function", True
            ... )
            >>> "record_id" in result
            True
        """
        record = self.analyzer.record_rebinding(
            module_name, attribute, old_type, new_type, is_late
        )
        obstruction = self.analyzer.compute_obstruction(record)
        obs_id: str | None = None
        severity: int | None = None
        if obstruction is not None:
            self.witness.add_obstruction(obstruction)
            obs_id = obstruction.obstruction_id
            severity = obstruction.severity

        evidence_id = self.witness.observe_patch(record)
        summary = {
            "record_id": record.record_id,
            "obstruction_id": obs_id,
            "severity": severity,
            "evidence_id": evidence_id,
            "label": record.label(),
        }
        _log.debug("apply_rebinding summary: %s", summary)
        return summary

    def revert_rebinding(self, record_id: str) -> bool:
        """Revert a previously recorded rebinding.

        Delegates to both the analyzer and the witness to ensure the
        revert is reflected in all subsystems.

        Args:
            record_id: The ID of the :class:`RebindingRecord` to revert.

        Returns:
            ``True`` if the record was found and reverted by the analyzer;
            the witness revert is always attempted regardless of the
            analyzer result.

        Example:
            >>> coordinator.revert_rebinding(record_id)
            True
        """
        analyst_result = self.analyzer.revert_record(record_id)
        self.witness.witness_revert(record_id)
        _log.debug("revert_rebinding(%s) → analyst=%s", record_id, analyst_result)
        return analyst_result

    def assess_obstructions(self) -> list[dict[str, Any]]:
        """Return a serialised list of all currently active obstructions.

        Returns:
            A list of dictionaries, one per active obstruction, produced
            by :meth:`ObstructionRecord.to_dict`.

        Example:
            >>> coordinator.assess_obstructions()
            []
        """
        active = self.witness.get_active_obstructions()
        _log.debug("Assessing %d active obstructions", len(active))
        return [obs.to_dict() for obs in active]

    def full_report(self) -> dict[str, Any]:
        """Generate a combined report from the analyzer and the witness.

        Returns:
            A dictionary with keys:

            * ``"session_id"`` — the coordinator session ID.
            * ``"session_age"`` — seconds since the coordinator was created.
            * ``"analyzer_stats"`` — output of :meth:`LateRebindingAnalyzer.stats`.
            * ``"obstruction_report"`` — output of
              :meth:`PatchObstructionWitness.generate_obstruction_report`.
            * ``"chains"`` — a snapshot of the analyzer chain index.

        Example:
            >>> report = coordinator.full_report()
            >>> "session_id" in report
            True
        """
        report = {
            "session_id": self._session_id,
            "session_age": time.time() - self._created_at,
            "analyzer_stats": self.analyzer.stats(),
            "obstruction_report": self.witness.generate_obstruction_report(),
            "chains": {k: list(v) for k, v in self.analyzer._chains.items()},
        }
        _log.debug("Full report generated for session %s", self._session_id)
        return report

    def stability_summary(self) -> dict[str, Any]:
        """Return a stability summary across all tracked modules.

        Computes the :meth:`~LateRebindingAnalyzer.type_stability_score`
        for every module that appears in the analyzer, then calculates the
        overall mean and the count of critical obstructions.

        Returns:
            A dictionary with keys:

            * ``"per_module"`` — mapping of module name → stability score.
            * ``"overall_average"`` — mean stability score (float).
            * ``"critical_obstruction_count"`` — number of active
              obstructions with severity ≥ 7.

        Example:
            >>> summary = coordinator.stability_summary()
            >>> 0.0 <= summary["overall_average"] <= 1.0
            True
        """
        modules = self.module_names()
        per_module = {m: self.analyzer.type_stability_score(m) for m in modules}
        overall = sum(per_module.values()) / len(per_module) if per_module else 1.0
        critical_count = len(self.witness.critical_obstructions())
        summary = {
            "per_module": per_module,
            "overall_average": overall,
            "critical_obstruction_count": critical_count,
        }
        _log.debug("Stability summary: overall_avg=%.3f, critical=%d", overall, critical_count)
        return summary

    def reset(self) -> None:
        """Reinitialise the analyzer and witness to an empty state.

        This replaces both :attr:`analyzer` and :attr:`witness` with fresh
        instances.  The session ID and creation timestamp are preserved.

        Example:
            >>> coordinator.reset()
            >>> coordinator.analyzer._records
            []
        """
        _log.debug("Resetting coordinator for session %s", self._session_id)
        self.analyzer = LateRebindingAnalyzer()
        self.witness = PatchObstructionWitness()

    def module_names(self) -> list[str]:
        """Return a sorted list of unique module names across all records.

        Returns:
            A deduplicated, sorted list of
            :attr:`~RebindingRecord.module_name` values from the
            analyzer's records.

        Example:
            >>> coordinator.module_names()
            []
        """
        names = sorted({r.module_name for r in self.analyzer._records})
        _log.debug("Unique module names: %s", names)
        return names

    def late_binding_count(self) -> int:
        """Return the total count of active late-binding records.

        Returns:
            An integer equal to the number of late-binding records that
            are still active (not reverted).

        Example:
            >>> coordinator.late_binding_count()
            0
        """
        count = len(self.analyzer.detect_late_bindings(self.analyzer._records))
        _log.debug("Late binding count: %d", count)
        return count


__all__ = [
    "RebindingKind",
    "ObstructionKind",
    "RebindingRecord",
    "ObstructionRecord",
    "RebindingChain",
    "LateRebindingAnalyzer",
    "PatchEvidence",
    "PatchObstructionWitness",
    "MonkeyPatchingLateRebindingCoordinator",
]

# copilot: s04 — Monkey Patching and Late Rebinding (Ch23 §4)
