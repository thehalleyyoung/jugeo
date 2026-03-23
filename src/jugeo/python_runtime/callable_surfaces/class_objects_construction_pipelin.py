from __future__ import annotations

r"""Class objects: construction pipeline (theory2.tex Ch16).

Overview
--------
This module studies the **class-construction pipeline** — the ordered sequence
of coordination obligations that Python executes whenever a ``class`` statement
is evaluated or :func:`type` (or a custom metaclass) is called directly.

Theory alignment (theory2.tex Ch16)
-------------------------------------
Class creation in Python proceeds through a well-defined pipeline that can be
read as a *sequence of coordination morphisms* in the sense of theory2.tex Ch16:

1. **Metaclass resolution** — the runtime determines the most-derived
   metaclass ``M`` compatible with all bases.  In sheaf language this is the
   *amalgamation* of the metaclass sections over the base-class covering
   family.

2. **Namespace preparation** — ``M.__prepare__(name, bases, **kwargs)``
   returns the *stalk* of the class namespace at the candidate class
   coordinate.  For plain ``type`` this is an ordinary ``dict``; for
   metaclasses like ``ABCMeta`` or ``EnumMeta`` it is a specialised mapping.

3. **Class body execution** — the class body is executed as a code object
   inside the prepared namespace, populating it with method definitions, class
   variables, and descriptor objects.

4. **type.__new__ call** — ``M.__new__(M, name, bases, namespace)`` allocates
   the class object, runs ``__set_name__`` on each descriptor, and calls
   ``__init_subclass__`` on each base.

5. **type.__init__ call** — ``M.__init__(cls, name, bases, namespace)``
   performs any additional initialisation required by the metaclass.

6. **__init_subclass__ propagation** — each base class's
   ``__init_subclass__`` hook is invoked, propagating the construction event
   upward through the MRO.

7. **__set_name__ on descriptors** — each descriptor defined in the class
   body has its ``__set_name__(owner, name)`` called so it can record its
   attribute name.

This seven-stage pipeline is the central object studied in this module.  The
three classes :class:`ClassObjectsConstructionPipelineCoordinator`,
:class:`ClassObjectsConstructionPipelineAnalyzer`, and
:class:`ClassObjectsConstructionPipelineWitness` cover coordination,
analysis, and witnessing respectively.

Architecture
------------
:class:`ClassObjectsConstructionPipelineCoordinator`
    Maintains a registry of class objects that have been seen, records the
    metaclass map, caches MRO lists, and exposes helpers for computing
    *construction morphisms* between base and derived classes.

:class:`ClassObjectsConstructionPipelineAnalyzer`
    Combines static (AST-level) and live (introspection) analysis.  Can parse
    source text to find class definitions and analyse a live ``type`` object in
    detail.

:class:`ClassObjectsConstructionPipelineWitness`
    Produces *evidence bundles* that attest to facts about class construction:
    MRO well-formedness, metaclass accessibility, ``__init__`` contract
    satisfaction, and subclass relationships.

Copilot integration
-------------------
This module was scaffolded with copilot assistance (analysis channel
``copilot-s02-class-objects-construction-pipeline``).  Construction-pipeline
traces and metaclass-conflict detections are proposed at
``TrustLevel.ORACLE_PROPOSED`` and promoted to
``TrustLevel.RUNTIME_WITNESSED`` once exercised by the witness layer.
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo geometry imports with stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateObject,
        CoordinateKind,
        CoordinateMorphism,
        MorphismKind,
        Site,
        SiteBuilder,
    )
except Exception:
    import enum as _enum_geo

    class CoordinateKind(_enum_geo.Enum):  # type: ignore[no-redef]
        """Stub for CoordinateKind."""

        MODULE = "module"
        FUNCTION = "function"
        INTERFACE = "interface"
        TEST = "test"
        THEOREM = "theorem"
        REGION = "region"

    class MorphismKind(_enum_geo.Enum):  # type: ignore[no-redef]
        """Stub for MorphismKind."""

        RESTRICTION = "restriction"
        INCLUSION = "inclusion"
        TRANSPORT = "transport"
        REFINEMENT = "refinement"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub for CoordinateObject."""

        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: Mapping[str, Any] = field(default_factory=dict)

    class CoordinateMorphism:  # type: ignore[no-redef]
        """Stub for CoordinateMorphism."""

        def __init__(self, source: str, target: str, reason: str = "") -> None:
            self.source = source
            self.target = target
            self.reason = reason

    class Site:  # type: ignore[no-redef]
        """Stub for Site."""

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub for SiteBuilder."""

# ---------------------------------------------------------------------------
# Jugeo judgment imports with stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        PropositionKind,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
    )
except Exception:
    import enum as _enum_jdg

    class TrustLevel(_enum_jdg.IntEnum):  # type: ignore[no-redef]
        """Stub for TrustLevel."""

        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class JudgmentStatus(_enum_jdg.Enum):  # type: ignore[no-redef]
        """Stub for JudgmentStatus."""

        OPEN = "open"
        SETTLED = "settled"
        CONTESTED = "contested"
        VACUOUS = "vacuous"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        """Stub for Proposition."""

        text: str = ""
        kind: str = "assertion"

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        """Stub for Judgment."""

        proposition: Any = None
        trust_level: Any = TrustLevel.UNVERIFIED
        status: Any = JudgmentStatus.OPEN
        evidence: tuple[Any, ...] = ()
        metadata: Mapping[str, Any] = field(default_factory=dict)

# ---------------------------------------------------------------------------
# Jugeo callable_surfaces.models imports with stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.callable_surfaces.models import (
        CallableSurface,
        ParameterSpec,
        SignatureRecord,
        ClassConstruction,
    )
except Exception:
    @dataclass(frozen=True, slots=True)
    class CallableSurface:  # type: ignore[no-redef]
        """Stub for CallableSurface."""

        qualname: str = ""
        module: str = ""
        parameters: tuple[Any, ...] = ()
        return_annotation: str = "Any"
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class ParameterSpec:  # type: ignore[no-redef]
        """Stub for ParameterSpec."""

        name: str = ""
        annotation: str = "Any"
        has_default: bool = False
        kind: str = "POSITIONAL_OR_KEYWORD"

    @dataclass(frozen=True, slots=True)
    class SignatureRecord:  # type: ignore[no-redef]
        """Stub for SignatureRecord."""

        qualname: str = ""
        parameters: tuple[Any, ...] = ()
        return_annotation: str = "Any"

    @dataclass(frozen=True, slots=True)
    class ClassConstruction:  # type: ignore[no-redef]
        """Stub for ClassConstruction."""

        qualname: str = ""
        module: str = ""
        mro: tuple[str, ...] = ()
        metaclass_name: str = "type"
        has_init: bool = False
        has_new: bool = False

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ANALYSIS_CHANNEL: str = "copilot-s02-class-objects-construction-pipeline"

_CONSTRUCTION_STAGES: tuple[str, ...] = (
    "metaclass_resolution",
    "namespace_preparation",
    "class_body_execution",
    "type_call",
    "object_allocation",
    "__init__",
    "__init_subclass__",
)

_DUNDER_CONSTRUCTION: frozenset[str] = frozenset({
    "__new__",
    "__init__",
    "__init_subclass__",
    "__class_getitem__",
    "__set_name__",
    "__post_init__",
})

_METACLASS_DUNDERS: frozenset[str] = frozenset({
    "__prepare__",
    "__new__",
    "__init__",
    "__call__",
})

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def class_id(cls: type) -> str:
    """Return a stable, human-readable identifier for *cls*.

    The identifier is derived from the class's ``__qualname__``,
    ``__module__``, and the result of ``id(cls)`` (the object's memory
    address).  Using all three ensures uniqueness even when two classes share
    the same qualname in different modules, or when a class is re-created at
    the same memory address.

    Parameters
    ----------
    cls:
        Any Python ``type`` object (including metaclasses).

    Returns
    -------
    str
        A hex-encoded SHA-256 digest truncated to 16 characters, prefixed
        with the simple class name for readability.

    Examples
    --------
    >>> class_id(int)
    'int_...'  # 16-char hex suffix
    >>> class_id(str) != class_id(int)
    True
    """
    qualname = getattr(cls, "__qualname__", repr(cls))
    module = getattr(cls, "__module__", "<unknown>")
    raw = f"{module}.{qualname}#{id(cls)}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    simple_name = getattr(cls, "__name__", "unknown")
    return f"{simple_name}_{digest}"


def mro_distance(base_cls: type, derived_cls: type) -> int:
    """Return the distance from *derived_cls* to *base_cls* along the MRO.

    The distance is defined as the zero-based index of *base_cls* in
    ``derived_cls.__mro__``.  A distance of ``0`` means *derived_cls* is
    *base_cls* itself.  Returns ``-1`` if *base_cls* is not in the MRO at
    all.

    Parameters
    ----------
    base_cls:
        The ancestor class whose position is sought.
    derived_cls:
        The class whose ``__mro__`` is searched.

    Returns
    -------
    int
        Index of *base_cls* in ``derived_cls.__mro__``, or ``-1``.

    Examples
    --------
    >>> class A: pass
    >>> class B(A): pass
    >>> mro_distance(A, B)
    1
    >>> mro_distance(object, B)
    2
    >>> mro_distance(int, B)
    -1
    """
    try:
        mro = derived_cls.__mro__
    except AttributeError:
        return -1
    for idx, cls in enumerate(mro):
        if cls is base_cls:
            return idx
    return -1


def is_default_new(cls: type) -> bool:
    """Return ``True`` if *cls* uses the default ``object.__new__``.

    A class uses the default ``__new__`` when no class in its MRO (other
    than ``object`` itself) defines a custom ``__new__``.  This function
    inspects ``cls.__new__`` directly and compares it to
    ``object.__new__``.

    Parameters
    ----------
    cls:
        The class to test.

    Returns
    -------
    bool
        ``True`` iff ``cls.__new__`` resolves to ``object.__new__``.

    Examples
    --------
    >>> is_default_new(int)
    False
    >>> class Plain: pass
    >>> is_default_new(Plain)
    True
    """
    try:
        return cls.__new__ is object.__new__
    except AttributeError:
        return True


def is_default_init(cls: type) -> bool:
    """Return ``True`` if *cls* uses the default ``object.__init__``.

    Parameters
    ----------
    cls:
        The class to test.

    Returns
    -------
    bool
        ``True`` iff ``cls.__init__`` resolves to ``object.__init__``.

    Examples
    --------
    >>> is_default_init(int)
    False
    >>> class Plain: pass
    >>> is_default_init(Plain)
    True
    """
    try:
        return cls.__init__ is object.__init__
    except AttributeError:
        return True


def get_own_methods(cls: type) -> dict[str, Any]:
    """Return the methods defined **directly** in *cls*'s own ``__dict__``.

    Unlike :func:`vars`, which returns the full ``__dict__`` including
    non-callable entries, this helper filters to callables (functions,
    classmethods, staticmethods, properties).

    Parameters
    ----------
    cls:
        The class whose own methods are sought.

    Returns
    -------
    dict[str, Any]
        Mapping ``{name: raw_dict_value}`` for each entry in
        ``cls.__dict__`` that is a callable or descriptor.

    Examples
    --------
    >>> class A:
    ...     def foo(self): pass
    ...     x = 42
    >>> get_own_methods(A)
    {'foo': <function A.foo ...>}
    """
    result: dict[str, Any] = {}
    try:
        own_dict = vars(cls)
    except TypeError:
        return result
    for name, val in own_dict.items():
        if callable(val) or isinstance(val, (classmethod, staticmethod, property)):
            result[name] = val
    return result


def count_overrides(base_cls: type, derived_cls: type) -> int:
    """Count how many methods in *derived_cls* override methods from *base_cls*.

    A method is counted as an override if:
    - its name appears in ``base_cls.__dict__``, and
    - it also appears in ``derived_cls.__dict__`` (not just inherited).

    Parameters
    ----------
    base_cls:
        The ancestor class.
    derived_cls:
        The class that potentially overrides methods.

    Returns
    -------
    int
        Number of methods in ``derived_cls.__dict__`` that have the same
        name as a method in ``base_cls.__dict__``.

    Examples
    --------
    >>> class A:
    ...     def foo(self): pass
    ...     def bar(self): pass
    >>> class B(A):
    ...     def foo(self): pass  # override
    >>> count_overrides(A, B)
    1
    """
    try:
        base_methods = set(get_own_methods(base_cls).keys())
        derived_methods = set(get_own_methods(derived_cls).keys())
    except Exception:
        return 0
    return len(base_methods & derived_methods)


def describe_inheritance(cls: type) -> str:
    """Return a human-readable description of *cls*'s inheritance.

    The returned string has the form ``"ClassName(Base1, Base2) with
    metaclass MetaName"``.  If the metaclass is plain ``type``, the suffix
    is omitted.

    Parameters
    ----------
    cls:
        Any ``type`` object.

    Returns
    -------
    str
        Readable inheritance string.

    Examples
    --------
    >>> class A: pass
    >>> class B(A): pass
    >>> describe_inheritance(B)
    'B(A)'
    >>> describe_inheritance(int)
    'int(object)'
    """
    name = getattr(cls, "__name__", repr(cls))
    try:
        bases = cls.__bases__
        base_names = ", ".join(b.__name__ for b in bases)
    except AttributeError:
        base_names = ""
    metacls = type(cls)
    meta_part = "" if metacls is type else f" with metaclass {metacls.__name__}"
    return f"{name}({base_names}){meta_part}"


# ---------------------------------------------------------------------------
# Class 1: Coordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClassObjectsConstructionPipelineCoordinator:
    """Coordinates registration and analysis of class construction pipelines.

    This class maintains a registry of class objects that have been presented
    to it, recording metaclass relationships, MRO lists, and pipeline stage
    logs.  It exposes helpers for computing *construction morphisms* — the
    structured relationship between a base class and a derived class in terms
    of the construction pipeline.

    Theory alignment (theory2.tex Ch16 §2)
    ----------------------------------------
    Each registered class ``C`` is assigned a *class coordinate* in the
    semantic site.  The construction morphism from ``Base`` to ``Derived`` is
    the site morphism that witnesses the extension relationship, carrying
    information about MRO distance, method override count, and which
    construction dunders are overridden.

    Parameters
    ----------
    (All fields have defaults and the class is instantiated with no arguments.)

    Attributes
    ----------
    _class_registry : dict[str, dict[str, Any]]
        Maps ``class_id`` strings to class metadata dicts.
    _pipeline_log : list[dict[str, Any]]
        Ordered log of construction pipeline events recorded during
        :meth:`trace_construction_pipeline` calls.
    _metaclass_map : dict[str, str]
        Maps ``class_name`` to the name of its metaclass.
    _coordinator_id : str
        A 16-character hex UUID fragment identifying this coordinator
        instance.
    _mro_cache : dict[str, list[str]]
        Cache mapping ``class_id`` to the MRO as a list of class names.
    """

    _class_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    _pipeline_log: list[dict[str, Any]] = field(default_factory=list)
    _metaclass_map: dict[str, str] = field(default_factory=dict)
    _coordinator_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    _mro_cache: dict[str, list[str]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_class(self, cls: type) -> str:
        """Register *cls* in the coordinator registry and return its class ID.

        Extracts ``__name__``, ``__qualname__``, ``__module__``, ``__bases__``,
        ``__mro__``, and the metaclass from *cls*, stores them in
        ``_class_registry``, and records the metaclass in ``_metaclass_map``.

        Parameters
        ----------
        cls:
            Any Python ``type`` object to register.

        Returns
        -------
        str
            A stable class ID (see :func:`class_id`).

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> cid = coord.register_class(int)
        >>> cid.startswith("int_")
        True
        """
        cid = class_id(cls)
        if cid in self._class_registry:
            return cid
        metacls = type(cls)
        entry: dict[str, Any] = {
            "class_id": cid,
            "name": getattr(cls, "__name__", repr(cls)),
            "qualname": getattr(cls, "__qualname__", repr(cls)),
            "module": getattr(cls, "__module__", "<unknown>"),
            "bases": [b.__name__ for b in getattr(cls, "__bases__", ())],
            "mro": [c.__name__ for c in getattr(cls, "__mro__", (cls,))],
            "metaclass": metacls.__name__,
            "registered_at": time.time(),
        }
        self._class_registry[cid] = entry
        self._metaclass_map[entry["name"]] = metacls.__name__
        logger.debug(
            "ClassObjectsConstructionPipelineCoordinator.register_class: "
            "registered %s (id=%s, metaclass=%s)",
            entry["qualname"],
            cid,
            metacls.__name__,
        )
        return cid

    # ------------------------------------------------------------------
    # Pipeline tracing
    # ------------------------------------------------------------------

    def trace_construction_pipeline(self, cls: type) -> list[dict[str, Any]]:
        """Trace the full construction pipeline for *cls*.

        Examines *cls* to determine which stages of the construction pipeline
        are non-trivial.  For each stage in :data:`_CONSTRUCTION_STAGES`,
        produces a dict describing what happens at that stage for *cls*.

        Parameters
        ----------
        cls:
            The class whose construction pipeline is to be traced.

        Returns
        -------
        list[dict[str, Any]]
            Ordered list of stage dicts, one per element of
            :data:`_CONSTRUCTION_STAGES`.  Each dict has keys:
            ``stage``, ``active``, ``detail``, ``source_class``.

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> class Foo: pass
        >>> stages = coord.trace_construction_pipeline(Foo)
        >>> [s["stage"] for s in stages]
        ['metaclass_resolution', 'namespace_preparation', ...]
        """
        metacls = self.resolve_metaclass(cls)
        mro = self.compute_mro(cls)
        stages: list[dict[str, Any]] = []

        # Stage 1: metaclass_resolution
        has_meta_conflict = False
        seen_metas = {type(b) for b in mro[1:] if b is not object}
        if len({m for m in seen_metas if m is not type}) > 1:
            has_meta_conflict = True
        stages.append({
            "stage": "metaclass_resolution",
            "active": True,
            "detail": f"metaclass={metacls.__name__}, conflict={has_meta_conflict}",
            "source_class": metacls.__name__,
        })

        # Stage 2: namespace_preparation
        has_prepare = "__prepare__" in vars(metacls)
        stages.append({
            "stage": "namespace_preparation",
            "active": has_prepare,
            "detail": f"__prepare__ defined on metaclass: {has_prepare}",
            "source_class": metacls.__name__ if has_prepare else "type",
        })

        # Stage 3: class_body_execution
        body_size = len(vars(cls))
        stages.append({
            "stage": "class_body_execution",
            "active": True,
            "detail": f"namespace entries after body execution: {body_size}",
            "source_class": cls.__name__,
        })

        # Stage 4: type_call
        stages.append({
            "stage": "type_call",
            "active": True,
            "detail": f"{metacls.__name__}(name, bases, namespace)",
            "source_class": metacls.__name__,
        })

        # Stage 5: object_allocation (__new__)
        new_owner = None
        for c in mro:
            if "__new__" in vars(c):
                new_owner = c
                break
        is_default_new_flag = is_default_new(cls)
        stages.append({
            "stage": "object_allocation",
            "active": not is_default_new_flag,
            "detail": (
                f"__new__ from {new_owner.__name__ if new_owner else 'object'}, "
                f"default={is_default_new_flag}"
            ),
            "source_class": new_owner.__name__ if new_owner else "object",
        })

        # Stage 6: __init__
        init_owner = None
        for c in mro:
            if "__init__" in vars(c):
                init_owner = c
                break
        is_default_init_flag = is_default_init(cls)
        stages.append({
            "stage": "__init__",
            "active": not is_default_init_flag,
            "detail": (
                f"__init__ from {init_owner.__name__ if init_owner else 'object'}, "
                f"default={is_default_init_flag}"
            ),
            "source_class": init_owner.__name__ if init_owner else "object",
        })

        # Stage 7: __init_subclass__
        subclass_hooks: list[str] = []
        for base in getattr(cls, "__bases__", ()):
            for c in getattr(base, "__mro__", ()):
                if "__init_subclass__" in vars(c) and c is not object:
                    subclass_hooks.append(c.__name__)
                    break
        stages.append({
            "stage": "__init_subclass__",
            "active": bool(subclass_hooks),
            "detail": f"hooks from bases: {subclass_hooks or ['none']}",
            "source_class": subclass_hooks[0] if subclass_hooks else "object",
        })

        cid = self.register_class(cls)
        log_entry = {
            "class_id": cid,
            "class_name": cls.__name__,
            "stages": stages,
            "traced_at": time.time(),
        }
        self._pipeline_log.append(log_entry)
        return stages

    # ------------------------------------------------------------------
    # Metaclass resolution
    # ------------------------------------------------------------------

    def resolve_metaclass(self, cls: type) -> type:
        """Resolve the effective metaclass for *cls*.

        Returns ``type(cls)``, which is the metaclass.  Also checks for
        potential metaclass conflicts by examining the metaclasses of all
        bases in the MRO.

        Parameters
        ----------
        cls:
            The class whose metaclass is to be resolved.

        Returns
        -------
        type
            The metaclass of *cls*.

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> coord.resolve_metaclass(int)
        <class 'type'>
        """
        metacls = type(cls)
        try:
            bases = cls.__mro__
        except AttributeError:
            return metacls
        # Verify no metaclass conflict: each base's metaclass must be a
        # subclass of the resolved metaclass or vice versa.
        for base in bases:
            base_meta = type(base)
            if not (issubclass(base_meta, metacls) or issubclass(metacls, base_meta)):
                logger.warning(
                    "resolve_metaclass: potential conflict between %s and %s for class %s",
                    metacls.__name__,
                    base_meta.__name__,
                    cls.__name__,
                )
        return metacls

    # ------------------------------------------------------------------
    # MRO computation
    # ------------------------------------------------------------------

    def compute_mro(self, cls: type) -> list[type]:
        """Return the C3 linearisation of *cls* as a list of types.

        Uses ``cls.__mro__`` (computed by the CPython C3 algorithm) and
        caches the result by ``class_id(cls)`` to avoid repeated
        attribute lookups.

        Parameters
        ----------
        cls:
            The class whose MRO is computed.

        Returns
        -------
        list[type]
            The MRO as an ordered list of ``type`` objects, starting with
            *cls* and ending with ``object``.

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> class A: pass
        >>> class B(A): pass
        >>> coord.compute_mro(B) == [B, A, object]
        True
        """
        cid = class_id(cls)
        if cid in self._mro_cache:
            # Return from cache but reconstruct list[type] from names
            # (we actually cache list[str] for JSON-serializability but here
            # we return list[type] from live cls.__mro__)
            pass
        try:
            mro: list[type] = list(cls.__mro__)
        except AttributeError:
            mro = [cls]
        self._mro_cache[cid] = [c.__name__ for c in mro]
        return mro

    # ------------------------------------------------------------------
    # Coordinate building
    # ------------------------------------------------------------------

    def class_coordinate(self, cls: type) -> CoordinateObject:
        """Build a :class:`CoordinateObject` for *cls*.

        The coordinate components are ``("python_runtime", "callable_surfaces",
        "classes", module, qualname)``.

        Parameters
        ----------
        cls:
            The class for which the coordinate is built.

        Returns
        -------
        CoordinateObject
            A coordinate locating *cls* in the semantic site.

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> co = coord.class_coordinate(int)
        >>> "int" in co.components
        True
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        module = getattr(cls, "__module__", "<unknown>")
        components = ("python_runtime", "callable_surfaces", "classes", module, qualname)
        try:
            return CoordinateObject(
                components=components,
                kind=CoordinateKind.INTERFACE,
                support_labels=frozenset({"class", "construction_pipeline"}),
                metadata={"metaclass": type(cls).__name__},
            )
        except Exception:
            return CoordinateObject(components=components)

    # ------------------------------------------------------------------
    # Construction morphism
    # ------------------------------------------------------------------

    def construction_morphism(self, base_cls: type, derived_cls: type) -> dict[str, Any]:
        """Compute the construction morphism from *base_cls* to *derived_cls*.

        The construction morphism captures how *derived_cls* extends *base_cls*:
        the MRO distance, the number of method overrides, whether the
        construction dunders are overridden, and whether ``derived_cls`` is
        a proper subclass.

        Parameters
        ----------
        base_cls:
            The ancestor class.
        derived_cls:
            The class that extends *base_cls*.

        Returns
        -------
        dict[str, Any]
            Keys: ``is_subclass``, ``mro_distance``, ``override_count``,
            ``overridden_construction_dunders``, ``base_name``,
            ``derived_name``, ``morphism_valid``.

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> class A:
        ...     def foo(self): pass
        >>> class B(A):
        ...     def foo(self): pass
        >>> m = coord.construction_morphism(A, B)
        >>> m["is_subclass"]
        True
        >>> m["mro_distance"]
        1
        """
        is_sub = False
        try:
            is_sub = issubclass(derived_cls, base_cls)
        except TypeError:
            pass
        dist = mro_distance(base_cls, derived_cls)
        overrides = count_overrides(base_cls, derived_cls) if is_sub else 0
        overridden_dunders: list[str] = []
        for dunder in _DUNDER_CONSTRUCTION:
            if dunder in vars(derived_cls) and dunder in vars(base_cls):
                overridden_dunders.append(dunder)
            elif dunder in vars(derived_cls) and dunder not in vars(base_cls):
                overridden_dunders.append(f"{dunder}(new)")
        return {
            "base_name": getattr(base_cls, "__name__", repr(base_cls)),
            "derived_name": getattr(derived_cls, "__name__", repr(derived_cls)),
            "is_subclass": is_sub,
            "mro_distance": dist,
            "override_count": overrides,
            "overridden_construction_dunders": overridden_dunders,
            "morphism_valid": is_sub and dist >= 0,
        }

    # ------------------------------------------------------------------
    # MRO conflict detection
    # ------------------------------------------------------------------

    def find_method_resolution_order_conflicts(self, cls: type) -> list[dict[str, Any]]:
        """Check for MRO inconsistencies and diamond-inheritance patterns.

        Scans the MRO of *cls* for:
        - Classes that appear more than once (should not happen in valid Python
          but can occur with dynamic class manipulation).
        - Diamond-inheritance patterns (a class that is an ancestor of more
          than one class in the immediate bases).
        - Metaclass conflicts (bases with incompatible metaclasses).

        Parameters
        ----------
        cls:
            The class to examine.

        Returns
        -------
        list[dict[str, Any]]
            A list of conflict dicts.  Each dict has keys: ``kind``,
            ``detail``, ``involved_classes``.  Returns an empty list if no
            conflicts are found.

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> coord.find_method_resolution_order_conflicts(object)
        []
        """
        conflicts: list[dict[str, Any]] = []
        try:
            mro = list(cls.__mro__)
        except AttributeError:
            return conflicts
        # Check for duplicates in MRO
        seen: dict[type, int] = {}
        for idx, c in enumerate(mro):
            if c in seen:
                conflicts.append({
                    "kind": "mro_duplicate",
                    "detail": f"{c.__name__} appears at both index {seen[c]} and {idx}",
                    "involved_classes": [c.__name__],
                })
            else:
                seen[c] = idx
        # Check for diamond inheritance
        bases = getattr(cls, "__bases__", ())
        ancestor_sets: list[set[type]] = []
        for base in bases:
            try:
                ancestor_sets.append(set(base.__mro__))
            except AttributeError:
                ancestor_sets.append({base})
        if len(ancestor_sets) > 1:
            for i in range(len(ancestor_sets)):
                for j in range(i + 1, len(ancestor_sets)):
                    common = ancestor_sets[i] & ancestor_sets[j] - {object}
                    if common:
                        names = sorted(c.__name__ for c in common)
                        conflicts.append({
                            "kind": "diamond_inheritance",
                            "detail": (
                                f"common ancestors between {bases[i].__name__} "
                                f"and {bases[j].__name__}: {names}"
                            ),
                            "involved_classes": names,
                        })
        # Check metaclass compatibility
        metacls = type(cls)
        for base in bases:
            base_meta = type(base)
            if not (issubclass(base_meta, metacls) or issubclass(metacls, base_meta)):
                conflicts.append({
                    "kind": "metaclass_conflict",
                    "detail": (
                        f"metaclass {metacls.__name__} of {cls.__name__} "
                        f"incompatible with {base_meta.__name__} of {base.__name__}"
                    ),
                    "involved_classes": [cls.__name__, base.__name__],
                })
        return conflicts

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return coordinator statistics.

        Returns
        -------
        dict[str, Any]
            Keys: ``coordinator_id``, ``registered_classes``,
            ``pipeline_log_entries``, ``metaclass_map``, ``mro_cache_size``.

        Examples
        --------
        >>> coord = ClassObjectsConstructionPipelineCoordinator()
        >>> s = coord.summary()
        >>> "coordinator_id" in s
        True
        """
        return {
            "coordinator_id": self._coordinator_id,
            "registered_classes": len(self._class_registry),
            "pipeline_log_entries": len(self._pipeline_log),
            "metaclass_map": dict(self._metaclass_map),
            "mro_cache_size": len(self._mro_cache),
        }


# ---------------------------------------------------------------------------
# Class 2: Analyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClassObjectsConstructionPipelineAnalyzer:
    """Combines static (AST) and live introspection analysis of class construction.

    This class provides two complementary analysis paths:

    1. **Static analysis** via :meth:`analyze_source` and
       :meth:`find_class_definitions` — parse Python source code and extract
       class metadata without executing the code.

    2. **Live analysis** via :meth:`analyze_live_class` — introspect a live
       ``type`` object using ``inspect``, ``vars``, and ``type(cls)`` to
       categorise the contents of ``cls.__dict__``.

    Theory alignment (theory2.tex Ch16 §3)
    ----------------------------------------
    The analyzer corresponds to the *functor* from source text to construction
    records: it sends each syntactic ``class`` statement to its corresponding
    :class:`ClassConstruction` record.  The live analysis functor instead
    sends a live ``type`` object to the same record type, verifying that the
    two paths agree.

    Attributes
    ----------
    _coordinator : ClassObjectsConstructionPipelineCoordinator
        Underlying coordinator used for registration and MRO computation.
    _ast_cache : dict[str, ast.Module]
        Caches parsed AST modules keyed by ``module_name``.
    _class_analysis_cache : dict[str, dict[str, Any]]
        Caches live analysis results keyed by class ``__qualname__``.
    _stats : dict[str, int]
        Call counts for each analysis method.
    """

    _coordinator: ClassObjectsConstructionPipelineCoordinator = field(
        default_factory=ClassObjectsConstructionPipelineCoordinator
    )
    _ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    _class_analysis_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    _stats: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )

    # ------------------------------------------------------------------
    # Source / AST analysis
    # ------------------------------------------------------------------

    def analyze_source(self, source: str, module_name: str = "<module>") -> dict[str, Any]:
        """Analyse Python source text and extract all class definitions.

        Parses *source* into an AST, finds all :class:`ast.ClassDef` nodes
        (including nested ones), and returns a summary of each class found.

        Parameters
        ----------
        source:
            Python source code as a string.
        module_name:
            An optional name for the module, used as a cache key.

        Returns
        -------
        dict[str, Any]
            Keys: ``module_name``, ``class_count``, ``classes`` (list of
            per-class dicts from :meth:`find_class_definitions`),
            ``parse_errors`` (list of error strings).

        Examples
        --------
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> result = analyzer.analyze_source("class A:\\n    pass\\n")
        >>> result["class_count"]
        1
        """
        self._stats["analyze_source"] += 1
        parse_errors: list[str] = []
        tree: ast.Module | None = None
        if module_name in self._ast_cache:
            tree = self._ast_cache[module_name]
        else:
            try:
                tree = ast.parse(textwrap.dedent(source), filename=module_name)
                self._ast_cache[module_name] = tree
            except SyntaxError as exc:
                parse_errors.append(f"SyntaxError: {exc}")
        if tree is None:
            return {
                "module_name": module_name,
                "class_count": 0,
                "classes": [],
                "parse_errors": parse_errors,
            }
        classes = self.find_class_definitions(tree)
        return {
            "module_name": module_name,
            "class_count": len(classes),
            "classes": classes,
            "parse_errors": parse_errors,
        }

    def find_class_definitions(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Walk *tree* and return a dict for every :class:`ast.ClassDef`.

        Parameters
        ----------
        tree:
            A parsed :class:`ast.AST` (typically an :class:`ast.Module`).

        Returns
        -------
        list[dict[str, Any]]
            One dict per class, with keys: ``name``, ``bases``,
            ``keywords`` (e.g. ``{"metaclass": "MyMeta"}``), ``lineno``,
            ``decorator_names``, ``has_init``, ``has_new``, ``class_vars``,
            ``body_analysis``.

        Examples
        --------
        >>> import ast
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> src = "class Foo(Bar, metaclass=Meta):\\n    x = 1\\n    def __init__(self): pass\\n"
        >>> tree = ast.parse(src)
        >>> defs = analyzer.find_class_definitions(tree)
        >>> defs[0]["name"]
        'Foo'
        >>> defs[0]["has_init"]
        True
        """
        self._stats["find_class_definitions"] += 1
        result: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Extract base names
            base_names: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(ast.unparse(base))
                else:
                    base_names.append(ast.unparse(base))
            # Extract keywords (e.g. metaclass=...)
            keywords: dict[str, str] = {}
            for kw in node.keywords:
                if kw.arg is not None:
                    keywords[kw.arg] = ast.unparse(kw.value)
            # Decorator names
            decorator_names: list[str] = []
            for dec in node.decorator_list:
                decorator_names.append(ast.unparse(dec))
            body_analysis = self.analyze_class_body(node)
            result.append({
                "name": node.name,
                "bases": base_names,
                "keywords": keywords,
                "lineno": node.lineno,
                "decorator_names": decorator_names,
                "has_init": body_analysis["has_init"],
                "has_new": body_analysis["has_new"],
                "class_vars": body_analysis["class_vars"],
                "body_analysis": body_analysis,
            })
        return result

    def analyze_class_body(self, class_node: ast.ClassDef) -> dict[str, Any]:
        """Analyse the body of an :class:`ast.ClassDef` node.

        Categorises each statement in the class body as a method definition,
        class variable assignment, property, classmethod, or staticmethod.

        Parameters
        ----------
        class_node:
            The AST ClassDef node to analyse.

        Returns
        -------
        dict[str, Any]
            Keys: ``has_init``, ``has_new``, ``has_post_init``,
            ``methods`` (list of method names), ``class_vars`` (list of names),
            ``classmethods``, ``staticmethods``, ``properties``,
            ``dunder_methods``, ``statement_count``.

        Examples
        --------
        >>> import ast
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> node = ast.parse("class A:\\n    x = 1\\n    def foo(self): pass\\n").body[0]
        >>> body = analyzer.analyze_class_body(node)
        >>> "foo" in body["methods"]
        True
        """
        self._stats["analyze_class_body"] += 1
        methods: list[str] = []
        class_vars: list[str] = []
        classmethods: list[str] = []
        staticmethods_list: list[str] = []
        properties: list[str] = []
        dunder_methods: list[str] = []

        for stmt in class_node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = stmt.name
                methods.append(name)
                if name.startswith("__") and name.endswith("__"):
                    dunder_methods.append(name)
                dec_names = [ast.unparse(d) for d in stmt.decorator_list]
                if "classmethod" in dec_names:
                    classmethods.append(name)
                elif "staticmethod" in dec_names:
                    staticmethods_list.append(name)
                elif "property" in dec_names or any(
                    re.search(r"\.setter|\.deleter|\.getter", d) for d in dec_names
                ):
                    properties.append(name)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        class_vars.append(target.id)
            elif isinstance(stmt, ast.AnnAssign):
                if isinstance(stmt.target, ast.Name):
                    class_vars.append(stmt.target.id)

        return {
            "has_init": "__init__" in methods,
            "has_new": "__new__" in methods,
            "has_post_init": "__post_init__" in methods,
            "methods": methods,
            "class_vars": class_vars,
            "classmethods": classmethods,
            "staticmethods": staticmethods_list,
            "properties": properties,
            "dunder_methods": dunder_methods,
            "statement_count": len(class_node.body),
        }

    # ------------------------------------------------------------------
    # Live class introspection
    # ------------------------------------------------------------------

    def analyze_live_class(self, cls: type) -> dict[str, Any]:
        """Fully introspect a live ``type`` object.

        Categorises every entry in ``cls.__dict__`` into methods, class
        variables, properties, classmethods, staticmethods, descriptors, and
        dunders.

        Parameters
        ----------
        cls:
            The live class to introspect.

        Returns
        -------
        dict[str, Any]
            Keys: ``qualname``, ``module``, ``metaclass``, ``bases``,
            ``mro``, ``methods``, ``class_vars``, ``properties``,
            ``classmethods``, ``staticmethods``, ``descriptors``,
            ``dunders``, ``dict_size``, ``is_abstract``.

        Examples
        --------
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> result = analyzer.analyze_live_class(list)
        >>> "append" in result["methods"]
        True
        """
        self._stats["analyze_live_class"] += 1
        qualname = getattr(cls, "__qualname__", repr(cls))
        if qualname in self._class_analysis_cache:
            return self._class_analysis_cache[qualname]

        metacls = type(cls)
        bases = [b.__name__ for b in getattr(cls, "__bases__", ())]
        mro = [c.__name__ for c in getattr(cls, "__mro__", (cls,))]

        methods: list[str] = []
        class_vars: list[str] = []
        properties_list: list[str] = []
        classmethods_list: list[str] = []
        staticmethods_list: list[str] = []
        descriptors: list[str] = []
        dunders: list[str] = []

        try:
            own_dict = vars(cls)
        except TypeError:
            own_dict = {}

        for name, val in own_dict.items():
            if name.startswith("__") and name.endswith("__"):
                dunders.append(name)
            elif isinstance(val, property):
                properties_list.append(name)
            elif isinstance(val, classmethod):
                classmethods_list.append(name)
            elif isinstance(val, staticmethod):
                staticmethods_list.append(name)
            elif callable(val) or inspect.isfunction(val):
                methods.append(name)
            elif hasattr(val, "__get__"):
                descriptors.append(name)
            else:
                class_vars.append(name)

        is_abstract = bool(getattr(cls, "__abstractmethods__", frozenset()))
        result: dict[str, Any] = {
            "qualname": qualname,
            "module": getattr(cls, "__module__", "<unknown>"),
            "metaclass": metacls.__name__,
            "bases": bases,
            "mro": mro,
            "methods": methods,
            "class_vars": class_vars,
            "properties": properties_list,
            "classmethods": classmethods_list,
            "staticmethods": staticmethods_list,
            "descriptors": descriptors,
            "dunders": dunders,
            "dict_size": len(own_dict),
            "is_abstract": is_abstract,
        }
        self._class_analysis_cache[qualname] = result
        return result

    # ------------------------------------------------------------------
    # Init / __new__ analysis
    # ------------------------------------------------------------------

    def analyze_init_signature(self, cls: type) -> dict[str, Any]:
        """Inspect ``cls.__init__`` and return a parameter profile.

        Uses :mod:`inspect` to extract parameter names, kinds, defaults, and
        annotations.

        Parameters
        ----------
        cls:
            The class whose ``__init__`` is analysed.

        Returns
        -------
        dict[str, Any]
            Keys: ``qualname``, ``has_custom_init``, ``parameters``,
            ``required_count``, ``optional_count``, ``has_var_positional``,
            ``has_var_keyword``, ``return_annotation``.

        Examples
        --------
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> class Foo:
        ...     def __init__(self, x: int, y: str = "hi"): pass
        >>> p = analyzer.analyze_init_signature(Foo)
        >>> p["required_count"]
        1
        """
        self._stats["analyze_init_signature"] += 1
        qualname = getattr(cls, "__qualname__", repr(cls))
        has_custom = not is_default_init(cls)
        if not has_custom:
            return {
                "qualname": qualname,
                "has_custom_init": False,
                "parameters": [],
                "required_count": 0,
                "optional_count": 0,
                "has_var_positional": False,
                "has_var_keyword": False,
                "return_annotation": "None",
            }
        try:
            sig = inspect.signature(cls.__init__)
        except (ValueError, TypeError):
            return {
                "qualname": qualname,
                "has_custom_init": True,
                "parameters": [],
                "required_count": 0,
                "optional_count": 0,
                "has_var_positional": False,
                "has_var_keyword": False,
                "return_annotation": "None",
                "signature_error": "could not inspect",
            }
        params: list[dict[str, Any]] = []
        required = 0
        optional = 0
        has_var_pos = False
        has_var_kw = False
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            annotation = (
                param.annotation.__name__
                if isinstance(param.annotation, type)
                else repr(param.annotation)
                if param.annotation is not inspect.Parameter.empty
                else "Any"
            )
            has_default = param.default is not inspect.Parameter.empty
            kind_name = param.kind.name
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                has_var_pos = True
            elif param.kind == inspect.Parameter.VAR_KEYWORD:
                has_var_kw = True
            elif has_default:
                optional += 1
            else:
                required += 1
            params.append({
                "name": name,
                "annotation": annotation,
                "has_default": has_default,
                "kind": kind_name,
            })
        return_ann = sig.return_annotation
        return_str = (
            "None"
            if return_ann is inspect.Parameter.empty
            else repr(return_ann)
        )
        return {
            "qualname": qualname,
            "has_custom_init": True,
            "parameters": params,
            "required_count": required,
            "optional_count": optional,
            "has_var_positional": has_var_pos,
            "has_var_keyword": has_var_kw,
            "return_annotation": return_str,
        }

    def analyze_new_method(self, cls: type) -> dict[str, Any]:
        """Inspect ``cls.__new__`` and determine if it is overridden.

        Parameters
        ----------
        cls:
            The class to examine.

        Returns
        -------
        dict[str, Any]
            Keys: ``qualname``, ``is_default``, ``new_owner``,
            ``parameters`` (empty list if default), ``signature_error``.

        Examples
        --------
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> analyzer.analyze_new_method(object)["is_default"]
        True
        """
        self._stats["analyze_new_method"] += 1
        qualname = getattr(cls, "__qualname__", repr(cls))
        default = is_default_new(cls)
        # Find which class in the MRO owns __new__
        new_owner_name = "object"
        for c in getattr(cls, "__mro__", (cls,)):
            if "__new__" in vars(c):
                new_owner_name = c.__name__
                break
        if default:
            return {
                "qualname": qualname,
                "is_default": True,
                "new_owner": new_owner_name,
                "parameters": [],
            }
        try:
            sig = inspect.signature(cls.__new__)
            params = [
                {"name": n, "kind": p.kind.name}
                for n, p in sig.parameters.items()
                if n != "cls"
            ]
        except (ValueError, TypeError):
            params = []
        return {
            "qualname": qualname,
            "is_default": False,
            "new_owner": new_owner_name,
            "parameters": params,
        }

    # ------------------------------------------------------------------
    # Construction call simulation
    # ------------------------------------------------------------------

    def trace_construction_call(
        self, cls: type, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        """Simulate (without executing) the construction pipeline for *cls*.

        Identifies which ``__new__`` and ``__init__`` would be called based on
        the MRO, without actually instantiating anything.

        Parameters
        ----------
        cls:
            The class to simulate construction for.
        *args:
            Positional arguments that would be passed to ``cls()``.
        **kwargs:
            Keyword arguments that would be passed to ``cls()``.

        Returns
        -------
        dict[str, Any]
            Keys: ``class_name``, ``metaclass``, ``new_owner``,
            ``init_owner``, ``arg_count``, ``kwarg_keys``,
            ``construction_would_succeed`` (heuristic), ``stages``.

        Examples
        --------
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> r = analyzer.trace_construction_call(dict, a=1)
        >>> r["class_name"]
        'dict'
        """
        self._stats["trace_construction_call"] += 1
        metacls = type(cls)
        mro = list(getattr(cls, "__mro__", (cls,)))
        new_owner = "object"
        init_owner = "object"
        for c in mro:
            if "__new__" in vars(c):
                new_owner = c.__name__
                break
        for c in mro:
            if "__init__" in vars(c):
                init_owner = c.__name__
                break
        init_profile = self.analyze_init_signature(cls)
        required = init_profile.get("required_count", 0)
        has_var_pos = init_profile.get("has_var_positional", False)
        has_var_kw = init_profile.get("has_var_keyword", False)
        provided_pos = len(args)
        would_succeed = (provided_pos >= required) or has_var_pos or has_var_kw
        stages: list[dict[str, Any]] = self._coordinator.trace_construction_pipeline(cls)
        return {
            "class_name": cls.__name__,
            "metaclass": metacls.__name__,
            "new_owner": new_owner,
            "init_owner": init_owner,
            "arg_count": len(args),
            "kwarg_keys": list(kwargs.keys()),
            "construction_would_succeed": would_succeed,
            "stages": stages,
        }

    # ------------------------------------------------------------------
    # Hierarchy comparison
    # ------------------------------------------------------------------

    def compare_class_hierarchies(self, cls1: type, cls2: type) -> dict[str, Any]:
        """Compare two class hierarchies and return a diff.

        Parameters
        ----------
        cls1:
            First class to compare.
        cls2:
            Second class to compare.

        Returns
        -------
        dict[str, Any]
            Keys: ``cls1_name``, ``cls2_name``, ``common_ancestors``,
            ``mro_difference``, ``method_overrides_diff``,
            ``shared_dunders``, ``cls1_only_methods``,
            ``cls2_only_methods``.

        Examples
        --------
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> r = analyzer.compare_class_hierarchies(list, tuple)
        >>> "object" in r["common_ancestors"]
        True
        """
        self._stats["compare_class_hierarchies"] += 1
        mro1 = set(getattr(cls1, "__mro__", (cls1,)))
        mro2 = set(getattr(cls2, "__mro__", (cls2,)))
        common = mro1 & mro2
        common_names = sorted(c.__name__ for c in common)
        mro1_names = [c.__name__ for c in getattr(cls1, "__mro__", (cls1,))]
        mro2_names = [c.__name__ for c in getattr(cls2, "__mro__", (cls2,))]
        methods1 = set(get_own_methods(cls1).keys())
        methods2 = set(get_own_methods(cls2).keys())
        shared_dunders = sorted(
            (methods1 & methods2) & {n for n in methods1 | methods2 if n.startswith("__")}
        )
        cls1_only = sorted(methods1 - methods2)
        cls2_only = sorted(methods2 - methods1)
        overrides_diff: dict[str, Any] = {}
        for name in methods1 & methods2:
            v1 = get_own_methods(cls1).get(name)
            v2 = get_own_methods(cls2).get(name)
            overrides_diff[name] = {"cls1": repr(v1), "cls2": repr(v2)}
        return {
            "cls1_name": cls1.__name__,
            "cls2_name": cls2.__name__,
            "common_ancestors": common_names,
            "mro_difference": {
                "cls1_mro": mro1_names,
                "cls2_mro": mro2_names,
                "only_in_cls1": sorted(set(mro1_names) - set(mro2_names)),
                "only_in_cls2": sorted(set(mro2_names) - set(mro1_names)),
            },
            "method_overrides_diff": overrides_diff,
            "shared_dunders": shared_dunders,
            "cls1_only_methods": cls1_only,
            "cls2_only_methods": cls2_only,
        }

    # ------------------------------------------------------------------
    # Judgment emission
    # ------------------------------------------------------------------

    def emit_construction_judgment(
        self,
        cls: type,
        stage: str,
        proposition: str,
        trust_level: Any = None,
    ) -> dict[str, Any]:
        """Emit a judgment for a construction pipeline stage.

        Wraps the proposition and trust level in a ``Judgment``-compatible
        structure and logs it to the analysis channel.

        Parameters
        ----------
        cls:
            The class the judgment is about.
        stage:
            One of :data:`_CONSTRUCTION_STAGES`.
        proposition:
            Human-readable proposition text.
        trust_level:
            A :class:`TrustLevel` value.  Defaults to
            ``TrustLevel.ORACLE_PROPOSED``.

        Returns
        -------
        dict[str, Any]
            Keys: ``class_name``, ``stage``, ``proposition``,
            ``trust_level``, ``channel``, ``emitted_at``.

        Examples
        --------
        >>> analyzer = ClassObjectsConstructionPipelineAnalyzer()
        >>> j = analyzer.emit_construction_judgment(int, "metaclass_resolution", "type is metaclass of int")
        >>> j["stage"]
        'metaclass_resolution'
        """
        self._stats["emit_construction_judgment"] += 1
        if trust_level is None:
            try:
                trust_level = TrustLevel.ORACLE_PROPOSED
            except Exception:
                trust_level = 2
        entry = {
            "class_name": getattr(cls, "__name__", repr(cls)),
            "stage": stage,
            "proposition": proposition,
            "trust_level": int(trust_level) if hasattr(trust_level, "__int__") else trust_level,
            "channel": _ANALYSIS_CHANNEL,
            "emitted_at": time.time(),
        }
        logger.debug(
            "emit_construction_judgment [%s] cls=%s stage=%s: %s",
            _ANALYSIS_CHANNEL,
            entry["class_name"],
            stage,
            proposition,
        )
        return entry


# ---------------------------------------------------------------------------
# Class 3: Witness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClassObjectsConstructionPipelineWitness:
    """Produces evidence bundles attesting to class construction facts.

    The witness layer records observable facts about class construction and
    makes them available as structured evidence.  It relies on the analyzer
    for deep introspection but focuses on *attestation* rather than analysis.

    Theory alignment (theory2.tex Ch16 §5)
    ----------------------------------------
    A witness is a *section* of the evidence sheaf: it provides local data
    (evidence items) whose global consistency can be checked by the judgment
    system.  Each ``witness_*`` method produces an evidence dict that can be
    promoted to ``TrustLevel.RUNTIME_WITNESSED`` once it has been verified at
    runtime.

    Attributes
    ----------
    _analyzer : ClassObjectsConstructionPipelineAnalyzer
        The underlying analyzer used for introspection.
    _witnessed_constructions : list[dict[str, Any]]
        Log of all witnessed construction events.
    _mro_evidence : list[dict[str, Any]]
        Evidence records for MRO well-formedness checks.
    _pipeline_evidence : list[dict[str, Any]]
        Evidence records for pipeline stage checks.
    _witness_id : str
        A 16-character hex UUID fragment identifying this witness instance.
    """

    _analyzer: ClassObjectsConstructionPipelineAnalyzer = field(
        default_factory=ClassObjectsConstructionPipelineAnalyzer
    )
    _witnessed_constructions: list[dict[str, Any]] = field(default_factory=list)
    _mro_evidence: list[dict[str, Any]] = field(default_factory=list)
    _pipeline_evidence: list[dict[str, Any]] = field(default_factory=list)
    _witness_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    # ------------------------------------------------------------------
    # Construction witnessing
    # ------------------------------------------------------------------

    def witness_class_construction(self, cls: type) -> dict[str, Any]:
        """Witness that *cls* is a properly constructed Python class.

        Checks:
        - ``cls`` is an instance of ``type`` (or a metaclass).
        - ``cls.__mro__`` exists and includes ``object`` as its last element.
        - The metaclass is accessible via ``type(cls)``.
        - ``cls.__name__`` is a non-empty string.

        Parameters
        ----------
        cls:
            The class to witness.

        Returns
        -------
        dict[str, Any]
            Keys: ``class_name``, ``is_type_instance``, ``mro_ends_with_object``,
            ``metaclass_accessible``, ``name_valid``, ``evidence_valid``,
            ``witness_id``, ``witnessed_at``.

        Examples
        --------
        >>> witness = ClassObjectsConstructionPipelineWitness()
        >>> ev = witness.witness_class_construction(int)
        >>> ev["is_type_instance"]
        True
        """
        is_type_instance = isinstance(cls, type)
        try:
            mro = cls.__mro__
            mro_ends_with_object = mro[-1] is object
        except AttributeError:
            mro = ()
            mro_ends_with_object = False
        try:
            _metacls = type(cls)
            metaclass_accessible = True
        except Exception:
            metaclass_accessible = False
        name = getattr(cls, "__name__", None)
        name_valid = isinstance(name, str) and len(name) > 0
        evidence_valid = (
            is_type_instance
            and mro_ends_with_object
            and metaclass_accessible
            and name_valid
        )
        evidence: dict[str, Any] = {
            "class_name": name or repr(cls),
            "is_type_instance": is_type_instance,
            "mro_ends_with_object": mro_ends_with_object,
            "metaclass_accessible": metaclass_accessible,
            "name_valid": name_valid,
            "evidence_valid": evidence_valid,
            "witness_id": self._witness_id,
            "witnessed_at": time.time(),
        }
        self._witnessed_constructions.append(evidence)
        return evidence

    # ------------------------------------------------------------------
    # MRO witnessing
    # ------------------------------------------------------------------

    def witness_mro_linearization(self, cls: type) -> dict[str, Any]:
        """Verify the C3 linearisation of *cls*.

        Checks:
        - Each class in ``cls.__mro__`` appears exactly once.
        - The last element of ``cls.__mro__`` is ``object``.
        - Each class's bases are a subsequence of its position in the MRO.

        Parameters
        ----------
        cls:
            The class whose MRO is verified.

        Returns
        -------
        dict[str, Any]
            Keys: ``class_name``, ``mro_length``, ``no_duplicates``,
            ``ends_with_object``, ``bases_ordered``, ``mro_names``,
            ``linearization_valid``.

        Examples
        --------
        >>> witness = ClassObjectsConstructionPipelineWitness()
        >>> ev = witness.witness_mro_linearization(int)
        >>> ev["no_duplicates"]
        True
        """
        class_name = getattr(cls, "__name__", repr(cls))
        try:
            mro = list(cls.__mro__)
        except AttributeError:
            mro = [cls]
        # Check for duplicates
        seen_ids: set[int] = set()
        no_duplicates = True
        for c in mro:
            if id(c) in seen_ids:
                no_duplicates = False
                break
            seen_ids.add(id(c))
        ends_with_object = bool(mro) and mro[-1] is object
        # Check that each class's direct bases appear after it in the MRO
        mro_positions = {id(c): i for i, c in enumerate(mro)}
        bases_ordered = True
        for c in mro:
            pos_c = mro_positions.get(id(c), -1)
            for base in getattr(c, "__bases__", ()):
                pos_base = mro_positions.get(id(base), len(mro))
                if pos_base <= pos_c:
                    bases_ordered = False
                    break
            if not bases_ordered:
                break
        mro_names = [c.__name__ for c in mro]
        linearization_valid = no_duplicates and ends_with_object
        evidence: dict[str, Any] = {
            "class_name": class_name,
            "mro_length": len(mro),
            "no_duplicates": no_duplicates,
            "ends_with_object": ends_with_object,
            "bases_ordered": bases_ordered,
            "mro_names": mro_names,
            "linearization_valid": linearization_valid,
        }
        self._mro_evidence.append(evidence)
        return evidence

    # ------------------------------------------------------------------
    # Init contract witnessing
    # ------------------------------------------------------------------

    def witness_init_contract(self, cls: type, instance: Any) -> dict[str, Any]:
        """Witness that *instance* satisfies the ``__init__`` contract of *cls*.

        After construction, checks:
        - ``isinstance(instance, cls)`` holds.
        - Any attributes that ``__init__`` sets via ``self.x = ...`` patterns
          (detected heuristically from the init signature) are present on
          the instance.

        Parameters
        ----------
        cls:
            The class that was used to construct *instance*.
        instance:
            The constructed instance to check.

        Returns
        -------
        dict[str, Any]
            Keys: ``class_name``, ``is_instance``, ``expected_attrs``,
            ``present_attrs``, ``missing_attrs``, ``contract_satisfied``.

        Examples
        --------
        >>> witness = ClassObjectsConstructionPipelineWitness()
        >>> class Foo:
        ...     def __init__(self, x):
        ...         self.x = x
        >>> foo = Foo(42)
        >>> ev = witness.witness_init_contract(Foo, foo)
        >>> ev["is_instance"]
        True
        """
        class_name = getattr(cls, "__name__", repr(cls))
        try:
            is_inst = isinstance(instance, cls)
        except TypeError:
            is_inst = False
        # Heuristically find attributes set in __init__ by inspecting source
        expected_attrs: list[str] = []
        try:
            src = inspect.getsource(cls.__init__)
            # Find patterns like "self.attr = ..."
            attr_pattern = re.compile(r"self\.([A-Za-z_][A-Za-z0-9_]*)\s*=")
            expected_attrs = list(dict.fromkeys(attr_pattern.findall(src)))
        except (OSError, TypeError):
            pass
        # Check which expected attrs are present
        instance_dict: dict[str, Any] = getattr(instance, "__dict__", {}) or {}
        present_attrs = [a for a in expected_attrs if a in instance_dict]
        missing_attrs = [a for a in expected_attrs if a not in instance_dict]
        contract_satisfied = is_inst and len(missing_attrs) == 0
        evidence: dict[str, Any] = {
            "class_name": class_name,
            "is_instance": is_inst,
            "expected_attrs": expected_attrs,
            "present_attrs": present_attrs,
            "missing_attrs": missing_attrs,
            "contract_satisfied": contract_satisfied,
        }
        self._witnessed_constructions.append(evidence)
        return evidence

    # ------------------------------------------------------------------
    # Metaclass pipeline witnessing
    # ------------------------------------------------------------------

    def witness_metaclass_pipeline(self, cls: type) -> dict[str, Any]:
        """Witness the metaclass pipeline for *cls*.

        Checks:
        - ``type(cls).__name__`` is accessible.
        - Whether the metaclass defines ``__prepare__``.
        - Whether the metaclass is a subclass of ``type``.
        - Which metaclass dunders (:data:`_METACLASS_DUNDERS`) are defined.

        Parameters
        ----------
        cls:
            The class whose metaclass pipeline is witnessed.

        Returns
        -------
        dict[str, Any]
            Keys: ``class_name``, ``metaclass_name``, ``has_prepare``,
            ``metaclass_is_type_subclass``, ``defined_metaclass_dunders``,
            ``metaclass_pipeline_valid``.

        Examples
        --------
        >>> witness = ClassObjectsConstructionPipelineWitness()
        >>> ev = witness.witness_metaclass_pipeline(int)
        >>> ev["metaclass_name"]
        'type'
        """
        class_name = getattr(cls, "__name__", repr(cls))
        metacls = type(cls)
        metaclass_name = metacls.__name__
        try:
            metaclass_is_type_subclass = issubclass(metacls, type)
        except TypeError:
            metaclass_is_type_subclass = False
        has_prepare = "__prepare__" in vars(metacls)
        defined_dunders: list[str] = []
        for dunder in _METACLASS_DUNDERS:
            if dunder in vars(metacls):
                defined_dunders.append(dunder)
        metaclass_pipeline_valid = metaclass_is_type_subclass
        evidence: dict[str, Any] = {
            "class_name": class_name,
            "metaclass_name": metaclass_name,
            "has_prepare": has_prepare,
            "metaclass_is_type_subclass": metaclass_is_type_subclass,
            "defined_metaclass_dunders": defined_dunders,
            "metaclass_pipeline_valid": metaclass_pipeline_valid,
        }
        self._pipeline_evidence.append(evidence)
        return evidence

    # ------------------------------------------------------------------
    # Subclass relationship witnessing
    # ------------------------------------------------------------------

    def witness_subclass_relationship(self, base_cls: type, derived_cls: type) -> bool:
        """Witness ``issubclass(derived_cls, base_cls)`` with evidence.

        Records the result in ``_mro_evidence``.

        Parameters
        ----------
        base_cls:
            The purported ancestor.
        derived_cls:
            The purported descendant.

        Returns
        -------
        bool
            ``True`` iff ``derived_cls`` is a subclass of ``base_cls``.

        Examples
        --------
        >>> witness = ClassObjectsConstructionPipelineWitness()
        >>> witness.witness_subclass_relationship(object, int)
        True
        """
        try:
            result = issubclass(derived_cls, base_cls)
        except TypeError:
            result = False
        dist = mro_distance(base_cls, derived_cls)
        evidence: dict[str, Any] = {
            "kind": "subclass_relationship",
            "base_name": getattr(base_cls, "__name__", repr(base_cls)),
            "derived_name": getattr(derived_cls, "__name__", repr(derived_cls)),
            "is_subclass": result,
            "mro_distance": dist,
            "witnessed_at": time.time(),
        }
        self._mro_evidence.append(evidence)
        return result

    # ------------------------------------------------------------------
    # Method resolution witnessing
    # ------------------------------------------------------------------

    def witness_method_resolution(self, cls: type, method_name: str) -> dict[str, Any]:
        """Witness how *method_name* is resolved in ``cls.__mro__``.

        For each class in ``cls.__mro__``, checks whether ``method_name`` is
        in ``__dict__``.  Returns the first class where it is found.

        Parameters
        ----------
        cls:
            The class to start resolution from.
        method_name:
            The name of the method to resolve.

        Returns
        -------
        dict[str, Any]
            Keys: ``class_name``, ``method_name``, ``resolved_in``,
            ``resolution_index``, ``method_repr``, ``found``,
            ``classes_searched``.

        Examples
        --------
        >>> witness = ClassObjectsConstructionPipelineWitness()
        >>> ev = witness.witness_method_resolution(bool, "__new__")
        >>> ev["found"]
        True
        """
        class_name = getattr(cls, "__name__", repr(cls))
        mro = list(getattr(cls, "__mro__", (cls,)))
        resolved_in: str | None = None
        resolution_index: int = -1
        method_repr: str = "<not found>"
        classes_searched: list[str] = []
        for idx, c in enumerate(mro):
            classes_searched.append(c.__name__)
            if method_name in vars(c):
                resolved_in = c.__name__
                resolution_index = idx
                method_obj = vars(c)[method_name]
                method_repr = repr(method_obj)
                break
        found = resolved_in is not None
        evidence: dict[str, Any] = {
            "class_name": class_name,
            "method_name": method_name,
            "resolved_in": resolved_in,
            "resolution_index": resolution_index,
            "method_repr": method_repr,
            "found": found,
            "classes_searched": classes_searched,
        }
        self._pipeline_evidence.append(evidence)
        return evidence

    # ------------------------------------------------------------------
    # Evidence collection
    # ------------------------------------------------------------------

    def collect_evidence(self) -> dict[str, Any]:
        """Return the complete evidence bundle gathered by this witness.

        Returns
        -------
        dict[str, Any]
            Keys: ``witness_id``, ``witnessed_constructions``,
            ``mro_evidence``, ``pipeline_evidence``,
            ``total_evidence_items``, ``collected_at``.

        Examples
        --------
        >>> witness = ClassObjectsConstructionPipelineWitness()
        >>> _ = witness.witness_class_construction(list)
        >>> bundle = witness.collect_evidence()
        >>> bundle["total_evidence_items"] >= 1
        True
        """
        total = (
            len(self._witnessed_constructions)
            + len(self._mro_evidence)
            + len(self._pipeline_evidence)
        )
        return {
            "witness_id": self._witness_id,
            "witnessed_constructions": list(self._witnessed_constructions),
            "mro_evidence": list(self._mro_evidence),
            "pipeline_evidence": list(self._pipeline_evidence),
            "total_evidence_items": total,
            "collected_at": time.time(),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "ClassObjectsConstructionPipelineCoordinator",
    "ClassObjectsConstructionPipelineAnalyzer",
    "ClassObjectsConstructionPipelineWitness",
    # Helper functions
    "class_id",
    "mro_distance",
    "is_default_new",
    "is_default_init",
    "get_own_methods",
    "count_overrides",
    "describe_inheritance",
    # Constants
    "_ANALYSIS_CHANNEL",
    "_CONSTRUCTION_STAGES",
    "_DUNDER_CONSTRUCTION",
    "_METACLASS_DUNDERS",
]

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # ------------------------------------------------------------------ #
    # Build a small class hierarchy to exercise all three classes          #
    # ------------------------------------------------------------------ #

    class Vehicle:
        """Base vehicle class."""

        category: str = "vehicle"

        def __init__(self, make: str, model: str) -> None:
            self.make = make
            self.model = model

        def describe(self) -> str:
            return f"{self.make} {self.model}"

    class Car(Vehicle):
        """A four-wheeled vehicle."""

        category: str = "car"

        def __init__(self, make: str, model: str, doors: int = 4) -> None:
            super().__init__(make, model)
            self.doors = doors

        def describe(self) -> str:
            return f"{super().describe()} ({self.doors} doors)"

    class ElectricCar(Car):
        """An electric vehicle."""

        def __init__(self, make: str, model: str, range_km: float) -> None:
            super().__init__(make, model)
            self.range_km = range_km

    class Truck(Vehicle):
        """A heavy vehicle."""

        def __init__(self, make: str, model: str, payload_kg: float) -> None:
            super().__init__(make, model)
            self.payload_kg = payload_kg

    # Demonstrate diamond inheritance (both Car and Truck inherit from Vehicle)
    class ServiceVehicle(Car, Truck):  # type: ignore[misc]
        """A service vehicle that is both a Car and a Truck (diamond)."""

        def __init__(self, make: str, model: str) -> None:
            Car.__init__(self, make, model)
            self.payload_kg = 0.0

    # ------------------------------------------------------------------ #
    # Coordinator                                                          #
    # ------------------------------------------------------------------ #
    print("=== Coordinator ===")
    coord = ClassObjectsConstructionPipelineCoordinator()
    for cls in (Vehicle, Car, ElectricCar, Truck, ServiceVehicle):
        cid = coord.register_class(cls)
        print(f"  registered {cls.__name__!r}: id={cid}")

    stages = coord.trace_construction_pipeline(ElectricCar)
    print(f"\nConstruction pipeline for ElectricCar ({len(stages)} stages):")
    for s in stages:
        flag = "✓" if s["active"] else "·"
        print(f"  {flag} {s['stage']}: {s['detail']}")

    morphism = coord.construction_morphism(Vehicle, ElectricCar)
    print(f"\nMorphism Vehicle → ElectricCar: {morphism}")

    conflicts = coord.find_method_resolution_order_conflicts(ServiceVehicle)
    print(f"\nMRO conflicts for ServiceVehicle: {len(conflicts)} found")
    for c in conflicts:
        print(f"  [{c['kind']}] {c['detail']}")

    print(f"\nCoordinator summary: {coord.summary()}")

    # ------------------------------------------------------------------ #
    # Analyzer                                                             #
    # ------------------------------------------------------------------ #
    print("\n=== Analyzer ===")
    analyzer = ClassObjectsConstructionPipelineAnalyzer()

    source = textwrap.dedent("""\
        class Animal:
            sound: str = "..."

            def __init__(self, name: str) -> None:
                self.name = name

            def speak(self) -> str:
                return self.sound

        class Dog(Animal):
            sound = "woof"

            def __init__(self, name: str, breed: str) -> None:
                super().__init__(name)
                self.breed = breed
    """)
    ast_result = analyzer.analyze_source(source, "example_animals")
    print(f"AST analysis found {ast_result['class_count']} classes:")
    for cls_info in ast_result["classes"]:
        print(
            f"  {cls_info['name']} (bases={cls_info['bases']}, "
            f"has_init={cls_info['has_init']}, class_vars={cls_info['class_vars']})"
        )

    live = analyzer.analyze_live_class(ElectricCar)
    print(f"\nLive analysis of ElectricCar:")
    print(f"  metaclass: {live['metaclass']}")
    print(f"  mro: {live['mro']}")
    print(f"  methods: {live['methods']}")
    print(f"  dunders (own): {live['dunders']}")

    init_profile = analyzer.analyze_init_signature(ElectricCar)
    print(f"\nElectricCar.__init__ profile: {init_profile}")

    new_profile = analyzer.analyze_new_method(ElectricCar)
    print(f"ElectricCar.__new__ profile: {new_profile}")

    sim = analyzer.trace_construction_call(ElectricCar, "Tesla", "Model3", 500.0)
    print(f"\nConstruction simulation: new={sim['new_owner']}, init={sim['init_owner']}, "
          f"would_succeed={sim['construction_would_succeed']}")

    comparison = analyzer.compare_class_hierarchies(Car, Truck)
    print(f"\nCar vs Truck comparison:")
    print(f"  common ancestors: {comparison['common_ancestors']}")
    print(f"  Car-only methods: {comparison['cls1_only_methods']}")
    print(f"  Truck-only methods: {comparison['cls2_only_methods']}")

    judgment = analyzer.emit_construction_judgment(
        ElectricCar, "metaclass_resolution",
        "ElectricCar uses type as its metaclass"
    )
    print(f"\nJudgment emitted: {judgment}")

    # ------------------------------------------------------------------ #
    # Witness                                                              #
    # ------------------------------------------------------------------ #
    print("\n=== Witness ===")
    witness = ClassObjectsConstructionPipelineWitness()

    for cls in (Vehicle, Car, ElectricCar, Truck, ServiceVehicle):
        ev = witness.witness_class_construction(cls)
        flag = "✓" if ev["evidence_valid"] else "✗"
        print(f"  {flag} witness_class_construction({cls.__name__}): valid={ev['evidence_valid']}")

    mro_ev = witness.witness_mro_linearization(ServiceVehicle)
    print(f"\nMRO linearization evidence for ServiceVehicle:")
    print(f"  mro_names: {mro_ev['mro_names']}")
    print(f"  no_duplicates: {mro_ev['no_duplicates']}")
    print(f"  ends_with_object: {mro_ev['ends_with_object']}")
    print(f"  linearization_valid: {mro_ev['linearization_valid']}")

    tesla = ElectricCar("Tesla", "Model S", 600.0)
    contract_ev = witness.witness_init_contract(ElectricCar, tesla)
    print(f"\n__init__ contract evidence for tesla:")
    print(f"  is_instance: {contract_ev['is_instance']}")
    print(f"  expected_attrs: {contract_ev['expected_attrs']}")
    print(f"  missing_attrs: {contract_ev['missing_attrs']}")
    print(f"  contract_satisfied: {contract_ev['contract_satisfied']}")

    meta_ev = witness.witness_metaclass_pipeline(ElectricCar)
    print(f"\nMetaclass pipeline evidence for ElectricCar: {meta_ev}")

    is_sub = witness.witness_subclass_relationship(Vehicle, ElectricCar)
    print(f"\nElectricCar subclass of Vehicle: {is_sub}")

    method_ev = witness.witness_method_resolution(ElectricCar, "describe")
    print(f"\nMethod resolution for 'describe' on ElectricCar:")
    print(f"  resolved_in: {method_ev['resolved_in']} (index {method_ev['resolution_index']})")
    print(f"  classes_searched: {method_ev['classes_searched']}")

    bundle = witness.collect_evidence()
    print(f"\nEvidence bundle: {bundle['total_evidence_items']} items "
          f"(witness_id={bundle['witness_id']})")

    # ------------------------------------------------------------------ #
    # Helper functions                                                     #
    # ------------------------------------------------------------------ #
    print("\n=== Helper functions ===")
    print(f"class_id(Car)            = {class_id(Car)}")
    print(f"mro_distance(Vehicle, ElectricCar) = {mro_distance(Vehicle, ElectricCar)}")
    print(f"is_default_new(Car)      = {is_default_new(Car)}")
    print(f"is_default_init(Car)     = {is_default_init(Car)}")
    print(f"get_own_methods(Car)     = {list(get_own_methods(Car).keys())}")
    print(f"count_overrides(Vehicle, Car) = {count_overrides(Vehicle, Car)}")
    print(f"describe_inheritance(ElectricCar) = {describe_inheritance(ElectricCar)}")
    print(f"describe_inheritance(ServiceVehicle) = {describe_inheritance(ServiceVehicle)}")

    print("\nSmoke test completed successfully.")
    sys.exit(0)
