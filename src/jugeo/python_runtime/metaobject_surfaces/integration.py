from __future__ import annotations

r"""theory2.tex Ch20 §20.8 — Integration layer connecting metaobject records to judgments, sites, and evidence channels.

This module bridges the four core metaobject record types
(``MetaclassRecord``, ``BehavioralSurface``, ``DescriptorChain``,
``ClassCreationTrace``) to the JuGeo judgment algebra, Grothendieck site
construction, and evidence channel pipeline.

JuGeo models Python's metaobject protocol (MOP) as a typed judgment system:
each class creation event, metaclass resolution step, descriptor lookup, and
protocol implementation becomes a ``Judgment`` tuple indexed by a ``Coordinate``
in the site.  Trust flows from lower channels (CopilotChannel at
``ORACLE_PROPOSED``) to higher channels (solver at ``SOLVER_DISCHARGED``) via
explicit promotion—no silent upgrades are permitted.

Theory alignment
----------------
§20.8.1  :class:`MetaclassJudgmentIntegrator`  — metaclass judgments
§20.8.2  :class:`BehavioralSurfaceSiteBuilder` — site construction
§20.8.3  :class:`DescriptorChainChannelBridge` — descriptor evidence
§20.8.4  :class:`ClassCreationJudgmentEmitter` — class-creation phase judgments

All copilot-assisted code generation in this module is governed by the trust
algebra: CopilotChannel proposals enter at ``ORACLE_PROPOSED`` and must be
corroborated by solver or runtime evidence before any judgment settles at
``SOLVER_DISCHARGED`` or higher.
"""

import hashlib
import json
import datetime
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Mapping, Sequence
from enum import Enum

# --- geometry imports ---

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology,
        CoordinateObject,
    )
except ImportError:
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

# --- judgment imports ---

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

# --- solver imports ---

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

# --- evidence channel imports ---

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

# --- metaobject model imports ---

try:
    from jugeo.python_runtime.metaobject_surfaces.models import (
        MetaclassRecord, BehavioralSurface, DescriptorChain, ClassCreationTrace,
        _metaclass_coordinate, _class_coordinate, _now_str,
    )
except ImportError:
    from dataclasses import dataclass as _dc2, field as _field2
    @_dc2(frozen=True, slots=True)
    class MetaclassRecord:
        class_name: str = ""; metaclass_name: str = ""; coordinate: object = None
        bases: tuple = (); metaclass_coordinate: object = None; trust: object = None
        mro: tuple = (); created_at: str = ""
        def to_dict(self): return {"class_name": self.class_name, "metaclass_name": self.metaclass_name}
        def as_carrier(self): return None
    @_dc2(frozen=True, slots=True)
    class BehavioralSurface:
        class_name: str = ""; coordinate: object = None; protocols: tuple = ()
        dunder_methods: tuple = (); abstract_methods: tuple = ()
        trust: object = None; judgment_index: dict = _field2(default_factory=dict)
        def as_covering_family(self): return CoveringFamily()
        def as_judgment(self): return Judgment()
    @_dc2(frozen=True, slots=True)
    class DescriptorChain:
        attribute_name: str = ""; owner_class: str = ""; coordinate: object = None
        chain: tuple = (); descriptor_kind: str = "NON_DATA"
        trust: object = None; override_map: dict = _field2(default_factory=dict)
        def as_morphism_sequence(self): return []
    @_dc2(frozen=True, slots=True)
    class ClassCreationTrace:
        class_name: str = ""; coordinate: object = None; namespace_coordinate: object = None
        metaclass: object = None; prepare_result: dict = _field2(default_factory=dict)
        body_names: tuple = (); init_subclass_called: bool = False
        trust: object = None; created_at: str = ""
        def creation_morphisms(self): return []
        def as_judgment(self): return Judgment()
        def surface(self): return BehavioralSurface(class_name=self.class_name)
    def _metaclass_coordinate(cn, mn): return Coordinate(components=(cn, mn, "meta"), kind=CoordinateKind.INTERFACE)
    def _class_coordinate(cn, mod="unknown"): return Coordinate(components=(mod, cn), kind=CoordinateKind.INTERFACE)
    def _now_str(): return datetime.datetime.utcnow().isoformat() + "Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_trust(trust_obj: Any) -> TrustLevel:
    """Coerce an arbitrary trust value to a ``TrustLevel`` enum member.

    If *trust_obj* is already a ``TrustLevel``, it is returned unchanged.
    If it is an integer, the corresponding enum member is returned.
    Otherwise ``UNVERIFIED`` is returned as a safe default.

    theory2.tex Ch20 §20.8
    """
    if isinstance(trust_obj, TrustLevel):
        return trust_obj
    if isinstance(trust_obj, int):
        for t in TrustLevel:
            if t.value == trust_obj:
                return t
    return TrustLevel.UNVERIFIED


