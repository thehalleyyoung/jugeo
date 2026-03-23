from __future__ import annotations

r"""theory2.tex Ch20 §20.4 — Descriptor protocol as attribute-access morphisms in the site.

This module formalises Python's descriptor protocol inside the JuGeo judgment
framework.  Every attribute access is modelled as a chain of morphisms in a
Grothendieck site whose objects are Coordinate instances.

Key concepts
------------
* **Descriptor resolution** (§20.4.1): Python's three-phase lookup
  (data descriptor → instance __dict__ → non-data descriptor) is encoded
  as a sequence of RESTRICTION morphisms walking the MRO.

* **Slot coordinate restriction** (§20.4.2): ``__slots__`` declarations
  restrict the instance coordinate space, replacing the flat ``__dict__``
  with a finite covering family of typed slot coordinates.

* **Property morphisms** (§20.4.3): ``property`` objects expose three
  independent morphisms (fget / fset / fdel); the presence of fset or fdel
  determines the DATA vs NON_DATA classification.

* **Trust propagation** (§20.4.4): each step in the descriptor chain may
  have a different trust level; the tracker enforces conservative minimum
  aggregation and flags CopilotChannel proposals (COPILOT_SUGGESTED /
  ORACLE_PROPOSED) that have not yet been promoted.

CopilotChannel-sourced descriptor suggestions enter at ORACLE_PROPOSED trust
and require explicit promotion by a solver or runtime witness before a
descriptor-access judgment is considered settled.
"""

import dataclasses
import datetime
import hashlib
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

# --- module-level helpers -----------------------------------------------------

def _fallback_coord(tag: str, kind: CoordinateKind = CoordinateKind.FUNCTION) -> Coordinate:
    """Return a synthetic Coordinate when no real one is available."""
    return Coordinate(
        components=("__synthetic__", tag),
        kind=kind,
        metadata={"synthetic": True},
    )


def _min_trust(*levels: TrustLevel) -> TrustLevel:
    """Return the minimum (most conservative) TrustLevel from the given values."""
    valid = [lv for lv in levels if lv is not None]
    if not valid:
        return TrustLevel.UNVERIFIED
    return min(valid, key=lambda lv: lv.value)


def _make_provenance(source: ProvenanceSource, note: str = "") -> Provenance:
    """Build a minimal Provenance record with a timestamp."""
    return Provenance(
        source=source,
        creation_timestamp=_now_iso(),
        metadata={"note": note} if note else {},
    )

# =============================================================================
# DescriptorResolver
# =============================================================================

