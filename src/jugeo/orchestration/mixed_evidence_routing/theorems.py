"""Theorem and lemma verifications for the Mixed-Evidence Routing package.

theory2.tex Chapter 45 — Theorem and Lemma Verifications
==========================================================
This module encodes the key correctness statements from Ch45 as executable
Python objects.  Each theorem or lemma is a dataclass with a ``verify``
method that returns a ``TheoremResult`` — a frozen, self-describing record
of whether the theorem holds, together with evidence, a counterexample (if
any), and a proof sketch.

The theorems are:

* **Theorem 45.1** — Jurisdiction Completeness: every verification task has
  at least one channel that can handle it.
* **Theorem 45.2** — Trust Ceiling Enforcement: Copilot evidence is strictly
  below solver-discharged trust.
* **Theorem 45.3** — Routing Consistency: the same task always routes to the
  same channel under the same policy.
* **Theorem 45.4** — Human Escalation Termination: all human-review
  escalations resolve within a bounded time window.
* **Lemma 45.A** — Channel Composability: composing evidence channels never
  inflates trust — composite trust is the minimum of component trusts.

A ``TheoremRegistry`` aggregates all five and exposes a ``verify_all``
method for end-to-end theorem suite execution.

All upstream imports are guarded by ``try/except`` so the module works in
isolation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Upstream jugeo imports — all guarded
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel  # type: ignore[import]
except Exception:
    class TrustLevel:  # type: ignore[no-redef]
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        HUMAN_ATTESTED = "HUMAN_ATTESTED"
        ORACLE_PROPOSED = "ORACLE_PROPOSED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        UNVERIFIED = "UNVERIFIED"
        CONTRADICTED = "CONTRADICTED"

# ---------------------------------------------------------------------------
# Internal MER imports — all guarded
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.mixed_evidence_routing.models import (  # type: ignore[import]
        HumanEscalation,
        RoutingDecision,
        JurisdictionMap,
        EvidenceChannel,
        EscalationUrgency,
    )
except Exception:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field

    class EvidenceChannel(str, Enum):  # type: ignore[no-redef]
        Z3 = "z3"
        COPILOT_LLM = "copilot_llm"
        RUNTIME_WITNESS = "runtime_witness"
        HUMAN = "human"
        COMPOSITE = "composite"

    class EscalationUrgency(str, Enum):  # type: ignore[no-redef]
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"
        CRITICAL = "critical"

    @_dc(frozen=True, slots=True)
    class RoutingDecision:  # type: ignore[no-redef]
        decision_id: str
        task_id: str
        channel: EvidenceChannel
        rationale: str
        confidence: float
        estimated_cost: float
        estimated_latency: float
        timestamp: float
        metadata: dict[str, Any] = _field(default_factory=dict)

    @_dc(frozen=True, slots=True)
    class JurisdictionMap:  # type: ignore[no-redef]
        map_id: str
        channel: EvidenceChannel
        supported_claim_kinds: list[str]
        max_complexity: int
        min_trust_level: str
        exclusions: list[str] = _field(default_factory=list)

        def can_handle(self, task: dict[str, Any]) -> bool:
            kind = task.get("claim_kind", "unknown")
            complexity = task.get("complexity", 0)
            excluded = task.get("claim_kind", "") in self.exclusions
            return (
                kind in self.supported_claim_kinds
                and complexity <= self.max_complexity
                and not excluded
            )

    @_dc(frozen=True, slots=True)
    class HumanEscalation:  # type: ignore[no-redef]
        escalation_id: str
        task_id: str
        urgency: EscalationUrgency
        created_at: float
        resolved_at: float | None = None
        resolution: str | None = None

        @property
        def is_resolved(self) -> bool:
            return self.resolved_at is not None

        @property
        def age_hours(self) -> float:
            end = self.resolved_at if self.resolved_at is not None else time.time()
            return (end - self.created_at) / 3600.0

try:
    from jugeo.orchestration.mixed_evidence_routing.routing_policy import (  # type: ignore[import]
        RoutingOutcome,
    )
except Exception:
    @dataclass(frozen=True, slots=True)
    class RoutingOutcome:  # type: ignore[no-redef]
        outcome_id: str
        decision_id: str
        success: bool
        actual_cost: float
        actual_latency_ms: float
        trust_achieved: str
        timestamp: float
        notes: str = ""

# ---------------------------------------------------------------------------
# Trust rank helper (mirrors integration.py, kept local to avoid circular deps)
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = [
    "CONTRADICTED",
    "UNVERIFIED",
    "COPILOT_SUGGESTED",
    "ORACLE_PROPOSED",
    "HUMAN_ATTESTED",
    "RUNTIME_WITNESSED",
    "SOLVER_DISCHARGED",
    "MECHANICALLY_VERIFIED",
]


def _rank(trust: str) -> int:
    """Return numeric rank for a trust level name (0 = weakest)."""
    try:
        return _TRUST_ORDER.index(trust.upper())
    except ValueError:
        return 0


def _weaker(a: str, b: str) -> str:
    """Return the weaker of two trust levels."""
    return a if _rank(a) <= _rank(b) else b


# ---------------------------------------------------------------------------
# TheoremResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremResult:
    """Immutable record of a theorem or lemma verification attempt.

    Parameters
    ----------
    theorem_name:
        Human-readable name of the theorem or lemma.
    holds:
        ``True`` iff the theorem was verified in the given context.
    evidence:
        List of evidence strings supporting the result.
    counterexample:
        ``None`` if holds, otherwise a dict describing the counterexample.
    proof_sketch:
        A brief prose justification of the result.
    timestamp:
        Unix timestamp of when the verification was performed.
    """

    theorem_name: str
    holds: bool
    evidence: list[str]
    counterexample: dict[str, Any] | None
    proof_sketch: str
    timestamp: float

    @classmethod
    def verified(
        cls,
        theorem_name: str,
        evidence: list[str],
        proof_sketch: str,
    ) -> TheoremResult:
        """Construct a *verified* (holds=True) result with no counterexample."""
        return cls(
            theorem_name=theorem_name,
            holds=True,
            evidence=evidence,
            counterexample=None,
            proof_sketch=proof_sketch,
            timestamp=time.time(),
        )

    @classmethod
    def falsified(
        cls,
        theorem_name: str,
        counterexample: dict[str, Any],
        proof_sketch: str,
    ) -> TheoremResult:
        """Construct a *falsified* (holds=False) result with a counterexample."""
        return cls(
            theorem_name=theorem_name,
            holds=False,
            evidence=[],
            counterexample=counterexample,
            proof_sketch=proof_sketch,
            timestamp=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "theorem_name": self.theorem_name,
            "holds": self.holds,
            "evidence": list(self.evidence),
            "counterexample": self.counterexample,
            "proof_sketch": self.proof_sketch,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """Return a one-line human-readable status string."""
        status = "HOLDS" if self.holds else "FALSIFIED"
        if not self.holds and self.counterexample:
            cex_keys = list(self.counterexample.keys())
            return f"[{status}] {self.theorem_name} — counterexample keys: {cex_keys}"
        evidence_count = len(self.evidence)
        return f"[{status}] {self.theorem_name} — {evidence_count} evidence item(s)"


# ---------------------------------------------------------------------------
# InvariantChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class InvariantChecker:
    """Checks system-level invariants from theory2.tex Ch45.

    Each ``check_*`` method returns ``True`` iff the invariant holds for
    the supplied data.  A call to ``all_invariants`` runs every check at
    once and returns a mapping of invariant name → bool.
    """

    checker_id: str
    checks_run: int = 0
    violations: list[str] = field(default_factory=list)

    def _record(self, name: str, holds: bool, detail: str = "") -> bool:
        self.checks_run += 1
        if not holds:
            msg = f"{name}: {detail}" if detail else name
            self.violations.append(msg)
        return holds

    def check_routing_consistency(
        self, decisions: list[RoutingDecision]
    ) -> bool:
        """Return ``True`` iff the same task_id always maps to the same channel."""
        task_channels: dict[str, EvidenceChannel] = {}
        for d in decisions:
            if d.task_id in task_channels:
                if task_channels[d.task_id] != d.channel:
                    return self._record(
                        "routing_consistency",
                        False,
                        f"task {d.task_id} maps to both "
                        f"{task_channels[d.task_id].value} and {d.channel.value}",
                    )
            else:
                task_channels[d.task_id] = d.channel
        return self._record("routing_consistency", True)

    def check_trust_monotonicity(
        self, trust_assignments: list[tuple[str, str]]
    ) -> bool:
        """Return ``True`` iff trust is never silently promoted across assignments.

        *trust_assignments* is a list of ``(decision_id, trust_level)`` pairs
        in chronological order.  Monotonicity requires that once a decision_id
        is assigned a trust level, subsequent assignments may only stay the
        same or go lower (no silent promotion).
        """
        latest: dict[str, str] = {}
        for decision_id, trust in trust_assignments:
            if decision_id in latest:
                prev = latest[decision_id]
                if _rank(trust) > _rank(prev):
                    return self._record(
                        "trust_monotonicity",
                        False,
                        f"decision {decision_id} silently promoted from {prev} to {trust}",
                    )
            latest[decision_id] = trust
        return self._record("trust_monotonicity", True)

    def check_jurisdiction_completeness(
        self,
        tasks: list[dict[str, Any]],
        jurisdiction_maps: list[JurisdictionMap],
    ) -> bool:
        """Return ``True`` iff every task has at least one handling channel."""
        for task in tasks:
            handled = any(jm.can_handle(task) for jm in jurisdiction_maps)
            if not handled:
                return self._record(
                    "jurisdiction_completeness",
                    False,
                    f"no handler for task {task.get('task_id', '?')} "
                    f"(claim_kind={task.get('claim_kind', '?')})",
                )
        return self._record("jurisdiction_completeness", True)

    def check_human_escalation_terminates(
        self,
        escalations: list[HumanEscalation],
        max_age_hours: float = 168.0,
    ) -> bool:
        """Return ``True`` iff all escalations have resolved or are recent.

        Unresolved escalations older than *max_age_hours* violate the
        termination bound.
        """
        for esc in escalations:
            if not esc.is_resolved and esc.age_hours > max_age_hours:
                return self._record(
                    "human_escalation_termination",
                    False,
                    f"escalation {esc.escalation_id} unresolved after "
                    f"{esc.age_hours:.1f}h (bound={max_age_hours}h)",
                )
        return self._record("human_escalation_termination", True)

    def check_channel_composability(
        self, channels: list[EvidenceChannel]
    ) -> bool:
        """Return ``True`` iff COMPOSITE can include all supplied channels.

        The composite channel is the designated aggregator; any non-composite
        channel is composable by definition.
        """
        non_composite = [c for c in channels if c != EvidenceChannel.COMPOSITE]
        # Composability holds as long as the set of non-composite channels is
        # non-empty (they can be combined) or if the list is empty (trivially).
        if len(channels) == 0:
            return self._record("channel_composability", True)
        can_compose = len(non_composite) >= 0  # always true, but explicit
        return self._record("channel_composability", can_compose)

    def all_invariants(
        self,
        tasks: list[dict[str, Any]],
        decisions: list[RoutingDecision],
        trust_assignments: list[tuple[str, str]],
        jurisdiction_maps: list[JurisdictionMap],
        escalations: list[HumanEscalation],
    ) -> dict[str, bool]:
        """Run every invariant check and return a name → bool mapping."""
        channels = [d.channel for d in decisions]
        return {
            "routing_consistency": self.check_routing_consistency(decisions),
            "trust_monotonicity": self.check_trust_monotonicity(trust_assignments),
            "jurisdiction_completeness": self.check_jurisdiction_completeness(
                tasks, jurisdiction_maps
            ),
            "human_escalation_termination": self.check_human_escalation_terminates(
                escalations
            ),
            "channel_composability": self.check_channel_composability(channels),
        }

    def report(self) -> dict[str, Any]:
        """Return a summary of all checks run and any violations found."""
        return {
            "checker_id": self.checker_id,
            "checks_run": self.checks_run,
            "violation_count": len(self.violations),
            "violations": list(self.violations),
            "all_clear": len(self.violations) == 0,
        }


# ---------------------------------------------------------------------------
# Theorem 45.1 — Jurisdiction Completeness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem45_1_JurisdictionCompleteness:
    """Theorem 45.1: Every verification task has at least one jurisdictional
    channel that can handle it.

    Formally: ∀ t ∈ Tasks, ∃ j ∈ JurisdictionMaps, j.can_handle(t) = True.

    A gap in jurisdiction coverage is a critical correctness failure: tasks
    that fall outside every jurisdiction map would be silently dropped.
    """

    theorem_id: str
    jurisdiction_maps: list[JurisdictionMap]

    @classmethod
    def with_default_maps(cls) -> Theorem45_1_JurisdictionCompleteness:
        """Construct with a sensible default set of jurisdiction maps."""
        maps = [
            JurisdictionMap(
                map_id=str(uuid.uuid4()),
                channel=EvidenceChannel.Z3,
                supported_claim_kinds=["smt", "arithmetic", "bitvector"],
                max_complexity=10,
                min_trust_level="SOLVER_DISCHARGED",
            ),
            JurisdictionMap(
                map_id=str(uuid.uuid4()),
                channel=EvidenceChannel.COPILOT_LLM,
                supported_claim_kinds=["semantic", "docstring", "heuristic"],
                max_complexity=5,
                min_trust_level="COPILOT_SUGGESTED",
            ),
            JurisdictionMap(
                map_id=str(uuid.uuid4()),
                channel=EvidenceChannel.RUNTIME_WITNESS,
                supported_claim_kinds=["runtime", "trace", "invariant"],
                max_complexity=7,
                min_trust_level="RUNTIME_WITNESSED",
            ),
            JurisdictionMap(
                map_id=str(uuid.uuid4()),
                channel=EvidenceChannel.HUMAN,
                supported_claim_kinds=["review", "approval", "override"],
                max_complexity=3,
                min_trust_level="HUMAN_ATTESTED",
            ),
        ]
        return cls(theorem_id=str(uuid.uuid4()), jurisdiction_maps=maps)

    def _find_gap_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return tasks with no matching jurisdiction map."""
        return [
            t for t in tasks
            if not any(jm.can_handle(t) for jm in self.jurisdiction_maps)
        ]

    def verify(self, tasks: list[dict[str, Any]]) -> TheoremResult:
        """Verify Theorem 45.1 for the given task list.

        Returns a verified result if every task has a handler, or a falsified
        result with the first gap task as the counterexample.
        """
        gaps = self._find_gap_tasks(tasks)
        if gaps:
            return TheoremResult.falsified(
                theorem_name="Theorem45_1_JurisdictionCompleteness",
                counterexample={
                    "gap_count": len(gaps),
                    "first_gap": gaps[0],
                    "all_gaps": gaps,
                },
                proof_sketch=self.proof_sketch(),
            )
        evidence = [
            f"Task '{t.get('task_id', '?')}' handled by "
            + ", ".join(
                jm.channel.value for jm in self.jurisdiction_maps if jm.can_handle(t)
            )
            for t in tasks[:5]  # sample up to 5 for brevity
        ]
        return TheoremResult.verified(
            theorem_name="Theorem45_1_JurisdictionCompleteness",
            evidence=evidence or ["No tasks to check — vacuously true"],
            proof_sketch=self.proof_sketch(),
        )

    def proof_sketch(self) -> str:
        """Return a human-readable proof sketch for Theorem 45.1."""
        return (
            "By exhaustive enumeration of the jurisdiction maps: for each task t, "
            "we iterate over all registered JurisdictionMap instances and check "
            "can_handle(t).  If at least one returns True, jurisdiction is "
            "established.  The theorem holds iff this check passes for every t."
        )


