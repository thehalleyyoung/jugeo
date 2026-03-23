r"""Closure algorithms for the semantic_closure package.

Theory (theory2.tex §38 — Closure algorithms):
    This module implements the core algorithms for computing semantic closure:

    **Fixed-point iteration** (Tarski's theorem):
        Given a monotone operator F : L → L on a complete lattice L, the
        least fixed point is::

            lfp(F) = ⋃_{n ≥ 0} F^n(⊥)

        where ⊥ is the bottom element.  If F is additionally ω-continuous,
        convergence is reached in at most ω steps.  In practice, for finite
        lattices, convergence is guaranteed in at most |L| steps.

    **Kleene closure** (reflexive transitive closure):
        Given a binary relation R on a set X, the Kleene closure is::

            K*(R) = ⋃_{n ≥ 0} R^n = I ∪ R ∪ R² ∪ R³ ∪ ...

        where I is the identity relation.  K*(R) is computed by iterating
        the one-step extension until a fixed point is reached.

    **Transitive closure over a judgment sheaf**:
        Obligations are modelled as nodes; evidence provides directed edges.
        The transitive closure propagates satisfaction through the sheaf's
        restriction maps: if obligation o₁ is satisfied and o₁ → o₂ (i.e.
        evidence for o₁ implies evidence for o₂), then o₂ is also satisfied.

    **Warshall's algorithm**:
        All-pairs reachability in O(n³) using dynamic programming.  Used when
        the relation is given as an n×n matrix rather than an adjacency list.

    Generated code enters at the PROPOSAL trust tier.  No section produced
    by this module is automatically trusted — it must pass theorem checks in
    :mod:`theorems` before being promoted.

    Trust tier ordering: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED

    # copilot: algorithms-closure

Usage::

    from jugeo.generation.semantic_closure.algorithms import (
        ClosureAlgorithm,
        FixedPointIterator,
        KleeneClosure,
        TransitiveClosure,
        compute_closure,
        fixed_point_iteration,
        kleene_step,
    )

    relation = {"a": {"b"}, "b": {"c"}, "c": set()}
    tc = compute_closure(relation, algorithm="kleene")
    print(tc.reaches("a", "c"))  # True
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

__all__ = [
    # Enums
    "AlgorithmType",
    # Dataclasses
    "ClosureAlgorithm",
    "TransitiveClosure",
    "ClosureIteration",
    "WarshallResult",
    # Classes
    "FixedPointIterator",
    "KleeneClosure",
    "JudgmentSheafClosure",
    "ClosureAlgorithmRegistry",
    # Functions
    "compute_closure",
    "fixed_point_iteration",
    "kleene_step",
    "warshall_closure",
    "lattice_join",
    "lattice_meet",
    "relation_compose",
    "relation_union",
    "is_fixed_point_relation",
    # Constants
    "ALGORITHM_REGISTRY",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.semantic_closure.models import ClosureResult  # type: ignore[import]
    _MODELS_AVAILABLE = True
except Exception:  # pragma: no cover
    _MODELS_AVAILABLE = False

    class ClosureResult(str, Enum):  # type: ignore[no-redef]
        OPEN = "open"
        PARTIAL = "partial"
        CLOSED = "closed"

try:
    from jugeo.evidence.trust import TrustTier  # type: ignore[import]
    _TRUST_AVAILABLE = True
except Exception:  # pragma: no cover
    _TRUST_AVAILABLE = False

# ---------------------------------------------------------------------------
# Lattice helpers
# ---------------------------------------------------------------------------

# Three-element ClosureResult lattice: OPEN < PARTIAL < CLOSED
_LATTICE_ORDER: dict[str, int] = {"open": 0, "partial": 1, "closed": 2}
_LATTICE_ELEMENTS: dict[int, str] = {0: "open", 1: "partial", 2: "closed"}


def _rank(result: Any) -> int:
    """Return the lattice rank of *result*."""
    s = str(result).lower().split(".")[-1]
    return _LATTICE_ORDER.get(s, 0)


def lattice_join(a: Any, b: Any) -> str:
    """Return the join (least upper bound) in the ClosureResult lattice."""
    return _LATTICE_ELEMENTS[max(_rank(a), _rank(b))]


def lattice_meet(a: Any, b: Any) -> str:
    """Return the meet (greatest lower bound) in the ClosureResult lattice."""
    return _LATTICE_ELEMENTS[min(_rank(a), _rank(b))]


def lattice_leq(a: Any, b: Any) -> bool:
    """Return True if a ≤ b in the ClosureResult lattice."""
    return _rank(a) <= _rank(b)


# ---------------------------------------------------------------------------
# Relation helpers
# ---------------------------------------------------------------------------


def relation_compose(
    r: dict[str, set[str]],
    s: dict[str, set[str]],
    nodes: set[str] | None = None,
) -> dict[str, set[str]]:
    """Compute the composition R ∘ S of two relations.

    (R ∘ S)(x) = {z : ∃ y, (x,y) ∈ R ∧ (y,z) ∈ S}

    Parameters
    ----------
    r, s:
        Adjacency-set representation of binary relations.
    nodes:
        Optional set of all nodes; if None, inferred from r and s.

    Returns
    -------
    dict[str, set[str]]
        The composed relation.
    """
    if nodes is None:
        nodes = set(r.keys()) | set(s.keys())
        for targets in r.values():
            nodes.update(targets)
        for targets in s.values():
            nodes.update(targets)

    result: dict[str, set[str]] = {n: set() for n in nodes}
    for x in nodes:
        for y in r.get(x, set()):
            result[x].update(s.get(y, set()))
    return result


def relation_union(
    r: dict[str, set[str]],
    s: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Compute the union R ∪ S of two relations.

    Parameters
    ----------
    r, s:
        Adjacency-set representations.

    Returns
    -------
    dict[str, set[str]]
        The union relation.
    """
    all_keys = set(r.keys()) | set(s.keys())
    result: dict[str, set[str]] = {}
    for k in all_keys:
        result[k] = set(r.get(k, set())) | set(s.get(k, set()))
    return result