def _build_provenance(
    source: ProvenanceSource,
    timestamp: str = "",
    parent_ids: tuple[str, ...] = (),
    history: tuple[str, ...] = (),
) -> Provenance:
    """Build a ``Provenance`` record with a canonical timestamp.

    Parameters
    ----------
    source:
        The originating provenance source (RUNTIME, SOLVER, ORACLE, etc.).
    timestamp:
        ISO-8601 creation timestamp.  Defaults to now.
    parent_ids:
        Identifiers of parent judgments that contributed to this provenance.
    history:
        Sequence of transformation labels applied so far.

    Returns
    -------
    Provenance
        A freshly constructed provenance record.
    """
    ts = timestamp if timestamp else _now_iso()
    return Provenance(
        source=source,
        parent_judgments=parent_ids,
        creation_timestamp=ts,
        transformation_history=history,
        metadata={},
    )


def _build_evidence_bundle(
    items: tuple[EvidenceItem, ...],
    summary: str = "",
) -> EvidenceBundle:
    """Wrap a sequence of evidence items in an ``EvidenceBundle``.

    If *summary* is empty it is generated from the item count and trust
    levels of the constituent items.

    Parameters
    ----------
    items:
        The evidence items to bundle.
    summary:
        Optional human-readable summary string.

    Returns
    -------
    EvidenceBundle
    """
    if not summary:
        levels = [
            i.trust_level.name if hasattr(i, "trust_level") and i.trust_level else "UNKNOWN"
            for i in items
        ]
        summary = f"{len(items)} evidence item(s); trust levels: {', '.join(levels)}"
    return EvidenceBundle(items=items, summary=summary)


# ---------------------------------------------------------------------------
# Class 1 — MetaclassJudgmentIntegrator
# ---------------------------------------------------------------------------


