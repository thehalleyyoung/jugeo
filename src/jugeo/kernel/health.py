"""Comprehensive health monitoring for JuGeo kernel services.

JuGeo treats type-checking as sheaf-theoretic geometry.  Health monitoring
must therefore track not only "is the service up" but whether the trust
algebra is consistent, whether evidence channels are producing within their
jurisdictions, whether obstructions are being properly retained, and whether
copilot oracle proposals are staying within trust ceilings.

Health snapshots remain public-facing projections — they keep their declared
scope narrow and never claim more than the current lifecycle and service
graph justify.  They are safe to surface in diagnostics, API responses, and
copilot-oriented orchestration UIs.

The monitoring subsystem is organized around *dimensions* that correspond to
the structural invariants of the sheaf-theoretic model:

* **Trust consistency** — the ordered algebra axioms (idempotent join,
  monotone demotion, no silent promotion) continue to hold.
* **Evidence flow** — channels produce within their declared jurisdictions
  and do not exceed escalation limits.
* **Obstruction retention** — obstructions discovered during descent are
  kept according to retention policy so that repair reasoning has the data
  it needs.
* **Solver responsiveness** — Z3 sessions respond within budget.
* **Copilot connectivity** — LLM oracle connections are alive, within rate
  limits, and proposals stay at or below trust ceilings.
* **Descent progress** — gluing is making forward progress and not stalled.
"""

from __future__ import annotations

import abc
import collections
import json
import logging
import math
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from jugeo.kernel.lifecycle import LifecycleController, LifecycleState
from jugeo.kernel.services import ServiceGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class HealthStatus(str, Enum):
    """Coarse health classification.

    Values form a degradation lattice: HEALTHY > RECOVERING > DEGRADED >
    UNHEALTHY > UNKNOWN.  The monitor computes an overall status by taking
    the *meet* (worst) across all active dimensions.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    RECOVERING = "recovering"

    # Keep backward-compatible alias so existing callers that reference
    # ``HealthStatus.FAILED`` continue to work without changes.
    FAILED = "unhealthy"

    @staticmethod
    def severity_order() -> Sequence[HealthStatus]:
        """Return statuses ordered from worst to best."""
        return (
            HealthStatus.UNKNOWN,
            HealthStatus.UNHEALTHY,
            HealthStatus.DEGRADED,
            HealthStatus.RECOVERING,
            HealthStatus.HEALTHY,
        )

    def is_worse_than(self, other: HealthStatus) -> bool:
        """Return ``True`` if *self* is strictly worse than *other*."""
        order = HealthStatus.severity_order()
        return order.index(self) < order.index(other)

    def meet(self, other: HealthStatus) -> HealthStatus:
        """Lattice meet — return the worse of two statuses."""
        order = HealthStatus.severity_order()
        return self if order.index(self) <= order.index(other) else other


class HealthDimension(str, Enum):
    """Orthogonal health dimensions tracked by the monitor.

    Each dimension maps to a structural invariant in the sheaf-theoretic
    model.  Dimensions are independent: a degraded solver does not imply
    degraded trust consistency.
    """

    SERVICE_AVAILABILITY = "service_availability"
    TRUST_CONSISTENCY = "trust_consistency"
    EVIDENCE_FLOW = "evidence_flow"
    OBSTRUCTION_RETENTION = "obstruction_retention"
    SOLVER_RESPONSIVENESS = "solver_responsiveness"
    COPILOT_CONNECTIVITY = "copilot_connectivity"
    DESCENT_PROGRESS = "descent_progress"
    MEMORY_PRESSURE = "memory_pressure"
    CERTIFICATE_VALIDITY = "certificate_validity"

    @property
    def display_label(self) -> str:
        """Human-readable label for dashboard rendering."""
        return self.value.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HealthIndicator:
    """Single health measurement along one dimension.

    *value* is a float in ``[0, 1]`` where ``0`` is the worst possible
    reading and ``1`` is fully healthy.  The ``status`` field provides the
    coarse classification; *value* gives a continuous signal useful for
    trend analysis and anomaly detection.
    """

    dimension: HealthDimension
    status: HealthStatus
    message: str
    value: float
    timestamp: float
    details: Mapping[str, Any] = field(default_factory=dict)
    check_duration_ms: float = 0.0

    def __post_init__(self) -> None:
        """Clamp *value* into ``[0, 1]``."""
        clamped = max(0.0, min(1.0, self.value))
        object.__setattr__(self, "value", clamped)

    def is_healthy(self) -> bool:
        """Return ``True`` when this indicator reports full health."""
        return self.status is HealthStatus.HEALTHY

    def is_actionable(self) -> bool:
        """Return ``True`` when this indicator warrants operator attention."""
        return self.status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED)

    def with_status(self, status: HealthStatus, message: str | None = None) -> HealthIndicator:
        """Return a copy with an updated status and optional message."""
        return HealthIndicator(
            dimension=self.dimension,
            status=status,
            message=message if message is not None else self.message,
            value=self.value,
            timestamp=self.timestamp,
            details=self.details,
            check_duration_ms=self.check_duration_ms,
        )


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Lightweight projection of subsystem health.

    Retained for backward compatibility with existing diagnostics and
    copilot orchestration surfaces that consume snapshots rather than full
    indicator records.
    """

    subsystem: str
    status: HealthStatus
    details: Mapping[str, Any] = field(default_factory=dict)
    scope: str = "shared-core"


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Aggregated health report across all monitored dimensions.

    Each report carries a unique ``report_id`` and an optional link to
    its ``previous_report_id`` so that consumers can construct a causal
    chain of health observations.  The *degradation_reasons* and
    *recovery_suggestions* fields give actionable context to operators and
    to copilot repair agents that consume health telemetry.
    """

    overall_status: HealthStatus
    indicators: tuple[HealthIndicator, ...]
    generated_at: float
    report_id: str
    previous_report_id: str | None = None
    degradation_reasons: tuple[str, ...] = ()
    recovery_suggestions: tuple[str, ...] = ()

    @property
    def dimension_statuses(self) -> Mapping[HealthDimension, HealthStatus]:
        """Map each dimension to its most recent status."""
        return {ind.dimension: ind.status for ind in self.indicators}

    @property
    def worst_indicator(self) -> HealthIndicator | None:
        """Return the indicator with the lowest value, or ``None``."""
        if not self.indicators:
            return None
        return min(self.indicators, key=lambda i: i.value)

    def filter_by_status(self, status: HealthStatus) -> tuple[HealthIndicator, ...]:
        """Return indicators matching *status*."""
        return tuple(i for i in self.indicators if i.status is status)

    def filter_by_dimension(self, dim: HealthDimension) -> tuple[HealthIndicator, ...]:
        """Return indicators for *dim*."""
        return tuple(i for i in self.indicators if i.dimension is dim)

    def is_fully_healthy(self) -> bool:
        """Return ``True`` when every indicator is healthy."""
        return all(i.is_healthy() for i in self.indicators)


# ---------------------------------------------------------------------------
# Abstract health check
# ---------------------------------------------------------------------------


class HealthCheck(abc.ABC):
    """Abstract base for health check implementations.

    Subclasses define the dimension they probe and implement the ``check``
    method which returns a single ``HealthIndicator``.  The base class
    provides timing scaffolding so that every indicator records its own
    ``check_duration_ms``.
    """

    @property
    @abc.abstractmethod
    def dimension(self) -> HealthDimension:
        """The health dimension this check evaluates."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short human-readable name for logging and dashboards."""

    @abc.abstractmethod
    def check(self) -> HealthIndicator:
        """Execute the health check and return an indicator."""

    def timed_check(self) -> HealthIndicator:
        """Run ``check`` and stamp the result with wall-clock duration."""
        t0 = time.monotonic()
        try:
            indicator = self.check()
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000.0
            logger.warning("Health check %s failed: %s", self.name, exc)
            return self._make_indicator(
                HealthStatus.UNHEALTHY,
                f"Check raised: {exc}",
                0.0,
                details={"exception": str(exc)},
                duration_ms=duration_ms,
            )
        duration_ms = (time.monotonic() - t0) * 1000.0
        return HealthIndicator(
            dimension=indicator.dimension,
            status=indicator.status,
            message=indicator.message,
            value=indicator.value,
            timestamp=indicator.timestamp,
            details=indicator.details,
            check_duration_ms=duration_ms,
        )

    # -- helpers -----------------------------------------------------------

    def _make_indicator(
        self,
        status: HealthStatus,
        message: str,
        value: float,
        details: Mapping[str, Any] | None = None,
        duration_ms: float = 0.0,
    ) -> HealthIndicator:
        """Build a ``HealthIndicator`` stamped with current time."""
        return HealthIndicator(
            dimension=self.dimension,
            status=status,
            message=message,
            value=value,
            timestamp=time.time(),
            details=details or {},
            check_duration_ms=duration_ms,
        )

    def _status_from_value(self, value: float) -> HealthStatus:
        """Derive a coarse status from a continuous ``[0, 1]`` value.

        Thresholds:
            >= 0.9  → HEALTHY
            >= 0.7  → RECOVERING
            >= 0.4  → DEGRADED
            <  0.4  → UNHEALTHY
        """
        if value >= 0.9:
            return HealthStatus.HEALTHY
        if value >= 0.7:
            return HealthStatus.RECOVERING
        if value >= 0.4:
            return HealthStatus.DEGRADED
        return HealthStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# Concrete health checks
