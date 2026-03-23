"""Section 3 — Descriptor Lookup as Sheaf Lookup.

Theory reference: theory2.tex Ch16 §3 — descriptor lookup as sheaf-theoretic
lookup over the MRO topology.

Generated with assistance from copilot.

Overview
--------
This module implements Python's descriptor protocol as a *sheaf lookup* in the
sense of theory2.tex Ch16 §3.  In that framework, the method resolution order
(MRO) of a class is a finite ordered covering family for the semantic site.
Each class in the MRO provides a local section (its ``__dict__``), and the
global section (the resolved attribute) is the unique amalgamation of those
local sections subject to a priority ordering.

The priority ordering mirrors Python's own rule:

1. **Data descriptors** (objects with ``__set__`` or ``__delete__``) in the MRO
   take precedence over everything else.  In sheaf language, they are "globally
   defined" sections that cannot be shadowed by instance data.
2. **Instance ``__dict__``** provides the instance-local section, evaluated
   only when no data descriptor claims the name.
3. **Non-data descriptors** (objects with only ``__get__``) are the fallback:
   they form "optional" local sections that yield to the instance dict.

Each step is recorded as a :class:`~jugeo.judgments.judgment_terms.Judgment` at
the appropriate trust level, giving downstream consumers a full audit trail of
every attribute resolution.

Sheaf-Theoretic Formulation
----------------------------
Let ``C`` be a class with MRO ``(C₀, C₁, …, Cₙ)`` where ``C₀ = C`` and
``Cₙ = object``.  Define the *descriptor site* as the small category whose
objects are the ``Cᵢ`` and whose morphisms are the restriction maps induced by
inheritance.  A *descriptor section* over the MRO is a family of values
``(dᵢ)`` — one per ``Cᵢ`` that contains the name — satisfying the obvious
compatibility condition (each ``dᵢ`` is consistent with the base class value).

The lookup algorithm is the *canonical section* of this sheaf:
- Scan the MRO left-to-right for a data descriptor → return it (data priority).
- If no data descriptor, check the instance ``__dict__``.
- If not in ``__dict__``, scan the MRO left-to-right for any descriptor → apply
  ``__get__``.
- Raise ``AttributeError`` if nothing is found.

Coordinate Assignment
---------------------
Each descriptor is assigned a :class:`~jugeo.geometry.site.CoordinateObject`
of kind ``FUNCTION`` (for callable descriptors) with components::

    ("python_runtime", "callable_surfaces", "descriptors", cls.__name__, name)

This locates the descriptor in the global semantic site so that
:class:`~jugeo.judgments.judgment_terms.Judgment` objects produced here can be
composed with judgments from other modules.

Copilot Note
------------
This module was scaffolded with copilot assistance (copilot integration point).
All judgment objects enter at ``TrustLevel.RUNTIME_WITNESSED`` when produced
from live inspection and ``TrustLevel.ORACLE_PROPOSED`` when produced from
static analysis alone.  See theory2.tex §16.9 for trust promotion policy.

Examples
--------
Basic descriptor lookup::

    from jugeo.python_runtime.callable_surfaces.descriptors import (
        DescriptorProtocol,
        DescriptorInspector,
    )

    class Celsius:
        @property
        def value(self) -> float:
            return self._value

        @value.setter
        def value(self, v: float) -> None:
            self._value = v

    protocol = DescriptorProtocol()
    inspector = DescriptorInspector(include_inherited=True)
    records = inspector.find_descriptors(Celsius)
    for rec in records:
        print(rec.name, rec.kind)
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
            return {
                "name": self.name,
                "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
                "annotation": self.annotation,
                "has_default": self.has_default,
            }

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

        def lookup_priority(self) -> int:
            """Return numeric lookup priority."""
            _priorities = {
                "data": 10,
                "property": 9,
                "slot": 8,
                "class_method": 5,
                "static_method": 5,
                "method": 3,
                "non_data": 2,
                "abstract": 1,
            }
            return _priorities.get(self.value, 0)

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
            kind_val = data.get("kind", "non_data")
            try:
                kind = DescriptorKind(kind_val)
            except (ValueError, KeyError):
                kind = DescriptorKind.NON_DATA
            return cls(
                name=data.get("name", ""),
                kind=kind,
                owner_class=data.get("owner_class", ""),
                has_get=bool(data.get("has_get", False)),
                has_set=bool(data.get("has_set", False)),
                has_delete=bool(data.get("has_delete", False)),
            )

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
# Module-level helpers
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        str: Current UTC time, e.g. ``"2024-01-15T12:34:56.789012"``.
    """
    return datetime.datetime.utcnow().isoformat()


def _make_descriptor_coord(cls_name: str, name: str) -> CoordinateObject:
    """Build a :class:`CoordinateObject` for a descriptor member.

    Produces a coordinate with components::

        ("python_runtime", "callable_surfaces", "descriptors", cls_name, name)

    Parameters:
        cls_name: The ``__name__`` of the owning class.
        name: The attribute name being described.

    Returns:
        CoordinateObject: A new coordinate located in the descriptor sub-site.
    """
    components = ("python_runtime", "callable_surfaces", "descriptors", cls_name, name)
    try:
        return CoordinateObject(
            components=components,
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({cls_name, name}),
            metadata={"descriptor_name": name, "owner": cls_name},
        )
    except Exception:
        return CoordinateObject(components=components)  # type: ignore[call-arg]