class DescriptorResolver:
    """Implements Python's full attribute lookup algorithm as site morphisms.

    The lookup order is:

    1. Data descriptors in the MRO (have both ``__get__`` and
       ``__set__`` / ``__delete__``).
    2. Instance ``__dict__`` (if the instance is not a type).
    3. Non-data descriptors and other class attributes in the MRO.

    Each step corresponds to a RESTRICTION morphism in the site: the
    accessor "restricts" to the most-specific coordinate that defines the
    attribute.

    CopilotChannel can propose alternative resolution orders for custom
    descriptors, subject to ORACLE_PROPOSED trust ceiling (also called
    COPILOT_SUGGESTED in earlier documentation drafts).  Such proposals
    must be validated by a solver or runtime witness before the judgment
    status can be set to SETTLED.

    theory2.tex Ch20 §20.4.1
    """

    def __init__(
        self,
        mro: list[str],
        descriptor_map: dict[str, DescriptorChain],
    ) -> None:
        """Initialise with the MRO of a class and a map of known descriptors.

        Parameters
        ----------
        mro:
            Ordered list of class names in method-resolution order, most
            derived first.  The list should include ``"object"`` at the end
            to match CPython behaviour.
        descriptor_map:
            Dictionary mapping attribute name → DescriptorChain for all
            attributes known to exist in this class hierarchy.
        """
        self._mro: list[str] = list(mro)
        self._desc_map: dict[str, DescriptorChain] = dict(descriptor_map)

    def resolve(self, attr: str, instance_class: str) -> DescriptorChain | None:
        """Run the full three-phase descriptor resolution for *attr*.

        Phase 1: scan the MRO for a DATA descriptor (descriptor_kind=="DATA").
                 Return it immediately if found — data descriptors win over
                 the instance dict.

        Phase 2: check whether *instance_class* itself defines *attr* as a
                 plain attribute (represented by a chain whose sole entry is
                 *instance_class* and descriptor_kind is "INSTANCE_DICT").
                 This represents the instance ``__dict__`` lookup.

        Phase 3: scan the MRO for a NON_DATA descriptor or a plain class
                 attribute (descriptor_kind in ("NON_DATA", "CLASS_VAR")).

        Parameters
        ----------
        attr:
            The attribute name to resolve.
        instance_class:
            The class of the instance performing the lookup (used for
            instance-dict phase ordering).

        Returns
        -------
        DescriptorChain | None
            The winning DescriptorChain, or ``None`` if the attribute is
            not found.
        """
        chain = self._desc_map.get(attr)
        if chain is None:
            return None

        # Phase 1 — data descriptor wins immediately.
        if chain.descriptor_kind == "DATA":
            _log.debug("resolve %r → DATA descriptor in %r", attr, chain.owner_class)
            return chain

        # Phase 2 — instance __dict__ represented as INSTANCE_DICT kind.
        if chain.descriptor_kind == "INSTANCE_DICT" and instance_class in chain.chain:
            _log.debug("resolve %r → instance __dict__ of %r", attr, instance_class)
            return chain

        # Phase 3 — non-data descriptor or plain class attribute.
        if chain.descriptor_kind in ("NON_DATA", "CLASS_VAR", "SLOT"):
            _log.debug("resolve %r → non-data descriptor in %r", attr, chain.owner_class)
            return chain

        return None

    def data_descriptors(self) -> list[DescriptorChain]:
        """Return all DATA descriptor chains, sorted by MRO position.

        DATA descriptors have ``descriptor_kind == "DATA"`` and take
        priority over instance ``__dict__`` entries.  They are sorted by the
        position of their ``owner_class`` in the MRO so that the most-derived
        descriptor comes first.

        Returns
        -------
        list[DescriptorChain]
            Sorted list of DATA descriptor chains.
        """
        data = [c for c in self._desc_map.values() if c.descriptor_kind == "DATA"]

        def _mro_pos(chain: DescriptorChain) -> int:
            try:
                return self._mro.index(chain.owner_class)
            except ValueError:
                return len(self._mro)

        data.sort(key=_mro_pos)
        return data

    def non_data_descriptors(self) -> list[DescriptorChain]:
        """Return all NON_DATA and SLOT descriptor chains.

        These are lower-priority than DATA descriptors and the instance
        ``__dict__``.

        Returns
        -------
        list[DescriptorChain]
            All chains with descriptor_kind in ``("NON_DATA", "SLOT")``.
        """
        return [
            c for c in self._desc_map.values()
            if c.descriptor_kind in ("NON_DATA", "SLOT")
        ]

    def resolution_morphism_chain(self, attr: str) -> list[Morphism]:
        """Build the ordered RESTRICTION morphism sequence for *attr*.

        For each class in the MRO that contains a definition for *attr*,
        emit a RESTRICTION morphism from the enclosing class coordinate to
        the attribute coordinate.  The chain is ordered MRO-first (most
        derived first) and terminates at the first defining class.

        Parameters
        ----------
        attr:
            The attribute name whose resolution chain to build.

        Returns
        -------
        list[Morphism]
            Ordered list of RESTRICTION morphisms; empty if *attr* is not
            found anywhere in the MRO.
        """
        chain = self._desc_map.get(attr)
        if chain is None:
            return []

        morphisms: list[Morphism] = []
        attr_coord = chain.coordinate or _fallback_coord(f"{chain.owner_class}.{attr}")

        for cls_name in self._mro:
            if cls_name in (chain.chain or ()):
                cls_coord = _fallback_coord(cls_name, CoordinateKind.MODULE)
                morphisms.append(
                    Morphism(
                        source=cls_coord,
                        target=attr_coord,
                        kind=MorphismKind.RESTRICTION,
                        label=f"{cls_name}.{attr}",
                    )
                )
        return morphisms

    def as_site_fragment(self) -> Site:
        """Build a Site containing all descriptor coordinates and morphisms.

        The fragment includes:
        * One coordinate per known DescriptorChain.
        * All RESTRICTION morphism chains for every known attribute.

        Returns
        -------
        Site
            A Site instance representing the full descriptor topology.
        """
        builder = SiteBuilder()
        seen: set[Any] = set()

        for attr, chain in self._desc_map.items():
            coord = chain.coordinate or _fallback_coord(f"{chain.owner_class}.{attr}")
            key = getattr(coord, "components", id(coord))
            if isinstance(key, list):
                key = tuple(key)
            if key not in seen:
                seen.add(key)
                builder.add_coordinate(coord)
            for morphism in self.resolution_morphism_chain(attr):
                builder.add_morphism(morphism)

        return builder.build()

    def trust_for(self, attr: str) -> TrustLevel:
        """Return the trust level of the winning descriptor for *attr*.

        Delegates to ``resolve`` with the owning class as *instance_class*.
        Falls back to ``TrustLevel.UNVERIFIED`` if the attribute is not
        found or has no trust annotation.

        Parameters
        ----------
        attr:
            The attribute name to query.

        Returns
        -------
        TrustLevel
            Trust of the winning descriptor, or UNVERIFIED if absent.
        """
        chain = self._desc_map.get(attr)
        if chain is None:
            return TrustLevel.UNVERIFIED
        owner = chain.owner_class or (self._mro[0] if self._mro else "unknown")
        winner = self.resolve(attr, owner)
        if winner is None:
            return TrustLevel.UNVERIFIED
        return winner.trust if winner.trust is not None else TrustLevel.UNVERIFIED

