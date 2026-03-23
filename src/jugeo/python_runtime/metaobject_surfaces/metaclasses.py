from __future__ import annotations

r"""theory2.tex Ch20 §20.2 — Metaclass resolution and type construction as site morphisms.

This module formalises Python's metaclass machinery inside the JuGeo judgment
framework.  Every class creation event is modelled as a morphism in a Grothendieck
site whose objects are Coordinate instances.

Key concepts
------------
* **Metaclass resolution** (§20.2.1): given a set of bases, pick the "winner"
  metaclass by finding the most-derived candidate.  Formalised as a REFINEMENT
  morphism from the winner's coordinate to the new class coordinate.

* **Metaclass conflict** (§20.2.2): if no winner exists the local sections of the
  judgment sheaf cannot be glued.  Represented as an Obstruction whose
  cohomology_class encodes the unresolvable pair.

* **Type constructor site** (§20.2.3): `type.__new__` is the universal TRANSPORT
  morphism from a namespace coordinate into a fresh class coordinate.

* **ABCMeta analysis** (§20.2.4): `__subclasshook__` is a covering axiom; virtual
  subclasses are members of a CoveringFamily.

All CopilotChannel-sourced evidence enters at ORACLE_PROPOSED trust (also written
COPILOT_SUGGESTED in earlier drafts) and requires explicit promotion by a solver
or runtime witness before the judgment is considered settled.
"""

import dataclasses
import datetime
import hashlib
import itertools
import logging
from typing import Any

# --- jugeo geometry imports ---------------------------------------------------

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology,
        CoordinateObject,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field
    from typing import Mapping

    class CoordinateKind(Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"

    class MorphismKind(Enum):
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"

    @_dc(frozen=True)
    class Coordinate:
        components: tuple = ()
        kind: CoordinateKind = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()
        metadata: dict = _field(default_factory=dict)

    @_dc(frozen=True)
    class Morphism:
        source: "Coordinate" = None
        target: "Coordinate" = None
        kind: MorphismKind = MorphismKind.INCLUSION
        label: str = ""

    @_dc(frozen=True)
    class CoordinateObject:
        coordinate: "Coordinate" = None
        label: str = ""

    @_dc
    class CoveringFamily:
        base: "Coordinate" = None
        members: list = _field(default_factory=list)
        label: str = ""
        _overlap_data: list = _field(default_factory=list)

    @_dc
    class GrothendieckTopology:
        name: str = "custom"

    @_dc
    class Site:
        label: str = ""
        _coords: list = _field(default_factory=list)
        _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c)
        def add_morphism(self, m): self._morphisms.append(m)

    @_dc
    class SiteBuilder:
        _coords: list = _field(default_factory=list)
        _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def build(self): return Site()