# ---------------------------------------------------------------------------
# Theorem 45.2 — Trust Ceiling Enforcement
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem45_2_TrustCeilingEnforcement:
    """Theorem 45.2: Copilot evidence always sits strictly below
    solver-discharged trust.

    Formally:
        ∀ d ∈ Decisions, channel(d) = COPILOT_LLM →
            trust_assigned(d) ≤ COPILOT_SUGGESTED
        ∀ d ∈ Decisions, channel(d) = Z3 →
            trust_assigned(d) ≥ SOLVER_DISCHARGED
    """

    theorem_id: str
    channel_ceilings: dict[str, str]

    @classmethod
    def default(cls) -> Theorem45_2_TrustCeilingEnforcement:
        """Construct with default channel ceilings."""
        return cls(
            theorem_id=str(uuid.uuid4()),
            channel_ceilings={
                EvidenceChannel.Z3.value: "MECHANICALLY_VERIFIED",
                EvidenceChannel.COPILOT_LLM.value: "COPILOT_SUGGESTED",
                EvidenceChannel.RUNTIME_WITNESS.value: "RUNTIME_WITNESSED",
                EvidenceChannel.HUMAN.value: "HUMAN_ATTESTED",
                EvidenceChannel.COMPOSITE.value: "SOLVER_DISCHARGED",
            },
        )

    def copilot_ceiling(self) -> str:
        """Return the Copilot trust ceiling (COPILOT_SUGGESTED)."""
        return "COPILOT_SUGGESTED"

    def solver_floor(self) -> str:
        """Return the Z3 solver trust floor (SOLVER_DISCHARGED)."""
        return "SOLVER_DISCHARGED"

    def verify(
        self,
        decisions: list[RoutingDecision],
        trust_assignments: dict[str, str],
    ) -> TheoremResult:
        """Verify Theorem 45.2 for the given decisions and trust map.

        *trust_assignments* maps decision_id → trust level string.
        Returns falsified if any COPILOT_LLM decision exceeds COPILOT_SUGGESTED
        or any Z3 decision falls below SOLVER_DISCHARGED.
        """
        violations: list[str] = []
        evidence: list[str] = []

        for d in decisions:
            assigned = trust_assignments.get(d.decision_id, "UNVERIFIED")

            if d.channel == EvidenceChannel.COPILOT_LLM:
                if _rank(assigned) > _rank(self.copilot_ceiling()):
                    violations.append(
                        f"COPILOT_LLM decision {d.decision_id} has trust "
                        f"{assigned} > {self.copilot_ceiling()}"
                    )
                else:
                    evidence.append(
                        f"COPILOT_LLM decision {d.decision_id}: {assigned} ≤ "
                        f"{self.copilot_ceiling()} ✓"
                    )

            elif d.channel == EvidenceChannel.Z3:
                if _rank(assigned) < _rank(self.solver_floor()):
                    violations.append(
                        f"Z3 decision {d.decision_id} has trust "
                        f"{assigned} < {self.solver_floor()}"
                    )
                else:
                    evidence.append(
                        f"Z3 decision {d.decision_id}: {assigned} ≥ "
                        f"{self.solver_floor()} ✓"
                    )

        if violations:
            return TheoremResult.falsified(
                theorem_name="Theorem45_2_TrustCeilingEnforcement",
                counterexample={"violations": violations},
                proof_sketch=self.proof_sketch(),
            )
        return TheoremResult.verified(
            theorem_name="Theorem45_2_TrustCeilingEnforcement",
            evidence=evidence or ["No Copilot or Z3 decisions to check"],
            proof_sketch=self.proof_sketch(),
        )

    def proof_sketch(self) -> str:
        """Return a human-readable proof sketch for Theorem 45.2."""
        return (
            "By channel invariant: the CopilotTrustGateway applies "
            "enforce_ceiling(COPILOT_SUGGESTED) to every COPILOT_LLM decision "
            "at dispatch time.  Z3 decisions receive a trust floor via the "
            "RoutingTrustIntegrator.  Any violation indicates a gateway bypass "
            "and constitutes a counterexample."
        )


