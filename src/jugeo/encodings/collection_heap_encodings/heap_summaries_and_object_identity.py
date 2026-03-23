"""Heap objects abstracted as summary nodes with identity tracked via judgment coordinates.

# copilot: collection-heap-encodings module 02 — heap summaries and object identity
# theory2.tex §27.7–§27.11: heap memory is encoded as a sheaf of location→value
# mappings.  Object identity is not a pointer equality check — it is tracked via
# judgment coordinates in the semantic site.

A **heap summary** is a compact, sound over-approximation of a concrete heap
configuration.  Rather than tracking every allocation precisely, heap summaries
group allocations into *summary nodes* — abstract representatives of one-or-many
concrete heap objects — and track inter-node relationships as sheaf morphisms.

Object identity is tracked via **judgment coordinates**: two heap objects are
considered the *same* object (in the sense of a potential alias) iff their
judgment coordinates resolve to the same stalk in the identity sheaf.

This module provides:

* :class:`HeapSummary` — the top-level heap abstraction.
* :class:`ObjectIdentityNode` — a summary node tracking one abstract object.
* :class:`AllocationRegion` — a contiguous region of the heap covered by a
  single separator.
* :class:`HeapGraphEncoding` — the bipartite sheaf encoding heap relationships
  as a directed graph of summary nodes.

Public functions
----------------
:func:`summarize_heap`
    Build a HeapSummary from a dict mapping address→value.
:func:`track_identity`
    Attach identity judgment coordinates to a set of HeapSummary nodes.
:func:`encode_allocation_region`
    Encode a contiguous allocation region as an AllocationRegion.

Theory invariants
-----------------
* Judgments are tuples ``(c, φ, A, E, O, B, T, Π)`` — NEVER booleans.
* Trust is an element of the ordered algebra — NEVER a scalar float.
* TrustTier: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED.
* Obstructions are Čech H¹ cohomology classes.
* Descent returns GlobalSection OR DescentObstruction — never raises.
* ``raise_with_scope(code, message=..., provenance=...)`` signature.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None:
            self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import TrustLevel, PropositionKind
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"

# ---------------------------------------------------------------------------
# Trust tier algebra
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust tiers — PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED."""

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: TrustTier) -> TrustTier:
        return TrustTier(min(int(self), int(other)))

    def promote(self) -> TrustTier:
        return TrustTier(min(int(self) + 1, TrustTier.PROOF_BACKED))

    def demote(self) -> TrustTier:
        return TrustTier(max(int(self) - 1, TrustTier.PROPOSAL))

    def is_at_least(self, threshold: TrustTier) -> bool:
        return int(self) >= int(threshold)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class HeapNodeKind(str, Enum):
    """The kind of a heap summary node."""

    CONCRETE = "concrete"         # exactly one concrete object
    SUMMARY = "summary"           # represents 0..∞ concrete objects (may be empty)
    SINGLETON_SUMMARY = "singleton_summary"  # exactly one concrete object (typed)
    WEAK_SUMMARY = "weak_summary"  # 0..∞ objects, may be unreachable
    ALLOCATION_SITE = "allocation_site"  # an abstract allocation site


class RegionKind(str, Enum):
    """The kind of an allocation region."""

    STACK = "stack"
    HEAP_YOUNG = "heap_young"
    HEAP_OLD = "heap_old"
    STATIC = "static"
    UNKNOWN = "unknown"


class IdentityRelation(str, Enum):
    """How two heap summary nodes relate by object identity."""

    MUST_BE_SAME = "must_be_same"     # provably the same object
    MUST_BE_DISTINCT = "must_be_distinct"  # provably distinct objects
    MAY_BE_SAME = "may_be_same"       # might alias
    DISJOINT_REGIONS = "disjoint_regions"  # in non-overlapping allocation regions
    UNKNOWN = "unknown"


class HeapSectionStatus(str, Enum):
    """Status of a heap section (node or edge)."""

    LIVE = "live"
    GARBAGE = "garbage"
    ESCAPED = "escaped"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Čech obstruction for heap descent
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapCechObstruction:
    """A Čech H¹ cohomology class blocking heap graph reconstruction."""

    coordinate: str
    cocycle_description: str
    conflicting_nodes: tuple[str, ...]
    trust_tier: TrustTier = TrustTier.PROPOSAL
    is_coboundary: bool = False
    repair_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "conflicting_nodes": list(self.conflicting_nodes),
            "trust_tier": self.trust_tier.name,
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
        }


