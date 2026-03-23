"""Solver/evidence router for the JuGeo verification pipeline.

This module dispatches verification tasks to the appropriate backend solver
or evidence source.  Each backend has *jurisdiction* over specific logical
domains and a *trust ceiling* that caps how much confidence the evidence it
produces can carry.  The router respects these boundaries so that, e.g., a
runtime witness cannot silently claim VERIFIED-tier authority over a purely
structural lemma, and a copilot oracle is only consulted when every cheaper
or more trusted backend has been exhausted.

Architecture (cf. theory2.tex §4 — Solver Dispatch & Jurisdiction):

    request  ──►  JurisdictionChecker
                        │
                        ▼
                  SolverRouter.route()
                        │
              ┌─────────┼──────────┐
              ▼         ▼          ▼
           Z3/SMT   Runtime    CopilotFallback
           backend   witness    (last resort)
              │         │          │
              └────►  merge  ◄─────┘
                        │
                        ▼
                  RoutingDecision

Key invariants
--------------
* **No silent promotion** — a low-trust backend cannot upgrade its own
  ceiling.  Promotion requires an explicit policy flag.
* **Jurisdiction is checked before dispatch** — a request whose domain
  falls outside *every* registered backend is rejected, not silently
  dropped.
* **Copilot is last-resort** — the copilot oracle is only selected when
  ``copilot_as_last_resort`` is set *and* no other backend can serve the
  request.
"""

from __future__ import annotations

import abc
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.solver.fragments import LogicalFragment, SolverFragment

# ---------------------------------------------------------------------------
# Optional imports for cross-subsystem integration (judgment-geometric links)
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import (
        ResourceBudget as _ResourceBudget,
        OrchestratorConfiguration as _OrchestratorConfiguration,
    )
    _ORCHESTRATION_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ORCHESTRATION_AVAILABLE = False

try:
    from jugeo.evidence.trust import TrustAlgebra as _TrustAlgebra
    _TRUST_ALGEBRA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _TRUST_ALGEBRA_AVAILABLE = False

try:
    from jugeo.encodings.structural_frontier import (
        DecidabilityClass as _DecidabilityClass,
        classify_formula_fragment as _classify_formula_fragment,
        StructuralFrontierDefiner as _StructuralFrontierDefiner,
    )
    _STRUCTURAL_FRONTIER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _STRUCTURAL_FRONTIER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class BackendKind(str, Enum):
    """The category of a solver/evidence backend."""

    Z3 = "z3"
    RUNTIME = "runtime"
    ORACLE = "oracle"
    COPILOT = "copilot"
    PROVER = "prover"
    HUMAN = "human"


class RoutingStrategyKind(str, Enum):
    """High-level label for a routing strategy."""

    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    MOST_TRUSTED = "most-trusted"
    ROUND_ROBIN = "round-robin"
    SMART = "smart"


class VerificationDomain(str, Enum):
    """Domains over which a backend may hold jurisdiction."""

    STRUCTURAL = "structural"
    ARITHMETIC = "arithmetic"
    HEAP = "heap"
    IDENTITY = "identity"
    SEMANTIC = "semantic"
    MECHANIZED = "mechanized"
    PROPOSITIONAL = "propositional"
    EQUALITY = "equality"


# ---------------------------------------------------------------------------
# Core data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """An immutable record of a single routing decision.

    Every field is populated *before* the request is actually dispatched so
    that the decision can be audited independently of the outcome.
    """

    request_id: str
    selected_backend: str
    fallback_backends: tuple[str, ...]
    jurisdiction_check_passed: bool
    trust_ceiling: TrustTier
    estimated_cost: float
    estimated_latency: float
    rationale: str

    # ------------------------------------------------------------------
    @property
    def engine(self) -> str:
        """Alias for ``selected_backend`` (backward-compatible)."""
        return self.selected_backend

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON encoding."""
        return {
            "request_id": self.request_id,
            "selected_backend": self.selected_backend,
            "fallback_backends": list(self.fallback_backends),
            "jurisdiction_check_passed": self.jurisdiction_check_passed,
            "trust_ceiling": self.trust_ceiling.name,
            "estimated_cost": self.estimated_cost,
            "estimated_latency": self.estimated_latency,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True, init=False)
class BackendDescriptor:
    """Describes a solver/evidence backend and its capabilities.

    ``jurisdiction`` is the set of :class:`VerificationDomain` values that
    this backend is authorized to handle.  ``trust_ceiling`` is the maximum
    :class:`TrustTier` that evidence produced by this backend may carry.
    """

    name: str
    kind: BackendKind
    jurisdiction: frozenset[VerificationDomain]
    trust_ceiling: TrustTier
    is_available: bool = True
    priority: int = 0
    cost_per_query: float = 0.0
    average_latency_ms: float = 100.0

    def __init__(
        self,
        name: str,
        kind: BackendKind,
        jurisdiction: frozenset[VerificationDomain] | set[VerificationDomain] | None = None,
        trust_ceiling: TrustTier = TrustTier.VERIFIED,
        is_available: bool = True,
        priority: int = 0,
        cost_per_query: float = 0.0,
        average_latency_ms: float = 100.0,
        *,
        domains: frozenset[VerificationDomain] | set[VerificationDomain] | None = None,
        latency_ms: float | None = None,
        cost: float | None = None,
    ) -> None:
        resolved_jurisdiction = jurisdiction if jurisdiction is not None else domains
        if resolved_jurisdiction is None:
            resolved_jurisdiction = frozenset()
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "jurisdiction", frozenset(resolved_jurisdiction))
        object.__setattr__(self, "trust_ceiling", trust_ceiling)
        object.__setattr__(self, "is_available", is_available)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "cost_per_query", cost if cost is not None else cost_per_query)
        object.__setattr__(self, "average_latency_ms", latency_ms if latency_ms is not None else average_latency_ms)

    # ------------------------------------------------------------------
    def covers_domain(self, domain: VerificationDomain) -> bool:
        """Return *True* if *domain* is within this backend's jurisdiction."""
        return domain in self.jurisdiction

    @property
    def domains(self) -> frozenset[VerificationDomain]:
        return self.jurisdiction

    @property
    def latency_ms(self) -> float:
        return self.average_latency_ms

    @property
    def cost(self) -> float:
        return self.cost_per_query

    def covers_any(self, domains: set[VerificationDomain]) -> bool:
        """Return *True* if the backend covers at least one of *domains*."""
        return bool(self.jurisdiction & domains)

    def covers_all(self, domains: set[VerificationDomain]) -> bool:
        """Return *True* if the backend covers every domain in *domains*."""
        return domains <= self.jurisdiction

    def effective_trust(self, requested_tier: TrustTier) -> TrustTier:
        """Return the lesser of *requested_tier* and the backend ceiling."""
        return min(self.trust_ceiling, requested_tier, key=lambda t: t.value)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON interchange."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "jurisdiction": sorted(d.value for d in self.jurisdiction),
            "trust_ceiling": self.trust_ceiling.name,
            "is_available": self.is_available,
            "priority": self.priority,
            "cost_per_query": self.cost_per_query,
            "average_latency_ms": self.average_latency_ms,
        }


