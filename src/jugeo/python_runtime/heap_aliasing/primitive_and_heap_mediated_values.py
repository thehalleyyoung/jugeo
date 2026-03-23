"""Primitive and heap-mediated values — theory2.tex Ch17, §4 — Primitives vs Heap Sections.

This module implements the machinery for classifying Python values as either
*primitive* (inlined coordinates with no heap identity) or *heap-mediated*
(objects with an identity coordinate and a section over their fields).

In the sheaf-theoretic model described in theory2.tex Ch17 §4, primitive types
(``int``, ``float``, ``bool``, ``str``, ``bytes``, ``NoneType``) act as *global
sections*: their value is the same everywhere, they have no mutable state, and
they are identified purely by their value.  Heap-mediated objects, by contrast,
are *local sections*: they exist at a specific identity coordinate ``id(obj)``
and can carry distinct state even when equality holds (i.e. ``a == b`` does not
imply ``a is b``).

The distinction drives all downstream alias and mutation reasoning: two
primitive values that are equal are interchangeable (up to sheaf isomorphism),
whereas two heap-mediated values at different coordinates are always distinct
sections even if their fields agree.

# copilot: s01 — primitive vs heap-mediated value classification; feeds the
#           alias and mutation layers with the initial object kind map.

Typical usage::

    coordinator = PrimitiveHeapMediatedValuesCoordinator()
    kind = coordinator.classify_object(42)          # "primitive"
    kind = coordinator.classify_object([1, 2, 3])   # "heap_mediated"

    analyzer = PrimitiveHeapMediatedValuesAnalyzer()
    report = analyzer.build_analysis_report(source_code)

    witness = PrimitiveHeapMediatedValuesWitness()
    witness.witness_object(some_obj, "my_var")
    bundle = witness.generate_evidence_bundle()
"""

from __future__ import annotations

import ast
import inspect
import logging
import sys
import time
import types
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

try:
    from jugeo.geometry.site import CoordinateKind, CoordinateObject, MorphismKind, Site, SiteBuilder
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
    from jugeo.judgments.judgment_terms import (
        JudgmentStatus,
        TrustLevel,
    )
except ImportError:
    from enum import IntEnum

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        """Stub for TrustLevel."""
        UNVERIFIED = 1
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

        def label(self) -> str:  # noqa: D102
            return self.name.lower()

    class CoordinateKind(str, Enum):  # type: ignore[no-redef]
        """Stub for CoordinateKind."""
        MODULE = "module"
        FUNCTION = "function"
        STATEMENT = "statement"
        EXPRESSION = "expression"

    class JudgmentStatus(str, Enum):  # type: ignore[no-redef]
        """Stub for JudgmentStatus."""
        PROPOSED = "proposed"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub for CoordinateObject."""
        coordinate_id: str = ""
        kind: str = "expression"
        label: str = ""

    @dataclass(frozen=True, slots=True)
    class SupportRegion:  # type: ignore[no-redef]
        """Stub for SupportRegion."""
        coordinate: str = ""

    class SupportSet:  # type: ignore[no-redef]
        """Stub for SupportSet."""
        def __init__(self, coordinates: frozenset[str] = frozenset()) -> None:
            self.coordinates = coordinates

    class SupportTracker:  # type: ignore[no-redef]
        """Stub for SupportTracker."""

    class Site:  # type: ignore[no-redef]
        """Stub for Site."""

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub for SiteBuilder."""

    class MorphismKind(str, Enum):  # type: ignore[no-redef]
        """Stub for MorphismKind."""
        RESTRICTION = "restriction"
        EXTENSION = "extension"

try:
    from jugeo.python_runtime.heap_aliasing.models import (
        AliasEdge,
        AliasPartition,
        HeapObject,
        HeapSection,
        HeapSnapshot,
        IdentityCoordinate,
        MutationEvent,
        ObjectKind,
        make_heap_object,
        make_identity_coordinate,
    )
except ImportError:

    class ObjectKind(str, Enum):  # type: ignore[no-redef]
        """Stub for ObjectKind."""
        PRIMITIVE = "primitive"
        CONTAINER = "container"
        INSTANCE = "instance"
        FUNCTION = "function"
        MODULE = "module"
        FROZEN = "frozen"
        BUILTIN = "builtin"
        UNKNOWN = "unknown"

    @dataclass(frozen=True, slots=True)
    class IdentityCoordinate:  # type: ignore[no-redef]
        """Stub for IdentityCoordinate."""
        object_id: int = 0
        type_name: str = ""
        coordinate_key: str = ""
        creation_site: str = ""
        created_at: float = 0.0

    @dataclass(frozen=True, slots=True)
    class HeapObject:  # type: ignore[no-redef]
        """Stub for HeapObject."""
        object_id: int = 0
        type_name: str = ""
        kind: ObjectKind = ObjectKind.UNKNOWN
        field_keys: frozenset[str] = frozenset()
        creation_site: str = ""
        created_at: float = 0.0

        def is_container(self) -> bool:  # noqa: D102
            return self.kind == ObjectKind.CONTAINER

        def is_primitive(self) -> bool:  # noqa: D102
            return self.kind == ObjectKind.PRIMITIVE

    @dataclass(frozen=True, slots=True)
    class HeapSection:  # type: ignore[no-redef]
        """Stub for HeapSection."""
        section_id: str = ""
        coordinate_key: str = ""
        fields: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class AliasPartition:  # type: ignore[no-redef]
        """Stub for AliasPartition."""
        partition_id: str = ""
        member_keys: frozenset[str] = frozenset()

    @dataclass(frozen=True, slots=True)
    class MutationEvent:  # type: ignore[no-redef]
        """Stub for MutationEvent."""
        event_id: str = ""
        coordinate_key: str = ""
        field_name: str = ""
        old_value_repr: str = ""
        new_value_repr: str = ""
        timestamp: float = 0.0

    @dataclass(frozen=True, slots=True)
    class AliasEdge:  # type: ignore[no-redef]
        """Stub for AliasEdge."""
        source_key: str = ""
        target_key: str = ""
        kind: str = "alias"

    @dataclass(frozen=True, slots=True)
    class HeapSnapshot:  # type: ignore[no-redef]
        """Stub for HeapSnapshot."""
        snapshot_id: str = ""
        created_at: float = 0.0

    def make_identity_coordinate(obj: Any, creation_site: str = "") -> IdentityCoordinate:  # type: ignore[no-redef]
        """Stub factory for IdentityCoordinate."""
        return IdentityCoordinate(
            object_id=id(obj),
            type_name=type(obj).__name__,
            coordinate_key=f"id:{id(obj)}",
            creation_site=creation_site,
            created_at=time.time(),
        )

    def make_heap_object(obj: Any, creation_site: str = "") -> HeapObject:  # type: ignore[no-redef]
        """Stub factory for HeapObject."""
        kind = ObjectKind.PRIMITIVE if isinstance(obj, (int, float, bool, str, bytes, type(None))) else ObjectKind.INSTANCE
        return HeapObject(
            object_id=id(obj),
            type_name=type(obj).__name__,
            kind=kind,
            field_keys=frozenset(),
            creation_site=creation_site,
            created_at=time.time(),
        )


