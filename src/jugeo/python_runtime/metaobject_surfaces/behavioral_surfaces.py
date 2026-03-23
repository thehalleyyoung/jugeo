from __future__ import annotations

r"""
theory2.tex Ch20 §20.3 — Behavioral surfaces as judgment-indexed protocol specifications.

A *behavioral surface* is the observable face of a Python class: the collection
of dunder methods, abstract methods, and protocol memberships that other objects
may rely on.  In JuGeo's site-theoretic framework a behavioral surface is a
covering family whose base is the class coordinate and whose members are the
individual method coordinates.

§20.3.1  Protocol surfaces and __subclasshook__ as covering axiom
§20.3.2  Structural vs nominal subtyping as different site topologies
§20.3.3  Incremental surface construction and build tracing
§20.3.4  Judgment-indexed protocols for fine-grained trust assignment

CopilotChannel evidence enters at COPILOT_SUGGESTED / ORACLE_PROPOSED trust and
must be explicitly promoted before any structural subtype claim is settled.
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

def _method_coord(protocol_name: str, method_name: str) -> Coordinate:
    """Return the canonical Coordinate for a single protocol method slot."""
    return Coordinate(
        components=(protocol_name, method_name),
        kind=CoordinateKind.FUNCTION,
        support_labels=frozenset({protocol_name}),
    )


def _surface_coord(class_name: str) -> Coordinate:
    """Return the canonical Coordinate for a class's behavioral surface."""
    return Coordinate(
        components=("behavioral_surface", class_name),
        kind=CoordinateKind.INTERFACE,
        support_labels=frozenset({class_name}),
    )