# ---------------------------------------------------------------------------


class TrustAlgebraHealthCheck(HealthCheck):
    """Verify that the ordered trust algebra axioms hold.

    The trust algebra requires:
    * Join is idempotent, commutative, and associative.
    * Demotion is monotone: ``demote(a) <= a``.
    * No silent promotion — strengthening requires explicit acknowledgement.
    * Challenge produces a result no stronger than the original.

    This check samples the current trust state and verifies these
    invariants hold for every observed profile pair.
    """

    def __init__(self, trust_profiles: Sequence[Any] | None = None) -> None:
        self._profiles: Sequence[Any] = trust_profiles or ()

    @property
    def dimension(self) -> HealthDimension:
        return HealthDimension.TRUST_CONSISTENCY

    @property
    def name(self) -> str:
        return "trust-algebra"

    def check(self) -> HealthIndicator:
        """Run algebra axiom checks over current trust profiles."""
        if not self._profiles:
            return self._make_indicator(
                HealthStatus.UNKNOWN,
                "No trust profiles available for axiom verification.",
                0.5,
                details={"profiles_count": 0},
            )

        violations: list[str] = []
        checked = 0

        for profile in self._profiles:
            checked += 1
            violations.extend(self._verify_idempotent_join(profile))
            violations.extend(self._verify_demotion_monotone(profile))
            violations.extend(self._verify_no_silent_promotion(profile))

        for i, p1 in enumerate(self._profiles):
            for p2 in self._profiles[i + 1 :]:
                checked += 1
                violations.extend(self._verify_commutative_join(p1, p2))
                violations.extend(self._verify_associative_join_pair(p1, p2))

        total_checks = max(checked, 1)
        violation_ratio = len(violations) / total_checks
        value = max(0.0, 1.0 - violation_ratio)
        status = self._status_from_value(value)

        msg = (
            f"Trust algebra: {total_checks} axiom checks, "
            f"{len(violations)} violation(s)."
        )
        return self._make_indicator(
            status,
            msg,
            value,
            details={
                "profiles_count": len(self._profiles),
                "checks_run": total_checks,
                "violations": violations[:10],
            },
        )

    # -- axiom verifiers ---------------------------------------------------

    def _verify_idempotent_join(self, profile: Any) -> list[str]:
        """Join of a profile with itself must equal the original."""
        if not hasattr(profile, "join"):
            return []
        try:
            joined = profile.join(profile)
            if joined != profile:
                return [f"Join not idempotent for {profile!r}"]
        except Exception as exc:
            return [f"Idempotent join raised for {profile!r}: {exc}"]
        return []

    def _verify_commutative_join(self, p1: Any, p2: Any) -> list[str]:
        """``p1.join(p2)`` must equal ``p2.join(p1)``."""
        if not (hasattr(p1, "join") and hasattr(p2, "join")):
            return []
        try:
            r1 = p1.join(p2)
            r2 = p2.join(p1)
            if r1 != r2:
                return [f"Join not commutative: {p1!r} ⊕ {p2!r}"]
        except Exception as exc:
            return [f"Commutativity check raised: {exc}"]
        return []

    def _verify_associative_join_pair(self, p1: Any, p2: Any) -> list[str]:
        """Check partial associativity for the available pair."""
        if not (hasattr(p1, "join") and hasattr(p2, "join")):
            return []
        try:
            left = p1.join(p2).join(p1)
            right = p1.join(p2.join(p1))
            if left != right:
                return [f"Join not associative: ({p1!r} ⊕ {p2!r}) ⊕ {p1!r}"]
        except Exception:
            pass
        return []

    def _verify_demotion_monotone(self, profile: Any) -> list[str]:
        """Demoted profile must not exceed the original."""
        if not hasattr(profile, "demote"):
            return []
        try:
            demoted = profile.demote(reason="health-check")
            if hasattr(demoted, "tier") and hasattr(profile, "tier"):
                if demoted.tier > profile.tier:
                    return [f"Demotion not monotone: {profile!r} → {demoted!r}"]
        except Exception as exc:
            return [f"Demotion check raised for {profile!r}: {exc}"]
        return []

    def _verify_no_silent_promotion(self, profile: Any) -> list[str]:
        """Promotion without explicit flag must fail or be a no-op."""
        if not hasattr(profile, "promote"):
            return []
        try:
            promoted = profile.promote()
            if hasattr(promoted, "tier") and hasattr(profile, "tier"):
                if promoted.tier > profile.tier:
                    return [
                        f"Silent promotion detected: {profile!r} → {promoted!r}"
                    ]
        except Exception:
            # Raising is the *expected* behavior — no silent promotion.
            pass
        return []


class EvidenceFlowHealthCheck(HealthCheck):
    """Verify that evidence channels are producing within their jurisdictions.

    Each evidence channel declares a jurisdiction — a set of admissible
    queries and evidence families.  This check verifies that:
    * Channels have produced at least one record recently.
    * No channel has exceeded its jurisdiction boundary.
    * Production rates are within expected bounds.
    """

    def __init__(
        self,
        channel_stats: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        min_production_rate: float = 0.01,
        max_jurisdiction_violations: int = 0,
    ) -> None:
        self._channel_stats = channel_stats or {}
        self._min_rate = min_production_rate
        self._max_violations = max_jurisdiction_violations

    @property
    def dimension(self) -> HealthDimension:
        return HealthDimension.EVIDENCE_FLOW

    @property
    def name(self) -> str:
        return "evidence-flow"

    def check(self) -> HealthIndicator:
        """Evaluate evidence flow across all registered channels."""
        if not self._channel_stats:
            return self._make_indicator(
                HealthStatus.UNKNOWN,
                "No channel statistics available.",
                0.5,
                details={"channels_count": 0},
            )

        issues: list[str] = []
        channel_health: dict[str, float] = {}

        for name, stats in self._channel_stats.items():
            ch_value = self._evaluate_channel(name, stats, issues)
            channel_health[name] = ch_value

        if not channel_health:
            return self._make_indicator(
                HealthStatus.UNKNOWN, "No channels evaluated.", 0.5,
            )

        overall_value = statistics.mean(channel_health.values())
        status = self._status_from_value(overall_value)
        return self._make_indicator(
            status,
            f"Evidence flow: {len(channel_health)} channel(s), "
            f"{len(issues)} issue(s).",
            overall_value,
            details={
                "channel_health": channel_health,
                "issues": issues[:10],
                "channels_evaluated": len(channel_health),
            },
        )

    def _evaluate_channel(
        self, name: str, stats: Mapping[str, Any], issues: list[str],
    ) -> float:
        """Score a single channel and append any issues found."""
        score = 1.0

        # Check production rate.
        rate = stats.get("production_rate", 0.0)
        if rate < self._min_rate:
            issues.append(
                f"Channel '{name}' production rate {rate:.4f} "
                f"below minimum {self._min_rate:.4f}."
            )
            score -= 0.4

        # Check jurisdiction violations.
        violations = stats.get("jurisdiction_violations", 0)
        if violations > self._max_violations:
            issues.append(
                f"Channel '{name}' has {violations} jurisdiction violation(s)."
            )
            score -= 0.3 * min(violations, 3)

        # Check staleness — last_production_timestamp should be recent.
        last_ts = stats.get("last_production_timestamp", 0.0)
        staleness = time.time() - last_ts if last_ts > 0 else float("inf")
        if staleness > 300:
            issues.append(
                f"Channel '{name}' stale for {staleness:.0f}s."
            )
            score -= 0.3

        return max(0.0, min(1.0, score))

    def get_stale_channels(self, threshold_seconds: float = 300.0) -> list[str]:
        """Return names of channels that have not produced recently."""
        now = time.time()
        stale: list[str] = []
        for name, stats in self._channel_stats.items():
            last_ts = stats.get("last_production_timestamp", 0.0)
            if last_ts <= 0 or (now - last_ts) > threshold_seconds:
                stale.append(name)
        return stale

    def get_jurisdiction_violators(self) -> list[str]:
        """Return channel names with jurisdiction violations."""
        return [
            name
            for name, stats in self._channel_stats.items()
            if stats.get("jurisdiction_violations", 0) > self._max_violations
        ]


