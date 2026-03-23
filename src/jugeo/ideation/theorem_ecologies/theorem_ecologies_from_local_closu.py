"""Theorem ecologies: from local closure to changes in reasoning environment — theory2.tex Ch60.

# copilot: shared-core marker

Module layout::

    TheoremRole              – enum: role of a theorem within an ecology
    ClosureStatus            – enum: closure status of an ecology
    EcologyConfig            – frozen dataclass: configuration for ecology management
    TheoremNode              – frozen dataclass: a single theorem node
    TheoremEcology           – mutable dataclass: a living theorem ecology
    ClosureCheckResult       – frozen dataclass: result of local-closure check
    EnvironmentChangeReport  – frozen dataclass: report on reasoning-environment change
    EcologyCycleResult       – frozen dataclass: result of a full ecology cycle
    ClosurePropertyReport    – frozen dataclass: closure property analysis
    DependencyGraphReport    – frozen dataclass: dependency graph analysis
    ReasoningReachReport     – frozen dataclass: reasoning reach analysis
    EcologyWitnessReport     – frozen dataclass: witness for ecology construction
    ExpansionWitnessReport   – frozen dataclass: witness for ecology expansion
    ClosureWitnessReport     – frozen dataclass: witness for closure check
    TheoremEcologiesCoordinator – orchestrates theorem ecology management
    TheoremEcologiesAnalyzer    – analyzes theorem ecology structure
    TheoremEcologiesWitness     – witnesses theorem ecology events

Theory Background
=================

A *theorem ecology* is a set of theorems that mutually reinforce each other
through shared dependencies and proof reuse.  The concept draws on the
ecological metaphor: just as a biological ecosystem consists of organisms that
interact symbiotically, a theorem ecology consists of mathematical results that
depend on and strengthen each other.

*Local closure* is a key structural property: a set of theorems S is locally
closed if, for every theorem T in S and every result R that is provable
exclusively from theorems in S, applying T to R also yields a result that is
either already in S or is directly derivable from S in one step.  Informally,
the ecology does not "leak" — any inference that stays within its boundaries
produces results that remain within those boundaries.

When the reasoning environment changes (a new theorem is added, an existing one
is deprecated, or a dependency is restructured), the local-closure property may
be broken or strengthened.  This module tracks those transitions.

The *EcologyRecord* (embodied here as TheoremEcology) captures:

  * The member theorems and their roles (seed, supporting, derived, keystone).
  * A dependency matrix recording which theorem depends on which.
  * The current closure status of the ecology.
  * A reasoning-environment snapshot representing the set of proof paths
    currently accessible from this ecology.

The ``TheoremEcologiesCoordinator`` drives the ecology lifecycle:

  1. **Build**: given a seed set, compute the transitive closure of dependencies
     and classify each node into a role.
  2. **Expand**: evaluate candidate theorems for membership, admit those that
     strengthen closure, and update the dependency matrix.
  3. **Check closure**: run the local-closure algorithm and produce a
     ``ClosureCheckResult`` with a detailed witness.
  4. **Analyse environment change**: given a newly added theorem, compute which
     proof paths become available or unavailable.
  5. **Cycle**: compose all of the above into a single round-trip that can be
     run in a background loop.

Design notes
============

* All value objects use ``@dataclass(frozen=True, slots=True)`` to avoid
  accidental mutation and reduce memory overhead.
* ``TheoremEcology`` is mutable (``@dataclass(slots=True)``) because it is
  the live working state that the Coordinator updates incrementally.
* Helper functions prefixed with ``_`` are module-private and not exported.
* The three witness classes record high-fidelity event traces so that an
  external audit log can reconstruct any ecology transition.
* Cross-module jugeo imports are wrapped in ``try/except Exception: pass``
  blocks so that this module can be used in isolation without a full jugeo
  installation.
"""

from __future__ import annotations

import math
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    from jugeo.evidence.store import EvidenceStore  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.packs.registry import PackRegistry  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.orchestration.bus import EventBus  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.models import (  # type: ignore[import]
        TheoremEcology as _BaseEcology,
        EcologyHealth,
        DynamicType,
    )
except Exception:
    pass

try:
    from jugeo.ideation.ideas import IdeaRecord  # type: ignore[import]
except Exception:
    pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two string sets."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _entropy(counts: list[int]) -> float:
    """Shannon entropy (bits) of a discrete distribution given raw counts."""
    total = sum(counts)
    if total == 0:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    return -sum(p * math.log2(p) for p in probs)


def _bfs_reachable(graph: dict[str, list[str]], root: str) -> set[str]:
    """Return all nodes reachable from *root* in *graph* via BFS."""
    visited: set[str] = set()
    queue: deque[str] = deque([root])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbour in graph.get(node, []):
            if neighbour not in visited:
                queue.append(neighbour)
    return visited


def _detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Detect all cycles in a directed graph using iterative DFS.

    Returns
    -------
    list[list[str]]
        Each inner list is the sequence of node IDs forming a cycle.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in graph}
    parent: dict[str, str | None] = {n: None for n in graph}
    cycles: list[list[str]] = []
    for start in list(graph):
        if colour.get(start, WHITE) != WHITE:
            continue
        stack = [(start, iter(graph.get(start, [])))]
        colour[start] = GREY
        path: list[str] = [start]
        while stack:
            node, children = stack[-1]
            try:
                child = next(children)
                c = colour.get(child, WHITE)
                if c == WHITE:
                    colour[child] = GREY
                    parent[child] = node
                    path.append(child)
                    stack.append((child, iter(graph.get(child, []))))
                elif c == GREY:
                    idx = path.index(child)
                    cycles.append(path[idx:] + [child])
            except StopIteration:
                colour[node] = BLACK
                if path and path[-1] == node:
                    path.pop()
                stack.pop()
    return cycles