# ---------------------------------------------------------------------------
# Judgment tuple for heap nodes — (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapJudgment:
    """A judgment about a heap summary node.  NEVER a boolean.

    Slots correspond to ``(c, φ, A, E, O, B, T, Π)`` from theory2.tex.
    """

    c: str              # coordinate
    phi: str            # proposition
    A: str              # carrier / type
    E: tuple[str, ...]  # evidence bundle
    O: tuple[str, ...]  # residual obligations
    B: tuple[HeapCechObstruction, ...]  # obstructions
    T: TrustTier        # trust annotation — algebraic, NEVER float
    Pi: Mapping[str, Any]  # provenance

    @property
    def is_settled(self) -> bool:
        return len(self.O) == 0 and len(self.B) == 0

    @property
    def is_obstructed(self) -> bool:
        return any(not ob.is_coboundary for ob in self.B)

    def with_obligation(self, ob: str) -> HeapJudgment:
        from dataclasses import replace
        return replace(self, O=(*self.O, ob))

    def with_obstruction(self, obs: HeapCechObstruction) -> HeapJudgment:
        from dataclasses import replace
        return replace(self, B=(*self.B, obs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c, "phi": self.phi, "A": self.A,
            "E": list(self.E), "O": list(self.O),
            "B": [ob.to_dict() for ob in self.B],
            "T": self.T.name, "Pi": dict(self.Pi),
        }


def _make_heap_judgment(
    coordinate: str, phi: str, carrier: str,
    evidence: Sequence[str], obligations: Sequence[str],
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Mapping[str, Any] | None = None,
) -> HeapJudgment:
    return HeapJudgment(
        c=coordinate, phi=phi, A=carrier,
        E=tuple(evidence), O=tuple(obligations), B=(),
        T=trust, Pi=dict(provenance or {}),
    )


# ---------------------------------------------------------------------------
# Object identity node
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ObjectIdentityNode:
    """A summary node in the heap graph, tracking one abstract heap object.

    Object identity is tracked via the semantic coordinate ``c``: two nodes
    with the same coordinate *may* alias; two nodes with provably disjoint
    coordinates *cannot* alias.

    Attributes
    ----------
    node_id : str
        Unique node identifier.
    kind : HeapNodeKind
        Whether this is a concrete object, summary, etc.
    coordinate : str
        Semantic coordinate in the heap site.
    python_type : str
        The Python type name of the tracked object(s).
    estimated_size_bytes : int
        Estimated memory footprint.
    allocation_site_id : str
        Identifier of the allocation site that produced this node.
    fields : Mapping[str, str]
        Map from field/attribute name to the node_id of the pointed-to node.
    status : HeapSectionStatus
        Whether this node is live, garbage, escaped, or unknown.
    judgment : HeapJudgment
        The governing judgment tuple.
    may_alias_with : tuple[str, ...]
        node_ids of nodes that may alias with this one.
    """

    node_id: str
    kind: HeapNodeKind
    coordinate: str
    python_type: str
    estimated_size_bytes: int
    allocation_site_id: str
    fields: Mapping[str, str]
    status: HeapSectionStatus
    judgment: HeapJudgment
    may_alias_with: tuple[str, ...] = ()

    def is_live(self) -> bool:
        return self.status == HeapSectionStatus.LIVE

    def points_to(self, field_name: str) -> str | None:
        return self.fields.get(field_name)

    def identity_relation_with(self, other: ObjectIdentityNode) -> IdentityRelation:
        """Compute the identity relation with *other* node."""
        if self.node_id == other.node_id:
            return IdentityRelation.MUST_BE_SAME
        if self.coordinate == other.coordinate:
            return IdentityRelation.MAY_BE_SAME
        if other.node_id in self.may_alias_with or self.node_id in other.may_alias_with:
            return IdentityRelation.MAY_BE_SAME
        if self.allocation_site_id != other.allocation_site_id:
            return IdentityRelation.MUST_BE_DISTINCT
        return IdentityRelation.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "coordinate": self.coordinate,
            "python_type": self.python_type,
            "estimated_size_bytes": self.estimated_size_bytes,
            "allocation_site_id": self.allocation_site_id,
            "fields": dict(self.fields),
            "status": self.status.value,
            "judgment": self.judgment.to_dict(),
            "may_alias_with": list(self.may_alias_with),
        }


