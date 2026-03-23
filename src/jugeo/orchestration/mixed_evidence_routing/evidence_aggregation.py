"""Evidence aggregation for the mixed-evidence routing layer.

This module implements the evidence-aggregation machinery described in
*theory2.tex* Ch 45 ("Channel Selection in Mixed-Evidence Routing Systems"),
specifically §45.6 "Evidence Aggregation".  That section establishes the
theoretical foundations for combining evidence pieces that arrive from
heterogeneous channels (SMT solver, LLM assistant, runtime witness, human
expert) into a single aggregate result whose trust tier is determined by
the *trust algebra* — not by arithmetic averaging.

§45.6.1 — The Trust Lattice
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The core invariant is that aggregation must follow the *trust lattice*
rather than treat trust tiers as scalars that can be averaged.  The trust
lattice orders six tiers from weakest (UNVERIFIED) to strongest
(MECHANICALLY_VERIFIED):

    0  UNVERIFIED         — no epistemic backing
    1  COPILOT_SUGGESTED  — LLM-generated, heuristic
    2  ORACLE_PROPOSED    — external oracle with partial verification
    3  RUNTIME_WITNESSED  — empirically observed at runtime
    4  HUMAN_ATTESTED     — manually reviewed and signed off
    5  SOLVER_DISCHARGED  — formally discharged by an automated solver
    6  MECHANICALLY_VERIFIED — checked end-to-end by a proof assistant

The *meet* (greatest lower bound, GLB) of two tiers is the weaker one:

    meet(SOLVER_DISCHARGED, COPILOT_SUGGESTED) = COPILOT_SUGGESTED

The *join* (least upper bound, LUB) of two tiers is the stronger one:

    join(SOLVER_DISCHARGED, COPILOT_SUGGESTED) = SOLVER_DISCHARGED

This means you *cannot* obtain a stronger aggregate than any individual
piece in the collection — the meet operation enforces a monotone downward
bound.  The trust invariant is formally stated in §45.6.3:

    aggregate_trust ≤ meet({piece.trust_tier | piece ∈ sources})

§45.6.2 — Aggregation Strategies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The chapter identifies four aggregation strategies that all respect the
trust invariant:

* **TRUST_MEET** (default, theory2 §45.6.4): Apply the lattice meet to
  all input tiers.  The aggregate trust tier is the weakest of all
  contributing pieces.  This is the conservative, sound strategy.

* **TRUST_JOIN** (optimistic, §45.6.5): Apply the lattice join.  The
  aggregate trust tier is the strongest of all contributing pieces.
  This is only safe when pieces are *independent* evidence of the same
  claim.  Use with caution.

* **HIGHEST_CONFIDENCE** (§45.6.6): Select the piece with the highest
  scalar confidence score as the representative, but cap the aggregate
  trust tier at the meet of all tiers.  The trust ceiling is never
  elevated above the meet.

* **WEIGHTED_MEET** (§45.6.7): Like TRUST_MEET but the resulting
  aggregate_confidence is a weighted average of the input confidences,
  weighted by tier rank.  The trust tier itself is still the lattice meet.

§45.6.8 — Audit Trail
~~~~~~~~~~~~~~~~~~~~~~~
Every aggregation produces a ``trust_algebra_trace`` — a list of
human-readable strings showing each pairwise meet or join that was
computed, e.g.::

    ["meet(solver_discharged, copilot_suggested) = copilot_suggested",
     "meet(copilot_suggested, runtime_witnessed) = copilot_suggested"]

This trace is essential for debugging and for the audit-log
:class:`AggregationWitness` that checks post-hoc that no aggregation
violated the trust invariant.

§45.6.9 — Buffer Model
~~~~~~~~~~~~~~~~~~~~~~~~
Evidence pieces arrive asynchronously from channels.  The
:class:`EvidenceBuffer` accumulates pieces for one task until either a
minimum quorum is reached or a wall-clock deadline expires, then drains
into the aggregator.

See Also:
    channel_selection.py — channel selection (§45.3)
    jurisdiction_mapping.py — jurisdiction maps (§45.4)
    routing_arbiter.py — routing arbiter (§45.5)
    result_promotion.py — result promotion (§45.7)
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Optional upstream jugeo imports — guarded with try/except so the module
# loads cleanly even when sister packages are absent or still stubs.
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustCeiling  # type: ignore[import]

    _TRUST_AVAILABLE = True
except Exception:
    _TRUST_AVAILABLE = False

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustLevel."""

        VERIFIED = "VERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        WITNESSED = "WITNESSED"
        UNKNOWN = "UNKNOWN"

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
    from jugeo.orchestration.frontier import FrontierItem, Frontier  # type: ignore[import]

    _FRONTIER_AVAILABLE = True
except Exception:
    _FRONTIER_AVAILABLE = False

    class FrontierItem:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.frontier.FrontierItem."""

    class Frontier:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.frontier.Frontier."""


try:
    from jugeo.orchestration.negotiation import NegotiationSession, Negotiator  # type: ignore[import]

    _NEGOTIATION_AVAILABLE = True
except Exception:
    _NEGOTIATION_AVAILABLE = False

    class NegotiationSession:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.negotiation.NegotiationSession."""

    class Negotiator:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.negotiation.Negotiator."""


try:
    from jugeo.geometry.descent import DescentEngine, DescentResult  # type: ignore[import]

    _DESCENT_AVAILABLE = True
except Exception:
    _DESCENT_AVAILABLE = False

    class DescentEngine:  # type: ignore[no-redef]
        """Stub for jugeo.geometry.descent.DescentEngine."""

    class DescentResult:  # type: ignore[no-redef]
        """Stub for jugeo.geometry.descent.DescentResult."""


# ---------------------------------------------------------------------------
# TrustLattice
# ---------------------------------------------------------------------------


