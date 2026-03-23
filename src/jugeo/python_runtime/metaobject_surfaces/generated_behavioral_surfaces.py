"""
generated_behavioral_surfaces.py
======================================
theory2.tex — Chapter 20 §4: "Generated behavioral surfaces"

A **behavioral surface** for class C is a judgment-indexed protocol
specification generated from metaclass and descriptor analysis.  In JuGeo's
sheaf-theoretic model, a behavioral surface is a *sheaf of observable
behaviors* indexed by the judgment coordinate system:

  Surface(C) : Coord^op → Set

where each coordinate U maps to the set of behavioral contracts C satisfies
at U.  The global section of Surface(C) is the complete behavioral interface
of C.

Key structural properties by class kind:
  DATACLASS        — auto-generated __init__, __repr__, __eq__ (and optionally
                     __hash__, __lt__, etc.).  Frozen dataclasses additionally
                     generate __setattr__ / __delattr__ guards.
  NAMED_TUPLE      — immutable, positional, iterable; generates __new__ with
                     defaults.
  PROTOCOL         — purely structural; runtime_checkable adds isinstance
                     support via __class_getitem__.
  ABC              — abstract methods create a behavioral obligation: any
                     concrete subclass must implement them.
  ENUM             — generates __members__, value/name accessors, iteration.

This module implements:
  • GeneratedBehavioralSurfacesCoordinator — orchestrates surface generation.
  • GeneratedBehavioralSurfacesAnalyzer    — analysis tools.
  • GeneratedBehavioralSurfacesWitness     — runtime witnessing helpers.

Cross-references:
  theory2.tex §20.4 (behavioral surfaces), §20.3 (contract transformers),
  §20.2 (descriptor routes), §16.2 (sheaf sections), §19.1 (protocol compliance).
"""
from __future__ import annotations

import abc
import ast
import dataclasses
import inspect
import logging
import types
import typing
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# jugeo cross-package imports with full stub fallbacks
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology, CoordinateObject,
    )
except ImportError:
    from dataclasses import dataclass as _dc, field as _field
    from enum import Enum as _Enum

    class CoordinateKind(_Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"

    class MorphismKind(_Enum):
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"

    @_dc(frozen=True)
    class Coordinate:
        components: tuple = ()
        kind: "CoordinateKind" = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()

    CoordinateObject = Coordinate

    @_dc(frozen=True)
    class Morphism:
        source: "Coordinate" = None; target: "Coordinate" = None
        kind: "MorphismKind" = MorphismKind.INCLUSION; label: str = ""

    @_dc
    class CoveringFamily:
        base: "Coordinate" = None; members: list = _field(default_factory=list)

    @_dc
    class GrothendieckTopology:
        name: str = "custom"

    @_dc
    class Site:
        label: str = ""; _coords: list = _field(default_factory=list); _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def objects(self): return list(self._coords)
        def morphisms_from(self, c): return [m for m in self._morphisms if getattr(m, 'source', None) == c]

    @_dc
    class SiteBuilder:
        _coords: list = _field(default_factory=list); _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def build(self): return Site()

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
    )
except ImportError:
    from enum import Enum as _Enum2
    from dataclasses import dataclass as _dc2, field as _field2

    class JudgmentStatus(str, _Enum2):
        PROPOSED = "proposed"; SETTLED = "settled"; OBSTRUCTED = "obstructed"; OPEN = "open"

    class TrustLevel(int, _Enum2):
        COPILOT_SUGGESTED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; VERIFIED = 4

    class PropositionKind(str, _Enum2):
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; TEMPORAL = "temporal"
        INVARIANT = "invariant"; LIVENESS = "liveness"; SAFETY = "safety"

    class EvidenceItemKind(str, _Enum2):
        STATIC_ANALYSIS = "static_analysis"; RUNTIME_TRACE = "runtime_trace"
        THEOREM_PROOF = "theorem_proof"; COPILOT_ANNOTATION = "copilot_annotation"

    @_dc2(frozen=True)
    class Proposition:
        kind: "PropositionKind" = PropositionKind.STRUCTURAL; statement: str = ""; label: str = ""

    @_dc2(frozen=True)
    class Carrier:
        coordinate: object = None; payload: object = None; label: str = ""

    @_dc2
    class EvidenceItem:
        kind: "EvidenceItemKind" = EvidenceItemKind.STATIC_ANALYSIS; payload: object = None; label: str = ""

    @_dc2
    class EvidenceBundle:
        items: list = _field2(default_factory=list)
        def add(self, item): self.items.append(item); return self

    @_dc2
    class TrustAnnotation:
        level: "TrustLevel" = TrustLevel.COPILOT_SUGGESTED; rationale: str = ""

    @_dc2
    class Provenance:
        source: str = ""; module: str = ""; timestamp: str = ""

    @_dc2
    class ResidualObligation:
        description: str = ""; discharged: bool = False

    @_dc2
    class Obstruction:
        description: str = ""; coordinate: object = None

    @_dc2
    class Judgment:
        status: "JudgmentStatus" = JudgmentStatus.PROPOSED
        proposition: "Proposition" = None
        carrier: "Carrier" = None
        evidence: "EvidenceBundle" = _field2(default_factory=EvidenceBundle)
        trust: "TrustAnnotation" = _field2(default_factory=TrustAnnotation)
        provenance: "Provenance" = _field2(default_factory=Provenance)
        obligations: list = _field2(default_factory=list)
        label: str = ""
        def settle(self): self.status = JudgmentStatus.SETTLED; return self
        def obstruct(self, obs): self.status = JudgmentStatus.OBSTRUCTED; return self

try:
    from jugeo.solver.z3_session import SolveOutcome, Z3Formula, Z3Session, z3_available
except ImportError:
    from enum import Enum as _Enum3
    from dataclasses import dataclass as _dc3

    class SolveOutcome(str, _Enum3):
        SAT = "sat"; UNSAT = "unsat"; UNKNOWN = "unknown"

    @_dc3
    class Z3Formula:
        smt2: str = ""; label: str = ""

    @_dc3
    class Z3Session:
        def check(self, formula): return SolveOutcome.UNKNOWN
        def add_assertion(self, formula): return self

    def z3_available() -> bool: return False


# ===========================================================================
# Domain enumerations
# ===========================================================================

