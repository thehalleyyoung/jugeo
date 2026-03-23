from __future__ import annotations

"""
Descriptor lookup: route-tagged attribute resolution
=====================================================
theory2.tex Ch16 — Function values, method binding, class construction,
and descriptor lookup.

This module implements the descriptor protocol analysis and route-tagging
infrastructure for jugeo's callable-surface analysis pipeline.  The
descriptor protocol is the mechanism by which Python routes attribute
access through a class hierarchy rather than directly to an instance's
``__dict__``.  A *data descriptor* defines both ``__get__`` and
``__set__`` (or ``__delete__``) and therefore has **higher priority** than
the instance dictionary.  A *non-data descriptor* defines only ``__get__``
and is therefore **overridden** by the instance dictionary.

Route of attribute lookup (CPython, PEP 3135 / ``object.__getattribute__``)
---------------------------------------------------------------------------
1. Search ``type(instance).__mro__`` for a **data descriptor** for the name.
2. Check ``instance.__dict__`` for the name.
3. Search ``type(instance).__mro__`` for a **non-data descriptor** or plain
   class attribute.
4. Raise ``AttributeError``.

This module provides three principal classes:

* ``DescriptorLookupRouteTaggedCoordinator`` — registers descriptors,
  simulates lookup routes, and coordinates morphism descriptions.
* ``DescriptorLookupRouteTaggedAnalyzer`` — static (AST) and live analysis
  of descriptor implementations and usages.
* ``DescriptorLookupRouteTaggedWitness`` — empirically witnesses the
  descriptor protocol by running live Python code and recording evidence.

See also: ``callable_surface_att.py``, ``method_binding_att.py``.
"""

import ast
import dis
import inspect
import logging
import types
import uuid
import time
import re
import textwrap
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Optional jugeo imports — all wrapped in try/except with stub fallbacks so
# that this module remains importable even outside the full jugeo environment.
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateObject, SiteRegistry  # type: ignore
except Exception:  # pragma: no cover
    class CoordinateObject:  # type: ignore  # noqa: D101
        """Stub CoordinateObject used when jugeo.geometry.site is unavailable."""

        def __init__(self, **kwargs: Any) -> None:
            self._data: dict[str, Any] = kwargs

        def __repr__(self) -> str:  # noqa: D105
            return f"CoordinateObject({self._data!r})"

    class SiteRegistry:  # type: ignore  # noqa: D101
        """Stub SiteRegistry."""

        def register(self, obj: Any) -> None:  # noqa: D102
            pass

try:
    from jugeo.judgments.judgment_terms import (  # type: ignore
        JudgmentTerm,
        TrustLevel,
        TermKind,
    )
except Exception:  # pragma: no cover
    class TrustLevel:  # type: ignore  # noqa: D101
        """Stub TrustLevel."""
        HIGH = "high"
        MEDIUM = "medium"
        LOW = "low"

    class TermKind:  # type: ignore  # noqa: D101
        """Stub TermKind."""
        DESCRIPTOR = "descriptor"
        ATTRIBUTE = "attribute"

    class JudgmentTerm:  # type: ignore  # noqa: D101
        """Stub JudgmentTerm."""

        def __init__(self, kind: Any = None, trust: Any = None, **kwargs: Any) -> None:
            self.kind = kind
            self.trust = trust
            self._meta: dict[str, Any] = kwargs

        def to_dict(self) -> dict[str, Any]:  # noqa: D102
            return {"kind": self.kind, "trust": self.trust, **self._meta}

try:
    from jugeo.python_runtime.callable_surfaces.models import (  # type: ignore
        AnalysisRecord,
        SurfaceKind,
        ResolutionStage,
    )
except Exception:  # pragma: no cover
    class AnalysisRecord:  # type: ignore  # noqa: D101
        """Stub AnalysisRecord."""

        def __init__(self, **kwargs: Any) -> None:
            self._fields: dict[str, Any] = kwargs

        def to_dict(self) -> dict[str, Any]:  # noqa: D102
            return dict(self._fields)

    class SurfaceKind:  # type: ignore  # noqa: D101
        """Stub SurfaceKind enum."""
        DESCRIPTOR = "descriptor"
        METHOD = "method"
        PROPERTY = "property"
        SLOT = "slot"

    class ResolutionStage:  # type: ignore  # noqa: D101
        """Stub ResolutionStage enum."""
        DATA_DESCRIPTOR = "data_descriptor"
        INSTANCE_DICT = "instance_dict"
        NON_DATA_DESCRIPTOR = "non_data_descriptor"
        CLASS_ATTRIBUTE = "class_attribute"
        NOT_FOUND = "not_found"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ANALYSIS_CHANNEL: str = "copilot-s03-descriptor-lookup-route-tagged"

# All four dunder methods that participate in the descriptor protocol.
_DESCRIPTOR_PROTOCOL: frozenset[str] = frozenset(
    {"__get__", "__set__", "__delete__", "__set_name__"}
)

# Data descriptors must implement at least __get__ and one of these two.
_DATA_DESCRIPTOR_DUNDERS: frozenset[str] = frozenset({"__get__", "__set__"})

# Non-data descriptors implement only __get__.
_NON_DATA_DESCRIPTOR_DUNDERS: frozenset[str] = frozenset({"__get__"})

# Priority order for the four stages of attribute lookup — lower index = higher priority.
_LOOKUP_ROUTE_PRIORITY: tuple[str, ...] = (
    "data_descriptor",      # highest priority — beats instance dict
    "instance_dict",        # second — the per-instance namespace
    "non_data_descriptor",  # third — can be shadowed by instance dict
    "class_attribute",      # fourth (fallback) — plain class var, no __get__
)

_logger = logging.getLogger(_ANALYSIS_CHANNEL)


# ---------------------------------------------------------------------------
# Helper functions (module-level, exported)
# ---------------------------------------------------------------------------

def is_data_descriptor(obj: Any) -> bool:
    """Return ``True`` if *obj* is a data descriptor.

    A data descriptor defines ``__get__`` and at least one of ``__set__``
    or ``__delete__``.  Data descriptors take priority over the instance
    ``__dict__`` during attribute lookup (see CPython
    ``Objects/object.c:_PyObject_GenericGetAttrWithDict``).

    Parameters
    ----------
    obj:
        Any Python object to test.

    Returns
    -------
    bool
        ``True`` when *obj* satisfies the data-descriptor contract.
    """
    return hasattr(obj, "__get__") and (
        hasattr(obj, "__set__") or hasattr(obj, "__delete__")
    )


def is_non_data_descriptor(obj: Any) -> bool:
    """Return ``True`` if *obj* is a non-data descriptor.

    A non-data descriptor defines ``__get__`` only — no ``__set__`` and no
    ``__delete__``.  Instance ``__dict__`` entries shadow non-data
    descriptors.

    Parameters
    ----------
    obj:
        Any Python object to test.

    Returns
    -------
    bool
        ``True`` when *obj* is a non-data descriptor.
    """
    return (
        hasattr(obj, "__get__")
        and not hasattr(obj, "__set__")
        and not hasattr(obj, "__delete__")
    )


def descriptor_kind(obj: Any) -> str:
    """Classify a descriptor and return a human-readable kind string.

    Returns
    -------
    str
        One of:
        * ``"full"``     — has ``__get__``, ``__set__``, and ``__delete__``
        * ``"data"``     — has ``__get__`` and ``__set__`` (or ``__delete__``)
        * ``"non_data"`` — has only ``__get__``
        * ``"none"``     — not a descriptor at all
    """
    has_get = hasattr(obj, "__get__")
    has_set = hasattr(obj, "__set__")
    has_del = hasattr(obj, "__delete__")

    if has_get and has_set and has_del:
        return "full"
    if has_get and (has_set or has_del):
        return "data"
    if has_get:
        return "non_data"
    return "none"


def mro_descriptor_search(
    cls: type, attr_name: str
) -> tuple[type | None, Any | None]:
    """Search *cls*'s MRO for *attr_name* in each class's ``__dict__``.

    Unlike ``getattr``, this function does **not** invoke any descriptor
    protocol — it inspects raw ``__dict__`` mappings directly, making it
    safe to call during lookup-route analysis without triggering side effects.

    Parameters
    ----------
    cls:
        The class (or instance type) whose MRO should be searched.
    attr_name:
        The attribute name to locate.

    Returns
    -------
    tuple[type | None, Any | None]
        ``(defining_class, raw_value)`` where *defining_class* is the first
        class in the MRO that contains *attr_name*, or ``(None, None)`` if
        not found in any class.
    """
    for klass in cls.__mro__:
        klass_dict: Mapping[str, Any] = klass.__dict__
        if attr_name in klass_dict:
            return klass, klass_dict[attr_name]
    return None, None


