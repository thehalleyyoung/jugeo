"""
descriptor_resolution_routes.py
====================================
theory2.tex — Chapter 20 §2: "Descriptor resolution routes"

Python descriptor resolution follows MRO order with the following priority:
  1. Data descriptors   (define both __get__ and __set__, or __delete__)
     take priority over instance __dict__.
  2. Instance __dict__  overrides non-data descriptors.
  3. Non-data descriptors (only __get__) are the lowest-priority class-level
     attribute source.

In JuGeo's site-theoretic interpretation:
  • The MRO chain is the *covering family* for the class coordinate.
  • Descriptor lookup (attribute access) is the *stalk computation*: at each
    coordinate (class in MRO), we compute the local section (the descriptor
    or plain value), and the global section is determined by the priority
    order above.
  • A DescriptorConflict is an incompatible cocycle condition — the covering
    family cannot be glued into a consistent global section.

This module implements:
  • DescriptorResolutionRoutesCoordinator — orchestrates resolution analysis.
  • DescriptorResolutionRoutesAnalyzer    — static / AST analysis tools.
  • DescriptorResolutionRoutesWitness     — runtime witnessing helpers.

Cross-references:
  theory2.tex §20.2 (descriptor routes), §20.1 (staged semantics),
  §14.3 (stalk computation), §18.6 (covering families).
"""
from __future__ import annotations

import ast
import inspect
import logging
import types
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

class DescriptorKind(str, Enum):
    """Classification of a descriptor by its protocol completeness.

    # copilot: DATA vs NON_DATA is the critical distinction for resolution
    priority.  DATA descriptors with __set__ take precedence over instance
    __dict__; NON_DATA descriptors do not.

    DATA        — defines both __get__ and __set__ (and/or __delete__).
    NON_DATA    — defines only __get__.
    CLASSMETHOD — classmethod descriptor.
    STATICMETHOD— staticmethod descriptor.
    PROPERTY    — property() descriptor (is DATA: has fget/fset/fdel).
    SLOT        — slot descriptor created by __slots__.
    """
    DATA = "data"
    NON_DATA = "non_data"
    CLASSMETHOD = "classmethod"
    STATICMETHOD = "staticmethod"
    PROPERTY = "property"
    SLOT = "slot"


class ResolutionRoute(str, Enum):
    """The route taken by attribute lookup during descriptor resolution.

    # copilot: corresponds to the six branches of type.__getattribute__.

    DATA_DESCRIPTOR    — found a data descriptor in the MRO.
    INSTANCE_DICT      — found in the instance __dict__.
    NON_DATA_DESCRIPTOR— found a non-data descriptor in the MRO.
    CLASS_ATTR         — found a plain (non-descriptor) class attribute.
    MRO_FALLBACK       — found only via object.__getattribute__ fallback.
    MISSING            — attribute does not exist.
    """
    DATA_DESCRIPTOR = "data_descriptor"
    INSTANCE_DICT = "instance_dict"
    NON_DATA_DESCRIPTOR = "non_data_descriptor"
    CLASS_ATTR = "class_attr"
    MRO_FALLBACK = "mro_fallback"
    MISSING = "missing"


# ===========================================================================
# Value-object dataclasses (frozen, slots)
# ===========================================================================

@dataclass(frozen=True, slots=True)
class DescriptorResolutionResult:
    """Result of a descriptor resolution query for a single attribute.

    # copilot: produced by DescriptorResolutionRoutesCoordinator.resolve_attribute.
    Captures both the route taken and the descriptor metadata.

    Attributes
    ----------
    obj_type         : __qualname__ of the object's type.
    attr_name        : the attribute name queried.
    resolution_route : which resolution branch was taken.
    descriptor_class : __qualname__ of the descriptor type (if any), else ''.
    descriptor_kind  : DescriptorKind of the descriptor (if any).
    found_in_class   : __qualname__ of the MRO class where the attr was found.
    """
    obj_type: str
    attr_name: str
    resolution_route: ResolutionRoute
    descriptor_class: str
    descriptor_kind: "DescriptorKind"
    found_in_class: str