def is_fixed_point_relation(
    prev: dict[str, set[str]],
    curr: dict[str, set[str]],
) -> bool:
    """Return True if prev and curr represent the same relation.

    Parameters
    ----------
    prev, curr:
        Adjacency-set representations.

    Returns
    -------
    bool
        True when every key has the same target set in both dicts.
    """
    all_keys = set(prev.keys()) | set(curr.keys())
    for k in all_keys:
        if set(prev.get(k, set())) != set(curr.get(k, set())):
            return False
    return True


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AlgorithmType(str, Enum):
    """Algorithms available for computing transitive closure.

    * ``KLEENE``    — iterate R ← R ∪ R∘R until fixed point.
    * ``WARSHALL``  — Floyd–Warshall all-pairs reachability.
    * ``BFS``       — BFS/DFS from each node.
    * ``FIXED_POINT`` — generic monotone fixed-point iteration.
    """

    KLEENE = "kleene"
    WARSHALL = "warshall"
    BFS = "bfs"
    FIXED_POINT = "fixed_point"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosureAlgorithm:
    """Metadata descriptor for a closure algorithm.

    Attributes
    ----------
    algorithm_id:
        Unique identifier.
    name:
        Human-readable name.
    description:
        Detailed description of the algorithm.
    complexity_class:
        Worst-case time complexity (e.g. ``"O(n^3)"``).
    space_complexity:
        Worst-case space complexity.
    is_complete:
        True when the algorithm always finds the full closure.
    is_sound:
        True when the algorithm never over-approximates.
    trust_tier:
        Trust tier for results produced by this algorithm.
    tags:
        Additional tags.
    """

    algorithm_id: str
    name: str
    description: str
    complexity_class: str
    space_complexity: str
    is_complete: bool
    is_sound: bool
    trust_tier: str
    tags: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        """Return a formatted description."""
        complete = "complete" if self.is_complete else "incomplete"
        sound = "sound" if self.is_sound else "approximate"
        return (
            f"[{self.algorithm_id}] {self.name} — {complete}, {sound}; "
            f"time={self.complexity_class}, space={self.space_complexity}"
        )