def instance_dict_lookup(
    instance: Any, attr_name: str
) -> tuple[bool, Any]:
    """Safely look up *attr_name* in *instance*'s own ``__dict__``.

    Some objects (those with ``__slots__`` and no ``__dict__`` slot) do not
    have an instance dictionary at all — this function handles that gracefully
    by returning ``(False, None)`` when ``__dict__`` is absent.

    Parameters
    ----------
    instance:
        The object whose instance dictionary should be checked.
    attr_name:
        The attribute name to search for.

    Returns
    -------
    tuple[bool, Any]
        ``(True, value)`` if *attr_name* was found, ``(False, None)``
        otherwise.
    """
    inst_dict: dict[str, Any] | None = getattr(instance, "__dict__", None)
    if inst_dict is None:
        return False, None
    if attr_name in inst_dict:
        return True, inst_dict[attr_name]
    return False, None


def lookup_route_priority(route: str) -> int:
    """Return the integer priority for a *route* string.

    Lower values indicate higher priority (data descriptors win).

    Parameters
    ----------
    route:
        One of the strings in ``_LOOKUP_ROUTE_PRIORITY``.

    Returns
    -------
    int
        Priority index, or ``len(_LOOKUP_ROUTE_PRIORITY)`` for unknown routes.
    """
    try:
        return _LOOKUP_ROUTE_PRIORITY.index(route)
    except ValueError:
        # Unknown routes get lowest possible priority.
        return len(_LOOKUP_ROUTE_PRIORITY)