@dataclass(frozen=True, slots=True)
class MROLookupTrace:
    """Trace of an MRO-order attribute search.

    # copilot: one MROLookupTrace per (cls, attr_name) pair.  The
    found_at_index field gives the MRO position (0 = most-derived).

    Attributes
    ----------
    cls_name         : __qualname__ of the starting class.
    attr_name        : attribute name being searched.
    mro_chain        : tuple of __qualname__ strings in MRO order.
    found_at_index   : index into mro_chain where the attr was found, or -1.
    found_in_class   : __qualname__ of the class that owns the attr, or ''.
    descriptor_kind  : DescriptorKind of the found attr, or None.
    """
    cls_name: str
    attr_name: str
    mro_chain: tuple
    found_at_index: int
    found_in_class: str
    descriptor_kind: "DescriptorKind | None"


@dataclass(frozen=True, slots=True)
class DescriptorAnalysisRecord:
    """Summary of one descriptor attribute in a class.

    # copilot: produced by analyze_class_descriptors; one record per
    descriptor-protocol-satisfying attribute in the class dict.

    Attributes
    ----------
    class_name       : __qualname__ of the owning class.
    attr_name        : attribute name.
    descriptor_class : __qualname__ of the descriptor type.
    kind             : DescriptorKind classification.
    priority         : resolution priority (lower = higher priority).
    is_inherited     : True iff the descriptor is inherited (not in cls.__dict__).
    """
    class_name: str
    attr_name: str
    descriptor_class: str
    kind: "DescriptorKind"
    priority: int
    is_inherited: bool


@dataclass(frozen=True, slots=True)
class PropertyUsageRecord:
    """AST-level record of a property definition in a class body.

    # copilot: extracted by DescriptorResolutionRoutesAnalyzer.classify_property_usage.

    Attributes
    ----------
    attr_name   : name of the property attribute.
    has_getter  : True iff a @property getter is defined.
    has_setter  : True iff a @<name>.setter is defined.
    has_deleter : True iff a @<name>.deleter is defined.
    line_no     : source line of the getter definition.
    """
    attr_name: str
    has_getter: bool
    has_setter: bool
    has_deleter: bool
    line_no: int


@dataclass(frozen=True, slots=True)
class DescriptorConflict:
    """Record of a descriptor name collision between two MRO classes.

    # copilot: conflicts arise when the same name is defined by two classes
    in the MRO with incompatible descriptor kinds (e.g., data vs non-data).

    Attributes
    ----------
    attr_name     : the conflicting attribute name.
    class1        : __qualname__ of the higher-MRO class.
    class2        : __qualname__ of the lower-MRO class.
    conflict_kind : short description of the kind of conflict.
    severity      : 'warning' or 'error'.
    """
    attr_name: str
    class1: str
    class2: str
    conflict_kind: str
    severity: str


@dataclass(frozen=True, slots=True)
class SlotsAnalysisRecord:
    """Summary of __slots__ usage in a class.

    # copilot: slot descriptors are a special category — they are data
    descriptors created automatically by the runtime, not by user code.

    Attributes
    ----------
    class_name          : __qualname__ of the class.
    has_slots           : True iff the class defines __slots__.
    slot_names          : tuple of slot names.
    conflicts_with_bases: tuple of slot names that collide with base slots.
    slot_count          : total number of slots.
    """
    class_name: str
    has_slots: bool
    slot_names: tuple
    conflicts_with_bases: tuple
    slot_count: int


@dataclass(frozen=True, slots=True)
class GetAttrWitnessRecord:
    """Witness record for a single getattr() call.

    # copilot: produced by DescriptorResolutionRoutesWitness.witness_getattr.
    Does NOT hold a reference to the resolved value; only its type name.

    Attributes
    ----------
    obj_type          : __qualname__ of the object's type.
    attr_name         : attribute name accessed.
    resolution_route  : the route taken.
    value_type        : __qualname__ of the resolved value's type, or 'MISSING'.
    descriptor_invoked: True iff a descriptor's __get__ was invoked.
    """
    obj_type: str
    attr_name: str
    resolution_route: "ResolutionRoute"
    value_type: str
    descriptor_invoked: bool


@dataclass(frozen=True, slots=True)
class SetAttrWitnessRecord:
    """Witness record for a single setattr() call.

    # copilot: produced by DescriptorResolutionRoutesWitness.witness_setattr.

    Attributes
    ----------
    obj_type          : __qualname__ of the object's type.
    attr_name         : attribute name written.
    value_type        : __qualname__ of the written value's type.
    descriptor_invoked: True iff a data descriptor's __set__ was invoked.
    slot_set          : True iff the attribute is a slot.
    """
    obj_type: str
    attr_name: str
    value_type: str
    descriptor_invoked: bool
    slot_set: bool