class MetaclassJudgmentIntegrator:
    """Converts MetaclassRecord objects to full Judgment tuples.

    Each MetaclassRecord represents a class creation event in the site.
    This integrator translates those records into Judgment objects suitable
    for the judgment algebra, with appropriate evidence from the solver
    and runtime channels.

    CopilotChannel evidence is admitted at ORACLE_PROPOSED trust and must
    be promoted explicitly.  All COPILOT_SUGGESTED metaclass resolution
    proposals require solver corroboration before the judgment settles.

    theory2.tex Ch20 §20.8.1
    """

    def __init__(
        self,
        records: list[MetaclassRecord],
        solver: Z3Session | None = None,
    ) -> None:
        """Initialise the integrator with a list of metaclass records.

        Parameters
        ----------
        records:
            The list of :class:`MetaclassRecord` objects to integrate.
        solver:
            Optional :class:`Z3Session` used to attempt solver-based
            corroboration of metaclass resolution judgments.  When ``None``
            all judgments are left at the trust level carried by the record.
        """
        self._records: list[MetaclassRecord] = list(records)
        self._solver: Z3Session | None = solver
        self._judgment_cache: dict[str, Judgment] = {}

    # --- primary conversion ---

    def judgment_for(self, record: MetaclassRecord) -> Judgment:
        """Build a ``Judgment`` for a single ``MetaclassRecord``.

        The judgment coordinate is taken directly from ``record.coordinate``.
        The proposition asserts metaclass validity for the class/metaclass pair.
        Trust and status are derived from the record's own trust annotation.

        If a judgment for ``record.class_name`` has already been built in this
        session it is returned from the cache.

        Parameters
        ----------
        record:
            The metaclass record to convert.

        Returns
        -------
        Judgment
            A fully-populated judgment for the record.

        Notes
        -----
        CopilotChannel-originated records carry ``ORACLE_PROPOSED`` trust and
        produce a ``PROPOSED`` judgment status.  Runtime-witnessed records
        produce ``SETTLED`` judgments.
        """
        cache_key = f"{record.class_name}::{record.metaclass_name}"
        if cache_key in self._judgment_cache:
            return self._judgment_cache[cache_key]

        trust = _resolve_trust(record.trust)
        coordinate = record.coordinate or _class_coordinate(record.class_name)

        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=f"metaclass_valid({record.class_name!r}, {record.metaclass_name!r})",
            free_variables=(),
            metadata={"class_name": record.class_name, "metaclass_name": record.metaclass_name},
        )

        raw_carrier = None
        if hasattr(record, "as_carrier") and callable(record.as_carrier):
            raw_carrier = record.as_carrier()
        if raw_carrier is None:
            raw_carrier = Carrier(
                name=f"MetaclassCarrier({record.class_name})",
                parameters={"metaclass": record.metaclass_name, "mro_length": len(record.mro)},
                is_dependent=False,
                metadata=record.to_dict(),
            )

        provenance = _build_provenance(
            source=ProvenanceSource.RUNTIME,
            timestamp=record.created_at if record.created_at else _now_iso(),
        )

        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"class_name": record.class_name, "metaclass_name": record.metaclass_name},
            trust_level=trust,
            channel=EvidenceChannel.RUNTIME,
            timestamp=provenance.creation_timestamp,
            provenance=provenance,
        )
        evidence = _build_evidence_bundle(
            (evidence_item,),
            summary=f"Runtime witness for metaclass_valid({record.class_name!r})",
        )

        threshold_value = TrustLevel.RUNTIME_WITNESSED.value
        if hasattr(trust, "value"):
            settled = trust.value >= threshold_value
        else:
            settled = False
        status = JudgmentStatus.SETTLED if settled else JudgmentStatus.PROPOSED

        judgment = Judgment(
            coordinate=coordinate,
            proposition=proposition,
            carrier=raw_carrier,
            evidence=evidence,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=status,
        )
        self._judgment_cache[cache_key] = judgment
        return judgment

    # --- solver verification ---

    def verify_resolution(self, records: list[MetaclassRecord]) -> Judgment:
        """Attempt solver verification for a set of metaclass records.

        Builds a summary ``Judgment`` asserting that all supplied records have
        pairwise-compatible metaclasses.  When a ``Z3Session`` is available
        the integrator submits a query and adjusts trust based on the solver
        outcome.

        Parameters
        ----------
        records:
            The records to verify collectively.

        Returns
        -------
        Judgment
            A summary judgment whose trust reflects solver results.  If the
            solver is unavailable the judgment carries ``ORACLE_PROPOSED``
            trust and ``PROPOSED`` status.
        """
        class_names = [r.class_name for r in records]
        metaclass_names = list({r.metaclass_name for r in records})

        base_coordinate = Coordinate(
            components=("metaclass_resolution", "summary"),
            kind=CoordinateKind.THEOREM,
        )

        formula = (
            f"metaclass_resolution_compatible("
            f"{', '.join(repr(cn) for cn in class_names)})"
        )
        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=tuple(class_names),
            metadata={"metaclass_names": metaclass_names},
        )

        # Attempt solver corroboration
        if self._solver is not None:
            query = {"class_names": class_names, "metaclass_names": metaclass_names}
            try:
                result = self._solver.solve(query)
                if getattr(result, "outcome", None) == SolveOutcome.UNSAT:
                    # Conflict detected — obstructed
                    obs = self.obstruction_for_conflict(records[0], records[-1]) if len(records) >= 2 else None
                    trust = TrustLevel.CONTRADICTED
                    status = JudgmentStatus.OBSTRUCTED
                    obstructions = (obs,) if obs is not None else ()
                elif getattr(result, "outcome", None) == SolveOutcome.SAT:
                    trust = TrustLevel.SOLVER_DISCHARGED
                    status = JudgmentStatus.SETTLED
                    obstructions = ()
                else:
                    trust = TrustLevel.ORACLE_PROPOSED
                    status = JudgmentStatus.PROPOSED
                    obstructions = ()
            except Exception:
                trust = TrustLevel.UNVERIFIED
                status = JudgmentStatus.PROPOSED
                obstructions = ()
        else:
            trust = TrustLevel.ORACLE_PROPOSED
            status = JudgmentStatus.PROPOSED
            obstructions = ()

        provenance = _build_provenance(
            source=ProvenanceSource.SOLVER if self._solver is not None else ProvenanceSource.ORACLE,
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.SOLVER_PROOF if self._solver is not None else EvidenceItemKind.ORACLE_PROPOSAL,
            payload={"class_names": class_names},
            trust_level=trust,
            channel=EvidenceChannel.SOLVER if self._solver is not None else EvidenceChannel.COPILOT,
            timestamp=provenance.creation_timestamp,
            provenance=provenance,
        )
        evidence = _build_evidence_bundle((evidence_item,))

        return Judgment(
            coordinate=base_coordinate,
            proposition=proposition,
            carrier=Carrier(
                name="MetaclassResolutionSummary",
                parameters={"record_count": len(records)},
            ),
            evidence=evidence,
            obligations=(),
            obstructions=obstructions,
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=status,
        )

    # --- bulk operations ---

    def all_judgments(self) -> list[Judgment]:
        """Return one ``Judgment`` per record in ``self._records``.

        Each judgment is produced via :meth:`judgment_for` and cached.

        Returns
        -------
        list[Judgment]
        """
        return [self.judgment_for(r) for r in self._records]

    def obstruction_for_conflict(
        self,
        a: MetaclassRecord,
        b: MetaclassRecord,
    ) -> Obstruction:
        """Build an ``Obstruction`` representing a metaclass conflict.

        A metaclass conflict arises when two classes in the same MRO have
        incompatible metaclasses—neither is a subtype of the other.  This
        obstruction records the violated condition, both coordinates, and
        repair hints.

        Parameters
        ----------
        a:
            The first conflicting metaclass record.
        b:
            The second conflicting metaclass record.

        Returns
        -------
        Obstruction
            An unresolved obstruction with repair hints.
        """
        conflict_id = _stable_hash(
            f"conflict::{a.class_name}::{a.metaclass_name}::{b.class_name}::{b.metaclass_name}"
        )
        violated = (
            f"metaclass_compatibility({a.metaclass_name!r}, {b.metaclass_name!r}): "
            f"neither is a subclass of the other"
        )
        provenance = _build_provenance(source=ProvenanceSource.RUNTIME)
        return Obstruction(
            obstruction_id=conflict_id,
            violated_condition=violated,
            coordinate=a.coordinate or _class_coordinate(a.class_name),
            evidence_at_time=(),
            repair_hints=(
                f"Make {a.metaclass_name!r} a subclass of {b.metaclass_name!r}",
                f"Make {b.metaclass_name!r} a subclass of {a.metaclass_name!r}",
                "Create a common metaclass that inherits from both",
            ),
            cohomology_class=f"H1(metaclass_conflict, {a.class_name}x{b.class_name})",
            is_resolved=False,
            resolution_evidence=(),
            provenance=provenance,
        )

    def emit_copilot_evidence(self, record: MetaclassRecord) -> EvidenceRecord:
        """Emit an ``EvidenceRecord`` via CopilotChannel for a metaclass record.

        CopilotChannel evidence is admitted at ``ORACLE_PROPOSED`` trust—the
        ceiling enforced by the COPILOT_SUGGESTED policy.  The caller is
        responsible for explicit promotion if solver corroboration is obtained.

        Parameters
        ----------
        record:
            The metaclass record to emit evidence for.

        Returns
        -------
        EvidenceRecord
            A record suitable for submission to the CopilotChannel router.
        """
        return EvidenceRecord(
            channel=EvidenceChannel.COPILOT,
            claim=f"metaclass_resolved({record.class_name!r})",
            payload={
                **record.to_dict(),
                "trust_ceiling": "ORACLE_PROPOSED",
                "copilot_policy": "COPILOT_SUGGESTED",
                "requires_corroboration": True,
            },
            obligations=(f"corroborate_with_solver({record.class_name!r})",),
            provenance=_build_provenance(source=ProvenanceSource.ORACLE),
        )

    def trust_summary(self) -> dict[str, str]:
        """Return a mapping of class name to trust level name.

        Useful for quick diagnostics without building full judgment objects.

        Returns
        -------
        dict[str, str]
            Keys are class names; values are trust level names.
        """
        result: dict[str, str] = {}
        for record in self._records:
            trust = _resolve_trust(record.trust)
            result[record.class_name] = trust.name if hasattr(trust, "name") else str(trust)
        return result