_log = logging.getLogger(__name__)

_ANALYSIS_CHANNEL: str = "copilot-s01-primitive-heap-mediated"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Python types considered primitive (pure-value, no heap identity).
PRIMITIVE_TYPES: tuple[type, ...] = (int, float, complex, bool, str, bytes, bytearray, type(None))

#: Python container types that hold references and have heap identity.
CONTAINER_TYPES: tuple[type, ...] = (list, dict, set, frozenset, tuple)

#: Types that are always frozen (no field mutation possible).
FROZEN_TYPES: tuple[type, ...] = (str, bytes, frozenset, tuple, complex, bool, int, float, type(None))

#: Maximum number of fields to inspect when building a heap-object report.
MAX_FIELD_DEPTH: int = 64

#: Label used for objects whose kind cannot be determined.
UNKNOWN_KIND_LABEL: str = "<unknown-kind>"

#: AST node types that correspond to primitive literals.
PRIMITIVE_AST_TYPES: tuple[type, ...] = (ast.Constant,)

#: AST node types that represent heap allocations (constructor calls, lists, etc.).
HEAP_ALLOCATION_AST_TYPES: tuple[type, ...] = (ast.List, ast.Dict, ast.Set, ast.Call, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)

#: Report key for primitive value count.
REPORT_KEY_PRIMITIVE_COUNT: str = "primitive_count"

#: Report key for heap-mediated value count.
REPORT_KEY_HEAP_COUNT: str = "heap_mediated_count"

#: Report key for analysis timestamp.
REPORT_KEY_TIMESTAMP: str = "analysis_timestamp"

#: Sentinel string for unlabelled witness observations.
UNLABELLED_WITNESS: str = "<unlabelled>"

#: Maximum witness observations stored before auto-flush.
MAX_WITNESS_OBSERVATIONS: int = 4096

#: Version tag for the value report schema.
VALUE_REPORT_VERSION: str = "1.0.0"

#: Default trust level for runtime observations.
DEFAULT_TRUST_LEVEL: int = 1  # TrustLevel.UNVERIFIED

#: Channel name for evidence emission.
EVIDENCE_CHANNEL_NAME: str = "heap-aliasing.primitive-classification"

#: Section title, used in generated reports.
SECTION_TITLE: str = "Primitive and heap-mediated values"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _new_uid() -> str:
    """Return a fresh unique identifier string.

    Returns:
        A hex UUID4 string prefixed with ``"uid_"``.
    """
    return f"uid_{uuid.uuid4().hex[:12]}"


def _safe_repr(obj: Any, max_len: int = 80) -> str:
    """Return a safe, length-limited repr of *obj*.

    Parameters:
        obj:     The object to represent.
        max_len: Maximum character length of the returned string.

    Returns:
        A string representation; truncated with ``"…"`` if too long.
    """
    try:
        r = repr(obj)
    except Exception:  # noqa: BLE001
        r = f"<repr-error: {type(obj).__name__}>"
    if len(r) > max_len:
        r = r[: max_len - 1] + "…"
    return r


def _classify_type(tp: type) -> str:
    """Classify a Python type into a broad category label.

    Parameters:
        tp: A Python type object.

    Returns:
        One of: ``"primitive"``, ``"container"``, ``"function"``, ``"class"``,
        ``"module"``, ``"builtin"``, ``"frozen"``, ``"instance"``.
    """
    if tp in PRIMITIVE_TYPES:
        return "primitive"
    if tp in CONTAINER_TYPES:
        return "container"
    if tp is types.FunctionType or tp is types.MethodType or tp is types.BuiltinFunctionType:
        return "function"
    if tp is types.ModuleType:
        return "module"
    if isinstance(tp, type) and tp.__name__ in ("type", "ABCMeta"):
        return "class"
    if tp in FROZEN_TYPES:
        return "frozen"
    return "instance"


def _is_interned_string(s: str) -> bool:
    """Heuristically detect whether *s* is likely to be interned by CPython.

    CPython interns short strings that look like identifiers.  This is a
    best-effort heuristic; it does not guarantee interning status.

    Parameters:
        s: A string value to check.

    Returns:
        ``True`` if the string is likely interned.
    """
    if not isinstance(s, str):
        return False
    if len(s) == 0:
        return True
    # CPython interns identifier-like strings of moderate length.
    return len(s) <= 20 and s.isidentifier()


def _is_small_integer(n: int) -> bool:
    """Return True if *n* is in CPython's small-integer cache range.

    CPython caches integers in ``[-5, 256]``.

    Parameters:
        n: An integer value.

    Returns:
        ``True`` if *n* is likely to be in the integer cache.
    """
    return isinstance(n, int) and not isinstance(n, bool) and -5 <= n <= 256


def _iter_object_fields(obj: Any) -> Iterator[tuple[str, Any]]:
    """Yield ``(field_name, field_value)`` pairs for *obj*'s public attributes.

    Parameters:
        obj: Any Python object.

    Yields:
        Tuples of ``(name, value)`` for each accessible attribute.
    """
    try:
        members = inspect.getmembers(obj, predicate=lambda v: not callable(v))
        for name, value in members:
            if not name.startswith("__"):
                yield name, value
    except Exception:  # noqa: BLE001
        pass


def _ast_node_summary(node: ast.AST) -> dict[str, Any]:
    """Return a JSON-serialisable summary of an AST node.

    Parameters:
        node: An ``ast.AST`` node.

    Returns:
        A dictionary with ``"type"``, ``"lineno"``, and ``"col_offset"`` keys.
    """
    return {
        "type": type(node).__name__,
        "lineno": getattr(node, "lineno", None),
        "col_offset": getattr(node, "col_offset", None),
    }