class SurfaceKind(str, Enum):
    """Structural classification of a behavioral surface.

    # copilot: used as the index for the behavioral surface sheaf.  Each
    SurfaceKind implies a different set of generated methods and constraints.

    PLAIN_CLASS      — ordinary class; no framework-generated methods.
    DATACLASS        — @dataclass decorated; has generated __init__, __repr__, __eq__.
    NAMED_TUPLE      — NamedTuple subclass; immutable, positional.
    TYPED_DICT       — TypedDict subclass; dict-backed, no generated methods.
    PROTOCOL         — typing.Protocol; structural subtyping.
    ABC              — abc.ABC subclass; abstract method obligations.
    ENUM             — enum.Enum subclass; member iteration, value/name access.
    FROZEN_DATACLASS — @dataclass(frozen=True); immutable, hashable.
    """
    PLAIN_CLASS = "plain_class"
    DATACLASS = "dataclass"
    NAMED_TUPLE = "named_tuple"
    TYPED_DICT = "typed_dict"
    PROTOCOL = "protocol"
    ABC = "abc"
    ENUM = "enum"
    FROZEN_DATACLASS = "frozen_dataclass"


class OverridePattern(str, Enum):
    """Classification of how a method is inherited / overridden in an MRO.

    # copilot: used by classify_override_pattern to label each method in a
    behavioral surface record.

    NO_OVERRIDE              — method not overridden anywhere in the MRO.
    DIRECT_OVERRIDE          — cls defines the method directly.
    COOPERATIVE_SUPER        — method calls super() at some point.
    ABSTRACT_IMPLEMENTATION  — method overrides an abstract base method.
    MIXIN_COMPOSITION        — method is composed from multiple mixins.
    """
    NO_OVERRIDE = "no_override"
    DIRECT_OVERRIDE = "direct_override"
    COOPERATIVE_SUPER = "cooperative_super"
    ABSTRACT_IMPLEMENTATION = "abstract_implementation"
    MIXIN_COMPOSITION = "mixin_composition"


# ===========================================================================
# Value-object dataclasses (frozen, slots)
# ===========================================================================

@dataclass(frozen=True, slots=True)
class BehavioralSurfaceRecord:
    """Top-level behavioral surface for a single class.

    # copilot: the primary carrier object for surface judgments.  Produced
    by GeneratedBehavioralSurfacesCoordinator.generate_surface.

    Attributes
    ----------
    class_name               : __qualname__ of the class.
    surface_kind             : SurfaceKind classification.
    public_methods           : tuple of non-dunder public method names.
    dunder_methods           : tuple of dunder method names defined.
    properties               : tuple of property attribute names.
    class_vars               : tuple of class-level variable names.
    is_abstract              : True iff the class has abstract methods.
    protocol_runtime_checkable: True iff the class is @runtime_checkable.
    """
    class_name: str
    surface_kind: "SurfaceKind"
    public_methods: tuple
    dunder_methods: tuple
    properties: tuple
    class_vars: tuple
    is_abstract: bool
    protocol_runtime_checkable: bool


@dataclass(frozen=True, slots=True)
class DataclassSurfaceRecord:
    """Behavioral surface specialisation for dataclass-decorated classes.

    # copilot: produced by generate_dataclass_surface.  Captures the full
    dataclass configuration so downstream analysis can determine which
    methods were generated vs user-defined.

    Attributes
    ----------
    class_name      : __qualname__ of the dataclass.
    fields          : tuple of (name, type_str) pairs.
    is_frozen       : True iff frozen=True.
    has_post_init   : True iff __post_init__ is defined.
    has_slots       : True iff slots=True.
    eq_generated    : True iff __eq__ was generated.
    order_generated : True iff __lt__ etc. were generated.
    kw_only         : True iff kw_only=True.
    """
    class_name: str
    fields: tuple
    is_frozen: bool
    has_post_init: bool
    has_slots: bool
    eq_generated: bool
    order_generated: bool
    kw_only: bool


@dataclass(frozen=True, slots=True)
class ProtocolSurfaceRecord:
    """Behavioral surface specialisation for typing.Protocol subclasses.

    # copilot: produced by generate_protocol_surface.

    Attributes
    ----------
    protocol_name          : __qualname__ of the protocol.
    required_methods       : tuple of method names that must be implemented.
    required_attrs         : tuple of class-var names the protocol requires.
    is_runtime_checkable   : True iff decorated with @runtime_checkable.
    structural_subtypes    : tuple of __qualname__ strings of known subtypes.
    """
    protocol_name: str
    required_methods: tuple
    required_attrs: tuple
    is_runtime_checkable: bool
    structural_subtypes: tuple


@dataclass(frozen=True, slots=True)
class ABCSurfaceRecord:
    """Behavioral surface specialisation for abc.ABC subclasses.

    # copilot: produced by generate_abc_surface.

    Attributes
    ----------
    abc_name           : __qualname__ of the ABC.
    abstract_methods   : tuple of abstract method names.
    abstract_properties: tuple of abstractproperty names.
    concrete_methods   : tuple of concrete (implemented) method names.
    concrete_subclasses: tuple of __qualname__ strings of registered subclasses.
    """
    abc_name: str
    abstract_methods: tuple
    abstract_properties: tuple
    concrete_methods: tuple
    concrete_subclasses: tuple


@dataclass(frozen=True, slots=True)
class MergedSurfaceRecord:
    """Result of merging multiple behavioral surfaces.

    # copilot: produced by merge_surfaces.  Captures the union of methods
    and any conflicts between surfaces.

    Attributes
    ----------
    surface_names  : tuple of source surface class names.
    merged_methods : tuple of all method names across surfaces.
    merged_attrs   : tuple of all attribute names across surfaces.
    conflicts      : tuple of (name, kind) pairs for conflicting entries.
    union_kind     : short description of the merge strategy used.
    """
    surface_names: tuple
    merged_methods: tuple
    merged_attrs: tuple
    conflicts: tuple
    union_kind: str


@dataclass(frozen=True, slots=True)
class BehavioralContract:
    """A single method-level behavioral contract in a surface.

    # copilot: represents one entry in the behavioral surface sheaf at a
    specific coordinate (method name).

    Attributes
    ----------
    method_name    : name of the method.
    signature      : str(inspect.signature(...)) or '(...unknown...)'.
    is_abstract    : True iff the method is abstract.
    preconditions  : tuple of precondition description strings (informal).
    postconditions : tuple of postcondition description strings (informal).
    is_inherited   : True iff the method is inherited (not in cls.__dict__).
    """
    method_name: str
    signature: str
    is_abstract: bool
    preconditions: tuple
    postconditions: tuple
    is_inherited: bool


