"""Comprehensive tests for the JuGeo solver scaling infrastructure.

Covers:
- QueryNormalizer: normalise SMT-LIB strings to canonical form, hashing
- DeduplicationCache: store/check results, batch dedup, eviction, serialization
- FragmentClassifier: classify QF_LIA, QF_LRA, QF_BV, QF_UF, QUANTIFIED, etc.
- QueryBatcher: add queries, flush, grouping by fragment, batch size limits
- SessionPool: acquire/release, reset threshold, eviction, drain, shutdown
- SolverRouter: full pipeline with dedup, batching, simulated solver
- SolverResultCache: put/get, invalidation by coordinate, persistence, pruning
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any
import sys

import pytest

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.scaling.solver.models import (
    DeduplicationResult,
    QueryBatch,
    QueryStatus,
    SessionInfo,
    SessionState,
    SolverFragment,
    SolverPoolConfig,
    SolverQuery,
    SolverResult,
    SolverStatistics,
)
from jugeo.scaling.solver.query_dedup import DeduplicationCache, QueryNormalizer
from jugeo.scaling.solver.fragment_batcher import FragmentClassifier, QueryBatcher
from jugeo.scaling.solver.session_lifecycle import SessionPool
from jugeo.scaling.solver.result_cache import SolverResultCache
from jugeo.scaling.solver.solver_router import SolverRouter


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _query(
    smt: str = "(assert (> x 0))",
    fragment: SolverFragment = SolverFragment.UNKNOWN,
    **kwargs: Any,
) -> SolverQuery:
    return SolverQuery.create(smt_text=smt, fragment=fragment, **kwargs)


def _result(
    query_id: str = "test-id",
    status: QueryStatus = QueryStatus.SAT,
    duration_ms: float = 10.0,
) -> SolverResult:
    return SolverResult.create(
        query_id=query_id,
        status=status,
        duration_ms=duration_ms,
        solver_version="test-1.0",
        session_id="sess-1",
    )


def _pool(max_sessions: int = 4, reset_threshold: int = 500) -> SessionPool:
    cfg = SolverPoolConfig(max_sessions=max_sessions, session_reset_threshold=reset_threshold)
    return SessionPool(cfg)


# ============================================================================
# Models round-trip tests
# ============================================================================

class TestModels:

    def test_solver_fragment_round_trip(self) -> None:
        for frag in SolverFragment:
            assert SolverFragment.from_dict(frag.to_dict()) is frag

    def test_query_status_round_trip(self) -> None:
        for status in QueryStatus:
            assert QueryStatus.from_dict(status.to_dict()) is status

    def test_session_state_round_trip(self) -> None:
        for state in SessionState:
            assert SessionState.from_dict(state.to_dict()) is state

    def test_solver_query_round_trip(self) -> None:
        q = _query(smt="(assert (= x 1))", fragment=SolverFragment.QF_LIA)
        d = q.to_dict()
        q2 = SolverQuery.from_dict(d)
        assert q2.id == q.id
        assert q2.smt_text == q.smt_text
        assert q2.fragment is q.fragment
        assert q2.content_hash == q.content_hash

    def test_solver_query_content_hash_set_on_create(self) -> None:
        q = _query(smt="(assert (> x 0))")
        expected = hashlib.sha256("(assert (> x 0))".encode()).hexdigest()
        assert q.content_hash == expected

    def test_solver_result_round_trip(self) -> None:
        r = _result(query_id="qid-1", status=QueryStatus.UNSAT)
        d = r.to_dict()
        r2 = SolverResult.from_dict(d)
        assert r2.query_id == r.query_id
        assert r2.status is QueryStatus.UNSAT
        assert r2.cached == r.cached

    def test_session_info_round_trip(self) -> None:
        s = SessionInfo.create(fragment=SolverFragment.QF_LIA)
        d = s.to_dict()
        s2 = SessionInfo.from_dict(d)
        assert s2.id == s.id
        assert s2.fragment is SolverFragment.QF_LIA
        assert s2.state is SessionState.FRESH

    def test_query_batch_round_trip(self) -> None:
        queries = [_query() for _ in range(3)]
        batch = QueryBatch.create(fragment=SolverFragment.QF_LIA, queries=queries)
        d = batch.to_dict()
        b2 = QueryBatch.from_dict(d)
        assert b2.id == batch.id
        assert len(b2.queries) == 3

    def test_dedup_result_round_trip(self) -> None:
        dr = DeduplicationResult.create(
            original_query_id="orig",
            duplicate_query_ids=["dup1", "dup2"],
            cache_hit=True,
        )
        d = dr.to_dict()
        dr2 = DeduplicationResult.from_dict(d)
        assert dr2.original_query_id == "orig"
        assert dr2.cache_hit is True
        assert "dup2" in dr2.duplicate_query_ids

    def test_solver_pool_config_round_trip(self) -> None:
        cfg = SolverPoolConfig(max_sessions=16, batch_size=100)
        d = cfg.to_dict()
        cfg2 = SolverPoolConfig.from_dict(d)
        assert cfg2.max_sessions == 16
        assert cfg2.batch_size == 100

    def test_solver_statistics_cache_hit_rate(self) -> None:
        stats = SolverStatistics(total_queries=10, cache_hits=4)
        assert stats.cache_hit_rate == pytest.approx(0.4)

    def test_solver_statistics_round_trip(self) -> None:
        stats = SolverStatistics(total_queries=20, cache_hits=5, dedup_hits=3)
        d = stats.to_dict()
        s2 = SolverStatistics.from_dict(d)
        assert s2.total_queries == 20
        assert s2.cache_hits == 5

    def test_solver_query_defaults(self) -> None:
        q = SolverQuery.create(smt_text="(check-sat)")
        assert q.timeout_ms == 30_000
        assert q.priority == pytest.approx(1.0)
        assert q.depends_on == []
        assert q.metadata == {}


# ============================================================================
# QueryNormalizer tests
# ============================================================================

class TestQueryNormalizer:

    def setup_method(self) -> None:
        self.norm = QueryNormalizer()

    def test_strip_line_comments(self) -> None:
        text = "; this is a comment\n(assert (> x 0))"
        result = self.norm._strip_comments(text)
        assert "comment" not in result
        assert "(assert" in result

    def test_strip_block_comments(self) -> None:
        text = "#| block comment |# (assert (= x 1))"
        result = self.norm._strip_comments(text)
        assert "block" not in result
        assert "(assert" in result

    def test_normalize_whitespace_collapses_spaces(self) -> None:
        text = "(assert   (>   x   0))"
        result = self.norm._normalize_whitespace(text)
        assert "  " not in result

    def test_normalize_whitespace_strips(self) -> None:
        text = "   (assert (= x 1))   "
        result = self.norm._normalize_whitespace(text)
        assert result == result.strip()

    def test_alpha_rename_bound_variable(self) -> None:
        text = "(forall ((myvar Int)) (> myvar 0))"
        result = self.norm._alpha_rename(text)
        # The bound variable should be renamed to x0
        assert "myvar" not in result
        assert "x0" in result

    def test_sort_commutative_and(self) -> None:
        text = "(and B A)"
        result = self.norm._sort_commutative(text)
        # A should come before B lexicographically
        assert result.index("A") < result.index("B")

    def test_sort_commutative_or(self) -> None:
        text = "(or Z A M)"
        result = self.norm._sort_commutative(text)
        parts = result.strip("()").split()
        # First token is 'or', rest should be sorted
        assert parts[0] == "or"
        assert parts[1:] == sorted(parts[1:])

    def test_sort_commutative_plus(self) -> None:
        text = "(+ c b a)"
        result = self.norm._sort_commutative(text)
        inner = result[1:-1].split()
        assert inner[0] == "+"
        assert inner[1:] == sorted(inner[1:])

    def test_identical_content_same_hash(self) -> None:
        t1 = "(assert (> x 0))"
        t2 = "(assert   (> x  0))  ; comment"
        h1 = self.norm.content_hash(t1)
        h2 = self.norm.content_hash(t2)
        assert h1 == h2

    def test_different_content_different_hash(self) -> None:
        h1 = self.norm.content_hash("(assert (> x 0))")
        h2 = self.norm.content_hash("(assert (> y 0))")
        assert h1 != h2

    def test_commutative_normalization_produces_same_hash(self) -> None:
        t1 = "(and A B)"
        t2 = "(and B A)"
        h1 = self.norm.content_hash(t1)
        h2 = self.norm.content_hash(t2)
        assert h1 == h2

    def test_normalize_pipeline(self) -> None:
        text = "; comment\n(assert   (and B A))"
        result = self.norm.normalize(text)
        assert "comment" not in result
        assert "  " not in result

    def test_content_hash_is_sha256_hex(self) -> None:
        h = self.norm.content_hash("(assert true)")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_string(self) -> None:
        result = self.norm.normalize("")
        assert result == ""
        h = self.norm.content_hash("")
        assert len(h) == 64


# ============================================================================
# DeduplicationCache tests
# ============================================================================

class TestDeduplicationCache:

    def setup_method(self) -> None:
        self.cache = DeduplicationCache(max_entries=100)

    def test_miss_on_empty_cache(self) -> None:
        q = _query()
        assert self.cache.check(q) is None

    def test_store_and_hit(self) -> None:
        q = _query(smt="(assert (= x 1))")
        r = _result(query_id=q.id)
        self.cache.store(q, r)
        hit = self.cache.check(q)
        assert hit is not None
        assert hit.status is QueryStatus.SAT
        assert hit.cached is True

    def test_cache_hit_preserves_status(self) -> None:
        q = _query()
        r = _result(query_id=q.id, status=QueryStatus.UNSAT)
        self.cache.store(q, r)
        hit = self.cache.check(q)
        assert hit is not None
        assert hit.status is QueryStatus.UNSAT

    def test_cache_hit_updates_query_id(self) -> None:
        q1 = _query(smt="(assert (= x 1))")
        r = _result(query_id=q1.id)
        self.cache.store(q1, r)
        # Same content, different query id
        q2 = SolverQuery.create(smt_text="(assert (= x 1))")
        hit = self.cache.check(q2)
        assert hit is not None
        assert hit.query_id == q2.id

    def test_deduplicate_batch_removes_duplicates(self) -> None:
        smt = "(assert (> x 0))"
        queries = [_query(smt=smt) for _ in range(5)]
        unique, dedup_results = self.cache.deduplicate_batch(queries)
        assert len(unique) == 1
        assert len(dedup_results) == 1
        assert len(dedup_results[0].duplicate_query_ids) == 4

    def test_deduplicate_batch_no_duplicates(self) -> None:
        queries = [_query(smt=f"(assert (= x {i}))") for i in range(5)]
        unique, dedup_results = self.cache.deduplicate_batch(queries)
        assert len(unique) == 5
        assert dedup_results == []

    def test_evict_lru(self) -> None:
        for i in range(10):
            q = _query(smt=f"(assert (= x {i}))")
            r = _result(query_id=q.id)
            self.cache.store(q, r)
        assert len(self.cache) == 10
        self.cache.evict_lru(5)
        assert len(self.cache) == 5

    def test_max_entries_enforced(self) -> None:
        cache = DeduplicationCache(max_entries=5)
        for i in range(10):
            q = _query(smt=f"(assert (= x {i}))")
            r = _result(query_id=q.id)
            cache.store(q, r)
        assert len(cache) <= 5

    def test_clear(self) -> None:
        q = _query()
        r = _result(query_id=q.id)
        self.cache.store(q, r)
        self.cache.clear()
        assert len(self.cache) == 0
        assert self.cache.check(q) is None

    def test_statistics_hit_rate(self) -> None:
        q = _query()
        r = _result(query_id=q.id)
        self.cache.store(q, r)
        # One hit
        self.cache.check(q)
        # One miss
        self.cache.check(_query(smt="(assert false)"))
        stats = self.cache.statistics()
        assert stats["hit_rate"] == pytest.approx(0.5)
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_serialize_and_load(self) -> None:
        for i in range(3):
            q = _query(smt=f"(assert (= y {i}))")
            r = _result(query_id=q.id)
            self.cache.store(q, r)
        data = self.cache.serialize()
        cache2 = DeduplicationCache(max_entries=100)
        cache2.load(data)
        assert len(cache2) == 3
        assert cache2.statistics()["stores"] == 3


# ============================================================================
# FragmentClassifier tests
# ============================================================================

class TestFragmentClassifier:

    def setup_method(self) -> None:
        self.clf = FragmentClassifier()

    def test_qf_lia_detect_int(self) -> None:
        smt = "(declare-const x Int) (assert (> x 0))"
        assert self.clf.classify(smt) is SolverFragment.QF_LIA

    def test_qf_lia_detect_div_mod(self) -> None:
        smt = "(assert (= (div x 2) 0))"
        assert self.clf.classify(smt) is SolverFragment.QF_LIA

    def test_qf_lra_detect_real(self) -> None:
        smt = "(declare-const x Real) (assert (> x 0.5))"
        assert self.clf.classify(smt) is SolverFragment.QF_LRA

    def test_qf_bv_detect_bitvec(self) -> None:
        smt = "(declare-const x (_ BitVec 32)) (assert (bvadd x #x00000001))"
        assert self.clf.classify(smt) is SolverFragment.QF_BV

    def test_qf_uf_detect_declare_fun(self) -> None:
        smt = "(declare-fun f (Int) Int) (assert (= (f 0) 1))"
        assert self.clf.classify(smt) is SolverFragment.QF_UF

    def test_quantified_forall(self) -> None:
        smt = "(assert (forall ((x Int)) (> x 0)))"
        assert self.clf.classify(smt) is SolverFragment.QUANTIFIED

    def test_quantified_exists(self) -> None:
        smt = "(assert (exists ((x Int)) (= x 0)))"
        assert self.clf.classify(smt) is SolverFragment.QUANTIFIED

    def test_qf_auflia_array_with_int(self) -> None:
        smt = "(declare-const a (Array Int Int)) (assert (= (select a 0) 1))"
        assert self.clf.classify(smt) is SolverFragment.QF_AUFLIA

    def test_unknown_no_keywords(self) -> None:
        smt = "(assert true)"
        assert self.clf.classify(smt) is SolverFragment.UNKNOWN

    def test_batch_classify_groups_correctly(self) -> None:
        queries = [
            _query(smt="(declare-const x Int) (assert (> x 0))"),
            _query(smt="(declare-const y Real) (assert (< y 1.0))"),
            _query(smt="(declare-const z Int) (assert (> z 1))"),
        ]
        groups = self.clf.batch_classify(queries)
        assert SolverFragment.QF_LIA in groups
        assert SolverFragment.QF_LRA in groups
        assert len(groups[SolverFragment.QF_LIA]) == 2
        assert len(groups[SolverFragment.QF_LRA]) == 1

    def test_horn_classify(self) -> None:
        smt = "(rule (=> (P x) (Q x)))"
        assert self.clf.classify(smt) is SolverFragment.HORN

    def test_has_quantifiers_true(self) -> None:
        assert self.clf._has_quantifiers("(forall ((x Int)) (> x 0))")

    def test_has_quantifiers_false(self) -> None:
        assert not self.clf._has_quantifiers("(assert (= x 0))")

    def test_has_bitvectors_true(self) -> None:
        assert self.clf._has_bitvectors("(bvadd x y)")

    def test_has_arrays_true(self) -> None:
        assert self.clf._has_arrays("(select a 0)")


# ============================================================================
# QueryBatcher tests
# ============================================================================

class TestQueryBatcher:

    def setup_method(self) -> None:
        self.batcher = QueryBatcher(batch_size=3)

    def test_add_and_pending_count(self) -> None:
        q = _query(fragment=SolverFragment.QF_LIA)
        self.batcher.add_query(q)
        assert self.batcher.pending_count() == 1

    def test_flush_returns_batches(self) -> None:
        for _ in range(3):
            self.batcher.add_query(_query(fragment=SolverFragment.QF_LIA))
        batches = self.batcher.flush()
        assert len(batches) == 1
        assert len(batches[0].queries) == 3

    def test_flush_clears_pending(self) -> None:
        self.batcher.add_query(_query(fragment=SolverFragment.QF_LIA))
        self.batcher.flush()
        assert self.batcher.pending_count() == 0

    def test_groups_by_fragment(self) -> None:
        self.batcher.add_query(_query(smt="(declare-const x Int) (assert (> x 0))", fragment=SolverFragment.UNKNOWN))
        self.batcher.add_query(_query(smt="(declare-const y Real) (assert (> y 0))", fragment=SolverFragment.UNKNOWN))
        self.batcher.add_query(_query(smt="(declare-const z Int) (assert (= z 1))", fragment=SolverFragment.UNKNOWN))
        batches = self.batcher.flush()
        fragments = {b.fragment for b in batches}
        assert SolverFragment.QF_LIA in fragments or SolverFragment.UNKNOWN in fragments

    def test_batch_size_respected(self) -> None:
        batcher = QueryBatcher(batch_size=2)
        for _ in range(5):
            batcher.add_query(_query(fragment=SolverFragment.QF_LIA))
        batches = batcher.flush()
        # 5 queries with batch_size=2 → 3 batches (2+2+1)
        assert len(batches) == 3
        assert sum(len(b.queries) for b in batches) == 5

    def test_pending_by_fragment(self) -> None:
        self.batcher.add_query(_query(fragment=SolverFragment.QF_LIA))
        self.batcher.add_query(_query(fragment=SolverFragment.QF_LIA))
        self.batcher.add_query(_query(fragment=SolverFragment.QF_LRA))
        pf = self.batcher.pending_by_fragment()
        assert pf.get(SolverFragment.QF_LIA.value, 0) == 2
        assert pf.get(SolverFragment.QF_LRA.value, 0) == 1

    def test_empty_flush_returns_no_batches(self) -> None:
        batches = self.batcher.flush()
        assert batches == []

    def test_batch_has_correct_fragment(self) -> None:
        for _ in range(2):
            self.batcher.add_query(_query(fragment=SolverFragment.QF_BV))
        batches = self.batcher.flush()
        assert all(b.fragment is SolverFragment.QF_BV for b in batches)

    def test_statistics_tracks_totals(self) -> None:
        self.batcher.add_query(_query(fragment=SolverFragment.QF_LIA))
        self.batcher.flush()
        stats = self.batcher.statistics()
        assert stats["total_added"] == 1
        assert stats["total_flushed"] == 1


# ============================================================================
# SessionPool tests
# ============================================================================

class TestSessionPool:

    def test_acquire_creates_session(self) -> None:
        pool = _pool()
        session = pool.acquire()
        assert session.id
        assert session.state is SessionState.ACTIVE

    def test_acquire_and_release(self) -> None:
        pool = _pool()
        session = pool.acquire()
        pool.release(session.id)
        stats = pool.statistics()
        assert stats["idle"] == 1
        assert stats["active"] == 0

    def test_acquire_prefers_matching_fragment(self) -> None:
        pool = _pool(max_sessions=4)
        # Create a session for QF_LIA
        s1 = pool.acquire(fragment=SolverFragment.QF_LIA)
        pool.release(s1.id)
        # Now acquire for QF_LIA — should get s1 back
        s2 = pool.acquire(fragment=SolverFragment.QF_LIA)
        assert s2.id == s1.id

    def test_acquire_falls_back_to_any_idle(self) -> None:
        pool = _pool(max_sessions=4)
        s1 = pool.acquire(fragment=SolverFragment.QF_LIA)
        pool.release(s1.id)
        # Request a different fragment — no perfect match, gets existing idle session
        s2 = pool.acquire(fragment=SolverFragment.QF_LRA)
        assert s2 is not None

    def test_pool_at_capacity_evicts_oldest(self) -> None:
        pool = _pool(max_sessions=2)
        s1 = pool.acquire()
        pool.release(s1.id)
        s2 = pool.acquire()
        pool.release(s2.id)
        # Both are idle, pool full — acquire should still work (evict + create)
        s3 = pool.acquire()
        assert s3 is not None

    def test_session_health(self) -> None:
        pool = _pool()
        session = pool.acquire()
        health = pool.session_health(session.id)
        assert health["session_id"] == session.id
        assert health["is_active"] is True
        pool.release(session.id)

    def test_active_sessions_listing(self) -> None:
        pool = _pool()
        s = pool.acquire()
        sessions = pool.active_sessions()
        ids = [sess.id for sess in sessions]
        assert s.id in ids

    def test_statistics_counts(self) -> None:
        pool = _pool()
        s = pool.acquire()
        stats = pool.statistics()
        assert stats["active"] == 1
        assert stats["sessions_created"] >= 1
        pool.release(s.id)
        stats2 = pool.statistics()
        assert stats2["idle"] == 1

    def test_reset_on_release_when_threshold_exceeded(self) -> None:
        pool = _pool(reset_threshold=2)
        session = pool.acquire()
        session.queries_served = 3  # exceed threshold
        pool.release(session.id)
        stats = pool.statistics()
        assert stats["sessions_reset"] >= 1

    def test_drain_blocks_new_acquires(self) -> None:
        pool = _pool()
        pool.drain()
        with pytest.raises(RuntimeError, match="draining"):
            pool.acquire()

    def test_shutdown_clears_all(self) -> None:
        pool = _pool()
        pool.acquire()
        pool.shutdown()
        stats = pool.statistics()
        assert stats["total_sessions"] == 0

    def test_release_unknown_session_is_noop(self) -> None:
        pool = _pool()
        # Should not raise
        pool.release("nonexistent-id")

    def test_session_health_unknown_raises(self) -> None:
        pool = _pool()
        with pytest.raises(KeyError):
            pool.session_health("nonexistent")


# ============================================================================
# SolverRouter tests
# ============================================================================

class TestSolverRouter:

    def _make_router(
        self,
        max_sessions: int = 4,
        dedup_enabled: bool = True,
        cache_enabled: bool = True,
    ) -> SolverRouter:
        cfg = SolverPoolConfig(
            max_sessions=max_sessions,
            dedup_enabled=dedup_enabled,
            cache_enabled=cache_enabled,
        )
        pool = SessionPool(cfg)
        dedup = DeduplicationCache(max_entries=1000)
        batcher = QueryBatcher(batch_size=10)
        return SolverRouter(pool=pool, dedup=dedup, batcher=batcher, config=cfg)

    def test_submit_returns_query_id(self) -> None:
        router = self._make_router()
        q = _query(smt="(assert (= x 1))", fragment=SolverFragment.QF_LIA)
        qid = router.submit(q)
        assert qid == q.id

    def test_get_result_after_submit(self) -> None:
        router = self._make_router()
        q = _query(smt="(assert (= x 2))", fragment=SolverFragment.QF_LIA)
        router.submit(q)
        result = router.get_result(q.id)
        assert result is not None
        assert result.query_id == q.id

    def test_result_status_is_valid(self) -> None:
        router = self._make_router()
        q = _query(fragment=SolverFragment.QF_LIA)
        router.submit(q)
        result = router.get_result(q.id)
        assert result.status in QueryStatus

    def test_second_submit_same_query_is_cached(self) -> None:
        router = self._make_router(dedup_enabled=True)
        smt = "(assert (> x 10))"
        q1 = _query(smt=smt, fragment=SolverFragment.QF_LIA)
        q2 = SolverQuery.create(smt_text=smt, fragment=SolverFragment.QF_LIA)

        router.submit(q1)
        router.submit(q2)
        result2 = router.get_result(q2.id)
        assert result2 is not None
        assert result2.cached is True

    def test_submit_batch_returns_all_ids(self) -> None:
        router = self._make_router()
        queries = [_query(smt=f"(assert (= x {i}))", fragment=SolverFragment.QF_LIA) for i in range(5)]
        ids = router.submit_batch(queries)
        assert len(ids) == 5
        assert all(qid in ids for qid in [q.id for q in queries])

    def test_submit_batch_deduplicates_internally(self) -> None:
        router = self._make_router()
        smt = "(assert (= y 42))"
        queries = [SolverQuery.create(smt_text=smt, fragment=SolverFragment.QF_LIA) for _ in range(3)]
        ids = router.submit_batch(queries)
        # All should have results
        for qid in ids:
            result = router.get_result(qid)
            assert result is not None

    def test_flush_pending_processes_queries(self) -> None:
        router = self._make_router()
        q = _query(fragment=SolverFragment.QF_LIA)
        # Manually add to batcher without routing
        router._batcher.add_query(q)
        router.flush_pending()
        result = router.get_result(q.id)
        assert result is not None

    def test_statistics_counts_total_queries(self) -> None:
        router = self._make_router()
        for i in range(3):
            router.submit(_query(smt=f"(assert (= x {i}))", fragment=SolverFragment.QF_LIA))
        stats = router.statistics()
        assert stats.total_queries >= 3

    def test_statistics_cache_hits(self) -> None:
        router = self._make_router(dedup_enabled=True)
        smt = "(assert (> x 100))"
        q1 = _query(smt=smt, fragment=SolverFragment.QF_LIA)
        q2 = SolverQuery.create(smt_text=smt, fragment=SolverFragment.QF_LIA)
        router.submit(q1)
        router.submit(q2)
        stats = router.statistics()
        assert stats.cache_hits >= 1

    def test_get_result_unknown_id_returns_none(self) -> None:
        router = self._make_router()
        assert router.get_result("nonexistent-id") is None

    def test_simulate_solve_deterministic(self) -> None:
        router = self._make_router()
        pool = router._pool
        q = _query(smt="(assert true)", fragment=SolverFragment.UNKNOWN)
        q.content_hash = hashlib.sha256(b"test").hexdigest()
        session = pool.acquire()
        r1 = router._simulate_solve(q, session)
        r2 = router._simulate_solve(q, session)
        assert r1.status == r2.status
        pool.release(session.id)

    def test_submit_batch_empty_returns_empty(self) -> None:
        router = self._make_router()
        assert router.submit_batch([]) == []

    def test_full_pipeline_with_classification(self) -> None:
        router = self._make_router()
        q = _query(smt="(declare-const x Int) (assert (> x 5))", fragment=SolverFragment.UNKNOWN)
        router.submit(q)
        result = router.get_result(q.id)
        assert result is not None
        # Fragment should have been classified
        assert q.fragment is not SolverFragment.UNKNOWN


# ============================================================================
# SolverResultCache tests
# ============================================================================

@pytest.fixture
def cache_dir(tmp_path: Path) -> str:
    return str(tmp_path / "solver_cache")


class TestSolverResultCache:

    def _make_cache(self, cache_dir: str, max_entries: int = 1000) -> SolverResultCache:
        return SolverResultCache(cache_dir=cache_dir, max_entries=max_entries)

    def test_miss_returns_none(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        assert cache.get("nonexistent-hash") is None

    def test_put_and_get(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "a" * 64
        r = _result(query_id="q1")
        cache.put(h, r)
        assert cache.get(h) is not None

    def test_get_version_mismatch_returns_none(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "b" * 64
        r = _result(query_id="q1")
        cache.put(h, r)
        assert cache.get(h, solver_version="different-version") is None

    def test_get_version_match_returns_result(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "c" * 64
        r = SolverResult.create(
            query_id="q1",
            status=QueryStatus.SAT,
            duration_ms=5.0,
            solver_version="z3-4.12",
            session_id="s1",
        )
        cache.put(h, r)
        assert cache.get(h, solver_version="z3-4.12") is not None

    def test_invalidate_by_coordinate_id(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "d" * 64
        r = SolverResult.create(
            query_id="q1",
            status=QueryStatus.SAT,
            duration_ms=5.0,
            session_id="s1",
            model={"coordinate_id": "coord-abc"},
        )
        cache.put(h, r)
        # Should be present before invalidation
        assert cache.get(h) is not None
        cache.invalidate(["coord-abc"])
        assert cache.get(h) is None

    def test_invalidate_all(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        for i in range(5):
            h = f"{i:0>64}"
            cache.put(h, _result(query_id=f"q{i}"))
        cache.invalidate_all()
        assert len(cache) == 0

    def test_max_entries_enforced(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir, max_entries=3)
        for i in range(6):
            h = hashlib.sha256(str(i).encode()).hexdigest()
            cache.put(h, _result(query_id=f"q{i}"))
        assert len(cache) <= 3

    def test_save_and_load_from_disk(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "e" * 64
        r = _result(query_id="q_persist")
        cache.put(h, r)
        cache.save_to_disk()

        cache2 = self._make_cache(cache_dir)
        cache2.load_from_disk()
        assert cache2.get(h) is not None

    def test_load_from_nonexistent_is_noop(self, cache_dir: str) -> None:
        cache = self._make_cache(os.path.join(cache_dir, "missing"))
        # Should not raise
        cache.load_from_disk()
        assert len(cache) == 0

    def test_prune_stale_removes_old_entries(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "f" * 64
        r = _result(query_id="q_old")
        cache.put(h, r)
        # Manually set the stored_at timestamp to be very old
        cache._store[h] = (r, time.time() - 40 * 86_400)
        removed = cache.prune_stale(max_age_days=30)
        assert removed >= 1
        assert cache.get(h) is None

    def test_prune_stale_keeps_recent(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "0" * 64
        r = _result(query_id="q_new")
        cache.put(h, r)
        removed = cache.prune_stale(max_age_days=30)
        assert removed == 0
        assert cache.get(h) is not None

    def test_size_on_disk_after_save(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        cache.put("a" * 64, _result(query_id="q1"))
        cache.save_to_disk()
        assert cache.size_on_disk() > 0

    def test_size_on_disk_no_dir(self, cache_dir: str) -> None:
        # A sub-path that has never been created → size should be 0
        absent = os.path.join(cache_dir, "never_created")
        cache = SolverResultCache(cache_dir=absent)
        assert cache.size_on_disk() == 0

    def test_statistics_reports_hits_and_misses(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        h = "1" * 64
        cache.put(h, _result(query_id="q1"))
        cache.get(h)           # hit
        cache.get("2" * 64)    # miss
        stats = cache.statistics()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(0.5)

    def test_statistics_includes_entries(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir)
        for i in range(3):
            cache.put(f"{i:0>64}", _result(query_id=f"q{i}"))
        stats = cache.statistics()
        assert stats["entries"] == 3

    def test_evict_lru_reduces_size(self, cache_dir: str) -> None:
        cache = self._make_cache(cache_dir, max_entries=100)
        for i in range(10):
            cache.put(hashlib.sha256(str(i).encode()).hexdigest(), _result(query_id=f"q{i}"))
        cache._evict_lru(5)
        assert len(cache) == 5
