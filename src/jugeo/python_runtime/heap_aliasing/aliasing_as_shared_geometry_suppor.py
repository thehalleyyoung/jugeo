"""Aliasing as shared geometry — theory2.tex Ch17, §5 — Aliasing as Shared Geometry.

This module implements the machinery for detecting, classifying, and reasoning
about aliasing relationships in live Python namespaces and in static Python
source code.  In the sheaf-theoretic model of theory2.tex Ch17 §5, two names
alias iff they point to the same identity coordinate (``id(a) == id(b)``), which
corresponds to sharing the same open set in the topology of the heap site.

The *shared geometry* of two aliases is the shared support region over which
their sections agree.  If ``a`` and ``b`` alias and we mutate ``a.x``, then
``b.x`` changes too — they share the same section over the same coordinate.

# copilot: s02 — aliasing as shared geometry; builds alias graphs, computes
#           shared coordinate support, and witnesses alias relationships at
#           both static (AST) and runtime levels.

Typical usage::

    coordinator = AliasingSharedGeometrySupportCoordinator()
    aliases = coordinator.detect_aliases({"x": obj, "y": obj, "z": other})
    graph = coordinator.build_alias_graph([obj, obj, other])
    report = coordinator.get_geometry_report()

    analyzer = AliasingSharedGeometrySupportAnalyzer()
    alias_map = analyzer.build_alias_map(source_code)

    witness = AliasingSharedGeometrySupportWitness()
    witness.witness_assignment("x", obj)
    witness.witness_assignment("y", obj)
    evidence = witness.generate_alias_evidence()
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
        kind_map = {list: ObjectKind.CONTAINER, dict: ObjectKind.CONTAINER,
                    set: ObjectKind.CONTAINER, tuple: ObjectKind.CONTAINER}
        tp = type(obj)
        kind = kind_map.get(tp, ObjectKind.INSTANCE)
        return HeapObject(
            object_id=id(obj),
            type_name=tp.__name__,
            kind=kind,
            field_keys=frozenset(),
            creation_site=creation_site,
            created_at=time.time(),
        )


_log = logging.getLogger(__name__)

_ANALYSIS_CHANNEL: str = "copilot-s02-aliasing-shared-geometry"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Edge kind for directly-detected alias pairs (``a is b``).
DIRECT_ALIAS_EDGE_KIND: str = "direct_alias"

#: Edge kind for aliases inferred through container membership.
CONTAINER_ALIAS_EDGE_KIND: str = "container_member_alias"

#: Edge kind for aliases detected via attribute chains.
ATTRIBUTE_ALIAS_EDGE_KIND: str = "attribute_chain_alias"

#: Edge kind for aliases found by inspecting function arguments.
ARGUMENT_ALIAS_EDGE_KIND: str = "argument_alias"

#: Maximum number of all-pairs comparisons before emitting a warning.
ALL_PAIRS_WARN_THRESHOLD: int = 512

#: Sentinel key used for objects with no deterministic label.
UNLABELLED_KEY: str = "<unlabelled>"

#: Maximum alias class size stored without truncation.
MAX_CLASS_SIZE: int = 256

#: Version string for generated geometry reports.
GEOMETRY_REPORT_VERSION: str = "1.0.0"

#: Section title used in reports.
SECTION_TITLE: str = "Aliasing as shared geometry"

#: Maximum depth for recursive container traversal during alias detection.
MAX_CONTAINER_DEPTH: int = 8

#: Minimum confidence score for an alias edge to be included in the graph.
MIN_EDGE_CONFIDENCE: float = 0.5

#: Channel name for evidence emission.
EVIDENCE_CHANNEL_NAME: str = "heap-aliasing.alias-geometry"

#: Python primitive types excluded from alias analysis (they have no identity).
PRIMITIVE_TYPES: tuple[type, ...] = (int, float, complex, bool, str, bytes, bytearray, type(None))

#: AST assignment node types tracked by the static analyser.
ASSIGNMENT_NODE_TYPES: tuple[type, ...] = (ast.Assign, ast.AugAssign, ast.AnnAssign)

#: Label used when a target name cannot be determined from an AST node.
UNKNOWN_TARGET: str = "<unknown-target>"

#: Default weight for alias edges in the geometry graph.
DEFAULT_EDGE_WEIGHT: float = 1.0

#: Maximum number of mutation propagation steps to trace.
MAX_MUTATION_DEPTH: int = 32


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _new_uid() -> str:
    """Return a fresh unique identifier string.

    Returns:
        A hex UUID4 string prefixed with ``"uid_"``.
    """
    return f"uid_{uuid.uuid4().hex[:12]}"


def _identity_key(obj: Any) -> str:
    """Return the canonical identity key for a live Python object.

    For primitive types (int, float, bool, str, bytes, NoneType), which have no
    meaningful heap identity, returns a value-based key.  For all other objects,
    returns an id-based key.

    Parameters:
        obj: Any live Python object.

    Returns:
        A string of the form ``"id:<id>"`` or ``"val:<repr>"``.
    """
    if isinstance(obj, PRIMITIVE_TYPES):
        try:
            r = repr(obj)
        except Exception:  # noqa: BLE001
            r = type(obj).__name__
        return f"val:{r[:40]}"
    return f"id:{id(obj)}"


def _safe_repr(obj: Any, max_len: int = 80) -> str:
    """Return a safe, length-limited repr of *obj*.

    Parameters:
        obj:     The object to represent.
        max_len: Maximum character length.

    Returns:
        A string representation truncated to *max_len* characters.
    """
    try:
        r = repr(obj)
    except Exception:  # noqa: BLE001
        r = f"<repr-error:{type(obj).__name__}>"
    return r[:max_len - 1] + "…" if len(r) > max_len else r


def _object_fields(obj: Any) -> list[tuple[str, Any]]:
    """Return a list of ``(name, value)`` field pairs for *obj*.

    Uses ``inspect.getmembers`` for user-defined objects and ``vars()``
    for objects with ``__dict__``.  Returns an empty list on failure.

    Parameters:
        obj: Any Python object.

    Returns:
        A list of ``(field_name, field_value)`` tuples for non-callable
        attributes whose names do not start with ``"__"``.
    """
    result: list[tuple[str, Any]] = []
    try:
        if hasattr(obj, "__dict__"):
            for k, v in vars(obj).items():
                if not k.startswith("__"):
                    result.append((k, v))
        else:
            for name, value in inspect.getmembers(obj, predicate=lambda v: not callable(v)):
                if not name.startswith("__"):
                    result.append((name, value))
    except Exception:  # noqa: BLE001
        pass
    return result


def _extract_assign_targets(node: ast.Assign) -> list[str]:
    """Extract target name strings from an ``ast.Assign`` node.

    Parameters:
        node: An ``ast.Assign`` AST node.

    Returns:
        A list of target name strings (only simple ``ast.Name`` targets).
    """
    names: list[str] = []
    for target in node.targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Tuple):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    names.append(elt.id)
    return names


def _is_alias_assignment(node: ast.Assign) -> bool:
    """Heuristically detect whether *node* is an alias assignment.

    An alias assignment has the form ``a = b`` where ``b`` is a plain name
    (not a call, literal, or expression).

    Parameters:
        node: An ``ast.Assign`` node.

    Returns:
        ``True`` if the RHS is a simple name (i.e. this is likely an alias).
    """
    return isinstance(node.value, ast.Name)


# ---------------------------------------------------------------------------
# UnionFind
# ---------------------------------------------------------------------------


class UnionFind:
    """Union-find (disjoint-set) forest with path compression and union by rank.

    Used to maintain alias equivalence classes over reference keys.  Each
    component is an alias class: a set of names that all point to the same
    heap object.

    Attributes:
        _parent: Maps each key to its parent in the forest.
        _rank:   Maps each key to its rank for union-by-rank.
        _size:   Maps each root to the size of its component.

    Examples:
        >>> uf = UnionFind()
        >>> uf.add("a")
        >>> uf.add("b")
        >>> uf.union("a", "b")
        >>> uf.find("a") == uf.find("b")
        True
    """

    def __init__(self) -> None:
        """Initialise an empty union-find forest."""
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}
        self._size: dict[str, int] = {}

    def add(self, key: str) -> None:
        """Add *key* as a singleton component.

        If *key* is already present this is a no-op.

        Parameters:
            key: A string identifier for the element.
        """
        if key not in self._parent:
            self._parent[key] = key
            self._rank[key] = 0
            self._size[key] = 1

    def find(self, key: str) -> str:
        """Return the root representative of *key*'s component.

        Applies path compression.

        Parameters:
            key: A string identifier.

        Returns:
            The root representative string.

        Raises:
            KeyError: If *key* has not been added.
        """
        if self._parent[key] != key:
            self._parent[key] = self.find(self._parent[key])
        return self._parent[key]

    def union(self, key_a: str, key_b: str) -> bool:
        """Merge the components of *key_a* and *key_b*.

        Uses union-by-rank to keep the tree shallow.

        Parameters:
            key_a: First element key.
            key_b: Second element key.

        Returns:
            ``True`` if the components were distinct and were merged;
            ``False`` if they were already in the same component.
        """
        ra, rb = self.find(key_a), self.find(key_b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] = self._size.get(ra, 1) + self._size.get(rb, 1)
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        return True

    def components(self) -> dict[str, list[str]]:
        """Return all components as a mapping from root → member list.

        Returns:
            A dictionary mapping each root key to the list of members in
            its component (including the root itself).
        """
        groups: dict[str, list[str]] = {}
        for key in self._parent:
            root = self.find(key)
            groups.setdefault(root, []).append(key)
        return groups

    def are_aliases(self, key_a: str, key_b: str) -> bool:
        """Return ``True`` if *key_a* and *key_b* are in the same component.

        Parameters:
            key_a: First element key.
            key_b: Second element key.

        Returns:
            ``True`` if both keys share a root representative.
        """
        if key_a not in self._parent or key_b not in self._parent:
            return False
        return self.find(key_a) == self.find(key_b)

    def component_size(self, key: str) -> int:
        """Return the size of the component containing *key*.

        Parameters:
            key: An element key.

        Returns:
            The number of elements in *key*'s component.
        """
        if key not in self._parent:
            return 0
        root = self.find(key)
        return self._size.get(root, 1)

    def all_keys(self) -> list[str]:
        """Return all registered keys.

        Returns:
            A list of all keys added to this union-find structure.
        """
        return list(self._parent.keys())


# ---------------------------------------------------------------------------
# AliasGeometryRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AliasGeometryRecord:
    """Immutable record of a detected alias relationship.

    In sheaf terms, an alias is a pair of sections at the *same* identity
    coordinate.  The shared support region is the singleton ``{id(obj)}``.

    Attributes:
        record_id:       Unique record identifier.
        key_a:           Identity key of the first reference.
        key_b:           Identity key of the second reference.
        label_a:         Human-readable name for the first reference.
        label_b:         Human-readable name for the second reference.
        shared_coord:    The shared identity coordinate key.
        edge_kind:       The kind of alias edge detected.
        confidence:      Confidence score in ``[0.0, 1.0]``.
        object_type:     Type name of the shared object.
        detected_at:     Unix timestamp of detection.
        provenance:      Tuple of provenance labels.
    """

    record_id: str
    key_a: str
    key_b: str
    label_a: str
    label_b: str
    shared_coord: str
    edge_kind: str
    confidence: float
    object_type: str
    detected_at: float
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Returns:
            A JSON-serialisable dict with all record fields.
        """
        return {
            "record_id": self.record_id,
            "key_a": self.key_a,
            "key_b": self.key_b,
            "label_a": self.label_a,
            "label_b": self.label_b,
            "shared_coord": self.shared_coord,
            "edge_kind": self.edge_kind,
            "confidence": self.confidence,
            "object_type": self.object_type,
            "detected_at": self.detected_at,
            "provenance": list(self.provenance),
        }

    def is_high_confidence(self) -> bool:
        """Return ``True`` if the confidence score is above 0.8.

        Returns:
            ``True`` when ``confidence > 0.8``.
        """
        return self.confidence > 0.8

    def involves_label(self, label: str) -> bool:
        """Return ``True`` if *label* is either :attr:`label_a` or :attr:`label_b`.

        Parameters:
            label: A reference label to check.

        Returns:
            ``True`` if *label* matches either end of this alias record.
        """
        return label in (self.label_a, self.label_b)