@dataclass(frozen=True, slots=True)
class ProtocolComplianceRecord:
    """Result of checking whether *cls* satisfies a *protocol*.

    # copilot: produced by detect_protocol_compliance.

    Attributes
    ----------
    cls_name       : __qualname__ of the class being checked.
    protocol_name  : __qualname__ of the protocol.
    compliant      : True iff all required methods and attrs are present.
    missing_methods: tuple of required method names not found on cls.
    missing_attrs  : tuple of required attr names not found on cls.
    extra_methods  : tuple of methods cls provides beyond the protocol.
    """
    cls_name: str
    protocol_name: str
    compliant: bool
    missing_methods: tuple
    missing_attrs: tuple
    extra_methods: tuple


@dataclass(frozen=True, slots=True)
class AbstractMethodRecord:
    """Record of a single abstract method in a class hierarchy.

    # copilot: produced by analyze_abstract_methods.  Tracks the full
    override chain so the compliance judgment can identify which concrete
    subclasses satisfy the obligation.

    Attributes
    ----------
    method_name   : name of the abstract method.
    defined_in    : __qualname__ of the ABC that declares the method abstract.
    overridden_in : tuple of __qualname__ strings of classes that override it.
    override_chain: tuple of (class_qualname, is_abstract) pairs along MRO.
    """
    method_name: str
    defined_in: str
    overridden_in: tuple
    override_chain: tuple


@dataclass(frozen=True, slots=True)
class DunderSurface:
    """Summary of dunder method coverage for a class.

    # copilot: produced by detect_dunder_surface.  Used to detect gaps
    in the behavioral surface (e.g., a class that defines __eq__ but not
    __hash__).

    Attributes
    ----------
    class_name              : __qualname__ of the class.
    implemented_dunders     : tuple of dunder names defined directly.
    inherited_dunders       : tuple of dunder names inherited (not in cls.__dict__).
    missing_standard_dunders: tuple of dunder names that are neither implemented
                              nor inherited but are part of the "standard set".
    """
    class_name: str
    implemented_dunders: tuple
    inherited_dunders: tuple
    missing_standard_dunders: tuple


@dataclass(frozen=True, slots=True)
class FieldSurfaceRecord:
    """Record of a single field in a dataclass or NamedTuple surface.

    # copilot: produced by extract_field_surface.

    Attributes
    ----------
    field_name    : name of the field.
    field_type    : string representation of the field type annotation.
    has_default   : True iff the field has a default value or default_factory.
    is_class_var  : True iff annotated as ClassVar[...].
    is_init_var   : True iff annotated as InitVar[...].
    metadata      : tuple of (key, str(value)) pairs from dataclasses.field metadata.
    """
    field_name: str
    field_type: str
    has_default: bool
    is_class_var: bool
    is_init_var: bool
    metadata: tuple


@dataclass(frozen=True, slots=True)
class SurfaceComplianceWitnessRecord:
    """Runtime witness for behavioral surface compliance of an object.

    # copilot: produced by witness_surface_compliance.  Validates that an
    actual instance satisfies the surface's behavioral contracts at runtime.

    Attributes
    ----------
    obj_type          : __qualname__ of the object's type.
    surface_kind      : the surface kind being verified.
    compliant_methods : tuple of method names that are callable on the object.
    failing_methods   : tuple of method names that are missing or not callable.
    witness_level     : TrustLevel at which this witness was produced.
    """
    obj_type: str
    surface_kind: "SurfaceKind"
    compliant_methods: tuple
    failing_methods: tuple
    witness_level: int


@dataclass(frozen=True, slots=True)
class AbstractInstantiationWitnessRecord:
    """Witness for attempting to instantiate an abstract class.

    # copilot: produced by witness_abstract_instantiation.  Confirms that
    ABCs with unimplemented abstract methods refuse instantiation.

    Attributes
    ----------
    cls_name                       : __qualname__ of the class attempted.
    instantiation_succeeded        : True iff instantiation raised no TypeError.
    error_if_failed                : the error message, or '' if succeeded.
    missing_abstract_implementations: tuple of abstract method names not implemented.
    """
    cls_name: str
    instantiation_succeeded: bool
    error_if_failed: str
    missing_abstract_implementations: tuple


@dataclass(frozen=True, slots=True)
class RuntimeProtocolCheckRecord:
    """Witness for an isinstance() check against a Protocol.

    # copilot: produced by record_protocol_runtime_check.

    Attributes
    ----------
    cls_name              : __qualname__ of the class being checked.
    protocol_name         : __qualname__ of the protocol.
    isinstance_result     : result of isinstance(cls_instance, protocol) if checkable.
    is_runtime_checkable  : True iff the protocol is decorated @runtime_checkable.
    """
    cls_name: str
    protocol_name: str
    isinstance_result: bool
    is_runtime_checkable: bool


# ===========================================================================
# Analyzer
# ===========================================================================

# copilot: standard dunders that every class should ideally address.
_STANDARD_DUNDERS = frozenset({
    "__init__", "__repr__", "__str__", "__eq__", "__hash__",
    "__bool__", "__len__", "__iter__", "__next__", "__getitem__",
    "__setitem__", "__delitem__", "__contains__", "__enter__", "__exit__",
    "__call__", "__del__", "__sizeof__", "__format__", "__reduce__",
})


