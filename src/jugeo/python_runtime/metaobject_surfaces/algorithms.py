from __future__ import annotations

r"""theory2.tex Ch20 §20.6 — Algorithms for metaclass resolution, MRO computation,
and behavioral surface construction.

This module implements the core algorithmic machinery for the metaobject surface
subsystem of jugeo.  The algorithms here correspond to the formal definitions in
theory2.tex Chapter 20, sections 20.6.x, which treat Python's metaclass protocol
as a sheaf of judgments over a Grothendieck site of class coordinates.

Each algorithm is annotated with the trust level at which its output enters the
judgment system:

* ``ORACLE_PROPOSED`` — output produced by copilot-assisted analysis without
  runtime confirmation.
* ``RUNTIME_WITNESSED`` — output confirmed by live Python interpreter reflection.
* ``SOLVER_DISCHARGED`` — output formally discharged by the Z3 solver session.

Copilot-assisted analysis (CopilotChannel) plays a central role in proposing
candidate MROs and surfacing behavioral protocols.  The copilot pipeline feeds
directly into the judgment builder at the ORACLE_PROPOSED trust ceiling.

See also:
  * ``theorems.py`` — formal theorems proved about these algorithms.
  * ``models.py`` — data models: MetaclassRecord, BehavioralSurface, etc.
"""

import hashlib
import json
import datetime
from dataclasses import dataclass, field, replace
from typing import Any

# ---
# Imports — jugeo geometry (with working stubs)
# ---

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

# ---
# Imports — jugeo judgment terms (with working stubs)
# ---

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

# ---
# Imports — jugeo Z3 solver (with working stubs)
# ---

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

# ---
# Imports — jugeo evidence channels (with working stubs)
# ---

try:
    from jugeo.evidence.channels import (
        EvidenceChannel, EvidenceRecord, EvidenceRequest, EvidenceResponse,
        ChannelRouter, CopilotChannel, SolverChannel, RuntimeChannel,
    )
except ImportError:
    class EvidenceChannel:
        COPILOT = "COPILOT"; SOLVER = "SOLVER"; RUNTIME = "RUNTIME"
    class EvidenceRecord:
        def __init__(self, **kw): self.__dict__.update(kw)
    class CopilotChannel:
        TRUST_CEILING = "proposal"
    class SolverChannel: pass
    class RuntimeChannel: pass
    class ChannelRouter: pass
    class EvidenceRequest: pass
    class EvidenceResponse: pass

# ---
# Imports — jugeo metaobject models (with working stubs)
# ---

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
        def is_abc(self): return "ABCMeta" in (self.metaclass_name or "")
    @_dc2(frozen=True, slots=True)
    class BehavioralSurface:
        class_name: str = ""; coordinate: object = None; protocols: tuple = ()
        dunder_methods: tuple = (); abstract_methods: tuple = ()
        trust: object = None; judgment_index: dict = _field2(default_factory=dict)
    @_dc2(frozen=True, slots=True)
    class DescriptorChain:
        attribute_name: str = ""; owner_class: str = ""; coordinate: object = None
        chain: tuple = (); descriptor_kind: str = "NON_DATA"
        trust: object = None; override_map: dict = _field2(default_factory=dict)
    @_dc2(frozen=True, slots=True)
    class ClassCreationTrace:
        class_name: str = ""; coordinate: object = None; namespace_coordinate: object = None
        metaclass: object = None; prepare_result: dict = _field2(default_factory=dict)
        body_names: tuple = (); init_subclass_called: bool = False
        trust: object = None; created_at: str = ""
        def surface(self): return BehavioralSurface(class_name=self.class_name)
    def _metaclass_coordinate(cn, mn): return Coordinate(components=(cn, mn, "meta"), kind=CoordinateKind.INTERFACE)
    def _class_coordinate(cn, mod="unknown"): return Coordinate(components=(mod, cn), kind=CoordinateKind.INTERFACE)
    def _now_str(): return datetime.datetime.utcnow().isoformat() + "Z"

# ---
# Module-level constants
# ---

#: Default MRO entries inserted for classes with no known linearization.
_DEFAULT_OBJECT_MRO: list[str] = ["object"]