# ---------------------------------------------------------------------------
# Allocation region
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AllocationRegion:
    """A contiguous region of the heap covered by a single separator.

    An allocation region is a sheaf-theoretic open set in the heap site.
    Nodes within the same region can interact; nodes in disjoint regions
    must be proven distinct before aliasing can be ruled out.

    Attributes
    ----------
    region_id : str
        Unique region identifier.
    kind : RegionKind
        Stack, heap young, heap old, static, or unknown.
    start_coordinate : str
        The starting semantic coordinate.
    end_coordinate : str
        The ending semantic coordinate.
    nodes : tuple[str, ...]
        node_ids of all heap nodes within this region.
    separator_description : str
        Description of the sheaf separator (e.g. "GC generation boundary").
    judgment : HeapJudgment
        The governing judgment.
    is_separating : bool
        Whether this region forms a separating conjunction with its complement.
    """

    region_id: str
    kind: RegionKind
    start_coordinate: str
    end_coordinate: str
    nodes: tuple[str, ...]
    separator_description: str
    judgment: HeapJudgment
    is_separating: bool = True

    def contains_node(self, node_id: str) -> bool:
        return node_id in self.nodes

    def is_disjoint_from(self, other: AllocationRegion) -> bool:
        """True iff this region has no nodes in common with *other*."""
        return frozenset(self.nodes).isdisjoint(frozenset(other.nodes))

    def separation_assertion(self) -> str:
        """Return the SMT-LIB-style separating conjunction assertion."""
        if len(self.nodes) < 2:
            return f"(sep-trivial {self.region_id})"
        pairs = [
            f"(distinct-locs {self.nodes[i]} {self.nodes[j]})"
            for i in range(len(self.nodes))
            for j in range(i + 1, len(self.nodes))
        ]
        return f"(sep-conjunction {self.region_id} {' '.join(pairs)})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "kind": self.kind.value,
            "start_coordinate": self.start_coordinate,
            "end_coordinate": self.end_coordinate,
            "nodes": list(self.nodes),
            "separator_description": self.separator_description,
            "judgment": self.judgment.to_dict(),
            "is_separating": self.is_separating,
        }


# ---------------------------------------------------------------------------
# Global section and descent obstruction for heap graphs
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapGlobalSection:
    """A globally consistent heap graph section reconstructed by descent."""

    coordinate: str
    node_map: Mapping[str, ObjectIdentityNode]
    judgment: HeapJudgment
    reconstruction_time_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "heap_global_section",
            "coordinate": self.coordinate,
            "num_nodes": len(self.node_map),
            "judgment": self.judgment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class HeapDescentObstruction:
    """A Čech obstruction returned when heap graph descent fails.

    NEVER raises — always returns this object on descent failure.
    """

    coordinate: str
    obstruction: HeapCechObstruction
    conflicting_node_ids: tuple[str, ...]
    diagnosis: str = ""
    repair_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "heap_descent_obstruction",
            "coordinate": self.coordinate,
            "obstruction": self.obstruction.to_dict(),
            "conflicting_node_ids": list(self.conflicting_node_ids),
            "diagnosis": self.diagnosis,
            "repair_hints": list(self.repair_hints),
        }