# ---------------------------------------------------------------------------
# Class 2 — BehavioralSurfaceSiteBuilder
# ---------------------------------------------------------------------------


class BehavioralSurfaceSiteBuilder:
    """Builds a Site from BehavioralSurface objects with protocol morphisms.

    Each BehavioralSurface becomes a coordinate in the site.  Protocol
    implementations generate covering families, and inheritance relationships
    generate INCLUSION morphisms.

    CopilotChannel-suggested protocol relationships are admitted at
    ORACLE_PROPOSED trust.

    theory2.tex Ch20 §20.8.2
    """

    def __init__(self, surfaces: list[BehavioralSurface]) -> None:
        """Initialise the builder with a list of behavioral surfaces.

        Parameters
        ----------
        surfaces:
            The list of :class:`BehavioralSurface` objects that will become
            site coordinates.
        """
        self._surfaces: list[BehavioralSurface] = list(surfaces)
        self._surface_index: dict[str, BehavioralSurface] = {
            s.class_name: s for s in surfaces
        }

    # --- primary site construction ---

    def build_site(self) -> Site:
        """Build and return a ``Site`` from all registered surfaces.

        The construction procedure:

        1. Each surface contributes one :class:`Coordinate` to the site.
        2. Protocol membership relationships contribute :class:`CoveringFamily`
           registrations (via the topology).
        3. For each pair ``(s1, s2)`` where ``s2.class_name`` appears in
           ``s1.protocols``, an ``INCLUSION`` morphism is added from ``s1``
           to ``s2``.
        4. For refinement relationships (behavioral sub-protocols), a
           ``REFINEMENT`` morphism is added.

        Returns
        -------
        Site
            The fully constructed site.
        """
        builder = SiteBuilder()

        for surface in self._surfaces:
            coord = surface.coordinate or _class_coordinate(surface.class_name)
            builder.add_coordinate(coord)

        # inclusion morphisms for protocol membership
        for surface in self._surfaces:
            src_coord = surface.coordinate or _class_coordinate(surface.class_name)
            for proto_name in (surface.protocols or ()):
                if proto_name in self._surface_index:
                    tgt = self._surface_index[proto_name]
                    tgt_coord = tgt.coordinate or _class_coordinate(tgt.class_name)
                    morphism = Morphism(
                        source=src_coord,
                        target=tgt_coord,
                        kind=MorphismKind.INCLUSION,
                        label=f"implements({surface.class_name!r}, {proto_name!r})",
                    )
                    builder.add_morphism(morphism)

        # refinement morphisms
        for m in self.protocol_morphisms():
            builder.add_morphism(m)

        return builder.build()

    def protocol_morphisms(self) -> list[Morphism]:
        """Return all REFINEMENT morphisms derived from protocol relationships.

        For each surface ``s1`` that lists ``s2.class_name`` in its protocols,
        a ``REFINEMENT`` morphism is created from ``s1.coordinate`` to
        ``s2.coordinate``.  This models behavioral sub-typing as a categorical
        refinement in the site.

        Returns
        -------
        list[Morphism]
        """
        morphisms: list[Morphism] = []
        for s1 in self._surfaces:
            src = s1.coordinate or _class_coordinate(s1.class_name)
            for proto_name in (s1.protocols or ()):
                if proto_name in self._surface_index:
                    s2 = self._surface_index[proto_name]
                    tgt = s2.coordinate or _class_coordinate(s2.class_name)
                    morphisms.append(Morphism(
                        source=src,
                        target=tgt,
                        kind=MorphismKind.REFINEMENT,
                        label=f"refines({s1.class_name!r}, {proto_name!r})",
                    ))
        return morphisms

    def covering_families(self) -> list[CoveringFamily]:
        """Return covering families derived from protocol implementations.

        For each surface with at least one protocol, its
        :meth:`BehavioralSurface.as_covering_family` method is called to
        produce the family.  Surfaces with no protocols do not contribute a
        covering family.

        Returns
        -------
        list[CoveringFamily]
        """
        families: list[CoveringFamily] = []
        for surface in self._surfaces:
            if surface.protocols:
                try:
                    family = surface.as_covering_family()
                    if family is not None:
                        families.append(family)
                except Exception:
                    # Gracefully degrade if as_covering_family is not implemented
                    base_coord = surface.coordinate or _class_coordinate(surface.class_name)
                    member_coords = [
                        self._surface_index[p].coordinate or _class_coordinate(p)
                        for p in surface.protocols
                        if p in self._surface_index
                    ]
                    families.append(CoveringFamily(
                        base=base_coord,
                        members=member_coords,
                        label=f"protocols_of({surface.class_name!r})",
                    ))
        return families

    def as_judgment_for(self, surface: BehavioralSurface) -> Judgment:
        """Return the judgment for a single behavioral surface.

        Delegates to :meth:`BehavioralSurface.as_judgment` when available.
        If the surface does not provide that method, a structural judgment
        asserting protocol completeness is synthesised here.

        Parameters
        ----------
        surface:
            The behavioral surface to convert to a judgment.

        Returns
        -------
        Judgment
        """
        try:
            result = surface.as_judgment()
            if result is not None:
                return result
        except Exception:
            pass

        coord = surface.coordinate or _class_coordinate(surface.class_name)
        trust = _resolve_trust(surface.trust)
        formula = (
            f"behavioral_surface_complete({surface.class_name!r}, "
            f"protocols={len(surface.protocols or ())}, "
            f"dunders={len(surface.dunder_methods or ())})"
        )
        proposition = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=formula,
            free_variables=(),
            metadata={"class_name": surface.class_name},
        )
        provenance = _build_provenance(source=ProvenanceSource.RUNTIME)
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"class_name": surface.class_name, "protocol_count": len(surface.protocols or ())},
            trust_level=trust,
            channel=EvidenceChannel.RUNTIME,
            timestamp=provenance.creation_timestamp,
            provenance=provenance,
        )
        evidence = _build_evidence_bundle((evidence_item,))
        status = JudgmentStatus.SETTLED if trust.value >= TrustLevel.RUNTIME_WITNESSED.value else JudgmentStatus.PROPOSED
        return Judgment(
            coordinate=coord,
            proposition=proposition,
            carrier=Carrier(
                name=f"BehavioralSurface({surface.class_name})",
                parameters={"protocol_count": len(surface.protocols or ())},
            ),
            evidence=evidence,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=status,
        )

    def all_judgments(self) -> list[Judgment]:
        """Return one judgment per registered surface.

        Returns
        -------
        list[Judgment]
        """
        return [self.as_judgment_for(s) for s in self._surfaces]

    def site_label(self) -> str:
        """Return a human-readable label for the constructed site.

        Returns
        -------
        str
            Label of the form ``behavioral_surface_site(N_classes)``.
        """
        return f"behavioral_surface_site({len(self._surfaces)}_classes)"