@dataclass(frozen=True, slots=True)
class DescriptorProtocolProbe:
    """Probe results for a single descriptor object.

    # copilot: produced by probe_descriptor_protocol.  Records which
    protocol methods are present and whether the descriptor is a data
    descriptor.

    Attributes
    ----------
    descriptor_class  : __qualname__ of the descriptor's type.
    has_get           : True iff type defines __get__.
    has_set           : True iff type defines __set__.
    has_delete        : True iff type defines __delete__.
    is_data_descriptor: True iff has_get and (has_set or has_delete).
    get_signature     : inspect.signature string for __get__, or ''.
    """
    descriptor_class: str
    has_get: bool
    has_set: bool
    has_delete: bool
    is_data_descriptor: bool
    get_signature: str


# ===========================================================================
# Analyzer
# ===========================================================================

class DescriptorResolutionRoutesAnalyzer:
    """Static and AST-based analysis tools for descriptor resolution routes.

    All methods are pure functions over class objects or source text.  No
    side effects are performed on the inspected classes.

    In the site-theoretic model, this analyzer computes the *sections* of
    the sheaf: for each coordinate (class in MRO), which local descriptor
    sections are defined, and whether they are compatible.

    # copilot: keep methods side-effect-free; never call __get__ or __set__
    on descriptors during analysis — use type(d).__dict__ inspection only.
    """

    # ------------------------------------------------------------------
    # Descriptor discovery
    # ------------------------------------------------------------------

    def find_data_descriptors(self, cls: type) -> list:
        """Return attribute names in *cls* that are data descriptors.

        A data descriptor defines both __get__ and (__set__ or __delete__).
        This method walks the full MRO and returns all names for which a
        data descriptor is found at any level.

        Parameters
        ----------
        cls : type
            The class to inspect.

        Returns
        -------
        list[str]
            Sorted list of attribute names backed by data descriptors.

        # copilot: uses type(v).__dict__ inspection to avoid triggering __get__.
        """
        names: list = []
        seen: set = set()
        for klass in cls.__mro__:
            for name, obj in klass.__dict__.items():
                if name in seen:
                    continue
                seen.add(name)
                t = type(obj)
                if "__get__" in t.__dict__ and ("__set__" in t.__dict__ or "__delete__" in t.__dict__):
                    names.append(name)
        return sorted(names)

    def find_non_data_descriptors(self, cls: type) -> list:
        """Return attribute names in *cls* that are non-data descriptors.

        A non-data descriptor defines __get__ but NOT __set__ or __delete__.

        Parameters
        ----------
        cls : type
            The class to inspect.

        Returns
        -------
        list[str]
            Sorted list of attribute names backed by non-data descriptors.

        # copilot: functions are non-data descriptors; this method will
        include all method names, which is typically the majority of results.
        """
        names: list = []
        seen: set = set()
        for klass in cls.__mro__:
            for name, obj in klass.__dict__.items():
                if name in seen:
                    continue
                seen.add(name)
                t = type(obj)
                if "__get__" in t.__dict__ and "__set__" not in t.__dict__ and "__delete__" not in t.__dict__:
                    names.append(name)
        return sorted(names)

    def classify_property_usage(self, node: ast.ClassDef) -> list:
        """Extract property usage records from an ast.ClassDef.

        Detects:
          - ``@property`` decorated methods.
          - ``@<name>.setter`` / ``@<name>.deleter`` decorated methods.

        Parameters
        ----------
        node : ast.ClassDef
            Class definition AST node.

        Returns
        -------
        list[PropertyUsageRecord]
            One record per property name found.

        # copilot: merges getter, setter, deleter into a single record per
        property name.  Multiple decorators on a single method are handled.
        """
        getters: dict = {}
        setters: set = set()
        deleters: set = set()

        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in stmt.decorator_list:
                # @property
                if isinstance(dec, ast.Name) and dec.id == "property":
                    getters[stmt.name] = stmt.lineno
                # @<name>.setter  or  @<name>.deleter
                elif isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                    prop_name = dec.value.id
                    if dec.attr == "setter":
                        setters.add(prop_name)
                    elif dec.attr == "deleter":
                        deleters.add(prop_name)
                    elif dec.attr == "getter":
                        getters.setdefault(prop_name, stmt.lineno)

        records: list = []
        for name, line in getters.items():
            records.append(PropertyUsageRecord(
                attr_name=name,
                has_getter=True,
                has_setter=name in setters,
                has_deleter=name in deleters,
                line_no=line,
            ))
        return records

    def detect_descriptor_conflicts(self, cls: type) -> list:
        """Find descriptor name collisions in *cls*'s MRO.

        A conflict exists when the same attribute name is defined by two
        different classes in the MRO with incompatible descriptor kinds.
        For example, if one class defines a data descriptor and a subclass
        defines a non-data descriptor with the same name, the data descriptor
        wins silently — which may be surprising.

        Parameters
        ----------
        cls : type
            The class to inspect.

        Returns
        -------
        list[DescriptorConflict]
            One DescriptorConflict per detected collision.

        # copilot: only reports conflicts where BOTH classes define a
        descriptor (not just a plain value) — plain shadowing is expected.
        """
        name_to_class: dict = {}
        name_to_kind: dict = {}
        conflicts: list = []

        for klass in cls.__mro__:
            for name, obj in klass.__dict__.items():
                t = type(obj)
                has_get = "__get__" in t.__dict__
                has_set = "__set__" in t.__dict__
                has_del = "__delete__" in t.__dict__
                if not has_get:
                    continue
                kind = DescriptorKind.DATA if (has_set or has_del) else DescriptorKind.NON_DATA

                if name in name_to_class:
                    prev_kind = name_to_kind[name]
                    if prev_kind != kind:
                        conflicts.append(DescriptorConflict(
                            attr_name=name,
                            class1=name_to_class[name].__qualname__,
                            class2=klass.__qualname__,
                            conflict_kind=f"{prev_kind.value}_vs_{kind.value}",
                            severity="warning",
                        ))
                else:
                    name_to_class[name] = klass
                    name_to_kind[name] = kind

        return conflicts

    def build_mro_covering_family(self, cls: type) -> list:
        """Return the MRO as a list of __qualname__ strings.

        In JuGeo's Grothendieck topology, the MRO is a covering family
        for the class coordinate: each element is an open cover that
        contributes local sections (descriptors / class attributes).

        Parameters
        ----------
        cls : type
            The class whose MRO covering family to compute.

        Returns
        -------
        list[str]
            Qualified names in MRO order (most-derived first).

        # copilot: excludes ``object`` from the covering family by convention
        since it contributes only default implementations.
        """
        mro = getattr(cls, "__mro__", (cls,))
        return [c.__qualname__ for c in mro if c is not object]

    def analyze_slots_vs_dict(self, cls: type) -> SlotsAnalysisRecord:
        """Analyse __slots__ usage in *cls* relative to its bases.

        Parameters
        ----------
        cls : type
            The class to analyse.

        Returns
        -------
        SlotsAnalysisRecord
            Summary of slot declarations and any base-class conflicts.

        # copilot: slot conflicts occur when a slot name in *cls* shadows a
        slot in a base class; this is technically legal but creates two
        slot descriptors with the same name.
        """
        own_slots = getattr(cls, "__slots__", None)
        has_slots = own_slots is not None
        slot_names = tuple(own_slots) if has_slots else ()

        # Collect slots from base classes (excluding object)
        base_slot_names: set = set()
        for base in cls.__mro__[1:]:
            if base is object:
                continue
            bs = getattr(base, "__slots__", None)
            if bs:
                base_slot_names.update(bs)

        conflicts = tuple(n for n in slot_names if n in base_slot_names)
        return SlotsAnalysisRecord(
            class_name=cls.__qualname__,
            has_slots=has_slots,
            slot_names=slot_names,
            conflicts_with_bases=conflicts,
            slot_count=len(slot_names),
        )


