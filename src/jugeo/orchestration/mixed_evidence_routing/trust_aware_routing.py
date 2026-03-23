"""Trust-aware routing for the mixed-evidence routing layer.

This module implements the trust-aware routing machinery described in
*theory2.tex* Ch 45 ("Channel Selection in Mixed-Evidence Routing Systems"),
§45.4 ("Trust-Aware Routing").  That section extends the basic channel-selection
algorithm of §45.3 with a formal *trust algebra*: every routing decision must
respect the registered *trust ceiling* of the chosen channel, and no decision
may upgrade a result to a trust tier higher than the ceiling allows.

The central invariant of the theory2 trust algebra (§45.4.1) is:

    **A channel can only return evidence at or below its registered trust
    ceiling.  Routing NEVER upgrades trust tier without explicit justification.**

This invariant is enforced at three levels:

1. ``TrustCeilingMap`` — statically declares the maximum achievable
   ``TrustTier`` for each ``EvidenceChannel``.
2. ``TrustAwareRouter.route`` — selects only channels whose ceiling meets or
   exceeds the ``TrustRequirement`` of the incoming task.
3. ``TrustRoutingWitness.verify_no_upgrades`` — post-hoc audit that scans the
   append-only witness log and flags any recorded decision where the claimed
   trust tier exceeds the channel's ceiling.

Key responsibilities
--------------------
* ``TrustTier`` — ordered enumeration of trust tiers from highest (mechanically
  verified) to lowest (unverified).  Exposes ``rank()`` and ``can_satisfy()``
  class methods so comparisons never rely on ordinal position in the enum.
* ``TrustRequirement`` — immutable description of the minimum trust tier needed
  to satisfy a routing request.  Supports downgrade allowance for tasks that
  can tolerate a weaker evidence channel when the ideal channel is unavailable.
* ``TrustCeilingMap`` — bidirectional map between evidence channels and their
  trust ceilings.  The ``default_map()`` factory reflects the trust algebra
  defined in §45.4.3 of theory2.tex.
* ``TrustAwareRoutingDecision`` — extends the basic routing decision with a
  snapshot of the ceiling map at the time of routing, the requirement that was
  being satisfied, and a ``trust_preserved()`` predicate that verifies the
  invariant holds for this particular decision.
* ``TrustRoutingAnalyzer`` — audits a stream of ``TrustAwareRoutingDecision``
  instances, collects violation messages, and computes compliance statistics.
* ``TrustAwareRouter`` — core router that combines ceiling-map filtering,
  channel scoring, rationale generation, and decision history.
* ``TrustRoutingCoordinator`` — orchestrates multiple ``TrustAwareRouter``
  instances partitioned by trust *domain* (e.g., "formal", "empirical",
  "heuristic").
* ``TrustRoutingWitness`` — append-only audit log that records every routing
  decision and every violation, and exposes ``verify_no_upgrades()`` for
  end-to-end trust-algebra compliance checking.

Design notes
------------
* All dataclasses that represent decisions or requirements are ``frozen=True``
  to prevent accidental post-hoc mutation that could mask trust violations.
* ``TrustCeilingMap`` is deliberately *not* frozen because the ceiling map may
  be updated at runtime (e.g., when a solver is upgraded and its ceiling
  improves), but such updates must be explicit calls to ``set_ceiling()``.
* Scoring in ``TrustAwareRouter._score_channel`` combines three components:
  tier compatibility (binary gate), a continuous trust-rank bonus, and a
  task-specific complexity penalty, producing a float in [0, 1].
* Factory functions (``make_default_trust_ceiling_map``, etc.) are provided so
  test fixtures and integration harnesses can obtain fully configured objects
  without knowing construction details.

References
----------
* theory2.tex Ch 45 §45.4 — "Trust-Aware Routing"
* theory2.tex Ch 45 §45.4.1 — Trust-algebra invariant (no upgrade without
  justification)
* theory2.tex Ch 45 §45.4.2 — Trust tier ordering and the ``can_satisfy``
  relation
* theory2.tex Ch 45 §45.4.3 — Default ceiling assignments per channel
* theory2.tex Ch 45 §45.4.4 — Compliance audit procedures and the witness log
* theory2.tex Ch 45 §45.3 — Basic channel-selection algorithm (predecessor
  section, implemented in channel_selection.py)
"""

from __future__ import annotations

import abc  # noqa: F401 — re-exported for downstream sub-classers
import enum
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Models import (guarded — provides fallback stubs when models package absent)
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.mixed_evidence_routing.models import (  # type: ignore[import]
        EvidenceChannel,
        RoutingDecision,
        RoutingHistory,
        JurisdictionMap,
        ChannelStats,
        EscalationUrgency,
    )
except Exception:  # pragma: no cover

    class EvidenceChannel(str, enum.Enum):  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.mixed_evidence_routing.models.EvidenceChannel."""

        Z3 = "z3"
        COPILOT_LLM = "copilot_llm"
        RUNTIME_WITNESS = "runtime_witness"
        HUMAN = "human"

    @dataclass
    class RoutingDecision:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.mixed_evidence_routing.models.RoutingDecision."""

        decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        channel: Any = None
        task_id: str = ""
        claim_kind: str = ""
        confidence: float = 0.0
        estimated_cost: float = 0.0
        estimated_latency: float = 0.0

        @classmethod
        def new(cls, **kwargs: Any) -> RoutingDecision:
            return cls(**kwargs)

    @dataclass
    class RoutingHistory:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.mixed_evidence_routing.models.RoutingHistory."""

        decisions: list = field(default_factory=list)

        def record(self, decision: Any) -> None:
            self.decisions.append(decision)

        def average_confidence(self) -> float:
            if not self.decisions:
                return 0.0
            return sum(d.confidence for d in self.decisions) / len(self.decisions)

    @dataclass
    class JurisdictionMap:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.mixed_evidence_routing.models.JurisdictionMap."""

        channel: Any = None
        claim_kinds: list = field(default_factory=list)

        def can_handle(self, task: dict) -> bool:
            return task.get("claim_kind") in self.claim_kinds

        def complexity_score(self, task: dict) -> float:
            return 0.5

        @classmethod
        def new(cls, **kwargs: Any) -> JurisdictionMap:
            return cls(
                channel=kwargs.get("channel"),
                claim_kinds=kwargs.get("claim_kinds", []),
            )

    @dataclass
    class ChannelStats:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.mixed_evidence_routing.models.ChannelStats."""

        channel: Any = None
        total_calls: int = 0
        success_count: int = 0

        def update(self, decision: Any, success: bool) -> None:
            self.total_calls += 1
            if success:
                self.success_count += 1

    class EscalationUrgency(str, enum.Enum):  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.mixed_evidence_routing.models.EscalationUrgency."""

        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"
        CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Trust algebra import (guarded — provides fallback stubs when absent)
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustCeiling  # type: ignore[import]

    _TRUST_ALGEBRA_AVAILABLE = True