#: Maximum merge-loop iterations before we assume a cycle bug (defensive cap).
_MAX_MERGE_ITERATIONS: int = 10_000


# ---
# Construction helpers for jugeo judgment types
# ---


def _make_trust(level: TrustLevel) -> TrustAnnotation:
    """Wrap a TrustLevel in a TrustAnnotation, handling both real and stub types.

    Parameters
    ----------
    level:
        The trust level to wrap.

    Returns
    -------
    TrustAnnotation
        A TrustAnnotation whose ``.level`` is *level*.
    """
    return TrustAnnotation(level=level)


def _make_carrier(name: str) -> Carrier:
    """Build a minimal Carrier with the given name.

    Parameters
    ----------
    name:
        The carrier name (typically a class or theorem identifier).

    Returns
    -------
    Carrier
        A Carrier with the given name and empty parameters.
    """
    return Carrier(name=name)


# ---
# Core algorithms
# ---


def compute_mro(
    class_name: str,
    bases: list[str],
    mro_map: dict[str, list[str]],
) -> list[str]:
    """Compute the C3 linearization (MRO) for *class_name*.

    C3 linearization is Python's method resolution order algorithm.
    It ensures that the MRO is:
    1. Consistent with the local precedence order of each class.
    2. Monotonic (preserves the order of base classes).
    3. Well-founded (no cycles in non-pathological cases).

    The algorithm:
      L[C] = C + merge(L[B1], L[B2], ..., L[Bn], [B1, B2, ..., Bn])
    where merge takes the head of the first list if it doesn't appear
    as a non-head anywhere, removes it from all lists, and repeats.

    This is the canonical algorithm used by type.__new__ for all Python
    classes.  Copilot-assisted MRO analysis enters at ORACLE_PROPOSED
    trust and requires runtime verification.

    Parameters
    ----------
    class_name:
        The name of the class whose MRO is being computed.
    bases:
        The direct base classes of *class_name*, in declaration order.
    mro_map:
        A dict mapping each base class name to its known MRO list.
        Missing entries default to [base_name, "object"].

    Returns
    -------
    list[str]
        The C3 linearization starting with *class_name* and ending with
        ``"object"`` (unless *class_name* is ``"object"``).

    Raises
    ------
    TypeError
        If no valid C3 linearization exists (inconsistent ordering).

    Examples
    --------
    >>> compute_mro("C", ["A", "B"], {"A": ["A", "object"], "B": ["B", "object"]})
    ['C', 'A', 'B', 'object']
    """
    if class_name == "object":
        return ["object"]

    # Build per-base linearization lists; fall back to [base, "object"] if unknown.
    def _base_mro(name: str) -> list[str]:
        if name in mro_map:
            return list(mro_map[name])
        return [name, "object"]

    # Construct the sequences to merge:
    #   L[B1], L[B2], ..., L[Bn], plus the bases list itself.
    sequences: list[list[str]] = [_base_mro(b) for b in bases] + [list(bases)]

    result: list[str] = [class_name]
    iterations = 0

    while True:
        iterations += 1
        if iterations > _MAX_MERGE_ITERATIONS:
            raise TypeError(
                f"C3 linearization exceeded iteration cap for {class_name!r}; "
                "likely a cycle in the base graph."
            )

        # Remove exhausted sequences.
        sequences = [s for s in sequences if s]
        if not sequences:
            break

        # Find a good head: one that does not appear in any tail.
        winner: str | None = None
        for seq in sequences:
            candidate = seq[0]
            # Check whether candidate appears in any tail (any seq[1:]).
            in_tail = any(candidate in s[1:] for s in sequences)
            if not in_tail:
                winner = candidate
                break

        if winner is None:
            # All heads appear in some tail — no valid linearization.
            heads = [s[0] for s in sequences if s]
            raise TypeError(
                f"Cannot create a consistent MRO for {class_name!r}. "
                f"Conflicting heads: {heads!r}.  "
                "Ensure there is no circular inheritance or incompatible base ordering."
            )

        result.append(winner)
        # Remove winner from all sequences.
        for seq in sequences:
            if seq and seq[0] == winner:
                seq.pop(0)

    # Guarantee "object" appears at the end if not already present and class != object.
    if result and result[-1] != "object":
        result.append("object")

    return result