# ---------------------------------------------------------------------------
# Heap graph encoding
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapGraphEncoding:
    """The bipartite sheaf encoding of a heap graph.

    The heap graph is a directed graph where nodes are ObjectIdentityNodes
    and edges represent field/pointer relationships.  The graph is encoded
    as a sheaf over the coordinate space of all node coordinates.

    Attributes
    ----------
    graph_id : str
        Unique identifier.
    nodes : tuple[ObjectIdentityNode, ...]
        All tracked heap objects.
    edges : tuple[tuple[str, str, str], ...]
        Directed edges as (from_node_id, field_name, to_node_id).
    regions : tuple[AllocationRegion, ...]
        Allocation regions covering the nodes.
    root_coordinates : tuple[str, ...]
        Entry-point coordinates (e.g. program variables).
    judgment : HeapJudgment
        Top-level heap graph judgment.
    encoding_metadata : Mapping[str, Any]
        Encoder metadata.
    """

    graph_id: str
    nodes: tuple[ObjectIdentityNode, ...]
    edges: tuple[tuple[str, str, str], ...]
    regions: tuple[AllocationRegion, ...]
    root_coordinates: tuple[str, ...]
    judgment: HeapJudgment
    encoding_metadata: Mapping[str, Any] = field(default_factory=dict)

    def get_node(self, node_id: str) -> ObjectIdentityNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def reachable_from(self, root_id: str) -> frozenset[str]:
        """BFS reachability from *root_id*."""
        visited: set[str] = set()
        queue = [root_id]
        adj: dict[str, list[str]] = {}
        for frm, _fld, to in self.edges:
            adj.setdefault(frm, []).append(to)
        while queue:
            nid = queue.pop()
            if nid in visited:
                continue
            visited.add(nid)
            queue.extend(adj.get(nid, []))
        return frozenset(visited)

    def find_may_alias_pairs(self) -> list[tuple[str, str]]:
        """Return pairs of nodes that may alias."""
        result: list[tuple[str, str]] = []
        node_list = list(self.nodes)
        for i, n1 in enumerate(node_list):
            for n2 in node_list[i + 1:]:
                rel = n1.identity_relation_with(n2)
                if rel == IdentityRelation.MAY_BE_SAME:
                    result.append((n1.node_id, n2.node_id))
        return result

    def attempt_descent(self) -> HeapGlobalSection | HeapDescentObstruction:
        """Attempt to glue heap node sections into a consistent global section.

        Returns HeapGlobalSection on success, HeapDescentObstruction on failure.
        Descent NEVER raises.
        """
        t0 = time.monotonic_ns()
        # Check for conflicting field assignments: same coordinate pointing to
        # different nodes via the same field
        coord_fields: dict[tuple[str, str], set[str]] = {}
        for frm, fld, to in self.edges:
            frm_node = self.get_node(frm)
            if frm_node is None:
                continue
            key = (frm_node.coordinate, fld)
            coord_fields.setdefault(key, set()).add(to)
        conflicts = [
            (coord, fld, targets)
            for (coord, fld), targets in coord_fields.items()
            if len(targets) > 1
        ]
        if conflicts:
            coord, fld, targets = conflicts[0]
            obs = HeapCechObstruction(
                coordinate=f"heap:{coord}",
                cocycle_description=(
                    f"Conflicting field assignment: {coord}.{fld} → {sorted(targets)}"
                ),
                conflicting_nodes=tuple(sorted(targets)),
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion="Disambiguate field assignments via aliasing obligations.",
            )
            return HeapDescentObstruction(
                coordinate=f"heap:{coord}",
                obstruction=obs,
                conflicting_node_ids=tuple(sorted(targets)),
                diagnosis=f"{len(conflicts)} conflicting field assignments detected.",
                repair_hints=("discharge-aliasing-obligations",),
            )
        node_map: dict[str, ObjectIdentityNode] = {n.node_id: n for n in self.nodes}
        jmt = _make_heap_judgment(
            coordinate="heap:global",
            phi="heap_graph_globally_consistent",
            carrier="heap_graph_sheaf",
            evidence=[f"node:{n.node_id}" for n in self.nodes[:10]],
            obligations=(),
            trust=TrustTier.VERIFIED,
            provenance={"graph_id": self.graph_id, "descent_at": _now_iso()},
        )
        return HeapGlobalSection(
            coordinate="heap:global",
            node_map=node_map,
            judgment=jmt,
            reconstruction_time_ns=time.monotonic_ns() - t0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "num_nodes": len(self.nodes),
            "num_edges": len(self.edges),
            "num_regions": len(self.regions),
            "root_coordinates": list(self.root_coordinates),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Top-level HeapSummary
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapSummary:
    """Compact, sound over-approximation of a concrete heap configuration.

    Attributes
    ----------
    summary_id : str
        Unique identifier.
    graph : HeapGraphEncoding
        The bipartite sheaf graph.
    live_nodes : tuple[str, ...]
        node_ids confirmed live.
    escaped_nodes : tuple[str, ...]
        node_ids that have escaped the current scope.
    total_estimated_bytes : int
        Total estimated memory footprint.
    created_at : str
        ISO-8601 creation timestamp.
    summary_judgment : HeapJudgment
        Governing judgment for the whole summary.
    """

    summary_id: str
    graph: HeapGraphEncoding
    live_nodes: tuple[str, ...]
    escaped_nodes: tuple[str, ...]
    total_estimated_bytes: int
    created_at: str
    summary_judgment: HeapJudgment

    def is_sound(self) -> bool:
        """True iff the summary is at least VERIFIED trust level."""
        return self.summary_judgment.T.is_at_least(TrustTier.VERIFIED)

    def get_live_node_objects(self) -> tuple[ObjectIdentityNode, ...]:
        live_set = frozenset(self.live_nodes)
        return tuple(n for n in self.graph.nodes if n.node_id in live_set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "num_live_nodes": len(self.live_nodes),
            "num_escaped_nodes": len(self.escaped_nodes),
            "total_estimated_bytes": self.total_estimated_bytes,
            "created_at": self.created_at,
            "is_sound": self.is_sound(),
            "graph": self.graph.to_dict(),
            "summary_judgment": self.summary_judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def summarize_heap(
    address_map: dict[int, Any],
    *,
    coordinate: str = "heap_root",
    region_kind: RegionKind = RegionKind.HEAP_YOUNG,
) -> HeapSummary:
    """Build a HeapSummary from a dict mapping address→value.

    Parameters
    ----------
    address_map : dict[int, Any]
        Map from integer addresses to Python values.
    coordinate : str
        Semantic coordinate.
    region_kind : RegionKind
        The kind of allocation region for all nodes.

    Returns
    -------
    HeapSummary
    """
    logger.debug(
        "summarize_heap: %d objects at %s", len(address_map), coordinate
    )
    nodes: list[ObjectIdentityNode] = []
    edges: list[tuple[str, str, str]] = []
    site_id = _stable_id("alloc", coordinate)

    for addr, val in address_map.items():
        node_id = _stable_id(f"node:{coordinate}", str(addr))
        coord = f"{coordinate}[{addr:#x}]"
        jmt = _make_heap_judgment(
            coordinate=coord,
            phi="heap_node_live",
            carrier="heap_object",
            evidence=[f"address:{addr:#x}", f"type:{type(val).__name__}"],
            obligations=["verify_reachability"],
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance={"address": addr, "python_type": type(val).__name__},
        )
        size_estimate = 0
        try:
            import sys as _sys
            size_estimate = _sys.getsizeof(val)
        except Exception:
            size_estimate = 64  # fallback

        # Build field map for dicts and objects
        field_map: dict[str, str] = {}
        if isinstance(val, dict):
            for k, v in list(val.items())[:8]:
                child_id_int = id(v)
                if child_id_int in address_map:
                    child_node_id = _stable_id(f"node:{coordinate}", str(child_id_int))
                    field_map[repr(k)] = child_node_id
                    edges.append((node_id, repr(k), child_node_id))
        elif hasattr(val, "__dict__"):
            for attr, v in list(vars(val).items())[:8]:
                child_id_int = id(v)
                if child_id_int in address_map:
                    child_node_id = _stable_id(f"node:{coordinate}", str(child_id_int))
                    field_map[attr] = child_node_id
                    edges.append((node_id, attr, child_node_id))

        node = ObjectIdentityNode(
            node_id=node_id,
            kind=HeapNodeKind.CONCRETE,
            coordinate=coord,
            python_type=type(val).__name__,
            estimated_size_bytes=size_estimate,
            allocation_site_id=site_id,
            fields=field_map,
            status=HeapSectionStatus.LIVE,
            judgment=jmt,
        )
        nodes.append(node)

    all_node_ids = tuple(n.node_id for n in nodes)
    region_jmt = _make_heap_judgment(
        coordinate=coordinate,
        phi="allocation_region_sound",
        carrier="allocation_region",
        evidence=[f"region_kind:{region_kind.value}"],
        obligations=[],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"coordinate": coordinate, "region_kind": region_kind.value},
    )
    region = AllocationRegion(
        region_id=_stable_id("region", coordinate),
        kind=region_kind,
        start_coordinate=coordinate,
        end_coordinate=coordinate,
        nodes=all_node_ids,
        separator_description=f"Python heap summary at {coordinate}",
        judgment=region_jmt,
        is_separating=True,
    )
    graph_jmt = _make_heap_judgment(
        coordinate=coordinate,
        phi="heap_graph_sound",
        carrier="heap_graph",
        evidence=[f"heap_size:{len(nodes)}"],
        obligations=["verify_reachability_from_roots"],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"coordinate": coordinate, "timestamp": _now_iso()},
    )
    graph = HeapGraphEncoding(
        graph_id=_stable_id("graph", coordinate),
        nodes=tuple(nodes),
        edges=tuple(edges),
        regions=(region,),
        root_coordinates=(coordinate,),
        judgment=graph_jmt,
    )
    summary_jmt = _make_heap_judgment(
        coordinate=coordinate,
        phi="heap_summary_sound",
        carrier="heap_summary",
        evidence=[f"total_objects:{len(nodes)}"],
        obligations=["verify_may_alias_pairs"],
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance={"coordinate": coordinate, "created_at": _now_iso()},
    )
    return HeapSummary(
        summary_id=str(uuid.uuid4()),
        graph=graph,
        live_nodes=all_node_ids,
        escaped_nodes=(),
        total_estimated_bytes=sum(n.estimated_size_bytes for n in nodes),
        created_at=_now_iso(),
        summary_judgment=summary_jmt,
    )


