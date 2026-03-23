"""Aliasing as shared support — theory2.tex Ch17, §2 — Aliasing as Shared Support.

This module implements the alias-analysis layer of the JuGeo Python runtime
heap model.  It is the second section of Ch17, covering the formal treatment
of aliasing as *shared support* between heap sections.

**Copilot integration note**: this module is part of the JuGeo copilot
integration layer.  The :class:`AliasPartitioner` builds alias partitions that
feed into the judgment machinery; the :class:`SupportOverlapChecker` connects
the alias analysis to the sheaf-theoretic support infrastructure.

Theoretical background (theory2.tex Ch17 §17.5):

* Two references ``r1`` and ``r2`` *alias* if and only if the sections they
  denote share a patch key — i.e. ``support(r1) ∩ support(r2) ≠ ∅``.
* Alias equivalence classes partition the reference space into disjoint groups
  of mutually-aliased references.
* The alias partition is represented as a union-find forest; each equivalence
  class is reified as an :class:`~jugeo.python_runtime.heap_aliasing.models.AliasPartition`.
* An :class:`AliasGraph` stores the alias relation as a graph for downstream
  traversals (reachability, component extraction).
* :class:`AliasDetector` detects aliases from live Python objects using the
  ``is`` operator, container membership, and function argument identity.
* :class:`AliasSetTracker` maintains a history of alias changes over time,
  enabling temporal diff queries.

The module is deliberately self-contained: it imports only the models from
:mod:`jugeo.python_runtime.heap_aliasing.models` and standard JuGeo geometry /
judgment primitives.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentBuilder,
    JudgmentStatus,
    ProvenanceSource,
    TrustLevel,
)
from jugeo.python_runtime.heap_aliasing.models import (
    AliasEdge,
    AliasPartition,
    HeapObject,
    HeapSection,
    IdentityCoordinate,
    ObjectKind,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum number of all-pairs comparisons before issuing a warning.
ALL_PAIRS_WARN_THRESHOLD: int = 1024

#: Default edge kind for directly-detected aliases.
DIRECT_ALIAS_KIND: str = "alias"

#: Edge kind for aliases detected through container elements.
CONTAINER_ALIAS_KIND: str = "container_alias"

#: Edge kind for aliases detected through function arguments.
ARGUMENT_ALIAS_KIND: str = "argument_alias"

#: Edge kind for aliases detected via function return values.
RETURN_ALIAS_KIND: str = "return_alias"

#: Sentinel value for unknown timestamps.
UNKNOWN_TIMESTAMP: float = 0.0


def _new_partition_id() -> str:
    """Return a fresh unique partition identifier string."""
    return f"part_{uuid.uuid4().hex[:10]}"


def _key_of(obj: object) -> str:
    """Return the canonical heap key for a live Python object.

    Parameters:
        obj: Any live Python object.

    Returns:
        A string ``"id:<id(obj)>"``.
    """
    return f"id:{id(obj)}"


# ---------------------------------------------------------------------------
# AliasPartitioner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasPartitioner:
    """Partition reference keys into alias equivalence classes using union-find.

    Implements a standard union-find (disjoint-set) forest with path
    compression and union by rank.  Each equivalence class is reified as an
    :class:`~jugeo.python_runtime.heap_aliasing.models.AliasPartition`
    (theory2.tex Ch17 §17.5).

    The partitioner also exposes a :meth:`build_alias_judgment` helper that
    emits a :class:`~jugeo.judgments.judgment_terms.Judgment` summarising the
    alias class, bridging the runtime analysis with the copilot proof layer.

    Attributes:
        _parent: Maps each key to its parent in the union-find forest.
        _rank: Maps each key to its rank (used for union-by-rank).
        _partitions: Maps ``partition_id`` → :class:`AliasPartition`.
        _key_to_partition: Maps ``member_key`` → ``partition_id``.

    Examples:
        >>> ap = AliasPartitioner()
        >>> ap.add_reference("id:1")
        >>> ap.add_reference("id:2")
        >>> ap.add_alias("id:1", "id:2")
        >>> ap.are_aliases("id:1", "id:2")
        True
    """

    _parent: dict[str, str] = field(default_factory=dict)
    _rank: dict[str, int] = field(default_factory=dict)
    _partitions: dict[str, AliasPartition] = field(default_factory=dict)
    _key_to_partition: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Union-find primitives
    # ------------------------------------------------------------------

    def add_reference(self, key: str) -> None:
        """Add *key* as a singleton reference in its own alias class.

        If *key* is already present, this is a no-op.

        Parameters:
            key: Canonical reference key (e.g. ``"id:12345"``).
        """
        if key in self._parent:
            return
        self._parent[key] = key
        self._rank[key] = 0
        partition_id = _new_partition_id()
        partition = AliasPartition(
            partition_id=partition_id,
            members=frozenset({key}),
            representative=key,
            edges=(),
        )
        self._partitions[partition_id] = partition
        self._key_to_partition[key] = partition_id
        logger.debug("AliasPartitioner: added singleton key=%s", key)

    def find_root(self, key: str) -> str:
        """Return the root representative of *key* with path compression.

        Parameters:
            key: A key that has been added via :meth:`add_reference`.

        Returns:
            The root representative of the equivalence class containing *key*.

        Raises:
            KeyError: If *key* was never added.
        """
        if key not in self._parent:
            raise KeyError(f"AliasPartitioner: key not tracked: {key!r}")
        # Iterative path compression
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression pass
        current = key
        while self._parent[current] != root:
            next_node = self._parent[current]
            self._parent[current] = root
            current = next_node
        return root

    def union(self, key1: str, key2: str) -> bool:
        """Union the equivalence classes of *key1* and *key2*.

        Uses union by rank to keep the forest balanced.

        Parameters:
            key1: First reference key.
            key2: Second reference key.

        Returns:
            ``True`` if the two keys were in different classes (and were
            therefore merged), ``False`` if they were already aliases.

        Raises:
            KeyError: If either key was never added.
        """
        root1 = self.find_root(key1)
        root2 = self.find_root(key2)
        if root1 == root2:
            return False  # already in the same class
        rank1 = self._rank.get(root1, 0)
        rank2 = self._rank.get(root2, 0)
        if rank1 < rank2:
            root1, root2 = root2, root1
        # root1 becomes the new root
        self._parent[root2] = root1
        if rank1 == rank2:
            self._rank[root1] = rank1 + 1
        return True

    def add_alias(self, key1: str, key2: str, evidence: str = "") -> None:
        """Record that *key1* and *key2* are aliases.

        Ensures both keys are present (adding them if necessary), then unions
        their equivalence classes and rebuilds the affected partitions.

        Parameters:
            key1: First reference key.
            key2: Second reference key.
            evidence: Optional human-readable explanation of why the alias
                was detected.
        """
        if key1 not in self._parent:
            self.add_reference(key1)
        if key2 not in self._parent:
            self.add_reference(key2)
        merged = self.union(key1, key2)
        if merged:
            logger.debug(
                "AliasPartitioner: merged keys %s and %s (evidence=%r)",
                key1, key2, evidence,
            )
            self.rebuild_partitions()

    def merge_partitions(
        self,
        p1_id: str,
        p2_id: str,
    ) -> AliasPartition | None:
        """Merge two named partitions into one and return the result.

        Both partitions are replaced in ``_partitions`` by the merged
        partition.  Members in ``_key_to_partition`` are updated accordingly.

        Parameters:
            p1_id: Identifier of the first partition.
            p2_id: Identifier of the second partition.

        Returns:
            The merged :class:`AliasPartition`, or ``None`` if either id is
            unknown.
        """
        p1 = self._partitions.get(p1_id)
        p2 = self._partitions.get(p2_id)
        if p1 is None or p2 is None:
            return None
        merged = p1.merge(p2)
        # Remove old partitions
        del self._partitions[p1_id]
        del self._partitions[p2_id]
        # Store merged
        self._partitions[merged.partition_id] = merged
        for member in merged.members:
            self._key_to_partition[member] = merged.partition_id
        logger.debug(
            "AliasPartitioner: merged partitions %s + %s → %s",
            p1_id, p2_id, merged.partition_id,
        )
        return merged

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def get_partition(self, key: str) -> AliasPartition | None:
        """Return the :class:`AliasPartition` containing *key*, or ``None``.

        Parameters:
            key: A reference key.

        Returns:
            The :class:`AliasPartition` if found, else ``None``.
        """
        partition_id = self._key_to_partition.get(key)
        if partition_id is None:
            return None
        return self._partitions.get(partition_id)

    def all_partitions(self) -> list[AliasPartition]:
        """Return all current alias partitions.

        Returns:
            A fresh list of every :class:`AliasPartition`.
        """
        return list(self._partitions.values())

    def are_aliases(self, key1: str, key2: str) -> bool:
        """Return ``True`` when *key1* and *key2* are in the same alias class.

        Parameters:
            key1: First reference key.
            key2: Second reference key.

        Returns:
            ``True`` iff both keys exist and share a union-find root.
        """
        if key1 not in self._parent or key2 not in self._parent:
            return False
        return self.find_root(key1) == self.find_root(key2)

    def singleton_count(self) -> int:
        """Return the number of singleton alias classes.

        Returns:
            Integer count of alias classes with exactly one member.
        """
        return sum(1 for p in self._partitions.values() if p.size() == 1)

    def non_singleton_count(self) -> int:
        """Return the number of non-singleton alias classes.

        Returns:
            Integer count of alias classes with two or more members.
        """
        return sum(1 for p in self._partitions.values() if p.size() > 1)

    def all_keys(self) -> frozenset[str]:
        """Return all keys that have been added to the partitioner.

        Returns:
            ``frozenset`` of all key strings.
        """
        return frozenset(self._parent)

    # ------------------------------------------------------------------
    # Partition rebuild
    # ------------------------------------------------------------------

    def rebuild_partitions(self) -> None:
        """Rebuild the ``_partitions`` and ``_key_to_partition`` maps.

        Called after union operations to ensure the partition dictionaries
        reflect the current union-find structure.  Groups keys by their root
        representative, then constructs a new :class:`AliasPartition` for
        each group.
        """
        # Collect keys by root
        root_to_keys: dict[str, list[str]] = {}
        for key in self._parent:
            root = self.find_root(key)
            root_to_keys.setdefault(root, []).append(key)
        # Rebuild maps
        self._partitions.clear()
        self._key_to_partition.clear()
        for root, members in root_to_keys.items():
            partition_id = _new_partition_id()
            partition = AliasPartition(
                partition_id=partition_id,
                members=frozenset(members),
                representative=root,
                edges=(),
            )
            self._partitions[partition_id] = partition
            for m in members:
                self._key_to_partition[m] = partition_id

    # ------------------------------------------------------------------
    # Judgment emission
    # ------------------------------------------------------------------

    def build_alias_judgment(
        self,
        partition: AliasPartition,
    ) -> Judgment:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` describing
        an alias equivalence class.

        The formula encodes the alias class as
        ``"alias_class({member1, member2, ...})"`` and is emitted at a
        coordinate derived from the canonical representative key.

        Parameters:
            partition: The :class:`AliasPartition` to summarise.

        Returns:
            A :class:`~jugeo.judgments.judgment_terms.Judgment` asserting that
            all members of *partition* alias one another.

        Raises:
            ValueError: If the partition has no members.
        """
        if not partition.members:
            raise ValueError("build_alias_judgment: partition has no members")
        rep_key = partition.representative or min(partition.members)
        # Parse object_id from rep_key ("id:<N>")
        try:
            raw_id = rep_key.split(":")[1]
        except IndexError:
            raw_id = rep_key
        coord = CoordinateObject(
            components=(raw_id, "AliasClass"),
            kind=CoordinateKind.REGION,
            support_labels=partition.members,
        )
        members_str = ", ".join(sorted(partition.members))
        formula = f"alias_class({{{members_str}}})"
        builder = JudgmentBuilder()
        return (
            builder
            .at(coord)
            .claiming_formula(formula)
            .of_type_named("AliasPartition")
            .with_trust_level(TrustLevel.RUNTIME_WITNESSED)
            .with_status(JudgmentStatus.VERIFIED)
            .from_source(ProvenanceSource.RUNTIME)
            .build()
        )