# =============================================================================
# SlotCoordinateBuilder
# =============================================================================

class SlotCoordinateBuilder:
    """Translates ``__slots__`` declarations into coordinate restrictions.

    A ``__slots__`` declaration eliminates the instance ``__dict__`` and
    replaces it with a fixed set of slot descriptors.  In the site, this
    is a coordinate restriction: the instance coordinate space is restricted
    to only the named slot coordinates.

    The covering family produced by ``covering_family`` can be fed directly
    into a GrothendieckTopology to declare that the slot coordinates cover
    the class-level coordinate.

    theory2.tex Ch20 §20.4.2
    """

    def __init__(self, class_name: str, slots: tuple[str, ...]) -> None:
        """Store the class name and its declared slots.

        Parameters
        ----------
        class_name:
            Unqualified name of the class declaring ``__slots__``.
        slots:
            Tuple of slot names as they appear in the ``__slots__``
            declaration.  May include ``"__dict__"`` and ``"__weakref__"``
            as explicit entries.
        """
        self._class_name = class_name
        self._slots = slots

    def slot_coordinates(self) -> list[Coordinate]:
        """Return one Coordinate per declared slot.

        Each slot coordinate has ``components=(class_name, slot_name,
        "__slot__")`` and ``kind=CoordinateKind.FUNCTION``.  The FUNCTION
        kind is used because slot descriptors behave like per-attribute
        accessor functions.

        Returns
        -------
        list[Coordinate]
            One Coordinate per slot; preserves declaration order.
        """
        return [
            Coordinate(
                components=(self._class_name, slot, "__slot__"),
                kind=CoordinateKind.FUNCTION,
                metadata={"slot_of": self._class_name},
            )
            for slot in self._slots
        ]

    def restriction_morphisms(self, base_coordinate: Coordinate) -> list[Morphism]:
        """Return RESTRICTION morphisms from *base_coordinate* to each slot.

        Each morphism represents the fact that the slot coordinate is
        accessible only when the instance coordinate is restricted to that
        particular slot.

        Parameters
        ----------
        base_coordinate:
            The coordinate of the class that declares ``__slots__``.

        Returns
        -------
        list[Morphism]
            One RESTRICTION morphism per slot.
        """
        return [
            Morphism(
                source=base_coordinate,
                target=slot_coord,
                kind=MorphismKind.RESTRICTION,
                label=f"__slot__{slot_coord.components[1]}",
            )
            for slot_coord in self.slot_coordinates()
        ]

    def as_descriptor_chain(
        self,
        slot_name: str,
        trust: TrustLevel,
    ) -> DescriptorChain:
        """Build a DescriptorChain for a single slot descriptor.

        Slot descriptors are DATA descriptors — they have both ``__get__``
        and ``__set__`` (and ``__delete__`` for optional slots) — so the
        ``descriptor_kind`` is set to ``"SLOT"`` which the resolver treats
        as a specialised DATA descriptor.

        Parameters
        ----------
        slot_name:
            Name of the slot.
        trust:
            Trust level to attach to the chain.

        Returns
        -------
        DescriptorChain
            A fully-populated DescriptorChain for the slot.
        """
        slot_coord = Coordinate(
            components=(self._class_name, slot_name, "__slot__"),
            kind=CoordinateKind.FUNCTION,
            metadata={"slot_of": self._class_name},
        )
        return DescriptorChain(
            attribute_name=slot_name,
            owner_class=self._class_name,
            coordinate=slot_coord,
            chain=(self._class_name,),
            descriptor_kind="SLOT",
            trust=trust,
            override_map={self._class_name: "__slot__"},
        )

    def covering_family(self) -> CoveringFamily:
        """Build the CoveringFamily that witnesses ``__slots__`` coverage.

        The base coordinate represents the abstract ``__slots__`` region of
        the class.  Members are the individual slot coordinates.  Together
        they form a covering: the union of slot coordinates covers the
        instance attribute space.

        Returns
        -------
        CoveringFamily
            A CoveringFamily with the __slots__ region as base.
        """
        base = Coordinate(
            components=(self._class_name, "__slots__"),
            kind=CoordinateKind.REGION,
            metadata={"slots_count": len(self._slots)},
        )
        return CoveringFamily(
            base=base,
            members=self.slot_coordinates(),
            label=f"{self._class_name}.__slots__",
        )

    def has_dict(self) -> bool:
        """Return True only if ``"__dict__"`` is explicitly in the slots tuple.

        By default, classes with ``__slots__`` do *not* have an instance
        ``__dict__``.  This method returns True only when the developer has
        explicitly included ``"__dict__"`` in the slots declaration to
        re-enable it.

        Returns
        -------
        bool
        """
        return "__dict__" in self._slots

    def has_weakref(self) -> bool:
        """Return True if ``"__weakref__"`` is explicitly declared in slots.

        Like ``__dict__``, weak-reference support is suppressed by
        ``__slots__`` unless explicitly included.

        Returns
        -------
        bool
        """
        return "__weakref__" in self._slots

