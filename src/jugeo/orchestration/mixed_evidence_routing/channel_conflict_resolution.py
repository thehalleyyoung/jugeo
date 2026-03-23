"""Channel conflict resolution for the mixed-evidence routing layer.

This module implements the channel-conflict-resolution machinery described in
*theory2.tex* Ch 45 §45.5 ("Channel Conflict Resolution").  That section
establishes that when two or more evidence channels produce divergent verdicts
for the same task, the routing layer must resolve the disagreement in a
principled, auditable manner rather than silently deferring to whichever
channel happened to answer first.

Ch 45 §45.5 identifies four root causes of channel conflict:

1. **Verdict mismatch** — channels genuinely disagree on pass/fail.  This is
   the most common case and the one that requires the strongest conservatism
   guarantees (§45.5.1).

2. **Trust-tier gap** — the verdicts agree on pass/fail but come from channels
   whose trust tiers differ by more than a configurable threshold, creating
   epistemic uncertainty about which verdict to propagate downstream (§45.5.2).

3. **Evidence contradiction** — the underlying evidence artefacts are
   logically incompatible even when the top-level pass/fail values agree; for
   example, one channel proves ``P`` while another proves ``¬P`` at the sub-
   claim level (§45.5.3).

4. **Timeout conflict** — at least one channel did not produce a verdict within
   the SLA window, so the system must decide whether to wait, escalate, or
   fall back to a conservative default (§45.5.4).

**Key theory2 invariant** (§45.5 Theorem 2 — *Conservatism Principle*):
    *When channels disagree, the resolution MUST NEVER silently upgrade trust.
    If a high-trust channel says PASS and a low-trust channel says FAIL, the
    resolution holds the lower-trust-tier result.*

    Formally: for any pair of verdicts (v_h, v_l) where
    trust_rank(v_h) > trust_rank(v_l) and v_h.passed ≠ v_l.passed, the
    resolved result must satisfy:

        resolved.passed == v_l.passed   AND   resolved.trust_tier == v_l.trust_tier

    This prevents a low-quality channel from being overruled by a superficially
    authoritative one whose jurisdiction does not cover the contested claim.

Key responsibilities
--------------------
* :class:`ConflictType` — taxonomy of conflict kinds.
* :class:`ChannelVerdict` — one channel's verdict on a task.
* :class:`ChannelConflict` — records a specific conflict between verdicts.
* :class:`ResolutionStrategy` — enumeration of resolution policies.
* :class:`ConflictResolutionResult` — outcome of resolving a conflict.
* :class:`TrustConservativeResolver` — implements the LOWEST_TRUST_CONSERVATIVE
  strategy (the default, mandated by the theory2 invariant above).
* :class:`MajorityVoteResolver` — democratic vote with configurable tie-breaking.
* :class:`ChannelConflictDetector` — scans a verdict list for conflicts.
* :class:`ChannelConflictResolver` — top-level dispatcher to strategy resolvers.
* :class:`ConflictResolutionCoordinator` — orchestrates detection + resolution
  at scale.

Design notes
------------
* All domain objects are *frozen* dataclasses to guarantee that once a verdict
  or conflict record is created it cannot be mutated by downstream code.  This
  supports the auditability requirement of §45.5.5.

* Trust tiers are ranked on an integer scale so that rank comparisons are O(1)
  and independent of the string names used externally.  The canonical mapping
  is: ``"high_trust"`` → 3, ``"medium_trust"`` → 2, ``"low_trust"`` → 1,
  anything else → 0.

* The :class:`ChannelConflictDetector` runs in a single pass over the verdict
  list, accumulating conflict records.  The total cost is O(n) in the number
  of verdicts.

References
----------
* theory2.tex Ch 45 §45.5 — Channel Conflict Resolution (primary source)
* theory2.tex Ch 45 §45.3 — Trust Algebra for Mixed-Evidence Systems
* theory2.tex Ch 45 §45.6 — Escalation and Human-in-the-Loop Policies
* channel_selection.py — upstream channel selection (module 1 of 5)
* evidence_aggregation.py — evidence aggregation (module 2 of 5)
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Optional upstream imports — guarded with try/except so the module loads
# even when sister packages are stubs or not yet installed.
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustCeiling  # type: ignore[import]

    _TRUST_AVAILABLE = True
except Exception:
    _TRUST_AVAILABLE = False

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustLevel."""

        HIGH = "high_trust"
        MEDIUM = "medium_trust"
        LOW = "low_trust"
        UNKNOWN = "unknown"

    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustAlgebra."""

    class TrustCeiling:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustCeiling."""


try:
    from jugeo.orchestration.controller import OrchestratorState, Orchestrator  # type: ignore[import]

    _CONTROLLER_AVAILABLE = True
except Exception:
    _CONTROLLER_AVAILABLE = False

    class OrchestratorState:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.controller.OrchestratorState."""

    class Orchestrator:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.controller.Orchestrator."""


try:
    from jugeo.orchestration.fleet import FleetMember, Fleet  # type: ignore[import]

    _FLEET_AVAILABLE = True
except Exception:
    _FLEET_AVAILABLE = False

    class FleetMember:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.fleet.FleetMember."""

    class Fleet:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.fleet.Fleet."""


try:
    from jugeo.orchestration.mixed_evidence_routing.models import (  # type: ignore[import]
        EvidenceChannel,
        RoutingDecision,
    )

    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False

    class EvidenceChannel:  # type: ignore[no-redef]
        """Stub for EvidenceChannel."""

    class RoutingDecision:  # type: ignore[no-redef]
        """Stub for RoutingDecision."""


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust-tier ranking constants (§45.5, Table 3)
# ---------------------------------------------------------------------------

_TRUST_TIER_RANKS: dict[str, int] = {
    "high_trust": 3,
    "medium_trust": 2,
    "low_trust": 1,
    "unknown": 0,
}

# Canonical tier name for each integer rank (inverse mapping)
_RANK_TO_TIER: dict[int, str] = {v: k for k, v in _TRUST_TIER_RANKS.items()}


# ---------------------------------------------------------------------------
# ConflictType
# ---------------------------------------------------------------------------


class ConflictType(str, enum.Enum):
    """Taxonomy of channel-conflict kinds (§45.5 §1 Classification).

    Each member corresponds to a distinct root cause that demands a
    dedicated resolution pathway.  The string values are used in serialised
    records so that downstream consumers (dashboards, audit logs) can filter
    by conflict kind without importing this enum.

    Members
    -------
    VERDICT_MISMATCH
        Two or more channels returned different pass/fail verdicts for the
        same task.  This is the primary case addressed by the Conservatism
        Principle (§45.5 Theorem 2).

    TRUST_TIER_GAP
        All channels agree on pass/fail but the verdicts span trust tiers
        that differ by more than a configurable threshold.  Even if every
        channel passes, routing to a downstream consumer that expects
        ``high_trust`` when only ``low_trust`` verdicts are available would
        silently upgrade trust.

    EVIDENCE_CONTRADICTION
        The supporting evidence artefacts are logically incompatible even
        when the pass/fail verdict appears to agree.  Example: two channels
        both report PASS but one proves ``x > 0`` while the other proves
        ``x < 0`` for the same variable.

    TIMEOUT_CONFLICT
        At least one nominated channel did not return a verdict within the
        SLA window.  The system must decide whether to hold on the partial
        results (conservative) or escalate to a human reviewer.

    PARTIAL_AGREEMENT
        A strict subset of channels agree, but at least one disagrees.
        Typically a precursor to VERDICT_MISMATCH; tracked separately to
        allow statistical analysis of partial-agreement rates over time.
    """

    VERDICT_MISMATCH = "verdict_mismatch"
    TRUST_TIER_GAP = "trust_tier_gap"
    EVIDENCE_CONTRADICTION = "evidence_contradiction"
    TIMEOUT_CONFLICT = "timeout_conflict"
    PARTIAL_AGREEMENT = "partial_agreement"