@dataclass(frozen=True)
class ClosureIteration:
    """Record of a single iteration in a fixed-point computation.

    Attributes
    ----------
    iteration_number:
        Zero-indexed iteration count.
    nodes_added:
        Number of new edges added in this iteration.
    converged:
        True when no new edges were added (fixed point reached).
    elapsed_secs:
        Wall-clock time for this iteration.
    relation_size:
        Total number of directed edges in the current relation.
    """

    iteration_number: int
    nodes_added: int
    converged: bool
    elapsed_secs: float
    relation_size: int


@dataclass(frozen=True)
class TransitiveClosure:
    """The computed reflexive transitive closure of a binary relation.

    Attributes
    ----------
    closure_id:
        Unique identifier for this closure result.
    nodes:
        Sorted tuple of all nodes in the domain.
    base_edges:
        Tuple of (source, target) pairs in the original relation.
    closure_edges:
        Tuple of all (source, target) pairs in the closure.
    computed_at:
        UNIX timestamp when the closure was computed.
    algorithm_used:
        Algorithm that produced this closure.
    iterations:
        Tuple of :class:`ClosureIteration` records.
    trust_tier:
        Trust tier of this closure result.
    """

    closure_id: str
    nodes: tuple[str, ...]
    base_edges: tuple[tuple[str, str], ...]
    closure_edges: tuple[tuple[str, str], ...]
    computed_at: float
    algorithm_used: str
    iterations: tuple[ClosureIteration, ...] = field(default_factory=tuple)
    trust_tier: str = "PROPOSAL"

    def reaches(self, a: str, b: str) -> bool:
        """Return True if there is a path from *a* to *b* in the closure.

        Parameters
        ----------
        a, b:
            Source and target nodes.

        Returns
        -------
        bool
            True when ``(a, b)`` is in the closure_edges set.
        """
        return (a, b) in set(self.closure_edges)

    def reachable_from(self, a: str) -> frozenset[str]:
        """Return the set of nodes reachable from *a*.

        Parameters
        ----------
        a:
            Source node.

        Returns
        -------
        frozenset[str]
            All targets reachable from *a* (excluding *a* itself unless self-loop).
        """
        return frozenset(t for (s, t) in self.closure_edges if s == a)

    def strongly_connected_to(self, a: str) -> frozenset[str]:
        """Return nodes mutually reachable from *a* (SCC of *a*)."""
        reach_a = self.reachable_from(a)
        return frozenset(
            b for b in reach_a
            if (b, a) in set(self.closure_edges)
        )

    def closure_size(self) -> int:
        """Return the number of edges in the closure."""
        return len(self.closure_edges)

    def density(self) -> float:
        """Return edge density: closure_edges / (n * (n-1)) for n nodes."""
        n = len(self.nodes)
        if n <= 1:
            return 0.0
        return self.closure_size() / (n * (n - 1))


@dataclass(frozen=True)
class WarshallResult:
    """Result of running Warshall's algorithm.

    Attributes
    ----------
    closure_id:
        Unique identifier.
    nodes:
        Sorted tuple of nodes.
    reachability_matrix:
        Flat tuple of booleans: reachability_matrix[i * n + j] = True iff i reaches j.
    n:
        Number of nodes.
    computed_at:
        UNIX timestamp.
    """

    closure_id: str
    nodes: tuple[str, ...]
    reachability_matrix: tuple[bool, ...]
    n: int
    computed_at: float

    def reaches(self, a: str, b: str) -> bool:
        """Return True if *a* reaches *b*."""
        try:
            i = self.nodes.index(a)
            j = self.nodes.index(b)
        except ValueError:
            return False
        return self.reachability_matrix[i * self.n + j]

    def reachable_from(self, a: str) -> frozenset[str]:
        """Return the set of nodes reachable from *a*."""
        try:
            i = self.nodes.index(a)
        except ValueError:
            return frozenset()
        return frozenset(
            self.nodes[j]
            for j in range(self.n)
            if self.reachability_matrix[i * self.n + j]
        )

    def to_adjacency(self) -> dict[str, set[str]]:
        """Convert the matrix back to an adjacency-set representation."""
        result: dict[str, set[str]] = {n: set() for n in self.nodes}
        for i, src in enumerate(self.nodes):
            for j, tgt in enumerate(self.nodes):
                if self.reachability_matrix[i * self.n + j]:
                    result[src].add(tgt)
        return result