# ---------------------------------------------------------------------------
# AliasDetector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasDetector:
    """Detects alias relationships among live Python objects.

    Uses the Python ``is`` operator and container-element identity to discover
    aliasing.  Detected edges are accumulated in ``_detected_edges`` and can
    be used to seed an :class:`AliasPartitioner`.

    Attributes:
        _detected_edges: All :class:`AliasEdge` objects found so far.
        _registry: Maps ``id(obj)`` → canonical key string to avoid recomputing
            keys for the same objects.

    Examples:
        >>> a = [1, 2, 3]
        >>> detector = AliasDetector()
        >>> edge = detector.detect_direct_alias(a, a)
        >>> edge is not None
        True
    """

    _detected_edges: list[AliasEdge] = field(default_factory=list)
    _registry: dict[int, str] = field(default_factory=dict)

    def _get_key(self, obj: object) -> str:
        """Return (and cache) the canonical key for *obj*.

        Parameters:
            obj: Any live Python object.

        Returns:
            String ``"id:<id(obj)>"``.
        """
        oid = id(obj)
        if oid not in self._registry:
            self._registry[oid] = f"id:{oid}"
        return self._registry[oid]

    # ------------------------------------------------------------------
    # Individual detection methods
    # ------------------------------------------------------------------

    def detect_direct_alias(
        self,
        obj1: object,
        obj2: object,
    ) -> AliasEdge | None:
        """Detect a direct alias using the ``is`` operator.

        Parameters:
            obj1: First Python object.
            obj2: Second Python object.

        Returns:
            An :class:`AliasEdge` when ``obj1 is obj2``, else ``None``.
        """
        if obj1 is not obj2:
            return None
        edge = AliasEdge(
            source_id=self._get_key(obj1),
            target_id=self._get_key(obj2),
            edge_kind=DIRECT_ALIAS_KIND,
            label="direct `is` identity check",
        )
        self._detected_edges.append(edge)
        logger.debug(
            "AliasDetector: direct alias detected %s → %s",
            edge.source_id, edge.target_id,
        )
        return edge

    def detect_through_container(
        self,
        container: object,
        key1: object,
        key2: object,
    ) -> AliasEdge | None:
        """Detect an alias through shared container membership.

        Checks whether ``container[key1] is container[key2]`` for mapping-like
        containers, or whether two sequence indices refer to the same object.

        Parameters:
            container: A dict-like or list-like container object.
            key1: First key / index.
            key2: Second key / index.

        Returns:
            An :class:`AliasEdge` if an alias is found, else ``None``.
        """
        try:
            if isinstance(container, dict):
                val1 = container.get(key1)  # type: ignore[arg-type]
                val2 = container.get(key2)  # type: ignore[arg-type]
            elif isinstance(container, (list, tuple)):
                if not (isinstance(key1, int) and isinstance(key2, int)):
                    return None
                val1 = container[key1]  # type: ignore[index]
                val2 = container[key2]  # type: ignore[index]
            else:
                return None
            if val1 is None or val2 is None:
                return None
            if val1 is not val2:
                return None
        except (KeyError, IndexError, TypeError):
            return None
        edge = AliasEdge(
            source_id=self._get_key(val1),
            target_id=self._get_key(val2),
            edge_kind=CONTAINER_ALIAS_KIND,
            label=f"container alias via {type(container).__qualname__}[{key1!r}] is [{key2!r}]",
        )
        self._detected_edges.append(edge)
        logger.debug(
            "AliasDetector: container alias %s → %s",
            edge.source_id, edge.target_id,
        )
        return edge

    def detect_through_return(
        self,
        fn_result: object,
        original: object,
    ) -> AliasEdge | None:
        """Detect an alias between a function's return value and a known object.

        Parameters:
            fn_result: The object returned by a function.
            original: A reference object to compare against.

        Returns:
            An :class:`AliasEdge` when ``fn_result is original``, else ``None``.
        """
        if fn_result is not original:
            return None
        edge = AliasEdge(
            source_id=self._get_key(fn_result),
            target_id=self._get_key(original),
            edge_kind=RETURN_ALIAS_KIND,
            label="function return value aliases original object",
        )
        self._detected_edges.append(edge)
        logger.debug(
            "AliasDetector: return alias %s → %s",
            edge.source_id, edge.target_id,
        )
        return edge

    def detect_through_argument(
        self,
        arg: object,
        param: object,
    ) -> AliasEdge | None:
        """Detect an alias between a call-site argument and a parameter object.

        In CPython, arguments are passed by object reference; ``arg is param``
        is true when the caller and callee refer to the same heap object.

        Parameters:
            arg: The argument object at the call site.
            param: The parameter object inside the function body.

        Returns:
            An :class:`AliasEdge` when ``arg is param``, else ``None``.
        """
        if arg is not param:
            return None
        edge = AliasEdge(
            source_id=self._get_key(arg),
            target_id=self._get_key(param),
            edge_kind=ARGUMENT_ALIAS_KIND,
            label="call-site argument aliases function parameter",
        )
        self._detected_edges.append(edge)
        return edge

    # ------------------------------------------------------------------
    # Bulk detection
    # ------------------------------------------------------------------

    def build_alias_edges(self, objects: list[object]) -> list[AliasEdge]:
        """Run all-pairs alias detection across *objects*.

        Compares every pair ``(obj_i, obj_j)`` with ``i < j`` using the ``is``
        operator.  Issues a warning when the number of pairs exceeds
        :data:`ALL_PAIRS_WARN_THRESHOLD`.

        Parameters:
            objects: List of live Python objects to analyse.

        Returns:
            List of :class:`AliasEdge` objects for detected aliases.
        """
        n = len(objects)
        n_pairs = n * (n - 1) // 2
        if n_pairs > ALL_PAIRS_WARN_THRESHOLD:
            logger.warning(
                "AliasDetector.build_alias_edges: %d pairs — this may be slow", n_pairs
            )
        new_edges: list[AliasEdge] = []
        for i in range(n):
            for j in range(i + 1, n):
                edge = self.detect_direct_alias(objects[i], objects[j])
                if edge is not None:
                    new_edges.append(edge)
        return new_edges

    def find_all_aliases(self, objects: list[object]) -> list[AliasPartition]:
        """Detect all aliases among *objects* and return the alias partitions.

        Builds edges via :meth:`build_alias_edges`, seeds an
        :class:`AliasPartitioner`, and returns all non-trivial (size > 1) and
        trivial partitions.

        Parameters:
            objects: List of live Python objects to analyse.

        Returns:
            A list of :class:`AliasPartition` objects covering all input keys.
        """
        self.build_alias_edges(objects)
        partitioner = AliasPartitioner()
        for obj in objects:
            partitioner.add_reference(self._get_key(obj))
        for edge in self._detected_edges:
            partitioner.add_alias(edge.source_id, edge.target_id)
        return partitioner.all_partitions()

    def build_alias_graph(
        self,
        partitioner: AliasPartitioner,
    ) -> AliasGraph:
        """Construct an :class:`AliasGraph` from the partitioner state.

        Parameters:
            partitioner: A populated :class:`AliasPartitioner`.

        Returns:
            An :class:`AliasGraph` with nodes for every key and edges for
            every detected alias.
        """
        graph = AliasGraph()
        for key in partitioner.all_keys():
            graph.add_node(key)
        for edge in self._detected_edges:
            graph.add_edge(edge)
        return graph

    def reset(self) -> None:
        """Clear all detected edges and the registry cache."""
        self._detected_edges.clear()
        self._registry.clear()
        logger.debug("AliasDetector: reset")


