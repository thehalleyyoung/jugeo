"""Heap aliasing algorithms for the JuGeo Python runtime.

Implements the core algorithmic machinery described in *theory2.tex Ch17 —
Algorithms*, covering heap analysis, alias detection, mutation flow propagation,
and snapshot diffing.  These algorithms are consumed by the integration layer
and by the copilot integration pipeline that threads runtime evidence back into
the JuGeo judgment lattice.

Design notes
------------
* All analysis is performed on already-materialised :class:`HeapObject` graphs;
  the algorithms are *pure* in the sense that they never reach into the live
  Python heap themselves — that is the job of the snapshot machinery.
* Mutable state holders (e.g. :class:`HeapAnalyzer`) use ``@dataclass(slots=True)``
  so that accidental attribute creation is caught at definition time.
* Frozen dataclass values are updated via ``replace(obj, field=val)``; mutable
  dataclasses mutate ``self`` in place.
* Union-Find is provided as a plain class (:class:`UnionFindAlgorithm`) because
  it needs dynamic ``__init__`` logic that does not compose cleanly with the
  dataclass protocol.

References
----------
* theory2.tex Ch17 — Algorithms (internal).
* copilot integration guide (internal).
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from jugeo.geometry.site import (
    CoordinateKind,
    CoordinateObject,
    Coordinate,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentBuilder,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.python_runtime.heap_aliasing.models import (
    AliasEdge,
    AliasPartition,
    HeapObject,
    HeapSection,
    HeapSnapshot,
    MutationEvent,
    MutationPatch,
    ObjectKind,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HeapAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapAnalyzer:
    """High-level heap analysis façade.

    Converts raw Python objects into :class:`HeapObject` graphs, builds an
    in-memory adjacency index, detects reference cycles, and computes alias
    partitions.  Results are packaged as plain dicts for downstream use by
    integration and theorem-checking layers.

    Attributes
    ----------
    _object_index:
        Mapping from integer ``object_id`` to :class:`HeapObject` populated by
        :meth:`build_object_index`.
    _alias_cache:
        Mapping from string object ID to list of aliased IDs, populated lazily
        by :meth:`find_all_aliases`.
    _analysis_log:
        Timestamped log entries produced by :meth:`log`.

    Examples
    --------
    >>> analyzer = HeapAnalyzer()
    >>> result = analyzer.analyze([1, "hello", [1, 2]])
    >>> isinstance(result["object_count"], int)
    True
    """

    _object_index: dict[int, HeapObject] = field(default_factory=dict)
    _alias_cache: dict[str, list[str]] = field(default_factory=dict)
    _analysis_log: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def analyze(self, objects: list[object]) -> dict[str, Any]:
        """Analyse a list of Python objects and return a structured result dict.

        Converts each object to a :class:`HeapObject`, builds an object index
        and heap graph, detects reference cycles, identifies alias partitions,
        and locates dangling references.

        Parameters
        ----------
        objects:
            Arbitrary Python objects to analyse.

        Returns
        -------
        dict[str, Any]
            Keys: ``"objects"``, ``"aliases"``, ``"cycles"``, ``"heap_graph"``,
            ``"dangling_refs"``, ``"object_count"``, ``"alias_count"``,
            ``"cycle_count"``.

        Raises
        ------
        TypeError
            If *objects* is not a list.

        Examples
        --------
        >>> HeapAnalyzer().analyze([42, "text"])["object_count"]
        2
        """
        if not isinstance(objects, list):
            raise TypeError(f"objects must be a list, got {type(objects).__name__}")

        self.log(f"analyze: starting with {len(objects)} raw objects")

        heap_objects: list[HeapObject] = []
        seen_ids: set[int] = set()
        for obj in objects:
            oid = id(obj)
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            heap_objects.append(HeapObject.from_object(obj))

        index = self.build_object_index(heap_objects)
        heap_graph = self.build_heap_graph(heap_objects)
        aliases = self.find_all_aliases(heap_objects)
        cycles = self.detect_cycles(heap_graph)
        valid_ids: frozenset[int] = frozenset(index.keys())
        dangling = self.find_dangling_references(heap_objects, valid_ids)

        self.log(
            f"analyze: found {len(heap_objects)} objects, "
            f"{len(aliases)} alias partitions, {len(cycles)} cycles"
        )

        return {
            "objects": [o.serialize() for o in heap_objects],
            "aliases": [a.serialize() for a in aliases],
            "cycles": cycles,
            "heap_graph": heap_graph,
            "dangling_refs": dangling,
            "object_count": len(heap_objects),
            "alias_count": len(aliases),
            "cycle_count": len(cycles),
        }

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def build_heap_graph(self, objects: list[HeapObject]) -> dict[str, list[str]]:
        """Build an adjacency list graph from a collection of heap objects.

        Each node is identified by ``str(object_id)``.  An edge ``u → v`` is
        added whenever object *u* has a field reference whose target ID equals
        *v*'s ``object_id``.

        Parameters
        ----------
        objects:
            List of heap objects to include as nodes.

        Returns
        -------
        dict[str, list[str]]
            Adjacency mapping ``node_id → [neighbour_id, ...]``.

        Examples
        --------
        >>> analyzer = HeapAnalyzer()
        >>> graph = analyzer.build_heap_graph([])
        >>> graph
        {}
        """
        node_ids: set[str] = {str(o.object_id) for o in objects}
        graph: dict[str, list[str]] = {}

        for obj in objects:
            node_key = str(obj.object_id)
            neighbours: list[str] = []
            for _field_name, target_id in obj.field_refs().items():
                t_str = str(target_id)
                if t_str in node_ids and t_str != node_key:
                    neighbours.append(t_str)
            graph[node_key] = neighbours

        return graph

    # ------------------------------------------------------------------
    # Alias detection
    # ------------------------------------------------------------------

    def find_all_aliases(self, objects: list[HeapObject]) -> list[AliasPartition]:
        """Identify alias partitions among a collection of heap objects.

        Two field references *alias* the same target when their
        ``target_object_id`` values are equal.  For each such target that is
        referenced by more than one ``(source_object_id, field_name)`` pair,
        this method creates an :class:`AliasPartition` whose members are the
        *source* objects that share the reference plus the target itself.

        The implementation uses :class:`UnionFindAlgorithm` to accumulate
        equivalence classes across the full object set.

        Parameters
        ----------
        objects:
            Heap objects whose field references will be examined.

        Returns
        -------
        list[AliasPartition]
            One partition per alias equivalence class with two or more members.

        Examples
        --------
        >>> HeapAnalyzer().find_all_aliases([])
        []
        """
        uf = UnionFindAlgorithm()

        # Register every object as its own set.
        for obj in objects:
            uf.make_set(str(obj.object_id))

        # Build a mapping: target_id_str -> list[(source_id_str, field_name)]
        target_to_refs: dict[str, list[tuple[str, str]]] = {}
        for obj in objects:
            src_str = str(obj.object_id)
            for fname, target_id in obj.field_refs().items():
                t_str = str(target_id)
                target_to_refs.setdefault(t_str, []).append((src_str, fname))

        # For each target referenced by multiple sources, union those sources.
        for target_str, refs in target_to_refs.items():
            if len(refs) < 2:
                continue
            first_src = refs[0][0]
            for src_str, _fn in refs[1:]:
                uf.union(first_src, src_str)

        # Collect components with >1 member into AliasPartitions.
        partitions: list[AliasPartition] = []
        for component in uf.all_components():
            if len(component) < 2:
                continue
            rep = min(component)
            edges: list[AliasEdge] = []
            members_list = sorted(component)
            for i, m in enumerate(members_list):
                for m2 in members_list[i + 1 :]:
                    edges.append(AliasEdge(source_id=m, target_id=m2, edge_kind="alias"))
            partition = AliasPartition(
                partition_id=str(uuid.uuid4()),
                members=frozenset(component),
                representative=rep,
                edges=tuple(edges),
            )
            partitions.append(partition)
            # Update cache
            for m in component:
                self._alias_cache[m] = [x for x in component if x != m]

        self.log(f"find_all_aliases: produced {len(partitions)} partitions")
        return partitions

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def detect_cycles(self, heap_graph: dict[str, list[str]]) -> list[list[str]]:
        """Detect reference cycles in a heap object graph using iterative DFS.

        Parameters
        ----------
        heap_graph:
            Adjacency mapping as produced by :meth:`build_heap_graph`.

        Returns
        -------
        list[list[str]]
            Each element is a list of node IDs forming one detected cycle.
            The cycle list starts at the back-edge target and ends at the node
            that re-encountered it.

        Examples
        --------
        >>> analyzer = HeapAnalyzer()
        >>> analyzer.detect_cycles({"a": ["b"], "b": ["a"]})
        [['a', 'b', 'a']]
        """
        visited: set[str] = set()
        rec_stack: set[str] = set()
        cycles: list[list[str]] = []

        def _dfs(node: str, parent_path: list[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            for neighbour in heap_graph.get(node, []):
                if neighbour not in visited:
                    _dfs(neighbour, parent_path + [node])
                elif neighbour in rec_stack:
                    # Reconstruct the cycle segment.
                    if neighbour in parent_path:
                        cycle_start = parent_path.index(neighbour)
                        cycle = parent_path[cycle_start:] + [node, neighbour]
                    else:
                        cycle = [node, neighbour]
                    cycles.append(cycle)
            rec_stack.discard(node)

        for start_node in heap_graph:
            if start_node not in visited:
                _dfs(start_node, [])

        self.log(f"detect_cycles: found {len(cycles)} cycle(s)")
        return cycles

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def compute_reachability(
        self,
        root_id: int,
        heap_graph: dict[str, list[str]],
    ) -> frozenset[int]:
        """Compute the set of object IDs reachable from *root_id* in a heap graph.

        Uses breadth-first search starting from ``str(root_id)``.

        Parameters
        ----------
        root_id:
            Integer object ID of the root node.
        heap_graph:
            Adjacency mapping as produced by :meth:`build_heap_graph`.

        Returns
        -------
        frozenset[int]
            All integer object IDs reachable from *root_id*, including the root
            itself when it appears as a node in the graph.

        Raises
        ------
        ValueError
            If *root_id* is negative.

        Examples
        --------
        >>> analyzer = HeapAnalyzer()
        >>> graph = {"1": ["2"], "2": ["3"], "3": []}
        >>> sorted(analyzer.compute_reachability(1, graph))
        [1, 2, 3]
        """
        if root_id < 0:
            raise ValueError(f"root_id must be non-negative, got {root_id}")

        root_str = str(root_id)
        if root_str not in heap_graph:
            return frozenset()

        frontier: list[str] = [root_str]
        seen: set[str] = {root_str}

        while frontier:
            node = frontier.pop()
            for neighbour in heap_graph.get(node, []):
                if neighbour not in seen:
                    seen.add(neighbour)
                    frontier.append(neighbour)

        reachable: set[int] = set()
        for s in seen:
            try:
                reachable.add(int(s))
            except ValueError:
                pass

        return frozenset(reachable)

    # ------------------------------------------------------------------
    # Dangling references
    # ------------------------------------------------------------------

    def find_dangling_references(
        self,
        objects: list[HeapObject],
        valid_ids: frozenset[int],
    ) -> list[str]:
        """Find field references that point outside the known object set.

        Parameters
        ----------
        objects:
            Heap objects whose outgoing references will be checked.
        valid_ids:
            Set of integer object IDs considered "live" in the current analysis
            scope.

        Returns
        -------
        list[str]
            Human-readable strings of the form
            ``"<source_id>.<field_name> -> <target_id>"`` for each dangling
            reference found.

        Examples
        --------
        >>> analyzer = HeapAnalyzer()
        >>> analyzer.find_dangling_references([], frozenset())
        []
        """
        dangling: list[str] = []
        for obj in objects:
            for fname, target_id in obj.field_refs().items():
                if target_id not in valid_ids:
                    dangling.append(f"{obj.object_id}.{fname} -> {target_id}")
        self.log(f"find_dangling_references: {len(dangling)} dangling ref(s)")
        return dangling

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def build_object_index(
        self,
        objects: list[HeapObject],
    ) -> dict[int, HeapObject]:
        """Build and store an integer-keyed index of heap objects.

        Parameters
        ----------
        objects:
            List of :class:`HeapObject` instances to index.

        Returns
        -------
        dict[int, HeapObject]
            Mapping ``object_id → HeapObject``.

        Examples
        --------
        >>> analyzer = HeapAnalyzer()
        >>> objs = [HeapObject.from_object(42)]
        >>> index = analyzer.build_object_index(objs)
        >>> len(index)
        1
        """
        index: dict[int, HeapObject] = {obj.object_id: obj for obj in objects}
        self._object_index.update(index)
        return index

    # ------------------------------------------------------------------
    # Judgment construction
    # ------------------------------------------------------------------

    def build_heap_judgment(
        self,
        analysis: dict[str, Any],
        coordinate: CoordinateObject,
    ) -> Judgment:
        """Construct a :class:`Judgment` recording the completion of a heap analysis.

        Parameters
        ----------
        analysis:
            Result dict as returned by :meth:`analyze`.
        coordinate:
            Semantic coordinate at which the judgment is asserted.

        Returns
        -------
        Judgment
            A fully-formed judgment with ``RUNTIME_WITNESSED`` trust.

        Raises
        ------
        KeyError
            If *analysis* is missing expected keys (handled gracefully via
            ``dict.get``).

        Examples
        --------
        >>> from jugeo.geometry.site import CoordinateObject
        >>> coord = CoordinateObject.root()
        >>> analyzer = HeapAnalyzer()
        >>> j = analyzer.build_heap_judgment({"object_count": 3}, coord)
        >>> j is not None
        True
        """
        object_count = analysis.get("object_count", 0)
        return (
            JudgmentBuilder()
            .at(coordinate)
            .claiming_formula(
                f"heap_analysis_complete: {object_count} objects",
                kind=PropositionKind.STRUCTURAL,
            )
            .of_type_named("HeapAnalysis")
            .with_trust(TrustAnnotation(level=TrustLevel.RUNTIME_WITNESSED))
            .from_source(ProvenanceSource.RUNTIME)
            .build()
        )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        """Append a timestamped message to the internal analysis log.

        Parameters
        ----------
        message:
            Human-readable log entry.

        Returns
        -------
        None
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = f"[{ts}] {message}"
        self._analysis_log.append(entry)
        logger.debug("HeapAnalyzer: %s", message)

    def clear_log(self) -> None:
        """Clear all entries from the internal analysis log.

        Returns
        -------
        None
        """
        self._analysis_log.clear()
        logger.debug("HeapAnalyzer: log cleared")


