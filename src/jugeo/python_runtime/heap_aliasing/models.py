"""Core data models for heap aliasing analysis.

This module defines the immutable value objects used throughout the
``heap_aliasing`` package to represent heap objects, alias partitions,
mutation events, and heap sections.  All models follow the sheaf-theoretic
framework described in **theory2.tex Ch17**.

Copilot integration note
------------------------
This file was scaffolded with GitHub Copilot assistance.  The model
definitions here are the canonical data contract between the ``heap_aliasing``
analysis components and the broader jugeo copilot integration pipeline.
Downstream consumers (judgment producers, visualization tools, the copilot
skill registry) should import exclusively from this module.

Background (theory2.tex Ch17)
------------------------------
Every live Python object ``o`` corresponds to an *identity coordinate*: the
singleton coordinate ``{id(o)}``.  A *heap section* is a mapping from field
names to values over that coordinate.  Two references ``x`` and ``y`` alias
each other iff they share an identity coordinate, i.e. ``id(x) == id(y)``.

The key constructs modelled here are:

``IdentityCoordinate``
    The canonical coordinate for a heap object; wraps ``id(o)`` together with
    type metadata and a :class:`~jugeo.geometry.site.CoordinateObject`.

``HeapObject``
    A frozen snapshot of one heap object: its identity coordinate, type,
    field table, and a :class:`~jugeo.geometry.supports.SupportRegion`.

``AliasPartition``
    An equivalence class of references that all point to the same heap object,
    i.e., share the same identity coordinate.

``MutationEvent``
    A record of a single field write, including which aliases were affected
    and whether the descent (sheaf) condition was satisfied.

``HeapSection``
    A section of the heap sheaf: a mapping from field names to values over the
    support defined by an :class:`IdentityCoordinate`.

``AliasEdge``
    A directed edge in the alias graph between two reference keys, with
    confidence and provenance metadata.

``HeapSnapshot``
    A point-in-time snapshot of all heap objects, alias partitions, and
    sections.

``MutationPatch``
    A proposed set of field updates to apply atomically to a
    :class:`HeapSection`, subject to the descent check.

Usage example
-------------
>>> from jugeo.python_runtime.heap_aliasing.models import (
...     ObjectKind, IdentityCoordinate, make_identity_coordinate,
...     make_heap_object, make_empty_snapshot,
... )
>>> x = [1, 2, 3]
>>> ic = make_identity_coordinate(x, "example.py:1")
>>> ic.type_name
'list'
>>> ho = make_heap_object(x, "example.py:1")
>>> ho.is_container()
True
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    CoordinateKind,
    CoordinateObject,
)
from jugeo.geometry.supports import SupportRegion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Coordinate component prefix for all heap identity coordinates.
HEAP_COORD_COMPONENT: str = "heap"

#: Coordinate component used for the address segment.
ID_COORD_COMPONENT: str = "id"

#: String value used for unknown / unavailable type names.
UNKNOWN_TYPE_NAME: str = "<unknown>"

#: String value used when the creation site cannot be determined.
UNKNOWN_SITE: str = "<unknown_site>"

#: Minimum confidence value for alias edges.
MIN_CONFIDENCE: float = 0.0

#: Maximum confidence value for alias edges.
MAX_CONFIDENCE: float = 1.0

#: Alias kind constant: direct reference equality (``is`` operator).
ALIAS_KIND_DIRECT: str = "direct"

#: Alias kind constant: inferred through transitive alias propagation.
ALIAS_KIND_TRANSITIVE: str = "transitive"

#: Alias kind constant: suggested by static analysis (heuristic).
ALIAS_KIND_HEURISTIC: str = "heuristic"

#: Maximum number of field entries stored in a HeapObject.
MAX_FIELD_COUNT: int = 256

#: Sentinel string for a field value that cannot be serialised.
UNSERIALIZABLE_FIELD_VALUE: str = "<unserializable>"

#: Version of the serialisation schema used by models in this module.
MODEL_SCHEMA_VERSION: str = "1"


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _make_heap_coordinate(object_id: int) -> CoordinateObject:
    """Construct the canonical identity coordinate for a heap object.

    Parameters
    ----------
    object_id : int
        The CPython object id (as returned by ``id()``).

    Returns
    -------
    CoordinateObject
        A :class:`~jugeo.geometry.site.Coordinate` with components
        ``("heap", "id", "<object_id>")`` and kind
        :attr:`~jugeo.geometry.site.CoordinateKind.REGION`.

    Examples
    --------
    >>> coord = _make_heap_coordinate(99999)
    >>> coord.components
    ('heap', 'id', '99999')
    """
    from jugeo.geometry.site import Coordinate  # local import to avoid circularity

    return Coordinate(
        components=(HEAP_COORD_COMPONENT, ID_COORD_COMPONENT, str(object_id)),
        kind=CoordinateKind.REGION,
    )


def _make_support_region(object_id: int) -> SupportRegion:
    """Construct the canonical support region for a heap object.

    Parameters
    ----------
    object_id : int
        The CPython object id.

    Returns
    -------
    SupportRegion
        A :class:`~jugeo.geometry.supports.SupportRegion` whose coordinate
        is the identity coordinate for ``object_id``.

    Examples
    --------
    >>> sr = _make_support_region(99999)
    >>> sr.coordinate.components[2]
    '99999'
    """
    coord = _make_heap_coordinate(object_id)
    return SupportRegion(
        coordinate=coord,
        patch_keys=frozenset({f"heap.id.{object_id}"}),
        labels=frozenset({"heap_object"}),
        provenance=(f"heap_aliasing.models._make_support_region({object_id})",),
    )


def _safe_repr(obj: Any, max_len: int = 120) -> str:
    """Return a truncated repr of ``obj``, never raising.

    Parameters
    ----------
    obj : Any
        Any Python object.
    max_len : int, optional
        Maximum length of the returned string.  Defaults to 120.

    Returns
    -------
    str
        A string representation, truncated to ``max_len`` characters.

    Examples
    --------
    >>> _safe_repr(42)
    '42'
    >>> _safe_repr("hello")
    "'hello'"
    """
    try:
        r = repr(obj)
    except Exception:  # noqa: BLE001
        r = UNSERIALIZABLE_FIELD_VALUE
    if len(r) > max_len:
        r = r[: max_len - 3] + "..."
    return r


def _new_uuid() -> str:
    """Return a fresh UUID4 hex string.

    Returns
    -------
    str
        A 32-character lowercase hex UUID string.

    Examples
    --------
    >>> uid = _new_uuid()
    >>> len(uid)
    32
    """
    return uuid.uuid4().hex


def _fingerprint(data: dict[str, Any]) -> str:
    """Compute a short fingerprint of a serialised dictionary.

    Parameters
    ----------
    data : dict[str, Any]
        A JSON-serialisable dictionary.

    Returns
    -------
    str
        First 12 characters of the SHA-256 hex digest.

    Examples
    --------
    >>> fp = _fingerprint({"key": "value"})
    >>> len(fp)
    12
    """
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# ObjectKind enum
# ---------------------------------------------------------------------------


class ObjectKind(str, Enum):
    """Categorisation of Python heap objects by structural role.

    Each value names a broad category of Python object.  The categorisation
    is used when deciding whether a mutation (field write) requires a descent
    check.

    Theory context (theory2.tex Ch17)
    ----------------------------------
    The kind of an object determines whether it can be *mutated* (i.e.,
    whether a descent check is required).  Primitives (int, str, etc.) are
    immutable; containers and custom objects are mutable and require a sheaf
    consistency check on each write.

    Values
    ------
    PRIMITIVE
        Immutable built-in scalars: ``int``, ``float``, ``complex``,
        ``bool``, ``bytes``, ``str``.
    CONTAINER
        Mutable or immutable container types: ``list``, ``dict``, ``set``,
        ``tuple``, ``frozenset``, ``deque``, etc.
    FUNCTION
        Callable objects: functions, methods, lambdas, built-in functions.
    CLASS
        Class objects and metaclasses.
    MODULE
        Module objects (``type(sys)``).
    CUSTOM
        User-defined class instances not covered by the other categories.
    NONE_TYPE
        The singleton ``None``.
    ELLIPSIS
        The singleton ``...`` (Ellipsis).

    Examples
    --------
    >>> ObjectKind.CONTAINER.is_mutable_kind()
    True
    >>> ObjectKind.PRIMITIVE.is_mutable_kind()
    False
    >>> ObjectKind.from_type_name("dict")
    <ObjectKind.CONTAINER: 'container'>
    >>> ObjectKind.FUNCTION.description()
    'Callable object: function, method, or built-in.'
    """

    PRIMITIVE = "primitive"
    CONTAINER = "container"
    FUNCTION = "function"
    CLASS = "class"
    MODULE = "module"
    CUSTOM = "custom"
    NONE_TYPE = "none_type"
    ELLIPSIS = "ellipsis"

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def is_mutable_kind(self) -> bool:
        """Return whether objects of this kind are mutable.

        Mutable objects require a descent check on each field write.
        Immutable objects (primitives, ``None``, ``...``) can never be
        mutated and therefore never require a descent check.

        Returns
        -------
        bool
            ``True`` for mutable categories; ``False`` for immutable ones.

        Examples
        --------
        >>> ObjectKind.CONTAINER.is_mutable_kind()
        True
        >>> ObjectKind.PRIMITIVE.is_mutable_kind()
        False
        >>> ObjectKind.NONE_TYPE.is_mutable_kind()
        False
        """
        return self in {
            ObjectKind.CONTAINER,
            ObjectKind.CUSTOM,
            ObjectKind.MODULE,
            ObjectKind.CLASS,
        }

    def description(self) -> str:
        """Return a human-readable description of this object kind.

        Returns
        -------
        str
            A short sentence describing the kind.

        Examples
        --------
        >>> ObjectKind.PRIMITIVE.description()
        'Immutable built-in scalar (int, float, str, bytes, bool, complex).'
        >>> ObjectKind.FUNCTION.description()
        'Callable object: function, method, or built-in.'
        """
        _descs: dict[str, str] = {
            "primitive": "Immutable built-in scalar (int, float, str, bytes, bool, complex).",
            "container": "Mutable or immutable container (list, dict, set, tuple, ...).",
            "function": "Callable object: function, method, or built-in.",
            "class": "Class object or metaclass.",
            "module": "Python module object.",
            "custom": "User-defined class instance.",
            "none_type": "The singleton None value.",
            "ellipsis": "The singleton Ellipsis (...) value.",
        }
        return _descs.get(self.value, f"Object kind: {self.value}")

    @classmethod
    def from_type_name(cls, type_name: str) -> "ObjectKind":
        """Infer an :class:`ObjectKind` from a Python type name string.

        Parameters
        ----------
        type_name : str
            The ``__name__`` of the type, e.g. ``"list"``, ``"int"``,
            ``"MyClass"``.

        Returns
        -------
        ObjectKind
            The best-matching :class:`ObjectKind` value.

        Examples
        --------
        >>> ObjectKind.from_type_name("list")
        <ObjectKind.CONTAINER: 'container'>
        >>> ObjectKind.from_type_name("int")
        <ObjectKind.PRIMITIVE: 'primitive'>
        >>> ObjectKind.from_type_name("NoneType")
        <ObjectKind.NONE_TYPE: 'none_type'>
        >>> ObjectKind.from_type_name("function")
        <ObjectKind.FUNCTION: 'function'>
        >>> ObjectKind.from_type_name("MyCustomClass")
        <ObjectKind.CUSTOM: 'custom'>
        """
        _primitives = frozenset(
            {"int", "float", "complex", "bool", "str", "bytes", "bytearray"}
        )
        _containers = frozenset(
            {
                "list", "dict", "set", "tuple", "frozenset", "deque",
                "OrderedDict", "defaultdict", "Counter",
            }
        )
        _functions = frozenset(
            {
                "function", "method", "builtin_function_or_method",
                "method-wrapper", "classmethod", "staticmethod",
            }
        )
        if type_name == "NoneType":
            return cls.NONE_TYPE
        if type_name == "ellipsis":
            return cls.ELLIPSIS
        if type_name in _primitives:
            return cls.PRIMITIVE
        if type_name in _containers:
            return cls.CONTAINER
        if type_name in _functions:
            return cls.FUNCTION
        if type_name == "module":
            return cls.MODULE
        if type_name == "type":
            return cls.CLASS
        return cls.CUSTOM


# ---------------------------------------------------------------------------
# IdentityCoordinate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IdentityCoordinate:
    """The canonical sheaf-theoretic coordinate for a single heap object.

    In the framework of theory2.tex Ch17, every Python object ``o`` occupies
    the singleton coordinate ``{id(o)}``.  :class:`IdentityCoordinate` wraps
    that raw integer together with type metadata and the associated
    :class:`~jugeo.geometry.site.CoordinateObject` so that downstream
    analysis components can reason about it geometrically.

    Fields
    ------
    object_id : int
        The CPython object identifier (``id(o)``).  This value is stable for
        the lifetime of the object but may be recycled after garbage
        collection.
    type_name : str
        The ``__name__`` of the object's type.
    creation_site : str
        A human-readable source location string, e.g. ``"mymodule.py:42"``.
    coordinate : CoordinateObject
        The geometric coordinate wrapping the identity address.
    is_interned : bool
        ``True`` if the object is an interned Python object (e.g. small ints
        or interned strings) that may be shared across multiple logical
        "variables".

    Examples
    --------
    >>> from jugeo.geometry.site import Coordinate, CoordinateKind
    >>> coord = Coordinate(components=("heap", "id", "100"), kind=CoordinateKind.REGION)
    >>> ic = IdentityCoordinate(
    ...     object_id=100,
    ...     type_name="int",
    ...     creation_site="test.py:1",
    ...     coordinate=coord,
    ...     is_interned=True,
    ... )
    >>> ic.to_key()
    'id:100:int'
    >>> ic.matches(ic)
    True
    """

    object_id: int
    type_name: str
    creation_site: str
    coordinate: CoordinateObject
    is_interned: bool

    def matches(self, other: "IdentityCoordinate") -> bool:
        """Return whether this coordinate refers to the same heap object.

        Two identity coordinates match iff their ``object_id`` values are
        equal.  Note that this is a *physical* equality check: it is possible
        for two coordinates with the same ``object_id`` to refer to *different*
        logical objects if the CPython allocator recycled the address.

        Parameters
        ----------
        other : IdentityCoordinate
            The coordinate to compare against.

        Returns
        -------
        bool
            ``True`` iff ``self.object_id == other.object_id``.

        Examples
        --------
        >>> from jugeo.geometry.site import Coordinate, CoordinateKind
        >>> c = Coordinate(components=("heap", "id", "1"), kind=CoordinateKind.REGION)
        >>> a = IdentityCoordinate(1, "int", "", c, False)
        >>> b = IdentityCoordinate(1, "str", "", c, False)
        >>> a.matches(b)
        True
        """
        return self.object_id == other.object_id

    def to_key(self) -> str:
        """Return a stable string key for this identity coordinate.

        The key format is ``"id:<object_id>:<type_name>"``.  Keys are used as
        dictionary keys, set members, and edge endpoints in alias graphs.

        Returns
        -------
        str
            A non-empty string key.

        Examples
        --------
        >>> from jugeo.geometry.site import Coordinate, CoordinateKind
        >>> c = Coordinate(components=("heap", "id", "42"), kind=CoordinateKind.REGION)
        >>> ic = IdentityCoordinate(42, "list", "mod.py:7", c, False)
        >>> ic.to_key()
        'id:42:list'
        """
        return f"id:{self.object_id}:{self.type_name}"

    def serialize(self) -> dict[str, Any]:
        """Serialise this identity coordinate to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary suitable for ``json.dumps()``.

        Examples
        --------
        >>> from jugeo.geometry.site import Coordinate, CoordinateKind
        >>> c = Coordinate(components=("heap", "id", "7"), kind=CoordinateKind.REGION)
        >>> ic = IdentityCoordinate(7, "str", "f.py:1", c, True)
        >>> d = ic.serialize()
        >>> d["object_id"]
        7
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "object_id": self.object_id,
            "type_name": self.type_name,
            "creation_site": self.creation_site,
            "coordinate_components": list(self.coordinate.components),
            "coordinate_kind": self.coordinate.kind.value,
            "is_interned": self.is_interned,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "IdentityCoordinate":
        """Deserialise an :class:`IdentityCoordinate` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        IdentityCoordinate
            The reconstructed coordinate.

        Raises
        ------
        KeyError
            If a required field is absent from ``data``.
        ValueError
            If ``coordinate_kind`` is not a valid
            :class:`~jugeo.geometry.site.CoordinateKind`.

        Examples
        --------
        >>> from jugeo.geometry.site import Coordinate, CoordinateKind
        >>> c = Coordinate(components=("heap", "id", "7"), kind=CoordinateKind.REGION)
        >>> ic = IdentityCoordinate(7, "str", "f.py:1", c, True)
        >>> ic2 = IdentityCoordinate.parse(ic.serialize())
        >>> ic2.object_id
        7
        """
        from jugeo.geometry.site import Coordinate  # local import

        components = tuple(data["coordinate_components"])
        kind = CoordinateKind(data.get("coordinate_kind", CoordinateKind.REGION.value))
        coord = Coordinate(components=components, kind=kind)
        return cls(
            object_id=int(data["object_id"]),
            type_name=str(data.get("type_name", UNKNOWN_TYPE_NAME)),
            creation_site=str(data.get("creation_site", UNKNOWN_SITE)),
            coordinate=coord,
            is_interned=bool(data.get("is_interned", False)),
        )