def _provenance_oracle(parent_ids: tuple[str, ...] = ()) -> Provenance:
    """Build a Provenance record sourced from the oracle/copilot channel."""
    return Provenance(
        source=ProvenanceSource.ORACLE,
        parent_judgments=parent_ids,
        creation_timestamp=_now_iso(),
        transformation_history=(),
        metadata={"channel": "CopilotChannel", "trust_ceiling": "ORACLE_PROPOSED"},
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


def _evidence_bundle_for(formula: str, trust: TrustLevel) -> EvidenceBundle:
    """Build a minimal EvidenceBundle for a behavioral surface assertion."""
    item = EvidenceItem(
        kind=EvidenceItemKind.RUNTIME_WITNESS,
        payload={"formula": formula},
        trust_level=trust,
        channel=EvidenceChannel.RUNTIME,
        timestamp=_now_iso(),
        provenance=(),
    )
    return EvidenceBundle(items=(item,))


# ---
# §20.3.1  ProtocolSurfaceAnalyzer
# ---

class ProtocolSurfaceAnalyzer:
    """Analyzes a Protocol class and extracts its BehavioralSurface.

    A ``typing.Protocol`` defines a structural type by listing required methods
    and attributes.  In JuGeo's site topology, a Protocol is a covering family:
    any class whose coordinate can be "covered" by the protocol's method
    coordinates is a structural subtype.

    CopilotChannel-assisted protocol inference enters at ORACLE_PROPOSED trust
    and requires explicit promotion before structural subtype claims are settled.

    theory2.tex Ch20 §20.3.1
    """

    def __init__(
        self,
        protocol_name: str,
        required_methods: tuple[str, ...],
        runtime_checkable: bool = False,
    ) -> None:
        """Initialize the analyzer with the protocol's name and required methods.

        Args:
            protocol_name: The fully-qualified name of the Protocol class.
            required_methods: Tuple of method names that define the protocol.
            runtime_checkable: Whether ``@runtime_checkable`` is applied,
                enabling ``isinstance`` checks at the cost of a covering axiom.

        Sets up the internal coordinate for the protocol surface so that
        subsequent calls to :meth:`surface`, :meth:`covering_family`, and
        :meth:`subclasshook_morphism` all share the same base coordinate.
        """
        self.protocol_name = protocol_name
        self.required_methods = required_methods
        self.runtime_checkable = runtime_checkable
        self._coord = _surface_coord(protocol_name)
        self._created_at = _now_iso()

    def surface(self, trust: TrustLevel = TrustLevel.RUNTIME_WITNESSED) -> BehavioralSurface:
        """Build and return the BehavioralSurface for this protocol.

        Splits ``required_methods`` into dunders (those wrapped in double
        underscores) and abstract methods (the full set), then constructs a
        :class:`BehavioralSurface` stamped at the given trust level.

        The ``protocols`` tuple contains only the protocol's own name because
        a Protocol does not inherit other Protocols unless explicitly declared.

        theory2.tex Ch20 §20.3.1 — surface extraction
        """
        dunders = tuple(m for m in self.required_methods if m.startswith("__") and m.endswith("__"))
        return BehavioralSurface(
            class_name=self.protocol_name,
            coordinate=self._coord,
            protocols=(self.protocol_name,),
            dunder_methods=dunders,
            abstract_methods=self.required_methods,
            trust=trust,
            judgment_index={
                m: _stable_hash(f"{self.protocol_name}:{m}:{trust}")
                for m in self.required_methods
            },
        )

    def covering_family(self) -> CoveringFamily:
        """Build the CoveringFamily for this protocol.

        Each required method corresponds to a member coordinate in the
        covering family.  The base is the protocol surface coordinate and
        the members are the individual method coordinates.

        In Grothendieck topology terms, the protocol's covering axiom states
        that a sieve generated by the method coordinates covers the protocol
        coordinate.  ``__subclasshook__`` acts as the explicit covering map.

        theory2.tex Ch20 §20.3.1 — covering axiom
        """
        members = [
            _method_coord(self.protocol_name, m)
            for m in self.required_methods
        ]
        return CoveringFamily(
            base=self._coord,
            members=members,
            label=f"protocol_cover({self.protocol_name})",
            _overlap_data=[
                {"method": m, "coord_hash": _stable_hash(f"{self.protocol_name}:{m}")}
                for m in self.required_methods
            ],
        )

    def subclasshook_morphism(self) -> Morphism:
        """Return the TRANSPORT morphism representing ``__subclasshook__``.

        ``__subclasshook__`` is the protocol's mechanism for customizing
        ``issubclass`` checks.  In site terms it is a TRANSPORT morphism from
        the protocol surface coordinate to the ``__subclasshook__`` method
        coordinate, carrying structural subtype evidence.

        CopilotChannel can inject a proposal for what ``__subclasshook__``
        should return at ORACLE_PROPOSED trust; that proposal requires solver
        verification before being promoted to SOLVER_DISCHARGED.

        theory2.tex Ch20 §20.3.1 — subclasshook as covering axiom
        """
        hook_coord = Coordinate(
            components=(self.protocol_name, "__subclasshook__"),
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({self.protocol_name}),
        )
        return Morphism(
            source=self._coord,
            target=hook_coord,
            kind=MorphismKind.TRANSPORT,
            label="__subclasshook__",
        )

    def is_runtime_checkable(self) -> bool:
        """Return whether this protocol is decorated with ``@runtime_checkable``.

        A runtime-checkable protocol permits ``isinstance(obj, Proto)`` checks
        at the cost of only testing for method *presence*, not signatures.
        In JuGeo trust terms this corresponds to RUNTIME_WITNESSED rather than
        SOLVER_DISCHARGED — the check is fast but structurally incomplete.

        theory2.tex Ch20 §20.3.1 — runtime_checkable and trust ceiling
        """
        return self.runtime_checkable

    def missing_for(self, candidate_surface: BehavioralSurface) -> tuple[str, ...]:
        """Return the required methods absent from ``candidate_surface``.

        Computes the set difference between the protocol's required methods and
        the union of ``candidate_surface.dunder_methods`` and
        ``candidate_surface.abstract_methods``.  Any non-empty result is direct
        evidence for an :class:`Obstruction` on the structural subtype claim.

        theory2.tex Ch20 §20.3.1 — obstruction witnesses
        """
        available: set[str] = (
            set(candidate_surface.dunder_methods) | set(candidate_surface.abstract_methods)
        )
        return tuple(m for m in self.required_methods if m not in available)

    def as_judgment(self, trust: TrustLevel) -> Judgment:
        """Build a Judgment asserting the protocol surface is complete.

        The proposition formula ``protocol_surface_complete(<name>)`` is a
        BEHAVIORAL claim: the protocol defines all the method slots it declares
        and each slot has a corresponding method coordinate in the site.

        When ``trust`` is ``ORACLE_PROPOSED`` the evidence bundle records the
        CopilotChannel as the originating channel — see §20.3.1 for the
        COPILOT_SUGGESTED trust ceiling semantics.

        theory2.tex Ch20 §20.3.1 — surface completeness judgment
        """
        formula = f"protocol_surface_complete({self.protocol_name})"
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=formula,
            free_variables=(self.protocol_name,),
            metadata={
                "required_methods": list(self.required_methods),
                "runtime_checkable": self.runtime_checkable,
            },
        )
        bundle = _evidence_bundle_for(formula, trust)
        provenance = _provenance_oracle()
        return Judgment(
            coordinate=self._coord,
            proposition=prop,
            carrier=Carrier(name=self.protocol_name, parameters=("Protocol",)),
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=_make_trust(trust),
            provenance=provenance,
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )


