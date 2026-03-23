"""Provenance tracking system for JuGeo shared core.

Provenance tracks WHERE a judgment or piece of evidence came from — which
channel produced it, what inputs were consumed, what transformations were
applied, and what the trust path is.  Provenance is essential for:

  1. Auditing trust chains end-to-end.
  2. Invalidating downstream judgments when upstream evidence is revoked.
  3. Preventing circular reasoning across evidence channels.
  4. Explaining to users (and copilot) why something is believed.

The module provides: ProvenanceNode, ProvenanceGraph, ProvenancePath,
ProvenanceQuery, ProvenanceValidator, ProvenanceInvalidator,
ProvenanceExplainer, ProvenanceSerializer, ProvenanceMerger,
ProvenanceStatistics, ProvenanceArchive, and CircularReasoningDetector.

copilot: This module is a core auditing surface for LLM orchestration.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Legacy compatibility — keep the original lightweight trace types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProvenanceStep:
    """A single step in a lightweight provenance trace.

    Retained for backward compatibility with manifests and certificates
    that reference the original append-only trace API.
    """

    actor: str
    action: str
    coordinate: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-ready dictionary."""
        return {
            'actor': self.actor,
            'action': self.action,
            'coordinate': self.coordinate,
            'details': dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ProvenanceTrace:
    """An append-only sequence of :class:`ProvenanceStep` entries.

    This is the lightweight provenance representation used by evidence
    manifests and settlement certificates.
    """

    origin: str
    steps: tuple[ProvenanceStep, ...] = ()

    def append(self, step: ProvenanceStep) -> ProvenanceTrace:
        """Return a new trace with *step* appended (immutable)."""
        return ProvenanceTrace(self.origin, self.steps + (step,))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-ready dictionary."""
        return {
            'origin': self.origin,
            'steps': [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProvenanceTrace:
        """Reconstruct a trace from a dictionary."""
        return cls(
            str(payload['origin']),
            tuple(
                ProvenanceStep(
                    str(s['actor']),
                    str(s['action']),
                    str(s['coordinate']),
                    dict(s.get('details', {})),
                )
                for s in payload.get('steps', [])
            ),
        )


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ProvenanceOperation(str, Enum):
    """The kind of transformation that produced a provenance node.

    Each value corresponds to a category in the theory:
    * PRODUCED — original evidence creation by a channel.
    * COMPOSED — merging multiple pieces of evidence.
    * TRANSPORTED — moving evidence across coordinates.
    * RESTRICTED — narrowing the scope of a judgment.
    * PROMOTED — raising the trust tier (requires justification).
    * DEMOTED — lowering the trust tier (always safe).
    """

    PRODUCED = 'produced'
    COMPOSED = 'composed'
    TRANSPORTED = 'transported'
    RESTRICTED = 'restricted'
    PROMOTED = 'promoted'
    DEMOTED = 'demoted'


class InvalidationReason(str, Enum):
    """Why a provenance node was invalidated."""

    REVOKED = 'revoked'
    UPSTREAM_INVALID = 'upstream_invalid'
    CIRCULAR = 'circular'
    TRUST_VIOLATION = 'trust_violation'
    CHANNEL_VIOLATION = 'channel_violation'


class CycleKind(str, Enum):
    """Classification of a detected cycle."""

    SELF_LOOP = 'self_loop'
    MUTUAL = 'mutual'
    TRANSITIVE = 'transitive'
    CROSS_CHANNEL = 'cross_channel'


# ---------------------------------------------------------------------------
# 1. ProvenanceNode
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProvenanceNode:
    """Immutable record representing a single point in a provenance DAG.

    Every judgment or evidence artefact that flows through JuGeo has a
    corresponding ``ProvenanceNode`` that records *how* it came to exist,
    *who* produced it, and *what* trust was in place at creation time.

    Attributes:
        node_id: Globally unique identifier for this node.
        source_channel: Name of the evidence channel that produced this node
            (e.g. ``'z3_solver'``, ``'copilot_review'``, ``'proof_checker'``).
        operation: The transformation class — see :class:`ProvenanceOperation`.
        inputs: Tuple of ``node_id`` values for the upstream nodes consumed.
        output_judgment_id: Identifier of the judgment or evidence created.
        timestamp: Unix epoch seconds when the node was created.
        coordinate: Semantic coordinate string (e.g. ``'module/auth/login'``).
        trust_at_creation: String label for the trust tier at creation time
            (e.g. ``'proposal'``, ``'reviewed'``, ``'verified'``).
        metadata: Arbitrary key-value metadata for channel-specific details.
    """

    node_id: str
    source_channel: str
    operation: ProvenanceOperation
    inputs: tuple[str, ...] = ()
    output_judgment_id: str = ''
    timestamp: float = 0.0
    coordinate: str = ''
    trust_at_creation: str = 'proposal'
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # -- helpers --------------------------------------------------------------

    def is_root(self) -> bool:
        """Return ``True`` if this node has no upstream inputs."""
        return len(self.inputs) == 0

    def is_copilot_node(self) -> bool:
        """Return ``True`` if the source channel is a copilot channel.

        copilot: This method supports LLM-aware provenance filtering.
        """
        return 'copilot' in self.source_channel.lower()

    def is_solver_node(self) -> bool:
        """Return ``True`` if the source channel is a solver channel."""
        return 'solver' in self.source_channel.lower()

    def is_promotion(self) -> bool:
        """Return ``True`` if this node represents a trust promotion."""
        return self.operation is ProvenanceOperation.PROMOTED

    def age_seconds(self, now: float | None = None) -> float:
        """Seconds elapsed since this node was created."""
        return (now or time.time()) - self.timestamp

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-ready dictionary."""
        return {
            'node_id': self.node_id,
            'source_channel': self.source_channel,
            'operation': self.operation.value,
            'inputs': list(self.inputs),
            'output_judgment_id': self.output_judgment_id,
            'timestamp': self.timestamp,
            'coordinate': self.coordinate,
            'trust_at_creation': self.trust_at_creation,
            'metadata': dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProvenanceNode:
        """Reconstruct a node from a dictionary."""
        return cls(
            node_id=str(data['node_id']),
            source_channel=str(data['source_channel']),
            operation=ProvenanceOperation(data['operation']),
            inputs=tuple(str(i) for i in data.get('inputs', ())),
            output_judgment_id=str(data.get('output_judgment_id', '')),
            timestamp=float(data.get('timestamp', 0.0)),
            coordinate=str(data.get('coordinate', '')),
            trust_at_creation=str(data.get('trust_at_creation', 'proposal')),
            metadata=dict(data.get('metadata', {})),
        )

    # -- cross-subsystem enrichment -----------------------------------------

    @property
    def site_projection(self) -> dict[str, Any]:
        """Project this node onto site coordinates.

        Maps the node's semantic coordinate string into the coordinate
        system of ``jugeo.geometry.site``, returning the site object(s)
        and any covering information associated with this node.
        """
        try:
            from jugeo.geometry.site import project_coordinate
        except ImportError:
            return {'coordinate': self.coordinate, 'site_objects': None}
        return project_coordinate(self.coordinate)

    def encoding_trace(self) -> dict[str, Any]:
        """Trace encoding steps that produced this node.

        Queries ``jugeo.encodings`` for the sequence of encoding
        transformations applied to the evidence that culminated in
        this provenance node.
        """
        try:
            from jugeo.encodings import trace_encoding_for
        except ImportError:
            return {'node_id': self.node_id, 'trace': None, 'reason': 'encodings unavailable'}
        return trace_encoding_for(self.node_id)

    def solver_trace(self) -> dict[str, Any]:
        """Trace solver queries that produced this node.

        Queries ``jugeo.solver`` for the Z3 query/response pairs
        generated during the evidence production recorded by this node.
        """
        try:
            from jugeo.solver import trace_solver_for
        except ImportError:
            return {'node_id': self.node_id, 'trace': None, 'reason': 'solver unavailable'}
        return trace_solver_for(self.node_id)

    def judgment_trace(self) -> dict[str, Any]:
        """Trace judgment construction for this node.

        Queries ``jugeo.judgments`` for the sequence of judgment-term
        construction steps that produced the judgment identified by
        ``output_judgment_id``.
        """
        try:
            from jugeo.judgments import trace_judgment_for
        except ImportError:
            return {
                'node_id': self.node_id,
                'judgment_id': self.output_judgment_id,
                'trace': None,
                'reason': 'judgments unavailable',
            }
        return trace_judgment_for(self.output_judgment_id)

    @property
    def orchestration_decisions(self) -> list[dict[str, Any]]:
        """Return orchestration decisions associated with this node.

        Queries ``jugeo.orchestration.controller`` for the routing,
        scheduling, and retry decisions that led to the creation of
        this provenance node.
        """
        try:
            from jugeo.orchestration.controller import decisions_for_node
        except ImportError:
            return []
        return decisions_for_node(self.node_id)

    def runtime_replay_trace(self) -> dict[str, Any]:
        """Return a replay trace for this node from ``jugeo.runtime.replay``.

        The replay trace captures the runtime execution state at the
        moment this node was created, enabling deterministic replay
        of the evidence production.
        """
        try:
            from jugeo.runtime.replay import replay_trace_for
        except ImportError:
            return {'node_id': self.node_id, 'replay': None, 'reason': 'replay unavailable'}
        return replay_trace_for(self.node_id)


def _make_node_id() -> str:
    """Generate a unique provenance node identifier."""
    return f'prov-{uuid.uuid4().hex[:12]}'


# ---------------------------------------------------------------------------
# 2. ProvenanceGraph
# ---------------------------------------------------------------------------

class ProvenanceGraph:
    """Directed acyclic graph of :class:`ProvenanceNode` entries.

    The graph maintains forward (parent→child) and backward (child→parent)
    adjacency maps for efficient traversal in either direction.

    copilot: The graph is the primary data structure queried during audit
    and explanation generation.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, ProvenanceNode] = {}
        self._children: dict[str, list[str]] = defaultdict(list)
        self._parents: dict[str, list[str]] = defaultdict(list)

    # -- mutators -------------------------------------------------------------

    def add_node(self, node: ProvenanceNode) -> None:
        """Insert a node into the graph and wire parent/child edges.

        Raises ``ValueError`` if a node with the same ``node_id`` already
        exists. Missing inputs are allowed so cycles and forward references
        can be represented and analysed explicitly.
        """
        if node.node_id in self._nodes:
            raise ValueError(f'Duplicate node_id: {node.node_id}')
        self._nodes[node.node_id] = node
        for inp in node.inputs:
            self._children[inp].append(node.node_id)
            self._parents[node.node_id].append(inp)

    def remove_node(self, node_id: str) -> ProvenanceNode:
        """Remove a node and all its edges.  Returns the removed node.

        Raises ``KeyError`` if the node does not exist.
        """
        node = self._nodes.pop(node_id)
        for inp in node.inputs:
            children = self._children.get(inp, [])
            if node_id in children:
                children.remove(node_id)
        if node_id in self._parents:
            del self._parents[node_id]
        if node_id in self._children:
            for child_id in list(self._children[node_id]):
                parent_list = self._parents.get(child_id, [])
                if node_id in parent_list:
                    parent_list.remove(node_id)
            del self._children[node_id]
        return node

    # -- accessors ------------------------------------------------------------

    def get_node(self, node_id: str) -> ProvenanceNode:
        """Return the node with the given id or raise ``KeyError``."""
        return self._nodes[node_id]

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def all_node_ids(self) -> frozenset[str]:
        """Return all node identifiers in the graph."""
        return frozenset(self._nodes)

    def parents_of(self, node_id: str) -> tuple[str, ...]:
        """Return the immediate parent (input) node ids."""
        return tuple(self._parents.get(node_id, ()))

    def children_of(self, node_id: str) -> tuple[str, ...]:
        """Return the immediate child (downstream) node ids."""
        return tuple(self._children.get(node_id, ()))

    def ancestors_of(self, node_id: str) -> frozenset[str]:
        """Return all transitive ancestors of *node_id* (BFS up)."""
        visited: set[str] = set()
        queue: deque[str] = deque(self._parents.get(node_id, []))
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self._parents.get(current, []))
        return frozenset(visited)

    def descendants_of(self, node_id: str) -> frozenset[str]:
        """Return all transitive descendants of *node_id* (BFS down)."""
        visited: set[str] = set()
        queue: deque[str] = deque(self._children.get(node_id, []))
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self._children.get(current, []))
        return frozenset(visited)

    def find_roots(self) -> tuple[str, ...]:
        """Return node ids that have no parents (original evidence)."""
        return tuple(
            nid for nid, node in self._nodes.items() if len(node.inputs) == 0
        )

    def find_leaves(self) -> tuple[str, ...]:
        """Return node ids that have no children (terminal judgments)."""
        return tuple(
            nid for nid in self._nodes
            if len(self._children.get(nid, [])) == 0
        )

    # -- structural analysis --------------------------------------------------

    def is_acyclic(self) -> bool:
        """Return ``True`` if the graph contains no directed cycles."""
        return len(self.detect_cycles()) == 0

    def detect_cycles(self) -> list[list[str]]:
        """Return all directed cycles as lists of node ids.

        Uses a DFS-based algorithm that records the recursion stack to
        identify back-edges.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._nodes}
        parent_map: dict[str, str | None] = {}
        cycles: list[list[str]] = []

        def _dfs(u: str) -> None:
            color[u] = GRAY
            for v in self._children.get(u, []):
                if color.get(v, BLACK) == GRAY:
                    # back-edge → extract cycle
                    cycle = [v, u]
                    cur = u
                    while cur != v and cur in parent_map and parent_map[cur] is not None:
                        cur = parent_map[cur]  # type: ignore[assignment]
                        if cur == v:
                            break
                        cycle.append(cur)
                    cycle.reverse()
                    cycles.append(cycle)
                elif color.get(v, BLACK) == WHITE:
                    parent_map[v] = u
                    _dfs(v)
            color[u] = BLACK

        for node_id in self._nodes:
            if color[node_id] == WHITE:
                parent_map[node_id] = None
                _dfs(node_id)
        return cycles

    def topological_sort(self) -> list[str]:
        """Return nodes in topological order (parents before children).

        Raises ``ValueError`` if the graph contains a cycle.
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for nid in self._nodes:
            for child in self._children.get(nid, []):
                in_degree[child] = in_degree.get(child, 0) + 1

        queue: deque[str] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        order: list[str] = []
        while queue:
            u = queue.popleft()
            order.append(u)
            for v in self._children.get(u, []):
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        if len(order) != len(self._nodes):
            raise ValueError('Graph contains a cycle; topological sort impossible')
        return order

    def subgraph_for(self, node_ids: Iterable[str]) -> ProvenanceGraph:
        """Return a new graph containing only the specified nodes.

        Edges are preserved only where both endpoints are in *node_ids*.
        """
        keep = frozenset(node_ids)
        sub = ProvenanceGraph()
        for nid in self.topological_sort():
            if nid not in keep:
                continue
            node = self._nodes[nid]
            restricted_inputs = tuple(i for i in node.inputs if i in keep)
            restricted = replace(node, inputs=restricted_inputs)
            # bypass validation for restricted inputs already present
            sub._nodes[restricted.node_id] = restricted
            for inp in restricted_inputs:
                sub._children[inp].append(restricted.node_id)
                sub._parents[restricted.node_id].append(inp)
        return sub

    def to_dict(self) -> dict[str, Any]:
        """Serialize the entire graph to a JSON-ready dictionary."""
        return {
            'nodes': [n.to_dict() for n in self._nodes.values()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProvenanceGraph:
        """Reconstruct a graph from a dictionary."""
        graph = cls()
        nodes = [ProvenanceNode.from_dict(nd) for nd in data.get('nodes', [])]
        id_set: set[str] = set()
        # insert in order, allowing forward references within the batch
        for node in nodes:
            id_set.add(node.node_id)
        for node in nodes:
            graph._nodes[node.node_id] = node
            for inp in node.inputs:
                if inp in id_set:
                    graph._children[inp].append(node.node_id)
                    graph._parents[node.node_id].append(inp)
        return graph

    # -- cross-subsystem integration ----------------------------------------

    def orchestration_trace(
        self,
        orchestrator: Any,
        *,
        include_regret: bool = True,
    ) -> dict[str, Any]:
        """Link provenance to orchestration decisions.

        Given an :class:`jugeo.orchestration.controller.Orchestrator`, this
        method correlates the provenance graph's root nodes with the
        orchestrator's move history to produce a trace showing which
        orchestration moves produced which evidence lineages.

        Parameters
        ----------
        orchestrator:
            An :class:`jugeo.orchestration.controller.Orchestrator` instance.
        include_regret:
            If ``True``, include regret analysis from the move history.

        Returns
        -------
        dict
            ``{'trace_entries': list[dict], 'move_count': int,
              'regret_analysis': list[dict] | None,
              'coverage': float}``
        """
        try:
            from jugeo.orchestration.controller import (  # noqa: F811
                Orchestrator,
                MoveHistory,
                MoveRecord,
            )
        except ImportError:
            return {
                'trace_entries': [],
                'move_count': 0,
                'regret_analysis': None,
                'coverage': 0.0,
                'error': 'jugeo.orchestration.controller not available',
            }

        history = getattr(orchestrator, '_history', None)
        if history is None:
            return {
                'trace_entries': [],
                'move_count': 0,
                'regret_analysis': None,
                'coverage': 0.0,
            }

        root_ids = set(self.find_roots())
        trace_entries: list[dict[str, Any]] = []
        matched_roots: set[str] = set()

        records = getattr(history, '_records', [])
        for record in records:
            move = getattr(record, 'move', None)
            if move is None:
                continue
            target = getattr(move, 'target_coordinate', '')
            move_id = getattr(move, 'move_id', '')
            kind = getattr(move, 'kind', None)
            kind_val = kind.value if kind is not None else 'unknown'

            linked_roots: list[str] = []
            for rid in root_ids:
                node = self._nodes.get(rid)
                if node is None:
                    continue
                node_coord = getattr(node, 'coordinate', '')
                if node_coord and target and node_coord == target:
                    linked_roots.append(rid)
                    matched_roots.add(rid)

            trace_entries.append({
                'move_id': move_id,
                'move_kind': kind_val,
                'target_coordinate': target,
                'success': getattr(record, 'success', False),
                'actual_gain': getattr(record, 'actual_gain', 0.0),
                'linked_provenance_roots': linked_roots,
                'descendant_count': sum(
                    len(self.descendants_of(r)) for r in linked_roots
                ),
            })

        regret = None
        if include_regret and hasattr(history, 'regret_analysis'):
            regret = history.regret_analysis()

        coverage = (
            len(matched_roots) / len(root_ids) if root_ids else 0.0
        )

        return {
            'trace_entries': trace_entries,
            'move_count': len(records),
            'regret_analysis': regret,
            'coverage': coverage,
        }

    def runtime_replay_provenance(
        self,
        replay_engine: Any,
        *,
        coordinate_filter: str = '',
    ) -> dict[str, Any]:
        """Trace provenance through replayed runtime results.

        Given a :class:`jugeo.runtime.replay.ReplayEngine`, this method
        correlates the provenance graph with the replay ledger to identify
        which provenance nodes were produced by replayed (cached) work
        versus fresh computation.

        Parameters
        ----------
        replay_engine:
            A :class:`jugeo.runtime.replay.ReplayEngine` instance.
        coordinate_filter:
            If non-empty, restrict to nodes at this coordinate.

        Returns
        -------
        dict
            ``{'replay_linked_nodes': list[str],
              'fresh_nodes': list[str],
              'replay_ratio': float,
              'replay_records_examined': int}``
        """
        try:
            from jugeo.runtime.replay import (  # noqa: F811
                ReplayEngine,
                ReplayRecord,
                ReplayLedger,
            )
        except ImportError:
            return {
                'replay_linked_nodes': [],
                'fresh_nodes': [],
                'replay_ratio': 0.0,
                'replay_records_examined': 0,
                'error': 'jugeo.runtime.replay not available',
            }

        ledger = getattr(replay_engine, 'ledger', None)
        if ledger is None:
            return {
                'replay_linked_nodes': [],
                'fresh_nodes': list(self.all_node_ids()),
                'replay_ratio': 0.0,
                'replay_records_examined': 0,
            }

        ledger_records = getattr(ledger, '_records', [])
        replay_keys: set[str] = set()
        for rec in ledger_records:
            replay_keys.add(getattr(rec, 'stable_key', ''))
            replay_keys.add(getattr(rec, 'name', ''))

        replay_linked: list[str] = []
        fresh: list[str] = []
        for nid, node in self._nodes.items():
            if coordinate_filter:
                node_coord = getattr(node, 'coordinate', '')
                if node_coord != coordinate_filter:
                    continue
            channel = getattr(node, 'source_channel', '')
            node_coord = getattr(node, 'coordinate', '')
            if nid in replay_keys or node_coord in replay_keys or channel in replay_keys:
                replay_linked.append(nid)
            else:
                fresh.append(nid)

        total = len(replay_linked) + len(fresh)
        ratio = len(replay_linked) / total if total > 0 else 0.0

        return {
            'replay_linked_nodes': replay_linked,
            'fresh_nodes': fresh,
            'replay_ratio': ratio,
            'replay_records_examined': len(ledger_records),
        }

    # -- cross-subsystem enrichment -----------------------------------------

    @property
    def site_projection(self) -> dict[str, Any]:
        """Project the full provenance graph onto site coordinates.

        Maps every node's semantic coordinate into the coordinate system
        of ``jugeo.geometry.site``, returning a dictionary keyed by node
        id with site-object information.
        """
        try:
            from jugeo.geometry.site import project_coordinate
        except ImportError:
            return {}
        projection: dict[str, Any] = {}
        for nid, node in self._nodes.items():
            projection[nid] = project_coordinate(node.coordinate)
        return projection

    def encoding_trace(self) -> dict[str, Any]:
        """Aggregate encoding traces for all nodes in the graph.

        Queries ``jugeo.encodings`` and returns a mapping from node id
        to the encoding trace for that node.
        """
        try:
            from jugeo.encodings import trace_encoding_for
        except ImportError:
            return {'traces': {}, 'reason': 'encodings unavailable'}
        traces = {}
        for nid in self._nodes:
            traces[nid] = trace_encoding_for(nid)
        return {'traces': traces}

    def solver_trace(self) -> dict[str, Any]:
        """Aggregate solver traces for all solver-produced nodes.

        Filters nodes to those from solver channels and queries
        ``jugeo.solver`` for their Z3 query/response pairs.
        """
        try:
            from jugeo.solver import trace_solver_for
        except ImportError:
            return {'traces': {}, 'reason': 'solver unavailable'}
        traces = {}
        for nid, node in self._nodes.items():
            if node.is_solver_node():
                traces[nid] = trace_solver_for(nid)
        return {'traces': traces}

    def judgment_trace(self) -> dict[str, Any]:
        """Aggregate judgment-construction traces for all nodes.

        Queries ``jugeo.judgments`` for every node that has a non-empty
        ``output_judgment_id``, collecting their construction traces.
        """
        try:
            from jugeo.judgments import trace_judgment_for
        except ImportError:
            return {'traces': {}, 'reason': 'judgments unavailable'}
        traces = {}
        for nid, node in self._nodes.items():
            if node.output_judgment_id:
                traces[nid] = trace_judgment_for(node.output_judgment_id)
        return {'traces': traces}

    @property
    def orchestration_decisions(self) -> dict[str, list[dict[str, Any]]]:
        """Return orchestration decisions for every node in the graph.

        Queries ``jugeo.orchestration.controller`` for per-node routing
        and scheduling decisions, keyed by node id.
        """
        try:
            from jugeo.orchestration.controller import decisions_for_node
        except ImportError:
            return {}
        decisions: dict[str, list[dict[str, Any]]] = {}
        for nid in self._nodes:
            decisions[nid] = decisions_for_node(nid)
        return decisions

    def runtime_replay_trace(self) -> dict[str, Any]:
        """Aggregate runtime replay traces for all nodes.

        Queries ``jugeo.runtime.replay`` for each node's replay trace,
        enabling deterministic replay of the full evidence-production
        history captured by this graph.
        """
        try:
            from jugeo.runtime.replay import replay_trace_for
        except ImportError:
            return {'traces': {}, 'reason': 'replay unavailable'}
        traces = {}
        for nid in self._nodes:
            traces[nid] = replay_trace_for(nid)
        return {'traces': traces}


# ---------------------------------------------------------------------------
# 3. ProvenancePath
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProvenancePath:
    """An ordered path through a :class:`ProvenanceGraph`.

    A path represents a concrete chain of evidence from an origin node to a
    terminal node.  It is used for trust analysis and explanation generation.

    Attributes:
        nodes: Ordered tuple of :class:`ProvenanceNode` from start to end.
    """

    nodes: tuple[ProvenanceNode, ...] = ()

    @property
    def length(self) -> int:
        """Number of edges in the path (nodes - 1)."""
        return max(0, len(self.nodes) - 1)

    @property
    def node_ids(self) -> tuple[str, ...]:
        """Node identifiers in path order."""
        return tuple(n.node_id for n in self.nodes)

    def trust_along_path(self) -> tuple[str, ...]:
        """Return the trust tier at each node along the path."""
        return tuple(n.trust_at_creation for n in self.nodes)

    def weakest_link(self) -> ProvenanceNode | None:
        """Return the node with the lowest trust tier.

        Uses lexicographic comparison of trust labels as a heuristic;
        ``'proposal' < 'reviewed' < 'verified'``.
        """
        if not self.nodes:
            return None
        _order = {
            'contradicted': 0,
            'unverified': 1,
            'proposal': 2,
            'copilot_suggested': 2,
            'reviewed': 3,
            'oracle_proposed': 3,
            'human_attested': 4,
            'runtime_witnessed': 5,
            'verified': 6,
            'solver_discharged': 6,
            'mechanically_verified': 7,
        }
        return min(
            self.nodes,
            key=lambda n: _order.get(n.trust_at_creation, -1),
        )

    def is_trust_monotone(self) -> bool:
        """Return ``True`` if trust never decreases along the path.

        A monotone path is one where the trust tier at each successive node
        is ≥ the previous node's tier.
        """
        _order = {'proposal': 0, 'reviewed': 1, 'verified': 2}
        levels = [_order.get(n.trust_at_creation, 0) for n in self.nodes]
        return all(a <= b for a, b in zip(levels, levels[1:]))

    def has_copilot_node(self) -> bool:
        """Return ``True`` if any node in the path is a copilot node.

        copilot: Used for filtering paths that involve LLM-generated evidence.
        """
        return any(n.is_copilot_node() for n in self.nodes)

    def has_solver_node(self) -> bool:
        """Return ``True`` if any node in the path is a solver node."""
        return any(n.is_solver_node() for n in self.nodes)

    def channels_traversed(self) -> tuple[str, ...]:
        """Return distinct channels encountered along the path, in order."""
        seen: set[str] = set()
        result: list[str] = []
        for n in self.nodes:
            if n.source_channel not in seen:
                seen.add(n.source_channel)
                result.append(n.source_channel)
        return tuple(result)

    def operations_traversed(self) -> tuple[ProvenanceOperation, ...]:
        """Return the sequence of operations along the path."""
        return tuple(n.operation for n in self.nodes)

    def contains_promotion(self) -> bool:
        """Return ``True`` if any node in the path is a promotion."""
        return any(n.is_promotion() for n in self.nodes)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-ready dictionary."""
        return {'nodes': [n.to_dict() for n in self.nodes]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProvenancePath:
        """Reconstruct a path from a dictionary."""
        return cls(
            nodes=tuple(
                ProvenanceNode.from_dict(nd) for nd in data.get('nodes', [])
            ),
        )


# ---------------------------------------------------------------------------
# 4. ProvenanceQuery
# ---------------------------------------------------------------------------

class ProvenanceQuery:
    """Query interface over a :class:`ProvenanceGraph`.

    Provides filtered views and path-finding utilities used during audit,
    explanation, and invalidation workflows.

    copilot: This is the main read-side interface for LLM-driven queries.
    """

    def __init__(self, graph: ProvenanceGraph) -> None:
        self._graph = graph

    def by_channel(self, channel: str) -> tuple[ProvenanceNode, ...]:
        """Return all nodes produced by *channel*."""
        return tuple(
            self._graph.get_node(nid)
            for nid in self._graph.all_node_ids()
            if self._graph.get_node(nid).source_channel == channel
        )

    def by_coordinate(self, coordinate: str) -> tuple[ProvenanceNode, ...]:
        """Return all nodes associated with *coordinate*."""
        return tuple(
            self._graph.get_node(nid)
            for nid in self._graph.all_node_ids()
            if self._graph.get_node(nid).coordinate == coordinate
        )

    def by_time_range(
        self, start: float, end: float
    ) -> tuple[ProvenanceNode, ...]:
        """Return nodes whose timestamp falls in [start, end]."""
        return tuple(
            self._graph.get_node(nid)
            for nid in self._graph.all_node_ids()
            if start <= self._graph.get_node(nid).timestamp <= end
        )

    def by_trust_level(self, trust: str) -> tuple[ProvenanceNode, ...]:
        """Return all nodes created at a specific *trust* tier."""
        return tuple(
            self._graph.get_node(nid)
            for nid in self._graph.all_node_ids()
            if self._graph.get_node(nid).trust_at_creation == trust
        )

    def by_operation(
        self, operation: ProvenanceOperation
    ) -> tuple[ProvenanceNode, ...]:
        """Return all nodes with a specific *operation* type."""
        return tuple(
            self._graph.get_node(nid)
            for nid in self._graph.all_node_ids()
            if self._graph.get_node(nid).operation is operation
        )

    def by_judgment(self, judgment_id: str) -> tuple[ProvenanceNode, ...]:
        """Return all nodes associated with *judgment_id*."""
        return tuple(
            self._graph.get_node(nid)
            for nid in self._graph.all_node_ids()
            if self._graph.get_node(nid).output_judgment_id == judgment_id
        )

    def shortest_path_between(
        self, start_id: str, end_id: str
    ) -> ProvenancePath | None:
        """Return the shortest directed path from *start_id* to *end_id*.

        Uses BFS over the child adjacency.  Returns ``None`` if no path
        exists.
        """
        if start_id not in self._graph or end_id not in self._graph:
            return None
        if start_id == end_id:
            return ProvenancePath(
                nodes=(self._graph.get_node(start_id),)
            )
        visited: set[str] = {start_id}
        parent: dict[str, str] = {}
        queue: deque[str] = deque([start_id])
        while queue:
            current = queue.popleft()
            for child in self._graph.children_of(current):
                if child in visited:
                    continue
                parent[child] = current
                if child == end_id:
                    # reconstruct
                    path_ids: list[str] = [end_id]
                    c = end_id
                    while c in parent:
                        c = parent[c]
                        path_ids.append(c)
                    path_ids.reverse()
                    return ProvenancePath(
                        nodes=tuple(
                            self._graph.get_node(pid) for pid in path_ids
                        )
                    )
                visited.add(child)
                queue.append(child)
        return None

    def all_paths_between(
        self, start_id: str, end_id: str, *, max_paths: int = 100
    ) -> list[ProvenancePath]:
        """Return all directed paths from *start_id* to *end_id*.

        The search is bounded by *max_paths* to prevent combinatorial
        explosion in dense graphs.
        """
        if start_id not in self._graph or end_id not in self._graph:
            return []
        results: list[ProvenancePath] = []

        def _dfs(current: str, visited: set[str], path: list[str]) -> None:
            if len(results) >= max_paths:
                return
            if current == end_id:
                results.append(
                    ProvenancePath(
                        nodes=tuple(
                            self._graph.get_node(pid) for pid in path
                        )
                    )
                )
                return
            for child in self._graph.children_of(current):
                if child not in visited:
                    visited.add(child)
                    path.append(child)
                    _dfs(child, visited, path)
                    path.pop()
                    visited.discard(child)

        _dfs(start_id, {start_id}, [start_id])
        return results

    def copilot_nodes(self) -> tuple[ProvenanceNode, ...]:
        """Return all nodes associated with copilot channels.

        copilot: Convenience method for LLM provenance auditing.
        """
        return tuple(
            self._graph.get_node(nid)
            for nid in self._graph.all_node_ids()
            if self._graph.get_node(nid).is_copilot_node()
        )


# ---------------------------------------------------------------------------
# 5. ProvenanceValidator
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single issue discovered during provenance validation."""

    severity: str  # 'error' | 'warning'
    category: str
    node_id: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'severity': self.severity,
            'category': self.category,
            'node_id': self.node_id,
            'message': self.message,
            'details': dict(self.details),
        }


class ProvenanceValidator:
    """Validates structural and semantic integrity of a provenance graph.

    The validator checks the graph against the rules from theory2.tex:
    * No cycles (acyclicity).
    * Trust monotonicity on every root-to-leaf path.
    * No circular reasoning across channels.
    * Channel jurisdiction constraints.
    * No silent (unjustified) promotions.

    copilot: Validation results drive copilot feedback on evidence quality.
    """

    def __init__(self, graph: ProvenanceGraph) -> None:
        self._graph = graph

    def check_acyclicity(self) -> list[ValidationIssue]:
        """Check that the provenance graph is a DAG."""
        cycles = self._graph.detect_cycles()
        issues: list[ValidationIssue] = []
        for cycle in cycles:
            issues.append(
                ValidationIssue(
                    severity='error',
                    category='acyclicity',
                    node_id=cycle[0] if cycle else '',
                    message=f'Cycle detected involving {len(cycle)} nodes: {" → ".join(cycle)}',
                    details={'cycle': cycle},
                )
            )
        return issues

    def check_trust_monotonicity(self) -> list[ValidationIssue]:
        """Check that trust never silently increases along any path.

        A trust increase is valid only if the node's operation is PROMOTED.
        """
        _order = {'proposal': 0, 'reviewed': 1, 'verified': 2}
        issues: list[ValidationIssue] = []
        for nid in self._graph.all_node_ids():
            node = self._graph.get_node(nid)
            for parent_id in self._graph.parents_of(nid):
                parent = self._graph.get_node(parent_id)
                p_level = _order.get(parent.trust_at_creation, 0)
                n_level = _order.get(node.trust_at_creation, 0)
                if n_level > p_level and node.operation is not ProvenanceOperation.PROMOTED:
                    issues.append(
                        ValidationIssue(
                            severity='error',
                            category='trust_monotonicity',
                            node_id=nid,
                            message=(
                                f'Trust increased from {parent.trust_at_creation!r} '
                                f'to {node.trust_at_creation!r} without PROMOTED operation'
                            ),
                            details={
                                'parent_id': parent_id,
                                'parent_trust': parent.trust_at_creation,
                                'node_trust': node.trust_at_creation,
                            },
                        )
                    )
        return issues

    def check_no_circular_reasoning(self) -> list[ValidationIssue]:
        """Detect circular reasoning where a judgment depends on itself.

        This is distinct from graph cycles — circular reasoning can occur
        when a node's output_judgment_id appears in the ancestry's
        output_judgment_ids.
        """
        issues: list[ValidationIssue] = []
        for nid in self._graph.all_node_ids():
            node = self._graph.get_node(nid)
            if not node.output_judgment_id:
                continue
            for ancestor_id in self._graph.ancestors_of(nid):
                ancestor = self._graph.get_node(ancestor_id)
                if ancestor.output_judgment_id == node.output_judgment_id:
                    issues.append(
                        ValidationIssue(
                            severity='error',
                            category='circular_reasoning',
                            node_id=nid,
                            message=(
                                f'Judgment {node.output_judgment_id!r} appears '
                                f'in its own ancestry via node {ancestor_id}'
                            ),
                            details={
                                'judgment_id': node.output_judgment_id,
                                'ancestor_id': ancestor_id,
                            },
                        )
                    )
        return issues

    def check_channel_jurisdiction(
        self,
        jurisdiction: Mapping[str, frozenset[str]] | None = None,
    ) -> list[ValidationIssue]:
        """Check that each channel only operates on permitted coordinates.

        *jurisdiction* maps channel name → frozenset of allowed coordinate
        prefixes.  If ``None``, the check is skipped.
        """
        if jurisdiction is None:
            return []
        issues: list[ValidationIssue] = []
        for nid in self._graph.all_node_ids():
            node = self._graph.get_node(nid)
            allowed = jurisdiction.get(node.source_channel)
            if allowed is None:
                continue
            if not any(
                node.coordinate.startswith(prefix) for prefix in allowed
            ):
                issues.append(
                    ValidationIssue(
                        severity='warning',
                        category='channel_jurisdiction',
                        node_id=nid,
                        message=(
                            f'Channel {node.source_channel!r} operated on '
                            f'coordinate {node.coordinate!r} outside its '
                            f'jurisdiction {sorted(allowed)}'
                        ),
                        details={
                            'channel': node.source_channel,
                            'coordinate': node.coordinate,
                            'allowed_prefixes': sorted(allowed),
                        },
                    )
                )
        return issues

    def check_no_silent_promotions(self) -> list[ValidationIssue]:
        """Check that every PROMOTED node has justification metadata.

        A promotion without a ``'justification'`` key in metadata is
        considered silent and therefore suspect.
        """
        issues: list[ValidationIssue] = []
        for nid in self._graph.all_node_ids():
            node = self._graph.get_node(nid)
            if node.operation is ProvenanceOperation.PROMOTED:
                if 'justification' not in node.metadata:
                    issues.append(
                        ValidationIssue(
                            severity='error',
                            category='silent_promotion',
                            node_id=nid,
                            message=(
                                f'Promotion at node {nid} lacks justification '
                                f'metadata'
                            ),
                        )
                    )
        return issues

    def full_validation(
        self,
        *,
        jurisdiction: Mapping[str, frozenset[str]] | None = None,
    ) -> list[ValidationIssue]:
        """Run all validation checks and return combined issues.

        copilot: Called automatically before publishing any judgment.
        """
        issues: list[ValidationIssue] = []
        issues.extend(self.check_acyclicity())
        issues.extend(self.check_trust_monotonicity())
        issues.extend(self.check_no_circular_reasoning())
        issues.extend(self.check_channel_jurisdiction(jurisdiction))
        issues.extend(self.check_no_silent_promotions())
        return issues


# ---------------------------------------------------------------------------
# 6. ProvenanceInvalidator
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class InvalidationRecord:
    """Record of a single invalidation event."""

    node_id: str
    reason: InvalidationReason
    triggered_by: str
    timestamp: float
    affected_judgments: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            'node_id': self.node_id,
            'reason': self.reason.value,
            'triggered_by': self.triggered_by,
            'timestamp': self.timestamp,
            'affected_judgments': list(self.affected_judgments),
        }


class ProvenanceInvalidator:
    """Cascade invalidation through a provenance graph when evidence is revoked.

    When upstream evidence is revoked or found faulty, every downstream node
    that transitively depends on it must be invalidated.  The invalidator
    computes the blast radius efficiently and notifies registered subscribers.

    copilot: Invalidation cascades may trigger copilot re-evaluation of
    affected judgments.
    """

    def __init__(self, graph: ProvenanceGraph) -> None:
        self._graph = graph
        self._invalidated: dict[str, InvalidationRecord] = {}
        self._subscribers: list[Callable[[InvalidationRecord], None]] = []

    def subscribe(
        self, callback: Callable[[InvalidationRecord], None]
    ) -> None:
        """Register a callback to be invoked on each invalidation."""
        self._subscribers.append(callback)

    @property
    def invalidated_ids(self) -> frozenset[str]:
        """Return all currently invalidated node ids."""
        return frozenset(self._invalidated)

    def invalidate_node(
        self, node_id: str, *, reason: InvalidationReason = InvalidationReason.REVOKED
    ) -> InvalidationRecord:
        """Mark *node_id* as invalid and return the record.

        Does **not** cascade — use :meth:`cascade_invalidation` for that.
        """
        node = self._graph.get_node(node_id)
        record = InvalidationRecord(
            node_id=node_id,
            reason=reason,
            triggered_by=node_id,
            timestamp=time.time(),
            affected_judgments=(node.output_judgment_id,)
            if node.output_judgment_id
            else (),
        )
        self._invalidated[node_id] = record
        self.notify_subscribers(record)
        return record

    def cascade_invalidation(
        self, root_id: str, *, reason: InvalidationReason = InvalidationReason.REVOKED
    ) -> list[InvalidationRecord]:
        """Invalidate *root_id* and all its transitive descendants.

        Returns the list of all invalidation records created during the
        cascade (including the root).
        """
        records: list[InvalidationRecord] = []
        # invalidate root
        records.append(self.invalidate_node(root_id, reason=reason))
        # BFS downstream
        queue: deque[str] = deque(self._graph.children_of(root_id))
        visited: set[str] = {root_id}
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            node = self._graph.get_node(nid)
            rec = InvalidationRecord(
                node_id=nid,
                reason=InvalidationReason.UPSTREAM_INVALID,
                triggered_by=root_id,
                timestamp=time.time(),
                affected_judgments=(node.output_judgment_id,)
                if node.output_judgment_id
                else (),
            )
            self._invalidated[nid] = rec
            records.append(rec)
            self.notify_subscribers(rec)
            queue.extend(self._graph.children_of(nid))
        return records

    def compute_affected_judgments(self, root_id: str) -> frozenset[str]:
        """Return judgment ids that would be affected if *root_id* is revoked.

        This is a dry-run that does not actually invalidate anything.
        """
        descendants = self._graph.descendants_of(root_id)
        all_ids = frozenset({root_id}) | descendants
        judgments: set[str] = set()
        for nid in all_ids:
            jid = self._graph.get_node(nid).output_judgment_id
            if jid:
                judgments.add(jid)
        return frozenset(judgments)

    def compute_repair_frontier(self) -> frozenset[str]:
        """Return node ids that are the boundary of valid → invalid.

        These are the *earliest* invalidated nodes whose parents are all
        still valid (or have no parents).  Repairing these nodes would
        restore the downstream chain.
        """
        frontier: set[str] = set()
        for nid in self._invalidated:
            parents = self._graph.parents_of(nid)
            if not parents or all(p not in self._invalidated for p in parents):
                frontier.add(nid)
        return frozenset(frontier)

    def is_invalidated(self, node_id: str) -> bool:
        """Return whether *node_id* has been invalidated."""
        return node_id in self._invalidated

    def notify_subscribers(self, record: InvalidationRecord) -> None:
        """Invoke all registered subscriber callbacks with *record*."""
        for cb in self._subscribers:
            try:
                cb(record)
            except Exception:
                pass  # subscribers must not break the invalidation cascade

    def reset(self) -> None:
        """Clear all invalidation state."""
        self._invalidated.clear()


# ---------------------------------------------------------------------------
# 7. ProvenanceExplainer
# ---------------------------------------------------------------------------

class ProvenanceExplainer:
    """Generate human-readable (and copilot-readable) explanations.

    The explainer walks the provenance graph and produces natural-language
    summaries of how a judgment came to be, what trust level it has, and
    what evidence chain supports it.

    copilot: Explanations are the primary interface for copilot to
    communicate provenance to end-users.
    """

    def __init__(self, graph: ProvenanceGraph) -> None:
        self._graph = graph
        self._query = ProvenanceQuery(graph)

    def explain_judgment(self, judgment_id: str) -> str:
        """Produce a textual explanation of how *judgment_id* was derived.

        Walks backward from all nodes with that judgment id and summarizes
        the evidence chain.
        """
        nodes = self._query.by_judgment(judgment_id)
        if not nodes:
            return f'No provenance found for judgment {judgment_id!r}.'
        lines: list[str] = [f'Provenance for judgment {judgment_id!r}:']
        for node in sorted(nodes, key=lambda n: n.timestamp):
            parents = self._graph.parents_of(node.node_id)
            parent_str = ', '.join(parents) if parents else '(root)'
            lines.append(
                f'  • [{node.operation.value.upper()}] {node.node_id} '
                f'via channel {node.source_channel!r} '
                f'at coordinate {node.coordinate!r} '
                f'(trust: {node.trust_at_creation}) ← {parent_str}'
            )
        return '\n'.join(lines)

    def explain_trust_level(self, node_id: str) -> str:
        """Explain why a node has its current trust level.

        Traces back to the root and reports each trust transition.
        """
        node = self._graph.get_node(node_id)
        ancestors = self._graph.ancestors_of(node_id)
        root_ids = [
            a for a in ancestors
            if self._graph.get_node(a).is_root()
        ]
        if not root_ids:
            root_ids = [node_id] if node.is_root() else []

        lines: list[str] = [
            f'Trust explanation for node {node_id}:',
            f'  Current trust: {node.trust_at_creation}',
        ]
        for rid in root_ids:
            path = self._query.shortest_path_between(rid, node_id)
            if path is not None:
                transitions = path.trust_along_path()
                lines.append(
                    f'  Path from root {rid}: '
                    + ' → '.join(transitions)
                )
        return '\n'.join(lines)

    def explain_evidence_chain(self, start_id: str, end_id: str) -> str:
        """Describe the evidence chain between two nodes."""
        path = self._query.shortest_path_between(start_id, end_id)
        if path is None:
            return f'No path from {start_id} to {end_id}.'
        lines: list[str] = [f'Evidence chain ({start_id} → {end_id}):']
        for i, node in enumerate(path.nodes):
            prefix = '  START' if i == 0 else f'  [{i}]  '
            lines.append(
                f'{prefix} {node.node_id} ({node.operation.value}) '
                f'channel={node.source_channel!r} '
                f'trust={node.trust_at_creation}'
            )
        wl = path.weakest_link()
        if wl:
            lines.append(f'  Weakest link: {wl.node_id} ({wl.trust_at_creation})')
        if path.contains_promotion():
            lines.append('  ⚠ Path contains a trust promotion.')
        return '\n'.join(lines)

    def generate_audit_report(self) -> str:
        """Generate a full audit report for the entire provenance graph."""
        stats = ProvenanceStatistics(self._graph)
        validator = ProvenanceValidator(self._graph)
        issues = validator.full_validation()

        lines: list[str] = [
            '═══ Provenance Audit Report ═══',
            f'Nodes:     {stats.node_count()}',
            f'Edges:     {stats.edge_count()}',
            f'Max depth: {stats.max_depth()}',
            f'Roots:     {len(self._graph.find_roots())}',
            f'Leaves:    {len(self._graph.find_leaves())}',
            f'Acyclic:   {self._graph.is_acyclic()}',
            '',
        ]
        # channel distribution
        dist = stats.channel_distribution()
        if dist:
            lines.append('Channel distribution:')
            for ch, count in sorted(dist.items(), key=lambda x: -x[1]):
                lines.append(f'  {ch}: {count}')
            lines.append('')

        # trust distribution
        tdist = stats.trust_distribution()
        if tdist:
            lines.append('Trust distribution:')
            for tl, count in sorted(tdist.items()):
                lines.append(f'  {tl}: {count}')
            lines.append('')

        # issues
        if issues:
            lines.append(f'Issues ({len(issues)}):')
            for issue in issues:
                lines.append(
                    f'  [{issue.severity.upper()}] {issue.category}: '
                    f'{issue.message}'
                )
        else:
            lines.append('No issues found.')
        return '\n'.join(lines)

    def copilot_explanation(self, node_id: str) -> str:
        """Produce a copilot-friendly summary suitable for LLM consumption.

        copilot: This method formats provenance data for injection into
        LLM prompts during copilot-assisted judgment review.
        """
        node = self._graph.get_node(node_id)
        parents = self._graph.parents_of(node_id)
        children = self._graph.children_of(node_id)
        ancestors_count = len(self._graph.ancestors_of(node_id))
        descendants_count = len(self._graph.descendants_of(node_id))

        return (
            f'[PROVENANCE] node={node_id} '
            f'op={node.operation.value} '
            f'channel={node.source_channel} '
            f'coord={node.coordinate} '
            f'trust={node.trust_at_creation} '
            f'parents={len(parents)} children={len(children)} '
            f'ancestors={ancestors_count} descendants={descendants_count} '
            f'judgment={node.output_judgment_id or "(none)"}'
        )


# ---------------------------------------------------------------------------
# 8. ProvenanceSerializer
# ---------------------------------------------------------------------------

class ProvenanceSerializer:
    """JSON serialization for provenance nodes, graphs, paths, and queries.

    All serialization is deterministic (sorted keys) for reproducible
    hashing and comparison.
    """

    @staticmethod
    def serialize_node(node: ProvenanceNode) -> str:
        """Serialize a single node to a JSON string."""
        return json.dumps(node.to_dict(), sort_keys=True)

    @staticmethod
    def deserialize_node(data: str) -> ProvenanceNode:
        """Deserialize a node from a JSON string."""
        return ProvenanceNode.from_dict(json.loads(data))

    @staticmethod
    def serialize_graph(graph: ProvenanceGraph) -> str:
        """Serialize a full graph to a JSON string.

        Nodes are emitted in topological order when the graph is acyclic,
        otherwise in insertion order.
        """
        try:
            ordered = graph.topological_sort()
        except ValueError:
            ordered = list(graph.all_node_ids())
        payload = {
            'nodes': [graph.get_node(nid).to_dict() for nid in ordered],
        }
        return json.dumps(payload, sort_keys=True)

    @staticmethod
    def deserialize_graph(data: str) -> ProvenanceGraph:
        """Deserialize a graph from a JSON string."""
        return ProvenanceGraph.from_dict(json.loads(data))

    @staticmethod
    def serialize_path(path: ProvenancePath) -> str:
        """Serialize a path to a JSON string."""
        return json.dumps(path.to_dict(), sort_keys=True)

    @staticmethod
    def deserialize_path(data: str) -> ProvenancePath:
        """Deserialize a path from a JSON string."""
        return ProvenancePath.from_dict(json.loads(data))

    @staticmethod
    def serialize_issues(issues: Sequence[ValidationIssue]) -> str:
        """Serialize a list of validation issues to JSON."""
        return json.dumps(
            [issue.to_dict() for issue in issues], sort_keys=True
        )

    @staticmethod
    def serialize_invalidation_records(
        records: Sequence[InvalidationRecord],
    ) -> str:
        """Serialize invalidation records to JSON."""
        return json.dumps(
            [rec.to_dict() for rec in records], sort_keys=True
        )


# ---------------------------------------------------------------------------
# 9. ProvenanceMerger
# ---------------------------------------------------------------------------

class ProvenanceMerger:
    """Merge provenance graphs from different subsystems.

    During composition of evidence from multiple channels or federation
    across packs, provenance graphs must be merged while preserving trust
    paths and deduplicating shared nodes.

    copilot: Merging is used when copilot integrates evidence from solver
    and proof channels into a unified provenance view.
    """

    def merge_graphs(
        self, *graphs: ProvenanceGraph
    ) -> ProvenanceGraph:
        """Merge multiple graphs into a single unified graph.

        Nodes are deduplicated by ``node_id``.  When the same ``node_id``
        appears in multiple graphs the first occurrence wins.
        """
        merged = ProvenanceGraph()
        seen: set[str] = set()
        # Collect all nodes, sort by timestamp for deterministic merge order
        all_nodes: list[ProvenanceNode] = []
        for g in graphs:
            for nid in g.all_node_ids():
                if nid not in seen:
                    seen.add(nid)
                    all_nodes.append(g.get_node(nid))
        all_nodes.sort(key=lambda n: n.timestamp)
        # Insert in timestamp order; skip nodes whose inputs aren't present
        pending: list[ProvenanceNode] = []
        inserted: set[str] = set()
        for node in all_nodes:
            if all(inp in inserted for inp in node.inputs):
                merged._nodes[node.node_id] = node
                for inp in node.inputs:
                    merged._children[inp].append(node.node_id)
                    merged._parents[node.node_id].append(inp)
                inserted.add(node.node_id)
            else:
                pending.append(node)
        # Retry pending nodes (handles cross-graph dependencies)
        max_iterations = len(pending) + 1
        for _ in range(max_iterations):
            if not pending:
                break
            still_pending: list[ProvenanceNode] = []
            for node in pending:
                if all(inp in inserted for inp in node.inputs):
                    merged._nodes[node.node_id] = node
                    for inp in node.inputs:
                        merged._children[inp].append(node.node_id)
                        merged._parents[node.node_id].append(inp)
                    inserted.add(node.node_id)
                else:
                    still_pending.append(node)
            if len(still_pending) == len(pending):
                # No progress — remaining nodes have missing dependencies;
                # insert them with restricted inputs
                for node in still_pending:
                    available_inputs = tuple(
                        i for i in node.inputs if i in inserted
                    )
                    patched = replace(node, inputs=available_inputs)
                    merged._nodes[patched.node_id] = patched
                    for inp in available_inputs:
                        merged._children[inp].append(patched.node_id)
                        merged._parents[patched.node_id].append(inp)
                    inserted.add(patched.node_id)
                break
            pending = still_pending
        return merged

    def resolve_conflicts(
        self,
        graph_a: ProvenanceGraph,
        graph_b: ProvenanceGraph,
        *,
        prefer: str = 'a',
    ) -> ProvenanceGraph:
        """Merge two graphs, resolving node conflicts by preferring one side.

        When the same ``node_id`` exists in both graphs with different
        content, *prefer* selects which version to keep (``'a'`` or ``'b'``).
        """
        if prefer == 'b':
            return self.merge_graphs(graph_b, graph_a)
        return self.merge_graphs(graph_a, graph_b)

    def deduplicate_nodes(
        self, graph: ProvenanceGraph
    ) -> ProvenanceGraph:
        """Return a copy of *graph* with content-duplicate nodes collapsed.

        Two nodes are content-duplicates if they share the same
        ``source_channel``, ``operation``, ``coordinate``,
        ``output_judgment_id``, and ``inputs``.
        """
        fingerprints: dict[tuple[Any, ...], str] = {}
        remap: dict[str, str] = {}
        for nid in graph.all_node_ids():
            node = graph.get_node(nid)
            fp = (
                node.source_channel,
                node.operation,
                node.coordinate,
                node.output_judgment_id,
                node.inputs,
            )
            if fp in fingerprints:
                remap[nid] = fingerprints[fp]
            else:
                fingerprints[fp] = nid
                remap[nid] = nid

        deduped = ProvenanceGraph()
        inserted: set[str] = set()
        try:
            order = graph.topological_sort()
        except ValueError:
            order = list(graph.all_node_ids())
        for nid in order:
            canonical = remap[nid]
            if canonical in inserted:
                continue
            node = graph.get_node(canonical)
            remapped_inputs = tuple(
                remap.get(i, i) for i in node.inputs
            )
            patched = replace(node, inputs=remapped_inputs)
            deduped._nodes[patched.node_id] = patched
            for inp in remapped_inputs:
                if inp in inserted:
                    deduped._children[inp].append(patched.node_id)
                    deduped._parents[patched.node_id].append(inp)
            inserted.add(canonical)
        return deduped

    def preserve_trust_paths(
        self, merged: ProvenanceGraph, originals: Sequence[ProvenanceGraph]
    ) -> list[ProvenancePath]:
        """Verify that all root-to-leaf trust paths in *originals* survive in *merged*.

        Returns a list of paths from the originals that are fully present
        in the merged graph.
        """
        preserved: list[ProvenancePath] = []
        query = ProvenanceQuery(merged)
        for g in originals:
            roots = g.find_roots()
            leaves = g.find_leaves()
            for r in roots:
                for lf in leaves:
                    if r in merged and lf in merged:
                        path = query.shortest_path_between(r, lf)
                        if path is not None:
                            preserved.append(path)
        return preserved


# ---------------------------------------------------------------------------
# 10. ProvenanceStatistics
# ---------------------------------------------------------------------------

class ProvenanceStatistics:
    """Compute descriptive statistics over a :class:`ProvenanceGraph`.

    copilot: Statistics are surfaced in audit reports and copilot
    diagnostics dashboards.
    """

    def __init__(self, graph: ProvenanceGraph) -> None:
        self._graph = graph

    def node_count(self) -> int:
        """Total number of nodes."""
        return len(self._graph)

    def edge_count(self) -> int:
        """Total number of directed edges."""
        count = 0
        for nid in self._graph.all_node_ids():
            count += len(self._graph.children_of(nid))
        return count

    def max_depth(self) -> int:
        """Longest path from any root to any leaf (number of edges).

        Returns 0 for an empty graph.
        """
        if len(self._graph) == 0:
            return 0
        try:
            order = self._graph.topological_sort()
        except ValueError:
            return -1  # cyclic
        depth: dict[str, int] = {}
        for nid in order:
            parents = self._graph.parents_of(nid)
            if not parents:
                depth[nid] = 0
            else:
                depth[nid] = max(depth.get(p, 0) for p in parents) + 1
        return max(depth.values()) if depth else 0

    def average_branching_factor(self) -> float:
        """Average number of children per non-leaf node."""
        non_leaves = [
            nid for nid in self._graph.all_node_ids()
            if len(self._graph.children_of(nid)) > 0
        ]
        if not non_leaves:
            return 0.0
        total = sum(
            len(self._graph.children_of(nid)) for nid in non_leaves
        )
        return total / len(non_leaves)

    def channel_distribution(self) -> dict[str, int]:
        """Count of nodes per source channel."""
        dist: dict[str, int] = defaultdict(int)
        for nid in self._graph.all_node_ids():
            dist[self._graph.get_node(nid).source_channel] += 1
        return dict(dist)

    def trust_distribution(self) -> dict[str, int]:
        """Count of nodes per trust level."""
        dist: dict[str, int] = defaultdict(int)
        for nid in self._graph.all_node_ids():
            dist[self._graph.get_node(nid).trust_at_creation] += 1
        return dict(dist)

    def operation_distribution(self) -> dict[str, int]:
        """Count of nodes per operation type."""
        dist: dict[str, int] = defaultdict(int)
        for nid in self._graph.all_node_ids():
            dist[self._graph.get_node(nid).operation.value] += 1
        return dict(dist)

    def cycle_count(self) -> int:
        """Number of distinct cycles detected."""
        return len(self._graph.detect_cycles())

    def copilot_node_fraction(self) -> float:
        """Fraction of nodes that are copilot-sourced.

        copilot: Used to assess how much of the provenance is LLM-generated.
        """
        if len(self._graph) == 0:
            return 0.0
        copilot_count = sum(
            1
            for nid in self._graph.all_node_ids()
            if self._graph.get_node(nid).is_copilot_node()
        )
        return copilot_count / len(self._graph)

    def summary(self) -> dict[str, Any]:
        """Return a JSON-ready summary of all statistics."""
        return {
            'node_count': self.node_count(),
            'edge_count': self.edge_count(),
            'max_depth': self.max_depth(),
            'average_branching_factor': round(
                self.average_branching_factor(), 3
            ),
            'channel_distribution': self.channel_distribution(),
            'trust_distribution': self.trust_distribution(),
            'operation_distribution': self.operation_distribution(),
            'cycle_count': self.cycle_count(),
            'copilot_node_fraction': round(
                self.copilot_node_fraction(), 3
            ),
        }


# ---------------------------------------------------------------------------
# 11. ProvenanceArchive
# ---------------------------------------------------------------------------

class ProvenanceArchive:
    """Persistent storage for provenance graphs.

    The archive provides append-only semantics: once a node is archived it
    cannot be mutated, only pruned.  The in-memory implementation stores
    serialized JSON; subclasses may persist to disk or a database.

    copilot: Archives are the long-term audit trail for compliance.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}  # node_id → JSON
        self._archived_at: dict[str, float] = {}

    def archive(self, graph: ProvenanceGraph) -> int:
        """Archive all nodes from *graph*.  Returns the count of new nodes."""
        count = 0
        serializer = ProvenanceSerializer()
        for nid in graph.all_node_ids():
            if nid not in self._store:
                self._store[nid] = serializer.serialize_node(
                    graph.get_node(nid)
                )
                self._archived_at[nid] = time.time()
                count += 1
        return count

    def retrieve(self, node_id: str) -> ProvenanceNode | None:
        """Retrieve a single archived node by id, or ``None``."""
        data = self._store.get(node_id)
        if data is None:
            return None
        return ProvenanceSerializer.deserialize_node(data)

    def retrieve_many(self, node_ids: Iterable[str]) -> list[ProvenanceNode]:
        """Retrieve multiple nodes, skipping missing ones."""
        results: list[ProvenanceNode] = []
        for nid in node_ids:
            node = self.retrieve(nid)
            if node is not None:
                results.append(node)
        return results

    def contains(self, node_id: str) -> bool:
        """Return ``True`` if *node_id* is in the archive."""
        return node_id in self._store

    def compact(self) -> int:
        """Compact the archive by re-serializing all nodes.

        Returns the number of nodes compacted.  In the in-memory
        implementation this is a no-op that validates serialization.
        """
        count = 0
        for nid in list(self._store):
            node = self.retrieve(nid)
            if node is not None:
                self._store[nid] = ProvenanceSerializer.serialize_node(node)
                count += 1
        return count

    def prune_before(self, cutoff: float) -> int:
        """Remove all nodes archived before *cutoff* (unix timestamp).

        Returns the number of nodes pruned.
        """
        to_remove = [
            nid
            for nid, ts in self._archived_at.items()
            if ts < cutoff
        ]
        for nid in to_remove:
            del self._store[nid]
            del self._archived_at[nid]
        return len(to_remove)

    def export_to_json(self) -> str:
        """Export the entire archive as a JSON string."""
        nodes: list[dict[str, Any]] = []
        for nid in sorted(self._store):
            node = self.retrieve(nid)
            if node is not None:
                entry = node.to_dict()
                entry['archived_at'] = self._archived_at.get(nid, 0.0)
                nodes.append(entry)
        return json.dumps({'archived_nodes': nodes}, sort_keys=True, indent=2)

    def import_from_json(self, data: str) -> int:
        """Import nodes from a JSON string.  Returns count of new nodes."""
        payload = json.loads(data)
        count = 0
        for entry in payload.get('archived_nodes', []):
            nid = entry.get('node_id', '')
            if nid and nid not in self._store:
                archived_at = entry.pop('archived_at', time.time())
                self._store[nid] = ProvenanceSerializer.serialize_node(
                    ProvenanceNode.from_dict(entry)
                )
                self._archived_at[nid] = archived_at
                count += 1
        return count

    @property
    def size(self) -> int:
        """Total number of nodes in the archive."""
        return len(self._store)


# ---------------------------------------------------------------------------
# 12. CircularReasoningDetector
# ---------------------------------------------------------------------------

class CircularReasoningDetector:
    """Specialized detector for circular reasoning in provenance graphs.

    Circular reasoning occurs when a judgment is used (directly or
    transitively) as evidence for itself.  This is distinct from simple
    graph cycles — it operates at the *judgment identity* level.

    copilot: Circular reasoning detection is critical for ensuring that
    copilot-generated evidence does not create self-referential loops.
    """

    def __init__(self, graph: ProvenanceGraph) -> None:
        self._graph = graph

    def detect(self) -> list[list[str]]:
        """Return all circular-reasoning chains as lists of node ids.

        A chain is a sequence of nodes where the first and last share the
        same ``output_judgment_id``, forming a logical circle.
        """
        # Group nodes by judgment id
        by_judgment: dict[str, list[str]] = defaultdict(list)
        for nid in self._graph.all_node_ids():
            jid = self._graph.get_node(nid).output_judgment_id
            if jid:
                by_judgment[jid].append(nid)

        circles: list[list[str]] = []
        query = ProvenanceQuery(self._graph)
        for jid, nids in by_judgment.items():
            if len(nids) < 2:
                continue
            # Check if any node in this judgment group is an ancestor of
            # another node in the same group
            for i, a in enumerate(nids):
                ancestors_a = self._graph.ancestors_of(a)
                for b in nids[i + 1:]:
                    if b in ancestors_a:
                        path = query.shortest_path_between(b, a)
                        if path is not None:
                            circles.append(list(path.node_ids))
                    ancestors_b = self._graph.ancestors_of(b)
                    if a in ancestors_b:
                        path = query.shortest_path_between(a, b)
                        if path is not None:
                            circles.append(list(path.node_ids))
        return circles

    def classify_cycle(self, cycle: Sequence[str]) -> CycleKind:
        """Classify a detected cycle by its structure.

        Returns:
            :attr:`CycleKind.SELF_LOOP` — single node references itself.
            :attr:`CycleKind.MUTUAL` — two nodes reference each other.
            :attr:`CycleKind.CROSS_CHANNEL` — cycle spans multiple channels.
            :attr:`CycleKind.TRANSITIVE` — cycle spans 3+ nodes in one channel.
        """
        if len(cycle) <= 1:
            return CycleKind.SELF_LOOP
        if len(cycle) == 2:
            return CycleKind.MUTUAL
        channels = {
            self._graph.get_node(nid).source_channel for nid in cycle
            if nid in self._graph
        }
        if len(channels) > 1:
            return CycleKind.CROSS_CHANNEL
        return CycleKind.TRANSITIVE

    def suggest_break_point(self, cycle: Sequence[str]) -> str | None:
        """Suggest which node to remove to break the circular reasoning.

        Heuristic: prefer removing the node with the lowest trust level,
        and among ties, the most recent node (highest timestamp).
        """
        if not cycle:
            return None
        _order = {'proposal': 0, 'reviewed': 1, 'verified': 2}
        valid_nodes = [
            nid for nid in cycle if nid in self._graph
        ]
        if not valid_nodes:
            return None
        return min(
            valid_nodes,
            key=lambda nid: (
                _order.get(
                    self._graph.get_node(nid).trust_at_creation, 0
                ),
                -self._graph.get_node(nid).timestamp,
            ),
        )

    def copilot_cycle_summary(self, cycle: Sequence[str]) -> str:
        """Generate a copilot-friendly summary of a circular reasoning cycle.

        copilot: This summary is injected into LLM prompts when circular
        reasoning is detected so the copilot can suggest remediation.
        """
        if not cycle:
            return '[CIRCULAR REASONING] Empty cycle.'
        kind = self.classify_cycle(cycle)
        break_point = self.suggest_break_point(cycle)
        nodes_info: list[str] = []
        for nid in cycle:
            if nid in self._graph:
                node = self._graph.get_node(nid)
                nodes_info.append(
                    f'{nid}(ch={node.source_channel}, '
                    f'trust={node.trust_at_creation}, '
                    f'op={node.operation.value})'
                )
            else:
                nodes_info.append(f'{nid}(missing)')
        return (
            f'[CIRCULAR REASONING] kind={kind.value} '
            f'length={len(cycle)} '
            f'nodes=[{" → ".join(nodes_info)}] '
            f'suggested_break={break_point or "(none)"}'
        )

    def detect_and_report(self) -> list[dict[str, Any]]:
        """Detect all circular reasoning and return structured reports.

        Each report contains the cycle node ids, classification,
        suggested break point, and a copilot summary.
        """
        circles = self.detect()
        reports: list[dict[str, Any]] = []
        for cycle in circles:
            reports.append({
                'cycle': cycle,
                'kind': self.classify_cycle(cycle).value,
                'break_point': self.suggest_break_point(cycle),
                'summary': self.copilot_cycle_summary(cycle),
            })
        return reports


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Legacy types
    'ProvenanceStep',
    'ProvenanceTrace',
    # Enumerations
    'ProvenanceOperation',
    'InvalidationReason',
    'CycleKind',
    # Core types
    'ProvenanceNode',
    'ProvenanceGraph',
    'ProvenancePath',
    # Query & validation
    'ProvenanceQuery',
    'ValidationIssue',
    'ProvenanceValidator',
    # Invalidation
    'InvalidationRecord',
    'ProvenanceInvalidator',
    # Explanation & serialization
    'ProvenanceExplainer',
    'ProvenanceSerializer',
    # Merge & stats
    'ProvenanceMerger',
    'ProvenanceStatistics',
    # Archive & cycle detection
    'ProvenanceArchive',
    'CircularReasoningDetector',
]

# copilot: shared-core marker for future LLM orchestration.