# ---------------------------------------------------------------------------
# ValueKind enum
# ---------------------------------------------------------------------------


class ValueKind(str, Enum):
    """Discriminates between primitive and heap-mediated values.

    In the sheaf model, a *primitive* is a global section (exists everywhere
    identically), while a *heap-mediated* value is a local section (exists at
    a specific identity coordinate).

    Members:
        PRIMITIVE:     Pure-value type with no identity coordinate.
        HEAP_MEDIATED: Object with an identity coordinate (``id(obj)``).
        UNKNOWN:       Classification could not be determined.
    """

    PRIMITIVE = "primitive"
    HEAP_MEDIATED = "heap_mediated"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ValueRecord dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValueRecord:
    """Immutable record describing a single classified value.

    Attributes:
        record_id:       Unique record identifier.
        label:           Human-readable label (e.g. variable name).
        value_repr:      Safe repr of the observed value.
        value_kind:      Whether this is primitive or heap-mediated.
        object_type:     The type name of the value.
        object_id:       ``id(obj)`` for heap-mediated values; 0 for primitives.
        coordinate_key:  Sheaf coordinate key (``"id:<n>"`` or ``"val:<repr>"``).
        is_mutable:      Whether the value can be mutated in place.
        is_interned:     Whether the value is likely interned (strings/ints).
        observed_at:     Unix timestamp of observation.
        provenance:      Tuple of provenance labels (e.g. call chain).
    """

    record_id: str
    label: str
    value_repr: str
    value_kind: ValueKind
    object_type: str
    object_id: int
    coordinate_key: str
    is_mutable: bool
    is_interned: bool
    observed_at: float
    provenance: tuple[str, ...]

    def is_primitive(self) -> bool:
        """Return True if this record represents a primitive value.

        Returns:
            ``True`` when ``value_kind == ValueKind.PRIMITIVE``.
        """
        return self.value_kind == ValueKind.PRIMITIVE

    def is_heap_mediated(self) -> bool:
        """Return True if this record represents a heap-mediated object.

        Returns:
            ``True`` when ``value_kind == ValueKind.HEAP_MEDIATED``.
        """
        return self.value_kind == ValueKind.HEAP_MEDIATED

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Returns:
            A JSON-serialisable dict with all record fields.
        """
        return {
            "record_id": self.record_id,
            "label": self.label,
            "value_repr": self.value_repr,
            "value_kind": self.value_kind.value,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "coordinate_key": self.coordinate_key,
            "is_mutable": self.is_mutable,
            "is_interned": self.is_interned,
            "observed_at": self.observed_at,
            "provenance": list(self.provenance),
        }

    def with_label(self, new_label: str) -> "ValueRecord":
        """Return a copy of this record with a different label.

        Parameters:
            new_label: The replacement label string.

        Returns:
            A new :class:`ValueRecord` with the updated label.
        """
        from dataclasses import replace as dc_replace
        return dc_replace(self, label=new_label)


# ---------------------------------------------------------------------------
# ASTValueVisitor
# ---------------------------------------------------------------------------


class ASTValueVisitor(ast.NodeVisitor):
    """AST visitor that collects primitive literals and heap-allocation nodes.

    This visitor walks a parsed Python AST and categorises each expression
    node as either a primitive literal or a heap allocation.  Results are
    accumulated in :attr:`primitive_nodes` and :attr:`heap_nodes`.

    Attributes:
        primitive_nodes: List of ``(node, value)`` tuples for constant/literal nodes.
        heap_nodes:      List of ``(node, kind_label)`` tuples for allocation nodes.
        assignment_targets: List of assignment target names found.
        call_sites:      List of ``ast.Call`` nodes found.

    Examples:
        >>> visitor = ASTValueVisitor()
        >>> tree = ast.parse("x = 42\\ny = [1, 2, 3]")
        >>> visitor.visit(tree)
        >>> len(visitor.primitive_nodes)
        3
        >>> len(visitor.heap_nodes)
        1
    """

    def __init__(self) -> None:
        """Initialise the visitor with empty accumulators."""
        self.primitive_nodes: list[tuple[ast.AST, Any]] = []
        self.heap_nodes: list[tuple[ast.AST, str]] = []
        self.assignment_targets: list[str] = []
        self.call_sites: list[ast.Call] = []

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        """Record a constant literal node.

        Parameters:
            node: The ``ast.Constant`` node.
        """
        self.primitive_nodes.append((node, node.value))
        self.generic_visit(node)

    def visit_List(self, node: ast.List) -> None:  # noqa: N802
        """Record a list literal as a heap allocation.

        Parameters:
            node: The ``ast.List`` node.
        """
        self.heap_nodes.append((node, "list_literal"))
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:  # noqa: N802
        """Record a dict literal as a heap allocation.

        Parameters:
            node: The ``ast.Dict`` node.
        """
        self.heap_nodes.append((node, "dict_literal"))
        self.generic_visit(node)

    def visit_Set(self, node: ast.Set) -> None:  # noqa: N802
        """Record a set literal as a heap allocation.

        Parameters:
            node: The ``ast.Set`` node.
        """
        self.heap_nodes.append((node, "set_literal"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        """Record a call site as a potential heap allocation.

        Parameters:
            node: The ``ast.Call`` node.
        """
        self.call_sites.append(node)
        self.heap_nodes.append((node, "call_site"))
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        """Record a list comprehension as a heap allocation.

        Parameters:
            node: The ``ast.ListComp`` node.
        """
        self.heap_nodes.append((node, "list_comprehension"))
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        """Record a dict comprehension as a heap allocation.

        Parameters:
            node: The ``ast.DictComp`` node.
        """
        self.heap_nodes.append((node, "dict_comprehension"))
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        """Record a set comprehension as a heap allocation.

        Parameters:
            node: The ``ast.SetComp`` node.
        """
        self.heap_nodes.append((node, "set_comprehension"))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        """Record assignment target names.

        Parameters:
            node: The ``ast.Assign`` node.
        """
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.assignment_targets.append(target.id)
        self.generic_visit(node)

    def summary(self) -> dict[str, Any]:
        """Return a summary dictionary of accumulated observations.

        Returns:
            A dictionary with counts and details of primitives, heap nodes,
            call sites, and assignment targets.
        """
        return {
            "primitive_node_count": len(self.primitive_nodes),
            "heap_node_count": len(self.heap_nodes),
            "call_site_count": len(self.call_sites),
            "assignment_target_count": len(self.assignment_targets),
            "primitive_values": [_safe_repr(v) for _, v in self.primitive_nodes[:20]],
            "heap_kinds": [kind for _, kind in self.heap_nodes[:20]],
            "assignment_names": self.assignment_targets[:20],
        }


# ---------------------------------------------------------------------------
# PrimitiveHeapMediatedValuesCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PrimitiveHeapMediatedValuesCoordinator:
    """Coordinates analysis of primitive vs heap-mediated Python values.

    This coordinator manages the distinction between values that exist purely
    as coordinates (primitives: ``int``, ``float``, ``bool``, ``str``,
    ``bytes``, ``NoneType``) and values mediated through a heap object.

    In the sheaf model, primitive values are *global sections*: they carry
    their full meaning in their value alone, with no identity coordinate.
    Heap-mediated values are *local sections*: they exist at a specific
    ``id(obj)`` coordinate and may be aliased or mutated.

    Attributes:
        _records:         Internal list of :class:`ValueRecord` instances.
        _type_cache:      Cache mapping type objects to their classification.
        _analysis_log:    List of (timestamp, message) analysis log entries.
        _primitive_count: Running count of primitives classified.
        _heap_count:      Running count of heap-mediated objects classified.
        created_at:       Timestamp when this coordinator was created.
        coordinator_id:   Unique identifier for this coordinator instance.

    Examples:
        >>> coord = PrimitiveHeapMediatedValuesCoordinator()
        >>> coord.classify_object(42)
        'primitive'
        >>> coord.classify_object([1, 2, 3])
        'heap_mediated'
    """

    _records: list[ValueRecord] = field(default_factory=list)
    _type_cache: dict[type, str] = field(default_factory=dict)
    _analysis_log: list[tuple[float, str]] = field(default_factory=list)
    _primitive_count: int = field(default=0)
    _heap_count: int = field(default=0)
    created_at: float = field(default_factory=time.time)
    coordinator_id: str = field(default_factory=_new_uid)

    def classify_object(self, obj: Any) -> str:
        """Classify *obj* as ``"primitive"`` or ``"heap_mediated"``.

        Primitive types are ``int``, ``float``, ``complex``, ``bool``, ``str``,
        ``bytes``, ``bytearray``, and ``NoneType``.  All other objects are
        classified as heap-mediated.

        Parameters:
            obj: Any Python object to classify.

        Returns:
            The string ``"primitive"`` or ``"heap_mediated"``.
        """
        tp = type(obj)
        if tp not in self._type_cache:
            self._type_cache[tp] = _classify_type(tp)
        result = self._type_cache[tp]
        if result == "primitive":
            self._primitive_count += 1
        else:
            self._heap_count += 1
        self._analysis_log.append((time.time(), f"classify_object: {tp.__name__} → {result}"))
        return result if result == "primitive" else "heap_mediated"

    def is_primitive(self, obj: Any) -> bool:
        """Return ``True`` if *obj* is a primitive value.

        Primitives are pure-value types with no mutable state and no meaningful
        identity coordinate beyond their value.  In CPython, most primitives
        are interned (small integers, interned strings) or otherwise singleton.

        Parameters:
            obj: Any Python object.

        Returns:
            ``True`` if *obj* is an instance of a primitive type.
        """
        return isinstance(obj, PRIMITIVE_TYPES)

    def is_heap_mediated(self, obj: Any) -> bool:
        """Return ``True`` if *obj* is a heap-mediated value.

        Heap-mediated objects have an identity coordinate (``id(obj)``), can
        hold references to other objects, and may be mutated in place.

        Parameters:
            obj: Any Python object.

        Returns:
            ``True`` if *obj* is not an instance of a primitive type.
        """
        return not isinstance(obj, PRIMITIVE_TYPES)

    def get_coordinate_kind(self, obj: Any) -> str:
        """Return the sheaf coordinate kind for *obj*.

        Primitives do not have an independent heap coordinate; they are
        identified by their value.  Heap-mediated objects are identified by
        their ``id(obj)`` coordinate.

        Parameters:
            obj: Any Python object.

        Returns:
            ``"value_coordinate"`` for primitives, ``"identity_coordinate"``
            for heap-mediated objects.
        """
        if self.is_primitive(obj):
            return "value_coordinate"
        return "identity_coordinate"

    def analyze_source(self, source_code: str) -> dict[str, Any]:
        """Analyse Python source code for primitive and heap-allocation patterns.

        Parses *source_code* with :func:`ast.parse`, then walks the tree to
        count primitive literals and heap allocation nodes.

        Parameters:
            source_code: A string of valid Python source code.

        Returns:
            A dictionary with keys:
            - ``"primitive_literals"``: list of ``(line, col, value_repr)`` tuples.
            - ``"heap_allocations"``: list of ``(line, col, kind)`` tuples.
            - ``"parse_error"``: error message if parsing failed, else ``None``.
            - ``"source_length"``: character count of the source.
        """
        result: dict[str, Any] = {
            "primitive_literals": [],
            "heap_allocations": [],
            "parse_error": None,
            "source_length": len(source_code),
        }
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            result["parse_error"] = str(exc)
            self._analysis_log.append((time.time(), f"analyze_source: parse error — {exc}"))
            return result

        visitor = ASTValueVisitor()
        visitor.visit(tree)

        for node, value in visitor.primitive_nodes:
            result["primitive_literals"].append((
                getattr(node, "lineno", 0),
                getattr(node, "col_offset", 0),
                _safe_repr(value),
            ))
        for node, kind in visitor.heap_nodes:
            result["heap_allocations"].append((
                getattr(node, "lineno", 0),
                getattr(node, "col_offset", 0),
                kind,
            ))
        self._analysis_log.append((time.time(), f"analyze_source: {len(result['primitive_literals'])} primitives, {len(result['heap_allocations'])} heap allocs"))
        return result

    def build_value_report(self, objects: list[Any]) -> dict[str, Any]:
        """Build a comprehensive value classification report for *objects*.

        Each object in the list is classified, and a :class:`ValueRecord` is
        created for it.  The resulting report summarises the distribution of
        primitives and heap-mediated values.

        Parameters:
            objects: A list of Python objects to classify.

        Returns:
            A dictionary with keys:
            - ``"version"``: report schema version string.
            - ``"report_id"``: unique report identifier.
            - ``"section"``: section title.
            - ``"total"``: total number of objects.
            - ``"primitive_count"``: number of primitives.
            - ``"heap_mediated_count"``: number of heap-mediated objects.
            - ``"records"``: list of serialised :class:`ValueRecord` dicts.
            - ``"generated_at"``: Unix timestamp.
        """
        records = []
        prim_count = 0
        heap_count = 0
        for i, obj in enumerate(objects):
            kind_str = self.classify_object(obj)
            kind = ValueKind.PRIMITIVE if kind_str == "primitive" else ValueKind.HEAP_MEDIATED
            is_prim = kind == ValueKind.PRIMITIVE
            if is_prim:
                prim_count += 1
                oid = 0
                ckey = f"val:{_safe_repr(obj, 40)}"
                interned = _is_interned_string(obj) if isinstance(obj, str) else _is_small_integer(obj) if isinstance(obj, int) else False
            else:
                heap_count += 1
                oid = id(obj)
                ckey = f"id:{oid}"
                interned = False
            rec = ValueRecord(
                record_id=_new_uid(),
                label=f"obj_{i}",
                value_repr=_safe_repr(obj),
                value_kind=kind,
                object_type=type(obj).__name__,
                object_id=oid,
                coordinate_key=ckey,
                is_mutable=not is_prim and not isinstance(obj, (frozenset, tuple)),
                is_interned=interned,
                observed_at=time.time(),
                provenance=("build_value_report",),
            )
            records.append(rec)
            self._records.append(rec)

        return {
            "version": VALUE_REPORT_VERSION,
            "report_id": _new_uid(),
            "section": SECTION_TITLE,
            "total": len(objects),
            REPORT_KEY_PRIMITIVE_COUNT: prim_count,
            REPORT_KEY_HEAP_COUNT: heap_count,
            "records": [r.to_dict() for r in records],
            REPORT_KEY_TIMESTAMP: time.time(),
        }

    def get_all_records(self) -> list[ValueRecord]:
        """Return a copy of all value records accumulated so far.

        Returns:
            A list of :class:`ValueRecord` instances.
        """
        return list(self._records)

    def get_statistics(self) -> dict[str, Any]:
        """Return statistics about classifications performed so far.

        Returns:
            A dictionary with ``"primitive_count"``, ``"heap_count"``,
            ``"total_count"``, ``"log_entries"``, and ``"coordinator_id"``.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "primitive_count": self._primitive_count,
            "heap_count": self._heap_count,
            "total_count": self._primitive_count + self._heap_count,
            "log_entries": len(self._analysis_log),
            "type_cache_size": len(self._type_cache),
        }

    def reset(self) -> None:
        """Reset all accumulated state to initial values.

        Clears records, type cache, analysis log, and counters.
        Preserves ``coordinator_id`` and ``created_at``.
        """
        self._records.clear()
        self._type_cache.clear()
        self._analysis_log.clear()
        self._primitive_count = 0
        self._heap_count = 0
        _log.debug("PrimitiveHeapMediatedValuesCoordinator %s: reset", self.coordinator_id)