# ---
# §20.3.2  StructuralSubtypeChecker
# ---

class StructuralSubtypeChecker:
    """Checks structural subtyping between behavioral surfaces.

    Two classes are structurally related if one's surface covers the other's.
    This differs from nominal subtyping (which requires explicit inheritance).
    In site terms, structural subtyping is an open cover: for each required
    method coordinate, there is a covering morphism.

    Structural subtype graphs can grow large; this checker uses a flat map of
    :class:`BehavioralSurface` objects so that look-ups are O(1).  The CopilotChannel
    can seed the ``surfaces`` map at ORACLE_PROPOSED trust for classes not yet
    witnessed at runtime.

    theory2.tex Ch20 §20.3.2
    """

    def __init__(self, surfaces: dict[str, BehavioralSurface]) -> None:
        """Initialize with a mapping from class name to BehavioralSurface.

        Args:
            surfaces: Dict mapping each class name (or protocol name) to its
                pre-computed :class:`BehavioralSurface`.  Entries may be
                ORACLE_PROPOSED (CopilotChannel) or RUNTIME_WITNESSED.

        theory2.tex Ch20 §20.3.2
        """
        self._surfaces: dict[str, BehavioralSurface] = dict(surfaces)

    def is_structural_subtype(self, cls_name: str, protocol_name: str) -> bool:
        """Return ``True`` if ``cls_name`` structurally implements ``protocol_name``.

        Looks up both surfaces and computes whether every abstract method of
        the protocol is available in the candidate class's method set (the
        union of its dunder methods and abstract methods).

        Returns ``False`` conservatively if either surface is missing from the
        registry.

        theory2.tex Ch20 §20.3.2 — structural subtype predicate
        """
        cls_surf = self._surfaces.get(cls_name)
        proto_surf = self._surfaces.get(protocol_name)
        if cls_surf is None or proto_surf is None:
            return False
        available: set[str] = set(cls_surf.dunder_methods) | set(cls_surf.abstract_methods)
        required: set[str] = set(proto_surf.abstract_methods)
        return required.issubset(available)

    def structural_morphism(self, cls_name: str, protocol_name: str) -> Morphism | None:
        """Return a REFINEMENT morphism if ``cls_name`` implements ``protocol_name``.

        The morphism goes from the class surface coordinate to the protocol
        surface coordinate, labelled with the structural subtype claim.
        Returns ``None`` if the subtype relationship does not hold.

        theory2.tex Ch20 §20.3.2 — structural morphism
        """
        if not self.is_structural_subtype(cls_name, protocol_name):
            return None
        cls_surf = self._surfaces[cls_name]
        proto_surf = self._surfaces[protocol_name]
        return Morphism(
            source=cls_surf.coordinate,
            target=proto_surf.coordinate,
            kind=MorphismKind.REFINEMENT,
            label=f"structural_subtype({cls_name}, {protocol_name})",
        )

    def all_structural_subtypes(self, protocol_name: str) -> list[str]:
        """Return all class names that are structural subtypes of ``protocol_name``.

        Iterates over every entry in the surface registry and tests each
        candidate with :meth:`is_structural_subtype`.  Protocol entries are
        included only if they themselves satisfy the required method set.

        theory2.tex Ch20 §20.3.2 — subtype enumeration
        """
        return [
            name for name in self._surfaces
            if name != protocol_name and self.is_structural_subtype(name, protocol_name)
        ]

    def subtype_lattice_edges(self) -> list[tuple[str, str]]:
        """Return all (cls, proto) pairs where cls is a structural subtype of proto.

        The edges of the structural subtype lattice allow the site to be built
        with REFINEMENT morphisms connecting each implementing class to each
        protocol it structurally satisfies.

        theory2.tex Ch20 §20.3.2 — subtype lattice
        """
        edges: list[tuple[str, str]] = []
        names = list(self._surfaces.keys())
        for cls_name in names:
            for proto_name in names:
                if cls_name != proto_name and self.is_structural_subtype(cls_name, proto_name):
                    edges.append((cls_name, proto_name))
        return edges

    def obstruction_for(self, cls_name: str, protocol_name: str) -> Obstruction | None:
        """Return an Obstruction if ``cls_name`` fails structural subtype of ``protocol_name``.

        When the check fails, enumerates the missing methods and packs them
        into ``repair_hints`` of the returned :class:`Obstruction`.  Returns
        ``None`` if the structural subtype holds (no obstruction).

        theory2.tex Ch20 §20.3.2 — obstruction construction
        """
        if self.is_structural_subtype(cls_name, protocol_name):
            return None
        cls_surf = self._surfaces.get(cls_name)
        proto_surf = self._surfaces.get(protocol_name)
        if cls_surf is None or proto_surf is None:
            missing_hints = (f"surface_not_registered({cls_name if cls_surf is None else protocol_name})",)
        else:
            available: set[str] = set(cls_surf.dunder_methods) | set(cls_surf.abstract_methods)
            missing = [m for m in proto_surf.abstract_methods if m not in available]
            missing_hints = tuple(f"add_method({m})" for m in missing)
        obs_id = _stable_hash(f"structural_subtype({cls_name},{protocol_name})")
        coord_str = f"{cls_name}.behavioral_surface" if cls_name in self._surfaces else cls_name
        return Obstruction(
            obstruction_id=obs_id,
            violated_condition=f"structural_subtype({cls_name}, {protocol_name})",
            coordinate=coord_str,
            evidence_at_time=(),
            repair_hints=missing_hints,
            cohomology_class=f"H1(structural_surface, {protocol_name})",
            is_resolved=False,
            resolution_evidence="",
            provenance=(),
        )

    def as_site(self) -> Site:
        """Build a Site with all surface coordinates and structural morphisms.

        Creates one coordinate per surface and one REFINEMENT morphism per
        structural subtype edge found by :meth:`subtype_lattice_edges`.
        The resulting Site can be handed to the Grothendieck topology engine
        for sheaf computations over the behavioral surface lattice.

        theory2.tex Ch20 §20.3.2 — site construction from subtype lattice
        """
        builder = SiteBuilder()
        for surf in self._surfaces.values():
            if surf.coordinate is not None:
                builder.add_coordinate(surf.coordinate)
        for cls_name, proto_name in self.subtype_lattice_edges():
            m = self.structural_morphism(cls_name, proto_name)
            if m is not None:
                builder.add_morphism(m)
        return builder.build()


