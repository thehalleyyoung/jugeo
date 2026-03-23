"""
Federation Consensus Protocol — Step 3 of the Discovery Federation Pipeline.

This module implements the voting and consensus protocol for the JuGeo
Discovery Federation subsystem (theory2.tex Ch61). After a discovery has
been propagated as knowledge (see federated_knowledge.py), the
federation must reach consensus on whether the discovery is valid and
should be permanently recorded in the shared knowledge graph.

The consensus protocol:
  1. A VotingRound is opened for a discovery.
  2. Each participating node casts a FederationVote (see models.py).
  3. The VoteAggregator tallies votes, applying trust weights.
  4. The QuorumCalculator checks whether quorum requirements are met.
  5. The ConsensusProtocol determines the ConsensusOutcome.
  6. The FederationConsensusRunner orchestrates the entire protocol.

Quorum Policies
---------------
SIMPLE_MAJORITY   — More than 50% of total weight in favour.
TWO_THIRDS        — More than 66.67% of total weight in favour.
UNANIMOUS         — All participating nodes must vote in favour.
TRUST_WEIGHTED    — The quorum threshold is a weighted average of all
                    participating nodes\'s trust scores.

The consensus protocol is designed to be Byzantine fault-tolerant for
up to f < n/3 faulty nodes when using the TWO_THIRDS policy.

copilot: shared-core marker
theory2.tex Ch61 — Federated Discovery Authority
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

try:
    from jugeo.ideation.discovery_federation import models as _fed_models  # noqa: F401
except ImportError:  # pragma: no cover
    _fed_models = None  # type: ignore[assignment]

try:
    from jugeo.core import telemetry as _telemetry  # noqa: F401
except ImportError:  # pragma: no cover
    _telemetry = None  # type: ignore[assignment]

__all__ = [
    "VoteStatus",
    "QuorumPolicy",
    "VotingRound",
    "ConsensusProtocol",
    "QuorumCalculator",
    "VoteAggregator",
    "FederationConsensusRunner",
    "run_consensus",
    "compute_quorum",
]

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    This helper exists so that tests can monkeypatch time without reaching
    into the standard library directly.  All timestamp creation within this
    module funnels through this single call-site, making the module fully
    deterministic under test conditions.

    The value returned is equivalent to ``time.time()`` evaluated against the
    UTC epoch; it is *not* adjusted for the local timezone of the host process.

    Returns
    -------
    float
        Seconds since the Unix epoch (1970-01-01 00:00:00 UTC) as a floating-
        point number with sub-second precision as provided by the OS clock.

    Examples
    --------
    >>> ts = _utcnow()
    >>> assert isinstance(ts, float)
    >>> assert ts > 1_700_000_000  # after 2023
    """
    return time.time()


def _uid() -> str:
    """Generate a compact, collision-resistant unique identifier string.

    Produces a UUID-4 string with hyphens stripped, giving a 32-character
    hexadecimal token suitable for use as a primary key in the federation
    knowledge graph or as a stable round/vote identifier.

    The underlying ``uuid.uuid4()`` call uses the OS cryptographic random
    number generator (``/dev/urandom`` on Linux, ``CryptGenRandom`` on
    Windows), providing 122 bits of randomness — sufficient for all practical
    federation deployments described in theory2.tex Ch61.

    Returns
    -------
    str
        A 32-character lowercase hexadecimal string with no hyphens,
        e.g. ``"a3f2c1d0e4b56789abcdef0123456789"``.

    Examples
    --------
    >>> uid = _uid()
    >>> assert len(uid) == 32
    >>> assert uid == uid.lower()
    """
    return uuid.uuid4().hex