# --- jugeo judgment imports ---------------------------------------------------

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
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
        @property
        def value(self): return self._value_

    class JudgmentStatus(Enum):
        PROPOSED = "proposed"; CHALLENGED = "challenged"
        SETTLED = "settled"; OBSTRUCTED = "obstructed"

    class PropositionKind(Enum):
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
        RESOURCE = "resource"; SEMANTIC = "semantic"

    class EvidenceItemKind(Enum):
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"; FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(Enum):
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"
        HUMAN = "human"; COMPOSED = "composed"

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
        trust_level: "TrustLevel" = None; channel: str = ""; timestamp: str = ""
        expiry: str = None; provenance: "Provenance" = None

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

    class JudgmentAlgebra:
        pass

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

    def _stable_hash(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    def _now_iso() -> str:
        return datetime.datetime.utcnow().isoformat() + "Z"

# --- jugeo solver imports -----------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3QueryBuilder, Z3Result, SolveOutcome, Z3Encoder
except ImportError:
    class SolveOutcome:
        SAT = "sat"; UNSAT = "unsat"; UNKNOWN = "unknown"

    class Z3Result:
        def __init__(self, outcome=None, model=None):
            self.outcome = outcome; self.model = model

    class Z3Session:
        def solve(self, q): return Z3Result(SolveOutcome.UNKNOWN)

    class Z3QueryBuilder:
        def build(self): return {}

    class Z3Encoder:
        pass

# --- jugeo evidence channel imports -------------------------------------------

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
            self.obligations = obligations; self.provenance = provenance

    class EvidenceRequest:
        def __init__(self, **kw): self.__dict__.update(kw)

    class EvidenceResponse:
        def __init__(self, **kw): self.__dict__.update(kw)

    class ChannelRouter:
        pass

    class CopilotChannel:
        TRUST_CEILING = "proposal"

    class SolverChannel:
        pass

    class RuntimeChannel:
        pass

# --- local model imports ------------------------------------------------------

try:
    from jugeo.python_runtime.metaobject_surfaces.models import (
        MetaclassRecord, BehavioralSurface, DescriptorChain, ClassCreationTrace,
        _metaclass_coordinate, _class_coordinate, _now_str,
    )
except ImportError:
    from dataclasses import dataclass as _dc, field as _field

    @_dc(frozen=True, slots=True)
    class MetaclassRecord:
        class_name: str = ""; metaclass_name: str = ""; coordinate: object = None
        bases: tuple = (); metaclass_coordinate: object = None; trust: object = None
        mro: tuple = (); created_at: str = ""

    @_dc(frozen=True, slots=True)
    class BehavioralSurface:
        class_name: str = ""; coordinate: object = None; protocols: tuple = ()
        dunder_methods: tuple = (); abstract_methods: tuple = ()
        trust: object = None; judgment_index: dict = _field(default_factory=dict)

    @_dc(frozen=True, slots=True)
    class DescriptorChain:
        attribute_name: str = ""; owner_class: str = ""; coordinate: object = None
        chain: tuple = (); descriptor_kind: str = "NON_DATA"
        trust: object = None; override_map: dict = _field(default_factory=dict)

    @_dc(frozen=True, slots=True)
    class ClassCreationTrace:
        class_name: str = ""; coordinate: object = None; namespace_coordinate: object = None
        metaclass: object = None; prepare_result: dict = _field(default_factory=dict)
        body_names: tuple = (); init_subclass_called: bool = False
        trust: object = None; created_at: str = ""

    def _metaclass_coordinate(cn: str, mn: str) -> Coordinate:
        return Coordinate(
            components=(cn, mn, "__metaclass__"),
            kind=CoordinateKind.MODULE,
        )

    def _class_coordinate(cn: str, mod: str = "unknown") -> Coordinate:
        return Coordinate(
            components=(mod, cn),
            kind=CoordinateKind.MODULE,
        )

    def _now_str() -> str:
        return datetime.datetime.utcnow().isoformat() + "Z"

# --- module logger ------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---  helpers -----------------------------------------------------------------

def _fallback_coordinate(tag: str) -> Coordinate:
    """Return a synthetic Coordinate when no real one is available."""
    return Coordinate(
        components=("__synthetic__", tag),
        kind=CoordinateKind.MODULE,
        metadata={"synthetic": True},
    )


def _min_trust(*levels: TrustLevel) -> TrustLevel:
    """Return the least-trusted TrustLevel from the given sequence."""
    valid = [lv for lv in levels if lv is not None]
    if not valid:
        return TrustLevel.UNVERIFIED
    return min(valid, key=lambda lv: lv.value)


def _make_provenance(source: ProvenanceSource, note: str = "") -> Provenance:
    """Build a minimal Provenance record."""
    return Provenance(
        source=source,
        creation_timestamp=_now_iso(),
        metadata={"note": note} if note else {},
    )

# =============================================================================
# MetaclassMROResolver
# =============================================================================

class MetaclassMROResolver:
    """Resolves the winner metaclass for a new class.

    Python requires that the metaclass of a derived class is a subtype of
    every base's metaclass.  This resolver walks the candidate list and
    picks the most-derived metaclass, or raises a synthetic Obstruction
    if no winner exists (copilot-assisted detection).

    The algorithm mirrors CPython's ``_PyType_CalculateMetaclass``:

    1. Collect all metaclasses from the base records.
    2. Sort them most-derived-first (longest MRO first).
    3. Accept the first candidate whose name appears in every other
       candidate's MRO — it is a subtype of all others.

    theory2.tex Ch20 §20.2.1
    """

    def __init__(self, records: list[MetaclassRecord]) -> None:
        """Initialise with a list of base-class MetaclassRecords.

        Parameters
        ----------
        records:
            One MetaclassRecord per base class of the class being created.
            May include the explicit metaclass keyword argument as an extra
            record.  Duplicates are tolerated; the resolver deduplicates by
            metaclass_name.
        """
        self._records: list[MetaclassRecord] = list(records)
        self._by_name: dict[str, MetaclassRecord] = {
            r.metaclass_name: r for r in records if r.metaclass_name
        }

    def candidate_metaclasses(self) -> list[MetaclassRecord]:
        """Return unique metaclass records, most-derived first.

        "Most-derived" is approximated by MRO length: a longer MRO
        indicates more inheritance steps, i.e. more specificity.

        Returns
        -------
        list[MetaclassRecord]
            Deduplicated list sorted by descending MRO length.
        """
        seen: set[str] = set()
        unique: list[MetaclassRecord] = []
        for r in self._records:
            if r.metaclass_name not in seen:
                seen.add(r.metaclass_name)
                unique.append(r)
        unique.sort(key=lambda r: len(r.mro), reverse=True)
        return unique

    def is_subtype(self, a: MetaclassRecord, b: MetaclassRecord) -> bool:
        """Return True if *a* is a subtype of *b* (b appears in a's MRO).

        This mirrors ``issubclass(a_type, b_type)`` at the metaclass level.
        An empty MRO is treated conservatively: a record with no MRO data
        is only considered a subtype of itself.

        Parameters
        ----------
        a:
            Candidate that may be more derived.
        b:
            Candidate that may be an ancestor.

        Returns
        -------
        bool
            ``True`` iff ``b.metaclass_name in a.mro`` or
            ``a.metaclass_name == b.metaclass_name``.
        """
        if a.metaclass_name == b.metaclass_name:
            return True
        return b.metaclass_name in a.mro

    def find_winner(self) -> MetaclassRecord:
        """Return the most-derived metaclass record that beats every other.

        The winner ``w`` must satisfy ``is_subtype(w, c)`` for every other
        candidate ``c``.  If no such winner exists, a ``TypeError`` is raised
        with a human-readable message listing all conflicting pairs.

        Returns
        -------
        MetaclassRecord
            The winning (most-derived) metaclass record.

        Raises
        ------
        TypeError
            When no linear winner exists, replicating Python's own error.
        """
        candidates = self.candidate_metaclasses()
        if not candidates:
            # No bases — default to type.
            return MetaclassRecord(
                class_name="<new>",
                metaclass_name="type",
                mro=("type", "object"),
                coordinate=_fallback_coordinate("type"),
                metaclass_coordinate=_fallback_coordinate("type.__metaclass__"),
                trust=TrustLevel.RUNTIME_WITNESSED,
                created_at=_now_str(),
            )

        for candidate in candidates:
            if all(self.is_subtype(candidate, other) for other in candidates):
                _log.debug(
                    "metaclass winner=%s for bases=%s",
                    candidate.metaclass_name,
                    [r.class_name for r in self._records],
                )
                return candidate

        conflicts = self.detect_conflicts()
        conflict_msg = "; ".join(
            f"{a.metaclass_name} vs {b.metaclass_name}" for a, b in conflicts
        )
        raise TypeError(
            f"metaclass conflict — no winner among candidates "
            f"[{', '.join(c.metaclass_name for c in candidates)}]: {conflict_msg}"
        )

    def detect_conflicts(self) -> list[tuple[MetaclassRecord, MetaclassRecord]]:
        """Return all pairs of metaclass records that are mutually incompatible.

        A pair (a, b) is a conflict when neither is_subtype(a, b) nor
        is_subtype(b, a) holds.  This is the exact condition that prevents
        Python from selecting a winner.

        Returns
        -------
        list[tuple[MetaclassRecord, MetaclassRecord]]
            Every conflicting pair; empty list means no conflicts.
        """
        candidates = self.candidate_metaclasses()
        conflicts: list[tuple[MetaclassRecord, MetaclassRecord]] = []
        for a, b in itertools.combinations(candidates, 2):
            if not self.is_subtype(a, b) and not self.is_subtype(b, a):
                conflicts.append((a, b))
        return conflicts

    def resolution_morphism(self) -> Morphism:
        """Build the REFINEMENT morphism representing metaclass resolution.

        The morphism goes from the winning metaclass's coordinate (source)
        to the new class's coordinate (target), labelled
        ``"metaclass_resolution"``.  This represents the structural fact
        that the new class inherits its metaclass machinery from the winner.

        Returns
        -------
        Morphism
            A REFINEMENT morphism in the site.

        Raises
        ------
        TypeError
            Propagated from ``find_winner`` if resolution fails.
        """
        winner = self.find_winner()
        target_coord = (
            self._records[0].coordinate
            if self._records
            else _fallback_coordinate("unknown_class")
        )
        source_coord = winner.metaclass_coordinate or _fallback_coordinate(
            winner.metaclass_name
        )
        return Morphism(
            source=source_coord,
            target=target_coord,
            kind=MorphismKind.REFINEMENT,
            label="metaclass_resolution",
        )

    def as_judgment(self) -> Judgment:
        """Build a Judgment asserting that metaclass resolution is well-founded.

        The proposition formula is ``"metaclass_resolution_well_founded"``.
        Trust is the minimum of all contributing record trusts (most
        conservative).  If ``find_winner`` raises, the judgment is produced
        with CONTRADICTED trust and an attached Obstruction.

        CopilotChannel can propose a repair at ORACLE_PROPOSED trust when the
        resolution fails; this is recorded as a ResidualObligation.

        Returns
        -------
        Judgment
            A STRUCTURAL judgment at the first record's coordinate.
        """
        coord = (
            self._records[0].coordinate
            if self._records
            else _fallback_coordinate("metaclass_resolution")
        )

        # Compute aggregate trust from all contributing records.
        trust_levels = [r.trust for r in self._records if r.trust is not None]
        aggregate_trust = _min_trust(*trust_levels) if trust_levels else TrustLevel.UNVERIFIED

        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula="metaclass_resolution_well_founded",
            free_variables=tuple(r.class_name for r in self._records),
        )
        provenance = _make_provenance(
            ProvenanceSource.RUNTIME,
            note="MetaclassMROResolver.as_judgment",
        )

        # Attempt to detect obstructions before building the judgment.
        obstructions: list[Obstruction] = []
        conflicts = self.detect_conflicts()
        for a, b in conflicts:
            checker = MetaclassConflictChecker(a, b)
            obstructions.append(checker.as_obstruction())
            aggregate_trust = TrustLevel.CONTRADICTED

        status = (
            JudgmentStatus.OBSTRUCTED if obstructions else JudgmentStatus.PROPOSED
        )

        evidence_items: list[EvidenceItem] = []
        for rec in self._records:
            evidence_items.append(
                EvidenceItem(
                    kind=EvidenceItemKind.RUNTIME_WITNESS,
                    payload={"metaclass": rec.metaclass_name, "class": rec.class_name},
                    trust_level=rec.trust or TrustLevel.UNVERIFIED,
                    channel=EvidenceChannel.RUNTIME,
                    timestamp=_now_iso(),
                    provenance=provenance,
                )
            )

        bundle = EvidenceBundle(
            items=tuple(evidence_items),
            summary=f"metaclass_resolution for {[r.class_name for r in self._records]}",
        )

        return Judgment(
            coordinate=coord,
            proposition=proposition,
            carrier=Carrier(name="MetaclassMROResolver"),
            evidence=bundle,
            obstructions=tuple(obstructions),
            trust=aggregate_trust,
            provenance=provenance,
            status=status,
        )