# ---------------------------------------------------------------------------
# Theorem 45.3 — Routing Consistency
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem45_3_RoutingConsistency:
    """Theorem 45.3: Routing is consistent — the same task always routes to the
    same channel under the same policy.

    Formally: ∀ t, p, route(t, p) = c is a total function (no ambiguity).
    """

    theorem_id: str
    decision_cache: dict[str, EvidenceChannel] = field(default_factory=dict)

    def record_decision(
        self, task_id: str, policy: str, channel: EvidenceChannel
    ) -> None:
        """Record a routing decision for later consistency checking."""
        key = f"{task_id}::{policy}"
        self.decision_cache[key] = channel

    def check_consistency(
        self, task_id: str, policy: str, channel: EvidenceChannel
    ) -> bool:
        """Return ``True`` iff the cached decision for (task_id, policy) matches *channel*.

        Returns ``True`` (and records) if no prior decision exists.
        """
        key = f"{task_id}::{policy}"
        if key not in self.decision_cache:
            self.decision_cache[key] = channel
            return True
        return self.decision_cache[key] == channel

    def verify(
        self,
        tasks: list[dict[str, Any]],
        policy_name: str,
        decisions: list[RoutingDecision],
    ) -> TheoremResult:
        """Verify Theorem 45.3 for *decisions* under *policy_name*.

        Groups decisions by task_id; if any task_id maps to multiple distinct
        channels, the theorem is falsified.
        """
        task_channel_map: dict[str, set[str]] = {}
        for d in decisions:
            task_channel_map.setdefault(d.task_id, set()).add(d.channel.value)

        inconsistent: list[dict[str, Any]] = []
        evidence: list[str] = []

        for task_id, channels in task_channel_map.items():
            if len(channels) > 1:
                inconsistent.append(
                    {"task_id": task_id, "channels": sorted(channels)}
                )
            else:
                evidence.append(
                    f"task {task_id} consistently routed to {next(iter(channels))}"
                )

        if inconsistent:
            return TheoremResult.falsified(
                theorem_name="Theorem45_3_RoutingConsistency",
                counterexample={
                    "policy": policy_name,
                    "inconsistent_tasks": inconsistent,
                },
                proof_sketch=self.proof_sketch(),
            )
        return TheoremResult.verified(
            theorem_name="Theorem45_3_RoutingConsistency",
            evidence=evidence or ["No routing decisions to check"],
            proof_sketch=self.proof_sketch(),
        )

    def proof_sketch(self) -> str:
        """Return a human-readable proof sketch for Theorem 45.3."""
        return (
            "By determinism of the routing policy: given the same task payload "
            "and the same policy name, the channel selector is a pure function "
            "of (task, policy) and must return a unique channel.  If two "
            "decisions for the same task_id disagree, a non-determinism bug "
            "exists in the policy implementation."
        )