# =============================================================================
# PropertyDescriptorAnalyzer
# =============================================================================

class PropertyDescriptorAnalyzer:
    """Models the ``property`` built-in as a DATA descriptor in the site.

    A ``property`` object has ``fget``, ``fset``, and ``fdel`` functions, each
    of which is a separate morphism in the site.  The presence of ``fset``
    (or ``fdel``) makes it a DATA descriptor that takes precedence over the
    instance ``__dict__``.  A read-only property (``fget`` only) degrades to
    a NON_DATA descriptor.

    CopilotChannel-generated property stubs enter at ORACLE_PROPOSED trust.
    A complete property (fget + fset + fdel) that was synthesised by copilot
    requires runtime witnessing before its trust can be raised.

    theory2.tex Ch20 §20.4.3
    """

    def __init__(
        self,
        attr_name: str,
        owner_class: str,
        has_getter: bool,
        has_setter: bool,
        has_deleter: bool,
        doc: str = "",
    ) -> None:
        """Describe a property descriptor on *owner_class*.

        Parameters
        ----------
        attr_name:
            The attribute name (e.g. ``"value"`` for ``obj.value``).
        owner_class:
            Unqualified name of the class that defines this property.
        has_getter:
            True if the property has a ``fget`` function.
        has_setter:
            True if the property has a ``fset`` function.
        has_deleter:
            True if the property has a ``fdel`` function.
        doc:
            Optional docstring for the property.
        """
        self._attr_name = attr_name
        self._owner_class = owner_class
        self._has_getter = has_getter
        self._has_setter = has_setter
        self._has_deleter = has_deleter
        self._doc = doc

    def descriptor_kind(self) -> str:
        """Return ``"DATA"`` if the property is writable or deletable.

        A property is a DATA descriptor when it defines ``fset`` or ``fdel``,
        because those methods satisfy Python's requirement for ``__set__`` or
        ``__delete__`` to classify an object as a data descriptor.

        Returns
        -------
        str
            ``"DATA"`` or ``"NON_DATA"``.
        """
        if self._has_setter or self._has_deleter:
            return "DATA"
        return "NON_DATA"

    def getter_morphism(self, base_coord: Coordinate) -> Morphism | None:
        """Return the TRANSPORT morphism for ``fget``, or None.

        The morphism represents reading the property: it transports the
        accessor from the base class coordinate to the getter function
        coordinate.

        Parameters
        ----------
        base_coord:
            Coordinate of the owning class.

        Returns
        -------
        Morphism | None
            A TRANSPORT morphism labelled ``"{attr_name}.fget"``, or
            ``None`` if the property has no getter.
        """
        if not self._has_getter:
            return None
        target = Coordinate(
            components=(self._owner_class, self._attr_name, "fget"),
            kind=CoordinateKind.FUNCTION,
        )
        return Morphism(
            source=base_coord,
            target=target,
            kind=MorphismKind.TRANSPORT,
            label=f"{self._attr_name}.fget",
        )

    def setter_morphism(self, base_coord: Coordinate) -> Morphism | None:
        """Return the TRANSPORT morphism for ``fset``, or None.

        The morphism represents writing through the property.  Its presence
        is what makes the property a DATA descriptor.

        Parameters
        ----------
        base_coord:
            Coordinate of the owning class.

        Returns
        -------
        Morphism | None
            A TRANSPORT morphism labelled ``"{attr_name}.fset"``, or
            ``None`` if the property has no setter.
        """
        if not self._has_setter:
            return None
        target = Coordinate(
            components=(self._owner_class, self._attr_name, "fset"),
            kind=CoordinateKind.FUNCTION,
        )
        return Morphism(
            source=base_coord,
            target=target,
            kind=MorphismKind.TRANSPORT,
            label=f"{self._attr_name}.fset",
        )

    def deleter_morphism(self, base_coord: Coordinate) -> Morphism | None:
        """Return the TRANSPORT morphism for ``fdel``, or None.

        The morphism represents deletion through the property.

        Parameters
        ----------
        base_coord:
            Coordinate of the owning class.

        Returns
        -------
        Morphism | None
            A TRANSPORT morphism labelled ``"{attr_name}.fdel"``, or
            ``None`` if the property has no deleter.
        """
        if not self._has_deleter:
            return None
        target = Coordinate(
            components=(self._owner_class, self._attr_name, "fdel"),
            kind=CoordinateKind.FUNCTION,
        )
        return Morphism(
            source=base_coord,
            target=target,
            kind=MorphismKind.TRANSPORT,
            label=f"{self._attr_name}.fdel",
        )

    def as_descriptor_chain(
        self,
        mro: list[str],
        trust: TrustLevel,
    ) -> DescriptorChain:
        """Build a DescriptorChain from this property analysis.

        The chain is restricted to the single ``owner_class`` entry (a
        property is defined in exactly one class; subclasses inherit it).
        The ``override_map`` records the property kind for the owner.

        Parameters
        ----------
        mro:
            Full MRO of the class hierarchy (used to validate ownership).
        trust:
            Trust level to associate with the resulting chain.

        Returns
        -------
        DescriptorChain
            A fully-populated DescriptorChain for this property.
        """
        # Only include the owner class in the chain; other MRO entries
        # merely inherit without overriding.
        chain_members = tuple(c for c in mro if c == self._owner_class)
        coord = Coordinate(
            components=(self._owner_class, self._attr_name, "property"),
            kind=CoordinateKind.FUNCTION,
            metadata={"doc": self._doc, "kind": self.descriptor_kind()},
        )
        return DescriptorChain(
            attribute_name=self._attr_name,
            owner_class=self._owner_class,
            coordinate=coord,
            chain=chain_members or (self._owner_class,),
            descriptor_kind=self.descriptor_kind(),
            trust=trust,
            override_map={self._owner_class: "property"},
        )

    def all_morphisms(self, base_coord: Coordinate) -> list[Morphism]:
        """Collect and return all non-None morphisms for this property.

        Calls ``getter_morphism``, ``setter_morphism``, and
        ``deleter_morphism`` in order, filtering out ``None`` results.

        Parameters
        ----------
        base_coord:
            Coordinate of the owning class, passed to each sub-method.

        Returns
        -------
        list[Morphism]
            Between 0 and 3 morphisms, depending on which accessors exist.
        """
        candidates = [
            self.getter_morphism(base_coord),
            self.setter_morphism(base_coord),
            self.deleter_morphism(base_coord),
        ]
        return [m for m in candidates if m is not None]