# ---------------------------------------------------------------------------
# DescriptorLookupRouteTaggedCoordinator
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DescriptorLookupRouteTaggedCoordinator:
    """Registers descriptors, simulates lookup routes, and coordinates morphism
    descriptions for the descriptor protocol analysis pipeline.

    This coordinator acts as the stateful registry at the heart of the
    route-tagging system.  Callers register descriptors with
    ``register_descriptor``, then query ``simulate_lookup_route`` to
    understand which resolution stage would be used for a given
    ``(instance, attr_name)`` pair without actually triggering the
    descriptor protocol.

    Attributes
    ----------
    _descriptor_registry:
        Keyed by ``descriptor_id`` (a hex UUID fragment), each entry holds
        metadata about the descriptor: class name, attribute name, kind,
        and a reference to the descriptor object itself.
    _route_log:
        Append-only log of every simulated or real lookup route recorded
        by this coordinator.
    _resolution_cache:
        Maps ``(class_name, attr_name)`` cache keys to ``(route, value)``
        tuples to avoid redundant MRO walks.
    _coordinator_id:
        Unique hex identifier for this coordinator instance.
    """

    _descriptor_registry: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
    _route_log: list[dict[str, Any]] = field(default_factory=list)
    _resolution_cache: dict[str, tuple[str, Any]] = field(default_factory=dict)
    _coordinator_id: str = field(
        default_factory=lambda: uuid.uuid4().hex[:16]
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_descriptor(
        self,
        descriptor: Any,
        owner_class: type,
        attr_name: str,
    ) -> str:
        """Register *descriptor* and return a unique ``descriptor_id``.

        The descriptor is inspected for the four dunder methods of the
        descriptor protocol: ``__get__``, ``__set__``, ``__delete__``, and
        ``__set_name__``.  Based on which are present the descriptor is
        classified as ``"full"``, ``"data"``, ``"non_data"``, or ``"none"``.

        Parameters
        ----------
        descriptor:
            The descriptor object to register.
        owner_class:
            The class that *owns* (defines) the descriptor.
        attr_name:
            The name under which the descriptor is stored in *owner_class*.

        Returns
        -------
        str
            A 16-character hex ``descriptor_id`` that can be used to
            retrieve the registration record.
        """
        kind = descriptor_kind(descriptor)
        descriptor_id = hashlib.md5(
            f"{owner_class.__qualname__}.{attr_name}.{id(descriptor)}".encode()
        ).hexdigest()[:16]

        # Inspect each protocol dunder for presence.
        has_get = hasattr(descriptor, "__get__")
        has_set = hasattr(descriptor, "__set__")
        has_delete = hasattr(descriptor, "__delete__")
        has_set_name = hasattr(descriptor, "__set_name__")

        # Determine the owning module if accessible.
        owner_module = getattr(owner_class, "__module__", "<unknown>")

        record: dict[str, Any] = {
            "descriptor_id": descriptor_id,
            "descriptor_repr": repr(descriptor),
            "descriptor_type": type(descriptor).__qualname__,
            "owner_class": owner_class.__qualname__,
            "owner_module": owner_module,
            "attr_name": attr_name,
            "kind": kind,  # "full" | "data" | "non_data" | "none"
            "has_get": has_get,
            "has_set": has_set,
            "has_delete": has_delete,
            "has_set_name": has_set_name,
            "registered_at": time.monotonic(),
        }

        self._descriptor_registry[descriptor_id] = record
        _logger.debug(
            "Registered descriptor %s (%s) for %s.%s",
            descriptor_id,
            kind,
            owner_class.__qualname__,
            attr_name,
        )
        return descriptor_id

    def simulate_lookup_route(
        self,
        instance: Any,
        attr_name: str,
    ) -> dict[str, Any]:
        """Simulate — without triggering — the full descriptor lookup for
        ``instance.attr_name`` and return a detailed route record.

        The simulation follows CPython's documented priority order:

        1. Search MRO for a **data descriptor**.
        2. Check ``instance.__dict__``.
        3. Search MRO for a **non-data descriptor** or plain class attribute.
        4. Conclude ``not_found``.

        This function never calls ``__get__``, ``__set__``, or
        ``__delete__`` — it only inspects ``__dict__`` mappings.

        Parameters
        ----------
        instance:
            The object for which to simulate attribute lookup.
        attr_name:
            The attribute name to resolve.

        Returns
        -------
        dict[str, Any]
            A route record with keys: ``route``, ``priority``,
            ``defining_class``, ``raw_descriptor``, ``descriptor_kind``,
            ``instance_dict_value``, ``instance_has_own``, ``timestamp``.
        """
        instance_type = type(instance)
        cache_key = f"{instance_type.__qualname__}.{attr_name}"

        # Stage 1 — search MRO for a data descriptor.
        defining_class, raw_value = mro_descriptor_search(instance_type, attr_name)
        if raw_value is not None and is_data_descriptor(raw_value):
            route = "data_descriptor"
            priority = lookup_route_priority(route)
            result = {
                "route": route,
                "priority": priority,
                "defining_class": defining_class.__qualname__ if defining_class else None,
                "raw_descriptor": repr(raw_value),
                "descriptor_kind": descriptor_kind(raw_value),
                "instance_dict_value": None,
                "instance_has_own": False,
                "attr_name": attr_name,
                "instance_type": instance_type.__qualname__,
                "timestamp": time.monotonic(),
            }
            self._route_log.append(result)
            self._resolution_cache[cache_key] = (route, raw_value)
            return result

        # Stage 2 — check instance.__dict__.
        inst_found, inst_value = instance_dict_lookup(instance, attr_name)
        if inst_found:
            route = "instance_dict"
            priority = lookup_route_priority(route)
            result = {
                "route": route,
                "priority": priority,
                "defining_class": None,
                "raw_descriptor": None,
                "descriptor_kind": "none",
                "instance_dict_value": repr(inst_value),
                "instance_has_own": True,
                "attr_name": attr_name,
                "instance_type": instance_type.__qualname__,
                "timestamp": time.monotonic(),
            }
            self._route_log.append(result)
            self._resolution_cache[cache_key] = (route, inst_value)
            return result

        # Stage 3 — search MRO for a non-data descriptor or plain class attr.
        if raw_value is not None:
            kind = descriptor_kind(raw_value)
            route = "non_data_descriptor" if kind != "none" else "class_attribute"
            priority = lookup_route_priority(route)
            result = {
                "route": route,
                "priority": priority,
                "defining_class": defining_class.__qualname__ if defining_class else None,
                "raw_descriptor": repr(raw_value),
                "descriptor_kind": kind,
                "instance_dict_value": None,
                "instance_has_own": False,
                "attr_name": attr_name,
                "instance_type": instance_type.__qualname__,
                "timestamp": time.monotonic(),
            }
            self._route_log.append(result)
            self._resolution_cache[cache_key] = (route, raw_value)
            return result

        # Stage 4 — not found.
        route = "not_found"
        result = {
            "route": route,
            "priority": len(_LOOKUP_ROUTE_PRIORITY),
            "defining_class": None,
            "raw_descriptor": None,
            "descriptor_kind": "none",
            "instance_dict_value": None,
            "instance_has_own": False,
            "attr_name": attr_name,
            "instance_type": instance_type.__qualname__,
            "timestamp": time.monotonic(),
        }
        self._route_log.append(result)
        return result

    def tag_route(
        self,
        route: str,
        attr_name: str,
        owner: str,
        descriptor_kind_str: str,
    ) -> dict[str, Any]:
        """Create a *route tag* dict fully describing a resolution route.

        Route tags are immutable descriptive records attached to analysis
        artifacts.  They capture the resolution route as a string together
        with its numeric priority and the descriptor kind involved.

        Parameters
        ----------
        route:
            One of ``"data_descriptor"``, ``"instance_dict"``,
            ``"non_data_descriptor"``, ``"class_attribute"``, or
            ``"not_found"``.
        attr_name:
            The attribute name being resolved.
        owner:
            Qualified name of the class that owns the descriptor (if any).
        descriptor_kind_str:
            One of ``"full"``, ``"data"``, ``"non_data"``, or ``"none"``.

        Returns
        -------
        dict[str, Any]
            A tag dict with: ``route``, ``priority``, ``attr_name``,
            ``owner``, ``descriptor_kind``, ``tag_id``, ``channel``.
        """
        priority = lookup_route_priority(route)
        tag_id = uuid.uuid4().hex[:8]
        return {
            "tag_id": tag_id,
            "channel": _ANALYSIS_CHANNEL,
            "route": route,
            "priority": priority,
            "attr_name": attr_name,
            "owner": owner,
            "descriptor_kind": descriptor_kind_str,
            "coordinator_id": self._coordinator_id,
        }

    def descriptor_coordinate(
        self,
        descriptor: Any,
        owner_class: type,
        attr_name: str,
    ) -> CoordinateObject:
        """Build a ``CoordinateObject`` for *descriptor* in the class hierarchy.

        The coordinate encodes the descriptor's position in the class
        hierarchy as well as its kind and priority, providing a stable
        identity for the descriptor within the jugeo geometry system.

        Parameters
        ----------
        descriptor:
            The descriptor object.
        owner_class:
            The class that owns the descriptor.
        attr_name:
            The name under which the descriptor is stored.

        Returns
        -------
        CoordinateObject
            A geometry coordinate capturing the descriptor's context.
        """
        kind = descriptor_kind(descriptor)
        # Determine the MRO depth at which the descriptor is defined.
        mro_depth: int = 0
        for depth, klass in enumerate(owner_class.__mro__):
            if attr_name in klass.__dict__:
                mro_depth = depth
                break

        return CoordinateObject(
            namespace=f"{owner_class.__module__}.{owner_class.__qualname__}",
            attr_name=attr_name,
            descriptor_kind=kind,
            mro_depth=mro_depth,
            priority=lookup_route_priority(
                "data_descriptor" if kind in ("data", "full") else "non_data_descriptor"
            ),
            coordinator_id=self._coordinator_id,
        )

    def lookup_morphism(
        self,
        instance_type: type,
        attr_name: str,
        route: str,
    ) -> dict[str, Any]:
        """Describe the morphism for attribute resolution.

        In category-theoretic terms, attribute lookup is a morphism from
        the object ``(instance_type, attr_name)`` to a value, mediated by
        either the type hierarchy (MRO) or the instance dictionary.  This
        function returns a dict that makes that morphism explicit for
        documentation and analysis purposes.

        Parameters
        ----------
        instance_type:
            The type of the instance being looked up.
        attr_name:
            The attribute name.
        route:
            The route through which the value would be found.

        Returns
        -------
        dict[str, Any]
            Morphism description with source, target mediator, route,
            and priority.
        """
        # Determine the target mediator: MRO or instance dict.
        mediator: str
        if route in ("data_descriptor", "non_data_descriptor", "class_attribute"):
            mediator = f"MRO({instance_type.__qualname__})"
        elif route == "instance_dict":
            mediator = f"instance.__dict__"
        else:
            mediator = "nowhere (not found)"

        return {
            "morphism_id": uuid.uuid4().hex[:12],
            "source": f"({instance_type.__qualname__}, {attr_name!r})",
            "target": "value",
            "mediator": mediator,
            "route": route,
            "priority": lookup_route_priority(route),
            "instance_type": instance_type.__qualname__,
            "attr_name": attr_name,
            "channel": _ANALYSIS_CHANNEL,
        }

    def check_descriptor_conflict(
        self,
        cls: type,
        attr_name: str,
    ) -> list[dict[str, Any]]:
        """Check whether *attr_name* in *cls* has a data-descriptor conflict.

        A conflict occurs when both the class's MRO provides a data
        descriptor for *attr_name* **and** instances can be constructed
        with *attr_name* in their ``__dict__``.  Because data descriptors
        beat the instance dict, the instance dict entry would be
        permanently shadowed — which is usually intentional (e.g.,
        ``property``) but sometimes a bug.

        Parameters
        ----------
        cls:
            The class to inspect.
        attr_name:
            The attribute name to check for conflicts.

        Returns
        -------
        list[dict[str, Any]]
            Possibly empty list of conflict records.  Each record describes
            the conflict with ``conflict_type``, ``attr_name``,
            ``defining_class``, and ``descriptor_kind``.
        """
        conflicts: list[dict[str, Any]] = []
        defining_class, raw_value = mro_descriptor_search(cls, attr_name)

        if raw_value is None:
            # No class-level attribute — no conflict possible.
            return conflicts

        kind = descriptor_kind(raw_value)
        if kind in ("data", "full"):
            # Data descriptor found — check whether instances might have the
            # key in __dict__ by looking at __init__ source (best effort).
            init_fn = getattr(cls, "__init__", None)
            init_source: str = ""
            try:
                init_source = inspect.getsource(init_fn) if init_fn else ""
            except (OSError, TypeError):
                init_source = ""

            # Heuristic: does __init__ assign self.attr_name = ...?
            pattern = rf"\bself\.{re.escape(attr_name)}\s*="
            if re.search(pattern, init_source):
                # The assignment will be intercepted by the data descriptor's
                # __set__ — this is expected behaviour for data descriptors but
                # worth flagging for audit purposes.
                conflicts.append(
                    {
                        "conflict_type": "data_descriptor_shadows_instance_assignment",
                        "attr_name": attr_name,
                        "defining_class": defining_class.__qualname__ if defining_class else None,
                        "descriptor_kind": kind,
                        "note": (
                            "__init__ assigns self.{} but a data descriptor "
                            "will intercept via __set__".format(attr_name)
                        ),
                        "severity": "info",
                    }
                )

        return conflicts

    def summary(self) -> dict[str, Any]:
        """Return a statistics summary for this coordinator.

        Returns
        -------
        dict[str, Any]
            Summary with counts of registered descriptors by kind, total
            route log entries, cache hit statistics, and coordinator id.
        """
        kind_counts: dict[str, int] = defaultdict(int)
        for record in self._descriptor_registry.values():
            kind_counts[record["kind"]] += 1

        # Tally route log by route type.
        route_counts: dict[str, int] = defaultdict(int)
        for entry in self._route_log:
            route_counts[entry["route"]] += 1

        return {
            "coordinator_id": self._coordinator_id,
            "registered_descriptors": len(self._descriptor_registry),
            "descriptor_kinds": dict(kind_counts),
            "route_log_entries": len(self._route_log),
            "route_distribution": dict(route_counts),
            "cache_entries": len(self._resolution_cache),
            "channel": _ANALYSIS_CHANNEL,
        }


# ---------------------------------------------------------------------------
# DescriptorLookupRouteTaggedAnalyzer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DescriptorLookupRouteTaggedAnalyzer:
    """Static (AST) and live analysis of descriptor implementations and usages.

    This analyzer operates at two levels:

    * **Static level** — parses Python source code with ``ast`` and walks
      the syntax tree to identify ClassDef nodes that implement the
      descriptor protocol, classify them, and find usage sites.
    * **Live level** — inspects running Python objects using ``inspect``,
      ``hasattr``, and ``getattr`` to profile descriptors and trace
      actual attribute lookup routes.

    Attributes
    ----------
    _coordinator:
        The ``DescriptorLookupRouteTaggedCoordinator`` used for route
        simulation and morphism documentation.
    _ast_cache:
        Maps source hashes to parsed ``ast.Module`` objects to avoid
        redundant parsing.
    _descriptor_analysis_cache:
        Maps class qualnames to their descriptor analysis results.
    _stats:
        Counter dict for tracking analysis operations.
    """

    _coordinator: DescriptorLookupRouteTaggedCoordinator = field(
        default_factory=DescriptorLookupRouteTaggedCoordinator
    )
    _ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    _descriptor_analysis_cache: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
    _stats: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )

    # ------------------------------------------------------------------
    # Static analysis
    # ------------------------------------------------------------------

    def analyze_source(
        self,
        source: str,
        module_name: str = "<module>",
    ) -> dict[str, Any]:
        """AST analysis of *source*: finds descriptor classes and usage sites.

        Parses the source, walks the AST to find ``ClassDef`` nodes that
        implement the descriptor protocol (define ``__get__``, ``__set__``,
        and/or ``__delete__``), classifies each as data or non-data, and
        finds usage sites (assignments of descriptor instances in class
        bodies).

        Parameters
        ----------
        source:
            Python source code as a string.
        module_name:
            Optional name for the module (used in error messages).

        Returns
        -------
        dict[str, Any]
            Comprehensive analysis result with keys:
            ``module_name``, ``descriptor_classes``, ``descriptor_usages``,
            ``data_descriptor_count``, ``non_data_descriptor_count``,
            ``parse_errors``.
        """
        self._stats["analyze_source_calls"] += 1
        source_hash = hashlib.md5(source.encode()).hexdigest()
        parse_errors: list[str] = []

        # Use cached AST if available.
        if source_hash in self._ast_cache:
            tree = self._ast_cache[source_hash]
        else:
            try:
                tree = ast.parse(source, filename=module_name)
                self._ast_cache[source_hash] = tree
            except SyntaxError as exc:
                parse_errors.append(str(exc))
                return {
                    "module_name": module_name,
                    "descriptor_classes": [],
                    "descriptor_usages": [],
                    "data_descriptor_count": 0,
                    "non_data_descriptor_count": 0,
                    "parse_errors": parse_errors,
                    "source_hash": source_hash,
                }

        descriptor_classes = self.find_descriptor_classes(tree)
        descriptor_usages = self.find_descriptor_usages(tree)

        # Classify counts.
        data_count = sum(
            1 for dc in descriptor_classes if dc.get("is_data_descriptor", False)
        )
        non_data_count = len(descriptor_classes) - data_count

        return {
            "module_name": module_name,
            "descriptor_classes": descriptor_classes,
            "descriptor_usages": descriptor_usages,
            "data_descriptor_count": data_count,
            "non_data_descriptor_count": non_data_count,
            "total_descriptor_classes": len(descriptor_classes),
            "total_descriptor_usages": len(descriptor_usages),
            "parse_errors": parse_errors,
            "source_hash": source_hash,
            "channel": _ANALYSIS_CHANNEL,
        }

    def find_descriptor_classes(
        self,
        tree: ast.AST,
    ) -> list[dict[str, Any]]:
        """Walk *tree* and identify ``ClassDef`` nodes that implement
        the descriptor protocol.

        A class is considered a descriptor if its body contains a
        ``FunctionDef`` (or ``AsyncFunctionDef``) named ``__get__``,
        ``__set__``, or ``__delete__``.

        Parameters
        ----------
        tree:
            An ``ast.AST`` node (typically a ``Module``) to walk.

        Returns
        -------
        list[dict[str, Any]]
            List of descriptor class records, each containing:
            ``class_name``, ``lineno``, ``has_get``, ``has_set``,
            ``has_delete``, ``has_set_name``, ``is_data_descriptor``,
            ``is_non_data_descriptor``, ``method_names``.
        """
        results: list[dict[str, Any]] = []
        func_def_types = (ast.FunctionDef, ast.AsyncFunctionDef)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Collect the names of all methods defined in the class body.
            method_names: set[str] = set()
            for item in node.body:
                if isinstance(item, func_def_types):
                    method_names.add(item.name)

            has_get = "__get__" in method_names
            has_set = "__set__" in method_names
            has_delete = "__delete__" in method_names
            has_set_name = "__set_name__" in method_names

            # A class is a descriptor only if it implements at least __get__.
            if not has_get:
                continue

            is_data = has_get and (has_set or has_delete)
            is_non_data = has_get and not has_set and not has_delete

            results.append(
                {
                    "class_name": node.name,
                    "lineno": node.lineno,
                    "has_get": has_get,
                    "has_set": has_set,
                    "has_delete": has_delete,
                    "has_set_name": has_set_name,
                    "is_data_descriptor": is_data,
                    "is_non_data_descriptor": is_non_data,
                    "method_names": sorted(method_names),
                }
            )

        return results

    def find_descriptor_usages(
        self,
        tree: ast.AST,
    ) -> list[dict[str, Any]]:
        """Walk *tree* looking for class-body assignments that instantiate
        a known descriptor class.

        We detect patterns like::

            class MyClass:
                x = MyDescriptor()

        by checking for ``Assign`` nodes in ``ClassDef`` bodies whose
        right-hand side is a ``Call`` node.

        Parameters
        ----------
        tree:
            An ``ast.AST`` node to walk.

        Returns
        -------
        list[dict[str, Any]]
            List of usage records with: ``owner_class``, ``attr_name``,
            ``descriptor_class_name``, ``lineno``.
        """
        # First pass: collect all known descriptor class names from this tree.
        descriptor_names: set[str] = {
            dc["class_name"] for dc in self.find_descriptor_classes(tree)
        }

        usages: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            owner_name = node.name
            for item in node.body:
                if not isinstance(item, ast.Assign):
                    continue
                if not isinstance(item.value, ast.Call):
                    continue
                call = item.value
                # Determine the callee name.
                callee_name: str | None = None
                if isinstance(call.func, ast.Name):
                    callee_name = call.func.id
                elif isinstance(call.func, ast.Attribute):
                    callee_name = call.func.attr

                if callee_name not in descriptor_names:
                    continue

                # Extract the attribute names being assigned.
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        usages.append(
                            {
                                "owner_class": owner_name,
                                "attr_name": target.id,
                                "descriptor_class_name": callee_name,
                                "lineno": item.lineno,
                            }
                        )

        return usages

    # ------------------------------------------------------------------
    # Live analysis
    # ------------------------------------------------------------------

    def analyze_live_descriptor(self, descriptor: Any) -> dict[str, Any]:
        """Inspect a live *descriptor* object and return its protocol profile.

        Uses ``hasattr`` and ``inspect.signature`` to gather detailed
        information about the descriptor without calling any of its
        methods.

        Parameters
        ----------
        descriptor:
            Any Python object that may or may not implement the descriptor
            protocol.

        Returns
        -------
        dict[str, Any]
            Profile dict with keys: ``type_name``, ``kind``, ``has_get``,
            ``has_set``, ``has_delete``, ``has_set_name``, ``get_signature``,
            ``set_signature``, ``set_name_signature``, ``is_builtin``,
            ``module``.
        """
        kind = descriptor_kind(descriptor)
        desc_type = type(descriptor)

        # Try to extract signatures for each protocol method.
        def _sig(attr: str) -> str:
            fn = getattr(descriptor, attr, None)
            if fn is None:
                return "N/A"
            try:
                return str(inspect.signature(fn))
            except (ValueError, TypeError):
                return "<unresolvable>"

        return {
            "type_name": desc_type.__qualname__,
            "module": getattr(desc_type, "__module__", "<unknown>"),
            "kind": kind,
            "has_get": hasattr(descriptor, "__get__"),
            "has_set": hasattr(descriptor, "__set__"),
            "has_delete": hasattr(descriptor, "__delete__"),
            "has_set_name": hasattr(descriptor, "__set_name__"),
            "get_signature": _sig("__get__"),
            "set_signature": _sig("__set__"),
            "delete_signature": _sig("__delete__"),
            "set_name_signature": _sig("__set_name__"),
            "is_builtin": isinstance(descriptor, types.BuiltinFunctionType),
            "repr": repr(descriptor),
        }

    def analyze_class_descriptors(self, cls: type) -> dict[str, Any]:
        """For each attribute in *cls*'s own ``__dict__``, check if it is a
        descriptor and categorize it.

        This function only inspects the class's *own* dictionary (not
        inherited attributes) to avoid noise from base classes.

        Parameters
        ----------
        cls:
            The class to analyze.

        Returns
        -------
        dict[str, Any]
            Mapping with keys: ``class_name``, ``data_descriptors``,
            ``non_data_descriptors``, ``plain_attributes``, ``total``.
        """
        self._stats["analyze_class_calls"] += 1
        data_descs: list[dict[str, Any]] = []
        non_data_descs: list[dict[str, Any]] = []
        plain_attrs: list[dict[str, Any]] = []

        for attr_name, value in cls.__dict__.items():
            kind = descriptor_kind(value)
            entry = {
                "attr_name": attr_name,
                "type": type(value).__qualname__,
                "kind": kind,
                "repr": repr(value)[:80],
            }
            if kind in ("data", "full"):
                data_descs.append(entry)
            elif kind == "non_data":
                non_data_descs.append(entry)
            else:
                plain_attrs.append(entry)

        # Cache the result under the class qualname.
        result = {
            "class_name": cls.__qualname__,
            "data_descriptors": data_descs,
            "non_data_descriptors": non_data_descs,
            "plain_attributes": plain_attrs,
            "total": len(cls.__dict__),
        }
        self._descriptor_analysis_cache[cls.__qualname__] = result
        return result

    def trace_attribute_lookup(
        self,
        instance: Any,
        attr_name: str,
    ) -> dict[str, Any]:
        """Perform attribute lookup tracing for ``instance.attr_name``.

        Walks the MRO manually to determine the resolution stage without
        triggering any descriptor __get__ calls on non-built-in types.

        Parameters
        ----------
        instance:
            The object to trace attribute lookup on.
        attr_name:
            The attribute name to trace.

        Returns
        -------
        dict[str, Any]
            Trace dict with keys: ``route``, ``value_repr``, ``found``,
            ``descriptor_class``, ``defining_class``, ``instance_type``,
            ``mro_searched``.
        """
        self._stats["trace_lookup_calls"] += 1
        instance_type = type(instance)
        mro_searched: list[str] = []

        # Stage 1 — data descriptor in MRO.
        for klass in instance_type.__mro__:
            mro_searched.append(klass.__qualname__)
            if attr_name in klass.__dict__:
                raw = klass.__dict__[attr_name]
                if is_data_descriptor(raw):
                    # Safely invoke __get__ to get the actual value.
                    try:
                        value = raw.__get__(instance, instance_type)
                        value_repr = repr(value)[:120]
                        found = True
                    except Exception as exc:
                        value_repr = f"<error: {exc}>"
                        found = False
                    return {
                        "route": "data_descriptor",
                        "value_repr": value_repr,
                        "found": found,
                        "descriptor_class": type(raw).__qualname__,
                        "defining_class": klass.__qualname__,
                        "instance_type": instance_type.__qualname__,
                        "mro_searched": mro_searched,
                    }
                break  # Found in MRO but not a data descriptor; move on.

        # Stage 2 — instance __dict__.
        inst_found, inst_value = instance_dict_lookup(instance, attr_name)
        if inst_found:
            return {
                "route": "instance_dict",
                "value_repr": repr(inst_value)[:120],
                "found": True,
                "descriptor_class": None,
                "defining_class": None,
                "instance_type": instance_type.__qualname__,
                "mro_searched": mro_searched,
            }

        # Stage 3 — non-data descriptor or plain class attribute.
        for klass in instance_type.__mro__:
            if attr_name in klass.__dict__:
                raw = klass.__dict__[attr_name]
                kind = descriptor_kind(raw)
                if kind == "non_data":
                    try:
                        value = raw.__get__(instance, instance_type)
                        value_repr = repr(value)[:120]
                        found = True
                    except Exception as exc:
                        value_repr = f"<error: {exc}>"
                        found = False
                    return {
                        "route": "non_data_descriptor",
                        "value_repr": value_repr,
                        "found": found,
                        "descriptor_class": type(raw).__qualname__,
                        "defining_class": klass.__qualname__,
                        "instance_type": instance_type.__qualname__,
                        "mro_searched": mro_searched,
                    }
                # Plain class attribute.
                return {
                    "route": "class_attribute",
                    "value_repr": repr(raw)[:120],
                    "found": True,
                    "descriptor_class": None,
                    "defining_class": klass.__qualname__,
                    "instance_type": instance_type.__qualname__,
                    "mro_searched": mro_searched,
                }

        # Not found anywhere.
        return {
            "route": "not_found",
            "value_repr": None,
            "found": False,
            "descriptor_class": None,
            "defining_class": None,
            "instance_type": instance_type.__qualname__,
            "mro_searched": mro_searched,
        }

    def analyze_property_descriptors(self, cls: type) -> dict[str, Any]:
        """Specifically analyze ``property`` descriptors defined in *cls*.

        Properties are the most common user-facing descriptor type.  This
        method extracts their ``fget``, ``fset``, and ``fdel`` functions and
        inspects their signatures to build a detailed property map.

        Parameters
        ----------
        cls:
            The class to inspect for ``property`` descriptors.

        Returns
        -------
        dict[str, Any]
            Property map with keys: ``class_name``, ``properties`` (list),
            ``total_properties``, ``read_only_count``, ``read_write_count``.
        """
        properties: list[dict[str, Any]] = []
        read_only = 0
        read_write = 0

        for attr_name, value in cls.__dict__.items():
            if not isinstance(value, property):
                continue

            fget_sig = fset_sig = fdel_sig = "N/A"
            try:
                if value.fget is not None:
                    fget_sig = str(inspect.signature(value.fget))
                if value.fset is not None:
                    fset_sig = str(inspect.signature(value.fset))
                if value.fdel is not None:
                    fdel_sig = str(inspect.signature(value.fdel))
            except (ValueError, TypeError):
                pass

            is_read_only = value.fset is None
            if is_read_only:
                read_only += 1
            else:
                read_write += 1

            properties.append(
                {
                    "attr_name": attr_name,
                    "has_getter": value.fget is not None,
                    "has_setter": value.fset is not None,
                    "has_deleter": value.fdel is not None,
                    "is_read_only": is_read_only,
                    "fget_signature": fget_sig,
                    "fset_signature": fset_sig,
                    "fdel_signature": fdel_sig,
                    "docstring": (value.__doc__ or "")[:200],
                }
            )

        return {
            "class_name": cls.__qualname__,
            "properties": properties,
            "total_properties": len(properties),
            "read_only_count": read_only,
            "read_write_count": read_write,
        }

    def compare_lookup_routes(
        self,
        cls1: type,
        cls2: type,
        attr_name: str,
    ) -> dict[str, Any]:
        """Compare how *attr_name* would be resolved in *cls1* vs *cls2*.

        This is useful for understanding behavioural differences between
        two classes with respect to a shared attribute name, e.g., when
        subclassing changes the descriptor.

        Parameters
        ----------
        cls1, cls2:
            The two classes to compare.
        attr_name:
            The attribute name to check.

        Returns
        -------
        dict[str, Any]
            Comparison dict with route info for each class and a
            ``differs`` boolean.
        """
        def _route_for(cls: type) -> dict[str, Any]:
            defining_class, raw_value = mro_descriptor_search(cls, attr_name)
            if raw_value is None:
                return {"route": "not_found", "kind": "none", "defining_class": None}
            kind = descriptor_kind(raw_value)
            if kind in ("data", "full"):
                route = "data_descriptor"
            elif kind == "non_data":
                route = "non_data_descriptor"
            else:
                route = "class_attribute"
            return {
                "route": route,
                "kind": kind,
                "defining_class": defining_class.__qualname__ if defining_class else None,
                "priority": lookup_route_priority(route),
            }

        route1 = _route_for(cls1)
        route2 = _route_for(cls2)
        differs = route1["route"] != route2["route"] or route1["kind"] != route2["kind"]

        return {
            "attr_name": attr_name,
            cls1.__qualname__: route1,
            cls2.__qualname__: route2,
            "differs": differs,
            "priority_delta": abs(route1["priority"] - route2["priority"]),
        }

    def emit_descriptor_judgment(
        self,
        descriptor: Any,
        owner_class: type,
        attr_name: str,
        route: str,
        trust_level: Any = None,
    ) -> dict[str, Any]:
        """Emit a jugeo judgment for a descriptor.

        Packages the descriptor analysis as a ``JudgmentTerm`` and
        returns a serialized dict.  Uses the stub ``TrustLevel.HIGH`` if
        no explicit trust level is provided.

        Parameters
        ----------
        descriptor:
            The descriptor object.
        owner_class:
            Owning class.
        attr_name:
            Attribute name.
        route:
            The resolution route string.
        trust_level:
            Optional trust level; defaults to ``TrustLevel.HIGH``.

        Returns
        -------
        dict[str, Any]
            Serialized judgment term dict.
        """
        if trust_level is None:
            trust_level = TrustLevel.HIGH

        kind = descriptor_kind(descriptor)
        term = JudgmentTerm(
            kind=TermKind.DESCRIPTOR,
            trust=trust_level,
            owner=owner_class.__qualname__,
            attr_name=attr_name,
            descriptor_kind=kind,
            route=route,
            priority=lookup_route_priority(route),
            channel=_ANALYSIS_CHANNEL,
        )
        return term.to_dict()


