from __future__ import annotations

r"""
Metaobject surfaces for the jugeo Python runtime model.

Implements the data structures described in theory2.tex Ch20 §20.1–§20.4:

  §20.1  Background: sites, sheaves, and the Python object model
  §20.2  Metaclass records as type-constructor morphisms in the site
  §20.3  Behavioral surfaces as judgment-indexed observable interfaces
  §20.4  Descriptor chains as MRO-ordered morphism sequences
  §20.5  Class-creation traces as full three-phase protocol records

All records are immutable frozen dataclasses.  Mutable compound fields use
``field(default_factory=...)``.  Immutable updates are performed with the
standard-library ``dataclasses.replace`` helper (re-exported here as
``replace`` for convenience).

CopilotChannel evidence carries the COPILOT_SUGGESTED trust annotation; see
``BehavioralSurface.as_judgment`` and ``ClassCreationTrace.as_judgment`` for
the canonical way to attach that annotation.
"""

import datetime
from dataclasses import dataclass, field, replace  # noqa: F401  (re-export replace)
from typing import Any, Mapping

# ---
# Jugeo geometry imports (with stubs for standalone use)
# ---

try:
    from jugeo.geometry.site import (
        Coordinate,
        CoordinateKind,
        Morphism,
        MorphismKind,
        Site,
        SiteBuilder,
        CoveringFamily,
        GrothendieckTopology,
        CoordinateObject,
    )
except ImportError:
    import enum

    class CoordinateKind(enum.Enum):  # type: ignore[no-redef]
        MODULE = "MODULE"
        FUNCTION = "FUNCTION"
        INTERFACE = "INTERFACE"
        TEST = "TEST"
        THEOREM = "THEOREM"
        REGION = "REGION"

    class MorphismKind(enum.Enum):  # type: ignore[no-redef]
        RESTRICTION = "RESTRICTION"
        INCLUSION = "INCLUSION"
        TRANSPORT = "TRANSPORT"
        REFINEMENT = "REFINEMENT"

    @dataclass(frozen=True, slots=True)
    class Coordinate:  # type: ignore[no-redef]
        components: tuple[str, ...]
        kind: CoordinateKind
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Morphism:  # type: ignore[no-redef]
        source: Coordinate
        target: Coordinate
        kind: MorphismKind
        label: str = ""

    @dataclass(frozen=True, slots=True)
    class CoveringFamily:  # type: ignore[no-redef]
        base: Coordinate
        members: list[Coordinate] = field(default_factory=list)
        label: str = ""

    class Site:  # type: ignore[no-redef]
        pass

    class SiteBuilder:  # type: ignore[no-redef]
        pass

    class GrothendieckTopology:  # type: ignore[no-redef]
        pass

    class CoordinateObject:  # type: ignore[no-redef]
        pass

# ---
# Jugeo judgment imports (with stubs for standalone use)
# ---

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        LocalJudgment,
        JudgmentBuilder,
        JudgmentAlgebra,
        JudgmentStatus,
        TrustLevel,
        PropositionKind,
        Proposition,
        Carrier,
        EvidenceItem,
        EvidenceBundle,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
        EvidenceItemKind,
        _stable_hash,
        _now_iso,
    )
