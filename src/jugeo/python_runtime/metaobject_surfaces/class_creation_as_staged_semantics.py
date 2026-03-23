"""
class_creation_as_staged_semantics.py
==========================================
theory2.tex — Chapter 20 §1: "Class creation as staged semantics"

Python class creation is a three-phase protocol:
  1. __prepare__  — metaclass produces a (possibly ordered) namespace dict.
  2. Body execution — the class body is executed, populating the namespace.
  3. __new__       — the metaclass constructs the final class object from
                     (name, bases, namespace).

In JuGeo's site-theoretic model each phase is a *morphism* in the site
category.  The prepare namespace is a coordinate object; the body execution
is a restriction morphism from the enclosing module scope into that
coordinate; and __new__ is the transport morphism that produces the final
class-level coordinate.

This module implements:
  • ClassCreationStagedSemanticsCoordinator — orchestrates staged analysis.
  • ClassCreationStagedSemanticsAnalyzer    — static / AST analysis.
  • ClassCreationStagedSemanticsWitness     — runtime witnessing helpers.

Together they emit Judgment objects that can be registered with the JuGeo
judgment registry for downstream sheaf-theoretic consistency checks.

Cross-references:
  theory2.tex §20.1 (staged semantics), §20.3 (metaclass as transformer),
  §14.2 (site morphisms), §18.5 (judgment coordinates).
"""
from __future__ import annotations

import ast
import inspect
import logging
import time
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

class ClassKind(str, Enum):
    """Coarse classification of a Python class by its structural origin.

    # copilot: used as a first-pass discriminant before detailed surface analysis.
    PLAIN        — ordinary class with no framework magic.
    DATACLASS    — decorated with @dataclass.
    ABC          — inherits from abc.ABC or has ABCMeta.
    PROTOCOL     — typing.Protocol subclass.
    ENUM         — inherits from enum.Enum or similar.
    NAMED_TUPLE  — collections.namedtuple / typing.NamedTuple.
    TYPED_DICT   — typing.TypedDict.
    """
    PLAIN = "plain"
    DATACLASS = "dataclass"
    ABC = "abc"
    PROTOCOL = "protocol"
    ENUM = "enum"
    NAMED_TUPLE = "named_tuple"
    TYPED_DICT = "typed_dict"


class MetaclassUsageKind(str, Enum):
    """How a class specifies (or inherits) its metaclass.

    # copilot: distinguishes syntactic metaclass=... from implicit inheritance.
    NONE              — no metaclass at all (pure object).
    TYPE_DEFAULT      — implicitly uses builtins.type.
    CUSTOM_METACLASS  — explicit metaclass= keyword argument.
    ABC_META          — inherits ABCMeta through abc.ABC.
    ENUM_META         — inherits EnumMeta through an Enum base.
    """
    NONE = "none"
    TYPE_DEFAULT = "type_default"
    CUSTOM_METACLASS = "custom_metaclass"
    ABC_META = "abc_meta"
    ENUM_META = "enum_meta"


# ===========================================================================
# Value-object dataclasses (frozen, slots)
# ===========================================================================

@dataclass(frozen=True, slots=True)
class ClassCreationRecord:
    """Immutable record of a single class definition discovered during analysis.

    # copilot: produced by ClassCreationStagedSemanticsAnalyzer.extract_class_defs
    and enriched by the Coordinator.  Serves as the primary carrier for
    class-creation judgments in the JuGeo judgment system.

    Attributes
    ----------
    class_name        : simple name of the class.
    bases             : tuple of base-class names (strings).
    metaclass_name    : string name of the metaclass, or '' if default.
    phase_count       : how many of the three phases were customised (0-3).
    has_prepare       : True iff metaclass overrides __prepare__.
    has_init_subclass : True iff the class defines __init_subclass__.
    has_set_name      : True iff any descriptor in the body defines __set_name__.
    line_no           : source line number of the class statement, or -1.
    """
    class_name: str
    bases: tuple
    metaclass_name: str
    phase_count: int
    has_prepare: bool
    has_init_subclass: bool
    has_set_name: bool
    line_no: int