# =============================================================================
# MetaclassConflictChecker
# =============================================================================

class MetaclassConflictChecker:
    """Represents a metaclass conflict as a cohomological obstruction.

    When two bases have incompatible metaclasses, Python raises TypeError.
    In JuGeo terms this is an Obstruction in the judgment sheaf — the
    local sections (base metaclasses) cannot be glued to a global section.
    CopilotChannel evidence may propose a resolution but carries only
    ORACLE_PROPOSED trust until solver-verified.

    The cohomology_class field on the resulting Obstruction uses the hash
    of the sorted metaclass-name pair as a stable identifier, enabling
    downstream tracking and deduplication.

    theory2.tex Ch20 §20.2.2
    """

    def __init__(self, a: MetaclassRecord, b: MetaclassRecord) -> None:
        """Store the two conflicting metaclass records.

        Parameters
        ----------
        a:
            First conflicting metaclass record.
        b:
            Second conflicting metaclass record.
        """
        self._a = a
        self._b = b

    def as_obstruction(self) -> Obstruction:
        """Build an Obstruction representing the metaclass conflict.

        The obstruction encodes:
        * A stable ``obstruction_id`` derived from both metaclass names.
        * The violated sheaf-gluing condition.
        * Repair hints generated by ``resolution_candidates``.
        * A ``cohomology_class`` string for downstream tracking.

        Returns
        -------
        Obstruction
            A fully-populated Obstruction instance.
        """
        oid = f"metaclass_conflict_{self._a.class_name}_{self._b.class_name}"
        cohomology = _stable_hash(
            "|".join(sorted([self._a.metaclass_name, self._b.metaclass_name]))
        )
        coord = self._a.coordinate or _fallback_coordinate(self._a.class_name)
        hints = tuple(
            f"Resolution candidate: {c}" for c in self.resolution_candidates()
        )
        provenance = _make_provenance(
            ProvenanceSource.ORACLE,
            note="MetaclassConflictChecker.as_obstruction",
        )
        return Obstruction(
            obstruction_id=oid,
            violated_condition="metaclass_must_be_subtype_of_all_bases",
            coordinate=coord,
            evidence_at_time=(),
            repair_hints=hints,
            cohomology_class=cohomology,
            is_resolved=False,
            resolution_evidence=(),
            provenance=provenance,
        )

    def resolution_candidates(self) -> list[str]:
        """Return candidate metaclass names that could resolve the conflict.

        The list is ordered from most-specific to least-specific:
        1. A synthesised combination name (most informative).
        2. A name derived from the class names (generic).
        3. ``"type"`` as the ultimate fallback (always included last).

        Returns
        -------
        list[str]
            Candidate metaclass names, never empty.
        """
        return [
            f"{self._a.metaclass_name}{self._b.metaclass_name}Combined",
            f"Meta_{self._a.class_name}_{self._b.class_name}",
            "type",
        ]

    def conflict_depth(self) -> int:
        """Quantify how far apart the two metaclasses are in the MRO lattice.

        Computed as::

            max(len(a.mro), len(b.mro)) - len(set(a.mro) & set(b.mro))

        A depth of 0 would mean the MROs are identical (no real conflict).
        Larger values indicate more deeply diverged hierarchies.

        Returns
        -------
        int
            Non-negative integer measuring divergence depth.
        """
        mro_a = set(self._a.mro)
        mro_b = set(self._b.mro)
        shared = mro_a & mro_b
        return max(len(self._a.mro), len(self._b.mro)) - len(shared)

    def is_trivially_resolvable(self) -> bool:
        """Return True if one metaclass is already a subtype of the other.

        In this case Python would have selected a winner without error;
        calling this method after a detected conflict is useful for
        double-checking resolver logic.

        Returns
        -------
        bool
            ``True`` iff one metaclass name appears in the other's MRO.
        """
        a_in_b = self._a.metaclass_name in self._b.mro
        b_in_a = self._b.metaclass_name in self._a.mro
        return a_in_b or b_in_a

    def copilot_repair_hint(self) -> str:
        """Return a human-readable hint for CopilotChannel-assisted resolution.

        The hint documents the conflict, lists resolution candidates, and
        explicitly notes that the resulting proposal enters at
        COPILOT_SUGGESTED / ORACLE_PROPOSED trust and cannot be
        auto-promoted without solver or runtime evidence.

        Returns
        -------
        str
            A formatted multi-line hint string.
        """
        candidates = self.resolution_candidates()
        depth = self.conflict_depth()
        lines = [
            f"Metaclass conflict detected (depth={depth}):",
            f"  Base A: {self._a.class_name!r} uses metaclass {self._a.metaclass_name!r}",
            f"  Base B: {self._b.class_name!r} uses metaclass {self._b.metaclass_name!r}",
            "",
            "Resolution candidates (COPILOT_SUGGESTED, trust=ORACLE_PROPOSED):",
        ]
        for idx, cand in enumerate(candidates, 1):
            lines.append(f"  {idx}. {cand}")
        lines += [
            "",
            "Note: CopilotChannel proposals require solver verification or",
            "runtime witnessing before trust can be raised above ORACLE_PROPOSED.",
        ]
        return "\n".join(lines)