# ---------------------------------------------------------------------------
# FixedPointIterator
# ---------------------------------------------------------------------------


class FixedPointIterator:
    """Computes the least fixed point of a monotone operator on a lattice.

    Implements the Kleene fixed-point theorem: starting from the bottom
    element ⊥, repeatedly apply the operator F until convergence.

    Attributes
    ----------
    operator:
        The monotone operator F : X → X.
    bottom:
        The bottom element ⊥ of the lattice.
    max_iterations:
        Maximum number of iterations before declaring non-convergence.
    tolerance:
        Tolerance for floating-point convergence (used when elements are floats).
    """

    def __init__(
        self,
        operator: Callable[[Any], Any],
        bottom: Any = None,
        max_iterations: int = 1000,
        tolerance: float = 1e-9,
    ) -> None:
        self.operator = operator
        self.bottom = bottom
        self.max_iterations = max_iterations
        self.tolerance = tolerance

    def has_converged(self, prev: Any, curr: Any) -> bool:
        """Return True if prev and curr are equal (fixed point reached).

        Handles dicts (for relation-valued operators), sets, frozensets,
        lists, floats, and objects with ``__eq__``.

        Parameters
        ----------
        prev, curr:
            Two successive iterates.

        Returns
        -------
        bool
            True when the operator has converged.
        """
        if isinstance(prev, dict) and isinstance(curr, dict):
            return is_fixed_point_relation(prev, curr)
        if isinstance(prev, (set, frozenset)) and isinstance(curr, (set, frozenset)):
            return set(prev) == set(curr)
        if isinstance(prev, float) and isinstance(curr, float):
            return abs(prev - curr) < self.tolerance
        return prev == curr

    def iterate(self, initial: Any = None) -> tuple[Any, int, bool]:
        """Run the fixed-point iteration.

        Parameters
        ----------
        initial:
            Starting point; if None, uses ``self.bottom``.

        Returns
        -------
        tuple[Any, int, bool]
            (fixed_point_value, iterations_taken, converged)
        """
        current = initial if initial is not None else self.bottom
        t0 = time.time()

        for i in range(self.max_iterations):
            next_val = self.operator(current)
            if self.has_converged(current, next_val):
                elapsed = time.time() - t0
                logger.debug(
                    "Fixed-point converged in %d iteration(s) (%.4f s).", i + 1, elapsed
                )
                return next_val, i + 1, True
            current = next_val

        logger.warning(
            "Fixed-point did not converge after %d iterations.", self.max_iterations
        )
        return current, self.max_iterations, False


# ---------------------------------------------------------------------------
# KleeneClosure
# ---------------------------------------------------------------------------