# ---
# §20.3.3  BehavioralSurfaceBuilder
# ---

class BehavioralSurfaceBuilder:
    """Incrementally constructs a BehavioralSurface from class inspection data.

    Used during class creation tracing to accumulate the observable surface
    step by step.  Each ``add_*`` call returns a *new* builder (immutable style)
    so the build history can be traced.

    The builder is deliberately not frozen so that internal mutable lists can be
    appended cheaply; immutability is expressed at the API level by always
    returning a new instance.  This mirrors the way JudgmentBuilder works in
    ``jugeo.judgments``.

    CopilotChannel can drive the builder at ORACLE_PROPOSED trust during static
    analysis; the resulting :class:`BehavioralSurface` will carry that trust
    level until an explicit :meth:`with_trust` call upgrades it.

    theory2.tex Ch20 §20.3.3
    """

    def __init__(self, class_name: str, coordinate: Coordinate) -> None:
        """Initialize an empty builder for the named class.

        Args:
            class_name: The simple or fully-qualified name of the class.
            coordinate: The pre-computed Coordinate for the class surface.

        All accumulator lists start empty.  Call :meth:`add_protocol`,
        :meth:`add_dunder`, :meth:`add_abstract`, and :meth:`with_trust`
        before calling :meth:`build`.

        theory2.tex Ch20 §20.3.3
        """
        self._class_name = class_name
        self._coordinate = coordinate
        self._protocols: list[str] = []
        self._dunder_methods: list[str] = []
        self._abstract_methods: list[str] = []
        self._trust: TrustLevel = TrustLevel.UNVERIFIED
        self._judgment_index: dict[str, str] = {}

    def _clone(self) -> "BehavioralSurfaceBuilder":
        """Return a shallow copy of this builder for immutable-style chaining."""
        clone = BehavioralSurfaceBuilder(self._class_name, self._coordinate)
        clone._protocols = list(self._protocols)
        clone._dunder_methods = list(self._dunder_methods)
        clone._abstract_methods = list(self._abstract_methods)
        clone._trust = self._trust
        clone._judgment_index = dict(self._judgment_index)
        return clone

    def add_protocol(self, p: str) -> "BehavioralSurfaceBuilder":
        """Return a new builder with protocol ``p`` added to the protocol list.

        Duplicate entries are silently ignored to keep the list canonical.
        The protocol name should match the name used in the surface registry
        so that :class:`StructuralSubtypeChecker` look-ups work correctly.

        theory2.tex Ch20 §20.3.3 — protocol accumulation
        """
        clone = self._clone()
        if p not in clone._protocols:
            clone._protocols.append(p)
        return clone

    def add_dunder(self, name: str) -> "BehavioralSurfaceBuilder":
        """Return a new builder with ``name`` added to dunder methods.

        Only names that start *and* end with ``__`` are accepted; others are
        silently dropped.  This mirrors Python's own dunder detection logic.

        theory2.tex Ch20 §20.3.3 — dunder accumulation
        """
        clone = self._clone()
        if name.startswith("__") and name.endswith("__") and name not in clone._dunder_methods:
            clone._dunder_methods.append(name)
        return clone

    def add_abstract(self, name: str) -> "BehavioralSurfaceBuilder":
        """Return a new builder with ``name`` added to abstract methods.

        Abstract methods form the behavioral contract: any class that provides
        all abstract methods satisfies the structural subtype claim.  Duplicate
        entries are ignored.

        theory2.tex Ch20 §20.3.3 — abstract method accumulation
        """
        clone = self._clone()
        if name not in clone._abstract_methods:
            clone._abstract_methods.append(name)
        return clone

    def with_trust(self, trust: TrustLevel) -> "BehavioralSurfaceBuilder":
        """Return a new builder whose trust level is set to ``trust``.

        Trust levels are monotone in JuGeo: upgrading from ORACLE_PROPOSED to
        RUNTIME_WITNESSED is always allowed, but the reverse requires an
        explicit challenge.  The builder does not enforce this invariant —
        callers are responsible.

        theory2.tex Ch20 §20.3.3 — trust annotation
        """
        clone = self._clone()
        clone._trust = trust
        return clone

    def index_judgment(self, method_name: str, judgment_id: str) -> "BehavioralSurfaceBuilder":
        """Return a new builder with ``method_name`` mapped to ``judgment_id``.

        The judgment index allows :class:`JudgmentIndexedProtocol` to look up
        which Judgment governs each method slot without scanning the full
        judgment store.

        theory2.tex Ch20 §20.3.3 — judgment indexing
        """
        clone = self._clone()
        clone._judgment_index[method_name] = judgment_id
        return clone

    def build(self) -> BehavioralSurface:
        """Construct and return the final :class:`BehavioralSurface`.

        All accumulated protocols, dunders, and abstract methods are frozen
        into tuples.  The judgment index is copied as a plain dict (it is
        not part of the frozen record schema but is attached to the surface
        object for downstream consumers).

        theory2.tex Ch20 §20.3.3 — surface materialisation
        """
        return BehavioralSurface(
            class_name=self._class_name,
            coordinate=self._coordinate,
            protocols=tuple(self._protocols),
            dunder_methods=tuple(self._dunder_methods),
            abstract_methods=tuple(self._abstract_methods),
            trust=self._trust,
            judgment_index=dict(self._judgment_index),
        )

    def from_trace(self, trace: ClassCreationTrace) -> BehavioralSurface:
        """Extract a BehavioralSurface from a :class:`ClassCreationTrace`.

        Reads:
        - ``dunder_methods`` from body_names that start and end with ``__``
        - ``protocols`` from ``prepare_result.get("protocols", [])``
        - ``abstract_methods`` from ``prepare_result.get("abstract_methods", [])``

        Uses the trace's own trust level so that the resulting surface reflects
        how the trace itself was collected (runtime witness vs. oracle proposal).

        theory2.tex Ch20 §20.3.3 — surface extraction from creation trace
        """
        dunders = tuple(n for n in trace.body_names if n.startswith("__") and n.endswith("__"))
        protocols_raw = trace.prepare_result.get("protocols", []) if trace.prepare_result else []
        abstract_raw = trace.prepare_result.get("abstract_methods", []) if trace.prepare_result else []
        trust = trace.trust if trace.trust is not None else TrustLevel.UNVERIFIED
        return BehavioralSurface(
            class_name=trace.class_name,
            coordinate=trace.coordinate,
            protocols=tuple(protocols_raw),
            dunder_methods=dunders,
            abstract_methods=tuple(abstract_raw),
            trust=trust,
            judgment_index={
                m: _stable_hash(f"{trace.class_name}:{m}")
                for m in list(dunders) + list(abstract_raw)
            },
        )