# ---


def resolve_metaclass(
    metaclasses: list[MetaclassRecord],
) -> MetaclassRecord:
    """Find the most-derived metaclass among a list of candidates.

    Python requires that the metaclass of a new class be a subtype of
    every base's metaclass.  This function finds the "winner": the most-
    derived metaclass in the list.

    If no single metaclass is most-derived (no winner exists), raises
    TypeError with a descriptive message listing the conflicting pairs.

    CopilotChannel suggestions for resolution enter at ORACLE_PROPOSED.

    Parameters
    ----------
    metaclasses:
        List of MetaclassRecords from each base class.

    Returns
    -------
    MetaclassRecord
        The most-derived metaclass.

    Raises
    ------
    TypeError
        If no winner exists (metaclass conflict).

    Examples
    --------
    >>> # If A uses type and B uses ABCMeta (which subclasses type),
    >>> # the winner is the record for B (ABCMeta).
    """
    if not metaclasses:
        raise ValueError("resolve_metaclass requires a non-empty list of MetaclassRecords.")

    if len(metaclasses) == 1:
        return metaclasses[0]

    # A candidate is a winner if its metaclass_name appears in every other
    # record's mro tuple (meaning it is at least as derived as all others).
    for candidate in metaclasses:
        mn = candidate.metaclass_name
        if all(mn in other.mro for other in metaclasses):
            return candidate

    # No winner — collect all conflicting pairs for a helpful error message.
    conflicts: list[tuple[str, str]] = []
    for i, a in enumerate(metaclasses):
        for b in metaclasses[i + 1:]:
            a_in_b = a.metaclass_name in b.mro
            b_in_a = b.metaclass_name in a.mro
            if not a_in_b and not b_in_a:
                conflicts.append((a.metaclass_name, b.metaclass_name))

    conflict_strs = ", ".join(f"({x} vs {y})" for x, y in conflicts)
    raise TypeError(
        f"metaclass conflict: no most-derived metaclass found. "
        f"Conflicting pairs: {conflict_strs}.  "
        "Define a new metaclass that subclasses all conflicting metaclasses."
    )


# ---


def build_behavioral_surface(
    class_name: str,
    trace: ClassCreationTrace,
) -> BehavioralSurface:
    """Extract the observable behavioral surface from a class creation trace.

    The behavioral surface consists of:
    - All dunder methods defined in the class body
    - All protocols declared in the prepare_result
    - All abstract methods flagged during body execution

    This is the copilot-assisted surface extraction algorithm: it analyzes
    the trace's body_names and prepare_result to build a BehavioralSurface
    at the appropriate trust level.

    Parameters
    ----------
    class_name:
        The class name (should match trace.class_name).
    trace:
        The ClassCreationTrace from which to extract the surface.

    Returns
    -------
    BehavioralSurface
        The extracted behavioral surface with trust from the trace.

    Notes
    -----
    Dunder methods are names that both start and end with ``__``.  The
    copilot pipeline automatically flags these during body introspection at
    ORACLE_PROPOSED trust; runtime confirmation upgrades them to
    RUNTIME_WITNESSED.
    """
    # Extract dunder methods: names that start and end with "__".
    dunder_methods: tuple[str, ...] = tuple(
        name for name in trace.body_names
        if name.startswith("__") and name.endswith("__") and len(name) > 4
    )

    # Pull protocols from the prepare_result dict (set by __init_subclass__ hooks).
    raw_protocols = trace.prepare_result.get("protocols", []) if trace.prepare_result else []
    protocols: tuple[str, ...] = tuple(raw_protocols)

    # Pull abstract methods flagged by ABCMeta or similar during body execution.
    raw_abstracts = trace.prepare_result.get("abstract_methods", []) if trace.prepare_result else []
    abstract_methods: tuple[str, ...] = tuple(raw_abstracts)

    # Build the judgment_index: maps each dunder name to a stable judgment id.
    judgment_index: dict[str, str] = {
        name: f"judgment_{_stable_hash(class_name + name)}"
        for name in dunder_methods
    }

    # The coordinate for the surface is derived from the trace's coordinate.
    surface_coordinate = trace.coordinate

    return BehavioralSurface(
        class_name=class_name,
        coordinate=surface_coordinate,
        protocols=protocols,
        dunder_methods=dunder_methods,
        abstract_methods=abstract_methods,
        trust=trace.trust,
        judgment_index=judgment_index,
    )