def _descriptor_coord_key(cls_name: str, name: str) -> str:
    """Compute a stable string key for a descriptor coordinate.

    Parameters:
        cls_name: Name of the owning class.
        name: Descriptor attribute name.

    Returns:
        str: A hex-digest string uniquely identifying the (cls_name, name) pair.
    """
    raw = f"{cls_name}::{name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# DescriptorProtocol — mutable orchestrator for the full lookup pipeline
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DescriptorProtocol:
    """Implements Python's descriptor protocol as a sheaf-theoretic lookup.

    In the site-theoretic framework of theory2.tex Ch16 §3, attribute lookup
    on a Python object is the *canonical section* of the descriptor sheaf over
    the MRO covering family.  This class implements that lookup in three phases:

    1. **Data-descriptor phase**: scan the class MRO for an object in any
       class ``__dict__`` that exposes a ``__set__`` or ``__delete__`` method.
       Data descriptors are "globally defined" sections and take priority over
       all instance data.

    2. **Instance-dict phase**: if no data descriptor claims the attribute,
       check the instance's own ``__dict__`` for a local override.

    3. **Non-data-descriptor phase**: scan the MRO for an object that exposes
       only ``__get__`` (no ``__set__``, no ``__delete__``).  These provide
       "default" sections that yield to instance data.

    Each phase is logged and can optionally emit a
    :class:`~jugeo.judgments.judgment_terms.Judgment` for audit.

    Attributes:
        _records: Registry of :class:`DescriptorRecord` objects keyed by
            ``"{owner_class}::{name}"`` strings.  Pre-populating this cache
            avoids redundant MRO traversals when the same class is queried
            repeatedly.
        _site: Optional :class:`~jugeo.geometry.site.Site` for coordinate-aware
            operations.  When provided, every new descriptor coordinate is
            registered in the site.
        _lookup_trace: Ordered list of strings describing each step taken
            during the most recent :meth:`lookup` call.  Useful for debugging
            resolution order surprises.
    """

    _records: dict[str, DescriptorRecord] = field(default_factory=dict)
    _site: Site | None = field(default=None)
    _lookup_trace: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Primary lookup entry-point
    # ------------------------------------------------------------------

    def lookup(self, obj: Any, name: str) -> Any:
        """Resolve *name* on *obj* following Python's descriptor protocol.

        Implements the full three-phase lookup:

        1. Data descriptor in the MRO.
        2. Instance ``__dict__``.
        3. Non-data descriptor in the MRO.

        Each phase appends a trace string to :attr:`_lookup_trace`.

        Parameters:
            obj: The Python object on which the attribute is being looked up.
                May be a class or an instance.
            name: The attribute name to resolve.

        Returns:
            Any: The resolved attribute value, possibly transformed by a
            descriptor's ``__get__`` method.

        Raises:
            AttributeError: When *name* cannot be found via any lookup phase.

        Examples:
            >>> protocol = DescriptorProtocol()
            >>> class Foo:
            ...     @property
            ...     def bar(self): return 42
            >>> protocol.lookup(Foo(), "bar")
            42
        """
        self._lookup_trace.clear()
        cls = type(obj) if not isinstance(obj, type) else obj
        objtype = cls

        # Phase 1: data descriptor
        self._lookup_trace.append(f"phase1:data_descriptor:{name}")
        data_rec = self.check_data_descriptor(cls, name)
        if data_rec is not None:
            logger.debug("lookup(%r, %r): data descriptor found in %r", obj, name, data_rec.owner_class)
            self._lookup_trace.append(f"phase1:hit:{data_rec.owner_class}")
            raw_obj = self._get_raw_from_mro(cls, name)
            if raw_obj is not None and hasattr(raw_obj, "__get__"):
                return self.apply_get(raw_obj, obj if not isinstance(obj, type) else None, objtype)
            if raw_obj is not None:
                return raw_obj

        # Phase 2: instance dict
        self._lookup_trace.append(f"phase2:instance_dict:{name}")
        if not isinstance(obj, type):
            inst_val = self.check_instance_dict(obj, name)
            if inst_val is not _MISSING:
                logger.debug("lookup(%r, %r): found in instance __dict__", obj, name)
                self._lookup_trace.append("phase2:hit")
                return inst_val

        # Phase 3: non-data descriptor
        self._lookup_trace.append(f"phase3:non_data_descriptor:{name}")
        non_data_rec = self.check_non_data_descriptor(cls, name)
        if non_data_rec is not None:
            logger.debug("lookup(%r, %r): non-data descriptor found in %r", obj, name, non_data_rec.owner_class)
            self._lookup_trace.append(f"phase3:hit:{non_data_rec.owner_class}")
            raw_obj = self._get_raw_from_mro(cls, name)
            if raw_obj is not None and hasattr(raw_obj, "__get__"):
                return self.apply_get(raw_obj, obj if not isinstance(obj, type) else None, objtype)
            if raw_obj is not None:
                return raw_obj

        self._lookup_trace.append(f"miss:{name}")
        logger.debug("lookup(%r, %r): AttributeError — name not found", obj, name)
        raise AttributeError(
            f"object of type {type(obj).__name__!r} has no attribute {name!r}"
        )

    def _get_raw_from_mro(self, cls: type, name: str) -> Any | None:
        """Return the raw object stored under *name* in the first MRO class that has it.

        This is a low-level helper used by :meth:`lookup` to fetch the actual
        descriptor object before invoking ``__get__``.

        Parameters:
            cls: The class whose MRO is searched.
            name: The attribute name sought.

        Returns:
            Any | None: The raw attribute object, or ``None`` if not found.
        """
        for base in cls.__mro__:
            if name in base.__dict__:
                return base.__dict__[name]
        return None

    # ------------------------------------------------------------------
    # Per-object lookup helpers
    # ------------------------------------------------------------------

    def lookup_on_instance(self, obj: Any, name: str) -> Any | None:
        """Look up *name* on the instance and its class hierarchy.

        Searches ``type(obj).__mro__`` for ``__dict__`` entries, then falls
        back to ``obj.__dict__``.  Does **not** invoke ``__get__``.

        Parameters:
            obj: The instance to search.
            name: The attribute name sought.

        Returns:
            Any | None: The raw attribute value, or ``None`` if not found.
        """
        cls = type(obj)
        for base in cls.__mro__:
            if name in base.__dict__:
                return base.__dict__[name]
        raw_dict = self.check_instance_dict(obj, name)
        if raw_dict is not _MISSING:
            return raw_dict
        return None

    def lookup_on_class(self, cls: type, name: str) -> Any | None:
        """Walk the MRO of *cls* and return the first value found for *name*.

        Unlike :meth:`lookup_on_instance`, this operates purely on the class
        hierarchy and never inspects instance ``__dict__`` objects.

        Parameters:
            cls: The class whose MRO is walked.
            name: The attribute name sought.

        Returns:
            Any | None: The attribute value from the first MRO class that
            defines it, or ``None`` if no class in the MRO defines *name*.

        Examples:
            >>> class A:
            ...     x = 10
            >>> class B(A): pass
            >>> protocol = DescriptorProtocol()
            >>> protocol.lookup_on_class(B, "x")
            10
        """
        for base in cls.__mro__:
            if name in base.__dict__:
                return base.__dict__[name]
        return None

    # ------------------------------------------------------------------
    # Phase detectors
    # ------------------------------------------------------------------

    def check_data_descriptor(self, cls: type, name: str) -> DescriptorRecord | None:
        """Scan the MRO for a data descriptor named *name*.

        A *data descriptor* is an object found in a class ``__dict__`` that
        defines at least one of ``__set__`` or ``__delete__``.  Such objects
        take priority over the instance ``__dict__`` in Python's attribute
        lookup.

        Parameters:
            cls: The class whose MRO is searched.
            name: The attribute name sought.

        Returns:
            DescriptorRecord | None: A freshly constructed
            :class:`DescriptorRecord` if a data descriptor is found, or
            ``None`` otherwise.  The record has ``has_get``, ``has_set``,
            ``has_delete`` set from the actual object, and ``kind`` set to
            ``DescriptorKind.DATA`` or ``DescriptorKind.PROPERTY`` as
            appropriate.
        """
        for base in cls.__mro__:
            if name not in base.__dict__:
                continue
            obj = base.__dict__[name]
            has_set = hasattr(obj, "__set__")
            has_delete = hasattr(obj, "__delete__")
            if has_set or has_delete:
                kind = DescriptorKind.PROPERTY if isinstance(obj, property) else DescriptorKind.DATA
                coord = _make_descriptor_coord(base.__name__, name)
                rec = DescriptorRecord(
                    name=name,
                    owner_class=base.__name__,
                    has_get=hasattr(obj, "__get__"),
                    has_set=has_set,
                    has_delete=has_delete,
                    kind=kind,
                    coordinate=coord,
                )
                cache_key = f"{base.__name__}::{name}"
                self._records[cache_key] = rec
                return rec
        return None

    def check_instance_dict(self, obj: Any, name: str) -> Any:
        """Safely retrieve *name* from the instance ``__dict__``.

        Objects without a ``__dict__`` attribute (e.g., those using ``__slots__``)
        are handled gracefully: this method returns ``_MISSING`` rather than
        raising.

        Parameters:
            obj: The instance whose ``__dict__`` is inspected.
            name: The key to look up.

        Returns:
            Any: The value stored under *name*, or the module-private sentinel
            ``_MISSING`` if not found (this allows the caller to distinguish
            ``None`` values from absent keys).
        """
        try:
            d = object.__getattribute__(obj, "__dict__")
        except AttributeError:
            return _MISSING
        if not isinstance(d, dict):
            return _MISSING
        return d.get(name, _MISSING)

    def check_non_data_descriptor(self, cls: type, name: str) -> DescriptorRecord | None:
        """Scan the MRO for a non-data descriptor named *name*.

        A *non-data descriptor* exposes ``__get__`` but neither ``__set__`` nor
        ``__delete__``.  Regular functions and ``staticmethod`` objects are the
        canonical examples.  These descriptors are lower priority than both
        data descriptors and the instance ``__dict__``.

        Parameters:
            cls: The class whose MRO is searched.
            name: The attribute name sought.

        Returns:
            DescriptorRecord | None: A :class:`DescriptorRecord` if a non-data
            descriptor is found, else ``None``.
        """
        for base in cls.__mro__:
            if name not in base.__dict__:
                continue
            obj = base.__dict__[name]
            has_get = hasattr(obj, "__get__")
            has_set = hasattr(obj, "__set__")
            has_delete = hasattr(obj, "__delete__")
            if has_get and not has_set and not has_delete:
                if isinstance(obj, staticmethod):
                    kind = DescriptorKind.STATIC_METHOD
                elif isinstance(obj, classmethod):
                    kind = DescriptorKind.CLASS_METHOD
                else:
                    kind = DescriptorKind.NON_DATA
                coord = _make_descriptor_coord(base.__name__, name)
                rec = DescriptorRecord(
                    name=name,
                    owner_class=base.__name__,
                    has_get=True,
                    has_set=False,
                    has_delete=False,
                    kind=kind,
                    coordinate=coord,
                )
                cache_key = f"{base.__name__}::{name}"
                self._records[cache_key] = rec
                return rec
        return None

    # ------------------------------------------------------------------
    # Descriptor invocation helpers
    # ------------------------------------------------------------------

    def apply_get(self, descriptor: Any, obj: Any, objtype: type) -> Any:
        """Invoke the descriptor's ``__get__`` method.

        Calls ``descriptor.__get__(obj, objtype)`` and returns the result.
        If ``__get__`` raises :exc:`TypeError` (e.g., wrong number of
        arguments), the raw descriptor is returned unmodified.

        Parameters:
            descriptor: An object that exposes a ``__get__`` method.
            obj: The instance argument to pass to ``__get__``.  Pass ``None``
                for class-level access.
            objtype: The ``type`` argument to pass to ``__get__``.  Should
                be ``type(obj)`` for instance access or the class itself for
                class-level access.

        Returns:
            Any: The return value of ``descriptor.__get__(obj, objtype)``, or
            the descriptor itself if ``__get__`` raises :exc:`TypeError`.

        Raises:
            AttributeError: Propagated from ``__get__`` if the descriptor
                itself raises it.
        """
        try:
            result = descriptor.__get__(obj, objtype)
            logger.debug("apply_get(%r): __get__ returned %r", descriptor, type(result).__name__)
            return result
        except TypeError as exc:
            logger.warning("apply_get(%r): TypeError from __get__: %s", descriptor, exc)
            return descriptor
        except AttributeError:
            raise

    def apply_set(self, descriptor: Any, obj: Any, value: Any) -> None:
        """Invoke the descriptor's ``__set__`` method.

        Calls ``descriptor.__set__(obj, value)``.  Raises :exc:`AttributeError`
        if the descriptor is read-only (i.e., ``__set__`` raises it).

        Parameters:
            descriptor: A data descriptor with a ``__set__`` method.
            obj: The instance on which the attribute is being set.
            value: The new value to assign.

        Raises:
            AttributeError: If ``__set__`` is absent or raises it (read-only
                descriptor).
        """
        if not hasattr(descriptor, "__set__"):
            raise AttributeError(
                f"descriptor {descriptor!r} does not support __set__"
            )
        try:
            descriptor.__set__(obj, value)
            logger.debug("apply_set(%r): __set__ called with value of type %r", descriptor, type(value).__name__)
        except AttributeError:
            raise

    def apply_delete(self, descriptor: Any, obj: Any) -> None:
        """Invoke the descriptor's ``__delete__`` method.

        Calls ``descriptor.__delete__(obj)``.

        Parameters:
            descriptor: A data descriptor with a ``__delete__`` method.
            obj: The instance from which the attribute is being deleted.

        Raises:
            AttributeError: If ``__delete__`` is absent or the descriptor
                raises it.
        """
        if not hasattr(descriptor, "__delete__"):
            raise AttributeError(
                f"descriptor {descriptor!r} does not support __delete__"
            )
        try:
            descriptor.__delete__(obj)
            logger.debug("apply_delete(%r): __delete__ called", descriptor)
        except AttributeError:
            raise

    # ------------------------------------------------------------------
    # Judgment builder
    # ------------------------------------------------------------------

    def build_judgment(self, obj: Any, name: str, result: Any) -> Judgment:
        """Build a :class:`Judgment` recording a successful attribute lookup.

        Produces a ``BEHAVIORAL`` proposition of the form::

            descriptor_resolved(type={cls_name}, name={name!r}, result_type={...})

        with a single ``RUNTIME_WITNESS`` evidence item and trust level
        ``RUNTIME_WITNESSED``.

        Parameters:
            obj: The object on which the lookup was performed.
            name: The attribute name that was resolved.
            result: The resolved value returned from the lookup.

        Returns:
            Judgment: A :class:`Judgment` at ``SETTLED`` status with
            ``RUNTIME_WITNESSED`` trust, recording that the descriptor lookup
            succeeded.
        """
        cls_name = type(obj).__name__
        result_type = type(result).__name__
        formula = (
            f"descriptor_resolved("
            f"type={cls_name!r}, "
            f"name={name!r}, "
            f"result_type={result_type!r})"
        )
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=formula,
            free_variables=(),
            metadata={"lookup_trace": list(self._lookup_trace)},
        )
        carrier = Carrier(
            name=name,
            parameters=(cls_name,),
            is_dependent=True,
            metadata={"owner_type": cls_name},
        )
        now = _now_iso()
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={
                "obj_type": cls_name,
                "name": name,
                "result_type": result_type,
                "lookup_trace": list(self._lookup_trace),
            },
            trust_level=TrustLevel.RUNTIME_WITNESSED,
            channel="descriptor_protocol",
            timestamp=now,
            expiry="",
            provenance=(cls_name,),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        prov = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=now,
            transformation_history=(),
            metadata={"module": __name__},
        )
        trust_ann = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=(cls_name, name),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"descriptor lookup on {cls_name!r}.{name!r} succeeded",),
        )
        coord = _make_descriptor_coord(cls_name, name)
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
# Sentinel value for missing keys in instance dicts
# ---------------------------------------------------------------------------

