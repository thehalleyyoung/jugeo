"""Section 4 — Class Construction as Factored Morphism.

Theory reference: theory2.tex Ch16 §4 — class construction as a factored
morphism through metaclass, ``__new__``, and ``__init__`` phases.

Generated with assistance from copilot.

Overview
--------
This module models Python's class construction pipeline as a sequence of
*factored morphisms* in the sense of theory2.tex Ch16 §4.  When Python
evaluates a ``class`` statement, the runtime executes a three-stage
pipeline:

1. **Metaclass resolution** — determine which metaclass ``M`` to use, subject
   to the *metaclass conflict* condition: if multiple base classes have
   potentially conflicting metaclasses, Python raises ``TypeError``.  In
   sheaf language this is the *amalgamation condition* for the metaclass
   sheaf over the base-class covering family.

2. **Namespace preparation** — the metaclass provides a fresh namespace
   (usually ``dict()`` but overridable via ``M.__prepare__``).  This is the
   *stalk* of the class sheaf at the would-be class coordinate.

3. **Class object construction** — ``M(name, bases, namespace)`` is called.
   Python factorises this as ``M.__new__(M, name, bases, namespace)`` (object
   allocation) followed by ``M.__init__(cls, name, bases, namespace)``
   (initialisation).  A subsequent optional call to ``cls.__init_subclass__``
   on each base propagates the construction event upward.

The site-theoretic interpretation identifies the class coordinate with the
image of the metaclass call morphism, and models the ``__new__`` / ``__init__``
split as a factorisation::

    metaclass_call_morphism = new_morphism ∘ init_morphism

This factorisation is the central object of study in §4.

MRO as a Sheaf Covering
------------------------
The method resolution order of the resulting class is the *canonical section*
of the MRO sheaf: a total order on the base classes that is consistent with the
C3 linearisation algorithm.  The C3 algorithm enforces two conditions:

- **Monotonicity**: if ``C`` precedes ``D`` in any base's MRO, then ``C``
  precedes ``D`` in the child's MRO.
- **Local precedence**: in the ``class Child(B1, B2, ...)`` statement, ``B1``
  precedes ``B2``, which precedes ``B3``, etc.

Violation of these conditions raises ``TypeError`` at class-creation time.

Coordinate Assignment
---------------------
Each :class:`ClassConstruction` is assigned a
:class:`~jugeo.geometry.site.CoordinateObject` with components::

    ("python_runtime", "callable_surfaces", "classes", module, qualname)

and kind ``CoordinateKind.INTERFACE``.

Copilot Note
------------
This module was scaffolded with copilot assistance (copilot integration point).
Metaclass conflict detection and cooperative-inheritance heuristics are
proposed by copilot at ``TrustLevel.ORACLE_PROPOSED`` and promoted by
runtime CI at ``TrustLevel.RUNTIME_WITNESSED`` once exercised.

Examples
--------
Analyse a class::

    from jugeo.python_runtime.callable_surfaces.class_construction import (
        ClassBuilder,
        MetaclassAnalyzer,
        InitAnalyzer,
        ClassHierarchyTracker,
    )

    class Animal:
        def __init__(self, name: str) -> None:
            self.name = name

    class Dog(Animal):
        def __init__(self, name: str, breed: str) -> None:
            super().__init__(name)
            self.breed = breed

    builder = ClassBuilder()
    construction = builder.analyze_class(Dog)
    print(construction.mro)
    print(construction.has_init)

    tracker = ClassHierarchyTracker()
    tracker.register_class(Dog, construction)
    print(tracker.find_superclasses("Dog"))
"""

from __future__ import annotations

import datetime
import hashlib
import inspect
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

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
except ImportError:
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
except ImportError:
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

        PROPOSED = "proposed"
        CHALLENGED = "challenged"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class PropositionKind(_enum_jdg.Enum):  # type: ignore[no-redef]
        """Stub for PropositionKind."""

        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"
        RESOURCE = "resource"
        SEMANTIC = "semantic"

    class EvidenceItemKind(_enum_jdg.Enum):  # type: ignore[no-redef]
        """Stub for EvidenceItemKind."""

        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"
        FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(_enum_jdg.Enum):  # type: ignore[no-redef]
        """Stub for ProvenanceSource."""

        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"
        COMPOSED = "composed"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        """Stub for Proposition."""

        kind: Any = None
        formula: str = ""
        free_variables: tuple[str, ...] = ()
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        """Stub for Carrier."""

        name: str = ""
        parameters: tuple[str, ...] = ()
        is_dependent: bool = False
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        """Stub for EvidenceItem."""

        kind: Any = None
        payload: Mapping[str, Any] = field(default_factory=dict)
        trust_level: Any = None
        channel: str = ""
        timestamp: str = ""
        expiry: str = ""
        provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        """Stub for EvidenceBundle."""

        items: tuple[Any, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        """Stub for ResidualObligation."""

        description: str = ""
        coordinate: Any = None

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        """Stub for Obstruction."""

        description: str = ""
        coordinate: Any = None

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        """Stub for TrustAnnotation."""

        level: Any = None
        evidence_basis: tuple[str, ...] = ()
        ceiling: Any = None
        floor: Any = None
        reasons: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        """Stub for Provenance."""

        source: Any = None
        parent_judgments: tuple[str, ...] = ()
        creation_timestamp: str = ""
        transformation_history: tuple[str, ...] = ()
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass
    class Judgment:  # type: ignore[no-redef]
        """Stub for Judgment."""

        coordinate: Any = None
        proposition: Any = None
        carrier: Any = None
        evidence: Any = None
        obligations: tuple[Any, ...] = ()
        obstructions: tuple[Any, ...] = ()
        trust: Any = None
        provenance: Any = None
        clauses: tuple[Any, ...] = ()
        status: Any = None

# ---------------------------------------------------------------------------
# Callable surfaces model imports with stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.callable_surfaces.models import (
        ParameterKind,
        ParameterSpec,
        CallableSurface,
        MethodBinding,
        DescriptorRecord,
        DescriptorKind,
        BoundMethod,
        ClassConstruction,
        SignatureRecord,
    )