class SolverHealthCheck(HealthCheck):
    """Verify that Z3 solver sessions are responsive.

    Sends a trivial satisfiability probe and measures latency.  If the
    solver does not respond within the configured timeout, the dimension
    is marked UNHEALTHY.
    """

    def __init__(
        self,
        solver_session: Any | None = None,
        *,
        timeout_ms: float = 5000.0,
        probe_formula: str = "p & (not p => q)",
    ) -> None:
        self._session = solver_session
        self._timeout_ms = timeout_ms
        self._probe_formula = probe_formula

    @property
    def dimension(self) -> HealthDimension:
        return HealthDimension.SOLVER_RESPONSIVENESS

    @property
    def name(self) -> str:
        return "solver-responsiveness"

    def check(self) -> HealthIndicator:
        """Probe the solver and measure response latency."""
        if self._session is None:
            return self._make_indicator(
                HealthStatus.UNKNOWN,
                "No solver session configured.",
                0.5,
                details={"session": None},
            )

        if getattr(self._session, "closed", False):
            return self._make_indicator(
                HealthStatus.UNHEALTHY,
                "Solver session is closed.",
                0.0,
                details={"closed": True},
            )

        t0 = time.monotonic()
        try:
            result = self._probe_solver()
            elapsed_ms = (time.monotonic() - t0) * 1000.0
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            return self._make_indicator(
                HealthStatus.UNHEALTHY,
                f"Solver probe failed: {exc}",
                0.0,
                details={"error": str(exc), "elapsed_ms": elapsed_ms},
                duration_ms=elapsed_ms,
            )

        value = self._latency_to_value(elapsed_ms)
        status = self._status_from_value(value)
        return self._make_indicator(
            status,
            f"Solver responded in {elapsed_ms:.1f}ms "
            f"(outcome={getattr(result, 'outcome', 'N/A')}).",
            value,
            details={
                "elapsed_ms": elapsed_ms,
                "timeout_ms": self._timeout_ms,
                "outcome": str(getattr(result, "outcome", "N/A")),
            },
            duration_ms=elapsed_ms,
        )

    def _probe_solver(self) -> Any:
        """Send a probe to the solver session."""
        if hasattr(self._session, "solve"):
            from jugeo.solver.fragments import SolverFragment

            fragment = SolverFragment.classify(self._probe_formula)
            return self._session.solve(fragment)
        raise RuntimeError("Solver session does not expose a solve() method.")

    def _latency_to_value(self, elapsed_ms: float) -> float:
        """Map response latency to a ``[0, 1]`` health value."""
        if elapsed_ms <= 0:
            return 1.0
        if elapsed_ms >= self._timeout_ms:
            return 0.0
        # Exponential decay: fast responses → value near 1.
        ratio = elapsed_ms / self._timeout_ms
        return max(0.0, 1.0 - ratio ** 0.5)


class CopilotHealthCheck(HealthCheck):
    """Verify that the copilot oracle connection is alive and rate-bounded.

    The copilot oracle (LLM backend) is a first-class evidence channel
    whose proposals enter the system at ``PROPOSAL`` trust tier.  This
    check verifies:
    * The copilot connection is reachable.
    * Rate limits have not been exhausted.
    * Proposals are not exceeding trust ceilings.
    """

    def __init__(
        self,
        copilot_state: Mapping[str, Any] | None = None,
        *,
        rate_limit_ceiling: int = 1000,
        trust_ceiling_tier: int = 1,
    ) -> None:
        self._state = copilot_state or {}
        self._rate_ceiling = rate_limit_ceiling
        self._trust_ceiling = trust_ceiling_tier

    @property
    def dimension(self) -> HealthDimension:
        return HealthDimension.COPILOT_CONNECTIVITY

    @property
    def name(self) -> str:
        return "copilot-connectivity"

    def check(self) -> HealthIndicator:
        """Evaluate copilot oracle connectivity and compliance."""
        if not self._state:
            return self._make_indicator(
                HealthStatus.UNKNOWN,
                "No copilot state available for health evaluation.",
                0.5,
                details={"copilot_configured": False},
            )

        issues: list[str] = []
        score = 1.0

        score = self._check_connectivity(score, issues)
        score = self._check_rate_limits(score, issues)
        score = self._check_trust_ceiling(score, issues)
        score = self._check_proposal_quality(score, issues)

        value = max(0.0, min(1.0, score))
        status = self._status_from_value(value)
        return self._make_indicator(
            status,
            f"Copilot oracle: {len(issues)} issue(s) detected."
            if issues
            else "Copilot oracle: all checks passed.",
            value,
            details={
                "issues": issues,
                "rate_usage": self._state.get("rate_usage", 0),
                "rate_ceiling": self._rate_ceiling,
                "trust_ceiling_tier": self._trust_ceiling,
                "connected": self._state.get("connected", False),
            },
        )

    def _check_connectivity(self, score: float, issues: list[str]) -> float:
        """Verify the copilot connection is alive."""
        connected = self._state.get("connected", False)
        if not connected:
            issues.append("Copilot oracle is not connected.")
            return score - 0.5
        last_heartbeat = self._state.get("last_heartbeat", 0.0)
        if last_heartbeat > 0 and (time.time() - last_heartbeat) > 120:
            issues.append("Copilot heartbeat stale — last seen >120s ago.")
            return score - 0.2
        return score

    def _check_rate_limits(self, score: float, issues: list[str]) -> float:
        """Verify rate limits have not been exhausted."""
        usage = self._state.get("rate_usage", 0)
        if usage >= self._rate_ceiling:
            issues.append(
                f"Copilot rate limit exhausted: {usage}/{self._rate_ceiling}."
            )
            return score - 0.4
        if usage >= self._rate_ceiling * 0.8:
            issues.append(
                f"Copilot rate usage high: {usage}/{self._rate_ceiling} "
                f"({100 * usage / max(self._rate_ceiling, 1):.0f}%)."
            )
            return score - 0.15
        return score

    def _check_trust_ceiling(self, score: float, issues: list[str]) -> float:
        """Verify proposals are not exceeding the trust ceiling tier."""
        max_tier_seen = self._state.get("max_proposal_tier", 0)
        if max_tier_seen > self._trust_ceiling:
            issues.append(
                f"Copilot proposal exceeded trust ceiling: "
                f"tier {max_tier_seen} > ceiling {self._trust_ceiling}. "
                f"No silent promotion allowed."
            )
            return score - 0.5
        return score

    def _check_proposal_quality(self, score: float, issues: list[str]) -> float:
        """Spot-check proposal acceptance rate as a quality signal."""
        total = self._state.get("proposals_total", 0)
        accepted = self._state.get("proposals_accepted", 0)
        if total > 10:
            acceptance_rate = accepted / total
            if acceptance_rate < 0.1:
                issues.append(
                    f"Copilot proposal acceptance rate very low: "
                    f"{acceptance_rate:.1%} ({accepted}/{total})."
                )
                return score - 0.15
        return score

    def get_rate_usage_fraction(self) -> float:
        """Return the fraction of rate limit consumed."""
        usage = self._state.get("rate_usage", 0)
        return usage / max(self._rate_ceiling, 1)