# ---


def resolve_descriptor_chain(
    attr: str,
    mro: list[str],
    descriptors: dict[str, DescriptorChain],
) -> DescriptorChain:
    """Walk the MRO to find the winning DescriptorChain for *attr*.

    Resolution order (per Python data model):
    1. Data descriptors from MRO (descriptor_kind == "DATA")
    2. Instance __dict__ (not modeled here — falls through to step 3)
    3. Non-data descriptors and class variables from MRO

    Returns the first matching DescriptorChain, prioritizing DATA descriptors.
    If no chain exists for *attr*, synthesizes a minimal NON_DATA chain.

    Parameters
    ----------
    attr:
        The attribute name being resolved.
    mro:
        The MRO list for the class performing the lookup.
    descriptors:
        Dict mapping attribute names to their known DescriptorChains.

    Returns
    -------
    DescriptorChain
        The winning chain.

    Notes
    -----
    The copilot pipeline enriches the descriptor map at ORACLE_PROPOSED trust
    during class body analysis.  Data descriptor precedence is a formal theorem
    (Theorem_DescriptorDataPrecedence in theorems.py) and holds regardless of
    the instance __dict__ contents.
    """
    existing = descriptors.get(attr)

    # Step 1: If we have a DATA descriptor for this attribute, it wins immediately.
    if existing is not None and existing.descriptor_kind == "DATA":
        return existing

    # Step 2: Walk the MRO to find the first class that has a chain whose
    # owner_class appears in the MRO.  Prefer DATA descriptors first.
    data_winner: DescriptorChain | None = None
    non_data_winner: DescriptorChain | None = None

    for class_in_mro in mro:
        # Check all descriptors to find one whose chain covers this MRO class.
        for chain_attr, chain in descriptors.items():
            if chain_attr != attr:
                continue
            # The chain's owner_class or any class in its chain tuple must match.
            owner_in_mro = chain.owner_class == class_in_mro
            chain_intersects_mro = any(c == class_in_mro for c in chain.chain)
            if owner_in_mro or chain_intersects_mro:
                if chain.descriptor_kind == "DATA" and data_winner is None:
                    data_winner = chain
                elif chain.descriptor_kind != "DATA" and non_data_winner is None:
                    non_data_winner = chain

    if data_winner is not None:
        return data_winner
    if non_data_winner is not None:
        return non_data_winner
    if existing is not None:
        return existing

    # Step 3: Synthesize a minimal chain for an attribute not found in any descriptor.
    first_class = mro[0] if mro else "object"
    synthetic_coord = Coordinate(
        components=(first_class, attr, "synthetic"),
        kind=CoordinateKind.INTERFACE,
    )
    return DescriptorChain(
        attribute_name=attr,
        owner_class=first_class,
        coordinate=synthetic_coord,
        chain=tuple(mro[:1]),
        descriptor_kind="NON_DATA",
        trust=TrustLevel.UNVERIFIED,
        override_map={},
    )


# ---


def detect_metaclass_conflicts(
    records: list[MetaclassRecord],
) -> list[tuple[MetaclassRecord, MetaclassRecord]]:
    """Find all pairs of MetaclassRecords with incompatible metaclasses.

    Two records conflict if neither metaclass is in the other's MRO.
    These conflicts correspond to Obstructions in the judgment sheaf
    (copilot-assisted detection at ORACLE_PROPOSED trust).

    Parameters
    ----------
    records:
        List of MetaclassRecords to check for conflicts.

    Returns
    -------
    list[tuple[MetaclassRecord, MetaclassRecord]]
        All conflicting pairs (a, b) where a < b in index order.

    Notes
    -----
    The returned pairs are used as inputs to
    ``Theorem_MetaclassConflictObstruction.obstruction_for()`` in
    theorems.py to produce formal Obstruction objects for the judgment
    sheaf.
    """
    conflicts: list[tuple[MetaclassRecord, MetaclassRecord]] = []
    n = len(records)
    for i in range(n):
        for j in range(i + 1, n):
            a = records[i]
            b = records[j]
            a_in_b_mro = a.metaclass_name in b.mro
            b_in_a_mro = b.metaclass_name in a.mro
            if not a_in_b_mro and not b_in_a_mro:
                conflicts.append((a, b))
    return conflicts


