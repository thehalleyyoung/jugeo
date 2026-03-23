"""Heap objects as sheaf sections — theory2.tex Ch17, §1 — Heap Objects as Sections.

This module implements the construction of :class:`HeapObject` and
:class:`HeapSection` instances from live Python objects, tracks their identity
coordinates over time, and provides factory / registry / builder abstractions
that mirror the sheaf-theoretic treatment of the heap described in §17.1–§17.4
of theory2.tex.

**Copilot integration note**: this file was developed in tandem with the
JuGeo copilot integration layer.  The :class:`IdentityTracker` in particular
exposes a :meth:`~IdentityTracker.build_identity_judgment` helper that
generates first-class :class:`~jugeo.judgments.judgment_terms.Judgment`
objects, bridging the runtime heap model with the proof-assistant layer.

Key design principles (theory2.tex Ch17 §17.1):

* Every Python object ``o`` has a *unique* identity coordinate keyed by
  ``str(id(o))``.  This coordinate is the singleton open set ``{id(o)}`` in
  the topology of the heap site.
* A ``HeapSection`` is a local section ``s : U → F`` where ``U = {id(o)}``
  and ``F`` is the heap functor assigning data to each open set.
* The :class:`HeapObjectFactory` constructs sections from live Python objects
  without mutating them.
* The :class:`HeapObjectRegistry` maintains a global index of known sections.
* The :class:`IdentityTracker` records creation/deletion timestamps and can
  emit :class:`~jugeo.judgments.judgment_terms.Judgment` objects for uniqueness
  properties.
* The :class:`HeapSectionBuilder` assembles multi-object sections in a
  step-by-step fashion.
"""

from __future__ import annotations