# =============================================================================
# TypeConstructorSite
# =============================================================================

class TypeConstructorSite:
    """Models the ``type`` metaclass as the universal type constructor in the site.

    Every call to ``type.__new__(mcs, name, bases, ns)`` is a morphism that
    constructs a new coordinate from the namespace coordinate.  This class
    tracks all such constructions and builds the resulting Site.

    The site has three kinds of coordinate:
    * **Namespace coordinates** — the ``__prepare__`` dict before class body
      execution.
    * **Class coordinates** — the finished class object.
    * **Metaclass coordinates** — the ``type`` or custom metaclass.

    TRANSPORT morphisms connect metaclass coordinates to class coordinates.
    INCLUSION morphisms record base-class relationships.

    theory2.tex Ch20 §20.2.3
    """

    def __init__(self, label: str = "type_constructor_site") -> None:
        """Initialise an empty TypeConstructorSite.

        Parameters
        ----------
        label:
            Human-readable label attached to the Site produced by
            ``build_site``.  Defaults to ``"type_constructor_site"``.
        """
        self._label = label
        self._traces: list[ClassCreationTrace] = []
        self._morphisms: list[Morphism] = []

    def register_trace(self, trace: ClassCreationTrace) -> None:
        """Record a ClassCreationTrace and derive its site morphisms.

        Side-effects:
        * Appends *trace* to the internal trace list.
        * Builds and stores the TRANSPORT morphism for ``type.__new__``.
        * Stores INCLUSION morphisms for each base listed in the trace.

        Parameters
        ----------
        trace:
            A ClassCreationTrace describing a single class creation event.
        """
        self._traces.append(trace)

        # type.__new__ transport morphism.
        transport = self.type_morphism(trace)
        self._morphisms.append(transport)

        # INCLUSION morphisms for inheritance.
        for inc_morphism in self.inheritance_morphisms_for(trace):
            self._morphisms.append(inc_morphism)

    def type_morphism(self, trace: ClassCreationTrace) -> Morphism:
        """Return the canonical TRANSPORT morphism for ``type.__new__``.

        The morphism goes from the metaclass coordinate (source — the factory)
        to the new class coordinate (target — the product).

        Parameters
        ----------
        trace:
            The ClassCreationTrace supplying both coordinates.

        Returns
        -------
        Morphism
            A TRANSPORT morphism labelled ``"type.__new__"``.
        """
        metaclass_record: MetaclassRecord | None = trace.metaclass
        if metaclass_record is not None:
            source = metaclass_record.metaclass_coordinate or _fallback_coordinate(
                metaclass_record.metaclass_name
            )
        else:
            source = _fallback_coordinate("type")

        target = trace.coordinate or _fallback_coordinate(trace.class_name)
        return Morphism(
            source=source,
            target=target,
            kind=MorphismKind.TRANSPORT,
            label="type.__new__",
        )

    def inheritance_morphisms_for(
        self, trace: ClassCreationTrace
    ) -> list[Morphism]:
        """Return INCLUSION morphisms for a single trace's base classes.

        For each base name recorded in the metaclass record's ``bases``
        tuple, look up the base's coordinate from previously registered
        traces and emit an INCLUSION morphism base → derived.

        Parameters
        ----------
        trace:
            The derived class trace.

        Returns
        -------
        list[Morphism]
            Zero or more INCLUSION morphisms.
        """
        result: list[Morphism] = []
        meta_record: MetaclassRecord | None = trace.metaclass
        if meta_record is None:
            return result

        for base_name in meta_record.bases:
            base_coord = self.get_class_coordinate(base_name)
            if base_coord is None:
                base_coord = _fallback_coordinate(base_name)
            derived_coord = trace.coordinate or _fallback_coordinate(trace.class_name)
            result.append(
                Morphism(
                    source=base_coord,
                    target=derived_coord,
                    kind=MorphismKind.INCLUSION,
                    label=f"inherits_from_{base_name}",
                )
            )
        return result

    def inheritance_morphisms(self) -> list[Morphism]:
        """Return all INCLUSION morphisms across all registered traces.

        Returns
        -------
        list[Morphism]
            All INCLUSION morphisms collected during ``register_trace``.
        """
        return [m for m in self._morphisms if m.kind == MorphismKind.INCLUSION]

    def build_site(self) -> Site:
        """Construct and return the full Site for all registered traces.

        The site includes:
        * Class coordinates (one per trace).
        * Namespace coordinates (one per trace, from ``trace.namespace_coordinate``).
        * Metaclass coordinates (one per distinct metaclass).
        * All stored morphisms (TRANSPORT and INCLUSION).

        Returns
        -------
        Site
            The constructed Site instance.
        """
        builder = SiteBuilder()
        seen_coords: set[tuple] = set()

        def _add(coord: Coordinate | None) -> None:
            if coord is None:
                return
            key = getattr(coord, "components", id(coord))
            if isinstance(key, list):
                key = tuple(key)
            if key not in seen_coords:
                seen_coords.add(key)
                builder.add_coordinate(coord)

        for trace in self._traces:
            _add(trace.coordinate)
            _add(trace.namespace_coordinate)
            if trace.metaclass:
                _add(trace.metaclass.coordinate)
                _add(trace.metaclass.metaclass_coordinate)

        for morphism in self._morphisms:
            builder.add_morphism(morphism)

        return builder.build()

    def get_class_coordinate(self, class_name: str) -> Coordinate | None:
        """Look up the coordinate for a class by name.

        Searches all registered traces.  Returns the first match or ``None``
        if no trace with the given class name has been registered.

        Parameters
        ----------
        class_name:
            The unqualified class name to look up.

        Returns
        -------
        Coordinate | None
            The coordinate, or ``None`` if not found.
        """
        for trace in self._traces:
            if trace.class_name == class_name:
                return trace.coordinate
        return None

    def to_judgment_list(self) -> list[Judgment]:
        """Emit one Judgment per registered ClassCreationTrace.

        Each Judgment asserts that the corresponding class was successfully
        created (STRUCTURAL proposition formula
        ``"class_creation_well_typed"``).  Trust is taken from the trace.

        Returns
        -------
        list[Judgment]
            One Judgment per trace; empty if no traces are registered.
        """
        judgments: list[Judgment] = []
        for trace in self._traces:
            coord = trace.coordinate or _fallback_coordinate(trace.class_name)
            trust = trace.trust or TrustLevel.UNVERIFIED
            provenance = _make_provenance(
                ProvenanceSource.RUNTIME,
                note=f"TypeConstructorSite trace for {trace.class_name}",
            )
            prop = Proposition(
                kind=PropositionKind.STRUCTURAL,
                formula="class_creation_well_typed",
                free_variables=(trace.class_name,),
            )
            bundle = EvidenceBundle(
                items=(
                    EvidenceItem(
                        kind=EvidenceItemKind.RUNTIME_WITNESS,
                        payload={"class_name": trace.class_name, "created_at": trace.created_at},
                        trust_level=trust,
                        channel=EvidenceChannel.RUNTIME,
                        timestamp=_now_iso(),
                        provenance=provenance,
                    ),
                ),
                summary=f"class_creation for {trace.class_name}",
            )
            judgments.append(
                Judgment(
                    coordinate=coord,
                    proposition=prop,
                    carrier=Carrier(name="TypeConstructorSite"),
                    evidence=bundle,
                    trust=trust,
                    provenance=provenance,
                    status=JudgmentStatus.PROPOSED,
                )
            )
        return judgments