def track_identity(
    summary: HeapSummary,
    *,
    identity_coordinate_map: Mapping[str, str] | None = None,
) -> HeapSummary:
    """Attach identity judgment coordinates to HeapSummary nodes.

    Nodes that share an identity coordinate are potential aliases.

    Parameters
    ----------
    summary : HeapSummary
        The heap summary to annotate.
    identity_coordinate_map : Mapping[str, str] or None
        Map from node_id to identity coordinate.  If None, identity
        coordinates are assigned based on allocation site.

    Returns
    -------
    HeapSummary
        A new HeapSummary with may_alias_with annotations updated.
    """
    from dataclasses import replace

    if identity_coordinate_map is None:
        coord_to_nodes: dict[str, list[str]] = {}
        for node in summary.graph.nodes:
            coord_to_nodes.setdefault(node.coordinate, []).append(node.node_id)
    else:
        coord_to_nodes_by_id: dict[str, list[str]] = {}
        for nid, coord in identity_coordinate_map.items():
            coord_to_nodes_by_id.setdefault(coord, []).append(nid)
        coord_to_nodes = coord_to_nodes_by_id

    updated_nodes: list[ObjectIdentityNode] = []
    for node in summary.graph.nodes:
        if identity_coordinate_map is not None:
            coord = identity_coordinate_map.get(node.node_id, node.coordinate)
        else:
            coord = node.coordinate
        siblings = [
            nid for nid in coord_to_nodes.get(coord, [])
            if nid != node.node_id
        ]
        updated_nodes.append(
            replace(node, may_alias_with=tuple(siblings))
        )

    updated_graph = replace(summary.graph, nodes=tuple(updated_nodes))
    return replace(summary, graph=updated_graph)