class DescentHealthCheck(HealthCheck):
    """Verify that sheaf descent is making forward progress.

    Descent stalls happen when the gluing engine repeatedly fails on the
    same overlaps without producing new obstructions or repair hints.
    This check tracks descent attempts and flags stalls.
    """

    def __init__(
        self,
        descent_stats: Mapping[str, Any] | None = None,
        *,
        stall_threshold: int = 5,
        max_obstruction_rank: int = 100,
    ) -> None:
        self._stats = descent_stats or {}
        self._stall_threshold = stall_threshold
        self._max_rank = max_obstruction_rank

    @property
    def dimension(self) -> HealthDimension:
        return HealthDimension.DESCENT_PROGRESS

    @property
    def name(self) -> str:
        return "descent-progress"

    def check(self) -> HealthIndicator:
        """Evaluate descent progress against stall thresholds."""
        if not self._stats:
            return self._make_indicator(
                HealthStatus.UNKNOWN,
                "No descent statistics available.",
                0.5,
                details={"descent_active": False},
            )

        issues: list[str] = []
        score = 1.0

        score = self._check_stall(score, issues)
        score = self._check_obstruction_growth(score, issues)
        score = self._check_gluing_success_rate(score, issues)
        score = self._check_repair_hint_coverage(score, issues)

        value = max(0.0, min(1.0, score))
        status = self._status_from_value(value)
        return self._make_indicator(
            status,
            f"Descent: {len(issues)} issue(s)."
            if issues
            else "Descent progressing normally.",
            value,
            details={
                "issues": issues,
                "consecutive_failures": self._stats.get("consecutive_failures", 0),
                "obstruction_rank": self._stats.get("obstruction_rank", 0),
                "gluing_attempts": self._stats.get("gluing_attempts", 0),
            },
        )

    def _check_stall(self, score: float, issues: list[str]) -> float:
        """Detect descent stalls from consecutive failures."""
        consecutive = self._stats.get("consecutive_failures", 0)
        if consecutive >= self._stall_threshold:
            issues.append(
                f"Descent stalled: {consecutive} consecutive failures "
                f"(threshold {self._stall_threshold})."
            )
            return score - 0.5
        if consecutive >= self._stall_threshold // 2:
            issues.append(
                f"Descent slowing: {consecutive} consecutive failures."
            )
            return score - 0.2
        return score

    def _check_obstruction_growth(self, score: float, issues: list[str]) -> float:
        """Flag unbounded obstruction accumulation."""
        rank = self._stats.get("obstruction_rank", 0)
        if rank > self._max_rank:
            issues.append(
                f"Obstruction rank {rank} exceeds limit {self._max_rank}."
            )
            return score - 0.3
        return score

    def _check_gluing_success_rate(self, score: float, issues: list[str]) -> float:
        """Verify gluing success rate is acceptable."""
        attempts = self._stats.get("gluing_attempts", 0)
        successes = self._stats.get("gluing_successes", 0)
        if attempts > 5:
            rate = successes / attempts
            if rate < 0.2:
                issues.append(
                    f"Gluing success rate critically low: "
                    f"{rate:.1%} ({successes}/{attempts})."
                )
                return score - 0.3
        return score

    def _check_repair_hint_coverage(self, score: float, issues: list[str]) -> float:
        """Verify obstructions have repair hints attached."""
        total_obstructions = self._stats.get("total_obstructions", 0)
        with_hints = self._stats.get("obstructions_with_hints", 0)
        if total_obstructions > 3:
            coverage = with_hints / total_obstructions
            if coverage < 0.5:
                issues.append(
                    f"Repair hint coverage low: {coverage:.1%} "
                    f"({with_hints}/{total_obstructions})."
                )
                return score - 0.15
        return score


class ObstructionHealthCheck(HealthCheck):
    """Verify that obstructions are retained according to policy.

    Obstructions discovered during descent are the raw material for repair
    reasoning — both human and copilot-driven.  Premature eviction defeats
    the purpose of the descent engine.  This check verifies:
    * Retention counts stay within policy bounds.
    * No obstructions were silently dropped.
    * Eviction follows the declared priority order.
    """

    def __init__(
        self,
        retention_stats: Mapping[str, Any] | None = None,
        *,
        min_retention_count: int = 0,
        max_retention_count: int = 10000,
        max_silent_drops: int = 0,
    ) -> None:
        self._stats = retention_stats or {}
        self._min_retention = min_retention_count
        self._max_retention = max_retention_count
        self._max_silent_drops = max_silent_drops

    @property
    def dimension(self) -> HealthDimension:
        return HealthDimension.OBSTRUCTION_RETENTION

    @property
    def name(self) -> str:
        return "obstruction-retention"

    def check(self) -> HealthIndicator:
        """Evaluate obstruction retention policy compliance."""
        if not self._stats:
            return self._make_indicator(
                HealthStatus.UNKNOWN,
                "No obstruction retention statistics available.",
                0.5,
                details={"retention_tracking": False},
            )

        issues: list[str] = []
        score = 1.0

        score = self._check_retention_bounds(score, issues)
        score = self._check_silent_drops(score, issues)
        score = self._check_eviction_order(score, issues)
        score = self._check_age_distribution(score, issues)

        value = max(0.0, min(1.0, score))
        status = self._status_from_value(value)
        return self._make_indicator(
            status,
            f"Obstruction retention: {len(issues)} issue(s)."
            if issues
            else "Obstruction retention policy satisfied.",
            value,
            details={
                "issues": issues,
                "current_count": self._stats.get("current_count", 0),
                "silent_drops": self._stats.get("silent_drops", 0),
            },
        )

    def _check_retention_bounds(self, score: float, issues: list[str]) -> float:
        """Verify retention count is within policy bounds."""
        count = self._stats.get("current_count", 0)
        if count < self._min_retention:
            issues.append(
                f"Obstruction count {count} below minimum {self._min_retention}."
            )
            return score - 0.2
        if count > self._max_retention:
            issues.append(
                f"Obstruction count {count} exceeds limit {self._max_retention}."
            )
            return score - 0.3
        return score

    def _check_silent_drops(self, score: float, issues: list[str]) -> float:
        """Detect obstructions silently dropped outside eviction policy."""
        drops = self._stats.get("silent_drops", 0)
        if drops > self._max_silent_drops:
            issues.append(
                f"{drops} obstruction(s) silently dropped "
                f"(limit {self._max_silent_drops})."
            )
            return score - 0.5
        return score

    def _check_eviction_order(self, score: float, issues: list[str]) -> float:
        """Verify eviction follows priority order."""
        out_of_order = self._stats.get("out_of_order_evictions", 0)
        if out_of_order > 0:
            issues.append(
                f"{out_of_order} eviction(s) violated priority order."
            )
            return score - 0.2 * min(out_of_order, 3)
        return score

    def _check_age_distribution(self, score: float, issues: list[str]) -> float:
        """Flag unhealthy age distributions in retained obstructions."""
        max_age = self._stats.get("max_obstruction_age_s", 0.0)
        if max_age > 3600:
            issues.append(
                f"Oldest retained obstruction is {max_age:.0f}s old "
                f"(>1 hour). Consider repair or explicit dismissal."
            )
            return score - 0.1
        return score