except Exception:  # pragma: no cover
    _TRUST_ALGEBRA_AVAILABLE = False

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustLevel."""

        VERIFIED = "VERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        WITNESSED = "WITNESSED"
        UNKNOWN = "UNKNOWN"

    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustAlgebra."""

        @staticmethod
        def compose(a: Any, b: Any) -> Any:
            return a

    class TrustCeiling:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustCeiling."""

        def __init__(self, tier: str) -> None:
            self.tier = tier


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Version tag for this module — bumped on every theory2.tex-driven revision.
MODULE_VERSION: str = "0.2.0"

#: Default fallback channel value when no eligible channel can be found.
DEFAULT_FALLBACK_CHANNEL: str = "human"

#: Minimum confidence threshold below which a routing decision triggers a
#: warning in the compliance report.
MIN_CONFIDENCE_THRESHOLD: float = 0.30

#: Trust-rank bonus applied to the channel score for each tier step above the
#: minimum required by the task.  Kept intentionally small so over-qualified
#: channels (those with ceiling much higher than required) are discouraged;
#: the over-qualification penalty dominates after the first step.
TRUST_RANK_BONUS_PER_STEP: float = 0.03

#: Maximum trust-rank bonus that can be accumulated.  Caps the bonus at two
#: rank steps so a moderately over-qualified channel still beats an exact-match
#: channel only when their scores are otherwise equal.
MAX_TRUST_RANK_BONUS: float = 0.06

#: Penalty applied per rank step by which a channel's ceiling *exceeds* the
#: required minimum.  An over-qualified channel wastes high-trust capacity on
#: low-trust tasks; this penalty steers routing toward exact-match channels.
#: Must be larger than TRUST_RANK_BONUS_PER_STEP to produce net-negative scores
#: for significantly over-qualified channels.
OVERQUALIFICATION_PENALTY_PER_STEP: float = 0.08

#: Cap on the total over-qualification penalty so a very-over-qualified channel
#: still receives a non-zero score and remains a viable last-resort.
MAX_OVERQUALIFICATION_PENALTY: float = 0.40

#: Penalty coefficient applied to the channel score when the task complexity
#: (normalised to [0, 1]) exceeds the channel's advertised max-complexity.
COMPLEXITY_PENALTY_COEFF: float = 0.40

#: Proportion of total decisions that may be violations before the compliance
#: report is marked "degraded".
COMPLIANCE_DEGRADED_THRESHOLD: float = 0.05

#: Proportion of total decisions that may be violations before the compliance
#: report is marked "critical".
COMPLIANCE_CRITICAL_THRESHOLD: float = 0.20


# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------


class TrustTier(str, enum.Enum):
    """Ordered enumeration of trust tiers for evidence routing (§45.4.2).

    Tiers are ordered from *most trusted* (``MECHANICALLY_VERIFIED``) to
    *least trusted* (``UNVERIFIED``).  The ordering is made explicit via the
    :meth:`rank` class method so that comparisons do not depend on enum ordinal
    position, which is fragile.

    The mapping from tier name to integer rank is:

    +--------------------------+------+----------------------------------------------+
    | Tier                     | Rank | Notes                                        |
    +==========================+======+==============================================+
    | MECHANICALLY_VERIFIED    |  6   | Interactive theorem prover / Coq / HOL4      |
    +--------------------------+------+----------------------------------------------+
    | HUMAN_ATTESTED           |  5   | Expert review; highest actionable authority  |
    +--------------------------+------+----------------------------------------------+
    | SOLVER_DISCHARGED        |  4   | SMT/SAT solver (Z3, CVC5, …)                 |
    +--------------------------+------+----------------------------------------------+
    | RUNTIME_WITNESSED        |  3   | Empirical execution / property-based test     |
    +--------------------------+------+----------------------------------------------+
    | ORACLE_PROPOSED          |  2   | Trusted oracle suggestion, unverified         |
    +--------------------------+------+----------------------------------------------+
    | COPILOT_SUGGESTED        |  1   | LLM / Copilot heuristic                      |
    +--------------------------+------+----------------------------------------------+
    | UNVERIFIED               |  0   | No evidence at all                           |
    +--------------------------+------+----------------------------------------------+

    Note: ``HUMAN_ATTESTED`` (rank 5) deliberately outranks ``SOLVER_DISCHARGED``
    (rank 4) because human-expert attestation carries regulatory and ethical
    authority that no automated solver can replicate (§45.4.2).  This means a
    channel whose ceiling is ``SOLVER_DISCHARGED`` cannot satisfy a requirement
    for ``HUMAN_ATTESTED`` evidence, which is the intended routing behaviour for
    ethical-judgment and policy-decision tasks.

    The trust algebra invariant (§45.4.1) states that a channel may only
    produce evidence *at or below* its registered ceiling tier.
    """

    MECHANICALLY_VERIFIED = "mechanically_verified"
    SOLVER_DISCHARGED = "solver_discharged"
    RUNTIME_WITNESSED = "runtime_witnessed"
    HUMAN_ATTESTED = "human_attested"
    ORACLE_PROPOSED = "oracle_proposed"
    COPILOT_SUGGESTED = "copilot_suggested"
    UNVERIFIED = "unverified"

    # Tier → integer rank table (not stored as a class variable to avoid
    # interfering with enum metaclass machinery).
    _RANK_TABLE: dict[str, int] = {
        "mechanically_verified": 6,
        "solver_discharged": 5,
        "runtime_witnessed": 4,
        "human_attested": 3,
        "oracle_proposed": 2,
        "copilot_suggested": 1,
        "unverified": 0,
    }

    @classmethod
    def rank(cls, tier: TrustTier) -> int:
        """Return the integer rank of *tier* (higher = more trusted).

        Args:
            tier: A :class:`TrustTier` member to rank.

        Returns:
            Non-negative integer in ``[0, 6]``.  Returns 0 for any
            unrecognised tier value.
        """
        return cls._RANK_TABLE.value.get(tier.value, 0)  # type: ignore[attr-defined]

    @classmethod
    def can_satisfy(cls, required: TrustTier, offered: TrustTier) -> bool:
        """Return True when *offered* meets or exceeds *required*.

        Implements the satisfaction relation of §45.4.2:
        ``offered ⊒ required  ↔  rank(offered) ≥ rank(required)``.

        Args:
            required: The minimum tier demanded by the task.
            offered: The tier the channel is capable of delivering.

        Returns:
            True if the offered tier satisfies the requirement.
        """
        return cls.rank(offered) >= cls.rank(required)


# Patch: enum._RANK_TABLE is stored as an enum member; access via value dict.
# We use a module-level dict instead to avoid enum member contamination.
_TRUST_TIER_RANKS: dict[str, int] = {
    TrustTier.MECHANICALLY_VERIFIED.value: 6,
    TrustTier.HUMAN_ATTESTED.value: 5,
    TrustTier.SOLVER_DISCHARGED.value: 4,
    TrustTier.RUNTIME_WITNESSED.value: 3,
    TrustTier.ORACLE_PROPOSED.value: 2,
    TrustTier.COPILOT_SUGGESTED.value: 1,
    TrustTier.UNVERIFIED.value: 0,
}


def _tier_rank(tier: TrustTier) -> int:
    """Return the integer rank of *tier* using the module-level rank table.

    This free function is used internally instead of ``TrustTier.rank()``
    because the latter would require the ``_RANK_TABLE`` trick; this keeps
    the logic simple and testable in isolation.

    Args:
        tier: A :class:`TrustTier` member.

    Returns:
        Integer rank in ``[0, 6]``.
    """
    return _TRUST_TIER_RANKS.get(tier.value, 0)


def _tier_can_satisfy(required: TrustTier, offered: TrustTier) -> bool:
    """Return True when *offered* satisfies *required* (§45.4.2).

    Args:
        required: Minimum tier required.
        offered: Tier offered by the channel.

    Returns:
        True if ``rank(offered) >= rank(required)``.
    """
    return _tier_rank(offered) >= _tier_rank(required)


# ---------------------------------------------------------------------------
# TrustRequirement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustRequirement:
    """Immutable description of the minimum trust needed for a routing request.

    A ``TrustRequirement`` is attached to every routing task to declare the
    weakest evidence tier the task consumer is willing to accept.  The router
    uses it to filter the candidate channel set before scoring.

    When ``allow_downgrade`` is True the router may fall back to a channel
    whose ceiling is *below* ``minimum_tier`` if no eligible channel is
    available.  The ``justification`` field must explain why downgrade is
    permitted.

    Attributes:
        requirement_id: Stable unique identifier for this requirement instance.
        minimum_tier: The lowest :class:`TrustTier` the consumer will accept.
        allow_downgrade: If True, the router may use a weaker channel when the
            ideal channel is unavailable, recording the downgrade in the
            decision rationale.
        justification: Human-readable string explaining why this trust level is
            required (or why downgrade is permitted).
        metadata: Arbitrary key-value annotations (e.g., regulatory references,
            policy IDs).
    """

    requirement_id: str
    minimum_tier: TrustTier
    allow_downgrade: bool
    justification: str
    metadata: dict

    def is_satisfied_by(self, tier: TrustTier) -> bool:
        """Return True when *tier* meets or exceeds :attr:`minimum_tier`.

        Uses the trust-algebra satisfaction relation (§45.4.2).

        Args:
            tier: The :class:`TrustTier` offered by a candidate channel.

        Returns:
            True if the offered tier is sufficient.
        """
        return _tier_can_satisfy(self.minimum_tier, tier)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this requirement to a plain dictionary.

        Returns:
            Dict with string-valued fields suitable for JSON serialisation.
        """
        return {
            "requirement_id": self.requirement_id,
            "minimum_tier": self.minimum_tier.value,
            "allow_downgrade": self.allow_downgrade,
            "justification": self.justification,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrustRequirement:
        """Deserialise a ``TrustRequirement`` from a plain dictionary.

        Args:
            d: Dict produced by :meth:`to_dict` or a compatible serialisation.

        Returns:
            A new :class:`TrustRequirement` instance.

        Raises:
            KeyError: If a required key is missing from *d*.
            ValueError: If the ``minimum_tier`` value is not a valid
                :class:`TrustTier` member.
        """
        return cls(
            requirement_id=d["requirement_id"],
            minimum_tier=TrustTier(d["minimum_tier"]),
            allow_downgrade=bool(d.get("allow_downgrade", False)),
            justification=str(d.get("justification", "")),
            metadata=dict(d.get("metadata", {})),
        )

    @classmethod
    def make(
        cls,
        minimum_tier: TrustTier,
        justification: str = "",
        allow_downgrade: bool = False,
        **metadata: Any,
    ) -> TrustRequirement:
        """Convenience factory that auto-generates a ``requirement_id``.

        Args:
            minimum_tier: The required :class:`TrustTier`.
            justification: Explanation text.
            allow_downgrade: Whether the router may use a weaker channel.
            **metadata: Forwarded into the ``metadata`` dict.

        Returns:
            A new :class:`TrustRequirement` instance with a UUID requirement_id.
        """
        return cls(
            requirement_id=str(uuid.uuid4()),
            minimum_tier=minimum_tier,
            allow_downgrade=allow_downgrade,
            justification=justification,
            metadata=dict(metadata),
        )


# ---------------------------------------------------------------------------
# TrustCeilingMap
# ---------------------------------------------------------------------------

#: Default channel → ceiling tier assignments per §45.4.3.
_DEFAULT_CEILING_ASSIGNMENTS: dict[str, str] = {
    EvidenceChannel.Z3.value: TrustTier.SOLVER_DISCHARGED.value,
    EvidenceChannel.RUNTIME_WITNESS.value: TrustTier.RUNTIME_WITNESSED.value,
    EvidenceChannel.HUMAN.value: TrustTier.HUMAN_ATTESTED.value,
    EvidenceChannel.COPILOT_LLM.value: TrustTier.COPILOT_SUGGESTED.value,
}


@dataclass(slots=True)
class TrustCeilingMap:
    """Maps each evidence channel to its maximum achievable trust tier.

    The ceiling map encodes the theory2 trust algebra invariant (§45.4.1):
    a channel's trust ceiling is an *upper bound* on the tier it can ever
    legitimately claim.  Routing decisions that would produce evidence above
    the ceiling are rejected.

    The map is keyed by ``EvidenceChannel.value`` strings to remain serialisable
    without requiring the enum to be importable everywhere.

    Attributes:
        channel_ceilings: Dict mapping channel value strings to tier value
            strings.  For example ``{"z3": "solver_discharged", ...}``.
    """

    channel_ceilings: dict[str, str] = field(default_factory=dict)

    def ceiling_for(self, channel: EvidenceChannel) -> TrustTier:
        """Return the trust ceiling for *channel*.

        Falls back to :attr:`TrustTier.UNVERIFIED` when the channel has no
        registered ceiling, which is the conservative safe default.

        Args:
            channel: The :class:`EvidenceChannel` to look up.

        Returns:
            The registered :class:`TrustTier` ceiling, or
            ``TrustTier.UNVERIFIED`` if not set.
        """
        tier_val = self.channel_ceilings.get(channel.value)
        if tier_val is None:
            _log.debug(
                "No ceiling registered for channel %r; defaulting to UNVERIFIED",
                channel.value,
            )
            return TrustTier.UNVERIFIED
        try:
            return TrustTier(tier_val)
        except ValueError:
            _log.warning(
                "Unknown tier value %r for channel %r; defaulting to UNVERIFIED",
                tier_val,
                channel.value,
            )
            return TrustTier.UNVERIFIED

    def set_ceiling(self, channel: EvidenceChannel, tier: TrustTier) -> None:
        """Register or update the ceiling for *channel*.

        Explicit calls to this method are the *only* sanctioned way to update
        a ceiling; direct mutation of :attr:`channel_ceilings` bypasses
        logging and should be avoided.

        Args:
            channel: The :class:`EvidenceChannel` whose ceiling to set.
            tier: The new :class:`TrustTier` ceiling.
        """
        old = self.channel_ceilings.get(channel.value, "<unset>")
        self.channel_ceilings[channel.value] = tier.value
        _log.info(
            "Ceiling for channel %r updated: %s → %s",
            channel.value,
            old,
            tier.value,
        )

    def can_channel_satisfy(
        self, channel: EvidenceChannel, requirement: TrustRequirement
    ) -> bool:
        """Return True when *channel*'s ceiling meets *requirement*.

        Applies the trust-algebra satisfaction relation: the channel can
        participate in routing if and only if its ceiling tier satisfies the
        minimum tier of the requirement.

        Args:
            channel: Candidate :class:`EvidenceChannel`.
            requirement: The :class:`TrustRequirement` to satisfy.

        Returns:
            True if the channel's ceiling is sufficient for the requirement.
        """
        ceiling = self.ceiling_for(channel)
        return requirement.is_satisfied_by(ceiling)

    def eligible_channels(
        self, requirement: TrustRequirement
    ) -> list[EvidenceChannel]:
        """Return all channels whose ceilings satisfy *requirement*.

        Iterates over all registered channels in :attr:`channel_ceilings` and
        returns those that pass :meth:`can_channel_satisfy`.

        Args:
            requirement: The :class:`TrustRequirement` to match against.

        Returns:
            List of eligible :class:`EvidenceChannel` instances, ordered by
            descending trust rank (highest ceiling first).
        """
        eligible: list[EvidenceChannel] = []
        for ch_val in self.channel_ceilings:
            try:
                channel = EvidenceChannel(ch_val)
            except ValueError:
                continue
            if self.can_channel_satisfy(channel, requirement):
                eligible.append(channel)

        # Sort by ceiling rank descending so the most-trusted channel comes first
        eligible.sort(key=lambda c: _tier_rank(self.ceiling_for(c)), reverse=True)
        return eligible

    def to_dict(self) -> dict[str, str]:
        """Serialise the ceiling map to a plain dict.

        Returns:
            A shallow copy of :attr:`channel_ceilings`.
        """
        return dict(self.channel_ceilings)

    @classmethod
    def default_map(cls) -> TrustCeilingMap:
        """Create a :class:`TrustCeilingMap` with the theory2 default ceilings.

        Default assignments (§45.4.3):
        - Z3 → ``SOLVER_DISCHARGED``
        - RUNTIME_WITNESS → ``RUNTIME_WITNESSED``
        - HUMAN → ``HUMAN_ATTESTED``
        - COPILOT_LLM → ``COPILOT_SUGGESTED``

        Returns:
            A fully populated :class:`TrustCeilingMap`.
        """
        return cls(channel_ceilings=dict(_DEFAULT_CEILING_ASSIGNMENTS))


# ---------------------------------------------------------------------------
# TrustAwareRoutingDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustAwareRoutingDecision:
    """A routing decision augmented with trust-tier information.

    Every field is immutable (``frozen=True``) to guarantee that audit trails
    cannot be altered after the fact.  The :meth:`trust_preserved` predicate
    verifies the theory2 invariant for this specific decision: the claimed
    ``trust_tier`` must not exceed the channel's registered ceiling.

    Attributes:
        decision_id: Unique identifier for this decision.
        task_id: Identifier of the task being routed.
        channel: String value of the :class:`EvidenceChannel` chosen.
        trust_tier: The :class:`TrustTier` this decision claims to deliver.
        requirement: The :class:`TrustRequirement` the decision is satisfying,
            or None if routing was performed without an explicit requirement.
        ceiling_map_snapshot: A snapshot of :attr:`TrustCeilingMap.channel_ceilings`
            at decision time.  Used by :meth:`trust_preserved` to verify the
            invariant without requiring the live ceiling map.
        rationale: Human-readable explanation of why this channel was chosen.
        confidence: Routing confidence score in ``[0, 1]``.
        timestamp: Unix timestamp (float seconds) when the decision was made.
        metadata: Arbitrary key-value annotations.
    """

    decision_id: str
    task_id: str
    channel: str
    trust_tier: TrustTier
    requirement: TrustRequirement | None
    ceiling_map_snapshot: dict
    rationale: str
    confidence: float
    timestamp: float
    metadata: dict

    def trust_preserved(self) -> bool:
        """Return True when the claimed trust tier does not exceed the ceiling.

        Checks the theory2 invariant (§45.4.1):
        ``rank(trust_tier) ≤ rank(ceiling_for(channel))``.

        Uses :attr:`ceiling_map_snapshot` rather than the live map so the
        check remains valid even after the map has been updated.

        Returns:
            True if the trust tier is at or below the channel's ceiling.
        """
        ceiling_val = self.ceiling_map_snapshot.get(self.channel)
        if ceiling_val is None:
            # No ceiling recorded — treat as UNVERIFIED (rank 0); any tier
            # above UNVERIFIED would be a violation.
            ceiling_rank = _tier_rank(TrustTier.UNVERIFIED)
        else:
            try:
                ceiling_tier = TrustTier(ceiling_val)
                ceiling_rank = _tier_rank(ceiling_tier)
            except ValueError:
                ceiling_rank = _tier_rank(TrustTier.UNVERIFIED)

        return _tier_rank(self.trust_tier) <= ceiling_rank

    def to_dict(self) -> dict[str, Any]:
        """Serialise this decision to a plain dictionary.

        Returns:
            Dict suitable for JSON serialisation or log storage.
        """
        return {
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "channel": self.channel,
            "trust_tier": self.trust_tier.value,
            "requirement": self.requirement.to_dict() if self.requirement else None,
            "ceiling_map_snapshot": dict(self.ceiling_map_snapshot),
            "rationale": self.rationale,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
            "trust_preserved": self.trust_preserved(),
        }


# ---------------------------------------------------------------------------
# TrustRoutingAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustRoutingAnalyzer:
    """Audits routing decisions for compliance with the trust-algebra invariant.

    The analyzer maintains a running list of violation records and a count of
    decisions audited.  It is designed to be called after every
    :class:`TrustAwareRoutingDecision` is produced so that compliance
    statistics are always up-to-date.

    Attributes:
        analyzer_id: Unique identifier for this analyzer instance.
        violations: List of violation records, each a dict with at least
            ``"decision_id"`` and ``"messages"`` keys.
        audited_count: Total number of decisions audited (including clean ones).
    """

    analyzer_id: str
    violations: list[dict] = field(default_factory=list)
    audited_count: int = 0

    def audit_decision(
        self, decision: TrustAwareRoutingDecision
    ) -> list[str]:
        """Audit *decision* and return a list of violation messages.

        Checks the following invariants:
        1. ``trust_preserved()`` — the claimed tier does not exceed the ceiling.
        2. Confidence is above the minimum threshold.
        3. If a requirement was provided, the delivered tier satisfies it (or
           downgrade is explicitly allowed).

        Increments :attr:`audited_count` unconditionally.  Appends a violation
        record to :attr:`violations` when any check fails.

        Args:
            decision: The :class:`TrustAwareRoutingDecision` to audit.

        Returns:
            List of violation message strings (empty list if compliant).
        """
        self.audited_count += 1
        messages: list[str] = []

        # Check 1: Trust-algebra invariant (never upgrade beyond ceiling)
        if not decision.trust_preserved():
            ceiling_val = decision.ceiling_map_snapshot.get(decision.channel, "?")
            messages.append(
                f"TRUST_UPGRADE_VIOLATION: channel={decision.channel!r} "
                f"claims tier={decision.trust_tier.value!r} but ceiling is "
                f"{ceiling_val!r}"
            )

        # Check 2: Confidence threshold
        if decision.confidence < MIN_CONFIDENCE_THRESHOLD:
            messages.append(
                f"LOW_CONFIDENCE: confidence={decision.confidence:.3f} is below "
                f"threshold={MIN_CONFIDENCE_THRESHOLD}"
            )

        # Check 3: Requirement satisfaction (if requirement is present)
        if decision.requirement is not None:
            req = decision.requirement
            if not req.is_satisfied_by(decision.trust_tier):
                if not req.allow_downgrade:
                    messages.append(
                        f"REQUIREMENT_NOT_MET: requirement={req.minimum_tier.value!r} "
                        f"was not satisfied by tier={decision.trust_tier.value!r} "
                        f"and allow_downgrade=False"
                    )
                else:
                    # Downgrade allowed — record as a warning, not a hard violation
                    _log.warning(
                        "Downgrade occurred for decision %s: required %s, got %s",
                        decision.decision_id,
                        req.minimum_tier.value,
                        decision.trust_tier.value,
                    )

        if messages:
            self.violations.append(
                {
                    "decision_id": decision.decision_id,
                    "task_id": decision.task_id,
                    "channel": decision.channel,
                    "tier": decision.trust_tier.value,
                    "messages": messages,
                    "timestamp": decision.timestamp,
                }
            )

        return messages

    def violation_rate(self) -> float:
        """Return the fraction of audited decisions that had violations.

        Returns:
            Float in ``[0, 1]``; 0.0 when no decisions have been audited.
        """
        if self.audited_count == 0:
            return 0.0
        return len(self.violations) / self.audited_count

    def summary(self) -> dict[str, Any]:
        """Return a compliance summary dictionary.

        Classifies compliance health as ``"ok"``, ``"degraded"``, or
        ``"critical"`` based on the violation rate thresholds defined by
        :attr:`COMPLIANCE_DEGRADED_THRESHOLD` and
        :attr:`COMPLIANCE_CRITICAL_THRESHOLD`.

        Returns:
            Dict with keys ``"analyzer_id"``, ``"audited_count"``,
            ``"violation_count"``, ``"violation_rate"``, ``"health"``,
            ``"violations"``.
        """
        rate = self.violation_rate()
        if rate >= COMPLIANCE_CRITICAL_THRESHOLD:
            health = "critical"
        elif rate >= COMPLIANCE_DEGRADED_THRESHOLD:
            health = "degraded"
        else:
            health = "ok"

        return {
            "analyzer_id": self.analyzer_id,
            "audited_count": self.audited_count,
            "violation_count": len(self.violations),
            "violation_rate": round(rate, 4),
            "health": health,
            "violations": list(self.violations),
        }

    def reset(self) -> None:
        """Clear all recorded violations and reset the audited count.

        Useful between test runs or after a compliance epoch boundary.
        """
        self.violations.clear()
        self.audited_count = 0
        _log.debug("TrustRoutingAnalyzer %s reset.", self.analyzer_id)


# ---------------------------------------------------------------------------
# TrustAwareRouter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustAwareRouter:
    """Core trust-aware router that enforces the theory2 ceiling invariant.

    The router integrates:
    - :class:`TrustCeilingMap` for determining eligible channels.
    - :class:`TrustRoutingAnalyzer` for post-decision audit.
    - An ordered decision history for per-task retrieval and export.

    Routing algorithm (§45.4 adaptation of §45.3 basic selection):
    1. Consult the ceiling map to obtain eligible channels for the requirement.
    2. Score each eligible channel via :meth:`_score_channel`.
    3. Select the channel with the highest score.
    4. If no channel is eligible and the requirement allows downgrade, fall back
       to :attr:`fallback_channel`; otherwise raise.
    5. Build a :class:`TrustAwareRoutingDecision` and submit it to the analyzer.
    6. Append to :attr:`history`.

    Attributes:
        router_id: Unique identifier for this router instance.
        ceiling_map: :class:`TrustCeilingMap` declaring per-channel ceilings.
        analyzer: :class:`TrustRoutingAnalyzer` for compliance checking.
        history: Ordered list of :class:`TrustAwareRoutingDecision` objects.
        fallback_channel: Channel value string used when no eligible channel
            exists and the requirement allows downgrade.  Defaults to
            ``"human"``.
    """

    router_id: str
    ceiling_map: TrustCeilingMap
    analyzer: TrustRoutingAnalyzer
    history: list[TrustAwareRoutingDecision] = field(default_factory=list)
    fallback_channel: str = DEFAULT_FALLBACK_CHANNEL

    def route(
        self, task: dict, requirement: TrustRequirement
    ) -> TrustAwareRoutingDecision:
        """Route *task* according to *requirement* and return a decision.

        Selects the highest-scoring eligible channel, builds a
        :class:`TrustAwareRoutingDecision`, audits it, appends it to
        :attr:`history`, and returns it.  If no channel satisfies the
        requirement and ``allow_downgrade`` is True, the fallback channel is
        used; otherwise a ``RuntimeError`` is raised.

        Args:
            task: Task dictionary.  Recognised keys include ``"task_id"``,
                  ``"claim_kind"``, ``"complexity"`` (float in [0, 1]).
            requirement: The :class:`TrustRequirement` the task must satisfy.

        Returns:
            A :class:`TrustAwareRoutingDecision` for the selected channel.

        Raises:
            RuntimeError: If no eligible channel exists and
                ``requirement.allow_downgrade`` is False.
        """
        task_id: str = task.get("task_id", str(uuid.uuid4()))
        eligible = self.ceiling_map.eligible_channels(requirement)

        chosen_channel_val: str
        chosen_tier: TrustTier
        score: float

        if eligible:
            # Score all eligible channels and pick the best
            scored: list[tuple[EvidenceChannel, float]] = []
            for channel in eligible:
                s = self._score_channel(channel, task, requirement)
                scored.append((channel, s))
            scored.sort(key=lambda t: t[1], reverse=True)
            best_channel, score = scored[0]
            chosen_channel_val = best_channel.value
            chosen_tier = self.ceiling_map.ceiling_for(best_channel)
        elif requirement.allow_downgrade:
            _log.warning(
                "No channel satisfies requirement %r for task %s; "
                "downgrading to fallback channel %r",
                requirement.minimum_tier.value,
                task_id,
                self.fallback_channel,
            )
            chosen_channel_val = self.fallback_channel
            # Determine tier for fallback channel
            try:
                fallback_enum = EvidenceChannel(self.fallback_channel)
                chosen_tier = self.ceiling_map.ceiling_for(fallback_enum)
            except ValueError:
                chosen_tier = TrustTier.UNVERIFIED
            score = MIN_CONFIDENCE_THRESHOLD
        else:
            raise RuntimeError(
                f"No channel satisfies TrustRequirement(minimum_tier="
                f"{requirement.minimum_tier.value!r}) and allow_downgrade=False. "
                f"Task ID: {task_id}"
            )

        rationale = self._build_rationale(chosen_channel_val, requirement, score)
        snapshot = self.ceiling_map.to_dict()

        decision = TrustAwareRoutingDecision(
            decision_id=str(uuid.uuid4()),
            task_id=task_id,
            channel=chosen_channel_val,
            trust_tier=chosen_tier,
            requirement=requirement,
            ceiling_map_snapshot=snapshot,
            rationale=rationale,
            confidence=min(1.0, max(0.0, score)),
            timestamp=time.time(),
            metadata={"claim_kind": task.get("claim_kind", "unknown")},
        )

        violations = self.analyzer.audit_decision(decision)
        if violations:
            _log.warning(
                "Decision %s has %d violation(s): %s",
                decision.decision_id,
                len(violations),
                "; ".join(violations),
            )

        self.history.append(decision)
        return decision

    def _score_channel(
        self,
        channel: EvidenceChannel,
        task: dict,
        requirement: TrustRequirement,
    ) -> float:
        """Compute a [0, 1] routing score for *channel* given *task* and *requirement*.

        Score components:
        1. **Base score** — 0.5 (constant; channel has already passed the
           eligibility filter).
        2. **Exact-match bonus** — a small bonus when the channel's ceiling rank
           is exactly the required rank (zero excess), meaning the channel is
           perfectly calibrated for this task tier.
        3. **Over-qualification penalty** — ``OVERQUALIFICATION_PENALTY_PER_STEP``
           per rank step the channel's ceiling exceeds the required minimum.
           This steers routing toward channels that are calibrated for the task
           rather than wasting high-trust capacity (e.g., routing natural-
           language tasks away from the human channel even though human is
           technically eligible).  The penalty dominates the trust bonus after
           the first step; net trust effect is negative for rank excess ≥ 2.
        4. **Complexity penalty** — ``COMPLEXITY_PENALTY_COEFF`` times the
           normalised excess complexity when the task's complexity exceeds a
           channel-specific threshold.

        The final score is clamped to ``[0, 1]``.

        Args:
            channel: The :class:`EvidenceChannel` to score.
            task: Task dictionary.  ``"complexity"`` key is a float in [0, 1].
            requirement: The :class:`TrustRequirement` being satisfied.

        Returns:
            Score float in ``[0, 1]``.
        """
        ceiling = self.ceiling_map.ceiling_for(channel)
        ceiling_rank = _tier_rank(ceiling)
        required_rank = _tier_rank(requirement.minimum_tier)

        # Number of ranks by which the channel's ceiling exceeds the minimum
        rank_excess = max(0, ceiling_rank - required_rank)

        # Small trust bonus for being slightly above minimum (capped at 2 steps)
        trust_bonus = min(MAX_TRUST_RANK_BONUS, rank_excess * TRUST_RANK_BONUS_PER_STEP)

        # Over-qualification penalty: discourages sending tasks to channels that
        # are much more capable than required (saves them for harder tasks)
        overqualification_penalty = min(
            MAX_OVERQUALIFICATION_PENALTY,
            rank_excess * OVERQUALIFICATION_PENALTY_PER_STEP,
        )

        # Net trust effect: positive for exact match or 1-step above, negative
        # for larger excess (overqualification dominates)
        net_trust = trust_bonus - overqualification_penalty

        # Complexity penalty: channels with higher ceilings tolerate more
        # complexity; use ceiling_rank as proxy for max tolerated complexity
        task_complexity: float = float(task.get("complexity", 0.5))
        max_complexity_for_channel = min(1.0, 0.4 + ceiling_rank * 0.1)
        if task_complexity > max_complexity_for_channel:
            excess = task_complexity - max_complexity_for_channel
            complexity_penalty = COMPLEXITY_PENALTY_COEFF * excess
        else:
            complexity_penalty = 0.0

        raw_score = 0.5 + net_trust - complexity_penalty
        return max(0.0, min(1.0, raw_score))

    def _build_rationale(
        self,
        channel_val: str,
        requirement: TrustRequirement,
        score: float,
    ) -> str:
        """Construct a human-readable rationale string for a routing decision.

        Args:
            channel_val: The :attr:`EvidenceChannel.value` of the chosen channel.
            requirement: The :class:`TrustRequirement` being satisfied.
            score: The computed routing score for the channel.

        Returns:
            A concise multi-clause rationale string.
        """
        ceiling_val = self.ceiling_map.channel_ceilings.get(channel_val, "unknown")
        parts = [
            f"Channel {channel_val!r} selected with score {score:.3f}.",
            f"Required minimum trust tier: {requirement.minimum_tier.value!r}.",
            f"Channel ceiling: {ceiling_val!r}.",
            f"Justification: {requirement.justification or '(none)'}.",
        ]
        if requirement.allow_downgrade:
            parts.append("Downgrade to weaker channel is permitted for this task.")
        return " ".join(parts)

    def history_for_task(self, task_id: str) -> list[TrustAwareRoutingDecision]:
        """Return all decisions for *task_id* in chronological order.

        Args:
            task_id: The task identifier to filter by.

        Returns:
            List of :class:`TrustAwareRoutingDecision` instances, oldest first.
        """
        return [d for d in self.history if d.task_id == task_id]

    def compliance_report(self) -> dict[str, Any]:
        """Return a compliance report combining router metadata and analyzer summary.

        Returns:
            Dict with keys ``"router_id"``, ``"total_decisions"``,
            ``"analyzer"``, ``"ceiling_map"``.
        """
        return {
            "router_id": self.router_id,
            "total_decisions": len(self.history),
            "analyzer": self.analyzer.summary(),
            "ceiling_map": self.ceiling_map.to_dict(),
        }

    def export(self) -> dict[str, Any]:
        """Serialise the full router state for persistence or debugging.

        Returns:
            Dict with all router fields serialised to plain Python types.
        """
        return {
            "router_id": self.router_id,
            "fallback_channel": self.fallback_channel,
            "ceiling_map": self.ceiling_map.to_dict(),
            "analyzer": self.analyzer.summary(),
            "history": [d.to_dict() for d in self.history],
        }


# ---------------------------------------------------------------------------
# TrustRoutingCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustRoutingCoordinator:
    """Orchestrates multiple :class:`TrustAwareRouter` instances by domain.

    Different trust *domains* (e.g., "formal", "empirical", "heuristic") may
    have different ceiling maps and fallback policies.  The coordinator routes
    each task to the appropriate domain's router and aggregates compliance
    reporting across all domains.

    Attributes:
        coordinator_id: Unique identifier for this coordinator.
        routers: Dict mapping domain name strings to :class:`TrustAwareRouter`
            instances.
        default_domain: The domain used when no explicit domain is specified
            in a ``route()`` call.
    """

    coordinator_id: str
    routers: dict[str, TrustAwareRouter] = field(default_factory=dict)
    default_domain: str = "default"

    def register_router(self, domain: str, router: TrustAwareRouter) -> None:
        """Register *router* under *domain*.

        If a router is already registered for *domain*, it is replaced.

        Args:
            domain: Domain name string (e.g., ``"formal"``, ``"empirical"``).
            router: The :class:`TrustAwareRouter` to register.
        """
        if domain in self.routers:
            _log.info(
                "Replacing existing router for domain %r in coordinator %s",
                domain,
                self.coordinator_id,
            )
        self.routers[domain] = router
        _log.debug(
            "Router %s registered for domain %r in coordinator %s",
            router.router_id,
            domain,
            self.coordinator_id,
        )

    def route(
        self,
        task: dict,
        requirement: TrustRequirement,
        domain: str | None = None,
    ) -> TrustAwareRoutingDecision:
        """Route *task* with *requirement* in the specified *domain*.

        If *domain* is None, :attr:`default_domain` is used.  If the chosen
        domain has no registered router, falls back to the first available
        router.

        Args:
            task: Task dictionary.
            requirement: The :class:`TrustRequirement` for the routing request.
            domain: Optional domain name.  Defaults to :attr:`default_domain`.

        Returns:
            A :class:`TrustAwareRoutingDecision` from the domain's router.

        Raises:
            RuntimeError: If no routers are registered at all.
        """
        if not self.routers:
            raise RuntimeError(
                f"TrustRoutingCoordinator {self.coordinator_id} has no "
                "registered routers."
            )

        target_domain = domain or self.default_domain
        router = self.routers.get(target_domain)

        if router is None:
            # Fall back to the first available router and log a warning
            fallback_domain, router = next(iter(self.routers.items()))
            _log.warning(
                "Domain %r not registered; falling back to %r in coordinator %s",
                target_domain,
                fallback_domain,
                self.coordinator_id,
            )

        return router.route(task, requirement)

    def compliance_summary(self) -> dict[str, Any]:
        """Return compliance reports for all registered domains.

        Returns:
            Dict mapping domain name → compliance report dict.
        """
        return {
            domain: router.compliance_report()
            for domain, router in self.routers.items()
        }

    def health(self) -> dict[str, Any]:
        """Return a high-level health snapshot of the coordinator.

        Aggregates total decisions, violation counts, and health status across
        all domains.

        Returns:
            Dict with ``"coordinator_id"``, ``"domain_count"``,
            ``"total_decisions"``, ``"total_violations"``, ``"health"`` keys.
        """
        total_decisions = 0
        total_violations = 0

        for router in self.routers.values():
            summary = router.analyzer.summary()
            total_decisions += summary["audited_count"]
            total_violations += summary["violation_count"]

        if total_decisions == 0:
            rate = 0.0
        else:
            rate = total_violations / total_decisions

        if rate >= COMPLIANCE_CRITICAL_THRESHOLD:
            health_status = "critical"
        elif rate >= COMPLIANCE_DEGRADED_THRESHOLD:
            health_status = "degraded"
        else:
            health_status = "ok"

        return {
            "coordinator_id": self.coordinator_id,
            "domain_count": len(self.routers),
            "total_decisions": total_decisions,
            "total_violations": total_violations,
            "violation_rate": round(rate, 4),
            "health": health_status,
        }


# ---------------------------------------------------------------------------
# TrustRoutingWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustRoutingWitness:
    """Append-only audit log for trust-aware routing decisions.

    The witness log is the authoritative record of all routing decisions and
    all violations detected.  It is designed to be *append-only*: new entries
    are added via :meth:`record` and :meth:`record_violation`, but no entry
    is ever mutated or removed.

    The :meth:`verify_no_upgrades` method provides a full-log scan that checks
    whether any recorded decision claimed a trust tier above its channel's
    registered ceiling — the theory2 invariant (§45.4.1) restated as a
    post-hoc audit.

    Attributes:
        witness_id: Unique identifier for this witness log instance.
        log: Ordered list of log entry dicts.
        created_at: Unix timestamp when this witness was created.
    """

    witness_id: str
    log: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def record(self, decision: TrustAwareRoutingDecision) -> None:
        """Append a routing decision to the witness log.

        Records the full serialised decision together with a ``"kind"`` tag
        of ``"decision"`` for easy filtering.

        Args:
            decision: The :class:`TrustAwareRoutingDecision` to record.
        """
        entry = decision.to_dict()
        entry["kind"] = "decision"
        entry["witness_id"] = self.witness_id
        self.log.append(entry)
        _log.debug(
            "Witness %s recorded decision %s for channel %r",
            self.witness_id,
            decision.decision_id,
            decision.channel,
        )

    def record_violation(
        self, decision_id: str, violations: list[str]
    ) -> None:
        """Append a violation record to the witness log.

        Violation records are distinct from decision records so that audit
        tools can scan only violations without parsing decision payloads.

        Args:
            decision_id: ID of the decision that generated the violations.
            violations: List of violation message strings.
        """
        entry: dict[str, Any] = {
            "kind": "violation",
            "witness_id": self.witness_id,
            "decision_id": decision_id,
            "violations": list(violations),
            "timestamp": time.time(),
        }
        self.log.append(entry)
        _log.warning(
            "Witness %s recorded %d violation(s) for decision %s",
            self.witness_id,
            len(violations),
            decision_id,
        )

    def verify_no_upgrades(self) -> bool:
        """Scan the full log and return True only when no trust-upgrade violations exist.

        A trust-upgrade violation is any decision-kind log entry for which the
        recorded ``trust_preserved`` field is False, or (if that field is
        absent) for which the tier rank exceeds the ceiling rank derived from
        ``ceiling_map_snapshot``.

        Returns:
            True if the log contains no trust-upgrade violations.
        """
        for entry in self.log:
            if entry.get("kind") != "decision":
                continue

            # Prefer the pre-computed field when present
            trust_preserved = entry.get("trust_preserved")
            if trust_preserved is not None:
                if not trust_preserved:
                    _log.warning(
                        "Witness %s: upgrade violation in decision %s",
                        self.witness_id,
                        entry.get("decision_id", "?"),
                    )
                    return False
                continue

            # Fallback: recompute from snapshot fields
            channel_val = entry.get("channel", "")
            tier_val = entry.get("trust_tier", "unverified")
            snapshot: dict = entry.get("ceiling_map_snapshot", {})
            ceiling_val = snapshot.get(channel_val, TrustTier.UNVERIFIED.value)

            try:
                tier = TrustTier(tier_val)
                ceiling = TrustTier(ceiling_val)
            except ValueError:
                # Unknown tiers — treat as unverified (no upgrade possible)
                continue

            if _tier_rank(tier) > _tier_rank(ceiling):
                _log.warning(
                    "Witness %s: re-computed upgrade violation in decision %s "
                    "(tier=%s > ceiling=%s)",
                    self.witness_id,
                    entry.get("decision_id", "?"),
                    tier_val,
                    ceiling_val,
                )
                return False

        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness log to a plain dictionary.

        Returns:
            Dict with ``"witness_id"``, ``"created_at"``, ``"entry_count"``,
            and ``"log"`` keys.
        """
        return {
            "witness_id": self.witness_id,
            "created_at": self.created_at,
            "entry_count": len(self.log),
            "log": list(self.log),
        }

    def decision_entries(self) -> list[dict]:
        """Return only decision-kind entries from the log.

        Returns:
            List of log entries with ``"kind" == "decision"``.
        """
        return [e for e in self.log if e.get("kind") == "decision"]

    def violation_entries(self) -> list[dict]:
        """Return only violation-kind entries from the log.

        Returns:
            List of log entries with ``"kind" == "violation"``.
        """
        return [e for e in self.log if e.get("kind") == "violation"]


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_default_trust_ceiling_map() -> TrustCeilingMap:
    """Create a :class:`TrustCeilingMap` with the theory2 default ceilings.

    Convenience wrapper around :meth:`TrustCeilingMap.default_map` for use
    in factory pipelines that import individual functions rather than classes.

    Default ceilings (§45.4.3):
    - ``z3`` → ``SOLVER_DISCHARGED``
    - ``runtime_witness`` → ``RUNTIME_WITNESSED``
    - ``human`` → ``HUMAN_ATTESTED``
    - ``copilot_llm`` → ``COPILOT_SUGGESTED``

    Returns:
        A fully populated :class:`TrustCeilingMap`.
    """
    return TrustCeilingMap.default_map()


def make_default_trust_aware_router(
    domain: str = "default",
    fallback_channel: str = DEFAULT_FALLBACK_CHANNEL,
) -> TrustAwareRouter:
    """Create a :class:`TrustAwareRouter` with default ceiling map and analyzer.

    Args:
        domain: Optional label used in the router's ID for traceability.
        fallback_channel: Channel value string to use when no eligible channel
            exists and downgrade is permitted.

    Returns:
        A fully configured :class:`TrustAwareRouter` ready to route tasks.
    """
    router_id = f"trust-router-{domain}-{uuid.uuid4().hex[:8]}"
    ceiling_map = make_default_trust_ceiling_map()
    analyzer = TrustRoutingAnalyzer(analyzer_id=f"analyzer-{uuid.uuid4().hex[:8]}")
    return TrustAwareRouter(
        router_id=router_id,
        ceiling_map=ceiling_map,
        analyzer=analyzer,
        fallback_channel=fallback_channel,
    )


def make_default_trust_routing_coordinator() -> TrustRoutingCoordinator:
    """Create a :class:`TrustRoutingCoordinator` with three pre-configured domains.

    Domains:
    - ``"formal"`` — router optimised for solver-discharged and mechanically
      verified evidence.  Uses the default ceiling map unchanged.
    - ``"empirical"`` — router for runtime-witnessed evidence.  Same ceilings.
    - ``"heuristic"`` — router for copilot-suggested and oracle-proposed
      evidence.  Same ceilings; fallback is still human.

    The coordinator's ``default_domain`` is set to ``"formal"``.

    Returns:
        A fully configured :class:`TrustRoutingCoordinator`.
    """
    coordinator_id = f"coordinator-{uuid.uuid4().hex[:8]}"
    coordinator = TrustRoutingCoordinator(
        coordinator_id=coordinator_id,
        default_domain="formal",
    )

    for domain in ("formal", "empirical", "heuristic"):
        router = make_default_trust_aware_router(domain=domain)
        coordinator.register_router(domain, router)

    return coordinator


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "TrustTier",
    "TrustRequirement",
    "TrustCeilingMap",
    "TrustAwareRoutingDecision",
    "TrustRoutingAnalyzer",
    "TrustAwareRouter",
    "TrustRoutingCoordinator",
    "TrustRoutingWitness",
    "make_default_trust_ceiling_map",
    "make_default_trust_aware_router",
    "make_default_trust_routing_coordinator",
]

# ---------------------------------------------------------------------------
# Module-level smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    print("=== trust_aware_routing.py — smoke test ===\n")

    # Build a coordinator with three trust domains.
    coordinator = make_default_trust_routing_coordinator()
    print(f"Coordinator ID : {coordinator.coordinator_id}")
    print(f"Registered domains: {list(coordinator.routers.keys())}\n")

    # Also build a shared witness log for audit.
    witness = TrustRoutingWitness(witness_id=str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Task 1: Arithmetic claim — Z3 can satisfy SOLVER_DISCHARGED.
    # ------------------------------------------------------------------
    req_solver = TrustRequirement.make(
        minimum_tier=TrustTier.SOLVER_DISCHARGED,
        justification="Arithmetic claims require mechanical solver discharge.",
    )
    task1 = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "arithmetic",
        "formula": "(assert (= (+ x y) 42))",
        "complexity": 0.4,
    }
    decision1 = coordinator.route(task1, req_solver, domain="formal")
    witness.record(decision1)
    print(
        f"Task 1 (arithmetic/solver):  channel={decision1.channel!r:18s} "
        f"tier={decision1.trust_tier.value!r:22s} "
        f"confidence={decision1.confidence:.3f} "
        f"trust_ok={decision1.trust_preserved()}"
    )

    # ------------------------------------------------------------------
    # Task 2: Runtime property test — RUNTIME_WITNESS satisfies
    #         RUNTIME_WITNESSED.
    # ------------------------------------------------------------------
    req_witness = TrustRequirement.make(
        minimum_tier=TrustTier.RUNTIME_WITNESSED,
        justification="Property test requires empirical runtime witness.",
    )
    task2 = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "property_test",
        "code": "assert all(x >= 0 for x in range(100))",
        "test_count": 100,
        "complexity": 0.5,
    }
    decision2 = coordinator.route(task2, req_witness, domain="empirical")
    witness.record(decision2)
    print(
        f"Task 2 (property_test/witness): channel={decision2.channel!r:18s} "
        f"tier={decision2.trust_tier.value!r:22s} "
        f"confidence={decision2.confidence:.3f} "
        f"trust_ok={decision2.trust_preserved()}"
    )

    # ------------------------------------------------------------------
    # Task 3: Natural language explanation — COPILOT satisfies
    #         COPILOT_SUGGESTED.
    # ------------------------------------------------------------------
    req_heuristic = TrustRequirement.make(
        minimum_tier=TrustTier.COPILOT_SUGGESTED,
        justification="Sketch-level explanation; copilot suggestion is sufficient.",
        allow_downgrade=False,
    )
    task3 = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "natural_language",
        "prompt": "Explain why the routing invariant prevents trust upgrades.",
        "complexity": 0.3,
    }
    decision3 = coordinator.route(task3, req_heuristic, domain="heuristic")
    witness.record(decision3)
    print(
        f"Task 3 (natural_lang/copilot): channel={decision3.channel!r:18s} "
        f"tier={decision3.trust_tier.value!r:22s} "
        f"confidence={decision3.confidence:.3f} "
        f"trust_ok={decision3.trust_preserved()}"
    )

    # ------------------------------------------------------------------
    # Task 4: Ethical judgment requiring HUMAN_ATTESTED trust.
    # Z3 (solver_discharged) CANNOT satisfy HUMAN_ATTESTED, so the router
    # must fall back to the human channel.  allow_downgrade=True is set to
    # demonstrate the graceful fallback path rather than a RuntimeError.
    # ------------------------------------------------------------------
    req_human = TrustRequirement.make(
        minimum_tier=TrustTier.HUMAN_ATTESTED,
        justification=(
            "Ethical judgment requires human attestation; "
            "solver-discharged evidence is insufficient."
        ),
        allow_downgrade=True,
    )
    task4 = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "ethical_judgment",
        "description": "Is this automated decision fair under regulation X?",
        "complexity": 0.9,
    }
    # Route in 'formal' domain — Z3 is its primary channel but CANNOT
    # satisfy HUMAN_ATTESTED; fallback to human demonstrates the invariant.
    decision4 = coordinator.route(task4, req_human, domain="formal")
    witness.record(decision4)
    print(
        f"Task 4 (ethical/human fallback): channel={decision4.channel!r:18s} "
        f"tier={decision4.trust_tier.value!r:22s} "
        f"confidence={decision4.confidence:.3f} "
        f"trust_ok={decision4.trust_preserved()}"
    )

    # ------------------------------------------------------------------
    # Compliance report
    # ------------------------------------------------------------------
    print("\n--- Compliance Report ---")
    compliance = coordinator.compliance_summary()
    for domain, report in compliance.items():
        analyzer_data = report["analyzer"]
        print(
            f"  domain={domain!r:12s}  decisions={analyzer_data['audited_count']:3d}  "
            f"violations={analyzer_data['violation_count']:3d}  "
            f"health={analyzer_data['health']!r}"
        )

    # ------------------------------------------------------------------
    # Coordinator health
    # ------------------------------------------------------------------
    health = coordinator.health()
    print(
        f"\nCoordinator health: {health['health']!r}  "
        f"(total_decisions={health['total_decisions']}, "
        f"total_violations={health['total_violations']})"
    )

    # ------------------------------------------------------------------
    # Witness log verification
    # ------------------------------------------------------------------
    no_upgrades = witness.verify_no_upgrades()
    print(f"\nWitness log: {len(witness.log)} entries")
    print(f"verify_no_upgrades() → {no_upgrades}")
    print(
        f"Decision entries: {len(witness.decision_entries())}  "
        f"Violation entries: {len(witness.violation_entries())}"
    )

    # ------------------------------------------------------------------
    # Per-task decision lookup
    # ------------------------------------------------------------------
    print(f"\nDecisions for task4 ({task4['task_id'][:8]}...):")
    formal_router = coordinator.routers["formal"]
    for d in formal_router.history_for_task(task4["task_id"]):
        print(
            f"  decision_id={d.decision_id[:8]}  channel={d.channel!r}  "
            f"tier={d.trust_tier.value!r}"
        )

    print("\n=== smoke test complete ===")