# ---------------------------------------------------------------------------
# Class 3 — DescriptorChainChannelBridge
# ---------------------------------------------------------------------------


class DescriptorChainChannelBridge:
    """Maps DescriptorChain resolution steps to evidence records.

    Each step in the descriptor resolution chain is an attribute access
    event.  This bridge converts those steps into EvidenceRecord objects
    suitable for submission to the evidence channels (RUNTIME or COPILOT).

    CopilotChannel-proposed descriptor resolution sequences are admitted at
    ORACLE_PROPOSED trust.  The COPILOT_SUGGESTED ceiling invariant is
    enforced by :meth:`trust_ceiling_enforced`.

    theory2.tex Ch20 §20.8.3
    """

    def __init__(self, chains: list[DescriptorChain]) -> None:
        """Initialise the bridge with a list of descriptor chains.

        Parameters
        ----------
        chains:
            The descriptor chains to bridge.  Each chain represents one
            named attribute's full MRO-ordered resolution sequence.
        """
        self._chains: list[DescriptorChain] = list(chains)

    # --- primary evidence construction ---

    def evidence_for(
        self,
        chain: DescriptorChain,
        resolution_class: str,
    ) -> EvidenceRecord:
        """Build a runtime ``EvidenceRecord`` for a single descriptor chain.

        The record documents which class in the MRO won the descriptor
        lookup, the kind of descriptor (data vs. non-data), and the depth
        of the resolution chain.

        Parameters
        ----------
        chain:
            The descriptor chain to document.
        resolution_class:
            The name of the class that provided the winning descriptor.

        Returns
        -------
        EvidenceRecord
            A record with ``channel=RUNTIME`` and structured payload.
        """
        trust = _resolve_trust(chain.trust)
        trust_name = trust.name if hasattr(trust, "name") else str(trust)
        provenance = _build_provenance(source=ProvenanceSource.RUNTIME)
        return EvidenceRecord(
            channel=EvidenceChannel.RUNTIME,
            claim=f"descriptor_resolved({chain.attribute_name!r})",
            payload={
                "attribute_name": chain.attribute_name,
                "winner": resolution_class,
                "kind": chain.descriptor_kind,
                "chain_depth": len(chain.chain),
                "trust": trust_name,
                "owner_class": chain.owner_class,
                "override_map_keys": list(getattr(chain, "override_map", {}).keys()),
            },
            obligations=(),
            provenance=provenance,
        )

    def all_records(self, instance_class: str) -> list[EvidenceRecord]:
        """Return one ``EvidenceRecord`` per registered chain.

        For each chain the resolution class is determined by calling
        ``chain.resolve_get(instance_class)`` if available, falling back to
        ``chain.owner_class``.

        Parameters
        ----------
        instance_class:
            The runtime class of the instance performing the attribute lookup.

        Returns
        -------
        list[EvidenceRecord]
        """
        records: list[EvidenceRecord] = []
        for chain in self._chains:
            if hasattr(chain, "resolve_get") and callable(chain.resolve_get):
                try:
                    resolution_class = chain.resolve_get(instance_class)
                except Exception:
                    resolution_class = chain.owner_class
            else:
                resolution_class = chain.owner_class
            records.append(self.evidence_for(chain, resolution_class))
        return records

    def copilot_proposals(self) -> list[EvidenceRecord]:
        """Return COPILOT evidence records for chains at ORACLE_PROPOSED trust.

        Only chains whose trust level is exactly ``ORACLE_PROPOSED`` (the
        COPILOT_SUGGESTED ceiling) are included.  These records indicate that
        CopilotChannel proposed the descriptor resolution ordering and that
        explicit promotion is required before the ordering is trusted.

        Returns
        -------
        list[EvidenceRecord]
        """
        proposals: list[EvidenceRecord] = []
        for chain in self._chains:
            trust = _resolve_trust(chain.trust)
            threshold = TrustLevel.ORACLE_PROPOSED.value
            if hasattr(trust, "value") and trust.value == threshold:
                provenance = _build_provenance(source=ProvenanceSource.ORACLE)
                proposals.append(EvidenceRecord(
                    channel=EvidenceChannel.COPILOT,
                    claim=f"copilot_descriptor_proposal({chain.attribute_name!r})",
                    payload={
                        "attribute_name": chain.attribute_name,
                        "owner_class": chain.owner_class,
                        "descriptor_kind": chain.descriptor_kind,
                        "chain_depth": len(chain.chain),
                        "trust_ceiling": "ORACLE_PROPOSED",
                        "copilot_policy": "COPILOT_SUGGESTED",
                    },
                    obligations=(
                        f"promote_trust({chain.attribute_name!r})",
                        f"corroborate_with_runtime({chain.attribute_name!r})",
                    ),
                    provenance=provenance,
                ))
        return proposals

    def morphism_records(self, chain: DescriptorChain) -> list[Morphism]:
        """Return the sequence of site morphisms for a descriptor chain.

        Delegates to :meth:`DescriptorChain.as_morphism_sequence` when
        available.  If the chain does not provide that method, ``RESTRICTION``
        morphisms are built from consecutive pairs in ``chain.chain``.

        Parameters
        ----------
        chain:
            The descriptor chain to convert.

        Returns
        -------
        list[Morphism]
        """
        if hasattr(chain, "as_morphism_sequence") and callable(chain.as_morphism_sequence):
            try:
                result = chain.as_morphism_sequence()
                if result:
                    return list(result)
            except Exception:
                pass

        # Manual fallback: build RESTRICTION morphisms from consecutive chain entries
        morphisms: list[Morphism] = []
        chain_seq = list(chain.chain)
        for i in range(len(chain_seq) - 1):
            src_name = str(chain_seq[i])
            tgt_name = str(chain_seq[i + 1])
            src_coord = Coordinate(
                components=("descriptor", chain.attribute_name, src_name),
                kind=CoordinateKind.INTERFACE,
            )
            tgt_coord = Coordinate(
                components=("descriptor", chain.attribute_name, tgt_name),
                kind=CoordinateKind.INTERFACE,
            )
            morphisms.append(Morphism(
                source=src_coord,
                target=tgt_coord,
                kind=MorphismKind.RESTRICTION,
                label=f"mro_step({chain.attribute_name!r}, {src_name!r}→{tgt_name!r})",
            ))
        return morphisms

    def trust_ceiling_enforced(self, chain: DescriptorChain) -> bool:
        """Return ``True`` if the chain's trust is at or below ORACLE_PROPOSED.

        COPILOT_SUGGESTED descriptor chains must not exceed ``ORACLE_PROPOSED``
        trust.  This method acts as a guard before promotion is attempted.

        Parameters
        ----------
        chain:
            The descriptor chain to check.

        Returns
        -------
        bool
            ``True`` if the trust ceiling invariant is satisfied.
        """
        trust = _resolve_trust(chain.trust)
        ceiling = TrustLevel.ORACLE_PROPOSED.value
        if hasattr(trust, "value"):
            return trust.value <= ceiling
        return True  # conservative default: treat unknown as within ceiling


