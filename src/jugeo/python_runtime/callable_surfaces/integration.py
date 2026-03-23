"""Integration of callable surface analysis with the JuGeo framework.

Provides Judgment emission, Z3 encoding, coordinate mapping, support region
building, and copilot integration for callable analysis.  References
theory2.tex Ch16.  All bridge classes connect the callable_surfaces analysis
layer to the judgment, geometry, and solver subsystems.

Integration architecture
-------------------------
This module follows a strict one-directional dependency rule: the
callable_surfaces package may *import* from the geometry, judgments, and
solver subsystems but must never be imported by them.  All cross-package
imports are guarded by ``try/except ImportError`` blocks so that this module
is usable in isolation when the broader JuGeo installation is absent.

Five bridge classes are provided:

* :class:`CallableJudgmentEmitter` — emit typed :class:`Judgment` records from
  callable surface analysis results.
* :class:`Z3CallableEncoder` — encode callable constraints as Z3 / SMT-LIB2
  formulas and discharge them via a :class:`Z3Session`.
* :class:`CallableCoordinateMapper` — map Python callables to
  :class:`CoordinateObject` positions in the JuGeo semantic site.
* :class:`SupportRegionBuilder` — build :class:`SupportRegion` objects that
  record where callable analysis results are supported.
* :class:`CopilotCallableAdvisor` — provide copilot-style suggestions,
  explanations, and human-readable reports for callable surfaces.

This module is a key copilot integration point: the advisor class exposes
structured advisory data that downstream LLM-orchestration layers can
consume directly to produce actionable code suggestions.
"""

from __future__ import annotations

import inspect
import logging
import time
import types
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded imports – callable_surfaces.models
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.callable_surfaces.models import (  # type: ignore[import]
        BoundMethod,
        CallableSurface,
        ClassConstruction,
        DescriptorKind,
        DescriptorRecord,
        MethodBinding,
        ParameterKind,
        ParameterSpec,
        SignatureRecord,
    )