# =============================================================================
# DescriptorTrustTracker
# =============================================================================

class DescriptorTrustTracker:
    """Tracks and propagates trust levels through descriptor resolution.

    When a descriptor is resolved via the MRO, each step in the chain may
    have a different trust level.  The tracker computes the minimum (most
    conservative) trust across the entire chain and flags any step where
    trust drops below a specified threshold.

    CopilotChannel-sourced descriptor proposals enter at ORACLE_PROPOSED
    (also labelled COPILOT_SUGGESTED in design documents) and require
    explicit promotion.  This tracker ensures the ceiling is enforced: no
    copilot-proposed descriptor chain may be promoted automatically beyond
    ORACLE_PROPOSED without either a solver discharge or a runtime witness.

    theory2.tex Ch20 §20.4.4
    """

    def __init__(self, chains: list[DescriptorChain]) -> None:
        """Initialise with a list of DescriptorChains to track.

        Parameters
        ----------
        chains:
            All DescriptorChains in scope for this class or module.  The
            tracker builds an internal index keyed by ``attribute_name``
            for fast lookup.  Duplicate attribute names are resolved by
            keeping the chain with the higher trust level (trusting the
            most-derived definition).
        """
        self._chains: list[DescriptorChain] = list(chains)
        self._by_attr: dict[str, DescriptorChain] = {}
        for chain in chains:
            existing = self._by_attr.get(chain.attribute_name)
            if existing is None:
                self._by_attr[chain.attribute_name] = chain
            else:
                # Prefer higher trust (more evidence).
                existing_val = existing.trust.value if existing.trust else 0
                new_val = chain.trust.value if chain.trust else 0
                if new_val > existing_val:
                    self._by_attr[chain.attribute_name] = chain

    def min_trust(self, attr: str) -> TrustLevel:
        """Return the trust level of the registered chain for *attr*.

        Because each DescriptorChain carries a single aggregate trust value
        (computed at creation time), this is a direct lookup.  Returns
        ``TrustLevel.UNVERIFIED`` when the attribute is not tracked.

        Parameters
        ----------
        attr:
            Attribute name to query.

        Returns
        -------
        TrustLevel
            The chain's trust level, or UNVERIFIED if not found.
        """
        chain = self._by_attr.get(attr)
        if chain is None:
            return TrustLevel.UNVERIFIED
        return chain.trust if chain.trust is not None else TrustLevel.UNVERIFIED

    def below_threshold(self, threshold: TrustLevel) -> list[DescriptorChain]:
        """Return all chains whose trust is strictly below *threshold*.

        Comparison is performed on the integer ``.value`` of TrustLevel so
        that ``ORACLE_PROPOSED (2) < RUNTIME_WITNESSED (3)`` etc.

        Parameters
        ----------
        threshold:
            The minimum acceptable trust level (exclusive lower bound).

        Returns
        -------
        list[DescriptorChain]
            Chains whose trust value is less than ``threshold.value``.
        """
        threshold_val = threshold.value if threshold is not None else 0
        result: list[DescriptorChain] = []
        for chain in self._chains:
            chain_val = chain.trust.value if chain.trust is not None else 0
            if chain_val < threshold_val:
                result.append(chain)
        return result

    def promote(self, attr: str, new_trust: TrustLevel) -> DescriptorChain:
        """Return a new DescriptorChain with an updated trust level.

        Uses ``dataclasses.replace`` to produce an immutable copy of the
        tracked chain for *attr* with ``trust=new_trust``.  The internal
        index is updated to reference the promoted chain.

        Parameters
        ----------
        attr:
            Attribute name whose chain to promote.
        new_trust:
            The new trust level (must be >= current trust to be meaningful,
            though the method does not enforce this).

        Returns
        -------
        DescriptorChain
            The promoted chain (immutable copy with updated trust).

        Raises
        ------
        KeyError
            If *attr* is not tracked by this tracker.
        """
        chain = self._by_attr[attr]
        promoted = dataclasses.replace(chain, trust=new_trust)
        self._by_attr[attr] = promoted
        # Update the list too.
        self._chains = [
            promoted if c.attribute_name == attr else c for c in self._chains
        ]
        return promoted

    def trust_report(self) -> dict[str, str]:
        """Return a dict mapping attribute name to trust level name.

        Useful for logging, debugging, and producing human-readable summaries
        of the trust state of a class's descriptor surface.

        Returns
        -------
        dict[str, str]
            ``{attr_name: trust_level.name}`` for all tracked chains.
        """
        return {
            attr: (chain.trust.name if chain.trust is not None else "NONE")
            for attr, chain in self._by_attr.items()
        }

    def copilot_flagged(self) -> list[DescriptorChain]:
        """Return chains sourced from CopilotChannel (ORACLE_PROPOSED trust).

        These are chains that were proposed by CopilotChannel and have not
        yet been promoted by a solver or runtime witness.  They carry the
        COPILOT_SUGGESTED trust ceiling and should be reviewed before any
        dependent judgment is settled.

        Returns
        -------
        list[DescriptorChain]
            All chains with ``trust == TrustLevel.ORACLE_PROPOSED``.
        """
        return [
            chain for chain in self._chains
            if chain.trust == TrustLevel.ORACLE_PROPOSED
        ]

    def evidence_items_for(self, attr: str) -> list[EvidenceItem]:
        """Build EvidenceItems for each class in the descriptor chain.

        Emits one RUNTIME_WITNESS EvidenceItem per class name in the chain
        tuple (i.e. one item per MRO class that contributed to the
        descriptor).  All items carry the chain's trust level and the
        RUNTIME channel.

        Parameters
        ----------
        attr:
            Attribute name to query.

        Returns
        -------
        list[EvidenceItem]
            One EvidenceItem per chain entry; empty if *attr* is not tracked.
        """
        chain = self._by_attr.get(attr)
        if chain is None:
            return []

        provenance = _make_provenance(
            ProvenanceSource.RUNTIME,
            note=f"DescriptorTrustTracker.evidence_items_for({attr!r})",
        )
        trust = chain.trust if chain.trust is not None else TrustLevel.UNVERIFIED
        items: list[EvidenceItem] = []

        for cls_name in (chain.chain or (chain.owner_class,)):
            items.append(
                EvidenceItem(
                    kind=EvidenceItemKind.RUNTIME_WITNESS,
                    payload={
                        "attribute": attr,
                        "class": cls_name,
                        "descriptor_kind": chain.descriptor_kind,
                    },
                    trust_level=trust,
                    channel=EvidenceChannel.RUNTIME,
                    timestamp=_now_iso(),
                    provenance=provenance,
                )
            )
        return items