def encode_allocation_region(
    node_ids: Sequence[str],
    *,
    coordinate: str = "region_root",
    kind: RegionKind = RegionKind.HEAP_YOUNG,
    separator_description: str = "",
) -> AllocationRegion:
    """Encode a contiguous allocation region as an AllocationRegion.

    Parameters
    ----------
    node_ids : Sequence[str]
        Identifiers of heap nodes within this region.
    coordinate : str
        Semantic coordinate for the region.
    kind : RegionKind
        The kind of this allocation region.
    separator_description : str
        Human-readable description of the sheaf separator.

    Returns
    -------
    AllocationRegion
    """
    jmt = _make_heap_judgment(
        coordinate=coordinate,
        phi="allocation_region_well_formed",
        carrier="allocation_region",
        evidence=[f"region_size:{len(node_ids)}"],
        obligations=[] if len(node_ids) > 0 else ["region_emptiness_justified"],
        trust=TrustTier.REVIEWED,
        provenance={"coordinate": coordinate, "kind": kind.value},
    )
    return AllocationRegion(
        region_id=_stable_id("region", coordinate + kind.value),
        kind=kind,
        start_coordinate=coordinate,
        end_coordinate=coordinate,
        nodes=tuple(node_ids),
        separator_description=separator_description or f"{kind.value} region at {coordinate}",
        judgment=jmt,
        is_separating=True,
    )