@dataclass
class RouterConfiguration:
    """Mutable configuration for a :class:`SolverRouter`.

    ``copilot_as_last_resort`` (default *True*) ensures the copilot oracle
    is only selected when no other backend can handle the request.
    """

    backends: list[BackendDescriptor] = field(default_factory=list)
    routing_strategy: RoutingStrategyKind = RoutingStrategyKind.SMART
    fallback_policy: str = "chain"
    jurisdiction_strict: bool = True
    copilot_as_last_resort: bool = True
    max_fallback_depth: int = 3
    cost_budget: float = float("inf")
    latency_budget_ms: float = float("inf")

    # ------------------------------------------------------------------
    def backend_by_name(self, name: str) -> BackendDescriptor | None:
        """Look up a backend by its unique *name*."""
        for b in self.backends:
            if b.name == name:
                return b
        return None

    def available_backends(self) -> list[BackendDescriptor]:
        """Return only the backends that are currently available."""
        return [b for b in self.backends if b.is_available]

    def add_backend(self, backend: BackendDescriptor) -> None:
        """Register *backend*, replacing any existing entry with the same name."""
        self.backends = [b for b in self.backends if b.name != backend.name]
        self.backends.append(backend)

    def remove_backend(self, name: str) -> bool:
        """Remove the backend named *name*.  Return *True* if it existed."""
        before = len(self.backends)
        self.backends = [b for b in self.backends if b.name != name]
        return len(self.backends) < before

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration."""
        return {
            "backends": [b.to_dict() for b in self.backends],
            "routing_strategy": self.routing_strategy.value,
            "fallback_policy": self.fallback_policy,
            "jurisdiction_strict": self.jurisdiction_strict,
            "copilot_as_last_resort": self.copilot_as_last_resort,
            "max_fallback_depth": self.max_fallback_depth,
            "cost_budget": self.cost_budget,
            "latency_budget_ms": self.latency_budget_ms,
        }


# ---------------------------------------------------------------------------
# Jurisdiction checker
# ---------------------------------------------------------------------------

class JurisdictionChecker:
    """Validates that a backend has authority over a requested domain.

    In the JuGeo model every subsystem — solver, runtime, oracle — has
    explicit bounds on what it can certify.  This class enforces those
    bounds *before* dispatch so that violations are caught early.
    """

    def __init__(self, strict: bool = True) -> None:
        self._strict = strict
        self._violation_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def check(
        self,
        backend: BackendDescriptor,
        domains: set[VerificationDomain],
    ) -> bool:
        """Return *True* if *backend* covers all requested *domains*.

        In non-strict mode the check passes as long as at least one
        domain is covered.
        """
        if self._strict:
            ok = backend.covers_all(domains)
        else:
            ok = backend.covers_any(domains)
        if not ok:
            self._record_violation(backend, domains)
        return ok

    def explain_violation(
        self,
        backend: BackendDescriptor,
        domains: set[VerificationDomain],
    ) -> str:
        """Return a human-readable explanation of why the check fails."""
        missing = domains - backend.jurisdiction
        if not missing:
            return f"Backend '{backend.name}' fully covers the requested domains."
        names = ", ".join(sorted(d.value for d in missing))
        return (
            f"Backend '{backend.name}' (kind={backend.kind.value}) lacks "
            f"jurisdiction over: {names}.  Its jurisdiction is limited to "
            f"{sorted(d.value for d in backend.jurisdiction)}."
        )

    def suggest_alternative(
        self,
        domains: set[VerificationDomain],
        available: Sequence[BackendDescriptor],
    ) -> BackendDescriptor | None:
        """Find the first available backend that covers all *domains*.

        Preference order: highest trust ceiling, then lowest cost.
        """
        candidates = [
            b for b in available
            if b.is_available and b.covers_all(domains)
        ]
        if not candidates:
            candidates = [
                b for b in available
                if b.is_available and b.covers_any(domains)
            ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda b: (-b.trust_ceiling.value, b.cost_per_query),
        )
        return candidates[0]

    def is_within_ceiling(
        self,
        backend: BackendDescriptor,
        requested_tier: TrustTier,
    ) -> bool:
        """Return *True* if *requested_tier* does not exceed the ceiling."""
        return requested_tier.value <= backend.trust_ceiling.value

    def compute_effective_trust(
        self,
        backend: BackendDescriptor,
        requested_tier: TrustTier,
    ) -> TrustTier:
        """Clamp *requested_tier* to the backend's trust ceiling.

        This is the tier that the produced evidence will actually carry.
        """
        return backend.effective_trust(requested_tier)

    def violation_count(self) -> int:
        """Total number of jurisdiction violations recorded."""
        return len(self._violation_log)

    def recent_violations(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the *n* most-recent violation records."""
        return list(self._violation_log[-n:])

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _record_violation(
        self,
        backend: BackendDescriptor,
        domains: set[VerificationDomain],
    ) -> None:
        self._violation_log.append({
            "backend": backend.name,
            "requested": sorted(d.value for d in domains),
            "jurisdiction": sorted(d.value for d in backend.jurisdiction),
            "timestamp": time.time(),
        })


# ---------------------------------------------------------------------------
# Routing strategies
# ---------------------------------------------------------------------------