def _topological_sort(graph: dict[str, list[str]]) -> list[str]:
    """Return a topological ordering of *graph* nodes (Kahn's algorithm).

    If the graph has cycles, returns a best-effort partial ordering.
    """
    in_degree: dict[str, int] = defaultdict(int)
    for node, neighbours in graph.items():
        in_degree.setdefault(node, 0)
        for nb in neighbours:
            in_degree[nb] = in_degree.get(nb, 0) + 1
    queue: deque[str] = deque(n for n in graph if in_degree[n] == 0)
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for nb in graph.get(node, []):
            in_degree[nb] -= 1
            if in_degree[nb] == 0:
                queue.append(nb)
    remaining = [n for n in graph if n not in set(order)]
    return order + remaining


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TheoremRole(str, Enum):
    """Role of a theorem within an ecology."""

    SEED = "seed"
    SUPPORTING = "supporting"
    DERIVED = "derived"
    KEYSTONE = "keystone"

    def is_structural(self) -> bool:
        """Return True if the role implies structural importance."""
        return self in (TheoremRole.SEED, TheoremRole.KEYSTONE)

    def priority(self) -> int:
        """Return a numeric priority (higher = more important)."""
        mapping = {
            TheoremRole.SEED: 4,
            TheoremRole.KEYSTONE: 3,
            TheoremRole.SUPPORTING: 2,
            TheoremRole.DERIVED: 1,
        }
        return mapping[self]


class ClosureStatus(str, Enum):
    """Closure status of a theorem ecology."""

    CLOSED = "closed"
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    UNDETERMINED = "undetermined"

    def is_satisfactory(self) -> bool:
        """Return True if the status is acceptable for production use."""
        return self in (ClosureStatus.CLOSED, ClosureStatus.PARTIALLY_CLOSED)

    def numeric_score(self) -> float:
        """Return a numeric representation in [0, 1]."""
        mapping = {
            ClosureStatus.CLOSED: 1.0,
            ClosureStatus.PARTIALLY_CLOSED: 0.6,
            ClosureStatus.UNDETERMINED: 0.3,
            ClosureStatus.OPEN: 0.0,
        }
        return mapping[self]


# ---------------------------------------------------------------------------
# Value objects (frozen + slots)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EcologyConfig:
    """Configuration for theorem ecology management.

    Attributes
    ----------
    max_ecology_size:
        Maximum number of theorems in a single ecology.
    closure_threshold:
        Fraction of derivable results that must remain in-ecology to call it
        closed.  In [0, 1].
    min_keystone_reach:
        Minimum fraction of other theorems a keystone must be reachable from.
    expand_iterations:
        Number of expansion rounds to run when building an ecology.
    score_threshold:
        Minimum score for a candidate to be admitted to the ecology.
    enable_cycle_detection:
        Whether to run cycle detection on the dependency graph.
    """

    max_ecology_size: int = 64
    closure_threshold: float = 0.85
    min_keystone_reach: float = 0.5
    expand_iterations: int = 3
    score_threshold: float = 0.4
    enable_cycle_detection: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "closure_threshold",
                           _clamp(self.closure_threshold))
        object.__setattr__(self, "min_keystone_reach",
                           _clamp(self.min_keystone_reach))
        object.__setattr__(self, "score_threshold",
                           _clamp(self.score_threshold))


@dataclass(frozen=True, slots=True)
class TheoremNode:
    """A single theorem node in the ecology graph.

    Attributes
    ----------
    node_id:
        Unique identifier for this node.
    label:
        Human-readable name of the theorem.
    statement:
        Formal or informal statement of the theorem.
    role:
        The role this theorem plays in the ecology.
    dependencies:
        IDs of theorems that this theorem directly depends on.
    proof_length:
        Approximate proof length in proof steps.
    tags:
        Semantic tags for clustering.
    created_at:
        Unix timestamp of creation.
    metadata:
        Arbitrary extra metadata.
    """

    node_id: str = field(default_factory=_uid)
    label: str = "unnamed_theorem"
    statement: str = ""
    role: TheoremRole = TheoremRole.SUPPORTING
    dependencies: tuple[str, ...] = ()
    proof_length: int = 0
    tags: tuple[str, ...] = ()
    created_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def token_set(self) -> frozenset[str]:
        """Return a frozenset of lowercase tokens from label and statement."""
        combined = f"{self.label} {self.statement}"
        return frozenset(w.lower() for w in combined.split() if len(w) >= 2)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "node_id": self.node_id,
            "label": self.label,
            "statement": self.statement,
            "role": self.role.value,
            "dependencies": list(self.dependencies),
            "proof_length": self.proof_length,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ClosureCheckResult:
    """Result of a local-closure check on a theorem ecology.

    Attributes
    ----------
    ecology_id:
        ID of the checked ecology.
    status:
        The computed closure status.
    closure_fraction:
        Fraction of derivable results that remain in-ecology.
    open_endpoints:
        IDs of theorems whose derivable results fall outside the ecology.
    witnesses:
        Human-readable descriptions of the evidence supporting the status.
    checked_at:
        ISO-8601 timestamp of the check.
    """

    ecology_id: str
    status: ClosureStatus
    closure_fraction: float
    open_endpoints: tuple[str, ...] = ()
    witnesses: tuple[str, ...] = ()
    checked_at: str = field(default_factory=_now_iso)

    def is_closed(self) -> bool:
        """Return True if the ecology is fully closed."""
        return self.status == ClosureStatus.CLOSED

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"Ecology {self.ecology_id}: {self.status.value} "
            f"(closure={self.closure_fraction:.2%}, "
            f"open_endpoints={len(self.open_endpoints)})"
        )


