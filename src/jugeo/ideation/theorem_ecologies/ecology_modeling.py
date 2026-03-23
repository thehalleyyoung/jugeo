"""Theorem ecology modeling (theory2.tex Ch61 §1).

Module layout::

    EcologyConfig        – configuration for ecology modeling
    TheoremNode          – node in the ecology graph
    EcologyBuilder       – builds ecology models from theorem collections
    DependencyMapper     – maps dependencies between theorems/lemmas
    HealthCalculator     – calculates ecology health metrics
    DiversityAnalyzer    – analyzes diversity in theorem ecosystems
    EcologyModeler       – orchestrates full ecology modeling

Theory Background
=================

A *theorem ecology* is a structured collection of theorems and supporting
lemmas together with their dependency relationships.  Drawing on metaphors
from biological ecology, we model the health of such a collection using
metrics analogous to biodiversity, connectance, and resilience.

The core intuition is that a healthy theorem ecology should exhibit:

* **Adequate size** — enough nodes to support meaningful inference chains.
* **Good connectivity** — theorems should be reachable from each other via
  dependency paths of bounded depth.
* **Structural diversity** — a mixture of deep chains, broad fanouts, and
  isolated clusters avoids brittleness.
* **Balance** — the ratio of lemmas to theorems should lie in a productive
  range; too few lemmas means theorems cannot be efficiently reused, too many
  means the collection is cluttered.

The ``EcologyHealth`` enum from ``theorem_ecologies.models`` maps the
continuous health score to a categorical tier for human consumption.

The ``DiversityAnalyzer`` uses Shannon entropy over binned distributions to
compute diversity indices, following standard ecological diversity measures
(Shannon 1948, Simpson 1949).  The ``HealthCalculator`` combines four
sub-scores via configurable weights from ``EcologyConfig``.

Dependency analysis in ``DependencyMapper`` implements both Kosaraju's
algorithm for strongly-connected-component detection and a BFS-based depth
computation.  Cycle detection uses iterative DFS with an explicit colour map
(WHITE = unvisited, GREY = in progress, BLACK = finished) to avoid Python
recursion-limit issues on large graphs.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.ideation.theorem_ecologies.models import (
    TheoremEcology,
    LemmaPortfolio,
    CompoundingEffect,
    EcologicalDynamic,
    PortfolioOptimization,
    EcologyHealth,
    DynamicType,
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenise *text* into lowercase alphabetic words of length >= 2."""
    return frozenset(w for w in re.split(r"[^a-z]+", text.lower()) if len(w) >= 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _entropy(counts: Sequence[int]) -> float:
    """Shannon entropy (bits) of a discrete distribution given raw counts."""
    total = sum(counts)
    if total == 0:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    return -sum(p * math.log2(p) for p in probs)


def _bfs_depth(graph: dict[str, list[str]], roots: list[str]) -> dict[str, int]:
    """BFS to compute minimum depth from any root node in a (possibly cyclic) graph.

    Parameters
    ----------
    graph:
        Adjacency list: node_id -> list of neighbour node_ids.
    roots:
        Nodes assigned depth 0.

    Returns
    -------
    dict mapping each reachable node to its minimum depth from any root.
    """
    depths: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque((r, 0) for r in roots)
    while queue:
        node, depth = queue.popleft()
        if node in depths:
            continue
        depths[node] = depth
        for neighbour in graph.get(node, []):
            if neighbour not in depths:
                queue.append((neighbour, depth + 1))
    return depths


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# EcologyConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EcologyConfig:
    """Configuration for ecology modeling algorithms.

    All weight fields must sum to approximately 1.0.  ``__post_init__``
    validates this constraint with a tolerance of 0.05.

    Attributes
    ----------
    min_health_threshold:
        Ecologies below this score are considered unhealthy.
    max_dependency_depth:
        Dependency chains beyond this depth trigger a penalty.
    diversity_bins:
        Number of histogram bins for entropy-based diversity analysis.
    health_weights:
        Contribution of each sub-score to the composite health score.
    growth_rate:
        Expected fractional growth per modeling cycle.
    decay_rate:
        Fractional decay applied to unused nodes per cycle.
    symbiosis_threshold:
        Node-similarity above this value triggers symbiosis detection.
    competition_threshold:
        Node-similarity above this value in overlapping domains triggers
        competition detection.
    """

    min_health_threshold: float = 0.3
    max_dependency_depth: int = 10
    diversity_bins: int = 5
    health_weights: dict[str, float] = field(
        default_factory=lambda: {
            "connectivity": 0.3,
            "diversity": 0.3,
            "depth": 0.2,
            "size": 0.2,
        }
    )
    growth_rate: float = 0.1
    decay_rate: float = 0.05
    symbiosis_threshold: float = 0.6
    competition_threshold: float = 0.8

    def __post_init__(self) -> None:
        total = sum(self.health_weights.values())
        if abs(total - 1.0) > 0.05:
            raise ValueError(
                f"health_weights must sum to ~1.0; got {total:.4f}"
            )

    def effective_health_weight(self, component: str) -> float:
        """Return the configured weight for *component*, defaulting to 0.0."""
        return self.health_weights.get(component, 0.0)

    def with_growth_rate(self, rate: float) -> EcologyConfig:
        """Return a copy with *growth_rate* replaced."""
        return replace(self, growth_rate=_clamp(rate, 0.0, 1.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_health_threshold": self.min_health_threshold,
            "max_dependency_depth": self.max_dependency_depth,
            "diversity_bins": self.diversity_bins,
            "health_weights": dict(self.health_weights),
            "growth_rate": self.growth_rate,
            "decay_rate": self.decay_rate,
            "symbiosis_threshold": self.symbiosis_threshold,
            "competition_threshold": self.competition_threshold,
        }


# ---------------------------------------------------------------------------
# TheoremNode
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremNode:
    """A single node in the theorem ecology graph.

    Nodes are immutable value objects.  Mutations return new instances via
    ``replace()``.  The ``similarity_to`` method uses Jaccard distance over
    the node's tag vocabulary, enabling content-based clustering.

    Attributes
    ----------
    node_id:
        Unique identifier (usually matches the theorem/lemma identifier in the
        source text).
    label:
        Human-readable name.
    node_type:
        Either ``"theorem"`` or ``"lemma"``.
    weight:
        Importance weight; higher means more central to the ecology.
    tags:
        Vocabulary tags for content-based similarity.
    depth:
        Depth in the dependency DAG (0 = root, higher = more derived).
    in_degree:
        Number of incoming dependency edges (how many nodes depend on this).
    out_degree:
        Number of outgoing dependency edges (how many this node depends on).
    """

    node_id: str
    label: str
    node_type: str
    weight: float = 1.0
    tags: tuple[str, ...] = ()
    depth: int = 0
    in_degree: int = 0
    out_degree: int = 0

    def is_theorem(self) -> bool:
        """Return True if this node represents a theorem."""
        return self.node_type == "theorem"

    def is_lemma(self) -> bool:
        """Return True if this node represents a lemma."""
        return self.node_type == "lemma"

    def is_root(self) -> bool:
        """Return True if this node has no incoming edges (no dependents)."""
        return self.in_degree == 0

    def is_leaf(self) -> bool:
        """Return True if this node has no outgoing edges (no dependencies)."""
        return self.out_degree == 0

    def centrality(self) -> float:
        """Combined in/out-degree centrality, normalised to [0, 1].

        Uses a sigmoid transformation so that very high-degree nodes do not
        dominate the score.
        """
        raw = (self.in_degree + self.out_degree) / 2.0
        return 1.0 / (1.0 + math.exp(-raw / 5.0)) * 2.0 - 1.0  # sigmoid shifted to [0,1]

    def tag_vector(self) -> frozenset[str]:
        """Return the tag set as a frozenset for set-algebraic operations."""
        return frozenset(self.tags)

    def similarity_to(self, other: TheoremNode) -> float:
        """Jaccard similarity between this node's tags and *other*'s tags."""
        a = self.tag_vector()
        b = other.tag_vector()
        return _jaccard(a, b)

    def with_depth(self, depth: int) -> TheoremNode:
        """Return a copy with *depth* replaced."""
        return replace(self, depth=max(0, depth))

    def with_degrees(self, in_deg: int, out_deg: int) -> TheoremNode:
        """Return a copy with in/out-degree replaced."""
        return replace(self, in_degree=max(0, in_deg), out_degree=max(0, out_deg))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "node_type": self.node_type,
            "weight": self.weight,
            "tags": list(self.tags),
            "depth": self.depth,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
        }


# ---------------------------------------------------------------------------
# EcologyBuilder
# ---------------------------------------------------------------------------

class EcologyBuilder:
    """Builds ``TheoremEcology`` instances from collections of theorems and lemmas.

    The builder validates inputs, computes health and diversity scores, and
    produces immutable ``TheoremEcology`` snapshots.  All mutations return new
    instances; the builder itself is stateless with respect to the ecologies
    it creates.

    Parameters
    ----------
    config:
        Configuration controlling scoring thresholds and weights.
    """

    def __init__(self, config: EcologyConfig = EcologyConfig()) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        name: str,
        theorems: list[str],
        lemmas: list[str],
        dependencies: dict[str, list[str]],
    ) -> TheoremEcology:
        """Build a ``TheoremEcology`` from raw lists.

        Parameters
        ----------
        name:
            Human-readable name for the ecology.
        theorems:
            List of theorem node IDs.
        lemmas:
            List of lemma node IDs.
        dependencies:
            Mapping from node_id to list of dependency node_ids.

        Returns
        -------
        TheoremEcology
            Frozen snapshot with computed health and diversity scores.
        """
        errors = self.validate_inputs(theorems, lemmas, dependencies)
        if errors:
            raise ValueError("Invalid ecology inputs: " + "; ".join(errors))

        dep_tuples: dict[str, tuple[str, ...]] = {
            k: tuple(v) for k, v in dependencies.items()
        }
        health = self._compute_health(theorems, lemmas, dependencies, self._config)
        diversity = self._compute_diversity(theorems, lemmas, dependencies)

        return TheoremEcology(
            name=name,
            theorem_ids=tuple(theorems),
            lemma_ids=tuple(lemmas),
            dependencies=dep_tuples,
            health_score=health,
            diversity_index=diversity,
        )

    def build_from_nodes(
        self, name: str, nodes: list[TheoremNode]
    ) -> TheoremEcology:
        """Build a ``TheoremEcology`` from a list of ``TheoremNode`` instances.

        Dependencies are inferred from the nodes' ``out_degree``; since full
        adjacency information is not stored in ``TheoremNode``, only the node
        taxonomy (theorem vs lemma) is extracted here.  Call
        ``EcologyModeler.node_analysis`` afterwards to enrich the result.
        """
        theorems = [n.node_id for n in nodes if n.is_theorem()]
        lemmas = [n.node_id for n in nodes if n.is_lemma()]
        return self.build(name, theorems, lemmas, {})

    def incremental_add(
        self,
        ecology: TheoremEcology,
        new_theorems: list[str] | None = None,
        new_lemmas: list[str] | None = None,
        new_deps: dict[str, list[str]] | None = None,
    ) -> TheoremEcology:
        """Add nodes to an existing ecology and recompute scores.

        Duplicate IDs are silently ignored.
        """
        existing_theorems = list(ecology.theorem_ids)
        existing_lemmas = list(ecology.lemma_ids)
        existing_deps: dict[str, list[str]] = {
            k: list(v) for k, v in ecology.dependencies.items()
        }

        if new_theorems:
            for t in new_theorems:
                if t not in existing_theorems:
                    existing_theorems.append(t)
        if new_lemmas:
            for lm in new_lemmas:
                if lm not in existing_lemmas:
                    existing_lemmas.append(lm)
        if new_deps:
            for k, v in new_deps.items():
                if k in existing_deps:
                    combined = list(existing_deps[k])
                    for dep in v:
                        if dep not in combined:
                            combined.append(dep)
                    existing_deps[k] = combined
                else:
                    existing_deps[k] = list(v)

        return self.build(ecology.name, existing_theorems, existing_lemmas, existing_deps)

    def merge(
        self,
        ecology_a: TheoremEcology,
        ecology_b: TheoremEcology,
        new_name: str,
    ) -> TheoremEcology:
        """Merge two ecologies into one, deduplicating nodes."""
        all_theorems = list(dict.fromkeys(
            list(ecology_a.theorem_ids) + list(ecology_b.theorem_ids)
        ))
        all_lemmas = list(dict.fromkeys(
            list(ecology_a.lemma_ids) + list(ecology_b.lemma_ids)
        ))
        merged_deps: dict[str, list[str]] = {}
        for src, deps in ecology_a.dependencies.items():
            merged_deps[src] = list(deps)
        for src, deps in ecology_b.dependencies.items():
            if src in merged_deps:
                existing = set(merged_deps[src])
                merged_deps[src] = list(existing | set(deps))
            else:
                merged_deps[src] = list(deps)
        return self.build(new_name, all_theorems, all_lemmas, merged_deps)

    def validate_inputs(
        self,
        theorems: list[str],
        lemmas: list[str],
        dependencies: dict[str, list[str]],
    ) -> list[str]:
        """Validate raw inputs and return a list of error messages."""
        errors: list[str] = []
        all_ids = set(theorems) | set(lemmas)

        # Check for duplicates within each list
        if len(theorems) != len(set(theorems)):
            errors.append("Duplicate theorem IDs detected")
        if len(lemmas) != len(set(lemmas)):
            errors.append("Duplicate lemma IDs detected")

        # Check for overlap between theorems and lemmas
        overlap = set(theorems) & set(lemmas)
        if overlap:
            errors.append(f"IDs appear in both theorems and lemmas: {overlap}")

        # Validate dependency keys are known nodes
        for src, deps in dependencies.items():
            if src not in all_ids:
                errors.append(f"Dependency source '{src}' not in theorem/lemma lists")
            for dep in deps:
                if dep not in all_ids:
                    errors.append(
                        f"Dependency target '{dep}' (from '{src}') not in theorem/lemma lists"
                    )

        return errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_health(
        theorems: list[str],
        lemmas: list[str],
        dependencies: dict[str, list[str]],
        config: EcologyConfig,
    ) -> float:
        """Compute a composite health score in [0, 1]."""
        n_total = len(theorems) + len(lemmas)
        if n_total == 0:
            return 0.0

        # Connectivity: fraction of nodes that have at least one edge
        nodes_with_edges = set()
        for src, deps in dependencies.items():
            if deps:
                nodes_with_edges.add(src)
                for d in deps:
                    nodes_with_edges.add(d)
        connectivity = len(nodes_with_edges) / n_total

        # Diversity: entropy of theorem vs lemma proportions
        theorem_frac = len(theorems) / n_total
        lemma_frac = len(lemmas) / n_total
        if theorem_frac > 0 and lemma_frac > 0:
            diversity = _entropy([len(theorems), len(lemmas)]) / 1.0  # max is 1 bit
        else:
            diversity = 0.0

        # Depth: penalise if max dependency depth exceeds config
        max_depth = 0
        for src, deps in dependencies.items():
            # simple DFS depth estimate
            stack = [(src, 0)]
            visited: set[str] = set()
            while stack:
                node, d = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                max_depth = max(max_depth, d)
                for child in dependencies.get(node, []):
                    stack.append((child, d + 1))
        depth_score = 1.0 - _clamp(max_depth / max(config.max_dependency_depth, 1), 0.0, 1.0)

        # Size: sigmoid-normalised size score
        size_score = 2.0 / (1.0 + math.exp(-n_total / 20.0)) - 1.0

        w = config.health_weights
        health = (
            w.get("connectivity", 0.3) * connectivity
            + w.get("diversity", 0.3) * diversity
            + w.get("depth", 0.2) * depth_score
            + w.get("size", 0.2) * size_score
        )
        return _clamp(health)

    @staticmethod
    def _compute_diversity(
        theorems: list[str],
        lemmas: list[str],
        dependencies: dict[str, list[str]],
    ) -> float:
        """Compute a diversity index based on graph structure."""
        all_nodes = theorems + lemmas
        n = len(all_nodes)
        if n == 0:
            return 0.0

        # Out-degree distribution entropy
        out_degrees = [len(dependencies.get(node, [])) for node in all_nodes]
        max_degree = max(out_degrees) if out_degrees else 0
        if max_degree == 0:
            degree_entropy = 0.0
        else:
            bins: list[int] = [0] * (max_degree + 1)
            for d in out_degrees:
                bins[d] += 1
            degree_entropy = _entropy(bins) / max(math.log2(max_degree + 1), 1.0)

        # Type entropy (theorem vs lemma balance)
        if theorems and lemmas:
            type_entropy = _entropy([len(theorems), len(lemmas)])  # 0..1 bit
        else:
            type_entropy = 0.0

        return _clamp(0.6 * degree_entropy + 0.4 * type_entropy)