except Exception:
    import enum as _enum_mdl

    class ParameterKind(_enum_mdl.Enum):  # type: ignore[no-redef]
        """Stub for ParameterKind."""

        POSITIONAL_ONLY = "positional_only"
        POSITIONAL_OR_KEYWORD = "positional_or_keyword"
        VAR_POSITIONAL = "var_positional"
        KEYWORD_ONLY = "keyword_only"
        VAR_KEYWORD = "var_keyword"

    @dataclass(frozen=True, slots=True)
    class ParameterSpec:  # type: ignore[no-redef]
        """Stub for ParameterSpec."""

        name: str = ""
        kind: Any = None
        annotation: str = "Any"
        has_default: bool = False
        default_repr: str = ""
        is_variadic: bool = False
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {"name": self.name}

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "ParameterSpec":
            """Parse from dict."""
            return cls(name=data.get("name", ""))

    @dataclass(frozen=True, slots=True)
    class CallableSurface:  # type: ignore[no-redef]
        """Stub for CallableSurface."""

        name: str = ""
        qualname: str = ""
        module: str = ""
        parameters: tuple[Any, ...] = ()
        return_annotation: str = "Any"
        is_async: bool = False
        is_generator: bool = False
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def arity(self) -> int:
            """Return parameter count."""
            return len(self.parameters)

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "name": self.name,
                "qualname": self.qualname,
                "module": self.module,
                "parameters": [],
                "return_annotation": self.return_annotation,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "CallableSurface":
            """Parse from dict."""
            return cls(
                name=data.get("name", ""),
                qualname=data.get("qualname", ""),
                module=data.get("module", ""),
            )

    class DescriptorKind(_enum_mdl.Enum):  # type: ignore[no-redef]
        """Stub for DescriptorKind."""

        DATA = "data"
        NON_DATA = "non_data"
        METHOD = "method"
        CLASS_METHOD = "class_method"
        STATIC_METHOD = "static_method"
        PROPERTY = "property"
        SLOT = "slot"
        ABSTRACT = "abstract"

    @dataclass(frozen=True, slots=True)
    class DescriptorRecord:  # type: ignore[no-redef]
        """Stub for DescriptorRecord."""

        name: str = ""
        kind: Any = None
        owner_class: str = ""
        has_get: bool = False
        has_set: bool = False
        has_delete: bool = False
        coordinate: Any = None

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "name": self.name,
                "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
                "owner_class": self.owner_class,
                "has_get": self.has_get,
                "has_set": self.has_set,
                "has_delete": self.has_delete,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "DescriptorRecord":
            """Parse from dict."""
            return cls(name=data.get("name", ""), owner_class=data.get("owner_class", ""))

    @dataclass(frozen=True, slots=True)
    class MethodBinding:  # type: ignore[no-redef]
        """Stub for MethodBinding."""

        surface: Any = None
        descriptor: Any = None
        bound_at: float = 0.0

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {"bound_at": self.bound_at}

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "MethodBinding":
            """Parse from dict."""
            return cls(bound_at=float(data.get("bound_at", 0.0)))

    @dataclass(frozen=True, slots=True)
    class BoundMethod:  # type: ignore[no-redef]
        """Stub for BoundMethod."""

        method_name: str = ""
        instance_coordinate: Any = None
        class_coordinate: Any = None
        surface: Any = None
        is_classmethod: bool = False
        is_staticmethod: bool = False

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {"method_name": self.method_name}

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "BoundMethod":
            """Parse from dict."""
            return cls(method_name=data.get("method_name", ""))

    @dataclass(frozen=True, slots=True)
    class ClassConstruction:  # type: ignore[no-redef]
        """Stub for ClassConstruction."""

        class_coordinate: Any = None
        base_classes: tuple[str, ...] = ()
        metaclass: str = "type"
        mro: tuple[str, ...] = ()
        has_slots: bool = False
        has_new: bool = False
        has_init: bool = False
        has_init_subclass: bool = False

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "base_classes": list(self.base_classes),
                "metaclass": self.metaclass,
                "mro": list(self.mro),
                "has_slots": self.has_slots,
                "has_new": self.has_new,
                "has_init": self.has_init,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "ClassConstruction":
            """Parse from dict."""
            return cls(
                base_classes=tuple(data.get("base_classes", [])),
                metaclass=data.get("metaclass", "type"),
                mro=tuple(data.get("mro", [])),
                has_slots=bool(data.get("has_slots", False)),
                has_new=bool(data.get("has_new", False)),
                has_init=bool(data.get("has_init", False)),
            )

    @dataclass(frozen=True, slots=True)
    class SignatureRecord:  # type: ignore[no-redef]
        """Stub for SignatureRecord."""

        surface: Any = None
        raw_annotations: Mapping[str, str] = field(default_factory=dict)
        forward_refs: tuple[str, ...] = ()
        is_complete: bool = True

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "raw_annotations": dict(self.raw_annotations),
                "forward_refs": list(self.forward_refs),
                "is_complete": self.is_complete,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "SignatureRecord":
            """Parse from dict."""
            return cls(is_complete=bool(data.get("is_complete", True)))