@dataclass(frozen=True, slots=True)
class ThreePhaseTrace:
    """Snapshot of all three class-creation phases for a single class.

    # copilot: used to build phase morphisms; each field corresponds to one
    of the three morphisms in the site diagram for staged semantics.

    Attributes
    ----------
    class_name           : name of the class being traced.
    prepare_result_keys  : keys present in the namespace after __prepare__.
    body_names           : names defined during body execution.
    created_at           : monotonic timestamp (ns) when __new__ completed.
    metaclass_used       : string name of the actual metaclass used.
    """
    class_name: str
    prepare_result_keys: tuple
    body_names: tuple
    created_at: int
    metaclass_used: str


@dataclass(frozen=True, slots=True)
class MetaclassRef:
    """Reference to a metaclass, including structural probe results.

    # copilot: distinguishes syntactic reference (from AST) from runtime probe.

    Attributes
    ----------
    metaclass_name    : qualified name of the metaclass.
    metaclass_module  : module where the metaclass is defined.
    is_explicit       : True iff the metaclass= keyword was present in source.
    has_custom_new    : True iff the metaclass defines __new__ beyond type.__new__.
    has_custom_prepare: True iff the metaclass defines __prepare__.
    """
    metaclass_name: str
    metaclass_module: str
    is_explicit: bool
    has_custom_new: bool
    has_custom_prepare: bool


@dataclass(frozen=True, slots=True)
class DescriptorRef:
    """Reference to a descriptor attribute discovered inside a class body.

    # copilot: lightweight proxy used in the ClassCreationRecord before full
    descriptor-resolution analysis (see descriptor_resolution_routes.py).

    Attributes
    ----------
    name             : attribute name.
    descriptor_class : class name of the descriptor object.
    has_get          : True iff descriptor defines __get__.
    has_set          : True iff descriptor defines __set__.
    has_delete       : True iff descriptor defines __delete__.
    line_no          : source line number of the assignment, or -1.
    """
    name: str
    descriptor_class: str
    has_get: bool
    has_set: bool
    has_delete: bool
    line_no: int


@dataclass(frozen=True, slots=True)
class ClassCreationWitnessRecord:
    """Runtime witness record for a live class object.

    # copilot: produced by ClassCreationStagedSemanticsWitness.witness_class_creation.
    Contains only serialisable primitives so it can be stored in a
    judgment carrier without holding live object references.

    Attributes
    ----------
    class_name       : __qualname__ of the witnessed class.
    mro_chain        : tuple of __qualname__ strings along the MRO.
    descriptor_names : tuple of attribute names that are descriptors.
    dunder_attrs     : tuple of dunder attribute names defined directly.
    is_abstract      : True iff the class has abstract methods.
    creation_time_ns : monotonic time (ns) at which witnessing occurred.
    """
    class_name: str
    mro_chain: tuple
    descriptor_names: tuple
    dunder_attrs: tuple
    is_abstract: bool
    creation_time_ns: int


@dataclass(frozen=True, slots=True)
class SetNameCallRecord:
    """Record of a single __set_name__ call during class creation.

    # copilot: __set_name__ is called by type.__new__ for every descriptor
    in the namespace; this record captures one such call event.

    Attributes
    ----------
    descriptor_name : attribute name under which the descriptor is stored.
    owner_class     : __qualname__ of the owning class.
    attr_name       : name passed as second arg to __set_name__.
    call_order      : ordinal position among all __set_name__ calls (0-based).
    """
    descriptor_name: str
    owner_class: str
    attr_name: str
    call_order: int


# ===========================================================================
# Analyzer
# ===========================================================================