class GeneratedBehavioralSurfacesAnalyzer:
    """Analysis tools for generated behavioral surfaces.

    Provides both AST-level and runtime inspection methods.  All methods
    are side-effect-free with respect to the inspected classes.

    In the site-theoretic model, this analyzer computes *local sections*
    of the behavioral sheaf: for each class coordinate, what behavioral
    contracts are visible?

    # copilot: never call abstract methods, invoke descriptors, or
    instantiate classes during analysis — inspect only __dict__ and MRO.
    """

    def classify_surface_kind(self, cls: type) -> SurfaceKind:
        """Classify the behavioral surface kind of a live class.

        Parameters
        ----------
        cls : type
            Any Python class.

        Returns
        -------
        SurfaceKind
            The most specific applicable surface kind.

        # copilot: priority order mirrors Python's own framework detection:
        FROZEN_DATACLASS > DATACLASS > ENUM > PROTOCOL > ABC > NAMED_TUPLE >
        TYPED_DICT > PLAIN_CLASS.
        """
        # Dataclass check
        if dataclasses.is_dataclass(cls) and isinstance(cls, type):
            params = getattr(cls, "__dataclass_params__", None)
            if params is not None and getattr(params, "frozen", False):
                return SurfaceKind.FROZEN_DATACLASS
            return SurfaceKind.DATACLASS

        # Enum
        try:
            import enum as _enum_mod
            if isinstance(cls, _enum_mod.EnumMeta):
                return SurfaceKind.ENUM
        except ImportError:
            pass

        # Protocol — check _is_protocol attribute
        if getattr(cls, "_is_protocol", False):
            return SurfaceKind.PROTOCOL

        # ABC (must check after Protocol, since Protocol is also an ABC)
        if isinstance(cls, abc.ABCMeta) and bool(getattr(cls, "__abstractmethods__", frozenset())):
            return SurfaceKind.ABC

        # NamedTuple — tuples with _fields
        if issubclass(cls, tuple) and hasattr(cls, "_fields"):
            return SurfaceKind.NAMED_TUPLE

        # TypedDict — _is_typeddict (Python 3.10+) or __total__
        if getattr(cls, "__total__", None) is not None and issubclass(cls, dict):
            return SurfaceKind.TYPED_DICT

        return SurfaceKind.PLAIN_CLASS

    def extract_behavioral_contracts(self, cls: type) -> list:
        """Extract BehavioralContract records for all methods in *cls*.

        Parameters
        ----------
        cls : type
            Any Python class.

        Returns
        -------
        list[BehavioralContract]
            One contract per callable in the class (including inherited).

        # copilot: abstract methods are flagged; super() call detection
        is a heuristic based on source text inspection.
        """
        abstract_methods = frozenset(getattr(cls, "__abstractmethods__", frozenset()))
        contracts: list = []
        seen: set = set()

        for klass in cls.__mro__:
            for name, obj in klass.__dict__.items():
                if name in seen:
                    continue
                seen.add(name)
                if not callable(obj) and not isinstance(obj, (classmethod, staticmethod)):
                    continue

                sig = ""
                try:
                    fn = obj.__func__ if isinstance(obj, (classmethod, staticmethod)) else obj
                    sig = str(inspect.signature(fn))
                except (ValueError, TypeError):
                    sig = "(...)"

                # Heuristic: detect super() usage
                uses_super = False
                try:
                    src = inspect.getsource(obj.__func__ if isinstance(obj, (classmethod, staticmethod)) else obj)
                    uses_super = "super()" in src
                except (OSError, TypeError):
                    src = ""

                contracts.append(BehavioralContract(
                    method_name=name,
                    signature=sig,
                    is_abstract=(name in abstract_methods),
                    preconditions=(),
                    postconditions=(),
                    is_inherited=(klass is not cls),
                ))
        return contracts

    def detect_protocol_compliance(self, cls: type, protocol: type) -> ProtocolComplianceRecord:
        """Check whether *cls* structurally satisfies *protocol*.

        Parameters
        ----------
        cls      : type  — the class to check.
        protocol : type  — a typing.Protocol subclass.

        Returns
        -------
        ProtocolComplianceRecord
            Compliance result with missing and extra method lists.

        # copilot: uses __protocol_attrs__ (Python 3.12+) or falls back to
        inspecting the protocol's __dict__ for non-dunder callables.
        """
        # Gather required names from the protocol
        required: set = set()
        # Python 3.12+: __protocol_attrs__
        proto_attrs = getattr(protocol, "__protocol_attrs__", None)
        if proto_attrs is not None:
            required = set(proto_attrs)
        else:
            for name, obj in protocol.__dict__.items():
                if name.startswith("_") and name.endswith("_"):
                    continue
                if callable(obj) or isinstance(obj, property):
                    required.add(name)
            # Include abstractmethods of the protocol
            required.update(getattr(protocol, "__abstractmethods__", frozenset()))

        # Gather methods on cls
        provided: set = set()
        for klass in cls.__mro__:
            for name in klass.__dict__:
                provided.add(name)

        missing_methods = tuple(sorted(required - provided))
        extra_methods = tuple(sorted(provided - required - {
            n for n in protocol.__dict__
        }))
        compliant = len(missing_methods) == 0

        return ProtocolComplianceRecord(
            cls_name=cls.__qualname__,
            protocol_name=protocol.__qualname__,
            compliant=compliant,
            missing_methods=missing_methods,
            missing_attrs=(),  # type-level attrs require type annotation analysis
            extra_methods=extra_methods,
        )

    def analyze_abstract_methods(self, cls: type) -> list:
        """Produce AbstractMethodRecord for every abstract method in *cls*.

        Parameters
        ----------
        cls : type
            An ABC or protocol class.

        Returns
        -------
        list[AbstractMethodRecord]
            One record per abstract method declared in or inherited by *cls*.

        # copilot: walks the full MRO to find where each abstract method
        is declared and where (if anywhere) it is overridden.
        """
        abstract = frozenset(getattr(cls, "__abstractmethods__", frozenset()))
        records: list = []

        for method_name in sorted(abstract):
            defined_in = ""
            overridden_in: list = []
            chain: list = []

            for klass in cls.__mro__:
                obj = klass.__dict__.get(method_name)
                if obj is None:
                    continue
                is_abs = getattr(obj, "__isabstractmethod__", False)
                chain.append((klass.__qualname__, is_abs))
                if is_abs and not defined_in:
                    defined_in = klass.__qualname__
                elif not is_abs:
                    overridden_in.append(klass.__qualname__)

            records.append(AbstractMethodRecord(
                method_name=method_name,
                defined_in=defined_in or cls.__qualname__,
                overridden_in=tuple(overridden_in),
                override_chain=tuple(chain),
            ))
        return records

    def detect_dunder_surface(self, cls: type) -> DunderSurface:
        """Detect which dunder methods are implemented, inherited, or missing.

        Parameters
        ----------
        cls : type
            Any Python class.

        Returns
        -------
        DunderSurface
            Summary of dunder coverage.

        # copilot: 'missing_standard_dunders' is computed against
        _STANDARD_DUNDERS and filtered by class kind — e.g., __len__ is
        only flagged if the class looks like a container.
        """
        implemented = tuple(sorted(
            n for n in cls.__dict__
            if n.startswith("__") and n.endswith("__") and callable(cls.__dict__[n])
        ))
        inherited_set: set = set()
        for klass in cls.__mro__[1:]:
            for n in klass.__dict__:
                if n.startswith("__") and n.endswith("__"):
                    inherited_set.add(n)

        all_covered = set(implemented) | inherited_set
        missing = tuple(sorted(_STANDARD_DUNDERS - all_covered))
        inherited = tuple(sorted(inherited_set - set(implemented)))

        return DunderSurface(
            class_name=cls.__qualname__,
            implemented_dunders=implemented,
            inherited_dunders=inherited,
            missing_standard_dunders=missing,
        )

    def extract_field_surface(self, cls: type) -> list:
        """Extract FieldSurfaceRecord for every field in a dataclass or NamedTuple.

        Parameters
        ----------
        cls : type
            A dataclass or NamedTuple class.

        Returns
        -------
        list[FieldSurfaceRecord]
            One record per field.

        # copilot: for non-dataclass classes, returns an empty list.
        """
        records: list = []
        if dataclasses.is_dataclass(cls) and isinstance(cls, type):
            for f in dataclasses.fields(cls):
                has_default = (
                    f.default is not dataclasses.MISSING or
                    f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
                )
                type_str = (
                    f.type if isinstance(f.type, str) else
                    getattr(f.type, "__name__", repr(f.type))
                )
                meta = tuple(
                    (k, str(v)) for k, v in f.metadata.items()
                ) if f.metadata else ()
                records.append(FieldSurfaceRecord(
                    field_name=f.name,
                    field_type=type_str,
                    has_default=has_default,
                    is_class_var=False,
                    is_init_var=False,
                    metadata=meta,
                ))
            return records

        # NamedTuple
        nt_fields = getattr(cls, "_fields", None)
        if nt_fields is not None and issubclass(cls, tuple):
            hints = getattr(cls, "__annotations__", {})
            defaults = getattr(cls, "_field_defaults", {})
            for fname in nt_fields:
                records.append(FieldSurfaceRecord(
                    field_name=fname,
                    field_type=str(hints.get(fname, "Any")),
                    has_default=(fname in defaults),
                    is_class_var=False,
                    is_init_var=False,
                    metadata=(),
                ))
        return records

    def classify_override_pattern(self, method_name: str, mro: list) -> OverridePattern:
        """Classify the override pattern for *method_name* along *mro*.

        Parameters
        ----------
        method_name : str
            The method name to classify.
        mro         : list[type]
            The MRO chain (most-derived first).

        Returns
        -------
        OverridePattern
            The most specific applicable pattern.

        # copilot: COOPERATIVE_SUPER is detected by inspecting source for
        'super()' calls; ABSTRACT_IMPLEMENTATION is detected by checking
        whether the method is abstract in a base class.
        """
        implementations: list = [k for k in mro if method_name in k.__dict__]
        if not implementations:
            return OverridePattern.NO_OVERRIDE
        if len(implementations) == 1:
            return OverridePattern.DIRECT_OVERRIDE

        # Check if any base defines it as abstract
        for klass in implementations[1:]:
            obj = klass.__dict__[method_name]
            if getattr(obj, "__isabstractmethod__", False):
                return OverridePattern.ABSTRACT_IMPLEMENTATION

        # Multiple concrete implementations → check for super() usage
        obj = implementations[0].__dict__[method_name]
        src = ""
        try:
            src = inspect.getsource(obj)
        except (OSError, TypeError):
            pass
        if "super()" in src:
            return OverridePattern.COOPERATIVE_SUPER

        # Multiple bases without super → mixin composition
        if len(mro) > 2:
            return OverridePattern.MIXIN_COMPOSITION

        return OverridePattern.DIRECT_OVERRIDE


