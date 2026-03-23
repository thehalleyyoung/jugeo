"""High-level navigation algorithms for theory-space traversal.

This module provides the orchestration layer for ``TheorySpace`` navigation
as described in ``theory2.tex`` §5 (theory-space navigation algorithms).
It glues together space construction (s01), purpose conditioning (s02), and
path-finding (s03) into coherent, reusable navigation workflows.

Module layout
─────────────
┌──────────────────────────┬────────────────────────────────────────────────┐
│ Symbol                   │ Role                                           │
├──────────────────────────┼────────────────────────────────────────────────┤
│ _uuid4_hex               │ Generate compact random identifiers            │
│ _elapsed_ms              │ Measure elapsed milliseconds from a start time │
│ _safe_avg                │ Safe average over a possibly-empty iterable    │
│ NavigationAlgorithm      │ Enum of available navigation algorithms        │
│ NavigationHistory        │ Non-dataclass record of past navigations       │
│ TheoryNavigator          │ High-level navigation orchestrator             │
│ MapBuilder               │ Space construction and indexing utilities      │
│ NavigationOptimizer      │ Path and config optimisation helpers           │
│ NavigationBenchmark      │ Benchmarking and profiling utilities           │
│ NavigationDiagnostics    │ Health-check and diagnostic tools              │
└──────────────────────────┴────────────────────────────────────────────────┘
"""

from __future__ import annotations

import math
import random
import time
import uuid
from collections import defaultdict
from enum import Enum
from typing import Any

from jugeo.ideation.theory_navigation.models import (
    NavigationPath,
    NavigationStrategy,
    NodeMaturity,
    PurposeCondition,
    TheoryNode,
    TheorySpace,
)
from jugeo.ideation.theory_navigation.space_construction import (
    EdgeBuilder,
    IncrementalSpaceUpdater,
    NodeExtractor,
    SpaceConstructionConfig,
    SpaceConstructor,
    SpaceIndexer,
)
from jugeo.ideation.theory_navigation.purpose_conditioning import (
    HeuristicComputer,
    PurposeAligner,
    PurposeConditioner,
    PurposeDriftDetector,
    PurposeVector,
    PurposeWeightMap,
)
from jugeo.ideation.theory_navigation.path_finding import (
    DiversePathFinder,
    PathCache,
    PathEvaluator,
    PathFinder,
    PurposeGuidedSearch,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _uuid4_hex() -> str:
    """Return a compact 12-character hex string from a random UUID."""
    return uuid.uuid4().hex[:12]


def _elapsed_ms(start: float) -> float:
    """Return the milliseconds elapsed since *start* (from ``time.time()``).

    Parameters
    ----------
    start : float
        Start timestamp in seconds.

    Returns
    -------
    float
        Elapsed time in milliseconds.
    """
    return (time.time() - start) * 1000.0


def _safe_avg(values: list[float]) -> float:
    """Return the mean of *values*, or 0.0 if the list is empty.

    Parameters
    ----------
    values : list[float]
        Numeric values to average.

    Returns
    -------
    float
        Arithmetic mean or 0.0 for empty input.
    """
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, float(value)))


# ---------------------------------------------------------------------------
# NavigationAlgorithm – enum of available algorithms
# ---------------------------------------------------------------------------


class NavigationAlgorithm(str, Enum):
    """Enumeration of navigation algorithms available in this package.

    Each member carries metadata about completeness, optimality, and whether
    a heuristic function is required.
    """

    A_STAR = "a_star"
    BFS = "bfs"
    DFS = "dfs"
    BEAM_SEARCH = "beam_search"
    DIVERSE_PATHS = "diverse_paths"
    PURPOSE_GUIDED = "purpose_guided"
    RANDOM_WALK = "random_walk"

    # ------------------------------------------------------------------
    # Metadata methods
    # ------------------------------------------------------------------

    def description(self) -> str:
        """Return a human-readable description of the algorithm.

        Returns
        -------
        str
            One-sentence description.
        """
        descriptions: dict[str, str] = {
            NavigationAlgorithm.A_STAR: (
                "A* best-first search using a purpose-aware admissible "
                "heuristic; cost-optimal and complete."
            ),
            NavigationAlgorithm.BFS: (
                "Breadth-first search; guaranteed to find the shortest-hop "
                "path if one exists."
            ),
            NavigationAlgorithm.DFS: (
                "Depth-first search with a configurable depth limit; "
                "complete within the depth bound."
            ),
            NavigationAlgorithm.BEAM_SEARCH: (
                "Beam search retaining the top-k candidates at each step; "
                "fast but not complete."
            ),
            NavigationAlgorithm.DIVERSE_PATHS: (
                "Yen-style k-shortest-paths algorithm with Jaccard diversity "
                "filtering to return structurally distinct routes."
            ),
            NavigationAlgorithm.PURPOSE_GUIDED: (
                "Purpose-conditioned beam search that scores nodes by "
                "alignment with an active PurposeCondition."
            ),
            NavigationAlgorithm.RANDOM_WALK: (
                "Stochastic random-walk exploration; useful for sampling "
                "novel routes without a specific goal."
            ),
        }
        return descriptions.get(self, "No description available.")

    def is_complete(self) -> bool:
        """Return ``True`` if the algorithm is guaranteed to find a path.

        Returns
        -------
        bool
            ``True`` for A_STAR, BFS, and DFS.
        """
        return self in (
            NavigationAlgorithm.A_STAR,
            NavigationAlgorithm.BFS,
            NavigationAlgorithm.DFS,
        )

    def is_optimal(self) -> bool:
        """Return ``True`` if the algorithm returns the cost-optimal path.

        Returns
        -------
        bool
            ``True`` only for A_STAR (given an admissible heuristic).
        """
        return self == NavigationAlgorithm.A_STAR

    def requires_heuristic(self) -> bool:
        """Return ``True`` if the algorithm uses a heuristic function.

        Returns
        -------
        bool
            ``True`` for A_STAR, BEAM_SEARCH, and PURPOSE_GUIDED.
        """
        return self in (
            NavigationAlgorithm.A_STAR,
            NavigationAlgorithm.BEAM_SEARCH,
            NavigationAlgorithm.PURPOSE_GUIDED,
        )


# ---------------------------------------------------------------------------
# NavigationHistory – record of past navigations
# ---------------------------------------------------------------------------


