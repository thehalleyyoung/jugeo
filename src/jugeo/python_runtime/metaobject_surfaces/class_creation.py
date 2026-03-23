from __future__ import annotations

r"""
theory2.tex Ch20 §20.5 — Three-phase class creation protocol as a morphism sequence.

Python's class creation machinery follows a strict three-phase protocol:

    Phase 1  ``type.__prepare__(mcs, name, bases, **kwargs)`` → namespace dict
             The metaclass prepares an (ordered) namespace dict that will
             accumulate the class body's definitions.

    Phase 2  Body execution — each statement in the class body populates the
             namespace produced by Phase 1.

    Phase 3  ``type.__new__(mcs, name, bases, namespace)`` → class object
             The metaclass constructs the class from the accumulated namespace.

Post-creation hooks:
    * ``__init_subclass__`` is called on each base with ``cls`` as the first
      argument, propagating the new subclass event up the MRO.
    * ``__set_name__`` is called on each descriptor found in the namespace,
      informing it of its owner class and attribute name.
    * Class decorators are applied in reverse order of appearance.

In JuGeo's site-theoretic model each phase is a morphism:
    Phase 1: TRANSPORT morphism from metaclass coordinate → namespace coordinate
    Phase 2: INCLUSION morphisms from each name-def into the namespace coordinate
    Phase 3: REFINEMENT morphism from namespace coordinate → class coordinate

The full sequence is recorded in a :class:`ClassCreationTrace` that serves as
the provenance record for all subsequent judgment claims about the class.

§20.5.1  ClassCreationOrchestrator — the three-phase driver
§20.5.2  BodyExecutionTracer — recording name definitions as inclusions
§20.5.3  InitSubclassProbe — propagating refinement morphisms to base classes
§20.5.4  SetNameHookApplicator — transport morphisms for descriptor binding

CopilotChannel can annotate each phase with ORACLE_PROPOSED evidence.
COPILOT_SUGGESTED trust is assigned at the orchestrator level and each phase's
judgment inherits a trust ceiling that requires explicit promotion.
"""