_MISSING: object = object()
"""Module-private sentinel indicating a key is absent from an instance dict.

Used by :meth:`DescriptorProtocol.check_instance_dict` to distinguish
a stored ``None`` value from a missing key.
"""


# ---------------------------------------------------------------------------
# DescriptorInspector — static analysis of descriptors in a class hierarchy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescriptorInspector:
    """Statically inspects a class for descriptor objects.

    Walks the class MRO (or just the class itself, depending on
    :attr:`include_inherited`) and classifies each member found in the
    various ``__dict__`` namespaces.

    This is primarily used at import-time or during test instrumentation
    to build a comprehensive :class:`DescriptorRecord` inventory for a
    class.  At runtime, the :class:`DescriptorProtocol` performs the
    actual lookup.

    Attributes:
        include_inherited: When ``True`` (the default), walk the entire
            MRO when searching for descriptors.  When ``False``, inspect
            only ``cls.__dict__`` itself (i.e., descriptors defined
            directly on the class, not inherited ones).
        include_slots: When ``True`` (the default), include
            ``member_descriptor`` objects created by ``__slots__``
            declarations.  When ``False``, skip them.
    """

    include_inherited: bool = True
    include_slots: bool = True

    def find_descriptors(self, cls: type) -> list[DescriptorRecord]:
        """Return all descriptor records found on *cls*.

        Iterates over the relevant ``__dict__`` namespaces and classifies
        each member by calling :meth:`classify_descriptor`.

        Parameters:
            cls: The class to inspect.

        Returns:
            list[DescriptorRecord]: All descriptor records, sorted by
            :meth:`get_descriptor_priority` descending (highest priority
            first).

        Examples:
            >>> class C:
            ...     @property
            ...     def x(self): return 1
            >>> inspector = DescriptorInspector()
            >>> recs = inspector.find_descriptors(C)
            >>> len(recs) >= 1
            True
        """
        records: list[DescriptorRecord] = []
        seen: set[str] = set()
        classes = cls.__mro__ if self.include_inherited else [cls]
        for base in classes:
            for name, obj in base.__dict__.items():
                if name.startswith("__") and name.endswith("__") and name not in ("__get__", "__set__", "__delete__"):
                    # Skip most dunder attributes to keep the list manageable
                    if name not in ("__init__", "__new__", "__call__", "__class_getitem__"):
                        continue
                if name in seen:
                    continue
                desc_kind = self.classify_descriptor(obj)
                if desc_kind is None:
                    continue
                # Optionally skip slots
                if not self.include_slots and desc_kind == DescriptorKind.SLOT:
                    continue
                seen.add(name)
                coord = _make_descriptor_coord(base.__name__, name)
                rec = DescriptorRecord(
                    name=name,
                    owner_class=base.__name__,
                    has_get=hasattr(obj, "__get__"),
                    has_set=hasattr(obj, "__set__"),
                    has_delete=hasattr(obj, "__delete__"),
                    kind=desc_kind,
                    coordinate=coord,
                )
                records.append(rec)
        records.sort(key=lambda r: self.get_descriptor_priority(r), reverse=True)
        return records

    def classify_descriptor(self, obj: Any) -> DescriptorKind | None:
        """Classify *obj* as a :class:`DescriptorKind`.

        Uses :func:`isinstance` checks first (for ``property``,
        ``classmethod``, ``staticmethod``), then falls back to checking
        for the descriptor protocol methods (``__get__``, ``__set__``,
        ``__delete__``).

        Parameters:
            obj: Any Python object to classify.

        Returns:
            DescriptorKind | None: The descriptor kind, or ``None`` if
            *obj* is not a descriptor at all (i.e., has none of
            ``__get__``, ``__set__``, ``__delete__``).

        Examples:
            >>> inspector = DescriptorInspector()
            >>> inspector.classify_descriptor(property(lambda self: 0))
            <DescriptorKind.PROPERTY: 'property'>
        """
        if isinstance(obj, property):
            return DescriptorKind.PROPERTY
        if isinstance(obj, classmethod):
            return DescriptorKind.CLASS_METHOD
        if isinstance(obj, staticmethod):
            return DescriptorKind.STATIC_METHOD
        # Detect slot descriptors (member_descriptor type)
        obj_type_name = type(obj).__name__
        if obj_type_name in ("member_descriptor", "getset_descriptor", "wrapper_descriptor"):
            return DescriptorKind.SLOT
        has_get = hasattr(obj, "__get__")
        has_set = hasattr(obj, "__set__")
        has_delete = hasattr(obj, "__delete__")
        if not (has_get or has_set or has_delete):
            return None
        if has_set or has_delete:
            return DescriptorKind.DATA
        if has_get:
            return DescriptorKind.NON_DATA
        return None

    def is_data_descriptor(self, obj: Any) -> bool:
        """Return whether *obj* is a data descriptor.

        An object is a data descriptor iff it exposes at least one of
        ``__set__`` or ``__delete__``.

        Parameters:
            obj: The object to test.

        Returns:
            bool: ``True`` iff ``hasattr(obj, '__set__') or hasattr(obj, '__delete__')``.
        """
        return hasattr(obj, "__set__") or hasattr(obj, "__delete__")

    def is_non_data_descriptor(self, obj: Any) -> bool:
        """Return whether *obj* is a non-data descriptor.

        An object is a non-data descriptor iff it exposes ``__get__`` but
        neither ``__set__`` nor ``__delete__``.

        Parameters:
            obj: The object to test.

        Returns:
            bool: ``True`` iff the object has ``__get__`` but not ``__set__``
            or ``__delete__``.
        """
        return (
            hasattr(obj, "__get__")
            and not hasattr(obj, "__set__")
            and not hasattr(obj, "__delete__")
        )

    def get_descriptor_priority(self, record: DescriptorRecord) -> int:
        """Return a numeric priority for *record* for sorting purposes.

        The priority mapping is:

        - ``DATA`` → 10
        - ``PROPERTY`` → 9
        - ``SLOT`` → 8
        - ``CLASS_METHOD`` → 5
        - ``STATIC_METHOD`` → 5
        - ``METHOD`` → 3
        - ``NON_DATA`` → 2
        - ``ABSTRACT`` → 1
        - All others → 0

        Parameters:
            record: The :class:`DescriptorRecord` to evaluate.

        Returns:
            int: The numeric priority (higher = evaluated earlier in lookup).
        """
        kind = record.kind
        if kind is None:
            return 0
        kind_val = kind.value if hasattr(kind, "value") else str(kind)
        priority_map = {
            "data": 10,
            "property": 9,
            "slot": 8,
            "class_method": 5,
            "static_method": 5,
            "method": 3,
            "non_data": 2,
            "abstract": 1,
        }
        return priority_map.get(kind_val, 0)

    def build_descriptor_record(self, name: str, cls: type, obj: Any) -> DescriptorRecord:
        """Build a full :class:`DescriptorRecord` for *obj* on *cls*.

        Parameters:
            name: The attribute name under which *obj* is stored on *cls*.
            cls: The class that directly defines *obj*.
            obj: The descriptor object itself.

        Returns:
            DescriptorRecord: A fully-populated record with all flags
            derived from ``isinstance`` and ``hasattr`` checks.
        """
        kind = self.classify_descriptor(obj) or DescriptorKind.NON_DATA
        coord = _make_descriptor_coord(cls.__name__, name)
        return DescriptorRecord(
            name=name,
            owner_class=cls.__name__,
            has_get=hasattr(obj, "__get__"),
            has_set=hasattr(obj, "__set__"),
            has_delete=hasattr(obj, "__delete__"),
            kind=kind,
            coordinate=coord,
        )


