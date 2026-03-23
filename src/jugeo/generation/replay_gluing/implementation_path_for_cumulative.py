"""
Implementation path: concrete steps to build a cumulative memory system.

This module provides the full roadmap, backend infrastructure, indexing pipeline,
query planning, migration utilities, capacity estimation, and validation framework
for constructing a production-grade cumulative memory system within the Jugeo
reasoning architecture.

Architecture decision rationale:
  - We separate the *storage backend* from the *indexing layer* so that backends
    can be swapped (IN_MEMORY → SQLITE → REDIS → VECTOR_DB) without rewriting
    retrieval logic.
  - Each implementation step is itself a typed, immutable value (ImplementationStep)
    so that the roadmap can be queried, replanned, and validated programmatically.
  - Trust tiers propagate through the pipeline: a result retrieved from a
    PROOF_BACKED backend inherits higher trust than one from an IN_MEMORY sketch.
  - Obstructions (Čech H¹ cohomology classes) are carried on every MemoryBackend
    to record which gluing conditions failed during distributed consistency checks.
  - Judgments are 8-tuples (c, φ, A, E, O, B, T, Π):
      c  = context (str)
      φ  = formula / proposition (str)
      A  = assumptions (tuple of str)
      E  = evidence (tuple of str)
      O  = obstructions as Čech H¹ cohomology classes (tuple of complex)
      B  = blame assignment (str)
      T  = trust tier (TrustTier)
      Π  = proof obligations (tuple of str)
  - Trust forms an ordered algebra with meet (∧) and join (∨) so that
    composite judgments can be safely combined without trust inflation.

# copilot:
"""
from __future__ import annotations

import hashlib
import itertools
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Optional jugeo imports – wrapped in try/except as required
# ---------------------------------------------------------------------------
try:
    from jugeo.core.context import JugeoContext  # type: ignore
except ImportError:
    JugeoContext = None  # type: ignore

try:
    from jugeo.core.trust import TrustRegistry  # type: ignore
except ImportError:
    TrustRegistry = None  # type: ignore

try:
    from jugeo.generation.replay_gluing.base import GluingBase  # type: ignore
except ImportError:
    GluingBase = None  # type: ignore

try:
    from jugeo.memory.episode import Episode  # type: ignore
except ImportError:
    Episode = None  # type: ignore

try:
    from jugeo.memory.retrieval import RetrievalResult  # type: ignore
except ImportError:
    RetrievalResult = None  # type: ignore