# ---------------------------------------------------------------------------
# Class 4 — ClassCreationJudgmentEmitter
# ---------------------------------------------------------------------------


class ClassCreationJudgmentEmitter:
    """Emits one Judgment per ClassCreationTrace step.

    The three-phase class creation (prepare / body / new) generates three
    distinct Judgment objects, one per phase.  Each phase has its own
    coordinate, proposition, and trust level.

    This emitter also integrates with CopilotChannel to annotate each
    phase with copilot-assisted analysis.  CopilotChannel annotations enter
    at ORACLE_PROPOSED and are flagged with the COPILOT_SUGGESTED policy tag
    so downstream consumers can enforce the promotion requirement.

    theory2.tex Ch20 §20.8.4
    """

    def __init__(self, traces: list[ClassCreationTrace]) -> None:
        """Initialise the emitter with a list of class creation traces.

        Parameters
        ----------
        traces:
            The list of :class:`ClassCreationTrace` objects to emit judgments
            for.  Typically one trace per class created in the monitored scope.
        """
        self._traces: list[ClassCreationTrace] = list(traces)

    # --- phase judgments ---

    def prepare_judgment(self, trace: ClassCreationTrace) -> Judgment:
        """Build the ``__prepare__`` phase judgment for a class creation trace.

        The prepare phase records that the metaclass has been called to
        produce an initial namespace.  The judgment coordinate is
        ``trace.namespace_coordinate`` (the namespace object's position in the
        site); the proposition asserts that the prepare phase completed
        successfully.

        Parameters
        ----------
        trace:
            The class creation trace whose prepare phase to judge.

        Returns
        -------
        Judgment
            STRUCTURAL judgment for the prepare phase.
        """
        coord = trace.namespace_coordinate or _class_coordinate(
            f"{trace.class_name}.__prepare__"
        )
        trust = _resolve_trust(trace.trust)
        formula = f"prepare_phase_complete({trace.class_name!r})"
        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(),
            metadata={
                "class_name": trace.class_name,
                "prepare_result_keys": list((trace.prepare_result or {}).keys()),
            },
        )
        status = (
            JudgmentStatus.SETTLED if trace.prepare_result else JudgmentStatus.PROPOSED
        )
        provenance = _build_provenance(
            source=ProvenanceSource.RUNTIME,
            timestamp=getattr(trace, "created_at", "") or _now_iso(),
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"phase": "prepare", "class_name": trace.class_name,
                     "prepare_keys": list((trace.prepare_result or {}).keys())},
            trust_level=trust,
            channel=EvidenceChannel.RUNTIME,
            timestamp=provenance.creation_timestamp,
            provenance=provenance,
        )
        evidence = _build_evidence_bundle(
            (evidence_item,),
            summary=f"Runtime witness: prepare_phase_complete({trace.class_name!r})",
        )
        return Judgment(
            coordinate=coord,
            proposition=proposition,
            carrier=Carrier(
                name=f"PreparePhase({trace.class_name})",
                parameters={"namespace_size": len(trace.prepare_result or {})},
            ),
            evidence=evidence,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=status,
        )

    def body_judgment(self, trace: ClassCreationTrace) -> Judgment:
        """Build the body-execution phase judgment for a class creation trace.

        The body phase records that the class body was executed, populating
        the namespace with ``len(trace.body_names)`` names.

        Parameters
        ----------
        trace:
            The class creation trace whose body phase to judge.

        Returns
        -------
        Judgment
            BEHAVIORAL judgment for the body-execution phase.
        """
        coord = trace.coordinate or _class_coordinate(trace.class_name)
        trust = _resolve_trust(trace.trust)
        body_count = len(trace.body_names or ())
        formula = f"body_executed({trace.class_name!r}, {body_count}_names)"
        proposition = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=formula,
            free_variables=(),
            metadata={
                "class_name": trace.class_name,
                "body_name_count": body_count,
                "body_names_sample": list((trace.body_names or ())[:5]),
            },
        )
        provenance = _build_provenance(
            source=ProvenanceSource.RUNTIME,
            timestamp=getattr(trace, "created_at", "") or _now_iso(),
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"phase": "body", "class_name": trace.class_name, "name_count": body_count},
            trust_level=trust,
            channel=EvidenceChannel.RUNTIME,
            timestamp=provenance.creation_timestamp,
            provenance=provenance,
        )
        evidence = _build_evidence_bundle(
            (evidence_item,),
            summary=f"Runtime witness: body_executed({trace.class_name!r})",
        )
        status = JudgmentStatus.SETTLED if body_count > 0 else JudgmentStatus.PROPOSED
        return Judgment(
            coordinate=coord,
            proposition=proposition,
            carrier=Carrier(
                name=f"BodyPhase({trace.class_name})",
                parameters={"body_name_count": body_count},
            ),
            evidence=evidence,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=status,
        )

    def creation_judgment(self, trace: ClassCreationTrace) -> Judgment:
        """Build the full class-creation judgment by delegating to the trace.

        Delegates to :meth:`ClassCreationTrace.as_judgment`.  If the trace
        does not implement that method, a structural judgment asserting class
        creation completeness is synthesised.

        Parameters
        ----------
        trace:
            The class creation trace to convert.

        Returns
        -------
        Judgment
        """
        try:
            result = trace.as_judgment()
            if result is not None:
                return result
        except Exception:
            pass

        coord = trace.coordinate or _class_coordinate(trace.class_name)
        trust = _resolve_trust(trace.trust)
        metaclass_name = (
            trace.metaclass.__name__
            if hasattr(trace.metaclass, "__name__")
            else str(trace.metaclass)
        )
        formula = f"class_created({trace.class_name!r}, metaclass={metaclass_name!r})"
        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(),
            metadata={"class_name": trace.class_name, "metaclass": metaclass_name},
        )
        provenance = _build_provenance(
            source=ProvenanceSource.RUNTIME,
            timestamp=getattr(trace, "created_at", "") or _now_iso(),
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"phase": "creation", "class_name": trace.class_name},
            trust_level=trust,
            channel=EvidenceChannel.RUNTIME,
            timestamp=provenance.creation_timestamp,
            provenance=provenance,
        )
        evidence = _build_evidence_bundle((evidence_item,))
        status = JudgmentStatus.SETTLED if trust.value >= TrustLevel.RUNTIME_WITNESSED.value else JudgmentStatus.PROPOSED
        return Judgment(
            coordinate=coord,
            proposition=proposition,
            carrier=Carrier(
                name=f"ClassCreation({trace.class_name})",
                parameters={"metaclass": metaclass_name},
            ),
            evidence=evidence,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=status,
        )

    # --- emission ---

    def emit_all(self, trace: ClassCreationTrace) -> list[Judgment]:
        """Emit all three phase judgments for a single creation trace.

        Returns the prepare, body, and creation judgments in order.

        Parameters
        ----------
        trace:
            The creation trace to emit judgments for.

        Returns
        -------
        list[Judgment]
            A three-element list ``[prepare_judgment, body_judgment, creation_judgment]``.
        """
        return [
            self.prepare_judgment(trace),
            self.body_judgment(trace),
            self.creation_judgment(trace),
        ]

    def all_judgments(self) -> list[Judgment]:
        """Flatten :meth:`emit_all` across all registered traces.

        Returns
        -------
        list[Judgment]
            Three judgments per trace, in trace order.
        """
        result: list[Judgment] = []
        for trace in self._traces:
            result.extend(self.emit_all(trace))
        return result

    def copilot_annotation(self, trace: ClassCreationTrace) -> EvidenceRecord:
        """Emit a CopilotChannel annotation record for a class creation trace.

        The annotation captures the full creation context (class name,
        metaclass, body name count, init_subclass_called) and marks it as
        a COPILOT_SUGGESTED analysis record.  Downstream consumers must
        promote this record explicitly before trusting the annotation.

        Parameters
        ----------
        trace:
            The creation trace to annotate.

        Returns
        -------
        EvidenceRecord
            A CopilotChannel record at ORACLE_PROPOSED trust.
        """
        metaclass_name = (
            trace.metaclass.__name__
            if hasattr(trace.metaclass, "__name__")
            else str(trace.metaclass)
        )
        provenance = _build_provenance(source=ProvenanceSource.ORACLE)
        return EvidenceRecord(
            channel=EvidenceChannel.COPILOT,
            claim=f"copilot_creation_annotation({trace.class_name!r})",
            payload={
                "class_name": trace.class_name,
                "metaclass": metaclass_name,
                "body_name_count": len(trace.body_names or ()),
                "body_names_sample": list((trace.body_names or ())[:8]),
                "init_subclass_called": trace.init_subclass_called,
                "prepare_result_keys": list((trace.prepare_result or {}).keys()),
                "trust_ceiling": "ORACLE_PROPOSED",
                "copilot_policy": "COPILOT_SUGGESTED",
            },
            obligations=(
                f"review_creation_annotation({trace.class_name!r})",
                f"promote_if_corroborated({trace.class_name!r})",
            ),
            provenance=provenance,
        )
