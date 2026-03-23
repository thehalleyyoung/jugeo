"""
metaclasses_as_contract_transformers.py
============================================
theory2.tex — Chapter 20 §3: "Metaclasses as contract transformers"

A metaclass is a *class factory*: it accepts the triple
  (name: str, bases: tuple[type, ...], namespace: dict)
and produces a class object.

In JuGeo's site-theoretic framework a metaclass is a **contract transformer**:
it takes an input contract (the class body specification, expressed as a
namespace dict) and maps it to an output contract (the public class interface).
This is analogous to a functor in the site category:
  F : C_body → C_interface
where C_body is the coordinate for the class body namespace and C_interface
is the coordinate for the class object.

Key sub-patterns:
  Singleton   — __new__ stores and returns a single instance.
  Registry    — __init__ adds every new subclass to a registry dict.
  Abstract    — ABCMeta refuses to instantiate classes with abstract methods.
  Validator   — __new__ validates the namespace before creating the class.
  DescriptorInjector — metaclass injects descriptors into the namespace.

This module implements:
  • MetaclassesContractTransformersCoordinator — orchestrates metaclass analysis.
  • MetaclassesContractTransformersAnalyzer    — static / AST analysis.
  • MetaclassesContractTransformersWitness     — runtime witnessing helpers.

Cross-references:
  theory2.tex §20.3 (metaclass transformers), §20.1 (staged semantics),
  §15.1 (functor laws), §18.7 (contract morphisms).
"""
from __future__ import annotations

import abc
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

class MetaclassPattern(str, Enum):
    """Recognised metaclass design patterns.

    # copilot: used as the primary discriminant when comparing metaclasses
    in the judgment graph.  The patterns are ordered from most to least
    invasive in terms of namespace transformation.

    PLAIN_TYPE          — trivial metaclass; uses type.__new__ unchanged.
    SINGLETON           — __new__ returns a cached instance.
    REGISTRY            — __init__ adds each new class to a registry.
    ABSTRACT            — ABCMeta pattern: enforces abstract method implementation.
    PROTOCOL            — typing.Protocol pattern: structural subtyping checks.
    DESCRIPTOR_INJECTOR — metaclass injects descriptors into the class dict.
    VALIDATOR           — metaclass validates the namespace before creation.
    """
    PLAIN_TYPE = "plain_type"
    SINGLETON = "singleton"
    REGISTRY = "registry"
    ABSTRACT = "abstract"
    PROTOCOL = "protocol"
    DESCRIPTOR_INJECTOR = "descriptor_injector"
    VALIDATOR = "validator"


# ===========================================================================
# Value-object dataclasses (frozen, slots)
# ===========================================================================

@dataclass(frozen=True, slots=True)
class MetaclassContractRecord:
    """Immutable record describing a metaclass contract.

    # copilot: produced by MetaclassesContractTransformersCoordinator.analyze_metaclass.
    Represents the *input contract specification* of the metaclass transformer.

    Attributes
    ----------
    metaclass_name   : __qualname__ of the metaclass.
    metaclass_bases  : tuple of base metaclass names.
    has_new          : True iff metaclass overrides __new__.
    has_init         : True iff metaclass overrides __init__.
    has_prepare      : True iff metaclass defines __prepare__.
    has_call         : True iff metaclass overrides __call__.
    transformed_attrs: tuple of attribute names the metaclass adds/mutates.
    pattern          : recognised MetaclassPattern.
    """
    metaclass_name: str
    metaclass_bases: tuple
    has_new: bool
    has_init: bool
    has_prepare: bool
    has_call: bool
    transformed_attrs: tuple
    pattern: "MetaclassPattern"


@dataclass(frozen=True, slots=True)
class ContractTransformationTrace:
    """Trace of a single metaclass contract transformation.

    # copilot: produced by trace_contract_transformation.  Captures the
    before/after state of the namespace and which transformations occurred.

    Attributes
    ----------
    metaclass_name          : __qualname__ of the metaclass performing the transform.
    input_name              : name argument passed to __new__.
    input_bases             : tuple of base class names.
    input_namespace_keys    : tuple of keys in the namespace before __new__.
    output_attrs            : tuple of attribute names in the created class.
    transformations_applied : tuple of TransformationStep descriptions.
    """
    metaclass_name: str
    input_name: str
    input_bases: tuple
    input_namespace_keys: tuple
    output_attrs: tuple
    transformations_applied: tuple