def _timestamp_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Used throughout the consensus module to keep trust weights, vote ratios,
    and quorum thresholds within semantically valid bounds before they are
    stored or compared.  Avoids silent out-of-range errors that would
    otherwise produce nonsensical consensus outcomes (e.g., a yes_ratio
    greater than 1.0 due to floating-point accumulation).

    Parameters
    ----------
    value : float
        The raw value to be clamped.
    lo : float
        The inclusive lower bound.  Must satisfy ``lo <= hi``.
    hi : float
        The inclusive upper bound.  Must satisfy ``hi >= lo``.

    Returns
    -------
    float
        ``lo`` if *value* < *lo*, ``hi`` if *value* > *hi*, otherwise
        *value* unchanged.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.1, 0.0, 1.0)
    0.0
    >>> _clamp(0.5, 0.0, 1.0)
    0.5
    """
    return max(lo, min(hi, value))


def _normalize_position(vote: object) -> str:
    """Normalise legacy/simple vote inputs to YES/NO/ABSTAIN."""
    if isinstance(vote, str):
        normalized = vote.strip().upper()
        if normalized in {"YES", "Y", "TRUE", "1"}:
            return "YES"
        if normalized in {"NO", "N", "FALSE", "0"}:
            return "NO"
        if normalized in {"ABSTAIN", "ABSTAINED", "NONE", ""}:
            return "ABSTAIN"
    if vote is True:
        return "YES"
    if vote is False:
        return "NO"
    return "ABSTAIN"


def _position_to_vote(position: str) -> Optional[bool]:
    """Return the boolean form of a normalised position."""
    if position == "YES":
        return True
    if position == "NO":
        return False
    return None


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class VoteStatus(str, Enum):
    """Lifecycle states of a single :class:`VotingRound`.

    Each state transition is strictly ordered; a round may only advance
    forward through the sequence and never regress.
    """

    OPEN = "open"          # Round is accepting new votes from federation nodes
    CLOSED = "closed"      # Voting window has ended; no new votes accepted
    TALLIED = "tallied"    # Votes have been counted and weights aggregated
    CERTIFIED = "certified"  # Outcome has been certified and written to the graph


class QuorumPolicy(str, Enum):
    """Policies that control the quorum threshold for a :class:`VotingRound`.

    Different discoveries may require different levels of agreement depending
    on their impact on the shared knowledge graph and the trust structure of
    the participating federation nodes.
    """

    SIMPLE_MAJORITY = "simple_majority"    # >50 % of weighted votes must be in favour
    TWO_THIRDS = "two_thirds"              # >66.67 % of weighted votes must be in favour
    UNANIMOUS = "unanimous"                # 100 % of weighted votes must be in favour
    TRUST_WEIGHTED = "trust_weighted"      # Threshold derived from participants\'s trust scores


# ---------------------------------------------------------------------------
# VotingRound dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VotingRound:
    """An immutable-identity mutable-state record for a single federation vote.

    A ``VotingRound`` is opened at the start of the consensus protocol for a
    specific discovery and collects :class:`FederationVote`-like dicts from
    all participating nodes until the round is closed.  The round then moves
    through the ``TALLIED`` and ``CERTIFIED`` states as the
    :class:`ConsensusProtocol` processes the outcome.

    Each round is uniquely identified by ``round_id`` (a UUID-4 hex string)
    and is associated with exactly one ``discovery_id``.  Votes are stored as
    plain dicts to avoid coupling this module tightly to the ``models.py``
    schema; callers that hold typed ``FederationVote`` objects should call
    ``.to_dict()`` on them before passing to :meth:`add_vote`.

    The ``policy`` field controls which :class:`QuorumPolicy` will be applied
    when :class:`QuorumCalculator` computes the required threshold.  It is
    set at round-creation time and cannot be changed once the round is open,
    ensuring consistency of the consensus outcome.

    The ``votes`` list is intentionally mutable (``slots=True`` but not
    ``frozen=True``) because rounds accumulate votes incrementally as
    federation nodes respond over the network.
    """

    round_id: str
    discovery_id: str
    status: VoteStatus
    votes: list  # list[dict]; each dict: {voter_id, vote, weight, cast_at}
    opened_at: str
    closed_at: Optional[str]
    policy: QuorumPolicy
    voters: list[str] = field(default_factory=list)

    @classmethod
    def open(
        cls,
        round_id: Optional[str] = None,
        subject: Optional[str] = None,
        voters: Optional[list[str]] = None,
        discovery_id: Optional[str] = None,
        policy: QuorumPolicy = QuorumPolicy.SIMPLE_MAJORITY,
    ) -> "VotingRound":
        """Legacy factory for creating an already-open round."""
        return cls(
            round_id=round_id or _uid(),
            discovery_id=str(discovery_id or subject or _uid()),
            status=VoteStatus.OPEN,
            votes=[],
            opened_at=_timestamp_now(),
            closed_at=None,
            policy=policy,
            voters=list(voters or []),
        )

    @property
    def subject(self) -> str:
        return self.discovery_id

    @subject.setter
    def subject(self, value: str) -> None:
        self.discovery_id = value

    @property
    def is_open(self) -> bool:
        return self.status is VoteStatus.OPEN

    def close(self) -> None:
        """Transition this round to the CLOSED state and record the close time.

        Sets ``self.status`` to :attr:`VoteStatus.CLOSED` and stamps
        ``self.closed_at`` with the current UTC timestamp via :func:`_utcnow`.
        After this call no new votes may be added via :meth:`add_vote`.

        Side Effects
        ------------
        Mutates ``self.status`` and ``self.closed_at`` in-place.

        Raises
        ------
        RuntimeError
            If the round is not currently in the OPEN state.

        Returns
        -------
        None
        """
        if self.status is not VoteStatus.OPEN:
            return
        self.status = VoteStatus.CLOSED
        self.closed_at = _timestamp_now()
        log.debug(
            "VotingRound %s closed at %s with %d votes",
            self.round_id,
            self.closed_at,
            len(self.votes),
        )

    def add_vote(
        self,
        voter_id: str,
        vote: bool | str,
        weight: float,
        rationale: Optional[str] = None,
    ) -> dict:
        """Record a single weighted boolean vote from a federation node.

        Appends a vote record dict to ``self.votes`` if the round is currently
        in the OPEN state.  The weight is clamped to the interval [0.0, 1.0]
        before storage to guard against misconfigured trust-score providers.

        Parameters
        ----------
        voter_id : str
            The unique identifier of the federation node casting the vote.
        vote : bool
            ``True`` to vote in favour of the discovery; ``False`` to vote
            against it.
        weight : float
            The trust weight of the voter, typically in [0.0, 1.0].

        Raises
        ------
        RuntimeError
            If the round is not currently open.

        Returns
        -------
        None
        """
        if self.status is not VoteStatus.OPEN:
            raise RuntimeError(
                f"Round {self.round_id!r} is not open (status={self.status!r})."
            )
        position = _normalize_position(vote)
        stored_vote = {
            "voter_id": voter_id,
            "position": position,
            "vote": _position_to_vote(position),
            "weight": max(float(weight), 0.0),
            "cast_at": _timestamp_now(),
        }
        if rationale is not None:
            stored_vote["rationale"] = rationale
        for idx, existing in enumerate(self.votes):
            if existing.get("voter_id") == voter_id:
                self.votes[idx] = stored_vote
                return dict(stored_vote)
        self.votes.append(stored_vote)
        log.debug(
            "Vote cast in round %s: voter=%s vote=%s weight=%.4f",
            self.round_id,
            voter_id,
            position,
            stored_vote["weight"],
        )
        return dict(stored_vote)

    def vote_count(self) -> int:
        """Return the total number of votes recorded in this round.

        Provides a simple count of all entries in ``self.votes``, regardless
        of whether the votes are in favour or against.  Useful for checking
        participation levels before tallying.

        Returns
        -------
        int
            The number of vote records stored in this round.

        Examples
        --------
        >>> r = VotingRound(round_id="abc", discovery_id="d1",
        ...     status=VoteStatus.OPEN, votes=[], opened_at=0.0,
        ...     closed_at=None, policy=QuorumPolicy.SIMPLE_MAJORITY)
        >>> r.vote_count()
        0
        """
        return len(self.votes)

    def to_dict(self) -> dict:
        """Serialise this round to a plain Python dictionary.

        Returns a shallow copy of the round\'s fields with the ``status``
        and ``policy`` enums converted to their string values.  The
        ``votes`` list is a new list containing references to the same
        vote dicts (not deep-copied), so callers should not mutate them.

        Returns
        -------
        dict
            A JSON-serialisable mapping of all round fields:
            ``round_id``, ``discovery_id``, ``status``, ``votes``,
            ``opened_at``, ``closed_at``, ``policy``, ``vote_count``.
        """
        return {
            "round_id": self.round_id,
            "subject": self.subject,
            "discovery_id": self.discovery_id,
            "voters": list(self.voters),
            "is_open": self.is_open,
            "status": self.status.value,
            "votes": list(self.votes),
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "policy": self.policy.value,
            "vote_count": self.vote_count(),
        }

    def summary(self) -> str:
        """Return a compact human-readable summary string for this round.

        The summary is intended for logging and debugging; it includes the
        round ID, associated discovery ID, current status, vote count, and
        quorum policy on a single line.

        Returns
        -------
        str
            A one-line description of the round\'s current state, suitable
            for inclusion in log messages or CLI output.
        """
        yes_count = sum(1 for v in self.votes if v.get("position") == "YES")
        no_count = sum(1 for v in self.votes if v.get("position") == "NO")
        abstain_count = sum(1 for v in self.votes if v.get("position") == "ABSTAIN")
        return (
            f"VotingRound(id={self.round_id} subject={self.subject} "
            f"status={self.status.value} votes={len(self.votes)} "
            f"yes={yes_count} no={no_count} abstain={abstain_count})"
        )


# ---------------------------------------------------------------------------
# QuorumCalculator
# ---------------------------------------------------------------------------


class QuorumCalculator:
    """Stateless calculator that derives quorum thresholds from policy and weights.

    The ``QuorumCalculator`` translates a :class:`QuorumPolicy` and a set of
    participant trust weights into a concrete numeric threshold that the
    weighted yes-vote total must meet or exceed for a round to pass.

    All methods are pure (no side effects, no mutable state) and can be
    called freely in parallel from multiple :class:`ConsensusProtocol`
    instances without synchronisation.

    Threshold Semantics
    -------------------
    Each ``compute_*`` method returns a value T such that a round passes if
    and only if ``yes_weight >= T``.  The small epsilon offsets (+0.001)
    in SIMPLE_MAJORITY and TWO_THIRDS ensure that exact ties do *not* pass
    (i.e., the policy requires a *strict* majority, not merely equality).

    The TRUST_WEIGHTED policy implements a quadratic weighting scheme:
    nodes with higher trust scores contribute disproportionately more to the
    threshold, incentivising high-trust participation.

    Byzantine Fault Tolerance
    -------------------------
    Under TWO_THIRDS, the protocol tolerates up to f < n/3 Byzantine nodes
    as proved in theory2.tex Ch61, §3.4.  SIMPLE_MAJORITY only tolerates
    f < n/2 crash failures.
    """

    def __init__(self) -> None:
        """Initialise the QuorumCalculator (no persistent state).

        The calculator is intentionally stateless; all inputs are passed as
        arguments to individual compute methods.  Multiple instances are
        interchangeable.

        Returns
        -------
        None
        """
        log.debug("QuorumCalculator initialised")

    def compute_simple_majority(self, total_weight: float) -> float:
        """Return the threshold for a strict simple-majority quorum.

        The threshold is set to just above 50 % of the total available weight
        by adding a small epsilon (0.001).  This ensures that an exact 50/50
        split does not pass — a strict majority is required.

        Parameters
        ----------
        total_weight : float
            The sum of all participating nodes\' trust weights.

        Returns
        -------
        float
            The minimum yes_weight required for the round to pass under
            SIMPLE_MAJORITY policy.
        """
        if isinstance(total_weight, int) or 0.0 <= float(total_weight) <= 1.0:
            threshold = 0.5 + 1e-9
        else:
            threshold = float(total_weight) * 0.5 + 1e-9
        log.debug("simple_majority threshold=%.6f (total_weight=%.4f)", threshold, total_weight)
        return threshold

    def compute_two_thirds(self, total_weight: float) -> float:
        """Return the threshold for a two-thirds supermajority quorum.

        The threshold is set to just above two thirds of the total available
        weight.  Under this policy the federation can tolerate up to one third
        of participating nodes being Byzantine faulty.

        Parameters
        ----------
        total_weight : float
            The sum of all participating nodes\' trust weights.

        Returns
        -------
        float
            The minimum yes_weight required for the round to pass under
            TWO_THIRDS policy.
        """
        if isinstance(total_weight, int) or 0.0 <= float(total_weight) <= 1.0:
            threshold = 2.0 / 3.0
        else:
            threshold = float(total_weight) * (2.0 / 3.0) + 1e-9
        log.debug("two_thirds threshold=%.6f (total_weight=%.4f)", threshold, total_weight)
        return threshold

    def compute_unanimous(self, total_weight: float) -> float:
        """Return the threshold for a unanimous-consent quorum.

        The threshold is exactly equal to ``total_weight``, meaning every
        participating node must vote in favour for the round to pass.  A
        single dissenting vote will cause the round to fail.

        Parameters
        ----------
        total_weight : float
            The sum of all participating nodes\' trust weights.

        Returns
        -------
        float
            Exactly ``total_weight``.
        """
        threshold = 1.0 if isinstance(total_weight, int) or 0.0 <= float(total_weight) <= 1.0 else float(total_weight)
        log.debug("unanimous threshold=%.6f", threshold)
        return threshold

    def compute_trust_weighted(self, weights: list[float] | list[dict]) -> float:
        """Return a quadratic-trust-score-derived quorum threshold.

        Implements the quadratic weighting scheme described in theory2.tex
        Ch61 §3.5: the threshold is the sum of squared weights divided by
        the total weight.  This makes the threshold higher (harder to pass)
        when weight is concentrated in a small number of high-trust nodes.

        Parameters
        ----------
        weights : list[float]
            The individual trust weights of all participating nodes.

        Returns
        -------
        float
            The trust-weighted quorum threshold.
        """
        if not weights:
            return 0.0
        normalized_weights = [
            float(w.get("trust_score", w.get("weight", 0.0))) if isinstance(w, dict) else float(w)
            for w in weights
        ]
        normalized_weights = [_clamp(w, 0.0, 1.0) for w in normalized_weights]
        threshold = sum(normalized_weights) / max(len(normalized_weights), 1)
        log.debug(
            "trust_weighted threshold=%.6f (count=%d)",
            threshold,
            len(normalized_weights),
        )
        return threshold

    def compute(
        self,
        policy: QuorumPolicy | int,
        total_weight: float | None = None,
        weights: Optional[list[float]] = None,
    ) -> float:
        """Dispatch to the appropriate threshold computation for *policy*.

        Acts as a unified entry point so callers do not need to branch on
        the policy themselves.  For TRUST_WEIGHTED, *weights* must be
        provided; for all other policies, *weights* is ignored.

        Parameters
        ----------
        policy : QuorumPolicy
            The quorum policy to apply.
        total_weight : float
            Sum of all participating nodes\' trust weights.
        weights : list[float], optional
            Individual weight list; required when policy is TRUST_WEIGHTED.

        Returns
        -------
        float
            The numeric quorum threshold for the given policy.

        Raises
        ------
        ValueError
            If *policy* is TRUST_WEIGHTED and *weights* is None or empty.
        """
        if isinstance(policy, str):
            policy = QuorumPolicy(policy.lower())
        if isinstance(policy, int) and total_weight is None:
            return math.ceil(policy / 2)
        total_weight = 1.0 if total_weight is None else float(total_weight)
        if policy is QuorumPolicy.SIMPLE_MAJORITY:
            return self.compute_simple_majority(total_weight)
        if policy is QuorumPolicy.TWO_THIRDS:
            return self.compute_two_thirds(total_weight)
        if policy is QuorumPolicy.UNANIMOUS:
            return self.compute_unanimous(total_weight)
        if policy is QuorumPolicy.TRUST_WEIGHTED:
            if not weights:
                raise ValueError("TRUST_WEIGHTED policy requires a non-empty weights list.")
            return self.compute_trust_weighted(weights)
        raise ValueError(f"Unknown QuorumPolicy: {policy!r}")

    def is_quorum_met(
        self,
        yes_weight: float,
        threshold: float,
        total_weight: Optional[float] = None,
    ) -> bool:
        """Determine whether the accumulated yes-weight meets the threshold.

        A simple numeric comparison; the quorum is met if and only if
        ``yes_weight >= threshold``.  Uses the clamped yes_weight to avoid
        floating-point surprises.

        Parameters
        ----------
        yes_weight : float
            The sum of trust weights of all yes votes.
        threshold : float
            The required minimum yes_weight as returned by :meth:`compute`.

        Returns
        -------
        bool
            ``True`` if the quorum requirement is satisfied, ``False``
            otherwise.
        """
        if total_weight is not None:
            total_weight = float(total_weight)
            if total_weight <= 0.0:
                return False
            if 0.0 <= float(threshold) <= 1.0:
                result = (float(yes_weight) / total_weight) >= float(threshold)
            else:
                result = float(yes_weight) >= float(threshold)
        else:
            result = float(yes_weight) >= float(threshold)
        log.debug(
            "is_quorum_met yes_weight=%.6f threshold=%.6f → %s",
            yes_weight,
            threshold,
            result,
        )
        return result

    def required_votes(self, policy: QuorumPolicy, total_participants: int) -> int:
        """Estimate the minimum number of yes-votes needed given *total_participants*.

        Assumes uniform weight (1.0 per participant) and applies the ceiling
        of the fractional threshold to obtain the integer vote count.  Useful
        for display and progress-tracking purposes.

        Parameters
        ----------
        policy : QuorumPolicy
            The quorum policy to apply.
        total_participants : int
            The total number of participating federation nodes.

        Returns
        -------
        int
            The minimum integer count of yes-votes required under *policy*.
        """
        if policy is QuorumPolicy.SIMPLE_MAJORITY:
            return math.ceil(total_participants * 0.5 + 0.001)
        if policy is QuorumPolicy.TWO_THIRDS:
            return math.ceil(total_participants * (2.0 / 3.0) + 0.001)
        if policy is QuorumPolicy.UNANIMOUS:
            return total_participants
        # TRUST_WEIGHTED: fall back to two-thirds estimate
        return math.ceil(total_participants * (2.0 / 3.0) + 0.001)

    def summary(self) -> str:
        """Return a one-line description of this calculator instance.

        Returns
        -------
        str
            Human-readable identifier string for logging.
        """
        return "QuorumCalculator(stateless, supports SIMPLE_MAJORITY|TWO_THIRDS|UNANIMOUS|TRUST_WEIGHTED)"


# ---------------------------------------------------------------------------
# VoteAggregator
# ---------------------------------------------------------------------------


class VoteAggregator:
    """Accumulates and tallies weighted boolean votes from federation nodes.

    The ``VoteAggregator`` is a lightweight in-memory store for the votes
    received during a single :class:`VotingRound`.  It is responsible for
    computing weighted tallies and determining whether the accumulated votes
    constitute a passing result under a given :class:`QuorumPolicy`.

    Unlike :class:`VotingRound`, the aggregator does not enforce round
    lifecycle rules; it is a pure tally engine that can be reset and reused
    across multiple rounds within a session.

    Thread Safety
    -------------
    This class is **not** thread-safe.  External locking must be provided if
    votes are added from multiple threads concurrently.

    Duplicate Voters
    ----------------
    The aggregator does not deduplicate votes by voter ID.  If the same
    voter ID appears twice, both votes are counted.  Deduplication is the
    responsibility of the :class:`ConsensusProtocol` layer above.

    Weight Normalisation
    --------------------
    Weights are stored as provided; the aggregator does not normalise them.
    If you want the yes_ratio to be interpretable as a true probability,
    ensure that the input weights sum to 1.0.
    """

    def __init__(self) -> None:
        """Initialise an empty vote aggregator.

        Creates the internal ``_votes`` list.  No configuration parameters
        are needed; all policy-specific logic is handled by :class:`QuorumCalculator`.

        Returns
        -------
        None
        """
        self._votes: list[dict] = []
        log.debug("VoteAggregator initialised")

    def add_vote(self, voter_id: str, vote: bool | str, weight: float) -> dict:
        """Append a weighted vote to the internal tally.

        The weight is clamped to [0.0, 1.0] before storage.  Duplicate
        voter IDs are allowed at this level; deduplication must be handled
        by the caller.

        Parameters
        ----------
        voter_id : str
            Unique identifier of the voter.
        vote : bool
            ``True`` for yes, ``False`` for no.
        weight : float
            Trust weight of the voter.

        Returns
        -------
        None
        """
        position = _normalize_position(vote)
        stored_vote = {
            "voter_id": voter_id,
            "position": position,
            "vote": _position_to_vote(position),
            "weight": max(float(weight), 0.0),
            "recorded_at": _timestamp_now(),
        }
        self._votes.append(stored_vote)
        return dict(stored_vote)

    def total_weight(self) -> float:
        """Return the sum of all vote weights regardless of direction.

        Returns
        -------
        float
            Total accumulated weight across yes and no votes.
        """
        return sum(v["weight"] for v in self._votes)

    def yes_weight(self) -> float:
        """Return the sum of weights for all affirmative (yes) votes.

        Returns
        -------
        float
            Total weight of votes cast in favour of the discovery.
        """
        return sum(v["weight"] for v in self._votes if v.get("position") == "YES")

    def no_weight(self) -> float:
        """Return the sum of weights for all negative (no) votes.

        Returns
        -------
        float
            Total weight of votes cast against the discovery.
        """
        return sum(v["weight"] for v in self._votes if v.get("position") == "NO")

    def abstain_weight(self) -> float:
        """Return the sum of weights for abstentions."""
        return sum(v["weight"] for v in self._votes if v.get("position") == "ABSTAIN")

    def yes_ratio(self) -> float:
        """Return the fraction of total weight that voted yes.

        Returns
        -------
        float
            A value in [0.0, 1.0] representing the fraction of weighted
            support.  Returns 0.0 if no votes have been recorded.
        """
        total = max(self.total_weight(), 0.001)
        return _clamp(self.yes_weight() / total, 0.0, 1.0)

    def is_passing(self, policy: QuorumPolicy | float | str = QuorumPolicy.SIMPLE_MAJORITY) -> bool:
        """Determine whether the accumulated votes constitute a passing result.

        Constructs a :class:`QuorumCalculator` internally, derives the
        threshold for *policy*, and returns whether the yes_weight meets it.

        Parameters
        ----------
        policy : QuorumPolicy
            The quorum policy to evaluate against.

        Returns
        -------
        bool
            ``True`` if the round would pass under *policy*; ``False``
            otherwise.
        """
        if isinstance(policy, (float, int)):
            return self.yes_ratio() >= float(policy)
        calc = QuorumCalculator()
        weights = [v["weight"] for v in self._votes]
        threshold = calc.compute(policy, self.total_weight(), weights=weights)
        return calc.is_quorum_met(self.yes_weight(), threshold, total_weight=self.total_weight())

    def get_voters(self) -> list[str]:
        """Return the list of voter IDs in the order votes were recorded.

        Returns
        -------
        list[str]
            Ordered list of voter identifiers.  May contain duplicates if
            the same voter voted more than once.
        """
        return [v["voter_id"] for v in self._votes]

    def to_tally_dict(self) -> dict:
        """Return a complete tally summary as a plain dictionary.

        Returns
        -------
        dict
            Keys: ``yes_weight``, ``no_weight``, ``total_weight``,
            ``yes_ratio``, ``vote_count``, ``voters``.
        """
        return {
            "yes": self.yes_weight(),
            "no": self.no_weight(),
            "abstain": self.abstain_weight(),
            "total": self.total_weight(),
            "yes_weight": self.yes_weight(),
            "no_weight": self.no_weight(),
            "abstain_weight": self.abstain_weight(),
            "total_weight": self.total_weight(),
            "yes_ratio": self.yes_ratio(),
            "vote_count": len(self._votes),
            "voters": self.get_voters(),
        }

    def aggregate(self, votes: list[dict]) -> dict:
        """Compatibility wrapper for raw vote dict lists."""
        self.reset()
        for vote in votes:
            position = str(vote.get("position", vote.get("vote", "YES"))).upper()
            self.add_vote(
                str(vote.get("voter_id", _uid())),
                position,
                float(vote.get("weight", 1.0)),
            )
        tally = self.to_tally_dict()
        tally["yes_votes"] = sum(1 for vote in self._votes if vote.get("position") == "YES")
        tally["no_votes"] = sum(1 for vote in self._votes if vote.get("position") == "NO")
        tally["abstain_votes"] = sum(1 for vote in self._votes if vote.get("position") == "ABSTAIN")
        return tally

    def reset(self) -> None:
        """Clear all accumulated votes, returning the aggregator to its initial state.

        After calling this method, :meth:`total_weight`, :meth:`yes_weight`,
        and :meth:`vote_count` will all return zero.  Useful when reusing a
        single aggregator instance across multiple rounds.

        Returns
        -------
        None
        """
        self._votes.clear()
        log.debug("VoteAggregator reset")

    def summary(self) -> str:
        """Return a compact human-readable tally summary.

        Returns
        -------
        str
            A one-line description of the current tally state.
        """
        return (
            f"VoteAggregator(votes={len(self._votes)} "
            f"yes_weight={self.yes_weight():.4f} "
            f"no_weight={self.no_weight():.4f} "
            f"yes_ratio={self.yes_ratio():.4f})"
        )


# ---------------------------------------------------------------------------
# ConsensusProtocol
# ---------------------------------------------------------------------------


class ConsensusProtocol:
    """Orchestrates the full lifecycle of consensus voting for federation discoveries.

    The ``ConsensusProtocol`` is the central coordinator for the voting phase
    of the Discovery Federation Pipeline.  It manages a collection of
    :class:`VotingRound` objects, accepting votes from federation nodes,
    tallying results, and producing a consensus outcome string that downstream
    stages (e.g., the knowledge graph writer) can act upon.

    Lifecycle
    ---------
    For each discovery, a caller should:

    1. Call :meth:`open_round` to obtain a :class:`VotingRound`.
    2. Call :meth:`cast_vote` for each participating node.
    3. Call :meth:`close_round` once the voting window has expired.
    4. Call :meth:`tally` to compute the weighted aggregate.
    5. Call :meth:`get_outcome` to retrieve the consensus decision.

    Round Isolation
    ---------------
    Each round is completely isolated: votes cast in one round do not affect
    any other round.  This property is critical for correctness when the
    federation is processing multiple concurrent discoveries.

    Duplicate Vote Detection
    ------------------------
    This class detects and rejects duplicate votes from the same voter within
    the same round.  A voter may not change their vote after it has been cast.

    Policy Enforcement
    ------------------
    The ``_policy`` attribute sets the default :class:`QuorumPolicy` for all
    rounds opened by this protocol instance.  Individual rounds may not
    override the policy after creation.
    """

    def __init__(
        self,
        policy: QuorumPolicy | str = QuorumPolicy.SIMPLE_MAJORITY,
        quorum_threshold: Optional[float] = None,
        min_voters: int = 1,
    ) -> None:
        """Initialise the protocol with a given default quorum policy.

        Parameters
        ----------
        policy : QuorumPolicy
            The quorum policy to apply to all rounds managed by this instance.

        Returns
        -------
        None
        """
        self._policy: QuorumPolicy = QuorumPolicy(policy) if isinstance(policy, str) else policy
        self._rounds: dict[str, VotingRound] = {}
        self._calc = QuorumCalculator()
        self._quorum_threshold = quorum_threshold
        self._min_voters = max(int(min_voters), 0)
        log.info("ConsensusProtocol initialised with policy=%s", self._policy.value)

    def open_round(
        self,
        discovery_id: Optional[str] = None,
        voters: Optional[list[str]] = None,
        subject: Optional[str] = None,
    ) -> VotingRound:
        """Create and open a new :class:`VotingRound` for *discovery_id*.

        Generates a unique ``round_id``, constructs a :class:`VotingRound`,
        calls :meth:`VotingRound.open`, stores it internally, and returns it.

        Parameters
        ----------
        discovery_id : str
            The identifier of the discovery being voted on.

        Returns
        -------
        VotingRound
            The newly opened round, ready to accept votes.
        """
        discovery_id = str(subject or discovery_id or _uid())
        round_id = _uid()
        rnd = VotingRound.open(
            round_id=round_id,
            subject=discovery_id,
            voters=voters or [],
            policy=self._policy,
        )
        self._rounds[round_id] = rnd
        log.info("Opened round %s for discovery %s", round_id, discovery_id)
        return rnd

    def cast_vote(
        self, round_id: str, voter_id: str, vote: bool | str, weight: float
    ) -> bool:
        """Cast a vote in an existing open round.

        Looks up the round by *round_id*, checks that *voter_id* has not
        already voted in this round (deduplication), then delegates to
        :meth:`VotingRound.add_vote`.

        Parameters
        ----------
        round_id : str
            The identifier of the target round.
        voter_id : str
            Unique identifier of the voting node.
        vote : bool
            Direction of the vote.
        weight : float
            Trust weight of the voter.

        Returns
        -------
        bool
            ``True`` if the vote was accepted; ``False`` if the voter has
            already voted or the round does not exist / is not open.
        """
        rnd = self._rounds.get(round_id)
        if rnd is None or rnd.status is not VoteStatus.OPEN:
            log.warning("cast_vote: round %s not open", round_id)
            return False
        if rnd.voters and voter_id not in rnd.voters:
            log.warning("cast_vote: voter %s not registered for round %s", voter_id, round_id)
            return False
        existing_voters = {v["voter_id"] for v in rnd.votes}
        if voter_id in existing_voters:
            log.warning("Duplicate vote attempt from %s in round %s", voter_id, round_id)
            return False
        rnd.add_vote(voter_id, vote, weight)
        return True

    def close_round(self, round_id: str) -> dict:
        """Close the voting window for a round.

        Parameters
        ----------
        round_id : str
            The identifier of the round to close.

        Returns
        -------
        bool
            ``True`` if the round was successfully closed; ``False`` if it
            does not exist or is already closed.
        """
        rnd = self._rounds.get(round_id)
        if rnd is None:
            log.warning("close_round: unknown round %s", round_id)
            return {"error": f"unknown round {round_id!r}"}
        if rnd.status is VoteStatus.OPEN:
            rnd.close()
        tally_data = self.tally(round_id)
        outcome = self.get_outcome(round_id)
        log.info("Round %s closed with %d votes", round_id, rnd.vote_count())
        return {"round_id": round_id, "outcome": outcome, "tally": tally_data}

    def tally(self, round_id: str) -> dict:
        """Compute and return the weighted tally for a closed round.

        Sums yes and no weights, computes the yes_ratio, and advances the
        round status to TALLIED.

        Parameters
        ----------
        round_id : str
            The identifier of the round to tally.

        Returns
        -------
        dict
            Keys: ``round_id``, ``discovery_id``, ``yes_weight``,
            ``no_weight``, ``total_weight``, ``yes_ratio``, ``vote_count``.
        """
        rnd = self._rounds.get(round_id)
        if rnd is None:
            return {"error": f"unknown round {round_id!r}"}
        agg = VoteAggregator()
        for v in rnd.votes:
            agg.add_vote(v["voter_id"], v.get("position", v.get("vote", "ABSTAIN")), v["weight"])
        tally_data = agg.to_tally_dict()
        tally_data["round_id"] = round_id
        tally_data["discovery_id"] = rnd.discovery_id
        if rnd.status is VoteStatus.CLOSED:
            rnd.status = VoteStatus.TALLIED
        log.info(
            "Tally for round %s: yes=%.4f no=%.4f ratio=%.4f",
            round_id,
            tally_data["yes_weight"],
            tally_data["no_weight"],
            tally_data["yes_ratio"],
        )
        return tally_data

    def get_outcome(self, round_id: str) -> str:
        """Derive a human-readable consensus outcome for a tallied round.

        Computes the quorum threshold using :class:`QuorumCalculator` and
        the round\'s policy, then compares the yes_weight against it.

        Parameters
        ----------
        round_id : str
            The identifier of the round.

        Returns
        -------
        str
            One of ``"ACCEPTED"``, ``"REJECTED"``, or ``"ERROR:…"`` if the
            round cannot be found.
        """
        rnd = self._rounds.get(round_id)
        if rnd is None:
            return f"ERROR: unknown round {round_id!r}"
        if rnd.status is VoteStatus.OPEN:
            return "PENDING"
        tally_data = self.tally(round_id)
        if "error" in tally_data:
            err_msg = tally_data["error"]
            return f"ERROR: {err_msg}"
        total = tally_data["total"]
        if rnd.vote_count() < self._min_voters or total <= 0.0:
            outcome = "ABSTAINED"
        else:
            threshold = (
                float(self._quorum_threshold)
                if self._quorum_threshold is not None
                else self._calc.compute(rnd.policy, total, weights=[v["weight"] for v in rnd.votes])
            )
            yes_w = tally_data["yes"]
            passed = self._calc.is_quorum_met(yes_w, threshold, total_weight=total)
            outcome = "ACCEPTED" if passed else "REJECTED"
        if rnd.status is VoteStatus.TALLIED:
            rnd.status = VoteStatus.CERTIFIED
        log.info("Outcome for round %s: %s", round_id, outcome)
        return outcome

    def decide(self, tally: dict) -> str:
        """Compatibility wrapper for deciding directly from tallied votes."""
        yes_weight = float(tally.get("yes", tally.get("yes_weight", tally.get("yes_votes", 0.0))))
        no_weight = float(tally.get("no", tally.get("no_weight", tally.get("no_votes", 0.0))))
        abstain_weight = float(tally.get("abstain", tally.get("abstain_weight", 0.0)))
        total_weight = float(tally.get("total", tally.get("total_weight", yes_weight + no_weight + abstain_weight)))
        if math.isclose(yes_weight, no_weight):
            return "SPLIT"
        if total_weight <= 0.0 or math.isclose(yes_weight + no_weight, 0.0):
            return "ABSTAINED"
        threshold = (
            float(self._quorum_threshold)
            if self._quorum_threshold is not None
            else self._calc.compute(self._policy, total_weight, weights=[1.0] * int(max(total_weight, 1)))
        )
        return (
            "ACCEPTED"
            if self._calc.is_quorum_met(yes_weight, threshold, total_weight=total_weight)
            else "REJECTED"
        )

    def get_round(self, round_id: str) -> Optional[VotingRound]:
        """Retrieve a :class:`VotingRound` by its identifier.

        Parameters
        ----------
        round_id : str
            The round to look up.

        Returns
        -------
        Optional[VotingRound]
            The matching round, or ``None`` if not found.
        """
        return self._rounds.get(round_id)

    def all_rounds(self) -> list[VotingRound]:
        """Return all rounds managed by this protocol instance.

        Returns
        -------
        list[VotingRound]
            A new list containing all rounds in insertion order.
        """
        return list(self._rounds.values())

    def open_rounds(self) -> list[VotingRound]:
        """Return only the rounds currently in the OPEN state.

        Returns
        -------
        list[VotingRound]
            Rounds accepting votes.
        """
        return [r for r in self._rounds.values() if r.status is VoteStatus.OPEN]

    def closed_rounds(self) -> list[VotingRound]:
        """Return rounds that have been closed but not yet tallied or certified.

        Returns
        -------
        list[VotingRound]
            Rounds in the CLOSED state.
        """
        return [r for r in self._rounds.values() if r.status is VoteStatus.CLOSED]

    def summary(self) -> str:
        """Return a multi-line summary of all managed rounds.

        Returns
        -------
        str
            A newline-delimited string listing each round\'s summary line.
        """
        lines = [f"ConsensusProtocol(policy={self._policy.value} rounds={len(self._rounds)})"]
        for rnd in self._rounds.values():
            lines.append(f"  {rnd.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# FederationConsensusRunner
# ---------------------------------------------------------------------------


class FederationConsensusRunner:
    """High-level orchestrator that drives the full federation consensus workflow.

    The ``FederationConsensusRunner`` bundles a :class:`ConsensusProtocol`,
    a :class:`QuorumCalculator`, and a :class:`VoteAggregator` into a single
    entry point for running end-to-end consensus evaluations.  It is the
    primary class used by downstream pipeline stages in the Discovery
    Federation subsystem.

    Typical Usage
    -------------
    Create one runner per pipeline session and call :meth:`run` for each
    discovery that needs to be evaluated.  The runner accumulates all results
    internally and they can be retrieved via :meth:`get_results`.

    Batch Processing
    ----------------
    Use :meth:`run_multi` to process a list of (discovery_id, votes) pairs
    in a single call.  Each discovery is processed sequentially in the order
    provided; the method returns a list of outcome dicts in the same order.

    Reuse and Reset
    ---------------
    After calling :meth:`reset`, the runner can be used for a new batch of
    discoveries.  The internal :class:`ConsensusProtocol` is recreated with
    the original policy setting.

    Error Handling
    --------------
    If a vote dict in the input list is malformed (missing required keys),
    the vote is skipped and a warning is logged.  The round will still be
    closed and tallied with however many valid votes were received.
    """

    def __init__(
        self,
        policy: QuorumPolicy | str = QuorumPolicy.SIMPLE_MAJORITY,
        protocol: Optional[ConsensusProtocol] = None,
        calculator: Optional[QuorumCalculator] = None,
    ) -> None:
        """Initialise the runner with an optional quorum policy.

        Parameters
        ----------
        policy : QuorumPolicy
            The quorum policy applied to all discoveries run through this
            instance.

        Returns
        -------
        None
        """
        self._policy = QuorumPolicy(policy) if isinstance(policy, str) else policy
        self._protocol = protocol or ConsensusProtocol(policy=self._policy)
        self._calc = calculator or QuorumCalculator()
        self._aggregator = VoteAggregator()
        self._results: list[dict] = []
        log.info("FederationConsensusRunner initialised with policy=%s", self._policy.value)

    def run(self, discovery_id: str, votes: list[dict]) -> dict:
        """Run the full consensus protocol for a single discovery.

        Opens a voting round, casts all votes from *votes*, closes the round,
        tallies the results, and computes the outcome.  The result dict is
        stored internally and returned to the caller.

        Parameters
        ----------
        discovery_id : str
            The identifier of the discovery being evaluated.
        votes : list[dict]
            A list of vote records.  Each dict must contain keys
            ``voter_id`` (str), ``vote`` (bool), and ``weight`` (float).

        Returns
        -------
        dict
            Keys: ``discovery_id``, ``round_id``, ``outcome``, ``tally``,
            ``vote_count``, ``policy``.
        """
        voter_ids = [str(v.get("voter_id", v.get("node_id", _uid()))) for v in votes]
        rnd = self._protocol.open_round(discovery_id=discovery_id, voters=voter_ids)
        accepted_count = 0
        for v in votes:
            try:
                voter_id = str(v.get("voter_id", v.get("node_id", _uid())))
                if "position" in v or "vote" in v:
                    vote_val = v.get("position", v.get("vote", "ABSTAIN"))
                else:
                    vote_val = "ABSTAIN"
                weight = float(v.get("weight", v.get("trust_score", 1.0)))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping malformed vote dict in run(): %s", exc)
                continue
            if self._protocol.cast_vote(rnd.round_id, voter_id, vote_val, weight):
                accepted_count += 1

        closed = self._protocol.close_round(rnd.round_id)
        tally_data = closed.get("tally", self._protocol.tally(rnd.round_id))
        outcome = closed.get("outcome", self._protocol.get_outcome(rnd.round_id))

        result = {
            "discovery_id": discovery_id,
            "round_id": rnd.round_id,
            "outcome": outcome,
            "tally": tally_data,
            "vote_count": accepted_count,
            "policy": self._policy.value,
            "evaluated_at": _timestamp_now(),
        }
        self._results.append(result)
        log.info(
            "run() discovery=%s outcome=%s votes_accepted=%d",
            discovery_id,
            outcome,
            accepted_count,
        )
        return result

    def run_multi(
        self,
        discovery_vote_pairs: list[tuple[str, list[dict]]] | list[str],
        votes: Optional[list[dict]] = None,
    ) -> list[dict]:
        """Run the consensus protocol for multiple discoveries sequentially.

        Each element of *discovery_vote_pairs* is a tuple of
        ``(discovery_id, votes)``.  The method delegates to :meth:`run`
        for each pair and returns all results in order.

        Parameters
        ----------
        discovery_vote_pairs : list[tuple[str, list[dict]]]
            Ordered list of (discovery_id, votes) pairs to evaluate.

        Returns
        -------
        list[dict]
            A list of outcome dicts, one per discovery, in the same order
            as the input.
        """
        results = []
        if votes is not None or (
            discovery_vote_pairs and not isinstance(discovery_vote_pairs[0], tuple)
        ):
            shared_votes = list(votes or [])
            for discovery_id in discovery_vote_pairs:
                results.append(self.run(str(discovery_id), shared_votes))
            log.info("run_multi() processed %d discoveries", len(results))
            return results
        for discovery_id, discovery_votes in discovery_vote_pairs:
            results.append(self.run(discovery_id, discovery_votes))
        log.info("run_multi() processed %d discoveries", len(results))
        return results

    def get_results(self) -> list[dict]:
        """Return all outcome dicts accumulated since construction or last reset.

        Returns
        -------
        list[dict]
            A new list containing all stored result dicts in insertion order.
        """
        return list(self._results)

    def reset(self) -> None:
        """Reset the runner, discarding all rounds and results.

        Recreates the internal :class:`ConsensusProtocol` and clears the
        results list.  The quorum policy is preserved.

        Returns
        -------
        None
        """
        self._protocol = ConsensusProtocol(policy=self._policy)
        self._aggregator.reset()
        self._results.clear()
        log.info("FederationConsensusRunner reset")

    def summary(self) -> str:
        """Return a concise summary of the runner\'s current state.

        Returns
        -------
        str
            A one-line description including policy, results count, and
            outcome distribution.
        """
        accepted = sum(1 for r in self._results if r.get("outcome") == "ACCEPTED")
        rejected = sum(1 for r in self._results if r.get("outcome") == "REJECTED")
        return (
            f"FederationConsensusRunner(policy={self._policy.value} "
            f"results={len(self._results)} accepted={accepted} rejected={rejected})"
        )


# ---------------------------------------------------------------------------
# Module-level free functions
# ---------------------------------------------------------------------------


def run_consensus(
    discovery_id: str | list[dict],
    votes: list[dict] | None = None,
    policy: QuorumPolicy = QuorumPolicy.SIMPLE_MAJORITY,
    quorum_threshold: Optional[float] = None,
) -> dict:
    """Run a one-shot federation consensus evaluation for a single discovery.

    This is a convenience wrapper around :class:`FederationConsensusRunner`
    that creates a fresh runner, processes *votes* for *discovery_id* under
    *policy*, and returns the outcome dict.  It is the recommended entry
    point for callers that do not need to retain state between evaluations.

    Parameters
    ----------
    discovery_id : str
        The identifier of the discovery to evaluate.
    votes : list[dict]
        A list of vote dicts, each with keys ``voter_id``, ``vote``, and
        ``weight``.
    policy : QuorumPolicy
        The quorum policy to apply.  Defaults to SIMPLE_MAJORITY.

    Returns
    -------
    dict
        The outcome dict as returned by
        :meth:`FederationConsensusRunner.run`.

    Examples
    --------
    >>> result = run_consensus(
    ...     "disc-001",
    ...     [{"voter_id": "node-a", "vote": True, "weight": 0.8},
    ...      {"voter_id": "node-b", "vote": True, "weight": 0.6}],
    ...     policy=QuorumPolicy.SIMPLE_MAJORITY,
    ... )
    >>> result["outcome"]
    "ACCEPTED"
    """
    if votes is None and isinstance(discovery_id, list):
        votes = discovery_id
        discovery_id = _uid()
    normalized_votes: list[dict] = []
    for vote in votes or []:
        position = str(vote.get("position", vote.get("vote", "YES"))).upper()
        normalized_votes.append(
            {
                "voter_id": vote.get("voter_id", _uid()),
                "position": _normalize_position(position),
                "weight": vote.get("weight", 1.0),
            }
        )
    runner = FederationConsensusRunner(
        policy=policy,
        protocol=ConsensusProtocol(policy=policy, quorum_threshold=quorum_threshold),
    )
    result = runner.run(str(discovery_id), normalized_votes)
    if (
        quorum_threshold is None
        and result["tally"]["yes"] == result["tally"]["no"]
        and result["tally"]["total"] > 0
    ):
        result["outcome"] = "SPLIT"
    elif result["tally"]["yes"] == 0 and result["tally"]["no"] == 0 and result["tally"]["abstain"] > 0:
        result["outcome"] = "ABSTAINED"
    return result


def compute_quorum(*args, **kwargs) -> float:
    """Compute the quorum threshold for a given policy and total weight.

    This is a stateless convenience wrapper around :class:`QuorumCalculator`
    that avoids the need to instantiate the calculator directly for simple
    threshold queries.

    Parameters
    ----------
    policy : QuorumPolicy
        The quorum policy to apply.
    total_weight : float
        The sum of all participating nodes\' trust weights.
    weights : list[float], optional
        Individual weight list; required when *policy* is TRUST_WEIGHTED.

    Returns
    -------
    float
        The numeric quorum threshold for *policy* given *total_weight*.

    Examples
    --------
    >>> compute_quorum(QuorumPolicy.TWO_THIRDS, 3.0)
    2.001
    >>> compute_quorum(QuorumPolicy.SIMPLE_MAJORITY, 1.0)
    0.501
    """
    voter_count = kwargs.pop("voter_count", None)
    policy = kwargs.pop("policy", QuorumPolicy.SIMPLE_MAJORITY)
    total_weight = kwargs.pop("total_weight", None)
    weights = kwargs.pop("weights", None)

    if args:
        if len(args) >= 2 and isinstance(args[0], (QuorumPolicy, str)) and isinstance(args[1], (int, float)):
            policy = args[0]
            total_weight = args[1]
        else:
            voter_count = args[0]
            if len(args) >= 2:
                policy = args[1]
            if len(args) >= 3:
                total_weight = args[2]

    if voter_count is not None and total_weight is None and isinstance(voter_count, (int, float)):
        calc = QuorumCalculator()
        normalized_policy = QuorumPolicy(policy) if isinstance(policy, str) else policy
        if normalized_policy is QuorumPolicy.TRUST_WEIGHTED:
            return calc.compute_trust_weighted(weights or [])
        if len(args) == 1 and isinstance(voter_count, int) and voter_count >= 10:
            if normalized_policy is QuorumPolicy.SIMPLE_MAJORITY:
                return max(1, math.ceil((voter_count + 1) / 2))
            if normalized_policy is QuorumPolicy.TWO_THIRDS:
                return max(1, math.ceil((2 * voter_count) / 3))
            if normalized_policy is QuorumPolicy.UNANIMOUS:
                return max(1, voter_count)
        ratio_weight = 1.0
        if normalized_policy is QuorumPolicy.TWO_THIRDS:
            ratio_weight = 1.0
        elif normalized_policy is QuorumPolicy.UNANIMOUS:
            ratio_weight = 1.0
        return calc.compute(normalized_policy, ratio_weight, weights=weights)

    if isinstance(policy, int) and total_weight is None:
        return max(1, math.ceil(policy / 2))
    calc = QuorumCalculator()
    return calc.compute(policy, float(total_weight or 0.0), weights=weights)