# ===========================================================================
# Witness
# ===========================================================================

class DescriptorResolutionRoutesWitness:
    """Runtime witnessing helpers for descriptor resolution routes.

    Methods here perform actual attribute access on live objects and record
    which resolution route was taken.  They are designed to be called in
    controlled test environments rather than on arbitrary production objects.

    In the site-theoretic model, witnesses confirm that the *global section*
    (the resolved value) is consistent with the local sections (descriptors
    defined in MRO classes).

    # copilot: witness methods should not raise; use try/except and record
    MISSING resolution route on AttributeError.
    """

    def _classify_descriptor(self, obj: object) -> DescriptorKind:
        """Internal helper: classify a single object's descriptor kind."""
        t = type(obj)
        if isinstance(obj, staticmethod):
            return DescriptorKind.STATICMETHOD
        if isinstance(obj, classmethod):
            return DescriptorKind.CLASSMETHOD
        if isinstance(obj, property):
            return DescriptorKind.PROPERTY
        # Check for slot_descriptor (member_descriptor)
        if t.__name__ in ("member_descriptor", "getset_descriptor"):
            return DescriptorKind.SLOT
        has_get = "__get__" in t.__dict__
        has_set = "__set__" in t.__dict__
        has_del = "__delete__" in t.__dict__
        if has_get and (has_set or has_del):
            return DescriptorKind.DATA
        if has_get:
            return DescriptorKind.NON_DATA
        return DescriptorKind.NON_DATA  # plain value, not a descriptor

    def _find_in_mro(self, cls: type, name: str):
        """Return (found_class, obj) for the first MRO class that has name."""
        for klass in cls.__mro__:
            if name in klass.__dict__:
                return klass, klass.__dict__[name]
        return None, None

    def witness_getattr(self, obj: object, name: str) -> GetAttrWitnessRecord:
        """Observe and classify a getattr access on *obj*.

        Determines which of the six resolution routes was taken:
          1. DATA_DESCRIPTOR: data descriptor in MRO wins.
          2. INSTANCE_DICT:   instance __dict__ entry.
          3. NON_DATA_DESCRIPTOR: non-data descriptor in MRO.
          4. CLASS_ATTR:      plain class attribute (non-descriptor).
          5. MRO_FALLBACK:    found via object.__getattribute__ fallback.
          6. MISSING:         AttributeError.

        Parameters
        ----------
        obj    : object  — the object to access.
        name   : str     — the attribute name.

        Returns
        -------
        GetAttrWitnessRecord
            Witness record describing the resolution outcome.

        # copilot: the resolution logic mirrors CPython's type.__getattribute__
        but does not actually call __get__ — it classifies only.
        """
        cls = type(obj)
        obj_type_name = cls.__qualname__
        found_class, descriptor = self._find_in_mro(cls, name)

        descriptor_kind = None
        descriptor_invoked = False
        if descriptor is not None:
            descriptor_kind = self._classify_descriptor(descriptor)
            is_data = descriptor_kind in (
                DescriptorKind.DATA, DescriptorKind.PROPERTY, DescriptorKind.SLOT
            )
            if is_data:
                # Data descriptor wins over instance dict
                try:
                    val = getattr(obj, name)
                    value_type = type(val).__qualname__
                except AttributeError:
                    value_type = "MISSING"
                descriptor_invoked = True
                return GetAttrWitnessRecord(
                    obj_type=obj_type_name,
                    attr_name=name,
                    resolution_route=ResolutionRoute.DATA_DESCRIPTOR,
                    value_type=value_type,
                    descriptor_invoked=True,
                )

        # Check instance dict
        inst_dict = getattr(obj, "__dict__", {})
        if name in inst_dict:
            val = inst_dict[name]
            return GetAttrWitnessRecord(
                obj_type=obj_type_name,
                attr_name=name,
                resolution_route=ResolutionRoute.INSTANCE_DICT,
                value_type=type(val).__qualname__,
                descriptor_invoked=False,
            )

        if descriptor is not None and descriptor_kind == DescriptorKind.NON_DATA:
            try:
                val = getattr(obj, name)
                value_type = type(val).__qualname__
            except AttributeError:
                value_type = "MISSING"
            return GetAttrWitnessRecord(
                obj_type=obj_type_name,
                attr_name=name,
                resolution_route=ResolutionRoute.NON_DATA_DESCRIPTOR,
                value_type=value_type,
                descriptor_invoked=True,
            )

        if descriptor is not None:
            try:
                val = getattr(obj, name)
                value_type = type(val).__qualname__
            except AttributeError:
                value_type = "MISSING"
            return GetAttrWitnessRecord(
                obj_type=obj_type_name,
                attr_name=name,
                resolution_route=ResolutionRoute.CLASS_ATTR,
                value_type=value_type,
                descriptor_invoked=False,
            )

        # Missing
        return GetAttrWitnessRecord(
            obj_type=obj_type_name,
            attr_name=name,
            resolution_route=ResolutionRoute.MISSING,
            value_type="MISSING",
            descriptor_invoked=False,
        )

    def witness_setattr(self, obj: object, name: str, value: object) -> SetAttrWitnessRecord:
        """Observe and classify a setattr call on *obj*.

        Determines whether a data descriptor's __set__ is invoked or
        whether the value is stored in the instance __dict__.

        Parameters
        ----------
        obj   : object  — the target object.
        name  : str     — the attribute name.
        value : object  — the value to assign.

        Returns
        -------
        SetAttrWitnessRecord
            Witness record for the setattr operation.

        # copilot: actually performs the setattr; do not call this on
        immutable objects (e.g., frozen dataclasses, slots-only classes).
        """
        cls = type(obj)
        obj_type_name = cls.__qualname__
        found_class, descriptor = self._find_in_mro(cls, name)

        descriptor_invoked = False
        slot_set = False

        if descriptor is not None:
            kind = self._classify_descriptor(descriptor)
            if kind in (DescriptorKind.DATA, DescriptorKind.PROPERTY):
                descriptor_invoked = True
            elif kind == DescriptorKind.SLOT:
                slot_set = True
                descriptor_invoked = True

        try:
            setattr(obj, name, value)
        except (AttributeError, TypeError) as exc:
            log.debug("witness_setattr: %s on %r.%s", exc, obj, name)

        return SetAttrWitnessRecord(
            obj_type=obj_type_name,
            attr_name=name,
            value_type=type(value).__qualname__,
            descriptor_invoked=descriptor_invoked,
            slot_set=slot_set,
        )

    def probe_descriptor_protocol(self, descriptor: object) -> DescriptorProtocolProbe:
        """Probe a descriptor object for protocol completeness.

        Parameters
        ----------
        descriptor : object
            Any Python object that may implement the descriptor protocol.

        Returns
        -------
        DescriptorProtocolProbe
            Summary of which protocol methods are present.

        # copilot: get_signature uses inspect.signature on __get__ method of
        the descriptor TYPE (not the descriptor itself) to avoid triggering
        the protocol.
        """
        t = type(descriptor)
        has_get = "__get__" in t.__dict__
        has_set = "__set__" in t.__dict__
        has_del = "__delete__" in t.__dict__
        is_data = has_get and (has_set or has_del)

        get_sig = ""
        if has_get:
            try:
                get_sig = str(inspect.signature(t.__dict__["__get__"]))
            except (ValueError, TypeError):
                get_sig = "(...)"

        return DescriptorProtocolProbe(
            descriptor_class=t.__qualname__,
            has_get=has_get,
            has_set=has_set,
            has_delete=has_del,
            is_data_descriptor=is_data,
            get_signature=get_sig,
        )

    def build_witness_judgment(self, record: GetAttrWitnessRecord) -> Judgment:
        """Build a JuGeo Judgment from a GetAttrWitnessRecord.

        Parameters
        ----------
        record : GetAttrWitnessRecord

        Returns
        -------
        Judgment
            A PROPOSED judgment with RUNTIME_WITNESSED trust.

        # copilot: the judgment is settled only if the resolution route
        is not MISSING.
        """
        is_found = record.resolution_route != ResolutionRoute.MISSING
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            statement=(
                f"Attribute '{record.attr_name}' on '{record.obj_type}' "
                f"resolves via {record.resolution_route.value}"
            ),
            label=f"getattr_witness:{record.obj_type}.{record.attr_name}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(record.obj_type, record.attr_name),
                kind=CoordinateKind.INTERFACE,
            ),
            payload=record,
            label=f"{record.obj_type}.{record.attr_name}",
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_TRACE,
            payload=record,
            label="getattr_route",
        ))
        j = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED, rationale="getattr witness"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"drrw:{record.obj_type}.{record.attr_name}",
        )
        if is_found:
            j.settle()
        return j