@dataclass(frozen=True, slots=True)
class MetaclassConflict:
    """Record of a metaclass conflict between two base-class metaclasses.

    # copilot: Python raises TypeError when two base classes have incompatible
    metaclasses.  This record captures the conflict for downstream judgment
    construction.

    Attributes
    ----------
    metaclass1           : first metaclass name.
    metaclass2           : second metaclass name.
    conflict_kind        : short description ('incompatible_mro', 'diamond', etc.).
    resolution_available : True iff a common subclass metaclass exists.
    winning_metaclass    : name of the metaclass that would win, or ''.
    """
    metaclass1: str
    metaclass2: str
    conflict_kind: str
    resolution_available: bool
    winning_metaclass: str


@dataclass(frozen=True, slots=True)
class MetaclassInheritanceRecord:
    """AST-level record of a metaclass definition.

    # copilot: produced by extract_metaclass_hierarchy from source text.

    Attributes
    ----------
    metaclass_name    : name of the metaclass.
    parent_metaclasses: tuple of parent metaclass names.
    adds_new          : True iff __new__ is defined in the class body.
    adds_init         : True iff __init__ is defined in the class body.
    adds_prepare      : True iff __prepare__ is defined in the class body.
    line_no           : source line of the class statement.
    """
    metaclass_name: str
    parent_metaclasses: tuple
    adds_new: bool
    adds_init: bool
    adds_prepare: bool
    line_no: int


@dataclass(frozen=True, slots=True)
class NewOverrideRecord:
    """Description of a __new__ override in a metaclass.

    # copilot: produced by detect_new_override.  Captures whether the
    override modifies bases or namespace beyond calling super().__new__.

    Attributes
    ----------
    class_name         : __qualname__ of the metaclass.
    signature          : str(inspect.signature(...)) for __new__.
    modifies_bases     : True iff the implementation modifies the bases tuple.
    modifies_namespace : True iff the implementation mutates the namespace dict.
    adds_attrs         : tuple of attribute names added by __new__.
    """
    class_name: str
    signature: str
    modifies_bases: bool
    modifies_namespace: bool
    adds_attrs: tuple


@dataclass(frozen=True, slots=True)
class InitOverrideRecord:
    """Description of a __init__ override in a metaclass.

    # copilot: produced by detect_init_override.

    Attributes
    ----------
    class_name          : __qualname__ of the metaclass.
    signature           : str(inspect.signature(...)) for __init__.
    modifies_class      : True iff __init__ modifies the class object after creation.
    post_creation_effects: tuple of attribute names set during __init__.
    """
    class_name: str
    signature: str
    modifies_class: bool
    post_creation_effects: tuple


@dataclass(frozen=True, slots=True)
class TransformationStep:
    """One step in a contract transformation pipeline.

    # copilot: a sequence of TransformationSteps is produced by
    analyze_class_body_transformations.

    Attributes
    ----------
    step_index   : ordinal index of this step (0-based).
    step_kind    : short label (e.g. 'validate', 'inject', 'register').
    input_keys   : tuple of namespace keys present before this step.
    output_keys  : tuple of namespace keys present after this step.
    description  : human-readable description of what this step does.
    """
    step_index: int
    step_kind: str
    input_keys: tuple
    output_keys: tuple
    description: str


@dataclass(frozen=True, slots=True)
class InjectedDescriptorRecord:
    """Record of a descriptor injected by a metaclass into a class namespace.

    # copilot: produced by analyze_descriptor_injection.  Tracks where the
    descriptor came from and which classes it is injected into.

    Attributes
    ----------
    attr_name        : attribute name under which the descriptor is stored.
    descriptor_class : __qualname__ of the descriptor type.
    injection_method : which metaclass method performs the injection
                       (__new__, __init__, __prepare__).
    target_classes   : tuple of class names the descriptor was injected into.
    """
    attr_name: str
    descriptor_class: str
    injection_method: str
    target_classes: tuple


@dataclass(frozen=True, slots=True)
class MetaclassCallWitnessRecord:
    """Runtime witness record for a metaclass __call__ (class creation) event.

    # copilot: produced by witness_metaclass_call.  Captures namespace
    state before and after __new__ to detect transformations.

    Attributes
    ----------
    metaclass_name         : __qualname__ of the metaclass.
    class_name             : name argument passed to __call__.
    bases                  : tuple of base class names.
    namespace_keys_before  : tuple of namespace keys before __new__.
    namespace_keys_after   : tuple of namespace keys on the resulting class.
    class_created          : True iff a class object was successfully created.
    creation_time_ns       : monotonic timestamp of the creation event.
    """
    metaclass_name: str
    class_name: str
    bases: tuple
    namespace_keys_before: tuple
    namespace_keys_after: tuple
    class_created: bool
    creation_time_ns: int