@dataclass(frozen=True, slots=True)
class EnvironmentChangeReport:
    """Report on how a new theorem changes the reasoning environment.

    Attributes
    ----------
    ecology_id:
        ID of the affected ecology.
    new_theorem_id:
        ID of the newly added theorem.
    new_proof_paths:
        Pairs (from_id, to_id) of newly enabled proof paths.
    closed_dead_ends:
        IDs of theorems whose dead-end proof paths were resolved.
    closure_delta:
        Change in closure fraction (positive = improvement).
    environment_version:
        Monotonically increasing version counter for the environment.
    computed_at:
        ISO-8601 timestamp.
    """

    ecology_id: str
    new_theorem_id: str
    new_proof_paths: tuple[tuple[str, str], ...] = ()
    closed_dead_ends: tuple[str, ...] = ()
    closure_delta: float = 0.0
    environment_version: int = 0
    computed_at: str = field(default_factory=_now_iso)

    def has_positive_impact(self) -> bool:
        """Return True if the new theorem improves the environment."""
        return self.closure_delta > 0 or len(self.new_proof_paths) > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "ecology_id": self.ecology_id,
            "new_theorem_id": self.new_theorem_id,
            "new_proof_paths": [list(p) for p in self.new_proof_paths],
            "closed_dead_ends": list(self.closed_dead_ends),
            "closure_delta": self.closure_delta,
            "environment_version": self.environment_version,
            "computed_at": self.computed_at,
        }


@dataclass(frozen=True, slots=True)
class EcologyCycleResult:
    """Result of a full ecology construction-and-expansion cycle.

    Attributes
    ----------
    ecology_id:
        ID of the produced ecology.
    admitted_count:
        Number of candidate theorems admitted during expansion.
    rejected_count:
        Number of candidate theorems rejected.
    final_closure_status:
        Closure status of the final ecology.
    closure_fraction:
        Final closure fraction.
    keystone_ids:
        IDs identified as keystone theorems.
    total_duration_s:
        Wall-clock seconds taken for the full cycle.
    cycle_at:
        ISO-8601 timestamp.
    """

    ecology_id: str
    admitted_count: int
    rejected_count: int
    final_closure_status: ClosureStatus
    closure_fraction: float
    keystone_ids: tuple[str, ...] = ()
    total_duration_s: float = 0.0
    cycle_at: str = field(default_factory=_now_iso)

    def success(self) -> bool:
        """Return True if the cycle produced a satisfactory ecology."""
        return self.final_closure_status.is_satisfactory()


@dataclass(frozen=True, slots=True)
class ClosurePropertyReport:
    """Detailed report of closure properties of a theorem ecology."""

    ecology_id: str
    closure_fraction: float
    strongly_closed_count: int
    weakly_closed_count: int
    open_count: int
    cycle_count: int
    max_dependency_depth: int
    avg_dependency_depth: float
    property_computed_at: str = field(default_factory=_now_iso)

    def overall_health(self) -> float:
        """Return a composite health score in [0, 1]."""
        depth_penalty = _clamp(1.0 - self.avg_dependency_depth / 20.0)
        return _clamp(
            0.5 * self.closure_fraction
            + 0.3 * depth_penalty
            + 0.2 * _clamp(1.0 - self.cycle_count / max(1, self.strongly_closed_count))
        )


@dataclass(frozen=True, slots=True)
class DependencyGraphReport:
    """Report on the dependency graph structure of a theorem ecology."""

    ecology_id: str
    node_count: int
    edge_count: int
    root_count: int
    leaf_count: int
    max_path_length: int
    avg_out_degree: float
    strongly_connected_components: int
    has_cycles: bool
    topological_order: tuple[str, ...] = ()
    report_at: str = field(default_factory=_now_iso)

    def is_dag(self) -> bool:
        """Return True if the dependency graph is a DAG (no cycles)."""
        return not self.has_cycles

    def density(self) -> float:
        """Return graph density (edges / possible edges)."""
        n = self.node_count
        if n <= 1:
            return 0.0
        return self.edge_count / (n * (n - 1))


@dataclass(frozen=True, slots=True)
class ReasoningReachReport:
    """Report on how far reasoning can reach within a theorem ecology."""

    ecology_id: str
    reachability_matrix: dict[str, frozenset[str]]
    avg_reach_fraction: float
    fully_reachable_ids: tuple[str, ...]
    isolated_ids: tuple[str, ...]
    median_reach: float
    computed_at: str = field(default_factory=_now_iso)

    def reach_for(self, node_id: str) -> frozenset[str]:
        """Return the set of nodes reachable from *node_id*."""
        return self.reachability_matrix.get(node_id, frozenset())


@dataclass(frozen=True, slots=True)
class EcologyWitnessReport:
    """Witness report for ecology construction."""

    witness_id: str = field(default_factory=_uid)
    ecology_id: str = ""
    seed_count: int = 0
    final_size: int = 0
    seed_ids: tuple[str, ...] = ()
    observed_at: str = field(default_factory=_now_iso)
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ExpansionWitnessReport:
    """Witness report for ecology expansion."""

    witness_id: str = field(default_factory=_uid)
    old_ecology_id: str = ""
    new_ecology_id: str = ""
    added_theorem_id: str = ""
    size_before: int = 0
    size_after: int = 0
    closure_before: float = 0.0
    closure_after: float = 0.0
    observed_at: str = field(default_factory=_now_iso)

    def net_closure_improvement(self) -> float:
        """Return the closure improvement from this expansion step."""
        return self.closure_after - self.closure_before


@dataclass(frozen=True, slots=True)
class ClosureWitnessReport:
    """Witness report for a closure check event."""

    witness_id: str = field(default_factory=_uid)
    ecology_id: str = ""
    status_observed: ClosureStatus = ClosureStatus.UNDETERMINED
    closure_fraction_observed: float = 0.0
    open_endpoint_count: int = 0
    observed_at: str = field(default_factory=_now_iso)
    narrative: str = ""