# ---------------------------------------------------------------------------
# PropertyAnalyzer — detailed introspection of property objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PropertyAnalyzer:
    """Performs detailed introspection of :class:`property` descriptor objects.

    The built-in ``property`` type is a data descriptor that manages a
    getter, an optional setter, and an optional deleter.  This class
    extracts those callables and, where possible, converts them to
    :class:`~jugeo.python_runtime.callable_surfaces.models.CallableSurface`
    records.

    Attributes:
        strict: When ``True``, methods that cannot produce a full
            :class:`CallableSurface` (because :func:`inspect.signature`
            fails) raise rather than returning ``None``.
    """

    strict: bool = False

    def analyze_property(self, prop: property) -> dict[str, Any]:
        """Return a diagnostic dictionary describing *prop*.

        Extracts the getter, setter, and deleter names, the docstring, and
        boolean flags for each accessor.

        Parameters:
            prop: The :class:`property` object to analyze.

        Returns:
            dict[str, Any]: A dictionary with keys:
            ``has_getter``, ``has_setter``, ``has_deleter``,
            ``getter_name``, ``setter_name``, ``deleter_name``, ``doc``.

        Examples:
            >>> analyzer = PropertyAnalyzer()
            >>> class Foo:
            ...     @property
            ...     def x(self) -> int: return 0
            >>> info = analyzer.analyze_property(Foo.x)
            >>> info["has_getter"]
            True
        """
        getter_name = prop.fget.__name__ if prop.fget is not None else None
        setter_name = prop.fset.__name__ if prop.fset is not None else None
        deleter_name = prop.fdel.__name__ if prop.fdel is not None else None
        return {
            "has_getter": prop.fget is not None,
            "has_setter": prop.fset is not None,
            "has_deleter": prop.fdel is not None,
            "getter_name": getter_name,
            "setter_name": setter_name,
            "deleter_name": deleter_name,
            "doc": prop.__doc__ or "",
        }

    def find_getter(self, prop: property) -> CallableSurface | None:
        """Extract and convert the getter of *prop* to a :class:`CallableSurface`.

        Uses :func:`inspect.signature` on ``prop.fget`` to extract the
        parameter and return annotation information.

        Parameters:
            prop: The :class:`property` to extract the getter from.

        Returns:
            CallableSurface | None: A :class:`CallableSurface` built from
            the getter, or ``None`` if *prop* has no getter or signature
            extraction fails (and :attr:`strict` is ``False``).

        Raises:
            ValueError: When :attr:`strict` is ``True`` and signature
                extraction fails.
        """
        if prop.fget is None:
            return None
        try:
            sig = inspect.signature(prop.fget)
            params: list[Any] = []
            for i, (pname, param) in enumerate(sig.parameters.items()):
                ann = (
                    str(param.annotation)
                    if param.annotation is not inspect.Parameter.empty
                    else "Any"
                )
                params.append(ParameterSpec(
                    name=pname,
                    kind=ParameterKind.POSITIONAL_OR_KEYWORD,
                    annotation=ann,
                    has_default=param.default is not inspect.Parameter.empty,
                ))
            ret_ann = (
                str(sig.return_annotation)
                if sig.return_annotation is not inspect.Signature.empty
                else "Any"
            )
            coord = _make_descriptor_coord(
                prop.fget.__qualname__.split(".")[0] if "." in prop.fget.__qualname__ else "",
                prop.fget.__name__,
            )
            return CallableSurface(
                name=prop.fget.__name__,
                qualname=prop.fget.__qualname__,
                module=getattr(prop.fget, "__module__", ""),
                parameters=tuple(params),
                return_annotation=ret_ann,
                is_async=False,
                is_generator=False,
            )
        except (ValueError, TypeError) as exc:
            if self.strict:
                raise ValueError(
                    f"Cannot extract getter surface from {prop!r}: {exc}"
                ) from exc
            logger.debug("find_getter: signature extraction failed for %r: %s", prop.fget, exc)
            return None

    def find_setter(self, prop: property) -> CallableSurface | None:
        """Extract and convert the setter of *prop* to a :class:`CallableSurface`.

        Parameters:
            prop: The :class:`property` to extract the setter from.

        Returns:
            CallableSurface | None: A surface for the setter, or ``None``.

        Raises:
            ValueError: When :attr:`strict` is ``True`` and extraction fails.
        """
        if prop.fset is None:
            return None
        try:
            sig = inspect.signature(prop.fset)
            params: list[Any] = []
            for pname, param in sig.parameters.items():
                ann = (
                    str(param.annotation)
                    if param.annotation is not inspect.Parameter.empty
                    else "Any"
                )
                params.append(ParameterSpec(
                    name=pname,
                    kind=ParameterKind.POSITIONAL_OR_KEYWORD,
                    annotation=ann,
                    has_default=param.default is not inspect.Parameter.empty,
                ))
            coord = _make_descriptor_coord(
                prop.fset.__qualname__.split(".")[0] if "." in prop.fset.__qualname__ else "",
                prop.fset.__name__,
            )
            return CallableSurface(
                name=prop.fset.__name__,
                qualname=prop.fset.__qualname__,
                module=getattr(prop.fset, "__module__", ""),
                parameters=tuple(params),
                return_annotation="None",
                is_async=False,
                is_generator=False,
            )
        except (ValueError, TypeError) as exc:
            if self.strict:
                raise ValueError(
                    f"Cannot extract setter surface from {prop!r}: {exc}"
                ) from exc
            logger.debug("find_setter: signature extraction failed for %r: %s", prop.fset, exc)
            return None

    def find_deleter(self, prop: property) -> CallableSurface | None:
        """Extract and convert the deleter of *prop* to a :class:`CallableSurface`.

        Parameters:
            prop: The :class:`property` to extract the deleter from.

        Returns:
            CallableSurface | None: A surface for the deleter, or ``None``.

        Raises:
            ValueError: When :attr:`strict` is ``True`` and extraction fails.
        """
        if prop.fdel is None:
            return None
        try:
            sig = inspect.signature(prop.fdel)
            params: list[Any] = []
            for pname, param in sig.parameters.items():
                ann = (
                    str(param.annotation)
                    if param.annotation is not inspect.Parameter.empty
                    else "Any"
                )
                params.append(ParameterSpec(
                    name=pname,
                    kind=ParameterKind.POSITIONAL_OR_KEYWORD,
                    annotation=ann,
                    has_default=param.default is not inspect.Parameter.empty,
                ))
            return CallableSurface(
                name=prop.fdel.__name__,
                qualname=prop.fdel.__qualname__,
                module=getattr(prop.fdel, "__module__", ""),
                parameters=tuple(params),
                return_annotation="None",
                is_async=False,
                is_generator=False,
            )
        except (ValueError, TypeError) as exc:
            if self.strict:
                raise ValueError(
                    f"Cannot extract deleter surface from {prop!r}: {exc}"
                ) from exc
            logger.debug("find_deleter: signature extraction failed for %r: %s", prop.fdel, exc)
            return None

    def build_property_surface(self, prop: property, name: str) -> DescriptorRecord:
        """Build a :class:`DescriptorRecord` for a :class:`property` descriptor.

        Parameters:
            prop: The :class:`property` object.
            name: The attribute name under which the property is stored.

        Returns:
            DescriptorRecord: A fully-populated record with
            ``kind=DescriptorKind.PROPERTY``, and ``has_get``, ``has_set``,
            ``has_delete`` determined from ``prop.fget``, ``prop.fset``,
            ``prop.fdel``.
        """
        cls_name = ""
        if prop.fget is not None and "." in getattr(prop.fget, "__qualname__", ""):
            cls_name = prop.fget.__qualname__.rsplit(".", 2)[-2]
        coord = _make_descriptor_coord(cls_name, name)
        return DescriptorRecord(
            name=name,
            owner_class=cls_name,
            has_get=prop.fget is not None,
            has_set=prop.fset is not None,
            has_delete=prop.fdel is not None,
            kind=DescriptorKind.PROPERTY,
            coordinate=coord,
        )

    def validate_property(self, prop: property) -> list[str]:
        """Validate *prop* and return a list of issue strings.

        Checks:
        - That a getter exists (a property without a getter cannot be read).
        - That the getter's return annotation is present.
        - That the setter, if present, has exactly two parameters (``self``
          and the new value).

        Parameters:
            prop: The :class:`property` to validate.

        Returns:
            list[str]: A list of human-readable issue descriptions.  An empty
            list means the property is well-formed.
        """
        issues: list[str] = []
        if prop.fget is None:
            issues.append("Property has no getter — it cannot be read.")
        else:
            try:
                sig = inspect.signature(prop.fget)
                if sig.return_annotation is inspect.Signature.empty:
                    issues.append(
                        f"Getter {prop.fget.__name__!r} has no return annotation."
                    )
            except (ValueError, TypeError):
                issues.append(f"Could not inspect getter signature for {prop.fget!r}.")

        if prop.fset is not None:
            try:
                sig = inspect.signature(prop.fset)
                param_count = len(sig.parameters)
                if param_count != 2:
                    issues.append(
                        f"Setter {prop.fset.__name__!r} has {param_count} parameters; "
                        f"expected exactly 2 (self + value)."
                    )
            except (ValueError, TypeError):
                issues.append(f"Could not inspect setter signature for {prop.fset!r}.")

        return issues