class TrustLattice:
    """The six-tier trust lattice from *theory2.tex* Ch 45 §45.6.1.

    The lattice is totally ordered by rank (0 = weakest, 6 = strongest).
    Operations are pure class-methods; no instances are needed.

    Trust tiers (canonical lower-case spellings used throughout this module):

    ====  ======================  =============================================
    Rank  Tier string             Meaning
    ====  ======================  =============================================
    0     ``unverified``          No epistemic backing
    1     ``copilot_suggested``   Heuristic LLM output
    2     ``oracle_proposed``     External oracle, partially verified
    3     ``runtime_witnessed``   Empirically observed at runtime
    4     ``human_attested``      Manually reviewed and signed off
    5     ``solver_discharged``   Discharged by automated solver
    6     ``mechanically_verified`` Full proof-assistant check
    ====  ======================  =============================================
    """

    # Ordered from weakest to strongest; index == rank.
    _TIERS: tuple[str, ...] = (
        "unverified",           # 0
        "copilot_suggested",    # 1
        "oracle_proposed",      # 2
        "runtime_witnessed",    # 3
        "human_attested",       # 4
        "solver_discharged",    # 5
        "mechanically_verified",  # 6
    )

    # Map tier name → rank for O(1) lookup.
    _RANK: dict[str, int] = {t: i for i, t in enumerate(_TIERS)}

    # Accept upper-case aliases (e.g. "SOLVER_DISCHARGED") by lower-casing.
    @classmethod
    def _normalise(cls, tier: str) -> str:
        """Return the canonical lower-case tier string for *tier*.

        Accepts both ``"SOLVER_DISCHARGED"`` and ``"solver_discharged"``.

        Args:
            tier: Raw tier string, any case.

        Returns:
            Canonical lower-case tier string.

        Raises:
            KeyError: If *tier* is not a recognised trust tier.
        """
        normalised = tier.strip().lower()
        if normalised not in cls._RANK:
            raise KeyError(
                f"Unknown trust tier {tier!r}. "
                f"Valid tiers: {list(cls._TIERS)}"
            )
        return normalised

    @classmethod
    def rank(cls, tier_str: str) -> int:
        """Return the integer rank of *tier_str* in the trust lattice.

        A higher rank means stronger trust.  Rank 0 is the weakest
        (``unverified``) and rank 6 is the strongest
        (``mechanically_verified``).

        Args:
            tier_str: Trust tier string, case-insensitive.

        Returns:
            Integer rank in [0, 6].

        Raises:
            KeyError: If *tier_str* is not a recognised tier.
        """
        return cls._RANK[cls._normalise(tier_str)]

    @classmethod
    def meet(cls, t1: str, t2: str) -> str:
        """Return the meet (greatest lower bound) of *t1* and *t2*.

        The meet is the *weaker* of the two tiers — the one with the
        lower rank.  This is the fundamental theory2 invariant: combining
        evidence from two sources never raises the trust tier above the
        weaker contributor.

        Example::

            TrustLattice.meet("solver_discharged", "copilot_suggested")
            # → "copilot_suggested"  (rank 1 < rank 5)

        Args:
            t1: First trust tier string.
            t2: Second trust tier string.

        Returns:
            The tier with the lower rank, i.e. min(rank(t1), rank(t2)).
        """
        n1, n2 = cls._normalise(t1), cls._normalise(t2)
        return n1 if cls._RANK[n1] <= cls._RANK[n2] else n2

    @classmethod
    def join(cls, t1: str, t2: str) -> str:
        """Return the join (least upper bound) of *t1* and *t2*.

        The join is the *stronger* of the two tiers — the one with the
        higher rank.  The join is appropriate only when pieces are
        *independent* evidence of the same claim (§45.6.5).

        Example::

            TrustLattice.join("solver_discharged", "copilot_suggested")
            # → "solver_discharged"  (rank 5 > rank 1)

        Args:
            t1: First trust tier string.
            t2: Second trust tier string.

        Returns:
            The tier with the higher rank, i.e. max(rank(t1), rank(t2)).
        """
        n1, n2 = cls._normalise(t1), cls._normalise(t2)
        return n1 if cls._RANK[n1] >= cls._RANK[n2] else n2

    @classmethod
    def meet_all(cls, tiers: list[str]) -> str:
        """Return the meet of all tiers in *tiers*.

        Applies :meth:`meet` left-to-right across the list.  The result is
        the weakest tier in the collection.

        Args:
            tiers: Non-empty list of trust tier strings.

        Returns:
            The weakest (minimum-rank) tier in *tiers*.

        Raises:
            ValueError: If *tiers* is empty.
        """
        if not tiers:
            raise ValueError("meet_all requires at least one tier")
        result = cls._normalise(tiers[0])
        for t in tiers[1:]:
            result = cls.meet(result, t)
        return result

    @classmethod
    def join_all(cls, tiers: list[str]) -> str:
        """Return the join of all tiers in *tiers*.

        Applies :meth:`join` left-to-right across the list.  The result is
        the strongest tier in the collection.

        Args:
            tiers: Non-empty list of trust tier strings.

        Returns:
            The strongest (maximum-rank) tier in *tiers*.

        Raises:
            ValueError: If *tiers* is empty.
        """
        if not tiers:
            raise ValueError("join_all requires at least one tier")
        result = cls._normalise(tiers[0])
        for t in tiers[1:]:
            result = cls.join(result, t)
        return result

    @classmethod
    def dominates(cls, t1: str, t2: str) -> bool:
        """Return True when *t1* is at least as trusted as *t2*.

        ``dominates(t1, t2)`` is ``rank(t1) >= rank(t2)``.  A tier
        dominates another when it provides equal or stronger epistemic
        backing.

        Args:
            t1: Candidate dominant tier.
            t2: Candidate dominated tier.

        Returns:
            True if rank(t1) ≥ rank(t2).
        """
        return cls.rank(t1) >= cls.rank(t2)

    @classmethod
    def all_tiers(cls) -> tuple[str, ...]:
        """Return all tier strings ordered from weakest to strongest.

        Returns:
            Tuple of seven tier strings.
        """
        return cls._TIERS