# ===========================================================================
# Coordinator
# ===========================================================================

class DescriptorResolutionRoutesCoordinator:
    """Orchestrator for descriptor resolution routes analysis.

    Combines static analysis and runtime witnessing to build comprehensive
    resolution records and JuGeo judgments.

    In the site-theoretic model, the Coordinator computes the *global
    section* of the descriptor sheaf: given the covering family (MRO), it
    resolves conflicts and produces a canonical section.

    # copilot: this class is stateful — it caches analysis results per class.
    Use a fresh instance per module boundary to avoid cross-contamination.
    """

    def __init__(self) -> None:
        self._analyzer = DescriptorResolutionRoutesAnalyzer()
        self._witness = DescriptorResolutionRoutesWitness()
        self._cache: dict = {}

    def resolve_attribute(self, obj: object, name: str) -> DescriptorResolutionResult:
        """Resolve attribute *name* on *obj* and return a classification record.

        Parameters
        ----------
        obj  : object  — the object to resolve against.
        name : str     — the attribute name.

        Returns
        -------
        DescriptorResolutionResult
            Classification of how the attribute would be resolved.

        # copilot: calls witness_getattr internally; does NOT return the
        resolved value to keep the result serialisable.
        """
        witness_rec = self._witness.witness_getattr(obj, name)
        cls = type(obj)
        mro_trace = self.trace_mro_lookup(cls, name)
        descriptor_class = ""
        desc_kind = DescriptorKind.NON_DATA
        if mro_trace.found_at_index >= 0:
            found_cls = cls.__mro__[mro_trace.found_at_index]
            raw = found_cls.__dict__.get(name)
            if raw is not None:
                descriptor_class = type(raw).__qualname__
                desc_kind = self._witness._classify_descriptor(raw)

        return DescriptorResolutionResult(
            obj_type=type(obj).__qualname__,
            attr_name=name,
            resolution_route=witness_rec.resolution_route,
            descriptor_class=descriptor_class,
            descriptor_kind=desc_kind,
            found_in_class=mro_trace.found_in_class,
        )

    def classify_descriptor(self, descriptor: object) -> DescriptorKind:
        """Classify a descriptor object.

        Parameters
        ----------
        descriptor : object
            Any Python object.

        Returns
        -------
        DescriptorKind
            The descriptor classification.

        # copilot: delegates to witness._classify_descriptor for consistency.
        """
        return self._witness._classify_descriptor(descriptor)

    def trace_mro_lookup(self, cls: type, name: str) -> MROLookupTrace:
        """Trace an MRO-order search for attribute *name* in *cls*.

        Parameters
        ----------
        cls  : type  — the starting class.
        name : str   — the attribute name to search for.

        Returns
        -------
        MROLookupTrace
            Search trace including the MRO chain and where the attr was found.

        # copilot: found_at_index is -1 if the attribute is not found in any
        MRO class.
        """
        mro = getattr(cls, "__mro__", (cls,))
        mro_chain = tuple(c.__qualname__ for c in mro)
        found_at = -1
        found_in = ""
        found_kind = None

        for i, klass in enumerate(mro):
            if name in klass.__dict__:
                found_at = i
                found_in = klass.__qualname__
                found_kind = self._witness._classify_descriptor(klass.__dict__[name])
                break

        return MROLookupTrace(
            cls_name=cls.__qualname__,
            attr_name=name,
            mro_chain=mro_chain,
            found_at_index=found_at,
            found_in_class=found_in,
            descriptor_kind=found_kind,
        )

    def build_resolution_morphism(self, trace: MROLookupTrace) -> object:
        """Build a Morphism representing the MRO lookup trace.

        Parameters
        ----------
        trace : MROLookupTrace
            The lookup trace to convert.

        Returns
        -------
        Morphism
            Source is the queried class coordinate; target is the found-in-class
            coordinate.  Kind is RESTRICTION (narrowing from class to MRO entry).

        # copilot: if found_at_index == -1 the target coordinate is the
        'missing' sentinel coordinate.
        """
        source = Coordinate(
            components=(trace.cls_name, trace.attr_name),
            kind=CoordinateKind.INTERFACE,
        )
        target_name = trace.found_in_class if trace.found_in_class else "missing"
        target = Coordinate(
            components=(target_name, trace.attr_name),
            kind=CoordinateKind.INTERFACE,
        )
        return Morphism(
            source=source,
            target=target,
            kind=MorphismKind.RESTRICTION,
            label=f"mro_lookup:{trace.cls_name}.{trace.attr_name}",
        )

    def build_resolution_judgment(self, result: DescriptorResolutionResult) -> Judgment:
        """Build a JuGeo Judgment for a DescriptorResolutionResult.

        Parameters
        ----------
        result : DescriptorResolutionResult

        Returns
        -------
        Judgment
            A PROPOSED judgment with COPILOT_SUGGESTED trust (static).

        # copilot: settle the judgment if the route is not MISSING.
        """
        is_found = result.resolution_route != ResolutionRoute.MISSING
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Attribute '{result.attr_name}' on '{result.obj_type}' "
                f"resolves via {result.resolution_route.value} "
                f"(found in: {result.found_in_class or 'nowhere'})"
            ),
            label=f"desc_resolution:{result.obj_type}.{result.attr_name}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(result.obj_type, result.attr_name),
                kind=CoordinateKind.INTERFACE,
            ),
            payload=result,
            label=f"{result.obj_type}.{result.attr_name}",
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload=result,
            label="resolution_result",
        ))
        j = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.COPILOT_SUGGESTED, rationale="MRO analysis"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"drr:{result.obj_type}.{result.attr_name}",
        )
        if is_found:
            j.settle()
        return j

    def analyze_class_descriptors(self, cls: type) -> list:
        """Produce DescriptorAnalysisRecord for every descriptor in *cls*.

        Parameters
        ----------
        cls : type
            The class to analyse.

        Returns
        -------
        list[DescriptorAnalysisRecord]
            One record per descriptor found (including inherited ones).

        # copilot: priority 0 = highest (data descriptor); 2 = lowest
        (non-data descriptor/plain attribute).
        """
        records: list = []
        seen: set = set()
        for mro_idx, klass in enumerate(cls.__mro__):
            for name, obj in klass.__dict__.items():
                if name in seen:
                    continue
                seen.add(name)
                t = type(obj)
                has_get = "__get__" in t.__dict__
                if not has_get:
                    continue
                kind = self._witness._classify_descriptor(obj)
                priority = 0 if kind in (DescriptorKind.DATA, DescriptorKind.PROPERTY, DescriptorKind.SLOT) else 2
                records.append(DescriptorAnalysisRecord(
                    class_name=cls.__qualname__,
                    attr_name=name,
                    descriptor_class=t.__qualname__,
                    kind=kind,
                    priority=priority,
                    is_inherited=(klass is not cls),
                ))
        return sorted(records, key=lambda r: (r.priority, r.attr_name))


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log.info("=== descriptor_resolution_routes smoke test ===")

    class _DataDesc:
        """A simple data descriptor."""
        def __get__(self, obj, objtype=None):
            return 42
        def __set__(self, obj, value):
            pass

    class _NonDataDesc:
        """A simple non-data descriptor."""
        def __get__(self, obj, objtype=None):
            return "hello"

    class _Base:
        dd = _DataDesc()
        nd = _NonDataDesc()

        @property
        def prop(self):
            return "prop_value"

        def method(self): ...

    class _Child(_Base):
        pass

    coord = DescriptorResolutionRoutesCoordinator()
    analyzer = DescriptorResolutionRoutesAnalyzer()
    witness = DescriptorResolutionRoutesWitness()

    # Data descriptor analysis
    data_descs = analyzer.find_data_descriptors(_Child)
    non_data_descs = analyzer.find_non_data_descriptors(_Child)
    print(f"Data descriptors on _Child: {data_descs}")
    print(f"Non-data descriptors on _Child: {non_data_descs}")

    # Slots
    class _Slotted:
        __slots__ = ("x", "y")

    slots_rec = analyzer.analyze_slots_vs_dict(_Slotted)
    print(f"Slots: {slots_rec}")

    # MRO lookup
    obj = _Child()
    trace = coord.trace_mro_lookup(_Child, "dd")
    print(f"MRO trace for 'dd': found_at={trace.found_at_index}, found_in={trace.found_in_class}")

    # Witness
    wr = witness.witness_getattr(obj, "dd")
    print(f"Getattr witness: route={wr.resolution_route}, type={wr.value_type}")

    # Judgment
    result = coord.resolve_attribute(obj, "method")
    j = coord.build_resolution_judgment(result)
    print(f"Judgment: {j.label}, status={j.status}")

    # Property usage (AST)
    _SRC = """
class MyClass:
    @property
    def name(self): return self._name
    @name.setter
    def name(self, v): self._name = v
    @property
    def age(self): return self._age
"""
    tree = ast.parse(_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            props = analyzer.classify_property_usage(node)
            for p in props:
                print(f"  Property: {p.attr_name}, setter={p.has_setter}")

    # Descriptor probe
    probe = witness.probe_descriptor_protocol(_DataDesc())
    print(f"Probe: is_data={probe.is_data_descriptor}, sig={probe.get_signature}")

    log.info("=== smoke test complete ===")