# ---------------------------------------------------------------------------
# PrimitiveHeapMediatedValuesAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PrimitiveHeapMediatedValuesAnalyzer:
    """Analyses Python source for primitives vs heap-allocated objects.

    Uses :mod:`ast` and :mod:`inspect` to parse Python source code and
    classify each expression as a primitive literal or a heap allocation
    (constructor call, list/dict/set literal, comprehension, etc.).

    This analyser is the static counterpart to the runtime
    :class:`PrimitiveHeapMediatedValuesWitness`: together they provide
    both static (AST-level) and dynamic (runtime-witness) evidence for the
    primitive/heap classification.

    Attributes:
        _analysis_cache:  Memoisation cache from source hash → report dict.
        _visitor_log:     Log of (timestamp, node_count) tuples.
        analyzer_id:      Unique identifier for this analyser instance.
        created_at:       Creation timestamp.

    Examples:
        >>> analyzer = PrimitiveHeapMediatedValuesAnalyzer()
        >>> report = analyzer.build_analysis_report("x = [1, 2]\\ny = 3")
        >>> report["heap_allocation_count"]
        1
        >>> report["primitive_literal_count"]
        2
    """

    _analysis_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    _visitor_log: list[tuple[float, int]] = field(default_factory=list)
    analyzer_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def analyze_ast_node(self, node: ast.AST) -> dict[str, Any]:
        """Classify a single AST node as primitive literal or heap allocation.

        Parameters:
            node: Any ``ast.AST`` node.

        Returns:
            A dictionary with:
            - ``"node_type"``: the class name of the node.
            - ``"classification"``: ``"primitive_literal"``, ``"heap_allocation"``,
              or ``"other"``.
            - ``"value_repr"``: string representation of the node's value (for
              constants) or ``None``.
            - ``"lineno"``, ``"col_offset"``: source location.
        """
        classification = "other"
        value_repr = None
        if isinstance(node, ast.Constant):
            classification = "primitive_literal"
            value_repr = _safe_repr(node.value)
        elif isinstance(node, (ast.List, ast.Dict, ast.Set)):
            classification = "heap_allocation"
        elif isinstance(node, ast.Call):
            classification = "heap_allocation"
        elif isinstance(node, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
            classification = "heap_allocation"
        return {
            "node_type": type(node).__name__,
            "classification": classification,
            "value_repr": value_repr,
            "lineno": getattr(node, "lineno", None),
            "col_offset": getattr(node, "col_offset", None),
        }

    def find_primitive_literals(self, source: str) -> list[dict[str, Any]]:
        """Find all primitive literal nodes in *source*.

        Parameters:
            source: Python source code string.

        Returns:
            A list of node summary dicts (see :meth:`analyze_ast_node`), one
            per :class:`ast.Constant` node found.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                results.append(self.analyze_ast_node(node))
        self._visitor_log.append((time.time(), len(results)))
        return results

    def find_heap_allocations(self, source: str) -> list[dict[str, Any]]:
        """Find all heap allocation nodes in *source*.

        A "heap allocation" is any expression that produces a fresh object:
        list/dict/set literals, calls, and comprehensions.

        Parameters:
            source: Python source code string.

        Returns:
            A list of node summary dicts (see :meth:`analyze_ast_node`), one
            per heap-allocation node found.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        results = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.Call,
                                  ast.ListComp, ast.DictComp, ast.SetComp,
                                  ast.GeneratorExp)):
                results.append(self.analyze_ast_node(node))
        self._visitor_log.append((time.time(), len(results)))
        return results

    def classify_expression(self, expr_node: ast.expr) -> ValueKind:
        """Classify an expression AST node as :class:`ValueKind`.

        Parameters:
            expr_node: An ``ast.expr`` node.

        Returns:
            :attr:`ValueKind.PRIMITIVE` for constant literals,
            :attr:`ValueKind.HEAP_MEDIATED` for allocation expressions,
            :attr:`ValueKind.UNKNOWN` otherwise.
        """
        if isinstance(expr_node, ast.Constant):
            return ValueKind.PRIMITIVE
        if isinstance(expr_node, (ast.List, ast.Dict, ast.Set, ast.Call,
                                   ast.ListComp, ast.DictComp, ast.SetComp,
                                   ast.GeneratorExp, ast.Tuple)):
            return ValueKind.HEAP_MEDIATED
        return ValueKind.UNKNOWN

    def _hash_source(self, source: str) -> str:
        """Return a short hash of *source* for memoisation.

        Parameters:
            source: Python source code string.

        Returns:
            A hex digest string of the first 16 bytes.
        """
        import hashlib
        return hashlib.md5(source.encode(), usedforsecurity=False).hexdigest()[:16]

    def build_analysis_report(self, source: str) -> dict[str, Any]:
        """Build a comprehensive analysis report for *source*.

        Parses and walks the AST of *source*, classifying every expression
        node.  The report includes counts, details of primitives and heap
        allocations, and per-line statistics.

        Parameters:
            source: A string of valid (or invalid) Python source code.

        Returns:
            A dictionary with:
            - ``"analyzer_id"``: this analyser's ID.
            - ``"source_length"``: length of *source*.
            - ``"parse_ok"``: ``True`` if parsing succeeded.
            - ``"parse_error"``: error string, or ``None``.
            - ``"primitive_literal_count"``: count of primitive literals.
            - ``"heap_allocation_count"``: count of heap allocation nodes.
            - ``"primitive_literals"``: list of node detail dicts.
            - ``"heap_allocations"``: list of node detail dicts.
            - ``"call_site_count"``: number of ``ast.Call`` nodes.
            - ``"assignment_count"``: number of ``ast.Assign`` nodes.
            - ``"node_total"``: total AST nodes visited.
            - ``"generated_at"``: Unix timestamp.
        """
        h = self._hash_source(source)
        if h in self._analysis_cache:
            return self._analysis_cache[h]

        report: dict[str, Any] = {
            "analyzer_id": self.analyzer_id,
            "source_length": len(source),
            "parse_ok": False,
            "parse_error": None,
            "primitive_literal_count": 0,
            "heap_allocation_count": 0,
            "primitive_literals": [],
            "heap_allocations": [],
            "call_site_count": 0,
            "assignment_count": 0,
            "node_total": 0,
            "generated_at": time.time(),
        }
        try:
            tree = ast.parse(source)
            report["parse_ok"] = True
        except SyntaxError as exc:
            report["parse_error"] = str(exc)
            return report

        visitor = ASTValueVisitor()
        visitor.visit(tree)
        all_nodes = list(ast.walk(tree))
        report["node_total"] = len(all_nodes)

        prim_details = []
        heap_details = []
        for node in all_nodes:
            detail = self.analyze_ast_node(node)
            if detail["classification"] == "primitive_literal":
                prim_details.append(detail)
            elif detail["classification"] == "heap_allocation":
                heap_details.append(detail)

        report["primitive_literals"] = prim_details
        report["heap_allocations"] = heap_details
        report["primitive_literal_count"] = len(prim_details)
        report["heap_allocation_count"] = len(heap_details)
        report["call_site_count"] = len(visitor.call_sites)
        report["assignment_count"] = sum(1 for n in all_nodes if isinstance(n, ast.Assign))
        self._analysis_cache[h] = report
        self._visitor_log.append((time.time(), report["node_total"]))
        return report

    def analyze_module_members(self, module: types.ModuleType) -> dict[str, str]:
        """Classify all members of a module using :func:`inspect.getmembers`.

        Parameters:
            module: A Python module object.

        Returns:
            A dict mapping member names to their classification strings
            (``"primitive"``, ``"function"``, ``"class"``, ``"module"``,
            or ``"instance"``).
        """
        result: dict[str, str] = {}
        try:
            for name, value in inspect.getmembers(module):
                if name.startswith("_"):
                    continue
                result[name] = _classify_type(type(value))
        except Exception as exc:  # noqa: BLE001
            _log.warning("analyze_module_members failed: %s", exc)
        return result

    def analyze_function_locals(self, func: types.FunctionType) -> dict[str, Any]:
        """Introspect a function's default arguments for primitive vs heap values.

        Uses ``inspect.getfullargspec`` to examine default values.

        Parameters:
            func: A Python function.

        Returns:
            A dict with ``"name"``, ``"defaults"`` (list of classified default
            values), and ``"annotations"`` summary.
        """
        result: dict[str, Any] = {"name": getattr(func, "__name__", "<unknown>"), "defaults": [], "annotations": {}}
        try:
            spec = inspect.getfullargspec(func)
            defaults = spec.defaults or ()
            for dv in defaults:
                result["defaults"].append({
                    "value_repr": _safe_repr(dv),
                    "kind": "primitive" if isinstance(dv, PRIMITIVE_TYPES) else "heap_mediated",
                })
            result["annotations"] = {k: str(v) for k, v in (spec.annotations or {}).items()}
        except (TypeError, AttributeError) as exc:
            _log.debug("analyze_function_locals: %s", exc)
        return result

    def get_cache_stats(self) -> dict[str, Any]:
        """Return statistics about the analysis cache.

        Returns:
            A dict with ``"cache_size"`` and ``"log_entries"``.
        """
        return {
            "cache_size": len(self._analysis_cache),
            "log_entries": len(self._visitor_log),
            "analyzer_id": self.analyzer_id,
        }