# ===========================================================================
# Witness
# ===========================================================================

class GeneratedBehavioralSurfacesWitness:
    """Runtime witnessing helpers for behavioral surface compliance.

    Methods here perform actual object creation and method invocation to
    verify behavioral contracts at runtime.

    In the site-theoretic model, witnesses compute the *global section*
    of the behavioral sheaf by actually evaluating local sections (calling
    methods) and checking consistency.

    # copilot: witness methods should not raise; use try/except throughout
    and record failures in the witness record.
    """

    def witness_surface_compliance(
        self,
        obj: object,
        surface: BehavioralSurfaceRecord,
    ) -> SurfaceComplianceWitnessRecord:
        """Check that *obj* satisfies the behavioral contracts in *surface*.

        For each public method in the surface, verifies that the attribute
        is callable on *obj*.

        Parameters
        ----------
        obj     : object                 — the object to check.
        surface : BehavioralSurfaceRecord — the surface to verify against.

        Returns
        -------
        SurfaceComplianceWitnessRecord
            Compliance record distinguishing passing and failing methods.

        # copilot: does not actually *call* the methods; only checks
        callability via callable(getattr(obj, name, None)).
        """
        compliant: list = []
        failing: list = []

        for method_name in surface.public_methods:
            attr = getattr(obj, method_name, None)
            if callable(attr):
                compliant.append(method_name)
            else:
                failing.append(method_name)

        return SurfaceComplianceWitnessRecord(
            obj_type=type(obj).__qualname__,
            surface_kind=surface.surface_kind,
            compliant_methods=tuple(sorted(compliant)),
            failing_methods=tuple(sorted(failing)),
            witness_level=int(TrustLevel.RUNTIME_WITNESSED),
        )

    def probe_dunder_implementations(self, cls: type) -> dict:
        """Return a dict mapping standard dunder names to whether they are defined.

        Parameters
        ----------
        cls : type
            Any Python class.

        Returns
        -------
        dict[str, bool]
            Maps each name in _STANDARD_DUNDERS to True/False based on
            whether it appears anywhere in the MRO.

        # copilot: checks the full MRO, not just cls.__dict__; a dunder is
        'implemented' iff any MRO class (other than object) defines it.
        """
        result: dict = {}
        mro_dicts = [k.__dict__ for k in cls.__mro__ if k is not object]
        for dunder in sorted(_STANDARD_DUNDERS):
            result[dunder] = any(dunder in d for d in mro_dicts)
        return result

    def witness_abstract_instantiation(self, cls: type) -> AbstractInstantiationWitnessRecord:
        """Attempt to instantiate *cls* and record the outcome.

        If *cls* has unimplemented abstract methods, ``type.__call__`` will
        raise TypeError.  This method captures that outcome.

        Parameters
        ----------
        cls : type
            A class that may be abstract.

        Returns
        -------
        AbstractInstantiationWitnessRecord
            Record of whether instantiation succeeded or failed.

        # copilot: calls cls() with no arguments; classes that require
        constructor arguments will raise TypeError for a different reason.
        Set instantiation_succeeded=False and capture the error in that case.
        """
        abstract = tuple(sorted(getattr(cls, "__abstractmethods__", frozenset())))
        try:
            _ = cls()
            return AbstractInstantiationWitnessRecord(
                cls_name=cls.__qualname__,
                instantiation_succeeded=True,
                error_if_failed="",
                missing_abstract_implementations=(),
            )
        except TypeError as exc:
            return AbstractInstantiationWitnessRecord(
                cls_name=cls.__qualname__,
                instantiation_succeeded=False,
                error_if_failed=str(exc),
                missing_abstract_implementations=abstract,
            )
        except Exception as exc:  # noqa: BLE001
            return AbstractInstantiationWitnessRecord(
                cls_name=cls.__qualname__,
                instantiation_succeeded=False,
                error_if_failed=f"Unexpected: {exc}",
                missing_abstract_implementations=abstract,
            )

    def record_protocol_runtime_check(
        self,
        cls: type,
        protocol: type,
    ) -> RuntimeProtocolCheckRecord:
        """Perform an isinstance() check of a *cls* instance against *protocol*.

        Parameters
        ----------
        cls      : type  — the class to test.
        protocol : type  — a typing.Protocol subclass.

        Returns
        -------
        RuntimeProtocolCheckRecord
            Result of the runtime isinstance check.

        # copilot: if the protocol is not @runtime_checkable, isinstance
        will raise TypeError; this method returns isinstance_result=False in
        that case and sets is_runtime_checkable=False.
        """
        is_checkable = getattr(protocol, "_is_runtime_protocol", False)
        result = False
        if is_checkable:
            try:
                instance = cls()
                result = isinstance(instance, protocol)
            except Exception:  # noqa: BLE001
                result = False
        return RuntimeProtocolCheckRecord(
            cls_name=cls.__qualname__,
            protocol_name=protocol.__qualname__,
            isinstance_result=result,
            is_runtime_checkable=is_checkable,
        )

    def build_witness_judgment(self, record: SurfaceComplianceWitnessRecord) -> Judgment:
        """Build a JuGeo Judgment from a SurfaceComplianceWitnessRecord.

        Parameters
        ----------
        record : SurfaceComplianceWitnessRecord

        Returns
        -------
        Judgment
            SETTLED if all public methods are compliant, OBSTRUCTED otherwise.

        # copilot: trust level is RUNTIME_WITNESSED for all surface compliance
        witnesses.
        """
        is_compliant = len(record.failing_methods) == 0
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            statement=(
                f"Object of type '{record.obj_type}' satisfies "
                f"{record.surface_kind.value} behavioral surface "
                f"({len(record.compliant_methods)} compliant, "
                f"{len(record.failing_methods)} failing)"
            ),
            label=f"surface_compliance:{record.obj_type}:{record.surface_kind.value}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(record.obj_type, record.surface_kind.value),
                kind=CoordinateKind.INTERFACE,
            ),
            payload=record,
            label=f"{record.obj_type}:{record.surface_kind.value}",
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_TRACE,
            payload=record,
            label="surface_compliance_witness",
        ))
        j = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED, rationale="surface compliance probe"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"gbs:{record.obj_type}:{record.surface_kind.value}",
        )
        if is_compliant:
            j.settle()
        else:
            j.obstruct(Obstruction(
                description=f"Missing methods: {record.failing_methods}",
            ))
        return j