# ---------------------------------------------------------------------------
# MutationPropagationRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MutationPropagationRecord:
    """Records the propagation of a mutation through an alias group.

    When one member of an alias group is mutated, all other members observe
    the same change (they share the same section over the shared coordinate).

    Attributes:
        record_id:       Unique record identifier.
        shared_coord:    Identity coordinate of the mutated object.
        field_name:      Name of the mutated field.
        old_value_repr:  Repr of the field value before mutation.
        new_value_repr:  Repr of the field value after mutation.
        affected_labels: Labels of all references that observed the mutation.
        propagated_at:   Unix timestamp of the mutation.
        provenance:      Tuple of provenance labels.
    """

    record_id: str
    shared_coord: str
    field_name: str
    old_value_repr: str
    new_value_repr: str
    affected_labels: tuple[str, ...]
    propagated_at: float
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Returns:
            A JSON-serialisable dict with all record fields.
        """
        return {
            "record_id": self.record_id,
            "shared_coord": self.shared_coord,
            "field_name": self.field_name,
            "old_value_repr": self.old_value_repr,
            "new_value_repr": self.new_value_repr,
            "affected_labels": list(self.affected_labels),
            "propagated_at": self.propagated_at,
            "provenance": list(self.provenance),
        }

    def affected_count(self) -> int:
        """Return the number of labels affected by this mutation propagation.

        Returns:
            Length of :attr:`affected_labels`.
        """
        return len(self.affected_labels)


# ---------------------------------------------------------------------------
# AliasAssignmentVisitor
# ---------------------------------------------------------------------------


class AliasAssignmentVisitor(ast.NodeVisitor):
    """AST visitor that collects assignment and augmented-assignment nodes.

    Walks a Python AST and accumulates information about all assignment
    statements, distinguishing between alias assignments (``a = b``) and
    initialisation assignments (``a = expr``).

    Attributes:
        alias_assignments:  List of ``(targets, source_name)`` tuples for
                            simple name-to-name assignments.
        init_assignments:   List of ``(targets, node_type)`` tuples for
                            assignments with complex RHS expressions.
        aug_assignments:    List of ``(target_name, op_name)`` tuples for
                            augmented assignments (``+=``, etc.).
        name_loads:         List of ``ast.Name`` nodes with ``Load`` context.
        name_stores:        List of ``ast.Name`` nodes with ``Store`` context.
        attribute_accesses: List of ``(attr_name, lineno)`` tuples for attribute
                            access patterns.
    """

    def __init__(self) -> None:
        """Initialise the visitor with empty accumulators."""
        self.alias_assignments: list[tuple[list[str], str]] = []
        self.init_assignments: list[tuple[list[str], str]] = []
        self.aug_assignments: list[tuple[str, str]] = []
        self.name_loads: list[ast.Name] = []
        self.name_stores: list[ast.Name] = []
        self.attribute_accesses: list[tuple[str, int]] = []

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        """Process a simple assignment node.

        Parameters:
            node: The ``ast.Assign`` node to visit.
        """
        targets = _extract_assign_targets(node)
        if isinstance(node.value, ast.Name):
            self.alias_assignments.append((targets, node.value.id))
        else:
            self.init_assignments.append((targets, type(node.value).__name__))
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        """Process an augmented assignment node.

        Parameters:
            node: The ``ast.AugAssign`` node to visit.
        """
        if isinstance(node.target, ast.Name):
            self.aug_assignments.append((node.target.id, type(node.op).__name__))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        """Process an annotated assignment node.

        Parameters:
            node: The ``ast.AnnAssign`` node to visit.
        """
        if node.value is not None and isinstance(node.target, ast.Name):
            if isinstance(node.value, ast.Name):
                self.alias_assignments.append(([node.target.id], node.value.id))
            else:
                self.init_assignments.append(([node.target.id], type(node.value).__name__))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        """Record name load/store events.

        Parameters:
            node: The ``ast.Name`` node to visit.
        """
        if isinstance(node.ctx, ast.Load):
            self.name_loads.append(node)
        elif isinstance(node.ctx, ast.Store):
            self.name_stores.append(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        """Record attribute access patterns.

        Parameters:
            node: The ``ast.Attribute`` node to visit.
        """
        self.attribute_accesses.append((node.attr, getattr(node, "lineno", 0)))
        self.generic_visit(node)

    def summary(self) -> dict[str, Any]:
        """Return a summary of all accumulated observations.

        Returns:
            A dictionary with counts of alias assignments, init assignments,
            augmented assignments, name loads/stores, and attribute accesses.
        """
        return {
            "alias_assignment_count": len(self.alias_assignments),
            "init_assignment_count": len(self.init_assignments),
            "aug_assignment_count": len(self.aug_assignments),
            "name_load_count": len(self.name_loads),
            "name_store_count": len(self.name_stores),
            "attribute_access_count": len(self.attribute_accesses),
            "alias_pairs": [(tgts, src) for tgts, src in self.alias_assignments[:10]],
        }


# ---------------------------------------------------------------------------
# AliasingSharedGeometrySupportCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasingSharedGeometrySupportCoordinator:
    """Coordinates alias geometry analysis over live Python namespaces.

    The coordinator detects alias relationships, builds alias graphs, computes
    shared identity coordinates, and traces mutation propagation through alias
    groups.

    In the sheaf model, two names alias iff they share the same open set
    ``{id(obj)}`` in the topology.  The coordinator maintains a
    :class:`UnionFind` structure over identity keys to represent alias
    equivalence classes, and records :class:`AliasGeometryRecord` instances
    for each detected alias pair.

    Attributes:
        _uf:                 Union-find for alias equivalence classes.
        _alias_records:      All detected alias records.
        _mutation_records:   All mutation propagation records.
        _geometry_log:       Log of (timestamp, event_label) tuples.
        _coord_map:          Maps identity key → list of reference labels.
        coordinator_id:      Unique coordinator identifier.
        created_at:          Creation timestamp.

    Examples:
        >>> coord = AliasingSharedGeometrySupportCoordinator()
        >>> obj = [1, 2]
        >>> result = coord.detect_aliases({"a": obj, "b": obj, "c": [3, 4]})
        >>> len(result)
        1
    """

    _uf: UnionFind = field(default_factory=UnionFind)
    _alias_records: list[AliasGeometryRecord] = field(default_factory=list)
    _mutation_records: list[MutationPropagationRecord] = field(default_factory=list)
    _geometry_log: list[tuple[float, str]] = field(default_factory=list)
    _coord_map: dict[str, list[str]] = field(default_factory=dict)
    coordinator_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def detect_aliases(self, namespace: dict[str, Any]) -> list[AliasGeometryRecord]:
        """Detect all alias pairs in *namespace*.

        Compares each pair of values using ``is`` to detect shared identity
        coordinates.  Primitive types are excluded from alias analysis since
        they have no meaningful heap identity.

        Parameters:
            namespace: A dictionary mapping names to Python objects.

        Returns:
            A list of :class:`AliasGeometryRecord` instances, one per detected
            alias pair.
        """
        items = [(k, v) for k, v in namespace.items() if not isinstance(v, PRIMITIVE_TYPES)]
        if len(items) > ALL_PAIRS_WARN_THRESHOLD:
            _log.warning("detect_aliases: large namespace (%d items), may be slow", len(items))

        detected: list[AliasGeometryRecord] = []
        for i, (name_a, obj_a) in enumerate(items):
            key_a = _identity_key(obj_a)
            self._uf.add(key_a)
            if key_a not in self._coord_map:
                self._coord_map[key_a] = []
            if name_a not in self._coord_map[key_a]:
                self._coord_map[key_a].append(name_a)

            for name_b, obj_b in items[i + 1:]:
                if obj_a is obj_b:
                    key_b = _identity_key(obj_b)
                    self._uf.add(key_b)
                    self._uf.union(key_a, key_b)
                    rec = AliasGeometryRecord(
                        record_id=_new_uid(),
                        key_a=key_a,
                        key_b=key_b,
                        label_a=name_a,
                        label_b=name_b,
                        shared_coord=key_a,
                        edge_kind=DIRECT_ALIAS_EDGE_KIND,
                        confidence=1.0,
                        object_type=type(obj_a).__name__,
                        detected_at=time.time(),
                        provenance=(self.coordinator_id, "detect_aliases"),
                    )
                    detected.append(rec)
                    self._alias_records.append(rec)

        self._geometry_log.append((time.time(), f"detect_aliases: {len(detected)} alias pairs in namespace of {len(namespace)}"))
        return detected

    def build_alias_graph(self, objects: list[Any]) -> dict[str, Any]:
        """Build an alias graph over a list of objects.

        Constructs a graph where nodes are identity keys and edges connect
        objects that are identical (``a is b``).

        Parameters:
            objects: A list of Python objects to compare.

        Returns:
            A dictionary with:
            - ``"nodes"``: list of ``{"key": …, "type": …}`` dicts.
            - ``"edges"``: list of ``{"source": …, "target": …, "kind": …}`` dicts.
            - ``"component_count"``: number of alias equivalence classes.
            - ``"alias_pair_count"``: total number of alias edges.
        """
        non_prim = [(i, obj) for i, obj in enumerate(objects) if not isinstance(obj, PRIMITIVE_TYPES)]
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for idx, obj in non_prim:
            key = _identity_key(obj)
            if key not in seen_keys:
                nodes.append({"key": key, "type": type(obj).__name__, "index": idx})
                seen_keys.add(key)
                self._uf.add(key)

        for i, (idx_a, obj_a) in enumerate(non_prim):
            ka = _identity_key(obj_a)
            for _, (idx_b, obj_b) in enumerate(non_prim[i + 1:], start=i + 1):
                if obj_a is obj_b:
                    kb = _identity_key(obj_b)
                    edges.append({"source": ka, "target": kb, "kind": DIRECT_ALIAS_EDGE_KIND, "weight": DEFAULT_EDGE_WEIGHT})
                    self._uf.union(ka, kb)

        components = self._uf.components()
        self._geometry_log.append((time.time(), f"build_alias_graph: {len(nodes)} nodes, {len(edges)} edges"))
        return {
            "nodes": nodes,
            "edges": edges,
            "component_count": len(components),
            "alias_pair_count": len(edges),
        }

    def compute_shared_coordinates(self, refs: list[Any]) -> dict[str, list[Any]]:
        """Compute the shared identity coordinates for a list of references.

        Groups objects by their identity coordinate (``id(obj)``), returning
        a mapping from coordinate key to the list of objects at that coordinate.

        Parameters:
            refs: A list of Python objects (references).

        Returns:
            A dictionary mapping identity coordinate keys to lists of objects
            at that coordinate.
        """
        coord_groups: dict[str, list[Any]] = {}
        for obj in refs:
            if isinstance(obj, PRIMITIVE_TYPES):
                continue
            key = _identity_key(obj)
            coord_groups.setdefault(key, []).append(obj)
        self._geometry_log.append((time.time(), f"compute_shared_coordinates: {len(coord_groups)} distinct coords from {len(refs)} refs"))
        return coord_groups

    def analyze_mutation_propagation(
        self,
        alias_group: list[Any],
        field: str,
        new_value: Any,
    ) -> MutationPropagationRecord:
        """Simulate and record mutation propagation through an alias group.

        When one member of an alias group has its ``field`` mutated to
        ``new_value``, the change is visible through all aliases.  This method
        records the propagation event and returns a
        :class:`MutationPropagationRecord`.

        Parameters:
            alias_group: A list of objects that are aliases of each other.
            field:       The field name being mutated.
            new_value:   The new value for the field.

        Returns:
            A :class:`MutationPropagationRecord` describing the propagation.
        """
        if not alias_group:
            return MutationPropagationRecord(
                record_id=_new_uid(),
                shared_coord="",
                field_name=field,
                old_value_repr="<empty-group>",
                new_value_repr=_safe_repr(new_value),
                affected_labels=(),
                propagated_at=time.time(),
                provenance=(self.coordinator_id,),
            )

        representative = alias_group[0]
        coord_key = _identity_key(representative)
        old_val_repr = _safe_repr(getattr(representative, field, "<no-field>"))
        affected: list[str] = []
        for i, obj in enumerate(alias_group):
            affected.append(f"alias_{i}[{type(obj).__name__}]")

        rec = MutationPropagationRecord(
            record_id=_new_uid(),
            shared_coord=coord_key,
            field_name=field,
            old_value_repr=old_val_repr,
            new_value_repr=_safe_repr(new_value),
            affected_labels=tuple(affected),
            propagated_at=time.time(),
            provenance=(self.coordinator_id, "analyze_mutation_propagation"),
        )
        self._mutation_records.append(rec)
        self._geometry_log.append((time.time(), f"mutation propagation on coord={coord_key} field={field} affects {len(affected)} refs"))
        return rec

    def get_geometry_report(self) -> dict[str, Any]:
        """Return a comprehensive geometry report for all accumulated state.

        Returns:
            A dictionary with:
            - ``"version"``: report version string.
            - ``"coordinator_id"``: this coordinator's ID.
            - ``"section"``: section title.
            - ``"alias_record_count"``: total alias records.
            - ``"mutation_record_count"``: total mutation records.
            - ``"alias_class_count"``: number of alias equivalence classes.
            - ``"alias_records"``: list of serialised alias record dicts (first 50).
            - ``"mutation_records"``: list of serialised mutation record dicts (first 20).
            - ``"geometry_log_entries"``: number of log entries.
            - ``"generated_at"``: Unix timestamp.
        """
        comps = self._uf.components()
        alias_classes = [v for v in comps.values() if len(v) > 1]
        return {
            "version": GEOMETRY_REPORT_VERSION,
            "coordinator_id": self.coordinator_id,
            "section": SECTION_TITLE,
            "alias_record_count": len(self._alias_records),
            "mutation_record_count": len(self._mutation_records),
            "alias_class_count": len(alias_classes),
            "total_component_count": len(comps),
            "alias_records": [r.to_dict() for r in self._alias_records[:50]],
            "mutation_records": [r.to_dict() for r in self._mutation_records[:20]],
            "geometry_log_entries": len(self._geometry_log),
            "generated_at": time.time(),
        }

    def build_restriction_morphism(self, source_id: str, target_id: str) -> dict[str, Any]:
        """Build a restriction morphism descriptor between two identity coordinates.

        In sheaf theory, a restriction morphism maps sections from a larger
        open set to a smaller one.  For aliases, the restriction from
        ``{source_id}`` to ``{target_id}`` is valid iff ``source_id == target_id``
        (they share the same identity coordinate).

        Parameters:
            source_id: Identity key of the source coordinate.
            target_id: Identity key of the target coordinate.

        Returns:
            A dictionary describing the morphism:
            - ``"morphism_id"``: unique identifier.
            - ``"source"``: source coordinate key.
            - ``"target"``: target coordinate key.
            - ``"kind"``: morphism kind string.
            - ``"is_valid"``: ``True`` if source and target share a component.
            - ``"component_root"``: root of the shared component, or ``None``.
        """
        self._uf.add(source_id)
        self._uf.add(target_id)
        is_valid = self._uf.are_aliases(source_id, target_id)
        root = self._uf.find(source_id) if is_valid else None
        morphism = {
            "morphism_id": _new_uid(),
            "source": source_id,
            "target": target_id,
            "kind": MorphismKind.RESTRICTION.value if is_valid else "invalid",
            "is_valid": is_valid,
            "component_root": root,
        }
        self._geometry_log.append((time.time(), f"restriction_morphism: {source_id}→{target_id} valid={is_valid}"))
        return morphism

    def get_alias_classes(self) -> list[list[str]]:
        """Return all alias equivalence classes with more than one member.

        Returns:
            A list of lists, where each inner list is an alias class (all
            members share the same identity coordinate).
        """
        comps = self._uf.components()
        return [sorted(members) for members in comps.values() if len(members) > 1]

    def reset(self) -> None:
        """Reset all accumulated state to initial values.

        Creates a fresh :class:`UnionFind` and clears all record lists and logs.
        Preserves ``coordinator_id`` and ``created_at``.
        """
        self._uf = UnionFind()
        self._alias_records.clear()
        self._mutation_records.clear()
        self._geometry_log.clear()
        self._coord_map.clear()
        _log.debug("AliasingSharedGeometrySupportCoordinator %s: reset", self.coordinator_id)


# ---------------------------------------------------------------------------
# AliasingSharedGeometrySupportAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasingSharedGeometrySupportAnalyzer:
    """Statically analyses Python source for aliasing patterns.

    Uses :mod:`ast` and :mod:`inspect` to find alias assignments, augmented
    assignments, and name-binding patterns in Python source code.

    This is the static counterpart to the runtime
    :class:`AliasingSharedGeometrySupportWitness`: together they provide
    both AST-level and dynamic evidence for alias analysis.

    Attributes:
        _parse_cache:   Memoisation cache from source hash → parsed AST.
        _report_cache:  Memoisation cache from source hash → report dict.
        _visitor_log:   Log of (timestamp, node_count) tuples.
        analyzer_id:    Unique analyser identifier.
        created_at:     Creation timestamp.

    Examples:
        >>> analyzer = AliasingSharedGeometrySupportAnalyzer()
        >>> alias_map = analyzer.build_alias_map("a = b = []\\nc = a")
        >>> "c" in alias_map["alias_sources"]
        True
    """

    _parse_cache: dict[str, ast.Module] = field(default_factory=dict)
    _report_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    _visitor_log: list[tuple[float, int]] = field(default_factory=list)
    analyzer_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def _hash_source(self, source: str) -> str:
        """Return a short hash of *source*.

        Parameters:
            source: Python source code string.

        Returns:
            A hex MD5 digest string (first 16 chars).
        """
        import hashlib
        return hashlib.md5(source.encode(), usedforsecurity=False).hexdigest()[:16]

    def _parse_source(self, source: str) -> ast.Module | None:
        """Parse *source*, using the cache if available.

        Parameters:
            source: Python source code string.

        Returns:
            The parsed :class:`ast.Module`, or ``None`` on parse error.
        """
        h = self._hash_source(source)
        if h in self._parse_cache:
            return self._parse_cache[h]
        try:
            tree = ast.parse(source)
            self._parse_cache[h] = tree
            return tree
        except SyntaxError:
            return None

    def analyze_assignments(self, source: str) -> dict[str, Any]:
        """Analyse all assignment statements in *source*.

        Detects alias assignments (``a = b``), initialisation assignments
        (``a = <expr>``), and augmented assignments (``a += …``).

        Parameters:
            source: Python source code string.

        Returns:
            A summary dict from :meth:`AliasAssignmentVisitor.summary` plus
            ``"parse_ok"`` and ``"analyzer_id"`` keys.
        """
        tree = self._parse_source(source)
        if tree is None:
            return {"parse_ok": False, "analyzer_id": self.analyzer_id}
        visitor = AliasAssignmentVisitor()
        visitor.visit(tree)
        self._visitor_log.append((time.time(), sum(1 for _ in ast.walk(tree))))
        summary = visitor.summary()
        summary["parse_ok"] = True
        summary["analyzer_id"] = self.analyzer_id
        return summary

    def find_alias_assignments(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Find all alias assignment nodes in *tree*.

        An alias assignment has the form ``target = source_name`` where the
        RHS is a plain :class:`ast.Name` node (not a call or literal).

        Parameters:
            tree: A parsed :class:`ast.Module`.

        Returns:
            A list of dicts with ``"targets"``, ``"source"``, ``"lineno"``
            for each alias assignment found.
        """
        results: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _is_alias_assignment(node):
                targets = _extract_assign_targets(node)
                if targets:
                    results.append({
                        "targets": targets,
                        "source": node.value.id,  # type: ignore[union-attr]
                        "lineno": getattr(node, "lineno", None),
                    })
        return results

    def detect_augmented_assignments(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Find all augmented assignment nodes in *tree*.

        Augmented assignments (``a += x``, ``a |= y``, etc.) can break aliasing
        for immutable types (``str``, ``int``) but preserve it for mutable
        containers (``list``, ``dict``).

        Parameters:
            tree: A parsed :class:`ast.Module`.

        Returns:
            A list of dicts with ``"target"``, ``"op"``, ``"lineno"`` for each
            augmented assignment found.
        """
        results: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AugAssign):
                target_name = node.target.id if isinstance(node.target, ast.Name) else UNKNOWN_TARGET
                results.append({
                    "target": target_name,
                    "op": type(node.op).__name__,
                    "lineno": getattr(node, "lineno", None),
                })
        return results

    def classify_binding(self, node: ast.stmt) -> str:
        """Classify a statement node as a binding kind.

        Parameters:
            node: Any ``ast.stmt`` node.

        Returns:
            One of: ``"alias_assignment"``, ``"init_assignment"``,
            ``"augmented_assignment"``, ``"annotated_assignment"``, or
            ``"other"``.
        """
        if isinstance(node, ast.Assign):
            return "alias_assignment" if _is_alias_assignment(node) else "init_assignment"
        if isinstance(node, ast.AugAssign):
            return "augmented_assignment"
        if isinstance(node, ast.AnnAssign):
            return "annotated_assignment"
        return "other"

    def build_alias_map(self, source: str) -> dict[str, Any]:
        """Build a static alias map from *source*.

        Identifies which names are potentially aliases of other names based
        purely on assignment structure.  Returns a map from alias target names
        to their source names.

        Parameters:
            source: Python source code string.

        Returns:
            A dictionary with:
            - ``"alias_sources"``: dict mapping target_name → source_name for
              alias assignments.
            - ``"alias_chains"``: list of name chains (a→b→c) inferred by
              transitivity.
            - ``"aug_assignments"``: list of augmented assignment dicts.
            - ``"parse_ok"``: whether parsing succeeded.
            - ``"analyzer_id"``: this analyser's ID.
        """
        tree = self._parse_source(source)
        if tree is None:
            return {"alias_sources": {}, "alias_chains": [], "aug_assignments": [], "parse_ok": False, "analyzer_id": self.analyzer_id}

        alias_pairs = self.find_alias_assignments(tree)
        alias_sources: dict[str, str] = {}
        for entry in alias_pairs:
            for tgt in entry["targets"]:
                alias_sources[tgt] = entry["source"]

        # Compute transitive chains
        def _chain(name: str, visited: set[str]) -> list[str]:
            if name in visited or name not in alias_sources:
                return [name]
            visited.add(name)
            return [name] + _chain(alias_sources[name], visited)

        chains: list[list[str]] = []
        for tgt in alias_sources:
            chain = _chain(tgt, set())
            if len(chain) > 1:
                chains.append(chain)

        aug = self.detect_augmented_assignments(tree)
        return {
            "alias_sources": alias_sources,
            "alias_chains": chains,
            "aug_assignments": aug,
            "parse_ok": True,
            "analyzer_id": self.analyzer_id,
        }

    def find_shared_references(self, namespace: dict[str, Any]) -> dict[str, list[str]]:
        """Find shared object references in a live namespace dict.

        Compares all non-primitive values by identity (``is``) and groups names
        that point to the same object.

        Parameters:
            namespace: A dictionary of name → object bindings.

        Returns:
            A dictionary mapping identity coordinate key → list of names
            that reference the same object.  Only entries with >1 name are
            included.
        """
        coord_to_names: dict[str, list[str]] = {}
        for name, obj in namespace.items():
            if isinstance(obj, PRIMITIVE_TYPES):
                continue
            key = _identity_key(obj)
            coord_to_names.setdefault(key, []).append(name)
        return {k: v for k, v in coord_to_names.items() if len(v) > 1}

    def analyze_module_structure(self, module: types.ModuleType) -> dict[str, Any]:
        """Analyse the live namespace of *module* for shared references.

        Uses :func:`inspect.getmembers` and :attr:`types.MappingProxyType`
        semantics to inspect the module's ``__dict__``.

        Parameters:
            module: A Python module object.

        Returns:
            A dictionary with ``"shared_references"``, ``"member_count"``,
            and ``"module_name"``.
        """
        try:
            ns = vars(module)
        except TypeError:
            ns = {}
        shared = self.find_shared_references(dict(ns))
        member_count = len(ns)
        return {
            "module_name": getattr(module, "__name__", "<unknown>"),
            "member_count": member_count,
            "shared_references": shared,
            "analyzer_id": self.analyzer_id,
        }

    def get_cache_stats(self) -> dict[str, Any]:
        """Return statistics about the parse and report caches.

        Returns:
            A dict with ``"parse_cache_size"``, ``"report_cache_size"``,
            ``"log_entries"``, and ``"analyzer_id"``.
        """
        return {
            "parse_cache_size": len(self._parse_cache),
            "report_cache_size": len(self._report_cache),
            "log_entries": len(self._visitor_log),
            "analyzer_id": self.analyzer_id,
        }


# ---------------------------------------------------------------------------
# AliasingSharedGeometrySupportWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasingSharedGeometrySupportWitness:
    """Runtime witness for alias relationships in live Python code.

    Observes name→object bindings as they occur at runtime, records alias pairs,
    and maintains a union-find structure for alias equivalence classes.
    Evidence is accumulated and can be emitted as an evidence bundle.

    Attributes:
        _uf:              Union-find for alias equivalence classes.
        _bindings:        Ordered list of ``(name, identity_key)`` pairs.
        _alias_pairs:     List of ``(name_a, name_b, coord)`` alias tuples.
        _alias_records:   List of :class:`AliasGeometryRecord` instances.
        _observation_log: Log of ``(timestamp, name, kind)`` tuples.
        _coord_to_names:  Maps identity key → list of names.
        witness_id:       Unique witness identifier.
        created_at:       Creation timestamp.

    Examples:
        >>> witness = AliasingSharedGeometrySupportWitness()
        >>> obj = [1, 2]
        >>> witness.witness_assignment("x", obj)
        >>> witness.witness_assignment("y", obj)
        >>> summary = witness.get_alias_summary()
        >>> summary["alias_class_count"]
        1
    """

    _uf: UnionFind = field(default_factory=UnionFind)
    _bindings: list[tuple[str, str]] = field(default_factory=list)
    _alias_pairs: list[tuple[str, str, str]] = field(default_factory=list)
    _alias_records: list[AliasGeometryRecord] = field(default_factory=list)
    _observation_log: list[tuple[float, str, str]] = field(default_factory=list)
    _coord_to_names: dict[str, list[str]] = field(default_factory=dict)
    witness_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def witness_assignment(self, name: str, obj: Any) -> str:
        """Record that *name* is bound to *obj*.

        If another name already references *obj* (i.e. ``id(other) == id(obj)``),
        this creates an alias relationship and a corresponding
        :class:`AliasGeometryRecord` is created.

        Parameters:
            name: The variable name being bound.
            obj:  The Python object being bound to *name*.

        Returns:
            The identity coordinate key for *obj*.
        """
        if isinstance(obj, PRIMITIVE_TYPES):
            self._observation_log.append((time.time(), name, "primitive_skip"))
            return _identity_key(obj)

        key = _identity_key(obj)
        self._uf.add(key)
        self._bindings.append((name, key))
        self._observation_log.append((time.time(), name, "heap_binding"))

        existing_names = self._coord_to_names.get(key, [])
        for existing_name in existing_names:
            rec = AliasGeometryRecord(
                record_id=_new_uid(),
                key_a=key,
                key_b=key,
                label_a=existing_name,
                label_b=name,
                shared_coord=key,
                edge_kind=DIRECT_ALIAS_EDGE_KIND,
                confidence=1.0,
                object_type=type(obj).__name__,
                detected_at=time.time(),
                provenance=(self.witness_id, "witness_assignment"),
            )
            self._alias_records.append(rec)
            self._alias_pairs.append((existing_name, name, key))

        self._coord_to_names.setdefault(key, []).append(name)
        return key

    def detect_shared_geometry(self, namespace: dict[str, Any]) -> list[AliasGeometryRecord]:
        """Detect shared geometry (alias pairs) in *namespace*.

        Iterates over all non-primitive bindings in *namespace* and calls
        :meth:`witness_assignment` for each, returning the list of alias
        records created.

        Parameters:
            namespace: A dictionary of name → object bindings.

        Returns:
            A list of :class:`AliasGeometryRecord` instances for all alias
            pairs detected.
        """
        before = len(self._alias_records)
        for name, obj in namespace.items():
            self.witness_assignment(name, obj)
        return self._alias_records[before:]

    def record_alias_pair(self, name_a: str, name_b: str, obj: Any) -> AliasGeometryRecord:
        """Explicitly record an alias pair ``(name_a, name_b)`` pointing to *obj*.

        Parameters:
            name_a: First name in the alias pair.
            name_b: Second name in the alias pair.
            obj:    The shared Python object.

        Returns:
            The :class:`AliasGeometryRecord` created for this pair.
        """
        key = _identity_key(obj)
        self._uf.add(key)
        self._uf.union(key, key)
        rec = AliasGeometryRecord(
            record_id=_new_uid(),
            key_a=key,
            key_b=key,
            label_a=name_a,
            label_b=name_b,
            shared_coord=key,
            edge_kind=DIRECT_ALIAS_EDGE_KIND,
            confidence=1.0,
            object_type=type(obj).__name__,
            detected_at=time.time(),
            provenance=(self.witness_id, "record_alias_pair"),
        )
        self._alias_records.append(rec)
        self._alias_pairs.append((name_a, name_b, key))
        self._coord_to_names.setdefault(key, [])
        for n in (name_a, name_b):
            if n not in self._coord_to_names[key]:
                self._coord_to_names[key].append(n)
        self._observation_log.append((time.time(), f"{name_a}≡{name_b}", "explicit_alias"))
        return rec

    def generate_alias_evidence(self) -> dict[str, Any]:
        """Generate an evidence bundle from all accumulated alias observations.

        Returns:
            A dictionary modelling a sheaf-theoretic evidence bundle:
            - ``"bundle_id"``: unique identifier.
            - ``"witness_id"``: this witness's ID.
            - ``"channel"``: evidence channel name.
            - ``"section"``: section title.
            - ``"alias_records"``: list of serialised alias record dicts.
            - ``"alias_classes"``: list of alias equivalence class lists.
            - ``"statistics"``: summary statistics.
            - ``"generated_at"``: Unix timestamp.
        """
        alias_classes = self.compute_alias_classes()
        return {
            "bundle_id": _new_uid(),
            "witness_id": self.witness_id,
            "channel": EVIDENCE_CHANNEL_NAME,
            "section": SECTION_TITLE,
            "alias_records": [r.to_dict() for r in self._alias_records],
            "alias_classes": alias_classes,
            "statistics": self.get_alias_summary(),
            "generated_at": time.time(),
        }

    def compute_alias_classes(self) -> list[list[str]]:
        """Return all alias equivalence classes with more than one member.

        Returns:
            A list of lists, where each inner list contains the names that
            share the same identity coordinate.
        """
        # Build from _coord_to_names
        classes = []
        for key, names in self._coord_to_names.items():
            if len(names) > 1:
                classes.append(sorted(set(names)))
        return classes

    def get_alias_summary(self) -> dict[str, Any]:
        """Return a concise summary of accumulated alias observations.

        Returns:
            A dictionary with ``"alias_record_count"``, ``"alias_class_count"``,
            ``"total_bindings"``, ``"unique_coords"``, ``"observation_count"``,
            and ``"witness_id"``.
        """
        alias_classes = self.compute_alias_classes()
        return {
            "witness_id": self.witness_id,
            "alias_record_count": len(self._alias_records),
            "alias_class_count": len(alias_classes),
            "total_bindings": len(self._bindings),
            "unique_coords": len(self._coord_to_names),
            "observation_count": len(self._observation_log),
        }

    def reset(self) -> None:
        """Clear all accumulated witness records and logs.

        Preserves ``witness_id`` and ``created_at``.
        """
        self._uf = UnionFind()
        self._bindings.clear()
        self._alias_pairs.clear()
        self._alias_records.clear()
        self._observation_log.clear()
        self._coord_to_names.clear()
        _log.debug("AliasingSharedGeometrySupportWitness %s: reset", self.witness_id)

    def get_observation_log(self) -> list[tuple[float, str, str]]:
        """Return a copy of the raw observation log.

        Returns:
            A list of ``(timestamp, name, event_kind)`` tuples.
        """
        return list(self._observation_log)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick sanity check for the aliasing-as-shared-geometry machinery.

    Exercises :class:`AliasingSharedGeometrySupportCoordinator`,
    :class:`AliasingSharedGeometrySupportAnalyzer`, and
    :class:`AliasingSharedGeometrySupportWitness` with small examples.
    Raises :class:`AssertionError` on failure.
    """
    print(f"[{_ANALYSIS_CHANNEL}] smoke test starting …")

    # --- UnionFind ---
    uf = UnionFind()
    uf.add("a"); uf.add("b"); uf.add("c")
    assert not uf.are_aliases("a", "b")
    uf.union("a", "b")
    assert uf.are_aliases("a", "b")
    assert not uf.are_aliases("a", "c")
    comps = uf.components()
    assert len(comps) == 2
    assert uf.component_size("a") == 2
    assert uf.component_size("c") == 1

    # --- Coordinator ---
    coord = AliasingSharedGeometrySupportCoordinator()
    obj1 = [1, 2, 3]
    obj2 = {"k": "v"}
    namespace = {"x": obj1, "y": obj1, "z": obj2}
    records = coord.detect_aliases(namespace)
    assert len(records) == 1, f"expected 1 alias pair, got {len(records)}"
    assert records[0].label_a in ("x", "y") and records[0].label_b in ("x", "y")
    assert records[0].edge_kind == DIRECT_ALIAS_EDGE_KIND

    graph = coord.build_alias_graph([obj1, obj1, obj2, obj1])
    assert graph["alias_pair_count"] >= 3, f"expected ≥3 edges, got {graph['alias_pair_count']}"

    shared = coord.compute_shared_coordinates([obj1, obj1, obj2])
    assert len(shared) == 2  # obj1 appears twice at same coord

    mut_rec = coord.analyze_mutation_propagation([obj1, obj1], "append", 99)
    assert mut_rec.affected_count() == 2
    assert mut_rec.field_name == "append"

    report = coord.get_geometry_report()
    assert report["alias_record_count"] >= 1
    assert report["alias_class_count"] >= 1

    morph = coord.build_restriction_morphism(f"id:{id(obj1)}", f"id:{id(obj1)}")
    assert morph["is_valid"]
    morph2 = coord.build_restriction_morphism(f"id:{id(obj1)}", f"id:{id(obj2)}")
    assert not morph2["is_valid"]

    alias_classes = coord.get_alias_classes()
    assert len(alias_classes) >= 1

    # --- Analyzer ---
    analyzer = AliasingSharedGeometrySupportAnalyzer()
    src = "x = []\ny = x\nz = y\na = {}\nb = a\nc = 42"
    asgn = analyzer.analyze_assignments(src)
    assert asgn["parse_ok"]
    assert asgn["alias_assignment_count"] >= 2, f"got {asgn}"

    tree = ast.parse(src)
    alias_nodes = analyzer.find_alias_assignments(tree)
    assert len(alias_nodes) >= 2

    aug_src = "x = []\nx += [1]\nx |= set()"
    aug_tree = ast.parse(aug_src)
    aug_nodes = analyzer.detect_augmented_assignments(aug_tree)
    assert len(aug_nodes) >= 2

    alias_map = analyzer.build_alias_map(src)
    assert "y" in alias_map["alias_sources"]
    assert alias_map["alias_sources"]["y"] == "x"

    shared_ns = analyzer.find_shared_references({"p": obj1, "q": obj1, "r": obj2})
    assert len(shared_ns) == 1

    import math as math_mod
    mod_struct = analyzer.analyze_module_structure(math_mod)
    assert mod_struct["module_name"] == "math"

    # --- Witness ---
    witness = AliasingSharedGeometrySupportWitness()
    shared_obj = {"key": "value"}
    witness.witness_assignment("m", shared_obj)
    witness.witness_assignment("n", shared_obj)
    witness.witness_assignment("o", {"other": True})
    witness.witness_assignment("p", 42)  # primitive — skipped

    summary = witness.get_alias_summary()
    assert summary["alias_record_count"] == 1, f"expected 1, got {summary['alias_record_count']}"
    assert summary["alias_class_count"] == 1

    classes = witness.compute_alias_classes()
    assert len(classes) == 1
    assert set(classes[0]) == {"m", "n"}

    another = [1, 2]
    explicit_rec = witness.record_alias_pair("r1", "r2", another)
    assert explicit_rec.label_a == "r1"

    evidence = witness.generate_alias_evidence()
    assert evidence["channel"] == EVIDENCE_CHANNEL_NAME
    assert len(evidence["alias_records"]) >= 1

    namespace2 = {"aa": shared_obj, "bb": shared_obj, "cc": {}}
    new_records = witness.detect_shared_geometry(namespace2)
    assert len(new_records) >= 1

    print(f"[{_ANALYSIS_CHANNEL}] smoke test PASSED ✓")
    print(f"  coordinator report: alias_classes={report['alias_class_count']}")
    print(f"  analyzer alias_map: {alias_map['alias_sources']}")
    print(f"  witness summary: {summary}")


if __name__ == "__main__":
    _smoke_test()