class NavigationHistory:
    """In-memory record of past navigation events.

    Stores metadata and results for every call to ``TheoryNavigator.navigate``
    and related methods.  Provides query helpers for filtering by start/goal,
    per-algorithm statistics, and human-readable summaries.

    This class is deliberately **not** a dataclass so that it can hold mutable
    state conveniently.
    """

    def __init__(self) -> None:
        # Ordered list of history entry dicts
        self._entries: list[dict[str, Any]] = []
        # Quick lookup by entry_id
        self._by_id: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        path: NavigationPath,
        algorithm: NavigationAlgorithm,
        duration_ms: float,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a completed navigation event.

        Parameters
        ----------
        path : NavigationPath
            The returned path (may be empty if search failed).
        algorithm : NavigationAlgorithm
            Algorithm that was used.
        duration_ms : float
            Wall-clock time in milliseconds.
        metadata : dict | None, optional
            Arbitrary additional context.

        Returns
        -------
        str
            Unique entry ID for this record.
        """
        entry_id = f"nav-{_uuid4_hex()}"
        entry: dict[str, Any] = {
            "entry_id": entry_id,
            "path_id": path.path_id,
            "start_id": path.start_id,
            "goal_id": path.goal_id,
            "algorithm": algorithm.value,
            "duration_ms": duration_ms,
            "path_length": path.length(),
            "total_cost": path.total_cost,
            "quality_score": path.quality_score(),
            "purpose_alignment": path.purpose_alignment,
            "is_empty": path.is_empty(),
            "timestamp": time.time(),
            "metadata": metadata or {},
            # Store the path object itself for retrieval
            "_path": path,
        }
        self._entries.append(entry)
        self._by_id[entry_id] = entry
        return entry_id

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, entry_id: str) -> dict[str, Any] | None:
        """Return the history entry with the given *entry_id*.

        Parameters
        ----------
        entry_id : str
            Entry identifier returned by ``record``.

        Returns
        -------
        dict | None
            Entry dict (without the ``_path`` key) or ``None`` if not found.
        """
        entry = self._by_id.get(entry_id)
        if entry is None:
            return None
        # Return a copy without the internal path object
        return {k: v for k, v in entry.items() if not k.startswith("_")}

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the last *n* history entries.

        Parameters
        ----------
        n : int, optional
            How many recent entries to return.

        Returns
        -------
        list[dict]
            Up to *n* most recent entries, newest last.
        """
        tail = self._entries[-n:]
        return [{k: v for k, v in e.items() if not k.startswith("_")} for e in tail]

    def paths_for_start(self, start_id: str) -> list[NavigationPath]:
        """Return all ``NavigationPath`` objects whose start matches *start_id*.

        Parameters
        ----------
        start_id : str
            Source node ID filter.

        Returns
        -------
        list[NavigationPath]
            Matching paths in chronological order.
        """
        return [
            e["_path"]
            for e in self._entries
            if e["start_id"] == start_id and "_path" in e
        ]

    def paths_for_goal(self, goal_id: str) -> list[NavigationPath]:
        """Return all ``NavigationPath`` objects whose goal matches *goal_id*.

        Parameters
        ----------
        goal_id : str
            Target node ID filter.

        Returns
        -------
        list[NavigationPath]
            Matching paths in chronological order.
        """
        return [
            e["_path"]
            for e in self._entries
            if e["goal_id"] == goal_id and "_path" in e
        ]

    def algorithm_stats(self) -> dict[str, dict[str, Any]]:
        """Compute per-algorithm usage and performance statistics.

        Returns
        -------
        dict[str, dict[str, Any]]
            Keys are algorithm values; each sub-dict contains:
            count, avg_duration_ms, avg_quality, success_rate.
        """
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in self._entries:
            buckets[entry["algorithm"]].append(entry)

        stats: dict[str, dict[str, Any]] = {}
        for algo, entries in buckets.items():
            durations = [e["duration_ms"] for e in entries]
            qualities = [e["quality_score"] for e in entries]
            successes = [e for e in entries if not e["is_empty"]]
            stats[algo] = {
                "count": len(entries),
                "avg_duration_ms": _safe_avg(durations),
                "avg_quality": _safe_avg(qualities),
                "success_rate": len(successes) / len(entries) if entries else 0.0,
                "min_duration_ms": min(durations) if durations else 0.0,
                "max_duration_ms": max(durations) if durations else 0.0,
            }
        return stats

    def clear(self) -> None:
        """Remove all history entries."""
        self._entries.clear()
        self._by_id.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialise the history to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation with all entries.
        """
        return {
            "entries": [
                {k: v for k, v in e.items() if not k.startswith("_")}
                for e in self._entries
            ],
            "total": len(self._entries),
        }

    def summary(self) -> str:
        """Return a multi-line statistics summary.

        Returns
        -------
        str
            Formatted summary of navigation history.
        """
        algo_stats = self.algorithm_stats()
        total = len(self._entries)
        success_count = sum(1 for e in self._entries if not e["is_empty"])
        all_durations = [e["duration_ms"] for e in self._entries]
        all_qualities = [e["quality_score"] for e in self._entries]

        lines: list[str] = [
            "╔══════════════════════════════════════════╗",
            "║       Navigation History Summary         ║",
            "╚══════════════════════════════════════════╝",
            f"  Total navigations   : {total}",
            f"  Successful          : {success_count}",
            f"  Overall success rate: {success_count/total:.1%}" if total else "  Overall success rate: N/A",
            f"  Avg duration (ms)   : {_safe_avg(all_durations):.1f}",
            f"  Avg quality score   : {_safe_avg(all_qualities):.4f}",
            "",
            "  Per-algorithm breakdown:",
        ]
        for algo, stats in sorted(algo_stats.items()):
            lines.append(
                f"    {algo:20s} count={stats['count']:4d} "
                f"success={stats['success_rate']:.0%} "
                f"avg_ms={stats['avg_duration_ms']:.1f} "
                f"avg_q={stats['avg_quality']:.3f}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TheoryNavigator – high-level navigation orchestrator
# ---------------------------------------------------------------------------


class TheoryNavigator:
    """Orchestrates navigation through a ``TheorySpace``.

    Combines path-finding, purpose conditioning, caching, and history
    recording into a single convenient interface.

    Parameters
    ----------
    space : TheorySpace | None, optional
        Initial theory graph.  Can be set later via ``set_space``.
    condition : PurposeCondition | None, optional
        Initial purpose condition.  Can be set later via ``set_condition``.
    """

    def __init__(
        self,
        space: TheorySpace | None = None,
        condition: PurposeCondition | None = None,
    ) -> None:
        self._space: TheorySpace | None = space
        self._condition: PurposeCondition | None = condition
        self._cache: PathCache = PathCache(max_size=300)
        self._history: NavigationHistory = NavigationHistory()
        self._finder: PathFinder = PathFinder()
        self._diverse_finder: DiversePathFinder = DiversePathFinder(self._finder)
        self._guided: PurposeGuidedSearch = PurposeGuidedSearch(condition)
        self._evaluator: PathEvaluator = PathEvaluator(condition)
        self._heuristic: HeuristicComputer = HeuristicComputer()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_space(self, space: TheorySpace) -> None:
        """Replace the active theory space.

        Parameters
        ----------
        space : TheorySpace
            New graph for navigation.
        """
        self._space = space
        # Invalidate cache on space change
        self._cache.clear()

    def set_condition(self, condition: PurposeCondition) -> None:
        """Replace the active purpose condition.

        Parameters
        ----------
        condition : PurposeCondition
            New scoring condition.
        """
        self._condition = condition
        self._guided.set_condition(condition)
        self._evaluator = PathEvaluator(condition)

    # ------------------------------------------------------------------
    # Core navigation
    # ------------------------------------------------------------------

    def navigate(
        self,
        start_id: str,
        goal_id: str,
        *,
        algorithm: NavigationAlgorithm = NavigationAlgorithm.A_STAR,
        purpose: str = "",
        use_cache: bool = True,
    ) -> NavigationPath:
        """Navigate from *start_id* to *goal_id* using *algorithm*.

        Checks the path cache first (when *use_cache* is True), dispatches
        to the appropriate search algorithm, records the result in history,
        and stores the result in cache.

        Parameters
        ----------
        start_id : str
            Source node ID.
        goal_id : str
            Target node ID.
        algorithm : NavigationAlgorithm, optional
            Which algorithm to use.
        purpose : str, optional
            Purpose annotation for the path.
        use_cache : bool, optional
            Whether to check and populate the path cache.

        Returns
        -------
        NavigationPath
            Discovered path, or empty path if unreachable / no space set.
        """
        if self._space is None:
            raise RuntimeError("TheoryNavigator has no space set; call set_space() first.")

        if use_cache:
            cached = self._cache.get(start_id, goal_id, purpose)
            if cached is not None:
                return cached

        start_time = time.time()
        path = self._dispatch(start_id, goal_id, algorithm, purpose)
        duration = _elapsed_ms(start_time)

        # Tag purpose if not already set
        if path.purpose != purpose and purpose:
            from dataclasses import replace as dc_replace
            path = dc_replace(path, purpose=purpose)

        self._history.record(path, algorithm, duration, {"purpose": purpose})

        if use_cache and not path.is_empty():
            self._cache.put(path)

        return path

    def navigate_diverse(
        self,
        start_id: str,
        goal_id: str,
        k: int = 3,
    ) -> list[NavigationPath]:
        """Find *k* structurally diverse paths from *start_id* to *goal_id*.

        Parameters
        ----------
        start_id : str
            Source node.
        goal_id : str
            Target node.
        k : int, optional
            Number of diverse paths to return.

        Returns
        -------
        list[NavigationPath]
            Up to *k* diverse paths.
        """
        if self._space is None:
            raise RuntimeError("No space set.")
        start_time = time.time()
        paths = self._diverse_finder.find_k_paths(start_id, goal_id, self._space, k=k)
        duration = _elapsed_ms(start_time)
        for p in paths:
            self._history.record(p, NavigationAlgorithm.DIVERSE_PATHS, duration)
        return paths

    def explore(
        self,
        start_id: str,
        max_depth: int = 5,
    ) -> list[TheoryNode]:
        """Purpose-guided exploration from *start_id*.

        Parameters
        ----------
        start_id : str
            Origin node.
        max_depth : int, optional
            Exploration depth limit.

        Returns
        -------
        list[TheoryNode]
            Visited nodes sorted by purpose alignment.
        """
        if self._space is None:
            raise RuntimeError("No space set.")
        return self._guided.explore_from(start_id, self._space, max_depth=max_depth)

    def find_all_paths_between(
        self,
        start_id: str,
        goal_id: str,
        max_paths: int = 10,
    ) -> list[NavigationPath]:
        """Find multiple paths using all available algorithms, de-duplicated.

        Parameters
        ----------
        start_id : str
            Source node.
        goal_id : str
            Target node.
        max_paths : int, optional
            Maximum paths to return.

        Returns
        -------
        list[NavigationPath]
            Paths sorted by quality.
        """
        if self._space is None:
            raise RuntimeError("No space set.")

        seen_node_ids: set[tuple[str, ...]] = set()
        all_paths: list[NavigationPath] = []

        algorithms = [
            NavigationAlgorithm.A_STAR,
            NavigationAlgorithm.BFS,
            NavigationAlgorithm.DFS,
            NavigationAlgorithm.PURPOSE_GUIDED,
        ]

        for algo in algorithms:
            try:
                p = self._dispatch(start_id, goal_id, algo, "")
                if not p.is_empty() and p.node_ids not in seen_node_ids:
                    seen_node_ids.add(p.node_ids)
                    all_paths.append(p)
            except Exception:
                pass

        # Also grab diverse paths
        try:
            diverse = self._diverse_finder.find_k_paths(
                start_id, goal_id, self._space, k=max(3, max_paths // 2)
            )
            for p in diverse:
                if p.node_ids not in seen_node_ids:
                    seen_node_ids.add(p.node_ids)
                    all_paths.append(p)
        except Exception:
            pass

        # Sort by quality and cap
        all_paths.sort(key=lambda p: p.quality_score(), reverse=True)
        return all_paths[:max_paths]

    def shortest_path_length(self, start_id: str, goal_id: str) -> int:
        """Return the BFS hop-count from *start_id* to *goal_id*.

        Parameters
        ----------
        start_id : str
            Source node.
        goal_id : str
            Target node.

        Returns
        -------
        int
            Hop count, or -1 if unreachable or no space set.
        """
        if self._space is None:
            return -1
        path = self._finder.find_path_bfs(start_id, goal_id, self._space)
        if path.is_empty():
            return -1
        return path.length() - 1

    def is_reachable(self, start_id: str, goal_id: str) -> bool:
        """Check whether *goal_id* is reachable from *start_id*.

        Parameters
        ----------
        start_id : str
            Source node.
        goal_id : str
            Target node.

        Returns
        -------
        bool
            ``True`` if a path exists.
        """
        return self.shortest_path_length(start_id, goal_id) >= 0

    def navigation_summary(self) -> str:
        """Return a comprehensive human-readable summary of the navigator.

        Returns
        -------
        str
            Multi-line report covering space stats, condition, and history.
        """
        lines: list[str] = [
            "┌─────────────────────────────────────────────┐",
            "│          TheoryNavigator Summary            │",
            "└─────────────────────────────────────────────┘",
        ]

        if self._space is not None:
            lines += [
                f"  Space ID         : {self._space.space_id}",
                f"  Nodes            : {self._space.node_count()}",
                f"  Edges            : {self._space.edge_count()}",
            ]
        else:
            lines.append("  Space            : <not set>")

        if self._condition is not None:
            lines += [
                f"  Condition ID     : {self._condition.condition_id}",
                f"  Condition label  : {self._condition.label}",
            ]
        else:
            lines.append("  Condition        : <not set>")

        lines += [
            f"  Cache size       : {self._cache.size()}",
            f"  Cache hit rate   : {self._cache.hit_rate():.1%}",
            "",
            self._history.summary(),
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        start_id: str,
        goal_id: str,
        algorithm: NavigationAlgorithm,
        purpose: str,
    ) -> NavigationPath:
        """Route a navigation request to the correct algorithm implementation."""
        space = self._space
        assert space is not None

        if algorithm == NavigationAlgorithm.BFS:
            return self._finder.find_path_bfs(start_id, goal_id, space)

        if algorithm == NavigationAlgorithm.DFS:
            return self._finder.find_path_dfs(start_id, goal_id, space)

        if algorithm == NavigationAlgorithm.RANDOM_WALK:
            return self._finder.find_path_by_strategy(
                start_id, goal_id, space, NavigationStrategy.RANDOM_WALK
            )

        if algorithm in (
            NavigationAlgorithm.BEAM_SEARCH,
            NavigationAlgorithm.PURPOSE_GUIDED,
        ):
            return self._guided.guided_search(start_id, goal_id, space)

        if algorithm == NavigationAlgorithm.DIVERSE_PATHS:
            paths = self._diverse_finder.find_k_paths(start_id, goal_id, space, k=1)
            if paths:
                return paths[0]
            from jugeo.ideation.theory_navigation.path_finding import _path_id
            return NavigationPath(
                path_id=_path_id(start_id, goal_id),
                node_ids=(),
                start_id=start_id,
                goal_id=goal_id,
                purpose=purpose,
                total_cost=math.inf,
                purpose_alignment=0.0,
                strategy=NavigationStrategy.BREADTH_FIRST,
                created_at=time.time(),
            )

        # Default: A*
        return self._finder.find_path_astar(start_id, goal_id, space, self._heuristic)


# ---------------------------------------------------------------------------
# MapBuilder – space construction utilities
# ---------------------------------------------------------------------------


class MapBuilder:
    """Utility class for constructing and maintaining ``TheorySpace`` graphs.

    Wraps ``SpaceConstructor``, ``SpaceIndexer``, and ``IncrementalSpaceUpdater``
    from s01 to provide a convenient map-building interface.

    Parameters
    ----------
    config : SpaceConstructionConfig | None, optional
        Construction configuration.  Uses default settings when ``None``.
    """

    def __init__(self, config: SpaceConstructionConfig | None = None) -> None:
        self._config = config or SpaceConstructionConfig()
        self._constructor = SpaceConstructor(self._config)
        self._updater = IncrementalSpaceUpdater()
        self._extractor = NodeExtractor()
        self._edge_builder = EdgeBuilder(self._config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_from_dicts(
        self,
        raw_nodes: list[dict[str, Any]],
    ) -> tuple[TheorySpace, SpaceIndexer]:
        """Build a ``TheorySpace`` from a list of raw node dictionaries.

        Each dictionary should contain at minimum a ``node_id`` key; other
        fields are used to populate ``TheoryNode`` attributes and compute
        similarity edges.

        Parameters
        ----------
        raw_nodes : list[dict]
            Raw node data.

        Returns
        -------
        tuple[TheorySpace, SpaceIndexer]
            Constructed space and its search index.
        """
        space = self._constructor.build(raw_nodes)
        indexer = SpaceIndexer(space)
        indexer.build_index()
        return space, indexer

    def build_from_texts(
        self,
        descriptions: list[str],
    ) -> tuple[TheorySpace, SpaceIndexer]:
        """Build a ``TheorySpace`` from plain-text node descriptions.

        Each string becomes a node whose description is the text itself.
        Node IDs are generated from content hashes.  Similarity edges are
        added based on lexical overlap.

        Parameters
        ----------
        descriptions : list[str]
            Free-text descriptions.

        Returns
        -------
        tuple[TheorySpace, SpaceIndexer]
            Constructed space and index.
        """
        raw_nodes: list[dict[str, Any]] = []
        for i, text in enumerate(descriptions):
            words = text.strip().split()
            name = " ".join(words[:5]) if words else f"node-{i}"
            import hashlib
            node_id = "n" + hashlib.sha1(text.encode()).hexdigest()[:10]
            raw_nodes.append(
                {
                    "node_id": node_id,
                    "name": name,
                    "description": text,
                    "purpose_alignment": 0.5,
                }
            )
        return self.build_from_dicts(raw_nodes)

    def augment_map(
        self,
        space: TheorySpace,
        new_nodes: list[dict[str, Any]],
    ) -> TheorySpace:
        """Incrementally add *new_nodes* to an existing *space*.

        Parameters
        ----------
        space : TheorySpace
            Base space to extend.
        new_nodes : list[dict]
            Additional node data to integrate.

        Returns
        -------
        TheorySpace
            Updated space (the same object is mutated and returned).
        """
        for raw in new_nodes:
            node = self._extractor.extract(raw)
            if node is not None:
                self._updater.add_node(space, node)

        # Re-compute edges for newly added nodes
        new_node_objects = [
            space.get_node(r["node_id"])
            for r in new_nodes
            if "node_id" in r and space.has_node(r["node_id"])
        ]
        for node in new_node_objects:
            if node is None:
                continue
            edges = self._edge_builder.build_edges_for(node, space)
            for src_id, dst_id in edges:
                space.add_edge(src_id, dst_id)

        return space

    def rebuild_index(self, space: TheorySpace) -> SpaceIndexer:
        """Re-create the search index for *space*.

        Parameters
        ----------
        space : TheorySpace
            Space to index.

        Returns
        -------
        SpaceIndexer
            Fresh index.
        """
        indexer = SpaceIndexer(space)
        indexer.build_index()
        return indexer

    def validate_map(self, space: TheorySpace) -> list[str]:
        """Validate a theory space and return a list of error messages.

        Checks performed:
        - Space contains at least one node
        - All edge endpoints exist as nodes
        - No self-loops
        - Node IDs are non-empty strings

        Parameters
        ----------
        space : TheorySpace
            Space to validate.

        Returns
        -------
        list[str]
            Error messages (empty list means valid).
        """
        errors: list[str] = []

        if space.node_count() == 0:
            errors.append("Space contains no nodes.")

        for src_id, neighbor_ids in space.edges.items():
            if not space.has_node(src_id):
                errors.append(f"Edge source '{src_id}' not in node set.")
            for dst_id in neighbor_ids:
                if not space.has_node(dst_id):
                    errors.append(
                        f"Edge target '{dst_id}' (from '{src_id}') not in node set."
                    )
                if src_id == dst_id:
                    errors.append(f"Self-loop detected at node '{src_id}'.")

        for node_id, node in space.nodes.items():
            if not node_id:
                errors.append("Empty node ID found.")
            if node_id != node.node_id:
                errors.append(
                    f"Node registry mismatch: key '{node_id}' ≠ node.node_id '{node.node_id}'."
                )

        return errors

    def map_report(self, space: TheorySpace) -> str:
        """Return a multi-line map construction report.

        Parameters
        ----------
        space : TheorySpace
            Space to report on.

        Returns
        -------
        str
            Formatted report.
        """
        errors = self.validate_map(space)
        maturity_dist: dict[str, int] = defaultdict(int)
        alignment_sum = 0.0
        for node in space.nodes.values():
            maturity_dist[node.maturity.value] += 1
            alignment_sum += node.purpose_alignment

        avg_alignment = alignment_sum / space.node_count() if space.node_count() else 0.0
        avg_degree = (
            (2 * space.edge_count()) / space.node_count()
            if space.node_count() > 0
            else 0.0
        )

        lines: list[str] = [
            "╔══════════════════════════════════════╗",
            "║         Map Construction Report      ║",
            "╚══════════════════════════════════════╝",
            f"  Space ID         : {space.space_id}",
            f"  Nodes            : {space.node_count()}",
            f"  Edges            : {space.edge_count()}",
            f"  Avg degree       : {avg_degree:.2f}",
            f"  Avg alignment    : {avg_alignment:.4f}",
            "",
            "  Maturity distribution:",
        ]
        for maturity, count in sorted(maturity_dist.items()):
            pct = 100.0 * count / space.node_count() if space.node_count() else 0.0
            lines.append(f"    {maturity:12s} : {count:4d} ({pct:5.1f}%)")

        lines.append("")
        if errors:
            lines.append(f"  ⚠ Validation errors ({len(errors)}):")
            for err in errors[:10]:
                lines.append(f"    • {err}")
            if len(errors) > 10:
                lines.append(f"    … and {len(errors) - 10} more")
        else:
            lines.append("  ✓ Validation passed — no errors found.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# NavigationOptimizer – path and config optimisation
# ---------------------------------------------------------------------------


class NavigationOptimizer:
    """Optimise paths and navigator configurations.

    Provides tools for post-hoc path improvement, beam-width tuning, and
    similarity-threshold calibration.
    """

    def __init__(self) -> None:
        self._finder = PathFinder()
        self._evaluator = PathEvaluator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def optimize_path(
        self,
        path: NavigationPath,
        space: TheorySpace,
        condition: PurposeCondition | None = None,
    ) -> NavigationPath:
        """Attempt to find a shorter or better-aligned alternative to *path*.

        For each consecutive triple (a, b, c) in the path, checks whether a
        direct edge a → c exists and replaces b with the shortcut if it
        improves quality.

        Parameters
        ----------
        path : NavigationPath
            Original path to optimise.
        space : TheorySpace
            Theory graph.
        condition : PurposeCondition | None, optional
            Condition for re-evaluating alignment after shortcutting.

        Returns
        -------
        NavigationPath
            Optimised path (may be identical to input if no improvements found).
        """
        if path.is_empty() or path.length() <= 2:
            return path

        evaluator = PathEvaluator(condition)
        node_ids = list(path.node_ids)
        improved = True

        while improved:
            improved = False
            i = 0
            while i < len(node_ids) - 2:
                a = node_ids[i]
                c = node_ids[i + 2]
                # Check if direct edge a→c exists
                if space.is_connected(a, c):
                    # Remove the intermediate node
                    candidate_ids = node_ids[:i + 1] + node_ids[i + 2:]
                    candidate_path = self._build_path_from_ids(
                        tuple(candidate_ids),
                        path.start_id,
                        path.goal_id,
                        path.strategy,
                        path.purpose,
                        space,
                    )
                    original_metrics = evaluator.evaluate(path, space)
                    candidate_metrics = evaluator.evaluate(candidate_path, space)
                    if candidate_metrics["final_score"] >= original_metrics["final_score"]:
                        node_ids = candidate_ids
                        improved = True
                        # Don't increment i; re-check this position
                        continue
                i += 1

        if tuple(node_ids) == path.node_ids:
            return path

        return self._build_path_from_ids(
            tuple(node_ids),
            path.start_id,
            path.goal_id,
            path.strategy,
            path.purpose,
            space,
        )

    def optimize_beam_width(
        self,
        space: TheorySpace,
        condition: PurposeCondition,
        sample_queries: list[tuple[str, str]],
    ) -> int:
        """Find the optimal beam width by testing multiple settings.

        Tests beam widths [3, 5, 10, 20] on *sample_queries* and returns the
        width that achieves the best average path quality.

        Parameters
        ----------
        space : TheorySpace
            Theory graph.
        condition : PurposeCondition
            Purpose condition for guided search.
        sample_queries : list[tuple[str, str]]
            List of (start_id, goal_id) pairs for evaluation.

        Returns
        -------
        int
            Optimal beam width.
        """
        if not sample_queries:
            return 5

        candidate_widths = [3, 5, 10, 20]
        searcher = PurposeGuidedSearch(condition)
        evaluator = PathEvaluator(condition)
        best_width = 5
        best_score = -1.0

        for bw in candidate_widths:
            scores: list[float] = []
            for start_id, goal_id in sample_queries:
                if not space.has_node(start_id) or not space.has_node(goal_id):
                    continue
                try:
                    p = searcher.guided_search(start_id, goal_id, space, beam_width=bw)
                    metrics = evaluator.evaluate(p, space)
                    scores.append(metrics["final_score"])
                except Exception:
                    scores.append(0.0)

            avg = _safe_avg(scores)
            if avg > best_score:
                best_score = avg
                best_width = bw

        return best_width

    def tune_similarity_threshold(
        self,
        raw_nodes: list[dict[str, Any]],
        target_avg_degree: float = 5.0,
    ) -> float:
        """Binary search for the similarity threshold achieving *target_avg_degree*.

        Builds spaces with varying thresholds and measures the resulting
        average node degree.  Returns the threshold closest to the target.

        Parameters
        ----------
        raw_nodes : list[dict]
            Node data for space construction.
        target_avg_degree : float, optional
            Desired average number of neighbours per node.

        Returns
        -------
        float
            Similarity threshold in [0, 1].
        """
        if not raw_nodes:
            return 0.5

        builder = MapBuilder()
        lo, hi = 0.0, 1.0
        best_threshold = 0.5

        for _ in range(12):  # Binary search iterations
            mid = (lo + hi) / 2.0
            config = SpaceConstructionConfig(similarity_threshold=mid)
            b = MapBuilder(config)
            space, _ = b.build_from_dicts(raw_nodes)
            n = space.node_count()
            avg_degree = (2 * space.edge_count()) / n if n > 0 else 0.0

            best_threshold = mid
            if abs(avg_degree - target_avg_degree) < 0.1:
                break
            if avg_degree < target_avg_degree:
                hi = mid  # Need lower threshold to add more edges
            else:
                lo = mid  # Need higher threshold to remove edges

        return _clamp(best_threshold)

    def suggest_config(self, space: TheorySpace) -> SpaceConstructionConfig:
        """Analyse a space and suggest a construction configuration.

        Uses heuristics based on space size and density to recommend
        sensible construction settings.

        Parameters
        ----------
        space : TheorySpace
            Space to analyse.

        Returns
        -------
        SpaceConstructionConfig
            Suggested configuration.
        """
        n = space.node_count()
        e = space.edge_count()
        avg_degree = (2 * e) / n if n > 0 else 0.0
        avg_alignment = _safe_avg(
            [node.purpose_alignment for node in space.nodes.values()]
        )

        # Tune threshold based on density
        if avg_degree < 2.0:
            threshold = 0.3  # Low threshold → more edges
        elif avg_degree > 10.0:
            threshold = 0.7  # High threshold → fewer edges
        else:
            threshold = 0.5

        # Tune max_nodes based on space size
        max_nodes = max(100, n * 2)

        return SpaceConstructionConfig(
            similarity_threshold=threshold,
            max_nodes=max_nodes,
            min_purpose_alignment=max(0.1, avg_alignment - 0.2),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_path_from_ids(
        self,
        node_ids: tuple[str, ...],
        start_id: str,
        goal_id: str,
        strategy: NavigationStrategy,
        purpose: str,
        space: TheorySpace,
    ) -> NavigationPath:
        """Construct a ``NavigationPath`` from a sequence of node IDs."""
        from jugeo.ideation.theory_navigation.path_finding import _path_id

        total_cost = float(len(node_ids) - 1)
        alignment_sum = 0.0
        for nid in node_ids:
            node = space.get_node(nid)
            if node is not None:
                alignment_sum += node.purpose_alignment
        avg_alignment = alignment_sum / len(node_ids) if node_ids else 0.0

        return NavigationPath(
            path_id=_path_id(start_id, goal_id),
            node_ids=node_ids,
            start_id=start_id,
            goal_id=goal_id,
            purpose=purpose,
            total_cost=total_cost,
            purpose_alignment=avg_alignment,
            strategy=strategy,
            created_at=time.time(),
        )


# ---------------------------------------------------------------------------
# NavigationBenchmark – performance benchmarking
# ---------------------------------------------------------------------------


class NavigationBenchmark:
    """Benchmark navigation algorithms and space configurations.

    Provides tools for comparing algorithms on held-out queries, comparing
    space-construction configurations, and profiling space structure.
    """

    def __init__(self) -> None:
        self._evaluator = PathEvaluator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def benchmark_algorithms(
        self,
        space: TheorySpace,
        queries: list[tuple[str, str]],
        condition: PurposeCondition | None = None,
    ) -> dict[str, Any]:
        """Run all algorithms on *queries* and collect timing/quality metrics.

        Parameters
        ----------
        space : TheorySpace
            Graph used for all queries.
        queries : list[tuple[str, str]]
            List of (start_id, goal_id) pairs.
        condition : PurposeCondition | None, optional
            Purpose condition for guided algorithms.

        Returns
        -------
        dict[str, Any]
            Per-algorithm results with timing and quality statistics.
        """
        navigator = TheoryNavigator(space, condition)
        evaluator = PathEvaluator(condition)

        results: dict[str, Any] = {}

        for algo in NavigationAlgorithm:
            if algo == NavigationAlgorithm.DIVERSE_PATHS:
                continue  # Handled separately

            algo_results: list[dict[str, Any]] = []
            for start_id, goal_id in queries:
                if not space.has_node(start_id) or not space.has_node(goal_id):
                    continue
                try:
                    t0 = time.time()
                    path = navigator.navigate(
                        start_id,
                        goal_id,
                        algorithm=algo,
                        use_cache=False,
                    )
                    elapsed = _elapsed_ms(t0)
                    metrics = evaluator.evaluate(path, space)
                    algo_results.append(
                        {
                            "query": f"{start_id}→{goal_id}",
                            "duration_ms": elapsed,
                            "found": not path.is_empty(),
                            "length": path.length(),
                            "quality": metrics["final_score"],
                            "total_cost": path.total_cost,
                        }
                    )
                except Exception as exc:
                    algo_results.append(
                        {
                            "query": f"{start_id}→{goal_id}",
                            "error": str(exc),
                            "found": False,
                        }
                    )

            durations = [r["duration_ms"] for r in algo_results if "duration_ms" in r]
            qualities = [r["quality"] for r in algo_results if "quality" in r]
            found_count = sum(1 for r in algo_results if r.get("found", False))

            results[algo.value] = {
                "total_queries": len(algo_results),
                "found_count": found_count,
                "success_rate": found_count / len(algo_results) if algo_results else 0.0,
                "avg_duration_ms": _safe_avg(durations),
                "max_duration_ms": max(durations) if durations else 0.0,
                "min_duration_ms": min(durations) if durations else 0.0,
                "avg_quality": _safe_avg(qualities),
                "per_query": algo_results,
                "is_complete": algo.is_complete(),
                "is_optimal": algo.is_optimal(),
            }

        return results

    def compare_configs(
        self,
        raw_nodes: list[dict[str, Any]],
        configs: list[SpaceConstructionConfig],
    ) -> dict[str, Any]:
        """Build spaces with different configurations and compare quality.

        Parameters
        ----------
        raw_nodes : list[dict]
            Source data for all space constructions.
        configs : list[SpaceConstructionConfig]
            List of configurations to compare.

        Returns
        -------
        dict[str, Any]
            Per-config statistics (node count, edge count, avg degree, etc.).
        """
        comparison: dict[str, Any] = {}

        for i, config in enumerate(configs):
            label = f"config_{i}"
            builder = MapBuilder(config)
            try:
                space, indexer = builder.build_from_dicts(raw_nodes)
                n = space.node_count()
                e = space.edge_count()
                avg_degree = (2 * e) / n if n > 0 else 0.0
                alignments = [nd.purpose_alignment for nd in space.nodes.values()]
                validation_errors = builder.validate_map(space)

                comparison[label] = {
                    "config_index": i,
                    "similarity_threshold": getattr(config, "similarity_threshold", "N/A"),
                    "node_count": n,
                    "edge_count": e,
                    "avg_degree": avg_degree,
                    "avg_alignment": _safe_avg(alignments),
                    "validation_errors": len(validation_errors),
                    "index_size": indexer.size() if hasattr(indexer, "size") else "N/A",
                }
            except Exception as exc:
                comparison[label] = {"error": str(exc)}

        return comparison

    def profile_space(self, space: TheorySpace) -> dict[str, Any]:
        """Compute structural statistics about a theory space.

        Estimates graph diameter via BFS from random start nodes, computes
        degree distribution, purpose alignment distribution, and maturity
        distribution.

        Parameters
        ----------
        space : TheorySpace
            Space to profile.

        Returns
        -------
        dict[str, Any]
            Comprehensive stats dictionary.
        """
        n = space.node_count()
        e = space.edge_count()

        if n == 0:
            return {"node_count": 0, "edge_count": 0}

        degrees = [len(space.get_neighbors(nid)) for nid in space.nodes]
        alignments = [nd.purpose_alignment for nd in space.nodes.values()]
        maturity_dist: dict[str, int] = defaultdict(int)
        for nd in space.nodes.values():
            maturity_dist[nd.maturity.value] += 1

        # Estimate diameter: BFS from up to 5 random nodes
        diameter_estimate = self._estimate_diameter(space, samples=5)

        # Isolated nodes (degree 0)
        isolated = sum(1 for d in degrees if d == 0)

        # Connectivity: fraction of node pairs reachable from a sample
        connectivity_estimate = self._estimate_connectivity(space, samples=10)

        return {
            "node_count": n,
            "edge_count": e,
            "avg_degree": _safe_avg(degrees),
            "max_degree": max(degrees) if degrees else 0,
            "min_degree": min(degrees) if degrees else 0,
            "isolated_nodes": isolated,
            "diameter_estimate": diameter_estimate,
            "connectivity_estimate": connectivity_estimate,
            "avg_purpose_alignment": _safe_avg(alignments),
            "max_purpose_alignment": max(alignments) if alignments else 0.0,
            "min_purpose_alignment": min(alignments) if alignments else 0.0,
            "maturity_distribution": dict(maturity_dist),
        }

    def benchmark_report(self, results: dict[str, Any]) -> str:
        """Format benchmark results as a multi-line report.

        Parameters
        ----------
        results : dict[str, Any]
            Output from ``benchmark_algorithms``.

        Returns
        -------
        str
            Formatted report string.
        """
        lines: list[str] = [
            "╔════════════════════════════════════════════════════════╗",
            "║               Navigation Benchmark Report              ║",
            "╚════════════════════════════════════════════════════════╝",
        ]

        header = (
            f"  {'Algorithm':20s} {'Queries':>7s} {'Found%':>7s} "
            f"{'AvgMs':>8s} {'MaxMs':>8s} {'AvgQ':>7s} {'Opt':>4s} {'Cmp':>4s}"
        )
        lines.append(header)
        lines.append("  " + "─" * 72)

        for algo_name, stats in sorted(results.items()):
            if "error" in stats:
                lines.append(f"  {algo_name:20s} ERROR: {stats['error']}")
                continue
            found_pct = f"{stats.get('success_rate', 0.0):.0%}"
            avg_ms = f"{stats.get('avg_duration_ms', 0.0):.1f}"
            max_ms = f"{stats.get('max_duration_ms', 0.0):.1f}"
            avg_q = f"{stats.get('avg_quality', 0.0):.3f}"
            queries = str(stats.get("total_queries", 0))
            opt = "✓" if stats.get("is_optimal", False) else "✗"
            cmp = "✓" if stats.get("is_complete", False) else "✗"
            lines.append(
                f"  {algo_name:20s} {queries:>7s} {found_pct:>7s} "
                f"{avg_ms:>8s} {max_ms:>8s} {avg_q:>7s} {opt:>4s} {cmp:>4s}"
            )

        lines += [
            "  " + "─" * 72,
            "  Opt = cost-optimal (A* only)   Cmp = complete (guaranteed to find)",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _estimate_diameter(
        self,
        space: TheorySpace,
        samples: int = 5,
    ) -> int:
        """Estimate graph diameter by BFS from random start nodes."""
        node_ids = list(space.nodes.keys())
        if len(node_ids) < 2:
            return 0

        sample_starts = random.sample(node_ids, min(samples, len(node_ids)))
        max_dist = 0
        finder = PathFinder({"max_nodes_expanded": 5000})

        for start in sample_starts:
            # BFS to find farthest reachable node
            from collections import deque as _deque
            dist: dict[str, int] = {start: 0}
            q: _deque[str] = _deque([start])
            while q:
                cur = q.popleft()
                for nb in space.get_neighbors(cur):
                    if nb.node_id not in dist:
                        dist[nb.node_id] = dist[cur] + 1
                        q.append(nb.node_id)
            if dist:
                max_dist = max(max_dist, max(dist.values()))

        return max_dist

    def _estimate_connectivity(
        self,
        space: TheorySpace,
        samples: int = 10,
    ) -> float:
        """Estimate connectivity as fraction of reachable pairs in a sample."""
        node_ids = list(space.nodes.keys())
        if len(node_ids) < 2:
            return 1.0

        n_samples = min(samples, len(node_ids))
        sampled = random.sample(node_ids, n_samples)
        reachable_pairs = 0
        total_pairs = 0
        finder = PathFinder({"max_nodes_expanded": 200, "max_depth": 20})

        for i in range(len(sampled)):
            for j in range(i + 1, len(sampled)):
                total_pairs += 1
                p = finder.find_path_bfs(sampled[i], sampled[j], space)
                if not p.is_empty():
                    reachable_pairs += 1

        return reachable_pairs / total_pairs if total_pairs > 0 else 0.0


# ---------------------------------------------------------------------------
# NavigationDiagnostics – health checks and diagnostics
# ---------------------------------------------------------------------------


class NavigationDiagnostics:
    """Comprehensive health-check and diagnostic tools for navigation.

    Analyses spaces, paths, and navigators to surface issues and provide
    actionable insights.
    """

    def __init__(self) -> None:
        self._evaluator = PathEvaluator()
        self._benchmark = NavigationBenchmark()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diagnose_space(self, space: TheorySpace) -> str:
        """Check a theory space for structural issues.

        Looks for isolated nodes, very low purpose-alignment nodes, large
        weakly-connected components, and edge anomalies.

        Parameters
        ----------
        space : TheorySpace
            Space to diagnose.

        Returns
        -------
        str
            Multi-line diagnostic report.
        """
        issues: list[str] = []
        warnings: list[str] = []
        info: list[str] = []

        n = space.node_count()
        e = space.edge_count()

        if n == 0:
            issues.append("CRITICAL: Space is empty (no nodes).")
            return self._format_report("Space Diagnostics", issues, warnings, info)

        # Degree analysis
        degree_map = {nid: len(space.get_neighbors(nid)) for nid in space.nodes}
        isolated = [nid for nid, d in degree_map.items() if d == 0]
        low_degree = [nid for nid, d in degree_map.items() if 0 < d <= 1]
        avg_degree = _safe_avg(list(degree_map.values()))

        if isolated:
            issues.append(
                f"Isolated nodes (degree 0): {len(isolated)} "
                f"({100*len(isolated)/n:.1f}% of nodes)"
            )
        if len(low_degree) > n * 0.3:
            warnings.append(
                f"High fraction of low-degree nodes: {len(low_degree)} "
                f"({100*len(low_degree)/n:.1f}% have degree ≤ 1)"
            )

        if avg_degree < 1.0:
            issues.append(f"Very low average degree ({avg_degree:.2f}); space may be too sparse.")
        elif avg_degree < 2.0:
            warnings.append(f"Low average degree ({avg_degree:.2f}); consider lower similarity threshold.")
        else:
            info.append(f"Average degree: {avg_degree:.2f} (healthy)")

        # Purpose alignment analysis
        alignments = [nd.purpose_alignment for nd in space.nodes.values()]
        avg_align = _safe_avg(alignments)
        low_align_count = sum(1 for a in alignments if a < 0.2)
        if low_align_count > n * 0.5:
            warnings.append(
                f"Many low-alignment nodes: {low_align_count} ({100*low_align_count/n:.1f}%)"
            )
        info.append(f"Average purpose alignment: {avg_align:.4f}")

        # Maturity distribution
        maturity_dist: dict[str, int] = defaultdict(int)
        for nd in space.nodes.values():
            maturity_dist[nd.maturity.value] += 1
        nascent_count = maturity_dist.get(NodeMaturity.NASCENT.value, 0)
        if nascent_count > n * 0.7:
            warnings.append(
                f"High fraction of NASCENT nodes ({100*nascent_count/n:.1f}%); "
                "space may lack mature theory coverage."
            )

        # Connected-component estimation
        components = self._count_components(space)
        if components > 5:
            issues.append(
                f"Space appears fragmented: ~{components} disconnected components."
            )
        elif components > 1:
            warnings.append(f"~{components} weakly-connected components detected.")
        else:
            info.append("Space appears well-connected (1 component).")

        info.append(f"Total: {n} nodes, {e} edges.")
        return self._format_report("Space Diagnostics", issues, warnings, info)

    def diagnose_path(
        self,
        path: NavigationPath,
        space: TheorySpace,
    ) -> str:
        """Check a navigation path for validity and quality issues.

        Parameters
        ----------
        path : NavigationPath
            Path to diagnose.
        space : TheorySpace
            Graph the path traverses.

        Returns
        -------
        str
            Multi-line diagnostic report.
        """
        issues: list[str] = []
        warnings: list[str] = []
        info: list[str] = []

        if path.is_empty():
            issues.append("Path is empty — no route was found.")
            return self._format_report("Path Diagnostics", issues, warnings, info)

        # Check all nodes exist
        missing = [nid for nid in path.node_ids if not space.has_node(nid)]
        if missing:
            issues.append(f"Path references {len(missing)} node(s) not in space: {missing[:5]}")

        # Check edge continuity
        broken_edges: list[str] = []
        for i in range(len(path.node_ids) - 1):
            a, b = path.node_ids[i], path.node_ids[i + 1]
            if not space.is_connected(a, b):
                broken_edges.append(f"{a}→{b}")
        if broken_edges:
            issues.append(
                f"Path has {len(broken_edges)} broken edge(s): {broken_edges[:3]}"
            )

        # Check for cycles
        seen: set[str] = set()
        cycles: list[str] = []
        for nid in path.node_ids:
            if nid in seen:
                cycles.append(nid)
            seen.add(nid)
        if cycles:
            warnings.append(f"Path contains {len(cycles)} repeated node(s) (cycles).")

        metrics = self._evaluator.evaluate(path, space)
        q = metrics["final_score"]
        if q < 0.3:
            warnings.append(f"Low quality score ({q:.3f}); consider alternative routes.")
        elif q >= 0.7:
            info.append(f"Good quality score ({q:.3f}).")
        else:
            info.append(f"Moderate quality score ({q:.3f}).")

        cov = metrics["coverage"]
        if cov < 0.3:
            warnings.append(f"Low coverage of mature nodes ({cov:.1%}).")
        else:
            info.append(f"Mature node coverage: {cov:.1%}.")

        lp = metrics["length_penalty"]
        if lp > 0.7:
            warnings.append(f"High length penalty ({lp:.3f}); path may be unnecessarily long.")

        info.append(f"Length: {path.length()} nodes, cost: {path.total_cost:.4f}.")
        return self._format_report("Path Diagnostics", issues, warnings, info)

    def diagnose_navigation(self, navigator: TheoryNavigator) -> str:
        """Comprehensive health check of a ``TheoryNavigator`` instance.

        Parameters
        ----------
        navigator : TheoryNavigator
            Navigator to inspect.

        Returns
        -------
        str
            Multi-line health report.
        """
        issues: list[str] = []
        warnings: list[str] = []
        info: list[str] = []

        # Check space
        if navigator._space is None:
            issues.append("Navigator has no space set — navigation will fail.")
        else:
            space_diag = self.diagnose_space(navigator._space)
            space_has_errors = "CRITICAL" in space_diag or "ERROR" in space_diag
            if space_has_errors:
                issues.append("Navigator space has critical issues (see space diagnostics).")
            else:
                info.append(
                    f"Space OK: {navigator._space.node_count()} nodes, "
                    f"{navigator._space.edge_count()} edges."
                )

        # Check condition
        if navigator._condition is None:
            warnings.append("No purpose condition set; navigation will use neutral scoring.")
        else:
            info.append(
                f"Purpose condition set: '{navigator._condition.label}' "
                f"(weight={navigator._condition.weight:.3f})."
            )

        # Cache stats
        cs = navigator._cache.stats()
        hr = cs["hit_rate"]
        if cs["size"] == 0 and (cs["hits"] + cs["misses"]) > 20:
            warnings.append("Cache is empty despite many lookups — may indicate cache invalidation issue.")
        info.append(
            f"Cache: {cs['size']}/{cs['max_size']} entries, hit rate {hr:.1%}."
        )

        # History stats
        hist_dict = navigator._history.to_dict()
        total = hist_dict.get("total", 0)
        if total == 0:
            info.append("No navigation history yet.")
        else:
            algo_stats = navigator._history.algorithm_stats()
            overall_success = _safe_avg(
                [s["success_rate"] for s in algo_stats.values()]
            )
            if overall_success < 0.5:
                warnings.append(
                    f"Low overall navigation success rate ({overall_success:.0%}); "
                    "check space connectivity."
                )
            info.append(
                f"History: {total} navigations, "
                f"avg success rate {overall_success:.0%}."
            )

        return self._format_report("Navigator Health Check", issues, warnings, info)

    def copilot_summary(
        self,
        space: TheorySpace,
        recent_paths: list[NavigationPath],
    ) -> str:
        """Generate a human-friendly Copilot-style summary.

        Parameters
        ----------
        space : TheorySpace
            Current theory space.
        recent_paths : list[NavigationPath]
            Recently navigated paths to summarise.

        Returns
        -------
        str
            Concise natural-language summary.
        """
        n = space.node_count()
        e = space.edge_count()
        avg_degree = (2 * e) / n if n > 0 else 0.0

        maturity_dist: dict[str, int] = defaultdict(int)
        for nd in space.nodes.values():
            maturity_dist[nd.maturity.value] += 1
        mature_count = (
            maturity_dist.get(NodeMaturity.MATURE.value, 0)
            + maturity_dist.get(NodeMaturity.ESTABLISHED.value, 0)
        )
        mature_pct = 100.0 * mature_count / n if n > 0 else 0.0

        alignments = [nd.purpose_alignment for nd in space.nodes.values()]
        avg_align = _safe_avg(alignments)

        evaluator = PathEvaluator()
        path_summaries: list[str] = []
        for p in recent_paths[:5]:
            metrics = evaluator.evaluate(p, space)
            q = metrics["final_score"]
            status = "✓ good" if q >= 0.6 else "~ ok" if q >= 0.3 else "✗ poor"
            path_summaries.append(
                f"  • {p.start_id[:8]}→{p.goal_id[:8]} "
                f"len={p.length()} q={q:.2f} [{status}]"
            )

        lines: list[str] = [
            "┌─────────────────────────────────────────┐",
            "│     Theory Navigation Copilot Summary   │",
            "└─────────────────────────────────────────┘",
            f"",
            f"  The theory space contains {n} nodes and {e} edges",
            f"  (avg degree {avg_degree:.1f}).  {mature_pct:.0f}% of nodes are",
            f"  mature/established.  Average purpose alignment is {avg_align:.3f}.",
            f"",
        ]

        if avg_degree < 2.0:
            lines.append("  ⚠ Space is sparse — consider lowering the similarity threshold.")
        elif avg_degree > 15.0:
            lines.append("  ⚠ Space is dense — consider raising the similarity threshold.")
        else:
            lines.append("  ✓ Space density looks healthy.")

        if mature_pct < 20.0:
            lines.append("  ⚠ Few mature nodes — theory coverage may be incomplete.")
        else:
            lines.append("  ✓ Good mature-node coverage for reliable navigation.")

        if avg_align < 0.3:
            lines.append("  ⚠ Low purpose alignment — review node relevance.")
        else:
            lines.append("  ✓ Purpose alignment is satisfactory.")

        if path_summaries:
            lines += ["", "  Recent paths:"] + path_summaries
        else:
            lines += ["", "  No recent paths to report."]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _count_components(self, space: TheorySpace) -> int:
        """Estimate the number of weakly-connected components via BFS."""
        unvisited = set(space.nodes.keys())
        components = 0
        while unvisited:
            components += 1
            start = next(iter(unvisited))
            queue = [start]
            while queue:
                cur = queue.pop()
                if cur not in unvisited:
                    continue
                unvisited.discard(cur)
                for nb in space.get_neighbors(cur):
                    if nb.node_id in unvisited:
                        queue.append(nb.node_id)
        return components

    def _format_report(
        self,
        title: str,
        issues: list[str],
        warnings: list[str],
        info: list[str],
    ) -> str:
        """Format a diagnostic report from categorised messages."""
        border = "═" * (len(title) + 6)
        lines: list[str] = [
            border,
            f"  {title}",
            border,
        ]
        if issues:
            lines.append(f"  ❌ Issues ({len(issues)}):")
            for msg in issues:
                lines.append(f"      • {msg}")
        else:
            lines.append("  ❌ No critical issues.")

        if warnings:
            lines.append(f"  ⚠ Warnings ({len(warnings)}):")
            for msg in warnings:
                lines.append(f"      • {msg}")
        else:
            lines.append("  ⚠ No warnings.")

        if info:
            lines.append(f"  ℹ Info:")
            for msg in info:
                lines.append(f"      • {msg}")

        lines.append(border)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "NavigationAlgorithm",
    "NavigationHistory",
    "TheoryNavigator",
    "MapBuilder",
    "NavigationOptimizer",
    "NavigationBenchmark",
    "NavigationDiagnostics",
]
