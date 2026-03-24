# Possible Next Steps 2: Scaling JuGeo to Million-LOC Codebases

> **Context**: JuGeo currently operates comfortably on programs of a few
> hundred to a few thousand lines.  Experiments use synthetic temp-file
> programs; the largest real target is JuGeo's own ~100-module codebase.
> This document has three parts:
>
> **Part A** analyzes the infrastructure scaling pain points — what breaks
> when moving from KLoC to 1M LOC, and how to fix it.
>
> **Part B** develops the Judgment-Geometry-theoretic approach to the
> *software engineering* problems of organizing, architecting, testing,
> debugging, and improving million-LOC software — not just making JuGeo
> run faster, but making it a *theory of software engineering* at scale.
>
> **Part C** shows how JG strictly generalizes the "augmented Mealy machine"
> formalism of Comet-H (a prompt automaton for LM-orchestrated research
> software co-evolution) while being more theoretically elegant, more
> expressive, and completely faithful to algebraic geometry.

---

# PART A — Infrastructure Scaling

## Table of Contents

1. [Current Architecture Baseline](#1-current-architecture-baseline)
2. [Pain Point 1: In-Memory Everything](#2-in-memory-everything)
3. [Pain Point 2: Whole-Program AST Loading](#3-whole-program-ast-loading)
4. [Pain Point 3: O(n²) Descent / Overlap Computation](#4-on2-descent--overlap-computation)
5. [Pain Point 4: Z3 Session Bottleneck](#5-z3-session-bottleneck)
6. [Pain Point 5: Single-Process Execution](#6-single-process-execution)
7. [Pain Point 6: Checkpoint / Serialization Cost](#7-checkpoint--serialization-cost)
8. [Pain Point 7: Import Graph Explosion](#8-import-graph-explosion)
9. [Pain Point 8: Evidence Store Scan Paths](#9-evidence-store-scan-paths)
10. [Pain Point 9: Orchestration State Explosion](#10-orchestration-state-explosion)
11. [Pain Point 10: Ideation Search Space Explosion](#11-ideation-search-space-explosion)
12. [Pain Point 11: Trust Propagation at Scale](#12-trust-propagation-at-scale)
13. [Pain Point 12: Cache Invalidation Cascades](#13-cache-invalidation-cascades)
14. [Pain Point 13: Pack Federation Combinatorics](#14-pack-federation-combinatorics)
15. [Pain Point 14: Server Architecture](#15-server-architecture)
16. [Pain Point 15: Human-in-the-Loop Scaling](#16-human-in-the-loop-scaling)
17. [Cross-Cutting Solution: Hierarchical Site Decomposition](#17-hierarchical-site-decomposition)
18. [Cross-Cutting Solution: Persistent Indexed Backend](#18-persistent-indexed-backend)
19. [Cross-Cutting Solution: Distributed Worker Architecture](#19-distributed-worker-architecture)
20. [Cross-Cutting Solution: Incremental Everything](#20-incremental-everything)
21. [Implementation Roadmap](#21-implementation-roadmap)

---

## 1. Current Architecture Baseline

### What exists today

| Layer | Storage | Parallelism | Persistence | Indexing |
|---|---|---|---|---|
| **Geometry (site.py)** | In-memory dicts + trie | None | JSON checkpoint | CoordinateIndex with trie |
| **Judgments** | In-memory per-manifest | None | JSON checkpoint | By-coordinate dict |
| **Evidence (manifests.py)** | In-memory stores | None | JSON checkpoint | By-coordinate, by-channel |
| **Solver (z3_session.py)** | Pooled Z3 sessions | Thread pool (descent only) | None | Fragment classifier |
| **Runtime (cache/memory)** | In-memory with TTL | None | JSON file checkpoints | Support/dependency graph |
| **Import graph** | In-memory AST walk | None | JSON serialization | Visited-set dedup |
| **Orchestration** | In-memory state | None | Event log | Frontier scorer |
| **Packs** | In-memory registry | None | pack.json discovery | Topo-sorted deps |

### Key metrics at current scale

- A ~500-line Python file produces ~50–200 coordinates, ~100–500 morphisms, ~20–50 covers
- A ~10-file project produces ~2,000 coordinates, ~5,000 morphisms, ~200 covers
- Everything fits comfortably in memory; serialization is sub-second

### Extrapolation to 1M LOC

- **Coordinates**: 1M LOC ≈ ~50,000 files × ~200 coords/file ≈ **10 million coordinates**
- **Morphisms**: ≈ **50 million morphisms** (5× coordinates is typical)
- **Covers**: ≈ **2 million covering families**
- **Judgments**: ≈ **5 million judgment 8-tuples**
- **Evidence records**: ≈ **20 million evidence entries** (multiple channels per judgment)
- **Obligations**: ≈ **2 million active obligations** at any given time

At these numbers, every in-memory dict becomes a multi-GB structure,
every O(n) scan takes minutes, and every O(n²) operation is infeasible.

---

## 2. In-Memory Everything

### The problem

JuGeo stores all semantic state (coordinates, morphisms, judgments,
evidence, obligations, obstructions, certificates, treaties) in Python
dicts and lists in a single process's heap.

- **10M coordinates × ~200 bytes each ≈ 2 GB** just for coordinate objects
- **50M morphisms × ~150 bytes each ≈ 7.5 GB** for morphisms alone
- **20M evidence records × ~300 bytes each ≈ 6 GB** for evidence
- Total working set: **~20–30 GB** for a 1M LOC codebase

This exceeds typical developer workstation RAM and makes the process
vulnerable to OOM kills, GC pauses, and swap thrashing.

### What breaks

- `Site._coordinates` dict hits Python dict resize thrashing at ~10M entries
- `Manifest` composite store exceeds available memory
- Python GC pauses on large object graphs become multi-second
- No way to page out cold data or work on a subset

### Solution: Tiered Storage with Geometric Locality

The fix must be **integrated into the sheaf paradigm**, not bolted on:

1. **Hot tier (in-memory)**: Coordinates, morphisms, and judgments for
   the *current cover* being verified. Typically ~1,000–10,000 objects.
   This is the "local section" that JuGeo is actively working on.

2. **Warm tier (memory-mapped / SQLite)**: Coordinates and judgments for
   *adjacent covers* — things that might be needed for descent checks.
   Loaded on demand when the orchestrator expands the frontier.

3. **Cold tier (disk / database)**: Everything else. The full site is
   stored in a persistent indexed backend. Only loaded when explicitly
   requested by a cover refinement or global query.

The key insight: **sheaf locality is a natural eviction policy**. The site
topology tells you exactly what's "nearby" (overlapping covers) and what's
"far away" (unrelated modules). This is better than LRU because it's
semantically informed.

```
Eviction priority = distance_in_site(coordinate, current_cover)
```

Coordinates far from the current verification frontier are evicted first.
When descent needs them, they're paged back in via the persistent backend.

---

## 3. Whole-Program AST Loading

### The problem

`python_runtime/program_loader.py` loads and parses Python source files
via AST. The import graph builder in `import_graph/` walks the entire
directory tree, parsing every `.py` file to build the full graph.

At 1M LOC / ~50,000 files:
- **Parsing**: ~50,000 AST parses ≈ **30–60 seconds** (Python's `ast.parse`
  is ~1ms per small file, but large files can be 10–50ms)
- **Memory**: 50,000 ASTs in memory simultaneously ≈ **5–10 GB**
- **Import resolution**: Following import chains across 50K files creates
  a dense graph that's expensive to build and query

### What breaks

- Initial load time becomes minutes, not seconds
- Memory pressure from holding all ASTs simultaneously
- Import graph cycles and dynamic imports create pathological cases
- No incremental reloading — changing one file re-parses everything

### Solution: Incremental, Lazy, Parallel Loading

1. **File-level hashing**: Hash each `.py` file. Only re-parse files
   whose hash changed since the last run. Store parse results (coordinate
   assignments, import edges) in the persistent backend keyed by
   file hash.

2. **Lazy AST loading**: Don't parse a file until its coordinates are
   actually needed. The import graph can be built from import statements
   alone (fast regex/AST scan of just the import lines), without full
   AST parsing.

3. **Parallel parsing**: AST parsing is CPU-bound and embarrassingly
   parallel. Use `ProcessPoolExecutor` to parse files across all cores.
   At 8 cores, 50K files drops from 60s to ~8s.

4. **Streaming coordinate assignment**: As each file is parsed, emit its
   coordinates and morphisms to the persistent backend immediately. Don't
   accumulate everything in memory first.

5. **Virtual coordinates for unresolved imports**: Third-party packages
   get lightweight stub coordinates (from contracts/packs) without full
   AST parsing.

### Paradigm integration

This maps naturally to **cover-by-cover loading**: the site topology
defines which files are in the current cover. Only those files need full
AST parsing. Adjacent covers are parsed lazily when the frontier expands.

---

## 4. O(n²) Descent / Overlap Computation

### The problem

Descent requires checking that local sections **agree on overlaps**
between covering family members. For a cover with k members, there are
O(k²) pairwise overlaps to check. At large scale:

- A module-level cover might have k = 500 functions → 125,000 overlap
  checks
- A project-level cover might have k = 1,000 modules → 500,000 overlap
  checks
- Each overlap check may invoke the solver

The existing `descent.py` has `parallel_descent()` using a thread pool,
but the quadratic structure remains.

### What breaks

- Whole-project descent on 50,000 files is O(50,000²) = 2.5 billion
  overlap checks — completely infeasible
- Even with solver caching, the sheer number of queries overwhelms Z3
- Thread-pool parallelism doesn't help with quadratic algorithms

### Solution: Hierarchical Descent with Sparse Overlaps

The mathematical structure of sheaf theory already provides the answer:
**hierarchical covers and hypercovers**.

1. **File-level descent**: Check overlaps within a single file (small k,
   fast). This is local descent — always feasible.

2. **Package-level descent**: Check overlaps between files in a package.
   But most files in a package don't overlap — they interact only through
   imports. Use the import graph to identify **sparse overlap structure**:
   only check pairs of files that share imports or exports.

3. **Project-level descent**: Check overlaps between packages. Again,
   most packages don't directly interact. Use the package dependency graph
   to identify the sparse overlap set.

4. **Hypercover refinement**: `hypercovers.py` already supports multi-level
   covers. Use this to decompose the project into a hierarchy:
   ```
   Project cover → package covers → file covers → function covers
   ```
   Descent at each level only checks the overlaps at that level.
   Total work: O(n × avg_overlap_degree) instead of O(n²).

5. **Overlap indexing**: Build a spatial index over coordinates that
   supports efficient overlap queries. Given coordinate c, find all
   coordinates c' that overlap with c in O(log n) instead of O(n).

### Paradigm integration

This is exactly what Grothendieck topologies are designed for. The
hypercover machinery already exists in `geometry/hypercovers.py`. The
scaling fix is to **actually use it** for large codebases instead of
falling back to flat covers.

---

## 5. Z3 Session Bottleneck

### The problem

`solver/z3_session.py` maintains a pool of reusable Z3 sessions. This is
good for small projects, but at scale:

- **Pool exhaustion**: With millions of obligations, the bounded session
  pool becomes a bottleneck. Threads block waiting for a free session.
- **Session state accumulation**: Z3 sessions that accumulate many
  assertions become slower over time (learned clauses, internal caches).
- **Single-machine limitation**: Z3 runs in-process. No way to offload
  queries to remote solver instances.
- **Fragment routing overhead**: The `FragmentClassifier` runs on every
  query. At millions of queries, classification itself becomes significant.

### What breaks

- Millions of solver queries serialized through a small pool
- Z3 memory usage grows unboundedly with assertion accumulation
- No horizontal scaling — one machine, one Z3 pool

### Solution: Distributed Solver Federation

1. **Session lifecycle management**: Periodically reset Z3 sessions after
   N queries or M megabytes of learned clauses. Reset cost is small
   compared to degraded performance from bloated sessions.

2. **Fragment-based batching**: Group queries by SMT fragment. Dispatch
   each batch with a single Z3 configuration optimized for that fragment.
   This amortizes classification cost and improves cache locality within
   Z3.

3. **Remote solver workers**: Add a `SolverWorker` process that accepts
   queries over a local socket or IPC channel. Multiple workers can run
   on different cores or machines. The `SolverRouter` dispatches to the
   nearest available worker.

4. **Incremental solving**: For obligations that differ by small deltas
   (e.g., the same function with different input ranges), use Z3's
   incremental solving mode (push/pop) instead of independent queries.
   This reuses learned clauses across related queries.

5. **Query deduplication**: Many obligations produce identical or
   alpha-equivalent SMT queries. Hash-cons queries and cache results.
   At 1M LOC, deduplication rates of 30–50% are typical.

6. **Solver result persistence**: Store solver results in the persistent
   backend keyed by (query_hash, solver_version). Re-running verification
   after a small code change only needs to solve queries for changed code.

### Paradigm integration

The `SolverRouter` already has jurisdiction management and routing
strategies. Extending it to route across remote workers is a natural
generalization. Solver results are evidence records — they get the same
trust, provenance, and caching treatment as any other evidence channel.

---

## 6. Single-Process Execution

### The problem

JuGeo runs as a single Python process. The only parallelism is a
`ThreadPoolExecutor` for descent overlap checks. Due to Python's GIL,
even thread-pool parallelism doesn't help for CPU-bound work (AST parsing,
fragment classification, judgment comparison).

At 1M LOC:
- Single-core AST parsing: ~60 seconds
- Single-core descent: hours to days
- Single-core evidence routing: hours
- Memory pressure from one process holding everything

### What breaks

- Can't utilize multi-core machines (typical: 8–32 cores)
- Can't distribute across machines for very large codebases
- Memory can't be shared between independent verification tasks

### Solution: Multi-Process Architecture with Geometric Partitioning

1. **Coordinator process**: Manages the global site topology, frontier,
   and orchestration state. Lightweight — just control plane.

2. **Worker processes**: Each worker verifies a **partition** of the site.
   Partitions are defined by the site topology (packages, modules, or
   cover members). Each worker loads only the coordinates in its partition.

3. **Partition assignment via site topology**: The site's covering families
   naturally define independent verification tasks. If two covers don't
   overlap, they can be verified in parallel with no coordination.

4. **Descent workers**: Overlap checks between partitions are dispatched
   to dedicated descent workers that load only the relevant coordinates
   from both partitions.

5. **Message passing**: Workers communicate via a lightweight message
   protocol (Unix sockets, or ZMQ for multi-machine). Messages carry
   judgment deltas, evidence records, and treaty proposals — exactly
   the objects JuGeo already serializes.

6. **Shared persistent backend**: All workers read/write to the same
   persistent store (SQLite for single-machine, PostgreSQL for
   multi-machine). The store provides coordination via atomic operations
   on judgment/evidence records.

### Paradigm integration

This is **distributed sheaf verification**. Each worker maintains local
sections over its partition. The coordinator performs global descent
across partition boundaries. Treaty negotiation between workers happens
via the same negotiation protocol used within a single process — the
abstraction is identical.

---

## 7. Checkpoint / Serialization Cost

### The problem

`runtime/checkpointing.py` serializes the entire manifest state to JSON
files. At 1M LOC:

- Manifest JSON ≈ **5–10 GB** (20M evidence records × ~300 bytes)
- Serialization time: **minutes** for full checkpoint
- Deserialization time: **minutes** to restore from checkpoint
- Disk I/O: **gigabytes** per checkpoint

### What breaks

- Checkpointing pauses verification for minutes
- Disk space: frequent checkpoints consume tens of GB
- Recovery time: restarting from checkpoint is slow
- No incremental checkpointing — full dump every time

### Solution: Incremental, Append-Only Checkpointing

1. **WAL (Write-Ahead Log) pattern**: Instead of full-state checkpoints,
   write an append-only log of judgment/evidence deltas. Each verification
   step appends a small delta (bytes to kilobytes) instead of rewriting
   the full state (gigabytes).

2. **Periodic compaction**: Periodically merge the WAL into a compacted
   snapshot. This happens in the background without pausing verification.

3. **LSM-tree-inspired structure**: Recent deltas are in memory (fast
   reads). Older deltas are on disk in sorted runs. Compaction merges
   runs. This gives O(1) writes and O(log n) reads.

4. **Coordinate-partitioned checkpoints**: Instead of one monolithic
   checkpoint, partition the state by top-level coordinate (package).
   Changing one file only touches that package's checkpoint partition.

5. **Streaming deserialization**: On recovery, don't load the entire
   checkpoint into memory. Stream coordinates on-demand as the
   orchestrator requests them.

### Paradigm integration

The JuGeo judgment lifecycle already tracks deltas (`CompressionRecord`
in generation). Extending this to persistent storage is natural. Each
delta is a small sheaf section update — the WAL is just the descent
history serialized.

---

## 8. Import Graph Explosion

### The problem

`python_runtime/import_graph/` builds the full import graph by walking
every `.py` file. At 1M LOC:

- **Node count**: ~50,000 internal modules + ~5,000 third-party packages
- **Edge count**: ~200,000 import edges (avg 4 imports per file)
- **Cycle detection**: Real Python projects have import cycles, especially
  at package level
- **Dynamic imports**: `importlib.import_module()`, `__import__()`,
  conditional imports — common in large codebases

### What breaks

- Full graph construction takes minutes
- Cycle resolution is NP-hard in the worst case (though heuristics work)
- Dynamic imports create phantom edges that aren't visible statically
- The graph must be rebuilt entirely on any structural change

### Solution: Incremental, Layered Import Analysis

1. **Static import index**: Extract import statements from all files
   using a fast regex pass (not full AST parse). Build a preliminary
   import graph in seconds.

2. **AST-backed refinement**: For files in the current verification
   frontier, do full AST parsing to resolve complex imports
   (`from X import *`, relative imports, conditional imports).

3. **Incremental update**: On file change, only recompute the changed
   file's import edges. Use the dependency structure to propagate
   invalidation only to files that import (directly or transitively)
   the changed file.

4. **Virtual nodes for third-party packages**: Don't analyze third-party
   package internals. Use contract packs to provide pre-computed coordinate
   stubs and import edges for popular packages (numpy, pandas, flask, etc.).

5. **Cycle handling via SCCs**: Compute strongly connected components
   (Tarjan's algorithm, O(V+E)). Treat each SCC as a single "super-node"
   for site construction. This is mathematically equivalent to collapsing
   a cyclic cover to a hypercover.

### Paradigm integration

Import edges are morphisms in the semantic site. The import graph *is*
part of the site topology. Incremental import analysis is incremental
site construction — a natural operation in the sheaf framework.

---

## 9. Evidence Store Scan Paths

### The problem

`evidence/manifests.py` stores evidence in dicts indexed by ID and
coordinate. Queries like "find all evidence for functions in package X"
or "find all obligations with trust < SOLVER_DISCHARGED" require scanning
the entire store.

At 20M evidence records:
- A full scan: ~10 seconds (Python dict iteration overhead)
- Complex queries (trust + coordinate + channel filters): ~30 seconds
- These queries happen frequently during descent and orchestration

### What breaks

- Orchestration's frontier scoring queries evidence for every candidate
  move — thousands of queries per second
- Descent checks evidence at every overlap — millions of lookups
- No way to answer range/prefix/multi-attribute queries efficiently

### Solution: Multi-Index Evidence Store

1. **Secondary indices**: Add indices for the most common query patterns:
   - By trust level: `{trust_level: [evidence_ids]}`
   - By channel: `{channel: [evidence_ids]}`
   - By coordinate prefix: trie-based (like `CoordinateIndex`)
   - By timestamp: sorted for recency queries
   - By obligation ID: for discharge lookups

2. **Composite indices**: For common multi-attribute queries
   (coordinate + trust, channel + trust), maintain composite indices
   that avoid intersection of individual index results.

3. **Materialized views**: For orchestration's most frequent queries
   ("what's the trust floor for this cover?", "how many obligations
   remain?"), maintain pre-computed aggregates that update incrementally
   on each evidence insertion.

4. **Backend-delegated queries**: When using a persistent backend
   (SQLite/PostgreSQL), push query predicates to the database engine
   instead of scanning in Python.

### Paradigm integration

Evidence indices are **support-aware**: they know which coordinates
each evidence record covers. This means queries like "find all evidence
whose support intersects cover C" can use the coordinate trie for
efficient spatial lookup — a query that's natural in the sheaf framework.

---

## 10. Orchestration State Explosion

### The problem

The orchestrator maintains a rich state: covers, contexts, partial
sections, treaties, obligations, evidence, frontier nodes, budgets,
and phase information. At 1M LOC:

- **Frontier size**: ~100,000 active frontier nodes (each with predicted
  gains, costs, and diversity scores)
- **Treaty count**: ~10,000 active treaties between subsystems
- **Move history**: ~1 million recorded moves
- **Active obligations**: ~2 million

The orchestrator's control loop evaluates every frontier node at each
step. With 100K nodes and multi-criterion scoring, each step takes
seconds instead of milliseconds.

### What breaks

- Control loop latency: seconds per step instead of milliseconds
- Frontier scoring becomes the bottleneck
- Move history grows unboundedly
- Treaty negotiation touches O(treaties × obligations) pairs

### Solution: Hierarchical Orchestration with Local Controllers

1. **Local controllers**: Each package/module gets its own lightweight
   orchestrator that manages local frontier, treaties, and obligations.
   It operates independently for purely local decisions.

2. **Regional controllers**: Group packages into regions (by dependency
   clusters). Regional controllers handle cross-package descent and
   treaty negotiation within the region.

3. **Global controller**: Manages only cross-region concerns: global
   phase transitions, budget allocation across regions, and project-wide
   convergence monitoring.

4. **Frontier pruning**: Aggressively prune frontier nodes below a
   quality threshold. At any given time, only the top ~1,000 nodes
   need full scoring. The rest are archived with approximate scores.

5. **Move history compaction**: Like git's garbage collection, periodically
   compact the move history by replacing long move sequences with
   their net effect (a single "macro-move").

### Paradigm integration

This is **hierarchical sheaf orchestration** — the same pattern as
hierarchical descent. Local controllers are local sections of the
orchestration sheaf. Regional controllers perform descent on the
orchestration state itself. The global controller ensures orchestration
decisions glue consistently.

---

## 11. Ideation Search Space Explosion

### The problem

The ideation engine generates candidate ideas by exploring a space of
propositions, analogies, and cross-domain connections. At 1M LOC:

- **Theorem portfolio**: ~100,000 known theorems/lemmas
- **Candidate space**: combinatorial explosion of possible new ideas
- **Novelty computation**: comparing each candidate against 100K known
  results requires O(candidates × portfolio) distance computations
- **Synthesis frontier**: with 128 fields and pairwise cross-domain
  generation, the tournament has O(128²) = 16,384 initial candidates

### What breaks

- Novelty search becomes the dominant cost
- Theorem economics portfolio optimization scales as O(n³) (general
  portfolio optimization)
- Analogy transport scoring requires pairwise structural comparison

### Solution: Approximate Novelty with Locality-Sensitive Hashing

1. **Proposition embeddings**: Embed propositions into a fixed-dimensional
   vector space. Novelty = Euclidean distance in embedding space.
   This turns O(n) novelty scoring into O(1) approximate nearest
   neighbor lookup.

2. **LSH index over theorem portfolio**: Build a locality-sensitive
   hash index over the portfolio. Novelty queries return approximate
   neighbors in O(1), with tuneable accuracy/speed tradeoff.

3. **Incremental portfolio updates**: When a new theorem is proven,
   update the LSH index incrementally (O(1) amortized) instead of
   rebuilding (O(n)).

4. **Hierarchical synthesis frontier**: Instead of all-pairs cross-domain
   generation, use the field taxonomy to identify high-potential pairs
   first (O(fields × log fields)), then generate candidates only for
   promising pairs.

5. **Budget-bounded ideation**: The existing theorem economics already
   models budget constraints. Enforce them strictly: never generate
   more candidates than the budget allows to evaluate.

### Paradigm integration

Proposition embeddings can be defined as a **functor** from the category
of propositions to a metric space. The LSH index is a "coarse topology"
on the proposition space — novelty is measured in this coarse topology
first, then refined in the full topology only for candidates that pass
the coarse filter. This is precisely how Grothendieck topologies work:
coarse covers refine to fine covers.

---

## 12. Trust Propagation at Scale

### The problem

The trust algebra requires that trust levels compose conservatively
(weakest-link) and that promotions are never silent. At 1M LOC:

- **Trust propagation**: When evidence at one coordinate changes trust,
  all downstream judgments (connected by morphisms) must be re-evaluated.
  In a dense graph, this can cascade across the entire codebase.
- **Promotion auditing**: Every trust promotion must be logged with
  justification. At millions of promotions, the audit log becomes
  enormous.
- **Challenge propagation**: A challenge to one judgment can cascade
  to all judgments that depend on it.

### What breaks

- Trust change on a widely-imported module triggers re-evaluation of
  thousands of downstream judgments
- Audit log grows without bound
- Challenge cascades create "trust earthquakes" that destabilize
  large sections of the verification state

### Solution: Trust Zones with Firewall Boundaries

1. **Trust zones**: Partition the site into zones with explicit trust
   boundaries. Trust changes within a zone propagate locally. Trust
   changes across zone boundaries propagate only if they affect the
   zone's *exported claims* (the interface).

2. **Lazy trust propagation**: Don't propagate trust changes eagerly.
   Instead, mark downstream judgments as "trust-dirty" and re-evaluate
   them lazily when they're next accessed. Most judgments in a large
   codebase are accessed rarely.

3. **Trust change batching**: Accumulate trust changes and propagate
   them in batches. This avoids the "cascade of cascades" problem where
   each intermediate propagation triggers further propagation.

4. **Audit log compaction**: Like WAL compaction, periodically compress
   the audit log by replacing long promotion chains with their net
   effect.

5. **Challenge containment**: When a challenge is issued, compute its
   **blast radius** (the set of judgments that would be affected) before
   propagating. If the blast radius exceeds a threshold, require human
   approval before cascading.

### Paradigm integration

Trust zones are **sub-sites** in the Grothendieck topology. The zone
boundary is a covering sieve that filters which trust changes propagate.
This is a natural construction in sheaf theory: restricting a sheaf to
an open sub-site.

---

## 13. Cache Invalidation Cascades

### The problem

`runtime/invalidation.py` maintains a dependency graph for cache
invalidation. When a cached result is invalidated, all dependent results
are also invalidated. At 1M LOC:

- **Invalidation graph**: ~10M nodes (cached results) × ~50M edges
  (dependencies)
- **Cascade depth**: In deep dependency chains (common in large codebases),
  one invalidation can cascade through hundreds of levels
- **Invalidation storms**: Changing a widely-used utility function
  invalidates everything that transitively depends on it — potentially
  millions of cached results

### What breaks

- Invalidation of a core utility function takes minutes to cascade
- The entire verification cache becomes effectively useless after
  a change to a popular module
- Re-verification after invalidation is as expensive as fresh verification

### Solution: Invalidation Dampening with Semantic Stability Analysis

1. **Change impact analysis**: Before invalidating, analyze *what changed*
   about the modified function. If only a comment changed, no invalidation
   needed. If only internal implementation changed but the contract is
   unchanged, only invalidate direct callers, not transitive dependents.

2. **Contract-based invalidation boundaries**: If a function has a
   verified contract (spec + evidence), changes to its implementation
   that preserve the contract don't invalidate downstream caches.
   This is the "verified interface" pattern — contracts act as
   invalidation firewalls.

3. **Probabilistic invalidation**: For very deep cascades, use a
   probabilistic estimate of whether the change actually affects
   downstream results. Re-verify a random sample; if the sample is
   unaffected, defer full invalidation.

4. **Tiered invalidation**: Invalidate in waves:
   - Wave 1: Direct dependents (immediate)
   - Wave 2: 2-hop dependents (deferred, on next access)
   - Wave 3: 3+ hop dependents (lazy, only if actually queried)

5. **Invalidation budget**: Limit the number of invalidations per change
   event. If the cascade would exceed the budget, mark the remaining
   entries as "stale" (lower trust) instead of fully invalidated.

### Paradigm integration

Contracts-as-invalidation-firewalls is deeply geometric: a contract is
a **descent condition** that, when satisfied, guarantees local changes
don't affect global properties. This is exactly the sheaf condition.

---

## 14. Pack Federation Combinatorics

### The problem

`packs/federation.py` combines evidence from multiple domain packs via
bridge theorems. At 1M LOC with many domain packs:

- **Pack count**: A large project might use 20–50 domain packs (numpy,
  pandas, flask, sqlalchemy, etc.)
- **Bridge theorems**: O(packs²) possible bridges ≈ ~1,000 bridge theorems
- **Federation queries**: For each cross-pack judgment, the federation
  engine must find and apply relevant bridges
- **Combinatorial explosion**: Multiple overlapping bridges can compose
  in exponentially many ways

### What breaks

- Bridge search becomes O(bridges × judgments) per cross-pack verification
- Bridge composition can create exponentially many candidate paths
- Pack loading overhead: 50 packs × initialization time

### Solution: Bridge Index with Typed Routing

1. **Bridge index**: Index bridges by their source/target pack and the
   proposition pattern they match. Bridge lookup becomes O(1) per query
   instead of O(bridges).

2. **Typed bridge routing**: Classify bridges by the type of proposition
   they transport (type safety, resource safety, concurrency, etc.).
   Route queries directly to the relevant bridge type without searching.

3. **Lazy pack loading**: Don't load a pack until its coordinates are
   actually needed. A 1M LOC codebase might import numpy, but only
   5% of the code uses numpy. Load the numpy pack only for that 5%.

4. **Pre-composed bridge bundles**: For common pack combinations
   (flask + sqlalchemy, numpy + pandas), pre-compute composed bridges
   and cache the results.

5. **Federation result caching**: Cache federation results keyed by
   (source_judgment, bridge_set). Identical cross-pack queries
   return cached results.

### Paradigm integration

Bridge indexing is a **morphism index** on the inter-pack site. Typed
routing uses the type structure of propositions to restrict the search
space. This is a standard technique in computational category theory.

---

## 15. Server Architecture

### The problem

`cli/cmd_server.py` runs a synchronous `http.server.HTTPServer`. This
is fine for development but cannot handle the load of a 1M LOC
verification service:

- **Synchronous**: One request at a time
- **No websockets**: Can't push live updates (progress, obstructions)
- **No authentication**: Not suitable for multi-user environments
- **No request queuing**: Long verification requests block the server

### What breaks

- Multiple developers can't use the server simultaneously
- Long verification runs block all other requests
- No way to stream incremental results
- No way to cancel in-progress verification

### Solution: Async Service with Task Queue

1. **ASGI server**: Replace `http.server` with an async framework
   (FastAPI, Starlette). This gives:
   - Concurrent request handling
   - WebSocket support for live updates
   - Automatic OpenAPI documentation
   - Middleware for auth, rate limiting, CORS

2. **Task queue**: Long-running verification tasks are dispatched to
   a background task queue (Celery, Dramatiq, or custom with Redis).
   The API returns a task ID immediately; clients poll or subscribe via
   WebSocket.

3. **Streaming results**: Use WebSockets or Server-Sent Events to push
   incremental results: new judgments, discharged obligations, detected
   obstructions, trust changes.

4. **Multi-tenant support**: Add workspace isolation so multiple
   developers (or CI jobs) can verify different codebases simultaneously
   without interference.

5. **Cancellation**: Support cancelling in-progress verification via
   task queue cancellation. The orchestrator's backpressure mechanism
   already supports graceful stopping.

### Paradigm integration

The server is a **projection surface** (per JuGeo's `interfaces/`
design). WebSocket streams are **live section projections** — they
project the evolving verification state to the client without
strengthening or weakening claims. Task queuing is budget management
at the service level.

---

## 16. Human-in-the-Loop Scaling

### The problem

At 1M LOC, the human can't review every obstruction, treaty, or
promotion. Currently, JuGeo generates obstructions and repair frontiers
but relies on the human to prioritize and address them.

- **Obstruction count**: ~50,000 obstructions at first run on a 1M LOC
  codebase
- **Treaty proposals**: ~10,000 interface negotiations
- **Promotion requests**: ~100,000 trust escalations needing review

### What breaks

- Humans can review ~50 items/day. At 50,000 obstructions, that's
  1,000 developer-days just for initial triage
- No prioritization guidance — all obstructions look equally important
- No batch operations — each obstruction is handled individually

### Solution: Risk-Based Triage with Autonomous Resolution

1. **Risk scoring**: Score each obstruction by:
   - Blast radius (how many downstream judgments are affected)
   - Trust deficit (how far below the desired trust level)
   - Repair cost (estimated effort from repair frontier)
   - Business criticality (from coordinate metadata: security, payments,
     auth get higher scores)

2. **Autonomous resolution for low-risk items**: Obstructions with
   low blast radius, clear repair frontiers, and non-critical coordinates
   can be resolved automatically (using the ideation engine to find fixes
   and the orchestration engine to verify them). Human is notified but
   doesn't need to act.

3. **Batch operations**: Group related obstructions (same root cause,
   same module, same pattern) and present them as a single action item.
   "37 functions in `utils/` are missing return-type annotations" is
   one item, not 37.

4. **Progressive verification**: Don't verify everything at once. Start
   with the most critical code (security, payments, auth), verify it
   thoroughly, then gradually expand. The human reviews only the
   expanding frontier.

5. **Confidence thresholds**: Set per-module confidence thresholds.
   Code in `tests/` might only need RUNTIME_WITNESSED trust. Code in
   `auth/` needs VERIFIED_PROOF. This dramatically reduces human
   review load.

### Paradigm integration

Risk scoring uses the site topology: blast radius is a graph property
of the coordinate's position in the site. Progressive verification is
**cover expansion** — starting with a small cover and growing it.
Confidence thresholds are **trust requirements on coordinates** — a
natural part of the judgment framework.

---

## 17. Cross-Cutting Solution: Hierarchical Site Decomposition

The single most impactful change for scaling is **hierarchical site
decomposition** — replacing the flat site with a multi-level hierarchy
that mirrors the project's natural structure.

```
Level 0: Project
  Level 1: Packages (top-level directories)
    Level 2: Modules (files)
      Level 3: Classes / Functions
        Level 4: Branches / Loops / Expressions
```

### Benefits

- **Memory**: Only one level needs to be fully in-memory at a time
- **Descent**: O(n × avg_degree) instead of O(n²)
- **Parallelism**: Independent packages verify in parallel
- **Caching**: Changes at Level 4 don't invalidate Level 0
- **Human comprehension**: Results presented at the appropriate level

### Implementation

- Add a `HierarchicalSite` class that wraps `Site` with level-aware
  operations
- Modify `cover_design/` to generate hierarchical covers by default
- Modify `descent.py` to perform level-by-level descent
- Modify `orchestration/controller.py` to manage per-level frontiers

### This is NOT bolted-on

Hierarchical sites are **exactly what Grothendieck topologies model**.
The project hierarchy is a basis for the topology. Hypercovers refine
this hierarchy. Descent at level N requires data from levels N and N+1
only. This is textbook sheaf theory, applied to a practical scaling
problem.

---

## 18. Cross-Cutting Solution: Persistent Indexed Backend

Replace in-memory dicts with a **persistent indexed store** for all
semantic state.

### Recommended: SQLite (single-machine) / PostgreSQL (multi-machine)

| Table | Key | Indexed Columns | Estimated Rows (1M LOC) |
|---|---|---|---|
| `coordinates` | coordinate_id | name, kind, depth, package | 10M |
| `morphisms` | morphism_id | source, target, kind | 50M |
| `judgments` | judgment_id | coordinate_id, trust_level, status | 5M |
| `evidence` | evidence_id | judgment_id, channel, trust_level | 20M |
| `obligations` | obligation_id | judgment_id, status, priority | 2M |
| `obstructions` | obstruction_id | coordinate_id, class, severity | 500K |
| `treaties` | treaty_id | parties, status | 10K |
| `certificates` | certificate_id | judgment_id, version | 1M |

### Benefits

- **Memory**: Only hot data in RAM; cold data on disk
- **Queries**: Complex queries pushed to the database engine
- **Persistence**: State survives process restarts
- **Concurrency**: Multiple workers can read/write safely
- **Transactions**: ACID guarantees for state consistency

### Implementation

- Add a `storage/` module with abstract `Store` interface and
  SQLite/PostgreSQL backends
- Modify `manifests.py` to delegate to the backend instead of in-memory
  dicts
- Add migration scripts for schema evolution
- Keep an in-memory LRU cache over the backend for hot-path performance

### Paradigm integration

The database schema *is* the judgment sheaf, serialized. Coordinate indices
are the site's coordinate system. Foreign keys between tables encode
morphisms. SQL queries are sections restricted to coordinate predicates.
This is a faithful representation of the sheaf in relational form.

---

## 19. Cross-Cutting Solution: Distributed Worker Architecture

For codebases beyond ~100K LOC, single-machine verification is
insufficient. A distributed architecture distributes verification
across multiple machines.

### Design

```
┌─────────────────┐
│  Coordinator     │  Manages global state, frontier, budget
│  (1 process)     │  Dispatches partitions to workers
└────────┬────────┘
         │
    ┌────┼────┬────────┐
    ▼    ▼    ▼        ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│Worker│ │Worker│ │Worker│ │Worker│  Each verifies a site partition
│  1   │ │  2   │ │  3   │ │  N   │  Reports results back
└──────┘ └──────┘ └──────┘ └──────┘
    │        │        │        │
    └────────┼────────┼────────┘
             ▼
    ┌─────────────────┐
    │ Persistent Store │  Shared state (PostgreSQL / S3)
    └─────────────────┘
```

### Partition assignment

1. Compute strongly connected components of the import graph
2. Assign each SCC to a worker (load-balanced by LOC)
3. Cross-SCC edges become inter-worker descent tasks
4. The coordinator handles cross-worker treaty negotiation

### Communication protocol

- Workers report: judgment deltas, evidence records, obstructions
- Coordinator sends: partition assignments, treaty proposals, phase
  transitions, budget adjustments
- Protocol: gRPC or NATS for low-latency messaging

### Fault tolerance

- Workers are stateless (state is in the persistent store)
- If a worker dies, its partition is reassigned to another worker
- Checkpointing ensures no work is lost
- The coordinator can be replicated for high availability

### Paradigm integration

This is **distributed sheaf descent**: each worker verifies local sections
over its partition, and the coordinator performs global descent across
partition boundaries. The communication protocol carries the same
judgment/evidence/treaty objects that flow within a single process.
The only change is the transport layer.

---

## 20. Cross-Cutting Solution: Incremental Everything

The most important performance principle for large codebases is
**never re-compute what hasn't changed**. This applies to every layer:

| Layer | Incremental Strategy |
|---|---|
| **AST parsing** | File-hash based; only re-parse changed files |
| **Coordinate assignment** | Delta coordinates for changed functions only |
| **Morphism generation** | Add/remove morphisms for changed import edges |
| **Covering families** | Adjust covers locally; don't regenerate globally |
| **Solver queries** | Cache results by query hash; only solve new queries |
| **Descent** | Only re-check overlaps involving changed sections |
| **Evidence** | Preserve valid evidence; only invalidate what depends on changes |
| **Trust** | Lazy propagation with trust-dirty marking |
| **Treaties** | Re-negotiate only affected treaties |
| **Certificates** | Patch certificates with deltas instead of regenerating |
| **Checkpoints** | WAL-based incremental persistence |

### Implementation priority

1. **File-hash caching** for AST parsing (highest ROI, simplest)
2. **Solver result caching** by query hash (second highest ROI)
3. **Contract-based invalidation boundaries** (third — prevents cascades)
4. **Incremental descent** (fourth — only re-check changed overlaps)
5. **Everything else** (subsequent phases)

### Paradigm integration

Incrementality is **sheaf restriction**: when the site changes locally,
only the local section and its overlaps need re-verification. The descent
condition guarantees that unchanged sections remain valid. This is the
**fundamental theorem of sheaf theory applied to caching**: if nothing
changed locally, the global section is still valid.

---

## 21. Implementation Roadmap

### Phase 1: Foundation (enables everything else)

1. **Persistent indexed backend** (SQLite initially)
2. **File-hash based incremental AST parsing**
3. **Solver result caching by query hash**
4. **Hierarchical site construction from project structure**

Expected result: JuGeo can handle ~100K LOC on a single machine with
tolerable performance (~minutes for initial run, seconds for incremental).

### Phase 2: Single-Machine Scaling

5. **Hierarchical descent** (O(n × degree) instead of O(n²))
6. **Multi-process workers** for CPU-bound tasks (parsing, solver)
7. **Contract-based invalidation boundaries**
8. **Tiered storage** (hot/warm/cold)
9. **Incremental descent** (only re-check changed overlaps)

Expected result: JuGeo can handle ~500K LOC on a multi-core workstation.
Initial run in ~10 minutes, incremental in ~30 seconds.


---

## Summary of Pain Points and Solutions

| # | Pain Point | Root Cause | Solution | Phase |
|---|---|---|---|---|
| 1 | In-memory everything | Python dicts | Persistent backend + tiered storage | 1 |
| 2 | Whole-program AST loading | Eager parsing | Incremental + parallel + lazy | 1 |
| 3 | O(n²) descent | Flat covers | Hierarchical covers + hypercovers | 2 |
| 4 | Z3 session bottleneck | Single pool | Distributed solvers + batching + caching | 2 |
| 5 | Single-process execution | Python GIL | Multi-process workers | 2 |
| 6 | Checkpoint cost | Full-state dumps | WAL + incremental + compaction | 1 |
| 7 | Import graph explosion | Full walk | Incremental + lazy + SCCs | 1 |
| 8 | Evidence store scans | Missing indices | Multi-index store + DB delegation | 1 |
| 9 | Orchestration state | Flat frontier | Hierarchical controllers + pruning | 2 |
| 10 | Ideation search space | Combinatorial | LSH + approximate novelty + budget | 3 |
| 11 | Trust propagation | Eager cascade | Trust zones + lazy propagation | 2 |
| 12 | Cache invalidation | Deep cascades | Contract firewalls + dampening | 2 |
| 13 | Pack federation | Bridge combinatorics | Bridge index + typed routing | 2 |
| 14 | Server architecture | Sync HTTP | ASGI + task queue | 4 |
| 15 | Human-in-the-loop | No triage | Risk scoring + autonomous resolution | 4 |

### The Unifying Principle

Every scaling solution above exploits the same mathematical fact:
**the sheaf condition guarantees that local verification composes to
global correctness**. This means:

- **Partition freely**: any partition that respects the site topology
  produces valid local verifications that can be glued
- **Cache aggressively**: the descent condition tells you exactly when
  a cached result is still valid
- **Parallelize naturally**: non-overlapping covers verify independently
- **Invalidate minimally**: contracts-as-interfaces are invalidation
  firewalls by construction

JuGeo's mathematical foundations aren't just theoretical elegance — they're
the key to scaling. The sheaf paradigm *is* the scaling strategy.

---
---

# PART B — The JG-Theoretic Approach to Software Engineering at Scale

> Part A addressed *infrastructure* scaling — making JuGeo's internal
> machinery handle 10M coordinates. Part B addresses the *software
> engineering* scaling — how judgment geometry provides a unified
> mathematical framework for the activities that humans and teams perform
> on million-LOC codebases: organizing, architecting, testing, debugging,
> and improving.

---

## B1. The Core Thesis

Traditional software engineering treats organizing, testing, debugging,
and improving as separate activities with separate tools: linters for
style, type checkers for types, test frameworks for tests, profilers for
performance, dependency analyzers for architecture. Each tool operates
independently, with its own model of the codebase, its own notion of
"correct," and its own failure reporting.

Judgment geometry offers a **unified mathematical framework** where all
these activities are instances of the same geometric operations:

| SE Activity | JG Operation |
|---|---|
| **Organizing** | Constructing the semantic site (coordinates, morphisms, covers) |
| **Architecting** | Designing covers with good overlap properties |
| **Testing** | Constructing local evidence sections (witnesses at coordinates) |
| **Debugging** | Localizing descent failures to specific overlaps and coordinates |
| **Refactoring** | Applying refinement morphisms that preserve descent |
| **Code review** | Checking that new sections are consistent with existing ones |
| **Technical debt** | Measuring obstruction density and cover quality degradation |
| **CI/CD** | Incremental descent verification and certificate gating |
| **Team coordination** | Jurisdiction management over site partitions |

The unifying principle: **every SE activity either constructs local
sections, checks descent, repairs obstructions, or manages the trust
algebra**. There is no SE activity that falls outside this framework.

---

## B2. Architecture as Cover Design

### The geometric reframing

Software architecture is the problem of decomposing a codebase into
modules with clean interfaces. In JG terms, architecture is **cover
design**: choosing a covering family {U_i} for the site such that:

1. Each U_i is **internally coherent** (local sections within a module
   are consistent)
2. Overlaps U_i ∩ U_j are **small and well-defined** (interfaces between
   modules are narrow)
3. The covering is **complete** (every coordinate is covered — no orphan code)
4. Descent is **efficient** (checking overlaps is O(n × degree), not O(n²))

### What this gives you at 1M LOC

**Architectural metrics become geometric invariants:**

- **Coupling** = overlap density: how many coordinates appear in multiple
  covers? High coupling = large overlaps = expensive descent.
- **Cohesion** = cover compactness: how tightly related are the coordinates
  within a single cover? Low cohesion = cover members that don't share
  morphisms.
- **Interface width** = overlap cardinality: how many propositions must
  agree at the overlap between two modules? Wide interfaces = expensive
  treaty negotiation.
- **Dependency depth** = morphism chain length in the site: deep dependency
  chains create long trust propagation paths.
- **Circular dependencies** = non-trivial fundamental group of the site:
  import cycles create topological obstructions to clean covering.

**Architectural decisions become geometric operations:**

- **Extract module** = refine a cover by splitting one cover member into
  two with a new overlap
- **Merge modules** = coarsen a cover by merging two cover members
- **Define interface** = specify the propositions that must hold on an
  overlap (a treaty)
- **Enforce boundary** = restrict morphisms to only those that pass through
  declared interface coordinates
- **Resolve circular dependency** = collapse an SCC in the import graph
  to a hypercover

### What JuGeo already has

- `generation/cover_design/` — cover synthesis, dependency ordering,
  parallelism strategy, quality metrics, completion criteria
- `python_runtime/import_graph/` — import graph as site morphisms, SCC
  detection, package boundary analysis
- `packs/` — domain packs as bounded local theories with authority,
  bridges, and federation
- `orchestration/treaty_memory/` — treaties as formalized interface
  agreements extracted from descent conditions

### What's needed for 1M LOC

- **Automatic cover suggestion**: Given a codebase with no explicit module
  boundaries, infer an optimal covering family from the morphism structure
  (spectral clustering on the coordinate graph, minimizing overlap density).
- **Cover quality dashboard**: Real-time metrics showing coupling, cohesion,
  interface width, and dependency depth per package. Degradation triggers
  refactoring suggestions.
- **Architecture-as-code**: Declare intended covers in a manifest file.
  JuGeo verifies that the actual code respects the declared architecture
  (no undeclared cross-module imports, no interface violations).
- **Architecture evolution tracking**: Store cover snapshots over time.
  Detect architectural drift (the actual covering diverging from the
  intended one) as a geometric obstruction.

---

## B3. Testing as Witness Construction

### The geometric reframing

Testing is traditionally understood as "running the code and checking
outputs." In JG, testing is **constructing evidence sections** at
coordinates — building local witnesses that, when they glue, certify
global properties.

A **test** is a local evidence record:
```
(coordinate, proposition, evidence_channel=RUNTIME, trust=RUNTIME_WITNESSED)
```

A **test suite** is a collection of local evidence records. The suite
provides **coverage** in the geometric sense: the set of coordinates
at which evidence has been constructed.

**Test adequacy** is a descent question: do the local witnesses glue
into a global certificate? If a function at coordinate c₁ calls a
function at coordinate c₂, testing c₁ and c₂ independently is
insufficient — you must also test the overlap (the call boundary).

### What this gives you at 1M LOC

**Test generation from covers:**

- Given a covering family, automatically generate test obligations:
  one test for each cover member (local correctness) plus one for
  each non-trivial overlap (interface correctness). This eliminates
  guesswork about what to test.
- **Residual gap analysis**: after running the test suite, compute the
  coordinates and overlaps that lack evidence. These are the literal
  "uncovered" parts of the codebase — not in the line-coverage sense,
  but in the sheaf-theoretic sense.

**Trust-aware test prioritization:**

- Tests that produce VERIFIED_PROOF evidence (via solver discharge)
  are more valuable than tests that produce RUNTIME_WITNESSED evidence.
- Tests at high-coupling overlaps (wide interfaces) are more valuable
  than tests at low-coupling coordinates (isolated functions).
- The trust algebra tells you exactly which tests to prioritize: those
  that would raise the trust floor of the most important judgments.

**Regression testing as re-descent:**

- A code change invalidates local sections at specific coordinates.
  Regression testing = re-verifying descent at the affected overlaps.
  The invalidation graph tells you exactly which tests to re-run — not
  "everything that might be affected" but "exactly the overlaps that
  were certified by the invalidated evidence."

### What JuGeo already has

- `problem_modes/specification_satisfaction/` — specs as target sections,
  witnesses, residual gaps, descent-based satisfaction checking
- `evidence/channels.py` — runtime evidence channel with trust ceiling
- `experiments/exp60_test_generation.py` — test generation from covers

### What's needed for 1M LOC

- **Test obligation generation at scale**: Given the hierarchical site,
  generate test obligations per-level (function tests, module integration
  tests, package integration tests, system tests) as a hierarchy of
  descent checks.
- **Incremental test selection**: On code change, use the invalidation
  graph to select exactly the tests that need re-running. This is
  mathematically precise test selection, not heuristic.
- **Evidence store integration**: Persist test results as evidence records
  in the backend. Enable queries like "which overlaps have no
  RUNTIME_WITNESSED evidence in the last 30 days?"

---

## B4. Debugging as Obstruction Localization

### The geometric reframing

Debugging is traditionally understood as "finding and fixing bugs." In JG,
debugging is **localizing descent failures to specific coordinates and
overlaps, extracting countermodels, and computing repair frontiers**.

A **bug** is an obstruction: a coordinate where the local section fails
to satisfy its proposition, or an overlap where two local sections
disagree. The obstruction carries:

- **Coordinate**: exactly where in the codebase the failure lives
- **Proposition**: exactly what property failed
- **Cohomology class**: what *kind* of failure it is (type error, logic
  error, protocol violation, resource leak, concurrency hazard, etc.)
- **Repair frontier**: the minimal set of coordinates whose modification
  could restore descent
- **Blast radius**: how many downstream judgments are affected
- **Countermodel**: a concrete witness to the failure

### What this gives you at 1M LOC

**Structured bug triage:**

- Every obstruction has a cohomology class. Bugs of the same class can
  be batched ("37 type errors in utils/", "12 missing null checks in
  handlers/"). This turns a 50,000-obstruction first run into ~500
  categorized action items.

**Root cause analysis via descent:**

- A symptom at coordinate c might be caused by a failure at coordinate
  c' connected by a morphism. Descent tracing follows the morphism
  chain to find the *root* coordinate where the local section first
  breaks. This is automated root-cause analysis — not "git blame" but
  "descent blame."

**Repair frontier computation:**

- Given an obstruction, compute the minimal set of coordinates whose
  modification would restore descent. This answers the question "what
  do I need to change?" not "what went wrong?" — it's prescriptive,
  not just diagnostic.

**Counterexample-driven debugging:**

- The solver's countermodel extraction (`solver/countermodels.py`)
  provides concrete inputs that trigger the bug. These counterexamples
  are structured data, not just stack traces — they can be automatically
  converted to regression tests.

### What JuGeo already has

- `problem_modes/bug_detection/` — AST-to-coordinate bridge, staged
  detection, cohomology class labeling
- `problem_modes/repair_semantics/` — counterexample extraction, repair
  planning, repair execution, debug orchestration
- `solver/countermodels.py` — countermodel extraction, failure
  classification, repair hints

### What's needed for 1M LOC

- **Obstruction database with similarity search**: Store all obstructions
  in a persistent backend with embedding-based similarity search. Enable
  queries like "find all bugs similar to this one across the codebase."
- **Incremental bug detection**: On code change, only re-check the
  affected coordinates and overlaps. The invalidation graph bounds
  the scope of re-analysis.
- **Automated repair with human review**: For obstructions with
  high-confidence repair frontiers and low blast radius, apply the
  repair automatically and present it as a PR for human review.

---

## B5. Refactoring as Refinement Morphism

### The geometric reframing

Refactoring is traditionally "changing code structure without changing
behavior." In JG, refactoring is **applying a refinement morphism to
the site that preserves or strengthens descent**.

A refactoring is correct if and only if: for every judgment J at every
coordinate c, the judgment after refactoring J' satisfies J ≤ J' (the
refinement relation). In other words, the refactored code satisfies
at least everything the original code satisfied.

### What this gives you at 1M LOC

- **Refactoring correctness as a descent check**: After applying a
  refactoring, verify that all existing judgments still hold. Any
  failure is a refactoring-induced regression — identified exactly
  at the coordinate and proposition where it occurs.
- **Refactoring safety scoring**: Before applying a refactoring, compute
  its blast radius (how many judgments and overlaps could be affected).
  High-blast-radius refactorings get more scrutiny.
- **Migration as transport**: Moving from one library to another (e.g.,
  `requests` → `httpx`) is a functor between sites. The migration is
  correct if the functor preserves descent. This gives a formal
  framework for migration planning.

### What JuGeo already has

- `problem_modes/relational_refinement/` — refinement checking,
  equivalence verification, witness construction, order-theoretic
  algorithms
- `problem_modes/public_alignment/` — public projection honesty,
  migration analysis
- `experiments/exp63_migration_planning.py` — migration as
  structure-preserving transport

---

## B6. Code Review as Section Compatibility

### The geometric reframing

Code review is "checking that proposed changes are correct and
consistent." In JG, code review is **checking that new local sections
are compatible with existing sections on their shared overlaps**.

A PR introduces new or modified sections at specific coordinates. Review
checks:

1. **Internal consistency**: Do the new sections satisfy their local
   propositions?
2. **Overlap compatibility**: Do the new sections agree with existing
   sections on shared coordinates?
3. **Trust adequacy**: Is the evidence for the new sections at an
   appropriate trust level?
4. **Public projection honesty**: Do any public-facing claims change,
   and if so, are they honest?

### What this gives you at 1M LOC

- **Automated review scope**: The site topology determines exactly which
  existing sections overlap with the PR's changes. No need to guess
  "who should review this" — the overlap structure tells you which
  modules, teams, and treaties are affected.
- **Trust-aware review**: Changes that lower the trust level of a judgment
  require more scrutiny than changes that preserve or raise it.
- **Treaty impact analysis**: If a PR changes code at an interface
  overlap, the treaty for that interface must be re-negotiated. The
  review process includes the treaty renegotiation.

### What JuGeo already has

- `experiments/exp67_code_review_automation.py` — review via executable
  specs + bug scanning
- `problem_modes/public_alignment/honesty_enforcement.py` — honesty
  checking for public projections
- `orchestration/treaty_memory/` — treaty renegotiation on interface changes

---

## B7. Technical Debt as Geometric Degradation

### The geometric reframing

Technical debt is traditionally a metaphor. In JG, it has a precise
geometric definition: **technical debt is the accumulation of
obstructions, the degradation of cover quality, and the decay of trust
levels across the site**.

### Debt metrics as geometric invariants

- **Obstruction density**: obstructions per coordinate. High density =
  many unresolved failures = debt.
- **Trust floor**: the minimum trust level across all judgments. A
  codebase where critical paths have only COPILOT_SUGGESTED trust has
  high debt.
- **Cover quality degradation**: increasing coupling (overlap density),
  decreasing cohesion (cover compactness), widening interfaces.
- **Evidence staleness**: evidence records whose timestamp is far from
  the last code change at that coordinate. Stale evidence = debt.
- **Repair frontier size**: the total number of coordinates in all
  repair frontiers. Large frontiers = many known but unaddressed issues.

### What this gives you at 1M LOC

- **Debt dashboard**: Real-time geometric metrics aggregated by package,
  module, team, and severity. Track debt over time as the site evolves.
- **Debt-driven prioritization**: The orchestration engine can allocate
  budget to debt reduction (repair obstructions, re-certify stale
  evidence, improve cover quality) as a first-class activity alongside
  feature development.
- **Debt thresholds as release gates**: Set per-package thresholds for
  obstruction density, trust floor, and evidence staleness. CI blocks
  releases that exceed thresholds.

### What JuGeo already has

- `experiments/exp68_technical_debt.py` — debt scoring from trust, bugs,
  obstructions, and maturity cycles
- `maturity/cyclic_picture/` — cyclic improvement model with maturity
  levels and self-improving pipelines
- `evaluation/scaling_limits/` — complexity bounds and scaling laws

---

## B8. CI/CD as Incremental Descent Verification

### The geometric reframing

CI/CD is "automatically building, testing, and deploying code." In JG,
CI/CD is **incremental descent verification with certificate gating**.

A CI pipeline is a sequence of descent checks at increasing scope:

1. **Pre-commit**: Local descent within the changed files (function-level)
2. **Pre-merge**: Descent at the package level (overlap checks between
   changed files and their imports)
3. **Post-merge**: Full descent at the project level (site-wide)
4. **Release gate**: Certificate emission — the release artifact carries
   a certificate recording trust levels, evidence coverage, and
   residual obligations

### What this gives you at 1M LOC

- **Incremental CI**: Only re-verify the coordinates and overlaps affected
  by the change. The invalidation graph provides the exact scope.
- **Trust-aware gates**: Different stages require different trust levels.
  Pre-commit might accept RUNTIME_WITNESSED; release gate might require
  SOLVER_DISCHARGED for critical paths.
- **Certificate-carrying artifacts**: The deployed artifact includes a
  machine-readable certificate recording exactly what was verified, at
  what trust level, with what evidence, and what obligations remain.

### What JuGeo already has

- `experiments/exp65_ci_cd_integration.py` — staged verification across
  pipeline stages
- `evidence/certificates.py` — certificate chains, authorities, verification
- `runtime/invalidation.py` — dependency graph, cascade invalidation,
  repair scheduling

---

## B9. Team Coordination as Jurisdiction Management

### The geometric reframing

Team coordination for large codebases involves code ownership, review
policies, deployment permissions, and responsibility for incidents. In
JG, this is **jurisdiction management over site partitions with authority
and delegation**.

- **Code ownership** = authority grants over coordinate regions. Team A
  owns package X, meaning they have authority to modify sections at
  coordinates within X and to approve modifications by others.
- **Review policy** = trust requirements on sections. Changes to
  security-critical coordinates require VERIFIED_PROOF trust (formal
  review); changes to test fixtures require only RUNTIME_WITNESSED
  (automated tests pass).
- **Delegation** = authority delegation chains. Team A can delegate
  review authority for a specific coordinate to Team B, with trust
  attenuation (the delegated authority can approve up to
  SOLVER_DISCHARGED but not VERIFIED_PROOF).
- **Incident response** = obstruction escalation. When an obstruction
  has blast radius exceeding a threshold, escalate to the authority
  for the affected region.

### What JuGeo already has

- `kernel/authority.py` — authority tiers, domains, grants, registry,
  enforcement, delegation chains, audit logs
- `experiments/exp64_team_workflow.py` — multi-developer jurisdiction
  simulation

### What's needed for 1M LOC

- **Integration with GitHub/GitLab CODEOWNERS**: Map CODEOWNERS files
  to authority grants in the JuGeo site.
- **Authority-aware routing**: When an obstruction is detected, route
  it to the team with authority over the affected coordinates.
- **Cross-team treaty negotiation**: When two teams' code interacts at
  an overlap, automatically generate a treaty proposal and route it
  to both teams for approval.

---

## B10. Continuous Improvement as Cyclic Maturity

### The geometric reframing

Large-codebase improvement is not a one-time activity — it's a
continuous cycle of verification, repair, and hardening. JuGeo's
maturity model (`maturity/cyclic_picture/`) captures this:

1. **Assess**: Run site-wide analysis, compute geometric metrics
2. **Prioritize**: Rank obstructions by blast radius and repair cost
3. **Repair**: Apply repair frontiers, re-verify descent
4. **Certify**: Emit updated certificates, update trust levels
5. **Repeat**: The cycle never ends — it's a fixed-point iteration on
   the verification state

Each cycle improves the "maturity level" of the codebase:
- Level 0: No verification (raw code)
- Level 1: Local evidence (tests exist)
- Level 2: Local descent (tests + solver for critical paths)
- Level 3: Global descent (cross-module consistency verified)
- Level 4: Full certification (proof-carrying artifact with certificates)

---
---

# PART C — JG as a Strict Generalization of Comet-H

> Comet-H (Young 2026) proposes an *augmented Mealy machine* —
> finite control modes extended with exponentially decaying obligation
> counters — for orchestrating LM-driven research software co-evolution.
> It addresses three failure modes: hallucination accumulation,
> world-model staleness, and specification drift. Its core contribution
> is treating the workspace (theory, code, paper, evidence, obligations)
> as a single mutable state and controlling prompt selection via a
> feature-vector scorer over workspace deficits and obligation pressure.
>
> We show that Judgment Geometry **strictly generalizes** every
> component of Comet-H while providing deeper mathematical structure,
> greater expressiveness, and complete fidelity to algebraic geometry.
> Every Comet-H concept is a special case of a JG concept; JG can
> implement everything Comet-H does, and much more, while being
> theoretically cleaner.

---

## C1. The Correspondence Table

| Comet-H concept | Comet-H formalization | JG generalization | Why JG is strictly more general |
|---|---|---|---|
| **Workspace state** W = (T, R, P, E, U, Q) | Flat tuple of surfaces | **Judgment sheaf** over a semantic site: each surface is a section at coordinates with trust, evidence, and provenance | Comet-H treats surfaces as opaque blobs. JG gives each surface *internal structure* — coordinates, morphisms, local propositions — enabling fine-grained reasoning. |
| **Artifact surfaces** (theory, code, paper, evidence) | Four named surfaces | **Coordinates in the site** with typed morphisms between them | Comet-H has exactly 4 surfaces. JG has an arbitrary, extensible coordinate system — add "benchmark," "deployment," "user feedback" as new coordinates without changing the framework. |
| **Co-evolution drift** | Informal description of four drift types | **Descent failure** between sections at overlapping coordinates: theory-code drift = sections at theory and code coordinates that fail to glue on their overlap | Comet-H can only *detect* drift informally (by running an audit prompt). JG can *precisely characterize* descent failures: which coordinates, which propositions, which overlap, what repair frontier. |
| **Hallucination accumulation** (FM-1) | Multi-step propagation of fabricated claims | **Trust violation + obstruction propagation**: a COPILOT_SUGGESTED claim silently promoted to VERIFIED_PROOF status. The "no silent promotion" law prevents this by construction. | Comet-H addresses FM-1 *reactively* (grounding trigger after each step). JG prevents it *structurally*: the trust algebra makes it algebraically impossible to promote LM output to formal-proof trust without explicit evidence. |
| **World-model staleness** (FM-2) | Implicit model diverges from disk state | **Section invalidation**: when disk state changes, the invalidation graph marks affected sections as stale, and the semantic cache evicts them. The orchestrator operates on validated sections only. | Comet-H has no mechanism for FM-2 beyond "re-read files." JG's invalidation graph tracks exactly which cached judgments are stale and repairs them incrementally. |
| **Specification drift** (FM-3) | Model optimizes under original spec | **Ideation engine + adjacency as cover refinement**: the ideation layer generates spec-revision candidates; adjacency constraints are cover refinement morphisms that must preserve existing evidence. | Comet-H constrains drift via 5 manual adjacency rules. JG's cover refinement is a universal mathematical construction: any spec revision is a cover refinement that must preserve descent, checked automatically. |
| **Obligation vector** v ∈ ℝ≥0^5 | 5-dimensional decaying counter | **Obligation presheaf**: each obligation is a judgment with coordinate, proposition, trust, evidence, and status (PENDING/ASSIGNED/DISCHARGED/FAILED/EXPIRED). Obligations live on the site, not in a flat vector. | Comet-H's 5 dimensions (capability, structural, benchmark, documentation, grounding) are fixed and scalar. JG's obligations are typed, coordinate-aware, trust-carrying, and compositional. You can have 5 or 5,000 obligation types, each at specific coordinates. |
| **Exponential decay** λ = 2^{-1/8} | Scalar decay on vector components | **Trust decay with support**: evidence freshness decays based on time and code changes, but the decay is *support-aware* — evidence at unchanged coordinates doesn't decay. | Comet-H's uniform decay is semantically wrong: a documentation obligation shouldn't decay if the docs haven't changed. JG's support-aware decay is precise. |
| **Bounded pressure** ‖v‖₁ ≤ k·α_max/(1−λ) | Scalar pressure bound | **Backpressure controller** with multi-dimensional budget, phase transitions, and convergence monitoring | Comet-H proves pressure is bounded (good). JG goes further: the backpressure controller detects *which dimension* is overloaded and adjusts strategy accordingly (pause generation, focus on audit, switch phase). |
| **Feature-vector scorer** ⟨w_i, feat(W,v)⟩ + b_i | Linear dot product over features | **Frontier search** with multi-objective scoring: closure gain, stability, diversity, cost, trust improvement. Plus fleet competition where multiple strategies bid. | Comet-H deliberately uses a linear scorer for interpretability. JG's frontier scorer is also interpretable (each criterion has a named meaning) but supports multi-objective Pareto ranking, adaptive control laws, and competitive bidding. |
| **Mode graph** {seed, generate, harden, tail} | 4 control states with manual transitions | **Phase management** with automatic phase detection, exploration/exploitation/recovery transitions, and convergence-based halting | Comet-H's 4 modes are hand-designed. JG's phases are *detected from the semantic state*: when progress stalls, the system switches from exploitation to recovery automatically. |
| **Prompt families** q₂–q₁₈ | 17 prompt types | **Semantic moves** with preconditions and effects, organized into structural/logical/geometric/evidence/treaty categories. Plus fleet competition over move selection. | Comet-H's prompts are a fixed alphabet. JG's moves are extensible, typed, and composable — you can define new moves without changing the controller. |
| **Grounding trigger** τ | Reactive: paper change → force audit | **Descent obligation**: any section change triggers re-verification of affected overlaps. This is not a special trigger — it's the *normal operation* of the sheaf condition. | Comet-H needs a special mechanism for grounding. In JG, grounding is simply descent checking — it falls out of the mathematics for free. |
| **Adjacency constraint** | 5 manual rules for spec revision | **Cover refinement morphism** that must preserve existing evidence | Comet-H's adjacency rules are ad-hoc (preserve flagship, distance-1, case-studyable, etc.). JG's refinement morphism is a universal construction: it preserves descent by theorem, not by rule list. |
| **Audit projection** Π_W | Monotone, deflationary, idempotent projection on claim lattice | **Public alignment** with honesty enforcement: the projection functor π_pub is faithful (preserves logical strength of claims) | Comet-H proves audit stabilizes after finitely many contractions (good). JG additionally guarantees *honesty*: public claims never exceed internal evidence, checked continuously. |
| **Contraction and descent** (Appendix B) | Audited potential Λ(W) = (u(W), ‖δ(W)‖₁) | **Convergence monitor** with Lyapunov-style convergence certificates, obligation tracking, coverage analysis, divergence detection | Comet-H's descent is a 2-dimensional potential. JG's convergence monitor tracks arbitrarily many dimensions and can *certify* convergence or *detect* divergence and trigger recovery. |

---

## C2. Why the Generalization Is Strict

### Comet-H concepts that JG subsumes identically

Every Comet-H concept has a direct JG representation. One can implement
Comet-H *inside* JG by:

1. Defining a site with 6 coordinates: Theory, Repository, Paper,
   Evidence, Utility, Obligations
2. Defining morphisms for the drift edges (theory↔code, code↔evidence,
   evidence↔claims, theory↔claims)
3. Representing prompt families as semantic moves with typed
   preconditions
4. Representing the obligation vector as 5 judgment objects with
   exponential-decay evidence
5. Representing the grounding trigger as a descent-obligation generator
   on the paper↔evidence overlap
6. Representing mode transitions as phase-management rules

This is a **faithful embedding** of Comet-H into JG — every Comet-H
execution trace is a valid JG execution trace.

### JG capabilities with no Comet-H analogue

1. **Internal structure of surfaces**: Comet-H treats "code" as a
   single opaque surface. JG decomposes it into coordinates (modules,
   functions, branches), each with its own propositions, evidence, and
   trust. This enables fine-grained reasoning about *which part* of the
   code drifted from *which part* of the theory.

2. **Multi-channel evidence with trust algebra**: Comet-H has a binary
   audit model (grounded or not). JG has a lattice of trust levels with
   conservative composition, explicit promotion, and challenge
   mechanisms. This enables nuanced statements like "the core algorithm
   is solver-verified, the IO handling is test-witnessed, and the
   benchmarks are copilot-suggested."

3. **Persistent obstructions**: When Comet-H's audit finds a problem,
   the response is "run another prompt." The obstruction doesn't persist
   as a queryable object. In JG, obstructions are first-class persistent
   objects with cohomology class, repair frontier, blast radius, and
   downstream dependencies. They can be queried, transported, and
   systematically repaired.

4. **Treaty negotiation**: Comet-H has no mechanism for two subsystems
   to negotiate an interface agreement. If the paper and the code
   disagree, the response is "audit and hope the LM fixes it." In JG,
   the treaty negotiation machinery finds the minimal consistent
   interface between conflicting sections.

5. **Hierarchical structure**: Comet-H operates at one level (the
   workspace). JG's hierarchical site supports verification at function,
   module, package, and project levels, with descent composing across
   levels.

6. **Ideation beyond prompt selection**: Comet-H selects from a fixed
   prompt alphabet. JG's ideation engine discovers *new propositions*
   (via novelty search, analogy transport, cross-domain synthesis),
   evaluates their feasibility, and injects them into the judgment
   sheaf. This is not prompt selection — it's theorem discovery.

7. **Convergence guarantees**: Comet-H proves pressure is bounded but
   does not prove convergence of the overall process. JG's convergence
   monitor can issue convergence certificates (the obligation count is
   monotonically decreasing under the current control law) or detect
   divergence and trigger recovery.

---

## C3. Why JG Is More Theoretically Elegant

### Comet-H's formalism is ad-hoc in specific ways

1. **The choice of 5 obligation dimensions is arbitrary.** Why 5? Why
   capability/structural/benchmark/documentation/grounding and not
   other categories? In JG, obligations are typed by the propositions
   they discharge — the number and kind of obligation types is
   determined by the codebase, not hardcoded.

2. **The decay rate λ = 2^{-1/8} is a magic constant.** Why this
   half-life? In JG, evidence freshness decays based on the actual
   relationship between the evidence and the code: unchanged code
   preserves its evidence, changed code invalidates it. No magic
   constant needed.

3. **The grounding trigger is a special mechanism.** It's a
   special-purpose rule bolted onto the controller. In JG, re-verification
   after changes is the *normal operation* of the sheaf condition — it
   doesn't need a special trigger because descent checking is the
   fundamental operation.

4. **The adjacency constraint is a list of rules.** Five manual rules
   that a spec revision must satisfy. In JG, adjacency is a
   *cover refinement morphism* — a single mathematical concept that
   subsumes all five rules and handles cases the rules don't cover.

5. **Audit projection is defined only on the public claim lattice.**
   Comet-H's Π_W operates on a finite set of claims. JG's public
   alignment operates on the full judgment sheaf — it can project any
   subset of the internal state to any audience, with trust attenuation
   appropriate to the audience.

### JG's formalism is canonical in algebraic geometry

Every JG concept has a standard mathematical pedigree:

- **Site**: Grothendieck topology (SGA 4)
- **Sheaf**: functor from site^op to Sets satisfying descent (Artin,
  Grothendieck)
- **Descent**: the gluing axiom for sheaves (Grothendieck, 1960s)
- **Obstruction**: Čech cohomology class (Čech, Leray, Serre)
- **Cover refinement**: standard morphism of covering families
- **Trust algebra**: ordered algebra with conservative composition
  (lattice theory)
- **Certificate**: faithful functor from judgment category to evidence
  category

None of these are ad-hoc definitions — they are standard tools of
algebraic geometry, repurposed for program verification. The entire
JG framework is a *single* mathematical construction (a sheaf on a site)
from which all capabilities derive.

Comet-H, by contrast, is an *engineering design* — effective, practical,
and well-motivated, but not derived from a single mathematical principle.
Each mechanism (obligation vector, decay, trigger, adjacency, audit
projection) is a separate design choice. JG subsumes all of them as
consequences of one construction.

---

## C4. How JG Would Implement Comet-H's Pipeline

A concrete JG implementation of the full Comet-H pipeline:

### Site construction

```
Coordinates:
  thesis.T          — the mathematical theory
  repo.R.module.*   — every file and function in the repository
  paper.P.section.* — every section and claim in the paper
  evidence.E.*      — benchmark results, grounding ledger entries
  utility.U         — the utility hypothesis
  obligation.O.*    — typed obligations (not a flat vector)

Morphisms:
  thesis.T → repo.R.*         (theory-code coupling)
  repo.R.* → evidence.E.*     (code-evidence coupling)
  evidence.E.* → paper.P.*    (evidence-claim coupling)
  thesis.T → paper.P.*        (theory-claim coupling)

Covering families:
  {thesis.T, repo.R, paper.P, evidence.E} covers the workspace
  Each has sub-covers at finer granularity
```

### Prompt families as semantic moves

Each Comet-H prompt q_i becomes a JG semantic move with typed
preconditions and effects:

- **q₂ (Ideation)** → `IdeationMove(regime=EXPLORATION)` —
  generates thesis candidates at thesis.T coordinates
- **q₄ (Seed generation)** → `ConstructionMove(target=repo.R)` —
  constructs initial code sections with obligation generation
- **q₉ (Grounding)** → `EvidenceMove(channel=GROUNDING)` —
  constructs evidence sections at evidence.E coordinates
- **q₁₀ (Audit)** → `DescentCheck(overlap=paper∩evidence)` —
  verifies descent between paper and evidence sections

### Obligation tracking

Instead of a 5-dimensional decaying vector, each obligation is a
judgment:

```
Obligation(
  coordinate = "repo.R.module.auth.login",
  proposition = "benchmark coverage ≥ 80%",
  trust = PROPOSED,
  evidence = {},
  status = PENDING,
  created_at = t,
  support = {coordinates that this obligation covers}
)
```

Decay is support-aware: if the code at the obligation's coordinate
hasn't changed, the obligation doesn't decay. If the code changes,
the obligation is re-evaluated (not decayed — re-assessed).

### Grounding trigger as descent obligation

When any section at paper.P.* changes, the descent condition
automatically generates a verification obligation for every overlap
between paper.P and evidence.E. This is not a special trigger — it's
the normal operation of the sheaf condition. The orchestrator
schedules these verification obligations according to priority.

### The full loop

```
1. Ideation discovers candidate propositions (JG ideation engine)
2. Orchestrator selects the highest-priority move (JG frontier scorer)
3. Move executes: constructs or modifies a local section
4. Descent checker verifies all affected overlaps
5. If descent fails: obstruction generated with repair frontier
6. If descent succeeds: certificate updated, trust levels adjusted
7. Convergence monitor checks progress
8. If converged: emit final certificates
9. If stalled: switch phase (exploration → recovery → exploitation)
10. Repeat from 1
```

This is Comet-H's loop, but with:
- Fine-grained coordinate tracking instead of opaque surfaces
- Trust algebra instead of binary audit
- Persistent obstructions instead of "run another prompt"
- Treaty negotiation instead of "hope the LM resolves conflicts"
- Convergence certification instead of "budget exhaustion"

---

## C5. What JG Adds Beyond Comet-H

The deepest addition is that JG turns Comet-H from an **engineering
control loop** into a **mathematical theory of co-evolution**.

1. **Co-evolution drift becomes a computable invariant.** In Comet-H,
   drift is detected by running audit prompts and hoping the LM notices
   inconsistencies. In JG, drift is a descent failure that can be
   precisely located, classified, quantified, and repaired.

2. **The specification-under-construction problem has a mathematical
   solution.** Comet-H treats theory mutability as a design principle
   enforced by adjacency rules. JG treats it as cover refinement — a
   universal construction in algebraic geometry that automatically
   preserves existing evidence while allowing the specification to
   evolve.

3. **Multi-agent orchestration is fleet competition.** Comet-H uses
   a single LM with prompt selection. JG's fleet competition allows
   multiple LMs (or multiple strategies from the same LM) to propose
   sections simultaneously, with trust-aware bidding and
   evidence-based selection.

4. **Quality is not just "grounded or not."** Comet-H's quality model
   is binary: a claim is either grounded or it isn't. JG's trust
   algebra provides a rich lattice: VERIFIED_PROOF > SOLVER_DISCHARGED >
   RUNTIME_WITNESSED > COPILOT_SUGGESTED > PROPOSED > UNTRUSTED.
   This enables graduated quality targets: "the core algorithm should
   be SOLVER_DISCHARGED; the documentation should be RUNTIME_WITNESSED;
   the benchmarks should be VERIFIED_PROOF."

5. **The 46-repository portfolio is a site, not a list.** Comet-H
   treats its 46 repositories as independent outputs. In JG, they
   would be a site with cross-repository morphisms (shared libraries,
   analogous algorithms, common design patterns), enabling
   cross-repository descent (a theorem proven in repo A helps verify
   code in repo B via analogy transport).

6. **The process doesn't stop at budget exhaustion.** Comet-H halts
   after a fixed tail sequence. JG's cyclic maturity model runs
   indefinitely: each cycle improves the verification state, emits
   better certificates, and discovers new propositions. There is no
   fixed halting point — there is convergence to a fixed point of
   the verification functor.