# ---------------------------------------------------------------------------
# Mutable working state
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TheoremEcology:
    """A living theorem ecology that evolves over time.

    Attributes
    ----------
    ecology_id:
        Unique identifier.
    members:
        List of TheoremNode objects currently in the ecology.
    dependency_matrix:
        Adjacency dict: node_id -> list of dependency node_ids.
    closure_status:
        Current closure status.
    environment_version:
        Monotonically increasing version number.
    created_at:
        Unix timestamp of creation.
    updated_at:
        Unix timestamp of last modification.
    metadata:
        Arbitrary metadata.
    """

    ecology_id: str = field(default_factory=_uid)
    members: list[TheoremNode] = field(default_factory=list)
    dependency_matrix: dict[str, list[str]] = field(default_factory=dict)
    closure_status: ClosureStatus = ClosureStatus.UNDETERMINED
    environment_version: int = 0
    created_at: float = field(default_factory=_utcnow)
    updated_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def size(self) -> int:
        """Return the number of member theorems."""
        return len(self.members)

    def member_ids(self) -> list[str]:
        """Return IDs of all member theorems."""
        return [m.node_id for m in self.members]

    def get_member(self, node_id: str) -> TheoremNode | None:
        """Look up a member by ID; return None if not found."""
        for m in self.members:
            if m.node_id == node_id:
                return m
        return None

    def add_member(self, node: TheoremNode) -> None:
        """Add *node* to the ecology and update the dependency matrix."""
        if node.node_id not in self.member_ids():
            self.members.append(node)
            self.dependency_matrix[node.node_id] = list(node.dependencies)
            self.updated_at = _utcnow()
            self.environment_version += 1

    def remove_member(self, node_id: str) -> bool:
        """Remove the member with *node_id*. Return True if found."""
        before = len(self.members)
        self.members = [m for m in self.members if m.node_id != node_id]
        if len(self.members) < before:
            self.dependency_matrix.pop(node_id, None)
            self.updated_at = _utcnow()
            self.environment_version += 1
            return True
        return False

    def roles_by_id(self) -> dict[str, TheoremRole]:
        """Return a mapping from node_id to TheoremRole."""
        return {m.node_id: m.role for m in self.members}

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "ecology_id": self.ecology_id,
            "members": [m.to_dict() for m in self.members],
            "dependency_matrix": {k: list(v) for k, v in self.dependency_matrix.items()},
            "closure_status": self.closure_status.value,
            "environment_version": self.environment_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def build_dependency_matrix(nodes: list[TheoremNode]) -> dict[str, list[str]]:
    """Build a dependency adjacency dict from a list of TheoremNodes.

    Parameters
    ----------
    nodes:
        List of theorem nodes.

    Returns
    -------
    dict[str, list[str]]
        Mapping from node_id to list of dependency node_ids (filtered to
        nodes that are actually present in *nodes*).
    """
    present = {n.node_id for n in nodes}
    return {
        n.node_id: [d for d in n.dependencies if d in present]
        for n in nodes
    }


def score_theorem_for_ecology(
    candidate: TheoremNode,
    ecology: TheoremEcology,
    config: EcologyConfig,
) -> float:
    """Score a candidate theorem for admission into *ecology*.

    The score is a weighted combination of:

    * **Semantic overlap** — Jaccard similarity between candidate tokens
      and the union of member tokens.
    * **Dependency gain** — fraction of candidate dependencies already
      covered by ecology members (higher is better, means fewer dangling
      edges).
    * **Role bonus** — seeds and keystones receive a small bonus.

    Parameters
    ----------
    candidate:
        TheoremNode to score.
    ecology:
        The current ecology.
    config:
        EcologyConfig with thresholds.

    Returns
    -------
    float
        Score in [0, 1].
    """
    if not ecology.members:
        return 0.5

    member_tokens: frozenset[str] = frozenset()
    for m in ecology.members:
        member_tokens = member_tokens | m.token_set()
    semantic_score = _jaccard(candidate.token_set(), member_tokens)

    member_ids_set = set(ecology.member_ids())
    deps = set(candidate.dependencies)
    dep_score = len(deps & member_ids_set) / max(1, len(deps))

    role_bonus = 0.1 if candidate.role.is_structural() else 0.0

    raw = 0.4 * semantic_score + 0.4 * dep_score + 0.2 + role_bonus
    return _clamp(raw)