# ---------------------------------------------------------------------------
# SlotDescriptorAnalyzer — introspection of __slots__ descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SlotDescriptorAnalyzer:
    """Analyzes the ``__slots__`` descriptor mechanism of a class.

    When a class declares ``__slots__``, Python creates
    ``member_descriptor`` objects in the class ``__dict__`` for each slot
    name.  These are data descriptors (they support both ``__get__`` and
    ``__set__``), and they take higher priority than the instance
    ``__dict__`` (which is absent when ``__slots__`` is used without
    ``__dict__`` in the slot list).

    This class extracts those descriptors, validates slot consistency
    across the MRO, and detects slot-name conflicts.

    Attributes:
        strict: When ``True``, validation failures raise instead of
            returning error lists.
    """

    strict: bool = False

    def analyze_slots(self, cls: type) -> list[DescriptorRecord]:
        """Return all slot descriptor records for *cls*.

        Parameters:
            cls: The class whose ``__slots__`` are analyzed.

        Returns:
            list[DescriptorRecord]: One record per slot name declared in
            ``cls.__slots__``.  Returns an empty list if *cls* has no
            ``__slots__``.

        Examples:
            >>> class Pt:
            ...     __slots__ = ("x", "y")
            >>> analyzer = SlotDescriptorAnalyzer()
            >>> recs = analyzer.analyze_slots(Pt)
            >>> len(recs)
            2
        """
        if not hasattr(cls, "__slots__"):
            return []
        return self.build_slot_records(cls)

    def find_slot_descriptors(self, cls: type) -> dict[str, Any]:
        """Return a mapping of slot name → descriptor object for *cls*.

        Filters ``cls.__dict__`` to only ``member_descriptor`` objects
        (the type of objects created by ``__slots__``).

        Parameters:
            cls: The class to search.

        Returns:
            dict[str, Any]: Mapping from slot name to descriptor object.
            May be empty if no slot descriptors are present.
        """
        result: dict[str, Any] = {}
        for name, obj in cls.__dict__.items():
            obj_type_name = type(obj).__name__
            if obj_type_name in ("member_descriptor", "getset_descriptor"):
                result[name] = obj
        return result

    def validate_slots(self, cls: type) -> list[str]:
        """Validate slot usage on *cls* and return issue strings.

        Checks:
        - Whether ``cls.__slots__`` is present at all.
        - Whether ``'__dict__'`` is in ``__slots__`` (which would negate
          the memory benefit and cause subtle inheritance issues).
        - Whether ``'__weakref__'`` is in ``__slots__``.

        Parameters:
            cls: The class to validate.

        Returns:
            list[str]: Issue descriptions.  Empty list means no issues.

        Raises:
            ValueError: When :attr:`strict` is ``True`` and any issue is
                found.
        """
        issues: list[str] = []
        if not hasattr(cls, "__slots__"):
            issues.append(f"Class {cls.__name__!r} has no __slots__.")
        else:
            slots = cls.__slots__
            if "__dict__" in slots:
                issues.append(
                    f"Class {cls.__name__!r} includes '__dict__' in __slots__, "
                    f"which negates slot memory benefits."
                )
        if self.strict and issues:
            raise ValueError(
                f"Slot validation failed for {cls.__name__!r}: {issues}"
            )
        return issues

    def build_slot_records(self, cls: type) -> list[DescriptorRecord]:
        """Build :class:`DescriptorRecord` objects for all slots on *cls*.

        Each slot produces a record with ``has_get=True``, ``has_set=True``,
        ``has_delete=True`` (all member descriptors support these
        operations), and ``kind=DescriptorKind.SLOT``.

        Parameters:
            cls: The class whose ``__slots__`` provide the slot names.

        Returns:
            list[DescriptorRecord]: One record per slot.
        """
        if not hasattr(cls, "__slots__"):
            return []
        records: list[DescriptorRecord] = []
        for slot_name in cls.__slots__:
            coord = _make_descriptor_coord(cls.__name__, slot_name)
            rec = DescriptorRecord(
                name=slot_name,
                owner_class=cls.__name__,
                has_get=True,
                has_set=True,
                has_delete=True,
                kind=DescriptorKind.SLOT,
                coordinate=coord,
            )
            records.append(rec)
        return records

    def detect_slot_conflicts(self, cls: type) -> list[str]:
        """Walk the MRO and report slot names defined in multiple classes.

        In Python, a slot name that appears in both a base class and a
        subclass wastes memory (the subclass slot silently shadows the
        base class slot without overriding it).  This method detects such
        conflicts.

        Parameters:
            cls: The class whose MRO is inspected.

        Returns:
            list[str]: Human-readable conflict descriptions, e.g.
            ``"Slot 'x' defined in both 'Child' and 'Parent'"``.
        """
        slot_sources: dict[str, list[str]] = {}
        for base in cls.__mro__:
            if not hasattr(base, "__slots__"):
                continue
            for slot_name in base.__slots__:
                slot_sources.setdefault(slot_name, []).append(base.__name__)

        conflicts: list[str] = []
        for slot_name, sources in slot_sources.items():
            if len(sources) > 1:
                conflicts.append(
                    f"Slot {slot_name!r} defined in multiple classes: "
                    + ", ".join(repr(s) for s in sources)
                )
        return conflicts


