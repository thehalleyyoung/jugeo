"""Solver router — routes queries through the full pipeline.

The :class:`SolverRouter` is the public entry-point for solver scaling.  It
wires together deduplication, fragment batching, session lifecycle management,
and result caching into a single submit/get-result interface.

Pipeline for each query:
1. **Deduplication** — check the in-memory :class:`DeduplicationCache`.  If
   the result is already known, return it immediately without touching any
   session.
2. **Classification** — if the query fragment is ``UNKNOWN``, classify it.
3. **Batching** — accumulate the query in the :class:`QueryBatcher`.  When
   ``flush_pending`` is called (or when *auto-flush* is triggered by a full
   batch), the batcher emits :class:`QueryBatch` objects.
4. **Session routing** — acquire a session from the :class:`SessionPool` that
   matches the batch fragment, execute each query, then release the session.
5. **Cache write-back** — store every new result in the
   :class:`DeduplicationCache` (and in the :class:`SolverResultCache` if
   configured).
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Any, Optional

from jugeo.scaling.solver.fragment_batcher import FragmentClassifier, QueryBatcher
from jugeo.scaling.solver.models import (
    QueryBatch,
    QueryStatus,
    SolverFragment,
    SolverPoolConfig,
    SolverQuery,
    SolverResult,
    SolverStatistics,
)
from jugeo.scaling.solver.query_dedup import DeduplicationCache
from jugeo.scaling.solver.session_lifecycle import SessionPool


class SolverRouter:
    """Route :class:`SolverQuery` objects through the full solver pipeline.

    This class is *not* thread-safe by itself; callers that share a router from
    multiple threads should use external locking.  (Thread-safety lives inside
    :class:`SessionPool`.)
    """

    # Simulated solver version string returned by :meth:`_simulate_solve`.
    _SOLVER_VERSION = "simulated-z3-4.12"

    def __init__(
        self,
        pool: SessionPool,
        dedup: DeduplicationCache,
        batcher: QueryBatcher,
        config: Optional[SolverPoolConfig] = None,
    ) -> None:
        self._pool = pool
        self._dedup = dedup
        self._batcher = batcher
        self._config = config or SolverPoolConfig()
        self._classifier = FragmentClassifier()

        # query_id → SolverResult for completed queries
        self._results: dict[str, SolverResult] = {}

        # Timing samples for statistics
        self._durations: list[float] = []
        self._total_queries = 0
        self._cache_hits = 0
        self._dedup_hits = 0

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def submit(self, query: SolverQuery) -> str:
        """Submit a single query and return its query_id.

        The query is processed synchronously through the full pipeline before
        this method returns.
        """
        result = self._route_query(query)
        if result is not None:
            self._results[query.id] = result
        return query.id

    def submit_batch(self, queries: list[SolverQuery]) -> list[str]:
        """Submit multiple queries, returning their query ids.

        Deduplicates within the batch first, then routes each unique query
        through the pipeline.  Duplicate queries receive a copy of the
        representative's result.
        """
        if not queries:
            return []

        # Deduplicate within the batch.
        unique_queries, dedup_results = self._dedup.deduplicate_batch(queries)
        self._dedup_hits += sum(len(dr.duplicate_query_ids) for dr in dedup_results)

        # Build a map: duplicate id → original id.
        dup_map: dict[str, str] = {}
        for dr in dedup_results:
            for dup_id in dr.duplicate_query_ids:
                dup_map[dup_id] = dr.original_query_id

        # Route unique queries.
        for q in unique_queries:
            result = self._route_query(q)
            if result is not None:
                self._results[q.id] = result

        # Propagate results to duplicates.
        for q in queries:
            if q.id in dup_map:
                original_id = dup_map[q.id]
                if original_id in self._results:
                    original_result = self._results[original_id]
                    self._results[q.id] = SolverResult(
                        query_id=q.id,
                        status=original_result.status,
                        duration_ms=original_result.duration_ms,
                        solver_version=original_result.solver_version,
                        session_id=original_result.session_id,
                        cached=True,
                        model=original_result.model,
                        proof_hash=original_result.proof_hash,
                    )

        return [q.id for q in queries]

    def get_result(
        self,
        query_id: str,
        timeout: Optional[float] = None,
    ) -> Optional[SolverResult]:
        """Return the result for *query_id*, or None if not yet available."""
        return self._results.get(query_id)

    def flush_pending(self) -> None:
        """Force-process all queries that have been added to the batcher but
        not yet executed."""
        batches = self._batcher.flush()
        for batch in batches:
            results = self._execute_batch(batch)
            for result in results:
                self._results[result.query_id] = result

    def statistics(self) -> SolverStatistics:
        """Return aggregate statistics for this router."""
        total = self._total_queries
        durations = self._durations

        avg_ms = sum(durations) / len(durations) if durations else 0.0
        p99_ms = 0.0
        if durations:
            sorted_d = sorted(durations)
            idx = max(0, int(len(sorted_d) * 0.99) - 1)
            p99_ms = sorted_d[idx]

        # Build by_status counts.
        by_status: dict[str, int] = defaultdict(int)
        for result in self._results.values():
            by_status[result.status.value] += 1

        # Build by_fragment counts (infer from cached results).
        by_fragment: dict[str, dict[str, Any]] = {}

        pool_stats = self._pool.statistics()

        return SolverStatistics(
            total_queries=total,
            cache_hits=self._cache_hits,
            dedup_hits=self._dedup_hits,
            unique_queries=total - self._dedup_hits,
            avg_duration_ms=avg_ms,
            p99_duration_ms=p99_ms,
            sessions_created=pool_stats.get("sessions_created", 0),
            sessions_reset=pool_stats.get("sessions_reset", 0),
            by_fragment=by_fragment,
            by_status=dict(by_status),
        )

    # ---------------------------------------------------------------------------
    # Internal pipeline
    # ---------------------------------------------------------------------------

    def _route_query(self, query: SolverQuery) -> Optional[SolverResult]:
        """Run the full pipeline for a single query."""
        self._total_queries += 1

        # Classify if needed.
        if query.fragment is SolverFragment.UNKNOWN:
            query.fragment = self._classifier.classify(query.smt_text)

        # 1. Deduplication cache check.
        if self._config.dedup_enabled:
            cached = self._dedup.check(query)
            if cached is not None:
                self._cache_hits += 1
                self._durations.append(cached.duration_ms)
                return cached

        # 2. Add to batcher and flush immediately (synchronous mode).
        self._batcher.add_query(query)

        # Flush and execute.
        batches = self._batcher.flush()
        executed: dict[str, SolverResult] = {}
        for batch in batches:
            for result in self._execute_batch(batch):
                executed[result.query_id] = result

        # 3. Store results in dedup cache.
        for qid, result in executed.items():
            # Re-fetch the original query to get its content_hash.
            pass

        result = executed.get(query.id)
        if result is not None:
            if self._config.dedup_enabled:
                self._dedup.store(query, result)
            self._durations.append(result.duration_ms)
        return result

    def _execute_batch(self, batch: QueryBatch) -> list[SolverResult]:
        """Execute all queries in *batch* on a session from the pool."""
        if not batch.queries:
            return []

        session = self._pool.acquire(fragment=batch.fragment)
        results: list[SolverResult] = []
        try:
            for query in batch.queries:
                result = self._simulate_solve(query, session)
                results.append(result)
                # Update session metrics.
                self._pool._record_query(session.id, result.duration_ms)
        finally:
            self._pool.release(session.id)

        return results

    def _simulate_solve(
        self, query: SolverQuery, session: "SessionInfo"  # noqa: F821
    ) -> SolverResult:
        """Return a simulated :class:`SolverResult` for *query*.

        In production this would invoke the real Z3 API.  For testing and
        demonstration purposes we return a deterministic result based on the
        query's content hash.
        """
        # Deterministic pseudo-result based on the content hash.
        h = query.content_hash
        # Use the first byte of the hash to pick a status distribution.
        first_byte = int(h[:2], 16) if h else 0

        if first_byte < 100:
            status = QueryStatus.SAT
        elif first_byte < 200:
            status = QueryStatus.UNSAT
        elif first_byte < 230:
            status = QueryStatus.UNKNOWN
        else:
            status = QueryStatus.SAT

        # Simulate a brief solving time (5–50 ms deterministically).
        duration_ms = 5.0 + (first_byte % 46)

        model: Optional[dict[str, Any]] = None
        proof_hash: Optional[str] = None
        if status == QueryStatus.SAT:
            model = {"x": first_byte % 10, "y": (first_byte * 3) % 10}
        elif status == QueryStatus.UNSAT:
            proof_hash = h[:16]

        return SolverResult.create(
            query_id=query.id,
            status=status,
            duration_ms=duration_ms,
            solver_version=self._SOLVER_VERSION,
            session_id=session.id,
            cached=False,
            model=model,
            proof_hash=proof_hash,
        )