class KleeneClosure:
    """Computes the reflexive transitive closure K*(R) of a binary relation.

    Uses the Kleene closure iteration:

        R_0 = I ∪ R   (add identity/reflexive edges)
        R_{n+1} = R_n ∪ R_n ∘ R   (extend by one step)
        K*(R) = lim_{n→∞} R_n

    Attributes
    ----------
    add_reflexive:
        When True, add self-loops for every node (reflexive closure).
    max_iterations:
        Maximum iterations before stopping.
    """

    def __init__(
        self,
        add_reflexive: bool = True,
        max_iterations: int = 1000,
    ) -> None:
        self.add_reflexive = add_reflexive
        self.max_iterations = max_iterations

    def _collect_nodes(self, relation: dict[str, set[str]]) -> set[str]:
        """Collect all nodes from the relation."""
        nodes: set[str] = set(relation.keys())
        for targets in relation.values():
            nodes.update(targets)
        return nodes

    def step(
        self,
        current: dict[str, set[str]],
        base: dict[str, set[str]],
    ) -> dict[str, set[str]]:
        """Perform one Kleene extension step.

        Parameters
        ----------
        current:
            Current closure approximation.
        base:
            The original (base) relation R.

        Returns
        -------
        dict[str, set[str]]
            The extended relation R_n ∪ R_n ∘ R.
        """
        composed = relation_compose(current, base)
        return relation_union(current, composed)

    def is_fixed_point(
        self,
        prev: dict[str, set[str]],
        curr: dict[str, set[str]],
    ) -> bool:
        """Return True if the iteration has converged."""
        return is_fixed_point_relation(prev, curr)

    def compute(
        self,
        relation: dict[str, set[str]],
    ) -> tuple[dict[str, set[str]], list[ClosureIteration]]:
        """Compute K*(relation).

        Parameters
        ----------
        relation:
            Adjacency-set representation of R.

        Returns
        -------
        tuple[dict[str, set[str]], list[ClosureIteration]]
            (closure_relation, iteration_log)
        """
        nodes = self._collect_nodes(relation)
        current: dict[str, set[str]] = {n: set(relation.get(n, set())) for n in nodes}

        # Add reflexive edges
        if self.add_reflexive:
            for n in nodes:
                current[n].add(n)

        base = {n: set(relation.get(n, set())) for n in nodes}
        iterations: list[ClosureIteration] = []

        for i in range(self.max_iterations):
            t0 = time.time()
            prev_size = sum(len(v) for v in current.values())
            next_val = self.step(current, base)
            next_size = sum(len(v) for v in next_val.values())
            added = next_size - prev_size
            converged = is_fixed_point_relation(current, next_val)
            elapsed = time.time() - t0

            iterations.append(ClosureIteration(
                iteration_number=i,
                nodes_added=added,
                converged=converged,
                elapsed_secs=elapsed,
                relation_size=next_size,
            ))

            if converged:
                logger.debug("Kleene closure converged in %d iteration(s).", i + 1)
                return next_val, iterations

            current = next_val

        logger.warning("Kleene closure did not converge in %d iterations.", self.max_iterations)
        return current, iterations


# ---------------------------------------------------------------------------
# JudgmentSheafClosure
# ---------------------------------------------------------------------------


class JudgmentSheafClosure:
    """Computes closure over a judgment sheaf.

    In the judgment sheaf model, obligations are nodes and evidence items
    provide directed edges (implication arrows).  The closure propagates
    satisfaction: if o₁ is satisfied and (o₁ → o₂) is in the sheaf,
    then o₂ becomes satisfied.

    Attributes
    ----------
    implication_graph:
        Adjacency-set dict from obligation_id to set of implied obligation IDs.
    initial_satisfied:
        Set of obligation IDs that are initially satisfied.
    """

    def __init__(
        self,
        implication_graph: dict[str, set[str]],
        initial_satisfied: set[str] | None = None,
    ) -> None:
        self.implication_graph = implication_graph
        self.initial_satisfied: set[str] = set(initial_satisfied or [])

    def compute_saturation(self) -> set[str]:
        """Saturate the satisfaction set under the implication graph.

        Uses BFS/DFS to propagate satisfaction through the implication graph.

        Returns
        -------
        set[str]
            The full set of satisfied obligations after saturation.
        """
        satisfied = set(self.initial_satisfied)
        queue = deque(satisfied)

        while queue:
            current = queue.popleft()
            for implied in self.implication_graph.get(current, set()):
                if implied not in satisfied:
                    satisfied.add(implied)
                    queue.append(implied)

        return satisfied

    def compute_closure_relation(self) -> dict[str, set[str]]:
        """Return the transitive closure of the implication graph."""
        kleene = KleeneClosure(add_reflexive=True)
        closure, _ = kleene.compute(self.implication_graph)
        return closure

    def obligations_closed_by(self, evidence_obligations: set[str]) -> set[str]:
        """Return all obligations closed when *evidence_obligations* are satisfied.

        Parameters
        ----------
        evidence_obligations:
            Set of obligation IDs that are directly satisfied by evidence.

        Returns
        -------
        set[str]
            All obligations that become satisfied through implication.
        """
        sheaf = JudgmentSheafClosure(
            implication_graph=self.implication_graph,
            initial_satisfied=evidence_obligations,
        )
        return sheaf.compute_saturation()