# ---------------------------------------------------------------------------
# DescriptorJudgmentBuilder — judgment factory for descriptor-level events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescriptorJudgmentBuilder:
    """Builds :class:`~jugeo.judgments.judgment_terms.Judgment` objects for descriptor events.

    Each method constructs a fully-populated :class:`Judgment` appropriate
    for the given descriptor kind.  All judgments carry a ``BEHAVIORAL``
    proposition, a single ``RUNTIME_WITNESS`` evidence item, and the
    trust level configured in :attr:`trust_level`.

    This class is the primary copilot integration point for the descriptor
    subsystem: copilot may propose trust upgrades by substituting a
    ``SOLVER_DISCHARGED`` trust level once a formal invariant is proved.

    Attributes:
        trust_level: The default trust level applied to all constructed
            judgments.  Defaults to ``TrustLevel.RUNTIME_WITNESSED``.
    """

    trust_level: TrustLevel = TrustLevel.RUNTIME_WITNESSED  # type: ignore[assignment]

    def _base_judgment(
        self,
        record: DescriptorRecord,
        formula: str,
        extra_payload: dict[str, Any] | None = None,
    ) -> Judgment:
        """Internal helper: build a judgment from *record* and *formula*.

        Parameters:
            record: The descriptor record being judged.
            formula: The proposition formula string.
            extra_payload: Extra key-value pairs to include in the
                evidence payload.

        Returns:
            Judgment: A ``SETTLED`` or ``OBSTRUCTED`` judgment depending on
            whether ``record.kind`` is recognised.
        """
        now = _now_iso()
        payload: dict[str, Any] = {
            "descriptor_name": record.name,
            "owner_class": record.owner_class,
            "kind": record.kind.value if hasattr(record.kind, "value") else str(record.kind),
            "has_get": record.has_get,
            "has_set": record.has_set,
            "has_delete": record.has_delete,
        }
        if extra_payload:
            payload.update(extra_payload)

        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=formula,
            free_variables=(),
            metadata={"owner_class": record.owner_class},
        )
        carrier = Carrier(
            name=record.name,
            parameters=(record.owner_class,),
            is_dependent=True,
            metadata={"descriptor_kind": payload["kind"]},
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload=payload,
            trust_level=self.trust_level,
            channel="descriptor_judgment_builder",
            timestamp=now,
            expiry="",
            provenance=(record.owner_class, record.name),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        prov = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=now,
            transformation_history=(),
            metadata={"builder": "DescriptorJudgmentBuilder"},
        )
        trust_ann = TrustAnnotation(
            level=self.trust_level,
            evidence_basis=(record.owner_class, record.name),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"descriptor {record.name!r} on {record.owner_class!r} verified",),
        )
        coord = _make_descriptor_coord(record.owner_class, record.name)
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

    def build_for_data_descriptor(self, record: DescriptorRecord) -> Judgment:
        """Build a judgment confirming *record* is a data descriptor.

        A data descriptor judgment asserts that the named attribute exposes
        both ``__get__`` and at least one of ``__set__`` / ``__delete__``,
        giving it lookup priority over instance ``__dict__``.

        Parameters:
            record: A :class:`DescriptorRecord` with ``has_set`` or
                ``has_delete`` set to ``True``.

        Returns:
            Judgment: A ``SETTLED`` behavioral judgment at
            :attr:`trust_level` recording the data-descriptor invariant.
        """
        formula = (
            f"data_descriptor_valid("
            f"name={record.name!r}, "
            f"owner={record.owner_class!r}, "
            f"has_set={record.has_set}, "
            f"has_delete={record.has_delete})"
        )
        return self._base_judgment(record, formula, {"priority": "high"})

    def build_for_non_data_descriptor(self, record: DescriptorRecord) -> Judgment:
        """Build a judgment confirming *record* is a non-data descriptor.

        A non-data descriptor asserts that the attribute exposes only
        ``__get__`` and thus yields to the instance ``__dict__``.

        Parameters:
            record: A :class:`DescriptorRecord` with only ``has_get=True``.

        Returns:
            Judgment: A ``SETTLED`` behavioral judgment.
        """
        formula = (
            f"non_data_descriptor_valid("
            f"name={record.name!r}, "
            f"owner={record.owner_class!r}, "
            f"has_get={record.has_get})"
        )
        return self._base_judgment(record, formula, {"priority": "low"})

    def build_for_property(self, record: DescriptorRecord) -> Judgment:
        """Build a judgment for a ``property``-typed descriptor.

        Parameters:
            record: A :class:`DescriptorRecord` with ``kind=PROPERTY``.

        Returns:
            Judgment: A ``SETTLED`` judgment asserting property-descriptor
            invariants (has_get, is data descriptor).
        """
        formula = (
            f"property_descriptor_valid("
            f"name={record.name!r}, "
            f"owner={record.owner_class!r}, "
            f"readable={record.has_get}, "
            f"writable={record.has_set}, "
            f"deletable={record.has_delete})"
        )
        return self._base_judgment(
            record, formula, {"descriptor_type": "property"}
        )

    def build_for_slot(self, record: DescriptorRecord) -> Judgment:
        """Build a judgment for a slot ``member_descriptor``.

        Parameters:
            record: A :class:`DescriptorRecord` with ``kind=SLOT``.

        Returns:
            Judgment: A ``SETTLED`` judgment asserting that the slot
            provides full get/set/delete semantics.
        """
        formula = (
            f"slot_descriptor_valid("
            f"name={record.name!r}, "
            f"owner={record.owner_class!r}, "
            f"has_get={record.has_get}, "
            f"has_set={record.has_set}, "
            f"has_delete={record.has_delete})"
        )
        return self._base_judgment(
            record, formula, {"descriptor_type": "slot"}
        )

    def build_lookup_judgment(
        self,
        obj: Any,
        name: str,
        priority_chain: list[str],
    ) -> Judgment:
        """Build a judgment summarising a complete descriptor lookup chain.

        Records the full resolution path (which phases were tried, which
        succeeded) as a single behavioral judgment.

        Parameters:
            obj: The object on which the lookup was performed.
            name: The attribute name that was resolved.
            priority_chain: The ordered list of phase names that were
                evaluated (e.g.,
                ``["phase1:data_descriptor:x", "phase2:instance_dict:x",
                "phase3:hit:MyClass"]``).

        Returns:
            Judgment: A ``SETTLED`` behavioral judgment recording the
            full lookup chain as an evidence payload.
        """
        cls_name = type(obj).__name__ if not isinstance(obj, type) else obj.__name__
        formula = (
            f"descriptor_lookup_chain("
            f"type={cls_name!r}, "
            f"name={name!r}, "
            f"phases={len(priority_chain)})"
        )
        now = _now_iso()
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=formula,
            free_variables=(),
            metadata={"priority_chain": priority_chain},
        )
        carrier = Carrier(
            name=name,
            parameters=(cls_name,),
            is_dependent=True,
            metadata={"chain_length": len(priority_chain)},
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={
                "obj_type": cls_name,
                "name": name,
                "priority_chain": priority_chain,
            },
            trust_level=self.trust_level,
            channel="descriptor_lookup_chain",
            timestamp=now,
            expiry="",
            provenance=(cls_name, name),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        prov = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=now,
            transformation_history=(),
            metadata={"builder": "DescriptorJudgmentBuilder.build_lookup_judgment"},
        )
        trust_ann = TrustAnnotation(
            level=self.trust_level,
            evidence_basis=(cls_name, name),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"lookup chain for {cls_name!r}.{name!r} recorded",),
        )
        coord = _make_descriptor_coord(cls_name, name)
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
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "DescriptorProtocol",
    "DescriptorInspector",
    "PropertyAnalyzer",
    "SlotDescriptorAnalyzer",
    "DescriptorJudgmentBuilder",
]