# ---------------------------------------------------------------------------
# PrimitiveHeapMediatedValuesWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PrimitiveHeapMediatedValuesWitness:
    """Runtime witness for primitive vs heap-mediated value observations.

    A *witness* observes live Python objects at runtime, records whether each
    is primitive or heap-mediated, and accumulates evidence that can be
    emitted as an evidence bundle.

    In sheaf terms, the witness is an *observation functor*: for each object
    it sees, it records the section type (global section for primitives, local
    section for heap objects) and the coordinate (value or identity).

    Attributes:
        _primitive_records:   :class:`ValueRecord` list for witnessed primitives.
        _heap_records:        :class:`ValueRecord` list for witnessed heap objects.
        _observation_log:     Chronological log of (timestamp, label, kind) tuples.
        _id_map:              Maps ``id(obj)`` → list of labels that reference it.
        witness_id:           Unique witness identifier.
        created_at:           Timestamp when this witness was created.

    Examples:
        >>> witness = PrimitiveHeapMediatedValuesWitness()
        >>> witness.witness_object(42, "x")
        >>> witness.witness_object([1, 2], "y")
        >>> summary = witness.get_witness_summary()
        >>> summary["primitive_count"]
        1
    """

    _primitive_records: list[ValueRecord] = field(default_factory=list)
    _heap_records: list[ValueRecord] = field(default_factory=list)
    _observation_log: list[tuple[float, str, str]] = field(default_factory=list)
    _id_map: dict[int, list[str]] = field(default_factory=dict)
    witness_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def witness_object(self, obj: Any, label: str = UNLABELLED_WITNESS) -> ValueRecord:
        """Observe *obj* and record a :class:`ValueRecord`.

        Classifies *obj* as primitive or heap-mediated and appends a record
        to the appropriate accumulator.

        Parameters:
            obj:   The Python object to witness.
            label: A human-readable label for this observation (e.g. variable name).

        Returns:
            The :class:`ValueRecord` created for this observation.
        """
        is_prim = isinstance(obj, PRIMITIVE_TYPES)
        kind = ValueKind.PRIMITIVE if is_prim else ValueKind.HEAP_MEDIATED
        oid = 0 if is_prim else id(obj)
        ckey = f"val:{_safe_repr(obj, 40)}" if is_prim else f"id:{oid}"
        interned = (
            (_is_interned_string(obj) if isinstance(obj, str) else _is_small_integer(obj) if isinstance(obj, int) else False)
            if is_prim else False
        )
        rec = ValueRecord(
            record_id=_new_uid(),
            label=label,
            value_repr=_safe_repr(obj),
            value_kind=kind,
            object_type=type(obj).__name__,
            object_id=oid,
            coordinate_key=ckey,
            is_mutable=not is_prim and not isinstance(obj, (frozenset, tuple)),
            is_interned=interned,
            observed_at=time.time(),
            provenance=(self.witness_id, label),
        )
        if is_prim:
            self._primitive_records.append(rec)
        else:
            self._heap_records.append(rec)
            if oid not in self._id_map:
                self._id_map[oid] = []
            self._id_map[oid].append(label)
        self._observation_log.append((time.time(), label, kind.value))
        return rec

    def record_primitive_witness(self, obj: Any) -> ValueRecord:
        """Force-record *obj* as a primitive observation.

        This bypasses the automatic classification and always uses
        :attr:`ValueKind.PRIMITIVE`.  Useful for testing and stub injection.

        Parameters:
            obj: The Python object to record as primitive.

        Returns:
            The :class:`ValueRecord` created.
        """
        rec = ValueRecord(
            record_id=_new_uid(),
            label=UNLABELLED_WITNESS,
            value_repr=_safe_repr(obj),
            value_kind=ValueKind.PRIMITIVE,
            object_type=type(obj).__name__,
            object_id=0,
            coordinate_key=f"val:{_safe_repr(obj, 40)}",
            is_mutable=False,
            is_interned=False,
            observed_at=time.time(),
            provenance=(self.witness_id, "force_primitive"),
        )
        self._primitive_records.append(rec)
        self._observation_log.append((time.time(), UNLABELLED_WITNESS, "primitive"))
        return rec

    def record_heap_witness(self, obj: Any) -> ValueRecord:
        """Force-record *obj* as a heap-mediated observation.

        This bypasses the automatic classification and always uses
        :attr:`ValueKind.HEAP_MEDIATED`.

        Parameters:
            obj: The Python object to record as heap-mediated.

        Returns:
            The :class:`ValueRecord` created.
        """
        oid = id(obj)
        rec = ValueRecord(
            record_id=_new_uid(),
            label=UNLABELLED_WITNESS,
            value_repr=_safe_repr(obj),
            value_kind=ValueKind.HEAP_MEDIATED,
            object_type=type(obj).__name__,
            object_id=oid,
            coordinate_key=f"id:{oid}",
            is_mutable=not isinstance(obj, (frozenset, tuple)),
            is_interned=False,
            observed_at=time.time(),
            provenance=(self.witness_id, "force_heap"),
        )
        self._heap_records.append(rec)
        if oid not in self._id_map:
            self._id_map[oid] = []
        self._id_map[oid].append(UNLABELLED_WITNESS)
        self._observation_log.append((time.time(), UNLABELLED_WITNESS, "heap_mediated"))
        return rec

    def find_aliased_heap_objects(self) -> list[tuple[int, list[str]]]:
        """Return all heap objects that were observed under multiple labels.

        Two observations alias iff they share the same ``id(obj)`` value.
        This method returns all identity coordinates with more than one
        label, indicating aliased references.

        Returns:
            A list of ``(object_id, label_list)`` tuples for aliased objects.
        """
        return [(oid, labels) for oid, labels in self._id_map.items() if len(labels) > 1]

    def generate_evidence_bundle(self) -> dict[str, Any]:
        """Generate an evidence bundle from all accumulated observations.

        Returns a dictionary modelling a sheaf-theoretic evidence bundle:
        a collection of witness records partitioned into primitive and
        heap-mediated categories, with summary statistics and provenance.

        Returns:
            A dictionary with:
            - ``"bundle_id"``: unique bundle identifier.
            - ``"witness_id"``: this witness's ID.
            - ``"channel"``: evidence channel name.
            - ``"primitive_records"``: list of serialised primitive records.
            - ``"heap_records"``: list of serialised heap records.
            - ``"aliased_objects"``: list of ``(id, labels)`` for aliased objects.
            - ``"statistics"``: summary statistics dict.
            - ``"generated_at"``: Unix timestamp.
        """
        aliased = self.find_aliased_heap_objects()
        return {
            "bundle_id": _new_uid(),
            "witness_id": self.witness_id,
            "channel": EVIDENCE_CHANNEL_NAME,
            "section": SECTION_TITLE,
            "primitive_records": [r.to_dict() for r in self._primitive_records],
            "heap_records": [r.to_dict() for r in self._heap_records],
            "aliased_objects": [(oid, labels) for oid, labels in aliased],
            "statistics": self.get_witness_summary(),
            "generated_at": time.time(),
        }

    def get_witness_summary(self) -> dict[str, Any]:
        """Return a concise summary of all observations.

        Returns:
            A dictionary with ``"primitive_count"``, ``"heap_mediated_count"``,
            ``"total_observations"``, ``"aliased_object_count"``,
            ``"unique_heap_ids"``, and ``"witness_id"``.
        """
        return {
            "witness_id": self.witness_id,
            "primitive_count": len(self._primitive_records),
            "heap_mediated_count": len(self._heap_records),
            "total_observations": len(self._observation_log),
            "aliased_object_count": sum(1 for labels in self._id_map.values() if len(labels) > 1),
            "unique_heap_ids": len(self._id_map),
        }

    def reset(self) -> None:
        """Clear all accumulated witness records and logs.

        Preserves ``witness_id`` and ``created_at``.
        """
        self._primitive_records.clear()
        self._heap_records.clear()
        self._observation_log.clear()
        self._id_map.clear()
        _log.debug("PrimitiveHeapMediatedValuesWitness %s: reset", self.witness_id)

    def get_observation_log(self) -> list[tuple[float, str, str]]:
        """Return a copy of the raw observation log.

        Returns:
            A list of ``(timestamp, label, kind_str)`` tuples.
        """
        return list(self._observation_log)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick sanity check for the primitive/heap-mediated machinery.

    Exercises :class:`PrimitiveHeapMediatedValuesCoordinator`,
    :class:`PrimitiveHeapMediatedValuesAnalyzer`, and
    :class:`PrimitiveHeapMediatedValuesWitness` with a small set of objects
    and a minimal source snippet.  Raises :class:`AssertionError` on failure.
    """
    print(f"[{_ANALYSIS_CHANNEL}] smoke test starting …")

    # --- Coordinator ---
    coord = PrimitiveHeapMediatedValuesCoordinator()
    assert coord.classify_object(42) == "primitive", "42 should be primitive"
    assert coord.classify_object(3.14) == "primitive", "3.14 should be primitive"
    assert coord.classify_object("hello") == "primitive", "str should be primitive"
    assert coord.classify_object(None) == "primitive", "None should be primitive"
    assert coord.classify_object([]) == "heap_mediated", "[] should be heap_mediated"
    assert coord.classify_object({}) == "heap_mediated", "{} should be heap_mediated"
    assert coord.classify_object(object()) == "heap_mediated", "object() should be heap_mediated"
    assert coord.get_coordinate_kind(42) == "value_coordinate"
    assert coord.get_coordinate_kind([]) == "identity_coordinate"
    assert coord.is_primitive(True)
    assert not coord.is_heap_mediated(b"bytes")

    src = "x = 42\ny = [1, 2, 3]\nz = {'a': 1}"
    src_report = coord.analyze_source(src)
    assert src_report["parse_error"] is None, "parse_error should be None"
    assert len(src_report["primitive_literals"]) >= 4, "expected ≥4 primitive literals"
    assert len(src_report["heap_allocations"]) >= 2, "expected ≥2 heap allocations"

    value_report = coord.build_value_report([1, "hi", [], {}, None, (1,)])
    assert value_report["primitive_count"] == 3, f"expected 3 primitives, got {value_report['primitive_count']}"
    assert value_report["heap_mediated_count"] == 3, f"expected 3 heap, got {value_report['heap_mediated_count']}"

    stats = coord.get_statistics()
    assert stats["total_count"] >= 6

    # --- Analyzer ---
    analyzer = PrimitiveHeapMediatedValuesAnalyzer()
    prims = analyzer.find_primitive_literals("x = 1\ny = 'hi'\nz = True")
    assert len(prims) >= 3, f"expected ≥3 primitives, got {len(prims)}"
    heaps = analyzer.find_heap_allocations("a = []\nb = {}\nc = set()")
    assert len(heaps) >= 3, f"expected ≥3 heap allocs, got {len(heaps)}"

    full_report = analyzer.build_analysis_report("x = 42\ny = [1]\nz = dict(a=1)")
    assert full_report["parse_ok"]
    assert full_report["primitive_literal_count"] >= 2
    assert full_report["heap_allocation_count"] >= 2

    import math
    members = analyzer.analyze_module_members(math)
    assert "pi" in members
    assert members["pi"] == "primitive"

    # --- Witness ---
    witness = PrimitiveHeapMediatedValuesWitness()
    r1 = witness.witness_object(99, "a")
    r2 = witness.witness_object(99, "b")
    assert r1.value_kind == ValueKind.PRIMITIVE
    obj = [1, 2]
    r3 = witness.witness_object(obj, "lst_a")
    r4 = witness.witness_object(obj, "lst_b")
    assert r3.value_kind == ValueKind.HEAP_MEDIATED
    assert r3.object_id == r4.object_id, "same object → same id"
    aliased = witness.find_aliased_heap_objects()
    assert len(aliased) == 1, f"expected 1 aliased object, got {len(aliased)}"
    bundle = witness.generate_evidence_bundle()
    assert bundle["channel"] == EVIDENCE_CHANNEL_NAME
    assert bundle["statistics"]["primitive_count"] == 2
    assert bundle["statistics"]["heap_mediated_count"] == 2
    summary = witness.get_witness_summary()
    assert summary["aliased_object_count"] == 1

    print(f"[{_ANALYSIS_CHANNEL}] smoke test PASSED ✓")
    print(f"  coordinator stats: {coord.get_statistics()}")
    print(f"  analyzer cache: {analyzer.get_cache_stats()}")
    print(f"  witness summary: {summary}")


if __name__ == "__main__":
    _smoke_test()