# ---------------------------------------------------------------------------
# UnionFindAlgorithm
# ---------------------------------------------------------------------------


class UnionFindAlgorithm:
    """Union-Find (disjoint-set) data structure with path compression and
    union by rank.

    Supports incremental merging of equivalence classes and efficient
    component queries.  Used internally by :class:`HeapAnalyzer` and
    :class:`AliasAnalysisAlgorithm` when building alias partitions.

    Attributes
    ----------
    _parent:
        Maps each key to its parent in the forest.
    _rank:
        Approximate upper bound on the height of each root's sub-tree.
    _size:
        Number of elements in the component rooted at each key.

    Examples
    --------
    >>> uf = UnionFindAlgorithm()
    >>> uf.make_set("a")
    >>> uf.make_set("b")
    >>> uf.union("a", "b")
    True
    >>> uf.connected("a", "b")
    True
    >>> uf.total_sets()
    1
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}
        self._size: dict[str, int] = {}

    def make_set(self, key: str) -> None:
        """Create a new singleton set for *key* if it does not already exist.

        Parameters
        ----------
        key:
            Unique string identifier for the new element.

        Returns
        -------
        None

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.make_set("x")
        >>> uf.find("x")
        'x'
        """
        if key not in self._parent:
            self._parent[key] = key
            self._rank[key] = 0
            self._size[key] = 1

    def find(self, key: str) -> str:
        """Find the representative of the component containing *key*.

        Applies full path compression so that repeated calls become O(α(n)).
        If *key* is unknown, it is implicitly added via :meth:`make_set`.

        Parameters
        ----------
        key:
            Element whose representative is sought.

        Returns
        -------
        str
            Root element of the component.

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.find("new")
        'new'
        """
        if key not in self._parent:
            self.make_set(key)
        return self.path_compression(key)

    def path_compression(self, key: str) -> str:
        """Flatten the path from *key* to its root, updating parent pointers.

        Parameters
        ----------
        key:
            Element at which to start path compression.

        Returns
        -------
        str
            The root of the component.

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.make_set("a")
        >>> uf.path_compression("a")
        'a'
        """
        root = key
        while self._parent.get(root, root) != root:
            root = self._parent[root]
        # Flatten all nodes on the path.
        current = key
        while current != root:
            nxt = self._parent[current]
            self._parent[current] = root
            current = nxt
        return root

    def union(self, key1: str, key2: str) -> bool:
        """Merge the components containing *key1* and *key2*.

        Uses union by rank to keep trees shallow.

        Parameters
        ----------
        key1:
            First element.
        key2:
            Second element.

        Returns
        -------
        bool
            ``True`` if *key1* and *key2* were in different components (a merge
            actually occurred), ``False`` if they were already in the same
            component.

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.union("p", "q")
        True
        >>> uf.union("p", "q")
        False
        """
        r1 = self.find(key1)
        r2 = self.find(key2)
        if r1 == r2:
            return False
        self.union_by_rank(r1, r2)
        return True

    def union_by_rank(self, key1: str, key2: str) -> None:
        """Merge two *roots* by attaching the lower-rank tree under the higher.

        Parameters
        ----------
        key1:
            Root of the first component.
        key2:
            Root of the second component.

        Returns
        -------
        None
        """
        r1, r2 = self.find(key1), self.find(key2)
        rank1 = self._rank.get(r1, 0)
        rank2 = self._rank.get(r2, 0)
        size1 = self._size.get(r1, 1)
        size2 = self._size.get(r2, 1)

        if rank1 < rank2:
            r1, r2 = r2, r1
            size1, size2 = size2, size1

        self._parent[r2] = r1
        self._size[r1] = size1 + size2
        if rank1 == rank2:
            self._rank[r1] = rank1 + 1

    def connected(self, key1: str, key2: str) -> bool:
        """Return ``True`` if *key1* and *key2* belong to the same component.

        Parameters
        ----------
        key1:
            First element.
        key2:
            Second element.

        Returns
        -------
        bool

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.union("a", "b")
        True
        >>> uf.connected("a", "b")
        True
        >>> uf.connected("a", "c")
        False
        """
        return self.find(key1) == self.find(key2)

    def all_components(self) -> list[frozenset[str]]:
        """Return all distinct equivalence classes as a list of frozen sets.

        Returns
        -------
        list[frozenset[str]]
            One :class:`frozenset` per component, each containing all member
            keys.

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.make_set("a"); uf.make_set("b"); uf.make_set("c")
        >>> _ = uf.union("a", "b")
        >>> len(uf.all_components())
        2
        """
        groups: dict[str, set[str]] = {}
        for key in self._parent:
            root = self.find(key)
            groups.setdefault(root, set()).add(key)
        return [frozenset(members) for members in groups.values()]

    def component_of(self, key: str) -> frozenset[str]:
        """Return all elements in the same component as *key*.

        Parameters
        ----------
        key:
            Element whose component is desired.

        Returns
        -------
        frozenset[str]
            All keys sharing the same root as *key*.

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.union("x", "y")
        True
        >>> "y" in uf.component_of("x")
        True
        """
        root = self.find(key)
        return frozenset(k for k in self._parent if self.find(k) == root)

    def size_of(self, key: str) -> int:
        """Return the number of elements in the component containing *key*.

        Parameters
        ----------
        key:
            Any element.

        Returns
        -------
        int
            Component size (at least 1).

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.union("a", "b")
        True
        >>> uf.size_of("a")
        2
        """
        root = self.find(key)
        return self._size.get(root, 1)

    def total_sets(self) -> int:
        """Return the number of distinct components.

        Returns
        -------
        int

        Examples
        --------
        >>> uf = UnionFindAlgorithm()
        >>> uf.make_set("a"); uf.make_set("b")
        >>> uf.total_sets()
        2
        """
        return len({self.find(k) for k in self._parent})


# ---------------------------------------------------------------------------
# AliasAnalysisAlgorithm
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AliasAnalysisAlgorithm:
    """Fine-grained alias analysis for Python function scopes and assignments.

    Tracks how Python variable bindings create aliases at the level of
    individual assignment statements and function parameters.  Uses
    ``inspect.getclosurevars`` to extract information from live callable
    objects.

    Attributes
    ----------
    _alias_summary:
        Accumulated alias summary data, updated by each analysis call.
    _assignment_log:
        Ordered list of ``(var_name, str(id(object)))`` pairs recording
        every tracked assignment.

    Examples
    --------
    >>> alg = AliasAnalysisAlgorithm()
    >>> x = [1, 2, 3]
    >>> y = x
    >>> result = alg.track_assignments([("x", x), ("y", y)])
    >>> len(result[str(id(x))])
    2
    """

    _alias_summary: dict[str, Any] = field(default_factory=dict)
    _assignment_log: list[tuple[str, str]] = field(default_factory=list)

    # ------------------------------------------------------------------

    def analyze_function(self, fn: object) -> dict[str, Any]:
        """Analyse a callable for closure and scope aliases.

        Uses :func:`inspect.getclosurevars` to extract non-local, global, and
        free variable bindings and identifies which of them alias the same
        underlying object.

        Parameters
        ----------
        fn:
            A Python callable (function, lambda, bound method, etc.).

        Returns
        -------
        dict[str, Any]
            Keys: ``"parameter_aliases"``, ``"return_aliases"``,
            ``"container_aliases"``, ``"summary"``.  Each value is a list or
            dict of alias information.

        Raises
        ------
        TypeError
            If *fn* is not callable.

        Examples
        --------
        >>> def foo(): pass
        >>> AliasAnalysisAlgorithm().analyze_function(foo)["summary"]
        {}
        """
        if not callable(fn):
            raise TypeError(f"fn must be callable, got {type(fn).__name__}")

        try:
            closure_vars = inspect.getclosurevars(fn)  # type: ignore[arg-type]
            nonlocals: dict[str, object] = dict(closure_vars.nonlocals)
            globals_: dict[str, object] = dict(closure_vars.globals)
            builtins_: dict[str, object] = dict(closure_vars.builtins)
            unbound: set[str] = set(closure_vars.unbound)
        except TypeError:
            nonlocals = {}
            globals_ = {}
            builtins_ = {}
            unbound = set()

        # Find aliases within nonlocals.
        nl_aliases = self.track_assignments(list(nonlocals.items()))
        gl_aliases = self.track_assignments(list(globals_.items()))

        summary: dict[str, Any] = {
            "nonlocal_count": len(nonlocals),
            "global_count": len(globals_),
            "builtin_count": len(builtins_),
            "unbound": sorted(unbound),
            "nonlocal_aliases": nl_aliases,
            "global_aliases": gl_aliases,
        }
        self._alias_summary.update(summary)

        return {
            "parameter_aliases": [],
            "return_aliases": [],
            "container_aliases": [],
            "summary": summary,
        }

    def track_assignments(
        self,
        assignments: list[tuple[str, object]],
    ) -> dict[str, list[str]]:
        """Group variable names by the identity of the object they reference.

        Parameters
        ----------
        assignments:
            List of ``(variable_name, object_reference)`` pairs.

        Returns
        -------
        dict[str, list[str]]
            Mapping ``str(id(object)) → [var_name, ...]`` for all identity
            classes that contain at least two names.

        Examples
        --------
        >>> alg = AliasAnalysisAlgorithm()
        >>> lst = [1, 2]
        >>> result = alg.track_assignments([("a", lst), ("b", lst)])
        >>> len(result[str(id(lst))])
        2
        """
        id_to_names: dict[str, list[str]] = {}
        for var_name, obj_ref in assignments:
            oid_str = str(id(obj_ref))
            id_to_names.setdefault(oid_str, []).append(var_name)
            self._assignment_log.append((var_name, oid_str))

        # Return only multi-member groups.
        return {k: v for k, v in id_to_names.items() if len(v) >= 2}

    def track_parameter_aliases(
        self,
        params: list[object],
    ) -> list[AliasPartition]:
        """Detect aliases among a function's positional parameters.

        Parameters
        ----------
        params:
            List of parameter objects as they would appear at a call site.

        Returns
        -------
        list[AliasPartition]
            One partition per group of two or more parameters that reference
            the same object.

        Examples
        --------
        >>> alg = AliasAnalysisAlgorithm()
        >>> x = object()
        >>> alg.track_parameter_aliases([x, x, object()])
        [AliasPartition(...)]
        """
        id_to_indices: dict[str, list[int]] = {}
        for i, p in enumerate(params):
            oid_str = str(id(p))
            id_to_indices.setdefault(oid_str, []).append(i)

        partitions: list[AliasPartition] = []
        for oid_str, indices in id_to_indices.items():
            if len(indices) < 2:
                continue
            members = frozenset(f"param_{i}" for i in indices)
            rep = min(members)
            edges: list[AliasEdge] = []
            members_list = sorted(members)
            for i, m in enumerate(members_list):
                for m2 in members_list[i + 1 :]:
                    edges.append(
                        AliasEdge(source_id=m, target_id=m2, edge_kind="parameter_alias")
                    )
            partitions.append(
                AliasPartition(
                    partition_id=str(uuid.uuid4()),
                    members=members,
                    representative=rep,
                    edges=tuple(edges),
                )
            )
        return partitions

    def track_return_aliases(
        self,
        fn_result: object,
        local_vars: dict[str, object],
    ) -> list[AliasEdge]:
        """Find which local variables alias the return value of a function.

        Parameters
        ----------
        fn_result:
            The object returned by the function.
        local_vars:
            Dict mapping local variable names to their current values, e.g.
            from :func:`locals`.

        Returns
        -------
        list[AliasEdge]
            One :class:`AliasEdge` per local variable that is identical (by
            ``id()``) to *fn_result*, with ``edge_kind="return_alias"``.

        Examples
        --------
        >>> alg = AliasAnalysisAlgorithm()
        >>> x = [1, 2, 3]
        >>> edges = alg.track_return_aliases(x, {"result": x, "other": []})
        >>> edges[0].target_id
        'return'
        """
        result_id = str(id(fn_result))
        edges: list[AliasEdge] = []
        for var_name, obj in local_vars.items():
            if str(id(obj)) == result_id:
                edges.append(
                    AliasEdge(
                        source_id=var_name,
                        target_id="return",
                        edge_kind="return_alias",
                        label=result_id,
                    )
                )
        return edges

    def track_container_aliases(self, container: object) -> list[AliasEdge]:
        """Detect elements that alias each other within a container.

        Inspects :class:`dict`, :class:`list`, and :class:`tuple` containers
        for repeated element identities.

        Parameters
        ----------
        container:
            The container object to inspect.

        Returns
        -------
        list[AliasEdge]
            Edges between positions/keys that reference the same element object.

        Examples
        --------
        >>> alg = AliasAnalysisAlgorithm()
        >>> x = object()
        >>> edges = alg.track_container_aliases([x, x, object()])
        >>> len(edges)
        1
        """
        edges: list[AliasEdge] = []
        items: list[tuple[str, object]] = []

        if isinstance(container, dict):
            items = [(str(k), v) for k, v in container.items()]
        elif isinstance(container, (list, tuple)):
            items = [(str(i), v) for i, v in enumerate(container)]
        else:
            return edges

        id_to_keys: dict[str, list[str]] = {}
        for key, obj in items:
            oid = str(id(obj))
            id_to_keys.setdefault(oid, []).append(key)

        for oid, keys in id_to_keys.items():
            if len(keys) < 2:
                continue
            for i, k1 in enumerate(keys):
                for k2 in keys[i + 1 :]:
                    edges.append(
                        AliasEdge(
                            source_id=k1,
                            target_id=k2,
                            edge_kind="container_alias",
                            label=oid,
                        )
                    )
        return edges

    def build_alias_summary(self) -> dict[str, Any]:
        """Return a copy of the accumulated alias summary.

        Returns
        -------
        dict[str, Any]
            Shallow copy of ``_alias_summary``.
        """
        return dict(self._alias_summary)

    def reset(self) -> None:
        """Clear all accumulated state.

        Returns
        -------
        None
        """
        self._alias_summary.clear()
        self._assignment_log.clear()
        logger.debug("AliasAnalysisAlgorithm: state reset")


# ---------------------------------------------------------------------------
# MutationFlowAlgorithm
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MutationFlowAlgorithm:
    """Propagates mutation events through an alias map.

    Given a :class:`MutationEvent` and an alias map that identifies which
    object IDs alias each other, this algorithm produces the *derived* mutation
    events that must logically accompany the primary event in order to preserve
    sheaf-consistency across the heap.

    Attributes
    ----------
    _flow_log:
        Sequence of flow computation results, each stored as a plain dict.

    Examples
    --------
    >>> alg = MutationFlowAlgorithm()
    >>> ev = MutationEvent(event_id="e1", object_id="10", field_name="x",
    ...                    old_value_repr="0", new_value_repr="1")
    >>> alg.compute_mutation_flow(ev, {"10": ["20", "30"]})
    [MutationEvent(event_id=..., object_id='20', ...), ...]
    """

    _flow_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------

    def compute_mutation_flow(
        self,
        event: MutationEvent,
        alias_map: dict[str, list[str]],
    ) -> list[MutationEvent]:
        """Derive propagated mutation events for all aliases of the event target.

        Parameters
        ----------
        event:
            The primary mutation event.
        alias_map:
            Mapping ``object_id → [aliased_object_id, ...]``.

        Returns
        -------
        list[MutationEvent]
            One derived :class:`MutationEvent` per alias, each with a fresh
            ``event_id``, the alias's ``object_id``, and ``is_valid=True``.

        Examples
        --------
        >>> alg = MutationFlowAlgorithm()
        >>> ev = MutationEvent("e0", "5", "val", "old", "new")
        >>> alg.compute_mutation_flow(ev, {"5": ["6"]})
        [MutationEvent(event_id=..., object_id='6', ...)]
        """
        aliases = alias_map.get(event.object_id, [])
        derived: list[MutationEvent] = []
        for alias_id in aliases:
            propagated = replace(
                event,
                event_id=str(uuid.uuid4()),
                object_id=alias_id,
                timestamp=time.time(),
            )
            derived.append(propagated)

        self._flow_log.append(
            {
                "primary_event_id": event.event_id,
                "primary_object_id": event.object_id,
                "alias_count": len(aliases),
                "derived_count": len(derived),
                "timestamp": time.time(),
            }
        )
        return derived

    def find_write_effects(
        self,
        event: MutationEvent,
        alias_map: dict[str, list[str]],
    ) -> list[str]:
        """Return all object IDs that will be written due to aliasing.

        Parameters
        ----------
        event:
            The triggering mutation event.
        alias_map:
            Mapping ``object_id → [aliased_object_id, ...]``.

        Returns
        -------
        list[str]
            Object IDs that will be mutated, including the event's own target.

        Examples
        --------
        >>> alg = MutationFlowAlgorithm()
        >>> ev = MutationEvent("e1", "1", "f", "", "")
        >>> alg.find_write_effects(ev, {"1": ["2", "3"]})
        ['1', '2', '3']
        """
        aliases = alias_map.get(event.object_id, [])
        return [event.object_id] + list(aliases)

    def find_read_effects(
        self,
        event: MutationEvent,
        alias_map: dict[str, list[str]],
    ) -> list[str]:
        """Return object IDs whose reads of the mutated field are affected.

        Any object that aliases the event target will observe a changed value
        when they next read the field named by ``event.field_name``.

        Parameters
        ----------
        event:
            The mutation event.
        alias_map:
            Mapping ``object_id → [aliased_object_id, ...]``.

        Returns
        -------
        list[str]
            Object IDs affected by stale reads.

        Examples
        --------
        >>> alg = MutationFlowAlgorithm()
        >>> ev = MutationEvent("e1", "A", "data", "", "")
        >>> alg.find_read_effects(ev, {"A": ["B"]})
        ['A', 'B']
        """
        return self.find_write_effects(event, alias_map)

    def build_effect_summary(
        self,
        event: MutationEvent,
        writes: list[str],
        reads: list[str],
    ) -> dict[str, Any]:
        """Summarise the write and read effects of a mutation event.

        Parameters
        ----------
        event:
            The mutation event being summarised.
        writes:
            Object IDs that will be written.
        reads:
            Object IDs whose reads are affected.

        Returns
        -------
        dict[str, Any]
            Keys: ``"event_id"``, ``"object_id"``, ``"field_name"``,
            ``"write_effects"``, ``"read_effects"``, ``"total_effects"``.

        Examples
        --------
        >>> alg = MutationFlowAlgorithm()
        >>> ev = MutationEvent("e1", "1", "x", "0", "1")
        >>> alg.build_effect_summary(ev, ["1", "2"], ["1", "2"])["total_effects"]
        4
        """
        return {
            "event_id": event.event_id,
            "object_id": event.object_id,
            "field_name": event.field_name,
            "write_effects": writes,
            "read_effects": reads,
            "total_effects": len(writes) + len(reads),
        }

    def detect_write_write_conflicts(
        self,
        events: list[MutationEvent],
    ) -> list[tuple[str, str]]:
        """Find pairs of events that write the same ``object.field`` key.

        Parameters
        ----------
        events:
            List of mutation events to examine.

        Returns
        -------
        list[tuple[str, str]]
            Pairs of ``(event_id_1, event_id_2)`` that conflict.

        Examples
        --------
        >>> alg = MutationFlowAlgorithm()
        >>> e1 = MutationEvent("e1", "1", "x", "0", "1")
        >>> e2 = MutationEvent("e2", "1", "x", "1", "2")
        >>> alg.detect_write_write_conflicts([e1, e2])
        [('e1', 'e2')]
        """
        key_to_events: dict[str, list[MutationEvent]] = {}
        for ev in events:
            key_to_events.setdefault(ev.key(), []).append(ev)

        conflicts: list[tuple[str, str]] = []
        for key, evs in key_to_events.items():
            for i, ev1 in enumerate(evs):
                for ev2 in evs[i + 1 :]:
                    conflicts.append((ev1.event_id, ev2.event_id))
        return conflicts

    def detect_read_write_conflicts(
        self,
        events: list[MutationEvent],
        read_map: dict[str, list[str]],
    ) -> list[tuple[str, str]]:
        """Find events that write a field that is also being read.

        Parameters
        ----------
        events:
            List of write mutation events.
        read_map:
            Mapping ``object_id → [field_name, ...]`` describing which fields
            are currently being read.

        Returns
        -------
        list[tuple[str, str]]
            Pairs of ``(event_id, "<object_id>.<field_name>")`` for each
            conflict found.

        Examples
        --------
        >>> alg = MutationFlowAlgorithm()
        >>> ev = MutationEvent("e1", "obj1", "val", "0", "1")
        >>> alg.detect_read_write_conflicts([ev], {"obj1": ["val"]})
        [('e1', 'obj1.val')]
        """
        conflicts: list[tuple[str, str]] = []
        for ev in events:
            read_fields = read_map.get(ev.object_id, [])
            if ev.field_name in read_fields:
                conflicts.append((ev.event_id, f"{ev.object_id}.{ev.field_name}"))
        return conflicts


# ---------------------------------------------------------------------------
# HeapDiffAlgorithm
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HeapDiffAlgorithm:
    """Computes structural differences between two :class:`HeapSnapshot` instances.

    Diff results identify which objects were added, removed, or mutated between
    two snapshots, and which alias partitions appeared or disappeared.

    Attributes
    ----------
    _diff_log:
        Accumulated log of diff operation results.

    Examples
    --------
    >>> alg = HeapDiffAlgorithm()
    >>> snap1 = HeapSnapshot(snapshot_id="s1", objects=(), partitions=(), sections=())
    >>> snap2 = HeapSnapshot(snapshot_id="s2", objects=(), partitions=(), sections=())
    >>> diff = alg.diff(snap1, snap2)
    >>> diff["added_count"]
    0
    """

    _diff_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------

    def diff(self, snap1: HeapSnapshot, snap2: HeapSnapshot) -> dict[str, Any]:
        """Compute a comprehensive diff between two heap snapshots.

        Parameters
        ----------
        snap1:
            The *before* snapshot.
        snap2:
            The *after* snapshot.

        Returns
        -------
        dict[str, Any]
            Keys: ``"added"``, ``"removed"``, ``"mutated"``, ``"new_aliases"``,
            ``"broken_aliases"``, ``"added_count"``, ``"removed_count"``,
            ``"mutated_count"``, ``"new_alias_count"``, ``"broken_alias_count"``,
            ``"snapshot_id_before"``, ``"snapshot_id_after"``.

        Examples
        --------
        >>> alg = HeapDiffAlgorithm()
        >>> s1 = HeapSnapshot("s1", (), (), ())
        >>> s2 = HeapSnapshot("s2", (), (), ())
        >>> alg.diff(s1, s2)["removed_count"]
        0
        """
        added = self.added_objects(snap1, snap2)
        removed = self.removed_objects(snap1, snap2)
        mutated = self.mutated_objects(snap1, snap2)
        na = self.new_aliases(snap1, snap2)
        ba = self.broken_aliases(snap1, snap2)

        result: dict[str, Any] = {
            "added": [o.serialize() for o in added],
            "removed": [o.serialize() for o in removed],
            "mutated": [(a.serialize(), b.serialize()) for a, b in mutated],
            "new_aliases": [p.serialize() for p in na],
            "broken_aliases": [p.serialize() for p in ba],
            "added_count": len(added),
            "removed_count": len(removed),
            "mutated_count": len(mutated),
            "new_alias_count": len(na),
            "broken_alias_count": len(ba),
            "snapshot_id_before": snap1.snapshot_id,
            "snapshot_id_after": snap2.snapshot_id,
        }
        self._diff_log.append(
            {
                "before": snap1.snapshot_id,
                "after": snap2.snapshot_id,
                "added": len(added),
                "removed": len(removed),
                "mutated": len(mutated),
                "timestamp": time.time(),
            }
        )
        return result

    def added_objects(
        self,
        snap1: HeapSnapshot,
        snap2: HeapSnapshot,
    ) -> list[HeapObject]:
        """Return objects present in *snap2* but not in *snap1*.

        Parameters
        ----------
        snap1:
            The before snapshot.
        snap2:
            The after snapshot.

        Returns
        -------
        list[HeapObject]
            Newly added objects.

        Examples
        --------
        >>> alg = HeapDiffAlgorithm()
        >>> alg.added_objects(
        ...     HeapSnapshot("s1", (), (), ()),
        ...     HeapSnapshot("s2", (), (), ()),
        ... )
        []
        """
        ids1 = snap1.all_object_ids()
        return [o for o in snap2.objects if o.object_id not in ids1]

    def removed_objects(
        self,
        snap1: HeapSnapshot,
        snap2: HeapSnapshot,
    ) -> list[HeapObject]:
        """Return objects present in *snap1* but absent from *snap2*.

        Parameters
        ----------
        snap1:
            The before snapshot.
        snap2:
            The after snapshot.

        Returns
        -------
        list[HeapObject]
            Removed objects.

        Examples
        --------
        >>> alg = HeapDiffAlgorithm()
        >>> alg.removed_objects(
        ...     HeapSnapshot("s1", (), (), ()),
        ...     HeapSnapshot("s2", (), (), ()),
        ... )
        []
        """
        ids2 = snap2.all_object_ids()
        return [o for o in snap1.objects if o.object_id not in ids2]

    def mutated_objects(
        self,
        snap1: HeapSnapshot,
        snap2: HeapSnapshot,
    ) -> list[tuple[HeapObject, HeapObject]]:
        """Return objects present in both snapshots whose fields differ.

        Parameters
        ----------
        snap1:
            The before snapshot.
        snap2:
            The after snapshot.

        Returns
        -------
        list[tuple[HeapObject, HeapObject]]
            Pairs of ``(old_object, new_object)`` for each mutated object.

        Examples
        --------
        >>> alg = HeapDiffAlgorithm()
        >>> alg.mutated_objects(
        ...     HeapSnapshot("s1", (), (), ()),
        ...     HeapSnapshot("s2", (), (), ()),
        ... )
        []
        """
        index1 = snap1.object_index()
        index2 = snap2.object_index()
        mutated: list[tuple[HeapObject, HeapObject]] = []

        for oid, obj2 in index2.items():
            if oid not in index1:
                continue
            obj1 = index1[oid]
            if obj1.field_refs() != obj2.field_refs() or obj1.size != obj2.size:
                mutated.append((obj1, obj2))
        return mutated

    def new_aliases(
        self,
        snap1: HeapSnapshot,
        snap2: HeapSnapshot,
    ) -> list[AliasPartition]:
        """Return alias partitions in *snap2* not present in *snap1*.

        Comparison is by ``partition_id``.

        Parameters
        ----------
        snap1:
            The before snapshot.
        snap2:
            The after snapshot.

        Returns
        -------
        list[AliasPartition]
            Newly formed alias partitions.

        Examples
        --------
        >>> alg = HeapDiffAlgorithm()
        >>> alg.new_aliases(
        ...     HeapSnapshot("s1", (), (), ()),
        ...     HeapSnapshot("s2", (), (), ()),
        ... )
        []
        """
        ids1 = {p.partition_id for p in snap1.partitions}
        return [p for p in snap2.partitions if p.partition_id not in ids1]

    def broken_aliases(
        self,
        snap1: HeapSnapshot,
        snap2: HeapSnapshot,
    ) -> list[AliasPartition]:
        """Return alias partitions in *snap1* that are absent from *snap2*.

        Parameters
        ----------
        snap1:
            The before snapshot.
        snap2:
            The after snapshot.

        Returns
        -------
        list[AliasPartition]
            Alias partitions that no longer exist.

        Examples
        --------
        >>> alg = HeapDiffAlgorithm()
        >>> alg.broken_aliases(
        ...     HeapSnapshot("s1", (), (), ()),
        ...     HeapSnapshot("s2", (), (), ()),
        ... )
        []
        """
        ids2 = {p.partition_id for p in snap2.partitions}
        return [p for p in snap1.partitions if p.partition_id not in ids2]

    def apply_diff(
        self,
        snap: HeapSnapshot,
        diff: dict[str, Any],
    ) -> HeapSnapshot | None:
        """Apply a diff dict to *snap* to produce a new :class:`HeapSnapshot`.

        Adds objects listed in ``diff["added"]``, removes those in
        ``diff["removed"]``, and replaces objects listed in
        ``diff["mutated"]`` with their updated versions.

        Parameters
        ----------
        snap:
            The base snapshot to patch.
        diff:
            A diff dict as produced by :meth:`diff`.

        Returns
        -------
        HeapSnapshot | None
            The patched snapshot, or ``None`` if an unrecoverable error occurs
            during parsing.

        Raises
        ------
        KeyError
            If *diff* is missing required top-level keys (handled gracefully).

        Examples
        --------
        >>> alg = HeapDiffAlgorithm()
        >>> snap = HeapSnapshot("s1", (), (), ())
        >>> diff = alg.diff(snap, snap)
        >>> patched = alg.apply_diff(snap, diff)
        >>> patched.object_count()
        0
        """
        try:
            removed_serialized: list[dict[str, Any]] = diff.get("removed", [])
            added_serialized: list[dict[str, Any]] = diff.get("added", [])
            mutated_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = diff.get(
                "mutated", []
            )

            removed_ids: set[int] = set()
            for r in removed_serialized:
                try:
                    removed_ids.add(int(r["object_id"]))
                except (KeyError, ValueError):
                    pass

            mutated_new_map: dict[int, HeapObject] = {}
            for _old_dict, new_dict in mutated_pairs:
                try:
                    new_obj = HeapObject.parse(new_dict)
                    mutated_new_map[new_obj.object_id] = new_obj
                except (KeyError, ValueError):
                    pass

            current_objects: list[HeapObject] = []
            for obj in snap.objects:
                oid = obj.object_id
                if oid in removed_ids:
                    continue
                if oid in mutated_new_map:
                    current_objects.append(mutated_new_map[oid])
                else:
                    current_objects.append(obj)

            added_objects: list[HeapObject] = []
            for a in added_serialized:
                try:
                    added_objects.append(HeapObject.parse(a))
                except (KeyError, ValueError):
                    pass

            new_objects = tuple(current_objects + added_objects)
            new_snapshot_id = str(uuid.uuid4())

            return replace(
                snap,
                snapshot_id=new_snapshot_id,
                objects=new_objects,
                timestamp=time.time(),
            )

        except Exception:
            logger.exception("HeapDiffAlgorithm.apply_diff: failed to apply diff")
            return None


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "HeapAnalyzer",
    "UnionFindAlgorithm",
    "AliasAnalysisAlgorithm",
    "MutationFlowAlgorithm",
    "HeapDiffAlgorithm",
]