# ---
# §20.3.4  JudgmentIndexedProtocol
# ---

# Sets used to classify method names into proposition kinds
_STRUCTURAL_DUNDERS: frozenset[str] = frozenset({
    "__init__", "__new__", "__init_subclass__", "__class_getitem__", "__set_name__",
    "__del__", "__slots__",
})
_BEHAVIORAL_DUNDERS: frozenset[str] = frozenset({
    "__call__", "__iter__", "__next__", "__aiter__", "__anext__",
    "__enter__", "__exit__", "__aenter__", "__aexit__",
    "__len__", "__length_hint__",
    "__getitem__", "__setitem__", "__delitem__", "__missing__", "__contains__",
    "__reversed__",
})
_RELATIONAL_DUNDERS: frozenset[str] = frozenset({
    "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__",
    "__hash__", "__bool__",
})
_RESOURCE_DUNDERS: frozenset[str] = frozenset({
    "__del__", "__enter__", "__exit__", "__aenter__", "__aexit__",
    "__sizeof__", "__format__",
})


class JudgmentIndexedProtocol:
    """A behavioral surface indexed by judgment kinds.

    Methods are grouped by the judgment kind they participate in:
    STRUCTURAL methods (like ``__init__``, ``__new__``) form one index entry,
    BEHAVIORAL methods (like ``__call__``, ``__iter__``) form another, etc.

    This enables fine-grained trust assignment: a copilot-assisted
    analysis of behavioral methods gets ORACLE_PROPOSED trust without
    contaminating the STRUCTURAL methods' SOLVER_DISCHARGED trust.

    The CopilotChannel populates this index during static analysis sessions;
    each resulting Judgment records the CopilotChannel as a provenance source
    and carries the COPILOT_SUGGESTED trust ceiling until promoted.

    theory2.tex Ch20 §20.3.4
    """

    def __init__(self, surface: BehavioralSurface) -> None:
        """Initialize with an existing BehavioralSurface.

        Immediately builds the internal categorization of all methods in
        ``surface.dunder_methods`` and ``surface.abstract_methods``.

        Args:
            surface: The pre-computed surface whose methods are to be indexed.

        theory2.tex Ch20 §20.3.4
        """
        self._surface = surface
        self._all_methods: frozenset[str] = (
            frozenset(surface.dunder_methods) | frozenset(surface.abstract_methods)
        )

    @property
    def surface(self) -> BehavioralSurface:
        """Return the underlying BehavioralSurface."""
        return self._surface

    def structural_methods(self) -> tuple[str, ...]:
        """Return dunder methods that belong to the STRUCTURAL proposition kind.

        These are the methods that establish the class's identity and
        construction protocol: ``__init__``, ``__new__``, ``__init_subclass__``,
        ``__class_getitem__``, and ``__set_name__``.

        theory2.tex Ch20 §20.3.4 — structural method classification
        """
        return tuple(m for m in self._surface.dunder_methods if m in _STRUCTURAL_DUNDERS)

    def behavioral_methods(self) -> tuple[str, ...]:
        """Return dunder methods that belong to the BEHAVIORAL proposition kind.

        These are the methods that define runtime behaviour: iteration,
        callable invocation, context management, and container protocols.
        A CopilotChannel analysis of these methods yields ORACLE_PROPOSED
        trust claims about the class's runtime behaviour.

        theory2.tex Ch20 §20.3.4 — behavioral method classification
        """
        return tuple(m for m in self._surface.dunder_methods if m in _BEHAVIORAL_DUNDERS)

    def judgments_for_kind(self, kind: PropositionKind) -> list[Judgment]:
        """Build Judgment objects for all methods matching the given kind.

        Dispatches to :meth:`structural_methods` for STRUCTURAL, to
        :meth:`behavioral_methods` for BEHAVIORAL, and to the full dunder set
        for RELATIONAL and RESOURCE kinds.  Each Judgment uses the surface's
        trust level.

        theory2.tex Ch20 §20.3.4 — per-kind judgment construction
        """
        trust = self._surface.trust or TrustLevel.UNVERIFIED
        if kind == PropositionKind.STRUCTURAL:
            methods = self.structural_methods()
        elif kind == PropositionKind.BEHAVIORAL:
            methods = self.behavioral_methods()
        elif kind == PropositionKind.RELATIONAL:
            methods = tuple(m for m in self._surface.dunder_methods if m in _RELATIONAL_DUNDERS)
        elif kind == PropositionKind.RESOURCE:
            methods = tuple(m for m in self._surface.dunder_methods if m in _RESOURCE_DUNDERS)
        else:
            methods = tuple(self._surface.abstract_methods)
        judgments: list[Judgment] = []
        for method in methods:
            formula = f"method_present({self._surface.class_name}, {method})"
            prop = Proposition(
                kind=kind,
                formula=formula,
                free_variables=(self._surface.class_name, method),
                metadata={"method": method, "kind": kind.value if hasattr(kind, "value") else str(kind)},
            )
            j = Judgment(
                coordinate=_method_coord(self._surface.class_name, method),
                proposition=prop,
                carrier=Carrier(name=self._surface.class_name),
                evidence=_evidence_bundle_for(formula, trust),
                obligations=(),
                obstructions=(),
                trust=_make_trust(trust),
                provenance=_provenance_oracle(),
                clauses=(),
                status=JudgmentStatus.PROPOSED,
            )
            judgments.append(j)
        return judgments

    def full_judgment_index(self) -> dict[str, Judgment]:
        """Return a dict mapping each method name to its governing Judgment.

        Covers structural, behavioral, relational, and resource kinds plus
        any remaining abstract methods.  Each Judgment is keyed by the simple
        method name for easy look-up by :class:`StructuralSubtypeChecker` and
        the CopilotChannel annotation pipeline.

        theory2.tex Ch20 §20.3.4 — full judgment index
        """
        index: dict[str, Judgment] = {}
        for kind in (
            PropositionKind.STRUCTURAL,
            PropositionKind.BEHAVIORAL,
            PropositionKind.RELATIONAL,
            PropositionKind.RESOURCE,
            PropositionKind.SEMANTIC,
        ):
            for j in self.judgments_for_kind(kind):
                # Extract method name from formula: "method_present(cls, method)"
                formula = j.proposition.formula if j.proposition else ""
                parts = formula.rstrip(")").split(", ", 1)
                method_name = parts[1] if len(parts) == 2 else formula
                index.setdefault(method_name, j)
        return index

    def merge(self, other: "JudgmentIndexedProtocol") -> "JudgmentIndexedProtocol":
        """Return a new JIP with the union of both surfaces' methods.

        The merged surface's trust level is the lower of the two surfaces'
        trust levels (conservative: the weakest evidence governs the union).
        Protocols, dunders, and abstract methods are unioned and deduplicated.

        theory2.tex Ch20 §20.3.4 — surface merge
        """
        s1, s2 = self._surface, other._surface
        trust_a = s1.trust or TrustLevel.UNVERIFIED
        trust_b = s2.trust or TrustLevel.UNVERIFIED
        merged_trust = trust_a if trust_a.value <= trust_b.value else trust_b  # type: ignore[attr-defined]
        merged_protocols = tuple(dict.fromkeys(list(s1.protocols) + list(s2.protocols)))
        merged_dunders = tuple(dict.fromkeys(list(s1.dunder_methods) + list(s2.dunder_methods)))
        merged_abstract = tuple(dict.fromkeys(list(s1.abstract_methods) + list(s2.abstract_methods)))
        merged_ji = {**s1.judgment_index, **s2.judgment_index}
        merged_coord = s1.coordinate
        merged_surface = BehavioralSurface(
            class_name=f"{s1.class_name}+{s2.class_name}",
            coordinate=merged_coord,
            protocols=merged_protocols,
            dunder_methods=merged_dunders,
            abstract_methods=merged_abstract,
            trust=merged_trust,
            judgment_index=merged_ji,
        )
        return JudgmentIndexedProtocol(merged_surface)

    def missing_structural(self, required: tuple[str, ...]) -> tuple[str, ...]:
        """Return required structural method names absent from the surface.

        Tests each name in ``required`` against ``surface.dunder_methods``.
        Non-empty result is a witness for a STRUCTURAL obstruction that the
        CopilotChannel can include in a repair hint bundle.

        theory2.tex Ch20 §20.3.4 — structural completeness gap
        """
        present: frozenset[str] = frozenset(self._surface.dunder_methods)
        return tuple(m for m in required if m not in present)


# ---
# Module-level convenience factory
# ---

def build_protocol_surface(
    protocol_name: str,
    required_methods: tuple[str, ...],
    runtime_checkable: bool = False,
    trust: TrustLevel = TrustLevel.RUNTIME_WITNESSED,
) -> BehavioralSurface:
    """Top-level helper: build a BehavioralSurface for a protocol in one call.

    Combines :class:`ProtocolSurfaceAnalyzer` and :class:`BehavioralSurfaceBuilder`
    to produce a surface ready for insertion into a :class:`StructuralSubtypeChecker`
    registry.

    theory2.tex Ch20 §20.3 — convenience entry-point
    """
    analyzer = ProtocolSurfaceAnalyzer(protocol_name, required_methods, runtime_checkable)
    return analyzer.surface(trust=trust)


def structural_subtype_site(
    class_surfaces: dict[str, BehavioralSurface],
) -> Site:
    """Build a full Site from a map of behavioral surfaces.

    Delegates to :class:`StructuralSubtypeChecker` to discover all subtype
    edges and materialise the corresponding REFINEMENT morphisms in the site.

    theory2.tex Ch20 §20.3.2 — site construction helper
    """
    checker = StructuralSubtypeChecker(class_surfaces)
    return checker.as_site()