class ClassCreationStagedSemanticsAnalyzer:
    """Static / AST-based analysis tools for class-creation staged semantics.

    This class provides pure functions over source text and AST nodes.
    No live class objects are required; all inputs are either source strings
    or ast.ClassDef nodes.

    In the site-theoretic interpretation, this analyzer computes the
    *pre-image* of the three-phase protocol: it infers which morphisms will
    be activated before any code is executed.

    # copilot: keep all methods stateless; no instance variables are mutated.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_class_defs(self, source: str) -> list:
        """Parse *source* and return all ast.ClassDef nodes.

        Performs a full-depth AST walk so nested class definitions are
        included.  The returned list is in document order (DFS pre-order).

        Parameters
        ----------
        source : str
            Valid Python source text.

        Returns
        -------
        list[ast.ClassDef]
            All ClassDef nodes found in the AST.

        # copilot: does not filter by nesting depth; caller is responsible for
        scoping if only top-level classes are wanted.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            log.warning("extract_class_defs: SyntaxError in source: %s", exc)
            return []
        nodes: list = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                nodes.append(node)
        log.debug("extract_class_defs: found %d class definitions", len(nodes))
        return nodes

    def classify_metaclass_usage(self, node: ast.ClassDef) -> MetaclassUsageKind:
        """Classify how the metaclass is specified in *node*.

        Looks for:
          - ``metaclass=...`` keyword in the class statement.
          - ABC / ABCMeta in the base list.
          - Enum / EnumMeta in the base list.

        Parameters
        ----------
        node : ast.ClassDef
            The class definition node.

        Returns
        -------
        MetaclassUsageKind
            The detected metaclass usage pattern.

        # copilot: priority order is CUSTOM_METACLASS > ABC_META > ENUM_META >
        TYPE_DEFAULT.  NONE is reserved for the pathological case where
        there is literally no type ancestry (not reachable in normal Python).
        """
        for kw in node.keywords:
            if kw.arg == "metaclass":
                # Inspect the keyword value for known metaclasses
                val = kw.value
                if isinstance(val, ast.Attribute):
                    name = val.attr
                elif isinstance(val, ast.Name):
                    name = val.id
                else:
                    name = ""
                if "ABCMeta" in name:
                    return MetaclassUsageKind.ABC_META
                if "EnumMeta" in name:
                    return MetaclassUsageKind.ENUM_META
                return MetaclassUsageKind.CUSTOM_METACLASS

        # Check base names
        for base in node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name in ("ABC", "ABCMeta"):
                return MetaclassUsageKind.ABC_META
            if base_name in ("Enum", "IntEnum", "Flag", "IntFlag", "StrEnum"):
                return MetaclassUsageKind.ENUM_META

        return MetaclassUsageKind.TYPE_DEFAULT

    def detect_phase_hooks(self, cls_namespace: dict) -> list:
        """Identify protocol hooks present in *cls_namespace*.

        Checks for the names ``__prepare__``, ``__init_subclass__``,
        ``__set_name__``, ``__class_getitem__``, and ``__init_subclass__``.

        Parameters
        ----------
        cls_namespace : dict
            The class body namespace (e.g. ``cls.__dict__``).

        Returns
        -------
        list[str]
            Sorted list of hook names present in the namespace.

        # copilot: the returned list is used to populate phase_count in
        ClassCreationRecord.
        """
        _HOOKS = frozenset({
            "__prepare__",
            "__init_subclass__",
            "__set_name__",
            "__class_getitem__",
            "__init__",
            "__new__",
        })
        found = [name for name in _HOOKS if name in cls_namespace]
        return sorted(found)

    def analyze_mro(self, cls: type) -> list:
        """Return the MRO chain as a list of qualified-name strings.

        Parameters
        ----------
        cls : type
            A live Python class.

        Returns
        -------
        list[str]
            Qualified names along the MRO, in MRO order (most-derived first).

        # copilot: uses cls.__mro__ rather than inspect.getmro to avoid
        triggering descriptors or metaclass hooks on exotic classes.
        """
        try:
            return [c.__qualname__ for c in cls.__mro__]
        except AttributeError:
            log.warning("analyze_mro: %r has no __mro__", cls)
            return [getattr(cls, "__qualname__", repr(cls))]

    def detect_descriptor_definitions(self, node: ast.ClassDef) -> list:
        """Find attribute assignments in *node* whose RHS looks like a descriptor.

        A heuristic: the RHS is a function call whose callee name ends with
        one of a known set of descriptor-creating names, or the assignment
        uses the ``property(...)`` builtin.

        Parameters
        ----------
        node : ast.ClassDef
            Class definition AST node.

        Returns
        -------
        list[DescriptorRef]
            One DescriptorRef per detected descriptor assignment.

        # copilot: false-negative-tolerant; errs on the side of not reporting
        a DescriptorRef if the pattern is ambiguous.
        """
        _DESCRIPTOR_CREATORS = frozenset({
            "property", "classmethod", "staticmethod",
            "field", "Field", "descriptor",
        })
        refs: list = []
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Assign):
                continue
            if not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            callee_name = ""
            if isinstance(call.func, ast.Name):
                callee_name = call.func.id
            elif isinstance(call.func, ast.Attribute):
                callee_name = call.func.attr
            if callee_name not in _DESCRIPTOR_CREATORS:
                continue
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    refs.append(DescriptorRef(
                        name=target.id,
                        descriptor_class=callee_name,
                        has_get=(callee_name == "property"),
                        has_set=False,
                        has_delete=False,
                        line_no=stmt.lineno,
                    ))
        return refs

    def classify_class_kind(self, node: ast.ClassDef) -> ClassKind:
        """Classify the structural kind of the class from its AST.

        Checks decorators and base classes in order of precedence:
        DATACLASS > PROTOCOL > ENUM > ABC > NAMED_TUPLE > TYPED_DICT > PLAIN.

        Parameters
        ----------
        node : ast.ClassDef
            Class definition AST node.

        Returns
        -------
        ClassKind
            The coarsest matching structural category.

        # copilot: decorator inspection looks only at the last component of
        dotted names (e.g. ``dataclasses.dataclass`` → "dataclass").
        """
        decorator_names: list = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorator_names.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorator_names.append(dec.attr)
            elif isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Name):
                    decorator_names.append(func.id)
                elif isinstance(func, ast.Attribute):
                    decorator_names.append(func.attr)

        if "dataclass" in decorator_names:
            return ClassKind.DATACLASS

        base_names: list = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_names.append(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.append(base.attr)

        if "Protocol" in base_names:
            return ClassKind.PROTOCOL
        if any(n in base_names for n in ("Enum", "IntEnum", "Flag", "IntFlag", "StrEnum")):
            return ClassKind.ENUM
        if any(n in base_names for n in ("ABC", "ABCMeta")):
            return ClassKind.ABC
        if any(n in base_names for n in ("NamedTuple",)):
            return ClassKind.NAMED_TUPLE
        if any(n in base_names for n in ("TypedDict",)):
            return ClassKind.TYPED_DICT
        return ClassKind.PLAIN


# ===========================================================================
# Witness
# ===========================================================================

class ClassCreationStagedSemanticsWitness:
    """Runtime witnessing helpers for class-creation staged semantics.

    All methods in this class operate on live Python type objects.  They
    produce serialisable records that can be stored in JuGeo judgment
    carriers without holding live references.

    In the site-theoretic model, witnesses provide the *empirical* side of
    the judgment: they confirm (or refute) claims made by the static analyzer
    by observing the actual behaviour of the Python runtime.

    # copilot: be careful with __prepare__ probing — calling it can have
    side effects in exotic metaclasses.  The probe_prepare_namespace method
    uses a try/except guard.
    """

    def witness_class_creation(self, cls: type) -> ClassCreationWitnessRecord:
        """Produce a witness record for a live class.

        Introspects the class using inspect and cls.__dict__ to build a
        snapshot suitable for inclusion in a judgment carrier.

        Parameters
        ----------
        cls : type
            Any Python class object.

        Returns
        -------
        ClassCreationWitnessRecord
            Serialisable record describing the class.

        # copilot: is_abstract is True iff getattr(cls, '__abstractmethods__', frozenset())
        is non-empty — this correctly handles ABCMeta and custom metaclasses.
        """
        mro_chain = tuple(c.__qualname__ for c in getattr(cls, "__mro__", (cls,)))
        descriptor_names: list = []
        for name, obj in cls.__dict__.items():
            if hasattr(obj, "__get__"):
                descriptor_names.append(name)
        dunder_attrs = tuple(
            n for n in cls.__dict__ if n.startswith("__") and n.endswith("__")
        )
        is_abstract = bool(getattr(cls, "__abstractmethods__", frozenset()))
        return ClassCreationWitnessRecord(
            class_name=cls.__qualname__,
            mro_chain=mro_chain,
            descriptor_names=tuple(sorted(descriptor_names)),
            dunder_attrs=tuple(sorted(dunder_attrs)),
            is_abstract=is_abstract,
            creation_time_ns=time.monotonic_ns(),
        )

    def probe_prepare_namespace(self, metaclass: type, name: str, bases: tuple) -> dict:
        """Call ``metaclass.__prepare__`` and return the resulting namespace.

        If the metaclass does not define __prepare__, or if calling it raises
        an exception, returns an empty dict.

        Parameters
        ----------
        metaclass : type
            The metaclass to probe.
        name : str
            Class name argument to __prepare__.
        bases : tuple
            Bases tuple argument to __prepare__.

        Returns
        -------
        dict
            The namespace produced by __prepare__, or {} on failure.

        # copilot: always wrap in try/except — __prepare__ on custom metaclasses
        may require specific keyword arguments or perform side effects.
        """
        prepare = getattr(metaclass, "__prepare__", None)
        if prepare is None:
            return {}
        try:
            ns = prepare(name, bases)
            return dict(ns) if ns is not None else {}
        except Exception as exc:  # noqa: BLE001
            log.debug("probe_prepare_namespace: %r raised %s", metaclass, exc)
            return {}

    def record_set_name_calls(self, cls: type) -> list:
        """Reconstruct the set of __set_name__ calls that occurred during class creation.

        Since __set_name__ is called by ``type.__new__``, we can only *infer*
        which descriptors had __set_name__ called by inspecting which
        descriptors currently in cls.__dict__ define __set_name__.

        Parameters
        ----------
        cls : type
            The class to inspect.

        Returns
        -------
        list[SetNameCallRecord]
            One record per descriptor that defines __set_name__, in dict
            insertion order.

        # copilot: we cannot actually *observe* past __set_name__ calls
        without instrumentation; this method reconstructs the expected calls
        from the current class dict state.
        """
        records: list = []
        order = 0
        for attr_name, obj in cls.__dict__.items():
            if callable(getattr(type(obj), "__set_name__", None)):
                records.append(SetNameCallRecord(
                    descriptor_name=type(obj).__qualname__,
                    owner_class=cls.__qualname__,
                    attr_name=attr_name,
                    call_order=order,
                ))
                order += 1
        return records

    def build_witness_judgment(self, record: ClassCreationWitnessRecord) -> Judgment:
        """Construct a JuGeo Judgment from a ClassCreationWitnessRecord.

        The judgment proposition states that the class identified by
        *record.class_name* satisfies the three-phase creation protocol.
        Trust level is RUNTIME_WITNESSED.

        Parameters
        ----------
        record : ClassCreationWitnessRecord
            The witness record to convert.

        Returns
        -------
        Judgment
            A PROPOSED judgment (caller should call .settle() if correct).

        # copilot: the judgment is left in PROPOSED state; the Coordinator
        is responsible for settling or obstructing it based on downstream
        checks.
        """
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=f"Class '{record.class_name}' satisfies three-phase creation protocol",
            label=f"class_creation_witness:{record.class_name}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(record.class_name,),
                kind=CoordinateKind.MODULE,
            ),
            payload=record,
            label=record.class_name,
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_TRACE,
            payload=record,
            label="creation_witness",
        ))
        return Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED, rationale="witnessed by probe"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"ccss_witness:{record.class_name}",
        )