# ---------------------------------------------------------------------------
# DependencyMapper
# ---------------------------------------------------------------------------

class DependencyMapper:
    """Analyses dependency relationships between theorem/lemma nodes.

    This class provides graph-theoretic tools for understanding the structure
    of a theorem ecology's dependency DAG.  Key algorithms:

    * **Topological sort** — Kahn's algorithm (BFS-based), raises ``ValueError``
      for cyclic graphs.
    * **SCC detection** — Kosaraju's two-pass algorithm.
    * **Cycle detection** — iterative DFS with three-colour marking.
    * **Ancestor/descendant sets** — iterative BFS.
    """

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def build_adjacency(
        self, deps: dict[str, tuple[str, ...]]
    ) -> dict[str, list[str]]:
        """Convert a frozen-tuple dependency map to a mutable adjacency list."""
        return {k: list(v) for k, v in deps.items()}

    def reverse_adjacency(
        self, adj: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        """Return the transpose (reversed) adjacency list."""
        rev: dict[str, list[str]] = defaultdict(list)
        for src, neighbours in adj.items():
            for nb in neighbours:
                rev[nb].append(src)
        return dict(rev)

    # ------------------------------------------------------------------
    # Degree and depth
    # ------------------------------------------------------------------

    def compute_node_depths(
        self,
        adj: dict[str, list[str]],
        all_nodes: list[str],
    ) -> dict[str, int]:
        """Compute minimum BFS depth from root nodes (nodes with in-degree 0)."""
        in_deg = self.compute_in_degrees(adj, all_nodes)
        roots = [n for n in all_nodes if in_deg.get(n, 0) == 0]
        if not roots:
            roots = all_nodes[:1]  # fallback: pick first node
        return _bfs_depth(adj, roots)

    def compute_in_degrees(
        self,
        adj: dict[str, list[str]],
        all_nodes: list[str],
    ) -> dict[str, int]:
        """Compute in-degree (number of incoming edges) for each node."""
        in_deg: dict[str, int] = {n: 0 for n in all_nodes}
        for src, neighbours in adj.items():
            for nb in neighbours:
                if nb in in_deg:
                    in_deg[nb] += 1
        return in_deg

    def compute_out_degrees(
        self,
        adj: dict[str, list[str]],
        all_nodes: list[str],
    ) -> dict[str, int]:
        """Compute out-degree (number of outgoing edges) for each node."""
        return {n: len(adj.get(n, [])) for n in all_nodes}

    # ------------------------------------------------------------------
    # Structural analysis
    # ------------------------------------------------------------------

    def find_cycles(
        self, adj: dict[str, list[str]]
    ) -> list[list[str]]:
        """Detect all simple cycles using iterative DFS with three-colour marking.

        Returns a list of cycles, where each cycle is represented as the list
        of nodes on the cycle path (starting and ending at the same node is
        implied).  For large graphs this returns only a representative subset.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in adj}
        parent: dict[str, str | None] = {n: None for n in adj}
        cycles: list[list[str]] = []

        for start in list(adj.keys()):
            if colour.get(start, WHITE) != WHITE:
                continue
            # Iterative DFS
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    colour[node] = BLACK
                    continue
                if colour.get(node, WHITE) == GREY:
                    # Back edge found — reconstruct cycle
                    cycle = [node]
                    cur = parent.get(node)
                    while cur and cur != node:
                        cycle.append(cur)
                        cur = parent.get(cur)
                    if cur:
                        cycle.append(cur)
                    cycles.append(list(reversed(cycle)))
                    continue
                if colour.get(node, WHITE) == BLACK:
                    continue
                colour[node] = GREY
                stack.append((node, True))  # mark as leaving later
                for nb in adj.get(node, []):
                    if colour.get(nb, WHITE) == WHITE:
                        parent[nb] = node
                        stack.append((nb, False))
                    elif colour.get(nb, WHITE) == GREY:
                        # Back edge — record cycle immediately
                        cycle = [nb, node]
                        cur = parent.get(node)
                        while cur and cur != nb:
                            cycle.append(cur)
                            cur = parent.get(cur)
                        cycles.append(list(reversed(cycle)))
        return cycles

    def critical_paths(
        self,
        adj: dict[str, list[str]],
        all_nodes: list[str],
    ) -> list[list[str]]:
        """Find the longest dependency paths (critical paths) in the DAG.

        Returns paths sorted by descending length.  If the graph has cycles
        the method falls back to returning empty paths.
        """
        try:
            topo = self.topological_sort(adj, all_nodes)
        except ValueError:
            return []

        dist: dict[str, int] = {n: 0 for n in all_nodes}
        pred: dict[str, str | None] = {n: None for n in all_nodes}

        for node in topo:
            for nb in adj.get(node, []):
                if dist.get(node, 0) + 1 > dist.get(nb, 0):
                    dist[nb] = dist[node] + 1
                    pred[nb] = node

        if not dist:
            return []

        # Reconstruct path from the node with maximum distance
        end = max(dist, key=lambda n: dist[n])
        path: list[str] = []
        cur: str | None = end
        while cur is not None:
            path.append(cur)
            cur = pred.get(cur)
        return [list(reversed(path))]

    def ancestors(
        self, node_id: str, adj: dict[str, list[str]]
    ) -> frozenset[str]:
        """Return all ancestors of *node_id* in the dependency DAG."""
        rev = self.reverse_adjacency(adj)
        visited: set[str] = set()
        queue: deque[str] = deque([node_id])
        while queue:
            cur = queue.popleft()
            for nb in rev.get(cur, []):
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        visited.discard(node_id)
        return frozenset(visited)

    def descendants(
        self, node_id: str, adj: dict[str, list[str]]
    ) -> frozenset[str]:
        """Return all descendants of *node_id* in the dependency DAG."""
        visited: set[str] = set()
        queue: deque[str] = deque([node_id])
        while queue:
            cur = queue.popleft()
            for nb in adj.get(cur, []):
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        visited.discard(node_id)
        return frozenset(visited)

    def strongly_connected_components(
        self, adj: dict[str, list[str]]
    ) -> list[list[str]]:
        """Compute SCCs using Kosaraju's two-pass algorithm (iterative).

        Returns a list of SCCs, each represented as a list of node IDs.
        Single-node SCCs are included only when the node has a self-loop.
        """
        all_nodes = list(adj.keys())
        visited: set[str] = set()
        finish_order: list[str] = []

        # Pass 1: DFS on original graph, record finish order
        for start in all_nodes:
            if start in visited:
                continue
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    finish_order.append(node)
                    continue
                if node in visited:
                    continue
                visited.add(node)
                stack.append((node, True))
                for nb in adj.get(node, []):
                    if nb not in visited:
                        stack.append((nb, False))

        # Pass 2: DFS on reversed graph in reverse finish order
        rev = self.reverse_adjacency(adj)
        visited2: set[str] = set()
        sccs: list[list[str]] = []

        for start in reversed(finish_order):
            if start in visited2:
                continue
            scc: list[str] = []
            stack2: list[str] = [start]
            while stack2:
                node = stack2.pop()
                if node in visited2:
                    continue
                visited2.add(node)
                scc.append(node)
                for nb in rev.get(node, []):
                    if nb not in visited2:
                        stack2.append(nb)
            sccs.append(scc)
        return sccs

    def topological_sort(
        self,
        adj: dict[str, list[str]],
        all_nodes: list[str],
    ) -> list[str]:
        """Topological sort via Kahn's algorithm.

        Raises ``ValueError`` if the graph contains a cycle.
        """
        in_deg = self.compute_in_degrees(adj, all_nodes)
        queue: deque[str] = deque(n for n in all_nodes if in_deg[n] == 0)
        result: list[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for nb in adj.get(node, []):
                in_deg[nb] -= 1
                if in_deg[nb] == 0:
                    queue.append(nb)

        if len(result) != len(all_nodes):
            raise ValueError("Cycle detected; topological sort is not possible.")
        return result


# ---------------------------------------------------------------------------
# HealthCalculator
# ---------------------------------------------------------------------------

class HealthCalculator:
    """Calculates ecology health metrics from a ``TheoremEcology`` snapshot.

    The calculator decomposes the overall health score into four independent
    sub-scores — connectivity, size, depth, and balance — and combines them
    via the configurable weights in ``EcologyConfig.health_weights``.

    Parameters
    ----------
    config:
        Configuration controlling scoring thresholds and weights.
    """

    def __init__(self, config: EcologyConfig = EcologyConfig()) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(self, ecology: TheoremEcology) -> float:
        """Compute the overall health score in [0, 1]."""
        components = self.composite_health(ecology)
        w = self._config.health_weights
        return _clamp(
            sum(w.get(k, 0.0) * v for k, v in components.items())
        )

    def connectivity_score(self, ecology: TheoremEcology) -> float:
        """Fraction of nodes connected by at least one dependency edge."""
        all_nodes = set(ecology.all_node_ids)
        n = len(all_nodes)
        if n == 0:
            return 0.0
        connected: set[str] = set()
        for src, deps in ecology.dependencies.items():
            if deps:
                connected.add(src)
                connected.update(deps)
        return _clamp(len(connected & all_nodes) / n)

    def size_score(self, ecology: TheoremEcology) -> float:
        """Sigmoid-normalised size score relative to an ideal ecology of 20 nodes."""
        n = ecology.size
        return _clamp(2.0 / (1.0 + math.exp(-(n - 5) / 10.0)) - 1.0)

    def depth_score(self, ecology: TheoremEcology) -> float:
        """Score penalising ecologies that are too shallow or too deep.

        The ideal depth is around max_dependency_depth / 2.  Ecologies with
        depth near 0 or very large receive lower scores.
        """
        adj: dict[str, list[str]] = {
            k: list(v) for k, v in ecology.dependencies.items()
        }
        all_nodes = list(ecology.all_node_ids)
        if not all_nodes:
            return 0.0
        mapper = DependencyMapper()
        depths = mapper.compute_node_depths(adj, all_nodes)
        if not depths:
            return 0.0
        max_depth = max(depths.values(), default=0)
        ideal = self._config.max_dependency_depth / 2.0
        # Gaussian-like score peaked at ideal depth
        score = math.exp(-0.5 * ((max_depth - ideal) / max(ideal, 1)) ** 2)
        return _clamp(score)

    def balance_score(self, ecology: TheoremEcology) -> float:
        """Score based on the lemma-to-theorem ratio.

        An ideal ratio is between 0.5 and 3.0 (more lemmas than theorems but
        not excessively so).  A ratio of 1.0 scores highest.
        """
        n_th = len(ecology.theorem_ids)
        n_lm = len(ecology.lemma_ids)
        if n_th == 0:
            return 0.0
        ratio = n_lm / n_th
        # Penalise deviation from ratio=1 using an exponential decay
        score = math.exp(-0.3 * abs(math.log(max(ratio, 0.01))))
        return _clamp(score)

    def fragility_score(self, ecology: TheoremEcology) -> float:
        """Detect structural fragility: fraction of nodes with no alternatives.

        A node is *fragile* if it is the only node connecting two otherwise
        disconnected components (an articulation point approximation).  Here we
        use a simpler proxy: nodes with in_degree == 1 and out_degree >= 2.
        """
        adj: dict[str, list[str]] = {k: list(v) for k, v in ecology.dependencies.items()}
        all_nodes = list(ecology.all_node_ids)
        mapper = DependencyMapper()
        in_degs = mapper.compute_in_degrees(adj, all_nodes)
        out_degs = mapper.compute_out_degrees(adj, all_nodes)
        fragile = sum(
            1
            for n in all_nodes
            if in_degs.get(n, 0) == 1 and out_degs.get(n, 0) >= 2
        )
        n = len(all_nodes)
        if n == 0:
            return 1.0
        fragility = fragile / n
        return _clamp(1.0 - fragility)

    def composite_health(self, ecology: TheoremEcology) -> dict[str, float]:
        """Return a dict of all component health scores."""
        return {
            "connectivity": self.connectivity_score(ecology),
            "diversity": self._diversity_proxy(ecology),
            "depth": self.depth_score(ecology),
            "size": self.size_score(ecology),
            "balance": self.balance_score(ecology),
            "fragility": self.fragility_score(ecology),
        }

    def health_category(self, health_score: float) -> EcologyHealth:
        """Map a continuous health score to the discrete ``EcologyHealth`` tier."""
        if health_score >= 0.85:
            return EcologyHealth.EXCELLENT
        elif health_score >= 0.65:
            return EcologyHealth.GOOD
        elif health_score >= 0.45:
            return EcologyHealth.FAIR
        elif health_score >= 0.25:
            return EcologyHealth.POOR
        else:
            return EcologyHealth.CRITICAL

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _diversity_proxy(self, ecology: TheoremEcology) -> float:
        """Quick diversity proxy without a full ``DiversityAnalyzer`` call."""
        return ecology.diversity_index

    @staticmethod
    def _normalize_size(count: int) -> float:
        """Sigmoid normalisation: maps count to (0, 1) with inflection at 10."""
        return 1.0 / (1.0 + math.exp(-(count - 10) / 5.0))


# ---------------------------------------------------------------------------
# DiversityAnalyzer
# ---------------------------------------------------------------------------

class DiversityAnalyzer:
    """Analyses diversity in theorem ecosystems using entropy-based metrics.

    Diversity is measured along three axes:

    1. **Depth diversity** — entropy of the depth-level histogram.
    2. **Type diversity** — balance between theorems and lemmas.
    3. **Connectivity diversity** — entropy of the degree distribution.

    These are combined into an overall diversity index using a weighted sum.

    Parameters
    ----------
    bins:
        Number of histogram bins for continuous distributions.
    """

    def __init__(self, bins: int = 5) -> None:
        self._bins = max(1, bins)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, ecology: TheoremEcology) -> float:
        """Return overall diversity index in [0, 1]."""
        report = self.diversity_report(ecology)
        # Weighted combination
        weights = {"depth_diversity": 0.35, "type_diversity": 0.3,
                   "connectivity_diversity": 0.35}
        total = sum(weights.get(k, 0.0) * v for k, v in report.items()
                    if k in weights)
        return _clamp(total)

    def depth_diversity(self, ecology: TheoremEcology) -> float:
        """Shannon entropy of the depth-level distribution, normalised to [0, 1]."""
        adj: dict[str, list[str]] = {k: list(v) for k, v in ecology.dependencies.items()}
        all_nodes = list(ecology.all_node_ids)
        if not all_nodes:
            return 0.0
        mapper = DependencyMapper()
        depths = mapper.compute_node_depths(adj, all_nodes)
        if not depths:
            return 0.0
        max_depth = max(depths.values(), default=0)
        if max_depth == 0:
            return 0.0
        bins: list[int] = [0] * (max_depth + 1)
        for d in depths.values():
            bins[d] += 1
        raw_entropy = _entropy(bins)
        max_entropy = math.log2(max_depth + 1) if max_depth > 0 else 1.0
        return _clamp(raw_entropy / max_entropy)

    def type_diversity(self, ecology: TheoremEcology) -> float:
        """Balance score between theorems and lemmas (1.0 = perfect balance)."""
        n_th = len(ecology.theorem_ids)
        n_lm = len(ecology.lemma_ids)
        total = n_th + n_lm
        if total == 0:
            return 0.0
        if n_th == 0 or n_lm == 0:
            return 0.0
        entropy = _entropy([n_th, n_lm])  # max 1.0 bit when equal
        return _clamp(entropy)

    def connectivity_diversity(self, ecology: TheoremEcology) -> float:
        """Entropy of the out-degree distribution, normalised to [0, 1]."""
        all_nodes = list(ecology.all_node_ids)
        if not all_nodes:
            return 0.0
        adj: dict[str, list[str]] = {k: list(v) for k, v in ecology.dependencies.items()}
        out_degs = [len(adj.get(n, [])) for n in all_nodes]
        max_deg = max(out_degs, default=0)
        if max_deg == 0:
            return 0.0
        bins: list[int] = [0] * (max_deg + 1)
        for d in out_degs:
            bins[d] += 1
        raw = _entropy(bins)
        normaliser = math.log2(max_deg + 1) if max_deg > 0 else 1.0
        return _clamp(raw / normaliser)

    def tag_diversity(self, nodes: list[TheoremNode]) -> float:
        """Entropy over tag frequency distribution across a list of nodes."""
        if not nodes:
            return 0.0
        tag_counts: dict[str, int] = defaultdict(int)
        for node in nodes:
            for tag in node.tags:
                tag_counts[tag] += 1
        if not tag_counts:
            return 0.0
        raw = _entropy(list(tag_counts.values()))
        max_ent = math.log2(len(tag_counts)) if len(tag_counts) > 1 else 1.0
        return _clamp(raw / max_ent)

    def structural_diversity(self, ecology: TheoremEcology) -> float:
        """Combine depth, type, and connectivity diversity into one index."""
        dd = self.depth_diversity(ecology)
        td = self.type_diversity(ecology)
        cd = self.connectivity_diversity(ecology)
        return _clamp(0.35 * dd + 0.3 * td + 0.35 * cd)

    def compare(
        self,
        ecology_a: TheoremEcology,
        ecology_b: TheoremEcology,
    ) -> float:
        """Jaccard-like comparison of the node-ID sets of two ecologies."""
        a_nodes = frozenset(ecology_a.all_node_ids)
        b_nodes = frozenset(ecology_b.all_node_ids)
        return _jaccard(a_nodes, b_nodes)

    def diversity_report(self, ecology: TheoremEcology) -> dict[str, float]:
        """Return all diversity sub-scores as a dictionary."""
        return {
            "depth_diversity": self.depth_diversity(ecology),
            "type_diversity": self.type_diversity(ecology),
            "connectivity_diversity": self.connectivity_diversity(ecology),
            "structural_diversity": self.structural_diversity(ecology),
        }


# ---------------------------------------------------------------------------
# EcologyModeler
# ---------------------------------------------------------------------------

class EcologyModeler:
    """Orchestrates the full theorem ecology modeling pipeline.

    The modeler maintains an in-memory registry of all modeled ecologies and
    delegates to ``EcologyBuilder``, ``DependencyMapper``, ``HealthCalculator``,
    and ``DiversityAnalyzer`` for each operation.

    Parameters
    ----------
    config:
        Configuration for all sub-components.
    """

    def __init__(self, config: EcologyConfig = EcologyConfig()) -> None:
        self._config = config
        self._builder = EcologyBuilder(config)
        self._mapper = DependencyMapper()
        self._health = HealthCalculator(config)
        self._diversity = DiversityAnalyzer(config.diversity_bins)
        self._ecologies: dict[str, TheoremEcology] = {}

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def model(
        self,
        name: str,
        theorems: list[str],
        lemmas: list[str],
        dependencies: dict[str, list[str]],
    ) -> TheoremEcology:
        """Build and register a new ecology."""
        ecology = self._builder.build(name, theorems, lemmas, dependencies)
        self._ecologies[ecology.ecology_id] = ecology
        return ecology

    def model_from_nodes(
        self, name: str, nodes: list[TheoremNode]
    ) -> TheoremEcology:
        """Build an ecology from ``TheoremNode`` instances and register it."""
        ecology = self._builder.build_from_nodes(name, nodes)
        self._ecologies[ecology.ecology_id] = ecology
        return ecology

    def update(
        self,
        ecology_id: str,
        new_theorems: list[str] | None = None,
        new_lemmas: list[str] | None = None,
        new_deps: dict[str, list[str]] | None = None,
    ) -> TheoremEcology:
        """Incrementally add to a registered ecology."""
        ecology = self._ecologies.get(ecology_id)
        if ecology is None:
            raise KeyError(f"No ecology with id '{ecology_id}'")
        updated = self._builder.incremental_add(ecology, new_theorems, new_lemmas, new_deps)
        self._ecologies[ecology_id] = updated
        return updated

    def get(self, ecology_id: str) -> TheoremEcology | None:
        """Return the ecology with the given ID, or None."""
        return self._ecologies.get(ecology_id)

    def remove(self, ecology_id: str) -> bool:
        """Remove an ecology from the registry.  Returns True if it existed."""
        if ecology_id in self._ecologies:
            del self._ecologies[ecology_id]
            return True
        return False

    def all_ecologies(self) -> list[TheoremEcology]:
        """Return all registered ecologies."""
        return list(self._ecologies.values())

    def find_healthy(self) -> list[TheoremEcology]:
        """Return ecologies with health_score >= min_health_threshold."""
        threshold = self._config.min_health_threshold
        return [e for e in self._ecologies.values() if e.health_score >= threshold]

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def assess_health(self, ecology_id: str) -> dict[str, float]:
        """Return composite health components for a registered ecology."""
        ecology = self._ecologies.get(ecology_id)
        if ecology is None:
            raise KeyError(f"No ecology with id '{ecology_id}'")
        return self._health.composite_health(ecology)

    def node_analysis(self, ecology: TheoremEcology) -> list[TheoremNode]:
        """Enrich each node in the ecology with computed depth and degree data."""
        adj: dict[str, list[str]] = {k: list(v) for k, v in ecology.dependencies.items()}
        all_node_ids = list(ecology.all_node_ids)
        depths = self._mapper.compute_node_depths(adj, all_node_ids)
        in_degs = self._mapper.compute_in_degrees(adj, all_node_ids)
        out_degs = self._mapper.compute_out_degrees(adj, all_node_ids)
        nodes: list[TheoremNode] = []
        for nid in all_node_ids:
            ntype = "theorem" if nid in ecology.theorem_ids else "lemma"
            tokens = _tokenize(nid)
            node = TheoremNode(
                node_id=nid,
                label=nid.replace("_", " ").title(),
                node_type=ntype,
                tags=tuple(sorted(tokens)),
                depth=depths.get(nid, 0),
                in_degree=in_degs.get(nid, 0),
                out_degree=out_degs.get(nid, 0),
            )
            nodes.append(node)
        return nodes

    def report(self, ecology_id: str) -> str:
        """Generate a multi-line human-readable report for a registered ecology."""
        ecology = self._ecologies.get(ecology_id)
        if ecology is None:
            return f"[EcologyModeler] No ecology found with id '{ecology_id}'"

        health_components = self._health.composite_health(ecology)
        tier = self._health.health_category(ecology.health_score)
        diversity_report = self._diversity.diversity_report(ecology)

        lines: list[str] = [
            f"=== Theorem Ecology Report ===",
            f"  Name:              {ecology.name}",
            f"  ID:                {ecology.ecology_id}",
            f"  Theorems:          {len(ecology.theorem_ids)}",
            f"  Lemmas:            {len(ecology.lemma_ids)}",
            f"  Total nodes:       {ecology.size}",
            f"  Dependencies:      {len(ecology.dependencies)}",
            f"  Health score:      {ecology.health_score:.4f} ({tier.value})",
            f"  Diversity index:   {ecology.diversity_index:.4f}",
            f"",
            f"--- Health Components ---",
        ]
        for k, v in sorted(health_components.items()):
            lines.append(f"  {k:<22} {v:.4f}")
        lines.append("")
        lines.append("--- Diversity Components ---")
        for k, v in sorted(diversity_report.items()):
            lines.append(f"  {k:<22} {v:.4f}")
        lines.append("")
        lines.append(f"  Created at: {_now_iso()}")
        return "\n".join(lines)

    def merge(
        self,
        ecology_id_a: str,
        ecology_id_b: str,
        new_name: str,
    ) -> TheoremEcology:
        """Merge two registered ecologies into a new one."""
        a = self._ecologies.get(ecology_id_a)
        b = self._ecologies.get(ecology_id_b)
        if a is None:
            raise KeyError(f"No ecology with id '{ecology_id_a}'")
        if b is None:
            raise KeyError(f"No ecology with id '{ecology_id_b}'")
        merged = self._builder.merge(a, b, new_name)
        self._ecologies[merged.ecology_id] = merged
        return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "EcologyConfig",
    "TheoremNode",
    "EcologyBuilder",
    "DependencyMapper",
    "HealthCalculator",
    "DiversityAnalyzer",
    "EcologyModeler",
]