# ---------------------------------------------------------------------------
# HeapObject
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeapObject:
    """Frozen snapshot of a single Python heap object.

    A :class:`HeapObject` is the *section* of the heap sheaf over the
    singleton coordinate ``{identity.object_id}``.  It records the object's
    type, a sample of its fields (at most :data:`MAX_FIELD_COUNT`), its
    approximate memory size, and whether it is mutable.

    Fields
    ------
    identity : IdentityCoordinate
        The identity coordinate for this object.
    kind : ObjectKind
        Structural category of the object.
    type_coordinate : CoordinateObject
        The coordinate of the object's type (class).
    fields : tuple[tuple[str, str], ...]
        Ordered sequence of ``(field_name, repr_value)`` pairs sampled from
        the object.
    size_bytes : int
        Approximate memory footprint in bytes (from ``sys.getsizeof``).
    is_mutable : bool
        Whether the object supports mutation.
    support : SupportRegion
        The support region for this section.

    Examples
    --------
    >>> x = [1, 2, 3]
    >>> ho = make_heap_object(x, "test.py:1")
    >>> ho.is_container()
    True
    >>> ho.kind
    <ObjectKind.CONTAINER: 'container'>
    """

    identity: IdentityCoordinate
    kind: ObjectKind
    type_coordinate: CoordinateObject
    fields: tuple[tuple[str, str], ...]
    size_bytes: int
    is_mutable: bool
    support: SupportRegion

    def has_field(self, name: str) -> bool:
        """Return whether the object has a field with the given name.

        Parameters
        ----------
        name : str
            The field name to test.

        Returns
        -------
        bool
            ``True`` iff a field with that name exists in :attr:`fields`.

        Examples
        --------
        >>> # Assuming HeapObject with fields (("x", "1"), ("y", "2")):
        >>> # ho.has_field("x") -> True
        >>> # ho.has_field("z") -> False
        """
        return any(k == name for k, _ in self.fields)

    def get_field(self, name: str) -> str | None:
        """Return the value repr for the given field name, or ``None``.

        Parameters
        ----------
        name : str
            The field name to look up.

        Returns
        -------
        str | None
            The repr string for the field, or ``None`` if not found.

        Examples
        --------
        >>> # ho.get_field("x") -> "1"
        >>> # ho.get_field("missing") -> None
        """
        for k, v in self.fields:
            if k == name:
                return v
        return None

    def field_names(self) -> tuple[str, ...]:
        """Return the ordered tuple of all field names.

        Returns
        -------
        tuple[str, ...]
            The field names in the order they appear in :attr:`fields`.

        Examples
        --------
        >>> # ho.field_names() -> ("x", "y")
        """
        return tuple(k for k, _ in self.fields)

    def is_container(self) -> bool:
        """Return whether this object is a container type.

        Returns
        -------
        bool
            ``True`` iff :attr:`kind` is :attr:`~ObjectKind.CONTAINER`.

        Examples
        --------
        >>> make_heap_object([]).is_container()
        True
        >>> make_heap_object(42).is_container()
        False
        """
        return self.kind == ObjectKind.CONTAINER

    def serialize(self) -> dict[str, Any]:
        """Serialise this heap object to a JSON-compatible dictionary.

        The :attr:`support` field is serialised minimally (coordinate
        components only) because full
        :class:`~jugeo.geometry.supports.SupportRegion` round-trip
        serialisation is handled by the geometry layer.

        Returns
        -------
        dict[str, Any]
            A plain dictionary suitable for ``json.dumps()``.
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "identity": self.identity.serialize(),
            "kind": self.kind.value,
            "type_coordinate_components": list(self.type_coordinate.components),
            "type_coordinate_kind": self.type_coordinate.kind.value,
            "fields": [list(pair) for pair in self.fields],
            "size_bytes": self.size_bytes,
            "is_mutable": self.is_mutable,
            "support_coordinate_components": list(self.support.coordinate.components),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "HeapObject":
        """Deserialise a :class:`HeapObject` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        HeapObject
            The reconstructed heap object.

        Raises
        ------
        KeyError
            If a required field is absent.
        """
        from jugeo.geometry.site import Coordinate  # local import

        identity = IdentityCoordinate.parse(data["identity"])
        kind = ObjectKind(data.get("kind", ObjectKind.CUSTOM.value))
        tc_comps = tuple(data.get("type_coordinate_components", ("heap", "type", "object")))
        tc_kind = CoordinateKind(
            data.get("type_coordinate_kind", CoordinateKind.REGION.value)
        )
        type_coord = Coordinate(components=tc_comps, kind=tc_kind)
        fields: tuple[tuple[str, str], ...] = tuple(
            (str(p[0]), str(p[1])) for p in data.get("fields", [])
        )
        sc_comps = tuple(
            data.get("support_coordinate_components", identity.coordinate.components)
        )
        support_coord = Coordinate(components=sc_comps, kind=CoordinateKind.REGION)
        support = SupportRegion(coordinate=support_coord)
        return cls(
            identity=identity,
            kind=kind,
            type_coordinate=type_coord,
            fields=fields,
            size_bytes=int(data.get("size_bytes", 0)),
            is_mutable=bool(data.get("is_mutable", True)),
            support=support,
        )


# ---------------------------------------------------------------------------
# AliasPartition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AliasPartition:
    """An equivalence class of heap references sharing one identity coordinate.

    In theory2.tex Ch17, two references ``x`` and ``y`` alias each other iff
    ``id(x) == id(y)``.  An :class:`AliasPartition` is the equivalence class
    ``[x] = {r : id(r) == id(x)}``.

    Fields
    ------
    partition_id : str
        A stable UUID-based identifier for this partition.
    members : frozenset[str]
        The set of reference keys belonging to this equivalence class.
    canonical_member : str
        The nominated representative of this class; must be in
        :attr:`members`.
    creation_site : str
        The source location where this partition was first created.
    evidence : tuple[str, ...]
        Human-readable evidence strings supporting the aliasing claim.

    Examples
    --------
    >>> p = AliasPartition(
    ...     partition_id="abc",
    ...     members=frozenset({"id:1:list", "id:1:list.alias"}),
    ...     canonical_member="id:1:list",
    ...     creation_site="analyzer.py:100",
    ...     evidence=("id(x)==id(y)",),
    ... )
    >>> p.size()
    2
    >>> p.contains("id:1:list")
    True
    >>> p.is_singleton()
    False
    """

    partition_id: str
    members: frozenset[str]
    canonical_member: str
    creation_site: str
    evidence: tuple[str, ...]

    def contains(self, member: str) -> bool:
        """Return whether ``member`` belongs to this partition.

        Parameters
        ----------
        member : str
            A reference key string.

        Returns
        -------
        bool
            ``True`` iff ``member in self.members``.

        Examples
        --------
        >>> p = AliasPartition("id1", frozenset({"a", "b"}), "a", "", ())
        >>> p.contains("a")
        True
        >>> p.contains("c")
        False
        """
        return member in self.members

    def size(self) -> int:
        """Return the number of members in this partition.

        Returns
        -------
        int
            ``len(self.members)``.

        Examples
        --------
        >>> p = AliasPartition("id1", frozenset({"a", "b", "c"}), "a", "", ())
        >>> p.size()
        3
        """
        return len(self.members)

    def is_singleton(self) -> bool:
        """Return whether this partition contains exactly one member.

        A singleton partition means no aliasing: the reference is unique.

        Returns
        -------
        bool
            ``True`` iff :meth:`size` equals 1.

        Examples
        --------
        >>> p = AliasPartition("id1", frozenset({"a"}), "a", "", ())
        >>> p.is_singleton()
        True
        """
        return len(self.members) == 1

    def merge(self, other: "AliasPartition") -> "AliasPartition":
        """Merge two alias partitions into one, keeping self's canonical member.

        Used when two previously separate alias classes are found to refer to
        the same object (e.g., after detecting a transitive aliasing path).
        The merged partition takes its ``canonical_member`` from ``self``.

        Parameters
        ----------
        other : AliasPartition
            The partition to merge into this one.

        Returns
        -------
        AliasPartition
            A new :class:`AliasPartition` containing all members from both
            partitions, with a fresh ``partition_id`` and the combined
            evidence.

        Examples
        --------
        >>> p1 = AliasPartition("id1", frozenset({"a", "b"}), "a", "s1", ("e1",))
        >>> p2 = AliasPartition("id2", frozenset({"c", "d"}), "c", "s2", ("e2",))
        >>> merged = p1.merge(p2)
        >>> merged.size()
        4
        >>> merged.canonical_member
        'a'
        """
        new_members = self.members | other.members
        new_evidence = self.evidence + other.evidence + (
            f"merged partitions {self.partition_id} and {other.partition_id}",
        )
        return AliasPartition(
            partition_id=_new_uuid(),
            members=new_members,
            canonical_member=self.canonical_member,
            creation_site=self.creation_site,
            evidence=new_evidence,
        )

    def serialize(self) -> dict[str, Any]:
        """Serialise this partition to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary.

        Examples
        --------
        >>> p = AliasPartition("id1", frozenset({"a"}), "a", "s", ())
        >>> p.serialize()["partition_id"]
        'id1'
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "partition_id": self.partition_id,
            "members": sorted(self.members),
            "canonical_member": self.canonical_member,
            "creation_site": self.creation_site,
            "evidence": list(self.evidence),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "AliasPartition":
        """Deserialise an :class:`AliasPartition` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        AliasPartition
            The reconstructed partition.

        Raises
        ------
        KeyError
            If required fields are absent.

        Examples
        --------
        >>> p = AliasPartition("id1", frozenset({"a"}), "a", "s", ("e",))
        >>> AliasPartition.parse(p.serialize()).partition_id
        'id1'
        """
        return cls(
            partition_id=str(data["partition_id"]),
            members=frozenset(data.get("members", [])),
            canonical_member=str(data.get("canonical_member", "")),
            creation_site=str(data.get("creation_site", UNKNOWN_SITE)),
            evidence=tuple(data.get("evidence", [])),
        )