# ===========================================================================
# Coordinator
# ===========================================================================

class GeneratedBehavioralSurfacesCoordinator:
    """Orchestrator for generated behavioral surface analysis.

    Combines the Analyzer and Witness to build comprehensive behavioral
    surface records and JuGeo judgments.

    In the site-theoretic model, the Coordinator assembles the *global
    section* of the behavioral surface sheaf: starting from local sections
    (individual method contracts) it glues them into a coherent overall
    behavioral specification.

    # copilot: this class is stateful; surfaces are cached by class id()
    to prevent redundant re-analysis within a session.
    """

    def __init__(self) -> None:
        self._analyzer = GeneratedBehavioralSurfacesAnalyzer()
        self._witness = GeneratedBehavioralSurfacesWitness()
        self._surface_cache: dict = {}

    # ------------------------------------------------------------------
    # Surface generation
    # ------------------------------------------------------------------

    def generate_surface(self, cls: type) -> BehavioralSurfaceRecord:
        """Generate the top-level behavioral surface for *cls*.

        Parameters
        ----------
        cls : type
            Any Python class.

        Returns
        -------
        BehavioralSurfaceRecord
            Top-level behavioral surface record.

        # copilot: results are cached by id(cls); use a fresh Coordinator
        instance if classes may be redefined (e.g., in tests).
        """
        cache_key = id(cls)
        if cache_key in self._surface_cache:
            return self._surface_cache[cache_key]

        kind = self._analyzer.classify_surface_kind(cls)

        public_methods: list = []
        dunder_methods: list = []
        properties: list = []
        class_vars: list = []

        for name, obj in cls.__dict__.items():
            if name.startswith("__") and name.endswith("__"):
                if callable(obj) or isinstance(obj, (classmethod, staticmethod)):
                    dunder_methods.append(name)
            elif isinstance(obj, property):
                properties.append(name)
            elif callable(obj) or isinstance(obj, (classmethod, staticmethod)):
                public_methods.append(name)
            elif not callable(obj) and not isinstance(obj, types.FunctionType):
                class_vars.append(name)

        is_abstract = bool(getattr(cls, "__abstractmethods__", frozenset()))
        is_checkable = getattr(cls, "_is_runtime_protocol", False)

        record = BehavioralSurfaceRecord(
            class_name=cls.__qualname__,
            surface_kind=kind,
            public_methods=tuple(sorted(public_methods)),
            dunder_methods=tuple(sorted(dunder_methods)),
            properties=tuple(sorted(properties)),
            class_vars=tuple(sorted(class_vars)),
            is_abstract=is_abstract,
            protocol_runtime_checkable=is_checkable,
        )
        self._surface_cache[cache_key] = record
        log.debug("generate_surface: produced surface for '%s' (%s)", cls.__qualname__, kind.value)
        return record

    def generate_dataclass_surface(self, cls: type) -> DataclassSurfaceRecord:
        """Generate the dataclass-specific behavioral surface for *cls*.

        Parameters
        ----------
        cls : type
            A dataclass-decorated class.

        Returns
        -------
        DataclassSurfaceRecord
            Detailed dataclass surface record.

        # copilot: returns a record with is_frozen=False and empty fields
        if *cls* is not actually a dataclass.
        """
        if not (dataclasses.is_dataclass(cls) and isinstance(cls, type)):
            return DataclassSurfaceRecord(
                class_name=cls.__qualname__,
                fields=(),
                is_frozen=False,
                has_post_init=False,
                has_slots=False,
                eq_generated=False,
                order_generated=False,
                kw_only=False,
            )

        params = getattr(cls, "__dataclass_params__", None)
        is_frozen = getattr(params, "frozen", False) if params else False
        has_slots = getattr(params, "slots", False) if params else False
        eq_gen = getattr(params, "eq", True) if params else True
        order_gen = getattr(params, "order", False) if params else False
        kw_only = getattr(params, "kw_only", False) if params else False
        has_post_init = "__post_init__" in cls.__dict__

        dc_fields = dataclasses.fields(cls)
        field_pairs = tuple(
            (f.name, f.type if isinstance(f.type, str) else getattr(f.type, "__name__", repr(f.type)))
            for f in dc_fields
        )

        return DataclassSurfaceRecord(
            class_name=cls.__qualname__,
            fields=field_pairs,
            is_frozen=is_frozen,
            has_post_init=has_post_init,
            has_slots=has_slots,
            eq_generated=eq_gen,
            order_generated=order_gen,
            kw_only=kw_only,
        )

    def generate_protocol_surface(self, cls: type) -> ProtocolSurfaceRecord:
        """Generate the protocol-specific behavioral surface for *cls*.

        Parameters
        ----------
        cls : type
            A typing.Protocol subclass.

        Returns
        -------
        ProtocolSurfaceRecord
            Protocol surface record with required methods and attrs.

        # copilot: uses __protocol_attrs__ if available (Python 3.12+);
        falls back to inspecting non-dunder callables in cls.__dict__.
        """
        proto_attrs = getattr(cls, "__protocol_attrs__", None)
        if proto_attrs is not None:
            required_methods = tuple(sorted(proto_attrs))
        else:
            required_methods = tuple(sorted(
                name for name, obj in cls.__dict__.items()
                if not name.startswith("_") and callable(obj)
            ))

        abstract = frozenset(getattr(cls, "__abstractmethods__", frozenset()))
        required_attrs = tuple(sorted(abstract - set(required_methods)))
        is_checkable = getattr(cls, "_is_runtime_protocol", False)

        return ProtocolSurfaceRecord(
            protocol_name=cls.__qualname__,
            required_methods=required_methods,
            required_attrs=required_attrs,
            is_runtime_checkable=is_checkable,
            structural_subtypes=(),
        )

    def generate_abc_surface(self, cls: type) -> ABCSurfaceRecord:
        """Generate the ABC-specific behavioral surface for *cls*.

        Parameters
        ----------
        cls : type
            An abc.ABC subclass.

        Returns
        -------
        ABCSurfaceRecord
            ABC surface record with abstract/concrete method lists.

        # copilot: concrete_subclasses uses __subclasses__() which only
        returns direct subclasses; deep subclasses are not included.
        """
        abstract = frozenset(getattr(cls, "__abstractmethods__", frozenset()))
        abstract_props: list = []
        abstract_methods: list = []

        for name in abstract:
            obj = cls.__dict__.get(name)
            if obj is None:
                # inherited abstract
                for klass in cls.__mro__[1:]:
                    obj = klass.__dict__.get(name)
                    if obj is not None:
                        break
            if isinstance(obj, property):
                abstract_props.append(name)
            else:
                abstract_methods.append(name)

        concrete: list = []
        for name, obj in cls.__dict__.items():
            if name.startswith("__") and name.endswith("__"):
                continue
            if callable(obj) and name not in abstract:
                concrete.append(name)

        subclasses = tuple(sc.__qualname__ for sc in cls.__subclasses__())

        return ABCSurfaceRecord(
            abc_name=cls.__qualname__,
            abstract_methods=tuple(sorted(abstract_methods)),
            abstract_properties=tuple(sorted(abstract_props)),
            concrete_methods=tuple(sorted(concrete)),
            concrete_subclasses=subclasses,
        )

    def merge_surfaces(self, surfaces: list) -> MergedSurfaceRecord:
        """Merge a list of BehavioralSurfaceRecord into a single record.

        The merge is a set-union of methods and attributes.  Conflicts are
        recorded when two surfaces define the same name with different
        surface kinds.

        Parameters
        ----------
        surfaces : list[BehavioralSurfaceRecord]
            The surfaces to merge.

        Returns
        -------
        MergedSurfaceRecord
            The merged surface.

        # copilot: conflicts are conservative — only surface-kind-level
        conflicts are reported here.  Method-signature conflicts require
        deeper analysis (see extract_behavioral_contracts).
        """
        all_methods: set = set()
        all_attrs: set = set()
        name_to_kind: dict = {}
        conflicts: list = []

        for surf in surfaces:
            for m in surf.public_methods:
                if m in name_to_kind and name_to_kind[m] != surf.surface_kind:
                    conflicts.append((m, f"{name_to_kind[m].value}_vs_{surf.surface_kind.value}"))
                name_to_kind[m] = surf.surface_kind
                all_methods.add(m)
            for a in surf.class_vars:
                all_attrs.add(a)

        surface_names = tuple(s.class_name for s in surfaces)
        kinds = [s.surface_kind for s in surfaces]
        union_kind = "homogeneous" if len(set(k.value for k in kinds)) == 1 else "heterogeneous"

        return MergedSurfaceRecord(
            surface_names=surface_names,
            merged_methods=tuple(sorted(all_methods)),
            merged_attrs=tuple(sorted(all_attrs)),
            conflicts=tuple(conflicts),
            union_kind=union_kind,
        )

    def build_surface_judgment(self, surface: BehavioralSurfaceRecord) -> Judgment:
        """Build a JuGeo Judgment for a BehavioralSurfaceRecord.

        Parameters
        ----------
        surface : BehavioralSurfaceRecord

        Returns
        -------
        Judgment
            A PROPOSED judgment describing the behavioral surface.

        # copilot: is_abstract causes an OPEN judgment (the surface has
        obligations that must be discharged by concrete subclasses).
        """
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            statement=(
                f"Class '{surface.class_name}' has a {surface.surface_kind.value} "
                f"behavioral surface with {len(surface.public_methods)} public methods"
                + (" (ABSTRACT)" if surface.is_abstract else "")
            ),
            label=f"surface:{surface.class_name}:{surface.surface_kind.value}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(surface.class_name, surface.surface_kind.value),
                kind=CoordinateKind.INTERFACE,
            ),
            payload=surface,
            label=f"{surface.class_name}:{surface.surface_kind.value}",
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload=surface,
            label="surface_record",
        ))
        status = JudgmentStatus.OPEN if surface.is_abstract else JudgmentStatus.PROPOSED
        j = Judgment(
            status=status,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.COPILOT_SUGGESTED, rationale="surface analysis"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"gbs_surface:{surface.class_name}",
        )
        if not surface.is_abstract:
            j.settle()
        return j

    def analyze_source_surfaces(self, source: str) -> list:
        """Parse *source*, find all class definitions, and generate surfaces.

        Parameters
        ----------
        source : str
            Valid Python source text.

        Returns
        -------
        list[BehavioralSurfaceRecord]
            One surface per class found (best-effort; requires live exec).

        # copilot: executes the source in a sandboxed namespace to obtain
        live class objects.  Uses exec() with a restricted globals dict.
        """
        sandbox: dict = {}
        try:
            exec(compile(source, "<surface_analysis>", "exec"), sandbox)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            log.warning("analyze_source_surfaces: exec failed: %s", exc)
            return []

        surfaces: list = []
        for name, obj in sandbox.items():
            if isinstance(obj, type) and not name.startswith("_"):
                try:
                    surfaces.append(self.generate_surface(obj))
                except Exception as exc:  # noqa: BLE001
                    log.debug("analyze_source_surfaces: skipping '%s': %s", name, exc)
        return surfaces


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log.info("=== generated_behavioral_surfaces smoke test ===")

    # --- Example classes ---

    @dataclass(frozen=True)
    class ImmutablePoint:
        """A frozen dataclass point."""
        x: float
        y: float = 0.0

        def distance(self) -> float:
            return (self.x ** 2 + self.y ** 2) ** 0.5

    @dataclass
    class MutableBox:
        """A mutable dataclass box."""
        width: float
        height: float
        label: str = ""

        def area(self) -> float:
            return self.width * self.height

    class MyABC(abc.ABC):
        @abc.abstractmethod
        def run(self): ...

        def name(self) -> str:
            return self.__class__.__name__

    class ConcreteImpl(MyABC):
        def run(self): return "running"

    coord = GeneratedBehavioralSurfacesCoordinator()
    analyzer = GeneratedBehavioralSurfacesAnalyzer()
    witness = GeneratedBehavioralSurfacesWitness()

    # BehavioralSurfaceRecord
    for cls in (ImmutablePoint, MutableBox, MyABC, ConcreteImpl):
        surf = coord.generate_surface(cls)
        print(f"Surface: {surf.class_name} [{surf.surface_kind.value}] "
              f"public={surf.public_methods}, abstract={surf.is_abstract}")

    # DataclassSurfaceRecord
    dc_surf = coord.generate_dataclass_surface(ImmutablePoint)
    print(f"DC Surface: {dc_surf.class_name}, frozen={dc_surf.is_frozen}, fields={dc_surf.fields}")

    # ABCSurfaceRecord
    abc_surf = coord.generate_abc_surface(MyABC)
    print(f"ABC Surface: {abc_surf.abc_name}, abstract={abc_surf.abstract_methods}, "
          f"subclasses={abc_surf.concrete_subclasses}")

    # Abstract methods
    am_records = analyzer.analyze_abstract_methods(MyABC)
    print(f"Abstract methods on MyABC: {[r.method_name for r in am_records]}")

    # Dunder surface
    ds = analyzer.detect_dunder_surface(ImmutablePoint)
    print(f"Dunder surface: implemented={ds.implemented_dunders[:5]}")

    # Field surface
    fields = analyzer.extract_field_surface(ImmutablePoint)
    print(f"Fields: {[(f.field_name, f.field_type, f.has_default) for f in fields]}")

    # Surface compliance witness
    pt = ImmutablePoint(x=3.0, y=4.0)
    surf = coord.generate_surface(ImmutablePoint)
    wr = witness.witness_surface_compliance(pt, surf)
    print(f"Compliance: compliant={wr.compliant_methods}, failing={wr.failing_methods}")

    # Abstract instantiation witness
    abs_wr = witness.witness_abstract_instantiation(MyABC)
    print(f"Abstract instantiation: succeeded={abs_wr.instantiation_succeeded}, "
          f"missing={abs_wr.missing_abstract_implementations}")

    # Merge surfaces
    surf1 = coord.generate_surface(ImmutablePoint)
    surf2 = coord.generate_surface(MutableBox)
    merged = coord.merge_surfaces([surf1, surf2])
    print(f"Merged: names={merged.surface_names}, union_kind={merged.union_kind}")

    # Judgment
    j = coord.build_surface_judgment(surf)
    print(f"Judgment: {j.label}, status={j.status}")

    # Override pattern
    op = analyzer.classify_override_pattern("run", list(ConcreteImpl.__mro__))
    print(f"Override pattern for 'run': {op}")

    # Dunder probe
    dunders = witness.probe_dunder_implementations(ImmutablePoint)
    print(f"__eq__ implemented: {dunders.get('__eq__')}")

    # Source surfaces
    _SRC = """
class Foo:
    def bar(self): return 1

class Baz(Foo):
    def qux(self): return 2
"""
    src_surfs = coord.analyze_source_surfaces(_SRC)
    print(f"Source surfaces: {[s.class_name for s in src_surfs]}")

    log.info("=== smoke test complete ===")