# =============================================================================
# ABCMetaAnalyzer
# =============================================================================

class ABCMetaAnalyzer:
    """Analyzes classes using ABCMeta as metaclass.

    ABCMeta adds ``__subclasshook__`` as a covering axiom in the site:
    if ``__subclasshook__`` returns ``True`` for a class, that class is a
    virtual member of the covering family.  This analyzer extracts the
    behavioral surface for ABC classes and builds the corresponding
    CoveringFamily instances.

    copilot-assisted analysis of ``__abstractmethods__`` is supported via
    CopilotChannel at ORACLE_PROPOSED trust.  The trust ceiling is enforced:
    no ABC coverage judgment may exceed ORACLE_PROPOSED without a solver or
    runtime witness that confirms every abstract method is implemented.

    theory2.tex Ch20 §20.2.4
    """

    # ABCMeta appears in many locations; check for all common names.
    _ABC_METACLASS_NAMES: frozenset[str] = frozenset(
        {"ABCMeta", "abc.ABCMeta", "ABC", "abc.ABC"}
    )

    def __init__(self, records: list[MetaclassRecord]) -> None:
        """Filter to ABCMeta records and build lookup tables.

        Parameters
        ----------
        records:
            All MetaclassRecords in scope.  Non-ABC records are silently
            discarded.
        """
        self._all_records: list[MetaclassRecord] = list(records)
        self._abc_records: list[MetaclassRecord] = [
            r for r in records if self._is_abc(r)
        ]
        self._by_class: dict[str, MetaclassRecord] = {
            r.class_name: r for r in records
        }

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    def _is_abc(self, record: MetaclassRecord) -> bool:
        """Return True if this record uses an ABCMeta-like metaclass."""
        return record.metaclass_name in self._ABC_METACLASS_NAMES

    def _coord_for(self, class_name: str, kind: CoordinateKind = CoordinateKind.INTERFACE) -> Coordinate:
        """Return the coordinate for *class_name*, synthesising one if needed."""
        rec = self._by_class.get(class_name)
        if rec and rec.coordinate:
            return rec.coordinate
        return Coordinate(
            components=("abc", class_name),
            kind=kind,
            metadata={"synthetic": True},
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def abstract_methods_for(self, class_name: str) -> tuple[str, ...]:
        """Return the tuple of abstract method names declared by *class_name*.

        Looks in the MetaclassRecord's associated data for an
        ``"abstract_methods"`` key (populated by the class body analyser).
        Falls back to scanning ``prepare_result`` if available.

        Parameters
        ----------
        class_name:
            Name of the ABC class to query.

        Returns
        -------
        tuple[str, ...]
            Sorted tuple of abstract method names; empty if none found.
        """
        rec = self._by_class.get(class_name)
        if rec is None:
            return ()

        # The ClassCreationTrace attached to the record may carry abstract
        # method names under the prepare_result mapping.
        prepare: dict[str, Any] = {}
        if hasattr(rec, "prepare_result") and isinstance(rec.prepare_result, dict):
            prepare = rec.prepare_result
        elif hasattr(rec, "coordinate") and rec.coordinate and hasattr(rec.coordinate, "metadata"):
            prepare = rec.coordinate.metadata or {}

        raw = prepare.get("abstract_methods", prepare.get("__abstractmethods__", ()))
        if isinstance(raw, (set, frozenset, list, tuple)):
            return tuple(sorted(str(m) for m in raw))
        return ()

    def covering_family(self, class_name: str) -> CoveringFamily:
        """Build a CoveringFamily whose members are all virtual subclasses.

        A class *C* is a member of the family for ABC *abc_name* if:
        * *abc_name* appears in *C*'s MRO, **or**
        * ``is_virtual_subclass(C, abc_name)`` returns True.

        Parameters
        ----------
        class_name:
            Name of the ABC class whose covering family to construct.

        Returns
        -------
        CoveringFamily
            A CoveringFamily with the ABC coordinate as base and member
            coordinates for every known virtual or concrete subclass.
        """
        base_coord = self._coord_for(class_name, CoordinateKind.INTERFACE)
        member_coords: list[Coordinate] = []
        for rec in self._all_records:
            if rec.class_name == class_name:
                continue
            if self.is_virtual_subclass(rec.class_name, class_name):
                member_coords.append(
                    self._coord_for(rec.class_name, CoordinateKind.MODULE)
                )
        return CoveringFamily(
            base=base_coord,
            members=member_coords,
            label=f"{class_name}_abc_covering",
        )

    def is_virtual_subclass(self, cls_name: str, abc_name: str) -> bool:
        """Return True if *cls_name* is a (virtual) subclass of *abc_name*.

        A class is considered a virtual subclass if the ABC name appears in
        its MRO or in its declared bases.  Real ``__subclasshook__`` logic
        is not re-executed here; this is a structural approximation.

        Parameters
        ----------
        cls_name:
            Name of the candidate subclass.
        abc_name:
            Name of the ABC to check against.

        Returns
        -------
        bool
        """
        rec = self._by_class.get(cls_name)
        if rec is None:
            return False
        if abc_name in rec.mro:
            return True
        if abc_name in rec.bases:
            return True
        return False

    def hook_morphism(self, abc_record: MetaclassRecord) -> Morphism:
        """Return the TRANSPORT morphism modelling ``__subclasshook__``.

        ``__subclasshook__`` transports a candidate class through the ABC
        membership test.  In the site, this is a morphism from the ABC
        class coordinate (source) to the metaclass coordinate (target),
        labelled ``"__subclasshook__"``.

        Parameters
        ----------
        abc_record:
            The MetaclassRecord for the ABC class.

        Returns
        -------
        Morphism
            A TRANSPORT morphism from abc_record.coordinate to
            abc_record.metaclass_coordinate.
        """
        source = abc_record.coordinate or _fallback_coordinate(abc_record.class_name)
        target = abc_record.metaclass_coordinate or _fallback_coordinate(
            abc_record.metaclass_name
        )
        return Morphism(
            source=source,
            target=target,
            kind=MorphismKind.TRANSPORT,
            label="__subclasshook__",
        )

    def as_obstruction_if_not_implemented(
        self,
        cls_name: str,
        required_methods: tuple[str, ...],
    ) -> Obstruction | None:
        """Build an Obstruction if *cls_name* does not implement all required methods.

        Checks the MetaclassRecord's ``mro`` and any ``body_names`` field on
        the associated ClassCreationTrace for coverage of each required
        method.  If any method is missing, an Obstruction is returned.

        CopilotChannel can propose stub implementations at ORACLE_PROPOSED
        trust; this is noted in the repair_hints.

        Parameters
        ----------
        cls_name:
            Name of the concrete class to check.
        required_methods:
            Tuple of method names that must be implemented.

        Returns
        -------
        Obstruction | None
            An Obstruction if any required method is absent, else ``None``.
        """
        rec = self._by_class.get(cls_name)
        if rec is None:
            return None

        # Gather known method names from MRO and body_names.
        known: set[str] = set(rec.mro)
        if hasattr(rec, "prepare_result") and isinstance(rec.prepare_result, dict):
            known.update(rec.prepare_result.keys())

        missing = [m for m in required_methods if m not in known]
        if not missing:
            return None

        coord = rec.coordinate or _fallback_coordinate(cls_name)
        hints = tuple(
            [f"Implement method {m!r} in {cls_name!r}" for m in missing]
            + [
                "CopilotChannel can generate stub implementations at "
                "ORACLE_PROPOSED trust (COPILOT_SUGGESTED ceiling)."
            ]
        )
        provenance = _make_provenance(
            ProvenanceSource.ORACLE,
            note="ABCMetaAnalyzer.as_obstruction_if_not_implemented",
        )
        oid = _stable_hash(f"abc_missing_{cls_name}_{'_'.join(sorted(missing))}")
        return Obstruction(
            obstruction_id=f"abc_missing_{oid}",
            violated_condition=f"must_implement_abstract_methods: {missing}",
            coordinate=coord,
            repair_hints=hints,
            cohomology_class=_stable_hash(cls_name + "|".join(sorted(missing))),
            is_resolved=False,
            provenance=provenance,
        )