# ---------------------------------------------------------------------------
# MutationEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MutationEvent:
    """An immutable record of a single field mutation on a heap object.

    Theory context (theory2.tex Ch17)
    ----------------------------------
    A *mutation* is a local section replacement: replacing the value of field
    ``f`` on object ``o`` changes the section ``s_o`` at coordinate
    ``{id(o)}``.  The *descent check* (sheaf condition) requires that every
    alias of ``o`` sees the new value — i.e., the mutation must be globally
    consistent across all members of the alias partition containing ``o``.

    Fields
    ------
    event_id : str
        UUID identifying this specific mutation event.
    target_identity : IdentityCoordinate
        The identity coordinate of the mutated object.
    field_name : str
        The name of the mutated field.  For sequence mutations, this is the
        stringified index, e.g. ``"[0]"``.
    old_value : str
        Repr of the field's value before the mutation.
    new_value : str
        Repr of the field's value after the mutation.
    mutation_site : CoordinateObject
        The coordinate of the code location where the mutation occurred.
    timestamp : float
        Unix timestamp when the event was recorded.
    aliases_affected : tuple[str, ...]
        Keys of alias partition members that are affected by this mutation.

    Examples
    --------
    >>> from jugeo.geometry.site import Coordinate, CoordinateKind
    >>> coord = Coordinate(components=("heap", "id", "1"), kind=CoordinateKind.REGION)
    >>> site_coord = Coordinate(components=("src", "mod", "10"), kind=CoordinateKind.REGION)
    >>> ic = IdentityCoordinate(1, "list", "mod.py:10", coord, False)
    >>> ev = MutationEvent(
    ...     event_id="evt1",
    ...     target_identity=ic,
    ...     field_name="[0]",
    ...     old_value="0",
    ...     new_value="42",
    ...     mutation_site=site_coord,
    ...     timestamp=0.0,
    ...     aliases_affected=("id:1:list.alias",),
    ... )
    >>> ev.is_container_mutation()
    True
    >>> ev.affects_aliases()
    True
    """

    event_id: str
    target_identity: IdentityCoordinate
    field_name: str
    old_value: str
    new_value: str
    mutation_site: CoordinateObject
    timestamp: float
    aliases_affected: tuple[str, ...]

    def is_field_mutation(self) -> bool:
        """Return whether this is an attribute (field) mutation.

        Returns
        -------
        bool
            ``True`` iff :attr:`field_name` is non-empty and does not start
            with ``"["``.

        Examples
        --------
        >>> # event with field_name="x" -> True
        >>> # event with field_name="" -> False
        >>> # event with field_name="[0]" -> False
        """
        return bool(self.field_name) and not self.field_name.startswith("[")

    def is_container_mutation(self) -> bool:
        """Return whether this is a container (index/key) mutation.

        Returns
        -------
        bool
            ``True`` iff :attr:`field_name` starts with ``"["``.

        Examples
        --------
        >>> # event with field_name="[0]" -> True
        >>> # event with field_name="x"   -> False
        """
        return self.field_name.startswith("[")

    def affects_aliases(self) -> bool:
        """Return whether this mutation affects any aliased references.

        Returns
        -------
        bool
            ``True`` iff :attr:`aliases_affected` is non-empty.

        Examples
        --------
        >>> # event with aliases_affected=("x",) -> True
        >>> # event with aliases_affected=()      -> False
        """
        return len(self.aliases_affected) > 0

    def serialize(self) -> dict[str, Any]:
        """Serialise this mutation event to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary.
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "event_id": self.event_id,
            "target_identity": self.target_identity.serialize(),
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "mutation_site_components": list(self.mutation_site.components),
            "mutation_site_kind": self.mutation_site.kind.value,
            "timestamp": self.timestamp,
            "aliases_affected": list(self.aliases_affected),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "MutationEvent":
        """Deserialise a :class:`MutationEvent` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        MutationEvent
            The reconstructed event.

        Raises
        ------
        KeyError
            If required fields are absent.
        """
        from jugeo.geometry.site import Coordinate  # local import

        identity = IdentityCoordinate.parse(data["target_identity"])
        ms_comps = tuple(data.get("mutation_site_components", ("heap", "unknown")))
        ms_kind = CoordinateKind(
            data.get("mutation_site_kind", CoordinateKind.REGION.value)
        )
        mutation_site = Coordinate(components=ms_comps, kind=ms_kind)
        return cls(
            event_id=str(data.get("event_id", _new_uuid())),
            target_identity=identity,
            field_name=str(data.get("field_name", "")),
            old_value=str(data.get("old_value", "")),
            new_value=str(data.get("new_value", "")),
            mutation_site=mutation_site,
            timestamp=float(data.get("timestamp", 0.0)),
            aliases_affected=tuple(data.get("aliases_affected", [])),
        )


# ---------------------------------------------------------------------------
# HeapSection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeapSection:
    """A section of the heap sheaf over one identity coordinate.

    In the language of theory2.tex Ch17, a *section* over coordinate ``U``
    is a choice of compatible local data for every open set in the cover of
    ``U``.  For a heap object, the coordinate is the singleton ``{id(o)}``,
    and a section maps field names to their current values.

    :class:`HeapSection` is versioned so that consumers can detect stale
    data: each mutation increments the :attr:`version` counter.

    Fields
    ------
    identity : IdentityCoordinate
        The identity coordinate this section lives over.
    support : SupportRegion
        The support region for this section.
    sections : tuple[tuple[str, str], ...]
        Ordered ``(field_name, value_repr)`` pairs.
    version : int
        Monotonically increasing mutation counter.

    Examples
    --------
    >>> from jugeo.geometry.site import Coordinate, CoordinateKind
    >>> from jugeo.geometry.supports import SupportRegion
    >>> coord = Coordinate(components=("heap", "id", "42"), kind=CoordinateKind.REGION)
    >>> ic = IdentityCoordinate(42, "dict", "mod.py:1", coord, False)
    >>> sec = HeapSection(
    ...     identity=ic,
    ...     support=SupportRegion(coordinate=coord),
    ...     sections=(("x", "1"), ("y", "2")),
    ...     version=0,
    ... )
    >>> sec.at_field("x")
    '1'
    >>> sec.at_field("z") is None
    True
    """

    identity: IdentityCoordinate
    support: SupportRegion
    sections: tuple[tuple[str, str], ...]
    version: int

    def at_field(self, field_name: str) -> str | None:
        """Return the section value for the named field, or ``None``.

        Parameters
        ----------
        field_name : str
            The field (attribute) name to look up.

        Returns
        -------
        str | None
            The repr string for the field, or ``None`` if not found.

        Examples
        --------
        >>> sec.at_field("x")
        '1'
        >>> sec.at_field("missing") is None
        True
        """
        for k, v in self.sections:
            if k == field_name:
                return v
        return None

    def restrict_to(self, fields: frozenset[str]) -> "HeapSection":
        """Return a new section restricted to the given field names.

        This implements the *restriction map* of the sheaf: given a sub-cover
        of the identity coordinate, we project the section onto it.

        Parameters
        ----------
        fields : frozenset[str]
            The field names to retain.

        Returns
        -------
        HeapSection
            A new :class:`HeapSection` containing only the specified fields,
            with the same :attr:`version` and :attr:`support`.

        Examples
        --------
        >>> restricted = sec.restrict_to(frozenset({"x"}))
        >>> restricted.at_field("y") is None
        True
        >>> restricted.at_field("x")
        '1'
        """
        restricted: tuple[tuple[str, str], ...] = tuple(
            pair for pair in self.sections if pair[0] in fields
        )
        return replace(self, sections=restricted)

    def serialize(self) -> dict[str, Any]:
        """Serialise this heap section to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary.
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "identity": self.identity.serialize(),
            "support_coordinate_components": list(self.support.coordinate.components),
            "sections": [list(pair) for pair in self.sections],
            "version": self.version,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "HeapSection":
        """Deserialise a :class:`HeapSection` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        HeapSection
            The reconstructed section.

        Raises
        ------
        KeyError
            If required fields are absent.
        """
        from jugeo.geometry.site import Coordinate  # local import

        identity = IdentityCoordinate.parse(data["identity"])
        sc_comps = tuple(
            data.get("support_coordinate_components", identity.coordinate.components)
        )
        support_coord = Coordinate(components=sc_comps, kind=CoordinateKind.REGION)
        support = SupportRegion(coordinate=support_coord)
        sections: tuple[tuple[str, str], ...] = tuple(
            (str(p[0]), str(p[1])) for p in data.get("sections", [])
        )
        return cls(
            identity=identity,
            support=support,
            sections=sections,
            version=int(data.get("version", 0)),
        )