# ---------------------------------------------------------------------------
# ChannelVerdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelVerdict:
    """One channel's verdict on a task.

    A :class:`ChannelVerdict` captures everything the routing layer needs to
    know about a single channel's assessment: the binary pass/fail decision,
    the channel's confidence, the trust tier at which the result was produced,
    a brief evidence summary, and timing information.

    Instances are frozen and use ``__slots__`` so they can be placed safely
    in sets, used as dict keys, and shared across threads without defensive
    copying.

    Attributes
    ----------
    verdict_id:
        Globally unique identifier for this verdict record, used in audit
        logs and conflict references.
    task_id:
        Identifier of the task this verdict evaluates.  Multiple verdicts
        for the same task share this field.
    channel:
        String identifier of the channel that produced this verdict
        (e.g. ``"z3"``, ``"copilot_llm"``, ``"runtime_witness"``).
    passed:
        The binary verdict: ``True`` = PASS (task satisfies the claim),
        ``False`` = FAIL.
    confidence:
        The channel's self-reported confidence in this verdict, in [0, 1].
        A value of ``1.0`` means the channel is certain; ``0.0`` means
        the result is no better than a random guess.
    trust_tier:
        The trust-tier string at which this verdict was produced (typically
        one of ``"high_trust"``, ``"medium_trust"``, ``"low_trust"``).  This
        is the tier at which downstream consumers MUST treat the result; it
        may be lower than the channel's maximum trust ceiling.
    evidence_summary:
        A short human-readable summary of the evidence underpinning the
        verdict (e.g. ``"Z3 returned sat for formula (assert (> x 0))"``).
    latency_seconds:
        Wall-clock time the channel took to produce this verdict, in seconds.
    timestamp:
        Unix epoch time at which the verdict was recorded.
    metadata:
        Arbitrary key/value pairs for extensibility.  Callers may store
        raw solver output, token counts, or reviewer IDs here without
        affecting the core verdict fields.
    """

    verdict_id: str
    task_id: str
    channel: str
    passed: bool
    confidence: float
    trust_tier: str
    evidence_summary: str
    latency_seconds: float
    timestamp: float
    metadata: dict

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this verdict to a JSON-compatible dictionary.

        Returns
        -------
        dict
            All fields serialised to primitive Python types.  The
            ``passed`` field is preserved as a boolean, not coerced to int.
        """
        return {
            "verdict_id": self.verdict_id,
            "task_id": self.task_id,
            "channel": self.channel,
            "passed": self.passed,
            "confidence": self.confidence,
            "trust_tier": self.trust_tier,
            "evidence_summary": self.evidence_summary,
            "latency_seconds": self.latency_seconds,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChannelVerdict:
        """Deserialise a :class:`ChannelVerdict` from a dictionary.

        Parameters
        ----------
        d:
            Dictionary previously produced by :meth:`to_dict` (or any dict
            with the required keys).

        Returns
        -------
        ChannelVerdict
            A new frozen instance populated from *d*.

        Raises
        ------
        KeyError
            If any required field is absent from *d*.
        """
        return cls(
            verdict_id=str(d["verdict_id"]),
            task_id=str(d["task_id"]),
            channel=str(d["channel"]),
            passed=bool(d["passed"]),
            confidence=float(d["confidence"]),
            trust_tier=str(d["trust_tier"]),
            evidence_summary=str(d["evidence_summary"]),
            latency_seconds=float(d["latency_seconds"]),
            timestamp=float(d["timestamp"]),
            metadata=dict(d.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# ChannelConflict
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelConflict:
    """Records a conflict detected among a set of channel verdicts.

    A :class:`ChannelConflict` is the output of the detection phase
    (:class:`ChannelConflictDetector`).  It groups the conflicting verdicts
    with a classification label (:class:`ConflictType`) and a human-readable
    description so that resolution logic and audit trails have enough context
    to act without re-running detection.

    Like :class:`ChannelVerdict`, instances are frozen to guarantee
    immutability throughout the resolution pipeline.

    Attributes
    ----------
    conflict_id:
        Globally unique identifier for this conflict record.
    task_id:
        The task about which the channels disagree.
    conflict_type:
        Classification of the conflict (see :class:`ConflictType`).
    verdicts:
        Immutable tuple of all :class:`ChannelVerdict` instances involved
        in this conflict.  Stored as a tuple so the dataclass remains
        hashable.
    detected_at:
        Unix epoch time at which the conflict was detected.
    description:
        Human-readable description of *why* this is a conflict, suitable
        for inclusion in audit logs and escalation tickets.
    metadata:
        Arbitrary key/value pairs for extensibility.
    """

    conflict_id: str
    task_id: str
    conflict_type: ConflictType
    verdicts: tuple[ChannelVerdict, ...]
    detected_at: float
    description: str
    metadata: dict

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def channels_involved(self) -> list[str]:
        """Return the channel identifiers of all conflicting verdicts.

        Returns
        -------
        list[str]
            Deduplicated list of channel identifier strings, in the order
            they appear in :attr:`verdicts`.
        """
        seen: set[str] = set()
        result: list[str] = []
        for v in self.verdicts:
            if v.channel not in seen:
                seen.add(v.channel)
                result.append(v.channel)
        return result

    def has_unanimous_pass(self) -> bool:
        """Return True if every verdict in this conflict is a PASS.

        Returns
        -------
        bool
            True when all verdicts passed; False when any verdict failed or
            when :attr:`verdicts` is empty.
        """
        if not self.verdicts:
            return False
        return all(v.passed for v in self.verdicts)

    def has_unanimous_fail(self) -> bool:
        """Return True if every verdict in this conflict is a FAIL.

        Returns
        -------
        bool
            True when all verdicts failed; False when any verdict passed or
            when :attr:`verdicts` is empty.
        """
        if not self.verdicts:
            return False
        return all(not v.passed for v in self.verdicts)

    def trust_spread(self) -> float:
        """Return the range of trust ranks across the conflicting verdicts.

        The trust spread is defined as
        ``max_rank(verdicts) − min_rank(verdicts)``.  A spread of 0 means
        all verdicts come from the same trust tier.  A spread of 3 (the
        maximum on the four-tier scale) indicates the highest possible
        epistemological gap.

        Returns
        -------
        float
            Non-negative integer-valued float representing the rank spread.
            Returns 0.0 when :attr:`verdicts` is empty.
        """
        if not self.verdicts:
            return 0.0
        ranks = [_TRUST_TIER_RANKS.get(v.trust_tier, 0) for v in self.verdicts]
        return float(max(ranks) - min(ranks))

    def to_dict(self) -> dict[str, Any]:
        """Serialise this conflict record to a JSON-compatible dictionary.

        Returns
        -------
        dict
            All fields serialised to primitive Python types.
        """
        return {
            "conflict_id": self.conflict_id,
            "task_id": self.task_id,
            "conflict_type": self.conflict_type.value,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "detected_at": self.detected_at,
            "description": self.description,
            "channels_involved": self.channels_involved(),
            "has_unanimous_pass": self.has_unanimous_pass(),
            "has_unanimous_fail": self.has_unanimous_fail(),
            "trust_spread": self.trust_spread(),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# ResolutionStrategy
# ---------------------------------------------------------------------------


class ResolutionStrategy(str, enum.Enum):
    """Enumeration of resolution policies for channel conflicts.

    The theory2 invariant mandates that :attr:`LOWEST_TRUST_CONSERVATIVE`
    be the default strategy (§45.5 §3.1).  Other strategies are available
    for specialised use cases but must be explicitly requested by the caller
    to prevent accidental trust upgrades.

    Members
    -------
    HIGHEST_TRUST_WINS
        The verdict from the channel with the highest trust rank is accepted
        as the resolution.  *Use with caution*: this strategy violates the
        theory2 conservatism invariant when the high-trust channel says PASS
        and a lower-trust channel says FAIL.  Permitted only when the calling
        context can justify why the high-trust result should prevail.

    LOWEST_TRUST_CONSERVATIVE
        The verdict that produces the most conservative (i.e. least
        permissive) outcome is selected.  If any channel FAILs, the
        resolution is FAIL; among FAILs the verdict from the lowest trust
        tier is chosen to avoid implicit trust upgrades.  This is the
        **default strategy** and the only one guaranteed to satisfy the
        theory2 invariant.

    MAJORITY_VOTE
        Pass/fail is decided by simple majority across all verdicts.  The
        final trust tier is the minimum tier among the majority coalition.
        Ties are broken according to :attr:`MajorityVoteResolver.tie_break_strategy`.

    REQUIRE_CONSENSUS
        The resolution is PASS only if *all* channels agree; otherwise the
        result is FAIL with the lowest trust tier.  Effectively an AND-gate.
        Appropriate for safety-critical decisions where a single objecting
        channel is enough to block.

    ESCALATE_TO_HUMAN
        The conflict is forwarded to the human escalation queue immediately.
        No automated pass/fail decision is made; the resolution record is
        marked ``escalated=True`` and ``final_passed=False`` pending human
        review.
    """

    HIGHEST_TRUST_WINS = "highest_trust_wins"
    LOWEST_TRUST_CONSERVATIVE = "lowest_trust_conservative"
    MAJORITY_VOTE = "majority_vote"
    REQUIRE_CONSENSUS = "require_consensus"
    ESCALATE_TO_HUMAN = "escalate_to_human"


# ---------------------------------------------------------------------------
# ConflictResolutionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConflictResolutionResult:
    """Outcome of resolving a channel conflict.

    A :class:`ConflictResolutionResult` is produced by one of the resolver
    classes (:class:`TrustConservativeResolver`, :class:`MajorityVoteResolver`,
    or the escalation path inside :class:`ChannelConflictResolver`).

    Like all domain records in this module it is frozen to ensure the
    resolution cannot be altered after the fact, satisfying the
    non-repudiation requirement of §45.5.5.

    Attributes
    ----------
    result_id:
        Globally unique identifier for this resolution record.
    conflict_id:
        Foreign key into :class:`ChannelConflict.conflict_id`.
    strategy_used:
        The :class:`ResolutionStrategy` that produced this result.
    winning_verdict:
        The :class:`ChannelVerdict` whose values were propagated to
        ``final_passed`` and ``final_trust_tier``.  ``None`` when the
        resolution escalated without selecting a winner.
    final_passed:
        The resolved pass/fail decision that will be forwarded downstream.
    final_trust_tier:
        The trust tier at which the resolved result is asserted.  This is
        NEVER higher than the trust tier of the winning verdict, satisfying
        the theory2 conservatism invariant.
    reasoning:
        Human-readable explanation of why this verdict was chosen, suitable
        for audit logs and developer debugging.
    escalated:
        True when the conflict was forwarded to a human reviewer rather than
        resolved automatically.
    timestamp:
        Unix epoch time at which the resolution was recorded.
    metadata:
        Arbitrary key/value pairs for extensibility.
    """

    result_id: str
    conflict_id: str
    strategy_used: ResolutionStrategy
    winning_verdict: ChannelVerdict | None
    final_passed: bool
    final_trust_tier: str
    reasoning: str
    escalated: bool
    timestamp: float
    metadata: dict

    def to_dict(self) -> dict[str, Any]:
        """Serialise this resolution result to a JSON-compatible dictionary.

        Returns
        -------
        dict
            All fields serialised to primitive Python types.  ``winning_verdict``
            is serialised via :meth:`ChannelVerdict.to_dict` when present,
            or ``None`` when absent.
        """
        return {
            "result_id": self.result_id,
            "conflict_id": self.conflict_id,
            "strategy_used": self.strategy_used.value,
            "winning_verdict": (
                self.winning_verdict.to_dict() if self.winning_verdict is not None else None
            ),
            "final_passed": self.final_passed,
            "final_trust_tier": self.final_trust_tier,
            "reasoning": self.reasoning,
            "escalated": self.escalated,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# TrustConservativeResolver
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustConservativeResolver:
    """Implements the LOWEST_TRUST_CONSERVATIVE resolution strategy.

    This resolver is the canonical implementation of the theory2 Conservatism
    Principle (§45.5 Theorem 2):

        *The resolution NEVER upgrades trust.  If any channel FAILs, the
        resolution is FAIL; among FAILs the verdict from the lowest trust
        tier is chosen.*

    The algorithm is:

    1. If any verdict is FAIL, collect all failing verdicts.
    2. Among the failing verdicts, pick the one with the lowest trust rank
       (most conservative).  Ties are broken by lowest confidence (more
       uncertain first) and then by earliest timestamp.
    3. If all verdicts pass, pick the passing verdict with the lowest trust
       rank (to avoid silently implying a higher trust than warranted).
    4. Construct a :class:`ConflictResolutionResult` recording the winner
       and the full reasoning chain.

    Attributes
    ----------
    resolver_id:
        Unique identifier for this resolver instance, used in logs and
        resolution records.
    """

    resolver_id: str

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def resolve(self, conflict: ChannelConflict) -> ConflictResolutionResult:
        """Resolve *conflict* using the LOWEST_TRUST_CONSERVATIVE strategy.

        Parameters
        ----------
        conflict:
            The :class:`ChannelConflict` to resolve.

        Returns
        -------
        ConflictResolutionResult
            A frozen result record documenting the winning verdict and the
            full reasoning chain.
        """
        _LOG.debug(
            "TrustConservativeResolver(%s): resolving conflict=%s type=%s",
            self.resolver_id,
            conflict.conflict_id,
            conflict.conflict_type.value,
        )

        if not conflict.verdicts:
            _LOG.warning(
                "TrustConservativeResolver(%s): conflict %s has no verdicts — "
                "producing conservative FAIL result",
                self.resolver_id,
                conflict.conflict_id,
            )
            return ConflictResolutionResult(
                result_id=str(uuid.uuid4()),
                conflict_id=conflict.conflict_id,
                strategy_used=ResolutionStrategy.LOWEST_TRUST_CONSERVATIVE,
                winning_verdict=None,
                final_passed=False,
                final_trust_tier="unknown",
                reasoning=(
                    "No verdicts available; defaulting to FAIL per conservatism principle."
                ),
                escalated=False,
                timestamp=time.time(),
                metadata={},
            )

        winner = self._most_conservative_verdict(conflict.verdicts)

        # Build a detailed reasoning string for the audit log.
        all_channels = ", ".join(
            f"{v.channel}({'PASS' if v.passed else 'FAIL'}/{v.trust_tier})"
            for v in conflict.verdicts
        )
        reasoning = (
            f"LOWEST_TRUST_CONSERVATIVE applied to {len(conflict.verdicts)} verdicts "
            f"[{all_channels}]. "
            f"Selected winner: channel={winner.channel}, passed={winner.passed}, "
            f"trust_tier={winner.trust_tier}, rank={self._rank_trust(winner.trust_tier)}. "
            f"Conservatism principle (§45.5 Theorem 2) mandates holding the most "
            f"conservative verdict to prevent silent trust upgrades."
        )

        _LOG.info(
            "TrustConservativeResolver(%s): conflict=%s resolved → passed=%s tier=%s",
            self.resolver_id,
            conflict.conflict_id,
            winner.passed,
            winner.trust_tier,
        )

        return ConflictResolutionResult(
            result_id=str(uuid.uuid4()),
            conflict_id=conflict.conflict_id,
            strategy_used=ResolutionStrategy.LOWEST_TRUST_CONSERVATIVE,
            winning_verdict=winner,
            final_passed=winner.passed,
            final_trust_tier=winner.trust_tier,
            reasoning=reasoning,
            escalated=False,
            timestamp=time.time(),
            metadata={
                "resolver_id": self.resolver_id,
                "total_verdicts": len(conflict.verdicts),
                "fail_count": sum(1 for v in conflict.verdicts if not v.passed),
                "pass_count": sum(1 for v in conflict.verdicts if v.passed),
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rank_trust(self, tier_str: str) -> int:
        """Map a trust-tier string to its integer rank.

        Higher rank = higher trust.  Uses the module-level
        ``_TRUST_TIER_RANKS`` table (§45.5 Table 3).

        Parameters
        ----------
        tier_str:
            Trust-tier identifier string, e.g. ``"high_trust"``.

        Returns
        -------
        int
            Integer rank in {0, 1, 2, 3}.  Unknown tiers map to 0.
        """
        return _TRUST_TIER_RANKS.get(tier_str, 0)

    def _most_conservative_verdict(
        self, verdicts: tuple[ChannelVerdict, ...]
    ) -> ChannelVerdict:
        """Pick the verdict that is most conservative (most blocking).

        Algorithm (§45.5 §3.1.2):
        1. Separate verdicts into FAIL and PASS groups.
        2. If any FAIL exists, select from the FAIL group:
           a. Lowest trust rank (minimise implicit trust).
           b. Tie-break by lowest confidence (most uncertain first).
           c. Tie-break by earliest timestamp (first objection wins).
        3. If all PASS, select from the PASS group by the same ordering
           to report the most conservatively-supported pass.

        Parameters
        ----------
        verdicts:
            Non-empty tuple of :class:`ChannelVerdict` instances.

        Returns
        -------
        ChannelVerdict
            The most conservative verdict.
        """
        failures = [v for v in verdicts if not v.passed]
        candidates = failures if failures else list(verdicts)

        return min(
            candidates,
            key=lambda v: (self._rank_trust(v.trust_tier), v.confidence, v.timestamp),
        )


# ---------------------------------------------------------------------------
# MajorityVoteResolver
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MajorityVoteResolver:
    """Resolves conflicts by simple majority vote across channel verdicts.

    Each channel verdict counts as one vote.  The majority outcome is PASS
    if more than half the verdicts pass; otherwise FAIL.

    When ``tie_break_strategy="conservative"`` (the default), ties are broken
    in favour of FAIL, consistent with the broader conservatism philosophy of
    the module.  When ``tie_break_strategy="highest_trust"``, ties are broken
    by adopting the verdict of the channel with the highest trust rank.

    Note: This resolver does *not* guarantee the theory2 invariant on its own.
    It is the responsibility of the caller to choose this strategy only in
    contexts where the invariant is known to be satisfied by the voting pool.

    Attributes
    ----------
    resolver_id:
        Unique identifier for this resolver instance.
    tie_break_strategy:
        How to break vote ties.  One of ``"conservative"`` (default) or
        ``"highest_trust"``.
    """

    resolver_id: str
    tie_break_strategy: str = "conservative"

    def resolve(self, conflict: ChannelConflict) -> ConflictResolutionResult:
        """Resolve *conflict* by majority vote.

        Parameters
        ----------
        conflict:
            The :class:`ChannelConflict` to resolve.

        Returns
        -------
        ConflictResolutionResult
            A frozen result record with the majority outcome.
        """
        _LOG.debug(
            "MajorityVoteResolver(%s): resolving conflict=%s with %d verdicts",
            self.resolver_id,
            conflict.conflict_id,
            len(conflict.verdicts),
        )

        if not conflict.verdicts:
            _LOG.warning(
                "MajorityVoteResolver(%s): conflict %s has no verdicts — FAIL",
                self.resolver_id,
                conflict.conflict_id,
            )
            return ConflictResolutionResult(
                result_id=str(uuid.uuid4()),
                conflict_id=conflict.conflict_id,
                strategy_used=ResolutionStrategy.MAJORITY_VOTE,
                winning_verdict=None,
                final_passed=False,
                final_trust_tier="unknown",
                reasoning="No verdicts; defaulting to FAIL.",
                escalated=False,
                timestamp=time.time(),
                metadata={},
            )

        pass_count = sum(1 for v in conflict.verdicts if v.passed)
        fail_count = len(conflict.verdicts) - pass_count
        total = len(conflict.verdicts)

        if pass_count > fail_count:
            majority_passed = True
            majority_coalition = [v for v in conflict.verdicts if v.passed]
        elif fail_count > pass_count:
            majority_passed = False
            majority_coalition = [v for v in conflict.verdicts if not v.passed]
        else:
            # Tie — apply tie-break strategy
            majority_passed, majority_coalition = self._break_tie(conflict.verdicts)

        # The trust tier of the resolution is the MINIMUM trust tier among
        # the majority coalition (conservatism: never claim more trust than
        # the least-trusted coalition member).
        min_rank = min(
            _TRUST_TIER_RANKS.get(v.trust_tier, 0) for v in majority_coalition
        )
        final_tier = _RANK_TO_TIER.get(min_rank, "unknown")

        # Select the coalition member whose trust rank equals the minimum as
        # the representative "winning" verdict for the resolution record.
        winning_verdict = min(
            majority_coalition,
            key=lambda v: (_TRUST_TIER_RANKS.get(v.trust_tier, 0), v.timestamp),
        )

        reasoning = (
            f"MAJORITY_VOTE: {pass_count} PASS / {fail_count} FAIL out of {total} verdicts. "
            f"Majority outcome: {'PASS' if majority_passed else 'FAIL'}. "
            f"Coalition min-trust tier: {final_tier}. "
            f"Tie-break strategy: {self.tie_break_strategy}."
        )

        _LOG.info(
            "MajorityVoteResolver(%s): conflict=%s → passed=%s tier=%s "
            "(%d/%d votes)",
            self.resolver_id,
            conflict.conflict_id,
            majority_passed,
            final_tier,
            pass_count if majority_passed else fail_count,
            total,
        )

        return ConflictResolutionResult(
            result_id=str(uuid.uuid4()),
            conflict_id=conflict.conflict_id,
            strategy_used=ResolutionStrategy.MAJORITY_VOTE,
            winning_verdict=winning_verdict,
            final_passed=majority_passed,
            final_trust_tier=final_tier,
            reasoning=reasoning,
            escalated=False,
            timestamp=time.time(),
            metadata={
                "resolver_id": self.resolver_id,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "total_votes": total,
                "tie_break_applied": pass_count == fail_count,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _break_tie(
        self, verdicts: tuple[ChannelVerdict, ...]
    ) -> tuple[bool, list[ChannelVerdict]]:
        """Break a vote tie according to :attr:`tie_break_strategy`.

        Parameters
        ----------
        verdicts:
            The full set of verdicts (already confirmed to be a tie).

        Returns
        -------
        tuple[bool, list[ChannelVerdict]]
            The decided pass/fail value and the winning coalition.
        """
        if self.tie_break_strategy == "highest_trust":
            # Adopt the verdict of the highest-trust-rank channel.
            highest = max(
                verdicts,
                key=lambda v: (_TRUST_TIER_RANKS.get(v.trust_tier, 0), -v.timestamp),
            )
            coalition = [v for v in verdicts if v.passed == highest.passed]
            _LOG.debug(
                "MajorityVoteResolver(%s): tie broken by highest_trust → %s",
                self.resolver_id,
                "PASS" if highest.passed else "FAIL",
            )
            return highest.passed, coalition
        else:
            # Default "conservative": ties resolve to FAIL.
            _LOG.debug(
                "MajorityVoteResolver(%s): tie broken conservatively → FAIL",
                self.resolver_id,
            )
            fail_coalition = [v for v in verdicts if not v.passed]
            # If no fails exist (shouldn't happen in a tie with equal counts
            # but guard defensively), use all verdicts.
            coalition = fail_coalition if fail_coalition else list(verdicts)
            return False, coalition


# ---------------------------------------------------------------------------
# ChannelConflictDetector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChannelConflictDetector:
    """Detects conflicts in a set of channel verdicts for the same task.

    The detector performs a single-pass scan (O(n)) over the verdict list,
    checking for each conflict type in turn.  Multiple conflicts may be
    detected for a single set of verdicts (e.g. a verdict mismatch that
    also spans a large trust-tier gap).

    Attributes
    ----------
    detector_id:
        Unique identifier for this detector instance.
    trust_gap_threshold:
        Minimum trust-rank spread that qualifies as a
        :attr:`ConflictType.TRUST_TIER_GAP` conflict.  Default is 2.0,
        meaning a gap of at least 2 trust ranks (e.g. ``high_trust``
        vs ``low_trust``) is required.
    """

    detector_id: str
    trust_gap_threshold: float = 2.0

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def detect(self, verdicts: list[ChannelVerdict]) -> list[ChannelConflict]:
        """Scan *verdicts* and return all detected conflicts.

        Each detection sub-method is called in order.  A conflict is
        appended only when the corresponding sub-method returns a non-None
        value.

        Parameters
        ----------
        verdicts:
            Verdicts for a single task, from one or more channels.

        Returns
        -------
        list[ChannelConflict]
            Zero or more conflict records.  Returns an empty list when all
            channels agree and no anomalies are present.
        """
        if len(verdicts) < 2:
            _LOG.debug(
                "ChannelConflictDetector(%s): fewer than 2 verdicts — no conflicts possible",
                self.detector_id,
            )
            return []

        conflicts: list[ChannelConflict] = []

        mismatch = self._detect_verdict_mismatch(verdicts)
        if mismatch is not None:
            conflicts.append(mismatch)

        gap = self._detect_trust_gap(verdicts)
        if gap is not None:
            conflicts.append(gap)

        timeout = self._detect_timeout(verdicts)
        if timeout is not None:
            conflicts.append(timeout)

        _LOG.info(
            "ChannelConflictDetector(%s): detected %d conflict(s) from %d verdicts",
            self.detector_id,
            len(conflicts),
            len(verdicts),
        )
        return conflicts

    # ------------------------------------------------------------------
    # Detection sub-methods
    # ------------------------------------------------------------------

    def _detect_verdict_mismatch(
        self, verdicts: list[ChannelVerdict]
    ) -> ChannelConflict | None:
        """Detect a VERDICT_MISMATCH conflict.

        A mismatch exists when not all verdicts agree on pass/fail.

        Parameters
        ----------
        verdicts:
            List of verdicts (length ≥ 2 is assumed by caller).

        Returns
        -------
        ChannelConflict or None
            A conflict record when a mismatch is found; None otherwise.
        """
        pass_verdicts = [v for v in verdicts if v.passed]
        fail_verdicts = [v for v in verdicts if not v.passed]

        if not pass_verdicts or not fail_verdicts:
            return None  # unanimous

        task_id = verdicts[0].task_id
        description = (
            f"Verdict mismatch on task {task_id}: "
            f"{len(pass_verdicts)} channel(s) PASS "
            f"({', '.join(v.channel for v in pass_verdicts)}) vs "
            f"{len(fail_verdicts)} channel(s) FAIL "
            f"({', '.join(v.channel for v in fail_verdicts)}). "
            f"Conservatism principle requires holding the FAIL result."
        )
        _LOG.debug(
            "ChannelConflictDetector(%s): verdict mismatch on task %s",
            self.detector_id,
            task_id,
        )
        return ChannelConflict(
            conflict_id=str(uuid.uuid4()),
            task_id=task_id,
            conflict_type=ConflictType.VERDICT_MISMATCH,
            verdicts=tuple(verdicts),
            detected_at=time.time(),
            description=description,
            metadata={
                "pass_count": len(pass_verdicts),
                "fail_count": len(fail_verdicts),
                "pass_channels": [v.channel for v in pass_verdicts],
                "fail_channels": [v.channel for v in fail_verdicts],
            },
        )

    def _detect_trust_gap(
        self, verdicts: list[ChannelVerdict]
    ) -> ChannelConflict | None:
        """Detect a TRUST_TIER_GAP conflict.

        A trust-tier gap exists when the spread between the highest and
        lowest trust ranks among the verdicts meets or exceeds
        :attr:`trust_gap_threshold`.

        Parameters
        ----------
        verdicts:
            List of verdicts (length ≥ 2 is assumed by caller).

        Returns
        -------
        ChannelConflict or None
            A conflict record when the gap is large enough; None otherwise.
        """
        ranks = [(_TRUST_TIER_RANKS.get(v.trust_tier, 0), v) for v in verdicts]
        max_rank, max_v = max(ranks, key=lambda x: x[0])
        min_rank, min_v = min(ranks, key=lambda x: x[0])
        spread = max_rank - min_rank

        if spread < self.trust_gap_threshold:
            return None

        task_id = verdicts[0].task_id
        description = (
            f"Trust-tier gap on task {task_id}: "
            f"highest tier is {max_v.trust_tier!r} (channel={max_v.channel}, rank={max_rank}), "
            f"lowest tier is {min_v.trust_tier!r} (channel={min_v.channel}, rank={min_rank}). "
            f"Spread {spread} ≥ threshold {self.trust_gap_threshold}."
        )
        _LOG.debug(
            "ChannelConflictDetector(%s): trust gap %d on task %s",
            self.detector_id,
            spread,
            task_id,
        )
        return ChannelConflict(
            conflict_id=str(uuid.uuid4()),
            task_id=task_id,
            conflict_type=ConflictType.TRUST_TIER_GAP,
            verdicts=tuple(verdicts),
            detected_at=time.time(),
            description=description,
            metadata={
                "max_trust_tier": max_v.trust_tier,
                "min_trust_tier": min_v.trust_tier,
                "spread": spread,
                "threshold": self.trust_gap_threshold,
            },
        )

    def _detect_timeout(
        self, verdicts: list[ChannelVerdict]
    ) -> ChannelConflict | None:
        """Detect a TIMEOUT_CONFLICT among verdicts.

        A timeout conflict is flagged when any verdict's ``latency_seconds``
        exceeds a hard ceiling of 60 seconds, or when a verdict's
        ``evidence_summary`` contains the marker string ``"timeout"``.
        This is a heuristic proxy for actual SLA data; production deployments
        should inject real SLA metadata via the ``metadata`` field.

        Parameters
        ----------
        verdicts:
            List of verdicts (length ≥ 2 is assumed by caller).

        Returns
        -------
        ChannelConflict or None
            A conflict record when a timed-out verdict is found; None otherwise.
        """
        _LATENCY_CEILING_S = 60.0
        timed_out = [
            v
            for v in verdicts
            if v.latency_seconds > _LATENCY_CEILING_S
            or "timeout" in v.evidence_summary.lower()
        ]
        if not timed_out:
            return None

        task_id = verdicts[0].task_id
        description = (
            f"Timeout conflict on task {task_id}: "
            f"{len(timed_out)} verdict(s) from channel(s) "
            f"{', '.join(v.channel for v in timed_out)} exceeded the latency ceiling "
            f"({_LATENCY_CEILING_S}s) or reported a timeout."
        )
        _LOG.warning(
            "ChannelConflictDetector(%s): timeout conflict on task %s (%d slow verdicts)",
            self.detector_id,
            task_id,
            len(timed_out),
        )
        return ChannelConflict(
            conflict_id=str(uuid.uuid4()),
            task_id=task_id,
            conflict_type=ConflictType.TIMEOUT_CONFLICT,
            verdicts=tuple(verdicts),
            detected_at=time.time(),
            description=description,
            metadata={
                "timed_out_channels": [v.channel for v in timed_out],
                "latency_ceiling_s": _LATENCY_CEILING_S,
            },
        )


# ---------------------------------------------------------------------------
# ChannelConflictResolver
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChannelConflictResolver:
    """Top-level resolver that dispatches to strategy-specific sub-resolvers.

    The :class:`ChannelConflictResolver` acts as a strategy router: given a
    :class:`ChannelConflict` and an optional explicit :class:`ResolutionStrategy`,
    it selects the appropriate sub-resolver and returns a
    :class:`ConflictResolutionResult`.

    All resolved conflicts are appended to :attr:`resolution_history` so that
    callers can audit the resolver's behaviour over time.

    Attributes
    ----------
    resolver_id:
        Unique identifier for this resolver instance.
    default_strategy:
        The :class:`ResolutionStrategy` to use when the caller does not
        supply an explicit strategy.  Defaults to
        :attr:`ResolutionStrategy.LOWEST_TRUST_CONSERVATIVE` per the
        theory2 mandate.
    resolution_history:
        Ordered list of :class:`ConflictResolutionResult` records produced
        by this resolver since instantiation.
    detector:
        The :class:`ChannelConflictDetector` used by
        :meth:`detect_and_resolve`.
    """

    resolver_id: str
    default_strategy: ResolutionStrategy = ResolutionStrategy.LOWEST_TRUST_CONSERVATIVE
    resolution_history: list[ConflictResolutionResult] = field(default_factory=list)
    detector: ChannelConflictDetector = field(
        default_factory=lambda: ChannelConflictDetector(
            detector_id=str(uuid.uuid4())
        )
    )

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def resolve(
        self,
        conflict: ChannelConflict,
        strategy: ResolutionStrategy | None = None,
    ) -> ConflictResolutionResult:
        """Resolve *conflict* using *strategy* (or :attr:`default_strategy`).

        Parameters
        ----------
        conflict:
            The :class:`ChannelConflict` to resolve.
        strategy:
            Optional override for the resolution strategy.  When ``None``,
            :attr:`default_strategy` is used.

        Returns
        -------
        ConflictResolutionResult
            The resolution record, also appended to :attr:`resolution_history`.
        """
        chosen = strategy if strategy is not None else self.default_strategy

        _LOG.debug(
            "ChannelConflictResolver(%s): resolving conflict=%s strategy=%s",
            self.resolver_id,
            conflict.conflict_id,
            chosen.value,
        )

        try:
            result = self._dispatch(conflict, chosen)
        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "ChannelConflictResolver(%s): error resolving conflict=%s: %s",
                self.resolver_id,
                conflict.conflict_id,
                exc,
                exc_info=True,
            )
            result = ConflictResolutionResult(
                result_id=str(uuid.uuid4()),
                conflict_id=conflict.conflict_id,
                strategy_used=chosen,
                winning_verdict=None,
                final_passed=False,
                final_trust_tier="unknown",
                reasoning=f"Resolution failed with exception: {exc}. Defaulting to FAIL.",
                escalated=False,
                timestamp=time.time(),
                metadata={"error": str(exc)},
            )

        self.resolution_history.append(result)
        return result

    def resolve_all(
        self, conflicts: list[ChannelConflict]
    ) -> list[ConflictResolutionResult]:
        """Resolve every conflict in *conflicts* using the default strategy.

        Parameters
        ----------
        conflicts:
            List of :class:`ChannelConflict` instances to resolve.

        Returns
        -------
        list[ConflictResolutionResult]
            Results in the same order as *conflicts*.
        """
        return [self.resolve(c) for c in conflicts]

    def detect_and_resolve(
        self, verdicts: list[ChannelVerdict]
    ) -> tuple[list[ChannelConflict], list[ConflictResolutionResult]]:
        """Detect conflicts in *verdicts* and resolve each one.

        Convenience method that combines :meth:`ChannelConflictDetector.detect`
        with :meth:`resolve_all` in a single call.

        Parameters
        ----------
        verdicts:
            List of :class:`ChannelVerdict` instances for the same task.

        Returns
        -------
        tuple[list[ChannelConflict], list[ConflictResolutionResult]]
            The detected conflicts and their corresponding resolutions.
        """
        conflicts = self.detector.detect(verdicts)
        results = self.resolve_all(conflicts)
        return conflicts, results

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return aggregate statistics over :attr:`resolution_history`.

        Returns
        -------
        dict
            Keys: ``total``, ``passed``, ``failed``, ``escalated``,
            ``by_strategy`` (count per strategy name),
            ``by_conflict_type`` is not available here (conflict type is
            on the :class:`ChannelConflict`, not the result).
        """
        total = len(self.resolution_history)
        passed = sum(1 for r in self.resolution_history if r.final_passed)
        failed = total - passed
        escalated = sum(1 for r in self.resolution_history if r.escalated)

        by_strategy: dict[str, int] = {}
        for r in self.resolution_history:
            key = r.strategy_used.value
            by_strategy[key] = by_strategy.get(key, 0) + 1

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "escalated": escalated,
            "by_strategy": by_strategy,
        }

    def export(self) -> dict[str, Any]:
        """Export the full resolution history as a serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``resolver_id``, ``default_strategy``, ``stats``,
            ``resolution_history`` (list of serialised results).
        """
        return {
            "resolver_id": self.resolver_id,
            "default_strategy": self.default_strategy.value,
            "stats": self.stats(),
            "resolution_history": [r.to_dict() for r in self.resolution_history],
        }

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _dispatch(
        self, conflict: ChannelConflict, strategy: ResolutionStrategy
    ) -> ConflictResolutionResult:
        """Route *conflict* to the appropriate strategy sub-resolver.

        Parameters
        ----------
        conflict:
            Conflict to resolve.
        strategy:
            Selected strategy.

        Returns
        -------
        ConflictResolutionResult
            The sub-resolver's result.

        Raises
        ------
        ValueError
            If *strategy* is not a recognised :class:`ResolutionStrategy`.
        """
        if strategy == ResolutionStrategy.LOWEST_TRUST_CONSERVATIVE:
            sub = TrustConservativeResolver(resolver_id=f"{self.resolver_id}:conservative")
            return sub.resolve(conflict)

        if strategy == ResolutionStrategy.MAJORITY_VOTE:
            sub = MajorityVoteResolver(resolver_id=f"{self.resolver_id}:majority")
            return sub.resolve(conflict)

        if strategy == ResolutionStrategy.REQUIRE_CONSENSUS:
            return self._resolve_consensus(conflict)

        if strategy == ResolutionStrategy.HIGHEST_TRUST_WINS:
            return self._resolve_highest_trust(conflict)

        if strategy == ResolutionStrategy.ESCALATE_TO_HUMAN:
            return self._resolve_escalate(conflict)

        raise ValueError(f"Unknown resolution strategy: {strategy!r}")

    def _resolve_consensus(self, conflict: ChannelConflict) -> ConflictResolutionResult:
        """Resolve by requiring all channels to agree (AND-gate).

        If any verdict fails, the resolution is FAIL at the minimum trust tier.
        Only when every verdict passes is the resolution PASS.

        Parameters
        ----------
        conflict:
            The conflict to resolve.

        Returns
        -------
        ConflictResolutionResult
        """
        all_pass = all(v.passed for v in conflict.verdicts)
        min_rank = min(
            (_TRUST_TIER_RANKS.get(v.trust_tier, 0) for v in conflict.verdicts),
            default=0,
        )
        final_tier = _RANK_TO_TIER.get(min_rank, "unknown")

        if all_pass:
            # Representative winner: lowest trust tier (conservative)
            winner = min(
                conflict.verdicts,
                key=lambda v: (_TRUST_TIER_RANKS.get(v.trust_tier, 0), v.timestamp),
            )
            reasoning = (
                f"REQUIRE_CONSENSUS: all {len(conflict.verdicts)} verdicts PASS. "
                f"Resolution is PASS at min trust tier {final_tier!r}."
            )
        else:
            fail_verdicts = [v for v in conflict.verdicts if not v.passed]
            winner = min(
                fail_verdicts,
                key=lambda v: (_TRUST_TIER_RANKS.get(v.trust_tier, 0), v.timestamp),
            )
            reasoning = (
                f"REQUIRE_CONSENSUS: {len(fail_verdicts)} verdict(s) FAIL — consensus "
                f"not achieved. Resolution is FAIL at min trust tier {final_tier!r}."
            )

        return ConflictResolutionResult(
            result_id=str(uuid.uuid4()),
            conflict_id=conflict.conflict_id,
            strategy_used=ResolutionStrategy.REQUIRE_CONSENSUS,
            winning_verdict=winner,
            final_passed=all_pass,
            final_trust_tier=final_tier,
            reasoning=reasoning,
            escalated=False,
            timestamp=time.time(),
            metadata={"consensus_achieved": all_pass},
        )

    def _resolve_highest_trust(
        self, conflict: ChannelConflict
    ) -> ConflictResolutionResult:
        """Resolve by deferring to the highest-trust-rank verdict.

        *Warning*: This strategy may violate the theory2 conservatism
        invariant.  Use only in explicitly justified contexts.

        Parameters
        ----------
        conflict:
            The conflict to resolve.

        Returns
        -------
        ConflictResolutionResult
        """
        if not conflict.verdicts:
            return ConflictResolutionResult(
                result_id=str(uuid.uuid4()),
                conflict_id=conflict.conflict_id,
                strategy_used=ResolutionStrategy.HIGHEST_TRUST_WINS,
                winning_verdict=None,
                final_passed=False,
                final_trust_tier="unknown",
                reasoning="No verdicts; defaulting to FAIL.",
                escalated=False,
                timestamp=time.time(),
                metadata={},
            )

        winner = max(
            conflict.verdicts,
            key=lambda v: (_TRUST_TIER_RANKS.get(v.trust_tier, 0), v.confidence),
        )
        reasoning = (
            f"HIGHEST_TRUST_WINS: selected verdict from channel={winner.channel} "
            f"(trust_tier={winner.trust_tier}, rank={_TRUST_TIER_RANKS.get(winner.trust_tier, 0)}, "
            f"confidence={winner.confidence:.3f}). "
            f"WARNING: may violate theory2 conservatism invariant."
        )
        _LOG.warning(
            "ChannelConflictResolver(%s): HIGHEST_TRUST_WINS may violate "
            "theory2 conservatism invariant for conflict=%s",
            self.resolver_id,
            conflict.conflict_id,
        )
        return ConflictResolutionResult(
            result_id=str(uuid.uuid4()),
            conflict_id=conflict.conflict_id,
            strategy_used=ResolutionStrategy.HIGHEST_TRUST_WINS,
            winning_verdict=winner,
            final_passed=winner.passed,
            final_trust_tier=winner.trust_tier,
            reasoning=reasoning,
            escalated=False,
            timestamp=time.time(),
            metadata={
                "conservatism_invariant_warning": (
                    "HIGHEST_TRUST_WINS may silently upgrade trust; verify caller intent."
                )
            },
        )

    def _resolve_escalate(
        self, conflict: ChannelConflict
    ) -> ConflictResolutionResult:
        """Resolve by escalating the conflict to a human reviewer.

        The result is immediately ``final_passed=False`` and
        ``escalated=True``, pending human resolution.

        Parameters
        ----------
        conflict:
            The conflict to escalate.

        Returns
        -------
        ConflictResolutionResult
        """
        _LOG.info(
            "ChannelConflictResolver(%s): escalating conflict=%s to human",
            self.resolver_id,
            conflict.conflict_id,
        )
        min_rank = min(
            (_TRUST_TIER_RANKS.get(v.trust_tier, 0) for v in conflict.verdicts),
            default=0,
        )
        final_tier = _RANK_TO_TIER.get(min_rank, "unknown")
        reasoning = (
            f"ESCALATE_TO_HUMAN: conflict {conflict.conflict_id} on task "
            f"{conflict.task_id} ({conflict.conflict_type.value}) forwarded "
            f"to human review queue. Channels involved: "
            f"{', '.join(conflict.channels_involved())}. "
            f"Holding FAIL until human resolution."
        )
        return ConflictResolutionResult(
            result_id=str(uuid.uuid4()),
            conflict_id=conflict.conflict_id,
            strategy_used=ResolutionStrategy.ESCALATE_TO_HUMAN,
            winning_verdict=None,
            final_passed=False,
            final_trust_tier=final_tier,
            reasoning=reasoning,
            escalated=True,
            timestamp=time.time(),
            metadata={
                "escalation_reason": conflict.description,
                "conflict_type": conflict.conflict_type.value,
            },
        )