except ImportError:
    import enum
    import hashlib

    class TrustLevel(enum.IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind(enum.Enum):  # type: ignore[no-redef]
        STRUCTURAL = "STRUCTURAL"
        BEHAVIORAL = "BEHAVIORAL"
        RELATIONAL = "RELATIONAL"
        RESOURCE = "RESOURCE"
        SEMANTIC = "SEMANTIC"

    class ProvenanceSource(enum.Enum):  # type: ignore[no-redef]
        SOLVER = "SOLVER"
        RUNTIME = "RUNTIME"
        ORACLE = "ORACLE"
        HUMAN = "HUMAN"
        COMPOSED = "COMPOSED"

    class EvidenceItemKind(enum.Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "SOLVER_PROOF"
        RUNTIME_WITNESS = "RUNTIME_WITNESS"
        ORACLE_PROPOSAL = "ORACLE_PROPOSAL"
        FORMAL_PROOF = "FORMAL_PROOF"

    class JudgmentStatus(enum.Enum):  # type: ignore[no-redef]
        OPEN = "OPEN"
        CLOSED = "CLOSED"
        PARTIAL = "PARTIAL"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        kind: PropositionKind
        formula: str
        free_variables: tuple[str, ...] = field(default_factory=tuple)
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        name: str
        parameters: Mapping[str, Any] = field(default_factory=dict)
        is_dependent: bool = False
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        source: ProvenanceSource
        parent_judgments: tuple[str, ...] = field(default_factory=tuple)
        creation_timestamp: str = ""
        transformation_history: tuple[str, ...] = field(default_factory=tuple)
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: EvidenceItemKind
        payload: Any
        trust_level: TrustLevel
        channel: Any
        timestamp: str
        expiry: str | None
        provenance: Provenance

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple[EvidenceItem, ...] = field(default_factory=tuple)

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        description: str
        coordinate: Any = None

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        description: str
        coordinate: Any = None

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        label: str
        trust: TrustLevel

    @dataclass
    class Judgment:  # type: ignore[no-redef]
        coordinate: Any
        proposition: Proposition
        carrier: Carrier
        evidence: EvidenceBundle
        obligations: tuple[ResidualObligation, ...]
        obstructions: tuple[Obstruction, ...]
        trust: TrustLevel
        provenance: Provenance
        clauses: tuple[str, ...]
        status: JudgmentStatus

    class LocalJudgment:  # type: ignore[no-redef]
        pass

    class JudgmentAlgebra:  # type: ignore[no-redef]
        pass

    class JudgmentBuilder:  # type: ignore[no-redef]
        """Minimal stub for JudgmentBuilder used in as_judgment methods."""

        def __init__(self) -> None:
            self._coordinate: Any = None
            self._proposition: Proposition | None = None
            self._carrier: Carrier | None = None
            self._trust: TrustLevel = TrustLevel.UNVERIFIED
            self._provenance: Provenance | None = None

        def at(self, coordinate: Any) -> JudgmentBuilder:
            """Set the coordinate for the judgment being constructed."""
            self._coordinate = coordinate
            return self

        def about(self, proposition: Proposition) -> JudgmentBuilder:
            """Set the proposition for the judgment being constructed."""
            self._proposition = proposition
            return self

        def claiming(self, proposition: Proposition) -> JudgmentBuilder:
            """Set the proposition for the judgment being constructed (real API)."""
            self._proposition = proposition
            return self

        def carrying(self, carrier: Carrier) -> JudgmentBuilder:
            """Set the carrier for the judgment being constructed."""
            self._carrier = carrier
            return self

        def of_type(self, carrier: Carrier) -> JudgmentBuilder:
            """Set the carrier for the judgment being constructed (real API)."""
            self._carrier = carrier
            return self

        def trusting(self, trust: TrustLevel) -> JudgmentBuilder:
            """Set the trust level for the judgment being constructed."""
            self._trust = trust
            return self

        def with_trust_level(self, level: TrustLevel) -> JudgmentBuilder:
            """Set the trust level for the judgment being constructed (real API)."""
            self._trust = level
            return self

        def from_provenance(self, provenance: Provenance) -> JudgmentBuilder:
            """Set the provenance for the judgment being constructed."""
            self._provenance = provenance
            return self

        def from_source(self, source: ProvenanceSource) -> JudgmentBuilder:  # type: ignore[override]
            """Set the provenance source for the judgment being constructed (real API)."""
            self._provenance = Provenance(
                source=source,
                parent_judgments=(),
                creation_timestamp=_now_iso(),
                transformation_history=(),
                metadata={},
            )
            return self

        def build(self) -> Judgment:
            """Construct and return the Judgment instance."""
            now = datetime.datetime.utcnow().isoformat() + "Z"
            prop = self._proposition or Proposition(
                kind=PropositionKind.STRUCTURAL, formula="unknown"
            )
            carrier = self._carrier or Carrier(name="unknown")
            prov = self._provenance or Provenance(
                source=ProvenanceSource.HUMAN, creation_timestamp=now
            )
            return Judgment(
                coordinate=self._coordinate,
                proposition=prop,
                carrier=carrier,
                evidence=EvidenceBundle(),
                obligations=(),
                obstructions=(),
                trust=self._trust,
                provenance=prov,
                clauses=(),
                status=JudgmentStatus.OPEN,
            )

    def _stable_hash(obj: Any) -> str:  # type: ignore[no-redef]
        """Return a stable hex digest for the string representation of obj."""
        return hashlib.sha256(str(obj).encode()).hexdigest()[:16]

    def _now_iso() -> str:  # type: ignore[no-redef]
        """Return the current UTC time as an ISO-8601 string."""
        return datetime.datetime.utcnow().isoformat() + "Z"

# ---
# Jugeo Z3 solver imports (with stubs for standalone use)
# ---

try:
    from jugeo.solver.z3_session import (
        Z3Session,
        Z3QueryBuilder,
        Z3Result,
        SolveOutcome,
        Z3Encoder,
    )
except ImportError:

    class SolveOutcome:  # type: ignore[no-redef]
        pass

    class Z3Result:  # type: ignore[no-redef]
        pass

    class Z3Session:  # type: ignore[no-redef]
        pass

    class Z3QueryBuilder:  # type: ignore[no-redef]
        pass

    class Z3Encoder:  # type: ignore[no-redef]
        pass

# ---
# Jugeo evidence channel imports (with stubs for standalone use)
# ---

try:
    from jugeo.evidence.channels import (
        EvidenceChannel,
        EvidenceRecord,
        EvidenceRequest,
        EvidenceResponse,
        ChannelRouter,
        CopilotChannel,
        SolverChannel,
        RuntimeChannel,
    )
except ImportError:
    import enum

    class EvidenceChannel(enum.Enum):  # type: ignore[no-redef]
        COPILOT = "COPILOT"
        SOLVER = "SOLVER"
        RUNTIME = "RUNTIME"
        HUMAN = "HUMAN"

    class EvidenceRecord:  # type: ignore[no-redef]
        pass

    class EvidenceRequest:  # type: ignore[no-redef]
        pass

    class EvidenceResponse:  # type: ignore[no-redef]
        pass

    class ChannelRouter:  # type: ignore[no-redef]
        pass

    class CopilotChannel:  # type: ignore[no-redef]
        """Stub for the real CopilotChannel evidence provider.

        In production the CopilotChannel routes COPILOT_SUGGESTED trust
        annotations to the Copilot inference backend and returns
        EvidenceItem instances stamped with ORACLE_PROPOSAL kind and
        ORACLE_PROPOSED trust.
        """

        TRUST = TrustLevel.ORACLE_PROPOSED

    class SolverChannel:  # type: ignore[no-redef]
        pass

    class RuntimeChannel:  # type: ignore[no-redef]
        pass

# ---
# Internal helpers
# ---


def _coerce_trust_min(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """Return the lower of two TrustLevel values.

    Used throughout this module when merging records; the merged record
    inherits the weaker trust guarantee of its constituents.

    Args:
        a: First trust level.
        b: Second trust level.

    Returns:
        The TrustLevel with the lower integer value.
    """
    return a if a.value <= b.value else b


def _validate_descriptor_kind(kind: str) -> str:
    """Ensure a descriptor kind string is one of the three recognised values.

    Args:
        kind: One of ``"DATA"``, ``"NON_DATA"``, or ``"SLOT"``.

    Returns:
        The validated kind string, unchanged.

    Raises:
        ValueError: If *kind* is not recognised.
    """
    valid = {"DATA", "NON_DATA", "SLOT"}
    if kind not in valid:
        raise ValueError(f"descriptor_kind must be one of {valid}, got {kind!r}")
    return kind


def _judgment_provenance(source: ProvenanceSource) -> Provenance:
    """Build a minimal Provenance record for use inside *as_judgment* methods.

    Args:
        source: The ProvenanceSource to stamp on the new record.

    Returns:
        A Provenance with an empty parent list and the current timestamp.
    """
    return Provenance(
        source=source,
        parent_judgments=(),
        creation_timestamp=_now_iso(),
        transformation_history=(),
        metadata={},
    )


# ---
# §20.2  MetaclassRecord
# ---


@dataclass(frozen=True, slots=True)
class MetaclassRecord:
    r"""Immutable record describing the metaclass relationship for one class.

    Corresponds to theory2.tex Ch20 §20.2: "The metaclass as type-constructor
    morphism in the site."  A metaclass record encodes enough geometric
    information to reconstruct the TRANSPORT morphism that the Python runtime
    implicitly creates when it evaluates a ``class`` statement.

    CopilotChannel introspection can supply a metaclass record with
    COPILOT_SUGGESTED (i.e. ORACLE_PROPOSED) trust when the metaclass cannot
    be determined statically; such records must be re-verified at runtime.

    Attributes:
        class_name:          Fully-qualified name of the class being defined.
        metaclass_name:      Fully-qualified name of the metaclass.
        coordinate:          Geometric coordinate of the class object.
        bases:               Tuple of base-class names in declaration order.
        metaclass_coordinate: Geometric coordinate of the metaclass object.
        trust:               Trust level attached to this record.
        class_mro:           Full MRO as a tuple of class names (C3 order).
        created_at:          ISO-8601 timestamp of record creation.
    """

    class_name: str
    metaclass_name: str
    coordinate: Coordinate
    bases: tuple[str, ...]
    metaclass_coordinate: Coordinate
    trust: TrustLevel
    class_mro: tuple[str, ...]
    created_at: str

    # ------------------------------------------------------------------
    def metaclass_morphism(self) -> Morphism:
        """Return the TRANSPORT morphism realising the metaclass constructor.

        In theory2.tex §20.2 the metaclass constructor is modelled as a
        TRANSPORT morphism from the metaclass coordinate to the class
        coordinate, labelled by the ``__new__`` call that Python uses
        internally to allocate the new type object.

        Returns:
            A ``Morphism`` with ``kind=MorphismKind.TRANSPORT`` running from
            ``self.metaclass_coordinate`` to ``self.coordinate``.
        """
        label = f"{self.metaclass_name}.__new__({self.class_name})"
        return Morphism(
            source=self.metaclass_coordinate,
            target=self.coordinate,
            kind=MorphismKind.TRANSPORT,
            label=label,
        )

    # ------------------------------------------------------------------
    def is_type(self) -> bool:
        """Return True when the metaclass is the built-in ``type``.

        A class whose metaclass is exactly ``type`` (or a dotted-path alias
        ending in ``.type``) has the minimal metaclass — it participates in
        no metaclass protocol beyond the default C-level machinery.

        Returns:
            ``True`` iff ``metaclass_name`` is ``"type"`` or ends with
            ``".type"``.
        """
        return self.metaclass_name == "type" or self.metaclass_name.endswith(".type")

    # ------------------------------------------------------------------
    def is_abc(self) -> bool:
        """Return True when the metaclass is ``ABCMeta`` or a subclass thereof.

        Classes with ``ABCMeta`` as their metaclass may have abstract methods
        and participate in the ``isinstance`` / ``issubclass`` protocol hooks
        defined by the ABC machinery.

        Returns:
            ``True`` iff ``"ABCMeta"`` appears anywhere in ``metaclass_name``.
        """
        return "ABCMeta" in self.metaclass_name

    # ------------------------------------------------------------------
    def mro_distance(self, cls_name: str) -> int:
        """Return the MRO index of *cls_name*, or -1 if absent.

        The MRO index is the zero-based position of *cls_name* inside
        ``self.mro``.  Lower values indicate more-derived classes (closer to
        the concrete class); higher values indicate more-base classes.

        Args:
            cls_name: The class name to look up in the MRO.

        Returns:
            Non-negative integer index, or ``-1`` if *cls_name* is not in the
            MRO.
        """
        try:
            return self.class_mro.index(cls_name)
        except ValueError:
            return -1

    # ------------------------------------------------------------------
    def conflicts_with(self, other: MetaclassRecord) -> bool:
        """Return True when the two metaclasses are incompatible.

        Two metaclasses conflict when neither is a subtype of the other.  The
        heuristic used here is name-based: if ``self.metaclass_name`` does not
        appear in ``other.mro`` *and* ``other.metaclass_name`` does not appear
        in ``self.mro``, the metaclasses are considered siblings and therefore
        incompatible.  This is the condition that would cause Python's
        ``type.__new__`` to raise ``TypeError: metaclass conflict``.

        Args:
            other: Another ``MetaclassRecord`` to compare against.

        Returns:
            ``True`` iff the metaclass relationship is potentially conflicting.
        """
        self_in_other = self.metaclass_name in other.class_mro
        other_in_self = other.metaclass_name in self.class_mro
        return not self_in_other and not other_in_self

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise the record to a plain dictionary.

        All coordinate information is reduced to its ``components`` tuple
        (as a list for JSON friendliness) and the trust level to its enum
        name string.

        Returns:
            A ``dict`` suitable for JSON serialisation.
        """
        return {
            "class_name": self.class_name,
            "metaclass_name": self.metaclass_name,
            "coordinate": list(self.coordinate.components),
            "coordinate_kind": self.coordinate.kind.name,
            "bases": list(self.bases),
            "metaclass_coordinate": list(self.metaclass_coordinate.components),
            "metaclass_coordinate_kind": self.metaclass_coordinate.kind.name,
            "trust": self.trust.name,
            "class_mro": list(self.class_mro),
            "created_at": self.created_at,
        }

    # ------------------------------------------------------------------
    def as_carrier(self) -> Carrier:
        """Return this metaclass record as a jugeo ``Carrier``.

        A ``Carrier`` wraps the type-level information needed to evaluate a
        proposition at a particular coordinate.  For a metaclass record the
        carrier is dependent (``is_dependent=True``) whenever the class has
        explicit base classes, because the metaclass may be inherited or
        composed from those bases.

        Returns:
            A ``Carrier`` whose ``name`` is the class name and whose
            ``parameters`` encode the metaclass name and full MRO.
        """
        return Carrier(
            name=self.class_name,
            parameters={
                "metaclass": self.metaclass_name,
                "mro": list(self.class_mro),
            },
            is_dependent=len(self.bases) > 0,
            metadata={
                "coordinate": str(self.coordinate.components),
                "trust": self.trust.name,
            },
        )

    # ------------------------------------------------------------------
    def dominant_metaclass(self, other: MetaclassRecord) -> MetaclassRecord:
        """Return whichever of the two records has the more-derived metaclass.

        Python's class creation machinery requires that the most-derived
        metaclass be used when combining multiple base classes.  This method
        implements that selection: if ``other.metaclass_name`` appears in
        ``self.mro`` then ``self``'s metaclass is more derived; if
        ``self.metaclass_name`` appears in ``other.mro`` then ``other``'s is
        more derived; otherwise the metaclasses conflict and a ``TypeError``
        is raised.

        Args:
            other: The ``MetaclassRecord`` to compare against.

        Returns:
            The more-derived of ``self`` or ``other``.

        Raises:
            TypeError: If neither metaclass is a subtype of the other (i.e.
                the metaclasses are incompatible).
        """
        if other.metaclass_name in self.class_mro:
            return self
        if self.metaclass_name in other.class_mro:
            return other
        raise TypeError(
            f"Metaclass conflict: {self.metaclass_name!r} and "
            f"{other.metaclass_name!r} are not compatible — "
            "neither appears in the other's MRO."
        )


# ---
# §20.3  BehavioralSurface
# ---


@dataclass(frozen=True, slots=True)
class BehavioralSurface:
    r"""Judgment-indexed observable interface surface for a class.

    Corresponds to theory2.tex Ch20 §20.3.  A behavioral surface records
    which protocols, dunder methods, and abstract methods a class exposes at
    the Python level, together with a trust level that can be updated via
    evidence from CopilotChannel or the runtime witness infrastructure.

    The ``judgment_index`` maps each method name to the ID of the Judgment
    that certified its presence.  When a method has not yet been judged, it is
    absent from the index.  CopilotChannel may pre-populate the index with
    COPILOT_SUGGESTED (ORACLE_PROPOSED) judgments that are later confirmed or
    refuted at runtime.

    Attributes:
        class_name:      Name of the class whose surface this describes.
        coordinate:      Geometric coordinate of the class.
        protocols:       Tuple of protocol names the class implements.
        dunder_methods:  Tuple of dunder method names present on the class.
        abstract_methods: Tuple of abstract method names (not yet overridden).
        trust:           Trust level of the surface record as a whole.
        judgment_index:  Mapping from method name to judgment ID string.
    """

    class_name: str
    coordinate: Coordinate
    protocols: tuple[str, ...]
    dunder_methods: tuple[str, ...]
    abstract_methods: tuple[str, ...]
    trust: TrustLevel
    judgment_index: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def implements_protocol(self, p: str) -> bool:
        """Return True when this surface implements the named protocol.

        Args:
            p: The protocol name to check (e.g. ``"Iterable"``,
               ``"ContextManager"``).

        Returns:
            ``True`` iff *p* is in ``self.protocols``.
        """
        return p in self.protocols

    # ------------------------------------------------------------------
    def missing_dunders(self, required: tuple[str, ...]) -> tuple[str, ...]:
        """Return the subset of *required* dunder names absent from this surface.

        This is used by the protocol-conformance checker to identify which
        abstract dunder slots must still be filled before the class is
        considered fully concrete.

        Args:
            required: Tuple of dunder names that must be present.

        Returns:
            A tuple containing only those names from *required* that are NOT
            in ``self.dunder_methods``.
        """
        present = frozenset(self.dunder_methods)
        return tuple(name for name in required if name not in present)

    # ------------------------------------------------------------------
    def surface_morphism(self, other: BehavioralSurface) -> Morphism:
        """Return the canonical morphism from this surface to *other*.

        The morphism kind encodes the relative richness of the two surfaces:

        * ``REFINEMENT`` — *other* implements strictly more protocols than
          ``self`` (other is more specific / more capable).
        * ``RESTRICTION`` — *other* implements strictly fewer protocols than
          ``self`` (other is a coarser view).
        * ``TRANSPORT`` — the two surfaces implement the same number of
          protocols (neither is strictly coarser or finer).

        Args:
            other: The ``BehavioralSurface`` to which the morphism points.

        Returns:
            A ``Morphism`` from ``self.coordinate`` to ``other.coordinate``.
        """
        self_count = len(self.protocols)
        other_count = len(other.protocols)
        if other_count > self_count:
            kind = MorphismKind.REFINEMENT
        elif other_count < self_count:
            kind = MorphismKind.RESTRICTION
        else:
            kind = MorphismKind.TRANSPORT
        label = f"surface({self.class_name}→{other.class_name})"
        return Morphism(
            source=self.coordinate,
            target=other.coordinate,
            kind=kind,
            label=label,
        )

    # ------------------------------------------------------------------
    def as_covering_family(self) -> CoveringFamily:
        """Return a ``CoveringFamily`` whose members are per-protocol coordinates.

        In the Grothendieck topology the behavioral surface is covered by the
        individual protocol coordinates — each protocol provides a local
        section of the observable behavior.  The covering family collects
        those local sections as ``INTERFACE``-kind coordinates.

        Returns:
            A ``CoveringFamily`` based at ``self.coordinate`` with one member
            coordinate per protocol.
        """
        members = [
            Coordinate(
                components=(self.class_name, p),
                kind=CoordinateKind.INTERFACE,
                support_labels=frozenset({self.class_name, p}),
            )
            for p in self.protocols
        ]
        return CoveringFamily(
            base=self.coordinate,
            members=members,
            label=f"behavioral_cover({self.class_name})",
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise the behavioral surface to a plain dictionary.

        Returns:
            A ``dict`` with all fields; coordinate is represented by its
            ``components`` list and ``kind`` name.
        """
        return {
            "class_name": self.class_name,
            "coordinate": list(self.coordinate.components),
            "coordinate_kind": self.coordinate.kind.name,
            "protocols": list(self.protocols),
            "dunder_methods": list(self.dunder_methods),
            "abstract_methods": list(self.abstract_methods),
            "trust": self.trust.name,
            "judgment_index": dict(self.judgment_index),
        }

    # ------------------------------------------------------------------
    def merge_with(self, other: BehavioralSurface) -> BehavioralSurface:
        """Return a new surface that is the union of ``self`` and *other*.

        The merged surface:

        * Combines protocols, dunder methods, and abstract methods via set
          union (preserving original ordering then appending new names).
        * Takes the *minimum* trust of the two surfaces — the merged record
          is only as trustworthy as its weaker constituent.
        * Merges ``judgment_index`` dictionaries (``other``'s values win on
          key collisions).
        * Sets ``class_name`` to ``"{self.class_name}+{other.class_name}"``.
        * Retains ``self.coordinate`` as the base coordinate.

        Args:
            other: The ``BehavioralSurface`` to merge into this one.

        Returns:
            A new ``BehavioralSurface`` representing the combined surface.
        """
        merged_protocols = tuple(
            dict.fromkeys(list(self.protocols) + list(other.protocols))
        )
        merged_dunders = tuple(
            dict.fromkeys(list(self.dunder_methods) + list(other.dunder_methods))
        )
        merged_abstract = tuple(
            dict.fromkeys(list(self.abstract_methods) + list(other.abstract_methods))
        )
        merged_index = {**self.judgment_index, **other.judgment_index}
        merged_trust = _coerce_trust_min(self.trust, other.trust)
        return replace(
            self,
            class_name=f"{self.class_name}+{other.class_name}",
            protocols=merged_protocols,
            dunder_methods=merged_dunders,
            abstract_methods=merged_abstract,
            trust=merged_trust,
            judgment_index=merged_index,
        )

    # ------------------------------------------------------------------
    def trust_surface(self) -> TrustLevel:
        """Return the effective trust level of this surface.

        Abstract classes are considered to have partial specification: if any
        abstract methods remain unimplemented the surface trust is capped at
        ``ORACLE_PROPOSED`` to signal that the specification is still
        incomplete.  Fully concrete surfaces carry their own ``self.trust``.

        Returns:
            ``min(self.trust, TrustLevel.ORACLE_PROPOSED)`` when there are
            unimplemented abstract methods; ``self.trust`` otherwise.
        """
        if self.abstract_methods:
            return _coerce_trust_min(self.trust, TrustLevel.ORACLE_PROPOSED)
        return self.trust

    # ------------------------------------------------------------------
    def as_judgment(self) -> Judgment:
        """Build and return a ``Judgment`` encoding this behavioral surface.

        The resulting judgment has:

        * ``coordinate``   — ``self.coordinate``
        * ``proposition``  — ``PropositionKind.BEHAVIORAL``, formula
          ``behavioral_surface(<class_name>)``
        * ``carrier``      — ``Carrier`` named after the class with protocol
          list in parameters
        * ``trust``        — ``self.trust_surface()``
        * ``provenance``   — ``ProvenanceSource.ORACLE`` (reflecting that
          behavioral surfaces are often inferred by CopilotChannel static
          analysis with COPILOT_SUGGESTED trust before runtime confirmation)

        Returns:
            A ``Judgment`` instance capturing the behavioral surface claim.
        """
        effective_trust = self.trust_surface()
        proposition = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=f"behavioral_surface({self.class_name})",
            free_variables=tuple(self.protocols),
            metadata={"abstract_methods": list(self.abstract_methods)},
        )
        carrier = Carrier(
            name=self.class_name,
            parameters={"protocols": list(self.protocols)},
            is_dependent=bool(self.abstract_methods),
            metadata={"dunder_methods": list(self.dunder_methods)},
        )
        provenance = _judgment_provenance(ProvenanceSource.ORACLE)
        return (
            JudgmentBuilder()
            .at(self.coordinate)
            .claiming(proposition)
            .of_type(carrier)
            .with_trust_level(effective_trust)
            .from_source(ProvenanceSource.ORACLE)
            .build()
        )


# ---
# §20.4  DescriptorChain
# ---


@dataclass(frozen=True, slots=True)
class DescriptorChain:
    r"""MRO-ordered descriptor morphism sequence for a single attribute.

    Corresponds to theory2.tex Ch20 §20.4.  Python's attribute lookup protocol
    is a three-way contest between the type's data-descriptor, the instance
    dictionary, and the type's non-data descriptor.  This record encodes that
    contest geometrically as a sequence of RESTRICTION morphisms ordered by
    the C3 MRO.

    CopilotChannel can propose a ``DescriptorChain`` with COPILOT_SUGGESTED
    (ORACLE_PROPOSED) trust for attributes that are first observed via static
    analysis; the chain is confirmed when runtime witnesses the ``__get__``
    call and upgrades the trust to RUNTIME_WITNESSED.

    Attributes:
        attribute_name: Name of the attribute this chain describes.
        owner_class:    Name of the class that originally defined the attribute.
        coordinate:     Geometric coordinate of the owner class.
        chain:          Class names in MRO order that define this attribute.
        descriptor_kind: One of ``"DATA"``, ``"NON_DATA"``, or ``"SLOT"``.
        trust:          Trust level of this descriptor chain record.
        override_map:   Maps class name → descriptor type string (e.g.
                        ``"property"``, ``"classmethod"``, ``"slot"``).
    """

    attribute_name: str
    owner_class: str
    coordinate: Coordinate
    chain: tuple[str, ...]
    descriptor_kind: str
    trust: TrustLevel
    override_map: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def resolve_get(self, instance_class: str) -> str:
        """Return the class name that wins ``__get__`` for this descriptor.

        The resolution rules follow CPython's ``type_getattro`` logic:

        * **DATA** descriptors (those defining both ``__get__`` and
          ``__set__``/``__delete__``) always win; the first class in ``chain``
          is returned.
        * **NON_DATA** descriptors lose to the instance ``__dict__``; if
          *instance_class* appears in ``override_map`` it is returned,
          otherwise the first class in ``chain`` is used.
        * **SLOT** descriptors are always resolved on the owner class
          regardless of instance type.

        Args:
            instance_class: The runtime type of the instance performing the
                attribute access.

        Returns:
            The name of the class whose descriptor definition wins.
        """
        if self.descriptor_kind == "DATA":
            return self.chain[0] if self.chain else self.owner_class
        if self.descriptor_kind == "NON_DATA":
            if instance_class in self.override_map:
                return instance_class
            return self.chain[0] if self.chain else self.owner_class
        # SLOT
        return self.owner_class

    # ------------------------------------------------------------------
    def resolve_set(self, instance_class: str) -> str:
        """Return the class name that handles ``__set__`` for this descriptor.

        For DATA descriptors the descriptor's ``__set__`` is authoritative.
        For NON_DATA descriptors the assignment goes into the instance
        ``__dict__``, represented here by returning *instance_class*.  For
        SLOT descriptors the slot is always on the owner class.

        Args:
            instance_class: The runtime type of the instance performing the
                attribute assignment.

        Returns:
            The class name responsible for the ``__set__`` operation.
        """
        if self.descriptor_kind == "DATA":
            return self.chain[0] if self.chain else self.owner_class
        if self.descriptor_kind == "NON_DATA":
            return instance_class
        # SLOT
        return self.owner_class

    # ------------------------------------------------------------------
    def is_data_descriptor(self) -> bool:
        """Return True when this chain represents a data descriptor.

        A data descriptor defines both ``__get__`` and at least one of
        ``__set__`` or ``__delete__``, giving it priority over the instance
        ``__dict__`` in attribute lookup.

        Returns:
            ``True`` iff ``self.descriptor_kind == "DATA"``.
        """
        return self.descriptor_kind == "DATA"

    # ------------------------------------------------------------------
    def as_morphism_sequence(self) -> list[Morphism]:
        """Return the chain of RESTRICTION morphisms encoding MRO override order.

        Each consecutive pair of classes in the chain is connected by a
        RESTRICTION morphism: the more-derived class restricts the more-base
        class's attribute definition.  The morphisms are ordered from
        most-derived to least-derived (i.e., the direction of Python's MRO
        traversal).

        Returns:
            A list of ``Morphism`` objects, one per consecutive pair in the
            chain.  Returns an empty list if the chain has fewer than two
            elements.
        """
        coords = [
            Coordinate(
                components=(self.attribute_name, cls),
                kind=CoordinateKind.FUNCTION,
                support_labels=frozenset({self.attribute_name, cls}),
            )
            for cls in self.chain
        ]
        morphisms: list[Morphism] = []
        for i in range(len(coords) - 1):
            morphisms.append(
                Morphism(
                    source=coords[i],
                    target=coords[i + 1],
                    kind=MorphismKind.RESTRICTION,
                    label=f"{self.attribute_name}:{self.chain[i]}→{self.chain[i + 1]}",
                )
            )
        return morphisms

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise the descriptor chain to a plain dictionary.

        Returns:
            A ``dict`` representing all fields; the coordinate is reduced to
            its ``components`` list and ``kind`` name.
        """
        return {
            "attribute_name": self.attribute_name,
            "owner_class": self.owner_class,
            "coordinate": list(self.coordinate.components),
            "coordinate_kind": self.coordinate.kind.name,
            "chain": list(self.chain),
            "descriptor_kind": self.descriptor_kind,
            "trust": self.trust.name,
            "override_map": dict(self.override_map),
        }

    # ------------------------------------------------------------------
    def chain_depth(self) -> int:
        """Return the number of classes that define this attribute.

        A depth of 1 means the attribute is defined only on one class; a
        greater depth indicates multiple levels of override in the MRO, which
        may be evidence of a complex descriptor protocol.

        Returns:
            ``len(self.chain)``
        """
        return len(self.chain)

    # ------------------------------------------------------------------
    def conflicts_in_mro(self) -> list[tuple[str, str]]:
        """Return pairs of sibling classes that both define this attribute.

        Two classes *conflict* if they both appear in the chain but neither
        appears in the other's "upstream" portion of the chain (i.e., they
        are siblings rather than ancestors of one another).  This is the
        descriptor analogue of the metaclass conflict test.

        The check is approximated by examining the relative positions in
        ``self.chain``: for each pair (i, j) with i < j we test whether the
        class at position j also appears *before* the class at position i —
        which cannot happen in a linear MRO — so any pair where the two
        classes are not in a strict ancestor–descendant relationship (one does
        not re-appear elsewhere in the chain relative to the other) is flagged.

        In practice this returns pairs (chain[i], chain[j]) for all i < j
        where chain[i] and chain[j] both appear but are not in an
        ancestor–descendant relationship inferred purely from their chain
        positions.  Because MRO chains are already linearised, conflicts
        manifest as classes that define the attribute at the *same* "level"
        without one delegating to the other.

        Returns:
            A list of ``(cls1, cls2)`` name tuples indicating conflicting
            pairs.  An empty list means the chain is conflict-free.
        """
        conflicts: list[tuple[str, str]] = []
        chain_list = list(self.chain)
        n = len(chain_list)
        for i in range(n):
            for j in range(i + 1, n):
                cls_i = chain_list[i]
                cls_j = chain_list[j]
                # cls_j comes after cls_i in MRO order (cls_i is more derived).
                # A *conflict* arises when cls_j also appears *before* cls_i in
                # the override_map with an incompatible type, signalling that
                # they are truly siblings rather than an ancestor–descendant
                # pair.
                kind_i = self.override_map.get(cls_i, "")
                kind_j = self.override_map.get(cls_j, "")
                if kind_i and kind_j and kind_i != kind_j:
                    conflicts.append((cls_i, cls_j))
        return conflicts


# ---
# §20.5  ClassCreationTrace
# ---


@dataclass(frozen=True, slots=True)
class ClassCreationTrace:
    r"""Full record of the three-phase Python class-creation protocol.

    Corresponds to theory2.tex Ch20 §20.5.  Python's class creation proceeds
    in three distinct phases:

    1. **prepare** — ``metaclass.__prepare__(name, bases, **kwargs)`` returns
       a namespace mapping (usually an ordered dict).
    2. **body** — the class body is executed inside that namespace.
    3. **new** — ``metaclass.__new__(metaclass, name, bases, namespace)``
       constructs the type object and calls ``__init_subclass__`` on each
       base.

    This record captures all three phases together with the geometric
    coordinates that link them into the site.  CopilotChannel evidence items
    with COPILOT_SUGGESTED trust may be attached to the resulting judgments
    when the trace is constructed from static analysis rather than live
    execution.

    Attributes:
        class_name:           The name of the class being created.
        coordinate:           Geometric coordinate of the resulting class.
        namespace_coordinate: Coordinate of the prepare-phase namespace.
        metaclass:            The ``MetaclassRecord`` for this class.
        prepare_result:       Snapshot of the namespace after ``__prepare__``.
        body_names:           Names bound in the class body, in order.
        init_subclass_called: Whether ``__init_subclass__`` was invoked.
        trust:                Trust level of the overall trace.
        created_at:           ISO-8601 creation timestamp.
    """

    class_name: str
    coordinate: Coordinate
    namespace_coordinate: Coordinate
    metaclass: MetaclassRecord
    prepare_result: dict[str, Any]
    body_names: tuple[str, ...]
    init_subclass_called: bool
    trust: TrustLevel
    created_at: str

    # ------------------------------------------------------------------
    def creation_morphisms(self) -> list[Morphism]:
        """Return the three morphisms representing the class-creation phases.

        The three morphisms are, in execution order:

        1. ``TRANSPORT`` from ``metaclass.metaclass_coordinate`` to
           ``namespace_coordinate``, labelled ``"__prepare__"``.
        2. ``INCLUSION`` from ``namespace_coordinate`` to ``coordinate``,
           labelled ``"body_execution"``.
        3. ``REFINEMENT`` from ``coordinate`` to ``metaclass.coordinate``
           labelled ``"__init_subclass__"`` when ``init_subclass_called``
           is ``True``; otherwise ``TRANSPORT`` labelled ``"type.__new__"``.

        Returns:
            A list of exactly three ``Morphism`` instances.
        """
        prepare_morphism = Morphism(
            source=self.metaclass.metaclass_coordinate,
            target=self.namespace_coordinate,
            kind=MorphismKind.TRANSPORT,
            label="__prepare__",
        )
        body_morphism = Morphism(
            source=self.namespace_coordinate,
            target=self.coordinate,
            kind=MorphismKind.INCLUSION,
            label="body_execution",
        )
        if self.init_subclass_called:
            final_morphism = Morphism(
                source=self.coordinate,
                target=self.metaclass.coordinate,
                kind=MorphismKind.REFINEMENT,
                label="__init_subclass__",
            )
        else:
            final_morphism = Morphism(
                source=self.coordinate,
                target=self.metaclass.coordinate,
                kind=MorphismKind.TRANSPORT,
                label="type.__new__",
            )
        return [prepare_morphism, body_morphism, final_morphism]

    # ------------------------------------------------------------------
    def body_coordinate(self) -> Coordinate:
        """Return the geometric coordinate of the class body execution region.

        The body coordinate is a ``REGION``-kind coordinate labelled with the
        first five names bound in the class body (used as support labels for
        the sheaf section).

        Returns:
            A ``Coordinate`` with ``kind=CoordinateKind.REGION``.
        """
        support = frozenset(self.body_names[:5])
        return Coordinate(
            components=(self.class_name, "body"),
            kind=CoordinateKind.REGION,
            support_labels=support,
        )

    # ------------------------------------------------------------------
    def as_judgment(self) -> Judgment:
        """Build and return a ``Judgment`` for this class-creation trace.

        The judgment records the structural claim that the class was created
        according to the three-phase protocol.  When the trace was obtained
        via CopilotChannel static analysis the COPILOT_SUGGESTED trust
        (``ORACLE_PROPOSED``) is preserved in the provenance; runtime
        confirmation upgrades the trust to ``RUNTIME_WITNESSED``.

        Returns:
            A ``Judgment`` with a ``STRUCTURAL`` proposition.
        """
        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=f"class_creation_trace({self.class_name})",
            free_variables=tuple(self.body_names[:10]),
            metadata={
                "metaclass": self.metaclass.metaclass_name,
                "init_subclass_called": self.init_subclass_called,
            },
        )
        carrier = self.metaclass.as_carrier()
        provenance = _judgment_provenance(ProvenanceSource.RUNTIME)
        return (
            JudgmentBuilder()
            .at(self.coordinate)
            .claiming(proposition)
            .of_type(carrier)
            .with_trust_level(self.trust)
            .from_source(ProvenanceSource.RUNTIME)
            .build()
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise the full creation trace to a plain dictionary.

        The nested ``MetaclassRecord`` is serialised using its own
        ``to_dict()`` method.

        Returns:
            A recursively serialisable ``dict``.
        """
        return {
            "class_name": self.class_name,
            "coordinate": list(self.coordinate.components),
            "coordinate_kind": self.coordinate.kind.name,
            "namespace_coordinate": list(self.namespace_coordinate.components),
            "namespace_coordinate_kind": self.namespace_coordinate.kind.name,
            "metaclass": self.metaclass.to_dict(),
            "prepare_result": {
                k: str(v) for k, v in self.prepare_result.items()
            },
            "body_names": list(self.body_names),
            "init_subclass_called": self.init_subclass_called,
            "trust": self.trust.name,
            "created_at": self.created_at,
        }

    # ------------------------------------------------------------------
    def verify_completeness(self) -> bool:
        """Return True when the trace contains the minimum required information.

        A complete trace must have:

        * At least one name in ``body_names``.
        * A non-empty ``prepare_result`` dict.
        * A non-empty ``metaclass.metaclass_name``.
        * A non-empty ``coordinate.components`` tuple.

        Returns:
            ``True`` iff all four conditions are satisfied.
        """
        return (
            bool(self.body_names)
            and bool(self.prepare_result)
            and bool(self.metaclass.metaclass_name)
            and bool(self.coordinate.components)
        )

    # ------------------------------------------------------------------
    def descriptor_chain(self, attr: str) -> DescriptorChain:
        """Return a ``DescriptorChain`` for *attr* as seen from this trace.

        The descriptor kind is inferred from ``prepare_result``: if *attr*
        appears in ``prepare_result`` and its value string starts with
        ``"property"`` it is treated as a DATA descriptor; otherwise as
        NON_DATA.  The chain is constructed from the class itself followed
        by up to three of its direct base classes (from the metaclass record).

        Args:
            attr: The attribute name to look up.

        Returns:
            A ``DescriptorChain`` for *attr* anchored at this class.
        """
        raw_value = str(self.prepare_result.get(attr, ""))
        if attr in self.prepare_result and raw_value.startswith("property"):
            kind = "DATA"
        else:
            kind = "NON_DATA"

        chain: tuple[str, ...] = (self.class_name,) + self.metaclass.bases[:3]
        override_map: dict[str, str] = {}
        if attr in self.prepare_result:
            override_map[self.class_name] = raw_value or "method"
        for base in self.metaclass.bases[:3]:
            override_map.setdefault(base, "inherited")

        return DescriptorChain(
            attribute_name=attr,
            owner_class=self.class_name,
            coordinate=self.coordinate,
            chain=chain,
            descriptor_kind=kind,
            trust=self.trust,
            override_map=override_map,
        )

    # ------------------------------------------------------------------
    def surface(self) -> BehavioralSurface:
        """Extract a ``BehavioralSurface`` from this class-creation trace.

        The surface is constructed by scanning ``body_names`` for dunder
        methods and consulting ``prepare_result`` for protocol and abstract
        method lists.  The result represents the behavioral interface that
        Python code can observe after the class has been created.

        Returns:
            A ``BehavioralSurface`` derived from this trace.
        """
        dunder_methods = tuple(
            name
            for name in self.body_names
            if name.startswith("__") and name.endswith("__")
        )
        raw_abstract = self.prepare_result.get("abstract_methods", [])
        abstract_methods: tuple[str, ...] = (
            tuple(raw_abstract)
            if isinstance(raw_abstract, (list, tuple))
            else ()
        )
        raw_protocols = self.prepare_result.get("protocols", [])
        protocols: tuple[str, ...] = (
            tuple(raw_protocols)
            if isinstance(raw_protocols, (list, tuple))
            else ()
        )
        return BehavioralSurface(
            class_name=self.class_name,
            coordinate=self.coordinate,
            protocols=protocols,
            dunder_methods=dunder_methods,
            abstract_methods=abstract_methods,
            trust=self.trust,
            judgment_index={},
        )


# ---
# Module-level registry helpers
# ---

_DESCRIPTOR_KIND_PRECEDENCE: dict[str, int] = {
    "SLOT": 3,
    "DATA": 2,
    "NON_DATA": 1,
}


def _metaclass_coordinate(class_name: str, metaclass_name: str) -> Coordinate:
    """Build a canonical Coordinate for a metaclass relationship.

    Used by ``MetaclassRecord`` and copilot-assisted trace builders to
    ensure that all metaclass-related coordinates are constructed uniformly
    and carry the appropriate support labels for sheaf-section matching.

    Args:
        class_name:      The name of the class whose metaclass relationship
                         is being encoded.
        metaclass_name:  The fully-qualified name of the metaclass.

    Returns:
        A ``Coordinate`` with ``kind=CoordinateKind.INTERFACE`` and
        ``support_labels={class_name, metaclass_name}``.
    """
    return Coordinate(
        components=(class_name, metaclass_name, "meta"),
        kind=CoordinateKind.INTERFACE,
        support_labels=frozenset({class_name, metaclass_name}),
    )


def _class_coordinate(class_name: str, module: str = "unknown") -> Coordinate:
    """Build a canonical Coordinate for a class object.

    Constructs the standard ``(module, class_name)`` coordinate used
    throughout the metaobject surface machinery to identify a class within
    the site.

    Args:
        class_name: The simple (unqualified) class name.
        module:     The module in which the class is defined; defaults to
                    ``"unknown"`` when module information is unavailable.

    Returns:
        A ``Coordinate`` with ``kind=CoordinateKind.INTERFACE`` and
        ``support_labels={class_name}``.
    """
    return Coordinate(
        components=(module, class_name),
        kind=CoordinateKind.INTERFACE,
        support_labels=frozenset({class_name}),
    )


def _now_str() -> str:
    """Return current ISO timestamp string.

    Convenience wrapper used when constructing ``created_at`` fields on
    new ``MetaclassRecord``, ``ClassCreationTrace``, and related records.

    Returns:
        Current UTC time as an ISO-8601 string ending in ``"Z"``.
    """
    return datetime.datetime.utcnow().isoformat() + "Z"