except ImportError:
    _log.debug("callable_surfaces.models unavailable – activating integration stubs")

    class ParameterKind:  # type: ignore[no-redef]
        POSITIONAL_ONLY = "POSITIONAL_ONLY"
        POSITIONAL_OR_KEYWORD = "POSITIONAL_OR_KEYWORD"
        VAR_POSITIONAL = "VAR_POSITIONAL"
        KEYWORD_ONLY = "KEYWORD_ONLY"
        VAR_KEYWORD = "VAR_KEYWORD"

    _EMPTY = inspect.Parameter.empty

    @dataclass(frozen=True, slots=True)
    class ParameterSpec:  # type: ignore[no-redef]
        """Stub: single callable parameter descriptor."""

        name: str
        kind: str
        annotation: Any = inspect.Parameter.empty
        has_default: bool = False
        default_value: Any = inspect.Parameter.empty

        def serialize(self) -> dict[str, Any]:
            return {"name": self.name, "kind": self.kind, "has_default": self.has_default}

        @classmethod
        def parse(cls, data: dict[str, Any]) -> ParameterSpec:
            return cls(
                name=data["name"],
                kind=data.get("kind", "POSITIONAL_OR_KEYWORD"),
                has_default=data.get("has_default", False),
            )

    @dataclass(frozen=True, slots=True)
    class CallableSurface:  # type: ignore[no-redef]
        """Stub: immutable descriptor of a Python callable's public surface."""

        name: str
        qualname: str
        parameters: tuple[ParameterSpec, ...]
        return_annotation: Any
        is_async: bool = False
        is_generator: bool = False
        is_coroutine: bool = False
        module: str = ""
        docstring: str | None = None
        source_file: str | None = None
        lineno: int | None = None
        closure_vars: tuple[str, ...] = ()
        decorators: tuple[str, ...] = ()

        def serialize(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "qualname": self.qualname,
                "parameters": [p.serialize() for p in self.parameters],
                "return_annotation": repr(self.return_annotation),
                "is_async": self.is_async,
                "is_generator": self.is_generator,
                "module": self.module,
                "docstring": self.docstring,
                "source_file": self.source_file,
                "lineno": self.lineno,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> CallableSurface:
            params = tuple(ParameterSpec.parse(p) for p in data.get("parameters", []))
            return cls(
                name=data["name"],
                qualname=data.get("qualname", data["name"]),
                parameters=params,
                return_annotation=inspect.Parameter.empty,
                module=data.get("module", ""),
            )

    @dataclass(frozen=True, slots=True)
    class MethodBinding:  # type: ignore[no-redef]
        """Stub: binding record for a method on a class."""

        method_name: str
        owner_class: str
        defined_in: str
        is_classmethod: bool = False
        is_staticmethod: bool = False
        is_abstractmethod: bool = False
        surface: Any = None

        def serialize(self) -> dict[str, Any]:
            return {
                "method_name": self.method_name,
                "owner_class": self.owner_class,
                "defined_in": self.defined_in,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> MethodBinding:
            return cls(
                method_name=data["method_name"],
                owner_class=data.get("owner_class", ""),
                defined_in=data.get("defined_in", ""),
            )

    class DescriptorKind:  # type: ignore[no-redef]
        DATA = "DATA"
        NON_DATA = "NON_DATA"
        OVERRIDING = "OVERRIDING"
        VIRTUAL = "VIRTUAL"

    @dataclass(frozen=True, slots=True)
    class DescriptorRecord:  # type: ignore[no-redef]
        """Stub: describes a descriptor found in a class hierarchy."""

        name: str
        kind: str
        owner: str
        has_get: bool = False
        has_set: bool = False
        has_delete: bool = False
        priority: int = 0

        def serialize(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "kind": self.kind,
                "owner": self.owner,
                "has_get": self.has_get,
                "has_set": self.has_set,
                "priority": self.priority,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> DescriptorRecord:
            return cls(
                name=data["name"],
                kind=data.get("kind", DescriptorKind.NON_DATA),
                owner=data.get("owner", ""),
            )

    @dataclass(frozen=True, slots=True)
    class BoundMethod:  # type: ignore[no-redef]
        """Stub: a callable surface bound to a concrete instance type."""

        surface: Any
        instance_type: str
        binding_id: str = ""

        def __post_init__(self) -> None:
            if not self.binding_id:
                object.__setattr__(self, "binding_id", uuid.uuid4().hex[:12])

        def serialize(self) -> dict[str, Any]:
            return {
                "surface": self.surface.serialize() if self.surface else {},
                "instance_type": self.instance_type,
                "binding_id": self.binding_id,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> BoundMethod:
            return cls(
                surface=CallableSurface.parse(data["surface"]) if "surface" in data else None,
                instance_type=data.get("instance_type", ""),
                binding_id=data.get("binding_id", uuid.uuid4().hex[:12]),
            )

    @dataclass(frozen=True, slots=True)
    class ClassConstruction:  # type: ignore[no-redef]
        """Stub: captures how a class is constructed."""

        class_name: str
        qualname: str
        bases: tuple[str, ...]
        methods: tuple[str, ...]
        metaclass: str = "type"
        is_abstract: bool = False
        is_dataclass: bool = False
        is_protocol: bool = False

        def serialize(self) -> dict[str, Any]:
            return {
                "class_name": self.class_name,
                "qualname": self.qualname,
                "bases": list(self.bases),
                "methods": list(self.methods),
                "metaclass": self.metaclass,
                "is_abstract": self.is_abstract,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> ClassConstruction:
            return cls(
                class_name=data["class_name"],
                qualname=data.get("qualname", data["class_name"]),
                bases=tuple(data.get("bases", [])),
                methods=tuple(data.get("methods", [])),
                metaclass=data.get("metaclass", "type"),
            )

    @dataclass(frozen=True, slots=True)
    class SignatureRecord:  # type: ignore[no-redef]
        """Stub: a named, possibly overloaded signature."""

        qualname: str
        parameters: tuple[ParameterSpec, ...]
        return_annotation: Any
        is_overloaded: bool = False

        def serialize(self) -> dict[str, Any]:
            return {
                "qualname": self.qualname,
                "parameters": [p.serialize() for p in self.parameters],
                "return_annotation": repr(self.return_annotation),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> SignatureRecord:
            return cls(
                qualname=data["qualname"],
                parameters=tuple(
                    ParameterSpec.parse(p) for p in data.get("parameters", [])
                ),
                return_annotation=inspect.Parameter.empty,
            )


# ---------------------------------------------------------------------------
# Guarded imports – JuGeo judgment terms
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (  # type: ignore[import]
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Judgment,
        JudgmentStatus,
        Obstruction,
        Proposition,
        PropositionKind,
        Provenance,
        ProvenanceSource,
        ResidualObligation,
        TrustAnnotation,
        TrustLevel,
    )
except ImportError:
    _log.debug("judgment_terms unavailable – using minimal stubs")

    class JudgmentStatus:  # type: ignore[no-redef]
        PROPOSED = "PROPOSED"
        CHALLENGED = "CHALLENGED"
        SETTLED = "SETTLED"
        OBSTRUCTED = "OBSTRUCTED"

    class TrustLevel:  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind:  # type: ignore[no-redef]
        STRUCTURAL = "STRUCTURAL"
        BEHAVIORAL = "BEHAVIORAL"
        RELATIONAL = "RELATIONAL"
        RESOURCE = "RESOURCE"
        SEMANTIC = "SEMANTIC"

    class EvidenceItemKind:  # type: ignore[no-redef]
        SOLVER_PROOF = "SOLVER_PROOF"
        RUNTIME_WITNESS = "RUNTIME_WITNESS"
        ORACLE_PROPOSAL = "ORACLE_PROPOSAL"
        FORMAL_PROOF = "FORMAL_PROOF"

    class ProvenanceSource:  # type: ignore[no-redef]
        SOLVER = "SOLVER"
        RUNTIME = "RUNTIME"
        ORACLE = "ORACLE"
        HUMAN = "HUMAN"
        COMPOSED = "COMPOSED"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        kind: Any
        formula: str
        free_variables: tuple[str, ...]
        metadata: dict[str, Any]

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        name: str
        parameters: tuple[str, ...]
        is_dependent: bool
        metadata: dict[str, Any]

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: Any
        payload: dict[str, Any]
        trust_level: Any
        channel: str
        timestamp: str
        expiry: str
        provenance: tuple[str, ...]

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple[EvidenceItem, ...]

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        obligation_id: str
        description: str
        required_evidence_kind: Any
        deadline: str
        priority: int
        depends_on: tuple[str, ...]
        is_discharged: bool
        discharge_evidence: str

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        obstruction_id: str
        violated_condition: str
        coordinate: Any
        cohomology_class: str
        is_resolved: bool
        resolution_evidence: str

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        level: Any
        evidence_basis: tuple[str, ...]
        ceiling: Any
        floor: Any
        reasons: tuple[str, ...]

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        source: Any
        parent_judgments: tuple[str, ...]
        creation_timestamp: str
        transformation_history: tuple[str, ...]
        metadata: dict[str, Any]

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        coordinate: Any
        proposition: Any
        carrier: Any
        evidence: Any
        obligations: tuple[Any, ...]
        obstructions: tuple[Any, ...]
        trust: Any
        provenance: Any
        clauses: tuple[Any, ...]
        status: Any


# ---------------------------------------------------------------------------
# Guarded imports – geometry (CoordinateObject / CoordinateKind / Site)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (  # type: ignore[import]
        CoordinateKind,
        CoordinateObject,
        Site,
        SiteBuilder,
    )
except ImportError:
    _log.debug("geometry.site unavailable – using coordinate stubs")

    class CoordinateKind:  # type: ignore[no-redef]
        MODULE = "MODULE"
        FUNCTION = "FUNCTION"
        INTERFACE = "INTERFACE"
        TEST = "TEST"
        THEOREM = "THEOREM"
        REGION = "REGION"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        components: tuple[str, ...]
        kind: Any
        support_labels: frozenset[str]
        metadata: dict[str, Any]

    class Site:  # type: ignore[no-redef]
        """Stub."""

        def __init__(self) -> None:
            self._coords: list[Any] = []

        def add(self, coord: Any) -> None:
            self._coords.append(coord)

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub."""

        def __init__(self) -> None:
            self._site = Site()

        def add_coordinate(self, coord: Any) -> SiteBuilder:
            self._site.add(coord)
            return self

        def build(self) -> Site:
            return self._site


# ---------------------------------------------------------------------------
# Guarded imports – geometry (SupportRegion / SupportSet)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.supports import (  # type: ignore[import]
        SupportRegion,
        SupportSet,
        SupportTracker,
    )
except ImportError:
    _log.debug("geometry.supports unavailable – using support stubs")

    @dataclass(frozen=True, slots=True)
    class SupportRegion:  # type: ignore[no-redef]
        """Stub: backward-compatible region where analysis results are supported."""

        coordinate: Any
        patch_keys: frozenset[str] = field(default_factory=frozenset)
        labels: frozenset[str] = field(default_factory=frozenset)
        provenance: tuple[str, ...] = ()

        def intersects(self, other: SupportRegion) -> bool:
            return bool(self.patch_keys & other.patch_keys) or bool(
                self.labels & other.labels
            )

    class SupportSet:  # type: ignore[no-redef]
        """Stub."""

        def __init__(self, coordinates: frozenset[str] = frozenset()) -> None:
            self.coordinates = coordinates

        def union(self, other: SupportSet) -> SupportSet:
            return SupportSet(self.coordinates | other.coordinates)

        def intersection(self, other: SupportSet) -> SupportSet:
            return SupportSet(self.coordinates & other.coordinates)

    class SupportTracker:  # type: ignore[no-redef]
        """Stub."""

        def __init__(self) -> None:
            self._history: list[Any] = []

        def extend(self, region: Any) -> None:
            self._history.append(region)

        def history(self) -> list[Any]:
            return list(self._history)


# ---------------------------------------------------------------------------
# Guarded imports – solver (Z3Session / Z3Formula / SolveOutcome)
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import (  # type: ignore[import]
        SolveOutcome,
        Z3Formula,
        Z3Session,
        z3_available,
    )
    _HAS_Z3_MODULE = True
except ImportError:
    _log.debug("z3_session unavailable – using solver stubs")
    _HAS_Z3_MODULE = False

    class SolveOutcome:  # type: ignore[no-redef]
        SAT = "SAT"
        UNSAT = "UNSAT"
        UNKNOWN = "UNKNOWN"
        TIMEOUT = "TIMEOUT"

    class _FormulaKindStub:
        BOOL = "BOOL"
        INT = "INT"
        REAL = "REAL"
        BITVEC = "BITVEC"
        ARRAY = "ARRAY"
        DATATYPE = "DATATYPE"

    @dataclass(frozen=True, slots=True)
    class Z3Formula:  # type: ignore[no-redef]
        """Stub: typed wrapper for an SMT-LIB2 formula expression."""

        kind: Any
        expression: str
        z3_ast: Any = None

        def serialize(self) -> dict[str, Any]:
            return {"kind": str(self.kind), "expression": self.expression}

        @classmethod
        def parse(cls, data: dict[str, Any]) -> Z3Formula:
            return cls(
                kind=data.get("kind", "BOOL"),
                expression=data.get("expression", "true"),
            )

    class Z3Session:  # type: ignore[no-redef]
        """Stub Z3 session that records assertions and always returns UNKNOWN."""

        def __init__(self) -> None:
            self._assertions: list[Z3Formula] = []
            self.session_id: str = uuid.uuid4().hex[:12]
            self.closed: bool = False

        def assert_formula(self, formula: Z3Formula) -> None:
            if not self.closed:
                self._assertions.append(formula)

        def check_sat(self) -> Any:
            return SolveOutcome.UNKNOWN

    def z3_available() -> bool:  # type: ignore[no-redef]
        return False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_EMPTY = inspect.Parameter.empty


def _coord_for_surface(surface: CallableSurface) -> CoordinateObject:
    """Build a :class:`CoordinateObject` from a :class:`CallableSurface`.

    Parameters:
        surface: The surface to locate.

    Returns:
        A :class:`CoordinateObject` with MODULE→qualname component hierarchy.
    """
    parts = tuple(
        c
        for c in (surface.module,) + tuple(surface.qualname.split("."))
        if c
    )
    return CoordinateObject(
        components=parts,
        kind=CoordinateKind.FUNCTION,
        support_labels=frozenset({"callable", "surface"}),
        metadata={"qualname": surface.qualname, "module": surface.module},
    )


def _coord_for_qualname(qualname: str, module: str = "") -> CoordinateObject:
    """Build a :class:`CoordinateObject` from a qualname string.

    Parameters:
        qualname: The dotted qualname to use as path.
        module: Optional module prefix.

    Returns:
        A :class:`CoordinateObject` for the given qualname.
    """
    parts = tuple(c for c in (module,) + tuple(qualname.split(".")) if c)
    return CoordinateObject(
        components=parts,
        kind=CoordinateKind.FUNCTION,
        support_labels=frozenset({"callable"}),
        metadata={"qualname": qualname, "module": module},
    )


# ---------------------------------------------------------------------------
# CallableJudgmentEmitter – mutable dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CallableJudgmentEmitter:
    """Emits typed :class:`Judgment` records from callable surface analysis results.

    Provides a unified interface for building judgments from the various
    structured types produced by callable surface analysis:
    :class:`CallableSurface`, :class:`BoundMethod`, :class:`DescriptorRecord`,
    and :class:`ClassConstruction`.

    All emitted judgments are accumulated in ``_emitted`` and can be retrieved
    via :meth:`all_emitted`.  The optional ``_site`` may be used by callers that
    want emitted coordinate objects registered into a live :class:`Site`.

    Attributes:
        _emitted: Accumulated list of emitted :class:`Judgment` records.
        _site: Optional JuGeo :class:`Site` to register coordinates into.
    """

    _emitted: list[Judgment] = field(default_factory=list)
    _site: Site | None = None

    # ------------------------------------------------------------------
    # Individual emitters
    # ------------------------------------------------------------------

    def emit_surface_judgment(self, surface: CallableSurface) -> Judgment:
        """Emit a structural :class:`Judgment` for a :class:`CallableSurface`.

        Captures parameter count, async/generator flags, and source location
        as a STRUCTURAL proposition with RUNTIME_WITNESSED trust.

        Parameters:
            surface: The callable surface to emit a judgment for.

        Returns:
            A :class:`Judgment` with ``PROPOSED`` status.
        """
        coord = _coord_for_surface(surface)
        param_names = tuple(p.name for p in surface.parameters)
        required_count = sum(
            1
            for p in surface.parameters
            if not p.has_default
            and p.kind not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
        )

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"callable_surface({surface.qualname!r}, "
                f"arity={len(surface.parameters)}, "
                f"required={required_count}, "
                f"async={surface.is_async})"
            ),
            free_variables=param_names,
            metadata={
                "module": surface.module,
                "source_file": surface.source_file,
                "lineno": surface.lineno,
                "decorators": list(surface.decorators),
            },
        )
        carrier = Carrier(
            name="CallableSurface",
            parameters=param_names,
            is_dependent=len(param_names) > 0,
            metadata={"qualname": surface.qualname},
        )
        bundle = self.build_evidence(
            f"callable surface inspected: {surface.qualname!r}",
            TrustLevel.RUNTIME_WITNESSED,
        )
        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=("runtime_inspect",),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"surface inspected via inspect for {surface.qualname!r}",),
        )
        prov = self.build_provenance("callable_surface_analysis")
        judgment = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=prov,
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )
        self._emitted.append(judgment)
        _log.debug("emit_surface_judgment: emitted for %r", surface.qualname)
        return judgment

    def emit_binding_judgment(self, bound: BoundMethod) -> Judgment:
        """Emit a relational :class:`Judgment` for a :class:`BoundMethod`.

        Captures the binding between a callable surface and a concrete
        instance type as a RELATIONAL proposition.

        Parameters:
            bound: The :class:`BoundMethod` to emit a judgment for.

        Returns:
            A :class:`Judgment` with ``PROPOSED`` status.
        """
        surface = bound.surface
        qualname = surface.qualname if surface else "unknown"
        coord = _coord_for_qualname(qualname, getattr(surface, "module", "") if surface else "")

        prop = Proposition(
            kind=PropositionKind.RELATIONAL,
            formula=(
                f"bound_method({qualname!r}, "
                f"instance_type={bound.instance_type!r}, "
                f"binding_id={bound.binding_id!r})"
            ),
            free_variables=(bound.instance_type,),
            metadata={
                "binding_id": bound.binding_id,
                "instance_type": bound.instance_type,
            },
        )
        carrier = Carrier(
            name="BoundMethod",
            parameters=(qualname, bound.instance_type),
            is_dependent=True,
            metadata={"binding_id": bound.binding_id},
        )
        bundle = self.build_evidence(
            f"bound method {qualname!r} on {bound.instance_type!r}",
            TrustLevel.RUNTIME_WITNESSED,
        )
        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=("runtime_binding",),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"method {qualname!r} bound to {bound.instance_type!r}",),
        )
        prov = self.build_provenance("method_binding_analysis")
        judgment = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=prov,
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )
        self._emitted.append(judgment)
        _log.debug("emit_binding_judgment: emitted for binding %r", bound.binding_id)
        return judgment

    def emit_descriptor_judgment(self, record: DescriptorRecord) -> Judgment:
        """Emit a structural :class:`Judgment` for a :class:`DescriptorRecord`.

        Encodes the descriptor's kind (data vs non-data), owner class, and
        protocol flags as a STRUCTURAL proposition.

        Parameters:
            record: The :class:`DescriptorRecord` to emit a judgment for.

        Returns:
            A :class:`Judgment` with ``PROPOSED`` status.
        """
        coord = _coord_for_qualname(f"{record.owner}.{record.name}", "")
        proto_parts: list[str] = []
        if record.has_get:
            proto_parts.append("__get__")
        if record.has_set:
            proto_parts.append("__set__")
        if record.has_delete:
            proto_parts.append("__delete__")
        proto_str = "+".join(proto_parts) or "none"

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"descriptor({record.name!r}, "
                f"kind={record.kind!r}, "
                f"owner={record.owner!r}, "
                f"protocol={proto_str!r})"
            ),
            free_variables=(record.name, record.owner),
            metadata={
                "kind": record.kind,
                "has_get": record.has_get,
                "has_set": record.has_set,
                "has_delete": record.has_delete,
                "priority": record.priority,
            },
        )
        carrier = Carrier(
            name="DescriptorRecord",
            parameters=(record.name, record.owner),
            is_dependent=False,
            metadata={"kind": record.kind},
        )
        bundle = self.build_evidence(
            f"descriptor {record.name!r} on {record.owner!r}",
            TrustLevel.RUNTIME_WITNESSED,
        )
        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=("descriptor_inspection",),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"descriptor protocol verified for {record.name!r}",),
        )
        prov = self.build_provenance("descriptor_analysis")
        judgment = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=prov,
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )
        self._emitted.append(judgment)
        _log.debug("emit_descriptor_judgment: emitted for %r.%r", record.owner, record.name)
        return judgment

    def emit_construction_judgment(self, construction: ClassConstruction) -> Judgment:
        """Emit a structural :class:`Judgment` for a :class:`ClassConstruction`.

        Captures the class's bases, method count, metaclass, and abstract/
        dataclass/protocol flags as a STRUCTURAL proposition.

        Parameters:
            construction: The :class:`ClassConstruction` to emit a judgment for.

        Returns:
            A :class:`Judgment` with ``PROPOSED`` status.
        """
        coord = _coord_for_qualname(construction.qualname, "")
        flags: list[str] = []
        if construction.is_abstract:
            flags.append("abstract")
        if construction.is_dataclass:
            flags.append("dataclass")
        if construction.is_protocol:
            flags.append("protocol")
        flags_str = ",".join(flags) or "none"

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"class_construction({construction.qualname!r}, "
                f"bases={list(construction.bases)!r}, "
                f"methods={len(construction.methods)}, "
                f"metaclass={construction.metaclass!r}, "
                f"flags=[{flags_str}])"
            ),
            free_variables=tuple(construction.bases),
            metadata={
                "class_name": construction.class_name,
                "metaclass": construction.metaclass,
                "method_count": len(construction.methods),
                "flags": flags,
            },
        )
        carrier = Carrier(
            name="ClassConstruction",
            parameters=construction.bases,
            is_dependent=len(construction.bases) > 0,
            metadata={"qualname": construction.qualname},
        )
        bundle = self.build_evidence(
            f"class construction inspected: {construction.qualname!r}",
            TrustLevel.RUNTIME_WITNESSED,
        )
        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=("class_inspection",),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"class {construction.qualname!r} construction analyzed",),
        )
        prov = self.build_provenance("class_construction_analysis")
        judgment = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=prov,
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )
        self._emitted.append(judgment)
        _log.debug(
            "emit_construction_judgment: emitted for class %r", construction.qualname
        )
        return judgment

    def batch_emit(self, items: list[Any]) -> list[Judgment]:
        """Dispatch each item in *items* to the appropriate emit method.

        The item type is checked via ``isinstance`` and the appropriate
        ``emit_*`` method is called.  Unknown types trigger a generic surface
        judgment if the item has a ``qualname`` attribute, otherwise they are
        skipped with a warning.

        Parameters:
            items: List of :class:`CallableSurface`, :class:`BoundMethod`,
                :class:`DescriptorRecord`, or :class:`ClassConstruction` objects.

        Returns:
            List of emitted :class:`Judgment` records in the same order as
            successfully processed items.
        """
        results: list[Judgment] = []
        for item in items:
            try:
                if isinstance(item, CallableSurface):
                    results.append(self.emit_surface_judgment(item))
                elif isinstance(item, BoundMethod):
                    results.append(self.emit_binding_judgment(item))
                elif isinstance(item, DescriptorRecord):
                    results.append(self.emit_descriptor_judgment(item))
                elif isinstance(item, ClassConstruction):
                    results.append(self.emit_construction_judgment(item))
                else:
                    _log.warning(
                        "batch_emit: unknown item type %r – skipping", type(item).__name__
                    )
            except Exception as exc:
                _log.warning("batch_emit: failed on %r: %s", item, exc)
        _log.info("batch_emit: emitted %d/%d judgment(s)", len(results), len(items))
        return results

    def build_evidence(
        self, description: str, trust: Any
    ) -> EvidenceBundle:
        """Build a single-item :class:`EvidenceBundle` with a runtime witness.

        Parameters:
            description: Human-readable description of what was witnessed.
            trust: The :class:`TrustLevel` value for this evidence item.

        Returns:
            An :class:`EvidenceBundle` containing one :class:`EvidenceItem`.
        """
        item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"description": description, "timestamp": str(time.time())},
            trust_level=trust,
            channel="callable_judgment_emitter",
            timestamp=str(time.time()),
            expiry="",
            provenance=(),
        )
        return EvidenceBundle(items=(item,))

    def build_provenance(self, source: str) -> Provenance:
        """Build a :class:`Provenance` record attributing the judgment to *source*.

        Parameters:
            source: A short string identifying the analysis component (e.g.
                ``'callable_surface_analysis'``).

        Returns:
            A :class:`Provenance` with ``RUNTIME`` source and current timestamp.
        """
        return Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=str(time.time()),
            transformation_history=(),
            metadata={"analysis_component": source},
        )

    def all_emitted(self) -> list[Judgment]:
        """Return a snapshot of all judgments emitted so far.

        Returns:
            A new list containing all accumulated :class:`Judgment` records.
        """
        return list(self._emitted)


# ---------------------------------------------------------------------------
# Z3CallableEncoder – mutable dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Z3CallableEncoder:
    """Encodes callable constraints as Z3 / SMT-LIB2 formulas.

    When a live :class:`Z3Session` is available (and ``z3_available()`` returns
    ``True``), formulas are asserted into the session and checked with the
    native solver.  When Z3 is absent the encoder falls back to text-based
    SMT-LIB2 formula strings so that the callable analysis pipeline remains
    functional in environments without Z3.

    Attributes:
        _session: Optional live :class:`Z3Session` to assert into.
        _formulas: All :class:`Z3Formula` objects produced during this session.
    """

    _session: Z3Session | None = None
    _formulas: list[Z3Formula] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Encoding helpers
    # ------------------------------------------------------------------

    def _ensure_session(self) -> bool:
        """Ensure a live :class:`Z3Session` is available.

        Attempts to construct one if none exists.  Returns ``False`` when Z3
        is unavailable and a stub session was created instead.

        Returns:
            ``True`` if a real (non-stub) Z3 session is ready for use.
        """
        if self._session is not None:
            return _HAS_Z3_MODULE

        if _HAS_Z3_MODULE:
            try:
                self._session = Z3Session()
                _log.debug("_ensure_session: created Z3Session %s", self._session.session_id)
                return True
            except Exception as exc:
                _log.warning("_ensure_session: failed to create Z3Session: %s", exc)
                self._session = Z3Session()
                return False
        else:
            self._session = Z3Session()
            return False

    def _make_formula(self, expression: str, kind: str = "BOOL") -> Z3Formula:
        """Produce a :class:`Z3Formula` wrapping *expression*.

        When Z3 is available and the expression is a valid SMT-LIB2 fragment
        this also populates ``z3_ast``; otherwise ``z3_ast`` is ``None``.

        Parameters:
            expression: SMT-LIB2 formula string.
            kind: Formula kind string (default ``"BOOL"``).

        Returns:
            A :class:`Z3Formula` ready for assertion.
        """
        z3_ast: Any = None
        if _HAS_Z3_MODULE:
            try:
                import z3 as _z3  # type: ignore[import]
                z3_ast = _z3.Bool(expression.replace(" ", "_").replace("(", "").replace(")", ""))
            except Exception:
                pass
        try:
            from jugeo.solver.z3_session import FormulaKind  # type: ignore[import]
            fk: Any = FormulaKind.BOOL
        except ImportError:
            fk = kind
        formula = Z3Formula(kind=fk, expression=expression, z3_ast=z3_ast)
        self._formulas.append(formula)
        return formula

    # ------------------------------------------------------------------
    # Individual encoders
    # ------------------------------------------------------------------

    def encode_arity_constraint(
        self, surface: CallableSurface
    ) -> Z3Formula | None:
        """Encode the arity constraint for *surface* as an SMT-LIB2 formula.

        The formula asserts that the number of required parameters is
        non-negative and does not exceed the total parameter count.

        Parameters:
            surface: The callable surface to encode.

        Returns:
            A :class:`Z3Formula` encoding arity bounds, or ``None`` if
            encoding fails.
        """
        try:
            total = len(surface.parameters)
            required = sum(
                1
                for p in surface.parameters
                if not p.has_default
                and p.kind
                not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
            )
            qname_safe = surface.qualname.replace(".", "_").replace("<", "").replace(">", "")
            expr = (
                f"(and "
                f"(>= arity_{qname_safe} 0) "
                f"(<= required_{qname_safe} arity_{qname_safe}) "
                f"(= arity_{qname_safe} {total}) "
                f"(= required_{qname_safe} {required}))"
            )
            formula = self._make_formula(expr)
            if self._session is not None:
                try:
                    self._session.assert_formula(formula)
                except Exception as exc:
                    _log.debug("encode_arity_constraint: assert failed: %s", exc)
            return formula
        except Exception as exc:
            _log.warning("encode_arity_constraint: failed for %r: %s", surface.qualname, exc)
            return None

    def encode_type_compatibility(
        self, a: CallableSurface, b: CallableSurface
    ) -> Z3Formula | None:
        """Encode a type-compatibility assertion between *a* and *b*.

        Two surfaces are type-compatible when their return annotations agree
        (or at least one is unannotated) and their required parameter counts
        are compatible.

        Parameters:
            a: First callable surface.
            b: Second callable surface.

        Returns:
            A :class:`Z3Formula` or ``None`` on failure.
        """
        try:
            a_safe = a.qualname.replace(".", "_").replace("<", "").replace(">", "")
            b_safe = b.qualname.replace(".", "_").replace("<", "").replace(">", "")
            a_req = sum(
                1
                for p in a.parameters
                if not p.has_default
                and p.kind not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
            )
            b_req = sum(
                1
                for p in b.parameters
                if not p.has_default
                and p.kind not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
            )
            # Return annotation compatibility: both must be the same type or
            # at least one must be unannotated (_EMPTY)
            ret_compatible = (
                a.return_annotation is _EMPTY
                or b.return_annotation is _EMPTY
                or a.return_annotation == b.return_annotation
            )
            expr = (
                f"(and "
                f"(>= arity_{b_safe} required_{a_safe}) "
                f"(>= arity_{a_safe} required_{b_safe}) "
                f"(= return_compatible_{a_safe}_{b_safe} {'true' if ret_compatible else 'false'}))"
            )
            formula = self._make_formula(expr)
            if self._session is not None:
                try:
                    self._session.assert_formula(formula)
                except Exception as exc:
                    _log.debug("encode_type_compatibility: assert failed: %s", exc)
            return formula
        except Exception as exc:
            _log.warning(
                "encode_type_compatibility: failed for %r/%r: %s",
                a.qualname, b.qualname, exc,
            )
            return None

    def encode_binding_validity(
        self, bound: BoundMethod
    ) -> Z3Formula | None:
        """Encode binding validity constraints for a :class:`BoundMethod`.

        Asserts that the surface is non-null, the instance type is non-empty,
        and the binding ID is unique (represented symbolically).

        Parameters:
            bound: The :class:`BoundMethod` to encode.

        Returns:
            A :class:`Z3Formula` or ``None`` on failure.
        """
        try:
            surface = bound.surface
            qualname = surface.qualname if surface else "unknown"
            qname_safe = qualname.replace(".", "_").replace("<", "").replace(">", "")
            itype_safe = bound.instance_type.replace(".", "_").replace("<", "").replace(">", "")
            has_surface = surface is not None
            has_itype = bool(bound.instance_type)
            has_bid = bool(bound.binding_id)
            expr = (
                f"(and "
                f"(= has_surface_{qname_safe} {'true' if has_surface else 'false'}) "
                f"(= has_instance_type_{itype_safe} {'true' if has_itype else 'false'}) "
                f"(= binding_valid_{qname_safe} "
                f"{'true' if has_surface and has_itype and has_bid else 'false'}))"
            )
            formula = self._make_formula(expr)
            if self._session is not None:
                try:
                    self._session.assert_formula(formula)
                except Exception as exc:
                    _log.debug("encode_binding_validity: assert failed: %s", exc)
            return formula
        except Exception as exc:
            _log.warning("encode_binding_validity: failed: %s", exc)
            return None

    def encode_descriptor_priority(
        self, records: list[DescriptorRecord]
    ) -> Z3Formula | None:
        """Encode descriptor priority ordering for *records*.

        Asserts that data descriptors (has_set=True) always have a higher
        priority than non-data descriptors.

        Parameters:
            records: List of :class:`DescriptorRecord` objects to encode.

        Returns:
            A :class:`Z3Formula` encoding the priority partial order, or
            ``None`` if *records* is empty or encoding fails.
        """
        if not records:
            return None
        try:
            # Build a list of (name, priority_value) assertions
            assertions: list[str] = []
            for rec in records:
                name_safe = f"{rec.owner}_{rec.name}".replace(".", "_")
                # Data descriptors (has_get + has_set) have higher priority
                effective_priority = rec.priority + (10 if rec.has_set else 0)
                assertions.append(
                    f"(= priority_{name_safe} {effective_priority})"
                )
            # Also assert that all data descriptors outrank non-data ones
            data_recs = [r for r in records if r.has_set]
            non_data_recs = [r for r in records if not r.has_set]
            for dr in data_recs:
                for ndr in non_data_recs:
                    dr_safe = f"{dr.owner}_{dr.name}".replace(".", "_")
                    ndr_safe = f"{ndr.owner}_{ndr.name}".replace(".", "_")
                    assertions.append(
                        f"(> priority_{dr_safe} priority_{ndr_safe})"
                    )
            expr = "(and " + " ".join(assertions) + ")" if assertions else "true"
            formula = self._make_formula(expr)
            if self._session is not None:
                try:
                    self._session.assert_formula(formula)
                except Exception as exc:
                    _log.debug("encode_descriptor_priority: assert failed: %s", exc)
            return formula
        except Exception as exc:
            _log.warning("encode_descriptor_priority: failed: %s", exc)
            return None

    def check_callable_constraints(
        self, surface: CallableSurface
    ) -> Any:
        """Encode and check all callable constraints for *surface*.

        Builds an arity constraint, asserts it into the session, and runs
        ``check_sat``.  Falls back to ``UNKNOWN`` when Z3 is unavailable.

        Parameters:
            surface: The callable surface to check.

        Returns:
            A :class:`SolveOutcome` value.
        """
        self._ensure_session()
        formula = self.encode_arity_constraint(surface)
        if formula is None or self._session is None:
            _log.debug(
                "check_callable_constraints: no formula or session for %r",
                surface.qualname,
            )
            return SolveOutcome.UNKNOWN
        try:
            outcome = self._session.check_sat()
            _log.debug(
                "check_callable_constraints: outcome=%r for %r", outcome, surface.qualname
            )
            return outcome
        except Exception as exc:
            _log.warning("check_callable_constraints: check_sat failed: %s", exc)
            return SolveOutcome.UNKNOWN


# ---------------------------------------------------------------------------
# CallableCoordinateMapper – mutable dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CallableCoordinateMapper:
    """Maps Python callables to :class:`CoordinateObject` positions in the site.

    Builds a persistent index mapping qualname strings to their coordinate
    objects, and optionally registers coordinates into a live :class:`Site`.

    Attributes:
        _index: Qualname → :class:`CoordinateObject` lookup table.
        _site: Optional live :class:`Site` to register coordinates into.
    """

    _index: dict[str, CoordinateObject] = field(default_factory=dict)
    _site: Site | None = None

    def map_function(self, func: Any) -> CoordinateObject:
        """Map a regular function to a :class:`CoordinateObject`.

        The coordinate path is ``module.qualname`` components; the kind is
        ``FUNCTION``.

        Parameters:
            func: A regular Python function (``types.FunctionType``).

        Returns:
            The corresponding :class:`CoordinateObject`.
        """
        name = getattr(func, "__name__", repr(func))
        qualname = getattr(func, "__qualname__", name)
        module = getattr(func, "__module__", "") or ""
        parts = tuple(c for c in (module,) + tuple(qualname.split(".")) if c)
        coord = CoordinateObject(
            components=parts,
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({"function", "callable"}),
            metadata={
                "qualname": qualname,
                "module": module,
                "is_async": inspect.iscoroutinefunction(func),
                "is_generator": inspect.isgeneratorfunction(func),
            },
        )
        self._index[qualname] = coord
        return coord

    def map_method(self, method: Any, cls: type) -> CoordinateObject:
        """Map a method (bound or unbound) to a :class:`CoordinateObject`.

        The coordinate path includes the class name in the hierarchy.

        Parameters:
            method: The method object.
            cls: The class that defines or owns the method.

        Returns:
            The corresponding :class:`CoordinateObject`.
        """
        name = getattr(method, "__name__", repr(method))
        qualname = f"{cls.__qualname__}.{name}"
        module = getattr(method, "__module__", getattr(cls, "__module__", "")) or ""
        is_classmethod = isinstance(
            cls.__dict__.get(name), classmethod
        )
        is_staticmethod = isinstance(
            cls.__dict__.get(name), staticmethod
        )
        parts = tuple(
            c for c in (module,) + tuple(qualname.split(".")) if c
        )
        coord = CoordinateObject(
            components=parts,
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset(
                {"method", "callable"}
                | ({"classmethod"} if is_classmethod else set())
                | ({"staticmethod"} if is_staticmethod else set())
            ),
            metadata={
                "qualname": qualname,
                "module": module,
                "class": cls.__qualname__,
                "is_classmethod": is_classmethod,
                "is_staticmethod": is_staticmethod,
            },
        )
        self._index[qualname] = coord
        return coord

    def map_class(self, cls: type) -> CoordinateObject:
        """Map a class to a :class:`CoordinateObject`.

        The coordinate kind is ``INTERFACE`` (classes define interfaces).
        Bases are recorded in the metadata.

        Parameters:
            cls: The class to map.

        Returns:
            The corresponding :class:`CoordinateObject`.
        """
        qualname = cls.__qualname__
        module = getattr(cls, "__module__", "") or ""
        parts = tuple(c for c in (module,) + tuple(qualname.split(".")) if c)
        base_names = [b.__qualname__ for b in cls.__bases__ if b is not object]
        is_abstract = bool(getattr(cls, "__abstractmethods__", None))
        coord = CoordinateObject(
            components=parts,
            kind=CoordinateKind.INTERFACE,
            support_labels=frozenset(
                {"class", "callable", "interface"}
                | ({"abstract"} if is_abstract else set())
            ),
            metadata={
                "qualname": qualname,
                "module": module,
                "bases": base_names,
                "is_abstract": is_abstract,
                "method_count": sum(
                    1
                    for v in cls.__dict__.values()
                    if callable(v) or isinstance(v, (staticmethod, classmethod))
                ),
            },
        )
        self._index[qualname] = coord
        return coord

    def map_lambda(self, func: Any) -> CoordinateObject:
        """Map a lambda to a :class:`CoordinateObject`.

        Lambdas are anonymous; the coordinate uses a UUID suffix to ensure
        uniqueness since ``__name__`` is always ``'<lambda>'``.

        Parameters:
            func: A lambda function (any callable with ``__name__ == '<lambda>'``).

        Returns:
            A :class:`CoordinateObject` with a unique lambda identifier.
        """
        module = getattr(func, "__module__", "") or ""
        lambda_id = uuid.uuid4().hex[:8]
        qualname = getattr(func, "__qualname__", f"<lambda>:{lambda_id}")
        # Embed the lambda ID into the qualname to guarantee uniqueness
        unique_key = f"{qualname}:{lambda_id}"
        parts = tuple(c for c in (module,) + tuple(unique_key.split(".")) if c)
        coord = CoordinateObject(
            components=parts,
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({"lambda", "callable", "anonymous"}),
            metadata={
                "qualname": qualname,
                "module": module,
                "lambda_id": lambda_id,
                "is_async": inspect.iscoroutinefunction(func),
            },
        )
        self._index[unique_key] = coord
        return coord

    def map_builtin(self, func: Any) -> CoordinateObject:
        """Map a builtin or C-extension callable to a :class:`CoordinateObject`.

        Builtins often lack ``__module__`` and ``__qualname__``, so we fall
        back to ``__name__`` and the ``builtins`` pseudo-module.

        Parameters:
            func: A builtin callable (e.g. ``len``, ``print``, ``isinstance``).

        Returns:
            The corresponding :class:`CoordinateObject`.
        """
        name = getattr(func, "__name__", repr(func))
        qualname = getattr(func, "__qualname__", name)
        module = getattr(func, "__module__", "builtins") or "builtins"
        parts = tuple(c for c in (module, qualname) if c)
        coord = CoordinateObject(
            components=parts,
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({"builtin", "callable", "c_extension"}),
            metadata={
                "qualname": qualname,
                "module": module,
                "is_builtin": inspect.isbuiltin(func),
                "is_method_wrapper": type(func).__name__ == "method-wrapper",
            },
        )
        self._index[qualname] = coord
        return coord

    def build_index(self, funcs: list[Any]) -> dict[str, CoordinateObject]:
        """Build a coordinate index for many callables at once.

        Dispatches each callable to the appropriate ``map_*`` method based on
        its type.

        Parameters:
            funcs: List of callables to map.

        Returns:
            Dict mapping qualname (or unique key for lambdas) to
            :class:`CoordinateObject`.
        """
        for func in funcs:
            try:
                if inspect.isbuiltin(func) or type(func).__name__ == "builtin_function_or_method":
                    self.map_builtin(func)
                elif isinstance(func, type):
                    self.map_class(func)
                elif getattr(func, "__name__", "") == "<lambda>":
                    self.map_lambda(func)
                elif inspect.isfunction(func) or inspect.ismethod(func):
                    self.map_function(func)
                else:
                    self.map_function(func)
            except Exception as exc:
                _log.warning("build_index: skipping %r: %s", func, exc)
        return dict(self._index)

    def lookup(self, qualname: str) -> CoordinateObject | None:
        """Look up the :class:`CoordinateObject` for *qualname*.

        Parameters:
            qualname: The qualified name to look up.

        Returns:
            The :class:`CoordinateObject` if found, else ``None``.
        """
        return self._index.get(qualname)


# ---------------------------------------------------------------------------
# SupportRegionBuilder – mutable dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SupportRegionBuilder:
    """Builds :class:`SupportRegion` objects for callable analysis results.

    Tracks all regions produced during a session in ``_regions`` so that
    callers can retrieve a merged view of the support landscape.

    Attributes:
        _regions: All :class:`SupportRegion` objects produced so far.
    """

    _regions: list[SupportRegion] = field(default_factory=list)

    def build_callable_support(
        self, surface: CallableSurface
    ) -> SupportRegion:
        """Build a :class:`SupportRegion` for a :class:`CallableSurface`.

        Parameters:
            surface: The callable surface to build support for.

        Returns:
            A :class:`SupportRegion` covering the callable's coordinate.
        """
        coord = _coord_for_surface(surface)
        patch_key = f"{surface.module}.{surface.qualname}" if surface.module else surface.qualname
        labels: frozenset[str] = frozenset(
            {"callable_surface", "analysis"}
            | ({"async"} if surface.is_async else set())
            | ({"generator"} if surface.is_generator else set())
        )
        region = SupportRegion(
            coordinate=coord,
            patch_keys=frozenset({patch_key}),
            labels=labels,
            provenance=(f"callable_surface_analysis:{surface.qualname}",),
        )
        self._regions.append(region)
        return region

    def build_method_support(self, bound: BoundMethod) -> SupportRegion:
        """Build a :class:`SupportRegion` for a :class:`BoundMethod`.

        Parameters:
            bound: The bound method to build support for.

        Returns:
            A :class:`SupportRegion` covering the method's binding coordinate.
        """
        surface = bound.surface
        qualname = surface.qualname if surface else "unknown"
        module = getattr(surface, "module", "") if surface else ""
        coord = _coord_for_qualname(qualname, module)
        patch_key = f"binding:{bound.binding_id}:{qualname}"
        region = SupportRegion(
            coordinate=coord,
            patch_keys=frozenset({patch_key}),
            labels=frozenset({"bound_method", "method_binding", "callable"}),
            provenance=(
                f"method_binding:{bound.binding_id}",
                f"instance_type:{bound.instance_type}",
            ),
        )
        self._regions.append(region)
        return region

    def build_class_support(
        self, construction: ClassConstruction
    ) -> SupportRegion:
        """Build a :class:`SupportRegion` for a :class:`ClassConstruction`.

        Parameters:
            construction: The class construction to build support for.

        Returns:
            A :class:`SupportRegion` covering the class hierarchy coordinate.
        """
        coord = _coord_for_qualname(construction.qualname, "")
        patch_key = f"class:{construction.qualname}"
        labels: frozenset[str] = frozenset(
            {"class_construction", "callable", "interface"}
            | ({"abstract"} if construction.is_abstract else set())
            | ({"dataclass"} if construction.is_dataclass else set())
            | ({"protocol"} if construction.is_protocol else set())
        )
        region = SupportRegion(
            coordinate=coord,
            patch_keys=frozenset({patch_key}),
            labels=labels,
            provenance=(
                f"class_construction:{construction.qualname}",
                f"metaclass:{construction.metaclass}",
            ),
        )
        self._regions.append(region)
        return region

    def merge_supports(self, regions: list[SupportRegion]) -> SupportRegion:
        """Merge multiple :class:`SupportRegion` objects into one.

        The merged region uses the coordinate of the first region and
        accumulates all patch keys, labels, and provenance strings.

        Parameters:
            regions: Non-empty list of :class:`SupportRegion` objects to merge.

        Returns:
            A single merged :class:`SupportRegion`.

        Raises:
            ValueError: If *regions* is empty.
        """
        if not regions:
            raise ValueError("merge_supports: regions list must be non-empty")

        merged_keys: frozenset[str] = frozenset()
        merged_labels: frozenset[str] = frozenset()
        merged_prov: list[str] = []
        for r in regions:
            merged_keys = merged_keys | r.patch_keys
            merged_labels = merged_labels | r.labels
            merged_prov.extend(r.provenance)

        result = SupportRegion(
            coordinate=regions[0].coordinate,
            patch_keys=merged_keys,
            labels=merged_labels | frozenset({"merged"}),
            provenance=tuple(dict.fromkeys(merged_prov)),  # deduplicate
        )
        self._regions.append(result)
        return result

    def all_regions(self) -> list[SupportRegion]:
        """Return a snapshot of all support regions built so far.

        Returns:
            New list containing all accumulated :class:`SupportRegion` objects.
        """
        return list(self._regions)


# ---------------------------------------------------------------------------
# CopilotCallableAdvisor – mutable dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CopilotCallableAdvisor:
    """Provides copilot-style suggestions and reports for callable analysis.

    This class is the primary copilot integration point in the callable
    surfaces package.  It generates structured advisory data that downstream
    LLM-orchestration layers (or human reviewers) can act on.

    Each advisory method appends its results to ``_suggestions`` or
    ``_reports`` so that a full session history is always available.

    Attributes:
        _suggestions: Accumulated suggestion strings from all advisory calls.
        _reports: Accumulated formatted report strings.
    """

    _suggestions: list[str] = field(default_factory=list)
    _reports: list[str] = field(default_factory=list)

    def suggest_type_annotation(
        self, surface: CallableSurface
    ) -> list[str]:
        """Suggest missing type annotations for *surface*.

        Identifies parameters and return values that lack explicit type
        annotations and suggests concrete types based on parameter names and
        common naming conventions.

        Parameters:
            surface: The callable surface to advise on.

        Returns:
            List of suggestion strings, one per missing annotation.
        """
        suggestions: list[str] = []

        # Check return annotation
        if surface.return_annotation is _EMPTY or surface.return_annotation is inspect.Parameter.empty:
            if surface.is_async:
                suggestions.append(
                    f"Add return annotation: `async def {surface.name}(...) -> Awaitable[T]` "
                    "or use `-> Coroutine[Any, Any, T]`"
                )
            elif surface.is_generator:
                suggestions.append(
                    f"Add return annotation: `def {surface.name}(...) -> Generator[YieldType, SendType, ReturnType]`"
                )
            else:
                suggestions.append(
                    f"Add return annotation to `{surface.qualname}` "
                    "(e.g. `-> None`, `-> str`, `-> int`)"
                )

        # Check parameters
        for param in surface.parameters:
            if param.annotation is _EMPTY or param.annotation is inspect.Parameter.empty:
                if param.kind in (ParameterKind.VAR_POSITIONAL,):
                    suggestions.append(
                        f"Annotate `*{param.name}` in `{surface.qualname}` "
                        "(e.g. `*args: int`)"
                    )
                elif param.kind in (ParameterKind.VAR_KEYWORD,):
                    suggestions.append(
                        f"Annotate `**{param.name}` in `{surface.qualname}` "
                        "(e.g. `**kwargs: str`)"
                    )
                else:
                    # Guess type from name
                    inferred = _infer_type_from_name(param.name)
                    suggestions.append(
                        f"Add annotation for `{param.name}` in `{surface.qualname}`: "
                        f"suggested type `{inferred}` based on naming convention"
                    )

        self._suggestions.extend(suggestions)
        return suggestions

    def explain_descriptor_lookup(self, record: DescriptorRecord) -> str:
        """Produce a human-readable explanation of descriptor lookup priority.

        Follows the Python data model: data descriptors take precedence over
        instance ``__dict__``, which takes precedence over non-data descriptors.

        Parameters:
            record: The :class:`DescriptorRecord` to explain.

        Returns:
            A multi-line explanation string.
        """
        lines: list[str] = [
            f"Descriptor: {record.name!r} on {record.owner!r}",
            f"  Kind: {record.kind}",
            f"  Protocol: "
            + ", ".join(
                m
                for m, present in [
                    ("__get__", record.has_get),
                    ("__set__", record.has_set),
                    ("__delete__", record.has_delete),
                ]
                if present
            ),
        ]
        if record.has_set:
            lines.append(
                "  Priority: DATA DESCRIPTOR — takes precedence over instance __dict__."
                " The descriptor __set__ will be called even if the attribute exists in "
                "the instance dictionary."
            )
        elif record.has_get:
            lines.append(
                "  Priority: NON-DATA DESCRIPTOR — instance __dict__ takes precedence. "
                "The __get__ is only invoked if the attribute is absent from the instance "
                "dictionary or if the object is a class."
            )
        else:
            lines.append(
                "  Priority: CLASS VARIABLE — no descriptor protocol implemented. "
                "Behaves as a plain class attribute accessible via __dict__."
            )
        lines.append(
            f"  Effective priority score: {record.priority + (10 if record.has_set else 0)}"
        )
        explanation = "\n".join(lines)
        self._reports.append(explanation)
        return explanation

    def detect_binding_errors(self, bound: BoundMethod) -> list[str]:
        """Detect common binding errors in a :class:`BoundMethod`.

        Checks for missing surface reference, empty instance type, missing
        binding ID, and mismatched parameter counts for common instance method
        patterns.

        Parameters:
            bound: The :class:`BoundMethod` to check.

        Returns:
            List of error description strings.  Empty if no errors detected.
        """
        errors: list[str] = []

        if bound.surface is None:
            errors.append(
                f"BoundMethod (id={bound.binding_id!r}) has no surface reference; "
                "the underlying CallableSurface was not captured."
            )
            self._suggestions.extend(errors)
            return errors

        surface = bound.surface
        if not bound.instance_type:
            errors.append(
                f"BoundMethod {surface.qualname!r} has an empty instance_type; "
                "the receiver class was not recorded."
            )

        if not bound.binding_id:
            errors.append(
                f"BoundMethod {surface.qualname!r} has no binding_id; "
                "identity tracking will fail."
            )

        # Check that the first parameter looks like 'self' for instance methods
        if surface.parameters:
            first = surface.parameters[0]
            if (
                first.kind
                in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD)
                and first.name not in ("self", "cls", "_self", "_cls")
            ):
                errors.append(
                    f"BoundMethod {surface.qualname!r}: first parameter {first.name!r} "
                    "does not follow the 'self'/'cls' convention for instance methods."
                )

        # Check for async/generator combinations that are unusual
        if surface.is_async and surface.is_generator:
            errors.append(
                f"BoundMethod {surface.qualname!r}: surface is both async and a generator "
                "(async generator); ensure callers use `async for` not `await`."
            )

        self._suggestions.extend(errors)
        return errors

    def suggest_method_refactoring(self, cls: type) -> list[str]:
        """Suggest refactoring improvements for the methods of *cls*.

        Analyses the class's method dictionary for common code smells: overly
        large parameter lists, methods that could be class/static methods, and
        missing ``__slots__`` on data-heavy classes.

        Parameters:
            cls: The class to analyse.

        Returns:
            List of refactoring suggestion strings.
        """
        suggestions: list[str] = []
        methods = {
            name: val
            for name, val in cls.__dict__.items()
            if callable(val) or isinstance(val, (staticmethod, classmethod))
        }

        # Suggest __slots__ if many instance attributes and no __slots__
        if "__slots__" not in cls.__dict__:
            num_methods = len(methods)
            if num_methods > 8:
                suggestions.append(
                    f"Consider adding `__slots__` to {cls.__qualname__!r} "
                    f"({num_methods} methods detected); this reduces per-instance memory "
                    "overhead and speeds up attribute access."
                )

        for name, method in methods.items():
            if name.startswith("__") and name.endswith("__"):
                continue  # skip dunders
            raw = method
            if isinstance(raw, (staticmethod, classmethod)):
                raw = raw.__func__
            if not callable(raw):
                continue
            try:
                sig = inspect.signature(raw, follow_wrapped=False)
                params = list(sig.parameters.values())
            except (ValueError, TypeError):
                continue

            # Large parameter list smell
            if len(params) > 6:
                suggestions.append(
                    f"{cls.__qualname__}.{name}: {len(params)} parameters is high; "
                    "consider extracting a parameter object or builder."
                )

            # Method that doesn't use 'self' could be a staticmethod
            if (
                params
                and params[0].name == "self"
                and not isinstance(method, (staticmethod, classmethod))
            ):
                code = getattr(raw, "__code__", None)
                if code is not None:
                    # If 'self' is not in co_varnames beyond index 0 uses, suggest staticmethod
                    free_and_local = set(code.co_varnames)
                    if "self" not in free_and_local or code.co_varnames[0] != "self":
                        suggestions.append(
                            f"{cls.__qualname__}.{name}: 'self' does not appear to be used; "
                            "consider converting to @staticmethod."
                        )

        self._suggestions.extend(suggestions)
        return suggestions

    def format_callable_report(self, surface: CallableSurface) -> str:
        """Produce a human-readable report for *surface*.

        Includes qualname, module, parameter table, return annotation, and
        flags (async, generator, closures, decorators).

        Parameters:
            surface: The callable surface to report on.

        Returns:
            A multi-line formatted string suitable for terminal or log output.
        """
        lines: list[str] = []
        lines.append(f"╔═══ Callable Surface Report ══════════════════════")
        lines.append(f"║ qualname   : {surface.qualname}")
        lines.append(f"║ module     : {surface.module or '<unknown>'}")
        loc = (
            f"{surface.source_file}:{surface.lineno}"
            if surface.source_file
            else "<no source>"
        )
        lines.append(f"║ location   : {loc}")
        flags: list[str] = []
        if surface.is_async:
            flags.append("async")
        if surface.is_generator:
            flags.append("generator")
        if surface.is_coroutine:
            flags.append("coroutine")
        if surface.closure_vars:
            flags.append(f"closure({','.join(surface.closure_vars)})")
        lines.append(f"║ flags      : {', '.join(flags) or 'none'}")
        lines.append(f"║ decorators : {', '.join(surface.decorators) or 'none'}")

        ret_ann = surface.return_annotation
        if ret_ann is _EMPTY or ret_ann is inspect.Parameter.empty:
            ret_str = "<unannotated>"
        else:
            ret_str = getattr(ret_ann, "__name__", repr(ret_ann))
        lines.append(f"║ returns    : {ret_str}")
        lines.append(f"║ parameters ({len(surface.parameters)}):")

        for p in surface.parameters:
            ann = p.annotation
            ann_str = (
                "<unannotated>"
                if (ann is _EMPTY or ann is inspect.Parameter.empty)
                else getattr(ann, "__name__", repr(ann))
            )
            default_str = f" = {p.default_value!r}" if p.has_default else ""
            kind_abbr = {
                ParameterKind.POSITIONAL_ONLY: "pos-only",
                ParameterKind.POSITIONAL_OR_KEYWORD: "pos-or-kw",
                ParameterKind.VAR_POSITIONAL: "*args",
                ParameterKind.KEYWORD_ONLY: "kw-only",
                ParameterKind.VAR_KEYWORD: "**kwargs",
            }.get(p.kind, p.kind)
            lines.append(
                f"║   {p.name}: {ann_str}{default_str}  [{kind_abbr}]"
            )

        if surface.docstring:
            first_line = surface.docstring.splitlines()[0][:72]
            lines.append(f"║ docstring  : {first_line}…")
        lines.append("╚══════════════════════════════════════════════════")
        report = "\n".join(lines)
        self._reports.append(report)
        return report

    def all_suggestions(self) -> list[str]:
        """Return all accumulated suggestions from this advisor session.

        Returns:
            A new list of all suggestion strings emitted so far.
        """
        return list(self._suggestions)


# ---------------------------------------------------------------------------
# Module-level private helpers
# ---------------------------------------------------------------------------


def _infer_type_from_name(name: str) -> str:
    """Infer a plausible Python type annotation from a parameter name.

    Uses common naming conventions (e.g. ``count`` → ``int``, ``name`` →
    ``str``, ``flag`` → ``bool``) to produce a useful suggestion.

    Parameters:
        name: The parameter name to infer from.

    Returns:
        A type annotation string (e.g. ``'int'``, ``'str'``, ``'bool'``).
    """
    name_lower = name.lower()
    if any(tok in name_lower for tok in ("count", "num", "size", "len", "idx", "index", "n_")):
        return "int"
    if any(tok in name_lower for tok in ("flag", "enable", "disable", "is_", "has_", "use_")):
        return "bool"
    if any(tok in name_lower for tok in ("name", "key", "path", "url", "text", "msg", "message", "label")):
        return "str"
    if any(tok in name_lower for tok in ("ratio", "rate", "weight", "score", "prob")):
        return "float"
    if any(tok in name_lower for tok in ("items", "values", "elements", "entries")):
        return "list[Any]"
    if any(tok in name_lower for tok in ("mapping", "config", "options", "settings", "kwargs")):
        return "dict[str, Any]"
    if any(tok in name_lower for tok in ("callback", "fn", "func", "handler", "action")):
        return "Callable[..., Any]"
    return "Any"


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "CallableCoordinateMapper",
    "CallableJudgmentEmitter",
    "CopilotCallableAdvisor",
    "SupportRegionBuilder",
    "Z3CallableEncoder",
]

# copilot: shared-core marker for LLM-assisted callable surface orchestration.
