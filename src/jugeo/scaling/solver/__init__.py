"""Solver scaling sub-package for JuGeo.

Provides Z3 solver session lifecycle management, query deduplication,
fragment-based batching, and persistent result caching for large-scale
verification workloads.
"""

from __future__ import annotations

from jugeo.scaling.solver.fragment_batcher import FragmentClassifier, QueryBatcher
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
from jugeo.scaling.solver.result_cache import SolverResultCache
from jugeo.scaling.solver.session_lifecycle import SessionPool
from jugeo.scaling.solver.solver_router import SolverRouter

__all__ = [
    # models
    "SolverFragment",
    "QueryStatus",
    "SessionState",
    "SolverQuery",
    "SolverResult",
    "SessionInfo",
    "QueryBatch",
    "DeduplicationResult",
    "SolverPoolConfig",
    "SolverStatistics",
    # components
    "QueryNormalizer",
    "DeduplicationCache",
    "FragmentClassifier",
    "QueryBatcher",
    "SessionPool",
    "SolverRouter",
    "SolverResultCache",
]