# ---------------------------------------------------------------------------
# ConflictResolutionCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConflictResolutionCoordinator:
    """Orchestrates conflict detection and resolution at scale.

    The coordinator provides a submission queue so that verdicts can be
    submitted in batches (e.g. at the end of a routing round) and processed
    asynchronously.  This design mirrors the pipeline architecture described
    in §45.6.1 of theory2.tex.

    Attributes
    ----------
    coordinator_id:
        Unique identifier for this coordinator instance.
    resolver:
        The :class:`ChannelConflictResolver` used to resolve detected
        conflicts.
    pending_conflicts:
        Ordered list of :class:`ChannelConflict` instances awaiting
        resolution.
    resolved_conflicts:
        Ordered list of :class:`ConflictResolutionResult` instances
        produced since this coordinator was created.
    """

    coordinator_id: str
    resolver: ChannelConflictResolver = field(
        default_factory=lambda: ChannelConflictResolver(
            resolver_id=str(uuid.uuid4())
        )
    )
    pending_conflicts: list[ChannelConflict] = field(default_factory=list)
    resolved_conflicts: list[ConflictResolutionResult] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Submission API
    # ------------------------------------------------------------------

    def submit_verdicts(
        self, task_id: str, verdicts: list[ChannelVerdict]
    ) -> list[ChannelConflict]:
        """Detect conflicts in *verdicts* and add them to the pending queue.

        Parameters
        ----------
        task_id:
            Identifier of the task the verdicts relate to.  Used for
            logging only; the actual task ID is taken from each
            :class:`ChannelVerdict`.
        verdicts:
            List of channel verdicts for the task.

        Returns
        -------
        list[ChannelConflict]
            The newly detected conflicts (also appended to
            :attr:`pending_conflicts`).
        """
        _LOG.debug(
            "ConflictResolutionCoordinator(%s): submitting %d verdicts for task %s",
            self.coordinator_id,
            len(verdicts),
            task_id,
        )
        new_conflicts = self.resolver.detector.detect(verdicts)
        self.pending_conflicts.extend(new_conflicts)
        _LOG.info(
            "ConflictResolutionCoordinator(%s): task %s → %d new conflict(s), "
            "%d total pending",
            self.coordinator_id,
            task_id,
            len(new_conflicts),
            len(self.pending_conflicts),
        )
        return new_conflicts

    def process_pending(self) -> list[ConflictResolutionResult]:
        """Resolve all pending conflicts and move them to the resolved list.

        Returns
        -------
        list[ConflictResolutionResult]
            Results for the conflicts that were pending at call time.
            The :attr:`pending_conflicts` list is cleared.
        """
        if not self.pending_conflicts:
            _LOG.debug(
                "ConflictResolutionCoordinator(%s): no pending conflicts to process",
                self.coordinator_id,
            )
            return []

        to_resolve = list(self.pending_conflicts)
        self.pending_conflicts.clear()

        _LOG.info(
            "ConflictResolutionCoordinator(%s): processing %d pending conflict(s)",
            self.coordinator_id,
            len(to_resolve),
        )

        results = self.resolver.resolve_all(to_resolve)
        self.resolved_conflicts.extend(results)

        _LOG.info(
            "ConflictResolutionCoordinator(%s): resolved %d conflict(s); "
            "%d total resolved",
            self.coordinator_id,
            len(results),
            len(self.resolved_conflicts),
        )
        return results

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return a current-state status summary.

        Returns
        -------
        dict
            Keys: ``coordinator_id``, ``pending_count``, ``resolved_count``,
            ``resolver_stats``.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "pending_count": len(self.pending_conflicts),
            "resolved_count": len(self.resolved_conflicts),
            "resolver_stats": self.resolver.stats(),
        }

    def health(self) -> dict[str, Any]:
        """Return a health indicator for the coordinator.

        Health is ``"ok"`` when there are no pending conflicts and the
        resolver has a fail rate below 80 %.  It is ``"degraded"`` when
        pending conflicts exist (they need processing).  It is
        ``"unhealthy"`` when the fail rate exceeds 80 % (possible
        systematic issue with verdict quality).

        Returns
        -------
        dict
            Keys: ``status`` (one of ``"ok"``, ``"degraded"``,
            ``"unhealthy"``), ``pending_count``, ``fail_rate``,
            ``details``.
        """
        stats = self.resolver.stats()
        total = stats["total"]
        failed = stats["failed"]
        fail_rate = failed / max(total, 1)
        pending = len(self.pending_conflicts)

        if fail_rate > 0.8:
            status = "unhealthy"
            details = (
                f"Fail rate {fail_rate:.1%} exceeds 80 % threshold — "
                f"check verdict quality."
            )
        elif pending > 0:
            status = "degraded"
            details = f"{pending} conflict(s) awaiting resolution."
        else:
            status = "ok"
            details = "No pending conflicts; fail rate within bounds."

        return {
            "status": status,
            "pending_count": pending,
            "fail_rate": fail_rate,
            "details": details,
        }


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_default_conflict_detector() -> ChannelConflictDetector:
    """Create a :class:`ChannelConflictDetector` with default settings.

    Uses a trust-gap threshold of 2.0 (detects gaps ≥ 2 trust ranks).

    Returns
    -------
    ChannelConflictDetector
        Ready-to-use detector instance.
    """
    return ChannelConflictDetector(
        detector_id=str(uuid.uuid4()),
        trust_gap_threshold=2.0,
    )