# ---


def build_class_site(
    traces: list[ClassCreationTrace],
) -> Site:
    """Build a Site from a list of ClassCreationTraces.

    Each trace contributes:
    - Its class coordinate (the new class)
    - Its namespace coordinate (the __prepare__ result)
    - Its metaclass coordinate
    - Creation morphisms (from creation_morphisms())
    - Inheritance morphisms (INCLUSION from each base coordinate to class coord)

    The resulting Site represents the full class hierarchy as a Grothendieck
    site, with inheritance as inclusion morphisms.

    Parameters
    ----------
    traces:
        ClassCreationTraces, one per class in the hierarchy.

    Returns
    -------
    Site
        A Site object with all class coordinates and morphisms.

    Notes
    -----
    The copilot pipeline populates the trace list via reflection at
    ORACLE_PROPOSED trust; morphisms are later confirmed at
    RUNTIME_WITNESSED trust when the class hierarchy is live.
    """
    builder = SiteBuilder()

    # Build a lookup map from class_name to its trace for resolving base coords.
    name_to_trace: dict[str, ClassCreationTrace] = {
        t.class_name: t for t in traces
    }

    for trace in traces:
        # Add the primary coordinates for this trace.
        if trace.coordinate is not None:
            builder.add_coordinate(trace.coordinate)
        if trace.namespace_coordinate is not None:
            builder.add_coordinate(trace.namespace_coordinate)
        if trace.metaclass is not None and getattr(trace.metaclass, "metaclass_coordinate", None) is not None:
            builder.add_coordinate(trace.metaclass.metaclass_coordinate)

        # Add creation morphisms: TRANSPORT from metaclass coord to class coord.
        if (
            trace.metaclass is not None
            and getattr(trace.metaclass, "metaclass_coordinate", None) is not None
            and trace.coordinate is not None
        ):
            creation_morph = Morphism(
                source=trace.metaclass.metaclass_coordinate,
                target=trace.coordinate,
                kind=MorphismKind.TRANSPORT,
                label=f"type.__new__({trace.class_name})",
            )
            builder.add_morphism(creation_morph)

        # Add namespace morphism: RESTRICTION from namespace_coordinate to class coord.
        if trace.namespace_coordinate is not None and trace.coordinate is not None:
            ns_morph = Morphism(
                source=trace.namespace_coordinate,
                target=trace.coordinate,
                kind=MorphismKind.RESTRICTION,
                label=f"__prepare__({trace.class_name})",
            )
            builder.add_morphism(ns_morph)

        # Add inheritance morphisms: INCLUSION from each base to this class.
        base_names: tuple[str, ...]
        if trace.metaclass is not None:
            base_names = getattr(trace.metaclass, "bases", ())
        else:
            base_names = ()

        for base_name in base_names:
            base_trace = name_to_trace.get(base_name)
            if base_trace is not None and base_trace.coordinate is not None and trace.coordinate is not None:
                inclusion_morph = Morphism(
                    source=base_trace.coordinate,
                    target=trace.coordinate,
                    kind=MorphismKind.INCLUSION,
                    label=f"inherits({trace.class_name}, {base_name})",
                )
                builder.add_morphism(inclusion_morph)

    return builder.build()


# ---
# MROAlgorithmTracer — step-by-step C3 merge recorder
# ---