class RoutingStrategy(abc.ABC):
    """Abstract base class for backend-selection strategies."""

    @abc.abstractmethod
    def select(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> BackendDescriptor | None:
        """Choose a single backend from *candidates*.

        Return *None* when no candidate is acceptable.
        """

    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    def rank(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> list[BackendDescriptor]:
        """Return *candidates* ordered by preference (best first).

        Default implementation delegates to :meth:`select` once; subclasses
        may override for a full ranking.
        """
        best = self.select(candidates, domains, requested_tier)
        if best is None:
            return []
        rest = [c for c in candidates if c.name != best.name]
        return [best, *rest]


class CheapestStrategy(RoutingStrategy):
    """Select the backend with the lowest per-query cost."""

    def select(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> BackendDescriptor | None:
        viable = [c for c in candidates if c.is_available]
        if not viable:
            return None
        viable.sort(key=lambda b: (b.cost_per_query, -b.trust_ceiling.value))
        return viable[0]

    def name(self) -> str:
        return "cheapest"

    def rank(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> list[BackendDescriptor]:
        viable = [c for c in candidates if c.is_available]
        viable.sort(key=lambda b: (b.cost_per_query, -b.trust_ceiling.value))
        return viable


class FastestStrategy(RoutingStrategy):
    """Select the backend with the lowest average latency."""

    def select(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> BackendDescriptor | None:
        viable = [c for c in candidates if c.is_available]
        if not viable:
            return None
        viable.sort(key=lambda b: (b.average_latency_ms, -b.trust_ceiling.value))
        return viable[0]

    def name(self) -> str:
        return "fastest"

    def rank(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> list[BackendDescriptor]:
        viable = [c for c in candidates if c.is_available]
        viable.sort(key=lambda b: (b.average_latency_ms, -b.trust_ceiling.value))
        return viable


class MostTrustedStrategy(RoutingStrategy):
    """Select the backend whose trust ceiling is highest."""

    def select(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> BackendDescriptor | None:
        viable = [c for c in candidates if c.is_available]
        if not viable:
            return None
        viable.sort(key=lambda b: (-b.trust_ceiling.value, b.cost_per_query))
        return viable[0]

    def name(self) -> str:
        return "most-trusted"

    def rank(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> list[BackendDescriptor]:
        viable = [c for c in candidates if c.is_available]
        viable.sort(key=lambda b: (-b.trust_ceiling.value, b.cost_per_query))
        return viable


class RoundRobinStrategy(RoutingStrategy):
    """Cycle through candidates in registration order.

    Maintains internal state so successive calls rotate through backends.
    """

    def __init__(self) -> None:
        self._index: int = 0

    def select(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> BackendDescriptor | None:
        viable = [c for c in candidates if c.is_available]
        if not viable:
            return None
        chosen = viable[self._index % len(viable)]
        self._index += 1
        return chosen

    def name(self) -> str:
        return "round-robin"

    def reset(self) -> None:
        """Reset the rotation index to zero."""
        self._index = 0


class SmartStrategy(RoutingStrategy):
    """Adaptive strategy that factors in historical performance.

    Uses a simple scoring function::

        score = w_trust * trust_norm
              + w_cost  * (1 - cost_norm)
              + w_speed * (1 - latency_norm)
              + w_hist  * historical_success_rate

    The weights can be tuned via the constructor.
    """

    def __init__(
        self,
        *,
        w_trust: float = 0.35,
        w_cost: float = 0.15,
        w_speed: float = 0.20,
        w_hist: float = 0.30,
        history: RoutingHistory | None = None,
    ) -> None:
        self._w_trust = w_trust
        self._w_cost = w_cost
        self._w_speed = w_speed
        self._w_hist = w_hist
        self._history = history

    def attach_history(self, history: RoutingHistory) -> None:
        """Bind a :class:`RoutingHistory` instance for adaptive scoring."""
        self._history = history

    def select(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> BackendDescriptor | None:
        ranked = self.rank(candidates, domains, requested_tier)
        return ranked[0] if ranked else None

    def name(self) -> str:
        return "smart"

    def rank(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> list[BackendDescriptor]:
        viable = [c for c in candidates if c.is_available]
        if not viable:
            return []

        max_cost = max(b.cost_per_query for b in viable) or 1.0
        max_lat = max(b.average_latency_ms for b in viable) or 1.0
        max_trust_val = max(t.value for t in TrustTier) or 1

        def _score(b: BackendDescriptor) -> float:
            trust_norm = b.trust_ceiling.value / max_trust_val
            cost_norm = b.cost_per_query / max_cost if max_cost else 0.0
            lat_norm = b.average_latency_ms / max_lat if max_lat else 0.0
            hist_rate = 0.5  # neutral prior
            if self._history is not None:
                rate = self._history.success_rate_for(b.name)
                if rate is not None:
                    hist_rate = rate
            return (
                self._w_trust * trust_norm
                + self._w_cost * (1.0 - cost_norm)
                + self._w_speed * (1.0 - lat_norm)
                + self._w_hist * hist_rate
            )

        viable.sort(key=lambda b: -_score(b))
        return viable


def _build_strategy(kind: RoutingStrategyKind) -> RoutingStrategy:
    """Factory helper: create a fresh strategy instance for *kind*."""
    if kind is RoutingStrategyKind.CHEAPEST:
        return CheapestStrategy()
    if kind is RoutingStrategyKind.FASTEST:
        return FastestStrategy()
    if kind is RoutingStrategyKind.MOST_TRUSTED:
        return MostTrustedStrategy()
    if kind is RoutingStrategyKind.ROUND_ROBIN:
        return RoundRobinStrategy()
    return SmartStrategy()


# ---------------------------------------------------------------------------
# Routing history
# ---------------------------------------------------------------------------

@dataclass
class _HistoryEntry:
    """One recorded routing outcome."""

    request_id: str
    backend: str
    domains: tuple[str, ...]
    succeeded: bool
    latency_ms: float
    cost: float
    timestamp: float


class RoutingHistory:
    """Records routing decisions and outcomes for later analysis.

    The history is stored in-memory and is intended to feed adaptive
    strategies (e.g. :class:`SmartStrategy`) as well as the monitoring
    dashboard.
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._entries: list[_HistoryEntry] = []
        self._max = max_entries

    # ------------------------------------------------------------------
    def record(
        self,
        request_id: str,
        backend: str,
        domains: Sequence[str],
        succeeded: bool,
        latency_ms: float = 0.0,
        cost: float = 0.0,
    ) -> None:
        """Append a routing-outcome entry."""
        entry = _HistoryEntry(
            request_id=request_id,
            backend=backend,
            domains=tuple(domains),
            succeeded=succeeded,
            latency_ms=latency_ms,
            cost=cost,
            timestamp=time.time(),
        )
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]

    def query_by_backend(self, backend: str) -> list[_HistoryEntry]:
        """Return all entries for *backend*."""
        return [e for e in self._entries if e.backend == backend]

    def query_by_domain(self, domain: str) -> list[_HistoryEntry]:
        """Return all entries that include *domain*."""
        return [e for e in self._entries if domain in e.domains]

    def success_rate_for(self, backend: str) -> float | None:
        """Return success rate in [0, 1] for *backend*, or *None* if no data."""
        entries = self.query_by_backend(backend)
        if not entries:
            return None
        return sum(1 for e in entries if e.succeeded) / len(entries)

    def average_latency_for(self, backend: str) -> float | None:
        """Mean latency in ms for *backend*, or *None*."""
        entries = self.query_by_backend(backend)
        if not entries:
            return None
        return sum(e.latency_ms for e in entries) / len(entries)

    def cost_summary(self) -> dict[str, float]:
        """Total cost per backend across all recorded entries."""
        totals: dict[str, float] = defaultdict(float)
        for e in self._entries:
            totals[e.backend] += e.cost
        return dict(totals)

    def anomaly_detection(self, *, latency_threshold_factor: float = 3.0) -> list[_HistoryEntry]:
        """Flag entries whose latency exceeds *threshold_factor* × backend average.

        This simple heuristic surfaces requests that took significantly longer
        than usual, which may indicate backend degradation.
        """
        avg_by_backend: dict[str, float] = {}
        for b_name in {e.backend for e in self._entries}:
            avg = self.average_latency_for(b_name)
            if avg is not None:
                avg_by_backend[b_name] = avg

        anomalies: list[_HistoryEntry] = []
        for e in self._entries:
            avg = avg_by_backend.get(e.backend)
            if avg is not None and e.latency_ms > avg * latency_threshold_factor:
                anomalies.append(e)
        return anomalies

    def total_entries(self) -> int:
        """Number of recorded entries."""
        return len(self._entries)

    def clear(self) -> None:
        """Drop all entries."""
        self._entries.clear()


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------

class FallbackChain:
    """An ordered list of fallback backends for a single request.

    The chain is consumed left-to-right; calling :meth:`try_next` advances
    the cursor.  Once exhausted the caller must either reject the request
    or escalate to an out-of-band policy (e.g. human review).
    """

    def __init__(self, backends: Sequence[BackendDescriptor]) -> None:
        self._backends = list(backends)
        self._cursor: int = 0

    # ------------------------------------------------------------------
    def current(self) -> BackendDescriptor | None:
        """The backend the chain is currently pointing at, or *None*."""
        if self._cursor < len(self._backends):
            return self._backends[self._cursor]
        return None

    def try_next(self) -> BackendDescriptor | None:
        """Advance to the next backend and return it, or *None*."""
        self._cursor += 1
        return self.current()

    def is_exhausted(self) -> bool:
        """True when there are no more backends to try."""
        return self._cursor >= len(self._backends)

    def reset(self) -> None:
        """Rewind the cursor to the beginning."""
        self._cursor = 0

    def remaining(self) -> list[BackendDescriptor]:
        """Backends that have not yet been tried."""
        return list(self._backends[self._cursor:])

    def skip_current(self) -> BackendDescriptor | None:
        """Skip the current backend without marking it as tried and advance."""
        return self.try_next()

    def depth(self) -> int:
        """How many backends have been tried so far."""
        return self._cursor

    def backend_names(self) -> list[str]:
        """Names of all backends in the chain, in order."""
        return [b.name for b in self._backends]


# ---------------------------------------------------------------------------
# Copilot fallback policy
# ---------------------------------------------------------------------------

class CopilotFallbackPolicy:
    """Special policy governing when and how the copilot oracle is consulted.

    The copilot is a powerful but *low-trust* backend: it can handle
    semantic claims that no formal solver can, but its evidence never
    exceeds PROPOSAL tier unless corroborated by a higher-trust source.

    This class encodes rate-limiting, corroboration requirements, and
    prompt construction for copilot queries.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_queries_per_minute: int = 10,
        require_corroboration_above: TrustTier = TrustTier.PROPOSAL,
        copilot_trust_ceiling: TrustTier = TrustTier.PROPOSAL,
    ) -> None:
        self._enabled = enabled
        self._max_qpm = max_queries_per_minute
        self._corroboration_threshold = require_corroboration_above
        self._ceiling = copilot_trust_ceiling
        self._timestamps: list[float] = []

    # ------------------------------------------------------------------
    def when_to_use_copilot(
        self,
        domains: set[VerificationDomain],
        other_backends_exhausted: bool,
    ) -> bool:
        """Decide whether the copilot should be invoked.

        The copilot is used when:
        1. The policy is enabled.
        2. The request domain includes SEMANTIC.
        3. All other backends have been exhausted — or the domain is
           *exclusively* semantic (no structural/arithmetic component).
        """
        if not self._enabled:
            return False
        if VerificationDomain.SEMANTIC in domains:
            purely_semantic = domains <= {VerificationDomain.SEMANTIC}
            return purely_semantic or other_backends_exhausted
        return other_backends_exhausted

    def trust_ceiling_for_request(
        self,
        requested_tier: TrustTier,
    ) -> TrustTier:
        """Clamp *requested_tier* to the copilot's ceiling."""
        return min(self._ceiling, requested_tier, key=lambda t: t.value)

    def require_corroboration(self, effective_tier: TrustTier) -> bool:
        """Return *True* if copilot evidence at *effective_tier* needs
        corroboration from a higher-trust source before it can be accepted.
        """
        return effective_tier.value > self._corroboration_threshold.value

    def rate_limit(self) -> bool:
        """Return *True* if the rate limit has **not** been exceeded.

        Side-effect: records the current timestamp if the call is allowed.
        """
        now = time.time()
        window_start = now - 60.0
        self._timestamps = [t for t in self._timestamps if t >= window_start]
        if len(self._timestamps) >= self._max_qpm:
            return False
        self._timestamps.append(now)
        return True

    def prompt_for_request(
        self,
        claim: str,
        domains: set[VerificationDomain],
        context: Mapping[str, Any] | None = None,
    ) -> str:
        """Build a structured prompt for the copilot oracle.

        The prompt includes the claim, its domain tags, and any
        additional context supplied by the caller.
        """
        domain_tags = ", ".join(sorted(d.value for d in domains))
        lines = [
            "# Copilot Verification Request",
            "",
            f"**Claim:** {claim}",
            f"**Domains:** {domain_tags}",
            f"**Trust ceiling:** {self._ceiling.name}",
            "",
            "Please evaluate the above claim and provide:",
            "1. A yes/no verdict on whether the claim holds.",
            "2. A brief justification (≤200 words).",
            "3. Confidence level (low / medium / high).",
        ]
        if context:
            lines.append("")
            lines.append("## Additional Context")
            for key, val in context.items():
                lines.append(f"- **{key}:** {val}")
        return "\n".join(lines)

    def queries_remaining(self) -> int:
        """How many copilot queries remain in the current 60-second window."""
        now = time.time()
        recent = sum(1 for t in self._timestamps if t >= now - 60.0)
        return max(0, self._max_qpm - recent)

    def is_enabled(self) -> bool:
        """Whether the copilot fallback is enabled."""
        return self._enabled


# ---------------------------------------------------------------------------
# Router monitor
# ---------------------------------------------------------------------------

class RouterMonitor:
    """Observability layer for the solver router.

    Tracks availability, latency, jurisdiction violations, copilot usage,
    and cumulative cost.
    """

    def __init__(self) -> None:
        self._availability_checks: dict[str, list[bool]] = defaultdict(list)
        self._routing_latencies: list[float] = []
        self._jurisdiction_violations: int = 0
        self._copilot_queries: int = 0
        self._total_queries: int = 0
        self._cost_by_backend: dict[str, float] = defaultdict(float)

    # ------------------------------------------------------------------
    def record_availability(self, backend: str, available: bool) -> None:
        """Record an availability observation for *backend*."""
        self._availability_checks[backend].append(available)

    def backend_availability(self, backend: str) -> float | None:
        """Fraction of positive availability checks for *backend*."""
        checks = self._availability_checks.get(backend)
        if not checks:
            return None
        return sum(1 for c in checks if c) / len(checks)

    def record_routing_latency(self, latency_ms: float) -> None:
        """Record how long the router itself took to make a decision."""
        self._routing_latencies.append(latency_ms)

    def routing_latency(self) -> dict[str, float]:
        """Summary statistics for routing latency.

        Returns a dict with keys ``mean``, ``p50``, ``p99``, ``max``.
        """
        if not self._routing_latencies:
            return {"mean": 0.0, "p50": 0.0, "p99": 0.0, "max": 0.0}
        s = sorted(self._routing_latencies)
        n = len(s)
        return {
            "mean": sum(s) / n,
            "p50": s[n // 2],
            "p99": s[int(n * 0.99)],
            "max": s[-1],
        }

    def record_jurisdiction_violation(self) -> None:
        """Increment the jurisdiction-violation counter."""
        self._jurisdiction_violations += 1

    def jurisdiction_violations(self) -> int:
        """Total number of jurisdiction violations seen."""
        return self._jurisdiction_violations

    def record_copilot_query(self) -> None:
        """Record that a copilot query was made."""
        self._copilot_queries += 1

    def record_query(self) -> None:
        """Record any query (for computing copilot ratio)."""
        self._total_queries += 1

    def copilot_usage_rate(self) -> float:
        """Fraction of total queries that went to copilot."""
        if self._total_queries == 0:
            return 0.0
        return self._copilot_queries / self._total_queries

    def record_cost(self, backend: str, cost: float) -> None:
        """Accumulate *cost* for *backend*."""
        self._cost_by_backend[backend] += cost

    def cost_tracking(self) -> dict[str, float]:
        """Cumulative cost per backend."""
        return dict(self._cost_by_backend)

    def total_cost(self) -> float:
        """Total cost across all backends."""
        return sum(self._cost_by_backend.values())

    def summary(self) -> dict[str, Any]:
        """Aggregate health summary."""
        return {
            "total_queries": self._total_queries,
            "copilot_queries": self._copilot_queries,
            "copilot_usage_rate": self.copilot_usage_rate(),
            "jurisdiction_violations": self._jurisdiction_violations,
            "routing_latency": self.routing_latency(),
            "cost_by_backend": self.cost_tracking(),
            "total_cost": self.total_cost(),
        }


# ---------------------------------------------------------------------------
# Batch router
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _BatchItem:
    """Internal wrapper pairing a request with its routing decision."""

    request_id: str
    fragment: SolverFragment
    domains: set[VerificationDomain]
    trust: TrustTier
    decision: RoutingDecision | None = None


class BatchRouter:
    """Routes multiple verification requests efficiently.

    Groups requests by their target backend so that backends which support
    batch queries can amortise setup cost.
    """

    def __init__(self, router: SolverRouter) -> None:
        self._router = router

    # ------------------------------------------------------------------
    def route_batch(
        self,
        items: Sequence[tuple[SolverFragment, set[VerificationDomain], TrustTier]],
    ) -> list[RoutingDecision]:
        """Route every item and return a list of decisions in order."""
        decisions: list[RoutingDecision] = []
        for frag, doms, tier in items:
            dec = self._router.route(frag, doms, tier)
            decisions.append(dec)
        return decisions

    def group_by_backend(
        self,
        decisions: Sequence[RoutingDecision],
    ) -> dict[str, list[RoutingDecision]]:
        """Partition *decisions* by their selected backend."""
        groups: dict[str, list[RoutingDecision]] = defaultdict(list)
        for d in decisions:
            groups[d.selected_backend].append(d)
        return dict(groups)

    def optimize_grouping(
        self,
        decisions: Sequence[RoutingDecision],
    ) -> dict[str, list[RoutingDecision]]:
        """Like :meth:`group_by_backend` but re-assigns borderline requests
        to reduce the number of groups (minimising cross-backend overhead).

        Heuristic: if a backend already has ≥50 % of the requests, migrate
        single-request groups to it when jurisdiction allows.
        """
        groups = self.group_by_backend(decisions)
        if len(groups) <= 1:
            return groups
        total = sum(len(v) for v in groups.values())
        dominant = max(groups, key=lambda k: len(groups[k]))
        dominant_share = len(groups[dominant]) / total
        if dominant_share < 0.5:
            return groups

        dominant_backend = self._router.config.backend_by_name(dominant)
        if dominant_backend is None:
            return groups

        optimized: dict[str, list[RoutingDecision]] = {dominant: list(groups[dominant])}
        for name, decs in groups.items():
            if name == dominant:
                continue
            if len(decs) == 1:
                optimized[dominant].extend(decs)
            else:
                optimized[name] = decs
        return optimized

    def parallel_dispatch(
        self,
        groups: dict[str, list[RoutingDecision]],
    ) -> dict[str, list[str]]:
        """Simulate parallel dispatch by returning request-IDs grouped by backend.

        Actual I/O dispatch is the caller's responsibility; this method
        provides the grouping that enables it.
        """
        return {
            backend: [d.request_id for d in decs]
            for backend, decs in groups.items()
        }

    def merge_results(
        self,
        partial_results: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """Merge result dicts from multiple backends into one mapping
        keyed by ``request_id``.
        """
        merged: dict[str, Any] = {}
        for chunk in partial_results:
            for rid, value in chunk.items():
                merged[rid] = value
        return merged

    def estimate_batch_cost(
        self,
        decisions: Sequence[RoutingDecision],
    ) -> float:
        """Sum estimated costs across all decisions."""
        return sum(d.estimated_cost for d in decisions)

    def estimate_batch_latency(
        self,
        groups: dict[str, list[RoutingDecision]],
    ) -> float:
        """Estimate wall-clock latency assuming groups run in parallel.

        The latency is the *maximum* per-group latency, where each group's
        latency is the *sum* of its members (sequential within a group).
        """
        if not groups:
            return 0.0
        return max(
            sum(d.estimated_latency for d in decs)
            for decs in groups.values()
        )


# ---------------------------------------------------------------------------
# Router serializer
# ---------------------------------------------------------------------------

class RouterSerializer:
    """JSON serialization for routing artefacts.

    All ``serialize_*`` methods return plain Python dicts/lists that are
    safe to pass to :func:`json.dumps`.  The ``deserialize_*`` methods
    accept the same shapes and return domain objects.
    """

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    @staticmethod
    def serialize_decision(decision: RoutingDecision) -> dict[str, Any]:
        """Serialize a single :class:`RoutingDecision`."""
        return decision.to_dict()

    @staticmethod
    def serialize_decisions(decisions: Sequence[RoutingDecision]) -> list[dict[str, Any]]:
        """Serialize a list of decisions."""
        return [d.to_dict() for d in decisions]

    @staticmethod
    def deserialize_decision(data: dict[str, Any]) -> RoutingDecision:
        """Reconstruct a :class:`RoutingDecision` from a dict."""
        return RoutingDecision(
            request_id=data["request_id"],
            selected_backend=data["selected_backend"],
            fallback_backends=tuple(data.get("fallback_backends", ())),
            jurisdiction_check_passed=data["jurisdiction_check_passed"],
            trust_ceiling=TrustTier[data["trust_ceiling"]],
            estimated_cost=float(data.get("estimated_cost", 0.0)),
            estimated_latency=float(data.get("estimated_latency", 0.0)),
            rationale=data.get("rationale", ""),
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @staticmethod
    def serialize_configuration(config: RouterConfiguration) -> dict[str, Any]:
        """Serialize router configuration."""
        return config.to_dict()

    @staticmethod
    def deserialize_backend(data: dict[str, Any]) -> BackendDescriptor:
        """Reconstruct a :class:`BackendDescriptor` from a dict."""
        return BackendDescriptor(
            name=data["name"],
            kind=BackendKind(data["kind"]),
            jurisdiction=frozenset(
                VerificationDomain(d) for d in data.get("jurisdiction", [])
            ),
            trust_ceiling=TrustTier[data["trust_ceiling"]],
            is_available=data.get("is_available", True),
            priority=data.get("priority", 0),
            cost_per_query=float(data.get("cost_per_query", 0.0)),
            average_latency_ms=float(data.get("average_latency_ms", 100.0)),
        )

    @staticmethod
    def deserialize_configuration(data: dict[str, Any]) -> RouterConfiguration:
        """Reconstruct a :class:`RouterConfiguration` from a dict."""
        backends = [
            RouterSerializer.deserialize_backend(b)
            for b in data.get("backends", [])
        ]
        return RouterConfiguration(
            backends=backends,
            routing_strategy=RoutingStrategyKind(data.get("routing_strategy", "smart")),
            fallback_policy=data.get("fallback_policy", "chain"),
            jurisdiction_strict=data.get("jurisdiction_strict", True),
            copilot_as_last_resort=data.get("copilot_as_last_resort", True),
            max_fallback_depth=data.get("max_fallback_depth", 3),
            cost_budget=float(data.get("cost_budget", math.inf)),
            latency_budget_ms=float(data.get("latency_budget_ms", math.inf)),
        )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    @staticmethod
    def serialize_history_entry(entry: _HistoryEntry) -> dict[str, Any]:
        """Serialize a single history entry."""
        return {
            "request_id": entry.request_id,
            "backend": entry.backend,
            "domains": list(entry.domains),
            "succeeded": entry.succeeded,
            "latency_ms": entry.latency_ms,
            "cost": entry.cost,
            "timestamp": entry.timestamp,
        }

    @staticmethod
    def serialize_history(history: RoutingHistory) -> list[dict[str, Any]]:
        """Serialize all entries in *history*."""
        return [
            RouterSerializer.serialize_history_entry(e)
            for e in history._entries  # noqa: SLF001 — controlled access
        ]

    @staticmethod
    def to_json(obj: Any, **kwargs: Any) -> str:
        """Convenience wrapper around :func:`json.dumps`."""
        return json.dumps(obj, default=str, **kwargs)


# ---------------------------------------------------------------------------
# Helper: map LogicalFragment to VerificationDomain
# ---------------------------------------------------------------------------

_FRAGMENT_DOMAIN_MAP: dict[LogicalFragment, set[VerificationDomain]] = {
    LogicalFragment.PROPOSITIONAL: {VerificationDomain.PROPOSITIONAL, VerificationDomain.STRUCTURAL},
    LogicalFragment.EQUALITY: {VerificationDomain.EQUALITY, VerificationDomain.STRUCTURAL},
    LogicalFragment.QUANTIFIER_FREE: {VerificationDomain.ARITHMETIC, VerificationDomain.STRUCTURAL},
    LogicalFragment.HORN: {VerificationDomain.STRUCTURAL},
    LogicalFragment.UNKNOWN: {VerificationDomain.SEMANTIC},
}


def domains_for_fragment(fragment: LogicalFragment) -> set[VerificationDomain]:
    """Map a :class:`LogicalFragment` to the verification domains it touches."""
    return set(_FRAGMENT_DOMAIN_MAP.get(fragment, {VerificationDomain.SEMANTIC}))


# ---------------------------------------------------------------------------
# Main solver router
# ---------------------------------------------------------------------------

class SolverRouter:
    """Central dispatcher for verification requests.

    The router examines each request, determines which domains it touches,
    finds backends with jurisdiction, selects the best one according to
    the active :class:`RoutingStrategy`, and returns an immutable
    :class:`RoutingDecision`.

    If the primary backend cannot handle the request the router builds a
    :class:`FallbackChain` and walks it until a capable backend is found
    or all options are exhausted.

    The copilot oracle is treated specially: it is only invoked when
    ``config.copilot_as_last_resort`` is set *and* no other backend
    can serve the request (see :class:`CopilotFallbackPolicy`).
    """

    def __init__(
        self,
        config: RouterConfiguration | None = None,
        *,
        strategy: RoutingStrategyKind | None = None,
        history: RoutingHistory | None = None,
        monitor: RouterMonitor | None = None,
        copilot_policy: CopilotFallbackPolicy | None = None,
    ) -> None:
        self._config = config or self._default_config()
        if strategy is not None:
            self._config.routing_strategy = strategy
        self._history = history or RoutingHistory()
        self._monitor = monitor or RouterMonitor()
        self._copilot_policy = copilot_policy or CopilotFallbackPolicy()
        self._strategy = _build_strategy(self._config.routing_strategy)
        self._checker = JurisdictionChecker(strict=self._config.jurisdiction_strict)

        # Wire history into SmartStrategy if applicable.
        if isinstance(self._strategy, SmartStrategy):
            self._strategy.attach_history(self._history)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------
    @property
    def config(self) -> RouterConfiguration:
        """Current router configuration."""
        return self._config

    @property
    def history(self) -> RoutingHistory:
        """Routing history tracker."""
        return self._history

    @property
    def monitor(self) -> RouterMonitor:
        """Router health monitor."""
        return self._monitor

    @property
    def strategy(self) -> RoutingStrategyKind:
        return self._config.routing_strategy

    # ------------------------------------------------------------------
    # Core routing
    # ------------------------------------------------------------------
    def route(
        self,
        fragment: SolverFragment,
        domains: set[VerificationDomain] | TrustProfile | None = None,
        requested_tier: TrustTier = TrustTier.VERIFIED,
        *,
        trust: TrustProfile | None = None,
    ) -> RoutingDecision:
        """Route a single verification request.

        Parameters
        ----------
        fragment:
            The logical fragment to be verified.
        domains:
            Explicit domain set.  If *None* the router infers domains
            from the fragment type.  For backward compatibility a
            :class:`TrustProfile` may be passed here; it will be
            interpreted as the *trust* parameter instead.
        requested_tier:
            The desired trust tier for the produced evidence.
        trust:
            Optional existing :class:`TrustProfile` to consult.

        Returns
        -------
        RoutingDecision
            An immutable record of the routing choice.
        """
        # Legacy compatibility: old callers pass TrustProfile as 2nd arg.
        if isinstance(domains, TrustProfile):
            trust = domains
            domains = None

        t0 = time.monotonic()
        request_id = uuid.uuid4().hex[:12]

        if domains is None:
            domains = domains_for_fragment(fragment.fragment)

        # If trust profile says PROPOSAL and we'd need fallback, be cautious
        if trust and trust.tier is TrustTier.PROPOSAL:
            requested_tier = min(
                requested_tier, TrustTier.PROPOSAL, key=lambda t: t.value,
            )

        # 1. Find capable backends
        capable = self.find_capable_backends(domains)

        # 2. Filter copilot if policy says last-resort
        non_copilot = [b for b in capable if b.kind is not BackendKind.COPILOT]
        primary_pool = non_copilot if (self._config.copilot_as_last_resort and non_copilot) else capable

        # 3. Select best via strategy
        best = self.select_best(primary_pool, domains, requested_tier)

        # 4. Copilot fallback?
        used_copilot = False
        if best is None and self._config.copilot_as_last_resort:
            copilot_backends = [b for b in capable if b.kind is BackendKind.COPILOT]
            if copilot_backends and self._copilot_policy.when_to_use_copilot(
                domains, other_backends_exhausted=True,
            ):
                best = copilot_backends[0]
                used_copilot = True

        # 4b. Proposal-tier guard: proposal evidence cannot silently
        #     escalate to a copilot/fallback backend.  Require review.
        if (
            trust is not None
            and trust.tier is TrustTier.PROPOSAL
            and (best is None or best.kind in {BackendKind.COPILOT, BackendKind.ORACLE})
        ):
            elapsed = (time.monotonic() - t0) * 1000.0
            self._monitor.record_routing_latency(elapsed)
            self._monitor.record_query()
            return RoutingDecision(
                request_id=request_id,
                selected_backend="review-required",
                fallback_backends=(),
                jurisdiction_check_passed=False,
                trust_ceiling=TrustTier.PROPOSAL,
                estimated_cost=0.0,
                estimated_latency=0.0,
                rationale=(
                    "proposal-tier evidence cannot silently escalate to "
                    "fallback reasoning"
                ),
            )

        # 5. Build fallback chain from remaining capable backends
        fallback_names: tuple[str, ...] = ()
        if best is not None:
            others = [b for b in capable if b.name != best.name]
            chain = FallbackChain(others[:self._config.max_fallback_depth])
            fallback_names = tuple(chain.backend_names())

        # 6. Jurisdiction check on selected backend
        jurisdiction_ok = False
        effective_ceiling = TrustTier.PROPOSAL
        if best is not None:
            jurisdiction_ok = self._checker.check(best, domains)
            effective_ceiling = self._checker.compute_effective_trust(
                best, requested_tier,
            )
            if not jurisdiction_ok:
                self._monitor.record_jurisdiction_violation()

        # 7. Cost & latency estimates
        est_cost = self.estimate_cost(best) if best else 0.0
        est_latency = self.estimate_latency(best) if best else 0.0

        # 8. Rationale
        rationale = self._build_rationale(
            best, domains, requested_tier, jurisdiction_ok, used_copilot,
        )

        # 9. Record monitoring
        elapsed = (time.monotonic() - t0) * 1000.0
        self._monitor.record_routing_latency(elapsed)
        self._monitor.record_query()
        if used_copilot:
            self._monitor.record_copilot_query()

        selected_name = best.name if best else "none"
        decision = RoutingDecision(
            request_id=request_id,
            selected_backend=selected_name,
            fallback_backends=fallback_names,
            jurisdiction_check_passed=jurisdiction_ok,
            trust_ceiling=effective_ceiling,
            estimated_cost=est_cost,
            estimated_latency=est_latency,
            rationale=rationale,
        )
        return decision

    def route_batch(
        self,
        items: Sequence[tuple[SolverFragment, set[VerificationDomain] | None, TrustTier]],
    ) -> list[RoutingDecision]:
        """Route a batch of requests sequentially.

        For parallel/optimised batching see :class:`BatchRouter`.
        """
        return [self.route(frag, doms, tier) for frag, doms, tier in items]

    # ------------------------------------------------------------------
    # Backend discovery
    # ------------------------------------------------------------------
    def find_capable_backends(
        self,
        domains: set[VerificationDomain],
    ) -> list[BackendDescriptor]:
        """Return all available backends that cover at least one *domain*.

        In strict-jurisdiction mode only backends covering *all* domains
        are returned.
        """
        available = self._config.available_backends()
        if self._config.jurisdiction_strict:
            return [b for b in available if b.covers_all(domains)]
        return [b for b in available if b.covers_any(domains)]

    def select_best(
        self,
        candidates: Sequence[BackendDescriptor],
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
    ) -> BackendDescriptor | None:
        """Delegate to the active strategy to pick the best backend."""
        return self._strategy.select(candidates, domains, requested_tier)

    def check_jurisdiction(
        self,
        backend: BackendDescriptor,
        domains: set[VerificationDomain],
    ) -> bool:
        """Check whether *backend* has jurisdiction over *domains*."""
        return self._checker.check(backend, domains)

    # ------------------------------------------------------------------
    # Cost / latency estimation
    # ------------------------------------------------------------------
    def estimate_cost(self, backend: BackendDescriptor | None) -> float:
        """Estimate cost for one query to *backend*.

        Uses historical data when available, falling back to the
        descriptor's static ``cost_per_query``.
        """
        if backend is None:
            return 0.0
        hist_entries = self._history.query_by_backend(backend.name)
        if len(hist_entries) >= 5:
            return sum(e.cost for e in hist_entries[-10:]) / min(len(hist_entries), 10)
        return backend.cost_per_query

    def estimate_latency(self, backend: BackendDescriptor | None) -> float:
        """Estimate latency in ms for one query to *backend*.

        Uses historical data when available, falling back to the
        descriptor's static ``average_latency_ms``.
        """
        if backend is None:
            return 0.0
        hist_avg = self._history.average_latency_for(backend.name)
        if hist_avg is not None:
            return hist_avg
        return backend.average_latency_ms

    # ------------------------------------------------------------------
    # Backend management
    # ------------------------------------------------------------------
    def register_backend(self, backend: BackendDescriptor) -> None:
        """Add or replace a backend in the configuration."""
        self._config.add_backend(backend)
        self._monitor.record_availability(backend.name, backend.is_available)

    def unregister_backend(self, name: str) -> bool:
        """Remove a backend by name.  Return *True* if it existed."""
        return self._config.remove_backend(name)

    def update_stats(
        self,
        request_id: str,
        backend: str,
        succeeded: bool,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        domains: Sequence[str] | None = None,
    ) -> None:
        """Record the outcome of a dispatched request.

        This feeds both :class:`RoutingHistory` and :class:`RouterMonitor`.
        """
        self._history.record(
            request_id=request_id,
            backend=backend,
            domains=domains or [],
            succeeded=succeeded,
            latency_ms=latency_ms,
            cost=cost,
        )
        self._monitor.record_cost(backend, cost)
        self._monitor.record_availability(backend, succeeded)

    # ------------------------------------------------------------------
    # Cross-subsystem integration
    # ------------------------------------------------------------------

    def orchestrated_routing(
        self,
        fragment: SolverFragment,
        domains: set[VerificationDomain] | None = None,
        requested_tier: TrustTier = TrustTier.VERIFIED,
    ) -> RoutingDecision:
        """Route with orchestration-budget awareness.

        Consults :class:`~jugeo.orchestration.controller.ResourceBudget`
        to determine whether the chosen backend's estimated cost fits
        within the remaining orchestration budget.  When the budget is
        exhausted, falls back to the cheapest available backend regardless
        of the active routing strategy.

        Parameters
        ----------
        fragment:
            The logical fragment to verify.
        domains:
            Explicit domain set; inferred from *fragment* when ``None``.
        requested_tier:
            Desired evidence trust tier.

        Returns
        -------
        RoutingDecision
            A routing decision that respects orchestration budgets.
        """
        if not _ORCHESTRATION_AVAILABLE:
            return self.route(fragment, domains, requested_tier)

        try:
            budget = _ResourceBudget()
            remaining = budget.remaining()
        except Exception:
            return self.route(fragment, domains, requested_tier)

        decision = self.route(fragment, domains, requested_tier)

        if decision.estimated_cost > remaining:
            # Budget exceeded — switch to cheapest backend
            inferred_domains = domains if domains is not None else domains_for_fragment(fragment.fragment)
            capable = self.find_capable_backends(inferred_domains)
            if capable:
                cheapest = min(capable, key=lambda b: b.cost_per_query)
                request_id = uuid.uuid4().hex[:12]
                return RoutingDecision(
                    request_id=request_id,
                    selected_backend=cheapest.name,
                    fallback_backends=decision.fallback_backends,
                    jurisdiction_check_passed=self._checker.check(cheapest, inferred_domains),
                    trust_ceiling=cheapest.trust_ceiling,
                    estimated_cost=cheapest.cost_per_query,
                    estimated_latency=cheapest.average_latency_ms,
                    rationale=(
                        f"Budget-constrained routing: remaining={remaining:.4f}, "
                        f"original cost={decision.estimated_cost:.4f}. "
                        f"Fell back to cheapest backend '{cheapest.name}'."
                    ),
                )

        return decision

    def trust_aware_dispatch(
        self,
        fragment: SolverFragment,
        domains: set[VerificationDomain] | None = None,
        requested_tier: TrustTier = TrustTier.VERIFIED,
    ) -> RoutingDecision:
        """Route with trust-algebraic filtering.

        Uses :class:`~jugeo.evidence.trust.TrustAlgebra` to compose and
        compare backend trust ceilings, ensuring that the selected backend
        can produce evidence at or above the requested tier under the
        partial-order semantics of the trust algebra.

        Parameters
        ----------
        fragment:
            The logical fragment to verify.
        domains:
            Explicit domain set; inferred from *fragment* when ``None``.
        requested_tier:
            Desired evidence trust tier.

        Returns
        -------
        RoutingDecision
            A routing decision filtered through the trust algebra.
        """
        if not _TRUST_ALGEBRA_AVAILABLE:
            return self.route(fragment, domains, requested_tier)

        try:
            algebra = _TrustAlgebra()
        except Exception:
            return self.route(fragment, domains, requested_tier)

        inferred_domains = domains if domains is not None else domains_for_fragment(fragment.fragment)
        capable = self.find_capable_backends(inferred_domains)

        # Filter backends whose trust ceiling meets the requested tier
        # under the partial-order comparison provided by TrustAlgebra.
        trust_filtered: list[BackendDescriptor] = []
        for backend in capable:
            try:
                cmp = algebra.compare(
                    backend.trust_ceiling,
                    requested_tier,
                )
                if cmp >= 0:  # backend ceiling ≥ requested tier
                    trust_filtered.append(backend)
            except Exception:
                # Incomparable levels — include conservatively
                trust_filtered.append(backend)

        if not trust_filtered:
            trust_filtered = capable  # graceful degradation

        best = self.select_best(trust_filtered, inferred_domains, requested_tier)
        if best is None:
            return self.route(fragment, domains, requested_tier)

        request_id = uuid.uuid4().hex[:12]
        jurisdiction_ok = self._checker.check(best, inferred_domains)
        return RoutingDecision(
            request_id=request_id,
            selected_backend=best.name,
            fallback_backends=tuple(
                b.name for b in trust_filtered if b.name != best.name
            )[:3],
            jurisdiction_check_passed=jurisdiction_ok,
            trust_ceiling=best.trust_ceiling,
            estimated_cost=best.cost_per_query,
            estimated_latency=best.average_latency_ms,
            rationale=(
                f"Trust-aware dispatch via TrustAlgebra: "
                f"{len(trust_filtered)}/{len(capable)} backends meet "
                f"tier {requested_tier.name}. Selected '{best.name}'."
            ),
        )

    def encoding_classified_routing(
        self,
        fragment: SolverFragment,
        requested_tier: TrustTier = TrustTier.VERIFIED,
    ) -> RoutingDecision:
        """Route using structural-frontier decidability classification.

        Classifies the formula through
        :func:`~jugeo.encodings.structural_frontier.classify_formula_fragment`
        to determine its decidability class *before* selecting a backend.
        Decidable formulas are fast-tracked to Z3; undecidable formulas are
        routed to provers or the copilot fallback.

        Parameters
        ----------
        fragment:
            The logical fragment to verify.
        requested_tier:
            Desired evidence trust tier.

        Returns
        -------
        RoutingDecision
            A routing decision informed by encoding decidability.
        """
        if not _STRUCTURAL_FRONTIER_AVAILABLE:
            return self.route(fragment, None, requested_tier)

        try:
            classification = _classify_formula_fragment(fragment.formula)
        except Exception:
            return self.route(fragment, None, requested_tier)

        # Map decidability class to preferred backend kind and domains
        try:
            decidability = classification.decidability
        except AttributeError:
            decidability = classification

        decidable_str = str(decidability).lower()
        if "decidable" in decidable_str and "un" not in decidable_str:
            # Decidable — prefer Z3
            preferred_domains = {
                VerificationDomain.STRUCTURAL,
                VerificationDomain.ARITHMETIC,
                VerificationDomain.PROPOSITIONAL,
            }
        elif "semi" in decidable_str:
            # Semi-decidable — prefer prover with timeout
            preferred_domains = {
                VerificationDomain.MECHANIZED,
                VerificationDomain.STRUCTURAL,
            }
        else:
            # Undecidable or unknown — use semantic domain, possibly copilot
            preferred_domains = {
                VerificationDomain.SEMANTIC,
                VerificationDomain.STRUCTURAL,
            }

        decision = self.route(fragment, preferred_domains, requested_tier)
        # Enrich the rationale with decidability info
        enriched_rationale = (
            f"Encoding-classified routing (decidability={decidable_str}). "
            f"{decision.rationale}"
        )
        return RoutingDecision(
            request_id=decision.request_id,
            selected_backend=decision.selected_backend,
            fallback_backends=decision.fallback_backends,
            jurisdiction_check_passed=decision.jurisdiction_check_passed,
            trust_ceiling=decision.trust_ceiling,
            estimated_cost=decision.estimated_cost,
            estimated_latency=decision.estimated_latency,
            rationale=enriched_rationale,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_rationale(
        self,
        backend: BackendDescriptor | None,
        domains: set[VerificationDomain],
        requested_tier: TrustTier,
        jurisdiction_ok: bool,
        used_copilot: bool,
    ) -> str:
        """Construct a human-readable rationale string."""
        domain_str = ", ".join(sorted(d.value for d in domains))
        if backend is None:
            return (
                f"No backend available for domains [{domain_str}] at "
                f"tier {requested_tier.name}."
            )
        parts: list[str] = [
            f"Selected '{backend.name}' ({backend.kind.value}) for "
            f"domains [{domain_str}].",
        ]
        if used_copilot:
            parts.append(
                "Copilot oracle invoked as last resort — all other "
                "backends exhausted."
            )
        if not jurisdiction_ok:
            explanation = self._checker.explain_violation(backend, domains)
            parts.append(f"WARNING: {explanation}")
        parts.append(
            f"Effective trust ceiling: {backend.effective_trust(requested_tier).name}."
        )
        parts.append(f"Strategy: {self._strategy.name()}.")
        return "  ".join(parts)

    # ------------------------------------------------------------------
    @staticmethod
    def _default_config() -> RouterConfiguration:
        """Build a sensible default configuration with common backends."""
        z3_backend = BackendDescriptor(
            name="z3-smt",
            kind=BackendKind.Z3,
            jurisdiction=frozenset({
                VerificationDomain.PROPOSITIONAL,
                VerificationDomain.EQUALITY,
                VerificationDomain.ARITHMETIC,
                VerificationDomain.STRUCTURAL,
            }),
            trust_ceiling=TrustTier.VERIFIED,
            is_available=True,
            priority=10,
            cost_per_query=0.0,
            average_latency_ms=50.0,
        )
        runtime_backend = BackendDescriptor(
            name="runtime-witness",
            kind=BackendKind.RUNTIME,
            jurisdiction=frozenset({
                VerificationDomain.HEAP,
                VerificationDomain.IDENTITY,
            }),
            trust_ceiling=TrustTier.REVIEWED,
            is_available=True,
            priority=5,
            cost_per_query=0.001,
            average_latency_ms=200.0,
        )
        copilot_backend = BackendDescriptor(
            name="copilot-oracle",
            kind=BackendKind.COPILOT,
            jurisdiction=frozenset({
                VerificationDomain.SEMANTIC,
                VerificationDomain.STRUCTURAL,
                VerificationDomain.ARITHMETIC,
                VerificationDomain.HEAP,
                VerificationDomain.IDENTITY,
                VerificationDomain.EQUALITY,
                VerificationDomain.PROPOSITIONAL,
                VerificationDomain.MECHANIZED,
            }),
            trust_ceiling=TrustTier.PROPOSAL,
            is_available=True,
            priority=1,
            cost_per_query=0.01,
            average_latency_ms=1500.0,
        )
        prover_backend = BackendDescriptor(
            name="formal-prover",
            kind=BackendKind.PROVER,
            jurisdiction=frozenset({
                VerificationDomain.MECHANIZED,
                VerificationDomain.STRUCTURAL,
            }),
            trust_ceiling=TrustTier.VERIFIED,
            is_available=True,
            priority=8,
            cost_per_query=0.05,
            average_latency_ms=5000.0,
        )
        return RouterConfiguration(
            backends=[z3_backend, runtime_backend, copilot_backend, prover_backend],
            routing_strategy=RoutingStrategyKind.SMART,
            fallback_policy="chain",
            jurisdiction_strict=False,
            copilot_as_last_resort=True,
        )

    # -- Judgment-geometric integration ------------------------------------

    def route_judgment(self, judgment: Any) -> RoutingDecision:
        r"""Route a judgment term to the best solver backend.

        In the judgment-geometric architecture, judgments are sections of the
        judgment presheaf ``\mathcal{J}``.  Each judgment has a logical domain
        (arithmetic, structural, heap, …) that determines which solver backend
        has jurisdiction.  This method infers the domain from the judgment's
        proposition type and delegates to :meth:`route`.

        Parameters
        ----------
        judgment:
            A judgment term from ``jugeo.judgments.judgment_terms``.

        Returns
        -------
        RoutingDecision
            The routing decision for this judgment.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment, Proposition
        except ImportError:
            pass

        prop = getattr(judgment, 'proposition', None)
        kind = getattr(prop, 'kind', 'semantic') if prop else 'semantic'

        domain_map = {
            'arithmetic': VerificationDomain.ARITHMETIC,
            'structural': VerificationDomain.STRUCTURAL,
            'heap': VerificationDomain.HEAP,
            'identity': VerificationDomain.IDENTITY,
            'semantic': VerificationDomain.SEMANTIC,
            'propositional': VerificationDomain.PROPOSITIONAL,
            'equality': VerificationDomain.EQUALITY,
        }
        domain = domain_map.get(str(kind), VerificationDomain.SEMANTIC)

        formula_text = getattr(prop, 'formula', str(judgment)) if prop else str(judgment)
        fragment = SolverFragment(
            formula=formula_text,
            fragment=LogicalFragment.PROPOSITIONAL,
            clauses=(formula_text,),
        )
        return self.route(fragment, domains={domain})

    @property
    def trust_routing(self) -> dict[str, Any]:
        r"""Return routing configuration partitioned by trust tier.

        In the trust algebra, each backend has a *trust ceiling* — the maximum
        trust tier its evidence may carry.  This property groups available
        backends by their ceiling, giving downstream consumers a map from
        trust tiers to available backends.

        Returns
        -------
        dict
            Keys are trust tier names, values are lists of backend names.
        """
        try:
            from jugeo.evidence.trust import TrustTier
        except ImportError:
            pass

        tier_map: dict[str, list[str]] = {}
        for b in self._config.available_backends():
            tier_name = b.trust_ceiling.name
            tier_map.setdefault(tier_name, []).append(b.name)
        return tier_map

    def orchestrated_routing(self, **kwargs: Any) -> dict[str, Any]:
        r"""Coordinate routing with the orchestration controller.

        The orchestrator manages resource budgets across verification tasks.
        This method queries the orchestrator for current budget constraints
        and returns a routing plan that respects them — ensuring the solver
        does not exceed CPU, memory, or cost budgets.

        Returns
        -------
        dict
            A routing plan with budget constraints applied.
        """
        try:
            from jugeo.orchestration.controller import (
                ResourceBudget, OrchestratorConfiguration,
            )
        except ImportError:
            return {
                "routing_strategy": self._config.routing_strategy.value,
                "backends": [b.name for b in self._config.available_backends()],
                "budget": "unconstrained",
                "orchestrator_available": False,
            }

        budget = kwargs.get('budget')
        if budget is None:
            budget = ResourceBudget()

        available = self._config.available_backends()
        filtered = [
            b for b in available
            if b.cost_per_query <= getattr(budget, 'max_cost_per_query', float('inf'))
            and b.average_latency_ms <= getattr(budget, 'max_latency_ms', float('inf'))
        ]
        return {
            "routing_strategy": self._config.routing_strategy.value,
            "backends": [b.name for b in filtered],
            "budget": str(budget),
            "orchestrator_available": True,
            "total_available": len(available),
            "budget_filtered": len(filtered),
        }

    def encoding_routing(self, encoding: Any = None) -> dict[str, Any]:
        r"""Route based on encoding decidability classification.

        The structural frontier (Theory2.tex Ch25) classifies each formula
        as INSIDE, BOUNDARY, or OUTSIDE the decidable region.  Encodings
        that lie INSIDE can be routed to Z3 directly; BOUNDARY encodings
        may need a timeout; OUTSIDE encodings should be repaired first.

        Parameters
        ----------
        encoding:
            Optional encoding from ``jugeo.encodings.structural_frontier``.

        Returns
        -------
        dict
            Routing recommendations based on decidability.
        """
        try:
            from jugeo.encodings.structural_frontier.models import (
                DecidabilityClass, DecidabilityMap, make_default_map,
            )
        except ImportError:
            return {
                "decidability": "unknown",
                "recommendation": "route_normally",
                "structural_frontier_available": False,
            }

        decidability = DecidabilityClass.UNKNOWN
        if encoding is not None:
            smt = getattr(encoding, 'z3_constraint_smt', '') or getattr(encoding, 'z3_invariant_smt', '')
            if smt:
                dm = make_default_map()
                frag_name = getattr(encoding, 'fragment', None)
                if frag_name:
                    frag_str = getattr(frag_name, 'smt_lib_name', lambda: str(frag_name))()
                    decidability = dm.fragment_assignments.get(
                        frag_str, DecidabilityClass.UNKNOWN
                    )

        recommendation = (
            "route_to_z3" if decidability == DecidabilityClass.DECIDABLE
            else "route_with_timeout" if decidability == DecidabilityClass.CONDITIONALLY_DECIDABLE
            else "repair_first" if decidability == DecidabilityClass.UNDECIDABLE
            else "route_normally"
        )
        return {
            "decidability": decidability.value,
            "recommendation": recommendation,
            "structural_frontier_available": True,
        }

    @property
    def site_partitioned_routing(self) -> dict[str, Any]:
        r"""Partition routing by geometric site coordinates.

        In the judgment site ``(\mathbf{C}, J)`` different coordinates may
        require different solver backends (e.g. arithmetic coordinates go to
        Z3, heap coordinates to a runtime witness).  This property builds a
        partition map from coordinate kinds to preferred backends.

        Returns
        -------
        dict
            Mapping from coordinate kind to backend names.
        """
        try:
            from jugeo.geometry.site import CoordinateKind
        except ImportError:
            pass

        domain_to_backends: dict[str, list[str]] = {}
        for b in self._config.available_backends():
            for d in b.jurisdiction:
                domain_to_backends.setdefault(d.value, []).append(b.name)
        return {
            "partitioned_by": "verification_domain",
            "partitions": domain_to_backends,
            "total_backends": len(self._config.available_backends()),
        }


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SolverRoute:
    """Thin routing record kept for backward-compatibility.

    New code should use :class:`RoutingDecision` directly.
    """

    engine: str
    fragment: LogicalFragment
    reason: str


def legacy_route(
    fragment: SolverFragment,
    trust: TrustProfile | None = None,
    *,
    router: SolverRouter | None = None,
) -> SolverRoute:
    """Backward-compatible entry-point matching the old ``SolverRouter.route`` API.

    Internally delegates to the full :class:`SolverRouter` and converts
    the :class:`RoutingDecision` back to a :class:`SolverRoute`.
    """
    r = router or SolverRouter()
    decision = r.route(fragment, trust=trust)
    return SolverRoute(
        engine=decision.selected_backend,
        fragment=fragment.fragment,
        reason=decision.rationale,
    )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "BackendKind",
    "RoutingStrategyKind",
    "VerificationDomain",
    # Core data
    "RoutingDecision",
    "BackendDescriptor",
    "RouterConfiguration",
    # Strategies
    "RoutingStrategy",
    "CheapestStrategy",
    "FastestStrategy",
    "MostTrustedStrategy",
    "RoundRobinStrategy",
    "SmartStrategy",
    # Infrastructure
    "RoutingHistory",
    "JurisdictionChecker",
    "FallbackChain",
    "CopilotFallbackPolicy",
    "RouterMonitor",
    "BatchRouter",
    "RouterSerializer",
    # Main router
    "SolverRouter",
    # Legacy
    "SolverRoute",
    "legacy_route",
    # Helpers
    "domains_for_fragment",
]

# copilot: shared-core marker for future LLM orchestration.