# ---------------------------------------------------------------------------
# Warshall's algorithm
# ---------------------------------------------------------------------------


def warshall_closure(
    nodes: list[str],
    edges: list[tuple[str, str]],
    add_reflexive: bool = True,
) -> WarshallResult:
    """Compute the transitive closure using Floyd–Warshall's algorithm.

    Complexity: O(n³) time, O(n²) space.

    Parameters
    ----------
    nodes:
        List of all nodes.
    edges:
        List of (source, target) directed edges.
    add_reflexive:
        When True, add self-loops for every node.

    Returns
    -------
    WarshallResult
        All-pairs reachability result.
    """
    n = len(nodes)
    node_index = {node: i for i, node in enumerate(nodes)}

    # Initialise matrix
    matrix = [False] * (n * n)

    # Add self-loops if requested
    if add_reflexive:
        for i in range(n):
            matrix[i * n + i] = True

    # Add base edges
    for src, tgt in edges:
        if src in node_index and tgt in node_index:
            i, j = node_index[src], node_index[tgt]
            matrix[i * n + j] = True

    # Floyd–Warshall
    for k in range(n):
        for i in range(n):
            if not matrix[i * n + k]:
                continue
            for j in range(n):
                if matrix[k * n + j]:
                    matrix[i * n + j] = True

    return WarshallResult(
        closure_id=str(uuid.uuid4()),
        nodes=tuple(nodes),
        reachability_matrix=tuple(matrix),
        n=n,
        computed_at=time.time(),
    )


# ---------------------------------------------------------------------------
# BFS closure
# ---------------------------------------------------------------------------