# ---------------------------------------------------------------------------
# DescriptorLookupRouteTaggedWitness
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DescriptorLookupRouteTaggedWitness:
    """Empirically witnesses the descriptor protocol by running live Python code.

    The witness pattern (from jugeo's epistemological model) requires that
    claims about runtime behaviour be backed by actual evidence — not just
    static analysis.  This class *executes* the descriptor protocol and
    records what actually happened, creating a verifiable evidence trail.

    Attributes
    ----------
    _analyzer:
        The ``DescriptorLookupRouteTaggedAnalyzer`` used for analysis
        support and route tracing.
    _witnessed_lookups:
        Append-only list of witnessed lookup records.
    _route_evidence:
        Evidence records linking simulated routes to witnessed routes.
    _protocol_violations:
        Records of descriptors that violated the protocol contract.
    _witness_id:
        Unique hex identifier for this witness instance.
    """

    _analyzer: DescriptorLookupRouteTaggedAnalyzer = field(
        default_factory=DescriptorLookupRouteTaggedAnalyzer
    )
    _witnessed_lookups: list[dict[str, Any]] = field(default_factory=list)
    _route_evidence: list[dict[str, Any]] = field(default_factory=list)
    _protocol_violations: list[dict[str, Any]] = field(default_factory=list)
    _witness_id: str = field(
        default_factory=lambda: uuid.uuid4().hex[:16]
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def witness_descriptor_protocol(
        self,
        descriptor: Any,
        owner_class: type,
        attr_name: str,
    ) -> dict[str, Any]:
        """Verify that *descriptor* actually works according to the protocol.

        Creates a fresh instance of *owner_class*, calls ``getattr`` on
        it, and records whether the invocation succeeded.  For data
        descriptors, also calls ``setattr`` and verifies the ``__set__``
        path is exercised.

        Parameters
        ----------
        descriptor:
            The descriptor to test.
        owner_class:
            The class owning the descriptor.
        attr_name:
            The attribute name.

        Returns
        -------
        dict[str, Any]
            Protocol evidence with keys: ``get_succeeded``,
            ``set_succeeded``, ``delete_succeeded``, ``get_return_repr``,
            ``errors``.
        """
        errors: list[str] = []
        get_succeeded = False
        set_succeeded = False
        delete_succeeded = False
        get_return_repr: str = "N/A"

        # Attempt to create a dummy instance.  Some classes require arguments;
        # we try with no arguments and fall back to object().
        try:
            instance = object.__new__(owner_class)
        except TypeError:
            instance = object.__new__(object)  # type: ignore[arg-type]

        # Test __get__.
        if hasattr(descriptor, "__get__"):
            try:
                result = descriptor.__get__(instance, owner_class)
                get_return_repr = repr(result)[:120]
                get_succeeded = True
            except Exception as exc:
                errors.append(f"__get__ raised: {exc!r}")

        # Test __set__ (data descriptor).
        if hasattr(descriptor, "__set__"):
            try:
                descriptor.__set__(instance, object())
                set_succeeded = True
            except Exception as exc:
                errors.append(f"__set__ raised: {exc!r}")

        # Test __delete__ (data descriptor with delete).
        if hasattr(descriptor, "__delete__"):
            try:
                descriptor.__delete__(instance)
                delete_succeeded = True
            except Exception as exc:
                errors.append(f"__delete__ raised: {exc!r}")

        kind = descriptor_kind(descriptor)
        evidence: dict[str, Any] = {
            "witness_id": self._witness_id,
            "descriptor_type": type(descriptor).__qualname__,
            "owner_class": owner_class.__qualname__,
            "attr_name": attr_name,
            "kind": kind,
            "get_succeeded": get_succeeded,
            "set_succeeded": set_succeeded,
            "delete_succeeded": delete_succeeded,
            "get_return_repr": get_return_repr,
            "errors": errors,
            "protocol_intact": get_succeeded and len(errors) == 0,
            "timestamp": time.monotonic(),
        }
        self._witnessed_lookups.append(evidence)
        return evidence

    def witness_lookup_route(
        self,
        instance: Any,
        attr_name: str,
    ) -> dict[str, Any]:
        """Witness the actual attribute lookup for ``instance.attr_name``.

        Performs the real ``getattr`` call and records what value was
        returned.  Also calls ``_analyzer.trace_attribute_lookup`` to
        compare the witnessed route with the analytically determined route.

        Parameters
        ----------
        instance:
            The object to look up the attribute on.
        attr_name:
            The attribute name to access.

        Returns
        -------
        dict[str, Any]
            Witness record with: ``witnessed_value_repr``, ``get_succeeded``,
            ``traced_route``, ``routes_agree``, ``error``.
        """
        # Trace the route analytically (without side effects).
        traced = self._analyzer.trace_attribute_lookup(instance, attr_name)

        # Actually perform the lookup.
        witnessed_value_repr: str = "N/A"
        get_succeeded = False
        error_msg: str | None = None
        witnessed_route: str = "not_found"

        try:
            value = getattr(instance, attr_name)
            witnessed_value_repr = repr(value)[:120]
            get_succeeded = True
            # Infer witnessed route by consulting the trace.
            witnessed_route = traced.get("route", "not_found")
        except AttributeError as exc:
            error_msg = str(exc)
            witnessed_route = "not_found"
        except Exception as exc:
            error_msg = f"unexpected: {exc!r}"

        routes_agree = (witnessed_route == traced.get("route", "not_found"))
        record = {
            "witness_id": self._witness_id,
            "instance_type": type(instance).__qualname__,
            "attr_name": attr_name,
            "witnessed_value_repr": witnessed_value_repr,
            "get_succeeded": get_succeeded,
            "traced_route": traced,
            "witnessed_route": witnessed_route,
            "routes_agree": routes_agree,
            "error": error_msg,
            "timestamp": time.monotonic(),
        }
        self._route_evidence.append(record)
        return record

    def witness_data_descriptor_priority(
        self,
        cls: type,
        attr_name: str,
    ) -> dict[str, Any]:
        """Verify that a data descriptor takes priority over instance ``__dict__``.

        Creates a new instance of *cls*, directly injects a value into
        ``instance.__dict__[attr_name]``, then accesses ``instance.attr_name``
        and checks whether the data descriptor's ``__get__`` was invoked
        (i.e., whether the data descriptor's value was returned rather than
        the injected value).

        Parameters
        ----------
        cls:
            A class with a data descriptor at *attr_name*.
        attr_name:
            The attribute name to test.

        Returns
        -------
        dict[str, Any]
            Evidence dict with: ``data_descriptor_won``, ``injected_value``,
            ``returned_value_repr``, ``error``.
        """
        sentinel = object()  # Unique value that the descriptor should ignore.
        error_msg: str | None = None
        data_descriptor_won = False
        returned_value_repr = "N/A"

        try:
            instance = object.__new__(cls)
        except TypeError as exc:
            return {
                "witness_id": self._witness_id,
                "class_name": cls.__qualname__,
                "attr_name": attr_name,
                "data_descriptor_won": False,
                "injected_value": repr(sentinel),
                "returned_value_repr": "N/A",
                "error": f"could not create instance: {exc!r}",
            }

        # Inject directly into instance dict if possible.
        inst_dict = getattr(instance, "__dict__", None)
        if inst_dict is not None:
            inst_dict[attr_name] = sentinel

        try:
            returned = getattr(instance, attr_name)
            returned_value_repr = repr(returned)[:120]
            # If the data descriptor took priority, the returned value should
            # NOT be our sentinel object.
            data_descriptor_won = returned is not sentinel
        except AttributeError as exc:
            error_msg = str(exc)

        evidence = {
            "witness_id": self._witness_id,
            "class_name": cls.__qualname__,
            "attr_name": attr_name,
            "data_descriptor_won": data_descriptor_won,
            "injected_value": repr(sentinel),
            "returned_value_repr": returned_value_repr,
            "error": error_msg,
        }
        self._route_evidence.append(evidence)
        return evidence

    def witness_non_data_descriptor_override(
        self,
        cls: type,
        attr_name: str,
        instance: Any,
    ) -> dict[str, Any]:
        """Verify that instance ``__dict__`` overrides a non-data descriptor.

        Injects a sentinel value directly into ``instance.__dict__[attr_name]``
        and checks that accessing ``instance.attr_name`` returns the injected
        sentinel rather than the non-data descriptor's value.

        Parameters
        ----------
        cls:
            The class that defines a non-data descriptor at *attr_name*.
        attr_name:
            The attribute name to test.
        instance:
            An existing instance of *cls* (or a compatible type).

        Returns
        -------
        dict[str, Any]
            Evidence dict with: ``instance_dict_won``, ``injected_value``,
            ``returned_value_repr``, ``error``.
        """
        sentinel = object()
        error_msg: str | None = None
        instance_dict_won = False
        returned_value_repr = "N/A"

        inst_dict = getattr(instance, "__dict__", None)
        if inst_dict is None:
            return {
                "witness_id": self._witness_id,
                "class_name": cls.__qualname__,
                "attr_name": attr_name,
                "instance_dict_won": False,
                "injected_value": repr(sentinel),
                "returned_value_repr": "N/A",
                "error": "instance has no __dict__ (probably uses __slots__)",
            }

        # Inject the sentinel.
        original = inst_dict.pop(attr_name, _SENTINEL := object())
        inst_dict[attr_name] = sentinel

        try:
            returned = getattr(instance, attr_name)
            returned_value_repr = repr(returned)[:120]
            instance_dict_won = returned is sentinel
        except AttributeError as exc:
            error_msg = str(exc)
        finally:
            # Restore original state.
            del inst_dict[attr_name]
            if original is not _SENTINEL:  # type: ignore[has-type]
                inst_dict[attr_name] = original

        evidence = {
            "witness_id": self._witness_id,
            "class_name": cls.__qualname__,
            "attr_name": attr_name,
            "instance_dict_won": instance_dict_won,
            "injected_value": repr(sentinel),
            "returned_value_repr": returned_value_repr,
            "error": error_msg,
        }
        self._route_evidence.append(evidence)
        return evidence

    def witness_get_call(
        self,
        descriptor: Any,
        instance: Any,
        owner: type,
    ) -> dict[str, Any]:
        """Directly call ``descriptor.__get__(instance, owner)`` and witness
        the return value.

        For function descriptors (plain Python functions), ``__get__``
        returns a *bound method*.  This witness checks that invariant and
        records the result.

        Parameters
        ----------
        descriptor:
            The descriptor to call ``__get__`` on.
        instance:
            The instance to pass as the first argument to ``__get__``.
        owner:
            The owner class to pass as the second argument.

        Returns
        -------
        dict[str, Any]
            Evidence dict with: ``succeeded``, ``return_type``,
            ``is_bound_method``, ``return_repr``, ``error``.
        """
        succeeded = False
        return_repr = "N/A"
        return_type = "N/A"
        is_bound_method = False
        error_msg: str | None = None

        if not hasattr(descriptor, "__get__"):
            return {
                "witness_id": self._witness_id,
                "succeeded": False,
                "return_type": "N/A",
                "is_bound_method": False,
                "return_repr": "N/A",
                "error": "descriptor has no __get__",
            }

        try:
            result = descriptor.__get__(instance, owner)
            return_repr = repr(result)[:120]
            return_type = type(result).__qualname__
            is_bound_method = isinstance(result, types.MethodType)
            succeeded = True
        except Exception as exc:
            error_msg = repr(exc)

        evidence = {
            "witness_id": self._witness_id,
            "descriptor_type": type(descriptor).__qualname__,
            "instance_type": type(instance).__qualname__,
            "owner": owner.__qualname__,
            "succeeded": succeeded,
            "return_type": return_type,
            "is_bound_method": is_bound_method,
            "return_repr": return_repr,
            "error": error_msg,
        }
        self._witnessed_lookups.append(evidence)
        return evidence

    def witness_set_name_protocol(self, cls: type) -> dict[str, Any]:
        """Witness ``__set_name__`` invocations during class construction.

        ``__set_name__`` is called by ``type.__new__`` for each descriptor
        in the class body immediately after class creation.  This witness
        inspects which descriptors in *cls*'s own ``__dict__`` define
        ``__set_name__`` and checks whether the method was actually called
        by looking for an ``_owner`` or ``_name`` attribute set by the
        descriptor.

        Parameters
        ----------
        cls:
            The class to inspect.

        Returns
        -------
        dict[str, Any]
            Evidence dict with list of ``set_name_reports``.
        """
        reports: list[dict[str, Any]] = []
        for attr_name, value in cls.__dict__.items():
            if not hasattr(value, "__set_name__"):
                continue
            # Check if the descriptor stored the name/owner (common pattern).
            stored_name = getattr(value, "_name", None) or getattr(value, "name", None)
            stored_owner = getattr(value, "_owner", None) or getattr(value, "owner", None)
            was_called_heuristic = (
                stored_name == attr_name or stored_owner is cls
            )
            reports.append(
                {
                    "attr_name": attr_name,
                    "descriptor_type": type(value).__qualname__,
                    "has_set_name": True,
                    "set_name_was_called_heuristic": was_called_heuristic,
                    "stored_name": repr(stored_name),
                    "stored_owner": repr(stored_owner),
                }
            )

        evidence = {
            "witness_id": self._witness_id,
            "class_name": cls.__qualname__,
            "set_name_reports": reports,
            "total_with_set_name": len(reports),
        }
        self._witnessed_lookups.append(evidence)
        return evidence

    def detect_protocol_violation(self, descriptor: Any) -> bool:
        """Check whether *descriptor* violates the descriptor protocol.

        A violation occurs when:
        * ``__set__`` or ``__delete__`` is defined but ``__get__`` raises
          unexpectedly on class access.
        * ``__delete__`` is defined but ``__get__`` is absent (malformed).

        Records any violations in ``_protocol_violations``.

        Parameters
        ----------
        descriptor:
            The descriptor to check.

        Returns
        -------
        bool
            ``True`` if a violation was detected, ``False`` otherwise.
        """
        violations: list[str] = []

        has_get = hasattr(descriptor, "__get__")
        has_set = hasattr(descriptor, "__set__")
        has_del = hasattr(descriptor, "__delete__")

        # __delete__ without __get__ is malformed.
        if has_del and not has_get:
            violations.append(
                "__delete__ defined without __get__ (malformed data descriptor)"
            )

        # Test __get__ invoked with None instance (class-level access).
        if has_get:
            try:
                _ = descriptor.__get__(None, type(descriptor))
            except Exception as exc:
                violations.append(
                    f"__get__(None, type) raised {type(exc).__name__}: {exc}"
                )

        if violations:
            record = {
                "witness_id": self._witness_id,
                "descriptor_type": type(descriptor).__qualname__,
                "violations": violations,
                "timestamp": time.monotonic(),
            }
            self._protocol_violations.append(record)
            return True

        return False

    def collect_evidence(self) -> dict[str, Any]:
        """Return the complete evidence bundle collected by this witness.

        Returns
        -------
        dict[str, Any]
            Bundle with: ``witness_id``, ``witnessed_lookups``,
            ``route_evidence``, ``protocol_violations``, ``summary``.
        """
        return {
            "witness_id": self._witness_id,
            "channel": _ANALYSIS_CHANNEL,
            "witnessed_lookups": list(self._witnessed_lookups),
            "route_evidence": list(self._route_evidence),
            "protocol_violations": list(self._protocol_violations),
            "summary": {
                "total_witnessed_lookups": len(self._witnessed_lookups),
                "total_route_evidence": len(self._route_evidence),
                "total_protocol_violations": len(self._protocol_violations),
                "has_violations": bool(self._protocol_violations),
            },
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "DescriptorLookupRouteTaggedCoordinator",
    "DescriptorLookupRouteTaggedAnalyzer",
    "DescriptorLookupRouteTaggedWitness",
    # Helper functions
    "is_data_descriptor",
    "is_non_data_descriptor",
    "descriptor_kind",
    "mro_descriptor_search",
    "instance_dict_lookup",
    "lookup_route_priority",
    # Constants
    "_ANALYSIS_CHANNEL",
    "_DESCRIPTOR_PROTOCOL",
    "_DATA_DESCRIPTOR_DUNDERS",
    "_NON_DATA_DESCRIPTOR_DUNDERS",
    "_LOOKUP_ROUTE_PRIORITY",
]

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    # ------------------------------------------------------------------
    # Define a realistic data descriptor: ValidatedInt
    # Validates that the value is an integer within [lo, hi].
    # ------------------------------------------------------------------

    class ValidatedInt:
        """Data descriptor that enforces an integer range constraint.

        Demonstrates the full data descriptor protocol: ``__get__``,
        ``__set__``, ``__delete__``, and ``__set_name__``.
        """

        def __init__(self, lo: int, hi: int) -> None:
            self.lo = lo
            self.hi = hi
            self._name: str = ""    # set by __set_name__
            self._owner: type | None = None  # set by __set_name__

        def __set_name__(self, owner: type, name: str) -> None:
            self._name = name
            self._owner = owner

        def __get__(self, obj: Any, objtype: type | None = None) -> Any:
            if obj is None:
                return self  # class-level access returns the descriptor itself
            return obj.__dict__.get(self._name, 0)

        def __set__(self, obj: Any, value: Any) -> None:
            if not isinstance(value, int):
                raise TypeError(f"{self._name} must be int, got {type(value).__name__}")
            if not (self.lo <= value <= self.hi):
                raise ValueError(f"{self._name} must be in [{self.lo}, {self.hi}]")
            obj.__dict__[self._name] = value

        def __delete__(self, obj: Any) -> None:
            obj.__dict__.pop(self._name, None)

    # ------------------------------------------------------------------
    # Define a non-data descriptor: LazyComputed
    # Computes a value on first access and caches it in the instance dict.
    # ------------------------------------------------------------------

    class LazyComputed:
        """Non-data descriptor that caches a computed value in instance dict."""

        def __init__(self, func: Any) -> None:
            self._func = func
            self._name: str = func.__name__

        def __set_name__(self, owner: type, name: str) -> None:
            self._name = name

        def __get__(self, obj: Any, objtype: type | None = None) -> Any:
            if obj is None:
                return self
            # Compute and cache in instance dict — future access bypasses descriptor.
            value = self._func(obj)
            obj.__dict__[self._name] = value
            return value

    # ------------------------------------------------------------------
    # Define a class that uses both descriptors.
    # ------------------------------------------------------------------

    class Sensor:
        """Example class with a ValidatedInt data descriptor and a
        LazyComputed non-data descriptor."""

        reading = ValidatedInt(0, 1023)

        @LazyComputed
        def calibrated(self) -> float:  # type: ignore[override]
            """Computed lazily on first access."""
            return self.reading * 3.14159 / 1023.0

        def __init__(self, reading: int) -> None:
            self.reading = reading

    # ------------------------------------------------------------------
    # Exercise all three classes.
    # ------------------------------------------------------------------

    print("=" * 70)
    print("descriptor_lookup_route_tagged_att.py  —  smoke test")
    print("=" * 70)

    # 1. Coordinator
    print("\n--- DescriptorLookupRouteTaggedCoordinator ---")
    coordinator = DescriptorLookupRouteTaggedCoordinator()

    desc_id = coordinator.register_descriptor(Sensor.reading, Sensor, "reading")
    print(f"Registered descriptor id: {desc_id}")

    sensor = Sensor(512)
    route_record = coordinator.simulate_lookup_route(sensor, "reading")
    print("Simulated route for sensor.reading:")
    pprint.pprint(route_record)

    tag = coordinator.tag_route(
        route_record["route"],
        "reading",
        "Sensor",
        route_record["descriptor_kind"],
    )
    print("Route tag:")
    pprint.pprint(tag)

    morphism = coordinator.lookup_morphism(Sensor, "reading", route_record["route"])
    print("Lookup morphism:")
    pprint.pprint(morphism)

    coord = coordinator.descriptor_coordinate(Sensor.reading, Sensor, "reading")
    print(f"Coordinate: {coord!r}")

    conflicts = coordinator.check_descriptor_conflict(Sensor, "reading")
    print(f"Conflicts: {conflicts}")

    print("Coordinator summary:")
    pprint.pprint(coordinator.summary())

    # 2. Analyzer
    print("\n--- DescriptorLookupRouteTaggedAnalyzer ---")
    analyzer = DescriptorLookupRouteTaggedAnalyzer()

    sample_source = textwrap.dedent("""\
        class BoundedInt:
            def __get__(self, obj, objtype=None):
                return obj.__dict__.get(self.name, 0) if obj else self
            def __set__(self, obj, value):
                obj.__dict__[self.name] = int(value)
            def __set_name__(self, owner, name):
                self.name = name

        class Cached:
            def __get__(self, obj, objtype=None):
                if obj is None: return self
                val = self.func(obj)
                obj.__dict__[self.name] = val
                return val
            def __set_name__(self, owner, name):
                self.name = name

        class Widget:
            width = BoundedInt()
            height = BoundedInt()
    """)
    source_result = analyzer.analyze_source(sample_source, "sample")
    print("AST analysis result:")
    pprint.pprint(source_result)

    live_profile = analyzer.analyze_live_descriptor(Sensor.reading)
    print("Live descriptor profile for ValidatedInt:")
    pprint.pprint(live_profile)

    class_descs = analyzer.analyze_class_descriptors(Sensor)
    print("Class descriptor analysis for Sensor:")
    pprint.pprint(class_descs)

    trace = analyzer.trace_attribute_lookup(sensor, "reading")
    print("Attribute lookup trace for sensor.reading:")
    pprint.pprint(trace)

    prop_source_class_sensor = analyzer.analyze_property_descriptors(Sensor)
    print("Property descriptors in Sensor:", prop_source_class_sensor)

    # Compare lookup routes between two classes.
    class SensorSubclass(Sensor):
        pass

    comparison = analyzer.compare_lookup_routes(Sensor, SensorSubclass, "reading")
    print("Route comparison Sensor vs SensorSubclass:")
    pprint.pprint(comparison)

    judgment = analyzer.emit_descriptor_judgment(
        Sensor.reading, Sensor, "reading", "data_descriptor"
    )
    print("Descriptor judgment:")
    pprint.pprint(judgment)

    # 3. Witness
    print("\n--- DescriptorLookupRouteTaggedWitness ---")
    witness = DescriptorLookupRouteTaggedWitness()

    proto_evidence = witness.witness_descriptor_protocol(
        Sensor.reading, Sensor, "reading"
    )
    print("Protocol evidence for ValidatedInt:")
    pprint.pprint(proto_evidence)

    lookup_witness = witness.witness_lookup_route(sensor, "reading")
    print("Lookup route witness for sensor.reading:")
    pprint.pprint(lookup_witness)

    priority_evidence = witness.witness_data_descriptor_priority(Sensor, "reading")
    print("Data descriptor priority evidence:")
    pprint.pprint(priority_evidence)

    # Non-data descriptor: LazyComputed for 'calibrated'.
    lazy_desc = Sensor.__dict__.get("calibrated")
    if lazy_desc is not None:
        override_evidence = witness.witness_non_data_descriptor_override(
            Sensor, "calibrated", sensor
        )
        print("Non-data descriptor override evidence:")
        pprint.pprint(override_evidence)

        get_call_evidence = witness.witness_get_call(lazy_desc, sensor, Sensor)
        print("__get__ call evidence:")
        pprint.pprint(get_call_evidence)

    set_name_evidence = witness.witness_set_name_protocol(Sensor)
    print("__set_name__ evidence:")
    pprint.pprint(set_name_evidence)

    has_violation = witness.detect_protocol_violation(Sensor.reading)
    print(f"Protocol violation detected: {has_violation}")

    bundle = witness.collect_evidence()
    print("Evidence bundle summary:")
    pprint.pprint(bundle["summary"])

    print("\n[smoke test complete]")