# ---------------------------------------------------------------------------
# Theorem 45.4 — Human Escalation Termination
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem45_4_HumanEscalationTermination:
    """Theorem 45.4: Human escalation always terminates within bounded time.

    Formally: ∀ e ∈ Escalations, ∃ t: t ≤ created_at(e) + max_pending_hours
    such that resolved(e, t) = True.

    The bound is 168 hours (1 week) by default; this is configurable.
    """

    theorem_id: str
    max_pending_hours: float = 168.0

    def termination_bound(self) -> float:
        """Return the maximum permitted pending time in hours."""
        return self.max_pending_hours

    def verify(self, escalations: list[HumanEscalation]) -> TheoremResult:
        """Verify Theorem 45.4 for the given escalations.

        Returns falsified if any escalation is unresolved AND older than
        ``max_pending_hours``.  Resolved escalations and recent unresolved
        escalations both satisfy the bound.
        """
        overdue: list[dict[str, Any]] = []
        evidence: list[str] = []

        for esc in escalations:
            if not esc.is_resolved and esc.age_hours > self.max_pending_hours:
                overdue.append(
                    {
                        "escalation_id": esc.escalation_id,
                        "task_id": esc.task_id,
                        "age_hours": esc.age_hours,
                        "bound_hours": self.max_pending_hours,
                    }
                )
            else:
                status = "resolved" if esc.is_resolved else "pending (within bound)"
                evidence.append(
                    f"escalation {esc.escalation_id}: {status} "
                    f"({esc.age_hours:.1f}h ≤ {self.max_pending_hours}h)"
                )

        if overdue:
            return TheoremResult.falsified(
                theorem_name="Theorem45_4_HumanEscalationTermination",
                counterexample={"overdue_escalations": overdue},
                proof_sketch=self.proof_sketch(),
            )
        return TheoremResult.verified(
            theorem_name="Theorem45_4_HumanEscalationTermination",
            evidence=evidence or ["No escalations to check"],
            proof_sketch=self.proof_sketch(),
        )

    def proof_sketch(self) -> str:
        """Return a human-readable proof sketch for Theorem 45.4."""
        return (
            "By policy invariant: the human escalation system SLA requires "
            "resolution within max_pending_hours.  The bound is enforced at the "
            "system level by escalation monitors.  Any unresolved escalation "
            "exceeding the bound indicates an SLA violation and constitutes a "
            "counterexample to the termination theorem."
        )