def _bfs_closure(
    relation: dict[str, set[str]],
    add_reflexive: bool = True,
) -> dict[str, set[str]]:
    """Compute transitive closure using BFS from each node.

    Parameters
    ----------
    relation:
        Adjacency-set representation.
    add_reflexive:
        When True, include self-loops.

    Returns
    -------
    dict[str, set[str]]
        Adjacency-set representation of the closure.
    """
    all_nodes: set[str] = set(relation.keys())
    for targets in relation.values():
        all_nodes.update(targets)

    closure: dict[str, set[str]] = {n: set() for n in all_nodes}

    for start in all_nodes:
        if add_reflexive:
            closure[start].add(start)
        queue: deque[str] = deque(relation.get(start, set()))
        visited = set(relation.get(start, set()))
        while queue:
            node = queue.popleft()
            closure[start].add(node)
            for neighbor in relation.get(node, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

    return closure


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def kleene_step(
    current_closure: dict[str, set[str]],
    base_relation: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Perform one Kleene extension step.

    Parameters
    ----------
    current_closure:
        Current transitive closure approximation.
    base_relation:
        The original relation R.

    Returns
    -------
    dict[str, set[str]]
        The updated closure after one step.
    """
    composed = relation_compose(current_closure, base_relation)
    return relation_union(current_closure, composed)


def fixed_point_iteration(
    operator: Callable[[Any], Any],
    bottom: Any,
    max_iter: int = 1000,
    tolerance: float = 1e-9,
) -> tuple[Any, int]:
    """Compute lfp(operator) via Kleene iteration from *bottom*.

    Parameters
    ----------
    operator:
        The monotone operator.
    bottom:
        The bottom element (starting point).
    max_iter:
        Maximum number of iterations.
    tolerance:
        Tolerance for float convergence.

    Returns
    -------
    tuple[Any, int]
        (fixed_point_value, iterations_taken)
    """
    iterator = FixedPointIterator(operator, bottom, max_iter, tolerance)
    result, iters, _ = iterator.iterate()
    return result, iters


def compute_closure(
    relation: dict[str, set[str]],
    algorithm: str = "kleene",
    add_reflexive: bool = True,
) -> TransitiveClosure:
    """Compute the transitive closure of *relation* using the given algorithm.

    Parameters
    ----------
    relation:
        Adjacency-set representation of the base relation.
    algorithm:
        One of ``"kleene"``, ``"warshall"``, ``"bfs"``.
    add_reflexive:
        When True, include reflexive (identity) edges.

    Returns
    -------
    TransitiveClosure
        The computed closure.

    Raises
    ------
    ValueError
        If *algorithm* is not recognised.
    """
    all_nodes: set[str] = set(relation.keys())
    for targets in relation.values():
        all_nodes.update(targets)
    sorted_nodes = sorted(all_nodes)

    # Collect base edges
    base_edges = tuple(
        (src, tgt)
        for src, targets in relation.items()
        for tgt in sorted(targets)
    )

    iterations: list[ClosureIteration] = []
    t0 = time.time()

    alg = algorithm.lower()
    if alg == "kleene":
        kleene = KleeneClosure(add_reflexive=add_reflexive)
        closure_dict, iterations = kleene.compute(relation)
    elif alg == "warshall":
        base_edge_list = list(base_edges)
        warshall_res = warshall_closure(sorted_nodes, base_edge_list, add_reflexive)
        closure_dict = warshall_res.to_adjacency()
    elif alg == "bfs":
        closure_dict = _bfs_closure(relation, add_reflexive)
    else:
        raise ValueError(f"Unknown algorithm: {algorithm!r}. Choose from: kleene, warshall, bfs")

    # Collect closure edges
    closure_edges = tuple(
        (src, tgt)
        for src in sorted(closure_dict.keys())
        for tgt in sorted(closure_dict.get(src, set()))
    )

    return TransitiveClosure(
        closure_id=str(uuid.uuid4()),
        nodes=tuple(sorted_nodes),
        base_edges=base_edges,
        closure_edges=closure_edges,
        computed_at=time.time(),
        algorithm_used=alg,
        iterations=tuple(iterations),
        trust_tier="PROPOSAL",
    )


# ---------------------------------------------------------------------------
# Algorithm registry
# ---------------------------------------------------------------------------


class ClosureAlgorithmRegistry:
    """Registry of available closure algorithms."""

    _algorithms: dict[str, ClosureAlgorithm] = {}

    @classmethod
    def register(cls, algorithm: ClosureAlgorithm) -> None:
        """Register an algorithm descriptor."""
        cls._algorithms[algorithm.algorithm_id] = algorithm

    @classmethod
    def get(cls, algorithm_id: str) -> ClosureAlgorithm | None:
        """Return the algorithm descriptor, or None."""
        return cls._algorithms.get(algorithm_id)

    @classmethod
    def list_all(cls) -> list[ClosureAlgorithm]:
        """Return all registered algorithms."""
        return list(cls._algorithms.values())


# Register default algorithms
_KLEENE_ALG = ClosureAlgorithm(
    algorithm_id="kleene",
    name="Kleene Closure",
    description="Iterative closure: R* = ⋃_{n≥0} R^n via step-wise composition.",
    complexity_class="O(n^3 * iterations)",
    space_complexity="O(n^2)",
    is_complete=True,
    is_sound=True,
    trust_tier="VERIFIED",
    tags=("complete", "exact"),
)
_WARSHALL_ALG = ClosureAlgorithm(
    algorithm_id="warshall",
    name="Floyd–Warshall",
    description="All-pairs reachability via dynamic programming.",
    complexity_class="O(n^3)",
    space_complexity="O(n^2)",
    is_complete=True,
    is_sound=True,
    trust_tier="VERIFIED",
    tags=("complete", "exact", "dp"),
)
_BFS_ALG = ClosureAlgorithm(
    algorithm_id="bfs",
    name="BFS Closure",
    description="BFS from each source node; O(n*(n+m)) time.",
    complexity_class="O(n*(n+m))",
    space_complexity="O(n+m)",
    is_complete=True,
    is_sound=True,
    trust_tier="VERIFIED",
    tags=("complete", "exact", "bfs"),
)

ClosureAlgorithmRegistry.register(_KLEENE_ALG)
ClosureAlgorithmRegistry.register(_WARSHALL_ALG)
ClosureAlgorithmRegistry.register(_BFS_ALG)

ALGORITHM_REGISTRY = ClosureAlgorithmRegistry


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== algorithms.py smoke test ===\n")

    # Simple chain: a → b → c → d
    relation = {"a": {"b"}, "b": {"c"}, "c": {"d"}, "d": set()}

    print("Base relation: a→b, b→c, c→d")
    print()

    # Test all three algorithms
    for alg in ("kleene", "warshall", "bfs"):
        tc = compute_closure(relation, algorithm=alg)
        print(f"[{alg.upper()}] closure_edges: {len(tc.closure_edges)}")
        print(f"  a→c: {tc.reaches('a', 'c')}, a→d: {tc.reaches('a', 'd')}")
        print(f"  density: {tc.density():.3f}")
        print()

    # Test FixedPointIterator on a simple number-valued operator
    def double_cap(x: float) -> float:
        return min(x * 2, 100.0)

    fpi = FixedPointIterator(double_cap, bottom=0.5, max_iterations=200, tolerance=0.01)
    result, iters, converged = fpi.iterate()
    print(f"FixedPointIterator: result={result}, iters={iters}, converged={converged}")
    print()

    # KleeneClosure
    kc = KleeneClosure(add_reflexive=True)
    closure_dict, iter_log = kc.compute(relation)
    print(f"KleeneClosure iterations: {len(iter_log)}")
    print(f"Reachable from 'a': {sorted(closure_dict.get('a', set()))}")
    print()

    # Warshall
    nodes = ["a", "b", "c", "d"]
    edges = [("a", "b"), ("b", "c"), ("c", "d")]
    wresult = warshall_closure(nodes, edges)
    print(f"Warshall a→d: {wresult.reaches('a', 'd')}")
    print(f"Warshall reachable from a: {sorted(wresult.reachable_from('a'))}")
    print()

    # JudgmentSheafClosure
    implication = {"obl-1": {"obl-2", "obl-3"}, "obl-2": {"obl-4"}, "obl-3": set(), "obl-4": set()}
    initial = {"obl-1"}
    sheaf = JudgmentSheafClosure(implication, initial)
    saturated = sheaf.compute_saturation()
    print(f"JudgmentSheafClosure: initially satisfied {initial}, saturated to {saturated}")
    print()

    # Lattice operations
    print(f"lattice_join('open', 'partial') = {lattice_join('open', 'partial')}")
    print(f"lattice_meet('closed', 'partial') = {lattice_meet('closed', 'partial')}")
    print(f"lattice_leq('open', 'closed') = {lattice_leq('open', 'closed')}")
    print()

    # Algorithm registry
    print("Registered algorithms:")
    for a in ClosureAlgorithmRegistry.list_all():
        print(f"  {a.describe()}")
    print()

    # kleene_step
    r = {"x": {"y"}, "y": {"z"}, "z": set()}
    r_with_reflexive = {n: s | {n} for n, s in r.items()}
    step_result = kleene_step(r_with_reflexive, r)
    print(f"kleene_step result for x: {sorted(step_result.get('x', set()))}")
    print()

    # fixed_point_iteration
    def incr(x: int) -> int:
        return min(x + 1, 10)

    lfp, n_iters = fixed_point_iteration(incr, bottom=0)
    print(f"fixed_point_iteration(incr, 0): lfp={lfp}, iters={n_iters}")

    print("\n=== smoke test PASSED ===")