# ---------------------------------------------------------------------------
# Heap summary statistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeapSummaryStats:
    """Aggregate statistics for a collection of heap summaries."""

    total_summaries: int
    total_nodes: int
    total_edges: int
    total_bytes: int
    sound_summaries: int
    descent_successes: int
    descent_failures: int
    may_alias_pairs: int

    @classmethod
    def from_summaries(cls, summaries: Sequence[HeapSummary]) -> HeapSummaryStats:
        total_nodes = sum(len(s.graph.nodes) for s in summaries)
        total_edges = sum(len(s.graph.edges) for s in summaries)
        total_bytes = sum(s.total_estimated_bytes for s in summaries)
        sound = sum(1 for s in summaries if s.is_sound())
        successes = 0
        failures = 0
        alias_pairs = 0
        for s in summaries:
            dr = s.graph.attempt_descent()
            if isinstance(dr, HeapGlobalSection):
                successes += 1
            else:
                failures += 1
            alias_pairs += len(s.graph.find_may_alias_pairs())
        return cls(
            total_summaries=len(summaries),
            total_nodes=total_nodes,
            total_edges=total_edges,
            total_bytes=total_bytes,
            sound_summaries=sound,
            descent_successes=successes,
            descent_failures=failures,
            may_alias_pairs=alias_pairs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_summaries": self.total_summaries,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "total_bytes": self.total_bytes,
            "sound_summaries": self.sound_summaries,
            "descent_successes": self.descent_successes,
            "descent_failures": self.descent_failures,
            "may_alias_pairs": self.may_alias_pairs,
        }


__all__ = [
    "AllocationRegion",
    "HeapCechObstruction",
    "HeapDescentObstruction",
    "HeapGlobalSection",
    "HeapGraphEncoding",
    "HeapJudgment",
    "HeapNodeKind",
    "HeapSectionStatus",
    "HeapSummary",
    "HeapSummaryStats",
    "IdentityRelation",
    "ObjectIdentityNode",
    "RegionKind",
    "TrustTier",
    "encode_allocation_region",
    "summarize_heap",
    "track_identity",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== heap_summaries_and_object_identity — smoke test ===")

    class Obj:
        def __init__(self, x: int, y: str) -> None:
            self.x = x
            self.y = y

    o1 = Obj(1, "hello")
    o2 = Obj(2, "world")
    o3 = {"key": "value", "ref": o1}

    addr_map: dict[int, Any] = {
        id(o1): o1,
        id(o2): o2,
        id(o3): o3,
    }

    summary = summarize_heap(addr_map, coordinate="test_heap")
    print(f"HeapSummary: id={summary.summary_id[:12]}… "
          f"live_nodes={len(summary.live_nodes)} "
          f"bytes={summary.total_estimated_bytes}")
    assert len(summary.live_nodes) == 3, "Expected 3 live nodes"

    # Track identity
    annotated = track_identity(summary)
    print(f"After track_identity: nodes={len(annotated.graph.nodes)}")

    # Attempt descent
    result = summary.graph.attempt_descent()
    print(f"Descent: {'GlobalSection' if isinstance(result, HeapGlobalSection) else 'DescentObstruction'}")
    assert isinstance(result, HeapGlobalSection), "Simple heap descent should succeed"

    # Encode allocation region
    node_ids = list(summary.live_nodes)
    region = encode_allocation_region(
        node_ids, coordinate="test_region", kind=RegionKind.HEAP_YOUNG
    )
    print(f"AllocationRegion: id={region.region_id[:12]}… nodes={len(region.nodes)}")
    assert region.is_separating

    # Separation assertion
    sep = region.separation_assertion()
    print(f"Separation assertion: {sep[:60]}…")

    # Trust algebra
    t = TrustTier.REVIEWED
    assert t.promote() == TrustTier.VERIFIED
    assert t.demote() == TrustTier.PROPOSAL
    assert t.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    print("TrustTier algebra: OK")

    # Stats
    stats = HeapSummaryStats.from_summaries([summary])
    print(f"Stats: {stats.to_dict()}")
    assert stats.total_nodes == 3

    # Serialization
    d = summary.to_dict()
    j = json.dumps(d, default=str)
    assert "summary_id" in j
    print("JSON serialization: OK")

    print("All assertions passed.")
    sys.exit(0)