class MROAlgorithmTracer:
    """Traces the C3 merge algorithm step by step.

    Used for debugging and for building a detailed Judgment about the
    well-foundedness of the MRO computation.  Each merge step is recorded
    as a RESTRICTION morphism in the computation site.

    theory2.tex Ch20 §20.6.1

    The tracer is designed to work alongside the copilot pipeline: as
    the copilot proposes candidate MROs, the tracer records each decision
    at ORACLE_PROPOSED trust.  Runtime verification then confirms the final
    order at RUNTIME_WITNESSED trust.

    Attributes
    ----------
    class_name:
        The class whose MRO computation is being traced.

    Examples
    --------
    >>> tracer = MROAlgorithmTracer("MyClass")
    >>> tracer.record_step("A", [["A", "B"], ["B"]])
    >>> tracer.record_step("B", [["B"], []])
    >>> tracer.is_well_founded()
    True
    """

    def __init__(self, class_name: str) -> None:
        """Initialise the tracer for *class_name*.

        Parameters
        ----------
        class_name:
            The class whose C3 merge is being recorded.
        """
        self.class_name: str = class_name
        self._steps: list[dict[str, Any]] = []

    # ---

    def record_step(self, head: str, remaining: list[list[str]]) -> None:
        """Record one merge step: the chosen *head* and the *remaining* sequences.

        Each call appends one entry to the internal step list.  The step
        includes the current step index, the chosen head class name, and a
        snapshot of the remaining merge sequences after removing the head.

        Parameters
        ----------
        head:
            The class name chosen as the winner at this step.
        remaining:
            The merge sequences *after* removing *head* from their fronts.
        """
        self._steps.append({
            "step": len(self._steps),
            "head": head,
            "remaining": [list(r) for r in remaining],
        })

    # ---

    def steps(self) -> list[dict[str, Any]]:
        """Return a shallow copy of all recorded merge steps.

        Returns
        -------
        list[dict[str, Any]]
            Each dict contains ``"step"`` (int), ``"head"`` (str), and
            ``"remaining"`` (list of lists of str).
        """
        return list(self._steps)

    # ---

    def step_morphisms(self) -> list[Morphism]:
        """Build RESTRICTION morphisms for each recorded step.

        Each consecutive pair of steps (i, i+1) yields a RESTRICTION morphism
        from a ``CoordinateKind.THEOREM`` coordinate at step *i* to one at step
        *i+1*.  These morphisms encode the algorithmic derivation as a chain in
        the computation site and can be added to a ``SiteBuilder`` for sheaf
        analysis.

        Returns
        -------
        list[Morphism]
            One Morphism per consecutive pair of steps.
        """
        morphisms: list[Morphism] = []
        for i in range(len(self._steps) - 1):
            src = Coordinate(
                components=(self.class_name, str(i)),
                kind=CoordinateKind.THEOREM,
            )
            tgt = Coordinate(
                components=(self.class_name, str(i + 1)),
                kind=CoordinateKind.THEOREM,
            )
            morphisms.append(Morphism(
                source=src,
                target=tgt,
                kind=MorphismKind.RESTRICTION,
                label=f"c3_step_{i}_to_{i + 1}({self.class_name})",
            ))
        return morphisms

    # ---

    def as_judgment(self, final_mro: list[str], trust: TrustLevel) -> Judgment:
        """Build a Judgment asserting the well-foundedness of the traced MRO.

        The judgment uses a STRUCTURAL proposition whose formula encodes the
        class name and the length of the produced MRO.  The copilot pipeline
        sets trust to ORACLE_PROPOSED; the solver can promote it to
        SOLVER_DISCHARGED after formal verification.

        Parameters
        ----------
        final_mro:
            The final MRO list produced by the algorithm.
        trust:
            The trust level to assign to the judgment.

        Returns
        -------
        Judgment
            A Judgment representing the well-foundedness assertion.
        """
        formula = (
            f"c3_mro_well_founded({self.class_name}, {len(final_mro)}_steps)"
        )
        coord = Coordinate(
            components=(self.class_name, "mro_well_founded"),
            kind=CoordinateKind.THEOREM,
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=tuple(final_mro),
        )
        provenance = Provenance(
            source=ProvenanceSource.ORACLE,
            creation_timestamp=_now_iso(),
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={"mro": final_mro, "steps": len(self._steps)},
            trust_level=trust,
            channel=EvidenceChannel.COPILOT,
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=_make_carrier(self.class_name),
            trust=_make_trust(trust),
            evidence=bundle,
            provenance=provenance,
            status=JudgmentStatus.PROPOSED,
        )

    # ---

    def is_well_founded(self) -> bool:
        """Return True if the algorithm completed without getting stuck.

        The algorithm is considered well-founded if:
        1. At least one step was recorded (i.e., the class has bases).
        2. No step recorded an empty head string (the merge never got stuck).

        Returns
        -------
        bool
            True when the traced computation is well-founded.
        """
        if not self._steps:
            return True  # No bases ⇒ trivially well-founded.
        return all(bool(step.get("head", "")) for step in self._steps)