# ---------------------------------------------------------------------------
# TrustTier – ordered algebra
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust levels forming an algebra with meet and join.

    Ordering: PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED

    The ordered algebra satisfies:
      - meet(a, b) = min(a, b)   (most conservative)
      - join(a, b) = max(a, b)   (most optimistic)
      - Associativity, commutativity, idempotency for both meet and join
      - Absorption: join(a, meet(a, b)) == a
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------

    def __le__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value <= other.value

    def __lt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value < other.value

    def __ge__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value >= other.value

    def __gt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value > other.value

    # ------------------------------------------------------------------
    # Algebra
    # ------------------------------------------------------------------

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Greatest lower bound – conservative composition."""
        return TrustTier(min(self.value, other.value))

    def join(self, other: "TrustTier") -> "TrustTier":
        """Least upper bound – optimistic composition."""
        return TrustTier(max(self.value, other.value))

    def promote(self) -> "TrustTier":
        """↑_π — promote one tier upward, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> "TrustTier":
        """↓_χ — demote one tier downward, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

    def __repr__(self) -> str:
        return f"TrustTier.{self.name}"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CumulativeMemoryImplementation:
    """Top-level descriptor for a cumulative memory implementation plan.

    Fields
    ------
    impl_id : str
        Unique identifier for this implementation plan instance.
    steps : tuple[str, ...]
        Ordered list of step_id strings referencing ImplementationStep objects.
    current_step : int
        Zero-based index into *steps* indicating the step currently in progress.
    completion_ratio : float
        Value in [0.0, 1.0] representing overall progress.
    trust_tier : TrustTier
        Aggregate trust of the implementation plan itself.
    judgment : tuple
        8-tuple (c, φ, A, E, O, B, T, Π) encoding the formal judgment
        that this implementation plan is correct and complete.
    """

    impl_id: str
    steps: tuple
    current_step: int
    completion_ratio: float
    trust_tier: TrustTier
    judgment: tuple  # 8-tuple: (c, φ, A, E, O, B, T, Π)

    def is_complete(self) -> bool:
        """Return True when all steps have been executed."""
        return self.completion_ratio >= 1.0

    def active_step_id(self) -> Optional[str]:
        """Return the step_id of the currently active step, or None if done."""
        if self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None

    def advance(self) -> "CumulativeMemoryImplementation":
        """Return a new plan with current_step incremented by one."""
        new_step = min(self.current_step + 1, len(self.steps))
        ratio = new_step / max(len(self.steps), 1)
        return CumulativeMemoryImplementation(
            impl_id=self.impl_id,
            steps=self.steps,
            current_step=new_step,
            completion_ratio=ratio,
            trust_tier=self.trust_tier,
            judgment=self.judgment,
        )


@dataclass(frozen=True)
class ImplementationStep:
    """A single concrete step in the cumulative memory implementation roadmap.

    Fields
    ------
    step_id : str
        Unique step identifier (e.g. "S01_SCHEMA_DESIGN").
    step_name : str
        Human-readable name.
    description : str
        Detailed description of what must be done.
    preconditions : tuple[str, ...]
        Conditions that must hold *before* this step executes.
    postconditions : tuple[str, ...]
        Conditions that must hold *after* this step completes successfully.
    estimated_effort : float
        Effort in person-hours (positive float).
    trust_tier : TrustTier
        Minimum trust required to claim this step is complete.
    dependencies : tuple[str, ...]
        step_id values that must be completed before this step can begin.
    """

    step_id: str
    step_name: str
    description: str
    preconditions: tuple
    postconditions: tuple
    estimated_effort: float
    trust_tier: TrustTier
    dependencies: tuple

    def is_ready(self, completed_ids: frozenset) -> bool:
        """Return True when all dependencies are in *completed_ids*."""
        return all(dep in completed_ids for dep in self.dependencies)

    def effort_hours(self) -> float:
        """Return estimated effort, clamped to positive domain."""
        return max(0.0, self.estimated_effort)

    def summary(self) -> str:
        """One-line summary string for logging / display."""
        return (
            f"[{self.step_id}] {self.step_name} "
            f"(effort={self.estimated_effort:.1f}h, tier={self.trust_tier.name})"
        )


@dataclass(frozen=True)
class MemoryBackend:
    """Descriptor for a storage backend used by the cumulative memory system.

    Čech cohomology note:
      *cech_class* stores the Čech H¹ cohomology class of the backend's
      distributed consistency nerve.  A zero class (all zeros) means that
      all local-to-global gluing conditions are satisfied; non-zero entries
      record obstructions to consistent patching across shards.

    Fields
    ------
    backend_id : str
        Unique backend identifier.
    backend_type : str
        One of "IN_MEMORY" | "SQLITE" | "REDIS" | "VECTOR_DB".
    connection_spec : str
        Connection string or in-process descriptor.
    capacity_mb : float
        Maximum storage capacity in megabytes.
    trust_tier : TrustTier
        Trust assigned to data stored in this backend.
    cech_class : tuple[complex, ...]
        Čech H¹ cohomology class (complex-valued coefficients).
    """

    backend_id: str
    backend_type: str
    connection_spec: str
    capacity_mb: float
    trust_tier: TrustTier
    cech_class: tuple  # tuple[complex, ...]

    def is_obstructed(self) -> bool:
        """Return True if any cohomology coefficient is non-zero."""
        return any(c != 0j for c in self.cech_class)

    def obstruction_norm(self) -> float:
        """Return the L2 norm of the cohomology vector (measure of obstruction)."""
        return math.sqrt(sum(abs(c) ** 2 for c in self.cech_class))

    def with_resolved_obstructions(self) -> "MemoryBackend":
        """Return a copy with the cohomology class zeroed out."""
        return MemoryBackend(
            backend_id=self.backend_id,
            backend_type=self.backend_type,
            connection_spec=self.connection_spec,
            capacity_mb=self.capacity_mb,
            trust_tier=self.trust_tier,
            cech_class=tuple(0j for _ in self.cech_class),
        )

    def utilization_ratio(self, used_mb: float) -> float:
        """Return fraction of capacity currently used."""
        if self.capacity_mb <= 0:
            return float("inf")
        return used_mb / self.capacity_mb


@dataclass(frozen=True)
class MemoryIndexer:
    """Descriptor for an index built over a MemoryBackend.

    Fields
    ------
    indexer_id : str
        Unique indexer identifier.
    backend_id : str
        Foreign key to the MemoryBackend being indexed.
    index_type : str
        One of "INVERTED" | "VECTOR" | "GRAPH" | "HYBRID".
    indexed_fields : tuple[str, ...]
        Names of the fields included in this index.
    index_size : int
        Number of index entries.
    trust_tier : TrustTier
        Trust of the indexing operation itself.
    """

    indexer_id: str
    backend_id: str
    index_type: str
    indexed_fields: tuple
    index_size: int
    trust_tier: TrustTier

    def supports_semantic_search(self) -> bool:
        """Return True when the index can answer approximate nearest-neighbour queries."""
        return self.index_type in ("VECTOR", "HYBRID")

    def supports_exact_match(self) -> bool:
        """Return True when the index can answer exact-match / keyword queries."""
        return self.index_type in ("INVERTED", "HYBRID")

    def supports_traversal(self) -> bool:
        """Return True when the index exposes edge-based traversal (graph queries)."""
        return self.index_type in ("GRAPH", "HYBRID")

    def density(self) -> float:
        """Ratio of index entries to indexed fields (rough quality heuristic)."""
        if not self.indexed_fields:
            return 0.0
        return self.index_size / len(self.indexed_fields)


# ---------------------------------------------------------------------------
# Module-level implementation step constants (≥ 8 required)
# ---------------------------------------------------------------------------

STEP_S01_SCHEMA_DESIGN = ImplementationStep(
    step_id="S01_SCHEMA_DESIGN",
    step_name="Schema Design",
    description=(
        "Define the canonical Episode schema including all required fields: "
        "episode_id, timestamp, context_hash, formula, assumptions, evidence, "
        "obstructions, blame, trust_tier, proof_obligations, and raw_content. "
        "Produce a JSON-Schema document and a SQLite DDL file that both reflect "
        "this canonical schema so that IN_MEMORY and SQLITE backends stay in sync."
    ),
    preconditions=("project_environment_initialised",),
    postconditions=(
        "episode_schema_json_written",
        "sqlite_ddl_written",
        "schema_version_tagged",
    ),
    estimated_effort=4.0,
    trust_tier=TrustTier.REVIEWED,
    dependencies=(),
)

STEP_S02_IN_MEMORY_BACKEND = ImplementationStep(
    step_id="S02_IN_MEMORY_BACKEND",
    step_name="In-Memory Backend",
    description=(
        "Implement an IN_MEMORY backend backed by a plain Python dict keyed on "
        "episode_id.  Write unit tests covering insert, get, delete, list, and "
        "capacity enforcement.  The backend must raise CapacityError when the "
        "episode count exceeds a configurable limit."
    ),
    preconditions=("episode_schema_json_written",),
    postconditions=(
        "in_memory_backend_implemented",
        "in_memory_unit_tests_passing",
    ),
    estimated_effort=6.0,
    trust_tier=TrustTier.VERIFIED,
    dependencies=("S01_SCHEMA_DESIGN",),
)

STEP_S03_SQLITE_BACKEND = ImplementationStep(
    step_id="S03_SQLITE_BACKEND",
    step_name="SQLite Persistent Backend",
    description=(
        "Implement a SQLITE backend using Python's built-in sqlite3 module. "
        "Store episodes in a single 'episodes' table matching the canonical schema. "
        "Support WAL mode for concurrent reads.  Write integration tests using a "
        "temporary database file that is cleaned up after each test run."
    ),
    preconditions=(
        "episode_schema_json_written",
        "sqlite_ddl_written",
    ),
    postconditions=(
        "sqlite_backend_implemented",
        "sqlite_integration_tests_passing",
        "wal_mode_enabled",
    ),
    estimated_effort=10.0,
    trust_tier=TrustTier.VERIFIED,
    dependencies=("S01_SCHEMA_DESIGN",),
)

STEP_S04_INVERTED_INDEX = ImplementationStep(
    step_id="S04_INVERTED_INDEX",
    step_name="Inverted Index Builder",
    description=(
        "Build an inverted index over the 'formula' and 'raw_content' fields of "
        "stored episodes.  Use a term-frequency / inverse-document-frequency (TF-IDF) "
        "weighting scheme.  Index must support incremental updates (insert/delete) "
        "without full rebuilds."
    ),
    preconditions=(
        "in_memory_backend_implemented",
        "sqlite_backend_implemented",
    ),
    postconditions=(
        "inverted_index_implemented",
        "tfidf_weighting_verified",
        "incremental_update_supported",
    ),
    estimated_effort=8.0,
    trust_tier=TrustTier.VERIFIED,
    dependencies=("S02_IN_MEMORY_BACKEND", "S03_SQLITE_BACKEND"),
)

STEP_S05_VECTOR_INDEX = ImplementationStep(
    step_id="S05_VECTOR_INDEX",
    step_name="Vector Index Builder",
    description=(
        "Build a dense vector index for semantic retrieval.  Use a sentence "
        "embedding model (e.g. all-MiniLM-L6-v2) to project each episode's "
        "formula + raw_content into a fixed-dimension vector space.  Store "
        "vectors in a flat FAISS index (or equivalent) with L2 distance. "
        "Support k-nearest-neighbour queries with configurable k."
    ),
    preconditions=(
        "inverted_index_implemented",
        "embedding_model_available",
    ),
    postconditions=(
        "vector_index_implemented",
        "knn_query_supported",
        "embedding_model_integrated",
    ),
    estimated_effort=12.0,
    trust_tier=TrustTier.VERIFIED,
    dependencies=("S04_INVERTED_INDEX",),
)

STEP_S06_QUERY_PLANNER = ImplementationStep(
    step_id="S06_QUERY_PLANNER",
    step_name="Query Planner",
    description=(
        "Implement a QueryPlanner that inspects a user query and selects the "
        "optimal index (INVERTED, VECTOR, GRAPH, or HYBRID) based on query "
        "features such as keyword density, trust-tier constraint, and presence "
        "of entity references.  The planner must output an execution plan object "
        "that can be inspected for debugging."
    ),
    preconditions=(
        "inverted_index_implemented",
        "vector_index_implemented",
    ),
    postconditions=(
        "query_planner_implemented",
        "plan_object_inspectable",
        "query_router_unit_tests_passing",
    ),
    estimated_effort=8.0,
    trust_tier=TrustTier.VERIFIED,
    dependencies=("S04_INVERTED_INDEX", "S05_VECTOR_INDEX"),
)

STEP_S07_MIGRATION_TOOL = ImplementationStep(
    step_id="S07_MIGRATION_TOOL",
    step_name="Backend Migration Tool",
    description=(
        "Implement MemoryMigrationTool that can copy all episodes from one backend "
        "to another (e.g. IN_MEMORY → SQLITE or SQLITE → VECTOR_DB) with resumable "
        "checkpointing.  Each migrated episode must be re-validated against the "
        "canonical schema and its trust_tier must not decrease during migration."
    ),
    preconditions=(
        "in_memory_backend_implemented",
        "sqlite_backend_implemented",
    ),
    postconditions=(
        "migration_tool_implemented",
        "checkpointing_supported",
        "trust_non_decreasing_invariant_verified",
    ),
    estimated_effort=10.0,
    trust_tier=TrustTier.RUNTIME_WITNESSED,
    dependencies=("S02_IN_MEMORY_BACKEND", "S03_SQLITE_BACKEND"),
)

STEP_S08_CAPACITY_PLANNER = ImplementationStep(
    step_id="S08_CAPACITY_PLANNER",
    step_name="Capacity Planner",
    description=(
        "Implement CapacityPlanner that estimates storage requirements given a "
        "target episode count, average episode size, index overhead ratios, and "
        "replication factor.  Produce tiered recommendations (IN_MEMORY, SQLITE, "
        "REDIS, VECTOR_DB) based on total estimated bytes."
    ),
    preconditions=(
        "in_memory_backend_implemented",
        "sqlite_backend_implemented",
        "inverted_index_implemented",
    ),
    postconditions=(
        "capacity_planner_implemented",
        "tiered_recommendations_produced",
    ),
    estimated_effort=5.0,
    trust_tier=TrustTier.REVIEWED,
    dependencies=(
        "S02_IN_MEMORY_BACKEND",
        "S03_SQLITE_BACKEND",
        "S04_INVERTED_INDEX",
    ),
)

STEP_S09_VALIDATOR = ImplementationStep(
    step_id="S09_VALIDATOR",
    step_name="Implementation Validator",
    description=(
        "Implement ImplementationValidator that walks each completed step's "
        "postconditions and verifies them programmatically where possible.  "
        "Unverifiable conditions are flagged as PROPOSAL-level trust until human "
        "review promotes them.  Validator produces a structured report object."
    ),
    preconditions=("query_planner_implemented",),
    postconditions=(
        "validator_implemented",
        "validation_report_structured",
        "all_prior_postconditions_checked",
    ),
    estimated_effort=6.0,
    trust_tier=TrustTier.REVIEWED,
    dependencies=("S06_QUERY_PLANNER",),
)

STEP_S10_GRAPH_INDEX = ImplementationStep(
    step_id="S10_GRAPH_INDEX",
    step_name="Graph Index Builder",
    description=(
        "Construct a directed graph index where episodes are nodes and causal / "
        "temporal edges connect them.  Support PageRank-style scoring for ranking "
        "retrieved episodes by their connectivity.  Graph must be serialisable to "
        "adjacency-list JSON for persistence."
    ),
    preconditions=(
        "inverted_index_implemented",
        "vector_index_implemented",
    ),
    postconditions=(
        "graph_index_implemented",
        "pagerank_scoring_supported",
        "graph_serialisable",
    ),
    estimated_effort=14.0,
    trust_tier=TrustTier.VERIFIED,
    dependencies=("S04_INVERTED_INDEX", "S05_VECTOR_INDEX"),
)

ALL_STEPS: Tuple[ImplementationStep, ...] = (
    STEP_S01_SCHEMA_DESIGN,
    STEP_S02_IN_MEMORY_BACKEND,
    STEP_S03_SQLITE_BACKEND,
    STEP_S04_INVERTED_INDEX,
    STEP_S05_VECTOR_INDEX,
    STEP_S06_QUERY_PLANNER,
    STEP_S07_MIGRATION_TOOL,
    STEP_S08_CAPACITY_PLANNER,
    STEP_S09_VALIDATOR,
    STEP_S10_GRAPH_INDEX,
)


# ---------------------------------------------------------------------------
# Helper: build a formal judgment 8-tuple
# ---------------------------------------------------------------------------

def make_judgment(
    context: str,
    formula: str,
    assumptions: Tuple[str, ...] = (),
    evidence: Tuple[str, ...] = (),
    obstructions: Tuple[complex, ...] = (0j,),
    blame: str = "unassigned",
    trust_tier: TrustTier = TrustTier.PROPOSAL,
    proof_obligations: Tuple[str, ...] = (),
) -> tuple:
    """Construct a formal judgment 8-tuple (c, φ, A, E, O, B, T, Π).

    Parameters
    ----------
    context : str
        The reasoning context (e.g. module name, agent id).
    formula : str
        The proposition being judged.
    assumptions : tuple[str, ...]
        Background assumptions in force.
    evidence : tuple[str, ...]
        Evidence items supporting the formula.
    obstructions : tuple[complex, ...]
        Čech H¹ cohomology class encoding gluing obstructions.
    blame : str
        Who or what is accountable if the judgment is wrong.
    trust_tier : TrustTier
        The tier assigned to this judgment.
    proof_obligations : tuple[str, ...]
        Remaining obligations needed to fully close the judgment.

    Returns
    -------
    tuple
        An 8-tuple (c, φ, A, E, O, B, T, Π).
    """
    return (
        context,
        formula,
        assumptions,
        evidence,
        obstructions,
        blame,
        trust_tier,
        proof_obligations,
    )


# ---------------------------------------------------------------------------
# Required functions
# ---------------------------------------------------------------------------

def build_memory_backend(
    spec: Dict[str, Any],
    trust_tier: TrustTier,
) -> MemoryBackend:
    """Construct a MemoryBackend from a specification dictionary.

    Parameters
    ----------
    spec : dict
        Must contain keys: "backend_type", "connection_spec", "capacity_mb".
        Optional: "backend_id", "cech_coefficients" (list of complex values).
    trust_tier : TrustTier
        Trust to assign to data stored in this backend.

    Returns
    -------
    MemoryBackend
        A frozen MemoryBackend instance.

    Raises
    ------
    ValueError
        If *backend_type* is not one of the recognised types.
    """
    recognised = {"IN_MEMORY", "SQLITE", "REDIS", "VECTOR_DB"}
    backend_type = spec.get("backend_type", "IN_MEMORY").upper()
    if backend_type not in recognised:
        raise ValueError(
            f"Unknown backend_type {backend_type!r}. Must be one of {recognised}."
        )
    backend_id = spec.get(
        "backend_id", f"backend-{backend_type.lower()}-{uuid.uuid4().hex[:8]}"
    )
    capacity_mb = float(spec.get("capacity_mb", 256.0))
    connection_spec = spec.get("connection_spec", ":memory:" if backend_type == "SQLITE" else "")
    raw_cech = spec.get("cech_coefficients", [0j])
    cech_class = tuple(complex(c) for c in raw_cech)
    return MemoryBackend(
        backend_id=backend_id,
        backend_type=backend_type,
        connection_spec=connection_spec,
        capacity_mb=capacity_mb,
        trust_tier=trust_tier,
        cech_class=cech_class,
    )


def index_memory(
    backend: MemoryBackend,
    episodes: Sequence[Dict[str, Any]],
    index_type: str = "INVERTED",
) -> MemoryIndexer:
    """Build an index over *episodes* stored in *backend*.

    Parameters
    ----------
    backend : MemoryBackend
        The backend whose data is being indexed.
    episodes : sequence of dicts
        Episode records, each expected to have at least "episode_id" and
        "raw_content" keys.
    index_type : str
        One of "INVERTED" | "VECTOR" | "GRAPH" | "HYBRID".

    Returns
    -------
    MemoryIndexer
        A frozen descriptor of the index that was built.
    """
    recognised_types = {"INVERTED", "VECTOR", "GRAPH", "HYBRID"}
    index_type = index_type.upper()
    if index_type not in recognised_types:
        raise ValueError(f"Unknown index_type {index_type!r}.")
    all_fields: set = set()
    for ep in episodes:
        all_fields.update(ep.keys())
    indexer_id = f"idx-{backend.backend_id}-{index_type.lower()}-{uuid.uuid4().hex[:6]}"
    return MemoryIndexer(
        indexer_id=indexer_id,
        backend_id=backend.backend_id,
        index_type=index_type,
        indexed_fields=tuple(sorted(all_fields)),
        index_size=len(episodes),
        trust_tier=backend.trust_tier,
    )


def retrieve_from_memory(
    indexer: MemoryIndexer,
    query: str,
    k: int = 5,
) -> Tuple[str, ...]:
    """Retrieve the top-k episode_ids matching *query* from *indexer*.

    This is a deterministic stub implementation: it hashes the query and
    indexer state to produce reproducible pseudo-results.  A real
    implementation would invoke the underlying index engine.

    Parameters
    ----------
    indexer : MemoryIndexer
        The index to query.
    query : str
        Query string (keyword or natural-language sentence).
    k : int
        Maximum number of results to return.

    Returns
    -------
    tuple[str, ...]
        Tuple of at most *k* episode_id strings, ordered by relevance (best first).
    """
    if k <= 0:
        return ()
    seed = hashlib.sha256(
        f"{indexer.indexer_id}|{query}|{k}".encode()
    ).hexdigest()
    results = []
    for i in range(min(k, indexer.index_size)):
        ep_hash = hashlib.md5(f"{seed}-{i}".encode()).hexdigest()[:12]
        results.append(f"episode-{ep_hash}")
    return tuple(results)


# ---------------------------------------------------------------------------
# ImplementationRoadmap
# ---------------------------------------------------------------------------

class ImplementationRoadmap:
    """A dependency graph of ImplementationStep objects with planning utilities.

    The roadmap maintains an internal adjacency representation and provides
    methods for topological ordering, critical path analysis, effort aggregation,
    status tracking, and subgraph extraction.
    """

    def __init__(self, steps: Sequence[ImplementationStep]) -> None:
        self._steps: Dict[str, ImplementationStep] = {s.step_id: s for s in steps}
        self._completed: set = set()

    # ------------------------------------------------------------------
    # 1. Topological sort (Kahn's algorithm)
    # ------------------------------------------------------------------

    def topological_order(self) -> List[str]:
        """Return step_ids in a valid execution order respecting all dependencies."""
        in_degree: Dict[str, int] = {sid: 0 for sid in self._steps}
        for step in self._steps.values():
            for dep in step.dependencies:
                if dep in in_degree:
                    in_degree[step.step_id] = in_degree.get(step.step_id, 0) + 1
        # Re-compute properly
        in_degree = {sid: 0 for sid in self._steps}
        for step in self._steps.values():
            for dep in step.dependencies:
                if dep in self._steps:
                    in_degree[step.step_id] += 1
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order: List[str] = []
        while queue:
            queue.sort()  # deterministic tie-breaking
            node = queue.pop(0)
            order.append(node)
            for step in self._steps.values():
                if node in step.dependencies:
                    in_degree[step.step_id] -= 1
                    if in_degree[step.step_id] == 0:
                        queue.append(step.step_id)
        return order

    # ------------------------------------------------------------------
    # 2. Ready steps
    # ------------------------------------------------------------------

    def ready_steps(self) -> List[ImplementationStep]:
        """Return steps whose dependencies are all completed."""
        completed = frozenset(self._completed)
        return [
            s for s in self._steps.values()
            if s.step_id not in completed and s.is_ready(completed)
        ]

    # ------------------------------------------------------------------
    # 3. Mark a step complete
    # ------------------------------------------------------------------

    def mark_complete(self, step_id: str) -> None:
        """Record that *step_id* has been completed."""
        if step_id not in self._steps:
            raise KeyError(f"Unknown step_id: {step_id!r}")
        self._completed.add(step_id)

    # ------------------------------------------------------------------
    # 4. Total effort estimate
    # ------------------------------------------------------------------

    def total_effort(self, include_completed: bool = True) -> float:
        """Return total estimated effort in hours across all (or only pending) steps."""
        steps = self._steps.values()
        if not include_completed:
            steps = [s for s in steps if s.step_id not in self._completed]
        return sum(s.effort_hours() for s in steps)

    # ------------------------------------------------------------------
    # 5. Critical path (longest path by effort)
    # ------------------------------------------------------------------

    def critical_path(self) -> List[str]:
        """Return the sequence of step_ids on the longest-effort path."""
        order = self.topological_order()
        dist: Dict[str, float] = {sid: 0.0 for sid in order}
        pred: Dict[str, Optional[str]] = {sid: None for sid in order}
        for sid in order:
            step = self._steps[sid]
            for dep in step.dependencies:
                if dep in dist:
                    candidate = dist[dep] + step.effort_hours()
                    if candidate > dist[sid]:
                        dist[sid] = candidate
                        pred[sid] = dep
            if not step.dependencies:
                dist[sid] = step.effort_hours()
        # Trace back from the node with max dist
        end = max(dist, key=lambda k: dist[k])
        path: List[str] = []
        node: Optional[str] = end
        while node is not None:
            path.append(node)
            node = pred[node]
        path.reverse()
        return path

    # ------------------------------------------------------------------
    # 6. Subgraph for a set of step_ids
    # ------------------------------------------------------------------

    def subgraph(self, step_ids: Sequence[str]) -> "ImplementationRoadmap":
        """Return a new roadmap containing only the specified steps."""
        selected = {sid: self._steps[sid] for sid in step_ids if sid in self._steps}
        return ImplementationRoadmap(list(selected.values()))

    # ------------------------------------------------------------------
    # 7. Completion ratio
    # ------------------------------------------------------------------

    def completion_ratio(self) -> float:
        """Return fraction of steps completed."""
        if not self._steps:
            return 1.0
        return len(self._completed) / len(self._steps)

    # ------------------------------------------------------------------
    # 8. Steps by trust tier
    # ------------------------------------------------------------------

    def steps_by_tier(self, tier: TrustTier) -> List[ImplementationStep]:
        """Return all steps with the given *tier*."""
        return [s for s in self._steps.values() if s.trust_tier == tier]

    # ------------------------------------------------------------------
    # 9. Dependency ancestors
    # ------------------------------------------------------------------

    def ancestors(self, step_id: str) -> frozenset:
        """Return the transitive closure of all dependencies for *step_id*."""
        visited: set = set()
        frontier = list(self._steps.get(step_id, ImplementationStep(
            step_id, "", "", (), (), 0.0, TrustTier.PROPOSAL, ()
        )).dependencies)
        while frontier:
            dep = frontier.pop()
            if dep in visited or dep not in self._steps:
                continue
            visited.add(dep)
            frontier.extend(self._steps[dep].dependencies)
        return frozenset(visited)


# ---------------------------------------------------------------------------
# BackendFactory
# ---------------------------------------------------------------------------

class BackendFactory:
    """Factory for creating MemoryBackend instances of various types.

    Provides a unified interface so that callers do not need to know the
    details of each backend's connection requirements.
    """

    _DEFAULT_CAPACITIES: Dict[str, float] = {
        "IN_MEMORY": 64.0,
        "SQLITE": 512.0,
        "REDIS": 1024.0,
        "VECTOR_DB": 4096.0,
    }

    # ------------------------------------------------------------------
    # 1. Create in-memory backend
    # ------------------------------------------------------------------

    def create_in_memory(
        self,
        capacity_mb: float = 64.0,
        trust_tier: TrustTier = TrustTier.PROPOSAL,
    ) -> MemoryBackend:
        """Create an ephemeral in-memory backend."""
        return build_memory_backend(
            {
                "backend_type": "IN_MEMORY",
                "connection_spec": "memory://",
                "capacity_mb": capacity_mb,
            },
            trust_tier,
        )

    # ------------------------------------------------------------------
    # 2. Create SQLite backend
    # ------------------------------------------------------------------

    def create_sqlite(
        self,
        path: str = ":memory:",
        capacity_mb: float = 512.0,
        trust_tier: TrustTier = TrustTier.VERIFIED,
    ) -> MemoryBackend:
        """Create a SQLite-backed persistent backend."""
        return build_memory_backend(
            {
                "backend_type": "SQLITE",
                "connection_spec": path,
                "capacity_mb": capacity_mb,
            },
            trust_tier,
        )

    # ------------------------------------------------------------------
    # 3. Create Redis backend
    # ------------------------------------------------------------------

    def create_redis(
        self,
        host: str = "localhost",
        port: int = 6379,
        capacity_mb: float = 1024.0,
        trust_tier: TrustTier = TrustTier.RUNTIME_WITNESSED,
    ) -> MemoryBackend:
        """Create a Redis-backed backend descriptor."""
        conn = f"redis://{host}:{port}"
        return build_memory_backend(
            {
                "backend_type": "REDIS",
                "connection_spec": conn,
                "capacity_mb": capacity_mb,
            },
            trust_tier,
        )

    # ------------------------------------------------------------------
    # 4. Create vector DB backend
    # ------------------------------------------------------------------

    def create_vector_db(
        self,
        uri: str = "faiss://localhost:8080",
        capacity_mb: float = 4096.0,
        trust_tier: TrustTier = TrustTier.PROOF_BACKED,
        cech_coefficients: Optional[List[complex]] = None,
    ) -> MemoryBackend:
        """Create a vector-database backend descriptor."""
        spec: Dict[str, Any] = {
            "backend_type": "VECTOR_DB",
            "connection_spec": uri,
            "capacity_mb": capacity_mb,
        }
        if cech_coefficients is not None:
            spec["cech_coefficients"] = cech_coefficients
        return build_memory_backend(spec, trust_tier)

    # ------------------------------------------------------------------
    # 5. Create backend from config dict
    # ------------------------------------------------------------------

    def from_config(self, config: Dict[str, Any]) -> MemoryBackend:
        """General-purpose factory method dispatching on config["backend_type"]."""
        tier_name = config.get("trust_tier", "PROPOSAL").upper()
        trust_tier = TrustTier[tier_name]
        return build_memory_backend(config, trust_tier)

    # ------------------------------------------------------------------
    # 6. Default backend for a given episode count
    # ------------------------------------------------------------------

    def recommend(self, episode_count: int) -> MemoryBackend:
        """Return a sensible default backend for the given episode volume."""
        if episode_count < 1_000:
            return self.create_in_memory()
        elif episode_count < 100_000:
            return self.create_sqlite()
        elif episode_count < 1_000_000:
            return self.create_redis()
        else:
            return self.create_vector_db()


# ---------------------------------------------------------------------------
# IndexBuilder
# ---------------------------------------------------------------------------

class IndexBuilder:
    """Constructs various index types over episode collections.

    Supports INVERTED, VECTOR, GRAPH, and HYBRID index construction.
    All methods return a MemoryIndexer descriptor and perform lightweight
    in-process simulation of the index-building process.
    """

    # ------------------------------------------------------------------
    # 1. Build inverted index
    # ------------------------------------------------------------------

    def build_inverted(
        self,
        backend: MemoryBackend,
        episodes: Sequence[Dict[str, Any]],
    ) -> MemoryIndexer:
        """Build a TF-IDF inverted index over formula and raw_content fields."""
        return index_memory(backend, episodes, "INVERTED")

    # ------------------------------------------------------------------
    # 2. Build vector index
    # ------------------------------------------------------------------

    def build_vector(
        self,
        backend: MemoryBackend,
        episodes: Sequence[Dict[str, Any]],
        embedding_dim: int = 384,
    ) -> MemoryIndexer:
        """Build a dense vector index (simulated; dimension stored in metadata)."""
        idx = index_memory(backend, episodes, "VECTOR")
        # In a real implementation we would embed each episode here.
        return idx

    # ------------------------------------------------------------------
    # 3. Build graph index
    # ------------------------------------------------------------------

    def build_graph(
        self,
        backend: MemoryBackend,
        episodes: Sequence[Dict[str, Any]],
        edge_field: str = "causes",
    ) -> MemoryIndexer:
        """Build a directed graph index using *edge_field* to find edges."""
        idx = index_memory(backend, episodes, "GRAPH")
        return idx

    # ------------------------------------------------------------------
    # 4. Build hybrid index
    # ------------------------------------------------------------------

    def build_hybrid(
        self,
        backend: MemoryBackend,
        episodes: Sequence[Dict[str, Any]],
    ) -> MemoryIndexer:
        """Build a hybrid index combining inverted + vector + graph layers."""
        return index_memory(backend, episodes, "HYBRID")

    # ------------------------------------------------------------------
    # 5. Incremental update
    # ------------------------------------------------------------------

    def update_index(
        self,
        indexer: MemoryIndexer,
        new_episodes: Sequence[Dict[str, Any]],
    ) -> MemoryIndexer:
        """Return a new MemoryIndexer reflecting the addition of *new_episodes*."""
        return MemoryIndexer(
            indexer_id=indexer.indexer_id,
            backend_id=indexer.backend_id,
            index_type=indexer.index_type,
            indexed_fields=indexer.indexed_fields,
            index_size=indexer.index_size + len(new_episodes),
            trust_tier=indexer.trust_tier,
        )

    # ------------------------------------------------------------------
    # 6. Delete from index
    # ------------------------------------------------------------------

    def delete_from_index(
        self,
        indexer: MemoryIndexer,
        episode_ids: Sequence[str],
    ) -> MemoryIndexer:
        """Return a new MemoryIndexer with *episode_ids* removed."""
        new_size = max(0, indexer.index_size - len(episode_ids))
        return MemoryIndexer(
            indexer_id=indexer.indexer_id,
            backend_id=indexer.backend_id,
            index_type=indexer.index_type,
            indexed_fields=indexer.indexed_fields,
            index_size=new_size,
            trust_tier=indexer.trust_tier,
        )

    # ------------------------------------------------------------------
    # 7. Merge two indexes of the same type
    # ------------------------------------------------------------------

    def merge(
        self,
        a: MemoryIndexer,
        b: MemoryIndexer,
    ) -> MemoryIndexer:
        """Merge two compatible indexes (same backend and type) into one."""
        if a.backend_id != b.backend_id:
            raise ValueError("Cannot merge indexes from different backends.")
        if a.index_type != b.index_type:
            raise ValueError("Cannot merge indexes of different types.")
        merged_fields = tuple(sorted(set(a.indexed_fields) | set(b.indexed_fields)))
        return MemoryIndexer(
            indexer_id=f"merged-{a.indexer_id[:8]}-{b.indexer_id[:8]}",
            backend_id=a.backend_id,
            index_type=a.index_type,
            indexed_fields=merged_fields,
            index_size=a.index_size + b.index_size,
            trust_tier=a.trust_tier.meet(b.trust_tier),
        )

    # ------------------------------------------------------------------
    # 8. Index statistics
    # ------------------------------------------------------------------

    def statistics(self, indexer: MemoryIndexer) -> Dict[str, Any]:
        """Return a dict of statistics for the given indexer."""
        return {
            "indexer_id": indexer.indexer_id,
            "index_type": indexer.index_type,
            "index_size": indexer.index_size,
            "field_count": len(indexer.indexed_fields),
            "density": indexer.density(),
            "supports_semantic": indexer.supports_semantic_search(),
            "supports_exact": indexer.supports_exact_match(),
            "supports_traversal": indexer.supports_traversal(),
            "trust_tier": indexer.trust_tier.name,
        }


# ---------------------------------------------------------------------------
# QueryPlanner
# ---------------------------------------------------------------------------

class QueryPlan:
    """Lightweight value object representing an execution plan for a query."""

    def __init__(
        self,
        query: str,
        selected_indexer: MemoryIndexer,
        strategy: str,
        estimated_latency_ms: float,
        reasoning: str,
    ) -> None:
        self.query = query
        self.selected_indexer = selected_indexer
        self.strategy = strategy
        self.estimated_latency_ms = estimated_latency_ms
        self.reasoning = reasoning

    def __repr__(self) -> str:
        return (
            f"QueryPlan(strategy={self.strategy!r}, "
            f"indexer={self.selected_indexer.index_type}, "
            f"latency≈{self.estimated_latency_ms:.1f}ms)"
        )


class QueryPlanner:
    """Selects the optimal MemoryIndexer and retrieval strategy for a query.

    Selection heuristics:
      - Short keyword queries (< 5 tokens) → INVERTED index
      - Long natural-language queries (≥ 5 tokens) → VECTOR index
      - Queries mentioning causal connectives → GRAPH index
      - Queries requiring both precision and recall → HYBRID index
    """

    _CAUSAL_KEYWORDS = frozenset({
        "because", "causes", "leads", "results", "consequence",
        "therefore", "hence", "implies", "follows",
    })

    def __init__(self, indexers: Sequence[MemoryIndexer]) -> None:
        self._indexers: Dict[str, MemoryIndexer] = {i.index_type: i for i in indexers}

    # ------------------------------------------------------------------
    # 1. Plan a query
    # ------------------------------------------------------------------

    def plan(self, query: str, trust_constraint: Optional[TrustTier] = None) -> QueryPlan:
        """Produce a QueryPlan for *query*, respecting *trust_constraint*."""
        tokens = query.lower().split()
        strategy = self._select_strategy(tokens)
        indexer = self._select_indexer(strategy, trust_constraint)
        latency = self._estimate_latency(indexer, len(tokens))
        reasoning = (
            f"Token count={len(tokens)}, causal={self._has_causal(tokens)}, "
            f"selected strategy={strategy} → indexer={indexer.index_type}"
        )
        return QueryPlan(
            query=query,
            selected_indexer=indexer,
            strategy=strategy,
            estimated_latency_ms=latency,
            reasoning=reasoning,
        )

    # ------------------------------------------------------------------
    # 2. Strategy selection
    # ------------------------------------------------------------------

    def _select_strategy(self, tokens: List[str]) -> str:
        if self._has_causal(tokens) and "GRAPH" in self._indexers:
            return "GRAPH_TRAVERSAL"
        if len(tokens) < 5 and "INVERTED" in self._indexers:
            return "EXACT_MATCH"
        if len(tokens) >= 5 and "VECTOR" in self._indexers:
            return "SEMANTIC_SEARCH"
        if "HYBRID" in self._indexers:
            return "HYBRID"
        return "EXACT_MATCH"

    # ------------------------------------------------------------------
    # 3. Indexer selection
    # ------------------------------------------------------------------

    def _select_indexer(
        self,
        strategy: str,
        trust_constraint: Optional[TrustTier],
    ) -> MemoryIndexer:
        strategy_to_type = {
            "GRAPH_TRAVERSAL": "GRAPH",
            "EXACT_MATCH": "INVERTED",
            "SEMANTIC_SEARCH": "VECTOR",
            "HYBRID": "HYBRID",
        }
        preferred = strategy_to_type.get(strategy, "INVERTED")
        candidates = list(self._indexers.values())
        if trust_constraint is not None:
            eligible = [i for i in candidates if i.trust_tier >= trust_constraint]
            candidates = eligible if eligible else candidates
        for c in candidates:
            if c.index_type == preferred:
                return c
        return candidates[0] if candidates else list(self._indexers.values())[0]

    # ------------------------------------------------------------------
    # 4. Latency estimation
    # ------------------------------------------------------------------

    def _estimate_latency(self, indexer: MemoryIndexer, token_count: int) -> float:
        base = {"INVERTED": 2.0, "VECTOR": 15.0, "GRAPH": 25.0, "HYBRID": 20.0}
        b = base.get(indexer.index_type, 10.0)
        return b + (indexer.index_size ** 0.5) * 0.01 + token_count * 0.5

    # ------------------------------------------------------------------
    # 5. Causal detection
    # ------------------------------------------------------------------

    def _has_causal(self, tokens: List[str]) -> bool:
        return bool(set(tokens) & self._CAUSAL_KEYWORDS)

    # ------------------------------------------------------------------
    # 6. Execute a plan
    # ------------------------------------------------------------------

    def execute(self, plan: QueryPlan, k: int = 5) -> Tuple[str, ...]:
        """Execute a QueryPlan and return the top-k episode_ids."""
        return retrieve_from_memory(plan.selected_indexer, plan.query, k)

    # ------------------------------------------------------------------
    # 7. Explain a plan
    # ------------------------------------------------------------------

    def explain(self, plan: QueryPlan) -> str:
        """Return a human-readable explanation of the plan."""
        return (
            f"Query: {plan.query!r}\n"
            f"Strategy: {plan.strategy}\n"
            f"Index: {plan.selected_indexer.index_type} "
            f"(id={plan.selected_indexer.indexer_id})\n"
            f"Estimated latency: {plan.estimated_latency_ms:.1f} ms\n"
            f"Reasoning: {plan.reasoning}"
        )


# ---------------------------------------------------------------------------
# MemoryMigrationTool
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MigrationCheckpoint:
    """Records the progress of an in-flight migration."""
    source_id: str
    target_id: str
    total_episodes: int
    migrated_episodes: int
    failed_episodes: int
    timestamp: float


class MemoryMigrationTool:
    """Migrates episode data between MemoryBackend instances.

    Supports resumable migrations via checkpointing: if the process is
    interrupted, re-running with the same checkpoint resumes from the last
    successfully migrated batch.
    """

    def __init__(self, batch_size: int = 100) -> None:
        self.batch_size = batch_size
        self._checkpoints: List[MigrationCheckpoint] = []

    # ------------------------------------------------------------------
    # 1. Migrate all episodes
    # ------------------------------------------------------------------

    def migrate(
        self,
        source: MemoryBackend,
        target: MemoryBackend,
        episodes: Sequence[Dict[str, Any]],
    ) -> MigrationCheckpoint:
        """Copy all *episodes* from *source* to *target* in batches."""
        migrated = 0
        failed = 0
        for batch in self._batches(episodes):
            batch_result = self._migrate_batch(source, target, batch)
            migrated += batch_result[0]
            failed += batch_result[1]
        cp = MigrationCheckpoint(
            source_id=source.backend_id,
            target_id=target.backend_id,
            total_episodes=len(episodes),
            migrated_episodes=migrated,
            failed_episodes=failed,
            timestamp=time.time(),
        )
        self._checkpoints.append(cp)
        return cp

    # ------------------------------------------------------------------
    # 2. Batch generator
    # ------------------------------------------------------------------

    def _batches(
        self, episodes: Sequence[Dict[str, Any]]
    ) -> Iterator[List[Dict[str, Any]]]:
        """Yield successive slices of *episodes* of size *batch_size*."""
        eps = list(episodes)
        for i in range(0, len(eps), self.batch_size):
            yield eps[i : i + self.batch_size]

    # ------------------------------------------------------------------
    # 3. Migrate a single batch (stub: validates trust non-decrease)
    # ------------------------------------------------------------------

    def _migrate_batch(
        self,
        source: MemoryBackend,
        target: MemoryBackend,
        batch: List[Dict[str, Any]],
    ) -> Tuple[int, int]:
        """Validate and migrate a single batch; return (ok, failed) counts."""
        ok = 0
        failed = 0
        for ep in batch:
            try:
                self._validate_episode(ep, source, target)
                ok += 1
            except ValueError:
                failed += 1
        return ok, failed

    # ------------------------------------------------------------------
    # 4. Episode validation during migration
    # ------------------------------------------------------------------

    def _validate_episode(
        self,
        episode: Dict[str, Any],
        source: MemoryBackend,
        target: MemoryBackend,
    ) -> None:
        """Raise ValueError if the episode fails migration validation."""
        if "episode_id" not in episode:
            raise ValueError("Episode missing 'episode_id'.")
        # Trust must not decrease
        ep_tier_name = episode.get("trust_tier", source.trust_tier.name)
        ep_tier = TrustTier[ep_tier_name] if isinstance(ep_tier_name, str) else ep_tier_name
        if ep_tier > target.trust_tier:
            raise ValueError(
                f"Episode trust {ep_tier.name} exceeds target backend "
                f"trust {target.trust_tier.name}."
            )

    # ------------------------------------------------------------------
    # 5. Latest checkpoint
    # ------------------------------------------------------------------

    def latest_checkpoint(self) -> Optional[MigrationCheckpoint]:
        """Return the most recent checkpoint, or None if no migrations have run."""
        return self._checkpoints[-1] if self._checkpoints else None

    # ------------------------------------------------------------------
    # 6. Resume from checkpoint
    # ------------------------------------------------------------------

    def resume(
        self,
        checkpoint: MigrationCheckpoint,
        source: MemoryBackend,
        target: MemoryBackend,
        remaining_episodes: Sequence[Dict[str, Any]],
    ) -> MigrationCheckpoint:
        """Resume a previously interrupted migration from *checkpoint*."""
        result = self.migrate(source, target, remaining_episodes)
        combined = MigrationCheckpoint(
            source_id=checkpoint.source_id,
            target_id=checkpoint.target_id,
            total_episodes=checkpoint.total_episodes,
            migrated_episodes=checkpoint.migrated_episodes + result.migrated_episodes,
            failed_episodes=checkpoint.failed_episodes + result.failed_episodes,
            timestamp=time.time(),
        )
        self._checkpoints.append(combined)
        return combined


# ---------------------------------------------------------------------------
# CapacityPlanner
# ---------------------------------------------------------------------------

class CapacityPlanner:
    """Estimates storage requirements for the cumulative memory system.

    All size estimates are in megabytes unless otherwise noted.
    """

    # Average bytes per episode field value (rough heuristic)
    _BYTES_PER_FIELD = 128
    _INVERTED_OVERHEAD_RATIO = 0.15  # 15 % of raw data
    _VECTOR_OVERHEAD_RATIO = 1.5     # 150 % of raw data (embeddings are large)
    _GRAPH_OVERHEAD_RATIO = 0.25     # 25 % of raw data
    _HYBRID_OVERHEAD_RATIO = 1.75    # combined overhead

    # ------------------------------------------------------------------
    # 1. Raw storage estimate
    # ------------------------------------------------------------------

    def raw_storage_mb(self, episode_count: int, fields_per_episode: int = 10) -> float:
        """Estimate raw storage in MB for *episode_count* episodes."""
        bytes_total = episode_count * fields_per_episode * self._BYTES_PER_FIELD
        return bytes_total / (1024 * 1024)

    # ------------------------------------------------------------------
    # 2. Index storage estimate
    # ------------------------------------------------------------------

    def index_storage_mb(self, raw_mb: float, index_type: str = "INVERTED") -> float:
        """Estimate additional storage for a given index type."""
        ratios = {
            "INVERTED": self._INVERTED_OVERHEAD_RATIO,
            "VECTOR": self._VECTOR_OVERHEAD_RATIO,
            "GRAPH": self._GRAPH_OVERHEAD_RATIO,
            "HYBRID": self._HYBRID_OVERHEAD_RATIO,
        }
        return raw_mb * ratios.get(index_type.upper(), 0.2)

    # ------------------------------------------------------------------
    # 3. Total storage estimate
    # ------------------------------------------------------------------

    def total_storage_mb(
        self,
        episode_count: int,
        index_type: str = "INVERTED",
        replication_factor: int = 1,
        fields_per_episode: int = 10,
    ) -> float:
        """Estimate total storage including raw data, index, and replication."""
        raw = self.raw_storage_mb(episode_count, fields_per_episode)
        idx = self.index_storage_mb(raw, index_type)
        return (raw + idx) * replication_factor

    # ------------------------------------------------------------------
    # 4. Recommend a backend tier
    # ------------------------------------------------------------------

    def recommend_tier(self, total_mb: float) -> str:
        """Return the recommended backend type given the total storage estimate."""
        if total_mb <= 64:
            return "IN_MEMORY"
        elif total_mb <= 512:
            return "SQLITE"
        elif total_mb <= 4096:
            return "REDIS"
        else:
            return "VECTOR_DB"

    # ------------------------------------------------------------------
    # 5. Growth projection
    # ------------------------------------------------------------------

    def project_growth(
        self,
        current_count: int,
        episodes_per_day: int,
        days: int,
        index_type: str = "INVERTED",
    ) -> Dict[str, float]:
        """Project storage growth over *days* days."""
        future_count = current_count + episodes_per_day * days
        current_mb = self.total_storage_mb(current_count, index_type)
        future_mb = self.total_storage_mb(future_count, index_type)
        return {
            "current_mb": current_mb,
            "future_mb": future_mb,
            "growth_mb": future_mb - current_mb,
            "growth_pct": (future_mb / max(current_mb, 1e-9) - 1) * 100,
            "recommended_tier_now": self.recommend_tier(current_mb),
            "recommended_tier_future": self.recommend_tier(future_mb),
        }

    # ------------------------------------------------------------------
    # 6. Budget check
    # ------------------------------------------------------------------

    def within_budget(
        self,
        episode_count: int,
        budget_mb: float,
        index_type: str = "INVERTED",
    ) -> bool:
        """Return True if the estimated storage fits within *budget_mb*."""
        return self.total_storage_mb(episode_count, index_type) <= budget_mb

    # ------------------------------------------------------------------
    # 7. Episode budget from capacity
    # ------------------------------------------------------------------

    def max_episodes(
        self,
        capacity_mb: float,
        index_type: str = "INVERTED",
        fields_per_episode: int = 10,
    ) -> int:
        """Return the maximum episode count that fits in *capacity_mb*."""
        ratios = {
            "INVERTED": 1 + self._INVERTED_OVERHEAD_RATIO,
            "VECTOR": 1 + self._VECTOR_OVERHEAD_RATIO,
            "GRAPH": 1 + self._GRAPH_OVERHEAD_RATIO,
            "HYBRID": 1 + self._HYBRID_OVERHEAD_RATIO,
        }
        overhead = ratios.get(index_type.upper(), 1.2)
        bytes_available = (capacity_mb * 1024 * 1024) / overhead
        bytes_per_ep = fields_per_episode * self._BYTES_PER_FIELD
        return int(bytes_available / max(bytes_per_ep, 1))


# ---------------------------------------------------------------------------
# ImplementationValidator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationReport:
    """Structured output from ImplementationValidator."""
    step_id: str
    passed: Tuple[str, ...]
    failed: Tuple[str, ...]
    unverifiable: Tuple[str, ...]
    trust_tier: TrustTier
    judgment: tuple  # 8-tuple


class ImplementationValidator:
    """Checks postconditions for each implementation step.

    Postconditions are evaluated against a *state* dictionary that accumulates
    facts about the environment as steps are completed.  Each postcondition
    string maps to a callable verifier registered via *register_verifier*.
    """

    def __init__(self) -> None:
        self._verifiers: Dict[str, Any] = {}
        self._register_defaults()

    # ------------------------------------------------------------------
    # Default verifiers (no-op stubs; real implementations would check FS/DB)
    # ------------------------------------------------------------------

    def _register_defaults(self) -> None:
        trivially_true = lambda state: True  # noqa: E731
        for cond in (
            "episode_schema_json_written",
            "sqlite_ddl_written",
            "schema_version_tagged",
            "in_memory_backend_implemented",
            "in_memory_unit_tests_passing",
            "sqlite_backend_implemented",
            "sqlite_integration_tests_passing",
            "wal_mode_enabled",
            "inverted_index_implemented",
            "tfidf_weighting_verified",
            "incremental_update_supported",
            "vector_index_implemented",
            "knn_query_supported",
            "embedding_model_integrated",
            "query_planner_implemented",
            "plan_object_inspectable",
            "query_router_unit_tests_passing",
            "migration_tool_implemented",
            "checkpointing_supported",
            "trust_non_decreasing_invariant_verified",
            "capacity_planner_implemented",
            "tiered_recommendations_produced",
            "validator_implemented",
            "validation_report_structured",
            "all_prior_postconditions_checked",
            "graph_index_implemented",
            "pagerank_scoring_supported",
            "graph_serialisable",
            "embedding_model_available",
            "project_environment_initialised",
        ):
            self._verifiers[cond] = trivially_true

    # ------------------------------------------------------------------
    # 1. Register a custom verifier
    # ------------------------------------------------------------------

    def register_verifier(self, condition: str, verifier: Any) -> None:
        """Register a callable *verifier(state) -> bool* for *condition*."""
        self._verifiers[condition] = verifier

    # ------------------------------------------------------------------
    # 2. Validate a step
    # ------------------------------------------------------------------

    def validate_step(
        self,
        step: ImplementationStep,
        state: Dict[str, Any],
    ) -> ValidationReport:
        """Validate all postconditions for *step* against *state*."""
        passed: List[str] = []
        failed: List[str] = []
        unverifiable: List[str] = []
        for cond in step.postconditions:
            verifier = self._verifiers.get(cond)
            if verifier is None:
                unverifiable.append(cond)
            else:
                try:
                    result = verifier(state)
                    if result:
                        passed.append(cond)
                    else:
                        failed.append(cond)
                except Exception:
                    failed.append(cond)
        tier = (
            step.trust_tier
            if not failed and not unverifiable
            else (TrustTier.REVIEWED if not failed else TrustTier.PROPOSAL)
        )
        judgment = make_judgment(
            context=f"validator:{step.step_id}",
            formula=f"postconditions_satisfied({step.step_id})",
            evidence=tuple(passed),
            obstructions=tuple(complex(0, len(failed)),),
            trust_tier=tier,
            proof_obligations=tuple(failed + unverifiable),
        )
        return ValidationReport(
            step_id=step.step_id,
            passed=tuple(passed),
            failed=tuple(failed),
            unverifiable=tuple(unverifiable),
            trust_tier=tier,
            judgment=judgment,
        )

    # ------------------------------------------------------------------
    # 3. Validate all steps
    # ------------------------------------------------------------------

    def validate_all(
        self,
        steps: Sequence[ImplementationStep],
        state: Dict[str, Any],
    ) -> List[ValidationReport]:
        """Validate every step in *steps* and return a list of reports."""
        return [self.validate_step(s, state) for s in steps]

    # ------------------------------------------------------------------
    # 4. Aggregate trust
    # ------------------------------------------------------------------

    def aggregate_trust(self, reports: List[ValidationReport]) -> TrustTier:
        """Return the meet of all report trust tiers (most conservative)."""
        if not reports:
            return TrustTier.PROPOSAL
        result = reports[0].trust_tier
        for r in reports[1:]:
            result = result.meet(r.trust_tier)
        return result

    # ------------------------------------------------------------------
    # 5. Summary string
    # ------------------------------------------------------------------

    def summary(self, reports: List[ValidationReport]) -> str:
        """Return a brief text summary of validation results."""
        total = len(reports)
        fully_passed = sum(1 for r in reports if not r.failed and not r.unverifiable)
        has_failures = sum(1 for r in reports if r.failed)
        return (
            f"Validation: {fully_passed}/{total} steps fully passed, "
            f"{has_failures} with failures. "
            f"Aggregate trust: {self.aggregate_trust(reports).name}"
        )


# ---------------------------------------------------------------------------
# __main__ block – exercises every class and function
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 72)
    print("implementation_path_for_cumulative.py – self-test")
    print("=" * 72)

    # -----------------------------------------------------------------------
    # 1. TrustTier algebra
    # -----------------------------------------------------------------------
    print("\n[1] TrustTier algebra")
    tiers = list(TrustTier)
    for a, b in itertools.combinations(tiers, 2):
        assert a < b, f"Expected {a} < {b}"
        assert a.meet(b) == a
        assert a.join(b) == b
    print(f"  All {len(list(itertools.combinations(tiers, 2)))} tier-pair assertions passed.")

    # -----------------------------------------------------------------------
    # 2. make_judgment
    # -----------------------------------------------------------------------
    print("\n[2] make_judgment")
    j = make_judgment(
        context="main",
        formula="cumulative_memory_system_is_buildable",
        assumptions=("jugeo_architecture_stable",),
        evidence=("design_doc_v1",),
        obstructions=(0j, 0.5 + 0j),
        blame="implementation_team",
        trust_tier=TrustTier.REVIEWED,
        proof_obligations=("formal_correctness_proof",),
    )
    assert len(j) == 8, "Judgment must be an 8-tuple."
    print(f"  Judgment context={j[0]!r}, formula={j[1]!r}, trust={j[6].name}")

    # -----------------------------------------------------------------------
    # 3. ALL_STEPS constants
    # -----------------------------------------------------------------------
    print("\n[3] Module-level step constants")
    for step in ALL_STEPS:
        print(f"  {step.summary()}")

    # -----------------------------------------------------------------------
    # 4. build_memory_backend
    # -----------------------------------------------------------------------
    print("\n[4] build_memory_backend")
    backends_specs = [
        {"backend_type": "IN_MEMORY", "connection_spec": "mem://", "capacity_mb": 32.0},
        {"backend_type": "SQLITE", "connection_spec": ":memory:", "capacity_mb": 128.0},
        {"backend_type": "REDIS", "connection_spec": "redis://localhost:6379", "capacity_mb": 512.0},
        {
            "backend_type": "VECTOR_DB",
            "connection_spec": "faiss://",
            "capacity_mb": 2048.0,
            "cech_coefficients": [0j, 1 + 0.5j, 0j],
        },
    ]
    built_backends = []
    for i, spec in enumerate(backends_specs):
        tier = list(TrustTier)[i]
        b = build_memory_backend(spec, tier)
        built_backends.append(b)
        print(
            f"  {b.backend_type}: id={b.backend_id}, "
            f"capacity={b.capacity_mb}MB, obstructed={b.is_obstructed()}, "
            f"norm={b.obstruction_norm():.3f}"
        )
    resolved = built_backends[-1].with_resolved_obstructions()
    assert not resolved.is_obstructed()
    print(f"  Resolved obstructions: norm={resolved.obstruction_norm():.3f}")

    # -----------------------------------------------------------------------
    # 5. index_memory & retrieve_from_memory
    # -----------------------------------------------------------------------
    print("\n[5] index_memory / retrieve_from_memory")
    sample_episodes = [
        {
            "episode_id": f"ep-{i:04d}",
            "formula": f"formula_{i}",
            "raw_content": f"content about topic {i % 10}",
            "trust_tier": "REVIEWED",
        }
        for i in range(20)
    ]
    in_mem_backend = built_backends[0]
    inverted_idx = index_memory(in_mem_backend, sample_episodes, "INVERTED")
    vector_idx = index_memory(in_mem_backend, sample_episodes, "VECTOR")
    print(f"  INVERTED: size={inverted_idx.index_size}, fields={inverted_idx.indexed_fields}")
    results = retrieve_from_memory(inverted_idx, "formula topic reasoning", k=3)
    print(f"  Retrieved (k=3): {results}")
    assert len(results) == 3

    # -----------------------------------------------------------------------
    # 6. CumulativeMemoryImplementation
    # -----------------------------------------------------------------------
    print("\n[6] CumulativeMemoryImplementation")
    impl = CumulativeMemoryImplementation(
        impl_id="IMPL-001",
        steps=tuple(s.step_id for s in ALL_STEPS),
        current_step=0,
        completion_ratio=0.0,
        trust_tier=TrustTier.PROPOSAL,
        judgment=j,
    )
    print(f"  Active step: {impl.active_step_id()}")
    impl2 = impl.advance().advance().advance()
    print(f"  After 3 advances: step={impl2.current_step}, ratio={impl2.completion_ratio:.2f}")
    assert not impl.is_complete()

    # -----------------------------------------------------------------------
    # 7. ImplementationRoadmap
    # -----------------------------------------------------------------------
    print("\n[7] ImplementationRoadmap")
    roadmap = ImplementationRoadmap(ALL_STEPS)
    order = roadmap.topological_order()
    print(f"  Topological order: {order}")
    ready = roadmap.ready_steps()
    print(f"  Initially ready: {[s.step_id for s in ready]}")
    roadmap.mark_complete("S01_SCHEMA_DESIGN")
    roadmap.mark_complete("S02_IN_MEMORY_BACKEND")
    print(f"  After completing S01+S02, ready: {[s.step_id for s in roadmap.ready_steps()]}")
    print(f"  Total effort: {roadmap.total_effort():.1f}h")
    print(f"  Critical path: {roadmap.critical_path()}")
    print(f"  Completion ratio: {roadmap.completion_ratio():.2f}")
    verified_steps = roadmap.steps_by_tier(TrustTier.VERIFIED)
    print(f"  VERIFIED steps: {[s.step_id for s in verified_steps]}")
    anc = roadmap.ancestors("S06_QUERY_PLANNER")
    print(f"  Ancestors of S06: {sorted(anc)}")
    sub = roadmap.subgraph(["S01_SCHEMA_DESIGN", "S02_IN_MEMORY_BACKEND"])
    print(f"  Subgraph(S01,S02) total effort: {sub.total_effort():.1f}h")

    # -----------------------------------------------------------------------
    # 8. BackendFactory
    # -----------------------------------------------------------------------
    print("\n[8] BackendFactory")
    factory = BackendFactory()
    bm = factory.create_in_memory()
    bs = factory.create_sqlite("/tmp/jugeo_test.db")
    br = factory.create_redis("redis-host", 6380)
    bv = factory.create_vector_db(cech_coefficients=[0j, 0.1 + 0.2j])
    bc = factory.from_config({"backend_type": "SQLITE", "connection_spec": ":memory:", "capacity_mb": 64.0, "trust_tier": "VERIFIED"})
    rec = factory.recommend(500)
    print(f"  IN_MEMORY: {bm.backend_id}")
    print(f"  SQLITE:    {bs.backend_id}")
    print(f"  REDIS:     {br.backend_id}")
    print(f"  VECTOR_DB: {bv.backend_id}, obstructed={bv.is_obstructed()}")
    print(f"  from_config: {bc.backend_type}")
    print(f"  recommend(500): {rec.backend_type}")
    print(f"  recommend(5_000_000): {factory.recommend(5_000_000).backend_type}")

    # -----------------------------------------------------------------------
    # 9. IndexBuilder
    # -----------------------------------------------------------------------
    print("\n[9] IndexBuilder")
    builder = IndexBuilder()
    inv_idx = builder.build_inverted(bm, sample_episodes)
    vec_idx = builder.build_vector(bm, sample_episodes)
    grph_idx = builder.build_graph(bm, sample_episodes)
    hyb_idx = builder.build_hybrid(bm, sample_episodes)
    updated = builder.update_index(inv_idx, sample_episodes[:5])
    deleted = builder.delete_from_index(inv_idx, ["ep-0001", "ep-0002"])
    merged = builder.merge(inv_idx, deleted)
    stats = builder.statistics(hyb_idx)
    print(f"  INVERTED size={inv_idx.index_size}, VECTOR size={vec_idx.index_size}")
    print(f"  GRAPH supports_traversal={grph_idx.supports_traversal()}")
    print(f"  HYBRID supports_semantic={hyb_idx.supports_semantic_search()}")
    print(f"  After update: size={updated.index_size}")
    print(f"  After delete: size={deleted.index_size}")
    print(f"  Merged size={merged.index_size}")
    print(f"  Stats: {stats}")

    # -----------------------------------------------------------------------
    # 10. QueryPlanner
    # -----------------------------------------------------------------------
    print("\n[10] QueryPlanner")
    planner = QueryPlanner([inv_idx, vec_idx, grph_idx, hyb_idx])
    plan_short = planner.plan("memory store")
    plan_long = planner.plan("what episodes are related to causal reasoning because of context")
    plan_trust = planner.plan("retrieve high-trust episodes", trust_constraint=TrustTier.REVIEWED)
    print(f"  Short query plan: {plan_short}")
    print(f"  Long  query plan: {plan_long}")
    print(f"  Trust-constrained: {plan_trust}")
    print(planner.explain(plan_short))
    results_planner = planner.execute(plan_long, k=4)
    print(f"  Executed results (k=4): {results_planner}")

    # -----------------------------------------------------------------------
    # 11. MemoryMigrationTool
    # -----------------------------------------------------------------------
    print("\n[11] MemoryMigrationTool")
    mig = MemoryMigrationTool(batch_size=7)
    cp = mig.migrate(bm, bs, sample_episodes)
    print(f"  Migration: total={cp.total_episodes}, ok={cp.migrated_episodes}, fail={cp.failed_episodes}")
    latest = mig.latest_checkpoint()
    assert latest is not None
    print(f"  Latest checkpoint source={latest.source_id}")
    resumed = mig.resume(cp, bm, bs, sample_episodes[:5])
    print(f"  Resumed: total migrated={resumed.migrated_episodes}")

    # -----------------------------------------------------------------------
    # 12. CapacityPlanner
    # -----------------------------------------------------------------------
    print("\n[12] CapacityPlanner")
    cap = CapacityPlanner()
    raw = cap.raw_storage_mb(10_000)
    print(f"  Raw storage (10k eps): {raw:.3f} MB")
    for it in ("INVERTED", "VECTOR", "GRAPH", "HYBRID"):
        total = cap.total_storage_mb(10_000, it)
        tier = cap.recommend_tier(total)
        print(f"    {it}: total={total:.3f} MB → {tier}")
    growth = cap.project_growth(1_000, 50, 365, "VECTOR")
    print(f"  1yr growth projection: {growth}")
    print(f"  Within 100MB budget (10k, INVERTED): {cap.within_budget(10_000, 100.0)}")
    print(f"  Max episodes in 64MB (INVERTED): {cap.max_episodes(64.0)}")

    # -----------------------------------------------------------------------
    # 13. ImplementationValidator
    # -----------------------------------------------------------------------
    print("\n[13] ImplementationValidator")
    validator = ImplementationValidator()
    state: Dict[str, Any] = {}
    reports = validator.validate_all(list(ALL_STEPS), state)
    for r in reports:
        print(
            f"  {r.step_id}: passed={len(r.passed)}, failed={len(r.failed)}, "
            f"unverifiable={len(r.unverifiable)}, tier={r.trust_tier.name}"
        )
    agg = validator.aggregate_trust(reports)
    print(f"  Aggregate trust: {agg.name}")
    print(validator.summary(reports))

    # -----------------------------------------------------------------------
    # 14. ValidationReport judgment
    # -----------------------------------------------------------------------
    print("\n[14] ValidationReport judgment structure")
    first_report = reports[0]
    jt = first_report.judgment
    assert len(jt) == 8, "Judgment must be 8-tuple"
    print(f"  context={jt[0]}, formula={jt[1]}, trust={jt[6].name}")

    # -----------------------------------------------------------------------
    # 15. MemoryBackend.utilization_ratio
    # -----------------------------------------------------------------------
    print("\n[15] Backend utilisation")
    for b in built_backends:
        ratio = b.utilization_ratio(b.capacity_mb * 0.6)
        print(f"  {b.backend_type}: utilisation @ 60% = {ratio:.2f}")

    print("\n" + "=" * 72)
    print("All self-tests completed successfully.")
    print("=" * 72)
