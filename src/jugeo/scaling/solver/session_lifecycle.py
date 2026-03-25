"""Session lifecycle management for the solver scaling layer.

:class:`SessionPool` maintains a pool of :class:`~jugeo.scaling.solver.models.SessionInfo`
records that represent live (or simulated) Z3 solver sessions.  It handles:

* acquiring a session for a given SMT fragment (preferring an existing session
  that already has the same fragment warmed up),
* releasing sessions back to the pool after a query batch completes,
* detecting when a session has become saturated (too many assertions / learned
  clauses) and resetting it,
* evicting the oldest session when the pool is at capacity and a new session is
  needed,
* graceful drain (stop issuing new sessions, wait for in-flight ones to return)
  and hard shutdown.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Optional

from jugeo.scaling.solver.models import (
    SessionInfo,
    SessionState,
    SolverFragment,
    SolverPoolConfig,
)


class SessionPool:
    """Thread-safe pool of solver sessions.

    All public methods acquire ``_lock`` and are safe to call from multiple
    threads simultaneously.
    """

    def __init__(self, config: SolverPoolConfig) -> None:
        self._config = config
        self._lock = threading.Lock()

        # id → SessionInfo for all known sessions
        self._sessions: dict[str, SessionInfo] = {}
        # ids of sessions that are currently checked out
        self._active: set[str] = set()
        # ids of sessions that are available to be acquired
        self._idle: list[str] = []

        self._sessions_created = 0
        self._sessions_reset = 0
        self._draining = False

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def acquire(
        self,
        fragment: Optional[SolverFragment] = None,
        timeout: Optional[float] = None,
    ) -> SessionInfo:
        """Return a session suitable for *fragment*, creating one if needed.

        Preference order:
        1. An idle session whose fragment matches *fragment* exactly.
        2. Any idle session (fragment will be re-used as-is).
        3. A newly created session (evicting the oldest idle session first if
           the pool is at capacity).

        Raises :exc:`RuntimeError` if the pool is draining.
        """
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            with self._lock:
                if self._draining:
                    raise RuntimeError("SessionPool is draining — cannot acquire new sessions")

                session = self._try_acquire_locked(fragment)
                if session is not None:
                    return session

            # Nothing available yet — wait briefly and retry (only possible in
            # future extension where callers can block).
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for an available session")
            time.sleep(0.01)

    def release(self, session_id: str) -> None:
        """Return *session_id* to the idle pool after use."""
        with self._lock:
            if session_id not in self._sessions:
                return
            session = self._sessions[session_id]
            self._active.discard(session_id)

            # Check whether the session needs resetting after release.
            if self._should_reset(session):
                session = self._reset_session_locked(session_id)

            if session.state not in (SessionState.SATURATED, SessionState.RESETTING):
                self._idle.append(session_id)

    def active_sessions(self) -> list[SessionInfo]:
        """Return a snapshot of all known sessions (idle and active)."""
        with self._lock:
            return list(self._sessions.values())

    def session_health(self, session_id: str) -> dict[str, Any]:
        """Return health metrics for *session_id*."""
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Unknown session: {session_id}")
            session = self._sessions[session_id]
            age = time.time() - session.created_at
            idle_time = time.time() - session.last_used_at
            return {
                "session_id": session_id,
                "state": session.state.value,
                "fragment": session.fragment.value,
                "queries_served": session.queries_served,
                "assertions_count": session.assertions_count,
                "learned_clauses_estimate": session.learned_clauses_estimate,
                "memory_estimate_mb": session.memory_estimate_mb,
                "age_seconds": age,
                "idle_seconds": idle_time,
                "is_active": session_id in self._active,
            }

    def statistics(self) -> dict[str, Any]:
        """Return pool-level statistics."""
        with self._lock:
            by_fragment: dict[str, int] = defaultdict(int)
            for s in self._sessions.values():
                by_fragment[s.fragment.value] += 1
            return {
                "total_sessions": len(self._sessions),
                "active": len(self._active),
                "idle": len(self._idle),
                "sessions_created": self._sessions_created,
                "sessions_reset": self._sessions_reset,
                "max_sessions": self._config.max_sessions,
                "draining": self._draining,
                "by_fragment": dict(by_fragment),
            }

    def drain(self) -> None:
        """Stop accepting new sessions and wait for active sessions to return."""
        with self._lock:
            self._draining = True
        # Poll until all checked-out sessions have been released.
        while True:
            with self._lock:
                if not self._active:
                    break
            time.sleep(0.05)

    def shutdown(self) -> None:
        """Force-close all sessions immediately."""
        with self._lock:
            self._draining = True
            self._sessions.clear()
            self._active.clear()
            self._idle.clear()

    # ---------------------------------------------------------------------------
    # Internal helpers (must be called with _lock held unless stated otherwise)
    # ---------------------------------------------------------------------------

    def _try_acquire_locked(
        self, fragment: Optional[SolverFragment]
    ) -> Optional[SessionInfo]:
        """Try to find/create a session without blocking.  Returns None if the
        pool is at capacity and all sessions are busy."""
        # Prefer idle sessions that match the requested fragment.
        if fragment is not None:
            for sid in list(self._idle):
                s = self._sessions[sid]
                if s.fragment == fragment:
                    self._idle.remove(sid)
                    self._active.add(sid)
                    s.last_used_at = time.time()
                    return s

        # Fall back to any idle session.
        if self._idle:
            sid = self._idle.pop(0)
            s = self._sessions[sid]
            self._active.add(sid)
            s.last_used_at = time.time()
            return s

        # Create a new session if capacity allows.
        if len(self._sessions) < self._config.max_sessions:
            return self._create_session_locked(fragment)

        # At capacity with nothing idle — evict the oldest idle session if any.
        evicted = self._evict_oldest_locked()
        if evicted is not None:
            return self._create_session_locked(fragment)

        return None

    def _create_session(
        self, fragment: Optional[SolverFragment] = None
    ) -> SessionInfo:
        """Create a new session (acquires lock internally)."""
        with self._lock:
            return self._create_session_locked(fragment)

    def _create_session_locked(
        self, fragment: Optional[SolverFragment] = None
    ) -> SessionInfo:
        """Create and register a new session.  Must be called with lock held."""
        frag = fragment if fragment is not None else SolverFragment.UNKNOWN
        session = SessionInfo.create(fragment=frag)
        session.state = SessionState.ACTIVE
        self._sessions[session.id] = session
        self._active.add(session.id)
        self._sessions_created += 1
        return session

    def _should_reset(self, session: SessionInfo) -> bool:
        """Return True if *session* has exceeded any reset threshold."""
        cfg = self._config
        if session.queries_served >= cfg.session_reset_threshold:
            return True
        if session.assertions_count >= cfg.max_queries_per_session:
            return True
        if session.memory_estimate_mb >= cfg.max_memory_per_session_mb:
            return True
        return False

    def _reset_session(self, session_id: str) -> SessionInfo:
        """Reset a session (acquires lock internally)."""
        with self._lock:
            return self._reset_session_locked(session_id)

    def _reset_session_locked(self, session_id: str) -> SessionInfo:
        """Reset *session_id* to a fresh state.  Must be called with lock held."""
        session = self._sessions[session_id]
        session.state = SessionState.RESETTING
        # Simulate the reset: clear all accumulated state.
        session.assertions_count = 0
        session.learned_clauses_estimate = 0
        session.queries_served = 0
        session.memory_estimate_mb = 0.0
        session.state = SessionState.ACTIVE
        session.last_used_at = time.time()
        self._sessions_reset += 1
        return session

    def _evict_oldest(self) -> Optional[str]:
        """Evict the oldest idle session (acquires lock internally)."""
        with self._lock:
            return self._evict_oldest_locked()

    def _evict_oldest_locked(self) -> Optional[str]:
        """Evict the oldest idle session.  Must be called with lock held."""
        if not self._idle:
            return None
        # Find the idle session with the smallest created_at.
        oldest_id = min(
            self._idle,
            key=lambda sid: self._sessions[sid].created_at,
        )
        self._idle.remove(oldest_id)
        del self._sessions[oldest_id]
        return oldest_id

    def _record_query(self, session_id: str, duration_ms: float) -> None:
        """Update session metrics after a query completes.

        Called externally by the router after each query finishes.
        """
        with self._lock:
            if session_id not in self._sessions:
                return
            s = self._sessions[session_id]
            s.queries_served += 1
            s.assertions_count += 1
            s.last_used_at = time.time()
            # Simple memory growth heuristic: 0.1 MB per query.
            s.memory_estimate_mb += 0.1
            # Rough learned-clause estimate: grows with queries_served.
            s.learned_clauses_estimate = s.queries_served * 3