def make_default_channel_conflict_resolver() -> ChannelConflictResolver:
    """Create a :class:`ChannelConflictResolver` with conservative defaults.

    The default strategy is :attr:`ResolutionStrategy.LOWEST_TRUST_CONSERVATIVE`
    as mandated by the theory2 Conservatism Principle (§45.5 Theorem 2).

    Returns
    -------
    ChannelConflictResolver
        Fully configured resolver with a fresh detector and empty history.
    """
    return ChannelConflictResolver(
        resolver_id=str(uuid.uuid4()),
        default_strategy=ResolutionStrategy.LOWEST_TRUST_CONSERVATIVE,
        resolution_history=[],
        detector=make_default_conflict_detector(),
    )


def make_default_resolution_coordinator() -> ConflictResolutionCoordinator:
    """Create a :class:`ConflictResolutionCoordinator` with default settings.

    The coordinator uses a conservative :class:`ChannelConflictResolver` so
    that all automated resolutions honour the theory2 invariant out of the box.

    Returns
    -------
    ConflictResolutionCoordinator
        Ready-to-use coordinator instance with empty queues.
    """
    return ConflictResolutionCoordinator(
        coordinator_id=str(uuid.uuid4()),
        resolver=make_default_channel_conflict_resolver(),
        pending_conflicts=[],
        resolved_conflicts=[],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "ConflictType",
    "ResolutionStrategy",
    # Domain dataclasses
    "ChannelVerdict",
    "ChannelConflict",
    "ConflictResolutionResult",
    # Resolvers
    "TrustConservativeResolver",
    "MajorityVoteResolver",
    # Detection
    "ChannelConflictDetector",
    # Top-level orchestration
    "ChannelConflictResolver",
    "ConflictResolutionCoordinator",
    # Factory functions
    "make_default_conflict_detector",
    "make_default_channel_conflict_resolver",
    "make_default_resolution_coordinator",
]

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=== channel_conflict_resolution.py — smoke test ===\n")

    now = time.time()
    task_id = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # 1. Create three ChannelVerdicts for the same task.
    #
    #    Channel      | Verdict | Trust tier
    #    -------------|---------|------------
    #    z3           | PASS    | high_trust
    #    copilot_llm  | FAIL    | low_trust
    #    runtime      | PASS    | medium_trust
    #
    #    Theory2 invariant: the FAIL from copilot_llm (low_trust) must
    #    prevent the conservative resolver from silently returning PASS.
    # ------------------------------------------------------------------

    v_z3 = ChannelVerdict(
        verdict_id=str(uuid.uuid4()),
        task_id=task_id,
        channel="z3",
        passed=True,
        confidence=0.99,
        trust_tier="high_trust",
        evidence_summary="Z3 returned sat for (assert (> x 0))",
        latency_seconds=0.12,
        timestamp=now,
        metadata={"solver": "z3", "formula": "(assert (> x 0))"},
    )

    v_llm = ChannelVerdict(
        verdict_id=str(uuid.uuid4()),
        task_id=task_id,
        channel="copilot_llm",
        passed=False,
        confidence=0.61,
        trust_tier="low_trust",
        evidence_summary="LLM heuristic: formula may fail on boundary inputs",
        latency_seconds=1.45,
        timestamp=now + 1.0,
        metadata={"model": "gpt-4", "tokens": 512},
    )

    v_runtime = ChannelVerdict(
        verdict_id=str(uuid.uuid4()),
        task_id=task_id,
        channel="runtime_witness",
        passed=True,
        confidence=0.87,
        trust_tier="medium_trust",
        evidence_summary="10/10 property tests passed",
        latency_seconds=0.55,
        timestamp=now + 0.5,
        metadata={"tests_run": 10, "passed": 10},
    )

    verdicts = [v_z3, v_llm, v_runtime]

    print(f"Task ID : {task_id}")
    print(f"Verdicts: {len(verdicts)}")
    for v in verdicts:
        label = "PASS" if v.passed else "FAIL"
        print(f"  [{label}] channel={v.channel!r:20s} trust={v.trust_tier!r:14s} "
              f"conf={v.confidence:.2f}")

    # ------------------------------------------------------------------
    # 2. Detect conflicts.
    # ------------------------------------------------------------------

    print("\n--- Conflict Detection ---")
    coordinator = make_default_resolution_coordinator()
    new_conflicts = coordinator.submit_verdicts(task_id, verdicts)

    print(f"Conflicts detected: {len(new_conflicts)}")
    for c in new_conflicts:
        print(f"  [{c.conflict_type.value}] id={c.conflict_id[:8]}…")
        print(f"    trust_spread={c.trust_spread():.0f}  "
              f"channels={c.channels_involved()}")
        print(f"    description: {c.description[:100]}…")

    # ------------------------------------------------------------------
    # 3. Resolve with the conservative strategy (default).
    # ------------------------------------------------------------------

    print("\n--- Conservative Resolution ---")
    results = coordinator.process_pending()

    for r in results:
        label = "PASS" if r.final_passed else "FAIL"
        print(f"  Conflict {r.conflict_id[:8]}… → [{label}] "
              f"tier={r.final_trust_tier!r} strategy={r.strategy_used.value}")
        print(f"    Reasoning: {r.reasoning[:120]}…")
        if r.winning_verdict:
            wv = r.winning_verdict
            print(f"    Winner: channel={wv.channel!r}, passed={wv.passed}, "
                  f"trust={wv.trust_tier!r}")

    # ------------------------------------------------------------------
    # 4. Verify the theory2 invariant: the resolution must be FAIL
    #    because the low-trust LLM said FAIL.
    # ------------------------------------------------------------------

    print("\n--- Theory2 Invariant Verification ---")
    verdict_mismatch_results = [
        r for r in results
        if any(
            c.conflict_type == ConflictType.VERDICT_MISMATCH
            for c in new_conflicts
            if c.conflict_id == r.conflict_id
        )
    ]
    if verdict_mismatch_results:
        r = verdict_mismatch_results[0]
        invariant_holds = not r.final_passed
        print(f"  Verdict mismatch resolution: final_passed={r.final_passed}")
        print(f"  Theory2 invariant holds (FAIL preserved): {invariant_holds}")
        assert invariant_holds, (
            "INVARIANT VIOLATION: conservative resolver returned PASS despite "
            "a low-trust channel saying FAIL!"
        )
        print("  ✓ Conservatism principle satisfied.")
    else:
        print("  (No verdict-mismatch conflict found — nothing to verify.)")

    # ------------------------------------------------------------------
    # 5. Coordinator status and health.
    # ------------------------------------------------------------------

    print("\n--- Coordinator Status ---")
    status = coordinator.status()
    print(f"  Pending  : {status['pending_count']}")
    print(f"  Resolved : {status['resolved_count']}")
    print(f"  Resolver stats: {status['resolver_stats']}")

    health = coordinator.health()
    print(f"\n--- Coordinator Health ---")
    print(f"  Status   : {health['status']}")
    print(f"  Fail rate: {health['fail_rate']:.1%}")
    print(f"  Details  : {health['details']}")

    # ------------------------------------------------------------------
    # 6. Demonstrate majority-vote resolver on the same conflict.
    # ------------------------------------------------------------------

    print("\n--- Majority Vote Demo (2 PASS / 1 FAIL) ---")
    majority_resolver = MajorityVoteResolver(
        resolver_id="majority-demo", tie_break_strategy="conservative"
    )
    if new_conflicts:
        # Re-use the first conflict
        mv_result = majority_resolver.resolve(new_conflicts[0])
        print(f"  Majority result: {'PASS' if mv_result.final_passed else 'FAIL'} "
              f"(tier={mv_result.final_trust_tier!r})")
        print(f"  Reasoning: {mv_result.reasoning}")

    print("\n=== Smoke test complete ===")
