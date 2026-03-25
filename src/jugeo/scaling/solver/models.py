"""Solver scaling models for JuGeo.

Plain dataclasses and enums representing queries, results, sessions, and
configuration for the Z3 solver scaling infrastructure.  All models provide
``to_dict`` / ``from_dict`` round-trips and are fully standalone (no jugeo
imports required).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _uid() -> str:
    return str(uuid.uuid4())


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)


def _loads(text: str) -> Any:
    return json.loads(text)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SolverFragment(str, Enum):
    """SMT-LIB 2 logics / theory fragments."""

    QF_LIA = "QF_LIA"        # quantifier-free linear integer arithmetic
    QF_LRA = "QF_LRA"        # quantifier-free linear real arithmetic
    QF_BV = "QF_BV"          # quantifier-free bit-vectors
    QF_AUFLIA = "QF_AUFLIA"  # quantifier-free arrays + uninterpreted fns + LIA
    QF_UF = "QF_UF"          # quantifier-free uninterpreted functions
    HORN = "HORN"             # constrained Horn clauses
    QUANTIFIED = "QUANTIFIED" # first-order logic with quantifiers
    MIXED = "MIXED"           # combination of multiple theories
    UNKNOWN = "UNKNOWN"       # could not be determined

    def to_dict(self) -> str:
        return self.value

    @classmethod
    def from_dict(cls, value: str) -> SolverFragment:
        return cls(value)


class QueryStatus(str, Enum):
    """Lifecycle status of a solver query."""

    PENDING = "pending"
    RUNNING = "running"
    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"

    def to_dict(self) -> str:
        return self.value

    @classmethod
    def from_dict(cls, value: str) -> QueryStatus:
        return cls(value)


class SessionState(str, Enum):
    """State of a Z3 solver session."""

    FRESH = "fresh"           # newly created, no assertions pushed
    ACTIVE = "active"         # assertions have been pushed, ready to query
    SATURATED = "saturated"   # too many assertions / learned clauses — needs reset
    RESETTING = "resetting"   # currently being reset

    def to_dict(self) -> str:
        return self.value

    @classmethod
    def from_dict(cls, value: str) -> SessionState:
        return cls(value)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SolverQuery:
    """A single SMT query to be sent to the solver.

    ``content_hash`` is the SHA-256 of the *normalised* SMT text and drives
    deduplication.  ``depends_on`` lists query ids that must complete first
    (incremental / push-pop style usage).
    """

    id: str
    smt_text: str
    fragment: SolverFragment
    content_hash: str
    coordinate_id: Optional[str] = None
    obligation_id: Optional[str] = None
    timeout_ms: int = 30_000
    priority: float = 1.0
    depends_on: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        smt_text: str,
        fragment: SolverFragment = SolverFragment.UNKNOWN,
        content_hash: str = "",
        coordinate_id: Optional[str] = None,
        obligation_id: Optional[str] = None,
        timeout_ms: int = 30_000,
        priority: float = 1.0,
        depends_on: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SolverQuery:
        if not content_hash:
            content_hash = hashlib.sha256(smt_text.encode()).hexdigest()
        return cls(
            id=_uid(),
            smt_text=smt_text,
            fragment=fragment,
            content_hash=content_hash,
            coordinate_id=coordinate_id,
            obligation_id=obligation_id,
            timeout_ms=timeout_ms,
            priority=priority,
            depends_on=list(depends_on or []),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "smt_text": self.smt_text,
            "fragment": self.fragment.to_dict(),
            "content_hash": self.content_hash,
            "coordinate_id": self.coordinate_id,
            "obligation_id": self.obligation_id,
            "timeout_ms": self.timeout_ms,
            "priority": self.priority,
            "depends_on": list(self.depends_on),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SolverQuery:
        return cls(
            id=d["id"],
            smt_text=d["smt_text"],
            fragment=SolverFragment.from_dict(d["fragment"]),
            content_hash=d["content_hash"],
            coordinate_id=d.get("coordinate_id"),
            obligation_id=d.get("obligation_id"),
            timeout_ms=d.get("timeout_ms", 30_000),
            priority=float(d.get("priority", 1.0)),
            depends_on=list(d.get("depends_on", [])),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(slots=True)
class SolverResult:
    """The result of executing a :class:`SolverQuery`."""

    query_id: str
    status: QueryStatus
    duration_ms: float
    solver_version: str
    session_id: str
    cached: bool
    model: Optional[dict[str, Any]] = None
    proof_hash: Optional[str] = None

    @classmethod
    def create(
        cls,
        query_id: str,
        status: QueryStatus,
        duration_ms: float,
        solver_version: str = "simulated-1.0",
        session_id: str = "",
        cached: bool = False,
        model: Optional[dict[str, Any]] = None,
        proof_hash: Optional[str] = None,
    ) -> SolverResult:
        return cls(
            query_id=query_id,
            status=status,
            duration_ms=duration_ms,
            solver_version=solver_version,
            session_id=session_id,
            cached=cached,
            model=model,
            proof_hash=proof_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "status": self.status.to_dict(),
            "duration_ms": self.duration_ms,
            "solver_version": self.solver_version,
            "session_id": self.session_id,
            "cached": self.cached,
            "model": self.model,
            "proof_hash": self.proof_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SolverResult:
        return cls(
            query_id=d["query_id"],
            status=QueryStatus.from_dict(d["status"]),
            duration_ms=float(d["duration_ms"]),
            solver_version=d.get("solver_version", ""),
            session_id=d.get("session_id", ""),
            cached=bool(d.get("cached", False)),
            model=d.get("model"),
            proof_hash=d.get("proof_hash"),
        )


@dataclass(slots=True)
class SessionInfo:
    """Metadata about a live solver session."""

    id: str
    state: SessionState
    fragment: SolverFragment
    assertions_count: int
    learned_clauses_estimate: int
    queries_served: int
    created_at: float
    last_used_at: float
    memory_estimate_mb: float

    @classmethod
    def create(
        cls,
        fragment: SolverFragment = SolverFragment.UNKNOWN,
    ) -> SessionInfo:
        now = _now()
        return cls(
            id=_uid(),
            state=SessionState.FRESH,
            fragment=fragment,
            assertions_count=0,
            learned_clauses_estimate=0,
            queries_served=0,
            created_at=now,
            last_used_at=now,
            memory_estimate_mb=0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.to_dict(),
            "fragment": self.fragment.to_dict(),
            "assertions_count": self.assertions_count,
            "learned_clauses_estimate": self.learned_clauses_estimate,
            "queries_served": self.queries_served,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "memory_estimate_mb": self.memory_estimate_mb,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionInfo:
        return cls(
            id=d["id"],
            state=SessionState.from_dict(d["state"]),
            fragment=SolverFragment.from_dict(d["fragment"]),
            assertions_count=int(d.get("assertions_count", 0)),
            learned_clauses_estimate=int(d.get("learned_clauses_estimate", 0)),
            queries_served=int(d.get("queries_served", 0)),
            created_at=float(d["created_at"]),
            last_used_at=float(d["last_used_at"]),
            memory_estimate_mb=float(d.get("memory_estimate_mb", 0.0)),
        )


@dataclass(slots=True)
class QueryBatch:
    """A group of queries belonging to the same SMT fragment."""

    id: str
    fragment: SolverFragment
    queries: list[SolverQuery]
    submitted_at: float
    completed_at: Optional[float] = None

    @classmethod
    def create(
        cls,
        fragment: SolverFragment,
        queries: list[SolverQuery],
    ) -> QueryBatch:
        return cls(
            id=_uid(),
            fragment=fragment,
            queries=list(queries),
            submitted_at=_now(),
            completed_at=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fragment": self.fragment.to_dict(),
            "queries": [q.to_dict() for q in self.queries],
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryBatch:
        return cls(
            id=d["id"],
            fragment=SolverFragment.from_dict(d["fragment"]),
            queries=[SolverQuery.from_dict(q) for q in d.get("queries", [])],
            submitted_at=float(d["submitted_at"]),
            completed_at=d.get("completed_at"),
        )


@dataclass(slots=True)
class DeduplicationResult:
    """Records which queries were deduplicated against which original."""

    original_query_id: str
    duplicate_query_ids: list[str]
    cache_hit: bool

    @classmethod
    def create(
        cls,
        original_query_id: str,
        duplicate_query_ids: Optional[list[str]] = None,
        cache_hit: bool = False,
    ) -> DeduplicationResult:
        return cls(
            original_query_id=original_query_id,
            duplicate_query_ids=list(duplicate_query_ids or []),
            cache_hit=cache_hit,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query_id": self.original_query_id,
            "duplicate_query_ids": list(self.duplicate_query_ids),
            "cache_hit": self.cache_hit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeduplicationResult:
        return cls(
            original_query_id=d["original_query_id"],
            duplicate_query_ids=list(d.get("duplicate_query_ids", [])),
            cache_hit=bool(d.get("cache_hit", False)),
        )


@dataclass(slots=True)
class SolverPoolConfig:
    """Configuration for the solver session pool."""

    max_sessions: int = 8
    max_queries_per_session: int = 1_000
    max_memory_per_session_mb: int = 512
    session_reset_threshold: int = 500
    dedup_enabled: bool = True
    cache_enabled: bool = True
    cache_max_entries: int = 100_000
    batch_size: int = 50
    default_timeout_ms: int = 30_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_sessions": self.max_sessions,
            "max_queries_per_session": self.max_queries_per_session,
            "max_memory_per_session_mb": self.max_memory_per_session_mb,
            "session_reset_threshold": self.session_reset_threshold,
            "dedup_enabled": self.dedup_enabled,
            "cache_enabled": self.cache_enabled,
            "cache_max_entries": self.cache_max_entries,
            "batch_size": self.batch_size,
            "default_timeout_ms": self.default_timeout_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SolverPoolConfig:
        return cls(
            max_sessions=int(d.get("max_sessions", 8)),
            max_queries_per_session=int(d.get("max_queries_per_session", 1_000)),
            max_memory_per_session_mb=int(d.get("max_memory_per_session_mb", 512)),
            session_reset_threshold=int(d.get("session_reset_threshold", 500)),
            dedup_enabled=bool(d.get("dedup_enabled", True)),
            cache_enabled=bool(d.get("cache_enabled", True)),
            cache_max_entries=int(d.get("cache_max_entries", 100_000)),
            batch_size=int(d.get("batch_size", 50)),
            default_timeout_ms=int(d.get("default_timeout_ms", 30_000)),
        )


@dataclass(slots=True)
class SolverStatistics:
    """Aggregate statistics for the solver scaling infrastructure."""

    total_queries: int = 0
    cache_hits: int = 0
    dedup_hits: int = 0
    unique_queries: int = 0
    avg_duration_ms: float = 0.0
    p99_duration_ms: float = 0.0
    sessions_created: int = 0
    sessions_reset: int = 0
    by_fragment: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)

    @property
    def cache_hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.cache_hits / self.total_queries

    @property
    def dedup_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.dedup_hits / self.total_queries

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "dedup_hits": self.dedup_hits,
            "unique_queries": self.unique_queries,
            "avg_duration_ms": self.avg_duration_ms,
            "p99_duration_ms": self.p99_duration_ms,
            "sessions_created": self.sessions_created,
            "sessions_reset": self.sessions_reset,
            "cache_hit_rate": self.cache_hit_rate,
            "dedup_rate": self.dedup_rate,
            "by_fragment": dict(self.by_fragment),
            "by_status": dict(self.by_status),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SolverStatistics:
        return cls(
            total_queries=int(d.get("total_queries", 0)),
            cache_hits=int(d.get("cache_hits", 0)),
            dedup_hits=int(d.get("dedup_hits", 0)),
            unique_queries=int(d.get("unique_queries", 0)),
            avg_duration_ms=float(d.get("avg_duration_ms", 0.0)),
            p99_duration_ms=float(d.get("p99_duration_ms", 0.0)),
            sessions_created=int(d.get("sessions_created", 0)),
            sessions_reset=int(d.get("sessions_reset", 0)),
            by_fragment=dict(d.get("by_fragment", {})),
            by_status=dict(d.get("by_status", {})),
        )