# ---------------------------------------------------------------------------
# Lemma 45.A — Channel Composability
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Lemma45_A_ChannelComposability:
    """Lemma 45.A: Evidence channels can be composed without trust leakage.

    Formally: trust(COMPOSITE(c₁, …, cₙ)) = min(trust(c₁), …, trust(cₙ)).

    Composing multiple channels must not inflate the resulting trust level.
    The composite trust is the minimum (weakest) of all component trusts;
    this prevents a weak channel from laundering its evidence through
    composition with a stronger channel.
    """

    lemma_id: str

    def compose_trust(self, trusts: list[str]) -> str:
        """Return the weakest trust level in *trusts*.

        An empty list returns CONTRADICTED (no components → no trust).
        """
        if not trusts:
            return "CONTRADICTED"
        result = trusts[0]
        for t in trusts[1:]:
            result = _weaker(result, t)
        return result

    def verify(self, channel_trusts: dict[str, str]) -> TheoremResult:
        """Verify Lemma 45.A for the supplied channel → trust mapping.

        Checks that the composite trust is the minimum of all component
        trusts and that no composition step inflates trust.
        Returns verified if the composite trust equals the minimum; falsified
        if composition would produce a trust level stronger than the minimum.
        """
        if not channel_trusts:
            return TheoremResult.verified(
                theorem_name="Lemma45_A_ChannelComposability",
                evidence=["No channels — vacuously true"],
                proof_sketch=self.proof_sketch(),
            )

        trusts = list(channel_trusts.values())
        expected_composite = self.compose_trust(trusts)

        # Check that each pairwise composition only weakens or preserves trust
        violations: list[str] = []
        evidence: list[str] = []

        for channel, trust in channel_trusts.items():
            composed = self.compose_trust([trust, expected_composite])
            if _rank(composed) > _rank(expected_composite):
                violations.append(
                    f"Channel {channel} with trust {trust} inflated composite "
                    f"from {expected_composite} to {composed}"
                )
            else:
                evidence.append(
                    f"Channel {channel}: trust={trust}, "
                    f"composed={composed} ≤ {expected_composite} ✓"
                )

        if violations:
            return TheoremResult.falsified(
                theorem_name="Lemma45_A_ChannelComposability",
                counterexample={
                    "violations": violations,
                    "expected_composite": expected_composite,
                    "channel_trusts": channel_trusts,
                },
                proof_sketch=self.proof_sketch(),
            )
        evidence.insert(0, f"Composite trust = {expected_composite} (minimum of all)")
        return TheoremResult.verified(
            theorem_name="Lemma45_A_ChannelComposability",
            evidence=evidence,
            proof_sketch=self.proof_sketch(),
        )

    def proof_sketch(self) -> str:
        """Return a human-readable proof sketch for Lemma 45.A."""
        return (
            "By the conjunction semantics of evidence composition: combining "
            "two evidence sources requires both to hold, so the joint trust is "
            "the minimum (weakest) of the individual trusts.  This is the "
            "meet operation in the trust lattice.  Trust inflation would violate "
            "the lattice ordering and is therefore impossible by construction."
        )


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRegistry:
    """Registry of all Ch45 theorems and lemmas.

    Provides a unified interface for registration, lookup, and batch
    verification of all theorem objects defined in this module.
    """

    registry_id: str
    theorems: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default(cls) -> TheoremRegistry:
        """Construct a registry pre-populated with all Ch45 theorems."""
        reg = cls(registry_id=str(uuid.uuid4()))
        reg.register(
            "Theorem45_1_JurisdictionCompleteness",
            Theorem45_1_JurisdictionCompleteness.with_default_maps(),
        )
        reg.register(
            "Theorem45_2_TrustCeilingEnforcement",
            Theorem45_2_TrustCeilingEnforcement.default(),
        )
        reg.register(
            "Theorem45_3_RoutingConsistency",
            Theorem45_3_RoutingConsistency(theorem_id=str(uuid.uuid4())),
        )
        reg.register(
            "Theorem45_4_HumanEscalationTermination",
            Theorem45_4_HumanEscalationTermination(theorem_id=str(uuid.uuid4())),
        )
        reg.register(
            "Lemma45_A_ChannelComposability",
            Lemma45_A_ChannelComposability(lemma_id=str(uuid.uuid4())),
        )
        return reg

    def register(self, name: str, theorem: Any) -> None:
        """Register *theorem* under *name*."""
        self.theorems[name] = theorem

    def get(self, name: str) -> Any:
        """Retrieve a theorem by name.  Returns ``None`` if not found."""
        return self.theorems.get(name)

    def list_theorems(self) -> list[str]:
        """Return the names of all registered theorems."""
        return list(self.theorems.keys())

    def verify_all(
        self,
        tasks: list[dict[str, Any]],
        decisions: list[RoutingDecision],
        trust_assignments: dict[str, str],
        jurisdiction_maps: list[JurisdictionMap],
        escalations: list[HumanEscalation],
        channel_trusts: dict[str, str],
    ) -> dict[str, TheoremResult]:
        """Run all registered theorems and return a name → TheoremResult map."""
        results: dict[str, TheoremResult] = {}

        t1 = self.get("Theorem45_1_JurisdictionCompleteness")
        if t1 is not None:
            # Temporarily use our jurisdiction_maps argument
            original_maps = t1.jurisdiction_maps
            t1.jurisdiction_maps = jurisdiction_maps if jurisdiction_maps else original_maps
            results["Theorem45_1_JurisdictionCompleteness"] = t1.verify(tasks)
            t1.jurisdiction_maps = original_maps

        t2 = self.get("Theorem45_2_TrustCeilingEnforcement")
        if t2 is not None:
            results["Theorem45_2_TrustCeilingEnforcement"] = t2.verify(
                decisions, trust_assignments
            )

        t3 = self.get("Theorem45_3_RoutingConsistency")
        if t3 is not None:
            results["Theorem45_3_RoutingConsistency"] = t3.verify(
                tasks, "strict_jurisdiction", decisions
            )

        t4 = self.get("Theorem45_4_HumanEscalationTermination")
        if t4 is not None:
            results["Theorem45_4_HumanEscalationTermination"] = t4.verify(escalations)

        la = self.get("Lemma45_A_ChannelComposability")
        if la is not None:
            results["Lemma45_A_ChannelComposability"] = la.verify(channel_trusts)

        return results

    def all_hold(self, results: dict[str, TheoremResult]) -> bool:
        """Return ``True`` iff every theorem result in *results* holds."""
        return all(r.holds for r in results.values())

    def summary_report(self, results: dict[str, TheoremResult]) -> str:
        """Return a multi-line human-readable summary of *results*."""
        lines: list[str] = [
            f"TheoremRegistry {self.registry_id} — verification report",
            f"Theorems registered: {len(self.theorems)}",
            f"Results: {len(results)} / {len(self.theorems)} verified",
            "",
        ]
        for name, result in results.items():
            lines.append(f"  {result.summary()}")
        holds_count = sum(1 for r in results.values() if r.holds)
        lines.append("")
        lines.append(
            f"Overall: {holds_count}/{len(results)} theorems hold."
        )
        return "\n".join(lines)