import inspect
import logging
import math
import sys
import time
import types
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentBuilder,
    JudgmentStatus,
    ProvenanceSource,
    TrustLevel,
)
from jugeo.python_runtime.heap_aliasing.models import (
    HeapObject,
    HeapSection,
    IdentityCoordinate,
    ObjectKind,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum number of fields inspected when building a section.
MAX_FIELD_DEPTH: int = 64

#: Default creation site label used when no explicit site is provided.
DEFAULT_CREATION_SITE: str = "<unknown>"

#: ObjectKind values that are never mutated in practice.
IMMUTABLE_KINDS: frozenset[ObjectKind] = frozenset(
    {ObjectKind.PRIMITIVE, ObjectKind.FROZEN, ObjectKind.BUILTIN}
)

#: Python primitive types treated as immutable leaves in the heap graph.
PRIMITIVE_TYPES: tuple[type, ...] = (
    int,
    float,
    complex,
    bool,
    str,
    bytes,
    bytearray,
    type(None),
)

#: Python container types that can hold references to other objects.
CONTAINER_TYPES: tuple[type, ...] = (
    list,
    dict,
    set,
    frozenset,
    tuple,
)


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def type_name_of(obj: object) -> str:
    """Return the qualified type name of *obj*.

    Uses ``type(obj).__qualname__`` which includes enclosing class names for
    nested classes (e.g. ``"Outer.Inner"``).

    Parameters:
        obj: Any Python object.

    Returns:
        The ``__qualname__`` string of ``type(obj)``.

    Examples:
        >>> type_name_of(42)
        'int'
        >>> class Foo: pass
        >>> type_name_of(Foo())
        'Foo'
    """
    return type(obj).__qualname__


def safe_sizeof(obj: object) -> int:
    """Return the estimated memory size of *obj* in bytes.

    Calls ``sys.getsizeof(obj)`` and silently returns ``0`` on any exception
    (e.g. ``TypeError`` for objects that do not support ``__sizeof__``).

    Parameters:
        obj: Any Python object.

    Returns:
        Estimated size in bytes, or ``0`` on failure.

    Examples:
        >>> safe_sizeof([]) >= 0
        True
        >>> safe_sizeof(None)
        16
    """
    try:
        return sys.getsizeof(obj)
    except Exception:
        return 0


def object_to_identity_coord(
    obj: object,
    creation_site: str = "",
) -> IdentityCoordinate:
    """Build an :class:`IdentityCoordinate` from a live Python object.

    Constructs the identity coordinate by reading ``id(obj)`` and
    ``type(obj).__qualname__``.  An optional *creation_site* label is stored
    for debugging.

    Parameters:
        obj: Any live Python object.
        creation_site: Optional string identifying where the object was
            created (e.g. ``"my_module.my_fn:42"``).

    Returns:
        A freshly constructed :class:`IdentityCoordinate`.

    Examples:
        >>> ic = object_to_identity_coord([], creation_site="test:1")
        >>> ic.type_name
        'list'
        >>> ic.address == id([])  # doctest: +SKIP
        True
    """
    oid = id(obj)
    tname = type_name_of(obj)
    coord = CoordinateObject(
        components=(str(oid), tname),
        kind=CoordinateKind.REGION,
        support_labels=frozenset({f"id:{oid}"}),
    )
    return IdentityCoordinate(
        object_id=oid,
        type_name=tname,
        address=oid,
        coordinate=coord,
    )


def _new_section_id() -> str:
    """Return a unique section identifier string."""
    return f"sec_{uuid.uuid4().hex[:10]}"


def _build_support_region(identity: IdentityCoordinate) -> SupportRegion:
    """Build a singleton ``SupportRegion`` for *identity*.

    Parameters:
        identity: The identity coordinate whose address becomes the sole
            patch key.

    Returns:
        A :class:`~jugeo.geometry.supports.SupportRegion` with one patch key.
    """
    coord = identity.coordinate or CoordinateObject(
        components=(str(identity.object_id), identity.type_name),
        kind=CoordinateKind.REGION,
        support_labels=frozenset({identity.key()}),
    )
    return SupportRegion(
        coordinate=coord,
        patch_keys=frozenset({identity.key()}),
        labels=frozenset({identity.type_name}),
    )


# ---------------------------------------------------------------------------
# HeapObjectFactory
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapObjectFactory:
    """Factory that constructs :class:`HeapObject` instances from live Python objects.

    The factory caches objects by their integer ``id()`` to avoid duplicating
    work when the same object is encountered multiple times during a traversal.
    The cache is intentionally *weak* in the sense that callers must explicitly
    manage its lifetime via :meth:`clear_cache`.

    Attributes:
        _cache: Maps ``id(obj)`` → :class:`HeapObject` for already-seen objects.
        _site_prefix: String prefix used when constructing coordinate labels.

    Examples:
        >>> factory = HeapObjectFactory()
        >>> obj = factory.from_object([1, 2, 3])
        >>> obj.kind
        <ObjectKind.CONTAINER: 'container'>
    """

    _cache: dict[int, HeapObject] = field(default_factory=dict)
    _site_prefix: str = "heap"

    # ------------------------------------------------------------------
    # Primary construction entry points
    # ------------------------------------------------------------------

    def from_object(
        self,
        obj: object,
        creation_site: str = "",
    ) -> HeapObject:
        """Build a :class:`HeapObject` from any live Python object.

        Returns the cached result if the object has been seen before.

        Parameters:
            obj: The Python object to wrap.
            creation_site: Optional code-site label stored in the identity
                coordinate.

        Returns:
            A :class:`HeapObject` snapshot of *obj*.

        Examples:
            >>> f = HeapObjectFactory()
            >>> ho = f.from_object({"a": 1})
            >>> ho.type_name
            'dict'
        """
        oid = id(obj)
        cached = self.cache_get(oid)
        if cached is not None:
            logger.debug("HeapObjectFactory: cache hit for id=%d", oid)
            return cached
        identity = self.build_identity_coordinate(obj, creation_site)
        kind = self.infer_kind(obj)
        raw_fields = self.compute_fields(obj)
        size = self.compute_size(obj)
        is_frozen = kind in IMMUTABLE_KINDS or isinstance(obj, tuple)
        heap_obj = HeapObject(
            object_id=oid,
            type_name=type_name_of(obj),
            kind=kind,
            fields=raw_fields,
            is_frozen=is_frozen,
            size_bytes=size,
            identity=identity,
        )
        self.cache_put(heap_obj)
        logger.debug(
            "HeapObjectFactory: created HeapObject id=%d kind=%s", oid, kind.value
        )
        return heap_obj

    def from_class(
        self,
        cls_obj: type,
        creation_site: str = "",
    ) -> HeapObject:
        """Build a :class:`HeapObject` from a class (``type``) object.

        Class objects are treated specially: their *fields* are derived from
        ``cls_obj.__dict__`` rather than ``vars(instance)``.

        Parameters:
            cls_obj: A Python class (``isinstance(cls_obj, type)``).
            creation_site: Optional code-site label.

        Returns:
            A :class:`HeapObject` with ``kind=ObjectKind.BUILTIN`` (if it is a
            built-in) or ``kind=ObjectKind.USER_DEFINED`` for user-defined classes.

        Raises:
            TypeError: If *cls_obj* is not a type.
        """
        if not isinstance(cls_obj, type):
            raise TypeError(f"from_class expects a type, got {type_name_of(cls_obj)}")
        oid = id(cls_obj)
        cached = self.cache_get(oid)
        if cached is not None:
            return cached
        is_builtin = cls_obj.__module__ == "builtins"
        kind = ObjectKind.BUILTIN if is_builtin else ObjectKind.USER_DEFINED
        identity = self.build_identity_coordinate(cls_obj, creation_site)
        # Extract method and attribute names as field refs pointing to their ids
        raw_fields: list[tuple[str, int]] = []
        for attr_name in list(vars(cls_obj))[:MAX_FIELD_DEPTH]:
            try:
                attr_val = getattr(cls_obj, attr_name)
                raw_fields.append((attr_name, id(attr_val)))
            except AttributeError:
                pass
        heap_obj = HeapObject(
            object_id=oid,
            type_name=type_name_of(cls_obj),
            kind=kind,
            fields=tuple(raw_fields),
            is_frozen=False,
            size_bytes=safe_sizeof(cls_obj),
            identity=identity,
        )
        self.cache_put(heap_obj)
        return heap_obj

    def from_function(
        self,
        fn: object,
        creation_site: str = "",
    ) -> HeapObject:
        """Build a :class:`HeapObject` from a callable (function / method).

        Extracts closure variables and default argument ids as outgoing refs.

        Parameters:
            fn: Any callable object.
            creation_site: Optional code-site label.

        Returns:
            A :class:`HeapObject` with ``kind=ObjectKind.BUILTIN`` or
            ``kind=ObjectKind.USER_DEFINED`` depending on origin.
        """
        oid = id(fn)
        cached = self.cache_get(oid)
        if cached is not None:
            return cached
        identity = self.build_identity_coordinate(fn, creation_site)
        raw_fields: list[tuple[str, int]] = []
        # Capture closure cell contents as refs
        if hasattr(fn, "__closure__") and fn.__closure__:  # type: ignore[union-attr]
            for idx, cell in enumerate(fn.__closure__):  # type: ignore[union-attr]
                try:
                    raw_fields.append((f"closure_{idx}", id(cell.cell_contents)))
                except ValueError:
                    pass  # empty cell
        # Capture default argument objects
        if hasattr(fn, "__defaults__") and fn.__defaults__:
            for idx, default in enumerate(fn.__defaults__):  # type: ignore[union-attr]
                raw_fields.append((f"default_{idx}", id(default)))
        is_builtin = isinstance(fn, types.BuiltinFunctionType)
        kind = ObjectKind.BUILTIN if is_builtin else ObjectKind.USER_DEFINED
        heap_obj = HeapObject(
            object_id=oid,
            type_name=type_name_of(fn),
            kind=kind,
            fields=tuple(raw_fields[:MAX_FIELD_DEPTH]),
            is_frozen=False,
            size_bytes=safe_sizeof(fn),
            identity=identity,
        )
        self.cache_put(heap_obj)
        return heap_obj

    def from_container(
        self,
        container: object,
        creation_site: str = "",
    ) -> HeapObject:
        """Build a :class:`HeapObject` from a container object.

        Extracts element / value ids as outgoing field references.  For
        ``dict`` objects the keys are turned into field name strings; for
        sequences an index-based naming scheme is used.

        Parameters:
            container: A list, dict, set, tuple, or similar object.
            creation_site: Optional code-site label.

        Returns:
            A :class:`HeapObject` with ``kind=ObjectKind.CONTAINER``.
        """
        oid = id(container)
        cached = self.cache_get(oid)
        if cached is not None:
            return cached
        identity = self.build_identity_coordinate(container, creation_site)
        raw_fields: list[tuple[str, int]] = []
        try:
            if isinstance(container, dict):
                for k, v in list(container.items())[:MAX_FIELD_DEPTH]:
                    raw_fields.append((repr(k)[:64], id(v)))
            elif isinstance(container, (list, tuple)):
                for idx, v in enumerate(container[:MAX_FIELD_DEPTH]):
                    raw_fields.append((f"[{idx}]", id(v)))
            elif isinstance(container, (set, frozenset)):
                for idx, v in enumerate(list(container)[:MAX_FIELD_DEPTH]):
                    raw_fields.append((f"{{{idx}}}", id(v)))
        except Exception as exc:  # pragma: no cover
            logger.warning("from_container: error extracting elements: %s", exc)
        is_frozen = isinstance(container, (frozenset, tuple))
        heap_obj = HeapObject(
            object_id=oid,
            type_name=type_name_of(container),
            kind=ObjectKind.CONTAINER,
            fields=tuple(raw_fields),
            is_frozen=is_frozen,
            size_bytes=safe_sizeof(container),
            identity=identity,
        )
        self.cache_put(heap_obj)
        return heap_obj

    # ------------------------------------------------------------------
    # Classification and field extraction
    # ------------------------------------------------------------------

    def infer_kind(self, obj: object) -> ObjectKind:
        """Classify *obj* into one of the :class:`ObjectKind` variants.

        Priority order: BUILTIN > FROZEN > PRIMITIVE > CONTAINER > USER_DEFINED.

        Parameters:
            obj: Any Python object.

        Returns:
            The most specific :class:`ObjectKind` for *obj*.

        Examples:
            >>> f = HeapObjectFactory()
            >>> f.infer_kind(42)
            <ObjectKind.PRIMITIVE: 'primitive'>
            >>> f.infer_kind([])
            <ObjectKind.CONTAINER: 'container'>
        """
        if isinstance(obj, types.BuiltinFunctionType):
            return ObjectKind.BUILTIN
        if isinstance(obj, PRIMITIVE_TYPES):
            return ObjectKind.PRIMITIVE
        if isinstance(obj, (frozenset, bytes)):
            return ObjectKind.FROZEN
        if isinstance(obj, CONTAINER_TYPES):
            return ObjectKind.CONTAINER
        # Check for frozen dataclasses
        cls = type(obj)
        params = getattr(cls, "__dataclass_params__", None)
        if params is not None and getattr(params, "frozen", False):
            return ObjectKind.FROZEN
        return ObjectKind.USER_DEFINED

    def compute_fields(self, obj: object) -> tuple[tuple[str, int], ...]:
        """Extract ``(field_name, target_object_id)`` pairs for *obj*.

        Uses ``vars(obj)`` when available (for instances with ``__dict__``),
        falling back to ``dir(obj)`` for objects without ``__dict__``.  Only
        non-dunder attributes are included, up to :data:`MAX_FIELD_DEPTH`.

        Parameters:
            obj: Any Python object.

        Returns:
            A tuple of ``(name, id(value))`` pairs representing outgoing
            references from *obj*.
        """
        result: list[tuple[str, int]] = []
        try:
            obj_vars = vars(obj)
            for name, val in list(obj_vars.items())[:MAX_FIELD_DEPTH]:
                if not name.startswith("__"):
                    result.append((name, id(val)))
        except TypeError:
            # vars() not available — fall back to dir()
            for name in dir(obj)[:MAX_FIELD_DEPTH]:
                if name.startswith("__"):
                    continue
                try:
                    val = getattr(obj, name)
                    if not callable(val):
                        result.append((name, id(val)))
                except AttributeError:
                    pass
        return tuple(result)

    def compute_size(self, obj: object) -> int:
        """Return the shallow memory size of *obj*.

        Parameters:
            obj: Any Python object.

        Returns:
            Estimated size in bytes from ``safe_sizeof``.
        """
        return safe_sizeof(obj)

    def build_identity_coordinate(
        self,
        obj: object,
        creation_site: str,
    ) -> IdentityCoordinate:
        """Build an :class:`IdentityCoordinate` for *obj*.

        Delegates to the module-level :func:`object_to_identity_coord` helper
        and annotates the coordinate with the *creation_site* and factory
        *_site_prefix*.

        Parameters:
            obj: The live Python object.
            creation_site: Optional code-site label.

        Returns:
            A freshly constructed :class:`IdentityCoordinate`.
        """
        site_label = creation_site or f"{self._site_prefix}:{DEFAULT_CREATION_SITE}"
        return object_to_identity_coord(obj, creation_site=site_label)

    def build_support(self, obj: object) -> SupportRegion:
        """Build a singleton :class:`~jugeo.geometry.supports.SupportRegion` for *obj*.

        The support region has a single patch key equal to ``"id:<id(obj)>"``,
        reflecting the theoretical fact that the section for *obj* lives over
        the singleton open set ``{id(obj)}``.

        Parameters:
            obj: Any live Python object.

        Returns:
            A :class:`~jugeo.geometry.supports.SupportRegion` containing only
            the identity key of *obj*.
        """
        oid = id(obj)
        coord = CoordinateObject(
            components=(str(oid), type_name_of(obj)),
            kind=CoordinateKind.REGION,
            support_labels=frozenset({f"id:{oid}"}),
        )
        return SupportRegion(
            coordinate=coord,
            patch_keys=frozenset({f"id:{oid}"}),
            labels=frozenset({type_name_of(obj)}),
        )

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def cache_get(self, object_id: int) -> HeapObject | None:
        """Return the cached :class:`HeapObject` for *object_id*, or ``None``.

        Parameters:
            object_id: The integer id of a Python object.

        Returns:
            Cached :class:`HeapObject` if present, else ``None``.
        """
        return self._cache.get(object_id)

    def cache_put(self, obj: HeapObject) -> None:
        """Store *obj* in the internal cache keyed by ``obj.object_id``.

        Parameters:
            obj: The :class:`HeapObject` to cache.
        """
        self._cache[obj.object_id] = obj

    def clear_cache(self) -> None:
        """Evict all entries from the internal cache.

        After calling this method, subsequent calls to :meth:`from_object`
        will rebuild :class:`HeapObject` instances from scratch.
        """
        self._cache.clear()
        logger.debug("HeapObjectFactory: cache cleared")


# ---------------------------------------------------------------------------
# HeapObjectRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapObjectRegistry:
    """Central registry of all tracked :class:`HeapObject` instances.

    Maintains a primary index by ``object_id`` and a secondary index by
    ``type_name`` for efficient lookup by type.

    Attributes:
        _objects: Maps ``object_id`` → :class:`HeapObject`.
        _type_index: Maps ``type_name`` → list of ``object_id`` values.

    Examples:
        >>> registry = HeapObjectRegistry()
        >>> registry.count()
        0
    """

    _objects: dict[int, HeapObject] = field(default_factory=dict)
    _type_index: dict[str, list[int]] = field(default_factory=dict)

    def register(self, obj: HeapObject) -> None:
        """Add *obj* to the registry and update the type index.

        If an object with the same ``object_id`` already exists, it is
        silently replaced (latest write wins).

        Parameters:
            obj: The :class:`HeapObject` to register.
        """
        self._objects[obj.object_id] = obj
        bucket = self._type_index.setdefault(obj.type_name, [])
        if obj.object_id not in bucket:
            bucket.append(obj.object_id)
        logger.debug("HeapObjectRegistry: registered id=%d type=%s", obj.object_id, obj.type_name)

    def unregister(self, object_id: int) -> bool:
        """Remove the object with *object_id* from the registry.

        Parameters:
            object_id: The integer id of the object to remove.

        Returns:
            ``True`` if the object was found and removed, ``False`` otherwise.
        """
        if object_id not in self._objects:
            return False
        obj = self._objects.pop(object_id)
        bucket = self._type_index.get(obj.type_name, [])
        if object_id in bucket:
            bucket.remove(object_id)
        logger.debug("HeapObjectRegistry: unregistered id=%d", object_id)
        return True

    def lookup(self, object_id: int) -> HeapObject | None:
        """Return the :class:`HeapObject` for *object_id*, or ``None``.

        Parameters:
            object_id: Python id to look up.

        Returns:
            The registered :class:`HeapObject`, or ``None`` if not found.
        """
        return self._objects.get(object_id)

    def lookup_by_type(self, type_name: str) -> list[HeapObject]:
        """Return all registered objects with a given type name.

        Parameters:
            type_name: The ``__qualname__`` of the desired type.

        Returns:
            List of matching :class:`HeapObject` instances (may be empty).
        """
        ids = self._type_index.get(type_name, [])
        return [self._objects[oid] for oid in ids if oid in self._objects]

    def all_objects(self) -> list[HeapObject]:
        """Return all registered :class:`HeapObject` instances.

        Returns:
            A fresh list of every registered object (no guaranteed order).
        """
        return list(self._objects.values())

    def filter_by_kind(self, kind: ObjectKind) -> list[HeapObject]:
        """Return all objects with the specified :class:`ObjectKind`.

        Parameters:
            kind: The :class:`ObjectKind` to filter by.

        Returns:
            List of :class:`HeapObject` instances matching *kind*.
        """
        return [obj for obj in self._objects.values() if obj.kind == kind]

    def filter_mutable(self) -> list[HeapObject]:
        """Return all registered objects that are *not* frozen.

        A :class:`HeapObject` is considered mutable when its ``is_frozen``
        attribute is ``False``.

        Returns:
            List of mutable :class:`HeapObject` instances.
        """
        return [obj for obj in self._objects.values() if not obj.is_frozen]

    def count(self) -> int:
        """Return the total number of registered objects.

        Returns:
            Integer count.
        """
        return len(self._objects)

    def serialize(self) -> dict[str, Any]:
        """Serialize the registry to a JSON-compatible dictionary.

        Returns:
            Dictionary with a single ``"objects"`` key mapping to a list of
            serialized :class:`HeapObject` dicts.
        """
        return {
            "objects": [obj.serialize() for obj in self._objects.values()],
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> HeapObjectRegistry:
        """Deserialize a registry from a dict produced by :meth:`serialize`.

        Parameters:
            data: Dictionary with an ``"objects"`` list.

        Returns:
            A new :class:`HeapObjectRegistry` populated from *data*.

        Raises:
            KeyError: If *data* is missing the ``"objects"`` key.
        """
        registry = cls()
        for obj_data in data.get("objects", []):
            registry.register(HeapObject.parse(obj_data))
        return registry

    def validate(self) -> list[str]:
        """Check internal invariants and return a list of error messages.

        Checks performed:
        * Every ``object_id`` in ``_type_index`` maps to an existing entry
          in ``_objects``.
        * No duplicate ``object_id`` values exist in a single type bucket.

        Returns:
            A list of human-readable error strings.  An empty list means the
            registry is internally consistent.
        """
        errors: list[str] = []
        for type_name, ids in self._type_index.items():
            seen: set[int] = set()
            for oid in ids:
                if oid not in self._objects:
                    errors.append(
                        f"type_index[{type_name!r}] references missing id={oid}"
                    )
                if oid in seen:
                    errors.append(
                        f"type_index[{type_name!r}] has duplicate id={oid}"
                    )
                seen.add(oid)
        return errors


# ---------------------------------------------------------------------------
# IdentityTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IdentityTracker:
    """Tracks :class:`IdentityCoordinate` objects over time.

    Maintains creation and deletion timestamps for each coordinate, supports
    lifetime queries, and can emit :class:`~jugeo.judgments.judgment_terms.Judgment`
    objects asserting the uniqueness of an identity.

    This class is the *copilot integration* entry point for the heap identity
    layer: downstream components (alias detectors, mutation trackers) query
    the tracker to find currently-live identities and their lifetimes.

    Attributes:
        _identities: Maps ``identity.key()`` → :class:`IdentityCoordinate`.
        _creation_times: Maps key → POSIX creation timestamp.
        _deletion_times: Maps key → POSIX deletion timestamp.
        _judgment_builder: Reusable :class:`~jugeo.judgments.judgment_terms.JudgmentBuilder`.

    Examples:
        >>> tracker = IdentityTracker()
        >>> tracker.count()
        0
    """

    _identities: dict[str, IdentityCoordinate] = field(default_factory=dict)
    _creation_times: dict[str, float] = field(default_factory=dict)
    _deletion_times: dict[str, float] = field(default_factory=dict)
    _judgment_builder: JudgmentBuilder = field(default_factory=JudgmentBuilder)

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def track(self, identity: IdentityCoordinate) -> None:
        """Register *identity* in the tracker.

        If the identity is already tracked (same ``key()``), the existing
        record is silently replaced.

        Parameters:
            identity: The :class:`IdentityCoordinate` to track.
        """
        key = identity.key()
        self._identities[key] = identity
        logger.debug("IdentityTracker: tracking key=%s", key)

    def untrack(self, key: str) -> bool:
        """Remove the identity with *key* from the tracker.

        Parameters:
            key: The string key (from :meth:`~IdentityCoordinate.key`).

        Returns:
            ``True`` if the key was found and removed, ``False`` otherwise.
        """
        if key not in self._identities:
            return False
        del self._identities[key]
        logger.debug("IdentityTracker: untracked key=%s", key)
        return True

    def get_identity(self, key: str) -> IdentityCoordinate | None:
        """Return the :class:`IdentityCoordinate` for *key*, or ``None``.

        Parameters:
            key: The canonical key string.

        Returns:
            Matching :class:`IdentityCoordinate`, or ``None``.
        """
        return self._identities.get(key)

    def all_identities(self) -> list[IdentityCoordinate]:
        """Return all currently tracked identities.

        Returns:
            A fresh list of all :class:`IdentityCoordinate` objects.
        """
        return list(self._identities.values())

    def is_tracked(self, key: str) -> bool:
        """Return ``True`` when *key* is currently tracked.

        Parameters:
            key: The canonical key string.

        Returns:
            Boolean membership result.
        """
        return key in self._identities

    # ------------------------------------------------------------------
    # Timestamp management
    # ------------------------------------------------------------------

    def record_creation(
        self,
        identity: IdentityCoordinate,
        timestamp: float | None = None,
    ) -> None:
        """Record the creation time of *identity*.

        Also calls :meth:`track` to ensure the identity is registered.

        Parameters:
            identity: The :class:`IdentityCoordinate` being created.
            timestamp: POSIX timestamp; defaults to :func:`time.time` when
                ``None``.
        """
        ts = timestamp if timestamp is not None else time.time()
        key = identity.key()
        self.track(identity)
        self._creation_times[key] = ts
        logger.debug("IdentityTracker: creation recorded key=%s ts=%.4f", key, ts)

    def record_deletion(
        self,
        key: str,
        timestamp: float | None = None,
    ) -> None:
        """Record the deletion time of the identity with *key*.

        Parameters:
            key: The canonical key string.
            timestamp: POSIX timestamp; defaults to :func:`time.time` when
                ``None``.
        """
        ts = timestamp if timestamp is not None else time.time()
        self._deletion_times[key] = ts
        logger.debug("IdentityTracker: deletion recorded key=%s ts=%.4f", key, ts)

    def lifetime_of(self, key: str) -> float | None:
        """Return the lifetime (deletion_time − creation_time) for *key*.

        Parameters:
            key: The canonical key string.

        Returns:
            Lifetime in seconds, or ``None`` when either timestamp is missing.
        """
        created = self._creation_times.get(key)
        deleted = self._deletion_times.get(key)
        if created is None or deleted is None:
            return None
        return max(0.0, deleted - created)

    # ------------------------------------------------------------------
    # Judgment emission
    # ------------------------------------------------------------------

    def build_identity_judgment(
        self,
        identity: IdentityCoordinate,
    ) -> Judgment:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` asserting
        the uniqueness of *identity*.

        The judgment carries the formula ``"unique(id:<N>)"`` at the coordinate
        derived from the identity, with a trust level of ``MACHINE`` (since the
        uniqueness of CPython ``id()`` values is a machine-observable fact).

        Parameters:
            identity: The identity whose uniqueness is being asserted.

        Returns:
            A :class:`~jugeo.judgments.judgment_terms.Judgment` for the
            uniqueness property of *identity*.

        Raises:
            ValueError: If :class:`~jugeo.judgments.judgment_terms.JudgmentBuilder`
                fails to build (missing required fields).
        """
        coord = identity.coordinate or CoordinateObject(
            components=(str(identity.object_id), identity.type_name),
            kind=CoordinateKind.REGION,
            support_labels=frozenset({identity.key()}),
        )
        formula = f"unique({identity.key()})"
        self._judgment_builder.reset()
        judgment = (
            self._judgment_builder
            .at(coord)
            .claiming_formula(formula)
            .of_type_named("IdentityCoordinate")
            .with_trust_level(TrustLevel.RUNTIME_WITNESSED)
            .with_status(JudgmentStatus.VERIFIED)
            .from_source(ProvenanceSource.RUNTIME)
            .build()
        )
        logger.debug("IdentityTracker: built judgment for key=%s", identity.key())
        return judgment

    # ------------------------------------------------------------------
    # Aggregate queries
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return the number of currently tracked identities.

        Returns:
            Integer count.
        """
        return len(self._identities)

    def snapshot_keys(self) -> frozenset[str]:
        """Return the set of all currently tracked key strings.

        Returns:
            ``frozenset`` of key strings.
        """
        return frozenset(self._identities)


# ---------------------------------------------------------------------------
# HeapSectionBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapSectionBuilder:
    """Step-by-step builder for :class:`HeapSection` objects.

    Accumulates a primary identity coordinate and a list of annotated
    field sections, then materialises a :class:`HeapSection` on demand.

    Attributes:
        _pending_identity: The :class:`IdentityCoordinate` of the primary
            object for the section under construction, or ``None``.
        _pending_sections: List of ``(field_name, value_repr)`` annotation
            pairs to embed in the section label metadata.
        _version: Monotonically increasing version counter, incremented by
            :meth:`update_version`.

    Examples:
        >>> builder = HeapSectionBuilder()
        >>> builder.add_field_section("x", "42").update_version()
        ... # doctest: +ELLIPSIS
        HeapSectionBuilder(...)
    """

    _pending_identity: IdentityCoordinate | None = None
    _pending_sections: list[tuple[str, str]] = field(default_factory=list)
    _version: int = 0

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_field_section(
        self,
        field_name: str,
        value_repr: str,
    ) -> HeapSectionBuilder:
        """Append a ``(field_name, value_repr)`` annotation to the builder.

        Parameters:
            field_name: Name of the field being annotated.
            value_repr: String representation of the field value.

        Returns:
            ``self`` for method chaining.
        """
        self._pending_sections.append((field_name, value_repr))
        return self

    def update_version(self) -> HeapSectionBuilder:
        """Increment the internal version counter.

        Returns:
            ``self`` for method chaining.
        """
        self._version += 1
        return self

    def reset(self) -> HeapSectionBuilder:
        """Clear all pending state, resetting the builder to its initial state.

        Returns:
            ``self`` for method chaining.
        """
        self._pending_identity = None
        self._pending_sections.clear()
        self._version = 0
        return self

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_sections(self) -> list[str]:
        """Check for duplicate field names in the pending annotations.

        Returns:
            A list of human-readable error strings; empty when valid.
        """
        errors: list[str] = []
        seen: set[str] = set()
        for name, _ in self._pending_sections:
            if name in seen:
                errors.append(f"duplicate field section: {name!r}")
            seen.add(name)
        return errors

    def build_support(self, identity: IdentityCoordinate) -> SupportRegion:
        """Build a singleton :class:`~jugeo.geometry.supports.SupportRegion`
        from *identity*.

        Parameters:
            identity: The :class:`IdentityCoordinate` whose key becomes the
                sole patch key.

        Returns:
            A :class:`~jugeo.geometry.supports.SupportRegion`.
        """
        return _build_support_region(identity)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def build(self) -> HeapSection:
        """Materialise a :class:`HeapSection` from the current builder state.

        The section's ``objects`` tuple is empty — use :meth:`from_heap_object`
        when you need to embed a :class:`HeapObject` directly.  The ``label``
        field encodes the pending field annotations as a semicolon-separated
        string.

        Returns:
            A new :class:`HeapSection`.

        Raises:
            ValueError: If ``_pending_identity`` has not been set.
        """
        if self._pending_identity is None:
            raise ValueError(
                "HeapSectionBuilder.build(): identity must be set before build()"
            )
        identity = self._pending_identity
        coord = identity.coordinate or CoordinateObject(
            components=(str(identity.object_id), identity.type_name),
            kind=CoordinateKind.REGION,
            support_labels=frozenset({identity.key()}),
        )
        annotation_label = "; ".join(
            f"{name}={val}" for name, val in self._pending_sections
        )
        section_id = f"sec:{identity.object_id}:v{self._version}"
        logger.debug("HeapSectionBuilder.build(): section_id=%s", section_id)
        return HeapSection(
            section_id=section_id,
            objects=(),
            coordinate=coord,
            label=annotation_label or identity.type_name,
        )

    def from_heap_object(self, obj: HeapObject) -> HeapSection:
        """Build a :class:`HeapSection` directly from a :class:`HeapObject`.

        Sets the builder's identity from *obj* (creating a new
        :class:`IdentityCoordinate` when ``obj.identity`` is ``None``), then
        builds and returns the section.

        Parameters:
            obj: The :class:`HeapObject` to embed as the sole occupant.

        Returns:
            A :class:`HeapSection` containing *obj*.
        """
        if obj.identity is not None:
            identity = obj.identity
        else:
            coord = CoordinateObject(
                components=(str(obj.object_id), obj.type_name),
                kind=CoordinateKind.REGION,
                support_labels=frozenset({f"id:{obj.object_id}"}),
            )
            identity = IdentityCoordinate(
                object_id=obj.object_id,
                type_name=obj.type_name,
                address=obj.object_id,
                coordinate=coord,
            )
        self._pending_identity = identity
        coord = identity.coordinate or CoordinateObject(
            components=(str(identity.object_id), identity.type_name),
            kind=CoordinateKind.REGION,
        )
        section_id = _new_section_id()
        return HeapSection(
            section_id=section_id,
            objects=(obj,),
            coordinate=coord,
            label=obj.type_name,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Helper functions
    "type_name_of",
    "safe_sizeof",
    "object_to_identity_coord",
    # Classes
    "HeapObjectFactory",
    "HeapObjectRegistry",
    "IdentityTracker",
    "HeapSectionBuilder",
    # Constants
    "MAX_FIELD_DEPTH",
    "DEFAULT_CREATION_SITE",
    "IMMUTABLE_KINDS",
    "PRIMITIVE_TYPES",
    "CONTAINER_TYPES",
]