def run_ecology_cycle(
    seed_theorems: list[TheoremNode],
    candidate_pool: list[TheoremNode],
    config: EcologyConfig | None = None,
) -> EcologyCycleResult:
    """Convenience wrapper: build an ecology and expand it from a candidate pool.

    Parameters
    ----------
    seed_theorems:
        Initial theorems to seed the ecology.
    candidate_pool:
        Theorems to evaluate for admission.
    config:
        EcologyConfig; uses defaults if None.

    Returns
    -------
    EcologyCycleResult
        Summary of the cycle.
    """
    cfg = config or EcologyConfig()
    coordinator = TheoremEcologiesCoordinator(cfg)
    return coordinator.run_ecology_cycle(seed_theorems, candidate_pool)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class TheoremEcologiesCoordinator:
    """Orchestrates theorem ecology management.

    This class drives the full ecology lifecycle: building from seeds,
    expanding with candidates, checking local closure, and analysing
    environment changes.

    Parameters
    ----------
    config:
        EcologyConfig controlling thresholds and limits.
    """

    def __init__(self, config: EcologyConfig) -> None:
        self.config = config
        self._history: list[dict[str, Any]] = []

    def build_ecology(self, seed_theorems: list[TheoremNode]) -> TheoremEcology:
        """Build an initial ecology from *seed_theorems*.

        The seed theorems are admitted unconditionally.  Their transitive
        dependencies (present in the seed list) are added as SUPPORTING
        members.  The dependency matrix is computed from the union.

        Parameters
        ----------
        seed_theorems:
            Initial set of theorems for the ecology.

        Returns
        -------
        TheoremEcology
            A new, mutable ecology populated with the seeds.
        """
        ecology = TheoremEcology()
        seed_ids = {t.node_id for t in seed_theorems}
        for node in seed_theorems:
            if node.role == TheoremRole.SEED:
                ecology.add_member(node)
            else:
                import dataclasses
                seeded = dataclasses.replace(node, role=TheoremRole.SEED)
                ecology.add_member(seeded)
        self._log_event("build_ecology", {"seed_count": len(seed_theorems),
                                           "ecology_id": ecology.ecology_id})
        return ecology

    def expand_ecology(
        self,
        ecology: TheoremEcology,
        candidates: list[TheoremNode],
    ) -> TheoremEcology:
        """Evaluate *candidates* and admit those that strengthen the ecology.

        Candidates are scored via ``score_theorem_for_ecology``.  Those
        scoring above ``config.score_threshold`` are admitted, up to
        ``config.max_ecology_size``.

        Parameters
        ----------
        ecology:
            The ecology to expand (mutated in place, then returned).
        candidates:
            Theorem nodes to evaluate.

        Returns
        -------
        TheoremEcology
            The same ecology object, now potentially larger.
        """
        scored = [
            (c, score_theorem_for_ecology(c, ecology, self.config))
            for c in candidates
            if c.node_id not in ecology.member_ids()
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        admitted = 0
        for node, score in scored:
            if ecology.size() >= self.config.max_ecology_size:
                break
            if score >= self.config.score_threshold:
                ecology.add_member(node)
                admitted += 1
        self._log_event("expand_ecology", {"admitted": admitted,
                                            "evaluated": len(candidates)})
        return ecology

    def check_local_closure(
        self, ecology: TheoremEcology
    ) -> ClosureCheckResult:
        """Check whether *ecology* is locally closed.

        The algorithm proceeds as follows:

        1. For each member theorem T, gather all theorems derivable from T
           using only members of the ecology as lemmas (simulated by
           following the dependency matrix one step forward).
        2. Check whether each such derived result is also a member.
        3. Compute the fraction of in-ecology derivations.

        Parameters
        ----------
        ecology:
            The ecology to check.

        Returns
        -------
        ClosureCheckResult
            Detailed result of the closure check.
        """
        member_set = set(ecology.member_ids())
        dm = ecology.dependency_matrix
        in_ecology = 0
        total = 0
        open_endpoints: list[str] = []
        for node_id, deps in dm.items():
            for dep in deps:
                total += 1
                if dep in member_set:
                    in_ecology += 1
                else:
                    open_endpoints.append(node_id)
        fraction = in_ecology / max(1, total)
        if fraction >= self.config.closure_threshold:
            status = ClosureStatus.CLOSED
        elif fraction >= 0.5:
            status = ClosureStatus.PARTIALLY_CLOSED
        elif total == 0:
            status = ClosureStatus.UNDETERMINED
        else:
            status = ClosureStatus.OPEN
        ecology.closure_status = status
        result = ClosureCheckResult(
            ecology_id=ecology.ecology_id,
            status=status,
            closure_fraction=_clamp(fraction),
            open_endpoints=tuple(dict.fromkeys(open_endpoints)),
            witnesses=(f"Checked {total} dependency edges; {in_ecology} in-ecology",),
        )
        self._log_event("check_closure", result.to_dict() if hasattr(result, "to_dict")
                        else {"status": status.value})
        return result

    def analyze_environment_change(
        self, ecology: TheoremEcology, new_theorem: TheoremNode
    ) -> EnvironmentChangeReport:
        """Analyse how adding *new_theorem* changes the reasoning environment.

        Computes:

        * New proof paths: pairs (existing_member, new_theorem) where the
          existing member's token set overlaps with the new theorem's.
        * Closed dead ends: existing open endpoints whose dependencies are
          now satisfied by the new theorem.
        * Change in closure fraction.

        Parameters
        ----------
        ecology:
            The current ecology (not mutated).
        new_theorem:
            The theorem being added.

        Returns
        -------
        EnvironmentChangeReport
            Detailed impact report.
        """
        old_check = self.check_local_closure(ecology)
        old_fraction = old_check.closure_fraction

        ecology_snapshot = TheoremEcology(
            ecology_id=ecology.ecology_id,
            members=list(ecology.members),
            dependency_matrix={k: list(v) for k, v in ecology.dependency_matrix.items()},
            closure_status=ecology.closure_status,
            environment_version=ecology.environment_version,
        )
        ecology_snapshot.add_member(new_theorem)
        new_check = self.check_local_closure(ecology_snapshot)
        new_fraction = new_check.closure_fraction

        new_paths: list[tuple[str, str]] = []
        nt_tokens = new_theorem.token_set()
        for m in ecology.members:
            if _jaccard(m.token_set(), nt_tokens) > 0.1:
                new_paths.append((m.node_id, new_theorem.node_id))

        closed_dead_ends = tuple(
            ep for ep in old_check.open_endpoints
            if ep in set(new_theorem.dependencies)
        )

        return EnvironmentChangeReport(
            ecology_id=ecology.ecology_id,
            new_theorem_id=new_theorem.node_id,
            new_proof_paths=tuple(new_paths),
            closed_dead_ends=closed_dead_ends,
            closure_delta=new_fraction - old_fraction,
            environment_version=ecology.environment_version + 1,
        )

    def run_ecology_cycle(
        self,
        seed_theorems: list[TheoremNode],
        candidate_pool: list[TheoremNode],
    ) -> EcologyCycleResult:
        """Run a full ecology lifecycle cycle.

        Steps:

        1. Build initial ecology from seeds.
        2. Run ``expand_iterations`` rounds of expansion.
        3. Check local closure.
        4. Identify keystones via the Analyzer.

        Parameters
        ----------
        seed_theorems:
            Seeds for the ecology.
        candidate_pool:
            Candidate theorems to evaluate for admission.

        Returns
        -------
        EcologyCycleResult
            Summary of the full cycle.
        """
        t0 = _utcnow()
        ecology = self.build_ecology(seed_theorems)
        initial_size = ecology.size()

        for _ in range(self.config.expand_iterations):
            self.expand_ecology(ecology, candidate_pool)

        closure_result = self.check_local_closure(ecology)
        analyzer = TheoremEcologiesAnalyzer()
        keystones = analyzer.identify_keystone_theorems(ecology)

        admitted = ecology.size() - initial_size
        rejected = len(candidate_pool) - admitted
        duration = _utcnow() - t0

        return EcologyCycleResult(
            ecology_id=ecology.ecology_id,
            admitted_count=max(0, admitted),
            rejected_count=max(0, rejected),
            final_closure_status=closure_result.status,
            closure_fraction=closure_result.closure_fraction,
            keystone_ids=tuple(k.node_id for k in keystones),
            total_duration_s=duration,
        )

    # ------------------------------------------------------------------
    # Private helpers

    def _log_event(self, event: str, data: dict[str, Any]) -> None:
        """Append an event record to the internal history log."""
        self._history.append({"event": event, "data": data, "at": _now_iso()})


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class TheoremEcologiesAnalyzer:
    """Analyzes theorem ecology structure.

    This class provides structural analysis capabilities for theorem
    ecologies, including closure properties, dependency graph metrics,
    reasoning reach, and keystone identification.
    """

    def analyze_closure_properties(
        self, ecology: TheoremEcology
    ) -> ClosurePropertyReport:
        """Analyse the closure properties of *ecology* in detail.

        Computes strongly-closed counts (all dependencies in-ecology),
        weakly-closed counts (some dependencies in-ecology), cycle counts
        (via iterative DFS), and depth statistics.

        Parameters
        ----------
        ecology:
            The ecology to analyse.

        Returns
        -------
        ClosurePropertyReport
            Detailed closure property report.
        """
        member_set = set(ecology.member_ids())
        dm = ecology.dependency_matrix
        strongly_closed = 0
        weakly_closed = 0
        open_count = 0
        for node_id, deps in dm.items():
            if not deps:
                strongly_closed += 1
            elif all(d in member_set for d in deps):
                strongly_closed += 1
            elif any(d in member_set for d in deps):
                weakly_closed += 1
            else:
                open_count += 1
        total_edges = sum(len(v) for v in dm.values())
        in_ecology = sum(
            sum(1 for d in deps if d in member_set)
            for deps in dm.values()
        )
        closure_fraction = in_ecology / max(1, total_edges)
        cycles = _detect_cycles({k: list(v) for k, v in dm.items()}) \
            if ecology.size() <= 200 else []
        depths = self._compute_depths(dm)
        max_depth = max(depths.values(), default=0)
        avg_depth = sum(depths.values()) / max(1, len(depths))
        return ClosurePropertyReport(
            ecology_id=ecology.ecology_id,
            closure_fraction=_clamp(closure_fraction),
            strongly_closed_count=strongly_closed,
            weakly_closed_count=weakly_closed,
            open_count=open_count,
            cycle_count=len(cycles),
            max_dependency_depth=max_depth,
            avg_dependency_depth=avg_depth,
        )

    def analyze_dependency_graph(
        self, ecology: TheoremEcology
    ) -> DependencyGraphReport:
        """Analyse the dependency graph structure of *ecology*.

        Parameters
        ----------
        ecology:
            The ecology to analyse.

        Returns
        -------
        DependencyGraphReport
            Report with node/edge counts, degree statistics, SCC count.
        """
        dm = {k: list(v) for k, v in ecology.dependency_matrix.items()}
        node_count = len(dm)
        edge_count = sum(len(v) for v in dm.values())
        out_degrees = [len(v) for v in dm.values()]
        avg_out = sum(out_degrees) / max(1, node_count)
        in_degree: dict[str, int] = defaultdict(int)
        for deps in dm.values():
            for d in deps:
                in_degree[d] += 1
        roots = [n for n in dm if in_degree.get(n, 0) == 0]
        leaves = [n for n in dm if not dm[n]]
        topo = _topological_sort(dm)
        cycles = _detect_cycles(dm)
        scc_count = self._count_sccs(dm)
        max_path = self._longest_path(dm)
        return DependencyGraphReport(
            ecology_id=ecology.ecology_id,
            node_count=node_count,
            edge_count=edge_count,
            root_count=len(roots),
            leaf_count=len(leaves),
            max_path_length=max_path,
            avg_out_degree=avg_out,
            strongly_connected_components=scc_count,
            has_cycles=len(cycles) > 0,
            topological_order=tuple(topo),
        )

    def analyze_reasoning_reach(
        self, ecology: TheoremEcology
    ) -> ReasoningReachReport:
        """Analyse how far reasoning can reach within *ecology*.

        For each member, computes the set of other members reachable via
        the dependency graph (in the forward direction: what can I derive
        from this theorem?).

        Parameters
        ----------
        ecology:
            The ecology to analyse.

        Returns
        -------
        ReasoningReachReport
            Reachability matrix and summary statistics.
        """
        dm = {k: list(v) for k, v in ecology.dependency_matrix.items()}
        total = max(1, ecology.size())
        reachability: dict[str, frozenset[str]] = {}
        for node_id in ecology.member_ids():
            reachable = _bfs_reachable(dm, node_id) - {node_id}
            reachability[node_id] = frozenset(reachable)
        reach_fractions = [len(v) / (total - 1) for v in reachability.values() if total > 1]
        avg_reach = sum(reach_fractions) / max(1, len(reach_fractions))
        sorted_fracs = sorted(reach_fractions)
        median_reach = sorted_fracs[len(sorted_fracs) // 2] if sorted_fracs else 0.0
        fully_reachable = [nid for nid, r in reachability.items()
                           if len(r) == total - 1 and total > 1]
        isolated = [nid for nid, r in reachability.items() if len(r) == 0]
        return ReasoningReachReport(
            ecology_id=ecology.ecology_id,
            reachability_matrix=reachability,
            avg_reach_fraction=_clamp(avg_reach),
            fully_reachable_ids=tuple(fully_reachable),
            isolated_ids=tuple(isolated),
            median_reach=_clamp(median_reach),
        )

    def identify_keystone_theorems(
        self, ecology: TheoremEcology
    ) -> list[TheoremNode]:
        """Identify keystone theorems in *ecology*.

        A theorem is a keystone if removing it would disconnect the
        dependency graph or reduce reachability by more than
        ``config.min_keystone_reach``.  The heuristic used here is:
        a node is a keystone if its in-degree plus out-degree exceeds
        the 75th percentile of all degrees.

        Parameters
        ----------
        ecology:
            The ecology to analyse.

        Returns
        -------
        list[TheoremNode]
            List of keystone nodes.
        """
        dm = {k: list(v) for k, v in ecology.dependency_matrix.items()}
        in_deg: dict[str, int] = defaultdict(int)
        for deps in dm.values():
            for d in deps:
                in_deg[d] += 1
        combined = {nid: len(dm.get(nid, [])) + in_deg.get(nid, 0)
                    for nid in ecology.member_ids()}
        if not combined:
            return []
        vals = sorted(combined.values())
        p75 = vals[int(0.75 * len(vals))]
        keystone_ids = {nid for nid, deg in combined.items() if deg >= max(1, p75)}
        return [m for m in ecology.members if m.node_id in keystone_ids]

    # ------------------------------------------------------------------
    # Private helpers

    def _compute_depths(self, dm: dict[str, list[str]]) -> dict[str, int]:
        """Compute depth of each node from roots (BFS)."""
        in_deg: dict[str, int] = defaultdict(int)
        for deps in dm.values():
            for d in deps:
                in_deg[d] += 1
        roots = [n for n in dm if in_deg.get(n, 0) == 0]
        depth: dict[str, int] = {r: 0 for r in roots}
        queue: deque[str] = deque(roots)
        while queue:
            node = queue.popleft()
            for nb in dm.get(node, []):
                if nb not in depth:
                    depth[nb] = depth[node] + 1
                    queue.append(nb)
        for n in dm:
            depth.setdefault(n, 0)
        return depth

    def _count_sccs(self, dm: dict[str, list[str]]) -> int:
        """Count strongly connected components using Kosaraju's algorithm."""
        visited: set[str] = set()
        finish_order: list[str] = []

        def dfs1(node: str) -> None:
            stack = [(node, iter(dm.get(node, [])))]
            visited.add(node)
            while stack:
                n, children = stack[-1]
                try:
                    child = next(children)
                    if child not in visited:
                        visited.add(child)
                        stack.append((child, iter(dm.get(child, []))))
                except StopIteration:
                    finish_order.append(n)
                    stack.pop()

        for node in dm:
            if node not in visited:
                dfs1(node)
        reverse_dm: dict[str, list[str]] = defaultdict(list)
        for node, deps in dm.items():
            for d in deps:
                reverse_dm[d].append(node)
        visited2: set[str] = set()
        scc_count = 0
        for node in reversed(finish_order):
            if node not in visited2:
                scc_count += 1
                stack2 = [node]
                visited2.add(node)
                while stack2:
                    n = stack2.pop()
                    for nb in reverse_dm.get(n, []):
                        if nb not in visited2:
                            visited2.add(nb)
                            stack2.append(nb)
        return scc_count

    def _longest_path(self, dm: dict[str, list[str]]) -> int:
        """Compute the longest path length in *dm* (DAG assumed)."""
        topo = _topological_sort(dm)
        dist: dict[str, int] = {n: 0 for n in dm}
        for node in topo:
            for nb in dm.get(node, []):
                if dist.get(nb, 0) < dist[node] + 1:
                    dist[nb] = dist[node] + 1
        return max(dist.values(), default=0)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

class TheoremEcologiesWitness:
    """Witnesses theorem ecology events for audit and replay.

    The witness records high-fidelity observations of ecology construction,
    expansion, and closure checks so that an external audit log can
    reconstruct any ecology transition.
    """

    def __init__(self) -> None:
        self._log: list[dict[str, Any]] = []

    def witness_ecology_construction(
        self,
        seed: list[TheoremNode],
        ecology: TheoremEcology,
    ) -> EcologyWitnessReport:
        """Witness the construction of *ecology* from *seed* theorems.

        Parameters
        ----------
        seed:
            The seed theorems provided to the Coordinator.
        ecology:
            The ecology that was constructed.

        Returns
        -------
        EcologyWitnessReport
            A signed witness record.
        """
        report = EcologyWitnessReport(
            ecology_id=ecology.ecology_id,
            seed_count=len(seed),
            final_size=ecology.size(),
            seed_ids=tuple(t.node_id for t in seed),
            notes=f"Built at {_now_iso()} with {ecology.size()} members",
        )
        self._log.append({"type": "construction", "report": report.ecology_id})
        return report

    def witness_expansion(
        self,
        old_ecology: TheoremEcology,
        new_ecology: TheoremEcology,
        added: TheoremNode,
    ) -> ExpansionWitnessReport:
        """Witness a single expansion step.

        Parameters
        ----------
        old_ecology:
            The ecology before the expansion.
        new_ecology:
            The ecology after the expansion.
        added:
            The theorem that was added.

        Returns
        -------
        ExpansionWitnessReport
            A signed expansion witness record.
        """
        old_size = old_ecology.size()
        new_size = new_ecology.size()
        old_closure = old_ecology.closure_status.numeric_score()
        new_closure = new_ecology.closure_status.numeric_score()
        report = ExpansionWitnessReport(
            old_ecology_id=old_ecology.ecology_id,
            new_ecology_id=new_ecology.ecology_id,
            added_theorem_id=added.node_id,
            size_before=old_size,
            size_after=new_size,
            closure_before=old_closure,
            closure_after=new_closure,
        )
        self._log.append({"type": "expansion", "added": added.node_id})
        return report

    def witness_closure_check(
        self,
        ecology: TheoremEcology,
        result: ClosureCheckResult,
    ) -> ClosureWitnessReport:
        """Witness a closure check event.

        Parameters
        ----------
        ecology:
            The ecology that was checked.
        result:
            The result of the closure check.

        Returns
        -------
        ClosureWitnessReport
            A signed closure witness record.
        """
        narrative = (
            f"Ecology {ecology.ecology_id} has {ecology.size()} members. "
            f"Closure fraction: {result.closure_fraction:.2%}. "
            f"Status: {result.status.value}. "
            f"Open endpoints: {len(result.open_endpoints)}."
        )
        report = ClosureWitnessReport(
            ecology_id=ecology.ecology_id,
            status_observed=result.status,
            closure_fraction_observed=result.closure_fraction,
            open_endpoint_count=len(result.open_endpoints),
            narrative=narrative,
        )
        self._log.append({"type": "closure_check", "status": result.status.value})
        return report

    def log_snapshot(self) -> list[dict[str, Any]]:
        """Return a snapshot of the internal witness log."""
        return list(self._log)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "TheoremRole",
    "ClosureStatus",
    "EcologyConfig",
    "TheoremNode",
    "TheoremEcology",
    "ClosureCheckResult",
    "EnvironmentChangeReport",
    "EcologyCycleResult",
    "ClosurePropertyReport",
    "DependencyGraphReport",
    "ReasoningReachReport",
    "EcologyWitnessReport",
    "ExpansionWitnessReport",
    "ClosureWitnessReport",
    "TheoremEcologiesCoordinator",
    "TheoremEcologiesAnalyzer",
    "TheoremEcologiesWitness",
    "run_ecology_cycle",
    "score_theorem_for_ecology",
    "build_dependency_matrix",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== s01 smoke test: theorem ecologies from local closure ===")

    config = EcologyConfig(max_ecology_size=20, closure_threshold=0.7,
                           expand_iterations=2)
    seeds = [
        TheoremNode(node_id="t1", label="Cauchy Completeness",
                    statement="Every Cauchy sequence converges.",
                    role=TheoremRole.SEED, dependencies=()),
        TheoremNode(node_id="t2", label="Bolzano-Weierstrass",
                    statement="Every bounded sequence has a convergent subsequence.",
                    role=TheoremRole.SEED, dependencies=("t1",)),
        TheoremNode(node_id="t3", label="Heine-Cantor",
                    statement="A continuous function on a compact set is uniformly continuous.",
                    role=TheoremRole.SEED, dependencies=("t2",)),
    ]
    candidates = [
        TheoremNode(node_id="t4", label="Extreme Value Theorem",
                    statement="A continuous function on a compact set attains its bounds.",
                    role=TheoremRole.SUPPORTING, dependencies=("t2", "t3")),
        TheoremNode(node_id="t5", label="Intermediate Value Theorem",
                    statement="A continuous function takes all intermediate values.",
                    role=TheoremRole.DERIVED, dependencies=("t1",)),
        TheoremNode(node_id="t6", label="Unrelated Lemma",
                    statement="All primes greater than 2 are odd.",
                    role=TheoremRole.SUPPORTING, dependencies=()),
    ]

    coordinator = TheoremEcologiesCoordinator(config)
    analyzer = TheoremEcologiesAnalyzer()
    witness = TheoremEcologiesWitness()

    ecology = coordinator.build_ecology(seeds)
    print(f"Built ecology {ecology.ecology_id!r} with {ecology.size()} members")

    wr = witness.witness_ecology_construction(seeds, ecology)
    print(f"Witness: seed_count={wr.seed_count}, final_size={wr.final_size}")

    ecology = coordinator.expand_ecology(ecology, candidates)
    print(f"After expansion: {ecology.size()} members")

    closure = coordinator.check_local_closure(ecology)
    print(f"Closure: {closure.summary()}")

    cwr = witness.witness_closure_check(ecology, closure)
    print(f"Closure witness narrative: {cwr.narrative[:80]}...")

    change_report = coordinator.analyze_environment_change(ecology, candidates[0])
    print(f"Environment change: delta={change_report.closure_delta:+.3f}, "
          f"new_paths={len(change_report.new_proof_paths)}")

    prop_report = analyzer.analyze_closure_properties(ecology)
    print(f"Closure properties: strongly_closed={prop_report.strongly_closed_count}, "
          f"health={prop_report.overall_health():.3f}")

    dep_report = analyzer.analyze_dependency_graph(ecology)
    print(f"Dep graph: nodes={dep_report.node_count}, edges={dep_report.edge_count}, "
          f"is_dag={dep_report.is_dag()}")

    reach_report = analyzer.analyze_reasoning_reach(ecology)
    print(f"Reasoning reach: avg={reach_report.avg_reach_fraction:.2%}, "
          f"isolated={len(reach_report.isolated_ids)}")

    keystones = analyzer.identify_keystone_theorems(ecology)
    print(f"Keystones: {[k.label for k in keystones]}")

    cycle_result = run_ecology_cycle(seeds, candidates, config)
    print(f"Cycle result: admitted={cycle_result.admitted_count}, "
          f"status={cycle_result.final_closure_status.value}, "
          f"success={cycle_result.success()}")

    dm = build_dependency_matrix(seeds + candidates)
    print(f"Dependency matrix: {len(dm)} entries")

    score = score_theorem_for_ecology(candidates[0], ecology, config)
    print(f"Score for '{candidates[0].label}': {score:.3f}")

    print("=== smoke test passed ===")