@dataclass(frozen=True, slots=True)
class NamespaceMutationRecord:
    """Summary of mutations applied to a class namespace by a metaclass.

    # copilot: produced by record_namespace_mutations by diffing before/after
    namespace dicts.

    Attributes
    ----------
    added_keys     : keys present in after but not before.
    removed_keys   : keys present in before but not after.
    modified_keys  : keys present in both but with different values (by type).
    total_mutations: total count of add + remove + modify.
    """
    added_keys: tuple
    removed_keys: tuple
    modified_keys: tuple
    total_mutations: int


# ===========================================================================
# Analyzer
# ===========================================================================

class MetaclassesContractTransformersAnalyzer:
    """Static / AST-based analysis tools for metaclass contract transformers.

    Methods in this class operate on either source text (via AST) or live
    class objects.  They are side-effect-free with respect to the inspected
    classes.

    In the site-theoretic model, this analyzer computes the *functor image*
    of the metaclass: given the input contract (namespace), what is the
    output contract (class interface)?

    # copilot: for AST-based methods, assume the source is valid Python 3.10+.
    """

    # ------------------------------------------------------------------
    # AST-level analysis
    # ------------------------------------------------------------------

    def extract_metaclass_hierarchy(self, source: str) -> list:
        """Parse *source* and extract records for every class that looks like a metaclass.

        A class is classified as a metaclass candidate if:
          - Its name ends with 'Meta' or 'Metaclass', OR
          - It inherits from 'type' or another known metaclass.

        Parameters
        ----------
        source : str
            Valid Python source text.

        Returns
        -------
        list[MetaclassInheritanceRecord]
            One record per detected metaclass candidate.

        # copilot: heuristic detection; classes that inherit from 'type' are
        the most reliable metaclass candidates.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            log.warning("extract_metaclass_hierarchy: SyntaxError: %s", exc)
            return []

        records: list = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names: list = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)

            is_metaclass = (
                node.name.endswith("Meta") or
                node.name.endswith("Metaclass") or
                "type" in base_names or
                any(n.endswith("Meta") for n in base_names)
            )
            if not is_metaclass:
                continue

            adds_new = any(
                isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__new__"
                for s in node.body
            )
            adds_init = any(
                isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__init__"
                for s in node.body
            )
            adds_prepare = any(
                isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__prepare__"
                for s in node.body
            )
            records.append(MetaclassInheritanceRecord(
                metaclass_name=node.name,
                parent_metaclasses=tuple(base_names),
                adds_new=adds_new,
                adds_init=adds_init,
                adds_prepare=adds_prepare,
                line_no=node.lineno,
            ))
        return records

    # ------------------------------------------------------------------
    # Runtime inspection
    # ------------------------------------------------------------------

    def detect_new_override(self, metaclass: type):
        """Detect whether *metaclass* overrides __new__ beyond type.__new__.

        Parameters
        ----------
        metaclass : type
            The metaclass to inspect.

        Returns
        -------
        NewOverrideRecord | None
            A record if __new__ is overridden, else None.

        # copilot: uses source introspection with inspect.getsource to
        heuristically detect namespace/bases mutation.
        """
        if "__new__" not in metaclass.__dict__:
            return None

        new_fn = metaclass.__dict__["__new__"]
        sig = ""
        try:
            sig = str(inspect.signature(new_fn))
        except (ValueError, TypeError):
            sig = "(...)"

        src = ""
        try:
            src = inspect.getsource(new_fn)
        except (OSError, TypeError):
            src = ""

        modifies_bases = "bases" in src and ("bases =" in src or "bases[" in src or "list(bases)" in src)
        modifies_namespace = "namespace" in src and (
            "namespace[" in src or "namespace.update" in src or "namespace.pop" in src
        )

        # Heuristic: look for assignments to new attribute names
        added: list = []
        if src:
            for line in src.splitlines():
                stripped = line.strip()
                if stripped.startswith("cls.") and "=" in stripped:
                    attr = stripped.split(".")[1].split("=")[0].strip()
                    if attr.isidentifier():
                        added.append(attr)

        return NewOverrideRecord(
            class_name=metaclass.__qualname__,
            signature=sig,
            modifies_bases=modifies_bases,
            modifies_namespace=modifies_namespace,
            adds_attrs=tuple(sorted(set(added))),
        )

    def detect_init_override(self, metaclass: type):
        """Detect whether *metaclass* overrides __init__ beyond type.__init__.

        Parameters
        ----------
        metaclass : type
            The metaclass to inspect.

        Returns
        -------
        InitOverrideRecord | None
            A record if __init__ is overridden, else None.

        # copilot: heuristic: any cls.X = ... pattern in __init__ source
        is considered a post-creation effect.
        """
        if "__init__" not in metaclass.__dict__:
            return None

        init_fn = metaclass.__dict__["__init__"]
        sig = ""
        try:
            sig = str(inspect.signature(init_fn))
        except (ValueError, TypeError):
            sig = "(...)"

        src = ""
        try:
            src = inspect.getsource(init_fn)
        except (OSError, TypeError):
            src = ""

        post_effects: list = []
        modifies_class = False
        if src:
            for line in src.splitlines():
                stripped = line.strip()
                # Pattern: cls.<name> = ...
                if stripped.startswith("cls.") and "=" in stripped:
                    modifies_class = True
                    attr = stripped.split(".")[1].split("=")[0].strip()
                    if attr.isidentifier():
                        post_effects.append(attr)
                # Pattern: self.<name> = ... where self is the class
                if stripped.startswith("self.") and "=" in stripped:
                    attr = stripped.split(".")[1].split("=")[0].strip()
                    if attr.isidentifier():
                        post_effects.append(attr)

        return InitOverrideRecord(
            class_name=metaclass.__qualname__,
            signature=sig,
            modifies_class=modifies_class,
            post_creation_effects=tuple(sorted(set(post_effects))),
        )

    def analyze_class_body_transformations(self, metaclass: type) -> list:
        """Produce a sequence of TransformationSteps for *metaclass*.

        The sequence models how the metaclass transforms the input namespace
        into the output class object.

        Parameters
        ----------
        metaclass : type
            The metaclass to analyse.

        Returns
        -------
        list[TransformationStep]
            Steps in execution order: __prepare__ → body → __new__ → __init__.

        # copilot: steps are inferred from which dunder methods are defined.
        This is a logical model, not an execution trace.
        """
        steps: list = []
        all_keys = tuple(sorted(metaclass.__dict__.keys()))

        if "__prepare__" in metaclass.__dict__:
            steps.append(TransformationStep(
                step_index=0,
                step_kind="prepare",
                input_keys=(),
                output_keys=all_keys,
                description=f"{metaclass.__qualname__}.__prepare__ produces custom namespace",
            ))

        steps.append(TransformationStep(
            step_index=len(steps),
            step_kind="body_execution",
            input_keys=all_keys,
            output_keys=all_keys,
            description="class body executes, filling namespace",
        ))

        if "__new__" in metaclass.__dict__:
            steps.append(TransformationStep(
                step_index=len(steps),
                step_kind="new",
                input_keys=all_keys,
                output_keys=all_keys,
                description=f"{metaclass.__qualname__}.__new__ constructs class object",
            ))

        if "__init__" in metaclass.__dict__:
            steps.append(TransformationStep(
                step_index=len(steps),
                step_kind="init",
                input_keys=all_keys,
                output_keys=all_keys,
                description=f"{metaclass.__qualname__}.__init__ post-processes class",
            ))

        if not steps:
            steps.append(TransformationStep(
                step_index=0,
                step_kind="passthrough",
                input_keys=(),
                output_keys=all_keys,
                description="trivial metaclass; delegates entirely to type",
            ))

        return steps

    def classify_abc_pattern(self, cls: type) -> bool:
        """Return True iff *cls* uses the ABCMeta metaclass pattern.

        Parameters
        ----------
        cls : type
            Any Python class.

        Returns
        -------
        bool
            True iff type(cls) is (or inherits from) abc.ABCMeta.

        # copilot: ABCMeta adds __abstractmethods__ as a frozenset and
        enforces non-empty means TypeError on instantiation.
        """
        return isinstance(cls, abc.ABCMeta)

    def detect_singleton_pattern(self, metaclass: type) -> bool:
        """Heuristically detect whether *metaclass* implements a Singleton.

        The heuristic looks for a ``_instances`` or ``_instance`` dict/attribute
        on the metaclass class body and a ``__new__`` or ``__call__`` override
        that returns a cached instance.

        Parameters
        ----------
        metaclass : type
            The metaclass to inspect.

        Returns
        -------
        bool
            True iff Singleton pattern is detected.

        # copilot: this is a best-effort heuristic; it will produce false
        negatives for unconventionally named singleton caches.
        """
        src = ""
        try:
            src = inspect.getsource(metaclass)
        except (OSError, TypeError):
            src = ""

        singleton_markers = ("_instances", "_instance", "_singleton")
        has_marker = any(m in src for m in singleton_markers)
        has_new_or_call = ("__new__" in metaclass.__dict__ or "__call__" in metaclass.__dict__)
        return has_marker and has_new_or_call

    def analyze_descriptor_injection(self, metaclass: type) -> list:
        """Detect descriptors injected into class namespaces by *metaclass*.

        Looks for assignments in __new__ or __init__ source of the form:
          namespace[<name>] = <Descriptor>(...)
          cls.<name> = <Descriptor>(...)

        Parameters
        ----------
        metaclass : type
            The metaclass to inspect.

        Returns
        -------
        list[InjectedDescriptorRecord]
            One record per detected descriptor injection.

        # copilot: produces conservative results; only patterns that clearly
        assign a callable with 'Descriptor' or 'descriptor' in the name are
        reported.
        """
        records: list = []
        for method_name in ("__new__", "__init__", "__prepare__"):
            method = metaclass.__dict__.get(method_name)
            if method is None:
                continue
            src = ""
            try:
                src = inspect.getsource(method)
            except (OSError, TypeError):
                continue
            for line in src.splitlines():
                stripped = line.strip()
                # Pattern: namespace["name"] = SomeDescriptor(...)
                if 'namespace[' in stripped and '=' in stripped and '(' in stripped:
                    lhs, _, rhs = stripped.partition("=")
                    attr_name = lhs.strip().strip("namespace[\"']").rstrip("\"']")
                    desc_class = rhs.strip().split("(")[0].strip()
                    if attr_name and desc_class and desc_class[0].isupper():
                        records.append(InjectedDescriptorRecord(
                            attr_name=attr_name,
                            descriptor_class=desc_class,
                            injection_method=method_name,
                            target_classes=(),
                        ))
        return records


# ===========================================================================
# Witness
# ===========================================================================

class MetaclassesContractTransformersWitness:
    """Runtime witnessing helpers for metaclass contract transformers.

    Methods here observe live metaclass invocations and record the resulting
    namespace mutations.

    In the site-theoretic model, witnesses confirm the functor laws:
    identity preservation and composition.  A metaclass that does not
    preserve the identity morphism (i.e., passes through an unchanged
    namespace when no customisation is needed) violates functor law #1.

    # copilot: witness_metaclass_call actually creates a class; use only
    with synthetic test metaclasses, not production metaclasses with
    side effects.
    """

    def witness_metaclass_call(
        self,
        metaclass: type,
        name: str,
        bases: tuple,
        ns: dict,
    ) -> MetaclassCallWitnessRecord:
        """Create a class with *metaclass* and record the transformation.

        Parameters
        ----------
        metaclass : type
            The metaclass to invoke.
        name      : str   — class name.
        bases     : tuple — base classes.
        ns        : dict  — class namespace.

        Returns
        -------
        MetaclassCallWitnessRecord
            Record of the creation event.

        # copilot: uses try/except to handle metaclass validation errors;
        class_created will be False if metaclass raises TypeError.
        """
        ns_before = tuple(sorted(ns.keys()))
        bases_names = tuple(b.__qualname__ for b in bases if isinstance(b, type))
        created = False
        ns_after: tuple = ()
        t_ns = time.monotonic_ns()
        try:
            cls_obj = metaclass(name, bases, dict(ns))
            ns_after = tuple(sorted(cls_obj.__dict__.keys()))
            created = True
        except Exception as exc:  # noqa: BLE001
            log.debug("witness_metaclass_call: %r raised %s", metaclass, exc)
            ns_after = ns_before

        return MetaclassCallWitnessRecord(
            metaclass_name=metaclass.__qualname__,
            class_name=name,
            bases=bases_names,
            namespace_keys_before=ns_before,
            namespace_keys_after=ns_after,
            class_created=created,
            creation_time_ns=t_ns,
        )

    def probe_class_creation_hooks(self, metaclass: type) -> list:
        """List all class-creation hook methods defined on *metaclass*.

        Looks for __prepare__, __new__, __init__, __call__, __init_subclass__,
        and __set_name__ in the metaclass's own __dict__ (not inherited).

        Parameters
        ----------
        metaclass : type
            The metaclass to probe.

        Returns
        -------
        list[str]
            Sorted list of hook names found directly on the metaclass.

        # copilot: does NOT walk the MRO; inherited hooks are not reported.
        """
        _HOOKS = frozenset({
            "__prepare__", "__new__", "__init__", "__call__",
            "__init_subclass__", "__set_name__", "__class_getitem__",
        })
        return sorted(name for name in _HOOKS if name in metaclass.__dict__)

    def record_namespace_mutations(self, before: dict, after: dict) -> NamespaceMutationRecord:
        """Diff two namespace dicts and return a mutation summary.

        Parameters
        ----------
        before : dict  — namespace before metaclass transformation.
        after  : dict  — namespace after metaclass transformation.

        Returns
        -------
        NamespaceMutationRecord
            Summary of added, removed, and modified keys.

        # copilot: 'modified' means the key is present in both dicts but
        the *type* of the value differs — value equality is not checked to
        avoid triggering __eq__ on arbitrary objects.
        """
        before_keys = set(before.keys())
        after_keys = set(after.keys())
        added = tuple(sorted(after_keys - before_keys))
        removed = tuple(sorted(before_keys - after_keys))
        common = before_keys & after_keys
        modified = tuple(sorted(
            k for k in common if type(before[k]) is not type(after[k])
        ))
        total = len(added) + len(removed) + len(modified)
        return NamespaceMutationRecord(
            added_keys=added,
            removed_keys=removed,
            modified_keys=modified,
            total_mutations=total,
        )

    def build_witness_judgment(self, record: MetaclassCallWitnessRecord) -> Judgment:
        """Build a JuGeo Judgment from a MetaclassCallWitnessRecord.

        Parameters
        ----------
        record : MetaclassCallWitnessRecord

        Returns
        -------
        Judgment
            SETTLED if the class was created, OBSTRUCTED otherwise.

        # copilot: trust level is RUNTIME_WITNESSED for all metaclass call
        witnesses; no additional static analysis is performed.
        """
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            statement=(
                f"Metaclass '{record.metaclass_name}' successfully transforms "
                f"contract for class '{record.class_name}'"
            ),
            label=f"metaclass_call:{record.metaclass_name}:{record.class_name}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(record.metaclass_name, record.class_name),
                kind=CoordinateKind.MODULE,
            ),
            payload=record,
            label=f"{record.metaclass_name}:{record.class_name}",
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_TRACE,
            payload=record,
            label="metaclass_call_witness",
        ))
        j = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED, rationale="metaclass call"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"mctw:{record.metaclass_name}:{record.class_name}",
        )
        if record.class_created:
            j.settle()
        else:
            j.obstruct(Obstruction(
                description=f"Metaclass {record.metaclass_name} failed to create {record.class_name}",
            ))
        return j


# ===========================================================================
# Coordinator
# ===========================================================================

class MetaclassesContractTransformersCoordinator:
    """Orchestrator for metaclass contract-transformer analysis.

    Combines the Analyzer and Witness to produce comprehensive metaclass
    records and JuGeo judgments.

    In the site-theoretic model, the Coordinator verifies the *functor
    coherence* conditions for each metaclass: the transformation is
    natural in the class-name argument and compatible with base-class
    transformations.

    # copilot: this class is stateful; the _cache dict prevents redundant
    re-analysis of the same metaclass object.
    """

    def __init__(self) -> None:
        self._analyzer = MetaclassesContractTransformersAnalyzer()
        self._witness = MetaclassesContractTransformersWitness()
        self._cache: dict = {}

    def analyze_metaclass(self, metaclass: type) -> MetaclassContractRecord:
        """Produce a MetaclassContractRecord for *metaclass*.

        Parameters
        ----------
        metaclass : type
            Any metaclass (subclass of type).

        Returns
        -------
        MetaclassContractRecord
            Immutable record describing the metaclass contract.

        # copilot: results are cached by metaclass id() to avoid redundant
        analysis.
        """
        cache_key = id(metaclass)
        if cache_key in self._cache:
            return self._cache[cache_key]

        mc_dict = metaclass.__dict__
        has_new = "__new__" in mc_dict and mc_dict["__new__"] is not type.__new__
        has_init = "__init__" in mc_dict
        has_prepare = "__prepare__" in mc_dict
        has_call = "__call__" in mc_dict

        transformed_attrs = tuple(sorted(
            k for k in mc_dict
            if not k.startswith("__") or k in ("__new__", "__init__", "__prepare__", "__call__")
        ))

        pattern = self.classify_metaclass_pattern(metaclass)
        bases = tuple(b.__qualname__ for b in metaclass.__mro__[1:] if b is not object)

        record = MetaclassContractRecord(
            metaclass_name=metaclass.__qualname__,
            metaclass_bases=bases,
            has_new=has_new,
            has_init=has_init,
            has_prepare=has_prepare,
            has_call=has_call,
            transformed_attrs=transformed_attrs,
            pattern=pattern,
        )
        self._cache[cache_key] = record
        log.debug("analyze_metaclass: produced record for '%s'", metaclass.__qualname__)
        return record

    def trace_contract_transformation(
        self,
        metaclass: type,
        name: str,
        bases: tuple,
        namespace: dict,
    ) -> ContractTransformationTrace:
        """Trace the contract transformation performed by *metaclass*.

        Parameters
        ----------
        metaclass  : type  — the metaclass to trace.
        name       : str   — class name argument.
        bases      : tuple — base classes.
        namespace  : dict  — input class body namespace.

        Returns
        -------
        ContractTransformationTrace
            Before/after snapshot of the namespace transformation.

        # copilot: actually invokes the metaclass via witness_metaclass_call
        to capture the output namespace.
        """
        witness_rec = self._witness.witness_metaclass_call(metaclass, name, bases, namespace)
        steps = self._analyzer.analyze_class_body_transformations(metaclass)
        step_descs = tuple(s.description for s in steps)
        return ContractTransformationTrace(
            metaclass_name=metaclass.__qualname__,
            input_name=name,
            input_bases=tuple(b.__qualname__ for b in bases if isinstance(b, type)),
            input_namespace_keys=witness_rec.namespace_keys_before,
            output_attrs=witness_rec.namespace_keys_after,
            transformations_applied=step_descs,
        )

    def detect_metaclass_conflicts(self, bases: tuple) -> list:
        """Detect metaclass conflicts among *bases*.

        A conflict occurs when two base classes have incompatible metaclasses
        (neither is a subclass of the other).

        Parameters
        ----------
        bases : tuple[type, ...]
            The proposed base classes for a new class.

        Returns
        -------
        list[MetaclassConflict]
            One MetaclassConflict per detected incompatibility.

        # copilot: uses issubclass to detect whether one metaclass is a
        subtype of the other; if so, the more-derived one wins (no conflict).
        """
        metaclasses = [(b, type(b)) for b in bases if isinstance(b, type)]
        conflicts: list = []
        for i, (b1, m1) in enumerate(metaclasses):
            for b2, m2 in metaclasses[i + 1:]:
                if m1 is m2:
                    continue
                if issubclass(m1, m2):
                    winning = m1.__qualname__
                    available = True
                elif issubclass(m2, m1):
                    winning = m2.__qualname__
                    available = True
                else:
                    winning = ""
                    available = False
                    conflicts.append(MetaclassConflict(
                        metaclass1=m1.__qualname__,
                        metaclass2=m2.__qualname__,
                        conflict_kind="incompatible_metaclass_mro",
                        resolution_available=available,
                        winning_metaclass=winning,
                    ))
        return conflicts

    def build_transformation_morphism(self, trace: ContractTransformationTrace) -> object:
        """Build a Morphism representing the contract transformation.

        Parameters
        ----------
        trace : ContractTransformationTrace

        Returns
        -------
        Morphism
            Source = body-namespace coordinate; target = class-object coordinate.
            Kind = TRANSPORT (namespace → class object).

        # copilot: TRANSPORT morphisms are the site-theoretic representation
        of type.__new__ calls.
        """
        source = Coordinate(
            components=(trace.metaclass_name, trace.input_name, "namespace"),
            kind=CoordinateKind.REGION,
        )
        target = Coordinate(
            components=(trace.metaclass_name, trace.input_name, "class_object"),
            kind=CoordinateKind.INTERFACE,
        )
        return Morphism(
            source=source,
            target=target,
            kind=MorphismKind.TRANSPORT,
            label=f"metaclass_transform:{trace.metaclass_name}:{trace.input_name}",
        )

    def build_metaclass_judgment(self, record: MetaclassContractRecord) -> Judgment:
        """Build a JuGeo Judgment for a MetaclassContractRecord.

        Parameters
        ----------
        record : MetaclassContractRecord

        Returns
        -------
        Judgment
            A PROPOSED judgment describing the metaclass contract.

        # copilot: trust is COPILOT_SUGGESTED since this is static analysis.
        Runtime witnessing upgrades to RUNTIME_WITNESSED.
        """
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Metaclass '{record.metaclass_name}' implements the "
                f"{record.pattern.value} contract-transformer pattern "
                f"(has_new={record.has_new}, has_prepare={record.has_prepare})"
            ),
            label=f"metaclass_contract:{record.metaclass_name}",
        )
        carrier = Carrier(
            coordinate=Coordinate(
                components=(record.metaclass_name,),
                kind=CoordinateKind.INTERFACE,
            ),
            payload=record,
            label=record.metaclass_name,
        )
        bundle = EvidenceBundle()
        bundle.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload=record,
            label="metaclass_record",
        ))
        return Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            trust=TrustAnnotation(level=TrustLevel.COPILOT_SUGGESTED, rationale="metaclass analysis"),
            provenance=Provenance(source=__name__, module=__name__),
            label=f"mct:{record.metaclass_name}",
        )

    def classify_metaclass_pattern(self, metaclass: type) -> MetaclassPattern:
        """Classify *metaclass* into a MetaclassPattern.

        Parameters
        ----------
        metaclass : type
            The metaclass to classify.

        Returns
        -------
        MetaclassPattern
            The most specific applicable pattern.

        # copilot: priority order is:
        SINGLETON > REGISTRY > ABSTRACT > PROTOCOL > DESCRIPTOR_INJECTOR >
        VALIDATOR > PLAIN_TYPE.
        """
        if self._analyzer.detect_singleton_pattern(metaclass):
            return MetaclassPattern.SINGLETON

        if isinstance(metaclass, type) and issubclass(metaclass, abc.ABCMeta):
            return MetaclassPattern.ABSTRACT

        src = ""
        try:
            src = inspect.getsource(metaclass)
        except (OSError, TypeError):
            src = ""

        if "_registry" in src or "_subclasses" in src or "registry" in src.lower():
            return MetaclassPattern.REGISTRY

        inj = self._analyzer.analyze_descriptor_injection(metaclass)
        if inj:
            return MetaclassPattern.DESCRIPTOR_INJECTOR

        if "raise" in src and ("namespace" in src or "attrs" in src):
            return MetaclassPattern.VALIDATOR

        has_new = "__new__" in metaclass.__dict__
        has_init = "__init__" in metaclass.__dict__
        if not has_new and not has_init:
            return MetaclassPattern.PLAIN_TYPE

        return MetaclassPattern.PLAIN_TYPE


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log.info("=== metaclasses_as_contract_transformers smoke test ===")

    # --- Define a simple registry metaclass ---
    class RegistryMeta(type):
        """Metaclass that tracks all subclasses in a registry."""
        _registry: dict = {}

        def __new__(mcs, name, bases, namespace):
            cls = super().__new__(mcs, name, bases, namespace)
            mcs._registry[name] = cls
            return cls

    class Base(metaclass=RegistryMeta):
        pass

    class Child(Base):
        pass

    coord = MetaclassesContractTransformersCoordinator()
    analyzer = MetaclassesContractTransformersAnalyzer()
    witness = MetaclassesContractTransformersWitness()

    # Analyze the metaclass
    rec = coord.analyze_metaclass(RegistryMeta)
    print(f"MetaclassContractRecord: {rec.metaclass_name}, pattern={rec.pattern}")

    # Transformation trace
    trace = coord.trace_contract_transformation(RegistryMeta, "TestCls", (object,), {"x": 1})
    print(f"Trace: {trace.metaclass_name}, output_attrs={trace.output_attrs[:5]}")

    # Conflict detection
    conflicts = coord.detect_metaclass_conflicts((Base, Child))
    print(f"Conflicts: {conflicts}")

    # ABCMeta classification
    is_abc = analyzer.classify_abc_pattern(abc.ABC)
    print(f"abc.ABC is_abc: {is_abc}")

    # Witness
    wr = witness.witness_metaclass_call(RegistryMeta, "WitnessDemo", (object,), {"val": 99})
    print(f"Witness: created={wr.class_created}, ns_after count={len(wr.namespace_keys_after)}")

    # Namespace mutation
    before = {"x": 1, "y": 2}
    after = {"x": 1, "y": "changed", "z": 3}
    mut = witness.record_namespace_mutations(before, after)
    print(f"Mutations: added={mut.added_keys}, modified={mut.modified_keys}, total={mut.total_mutations}")

    # Hooks
    hooks = witness.probe_class_creation_hooks(RegistryMeta)
    print(f"Hooks: {hooks}")

    # Judgment
    j = coord.build_metaclass_judgment(rec)
    j.settle()
    print(f"Judgment: {j.label}, status={j.status}")

    # Source-level extraction
    _SRC = """
class SingletonMeta(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
"""
    records = analyzer.extract_metaclass_hierarchy(_SRC)
    print(f"Metaclass hierarchy records from source: {[r.metaclass_name for r in records]}")

    log.info("=== smoke test complete ===")