# ---
# Utility helpers
# ---


def _all_tails(sequences: list[list[str]]) -> frozenset[str]:
    """Return the union of all tails (seq[1:]) across all sequences.

    Parameters
    ----------
    sequences:
        The current merge sequences.

    Returns
    -------
    frozenset[str]
        All class names that appear as non-head elements.
    """
    tails: set[str] = set()
    for seq in sequences:
        tails.update(seq[1:])
    return frozenset(tails)


def _compute_mro_with_trace(
    class_name: str,
    bases: list[str],
    mro_map: dict[str, list[str]],
    tracer: MROAlgorithmTracer | None = None,
) -> list[str]:
    """Like :func:`compute_mro` but records steps into *tracer* if provided.

    This variant is used internally when the copilot pipeline needs a
    detailed audit trail of the C3 merge for judgment construction.

    Parameters
    ----------
    class_name:
        The class whose MRO is being computed.
    bases:
        Direct base classes in declaration order.
    mro_map:
        Dict mapping base names to their known MROs.
    tracer:
        Optional :class:`MROAlgorithmTracer` to receive step recordings.

    Returns
    -------
    list[str]
        The C3 linearization.

    Raises
    ------
    TypeError
        If no valid linearization exists.
    """
    if class_name == "object":
        return ["object"]

    def _base_mro(name: str) -> list[str]:
        if name in mro_map:
            return list(mro_map[name])
        return [name, "object"]

    sequences: list[list[str]] = [_base_mro(b) for b in bases] + [list(bases)]
    result: list[str] = [class_name]
    iterations = 0

    while True:
        iterations += 1
        if iterations > _MAX_MERGE_ITERATIONS:
            raise TypeError(
                f"C3 merge exceeded {_MAX_MERGE_ITERATIONS} iterations for {class_name!r}."
            )

        sequences = [s for s in sequences if s]
        if not sequences:
            break

        tails = _all_tails(sequences)
        winner: str | None = None
        for seq in sequences:
            if seq[0] not in tails:
                winner = seq[0]
                break

        if winner is None:
            heads = [s[0] for s in sequences if s]
            raise TypeError(
                f"Cannot create a consistent MRO for {class_name!r}. "
                f"Conflicting heads: {heads!r}."
            )

        result.append(winner)
        for seq in sequences:
            if seq and seq[0] == winner:
                seq.pop(0)

        if tracer is not None:
            tracer.record_step(winner, [list(s) for s in sequences if s])

    if result and result[-1] != "object":
        result.append("object")

    return result


def build_mro_judgment(
    class_name: str,
    bases: list[str],
    mro_map: dict[str, list[str]],
    trust: TrustLevel = TrustLevel.ORACLE_PROPOSED,
) -> tuple[list[str], Judgment]:
    """Compute the MRO and produce a Judgment asserting its well-foundedness.

    Combines :func:`compute_mro` with :class:`MROAlgorithmTracer` to return
    both the computed MRO and a copilot-generated Judgment suitable for
    submission to the judgment sheaf.

    Parameters
    ----------
    class_name:
        The class whose MRO is being computed.
    bases:
        Direct base classes in declaration order.
    mro_map:
        Dict mapping base names to their known MROs.
    trust:
        Trust level to assign the resulting Judgment.

    Returns
    -------
    tuple[list[str], Judgment]
        The computed MRO and the associated Judgment.
    """
    tracer = MROAlgorithmTracer(class_name)
    mro = _compute_mro_with_trace(class_name, bases, mro_map, tracer)
    judgment = tracer.as_judgment(mro, trust)
    return mro, judgment