# ---------------------------------------------------------------------------
# AliasEdge
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AliasEdge:
    """A directed edge in the alias graph between two reference keys.

    Alias edges are used to build the full alias graph for a heap snapshot.
    A *direct* edge connects two references that are known to alias (``is``
    test succeeds at a specific program point).  A *transitive* edge is
    inferred by transitivity of the alias relation.

    Fields
    ------
    source_key : str
        The reference key of the edge's source node.
    target_key : str
        The reference key of the edge's target node.
    alias_kind : str
        One of :data:`ALIAS_KIND_DIRECT`, :data:`ALIAS_KIND_TRANSITIVE`, or
        :data:`ALIAS_KIND_HEURISTIC`.
    confidence : float
        Confidence score in ``[0.0, 1.0]``.  Direct edges have confidence
        1.0; heuristic edges may be lower.
    evidence : tuple[str, ...]
        Human-readable evidence strings.

    Examples
    --------
    >>> e = AliasEdge("a", "b", ALIAS_KIND_DIRECT, 1.0, ("id(a)==id(b)",))
    >>> e.is_direct()
    True
    >>> e.reversed().source_key
    'b'
    """

    source_key: str
    target_key: str
    alias_kind: str
    confidence: float
    evidence: tuple[str, ...]

    def is_direct(self) -> bool:
        """Return whether this is a direct (non-inferred) alias edge.

        Returns
        -------
        bool
            ``True`` iff :attr:`alias_kind` is :data:`ALIAS_KIND_DIRECT`.

        Examples
        --------
        >>> AliasEdge("a", "b", ALIAS_KIND_DIRECT, 1.0, ()).is_direct()
        True
        >>> AliasEdge("a", "b", ALIAS_KIND_TRANSITIVE, 0.9, ()).is_direct()
        False
        """
        return self.alias_kind == ALIAS_KIND_DIRECT

    def reversed(self) -> "AliasEdge":
        """Return a new edge with source and target swapped.

        The alias relation is symmetric, so the reversed edge carries the
        same ``alias_kind``, ``confidence``, and ``evidence``.

        Returns
        -------
        AliasEdge
            A new :class:`AliasEdge` with ``source_key`` and ``target_key``
            exchanged.

        Examples
        --------
        >>> e = AliasEdge("x", "y", ALIAS_KIND_DIRECT, 1.0, ())
        >>> e.reversed().source_key
        'y'
        >>> e.reversed().target_key
        'x'
        """
        return replace(self, source_key=self.target_key, target_key=self.source_key)

    def serialize(self) -> dict[str, Any]:
        """Serialise this alias edge to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary.
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "source_key": self.source_key,
            "target_key": self.target_key,
            "alias_kind": self.alias_kind,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "AliasEdge":
        """Deserialise an :class:`AliasEdge` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        AliasEdge
            The reconstructed edge.

        Raises
        ------
        KeyError
            If required fields are absent.

        Examples
        --------
        >>> e = AliasEdge("a", "b", ALIAS_KIND_DIRECT, 1.0, ("ev",))
        >>> AliasEdge.parse(e.serialize()).source_key
        'a'
        """
        confidence = float(data.get("confidence", 1.0))
        confidence = max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, confidence))
        return cls(
            source_key=str(data["source_key"]),
            target_key=str(data["target_key"]),
            alias_kind=str(data.get("alias_kind", ALIAS_KIND_DIRECT)),
            confidence=confidence,
            evidence=tuple(data.get("evidence", [])),
        )


# ---------------------------------------------------------------------------
# HeapSnapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeapSnapshot:
    """A point-in-time snapshot of the full heap aliasing state.

    A :class:`HeapSnapshot` collects all live :class:`HeapObject` records,
    :class:`AliasPartition` records, and :class:`HeapSection` records into
    a single immutable value.  Snapshots can be diffed, archived, and
    queried efficiently.

    Fields
    ------
    snapshot_id : str
        UUID identifier for this snapshot.
    timestamp : float
        Unix timestamp when the snapshot was taken.
    objects : tuple[HeapObject, ...]
        All heap objects present at snapshot time.
    partitions : tuple[AliasPartition, ...]
        All alias partitions computed at snapshot time.
    sections : tuple[HeapSection, ...]
        All heap sections at snapshot time.
    metadata : dict[str, Any]
        Arbitrary metadata (e.g., GC generation counts).

    Examples
    --------
    >>> snap = HeapSnapshot(
    ...     snapshot_id="s1",
    ...     timestamp=0.0,
    ...     objects=(),
    ...     partitions=(),
    ...     sections=(),
    ...     metadata={"gc_generation": 0},
    ... )
    >>> snap.find_object(999) is None
    True
    >>> snap.find_partition("id:1:list") is None
    True
    """

    snapshot_id: str
    timestamp: float
    objects: tuple[HeapObject, ...]
    partitions: tuple[AliasPartition, ...]
    sections: tuple[HeapSection, ...]
    metadata: dict[str, Any]

    def find_object(self, object_id: int) -> "HeapObject | None":
        """Return the :class:`HeapObject` with the given object id, or ``None``.

        Parameters
        ----------
        object_id : int
            The CPython object id to search for.

        Returns
        -------
        HeapObject | None
            The first matching object, or ``None`` if not found.

        Examples
        --------
        >>> snap.find_object(99999) is None
        True
        """
        for obj in self.objects:
            if obj.identity.object_id == object_id:
                return obj
        return None

    def find_partition(self, member: str) -> "AliasPartition | None":
        """Return the alias partition containing ``member``, or ``None``.

        Parameters
        ----------
        member : str
            A reference key string (from :meth:`IdentityCoordinate.to_key`).

        Returns
        -------
        AliasPartition | None
            The first partition whose :attr:`~AliasPartition.members` set
            contains ``member``, or ``None`` if no such partition exists.

        Examples
        --------
        >>> snap.find_partition("id:1:list") is None
        True
        """
        for part in self.partitions:
            if part.contains(member):
                return part
        return None

    def serialize(self) -> dict[str, Any]:
        """Serialise this snapshot to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary.
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "objects": [o.serialize() for o in self.objects],
            "partitions": [p.serialize() for p in self.partitions],
            "sections": [s.serialize() for s in self.sections],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "HeapSnapshot":
        """Deserialise a :class:`HeapSnapshot` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        HeapSnapshot
            The reconstructed snapshot.

        Raises
        ------
        KeyError
            If required fields are absent.
        """
        objects = tuple(HeapObject.parse(o) for o in data.get("objects", []))
        partitions = tuple(AliasPartition.parse(p) for p in data.get("partitions", []))
        sections = tuple(HeapSection.parse(s) for s in data.get("sections", []))
        return cls(
            snapshot_id=str(data.get("snapshot_id", _new_uuid())),
            timestamp=float(data.get("timestamp", 0.0)),
            objects=objects,
            partitions=partitions,
            sections=sections,
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# MutationPatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MutationPatch:
    """A proposed atomic set of field updates for one heap object.

    A :class:`MutationPatch` groups one or more field writes into a single
    logical unit.  Before applying, the *descent check* (sheaf condition,
    theory2.tex Ch17 sect 4) must verify that the patch is globally
    consistent with all alias observers.

    Fields
    ------
    patch_id : str
        UUID identifier for this patch.
    target_identity : IdentityCoordinate
        The identity coordinate of the object being patched.
    field_updates : tuple[tuple[str, str], ...]
        Ordered sequence of ``(field_name, new_value_repr)`` pairs.
    timestamp : float
        Unix timestamp when the patch was created.
    is_validated : bool
        Whether the descent check has been performed and passed.

    Examples
    --------
    >>> from jugeo.geometry.site import Coordinate, CoordinateKind
    >>> from jugeo.geometry.supports import SupportRegion
    >>> coord = Coordinate(components=("heap", "id", "5"), kind=CoordinateKind.REGION)
    >>> ic = IdentityCoordinate(5, "dict", "patch.py:1", coord, False)
    >>> patch = MutationPatch(
    ...     patch_id="p1",
    ...     target_identity=ic,
    ...     field_updates=(("x", "42"),),
    ...     timestamp=0.0,
    ...     is_validated=False,
    ... )
    >>> sec = HeapSection(
    ...     identity=ic,
    ...     support=SupportRegion(coordinate=coord),
    ...     sections=(("x", "0"), ("y", "1")),
    ...     version=0,
    ... )
    >>> new_sec = patch.apply_to(sec)
    >>> new_sec.at_field("x")
    '42'
    >>> new_sec.version
    1
    """

    patch_id: str
    target_identity: IdentityCoordinate
    field_updates: tuple[tuple[str, str], ...]
    timestamp: float
    is_validated: bool

    def apply_to(self, section: HeapSection) -> HeapSection:
        """Apply this patch to a :class:`HeapSection`, returning a new section.

        The application merges the patch's :attr:`field_updates` into the
        section's :attr:`~HeapSection.sections` mapping.  Existing fields are
        updated; new fields are appended.  The :attr:`~HeapSection.version`
        counter is incremented by 1.

        Parameters
        ----------
        section : HeapSection
            The section to apply this patch to.

        Returns
        -------
        HeapSection
            A new :class:`HeapSection` with the patch applied and version
            incremented.

        Raises
        ------
        ValueError
            If the patch's ``target_identity`` does not match the section's
            ``identity`` (i.e., mismatched object ids).

        Examples
        --------
        >>> new_sec = patch.apply_to(sec)
        >>> new_sec.version == sec.version + 1
        True
        >>> new_sec.at_field("x")
        '42'
        """
        if not self.target_identity.matches(section.identity):
            raise ValueError(
                f"Patch target {self.target_identity.object_id} does not match"
                f" section identity {section.identity.object_id}."
            )
        merged: dict[str, str] = dict(section.sections)
        for field_name, new_value in self.field_updates:
            merged[field_name] = new_value
        new_sections: tuple[tuple[str, str], ...] = tuple(merged.items())
        return replace(section, sections=new_sections, version=section.version + 1)

    def conflicts_with(self, other: "MutationPatch") -> bool:
        """Return whether this patch conflicts with another patch.

        Two patches *conflict* if they target the same object (identity) and
        both update at least one field in common.

        Parameters
        ----------
        other : MutationPatch
            The other patch to test against.

        Returns
        -------
        bool
            ``True`` iff both patches touch the same object and share at
            least one field name.

        Examples
        --------
        >>> p1.conflicts_with(p2)  # same field "x"
        True
        >>> p1.conflicts_with(p3)  # different field "y"
        False
        """
        if not self.target_identity.matches(other.target_identity):
            return False
        my_fields = frozenset(name for name, _ in self.field_updates)
        other_fields = frozenset(name for name, _ in other.field_updates)
        return bool(my_fields & other_fields)

    def serialize(self) -> dict[str, Any]:
        """Serialise this mutation patch to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary.
        """
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "patch_id": self.patch_id,
            "target_identity": self.target_identity.serialize(),
            "field_updates": [list(pair) for pair in self.field_updates],
            "timestamp": self.timestamp,
            "is_validated": self.is_validated,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "MutationPatch":
        """Deserialise a :class:`MutationPatch` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        MutationPatch
            The reconstructed patch.

        Raises
        ------
        KeyError
            If required fields are absent.

        Examples
        --------
        >>> patch2 = MutationPatch.parse(patch.serialize())
        >>> patch2.patch_id == patch.patch_id
        True
        """
        identity = IdentityCoordinate.parse(data["target_identity"])
        updates: tuple[tuple[str, str], ...] = tuple(
            (str(p[0]), str(p[1])) for p in data.get("field_updates", [])
        )
        return cls(
            patch_id=str(data.get("patch_id", _new_uuid())),
            target_identity=identity,
            field_updates=updates,
            timestamp=float(data.get("timestamp", 0.0)),
            is_validated=bool(data.get("is_validated", False)),
        )


# ---------------------------------------------------------------------------
# Public factory helpers
# ---------------------------------------------------------------------------


def make_identity_coordinate(
    obj: Any,
    creation_site: str = UNKNOWN_SITE,
    *,
    is_interned: bool = False,
) -> IdentityCoordinate:
    """Construct an :class:`IdentityCoordinate` for a live Python object.

    This is the primary factory for creating identity coordinates during
    heap analysis.  It calls ``id(obj)`` to obtain the address, infers the
    type name, and builds the underlying
    :class:`~jugeo.geometry.site.Coordinate`.

    Parameters
    ----------
    obj : Any
        Any live Python object.
    creation_site : str, optional
        Human-readable source location.  Defaults to :data:`UNKNOWN_SITE`.
    is_interned : bool, optional
        Set to ``True`` for known interned objects (small ints, interned
        strings).  Defaults to ``False``.

    Returns
    -------
    IdentityCoordinate
        A fully populated identity coordinate.

    Examples
    --------
    >>> x = [1, 2, 3]
    >>> ic = make_identity_coordinate(x, "example.py:1")
    >>> ic.type_name
    'list'
    >>> ic.object_id == id(x)
    True
    """
    object_id = id(obj)
    type_name = type(obj).__name__
    coord = _make_heap_coordinate(object_id)
    return IdentityCoordinate(
        object_id=object_id,
        type_name=type_name,
        creation_site=creation_site,
        coordinate=coord,
        is_interned=is_interned,
    )


def make_heap_object(
    obj: Any,
    creation_site: str = UNKNOWN_SITE,
    max_fields: int = MAX_FIELD_COUNT,
) -> HeapObject:
    """Construct a :class:`HeapObject` snapshot for a live Python object.

    This factory samples the object's fields (via ``vars()`` or iteration),
    computes the memory footprint via ``sys.getsizeof()``, and packages
    everything into an immutable :class:`HeapObject`.

    Parameters
    ----------
    obj : Any
        The object to snapshot.
    creation_site : str, optional
        Human-readable source location.  Defaults to :data:`UNKNOWN_SITE`.
    max_fields : int, optional
        Maximum number of fields to record.  Defaults to
        :data:`MAX_FIELD_COUNT`.

    Returns
    -------
    HeapObject
        A frozen snapshot of ``obj``.

    Examples
    --------
    >>> x = [1, 2, 3]
    >>> ho = make_heap_object(x)
    >>> ho.is_container()
    True
    >>> ho.kind
    <ObjectKind.CONTAINER: 'container'>
    """
    from jugeo.geometry.site import Coordinate  # local import

    identity = make_identity_coordinate(obj, creation_site)
    kind = ObjectKind.from_type_name(type(obj).__name__)

    type_name = type(obj).__name__
    type_coord = Coordinate(
        components=(HEAP_COORD_COMPONENT, "type", type_name),
        kind=CoordinateKind.REGION,
    )

    raw_fields: list[tuple[str, str]] = []
    try:
        obj_dict = vars(obj)
        for k, v in list(obj_dict.items())[:max_fields]:
            raw_fields.append((str(k), _safe_repr(v)))
    except TypeError:
        if hasattr(obj, "__dict__"):
            for k, v in list(obj.__dict__.items())[:max_fields]:
                raw_fields.append((str(k), _safe_repr(v)))
        elif hasattr(obj, "__iter__"):
            for i, v in enumerate(obj):
                if i >= max_fields:
                    break
                raw_fields.append((f"[{i}]", _safe_repr(v)))

    try:
        size_bytes = sys.getsizeof(obj)
    except Exception:  # noqa: BLE001
        size_bytes = 0

    is_mutable = kind.is_mutable_kind()
    support = _make_support_region(identity.object_id)

    return HeapObject(
        identity=identity,
        kind=kind,
        type_coordinate=type_coord,
        fields=tuple(raw_fields),
        size_bytes=size_bytes,
        is_mutable=is_mutable,
        support=support,
    )


def make_alias_partition_for(
    keys: list[str],
    canonical: str,
    creation_site: str = UNKNOWN_SITE,
    evidence: tuple[str, ...] = (),
) -> AliasPartition:
    """Convenience factory for creating an :class:`AliasPartition`.

    Parameters
    ----------
    keys : list[str]
        All reference keys in the partition.
    canonical : str
        The canonical (representative) key; must be in ``keys``.
    creation_site : str, optional
        Source location string.  Defaults to :data:`UNKNOWN_SITE`.
    evidence : tuple[str, ...], optional
        Evidence strings.  Defaults to an empty tuple.

    Returns
    -------
    AliasPartition
        A new partition with a fresh UUID.

    Raises
    ------
    ValueError
        If ``canonical`` is not in ``keys``.

    Examples
    --------
    >>> p = make_alias_partition_for(["a", "b"], "a")
    >>> p.canonical_member
    'a'
    >>> p.size()
    2
    """
    if canonical not in keys:
        raise ValueError(f"canonical {canonical!r} must be in keys {keys!r}.")
    return AliasPartition(
        partition_id=_new_uuid(),
        members=frozenset(keys),
        canonical_member=canonical,
        creation_site=creation_site,
        evidence=evidence,
    )


def make_empty_snapshot(metadata: dict[str, Any] | None = None) -> HeapSnapshot:
    """Construct an empty :class:`HeapSnapshot` with a fresh id and timestamp.

    Parameters
    ----------
    metadata : dict[str, Any] | None, optional
        Metadata to attach.  Defaults to an empty dict.

    Returns
    -------
    HeapSnapshot
        An empty snapshot suitable as a baseline for incremental updates.

    Examples
    --------
    >>> snap = make_empty_snapshot()
    >>> len(snap.objects)
    0
    >>> len(snap.partitions)
    0
    """
    return HeapSnapshot(
        snapshot_id=_new_uuid(),
        timestamp=time.time(),
        objects=(),
        partitions=(),
        sections=(),
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "HEAP_COORD_COMPONENT",
    "ID_COORD_COMPONENT",
    "UNKNOWN_TYPE_NAME",
    "UNKNOWN_SITE",
    "MIN_CONFIDENCE",
    "MAX_CONFIDENCE",
    "ALIAS_KIND_DIRECT",
    "ALIAS_KIND_TRANSITIVE",
    "ALIAS_KIND_HEURISTIC",
    "MAX_FIELD_COUNT",
    "UNSERIALIZABLE_FIELD_VALUE",
    "MODEL_SCHEMA_VERSION",
    # Enums
    "ObjectKind",
    # Dataclasses
    "IdentityCoordinate",
    "HeapObject",
    "AliasPartition",
    "MutationEvent",
    "HeapSection",
    "AliasEdge",
    "HeapSnapshot",
    "MutationPatch",
    # Factories
    "make_identity_coordinate",
    "make_heap_object",
    "make_alias_partition_for",
    "make_empty_snapshot",
]