# ---------------------------------------------------------------------------
# Alert rules and manager
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HealthAlertRule:
    """Configurable alerting rule evaluated against health reports.

    The ``condition`` predicate receives a ``HealthReport`` and returns
    ``True`` when the alert should fire.  ``cooldown_seconds`` prevents
    the same rule from firing repeatedly within a short window.
    """

    rule_id: str
    description: str
    condition: Callable[[HealthReport], bool]
    severity: str = "warning"
    cooldown_seconds: float = 300.0
    notification_channel: str = "default"
    message_template: str = "Health alert: {rule_id} — {description}"

    def evaluate(self, report: HealthReport) -> bool:
        """Return ``True`` if the rule condition is met."""
        try:
            return self.condition(report)
        except Exception as exc:
            logger.warning("Alert rule %s evaluation failed: %s", self.rule_id, exc)
            return False

    def format_message(self, report: HealthReport) -> str:
        """Render the alert message from the template."""
        return self.message_template.format(
            rule_id=self.rule_id,
            description=self.description,
            overall_status=report.overall_status.value,
            report_id=report.report_id,
        )

    def is_critical(self) -> bool:
        """Return ``True`` for critical-severity rules."""
        return self.severity == "critical"

    def matches_channel(self, channel: str) -> bool:
        """Return ``True`` if this rule targets the given channel."""
        return self.notification_channel == channel


@dataclass(slots=True)
class _AlertState:
    """Internal mutable state for a single alert rule."""

    firing: bool = False
    last_fired_at: float = 0.0
    fire_count: int = 0
    last_resolved_at: float = 0.0


class HealthAlertManager:
    """Manages alert rules and evaluates them against health reports.

    Alert state is tracked per rule so that cooldowns and deduplication
    work correctly.  Consumers subscribe to notifications via callbacks.
    The copilot orchestration layer can register its own rules to get
    notified when proposals are rejected above a threshold.
    """

    def __init__(self) -> None:
        self._rules: dict[str, HealthAlertRule] = {}
        self._state: dict[str, _AlertState] = {}
        self._subscribers: list[Callable[[str, HealthAlertRule, HealthReport], None]] = []

    def register_rule(self, rule: HealthAlertRule) -> None:
        """Register a new alert rule."""
        self._rules[rule.rule_id] = rule
        self._state.setdefault(rule.rule_id, _AlertState())
        logger.debug("Registered alert rule: %s", rule.rule_id)

    def unregister_rule(self, rule_id: str) -> bool:
        """Remove a rule.  Returns ``True`` if it existed."""
        removed = self._rules.pop(rule_id, None) is not None
        self._state.pop(rule_id, None)
        return removed

    def subscribe(self, callback: Callable[[str, HealthAlertRule, HealthReport], None]) -> None:
        """Subscribe to alert notifications.

        The callback receives ``(event, rule, report)`` where *event* is
        ``'firing'`` or ``'resolved'``.
        """
        self._subscribers.append(callback)

    def evaluate(self, report: HealthReport) -> list[tuple[str, HealthAlertRule]]:
        """Evaluate all rules against *report*.

        Returns a list of ``(event, rule)`` tuples for rules that changed
        state (either started firing or resolved).
        """
        now = time.time()
        events: list[tuple[str, HealthAlertRule]] = []

        for rule_id, rule in self._rules.items():
            state = self._state[rule_id]
            triggered = rule.evaluate(report)

            if triggered and not state.firing:
                if (now - state.last_fired_at) >= rule.cooldown_seconds:
                    state.firing = True
                    state.last_fired_at = now
                    state.fire_count += 1
                    events.append(("firing", rule))
                    self._notify("firing", rule, report)
            elif not triggered and state.firing:
                state.firing = False
                state.last_resolved_at = now
                events.append(("resolved", rule))
                self._notify("resolved", rule, report)

        return events

    def get_firing_rules(self) -> list[HealthAlertRule]:
        """Return all rules currently in the firing state."""
        return [
            self._rules[rid]
            for rid, st in self._state.items()
            if st.firing and rid in self._rules
        ]

    def get_rule_state(self, rule_id: str) -> Mapping[str, Any]:
        """Return current state of a rule for diagnostic display."""
        state = self._state.get(rule_id)
        if state is None:
            return {"exists": False}
        return {
            "exists": True,
            "firing": state.firing,
            "fire_count": state.fire_count,
            "last_fired_at": state.last_fired_at,
            "last_resolved_at": state.last_resolved_at,
        }

    def reset(self) -> None:
        """Reset all alert state — used during recovery procedures."""
        for state in self._state.values():
            state.firing = False
        logger.info("Alert manager state reset.")

    def _notify(self, event: str, rule: HealthAlertRule, report: HealthReport) -> None:
        """Dispatch notification to all subscribers."""
        for callback in self._subscribers:
            try:
                callback(event, rule, report)
            except Exception as exc:
                logger.warning(
                    "Alert subscriber failed for %s/%s: %s",
                    event,
                    rule.rule_id,
                    exc,
                )


# ---------------------------------------------------------------------------
# Trend analysis
# ---------------------------------------------------------------------------


class HealthTrend:
    """Track health values over time with sliding-window statistics.

    Maintains a bounded deque of ``(timestamp, value)`` observations per
    dimension and computes mean, variance, trend direction, and basic
    anomaly detection.  Useful for the health dashboard sparklines and
    for copilot repair heuristics that react to gradual degradation.
    """

    def __init__(self, window_size: int = 100) -> None:
        self._window_size = max(2, window_size)
        self._series: dict[HealthDimension, Deque[tuple[float, float]]] = (
            collections.defaultdict(lambda: collections.deque(maxlen=self._window_size))
        )

    def record(self, indicator: HealthIndicator) -> None:
        """Append an indicator observation to the trend series."""
        self._series[indicator.dimension].append(
            (indicator.timestamp, indicator.value)
        )

    def record_many(self, indicators: Sequence[HealthIndicator]) -> None:
        """Record a batch of indicators."""
        for ind in indicators:
            self.record(ind)

    def mean(self, dim: HealthDimension) -> float:
        """Return the mean value for *dim* over the window."""
        values = self._values(dim)
        return statistics.mean(values) if values else 0.5

    def variance(self, dim: HealthDimension) -> float:
        """Return the variance for *dim* over the window."""
        values = self._values(dim)
        if len(values) < 2:
            return 0.0
        return statistics.variance(values)

    def trend_direction(self, dim: HealthDimension) -> str:
        """Return ``'improving'``, ``'stable'``, or ``'degrading'``.

        Uses simple linear regression slope over the window.
        """
        series = list(self._series.get(dim, []))
        if len(series) < 3:
            return "stable"

        slope = self._linear_slope(series)
        if slope > 0.005:
            return "improving"
        if slope < -0.005:
            return "degrading"
        return "stable"

    def is_anomaly(self, dim: HealthDimension, value: float, z_threshold: float = 2.5) -> bool:
        """Return ``True`` if *value* is an outlier relative to the window.

        Uses a simple Z-score check against window mean and standard
        deviation.
        """
        values = self._values(dim)
        if len(values) < 5:
            return False
        mu = statistics.mean(values)
        sigma = statistics.stdev(values)
        if sigma < 1e-9:
            return abs(value - mu) > 1e-9
        z = abs(value - mu) / sigma
        return z > z_threshold

    def sparkline(self, dim: HealthDimension, length: int = 20) -> list[float]:
        """Return the most recent *length* values for sparkline rendering."""
        values = self._values(dim)
        return values[-length:]

    def dimensions_tracked(self) -> list[HealthDimension]:
        """Return dimensions that have at least one observation."""
        return [d for d, s in self._series.items() if s]

    def window_fill_ratio(self, dim: HealthDimension) -> float:
        """Fraction of the window that has been filled for *dim*."""
        series = self._series.get(dim)
        if not series:
            return 0.0
        return len(series) / self._window_size

    def clear(self, dim: HealthDimension | None = None) -> None:
        """Clear observations for *dim*, or all if ``None``."""
        if dim is None:
            self._series.clear()
        else:
            self._series.pop(dim, None)

    # -- internal ----------------------------------------------------------

    def _values(self, dim: HealthDimension) -> list[float]:
        """Extract the value component from the time series."""
        return [v for _, v in self._series.get(dim, [])]

    @staticmethod
    def _linear_slope(series: list[tuple[float, float]]) -> float:
        """Compute slope via least-squares regression."""
        n = len(series)
        if n < 2:
            return 0.0
        t0 = series[0][0]
        xs = [t - t0 for t, _ in series]
        ys = [v for _, v in series]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        if abs(den) < 1e-12:
            return 0.0
        return num / den