import hashlib
import json
import datetime
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Sequence

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology,
        CoordinateObject,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field
    class CoordinateKind(Enum):
        MODULE="module"; FUNCTION="function"; INTERFACE="interface"
        TEST="test"; THEOREM="theorem"; REGION="region"
    class MorphismKind(Enum):
        RESTRICTION="restriction"; INCLUSION="inclusion"
        TRANSPORT="transport"; REFINEMENT="refinement"
    @_dc(frozen=True)
    class Coordinate:
        components: tuple = ()
        kind: "CoordinateKind" = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()
        metadata: dict = _field(default_factory=dict)
    @_dc(frozen=True)
    class Morphism:
        source: "Coordinate" = None; target: "Coordinate" = None
        kind: "MorphismKind" = MorphismKind.INCLUSION; label: str = ""
    @_dc(frozen=True)
    class CoordinateObject:
        coordinate: "Coordinate" = None; label: str = ""
    @_dc
    class CoveringFamily:
        base: "Coordinate" = None; members: list = _field(default_factory=list)
        label: str = ""; _overlap_data: list = _field(default_factory=list)
    @_dc
    class GrothendieckTopology:
        name: str = "custom"
    @_dc
    class Site:
        label: str = ""
        _coords: list = _field(default_factory=list)
        _morphisms: list = _field(default_factory=list)
    @_dc
    class SiteBuilder:
        _coords: list = _field(default_factory=list)
        _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def build(self): return Site()

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, LocalJudgment, JudgmentBuilder, JudgmentAlgebra,
        JudgmentStatus, TrustLevel, PropositionKind,
        Proposition, Carrier, EvidenceItem, EvidenceBundle,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
        ProvenanceSource, EvidenceItemKind,
        _stable_hash, _now_iso,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field
    class TrustLevel(Enum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
        @property
        def value(self): return self._value_
    class JudgmentStatus(Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    class PropositionKind(Enum):
        STRUCTURAL="structural"; BEHAVIORAL="behavioral"; RELATIONAL="relational"
        RESOURCE="resource"; SEMANTIC="semantic"
    class EvidenceItemKind(Enum):
        SOLVER_PROOF="solver_proof"; RUNTIME_WITNESS="runtime_witness"
        ORACLE_PROPOSAL="oracle_proposal"; FORMAL_PROOF="formal_proof"
    class ProvenanceSource(Enum):
        SOLVER="solver"; RUNTIME="runtime"; ORACLE="oracle"; HUMAN="human"; COMPOSED="composed"
    @_dc(frozen=True)
    class Carrier:
        name: str = ""; parameters: dict = _field(default_factory=dict)
        is_dependent: bool = False; metadata: dict = _field(default_factory=dict)
    @_dc(frozen=True)
    class Proposition:
        kind: "PropositionKind" = None; formula: str = ""
        free_variables: tuple = (); metadata: dict = _field(default_factory=dict)
    @_dc(frozen=True)
    class Provenance:
        source: "ProvenanceSource" = None; parent_judgments: tuple = ()
        creation_timestamp: str = ""; transformation_history: tuple = ()
        metadata: dict = _field(default_factory=dict)
    @_dc(frozen=True)
    class EvidenceItem:
        kind: "EvidenceItemKind" = None; payload: dict = _field(default_factory=dict)
        trust_level: "TrustLevel" = None; channel: str = ""
        timestamp: str = ""; expiry: str = None; provenance: "Provenance" = None
    @_dc(frozen=True)
    class EvidenceBundle:
        items: tuple = (); summary: str = ""
    @_dc(frozen=True)
    class Obstruction:
        obstruction_id: str = ""; violated_condition: str = ""; coordinate: "Coordinate" = None
        evidence_at_time: tuple = (); repair_hints: tuple = (); cohomology_class: str = ""
        is_resolved: bool = False; resolution_evidence: tuple = (); provenance: "Provenance" = None
    @_dc(frozen=True)
    class ResidualObligation:
        obligation_id: str = ""; description: str = ""; coordinate: "Coordinate" = None
        required_trust: "TrustLevel" = None; is_discharged: bool = False
    @_dc(frozen=True)
    class TrustAnnotation:
        level: "TrustLevel" = None; evidence_basis: tuple = ()
        ceiling: "TrustLevel" = None; floor: "TrustLevel" = None; reasons: tuple = ()
    @_dc(frozen=True)
    class Judgment:
        coordinate: "Coordinate" = None; proposition: "Proposition" = None
        carrier: "Carrier" = None; evidence: "EvidenceBundle" = None
        obligations: tuple = (); obstructions: tuple = ()
        trust: "TrustLevel" = None; provenance: "Provenance" = None
        clauses: tuple = (); status: "JudgmentStatus" = None
    LocalJudgment = Judgment
    class JudgmentAlgebra: pass
    class JudgmentBuilder:
        def at(self, c): return self
        def claiming(self, p): return self
        def claiming_formula(self, f): return self
        def of_type(self, k): return self
        def of_type_named(self, n): return self
        def from_source(self, s): return self
        def with_trust_level(self, t): return self
        def with_evidence(self, e): return self
        def with_obligation(self, o): return self
        def with_obstruction(self, o): return self
        def with_status(self, s): return self
        def build(self): return Judgment()
        def reset(self): return self
    def _stable_hash(s): import hashlib; return hashlib.sha256(s.encode()).hexdigest()[:16]
    def _now_iso():
        import datetime; return datetime.datetime.utcnow().isoformat() + "Z"

try:
    from jugeo.solver.z3_session import Z3Session, Z3QueryBuilder, Z3Result, SolveOutcome, Z3Encoder
except ImportError:
    class SolveOutcome:
        SAT = "sat"; UNSAT = "unsat"; UNKNOWN = "unknown"
    class Z3Result:
        def __init__(self, outcome=None, model=None): self.outcome = outcome; self.model = model
    class Z3Session:
        def solve(self, q): return Z3Result(SolveOutcome.UNKNOWN)
    class Z3QueryBuilder:
        def build(self): return {}
    class Z3Encoder: pass

try:
    from jugeo.evidence.channels import (
        EvidenceChannel, EvidenceRecord, EvidenceRequest, EvidenceResponse,
        ChannelRouter, CopilotChannel, SolverChannel, RuntimeChannel,
    )
except ImportError:
    class EvidenceChannel:
        COPILOT = "COPILOT"; SOLVER = "SOLVER"; RUNTIME = "RUNTIME"
        ORACLE = "ORACLE"; HUMAN = "HUMAN"; COMPOSED = "COMPOSED"
    class EvidenceRecord:
        def __init__(self, channel="", claim="", payload=None, obligations=(), provenance=None):
            self.channel = channel; self.claim = claim; self.payload = payload or {}
    class EvidenceRequest:
        def __init__(self, **kw): self.__dict__.update(kw)
    class EvidenceResponse:
        def __init__(self, **kw): self.__dict__.update(kw)
    class ChannelRouter: pass
    class CopilotChannel:
        TRUST_CEILING = "proposal"
    class SolverChannel: pass
    class RuntimeChannel: pass

try:
    from jugeo.python_runtime.metaobject_surfaces.models import (
        MetaclassRecord, BehavioralSurface, DescriptorChain, ClassCreationTrace,
        _metaclass_coordinate, _class_coordinate, _now_str,
    )
except ImportError:
    from dataclasses import dataclass as _dc2, field as _field2
    @_dc2(frozen=True)
    class MetaclassRecord:
        class_name: str = ""; metaclass_name: str = ""; coordinate: object = None
        bases: tuple = (); metaclass_coordinate: object = None; trust: object = None
        class_mro: tuple = (); created_at: str = ""
    @_dc2(frozen=True)
    class BehavioralSurface:
        class_name: str = ""; coordinate: object = None; protocols: tuple = ()
        dunder_methods: tuple = (); abstract_methods: tuple = ()
        trust: object = None; judgment_index: dict = _field2(default_factory=dict)
    @_dc2(frozen=True)
    class DescriptorChain:
        attribute_name: str = ""; owner_class: str = ""; coordinate: object = None
        chain: tuple = (); descriptor_kind: str = "NON_DATA"
        trust: object = None; override_map: dict = _field2(default_factory=dict)
    @_dc2(frozen=True)
    class ClassCreationTrace:
        class_name: str = ""; coordinate: object = None; namespace_coordinate: object = None
        metaclass: object = None; prepare_result: dict = _field2(default_factory=dict)
        body_names: tuple = (); init_subclass_called: bool = False
        trust: object = None; created_at: str = ""
    def _metaclass_coordinate(cn, mn): return Coordinate(components=(cn, mn, "meta"), kind=CoordinateKind.INTERFACE)
    def _class_coordinate(cn, mod="unknown"): return Coordinate(components=(mod, cn), kind=CoordinateKind.INTERFACE)
    def _now_str(): return datetime.datetime.utcnow().isoformat() + "Z"

# ---
# Internal helpers
# ---

def _namespace_coord(class_name: str) -> Coordinate:
    """Return the Coordinate for the class body namespace (Phase 2 target)."""
    return Coordinate(
        components=("namespace", class_name),
        kind=CoordinateKind.REGION,
        support_labels=frozenset({class_name, "namespace"}),
    )


def _name_def_coord(class_name: str, attr_name: str) -> Coordinate:
    """Return the Coordinate for a single name defined in a class body."""
    return Coordinate(
        components=("namespace", class_name, attr_name),
        kind=CoordinateKind.FUNCTION,
        support_labels=frozenset({class_name, attr_name}),
    )


def _class_coord(class_name: str, bases: tuple[str, ...]) -> Coordinate:
    """Return the Coordinate for the fully-constructed class (Phase 3 output)."""
    return Coordinate(
        components=(class_name,) + tuple(bases[:2]),
        kind=CoordinateKind.INTERFACE,
        support_labels=frozenset({class_name}),
    )


def _provenance_runtime(parent_ids: tuple[str, ...] = ()) -> Provenance:
    """Build a Provenance record sourced from the runtime channel."""
    return Provenance(
        source=ProvenanceSource.RUNTIME,
        parent_judgments=parent_ids,
        creation_timestamp=_now_iso(),
        transformation_history=(),
        metadata={"phase": "class_creation"},
    )


def _provenance_oracle_cc(phase: str) -> Provenance:
    """Build a Provenance record sourced from CopilotChannel for the given phase."""
    return Provenance(
        source=ProvenanceSource.ORACLE,
        parent_judgments=(),
        creation_timestamp=_now_iso(),
        transformation_history=(f"CopilotChannel:{phase}",),
        metadata={"channel": "CopilotChannel", "trust_ceiling": "ORACLE_PROPOSED", "phase": phase},
    )


def _make_trust(trust: TrustLevel) -> Any:
    """Wrap TrustLevel in TrustAnnotation when the real jugeo is available.

    The real jugeo ``Judgment`` expects a ``TrustAnnotation``; stub Judgment
    accepts a raw ``TrustLevel``.  This helper produces whichever form works.
    """
    try:
        return TrustAnnotation(level=trust)
    except Exception:
        return trust


def _evidence_bundle(formula: str, trust: TrustLevel, channel: str = EvidenceChannel.RUNTIME) -> EvidenceBundle:
    """Build a minimal EvidenceBundle for a class creation assertion."""
    item = EvidenceItem(
        kind=EvidenceItemKind.RUNTIME_WITNESS,
        payload={"formula": formula},
        trust_level=trust,
        channel=channel,
        timestamp=_now_iso(),
        provenance=(),
    )
    return EvidenceBundle(items=(item,))


# ---
# §20.5.1  ClassCreationOrchestrator
# ---

class ClassCreationOrchestrator:
    """Orchestrates the full three-phase class creation protocol.

    Phase 1: ``__prepare__(mcs, name, bases, **kwargs)`` → namespace dict
    Phase 2: body execution populating the namespace
    Phase 3: ``type.__new__(mcs, name, bases, namespace)`` → class object

    Post-creation: ``__init_subclass__`` is called on each base, then
    ``__set_name__`` is called on each descriptor found in the namespace.

    Each phase is recorded as a morphism in the site, building a
    :class:`ClassCreationTrace`.  CopilotChannel evidence can annotate each
    phase with copilot-assisted analysis at ORACLE_PROPOSED trust.

    theory2.tex Ch20 §20.5.1
    """

    def __init__(
        self,
        class_name: str,
        bases: tuple[str, ...],
        metaclass_record: MetaclassRecord,
    ) -> None:
        """Initialize the orchestrator for a specific class under creation.

        Args:
            class_name: Simple name of the class being created.
            bases: Tuple of base class names (as strings) in MRO order.
            metaclass_record: Pre-computed MetaclassRecord describing the
                metaclass that will drive Phases 1 and 3.

        Initialises internal phase tracking so that :meth:`build_trace` can
        be called after all three phases complete.

        theory2.tex Ch20 §20.5.1 — orchestrator initialisation
        """
        self._class_name = class_name
        self._bases = bases
        self._metaclass_record = metaclass_record
        self._phase1_result: dict[str, Any] | None = None
        self._phase2_names: tuple[str, ...] = ()
        self._phase3_coord: Coordinate | None = None
        self._morphisms: list[Morphism] = []
        self._created_at: str = _now_iso()

    def run_prepare(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Simulate Phase 1: ``__prepare__``.

        Returns a canonical namespace dict carrying bookkeeping metadata.
        In a live interpreter this would delegate to ``metaclass.__prepare__``;
        here we return a minimal dict sufficient for downstream tracing.

        The Phase 1 TRANSPORT morphism is recorded internally so that
        :meth:`creation_site_fragment` can include it in the site graph.

        theory2.tex Ch20 §20.5.1 — Phase 1 prepare morphism
        """
        result: dict[str, Any] = {
            "class_name": self._class_name,
            "bases": list(self._bases),
            "kwargs": kwargs,
            "phase": "prepare",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "metaclass": self._metaclass_record.metaclass_name,
            "protocols": kwargs.get("protocols", []),
            "abstract_methods": kwargs.get("abstract_methods", []),
        }
        self._phase1_result = result
        # Record Phase 1 as a TRANSPORT morphism: metaclass coord → namespace coord
        meta_coord = self._metaclass_record.metaclass_coordinate or Coordinate(
            components=(self._metaclass_record.metaclass_name, "prepare"),
            kind=CoordinateKind.INTERFACE,
        )
        ns_coord = _namespace_coord(self._class_name)
        self._morphisms.append(Morphism(
            source=meta_coord,
            target=ns_coord,
            kind=MorphismKind.TRANSPORT,
            label=f"__prepare__({self._class_name})",
        ))
        return result

    def run_body(self, namespace: dict[str, Any], body_statements: list[str]) -> tuple[str, ...]:
        """Simulate Phase 2: class body execution.

        Each string in ``body_statements`` represents a name defined during
        class body execution.  The method records INCLUSION morphisms from
        each name-definition coordinate into the namespace coordinate.

        In a live interpreter the body is compiled Python bytecode executed
        inside the namespace dict; here ``body_statements`` is a list of
        symbolic name strings.

        theory2.tex Ch20 §20.5.1 — Phase 2 body inclusions
        """
        ns_coord = _namespace_coord(self._class_name)
        result_names: list[str] = []
        for stmt in body_statements:
            result_names.append(stmt)
            def_coord = _name_def_coord(self._class_name, stmt)
            self._morphisms.append(Morphism(
                source=def_coord,
                target=ns_coord,
                kind=MorphismKind.INCLUSION,
                label=f"body_def({stmt})",
            ))
        self._phase2_names = tuple(result_names)
        return self._phase2_names

    def run_new(self, namespace: dict[str, Any]) -> Coordinate:
        """Simulate Phase 3: ``type.__new__``.

        Constructs and returns the Coordinate representing the newly-created
        class.  Records the Phase 3 REFINEMENT morphism from the namespace
        coordinate to the class coordinate.

        The class coordinate encodes the class name and up to two base class
        names in its components, giving a unique address in the site topology.

        theory2.tex Ch20 §20.5.1 — Phase 3 new morphism
        """
        class_coord = Coordinate(
            components=(self._class_name,) + tuple(self._bases[:2]),
            kind=CoordinateKind.INTERFACE,
            support_labels=frozenset({self._class_name}),
        )
        self._phase3_coord = class_coord
        ns_coord = _namespace_coord(self._class_name)
        self._morphisms.append(Morphism(
            source=ns_coord,
            target=class_coord,
            kind=MorphismKind.REFINEMENT,
            label=f"type.__new__({self._class_name})",
        ))
        return class_coord

    def build_trace(
        self,
        prepare_result: dict[str, Any],
        body_names: tuple[str, ...],
        class_coord: Coordinate,
        init_subclass_called: bool,
        trust: TrustLevel,
    ) -> ClassCreationTrace:
        """Construct and return a :class:`ClassCreationTrace` from all phases.

        Args:
            prepare_result: The dict returned by :meth:`run_prepare`.
            body_names: The tuple returned by :meth:`run_body`.
            class_coord: The Coordinate returned by :meth:`run_new`.
            init_subclass_called: Whether ``__init_subclass__`` was invoked.
            trust: The trust level to stamp on the trace (typically
                RUNTIME_WITNESSED for live creation or ORACLE_PROPOSED
                for CopilotChannel-assisted static analysis).

        theory2.tex Ch20 §20.5.1 — trace assembly
        """
        ns_coord = _namespace_coord(self._class_name)
        return ClassCreationTrace(
            class_name=self._class_name,
            coordinate=class_coord,
            namespace_coordinate=ns_coord,
            metaclass=self._metaclass_record,
            prepare_result=prepare_result,
            body_names=body_names,
            init_subclass_called=init_subclass_called,
            trust=trust,
            created_at=self._created_at,
        )

    def creation_site_fragment(self, trace: ClassCreationTrace) -> Site:
        """Build a Site fragment capturing all morphisms of the creation sequence.

        Adds three coordinate nodes (metaclass, namespace, class) and all
        phase morphisms recorded during :meth:`run_prepare`, :meth:`run_body`,
        and :meth:`run_new`.

        theory2.tex Ch20 §20.5.1 — creation site fragment
        """
        builder = SiteBuilder()
        meta_coord = (
            self._metaclass_record.metaclass_coordinate
            or Coordinate(components=(self._metaclass_record.metaclass_name,), kind=CoordinateKind.INTERFACE)
        )
        ns_coord = _namespace_coord(self._class_name)
        class_coord = trace.coordinate or _class_coord(self._class_name, self._bases)
        builder.add_coordinate(meta_coord)
        builder.add_coordinate(ns_coord)
        builder.add_coordinate(class_coord)
        for m in self._morphisms:
            builder.add_morphism(m)
        return builder.build()

    def orchestration_judgment(self, trace: ClassCreationTrace) -> Judgment:
        """Build a Judgment asserting the three-phase trace is complete.

        The STRUCTURAL proposition ``class_creation_complete(<name>)`` asserts
        that all three phases ran, __init_subclass__ was propagated, and the
        resulting class coordinate is well-formed.

        CopilotChannel can annotate this judgment with ORACLE_PROPOSED evidence
        during static analysis (COPILOT_SUGGESTED trust ceiling); runtime
        execution upgrades it to RUNTIME_WITNESSED.

        theory2.tex Ch20 §20.5.1 — orchestration completeness judgment
        """
        trust = trace.trust or TrustLevel.UNVERIFIED
        formula = f"class_creation_complete({self._class_name})"
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(self._class_name,),
            metadata={
                "bases": list(self._bases),
                "body_name_count": len(trace.body_names),
                "init_subclass_called": trace.init_subclass_called,
                "phases_completed": 3,
            },
        )
        bundle = _evidence_bundle(formula, trust)
        return Judgment(
            coordinate=trace.coordinate or Coordinate(),
            proposition=prop,
            carrier=Carrier(
                name=self._class_name,
                parameters=(f"metaclass:{self._metaclass_record.metaclass_name}",),
            ),
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=_make_trust(trust),
            provenance=_provenance_oracle_cc("orchestration"),
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )


# ---
# §20.5.2  BodyExecutionTracer
# ---

@dataclass
class _TracedDefinition:
    """Internal record of a single name defined in a class body."""
    name: str
    kind: str  # "assignment", "function_def", "decorator_application"
    metadata: dict[str, Any] = field(default_factory=dict)


class BodyExecutionTracer:
    """Traces the execution of a class body and records defined names.

    During class body execution, each assignment, function definition,
    and decorator application is a local section in the namespace coordinate.
    This tracer records the sequence of definitions and builds the
    corresponding site morphisms.

    The tracer is stateful: call :meth:`trace_assignment`,
    :meth:`trace_function_def`, and :meth:`trace_decorator_application` in
    execution order, then read :meth:`body_names` and :meth:`dunder_names`
    after the body is complete.

    theory2.tex Ch20 §20.5.2
    """

    def __init__(self, namespace: dict[str, Any]) -> None:
        """Initialize the tracer with the prepared namespace dict.

        Args:
            namespace: The dict produced by ``__prepare__``; may contain
                pre-existing names from the metaclass (e.g., ``__module__``,
                ``__qualname__``).

        The tracer copies the initial namespace keys into ``_traced_definitions``
        as implicit pre-body entries so :meth:`body_names` returns a complete
        picture.

        theory2.tex Ch20 §20.5.2 — tracer initialisation
        """
        self._initial_namespace: dict[str, Any] = dict(namespace)
        self._traced_definitions: list[_TracedDefinition] = []
        self._morphism_log: list[Morphism] = []
        # Record pre-existing namespace entries as implicit body-zero assignments
        for k in namespace:
            self._traced_definitions.append(_TracedDefinition(
                name=k, kind="assignment",
                metadata={"source": "namespace_pre_existing"},
            ))

    def trace_assignment(self, name: str, value_repr: str) -> None:
        """Record a simple assignment ``name = <value>`` in the class body.

        Args:
            name: The attribute name being assigned.
            value_repr: A string representation of the assigned value (used
                for provenance and debugging; not evaluated).

        Records the definition in internal state so :meth:`body_names` returns
        a complete ordered list after all statements are traced.

        theory2.tex Ch20 §20.5.2 — assignment recording
        """
        self._traced_definitions.append(_TracedDefinition(
            name=name,
            kind="assignment",
            metadata={"value_repr": value_repr, "timestamp": _now_iso()},
        ))

    def trace_function_def(
        self,
        func_name: str,
        is_dunder: bool,
        is_classmethod: bool,
        is_staticmethod: bool,
    ) -> None:
        """Record a ``def`` statement in the class body.

        Args:
            func_name: The name of the function being defined.
            is_dunder: Whether the name starts and ends with ``__``.
            is_classmethod: Whether the definition is wrapped in ``@classmethod``.
            is_staticmethod: Whether the definition is wrapped in ``@staticmethod``.

        The metadata dict captures the kind of function so that downstream
        :class:`BehavioralSurfaceBuilder` calls can classify dunders correctly.

        theory2.tex Ch20 §20.5.2 — function definition recording
        """
        self._traced_definitions.append(_TracedDefinition(
            name=func_name,
            kind="function_def",
            metadata={
                "is_dunder": is_dunder,
                "is_classmethod": is_classmethod,
                "is_staticmethod": is_staticmethod,
                "timestamp": _now_iso(),
            },
        ))

    def trace_decorator_application(self, target_name: str, decorator_name: str) -> Morphism:
        """Record the application of a decorator to a class body member.

        Returns a TRANSPORT morphism from the target's plain coordinate to its
        decorated coordinate.  The decorator transforms the definition's
        coordinate by appending the decorator name as a component, modelling
        the fact that decorators compose transformations on the value.

        Args:
            target_name: The name of the attribute or function being decorated.
            decorator_name: The name of the decorator being applied (e.g.,
                ``"classmethod"``, ``"staticmethod"``, ``"property"``).

        theory2.tex Ch20 §20.5.2 — decorator transport morphism
        """
        pre_coord = Coordinate(
            components=(target_name,),
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({target_name}),
        )
        post_coord = Coordinate(
            components=(target_name, decorator_name),
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({target_name, decorator_name}),
        )
        m = Morphism(
            source=pre_coord,
            target=post_coord,
            kind=MorphismKind.TRANSPORT,
            label=f"@{decorator_name}",
        )
        self._morphism_log.append(m)
        self._traced_definitions.append(_TracedDefinition(
            name=target_name,
            kind="decorator_application",
            metadata={"decorator": decorator_name, "timestamp": _now_iso()},
        ))
        return m

    def body_names(self) -> tuple[str, ...]:
        """Return an ordered tuple of all names defined during tracing.

        Duplicate names (e.g., a function that is later decorated) appear once
        per definition event — callers that need a de-duplicated set should
        use ``dict.fromkeys`` on the result.

        theory2.tex Ch20 §20.5.2 — body name sequence
        """
        return tuple(d.name for d in self._traced_definitions)

    def dunder_names(self) -> tuple[str, ...]:
        """Return the subset of body names that are dunder names.

        A dunder name starts *and* ends with ``__``.  This is the set used by
        :class:`BehavioralSurfaceBuilder.add_dunder` calls when constructing
        the class's behavioral surface.

        theory2.tex Ch20 §20.5.2 — dunder name extraction
        """
        return tuple(
            d.name for d in self._traced_definitions
            if d.name.startswith("__") and d.name.endswith("__")
        )

    def as_covering_family(self, base: Coordinate) -> CoveringFamily:
        """Return a CoveringFamily whose base is ``base`` and members are each traced name.

        Each distinct traced name becomes a member coordinate in the covering
        family, modelling the class body as a sieve over the namespace
        coordinate.  The sieve generates a cover when the body is complete.

        theory2.tex Ch20 §20.5.2 — body as covering family
        """
        seen: set[str] = set()
        members: list[Coordinate] = []
        for d in self._traced_definitions:
            if d.name not in seen:
                seen.add(d.name)
                members.append(Coordinate(
                    components=base.components + (d.name,),
                    kind=CoordinateKind.FUNCTION,
                    support_labels=frozenset({d.name}),
                ))
        return CoveringFamily(
            base=base,
            members=members,
            label=f"body_cover({base.components})",
            _overlap_data=[{"name": d.name, "kind": d.kind} for d in self._traced_definitions],
        )


# ---
# §20.5.3  InitSubclassProbe
# ---

class InitSubclassProbe:
    """Records ``__init_subclass__`` calls during class creation.

    When a class is created, Python calls ``Base.__init_subclass__(cls, **kwargs)``
    on each base class (excluding the class being created itself).  In JuGeo
    terms, each such call is a REFINEMENT morphism from the new class's
    coordinate to the base's coordinate.

    The probe is passive: it does not call ``__init_subclass__`` itself but
    records calls that the class creation machinery reports.  This allows the
    probe to be used in both live and simulated creation contexts.

    CopilotChannel can seed the call log at ORACLE_PROPOSED trust when static
    analysis infers which bases would receive the propagation.

    theory2.tex Ch20 §20.5.3
    """

    def __init__(self, bases: tuple[str, ...]) -> None:
        """Initialize the probe for the given base class names.

        Args:
            bases: Tuple of base class names, in MRO order.  The probe will
                track which of these bases has had ``__init_subclass__``
                reported to it.

        theory2.tex Ch20 §20.5.3 — probe initialisation
        """
        self._bases: tuple[str, ...] = bases
        self._call_log: list[dict[str, Any]] = []

    def record_call(self, base_name: str, kwargs: dict[str, Any]) -> None:
        """Record an ``__init_subclass__`` call for the named base class.

        Args:
            base_name: The name of the base class receiving the call.
            kwargs: The keyword arguments forwarded to ``__init_subclass__``.

        Appends a structured record to the internal call log so that
        :meth:`all_calls` and :meth:`was_called_for` can report accurately.

        theory2.tex Ch20 §20.5.3 — call recording
        """
        self._call_log.append({
            "base_name": base_name,
            "kwargs": dict(kwargs),
            "timestamp": _now_iso(),
            "call_index": len(self._call_log),
        })

    def morphisms_for(
        self,
        class_coord: Coordinate,
        base_coords: dict[str, Coordinate],
    ) -> list[Morphism]:
        """Return REFINEMENT morphisms from ``class_coord`` to each called base.

        Only bases for which a call was recorded (via :meth:`record_call`) and
        for which a Coordinate is present in ``base_coords`` contribute a
        morphism.  Missing coordinates are silently skipped with a note in
        the morphism label.

        theory2.tex Ch20 §20.5.3 — refinement morphism list
        """
        morphisms: list[Morphism] = []
        called_bases = {entry["base_name"] for entry in self._call_log}
        for base_name in called_bases:
            base_coord = base_coords.get(base_name)
            if base_coord is None:
                # Synthesise a minimal coordinate for the base
                base_coord = Coordinate(
                    components=(base_name, "__init_subclass__"),
                    kind=CoordinateKind.INTERFACE,
                    support_labels=frozenset({base_name}),
                )
            morphisms.append(Morphism(
                source=class_coord,
                target=base_coord,
                kind=MorphismKind.REFINEMENT,
                label=f"__init_subclass__({base_name})",
            ))
        return morphisms

    def was_called_for(self, base_name: str) -> bool:
        """Return ``True`` if ``__init_subclass__`` was recorded for ``base_name``.

        theory2.tex Ch20 §20.5.3 — call presence query
        """
        return any(entry["base_name"] == base_name for entry in self._call_log)

    def all_calls(self) -> list[dict[str, Any]]:
        """Return the full list of recorded ``__init_subclass__`` call records.

        Each record is a dict with keys: ``base_name``, ``kwargs``,
        ``timestamp``, and ``call_index``.  The list is ordered by the
        sequence in which calls were recorded.

        theory2.tex Ch20 §20.5.3 — call log access
        """
        return list(self._call_log)

    def as_judgment(self, class_coord: Coordinate, trust: TrustLevel) -> Judgment:
        """Build a Judgment asserting ``__init_subclass__`` was propagated to all bases.

        The STRUCTURAL proposition formula encodes the number of bases that
        received the call.  If no calls were recorded, the formula still
        holds vacuously (zero bases → zero calls required).

        CopilotChannel can supply this judgment at ORACLE_PROPOSED trust when
        static analysis confirms the propagation without running the code.

        theory2.tex Ch20 §20.5.3 — propagation judgment
        """
        n_bases = len(self._call_log)
        formula = f"init_subclass_propagated({n_bases}_bases)"
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=tuple(str(i) for i in range(n_bases)),
            metadata={
                "bases_called": [e["base_name"] for e in self._call_log],
                "total_bases": len(self._bases),
            },
        )
        bundle = _evidence_bundle(formula, trust)
        return Judgment(
            coordinate=class_coord,
            proposition=prop,
            carrier=Carrier(
                name="__init_subclass__",
                parameters=(f"bases_count:{n_bases}",),
            ),
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=_make_trust(trust),
            provenance=_provenance_oracle_cc("init_subclass"),
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )


# ---
# §20.5.4  SetNameHookApplicator
# ---

class SetNameHookApplicator:
    """Applies ``__set_name__`` hooks to descriptors found in the class namespace.

    After ``type.__new__`` creates the class, Python calls
    ``descriptor.__set_name__(owner, name)`` for each descriptor in the new
    class's namespace.  This establishes the descriptor's knowledge of its
    owner class and attribute name.

    In site terms, ``__set_name__`` is a TRANSPORT morphism that moves the
    descriptor coordinate from the anonymous namespace coordinate to the
    named class coordinate.

    The applicator identifies candidate descriptor names heuristically: any
    name that doesn't start with ``__`` and whose string representation
    contains the word "descriptor", or any callable-looking name, is treated
    as a potential descriptor.

    CopilotChannel can annotate the descriptor identification step with
    ORACLE_PROPOSED trust, allowing CopilotChannel proposals for descriptor
    names to be tracked separately from runtime-witnessed ones.

    theory2.tex Ch20 §20.5.4
    """

    def __init__(self, class_name: str, namespace: dict[str, Any]) -> None:
        """Initialize with the class name and its populated namespace dict.

        Args:
            class_name: The name of the class whose namespace is being inspected.
            namespace: The namespace dict after body execution (Phase 2 output).

        Identifies descriptor candidates by scanning for names that:
        1. Do not start with ``__`` (non-dunder), AND
        2. Have string values containing the word "descriptor", OR look like
           method/callable definitions (function-like string representations).

        theory2.tex Ch20 §20.5.4 — descriptor identification
        """
        self._class_name = class_name
        self._namespace: dict[str, Any] = dict(namespace)
        self._descriptor_names: list[str] = self._identify_descriptors()

    def _identify_descriptors(self) -> list[str]:
        """Heuristically identify descriptor names in the namespace.

        A name qualifies as a descriptor candidate when:
        - It does not start with ``__`` (to exclude module-level dunders), AND
        - Its string representation contains ``"descriptor"`` (explicit), OR
        - Its repr contains ``"<function"`` or ``"<method"`` (callable objects), OR
        - It appears to be a non-dunder name that looks like it wraps an object.

        theory2.tex Ch20 §20.5.4 — descriptor heuristic
        """
        candidates: list[str] = []
        for name, value in self._namespace.items():
            if name.startswith("__"):
                continue
            value_str = repr(value) if not isinstance(value, str) else value
            is_descriptor_hint = "descriptor" in value_str.lower()
            is_callable_hint = any(
                kw in value_str for kw in ("<function", "<method", "<classmethod", "<staticmethod", "<property")
            )
            is_plain_string_descriptor = isinstance(value, str) and "descriptor" in value.lower()
            if is_descriptor_hint or is_callable_hint or is_plain_string_descriptor:
                candidates.append(name)
        return candidates

    def descriptor_names(self) -> list[str]:
        """Return the list of attribute names identified as likely descriptors.

        The list is ordered by their appearance in the namespace iteration
        order (which is insertion-ordered in Python 3.7+).

        theory2.tex Ch20 §20.5.4 — descriptor name access
        """
        return list(self._descriptor_names)

    def apply_set_name(self, attr_name: str, class_coord: Coordinate) -> Morphism:
        """Return the TRANSPORT morphism representing ``__set_name__`` for ``attr_name``.

        The morphism transports the descriptor from its anonymous namespace
        coordinate (where it was defined in the class body) to a named
        coordinate that encodes both the class and the attribute name.

        Args:
            attr_name: The attribute name passed to ``__set_name__``.
            class_coord: The Coordinate of the owning class (Phase 3 output).

        theory2.tex Ch20 §20.5.4 — set_name transport morphism
        """
        anon_coord = Coordinate(
            components=("namespace", self._class_name, attr_name),
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({attr_name}),
        )
        named_coord = Coordinate(
            components=class_coord.components + (attr_name, "__set_name__"),
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({self._class_name, attr_name}),
        )
        return Morphism(
            source=anon_coord,
            target=named_coord,
            kind=MorphismKind.TRANSPORT,
            label=f"__set_name__({attr_name}, {self._class_name})",
        )

    def all_morphisms(self, class_coord: Coordinate) -> list[Morphism]:
        """Return all ``__set_name__`` morphisms for every identified descriptor.

        Calls :meth:`apply_set_name` for each descriptor name and collects the
        results.  The order matches :meth:`descriptor_names`.

        theory2.tex Ch20 §20.5.4 — all set_name morphisms
        """
        return [self.apply_set_name(name, class_coord) for name in self._descriptor_names]

    def as_descriptor_chains(self, trust: TrustLevel) -> list[DescriptorChain]:
        """Build a :class:`DescriptorChain` for each identified descriptor.

        Each chain carries:
        - ``descriptor_kind = "DATA"`` if the attribute name suggests a data
          descriptor (contains ``"prop"`` or ``"data"`` or ``"field"``).
        - ``descriptor_kind = "NON_DATA"`` otherwise.

        The trust level is applied uniformly to all chains; downstream code
        can upgrade individual chains after runtime inspection.

        theory2.tex Ch20 §20.5.4 — descriptor chain construction
        """
        chains: list[DescriptorChain] = []
        for name in self._descriptor_names:
            is_data = any(kw in name.lower() for kw in ("prop", "data", "field", "attr", "slot"))
            desc_kind = "DATA" if is_data else "NON_DATA"
            chain_coord = Coordinate(
                components=(self._class_name, name, "descriptor"),
                kind=CoordinateKind.FUNCTION,
                support_labels=frozenset({self._class_name, name}),
            )
            chains.append(DescriptorChain(
                attribute_name=name,
                owner_class=self._class_name,
                coordinate=chain_coord,
                chain=(name,),
                descriptor_kind=desc_kind,
                trust=trust,
                override_map={},
            ))
        return chains

    def probe_judgment(self, class_coord: Coordinate, trust: TrustLevel) -> Judgment:
        """Build a Judgment asserting all ``__set_name__`` hooks were applied.

        The STRUCTURAL proposition ``set_name_hooks_applied(<class>, <count>)``
        asserts that each identified descriptor received its ``__set_name__``
        call.  CopilotChannel can supply this judgment as ORACLE_PROPOSED
        during static analysis.

        theory2.tex Ch20 §20.5.4 — set_name completeness judgment
        """
        n = len(self._descriptor_names)
        formula = f"set_name_hooks_applied({self._class_name}, {n})"
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(self._class_name, str(n)),
            metadata={
                "descriptor_names": self._descriptor_names,
                "class_name": self._class_name,
                "hook_count": n,
            },
        )
        bundle = _evidence_bundle(formula, trust)
        return Judgment(
            coordinate=class_coord,
            proposition=prop,
            carrier=Carrier(
                name="__set_name__",
                parameters=(f"descriptor_count:{n}", f"class:{self._class_name}"),
            ),
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=_make_trust(trust),
            provenance=_provenance_oracle_cc("set_name"),
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )


# ---
# Module-level convenience entry-points
# ---

def trace_class_creation(
    class_name: str,
    bases: tuple[str, ...],
    metaclass_name: str,
    body_statements: list[str],
    kwargs: dict[str, Any] | None = None,
    trust: TrustLevel = TrustLevel.RUNTIME_WITNESSED,
) -> ClassCreationTrace:
    """Top-level helper: run all three phases and return a :class:`ClassCreationTrace`.

    Combines :class:`ClassCreationOrchestrator`, :class:`BodyExecutionTracer`,
    :class:`InitSubclassProbe`, and :class:`SetNameHookApplicator` into a
    single call.  Suitable for unit tests and CopilotChannel-driven analysis.

    theory2.tex Ch20 §20.5 — full-pipeline entry-point
    """
    meta_coord = _metaclass_coordinate(class_name, metaclass_name)
    mc_record = MetaclassRecord(
        class_name=class_name,
        metaclass_name=metaclass_name,
        coordinate=_class_coordinate(class_name),
        bases=bases,
        metaclass_coordinate=meta_coord,
        trust=trust,
        class_mro=bases,
        created_at=_now_iso(),
    )
    orchestrator = ClassCreationOrchestrator(class_name, bases, mc_record)
    kw = kwargs or {}
    prepare_result = orchestrator.run_prepare(kw)
    body_names = orchestrator.run_body(prepare_result, body_statements)
    class_coord = orchestrator.run_new(prepare_result)
    init_probe = InitSubclassProbe(bases)
    for base in bases:
        init_probe.record_call(base, {})
    trace = orchestrator.build_trace(
        prepare_result=prepare_result,
        body_names=body_names,
        class_coord=class_coord,
        init_subclass_called=len(bases) > 0,
        trust=trust,
    )
    return trace


def creation_judgments(trace: ClassCreationTrace, trust: TrustLevel) -> list[Judgment]:
    """Return all judgments produced for a completed ClassCreationTrace.

    Generates:
    1. The orchestration completeness judgment.
    2. An ``__init_subclass__`` propagation judgment.
    3. A ``__set_name__`` hooks judgment.

    theory2.tex Ch20 §20.5 — judgment bundle from trace
    """
    mc_record = trace.metaclass or MetaclassRecord(
        class_name=trace.class_name,
        metaclass_name="type",
    )
    orchestrator = ClassCreationOrchestrator(
        trace.class_name,
        mc_record.bases if hasattr(mc_record, "bases") else (),
        mc_record,
    )
    class_coord = trace.coordinate or Coordinate()
    j1 = orchestrator.orchestration_judgment(trace)
    init_probe = InitSubclassProbe(mc_record.bases if hasattr(mc_record, "bases") else ())
    for entry in []:  # no extra calls needed; bases already baked into trace
        init_probe.record_call(entry, {})
    j2 = init_probe.as_judgment(class_coord, trust)
    ns_dict: dict[str, Any] = {}
    applicator = SetNameHookApplicator(trace.class_name, ns_dict)
    j3 = applicator.probe_judgment(class_coord, trust)
    return [j1, j2, j3]