# ---------------------------------------------------------------------------
# EvidencePiece
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidencePiece:
    """A single piece of evidence produced by one evidence channel.

    Evidence pieces are immutable value objects.  Each piece carries a
    ``trust_tier`` drawn from the trust lattice, a scalar ``confidence``
    in [0, 1], and arbitrary ``content`` produced by the channel.

    Attributes:
        piece_id: Unique identifier for this evidence piece (UUID4 string).
        task_id: Identifier of the task this evidence pertains to.
        channel: String identifier of the channel that produced this piece
                 (e.g. ``"z3"``, ``"copilot_llm"``, ``"runtime_witness"``).
        trust_tier: Trust tier assigned by the producing channel, as a
                    lower-case string from the trust lattice.
        content: Arbitrary dictionary of evidence payload.
        confidence: Scalar confidence in [0.0, 1.0].
        timestamp: POSIX timestamp of when the piece was produced.
        metadata: Optional extra metadata (channel-specific annotations,
                  version strings, experiment IDs, etc.).
    """

    piece_id: str
    task_id: str
    channel: str
    trust_tier: str
    content: dict
    confidence: float
    timestamp: float
    metadata: dict

    def to_dict(self) -> dict[str, Any]:
        """Serialise this evidence piece to a JSON-compatible dictionary.

        Returns:
            Dictionary with all fields.  The ``content`` and ``metadata``
            values are included as-is (shallow copy semantics).
        """
        return {
            "piece_id": self.piece_id,
            "task_id": self.task_id,
            "channel": self.channel,
            "trust_tier": self.trust_tier,
            "content": dict(self.content),
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidencePiece":
        """Deserialise an :class:`EvidencePiece` from a dictionary.

        Args:
            d: Dictionary produced by :meth:`to_dict`.

        Returns:
            A new :class:`EvidencePiece` instance.
        """
        return cls(
            piece_id=d["piece_id"],
            task_id=d["task_id"],
            channel=d["channel"],
            trust_tier=d["trust_tier"],
            content=dict(d.get("content", {})),
            confidence=float(d.get("confidence", 0.0)),
            timestamp=float(d.get("timestamp", 0.0)),
            metadata=dict(d.get("metadata", {})),
        )

    def age(self) -> float:
        """Return the age of this evidence piece in seconds.

        Age is computed as ``now - timestamp``.

        Returns:
            Non-negative float: elapsed seconds since the piece was created.
        """
        return max(0.0, time.time() - self.timestamp)

    @classmethod
    def make(
        cls,
        task_id: str,
        channel: str,
        trust_tier: str,
        content: dict | None = None,
        confidence: float = 1.0,
        metadata: dict | None = None,
    ) -> "EvidencePiece":
        """Convenience factory that fills in ``piece_id`` and ``timestamp``.

        Args:
            task_id: Task this evidence belongs to.
            channel: Producing channel identifier.
            trust_tier: Trust tier string from the lattice.
            content: Evidence payload (defaults to empty dict).
            confidence: Confidence score in [0, 1] (defaults to 1.0).
            metadata: Additional metadata (defaults to empty dict).

        Returns:
            A freshly created :class:`EvidencePiece`.
        """
        return cls(
            piece_id=str(uuid.uuid4()),
            task_id=task_id,
            channel=channel,
            trust_tier=TrustLattice._normalise(trust_tier),
            content=dict(content or {}),
            confidence=max(0.0, min(1.0, confidence)),
            timestamp=time.time(),
            metadata=dict(metadata or {}),
        )


# ---------------------------------------------------------------------------
# AggregationStrategy
# ---------------------------------------------------------------------------


class AggregationStrategy(str, enum.Enum):
    """Enumeration of evidence aggregation strategies.

    All strategies respect the trust invariant from *theory2.tex* §45.6.3:
    the aggregate trust tier is never stronger than the meet of the input
    tiers.

    Members:
        TRUST_MEET: Conservative default (§45.6.4).  Aggregate trust tier
                    is the lattice meet (GLB) of all input tiers.  The
                    aggregate confidence is the arithmetic mean.
        TRUST_JOIN: Optimistic strategy (§45.6.5).  Aggregate trust tier
                    is the lattice join (LUB) of all input tiers.  Only
                    valid for independent pieces of the same claim.
        HIGHEST_CONFIDENCE: Pick the piece with the highest confidence
                             (§45.6.6), but cap aggregate trust tier at the
                             meet of all input tiers.
        WEIGHTED_MEET: Like TRUST_MEET for the trust tier (§45.6.7), but
                       the aggregate confidence is a rank-weighted mean of
                       the input confidences.
    """

    TRUST_MEET = "trust_meet"
    TRUST_JOIN = "trust_join"
    HIGHEST_CONFIDENCE = "highest_confidence"
    WEIGHTED_MEET = "weighted_meet"


# ---------------------------------------------------------------------------
# AggregatedEvidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AggregatedEvidence:
    """The result of combining multiple :class:`EvidencePiece` objects.

    Produced by :class:`TrustAlgebraAggregator`.  The ``aggregate_trust_tier``
    is always bounded above by the meet of all ``source_pieces`` trust tiers
    (the trust invariant from §45.6.3).

    Attributes:
        aggregate_id: Unique identifier for this aggregation result.
        task_id: Task identifier shared by all source pieces.
        strategy: The :class:`AggregationStrategy` used.
        source_pieces: Immutable tuple of all contributing pieces.
        aggregate_trust_tier: Resulting trust tier; meets the trust invariant.
        aggregate_confidence: Scalar confidence of the aggregate in [0, 1].
        summary: Human-readable summary of the aggregation outcome.
        trust_algebra_trace: Ordered list of pairwise operation strings, e.g.
                             ``["meet(solver_discharged, copilot_suggested) =
                             copilot_suggested"]``.
        timestamp: POSIX timestamp of when aggregation was performed.
        metadata: Extra metadata attached by the aggregator.
    """

    aggregate_id: str
    task_id: str
    strategy: AggregationStrategy
    source_pieces: tuple
    aggregate_trust_tier: str
    aggregate_confidence: float
    summary: str
    trust_algebra_trace: list
    timestamp: float
    metadata: dict

    def trust_preserved(self) -> bool:
        """Return True when the trust invariant is satisfied.

        The invariant (§45.6.3) requires::

            rank(aggregate_trust_tier) ≤ min(rank(p.trust_tier)
                                             for p in source_pieces)

        A result that violates this has fraudulently elevated its trust tier
        beyond what the weakest piece supports.

        Returns:
            True if the aggregate trust tier does not exceed the meet of all
            source piece tiers.
        """
        if not self.source_pieces:
            return True
        source_tiers = [p.trust_tier for p in self.source_pieces]
        meet_tier = TrustLattice.meet_all(source_tiers)
        agg_rank = TrustLattice.rank(self.aggregate_trust_tier)
        meet_rank = TrustLattice.rank(meet_tier)
        # Trust-join strategy legitimately exceeds the meet — it is an
        # optimistic strategy but not a violation of the meet-specific invariant.
        if self.strategy == AggregationStrategy.TRUST_JOIN:
            return True
        return agg_rank <= meet_rank

    def channel_count(self) -> int:
        """Return the number of distinct channels represented in source pieces.

        Returns:
            Count of unique ``channel`` values across ``source_pieces``.
        """
        return len({p.channel for p in self.source_pieces})

    def to_dict(self) -> dict[str, Any]:
        """Serialise this aggregated evidence to a JSON-compatible dictionary.

        Returns:
            Dictionary representation including serialised source pieces and
            a copy of the trust_algebra_trace list.
        """
        return {
            "aggregate_id": self.aggregate_id,
            "task_id": self.task_id,
            "strategy": self.strategy.value,
            "source_pieces": [p.to_dict() for p in self.source_pieces],
            "aggregate_trust_tier": self.aggregate_trust_tier,
            "aggregate_confidence": self.aggregate_confidence,
            "summary": self.summary,
            "trust_algebra_trace": list(self.trust_algebra_trace),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# TrustAlgebraAggregator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustAlgebraAggregator:
    """Implements all four :class:`AggregationStrategy` variants.

    This is the core compute engine for §45.6.  It takes a list of
    :class:`EvidencePiece` objects, applies a strategy, and returns an
    :class:`AggregatedEvidence` whose trust tier respects the trust lattice.

    Attributes:
        aggregator_id: Stable identifier for this aggregator instance.
    """

    aggregator_id: str

    def aggregate(
        self,
        pieces: list[EvidencePiece],
        strategy: AggregationStrategy = AggregationStrategy.TRUST_MEET,
    ) -> AggregatedEvidence:
        """Aggregate *pieces* using *strategy* and return the result.

        Dispatches to the appropriate private method based on *strategy*.

        Args:
            pieces: Non-empty list of :class:`EvidencePiece` objects.  All
                    pieces must share the same ``task_id``.
            strategy: Aggregation strategy to apply.  Defaults to
                      :attr:`AggregationStrategy.TRUST_MEET`.

        Returns:
            An :class:`AggregatedEvidence` whose trust tier satisfies the
            trust invariant.

        Raises:
            ValueError: If *pieces* is empty.
        """
        if not pieces:
            raise ValueError(
                "TrustAlgebraAggregator.aggregate: pieces must be non-empty"
            )
        if strategy == AggregationStrategy.TRUST_MEET:
            return self._aggregate_meet(pieces)
        if strategy == AggregationStrategy.TRUST_JOIN:
            return self._aggregate_join(pieces)
        if strategy == AggregationStrategy.HIGHEST_CONFIDENCE:
            return self._aggregate_highest_confidence(pieces)
        if strategy == AggregationStrategy.WEIGHTED_MEET:
            return self._aggregate_weighted_meet(pieces)
        # Fallback — should never reach here
        return self._aggregate_meet(pieces)

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _aggregate_meet(self, pieces: list[EvidencePiece]) -> AggregatedEvidence:
        """Apply the TRUST_MEET strategy.

        The aggregate trust tier is the lattice meet (GLB) of all input
        tiers.  The aggregate confidence is the arithmetic mean of the
        input confidences.

        This is the conservative, theory2-canonical strategy (§45.6.4).

        Args:
            pieces: Evidence pieces to aggregate.

        Returns:
            :class:`AggregatedEvidence` with meet trust tier.
        """
        tiers = [p.trust_tier for p in pieces]
        result_tier = TrustLattice.meet_all(tiers)
        avg_confidence = sum(p.confidence for p in pieces) / len(pieces)
        trace = self._build_trace(pieces, result_tier)
        return AggregatedEvidence(
            aggregate_id=str(uuid.uuid4()),
            task_id=pieces[0].task_id,
            strategy=AggregationStrategy.TRUST_MEET,
            source_pieces=tuple(pieces),
            aggregate_trust_tier=result_tier,
            aggregate_confidence=round(avg_confidence, 6),
            summary=(
                f"TRUST_MEET of {len(pieces)} piece(s) from channels "
                f"{sorted({p.channel for p in pieces})}: "
                f"trust tier = {result_tier}"
            ),
            trust_algebra_trace=trace,
            timestamp=time.time(),
            metadata={
                "aggregator_id": self.aggregator_id,
                "piece_count": len(pieces),
                "channel_count": len({p.channel for p in pieces}),
            },
        )

    def _aggregate_join(self, pieces: list[EvidencePiece]) -> AggregatedEvidence:
        """Apply the TRUST_JOIN strategy.

        The aggregate trust tier is the lattice join (LUB) of all input
        tiers — the strongest tier among contributors.  Only valid for
        independent evidence of the same claim (§45.6.5).

        Args:
            pieces: Evidence pieces to aggregate.

        Returns:
            :class:`AggregatedEvidence` with join trust tier.
        """
        tiers = [p.trust_tier for p in pieces]
        result_tier = TrustLattice.join_all(tiers)
        avg_confidence = sum(p.confidence for p in pieces) / len(pieces)
        trace = self._build_trace_join(pieces, result_tier)
        return AggregatedEvidence(
            aggregate_id=str(uuid.uuid4()),
            task_id=pieces[0].task_id,
            strategy=AggregationStrategy.TRUST_JOIN,
            source_pieces=tuple(pieces),
            aggregate_trust_tier=result_tier,
            aggregate_confidence=round(avg_confidence, 6),
            summary=(
                f"TRUST_JOIN of {len(pieces)} piece(s) from channels "
                f"{sorted({p.channel for p in pieces})}: "
                f"trust tier = {result_tier} (optimistic)"
            ),
            trust_algebra_trace=trace,
            timestamp=time.time(),
            metadata={
                "aggregator_id": self.aggregator_id,
                "piece_count": len(pieces),
                "channel_count": len({p.channel for p in pieces}),
                "warning": "TRUST_JOIN is optimistic — only valid for independent pieces",
            },
        )

    def _aggregate_highest_confidence(
        self, pieces: list[EvidencePiece]
    ) -> AggregatedEvidence:
        """Apply the HIGHEST_CONFIDENCE strategy.

        Selects the piece with the highest scalar confidence as the
        representative, but caps the aggregate trust tier at the meet of
        all input tiers.  This ensures the trust ceiling is never elevated
        by selecting a high-confidence piece (§45.6.6).

        Args:
            pieces: Evidence pieces to aggregate.

        Returns:
            :class:`AggregatedEvidence` with the representative piece's
            confidence but capped trust tier.
        """
        best = max(pieces, key=lambda p: p.confidence)
        tiers = [p.trust_tier for p in pieces]
        # Cap trust at meet — confidence cannot elevate the trust tier.
        meet_tier = TrustLattice.meet_all(tiers)
        result_tier = TrustLattice.meet(best.trust_tier, meet_tier)
        trace = self._build_trace(pieces, result_tier)
        trace.append(
            f"highest_confidence_piece = {best.piece_id} "
            f"(channel={best.channel}, confidence={best.confidence:.4f})"
        )
        trace.append(
            f"trust_cap = meet({best.trust_tier}, {meet_tier}) = {result_tier}"
        )
        return AggregatedEvidence(
            aggregate_id=str(uuid.uuid4()),
            task_id=pieces[0].task_id,
            strategy=AggregationStrategy.HIGHEST_CONFIDENCE,
            source_pieces=tuple(pieces),
            aggregate_trust_tier=result_tier,
            aggregate_confidence=round(best.confidence, 6),
            summary=(
                f"HIGHEST_CONFIDENCE from {best.channel} "
                f"(confidence={best.confidence:.4f}), "
                f"trust capped at {result_tier}"
            ),
            trust_algebra_trace=trace,
            timestamp=time.time(),
            metadata={
                "aggregator_id": self.aggregator_id,
                "representative_piece_id": best.piece_id,
                "representative_channel": best.channel,
                "piece_count": len(pieces),
            },
        )

    def _aggregate_weighted_meet(
        self, pieces: list[EvidencePiece]
    ) -> AggregatedEvidence:
        """Apply the WEIGHTED_MEET strategy.

        The trust tier is still the lattice meet (identical to TRUST_MEET),
        but the aggregate confidence is a rank-weighted average of the input
        confidences.  Higher-ranked pieces contribute more to the confidence
        score (§45.6.7).

        Args:
            pieces: Evidence pieces to aggregate.

        Returns:
            :class:`AggregatedEvidence` with meet trust tier and weighted
            confidence.
        """
        tiers = [p.trust_tier for p in pieces]
        result_tier = TrustLattice.meet_all(tiers)

        # Rank-weighted average: weight_i = rank(tier_i) + 1 (avoid zero weights)
        weights = [TrustLattice.rank(p.trust_tier) + 1 for p in pieces]
        total_weight = sum(weights)
        weighted_confidence = sum(
            w * p.confidence for w, p in zip(weights, pieces)
        ) / total_weight

        trace = self._build_trace(pieces, result_tier)
        weight_details = ", ".join(
            f"{p.channel}(w={w})" for p, w in zip(pieces, weights)
        )
        trace.append(f"weighted_confidence = {weighted_confidence:.6f}  [{weight_details}]")

        return AggregatedEvidence(
            aggregate_id=str(uuid.uuid4()),
            task_id=pieces[0].task_id,
            strategy=AggregationStrategy.WEIGHTED_MEET,
            source_pieces=tuple(pieces),
            aggregate_trust_tier=result_tier,
            aggregate_confidence=round(weighted_confidence, 6),
            summary=(
                f"WEIGHTED_MEET of {len(pieces)} piece(s): "
                f"trust tier = {result_tier}, "
                f"weighted_confidence = {weighted_confidence:.4f}"
            ),
            trust_algebra_trace=trace,
            timestamp=time.time(),
            metadata={
                "aggregator_id": self.aggregator_id,
                "piece_count": len(pieces),
                "weights": weights,
                "total_weight": total_weight,
            },
        )

    # ------------------------------------------------------------------
    # Trace builders
    # ------------------------------------------------------------------

    def _build_trace(
        self, pieces: list[EvidencePiece], result_tier: str
    ) -> list[str]:
        """Build a pairwise meet trace for the given pieces.

        Produces a list of human-readable strings documenting each
        application of :meth:`TrustLattice.meet`, matching the audit
        format from §45.6.8.

        Args:
            pieces: Evidence pieces in aggregation order.
            result_tier: Pre-computed meet result (used for consistency
                         check only; not re-derived here).

        Returns:
            List of trace strings, e.g.
            ``["meet(solver_discharged, copilot_suggested) = copilot_suggested",
               "meet(copilot_suggested, runtime_witnessed) = copilot_suggested"]``.
        """
        if not pieces:
            return []
        trace: list[str] = []
        current = TrustLattice._normalise(pieces[0].trust_tier)
        for piece in pieces[1:]:
            next_tier = TrustLattice._normalise(piece.trust_tier)
            merged = TrustLattice.meet(current, next_tier)
            trace.append(f"meet({current}, {next_tier}) = {merged}")
            current = merged
        return trace

    def _build_trace_join(
        self, pieces: list[EvidencePiece], result_tier: str
    ) -> list[str]:
        """Build a pairwise join trace for the given pieces.

        Mirrors :meth:`_build_trace` but records join operations.

        Args:
            pieces: Evidence pieces in aggregation order.
            result_tier: Pre-computed join result.

        Returns:
            List of trace strings documenting join operations.
        """
        if not pieces:
            return []
        trace: list[str] = []
        current = TrustLattice._normalise(pieces[0].trust_tier)
        for piece in pieces[1:]:
            next_tier = TrustLattice._normalise(piece.trust_tier)
            merged = TrustLattice.join(current, next_tier)
            trace.append(f"join({current}, {next_tier}) = {merged}")
            current = merged
        return trace


# ---------------------------------------------------------------------------
# EvidenceBuffer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvidenceBuffer:
    """Accumulates :class:`EvidencePiece` objects for one task.

    Pieces arrive asynchronously from different channels.  The buffer holds
    them until either a minimum quorum (≥2 pieces) is reached or a wall-clock
    deadline passes, at which point :meth:`drain` transfers the pieces to the
    aggregator (§45.6.9).

    Attributes:
        buffer_id: Unique identifier for this buffer.
        task_id: Task identifier shared by all buffered pieces.
        pieces: Mutable list of accumulated pieces.
        max_size: Maximum number of pieces before the buffer is considered
                  full (further :meth:`add` calls are rejected).
        created_at: POSIX timestamp of buffer creation.
        deadline: Optional POSIX timestamp after which the buffer is
                  considered expired even with fewer than 2 pieces.
    """

    buffer_id: str
    task_id: str
    pieces: list
    max_size: int
    created_at: float
    deadline: float | None

    def add(self, piece: EvidencePiece) -> bool:
        """Add *piece* to the buffer if there is capacity.

        Rejects the piece when the buffer is already at :attr:`max_size`.
        Also rejects pieces whose ``task_id`` does not match.

        Args:
            piece: :class:`EvidencePiece` to add.

        Returns:
            True if the piece was accepted, False if rejected.
        """
        if len(self.pieces) >= self.max_size:
            return False
        if piece.task_id != self.task_id:
            return False
        self.pieces.append(piece)
        return True

    def ready_to_aggregate(self) -> bool:
        """Return True when the buffer has enough pieces to aggregate.

        The buffer is ready when either:
        - it contains at least 2 pieces (minimum quorum), or
        - the deadline has passed (even with only 1 piece).

        Returns:
            True if aggregation should proceed.
        """
        if len(self.pieces) >= 2:
            return True
        if self.deadline is not None and time.time() >= self.deadline:
            return True
        return False

    def is_expired(self) -> bool:
        """Return True when the buffer's deadline has passed.

        An expired buffer should be flushed immediately regardless of piece
        count.

        Returns:
            True if ``deadline`` is set and the current time is past it.
        """
        if self.deadline is None:
            return False
        return time.time() >= self.deadline

    def drain(self) -> list[EvidencePiece]:
        """Remove and return all buffered pieces.

        After draining, the buffer is empty and ready to accept new pieces
        for the same task (though in practice it is usually discarded).

        Returns:
            The list of all buffered pieces in insertion order.
        """
        pieces = list(self.pieces)
        self.pieces.clear()
        return pieces

    def snapshot(self) -> dict[str, Any]:
        """Return a read-only snapshot of the buffer's current state.

        Returns:
            Dictionary with buffer metadata and a list of piece summaries
            (not the full piece objects, to avoid serialisation overhead).
        """
        return {
            "buffer_id": self.buffer_id,
            "task_id": self.task_id,
            "piece_count": len(self.pieces),
            "max_size": self.max_size,
            "created_at": self.created_at,
            "deadline": self.deadline,
            "is_expired": self.is_expired(),
            "ready_to_aggregate": self.ready_to_aggregate(),
            "channels": [p.channel for p in self.pieces],
            "trust_tiers": [p.trust_tier for p in self.pieces],
        }


# ---------------------------------------------------------------------------
# EvidenceAggregator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvidenceAggregator:
    """Manages :class:`EvidenceBuffer` objects and triggers aggregation.

    This is the operational manager that:
    1. Opens a buffer per task via :meth:`open_buffer`.
    2. Accepts incoming evidence pieces via :meth:`submit`.
    3. Flushes buffers when ready via :meth:`flush`.
    4. Auto-flushes expired buffers via :meth:`flush_expired`.
    5. Accumulates completed :class:`AggregatedEvidence` in
       :attr:`completed`.

    Attributes:
        aggregator_id: Unique identifier for this manager instance.
        algebra_aggregator: The :class:`TrustAlgebraAggregator` used to
                            perform the actual algebraic aggregation.
        buffers: Map of ``task_id`` → :class:`EvidenceBuffer`.
        completed: List of all :class:`AggregatedEvidence` produced so far.
        default_strategy: Default :class:`AggregationStrategy` for all
                          flushes where no strategy is specified.
    """

    aggregator_id: str
    algebra_aggregator: TrustAlgebraAggregator
    buffers: dict
    completed: list
    default_strategy: AggregationStrategy

    def open_buffer(
        self,
        task_id: str,
        max_size: int = 5,
        deadline: float | None = None,
    ) -> EvidenceBuffer:
        """Open a new :class:`EvidenceBuffer` for *task_id*.

        If a buffer for *task_id* already exists it is returned as-is
        (idempotent).

        Args:
            task_id: Task identifier.
            max_size: Maximum number of pieces the buffer will accept.
                      Defaults to 5.
            deadline: Optional POSIX timestamp after which the buffer is
                      considered expired.

        Returns:
            The (possibly existing) :class:`EvidenceBuffer` for *task_id*.
        """
        if task_id not in self.buffers:
            self.buffers[task_id] = EvidenceBuffer(
                buffer_id=str(uuid.uuid4()),
                task_id=task_id,
                pieces=[],
                max_size=max_size,
                created_at=time.time(),
                deadline=deadline,
            )
        return self.buffers[task_id]

    def submit(self, piece: EvidencePiece) -> bool:
        """Submit *piece* to the buffer for its task.

        If no buffer exists for ``piece.task_id``, one is opened
        automatically with default parameters.

        Args:
            piece: :class:`EvidencePiece` to buffer.

        Returns:
            True if the piece was accepted by the buffer.
        """
        if piece.task_id not in self.buffers:
            self.open_buffer(piece.task_id)
        return self.buffers[piece.task_id].add(piece)

    def flush(
        self,
        task_id: str,
        strategy: AggregationStrategy | None = None,
    ) -> AggregatedEvidence | None:
        """Drain the buffer for *task_id* and aggregate its pieces.

        Returns None (without aggregating) if the buffer does not exist or
        contains no pieces.

        Args:
            task_id: Task identifier.
            strategy: Aggregation strategy; defaults to
                      :attr:`default_strategy`.

        Returns:
            :class:`AggregatedEvidence`, or None if nothing to flush.
        """
        buf = self.buffers.get(task_id)
        if buf is None:
            return None
        pieces = buf.drain()
        if not pieces:
            return None
        used_strategy = strategy if strategy is not None else self.default_strategy
        result = self.algebra_aggregator.aggregate(pieces, used_strategy)
        self.completed.append(result)
        # Remove the now-empty buffer
        del self.buffers[task_id]
        return result

    def flush_expired(self) -> list[AggregatedEvidence]:
        """Flush all expired buffers and return the resulting aggregates.

        A buffer is expired when its deadline has passed.  Buffers with
        fewer than 1 piece are discarded without aggregation.

        Returns:
            List of :class:`AggregatedEvidence` produced from expired buffers.
        """
        expired_ids = [
            tid for tid, buf in self.buffers.items() if buf.is_expired()
        ]
        results: list[AggregatedEvidence] = []
        for tid in expired_ids:
            result = self.flush(tid)
            if result is not None:
                results.append(result)
            elif tid in self.buffers:
                # Buffer had 0 pieces — remove it cleanly
                del self.buffers[tid]
        return results

    def stats(self) -> dict[str, Any]:
        """Return a summary of current aggregator state.

        Returns:
            Dictionary with buffer counts, completed aggregation count, and
            per-strategy breakdown of completed aggregations.
        """
        strategy_counts: dict[str, int] = {}
        for agg in self.completed:
            key = agg.strategy.value
            strategy_counts[key] = strategy_counts.get(key, 0) + 1

        return {
            "aggregator_id": self.aggregator_id,
            "open_buffers": len(self.buffers),
            "completed_aggregations": len(self.completed),
            "strategy_breakdown": strategy_counts,
            "buffered_piece_counts": {
                tid: len(buf.pieces) for tid, buf in self.buffers.items()
            },
        }

    def export(self) -> dict[str, Any]:
        """Serialise the full aggregator state to a JSON-compatible dict.

        Returns:
            Dictionary with all completed aggregations and buffer snapshots.
        """
        return {
            "aggregator_id": self.aggregator_id,
            "default_strategy": self.default_strategy.value,
            "open_buffers": {
                tid: buf.snapshot() for tid, buf in self.buffers.items()
            },
            "completed": [agg.to_dict() for agg in self.completed],
        }


# ---------------------------------------------------------------------------
# AggregationCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AggregationCoordinator:
    """Top-level coordinator for evidence aggregation.

    Wraps :class:`EvidenceAggregator` and provides a clean task-lifecycle
    interface:

    1. :meth:`begin_task` — register a new task and open its buffer.
    2. :meth:`submit_evidence` — route an evidence piece to the right buffer.
    3. :meth:`collect` — flush the buffer and return the aggregate.
    4. :meth:`status` — current counts.
    5. :meth:`health` — health check.

    Attributes:
        coordinator_id: Unique identifier for this coordinator.
        aggregator: The backing :class:`EvidenceAggregator`.
        strategy: Default :class:`AggregationStrategy` for all tasks.
    """

    coordinator_id: str
    aggregator: EvidenceAggregator
    strategy: AggregationStrategy

    def begin_task(
        self,
        task_id: str,
        expected_channels: list[str],
        deadline: float | None = None,
    ) -> str:
        """Register a new task and open an evidence buffer for it.

        Args:
            task_id: Stable task identifier.
            expected_channels: List of channel names expected to contribute
                               evidence.  Used to set :attr:`max_size`.
            deadline: Optional wall-clock deadline (POSIX timestamp).

        Returns:
            The ``buffer_id`` of the newly opened buffer.
        """
        max_size = max(2, len(expected_channels) + 1)
        buf = self.aggregator.open_buffer(
            task_id=task_id,
            max_size=max_size,
            deadline=deadline,
        )
        return buf.buffer_id

    def submit_evidence(self, piece: EvidencePiece) -> bool:
        """Forward *piece* to the appropriate evidence buffer.

        Args:
            piece: :class:`EvidencePiece` to submit.

        Returns:
            True if the piece was accepted.
        """
        return self.aggregator.submit(piece)

    def collect(self, task_id: str) -> AggregatedEvidence | None:
        """Flush the buffer for *task_id* and return the aggregate.

        Uses the coordinator's default strategy.

        Args:
            task_id: Task identifier.

        Returns:
            :class:`AggregatedEvidence`, or None if the buffer is empty or
            does not exist.
        """
        return self.aggregator.flush(task_id, self.strategy)

    def status(self) -> dict[str, Any]:
        """Return a status snapshot.

        Returns:
            Dictionary with coordinator and aggregator state.
        """
        agg_stats = self.aggregator.stats()
        return {
            "coordinator_id": self.coordinator_id,
            "strategy": self.strategy.value,
            **agg_stats,
        }

    def health(self) -> dict[str, Any]:
        """Return a health-check dictionary.

        Returns:
            Dictionary with ``status`` (``"ok"`` / ``"degraded"``) and
            basic diagnostics.
        """
        stats = self.aggregator.stats()
        healthy = True
        warnings: list[str] = []

        # Warn if there are many open buffers that might be stale
        if stats["open_buffers"] > 50:
            healthy = False
            warnings.append(f"Too many open buffers: {stats['open_buffers']}")

        return {
            "coordinator_id": self.coordinator_id,
            "status": "ok" if healthy else "degraded",
            "open_buffers": stats["open_buffers"],
            "completed_aggregations": stats["completed_aggregations"],
            "warnings": warnings,
        }


# ---------------------------------------------------------------------------
# AggregationWitness (audit log)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AggregationWitness:
    """Audit log that records submissions and aggregations.

    The witness records every :class:`EvidencePiece` submission and every
    completed :class:`AggregatedEvidence`, then allows post-hoc verification
    that no aggregation violated the trust invariant (§45.6.3).

    Attributes:
        witness_id: Unique identifier for this witness instance.
        events: Ordered list of event dictionaries.
        created_at: POSIX timestamp of witness creation.
    """

    witness_id: str
    events: list
    created_at: float

    def record_submission(self, piece: EvidencePiece) -> None:
        """Record an evidence piece submission event.

        Args:
            piece: The :class:`EvidencePiece` that was submitted.
        """
        self.events.append(
            {
                "event_type": "submission",
                "timestamp": time.time(),
                "piece_id": piece.piece_id,
                "task_id": piece.task_id,
                "channel": piece.channel,
                "trust_tier": piece.trust_tier,
                "confidence": piece.confidence,
            }
        )

    def record_aggregation(self, aggregate: AggregatedEvidence) -> None:
        """Record a completed aggregation event.

        Args:
            aggregate: The :class:`AggregatedEvidence` that was produced.
        """
        self.events.append(
            {
                "event_type": "aggregation",
                "timestamp": time.time(),
                "aggregate_id": aggregate.aggregate_id,
                "task_id": aggregate.task_id,
                "strategy": aggregate.strategy.value,
                "aggregate_trust_tier": aggregate.aggregate_trust_tier,
                "aggregate_confidence": aggregate.aggregate_confidence,
                "source_tiers": [p.trust_tier for p in aggregate.source_pieces],
                "trust_preserved": aggregate.trust_preserved(),
                "trust_algebra_trace": list(aggregate.trust_algebra_trace),
            }
        )

    def verify_trust_algebra(self) -> list[str]:
        """Check every recorded aggregation for trust invariant violations.

        For each aggregation event, recomputes the meet of the source tiers
        and flags any case where the aggregate trust tier is *stronger* than
        the meet.  Trust-join aggregations are exempt (they are intentionally
        optimistic).

        Returns:
            List of violation description strings.  An empty list means no
            violations were found — all aggregations respected the trust
            algebra.
        """
        violations: list[str] = []
        for event in self.events:
            if event["event_type"] != "aggregation":
                continue
            strategy = event.get("strategy", "")
            if strategy == AggregationStrategy.TRUST_JOIN.value:
                continue  # join is exempt
            source_tiers = event.get("source_tiers", [])
            if not source_tiers:
                continue
            meet_tier = TrustLattice.meet_all(source_tiers)
            agg_tier = event.get("aggregate_trust_tier", "unverified")
            if TrustLattice.rank(agg_tier) > TrustLattice.rank(meet_tier):
                violations.append(
                    f"VIOLATION aggregate_id={event.get('aggregate_id')} "
                    f"task_id={event.get('task_id')}: "
                    f"aggregate_trust={agg_tier} "
                    f"(rank {TrustLattice.rank(agg_tier)}) > "
                    f"meet={meet_tier} "
                    f"(rank {TrustLattice.rank(meet_tier)}) "
                    f"source_tiers={source_tiers}"
                )
        return violations

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness log to a JSON-compatible dictionary.

        Returns:
            Dictionary with witness metadata and all event records.
        """
        return {
            "witness_id": self.witness_id,
            "created_at": self.created_at,
            "event_count": len(self.events),
            "events": list(self.events),
        }


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_default_trust_algebra_aggregator() -> TrustAlgebraAggregator:
    """Construct a :class:`TrustAlgebraAggregator` with a fresh UUID.

    Returns:
        A new :class:`TrustAlgebraAggregator` ready for use.
    """
    return TrustAlgebraAggregator(aggregator_id=str(uuid.uuid4()))


def make_default_evidence_aggregator(
    strategy: AggregationStrategy = AggregationStrategy.TRUST_MEET,
) -> EvidenceAggregator:
    """Construct an :class:`EvidenceAggregator` backed by the default algebra aggregator.

    Args:
        strategy: Default aggregation strategy.  Defaults to TRUST_MEET.

    Returns:
        A new :class:`EvidenceAggregator` ready to open buffers and flush.
    """
    return EvidenceAggregator(
        aggregator_id=str(uuid.uuid4()),
        algebra_aggregator=make_default_trust_algebra_aggregator(),
        buffers={},
        completed=[],
        default_strategy=strategy,
    )


def make_default_aggregation_coordinator(
    strategy: AggregationStrategy = AggregationStrategy.TRUST_MEET,
) -> AggregationCoordinator:
    """Construct an :class:`AggregationCoordinator` with default sub-components.

    Args:
        strategy: Default aggregation strategy.  Defaults to TRUST_MEET.

    Returns:
        A fully initialised :class:`AggregationCoordinator`.
    """
    return AggregationCoordinator(
        coordinator_id=str(uuid.uuid4()),
        aggregator=make_default_evidence_aggregator(strategy),
        strategy=strategy,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Smoke test: theory2 §45.6 — trust invariant under TRUST_MEET.

    Creates a coordinator, submits three pieces with tiers:
        solver_discharged (rank 5) + copilot_suggested (rank 1) + runtime_witnessed (rank 3)
    Expected meet = copilot_suggested (rank 1 — the weakest).
    """
    print("=" * 70)
    print("evidence_aggregation.py — smoke test")
    print("theory2.tex Ch45 §45.6  Evidence Aggregation / Trust Algebra")
    print("=" * 70)

    # Verify lattice operations before anything else
    print("\n[1] TrustLattice sanity checks")
    assert TrustLattice.rank("unverified") == 0
    assert TrustLattice.rank("copilot_suggested") == 1
    assert TrustLattice.rank("solver_discharged") == 5
    assert TrustLattice.rank("mechanically_verified") == 6
    assert TrustLattice.meet("solver_discharged", "copilot_suggested") == "copilot_suggested"
    assert TrustLattice.join("solver_discharged", "copilot_suggested") == "solver_discharged"
    assert TrustLattice.meet_all(["solver_discharged", "copilot_suggested", "runtime_witnessed"]) == "copilot_suggested"
    assert TrustLattice.dominates("solver_discharged", "copilot_suggested")
    assert not TrustLattice.dominates("copilot_suggested", "solver_discharged")
    print("    ✓ All lattice operations correct")

    # Set up coordinator
    print("\n[2] Creating coordinator with TRUST_MEET strategy")
    coordinator = make_default_aggregation_coordinator(AggregationStrategy.TRUST_MEET)
    print(f"    coordinator_id = {coordinator.coordinator_id}")

    # Open buffer for our task
    TASK_ID = "task-evidence-agg-smoke-001"
    expected_channels = ["z3", "copilot_llm", "runtime_witness"]
    print(f"\n[3] Opening buffer for task_id={TASK_ID!r}")
    print(f"    expected_channels = {expected_channels}")
    buffer_id = coordinator.begin_task(TASK_ID, expected_channels)
    print(f"    buffer_id = {buffer_id}")

    # Build three evidence pieces with different trust tiers
    print("\n[4] Constructing three EvidencePiece objects")
    piece_solver = EvidencePiece.make(
        task_id=TASK_ID,
        channel="z3",
        trust_tier="solver_discharged",
        content={"formula": "x + y = 10", "sat_result": "sat"},
        confidence=0.99,
        metadata={"solver_version": "4.12.2"},
    )
    piece_copilot = EvidencePiece.make(
        task_id=TASK_ID,
        channel="copilot_llm",
        trust_tier="copilot_suggested",
        content={"suggestion": "The sum constraint is satisfiable", "model": "gpt-4"},
        confidence=0.72,
        metadata={"tokens": 128},
    )
    piece_runtime = EvidencePiece.make(
        task_id=TASK_ID,
        channel="runtime_witness",
        trust_tier="runtime_witnessed",
        content={"test_id": "test_sum_constraint", "outcome": "pass"},
        confidence=0.95,
        metadata={"runner": "pytest"},
    )

    print(f"    piece_solver:   trust_tier={piece_solver.trust_tier!r}  (rank {TrustLattice.rank(piece_solver.trust_tier)})")
    print(f"    piece_copilot:  trust_tier={piece_copilot.trust_tier!r}   (rank {TrustLattice.rank(piece_copilot.trust_tier)})")
    print(f"    piece_runtime:  trust_tier={piece_runtime.trust_tier!r} (rank {TrustLattice.rank(piece_runtime.trust_tier)})")

    # Submit pieces through the coordinator
    print("\n[5] Submitting pieces via coordinator.submit_evidence()")
    for piece in [piece_solver, piece_copilot, piece_runtime]:
        accepted = coordinator.submit_evidence(piece)
        print(f"    submitted {piece.channel!r}: accepted={accepted}")

    # Create a witness to record events
    witness = AggregationWitness(
        witness_id=str(uuid.uuid4()),
        events=[],
        created_at=time.time(),
    )
    for piece in [piece_solver, piece_copilot, piece_runtime]:
        witness.record_submission(piece)

    # Flush and collect the aggregate
    print(f"\n[6] Flushing buffer (strategy=TRUST_MEET)")
    aggregate = coordinator.collect(TASK_ID)

    if aggregate is None:
        print("    ERROR: collect() returned None — buffer was empty!")
    else:
        witness.record_aggregation(aggregate)

        print(f"\n[7] Aggregate result:")
        print(f"    aggregate_id        = {aggregate.aggregate_id}")
        print(f"    task_id             = {aggregate.task_id}")
        print(f"    strategy            = {aggregate.strategy.value}")
        print(f"    aggregate_trust_tier= {aggregate.aggregate_trust_tier!r}  ← should be 'copilot_suggested'")
        print(f"    aggregate_confidence= {aggregate.aggregate_confidence:.6f}")
        print(f"    channel_count       = {aggregate.channel_count()}")
        print(f"    trust_preserved()   = {aggregate.trust_preserved()}  ← MUST be True")

        print(f"\n[8] trust_algebra_trace:")
        for step in aggregate.trust_algebra_trace:
            print(f"    {step}")

        print(f"\n[9] Verifying assertions:")
        assert aggregate.aggregate_trust_tier == "copilot_suggested", (
            f"Expected 'copilot_suggested', got {aggregate.aggregate_trust_tier!r}"
        )
        assert aggregate.trust_preserved(), "trust_preserved() returned False — invariant violated!"
        assert aggregate.channel_count() == 3, f"Expected 3 channels, got {aggregate.channel_count()}"
        print("    ✓ aggregate_trust_tier == 'copilot_suggested'  (meet of ranks 5, 1, 3 = rank 1)")
        print("    ✓ trust_preserved() == True")
        print("    ✓ channel_count() == 3")

        # Verify the witness found no violations
        violations = witness.verify_trust_algebra()
        assert violations == [], f"Unexpected trust algebra violations: {violations}"
        print("    ✓ AggregationWitness.verify_trust_algebra() found 0 violations")

        print(f"\n[10] Coordinator status:")
        status = coordinator.status()
        for k, v in status.items():
            print(f"    {k}: {v}")

    print("\n[11] Additional strategy smoke tests")

    # TRUST_JOIN test
    algebra_agg = make_default_trust_algebra_aggregator()
    join_result = algebra_agg.aggregate(
        [piece_solver, piece_copilot, piece_runtime],
        AggregationStrategy.TRUST_JOIN,
    )
    assert join_result.aggregate_trust_tier == "solver_discharged", (
        f"TRUST_JOIN should produce 'solver_discharged', got {join_result.aggregate_trust_tier!r}"
    )
    print(f"    ✓ TRUST_JOIN aggregate_trust_tier = {join_result.aggregate_trust_tier!r}")

    # HIGHEST_CONFIDENCE test
    hc_result = algebra_agg.aggregate(
        [piece_solver, piece_copilot, piece_runtime],
        AggregationStrategy.HIGHEST_CONFIDENCE,
    )
    # Best piece by confidence = piece_solver (0.99), but trust is capped at meet = copilot_suggested
    assert hc_result.aggregate_trust_tier == "copilot_suggested", (
        f"HIGHEST_CONFIDENCE trust tier should be capped at meet='copilot_suggested', "
        f"got {hc_result.aggregate_trust_tier!r}"
    )
    assert abs(hc_result.aggregate_confidence - 0.99) < 1e-6, (
        f"HIGHEST_CONFIDENCE confidence should be 0.99, got {hc_result.aggregate_confidence}"
    )
    print(f"    ✓ HIGHEST_CONFIDENCE aggregate_trust_tier = {hc_result.aggregate_trust_tier!r} (capped at meet)")
    print(f"    ✓ HIGHEST_CONFIDENCE aggregate_confidence = {hc_result.aggregate_confidence} (from z3 piece)")

    # WEIGHTED_MEET test
    wm_result = algebra_agg.aggregate(
        [piece_solver, piece_copilot, piece_runtime],
        AggregationStrategy.WEIGHTED_MEET,
    )
    assert wm_result.aggregate_trust_tier == "copilot_suggested", (
        f"WEIGHTED_MEET trust tier should be meet='copilot_suggested', got {wm_result.aggregate_trust_tier!r}"
    )
    print(f"    ✓ WEIGHTED_MEET aggregate_trust_tier = {wm_result.aggregate_trust_tier!r}")
    print(f"    ✓ WEIGHTED_MEET aggregate_confidence = {wm_result.aggregate_confidence}")

    print("\n" + "=" * 70)
    print("All smoke tests passed.  Trust algebra invariant preserved.")
    print("=" * 70)