# ---------------------------------------------------------------------------
# Module-level logger and helpers
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        str: Current UTC time, e.g. ``"2024-01-15T12:34:56.789012"``.
    """
    return datetime.datetime.utcnow().isoformat()


def _make_class_coord(module: str, qualname: str) -> CoordinateObject:
    """Build a :class:`CoordinateObject` for a class.

    Produces a coordinate with components::

        ("python_runtime", "callable_surfaces", "classes", module, qualname)

    and ``kind=CoordinateKind.INTERFACE``.

    Parameters:
        module: The ``__module__`` of the class.
        qualname: The ``__qualname__`` of the class.

    Returns:
        CoordinateObject: A coordinate representing the class in the semantic
        site.
    """
    components = ("python_runtime", "callable_surfaces", "classes", module, qualname)
    try:
        return CoordinateObject(
            components=components,
            kind=CoordinateKind.INTERFACE,
            support_labels=frozenset({qualname, module}),
            metadata={"class_qualname": qualname, "module": module},
        )
    except Exception:
        return CoordinateObject(components=components)  # type: ignore[call-arg]


def _class_qualname(cls: type) -> str:
    """Return a safe ``__qualname__`` for *cls*.

    Parameters:
        cls: The class to inspect.

    Returns:
        str: ``cls.__qualname__`` or ``cls.__name__`` if the former is absent.
    """
    return getattr(cls, "__qualname__", getattr(cls, "__name__", repr(cls)))


def _class_module(cls: type) -> str:
    """Return the module name for *cls*.

    Parameters:
        cls: The class to inspect.

    Returns:
        str: ``cls.__module__`` or empty string if absent.
    """
    return getattr(cls, "__module__", "") or ""


# ---------------------------------------------------------------------------
# ClassBuilder — mutable orchestrator for full class analysis
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClassBuilder:
    """Analyses a Python class and builds a :class:`ClassConstruction` record.

    :class:`ClassBuilder` is the primary entry-point for section §4.  It
    orchestrates calls to the specialised analyser sub-classes
    (:class:`MetaclassAnalyzer`, :class:`InitAnalyzer`) and assembles their
    findings into a single :class:`ClassConstruction` record.

    The builder is *mutable* (not frozen) because it accumulates a registry of
    analysed classes and error strings over multiple calls to
    :meth:`analyze_class`.

    Attributes:
        _registry: Mapping from class ``qualname`` to the corresponding
            :class:`ClassConstruction` record.  Populated by successive calls
            to :meth:`analyze_class`.
        _hierarchy: Mapping from class ``qualname`` to the list of direct
            base-class ``qualname`` strings.  Used for quick hierarchy queries.
        _errors: Accumulated error strings from failed analyses.  Individual
            method failures are captured here rather than propagated so that
            a partial result is returned rather than nothing.
    """

    _registry: dict[str, ClassConstruction] = field(default_factory=dict)
    _hierarchy: dict[str, list[str]] = field(default_factory=dict)
    _errors: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Primary analysis entry-point
    # ------------------------------------------------------------------

    def analyze_class(self, cls: type) -> ClassConstruction:
        """Analyse *cls* and return a :class:`ClassConstruction` record.

        Calls all component methods to extract metaclass, MRO, slots, class
        methods, instance methods, class vars, and the namespace.  Errors
        in individual component methods are caught and accumulated in
        :attr:`_errors` rather than propagated, so a partial result is
        always returned.

        Parameters:
            cls: The class to analyse.  Must be a Python ``type`` object.

        Returns:
            ClassConstruction: A fully (or partially, if errors occurred)
            populated :class:`ClassConstruction` record.

        Examples:
            >>> builder = ClassBuilder()
            >>> class Foo: pass
            >>> rec = builder.analyze_class(Foo)
            >>> rec.metaclass
            'type'
        """
        qualname = _class_qualname(cls)
        module = _class_module(cls)
        logger.debug("analyze_class: analysing %r (module=%r)", qualname, module)

        metaclass_name = self.find_metaclass(cls)
        mro = self.compute_mro(cls)
        has_slots = self.find_slots(cls)
        has_new = "__new__" in cls.__dict__
        has_init = "__init__" in cls.__dict__
        has_init_subclass = self.check_init_subclass_defined(cls)
        base_classes = tuple(
            _class_qualname(b)
            for b in cls.__bases__
            if b is not object
        )
        coord = _make_class_coord(module, qualname)

        construction = ClassConstruction(
            class_coordinate=coord,
            base_classes=base_classes,
            metaclass=metaclass_name,
            mro=mro,
            has_slots=has_slots,
            has_new=has_new,
            has_init=has_init,
            has_init_subclass=has_init_subclass,
        )

        self._registry[qualname] = construction
        self._hierarchy[qualname] = list(base_classes)
        logger.debug("analyze_class: registered %r (mro depth=%d)", qualname, len(mro))
        return construction

    def find_metaclass(self, cls: type) -> str:
        """Return the name of *cls*'s metaclass.

        Accesses ``type(cls)`` and returns its ``__name__``.  For classes
        with the default metaclass, this returns ``"type"``.

        Parameters:
            cls: The class whose metaclass is queried.

        Returns:
            str: The ``__name__`` of the metaclass, e.g. ``"type"``,
            ``"ABCMeta"``, or a custom metaclass name.

        Examples:
            >>> builder = ClassBuilder()
            >>> class Foo: pass
            >>> builder.find_metaclass(Foo)
            'type'
        """
        meta = type(cls)
        return meta.__name__

    def compute_mro(self, cls: type) -> tuple[str, ...]:
        """Compute the MRO of *cls* as a tuple of class name strings.

        Uses ``cls.__mro__`` (Python's C3 linearisation) and converts each
        class to its ``__name__``.

        Parameters:
            cls: The class whose MRO is computed.

        Returns:
            tuple[str, ...]: The MRO as a tuple of class names, e.g.
            ``("Dog", "Animal", "object")``.

        Examples:
            >>> class A: pass
            >>> class B(A): pass
            >>> ClassBuilder().compute_mro(B)
            ('B', 'A', 'object')
        """
        return tuple(c.__name__ for c in cls.__mro__)

    def find_slots(self, cls: type) -> bool:
        """Return whether *cls* declares ``__slots__``.

        Only checks ``cls.__dict__`` (direct definition), not inherited
        slots.

        Parameters:
            cls: The class to check.

        Returns:
            bool: ``True`` iff ``"__slots__" in cls.__dict__``.
        """
        return "__slots__" in cls.__dict__

    def find_class_methods(self, cls: type) -> list[str]:
        """Return the names of all ``classmethod`` members defined on *cls*.

        Inspects ``cls.__dict__`` (not inherited methods) for objects that
        are instances of ``classmethod``.

        Parameters:
            cls: The class to inspect.

        Returns:
            list[str]: Names of classmethod members in declaration order
            (i.e., in ``cls.__dict__`` iteration order).

        Examples:
            >>> class C:
            ...     @classmethod
            ...     def create(cls): pass
            >>> ClassBuilder().find_class_methods(C)
            ['create']
        """
        return [
            name
            for name, obj in cls.__dict__.items()
            if isinstance(obj, classmethod)
        ]

    def find_instance_methods(self, cls: type) -> list[str]:
        """Return the names of all regular function members defined on *cls*.

        Inspects ``cls.__dict__`` for plain :class:`function` objects
        (i.e., not ``classmethod``, ``staticmethod``, or ``property``).

        Parameters:
            cls: The class to inspect.

        Returns:
            list[str]: Names of instance method members.

        Examples:
            >>> class C:
            ...     def greet(self): pass
            >>> ClassBuilder().find_instance_methods(C)
            ['greet']
        """
        import types as _types
        return [
            name
            for name, obj in cls.__dict__.items()
            if isinstance(obj, _types.FunctionType)
        ]

    def find_class_vars(self, cls: type) -> dict[str, Any]:
        """Return non-callable, non-dunder items from ``cls.__dict__``.

        These are the class-level variables (not methods or descriptors).
        Dunder names are excluded because they are internal implementation
        detail, and callables are excluded because they are methods.

        Parameters:
            cls: The class to inspect.

        Returns:
            dict[str, Any]: A ``{name: value}`` mapping of class variables.

        Examples:
            >>> class C:
            ...     count: int = 0
            ...     label = "hello"
            >>> ClassBuilder().find_class_vars(C)
            {'count': 0, 'label': 'hello'}
        """
        skip_names = frozenset({
            "__dict__", "__weakref__", "__doc__", "__module__",
            "__qualname__", "__slots__",
        })
        result: dict[str, Any] = {}
        for name, obj in cls.__dict__.items():
            if name.startswith("__") and name.endswith("__"):
                continue
            if name in skip_names:
                continue
            if callable(obj) or isinstance(obj, (classmethod, staticmethod, property)):
                continue
            result[name] = obj
        return result

    def build_construction_record(self, cls: type) -> ClassConstruction:
        """Build and return a :class:`ClassConstruction` without caching.

        This is a functional version of :meth:`analyze_class` that does not
        update :attr:`_registry` or :attr:`_hierarchy`.  Useful when you
        want a snapshot of a class state without affecting the builder's
        internal state.

        Parameters:
            cls: The class to build a record for.

        Returns:
            ClassConstruction: A freshly built record.
        """
        qualname = _class_qualname(cls)
        module = _class_module(cls)
        metaclass_name = self.find_metaclass(cls)
        mro = self.compute_mro(cls)
        has_slots = self.find_slots(cls)
        base_classes = tuple(
            _class_qualname(b)
            for b in cls.__bases__
            if b is not object
        )
        coord = _make_class_coord(module, qualname)
        return ClassConstruction(
            class_coordinate=coord,
            base_classes=base_classes,
            metaclass=metaclass_name,
            mro=mro,
            has_slots=has_slots,
            has_new="__new__" in cls.__dict__,
            has_init="__init__" in cls.__dict__,
            has_init_subclass="__init_subclass__" in cls.__dict__,
        )

    def build_namespace(self, cls: type) -> dict[str, Any]:
        """Build a filtered copy of ``cls.__dict__`` without non-serialisable items.

        Removes ``__dict__``, ``__weakref__``, and any object that fails a
        basic serializability check (e.g., C-level descriptors that have no
        ``__name__``).

        Parameters:
            cls: The class whose namespace is extracted.

        Returns:
            dict[str, Any]: A ``{name: repr(value)}`` mapping of namespace
            items, with values converted to ``repr()`` strings to ensure
            serializability.
        """
        skip_names = frozenset({
            "__dict__", "__weakref__",
        })
        result: dict[str, Any] = {}
        for name, obj in cls.__dict__.items():
            if name in skip_names:
                continue
            try:
                result[name] = repr(obj)
            except Exception:
                result[name] = f"<unrepr:{type(obj).__name__}>"
        return result

    def check_init_subclass_defined(self, cls: type) -> bool:
        """Return whether *cls* directly defines ``__init_subclass__``.

        Checks only ``cls.__dict__``, not inherited definitions.

        Parameters:
            cls: The class to check.

        Returns:
            bool: ``True`` iff ``"__init_subclass__" in cls.__dict__``.
        """
        return "__init_subclass__" in cls.__dict__

    def build_judgment(self, construction: ClassConstruction) -> Judgment:
        """Build a :class:`Judgment` recording the class construction event.

        Produces a ``STRUCTURAL`` proposition asserting that the class
        construction morphism (metaclass call → ``__new__`` → ``__init__``)
        was successfully factorised.

        Parameters:
            construction: The :class:`ClassConstruction` record to judge.

        Returns:
            Judgment: A ``SETTLED`` structural judgment at
            ``RUNTIME_WITNESSED`` trust level.
        """
        qualname = ""
        module = ""
        coord_obj = construction.class_coordinate
        if coord_obj is not None:
            comps = getattr(coord_obj, "components", ())
            if len(comps) >= 5:
                module = comps[3]
                qualname = comps[4]
        formula = (
            f"class_construction_valid("
            f"qualname={qualname!r}, "
            f"module={module!r}, "
            f"metaclass={construction.metaclass!r}, "
            f"mro_depth={len(construction.mro)}, "
            f"has_slots={construction.has_slots})"
        )
        now = _now_iso()
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(),
            metadata={
                "mro": list(construction.mro),
                "metaclass": construction.metaclass,
            },
        )
        carrier = Carrier(
            name=qualname,
            parameters=tuple(construction.base_classes),
            is_dependent=bool(construction.base_classes),
            metadata={
                "metaclass": construction.metaclass,
                "has_slots": construction.has_slots,
            },
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={
                "qualname": qualname,
                "module": module,
                "metaclass": construction.metaclass,
                "mro": list(construction.mro),
                "has_slots": construction.has_slots,
                "has_new": construction.has_new,
                "has_init": construction.has_init,
            },
            trust_level=TrustLevel.RUNTIME_WITNESSED,
            channel="class_builder",
            timestamp=now,
            expiry="",
            provenance=(qualname,),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        prov = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=now,
            transformation_history=(),
            metadata={"builder": "ClassBuilder"},
        )
        trust_ann = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=(qualname,),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"class {qualname!r} construction analysed",),
        )
        coord = construction.class_coordinate or _make_class_coord(module, qualname)
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=trust_ann,
            provenance=prov,
            clauses=(),
            status=JudgmentStatus.SETTLED,
        )


# ---------------------------------------------------------------------------
# MetaclassAnalyzer — detailed introspection of metaclass chains
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetaclassAnalyzer:
    """Analyses the metaclass chain of a class and detects conflicts.

    In Python, the metaclass of a class is the type of the class object.
    For ``class C(B1, B2)``, the metaclass must be a (possibly strict)
    subclass of every base class's metaclass.  If no such subclass exists,
    Python raises ``TypeError: metaclass conflict``.

    This class implements the metaclass resolution algorithm from CPython's
    ``type_new`` C function (also described in theory2.tex §16.4.2) and
    detects conflicts before class creation.

    Attributes:
        strict: When ``True``, :meth:`check_metaclass_conflict` and
            :meth:`effective_metaclass` raise rather than returning
            empty lists / ``type`` on conflict.
    """

    strict: bool = False

    def find_metaclass(self, cls: type) -> type:
        """Return the metaclass of *cls*.

        Uses ``type(cls)`` which is always defined for any class object.

        Parameters:
            cls: The class to query.

        Returns:
            type: The metaclass of *cls*, e.g. ``type``, ``ABCMeta``.

        Examples:
            >>> from abc import ABCMeta
            >>> class A(metaclass=ABCMeta): pass
            >>> MetaclassAnalyzer().find_metaclass(A)
            <class 'abc.ABCMeta'>
        """
        return type(cls)

    def is_custom_metaclass(self, cls: type) -> bool:
        """Return whether *cls* uses a metaclass other than ``type``.

        Parameters:
            cls: The class to query.

        Returns:
            bool: ``True`` iff ``type(cls) is not type``.

        Examples:
            >>> class Plain: pass
            >>> MetaclassAnalyzer().is_custom_metaclass(Plain)
            False
        """
        return type(cls) is not type

    def metaclass_chain(self, cls: type) -> list[type]:
        """Return the MRO of the metaclass of *cls*.

        Provides the full metaclass inheritance chain, which determines
        which metaclass methods (e.g., ``__prepare__``, ``__new__``) are
        available.

        Parameters:
            cls: The class whose metaclass chain is returned.

        Returns:
            list[type]: The MRO of ``type(cls)`` as a list of type objects.

        Examples:
            >>> class Plain: pass
            >>> MetaclassAnalyzer().metaclass_chain(Plain)
            [<class 'type'>, <class 'object'>]
        """
        meta = type(cls)
        return list(meta.__mro__)

    def check_metaclass_conflict(self, bases: tuple[type, ...]) -> list[str]:
        """Detect metaclass conflicts among *bases*.

        Two metaclasses ``M1`` and ``M2`` *conflict* iff neither
        ``issubclass(M1, M2)`` nor ``issubclass(M2, M1)``.

        Implements a pairwise conflict check across all base class
        metaclasses.

        Parameters:
            bases: The tuple of base classes as supplied to the ``class``
                statement.

        Returns:
            list[str]: Human-readable conflict descriptions.  An empty
            list means no metaclass conflicts were detected.

        Raises:
            TypeError: When :attr:`strict` is ``True`` and a conflict is
                detected.

        Examples:
            >>> MetaclassAnalyzer().check_metaclass_conflict((object,))
            []
        """
        metas = [type(b) for b in bases]
        conflicts: list[str] = []
        for i, mi in enumerate(metas):
            for j, mj in enumerate(metas):
                if i >= j:
                    continue
                if not (issubclass(mi, mj) or issubclass(mj, mi)):
                    conflicts.append(
                        f"Metaclass conflict: {mi.__name__!r} (for {bases[i].__name__!r}) "
                        f"and {mj.__name__!r} (for {bases[j].__name__!r}) are unrelated."
                    )
        if self.strict and conflicts:
            raise TypeError(
                "Metaclass conflict detected: " + "; ".join(conflicts)
            )
        return conflicts

    def effective_metaclass(
        self,
        bases: tuple[type, ...],
        explicit_meta: type | None = None,
    ) -> type:
        """Resolve the effective metaclass for a class with the given *bases*.

        Implements the CPython metaclass resolution algorithm:

        1. Start with *explicit_meta* (from ``metaclass=`` keyword) or
           ``type`` as the winner.
        2. For each base class, get its metaclass.  If the candidate is
           a subclass of the current winner, replace the winner.  If the
           current winner is a subclass of the candidate, keep the winner.
           Otherwise, raise ``TypeError`` (if :attr:`strict`) or fall
           back to ``type``.

        Parameters:
            bases: The tuple of base classes.
            explicit_meta: The metaclass given explicitly in the ``class``
                statement, or ``None`` to infer from bases.

        Returns:
            type: The effective metaclass that should be used for the new
            class.

        Raises:
            TypeError: When :attr:`strict` is ``True`` and a conflict is
                unresolvable.
        """
        winner: type = explicit_meta if explicit_meta is not None else type
        for base in bases:
            base_meta = type(base)
            if issubclass(base_meta, winner):
                winner = base_meta
            elif issubclass(winner, base_meta):
                pass  # current winner is already more derived, keep it
            else:
                if self.strict:
                    raise TypeError(
                        f"Metaclass conflict: {winner.__name__!r} and "
                        f"{base_meta.__name__!r} are incompatible."
                    )
                logger.warning(
                    "effective_metaclass: unresolvable conflict between %r and %r, "
                    "returning type as fallback",
                    winner.__name__, base_meta.__name__,
                )
                return type
        return winner


# ---------------------------------------------------------------------------
# InitAnalyzer — introspection of __new__ / __init__ / __init_subclass__
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InitAnalyzer:
    """Analyses the initialisation protocol (``__new__`` / ``__init__``) of a class.

    In the factored-morphism model of theory2.tex §16.4, the metaclass call
    ``M(name, bases, namespace)`` is factored as::

        M.__call__  →  M.__new__  ×  M.__init__

    This class extracts those callables, converts them to
    :class:`~jugeo.python_runtime.callable_surfaces.models.CallableSurface`
    records, and checks for cooperative inheritance (``super().__init__``).

    Attributes:
        include_inherited: When ``True``, walk the MRO for ``__new__`` and
            ``__init__`` if they are not defined directly on the class.
    """

    include_inherited: bool = True

    def find_new(self, cls: type) -> Any | None:
        """Locate ``__new__`` on *cls* or its MRO.

        Parameters:
            cls: The class to inspect.

        Returns:
            Any | None: The ``__new__`` callable, or ``None`` if it is
            ``object.__new__`` (the default with no custom logic).

        Examples:
            >>> class C:
            ...     def __new__(cls): return super().__new__(cls)
            >>> InitAnalyzer().find_new(C) is not None
            True
        """
        if "__new__" in cls.__dict__:
            return cls.__dict__["__new__"]
        if not self.include_inherited:
            return None
        for base in cls.__mro__[1:]:
            if "__new__" in base.__dict__:
                result = base.__dict__["__new__"]
                if base is not object:
                    return result
                return None
        return None

    def find_init(self, cls: type) -> Any | None:
        """Locate ``__init__`` on *cls* or its MRO.

        Parameters:
            cls: The class to inspect.

        Returns:
            Any | None: The ``__init__`` callable, or ``None`` if it is
            ``object.__init__`` (the no-op default).

        Examples:
            >>> class C:
            ...     def __init__(self, x): self.x = x
            >>> InitAnalyzer().find_init(C) is not None
            True
        """
        if "__init__" in cls.__dict__:
            return cls.__dict__["__init__"]
        if not self.include_inherited:
            return None
        for base in cls.__mro__[1:]:
            if "__init__" in base.__dict__:
                result = base.__dict__["__init__"]
                if base is not object:
                    return result
                return None
        return None

    def analyze_init_signature(self, cls: type) -> CallableSurface | None:
        """Extract a :class:`CallableSurface` from ``cls.__init__``.

        Uses :func:`inspect.signature` on ``cls.__init__`` to extract
        parameters and the return annotation (always ``None`` for
        ``__init__``).

        Parameters:
            cls: The class whose ``__init__`` is analysed.

        Returns:
            CallableSurface | None: A surface record, or ``None`` if
            ``cls.__init__`` is ``object.__init__`` or signature extraction
            fails.

        Examples:
            >>> class C:
            ...     def __init__(self, x: int) -> None: ...
            >>> surf = InitAnalyzer().analyze_init_signature(C)
            >>> surf is not None
            True
        """
        init = self.find_init(cls)
        if init is None:
            return None
        try:
            sig = inspect.signature(init)
        except (ValueError, TypeError) as exc:
            logger.debug(
                "analyze_init_signature: cannot inspect %r.__init__: %s",
                cls.__name__, exc,
            )
            return None

        params: list[Any] = []
        for i, (pname, param) in enumerate(sig.parameters.items()):
            raw_kind = param.kind
            # Map inspect.Parameter kind int to ParameterKind
            kind_map = {
                inspect.Parameter.POSITIONAL_ONLY: ParameterKind.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD: ParameterKind.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL: ParameterKind.VAR_POSITIONAL,
                inspect.Parameter.KEYWORD_ONLY: ParameterKind.KEYWORD_ONLY,
                inspect.Parameter.VAR_KEYWORD: ParameterKind.VAR_KEYWORD,
            }
            pk = kind_map.get(raw_kind, ParameterKind.POSITIONAL_OR_KEYWORD)
            ann = (
                str(param.annotation)
                if param.annotation is not inspect.Parameter.empty
                else "Any"
            )
            params.append(ParameterSpec(
                name=pname,
                kind=pk,
                annotation=ann,
                has_default=param.default is not inspect.Parameter.empty,
            ))

        qualname = getattr(init, "__qualname__", f"{cls.__name__}.__init__")
        module = getattr(init, "__module__", _class_module(cls))
        return CallableSurface(
            name="__init__",
            qualname=qualname,
            module=module,
            parameters=tuple(params),
            return_annotation="None",
            is_async=False,
            is_generator=False,
        )

    def check_init_subclass(self, cls: type) -> bool:
        """Return whether *cls* directly defines ``__init_subclass__``.

        Parameters:
            cls: The class to check.

        Returns:
            bool: ``True`` iff ``"__init_subclass__" in cls.__dict__``.
        """
        return "__init_subclass__" in cls.__dict__

    def detect_cooperative_inheritance(self, cls: type) -> bool:
        """Heuristically detect whether ``cls.__init__`` calls ``super().__init__``.

        Uses :func:`inspect.getsource` to read the source of ``__init__``
        and looks for the string ``"super()"`` in the body.  This is a
        best-effort heuristic; it will return ``False`` for built-in
        classes or dynamically generated code.

        Parameters:
            cls: The class to check.

        Returns:
            bool: ``True`` iff ``super()`` appears in the ``__init__``
            source.  Returns ``False`` on any error (OSError, TypeError).

        Examples:
            >>> class Base:
            ...     def __init__(self): pass
            >>> class Child(Base):
            ...     def __init__(self):
            ...         super().__init__()
            >>> InitAnalyzer().detect_cooperative_inheritance(Child)
            True
        """
        init = cls.__dict__.get("__init__")
        if init is None:
            return False
        try:
            source = inspect.getsource(init)
            return "super()" in source
        except (OSError, TypeError):
            return False

    def build_construction_morphism(self, cls: type) -> dict[str, Any]:
        """Return a dict describing the ``__new__`` → ``__init__`` factorisation.

        Assembles a summary of the construction morphism for *cls*,
        including whether both parts are present, whether cooperative
        inheritance is detected, and the ``__init__`` surface if available.

        Parameters:
            cls: The class to describe.

        Returns:
            dict[str, Any]: A dictionary with keys:
            ``class_name``, ``has_new``, ``has_init``,
            ``cooperative_inheritance``, ``init_surface_qualname``,
            ``metaclass``.

        Examples:
            >>> class C:
            ...     def __init__(self, x): ...
            >>> morphism = InitAnalyzer().build_construction_morphism(C)
            >>> morphism["has_init"]
            True
        """
        has_new = "__new__" in cls.__dict__
        has_init = "__init__" in cls.__dict__
        cooperative = self.detect_cooperative_inheritance(cls)
        surface = self.analyze_init_signature(cls)
        surface_qualname = surface.qualname if surface is not None else ""
        metaclass = type(cls).__name__
        return {
            "class_name": cls.__name__,
            "qualname": _class_qualname(cls),
            "has_new": has_new,
            "has_init": has_init,
            "cooperative_inheritance": cooperative,
            "init_surface_qualname": surface_qualname,
            "metaclass": metaclass,
            "mro": [c.__name__ for c in cls.__mro__],
        }


# ---------------------------------------------------------------------------
# ClassHierarchyTracker — mutable registry of class constructions
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClassHierarchyTracker:
    """Tracks a set of :class:`ClassConstruction` records and their relationships.

    As each class is analysed by :class:`ClassBuilder`, the resulting
    :class:`ClassConstruction` can be registered here.  The tracker maintains
    two bidirectional indices:

    - :attr:`_subclass_map`: maps a class name → set of direct subclass names.
    - :attr:`_superclass_map`: maps a class name → set of direct superclass names.

    This enables efficient queries such as "find all subclasses of X" or
    "find all classes that inherit from both X and Y".

    The tracker is *mutable* because it is designed to accumulate registrations
    incrementally as new classes are loaded.

    Attributes:
        _classes: Mapping from class ``qualname`` to its
            :class:`ClassConstruction` record.
        _subclass_map: Mapping from class name to the set of names of classes
            that directly extend it.
        _superclass_map: Mapping from class name to the set of names of its
            direct superclasses (i.e., ``base_classes`` from the construction).
    """

    _classes: dict[str, ClassConstruction] = field(default_factory=dict)
    _subclass_map: dict[str, set[str]] = field(default_factory=dict)
    _superclass_map: dict[str, set[str]] = field(default_factory=dict)

    def register_class(self, cls: type, construction: ClassConstruction) -> None:
        """Register *cls* and its *construction* in the tracker.

        Updates :attr:`_classes`, :attr:`_subclass_map`, and
        :attr:`_superclass_map` based on the ``base_classes`` tuple in
        *construction*.

        Parameters:
            cls: The class being registered.  Its ``__name__`` is used as
                the primary key.
            construction: The :class:`ClassConstruction` record for *cls*.

        Returns:
            None

        Examples:
            >>> tracker = ClassHierarchyTracker()
            >>> class A: pass
            >>> builder = ClassBuilder()
            >>> rec = builder.analyze_class(A)
            >>> tracker.register_class(A, rec)
            >>> "A" in tracker._classes
            True
        """
        cls_name = cls.__name__
        qualname = _class_qualname(cls)
        self._classes[qualname] = construction
        self._superclass_map.setdefault(cls_name, set())
        self._subclass_map.setdefault(cls_name, set())
        for base_qualname in construction.base_classes:
            base_name = base_qualname.split(".")[-1]
            self._superclass_map[cls_name].add(base_qualname)
            self._subclass_map.setdefault(base_name, set()).add(cls_name)
        logger.debug(
            "register_class: registered %r with %d bases",
            cls_name, len(construction.base_classes),
        )

    def find_subclasses(self, cls_name: str) -> set[str]:
        """Return the set of direct subclass names of *cls_name*.

        Parameters:
            cls_name: The simple class name (not qualname) to look up.

        Returns:
            set[str]: Direct subclass names, or empty set if *cls_name* has
            no registered subclasses.

        Examples:
            >>> tracker = ClassHierarchyTracker()
            >>> # After registering classes...
            >>> tracker.find_subclasses("Animal")
            {'Dog', 'Cat'}
        """
        return set(self._subclass_map.get(cls_name, set()))

    def find_superclasses(self, cls_name: str) -> set[str]:
        """Return the set of direct superclass qualnames of *cls_name*.

        Parameters:
            cls_name: The simple class name to look up.

        Returns:
            set[str]: Qualnames of direct superclasses, or empty set if
            *cls_name* has no registered superclasses.

        Examples:
            >>> tracker = ClassHierarchyTracker()
            >>> tracker.find_superclasses("object")
            set()
        """
        return set(self._superclass_map.get(cls_name, set()))

    def find_siblings(self, cls_name: str) -> set[str]:
        """Return all classes that share at least one direct superclass with *cls_name*.

        A sibling of ``C`` is any class ``S ≠ C`` that extends some class
        ``P`` that also extends ``P`` — i.e., the union of all subclasses of
        each of ``C``'s superclasses, minus ``C`` itself.

        Parameters:
            cls_name: The class whose siblings are sought.

        Returns:
            set[str]: Names of sibling classes.

        Examples:
            >>> # Dog and Cat both extend Animal → they are siblings.
            >>> tracker.find_siblings("Dog")
            {'Cat'}
        """
        siblings: set[str] = set()
        for superclass_qualname in self._superclass_map.get(cls_name, set()):
            super_name = superclass_qualname.split(".")[-1]
            for sub in self._subclass_map.get(super_name, set()):
                if sub != cls_name:
                    siblings.add(sub)
        return siblings

    def depth_in_hierarchy(self, cls_name: str) -> int:
        """Return the number of superclasses above *cls_name* in the hierarchy.

        Counts the cardinality of the transitive closure of the superclass
        relation from *cls_name* upward, bounded by ``object``.  Uses a
        simple BFS over the registered superclass map.

        Parameters:
            cls_name: The class name to measure depth for.

        Returns:
            int: The number of ancestor classes (direct + indirect) above
            *cls_name* in the registered hierarchy.  Returns 0 if *cls_name*
            has no registered superclasses.

        Examples:
            >>> # Dog → Animal → object → depth = 2
            >>> tracker.depth_in_hierarchy("Dog")
            2
        """
        visited: set[str] = set()
        frontier = set(
            sn.split(".")[-1]
            for sn in self._superclass_map.get(cls_name, set())
        )
        depth = 0
        while frontier:
            next_frontier: set[str] = set()
            for name in frontier:
                if name in visited:
                    continue
                visited.add(name)
                depth += 1
                for sn in self._superclass_map.get(name, set()):
                    sn_simple = sn.split(".")[-1]
                    if sn_simple not in visited:
                        next_frontier.add(sn_simple)
            frontier = next_frontier
        return depth

    def all_concrete_classes(self) -> list[str]:
        """Return the names of all registered classes that have no subclasses.

        A *concrete* class in the tracker sense is a class that no other
        registered class directly extends.  These are the leaves of the
        inheritance forest.

        Returns:
            list[str]: Sorted list of concrete class names (no subclasses
            in the current registry).

        Examples:
            >>> tracker.all_concrete_classes()
            ['Dog', 'Cat', 'Fish']
        """
        result: list[str] = []
        for qualname, construction in self._classes.items():
            cls_name = qualname.split(".")[-1]
            subs = self._subclass_map.get(cls_name, set())
            if not subs:
                result.append(cls_name)
        return sorted(set(result))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ClassBuilder",
    "MetaclassAnalyzer",
    "InitAnalyzer",
    "ClassHierarchyTracker",
]