# ---------------------------------------------------------------------------
# Dashboard data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HealthDashboardData:
    """Structured payload for rendering a health dashboard.

    Aggregates current-status-per-dimension, trend sparklines, active
    alerts, and recent events into a single transferable object suitable
    for the diagnostics API and copilot orchestration UIs.
    """

    dimension_statuses: Mapping[str, str]
    dimension_values: Mapping[str, float]
    trend_sparklines: Mapping[str, list[float]]
    trend_directions: Mapping[str, str]
    active_alerts: tuple[Mapping[str, Any], ...]
    recent_events: tuple[Mapping[str, Any], ...]
    generated_at: float
    overall_status: str

    @staticmethod
    def build(
        report: HealthReport,
        trend: HealthTrend,
        alert_manager: HealthAlertManager,
        recent_events: Sequence[Mapping[str, Any]] | None = None,
    ) -> HealthDashboardData:
        """Construct dashboard data from current monitoring state."""
        dim_statuses: dict[str, str] = {}
        dim_values: dict[str, float] = {}
        sparklines: dict[str, list[float]] = {}
        directions: dict[str, str] = {}

        for ind in report.indicators:
            key = ind.dimension.value
            dim_statuses[key] = ind.status.value
            dim_values[key] = ind.value
            sparklines[key] = trend.sparkline(ind.dimension)
            directions[key] = trend.trend_direction(ind.dimension)

        active = tuple(
            {
                "rule_id": r.rule_id,
                "description": r.description,
                "severity": r.severity,
                "channel": r.notification_channel,
            }
            for r in alert_manager.get_firing_rules()
        )

        return HealthDashboardData(
            dimension_statuses=dim_statuses,
            dimension_values=dim_values,
            trend_sparklines=sparklines,
            trend_directions=directions,
            active_alerts=active,
            recent_events=tuple(recent_events or ()),
            generated_at=time.time(),
            overall_status=report.overall_status.value,
        )

    def get_dimension_summary(self, dimension: str) -> Mapping[str, Any]:
        """Return a summary dict for a single dimension."""
        return {
            "status": self.dimension_statuses.get(dimension, "unknown"),
            "value": self.dimension_values.get(dimension, 0.5),
            "trend": self.trend_directions.get(dimension, "stable"),
            "sparkline": self.trend_sparklines.get(dimension, []),
        }

    def unhealthy_dimensions(self) -> list[str]:
        """Return dimensions currently marked unhealthy or degraded."""
        return [
            d
            for d, s in self.dimension_statuses.items()
            if s in ("unhealthy", "degraded")
        ]

    def alert_count(self) -> int:
        """Return the number of active alerts."""
        return len(self.active_alerts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON encoding."""
        return {
            "dimension_statuses": dict(self.dimension_statuses),
            "dimension_values": dict(self.dimension_values),
            "trend_sparklines": dict(self.trend_sparklines),
            "trend_directions": dict(self.trend_directions),
            "active_alerts": [dict(a) for a in self.active_alerts],
            "recent_events": [dict(e) for e in self.recent_events],
            "generated_at": self.generated_at,
            "overall_status": self.overall_status,
        }


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class HealthSerializer:
    """Serialize and deserialize health reports to/from JSON and dicts.

    Provides round-trip fidelity for ``HealthReport``, ``HealthIndicator``,
    and ``HealthDashboardData`` — the three main types that cross process
    boundaries in the diagnostics API and copilot telemetry feeds.
    """

    @staticmethod
    def indicator_to_dict(indicator: HealthIndicator) -> dict[str, Any]:
        """Convert a ``HealthIndicator`` to a plain dict."""
        return {
            "dimension": indicator.dimension.value,
            "status": indicator.status.value,
            "message": indicator.message,
            "value": indicator.value,
            "timestamp": indicator.timestamp,
            "details": dict(indicator.details),
            "check_duration_ms": indicator.check_duration_ms,
        }

    @staticmethod
    def indicator_from_dict(data: Mapping[str, Any]) -> HealthIndicator:
        """Reconstruct a ``HealthIndicator`` from a plain dict."""
        return HealthIndicator(
            dimension=HealthDimension(data["dimension"]),
            status=HealthStatus(data["status"]),
            message=data["message"],
            value=float(data["value"]),
            timestamp=float(data["timestamp"]),
            details=data.get("details", {}),
            check_duration_ms=float(data.get("check_duration_ms", 0.0)),
        )

    @classmethod
    def report_to_dict(cls, report: HealthReport) -> dict[str, Any]:
        """Convert a ``HealthReport`` to a plain dict."""
        return {
            "overall_status": report.overall_status.value,
            "indicators": [
                cls.indicator_to_dict(ind) for ind in report.indicators
            ],
            "generated_at": report.generated_at,
            "report_id": report.report_id,
            "previous_report_id": report.previous_report_id,
            "degradation_reasons": list(report.degradation_reasons),
            "recovery_suggestions": list(report.recovery_suggestions),
        }

    @classmethod
    def report_from_dict(cls, data: Mapping[str, Any]) -> HealthReport:
        """Reconstruct a ``HealthReport`` from a plain dict."""
        indicators = tuple(
            cls.indicator_from_dict(d) for d in data.get("indicators", [])
        )
        return HealthReport(
            overall_status=HealthStatus(data["overall_status"]),
            indicators=indicators,
            generated_at=float(data["generated_at"]),
            report_id=data["report_id"],
            previous_report_id=data.get("previous_report_id"),
            degradation_reasons=tuple(data.get("degradation_reasons", ())),
            recovery_suggestions=tuple(data.get("recovery_suggestions", ())),
        )

    @classmethod
    def report_to_json(cls, report: HealthReport) -> str:
        """Serialize a ``HealthReport`` to a JSON string."""
        return json.dumps(cls.report_to_dict(report), indent=2, default=str)

    @classmethod
    def report_from_json(cls, text: str) -> HealthReport:
        """Deserialize a ``HealthReport`` from a JSON string."""
        return cls.report_from_dict(json.loads(text))

    @staticmethod
    def dashboard_to_json(dashboard: HealthDashboardData) -> str:
        """Serialize ``HealthDashboardData`` to a JSON string."""
        return json.dumps(dashboard.to_dict(), indent=2, default=str)

    @staticmethod
    def dashboard_from_json(text: str) -> HealthDashboardData:
        """Deserialize ``HealthDashboardData`` from a JSON string."""
        data = json.loads(text)
        return HealthDashboardData(
            dimension_statuses=data["dimension_statuses"],
            dimension_values=data["dimension_values"],
            trend_sparklines=data["trend_sparklines"],
            trend_directions=data["trend_directions"],
            active_alerts=tuple(data.get("active_alerts", ())),
            recent_events=tuple(data.get("recent_events", ())),
            generated_at=float(data["generated_at"]),
            overall_status=data["overall_status"],
        )

    @classmethod
    def snapshot_to_dict(cls, snapshot: HealthSnapshot) -> dict[str, Any]:
        """Convert a ``HealthSnapshot`` to a plain dict."""
        return {
            "subsystem": snapshot.subsystem,
            "status": snapshot.status.value,
            "details": dict(snapshot.details),
            "scope": snapshot.scope,
        }

    @classmethod
    def snapshot_from_dict(cls, data: Mapping[str, Any]) -> HealthSnapshot:
        """Reconstruct a ``HealthSnapshot`` from a plain dict."""
        return HealthSnapshot(
            subsystem=data["subsystem"],
            status=HealthStatus(data["status"]),
            details=data.get("details", {}),
            scope=data.get("scope", "shared-core"),
        )


# ---------------------------------------------------------------------------
# Main monitoring orchestrator
# ---------------------------------------------------------------------------


class HealthMonitor:
    """Central health monitoring orchestrator.

    Manages a registry of ``HealthCheck`` instances, runs them on demand
    or periodically, aggregates results into ``HealthReport`` objects,
    feeds them to the ``HealthAlertManager`` and ``HealthTrend`` tracker,
    and stores a bounded history of reports.

    Also retains backward compatibility with the original simple interface
    that produces ``HealthSnapshot`` tuples from a ``ServiceGraph`` and
    ``LifecycleController`` — see ``collect_snapshots()``.
    """

    def __init__(
        self,
        graph: ServiceGraph | None = None,
        lifecycle: LifecycleController | None = None,
        *,
        history_limit: int = 200,
        trend_window: int = 100,
    ) -> None:
        self._graph = graph
        self._lifecycle = lifecycle
        self._checks: dict[str, HealthCheck] = {}
        self._history: Deque[HealthReport] = collections.deque(maxlen=history_limit)
        self._trend = HealthTrend(window_size=trend_window)
        self._alert_manager = HealthAlertManager()
        self._subscribers: list[Callable[[HealthReport], None]] = []
        self._last_report_id: str | None = None

    # -- registration ------------------------------------------------------

    def register_check(self, check: HealthCheck) -> None:
        """Register a ``HealthCheck`` instance by its ``name``."""
        self._checks[check.name] = check
        logger.debug("Registered health check: %s", check.name)

    def unregister_check(self, name: str) -> bool:
        """Remove a check.  Returns ``True`` if it existed."""
        return self._checks.pop(name, None) is not None

    def registered_checks(self) -> list[str]:
        """Return names of all registered checks."""
        return list(self._checks.keys())

    # -- execution ---------------------------------------------------------

    def run_all_checks(self) -> HealthReport:
        """Execute every registered check and build an aggregated report.

        Indicators are collected, the overall status is computed, and the
        report is pushed to the alert manager, trend tracker, subscribers,
        and history buffer.
        """
        indicators: list[HealthIndicator] = []
        for check in self._checks.values():
            indicator = check.timed_check()
            indicators.append(indicator)

        overall = self.compute_overall_status(indicators)
        reasons = self._collect_degradation_reasons(indicators)
        suggestions = self._suggest_recovery(indicators)

        report = HealthReport(
            overall_status=overall,
            indicators=tuple(indicators),
            generated_at=time.time(),
            report_id=uuid.uuid4().hex[:16],
            previous_report_id=self._last_report_id,
            degradation_reasons=tuple(reasons),
            recovery_suggestions=tuple(suggestions),
        )

        self._last_report_id = report.report_id
        self._history.append(report)
        self._trend.record_many(indicators)
        self._alert_manager.evaluate(report)
        self._notify_subscribers(report)

        return report

    def run_check(self, name: str) -> HealthIndicator | None:
        """Execute a single named check.  Returns ``None`` if not found."""
        check = self._checks.get(name)
        if check is None:
            logger.warning("Health check not found: %s", name)
            return None
        indicator = check.timed_check()
        self._trend.record(indicator)
        return indicator

    # -- queries -----------------------------------------------------------

    def get_latest_report(self) -> HealthReport | None:
        """Return the most recent report, or ``None``."""
        return self._history[-1] if self._history else None

    def get_history(self, limit: int | None = None) -> list[HealthReport]:
        """Return recent reports, newest first."""
        reports = list(reversed(self._history))
        if limit is not None:
            reports = reports[:limit]
        return reports

    def get_trend(self) -> HealthTrend:
        """Return the trend tracker for external inspection."""
        return self._trend

    def get_alert_manager(self) -> HealthAlertManager:
        """Return the alert manager for rule registration."""
        return self._alert_manager

    # -- subscriptions -----------------------------------------------------

    def subscribe_to_changes(self, callback: Callable[[HealthReport], None]) -> None:
        """Register a callback invoked after each ``run_all_checks``."""
        self._subscribers.append(callback)

    # -- status computation ------------------------------------------------

    @staticmethod
    def compute_overall_status(indicators: Sequence[HealthIndicator]) -> HealthStatus:
        """Compute the lattice meet of all indicator statuses.

        If no indicators are present, the overall status is UNKNOWN.
        """
        if not indicators:
            return HealthStatus.UNKNOWN
        result = HealthStatus.HEALTHY
        for ind in indicators:
            result = result.meet(ind.status)
        return result

    # -- backward compatibility --------------------------------------------

    def collect(self) -> tuple[HealthSnapshot, ...]:
        """Legacy snapshot collection for backward compatibility.

        Produces ``HealthSnapshot`` tuples from the service graph and
        lifecycle controller, matching the original simple interface.
        """
        return self.collect_snapshots()

    def collect_snapshots(self) -> tuple[HealthSnapshot, ...]:
        """Build snapshot tuples from service graph and lifecycle state."""
        if self._graph is None or self._lifecycle is None:
            return ()
        base_status = (
            HealthStatus.UNHEALTHY
            if self._lifecycle.state is LifecycleState.FAILED
            else HealthStatus.HEALTHY
        )
        snapshots: list[HealthSnapshot] = []
        for name, binding in self._graph.bindings.items():
            deps_met = all(
                dep in self._graph.bindings for dep in binding.dependencies
            )
            status = base_status if deps_met else HealthStatus.DEGRADED
            snapshots.append(
                HealthSnapshot(
                    subsystem=name,
                    status=status,
                    details={
                        "dependencies": list(binding.dependencies),
                        "authority": binding.authority.name,
                        "lifecycle": self._lifecycle.state.value,
                    },
                )
            )
        return tuple(snapshots)

    # -- internal ----------------------------------------------------------

    def _collect_degradation_reasons(
        self, indicators: Sequence[HealthIndicator],
    ) -> list[str]:
        """Extract human-readable degradation reasons from indicators."""
        reasons: list[str] = []
        for ind in indicators:
            if ind.status in (HealthStatus.DEGRADED, HealthStatus.UNHEALTHY):
                reasons.append(
                    f"[{ind.dimension.display_label}] {ind.message}"
                )
        return reasons

    def _suggest_recovery(
        self, indicators: Sequence[HealthIndicator],
    ) -> list[str]:
        """Generate recovery suggestions based on degraded dimensions."""
        suggestions: list[str] = []
        for ind in indicators:
            if ind.status is HealthStatus.UNHEALTHY:
                suggestions.extend(self._recovery_for(ind))
            elif ind.status is HealthStatus.DEGRADED:
                suggestions.extend(self._recovery_for(ind, severity="degraded"))
        return suggestions

    @staticmethod
    def _recovery_for(
        indicator: HealthIndicator, severity: str = "unhealthy",
    ) -> list[str]:
        """Map a degraded indicator to actionable recovery suggestions."""
        dim = indicator.dimension
        prefix = "CRITICAL" if severity == "unhealthy" else "WARNING"

        recovery_map: dict[HealthDimension, list[str]] = {
            HealthDimension.TRUST_CONSISTENCY: [
                f"{prefix}: Trust algebra violation detected. "
                "Audit recent trust profile mutations and verify no silent "
                "promotions occurred.",
            ],
            HealthDimension.EVIDENCE_FLOW: [
                f"{prefix}: Evidence channel issue. Check channel "
                "jurisdictions and production rates.",
            ],
            HealthDimension.SOLVER_RESPONSIVENESS: [
                f"{prefix}: Solver unresponsive. Consider restarting the "
                "Z3 session or reducing formula complexity.",
            ],
            HealthDimension.COPILOT_CONNECTIVITY: [
                f"{prefix}: Copilot oracle issue. Verify LLM endpoint "
                "connectivity and check rate-limit quotas.",
            ],
            HealthDimension.DESCENT_PROGRESS: [
                f"{prefix}: Descent stalled. Review obstruction log and "
                "consider manual repair or cover refinement.",
            ],
            HealthDimension.OBSTRUCTION_RETENTION: [
                f"{prefix}: Obstruction retention policy violated. "
                "Check eviction configuration and silent-drop counters.",
            ],
            HealthDimension.MEMORY_PRESSURE: [
                f"{prefix}: Memory pressure elevated. Consider evicting "
                "stale cache entries or reducing window sizes.",
            ],
            HealthDimension.CERTIFICATE_VALIDITY: [
                f"{prefix}: Certificate validity issue. Re-verify "
                "evidence certificates or refresh expired ones.",
            ],
            HealthDimension.SERVICE_AVAILABILITY: [
                f"{prefix}: Service availability issue. Check lifecycle "
                "state and dependency resolution.",
            ],
        }
        return recovery_map.get(dim, [f"{prefix}: {dim.value} issue."])

    def _notify_subscribers(self, report: HealthReport) -> None:
        """Dispatch report to all subscribers."""
        for callback in self._subscribers:
            try:
                callback(report)
            except Exception as exc:
                logger.warning("Health subscriber failed: %s", exc)


# ---------------------------------------------------------------------------
# Backward-compatible module-level helpers
# ---------------------------------------------------------------------------


def collect_health(
    graph: ServiceGraph,
    lifecycle: LifecycleController,
) -> tuple[HealthSnapshot, ...]:
    """Collect health snapshots from a service graph and lifecycle.

    This is the original public API preserved for backward compatibility.
    New consumers should prefer ``HealthMonitor.run_all_checks()`` for
    the richer ``HealthReport`` interface.
    """
    return HealthMonitor(graph=graph, lifecycle=lifecycle).collect_snapshots()


def render_health_summary(snapshots: tuple[HealthSnapshot, ...]) -> str:
    """Render a one-line semicolon-delimited health summary string.

    Suitable for log lines, CLI output, and lightweight copilot status
    indicators that need a single string projection of system health.
    """
    return "; ".join(
        f"{snapshot.subsystem}:{snapshot.status.value}"
        for snapshot in snapshots
    )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def build_default_monitor(
    graph: ServiceGraph | None = None,
    lifecycle: LifecycleController | None = None,
    *,
    trust_profiles: Sequence[Any] | None = None,
    channel_stats: Mapping[str, Mapping[str, Any]] | None = None,
    solver_session: Any | None = None,
    copilot_state: Mapping[str, Any] | None = None,
    descent_stats: Mapping[str, Any] | None = None,
    retention_stats: Mapping[str, Any] | None = None,
) -> HealthMonitor:
    """Create a ``HealthMonitor`` with all standard checks pre-registered.

    This is the recommended entry-point for production deployments and
    copilot orchestration integration.  Each check is configured with
    sensible defaults that can be overridden via the keyword arguments.
    """
    monitor = HealthMonitor(graph=graph, lifecycle=lifecycle)

    monitor.register_check(TrustAlgebraHealthCheck(trust_profiles))
    monitor.register_check(EvidenceFlowHealthCheck(channel_stats))
    monitor.register_check(SolverHealthCheck(solver_session))
    monitor.register_check(CopilotHealthCheck(copilot_state))
    monitor.register_check(DescentHealthCheck(descent_stats))
    monitor.register_check(ObstructionHealthCheck(retention_stats))

    return monitor


__all__ = [
    "HealthStatus",
    "HealthDimension",
    "HealthIndicator",
    "HealthSnapshot",
    "HealthReport",
    "HealthCheck",
    "TrustAlgebraHealthCheck",
    "EvidenceFlowHealthCheck",
    "SolverHealthCheck",
    "CopilotHealthCheck",
    "DescentHealthCheck",
    "ObstructionHealthCheck",
    "HealthAlertRule",
    "HealthAlertManager",
    "HealthTrend",
    "HealthDashboardData",
    "HealthSerializer",
    "HealthMonitor",
    "collect_health",
    "render_health_summary",
    "build_default_monitor",
    # Cross-subsystem health checks
    "geometry_health",
    "evidence_health",
    "solver_health",
]


# ---------------------------------------------------------------------------
# Cross-subsystem health checks
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry import site as _geo_h_site, descent as _geo_h_descent  # type: ignore[import]
    _GEOMETRY_HEALTH_AVAILABLE = True
except ImportError:
    _geo_h_site = None  # type: ignore[assignment]
    _geo_h_descent = None  # type: ignore[assignment]
    _GEOMETRY_HEALTH_AVAILABLE = False

try:
    from jugeo.evidence import trust as _ev_h_trust, channels as _ev_h_channels  # type: ignore[import]
    _EVIDENCE_HEALTH_AVAILABLE = True
except ImportError:
    _ev_h_trust = None  # type: ignore[assignment]
    _ev_h_channels = None  # type: ignore[assignment]
    _EVIDENCE_HEALTH_AVAILABLE = False

try:
    from jugeo.solver import session as _solver_h_session  # type: ignore[import]
    _SOLVER_HEALTH_AVAILABLE = True
except ImportError:
    _solver_h_session = None  # type: ignore[assignment]
    _SOLVER_HEALTH_AVAILABLE = False


def geometry_health() -> HealthSnapshot:
    """Health check for the geometry subsystem from ``jugeo.geometry``.

    Queries site topology and descent engine health, returning a
    :class:`HealthSnapshot` summarising the geometry subsystem status.

    Returns
    -------
    HealthSnapshot
        Snapshot with ``subsystem="geometry"`` and aggregated status.
    """
    if not _GEOMETRY_HEALTH_AVAILABLE:
        return HealthSnapshot(
            subsystem="geometry",
            status=HealthStatus.UNKNOWN,
            details={"error": "jugeo.geometry subsystem is not installed"},
        )
    details: Dict[str, Any] = {}
    status = HealthStatus.HEALTHY
    try:
        if hasattr(_geo_h_site, "health_check"):
            site_health = _geo_h_site.health_check()
            details["site"] = site_health
        if hasattr(_geo_h_descent, "health_check"):
            descent_health = _geo_h_descent.health_check()
            details["descent"] = descent_health
    except Exception as exc:  # noqa: BLE001
        status = HealthStatus.DEGRADED
        details["error"] = str(exc)
    return HealthSnapshot(subsystem="geometry", status=status, details=details)


def evidence_health() -> HealthSnapshot:
    """Health check for the evidence subsystem from ``jugeo.evidence``.

    Queries trust algebra consistency and evidence channel health,
    returning a :class:`HealthSnapshot` summarising the evidence subsystem.

    Returns
    -------
    HealthSnapshot
        Snapshot with ``subsystem="evidence"`` and aggregated status.
    """
    if not _EVIDENCE_HEALTH_AVAILABLE:
        return HealthSnapshot(
            subsystem="evidence",
            status=HealthStatus.UNKNOWN,
            details={"error": "jugeo.evidence subsystem is not installed"},
        )
    details: Dict[str, Any] = {}
    status = HealthStatus.HEALTHY
    try:
        if hasattr(_ev_h_trust, "health_check"):
            trust_health = _ev_h_trust.health_check()
            details["trust"] = trust_health
        if hasattr(_ev_h_channels, "health_check"):
            channels_health = _ev_h_channels.health_check()
            details["channels"] = channels_health
    except Exception as exc:  # noqa: BLE001
        status = HealthStatus.DEGRADED
        details["error"] = str(exc)
    return HealthSnapshot(subsystem="evidence", status=status, details=details)


def solver_health() -> HealthSnapshot:
    """Health check for the solver subsystem from ``jugeo.solver``.

    Queries Z3 session health including responsiveness and memory usage,
    returning a :class:`HealthSnapshot` summarising the solver subsystem.

    Returns
    -------
    HealthSnapshot
        Snapshot with ``subsystem="solver"`` and aggregated status.
    """
    if not _SOLVER_HEALTH_AVAILABLE:
        return HealthSnapshot(
            subsystem="solver",
            status=HealthStatus.UNKNOWN,
            details={"error": "jugeo.solver subsystem is not installed"},
        )
    details: Dict[str, Any] = {}
    status = HealthStatus.HEALTHY
    try:
        if hasattr(_solver_h_session, "health_check"):
            session_health = _solver_h_session.health_check()
            details["session"] = session_health
    except Exception as exc:  # noqa: BLE001
        status = HealthStatus.DEGRADED
        details["error"] = str(exc)
    return HealthSnapshot(subsystem="solver", status=status, details=details)