# ===========================================================================
# Coordinator
# ===========================================================================

class ClassCreationStagedSemanticsCoordinator:
    """Orchestrator for class-creation staged-semantics analysis.

    The Coordinator combines static analysis (via the Analyzer) and runtime
    witnessing (via the Witness) to produce ClassCreationRecord, ThreePhaseTrace,
    and Judgment objects.

    In the site-theoretic model, the Coordinator acts as a *functor* from
    the category of Python source modules to the category of JuGeo judgment
    coordinates.

    # copilot: this class is stateful; it caches records to avoid re-parsing
    the same source multiple times.  Thread safety is not guaranteed.
    """

    def __init__(self) -> None:
        self._analyzer = ClassCreationStagedSemanticsAnalyzer()
        self._witness = ClassCreationStagedSemanticsWitness()
        # copilot: simple list cache; replace with an LRU cache if perf matters.
        self._record_cache: list = []

    # ------------------------------------------------------------------
    # Core protocol methods
    # ------------------------------------------------------------------

    def analyze_class_definition(self, node: ast.ClassDef) -> ClassCreationRecord:
        """Produce a ClassCreationRecord from an ast.ClassDef node.

        Parameters
        ----------
        node : ast.ClassDef
            Parsed class definition node.

        Returns
        -------
        ClassCreationRecord
            Immutable record describing the class definition.

        # copilot: does not require a live class; purely static.
        """
        metaclass_name = ""
        for kw in node.keywords:
            if kw.arg == "metaclass":
                val = kw.value
                if isinstance(val, ast.Name):
                    metaclass_name = val.id
                elif isinstance(val, ast.Attribute):
                    metaclass_name = f"{ast.unparse(val)}"
                break

        bases = tuple(ast.unparse(b) for b in node.bases if not (
            isinstance(b, ast.keyword)
        ))

        body_names = [
            stmt.targets[0].id if isinstance(stmt, ast.Assign) and
            isinstance(stmt.targets[0], ast.Name) else
            stmt.name if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else
            ""
            for stmt in node.body
        ]

        has_prepare = any(
            isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__prepare__"
            for s in node.body
        )
        has_init_subclass = any(
            isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__init_subclass__"
            for s in node.body
        )
        has_set_name_in_body = any(
            isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__set_name__"
            for s in node.body
        )

        phase_count = sum([has_prepare, has_init_subclass, has_set_name_in_body])

        record = ClassCreationRecord(
            class_name=node.name,
            bases=bases,
            metaclass_name=metaclass_name,
            phase_count=phase_count,
            has_prepare=has_prepare,
            has_init_subclass=has_init_subclass,
            has_set_name=has_set_name_in_body,
            line_no=node.lineno,
        )
        self._record_cache.append(record)
        log.debug("analyze_class_definition: produced record for '%s'", node.name)
        return record

    def trace_three_phases(self, cls: type) -> ThreePhaseTrace:
        """Trace the three creation phases for a live class *cls*.

        Phase 1: __prepare__ namespace keys (probed via witness).
        Phase 2: body names from cls.__dict__.
        Phase 3: recorded at probe time via monotonic clock.

        Parameters
        ----------
        cls : type
            A live Python class.

        Returns
        -------
        ThreePhaseTrace
            Snapshot of all three phases.

        # copilot: prepare_result_keys may be empty if the metaclass does not
        define __prepare__ or if probing raises an exception.
        """
        metaclass = type(cls)
        prepare_ns = self._witness.probe_prepare_namespace(metaclass, cls.__name__, cls.__bases__)
        prepare_keys = tuple(sorted(prepare_ns.keys()))
        body_names = tuple(sorted(cls.__dict__.keys()))
        return ThreePhaseTrace(
            class_name=cls.__qualname__,
            prepare_result_keys=prepare_keys,
            body_names=body_names,
            created_at=time.monotonic_ns(),
            metaclass_used=type(cls).__qualname__,
        )

    def identify_metaclass(self, node: ast.ClassDef, module_globals: dict) -> MetaclassRef:
        """Identify the metaclass referenced in *node*.

        Looks up the actual metaclass object in *module_globals* if possible,
        then probes it for __new__ and __prepare__ overrides.

        Parameters
        ----------
        node : ast.ClassDef
            Class definition AST node.
        module_globals : dict
            The module-level globals dict (used to resolve metaclass names).

        Returns
        -------
        MetaclassRef
            Reference record for the resolved metaclass.

        # copilot: falls back to 'type' if the metaclass name cannot be
        resolved in module_globals.
        """
        metaclass_name = ""
        is_explicit = False
        for kw in node.keywords:
            if kw.arg == "metaclass":
                is_explicit = True
                val = kw.value
                if isinstance(val, ast.Name):
                    metaclass_name = val.id
                elif isinstance(val, ast.Attribute):
                    metaclass_name = ast.unparse(val)
                break

        if not metaclass_name:
            metaclass_name = "type"

        live_meta = module_globals.get(metaclass_name, type)
        if not isinstance(live_meta, type):
            live_meta = type

        has_custom_new = (
            "__new__" in live_meta.__dict__ and live_meta.__dict__["__new__"] is not type.__new__
        )
        has_custom_prepare = "__prepare__" in live_meta.__dict__

        return MetaclassRef(
            metaclass_name=metaclass_name,
            metaclass_module=getattr(live_meta, "__module__", ""),
            is_explicit=is_explicit,
            has_custom_new=has_custom_new,
            has_custom_prepare=has_custom_prepare,
        )

    def build_phase_morphisms(self, trace: ThreePhaseTrace) -> list:
        """Build a list of Morphism objects representing the three phases.

        Returns a list of three Morphism objects:
          [0] prepare_morphism   — module coord → prepare-namespace coord
          [1] body_morphism      — prepare-namespace coord → body-namespace coord
          [2] new_morphism       — body-namespace coord → class-object coord

        Parameters
        ----------
        trace : ThreePhaseTrace
            The three-phase trace for a class.

        Returns
        -------
        list[Morphism]
            Three morphisms in creation order.

        # copilot: morphism labels encode the phase name and class name for
        traceability in the judgment graph.
        """
        module_coord = Coordinate(
            components=(trace.class_name, "module"),
            kind=CoordinateKind.MODULE,
        )
        prepare_coord = Coordinate(
            components=(trace.class_name, "prepare"),
            kind=CoordinateKind.REGION,
        )
        body_coord = Coordinate(
            components=(trace.class_name, "body"),
            kind=CoordinateKind.REGION,
        )
        class_coord = Coordinate(
            components=(trace.class_name, "class_object"),
            kind=CoordinateKind.INTERFACE,
        )
        return [
            Morphism(source=module_coord, target=prepare_coord,
                     kind=MorphismKind.RESTRICTION, label=f"prepare:{trace.class_name}"),
            Morphism(source=prepare_coord, target=body_coord,
                     kind=MorphismKind.INCLUSION, label=f"body:{trace.class_name}"),
            Morphism(source=body_coord, target=class_coord,
                     kind=MorphismKind.TRANSPORT, label=f"new:{trace.class_name}"),
        ]

    def build_creation_judgment(self, record: ClassCreationRecord) -> Judgment:
        """Build a JuGeo Judgment for a ClassCreationRecord.

        The judgment proposition states that the class definition with the
        given name and phase_count is structurally well-formed.

        Parameters
        ----------
        record : ClassCreationRecord
            The class creation record to convert.

        Returns
        -------
        Judgment
            A PROPOSED judgment backed by static analysis evidence.

        # copilot: trust level is COPILOT_SUGGESTED because this is derived
        purely from static analysis; runtime witnessing upgrades trust.
        """
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Class '{record.class_name}' uses {record.phase_count} custom "
                f"creation-phase hooks (metaclass={record.metaclass_name or 'type'})"
            ),
            label=f"class_creation:{record.class_name}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(record.class_name,),
                kind=CoordinateKind.MODULE,
            ),
            payload=record,
            label=record.class_name,
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload=record,
            label="static_record",
        ))
        j = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.COPILOT_SUGGESTED, rationale="AST analysis"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"ccss:{record.class_name}",
        )
        return j

    def run_on_source(self, source: str) -> list:
        """Parse *source* and return ClassCreationRecord for every class defined.

        Convenience method that wraps extract_class_defs and
        analyze_class_definition.

        Parameters
        ----------
        source : str
            Valid Python source text.

        Returns
        -------
        list[ClassCreationRecord]
            One record per class definition in the source.

        # copilot: nested classes are included; the record's line_no
        distinguishes them if needed.
        """
        nodes = self._analyzer.extract_class_defs(source)
        records: list = []
        for node in nodes:
            try:
                records.append(self.analyze_class_definition(node))
            except Exception as exc:  # noqa: BLE001
                log.warning("run_on_source: failed on '%s': %s", node.name, exc)
        return records


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log.info("=== class_creation_as_staged_semantics smoke test ===")

    _SOURCE = '''
import abc

class Plain:
    x = 1

class WithMeta(metaclass=type):
    def __prepare__(name, bases):
        return {}

class MyABC(abc.ABC):
    @abc.abstractmethod
    def run(self): ...

import dataclasses
@dataclasses.dataclass
class DC:
    x: int = 0
    def __set_name__(self, owner, name): pass
'''

    coord = ClassCreationStagedSemanticsCoordinator()
    analyzer = ClassCreationStagedSemanticsAnalyzer()
    witness = ClassCreationStagedSemanticsWitness()

    records = coord.run_on_source(_SOURCE)
    print(f"Records found: {len(records)}")
    for r in records:
        print(f"  {r.class_name}: metaclass={r.metaclass_name!r}, phase_count={r.phase_count}")

    # Runtime witness on a live class
    class _Demo:
        class_var: int = 42
        def method(self): ...

    wr = witness.witness_class_creation(_Demo)
    print(f"Witness: class={wr.class_name}, mro={wr.mro_chain}, abstract={wr.is_abstract}")

    j = coord.build_creation_judgment(records[0])
    print(f"Judgment: status={j.status}, label={j.label}")
    j.settle()
    print(f"After settle: status={j.status}")

    # ThreePhaseTrace
    trace = coord.trace_three_phases(_Demo)
    morphisms = coord.build_phase_morphisms(trace)
    print(f"Phase morphisms: {[m.label for m in morphisms]}")

    # SetNameCallRecord
    class _DescBase:
        def __set_name__(self, owner, name): pass

    class _DescHolder:
        d = _DescBase()

    sn = witness.record_set_name_calls(_DescHolder)
    print(f"SetName records: {sn}")

    log.info("=== smoke test complete ===")