# ---------------------------------------------------------------------------
# AliasGraph
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasGraph:
    """Graph representation of the alias relation.

    Nodes are reference key strings; edges are :class:`AliasEdge` objects.
    Supports BFS-based connected-component extraction and reachability queries.

    Attributes:
        _nodes: Set of all node keys in the graph.
        _edges: Ordered list of :class:`AliasEdge` objects.
        _adj: Adjacency list mapping each key to its neighbours.

    Examples:
        >>> g = AliasGraph()
        >>> g.add_node("id:1")
        >>> g.add_node("id:2")
        >>> g.add_edge(AliasEdge(source_id="id:1", target_id="id:2"))
        >>> g.node_count()
        2
        >>> g.edge_count()
        1
    """

    _nodes: set[str] = field(default_factory=set)
    _edges: list[AliasEdge] = field(default_factory=list)
    _adj: dict[str, list[str]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, key: str) -> None:
        """Add *key* as a node in the graph.

        Parameters:
            key: Reference key string.
        """
        self._nodes.add(key)
        self._adj.setdefault(key, [])

    def add_edge(self, edge: AliasEdge) -> None:
        """Add *edge* to the graph, adding nodes as needed.

        The graph is treated as undirected: the edge is added in both
        directions (``source → target`` and ``target → source``).

        Parameters:
            edge: The :class:`AliasEdge` to add.
        """
        self.add_node(edge.source_id)
        self.add_node(edge.target_id)
        self._edges.append(edge)
        adj_src = self._adj.setdefault(edge.source_id, [])
        if edge.target_id not in adj_src:
            adj_src.append(edge.target_id)
        adj_tgt = self._adj.setdefault(edge.target_id, [])
        if edge.source_id not in adj_tgt:
            adj_tgt.append(edge.source_id)

    def remove_edge(self, source: str, target: str) -> bool:
        """Remove the first edge between *source* and *target*.

        Removes the edge from ``_edges`` and updates the adjacency list in
        both directions.

        Parameters:
            source: Source node key.
            target: Target node key.

        Returns:
            ``True`` if an edge was found and removed, ``False`` otherwise.
        """
        for idx, edge in enumerate(self._edges):
            if (edge.source_id == source and edge.target_id == target) or (
                edge.source_id == target and edge.target_id == source
            ):
                del self._edges[idx]
                # Clean adjacency list
                src_adj = self._adj.get(source, [])
                if target in src_adj:
                    src_adj.remove(target)
                tgt_adj = self._adj.get(target, [])
                if source in tgt_adj:
                    tgt_adj.remove(source)
                return True
        return False

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def neighbors(self, key: str) -> list[str]:
        """Return the adjacency list for *key*.

        Parameters:
            key: A node key string.

        Returns:
            List of neighbouring key strings (may be empty).
        """
        return list(self._adj.get(key, []))

    def connected_components(self) -> list[frozenset[str]]:
        """Return all connected components via BFS.

        Returns:
            A list of ``frozenset[str]`` where each frozenset is a connected
            component of the graph.
        """
        visited: set[str] = set()
        components: list[frozenset[str]] = []
        for start in self._nodes:
            if start in visited:
                continue
            # BFS from start
            component: set[str] = set()
            queue: deque[str] = deque([start])
            while queue:
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)
                for nb in self._adj.get(node, []):
                    if nb not in visited:
                        queue.append(nb)
            components.append(frozenset(component))
        return components

    def has_path(self, source: str, target: str) -> bool:
        """Return ``True`` when *source* can reach *target* via graph edges.

        Uses BFS.  Returns ``True`` immediately if ``source == target``.

        Parameters:
            source: Starting node key.
            target: Goal node key.

        Returns:
            Boolean reachability result.
        """
        if source == target:
            return True
        if source not in self._nodes or target not in self._nodes:
            return False
        visited: set[str] = set()
        queue: deque[str] = deque([source])
        while queue:
            node = queue.popleft()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            for nb in self._adj.get(node, []):
                if nb not in visited:
                    queue.append(nb)
        return False

    def all_edges(self) -> list[AliasEdge]:
        """Return all edges in the graph.

        Returns:
            A copy of the internal edges list.
        """
        return list(self._edges)

    def node_count(self) -> int:
        """Return the number of nodes.

        Returns:
            Integer count.
        """
        return len(self._nodes)

    def edge_count(self) -> int:
        """Return the number of edges.

        Returns:
            Integer count.
        """
        return len(self._edges)

    def serialize(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dictionary.

        Returns:
            Dictionary with ``nodes`` (sorted list) and ``edges`` (list of
            serialized :class:`AliasEdge` dicts).
        """
        return {
            "nodes": sorted(self._nodes),
            "edges": [e.serialize() for e in self._edges],
        }


# ---------------------------------------------------------------------------
# SupportOverlapChecker
# ---------------------------------------------------------------------------


class SupportOverlapChecker:
    """Checks for shared support (aliasing) between :class:`HeapSection` objects.

    In the sheaf-theoretic model (theory2.tex Ch17 §17.5), two sections alias
    when their support regions overlap — i.e. they share at least one object
    in their ``object_ids()`` sets.

    This class is *not* a dataclass; it uses plain ``__init__`` and instance
    attributes.

    Examples:
        >>> checker = SupportOverlapChecker()
        >>> s1 = HeapSection(section_id="s1", objects=(), label="")
        >>> s2 = HeapSection(section_id="s2", objects=(), label="")
        >>> checker.check_overlap(s1, s2)
        False
    """

    def __init__(self) -> None:
        """Initialise the checker with an empty overlap cache."""
        self._cache: dict[tuple[str, str], bool] = {}
        self._judgment_builder: JudgmentBuilder = JudgmentBuilder()

    # ------------------------------------------------------------------
    # Core overlap logic
    # ------------------------------------------------------------------

    def check_overlap(
        self,
        s1: HeapSection,
        s2: HeapSection,
    ) -> bool:
        """Return ``True`` when sections *s1* and *s2* share a common object.

        Implements the formal alias criterion: two sections overlap iff their
        ``object_ids()`` intersect.

        Parameters:
            s1: First :class:`HeapSection`.
            s2: Second :class:`HeapSection`.

        Returns:
            ``True`` when ``s1.object_ids() ∩ s2.object_ids() ≠ ∅``.
        """
        cache_key = (s1.section_id, s2.section_id)
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = bool(s1.object_ids() & s2.object_ids())
        self._cache[cache_key] = result
        return result

    def find_all_overlaps(
        self,
        sections: list[HeapSection],
    ) -> list[tuple[HeapSection, HeapSection]]:
        """Return all pairs of sections that share support.

        Parameters:
            sections: List of :class:`HeapSection` objects to compare.

        Returns:
            A list of ``(s_i, s_j)`` pairs (with ``i < j``) where the two
            sections overlap.
        """
        overlapping: list[tuple[HeapSection, HeapSection]] = []
        n = len(sections)
        for i in range(n):
            for j in range(i + 1, n):
                if self.check_overlap(sections[i], sections[j]):
                    overlapping.append((sections[i], sections[j]))
        return overlapping

    def compute_overlap_matrix(
        self,
        sections: list[HeapSection],
    ) -> dict[tuple[str, str], bool]:
        """Compute the full pairwise overlap matrix.

        Parameters:
            sections: List of :class:`HeapSection` objects.

        Returns:
            A dict mapping ``(section_id_i, section_id_j)`` → ``bool`` for all
            ordered pairs ``(i, j)`` with ``i <= j``.
        """
        matrix: dict[tuple[str, str], bool] = {}
        n = len(sections)
        for i in range(n):
            for j in range(i, n):
                key = (sections[i].section_id, sections[j].section_id)
                if i == j:
                    matrix[key] = True
                else:
                    matrix[key] = self.check_overlap(sections[i], sections[j])
        return matrix

    def report_aliases(
        self,
        sections: list[HeapSection],
    ) -> list[AliasEdge]:
        """Return :class:`AliasEdge` objects for every pair of overlapping sections.

        Parameters:
            sections: List of :class:`HeapSection` objects to analyse.

        Returns:
            A list of :class:`AliasEdge` objects, one per overlapping pair.
        """
        edges: list[AliasEdge] = []
        for s1, s2 in self.find_all_overlaps(sections):
            edge = AliasEdge(
                source_id=s1.section_id,
                target_id=s2.section_id,
                edge_kind=DIRECT_ALIAS_KIND,
                label=f"shared support: {sorted(s1.object_ids() & s2.object_ids())}",
            )
            edges.append(edge)
        return edges

    def build_overlap_judgment(
        self,
        s1: HeapSection,
        s2: HeapSection,
    ) -> Judgment:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` asserting
        that *s1* and *s2* share support.

        Parameters:
            s1: First :class:`HeapSection`.
            s2: Second :class:`HeapSection`.

        Returns:
            A :class:`~jugeo.judgments.judgment_terms.Judgment` with formula
            ``"overlap(s1_id, s2_id)"``.

        Raises:
            ValueError: If the :class:`~jugeo.judgments.judgment_terms.JudgmentBuilder`
                fails validation.
        """
        coord = CoordinateObject(
            components=(s1.section_id, s2.section_id),
            kind=CoordinateKind.REGION,
            support_labels=frozenset({s1.section_id, s2.section_id}),
        )
        formula = f"overlap({s1.section_id!r}, {s2.section_id!r})"
        self._judgment_builder.reset()
        return (
            self._judgment_builder
            .at(coord)
            .claiming_formula(formula)
            .of_type_named("SupportOverlap")
            .with_trust_level(TrustLevel.RUNTIME_WITNESSED)
            .with_status(JudgmentStatus.VERIFIED)
            .from_source(ProvenanceSource.RUNTIME)
            .build()
        )


# ---------------------------------------------------------------------------
# AliasSetTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasSetTracker:
    """Tracks the history of alias set changes over time.

    Records each alias creation and deletion event with a timestamp, enabling
    temporal diff queries that answer "which alias classes existed between time
    *t1* and *t2*?".

    Attributes:
        _history: Ordered list of event records (dicts with at least
            ``event``, ``key1``/``key``, and ``timestamp`` keys).
        _current_partitions: Maps ``partition_id`` → current
            :class:`AliasPartition`.

    Examples:
        >>> tracker = AliasSetTracker()
        >>> tracker.record_alias("id:1", "id:2")
        >>> tracker.count_events()
        1
    """

    _history: list[dict[str, Any]] = field(default_factory=list)
    _current_partitions: dict[str, AliasPartition] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record_alias(
        self,
        key1: str,
        key2: str,
        timestamp: float | None = None,
    ) -> None:
        """Record that *key1* and *key2* have been detected as aliases.

        Updates ``_current_partitions`` using a micro-partitioner and appends
        an event record to the history.

        Parameters:
            key1: First reference key.
            key2: Second reference key.
            timestamp: POSIX timestamp; defaults to :func:`time.time`.
        """
        ts = timestamp if timestamp is not None else time.time()
        event: dict[str, Any] = {
            "event": "alias",
            "key1": key1,
            "key2": key2,
            "timestamp": ts,
        }
        self._history.append(event)
        # Merge or create partition
        pid1 = self._find_partition_id(key1)
        pid2 = self._find_partition_id(key2)
        if pid1 is not None and pid2 is not None and pid1 == pid2:
            # Already in same partition — no-op
            return
        if pid1 is None and pid2 is None:
            new_id = _new_partition_id()
            partition = AliasPartition(
                partition_id=new_id,
                members=frozenset({key1, key2}),
                representative=min(key1, key2),
                edges=(),
            )
            self._current_partitions[new_id] = partition
        elif pid1 is not None and pid2 is None:
            old = self._current_partitions[pid1]
            updated = replace(old, members=old.members | {key2})
            self._current_partitions[pid1] = updated
        elif pid1 is None and pid2 is not None:
            old = self._current_partitions[pid2]
            updated = replace(old, members=old.members | {key1})
            self._current_partitions[pid2] = updated
        else:
            # Both in different partitions — merge
            assert pid1 is not None and pid2 is not None
            p1 = self._current_partitions.pop(pid1)
            p2 = self._current_partitions.pop(pid2)
            merged = p1.merge(p2)
            self._current_partitions[merged.partition_id] = merged
        logger.debug(
            "AliasSetTracker: alias event %s <-> %s at %.4f", key1, key2, ts
        )

    def record_unalias(
        self,
        key: str,
        timestamp: float | None = None,
    ) -> None:
        """Record that the object identified by *key* has been deallocated.

        Removes *key* from its partition.  If the partition becomes empty or a
        singleton, it is retained (singletons are valid states).

        Parameters:
            key: The reference key being removed.
            timestamp: POSIX timestamp; defaults to :func:`time.time`.
        """
        ts = timestamp if timestamp is not None else time.time()
        event: dict[str, Any] = {
            "event": "unalias",
            "key": key,
            "timestamp": ts,
        }
        self._history.append(event)
        pid = self._find_partition_id(key)
        if pid is None:
            return
        old = self._current_partitions[pid]
        new_members = old.members - {key}
        if not new_members:
            del self._current_partitions[pid]
        else:
            new_rep = min(new_members)
            updated = replace(old, members=new_members, representative=new_rep)
            self._current_partitions[pid] = updated
        logger.debug("AliasSetTracker: unalias event key=%s at %.4f", key, ts)

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, AliasPartition]:
        """Return a copy of the current partition map.

        Returns:
            A shallow copy of ``_current_partitions``.
        """
        return dict(self._current_partitions)

    def diff(
        self,
        t1: float,
        t2: float,
    ) -> dict[str, Any]:
        """Compute which alias events occurred between *t1* and *t2*.

        Scans the history and collects alias/unalias events whose timestamps
        fall in ``[t1, t2)``.

        Parameters:
            t1: Start of the time window (inclusive, POSIX seconds).
            t2: End of the time window (exclusive, POSIX seconds).

        Returns:
            Dictionary with keys ``"alias_events"`` and ``"unalias_events"``,
            each a list of event records.
        """
        alias_events: list[dict[str, Any]] = []
        unalias_events: list[dict[str, Any]] = []
        for ev in self._history:
            ts = ev.get("timestamp", 0.0)
            if t1 <= ts < t2:
                if ev.get("event") == "alias":
                    alias_events.append(ev)
                elif ev.get("event") == "unalias":
                    unalias_events.append(ev)
        return {
            "alias_events": alias_events,
            "unalias_events": unalias_events,
        }

    def history_for(self, key: str) -> list[dict[str, Any]]:
        """Return all history events involving *key*.

        Parameters:
            key: The reference key to filter by.

        Returns:
            List of event records that mention *key*.
        """
        result: list[dict[str, Any]] = []
        for ev in self._history:
            if ev.get("key1") == key or ev.get("key2") == key or ev.get("key") == key:
                result.append(ev)
        return result

    def count_events(self) -> int:
        """Return the total number of recorded events.

        Returns:
            Integer count.
        """
        return len(self._history)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_partition_id(self, key: str) -> str | None:
        """Return the partition id containing *key*, or ``None``.

        Parameters:
            key: Reference key to search for.

        Returns:
            Partition id string, or ``None`` if *key* is not in any partition.
        """
        for pid, partition in self._current_partitions.items():
            if key in partition.members:
                return pid
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "ALL_PAIRS_WARN_THRESHOLD",
    "DIRECT_ALIAS_KIND",
    "CONTAINER_ALIAS_KIND",
    "ARGUMENT_ALIAS_KIND",
    "RETURN_ALIAS_KIND",
    # Helper functions
    "_key_of",
    # Classes
    "AliasPartitioner",
    "AliasDetector",
    "AliasGraph",
    "SupportOverlapChecker",
    "AliasSetTracker",
]
